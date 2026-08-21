"""What a live session's own TUI is showing right now.

The one reader of a harness's `terminal_probe`: it resolves the session's
window from evidence, then lets the harness read its own input line off that
window's screen. A session that is not on screen simply has no input state.
"""

from __future__ import annotations

from domain.ids import SessionId
from harness.models import TerminalInputState, TerminalSessionState
from repository.contract.sessions import SessionRepository
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalViewport


class TerminalInputService:
    def __init__(
        self,
        session_repository: SessionRepository,
        terminal_adapter: TerminalAdapter,
        terminal_viewport: TerminalViewport,
    ) -> None:
        self.sessions = session_repository
        self.terminal = terminal_adapter
        self.viewport = terminal_viewport

    def read(self, session_id: SessionId) -> TerminalInputState | None:
        return self.state(session_id).input_state

    def state(self, session_id: SessionId) -> TerminalSessionState:
        window_id = self.terminal.window_for_session(session_id)
        session = self.sessions.find(session_id)
        plugin = session.plugin if session is not None else None
        input_state = (
            plugin.terminal_probe.input_state(self.viewport, window_id)
            if window_id is not None
            and plugin is not None
            and plugin.terminal_probe is not None
            else None
        )
        return TerminalSessionState(window_id, input_state)
