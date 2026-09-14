# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the plugin module."""
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

from typing import TYPE_CHECKING, override

from terminal.contract import (
    TerminalInput,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.impl.pty import keys
from terminal.impl.pty.metadata import PtyMetadata
from terminal.impl.pty.registry import PtyWindows
from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
    TextInsertRequest,
    TextInsertResponse,
    TextSubmitRequest,
    TextSubmitResponse,
)
from terminal.models.pane_results import (
    PaneCloseResponse,
    PaneOpenResponse,
    PaneResizeResponse,
    WindowFocusResponse,
)
from terminal.models.panes import (
    PaneCloseRequest,
    PaneOpenRequest,
    PaneResizeRequest,
    SplitAxis,
    WindowFocusRequest,
)
from terminal.models.tab_results import (
    TabCloseResponse,
    TabColorClearResponse,
    TabColorSetResponse,
    TabOpenResponse,
    TabRenameResponse,
)
from terminal.models.viewport import ScreenReadRequest, ScreenReadResponse

if TYPE_CHECKING:
    from terminal.models.tabs import (
        TabCloseRequest,
        TabColorClearRequest,
        TabColorSetRequest,
        TabOpenRequest,
        TabRenameRequest,
    )

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
SUBMIT_PAINT_TIMEOUT_SECONDS = 2.0


class PtyTabs(TerminalTabs):
    """Represent pty tabs."""

    def __init__(self, pty_windows: PtyWindows) -> None:
        """Initialize PTY tab operations."""
        self.pty_windows = pty_windows

    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        # The request's title is not applied, for the reason TabOpenRequest gives
        # for leaving it to the program — and there is nothing here to show it.
        """Open tab.

        Returns:
            The tab open response.

        """
        try:
            window = self.pty_windows.launch(
                tab_open_request.command,
                tab_open_request.working_directory,
                tab_open_request.environment,
            )
        except OSError as error:
            return TabOpenResponse(succeeded=False, window_id=None, reason=f"pty launch failed: {error}")
        return TabOpenResponse(succeeded=True, window_id=window.window_id)

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        """Close tab.

        Returns:
            The tab close response.

        """
        closed = self.pty_windows.close(tab_close_request.window_id)
        return TabCloseResponse(closed, None if closed else NO_WINDOW)

    @override
    def rename_tab(self, tab_rename_request: TabRenameRequest) -> TabRenameResponse:
        # The canonical title is already stored. A headless PTY has no tab title
        # to update, so this operation is a completed no-op.
        """Rename tab.

        Returns:
            The tab rename response.

        """
        return TabRenameResponse(succeeded=True)

    @override
    def set_tab_color(self, tab_color_set_request: TabColorSetRequest) -> TabColorSetResponse:
        """Set tab color.

        Returns:
            The tab color set response.

        """
        return TabColorSetResponse(succeeded=False, reason=NO_CHROME)

    @override
    def clear_tab_color(self, tab_color_clear_request: TabColorClearRequest) -> TabColorClearResponse:
        """Clear tab color.

        Returns:
            The tab color clear response.

        """
        return TabColorClearResponse(succeeded=False, reason=NO_CHROME)


class PtyPanes(TerminalPanes):
    """Represent pty panes."""

    def __init__(self, pty_windows: PtyWindows) -> None:
        """Initialize PTY pane operations."""
        self.pty_windows = pty_windows

    @override
    def open_pane(self, pane_open_request: PaneOpenRequest) -> PaneOpenResponse:
        """Open pane.

        Returns:
            The pane open response.

        """
        return PaneOpenResponse(succeeded=False, window_id=None, reason=NO_SPLITS)

    def close_pane(self, pane_close_request: PaneCloseRequest) -> PaneCloseResponse:
        """Close pane.

        Returns:
            The pane close response.

        """
        closed = self.pty_windows.close(pane_close_request.window_id)
        return PaneCloseResponse(closed, None if closed else NO_WINDOW)

    def resize_pane(self, pane_resize_request: PaneResizeRequest) -> PaneResizeResponse:
        # The one pane operation a pty really has: a window size is a property
        # of the tty, and a program watching SIGWINCH reflows for it.
        """Return the resize pane.

        Returns:
            Resize pane.

        """
        with self.pty_windows.lock:
            window = self.pty_windows.get(pane_resize_request.window_id)
            if window is None:
                return PaneResizeResponse(succeeded=False, reason=NO_WINDOW)
            columns, lines = window.screen.columns, window.screen.lines
            if pane_resize_request.axis == SplitAxis.HORIZONTAL:
                columns = max(1, columns + pane_resize_request.cells)
            else:
                lines = max(1, lines + pane_resize_request.cells)
            resized = window.resize(columns, lines)
        return PaneResizeResponse(resized, None if resized else "pty resize failed")

    @override
    def focus_window(self, window_focus_request: WindowFocusRequest) -> WindowFocusResponse:
        """Return the focus window.

        Returns:
            Focus window.

        """
        return WindowFocusResponse(succeeded=False, reason=NO_FOCUS)


class PtyInput(TerminalInput):
    """Represent pty input."""

    def __init__(self, pty_windows: PtyWindows) -> None:
        """Initialize PTY input operations."""
        self.pty_windows = pty_windows

    def insert_text(self, text_insert_request: TextInsertRequest) -> TextInsertResponse:
        """Return the insert text.

        Returns:
            Insert text.

        """
        with self.pty_windows.lock:
            window = self.pty_windows.get(text_insert_request.window_id)
            if window is None:
                return TextInsertResponse(succeeded=False, reason=NO_WINDOW)
            payload = keys.payload(text_insert_request.text, text_insert_request.mode)
            delivered = window.write(payload)
        return TextInsertResponse(delivered, None if delivered else "pty input failed")

    def submit_text(self, text_submit_request: TextSubmitRequest) -> TextSubmitResponse:
        """Submit text.

        Returns:
            The text submit response.

        """
        with self.pty_windows.lock:
            window = self.pty_windows.get(text_submit_request.window_id)
            if window is None:
                return TextSubmitResponse(succeeded=False, reason=NO_WINDOW)
            payload = keys.payload(text_submit_request.text, text_submit_request.mode)
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
        """Send key.

        Returns:
            The key send response.

        """
        with self.pty_windows.lock:
            window = self.pty_windows.get(key_send_request.window_id)
            if window is None:
                return KeySendResponse(succeeded=False, reason=NO_WINDOW)
            payload = keys.chord(key_send_request.key)
            if payload is None:
                return KeySendResponse(succeeded=False, reason=f"the pty terminal cannot send {key_send_request.key!r}")
            delivered = window.write(payload)
        return KeySendResponse(delivered, None if delivered else "pty key input failed")


class PtyViewport(TerminalViewport):
    """Represent pty viewport."""

    def __init__(self, pty_windows: PtyWindows) -> None:
        """Initialize PTY viewport operations."""
        self.pty_windows = pty_windows

    def read_screen(self, screen_read_request: ScreenReadRequest) -> ScreenReadResponse:
        """Return screen.

        Returns:
            Screen.

        """
        if screen_read_request.ansi:
            return ScreenReadResponse(succeeded=False, text=None, reason=NO_ANSI)
        with self.pty_windows.lock:
            window = self.pty_windows.get(screen_read_request.window_id)
            if window is None:
                return ScreenReadResponse(succeeded=False, text=None, reason=NO_WINDOW)
            return ScreenReadResponse(succeeded=True, text=window.display())


def pty_plugin(pty_windows: PtyWindows | None = None) -> TerminalPlugin:
    """Return the pty plugin.

    Returns:
        Pty plugin.

    """
    pty_windows = PtyWindows() if pty_windows is None else pty_windows
    return TerminalPlugin(
        name="pty",
        tabs=PtyTabs(pty_windows),
        panes=PtyPanes(pty_windows),
        metadata=PtyMetadata(pty_windows),
        input=PtyInput(pty_windows),
        viewport=PtyViewport(pty_windows),
        close=pty_windows.close_all,
    )


def build_plugin() -> TerminalPlugin:
    """Build the terminal plugin.

    Returns:
        The terminal plugin.

    """
    return pty_plugin()
