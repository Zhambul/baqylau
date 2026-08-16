"""Shared mechanics for Claude Code's screen-verified dialog drivers."""

from __future__ import annotations

import time
from collections.abc import Callable

POLL_SECONDS = 0.15
SCREEN_LIMIT = 2000


def poll_until(
    terminal,
    window_id: str,
    predicate: Callable[[str], bool],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    poll: float = POLL_SECONDS,
) -> tuple[str, bool]:
    deadline = time.monotonic() + timeout
    screen = terminal.get_text(window_id) or ""
    while not predicate(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(poll)
        screen = terminal.get_text(window_id) or ""
    return screen, True


class StepError(Exception):
    def __init__(self, step: str, detail: str = "", screen: str | None = None) -> None:
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.screen = screen
