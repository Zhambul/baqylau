"""One terminal driver for all harness screen controls."""

from __future__ import annotations

from domain.ids import WindowId
from harness.contract import ComposerDriver
from terminal.contract import TerminalPlugin
from terminal.models import (
    KeySendRequest,
    PaneResizeRequest,
    ScreenReadRequest,
    SplitAxis,
    TextInputMode,
    TextInsertRequest,
    TextSubmitRequest,
)
from terminal.models.values import WindowId as NativeWindowId


class TerminalDriver(ComposerDriver):
    """Convert harness window values to terminal requests in one place."""

    def __init__(self, terminal_plugin: TerminalPlugin) -> None:
        self.terminal = terminal_plugin

    @staticmethod
    def _window(window_id: WindowId) -> NativeWindowId:
        return NativeWindowId(str(window_id))

    def get_text(
        self,
        window_id: WindowId,
        extent: str = "screen",
        ansi: bool = False,
    ) -> str | None:
        del extent
        response = self.terminal.viewport.read_screen(
            ScreenReadRequest(self._window(window_id), ansi=ansi)
        )
        return response.text if response.succeeded else None

    def send_key(self, window_id: WindowId, *keys: str) -> bool:
        native = self._window(window_id)
        return all(
            self.terminal.input.send_key(KeySendRequest(native, str(key))).succeeded
            for key in keys
        )

    def insert_text(self, window_id: WindowId, text: str, *, paste: bool = True) -> bool:
        mode = TextInputMode.PASTE if paste else TextInputMode.TYPE
        return self.terminal.input.insert_text(
            TextInsertRequest(self._window(window_id), str(text), mode)
        ).succeeded

    def submit_text(self, window_id: WindowId, text: str, *, paste: bool = True) -> bool:
        mode = TextInputMode.PASTE if paste else TextInputMode.TYPE
        return self.terminal.input.submit_text(
            TextSubmitRequest(self._window(window_id), str(text), mode)
        ).succeeded

    def send_text(self, window_id: WindowId, text: str) -> bool:
        """Submit text as typed keys for existing screen drivers."""
        return self.submit_text(window_id, text, paste=False)

    def paste_text(self, window_id: WindowId, text: str) -> bool:
        """Submit text as one paste for existing screen drivers."""
        return self.submit_text(window_id, text, paste=True)

    def lines(self, window_id: WindowId) -> int | None:
        native = self._window(window_id)
        return next(
            (
                window.lines
                for window in self.terminal.metadata.windows()
                if window.window_id == native
            ),
            None,
        )

    def resize_lines(self, window_id: WindowId, cells: int) -> bool:
        response = self.terminal.panes.resize_pane(
            PaneResizeRequest(
                self._window(window_id),
                SplitAxis.VERTICAL,
                cells,
            )
        )
        return response.succeeded
