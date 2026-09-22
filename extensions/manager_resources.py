# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep manager dependencies and owned futures outside public wire state."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event
from time import time
from typing import Literal

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.runtime.models import RequestTimeout

from extensions import registry_contract, runtime_ownership_contract, runtime_preparation_contract
from extensions.manager_retirement import RetirementOwner
from extensions.models import cleanup, lifecycle_operations
from repository.contract import extension_catalog, extension_lifecycle


class ManagerPolicy(WireModel):
    """Bound the wait for admitted registry calls during explicit manager close."""

    drain_seconds: RequestTimeout = 30.0
    deactivation_seconds: RequestTimeout = 2.0


@dataclass(frozen=True)
class ManagerCallbacks:
    """Wake the engine after preparation and use the host clock for stored outcomes."""

    changed: Callable[[], None]
    clock: Callable[[], float] = time


@dataclass(frozen=True)
class ManagerServices:
    """Keep one claimed process owner and its explicit application dependencies."""

    repository: extension_lifecycle.ExtensionLifecycleRepository
    catalog: extension_catalog.ExtensionCatalogRepository
    registry: registry_contract.ExtensionRegistry
    preparation: runtime_preparation_contract.ExtensionRuntimePreparation
    lease: runtime_ownership_contract.ExtensionRuntimeLease
    callbacks: ManagerCallbacks
    manager_id: Identifier


@dataclass(frozen=True)
class PreparationOutcome:
    """Return an owned candidate or one bounded host-selected failure."""

    prepared: runtime_preparation_contract.PreparedExtensionRuntime | None = None
    failure: lifecycle_operations.LifecycleFailure | None = None


@dataclass(frozen=True)
class PendingPreparation:
    """Bind asynchronous work to its exact accepted operation and registry revision."""

    operation: lifecycle_operations.LifecycleOperation
    expected_registry_revision: int
    task: Future[PreparationOutcome]


@dataclass
class ManagerRuntime:
    """Change these fields only while the manager mutex is held."""

    registry_revision: int
    active: runtime_preparation_contract.PreparedExtensionRuntime | None = None
    pending: PendingPreparation | None = None
    mode: Literal["running", "closing", "closed", "fenced"] = "running"
    retired: tuple[RetirementOwner, ...] = ()
    cleanup_task: Future[tuple[cleanup.RetirementIssue, ...]] | None = None
    cleanup_issues: tuple[cleanup.RetirementIssue, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ManagerSession:
    """Own one serial preparation executor and its stop request for a manager run."""

    services: ManagerServices
    runtime: ManagerRuntime
    executor: ThreadPoolExecutor
    stopped: Event
    policy: ManagerPolicy
