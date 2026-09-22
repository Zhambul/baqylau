# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate complete observer outcomes before any result observation is stored."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.observer_jobs import (
    ObservationCancelRequest,
    ObservationCancelResult,
    ObservationJobBinding,
)
from baqylau_extension_api.models.observer_results import ObservationJobResult, ObservationOutcomeUnknown
from baqylau_extension_api.observers.registration import observer_selection
from baqylau_extension_api.operations import documents, observations
from baqylau_extension_api.schemas import SchemaSet

MAX_OBSERVER_RESPONSE_BYTES = 4_194_304


def validate_observation_result(binding: ObservationJobBinding, response: ObservationJobResult) -> ObservationJobResult:
    """Keep stale attempts, duplicate output, and missing causes out of storage.

    Returns:
        A bounded success, failure, cancellation, or uncertain outcome.

    Raises:
        ExtensionContractError: If the result changes its binding or output limits.

    """
    checked = TypeAdapter[ObservationJobResult](ObservationJobResult).validate_python(response)
    if checked.binding != ObservationJobBinding.model_validate(binding):
        message = "observer result does not match its accepted job attempt"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_OBSERVER_RESPONSE_BYTES:
        message = "observer response exceeds its encoded size limit"
        raise ExtensionContractError(message)
    rules.require_unique((content.content_id for content in checked.content), "observer content IDs")
    for observation in checked.observations:
        if checked.binding.event_id not in observation.causes:
            message = "observer output must retain its committed trigger as a cause"
            raise ExtensionContractError(message)
    return checked


def validate_observation_documents(
    manifest: ExtensionManifest, schemas: SchemaSet, response: ObservationJobResult,
) -> None:
    """Check owned receipts and the complete observation set before reply."""
    observer_selection(manifest, response.binding)
    if isinstance(response, ObservationOutcomeUnknown) and response.receipt is not None:
        documents.validate_owned_document(response.receipt, manifest.extension_id, schemas)
    observations.validate_observations(manifest, schemas, response.binding.scope, response.observations)


def validate_observation_cancel_result(
    request: ObservationCancelRequest, response: ObservationCancelResult,
) -> ObservationCancelResult:
    """Bind a stop acknowledgment to the exact requested job attempt.

    Returns:
        The checked acknowledgment, not a final durable job state.

    Raises:
        ExtensionContractError: If the acknowledgment changes its binding.

    """
    checked = ObservationCancelResult.model_validate(response)
    if checked.binding != ObservationCancelRequest.model_validate(request).binding:
        message = "observer cancellation does not match its requested job attempt"
        raise ExtensionContractError(message)
    return checked
