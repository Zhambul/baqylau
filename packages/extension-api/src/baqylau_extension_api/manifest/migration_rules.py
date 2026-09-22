# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate declared migration paths without importing a package backend."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.migrations import RecordMigrationPath, SettingsMigrationPath
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.schemas import SchemaSet


def validate_migration_paths(manifest: ExtensionManifest, schemas: SchemaSet) -> None:
    """Require exact owned schemas and a current declared target for every path.

    Raises:
        ExtensionContractError: If a path changes owner or does not change schema.

    """
    rules.require_unique(manifest.migration_paths, "migration paths")
    for path in manifest.migration_paths:
        if path.source_schema.owner != manifest.extension_id or path.target_schema.owner != manifest.extension_id:
            message = "migration schemas must belong to the selected extension"
            raise ExtensionContractError(message)
        if path.source_schema == path.target_schema:
            message = "a migration path must select a different target schema"
            raise ExtensionContractError(message)
        schemas.definition(path.source_schema)
        schemas.definition(path.target_schema)
        _require_current_target(manifest, path)


def _require_current_target(
    manifest: ExtensionManifest, path: SettingsMigrationPath | RecordMigrationPath,
) -> None:
    if isinstance(path, SettingsMigrationPath):
        _require_settings_target(manifest, path)
    else:
        _require_record_target(manifest, path)


def _require_settings_target(manifest: ExtensionManifest, path: SettingsMigrationPath) -> None:
    if manifest.settings is None or path.target_schema != manifest.settings.defaults.schema_ref:
        message = "migration target is not the current settings schema"
        raise ExtensionContractError(message)


def _require_record_target(manifest: ExtensionManifest, path: RecordMigrationPath) -> None:
    if any(
        declaration.name == path.collection and declaration.schema_ref == path.target_schema
        for declaration in manifest.contributions.collections
    ):
        return
    message = "migration target is not the current collection schema"
    raise ExtensionContractError(message)
