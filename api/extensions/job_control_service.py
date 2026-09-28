# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one job control operation against the active snapshot and map its errors to HTTP."""

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.errors import ExtensionContractError
from fastapi import Depends, HTTPException

from app import (
    provider_extension_jobs as job_providers,
    provider_extension_registry as registry_providers,
    provider_extension_runtime as runtime_providers,
)
from extensions import command_packages, job_control as host_job_control, observer_packages, runtime_identity
from extensions.observer_models import ObserverStores
from extensions.registry_contract import ExtensionRegistry

NOT_FOUND_ERRORS = (
    host_job_control.JobNotFoundError, command_packages.CommandNotFoundError, observer_packages.ObserverNotFoundError,
)
CONFLICT_ERRORS = (host_job_control.JobRevisionError, host_job_control.RuntimeIdentityError)


@dataclass(frozen=True)
class JobControlServices:
    """Keep the job control dependencies together for one request."""

    registry: ExtensionRegistry
    stores: ObserverStores
    runtime: runtime_providers.RuntimeManager


def job_control_services(
    registry: registry_providers.Registry, stores: job_providers.JobStores, runtime: runtime_providers.Runtime,
) -> JobControlServices:
    """Build the job control dependencies for one request.

    Returns:
        The active registry, the job stores, and the daemon manager owner.

    """
    return JobControlServices(registry=registry, stores=stores, runtime=runtime)


JobControlDependencies = Annotated[JobControlServices, Depends(job_control_services)]


def control[Outcome](
    job_control_services: JobControlServices, operation: Callable[[host_job_control.JobControl], Outcome],
) -> Outcome:
    """Run one operation with the active packages and the committed manager of the snapshot.

    Returns:
        The operation outcome.

    Raises:
        HTTPException: If the job or package is absent, the revision changed, or the request is invalid.

    """
    with job_control_services.registry.read_snapshot() as read:
        manager = job_control_services.runtime.manager
        revision = read.snapshot.directory.runtime_revision
        manager_id = None if manager is None else runtime_identity.snapshot_manager_id(manager.read_state(), revision)
        job_control = host_job_control.JobControl(read.snapshot.packages, job_control_services.stores, manager_id)
        try:
            return operation(job_control)
        except NOT_FOUND_ERRORS as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
        except CONFLICT_ERRORS as error:
            raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error
        except ExtensionContractError as error:
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error
