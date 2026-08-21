"""The panes' own reaction to committed facts: a session appears, a session ends."""

from __future__ import annotations

from domain.events import CanonicalEvent, EventPayload, SessionFinished, SessionStarted
from domain.ids import SessionId
from harness.contract import CanonicalEventReaction
from repository.contract.sessions import SessionRepository
from terminal.adapter import SessionPaneRequest, TerminalAdapter
from terminal.services.panes import PaneWidthService


class PaneCanonicalEventReaction(CanonicalEventReaction):
    """The terminal display: open the session's panes at the window its own
    evidence recorded, close them when the session finishes."""

    def __init__(
        self,
        terminal: TerminalAdapter,
        sessions: SessionRepository,
        widths: PaneWidthService,
    ) -> None:
        self.terminal = terminal
        self.sessions = sessions
        self.widths = widths

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        if isinstance(payload, SessionFinished):
            self.terminal.close_session_panes(canonical_event.session_id)
        elif isinstance(payload, SessionStarted):
            self._open(canonical_event.session_id)

    def _open(self, session_id: SessionId) -> None:
        if self.terminal.session_panes_are_open(session_id):
            return
        # The session-upsert reaction already ran for this whole batch
        # (reaction-outer order), so the row exists and carries the window the
        # same delivery shipped.
        session = self.sessions.find(session_id)
        if session is None or session.terminal_window_id is None:
            return  # headless launch: no anchor, no panes
        self.terminal.open_session_panes(SessionPaneRequest(
            session_id,
            session.terminal_window_id,
            self.widths.width_percent(session.working_directory or ""),
        ))
