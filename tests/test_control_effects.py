"""Confirmed control effects use the canonical event pipeline."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from audit.recorder import AuditRecorder
from domain.entries import (
    AssignmentStartedBody,
    PlanProposedBody,
    SessionEntry,
    ShellStartedBody,
    TurnStartedBody,
)
from domain.events import (
    ActorAssignmentFinished,
    ActorStarted,
    CanonicalEvent,
    EffortChanged,
    EventPayload,
    MessageCreated,
    ModelChanged,
    PlanResolved,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    ShellFinished,
    TurnAborted,
    TurnFinished,
)
from domain.ids import (
    ActorId,
    AssignmentId,
    AttentionId,
    CanonicalEventId,
    HarnessName,
    MessageId,
    RequestId,
    RawEventId,
    SessionId,
    ShellId,
    TurnId,
    WindowId,
)
from domain.values import (
    ActorRole,
    ExecutionMode,
    EffortChangeReason,
    MessagePhase,
    MessageRole,
    ModelChangeReason,
    ModelReference,
    Outcome,
    PlanState,
    TextContent,
    TitleOrigin,
)
from domain.workspace import ComposerQueue, QueuedMessage, SessionWorkspace
from dashboard.services.workspace import QueuedPromptCanonicalEventReaction
from engine.interpret.translators import (
    ControlTranslator,
    ResumeLivenessTranslator,
    SessionResumeTranslator,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models import (
    AttachmentReference,
    CloseSession,
    plan_resolution_phase,
    ControlAcknowledgement,
    DecidePlan,
    DeliveryResult,
    DurableTitleResult,
    RenameSession,
    SelectEffort,
    SelectModel,
    SendText,
    Session,
)
from harness.contract import HarnessPlugin
from harness.models.raw_events import RawEvent
from harness.services.control_effects import ControlEffectRecorder
from harness.services.controls import HarnessControlService
from harness.services.launch_effects import SessionLaunchEffectRecorder
from repository.contract.facts import RawEventRepository
from repository.contract.session_data import SessionDataRepository
from repository.contract.sessions import SessionRepository
from repository.contract.workspace import SessionWorkspaceRepository


class RawEvents:
    def __init__(self) -> None:
        self.items: list[RawEvent] = []

    def record(self, raw_events) -> None:
        self.items.extend(raw_events)


class Workspaces:
    def __init__(self) -> None:
        self.queued: list[tuple[SessionId, QueuedMessage, str]] = []

    def enqueue_composer_message(self, session_id, message, origin) -> None:
        self.queued.append((session_id, message, origin))


class SessionEntries:
    def __init__(self, entries=()) -> None:
        self.entries = tuple(entries)

    def entries_of_types(self, _session_id, _entry_types):
        return self.entries


class Sessions:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find(self, session_id: SessionId) -> Session | None:
        return self.session if self.session.session_id == session_id else None


def test_a_confirmed_resume_launch_reopens_the_exact_session_and_lead() -> None:
    raw_events = RawEvents()
    session = Session(
        SessionId("session-one"),
        ActorId("actor-one"),
        "/rollouts/session-one.jsonl",
        "/work",
    )
    recorder = SessionLaunchEffectRecorder(
        cast(RawEventRepository, raw_events),
        cast(SessionRepository, Sessions(session)),
    )

    recorder.resumed(
        HarnessName.CODEX,
        session.session_id,
        WindowId("window-two"),
    )

    assert len(raw_events.items) == 1
    raw_event = raw_events.items[0]
    assert raw_event.session_id == session.session_id
    assert raw_event.actor_id == session.lead_actor_id
    assert raw_event.terminal_window_id == "window-two"
    assert raw_event.harness_process_id is None
    translated = SessionResumeTranslator().translate(raw_event)
    assert len(translated.canonical_events) == 2
    assert translated.canonical_events[0].payload == SessionStarted(
        "/work",
        "/rollouts/session-one.jsonl",
        session.session_id,
        None,
        None,
        None,
        None,
    )
    assert translated.canonical_events[1].payload == ActorStarted(
        "lead",
        ActorRole.LEAD,
    )


def test_a_native_start_hook_and_resume_observation_share_one_run_identity() -> None:
    raw_events = RawEvents()
    session = Session(
        SessionId("session-one"),
        ActorId("actor-one"),
        "/transcripts/session-one.jsonl",
        "/work",
    )
    recorder = SessionLaunchEffectRecorder(
        cast(RawEventRepository, raw_events),
        cast(SessionRepository, Sessions(session)),
    )
    recorder.resumed(
        HarnessName.CLAUDE_CODE,
        session.session_id,
        WindowId("window-two"),
    )
    resumed = SessionResumeTranslator().translate(raw_events.items[0])
    native_raw_event = replace(
        raw_events.items[0],
        raw_event_id=RawEventId("native-start-two"),
        source_type="hook",
        source_name="SessionStart",
        source_position="native-start-two",
        payload=json.dumps(
            {
                "session_id": "session-one",
                "transcript_path": "/transcripts/session-one.jsonl",
                "cwd": "/work",
                "hook_event_name": "SessionStart",
                "hook_event_id": "native-start-two",
            }
        ).encode(),
    )
    native = ClaudeCanonicalTranslator().translate(native_raw_event)

    assert [event.event_id for event in native.canonical_events] == [
        event.event_id for event in resumed.canonical_events
    ]

    another_run = ClaudeCanonicalTranslator().translate(
        replace(
            native_raw_event,
            raw_event_id=RawEventId("native-start-three"),
            source_position="native-start-three",
            terminal_window_id=WindowId("window-three"),
        )
    )
    assert [event.event_id for event in another_run.canonical_events] != [
        event.event_id for event in native.canonical_events
    ]

    native_end_raw_event = replace(
        native_raw_event,
        raw_event_id=RawEventId("native-end-two"),
        source_name="SessionEnd",
        source_position="native-end-two",
        payload=json.dumps(
            {
                "session_id": "session-one",
                "transcript_path": "/transcripts/session-one.jsonl",
                "cwd": "/work",
                "hook_event_name": "SessionEnd",
                "hook_event_id": "native-end-two",
            }
        ).encode(),
    )
    native_end = ClaudeCanonicalTranslator().translate(native_end_raw_event)
    liveness_end = ResumeLivenessTranslator().translate(
        replace(
            raw_events.items[0],
            raw_event_id=RawEventId("resume-liveness-two"),
            source_type="resume_liveness",
            source_position="window-two:closed",
            payload=b"",
        )
    )
    assert native_end.canonical_events[0].event_id == liveness_end.canonical_events[0].event_id


class DurableQueue:
    def __init__(self) -> None:
        self.session_id = SessionId("session-one")
        self.items = [
            QueuedMessage(RequestId("request-one"), "same prompt"),
            QueuedMessage(RequestId("request-two"), "same prompt"),
        ]
        self.removed: list[str] = []

    def find(self, session_id):
        assert session_id == self.session_id
        return SessionWorkspace(
            session_id,
            queue=ComposerQueue(tuple(self.items), "send"),
        )

    def remove_queued_message(self, session_id, request_id) -> None:
        assert session_id == self.session_id
        self.removed.append(request_id)
        self.items = [item for item in self.items if item.request_id != request_id]


@pytest.mark.parametrize(
    ("decision", "feedback", "state"),
    (
        ("1", None, PlanState.APPROVED),
        ("dismiss", None, PlanState.REJECTED),
        ("feedback", "start with tests", PlanState.CHANGES_REQUESTED),
    ),
)
def test_confirmed_plan_decision_becomes_one_canonical_resolution(
    decision,
    feedback,
    state,
):
    raw_events = RawEvents()
    recorder = ControlEffectRecorder(
        cast(RawEventRepository, raw_events),
        cast(SessionWorkspaceRepository, Workspaces()),
        cast(SessionDataRepository, SessionEntries()),
    )
    session_id = SessionId("session-one")
    actor_id = ActorId("actor-one")
    turn_id = TurnId("turn-one")
    attention_id = AttentionId("plan-one")
    session = Session(
        session_id,
        actor_id,
        "rollout.jsonl",
        "/work",
        plugin=cast(
            HarnessPlugin,
            SimpleNamespace(info=SimpleNamespace(name=HarnessName.CODEX)),
        ),
    )
    pending = SessionEntry(
        CanonicalEventId("plan-proposed"),
        session_id,
        actor_id,
        None,
        turn_id,
        1.0,
        None,
        PlanProposedBody(attention_id, TextContent("do the work")),
    )
    request = DecidePlan(
        session_id,
        RequestId("request-one"),
        attention_id,
        decision,
        feedback,
    )

    recorder.plan_decided(session, request, pending)

    assert len(raw_events.items) == 1
    raw_event = raw_events.items[0]
    translation = ControlTranslator().translate(raw_event)
    assert len(translation.canonical_events) == 1
    event = translation.canonical_events[0]
    assert event.actor_id == actor_id
    assert event.turn_id == turn_id
    assert event.payload == PlanResolved(attention_id, state, feedback, False)


def test_plan_feedback_is_a_newer_revision_than_a_generic_rejection() -> None:
    attention_id = AttentionId("plan-one")
    generic = PlanResolved(attention_id, PlanState.REJECTED, None, False)
    feedback = PlanResolved(
        attention_id,
        PlanState.CHANGES_REQUESTED,
        "start with tests",
        False,
    )

    assert plan_resolution_phase(generic) != plan_resolution_phase(feedback)


def test_confirmed_parked_rename_becomes_one_canonical_title_change() -> None:
    raw_events = RawEvents()
    recorder = ControlEffectRecorder(
        cast(RawEventRepository, raw_events),
        cast(SessionWorkspaceRepository, Workspaces()),
        cast(SessionDataRepository, SessionEntries()),
    )
    session_id = SessionId("session-one")
    actor_id = ActorId("actor-one")
    session = Session(
        session_id,
        actor_id,
        "rollout.jsonl",
        "/work",
        plugin=cast(
            HarnessPlugin,
            SimpleNamespace(info=SimpleNamespace(name=HarnessName.CODEX)),
        ),
    )
    request = RenameSession(
        session_id,
        RequestId("request-one"),
        "Parked title",
    )

    recorder.session_renamed(session, request)

    assert len(raw_events.items) == 1
    raw_event = raw_events.items[0]
    assert raw_event.source_name == "session_rename"
    translation = ControlTranslator().translate(raw_event)
    assert len(translation.canonical_events) == 1
    event = translation.canonical_events[0]
    assert event.actor_id == actor_id
    assert event.turn_id is None
    assert event.payload == SessionTitleChanged(
        "Parked title",
        TitleOrigin.CUSTOM,
    )


@pytest.mark.parametrize(
    ("selection_request", "source_name", "expected"),
    (
        (
            SelectModel(SessionId("session-one"), RequestId("model-one"), "sonnet"),
            "model_selection",
            ModelChanged(
                None,
                ModelReference("sonnet", "sonnet"),
                ModelChangeReason.SELECTED,
            ),
        ),
        (
            SelectEffort(SessionId("session-one"), RequestId("effort-one"), "medium"),
            "effort_selection",
            EffortChanged(None, "medium", EffortChangeReason.SELECTED),
        ),
    ),
)
def test_confirmed_selections_become_canonical_state_changes(
    selection_request,
    source_name,
    expected,
) -> None:
    raw_events = RawEvents()
    recorder = ControlEffectRecorder(
        cast(RawEventRepository, raw_events),
        cast(SessionWorkspaceRepository, Workspaces()),
        cast(SessionDataRepository, SessionEntries()),
    )
    session = Session(
        SessionId("session-one"),
        ActorId("actor-one"),
        "transcript.jsonl",
        "/work",
        plugin=cast(
            HarnessPlugin,
            SimpleNamespace(info=SimpleNamespace(name=HarnessName.CLAUDE_CODE)),
        ),
    )

    recorder.selection_changed(session, selection_request)

    assert len(raw_events.items) == 1
    raw_event = raw_events.items[0]
    assert raw_event.source_name == source_name
    translated = ControlTranslator().translate(raw_event)
    assert [event.payload for event in translated.canonical_events] == [expected]


@pytest.mark.parametrize(
    ("text", "attachments", "expected"),
    (
        ("do this next", (), "do this next"),
        (
            "",
            (AttachmentReference("/tmp/input.txt", "input.txt"),),
            "/tmp/input.txt",
        ),
    ),
)
def test_an_accepted_mid_turn_send_is_saved_by_request_identity(
    text,
    attachments,
    expected,
):
    workspaces = Workspaces()
    recorder = ControlEffectRecorder(
        cast(RawEventRepository, RawEvents()),
        cast(SessionWorkspaceRepository, workspaces),
        cast(SessionDataRepository, SessionEntries()),
    )
    request = SendText(
        SessionId("session-one"),
        RequestId("request-one"),
        text=text,
        attachments=attachments,
    )

    recorder.text_queued(request)

    assert len(workspaces.queued) == 1
    session_id, message, origin = workspaces.queued[0]
    assert session_id == "session-one"
    assert message.request_id == "request-one"
    assert message.text == expected
    assert origin == "send"


def test_a_confirmed_close_cancels_each_open_work_identity():
    session_id = SessionId("session-one")
    lead_id = ActorId("session-one:lead")
    child_id = ActorId("child-one")
    turn_id = TurnId("turn-one")
    shell_id = ShellId("shell-one")
    assignment_id = AssignmentId("assignment-one")
    entries = (
        SessionEntry(
            CanonicalEventId("turn-started"),
            session_id,
            child_id,
            lead_id,
            turn_id,
            1.0,
            None,
            TurnStartedBody(),
        ),
        SessionEntry(
            CanonicalEventId("shell-started"),
            session_id,
            child_id,
            lead_id,
            turn_id,
            2.0,
            None,
            ShellStartedBody(
                shell_id,
                TextContent("sleep 30"),
                ExecutionMode.FOREGROUND,
            ),
        ),
        SessionEntry(
            CanonicalEventId("assignment-started"),
            session_id,
            child_id,
            lead_id,
            turn_id,
            3.0,
            None,
            AssignmentStartedBody(assignment_id),
        ),
    )
    raw_events = RawEvents()
    recorder = ControlEffectRecorder(
        cast(RawEventRepository, raw_events),
        cast(SessionWorkspaceRepository, Workspaces()),
        cast(SessionDataRepository, SessionEntries(entries)),
    )
    session = Session(
        session_id,
        lead_id,
        "rollout.jsonl",
        "/work",
        plugin=cast(
            HarnessPlugin,
            SimpleNamespace(info=SimpleNamespace(name=HarnessName.CODEX)),
        ),
    )

    observations = recorder.work_before_close(session_id)
    recorder.session_closed(
        session,
        CloseSession(session_id, RequestId("close-one")),
        observations,
    )

    assert len(raw_events.items) == 4
    translated = [ControlTranslator().translate(item).canonical_events[0] for item in raw_events.items]
    assert [type(item.payload) for item in translated] == [
        SessionFinished,
        TurnAborted,
        ShellFinished,
        ActorAssignmentFinished,
    ]
    assert translated[0].actor_id == lead_id
    assert all(item.actor_id == child_id for item in translated[1:])
    assert all(item.parent_actor_id == lead_id for item in translated[1:])
    assert all(item.turn_id == turn_id for item in translated[1:])
    assert translated[2].payload.outcome == Outcome.CANCELLED
    assert translated[3].payload.outcome == Outcome.CANCELLED


@pytest.mark.parametrize(
    ("status", "queued", "saved"),
    (
        (ControlAcknowledgement.ACKNOWLEDGED, True, True),
        (ControlAcknowledgement.ACKNOWLEDGED, False, False),
        (ControlAcknowledgement.INDETERMINATE, True, False),
    ),
)
def test_only_an_acknowledged_queued_send_is_persisted(
    monkeypatch,
    status,
    queued,
    saved,
):
    recorded = []
    service = object.__new__(HarnessControlService)
    service.audit = cast(
        AuditRecorder,
        SimpleNamespace(state_file=lambda *_args, **_kwargs: None),
    )
    service.control_effects = cast(
        ControlEffectRecorder,
        SimpleNamespace(text_queued=lambda request: recorded.append(request)),
    )
    service.sessions = cast(
        SessionRepository,
        SimpleNamespace(find=lambda _session_id: None),
    )
    monkeypatch.setattr(
        service,
        "_execute",
        lambda request: DeliveryResult(
            request.request_id,
            status,
            queued=queued,
        ),
    )
    monkeypatch.setattr(service, "_lead_active", lambda _session_id: True)
    request = SendText(
        SessionId("session-one"),
        RequestId("request-one"),
        text="do this next",
    )

    service.send_text(request)

    assert recorded == ([request] if saved else [])


def test_a_send_that_misses_the_turn_boundary_drains_after_it_is_persisted(
    monkeypatch,
) -> None:
    recorded = []
    delivered = []
    executions: list[bool | None] = []
    service = object.__new__(HarnessControlService)
    service.audit = cast(
        AuditRecorder,
        SimpleNamespace(state_file=lambda *_args, **_kwargs: None),
    )
    service.control_effects = cast(
        ControlEffectRecorder,
        SimpleNamespace(
            text_queued=lambda request: recorded.append(request),
            text_delivered=lambda request: delivered.append(request),
        ),
    )
    service.sessions = cast(
        SessionRepository,
        SimpleNamespace(find=lambda _session_id: None),
    )

    def execute(_request, *, lead_active=None):
        executions.append(lead_active)
        return DeliveryResult(
            RequestId("request-one"),
            ControlAcknowledgement.ACKNOWLEDGED,
            queued=lead_active is not False,
        )

    monkeypatch.setattr(service, "_execute", execute)
    monkeypatch.setattr(service, "_lead_active", lambda _session_id: False)
    request = SendText(
        SessionId("session-one"),
        RequestId("request-one"),
        text="do this next",
    )

    outcome = service.send_text(request)

    assert isinstance(outcome, DeliveryResult)
    assert outcome.queued is True
    assert recorded == [request]
    assert delivered == [request]
    assert executions == [None, False]


def test_a_drained_slash_command_is_not_sent_at_a_later_turn_boundary(
    monkeypatch,
) -> None:
    request = SendText(
        SessionId("session-one"),
        RequestId("request-one"),
        text="/plan",
    )
    queue = DurableQueue()
    queue.items = [QueuedMessage(request.request_id, request.text)]
    executions = []
    service = object.__new__(HarnessControlService)
    service.audit = cast(
        AuditRecorder,
        SimpleNamespace(state_file=lambda *_args, **_kwargs: None),
    )
    service.sessions = cast(
        SessionRepository,
        SimpleNamespace(find=lambda _session_id: None),
    )
    service.control_effects = ControlEffectRecorder(
        cast(RawEventRepository, RawEvents()),
        cast(SessionWorkspaceRepository, queue),
        cast(SessionDataRepository, SessionEntries()),
    )

    def execute(sent, *, lead_active=None):
        executions.append((sent, lead_active))
        return DeliveryResult(
            sent.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
            queued=False,
        )

    monkeypatch.setattr(service, "_execute", execute)
    reaction = QueuedPromptCanonicalEventReaction(
        cast(SessionWorkspaceRepository, queue),
        service,
    )
    finished: CanonicalEvent[EventPayload] = CanonicalEvent(
        event_id=CanonicalEventId("turn-finished"),
        session_id=request.session_id,
        actor_id=ActorId("actor-one"),
        turn_id=TurnId("turn-one"),
        parent_actor_id=None,
        harness=HarnessName.CODEX,
        occurred_at=2.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=TurnFinished(None, Outcome.SUCCEEDED),
    )

    reaction.react(finished)
    reaction.react(replace(finished, event_id=CanonicalEventId("turn-finished-again")))

    assert executions == [(request, False)]
    assert queue.items == []


def test_a_native_queue_is_not_submitted_again_at_the_turn_boundary(
    monkeypatch,
) -> None:
    session = Session(
        SessionId("session-one"),
        ActorId("actor-one"),
        "transcript.jsonl",
        "/work",
        plugin=cast(
            HarnessPlugin,
            SimpleNamespace(
                info=SimpleNamespace(supports_native_text_queue=True),
            ),
        ),
    )
    service = object.__new__(HarnessControlService)
    service.sessions = cast(SessionRepository, Sessions(session))
    monkeypatch.setattr(
        service,
        "_audited",
        lambda *_args, **_kwargs: pytest.fail("native queued text was resubmitted"),
    )

    outcome = service.send_queued_text(
        SendText(session.session_id, RequestId("request-one"), "next prompt")
    )

    assert isinstance(outcome, DeliveryResult)
    assert outcome.status == ControlAcknowledgement.ACKNOWLEDGED
    assert outcome.queued is True


def test_only_a_confirmed_durable_rename_is_recorded(monkeypatch) -> None:
    session = Session(
        SessionId("session-one"),
        ActorId("actor-one"),
        "rollout.jsonl",
        "/work",
    )
    recorded = []
    service = object.__new__(HarnessControlService)
    service.audit = cast(
        AuditRecorder,
        SimpleNamespace(state_file=lambda *_args, **_kwargs: None),
    )
    service.sessions = cast(SessionRepository, Sessions(session))
    service.control_effects = cast(
        ControlEffectRecorder,
        SimpleNamespace(
            session_renamed=lambda found, request: recorded.append((found, request)),
        ),
    )
    request = RenameSession(
        session.session_id,
        RequestId("request-one"),
        "Parked title",
    )
    monkeypatch.setattr(
        service,
        "_execute",
        lambda _request: DurableTitleResult(
            request.request_id,
            ControlAcknowledgement.ACKNOWLEDGED,
        ),
    )

    service.rename_session(request)

    assert recorded == [(session, request)]


def test_each_native_prompt_consumes_only_one_equal_queued_send():
    workspaces = DurableQueue()
    reaction = QueuedPromptCanonicalEventReaction(workspaces)

    for ordinal in (1, 2):
        reaction.react(
            CanonicalEvent(
                event_id=CanonicalEventId(f"prompt-{ordinal}"),
                session_id=SessionId("session-one"),
                actor_id=ActorId("actor-one"),
                turn_id=TurnId(f"turn-{ordinal}"),
                parent_actor_id=None,
                harness=HarnessName.CODEX,
                occurred_at=float(ordinal),
                terminal_window_id=None,
                harness_process_id=None,
                payload=MessageCreated(
                    MessageId(f"message-{ordinal}"),
                    MessageRole.USER,
                    TextContent("same prompt"),
                    MessagePhase.PROMPT,
                    None,
                ),
            )
        )

    assert workspaces.removed == ["request-one", "request-two"]
    assert workspaces.items == []


def test_a_queued_codex_prompt_is_submitted_after_the_active_turn_finishes():
    workspaces = DurableQueue()
    sent = []
    reaction = QueuedPromptCanonicalEventReaction(
        workspaces,
        SimpleNamespace(send_queued_text=lambda request: sent.append(request)),
    )
    reaction.react(
        CanonicalEvent(
            event_id=CanonicalEventId("active-turn-finished"),
            session_id=SessionId("session-one"),
            actor_id=ActorId("actor-one"),
            turn_id=TurnId("turn-one"),
            parent_actor_id=None,
            harness=HarnessName.CODEX,
            occurred_at=2.0,
            terminal_window_id=None,
            harness_process_id=None,
            payload=TurnFinished(None, Outcome.SUCCEEDED),
        )
    )

    assert len(sent) == 1
    assert sent[0].request_id == "request-one"
    assert sent[0].text == "same prompt"
    assert workspaces.removed == []
