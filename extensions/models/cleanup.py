# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep physical resource closure separate from unresolved external work."""

from typing import Annotated, Literal

from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from pydantic import Field

from extensions.models.lifecycle_operations import Timestamp


class RetirementIssue(WireModel):
    """Retain uncertainty without claiming that an external job has completed."""

    runtime_revision: Identifier
    extension_id: ExtensionId | None = None
    reason: Literal["unresolved_jobs", "deactivation_failed", "close_failed", "cleanup_unavailable"]
    pending_job_ids: Annotated[tuple[Identifier, ...], Field(max_length=1000)] = ()


class RuntimeShutdown(WireModel):
    """Report one drained runtime and the result of its resource close."""

    runtime_revision: Identifier
    resources_closed: bool
    issues: tuple[RetirementIssue, ...] = ()


class ShutdownRecord(WireModel):
    """Store an immutable shutdown observation before native ownership is released."""

    record_id: Identifier
    manager_id: Identifier
    recorded_at: Timestamp
    runtimes: tuple[RuntimeShutdown, ...]
