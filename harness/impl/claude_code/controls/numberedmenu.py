"""Shared parsing and selection for Claude Code numbered menus."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from domain.ids import WindowId
from harness.impl.claude_code.controls.screen_driver import ScreenDriver

_ROW = re.compile(
    r"^\s*(?P<marks>(?:[❯↑↓]\s*)*)(?P<digit>\d+)\.\s+(?P<label>.+?)\s*$"
)
KEY_EFFECT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class Row:
    digit: str
    label: str
    cursor: bool


class SelectionError(RuntimeError):
    pass


def rows(text: str) -> tuple[Row, ...]:
    found: list[Row] = []
    for line in (text or "").splitlines():
        match = _ROW.match(line)
        if match is not None:
            found.append(Row(
                match.group("digit"),
                match.group("label").strip(),
                "❯" in match.group("marks"),
            ))
    return tuple(found)


def select(
    screen_driver: ScreenDriver,
    win: WindowId,
    read_rows: Callable[[], Sequence[Row]],
    digit: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    key_gap: float,
) -> None:
    """Move to one verified row and press Enter."""
    options = tuple(read_rows())
    target_index = next(
        (index for index, option in enumerate(options) if option.digit == digit),
        None,
    )
    current_index = next(
        (index for index, option in enumerate(options) if option.cursor),
        None,
    )
    if target_index is None or current_index is None:
        raise SelectionError("the cursor or target option is absent")
    key = "down" if target_index > current_index else "up"
    for _ in range(abs(target_index - current_index)):
        before = next((option.digit for option in read_rows() if option.cursor), None)
        if not screen_driver.send_key(win, key):
            raise SelectionError("the cursor key was not delivered")
        deadline = time.monotonic() + KEY_EFFECT_TIMEOUT_SECONDS
        while True:
            after = next((option.digit for option in read_rows() if option.cursor), None)
            if after is not None and after != before:
                break
            if time.monotonic() >= deadline:
                raise SelectionError("the cursor key had no visible effect")
            sleep(key_gap)
    selected = next((option for option in read_rows() if option.cursor), None)
    if selected is None or selected.digit != digit:
        raise SelectionError("the target option did not get the cursor")
    screen_driver.send_key(win, "enter")
