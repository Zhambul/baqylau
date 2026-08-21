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

import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import threading
from dataclasses import dataclass, field

import pyte

from terminal.models.values import WindowId

COLUMNS = 200
LINES = 50
CLOSE_TIMEOUT_SECONDS = 10.0
READ_SIZE = 65536


@dataclass
class PtyWindow:
    """A running program, everything it has painted, and how to type at it."""

    window_id: WindowId
    process: subprocess.Popen[bytes]
    descriptor: int
    screen: pyte.Screen
    stream: pyte.ByteStream
    tags: dict[str, str] = field(default_factory=dict)
    # The emulator is fed from the drain thread and read from the caller's, and
    # pyte keeps a mutable grid: a read mid-feed would see half a repaint.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def display(self) -> str:
        with self.lock:
            rows = list(self.screen.display)
        return "\n".join(row.rstrip() for row in rows).rstrip("\n")

    def write(self, payload: bytes) -> bool:
        try:
            os.write(self.descriptor, payload)
            return True
        except OSError:
            return False

    def resize(self, columns: int, lines: int) -> bool:
        try:
            fcntl.ioctl(
                self.descriptor,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", lines, columns, 0, 0),
            )
        except OSError:
            return False
        with self.lock:
            self.screen.resize(lines, columns)
        return True

    def close(self) -> bool:
        """Signal the whole process group: a login shell wrapping a CLI is two
        processes, and killing only the shell orphans the program."""
        if self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except OSError:
                self.process.kill()
            try:
                self.process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
        try:
            os.close(self.descriptor)
        except OSError:
            pass
        return True


def open_window(
    window_id: WindowId,
    command: tuple[str, ...],
    working_directory: str,
    environment: dict[str, str],
) -> PtyWindow | None:
    """Start `command` on a new pty, or None when it cannot be started."""
    screen = pyte.Screen(COLUMNS, LINES)
    controller, program_side = pty.openpty()
    fcntl.ioctl(program_side, termios.TIOCSWINSZ, struct.pack("HHHH", LINES, COLUMNS, 0, 0))
    try:
        process = subprocess.Popen(
            command,
            cwd=working_directory or None,
            env=environment,
            stdin=program_side,
            stdout=program_side,
            stderr=program_side,
            # Its own session, so the group signal on close reaches the whole
            # tree and so the program owns this tty rather than sharing ours.
            start_new_session=True,
        )
    except OSError:
        os.close(controller)
        os.close(program_side)
        return None
    os.close(program_side)
    window = PtyWindow(
        window_id=window_id,
        process=process,
        descriptor=controller,
        screen=screen,
        stream=pyte.ByteStream(screen),
    )
    threading.Thread(target=_drain, args=(window,), daemon=True).start()
    return window


def _drain(pty_window: PtyWindow) -> None:
    while True:
        try:
            chunk = os.read(pty_window.descriptor, READ_SIZE)
        except OSError:                      # the pty closed with the process
            return
        if not chunk:
            return
        with pty_window.lock:
            pty_window.stream.feed(chunk)
