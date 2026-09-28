# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the durable extension job and observer repositories over the main database."""

from typing import Annotated

from fastapi import Depends

from app import provider_databases as database_providers
from app.injection import singleton
from extensions.observer_models import ObserverStores
from repository.contract.extension_jobs import ExtensionJobRepository
from repository.contract.extension_observers import ExtensionObserverRepository
from repository.impl.sqlite.extension_jobs import SqliteExtensionJobRepository
from repository.impl.sqlite.extension_observers import SqliteExtensionObserverRepository


@singleton
def extension_jobs(database: database_providers.MainDb) -> ExtensionJobRepository:
    """Return the extension job repository.

    Returns:
        The job repository over the main database.

    """
    return SqliteExtensionJobRepository(database)


Jobs = Annotated[
    ExtensionJobRepository,
    Depends(extension_jobs),
]


@singleton
def extension_observers(database: database_providers.MainDb) -> ExtensionObserverRepository:
    """Return the extension observer repository.

    Returns:
        The observer cursor and settlement repository over the main database.

    """
    return SqliteExtensionObserverRepository(database)


@singleton
def observer_stores(
    jobs: Jobs, observers: Annotated[ExtensionObserverRepository, Depends(extension_observers)],
) -> ObserverStores:
    """Group the job and observer stores for execution and job control.

    Returns:
        The stores which settle observer and command jobs.

    """
    return ObserverStores(observers=observers, jobs=jobs)


JobStores = Annotated[ObserverStores, Depends(observer_stores)]
