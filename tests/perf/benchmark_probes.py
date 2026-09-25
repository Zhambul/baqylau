# Copyright (c) 2026 Zhambyl Yermagambet
"""Send hooks to a private daemon and read its progress, delay, memory, and idle cost."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import psutil

from tests.extension_host import lifecycle_daemon_fixture as fixture

if TYPE_CHECKING:
    from sdk.client import BaqylauClient

DELAY_SAMPLES = 20
POLL_SECONDS = 0.01
DRAIN_SECONDS = 600.0
MILLISECONDS = 1000
MEGABYTE = 1_000_000


def processes(client: BaqylauClient) -> list[psutil.Process]:
    """List the daemon process and its worker children.

    Returns:
        The processes.

    """
    daemon = psutil.Process(client.application.health().process_id)
    return [daemon, *daemon.children(recursive=True)]


def post(client: BaqylauClient, hook: bytes) -> None:
    """Send one hook."""
    reply = client.transport.client.post(fixture.HOOK_PATH, content=hook, headers=fixture.JSON_HEADERS)
    reply.raise_for_status()


def drain(client: BaqylauClient, total: int) -> int:
    """Wait until every sent raw event has a verdict.

    Returns:
        The largest pending count seen.

    Raises:
        TimeoutError: If the host does not drain in time.

    """
    deadline = time.monotonic() + DRAIN_SECONDS
    highest = 0
    while time.monotonic() < deadline:
        checkpoint = client.diagnostics.checkpoint()
        highest = max(highest, checkpoint.pending_raw_event_count)
        if checkpoint.raw_event_cursor >= total and not checkpoint.pending_raw_event_count:
            return highest
        time.sleep(POLL_SECONDS)
    message = "the host did not drain the capture in time"
    raise TimeoutError(message)


def delays(client: BaqylauClient, first_cursor: int) -> list[float]:
    """Send single hooks and time each until its verdict.

    Returns:
        The delays in milliseconds, in order.

    """
    measured = []
    for index in range(DELAY_SAMPLES):
        started = time.monotonic()
        post(client, fixture.hook("Stop", f"delay-{index}").model_dump_json().encode())
        drain(client, first_cursor + index + 1)
        measured.append((time.monotonic() - started) * MILLISECONDS)
    return sorted(measured)


def memory_mb(client: BaqylauClient) -> float:
    """Add the resident memory of the daemon and its workers.

    Returns:
        The megabytes.

    """
    return sum(resident(proc) for proc in processes(client)) / MEGABYTE


def resident(proc: psutil.Process) -> int:
    """Read one process's resident memory.

    Returns:
        The bytes, or zero if the process ended.

    """
    try:
        return int(proc.memory_info().rss)
    except psutil.NoSuchProcess:
        return 0
