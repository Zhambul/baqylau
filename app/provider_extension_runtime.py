# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose runtime services without starting workers during dependency resolution."""

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app import (
    provider_databases,
    provider_extension_artifacts,
    provider_extension_registry,
    provider_extension_storage,
    provider_work_queue,
    provider_worker_services,
)
from app.injection import singleton
from core.work_queue import WorkKind
from extensions import (
    manager_contract,
    manager_factory,
    manager_resources,
    runtime_ownership,
    runtime_preparation,
    runtime_preparation_contract,
)
from repository.impl.sqlite import extension_catalog, extension_lifecycle


@singleton
def runtime_preparer(
    artifacts: provider_extension_artifacts.Artifacts,
    workers: provider_extension_storage.Workers,
    registry: provider_extension_registry.Registry, ledger: provider_extension_registry.CallLedger,
    services: provider_worker_services.WorkerServices,
) -> runtime_preparation_contract.ExtensionRuntimePreparation:
    """Prepare complete runtimes with the application's workers.

    Returns:
        The complete-runtime preparer, with no active process yet.

    """
    return runtime_preparation.RuntimePreparation(artifacts, workers, registry, ledger, services)


Preparation = Annotated[runtime_preparation_contract.ExtensionRuntimePreparation, Depends(runtime_preparer)]


@singleton
def extension_manager_factory(
    database: provider_databases.MainDb, registry: provider_extension_registry.Registry,
    preparation: Preparation, work_queue: provider_work_queue.EngineWork,
    startup_retention: provider_extension_storage.Retention,
) -> manager_factory.ExtensionManagerFactory:
    """Build the explicit startup factory; only daemon startup opens its manager.

    Returns:
        A factory using the application's database, queue, and data-directory lease.

    """
    return manager_factory.ExtensionManagerFactory(
        extension_lifecycle.SqliteExtensionLifecycleRepository(database),
        extension_catalog.SqliteExtensionCatalogRepository(database), registry, preparation,
        runtime_ownership.FilesystemRuntimeOwnership(Path(database.path).parent),
        manager_resources.ManagerCallbacks(partial(work_queue.put, WorkKind.EXTENSIONS)),
        retention=startup_retention,
    )


@dataclass(frozen=True)
class RuntimeManager:
    """Keep daemon-owned state distinct from a request-only application graph."""

    manager: manager_contract.ExtensionManager | None = None


@singleton
def extension_runtime() -> RuntimeManager:
    """Require daemon startup to seed an owned manager before it builds the engine.

    Returns:
        An empty owner for request-only applications and standalone engine tests.

    """
    return RuntimeManager()


Runtime = Annotated[RuntimeManager, Depends(extension_runtime)]
