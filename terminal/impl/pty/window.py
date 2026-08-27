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
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import psutil
import pyte

from terminal.models.values import WindowId

COLUMNS = 200
LINES = 40
CLOSE_TIMEOUT_SECONDS = 10.0
DESCENDANT_CLOSE_TIMEOUT_SECONDS = 2.0
READ_SIZE = 65536

_QUERY_REPLIES = (
    (b"\x1b[c", b"\x1b[?1;2c"),
    (b"\x1b[?u", b"\x1b[?0u"),
    (b"\x1b]10;?\x07", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
    (b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
    (b"\x1b]11;?\x07", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
)
_CURSOR_POSITION_QUERY = b"\x1b[6n"


@dataclass
class _TerminalQueryResponder:
    """Reply to terminal queries that require input from the emulator."""

    pending: bytes = b""

    def feed(self, chunk: bytes, row: int, column: int) -> bytes:
        data = self.pending + chunk
        found: list[tuple[int, bytes]] = []
        for query, reply in _QUERY_REPLIES:
            position = data.find(query)
            while position >= 0:
                found.append((position, reply))
                position = data.find(query, position + len(query))
        position = data.find(_CURSOR_POSITION_QUERY)
        while position >= 0:
            found.append((position, f"\x1b[{row};{column}R".encode()))
            position = data.find(
                _CURSOR_POSITION_QUERY,
                position + len(_CURSOR_POSITION_QUERY),
            )

        queries = (*(query for query, _reply in _QUERY_REPLIES), _CURSOR_POSITION_QUERY)
        longest_prefix = max(len(query) for query in queries) - 1
        tail = data[-longest_prefix:]
        self.pending = b""
        for length in range(len(tail), 0, -1):
            candidate = tail[-length:]
            if any(query.startswith(candidate) for query in queries):
                self.pending = candidate
                break
        return b"".join(reply for _position, reply in sorted(found))


@dataclass
class PtyWindow:
    """A running program, everything it has painted, and how to type at it."""

    window_id: WindowId
    process: subprocess.Popen[bytes]
    descriptor: int
    screen: pyte.Screen
    stream: pyte.ByteStream
    command: tuple[str, ...]
    query_responder: _TerminalQueryResponder = field(default_factory=_TerminalQueryResponder)
    tags: dict[str, str] = field(default_factory=dict)
    descendant_identities: dict[int, float] = field(default_factory=dict)
    # The emulator is fed from the drain thread and read from the caller's, and
    # pyte keeps a mutable grid: a read mid-feed would see half a repaint.
    lock: threading.Condition = field(default_factory=threading.Condition)
    revision: int = 0

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

    def wait_for_screen_change(self, after: int, timeout: float) -> bool:
        """Wait until the child has processed input and painted a response."""
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.revision <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.lock.wait(remaining)
            return True

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

    def observe_descendants(self) -> tuple[psutil.Process, ...]:
        """Remember descendants while ancestry still connects them to the window."""
        try:
            found = tuple(psutil.Process(self.process.pid).children(recursive=True))
        except (psutil.Error, OSError, SystemError):
            return ()
        identities: dict[int, float] = {}
        for child in found:
            try:
                identities[child.pid] = child.create_time()
            except (psutil.Error, OSError, SystemError):
                continue
        with self.lock:
            self.descendant_identities.update(identities)
        return found

    def owned_descendants(self) -> tuple[psutil.Process, ...]:
        """Live descendants previously observed, even after they are reparented."""
        observed = {child.pid: child for child in self.observe_descendants()}
        with self.lock:
            identities = tuple(self.descendant_identities.items())
        for pid, created_at in identities:
            if pid in observed:
                continue
            try:
                candidate = psutil.Process(pid)
                if candidate.create_time() == created_at:
                    observed[pid] = candidate
            except (psutil.Error, OSError, SystemError):
                continue
        return tuple(observed.values())

    def close(self) -> bool:
        """Close the wrapper and descendants, including escaped tool groups.

        The login shell and CLI share our process group, but a harness can launch a
        tool in a new session of its own. Snapshot the tree before signalling
        the root group, then explicitly reap any descendants that survived it.
        """
        descendants = list(self.owned_descendants())
        if self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except OSError:
                self.process.kill()
            try:
                self.process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self.process.kill()
        for child in reversed(descendants):
            try:
                if child.is_running():
                    child.terminate()
            except (psutil.Error, OSError, SystemError):
                pass
        _gone, alive = psutil.wait_procs(
            descendants,
            timeout=DESCENDANT_CLOSE_TIMEOUT_SECONDS,
        )
        for child in alive:
            try:
                child.kill()
            except (psutil.Error, OSError, SystemError):
                pass
        if alive:
            psutil.wait_procs(alive, timeout=DESCENDANT_CLOSE_TIMEOUT_SECONDS)
        try:
            os.close(self.descriptor)
        except OSError:
            pass
        return True


def open_window(
    window_id: WindowId,
    command: tuple[str, ...],
    working_directory: str,
    environment: Mapping[str, str],
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
