"""Semantic projection tests independent of either harness and presenter."""

from __future__ import annotations

from decimal import Decimal

import pytest

from harness.models import RawEvent, Session, TranslationResult
from domain.events import (
    ActorFinished,
    ActorStarted,
    AttentionRequested,
    AttentionResolved,
    CanonicalEvent,
    ContextReported,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CompactionFinished,
    CompactionStarted,
    GoalChanged,
    FileAccessed,
    MessageCreated,
    ModelChanged,
    OperationFinished,
    OperationInputProvided,
    OperationProgressed,
    OperationStarted,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    SessionWorkingDirectoryChanged,
    TaskChanged,
    TaskListChanged,
    TurnFinished,
    TurnStarted,
    UsageReported,
)
from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    AssignmentId,
    MessageId,
    OperationId,
    RawEventId,
    SessionId,
    TaskId,
    TurnId,
)
from domain.values import AttentionPrompt, ModelReference, StructuredContent, TextContent, TokenUsage
from canonical_runtime import CanonicalRuntime
from runtime.projections import ActivityScope, ActorAssignmentActivity, OperationActivity

SESSION_ID = SessionId("session-one")
LEAD_ACTOR_ID = ActorId("actor-lead")


def canonical(
    event_id: str,
    payload,
    *,
    actor_id: ActorId = LEAD_ACTOR_ID,
    parent_actor_id: ActorId | None = None,
    turn_id: TurnId | None = None,
    occurred_at: float | None = 10.0,
):
    return CanonicalEvent(
        event_id=CanonicalEventId(event_id),
        session_id=SESSION_ID,
        actor_id=actor_id,
        turn_id=turn_id,
        parent_actor_id=parent_actor_id,
        harness="example",
        occurred_at=occurred_at,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )


def store_with_events(tmp_path, events):
    next_accepted_at = [100.0]

    def clock():
        accepted_at = next_accepted_at[0]
        next_accepted_at[0] += 1.0
        return accepted_at

    store = CanonicalRuntime(str(tmp_path / "events.db"), clock=clock)
    store.register(
        "example",
        Session(SESSION_ID, LEAD_ACTOR_ID, "native", "fixture", "/work"),
    )
    for index, event in enumerate(events):
        raw = RawEvent(
            raw_event_id=RawEventId(f"raw-{index}"),
            harness="example",
            source_type="fixture",
            source_name="fixture",
            source_position=str(index),
            session_id=SESSION_ID,
            actor_id=event.actor_id,
            parent_actor_id=event.parent_actor_id,
            observed_at=100.0 + index,
            encoding="json",
            payload=f'{{"index":{index}}}'.encode(),
        )
        store.record(raw, "1", TranslationResult((event,), "translated"))
    return store


def test_session_page_cache_appends_only_the_new_cursor_range(monkeypatch, tmp_path):
    store = store_with_events(
        tmp_path,
        [canonical("session-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None))],
    )
    queries = store.queries()
    assert queries.summary(SESSION_ID).title is None

    title_event = canonical("title", SessionTitleChanged("Incremental", "custom"))
    store.record(
        RawEvent(
            raw_event_id=RawEventId("raw-title"),
            harness="example",
            source_type="fixture",
            source_name="fixture",
            source_position="title",
            session_id=SESSION_ID,
            actor_id=LEAD_ACTOR_ID,
            parent_actor_id=None,
            observed_at=101.0,
            encoding="json",
            payload=b'{"title":"Incremental"}',
        ),
        "1",
        TranslationResult((title_event,), "translated"),
    )
    monkeypatch.setattr(
        store,
        "through",
        lambda *_arguments, **_options: pytest.fail("complete session page was reloaded"),
    )

    assert queries.summary(SESSION_ID).title == "Incremental"


def test_session_summary_folds_metadata_prompts_model_change_and_finish(tmp_path):
    events = [
        canonical("session-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        canonical("title", SessionTitleChanged("Canonical work", "custom"), occurred_at=11.0),
        canonical(
            "directory",
            SessionWorkingDirectoryChanged("/work/changed"),
            occurred_at=11.5,
        ),
        canonical(
            "prompt",
            MessageCreated(MessageId("message-one"), "user", TextContent("do it"), "prompt", None),
            occurred_at=12.0,
        ),
        canonical(
            "model",
            ModelChanged(None, ModelReference("model-two", "Model Two", None), "automatic_fallback"),
            occurred_at=13.0,
        ),
        canonical("session-finish", SessionFinished("succeeded", None), occurred_at=14.0),
    ]
    summary = store_with_events(tmp_path, events).queries().summary(SESSION_ID)
    assert summary is not None
    assert summary.working_directory == "/work/changed"
    assert summary.initial_working_directory == "/work"
    assert summary.title == "Canonical work"
    assert summary.prompt_count == 1
    assert summary.model == ModelReference("model-two", "Model Two", None)
    assert summary.automatic_model_change is not None
    assert summary.state == "finished" and summary.finished_at == 14.0


def test_session_summary_returns_to_running_when_the_session_is_resumed(tmp_path):
    events = [
        canonical("first-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        canonical("first-finish", SessionFinished("unknown", "process_exited")),
        canonical("resume-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
    ]
    queries = store_with_events(tmp_path, events).queries()

    summary = queries.summary(SESSION_ID)

    assert summary is not None
    assert summary.state == "running"
    assert summary.finished_at is None


def test_session_list_is_a_semantic_query_sorted_by_start_time(tmp_path):
    store = store_with_events(
        tmp_path,
        [canonical("session-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None))],
    )
    second_session_id = SessionId("session-two")
    second_actor_id = ActorId("actor-two")
    store.register(
        "example",
        Session(second_session_id, second_actor_id, "native-two", "fixture-two", "/work"),
    )
    second_start = CanonicalEvent(
        CanonicalEventId("session-two-start"),
        second_session_id,
        second_actor_id,
        None,
        None,
        "example",
        20.0,
        None,
        None,
        SessionStarted("/work", "fixture.jsonl", None, None, None, None, None),
    )
    second_raw = RawEvent(
        RawEventId("session-two-raw"),
        "example",
        "fixture",
        "fixture-two",
        "0",
        second_session_id,
        second_actor_id,
        None,
        20.0,
        "json",
        b"{}",
    )
    store.record(second_raw, "1", TranslationResult((second_start,), "translated"))

    assert [summary.session_id for summary in store.queries().sessions()] == [
        second_session_id,
        SESSION_ID,
    ]


def test_custom_session_title_is_not_overwritten_by_later_automatic_title(tmp_path):
    events = [
        canonical("session-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        canonical("summary-title", SessionTitleChanged("Summary", "summary")),
        canonical("custom-title", SessionTitleChanged("Chosen", "custom")),
        canonical("automatic-title", SessionTitleChanged("Generated later", "automatic")),
    ]

    summary = store_with_events(tmp_path, events).queries().summary(SESSION_ID)

    assert summary is not None
    assert summary.title == "Chosen"


def test_activity_joins_operation_progress_and_finish_by_identity(tmp_path):
    operation_id = OperationId("operation-one")
    events = [
        canonical("session-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        canonical(
            "operation-start",
            OperationStarted(
                operation_id,
                "shell",
                "shell",
                "background",
                StructuredContent('{"command":"make test"}'),
                None,
                None,
            ),
            occurred_at=11.0,
        ),
        canonical(
            "operation-progress",
            OperationProgressed(operation_id, 0, "output", TextContent("running"), "append"),
            occurred_at=12.0,
        ),
        canonical(
            "operation-input",
            OperationInputProvided(operation_id, TextContent("yes\n"), False),
            occurred_at=12.5,
        ),
        canonical(
            "operation-finish",
            OperationFinished(operation_id, "succeeded", TextContent("passed"), 0),
            occurred_at=13.0,
        ),
    ]
    queries = store_with_events(tmp_path, events).queries()
    page = queries.activity_after(SESSION_ID, 0, ActivityScope(), 10)
    assert len(page.activities) == 1
    operation = page.activities[0]
    assert isinstance(operation, OperationActivity)
    assert operation.state == "finished"
    assert operation.progress[0].content == TextContent("running")
    assert operation.result == TextContent("passed")
    assert operation.context.source_event_ids == (
        CanonicalEventId("operation-start"),
        CanonicalEventId("operation-progress"),
        CanonicalEventId("operation-finish"),
    )
    background = queries.background_work(SESSION_ID, ActivityScope())
    assert background.running_operation_ids == ()
    assert background.background_job_count == 1


def test_activity_uses_accepted_time_when_native_time_is_absent(tmp_path):
    operation_id = OperationId("operation-without-native-time")
    store = store_with_events(
        tmp_path,
        [
            canonical(
                "operation-start",
                OperationStarted(
                    operation_id,
                    "shell",
                    "shell",
                    "foreground",
                    TextContent("make test"),
                    None,
                    None,
                ),
                occurred_at=None,
            ),
            canonical(
                "operation-finish",
                OperationFinished(operation_id, "succeeded", None, 0),
                occurred_at=None,
            ),
        ],
    )

    operation = store.queries().activity_after(
        SESSION_ID, 0, ActivityScope(), 10
    ).activities[0]

    assert operation.context.started_at == 100.0
    assert operation.context.finished_at == 101.0


def test_actor_assignment_finish_without_start_has_one_source_event(tmp_path):
    event_id = CanonicalEventId("assignment-finish")
    events = [
        canonical(
            str(event_id),
            ActorAssignmentFinished(
                AssignmentId("actor-assignment"),
                "succeeded",
                TextContent("done"),
                None,
            ),
        ),
    ]

    page = store_with_events(tmp_path, events).queries().activity_after(
        SESSION_ID,
        0,
        ActivityScope(),
        10,
    )

    assert page.activities[0].context.source_event_ids == (event_id,)


def test_actor_assignment_start_and_finish_are_distinct_timeline_items(tmp_path):
    assignment_id = AssignmentId("actor-assignment")
    events = [
        canonical(
            "assignment-start",
            ActorAssignmentStarted(assignment_id, TextContent("Get Bali weather")),
            occurred_at=10.0,
        ),
        canonical(
            "assignment-finish",
            ActorAssignmentFinished(
                assignment_id,
                "succeeded",
                TextContent("Sunny"),
                None,
            ),
            occurred_at=12.0,
        ),
    ]

    activities = store_with_events(tmp_path, events).queries().activity_after(
        SESSION_ID, 0, ActivityScope(), 10
    ).activities

    assert [activity.state for activity in activities] == ["running", "finished"]
    assert [activity.context.activity_id for activity in activities] == [
        "actor_assignment-start:assignment-start",
        "actor_assignment-finish:assignment-finish",
    ]
    assert activities[1].brief == TextContent("Get Bali weather")
    assert activities[1].context.started_at == 10.0
    assert activities[1].context.finished_at == 12.0


def test_actor_owned_completion_finishes_assignment_in_parent_timeline(tmp_path):
    actor_id = ActorId("actor-one")
    child_turn_id = TurnId("child-turn")
    assignment_id = AssignmentId(str(child_turn_id))
    events = [
        canonical(
            "assignment-start",
            ActorAssignmentStarted(assignment_id, TextContent("Bali weather")),
            actor_id=actor_id,
            parent_actor_id=LEAD_ACTOR_ID,
            turn_id=child_turn_id,
            occurred_at=10.0,
        ),
        canonical(
            "assignment-finish",
            ActorAssignmentFinished(
                assignment_id,
                "succeeded",
                TextContent("Rain, 24°C"),
                None,
            ),
            actor_id=actor_id,
            parent_actor_id=LEAD_ACTOR_ID,
            turn_id=child_turn_id,
            occurred_at=12.0,
        ),
    ]

    queries = store_with_events(tmp_path, events).queries()
    lead_activities = queries.activity_after(
        SESSION_ID, 0, ActivityScope(actor_id=LEAD_ACTOR_ID), 10
    ).activities
    child_activities = queries.activity_after(
        SESSION_ID, 0, ActivityScope(actor_id=actor_id), 10
    ).activities

    assert [activity.state for activity in lead_activities] == ["running", "finished"]
    assert lead_activities[1].brief == TextContent("Bali weather")
    assert lead_activities[1].context.actor_id == LEAD_ACTOR_ID
    assert lead_activities[1].context.turn_id == child_turn_id
    assert child_activities == ()


def test_dedicated_actor_assignment_fact_replaces_its_generic_operation_item(tmp_path):
    operation_id = OperationId("task-operation")
    assignment_id = AssignmentId("actor-assignment")
    events = [
        canonical(
            "operation-start",
            OperationStarted(
                operation_id,
                "task",
                "native-task-tool",
                "foreground",
                TextContent("inspect the code"),
                None,
                None,
            ),
        ),
        canonical(
            "assignment-start",
            ActorAssignmentStarted(assignment_id, TextContent("inspect the code")),
        ),
    ]

    activities = store_with_events(tmp_path, events).queries().activity_after(
        SESSION_ID, 0, ActivityScope(), 10
    ).activities

    assert len(activities) == 1
    assert isinstance(activities[0], ActorAssignmentActivity)


def test_file_operation_is_presented_once_by_its_file_fact(tmp_path):
    operation_id = OperationId("file-operation")
    events = [
        canonical(
            "operation-start",
            OperationStarted(
                operation_id,
                "file_read",
                "read",
                "foreground",
                TextContent("/work/example.py"),
                None,
                None,
            ),
        ),
        canonical(
            "file-access",
            FileAccessed(operation_id, "/work/example.py", "read"),
        ),
        canonical(
            "operation-progress",
            OperationProgressed(
                operation_id,
                0,
                "output",
                TextContent("file contents"),
                "replace",
            ),
        ),
        canonical(
            "operation-finish",
            OperationFinished(operation_id, "succeeded", None, None),
        ),
    ]

    page = store_with_events(tmp_path, events).queries().activity_after(
        SESSION_ID,
        0,
        ActivityScope(),
        10,
    )

    assert len(page.activities) == 1
    assert page.activities[0].file == FileAccessed(
        operation_id,
        "/work/example.py",
        "read",
    )
    assert page.activities[0].progress[0].content == TextContent("file contents")
    assert page.activities[0].content_event_id == CanonicalEventId("operation-progress")
    assert page.activities[0].content_field == "content"

    after_file = store_with_events(tmp_path, events).queries().activity_after(
        SESSION_ID,
        3,
        ActivityScope(),
        10,
    )
    assert after_file.activities[0].outcome == "succeeded"
    assert after_file.cursor == 4


def test_live_activity_pages_advance_by_revision_without_skipping_items(tmp_path):
    operation_id = OperationId("operation-one")
    events = [
        canonical(
            "operation-start",
            OperationStarted(operation_id, "shell", "shell", "foreground", None, None, None),
        ),
        canonical(
            "message",
            MessageCreated(MessageId("message-one"), "assistant", TextContent("working"), "intermediate", None),
        ),
        canonical(
            "operation-finish",
            OperationFinished(operation_id, "succeeded", TextContent("done"), 0),
        ),
    ]
    queries = store_with_events(tmp_path, events).queries()

    first = queries.activity_after(SESSION_ID, 0, ActivityScope(), 1)
    second = queries.activity_after(SESSION_ID, first.cursor, ActivityScope(), 1)

    assert [activity.context.activity_id for activity in first.activities] == [
        "message:actor-lead:message-one"
    ]
    assert [activity.context.activity_id for activity in second.activities] == [
        "operation:actor-lead:operation-one"
    ]
    assert (first.cursor, second.cursor) == (2, 3)


def test_activity_updates_do_not_move_their_backlog_position(tmp_path):
    operation_id = OperationId("operation-one")
    events = [
        canonical(
            "operation-start",
            OperationStarted(operation_id, "shell", "shell", "foreground", None, None, None),
        ),
        canonical(
            "message",
            MessageCreated(MessageId("message-one"), "assistant", TextContent("working"), "intermediate", None),
        ),
        canonical(
            "operation-finish",
            OperationFinished(operation_id, "succeeded", TextContent("done"), 0),
        ),
    ]
    queries = store_with_events(tmp_path, events).queries()

    older = queries.activity_before(SESSION_ID, 2, ActivityScope(), 10)

    assert [activity.context.activity_id for activity in older.activities] == [
        "operation:actor-lead:operation-one"
    ]
    assert older.oldest_cursor == 1


def test_native_subject_ids_are_scoped_by_actor_in_activity_identity(tmp_path):
    child_actor_id = ActorId("actor-child")
    operation_id = OperationId("call-one")
    events = [
        canonical(
            "lead-operation",
            OperationStarted(operation_id, "shell", "shell", "foreground", None, None, None),
        ),
        canonical(
            "child-operation",
            OperationStarted(operation_id, "shell", "shell", "foreground", None, None, None),
            actor_id=child_actor_id,
        ),
    ]

    page = store_with_events(tmp_path, events).queries().activity_after(
        SESSION_ID,
        0,
        ActivityScope(),
        10,
    )

    assert [activity.context.activity_id for activity in page.activities] == [
        "operation:actor-lead:call-one",
        "operation:actor-child:call-one",
    ]


def test_actor_remains_running_until_its_finish_fact(tmp_path):
    actor_id = ActorId("actor-worker")
    events = [
        canonical(
            "actor-start",
            ActorStarted("Get weather in Bali", "child"),
            actor_id=actor_id,
        ),
        canonical(
            "actor-search",
            OperationStarted(
                OperationId("search-one"),
                "search",
                "WebSearch",
                "foreground",
                TextContent("weather in Bali"),
                None,
                None,
            ),
            actor_id=actor_id,
        ),
        canonical(
            "actor-finish",
            ActorFinished(None),
            actor_id=actor_id,
        ),
    ]
    queries = store_with_events(tmp_path, events).queries()

    assert queries.actors(SESSION_ID, through_cursor=2)[0].state == "running"
    assert queries.actors(SESSION_ID, through_cursor=3)[0].state == "finished"


def test_focused_state_projections_are_exhaustive_and_actor_scoped(tmp_path):
    child_actor_id = ActorId("actor-child")
    attention_id = AttentionId("attention-one")
    events = [
        canonical("session-start", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        canonical(
            "actor-start",
            ActorStarted("Worker", "child"),
            actor_id=child_actor_id,
        ),
        canonical(
            "usage-one",
            UsageReported(
                "session",
                str(SESSION_ID),
                ModelReference("model-one", None, None),
                None,
                TokenUsage(input_tokens=10),
                True,
                Decimal("0.01"),
            ),
        ),
        canonical(
            "usage-two",
            UsageReported(
                "session",
                str(SESSION_ID),
                ModelReference("model-one", None, None),
                None,
                TokenUsage(input_tokens=20),
                True,
                Decimal("0.02"),
            ),
        ),
        canonical("context", ContextReported(50, 100, ModelReference("model-one", None, None))),
        canonical(
            "attention-open",
            AttentionRequested(
                attention_id,
                "question",
                (AttentionPrompt("answer", None, "Continue?", False, ()),),
                None,
            ),
        ),
        canonical(
            "attention-close",
            AttentionResolved(
                attention_id,
                "answered",
                (),
                "yes",
                False,
                "succeeded",
            ),
        ),
        canonical("task", TaskChanged(TaskId("task-one"), "1", "Test", None, "in_progress", child_actor_id)),
        canonical("goal", GoalChanged("Ship it", "active", None)),
        canonical("child-goal", GoalChanged("Child objective", "active", None), actor_id=child_actor_id),
        canonical("actor-finish", ActorFinished(None), actor_id=child_actor_id),
    ]
    queries = store_with_events(tmp_path, events).queries()
    usage = queries.usage(SESSION_ID)
    assert usage.tokens.input_tokens == 20
    assert usage.cost_in_usd == Decimal("0.02")
    assert queries.context(SESSION_ID).by_actor[LEAD_ACTOR_ID].used_tokens == 50
    assert queries.attention(SESSION_ID).pending == ()
    assert queries.tasks(SESSION_ID)[0].owner_actor_id == child_actor_id
    assert queries.goal(SESSION_ID).objective == "Ship it"
    assert queries.actors(SESSION_ID)[0].state == "finished"


def queries_over(store):
    return store.queries()


def test_task_list_membership_removes_tasks_without_translator_memory(tmp_path):
    queries = queries_over(store_with_events(tmp_path, [
        canonical("list-one", TaskListChanged("lead", (TaskId("plan:1"), TaskId("plan:2")))),
        canonical("task-one", TaskChanged(TaskId("plan:1"), "1", "Inspect", None, "completed", None)),
        canonical("task-two", TaskChanged(TaskId("plan:2"), "2", "Implement", None, "pending", None)),
        canonical("worker-list", TaskListChanged("worker", (TaskId("worker:plan:1"),))),
        canonical(
            "worker-task",
            TaskChanged(TaskId("worker:plan:1"), "1", "Review", None, "pending", ActorId("worker")),
        ),
        canonical("list-two", TaskListChanged("lead", (TaskId("plan:1"),))),
    ]))

    assert [task.task_id for task in queries.tasks(SESSION_ID)] == [
        TaskId("plan:1"),
        TaskId("worker:plan:1"),
    ]


def test_context_projection_tracks_compaction_by_actor(tmp_path):
    child_actor_id = ActorId("actor-child")
    events = [
        canonical("compact-lead", CompactionStarted(100)),
        canonical("compact-child", CompactionStarted(50), actor_id=child_actor_id),
        canonical("compact-lead-done", CompactionFinished(100, 20)),
    ]

    context = store_with_events(tmp_path, events).queries().context(SESSION_ID)

    assert context.compacting_actor_ids == (child_actor_id,)


def test_context_projection_uses_the_actors_latest_model(tmp_path):
    model = ModelReference("native-model", "Model", "model-option")
    events = [
        canonical("model", ModelChanged(None, model, "reported_by_harness")),
        canonical("context", ContextReported(50, 100, None)),
    ]

    context = store_with_events(tmp_path, events).queries().context(SESSION_ID)

    assert context.by_actor[LEAD_ACTOR_ID].model == model


def test_active_time_is_rebuilt_from_turn_boundaries(tmp_path):
    events = [
        canonical(
            "session-start",
            SessionStarted("/work", "fixture.jsonl", None, None, None, None, None),
            occurred_at=10.0,
        ),
        canonical("turn-finished", TurnFinished(None, "succeeded"), occurred_at=20.0),
        canonical(
            "next-prompt",
            MessageCreated(MessageId("next"), "user", TextContent("continue"), "prompt", None),
            occurred_at=30.0,
        ),
    ]

    active_seconds = store_with_events(tmp_path, events).queries().active_seconds(
        SESSION_ID,
        current_time=35.0,
    )

    assert active_seconds == 15.0


def test_tab_state_is_a_canonical_fold_over_semantic_lifecycle(tmp_path):
    operation_id = OperationId("background-one")
    attention_id = AttentionId("attention-one")
    events = [
        canonical("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        canonical("turn", TurnStarted(None)),
        canonical(
            "operation",
            OperationStarted(operation_id, "shell", "shell", "background", None, None, None),
        ),
        canonical("waiting", TurnFinished(None, "succeeded")),
        canonical(
            "attention",
            AttentionRequested(attention_id, "question", (), None),
        ),
        canonical(
            "answered",
            AttentionResolved(attention_id, "answered", (), None, False, "succeeded"),
        ),
        canonical("operation-finished", OperationFinished(operation_id, "succeeded", None, 0)),
        canonical("finished", TurnFinished(None, "succeeded")),
        canonical("session-finished", SessionFinished("succeeded", None)),
    ]
    queries = store_with_events(tmp_path, events).queries()

    assert queries.tab_state(SESSION_ID, 1) == "idle"
    assert queries.tab_state(SESSION_ID, 2) == "thinking"
    assert queries.tab_state(SESSION_ID, 3) == "executing"
    assert queries.tab_state(SESSION_ID, 4) == "awaiting_background"
    assert queries.tab_state(SESSION_ID, 5) == "awaiting_attention"
    assert queries.tab_state(SESSION_ID, 6) == "working"
    assert queries.tab_state(SESSION_ID, 8) == "awaiting_response"
    assert queries.tab_state(SESSION_ID, 9) is None
