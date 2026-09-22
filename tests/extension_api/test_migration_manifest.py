# Copyright (c) 2026 Zhambyl Yermagambet
"""Require exact migration declarations, including explicit reverse paths."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.migrations import SettingsMigrationPath
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.migrations.requests import validate_settings_request
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import migration_samples as fixtures


@pytest.mark.parametrize("downgrade", [False, True])
def test_declared_schema_direction_is_valid(*, downgrade: bool) -> None:
    """Both directions are valid only when the package declares the exact path."""
    manifest = fixtures.manifest(downgrade=downgrade)
    assert validate_manifest(manifest) == manifest


def test_duplicate_paths_are_rejected() -> None:
    """One declaration cannot be registered twice under the same schema identity."""
    manifest = fixtures.manifest()
    invalid = manifest.model_copy(update={
        "migration_paths": (*manifest.migration_paths, manifest.migration_paths[0]),
    })
    with pytest.raises(ExtensionContractError, match="migration paths"):
        validate_manifest(invalid)


@pytest.mark.parametrize("change", ["owner", "target", "same", "missing"])
def test_invalid_path_schema_is_rejected(change: str) -> None:
    """Reject foreign, unavailable, unchanged, and noncurrent schema targets."""
    manifest = fixtures.manifest()
    source = fixtures.schema(1).reference
    target = fixtures.schema(2).reference
    changes = {
        "owner": source.model_copy(update={"owner": "peer"}),
        "target": source, "same": target, "missing": source.model_copy(update={"version": 99}),
    }
    path = SettingsMigrationPath(source_schema=changes[change], target_schema=target)
    if change == "target":
        path = SettingsMigrationPath(source_schema=target, target_schema=source)
    with pytest.raises(ExtensionContractError):
        validate_manifest(manifest.model_copy(update={"migration_paths": (path,)}))


def test_path_requires_migration_capability() -> None:
    """A declared conversion cannot dispatch an absent runtime capability."""
    manifest = fixtures.manifest().model_copy(update={"capabilities": ("lifecycle",)})
    with pytest.raises(ExtensionContractError, match="capability declarations"):
        validate_manifest(manifest)


def test_migration_capability_requires_paths() -> None:
    """A generic migration flag does not permit unspecified conversions."""
    manifest = fixtures.manifest().model_copy(update={"migration_paths": ()})
    with pytest.raises(ExtensionContractError, match="capability declarations"):
        validate_manifest(manifest)


def test_undeclared_downgrade_is_rejected() -> None:
    """Do not infer a reverse conversion from a supported forward conversion."""
    manifest = fixtures.manifest()
    request = fixtures.settings_request().model_copy(update={
        "source_schema": fixtures.schema(2).reference, "target_schema": fixtures.schema(1).reference,
        "source": fixtures.document(2, '{"title":"Before"}'),
    })
    with pytest.raises(ExtensionContractError, match="exact migration path"):
        validate_settings_request(manifest, SchemaSet(manifest.schemas), request)
