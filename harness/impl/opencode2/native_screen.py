# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the owned terminal while a native dialog opens and closes."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from terminal.models.values import WindowId
from terminal.models.viewport import ScreenReadRequest

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness.models import controls

READ_INTERVAL_SECONDS = 0.1


def is_composer_footer(line: str) -> bool:
    """Check the native composer footer.

    Returns:
        True for a Build or Plan composer footer.

    """
    return line.lstrip().startswith(("┃  Build ·", "┃  Plan ·"))


def text(control_context: controls.ControlContext) -> str:
    """Read what the owned terminal shows now.

    Returns:
        The screen text, or an empty string when there is no terminal.

    """
    window_id = control_context.terminal_window_id
    if window_id is None:
        return ""
    screen = control_context.terminal.viewport.read_screen(ScreenReadRequest(WindowId(str(window_id))))
    return screen.text or ""


def wait_for(
    control_context: controls.ControlContext,
    matches: Callable[[str], bool],
    seconds: float,
) -> bool:
    """Wait until the owned terminal shows what a control needs.

    Returns:
        True when the screen matches inside the time limit.

    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if matches(text(control_context)):
            return True
        time.sleep(READ_INTERVAL_SECONDS)
    return False
