#!/usr/bin/env python3
# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint one extension terminal view in a pane and act on its focused item.

Up and down (or `k` and `j`) move the focus, PgUp and PgDn scroll, Enter runs
the focused item's action, and `q` closes the pane. The pane repaints after each
record change of its extension and scope, after a resize, and on a slow timer
that shows settings and runtime changes. The pane also ends when the view is no
longer active.
"""

from __future__ import annotations

import signal
import sys
import termios
import threading
import time
import tty
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _extension_changes import follow
from _extension_input import QUIT, Repaint, apply_key, wait_for_input
from _extension_pane import ExtensionPaneTarget, PaneState, paint_once

ARGUMENT_COUNT = 5
REFRESH_SECONDS = 10.0
CLOSE_DELAY_SECONDS = 3.0


def main(arguments: list[str]) -> None:
    """Run the command."""
    target = _target(arguments)
    repaint = Repaint()
    signal.signal(signal.SIGWINCH, repaint.mark)
    closed = _follow_changes(target, repaint)
    with ExitStack() as restore:
        restore.callback(closed.set)
        if sys.stdin.isatty():
            saved = termios.tcgetattr(sys.stdin)
            restore.callback(termios.tcsetattr, sys.stdin, termios.TCSADRAIN, saved)
            tty.setcbreak(sys.stdin)
        _run(target, repaint)
    time.sleep(CLOSE_DELAY_SECONDS)


def _run(target: ExtensionPaneTarget, repaint: Repaint) -> None:
    state = PaneState()
    while paint_once(target, state):
        key = wait_for_input(REFRESH_SECONDS, repaint)
        if key == QUIT:
            return
        apply_key(key, state, target)


def _follow_changes(target: ExtensionPaneTarget, repaint: Repaint) -> threading.Event:
    closed = threading.Event()
    follower = threading.Thread(target=follow, args=(target, repaint, closed), daemon=True)
    follower.start()
    return closed


def _target(arguments: list[str]) -> ExtensionPaneTarget:
    if len(arguments) != ARGUMENT_COUNT:
        sys.exit("usage: terminal_extension_pane.py HOST PORT EXTENSION_ID VIEW_ID SCOPE")
    host, port_text, *view = arguments
    return ExtensionPaneTarget(host, int(port_text), *view)


if __name__ == "__main__":
    main(sys.argv[1:])
