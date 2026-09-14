# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the worker that one scenario names."""

from __future__ import annotations

from tests.e2e.testkit.references import WorkerKind

# The words a scenario uses for one subagent. A subagent is left to run unless
# the scenario asks the lead to wait for it. Every harness offers both, but by
# a different gesture, so the wanted behaviour belongs in the scenario and the
# native tool argument belongs in the delegation adapter.
FOREGROUND_SUBAGENT = "foreground subagent"
SUBAGENT_WORKERS = frozenset((
    "named subagent",
    "background subagent",
    FOREGROUND_SUBAGENT,
))


def worker(worker_name: str) -> tuple[WorkerKind, bool]:
    """Read the worker kind and whether the lead leaves that worker to run.

    Returns:
        The worker kind and its background setting.

    Raises:
        AssertionError: If the scenario names an unknown worker type.

    """
    if worker_name in SUBAGENT_WORKERS:
        return WorkerKind.SUBAGENT, worker_name != FOREGROUND_SUBAGENT
    try:
        return WorkerKind(worker_name), True
    except ValueError as error:
        message = f"unknown worker type {worker_name!r}"
        raise AssertionError(message) from error
