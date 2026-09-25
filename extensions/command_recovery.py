# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop or reconcile one stored command job without a second command call."""

from baqylau_extension_api.models.commands import (
    CommandBinding,
    CommandCancelRequest,
    CommandCancelResult,
    CommandReconcileRequest,
    CommandRequest,
)
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.operations import command_results, commands as command_operations
from baqylau_extension_api.schemas import SchemaSet

from domain.extension_jobs import JobState
from extensions.command_execution import CommandRun, checked_result
from extensions.command_packages import command_package
from extensions.job_states import settle
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import ExtensionJob, ExtensionJobRepository

STOPPED_STATES = (JobState.CANCELED, JobState.OUTCOME_UNKNOWN)


def cancel_command_job(
    packages: tuple[RegistryPackage, ...], jobs: ExtensionJobRepository, job: ExtensionJob, reason: str,
) -> tuple[ExtensionJob, CommandCancelResult]:
    """Ask the command capability to stop one attempt and store only a proven stop.

    Returns:
        The stored job and the checked acknowledgment.

    """
    package, capability = command_package(packages, job.owner)
    cancel_request = CommandCancelRequest(binding=CommandBinding.model_validate_json(job.binding), reason=reason)
    checked = command_operations.validate_cancel_request(package.manifest, cancel_request)
    cancel_result = command_results.validate_cancel_response(checked, capability.cancel(checked))
    if cancel_result.status not in STOPPED_STATES:
        return job, cancel_result
    return settle(jobs, job, JobState(cancel_result.status), diagnostic=cancel_result.diagnostic), cancel_result


def reconcile_command_job(
    packages: tuple[RegistryPackage, ...],
    jobs: ExtensionJobRepository,
    job: ExtensionJob,
    receipt: EncodedDocument | None,
) -> ExtensionJob:
    """Inspect an uncertain command result without repeating the command.

    Returns:
        The stored job after the reconciled outcome.

    """
    package, capability = command_package(packages, job.owner)
    command_run = CommandRun(
        capability=capability,
        request=CommandRequest.model_validate_json(job.request),
        package=package,
        schemas=SchemaSet(package.manifest.schemas),
    )
    checked = command_operations.validate_reconcile_request(
        package.manifest, command_run.schemas, CommandReconcileRequest(command=command_run.request, receipt=receipt),
    )
    command_result = checked_result(command_run, capability.reconcile(checked))
    return settle(jobs, job, JobState(command_result.status), job_result=command_result)
