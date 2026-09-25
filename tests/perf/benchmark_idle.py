# Copyright (c) 2026 Zhambyl Yermagambet
"""Measure the CPU time and context switches of the daemon and its workers while no input arrives."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

from tests.perf.benchmark_probes import processes

if TYPE_CHECKING:
    from sdk.client import BaqylauClient

IDLE_SECONDS = 10.0


@dataclass(frozen=True)
class Usage:
    """Keep one process's CPU seconds and context switches."""

    cpu_seconds: float
    context_switches: int


def idle(client: BaqylauClient) -> Usage:
    """Measure the growth of CPU seconds and context switches while nothing arrives.

    A process that started or ended during the window has no pair; the long-lived ones do.

    Returns:
        The total growth.

    """
    before = usages(client)
    time.sleep(IDLE_SECONDS)
    after = usages(client)
    paired = before.keys() & after.keys()
    grown = [growth(before[pid], after[pid]) for pid in paired]
    cpu_seconds = sum(each.cpu_seconds for each in grown)
    return Usage(cpu_seconds, sum(each.context_switches for each in grown))


def growth(before: Usage, after: Usage) -> Usage:
    """Subtract one reading from a later one.

    Returns:
        The growth.

    """
    return Usage(after.cpu_seconds - before.cpu_seconds, after.context_switches - before.context_switches)


def usages(client: BaqylauClient) -> dict[int, Usage]:
    """Read the usage of each live process.

    Returns:
        The usage by process ID.

    """
    found = ((proc.pid, usage(proc)) for proc in processes(client))
    return {pid: counts for pid, counts in found if counts is not None}


def usage(proc: psutil.Process) -> Usage | None:
    """Read one process's usage.

    Returns:
        The usage, or None if the process ended.

    """
    try:
        times, counts = proc.cpu_times(), proc.num_ctx_switches()
    except psutil.NoSuchProcess:
        return None
    return Usage(times.user + times.system, counts.voluntary + counts.involuntary)
