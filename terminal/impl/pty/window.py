# Copyright (c) 2026 Zhambyl Yermagambet
"""One pseudo-terminal window: a process, its tty, and the screen it painted.

A window here is a pty this process owns rather than something a terminal
application shows, which is what makes this terminal headless. The program on
the other side cannot tell the difference: it has a tty, so it runs its full
TUI, negotiates its keyboard mode, and repaints exactly as it would anywhere.

The output is drained by a thread rather than on demand. A TUI that fills the
pty buffer with nobody reading BLOCKS, and a blocked program looks exactly like
a broken one. Every byte drained is fed to a terminal emulator, because the pty
gives paint operations — "move to row 3, erase to end, write these cells" — and
a screen is what those operations ADD UP TO. Scraping the escapes out of the
stream instead answers a different question: everything that was ever painted,
including what has since been overwritten.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from typing import TYPE_CHECKING

import pyte

from terminal.impl.pty import runtime
from terminal.impl.pty.query import TerminalQueryResponder
from terminal.models.values import WindowId

if TYPE_CHECKING:
    from collections.abc import Mapping


COLUMNS = 200
LINES = 40
CLOSE_TIMEOUT_SECONDS = 10.0
DESCENDANT_CLOSE_TIMEOUT_SECONDS = 2.0
READ_SIZE = 65536


@dataclasses.dataclass
class PtyWindow:
    """A running program, everything it has painted, and how to type at it."""

    window_id: WindowId
    process: runtime.RunningProcess
    descriptor: int
    screen: pyte.Screen
    stream: pyte.ByteStream
    command: tuple[str, ...]
    query_responder: TerminalQueryResponder = dataclasses.field(
        default_factory=TerminalQueryResponder,
    )
    tags: dict[str, str] = dataclasses.field(default_factory=dict)
    descendant_identities: dict[int, float] = dataclasses.field(default_factory=dict)
    # The emulator is fed from the drain thread and read from the caller's, and
    # pyte keeps a mutable grid: a read mid-feed would see half a repaint.
    lock: threading.Condition = dataclasses.field(default_factory=threading.Condition)
    revision: int = 0

    def display(self) -> str:
        """Return the display.

        Returns:
            Display.

        """
        with self.lock:
            rows = list(self.screen.display)
        return "\n".join(row.rstrip() for row in rows).rstrip("\n")

    def write(self, payload: bytes) -> bool:
        """Write write.

        Returns:
            True when the stated condition is met; otherwise, false.

        """
        try:
            os.write(self.descriptor, payload)
        except OSError:
            return False
        else:
            return True

    def wait_for_screen_change(self, after: int, timeout: float) -> bool:
        """Wait until the child has processed input and painted a response.

        Returns:
            True when the stated condition is met; otherwise, false.

        """
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.revision <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.lock.wait(remaining)
            return True

    def resize(self, columns: int, lines: int) -> bool:
        """Return the resize.

        Returns:
            Resize.

        """
        try:
            runtime.resize(self.descriptor, columns, lines)
        except OSError:
            return False
        with self.lock:
            self.screen.resize(lines, columns)
        return True

    def observe_descendants(self) -> tuple[runtime.ObservedProcess, ...]:
        """Remember descendants while ancestry still connects them to the window.

        Returns:
            Result items.

        """
        return runtime.observe_descendants(
            self.process.pid,
            self.descendant_identities,
            self.lock,
        )

    def owned_descendants(self) -> tuple[runtime.ObservedProcess, ...]:
        """Live descendants previously observed, even after they are reparented.

        Returns:
            Result items.

        """
        return runtime.owned_descendants(
            self.process.pid,
            self.descendant_identities,
            self.lock,
        )

    def close(self) -> bool:
        """Close the wrapper and descendants, including escaped tool groups.

        The login shell and CLI share our process group, but a harness can launch a
        tool in a new session of its own. Snapshot the tree before signalling
        the root group, then explicitly reap any descendants that survived it.

        Returns:
            True when the stated condition is met; otherwise, false.

        """
        descendants = list(self.owned_descendants())
        runtime.close_process_tree(
            self.process,
            self.descriptor,
            descendants,
            CLOSE_TIMEOUT_SECONDS,
            DESCENDANT_CLOSE_TIMEOUT_SECONDS,
        )
        return True


def open_window(
    window_id: WindowId,
    command: tuple[str, ...],
    working_directory: str,
    environment: Mapping[str, str],
) -> PtyWindow:
    """Start `command` on a new pty.

    Returns:
        The pty window.

    """
    screen = pyte.Screen(COLUMNS, LINES)
    process, controller = runtime.open_process(
        command,
        working_directory,
        environment,
        COLUMNS,
        LINES,
    )
    window = PtyWindow(
        window_id=window_id,
        process=process,
        descriptor=controller,
        screen=screen,
        stream=pyte.ByteStream(screen),
        command=command,
    )
    threading.Thread(target=_drain, args=(window,), daemon=True).start()
    return window


def _drain(pty_window: PtyWindow) -> None:
    while True:
        try:
            chunk = os.read(pty_window.descriptor, READ_SIZE)
        except OSError:  # the pty closed with the process
            return
        if not chunk:
            return
        with pty_window.lock:
            pty_window.stream.feed(chunk)
            replies = pty_window.query_responder.feed(
                chunk,
                pty_window.screen.cursor.y + 1,
                pty_window.screen.cursor.x + 1,
            )
            if replies:
                pty_window.write(replies)
            pty_window.revision += 1
            pty_window.lock.notify_all()
