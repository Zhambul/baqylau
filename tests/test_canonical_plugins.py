"""Cross-harness canonical translation tests from native fixture shapes."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from canonical_runtime import ProviderGraph
from audit.recorder import AuditRecorder
from repository.impl.sqlite.databases import audit_database
from repository.impl.sqlite.audit import SqliteAuditWriteRepository
from terminal.panes import commands as pane_commands
from harness.hooks.gateway import HookGatewayService, UnknownHookHarness
from harness.impl import installed
from engine.interpret.reactions import ShellOutputCanonicalEventReaction
from harness.models import (
    AnswerQuestion,
    AttachmentReference,
    ControlContext,
    ControlResult,
    HarnessHookRequest,
    InterruptRegistry,
    LIVENESS_SOURCE_TYPE,
    LaunchRequest,
    OUTPUT_LOCATION_SOURCE_TYPE,
    QueryContext,
    RawEvent,
    RenameSession,
    SelectModel,
    Session,
    TranslationError,
)
from fake_terminal import FakeSessions, FakeTerminal, window
from terminal.adapter import SessionPaneRequest, SessionTerminalResult, TerminalAdapter
from terminal.models import (
    ACTIVITY_PANE_TAG,
    SCOREBOARD_PANE_TAG,
    SESSION_WINDOW_TAG,
)
from core.daemon.contract import HOST_ADDRESS, PORT_NUMBER
from repository.mapper import facts as mapper
from domain.events import CanonicalEvent
from domain.events import (
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
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
    PlanProposed,
    PlanResolved,
    QuestionAnswered,
    QuestionAsked,
    ReasoningCreated,
    SearchPerformed,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    ShellBackgrounded,
    ShellFinished,
    ShellInputProvided,
    ShellOutputFinished,
    ShellProgressed,
    ShellStarted,
    SkillFinished,
    SkillStarted,
    TaskChanged,
    TaskListChanged,
    TurnFinished,
    TurnStarted,
    UsageReported,
    WebFetched,
    WorktreeChanged,
)
from domain.ids import (
    AccountId,
    ActorId,
    AssignmentId,
    CanonicalEventId,
    HarnessName,
    RawEventId,
    SessionId,
    ShellId,
    TaskId,
    TurnId,
    WindowId,
)
from domain.values import AccountReference, AttentionPrompt, ModelReference, ShellFollowUntil, TextContent
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.canonical.records import HookPayload, MessageUsage, SystemRecord
from harness.impl.claude_code.canonical.sources import (
    ClaudeRawEventSources,
    ClaudeTaskRawEventSource,
    ClaudeTranscriptRawEventSource,
)
from harness.impl.claude_code import account
from harness.impl.claude_code.hooks import gateway as claude_hooks
from harness.impl.claude_code.hooks import foreground as claude_foreground
from harness.impl.claude_code.controls import confirmdialog, tui as claude_tui
from harness.impl.claude_code.usage.rows import usage_reader as claude_usage_reader
from harness.impl.claude_code.usage import live as claude_live_usage
from harness.impl.claude_code.reactors import ClaudeOtelCanonicalEventReactor
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.impl.codex.canonical.sources import (
    CodexRawEventSources,
    CodexRolloutRawEventSource,
)
from harness.impl.codex.hooks import gateway as codex_hooks
from harness.impl.codex import usage as codex_usage
from harness.impl.codex.canonical import rollout as codex_rollout
from harness.impl.codex.canonical.records import SessionMetaPayload, TurnContextRecord
from harness.impl.codex.controls.controller import _rollout_abort_state
from harness.impl.codex.model import BaseInstructionsSourceType, CodexEffort, CodexModel
from harness.impl.claude_code.otel import gateway as claude_telemetry
from harness.models import HarnessTelemetryRequest
from repository.errors import EventIdentityConflict
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.usage import SqliteAccountUsageRepository
from canonical_runtime import CanonicalRuntime
from engine.interpret.translators import LivenessTranslator, ShellOutputTranslator
from engine.interpret.loop import Interpreter
from engine.interpret.reactions import (
    SessionUpsertCanonicalEventReaction,
)
from harness.services.launcher import HarnessLauncherService
from harness.services.telemetry import TelemetryGatewayService
from harness.registry import HarnessRegistry
from domain.events import ShellOutputLocated


class _QuietLiveness:
    """Liveness has its own contract tests; here it must not finish fixture sessions."""

    def __init__(self, session, probe):
        self.source_identity = f"test:liveness:{session.session_id}"

    def read(self, after_position):
        return ()


@pytest.fixture(autouse=True)
def quiet_liveness(monkeypatch):
    monkeypatch.setattr("engine.interpret.loop.SessionLivenessSource", _QuietLiveness)


def hook_request(
    payload: bytes,
    *,
    terminal_window_id: WindowId | None = None,
    harness_process_id: int | None = None,
    account_id: AccountId | None = None,
    account_display_name: str | None = None,
    launch_model: str | None = None,
    launch_effort: str | None = None,
    client_process_id: int | None = None,
) -> HarnessHookRequest:
    return HarnessHookRequest(
        payload=payload,
        terminal_window_id=terminal_window_id,
        harness_process_id=harness_process_id,
        account_id=account_id,
        account_display_name=account_display_name,
        launch_model=launch_model,
        launch_effort=launch_effort,
        client_process_id=client_process_id,
    )


def _deliver_hook(gateway, payload: bytes, **observed) -> bytes:
    """The daemon-side hook path minus HTTP: gateway → recorder.

    Mirrors HookGatewayService.record against the test's BAQYLAU_DATA_DIR."""
    response = gateway.handle(hook_request(payload, **observed))
    database_path = os.path.join(os.environ["BAQYLAU_DATA_DIR"], "main.db")
    SqliteRawEventRepository(main_database(database_path)).record(response.raw_events)
    return response.reply


def raw_event(
    document,
    *,
    harness: HarnessName,
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



def stored_payloads(runtime, session_id, payload_type):
    """Every fact of one kind a session accumulated, in the order it was accepted.

    Read straight off the canonical log, because that is what a PLUGIN test is
    about: the evidence became these facts. Folding them into something a reader
    sees belongs to the read model, and has its own tests.
    """
    return [
        item.payload
        for item in runtime.store.page_from(0, 100_000)
        if isinstance(item.payload, payload_type)
    ]


def shell_output_text(runtime, session_id, shell_id):
    """One command's output as the facts spell it, honouring append and replace."""
    text = ""
    for payload in stored_payloads(runtime, session_id, ShellProgressed):
        if payload.shell_id != shell_id or payload.stream != "output":
            continue
        text = text + payload.content.text if payload.mode == "append" else payload.content.text
    return text


def payloads(translation, payload_type):
    return [event for event in translation.canonical_events if isinstance(event.payload, payload_type)]


class RecordingControls:
    def __init__(self):
        self.executed = []

    def execute(self, request):
        self.executed.append(request)
        return ControlResult(request.request_id, "acknowledged")


def _committed(payload, *, parent_actor_id=None):
    return CanonicalEvent(
        CanonicalEventId(f"event-{type(payload).__name__}"),
        SessionId("session-one"),
        ActorId("session-one:lead"),
        None,
        parent_actor_id,
        "claude_code",
        10.0,
        None,
        None,
        payload,
    )


def test_claude_registers_no_automatic_account_migration_reactor():
    # A rate limit must not relaunch the CLI: the resumed run's
    # `session.started` deduplicates against the first run's, so the first
    # run's `session.finished` would keep the session out of `watchable()`.
    reactors = ProviderGraph().registry.plugin("claude_code").reactors

    assert [type(reactor).__name__ for reactor in reactors] == [
        "ClaudeOtelCanonicalEventReactor"
    ]


def test_claude_stop_failure_rate_limit_yields_the_usage_limited_goal_fact():
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {"hook_event_name": "StopFailure", "error": "rate_limit"},
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="stop-failure",
    ))
    unrelated = ClaudeCanonicalTranslator().translate(raw_event(
        {"hook_event_name": "StopFailure", "error": "network"},
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="stop-network",
    ))

    goals = payloads(translation, GoalChanged)
    assert [goal.payload.state for goal in goals] == ["usage_limited"]
    assert goals[0].payload.reason == "rate_limit"
    assert payloads(unrelated, GoalChanged) == []


def test_claude_reactor_starts_telemetry_on_session_start(monkeypatch):
    telemetry_starts = []
    monkeypatch.setattr(
        "harness.impl.claude_code.reactors.otel.start",
        lambda: telemetry_starts.append("started"),
    )
    reactor = ClaudeOtelCanonicalEventReactor()
    reactor.react(_committed(
        SessionStarted("/work", "/work/session.jsonl", None, None, None, None, None)
    ), RecordingControls())
    reactor.react(_committed(GoalChanged("Ship it", "active", None)), RecordingControls())

    assert telemetry_starts == ["started"]


def test_plugin_folder_descriptors_are_discovered_without_harness_branches():
    assert [plugin.info.name for plugin in installed()] == ["claude_code", "codex"]


def _silent_audit():
    """An audit recorder that writes to this test's own audit database."""
    return AuditRecorder(SqliteAuditWriteRepository(audit_database()))


def interpreting_runtime(database_path):
    """The real installed plugins wired to one database, with a silent terminal."""
    harnesses = HarnessRegistry()
    for plugin in installed():
        harnesses.register(plugin)
    harnesses.validate()
    runtime = CanonicalRuntime(str(database_path), harnesses=harnesses)
    interpreter = Interpreter(
        runtime.sessions,
        harnesses,
        runtime.recorder,
        runtime.shell_output,
        runtime.store,
        {
            OUTPUT_LOCATION_SOURCE_TYPE: ShellOutputTranslator(),
            LIVENESS_SOURCE_TYPE: LivenessTranslator(),
        },
        # The interpreter's own two, and nothing else: the pull phase reads the
        # rows they write. Everything a fact CAUSES rides the reaction loop.
        (
            SessionUpsertCanonicalEventReaction(runtime.sessions),
            ShellOutputCanonicalEventReaction(runtime.shell_output, runtime.recorder),
        ),
        _silent_audit(),
        InterruptRegistry(),
    )
    return runtime, interpreter


def test_file_sources_preserve_the_exact_complete_line(tmp_path):
    source_path = tmp_path / "source.jsonl"
    exact_line = b'{"type":"example"}\n'
    source_path.write_bytes(exact_line)
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
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
    (
        "source_actor_id",
        "source_parent_actor_id",
        "sender",
        "expected_actor_id",
        "expected_parent_actor_id",
        "starts_actor",
    ),
    [
        ("session-one:lead", None, "worker-one", "worker-one", ActorId("session-one:lead"), True),
        (
            "worker-one",
            ActorId("session-one:lead"),
            "session-one:lead",
            "session-one:lead",
            None,
            False,
        ),
        # The lead under its teammate-vocabulary ALIAS — the first record of every
        # teammate's transcript, its brief. Read literally it named an actor that
        # does not exist, so each session grew a phantom "team-lead" teammate that
        # never started work and never finished it (measured live: two agents
        # launched at once, one phantom, permanently running).
        (
            "worker-one",
            ActorId("session-one:lead"),
            "team-lead",
            "session-one:lead",
            None,
            False,
        ),
    ],
)
def test_claude_team_messages_preserve_the_native_sender_as_evidence_actor(
    tmp_path,
    source_actor_id,
    source_parent_actor_id,
    sender,
    expected_actor_id,
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
        str(source_path),
        "/work",
    )
    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "main.db")
    runtime.register("claude_code", session)
    context = replace(
        session.source_context,
        actor_id=ActorId(source_actor_id),
        parent_actor_id=source_parent_actor_id,
    )

    runtime.recorder.record(ClaudeTranscriptRawEventSource(context).read(None))
    interpreter.tick()

    audit = runtime.raw_event_audits.audits_for_session(session.session_id)[-1]
    assert audit.raw_event.actor_id == ActorId(expected_actor_id)
    assert audit.raw_event.parent_actor_id == expected_parent_actor_id
    assert audit.interpretation is not None
    assert all(
        item.event.actor_id == ActorId(expected_actor_id)
        for item in audit.interpretation.events
    )
    assert (
        any(
            isinstance(item.event.payload, ActorStarted)
            for item in audit.interpretation.events
        )
        is starts_actor
    )
    message = next(
        item.event.payload
        for item in audit.interpretation.events
        if isinstance(item.event.payload, MessageCreated)
    )
    assert message.role == "peer"


def test_claude_source_factory_includes_child_transcripts(tmp_path):
    parent_path = tmp_path / "projects" / "workspace" / "session-one.jsonl"
    child_path = tmp_path / "projects" / "workspace" / "session-one" / "subagents" / "agent-child-one.jsonl"
    child_path.parent.mkdir(parents=True)
    parent_path.write_text('{"type":"user","uuid":"parent"}\n')
    child_path.write_text('{"type":"user","uuid":"child"}\n')
    session = Session(
        session_id=SessionId("session-one"),
        lead_actor_id=ActorId("session-one:lead"),
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
        TaskId("1"), "Run tests", "Run the focused suite", "pending", ActorId("worker-one")
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
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CODEX,
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
        harness=HarnessName.CODEX,
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
    assert not payloads(plan, ShellStarted)


def test_codex_goal_state_is_strict_and_clear_removes_the_goal():
    translator = CodexCanonicalTranslator()
    cleared = translator.translate(raw_event(
        {"type": "event_msg", "payload": {"type": "thread_goal_cleared"}},
        harness=HarnessName.CODEX,
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
            harness=HarnessName.CODEX,
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
                "parent_thread_id": "parent-session",
                "timestamp": "2026-08-14T10:00:00Z",
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "parent replay"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "started_at": 1786701599},
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
    # The parent's replayed task_started (started_at BEFORE the fork) is prefix;
    # the child's OWN bootstrap task_started (started_at >= the fork) is the
    # first child-own record — classified as replay it eats the child's
    # turn/assignment start (session 01a00a31-3a90 painted no started card).
    assert raw_events[2].source_type == "sidecar_replay"
    assert raw_events[3].source_type == "sidecar_rollout"
    assert payloads(translator.translate(raw_events[3]), TurnStarted)
    assert raw_events[4].source_type == "sidecar_rollout"
    message = payloads(translator.translate(raw_events[4]), MessageCreated)[0]
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
                    "parent_thread_id": "parent-session",
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
        str(tmp_path / "not-a-codex-session.jsonl"),
        "/work",
    )
    factory = CodexRawEventSources()

    actors = [
        factory.for_session(session)[0].context.actor_id
        for _ in range(3)
    ]

    assert actors == [ActorId("child-one"), ActorId("child-two"), ActorId("child-one")]


def test_codex_session_start_announces_only_a_lead_rollout(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    lead_path = tmp_path / "sessions" / "2026" / "08" / "15" / "rollout-2026-08-15T10-00-00-lead-one.jsonl"
    lead_path.parent.mkdir(parents=True)
    lead_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {
            "id": "lead-one",
            "cwd": "/work",
            "thread_source": "user",
            "base_instructions": {
                "text": "You are Codex.",
                "provenance": {"type": "model", "model": "gpt-5.6-luna"},
            },
        },
    }) + "\n")
    child_path = lead_path.with_name("rollout-2026-08-15T10-00-01-child-one.jsonl")
    child_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {"thread_source": "subagent", "parent_thread_id": "lead-one"},
    }) + "\n")

    def hook_raw(session_id, path):
        return replace(raw_event(
            {"session_id": session_id, "transcript_path": str(path), "cwd": "/work",
             "hook_event_name": "SessionStart"},
            harness=HarnessName.CODEX,
            source_type="hook",
            raw_event_id=f"hook-{session_id}",
        ), session_id=SessionId(session_id))

    translator = CodexCanonicalTranslator()
    lead = translator.translate(hook_raw("lead-one", lead_path))
    child = translator.translate(hook_raw("child-one", child_path))

    started = payloads(lead, SessionStarted)
    assert len(started) == 1
    assert started[0].payload.source_reference == str(lead_path.resolve())
    assert child.decision == "ignored_nonsemantic"
    assert child.canonical_events == ()


def test_codex_0149_base_instruction_source_is_a_closed_vocabulary():
    metadata = SessionMetaPayload.model_validate({
        "base_instructions": {
            "text": "You are Codex.",
            "provenance": {"type": "model", "model": "gpt-5.6-luna"},
        },
    })

    assert metadata.base_instructions is not None
    assert metadata.base_instructions.source is not None
    assert metadata.base_instructions.source.type is BaseInstructionsSourceType.MODEL
    assert metadata.base_instructions.source.model is CodexModel.GPT_5_6_LUNA


def test_codex_turn_context_model_and_effort_are_closed_vocabularies():
    record = codex_rollout.parse({
        "type": "turn_context",
        "payload": {"model": "gpt-5.6-luna", "effort": "low"},
    })

    assert isinstance(record, TurnContextRecord)
    assert record.model is CodexModel.GPT_5_6_LUNA
    assert record.effort is CodexEffort.LOW


@pytest.mark.parametrize(
    ("field", "value"),
    (("model", "gpt-codex-next"), ("effort", "extreme")),
)
def test_codex_unknown_turn_context_selection_is_contract_drift(field, value):
    payload = {"model": "gpt-5.6-luna", "effort": "low"}
    payload[field] = value

    with pytest.raises(ValidationError, match=field):
        codex_rollout.parse({"type": "turn_context", "payload": payload})


@pytest.mark.parametrize(
    ("field", "value"),
    (("type", "configuration"), ("model", "gpt-codex-next")),
)
def test_codex_unknown_base_instruction_source_value_is_contract_drift(field, value):
    source = {"type": "model", "model": "gpt-5.6-luna"}
    source[field] = value

    with pytest.raises(ValidationError, match=field):
        SessionMetaPayload.model_validate({
            "base_instructions": {
                "text": "You are Codex.",
                "provenance": source,
            },
        })


def test_codex_source_factory_waits_for_native_child_boundary(tmp_path, monkeypatch):
    child_path = tmp_path / "sessions" / "2026" / "08" / "14" / "rollout-2026-08-14T10-00-00-child-one.jsonl"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(json.dumps({
        "type": "session_meta",
        "timestamp": "2026-08-14T10:00:00Z",
        "payload": {
            "thread_source": "subagent",
            "parent_thread_id": "parent-session",
        },
    }) + "\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    session = Session(
        SessionId("parent-session"),
        ActorId("parent-session:lead"),
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
        str(tmp_path / "not-a-codex-session.jsonl"),
        "/work",
    )

    assert CodexRawEventSources().for_session(session) == ()
    assert codex_rollout.subagent_fork_epoch(str(rollout_path)) is None


def test_hooks_record_exact_raw_bytes_and_both_sessions_are_born_from_them(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    rollout_path = (
        tmp_path / "codex-home" / "sessions" / "2026" / "08" / "15"
        / "rollout-2026-08-15T10-00-00-codex-session.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {"id": "codex-session", "cwd": "/work", "thread_source": "user"},
    }) + "\n")
    claude_payload = (
        b'{ "session_id": "claude-session", "transcript_path": "/work/claude.jsonl", '
        b'"cwd": "/work", "hook_event_name": "SessionStart" }'
    )
    codex_payload = json.dumps({
        "session_id": "codex-session",
        "transcript_path": str(rollout_path),
        "cwd": "/work",
        "hook_event_name": "SessionStart",
    }).encode()

    _deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        claude_payload,
        terminal_window_id="window-1",
        harness_process_id=4242,
        account_id="c2",
        account_display_name="Account Two",
    )
    _deliver_hook(
        codex_hooks.CodexHookGateway(),
        codex_payload,
        terminal_window_id="window-2",
        harness_process_id=4343,
    )

    runtime, interpreter = interpreting_runtime(tmp_path / "main.db")
    interpreter.tick()

    claude_session = runtime.sessions.find(SessionId("claude-session"))
    assert claude_session is not None
    assert claude_session.source_reference == str(Path("/work/claude.jsonl").resolve())
    assert claude_session.terminal_window_id == "window-1"
    assert claude_session.harness_process_id == 4242
    codex_session = runtime.sessions.find(SessionId("codex-session"))
    assert codex_session is not None
    assert codex_session.source_reference == str(rollout_path.resolve())
    assert codex_session.terminal_window_id == "window-2"

    claude_audits = runtime.raw_event_audits.audits_for_session(SessionId("claude-session"))
    codex_audits = runtime.raw_event_audits.audits_for_session(SessionId("codex-session"))
    assert claude_audits[0].raw_event.payload == claude_payload
    assert claude_audits[0].interpretation is not None
    # session.started + actor.started + session.account_changed (the header)
    assert len(claude_audits[0].interpretation.events) == 3
    account_changed = [
        item.event.payload
        for item in claude_audits[0].interpretation.events
        if isinstance(item.event.payload, SessionAccountChanged)
    ]
    assert account_changed[0].account == AccountReference("c2", "Account Two")
    assert codex_audits[0].raw_event.payload == codex_payload
    assert codex_audits[0].interpretation is not None
    assert codex_audits[0].interpretation.decision == "translated"
    assert len(codex_audits[0].interpretation.events) == 2


def test_claude_launch_selections_reach_the_summary_from_the_hook_environment(monkeypatch, tmp_path):
    # a dashboard launch exports BAQYLAU_LAUNCH_MODEL/EFFORT on the CLI; the hook
    # observes them and the gateway records ONE launch raw event on SessionStart —
    # the only evidence source for them (Claude Code never echoes the effort, and
    # reports the model only on its first assistant record)
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    start_payload = json.dumps({
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "cwd": "/work",
        "hook_event_name": "SessionStart",
        "hook_event_id": "start-1",
    }).encode()
    stop_payload = json.dumps({
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "hook_event_name": "Stop",
        "hook_event_id": "stop-1",
    }).encode()

    _deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        start_payload,
        launch_model="fable",
        launch_effort="high",
    )
    # a later delivery still carries the inherited environment but is not a launch
    _deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        stop_payload,
        launch_model="fable",
        launch_effort="high",
    )

    runtime, interpreter = interpreting_runtime(tmp_path / "main.db")
    interpreter.tick()

    launch_evidence = [
        item
        for item in runtime.raw_event_audits.audits_for_session(SessionId("claude-session"))
        if item.raw_event.source_type == "launch"
    ]
    assert len(launch_evidence) == 1
    assert launch_evidence[0].interpretation is not None
    model_changes = [
        item.event.payload
        for item in launch_evidence[0].interpretation.events
        if isinstance(item.event.payload, ModelChanged)
    ]
    # the environment carries the selection ALIAS; the native id arrives later,
    # on the first assistant record, as `reported_by_harness`
    assert model_changes[0].reason == "selected"
    assert model_changes[0].current.name == "fable"

    stored_models = stored_payloads(runtime, SessionId("claude-session"), ModelChanged)
    stored_efforts = stored_payloads(runtime, SessionId("claude-session"), EffortChanged)
    assert stored_models[0].current.name == "fable"
    assert stored_efforts[0].current == "high"


def test_hook_without_native_identity_uses_the_exact_payload_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    payload = (
        b'{"session_id":"claude-session","transcript_path":"/work/claude.jsonl",'
        b'"cwd":"/work","hook_event_name":"SessionStart"}'
    )

    _deliver_hook(claude_hooks.ClaudeHookGateway(), payload)
    _deliver_hook(claude_hooks.ClaudeHookGateway(), payload)

    runtime = CanonicalRuntime(str(tmp_path / "main.db"))
    evidence = runtime.raw_event_audits.audits_for_session(SessionId("claude-session"))
    assert len(evidence) == 1
    assert str(evidence[0].raw_event.raw_event_id).endswith(hashlib.sha256(payload).hexdigest())


def test_hook_recording_preserves_native_child_actor_context(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    payload = json.dumps({
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "hook_event_name": "SubagentStart",
        "hook_event_id": "child-start",
        "agent_id": "child-one",
    }).encode()

    _deliver_hook(claude_hooks.ClaudeHookGateway(), payload)

    runtime, interpreter = interpreting_runtime(tmp_path / "main.db")
    interpreter.tick()
    evidence = runtime.raw_event_audits.audits_for_session(SessionId("claude-session"))[0]
    assert evidence.raw_event.actor_id == ActorId("child-one")
    assert evidence.raw_event.parent_actor_id == ActorId("claude-session:lead")
    assert evidence.interpretation is not None
    assert evidence.interpretation.events[0].event.actor_id == ActorId("child-one")


def test_claude_hook_returns_native_pretool_output_and_an_output_location(monkeypatch):
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
    located = ShellOutputLocated(
        shell_id=ShellId("pretool-one"),
        source_path="/work/out",
        chunk_source_type="foreground_output",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=ShellFollowUntil.SESSION_FINISHED,
    )
    monkeypatch.setattr(
        claude_hooks.foreground,
        "prepare",
        lambda value: SimpleNamespace(reply=expected, located=located),
    )

    response = claude_hooks.ClaudeHookGateway().handle(
        hook_request(json.dumps(document).encode())
    )

    assert response.reply == expected
    assert response.raw_events[0].payload == json.dumps(document).encode()
    directive = response.raw_events[-1]
    assert directive.source_type == "output_location"
    body = json.loads(directive.payload)
    assert body["source_path"] == "/work/out"
    assert body["until"] == ShellFollowUntil.SESSION_FINISHED


def test_the_hook_row_carries_what_the_delivery_observed():
    payload = json.dumps({
        "session_id": "claude-session",
        "transcript_path": "/work/claude.jsonl",
        "cwd": "/work",
        "hook_event_name": "PostToolUse",
        "hook_event_id": "post-one",
        "tool_name": "Read",
    }).encode()

    response = claude_hooks.ClaudeHookGateway().handle(hook_request(
        payload,
        terminal_window_id="1114",
        harness_process_id=4242,
        account_id="c2",
        account_display_name="Account Two",
    ))
    bare = claude_hooks.ClaudeHookGateway().handle(hook_request(payload))

    hook_row = response.raw_events[0]
    assert hook_row.terminal_window_id == "1114"
    assert hook_row.harness_process_id == 4242
    assert hook_row.account_id == "c2"
    assert hook_row.account_display_name == "Account Two"
    assert bare.raw_events[0].terminal_window_id is None
    # the flat fields ride the SAME row: no separate anchor observations exist
    assert [event.source_type for event in response.raw_events] == ["hook"]


class SubmitProbeDriver:
    """A driver whose input box keeps the text for `sticky` Enter presses."""

    def __init__(self, sticky):
        self.sticky = sticky
        self.enters = 0
        self.box = ""

    terminal = None  # the probe is monkeypatched; only the attribute must exist

    def paste_text(self, window_id, text):
        self.box = text
        return True

    def send_key(self, window_id, *keys):
        self.enters += 1
        if self.enters >= self.sticky:
            self.box = ""
        return True


def test_type_command_verifies_the_submit_and_retries_the_enter(monkeypatch):
    monkeypatch.setattr(claude_tui.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(claude_tui.clipboard_image, "clear_image", lambda: False)
    monkeypatch.setattr(
        claude_tui,
        "_submission_pending",
        lambda fe, win, marker: marker in fe.box,
    )

    retried = SubmitProbeDriver(sticky=1)
    ok, _clip = claude_tui.type_command(retried, "window-1", "hello from the dashboard")
    assert ok is True
    assert retried.enters == 1

    stuck = SubmitProbeDriver(sticky=99)
    ok, _clip = claude_tui.type_command(stuck, "window-1", "hello from the dashboard")
    assert ok is False
    assert stuck.enters == 2  # every retry spent before giving up honestly


def test_claude_foreground_post_tool_records_no_directive():
    """The foreground following ends with the committed operation.finished fact,
    not with a directive from the PostToolUse delivery."""
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

    response = claude_hooks.ClaudeHookGateway().handle(
        hook_request(json.dumps(document).encode())
    )

    assert response.reply == b""
    assert [event.source_type for event in response.raw_events] == ["hook"]


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


def test_claude_background_bash_locates_its_native_output_file(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path))
    output_path, document = _background_post_tool_document(tmp_path)

    response = claude_hooks.ClaudeHookGateway().handle(
        hook_request(json.dumps(document).encode())
    )

    assert response.reply == b""
    directive = response.raw_events[-1]
    assert directive.source_type == "output_location"
    body = json.loads(directive.payload)
    assert body["shell_id"] == "background-op-one"
    assert body["source_path"] == str(output_path.resolve())
    assert body["delete_source"] is False
    # a background launch reports "finished" while output keeps flowing, so the
    # following must outlive the operation and end with the session
    assert body["until"] == "session_finished"


def test_claude_background_output_requires_the_native_task_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path))
    _output_path, document = _background_post_tool_document(tmp_path)

    foreground_document = json.loads(json.dumps(document))
    foreground_document["tool_input"].pop("run_in_background")
    assert claude_foreground.background_output(HookPayload.model_validate(foreground_document)) is None

    missing_task = json.loads(json.dumps(document))
    missing_task["tool_response"].pop("backgroundTaskId")
    assert claude_foreground.background_output(HookPayload.model_validate(missing_task)) is None

    no_file_yet = json.loads(json.dumps(document))
    no_file_yet["tool_response"]["backgroundTaskId"] = "btk-without-a-file"
    assert claude_foreground.background_output(HookPayload.model_validate(no_file_yet)) is None


def test_claude_background_output_streams_into_the_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path / "native"))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    output_path, document = _background_post_tool_document(tmp_path / "native", session_id="session-one")
    document["transcript_path"] = str(tmp_path / "session-one.jsonl")

    _deliver_hook(claude_hooks.ClaudeHookGateway(), json.dumps(document).encode())

    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "main.db")
    runtime.register("claude_code", Session(
        SessionId("session-one"), ActorId("session-one:lead"),
        str(tmp_path / "session-one.jsonl"), "/work",
    ))
    interpreter.tick()  # translates the directive; the reaction starts the following
    output_path.write_bytes(b"1\n2\n3\n")  # the job keeps writing
    interpreter.tick()  # pulls chunks
    interpreter.tick()  # translates them

    assert shell_output_text(
        runtime, SessionId("session-one"), ShellId("background-op-one")
    ) == "1\n2\n3\n"

    # the session's end is the background following's end: tail captured, row
    # gone, the NATIVE file untouched
    output_path.write_bytes(b"1\n2\n3\n4\n")
    finish = CanonicalEvent(
        CanonicalEventId("session-finish"),
        SessionId("session-one"),
        ActorId("session-one:lead"),
        None,
        None,
        "claude_code",
        30.0,
        None,
        None,
        SessionFinished("succeeded", None),
    )
    ShellOutputCanonicalEventReaction(runtime.shell_output, runtime.recorder).react(finish)
    assert runtime.shell_output.find_for_session(SessionId("session-one")) == ()
    assert output_path.exists()
    interpreter.tick()
    assert shell_output_text(
        runtime, SessionId("session-one"), ShellId("background-op-one")
    ) == "1\n2\n3\n4\n"


def test_a_command_backgrounded_mid_run_keeps_its_output_file_and_its_following(monkeypatch, tmp_path):
    """The whole point of the fact, end to end through the real interpreter.

    A foreground command is followed `until="operation_finished"`, and ctrl+b makes
    that finish arrive while the command runs on. Unhandled, the next tick drained
    the row, removed it, and UNLINKED the tee file the process was still writing
    to — output gone, and no exception anywhere to notice it by.
    """
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    session_id, shell_id = "session-one", "op-one"
    transcript_path = str(tmp_path / "session-one.jsonl")
    hook = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": "/work",
        "hook_event_name": "PreToolUse",
        "hook_event_id": "pretool-one",
        "tool_name": "Bash",
        "tool_use_id": shell_id,
        "tool_input": {"command": "sleep 30; echo done"},
    }
    gateway = claude_hooks.ClaudeHookGateway()
    _deliver_hook(gateway, json.dumps(hook).encode())

    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "main.db")
    runtime.register("claude_code", Session(
        SessionId(session_id), ActorId(f"{session_id}:lead"), transcript_path, "/work",
    ))
    interpreter.tick()                                   # the directive starts the following
    following = runtime.shell_output.find_for_session(SessionId(session_id))
    assert len(following) == 1
    tee_path = following[0].source_path
    assert following[0].until == "shell_finished"        # …as a foreground command
    Path(tee_path).write_bytes(b"working\n")
    interpreter.tick()
    interpreter.tick()

    # ctrl+b: the input never asked for the background, the response carries a task id
    _deliver_hook(gateway, json.dumps({
        **hook,
        "hook_event_name": "PostToolUse",
        "hook_event_id": "posttool-one",
        "tool_response": {"backgroundTaskId": "btk9y72c9"},
    }).encode())
    interpreter.tick()

    survived = runtime.shell_output.find_for_session(SessionId(session_id))
    assert len(survived) == 1, "the following was ended by the launch's finish"
    assert survived[0].until == "session_finished"
    assert Path(tee_path).exists(), "the file the command is still writing to was unlinked"

    Path(tee_path).write_bytes(b"working\ndone\n")        # the command runs on
    interpreter.tick()
    interpreter.tick()
    assert shell_output_text(runtime, SessionId(session_id), ShellId(shell_id)).endswith("done\n")
    # The command moved, and said so before it said finished — so nothing in the
    # facts claims it ended.
    backgrounded = stored_payloads(runtime, SessionId(session_id), ShellBackgrounded)
    assert [fact.shell_id for fact in backgrounded] == [ShellId(shell_id)]
    assert not stored_payloads(runtime, SessionId(session_id), ShellOutputFinished)


def test_claude_foreground_output_is_canonical_append_progress():
    content = b"first line\nsecond line\n"
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "shell_id": "command-one",
            "ordinal": 3,
            "stream": "output",
            "content_base64": base64.b64encode(content).decode("ascii"),
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="foreground_output",
        raw_event_id="foreground-one",
    ))

    progress = payloads(translation, ShellProgressed)[0].payload
    assert progress.shell_id == "command-one"
    assert progress.ordinal == 3
    assert progress.mode == "append"
    assert progress.content.text == content.decode()


def test_claude_background_launch_stub_is_not_progress(tmp_path):
    """The 'Command running in background with ID …' tool_result is boilerplate,
    and its REPLACE mode wiped watch chunks that committed first. The finish
    fact still converges from the hook evidence."""
    stub = (
        "Command running in background with ID: btk9y72c9. Output is being "
        "written to: /tmp/task.output. You will be notified when it completes."
    )
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "user",
            "uuid": "background-result",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "background-op", "content": stub}]
            },
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="background-stub",
    ))

    assert not payloads(translation, ShellProgressed)
    assert translation.decision == "ignored_nonsemantic"


def test_claude_foreground_prepare_rewrites_the_command_into_an_output_location(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    document = {
        "session_id": "session-one",
        "agent_id": "child-one",
        "cwd": str(tmp_path),
        "tool_use_id": "command-one",
        "tool_input": {"command": "printf hello"},
    }

    prepared = claude_foreground.prepare(HookPayload.model_validate(document))

    assert prepared is not None
    native_output = json.loads(prepared.reply)
    updated_command = native_output["hookSpecificOutput"]["updatedInput"]["command"]
    assert native_output["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "tee -a" in updated_command
    assert prepared.located.shell_id == "command-one"
    assert prepared.located.delete_source is True
    assert prepared.located.until == "shell_finished"
    assert prepared.located.chunk_source_type == "foreground_output"
    assert prepared.located.source_path in updated_command


def test_claude_foreground_bytes_flow_through_raw_audit_into_operation_projection(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "application"))
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

    output = _deliver_hook(claude_hooks.ClaudeHookGateway(), json.dumps(document).encode())
    assert b"updatedInput" in output

    runtime, interpreter = interpreting_runtime(tmp_path / "application" / "main.db")
    runtime.register("claude_code", Session(
        SessionId("session-one"), ActorId("session-one:lead"),
        str(tmp_path / "session-one.jsonl"), str(tmp_path),
    ))
    interpreter.tick()  # translates the directive; the reaction starts the following
    output_sources = runtime.shell_output.find_for_session(SessionId("session-one"))
    assert len(output_sources) == 1
    Path(output_sources[0].source_path).write_bytes(b"hello\n")
    interpreter.tick()  # pulls the chunk and translates it

    assert shell_output_text(
        runtime, SessionId("session-one"), ShellId("command-one")
    ) == "hello\n"
    evidence = runtime.raw_event_audits.audits_for_session(SessionId("session-one"))
    foreground_evidence = [
        row for row in evidence if row.raw_event.source_type == "foreground_output"
    ]
    assert len(foreground_evidence) == 1
    assert base64.b64decode(
        json.loads(foreground_evidence[0].raw_event.payload)["content_base64"]
    ) == b"hello\n"


def pane_terminal():
    """A terminal showing one session window, and the adapter over it."""
    terminal = FakeTerminal(
        windows=[window("window-one", tags={})],
        current_window="window-one",
    )
    sessions = FakeSessions({"session-one": "window-one"})
    return terminal, TerminalAdapter(terminal.plugin(), sessions)


def test_terminal_adapter_opens_canonical_processes_with_generic_tags():
    terminal, adapter = pane_terminal()

    result = adapter.open_session_panes(
        SessionPaneRequest(SessionId("session-one"), "window-one", 25)
    )

    assert result.succeeded
    assert terminal.tagged == [("window-one", {SESSION_WINDOW_TAG: "session-one"})]
    assert [dict(request.tags) for request in terminal.opened_panes] == [
        {ACTIVITY_PANE_TAG: "session-one"},
        {SCOREBOARD_PANE_TAG: "session-one"},
    ]
    # One client program, told where the daemon listens and which stream to open:
    # a pane imports nothing of ours, so everything it cannot observe is argv.
    pane_client = str(Path(__file__).parents[1] / "client" / "terminal_pane.py")
    assert [request.command[1:] for request in terminal.opened_panes] == [
        (pane_client, HOST_ADDRESS, str(PORT_NUMBER), "session-one", "mirror"),
        (pane_client, HOST_ADDRESS, str(PORT_NUMBER), "session-one", "scoreboard"),
    ]
    # the anchor is stated as intent, never as one terminal's match syntax
    assert terminal.opened_panes[0].anchor.window_id == "window-one"
    assert terminal.opened_panes[1].anchor.tag == (ACTIVITY_PANE_TAG, "session-one")
    assert terminal.focused == ["window-one"]


def test_a_pane_process_that_exits_on_startup_is_named_not_guessed_at():
    """A launch is not a pane until the pane is still there.

    Measured (session 11b25475, 2026-08-17): the pane processes died on their
    first import, every time. kitty had made the window, so `open_pane` reported
    success; the window vanished with the process, and the composite failed with
    "scoreboard pane is not open" — a symptom of the symptom. Now the reason names
    the thing that happened.
    """
    terminal = FakeTerminal(
        windows=[window("window-one", tags={})],
        current_window="window-one",
        pane_processes_die=True,
    )
    adapter = TerminalAdapter(terminal.plugin(), FakeSessions({"session-one": "window-one"}))

    result = adapter.open_session_panes(
        SessionPaneRequest(SessionId("session-one"), "window-one", 25)
    )

    assert not result.succeeded
    assert result.reason == "mirror pane process exited on startup"


def test_terminal_adapter_settles_the_scoreboard_on_its_five_rows():
    terminal, adapter = pane_terminal()

    adapter.open_session_panes(SessionPaneRequest(SessionId("session-one"), "window-one", 25))

    scoreboard = next(found for found in terminal.windows()
                      if found.tags.get(SCOREBOARD_PANE_TAG))
    assert scoreboard.lines == 5
    assert terminal.resized == [(scoreboard.window_id, "vertical", 2)]


def test_terminal_adapter_leaves_panes_it_finds_already_open():
    terminal, adapter = pane_terminal()
    adapter.open_session_panes(SessionPaneRequest(SessionId("session-one"), "window-one", 25))
    terminal.opened_panes.clear()

    adapter.open_session_panes(SessionPaneRequest(SessionId("session-one"), "window-one", 25))

    assert terminal.opened_panes == []


def test_terminal_adapter_close_removes_the_session_window_tag():
    terminal, adapter = pane_terminal()
    adapter.open_session_panes(SessionPaneRequest(SessionId("session-one"), "window-one", 25))
    terminal.tagged.clear()

    result = adapter.close_session_panes(SessionId("session-one"))

    assert result.succeeded
    assert terminal.tagged == [("window-one", {SESSION_WINDOW_TAG: ""})]
    assert terminal.cleared == ["window-one"]
    assert not [found for found in terminal.windows() if found.tags.get(ACTIVITY_PANE_TAG)]


def test_terminal_adapter_resolves_the_active_tab_without_window_environment():
    terminal = FakeTerminal(
        windows=[window(41, tags={SESSION_WINDOW_TAG: "session-one"})],
        current_window=None,
    )
    adapter = TerminalAdapter(terminal.plugin(), FakeSessions())

    assert adapter.current_session() == SessionId("session-one")


def test_terminal_adapter_reads_the_session_window_from_evidence_and_checks_it_lives():
    terminal = FakeTerminal(windows=[window("window-one")])
    sessions = FakeSessions({"session-one": "window-one", "session-two": "window-gone"})
    adapter = TerminalAdapter(terminal.plugin(), sessions)

    assert adapter.window_for_session(SessionId("session-one")) == "window-one"
    # the row outlived its window: a session id alone is not liveness
    assert adapter.window_for_session(SessionId("session-two")) is None
    assert adapter.window_for_session(SessionId("session-missing")) is None


def test_terminal_adapter_measures_the_activity_pane_against_its_row():
    terminal = FakeTerminal(windows=[
        window("window-one", columns=75),
        window("window-two", tags={ACTIVITY_PANE_TAG: "session-one"},
               columns=25, is_first_in_tab=False),
        # stacked INSIDE the activity pane's column — counting it would count
        # that column twice
        window("window-three", tags={SCOREBOARD_PANE_TAG: "session-one"},
               columns=25, lines=5, is_first_in_tab=False),
    ])
    adapter = TerminalAdapter(terminal.plugin(), FakeSessions())

    assert adapter.activity_pane_geometry(SessionId("session-one")) == (25, 100)

    adapter.set_activity_pane_width(SessionId("session-one"), 40)
    assert terminal.resized == [("window-two", "horizontal", 15)]


# Four tests lived here: the keybinding's body, its handling of a refusal, and the
# hook client's headers and swallowing. All four are in
# tests/test_canonical_clients.py now, where they RUN the process instead of
# monkeypatching a function inside it — which is the difference between a test that
# would have caught the pane outage and one that could not.


def test_hook_gateway_service_records_only_for_harnesses_that_accept_deliveries(tmp_path):
    registry = HarnessRegistry()
    for plugin in installed():
        registry.register(plugin if plugin.info.name != "codex" else replace(plugin, hooks=None))
    service = HookGatewayService(registry, SqliteRawEventRepository(main_database(str(tmp_path / "main.db"))))
    payload = json.dumps({
        "session_id": "session-one",
        "transcript_path": "/work/session.jsonl",
        "hook_event_name": "PostToolUse",
        "hook_event_id": "post-one",
        "tool_name": "Read",
    }).encode()

    assert service.record("claude_code", hook_request(payload, terminal_window_id=WindowId("9"))) == b""
    evidence = CanonicalRuntime(str(tmp_path / "main.db")).raw_event_audits.audits_for_session(
        SessionId("session-one")
    )
    assert [audit.raw_event.source_type for audit in evidence] == ["hook"]

    with pytest.raises(UnknownHookHarness, match="unregistered harness"):
        service.record("mystery", hook_request(payload))
    with pytest.raises(UnknownHookHarness, match="accepts no hook deliveries"):
        service.record("codex", hook_request(payload))


def test_the_cli_pid_is_resolved_from_the_pid_its_client_reported(tmp_path, monkeypatch):
    """A client observes; the daemon interprets.

    A hook process used to walk its own ancestry with `ps` — up to 32 forks in a
    process the harness is waiting on — to name the CLI, which took the harness's
    own process name and so an import of its plugin. It reports its own pid
    instead, and the walk happens here, where the process name is already known
    and where the chain is still alive: the CLI is blocked on this delivery.
    """
    walked = []

    def ancestry(process_name, from_process_id=None):
        walked.append((process_name, from_process_id))
        return 4242

    monkeypatch.setattr("harness.hooks.gateway.nearest_ancestor_named", ancestry)
    registry = HarnessRegistry()
    for plugin in installed():
        registry.register(plugin)
    service = HookGatewayService(
        registry, SqliteRawEventRepository(main_database(str(tmp_path / "main.db")))
    )
    payload = json.dumps({
        "session_id": "session-one",
        "transcript_path": "/work/session.jsonl",
        "hook_event_name": "SessionStart",
        "hook_event_id": "start-one",
    }).encode()

    service.record("claude_code", hook_request(payload, client_process_id=999))

    assert walked == [("claude", 999)]
    evidence = CanonicalRuntime(str(tmp_path / "main.db")).raw_event_audits.audits_for_session(
        SessionId("session-one")
    )
    assert [
        audit.raw_event.harness_process_id
        for audit in evidence
        if audit.raw_event.source_type == "hook"
    ] == [4242]

    # Nothing to walk from: a delivery with no client pid claims no CLI pid.
    walked.clear()
    service.record("claude_code", hook_request(payload))
    assert walked == []


class _Widths:
    """The width policy a pane gesture consults, with the store left out."""

    def __init__(self, remembered) -> None:
        self.remembered = remembered

    @staticmethod
    def width_percent(working_directory) -> int:
        del working_directory
        return 31

    @staticmethod
    def configured_width_percent() -> int:
        return 31

    @staticmethod
    def resize_columns() -> int:
        return 7

    def remember_width(self, working_directory, width_percent) -> None:
        self.remembered.append((working_directory, width_percent))


def test_pane_command_service_executes_gestures_for_the_windows_session():
    class Terminal:
        def __init__(self):
            self.toggles = []
            self.resizes = []
            self.widths = []

        def session_for_window(self, window_id):
            assert window_id == "77"
            return SessionId("session-one")

        def toggle_session_panes(self, session_id, width_percent):
            self.toggles.append((session_id, width_percent))
            return SessionTerminalResult(True)

        def resize_activity_pane(self, session_id, columns):
            self.resizes.append((session_id, columns))
            return SessionTerminalResult(True)

        def activity_pane_geometry(self, session_id):
            return (25, 100)

        def set_activity_pane_width(self, session_id, width_percent):
            self.widths.append((session_id, width_percent))
            return SessionTerminalResult(True)

    terminal = Terminal()
    remembered = []
    service = pane_commands.PaneCommandService(terminal, _Widths(remembered), _silent_audit())

    outcomes = [
        service.toggle("77", "/project"),
        service.grow("77", "/project"),
        service.shrink("77", "/project"),
        service.reset("77", "/project"),
        service.set_percent("77", "/project", 75),
    ]

    assert all(outcome.handled and outcome.succeeded for outcome in outcomes)
    assert terminal.toggles == [(SessionId("session-one"), 31)]
    assert terminal.resizes == [
        (SessionId("session-one"), 7),
        (SessionId("session-one"), -7),
    ]
    assert terminal.widths == [
        (SessionId("session-one"), 31),
        (SessionId("session-one"), 75),
    ]
    assert [width_percent for _directory, width_percent in remembered] == [25, 25, 31, 75]


def test_pane_command_in_a_tab_without_a_session_is_quietly_unhandled():
    class Terminal:
        def session_for_window(self, window_id):
            return None

    outcome = pane_commands.PaneCommandService(Terminal(), _Widths([]), _silent_audit()).toggle("", "/project")
    assert outcome == pane_commands.PaneCommandOutcome(False, True)


# Every one of the 5 typed gesture methods must flow through the SAME private
# core (`_audited`) that writes the one pane-command audit row — a method that
# wrote its own row, or skipped the core, would leave that gesture unaudited.
def test_every_pane_command_method_writes_exactly_one_audit_row_through_one_core():
    class Terminal:
        def session_for_window(self, window_id):
            return SessionId("session-one")

        def toggle_session_panes(self, session_id, width_percent):
            return SessionTerminalResult(True)

        def resize_activity_pane(self, session_id, columns):
            return SessionTerminalResult(True)

        def activity_pane_geometry(self, session_id):
            return (25, 100)

        def set_activity_pane_width(self, session_id, width_percent):
            return SessionTerminalResult(True)

    rows = []

    class RowRecorder:
        def state_file(self, log, path, action, content=""):
            rows.append((action, content))

    service = pane_commands.PaneCommandService(Terminal(), _Widths([]), RowRecorder())
    calls = (
        (service.toggle, ("77", "/project")),
        (service.grow, ("77", "/project")),
        (service.shrink, ("77", "/project")),
        (service.reset, ("77", "/project")),
        (service.set_percent, ("77", "/project", 75)),
    )

    for before, (method, arguments) in enumerate(calls):
        method(*arguments)
        assert len(rows) == before + 1
        action, content = rows[-1]
        assert action == "pane-command"
        assert content["ok"] is True


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
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="child-hook",
        ),
        **child_context,
    )
    transcript_record = replace(
        raw_event(
            {"type": "user", "uuid": "child-prompt", "message": {"content": "inspect"}},
            harness=HarnessName.CLAUDE_CODE,
            source_type="transcript",
            raw_event_id="child-transcript",
            source_position="0",
        ),
        **child_context,
    )

    hook_start = ClaudeCanonicalTranslator().translate(hook).canonical_events[0]
    transcript_start = ClaudeCanonicalTranslator().translate(transcript_record).canonical_events[0]

    assert mapper.encode_canonical_event(hook_start) == mapper.encode_canonical_event(transcript_start)


def test_claude_subagent_stop_hook_finishes_the_actor():
    """The one signal that survives even when Claude Code suppresses the
    parent's `<task-notification>` — e.g. because the subagent left a
    `run_in_background` command still tracked — is the child's own
    SubagentStop hook. It must close the actor out on its own."""
    hook = replace(
        raw_event(
            {"hook_event_name": "SubagentStop", "hook_event_id": "child-stop"},
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="child-stop-hook",
        ),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )

    result = ClaudeCanonicalTranslator().translate(hook)

    finished = payloads(result, ActorFinished)
    assert len(finished) == 1
    assert finished[0].actor_id == ActorId("child-one")


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
            harness=HarnessName.CLAUDE_CODE,
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
            harness=HarnessName.CLAUDE_CODE,
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
            harness=HarnessName.CLAUDE_CODE,
            source_type="teammate_transcript",
            raw_event_id="later-message",
            source_position="500",
        ),
        actor_id=ActorId("worker-one"),
        parent_actor_id=ActorId("session-one:lead"),
    )

    first_start = translator.translate(first_record).canonical_events[0]
    later_start = translator.translate(later_message).canonical_events[0]

    assert mapper.encode_canonical_event(first_start) == mapper.encode_canonical_event(later_start)


def test_claude_lead_start_uses_the_first_root_record_with_a_working_directory():
    translator = ClaudeCanonicalTranslator()
    plumbing = translator.translate(raw_event(
        {"type": "queue-operation", "operation": "enqueue"},
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="root-record",
        source_position="297",
    ))
    hook = translator.translate(raw_event(
        {"hook_event_name": "SessionStart", "cwd": "/work"},
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="session-hook",
    ))

    assert plumbing.decision == "ignored_nonsemantic"
    assert [mapper.encode_canonical_event(event) for event in root_record.canonical_events[:2]] == [
        mapper.encode_canonical_event(event) for event in hook.canonical_events
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
    hook_payload = json.dumps({
        "session_id": "session-one",
        "transcript_path": str(main_path),
        "hook_event_name": "SubagentStart",
        "hook_event_id": "worker-start",
        "agent_id": "worker-one",
        "agent_type": "reviewer",
    }).encode()

    _deliver_hook(claude_hooks.ClaudeHookGateway(), hook_payload)
    runtime, interpreter = interpreting_runtime(tmp_path / "data" / "main.db")
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
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

    started = stored_payloads(runtime, SessionId("session-one"), ActorStarted)
    assert [actor.role for actor in started if actor.name == "worker-one"] == ["teammate"]


def test_codex_hook_maps_unique_compaction_lifecycle():
    translator = CodexCanonicalTranslator()
    before = translator.translate(raw_event(
        {"hook_event_name": "PreCompact", "hook_event_id": "compact-one"},
        harness=HarnessName.CODEX,
        source_type="hook",
        raw_event_id="compact-before",
    ))
    after = translator.translate(raw_event(
        {"hook_event_name": "PostCompact", "hook_event_id": "compact-one"},
        harness=HarnessName.CODEX,
        source_type="hook",
        raw_event_id="compact-after",
    ))

    assert isinstance(before.canonical_events[0].payload, CompactionStarted)
    assert isinstance(after.canonical_events[0].payload, CompactionFinished)


def test_codex_session_start_hook_matches_rollout_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    rollout_path = (
        tmp_path / "sessions" / "2026" / "08" / "14"
        / "rollout-2026-08-14T12-00-00-session-one.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text(json.dumps({
        "type": "session_meta",
        "payload": {"id": "session-one", "cwd": "/work", "thread_source": "user"},
    }) + "\n")
    translator = CodexCanonicalTranslator()
    hook = translator.translate(raw_event(
        {"hook_event_name": "SessionStart", "cwd": "/work",
         "transcript_path": str(rollout_path)},
        harness=HarnessName.CODEX,
        source_type="hook",
        raw_event_id="session-hook",
    ))
    rollout = translator.translate(replace(
        raw_event(
            {
                "timestamp": "2026-08-14T12:00:00Z",
                "type": "session_meta",
                "payload": {"cwd": "/work", "originator": "codex-tui"},
            },
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="session-rollout",
            source_position="0",
        ),
        source_name=str(rollout_path),
    ))

    assert hook.decision == "translated"
    # the rollout record carries its own timestamp; the identities and payloads converge
    assert [event.event_id for event in hook.canonical_events] == [
        event.event_id for event in rollout.canonical_events
    ]
    assert [mapper.payload_json(event) for event in hook.canonical_events] == [
        mapper.payload_json(event) for event in rollout.canonical_events
    ]


def test_hook_native_identity_reuse_with_different_bytes_is_a_hard_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    first = (
        b'{"session_id":"session-one","transcript_path":"/work/session.jsonl",'
        b'"cwd":"/work","hook_event_name":"PreToolUse","hook_event_id":"hook-one",'
        b'"tool_use_id":"tool-one",'
        b'"tool_name":"Bash","tool_input":{"command":"first"}}'
    )
    changed = first.replace(b'"first"', b'"changed"')
    _deliver_hook(claude_hooks.ClaudeHookGateway(), first)

    with pytest.raises(EventIdentityConflict, match="raw event identity reused"):
        _deliver_hook(claude_hooks.ClaudeHookGateway(), changed)


def test_catalogs_expose_only_what_depends_on_the_directory(tmp_path):
    """The catalogue is now the per-DIRECTORY half of the menu vocabulary.

    Everything a harness offers unconditionally moved onto HarnessInfo, which is
    a frozen literal built at import -- so only the slash commands, discovered by
    walking the session's own directory, still need a QueryContext.
    """
    application = ProviderGraph()
    context = QueryContext(session_id=None, working_directory=str(tmp_path))

    claude_catalog = application.catalog.read("claude_code", context)
    codex_catalog = application.catalog.read("codex", context)

    assert {command.command for command in claude_catalog.commands} != {
        command.command for command in codex_catalog.commands
    }
    assert not hasattr(claude_catalog, "models")
    assert not hasattr(claude_catalog, "accounts")


def test_static_menu_vocabulary_lives_on_the_harness_descriptor():
    from harness.impl.claude_code.plugin import plugin as claude_plugin
    from harness.impl.codex.plugin import plugin as codex_plugin

    assert [model.value for model in claude_plugin.info.models] == [
        "fable",
        "opus",
        "sonnet",
        "haiku",
    ]
    assert all(model.value.startswith("gpt-") for model in codex_plugin.info.models)
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
    from harness.impl.codex.plugin import plugin as codex_plugin

    by_id = {model.value: model for model in codex_plugin.info.models}
    luna = {effort.value for effort in by_id["gpt-5.6-luna"].efforts}
    sol = {effort.value for effort in by_id["gpt-5.6-sol"].efforts}

    assert "ultra" not in luna
    assert "ultra" in sol
    # every model still names exactly one default
    for model in codex_plugin.info.models:
        assert len([effort for effort in model.efforts if effort.default]) == 1


class _NoSessions:
    """A telemetry context for a delivery that names no session."""

    @staticmethod
    def find_session(session_id):
        del session_id


def test_the_daemon_decides_what_a_statusline_delivery_meant(monkeypatch, tmp_path):
    """The shim ships bytes; the WINDOWS are read here.

    The client half — that it forwards the stdin verbatim, stamps the two account
    values its environment selects raw, and still runs the real status line — is
    tested by running the process in tests/test_canonical_clients.py. What is left
    here is the half that decides what those bytes meant, including the validation
    the client no longer does.
    """
    monkeypatch.setattr(
        "harness.impl.claude_code.usage.rows.account.registry",
        lambda: [account.AccountRecord("work", "Work", "work")],
    )
    monkeypatch.setattr(claude_live_usage, "usage", lambda _config_directory: None)
    body = json.dumps({
        "session_id": "session-usage",
        "rate_limits": {
            "five_hour": {"used_percentage": 25, "resets_at": 2_000_000_000},
            "seven_day": {"used_percentage": 40, "resets_at": 2_000_100_000},
        },
        "_account_id": "work",
        "_account_name": "Work",
    }).encode()

    usage = SqliteAccountUsageRepository(main_database(str(tmp_path / "main.db")))
    response = claude_telemetry.ClaudeTelemetryGateway().handle(
        HarnessTelemetryRequest("statusline", body), _NoSessions()
    )
    assert response.usage is not None
    usage.record(response.usage)
    rows = claude_usage_reader.read(usage)

    assert rows[0].account_id == "work"
    assert [window.label for window in rows[0].windows] == ["5h", "7d"]
    assert rows[0].scheduling_score == Decimal("75")


def test_an_account_a_client_reported_is_validated_by_the_daemon():
    """A client forwards its environment and validates nothing, so both values
    reach us as external input: the id has to look like an id or it is no id, and
    a row always has a name to render."""
    assert account.normalize("work", "Work") == ("work", "Work")
    assert account.normalize("work", "") == ("work", "work")
    assert account.normalize("", "") == (None, "default")
    assert account.normalize("../etc/passwd", "Sneaky") == (None, "Sneaky")
    assert account.normalize(None, None) == (None, "default")
    # A status line writes JSON, so the field is not even known to be a string.
    assert account.normalize(7, 7) == ("7", "7")


def test_launchers_build_native_commands_and_share_terminal_launch_mechanics(monkeypatch, tmp_path):
    monkeypatch.setattr(account, "registry", lambda: [
        account.AccountRecord("c1", "Account One", "c1"),
        account.AccountRecord("c2", "Account Two", "c2"),
    ])
    application = ProviderGraph()
    terminal = FakeTerminal()
    launcher = HarnessLauncherService(
        application.registry,
        TerminalAdapter(terminal.plugin(), FakeSessions()),
        terminal,
    )
    attachment = AttachmentReference("/work/context.md", "context.md", "text/markdown")

    claude_result = launcher.launch(
        "claude_code",
        LaunchRequest(
            working_directory="/work",
            initial_text="hello",
            model="fable",
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
            model="gpt-5.6-terra",
            effort="high",
            account_id=None,
            resume_session_id=None,
            attachments=(attachment,),
        ),
    )

    assert claude_result.status == codex_result.status == "started"
    # launching is just running the CLI under a login shell — no wrapper
    # program, no session id invention. The shell's first three words are the
    # shared launch convention; everything after them is the harness's plan.
    assert terminal.opened_tabs[0].command[1] == "-lic"
    assert terminal.opened_tabs[0].command[3:] == (
        "c2",
        "--model",
        "fable",
        "--effort",
        "high",
        "@/work/context.md\nhello",
    )
    assert terminal.opened_tabs[1].command[3:] == (
        "codex",
        "-C",
        "/work",
        "-m",
        "gpt-5.6-terra",
        "-c",
        "model_reasoning_effort=high",
        "/work/context.md\nhello",
    )
    # the selections also ride the CLI's environment, so the hook process can
    # observe them — Claude Code's launch evidence (codex reports its own).
    # They precede the command word inside the shell's own -c string, which is
    # what keeps an aliased CLI resolving.
    assert terminal.opened_tabs[0].command[2].startswith(
        'BAQYLAU_LAUNCH_MODEL=fable BAQYLAU_LAUNCH_EFFORT=high c2 "$@"'
    )
    assert "BAQYLAU_LAUNCH_MODEL" not in terminal.opened_tabs[1].command[2]


def test_claude_account_selection_prefers_c2_only_as_the_missing_selection_fallback(monkeypatch):
    monkeypatch.setattr(account, "registry", lambda: [
        account.AccountRecord("c1", "Account One", "c1"),
        account.AccountRecord("c2", "Account Two", "c2"),
    ])

    assert account.alias_for(None) == "c2"
    assert account.alias_for(AccountId("c1")) == "c1"
    assert account.alias_for(AccountId("claude")) == "claude"
    assert account.alias_for(AccountId("missing")) is None

    monkeypatch.setattr(account, "registry", lambda: [])
    assert account.alias_for(None) == "claude"


def test_a_harness_that_announces_at_its_first_turn_refuses_an_empty_launch(tmp_path):
    """codex's session_start hook fires WITH the first prompt, so a promptless
    launch runs in the terminal and is never observed here — the launcher declines
    it (HarnessInfo.requires_initial_message) instead of leaving the dashboard
    waiting for a session that cannot arrive. Claude Code announces itself at
    startup and so still launches empty."""
    application = ProviderGraph()
    terminal = FakeTerminal()
    launcher = HarnessLauncherService(
        application.registry,
        TerminalAdapter(terminal.plugin(), FakeSessions()),
        terminal,
    )
    empty = LaunchRequest(
        working_directory="/work",
        initial_text="   ",
        model=None,
        effort=None,
        account_id=None,
        resume_session_id=None,
    )

    rejected = launcher.launch("codex", empty)
    assert rejected.status == "rejected"
    assert "needs a first message" in (rejected.reason or "")
    assert terminal.opened_tabs == []

    # attachments ARE a first message: they ride the argv as the prompt, which is
    # a turn as far as the CLI is concerned — and so an announcement.
    attached = launcher.launch("codex", replace(
        empty,
        initial_text=None,
        attachments=(AttachmentReference("/work/context.md", "context.md"),),
    ))
    assert attached.status == "started"

    assert launcher.launch("claude_code", empty).status == "started"


def test_login_shell_command_carries_environment_before_the_command_word(monkeypatch):
    from terminal.launch import login_shell_command

    monkeypatch.setenv("SHELL", "/bin/zsh")
    shell, flag, script, *argv = login_shell_command(
        ("c1", "--model", "fable"),
        (("BAQYLAU_LAUNCH_MODEL", "fable"), ("BAQYLAU_LAUNCH_EFFORT", "high")),
    )
    assert (shell, flag) == ("/bin/zsh", "-lic")
    # assignments precede the command word so the alias still resolves
    assert script == 'BAQYLAU_LAUNCH_MODEL=fable BAQYLAU_LAUNCH_EFFORT=high c1 "$@"'
    assert argv == ["c1", "--model", "fable"]
    with pytest.raises(ValueError):
        login_shell_command(("c1",), (("bad name", "x"),))


def test_claude_terminal_probe_owns_input_box_grammar(tmp_path):
    divider = "\x1b[m\x1b[38:2:136:136:136m" + "─" * 20
    screen = divider + "\n\x1b[m❯\xa0\x1b[22;2mapply the fix\n" + divider

    terminal = FakeTerminal(screen_text=screen)

    plugin = ProviderGraph().registry.plugin("claude_code")
    state = plugin.terminal_probe.input_state(terminal, "window-one")

    assert state.suggestion == "apply the fix"
    assert state.typed_text == ""


def control_context(session, terminal, pending_attention=None, window_id="window-one"):
    return ControlContext(
        session, terminal, window_id, None, pending_attention
    )


def test_claude_question_discussion_is_delivered_after_declining(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.askdialog.drive",
        lambda _terminal, _window, _prompts, _answers, *, chat: calls.append(("dialog", chat)),
    )
    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.tui.type_command",
        lambda _terminal, _window, text: (calls.append(("discussion", text)) or (True, False)),
    )
    application = ProviderGraph()
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
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
    attention = QuestionAsked(
        "attention-one",
        (AttentionPrompt("question-one", None, "Continue?", False, ()),),
    )

    outcome = application.registry.plugin("claude_code").controller.execute(
        request,
        control_context(session, FakeTerminal().plugin(), attention),
    )

    assert outcome.status == "acknowledged"
    assert calls == [("dialog", True), ("discussion", "change the approach")]


def test_codex_question_discussion_stays_in_the_native_dialog(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "harness.impl.codex.controls.controller.dialog.decline",
        lambda _terminal, _window, _prompts, message: calls.append(message),
    )
    application = ProviderGraph()
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
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
    attention = QuestionAsked(
        "attention-one",
        (AttentionPrompt("question-one", None, "Continue?", False, ()),),
    )

    outcome = application.registry.plugin("codex").controller.execute(
        request,
        control_context(session, FakeTerminal().plugin(), attention),
    )

    assert outcome.status == "acknowledged"
    assert calls == ["change the approach"]


def test_claude_model_control_resolves_the_native_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.tui.type_command",
        lambda _terminal, _window, _text: (True, False),
    )
    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.confirmdialog.confirm",
        lambda _terminal, _window: confirmdialog.ConfirmOutcome(True, "1"),
    )
    application = ProviderGraph()
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "/work/session.jsonl",
        "/work",
    )
    request = SelectModel(session.session_id, "request-one", "opus")

    outcome = application.registry.plugin("claude_code").controller.execute(
        request,
        control_context(session, FakeTerminal().plugin()),
    )

    assert outcome.status == "acknowledged"
    assert outcome.confirmation == "confirmed"


class _RecordingTitles:
    """A `NativeSessionTitleRepository` that records rather than writes."""

    def __init__(self, calls) -> None:
        self.calls = calls

    @staticmethod
    def renameable(source_reference) -> bool:
        del source_reference
        return True

    def set_title(self, source_reference, title):
        self.calls.append((source_reference, title))
        return "renamed"


@pytest.mark.parametrize(
    ("harness", "native_writer"),
    [
        ("claude_code", "harness.impl.claude_code.controls.controller.transcript.titles"),
        ("codex", "harness.impl.codex.controls.controller.title.titles"),
    ],
)
def test_parked_rename_uses_only_the_owning_harness_title_store(
    monkeypatch,
    tmp_path,
    harness,
    native_writer,
):
    calls = []
    monkeypatch.setattr(native_writer, _RecordingTitles(calls))
    application = ProviderGraph()
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
        "/work/native-session",
        "/work",
    )
    request = RenameSession(session.session_id, "request-one", "New title")

    outcome = application.registry.plugin(harness).controller.execute(
        request,
        control_context(session, FakeTerminal().plugin(), window_id=None),
    )

    assert outcome.status == "acknowledged"
    assert calls == [(session.source_reference, "New title")]


def test_claude_prompt_and_codex_prompt_share_the_message_model():
    claude = ClaudeCanonicalTranslator().translate(
        raw_event(
            {"type": "user", "uuid": "claude-message", "message": {"content": "fix it"}},
            harness=HarnessName.CLAUDE_CODE,
            source_type="transcript",
            raw_event_id="claude-prompt",
        )
    )
    codex = CodexCanonicalTranslator().translate(
        raw_event(
            {"type": "event_msg", "payload": {"type": "user_message", "message": "fix it"}},
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="codex-prompt",
        )
    )
    claude_message = payloads(claude, MessageCreated)[0].payload
    codex_message = payloads(codex, MessageCreated)[0].payload
    assert claude_message.role == codex_message.role == "user"
    assert claude_message.phase == codex_message.phase == "prompt"
    # Claude Code announces no turn of its own, so the prompt opens one and the
    # message rides it; codex names its own turns and needs no such help.
    assert payloads(claude, TurnStarted)[0].payload.prompt_message_id == "claude-message"
    assert [event.turn_id for event in claude.canonical_events] == ["claude-message"] * 2


def test_claude_child_prompt_is_authored_by_the_parent_agent():
    child_prompt = replace(
        raw_event(
            {"type": "user", "uuid": "child-prompt", "message": {"content": "inspect it"}},
            harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="agent-launch-ack",
    ))

    child_started = payloads(started, ActorAssignmentStarted)[0].payload
    assert child_started.brief.text == "Get current weather in Bali"
    assert child_started.actor_name == "general-purpose"
    assert child_started.prompt.text == "Look up current weather and a short forecast."
    # An async launch's result finishes nothing: the Agent tool returned, the
    # assignment did not. There is no shell here either — an assignment is not a
    # command — so the whole delivery says only that.
    assert launch_ack.canonical_events == ()
    assert launch_ack.decision == "ignored_nonsemantic"


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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="task-notification",
    ))

    finished = payloads(notification, ActorAssignmentFinished)
    assert not payloads(notification, MessageCreated)
    assert finished[0].payload.assignment_id == "agent-tool-one"
    assert finished[0].payload.outcome == "succeeded"
    assert finished[0].payload.result.text == "Sunny, 29°C."


def test_claude_background_completion_is_an_output_finish_not_an_agent_finish():
    """Background Bash completions ride the SAME <task-notification> channel as
    agent completions; treating them as assignment finishes painted phantom
    "Agent finished" blocks for plain background commands (session 67dfd402,
    2026-08-16). The summary prefix is the discriminator."""
    notification = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "type": "user",
            "uuid": "background-completion-one",
            "origin": {"kind": "task-notification"},
            "promptSource": "system",
            "message": {
                "content": (
                    "<task-notification><task-id>bkdr7jbeo</task-id>"
                    "<tool-use-id>background-op-one</tool-use-id>"
                    "<output-file>/tmp/tasks/bkdr7jbeo.output</output-file>"
                    "<status>completed</status>"
                    '<summary>Background command "Count 1 to 10" completed (exit code 0)</summary>'
                    "</task-notification>"
                )
            },
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="background-completion",
    ))

    assert not payloads(notification, ActorAssignmentFinished)
    assert not payloads(notification, MessageCreated)
    finished = payloads(notification, ShellOutputFinished)
    assert len(finished) == 1
    assert finished[0].payload.shell_id == ShellId("background-op-one")
    assert finished[0].payload.outcome == "succeeded"


def test_claude_background_completion_carries_the_jobs_own_outcome():
    """The `<status>` is the JOB's, and the launch's says nothing about it: a
    command that exits non-zero launched perfectly. Values measured over every
    retained transcript (2026-08-18): completed, failed, killed, stopped."""

    def outcome_for(status):
        translation = ClaudeCanonicalTranslator().translate(raw_event(
            {
                "type": "user",
                "uuid": f"background-completion-{status}",
                "origin": {"kind": "task-notification"},
                "promptSource": "system",
                "message": {
                    "content": (
                        "<task-notification><task-id>bkdr7jbeo</task-id>"
                        "<tool-use-id>background-op-one</tool-use-id>"
                        f"<status>{status}</status>"
                        '<summary>Background command "Count" completed (exit code 0)</summary>'
                        "</task-notification>"
                    )
                },
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="transcript",
            raw_event_id=f"background-completion-{status}",
        ))
        return payloads(translation, ShellOutputFinished)[0].payload.outcome

    assert outcome_for("completed") == "succeeded"
    assert outcome_for("failed") == "failed"
    assert outcome_for("killed") == "cancelled"
    assert outcome_for("stopped") == "cancelled"
    assert outcome_for("something-new") == "unknown"


def _monitor_notification(uuid, body):
    """One <task-notification> as a `user` record — the shape every notification
    really arrives in (measured, claude-code 2.1.233)."""
    return raw_event(
        {
            "type": "user",
            "uuid": uuid,
            "origin": {"kind": "task-notification"},
            "promptSource": "system",
            "message": {"content": f"<task-notification>{body}</task-notification>"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id=uuid,
    )


def _armed_monitor(translator, shell_id="monitor-op-one", task_id="bmfwjr03l"):
    """A Monitor tool call returning, which is where its task id is announced."""
    translator.translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": shell_id,
            "tool_name": "Monitor",
            "tool_input": {"command": "tail -f log", "description": "ticks"},
            "tool_response": {"taskId": task_id, "timeoutMs": 300000, "persistent": False},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id=f"arm-{shell_id}",
    ))


def test_claude_monitor_events_are_progress_on_the_monitor_not_agent_finishes():
    """A monitor's events are the whole point of arming one, and every one of them
    was being read as an AGENT completing (session 246c8079, 2026-08-17): the
    <task-notification> fallback treated anything that was not a background
    command as an assignment finish, so six ticks became one phantom
    `actor.assignment_finished` — one, because they all carried an empty
    assignment id and collapsed onto a single event id — and the event text was
    dropped on the floor."""
    translator = ClaudeCanonicalTranslator()
    _armed_monitor(translator)

    ticks = [
        translator.translate(_monitor_notification(
            f"tick-{number}",
            "<task-id>bmfwjr03l</task-id>"
            '<summary>Monitor event: "ticks"</summary>'
            f"<event>tick-{number}</event>",
        ))
        for number in (1, 2, 3)
    ]

    for tick in ticks:
        assert not payloads(tick, ActorAssignmentFinished)
        assert not payloads(tick, MessageCreated)
    progressed = [payloads(tick, ShellProgressed)[0] for tick in ticks]
    assert [entry.payload.content.text for entry in progressed] == ["tick-1", "tick-2", "tick-3"]
    assert all(entry.payload.shell_id == ShellId("monitor-op-one") for entry in progressed)
    # The "status" stream is what the monitors tab reads as an event rather than
    # as output, and the ordinals are what keep three events three rows: the
    # event id is built from the subject and the phase, so a shared phase would
    # collapse them the way the phantom assignment finishes collapsed.
    assert all(entry.payload.stream == "status" for entry in progressed)
    assert [entry.payload.ordinal for entry in progressed] == [0, 1, 2]
    assert len({entry.event_id for entry in progressed}) == 3


def test_claude_monitor_event_for_an_unknown_task_is_dropped_not_invented():
    """The per-event notification names only the TASK id, so an event whose arm
    this translator never saw — a daemon restarted mid-watch — cannot be placed.
    Dropping it loses one line; inventing an operation would put a monitor on the
    tab that nothing ever armed."""
    translation = ClaudeCanonicalTranslator().translate(_monitor_notification(
        "orphan-tick",
        "<task-id>never-seen</task-id>"
        '<summary>Monitor event: "ticks"</summary>'
        "<event>tick-1</event>",
    ))

    assert translation.canonical_events == ()
    assert translation.decision == "ignored_nonsemantic"


def test_claude_monitor_ends_on_its_own_notification_not_on_its_arm():
    """The arm's `operation.finished` arrives turns earlier and means only that
    the tool call returned — the projection ignores it for a monitor, which is
    why nothing ended one. The stream-ended notification is the monitor's own
    end, and it carries a tool_use_id, so it needs no memory of the arm."""
    translator = ClaudeCanonicalTranslator()
    _armed_monitor(translator)

    ended = translator.translate(_monitor_notification(
        "monitor-ended",
        "<task-id>bmfwjr03l</task-id>"
        "<tool-use-id>monitor-op-one</tool-use-id>"
        "<output-file>/tmp/tasks/bmfwjr03l.output</output-file>"
        "<status>completed</status>"
        '<summary>Monitor "ticks" stream ended</summary>',
    ))

    assert not payloads(ended, ActorAssignmentFinished)
    finished = payloads(ended, ShellOutputFinished)
    assert len(finished) == 1
    assert finished[0].payload.shell_id == ShellId("monitor-op-one")
    assert finished[0].payload.outcome == "succeeded"


def test_claude_task_notifications_are_counted_once_though_they_arrive_twice():
    """Every notification appears in the transcript twice: as the `queue-operation`
    that enqueued it and as the `user` record that delivered it. Reading both
    would double every monitor event."""
    translator = ClaudeCanonicalTranslator()
    _armed_monitor(translator)

    enqueued = translator.translate(raw_event(
        {
            "type": "queue-operation",
            "operation": "enqueue",
            "content": (
                "<task-notification><task-id>bmfwjr03l</task-id>"
                '<summary>Monitor event: "ticks"</summary>'
                "<event>tick-1</event></task-notification>"
            ),
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="enqueue-tick-1",
    ))

    assert enqueued.canonical_events == ()
    assert enqueued.decision == "ignored_nonsemantic"


def test_claude_command_backgrounded_mid_run_says_so_before_it_says_finished():
    """ctrl+b on a running command. The input never asked for the background and
    the response carries a task id anyway — and the ORDER matters: the
    `operation.finished` from this same delivery ends the follow of the file the
    command is still writing to unless the backgrounded fact lands first."""
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "session-one",
            "transcript_path": "/tmp/session-one.jsonl",
            "tool_name": "Bash",
            "tool_use_id": "op-backgrounded",
            "tool_input": {"command": "sleep 30; echo done"},
            "tool_response": {"backgroundTaskId": "btk9y72c9"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="post-tool-use-backgrounded",
    ))

    kinds = [type(canonical.payload).__name__ for canonical in translation.canonical_events]
    assert kinds.index("ShellBackgrounded") < kinds.index("ShellFinished")
    backgrounded = payloads(translation, ShellBackgrounded)[0].payload
    assert backgrounded.shell_id == ShellId("op-backgrounded")
    assert backgrounded.shell_id == ShellId("op-backgrounded")


def test_claude_background_launch_is_not_a_mid_run_backgrounding():
    """A command that ASKED for the background is already background at
    `operation.started`; announcing the transition too would be a second, later
    answer to a question the launch already settled."""
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "session-one",
            "transcript_path": "/tmp/session-one.jsonl",
            "tool_name": "Bash",
            "tool_use_id": "op-native-background",
            "tool_input": {"command": "sleep 30", "run_in_background": True},
            "tool_response": {"backgroundTaskId": "btk9y72c9"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="post-tool-use-native-background",
    ))

    assert not payloads(translation, ShellBackgrounded)


def test_codex_exec_that_outlives_its_yield_is_announced_as_background_once():
    """codex's background terminal: the exec handed back a live session (the cell
    id `/ps` lists) with no exit code. Every continuation poll reports it again,
    and the fact is about the operation, not about the poll."""
    translator = CodexCanonicalTranslator()

    def rollout_event(document, position):
        return raw_event(
            document,
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id=f"codex-bg-{position}",
            source_position=str(position),
        )

    translator.translate(rollout_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "call-one",
            "input": 'const r = await tools.exec_command({"cmd":"sleep 30","yield_time_ms":250});',
        },
    }, 10))
    first = translator.translate(rollout_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output", "call_id": "call-one",
            "output": '{"output":"","session_id":4242}',
        },
    }, 20))
    second = translator.translate(rollout_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output", "call_id": "call-one",
            "output": '{"output":"still going","session_id":4242}',
        },
    }, 30))

    backgrounded = payloads(first, ShellBackgrounded)
    assert len(backgrounded) == 1
    assert backgrounded[0].payload.shell_id
    assert not payloads(first, ShellFinished)
    assert not payloads(second, ShellBackgrounded)


def test_claude_skill_and_web_and_worktree_tools_have_their_own_facts():
    """Four tool families that used to be one generic operation, each now saying
    what it actually is. A skill has a life (it runs, it answers); a fetch and a
    worktree move do not — they are one fact at result time."""
    translator = ClaudeCanonicalTranslator()

    def hook(document, raw_event_id):
        return translator.translate(raw_event(
            document, harness=HarnessName.CLAUDE_CODE, source_type="hook", raw_event_id=raw_event_id
        ))

    skill_start = hook({
        "hook_event_name": "PreToolUse",
        "tool_use_id": "skill-one",
        "tool_name": "Skill",
        "tool_input": {"skill": "audit-debug"},
    }, "skill-start")
    skill_finish = hook({
        "hook_event_name": "PostToolUse",
        "tool_use_id": "skill-one",
        "tool_name": "Skill",
        "tool_input": {"skill": "audit-debug"},
        "tool_response": "the skill's report",
    }, "skill-finish")
    fetched = hook({
        "hook_event_name": "PostToolUse",
        "tool_use_id": "fetch-one",
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.dev/docs"},
        "tool_response": "the page",
    }, "fetch")
    entered = hook({
        "hook_event_name": "PostToolUse",
        "tool_use_id": "worktree-one",
        "tool_name": "EnterWorktree",
        "tool_input": {"branch": "wip"},
        "tool_response": "entered",
    }, "worktree")

    started = payloads(skill_start, SkillStarted)[0].payload
    assert (started.skill_id, started.name) == ("skill-one", "audit-debug")
    # Claude collapses a Skill call's input to the bare name, so there is nothing
    # left to show as arguments.
    assert started.arguments is None
    assert payloads(skill_finish, SkillFinished)[0].payload.result.text == "the skill's report"

    assert payloads(fetched, WebFetched)[0].payload.url == "https://example.dev/docs"
    assert payloads(fetched, WebFetched)[0].payload.result.text == "the page"
    # No harness exposes a worktree path, so the call's own arguments ride along
    # rather than a parsed field that would always be empty.
    changed = payloads(entered, WorktreeChanged)[0].payload
    assert changed.action == "entered"
    assert changed.arguments.field("branch") == "wip"


def test_claude_plan_is_proposed_and_then_resolved_with_what_the_person_decided():
    translator = ClaudeCanonicalTranslator()
    proposed = translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "plan-one",
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": "1. Read it\n2. Change it"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="plan-proposed",
    ))
    changes_requested = translator.translate(raw_event(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_use_id": "plan-one",
            "tool_name": "ExitPlanMode",
            "tool_input": {},
            "tool_response": (
                "The user doesn't want to proceed. To tell you how to proceed, "
                "the user said:\nstart with the tests"
            ),
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="plan-resolved",
    ))

    assert payloads(proposed, PlanProposed)[0].payload.plan.text == "1. Read it\n2. Change it"
    resolved = payloads(changes_requested, PlanResolved)[0].payload
    assert resolved.attention_id == "plan-one"
    assert resolved.state == "changes_requested"
    assert resolved.feedback == "start with the tests"
    assert resolved.edited is False


def test_claude_enter_plan_mode_is_a_deliberate_ignore_not_drift():
    """`EnterPlanMode` is `ExitPlanMode`'s sibling, but it carries nothing to
    show: measured against the real corpus, every call sends no arguments and
    every result is the one fixed instruction Claude Code always sends back
    ("Entered plan mode. You should now focus on..."). Nothing there is
    session-specific, so it must land as a named, deliberate ignore — not
    `ignored_unknown`, which means a shape nobody has decided about."""
    translator = ClaudeCanonicalTranslator()
    started = translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "enter-plan-one",
            "tool_name": "EnterPlanMode",
            "tool_input": {},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="enter-plan-started",
    ))
    finished = translator.translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "enter-plan-one",
            "tool_name": "EnterPlanMode",
            "tool_input": {},
            "tool_response": (
                "Entered plan mode. You should now focus on exploring the "
                "codebase and designing an implementation approach."
            ),
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="enter-plan-finished",
    ))

    assert started.decision == "ignored_nonsemantic"
    assert finished.decision == "ignored_nonsemantic"


def test_claude_turn_opens_on_the_prompt_and_closes_on_the_stop_hook():
    """Claude Code emits no turn boundary of its own — its Stop hook says a turn
    ended and nothing says one began — so the prompt opens the turn and every
    fact until the Stop rides it. Without this the feed has nothing to group by."""
    translator = ClaudeCanonicalTranslator()
    prompt = translator.translate(raw_event(
        {"type": "user", "uuid": "prompt-one", "message": {"content": "fix it"}},
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="prompt",
    ))
    during = translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "tool-one",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="tool",
    ))
    injected = translator.translate(raw_event(
        {"type": "user", "uuid": "prompt-two", "message": {"content": "and also this"}},
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="injection",
    ))
    stop = translator.translate(raw_event(
        {"hook_event_name": "Stop", "hook_event_id": "stop-one"},
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="stop",
    ))
    after = translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "tool-two",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="after",
    ))

    assert payloads(prompt, TurnStarted)[0].payload.prompt_message_id == "prompt-one"
    assert during.canonical_events[0].turn_id == TurnId("prompt-one")
    # An injection is part of the turn it interrupted, not a turn of its own.
    assert payloads(injected, TurnStarted) == []
    assert injected.canonical_events[0].turn_id == TurnId("prompt-one")
    assert payloads(stop, TurnFinished)[0].turn_id == TurnId("prompt-one")
    # …and nothing after the Stop belongs to the turn it closed.
    assert after.canonical_events[0].turn_id is None


def test_claude_search_is_one_fact_holding_both_its_query_and_its_result():
    """A search has no life between asking and answering that anyone reads, so
    the call alone is not a fact — it is remembered, and the result carries
    both halves. The result text is rendered readably from its native blocks
    (here a `tool_reference`, which is how ToolSearch answers)."""
    translator = ClaudeCanonicalTranslator()
    call = translator.translate(raw_event(
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="tool-search",
    ))
    result = translator.translate(raw_event(
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="tool-result",
    ))

    assert call.decision == "ignored_nonsemantic"
    performed = payloads(result, SearchPerformed)[0].payload
    assert performed.tool == "ToolSearch"
    assert performed.query == TextContent("select:WebSearch")
    assert performed.result.text == "→ loaded tool: WebSearch"
    assert performed.outcome == "succeeded"


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
            harness=HarnessName.CLAUDE_CODE,
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


def test_codex_deliberate_ignores_are_nonsemantic_and_only_drift_stays_unknown():
    """`ignored_unknown` must mean "a shape nobody has decided about" — nothing else.

    Two records were decided about in code and still reported themselves as
    unknown (measured against codex-cli 0.147.0, which is what the live-harness
    suite caught): a `world_state` snapshot, and the `item_completed` envelope for
    message items whose prose the response_item register already delivers. They
    are nonsemantic now. An item_completed for a type NOBODY has ruled on stays
    unknown — that is the tripwire, and it has to survive this change.
    """

    def verdict(document):
        return CodexCanonicalTranslator().translate(raw_event(
            document,
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id=f"codex-{document.get('type')}-{id(document)}",
        )).decision

    def item_completed(item):
        return {
            "type": "event_msg",
            "payload": {"type": "item_completed", "turn_id": "turn-one", "item": item},
        }

    assert verdict({"type": "world_state", "payload": {"full": True, "state": {}}}) \
        == "ignored_nonsemantic"
    assert verdict(item_completed({
        "type": "AgentMessage", "id": "msg-one",
        "content": [{"type": "Text", "text": "Hi"}], "phase": "final_answer",
    })) == "ignored_nonsemantic"
    assert verdict(item_completed({"type": "UserMessage", "id": "item-one"})) == "ignored_nonsemantic"
    assert verdict(item_completed({
        "type": "Reasoning", "id": "rs-one", "summary_text": [], "raw_content": [],
    })) == "ignored_nonsemantic"
    assert verdict(item_completed({"type": "SomethingCodexShipsNextMonth", "id": "item-two"})) \
        == "ignored_unknown"

    # A type we parse whose TEXT is absent: an assistant `message` placeholder
    # (measured: `phase: "commentary"` with `output_text: ""`) and a `reasoning`
    # whose summary was stored encrypted. Recognised, empty, not drift.
    assert verdict({
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "phase": "commentary",
                    "content": [{"type": "output_text", "text": ""}]},
    }) == "ignored_nonsemantic"
    assert verdict({"type": "response_item", "payload": {"type": "reasoning", "summary": []}}) \
        == "ignored_nonsemantic"

    # …but a record missing a REQUIRED field stays drift: that is a field that
    # moved, which is the one thing the unknown verdict is for.
    assert verdict(item_completed({"type": "CommandExecution", "id": "item-three"})) \
        == "ignored_unknown"


def test_codex_unknown_field_on_a_known_record_fails_translation_naming_it():
    """The owner's strictest-stance decision (TASKS.md, 2026-08-21): a KNOWN
    record kind carrying a field records.py has not declared is schema drift,
    not tolerance. `translate()` raises exactly like the existing "unknown
    Codex goal state" tripwire above — the interpreter loop
    (engine/interpret/loop.py) is what turns any exception into the stored
    `translation_failed` verdict, with pydantic's own `extra_forbidden`
    message naming the field."""
    with pytest.raises(ValidationError, match="a_field_records_py_has_never_declared"):
        CodexCanonicalTranslator().translate(raw_event(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "turn-one",
                    "a_field_records_py_has_never_declared": "surprise",
                },
            },
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="codex-unknown-field",
        ))


def test_codex_wrong_typed_field_on_a_known_record_fails_translation():
    """Same decision, the other half of "shape mismatch": a declared field
    present with the WRONG type is exactly as much drift as a missing one or
    an extra one — `turn_id` is a string in every measured rollout, never a
    list."""
    with pytest.raises(ValidationError, match="turn_id"):
        CodexCanonicalTranslator().translate(raw_event(
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": ["not", "a", "string"]},
            },
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="codex-wrong-type",
        ))


def test_codex_unknown_record_kind_stays_ignored_not_failed():
    """The distinction the owner's decision draws (TASKS.md, 2026-08-21): an
    UNRECOGNISED `payload.type` string is the grammar growing (verified drift
    across codex 0.95 -> 0.144), not a shape mismatch within a type this
    codebase claims to know — `ignored_unknown`, never `translation_failed`,
    however unfamiliar its payload looks."""
    translated = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "a_record_kind_codex_has_not_shipped_yet",
                "whatever_fields_it_someday_carries": True,
            },
        },
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="codex-unknown-kind",
    ))
    assert translated.decision == "ignored_unknown"
    assert translated.canonical_events == ()


def test_claude_unknown_hook_field_fails_translation_naming_it():
    """The owner's strictest-stance decision (TASKS.md, 2026-08-21) applied to
    Claude Code's own hook contract (canonical/records.py's HookPayload): a
    hook delivery carrying a field that module has not declared is schema
    drift, not tolerance — the same outcome the codex wave's equivalent test
    checks for its own foreign register."""
    with pytest.raises(ValidationError, match="a_field_records_py_has_never_declared"):
        ClaudeCanonicalTranslator().translate(raw_event(
            {
                "hook_event_name": "Stop",
                "session_id": "session-one",
                "a_field_records_py_has_never_declared": "surprise",
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="claude-unknown-field",
        ))


def test_claude_wrong_typed_hook_field_fails_translation():
    """Same decision, the other half of "shape mismatch": a declared field
    present with the WRONG type is exactly as much drift as a missing or an
    extra one — `duration_ms` is a number in every measured hook delivery,
    never a list."""
    with pytest.raises(ValidationError, match="duration_ms"):
        ClaudeCanonicalTranslator().translate(raw_event(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "session-one",
                "tool_use_id": "call-one",
                "tool_name": "Bash",
                "duration_ms": ["not", "a", "number"],
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="claude-wrong-type",
        ))


def test_claude_message_usage_models_the_complete_current_vendor_shape():
    """The 2.1.239 transcript contract is typed all the way through its nested
    usage records; these are records, not dynamic dictionaries."""
    usage = MessageUsage.model_validate({
        "output_tokens_details": {"thinking_tokens": 7},
        "input_tokens": 2,
        "output_tokens": 3,
        "cache_creation_input_tokens": 4,
        "cache_read_input_tokens": 5,
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "cache_creation": {
            "ephemeral_1h_input_tokens": 0,
            "ephemeral_5m_input_tokens": 4,
        },
        "inference_geo": "not_available",
        "iterations": [{
            "input_tokens": 2,
            "output_tokens": 3,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 4,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 4,
            },
            "type": "message",
            "model": "claude-fable-5",
        }],
        "speed": "standard",
    })

    assert usage.server_tool_use is not None
    assert usage.server_tool_use.web_fetch_requests == 0
    assert usage.iterations is not None
    assert usage.iterations[0].type.value == "message"


def test_claude_stop_hook_summary_uses_typed_hook_records():
    summary = SystemRecord.model_validate({
        "type": "system",
        "subtype": "stop_hook_summary",
        "hookCount": 2,
        "hookInfos": [
            {"command": ".venv/bin/python client/claude_hook.py", "durationMs": 74},
            {"command": "node stop-review-gate-hook.mjs", "durationMs": 105},
        ],
        "hookErrors": [],
        "hookAdditionalContext": [],
        "preventedContinuation": False,
        "stopReason": "",
        "hasOutput": False,
        "level": "suggestion",
    })

    assert summary.hookInfos is not None
    assert summary.hookInfos[0].durationMs == 74


def test_codex_current_app_server_rate_limits_are_strictly_typed_and_normalized():
    response = codex_usage.RateLimitsRpcResponse.model_validate({
        "id": 2,
        "result": {
            "rateLimits": {
                "limitId": "codex",
                "limitName": None,
                "primary": {
                    "usedPercent": 12,
                    "windowDurationMins": 10080,
                    "resetsAt": 1787879978,
                },
                "secondary": None,
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                "individualLimit": None,
                "spendControlReached": False,
                "planType": "prolite",
                "rateLimitReachedType": None,
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "limitName": None,
                    "primary": {
                        "usedPercent": 12,
                        "windowDurationMins": 10080,
                        "resetsAt": 1787879978,
                    },
                    "secondary": None,
                    "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                    "individualLimit": None,
                    "spendControlReached": False,
                    "planType": "prolite",
                    "rateLimitReachedType": None,
                },
            },
            "rateLimitResetCredits": {
                "availableCount": 1,
                "credits": [{
                    "id": "credit-one",
                    "resetType": "codexRateLimits",
                    "status": "available",
                    "grantedAt": 1787358029,
                    "expiresAt": 1789950029,
                    "title": "Full reset",
                    "description": "One free rate limit reset.",
                }],
            },
        },
    })

    normalized = codex_usage.normalize_rate_limits(response.result)
    assert normalized is not None
    assert normalized.plan == "prolite"
    assert normalized.windows[0].used_percent == 12
    assert normalized.windows[0].duration_minutes == 10080


def test_claude_unmapped_tool_stays_ignored_not_failed():
    """The distinction the owner's decision draws, on THIS package's own
    "unknown kind" dispatch (toolcalls.TOOL_KINDS, not records.py): an
    unrecognised NATIVE TOOL NAME is that vocabulary growing — a Claude Code
    build shipping a tool this codebase has not mapped yet — not a shape
    mismatch within a tool it claims to know, so it stays `ignored_unknown`,
    never `translation_failed`, however ordinary its arguments look."""
    translated = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": "session-one",
            "tool_use_id": "call-one",
            "tool_name": "ATool2026HasNotShippedYet",
            "tool_input": {"whatever": "fields"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="claude-unknown-kind",
    ))
    assert translated.decision == "ignored_unknown"
    assert translated.canonical_events == ()


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
            harness=HarnessName.CODEX,
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
            harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="custom-title",
    ))
    automatic = translator.translate(raw_event(
        {"type": "ai-title", "aiTitle": "Generated name"},
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="assistant",
    ))

    reasoning = payloads(translation, ReasoningCreated)[0].payload
    model = payloads(translation, ModelChanged)[0].payload
    context = payloads(translation, ContextReported)[0].payload
    assert reasoning.content.text == "Inspect the failure"
    assert model.current.name == "claude-opus-4-8"
    assert model.current.display_name == "opus-4.8"
    assert context.used_tokens == 10
    assert context.window_tokens == 1_000_000
    assert context.model == model.current
    assert payloads(translation, UsageReported) == []


def test_claude_marks_where_the_model_stopped_from_the_response_stop_reason():
    """`stop_reason` is the only structural tell, and it belongs to the RESPONSE.

    So a response that broke off to call a tool ends no turn, and of a response
    that DID stop only its last text block does — the earlier blocks are prose the
    model wrote on the way there. Measured against claude-code 2.1.233.
    """

    def phases(stop_reason, blocks):
        translation = ClaudeCanonicalTranslator().translate(raw_event(
            {
                "type": "assistant",
                "uuid": "assistant-one",
                "message": {"content": blocks, "stop_reason": stop_reason},
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="transcript",
            raw_event_id=f"assistant-{stop_reason}-{len(blocks)}",
        ))
        return [payload.payload.phase for payload in payloads(translation, MessageCreated)]

    one_block = [{"type": "text", "text": "Hi"}]
    two_blocks = [{"type": "text", "text": "Working on it"}, {"type": "text", "text": "Done"}]

    assert phases("end_turn", one_block) == ["end_turn"]
    assert phases("tool_use", one_block) == ["intermediate"]
    assert phases("end_turn", two_blocks) == ["intermediate", "end_turn"]
    assert phases(None, one_block) == ["intermediate"]


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
        harness=HarnessName.CLAUDE_CODE,
        source_type="otel",
        raw_event_id="otel-one",
    ))
    reports = payloads(translation, UsageReported)
    assert len(reports) == 1
    usage = reports[0].payload
    assert usage.model == ModelReference("claude-opus-4-8", "opus-4.8")
    assert usage.tokens.input_tokens == 10
    assert usage.tokens.cache_read_tokens == 7
    assert usage.cost_in_usd == Decimal("0.25")


def test_claude_otel_delivery_records_raw_and_canonical_audit(tmp_path):
    runtime, interpreter = interpreting_runtime(tmp_path / "main.db")
    session = Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
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

    # The receiver is a thin client now: it ships the bytes and the DAEMON
    # decides what they meant, so the gateway is what this exercises. Two
    # deliveries of the same export converge on one raw event, as before.
    telemetry = TelemetryGatewayService(
        runtime.sessions.harness_registry,
        runtime.recorder,
        runtime.sessions,
        SqliteAccountUsageRepository(runtime.database),
    )
    delivery = HarnessTelemetryRequest("otlp", raw_body)
    assert telemetry.record("claude_code", delivery) == 1
    assert telemetry.record("claude_code", delivery) == 1
    interpreter.tick()

    evidence = runtime.raw_event_audits.audits_for_session(SessionId("session-one"))
    assert len(evidence) == 1
    assert evidence[0].raw_event.payload == raw_body
    assert evidence[0].interpretation is not None
    assert evidence[0].interpretation.decision == "translated"
    assert len(evidence[0].interpretation.events) == 1
    reported = stored_payloads(runtime, SessionId("session-one"), UsageReported)
    assert [usage.tokens.output_tokens for usage in reported] == [9]


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
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="monitor",
    ))

    assert payloads(background, ShellStarted)[0].payload.execution == "background"
    assert payloads(background, ShellStarted)[0].payload.description == "Run tests"
    assert payloads(background, ShellStarted)[0].payload.command.text == "make test"
    assert payloads(monitor, ShellStarted)[0].payload.execution == "monitor"


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
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command", source_position="40"))
    initial_output = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "command-one",
            "output": json.dumps({"session_id": 77, "output": "waiting\n"}),
        },
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command-output", source_position="41"))
    provided = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "input-one",
            "input": 'tools.write_stdin({session_id:77,chars:"yes\\n",yield_time_ms:1000})',
        },
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="stdin", source_position="42"))
    continued_output = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "input-one",
            "output": "accepted\n",
        },
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="stdin-output", source_position="43"))
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
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command-finished", source_position="44"))

    shell_id = payloads(started, ShellStarted)[0].payload.shell_id
    assert shell_id == "command-one"
    assert payloads(initial_output, ShellProgressed)[0].payload.shell_id == shell_id
    input_payload = payloads(provided, ShellInputProvided)[0].payload
    assert input_payload.shell_id == shell_id
    assert input_payload.content.text == "yes\n"
    assert input_payload.closed is False
    assert payloads(continued_output, ShellProgressed)[0].payload.shell_id == shell_id
    finished_payload = payloads(finished, ShellFinished)[0].payload
    assert finished_payload.shell_id == shell_id
    assert finished_payload.result.text == "waiting\naccepted\n"
    # zero is a real exit code: a falsy-int coercion once dropped it and marked
    # the clean exit "failed" (session 01a009e1, 2026-08-16)
    assert finished_payload.exit_code == 0
    assert finished_payload.outcome == "succeeded"


def test_codex_command_completion_outcome_follows_the_integer_exit_code():
    translator = CodexCanonicalTranslator()
    for exit_code, expected_outcome, suffix in ((0, "succeeded", "ok"), (2, "failed", "bad")):
        translator.translate(raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": f"command-{suffix}",
                "input": 'tools.exec_command({"cmd":"run"})',
            },
        }, harness=HarnessName.CODEX, source_type="rollout",
            raw_event_id=f"command-{suffix}", source_position="40"))
        translator.translate(raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": f"command-{suffix}",
                "output": json.dumps({"session_id": suffix, "output": "running\n"}),
            },
        }, harness=HarnessName.CODEX, source_type="rollout",
            raw_event_id=f"command-output-{suffix}", source_position="41"))
        finished = translator.translate(raw_event({
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CommandExecution",
                    "id": f"execution-{suffix}",
                    "process_id": suffix,
                    "status": "completed",
                    "aggregated_output": "done\n",
                    "exit_code": exit_code,
                },
            },
        }, harness=HarnessName.CODEX, source_type="rollout",
            raw_event_id=f"command-finished-{suffix}", source_position="42"))

        finished_payload = payloads(finished, ShellFinished)[0].payload
        assert finished_payload.exit_code == exit_code
        assert finished_payload.outcome == expected_outcome


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
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command"))
    translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call_output",
            "call_id": "command-one",
            "output": json.dumps({"session_id": 88, "output": ""}),
        },
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command-output", source_position="11"))
    poll = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "poll-one",
            "input": 'tools.write_stdin({session_id:88,chars:"",yield_time_ms:1000})',
        },
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="poll", source_position="12"))
    interrupt = translator.translate(raw_event({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "interrupt-one",
            "input": 'tools.write_stdin({session_id:88,chars:"\\u0003",yield_time_ms:1000})',
        },
    }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="interrupt", source_position="13"))

    assert poll.decision == "ignored_nonsemantic"
    assert poll.canonical_events == ()
    assert payloads(interrupt, ShellInputProvided)[0].payload.content.text == "\x03"


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
        }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="stdin"))


def test_codex_write_stdin_records_raw_and_canonical_audit(tmp_path):
    runtime, interpreter = interpreting_runtime(tmp_path / "main.db")
    runtime.register("codex", Session(
        SessionId("session-one"),
        ActorId("session-one:lead"),
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
        }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command", source_position="40"),
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "command-one",
                "output": json.dumps({"session_id": 77, "output": ""}),
            },
        }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="command-output", source_position="41"),
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "poll-one",
                "input": 'tools.write_stdin({session_id:77,chars:""})',
            },
        }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="poll", source_position="42"),
        raw_event({
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "input-one",
                "input": 'tools.write_stdin({session_id:77,chars:"yes\\n"})',
            },
        }, harness=HarnessName.CODEX, source_type="rollout", raw_event_id="stdin", source_position="43"),
    )
    runtime.recorder.record(observations)
    interpreter.tick()

    stdin_evidence = runtime.raw_event_audits.audit(RawEventId("stdin"))
    assert stdin_evidence is not None
    assert stdin_evidence.raw_event.payload == observations[-1].payload
    assert stdin_evidence.interpretation is not None
    assert stdin_evidence.interpretation.decision == "translated"
    assert isinstance(
        stdin_evidence.interpretation.events[0].event.payload, ShellInputProvided
    )
    poll_evidence = runtime.raw_event_audits.audit(RawEventId("poll"))
    assert poll_evidence is not None
    assert poll_evidence.raw_event.payload == observations[-2].payload
    assert poll_evidence.interpretation is not None
    assert poll_evidence.interpretation.decision == "ignored_nonsemantic"
    assert poll_evidence.interpretation.events == ()


def test_codex_plan_has_a_canonical_fact():
    plan = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {"type": "Plan", "id": "plan-one", "text": "1. Change it"},
            },
        },
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="plan",
    ))

    proposed = payloads(plan, PlanProposed)[0].payload
    assert proposed.attention_id == "plan-one"
    assert proposed.plan.text == "1. Change it"


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
        harness=HarnessName.CODEX,
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
        harness=HarnessName.CODEX,
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
    # The patch itself is not a command and has no life of its own: the files it
    # touched are the whole fact.
    assert len(translated.canonical_events) == len(files)
    assert {file.outcome for file in files} == {"succeeded"}


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
        harness=HarnessName.CODEX,
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
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="wrapped-patch-output",
    ))

    assert translation.decision.startswith("ignored_")
    assert translation.canonical_events == ()


def test_codex_opaque_exec_output_does_not_create_a_finish_without_a_start():
    translator = CodexCanonicalTranslator()
    started = translator.translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "opaque-one",
                "input": "const hits = ALL_TOOLS.filter(x => x.name); text(hits);",
            },
        },
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="opaque-call",
        source_position="40",
    ))
    finished = translator.translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "opaque-one",
                "output": "Script completed\nOutput:\n[]",
            },
        },
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="opaque-output",
        source_position="41",
    ))

    assert started.canonical_events == ()
    assert finished.canonical_events == ()


@pytest.mark.parametrize(
    ("call_input", "expected_facts"),
    (
        # No `tools.<fn>(…)` at all: the output belongs to no call this
        # vocabulary has a fact for, and inventing one is worse than none.
        ("const hits = ALL_TOOLS.filter(x => x.name); text(hits);", 0),
        # A file read, whose PATH is in the call the scan recovered — without
        # it the result would be a fact about no file.
        ('text(await tools.view_image({path:"/tmp/image.png"}));', 1),
    ),
)
def test_codex_output_recovers_its_call_pairing_across_a_restart(
    tmp_path, call_input, expected_facts
):
    call = {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "restart-one",
            "input": call_input,
        },
    }
    call_line = json.dumps(call) + "\n"
    rollout_path = tmp_path / "rollout.jsonl"
    rollout_path.write_text(call_line)
    output = replace(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "restart-one",
                "output": "Script completed\nOutput:\nresult",
            },
        },
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="restart-output",
        source_position=str(len(call_line.encode())),
    ), source_name=str(rollout_path))

    translated = CodexCanonicalTranslator().translate(output)

    assert len(translated.canonical_events) == expected_facts
    if expected_facts:
        assert payloads(translated, FileAccessed)[0].payload.path == "/tmp/image.png"


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
        harness=HarnessName.CODEX,
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
            harness=HarnessName.CODEX,
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
    assert start_payload.actor_name == "bali weather"
    assert start_payload.prompt is None
    assert str(finish_payload.assignment_id) == "child-turn"
    assert finish_payload.result.text == "Rain, 24°C"
    assert not payloads(finished, ActorFinished)
    hook_raw = replace(
        raw_event(
            {"hook_event_name": "SubagentStop", "agent_id": "child-one"},
            harness=HarnessName.CODEX,
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
            harness=HarnessName.CODEX,
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
    # An actor-to-actor message IS a message: the actor speaking, to a named
    # recipient, carrying what the send_message call was given.
    message = payloads(sent, MessageCreated)[0].payload
    assert message.recipient_actor_id == ActorId("child-one")
    assert message.role == "assistant"
    assert message.content.text == "encrypted"

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
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="send-activity",
        source_position=str(len(call.encode())),
    ), source_name=str(rollout_path))

    message = payloads(
        CodexCanonicalTranslator().translate(activity),
        MessageCreated,
    )[0].payload
    assert message.recipient_actor_id == ActorId("child-one")
    # The text comes from the call the backwards scan recovered, so a restart
    # loses the correlation AND the words together, or neither.
    assert message.content.text == "encrypted"


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
        harness=HarnessName.CODEX,
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


def test_codex_web_tool_uses_shared_search_vocabulary(tmp_path):
    """One web tool covers search and fetch, and which one it was is decided by
    the fields it was called with — so the call names the tool and the result
    completes the fact."""
    call = json.dumps({
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "web-one",
            "input": (
                'const result = await tools.web__run('
                '{"search_query":"Bali weather"}); text(result);'
            ),
        },
    }) + "\n"
    rollout_path = tmp_path / "rollout.jsonl"
    rollout_path.write_text(call)
    translator = CodexCanonicalTranslator()
    opened = translator.translate(replace(
        raw_event(
            json.loads(call),
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="web-search",
            source_position="0",
        ),
        source_name=str(rollout_path),
    ))
    answered = translator.translate(replace(
        raw_event(
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "web-one",
                    "output": "26C and sunny",
                },
            },
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="web-search-result",
            source_position=str(len(call.encode())),
        ),
        source_name=str(rollout_path),
    ))

    assert opened.canonical_events == ()
    performed = payloads(answered, SearchPerformed)[0].payload
    assert performed.tool == "WebSearch"
    assert performed.query.text == "Bali weather"
    assert performed.result.text == "26C and sunny"


def test_codex_unmapped_tool_is_unknown_evidence_not_a_failure():
    """An unmapped tool is a hole in this translator, not bad evidence: the
    verdict says `ignored_unknown` so the audit can name it, and the rest of
    the session carries on."""
    translation = CodexCanonicalTranslator().translate(raw_event(
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "unknown-one",
                "input": "const result = await tools.unknown_tool({}); text(result);",
            },
        },
        harness=HarnessName.CODEX,
        source_type="rollout",
        raw_event_id="unknown-tool",
    ))

    assert translation.canonical_events == ()
    assert translation.decision == "ignored_unknown"
    assert "unmapped Codex tool" in translation.reason


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
                "tool_name": "Bash",
                "tool_input": {"command": "pwd"},
            },
            harness=HarnessName.CLAUDE_CODE,
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
                            "name": "Bash",
                            "input": {"command": "pwd"},
                        }
                    ],
                },
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="transcript",
            raw_event_id="transcript-start",
            observed_at=200.0,
        )
    )
    assert mapper.encode_canonical_event(payloads(hook, ShellStarted)[0]) == mapper.encode_canonical_event(
        payloads(transcript, ShellStarted)[0]
    )


def test_claude_file_facts_converge_from_either_evidence_stream():
    """A file's path is in the call and its diff is in the result, so the fact is
    built at result time from both. Either stream can carry it — the hook's own
    response, or the transcript's `toolUseResult` sidecar — and both spellings
    are the same fact."""
    response = {"content": "print(1)\n"}
    hook_translator = ClaudeCanonicalTranslator()
    transcript_translator = ClaudeCanonicalTranslator()
    call = {
        "type": "assistant",
        "uuid": "assistant-one",
        "message": {
            "id": "api-message",
            "content": [
                {"type": "tool_use", "id": "tool-one", "name": "Read", "input": {"file_path": "/work/a.py"}}
            ],
        },
    }
    for translator in (hook_translator, transcript_translator):
        translator.translate(raw_event(
            call, harness=HarnessName.CLAUDE_CODE, source_type="transcript", raw_event_id="start"
        ))
    hook = hook_translator.translate(raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "tool-one",
            "tool_name": "Read",
            "tool_input": {"file_path": "/work/a.py"},
            "tool_response": response,
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="hook-finish",
    ))
    transcript = transcript_translator.translate(raw_event(
        {
            "type": "user",
            "uuid": "result-one",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-one", "content": "print(1)\n"}
                ]
            },
            "toolUseResult": response,
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="transcript-finish",
    ))

    assert mapper.encode_canonical_event(payloads(hook, FileAccessed)[0]) == mapper.encode_canonical_event(
        payloads(transcript, FileAccessed)[0]
    )
    assert payloads(hook, FileAccessed)[0].payload.content.text == "print(1)\n"


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
        harness=HarnessName.CLAUDE_CODE,
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
    # The transcript's own tool_result names no tool, so the call it belongs to
    # has to have been seen. It always has been: the request precedes its result
    # in both streams.
    translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "tool-one",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="hook-start",
    ))
    hook_raw = raw_event(
        {
            "hook_event_name": "PostToolUse",
            "tool_use_id": "tool-one",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
            "tool_response": "output",
        },
        harness=HarnessName.CLAUDE_CODE,
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
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="transcript-finish",
    )
    hook = translator.translate(hook_raw)
    transcript = translator.translate(transcript_raw)
    hook_finished = payloads(hook, ShellFinished)[0]
    transcript_finished = payloads(transcript, ShellFinished)[0]
    assert mapper.encode_canonical_event(hook_finished) == mapper.encode_canonical_event(transcript_finished)

    store = CanonicalRuntime(str(tmp_path / "main.db"))
    store.register(
        "claude_code",
        Session(
            SessionId("session-one"),
            ActorId("session-one:lead"),
            "fixture.jsonl",
            "/work",
        ),
    )
    store.record(hook_raw, "1", hook)
    accepted = store.record(transcript_raw, "1", transcript)
    assert hook_finished.event_id not in {event.event_id for event in accepted}
    committed = store.store.page_from(0, 10)
    assert hook_finished.event_id in {item.event_id for item in committed}
    finished = store.store.find(hook_finished.event_id)
    assert finished is not None
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
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="ask",
        )
    )
    asked = payloads(translation, QuestionAsked)[0].payload
    assert len(asked.questions) == 2
    assert asked.questions[0].multiple is True
    assert [choice.label for choice in asked.questions[0].choices] == ["Python", "JavaScript"]


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
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="ask-answer",
        )
    )

    answered = payloads(translation, QuestionAnswered)[0].payload

    assert answered.answers[0].prompt_id == "language"
    # Labels, not values: both harnesses answer with the label they were shown,
    # so a second spelling of the same string was a mapping nobody needed.
    assert answered.answers[0].labels == ("Python", "JavaScript")
    assert not hasattr(answered, "tool_response")


def test_claude_refused_question_resolves_from_the_transcript_not_a_missing_hook():
    """A refused tool call never runs, so Claude Code fires no PostToolUse for it. The
    transcript's tool_result is the only evidence the question ended — and it names no
    tool, so the resolution depends on remembering the id from the request."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "question-refused",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Which approach?", "options": []}]},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="ask-refused",
    ))

    refusal = translator.translate(raw_event(
        {
            "type": "user",
            "uuid": "question-refused-result",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "question-refused",
                        "is_error": True,
                        "content": (
                            "The user doesn't want to proceed with this tool use. "
                            "The tool use was rejected. To tell you how to proceed, "
                            "the user said:\nThe user wants to clarify these questions."
                        ),
                    }
                ],
            },
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="ask-refused-result",
    ))

    # A refusal answers nothing, and the harness's own word for the refusal
    # (rejected, discussed) is deliberately not carried: every reader collapsed
    # all of them to one line.
    answered = payloads(refusal, QuestionAnswered)[0].payload
    assert answered.attention_id == "question-refused"
    assert answered.answers == ()
    assert answered.feedback is None


def test_claude_answered_question_leaves_the_transcript_result_to_the_hook():
    """The hook's resolution carries the ANSWERS; the transcript's tool_result cannot.
    Both would converge on one event_id where the first writer wins, so the transcript
    must stay silent on a question that succeeded."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "tool_use_id": "question-answered",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{"question": "Which approach?", "options": []}]},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="ask-answered",
    ))

    result = translator.translate(raw_event(
        {
            "type": "user",
            "uuid": "question-answered-result",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "question-answered",
                        "content": "The user answered: vulture wrapper",
                    }
                ],
            },
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="transcript",
        raw_event_id="ask-answered-result",
    ))

    assert payloads(result, QuestionAnswered) == []


def test_codex_session_turn_operation_usage_and_context_records():
    translator = CodexCanonicalTranslator()
    session = translator.translate(
        raw_event(
            {"type": "session_meta", "payload": {"cwd": "/work", "originator": "codex-tui"}},
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="session",
            source_position="0",
        )
    )
    turn = translator.translate(
        raw_event(
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-one"}},
            harness=HarnessName.CODEX,
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
            harness=HarnessName.CODEX,
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
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="usage",
        )
    )
    assert isinstance(session.canonical_events[0].payload, SessionStarted)
    assert isinstance(session.canonical_events[1].payload, ActorStarted)
    assert isinstance(turn.canonical_events[0].payload, TurnStarted)
    assert isinstance(operation.canonical_events[0].payload, ShellStarted)
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
                        "turn_id": "turn-one",
                        "create_time": 1787403595.261263,
                    },
                },
            },
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="message",
        )
    )

    assert translation.canonical_events[0].turn_id == TurnId("turn-one")


def test_codex_source_can_attach_an_actor_to_another_harness_session():
    nested_raw_event = raw_event(
        {"type": "session_meta", "payload": {"cwd": "/work", "originator": "codex-exec"}},
        harness=HarnessName.CODEX,
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
        harness=HarnessName.CODEX,
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
            harness=HarnessName.CODEX,
            source_type="rollout",
            raw_event_id="ask",
        )
    )
    asked = payloads(translation, QuestionAsked)[0].payload
    assert asked.questions[0].prompt == "Continue?"
    assert asked.questions[0].choices[0].description == "Proceed"


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
                    harness=HarnessName.CLAUDE_CODE,
                    source_type="transcript",
                    raw_event_id="slash-" + uuid,
                )
            ).canonical_events
        )
    return events


def test_claude_slash_model_command_turn_is_the_state_change_not_a_prompt_bubble():
    # Three raw transcript records (the caveat, the envelope, the echoed
    # stdout) collapse into ONE canonical fact: the model change itself. No
    # prompt bubble either — the dashboard's own model-change block shows the
    # switch, so echoing "/model opus" as a second, redundant message would
    # just duplicate it (and, with nothing to close the turn, permanently
    # stick the tab on "thinking" — see tabstate.py).
    events = _slash_turn_events()
    assert not [e.payload for e in events if isinstance(e.payload, MessageCreated)]
    models = [e.payload for e in events if isinstance(e.payload, ModelChanged)]
    assert len(models) == 1


def test_claude_slash_model_reports_the_selection_at_the_moment_it_was_made():
    models = [e.payload for e in _slash_turn_events() if isinstance(e.payload, ModelChanged)]
    assert len(models) == 1
    assert models[0].reason == "selected"
    # the transcript carries the ALIAS here; the native id arrives a turn later
    # on the next assistant record, as `reported_by_harness`
    assert models[0].current.name == "opus"


def test_claude_slash_effort_reports_the_selection():
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {"type": "user", "uuid": "eff", "message": {"content":
                "<command-name>/effort</command-name><command-args>high</command-args>"}},
            harness=HarnessName.CLAUDE_CODE,
            source_type="transcript",
            raw_event_id="slash-effort",
        )
    )
    assert payloads(translation, EffortChanged)[0].payload.current == "high"
    assert payloads(translation, EffortChanged)[0].payload.reason == "selected"


def test_claude_subagent_hook_reports_its_own_effort():
    # launch_selections() and a typed /effort only ever see the LEAD actor; a
    # hook firing mid-turn inside the subagent's own process is the only place
    # its effort level is ever observed from.
    translator = ClaudeCanonicalTranslator()
    translator.translate(replace(
        raw_event(
            {
                "hook_event_name": "SubagentStart",
                "hook_event_id": "child-start",
                "agent_id": "child-one",
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="child-start-hook",
        ),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("session-one:lead"),
    ))
    pretool = translator.translate(replace(
        raw_event(
            {
                "hook_event_name": "PreToolUse",
                "hook_event_id": "child-pretool",
                "tool_use_id": "tool-one",
                "tool_name": "Read",
                "tool_input": {"file_path": "/work/a.py"},
                "effort": {"level": "high"},
            },
            harness=HarnessName.CLAUDE_CODE,
            source_type="hook",
            raw_event_id="child-pretool-hook",
        ),
        actor_id=ActorId("child-one"),
        parent_actor_id=ActorId("session-one:lead"),
    ))

    effort_events = payloads(pretool, EffortChanged)
    assert len(effort_events) == 1
    assert effort_events[0].actor_id == ActorId("child-one")
    assert effort_events[0].payload.current == "high"
    assert effort_events[0].payload.reason == "reported_by_harness"


def test_claude_pretool_without_effort_reports_no_effort_change():
    translation = ClaudeCanonicalTranslator().translate(raw_event(
        {
            "hook_event_name": "PreToolUse",
            "hook_event_id": "no-effort-pretool",
            "tool_use_id": "tool-two",
            "tool_name": "Read",
            "tool_input": {"file_path": "/work/b.py"},
        },
        harness=HarnessName.CLAUDE_CODE,
        source_type="hook",
        raw_event_id="no-effort-hook",
    ))

    assert not payloads(translation, EffortChanged)


def test_claude_argless_slash_command_settles_no_state():
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {"type": "user", "uuid": "bare", "message": {"content":
                "<command-name>/model</command-name>"}},
            harness=HarnessName.CLAUDE_CODE,
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
                harness=HarnessName.CLAUDE_CODE,
                source_type="transcript",
                raw_event_id="quote-" + content[:6],
            )
        )
        message = payloads(translation, MessageCreated)[0].payload
        assert message.role == "user"
        assert message.content.text == content
        assert not payloads(translation, ModelChanged)


# The SSE decoder lived here as a unit test of `core/daemon/client.py`. That module
# is gone: decoding a pane stream is the pane's own loop now
# (`client/terminal_pane.py`), and it is checked end to end — a stub daemon, a real
# process, a frame on its stdout — in tests/test_canonical_clients.py.
