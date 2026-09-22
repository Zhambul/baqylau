# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exact observer outcomes before the host accepts any new records."""

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.observer_jobs import ObservationCancelRequest, ObservationCancelResult
from baqylau_extension_api.models.observer_results import (
    ObservationCanceled,
    ObservationFailed,
    ObservationJobResult,
    ObservationOutcomeUnknown,
    ObservationSucceeded,
)
from baqylau_extension_api.observers import results
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import observer_samples as fixtures, operation_samples, samples

if TYPE_CHECKING:
    from baqylau_extension_api.models.observations import ObservationCandidate

DIAGNOSTIC = Diagnostic(code="fixture.failure", message="The fixture result is not complete.")


@pytest.mark.parametrize("response", [
    fixtures.succeeded(), ObservationSucceeded(binding=fixtures.request().binding),
    ObservationCanceled(binding=fixtures.request().binding),
    ObservationFailed(binding=fixtures.request().binding, diagnostic=DIAGNOSTIC),
    ObservationOutcomeUnknown(binding=fixtures.request().binding, diagnostic=DIAGNOSTIC),
])
def test_observer_outcomes_remain_distinct(response: ObservationJobResult) -> None:
    """Zero output is valid and does not collapse failed or unknown outcomes."""
    assert results.validate_observation_result(fixtures.request().binding, response) == response
    manifest = fixtures.manifest()
    results.validate_observation_documents(manifest, SchemaSet(manifest.schemas), response)


@pytest.mark.parametrize("field", [
    "extension_id", "runtime_revision", "history_revision", "job_id", "call_id", "event_id",
])
def test_changed_attempt_binding_is_rejected(field: str) -> None:
    """A response cannot claim a different owner, history, trigger, or attempt."""
    response = fixtures.succeeded()
    binding = response.binding.model_copy(update={field: "changed"})
    with pytest.raises(ExtensionContractError, match="accepted job attempt"):
        results.validate_observation_result(response.binding, response.model_copy(update={"binding": binding}))


def test_output_requires_its_trigger_as_cause() -> None:
    """An observer cannot hide the trigger link needed for host cycle checks."""
    response = fixtures.succeeded().model_copy(update={"observations": (operation_samples.observation(),)})
    with pytest.raises(ExtensionContractError, match="cause"):
        results.validate_observation_result(response.binding, response)


@pytest.mark.parametrize("change", ["duplicate", "scope", "type", "schema", "body"])
def test_invalid_output_is_rejected_as_a_set(change: str) -> None:
    """Schema and ownership checks apply to all proposed original observations."""
    response = fixtures.succeeded()
    source = response.observations[0]
    changes: dict[str, dict[str, object]] = {
        "scope": {"scope": fixtures.SESSION_SCOPE}, "type": {"source_type": "test.sample.other"},
        "schema": {"document": source.document.model_copy(update={
            "schema_ref": samples.schema_definition().reference,
        })},
        "body": {"document": source.document.model_copy(update={"json_text": "42"})}, "duplicate": {},
    }
    observations: tuple[ObservationCandidate, ...] = (source.model_copy(update=changes[change]),)
    if change == "duplicate":
        observations = (source, source)
    manifest = fixtures.manifest()
    with pytest.raises(ExtensionContractError):
        results.validate_observation_documents(
            manifest, SchemaSet(manifest.schemas), response.model_copy(update={"observations": observations}),
        )


def test_observer_cancel_keeps_exact_attempt() -> None:
    """A valid stop status for another attempt cannot finish this job."""
    request = ObservationCancelRequest(binding=fixtures.request().binding, reason="Stop this attempt.")
    response = ObservationCancelResult(binding=request.binding, status="requested")
    assert results.validate_observation_cancel_result(request, response) == response
    binding = request.binding.model_copy(update={"call_id": "old"})
    with pytest.raises(ExtensionContractError):
        results.validate_observation_cancel_result(request, response.model_copy(update={"binding": binding}))
