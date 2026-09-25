# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept and run durable command jobs against an active package."""

from dataclasses import dataclass, field
from uuid import uuid4

from baqylau_extension_api.models import documents, scopes
from baqylau_extension_api.models.commands import CommandBinding, CommandRequest
from baqylau_extension_api.operations import commands as command_operations, registration
from baqylau_extension_api.schemas import SchemaSet

from domain.extension_jobs import JobState
from domain.ids import ExtensionJobId
from extensions import command_execution, command_packages, job_states
from extensions.control_policy import ExtensionControlPolicy, require_extension_write
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import CommandJobRequest, ExtensionJob, ExtensionJobRepository

READ_ONLY_CODE = "host.read_only"
READ_ONLY_MESSAGE = "The host is read-only, so the write did not run."
STALE_CODE = "host.stale_request"
STALE_MESSAGE = "The runtime or settings changed after acceptance, so the command did not run."


@dataclass(frozen=True)
class CommandSubmission:
    """Carry one command's arguments and stable request key."""

    arguments: str
    request_key: str
    expected_state_revision: str | None = None


@dataclass(frozen=True)
class CommandDispatch:
    """Keep the active package set, the job store, and the write policy together."""

    packages: tuple[RegistryPackage, ...]
    jobs: ExtensionJobRepository
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)


def accept_command(
    dispatch: CommandDispatch,
    extension_id: str,
    command_id: str,
    scope: scopes.ExtensionScope,
    submission: CommandSubmission,
) -> ExtensionJob:
    """Validate and store one command without running it.

    Returns:
        The accepted job, or the stored job for a repeated request key.

    """
    package, _capability = command_packages.command_package(dispatch.packages, extension_id)
    binding = CommandBinding(
        extension_id=extension_id,
        operation_id=command_id,
        scope=scope,
        runtime_revision=command_packages.runtime_revision(package),
        call_id=uuid4().hex,
        job_id=uuid4().hex,
        request_key=submission.request_key,
    )
    definition = registration.command_definition(package.manifest, binding)
    if definition.effect == "write":
        require_extension_write(dispatch.policy)
    request = _checked_request(package, definition.arguments, binding, submission)
    return dispatch.jobs.accept_command(CommandJobRequest(
        owner=extension_id,
        scope=scope,
        job_id=ExtensionJobId(binding.job_id),
        request_key=submission.request_key,
        binding=binding.model_dump_json(),
        request=request.model_dump_json(),
    ))


def run_command(
    dispatch: CommandDispatch,
    extension_id: str,
    command_id: str,
    scope: scopes.ExtensionScope,
    submission: CommandSubmission,
) -> ExtensionJob:
    """Accept and run one command against the active package.

    Returns:
        The stored job after its final state.

    """
    return execute_command_job(dispatch, accept_command(dispatch, extension_id, command_id, scope, submission))


def execute_command_job(dispatch: CommandDispatch, accepted: ExtensionJob) -> ExtensionJob:
    """Claim and run one accepted command job against the active package.

    A write command that reaches the executor in read-only mode fails without a
    call, so an old accepted write cannot run after the policy changed. A
    command whose runtime or settings changed after acceptance also fails
    without a call, so it cannot apply an obsolete selection.

    Returns:
        The stored job after its final state, or the job when it is not accepted.

    """
    if accepted.state != JobState.ACCEPTED:
        return accepted
    package, capability = command_packages.command_package(dispatch.packages, accepted.owner)
    request = CommandRequest.model_validate_json(accepted.request)
    refusal = _refusal(dispatch.policy, package, request)
    if refusal is not None:
        return job_states.settle(dispatch.jobs, accepted, JobState.FAILED, diagnostic=refusal)
    command_run = command_execution.CommandRun(
        capability=capability, request=request, package=package, schemas=SchemaSet(package.manifest.schemas),
    )
    return command_execution.execute(dispatch.jobs, accepted, command_run)


def _checked_request(
    package: RegistryPackage,
    arguments_schema: documents.SchemaRef,
    binding: CommandBinding,
    submission: CommandSubmission,
) -> CommandRequest:
    request = CommandRequest(
        binding=binding,
        arguments=documents.EncodedDocument(schema_ref=arguments_schema, json_text=submission.arguments),
        settings_revision=package.settings.revision,
        settings=package.resolved_settings.for_scope(binding.scope),
        expected_state_revision=submission.expected_state_revision,
    )
    return command_operations.validate_command_request(package.manifest, SchemaSet(package.manifest.schemas), request)


def _refusal(
    policy: ExtensionControlPolicy, package: RegistryPackage, request: CommandRequest,
) -> documents.Diagnostic | None:
    definition = registration.command_definition(package.manifest, request.binding)
    if definition.effect == "write" and policy.read_only:
        return documents.Diagnostic(code=READ_ONLY_CODE, message=READ_ONLY_MESSAGE)
    selection = (command_packages.runtime_revision(package), package.settings.revision)
    if (request.binding.runtime_revision, request.settings_revision) != selection:
        return documents.Diagnostic(code=STALE_CODE, message=STALE_MESSAGE)
    return None
