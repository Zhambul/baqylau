# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and run one durable extension command."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import ExtensionScope
from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter, ValidationError

from api.extensions import command_service, job_service
from api.extensions.admission import require_extension_json
from api.extensions.command_models import ExtensionCommandRequest
from api.extensions.job_models import (
    ExtensionJobCancelRequest,
    ExtensionJobCancelResponse,
    ExtensionJobReconcileRequest,
    ExtensionJobResponse,
)
from app.provider_extension_controls import ControlPolicy
from app.provider_extension_jobs import Jobs
from app.provider_extension_registry import Registry
from extensions.control_policy import ExtensionControlPolicy
from extensions.registry_contract import ExtensionRegistry
from repository.contract.extension_jobs import ExtensionJobRepository

router = APIRouter()
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


@dataclass(frozen=True)
class CommandServices:
    """Keep the command dependencies together for one request."""

    jobs: ExtensionJobRepository
    registry: ExtensionRegistry
    policy: ExtensionControlPolicy


def command_services(jobs: Jobs, registry: Registry, policy: ControlPolicy) -> CommandServices:
    """Build the command dependencies for one request.

    Returns:
        The job store, the active registry, and the host write policy.

    """
    return CommandServices(jobs=jobs, registry=registry, policy=policy)


CommandDependencies = Annotated[CommandServices, Depends(command_services)]


@router.post(
    "/api/extensions/{extension_id}/commands/{command_id}",
    dependencies=[Depends(require_extension_json)],
)
def extension_command(
    extension_id: str,
    command_id: str,
    extension_command_request: ExtensionCommandRequest,
    services: CommandDependencies,
) -> ExtensionJobResponse:
    """Accept and run one declared command against the active package.

    Returns:
        The stored job after its final state.

    Raises:
        HTTPException: If the scope, package, or declaration is invalid.

    """
    try:
        scope = SCOPE_ADAPTER.validate_json(extension_command_request.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    with services.registry.read_snapshot() as read:
        try:
            job = command_service.run_command(
                command_service.CommandDispatch(read.snapshot.packages, services.jobs, services.policy),
                extension_id,
                command_id,
                scope,
                extension_command_request,
            )
        except command_service.CommandNotFoundError as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
        except ExtensionContractError as error:
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error
    return job_service.job_response(job)


@router.post(
    "/api/extensions/{extension_id}/jobs/{job_id}/cancel",
    dependencies=[Depends(require_extension_json)],
)
def cancel_extension_job(
    extension_id: str,
    job_id: str,
    extension_job_cancel_request: ExtensionJobCancelRequest,
    services: CommandDependencies,
) -> ExtensionJobCancelResponse:
    """Request cancellation of one stored job attempt.

    Returns:
        The checked cancellation request state.

    Raises:
        HTTPException: If the scope, job, or revision is invalid.

    """
    try:
        scope = SCOPE_ADAPTER.validate_json(extension_job_cancel_request.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    with services.registry.read_snapshot() as read:
        try:
            return command_service.cancel_command(
                command_service.CommandDispatch(read.snapshot.packages, services.jobs, services.policy),
                extension_id,
                job_id,
                scope,
                extension_job_cancel_request,
            )
        except (command_service.JobNotFoundError, command_service.CommandNotFoundError) as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
        except command_service.JobRevisionError as error:
            raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error
        except ExtensionContractError as error:
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error


@router.post(
    "/api/extensions/{extension_id}/jobs/{job_id}/reconcile",
    dependencies=[Depends(require_extension_json)],
)
def reconcile_extension_job(
    extension_id: str,
    job_id: str,
    extension_job_reconcile_request: ExtensionJobReconcileRequest,
    services: CommandDependencies,
) -> ExtensionJobResponse:
    """Inspect an uncertain job result without repeating the command.

    Returns:
        The stored job after the reconciled outcome.

    Raises:
        HTTPException: If the scope, job, or revision is invalid.

    """
    try:
        scope = SCOPE_ADAPTER.validate_json(extension_job_reconcile_request.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    with services.registry.read_snapshot() as read:
        try:
            job = command_service.reconcile_command(
                command_service.CommandDispatch(read.snapshot.packages, services.jobs, services.policy),
                extension_id,
                job_id,
                scope,
                extension_job_reconcile_request,
            )
        except (command_service.JobNotFoundError, command_service.CommandNotFoundError) as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
        except command_service.JobRevisionError as error:
            raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error
        except ExtensionContractError as error:
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error
    return job_service.job_response(job)
