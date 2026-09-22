# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate record schemas and expected revisions as one complete proposal."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.projections import ProjectionRequest, ProjectionResult
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.records import DeletedRecord, MissingRecord, RecordKey, RecordState, StoredRecord
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet


def validate_prior_record(manifest: ExtensionManifest, schemas: SchemaSet, record: RecordState) -> None:
    """Check owned captured rows, including absent keys and deletion markers.

    Raises:
        ExtensionContractError: If a deletion marker uses an unknown schema.

    """
    _require_owner(manifest, record.key)
    definition = lookup.collection_definition(manifest, record.key.collection, record.key.scope.kind)
    if isinstance(record, StoredRecord):
        documents.require_document_schema(record.document, definition.schema_ref, schemas, "prior record")
    elif isinstance(record, DeletedRecord) and record.schema_ref != definition.schema_ref:
        message = "deleted record must keep the registered collection schema"
        raise ExtensionContractError(message)


def validate_record_changes(request: ProjectionRequest, response: ProjectionResult) -> None:
    """Require an explicit captured key and exact revision for every write.

    Raises:
        ExtensionContractError: If keys repeat, are absent, or have a stale revision.

    """
    prior = {record.key: record for record in request.prior_records}
    keys = tuple(change.key for change in response.record_changes)
    if len(set(keys)) != len(keys):
        message = "projection changes must name each record key at most once"
        raise ExtensionContractError(message)
    for change in response.record_changes:
        captured = prior.get(change.key)
        _require_revision(captured, change.expected_revision)
        if not isinstance(change, PutRecord) and isinstance(captured, (MissingRecord, DeletedRecord)):
            message = "record deletion requires a stored row"
            raise ExtensionContractError(message)


def validate_record_documents(manifest: ExtensionManifest, schemas: SchemaSet, response: ProjectionResult) -> None:
    """Check every proposed collection schema before the host accepts any change."""
    for change in response.record_changes:
        _require_owner(manifest, change.key)
        definition = lookup.collection_definition(manifest, change.key.collection, change.key.scope.kind)
        if isinstance(change, PutRecord):
            documents.require_document_schema(change.document, definition.schema_ref, schemas, "projected record")


def _require_owner(manifest: ExtensionManifest, key: RecordKey) -> None:
    if key.owner != manifest.extension_id:
        message = "projector records must belong to the selected extension"
        raise ExtensionContractError(message)


def _require_revision(captured: RecordState | None, expected: int) -> None:
    if captured is None or captured.revision != expected:
        message = "record change requires a captured key with the exact expected revision"
        raise ExtensionContractError(message)
