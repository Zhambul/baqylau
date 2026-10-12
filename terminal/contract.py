# Copyright (c) 2026 Zhambyl Yermagambet
"""The terminal implementation boundary — five narrow protocols, one plugin.

A terminal is a PLUGIN, the same shape as a harness plugin: a frozen dataclass
with one typed field per sub-protocol, resolved once at bootstrap and passed
down. Consumers take the field they need — the launcher takes `tabs`, a screen
driver takes `input` + `viewport` — so what a component can do to a terminal is
readable from its constructor.

Every write is best-effort and SILENT: a failure is a failure-shaped response,
never an exception. The caller audits what matters.

This file and `terminal/models/` know nothing about sessions, harnesses, or any
concrete terminal. They are keyed on window ids and nothing else, which is why
the harness contract may import them without inverting a layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:

    from terminal.models.input import (
        KeySendRequest,
        KeySendResponse,
        TextInsertRequest,
        TextInsertResponse,
        TextSubmitRequest,
        TextSubmitResponse,
    )
    from terminal.models.metadata import WindowTagRequest, WindowTagResponse
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
        WindowFocusRequest,
    )
    from terminal.models.tab_results import (
        TabCloseResponse,
        TabColorClearResponse,
        TabColorSetResponse,
        TabOpenResponse,
        TabRenameResponse,
    )
    from terminal.models.tabs import (
        TabCloseRequest,
        TabColorClearRequest,
        TabColorSetRequest,
        TabOpenRequest,
        TabRenameRequest,
    )
    from terminal.models.values import WindowId, WindowInfo
    from terminal.models.viewport import (
        ScreenReadRequest,
        ScreenReadResponse,
    )


class TerminalTabs(Protocol):
    """Represent terminal tabs."""

    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        """Open tab.

        A new tab running the request's command.

                May select the new tab inside the terminal, but must NOT make the
                terminal application take OS-level focus when it is backgrounded: the
                caller is a web request whose user is in a browser.
        """

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        """Close tab.

        Close the whole tab containing the window — the session's own window
                and the panes split off it. The processes get SIGHUP and exit.
        """

    def rename_tab(self, tab_rename_request: TabRenameRequest) -> TabRenameResponse:
        """Rename tab.

        Explicitly title the tab containing the window. An explicit title is
                sticky: it stops following the active window's title, which may be
                owned by a harness's own title publisher. Use it only where the owning
                plugin declares that the terminal is the title writer.
        """

    def set_tab_color(self, tab_color_set_request: TabColorSetRequest) -> TabColorSetResponse:
        """Set tab color.

        Colour the tab containing the window, active AND inactive, so a
                background session stays visible.
        """

    def clear_tab_color(self, tab_color_clear_request: TabColorClearRequest) -> TabColorClearResponse:
        """Revert the tab containing the window to the theme default."""


class TerminalPanes(Protocol):
    """Represent terminal panes."""

    def open_pane(self, pane_open_request: PaneOpenRequest) -> PaneOpenResponse:
        """Open pane.

        Split a new pane next to the anchor. Arranging whatever layout the
                split needs is the implementation's business, not the caller's.
        """

    def close_pane(self, pane_close_request: PaneCloseRequest) -> PaneCloseResponse:
        """Close pane."""
        ...

    def resize_pane(self, pane_resize_request: PaneResizeRequest) -> PaneResizeResponse:
        """Return the resize pane."""
        ...

    def focus_window(self, window_focus_request: WindowFocusRequest) -> WindowFocusResponse:
        """Return the focus window.

        Move focus INSIDE the tab, without raising the application. With
                `WindowInfo.is_first_in_tab` to name the host, this is how focus is
                handed back after a pane took it.
        """


class TerminalMetadata(Protocol):
    """Represent terminal metadata."""

    def windows(self) -> tuple[WindowInfo, ...]:
        """Every window the terminal reports; () on failure.

        The one STATE READ in this protocol — everything else is a write. It
        answers liveness (a recorded window id can outlive its window), pane
        rediscovery (so a toggle stays idempotent across daemon restarts), tab
        grouping (which session owns the tab a keypress landed in), host
        detection, and the pane width arithmetic.
        """

    def last_windows(self) -> tuple[WindowInfo, ...]:
        """Return the windows of the last `windows` read, without a new read.

        A display read uses it, so that it does not wait on a busy terminal.
        This default reads again, which is right for a terminal that keeps its
        windows in memory.

        Returns:
            The windows that the last read found.

        """
        return self.windows()

    def tag_window(self, window_tag_request: WindowTagRequest) -> WindowTagResponse:
        """Return the tag window.

        The write half of the tag read in `windows()`.
        """

    def current_window_id(self) -> WindowId | None:
        """Return the current window ID.

        This process's own window, from the terminal's environment.

                The origin of all window facts: only a process running INSIDE a
                window can observe which one it is. The hook client ships the answer as
                a header, and that is where a session's recorded window comes from.
        """


class TerminalInput(Protocol):
    """Represent terminal input."""

    def insert_text(self, text_insert_request: TextInsertRequest) -> TextInsertResponse:
        """Return the insert text."""
        ...

    def submit_text(self, text_submit_request: TextSubmitRequest) -> TextSubmitResponse:
        """Submit text."""
        ...

    def send_key(self, key_send_request: KeySendRequest) -> KeySendResponse:
        """Send key."""
        ...


class TerminalViewport(Protocol):
    """Represent terminal viewport."""

    def read_screen(self, screen_read_request: ScreenReadRequest) -> ScreenReadResponse:
        """Return screen."""
        ...


def _no_terminal_cleanup() -> None:
    """Perform no cleanup for an external terminal."""


@dataclass(frozen=True)
class TerminalPlugin:
    """One terminal, composed."""

    name: str
    tabs: TerminalTabs
    panes: TerminalPanes
    metadata: TerminalMetadata
    input: TerminalInput
    viewport: TerminalViewport
    # A real terminal application owns its windows outside this process. The
    # headless PTY implementation does not: its child process groups belong to
    # this plugin and must be reaped when the application lifetime ends.
    close: Callable[[], None] = _no_terminal_cleanup
