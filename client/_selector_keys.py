# Copyright (c) 2026 Zhambyl Yermagambet
"""Read single key presses in raw mode and run the pane selector's choice loop."""

from __future__ import annotations

import os
import shutil
import sys
import termios
import tty
from contextlib import ExitStack
from types import MappingProxyType
from typing import TYPE_CHECKING

import _extension_selector as selector
import _render_wrap

if TYPE_CHECKING:
    from _extension_selector import AvailableView

KEY_BYTES = 4
OPEN = "open"
QUIT = "quit"
KEYS = MappingProxyType({
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[5~": "page_up",
    "\x1b[6~": "page_down",
    "k": "up",
    "j": "down",
    "\r": OPEN,
    "\n": OPEN,
    "q": QUIT,
    "\x1b": QUIT,
})


def choose(views: tuple[AvailableView, ...]) -> AvailableView | None:
    """Show the list until the user opens a view or quits; the terminal mode is always restored.

    Returns:
        The opened view, or None.

    """
    selected = 0
    with ExitStack() as restore:
        saved = termios.tcgetattr(sys.stdin)
        restore.callback(termios.tcsetattr, sys.stdin, termios.TCSADRAIN, saved)
        tty.setraw(sys.stdin)
        while True:
            _paint(views, selected)
            key = read_key()
            if key == QUIT or (key == OPEN and not views):
                return None
            if key == OPEN:
                return views[selected]
            selected = selector.moved(selected, key, len(views))


def read_key() -> str:
    """Read one key press.

    Returns:
        The named key, or "" for a key without a name.

    """
    pressed = os.read(sys.stdin.fileno(), KEY_BYTES).decode(errors="ignore")
    return KEYS.get(pressed, "")


def _paint(views: tuple[AvailableView, ...], selected: int) -> None:
    width = shutil.get_terminal_size().columns
    sys.stdout.write(_render_wrap.screen(selector.selector_rows(views, selected, width)))
    sys.stdout.flush()
