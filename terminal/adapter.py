# Copyright (c) 2026 Zhambyl Yermagambet
"""The session-level terminal service — sessions in, window ids out.

`terminal/contract.py` is keyed on window ids and knows nothing about sessions.
This is where the two meet: every gesture the rest of the system wants is
phrased about a SESSION ("open that session's panes", "paint its tab"), and
resolving one to a window is a RAW EVENT lookup, not an interrogation — the
session row already carries the window its own hook delivery observed, kept
current through every later fact. The terminal is asked only whether that
window is still on screen, because a row can outlive its window.

The session store arrives as a constructor dependency rather than an import:
`terminal/` sits below `app/`, and importing the application graph from here
would close a cycle.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from domain.ids import SessionId, WindowId
from harness.contract import SessionTerminalState
from terminal import pane_client
from terminal.adapter_models import (
    MAXIMUM_PANE_WIDTH_PERCENT,
    MINIMUM_PANE_WIDTH_PERCENT,
    PERCENT_SCALE,
    SessionFinder as SessionFinder,
    SessionPaneRequest as SessionPaneRequest,
    SessionTerminalResult as SessionTerminalResult,
    TerminalOutcome as TerminalOutcome,
    combined_outcomes,
)
from terminal.models import (
    metadata as metadata_models,
    pane_results,
    panes as pane_models,
    tabs as tab_models,
    values as terminal_values,
)

# The terminal's own window id (`terminal/models/`), distinct from the domain
# fact of the same name: `terminal/` may depend on nothing outside itself, so
# this module — the one place a session's RAW EVENT (`WindowId` above) meets a
# live terminal window — converts explicitly at the boundary rather than
# reusing one NewType across it.
from terminal.ownership import window_hosts_process

if TYPE_CHECKING:
    from collections.abc import Iterable

    from terminal.contract import TerminalPlugin

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
PANE_CLIENT = pane_client.PANE_CLIENT


class _TerminalAdapterState:
    """Store services and shared terminal lookup helpers."""

    def __init__(self, terminal_plugin: TerminalPlugin, session_finder: SessionFinder) -> None:
        """Initialize the object."""
        self._plugin = terminal_plugin
        self._sessions = session_finder

    def _session_is_live(
        self,
        session_id: SessionId,
        on_screen: dict[terminal_values.WindowId, str | None],
    ) -> bool:
        session = self._sessions.find(session_id)
        window_id = None if session is None else session.terminal_window_id
        if not window_id:
            return False
        native_window_id = terminal_values.WindowId(str(window_id))
        owner = on_screen.get(native_window_id)
        return native_window_id in on_screen and owner == str(session_id)

    def _tagged(self, tag: str, session_id: SessionId) -> terminal_values.WindowInfo | None:
        for window in self._plugin.metadata.windows():
            if window.tags.get(tag) == str(session_id):
                return window
        return None

    def _tab_windows(self, window_id: terminal_values.WindowId | None) -> tuple[terminal_values.WindowInfo, ...]:
        windows = self._plugin.metadata.windows()
        named = window_id or self._plugin.metadata.current_window_id()
        if named:
            tab_id = next((window.tab_id for window in windows if window.window_id == named), None)
            if tab_id is not None:
                return tuple(window for window in windows if window.tab_id == tab_id)
        focused = tuple(window for window in windows if window.tab_is_focused)
        if focused:
            return focused
        active_tabs = {window.tab_id for window in windows if window.tab_is_active}
        if len(active_tabs) == 1:
            return tuple(window for window in windows if window.tab_is_active)
        return ()


class _TerminalSessionWindows(_TerminalAdapterState, SessionTerminalState):
    """Resolve sessions and their live terminal windows."""

    def window_for_session(self, session_id: SessionId) -> WindowId | None:
        """Return the window for session.

        The session's window, when it is still on screen.

        Returns:
            Window for session.

        """
        session = self._sessions.find(session_id)
        window_id = None if session is None else session.terminal_window_id
        if not window_id:
            return None
        return (
            window_id
            if self.window_is_live(
                session_id,
                window_id,
                self._plugin.metadata.windows(),
            )
            else None
        )

    def window_is_live(
        self,
        session_id: SessionId,
        window_id: WindowId,
        windows: tuple[terminal_values.WindowInfo, ...],
    ) -> bool:
        """Return the window is live.

        Returns:
            Window is live.

        """
        native = terminal_values.WindowId(str(window_id))
        window = next((window_info for window_info in windows if window_info.window_id == native), None)
        if window is None:
            return False
        owner = window.tags.get(terminal_values.SESSION_WINDOW_TAG)
        return owner == str(session_id)

    def window_hosts_process(
        self,
        window_id: WindowId,
        process_id: int | None,
        process_name: str,
    ) -> bool:
        """Whether the named harness is the foreground process in this window.

        A terminal window id is inherited by every child command. It is a location
        hint, not ownership proof. A hook's resolved CLI PID is exact. A
        resume-launch observation can arrive before its hook and has no PID,
        so that one case uses the plugin's exact executable name.

        Returns:
            Whether the named harness is the foreground process in this window.

        """
        native = terminal_values.WindowId(str(window_id))
        window = next(
            (window_info for window_info in self._plugin.metadata.windows() if window_info.window_id == native),
            None,
        )
        if window is None:
            return False
        return window_hosts_process(window, process_id, process_name)

    def live_sessions(self, session_ids: Iterable[SessionId]) -> frozenset[SessionId]:
        """Return the live sessions.

        The subset whose window is still on screen — `window_for_session`
                for many sessions, paying for ONE window listing instead of one per
                session. Listing the windows costs a subprocess in the real plugins,
                and the session-list route asks about every visible session at once.

        Returns:
            Live sessions.

        """
        on_screen = {
            window.window_id: window.tags.get(terminal_values.SESSION_WINDOW_TAG)
            for window in self._plugin.metadata.windows()
        }
        live = set()
        for session_id in session_ids:
            if self._session_is_live(session_id, on_screen):
                live.add(session_id)
        return frozenset(live)

    def current_window(self) -> WindowId | None:
        """Return the current window.

        Returns:
            Current window.

        """
        native = self._plugin.metadata.current_window_id()
        return WindowId(str(native)) if native else None

    def windows(self) -> tuple[terminal_values.WindowInfo, ...]:
        """Return the terminal windows for harness session discovery.

        Returns:
            Terminal windows for harness session discovery.

        """
        return self._plugin.metadata.windows()

    def session_for_window(self, window_id: WindowId | None) -> SessionId | None:
        """Return the session for window.

        Returns:
            Session for window.

        """
        native = terminal_values.WindowId(str(window_id)) if window_id else None
        for window in self._tab_windows(native):
            session_id = window.tags.get(terminal_values.SESSION_WINDOW_TAG)
            if session_id:
                return SessionId(session_id)
        return None


class _TerminalPaneDiscovery(_TerminalSessionWindows):
    """Discover and maintain session panes."""

    def session_panes_are_open(self, session_id: SessionId) -> bool:
        """Return the session panes are open.

        Returns:
            Session panes are open.

        """
        return self._tagged(terminal_values.ACTIVITY_PANE_TAG, session_id) is not None

    def _confirm_panes_alive(self, session_id: SessionId) -> SessionTerminalResult:
        for tag, pane_name in (
            (terminal_values.ACTIVITY_PANE_TAG, "mirror"),
            (terminal_values.SCOREBOARD_PANE_TAG, "scoreboard"),
        ):
            if self._tagged(tag, session_id) is None:
                return SessionTerminalResult(succeeded=False, reason=f"{pane_name} pane process exited on startup")
        return SessionTerminalResult(succeeded=True)

    def _close_session_panes(
        self,
        session_id: SessionId,
        *,
        clear_tab: bool,
    ) -> SessionTerminalResult:
        outcomes = self._close_owned_panes(session_id)
        session_window_id = self.window_for_session(session_id)
        if clear_tab and session_window_id is not None:
            native_session_window_id = terminal_values.WindowId(str(session_window_id))
            cleared_tags = {terminal_values.SESSION_WINDOW_TAG: ""}
            outcomes.extend(
                (
                    self._plugin.tabs.clear_tab_color(tab_models.TabColorClearRequest(native_session_window_id)),
                    self._plugin.metadata.tag_window(
                        metadata_models.WindowTagRequest(native_session_window_id, cleared_tags),
                    ),
                ),
            )
        return combined_outcomes(outcomes, "terminal pane close failed")

    def _close_owned_panes(self, session_id: SessionId) -> list[TerminalOutcome]:
        outcomes: list[TerminalOutcome] = []
        for tag in (terminal_values.SCOREBOARD_PANE_TAG, terminal_values.ACTIVITY_PANE_TAG):
            pane = self._tagged(tag, session_id)
            if pane is not None:
                outcomes.append(
                    self._plugin.panes.close_pane(pane_models.PaneCloseRequest(pane.window_id)),
                )
        return outcomes

    def _settle_scoreboard_height(self, session_id: SessionId) -> SessionTerminalResult:
        for _ in range(SCOREBOARD_RESIZE_ATTEMPTS):
            scoreboard = self._tagged(terminal_values.SCOREBOARD_PANE_TAG, session_id)
            if scoreboard is None:
                return SessionTerminalResult(succeeded=False, reason="scoreboard pane is not open")
            row_difference = SCOREBOARD_HEIGHT - scoreboard.lines
            if row_difference == 0:
                return SessionTerminalResult(succeeded=True)
            response = self._plugin.panes.resize_pane(
                pane_models.PaneResizeRequest(scoreboard.window_id, pane_models.SplitAxis.VERTICAL, row_difference),
            )
            if not response.succeeded:
                return SessionTerminalResult(succeeded=False, reason=response.reason)
            time.sleep(SCOREBOARD_RESIZE_SETTLE_SECONDS)
        scoreboard = self._tagged(terminal_values.SCOREBOARD_PANE_TAG, session_id)
        if scoreboard is not None and scoreboard.lines == SCOREBOARD_HEIGHT:
            return SessionTerminalResult(succeeded=True)
        return SessionTerminalResult(succeeded=False, reason="scoreboard pane did not reach its height")


class _TerminalPaneOpening(_TerminalPaneDiscovery):
    """Open session panes."""

    def open_session_panes(self, session_pane_request: SessionPaneRequest) -> SessionTerminalResult:
        """Open session panes.

        The mirror and scoreboard panes, as one gesture.

                Idempotent by rediscovery: a pane that is already open is found by its
                tag and left alone, so a toggle survives a daemon restart.

        Returns:
            The session terminal result.

        """
        session_id = str(session_pane_request.session_id)
        anchor_window_id = terminal_values.WindowId(str(session_pane_request.anchor_window_id))
        outcomes: list[TerminalOutcome] = [
            self._plugin.metadata.tag_window(
                metadata_models.WindowTagRequest(anchor_window_id, {terminal_values.SESSION_WINDOW_TAG: session_id}),
            ),
        ]
        if self._tagged(terminal_values.ACTIVITY_PANE_TAG, session_pane_request.session_id) is None:
            outcomes.append(
                self._open_activity_pane(session_pane_request, anchor_window_id),
            )
        if self._tagged(terminal_values.SCOREBOARD_PANE_TAG, session_pane_request.session_id) is None:
            outcomes.extend(
                (
                    self._open_scoreboard_pane(
                        session_pane_request.session_id,
                        anchor_window_id,
                    ),
                    self._settle_scoreboard_height(session_pane_request.session_id),
                ),
            )
        # Hand inner focus back to the host pane the splits took it from, which
        # restores the host's window title as the visible tab title.
        outcomes.append(self._plugin.panes.focus_window(pane_models.WindowFocusRequest(anchor_window_id)))
        # Named before the fold, not inside it: a pane process that died on
        # startup is the most useful thing we can say, and `_combined` reports
        # one reason for the whole composite.
        alive = self._confirm_panes_alive(session_pane_request.session_id)
        if not alive.succeeded:
            return alive
        return combined_outcomes(outcomes, "terminal pane setup failed")

    def _open_activity_pane(
        self,
        session_pane_request: SessionPaneRequest,
        anchor_window_id: terminal_values.WindowId,
    ) -> pane_results.PaneOpenResponse:
        return self._plugin.panes.open_pane(
            pane_models.PaneOpenRequest(
                command=pane_client.command("mirror", session_pane_request.session_id),
                working_directory="",
                title=MIRROR_PANE_TITLE,
                split=pane_models.SplitAxis.VERTICAL,
                size_percent=session_pane_request.activity_width_percent,
                anchor=pane_models.PaneAnchor(window_id=anchor_window_id),
                same_tab_as=anchor_window_id,
                tags={terminal_values.ACTIVITY_PANE_TAG: str(session_pane_request.session_id)},
            ),
        )

    def _open_scoreboard_pane(
        self,
        session_id: SessionId,
        anchor_window_id: terminal_values.WindowId,
    ) -> pane_results.PaneOpenResponse:
        return self._plugin.panes.open_pane(
            pane_models.PaneOpenRequest(
                command=pane_client.command("scoreboard", session_id),
                working_directory="",
                title=SCOREBOARD_PANE_TITLE,
                split=pane_models.SplitAxis.HORIZONTAL,
                size_percent=SCOREBOARD_SIZE_PERCENT,
                anchor=pane_models.PaneAnchor(tag=(terminal_values.ACTIVITY_PANE_TAG, str(session_id))),
                same_tab_as=anchor_window_id,
                tags={terminal_values.SCOREBOARD_PANE_TAG: str(session_id)},
            ),
        )


class _TerminalPaneControls(_TerminalPaneOpening):
    """Close, resize, and configure session panes."""

    def close_session_panes(self, session_id: SessionId) -> SessionTerminalResult:
        """Close session panes.

        Returns:
            The session terminal result.

        """
        return self._close_session_panes(session_id, clear_tab=True)

    def toggle_session_panes(
        self,
        session_id: SessionId,
        activity_width_percent: int,
        anchor_window_id: WindowId | None = None,
    ) -> SessionTerminalResult:
        """Toggle session panes.

        Returns:
            The session terminal result.

        """
        if self.session_panes_are_open(session_id):
            # A toggle-off keeps the tab colour: the session is still running
            # in that tab, only its display panes are gone.
            return self._close_session_panes(session_id, clear_tab=False)
        anchor_window_id = anchor_window_id or self.current_window() or self.window_for_session(session_id)
        if anchor_window_id is None:
            return SessionTerminalResult(succeeded=False, reason="session has no terminal window")
        return self.open_session_panes(SessionPaneRequest(session_id, anchor_window_id, activity_width_percent))

    def resize_activity_pane(self, session_id: SessionId, columns: int) -> SessionTerminalResult:
        """Return the resize activity pane.

        Returns:
            Resize activity pane.

        """
        activity = self._tagged(terminal_values.ACTIVITY_PANE_TAG, session_id)
        if activity is None:
            return SessionTerminalResult(succeeded=False, reason="activity pane is not open")
        response = self._plugin.panes.resize_pane(
            pane_models.PaneResizeRequest(activity.window_id, pane_models.SplitAxis.HORIZONTAL, columns),
        )
        return SessionTerminalResult(response.succeeded, response.reason)

    def activity_pane_geometry(self, session_id: SessionId) -> tuple[int, int] | None:
        """Return the activity pane geometry.

        (activity columns, the row's total columns), or None when the pane
                is not open.

                The row total is the HOST plus the activity pane, not the sum of every
                window in the tab: the scoreboard is stacked inside the activity pane's
                own column, so counting it would count that column twice — which is
                what once under-reported the pane's share and drove the width gestures
                far off their target.

        Returns:
            Activity pane geometry.

        """
        activity = self._tagged(terminal_values.ACTIVITY_PANE_TAG, session_id)
        if activity is None or not activity.columns:
            return None
        host = next(
            (
                window
                for window in self._plugin.metadata.windows()
                if window.tab_id == activity.tab_id and window.is_first_in_tab
            ),
            None,
        )
        if host is None:
            return None
        return activity.columns, host.columns + activity.columns

    def set_activity_pane_width(self, session_id: SessionId, percent: int) -> SessionTerminalResult:
        """Set activity pane width.

        Returns:
            The session terminal result.

        Raises:
            ValueError: If an input value is not valid.

        """
        if not MINIMUM_PANE_WIDTH_PERCENT <= percent <= MAXIMUM_PANE_WIDTH_PERCENT:
            message = "activity pane width must be between 1 and 99 percent"
            raise ValueError(message)
        geometry = self.activity_pane_geometry(session_id)
        if geometry is None:
            return SessionTerminalResult(succeeded=False, reason="activity pane is not open")
        current_columns, total_columns = geometry
        target_columns = round(total_columns * percent / PERCENT_SCALE)
        return self.resize_activity_pane(session_id, target_columns - current_columns)


class _TerminalTabs(_TerminalSessionWindows):
    """Rename and style session tabs."""

    def rename_session_tab(
        self,
        session_id: SessionId,
        title: str,
    ) -> SessionTerminalResult:
        """Set the explicit title of a live session tab.

        A parked session has no tab to update. That is a completed no-op, not
        a terminal failure.

        Returns:
            The session terminal result.

        """
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(succeeded=True)
        request = tab_models.TabRenameRequest(terminal_values.WindowId(str(window_id)), title)
        response = self._plugin.tabs.rename_tab(request)
        if not response.succeeded and self.window_for_session(session_id) is None:
            return SessionTerminalResult(succeeded=True)
        return SessionTerminalResult(response.succeeded, response.reason)

    def paint_session_tab(
        self,
        session_id: SessionId,
        tab_appearance: terminal_values.TabAppearance,
    ) -> SessionTerminalResult:
        """Paint session tab.

        Returns:
            The session terminal result.

        """
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(succeeded=False, reason="session has no terminal window")
        request = tab_models.TabColorSetRequest(terminal_values.WindowId(str(window_id)), tab_appearance)
        response = self._plugin.tabs.set_tab_color(request)
        return SessionTerminalResult(response.succeeded, response.reason)

    def clear_session_tab(self, session_id: SessionId) -> SessionTerminalResult:
        """Clear session tab.

        Returns:
            The session terminal result.

        """
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(succeeded=False, reason="session has no terminal window")
        request = tab_models.TabColorClearRequest(terminal_values.WindowId(str(window_id)))
        response = self._plugin.tabs.clear_tab_color(request)
        return SessionTerminalResult(response.succeeded, response.reason)


class TerminalAdapter(_TerminalPaneControls, _TerminalTabs):
    """Provide session-level operations for terminal windows and panes."""
