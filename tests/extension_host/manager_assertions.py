# Copyright (c) 2026 Zhambyl Yermagambet
"""Narrow optional manager reads before asserting the stored and active selection."""

from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.manager import ManagerSnapshot
from tests.extension_host import manager_fixture


def committed(snapshot: ManagerSnapshot) -> RuntimeSelection:
    """Require a completed stored runtime before reading its fields.

    Returns:
        The checked stored selection, not proof of a running worker.

    """
    assert snapshot.lifecycle.committed_runtime is not None
    return snapshot.lifecycle.committed_runtime


def require_operation(host: manager_fixture.ManagerHost, operation_id: str, status: str = "succeeded") -> None:
    """Read the manager's durable operation result."""
    operation = host.controller.read_operation(operation_id)
    assert operation is not None and operation.status == status


def require_disabled_directory(host: manager_fixture.ManagerHost) -> None:
    """Keep an installed inactive package visible to peers without a live worker."""
    with host.runtime.preparation.registry.read_snapshot() as selected:
        assert not selected.snapshot.active_order
        package = selected.snapshot.packages[0]
        assert package.entry.state == "disabled"
