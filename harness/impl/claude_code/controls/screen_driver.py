"""Shared mechanics for Claude Code's screen-verified dialog drivers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from terminal.contract import TerminalPlugin

POLL_SECONDS = 0.15
SCREEN_LIMIT = 2000


class ScreenDriver(Protocol):
    """The small driver vocabulary the dialog drivers press keys through."""

    terminal: TerminalPlugin

    def get_text(
        self, window_id: str, extent: str = "screen", ansi: bool = False
    ) -> str | None: ...
    def send_key(self, window_id: str, *keys: str) -> bool: ...
    def send_text(self, window_id: str, text: str) -> bool: ...
    def paste_text(self, window_id: str, text: str) -> bool: ...


def poll_until(
    terminal: ScreenDriver,
    window_id: str,
    predicate: Callable[[str], object],
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
