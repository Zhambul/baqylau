# Copyright (c) 2026 Zhambyl Yermagambet
"""Scan package roots and change one package's lifecycle through the public HTTP routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from baqylau_extension_testkit.lifecycle_models import (
    CatalogDocument,
    HostDocument,
    LifecycleAction,
    LifecycleChange,
    OperationDocument,
    RuntimeDocument,
)
from baqylau_extension_testkit.waiting import wait_until

if TYPE_CHECKING:
    from baqylau_extension_testkit.client import HostClient

# Private worker preparation builds a package environment; this takes time on a loaded machine.
OPERATION_SECONDS = 120.0
RUNNING_PHASE = "running"
PREPARING_STATUS = "preparing"


class _Rescan(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_revision: int


class _Admission(HostDocument):
    operation: OperationDocument


def rescan(client: HostClient) -> CatalogDocument:
    """Scan the package roots again at the observed catalog revision.

    Returns:
        The new catalog.

    """
    current = client.read("/api/extensions", CatalogDocument)
    return client.send("/api/extensions/rescan", _Rescan(expected_revision=current.revision), CatalogDocument)


def change(client: HostClient, owner: str, action: LifecycleAction, request_id: str) -> OperationDocument:
    """Ask for one lifecycle change and wait for its stored result and cleanup.

    Returns:
        The final operation; the caller checks its status.

    """
    request = _request(client, owner, action, request_id)
    admitted = client.send(f"/api/extensions/{owner}/lifecycle", request, _Admission).operation
    path = f"/api/extensions/operations/{admitted.operation_id}"
    wait_until(
        lambda: _settled(client.read(path, OperationDocument)), OPERATION_SECONDS,
        lambda: f"operation {admitted.operation_id} is still preparing",
    )
    wait_until(
        lambda: _running(client, cleaned=True), OPERATION_SECONDS, lambda: "extension cleanup is still pending",
    )
    return client.read(path, OperationDocument)


def _request(client: HostClient, owner: str, action: LifecycleAction, request_id: str) -> LifecycleChange:
    runtime = wait_until(
        lambda: _running(client), OPERATION_SECONDS, lambda: "the extension runtime is not running",
    )
    catalog = client.read("/api/extensions", CatalogDocument)
    digest = None if action == "disable" else next(
        entry.package_digest for entry in catalog.entries if entry.extension_id == owner
    )
    return LifecycleChange(
        action=action, request_id=request_id, expected_revision=runtime.revision,
        expected_catalog_revision=catalog.revision, package_digest=digest,
    )


def _running(client: HostClient, *, cleaned: bool = False) -> RuntimeDocument | None:
    runtime = client.read("/api/extensions/state", RuntimeDocument)
    if runtime.phase != RUNNING_PHASE or (cleaned and runtime.cleanup_pending):
        return None
    return runtime


def _settled(operation: OperationDocument) -> OperationDocument | None:
    return None if operation.status == PREPARING_STATUS else operation
