"""The /sessionData surface: what the two frontends are actually promised.

The suite that replaced the dashboard read tests. It asserts the WIRE — the
shapes, the cursor contract, and the two read-time fields — because that is the
whole of what a client may rely on. Nothing here reaches past the read model,
which is the point of the redesign: a route that needed a fold would be a route
that could be slow.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

import pytest

from domain.entries import (
    EntryBody,
    FileState,
    MessageBody,
    PlanProposedBody,
    PlanResolvedBody,
    QuestionAnsweredBody,
    QuestionAskedBody,
    RunState,
    SessionEntry,
    ShellStartedBody,
    TurnState,
)
from domain.ids import (
    AccountId,
    ActorId,
    AttentionId,
    CanonicalEventId,
    HarnessName,
    MessageId,
    ModelId,
    SelectionId,
    SessionId,
    ShellId,
    TaskId,
    TurnId,
)
from domain.sessiondata import (
    ActorContext,
    ActorFacts,
    ActorStatistics,
    ActorStatus,
    ActorUsage,
    LifecycleState,
    SessionData,
    SessionFacts,
    SessionGoal,
    SessionTask,
)
from domain.values import (
    AccountReference,
    ActorRole,
    AttentionAnswer,
    AttentionChoice,
    AttentionPrompt,
    ExecutionMode,
    FileAction,
    MessagePhase,
    MessageRole,
    ModelReference,
    OutputMode,
    PlanState,
    ProgressStream,
    StructuredContent,
    TaskState,
    TextContent,
    TokenUsage,
    WorktreeAction,
)
from api.sessiondata import mapper, streams
from core.repository import RepositoryStatus
from decimal import Decimal
from repository.contract.session_data import SessionDataChanges
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.session_data import SqliteSessionDataRepository

SESSION = SessionId("session-one")
LEAD = ActorId("session-one:lead")


# One of each, and `dataclasses.replace` for the differences. A dict of defaults
# updated with kwargs is the same builder untyped: every field arrives as
# `object`, so a Literal spelled wrong would reach the mapper unremarked — and
# what these tests check IS the mapping.
FACTS = SessionFacts(
    session_id=SESSION,
    harness=HarnessName("claude_code"),
    state=LifecycleState.RUNNING,
    working_directory="/work/baqylau",
    started_at=1755590000.0,
    lead_actor_id=LEAD,
    title="Fix the SSE reconnect bug",
    account=AccountReference(AccountId("acc_01"), "zhambyl"),
)
ACTOR = ActorFacts(
    session_id=SESSION,
    actor_id=LEAD,
    role=ActorRole.LEAD,
    name="Claude",
    state=LifecycleState.RUNNING,
    status=ActorStatus.EXECUTING,
    model=ModelReference(ModelId("claude-fable-5"), "Fable 5", SelectionId("fable")),
    effort="high",
)


AN_ENTRY_ID = CanonicalEventId("event-one")


def entry(
    body: EntryBody,
    *,
    entry_id: CanonicalEventId = AN_ENTRY_ID,
    occurred_at: float = 1755590100.0,
) -> SessionEntry:
    """One entry. Named parameters rather than `**overrides`, because the two
    things a test varies are its identity and its clock, and both are typed."""
    return SessionEntry(
        entry_id=entry_id,
        session_id=SESSION,
        actor_id=LEAD,
        parent_actor_id=None,
        turn_id=TurnId("turn-7"),
        occurred_at=occurred_at,
        summary=None,
        body=body,
        cursor=4810,
    )


# --- the aggregate on the wire ------------------------------------------------


def test_the_snapshot_carries_the_facts_the_world_state_and_one_cursor():
    """The three kinds of thing a client is given, and the boundary between
    them: stored facts, read-time truths, and the cursor that ties the snapshot
    to the page and the stream."""
    data = SessionData(
        session=replace(
            FACTS,
            goal=SessionGoal("ship the redesign", False),
            tasks=(SessionTask(TaskId("t1"), "Fix the reconnect", None, TaskState.IN_PROGRESS, LEAD),),
        ),
        actors=(ACTOR,),
        cursor=4812,
    )

    response = mapper.session_data(
        data, live=True, repository_status=RepositoryStatus("main", None, True)
    )

    assert response.cursor == 4812
    assert response.session.session_id == "session-one"
    assert response.session.title == "Fix the SSE reconnect bug"
    assert response.session.state == "running"
    assert response.session.working_directory == "/work/baqylau"
    assert response.session.account.display_name == "zhambyl"
    assert response.session.goal.objective == "ship the redesign"
    assert [task.subject for task in response.session.tasks] == ["Fix the reconnect"]
    # Beside the facts, not inside them: an SSE frame carries the same `session`
    # shape and cannot know either of these.
    assert response.live is True
    assert response.repository.branch == "main"
    assert not hasattr(response.session, "live")


def test_an_actor_carries_one_model_name_and_never_the_ids_behind_it():
    """The picker gets its selectable ids from the harness catalog; a reader
    needs the name. The native id stays in the read model, where the relaunch
    path reads it."""
    response = mapper.actor(ACTOR)
    assert response.model == "Fable 5"
    assert response.effort == "high"
    assert response.status == "executing"
    assert "native_id" not in response.model_dump_json()


def test_an_actors_numbers_are_its_own():
    response = mapper.actor(
        replace(
            ACTOR,
            usage=ActorUsage(TokenUsage(input_tokens=12000, output_tokens=4100), Decimal("1.42")),
            context=ActorContext(used_tokens=61000, window_tokens=200000, compacting=False),
            statistics=ActorStatistics(
                prompt_count=7,
                shell_command_count=12,
                failed_shell_command_count=1,
                file_count=4,
                lines_added=120,
                lines_removed=30,
                actor_message_count=2,
                tool_counts=(("Bash", 12), ("Read", 4)),
                active_seconds=1240.0,
            ),
        )
    )

    assert response.usage.tokens.input_tokens == 12000
    # A string, because money is a decimal and JSON has one number type.
    assert response.usage.cost_in_usd == "1.42"
    assert response.context.used_tokens == 61000
    assert {count.tool: count.count for count in response.statistics.tool_counts} == {
        "Bash": 12,
        "Read": 4,
    }
    assert response.statistics.active_seconds == 1240.0


def test_an_open_interval_is_added_when_the_route_answers():
    """`active_seconds` cannot be a stored number that grows on its own — the
    stored part is the closed intervals, and the one still open is measured
    against now."""
    response = mapper.actor(
        replace(
            ACTOR,
            statistics=ActorStatistics(active_seconds=100.0, active_since_internal=1000.0),
        ),
        now=1030.0,
    )
    assert response.statistics.active_seconds == 130.0


def test_the_writers_own_memory_never_reaches_a_client():
    """The internal fields exist so a restart resumes the fold. They are not
    facts about the session, and nothing outside the writers may see them."""
    response = mapper.session_data(
        SessionData(
            session=replace(
                FACTS,
                prompt_title_internal="the first thing asked",
                task_order_internal=(TaskId("t1"),),
            ),
            actors=(
                replace(
                    ACTOR,
                    pending_attention_internal=(AttentionId("att-3"),),
                    statistics=ActorStatistics(file_paths_internal=("/work/a.py",)),
                ),
            ),
            cursor=1,
        ),
        live=False,
        repository_status=None,
    )

    encoded = response.model_dump_json()
    assert "internal" not in encoded
    assert "the first thing asked" not in encoded
    assert "/work/a.py" not in encoded


# --- the feed on the wire -----------------------------------------------------


def test_an_entry_carries_its_envelope_and_its_typed_body():
    response = mapper.entry(
        entry(
            MessageBody(MessageId("m1"), MessageRole.ASSISTANT, MessagePhase.END_TURN, TextContent("Done.")),
        )
    )
    assert response.entry_id == "event-one"
    assert response.type == "message"
    assert response.cursor == 4810
    assert response.turn_id == "turn-7"
    assert response.occurred_at == 1755590100.0
    assert response.body.role == "assistant"
    assert response.body.content.text == "Done."


def test_content_says_how_to_draw_itself():
    """Markdown or not is a fact the harness told us; a client that had to guess
    by role would render a plain-text tool result as markdown."""
    markdown = mapper.entry(
        entry(
            MessageBody(
                MessageId("m1"), MessageRole.ASSISTANT, MessagePhase.END_TURN, TextContent("**bold**", "text/markdown")
            )
        )
    )
    assert markdown.body.content.media_type == "text/markdown"

    structured = mapper.entry(
        entry(ShellStartedBody(ShellId("sh1"), StructuredContent('{"b":2,"a":1}'), ExecutionMode.FOREGROUND))
    )
    # A document in a shape we do not define is laid out as the text a person
    # reads — the only thing a client can do with it.
    assert structured.body.command.media_type == "text/plain"
    assert '"a": 1' in structured.body.command.text


def test_a_question_entry_offers_labels_and_nothing_else():
    """The label IS the value: both harnesses answer with the label they were
    shown, so a second spelling was a mapping every client had to keep."""
    response = mapper.entry(
        entry(
            QuestionAskedBody(
                AttentionId("att-3"),
                (
                    AttentionPrompt(
                        "q1",
                        "Permissions",
                        "Allow Bash?",
                        False,
                        (AttentionChoice("Yes", "go ahead"), AttentionChoice("No", None)),
                    ),
                ),
            )
        )
    )
    question = response.body.questions[0]
    assert (question.question_id, question.question) == ("q1", "Allow Bash?")
    assert [choice.label for choice in question.choices] == ["Yes", "No"]
    assert "value" not in response.model_dump_json()


def test_every_entry_kind_has_a_wire_shape():
    """Exhaustiveness, checked rather than trusted: an entry kind the api layer
    never decided how to expose would otherwise reach a client as `{}`."""
    from domain.entries import BODY_TYPES

    unmapped = []
    for name, body_type in BODY_TYPES.items():
        try:
            mapper.entry_body(_sample(body_type))
        except TypeError:
            unmapped.append(name)
    assert unmapped == []


def _sample(body_type):
    """One of each body, built with the least it accepts."""
    samples = {
        "TurnStartedBody": lambda: body_type(),
        "TurnFinishedBody": lambda: body_type(TurnState.FINISHED),
        "MessageBody": lambda: body_type(MessageId("m"), MessageRole.USER, MessagePhase.PROMPT, TextContent("x")),
        "ReasoningBody": lambda: body_type("r", TextContent("x")),
        "ShellStartedBody": lambda: body_type(ShellId("s"), TextContent("ls"), ExecutionMode.FOREGROUND),
        "ShellOutputBody": lambda: body_type(ShellId("s"), ProgressStream.OUTPUT, OutputMode.APPEND, TextContent("x")),
        "ShellBackgroundedBody": lambda: body_type(ShellId("s")),
        "ShellFinishedBody": lambda: body_type(ShellId("s"), RunState.SUCCEEDED),
        "FileBody": lambda: body_type("/p", FileAction.READ, FileState.SUCCEEDED),
        "SearchBody": lambda: body_type("Grep", TextContent("q"), FileState.SUCCEEDED),
        "WebBody": lambda: body_type("https://x", FileState.SUCCEEDED),
        "WorktreeBody": lambda: body_type(WorktreeAction.ENTERED, FileState.SUCCEEDED),
        "SkillStartedBody": lambda: body_type("k", "audit-debug"),
        "SkillFinishedBody": lambda: body_type("k", RunState.SUCCEEDED),
        "QuestionAskedBody": lambda: body_type(AttentionId("a"), ()),
        "QuestionAnsweredBody": lambda: body_type(AttentionId("a")),
        "PlanProposedBody": lambda: body_type(AttentionId("a"), TextContent("plan")),
        "PlanResolvedBody": lambda: body_type(AttentionId("a"), PlanState.APPROVED),
        "CompactionStartedBody": lambda: body_type(),
        "CompactionFinishedBody": lambda: body_type(),
        "AssignmentStartedBody": lambda: body_type("as"),
        "AssignmentFinishedBody": lambda: body_type("as"),
        "ModelChangeBody": lambda: body_type("Fable 5"),
        "EffortChangeBody": lambda: body_type("high"),
    }
    return samples[body_type.__name__]()


# --- the cursor contract, over a real store -----------------------------------


def store(tmp_path) -> SqliteSessionDataRepository:
    return SqliteSessionDataRepository(main_database(str(tmp_path / "main.db")))


def test_a_page_taken_at_the_snapshots_cursor_and_a_stream_from_it_never_overlap(tmp_path):
    """The whole boundary contract in one test: whatever the page ends with, the
    stream starts after — no entry twice, none missed."""
    read_model = store(tmp_path)
    read_model.apply(SESSION, SessionDataChanges(session=FACTS, actors=(ACTOR,)), 1)
    for ordinal in range(1, 4):
        read_model.apply(
            SESSION,
            SessionDataChanges(
                entry=entry(
                    MessageBody(MessageId("m%d" % ordinal), MessageRole.USER, MessagePhase.PROMPT, TextContent("go")),
                    entry_id=CanonicalEventId("event-%d" % ordinal),
                )
            ),
            ordinal + 1,
        )

    snapshot = read_model.read(SESSION)
    page = read_model.entries_page(SESSION, at=snapshot.cursor, limit=100)
    assert [item.entry_id for item in page.items] == ["event-1", "event-2", "event-3"]

    # Nothing new yet: the stream's first poll from that cursor is empty.
    assert read_model.delta(SESSION, snapshot.cursor).empty

    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                MessageBody(MessageId("m4"), MessageRole.USER, MessagePhase.PROMPT, TextContent("more")),
                entry_id=CanonicalEventId("event-4"),
            )
        ),
        5,
    )
    delta = read_model.delta(SESSION, snapshot.cursor)
    assert [item.entry_id for item in delta.entries] == ["event-4"]


def test_a_pending_question_is_derived_and_stops_being_pending_when_answered(tmp_path):
    """No stored flag: an asked entry whose answer has not arrived. A flag would
    be a second answer to the same question, and it could disagree with the feed
    the person is looking at."""
    read_model = store(tmp_path)
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                QuestionAskedBody(AttentionId("att-1"), ()),
                entry_id=CanonicalEventId("asked-1"),
            )
        ),
        1,
    )
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                PlanProposedBody(AttentionId("att-2"), TextContent("do it")),
                entry_id=CanonicalEventId("proposed-2"),
            )
        ),
        2,
    )
    assert [item.entry_id for item in read_model.pending_attention(SESSION)] == [
        "asked-1",
        "proposed-2",
    ]

    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                QuestionAnsweredBody(AttentionId("att-1"), (AttentionAnswer("q1", ("Yes",)),)),
                entry_id=CanonicalEventId("answered-1"),
            )
        ),
        3,
    )
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                PlanResolvedBody(AttentionId("att-2"), PlanState.APPROVED),
                entry_id=CanonicalEventId("resolved-2"),
            )
        ),
        4,
    )
    assert read_model.pending_attention(SESSION) == ()


def test_the_entries_of_one_kind_are_read_without_paging_the_whole_feed(tmp_path):
    read_model = store(tmp_path)
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                MessageBody(MessageId("m1"), MessageRole.USER, MessagePhase.PROMPT, TextContent("go")),
                entry_id=CanonicalEventId("message-1"),
            )
        ),
        1,
    )
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                ShellStartedBody(ShellId("sh1"), TextContent("make test"), ExecutionMode.FOREGROUND),
                entry_id=CanonicalEventId("shell-1"),
            )
        ),
        2,
    )

    assert [item.entry_id for item in read_model.entries_of_types(SESSION, ("message",))] == [
        "message-1"
    ]
    assert read_model.entries_of_types(SESSION, ()) == ()


def test_the_last_activity_is_the_newest_entry_not_a_stored_clock(tmp_path):
    """Two consumers need it — the resume picker and the list — and storing it on
    the session row would rewrite that row on every single fact."""
    read_model = store(tmp_path)
    read_model.apply(SESSION, SessionDataChanges(session=FACTS), 1)
    assert read_model.read(SESSION).last_activity_at is None

    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                MessageBody(MessageId("m1"), MessageRole.USER, MessagePhase.PROMPT, TextContent("go")),
                occurred_at=1755599999.0,
            )
        ),
        2,
    )
    assert read_model.read(SESSION).last_activity_at == 1755599999.0


def test_an_unknown_session_has_no_aggregate_at_all(tmp_path):
    assert store(tmp_path).read(SessionId("never-seen")) is None


def test_the_wire_shapes_survive_a_round_trip_through_the_store(tmp_path):
    """End to end: what a writer produced, through SQLite, out as JSON."""
    read_model = store(tmp_path)
    read_model.apply(
        SESSION,
        SessionDataChanges(
            session=FACTS,
            actors=(ACTOR,),
            entry=entry(ShellStartedBody(ShellId("sh9"), TextContent("make test"), ExecutionMode.BACKGROUND)),
        ),
        1,
    )

    data = read_model.read(SESSION)
    response = mapper.session_data(data, live=True, repository_status=None, now=time.time())
    page = mapper.entry_page(read_model.entries_page(SESSION, limit=10))

    assert response.session.title == "Fix the SSE reconnect bug"
    assert response.actors[0].model == "Fable 5"
    assert page.items[0].type == "shell_started"
    assert page.items[0].body.execution == "background"
    assert page.items[0].body.command.text == "make test"


# --- the streams --------------------------------------------------------------
#
# Driven as the generators they are, not through a socket: what is under test is
# the poll's contract — one frame per poll with news, carrying everything found,
# stamped with the cursor to resume from — and a socket only adds a way for the
# test to hang.


class SilentAudit:
    def __init__(self) -> None:
        self.failures: list[tuple[str, dict]] = []

    def error(self, session_id: str, where: str, context: dict) -> None:
        self.failures.append((where, context))


def frame_body(frame: str) -> dict:
    """The `data:` line of one SSE frame, as the object it is."""
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(frame)


def frame_id(frame: str) -> int:
    for line in frame.splitlines():
        if line.startswith("id: "):
            return int(line[len("id: "):])
    raise AssertionError(frame)


def test_a_session_stream_sends_one_frame_per_poll_with_news(tmp_path):
    read_model = store(tmp_path)
    read_model.apply(SESSION, SessionDataChanges(session=FACTS, actors=(ACTOR,)), 1)

    async def frames():
        stream = streams._session_frames(read_model, SilentAudit(), SESSION, 0)
        first = await asyncio.wait_for(stream.__anext__(), 3)
        # Everything committed so far, in one frame.
        read_model.apply(
            SESSION,
            SessionDataChanges(
                entry=entry(MessageBody(MessageId("m1"), MessageRole.USER, MessagePhase.PROMPT, TextContent("go"))),
                actors=(replace(ACTOR, status=ActorStatus.THINKING),),
            ),
            2,
        )
        second = await asyncio.wait_for(stream.__anext__(), 3)
        await stream.aclose()
        return first, second

    first, second = asyncio.run(frames())

    opening = frame_body(first)
    assert opening["session"]["title"] == "Fix the SSE reconnect bug"
    assert [row["status"] for row in opening["actors"]] == ["executing"]
    assert opening["entries"] == []

    news = frame_body(second)
    # Only what changed: the session part is absent, the actor is the new one,
    # and the entry rides the same frame as the status it implies.
    assert news["session"] is None
    assert [row["status"] for row in news["actors"]] == ["thinking"]
    assert [item["type"] for item in news["entries"]] == ["message"]
    assert frame_id(second) > frame_id(first)


def test_a_stream_resumes_from_the_id_the_client_last_saw(tmp_path):
    """The frame id is a database cursor, not a sequence number the server
    invents — so whatever the client last saw, the next poll returns exactly
    what committed after it, across a reconnect or a daemon restart."""
    read_model = store(tmp_path)
    read_model.apply(SESSION, SessionDataChanges(session=FACTS, actors=(ACTOR,)), 1)
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                MessageBody(MessageId("m1"), MessageRole.USER, MessagePhase.PROMPT, TextContent("first")),
                entry_id=CanonicalEventId("event-1"),
            )
        ),
        2,
    )
    boundary = read_model.read(SESSION).cursor
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(
                MessageBody(MessageId("m2"), MessageRole.USER, MessagePhase.PROMPT, TextContent("second")),
                entry_id=CanonicalEventId("event-2"),
            )
        ),
        3,
    )

    async def reconnect():
        stream = streams._session_frames(read_model, SilentAudit(), SESSION, boundary)
        frame = await asyncio.wait_for(stream.__anext__(), 3)
        await stream.aclose()
        return frame

    resumed = frame_body(asyncio.run(reconnect()))
    assert [item["entry_id"] for item in resumed["entries"]] == ["event-2"]


def test_an_aggregate_only_change_advances_the_cursor_it_was_sent_with(tmp_path):
    """Otherwise the same row comes back every quarter second for the life of
    the connection: an actor row's revision is a column, not something the actor
    object carries, so the frame id has to come from the read that found it."""
    read_model = store(tmp_path)
    read_model.apply(SESSION, SessionDataChanges(session=FACTS, actors=(ACTOR,)), 1)

    async def two_polls():
        stream = streams._session_frames(read_model, SilentAudit(), SESSION, 0)
        first = await asyncio.wait_for(stream.__anext__(), 3)
        # Nothing new: the next frame must not arrive at all.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(stream.__anext__(), 0.8)
        await stream.aclose()
        return first

    first = asyncio.run(two_polls())
    assert frame_body(first)["actors"] != []


def test_the_global_stream_carries_every_session_and_no_entries(tmp_path):
    """It drives the list and the tab colours, and neither reads an entry."""
    read_model = store(tmp_path)
    other = SessionId("session-two")
    read_model.apply(SESSION, SessionDataChanges(session=FACTS, actors=(ACTOR,)), 1)
    read_model.apply(
        other,
        SessionDataChanges(
            session=replace(FACTS, session_id=other, title="Another"),
            actors=(replace(ACTOR, session_id=other),),
        ),
        2,
    )
    read_model.apply(
        SESSION,
        SessionDataChanges(
            entry=entry(MessageBody(MessageId("m1"), MessageRole.USER, MessagePhase.PROMPT, TextContent("go")))
        ),
        3,
    )

    async def frames():
        stream = streams._global_frames(read_model, SilentAudit(), 0)
        frame = await asyncio.wait_for(stream.__anext__(), 3)
        await stream.aclose()
        return frame

    body = frame_body(asyncio.run(frames()))
    assert {row["session_id"] for row in body["sessions"]} == {"session-one", "session-two"}
    assert len(body["actors"]) == 2
    assert "entries" not in body


def test_a_stream_that_fails_says_so_and_ends_so_the_client_reconnects(tmp_path):
    """An SSE stream drives a whole view; dying silently would leave a page
    frozen with no way to know it."""

    class BrokenReadModel:
        def delta(self, session_id, cursor):
            raise RuntimeError("no")

    audit = SilentAudit()

    async def frames():
        stream = streams._session_frames(BrokenReadModel(), audit, SESSION, 0)
        frame = await asyncio.wait_for(stream.__anext__(), 3)
        await stream.aclose()
        return frame

    frame = asyncio.run(frames())
    assert frame_body(frame) == {"error": "stream failed"}
    assert [where for where, _context in audit.failures] == ["session data stream"]
