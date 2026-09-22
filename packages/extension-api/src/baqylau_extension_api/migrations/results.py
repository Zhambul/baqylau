# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject partial, stale, or invalid migration output before candidate storage."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.migrations import common
from baqylau_extension_api.models.migration_results import (
    RecordMigrationReady,
    RecordMigrationResult,
    SettingsMigrationReady,
    SettingsMigrationResult,
)
from baqylau_extension_api.models.migrations import RecordMigrationRequest, SettingsMigrationRequest
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.records import StoredRecord
from baqylau_extension_api.schemas import SchemaSet


def validate_settings_result(
    request: SettingsMigrationRequest, response: SettingsMigrationResult,
) -> SettingsMigrationResult:
    """Validate exact candidate identity and the unchanged source revision.

    Returns:
        A complete candidate or an explicit failure, never a storage commit.

    Raises:
        ExtensionContractError: If the result changes its captured revision.

    """
    captured = SettingsMigrationRequest.model_validate(request)
    checked = TypeAdapter[SettingsMigrationResult](SettingsMigrationResult).validate_python(response)
    common.require_encoded_limit(checked, common.MAX_MIGRATION_RESULT_BYTES)
    common.require_migration_binding(captured.binding, checked.binding)
    if checked.source_revision != captured.source_revision:
        message = "settings migration changed its captured revision"
        raise ExtensionContractError(message)
    if isinstance(checked, SettingsMigrationReady) and checked.document.schema_ref != captured.target_schema:
        message = "settings migration result changed its target schema"
        raise ExtensionContractError(message)
    return checked


def validate_records_result(request: RecordMigrationRequest, response: RecordMigrationResult) -> RecordMigrationResult:
    """Require one exact replacement for each captured row in stable order.

    Returns:
        A complete checked batch or an explicit failure with no partial rows.

    Raises:
        ExtensionContractError: If the source snapshot changes.

    """
    captured = RecordMigrationRequest.model_validate(request)
    checked = TypeAdapter[RecordMigrationResult](RecordMigrationResult).validate_python(response)
    common.require_encoded_limit(checked, common.MAX_MIGRATION_RESULT_BYTES)
    common.require_migration_binding(captured.binding, checked.binding)
    if checked.source_snapshot != captured.source_snapshot:
        message = "record migration changed its captured snapshot"
        raise ExtensionContractError(message)
    if isinstance(checked, RecordMigrationReady):
        _require_complete_records(captured, checked)
    return checked


def validate_migrated_documents(
    schemas: SchemaSet, response: SettingsMigrationResult | RecordMigrationResult,
) -> None:
    """Validate all candidate documents before accepting any part of the result."""
    if isinstance(response, SettingsMigrationReady):
        schemas.validate(response.document)
    elif isinstance(response, RecordMigrationReady):
        for record in response.records:
            schemas.validate(record.document)


def _require_complete_records(request: RecordMigrationRequest, response: RecordMigrationReady) -> None:
    if len(request.records) != len(response.records):
        message = "record migration must replace every captured row exactly once"
        raise ExtensionContractError(message)
    for original, replacement in zip(request.records, response.records, strict=True):
        _require_captured_identity(original, replacement)
        if replacement.document.schema_ref != request.target_schema:
            message = "record migration result changed its target schema"
            raise ExtensionContractError(message)


def _require_captured_identity(original: StoredRecord, replacement: PutRecord) -> None:
    if replacement.key != original.key or replacement.expected_revision != original.revision:
        message = "record migration changed a captured key, order, or revision"
        raise ExtensionContractError(message)
