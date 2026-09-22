# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source observations and resume progress inside one checked proposal."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.source_results import SourceBatch, SourceReadBinding, SourceReadResult
from baqylau_extension_api.models.sources import SourceReadRequest
from baqylau_extension_api.operations.observations import validate_observations
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources.progress import validate_progress

MAX_SOURCE_BATCH_BYTES = 4_194_304


def source_read_binding(request: SourceReadRequest) -> SourceReadBinding:
    """Select the exact source and position whose read is in flight.

    Returns:
        The complete expected response binding.

    """
    return SourceReadBinding(
        call=request.context.binding, source_identity=request.source.source_identity,
        source_type=request.source.source_type, after_position=request.after_position,
    )


def validate_source_batch(request: SourceReadRequest, response: SourceReadResult) -> SourceReadResult:
    """Reject stale reads, oversized output, and invalid progress before storage.

    Returns:
        A checked batch or a failed read with no progress.

    Raises:
        ExtensionContractError: If response identity or encoded size is invalid.

    """
    checked_request = SourceReadRequest.model_validate(request)
    checked = TypeAdapter[SourceReadResult](SourceReadResult).validate_python(response)
    if checked.binding != source_read_binding(checked_request):
        message = "source read result does not match the selected source and position"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_SOURCE_BATCH_BYTES:
        message = "source batch exceeds its encoded size limit"
        raise ExtensionContractError(message)
    if isinstance(checked, SourceBatch):
        _validate_observation_positions(checked_request, checked)
        validate_progress(checked)
    return checked


def validate_batch_documents(manifest: ExtensionManifest, schemas: SchemaSet, response: SourceReadResult) -> None:
    """Check all original documents and source identities before committing any."""
    if isinstance(response, SourceBatch):
        candidates = tuple(positioned.observation for positioned in response.observations)
        validate_observations(manifest, schemas, response.binding.call.scope, candidates)


def _validate_observation_positions(request: SourceReadRequest, response: SourceBatch) -> None:
    if len(response.observations) > request.limit:
        message = "source batch exceeds its requested limit"
        raise ExtensionContractError(message)
    rules.require_unique((entry.position for entry in response.observations), "source observation positions")
    for entry in response.observations:
        if entry.position == request.after_position:
            message = "source observation repeats the committed position"
            raise ExtensionContractError(message)
        if (entry.observation.source_identity, entry.observation.source_type) != (
            request.source.source_identity, request.source.source_type,
        ):
            message = "source observation changed the selected source identity or type"
            raise ExtensionContractError(message)
