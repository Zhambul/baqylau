# Copyright (c) 2026 Zhambyl Yermagambet
"""Check accepted command inputs before execution or reconciliation."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.models.commands import CommandCancelRequest, CommandReconcileRequest
from baqylau_extension_api.operations import commands
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import operation_samples, samples


def test_read_command_requires_valid_registration() -> None:
    """Accept a typed read-class job with no artificial write precondition."""
    manifest = validate_manifest(operation_samples.manifest())
    request = operation_samples.command_request()
    assert commands.validate_command_request(manifest, SchemaSet(manifest.schemas), request) == request


def test_write_command_requires_expected_state() -> None:
    """Require an explicit state revision before a write reaches feature code."""
    manifest = validate_manifest(operation_samples.manifest(effect="write"))
    schemas = SchemaSet(manifest.schemas)
    request = operation_samples.command_request()
    with pytest.raises(ExtensionContractError, match="expected state"):
        commands.validate_command_request(manifest, schemas, request)
    checked = request.model_copy(update={"expected_state_revision": "state-1"})
    assert commands.validate_command_request(manifest, schemas, checked) == checked


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"operation_id": "test.sample.absent"}, {"scope": samples.SESSION},
])
def test_command_checks_owner_and_scope(change: dict[str, object]) -> None:
    """Reject unknown operations and a different selected scope."""
    manifest = operation_samples.manifest()
    request = operation_samples.command_request()
    request = request.model_copy(update={"binding": request.binding.model_copy(update=change)})
    with pytest.raises(ExtensionContractError):
        commands.validate_command_request(manifest, SchemaSet(manifest.schemas), request)


def test_command_arguments_are_schema_validated() -> None:
    """Reject invalid encoded content despite a well-formed request envelope."""
    manifest = operation_samples.manifest()
    with pytest.raises(ExtensionContractError):
        commands.validate_command_request(
            manifest, SchemaSet(manifest.schemas), operation_samples.command_request("42"),
        )


def test_reconciliation_requires_a_declaration() -> None:
    """Do not call recovery for a command that does not support it."""
    manifest = operation_samples.manifest(reconciliation=False)
    request = CommandReconcileRequest(command=operation_samples.command_request())
    with pytest.raises(ExtensionContractError, match="reconciliation is not declared"):
        commands.validate_reconcile_request(manifest, SchemaSet(manifest.schemas), request)


def test_reconciliation_checks_owned_receipt() -> None:
    """Check persisted recovery evidence against the loaded schema set."""
    manifest = operation_samples.manifest()
    command = operation_samples.command_request()
    request = CommandReconcileRequest(command=command, receipt=command.arguments)
    assert commands.validate_reconcile_request(manifest, SchemaSet(manifest.schemas), request) == request
    with pytest.raises(ExtensionContractError):
        commands.validate_reconcile_request(manifest, SchemaSet(manifest.schemas), request.model_copy(update={
            "receipt": samples.encoded_document(),
        }))


def test_cancel_checks_command_registration() -> None:
    """Do not forward a stop request for an undeclared command."""
    manifest = operation_samples.manifest()
    binding = operation_samples.command_request().binding.model_copy(update={"operation_id": "test.sample.absent"})
    request = CommandCancelRequest(binding=binding, reason="User requested a stop.")
    with pytest.raises(ExtensionContractError, match="not declared"):
        commands.validate_cancel_request(manifest, request)
