# Copyright (c) 2026 Zhambyl Yermagambet
"""Process liveness and ancestry used across the application."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import psutil

ANCESTRY_WALK_LIMIT = 32


def _matches(process: psutil.Process, process_name: str) -> bool:
    """Match the reported name or the installed executable's exact path.

    Returns:
        True if a process name or resolved executable path matches.

    """
    if process.status() == psutil.STATUS_ZOMBIE:
        return False
    if process_name in _names(process):
        return True
    executable = shutil.which(process_name)
    return (
        executable is not None
        and Path(executable).resolve() == Path(process.exe()).resolve()
    )


def _names(process: psutil.Process) -> frozenset[str]:
    """Return the executable names that the operating system supplies.

    macOS can keep the invoked name in argv while the process record and the
    resolved executable use a different symlink target. `ps comm`, which this
    replaced, reports the invoked form. Accept the three exact basenames so a
    symlink does not make a live CLI look like a reused pid.

    Returns:
        Executable names that the operating system supplies.

    """
    command = process.cmdline()
    return frozenset(
        name
        for name in (
            process.name(),
            Path(process.exe()).name,
            _command_name(command),
        )
        if name
    )


def nearest_ancestor_named(process_name: str, from_process_id: int | None = None) -> int | None:
    """Return the nearest ancestor that has the specified process name.

    A hook process is spawned by the harness CLI, so walking that process's
    ancestry is how the CLI's pid is named without guessing. `from_process_id`
    is where to start: a hook CLIENT sends its own pid and the daemon walks from
    there, which keeps the walk (and its `ps` forks) out of a process the
    harness is waiting on — and the chain is alive while we read it, because the
    CLI is blocked on that delivery's response. Omitted, it walks our own.

    Returns:
        Nearest ancestor that has the specified process name.

    """
    process_id = os.getppid() if from_process_id is None else from_process_id
    try:
        return _find_named_ancestor(psutil.Process(process_id), process_name)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def process_alive(process_id: int, process_name: str) -> bool:
    """Test if an identifier still belongs to the specified live process.

    The operating system can reuse process identifiers. The process name
    prevents a false match after this reuse.

    Returns:
        True when the stated condition is met; otherwise, false.

    """
    try:
        return _matches(psutil.Process(process_id), process_name)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def process_is_alive(process_id: int) -> bool:
    """Test if the operating system reports a live process identifier.

    Returns:
        True when the stated condition is met; otherwise, false.

    """
    if process_id <= 0:
        return False
    try:
        return psutil.Process(process_id).status() != psutil.STATUS_ZOMBIE
    except psutil.AccessDenied:
        return True
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _find_named_ancestor(process: psutil.Process, process_name: str) -> int | None:
    """Walk the bounded process ancestry and return the first name match.

    Returns:
        Integer result.

    """
    current_process = process
    remaining_steps = ANCESTRY_WALK_LIMIT
    while remaining_steps:
        if current_process.pid <= 1:
            return None
        if _matches(current_process, process_name):
            return current_process.pid
        parent_process = current_process.parent()
        if parent_process is None:
            return None
        current_process = parent_process
        remaining_steps -= 1
    return None


def _command_name(command: list[str]) -> str:
    """Return the executable name from a process command.

    Returns:
        Executable name from a process command.

    """
    if not command:
        return ""
    return Path(command[0]).name
