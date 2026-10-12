# Copyright (c) 2026 Zhambyl Yermagambet
"""The one control dispatch point: a gesture in, its harness's outcome out."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from domain import (
    entries as domain_entries,
    entry_attention,
    event_work,
)
from harness import contract as harness_contract
from harness.models import controls as control_models
from harness.services import (
    control_audit,
    control_dependencies,
    control_results,
    control_types,
    open_session_work,
    terminal_gate,
)

if TYPE_CHECKING:
    from domain.ids import WindowId

ControlServiceDependencies = control_dependencies.ControlServiceDependencies
ControlAudit = control_types.ControlAudit
AutomaticSessionNaming = control_types.AutomaticSessionNaming
SessionRenaming = control_types.SessionRenaming
SessionFinder = control_types.SessionFinder


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
class _HarnessControlState:
    """Store control service dependencies."""

    def __init__(
        self,
        control_service_dependencies: ControlServiceDependencies,
        session_terminal_gate: terminal_gate.SessionTerminalGate | None = None,
    ) -> None:
        """Initialize the object."""
        self.sessions = control_service_dependencies.session_repository
        self._terminal = control_service_dependencies.terminal_adapter
        self._plugin = control_service_dependencies.terminal_plugin
        self._read_model = control_service_dependencies.session_data_repository
        self.audit = control_service_dependencies.audit_recorder
        self.interrupts = control_service_dependencies.interrupt_registry
        self.control_effects = control_service_dependencies.control_effect_recorder
        self._automatic_namer = control_service_dependencies.automatic_session_naming
        self._session_renamer = control_service_dependencies.session_renaming
        self._control_gate = session_terminal_gate or terminal_gate.SessionTerminalGate()

    # One typed public method per gesture — the request type IS the parameter,
    # so a caller never builds a bare `ControlRequest` and this class never
    # branches on a command word. Every one of them flows through `_audited`,
    # the single core that times the gesture, calls the harness, and writes
    # the one audit row.


class _HarnessControlExecution(_HarnessControlState):
    """Read and execute one harness control."""

    def _window_is_closed(self, window_id: WindowId) -> bool:
        open_windows = {str(window.window_id) for window in self._terminal.windows()}
        return str(window_id) not in open_windows

    def _pending_attention_entry(self, request: control_models.ControlRequest) -> domain_entries.SessionEntry | None:
        attention_id = getattr(request, "attention_id", None)
        if attention_id is None:
            return None
        return next(
            (
                entry
                for entry in self._read_model.pending_attention(request.session_id)
                if getattr(entry.body, "attention_id", None) == attention_id
            ),
            None,
        )

    def _pending_attention(
        self,
        request: control_models.ControlRequest,
    ) -> event_work.QuestionAsked | event_work.PlanProposed | None:
        """Return the pending attention.

        The question or plan THIS gesture is answering, if it is still open.

                A gesture names the attention it answers; anything else pending is
                somebody else's, and answering the wrong dialog is worse than declining.

        Returns:
            Pending attention.

        """
        entry = self._pending_attention_entry(request)
        if entry is not None:
            body = entry.body
            if isinstance(body, entry_attention.QuestionAskedBody):
                return event_work.QuestionAsked(body.attention_id, body.questions)
            if isinstance(body, entry_attention.PlanProposedBody):
                return event_work.PlanProposed(body.attention_id, body.plan)
        return None

    def _execute(
        self,
        request: control_models.ControlRequest,
        *,
        lead_active: bool | None = None,
    ) -> control_models.ControlOutcome:
        session = self.sessions.find(request.session_id)
        if session is None:
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                "unknown session",
            )
        plugin = session.plugin
        if plugin is None or plugin.controller is None:
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                "unsupported control",
            )
        # The read model, not a fold: what the session's state IS was decided
        # when the facts arrived, and a gesture asking again would be asking a
        # second time in a second way.
        session_record = self._read_model.read(request.session_id)
        lead = None
        if session_record is not None:
            lead = next(
                (actor for actor in session_record.actors if actor.actor_id == session_record.session.lead_actor_id),
                None,
            )
        context = control_models.ControlContext(
            session=session,
            terminal=self._plugin,
            terminal_window_id=self._terminal.window_for_session(request.session_id),
            current_effort=None if lead is None else lead.effort,
            lead_active=(
                bool(lead and lead.statistics.active_since_internal is not None) if lead_active is None else lead_active
            ),
            pending_attention=self._pending_attention(request),
        )
        if (
            isinstance(request, control_models.AutoNameSession)
            and not plugin.harness_info.supports_native_automatic_renaming
        ):
            return self._automatic_namer.requested_name(
                session,
                request.request_id,
                lambda title: self._apply_generated_title(request, context, title),
            )
        if isinstance(request, control_models.RenameSession):
            return self._session_renamer.rename(plugin.controller, request, context)
        return self._harness_outcome(plugin.controller, request, context)

    def _harness_outcome(
        self,
        controller: harness_contract.HarnessController,
        request: control_models.ControlRequest,
        context: control_models.ControlContext,
    ) -> control_models.ControlOutcome:
        stored_window = context.session.terminal_window_id
        if (
            isinstance(request, control_models.CloseSession)
            and stored_window
            and context.terminal_window_id is None
            and self._window_is_closed(stored_window)
        ):
            # The session's window closed before this request. A snapshot uses the
            # last terminal read, so it can show the window as live a little longer.
            # The close asks for what is already true. A session that never had a
            # window, or whose window belongs to another session, still goes to its
            # harness, which refuses it.
            return control_models.ControlResult(
                request.request_id,
                control_models.ControlAcknowledgement.ACKNOWLEDGED,
                "terminal window is already closed",
            )
        return controller.execute(request, context)

    def _apply_generated_title(
        self,
        auto_name_session: control_models.AutoNameSession,
        control_context: control_models.ControlContext,
        title: str,
    ) -> control_models.ControlResult:
        session = control_context.session
        plugin = session.plugin
        if plugin is None or plugin.controller is None:
            return control_models.ControlResult(
                auto_name_session.request_id,
                control_models.ControlAcknowledgement.REJECTED,
                "unsupported control",
            )
        rename = control_models.RenameSession(
            auto_name_session.session_id,
            auto_name_session.request_id,
            title,
        )
        outcome = self._session_renamer.rename(
            plugin.controller,
            rename,
            control_context,
        )
        if (
            isinstance(outcome, control_models.DurableTitleResult)
            and outcome.status == control_models.ControlAcknowledgement.ACKNOWLEDGED
        ):
            self.control_effects.session_renamed(session, rename)
        return outcome


class _HarnessControlEffects(_HarnessControlExecution):
    """Apply facts caused by acknowledged controls."""

    def _apply_plan_effect(
        self,
        request: control_models.ControlRequest,
        outcome: control_models.ControlOutcome,
        pending_entry: domain_entries.SessionEntry | None,
    ) -> None:
        if (
            isinstance(request, control_models.DecidePlan)
            and outcome.status == control_models.ControlAcknowledgement.ACKNOWLEDGED
            and pending_entry is not None
        ):
            self._record_plan_decision(request, pending_entry)

    def _apply_message_effect(
        self,
        request: control_models.ControlRequest,
        outcome: control_models.ControlOutcome,
    ) -> None:
        if isinstance(request, control_models.SendText) and isinstance(outcome, control_models.MessageDeliveryResult):
            session = self.sessions.find(request.session_id)
            if session is not None and outcome.status == control_models.MessageDeliveryStatus.QUEUED:
                self.control_effects.message_queued(session, request)

    def _apply_close_effect(
        self,
        request: control_models.ControlRequest,
        outcome: control_models.ControlOutcome,
        work_before_close: tuple[open_session_work.SessionCloseWork, ...],
    ) -> None:
        if (
            isinstance(request, control_models.CloseSession)
            and outcome.status == control_models.ControlAcknowledgement.ACKNOWLEDGED
        ):
            session = self.sessions.find(request.session_id)
            if session is not None:
                self.control_effects.session_closed(
                    session,
                    request,
                    work_before_close,
                )

    def _apply_rename_effect(
        self,
        request: control_models.ControlRequest,
        outcome: control_models.ControlOutcome,
    ) -> None:
        if (
            isinstance(request, control_models.RenameSession)
            and isinstance(outcome, control_models.DurableTitleResult)
            and outcome.status == control_models.ControlAcknowledgement.ACKNOWLEDGED
        ):
            session = self.sessions.find(request.session_id)
            if session is not None:
                self.control_effects.session_renamed(session, request)

    def _apply_selection_effect(
        self,
        request: control_models.ControlRequest,
        outcome: control_models.ControlOutcome,
    ) -> None:
        if (
            isinstance(request, (control_models.SelectModel, control_models.SelectEffort))
            and outcome.status == control_models.ControlAcknowledgement.ACKNOWLEDGED
        ):
            session = self.sessions.find(request.session_id)
            if session is not None:
                self.control_effects.selection_changed(session, request)

    def _apply_interrupt_effect(
        self,
        request: control_models.ControlRequest,
        outcome: control_models.ControlOutcome,
    ) -> None:
        # An interrupt the harness acknowledged but did not corroborate in its
        # own raw event: nothing else will ever tell the interpreter this turn
        # ended, so mark it for the registry's fallback fact. A harness whose
        # translator will read a native abort record on its own next pass
        # sets `corroborated=True` and is never marked.
        if (
            isinstance(request, control_models.Interrupt)
            and outcome.status == control_models.ControlAcknowledgement.ACKNOWLEDGED
            and not getattr(outcome, "corroborated", False)
        ):
            self.interrupts.mark(request.session_id)

    def _record_plan_decision(
        self,
        decide_plan: control_models.DecidePlan,
        pending_session_entry: domain_entries.SessionEntry,
    ) -> None:
        session = self.sessions.find(decide_plan.session_id)
        if session is None or session.plugin is None:
            return
        self.control_effects.plan_decided(
            session,
            decide_plan,
            pending_session_entry,
        )


class _HarnessControlDispatch(_HarnessControlEffects):
    """Dispatch one control and write its audit."""

    def _dispatch(self, request: control_models.ControlRequest) -> control_models.ControlOutcome:
        with self._control_gate.enter(request.session_id):
            return self._audited(request)

    def _audited(
        self,
        request: control_models.ControlRequest,
        *,
        lead_active: bool | None = None,
    ) -> control_models.ControlOutcome:
        pending_entry = (
            self._pending_attention_entry(request) if isinstance(request, control_models.DecidePlan) else None
        )
        work_before_close = (
            self.control_effects.work_before_close(request.session_id)
            if isinstance(request, control_models.CloseSession)
            else ()
        )
        outcome = self._execute_audited(request, lead_active=lead_active)
        self._apply_plan_effect(request, outcome, pending_entry)
        self._apply_message_effect(request, outcome)
        self._apply_close_effect(request, outcome, work_before_close)
        self._apply_rename_effect(request, outcome)
        self._apply_selection_effect(request, outcome)
        self._apply_interrupt_effect(request, outcome)
        return outcome

    def _execute_audited(
        self,
        request: control_models.ControlRequest,
        *,
        lead_active: bool | None,
    ) -> control_models.ControlOutcome:
        started = time.monotonic()
        try:
            outcome = (
                self._execute(request)
                if lead_active is None
                else self._execute(request, lead_active=lead_active)
            )
        except Exception:
            control_audit.audit_control(self.audit, request, None, time.monotonic() - started)
            raise
        control_audit.audit_control(self.audit, request, outcome, time.monotonic() - started)
        return outcome


class _HarnessBasicControls(_HarnessControlDispatch):
    """Expose basic typed session controls."""

    def send_text(
        self,
        send_text: control_models.SendText,
    ) -> control_models.ControlResult | control_models.MessageDeliveryResult:
        """Send text.

        Returns:
            The send text.

        """
        return self._dispatch(send_text)

    def interrupt(self, interrupt: control_models.Interrupt) -> control_models.ControlResult:
        """Interrupt.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(interrupt))

    def background(self, background: control_models.Background) -> control_models.ControlResult:
        """Return the background.

        Returns:
            Background.

        """
        return control_results.control_result(self._dispatch(background))

    def close_session(self, close_session: control_models.CloseSession) -> control_models.ControlResult:
        """Close session.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(close_session))

    def rename_session(self, rename_session: control_models.RenameSession) -> control_models.ControlResult:
        """Rename session.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(rename_session))

    def auto_name_session(self, auto_name_session: control_models.AutoNameSession) -> control_models.ControlResult:
        """Name the session automatically.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(auto_name_session))

    def open_rewind(self, open_rewind: control_models.OpenRewind) -> control_models.ControlResult:
        """Open rewind.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(open_rewind))


class _HarnessAdvancedControls(_HarnessBasicControls, harness_contract.HarnessReactorContext):
    """Expose typed attention and configuration controls."""

    def apply_rewind(self, apply_rewind: control_models.ApplyRewind) -> control_models.ControlResult:
        """Apply rewind.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(apply_rewind))

    def compact(self, compact: control_models.Compact) -> control_models.ControlResult:
        """Compact.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(compact))

    def select_model(self, select_model: control_models.SelectModel) -> control_models.ControlResult:
        """Select model.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(select_model))

    def select_effort(self, select_effort: control_models.SelectEffort) -> control_models.ControlResult:
        """Select effort.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(select_effort))

    def answer_question(self, answer_question: control_models.AnswerQuestion) -> control_models.ControlResult:
        """Answer question.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(answer_question))

    def read_plan_choices(self, read_plan_choices: control_models.ReadPlanChoices) -> control_models.ControlResult:
        """Return plan choices.

        Returns:
            Plan choices.

        """
        return control_results.control_result(self._dispatch(read_plan_choices))

    def decide_plan(self, decide_plan: control_models.DecidePlan) -> control_models.ControlResult:
        """Decide plan.

        Returns:
            The control result.

        """
        return control_results.control_result(self._dispatch(decide_plan))


class HarnessControlService(_HarnessAdvancedControls):
    """Represent harness control service."""
