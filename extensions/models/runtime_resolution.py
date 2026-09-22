# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind complete migration output to its immutable source selection."""

from typing import Annotated, Self

from baqylau_extension_api.models.base import WireModel
from pydantic import Field, model_validator

from extensions.models.lifecycle_selection import RuntimePackageSelection, RuntimeSelection, SettingsChange
from extensions.models.registry import ScopedRuntimeSettings
from extensions.models.runtime_candidates import MigratingRuntimePackage, MigratingRuntimeSelection
from extensions.models.settings_migration import explicit_settings

MAX_RESOLUTION_BYTES = 8_388_608


class RuntimeResolution(WireModel):
    """Keep converted raw overrides with the complete prepared runtime."""

    runtime: RuntimeSelection
    settings_changes: Annotated[tuple[SettingsChange, ...], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def require_bounded_resolution(self) -> Self:
        """Bound the complete persisted output, not only each conversion reply.

        Returns:
            Complete output within the host storage limit.

        Raises:
            ValueError: If the encoded output exceeds the bound.

        """
        if len(self.model_dump_json().encode()) > MAX_RESOLUTION_BYTES:
            message = "extension migration resolution exceeds its document limit"
            raise ValueError(message)
        return self

    def validate_candidate(self, candidate: MigratingRuntimeSelection) -> None:
        """Reject changed identities, unaffected packages, or raw override structure.

        Raises:
            ValueError: If output does not resolve this exact source plan.

        """
        if (
            self.runtime.runtime_revision != candidate.runtime_revision
            or self.runtime.catalog_revision != candidate.catalog_revision
            or len(self.runtime.packages) != len(candidate.packages)
        ):
            message = "migration resolution changed the runtime selection"
            raise ValueError(message)
        expected = tuple(package.extension_info.extension_id for package in candidate.packages
                         if isinstance(package, MigratingRuntimePackage))
        if tuple(change.extension_id for change in self.settings_changes) != expected:
            message = "migration resolution changed the settings owner set or order"
            raise ValueError(message)
        _validate_packages(candidate, self)


def _validate_packages(candidate: MigratingRuntimeSelection, resolution: RuntimeResolution) -> None:
    for source, resolved in zip(candidate.packages, resolution.runtime.packages, strict=True):
        if isinstance(source, MigratingRuntimePackage):
            change = next(
                entry for entry in resolution.settings_changes
                if entry.extension_id == source.extension_info.extension_id
            )
            _validate_package(source, resolved, change)
        elif source != resolved:
            message = "migration resolution changed an unaffected package"
            raise ValueError(message)


def _validate_package(
    source: MigratingRuntimePackage, resolved: RuntimePackageSelection, change: SettingsChange,
) -> None:
    if (
        source.extension_info != resolved.extension_info
        or change.package_digest != source.extension_info.package_digest
        or change.expected_revision != source.source.revision
        or resolved.settings.revision != change.settings.revision
    ):
        message = "migration resolution changed package identity or settings revision"
        raise ValueError(message)
    before = explicit_settings(source.source)
    after = explicit_settings(change.settings)
    _require_same_scopes(before, after)
    for prior, converted in zip(before, after, strict=True):
        if prior.settings.schema_ref == converted.settings.schema_ref and prior.settings != converted.settings:
            message = "migration resolution changed settings without a schema conversion"
            raise ValueError(message)


def _require_same_scopes(before: tuple[ScopedRuntimeSettings, ...], after: tuple[ScopedRuntimeSettings, ...]) -> None:
    before_scopes = tuple(entry.scope for entry in before)
    if before_scopes != tuple(entry.scope for entry in after):
        message = "migration resolution changed explicit override scopes or inheritance"
        raise ValueError(message)
