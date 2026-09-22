# Copyright (c) 2026 Zhambyl Yermagambet
"""Check captured peer and owned record schemas without private collection access."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import SchemaRef
from baqylau_extension_api.models.projection_changes import ExtensionRecordChange, ProjectionChange
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest
from baqylau_extension_api.models.projections import ProjectionRequest, ProjectionResult
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.records import DeletedRecord, RecordState, StoredRecord
from baqylau_extension_api.operations import documents
from baqylau_extension_api.projection.records import validate_record_changes
from baqylau_extension_api.schemas import SchemaSet


def validate_record_snapshot(request: ProjectionTransformRequest, schemas: SchemaSet) -> None:
    """Check all captured schemas, including peer rows and deletion markers."""
    for record in request.prior_records:
        reference = record_schema(record)
        if reference is not None:
            _require_owner(reference, record.key.owner)
            schemas.definition(reference)
        if isinstance(record, StoredRecord):
            schemas.validate(record.document)


def validate_record_proposals(
    request: ProjectionTransformRequest, changes: tuple[ProjectionChange, ...], schemas: SchemaSet,
) -> None:
    """Apply the same revision and deletion rules used by extension projectors."""
    selected = ProjectionRequest(binding=request.binding, events=request.events, prior_records=request.prior_records)
    proposed = ProjectionResult(binding=request.binding, record_changes=tuple(
        change.write for change in changes if isinstance(change, ExtensionRecordChange)
    ))
    validate_record_changes(selected, proposed)
    for write in proposed.record_changes:
        if isinstance(write, PutRecord):
            _require_owner(write.document.schema_ref, write.key.owner)
            captured = next(record for record in request.prior_records if record.key == write.key)
            reference = record_schema(captured) or write.document.schema_ref
            documents.require_document_schema(write.document, reference, schemas, "projection record")


def record_schema(record: RecordState) -> SchemaRef | None:
    """Read a captured schema without treating absence as a deleted row.

    Returns:
        The stored schema, or no schema for a key that has never existed.

    """
    if isinstance(record, StoredRecord):
        return record.document.schema_ref
    return record.schema_ref if isinstance(record, DeletedRecord) else None


def _require_owner(reference: SchemaRef, owner: str) -> None:
    if reference.owner != owner:
        message = "projection record schema must keep the record owner"
        raise ExtensionContractError(message)
