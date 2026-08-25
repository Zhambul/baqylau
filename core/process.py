"""Process liveness and ancestry used across the application."""

from __future__ import annotations

import os

import psutil

ANCESTRY_WALK_LIMIT = 32


def _names(process: psutil.Process) -> frozenset[str]:
    """The executable names the OS exposes for one process.

    macOS can keep the invoked name in argv while the process record and the
    resolved executable use a different symlink target. `ps comm`, which this
    replaced, reports the invoked form. Accept the three exact basenames so a
    symlink does not make a live CLI look like a reused pid.
    """
    command = process.cmdline()
    return frozenset(
        name
        for name in (
            process.name(),
            os.path.basename(process.exe()),
            os.path.basename(command[0]) if command else "",
        )
        if name
    )


def nearest_ancestor_named(process_name: str, from_process_id: int | None = None) -> int | None:
    """The pid of the nearest ancestor process whose name matches, or None.

    A hook process is spawned by the harness CLI, so walking that process's
    ancestry is how the CLI's pid is named without guessing. `from_process_id`
    is where to start: a hook CLIENT sends its own pid and the daemon walks from
    there, which keeps the walk (and its `ps` forks) out of a process the
    harness is waiting on — and the chain is alive while we read it, because the
    CLI is blocked on that delivery's response. Omitted, it walks our own.
    """
    process_id = os.getppid() if from_process_id is None else from_process_id
    try:
        process = psutil.Process(process_id)
        for _ in range(ANCESTRY_WALK_LIMIT):
            if process.pid <= 1:
                return None
            if process_name in _names(process):
                return process.pid
            parent = process.parent()
            if parent is None:
                return None
            process = parent
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None
    return None


def process_alive(process_id: int, process_name: str) -> bool:
    """Alive AND still the named executable — pids get reused by the OS, which
    is the only reason the name is required."""
    try:
        return process_name in _names(psutil.Process(process_id))
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    try:
        os.kill(process_id, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
