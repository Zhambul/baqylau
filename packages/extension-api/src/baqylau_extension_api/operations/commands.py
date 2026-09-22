# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate accepted command inputs without executing or repeating work."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.commands import CommandCancelRequest, CommandReconcileRequest, CommandRequest
from baqylau_extension_api.operations import documents, registration
from baqylau_extension_api.schemas import SchemaSet


def validate_command_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: CommandRequest,
) -> CommandRequest:
    """Require a declared command, valid captured settings, and write preconditions.

    Returns:
        The checked immutable request; the host still must authorize this job.

    Raises:
        ExtensionContractError: If a write has no expected state revision.

    """
    checked = CommandRequest.model_validate(request)
    definition = registration.command_definition(manifest, checked.binding)
    documents.require_document_schema(checked.arguments, definition.arguments, schemas, "command arguments")
    documents.validate_settings(manifest, checked.settings, schemas)
    if definition.effect == "write" and checked.expected_state_revision is None:
        message = "write commands require an expected state revision"
        raise ExtensionContractError(message)
    return checked


def validate_reconcile_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: CommandReconcileRequest,
) -> CommandReconcileRequest:
    """Check a recovery request without dispatching the original command.

    Returns:
        The checked request and optional package-owned receipt.

    Raises:
        ExtensionContractError: If the command does not declare reconciliation.

    """
    checked = CommandReconcileRequest.model_validate(request)
    validate_command_request(manifest, schemas, checked.command)
    definition = registration.command_definition(manifest, checked.command.binding)
    if not definition.reconciliation:
        message = "command reconciliation is not declared"
        raise ExtensionContractError(message)
    if checked.receipt is not None:
        documents.validate_owned_document(checked.receipt, manifest.extension_id, schemas)
    return checked


def validate_cancel_request(manifest: ExtensionManifest, request: CommandCancelRequest) -> CommandCancelRequest:
    """Require a declared command before forwarding a stop request.

    Returns:
        The checked request; the host must also check the active job and attempt.

    """
    checked = CommandCancelRequest.model_validate(request)
    registration.command_definition(manifest, checked.binding)
    return checked
