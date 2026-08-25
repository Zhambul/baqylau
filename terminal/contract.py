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

from dataclasses import dataclass
from typing import Callable, Protocol

from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
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
from terminal.models.values import WindowId, WindowInfo
from terminal.models.viewport import (
    ScreenReadRequest,
    ScreenReadResponse,
)


class TerminalTabs(Protocol):
    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        """A new tab running the request's command.

        May select the new tab inside the terminal, but must NOT make the
        terminal application take OS-level focus when it is backgrounded: the
        caller is a web request whose user is in a browser.
        """

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        """Close the whole tab containing the window — the session's own window
        and the panes split off it. The processes get SIGHUP and exit."""

    def rename_tab(self, tab_rename_request: TabRenameRequest) -> TabRenameResponse:
        """Explicitly title the tab containing the window. An explicit title is
        sticky: it stops following the active window's title, which may be
        owned by a harness's own title publisher. Use it only where the owning
        plugin declares that the terminal is the title writer."""

    def set_tab_color(self, tab_color_set_request: TabColorSetRequest) -> TabColorSetResponse:
        """Colour the tab containing the window, active AND inactive, so a
        background session stays visible."""

    def clear_tab_color(self, tab_color_clear_request: TabColorClearRequest) -> TabColorClearResponse:
        """Revert the tab containing the window to the theme default."""


class TerminalPanes(Protocol):
    def open_pane(self, pane_open_request: PaneOpenRequest) -> PaneOpenResponse:
        """Split a new pane next to the anchor. Arranging whatever layout the
        split needs is the implementation's business, not the caller's."""

    def close_pane(self, pane_close_request: PaneCloseRequest) -> PaneCloseResponse: ...

    def resize_pane(self, pane_resize_request: PaneResizeRequest) -> PaneResizeResponse: ...

    def focus_window(self, window_focus_request: WindowFocusRequest) -> WindowFocusResponse:
        """Move focus INSIDE the tab, without raising the application. With
        `WindowInfo.is_first_in_tab` to name the host, this is how focus is
        handed back after a pane took it."""


class TerminalMetadata(Protocol):
    def windows(self) -> tuple[WindowInfo, ...]:
        """Every window the terminal reports; () on failure.

        The one STATE READ in this protocol — everything else is a write. It
        answers liveness (a recorded window id can outlive its window), pane
        rediscovery (so a toggle stays idempotent across daemon restarts), tab
        grouping (which session owns the tab a keypress landed in), host
        detection, and the pane width arithmetic.
        """

    def tag_window(self, window_tag_request: WindowTagRequest) -> WindowTagResponse:
        """The write half of the tag read in `windows()`."""

    def current_window_id(self) -> WindowId | None:
        """This process's own window, from the terminal's environment.

        The origin of all window facts: only a process running INSIDE a
        window can observe which one it is. The hook client ships the answer as
        a header, and that is where a session's recorded window comes from.
        """


class TerminalInput(Protocol):
    def submit_text(self, text_submit_request: TextSubmitRequest) -> TextSubmitResponse: ...
    def send_key(self, key_send_request: KeySendRequest) -> KeySendResponse: ...


class TerminalViewport(Protocol):
    def read_screen(self, screen_read_request: ScreenReadRequest) -> ScreenReadResponse: ...


def _no_terminal_cleanup() -> None:
    return None


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
