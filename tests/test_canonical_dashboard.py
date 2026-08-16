"""Dashboard page/content/SSE contracts over canonical projections."""

from __future__ import annotations

import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from harness.models import RawEvent, Session, TerminalSessionState, TranslationResult
from engine.queries.content import CanonicalContentService
from core.repository import RepositoryQueries
from dashboard.activity import (
    DashboardActivityService,
    DashboardSessionListItem,
    DashboardSessionService,
    DashboardStreamService,
)
from dashboard import prefs
from dashboard.application import DashboardNotificationState
from dashboard.notify.notifier import Notifier
from domain.events import (
    ActorStarted,
    AttentionRequested,
    AttentionResolved,
    CanonicalEvent,
    CompactionFinished,
    FileAccessed,
    MessageCreated,
    OperationFinished,
    OperationProgressed,
    OperationStarted,
    SessionStarted,
    TaskChanged,
)
from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    MessageId,
    OperationId,
    RawEventId,
    SessionId,
    TaskId,
)
from domain.values import AttentionAnswer, AttentionPrompt, StructuredContent, TextContent, TokenUsage
from canonical_runtime import CanonicalRuntime
from engine.projections import (
    ActivityScope,
    ActivityStatistics,
    ContextSummary,
    SessionSummary,
    UsageSummary,
)


def test_preferences_fail_clearly_instead_of_returning_fallback_state(monkeypatch):
    def unavailable_database():
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(prefs, "_connect", unavailable_database)

    with pytest.raises(sqlite3.OperationalError, match="database unavailable"):
        prefs.get("missing", {})
    with pytest.raises(sqlite3.OperationalError, match="database unavailable"):
        prefs.set("example", {})
    with pytest.raises(sqlite3.OperationalError, match="database unavailable"):
        prefs.mutate_map("example", lambda document: document.update(saved=True))


def test_preferences_reject_invalid_current_schema_values():
    prefs.set(prefs.HIDDEN_KEY, [])

    with pytest.raises(TypeError, match="must contain an object"):
        prefs.hidden_dirs()

SESSION_ID = SessionId("session-one")
LEAD_ACTOR_ID = ActorId("actor-lead")


class NoTerminal:
    def state(self, session_id):
        del session_id
        return TerminalSessionState(None, None)


def event(event_id, payload, *, actor_id=LEAD_ACTOR_ID):
    return CanonicalEvent(
        CanonicalEventId(event_id),
        SESSION_ID,
        actor_id,
        None,
        None,
        "example",
        10.0,
        None,
        None,
        payload,
    )


def services(tmp_path, events):
    store = CanonicalRuntime(str(tmp_path / "events.db"))
    store.register(
        "example",
        Session(SESSION_ID, LEAD_ACTOR_ID, "native", "fixture", "/work"),
    )
    for index, canonical_event in enumerate(events):
        raw = RawEvent(
            RawEventId(f"raw-{index}"),
            "example",
            "fixture",
            "fixture",
            str(index),
            SESSION_ID,
            canonical_event.actor_id,
            canonical_event.parent_actor_id,
            100.0 + index,
            "json",
            f'{{"index":{index}}}'.encode(),
        )
        store.record(raw, "1", TranslationResult((canonical_event,), "translated"))
    queries = store.queries()
    return (
        store,
        DashboardActivityService(store, queries),
        DashboardStreamService(store, queries, NoTerminal(), RepositoryQueries()),
        CanonicalContentService(store, queries),
    )


def test_backlog_and_live_use_the_same_stable_item_identity(tmp_path):
    operation_id = OperationId("operation-one")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "start",
            OperationStarted(
                operation_id,
                "shell",
                "shell",
                "foreground",
                StructuredContent('{"command":"printf hello"}'),
                None,
                None,
            ),
        ),
        event("finish", OperationFinished(operation_id, "succeeded", TextContent("passed"), 0)),
    ]
    _store, activity, stream, _content = services(tmp_path, events)
    backlog = activity.backlog(SESSION_ID, None, ActivityScope(), 10)
    frame = stream.frame(SESSION_ID, 1, ActivityScope())
    assert frame is not None
    assert backlog.items[0].item_id == frame.items[0].item_id == "operation:actor-lead:operation-one"
    assert frame.items[0].state == "succeeded"


def test_backlog_cursor_cannot_skip_an_event_committed_during_projection(tmp_path, monkeypatch):
    events = [event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None))]
    store, activity, stream, _content = services(tmp_path, events)
    original_latest_cursor = store.latest_cursor

    def capture_then_commit():
        captured = original_latest_cursor()
        new_event = event(
            "message-after-snapshot",
            MessageCreated(MessageId("message-two"), "assistant", TextContent("later"), "final", None),
        )
        raw = RawEvent(
            RawEventId("raw-after-snapshot"),
            "example",
            "fixture",
            "fixture",
            "later",
            SESSION_ID,
            LEAD_ACTOR_ID,
            None,
            200.0,
            "json",
            b"{}",
        )
        store.record(raw, "1", TranslationResult((new_event,), "translated"))
        return captured

    monkeypatch.setattr(store, "latest_cursor", capture_then_commit)

    backlog = activity.backlog(SESSION_ID, None, ActivityScope(), 10)
    frame = stream.frame(SESSION_ID, backlog.latest_cursor, ActivityScope())

    assert backlog.latest_cursor == 1
    assert backlog.items == ()
    assert frame is not None
    assert frame.items[0].plain_text == "later"


def test_stream_frame_never_projects_events_after_its_cursor(tmp_path):
    operation_id = OperationId("operation-one")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "start",
            OperationStarted(
                operation_id,
                "shell",
                "shell",
                "foreground",
                StructuredContent('{"command":"printf hello"}'),
                None,
                None,
            ),
        ),
        event("finish", OperationFinished(operation_id, "succeeded", TextContent("passed"), 0)),
    ]
    _store, _activity, stream, _content = services(tmp_path, events)

    frame = stream.frame(SESSION_ID, 1, ActivityScope(), limit=1)

    assert frame is not None
    assert frame.cursor == 2
    assert frame.items[0].state == "running"


def test_actor_scope_advances_the_cursor_across_invisible_events(tmp_path):
    other_actor = ActorId("other-actor")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "message",
            MessageCreated(MessageId("message-one"), "assistant", TextContent("hidden"), "final", None),
            actor_id=other_actor,
        ),
    ]
    _store, _activity, stream, _content = services(tmp_path, events)
    frame = stream.frame(SESSION_ID, 1, ActivityScope(actor_id=LEAD_ACTOR_ID))
    assert frame is not None
    assert frame.cursor == 2
    assert frame.items == ()
    assert frame.sse().startswith("id: 2\nevent: activity\ndata: ")


def test_one_canonical_frame_contains_all_changed_focused_projections(tmp_path):
    events = [event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None))]
    _store, _activity, stream, _content = services(tmp_path, events)
    frame = stream.frame(SESSION_ID, 0, ActivityScope())
    assert frame is not None
    assert frame.snapshot.session is not None
    assert frame.snapshot.actors == ()
    assert frame.snapshot.cursor == frame.cursor
    assert '"cursor":1' in frame.json()
    assert frame.sse().count("event: activity") == 1


def test_session_snapshot_uses_one_fixed_canonical_cursor(tmp_path):
    events = [event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None))]
    store, _activity, _stream, _content = services(tmp_path, events)
    queries = store.queries()

    snapshot = DashboardSessionService(
        store, queries, NoTerminal(), RepositoryQueries()
    ).snapshot(
        SESSION_ID,
        ActivityScope(),
    )

    assert snapshot.cursor == 1
    assert snapshot.session.session_id == SESSION_ID
    assert snapshot.context.compacting_actor_ids == ()


def test_repository_queries_group_a_linked_worktree_under_its_main_checkout(tmp_path):
    main_checkout = tmp_path / "project"
    git_directory = main_checkout / ".git"
    worktree_directory = tmp_path / "worktree"
    worktree_metadata = git_directory / "worktrees" / "feature"
    worktree_metadata.mkdir(parents=True)
    worktree_directory.mkdir()
    (worktree_directory / ".git").write_text(
        f"gitdir: {worktree_metadata}\n",
        encoding="utf-8",
    )

    assert RepositoryQueries().project_directory(str(worktree_directory)) == str(
        main_checkout
    )


def test_repository_status_is_unavailable_when_git_times_out(monkeypatch):
    def time_out(*arguments, **options):
        raise subprocess.TimeoutExpired("git", 2)

    monkeypatch.setattr(
        "core.repository.subprocess.run",
        time_out,
    )

    assert RepositoryQueries.status("/work") is None


def test_session_snapshot_projects_background_jobs_and_monitors_without_legacy_reads(tmp_path):
    job_id = OperationId("job-one")
    monitor_id = OperationId("monitor-one")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "job-start",
            OperationStarted(
                job_id,
                "shell",
                "Bash",
                "background",
                TextContent("make test"),
                "Run tests",
                None,
            ),
        ),
        event("job-output", OperationProgressed(job_id, 0, "output", TextContent("passed"), "append")),
        event("job-finish", OperationFinished(job_id, "succeeded", None, 0)),
        event(
            "monitor-start",
            OperationStarted(
                monitor_id,
                "network",
                "Monitor",
                "monitor",
                TextContent("https://example.test/events"),
                "Watch events",
                None,
            ),
        ),
        event(
            "monitor-event",
            OperationProgressed(monitor_id, 0, "status", TextContent("connected"), "append"),
        ),
    ]
    store, _activity, _stream, _content = services(tmp_path, events)
    snapshot = DashboardSessionService(
        store, store.queries(), NoTerminal(), RepositoryQueries()
    ).snapshot(
        SESSION_ID,
        ActivityScope(),
    )

    assert snapshot.background_work.background_job_count == 1
    assert snapshot.background_work.monitor_count == 1
    job = snapshot.background_work.jobs[0]
    assert job.task == "job-one"
    assert job.command == "make test"
    assert job.output == "passed"
    assert job.line_count == 1
    assert job.live is False
    monitor = snapshot.background_work.monitors[0]
    assert monitor.live is True
    assert monitor.events[0].event == "connected"


def test_content_reference_resolves_directly_from_the_canonical_event(tmp_path):
    operation_id = OperationId("operation-one")
    large = TextContent("x" * 5000)
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "start",
            OperationStarted(
                operation_id,
                "network",
                "Fetch",
                "foreground",
                None,
                None,
                None,
            ),
        ),
        event("finish", OperationFinished(operation_id, "succeeded", large, 0)),
    ]
    _store, activity, _stream, content = services(tmp_path, events)
    item = activity.backlog(SESSION_ID, None, ActivityScope(), 10).items[0]
    assert item.content_reference == "finish:operation_content"
    assert content.resolve(item.content_reference) == large.text


def test_streaming_operation_content_keeps_its_exact_canonical_reference(tmp_path):
    operation_id = OperationId("operation-one")
    first = TextContent("x" * 3000)
    second = TextContent("y" * 3000)
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "start",
            OperationStarted(
                operation_id,
                "shell",
                "shell",
                "foreground",
                StructuredContent('{"command":"printf hello"}'),
                None,
                None,
            ),
        ),
        event("progress-one", OperationProgressed(operation_id, 0, "output", first, "append")),
        event("progress-two", OperationProgressed(operation_id, 1, "output", second, "append")),
    ]
    _store, activity, _stream, content = services(tmp_path, events)

    item = activity.backlog(SESSION_ID, None, ActivityScope(), 10).items[0]

    assert item.content_reference == "progress-two:operation_content"
    assert content.resolve(item.content_reference) == first.text + "\n" + second.text
    assert item.command_reference == "start:operation_command"
    assert content.resolve(item.command_reference) == "printf hello"
    assert item.output_reference == "progress-two:operation_output"
    assert content.resolve(item.output_reference) == first.text + "\n" + second.text
    with pytest.raises(KeyError):
        content.resolve("progress-two:stream")


def test_file_activity_keeps_the_existing_visible_html_and_canonical_content_reference(tmp_path):
    operation_id = OperationId("file-one")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "operation",
            OperationStarted(operation_id, "file_edit", "edit", "foreground", None, None, None),
        ),
        event(
            "file",
            FileAccessed(
                operation_id,
                "a.py",
                "updated",
                lines_added=2,
                lines_removed=1,
                content=TextContent("new contents"),
            ),
        ),
    ]
    _store, activity, _stream, content = services(tmp_path, events)

    item = activity.backlog(SESSION_ID, None, ActivityScope(), 10).items[0]

    assert item.html == (
        '<pre class="opl"><span style="color:rgb(229,192,123)">Update</span>'
        '<span style="color:rgb(92,99,112)">(</span>'
        '<span style="color:rgb(171,178,191)">a.py</span>'
        '<span style="color:rgb(92,99,112)">)</span>  '
        '<span style="color:rgb(152,195,121)">+2</span> '
        '<span style="color:rgb(224,108,117)">-1</span></pre>'
    )
    assert item.content_reference == "file:content"
    assert content.resolve(item.content_reference) == "new contents"


def test_file_activity_progress_reference_resolves_direct_canonical_content(tmp_path):
    operation_id = OperationId("file-one")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event(
            "operation",
            OperationStarted(operation_id, "file_read", "read", "foreground", None, None, None),
        ),
        event("file", FileAccessed(operation_id, "a.py", "read")),
        event(
            "progress",
            OperationProgressed(operation_id, 0, "output", TextContent("file contents"), "replace"),
        ),
    ]
    _store, activity, _stream, content = services(tmp_path, events)

    item = activity.backlog(SESSION_ID, None, ActivityScope(), 10).items[0]

    assert item.content_reference == "progress:content"
    assert content.resolve(item.content_reference) == "file contents"


def test_visible_attention_task_and_compaction_facts_are_dashboard_items(tmp_path):
    attention_id = AttentionId("question-one")
    events = [
        event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        event("actor", ActorStarted("claude", "lead")),
        event(
            "question",
            AttentionRequested(
                attention_id,
                "question",
                (AttentionPrompt("language", "Language", "Choose?", True, ()),),
                None,
            ),
        ),
        event(
            "answer",
            AttentionResolved(
                attention_id,
                "answered",
                (AttentionAnswer("language", ("Python", "JavaScript")),),
                None,
                False,
                "succeeded",
            ),
        ),
        event("task", TaskChanged(TaskId("4"), "4", "ship it", None, "completed", None)),
        event("compaction", CompactionFinished(100, 20)),
    ]
    _store, activity, _stream, _content = services(tmp_path, events)

    items = activity.backlog(SESSION_ID, None, ActivityScope(), 10).items

    assert [item.item_type for item in items] == [
        "attention",
        "attention",
        "task",
        "compaction",
    ]
    assert "claude ▸ asks you" in items[0].html
    assert 'class="ansv">Python</span>' in items[1].html
    assert items[2].plain_text == "task #4 · ship it"
    assert items[3].plain_text == "compacted"


class MutableNotificationQueries:
    def __init__(self, state):
        self.state = state

    def tab_state(self, session_id):
        del session_id
        return self.state


class StaticNotificationSessions:
    def __init__(self, terminal_state):
        summary = SessionSummary(
            session_id=SESSION_ID,
            harness="example",
            title="Refactor",
            working_directory="/work",
            initial_working_directory="/work",
            started_at=10.0,
            finished_at=None,
            lead_actor_id=LEAD_ACTOR_ID,
            model=None,
            effort=None,
            account=None,
            prompt_count=1,
            automatic_model_change=None,
            state="running",
        )
        self.item = DashboardSessionListItem(
            session=summary,
            terminal=terminal_state,
            project_directory="/work",
            tab_state="idle",
            statistics=ActivityStatistics(0, 0, 0, 0, 0, 0, {}),
            usage=UsageSummary(TokenUsage(), None, {}, {}),
            context=ContextSummary({}, ()),
            repository=None,
        )

    def sessions(self):
        return (self.item,)


def test_notifier_uses_canonical_tab_transitions(monkeypatch):
    queries = MutableNotificationQueries("idle")
    notification_state = DashboardNotificationState()
    application = SimpleNamespace(
        queries=queries,
        dashboard_sessions=StaticNotificationSessions(
            TerminalSessionState("window-one", None)
        ),
        dashboard_notification_state=notification_state,
    )
    notifier = Notifier(application)
    retractions = []
    monkeypatch.setattr("dashboard.notify.notifier.prefs.notify_enabled", lambda: True)
    monkeypatch.setattr("dashboard.notify.notifier.prefs.notify_muted", lambda session_id: False)
    monkeypatch.setattr("dashboard.notify.notifier.config.NOTIFICATION_DELAY_SECONDS", 0)
    monkeypatch.setattr("dashboard.notify.notifier.config.NOTIFICATION_SETTLE_SECONDS", 0)
    monkeypatch.setattr("dashboard.notify.notifier.config.NOTIFY_WEBPUSH", False)
    monkeypatch.setattr("dashboard.notify.notifier.config.NOTIFY_TELEGRAM", True)
    monkeypatch.setattr("dashboard.notify.notifier.presence.web_viewing", lambda session_id: False)
    monkeypatch.setattr("dashboard.notify.notifier.presence.device_active", lambda: False)
    monkeypatch.setattr("dashboard.notify.notifier.presence.route", lambda: ("terminal", (), {}))
    monkeypatch.setattr(
        "dashboard.notify.notifier.channels.send_telegram",
        lambda payload, reason: {"payload": payload, "reason": reason},
    )
    monkeypatch.setattr(
        "dashboard.notify.notifier.channels.retract",
        lambda handle, reason: retractions.append((handle, reason)) or "retracted",
    )
    monkeypatch.setattr(
        "dashboard.notify.notifier.AUDIT.state_file",
        lambda *arguments, **keywords: None,
    )

    notifier.scan()
    queries.state = "awaiting_attention"
    notifier.scan()

    notice = notification_state.notification()
    assert notice is not None
    assert notice.session_id == str(SESSION_ID)
    assert notice.kind == "asking"
    assert SESSION_ID in notifier.delivered

    queries.state = "idle"
    notifier.scan()

    assert SESSION_ID not in notifier.delivered
    assert retractions[0][1] == "state-changed"


def test_notifier_ignores_sessions_without_a_terminal_window(monkeypatch):
    queries = MutableNotificationQueries("idle")
    notification_state = DashboardNotificationState()
    application = SimpleNamespace(
        queries=queries,
        dashboard_sessions=StaticNotificationSessions(TerminalSessionState(None, None)),
        dashboard_notification_state=notification_state,
    )
    notifier = Notifier(application)
    monkeypatch.setattr("dashboard.notify.notifier.prefs.notify_enabled", lambda: True)

    notifier.scan()
    queries.state = "awaiting_attention"
    notifier.scan()

    assert notification_state.notification() is None
