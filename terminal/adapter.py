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
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from core import clients
from domain.ids import SessionId, WindowId
from terminal.contract import TerminalPlugin
from terminal.models import (
    ACTIVITY_PANE_TAG,
    PaneAnchor,
    PaneCloseRequest,
    PaneOpenRequest,
    PaneResizeRequest,
    SCOREBOARD_PANE_TAG,
    SESSION_WINDOW_TAG,
    SplitAxis,
    TabAppearance,
    TabColorClearRequest,
    TabColorSetRequest,
    TabRenameRequest,
    WindowFocusRequest,
    WindowInfo,
    WindowTagRequest,
)

# The terminal's own window id (`terminal/models/`), distinct from the domain
# fact of the same name: `terminal/` may depend on nothing outside itself, so
# this module — the one place a session's RAW EVENT (`WindowId` above) meets a
# live terminal window — converts explicitly at the boundary rather than
# reusing one NewType across it.
from terminal.models.values import WindowId as NativeWindowId
from terminal.ownership import window_hosts_process

if TYPE_CHECKING:
    from repository.contract.sessions import SessionRepository

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

# Both panes are one client program, told which stream to open. We launch it, so
# it is free to move and free to be told things — the name is here, beside the
# only code that runs it, and `core/clients.py` owns nothing but the path.
PANE_CLIENT = "terminal_pane.py"


class TerminalOutcome(Protocol):
    """What every per-window response below has in common: it worked, or it
    didn't.

    The gestures in this file are COMPOSITES — opening a session's panes tags a
    window, opens two panes and restores focus, each answering with its own
    response type. They are collected in one list and folded by `_combined`,
    which only ever reads `.succeeded`, so that single field is the whole
    contract between them and this module. Structural, deliberately: the
    response types live in terminal/models/ and are not going to grow a shared
    base class for one bool.
    """

    # A read-only property, not a bare `succeeded: bool` attribute: the
    # responses are all frozen dataclasses, and a plain annotation here would
    # demand a SETTABLE field none of them has.
    @property
    def succeeded(self) -> bool: ...


@dataclass(frozen=True)
class SessionTerminalResult:
    """One session-level gesture's outcome. The caller audits the reason."""

    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class SessionPaneRequest:
    session_id: SessionId
    anchor_window_id: WindowId
    activity_width_percent: int

    def __post_init__(self) -> None:
        if not 1 <= self.activity_width_percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")


class TerminalAdapter:
    def __init__(self, terminal_plugin: TerminalPlugin, sessions: "SessionRepository") -> None:
        self._plugin = terminal_plugin
        self._sessions = sessions

    # --- session ⇄ window ---------------------------------------------------
    def window_for_session(self, session_id: SessionId) -> WindowId | None:
        """The session's window, when it is still on screen."""
        session = self._sessions.find(session_id)
        window_id = session.terminal_window_id if session is not None else None
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
        windows: tuple[WindowInfo, ...],
    ) -> bool:
        native = NativeWindowId(str(window_id))
        window = next((item for item in windows if item.window_id == native), None)
        if window is None:
            return False
        owner = window.tags.get(SESSION_WINDOW_TAG)
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
        """
        native = NativeWindowId(str(window_id))
        window = next(
            (item for item in self._plugin.metadata.windows() if item.window_id == native),
            None,
        )
        if window is None:
            return False
        return window_hosts_process(window, process_id, process_name)

    def live_sessions(self, session_ids: Iterable[SessionId]) -> frozenset[SessionId]:
        """The subset whose window is still on screen — `window_for_session`
        for many sessions, paying for ONE window listing instead of one per
        session. Listing the windows costs a subprocess in the real plugins,
        and the session-list route asks about every visible session at once."""
        on_screen = {
            window.window_id: window.tags.get(SESSION_WINDOW_TAG) for window in self._plugin.metadata.windows()
        }
        live = set()
        for session_id in session_ids:
            session = self._sessions.find(session_id)
            window_id = session.terminal_window_id if session is not None else None
            if not window_id:
                continue
            native = NativeWindowId(str(window_id))
            owner = on_screen.get(native)
            if native in on_screen and owner == str(session_id):
                live.add(session_id)
        return frozenset(live)

    def current_window(self) -> WindowId | None:
        native = self._plugin.metadata.current_window_id()
        return WindowId(str(native)) if native else None

    def windows(self) -> tuple[WindowInfo, ...]:
        """Return the terminal windows for harness session discovery."""
        return self._plugin.metadata.windows()

    def session_for_window(self, window_id: WindowId | None) -> SessionId | None:
        native = NativeWindowId(str(window_id)) if window_id else None
        for window in self._tab_windows(native):
            session_id = window.tags.get(SESSION_WINDOW_TAG)
            if session_id:
                return SessionId(session_id)
        return None

    def current_session(self) -> SessionId | None:
        return self.session_for_window(None)

    # --- panes ---------------------------------------------------------------
    def session_panes_are_open(self, session_id: SessionId) -> bool:
        return self._tagged(ACTIVITY_PANE_TAG, session_id) is not None

    def open_session_panes(self, session_pane_request: SessionPaneRequest) -> SessionTerminalResult:
        """The mirror and scoreboard panes, as one gesture.

        Idempotent by rediscovery: a pane that is already open is found by its
        tag and left alone, so a toggle survives a daemon restart.
        """
        session_id = str(session_pane_request.session_id)
        anchor_window_id = NativeWindowId(str(session_pane_request.anchor_window_id))
        outcomes: list[TerminalOutcome] = [
            self._plugin.metadata.tag_window(WindowTagRequest(anchor_window_id, {SESSION_WINDOW_TAG: session_id}))
        ]
        if self._tagged(ACTIVITY_PANE_TAG, session_pane_request.session_id) is None:
            activity_tags = {ACTIVITY_PANE_TAG: session_id}
            outcomes.append(
                self._plugin.panes.open_pane(
                    PaneOpenRequest(
                        command=self._pane_command("mirror", session_pane_request.session_id),
                        working_directory="",
                        title=MIRROR_PANE_TITLE,
                        split=SplitAxis.VERTICAL,
                        size_percent=session_pane_request.activity_width_percent,
                        anchor=PaneAnchor(window_id=anchor_window_id),
                        same_tab_as=anchor_window_id,
                        tags=activity_tags,
                    )
                )
            )
        if self._tagged(SCOREBOARD_PANE_TAG, session_pane_request.session_id) is None:
            scoreboard_tags = {SCOREBOARD_PANE_TAG: session_id}
            outcomes.append(
                self._plugin.panes.open_pane(
                    PaneOpenRequest(
                        command=self._pane_command("scoreboard", session_pane_request.session_id),
                        working_directory="",
                        title=SCOREBOARD_PANE_TITLE,
                        split=SplitAxis.HORIZONTAL,
                        size_percent=SCOREBOARD_SIZE_PERCENT,
                        # The scoreboard sits under the MIRROR, not under the session's
                        # own window — it shares the mirror's column.
                        anchor=PaneAnchor(tag=(ACTIVITY_PANE_TAG, session_id)),
                        same_tab_as=anchor_window_id,
                        tags=scoreboard_tags,
                    )
                )
            )
            outcomes.append(self._settle_scoreboard_height(session_pane_request.session_id))
        # Hand inner focus back to the host pane the splits took it from, which
        # restores the host's window title as the visible tab title.
        outcomes.append(self._plugin.panes.focus_window(WindowFocusRequest(anchor_window_id)))
        # Named before the fold, not inside it: a pane process that died on
        # startup is the most useful thing we can say, and `_combined` reports
        # one reason for the whole composite.
        alive = self._confirm_panes_alive(session_pane_request.session_id)
        if not alive.succeeded:
            return alive
        return self._combined(outcomes, "terminal pane setup failed")

    def close_session_panes(self, session_id: SessionId) -> SessionTerminalResult:
        return self._close_session_panes(session_id, clear_tab=True)

    def toggle_session_panes(
        self,
        session_id: SessionId,
        activity_width_percent: int,
        anchor_window_id: WindowId | None = None,
    ) -> SessionTerminalResult:
        if self.session_panes_are_open(session_id):
            # A toggle-off keeps the tab colour: the session is still running
            # in that tab, only its display panes are gone.
            return self._close_session_panes(session_id, clear_tab=False)
        anchor_window_id = anchor_window_id or self.current_window() or self.window_for_session(session_id)
        if anchor_window_id is None:
            return SessionTerminalResult(False, "session has no terminal window")
        return self.open_session_panes(SessionPaneRequest(session_id, anchor_window_id, activity_width_percent))

    def resize_activity_pane(self, session_id: SessionId, columns: int) -> SessionTerminalResult:
        activity = self._tagged(ACTIVITY_PANE_TAG, session_id)
        if activity is None:
            return SessionTerminalResult(False, "activity pane is not open")
        response = self._plugin.panes.resize_pane(PaneResizeRequest(activity.window_id, SplitAxis.HORIZONTAL, columns))
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
        if not 1 <= percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")
        geometry = self.activity_pane_geometry(session_id)
        if geometry is None:
            return SessionTerminalResult(False, "activity pane is not open")
        current_columns, total_columns = geometry
        target_columns = round(total_columns * percent / 100)
        return self.resize_activity_pane(session_id, target_columns - current_columns)

    # --- tabs ----------------------------------------------------------------
    def rename_session_tab(
        self,
        session_id: SessionId,
        title: str,
    ) -> SessionTerminalResult:
        """Set the explicit title of a live session tab.

        A parked session has no tab to update. That is a completed no-op, not
        a terminal failure.
        """
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(True)
        request = TabRenameRequest(NativeWindowId(str(window_id)), title)
        response = self._plugin.tabs.rename_tab(request)
        return SessionTerminalResult(response.succeeded, response.reason)

    def paint_session_tab(
        self,
        session_id: SessionId,
        tab_appearance: TabAppearance,
    ) -> SessionTerminalResult:
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(False, "session has no terminal window")
        request = TabColorSetRequest(NativeWindowId(str(window_id)), tab_appearance)
        response = self._plugin.tabs.set_tab_color(request)
        return SessionTerminalResult(response.succeeded, response.reason)

    def clear_session_tab(self, session_id: SessionId) -> SessionTerminalResult:
        window_id = self.window_for_session(session_id)
        if window_id is None:
            return SessionTerminalResult(False, "session has no terminal window")
        request = TabColorClearRequest(NativeWindowId(str(window_id)))
        response = self._plugin.tabs.clear_tab_color(request)
        return SessionTerminalResult(response.succeeded, response.reason)

    # --- internals -----------------------------------------------------------
    @staticmethod
    def _pane_command(kind: str, session_id: SessionId) -> tuple[str, ...]:
        """The argv a terminal runs for one pane.

        The daemon's address is PASSED, not shared: a pane imports nothing of
        ours, which is what makes it a program a refactor here cannot break.
        """
        return clients.command(PANE_CLIENT, session_id, kind)

    def _confirm_panes_alive(self, session_id: SessionId) -> SessionTerminalResult:
        """Both panes, still there a moment after the launch.

        The terminal reports a launch as successful the instant it has made the
        WINDOW, and a pane process that exits on startup takes its window with
        it — so without this the composite fails with a reason describing a
        symptom ("scoreboard pane is not open") instead of the cause.
        """
        for tag, what in ((ACTIVITY_PANE_TAG, "mirror"), (SCOREBOARD_PANE_TAG, "scoreboard")):
            if self._tagged(tag, session_id) is None:
                return SessionTerminalResult(False, f"{what} pane process exited on startup")
        return SessionTerminalResult(True)

    def _close_session_panes(
        self,
        session_id: SessionId,
        *,
        clear_tab: bool,
    ) -> SessionTerminalResult:
        outcomes: list[TerminalOutcome] = []
        for tag in (SCOREBOARD_PANE_TAG, ACTIVITY_PANE_TAG):
            pane = self._tagged(tag, session_id)
            if pane is not None:
                outcomes.append(self._plugin.panes.close_pane(PaneCloseRequest(pane.window_id)))
        session_window_id = self.window_for_session(session_id)
        if clear_tab and session_window_id is not None:
            native_session_window_id = NativeWindowId(str(session_window_id))
            cleared_tags = {SESSION_WINDOW_TAG: ""}
            outcomes.append(self._plugin.tabs.clear_tab_color(TabColorClearRequest(native_session_window_id)))
            outcomes.append(
                self._plugin.metadata.tag_window(
                    WindowTagRequest(native_session_window_id, cleared_tags)
                )
            )
        return self._combined(outcomes, "terminal pane close failed")

    def _settle_scoreboard_height(self, session_id: SessionId) -> SessionTerminalResult:
        for _attempt in range(SCOREBOARD_RESIZE_ATTEMPTS):
            scoreboard = self._tagged(SCOREBOARD_PANE_TAG, session_id)
            if scoreboard is None:
                return SessionTerminalResult(False, "scoreboard pane is not open")
            row_difference = SCOREBOARD_HEIGHT - scoreboard.lines
            if row_difference == 0:
                return SessionTerminalResult(True)
            response = self._plugin.panes.resize_pane(
                PaneResizeRequest(scoreboard.window_id, SplitAxis.VERTICAL, row_difference)
            )
            if not response.succeeded:
                return SessionTerminalResult(False, response.reason)
            time.sleep(SCOREBOARD_RESIZE_SETTLE_SECONDS)
        scoreboard = self._tagged(SCOREBOARD_PANE_TAG, session_id)
        if scoreboard is not None and scoreboard.lines == SCOREBOARD_HEIGHT:
            return SessionTerminalResult(True)
        return SessionTerminalResult(False, "scoreboard pane did not reach its height")

    def _tagged(self, tag: str, session_id: SessionId) -> WindowInfo | None:
        return next(
            (window for window in self._plugin.metadata.windows() if window.tags.get(tag) == str(session_id)), None
        )

    def _tab_windows(self, window_id: NativeWindowId | None) -> tuple[WindowInfo, ...]:
        """The windows of one tab: the tab holding `window_id`, or — when no
        window is named (a caller with no terminal environment of its own) —
        the focused terminal's active tab."""
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

    @staticmethod
    def _combined(outcomes: list[TerminalOutcome], reason: str) -> SessionTerminalResult:
        succeeded = all(outcome.succeeded for outcome in outcomes)
        return SessionTerminalResult(succeeded, None if succeeded else reason)
