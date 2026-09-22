# Copyright (c) 2026 Zhambyl Yermagambet
"""Check captured settings and record input before a pure migration runs."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup, rules
from baqylau_extension_api.manifest.migrations import RecordMigrationPath, SettingsMigrationPath
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.migrations import common
from baqylau_extension_api.models.migrations import RecordMigrationRequest, SettingsMigrationRequest
from baqylau_extension_api.models.records import StoredRecord
from baqylau_extension_api.operations.documents import require_document_schema
from baqylau_extension_api.schemas import SchemaSet


def validate_settings_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: SettingsMigrationRequest,
) -> SettingsMigrationRequest:
    """Require a declared path and a complete captured settings document.

    Returns:
        Immutable settings input with no credential service field.

    Raises:
        ExtensionContractError: If the settings scope or target is not registered.

    """
    checked = SettingsMigrationRequest.model_validate(request)
    common.require_encoded_limit(checked, common.MAX_MIGRATION_REQUEST_BYTES)
    common.require_declared_path(manifest, checked.binding, SettingsMigrationPath(
        source_schema=checked.source_schema, target_schema=checked.target_schema,
    ))
    definition = manifest.settings
    if (
        definition is None or checked.binding.scope.kind not in definition.scopes
        or checked.target_schema != definition.defaults.schema_ref
    ):
        message = "settings migration scope or target is not declared"
        raise ExtensionContractError(message)
    schemas.definition(checked.target_schema)
    require_document_schema(checked.source, checked.source_schema, schemas, "migration source settings")
    return checked


def validate_records_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: RecordMigrationRequest,
) -> RecordMigrationRequest:
    """Require an owned collection and a complete, bounded source snapshot.

    Returns:
        Immutable records that remain unchanged until candidate acceptance.

    """
    checked = RecordMigrationRequest.model_validate(request)
    common.require_encoded_limit(checked, common.MAX_MIGRATION_REQUEST_BYTES)
    common.require_declared_path(manifest, checked.binding, RecordMigrationPath(
        source_schema=checked.source_schema, target_schema=checked.target_schema, collection=checked.collection,
    ))
    _require_record_target(manifest, schemas, checked)
    rules.require_unique((record.key for record in checked.records), "migration record keys")
    for record in checked.records:
        _require_captured_record(checked, record)
        require_document_schema(record.document, checked.source_schema, schemas, "migration source record")
    return checked


def _require_record_target(manifest: ExtensionManifest, schemas: SchemaSet, request: RecordMigrationRequest) -> None:
    definition = lookup.collection_definition(manifest, request.collection, request.binding.scope.kind)
    if definition.schema_ref != request.target_schema:
        message = "record migration target is not the current collection schema"
        raise ExtensionContractError(message)
    if request.source_snapshot.scope != request.binding.scope:
        message = "record migration snapshot changed scope"
        raise ExtensionContractError(message)
    schemas.definition(request.target_schema)


def _require_captured_record(request: RecordMigrationRequest, record: StoredRecord) -> None:
    if (
        record.key.owner != request.binding.extension_id or record.key.collection != request.collection
        or record.key.scope != request.binding.scope
    ):
        message = "migration source record changed owner, collection, or scope"
        raise ExtensionContractError(message)
    if record.revision > request.source_snapshot.commit_cursor:
        message = "migration source record is newer than its captured snapshot"
        raise ExtensionContractError(message)
