# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and run one durable command against an active package."""

from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

from baqylau_extension_api.contracts.operations import ExtensionCommands
from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.commands import (
    CommandBinding,
    CommandCancelRequest,
    CommandReconcileRequest,
    CommandRequest,
)
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.operations import command_results, commands as command_operations, registration
from baqylau_extension_api.schemas import SchemaSet

from api.extensions import command_models, job_models
from extensions.control_policy import ExtensionControlPolicy, require_extension_write
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import (
    CommandJobRequest,
    ExtensionJob,
    ExtensionJobRepository,
    JobState,
    JobStateChange,
)

FAILED_CODE = "host.command_failed"
FAILED_MESSAGE = "The command failed before it produced a checked result."


class CommandNotFoundError(LookupError):
    """Reject a command whose package or declaration is not active."""


class JobNotFoundError(LookupError):
    """Reject a job operation for an absent job."""


class JobRevisionError(RuntimeError):
    """Reject a job operation whose exact revision no longer matches."""


@dataclass(frozen=True)
class CommandDispatch:
    """Keep the active package set, the job store, and the write policy together."""

    packages: tuple[RegistryPackage, ...]
    jobs: ExtensionJobRepository
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)


@dataclass(frozen=True)
class CommandRun:
    """Keep the command capability, request, and package schemas together."""

    capability: ExtensionCommands
    request: CommandRequest
    package: RegistryPackage
    schemas: SchemaSet


def run_command(
    dispatch: CommandDispatch,
    extension_id: str,
    command_id: str,
    scope: ExtensionScope,
    document: command_models.ExtensionCommandRequest,
) -> ExtensionJob:
    """Accept and run one command against the active package.

    Returns:
        The stored job after its final state.

    """
    package, capability = _command_package(dispatch.packages, extension_id)
    job_id = uuid4().hex
    runtime_revision = "" if package.environment is None else package.environment.runtime_revision
    binding = CommandBinding(
        extension_id=extension_id,
        operation_id=command_id,
        scope=scope,
        runtime_revision=runtime_revision,
        call_id=uuid4().hex,
        job_id=job_id,
        request_key=document.request_key,
    )
    definition = registration.command_definition(package.manifest, binding)
    if definition.effect == "write":
        require_extension_write(dispatch.policy)
    schemas = SchemaSet(package.manifest.schemas)
    request = CommandRequest(
        binding=binding,
        arguments=EncodedDocument(schema_ref=definition.arguments, json_text=document.arguments),
        settings_revision=package.settings.revision,
        settings=package.settings.for_scope(binding.scope),
        expected_state_revision=document.expected_state_revision,
    )
    checked = command_operations.validate_command_request(package.manifest, schemas, request)
    accepted = dispatch.jobs.accept_command(CommandJobRequest(
        owner=extension_id,
        scope=scope,
        job_id=job_id,
        request_key=document.request_key,
        binding=binding.model_dump_json(),
        request=checked.model_dump_json(),
    ))
    if accepted.state != "accepted":
        return accepted
    run = CommandRun(capability=capability, request=checked, package=package, schemas=schemas)
    return _execute(dispatch.jobs, accepted, run)


def cancel_command(
    dispatch: CommandDispatch,
    extension_id: str,
    job_id: str,
    scope: ExtensionScope,
    request: job_models.ExtensionJobCancelRequest,
) -> job_models.ExtensionJobCancelResponse:
    """Request cancellation of one stored job attempt.

    Returns:
        The checked cancellation request state.

    Raises:
        JobNotFoundError: If the job does not exist.
        JobRevisionError: If the stored revision no longer matches.

    """
    package, capability = _command_package(dispatch.packages, extension_id)
    job = dispatch.jobs.read(extension_id, scope, job_id)
    if job is None:
        message = "extension job not found"
        raise JobNotFoundError(message)
    if job.revision != request.expected_revision:
        message = "extension job revision changed"
        raise JobRevisionError(message)
    binding = CommandBinding.model_validate_json(job.binding)
    cancel_request = CommandCancelRequest(binding=binding, reason=request.reason)
    checked = command_operations.validate_cancel_request(package.manifest, cancel_request)
    result = command_results.validate_cancel_response(checked, capability.cancel(checked))
    stored = _apply_cancel(dispatch.jobs, job, result)
    return job_models.ExtensionJobCancelResponse(
        status=result.status,
        revision=stored.revision,
        diagnostic=result.diagnostic,
    )


def reconcile_command(
    dispatch: CommandDispatch,
    extension_id: str,
    job_id: str,
    scope: ExtensionScope,
    request: job_models.ExtensionJobReconcileRequest,
) -> ExtensionJob:
    """Inspect an uncertain job result without repeating the command.

    Returns:
        The stored job after the reconciled outcome.

    Raises:
        JobNotFoundError: If the job does not exist.
        JobRevisionError: If the stored revision no longer matches.

    """
    package, capability = _command_package(dispatch.packages, extension_id)
    job = dispatch.jobs.read(extension_id, scope, job_id)
    if job is None:
        message = "extension job not found"
        raise JobNotFoundError(message)
    if request.expected_revision is not None and job.revision != request.expected_revision:
        message = "extension job revision changed"
        raise JobRevisionError(message)
    reconcile_request = CommandReconcileRequest(
        command=CommandRequest.model_validate_json(job.request), receipt=request.receipt,
    )
    schemas = SchemaSet(package.manifest.schemas)
    checked = command_operations.validate_reconcile_request(package.manifest, schemas, reconcile_request)
    result = command_results.validate_command_response(
        checked.command.binding, capability.reconcile(checked),
    )
    command_results.validate_command_documents(package.manifest, schemas, result)
    return _store_result(dispatch.jobs, job, result)


def _apply_cancel(jobs: ExtensionJobRepository, job: ExtensionJob, result: object) -> ExtensionJob:
    status = getattr(result, "status", "not_running")
    if status not in {"canceled", "outcome_unknown"}:
        return job
    diagnostic = getattr(result, "diagnostic", None)
    try:
        return jobs.update_state(JobStateChange(
            owner=job.owner,
            scope=job.scope,
            job_id=job.job_id,
            expected_revision=job.revision,
            state=cast("JobState", status),
            diagnostic=None if diagnostic is None else diagnostic.model_dump_json(),
        ))
    except ValueError:
        current = jobs.read(job.owner, job.scope, job.job_id)
        return job if current is None else current


def _command_package(
    packages: tuple[RegistryPackage, ...], extension_id: str,
) -> tuple[RegistryPackage, ExtensionCommands]:
    for package in packages:
        if package.manifest.extension_id != extension_id:
            continue
        plugin = package.plugin
        if plugin is not None and plugin.capabilities.commands is not None:
            return package, plugin.capabilities.commands
    message = "extension command not found"
    raise CommandNotFoundError(message)


def _execute(jobs: ExtensionJobRepository, accepted: ExtensionJob, run: CommandRun) -> ExtensionJob:
    try:
        running = jobs.update_state(JobStateChange(
            owner=accepted.owner,
            scope=accepted.scope,
            job_id=accepted.job_id,
            expected_revision=accepted.revision,
            state="running",
        ))
    except ValueError:
        current = jobs.read(accepted.owner, accepted.scope, accepted.job_id)
        return accepted if current is None else current
    try:
        result = run.capability.execute(run.request)
        checked = command_results.validate_command_response(run.request.binding, result)
        command_results.validate_command_documents(run.package.manifest, run.schemas, checked)
    except Exception:  # noqa: BLE001 -- One feature failure must not escape as a host error.
        return jobs.update_state(JobStateChange(
            owner=running.owner,
            scope=running.scope,
            job_id=running.job_id,
            expected_revision=running.revision,
            state="failed",
            diagnostic=Diagnostic(code=FAILED_CODE, message=FAILED_MESSAGE).model_dump_json(),
        ))
    return _store_result(jobs, running, checked)


def _store_result(jobs: ExtensionJobRepository, job: ExtensionJob, result: CommandResult) -> ExtensionJob:
    try:
        return jobs.update_state(JobStateChange(
            owner=job.owner,
            scope=job.scope,
            job_id=job.job_id,
            expected_revision=job.revision,
            state=_state(result),
            result=result.model_dump_json(),
        ))
    except ValueError:
        current = jobs.read(job.owner, job.scope, job.job_id)
        return job if current is None else current


def _state(result: object) -> JobState:
    status = getattr(result, "status", "failed")
    return cast("JobState", status) if status in {"succeeded", "failed", "canceled", "outcome_unknown"} else "failed"
