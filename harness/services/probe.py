"""What a live session's own TUI is showing right now.

The one reader of a harness's `composer`: it resolves the session's
window from a raw event, then lets the harness read its own input line off that
window's screen. A session that is not on screen simply has no input state.
"""

from __future__ import annotations

from domain.ids import SessionId
from harness.models import TerminalInputState, TerminalSessionState
from repository.contract.sessions import SessionRepository
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalPlugin
from harness.services.terminal_driver import TerminalDriver


class TerminalInputService:
    def __init__(
        self,
        session_repository: SessionRepository,
        terminal_adapter: TerminalAdapter,
        terminal_plugin: TerminalPlugin,
    ) -> None:
        self.sessions = session_repository
        self.terminal = terminal_adapter
        self.plugin = terminal_plugin
        self.driver = TerminalDriver(terminal_plugin)

    def read(self, session_id: SessionId) -> TerminalInputState | None:
        return self.state(session_id).input_state

    def state(self, session_id: SessionId) -> TerminalSessionState:
        window_id = self.terminal.window_for_session(session_id)
        session = self.sessions.find(session_id)
        plugin = session.plugin if session is not None else None
        input_state = (
            plugin.composer.read(self.driver, window_id)
            if window_id is not None
            and plugin is not None
            and plugin.composer is not None
            else None
        )
        return TerminalSessionState(window_id, input_state)
