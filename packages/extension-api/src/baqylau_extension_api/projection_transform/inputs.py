# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate captured state and registered projection-transform input."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.data import ProcessingSelection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.canonical import CoreFact
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest
from baqylau_extension_api.models.projections import ProjectionRequest
from baqylau_extension_api.operations import documents
from baqylau_extension_api.projection import boundaries
from baqylau_extension_api.projection_transform import changes, records, state
from baqylau_extension_api.schemas import SchemaSet


def validate_transform_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ProjectionTransformRequest,
) -> ProjectionTransformRequest:
    """Check all captured input without reading live host data.

    Returns:
        A complete checked request for the selected pure capability.

    Raises:
        ExtensionContractError: If the encoded request exceeds its byte bound.

    """
    checked = ProjectionTransformRequest.model_validate(request)
    boundaries.validate_request_boundary(ProjectionRequest(
        binding=checked.binding, events=checked.events, prior_records=checked.prior_records,
    ))
    if len(checked.model_dump_json().encode("utf-8")) > boundaries.MAX_PROJECTION_REQUEST_BYTES:
        message = "projection transform request exceeds its encoded size limit"
        raise ExtensionContractError(message)
    _validate_selection(manifest, checked, schemas)
    documents.validate_settings(manifest, checked.binding.context.settings, schemas)
    state.validate_core_state(checked.binding.context.scope, checked.before_core)
    records.validate_record_snapshot(checked, schemas)
    changes.validate_changes(checked, checked.changes, schemas)
    return checked


def _validate_selection(manifest: ExtensionManifest, request: ProjectionTransformRequest, schemas: SchemaSet) -> None:
    selection = _selection(manifest, request)
    for stored in request.events:
        kind = (
            stored.fact.payload.kind if isinstance(stored.fact, CoreFact)
            else stored.fact.event_type
        )
        if kind not in selection.input_types:
            message = "projection transform fact type is not selected"
            raise ExtensionContractError(message)
        if isinstance(stored.fact, ExtensionFact):
            schemas.validate(stored.fact.document)


def _selection(manifest: ExtensionManifest, request: ProjectionTransformRequest) -> ProcessingSelection:
    context = request.binding.context
    if context.extension_id != manifest.extension_id or "projection_transformer" not in manifest.capabilities:
        message = "projection transform context does not match a declared capability"
        raise ExtensionContractError(message)
    for selection in manifest.contributions.processing:
        if selection.capability == "projection_transformer" and context.scope.kind in selection.scopes:
            return selection
    message = "projection transformer scope is not declared"
    raise ExtensionContractError(message)
