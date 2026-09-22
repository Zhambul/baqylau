# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate new observations before an operation result reaches raw storage."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.models.command_results import CommandSucceeded
from baqylau_extension_api.models.observations import ObservationCandidate
from baqylau_extension_api.operations import command_results, observations
from baqylau_extension_api.schemas import SchemaSet
from pydantic import ValidationError

from tests.extension_api import operation_samples, samples


def test_observations_use_registered_source() -> None:
    """Validate result observations and command output as one complete response."""
    manifest = validate_manifest(operation_samples.source_manifest())
    request = operation_samples.command_request()
    response = CommandSucceeded(
        binding=request.binding, document=request.arguments, observations=(operation_samples.observation(),),
    )
    command_results.validate_command_documents(manifest, SchemaSet(manifest.schemas), response)


@pytest.mark.parametrize("change", [
    {"source_type": "peer.observation"}, {"causes": ("event-1", "event-1")}, {"occurred_at": float("inf")},
])
def test_observation_model_rejects_invalid_data(change: dict[str, object]) -> None:
    """Reject wrong ownership, repeated causes, and non-finite times."""
    with pytest.raises(ValidationError):
        ObservationCandidate.model_validate({**operation_samples.observation().model_dump(), **change})


def test_result_observation_cannot_change_scope() -> None:
    """Require separate authorized source work for an observation in another scope."""
    manifest = operation_samples.source_manifest()
    request = operation_samples.command_request()
    changed = operation_samples.observation().model_copy(update={"scope": samples.SESSION})
    with pytest.raises(ExtensionContractError, match="operation scope"):
        observations.validate_observations(manifest, SchemaSet(manifest.schemas), request.binding.scope, (changed,))


def test_observation_batch_has_unique_source_keys() -> None:
    """Reject two proposed raw records with one scoped source identity and key."""
    manifest = operation_samples.source_manifest()
    request = operation_samples.command_request()
    with pytest.raises(ExtensionContractError, match="source keys must be unique"):
        observations.validate_observations(
            manifest, SchemaSet(manifest.schemas), request.binding.scope, (operation_samples.observation(),) * 2,
        )


def test_observation_source_must_be_declared() -> None:
    """Do not accept a well-typed observation for an unknown source type."""
    manifest = operation_samples.manifest()
    request = operation_samples.command_request()
    with pytest.raises(ExtensionContractError, match="source type is not declared"):
        observations.validate_observations(
            manifest, SchemaSet(manifest.schemas), request.binding.scope, (operation_samples.observation(),),
        )


def test_observation_source_document_is_checked() -> None:
    """Reject malformed source content before writing any batch member."""
    manifest = operation_samples.source_manifest()
    request = operation_samples.command_request()
    changed = operation_samples.observation().model_copy(update={
        "document": operation_samples.query_request("42").arguments,
    })
    with pytest.raises(ExtensionContractError):
        observations.validate_observations(manifest, SchemaSet(manifest.schemas), request.binding.scope, (changed,))
