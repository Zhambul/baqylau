# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and change the draft in the native terminal composer."""

import time

from domain.ids import WindowId
from harness.contract import ComposerDriver, HarnessComposer
from harness.impl.opencode2 import native_screen
from harness.models.probe import TerminalInputState

CONFIRM_SECONDS = 5
POLL_SECONDS = 0.1


class ComposerError(Exception):
    """Report a failed native draft operation."""


class OpenCodeComposer(HarnessComposer):
    """Keep terminal and dashboard drafts in one composer."""

    def read(self, composer_driver: ComposerDriver, window_id: WindowId) -> TerminalInputState | None:
        """Read the draft above the native model footer.

        Returns:
            The visible draft, or None when the composer is not visible.

        """
        screen = composer_driver.read_text(window_id)
        text = _draft(screen)
        return None if text is None else TerminalInputState(typed_text=text, suggestion=None)

    def clear(self, composer_driver: ComposerDriver, window_id: WindowId) -> None:
        """Clear a non-empty draft and confirm the empty composer.

        Raises:
            ComposerError: If the draft cannot be read or cleared.

        """
        state = self.read(composer_driver, window_id)
        if state is None:
            message = "OpenCode2 composer is not visible"
            raise ComposerError(message)
        if not state.typed_text:
            return
        if not composer_driver.send_key(window_id, "ctrl+c"):
            message = "OpenCode2 draft clear key failed"
            raise ComposerError(message)
        self._confirm(composer_driver, window_id, "")

    def insert(self, composer_driver: ComposerDriver, window_id: WindowId, text: str) -> None:
        """Insert a draft and confirm the native input.

        Raises:
            ComposerError: If the text cannot be inserted.

        """
        if not composer_driver.insert_text(window_id, text, paste=True):
            message = "OpenCode2 draft insertion failed"
            raise ComposerError(message)
        self._confirm(composer_driver, window_id, text, expand=True)

    def submit(self, composer_driver: ComposerDriver, window_id: WindowId, text: str) -> None:
        """Submit the text through the native composer.

        Raises:
            ComposerError: If the terminal rejects the text.

        """
        if not composer_driver.submit_text(window_id, text, paste=True):
            message = "OpenCode2 prompt submission failed"
            raise ComposerError(message)

    def _confirm(
        self, composer_driver: ComposerDriver, window_id: WindowId, expected: str, *, expand: bool = False,
    ) -> None:
        deadline = time.monotonic() + CONFIRM_SECONDS
        placeholder = f"[Pasted ~{len(expected.strip().splitlines())} lines]"
        while time.monotonic() < deadline:
            state = self.read(composer_driver, window_id)
            if state is not None and state.typed_text == expected:
                return
            if expand and state is not None and state.typed_text == placeholder:
                # The native paste handler expands a matching adjacent paste
                # when the same text is pasted again. It does not insert it twice.
                composer_driver.insert_text(window_id, expected, paste=True)
                expand = False
            time.sleep(POLL_SECONDS)
        message = "OpenCode2 draft did not match the requested text"
        raise ComposerError(message)


def _draft(screen: str | None) -> str | None:
    if screen is None:
        return None
    lines = screen.splitlines()
    footers = [index for index, line in enumerate(lines) if native_screen.is_composer_footer(line)]
    if not footers:
        return None
    draft: list[str] = []
    for line in reversed(lines[:footers[-1]]):
        if not line.lstrip().startswith("┃"):
            break
        draft.append(_line_text(line))
    return "\n".join(reversed(draft)).strip("\n")


def _line_text(line: str) -> str:
    after_border = line.partition("┃")[2]
    return after_border.removeprefix("  ").rstrip()
