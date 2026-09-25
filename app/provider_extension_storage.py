# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep extension worker environments and package copies beside the main database."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app import provider_databases, provider_extension_artifacts, provider_extension_policy, provider_extension_registry
from app.injection import singleton
from extensions import configuration, environments, preparation_runner, retention
from extensions.impl.process.factory import ProcessExtensionWorkers
from repository.impl.sqlite.extension_retention import SqliteRetainedDigests


@singleton
def extension_workers(
    database: provider_databases.MainDb, artifacts: provider_extension_artifacts.Artifacts,
    ledger: provider_extension_registry.CallLedger, worker_policy: provider_extension_policy.WorkerLimits,
) -> ProcessExtensionWorkers:
    """Keep private environments beside this application's actual database.

    Returns:
        The worker factory with the host-selected call limits.

    """
    private_environments = environments.LocalExtensionEnvironments(
        Path(database.path).parent / configuration.ENVIRONMENTS_DIRECTORY, artifacts,
        preparation_runner.BoundedPreparationRunner(),
    )
    return ProcessExtensionWorkers(private_environments, ledger, worker_policy)


Workers = Annotated[ProcessExtensionWorkers, Depends(extension_workers)]


@singleton
def startup_retention(database: provider_databases.MainDb) -> retention.StartupRetention:
    """Remove unreferred copies and stopped environments when the manager starts.

    Returns:
        The retention over the application's storage.

    """
    data_directory = Path(database.path).parent
    return retention.StartupRetention(
        data_directory / configuration.ARTIFACTS_DIRECTORY, data_directory / configuration.ENVIRONMENTS_DIRECTORY,
        SqliteRetainedDigests(database),
    )


Retention = Annotated[retention.StartupRetention, Depends(startup_retention)]
