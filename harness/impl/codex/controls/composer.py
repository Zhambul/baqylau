"""Control Codex's native prompt composer."""

from __future__ import annotations

import time
from collections.abc import Callable

from domain.ids import WindowId
from harness.contract import ComposerDriver, ComposerDriver as Driver, HarnessComposer
from harness.models import TerminalInputState

POLL_SECONDS = 0.1
CLEAR_TIMEOUT_SECONDS = 3.0
EMPTY_PROMPT = "Ask Codex to do anything"


class ComposerError(Exception):
    """A prompt composer action did not reach its checked state."""


def empty(screen: str | None) -> bool:
    """True when the last visible composer shows its empty prompt."""
    lines = (screen or "").splitlines()
    marker = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if lines[index].lstrip().startswith(("›", "❯"))
        ),
        None,
    )
    if marker is None:
        return False
    return EMPTY_PROMPT in " ".join(lines[marker].split())


def typed(screen: str | None) -> str | None:
    """Return the text in the last visible Codex composer."""
    lines = (screen or "").splitlines()
    marker = next(
        (
            index
            for index in range(len(lines) - 1, -1, -1)
            if lines[index].lstrip().startswith(("›", "❯"))
        ),
        None,
    )
    if marker is None:
        return None
    first = lines[marker].lstrip()[1:].strip()
    if EMPTY_PROMPT in first:
        return ""
    body = [first]
    for line in lines[marker + 1 :]:
        if not line.strip():
            break
        body.append(line.strip())
    return "\n".join(body).strip()


def clear(
    composer_driver: Driver,
    window_id: WindowId,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Clear the complete Codex draft and check the empty composer."""
    deadline = time.monotonic() + CLEAR_TIMEOUT_SECONDS
    screen = composer_driver.get_text(window_id)
    while not empty(screen) and time.monotonic() < deadline:
        # Codex's kill shortcuts apply to one logical line. Clear both sides
        # of the cursor, then join the preceding line and repeat.
        if not composer_driver.send_key(window_id, "ctrl+u", "ctrl+k"):
            raise ComposerError("the draft clear key was not delivered")
        sleep(POLL_SECONDS)
        screen = composer_driver.get_text(window_id)
        if empty(screen):
            break
        if not composer_driver.send_key(window_id, "backspace"):
            raise ComposerError("the draft join key was not delivered")
        sleep(POLL_SECONDS)
        screen = composer_driver.get_text(window_id)
    if not empty(screen):
        observed = (screen or "")[-1200:]
        raise ComposerError(
            f"the Codex composer did not become empty; screen={observed!r}"
        )


class CodexComposer(HarnessComposer):
    def read(
        self,
        composer_driver: ComposerDriver,
        window_id: WindowId,
    ) -> TerminalInputState | None:
        screen = composer_driver.get_text(window_id)
        text = typed(screen)
        if text is None:
            return None
        return TerminalInputState(typed_text=text, suggestion=None)

    def clear(self, composer_driver: ComposerDriver, window_id: WindowId) -> None:
        clear(composer_driver, window_id)

    def insert(self, composer_driver: ComposerDriver, window_id: WindowId, text: str) -> None:
        if not text:
            return
        if not composer_driver.insert_text(window_id, text, paste=True):
            raise ComposerError("the Codex draft was not inserted")
        self._wait_for(composer_driver, window_id, text)

    def submit(self, composer_driver: ComposerDriver, window_id: WindowId, text: str) -> None:
        if not composer_driver.submit_text(window_id, text, paste=True):
            raise ComposerError("the Codex message was not delivered")

    def _wait_for(
        self,
        composer_driver: ComposerDriver,
        window_id: WindowId,
        expected: str,
    ) -> None:
        deadline = time.monotonic() + CLEAR_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = self.read(composer_driver, window_id)
            if state is not None and state.typed_text == expected:
                return
            time.sleep(POLL_SECONDS)
        state = self.read(composer_driver, window_id)
        observed = None if state is None else state.typed_text
        raise ComposerError(
            f"the Codex composer did not contain the expected draft; observed={observed!r}"
        )
