# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one claimed command job and store its final state."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.operations import ExtensionCommands
from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.commands import CommandRequest
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.operations import command_results
from baqylau_extension_api.schemas import SchemaSet

from domain.extension_jobs import JobState
from extensions import job_states
from extensions.registry_package import RegistryPackage
from repository.contract.extension_jobs import ExtensionJob, ExtensionJobRepository

UNKNOWN_CODE = "host.reply_lost"
UNKNOWN_MESSAGE = "The command call ended without a checked result, so its outcome is not known."


@dataclass(frozen=True)
class CommandRun:
    """Keep the command capability, request, and package schemas together."""

    capability: ExtensionCommands
    request: CommandRequest
    package: RegistryPackage
    schemas: SchemaSet


def execute(jobs: ExtensionJobRepository, accepted: ExtensionJob, command_run: CommandRun) -> ExtensionJob:
    """Claim the accepted job, call the capability once, and store the checked result.

    Returns:
        The stored job after its final state, or the current job when another worker claimed it.

    """
    running = job_states.claim(jobs, job_states.state_change(accepted, JobState.RUNNING))
    if running is None:
        return job_states.current(jobs, accepted)
    try:
        checked = checked_result(command_run, command_run.capability.execute(command_run.request))
    except Exception:  # noqa: BLE001 -- A lost or invalid reply may follow an external write.
        lost = Diagnostic(code=UNKNOWN_CODE, message=UNKNOWN_MESSAGE)
        return job_states.settle(jobs, running, JobState.OUTCOME_UNKNOWN, diagnostic=lost)
    return job_states.settle(jobs, running, JobState(checked.status), job_result=checked)


def checked_result(command_run: CommandRun, reply: CommandResult) -> CommandResult:
    """Check one capability reply against the attempt's binding and the package schemas.

    Returns:
        The checked result.

    """
    checked = command_results.validate_command_response(command_run.request.binding, reply)
    command_results.validate_command_documents(command_run.package.manifest, command_run.schemas, checked)
    return checked
