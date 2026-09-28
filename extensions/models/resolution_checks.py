# Copyright (c) 2026 Zhambyl Yermagambet
"""Check that complete migration output resolves its exact source plan."""

from __future__ import annotations

from typing import TYPE_CHECKING

from extensions.models.runtime_candidates import MigratingRuntimePackage
from extensions.models.settings_migration import explicit_settings

if TYPE_CHECKING:
    from extensions.models.lifecycle_selection import RuntimePackageSelection, SettingsChange
    from extensions.models.registry import ScopedRuntimeSettings
    from extensions.models.runtime_candidates import MigratingRuntimeSelection
    from extensions.models.runtime_resolution import RuntimeResolution


def validate_resolution(candidate: MigratingRuntimeSelection, resolution: RuntimeResolution) -> None:
    """Reject changed identities, unaffected packages, or raw override structure."""
    _require(
        "migration resolution changed the runtime selection",
        holds=resolution.runtime.runtime_revision == candidate.runtime_revision
        and resolution.runtime.catalog_revision == candidate.catalog_revision
        and len(resolution.runtime.packages) == len(candidate.packages),
    )
    migrating = tuple(package for package in candidate.packages if isinstance(package, MigratingRuntimePackage))
    _require_owners(migrating, resolution)
    for source, resolved in zip(candidate.packages, resolution.runtime.packages, strict=True):
        if isinstance(source, MigratingRuntimePackage):
            _validate_package(source, resolved, _settings_change(resolution, _owner(source)))
        else:
            _require("migration resolution changed an unaffected package", holds=source == resolved)


def _require_owners(migrating: tuple[MigratingRuntimePackage, ...], resolution: RuntimeResolution) -> None:
    """Require record output for each record source, and settings output for each package without one, in order."""
    record_owners = tuple(_owner(package) for package in migrating if package.records)
    _require(
        "migration resolution changed the record owner set or order",
        holds=tuple(generation.extension_id for generation in resolution.record_generations) == record_owners,
    )
    settings_owners = tuple(change.extension_id for change in resolution.settings_changes)
    in_order = tuple(
        _owner(package) for package in migrating if _owner(package) in settings_owners
    )
    required = tuple(_owner(package) for package in migrating if not package.records)
    _require(
        "migration resolution changed the settings owner set or order",
        holds=settings_owners == in_order and all(owner in settings_owners for owner in required),
    )


def _validate_package(
    source: MigratingRuntimePackage, resolved: RuntimePackageSelection, change: SettingsChange | None,
) -> None:
    _require("migration resolution changed package identity", holds=source.extension_info == resolved.extension_info)
    if change is None:
        _require(
            "migration resolution changed settings without a settings conversion",
            holds=resolved.settings.revision == source.source.revision,
        )
        return
    _require(
        "migration resolution changed package identity or settings revision",
        holds=change.package_digest == source.extension_info.package_digest
        and change.expected_revision == source.source.revision
        and resolved.settings.revision == change.settings.revision,
    )
    _require_same_structure(explicit_settings(source.source), explicit_settings(change.settings))


def _require_same_structure(
    before: tuple[ScopedRuntimeSettings, ...], after: tuple[ScopedRuntimeSettings, ...],
) -> None:
    before_scopes = tuple(entry.scope for entry in before)
    _require(
        "migration resolution changed explicit override scopes or inheritance",
        holds=before_scopes == tuple(entry.scope for entry in after),
    )
    unconverted = (
        prior for prior, converted in zip(before, after, strict=True)
        if prior.settings.schema_ref == converted.settings.schema_ref
        and prior.settings != converted.settings
    )
    _require("migration resolution changed settings without a schema conversion", holds=next(unconverted, None) is None)


def _settings_change(resolution: RuntimeResolution, owner: str) -> SettingsChange | None:
    return next((change for change in resolution.settings_changes if change.extension_id == owner), None)


def _owner(package: MigratingRuntimePackage) -> str:
    return package.extension_info.extension_id


def _require(message: str, *, holds: bool) -> None:
    """Reject output that breaks one resolution rule.

    Raises:
        ValueError: If the rule does not hold.

    """
    if not holds:
        raise ValueError(message)
