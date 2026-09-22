# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep job outcomes exact, bounded, and distinct from cancellation requests."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.command_results import (
    CommandCanceled,
    CommandFailed,
    CommandOutcomeUnknown,
    CommandResult,
    CommandSucceeded,
)
from baqylau_extension_api.models.commands import CommandCancelRequest, CommandCancelResult
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.operations import command_results
from baqylau_extension_api.schemas import SchemaSet
from pydantic import TypeAdapter, ValidationError

from tests.extension_api import operation_samples, samples

DIAGNOSTIC = Diagnostic(code="example", message="Recorded test outcome.")


def test_command_outcomes_round_trip() -> None:
    """Preserve proven success, failure, cancellation, and uncertainty on the wire."""
    request = operation_samples.command_request()
    responses: tuple[CommandResult, ...] = (
        CommandSucceeded(binding=request.binding, document=request.arguments),
        CommandFailed(binding=request.binding, diagnostic=DIAGNOSTIC),
        CommandCanceled(binding=request.binding),
        CommandOutcomeUnknown(binding=request.binding, diagnostic=DIAGNOSTIC, receipt=request.arguments),
    )
    adapter = TypeAdapter[CommandResult](CommandResult)
    for response in responses:
        assert adapter.validate_json(response.model_dump_json()) == response
        assert command_results.validate_command_response(request.binding, response) == response


@pytest.mark.parametrize("change", [
    {"job_id": "other-job"}, {"request_key": "other-request"}, {"call_id": "late-attempt"},
    {"runtime_revision": "stale"}, {"scope": samples.SESSION},
])
def test_command_reply_keeps_the_complete_attempt(change: dict[str, object]) -> None:
    """Reject a valid-looking result from another job, scope, or dispatch attempt."""
    request = operation_samples.command_request()
    response = CommandCanceled(binding=request.binding.model_copy(update=change))
    with pytest.raises(ExtensionContractError, match="accepted job attempt"):
        command_results.validate_command_response(request.binding, response)


def test_command_result_checks_document_schema() -> None:
    """Do not store a successful result with invalid encoded content."""
    manifest = operation_samples.manifest()
    request = operation_samples.command_request("42")
    response = CommandSucceeded(binding=request.binding, document=request.arguments)
    with pytest.raises(ExtensionContractError):
        command_results.validate_command_documents(manifest, SchemaSet(manifest.schemas), response)


def test_unknown_receipt_cannot_claim_a_peer() -> None:
    """Do not persist unowned reconciliation data as this package's state."""
    manifest = operation_samples.manifest()
    response = CommandOutcomeUnknown(
        binding=operation_samples.command_request().binding, diagnostic=DIAGNOSTIC, receipt=samples.encoded_document(),
    )
    with pytest.raises(ExtensionContractError):
        command_results.validate_command_documents(manifest, SchemaSet(manifest.schemas), response)


@pytest.mark.parametrize("status", ["requested", "canceled", "not_running", "outcome_unknown"])
def test_cancel_acknowledgment_keeps_its_state(status: str) -> None:
    """A requested stop is not converted to a canceled job result."""
    request = CommandCancelRequest(binding=operation_samples.command_request().binding, reason="Stop")
    response = CommandCancelResult.model_validate({"binding": request.binding, "status": status})
    assert command_results.validate_cancel_response(request, response).status == status


def test_cancel_acknowledgment_checks_the_attempt() -> None:
    """Do not stop the displayed state of a different job or later attempt."""
    request = CommandCancelRequest(binding=operation_samples.command_request().binding, reason="Stop")
    changed = request.binding.model_copy(update={"call_id": "other"})
    response = CommandCancelResult(binding=changed, status="canceled")
    with pytest.raises(ExtensionContractError, match="requested job attempt"):
        command_results.validate_cancel_response(request, response)


def test_outcome_variants_reject_invalid_fields() -> None:
    """Keep a result document out of the uncertain outcome variant."""
    request = operation_samples.command_request()
    with pytest.raises(ValidationError):
        TypeAdapter[CommandResult](CommandResult).validate_python({
            "binding": request.binding, "status": "outcome_unknown", "document": request.arguments,
        })
