# Copyright (c) 2026 Zhambyl Yermagambet
"""Release only explicitly owned subprocess groups."""

import os
import signal

import psutil


def kill_process_group(process_id: int) -> None:
    """Release surviving descendants even when their direct parent has exited.

    Raises:
        ValueError: If the group is not an explicit positive process ID.
        PermissionError: If a live group member could not be signaled.

    """
    if process_id <= 0:
        message = "process cleanup requires an explicit positive group ID"
        raise ValueError(message)
    try:
        os.killpg(process_id, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        # XNU can report EPERM for a group with only zombie members.
        # Keep real permission failures unless no live group member remains.
        if _has_live_members(process_id):
            raise


def _has_live_members(process_id: int) -> bool:
    candidates = psutil.process_iter()
    return any(_is_live_member(candidate, process_id) for candidate in candidates)


def _is_live_member(candidate: psutil.Process, process_id: int) -> bool:
    try:
        return (
            os.getpgid(candidate.pid) == process_id
            and candidate.status() != psutil.STATUS_ZOMBIE
        )
    except (ProcessLookupError, psutil.NoSuchProcess):
        return False
