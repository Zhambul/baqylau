# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate command replies before the host commits a job outcome."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.command_results import CommandOutcomeUnknown, CommandResult, CommandSucceeded
from baqylau_extension_api.models.commands import CommandBinding, CommandCancelRequest, CommandCancelResult
from baqylau_extension_api.operations import documents, observations, registration
from baqylau_extension_api.schemas import SchemaSet

MAX_COMMAND_RESPONSE_BYTES = 4_194_304


def validate_command_response(binding: CommandBinding, response: CommandResult) -> CommandResult:
    """Keep stale attempts and duplicate output references out of job storage.

    Returns:
        A bounded success, failure, canceled, or uncertain outcome.

    Raises:
        ExtensionContractError: If the reply has a changed binding or is too large.

    """
    checked = TypeAdapter[CommandResult](CommandResult).validate_python(response)
    if checked.binding != CommandBinding.model_validate(binding):
        message = "command result does not match its accepted job attempt"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_COMMAND_RESPONSE_BYTES:
        message = "command response exceeds its encoded size limit"
        raise ExtensionContractError(message)
    rules.require_unique((content.content_id for content in checked.content), "command content IDs")
    return checked


def validate_command_documents(manifest: ExtensionManifest, schemas: SchemaSet, response: CommandResult) -> None:
    """Validate result documents, receipts, and emitted observations together."""
    definition = registration.command_definition(manifest, response.binding)
    if isinstance(response, CommandSucceeded):
        documents.require_document_schema(response.document, definition.result, schemas, "command result")
    if isinstance(response, CommandOutcomeUnknown) and response.receipt is not None:
        documents.validate_owned_document(response.receipt, manifest.extension_id, schemas)
    observations.validate_observations(manifest, schemas, response.binding.scope, response.observations)


def validate_cancel_response(request: CommandCancelRequest, response: CommandCancelResult) -> CommandCancelResult:
    """Do not let a cancel acknowledgment finish a different job or attempt.

    Returns:
        The checked acknowledgment, not a stored final job outcome.

    Raises:
        ExtensionContractError: If the acknowledgment names another attempt.

    """
    checked_request = CommandCancelRequest.model_validate(request)
    checked = CommandCancelResult.model_validate(response)
    if checked.binding != checked_request.binding:
        message = "command cancellation does not match its requested job attempt"
        raise ExtensionContractError(message)
    return checked
