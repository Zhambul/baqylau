"""The session-level terminal service — sessions in, window ids out.

`terminal/contract.py` is keyed on window ids and knows nothing about sessions.
This is where the two meet: every gesture the rest of the system wants is
phrased about a SESSION ("open that session's panes", "paint its tab"), and
resolving one to a window is an EVIDENCE lookup, not an interrogation — the
session row already carries the window its own hook delivery observed, kept
current through every later fact. The terminal is asked only whether that
window is still on screen, because a row can outlive its window.

The session store arrives as a constructor dependency rather than an import:
`terminal/` sits below `app/`, and importing the application graph from here
would close a cycle.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from domain.ids import SessionId
from terminal.contract import TerminalPlugin
from terminal.models import (
    ACTIVITY_PANE_TAG,
    PaneAnchor,
    PaneCloseRequest,
    PaneOpenRequest,
    PaneResizeRequest,
    SCOREBOARD_PANE_TAG,
    SESSION_WINDOW_TAG,
    TabAppearance,
    TabColorClearRequest,
    TabColorSetRequest,
    WindowFocusRequest,
    WindowInfo,
    WindowTagRequest,
)

# The scoreboard is a fixed five rows — the surface is five lines of session
# statistics, so any other height is either clipped or padded with blank rows.
SCOREBOARD_HEIGHT = 5
SCOREBOARD_SIZE_PERCENT = 5
# A resize is asynchronous: the terminal accepts it, then reports the new size
# on a later read. Ask again after a beat rather than trusting the first
# acknowledgement, and give up after a few rounds instead of spinning.
SCOREBOARD_RESIZE_ATTEMPTS = 3
SCOREBOARD_RESIZE_SETTLE_SECONDS = 0.08

MIRROR_PANE_TITLE = "◧ cmd mirror"
SCOREBOARD_PANE_TITLE = "▪ session"


@dataclass(frozen=True)
class SessionTerminalResult:
    """One session-level gesture's outcome. The caller audits the reason."""

    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class SessionPaneRequest:
    session_id: SessionId
    anchor_window_id: str
    activity_width_percent: int

    def __post_init__(self) -> None:
        if not 1 <= self.activity_width_percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")


class TerminalAdapter:
    def __init__(self, plugin: TerminalPlugin, sessions) -> None:
        # `sessions` is the session store, untyped on purpose: naming its type
        # here would make `terminal/` import the layer above it, and declaring
        # a Protocol for it would claim the store implements something it has
        # never heard of. All this needs is `find_by_id`.
        self._plugin = plugin
        self._sessions = sessions

    # --- session ⇄ window ---------------------------------------------------
    def window_for_session(self, session_id: SessionId) -> str | None:
        """The session's window, when it is still on screen."""
        session = self._sessions.find_by_id(session_id)
        window_id = session.terminal_window_id if session is not None else None
        if not window_id:
            return None
        return window_id if self._window(str(window_id)) is not None else None

    def current_window(self) -> str | None:
        return self._plugin.metadata.current_window_id()

    def session_for_window(self, window_id: str | None) -> SessionId | None:
        for window in self._tab_windows(window_id):
            session_id = window.tags.get(SESSION_WINDOW_TAG)
            if session_id:
                return SessionId(session_id)
        return None

    def current_session(self) -> SessionId | None:
        return self.session_for_window(None)

    # --- panes ---------------------------------------------------------------
    def session_panes_are_open(self, session_id: SessionId) -> bool:
        return self._tagged(ACTIVITY_PANE_TAG, session_id) is not None

    def open_session_panes(self, request: SessionPaneRequest) -> SessionTerminalResult:
        """The mirror and scoreboard panes, as one gesture.

        Idempotent by rediscovery: a pane that is already open is found by its
        tag and left alone, so a toggle survives a daemon restart.
        """
        session_id = str(request.session_id)
        anchor_window_id = request.anchor_window_id
        outcomes = [self._plugin.metadata.tag_window(
            WindowTagRequest(anchor_window_id, {SESSION_WINDOW_TAG: session_id})
        )]
        if self._tagged(ACTIVITY_PANE_TAG, request.session_id) is None:
            outcomes.append(self._plugin.panes.open_pane(PaneOpenRequest(
                command=self._pane_command("mirror_process.py", session_id),
                working_directory="",
                title=MIRROR_PANE_TITLE,
                split="vertical",
                size_percent=request.activity_width_percent,
                anchor=PaneAnchor(window_id=anchor_window_id),
                same_tab_as=anchor_window_id,
                tags={ACTIVITY_PANE_TAG: session_id},
            )))
        if self._tagged(SCOREBOARD_PANE_TAG, request.session_id) is None:
            outcomes.append(self._plugin.panes.open_pane(PaneOpenRequest(
                command=self._pane_command("scoreboard_process.py", session_id),
                working_directory="",
                title=SCOREBOARD_PANE_TITLE,
                split="horizontal",
                size_percent=SCOREBOARD_SIZE_PERCENT,
                # The scoreboard sits under the MIRROR, not under the session's
                # own window — it shares the mirror's column.
                anchor=PaneAnchor(tag=(ACTIVITY_PANE_TAG, session_id)),
                same_tab_as=anchor_window_id,
                tags={SCOREBOARD_PANE_TAG: session_id},
            )))
            outcomes.append(self._settle_scoreboard_height(request.session_id))
        # Hand inner focus back to the host pane the splits took it from, which
        # restores the host's window title as the visible tab title.
        outcomes.append(self._plugin.panes.focus_window(WindowFocusRequest(anchor_window_id)))
        return self._combined(outcomes, "terminal pane setup failed")

    def close_session_panes(self, session_id: SessionId) -> SessionTerminalResult:
        return self._close_session_panes(session_id, clear_tab=True)

    def toggle_session_panes(
        self,
        session_id: SessionId,
        activity_width_percent: int,
        anchor_window_id: str | None = None,
    ) -> SessionTerminalResult:
        if self.session_panes_are_open(session_id):
            # A toggle-off keeps the tab colour: the session is still running
            # in that tab, only its display panes are gone.
            return self._close_session_panes(session_id, clear_tab=False)
        anchor_window_id = (
            anchor_window_id or self.current_window() or self.window_for_session(session_id)
        )
        if anchor_window_id is None:
            return SessionTerminalResult(False, "session has no terminal window")
        return self.open_session_panes(
            SessionPaneRequest(session_id, anchor_window_id, activity_width_percent)
        )

    def resize_activity_pane(self, session_id: SessionId, columns: int) -> SessionTerminalResult:
        activity = self._tagged(ACTIVITY_PANE_TAG, session_id)
        if activity is None:
            return SessionTerminalResult(False, "activity pane is not open")
        response = self._plugin.panes.resize_pane(
            PaneResizeRequest(activity.window_id, "horizontal", columns)
        )
        return SessionTerminalResult(response.succeeded, response.reason)

    def activity_pane_geometry(self, session_id: SessionId) -> tuple[int, int] | None:
        """(activity columns, the row's total columns), or None when the pane
        is not open.

        The row total is the HOST plus the activity pane, not the sum of every
        window in the tab: the scoreboard is stacked inside the activity pane's
        own column, so counting it would count that column twice — which is
        what once under-reported the pane's share and drove the width gestures
        far off their target.
        """
        activity = self._tagged(ACTIVITY_PANE_TAG, session_id)
        if activity is None or not activity.columns:
            return None
        host = next((window for window in self._plugin.metadata.windows()
                     if window.tab_id == activity.tab_id and window.is_first_in_tab), None)
        if host is None:
            return None
        return activity.columns, host.columns + activity.columns

    def set_activity_pane_width(self, session_id: SessionId, percent: int) -> SessionTerminalResult:
        if not 1 <= percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")
        geometry = self.activity_pane_geometry(session_id)
        if geometry is None:
            return SessionTerminalResult(False, "activity pane is not open")
        current_columns, total_columns = geometry
        target_columns = round(total_columns * percent / 100)
        return self.resize_activity_pane(session_id, target_columns - current_columns)

    # --- tabs ----------------------------------------------------------------
    def paint_session_tab(
        self,
        session_id: SessionId,
        appearance: TabAppearance,
    ) -> SessionTerminalResult:
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(False, "session has no terminal window")
        response = self._plugin.tabs.set_tab_color(TabColorSetRequest(window_id, appearance))
        return SessionTerminalResult(response.succeeded, response.reason)

    def clear_session_tab(self, session_id: SessionId) -> SessionTerminalResult:
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(False, "session has no terminal window")
        response = self._plugin.tabs.clear_tab_color(TabColorClearRequest(window_id))
        return SessionTerminalResult(response.succeeded, response.reason)

    # --- internals -----------------------------------------------------------
    @staticmethod
    def _pane_command(process_file: str, session_id: str) -> tuple[str, ...]:
        pane_directory = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panes")
        return (sys.executable, os.path.join(pane_directory, process_file), session_id)

    def _close_session_panes(
        self,
        session_id: SessionId,
        *,
        clear_tab: bool,
    ) -> SessionTerminalResult:
        outcomes = []
        for tag in (SCOREBOARD_PANE_TAG, ACTIVITY_PANE_TAG):
            pane = self._tagged(tag, session_id)
            if pane is not None:
                outcomes.append(self._plugin.panes.close_pane(PaneCloseRequest(pane.window_id)))
        session_window_id = self.window_for_session(session_id)
        if clear_tab and session_window_id is not None:
            outcomes.append(
                self._plugin.tabs.clear_tab_color(TabColorClearRequest(session_window_id))
            )
            outcomes.append(self._plugin.metadata.tag_window(
                WindowTagRequest(session_window_id, {SESSION_WINDOW_TAG: ""})
            ))
        return self._combined(outcomes, "terminal pane close failed")

    def _settle_scoreboard_height(self, session_id: SessionId):
        for _attempt in range(SCOREBOARD_RESIZE_ATTEMPTS):
            scoreboard = self._tagged(SCOREBOARD_PANE_TAG, session_id)
            if scoreboard is None:
                return SessionTerminalResult(False, "scoreboard pane is not open")
            row_difference = SCOREBOARD_HEIGHT - scoreboard.lines
            if row_difference == 0:
                return SessionTerminalResult(True)
            response = self._plugin.panes.resize_pane(
                PaneResizeRequest(scoreboard.window_id, "vertical", row_difference)
            )
            if not response.succeeded:
                return SessionTerminalResult(False, response.reason)
            time.sleep(SCOREBOARD_RESIZE_SETTLE_SECONDS)
        scoreboard = self._tagged(SCOREBOARD_PANE_TAG, session_id)
        if scoreboard is not None and scoreboard.lines == SCOREBOARD_HEIGHT:
            return SessionTerminalResult(True)
        return SessionTerminalResult(False, "scoreboard pane did not reach its height")

    def _tagged(self, tag: str, session_id: SessionId) -> WindowInfo | None:
        return next((window for window in self._plugin.metadata.windows()
                     if window.tags.get(tag) == str(session_id)), None)

    def _window(self, window_id: str) -> WindowInfo | None:
        return next((window for window in self._plugin.metadata.windows()
                     if window.window_id == window_id), None)

    def _tab_windows(self, window_id: str | None) -> tuple[WindowInfo, ...]:
        """The windows of one tab: the tab holding `window_id`, or — when no
        window is named (a caller with no terminal environment of its own) —
        the focused terminal's active tab."""
        windows = self._plugin.metadata.windows()
        named = window_id or self.current_window()
        if named:
            tab_id = next((window.tab_id for window in windows
                           if window.window_id == str(named)), None)
            if tab_id is not None:
                return tuple(window for window in windows if window.tab_id == tab_id)
        focused = tuple(window for window in windows if window.tab_is_focused)
        if focused:
            return focused
        active_tabs = {window.tab_id for window in windows if window.tab_is_active}
        if len(active_tabs) == 1:
            return tuple(window for window in windows if window.tab_is_active)
        return ()

    @staticmethod
    def _combined(outcomes, reason: str) -> SessionTerminalResult:
        succeeded = all(outcome.succeeded for outcome in outcomes)
        return SessionTerminalResult(succeeded, None if succeeded else reason)
