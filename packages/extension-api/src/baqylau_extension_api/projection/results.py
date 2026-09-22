# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate ordered feed rows and record writes without applying partial output."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.projections import MAX_PROJECTION_ROWS, ProjectionRequest, ProjectionResult
from baqylau_extension_api.operations import documents
from baqylau_extension_api.projection import boundaries, records
from baqylau_extension_api.schemas import SchemaSet

MAX_PROJECTION_RESPONSE_BYTES = 4_194_304


def validate_projection_result(request: ProjectionRequest, response: ProjectionResult) -> ProjectionResult:
    """Reject any stale or partial proposal before accepting entries or records.

    Returns:
        The complete result, with its entry order unchanged.

    Raises:
        ExtensionContractError: If the processing binding or output bounds differ.

    """
    checked_request = boundaries.validate_request_boundary(request)
    checked = ProjectionResult.model_validate(response)
    if checked.binding != checked_request.binding:
        message = "projection result must keep its exact processing and snapshot binding"
        raise ExtensionContractError(message)
    if len(checked.entries) + len(checked.record_changes) > MAX_PROJECTION_ROWS:
        message = "projection result exceeds its total row limit"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_PROJECTION_RESPONSE_BYTES:
        message = "projection result exceeds its encoded size limit"
        raise ExtensionContractError(message)
    _validate_entries(checked_request, checked)
    records.validate_record_changes(checked_request, checked)
    return checked


def validate_projection_documents(manifest: ExtensionManifest, schemas: SchemaSet, response: ProjectionResult) -> None:
    """Require exact registered entry and record schemas for the whole proposal."""
    for entry in response.entries:
        definition = lookup.entry_definition(manifest, entry.entry_type, response.binding.context.scope.kind)
        documents.require_document_schema(entry.document, definition.schema_ref, schemas, "projected entry")
    records.validate_record_documents(manifest, schemas, response)


def _validate_entries(request: ProjectionRequest, response: ProjectionResult) -> None:
    keys = tuple((entry.source_event_id, entry.entry_key) for entry in response.entries)
    if len(set(keys)) != len(keys):
        message = "projected entry keys must be unique per source event"
        raise ExtensionContractError(message)
    causes = {stored.fact.event_id for stored in request.events}
    if any(entry.source_event_id not in causes for entry in response.entries):
        message = "projected entry must refer to a fact in this request"
        raise ExtensionContractError(message)
