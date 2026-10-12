# Copyright (c) 2026 Zhambyl Yermagambet
"""The panes' own reaction to committed facts: a session appears, a session ends."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from domain.event_session import SessionFinished, SessionStarted
from harness.contract import CanonicalEventReaction
from terminal.adapter import SessionPaneRequest, SessionTerminalResult

if TYPE_CHECKING:
    from domain.event_base import CanonicalEvent, EventPayload
    from domain.ids import SessionId, WindowId
    from harness.models.session import (
        Session,
    )


# The only facts that open or close panes.
PANE_FACTS = (SessionStarted, SessionFinished)


def _prior_session(session_started: SessionStarted, session_id: SessionId) -> SessionId | None:
    previous = session_started.continued_from
    return None if previous == session_id else previous


class SessionPaneController(Protocol):
    """Control the pane operations used by the reaction."""

    def close_session_panes(self, session_id: SessionId) -> SessionTerminalResult:
        """Close session panes."""
        ...

    def session_panes_are_open(self, session_id: SessionId) -> bool:
        """Test if session panes are open."""
        ...

    def window_hosts_process(
        self,
        window_id: WindowId,
        process_id: int | None,
        process_name: str,
    ) -> bool:
        """Test if a window hosts a process."""
        ...

    def open_session_panes(self, session_pane_request: SessionPaneRequest) -> SessionTerminalResult:
        """Open session panes."""
        ...


class SessionFinder(Protocol):
    """Find a session for a pane reaction."""

    def find(self, session_id: SessionId) -> Session | None:
        """Return a session."""
        ...


class PaneWidthReader(Protocol):
    """Read the configured pane width."""

    def width_percent(self, working_directory: str) -> int:
        """Return the pane width percentage."""
        ...


class PaneCanonicalEventReaction(CanonicalEventReaction):
    """Represent pane canonical event reaction.

    The terminal display: open the session's panes at the window its own
        raw event recorded, close them when the session finishes.
    """

    def __init__(
        self,
        session_pane_controller: SessionPaneController,
        session_finder: SessionFinder,
        pane_width_reader: PaneWidthReader,
    ) -> None:
        """Initialize the object."""
        self.terminal = session_pane_controller
        self.sessions = session_finder
        self.widths = pane_width_reader

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        """Return the react."""
        payload = canonical_event.payload
        if isinstance(payload, SessionFinished):
            self.terminal.close_session_panes(canonical_event.session_id)
        elif isinstance(payload, SessionStarted):
            continued_from = _prior_session(payload, canonical_event.session_id)
            if continued_from is not None:
                self.terminal.close_session_panes(continued_from)
            # Resume observations are emitted only after our launcher opened
            # the exact window.  At that instant its login shell may not have
            # exec'd the harness yet, so waiting for process corroboration
            # would miss the only non-deduplicated start fact and leave the
            # window permanently untagged.
            resumed = payload.resumed_from is not None
            self._open(
                canonical_event.session_id,
                trusted_transfer=continued_from is not None or resumed,
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
        if not trusted_transfer and (
            session.plugin is None
            or not self.terminal.window_hosts_process(
                session.terminal_window_id,
                session.harness_process_id,
                session.plugin.harness_info.cli_process_name,
            )
        ):
            # A child command inherits its window id from its parent. Do not
            # let that copied value retag the parent's tab or open panes in it.
            return
        self.terminal.open_session_panes(
            SessionPaneRequest(
                session_id,
                session.terminal_window_id,
                self.widths.width_percent(session.working_directory or ""),
            ),
        )
