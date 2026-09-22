# Copyright (c) 2026 Zhambyl Yermagambet
"""Read actual manager progress without treating stored selection as live state."""

from typing import Literal

from baqylau_extension_api.models.base import Identifier, Revision, WireModel
from baqylau_extension_api.models.directory import DirectorySnapshot

from extensions.models.cleanup import RetirementIssue
from extensions.models.lifecycle_state import LifecycleState

type ManagerProgressStatus = Literal["idle", "preparing", "busy", "published", "failed", "fenced", "closing"]


class ManagerSnapshot(WireModel):
    """Separate durable requests, running workers, preparation, and cleanup."""

    lifecycle: LifecycleState
    registry_revision: Revision
    active_runtime: Identifier | None
    directory: DirectorySnapshot | None = None
    phase: Literal["running", "preparing", "awaiting_boundary", "closing", "closed", "fenced"]
    cleanup: tuple[RetirementIssue, ...] = ()
    cleanup_pending: bool = False


class ManagerProgress(WireModel):
    """Report one short engine-boundary attempt without running feature cleanup."""

    status: ManagerProgressStatus
    registry_revision: Revision
    allow_processing: bool = True
