# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid observer jobs before any live feature call."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.observer_jobs import ObservationJobRequest, ObservationReconcileRequest
from baqylau_extension_api.observers import requests
from baqylau_extension_api.schemas import SchemaSet
from pydantic import ValidationError

from tests.extension_api import observer_samples as fixtures, operation_samples, samples

SCOPE = "scope"
BODY = "body"
DEADLINE = "deadline"


@pytest.mark.parametrize("encoded", ['"complete"', '""'])
def test_selected_extension_trigger_is_valid(encoded: str) -> None:
    """Empty document text still has a complete declared schema and job binding."""
    request = fixtures.request(encoded)
    manifest = fixtures.manifest()
    assert requests.validate_observation_request(manifest, SchemaSet(manifest.schemas), request) == request


@pytest.mark.parametrize("change", ["owner", "cause", SCOPE, "type", BODY])
def test_invalid_trigger_is_rejected(change: str) -> None:
    """Do not run a different cause, undeclared type, or schema-invalid document."""
    request = fixtures.request("42" if change == BODY else '"complete"')
    if change in {"owner", "cause", SCOPE}:
        changes: dict[str, dict[str, object]] = {
            "owner": {"extension_id": "wrong"}, "cause": {"event_id": "wrong"},
            SCOPE: {SCOPE: fixtures.SESSION_SCOPE},
        }
        binding = request.binding.model_copy(update=changes[change])
        request = request.model_copy(update={"binding": binding})
    if change == "type":
        fact = request.event.fact.model_copy(update={"event_type": "test.sample.other"})
        event = request.event.model_copy(update={"fact": fact})
        request = request.model_copy(update={"event": event})
    with pytest.raises((ExtensionContractError, ValidationError)):
        _validate(request)


@pytest.mark.parametrize(("field", "invalid"), [
    ("mode", "replay"),
    ("settings_revision", True), ("settings_revision", -1), ("settings_revision", "1"),
    (DEADLINE, 0), (DEADLINE, float("inf")),
    (DEADLINE, float("nan")), (DEADLINE, "10"),
])
def test_observer_request_fields_are_strict(field: str, invalid: object) -> None:
    """Reject replay, invalid revisions, and nonfinite or coerced deadlines."""
    with pytest.raises(ValidationError):
        ObservationJobRequest.model_validate(fixtures.request().model_copy(update={field: invalid}))


def test_uncommitted_trigger_is_rejected() -> None:
    """A zero cursor cannot masquerade as a committed fact."""
    request = fixtures.request()
    with pytest.raises(ValidationError):
        ObservationJobRequest.model_validate(request.model_copy(update={
            "event": request.event.model_copy(update={"cursor": 0}),
        }))


def test_observer_write_requires_expected_state() -> None:
    """A declaration does not waive write preconditions or host read-only policy."""
    manifest = fixtures.manifest(effect="write")
    schemas = SchemaSet(manifest.schemas)
    with pytest.raises(ExtensionContractError, match="expected state"):
        requests.validate_observation_request(manifest, schemas, fixtures.request())
    request = fixtures.request().model_copy(update={"expected_state_revision": "state-1"})
    assert requests.validate_observation_request(manifest, schemas, request) == request


@pytest.mark.parametrize("change", ["undeclared", "foreign", BODY])
def test_recovery_requires_owned_proof(change: str) -> None:
    """Recovery needs an explicit declaration and a valid owned receipt."""
    manifest = fixtures.manifest(reconciliation=change != "undeclared")
    receipt = operation_samples.query_request("42" if change == BODY else '"complete"').arguments
    if change == "foreign":
        receipt = receipt.model_copy(update={"schema_ref": samples.schema_definition().reference})
    request = ObservationReconcileRequest(observation=fixtures.request(), receipt=receipt)
    with pytest.raises(ExtensionContractError):
        requests.validate_observation_reconcile(manifest, SchemaSet(manifest.schemas), request)


def _validate(request: ObservationJobRequest) -> ObservationJobRequest:
    manifest = fixtures.manifest()
    return requests.validate_observation_request(manifest, SchemaSet(manifest.schemas), request)
