"""Process liveness and ancestry used across the application."""

from __future__ import annotations

import os
import subprocess

PROCESS_QUERY_TIMEOUT_SECONDS = 2
ANCESTRY_WALK_LIMIT = 32


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
    for _ in range(ANCESTRY_WALK_LIMIT):
        if process_id <= 1:
            return None
        completed = subprocess.run(
            ["ps", "-o", "ppid=,comm=", "-p", str(process_id)],
            capture_output=True,
            text=True,
            timeout=PROCESS_QUERY_TIMEOUT_SECONDS,
        )
        fields = completed.stdout.strip().split(None, 1)
        if completed.returncode != 0 or len(fields) != 2:
            return None
        parent_id, command = fields
        if os.path.basename(command) == process_name:
            return process_id
        process_id = int(parent_id)
    return None


def process_alive(process_id: int, process_name: str) -> bool:
    """Alive AND still the named executable — pids get reused by the OS, which
    is the only reason the name is required."""
    completed = subprocess.run(
        ["ps", "-o", "comm=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        timeout=PROCESS_QUERY_TIMEOUT_SECONDS,
    )
    return (
        completed.returncode == 0
        and os.path.basename(completed.stdout.strip()) == process_name
    )


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
