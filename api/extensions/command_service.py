# Copyright (c) 2026 Zhambyl Yermagambet
"""Adapt typed API command requests to the host command dispatch."""

from baqylau_extension_api.models.scopes import ExtensionScope

from api.extensions import command_models
from extensions.command_dispatch import (
    CommandDispatch as CommandDispatch,
    CommandSubmission,
    accept_command as accept_host_command,
    run_command as run_host_command,
)
from extensions.command_packages import CommandNotFoundError as CommandNotFoundError
from repository.contract.extension_jobs import ExtensionJob


def accept_command(
    dispatch: CommandDispatch,
    extension_id: str,
    command_id: str,
    scope: ExtensionScope,
    document: command_models.ExtensionCommandRequest,
) -> ExtensionJob:
    """Validate and store one command without running it.

    Returns:
        The accepted job, or the stored job for a repeated request key.

    """
    return accept_host_command(dispatch, extension_id, command_id, scope, submission(document))


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
    return run_host_command(dispatch, extension_id, command_id, scope, submission(document))


def submission(document: command_models.ExtensionCommandRequest) -> CommandSubmission:
    """Map one typed API command request onto the host submission.

    Returns:
        The host command submission.

    """
    return CommandSubmission(
        arguments=document.arguments,
        request_key=document.request_key,
        expected_state_revision=document.expected_state_revision,
    )
