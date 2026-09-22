# Copyright (c) 2026 Zhambyl Yermagambet
"""Protect peer ownership and require registrations for new extension writes."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.projection_changes import (
    ExtensionEntryChange,
    ExtensionRecordChange,
    ProjectionChange,
)
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet


def validate_extension_replacement(original: ProjectionChange, replacement: ProjectionChange) -> None:
    """Preserve peer identity and exact schemas while permitting valid content changes."""
    if isinstance(original, ExtensionEntryChange) and isinstance(replacement, ExtensionEntryChange):
        _require_entry_origin(original, replacement)
    elif isinstance(original, ExtensionRecordChange) and isinstance(replacement, ExtensionRecordChange):
        _require_record_origin(original, replacement)


def validate_extension_addition(
    manifest: ExtensionManifest, schemas: SchemaSet, addition: ProjectionChange,
) -> None:
    """Require newly inserted extension rows to belong to the current package."""
    if isinstance(addition, ExtensionEntryChange):
        _require_owner(manifest, addition.owner)
        definition = lookup.entry_definition(manifest, addition.entry.entry_type, addition.scope.kind)
        documents.require_document_schema(addition.entry.document, definition.schema_ref, schemas, "inserted feed row")
    elif isinstance(addition, ExtensionRecordChange):
        _require_owner(manifest, addition.write.key.owner)
        definition = lookup.collection_definition(
            manifest, addition.write.key.collection, addition.write.key.scope.kind,
        )
        if isinstance(addition.write, PutRecord):
            documents.require_document_schema(
                addition.write.document, definition.schema_ref, schemas, "inserted record",
            )


def _require_entry_origin(expected: ExtensionEntryChange, actual: ExtensionEntryChange) -> None:
    expected_origin = (
        expected.owner, expected.scope, expected.entry.source_event_id, expected.entry.entry_key,
        expected.entry.entry_type, expected.entry.document.schema_ref,
    )
    actual_origin = (
        actual.owner, actual.scope, actual.entry.source_event_id, actual.entry.entry_key,
        actual.entry.entry_type, actual.entry.document.schema_ref,
    )
    if expected_origin != actual_origin:
        message = "projection replacement cannot change extension feed ownership, identity, or schema"
        raise ExtensionContractError(message)


def _require_record_origin(expected: ExtensionRecordChange, actual: ExtensionRecordChange) -> None:
    expected_identity = expected.write.key, expected.write.expected_revision
    actual_identity = actual.write.key, actual.write.expected_revision
    if expected_identity != actual_identity:
        message = "projection replacement cannot change record identity or expected revision"
        raise ExtensionContractError(message)
    if (
        isinstance(expected.write, PutRecord) and isinstance(actual.write, PutRecord)
        and expected.write.document.schema_ref != actual.write.document.schema_ref
    ):
        message = "projection replacement cannot change the record schema"
        raise ExtensionContractError(message)


def _require_owner(manifest: ExtensionManifest, owner: str) -> None:
    if owner != manifest.extension_id:
        message = "projection additions must belong to the selected extension"
        raise ExtensionContractError(message)
