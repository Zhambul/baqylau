"""The terminal that isn't there.

A concrete `TerminalPlugin` whose every operation returns its failure-shaped
response and whose one read returns nothing. Bootstrap wires it when `resolve()`
finds no terminal, so every service above stays unconditional — nothing has to
ask whether a terminal exists, and "no terminal" reads out of the audit as a
reason string like any other failure.
"""

from __future__ import annotations

from terminal.models.values import WindowId
from terminal.contract import (
    TerminalInput,
    TerminalMetadata,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.models.input import (
    KeySendRequest,
    KeySendResponse,
    TextInsertRequest,
    TextInsertResponse,
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
from terminal.models.values import WindowInfo
from terminal.models.viewport import ScreenReadRequest, ScreenReadResponse

NO_TERMINAL = "no terminal available"


class NullTabs(TerminalTabs):
    def open_tab(self, tab_open_request: TabOpenRequest) -> TabOpenResponse:
        return TabOpenResponse(False, None, NO_TERMINAL)

    def close_tab(self, tab_close_request: TabCloseRequest) -> TabCloseResponse:
        return TabCloseResponse(False, NO_TERMINAL)

    def rename_tab(self, tab_rename_request: TabRenameRequest) -> TabRenameResponse:
        return TabRenameResponse(False, NO_TERMINAL)

    def set_tab_color(self, tab_color_set_request: TabColorSetRequest) -> TabColorSetResponse:
        return TabColorSetResponse(False, NO_TERMINAL)

    def clear_tab_color(self, tab_color_clear_request: TabColorClearRequest) -> TabColorClearResponse:
        return TabColorClearResponse(False, NO_TERMINAL)


class NullPanes(TerminalPanes):
    def open_pane(self, pane_open_request: PaneOpenRequest) -> PaneOpenResponse:
        return PaneOpenResponse(False, None, NO_TERMINAL)

    def close_pane(self, pane_close_request: PaneCloseRequest) -> PaneCloseResponse:
        return PaneCloseResponse(False, NO_TERMINAL)

    def resize_pane(self, pane_resize_request: PaneResizeRequest) -> PaneResizeResponse:
        return PaneResizeResponse(False, NO_TERMINAL)

    def focus_window(self, window_focus_request: WindowFocusRequest) -> WindowFocusResponse:
        return WindowFocusResponse(False, NO_TERMINAL)


class NullMetadata(TerminalMetadata):
    def windows(self) -> tuple[WindowInfo, ...]:
        return ()

    def tag_window(self, window_tag_request: WindowTagRequest) -> WindowTagResponse:
        return WindowTagResponse(False, NO_TERMINAL)

    def current_window_id(self) -> WindowId | None:
        return None


class NullInput(TerminalInput):
    def insert_text(self, text_insert_request: TextInsertRequest) -> TextInsertResponse:
        return TextInsertResponse(False, NO_TERMINAL)

    def submit_text(self, text_submit_request: TextSubmitRequest) -> TextSubmitResponse:
        return TextSubmitResponse(False, NO_TERMINAL)

    def send_key(self, key_send_request: KeySendRequest) -> KeySendResponse:
        return KeySendResponse(False, NO_TERMINAL)


class NullViewport(TerminalViewport):
    def read_screen(self, screen_read_request: ScreenReadRequest) -> ScreenReadResponse:
        return ScreenReadResponse(False, None, NO_TERMINAL)

def null_plugin() -> TerminalPlugin:
    return TerminalPlugin(
        name="none",
        tabs=NullTabs(),
        panes=NullPanes(),
        metadata=NullMetadata(),
        input=NullInput(),
        viewport=NullViewport(),
    )
