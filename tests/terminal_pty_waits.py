# Copyright (c) 2026 Zhambyl Yermagambet
"""Wait for observable PTY process conditions."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import psutil

from tests.terminal_pty_fixture import POLL_SECONDS, TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from terminal.contract import TerminalPlugin


def wait_for_process_id(process_id_path: Path) -> int:
    """Wait for a process identifier file.

    Returns:
        The identifier read from the file.

    """
    wait_until(process_id_path.exists)
    return int(process_id_path.read_text(encoding="utf-8"))


def window_has_process(plugin: TerminalPlugin, process_id: int) -> bool:
    """Return whether the current PTY window reports a process id.

    Returns:
        Whether the current PTY window reports a process id.

    """
    return process_id in reported_process_ids(plugin)


def reported_process_ids(plugin: TerminalPlugin) -> frozenset[int | None]:
    """Return process ids reported by the first PTY window.

    Returns:
        Process ids reported by the first PTY window.

    """
    return frozenset(process.process_id for process in plugin.metadata.windows()[0].processes)


def wait_for_no_windows(plugin: TerminalPlugin) -> None:
    """Wait until the plugin has no live windows."""
    wait_until(lambda: not plugin.metadata.windows())
    assert not plugin.metadata.windows()


def wait_for_process_exit(process_id: int) -> None:
    """Wait until a process id no longer exists."""
    wait_until(lambda: not psutil.pid_exists(process_id))
    assert not psutil.pid_exists(process_id), "orphaned tool outlived its PTY window"


def wait_until(condition: Callable[[], bool], timeout: float = TIMEOUT_SECONDS) -> None:
    """Wait for a condition until the PTY test timeout.

    A caller with slow work uses a larger explicit timeout.
    """
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
    assert condition()
