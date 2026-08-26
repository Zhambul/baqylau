"""One session rename operation for controls and observed title facts."""

from __future__ import annotations

from domain.events import CanonicalEvent, EventPayload, SessionTitleChanged
from harness.contract import CanonicalEventReaction, HarnessController
from harness.models import (
    ControlAcknowledgement,
    ControlContext,
    ControlOutcome,
    ControlResult,
    RenameSession,
)
from terminal.adapter import TerminalAdapter


class SessionRenamer(CanonicalEventReaction):
    """Apply a harness rename and keep its live terminal tab in agreement.

    A control uses `rename` because it knows the requested title. A native or
    automatic title source uses the same tab operation through `react`.
    """

    def __init__(self, terminal_adapter: TerminalAdapter) -> None:
        self._terminal = terminal_adapter

    def rename(
        self,
        controller: HarnessController,
        rename_session: RenameSession,
        control_context: ControlContext,
    ) -> ControlOutcome:
        outcome = controller.execute(rename_session, control_context)
        if outcome.status != ControlAcknowledgement.ACKNOWLEDGED:
            return outcome
        terminal_outcome = self._terminal.rename_session_tab(
            rename_session.session_id,
            rename_session.name,
        )
        if terminal_outcome.succeeded:
            return outcome
        return ControlResult(
            rename_session.request_id,
            ControlAcknowledgement.INDETERMINATE,
            terminal_outcome.reason or "terminal title was not changed",
        )

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        if not isinstance(payload, SessionTitleChanged):
            return
        outcome = self._terminal.rename_session_tab(
            canonical_event.session_id,
            payload.title,
        )
        if not outcome.succeeded:
            raise RuntimeError(outcome.reason or "terminal title was not changed")
