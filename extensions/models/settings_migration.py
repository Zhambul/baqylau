# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture exact raw settings which need a declared candidate conversion."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.migrations import SettingsMigrationPath
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import SchemaRef
from baqylau_extension_api.models.scopes import InstallationScope
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.registry import ScopedRuntimeSettings
from extensions.models.settings import SettingsOverrides


def explicit_settings(overrides: SettingsOverrides) -> tuple[ScopedRuntimeSettings, ...]:
    """Keep absent defaults absent when enumerating the user's saved choices.

    Returns:
        Installation first, if explicit, followed by the exact saved scopes.

    """
    installation = () if overrides.installation is None else (
        ScopedRuntimeSettings(scope=InstallationScope(), settings=overrides.installation),
    )
    return (*installation, *overrides.scopes)


def needs_settings_migration(manifest: ExtensionManifest, source: SettingsOverrides) -> bool:
    """Select conversion by exact schema identity, not a package version comparison.

    Returns:
        True when at least one explicit value uses another declared schema.

    """
    return manifest.settings is not None and any(
        entry.settings.schema_ref != manifest.settings.defaults.schema_ref for entry in explicit_settings(source)
    )


def validate_migration_source(manifest: ExtensionManifest, source: SettingsOverrides, schemas: SchemaSet) -> None:
    """Check every source value and conversion path before worker preparation.

    Raises:
        ExtensionContractError: If a scope, source document, or exact path is invalid.

    """
    definition = manifest.settings
    if definition is None or manifest.backend is None or not needs_settings_migration(manifest, source):
        message = "settings migration requires a backend and changed explicit schemas"
        raise ExtensionContractError(message)
    for entry in explicit_settings(source):
        if entry.scope.kind not in definition.scopes:
            message = "settings migration cannot remove a saved scope"
            raise ExtensionContractError(message)
        schemas.validate(entry.settings)
        if entry.settings.schema_ref != definition.defaults.schema_ref:
            _require_path(manifest, entry.settings.schema_ref, definition.defaults.schema_ref)


def _require_path(manifest: ExtensionManifest, source: SchemaRef, target: SchemaRef) -> None:
    path = SettingsMigrationPath(source_schema=source, target_schema=target)
    if "migrations" not in manifest.capabilities or path not in manifest.migration_paths:
        message = "the exact settings migration path is not declared"
        raise ExtensionContractError(message)
