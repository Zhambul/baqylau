# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare raw override changes and their separately captured runtime values."""

from pathlib import Path

from extensions.models.lifecycle_operations import LifecycleFailure, LifecycleOperation, LifecycleProposal
from extensions.models.lifecycle_selection import RuntimePackageSelection, SettingsChange
from extensions.models.lifecycle_state import LifecycleWrite
from extensions.models.settings import SettingsOverrides, capture_settings
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from tests.extension_host import catalog_fixture, lifecycle_fixture as fixtures


def settings_proposal(
    store: SqliteExtensionLifecycleRepository, package: RuntimePackageSelection,
    overrides: SettingsOverrides, operation_id: str = "settings",
) -> LifecycleProposal:
    """Use retained defaults, not the prior captured effective fallback.

    Returns:
        A complete settings operation with matching raw and effective values.

    """
    manifest = catalog_fixture.repository(Path(store.database.path).parent).retained_extension_manifest(
        package.extension_info.package_digest,
    )
    assert manifest is not None
    selected = package.model_copy(update={"settings": capture_settings(manifest, overrides)})
    proposed = fixtures.proposal(store, selected, operation_id)
    return proposed.model_copy(update={"kind": "settings", "settings_changes": (SettingsChange(
        extension_id=package.extension_info.extension_id, package_digest=package.extension_info.package_digest,
        expected_revision=overrides.revision - 1, settings=overrides,
    ),)})


def changed_overrides(package: RuntimePackageSelection) -> SettingsOverrides:
    """Use the package's declared text schema for the first explicit user choice.

    Returns:
        A complete installation override independent of future default changes.

    """
    assert package.settings.default is not None
    return SettingsOverrides(
        revision=1, installation=package.settings.default.model_copy(update={"json_text": '"custom"'}),
    )


def fail_preparation(store: SqliteExtensionLifecycleRepository, operation: LifecycleOperation) -> LifecycleWrite:
    """Record a fixture preparation failure without changing the reserved candidate.

    Returns:
        The stored terminal outcome and unchanged committed selection.

    """
    failed = fixtures.completion(operation).model_copy(update={"failure": LifecycleFailure(
        code="preparation_failed", detail="candidate worker did not become ready",
    )})
    return store.finish_extension_operation(failed)


def invalid_overrides(package: RuntimePackageSelection) -> SettingsOverrides:
    """Break the declared text schema while keeping the host override model valid.

    Returns:
        An override which repository admission must reject.

    """
    overrides = changed_overrides(package)
    assert overrides.installation is not None
    invalid = overrides.installation.model_copy(update={"json_text": "42"})
    return overrides.model_copy(update={"installation": invalid})
