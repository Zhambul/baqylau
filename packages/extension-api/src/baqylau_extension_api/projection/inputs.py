# Copyright (c) 2026 Zhambyl Yermagambet
"""Check projector registration and captured documents without live reads."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.data import ProcessingSelection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.projections import ProjectionRequest, ProjectionSelectionRequest
from baqylau_extension_api.operations import documents
from baqylau_extension_api.projection import boundaries, records
from baqylau_extension_api.schemas import SchemaSet


def validate_projection_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ProjectionRequest,
) -> ProjectionRequest:
    """Require selected fact types and owned record state before a feature call.

    Returns:
        The complete checked request.

    """
    checked = boundaries.validate_request_boundary(request)
    _validate_selection(manifest, schemas, checked)
    for record in checked.prior_records:
        records.validate_prior_record(manifest, schemas, record)
    return checked


def validate_projection_selection(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ProjectionSelectionRequest,
) -> ProjectionSelectionRequest:
    """Apply the same registration and schema rules before selecting record keys.

    Returns:
        A complete checked fact selection.

    """
    checked = boundaries.validate_selection_boundary(request)
    _validate_selection(manifest, schemas, checked)
    return checked


def _validate_selection(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ProjectionSelectionRequest,
) -> None:
    selection = _selection(manifest, request)
    documents.validate_settings(manifest, request.binding.context.settings, schemas)
    for stored in request.events:
        if _fact_type(stored.fact) not in selection.input_types:
            message = "projection fact type is not selected by this projector"
            raise ExtensionContractError(message)
        if isinstance(stored.fact, ExtensionFact):
            schemas.validate(stored.fact.document)


def _selection(manifest: ExtensionManifest, request: ProjectionSelectionRequest) -> ProcessingSelection:
    context = request.binding.context
    if context.extension_id != manifest.extension_id or "projector" not in manifest.capabilities:
        message = "projection context does not match a declared projector"
        raise ExtensionContractError(message)
    for selection in manifest.contributions.processing:
        if selection.capability == "projector" and context.scope.kind in selection.scopes:
            return selection
    message = "projector scope is not declared"
    raise ExtensionContractError(message)


def _fact_type(fact: CanonicalFact) -> str:
    return fact.payload.kind if isinstance(fact, CoreFact) else fact.event_type
