"""Pure ownership checks for one terminal metadata snapshot."""

from __future__ import annotations

import os

from terminal.models import WindowInfo


def window_hosts_process(
    window: WindowInfo,
    process_id: int | None,
    process_name: str,
) -> bool:
    """Return true when the window reports the specified foreground process."""
    if process_id is not None:
        return any(process.process_id == process_id for process in window.processes)
    return any(
        bool(process.command)
        and os.path.basename(process.command[0]) == process_name
        for process in window.processes
    )
