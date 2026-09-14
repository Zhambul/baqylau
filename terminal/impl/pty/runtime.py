# Copyright (c) 2026 Zhambyl Yermagambet
"""Control the PTY device and the process tree that uses it."""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import signal
import struct
import termios
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    import threading
    from collections.abc import Mapping

type ObservedProcess = psutil.Process
type RunningProcess = psutil.Popen


def resize(descriptor: int, columns: int, lines: int) -> None:
    """Set the PTY device size."""
    size = struct.pack("HHHH", lines, columns, 0, 0)
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, size)


def open_process(
    command: tuple[str, ...],
    working_directory: str,
    environment: Mapping[str, str],
    columns: int,
    lines: int,
) -> tuple[RunningProcess, int]:
    """Open a PTY and start a process in a new session.

    Returns:
        The process and its PTY descriptor.

    Raises:
        OSError: If the operating system refuses the process. The cause is kept
            because a launch that fails only under load — no descriptors, no
            processes — cannot be told apart from a missing program without it.

    """
    controller, program_side = pty.openpty()
    resize(program_side, columns, lines)
    try:
        process = psutil.Popen(
            command,
            cwd=working_directory or None,
            env=environment,
            stdin=program_side,
            stdout=program_side,
            stderr=program_side,
            start_new_session=True,
        )
    except OSError:
        os.close(controller)
        os.close(program_side)
        raise
    os.close(program_side)
    return process, controller


def observe_descendants(
    root_pid: int,
    identities: dict[int, float],
    lock: threading.Condition,
) -> tuple[ObservedProcess, ...]:
    """Store descendants while they still have a link to the root process.

    Returns:
        The descendants that the process has now.

    """
    try:
        found = tuple(psutil.Process(root_pid).children(recursive=True))
    except (psutil.Error, OSError, SystemError):
        return ()
    found_identities: dict[int, float] = {}
    for process in found:
        with contextlib.suppress(psutil.Error, OSError, SystemError):
            found_identities[process.pid] = process.create_time()
    with lock:
        identities.update(found_identities)
    return found


def owned_descendants(
    root_pid: int,
    identities: dict[int, float],
    lock: threading.Condition,
) -> tuple[ObservedProcess, ...]:
    """Return stored descendants that are still live.

    Returns:
        The live descendants.

    """
    observed = {child.pid: child for child in observe_descendants(root_pid, identities, lock)}
    with lock:
        stored_identities = tuple(identities.items())
    for pid, created_at in stored_identities:
        if pid not in observed:
            candidate = _matching_process(pid, created_at)
            if candidate is not None:
                observed[pid] = candidate
    return tuple(observed.values())


def close_process_tree(
    process: psutil.Popen,
    descriptor: int,
    descendants: list[ObservedProcess],
    process_timeout: float,
    descendant_timeout: float,
) -> None:
    """Stop the root process, its group, and all stored descendants."""
    _stop_root_process(process, process_timeout)
    for child in reversed(descendants):
        with contextlib.suppress(psutil.Error, OSError, SystemError):
            if child.is_running():
                child.terminate()
    _gone, alive = psutil.wait_procs(descendants, timeout=descendant_timeout)
    for child in alive:
        with contextlib.suppress(psutil.Error, OSError, SystemError):
            child.kill()
    if alive:
        psutil.wait_procs(alive, timeout=descendant_timeout)
    with contextlib.suppress(OSError):
        os.close(descriptor)


def _matching_process(pid: int, created_at: float) -> ObservedProcess | None:
    try:
        candidate = psutil.Process(pid)
    except (psutil.Error, OSError, SystemError):
        return None
    try:
        has_matching_identity = candidate.create_time() == created_at
    except (psutil.Error, OSError, SystemError):
        return None
    return candidate if has_matching_identity else None


def _stop_root_process(
    process: psutil.Popen,
    timeout: float,
) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except OSError:
        process.kill()
    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        process.kill()
