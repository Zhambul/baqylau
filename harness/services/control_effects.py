"""Record confirmed control effects as raw observations."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

from domain.entries import (
    AssignmentFinishedBody,
    AssignmentStartedBody,
    EntryTypeName,
    SessionEntry,
    ShellFinishedBody,
    ShellStartedBody,
    TurnFinishedBody,
    TurnStartedBody,
)
from domain.ids import AssignmentId, RawEventId, SessionId, ShellId, TurnId
from domain.values import OpenWorkKind, PlanState, TitleOrigin
from harness.models import (
    CONTROL_SOURCE_TYPE,
    CloseSession,
    DecidePlan,
    RawEvent,
    RenameSession,
    SelectEffort,
    SelectModel,
    SendText,
    Session,
)
from harness.models.directives import (
    EffortSelectionObservation,
    ModelSelectionObservation,
    MessageQueueObservation,
    PlanDecisionObservation,
    ProcessExit,
    ProcessExitState,
    SessionCloseWorkObservation,
    SessionRenameObservation,
)
from repository.contract.facts import RawEventRepository
from repository.contract.session_data import SessionDataRepository
from repository.mapper.documents import encode_document


@dataclass(frozen=True)
class SessionCloseWork:
    entry: SessionEntry
    observation: SessionCloseWorkObservation


class ControlEffectRecorder:
    """Make confirmed control effects durable for the interpreter."""

    def __init__(
        self,
        raw_event_repository: RawEventRepository,
        session_data_repository: SessionDataRepository,
    ) -> None:
        self.raw_events = raw_event_repository
        self.session_data = session_data_repository

    def message_queued(self, session: Session, send_text: SendText) -> None:
        """Record a queue acceptance that the harness confirmed."""
        if session.plugin is None:
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        text = self._message_text(send_text)
        if not text:
            return
        harness = session.plugin.info.name
        identity = f"{harness}:control:{send_text.session_id}:{send_text.request_id}:message_queued"
        self.raw_events.record(
            (
                RawEvent(
                    raw_event_id=RawEventId(identity),
                    harness=harness,
                    source_type=CONTROL_SOURCE_TYPE,
                    source_name="message_queued",
                    source_position=str(send_text.request_id),
                    session_id=send_text.session_id,
                    actor_id=session.lead_actor_id,
                    parent_actor_id=None,
                    observed_at=time.time(),
                    encoding="json",
                    payload=encode_document(MessageQueueObservation(send_text.request_id, text)),
                    source_identity=f"{harness}:control:{send_text.session_id}",
                ),
            )
        )

    @staticmethod
    def _message_text(send_text: SendText) -> str:
        attachments = " ".join(
            attachment.local_path for attachment in send_text.attachments
        )
        text = attachments + ("\n" if attachments and send_text.text else "")
        return (text + send_text.text).strip()

    def plan_decided(
        self,
        session: Session,
        decide_plan: DecidePlan,
        pending_session_entry: SessionEntry,
    ) -> None:
        if session.plugin is None:
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        state = (
            PlanState.CHANGES_REQUESTED
            if decide_plan.feedback is not None
            else PlanState.REJECTED
            if decide_plan.decision == "dismiss"
            else PlanState.APPROVED
        )
        observed_at = time.time()
        identity = f"{session.plugin.info.name}:control:{decide_plan.session_id}:{decide_plan.request_id}"
        self.raw_events.record(
            (
                RawEvent(
                    raw_event_id=RawEventId(identity),
                    harness=session.plugin.info.name,
                    source_type=CONTROL_SOURCE_TYPE,
                    source_name="plan_decision",
                    source_position=str(decide_plan.request_id),
                    session_id=decide_plan.session_id,
                    actor_id=pending_session_entry.actor_id,
                    parent_actor_id=pending_session_entry.parent_actor_id,
                    observed_at=observed_at,
                    encoding="json",
                    payload=encode_document(
                        PlanDecisionObservation(
                            attention_id=decide_plan.attention_id,
                            state=state,
                            feedback=decide_plan.feedback,
                            edited=False,
                            turn_id=pending_session_entry.turn_id,
                        )
                    ),
                    source_identity=(f"{session.plugin.info.name}:control:{decide_plan.session_id}"),
                ),
            )
        )

    def session_renamed(
        self,
        session: Session,
        rename_session: RenameSession,
    ) -> None:
        """Record a title that was written directly with no live source poll."""
        if session.plugin is None:
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        harness = session.plugin.info.name
        identity = f"{harness}:control:{rename_session.session_id}:{rename_session.request_id}:session_rename"
        self.raw_events.record(
            (
                RawEvent(
                    raw_event_id=RawEventId(identity),
                    harness=harness,
                    source_type=CONTROL_SOURCE_TYPE,
                    source_name="session_rename",
                    source_position=str(rename_session.request_id),
                    session_id=rename_session.session_id,
                    actor_id=session.lead_actor_id,
                    parent_actor_id=None,
                    observed_at=time.time(),
                    encoding="json",
                    payload=encode_document(
                        SessionRenameObservation(
                            rename_session.name,
                            TitleOrigin.CUSTOM,
                        )
                    ),
                    source_identity=f"{harness}:control:{rename_session.session_id}",
                ),
            )
        )

    def selection_changed(
        self,
        session: Session,
        selection: SelectModel | SelectEffort,
    ) -> None:
        """Record a confirmed TUI selection even when the client omits a slash record."""
        if session.plugin is None:
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        harness = session.plugin.info.name
        if isinstance(selection, SelectModel):
            source_name = "model_selection"
            payload = encode_document(ModelSelectionObservation(selection.model))
        else:
            source_name = "effort_selection"
            payload = encode_document(EffortSelectionObservation(selection.effort))
        identity = f"{harness}:control:{selection.session_id}:{selection.request_id}:{source_name}"
        self.raw_events.record(
            (
                RawEvent(
                    raw_event_id=RawEventId(identity),
                    harness=harness,
                    source_type=CONTROL_SOURCE_TYPE,
                    source_name=source_name,
                    source_position=str(selection.request_id),
                    session_id=selection.session_id,
                    actor_id=session.lead_actor_id,
                    parent_actor_id=None,
                    observed_at=time.time(),
                    encoding="json",
                    payload=payload,
                    source_identity=f"{harness}:control:{selection.session_id}",
                ),
            )
        )

    def work_before_close(
        self,
        session_id: SessionId,
    ) -> tuple[SessionCloseWork, ...]:
        """Read the open work before the terminal can end or change it."""
        entries = self.session_data.entries_of_types(
            session_id,
            (
                EntryTypeName.TURN_STARTED,
                EntryTypeName.TURN_FINISHED,
                EntryTypeName.SHELL_STARTED,
                EntryTypeName.SHELL_FINISHED,
                EntryTypeName.ASSIGNMENT_STARTED,
                EntryTypeName.ASSIGNMENT_FINISHED,
            ),
        )
        return _open_work(entries)

    def session_closed(
        self,
        session: Session,
        close_session: CloseSession,
        observations: tuple[SessionCloseWork, ...],
    ) -> None:
        """Record the confirmed close and every work item that it stopped."""
        if session.plugin is None:
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        observed_at = time.time()
        harness = session.plugin.info.name
        finish_identity = f"{harness}:control:{close_session.session_id}:{close_session.request_id}:session_finish"
        raw_events = [
            RawEvent(
                raw_event_id=RawEventId(finish_identity),
                harness=harness,
                source_type=CONTROL_SOURCE_TYPE,
                source_name="session_finish",
                source_position=str(close_session.request_id),
                session_id=close_session.session_id,
                actor_id=session.lead_actor_id,
                parent_actor_id=None,
                observed_at=observed_at,
                encoding="json",
                payload=encode_document(
                    ProcessExit(
                        process_id=session.harness_process_id,
                        state=ProcessExitState.EXITED,
                    )
                ),
                source_identity=f"{harness}:control:{close_session.session_id}",
                terminal_window_id=session.terminal_window_id,
                harness_process_id=session.harness_process_id,
            )
        ]
        for work in observations:
            entry = work.entry
            observation = work.observation
            identity = (
                f"{harness}:control:{close_session.session_id}:"
                f"{close_session.request_id}:{observation.kind}:{observation.subject_id}"
            )
            raw_events.append(
                RawEvent(
                    raw_event_id=RawEventId(identity),
                    harness=harness,
                    source_type=CONTROL_SOURCE_TYPE,
                    source_name="session_close",
                    source_position=identity,
                    session_id=close_session.session_id,
                    actor_id=entry.actor_id,
                    parent_actor_id=entry.parent_actor_id,
                    observed_at=observed_at,
                    encoding="json",
                    payload=encode_document(observation),
                    source_identity=f"{harness}:control:{close_session.session_id}",
                )
            )
        self.raw_events.record(tuple(raw_events))


def _open_work(
    entries: tuple[SessionEntry, ...],
) -> tuple[SessionCloseWork, ...]:
    turns: dict[TurnId | ShellId | AssignmentId, SessionEntry] = {}
    shells: dict[TurnId | ShellId | AssignmentId, SessionEntry] = {}
    assignments: dict[TurnId | ShellId | AssignmentId, SessionEntry] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, TurnStartedBody) and entry.turn_id is not None:
            turns[entry.turn_id] = entry
        elif isinstance(body, TurnFinishedBody) and entry.turn_id is not None:
            turns.pop(entry.turn_id, None)
        elif isinstance(body, ShellStartedBody):
            shells[body.shell_id] = entry
        elif isinstance(body, ShellFinishedBody):
            shells.pop(body.shell_id, None)
        elif isinstance(body, AssignmentStartedBody):
            assignments[body.assignment_id] = entry
        elif isinstance(body, AssignmentFinishedBody):
            assignments.pop(body.assignment_id, None)
    return (
        *_work_observations(OpenWorkKind.TURN, turns),
        *_work_observations(OpenWorkKind.SHELL, shells),
        *_work_observations(OpenWorkKind.ASSIGNMENT, assignments),
    )


def _work_observations(
    open_work_kind: OpenWorkKind,
    open_items: Mapping[TurnId | ShellId | AssignmentId, SessionEntry],
) -> tuple[SessionCloseWork, ...]:
    return tuple(
        SessionCloseWork(
            entry,
            SessionCloseWorkObservation(open_work_kind, subject_id, entry.turn_id),
        )
        for subject_id, entry in open_items.items()
    )
