"""The one control dispatch point: a gesture in, its harness's outcome out."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from audit.models import AuditDocument
from audit.recorder import AuditRecorder
from domain.entries import PlanProposedBody, QuestionAskedBody, SessionEntry
from domain.events import PlanProposed, QuestionAsked
from domain.ids import RequestId, SessionId
from harness.contract import HarnessController, HarnessReactorContext
from harness.models import (
    AnswerQuestion,
    ApplyRewind,
    AutoNameSession,
    Background,
    CloseSession,
    Compact,
    ControlAcknowledgement,
    ControlContext,
    ControlOutcome,
    ControlRequest,
    ControlResult,
    DecidePlan,
    DeliveryResult,
    DurableTitleResult,
    Interrupt,
    InterruptRegistry,
    OpenRewind,
    ReadPlanChoices,
    RenameSession,
    SelectEffort,
    SelectModel,
    SendText,
    Session,
)
from harness.services.control_effects import ControlEffectRecorder
from repository.contract.session_data import SessionDataRepository
from repository.contract.sessions import SessionRepository
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalPlugin


class ControlAudit(AuditDocument):
    control: str
    request_id: RequestId
    status: str
    reason: str
    ms: int


class AutomaticSessionNaming(Protocol):
    def requested_name(
        self,
        session: Session,
        request_id: RequestId,
        _apply_title: Callable[[str], ControlOutcome],
    ) -> ControlOutcome: ...


class SessionRenaming(Protocol):
    def rename(
        self,
        _harness_controller: HarnessController,
        rename_session: RenameSession,
        control_context: ControlContext,
    ) -> ControlOutcome: ...


# Every control gesture's OUTCOME, recorded at the one dispatch point every
# harness and every gesture passes through (`HarnessControlService.execute`).
#
# It exists because a failed gesture used to leave NOTHING in the audit. Measured
# (session 01a0037d, 2026-08-15 11:36): a web model switch failed inside its
# harness's screen driver and the only trace anywhere was the browser's own
# `command.ok` row carrying `status: 202` — and 202 is `indeterminate`, i.e. the
# FAILURE code. The reason string went into the HTTP response body and nowhere
# else, so the driver's own step name — which its error type carries expressly
# "for the audit" — was unrecoverable, and the bug could only be named because
# the stuck dialog happened to still be on screen an hour later.
#
# `status` is the audit column, and `indeterminate` is the interesting
# value: the request was understood and the gesture was attempted, but the
# harness never confirmed it — a screen driver that bailed, a paste the TUI
# refused. `rejected` is a guard declining up front and `acknowledged` is the
# happy path. A raised gesture records `status: "raised"` before re-raising, so
# the row exists even when the HTTP layer turns it into a 500.
# The row carries the SESSION ID in its own column, unlike the browser-event
# rows, whose session lives inside the JSON — those are invisible to the obvious
# `WHERE session_id = ?` triage query, which is how this gesture first read as
# "no audit at all".
def _audit_control(
    audit_recorder: AuditRecorder,
    request: ControlRequest,
    outcome: ControlOutcome | None,
    elapsed: float,
) -> None:
    try:
        audit_recorder.state_file(
            str(request.session_id),
            "",
            "control",
            ControlAudit(
                control=getattr(request, "control_name", ""),
                request_id=request.request_id,
                status=outcome.status if outcome is not None else "raised",
                reason=(outcome.reason if outcome is not None else "") or "",
                ms=round(elapsed * 1000),
            ),
        )
    except Exception:
        # The one sanctioned silent swallow: this IS the recording path, so
        # there is nowhere left to record that it failed, and a locked audit DB
        # must never take down the gesture it is only observing.
        pass


class HarnessControlService(HarnessReactorContext):
    def __init__(
        self,
        session_repository: SessionRepository,
        terminal_adapter: TerminalAdapter,
        terminal_plugin: TerminalPlugin,
        session_data_repository: SessionDataRepository,
        audit_recorder: AuditRecorder,
        interrupt_registry: InterruptRegistry,
        control_effect_recorder: ControlEffectRecorder,
        automatic_session_naming: AutomaticSessionNaming,
        session_renaming: SessionRenaming,
    ) -> None:
        self.sessions = session_repository
        self.terminal = terminal_adapter
        self.plugin = terminal_plugin
        self.read_model = session_data_repository
        self.audit = audit_recorder
        self.interrupts = interrupt_registry
        self.control_effects = control_effect_recorder
        self.automatic_namer = automatic_session_naming
        self.session_renamer = session_renaming

    # One typed public method per gesture — the request type IS the parameter,
    # so a caller never builds a bare `ControlRequest` and this class never
    # branches on a command word. Every one of them flows through `_audited`,
    # the single core that times the gesture, calls the harness, and writes
    # the one audit row.
    def send_text(self, send_text: SendText) -> ControlOutcome:
        return self._audited(send_text)

    def send_queued_text(self, send_text: SendText) -> ControlOutcome:
        """Deliver a durable item after a canonical idle boundary.

        The session-data projection can still report the just-finished turn as
        active while reactions for that same fact are running.  The canonical
        boundary is the stronger fact, so the drain path explicitly supplies
        the idle state instead of re-queueing the item forever.
        """
        session = self.sessions.find(send_text.session_id)
        if (
            session is not None
            and session.plugin is not None
            and session.plugin.info.supports_native_text_queue
        ):
            # The native queue accepted the original Enter and owns delivery.
            # Its eventual prompt fact removes the durable UI copy.
            return DeliveryResult(
                send_text.request_id,
                ControlAcknowledgement.ACKNOWLEDGED,
                queued=True,
            )
        outcome = self._audited(send_text, lead_active=False)
        if (
            isinstance(outcome, DeliveryResult)
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
            and not outcome.queued
        ):
            # A plugin without a native queue gets one idle submit. Remove the
            # item now so a slash command, which has no native prompt fact,
            # cannot run again at each later turn boundary.
            self.control_effects.text_delivered(send_text)
        return outcome

    def interrupt(self, interrupt: Interrupt) -> ControlOutcome:
        return self._audited(interrupt)

    def background(self, background: Background) -> ControlOutcome:
        return self._audited(background)

    def close_session(self, close_session: CloseSession) -> ControlOutcome:
        return self._audited(close_session)

    def rename_session(self, rename_session: RenameSession) -> ControlOutcome:
        return self._audited(rename_session)

    def auto_name_session(self, auto_name_session: AutoNameSession) -> ControlOutcome:
        return self._audited(auto_name_session)

    def open_rewind(self, open_rewind: OpenRewind) -> ControlOutcome:
        return self._audited(open_rewind)

    def apply_rewind(self, apply_rewind: ApplyRewind) -> ControlOutcome:
        return self._audited(apply_rewind)

    def compact(self, compact: Compact) -> ControlOutcome:
        return self._audited(compact)

    def select_model(self, select_model: SelectModel) -> ControlOutcome:
        return self._audited(select_model)

    def select_effort(self, select_effort: SelectEffort) -> ControlOutcome:
        return self._audited(select_effort)

    def answer_question(self, answer_question: AnswerQuestion) -> ControlOutcome:
        return self._audited(answer_question)

    def read_plan_choices(self, read_plan_choices: ReadPlanChoices) -> ControlOutcome:
        return self._audited(read_plan_choices)

    def decide_plan(self, decide_plan: DecidePlan) -> ControlOutcome:
        return self._audited(decide_plan)

    def _audited(
        self,
        request: ControlRequest,
        *,
        lead_active: bool | None = None,
    ) -> ControlOutcome:
        pending_entry = self._pending_attention_entry(request) if isinstance(request, DecidePlan) else None
        work_before_close = (
            self.control_effects.work_before_close(request.session_id) if isinstance(request, CloseSession) else ()
        )
        started = time.monotonic()
        try:
            outcome = (
                self._execute(request)
                if lead_active is None
                else self._execute(request, lead_active=lead_active)
            )
        except Exception:
            _audit_control(self.audit, request, None, time.monotonic() - started)
            raise
        _audit_control(self.audit, request, outcome, time.monotonic() - started)
        if (
            isinstance(request, DecidePlan)
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
            and pending_entry is not None
        ):
            self._record_plan_decision(request, pending_entry)
        if (
            isinstance(request, SendText)
            and isinstance(outcome, DeliveryResult)
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
            and outcome.queued
        ):
            self.control_effects.text_queued(request)
            # Persist first, then check for the missed-wakeup ordering where
            # the turn finished between the controller's stale active read and
            # this write. If it is idle now, delivery cannot be left waiting
            # for a turn-finished reaction that already ran.
            if not self._lead_active(request.session_id):
                self.send_queued_text(request)
        if isinstance(request, CloseSession) and outcome.status == ControlAcknowledgement.ACKNOWLEDGED:
            session = self.sessions.find(request.session_id)
            if session is not None:
                self.control_effects.session_closed(
                    session,
                    request,
                    work_before_close,
                )
        if (
            isinstance(request, RenameSession)
            and isinstance(outcome, DurableTitleResult)
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
        ):
            session = self.sessions.find(request.session_id)
            if session is not None:
                self.control_effects.session_renamed(session, request)
        if (
            isinstance(request, (SelectModel, SelectEffort))
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
        ):
            session = self.sessions.find(request.session_id)
            if session is not None:
                self.control_effects.selection_changed(session, request)
        # An interrupt the harness acknowledged but did not corroborate in its
        # own raw event: nothing else will ever tell the interpreter this turn
        # ended, so mark it for the registry's fallback fact. A harness whose
        # translator will read a native abort record on its own next pass
        # sets `corroborated=True` and is never marked.
        if (
            isinstance(request, Interrupt)
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
            and not getattr(outcome, "corroborated", False)
        ):
            self.interrupts.mark(request.session_id)
        return outcome

    def _record_plan_decision(
        self,
        decide_plan: DecidePlan,
        pending_session_entry: SessionEntry,
    ) -> None:
        session = self.sessions.find(decide_plan.session_id)
        if session is None or session.plugin is None:
            return
        self.control_effects.plan_decided(
            session,
            decide_plan,
            pending_session_entry,
        )

    def _lead_active(self, session_id: SessionId) -> bool:
        data = self.read_model.read(session_id)
        if data is None:
            return False
        lead = next(
            (
                actor
                for actor in data.actors
                if actor.actor_id == data.session.lead_actor_id
            ),
            None,
        )
        return bool(lead and lead.statistics.active_since_internal is not None)

    def _execute(
        self,
        request: ControlRequest,
        *,
        lead_active: bool | None = None,
    ) -> ControlOutcome:
        session = self.sessions.find(request.session_id)
        if session is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "unknown session")
        plugin = session.plugin
        if plugin is None or plugin.controller is None:
            return ControlResult(request.request_id, ControlAcknowledgement.REJECTED, "unsupported control")
        # The read model, not a fold: what the session's state IS was decided
        # when the facts arrived, and a gesture asking again would be asking a
        # second time in a second way.
        data = self.read_model.read(request.session_id)
        lead = None
        if data is not None:
            lead = next(
                (actor for actor in data.actors if actor.actor_id == data.session.lead_actor_id),
                None,
            )
        context = ControlContext(
            session=session,
            terminal=self.plugin,
            terminal_window_id=self.terminal.window_for_session(request.session_id),
            current_effort=lead.effort if lead is not None else None,
            lead_active=(
                lead_active
                if lead_active is not None
                else bool(lead and lead.statistics.active_since_internal is not None)
            ),
            pending_attention=self._pending_attention(request),
        )
        if (
            isinstance(request, AutoNameSession)
            and not plugin.info.supports_native_automatic_renaming
        ):
            return self.automatic_namer.requested_name(
                session,
                request.request_id,
                lambda title: self._apply_generated_title(request, context, title),
            )
        if isinstance(request, RenameSession):
            return self.session_renamer.rename(plugin.controller, request, context)
        return plugin.controller.execute(request, context)

    def _apply_generated_title(
        self,
        auto_name_session: AutoNameSession,
        control_context: ControlContext,
        title: str,
    ) -> ControlOutcome:
        session = control_context.session
        plugin = session.plugin
        if plugin is None or plugin.controller is None:
            return ControlResult(
                auto_name_session.request_id,
                ControlAcknowledgement.REJECTED,
                "unsupported control",
            )
        rename = RenameSession(
            auto_name_session.session_id,
            auto_name_session.request_id,
            title,
        )
        outcome = self.session_renamer.rename(
            plugin.controller,
            rename,
            control_context,
        )
        if (
            isinstance(outcome, DurableTitleResult)
            and outcome.status == ControlAcknowledgement.ACKNOWLEDGED
        ):
            self.control_effects.session_renamed(session, rename)
        return outcome

    def _pending_attention(self, request: ControlRequest) -> QuestionAsked | PlanProposed | None:
        """The question or plan THIS gesture is answering, if it is still open.

        A gesture names the attention it answers; anything else pending is
        somebody else's, and answering the wrong dialog is worse than declining.
        """
        entry = self._pending_attention_entry(request)
        if entry is not None:
            body = entry.body
            if isinstance(body, QuestionAskedBody):
                return QuestionAsked(body.attention_id, body.questions)
            if isinstance(body, PlanProposedBody):
                return PlanProposed(body.attention_id, body.plan)
        return None

    def _pending_attention_entry(self, request: ControlRequest) -> SessionEntry | None:
        attention_id = getattr(request, "attention_id", None)
        if attention_id is None:
            return None
        return next(
            (
                entry
                for entry in self.read_model.pending_attention(request.session_id)
                if getattr(entry.body, "attention_id", None) == attention_id
            ),
            None,
        )
