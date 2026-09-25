# Copyright (c) 2026 Zhambyl Yermagambet
"""Check committed input, declarations, and write preconditions for observers."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.canonical import CoreFact, fact_type
from baqylau_extension_api.models.observer_jobs import (
    ObservationCancelRequest,
    ObservationJobRequest,
    ObservationReconcileRequest,
)
from baqylau_extension_api.observers.registration import observer_selection
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet

MAX_OBSERVER_REQUEST_BYTES = 8_388_608


def validate_observation_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ObservationJobRequest,
) -> ObservationJobRequest:
    """Check a live captured trigger before executing any feature work.

    Returns:
        The immutable request; acceptance and current state remain host checks.

    Raises:
        ExtensionContractError: If the type, write precondition, or size is invalid.

    """
    checked = ObservationJobRequest.model_validate(request)
    selection = observer_selection(manifest, checked.binding)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_OBSERVER_REQUEST_BYTES:
        message = "observer request exceeds its encoded size limit"
        raise ExtensionContractError(message)
    _validate_trigger(checked, schemas)
    if fact_type(checked.event.fact) not in selection.input_types:
        message = "observer fact type is not selected"
        raise ExtensionContractError(message)
    documents.validate_settings(manifest, checked.settings, schemas)
    if selection.effect == "write" and checked.expected_state_revision is None:
        message = "write observers require an expected state revision"
        raise ExtensionContractError(message)
    return checked


def validate_observation_cancel(
    manifest: ExtensionManifest, request: ObservationCancelRequest,
) -> ObservationCancelRequest:
    """Check the declaration before asking the feature to stop an attempt.

    Returns:
        The checked request; it does not prove that the job is running.

    """
    checked = ObservationCancelRequest.model_validate(request)
    observer_selection(manifest, checked.binding)
    return checked


def validate_observation_reconcile(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ObservationReconcileRequest,
) -> ObservationReconcileRequest:
    """Validate recovery evidence without dispatching observe.

    Returns:
        The checked original trigger and optional owned receipt.

    Raises:
        ExtensionContractError: If recovery is not declared or the message is too large.

    """
    checked = ObservationReconcileRequest.model_validate(request)
    validate_observation_request(manifest, schemas, checked.observation)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_OBSERVER_REQUEST_BYTES:
        message = "observer recovery request exceeds its encoded size limit"
        raise ExtensionContractError(message)
    if not observer_selection(manifest, checked.observation.binding).reconciliation:
        message = "observer reconciliation is not declared"
        raise ExtensionContractError(message)
    if checked.receipt is not None:
        documents.validate_owned_document(checked.receipt, manifest.extension_id, schemas)
    return checked


def _validate_trigger(request: ObservationJobRequest, schemas: SchemaSet) -> None:
    fact = request.event.fact
    if fact.event_id != request.binding.event_id or fact.scope != request.binding.scope:
        message = "observer trigger changed its accepted cause or scope"
        raise ExtensionContractError(message)
    if not isinstance(fact, CoreFact):
        schemas.validate(fact.document)
