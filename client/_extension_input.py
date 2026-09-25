# Copyright (c) 2026 Zhambyl Yermagambet
"""Turn pane keys into focus moves, page scrolls, and actions."""

from __future__ import annotations

import select
import shutil
import sys
import time
from typing import TYPE_CHECKING

import _selector_keys
from _extension_actions import send_action
from _extension_focus import moved

if TYPE_CHECKING:
    from types import FrameType

    from _extension_pane import ExtensionPaneTarget, PaneState

WAIT_STEP_SECONDS = 0.1
QUIT = _selector_keys.QUIT
NO_ACTION = "The focused item has no action."


class Repaint:
    """Record a request to repaint after a size change or new data.

    A signal does not end `select` or `time.sleep`, and the change follower runs on
    another thread, so the wait reads this flag in short steps.
    """

    def __init__(self) -> None:
        """Start with no request."""
        self.requested = False

    def mark(self, _signal_number: int, _frame: FrameType | None) -> None:
        """Record one size change."""
        self.requested = True

    def request(self) -> None:
        """Record new data."""
        self.requested = True


def wait_for_input(seconds: float, repaint: Repaint) -> str:
    """Wait for a key, a repaint request, or the refresh time, whichever comes first.

    Returns:
        The named key, or "" after a repaint request or the refresh time.

    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not repaint.requested:
        ready, _, _ = select.select([sys.stdin], [], [], WAIT_STEP_SECONDS)
        if ready:
            return _selector_keys.read_key()
    repaint.requested = False
    return ""


def apply_key(key: str, state: PaneState, target: ExtensionPaneTarget) -> None:
    """Move the focus, scroll one page, or send the focused item's action."""
    size = shutil.get_terminal_size()
    page = max(1, size.lines - 2)
    if key in {"up", "down"}:
        state.focus = moved(state.focusable, state.focus, key)
    elif key == "page_up":
        state.offset = max(0, state.offset - page)
    elif key == "page_down":
        state.offset += page
    elif key == _selector_keys.OPEN:
        state.status = _action_status(state, target, (size.columns, size.lines))


def _action_status(state: PaneState, target: ExtensionPaneTarget, size: tuple[int, int]) -> str:
    if state.focus is None or state.focus.action_id is None:
        return NO_ACTION
    return send_action(target, state.focus, size)
