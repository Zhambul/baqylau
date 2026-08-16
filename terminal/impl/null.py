"""The terminal that isn't there.

A concrete `TerminalPlugin` whose every operation returns its failure-shaped
response and whose one read returns nothing. Bootstrap wires it when `resolve()`
finds no terminal, so every service above stays unconditional — nothing has to
ask whether a terminal exists, and "no terminal" reads out of the audit as a
reason string like any other failure.
"""

from __future__ import annotations

from terminal.contract import (
    TerminalInput,
    TerminalMetadata,
    TerminalPanes,
    TerminalPlugin,
    TerminalTabs,
    TerminalViewport,
)
from terminal.models.input import KeySendResponse, TextSubmitResponse
from terminal.models.metadata import WindowTagResponse
from terminal.models.panes import (
    PaneCloseResponse,
    PaneOpenResponse,
    PaneResizeResponse,
    WindowFocusResponse,
)
from terminal.models.tabs import (
    TabCloseResponse,
    TabColorClearResponse,
    TabColorSetResponse,
    TabOpenResponse,
    TabRenameResponse,
)
from terminal.models.values import WindowInfo
from terminal.models.viewport import ScreenReadResponse

NO_TERMINAL = "no terminal available"


class NullTabs(TerminalTabs):
    def open_tab(self, request) -> TabOpenResponse:
        return TabOpenResponse(False, None, NO_TERMINAL)

    def close_tab(self, request) -> TabCloseResponse:
        return TabCloseResponse(False, NO_TERMINAL)

    def rename_tab(self, request) -> TabRenameResponse:
        return TabRenameResponse(False, NO_TERMINAL)

    def set_tab_color(self, request) -> TabColorSetResponse:
        return TabColorSetResponse(False, NO_TERMINAL)

    def clear_tab_color(self, request) -> TabColorClearResponse:
        return TabColorClearResponse(False, NO_TERMINAL)


class NullPanes(TerminalPanes):
    def open_pane(self, request) -> PaneOpenResponse:
        return PaneOpenResponse(False, None, NO_TERMINAL)

    def close_pane(self, request) -> PaneCloseResponse:
        return PaneCloseResponse(False, NO_TERMINAL)

    def resize_pane(self, request) -> PaneResizeResponse:
        return PaneResizeResponse(False, NO_TERMINAL)

    def focus_window(self, request) -> WindowFocusResponse:
        return WindowFocusResponse(False, NO_TERMINAL)


class NullMetadata(TerminalMetadata):
    def windows(self) -> tuple[WindowInfo, ...]:
        return ()

    def tag_window(self, request) -> WindowTagResponse:
        return WindowTagResponse(False, NO_TERMINAL)

    def current_window_id(self) -> str | None:
        return None


class NullInput(TerminalInput):
    def submit_text(self, request) -> TextSubmitResponse:
        return TextSubmitResponse(False, NO_TERMINAL)

    def send_key(self, request) -> KeySendResponse:
        return KeySendResponse(False, NO_TERMINAL)


class NullViewport(TerminalViewport):
    def read_screen(self, request) -> ScreenReadResponse:
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
