"""Shared mechanics for Claude Code's screen-verified dialog drivers."""

from __future__ import annotations

import time
from collections.abc import Callable

from domain.ids import WindowId
from harness.contract import ComposerDriver

ScreenDriver = ComposerDriver

POLL_SECONDS = 0.15
SCREEN_LIMIT = 2000


def poll_until(
    composer_driver: ComposerDriver,
    window_id: WindowId,
    predicate: Callable[[str], object],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    poll: float = POLL_SECONDS,
) -> tuple[str, bool]:
    deadline = time.monotonic() + timeout
    screen = composer_driver.get_text(window_id) or ""
    while not predicate(screen):
        if time.monotonic() >= deadline:
            return screen, False
        sleep(poll)
        screen = composer_driver.get_text(window_id) or ""
    return screen, True


class StepError(Exception):
    def __init__(self, step: str, detail: str = "", screen: str | None = None) -> None:
        super().__init__(step + ((": " + detail) if detail else ""))
        self.step = step
        self.screen = screen


def failure_detail(step_error: StepError) -> str:
    """Keep a bounded native screen with a verified driver failure."""
    if not step_error.screen:
        return str(step_error)
    return f"{step_error}; screen={step_error.screen[-SCREEN_LIMIT:]!r}"
