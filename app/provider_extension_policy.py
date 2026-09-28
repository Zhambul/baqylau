# Copyright (c) 2026 Zhambyl Yermagambet
"""Freeze the extension write policy and host limits of one application instance."""

import os
from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from extensions.configuration import configured_health_policy, configured_read_only, configured_worker_policy
from extensions.control_policy import ExtensionControlPolicy
from extensions.models.extension_health import HealthPolicy
from extensions.models.source_processing import SourcePolicy
from extensions.models.workers import WorkerPolicy


@singleton
def extension_control_policy() -> ExtensionControlPolicy:
    """Freeze management policy for this application instance.

    Returns:
        Extension write admission policy, not a global Baqylau read-only mode.

    """
    return ExtensionControlPolicy(read_only=configured_read_only(os.environ))


ControlPolicy = Annotated[ExtensionControlPolicy, Depends(extension_control_policy)]


@singleton
def extension_worker_policy() -> WorkerPolicy:
    """Freeze the longest time of one worker call for this application instance.

    Returns:
        The host-selected worker limits.

    """
    return configured_worker_policy(os.environ)


WorkerLimits = Annotated[WorkerPolicy, Depends(extension_worker_policy)]


@singleton
def extension_source_policy(worker: WorkerLimits) -> SourcePolicy:
    """Bound each source call by the same call time as the worker.

    Returns:
        The source limits.

    """
    return SourcePolicy(call_seconds=worker.request_seconds)


SourceLimits = Annotated[SourcePolicy, Depends(extension_source_policy)]


@singleton
def extension_health_policy() -> HealthPolicy:
    """Freeze how many consecutive failed calls make an extension failed.

    Returns:
        The host-selected health limits.

    """
    return configured_health_policy(os.environ)


HealthLimits = Annotated[HealthPolicy, Depends(extension_health_policy)]
