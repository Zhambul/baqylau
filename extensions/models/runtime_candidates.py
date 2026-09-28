# Copyright (c) 2026 Zhambyl Yermagambet
"""Distinguish an unresolved migration input from ready runtime settings."""

from typing import Annotated, Self

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.models.base import Identifier, Revision, WireModel
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.schemas import SchemaSet
from pydantic import Field, model_validator

from extensions.models.lifecycle_selection import RuntimePackageSelection, RuntimeSelection, validate_package_identity
from extensions.models.record_migration import RecordSource, validate_record_sources
from extensions.models.settings import SettingsOverrides
from extensions.models.settings_migration import needs_settings_migration, validate_migration_source


class MigratingRuntimePackage(WireModel):
    """Pin old raw choices and stored record schemas without pretending that converted values already exist."""

    extension_info: ExtensionInfo
    source: SettingsOverrides
    records: Annotated[tuple[RecordSource, ...], Field(max_length=1000)] = ()

    def validate_manifest(self, manifest: ExtensionManifest, schemas: SchemaSet) -> None:
        """Check the selected identity, saved values, scopes, and exact conversion paths.

        Raises:
            ExtensionContractError: If the package needs neither a settings nor a record conversion.

        """
        validate_package_identity(self.extension_info, manifest)
        validate_record_sources(manifest, self.records)
        if needs_settings_migration(manifest, self.source):
            validate_migration_source(manifest, self.source, schemas)
        elif not self.records:
            message = "a migrating package requires a settings or record conversion"
            raise ExtensionContractError(message)


type RuntimePackageCandidate = RuntimePackageSelection | MigratingRuntimePackage


class MigratingRuntimeSelection(WireModel):
    """Reserve one full package plan which must be resolved before activation."""

    runtime_revision: Identifier
    catalog_revision: Revision
    packages: Annotated[tuple[RuntimePackageCandidate, ...], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def require_migration_package(self) -> Self:
        """Keep unresolved plans distinct from complete runtime selections.

        Returns:
            A plan with unique owners and at least one required conversion.

        Raises:
            ValueError: If no package requires a conversion.

        """
        require_unique((package.extension_info.extension_id for package in self.packages), "runtime package IDs")
        if not any(isinstance(package, MigratingRuntimePackage) for package in self.packages):
            message = "migration runtime requires an unresolved package"
            raise ValueError(message)
        return self


type RuntimeCandidate = RuntimeSelection | MigratingRuntimeSelection


def runtime_candidate(
    runtime_revision: str, catalog_revision: int, packages: tuple[RuntimePackageCandidate, ...],
) -> RuntimeCandidate:
    """Use the complete selection type unless a declared migration is required.

    Returns:
        Exact selected settings or explicit unresolved migration sources.

    """
    if any(isinstance(package, MigratingRuntimePackage) for package in packages):
        return MigratingRuntimeSelection(
            runtime_revision=runtime_revision, catalog_revision=catalog_revision, packages=packages,
        )
    return RuntimeSelection(
        runtime_revision=runtime_revision, catalog_revision=catalog_revision,
        packages=tuple(package for package in packages if isinstance(package, RuntimePackageSelection)),
    )
