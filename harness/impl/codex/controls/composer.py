"""Control Codex's native prompt composer."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from domain.ids import WindowId

POLL_SECONDS = 0.1
CLEAR_TIMEOUT_SECONDS = 3.0
EMPTY_PROMPT = "Ask Codex to do anything"


class Driver(Protocol):
    def get_text(
        self,
        window_id: WindowId,
        extent: str = "screen",
        ansi: bool = False,
    ) -> str | None: ...

    def send_key(self, window_id: WindowId, *keys: str) -> bool: ...


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


def clear(
    driver: Driver,
    window_id: WindowId,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Clear the complete Codex draft and check the empty composer."""
    deadline = time.monotonic() + CLEAR_TIMEOUT_SECONDS
    screen = driver.get_text(window_id)
    while not empty(screen) and time.monotonic() < deadline:
        # Codex's kill shortcuts apply to one logical line. Clear both sides
        # of the cursor, then join the preceding line and repeat.
        if not driver.send_key(window_id, "ctrl+u", "ctrl+k"):
            raise ComposerError("the draft clear key was not delivered")
        sleep(POLL_SECONDS)
        screen = driver.get_text(window_id)
        if empty(screen):
            break
        if not driver.send_key(window_id, "backspace"):
            raise ComposerError("the draft join key was not delivered")
        sleep(POLL_SECONDS)
        screen = driver.get_text(window_id)
    if not empty(screen):
        observed = (screen or "")[-1200:]
        raise ComposerError(
            f"the Codex composer did not become empty; screen={observed!r}"
        )
