"""Cross-harness canonical translation tests from native fixture shapes."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bootstrap import build_application
from app import terminal_panes
from app import pending_session
from app.plugins import installed_plugins
from app.session_terminal import ApplicationTerminal
from contracts.harness import (
    AnswerQuestion,
    AttachmentReference,
    ControlContext,
    ControlResult,
    FileWatch,
    LaunchRequest,
    MigrateAccount,
    QueryContext,
    RawEvent,
    RenameSession,
    SelectModel,
    Session,
    TranslationError,
)
from contracts.terminal import (
    ACTIVITY_PANE_TAG,
    SCOREBOARD_PANE_TAG,
    SESSION_WINDOW_TAG,
    ScreenText,
    SessionPaneRequest,
    TabResult,
    TerminalResult,
)
from domain.codec import CanonicalEventCodec
from domain.events import CanonicalEvent
from domain.events import (
    ActorFinished,
    ActorMessageSent,
    ActorNameChanged,
    ActorStarted,
    AttentionRequested,
    AttentionResolved,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    FileAccessed,
    GoalChanged,
    MessageCreated,
    EffortChanged,
    ModelChanged,
    OperationFinished,
    OperationInputProvided,
    OperationProgressed,
    OperationStarted,
    ReasoningCreated,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    TaskChanged,
    TaskListChanged,
    TurnStarted,
    UsageReported,
)
from domain.ids import ActorId, AssignmentId, CanonicalEventId, OperationId, RawEventId, SessionId, TaskId, TurnId
from domain.values import AccountReference, AttentionPrompt, ModelReference, TextContent
from plugins.claude_code.canonical import (
    ClaudeCanonicalTranslator,
    ClaudeRawEventSources,
    ClaudeTaskRawEventSource,
    ClaudeTranscriptRawEventSource,
)
from plugins.claude_code import canonical_hook as claude_canonical_hook
from plugins.claude_code import foreground as claude_foreground
from plugins.claude_code import memory_state as claude_memory_state
from plugins.claude_code import statusline as claude_statusline
from plugins.claude_code import usage_state as claude_usage_state
from plugins.claude_code.usage_rows import usage_reader as claude_usage_reader
from plugins.claude_code.reactor import ClaudeReactor
from plugins.claude_code.otel import receiver as claude_otel_receiver
from plugins.codex.canonical import (
    CodexCanonicalTranslator,
    CodexProcessRawEventSource,
    CodexRawEventSources,
    CodexRolloutRawEventSource,
    process_event,
)
from plugins.codex import command as codex_command
from plugins.codex import canonical_hook as codex_canonical_hook
from plugins.codex import rollout as codex_rollout
from plugins.codex.controller import _rollout_abort_state
from runtime.recorder import EventIdentityConflict
from canonical_runtime import CanonicalRuntime
from runtime.evidence import EvidenceQueries
from app.interpreter import Interpreter
from app.services import HarnessLauncherService
from runtime.harnesses import HarnessRegistry


def raw_event(
    document,
    *,
    harness: str,
    source_type: str,
    raw_event_id: str,
    source_position: str = "10",
    observed_at: float = 100.0,
) -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(raw_event_id),
        harness=harness,
        source_type=source_type,
        source_name="fixture.jsonl",
        source_position=source_position,
        session_id=SessionId("session-one"),
        actor_id=ActorId("session-one:lead"),
        parent_actor_id=None,
        observed_at=observed_at,
        encoding="jsonl" if source_type != "hook" else "json",
        payload=json.dumps(document).encode(),
    )


def payloads(translation, payload_type):
    return [event for event in translation.canonical_events if isinstance(event.payload, payload_type)]


class RecordingControls:
    def __init__(self):
        self.executed = []

    def execute(self, request):
        self.executed.append(request)
        return ControlResult(request.request_id, "acknowledged")


def test_claude_reactor_migrates_the_account_on_a_committed_rate_limit(monkeypatch):
    monkeypatch.setattr("plugins.claude_code.reactor.otel.start", lambda: None)
    controls = RecordingControls()
    rate_limited = raw_event(
        {"hook_event_name": "StopFailure", "error": "rate_limit"},
        harness="claude_code",
        source_type="hook",
        raw_event_id="stop-failure",
    )
    unrelated_failure = raw_event(
        {"hook_event_name": "StopFailure", "error": "network"},
        harness="claude_code",
        source_type="hook",
        raw_event_id="stop-network",
    )
    child_failure = raw_event(
        {"hook_event_name": "StopFailure", "error": "rate_limit", "agent_id": "child-one"},
        harness="claude_code",
        source_type="hook",
        raw_event_id="stop-child",
    )

    reactor = ClaudeReactor()
    reactor.react(rate_limited, controls)
    reactor.react(unrelated_failure, controls)
    reactor.react(child_failure, controls)

    assert len(controls.executed) == 1
    request = controls.executed[0]
    assert isinstance(request, MigrateAccount)
    assert request.session_id == SessionId("session-one")


def test_claude_reactor_starts_telemetry_on_session_start(monkeypatch):
    telemetry_starts = []
    monkeypatch.setattr(
        "plugins.claude_code.reactor.otel.start",
        lambda: telemetry_starts.append("started"),
    )
    session_start = raw_event(
        {"hook_event_name": "SessionStart", "cwd": "/work"},
        harness="claude_code",
        source_type="hook",
        raw_event_id="session-start",
    )
    pulled = raw_event(
        {"type": "user"},
        harness="claude_code",
        source_type="transcript",
        raw_event_id="pulled-record",
    )

    reactor = ClaudeReactor()
    reactor.react(session_start, RecordingControls())
    reactor.react(pulled, RecordingControls())

    assert telemetry_starts == ["started"]


def test_plugin_folder_descriptors_are_discovered_without_harness_branches():
    assert [plugin.info.name for plugin in installed_plugins()] == ["claude_code", "codex"]


class InterpreterTerminal:
    def close_session_panes(self, session_id):
        return None

    def hosting_session(self, excluding_session_id):
        return SessionId("already-hosting")

    def current_window(self):
        return None

    def window_for_session(self, session_id):
        return None


def interpreting_runtime(database_path):
    """The real installed plugins wired to one database, with a silent terminal."""
    harnesses = HarnessRegistry()
    for plugin in installed_plugins():
        harnesses.register(plugin)
    harnesses.validate()
    runtime = CanonicalRuntime(str(database_path), harnesses=harnesses)
    controls = RecordingControls()
    interpreter = Interpreter(
        runtime.sessions,
        harnesses,
        runtime.recorder,
        runtime.watches,
        runtime.store,
        controls,
        InterpreterTerminal(),
    )
    return runtime, interpreter


def test_file_sources_preserve_the_exact_complete_line(tmp_path):
    source_path = tmp_path / "source.jsonl"
    exact_line = b'{"type":"example"}\n'
    source_path.write_bytes(exact_line)
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native",
        str(source_path),
        "/work",
    )

    for source_type in (ClaudeTranscriptRawEventSource, CodexRolloutRawEventSource):
        source = source_type(session.source_context)
        raw_events = source.read(None)

        assert raw_events[0].payload == exact_line
        assert raw_events[0].source_identity == source.source_identity
        # the position names the last consumed record; resuming after it is empty
        assert source.read(raw_events[-1].source_position) == ()


@pytest.mark.parametrize(
    "source_type", [ClaudeTranscriptRawEventSource, CodexRolloutRawEventSource]
)
def test_file_sources_read_bounded_batches_and_resume_by_position(tmp_path, source_type):
    source_path = tmp_path / "source.jsonl"
    line = b'{"type":"example"}\n'
    source_path.write_bytes(line * 101)
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native",
        str(source_path),
        "/work",
    )
    source = source_type(session.source_context)

    first_batch = source.read(None)
    assert len(first_batch) == 100

    second_batch = source.read(first_batch[-1].source_position)
    assert len(second_batch) == 1
    assert source.read(second_batch[-1].source_position) == ()


@pytest.mark.parametrize(
    ("source_actor_id", "source_parent_actor_id", "sender", "expected_parent_actor_id", "starts_actor"),
    [
        ("session-one:lead", None, "worker-one", ActorId("session-one:lead"), True),
        ("worker-one", ActorId("session-one:lead"), "session-one:lead", None, False),
    ],
)
def test_claude_team_messages_preserve_the_native_sender_as_evidence_actor(
    tmp_path,
    source_actor_id,
    source_parent_actor_id,
    sender,
    expected_parent_actor_id,
    starts_actor,
):
    source_path = tmp_path / "transcript.jsonl"
    session_record = json.dumps({
        "type": "user",
        "uuid": "session-prompt",
        "message": {"content": "begin"},
    })
    team_record = json.dumps({
        "type": "user",
        "uuid": "team-message-one",
        "message": {
            "content": f'<teammate-message teammate_id="{sender}">hello</teammate-message>',
        },
    })
    source_path.write_text(session_record + "\n" + team_record + "\n")
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native",
        str(source_path),
        "/work",
    )
    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "events.db")
    runtime.register("claude_code", session)
    context = replace(
        session.source_context,
        actor_id=ActorId(source_actor_id),
        parent_actor_id=source_parent_actor_id,
    )

    runtime.recorder.record(ClaudeTranscriptRawEventSource(context).read(None))
    interpreter.tick()

    evidence = EvidenceQueries(runtime.store).session(session.session_id)[-1]
    assert evidence.actor_id == ActorId(sender)
    assert evidence.parent_actor_id == expected_parent_actor_id
    assert all(item.event.actor_id == ActorId(sender) for item in evidence.canonical)
    assert any(isinstance(item.event.payload, ActorStarted) for item in evidence.canonical) is starts_actor
    message = next(
        item.event.payload
        for item in evidence.canonical
        if isinstance(item.event.payload, MessageCreated)
    )
    assert message.role == "peer"


def test_codex_process_source_emits_one_auditable_session_finish(monkeypatch):
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native",
        "/work/rollout.jsonl",
        "/work",
        native_process_id=4242,
    )
    monkeypatch.setattr("plugins.codex.canonical._codex_process_is_running", lambda process_id: False)

    source = CodexProcessRawEventSource(session)
    raw_events = source.read(None)
    # the wrapper's own `started` observation shares the identity; it is not a latch
    also_after_start = source.read("started")

    assert len(raw_events) == 1
    raw_liveness = raw_events[0]
    assert raw_liveness.source_type == "process"
    assert raw_liveness.source_identity == source.source_identity
    assert json.loads(raw_liveness.payload) == {
        "process_id": 4242,
        "rollout_path": "/work/rollout.jsonl",
        "state": "finished",
    }
    assert [event.raw_event_id for event in also_after_start] == [
        event.raw_event_id for event in raw_events
    ]
    translation = CodexCanonicalTranslator().translate(raw_liveness)
    assert isinstance(translation.canonical_events[0].payload, SessionFinished)
    assert translation.canonical_events[0].payload.reason == "process_exited"
    # the recorded finish is the latch
    assert raw_liveness.source_position == "finished"
    assert source.read("finished") == ()


def test_codex_process_boundaries_are_distinct_for_each_resume():
    first = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "session-one",
        "/work/rollout.jsonl",
        "/work",
        native_process_id=4242,
    )
    resumed = replace(first, native_process_id=4343)
    translator = CodexCanonicalTranslator()

    first_start = translator.translate(process_event(first, "started")).canonical_events[0]
    first_finish = translator.translate(process_event(first, "finished")).canonical_events[0]
    resumed_start = translator.translate(process_event(resumed, "started")).canonical_events[0]
    resumed_finish = translator.translate(process_event(resumed, "finished")).canonical_events[0]

    assert isinstance(first_start.payload, SessionStarted)
    assert isinstance(first_finish.payload, SessionFinished)
    assert len({
        first_start.event_id,
        first_finish.event_id,
        resumed_start.event_id,
        resumed_finish.event_id,
    }) == 4


def test_codex_command_identifies_the_rollout_opened_by_the_native_process(
    monkeypatch,
    tmp_path,
):
    rollout_path = (
        tmp_path
        / ".codex"
        / "sessions"
        / "2026"
        / "08"
        / "14"
        / "rollout-2026-08-14T14-00-00-session-one.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {"id": "session-one", "cwd": "/work", "thread_source": "user"},
    }) + "\n")
    monkeypatch.setattr(
        codex_command.subprocess,
        "run",
        lambda *arguments, **options: SimpleNamespace(
            stdout=f"p4242\nfcwd\nn{rollout_path}\n",
            returncode=0,
        ),
    )

    session = codex_command.process_rollout(4242)

    assert session is not None
    assert session.session_id == SessionId("session-one")
    assert session.source_reference == str(rollout_path)


def test_codex_command_selects_the_exact_native_child_process(monkeypatch):
    def run(arguments, **_options):
        if arguments[0] == "pgrep":
            return SimpleNamespace(stdout="4242\n", returncode=0)
        return SimpleNamespace(
            stdout="4100 node\n4242 /native/path/codex\n",
            returncode=0,
        )

    monkeypatch.setattr(codex_command.subprocess, "run", run)

    assert codex_command.native_codex_process(4100) == 4242


def test_codex_command_opens_pending_panes_then_adopts_the_native_session(monkeypatch):
    calls = []
    recognized = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "session-one",
        "/work/rollout.jsonl",
        "/work",
    )

    class Process:
        pid = 4100

        def poll(self):
            return None

        def wait(self):
            calls.append(("wait",))
            return 0

    class Terminal:
        def current_window(self):
            return "window-one"

        def open_pending_session_panes(self, request):
            calls.append(("open", request.session_id, request.anchor_window_id))
            return TerminalResult(True)

        def adopt_pending_session_panes(self, pending_id, session_id):
            calls.append(("adopt", pending_id, session_id))
            return TerminalResult(True)

    context = SimpleNamespace(terminal=Terminal())
    monkeypatch.setattr(codex_command, "LaunchContext", lambda: context)
    monkeypatch.setattr(codex_command.shutil, "which", lambda executable: "/bin/codex")
    monkeypatch.setattr(codex_command.subprocess, "Popen", lambda arguments: Process())
    monkeypatch.setattr(codex_command, "native_codex_process", lambda process_id: 4242)
    monkeypatch.setattr(codex_command, "process_rollout", lambda process_id: recognized)
    monkeypatch.setattr(codex_command.pane_preferences, "width_percent", lambda directory: 25)
    monkeypatch.setattr(
        codex_command,
        "record_process",
        lambda _context, session, state: calls.append(
            ("record", session.session_id, session.native_process_id, state)
        ),
    )
    monkeypatch.setattr(
        codex_command.pending_session,
        "clear",
        lambda pending_id: calls.append(("clear", pending_id)),
    )

    assert codex_command.run([]) == 0
    assert calls == [
        ("open", SessionId("pending-4242"), "window-one"),
        ("record", SessionId("session-one"), 4242, "started"),
        ("adopt", SessionId("pending-4242"), SessionId("session-one")),
        ("wait",),
        ("record", SessionId("session-one"), 4242, "finished"),
        ("clear", SessionId("pending-4242")),
    ]


def test_claude_source_factory_includes_child_transcripts(tmp_path):
    parent_path = tmp_path / "projects" / "workspace" / "session-one.jsonl"
    child_path = tmp_path / "projects" / "workspace" / "session-one" / "subagents" / "agent-child-one.jsonl"
    child_path.parent.mkdir(parents=True)
    parent_path.write_text('{"type":"user","uuid":"parent"}\n')
    child_path.write_text('{"type":"user","uuid":"child"}\n')
    session = Session(
        session_id=SessionId("session-one"),
        lead_actor_id=ActorId("session-one:lead"),
        native_session_id="session-one",
        source_reference=str(parent_path),
        working_directory=str(tmp_path),
    )

    sources = ClaudeRawEventSources().for_session(session)

    assert len(sources) == 3
    assert isinstance(sources[1], ClaudeTaskRawEventSource)
    assert sources[2].context.actor_id == ActorId("child-one")
    assert sources[2].context.parent_actor_id == ActorId("session-one:lead")


def test_claude_task_source_captures_full_updates_and_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    task_directory = tmp_path / "tasks" / "session-6165ab88"
    task_directory.mkdir(parents=True)
    task_path = task_directory / "1.json"
    task = {
        "id": "1",
        "subject": "Run tests",
        "description": "Run the focused suite",
        "activeForm": "Running tests",
        "owner": "worker-one",
        "status": "pending",
        "blocks": [],
        "blockedBy": [],
    }
    task_path.write_text(json.dumps(task), encoding="utf-8")
    session = Session(
        SessionId("6165ab88-21b7-4b54-a2dd-c25a8ecb0b59"),
        ActorId("6165ab88-21b7-4b54-a2dd-c25a8ecb0b59:lead"),
        "6165ab88-21b7-4b54-a2dd-c25a8ecb0b59",
        "/work/session.jsonl",
        "/work",
    )
    source = ClaudeTaskRawEventSource(session)

    raw_events = source.read(None)
    # the membership fact carries the resume position, so it is emitted last
    assert [event.source_type for event in raw_events] == ["tasks", "task_list"]
    position = raw_events[-1].source_position
    assert source.read(position) == ()
    created = ClaudeCanonicalTranslator().translate(raw_events[0]).canonical_events[0].payload
    assert created == TaskChanged(
        TaskId("1"), "1", "Run tests", "Run the focused suite", "pending", ActorId("worker-one")
    )

    task["status"] = "in_progress"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    raw_events = source.read(position)
    position = raw_events[-1].source_position
    updated = ClaudeCanonicalTranslator().translate(raw_events[0]).canonical_events[0].payload
    assert updated.state == "in_progress"

    task_path.unlink()
    raw_events = source.read(position)
    # deletion needs no synthetic record: the membership fact names the survivors
    assert [event.source_type for event in raw_events] == ["task_list"]
    membership = ClaudeCanonicalTranslator().translate(raw_events[0]).canonical_events[0].payload
    assert membership == TaskListChanged("session", ())


def test_claude_goal_status_is_canonical_goal_state():
    active = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "attachment",
            "uuid": "goal-active",
            "attachment": {
                "type": "goal_status",
                "met": False,
                "condition": "All tests pass",
                "reason": "One test remains",
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="goal-active",
    ))
    completed = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "attachment",
            "uuid": "goal-completed",
            "attachment": {
                "type": "goal_status",
                "met": True,
                "condition": "All tests pass",
                "reason": "The suite is green",
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="goal-completed",
    ))

    assert payloads(active, GoalChanged)[0].payload == GoalChanged(
        "All tests pass", "active", "One test remains"
    )
    assert payloads(completed, GoalChanged)[0].payload == GoalChanged(
        "All tests pass", "completed", "The suite is green"
    )

    cleared = ClaudeCanonicalTranslator().translate(raw_event(
        {"type": "system", "uuid": "goal-cleared", "content": "Goal cleared: All tests pass"},
        harness="claude_code",
        source_type="transcript",
        raw_event_id="goal-cleared",
    ))
    assert payloads(cleared, GoalChanged)[0].payload == GoalChanged(
        "All tests pass", "cleared", None
    )


def test_codex_goal_and_plan_use_shared_goal_and_task_events():
    translator = CodexCanonicalTranslator()
    goal = translator.translate(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "thread_goal_updated",
                "goal": {"objective": "Ship it", "status": "active"},
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="goal-one",
    ))
    plan = translator.translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "plan-one",
                "input": (
                    'const result = await tools.update_plan({plan:['
                    '{step:"Inspect",status:"completed"},'
                    '{step:"Implement",status:"in_progress"}]}); text(result);'
                ),
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="plan-one",
    ))

    assert payloads(goal, GoalChanged)[0].payload == GoalChanged("Ship it", "active", None)
    assert payloads(plan, TaskListChanged)[0].payload.task_ids == (
        TaskId("session-one:lead:plan:1"),
        TaskId("session-one:lead:plan:2"),
    )
    assert [event.payload.subject for event in payloads(plan, TaskChanged)] == ["Inspect", "Implement"]
    assert [event.payload.state for event in payloads(plan, TaskChanged)] == ["completed", "in_progress"]
    assert not payloads(plan, OperationStarted)


def test_codex_goal_state_is_strict_and_clear_removes_the_goal():
    translator = CodexCanonicalTranslator()
    cleared = translator.translate(raw_event(
        {"type": "event_msg", "payload": {"type": "thread_goal_cleared"}},
        harness="codex",
        source_type="rollout",
        raw_event_id="goal-cleared",
    ))
    assert payloads(cleared, GoalChanged)[0].payload == GoalChanged(None, "cleared", None)

    with pytest.raises(TranslationError, match="unknown Codex goal state"):
        translator.translate(raw_event(
            {
                "type": "event_msg",
                "payload": {
                    "type": "thread_goal_updated",
                    "goal": {"objective": "Ship it", "status": "mystery"},
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="goal-invalid",
        ))


def test_codex_source_factory_includes_native_subagent_rollouts(tmp_path, monkeypatch):
    child_path = tmp_path / "sessions" / "2026" / "08" / "14" / "rollout-2026-08-14T10-00-00-child-one.jsonl"
    child_path.parent.mkdir(parents=True)
    records = [
        {
            "type": "session_meta",
            "timestamp": "2026-08-14T10:00:00Z",
            "payload": {
                "cwd": "/work",
                "thread_source": "subagent",
                "parent_thread_id": "parent-native",
                "timestamp": "2026-08-14T10:00:00Z",
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "parent replay"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "started_at": 1786701600},
        },
        {
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "child work"},
        },
    ]
    child_path.write_text("".join(json.dumps(record) + "\n" for record in records))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    session = Session(
        session_id=SessionId("parent-session"),
        lead_actor_id=ActorId("parent-session:lead"),
        native_session_id="parent-native",
        source_reference=str(tmp_path / "not-a-codex-session.jsonl"),
        working_directory="/work",
    )

    sources = CodexRawEventSources().for_session(session)

    assert len(sources) == 1
    assert sources[0].context.actor_id == ActorId("child-one")
    assert sources[0].context.parent_actor_id == ActorId("parent-session:lead")
    raw_events = sources[0].read(None)
    translator = CodexCanonicalTranslator()
    assert translator.translate(raw_events[0]).canonical_events[0].payload.role == "sidecar"
    assert raw_events[1].source_type == "sidecar_replay"
    replay = translator.translate(raw_events[1])
    assert replay.canonical_events == ()
    assert replay.decision == "ignored_nonsemantic"
    assert raw_events[3].source_type == "sidecar_rollout"
    message = payloads(translator.translate(raw_events[3]), MessageCreated)[0]
    assert message.payload.content.text == "child work"


def test_codex_source_factory_rotates_native_subagent_rollouts(tmp_path, monkeypatch):
    sessions_directory = tmp_path / "sessions" / "2026" / "08" / "14"
    sessions_directory.mkdir(parents=True)
    for child_name in ("child-one", "child-two"):
        child_path = sessions_directory / f"rollout-2026-08-14T10-00-00-{child_name}.jsonl"
        child_path.write_text(
            json.dumps({
                "type": "session_meta",
                "timestamp": "2026-08-14T10:00:00Z",
                "payload": {
                    "cwd": "/work",
                    "thread_source": "subagent",
                    "parent_thread_id": "parent-native",
                    "timestamp": "2026-08-14T10:00:00Z",
                },
            })
            + "\n"
            + json.dumps({
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "parent replay"},
            })
            + "\n"
            + json.dumps({
                "type": "event_msg",
                "payload": {"type": "task_started", "started_at": 1786701600},
            })
            + "\n"
            + json.dumps({
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "child work"},
            })
            + "\n"
        )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    session = Session(
        SessionId("parent-session"),
        ActorId("parent-session:lead"),
        "parent-native",
        str(tmp_path / "not-a-codex-session.jsonl"),
        "/work",
    )
    factory = CodexRawEventSources()

    actors = [
        factory.for_session(session)[0].context.actor_id
        for _ in range(3)
    ]

    assert actors == [ActorId("child-one"), ActorId("child-two"), ActorId("child-one")]


def test_codex_source_factory_waits_for_native_child_boundary(tmp_path, monkeypatch):
    child_path = tmp_path / "sessions" / "2026" / "08" / "14" / "rollout-2026-08-14T10-00-00-child-one.jsonl"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(json.dumps({
        "type": "session_meta",
        "timestamp": "2026-08-14T10:00:00Z",
        "payload": {
            "thread_source": "subagent",
            "parent_thread_id": "parent-native",
        },
    }) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    session = Session(
        SessionId("parent-session"),
        ActorId("parent-session:lead"),
        "parent-native",
        str(tmp_path / "not-a-codex-session.jsonl"),
        "/work",
    )

    assert CodexRawEventSources().for_session(session) == ()


def test_codex_source_factory_accepts_string_session_source(tmp_path, monkeypatch):
    rollout_path = (
        tmp_path
        / "sessions"
        / "2026"
        / "08"
        / "14"
        / "rollout-2026-08-14T10-00-00-regular-session.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text(json.dumps({
        "type": "session_meta",
        "timestamp": "2026-08-14T10:00:00Z",
        "payload": {"source": "vscode"},
    }) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    session = Session(
        SessionId("parent-session"),
        ActorId("parent-session:lead"),
        "parent-native",
        str(tmp_path / "not-a-codex-session.jsonl"),
        "/work",
    )

    assert CodexRawEventSources().for_session(session) == ()
    assert codex_rollout.subagent_fork_epoch(str(rollout_path)) is None


def test_hooks_record_exact_raw_bytes_and_the_interpreter_translates_them(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    claude_payload = (
        b'{ "session_id": "claude-session", "transcript_path": "/work/claude.jsonl", '
        b'"cwd": "/work", "hook_event_name": "SessionStart" }'
    )
    codex_payload = (
        b'{ "session_id": "codex-session", "transcript_path": "/work/codex.jsonl", '
        b'"cwd": "/work", "hook_event_name": "SessionStart" }'
    )

    claude_canonical_hook.record_hook(claude_payload)
    codex_canonical_hook.record_hook(codex_payload)

    runtime, interpreter = interpreting_runtime(tmp_path / "events.db")
    # hooks never register; the wrappers do, and the evidence waits until then
    assert runtime.store.untranslated_raw_events(10) == ()
    runtime.register("claude_code", Session(
        SessionId("claude-session"), ActorId("claude-session:lead"),
        "claude-session", "/work/claude.jsonl", "/work",
    ))
    runtime.register("codex", Session(
        SessionId("codex-session"), ActorId("codex-session:lead"),
        "codex-session", "/work/codex.jsonl", "/work",
    ))
    interpreter.tick()

    claude_evidence = EvidenceQueries(runtime.store).session(SessionId("claude-session"))
    codex_evidence = EvidenceQueries(runtime.store).session(SessionId("codex-session"))
    assert claude_evidence[0].payload == claude_payload
    assert len(claude_evidence[0].canonical) == 2
    assert isinstance(claude_evidence[1].canonical[0].event.payload, SessionAccountChanged)
    assert codex_evidence[0].payload == codex_payload
    assert codex_evidence[0].decision == "translated"
    assert len(codex_evidence[0].canonical) == 2


def test_hook_without_native_identity_uses_the_exact_payload_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    payload = (
        b'{"session_id":"claude-session","transcript_path":"/work/claude.jsonl",'
        b'"cwd":"/work","hook_event_name":"SessionStart"}'
    )

    claude_canonical_hook.record_hook(payload)
    claude_canonical_hook.record_hook(payload)

    runtime = CanonicalRuntime(str(tmp_path / "events.db"))
    runtime.register("claude_code", Session(
        SessionId("claude-session"), ActorId("claude-session:lead"),
        "claude-session", "/work/claude.jsonl", "/work",
    ))
    evidence = EvidenceQueries(runtime.store).session(SessionId("claude-session"))
    assert len(evidence) == 2
    assert str(evidence[0].raw_event_id).endswith(hashlib.sha256(payload).hexdigest())
    assert evidence[1].source_type == "account"


def test_hook_recording_preserves_native_child_actor_context(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    payload = json.dumps({
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "hook_event_name": "SubagentStart",
        "hook_event_id": "child-start",
        "agent_id": "child-one",
    }).encode()

    claude_canonical_hook.record_hook(payload)

    runtime, interpreter = interpreting_runtime(tmp_path / "events.db")
    runtime.register("claude_code", Session(
        SessionId("claude-session"), ActorId("claude-session:lead"),
        "claude-session", "/work/claude.jsonl", "/work",
    ))
    interpreter.tick()
    evidence = EvidenceQueries(runtime.store).session(SessionId("claude-session"))[0]
    assert evidence.actor_id == ActorId("child-one")
    assert evidence.parent_actor_id == ActorId("claude-session:lead")
    assert evidence.canonical[0].event.actor_id == ActorId("child-one")


def test_claude_hook_returns_native_pretool_output_and_a_watch_directive(monkeypatch):
    document = {
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "cwd": "/work",
        "hook_event_name": "PreToolUse",
        "hook_event_id": "pretool-one",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello"},
    }
    expected = b'{"hookSpecificOutput":{"updatedInput":{}}}\n'
    watch = FileWatch(
        operation_id="pretool-one",
        source_path="/work/out",
        chunk_source_type="foreground_output",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
    )
    monkeypatch.setattr(
        claude_canonical_hook.foreground,
        "prepare",
        lambda value: SimpleNamespace(output=expected, watch=watch),
    )

    raw_events, output = claude_canonical_hook.hook_raw_events(json.dumps(document).encode())

    assert output == expected
    assert raw_events[0].payload == json.dumps(document).encode()
    directive = raw_events[-1]
    assert directive.source_type == "watch"
    assert json.loads(directive.payload)["action"] == "start"
    assert json.loads(directive.payload)["source_path"] == "/work/out"


def test_claude_post_tool_hook_records_the_watch_finish_directive():
    document = {
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "cwd": "/work",
        "hook_event_name": "PostToolUse",
        "hook_event_id": "posttool-one",
        "tool_name": "Bash",
        "tool_use_id": "command-one",
        "tool_input": {"command": "echo hello"},
        "tool_response": {"stdout": "hello"},
    }

    raw_events, output = claude_canonical_hook.hook_raw_events(json.dumps(document).encode())

    assert output == b""
    directive = raw_events[-1]
    assert directive.source_type == "watch"
    assert json.loads(directive.payload) == {"action": "finish", "operation_id": "command-one"}


def _background_post_tool_document(tmp_path, session_id="claude-session", task_id="btk000001"):
    output_path = (
        tmp_path / "claude-503" / "-work-slug" / session_id / "tasks" / f"{task_id}.output"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"1\n2\n")
    return output_path, {
        "session_id": session_id,
        "transcript_path": "/work/claude.jsonl",
        "cwd": "/work",
        "hook_event_name": "PostToolUse",
        "hook_event_id": "post-background-one",
        "tool_name": "Bash",
        "tool_use_id": "background-op-one",
        "tool_input": {"command": "for i in 1 2; do echo $i; done", "run_in_background": True},
        "tool_response": {"stdout": "", "stderr": "", "backgroundTaskId": task_id},
    }


def test_claude_background_bash_starts_a_watch_on_its_native_output_file(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path))
    output_path, document = _background_post_tool_document(tmp_path)

    raw_events, output = claude_canonical_hook.hook_raw_events(json.dumps(document).encode())

    assert output == b""
    directive = raw_events[-1]
    assert directive.source_type == "watch"
    body = json.loads(directive.payload)
    assert body["action"] == "start"
    assert body["operation_id"] == "background-op-one"
    assert body["source_path"] == str(output_path.resolve())
    assert body["delete_source"] is False
    # the watch shares the operation id, so no finish directive may accompany it
    assert [json.loads(event.payload)["action"] for event in raw_events if event.source_type == "watch"] == ["start"]


def test_claude_background_watch_requires_the_native_task_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path))
    _output_path, document = _background_post_tool_document(tmp_path)

    foreground_document = json.loads(json.dumps(document))
    foreground_document["tool_input"].pop("run_in_background")
    assert claude_foreground.background_watch(foreground_document) is None

    missing_task = json.loads(json.dumps(document))
    missing_task["tool_response"].pop("backgroundTaskId")
    assert claude_foreground.background_watch(missing_task) is None

    no_file_yet = json.loads(json.dumps(document))
    no_file_yet["tool_response"]["backgroundTaskId"] = "btk-without-a-file"
    assert claude_foreground.background_watch(no_file_yet) is None


def test_claude_background_output_streams_into_the_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path / "native"))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    output_path, document = _background_post_tool_document(tmp_path / "native", session_id="session-one")
    document["transcript_path"] = str(tmp_path / "session-one.jsonl")

    claude_canonical_hook.record_hook(json.dumps(document).encode())

    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "events.db")
    runtime.register("claude_code", Session(
        SessionId("session-one"), ActorId("session-one:lead"),
        "session-one", str(tmp_path / "session-one.jsonl"), "/work",
    ))
    interpreter.tick()  # applies the directive
    output_path.write_bytes(b"1\n2\n3\n")  # the job keeps writing
    interpreter.tick()  # pulls chunks
    interpreter.tick()  # translates them

    cursor = runtime.store.latest_cursor()
    operation = runtime.queries().operation_activity(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        OperationId("background-op-one"),
        cursor,
    )
    assert "".join(part.text for part in operation.current_progress()) == "1\n2\n3\n"

    # the session's end is the background watch's end: tail captured, row gone,
    # the NATIVE file untouched
    output_path.write_bytes(b"1\n2\n3\n4\n")
    finish = CanonicalEvent(
        CanonicalEventId("session-finish"),
        SessionId("session-one"),
        ActorId("session-one:lead"),
        None,
        None,
        "claude_code",
        30.0,
        SessionFinished("succeeded", None),
    )
    interpreter._react(SimpleNamespace(event=finish))
    assert runtime.watches.for_session(SessionId("session-one")) == ()
    assert output_path.exists()
    interpreter.tick()
    tail = runtime.queries().operation_activity(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        OperationId("background-op-one"),
        runtime.store.latest_cursor(),
    )
    assert "".join(part.text for part in tail.current_progress()) == "1\n2\n3\n4\n"


def test_claude_foreground_output_is_canonical_append_progress():
    content = b"first line\nsecond line\n"
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "operation_id": "command-one",
            "ordinal": 3,
            "stream": "output",
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
        harness="claude_code",
        source_type="foreground_output",
        raw_event_id="foreground-one",
    ))

    progress = payloads(translation, OperationProgressed)[0].payload
    assert progress.operation_id == "command-one"
    assert progress.ordinal == 3
    assert progress.mode == "append"
    assert progress.content.text == content.decode()


def test_claude_foreground_prepare_rewrites_the_command_into_a_watch(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    document = {
        "session_id": "session-one",
        "agent_id": "child-one",
        "cwd": str(tmp_path),
        "tool_use_id": "command-one",
        "tool_input": {"command": "printf hello"},
    }

    prepared = claude_foreground.prepare(document)

    assert prepared is not None
    native_output = json.loads(prepared.output)
    updated_command = native_output["hookSpecificOutput"]["updatedInput"]["command"]
    assert native_output["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "tee -a" in updated_command
    assert prepared.watch.operation_id == "command-one"
    assert prepared.watch.delete_source is True
    assert prepared.watch.chunk_source_type == "foreground_output"
    assert prepared.watch.source_path in updated_command


def test_claude_foreground_bytes_flow_through_raw_audit_into_operation_projection(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "application"))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    document = {
        "session_id": "session-one",
        "transcript_path": str(tmp_path / "session-one.jsonl"),
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "hook_event_id": "pre-command-one",
        "tool_name": "Bash",
        "tool_use_id": "command-one",
        "tool_input": {"command": "printf hello"},
    }

    output = claude_canonical_hook.record_hook(json.dumps(document).encode())
    assert b"updatedInput" in output

    runtime, interpreter = interpreting_runtime(tmp_path / "application" / "events.db")
    runtime.register("claude_code", Session(
        SessionId("session-one"), ActorId("session-one:lead"),
        "session-one", str(tmp_path / "session-one.jsonl"), str(tmp_path),
    ))
    interpreter.tick()  # applies the watch directive
    watch_sources = runtime.watches.for_session(SessionId("session-one"))
    assert len(watch_sources) == 1
    Path(watch_sources[0].source_path).write_bytes(b"hello\n")
    interpreter.tick()  # pulls the chunk and translates it

    cursor = runtime.store.latest_cursor()
    operation = runtime.queries().operation_activity(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        OperationId("command-one"),
        cursor,
    )
    assert operation.state == "running"
    assert operation.current_progress()[0].text == "hello\n"
    evidence = EvidenceQueries(runtime.store).session(SessionId("session-one"))
    foreground_evidence = [row for row in evidence if row.source_type == "foreground_output"]
    assert len(foreground_evidence) == 1
    assert base64.b64decode(
        json.loads(foreground_evidence[0].payload)["content_base64"]
    ) == b"hello\n"


class PaneFrontend:
    def __init__(self):
        self.launched = []
        self.tags = []
        self.closed = []
        self.scoreboard_lines = None

    def usable(self):
        return True

    def current_window(self):
        return "window-one"

    def set_user_vars(self, window_id, tags):
        self.tags.append((window_id, tags))
        return 0

    def goto_splits_layout(self, window_id):
        return 0

    def find_window(self, tag, value, tree=None):
        if tag == SCOREBOARD_PANE_TAG and self.scoreboard_lines is not None:
            return {"id": "scoreboard-window", "lines": self.scoreboard_lines}
        return None

    def launch_pane(self, command, placement, **arguments):
        self.launched.append((command, placement, arguments))
        if SCOREBOARD_PANE_TAG in arguments.get("var", {}):
            self.scoreboard_lines = 3
        return 0

    def resize_pane(self, tag, axis, amount):
        assert tag[0] == SCOREBOARD_PANE_TAG
        assert axis == "vertical"
        self.scoreboard_lines += amount
        return 0

    def focus_first_pane(self, window_id):
        return 0

    def close_pane(self, var=None, win_id=None):
        self.closed.append(var)
        return 0

    def window_for_session(self, session_id):
        return None

    def ls(self):
        return []


def test_application_terminal_opens_canonical_processes_with_generic_tags(monkeypatch):
    frontend = PaneFrontend()
    monkeypatch.setattr("app.session_terminal.frontends.get", lambda resolve=True: frontend)
    terminal = ApplicationTerminal()

    result = terminal.open_session_panes(
        SessionPaneRequest(SessionId("session-one"), "window-one", 25)
    )

    assert result.succeeded
    assert frontend.tags == [("window-one", {SESSION_WINDOW_TAG: "session-one"})]
    assert [arguments["var"] for _command, _placement, arguments in frontend.launched] == [
        {ACTIVITY_PANE_TAG: "session-one"},
        {SCOREBOARD_PANE_TAG: "session-one"},
    ]
    commands = [command for command, _placement, _arguments in frontend.launched]
    assert commands[0][-2:] == [
        str(Path(__file__).parents[1] / "app" / "terminal_process.py"),
        "session-one",
    ]
    assert commands[1][-2:] == [
        str(Path(__file__).parents[1] / "app" / "scoreboard_process.py"),
        "session-one",
    ]


def test_application_terminal_opens_and_adopts_pending_session_panes(monkeypatch):
    class PendingPaneFrontend(PaneFrontend):
        def __init__(self):
            super().__init__()
            self.adopting = False

        def window_for_session(self, session_id):
            return (
                "window-one"
                if self.adopting and session_id == "pending-4242"
                else None
            )

        def find_window(self, tag, value, tree=None):
            del tree
            if not self.adopting:
                return super().find_window(tag, value)
            windows = {
                (ACTIVITY_PANE_TAG, "pending-4242"): {"id": "activity-window"},
                (SCOREBOARD_PANE_TAG, "pending-4242"): {"id": "scoreboard-window"},
            }
            return windows.get((tag, value))

    frontend = PendingPaneFrontend()
    monkeypatch.setattr("app.session_terminal.frontends.get", lambda resolve=True: frontend)
    bindings = []
    monkeypatch.setattr(
        "app.session_terminal.pending_session.bind",
        lambda pending_id, session_id: bindings.append((pending_id, session_id)),
    )
    terminal = ApplicationTerminal()
    pending_id = pending_session.identity(4242)

    opened = terminal.open_pending_session_panes(
        SessionPaneRequest(pending_id, "window-one", 25)
    )
    frontend.adopting = True
    adopted = terminal.adopt_pending_session_panes(
        pending_id,
        SessionId("session-one"),
    )

    assert opened.succeeded and adopted.succeeded
    assert [command[-2:] for command, _placement, _arguments in frontend.launched] == [
        ["--pending", "pending-4242"],
        ["--pending", "pending-4242"],
    ]
    assert bindings == [(pending_id, SessionId("session-one"))]
    assert frontend.tags[-3:] == [
        ("window-one", {SESSION_WINDOW_TAG: "session-one"}),
        ("activity-window", {ACTIVITY_PANE_TAG: "session-one"}),
        ("scoreboard-window", {SCOREBOARD_PANE_TAG: "session-one"}),
    ]


def test_application_terminal_close_removes_the_session_window_tag(monkeypatch):
    frontend = PaneFrontend()
    frontend.window_for_session = lambda session_id: "window-one"
    frontend.clear_tab_color = lambda window_id: 0
    monkeypatch.setattr("app.session_terminal.frontends.get", lambda resolve=True: frontend)

    result = ApplicationTerminal().close_session_panes(SessionId("session-one"))

    assert result.succeeded
    assert frontend.tags == [("window-one", {SESSION_WINDOW_TAG: ""})]


def test_application_terminal_resolves_the_active_tab_without_window_environment(monkeypatch):
    frontend = PaneFrontend()
    frontend.current_window = lambda: ""
    frontend.ls = lambda: [
        {
            "is_focused": True,
            "tabs": [
                {
                    "is_active": True,
                    "windows": [
                        {
                            "id": 41,
                            "user_vars": {SESSION_WINDOW_TAG: "session-one"},
                        }
                    ],
                }
            ],
        }
    ]
    monkeypatch.setattr("app.session_terminal.frontends.get", lambda resolve=True: frontend)

    assert ApplicationTerminal().current_session() == SessionId("session-one")


def test_pane_keybinding_uses_the_canonical_terminal_service(monkeypatch):
    class Terminal:
        def __init__(self):
            self.toggles = []

        def current_session(self):
            return SessionId("session-one")

        def toggle_session_panes(self, session_id, width_percent):
            self.toggles.append((session_id, width_percent))
            return TerminalResult(True)

    terminal = Terminal()
    monkeypatch.setattr(
        "app.bootstrap.build_default_application",
        lambda: SimpleNamespace(terminal=terminal),
    )
    monkeypatch.setattr(
        terminal_panes.pane_preferences,
        "width_percent",
        lambda directory: 31,
    )

    assert terminal_panes.main(["toggle"]) == 0
    assert terminal.toggles == [(SessionId("session-one"), 31)]


def test_pane_resize_keybindings_use_the_canonical_terminal_service(monkeypatch):
    class Terminal:
        def __init__(self):
            self.resizes = []
            self.widths = []

        def current_session(self):
            return SessionId("session-one")

        def resize_activity_pane(self, session_id, columns):
            self.resizes.append((session_id, columns))
            return TerminalResult(True)

        def activity_pane_geometry(self, session_id):
            return (25, 100)

        def set_activity_pane_width(self, session_id, width_percent):
            self.widths.append((session_id, width_percent))
            return TerminalResult(True)

    terminal = Terminal()
    remembered = []
    monkeypatch.setattr(
        "app.bootstrap.build_default_application",
        lambda: SimpleNamespace(terminal=terminal),
    )
    monkeypatch.setattr(terminal_panes.pane_preferences, "resize_columns", lambda: 7)
    monkeypatch.setattr(
        terminal_panes.pane_preferences,
        "configured_width_percent",
        lambda: 31,
    )
    monkeypatch.setattr(
        terminal_panes.pane_preferences,
        "remember_width",
        lambda directory, width_percent: remembered.append((directory, width_percent)),
    )

    assert terminal_panes.main(["grow"]) == 0
    assert terminal_panes.main(["shrink"]) == 0
    assert terminal_panes.main(["reset"]) == 0
    assert terminal_panes.main(["setpct", "75"]) == 0

    assert terminal.resizes == [
        (SessionId("session-one"), 7),
        (SessionId("session-one"), -7),
    ]
    assert terminal.widths == [
        (SessionId("session-one"), 31),
        (SessionId("session-one"), 75),
    ]
    assert [width_percent for _directory, width_percent in remembered] == [25, 25, 31, 75]


def test_claude_hook_and_child_transcript_deduplicate_actor_start():
    child_context = {
        "actor_id": ActorId("child-one"),
        "parent_actor_id": ActorId("session-one:lead"),
    }
    hook = replace(
        raw_event(
            {
                "hook_event_name": "SubagentStart",
                "hook_event_id": "child-start",
                "agent_id": "child-one",
                "agent_type": "researcher",
            },
            harness="claude_code",
            source_type="hook",
            raw_event_id="child-hook",
        ),
        **child_context,
    )
    transcript_record = replace(
        raw_event(
            {"type": "user", "uuid": "child-prompt", "message": {"content": "inspect"}},
            harness="claude_code",
            source_type="transcript",
            raw_event_id="child-transcript",
            source_position="0",
        ),
        **child_context,
    )

    hook_start = ClaudeCanonicalTranslator().translate(hook).canonical_events[0]
    transcript_start = ClaudeCanonicalTranslator().translate(transcript_record).canonical_events[0]

    assert CanonicalEventCodec().encode(hook_start) == CanonicalEventCodec().encode(transcript_start)


def test_claude_first_teammate_message_starts_the_actor_once():
    teammate_message = replace(
        raw_event(
            {
                "type": "user",
                "uuid": "team-message-one",
                "message": {
                    "content": '<teammate-message teammate_id="worker-one">hello</teammate-message>',
                },
            },
            harness="claude_code",
            source_type="teammate_transcript",
            raw_event_id="worker-transcript",
            source_position="0",
        ),
        actor_id=ActorId("worker-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )

    result = ClaudeCanonicalTranslator().translate(teammate_message)

    actor_starts = [
        event for event in result.canonical_events if isinstance(event.payload, ActorStarted)
    ]
    assert len(actor_starts) == 1
    assert len({event.event_id for event in result.canonical_events}) == len(result.canonical_events)


def test_claude_later_teammate_message_reuses_the_canonical_actor_start():
    translator = ClaudeCanonicalTranslator()
    first_record = replace(
        raw_event(
            {"type": "user", "uuid": "first", "message": {"content": "inspect"}},
            harness="claude_code",
            source_type="teammate_transcript",
            raw_event_id="first-record",
            source_position="0",
        ),
        actor_id=ActorId("worker-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )
    later_message = replace(
        raw_event(
            {
                "type": "user",
                "uuid": "later",
                "timestamp": "2026-08-14T08:00:00Z",
                "message": {
                    "content": '<teammate-message teammate_id="worker-one">done</teammate-message>',
                },
            },
            harness="claude_code",
            source_type="teammate_transcript",
            raw_event_id="later-message",
            source_position="500",
        ),
        actor_id=ActorId("worker-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )

    first_start = translator.translate(first_record).canonical_events[0]
    later_start = translator.translate(later_message).canonical_events[0]

    assert CanonicalEventCodec().encode(first_start) == CanonicalEventCodec().encode(later_start)


def test_claude_lead_start_uses_the_first_root_record_with_a_working_directory():
    translator = ClaudeCanonicalTranslator()
    plumbing = translator.translate(raw_event(
        {"type": "queue-operation", "operation": "enqueue"},
        harness="claude_code",
        source_type="transcript",
        raw_event_id="queue",
        source_position="0",
    ))
    root_record = translator.translate(raw_event(
        {
            "type": "user",
            "uuid": "prompt-one",
            "parentUuid": None,
            "cwd": "/work",
            "message": {"content": "hello"},
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="root-record",
        source_position="297",
    ))
    hook = translator.translate(raw_event(
        {"hook_event_name": "SessionStart", "cwd": "/work"},
        harness="claude_code",
        source_type="hook",
        raw_event_id="session-hook",
    ))

    assert plumbing.decision == "ignored_nonsemantic"
    codec = CanonicalEventCodec()
    assert [codec.encode(event) for event in root_record.canonical_events[:2]] == [
        codec.encode(event) for event in hook.canonical_events
    ]


def test_claude_teammate_hook_and_transcript_share_one_actor_identity(monkeypatch, tmp_path):
    main_path = tmp_path / "session-one.jsonl"
    main_path.write_text('{"type":"user","uuid":"lead"}\n')
    child_path = tmp_path / "session-one" / "subagents" / "agent-worker-one.jsonl"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(json.dumps({
        "type": "user",
        "uuid": "worker-prompt",
        "message": {"content": "inspect"},
    }) + "\n")
    child_path.with_name("agent-worker-one.meta.json").write_text(json.dumps({
        "taskKind": "in_process_teammate",
    }))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    hook_payload = json.dumps({
        "session_id": "session-one",
        "transcript_path": str(main_path),
        "hook_event_name": "SubagentStart",
        "hook_event_id": "worker-start",
        "agent_id": "worker-one",
        "agent_type": "reviewer",
    }).encode()

    claude_canonical_hook.record_hook(hook_payload)
    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "events.db")
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "session-one",
        str(main_path),
        str(tmp_path),
    )
    runtime.register("claude_code", session)
    source = ClaudeTranscriptRawEventSource(
        replace(
            session.source_context,
            actor_id=ActorId("worker-one"),
            parent_actor_id=session.lead_actor_id,
            source_reference=str(child_path),
        ),
        "teammate",
    )
    runtime.recorder.record(source.read(None))
    interpreter.tick()

    actors = runtime.queries().actors(SessionId("session-one"))
    teammate = next(actor for actor in actors if actor.actor_id == ActorId("worker-one"))
    assert teammate.role == "teammate"


def test_codex_hook_maps_unique_compaction_lifecycle():
    translator = CodexCanonicalTranslator()
    before = translator.translate(raw_event(
        {"hook_event_name": "PreCompact", "hook_event_id": "compact-one"},
        harness="codex",
        source_type="hook",
        raw_event_id="compact-before",
    ))
    after = translator.translate(raw_event(
        {"hook_event_name": "PostCompact", "hook_event_id": "compact-one"},
        harness="codex",
        source_type="hook",
        raw_event_id="compact-after",
    ))

    assert isinstance(before.canonical_events[0].payload, CompactionStarted)
    assert isinstance(after.canonical_events[0].payload, CompactionFinished)


def test_codex_session_start_hook_matches_rollout_metadata():
    translator = CodexCanonicalTranslator()
    hook = translator.translate(raw_event(
        {"hook_event_name": "SessionStart", "cwd": "/work"},
        harness="codex",
        source_type="hook",
        raw_event_id="session-hook",
    ))
    rollout = translator.translate(raw_event(
        {
            "timestamp": "2026-08-14T12:00:00Z",
            "type": "session_meta",
            "payload": {"cwd": "/work", "originator": "codex-tui"},
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="session-rollout",
        source_position="0",
    ))

    codec = CanonicalEventCodec()
    assert hook.decision == "translated"
    assert [codec.encode(event) for event in hook.canonical_events] == [
        codec.encode(event) for event in rollout.canonical_events
    ]


def test_hook_native_identity_reuse_with_different_bytes_is_a_hard_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.host.ApplicationHost.ensure_running", lambda self: None)
    first = (
        b'{"session_id":"session-one","transcript_path":"/work/session.jsonl",'
        b'"cwd":"/work","hook_event_name":"PreToolUse","hook_event_id":"hook-one",'
        b'"tool_use_id":"tool-one",'
        b'"tool_name":"Bash","tool_input":{"command":"first"}}'
    )
    changed = first.replace(b'"first"', b'"changed"')
    claude_canonical_hook.record_hook(first)

    with pytest.raises(EventIdentityConflict, match="raw event identity reused"):
        claude_canonical_hook.record_hook(changed)


def test_catalogs_expose_only_what_depends_on_the_directory(tmp_path):
    """The catalogue is now the per-DIRECTORY half of the menu vocabulary.

    Everything a harness offers unconditionally moved onto HarnessInfo, which is
    a frozen literal built at import -- so only the slash commands, discovered by
    walking the session's own directory, still need a QueryContext.
    """
    application = build_application(str(tmp_path))
    context = QueryContext(session_id=None, working_directory=str(tmp_path))

    claude_catalog = application.catalog.read("claude_code", context)
    codex_catalog = application.catalog.read("codex", context)

    assert {command.command for command in claude_catalog.commands} != {
        command.command for command in codex_catalog.commands
    }
    assert not hasattr(claude_catalog, "models")
    assert not hasattr(claude_catalog, "accounts")


def test_static_menu_vocabulary_lives_on_the_harness_descriptor():
    from plugins.claude_code.plugin import plugin as claude_plugin
    from plugins.codex.plugin import plugin as codex_plugin

    assert [model.model_id for model in claude_plugin.info.models] == [
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    assert all(model.model_id.startswith("gpt-") for model in codex_plugin.info.models)
    assert claude_plugin.info.rewind_modes
    assert codex_plugin.info.rewind_modes == ()
    # only one harness has a subscription switcher behind it
    assert claude_plugin.info.supports_accounts
    assert not codex_plugin.info.supports_accounts


def test_reasoning_levels_belong_to_the_model_that_offers_them():
    """A level a model does not have must not be advertised for it.

    Measured on the live picker: one codex model's advanced sub-step holds Max
    alone, with no Ultra row, while its siblings list both. The old flat
    per-harness list promised Ultra for every model, so the menu offered a level
    the picker would then refuse.
    """
    from plugins.codex.plugin import plugin as codex_plugin

    by_id = {model.model_id: model for model in codex_plugin.info.models}
    luna = {effort.value for effort in by_id["gpt-5.6-luna"].efforts}
    sol = {effort.value for effort in by_id["gpt-5.6-sol"].efforts}

    assert "ultra" not in luna
    assert "ultra" in sol
    # every model still names exactly one default
    for model in codex_plugin.info.models:
        assert len([effort for effort in model.efforts if effort.default]) == 1


def test_claude_memory_capture_stays_inside_the_plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "application-data"))
    monkeypatch.setattr(claude_memory_state.memory, "in_scope", lambda directory: True)
    monkeypatch.setattr(claude_memory_state.memory, "is_memory", lambda path: True)
    monkeypatch.setattr(
        claude_memory_state.memory,
        "rel",
        lambda path: "platform/design.md",
    )
    claude_memory_state.capture(
        {
            "session_id": "session-memory",
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "design.md")},
            "agent_id": "actor-memory",
        }
    )

    snapshot = claude_memory_state.snapshot(SessionId("session-memory"))

    assert len(snapshot.notes) == 1
    assert snapshot.notes[0].relative_path == "platform/design.md"
    assert snapshot.notes[0].action == "Update"
    assert snapshot.notes[0].actor_name == "actor-memory"


def test_only_claude_declares_the_optional_memory_port():
    descriptors = {plugin.info.name: plugin for plugin in installed_plugins()}
    assert descriptors["claude_code"].memory is not None
    assert descriptors["codex"].memory is None


def test_claude_statusline_writes_plugin_owned_typed_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "application-data"))
    monkeypatch.setattr(
        claude_statusline.ACC,
        "current",
        lambda: {"slug": "work", "label": "Work"},
    )
    monkeypatch.setattr(
        "plugins.claude_code.usage_rows.account.registry",
        lambda: [{"slug": "work", "label": "Work", "alias": "work"}],
    )
    claude_statusline.capture(
        json.dumps(
            {
                "session_id": "session-usage",
                "rate_limits": {
                    "five_hour": {"used_percentage": 25, "resets_at": 2_000_000_000},
                    "seven_day": {"used_percentage": 40, "resets_at": 2_000_100_000},
                },
            }
        ).encode()
    )

    stored = claude_usage_state.latest_by_account()["work"]
    rows = claude_usage_reader.read()

    assert stored["windows"]["five_hour"] == 25
    assert rows[0].account_id == "work"
    assert [window.label for window in rows[0].windows] == ["5h", "7d"]
    assert rows[0].scheduling_score == Decimal("75")


class RecordingSessionTerminal:
    def __init__(self):
        self.requests = []
        self.live_window_id = None

    def window_for_session(self, session_id):
        del session_id
        return self.live_window_id

    def open_tab(self, request):
        self.requests.append(request)
        return TabResult(True, "window-one")


def test_launchers_build_native_commands_and_share_terminal_launch_mechanics(tmp_path):
    application = build_application(str(tmp_path))
    terminal = RecordingSessionTerminal()
    launcher = HarnessLauncherService(application.registry, terminal)
    attachment = AttachmentReference("/work/context.md", "context.md", "text/markdown")

    claude_result = launcher.launch(
        "claude_code",
        LaunchRequest(
            working_directory="/work",
            initial_text="hello",
            model_id="fable",
            effort="high",
            account_id=None,
            resume_session_id=None,
            attachments=(attachment,),
        ),
    )
    codex_result = launcher.launch(
        "codex",
        LaunchRequest(
            working_directory="/work",
            initial_text="hello",
            model_id="gpt-5.6-terra",
            effort="high",
            account_id=None,
            resume_session_id=None,
            attachments=(attachment,),
        ),
    )

    assert claude_result.status == codex_result.status == "started"
    assert terminal.requests[0].command == (
        sys.executable,
        str(Path(claude_canonical_hook.__file__).with_name("command.py")),
        "claude",
        "--model",
        "fable",
        "--effort",
        "high",
        "@/work/context.md\nhello",
    )
    assert terminal.requests[1].command == (
        sys.executable,
        str(Path(codex_command.__file__)),
        "-C",
        "/work",
        "-m",
        "gpt-5.6-terra",
        "-c",
        "model_reasoning_effort=high",
        "/work/context.md\nhello",
    )


def test_claude_terminal_probe_owns_input_box_grammar(tmp_path):
    divider = "\x1b[m\x1b[38:2:136:136:136m" + "─" * 20
    screen = divider + "\n\x1b[m❯\xa0\x1b[22;2mapply the fix\n" + divider

    class ScreenFrontend:
        def read_screen(self, window_id, region=None, ansi=False):
            assert window_id == "window-one"
            assert region is None
            assert ansi is True
            return ScreenText(screen)

    plugin = build_application(str(tmp_path)).registry.plugin("claude_code")
    state = plugin.terminal_probe.input_state(ScreenFrontend(), "window-one")

    assert state.suggestion == "apply the fix"
    assert state.typed_text == ""


class LiveTerminal:
    def window_for_session(self, session_id):
        del session_id
        return "window-one"


class ParkedTerminal:
    def window_for_session(self, session_id):
        del session_id


def control_context(session, terminal, pending_attention=None):
    return ControlContext(session, terminal, None, None, None, pending_attention)


def test_claude_question_discussion_is_delivered_after_declining(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "plugins.claude_code.controller.askdialog.drive",
        lambda _terminal, _window, _prompts, _answers, *, chat: calls.append(("dialog", chat)),
    )
    monkeypatch.setattr(
        "plugins.claude_code.controller.tui.type_command",
        lambda _terminal, _window, text: (calls.append(("discussion", text)) or (True, False)),
    )
    application = build_application(str(tmp_path))
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native-one",
        "/work/session.jsonl",
        "/work",
    )
    request = AnswerQuestion(
        session_id=session.session_id,
        request_id="request-one",
        attention_id="attention-one",
        decision="discuss",
        discussion="change the approach",
    )
    attention = AttentionRequested(
        "attention-one",
        "question",
        (AttentionPrompt("question-one", None, "Continue?", False, ()),),
        None,
    )

    outcome = application.registry.plugin("claude_code").controller.execute(
        request,
        control_context(session, LiveTerminal(), attention),
    )

    assert outcome.status == "acknowledged"
    assert calls == [("dialog", True), ("discussion", "change the approach")]


def test_codex_question_discussion_stays_in_the_native_dialog(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "plugins.codex.controller.dialog.decline",
        lambda _terminal, _window, _prompts, message: calls.append(message),
    )
    application = build_application(str(tmp_path))
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native-one",
        "/work/rollout-session-one.jsonl",
        "/work",
    )
    request = AnswerQuestion(
        session_id=session.session_id,
        request_id="request-one",
        attention_id="attention-one",
        decision="discuss",
        discussion="change the approach",
    )
    attention = AttentionRequested(
        "attention-one",
        "question",
        (AttentionPrompt("question-one", None, "Continue?", False, ()),),
        None,
    )

    outcome = application.registry.plugin("codex").controller.execute(
        request,
        control_context(session, LiveTerminal(), attention),
    )

    assert outcome.status == "acknowledged"
    assert calls == ["change the approach"]


def test_claude_model_control_resolves_the_native_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "plugins.claude_code.controller.tui.type_command",
        lambda _terminal, _window, _text: (True, False),
    )
    monkeypatch.setattr(
        "plugins.claude_code.controller.confirmdialog.confirm",
        lambda _terminal, _window: {"dialog": True, "digit": "1"},
    )
    application = build_application(str(tmp_path))
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native-one",
        "/work/session.jsonl",
        "/work",
    )
    request = SelectModel(session.session_id, "request-one", "opus")

    outcome = application.registry.plugin("claude_code").controller.execute(
        request,
        control_context(session, LiveTerminal()),
    )

    assert outcome.status == "acknowledged"
    assert outcome.confirmation == "confirmed"


def test_claude_account_migration_uses_only_projected_context(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "plugins.claude_code.controller.account.migration_target",
        lambda current_account: {
            "slug": "account-two",
            "alias": "account-two",
        },
    )
    class MigrationTerminal:
        def __init__(self):
            self.closed = []
            self.launched = []

        def window_for_session(self, session_id):
            assert session_id == SessionId("session-one")
            return "window-one"

        def close_tab(self, window_id):
            self.closed.append(window_id)
            return TerminalResult(True)

        def open_tab(self, request):
            self.launched.append(request)
            return TabResult(True, "window-two")

    terminal = MigrationTerminal()
    application = build_application(str(tmp_path))
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native-one",
        "/work/session.jsonl",
        "/work",
    )
    context = ControlContext(
        session,
        terminal,
        ModelReference("fable", None, None),
        "high",
        AccountReference("account-one", "Account One"),
        None,
    )

    outcome = application.registry.plugin("claude_code").controller.execute(
        MigrateAccount(session.session_id, "request-one"),
        context,
    )

    assert outcome.status == "acknowledged"
    assert outcome.target_account_id == "account-two"
    assert terminal.closed == ["window-one"]
    assert terminal.launched[0].command == (
        sys.executable,
        str(Path(claude_canonical_hook.__file__).with_name("command.py")),
        "account-two", "--resume", "session-one", "--model", "fable",
    )


@pytest.mark.parametrize(
    ("harness", "native_writer"),
    [
        ("claude_code", "plugins.claude_code.controller.transcript.set_session_title"),
        ("codex", "plugins.codex.controller.title.set_session_title"),
    ],
)
def test_parked_rename_uses_only_the_owning_harness_title_store(
    monkeypatch,
    tmp_path,
    harness,
    native_writer,
):
    calls = []
    monkeypatch.setattr(native_writer, lambda path, name: calls.append((path, name)) or True)
    application = build_application(str(tmp_path))
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "native-one",
        "/work/native-session",
        "/work",
    )
    request = RenameSession(session.session_id, "request-one", "New title")

    outcome = application.registry.plugin(harness).controller.execute(
        request,
        control_context(session, ParkedTerminal()),
    )

    assert outcome.status == "acknowledged"
    assert calls == [(session.source_reference, "New title")]


def test_claude_prompt_and_codex_prompt_share_the_message_model():
    claude = ClaudeCanonicalTranslator().translate(
        raw_event(
            {"type": "user", "uuid": "claude-message", "message": {"content": "fix it"}},
            harness="claude_code",
            source_type="transcript",
            raw_event_id="claude-prompt",
        )
    )
    codex = CodexCanonicalTranslator().translate(
        raw_event(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "fix it"}},
            harness="codex",
            source_type="rollout",
            raw_event_id="codex-prompt",
        )
    )
    assert isinstance(claude.canonical_events[0].payload, MessageCreated)
    assert isinstance(codex.canonical_events[0].payload, MessageCreated)
    assert claude.canonical_events[0].payload.role == codex.canonical_events[0].payload.role == "user"
    assert claude.canonical_events[0].payload.phase == codex.canonical_events[0].payload.phase == "prompt"


def test_claude_child_prompt_is_authored_by_the_parent_agent():
    child_prompt = replace(
        raw_event(
            {"type": "user", "uuid": "child-prompt", "message": {"content": "inspect it"}},
            harness="claude_code",
            source_type="transcript",
            raw_event_id="child-prompt",
            source_position="1",
        ),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )

    messages = payloads(ClaudeCanonicalTranslator().translate(child_prompt), MessageCreated)

    assert messages[0].payload.role == "parent"


def test_claude_async_agent_launch_stays_running_until_task_notification():
    translator = ClaudeCanonicalTranslator()
    started = translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "agent-tool-one",
            "tool_name": "Agent",
            "tool_input": {
                "description": "Get current weather in Bali",
                "prompt": "Look up current weather and a short forecast.",
                "subagent_type": "general-purpose",
            },
        },
        harness="claude_code",
        source_type="hook",
        raw_event_id="agent-start",
    ))
    launch_ack = translator.translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "agent-tool-one",
            "tool_name": "Agent",
            "tool_input": {"description": "Get current weather in Bali"},
            "tool_response": {
                "isAsync": True,
                "status": "async_launched",
                "agentId": "child-one",
            },
        },
        harness="claude_code",
        source_type="hook",
        raw_event_id="agent-launch-ack",
    ))

    child_started = payloads(started, ActorAssignmentStarted)[0].payload
    assert child_started.brief.text == "Get current weather in Bali"
    assert payloads(launch_ack, OperationFinished)
    assert not payloads(launch_ack, ActorAssignmentFinished)


def test_claude_task_notification_finishes_actor_assignment_instead_of_creating_user_message():
    notification = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "user",
            "uuid": "task-notification-one",
            "origin": {"kind": "task-notification"},
            "promptSource": "system",
            "message": {
                "content": (
                    "<task-notification><task-id>child-one</task-id>"
                    "<tool-use-id>agent-tool-one</tool-use-id>"
                    "<status>completed</status>"
                    '<summary>Agent "Get current weather in Bali" finished</summary>'
                    "<result>Sunny, 29°C.</result></task-notification>"
                )
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="task-notification",
    ))

    finished = payloads(notification, ActorAssignmentFinished)
    assert not payloads(notification, MessageCreated)
    assert finished[0].payload.assignment_id == "agent-tool-one"
    assert finished[0].payload.outcome == "succeeded"
    assert finished[0].payload.result.text == "Sunny, 29°C."


def test_claude_tool_reference_result_has_a_readable_output():
    result = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "user",
            "uuid": "tool-result-one",
            "message": {
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "tool-search-one",
                    "content": [{"type": "tool_reference", "tool_name": "WebSearch"}],
                }]
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="tool-result",
    ))

    progress = payloads(result, OperationProgressed)[0].payload
    assert progress.content.text == "→ loaded tool: WebSearch"


def test_claude_tool_search_uses_its_query_as_the_operation_arguments():
    translated = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "id": "tool-search-one",
                    "name": "ToolSearch",
                    "input": {"query": "select:WebSearch", "max_results": 1},
                }]
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="tool-search",
    ))

    operation = payloads(translated, OperationStarted)[0].payload
    assert operation.arguments == TextContent("select:WebSearch")


def test_claude_child_actor_uses_the_task_description_from_its_sidecar(tmp_path):
    transcript_path = tmp_path / "agent-child-one.jsonl"
    transcript_path.with_suffix(".meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "description": "Get Bali weather"}),
        encoding="utf-8",
    )
    event = replace(
        raw_event(
            {
                "type": "user",
                "uuid": "child-prompt",
                "cwd": "/work",
                "message": {"content": "Find the weather"},
            },
            harness="claude_code",
            source_type="child_transcript",
            raw_event_id="child-prompt",
            source_position="0",
        ),
        source_name=str(transcript_path),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )

    translated = ClaudeCanonicalTranslator().translate(event)

    actor = payloads(translated, ActorStarted)[0].payload
    name = payloads(translated, ActorNameChanged)[0].payload
    assert actor.name == "child-one"
    assert name.name == "Get Bali weather"


def test_native_instruction_wrappers_are_canonical_system_messages():
    codex = CodexCanonicalTranslator().translate(
        raw_event(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<environment_context>facts</environment_context>"}],
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="codex-system-message",
        )
    )
    claude = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                "type": "user",
                "uuid": "claude-system-message",
                "isMeta": True,
                "message": {"content": "Continue from where you left off."},
            },
            harness="claude_code",
            source_type="transcript",
            raw_event_id="claude-system-message",
        )
    )

    assert codex.canonical_events[0].payload.role == "system"
    assert claude.canonical_events[0].payload.role == "system"


def test_claude_title_records_preserve_native_title_origin():
    translator = ClaudeCanonicalTranslator()
    custom = translator.translate(raw_event(
        {"type": "agent-name", "agentName": "Chosen name"},
        harness="claude_code",
        source_type="transcript",
        raw_event_id="custom-title",
    ))
    automatic = translator.translate(raw_event(
        {"type": "ai-title", "aiTitle": "Generated name"},
        harness="claude_code",
        source_type="transcript",
        raw_event_id="automatic-title",
    ))

    assert custom.canonical_events[0].payload == SessionTitleChanged("Chosen name", "custom")
    assert automatic.canonical_events[0].payload == SessionTitleChanged("Generated name", "automatic")


def test_claude_assistant_preserves_reasoning_and_model_without_duplicate_usage():
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "assistant",
            "uuid": "assistant-one",
            "message": {
                "model": "claude-opus-4-8",
                "content": [
                    {"type": "thinking", "thinking": "Inspect the failure"},
                    {"type": "text", "text": "I found it"},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="assistant",
    ))

    reasoning = payloads(translation, ReasoningCreated)[0].payload
    model = payloads(translation, ModelChanged)[0].payload
    context = payloads(translation, ContextReported)[0].payload
    assert reasoning.content.text == "Inspect the failure"
    assert model.current.native_id == "claude-opus-4-8"
    assert model.current.display_name == "opus-4.8"
    assert model.current.selection_id == "opus"
    assert context.used_tokens == 10
    assert context.window_tokens == 1_000_000
    assert context.model == model.current
    assert payloads(translation, UsageReported) == []


def test_claude_otel_translates_raw_usage_once_by_model_and_query_source():
    def point(attributes, value):
        return {
            "attributes": [
                {"key": key, "value": {"stringValue": attribute_value}}
                for key, attribute_value in attributes.items()
            ],
            "asInt" if isinstance(value, int) else "asDouble": value,
        }

    document = {
        "resourceMetrics": [{
            "scopeMetrics": [{
                "metrics": [
                    {
                        "name": "claude_code.token.usage",
                        "sum": {"dataPoints": [
                            point({
                                "session.id": "session-one",
                                "query_source": "main",
                                "model": "claude-opus-4-8",
                                "type": "input",
                            }, 10),
                            point({
                                "session.id": "session-one",
                                "query_source": "main",
                                "model": "claude-opus-4-8",
                                "type": "cacheRead",
                            }, 7),
                        ]},
                    },
                    {
                        "name": "claude_code.cost.usage",
                        "sum": {"dataPoints": [
                            point({
                                "session.id": "session-one",
                                "query_source": "main",
                                "model": "claude-opus-4-8",
                            }, 0.25),
                        ]},
                    },
                ],
            }],
        }],
    }
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        document,
        harness="claude_code",
        source_type="otel",
        raw_event_id="otel-one",
    ))
    reports = payloads(translation, UsageReported)
    assert len(reports) == 1
    usage = reports[0].payload
    assert usage.model == ModelReference("claude-opus-4-8", "opus-4.8", "opus")
    assert usage.tokens.input_tokens == 10
    assert usage.tokens.cache_read_tokens == 7
    assert usage.cost_in_usd == Decimal("0.25")


def test_claude_otel_delivery_records_raw_and_canonical_audit(tmp_path):
    runtime, interpreter = interpreting_runtime(tmp_path / "events.db")
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "session-one",
        "/tmp/session-one.jsonl",
        "/work",
    )
    runtime.register("claude_code", session)
    document = {
        "resourceMetrics": [{
            "scopeMetrics": [{
                "metrics": [{
                    "name": "claude_code.token.usage",
                    "sum": {"dataPoints": [{
                        "attributes": [
                            {"key": "session.id", "value": {"stringValue": "session-one"}},
                            {"key": "query_source", "value": {"stringValue": "main"}},
                            {"key": "type", "value": {"stringValue": "output"}},
                        ],
                        "asInt": 9,
                    }]},
                }],
            }],
        }],
    }
    raw_body = json.dumps(document, separators=(",", ":")).encode()

    assert claude_otel_receiver.deliver(runtime.recorder, runtime.sessions, raw_body) == 1
    assert claude_otel_receiver.deliver(runtime.recorder, runtime.sessions, raw_body) == 1
    interpreter.tick()

    evidence = EvidenceQueries(runtime.store).session(SessionId("session-one"))
    assert len(evidence) == 1
    assert evidence[0].payload == raw_body
    assert evidence[0].decision == "translated"
    assert len(evidence[0].canonical) == 1
    assert runtime.queries().usage(SessionId("session-one")).tokens.output_tokens == 9


def test_claude_operation_execution_comes_from_native_tool_semantics():
    background = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "background-one",
            "tool_name": "Bash",
            "tool_input": {
                "command": "make test",
                "run_in_background": True,
                "description": "Run tests",
            },
        },
        harness="claude_code",
        source_type="hook",
        raw_event_id="background",
    ))
    monitor = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "monitor-one",
            "tool_name": "Monitor",
            "tool_input": {"task_id": "task-one"},
        },
        harness="claude_code",
        source_type="hook",
        raw_event_id="monitor",
    ))

    assert payloads(background, OperationStarted)[0].payload.execution == "background"
    assert payloads(background, OperationStarted)[0].payload.description == "Run tests"
    assert payloads(background, OperationStarted)[0].payload.arguments.text == "make test"
    assert payloads(monitor, OperationStarted)[0].payload.execution == "monitor"


def test_codex_write_stdin_continues_the_original_operation():
    translator = CodexCanonicalTranslator()

    started = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "command-one",
            "input": 'tools.exec_command({"cmd":"read value"})',
        },
    }, harness="codex", source_type="rollout", raw_event_id="command", source_position="40"))
    initial_output = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "command-one",
            "output": json.dumps({"session_id": 77, "output": "waiting\n"}),
        },
    }, harness="codex", source_type="rollout", raw_event_id="command-output", source_position="41"))
    provided = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "input-one",
            "input": 'tools.write_stdin({session_id:77,chars:"yes\\n",yield_time_ms:1000})',
        },
    }, harness="codex", source_type="rollout", raw_event_id="stdin", source_position="42"))
    continued_output = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "input-one",
            "output": "accepted\n",
        },
    }, harness="codex", source_type="rollout", raw_event_id="stdin-output", source_position="43"))
    finished = translator.translate(raw_event({
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "id": "execution-one",
                "process_id": 77,
                "status": "completed",
                "aggregated_output": "waiting\naccepted\n",
                "exit_code": 0,
            },
        },
    }, harness="codex", source_type="rollout", raw_event_id="command-finished", source_position="44"))

    operation_id = payloads(started, OperationStarted)[0].payload.operation_id
    assert operation_id == "command-one"
    assert payloads(initial_output, OperationProgressed)[0].payload.operation_id == operation_id
    input_payload = payloads(provided, OperationInputProvided)[0].payload
    assert input_payload.operation_id == operation_id
    assert input_payload.content.text == "yes\n"
    assert input_payload.closed is False
    assert payloads(continued_output, OperationProgressed)[0].payload.operation_id == operation_id
    finished_payload = payloads(finished, OperationFinished)[0].payload
    assert finished_payload.operation_id == operation_id
    assert finished_payload.result.text == "waiting\naccepted\n"


def test_codex_empty_write_stdin_poll_is_raw_only_and_ctrl_c_is_input():
    translator = CodexCanonicalTranslator()
    translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "command-one",
            "input": 'tools.exec_command({"cmd":"sleep 30"})',
        },
    }, harness="codex", source_type="rollout", raw_event_id="command"))
    translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "command-one",
            "output": json.dumps({"session_id": 88, "output": ""}),
        },
    }, harness="codex", source_type="rollout", raw_event_id="command-output", source_position="11"))
    poll = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "poll-one",
            "input": 'tools.write_stdin({session_id:88,chars:"",yield_time_ms:1000})',
        },
    }, harness="codex", source_type="rollout", raw_event_id="poll", source_position="12"))
    interrupt = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "interrupt-one",
            "input": 'tools.write_stdin({session_id:88,chars:"\\u0003",yield_time_ms:1000})',
        },
    }, harness="codex", source_type="rollout", raw_event_id="interrupt", source_position="13"))

    assert poll.decision == "ignored_nonsemantic"
    assert poll.canonical_events == ()
    assert payloads(interrupt, OperationInputProvided)[0].payload.content.text == "\x03"


def test_codex_write_stdin_requires_a_known_process_session():
    with pytest.raises(TranslationError, match="unknown process session"):
        CodexCanonicalTranslator().translate(raw_event({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "write_stdin",
                "call_id": "input-one",
                "arguments": json.dumps({"session_id": 99, "chars": "hello"}),
            },
        }, harness="codex", source_type="rollout", raw_event_id="stdin"))


def test_codex_write_stdin_records_raw_and_canonical_audit(tmp_path):
    runtime, interpreter = interpreting_runtime(tmp_path / "events.db")
    runtime.register("codex", Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "session-one",
        "fixture.jsonl",
        "/work",
    ))
    observations = (
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "command-one",
                "input": 'tools.exec_command({"cmd":"read value"})',
            },
        }, harness="codex", source_type="rollout", raw_event_id="command", source_position="40"),
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "command-one",
                "output": json.dumps({"session_id": 77, "output": ""}),
            },
        }, harness="codex", source_type="rollout", raw_event_id="command-output", source_position="41"),
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "poll-one",
                "input": 'tools.write_stdin({session_id:77,chars:""})',
            },
        }, harness="codex", source_type="rollout", raw_event_id="poll", source_position="42"),
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "input-one",
                "input": 'tools.write_stdin({session_id:77,chars:"yes\\n"})',
            },
        }, harness="codex", source_type="rollout", raw_event_id="stdin", source_position="43"),
    )
    runtime.recorder.record(observations)
    interpreter.tick()

    stdin_evidence = EvidenceQueries(runtime.store).raw_event(RawEventId("stdin"))
    assert stdin_evidence is not None
    assert stdin_evidence.payload == observations[-1].payload
    assert stdin_evidence.decision == "translated"
    assert isinstance(stdin_evidence.canonical[0].event.payload, OperationInputProvided)
    poll_evidence = EvidenceQueries(runtime.store).raw_event(RawEventId("poll"))
    assert poll_evidence is not None
    assert poll_evidence.payload == observations[-2].payload
    assert poll_evidence.decision == "ignored_nonsemantic"
    assert poll_evidence.canonical == ()


def test_codex_plan_has_a_canonical_fact():
    plan = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "Plan", "id": "plan-one", "text": "1. Change it"},
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="plan",
    ))

    attention = payloads(plan, AttentionRequested)[0].payload
    assert attention.attention_type == "plan"
    assert attention.prompts[0].prompt == "1. Change it"


def test_codex_preliminary_patch_marker_is_nonsemantic():
    translation = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "call_id": "patch-one",
                "input": "*** Begin Patch",
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="patch-call",
    ))

    assert translation.decision == "ignored_nonsemantic"
    assert translation.canonical_events == ()


def test_codex_current_file_change_emits_the_shared_file_facts():
    translated = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "FileChange",
                    "id": "edit-one",
                    "status": "completed",
                    "changes": {
                        "/work/a.py": {
                            "type": "update",
                            "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
                            "move_path": None,
                        },
                        "/work/b.py": {
                            "type": "add",
                            "content": "print('captured')\n",
                        },
                    },
                },
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="file-change",
    ))

    files = [event.payload for event in payloads(translated, FileAccessed)]
    assert [(file.path, file.action) for file in files] == [
        ("/work/a.py", "updated"),
        ("/work/b.py", "created"),
    ]
    assert files[0].unified_diff == "@@ -1 +1 @@\n-old\n+new\n"
    assert files[1].content.text == "print('captured')\n"
    assert isinstance(translated.canonical_events[-1].payload, OperationFinished)


def test_codex_exec_wrapped_apply_patch_does_not_render_an_empty_tool_block():
    translated = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "patch-one",
                "input": 'text(await tools.apply_patch(patch));',
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="wrapped-patch-call",
    ))

    assert translated.decision.startswith("ignored_")
    assert translated.canonical_events == ()


def test_codex_apply_patch_wrapper_output_is_nonsemantic():
    translation = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "patch-one",
                "output": [
                    {"type": "input_text", "text": "Script completed\nOutput:\n"},
                    {"type": "input_text", "text": "{}"},
                ],
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="wrapped-patch-output",
    ))

    assert translation.decision.startswith("ignored_")
    assert translation.canonical_events == ()


def test_codex_collaboration_lifecycle_uses_child_turn_as_assignment_identity(tmp_path):
    rollout_path = tmp_path / "child.jsonl"
    rollout_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {
            "source": {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": "lead-one",
                        "agent_path": "/root/bali_weather",
                    }
                }
            }
        },
    }) + "\n")
    translator = CodexCanonicalTranslator()
    started_raw = replace(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "child-turn",
                "started_at": 1,
            },
        },
        harness="codex",
        source_type="child_rollout",
        raw_event_id="child-start",
    ), source_name=str(rollout_path), actor_id=ActorId("child-one"), parent_actor_id=ActorId("lead-one"))
    started = translator.translate(started_raw)
    child_raw = replace(
        raw_event(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "child-turn",
                    "completed_at": 2,
                    "last_agent_message": "Rain, 24°C",
                },
            },
            harness="codex",
            source_type="child_rollout",
            raw_event_id="child-finish",
        ),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("lead-one"),
    )
    finished = translator.translate(child_raw)

    start_payload = payloads(started, ActorAssignmentStarted)[0].payload
    finish_payload = payloads(finished, ActorAssignmentFinished)[0].payload
    assert str(start_payload.assignment_id) == "child-turn"
    assert start_payload.brief.text == "bali weather"
    assert str(finish_payload.assignment_id) == "child-turn"
    assert finish_payload.result.text == "Rain, 24°C"
    assert not payloads(finished, ActorFinished)
    hook_raw = replace(
        raw_event(
            {"hook_event_name": "SubagentStop", "agent_id": "child-one"},
            harness="codex",
            source_type="hook",
            raw_event_id="child-stop-hook",
        ),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("lead-one"),
    )
    hook_result = translator.translate(hook_raw)
    assert hook_result.canonical_events == ()
    assert hook_result.decision == "ignored_nonsemantic"


def test_codex_collaboration_controls_map_only_semantic_actor_facts(tmp_path):
    calls = [
        ("spawn", "spawn_agent", {"task_name": "weather", "message": "encrypted"}),
        ("send", "send_message", {"target": "/root/weather", "message": "encrypted"}),
        ("follow", "followup_task", {"target": "/root/weather", "message": "encrypted"}),
        ("wait", "wait_agent", {"timeout_ms": 1000}),
        ("interrupt", "interrupt_agent", {"target": "/root/weather"}),
        ("list", "list_agents", {}),
    ]
    rollout_path = tmp_path / "lead.jsonl"
    rollout_path.write_text("".join(json.dumps({
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": name,
            "arguments": json.dumps(arguments),
            "call_id": call_id,
        },
    }) + "\n" for call_id, name, arguments in calls))
    translator = CodexCanonicalTranslator()

    def translate(document, raw_id):
        event = replace(raw_event(
            document,
            harness="codex",
            source_type="rollout",
            raw_event_id=raw_id,
        ), source_name=str(rollout_path))
        return translator.translate(event)

    for call_id, name, arguments in calls:
        result = translate({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(arguments),
                "call_id": call_id,
            },
        }, f"{call_id}-call")
        assert result.canonical_events == ()
        assert result.decision == "ignored_nonsemantic"

    sent = translate({
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "turn_id": "lead-turn",
            "item": {
                "type": "SubAgentActivity",
                "id": "send",
                "kind": "interacted",
                "agent_thread_id": "child-one",
                "agent_path": "/root/weather",
            },
        },
    }, "send-activity")
    message = payloads(sent, ActorMessageSent)[0].payload
    assert message.recipient_actor_id == ActorId("child-one")
    assert message.content is None

    for call_id, activity in (
        ("spawn", "started"),
        ("follow", "interacted"),
        ("interrupt", "interrupted"),
    ):
        result = translate({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "lead-turn",
                "item": {
                    "type": "SubAgentActivity",
                    "id": call_id,
                    "kind": activity,
                    "agent_thread_id": "child-one",
                    "agent_path": "/root/weather",
                },
            },
        }, f"{call_id}-activity")
        assert result.canonical_events == ()
        assert result.decision == "ignored_nonsemantic"

    for call_id, output in (
        ("spawn", '{"task_name":"/root/weather"}'),
        ("wait", '{"message":"Wait timed out.","timed_out":true}'),
        ("interrupt", '{"previous_status":"running"}'),
        ("list", '{"agents":[]}'),
    ):
        result = translate({
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        }, f"{call_id}-output")
        assert result.canonical_events == ()
        assert result.decision == "ignored_nonsemantic"


def test_codex_actor_message_correlation_survives_translator_restart(tmp_path):
    call = json.dumps({
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "send_message",
            "arguments": json.dumps({"target": "/root/weather", "message": "encrypted"}),
            "call_id": "send-one",
        },
    }) + "\n"
    rollout_path = tmp_path / "lead.jsonl"
    rollout_path.write_text(call)
    activity = replace(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "turn_id": "lead-turn",
                "item": {
                    "type": "SubAgentActivity",
                    "id": "send-one",
                    "kind": "interacted",
                    "agent_thread_id": "child-one",
                    "agent_path": "/root/weather",
                },
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="send-activity",
        source_position=str(len(call.encode())),
    ), source_name=str(rollout_path))

    message = payloads(
        CodexCanonicalTranslator().translate(activity),
        ActorMessageSent,
    )[0].payload
    assert message.recipient_actor_id == ActorId("child-one")


def test_codex_child_abort_cancels_only_its_current_assignment():
    child_raw = replace(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "turn_aborted",
                "turn_id": "child-turn-two",
                "reason": "interrupted",
            },
        },
        harness="codex",
        source_type="child_rollout",
        raw_event_id="child-abort",
    ), actor_id=ActorId("child-one"), parent_actor_id=ActorId("lead-one"))

    assignment = payloads(
        CodexCanonicalTranslator().translate(child_raw),
        ActorAssignmentFinished,
    )[0].payload
    assert assignment.assignment_id == AssignmentId("child-turn-two")
    assert assignment.outcome == "cancelled"
    assert assignment.result is None
    assert assignment.reason == "interrupted"


def test_codex_web_tool_uses_shared_search_vocabulary():
    translation = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "web-one",
                "input": (
                    'const result = await tools.web__run('
                    '{"search_query":[{"q":"Bali weather"}]}); text(result);'
                ),
            },
        },
        harness="codex",
        source_type="rollout",
        raw_event_id="web-search",
    ))

    operation = payloads(translation, OperationStarted)[0].payload
    assert operation.category == "search"
    assert operation.native_name == "WebSearch"


def test_codex_unmapped_tool_fails_translation():
    with pytest.raises(TranslationError, match="unmapped Codex tool"):
        CodexCanonicalTranslator().translate(raw_event(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "unknown-one",
                    "input": "const result = await tools.unknown_tool({}); text(result);",
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="unknown-tool",
        ))


def test_codex_interrupt_detects_a_queued_turn_after_abort(tmp_path):
    rollout_path = tmp_path / "rollout.jsonl"
    rollout_path.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "turn_aborted"}})
        + "\n"
        + json.dumps({
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-two"},
        })
        + "\n"
    )

    assert _rollout_abort_state(str(rollout_path), 0) == (True, True)


def test_claude_hook_and_transcript_produce_identical_tool_start_facts():
    translator = ClaudeCanonicalTranslator()
    hook = translator.translate(
        raw_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_use_id": "tool-one",
                "tool_name": "Read",
                "tool_input": {"file_path": "/work/a.py"},
            },
            harness="claude_code",
            source_type="hook",
            raw_event_id="hook-start",
            observed_at=100.0,
        )
    )
    transcript = translator.translate(
        raw_event(
            {
                "type": "assistant",
                "uuid": "assistant-one",
                "message": {
                    "id": "api-message",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-one",
                            "name": "Read",
                            "input": {"file_path": "/work/a.py"},
                        }
                    ],
                },
            },
            harness="claude_code",
            source_type="transcript",
            raw_event_id="transcript-start",
            observed_at=200.0,
        )
    )
    codec = CanonicalEventCodec()
    assert codec.encode(payloads(hook, OperationStarted)[0]) == codec.encode(payloads(transcript, OperationStarted)[0])
    assert codec.encode(payloads(hook, FileAccessed)[0]) == codec.encode(payloads(transcript, FileAccessed)[0])


def test_claude_edit_completion_preserves_the_native_structured_patch():
    translated = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "edit-one",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/work/a.py", "old_string": "old", "new_string": "new"},
            "tool_response": {
                "structuredPatch": [{
                    "oldStart": 1,
                    "oldLines": 1,
                    "newStart": 1,
                    "newLines": 1,
                    "lines": ["-old", "+new"],
                }],
            },
        },
        harness="claude_code",
        source_type="hook",
        raw_event_id="edit-finish",
    ))

    file_event = payloads(translated, FileAccessed)[0].payload
    assert file_event.lines_added == 1
    assert file_event.lines_removed == 1
    assert file_event.unified_diff == (
        "--- /work/a.py\n+++ /work/a.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    )


def test_claude_hook_and_transcript_tool_finish_deduplicate_transactionally(tmp_path):
    translator = ClaudeCanonicalTranslator()
    hook_raw = raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "tool-one",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
            "tool_response": "output",
        },
        harness="claude_code",
        source_type="hook",
        raw_event_id="hook-finish",
    )
    transcript_raw = raw_event(
        {
            "type": "user",
            "uuid": "result-one",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tool-one", "content": "output"}]
            },
        },
        harness="claude_code",
        source_type="transcript",
        raw_event_id="transcript-finish",
    )
    hook = translator.translate(hook_raw)
    transcript = translator.translate(transcript_raw)
    hook_finished = payloads(hook, OperationFinished)[0]
    transcript_finished = payloads(transcript, OperationFinished)[0]
    assert CanonicalEventCodec().encode(hook_finished) == CanonicalEventCodec().encode(transcript_finished)

    store = CanonicalRuntime(str(tmp_path / "events.db"))
    store.register(
        "claude_code",
        Session(
            SessionId("session-one"),
            ActorId("session-one:lead"),
            "native",
            "fixture.jsonl",
            "/work",
        ),
    )
    store.record(hook_raw, "1", hook)
    accepted = store.record(transcript_raw, "1", transcript)
    assert hook_finished.event_id not in {stored.event.event_id for stored in accepted}
    stored = store.store.after(SessionId("session-one"), 0, 10).events
    finished = next(item for item in stored if item.event.event_id == hook_finished.event_id)
    assert RawEventId("transcript-finish") in finished.raw_event_ids


def test_claude_question_preserves_multiple_prompts_and_multiselect():
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_use_id": "question-one",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "id": "language",
                            "header": "Language",
                            "question": "Which languages?",
                            "multiSelect": True,
                            "options": [
                                {"label": "Python", "description": "Backend"},
                                {"label": "JavaScript", "description": "Browser"},
                            ],
                        },
                        {"id": "deploy", "question": "Deploy now?", "options": []},
                    ]
                },
            },
            harness="claude_code",
            source_type="hook",
            raw_event_id="ask",
        )
    )
    attention = payloads(translation, AttentionRequested)[0].payload
    assert len(attention.prompts) == 2
    assert attention.prompts[0].multiple is True
    assert [choice.label for choice in attention.prompts[0].choices] == ["Python", "JavaScript"]


def test_claude_question_resolution_is_canonical_not_a_native_response_object():
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_use_id": "question-one",
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "id": "language",
                            "question": "Which languages?",
                            "multiSelect": True,
                        }
                    ],
                    "answers": {"Which languages?": "Python, JavaScript"},
                },
                "tool_response": {"vendor_field": "not canonical"},
            },
            harness="claude_code",
            source_type="hook",
            raw_event_id="ask-answer",
        )
    )

    resolution = payloads(translation, AttentionResolved)[0].payload

    assert resolution.decision == "answered"
    assert resolution.answers[0].prompt_id == "language"
    assert resolution.answers[0].values == ("Python", "JavaScript")
    assert not hasattr(resolution, "tool_response")


def test_codex_session_turn_operation_usage_and_context_records():
    translator = CodexCanonicalTranslator()
    session = translator.translate(
        raw_event(
            {"type": "session_meta", "payload": {"cwd": "/work", "originator": "codex-tui"}},
            harness="codex",
            source_type="rollout",
            raw_event_id="session",
            source_position="0",
        )
    )
    turn = translator.translate(
        raw_event(
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-one"}},
            harness="codex",
            source_type="rollout",
            raw_event_id="turn",
        )
    )
    operation = translator.translate(
        raw_event(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-one",
                    "arguments": json.dumps({"cmd": "pwd"}),
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="operation",
        )
    )
    usage = translator.translate(
        raw_event(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 100, "output_tokens": 20},
                        "last_token_usage": {"total_tokens": 60},
                        "model_context_window": 200,
                    },
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="usage",
        )
    )
    assert isinstance(session.canonical_events[0].payload, SessionStarted)
    assert isinstance(session.canonical_events[1].payload, ActorStarted)
    assert isinstance(turn.canonical_events[0].payload, TurnStarted)
    assert isinstance(operation.canonical_events[0].payload, OperationStarted)
    assert len(payloads(usage, UsageReported)) == 1
    assert len(payloads(usage, ContextReported)) == 1


def test_codex_message_keeps_its_native_turn_identity():
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "message-one",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Finished"}],
                    "internal_chat_message_metadata_passthrough": {
                        "turn_id": "turn-one"
                    },
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="message",
        )
    )

    assert translation.canonical_events[0].turn_id == TurnId("turn-one")


def test_codex_source_can_attach_an_actor_to_another_harness_session():
    nested_raw_event = raw_event(
        {"type": "session_meta", "payload": {"cwd": "/work", "originator": "codex-exec"}},
        harness="codex",
        source_type="sidecar_rollout",
        raw_event_id="nested-session",
        source_position="0",
    )
    nested_raw_event = replace(
        nested_raw_event,
        actor_id=ActorId("codex-child"),
        parent_actor_id=ActorId("claude-lead"),
    )

    translation = CodexCanonicalTranslator().translate(nested_raw_event)

    assert len(translation.canonical_events) == 1
    assert isinstance(translation.canonical_events[0].payload, ActorStarted)
    assert translation.canonical_events[0].payload.role == "sidecar"
    assert translation.canonical_events[0].actor_id == ActorId("codex-child")
    assert translation.canonical_events[0].parent_actor_id == ActorId("claude-lead")


def test_codex_native_subagent_keeps_the_child_role():
    child_raw_event = raw_event(
        {"type": "session_meta", "payload": {"parent_thread_id": "codex-parent"}},
        harness="codex",
        source_type="child_rollout",
        raw_event_id="native-child",
        source_position="0",
    )
    child_raw_event = replace(
        child_raw_event,
        actor_id=ActorId("codex-child"),
        parent_actor_id=ActorId("codex-lead"),
    )

    translation = CodexCanonicalTranslator().translate(child_raw_event)

    assert isinstance(translation.canonical_events[0].payload, ActorStarted)
    assert translation.canonical_events[0].payload.role == "child"


def test_codex_question_uses_the_same_attention_prompt_model():
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "ask-one",
                    "arguments": json.dumps({
                        "questions": [{
                            "id": "choice",
                            "header": "Choice",
                            "question": "Continue?",
                            "options": [{"label": "Yes", "description": "Proceed"}],
                        }]
                    }),
                },
            },
            harness="codex",
            source_type="rollout",
            raw_event_id="ask",
        )
    )
    attention = payloads(translation, AttentionRequested)[0].payload
    assert attention.prompts[0].prompt == "Continue?"
    assert attention.prompts[0].choices[0].description == "Proceed"


# A `/command` turn is THREE user-shaped transcript records (measured, session
# 6a23d1c5): the isMeta caveat, the `<command-name>` envelope, and the command's
# echoed stdout. Two of them carried no structural flag, so each became its own
# `role="user"` message and one keystroke drew three blocks, two labelled "you".
CLAUDE_SLASH_COMMAND_TURN = (
    ("caveat", "<local-command-caveat>Caveat: The messages below were generated "
               "by the user while running local commands.</local-command-caveat>", True),
    ("envelope", "<command-name>/model</command-name>\n            "
                 "<command-message>model</command-message>\n            "
                 "<command-args>opus</command-args>", False),
    ("stdout", "<local-command-stdout>Set model to Opus 5 and saved as your "
               "default for new sessions</local-command-stdout>", False),
)


def _slash_turn_events():
    translator = ClaudeCanonicalTranslator()
    events = []
    for uuid, content, is_meta in CLAUDE_SLASH_COMMAND_TURN:
        document = {"type": "user", "uuid": uuid, "message": {"content": content}}
        if is_meta:
            document["isMeta"] = True
        events.extend(
            translator.translate(
                raw_event(
                    document,
                    harness="claude_code",
                    source_type="transcript",
                    raw_event_id="slash-" + uuid,
                )
            ).canonical_events
        )
    return events


def test_claude_slash_command_turn_is_one_prompt_not_three_blocks():
    messages = [e.payload for e in _slash_turn_events() if isinstance(e.payload, MessageCreated)]
    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].phase == "prompt"
    # what the human typed, not the envelope Claude Code stored it in
    assert messages[0].content.text == "/model opus"


def test_claude_slash_model_reports_the_selection_at_the_moment_it_was_made():
    models = [e.payload for e in _slash_turn_events() if isinstance(e.payload, ModelChanged)]
    assert len(models) == 1
    assert models[0].reason == "selected"
    # the transcript carries the ALIAS here; the native id arrives a turn later
    # on the next assistant record, as `reported_by_harness`
    assert models[0].current.selection_id == "opus"


def test_claude_slash_effort_reports_the_selection():
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {"type": "user", "uuid": "eff", "message": {"content":
                "<command-name>/effort</command-name><command-args>high</command-args>"}},
            harness="claude_code",
            source_type="transcript",
            raw_event_id="slash-effort",
        )
    )
    assert payloads(translation, EffortChanged)[0].payload.current == "high"
    assert payloads(translation, EffortChanged)[0].payload.reason == "selected"


def test_claude_argless_slash_command_settles_no_state():
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {"type": "user", "uuid": "bare", "message": {"content":
                "<command-name>/model</command-name>"}},
            harness="claude_code",
            source_type="transcript",
            raw_event_id="slash-bare",
        )
    )
    # a bare `/model` opens the picker and chooses nothing
    assert not payloads(translation, ModelChanged)
    assert payloads(translation, MessageCreated)[0].payload.content.text == "/model"


def test_claude_prompt_quoting_a_command_envelope_stays_a_prompt():
    # the anchor: a message ABOUT a slash command has prose in front of the tag
    for content in (
        "why is <command-name>/model</command-name> in my transcript?",
        "explain <local-command-stdout>Set model to Opus 5</local-command-stdout>",
    ):
        translation = ClaudeCanonicalTranslator().translate(
            raw_event(
                {"type": "user", "uuid": "quote", "message": {"content": content}},
                harness="claude_code",
                source_type="transcript",
                raw_event_id="quote-" + content[:6],
            )
        )
        message = payloads(translation, MessageCreated)[0].payload
        assert message.role == "user"
        assert message.content.text == content
        assert not payloads(translation, ModelChanged)
