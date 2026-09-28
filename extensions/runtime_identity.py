# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind the committing manager to one borrowed registry snapshot."""

from typing import Protocol

from extensions.models.manager import ManagerSnapshot


class ManagerStateReads(Protocol):
    """Read the manager state without a worker call."""

    def read_state(self) -> ManagerSnapshot:
        """Read stored intent and observed manager state."""
        ...


def snapshot_manager_id(manager_snapshot: ManagerSnapshot, runtime_revision: str) -> str | None:
    """Return the manager identity only when its active runtime is the borrowed one.

    Returns:
        The manager identity, or None when no runtime is active or another one is.

    """
    if manager_snapshot.active_runtime != runtime_revision:
        return None
    return manager_snapshot.lifecycle.manager_id
