"""Application adapter from the terminal contract to the installed frontend."""

from __future__ import annotations

import os
import sys
import time

import frontends

from contracts.terminal import (
    ACTIVITY_PANE_TAG,
    SCOREBOARD_PANE_TAG,
    SESSION_WINDOW_TAG,
    ScreenText,
    SessionPaneControl,
    SessionPaneRequest,
    SessionTabControl,
    SessionTerminal,
    TabAppearance,
    TabRequest,
    TabResult,
    TerminalControl,
    TerminalResult,
    TextSubmission,
)
from domain.ids import SessionId
from terminal.session import login_shell_command

SCOREBOARD_HEIGHT = 5
SCOREBOARD_RESIZE_ATTEMPTS = 3
SCOREBOARD_RESIZE_SETTLE_SECONDS = 0.08


# TerminalControl already extends TerminalScreen, so naming both here would put a
# base in front of its own subclass and Python could not order the MRO.
class ApplicationTerminal(
    SessionTerminal, SessionPaneControl, SessionTabControl, TerminalControl
):
    def _frontend(self):
        return frontends.get(resolve=True)

    def window_for_session(self, session_id: SessionId) -> str | None:
        return self._frontend().window_for_session(str(session_id))

    def current_window(self) -> str | None:
        return self._frontend().current_window() or None

    def _tab_windows(self, window_id: str | None):
        """The windows of one tab: the tab holding `window_id`, or — when no
        window is named (a caller without a terminal environment) — the focused
        terminal's active tab."""
        frontend = self._frontend()
        current_window_id = window_id or frontend.current_window()
        terminal_state = frontend.ls()
        for operating_system_window in terminal_state:
            for tab in operating_system_window.get("tabs", ()):
                windows = tuple(tab.get("windows", ()))
                if current_window_id and any(
                    str(window.get("id")) == current_window_id for window in windows
                ):
                    return windows
        for operating_system_window in terminal_state:
            if not operating_system_window.get("is_focused"):
                continue
            for tab in operating_system_window.get("tabs", ()):
                if tab.get("is_active"):
                    return tuple(tab.get("windows", ()))
        active_tabs = tuple(
            tab
            for operating_system_window in terminal_state
            for tab in operating_system_window.get("tabs", ())
            if tab.get("is_active")
        )
        if len(active_tabs) == 1:
            return tuple(active_tabs[0].get("windows", ()))
        return ()

    def session_for_window(self, window_id: str | None) -> SessionId | None:
        for window in self._tab_windows(window_id):
            session_id = (window.get("user_vars") or {}).get(SESSION_WINDOW_TAG)
            if session_id:
                return SessionId(session_id)
        return None

    def current_session(self) -> SessionId | None:
        return self.session_for_window(None)

    def hosting_session(self, excluding_session_id: SessionId) -> SessionId | None:
        return self._hosting_session(excluding_session_id, None)

    def _hosting_session(
        self,
        excluding_session_id: SessionId,
        window_id: str | None,
    ) -> SessionId | None:
        for window in self._tab_windows(window_id):
            user_variables = window.get("user_vars") or {}
            hosted_session_id = (
                user_variables.get(ACTIVITY_PANE_TAG)
                or user_variables.get(SESSION_WINDOW_TAG)
            )
            if hosted_session_id and hosted_session_id != str(excluding_session_id):
                return SessionId(hosted_session_id)
        return None

    def session_panes_are_open(self, session_id: SessionId) -> bool:
        return self._frontend().find_window(ACTIVITY_PANE_TAG, str(session_id)) is not None

    def toggle_session_panes(
        self,
        session_id: SessionId,
        activity_width_percent: int,
        anchor_window_id: str | None = None,
    ) -> TerminalResult:
        if self.session_panes_are_open(session_id):
            return self._close_session_panes(session_id, clear_tab=False)
        anchor_window_id = (
            anchor_window_id or self.current_window() or self.window_for_session(session_id)
        )
        if anchor_window_id is None:
            return TerminalResult(False, "session has no terminal window")
        request = SessionPaneRequest(session_id, anchor_window_id, activity_width_percent)
        return self.open_session_panes(request)

    def resize_activity_pane(self, session_id: SessionId, columns: int) -> TerminalResult:
        result = self._frontend().resize_pane(
            (ACTIVITY_PANE_TAG, str(session_id)),
            "horizontal",
            columns,
        )
        return TerminalResult(result == 0, None if result == 0 else "terminal pane resize failed")

    def activity_pane_geometry(self, session_id: SessionId) -> tuple[int, int] | None:
        return self._frontend().split_geometry(
            (ACTIVITY_PANE_TAG, str(session_id)),
            exclude_var=SCOREBOARD_PANE_TAG,
        )

    def set_activity_pane_width(
        self,
        session_id: SessionId,
        percent: int,
    ) -> TerminalResult:
        if not 1 <= percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")
        geometry = self.activity_pane_geometry(session_id)
        if geometry is None:
            return TerminalResult(False, "activity pane is not open")
        current_columns, total_columns = geometry
        target_columns = round(total_columns * percent / 100)
        return self.resize_activity_pane(session_id, target_columns - current_columns)

    def open_session_panes(self, request: SessionPaneRequest) -> TerminalResult:
        frontend = self._frontend()
        if not frontend.usable():
            return TerminalResult(False, "no terminal available")
        session_id = str(request.session_id)
        anchor_window_id = request.anchor_window_id
        results = [frontend.set_user_vars(anchor_window_id, {SESSION_WINDOW_TAG: session_id})]
        frontend.goto_splits_layout(anchor_window_id)
        application_directory = os.path.dirname(os.path.abspath(__file__))
        process_arguments = [session_id]
        if frontend.find_window(ACTIVITY_PANE_TAG, session_id) is None:
            results.append(
                frontend.launch_pane(
                    [
                        sys.executable,
                        os.path.join(application_directory, "terminal_process.py"),
                        *process_arguments,
                    ],
                    "vsplit",
                    bias=request.activity_width_percent,
                    next_to=f"id:{anchor_window_id}",
                    in_tab_of=anchor_window_id,
                    var={ACTIVITY_PANE_TAG: session_id},
                    title="◧ cmd mirror",
                )
            )
        if frontend.find_window(SCOREBOARD_PANE_TAG, session_id) is None:
            results.append(
                frontend.launch_pane(
                    [
                        sys.executable,
                        os.path.join(application_directory, "scoreboard_process.py"),
                        *process_arguments,
                    ],
                    "hsplit",
                    bias=5,
                    next_to=f"var:{ACTIVITY_PANE_TAG}={session_id}",
                    in_tab_of=anchor_window_id,
                    var={SCOREBOARD_PANE_TAG: session_id},
                    title="▪ session",
                )
            )
            results.append(self._set_scoreboard_height(frontend, session_id))
        results.append(frontend.focus_first_pane(anchor_window_id))
        succeeded = all(result == 0 for result in results)
        return TerminalResult(succeeded, None if succeeded else "terminal pane setup failed")

    @staticmethod
    def _set_scoreboard_height(frontend, session_id: str) -> int:
        for _attempt in range(SCOREBOARD_RESIZE_ATTEMPTS):
            window = frontend.find_window(SCOREBOARD_PANE_TAG, session_id)
            if window is None:
                return 1
            row_difference = SCOREBOARD_HEIGHT - int(window.get("lines") or 0)
            if row_difference == 0:
                return 0
            result = frontend.resize_pane(
                (SCOREBOARD_PANE_TAG, session_id),
                "vertical",
                row_difference,
            )
            if result != 0:
                return result
            time.sleep(SCOREBOARD_RESIZE_SETTLE_SECONDS)
        window = frontend.find_window(SCOREBOARD_PANE_TAG, session_id)
        return 0 if window is not None and int(window.get("lines") or 0) == SCOREBOARD_HEIGHT else 1

    def close_session_panes(self, session_id: SessionId) -> TerminalResult:
        return self._close_session_panes(session_id, clear_tab=True)

    def _close_session_panes(
        self,
        session_id: SessionId,
        *,
        clear_tab: bool,
    ) -> TerminalResult:
        frontend = self._frontend()
        identity = str(session_id)
        results = []
        if frontend.find_window(SCOREBOARD_PANE_TAG, identity) is not None:
            results.append(frontend.close_pane(var=(SCOREBOARD_PANE_TAG, identity)))
        if frontend.find_window(ACTIVITY_PANE_TAG, identity) is not None:
            results.append(frontend.close_pane(var=(ACTIVITY_PANE_TAG, identity)))
        session_window_id = frontend.window_for_session(identity)
        if clear_tab and session_window_id is not None:
            results.append(frontend.clear_tab_color(session_window_id))
            results.append(
                frontend.set_user_vars(
                    session_window_id,
                    {SESSION_WINDOW_TAG: ""},
                )
            )
        succeeded = all(result == 0 for result in results)
        return TerminalResult(succeeded, None if succeeded else "terminal pane close failed")

    @staticmethod
    def _hex_color(color) -> str:
        return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"

    def paint_session_tab(
        self,
        session_id: SessionId,
        appearance: TabAppearance,
    ) -> TerminalResult:
        frontend = self._frontend()
        window_id = frontend.window_for_session(str(session_id))
        if window_id is None:
            return TerminalResult(False, "session has no terminal window")
        result = frontend.set_tab_color(
            window_id,
            self._hex_color(appearance.active_background),
            self._hex_color(appearance.active_foreground),
            self._hex_color(appearance.inactive_background),
            self._hex_color(appearance.inactive_foreground),
        )
        return TerminalResult(result == 0, None if result == 0 else "terminal tab paint failed")

    def clear_session_tab(self, session_id: SessionId) -> TerminalResult:
        frontend = self._frontend()
        window_id = frontend.window_for_session(str(session_id))
        if window_id is None:
            return TerminalResult(False, "session has no terminal window")
        result = frontend.clear_tab_color(window_id)
        return TerminalResult(result == 0, None if result == 0 else "terminal tab clear failed")

    def open_tab(self, request: TabRequest) -> TabResult:
        frontend = self._frontend()
        if not frontend.usable():
            return TabResult(False, None, "no terminal available")
        launched = frontend.launch_tab(
            request.working_directory,
            list(login_shell_command(request.command)),
        )
        if not launched:
            return TabResult(False, None, "terminal launch failed")
        window_id = launched if isinstance(launched, str) else None
        return TabResult(True, window_id)

    def read_screen(
        self,
        window_id: str,
        ansi: bool = False,
    ) -> ScreenText | None:
        text = self._frontend().get_text(window_id, ansi=ansi)
        return ScreenText(text) if text is not None else None

    def submit_text(self, window_id: str, submission: TextSubmission) -> TerminalResult:
        frontend = self._frontend()
        sender = frontend.paste_text if submission.mode == "paste" else frontend.send_text
        succeeded = bool(sender(window_id, submission.text))
        return TerminalResult(succeeded, None if succeeded else "terminal input failed")

    def send_key(self, window_id: str, key: str) -> TerminalResult:
        succeeded = bool(self._frontend().send_key(window_id, key))
        return TerminalResult(succeeded, None if succeeded else "terminal key input failed")

    def close_tab(self, window_id: str) -> TerminalResult:
        succeeded = bool(self._frontend().close_tab(window_id))
        return TerminalResult(succeeded, None if succeeded else "terminal close failed")

    def set_tab_title(self, window_id: str, title: str) -> TerminalResult:
        succeeded = bool(self._frontend().set_tab_title(window_id, title))
        return TerminalResult(succeeded, None if succeeded else "terminal title failed")
