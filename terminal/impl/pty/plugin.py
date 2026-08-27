# terminal/impl/pty/plugin.py — a pseudo-terminal as a TerminalPlugin.
#
# The five sub-protocols of terminal/contract.py over ptys this process owns.
# Everything a terminal APPLICATION provides — tabs, splits, tab colours, window
# focus — has no counterpart here and answers with the contract's failure shape
# and a reason, the same way the null plugin does for everything. What a pty
# genuinely has, it really implements: a program, a screen, keys, a size.
#
# Selected only by pinning `BAQYLAU_TERMINAL=pty`, never by detection. A pty is
# available on every POSIX machine, so a detector for it would fire wherever
# no real terminal is installed, and the daemon would launch harnesses into windows
# nobody can see — worse than having no terminal, which is at least visible as
# nothing happening.

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from itertools import count
from uuid import uuid4

import psutil

from terminal.models.values import TabId, WindowId
from terminal.contract import (
    TerminalInput,
    TerminalMetadata,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.impl.pty import keys
from terminal.impl.pty.window import PtyWindow, open_window
from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
    TextSubmitMode,
    TextSubmitRequest,
    TextSubmitResponse,
)
from terminal.models.metadata import WindowTagRequest, WindowTagResponse
from terminal.models.panes import (
    PaneCloseRequest,
    PaneCloseResponse,
    PaneOpenRequest,
    PaneOpenResponse,
    PaneResizeRequest,
    PaneResizeResponse,
    SplitAxis,
    WindowFocusRequest,
    WindowFocusResponse,
)
from terminal.models.tabs import (
    TabCloseRequest,
    TabCloseResponse,
    TabColorClearRequest,
    TabColorClearResponse,
    TabColorSetRequest,
    TabColorSetResponse,
    TabOpenRequest,
    TabOpenResponse,
    TabRenameRequest,
    TabRenameResponse,
)
from terminal.models.values import WindowInfo, WindowProcess
from terminal.models.viewport import ScreenReadRequest, ScreenReadResponse

NO_CHROME = "a pty has no tabs to show"
NO_SPLITS = "a pty has no panes to split"
NO_FOCUS = "a pty has no keyboard focus to move"
NO_WINDOW = "no such pty window"
# What an ANSI screen read would take: pyte keeps per-cell attributes, so the
# SGR runs could be reconstructed from the grid. Nothing asks a pty for one —
# the callers that read formatting are probing a screen a user is looking at —
# so it reports the limit instead of answering a different question than it was
# asked.
NO_ANSI = "the pty terminal reads plain screens only"

# How a program launched into one of these windows learns WHICH window it is in.
#
# Every window fact in the system originates in the launched process: a hook runs
# inside the session's own window and is the only thing that can observe which
# one that is, so it reads this from its environment and ships the answer as
# a raw event (`client/_http.py` WINDOW_ID_VARIABLES, which carries one name per
# terminal we can drive). A terminal that exported nothing left every session's
# window unknown, and with it every gesture that needs one — send-text,
# interrupt, backgrounding — declining with "session is not live" forever.
# A terminal with a window manager of its own hands its programs such a variable
# for free; a pseudo-terminal has no window manager and no such convention, so
# this establishes one.
WINDOW_ID_VARIABLE = "BAQYLAU_PTY_WINDOW_ID"
SUBMIT_PAINT_TIMEOUT_SECONDS = 2.0


class PtyWindows:
    """The open windows, and the environment their programs inherit.

    The environment is injected rather than read from `os.environ` here, for the
    same reason the channel to a terminal application is: the caller may need to
    hand the program a different one than this process holds — a test rig
    scrubbing the session identity it was itself started with, for one.
    """

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self.environment = dict(os.environ if environment is None else environment)
        self.windows: dict[WindowId, PtyWindow] = {}
        self.lock = threading.RLock()
        # Session facts are durable, but this in-memory terminal is not. Keep
        # its window identities unique after an application restart so a new
        # native run cannot deduplicate against a finished run that used the
        # same local counter value.
        self._namespace = uuid4().hex
        self._ids = count(1)

    def launch(
        self,
        command: tuple[str, ...],
        working_directory: str,
        environment: tuple[tuple[str, str], ...],
    ) -> PtyWindow | None:
        with self.lock:
            window_id = WindowId(f"{self._namespace}:{next(self._ids)}")
            child_environment = dict(self.environment)
            launch_environment = {
                str(name): str(value) for name, value in environment
            }
            child_environment.update(launch_environment)
        # Last, so the window's own identity cannot be overridden by a caller's
        # environment: this is the one value the terminal knows and the program
        # cannot be told by anyone else.
        child_environment[WINDOW_ID_VARIABLE] = window_id
        window = open_window(window_id, tuple(command), working_directory, child_environment)
        if window is not None:
            with self.lock:
                self.windows[window_id] = window
        return window

    def get(self, window_id: WindowId) -> PtyWindow | None:
        with self.lock:
            return self.windows.get(window_id)

    def close(self, window_id: WindowId) -> bool:
        with self.lock:
            window = self.windows.pop(window_id, None)
        if window is None:
            return False
        return window.close()

    def close_all(self) -> None:
        """Close every owned process group at the application boundary."""
        with self.lock:
            window_ids = tuple(self.windows)
        for window_id in window_ids:
            self.close(window_id)


class PtyTabs(TerminalTabs):
    def __init__(self, pty_windows: PtyWindows) -> None:
        self.pty_windows = pty_windows

    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        # The request's title is not applied, for the reason TabOpenRequest gives
        # for leaving it to the program — and there is nothing here to show it.
        window = self.pty_windows.launch(
            tab_open_request.command, tab_open_request.working_directory, tab_open_request.environment
        )
        if window is None:
            return TabOpenResponse(False, None, "pty launch failed")
        return TabOpenResponse(True, window.window_id)

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        closed = self.pty_windows.close(tab_close_request.window_id)
        return TabCloseResponse(closed, None if closed else NO_WINDOW)

    def rename_tab(self, tab_rename_request: TabRenameRequest) -> TabRenameResponse:
        # The canonical title is already stored. A headless PTY has no tab title
        # to update, so this operation is a completed no-op.
        return TabRenameResponse(True)

    def set_tab_color(self, tab_color_set_request: TabColorSetRequest) -> TabColorSetResponse:
        return TabColorSetResponse(False, NO_CHROME)

    def clear_tab_color(self, tab_color_clear_request: TabColorClearRequest) -> TabColorClearResponse:
        return TabColorClearResponse(False, NO_CHROME)


class PtyPanes(TerminalPanes):
    def __init__(self, pty_windows: PtyWindows) -> None:
        self.pty_windows = pty_windows

    def open_pane(self, pane_open_request: PaneOpenRequest) -> PaneOpenResponse:
        return PaneOpenResponse(False, None, NO_SPLITS)

    def close_pane(self, pane_close_request: PaneCloseRequest) -> PaneCloseResponse:
        closed = self.pty_windows.close(pane_close_request.window_id)
        return PaneCloseResponse(closed, None if closed else NO_WINDOW)

    def resize_pane(self, pane_resize_request: PaneResizeRequest) -> PaneResizeResponse:
        # The one pane operation a pty really has: a window size is a property
        # of the tty, and a program watching SIGWINCH reflows for it.
        with self.pty_windows.lock:
            window = self.pty_windows.get(pane_resize_request.window_id)
            if window is None:
                return PaneResizeResponse(False, NO_WINDOW)
            columns, lines = window.screen.columns, window.screen.lines
            if pane_resize_request.axis == SplitAxis.HORIZONTAL:
                columns = max(1, columns + pane_resize_request.cells)
            else:
                lines = max(1, lines + pane_resize_request.cells)
            resized = window.resize(columns, lines)
        return PaneResizeResponse(resized, None if resized else "pty resize failed")

    def focus_window(self, window_focus_request: WindowFocusRequest) -> WindowFocusResponse:
        return WindowFocusResponse(False, NO_FOCUS)


class PtyMetadata(TerminalMetadata):
    def __init__(self, pty_windows: PtyWindows) -> None:
        self.pty_windows = pty_windows

    def windows(self) -> tuple[WindowInfo, ...]:
        """Every open window, each alone in a tab of its own — which is what a
        pty is: one program on one tty, with nothing beside it.

        A window whose program has EXITED is not listed: in a real terminal that
        window is gone, and a caller asking what is on screen is asking about
        windows a user could be looking at. It is still readable by id until it
        is closed, so the screen a program died on survives for whoever needs to
        see why."""
        with self.pty_windows.lock:
            return tuple(
                WindowInfo(
                    window_id=window.window_id,
                    tab_id=TabId(str(window.window_id)),
                    tags=window.tags,
                    columns=window.screen.columns,
                    lines=window.screen.lines,
                    is_first_in_tab=True,
                    tab_is_active=True,
                    # Nothing here holds keyboard focus: there is no keyboard and no
                    # user. Reporting focus would make the mirror believe a window
                    # the user is looking at is on screen.
                    tab_is_focused=False,
                    is_active_in_tab=True,
                    processes=_window_processes(window),
                )
                for window in self.pty_windows.windows.values()
                if window.process.poll() is None
            )

    def tag_window(self, window_tag_request: WindowTagRequest) -> WindowTagResponse:
        with self.pty_windows.lock:
            window = self.pty_windows.get(window_tag_request.window_id)
            if window is None:
                return WindowTagResponse(False, NO_WINDOW)
            # Stored IN the window, so a tag has exactly the window's lifetime —
            # the property the contract asks of this operation.
            window.tags.update(window_tag_request.tags)
        return WindowTagResponse(True)

    def current_window_id(self) -> WindowId | None:
        # The process asking is never inside one of these: it OWNS them.
        return None


def _window_processes(pty_window: PtyWindow) -> tuple[WindowProcess, ...]:
    """The wrapper and every live descendant hosted by this PTY window."""
    try:
        root = psutil.Process(pty_window.process.pid)
        descendants = (
            pty_window.observe_descendants()
            if isinstance(pty_window, PtyWindow)
            else tuple(root.children(recursive=True))
        )
        process_tree = (root, *descendants)
    except (psutil.Error, OSError, SystemError):
        return (WindowProcess(pty_window.process.pid, pty_window.command),)
    reported: list[WindowProcess] = []
    for process in process_tree:
        try:
            reported.append(WindowProcess(process.pid, tuple(process.cmdline())))
        except (psutil.Error, OSError, SystemError):
            continue
    return tuple(reported) or (WindowProcess(pty_window.process.pid, pty_window.command),)


class PtyInput(TerminalInput):
    def __init__(self, pty_windows: PtyWindows) -> None:
        self.pty_windows = pty_windows

    def submit_text(self, text_submit_request: TextSubmitRequest) -> TextSubmitResponse:
        with self.pty_windows.lock:
            window = self.pty_windows.get(text_submit_request.window_id)
            if window is None:
                return TextSubmitResponse(False, NO_WINDOW)
            payload = text_submit_request.text.encode("utf-8")
            if text_submit_request.mode == TextSubmitMode.PASTE:
                payload = keys.BRACKETED_PASTE_START + payload + keys.BRACKETED_PASTE_END
            # The Enter stays a separate keystroke, so it submits rather than
            # becoming a newline in the draft (TextSubmitRequest). The delay also
            # keeps the operating system from coalescing both writes into one read,
            # which a chunk-based TUI can interpret as one paste with a newline.
            revision = window.revision
            delivered = window.write(payload)
            if delivered:
                # Wait until the TUI has consumed the text. A fixed
                # sleep can expire while the child is descheduled and lets the
                # OS coalesce text + Enter into one terminal read.
                window.wait_for_screen_change(revision, SUBMIT_PAINT_TIMEOUT_SECONDS)
                delivered = window.write(keys.NAMED_KEYS["enter"])
        return TextSubmitResponse(delivered, None if delivered else "pty input failed")

    def send_key(self, key_send_request: KeySendRequest) -> KeySendResponse:
        with self.pty_windows.lock:
            window = self.pty_windows.get(key_send_request.window_id)
            if window is None:
                return KeySendResponse(False, NO_WINDOW)
            payload = keys.chord(key_send_request.key)
            if payload is None:
                return KeySendResponse(False, f"the pty terminal cannot send {key_send_request.key!r}")
            delivered = window.write(payload)
        return KeySendResponse(delivered, None if delivered else "pty key input failed")


class PtyViewport(TerminalViewport):
    def __init__(self, pty_windows: PtyWindows) -> None:
        self.pty_windows = pty_windows

    def read_screen(self, screen_read_request: ScreenReadRequest) -> ScreenReadResponse:
        if screen_read_request.ansi:
            return ScreenReadResponse(False, None, NO_ANSI)
        with self.pty_windows.lock:
            window = self.pty_windows.get(screen_read_request.window_id)
            if window is None:
                return ScreenReadResponse(False, None, NO_WINDOW)
            return ScreenReadResponse(True, window.display())


def pty_plugin(pty_windows: PtyWindows | None = None) -> TerminalPlugin:
    pty_windows = pty_windows if pty_windows is not None else PtyWindows()
    return TerminalPlugin(
        name="pty",
        tabs=PtyTabs(pty_windows),
        panes=PtyPanes(pty_windows),
        metadata=PtyMetadata(pty_windows),
        input=PtyInput(pty_windows),
        viewport=PtyViewport(pty_windows),
        close=pty_windows.close_all,
    )
