"""The push-based read model: the writers, the entries, and the loop that commits them.

The suite that replaced the projections' one. Nothing here folds at read time,
so nothing here reads a page of events and asks what it adds up to: each test
hands facts to the writers in order and asks what the row SAYS afterwards.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest

from domain.entries import (
    EffortChangeBody,
    AssignmentStartedBody,
    FileBody,
    MessageBody,
    ModelChangeBody,
    QuestionAskedBody,
    ShellFinishedBody,
    ShellOutputBody,
    ShellStartedBody,
    TurnFinishedBody,
)
from domain.events import (
    EffortChanged,
    ActorAssignmentStarted,
    ActorFinished,
    ActorStarted,
    CanonicalEvent,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    EventPayload,
    FileAccessed,
    GoalChanged,
    MessageCreated,
    ModelChanged,
    PlanProposed,
    PlanResolved,
    QuestionAnswered,
    QuestionAsked,
    ReasoningCreated,
    SearchPerformed,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    ShellBackgrounded,
    ShellFinished,
    ShellOutputFinished,
    ShellOutputLocated,
    ShellProgressed,
    ShellStarted,
    SkillStarted,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
    TurnStarted,
    UsageReported,
    WebFetched,
)
from domain.ids import (
    ActorId,
    AssignmentId,
    AttentionId,
    CanonicalEventId,
    MessageId,
    RawEventId,
    SessionId,
    ShellId,
    SkillId,
    TaskId,
    TurnId,
)
from domain.records import CommittedEvent
from domain.sessiondata import ActorStatus
from domain.values import AccountReference, ModelReference, TextContent, TokenUsage
from engine.react.loop import ReactionLoop
from engine.sessiondata.actors import (
    ActorWriter,
    ContextWriter,
    StatisticsWriter,
    StatusWriter,
    UsageWriter,
)
from audit.recorder import AuditRecorder
from engine.sessiondata.contract import AggregateState, AppliedActorListener
from engine.sessiondata.entries import EntryWriter
from engine.sessiondata.session import GoalWriter, SessionWriter, TaskWriter
from harness.contract import CanonicalEventReaction, HarnessReactorContext
from harness.registry import HarnessRegistry
from harness.models import RawEvent, Session, TranslationResult
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.session_data import SqliteSessionDataRepository

SESSION = SessionId("session-one")
LEAD = ActorId("session-one:lead")
CHILD = ActorId("child-one")

WRITERS = (
    SessionWriter(),
    GoalWriter(),
    TaskWriter(),
    ActorWriter(),
    StatusWriter(),
    UsageWriter(),
    ContextWriter(),
    StatisticsWriter(),
)


def committed(
    payload: EventPayload,
    *,
    actor_id: ActorId = LEAD,
    parent_actor_id: ActorId | None = None,
    turn_id: TurnId | None = None,
    occurred_at: float | None = None,
    accepted_at: float = 100.0,
    cursor: int = 1,
    event_id: str | None = None,
) -> CommittedEvent:
    return CommittedEvent(
        cursor=cursor,
        accepted_at=accepted_at,
        event=CanonicalEvent(
            event_id=CanonicalEventId(event_id or f"event-{cursor}"),
            session_id=SESSION,
            actor_id=actor_id,
            turn_id=turn_id,
            parent_actor_id=parent_actor_id,
            harness="example",
            occurred_at=occurred_at,
            terminal_window_id=None,
            harness_process_id=None,
            payload=payload,
        ),
    )


def fold(*payloads: EventPayload | CommittedEvent) -> AggregateState:
    """Every writer over every fact, in order — the loop's fold, without the store."""
    state = AggregateState()
    for cursor, payload in enumerate(payloads, start=1):
        event = (
            payload
            if isinstance(payload, CommittedEvent)
            else committed(payload, cursor=cursor)
        )
        for writer in WRITERS:
            state = writer.write(event, state)
    return state


# One start, and `replace` for the differences: a dict of defaults updated with
# kwargs is the same builder untyped, and a Literal passed as a plain string
# would go unnoticed.
A_START = SessionStarted(
    working_directory="/work",
    source_reference="transcript",
    resumed_from=None,
    title=None,
    model=None,
    effort=None,
    account=None,
)


def started() -> SessionStarted:
    return A_START


def alive() -> tuple[EventPayload, ...]:
    """The two facts every session begins with."""
    return (started(), ActorStarted("claude", "lead"))


def status_after(*payloads: EventPayload) -> ActorStatus | None:
    actor = fold(*alive(), *payloads).actor(LEAD)
    assert actor is not None, "the lead actor has no row"
    return actor.status


# --- the session row ----------------------------------------------------------


def test_a_session_is_born_from_its_own_fact_and_nothing_else():
    """No `session.started`, no row. A usage report for a session nobody
    announced would otherwise put a nameless entry on the list."""
    assert fold(UsageReported("session", "session-one", None, None, TokenUsage(1), True, None)).session is None
    facts = fold(replace(A_START, working_directory="/work")).session
    assert facts is not None
    assert (facts.session_id, facts.state, facts.working_directory) == (SESSION, "running", "/work")
    assert facts.lead_actor_id == LEAD


def test_a_title_a_person_chose_outranks_every_title_a_harness_derived():
    """Four sources name a session and they arrive in any order, so precedence
    cannot be "the last one wins" — it is what a person chose, then what the
    harness named, then a summary of it, then the first thing asked."""
    prompt = MessageCreated(MessageId("m1"), "user", TextContent("Fix the reconnect bug"), "prompt", None)
    assert fold(*alive(), prompt).session.title == "Fix the reconnect bug"
    assert fold(
        *alive(),
        prompt,
        SessionTitleChanged("Summarised", "summary"),
    ).session.title == "Summarised"
    assert fold(
        *alive(),
        SessionTitleChanged("Chosen", "custom"),
        SessionTitleChanged("Derived", "automatic"),
        prompt,
    ).session.title == "Chosen"


def test_only_the_first_prompt_titles_a_session():
    state = fold(
        *alive(),
        MessageCreated(MessageId("m1"), "user", TextContent("first ask"), "prompt", None),
        MessageCreated(MessageId("m2"), "user", TextContent("second ask"), "prompt", None),
    )
    assert state.session.title == "first ask"


def test_a_finished_session_says_when_and_a_resumed_one_keeps_its_work():
    """A session that starts again is the same session: the lifecycle reopens and
    the title, goal and tasks it accumulated are still true."""
    finished = fold(
        *alive(),
        SessionTitleChanged("Chosen", "custom"),
        GoalChanged("ship it", "active", None),
        committed(SessionFinished("succeeded", None), cursor=9, occurred_at=500.0),
    ).session
    assert (finished.state, finished.finished_at) == ("finished", 500.0)

    resumed = fold(
        *alive(),
        SessionTitleChanged("Chosen", "custom"),
        GoalChanged("ship it", "active", None),
        SessionFinished("succeeded", None),
        started(),
    ).session
    assert (resumed.state, resumed.finished_at) == ("running", None)
    assert resumed.title == "Chosen"
    assert resumed.goal.objective == "ship it"


def test_an_account_is_the_last_one_reported():
    state = fold(*alive(), SessionTitleChanged("t", "custom"))
    assert state.session.account is None
    state = fold(*alive(), SessionAccountChangedFixture())
    assert state.session.account.display_name == "zhambyl"


def SessionAccountChangedFixture():
    from domain.events import SessionAccountChanged

    return SessionAccountChanged(AccountReference("acc-1", "zhambyl"))


# --- the goal and the tasks ---------------------------------------------------


def test_a_cleared_goal_is_no_goal_and_a_complete_one_says_so():
    assert fold(*alive(), GoalChanged("ship it", "active", None)).session.goal.completed is False
    assert fold(*alive(), GoalChanged("ship it", "completed", None)).session.goal.completed is True
    assert fold(
        *alive(),
        GoalChanged("ship it", "active", None),
        GoalChanged(None, "cleared", None),
    ).session.goal is None


def test_the_list_fact_orders_the_tasks_and_decides_which_belong():
    """Two facts, two jobs: what a task IS, and which tasks there are. A task the
    list stopped naming is gone from it even though its own last state stands."""
    first = TaskChanged(TaskId("t1"), "Read it", None, "completed", LEAD)
    second = TaskChanged(TaskId("t2"), "Change it", None, "in_progress", LEAD)
    state = fold(*alive(), second, first, TaskListChanged("list", (TaskId("t1"), TaskId("t2"))))
    assert [task.subject for task in state.session.tasks] == ["Read it", "Change it"]

    dropped = fold(
        *alive(),
        first,
        second,
        TaskListChanged("list", (TaskId("t2"),)),
    )
    assert [task.task_id for task in dropped.session.tasks] == [TaskId("t2")]


def test_a_task_nobody_has_listed_yet_still_belongs_to_the_session():
    state = fold(*alive(), TaskChanged(TaskId("t1"), "Read it", None, "pending", None))
    assert [task.task_id for task in state.session.tasks] == [TaskId("t1")]


# --- the actors ---------------------------------------------------------------


def test_an_actor_is_born_once_and_reopens_rather_than_forgetting():
    """Both evidence streams announce a subagent, so the second announcement is
    the same actor — and it must not discard what the first one learned."""
    state = fold(
        *alive(),
        committed(ActorStarted("Explore", "child"), actor_id=CHILD, parent_actor_id=LEAD, cursor=3),
        committed(ActorFinished(None), actor_id=CHILD, parent_actor_id=LEAD, cursor=4, occurred_at=9.0),
        committed(ActorStarted("Explore", "child"), actor_id=CHILD, parent_actor_id=LEAD, cursor=5),
    )
    child = state.actor(CHILD)
    assert (child.role, child.name, child.parent_actor_id) == ("child", "Explore", LEAD)
    assert (child.state, child.finished_at) == ("running", None)


def test_an_actor_carries_one_model_name_and_its_effort():
    state = fold(
        *alive(),
        ModelChanged(None, ModelReference("claude-opus-5", "Opus 5", "opus"), "selected"),
        EffortChanged(None, "high", "selected"),
    )
    # The whole reference is kept: a reader is shown the display name, and a
    # relaunch needs the harness's own id for the same model.
    assert state.actor(LEAD).model == ModelReference("claude-opus-5", "Opus 5", "opus")
    assert state.actor(LEAD).effort == "high"


def test_a_model_with_no_display_name_still_records_its_native_id():
    state = fold(*alive(), ModelChanged(None, ModelReference("gpt-5.4", None, None), "selected"))
    assert state.actor(LEAD).model.native_id == "gpt-5.4"


def test_nothing_but_the_actor_writer_invents_an_actor():
    """A fact about an actor nobody announced is a fact about a name we cannot
    describe; the row waits for the announcement rather than guessing."""
    state = fold(
        started(),
        committed(ContextReported(10, 200, None), actor_id=CHILD, cursor=2),
        committed(UsageReported("actor", "child-one", None, None, TokenUsage(5), True, None), actor_id=CHILD, cursor=3),
    )
    assert state.actor(CHILD) is None


# --- the status, branch by branch (Table 0) -----------------------------------


def test_a_started_session_is_idle_and_a_finished_one_shows_no_state():
    assert status_after() == "idle"
    assert status_after(SessionFinished("succeeded", None)) is None


def test_a_prompt_or_a_turn_start_is_thinking_and_reasoning_is_working():
    assert status_after(TurnStarted(None)) == "thinking"
    assert status_after(
        MessageCreated(MessageId("m1"), "user", TextContent("go"), "prompt", None)
    ) == "thinking"
    assert status_after(ReasoningCreated("r1", TextContent("hmm"))) == "working"


def test_an_assistant_message_is_not_a_prompt():
    assert status_after(
        MessageCreated(MessageId("m1"), "assistant", TextContent("done"), "end_turn", None)
    ) == "idle"


@pytest.mark.parametrize(
    "payload",
    (
        ShellStarted(ShellId("sh1"), TextContent("make test"), "foreground", None),
        SkillStarted(SkillId("k1"), "audit-debug", None),
        TaskChanged(TaskId("t1"), "Read it", None, "in_progress", LEAD),
        TaskListChanged("list", (TaskId("t1"),)),
    ),
)
def test_work_being_done_is_executing(payload):
    """A task tool is work, the same as a command — which is what the `task`
    category set before the categories dissolved."""
    assert status_after(payload) == "executing"


@pytest.mark.parametrize(
    "payload",
    (
        ShellFinished(ShellId("sh1"), "succeeded", None, 0),
        FileAccessed("/work/a.py", "updated", "succeeded"),
        SearchPerformed("Grep", TextContent("q"), None, "succeeded"),
        WebFetched("https://x.dev", None, "succeeded"),
    ),
)
def test_work_that_ended_is_working_again(payload):
    assert status_after(
        ShellStarted(ShellId("sh1"), TextContent("make test"), "foreground", None),
        payload,
    ) == "working"


def test_an_unanswered_question_outlives_the_work_that_finished_after_it():
    """The pending set is why: a finish has to know whether anybody is still
    waiting, and no single fact can say so."""
    assert status_after(QuestionAsked(AttentionId("a1"), ())) == "awaiting_attention"
    assert status_after(
        QuestionAsked(AttentionId("a1"), ()),
        ShellFinished(ShellId("sh1"), "succeeded", None, 0),
    ) == "awaiting_attention"
    assert status_after(
        QuestionAsked(AttentionId("a1"), ()),
        QuestionAnswered(AttentionId("a1"), (), None),
        ShellFinished(ShellId("sh1"), "succeeded", None, 0),
    ) == "working"


def test_a_plan_waits_for_a_person_the_same_way_a_question_does():
    assert status_after(PlanProposed(AttentionId("a1"), TextContent("do it"))) == "awaiting_attention"
    assert status_after(
        PlanProposed(AttentionId("a1"), TextContent("do it")),
        PlanResolved(AttentionId("a1"), "approved", None, False),
    ) == "working"


def test_compaction_is_work():
    assert status_after(CompactionStarted(1000)) == "working"


def test_a_turn_that_ends_with_nothing_running_is_awaiting_a_response():
    assert status_after(TurnStarted(None), TurnFinished(None, "succeeded")) == "awaiting_response"
    assert status_after(TurnStarted(None), TurnAborted(None)) == "awaiting_response"


def test_a_turn_that_ends_over_a_running_background_job_is_awaiting_it():
    """The state that used to be unreachable. A background job's launch reports
    finished immediately, while its output still flows — so ending it there
    emptied the set before a turn could ever end on it, and a session with a job
    still running read as idle."""
    assert status_after(
        ShellStarted(ShellId("bg1"), TextContent("tail -f log"), "background", None),
        ShellFinished(ShellId("bg1"), "succeeded", None, None),
        TurnFinished(None, "succeeded"),
    ) == "awaiting_background"


def test_a_background_job_ends_on_its_own_notification_not_on_its_launch():
    assert status_after(
        ShellStarted(ShellId("bg1"), TextContent("tail -f log"), "background", None),
        ShellFinished(ShellId("bg1"), "succeeded", None, None),
        ShellOutputFinished(ShellId("bg1"), "succeeded"),
        TurnFinished(None, "succeeded"),
    ) == "awaiting_response"


def test_a_command_backgrounded_mid_run_becomes_background_work_and_counts_as_a_job():
    state = fold(
        *alive(),
        ShellStarted(ShellId("sh1"), TextContent("make test"), "foreground", None),
        ShellBackgrounded(ShellId("sh1"), "b18"),
    )
    background = state.actor(LEAD).background
    assert background.running_shell_ids == (ShellId("sh1"),)
    assert background.background_job_count == 1
    # …and it did not move the status: `awaiting_background` is a turn's end.
    assert state.actor(LEAD).status == "executing"


def test_monitors_and_background_jobs_are_counted_apart():
    state = fold(
        *alive(),
        ShellStarted(ShellId("m1"), TextContent("watch"), "monitor", None),
        ShellStarted(ShellId("bg1"), TextContent("tail"), "background", None),
    )
    background = state.actor(LEAD).background
    assert (background.monitor_count, background.background_job_count) == (1, 1)
    assert set(background.running_shell_ids) == {ShellId("m1"), ShellId("bg1")}


def test_a_finished_session_clears_every_actor_not_just_the_one_that_ended_it():
    state = fold(
        *alive(),
        committed(ActorStarted("Explore", "child"), actor_id=CHILD, parent_actor_id=LEAD, cursor=3),
        committed(ReasoningCreated("r1", TextContent("hmm")), actor_id=CHILD, parent_actor_id=LEAD, cursor=4),
        SessionFinished("succeeded", None),
    )
    assert [actor.status for actor in dict(state.actors).values()] == [None, None]


# --- usage, context, statistics ----------------------------------------------


def test_a_cumulative_usage_report_replaces_and_a_share_adds_up():
    """A harness says which it is sending, and treating a total as a share is how
    a session's cost silently doubles."""
    replaced = fold(
        *alive(),
        UsageReported("actor", "lead", None, None, TokenUsage(input_tokens=10), True, Decimal("1.00")),
        UsageReported("actor", "lead", None, None, TokenUsage(input_tokens=30), True, Decimal("3.00")),
    )
    assert replaced.actor(LEAD).usage.tokens.input_tokens == 30
    assert replaced.actor(LEAD).usage.cost_in_usd == Decimal("3.00")

    added = fold(
        *alive(),
        UsageReported("actor", "lead", None, None, TokenUsage(input_tokens=10), False, Decimal("1.00")),
        UsageReported("actor", "lead", None, None, TokenUsage(input_tokens=30), False, Decimal("3.00")),
    )
    assert added.actor(LEAD).usage.tokens.input_tokens == 40
    assert added.actor(LEAD).usage.cost_in_usd == Decimal("4.00")


def test_the_context_window_reports_its_fill_and_says_when_it_is_being_emptied():
    state = fold(*alive(), ContextReported(61000, 200000, None), CompactionStarted(61000))
    assert state.actor(LEAD).context.used_tokens == 61000
    assert state.actor(LEAD).context.window_tokens == 200000
    assert state.actor(LEAD).context.compacting is True

    compacted = fold(
        *alive(),
        ContextReported(61000, 200000, None),
        CompactionStarted(61000),
        CompactionFinished(61000, 4000),
    )
    assert compacted.actor(LEAD).context.compacting is False
    assert compacted.actor(LEAD).context.used_tokens == 4000


def test_the_scoreboard_counts_distinct_files_and_names_a_tool_per_action():
    state = fold(
        *alive(),
        FileAccessed("/work/a.py", "updated", "succeeded", lines_added=12, lines_removed=3),
        FileAccessed("/work/a.py", "updated", "succeeded", lines_added=1, lines_removed=0),
        FileAccessed("/work/b.py", "read", "succeeded"),
        SearchPerformed("Grep", TextContent("shell_id"), None, "succeeded"),
        WebFetched("https://x.dev", None, "succeeded"),
    )
    statistics = state.actor(LEAD).statistics
    assert statistics.file_count == 2
    assert (statistics.lines_added, statistics.lines_removed) == (13, 3)
    assert dict(statistics.tool_counts) == {"Edit": 2, "Read": 1, "Grep": 1, "WebFetch": 1}


def test_commands_are_counted_once_and_their_failures_separately():
    state = fold(
        *alive(),
        ShellStarted(ShellId("sh1"), TextContent("make test"), "foreground", None),
        ShellFinished(ShellId("sh1"), "failed", None, 1),
        ShellStarted(ShellId("sh2"), TextContent("make lint"), "foreground", None),
        ShellFinished(ShellId("sh2"), "succeeded", None, 0),
    )
    statistics = state.actor(LEAD).statistics
    assert (statistics.shell_command_count, statistics.failed_shell_command_count) == (2, 1)
    # A command is not a "tool" on the counts row: it is already the row above.
    assert statistics.tool_counts == ()


def test_prompts_and_actor_messages_are_counted_apart():
    state = fold(
        *alive(),
        MessageCreated(MessageId("m1"), "user", TextContent("go"), "prompt", None),
        MessageCreated(MessageId("m2"), "assistant", TextContent("ok"), "intermediate", None, CHILD),
    )
    statistics = state.actor(LEAD).statistics
    assert (statistics.prompt_count, statistics.actor_message_count) == (1, 1)


def test_active_seconds_measures_closed_intervals_and_leaves_the_open_one_to_the_reader():
    """A number that grows on its own cannot be a stored fact — writing a row per
    second is the alternative. The interval still open is added when somebody
    asks, so what is stored is only what has definitely elapsed."""
    state = fold(
        committed(started(), cursor=1, occurred_at=100.0),
        committed(ActorStarted("claude", "lead"), cursor=2, occurred_at=100.0),
        committed(TurnFinished(None, "succeeded"), cursor=3, occurred_at=130.0),
        committed(
            MessageCreated(MessageId("m1"), "user", TextContent("again"), "prompt", None),
            cursor=4,
            occurred_at=200.0,
        ),
    )
    statistics = state.actor(LEAD).statistics
    assert statistics.active_seconds == 30.0
    assert statistics.active_since_internal == 200.0


def test_a_fact_with_no_clock_of_its_own_is_timed_by_when_we_recorded_it():
    """`occurred_at` is nullable by design, and a fold that measured on the bare
    column would be subtracting None."""
    state = fold(
        committed(started(), cursor=1, accepted_at=100.0),
        committed(ActorStarted("claude", "lead"), cursor=2, accepted_at=100.0),
        committed(TurnFinished(None, "succeeded"), cursor=3, accepted_at=142.0),
    )
    assert state.actor(LEAD).statistics.active_seconds == 42.0


# --- the entries --------------------------------------------------------------


def entry_of(payload: EventPayload, **kwargs):
    return EntryWriter().entry(committed(payload, **kwargs))


def body_of(payload: EventPayload, **kwargs):
    """The entry's body, or None for a fact that draws no line at all.

    "No entry" is an ANSWER for some facts — the two turn markers, everything
    aggregate-shaped, and a report that changed nothing — so the assertions about
    them read better against the body than against the envelope.
    """
    entry = entry_of(payload, **kwargs)
    return None if entry is None else entry.body


def test_an_entry_carries_the_envelope_the_client_joins_on():
    """The envelope is the join: a client resolves the actor's name and colour
    from `SessionData.actors`, groups by the turn, and orders by the cursor — so
    an entry that dropped any of them would need the canonical log to be read."""
    entry = entry_of(
        MessageCreated(MessageId("m1"), "assistant", TextContent("hi"), "end_turn", None),
        actor_id=CHILD,
        parent_actor_id=LEAD,
        turn_id=TurnId("turn-7"),
        occurred_at=1755590100.0,
        event_id="event-abc",
    )
    assert entry.entry_id == CanonicalEventId("event-abc")
    assert (entry.actor_id, entry.parent_actor_id) == (CHILD, LEAD)
    assert entry.turn_id == TurnId("turn-7")
    assert entry.occurred_at == 1755590100.0
    assert entry.entry_type == "message"
    assert entry.body == MessageBody(
        MessageId("m1"), "assistant", "end_turn", TextContent("hi"), None
    )


def test_an_actor_to_actor_message_is_a_message_with_a_recipient():
    entry = entry_of(
        MessageCreated(
            MessageId("m1"), "assistant", TextContent("go"), "intermediate", None, CHILD
        )
    )
    assert entry.body.recipient_actor_id == CHILD


@pytest.mark.parametrize(
    "payload",
    (
        SessionStarted("/work", "ref", None, None, None, None, None),
        ActorStarted("claude", "lead"),
        ActorFinished(None),
        TaskChanged(TaskId("t1"), "Read it", None, "pending", None),
        TaskListChanged("list", ()),
        GoalChanged("ship it", "active", None),
        UsageReported("actor", "lead", None, None, TokenUsage(1), True, None),
        ContextReported(1, 2, None),
        ShellOutputLocated(ShellId("sh1"), "/tmp/o", "chunk", False, 0, 0, False, "shell_finished"),
    ),
)
def test_plumbing_and_aggregate_facts_produce_no_entry(payload):
    """A feed that showed these would be showing machinery: they feed the
    aggregate, where the current value is the whole truth."""
    assert entry_of(payload) is None


def test_a_shell_entry_carries_the_command_and_the_harness_description_as_its_summary():
    entry = entry_of(
        ShellStarted(ShellId("sh9"), TextContent("make test"), "foreground", "Run the tests")
    )
    assert entry.summary == "Run the tests"
    assert entry.body == ShellStartedBody(ShellId("sh9"), TextContent("make test"), "foreground")


def test_output_arrives_as_immutable_chunks_for_the_client_to_fold():
    entry = entry_of(ShellProgressed(ShellId("sh9"), 0, "output", TextContent("142 passed\n"), "append"))
    assert entry.body == ShellOutputBody(ShellId("sh9"), "output", "append", TextContent("142 passed\n"))


def test_a_shell_finish_carries_three_states_and_the_exit_code_where_there_is_one():
    assert entry_of(ShellFinished(ShellId("sh9"), "succeeded", None, 0)).body == ShellFinishedBody(
        ShellId("sh9"), "succeeded", 0
    )
    assert entry_of(ShellFinished(ShellId("sh9"), "rejected", None, None)).body == ShellFinishedBody(
        ShellId("sh9"), "failed", None
    )
    assert entry_of(ShellFinished(ShellId("sh9"), "cancelled", None, None)).body == ShellFinishedBody(
        ShellId("sh9"), "cancelled", None
    )


def test_a_changed_file_is_shown_as_its_diff_and_a_read_one_as_its_text():
    changed = entry_of(
        FileAccessed(
            "/work/a.py",
            "updated",
            "succeeded",
            lines_added=1,
            lines_removed=1,
            unified_diff="@@ -1 +1 @@\n-a\n+b\n",
            content=TextContent("the whole file"),
        )
    )
    assert changed.body.content.text == "@@ -1 +1 @@\n-a\n+b\n"
    read = entry_of(FileAccessed("/work/a.py", "read", "succeeded", content=TextContent("print(1)")))
    assert read.body == FileBody("/work/a.py", "read", "succeeded", None, None, None, TextContent("print(1)"))


def test_a_turn_marker_carries_nothing_and_its_end_carries_how_it_ended():
    assert entry_of(TurnStarted(MessageId("m1"))).entry_type == "turn_started"
    assert entry_of(TurnFinished(None, "succeeded")).body == TurnFinishedBody("finished")
    assert entry_of(TurnAborted(None)).body == TurnFinishedBody("aborted")


def test_a_question_entry_keeps_the_choices_a_person_is_offered():
    from domain.values import AttentionChoice, AttentionPrompt

    asked = QuestionAsked(
        AttentionId("att-3"),
        (AttentionPrompt("q1", None, "Allow Bash?", False, (AttentionChoice("Yes", None),)),),
    )
    assert entry_of(asked).body == QuestionAskedBody(AttentionId("att-3"), asked.questions)


def test_a_model_change_entry_marks_a_fallback_the_harness_chose_for_you():
    automatic = entry_of(
        ModelChanged(
            ModelReference("claude-opus-5", "Opus 5", None),
            ModelReference("claude-fable-5", "Fable 5", None),
            "automatic_fallback",
        )
    )
    assert automatic.body == ModelChangeBody("Fable 5", "Opus 5", True)


def test_only_a_real_switch_reaches_the_feed():
    """Four facts a launch produces, and how many lines a reader should see: one,
    and only if they actually switched something.

    Measured on a live session (b29af821): the feed showed "model sonnet",
    "effort low", "model sonnet → sonnet-5" and "effort low" again, for a person
    who had chosen sonnet at low effort exactly once. Every one of those is a
    report, not a change. The fourth was not even the same actor — a subagent
    reporting its own effort three seconds later, which is per-actor state
    landing on a per-actor row and correct; it drew a line only because a report
    used to draw one.
    """
    launched = ModelReference("sonnet", "sonnet", "sonnet")
    resolved = ModelReference("claude-sonnet-5", "sonnet-5", "sonnet")

    # An initial report is not a change: nothing it replaced is known, which is
    # what `previous is None` means.
    assert body_of(ModelChanged(None, launched, "selected")) is None
    assert body_of(EffortChanged(None, "low", "selected")) is None
    # …and the same value again, from the harness's own stream, is not either.
    assert body_of(EffortChanged("low", "low", "reported_by_harness")) is None

    # A name being REFINED is not a change: same selection, two spellings.
    assert body_of(ModelChanged(launched, resolved, "reported_by_harness")) is None
    # …but the actor's row takes the better name, because that is what an
    # aggregate is for. The refinement lands; only the feed line goes.
    state = fold(
        *alive(),
        ModelChanged(None, launched, "selected"),
        ModelChanged(launched, resolved, "reported_by_harness"),
    )
    assert state.actor(LEAD).model == resolved

    # A person switching models IS a change: a different selection.
    assert body_of(
        ModelChanged(resolved, ModelReference("claude-opus-5", "Opus 5", "opus"), "selected")
    ) == ModelChangeBody("Opus 5", "sonnet-5", False)
    # So is a fallback the harness chose, and it says so.
    assert body_of(
        ModelChanged(resolved, ModelReference("claude-haiku-4-5", "haiku", None),
                     "automatic_fallback")
    ) == ModelChangeBody("haiku", "sonnet-5", True)
    # And so is a real effort switch.
    assert body_of(EffortChanged("low", "high", "selected")) == EffortChangeBody("high", "low")


def test_an_assignment_entry_carries_the_brief_as_its_summary():
    entry = entry_of(
        ActorAssignmentStarted(
            AssignmentId("a1"),
            TextContent("Get the weather"),
            actor_name="Explore",
            prompt=TextContent("look it up"),
        )
    )
    assert entry.summary == "Get the weather"
    assert entry.body == AssignmentStartedBody(AssignmentId("a1"), "Explore", TextContent("look it up"))


# --- the loop -----------------------------------------------------------------


class RecordingReaction(CanonicalEventReaction):
    def __init__(self) -> None:
        self.seen: list[str] = []

    def react(self, canonical_event: CanonicalEvent) -> None:
        self.seen.append(str(canonical_event.event_id))


class NoReactors:
    def plugin(self, harness: str):
        return type("Plugin", (), {"reactors": ()})()


class RecordingAudit:
    def __init__(self) -> None:
        self.failures: list[tuple[str, dict]] = []

    def error(self, session_id: str, where: str, context: dict) -> None:
        self.failures.append((where, context))


def loop_over(
    tmp_path,
    payloads,
    *,
    reaction: CanonicalEventReaction | None = None,
    listener: AppliedActorListener | None = None,
):
    """A real store, a real loop: facts in the log, rows out of the read model."""
    database = main_database(str(tmp_path / "main.db"))
    events = SqliteCanonicalEventRepository(database)
    read_model = SqliteSessionDataRepository(database)
    audit = RecordingAudit()
    loop = ReactionLoop(
        events,
        read_model,
        (reaction,) if reaction is not None else (),
        EntryWriter(),
        WRITERS,
        (listener,) if listener is not None else (),
        # Two collaborators the loop declares by CLASS rather than by protocol, so
        # a double cannot subtype them: a registry that offers no reactors, and an
        # audit that keeps its rows in a list for the test to read back. Cast
        # here, once, rather than widening the loop's own signature for a test.
        cast(HarnessRegistry, NoReactors()),
        cast(HarnessReactorContext, None),
        cast(AuditRecorder, audit),
    )
    _record(database, events, payloads)
    return loop, read_model, audit


def _record(database, events, payloads) -> None:
    """The facts, in the log, through the door they really arrive by: a recorded
    observation and the verdict reached about it."""
    recorder = SqliteRawEventRepository(database)
    for cursor, payload in enumerate(payloads, start=1):
        raw_event = RawEvent(
            raw_event_id=RawEventId(f"raw-{cursor}"),
            harness="example",
            source_type="fixture",
            source_name="fixture.jsonl",
            source_position=str(cursor),
            session_id=SESSION,
            actor_id=committed(payload, cursor=cursor).event.actor_id,
            parent_actor_id=None,
            observed_at=100.0,
            encoding="json",
            payload=b"{}",
        )
        recorder.record((raw_event,))
        events.record_translation(
            raw_event,
            "1",
            TranslationResult((committed(payload, cursor=cursor).event,), "translated"),
            time.time(),
        )


def test_the_loop_follows_the_cursor_and_stops_where_it_left_off(tmp_path):
    loop, read_model, audit = loop_over(tmp_path, alive())
    assert read_model.progress() == 0

    assert loop.tick() == 2
    assert audit.failures == []
    assert read_model.progress() == 2
    # Nothing new: nothing done, and nothing re-done.
    assert loop.tick() == 0
    assert read_model.read(SESSION).session.state == "running"


def test_one_event_commits_its_entry_and_its_rows_under_one_revision(tmp_path):
    """The single handshake the streams depend on: an entry and the aggregate
    change it implies share a revision, so no poll can see one without the
    other."""
    loop, read_model, _audit = loop_over(
        tmp_path,
        (*alive(), ShellStarted(ShellId("sh1"), TextContent("make test"), "foreground", None)),
    )
    loop.tick()

    data = read_model.read(SESSION)
    entries = read_model.entries_page(SESSION, limit=10).items
    assert [entry.entry_type for entry in entries] == ["shell_started"]
    assert entries[0].cursor == data.cursor
    # One read, one revision: the entry and the status it implies arrive
    # together, so no poll can see the command without the actor running it.
    delta = read_model.delta(SESSION, entries[0].cursor - 1)
    assert [entry.entry_type for entry in delta.entries] == ["shell_started"]
    assert [actor.status for actor in delta.actors] == ["executing"]


def test_an_event_that_changes_nothing_moves_the_mark_without_burning_a_cursor(tmp_path):
    """A cursor with no row behind it is a client's poll that returns nothing,
    every time, forever."""
    loop, read_model, _audit = loop_over(
        tmp_path,
        (
            *alive(),
            ShellOutputLocated(
                ShellId("sh1"), "/tmp/o", "chunk", False, 0, 0, False, "shell_finished"
            ),
        ),
    )
    loop.tick()
    assert read_model.progress() == 3
    assert read_model.read(SESSION).cursor == 2


def test_the_read_model_is_rebuilt_from_the_log_without_replaying_a_side_effect(tmp_path):
    """The whole point of the live-versus-replay boundary: a rebuild folds every
    fact again, and if the reactions rode along, every session that ever finished
    would reopen its panes and re-announce its work."""
    reaction = RecordingReaction()
    loop, read_model, _audit = loop_over(
        tmp_path,
        (*alive(), MessageCreated(MessageId("m1"), "user", TextContent("go"), "prompt", None)),
        reaction=reaction,
    )
    loop.tick()
    live = read_model.read(SESSION)
    seen_live = list(reaction.seen)
    assert len(seen_live) == 3

    assert loop.rebuild() == 3

    rebuilt = read_model.read(SESSION)
    assert rebuilt.session == live.session
    assert rebuilt.actors == live.actors
    assert [entry.entry_type for entry in read_model.entries_page(SESSION, limit=10).items] == [
        "message"
    ]
    assert reaction.seen == seen_live


def test_replaying_an_event_writes_its_entry_once(tmp_path):
    """A crash between the rows and the mark replays the event, so the insert has
    to be idempotent — the entry's id is the event's, and it is UNIQUE."""
    loop, read_model, _audit = loop_over(
        tmp_path,
        (*alive(), MessageCreated(MessageId("m1"), "user", TextContent("go"), "prompt", None)),
    )
    loop.tick()
    loop.rebuild()
    loop.rebuild()
    assert len(read_model.entries_page(SESSION, limit=10).items) == 1


def test_a_writer_that_raises_is_audited_and_the_loop_carries_on(tmp_path):
    """Nothing restarts this thread, so no single fact may end it."""

    class BrokenWriter:
        def write(self, canonical_event, state):
            raise RuntimeError("no")

    database = main_database(str(tmp_path / "main.db"))
    events = SqliteCanonicalEventRepository(database)
    read_model = SqliteSessionDataRepository(database)
    audit = RecordingAudit()
    loop = ReactionLoop(
        events, read_model, (), EntryWriter(), (BrokenWriter(),), (), NoReactors(), None, audit
    )
    _record(database, events, alive())

    assert loop.tick() == 2
    assert [where for where, _context in audit.failures] == [
        "reactions (session data)",
        "reactions (session data)",
    ]
    # The mark did not move: the next tick sees the same facts again.
    assert read_model.progress() == 0


# --- what a committed change causes -------------------------------------------


class RecordingTabs:
    def __init__(self) -> None:
        self.painted: list[tuple[str, object]] = []

    def paint_session_tab(self, session_id, appearance):
        self.painted.append(("paint", appearance.active_background))

    def clear_session_tab(self, session_id):
        self.painted.append(("clear", None))


class FixedSessions:
    def __init__(self, lead_actor_id: ActorId) -> None:
        self.lead_actor_id = lead_actor_id

    def find(self, session_id):
        return Session(SESSION, self.lead_actor_id, "native", "fixture", "/work")


def test_the_tab_is_painted_from_the_status_that_was_just_committed(tmp_path):
    """The listener runs AFTER apply, which is the whole reason it is not a
    reaction: a reaction runs before the writers, where the status is still the
    previous one — so a session going idle would keep its old colour until some
    later, unrelated fact arrived."""
    from terminal.tabs import TabColorPainter

    tabs = RecordingTabs()
    painter = TabColorPainter(tabs, FixedSessions(LEAD))
    loop, read_model, _audit = loop_over(
        tmp_path,
        (*alive(), ShellStarted(ShellId("sh1"), TextContent("make test"), "foreground", None)),
        listener=painter,
    )

    loop.tick()

    # idle at birth, then executing — and nothing in between, because the
    # painter repaints only what changed.
    assert [action for action, _colour in tabs.painted] == ["paint", "paint"]
    assert read_model.read(SESSION).actors[0].status == "executing"


def test_a_finished_session_has_its_tab_colour_cleared(tmp_path):
    from terminal.tabs import TabColorPainter

    tabs = RecordingTabs()
    loop, _read_model, _audit = loop_over(
        tmp_path,
        (*alive(), SessionFinished("succeeded", None)),
        listener=TabColorPainter(tabs, FixedSessions(LEAD)),
    )

    loop.tick()

    assert [action for action, _colour in tabs.painted] == ["paint", "clear"]


def test_a_subagents_status_never_paints_the_sessions_tab(tmp_path):
    """A tab shows a session and a session shows its lead: a subagent turning
    red because it asked ITSELF something is not the session asking you."""
    from terminal.tabs import TabColorPainter

    tabs = RecordingTabs()
    loop, _read_model, _audit = loop_over(
        tmp_path,
        (
            *alive(),
            committed(ActorStarted("Explore", "child"), actor_id=CHILD, cursor=3).event.payload,
        ),
        listener=TabColorPainter(tabs, FixedSessions(LEAD)),
    )
    loop.tick()
    painted_before = list(tabs.painted)

    # …and a fact about the child changes nothing on the tab.
    loop.tick()
    assert tabs.painted == painted_before


def test_a_rebuild_repaints_nothing(tmp_path):
    """A replay of history through the writers must not touch the world: every
    session that ever finished would otherwise repaint its tab."""
    from terminal.tabs import TabColorPainter

    tabs = RecordingTabs()
    loop, _read_model, _audit = loop_over(
        tmp_path, alive(), listener=TabColorPainter(tabs, FixedSessions(LEAD))
    )
    loop.tick()
    painted_live = list(tabs.painted)
    assert painted_live

    loop.rebuild()

    assert tabs.painted == painted_live
