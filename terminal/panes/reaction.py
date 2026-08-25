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
    raw event recorded, close them when the session finishes."""

    def __init__(
        self,
        terminal_adapter: TerminalAdapter,
        session_repository: SessionRepository,
        pane_width_service: PaneWidthService,
    ) -> None:
        self.terminal = terminal_adapter
        self.sessions = session_repository
        self.widths = pane_width_service

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        if isinstance(payload, SessionFinished):
            self.terminal.close_session_panes(canonical_event.session_id)
        elif isinstance(payload, SessionStarted):
            continued = (
                payload.continued_from is not None
                and payload.continued_from != canonical_event.session_id
            )
            if continued:
                assert payload.continued_from is not None
                self.terminal.close_session_panes(payload.continued_from)
            # Resume observations are emitted only after our launcher opened
            # the exact window.  At that instant its login shell may not have
            # exec'd the harness yet, so waiting for process corroboration
            # would miss the only non-deduplicated start fact and leave the
            # window permanently untagged.
            resumed = payload.resumed_from is not None
            self._open(
                canonical_event.session_id,
                trusted_transfer=continued or resumed,
            )

    def _open(self, session_id: SessionId, *, trusted_transfer: bool = False) -> None:
        if self.terminal.session_panes_are_open(session_id):
            return
        # The session-upsert reaction already ran for this whole batch
        # (reaction-outer order), so the row exists and carries the window the
        # same delivery shipped.
        session = self.sessions.find(session_id)
        if session is None or session.terminal_window_id is None:
            return  # headless launch: no anchor, no panes
        if (
            not trusted_transfer
            and (
                session.plugin is None
                or not self.terminal.window_hosts_process(
                    session.terminal_window_id,
                    session.harness_process_id,
                    session.plugin.info.cli_process_name,
                )
            )
        ):
            # A child command inherits its window id from its parent. Do not
            # let that copied value retag the parent's tab or open panes in it.
            return
        self.terminal.open_session_panes(SessionPaneRequest(
            session_id,
            session.terminal_window_id,
            self.widths.width_percent(session.working_directory or ""),
        ))
