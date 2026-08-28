"""Read and change the Claude Code prompt composer."""

from __future__ import annotations

from domain.ids import WindowId
import time

from harness.contract import ComposerDriver, HarnessComposer
from harness.models import TerminalInputState
from harness.impl.claude_code import suggestion
from harness.impl.claude_code.controls import tui

CHANGE_TIMEOUT_SECONDS = 3.0
POLL_SECONDS = 0.1


class ComposerError(Exception):
    """A Claude composer action did not reach its checked state."""


class ClaudeCodeComposer(HarnessComposer):
    def read(
        self,
        composer_driver: ComposerDriver,
        window_id: WindowId,
    ) -> TerminalInputState | None:
        screen = composer_driver.get_text(window_id, ansi=True)
        if screen is None:
            screen = composer_driver.get_text(window_id)
        if screen is None or not suggestion.input_box_visible(screen):
            return None
        return TerminalInputState(
            typed_text=suggestion.typed(screen) or "",
            suggestion=suggestion.parse(screen),
        )

    def clear(self, composer_driver: ComposerDriver, window_id: WindowId) -> None:
        state = self.read(composer_driver, window_id)
        if state is None:
            raise ComposerError("the Claude composer is not readable")
        self._insert_mode(composer_driver, window_id)
        tui.clear_input(composer_driver, window_id, state.typed_text or "")
        self._wait_for(composer_driver, window_id, "")

    def insert(self, composer_driver: ComposerDriver, window_id: WindowId, text: str) -> None:
        if not text:
            return
        self._insert_mode(composer_driver, window_id)
        if not composer_driver.insert_text(window_id, text, paste=True):
            raise ComposerError("the Claude draft was not inserted")
        self._wait_for(composer_driver, window_id, suggestion.norm(text))

    def submit(self, composer_driver: ComposerDriver, window_id: WindowId, text: str) -> None:
        succeeded, _cleared_image = tui.type_command(composer_driver, window_id, text)
        if not succeeded:
            raise ComposerError("the Claude message was not delivered")

    @staticmethod
    def _insert_mode(composer_driver: ComposerDriver, window_id: WindowId) -> None:
        screen = composer_driver.get_text(window_id) or ""
        keys: tuple[str, ...]
        if "-- VISUAL --" in screen:
            keys = ("escape", "i")
        elif "-- NORMAL --" in screen:
            keys = ("i",)
        else:
            # Insert mode and the standard non-modal editor both accept the
            # clear and paste operations directly.
            return
        if not composer_driver.send_key(window_id, *keys):
            raise ComposerError("the Claude composer mode keys were not delivered")

    def _wait_for(
        self,
        composer_driver: ComposerDriver,
        window_id: WindowId,
        expected: str,
    ) -> None:
        deadline = time.monotonic() + CHANGE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = self.read(composer_driver, window_id)
            if state is not None and (state.typed_text or "") == expected:
                return
            time.sleep(POLL_SECONDS)
        state = self.read(composer_driver, window_id)
        observed = None if state is None else state.typed_text
        raise ComposerError(
            f"the Claude composer did not contain the expected draft; observed={observed!r}"
        )
