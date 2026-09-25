# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and run one durable extension command."""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.errors import ExtensionContractError
from fastapi import APIRouter, Depends, HTTPException

from api.extensions import admission, command_models, command_service, job_models, job_service, scope_documents
from app import (
    provider_extension_executor as executor_providers,
    provider_extension_jobs as job_providers,
    provider_extension_policy as policy_providers,
    provider_extension_registry as registry_providers,
)
from extensions.control_policy import ExtensionControlPolicy
from extensions.job_requests import JobKey
from extensions.registry_contract import ExtensionRegistry
from repository.contract.extension_jobs import ExtensionJobRepository

router = APIRouter()


@dataclass(frozen=True)
class CommandServices:
    """Keep the command dependencies together for one request."""

    jobs: ExtensionJobRepository
    registry: ExtensionRegistry
    policy: ExtensionControlPolicy


def command_services(
    jobs: job_providers.Jobs, registry: registry_providers.Registry, policy: policy_providers.ControlPolicy,
) -> CommandServices:
    """Build the command dependencies for one request.

    Returns:
        The job store, the active registry, and the host write policy.

    """
    return CommandServices(jobs=jobs, registry=registry, policy=policy)


CommandDependencies = Annotated[CommandServices, Depends(command_services)]


@router.post(
    "/api/extensions/{extension_id}/commands/{command_id}",
    dependencies=[Depends(admission.require_extension_json)],
    status_code=HTTPStatus.ACCEPTED,
)
def extension_command(
    extension_id: str,
    command_id: str,
    extension_command_request: command_models.ExtensionCommandRequest,
    services: CommandDependencies,
    executor: executor_providers.JobExecution,
) -> job_models.ExtensionJobResponse:
    """Accept one declared command and schedule it on the background executor.

    Returns:
        The accepted job before its final state.

    Raises:
        HTTPException: If the scope, package, or declaration is invalid.

    """
    scope = scope_documents.request_scope(extension_command_request.scope)
    with services.registry.read_snapshot() as read:
        try:
            accepted = command_service.accept_command(
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
    if accepted.state == "accepted":
        executor.submit(JobKey(extension_id, scope, accepted.job_id))
    return job_service.job_response(accepted)
