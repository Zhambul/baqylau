# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one validated capability plan with no mutable discovery lookups."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.services import ExtensionDirectory
from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.service_provider import ServiceProvider, ServiceProviderLookup
from baqylau_extension_api.schemas import SchemaSet

from extensions import registry_validation
from extensions.models.lifecycle_selection import RuntimePackageSelection, RuntimeSelection
from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class RuntimeSnapshot(ExtensionDirectory, ServiceProviderLookup):
    """Use borrowed capabilities only inside an ExtensionRegistry read context."""

    directory: DirectorySnapshot
    packages: tuple[RegistryPackage, ...]
    schemas: SchemaSet
    active_order: tuple[str, ...]

    def runtime_selection(self) -> RuntimeSelection:
        """Capture the exact enabled identity and settings in validated active order.

        Returns:
            The data which a durable publication must accept before the pointer moves.

        """
        return RuntimeSelection(
            runtime_revision=self.directory.runtime_revision, catalog_revision=self.directory.catalog_revision,
            packages=tuple(
                RuntimePackageSelection(extension_info=package.entry.extension_info, settings=package.settings)
                for owner in self.active_order for package in self.packages if package.manifest.extension_id == owner
            ),
        )

    def list_extensions(self, request: DirectoryRequest) -> DirectorySnapshot:
        """Select active peers without changing the captured catalog boundary.

        Returns:
            Typed public metadata, never a backend object or its settings.

        """
        return DirectorySnapshot(
            catalog_revision=self.directory.catalog_revision, runtime_revision=self.directory.runtime_revision,
            entries=tuple(
                entry for entry in self.directory.entries if not request.active_only or entry.state == "enabled"
            ),
        )

    def get_service_provider(self, owner: str, scope: ExtensionScope) -> ServiceProvider | None:
        """Read a provider while the caller holds the registry context.

        Returns:
            Captured effective settings and the selected typed read capability.

        """
        return next((
            package.service_provider(self.schemas, scope)
            for package in self.packages if package.manifest.extension_id == owner
        ), None)


def prepare_snapshot(
    catalog_revision: int, runtime_revision: str, packages: tuple[RegistryPackage, ...],
) -> RuntimeSnapshot:
    """Validate and order a proposed set without changing workers or published state.

    Returns:
        One checked selection ready for a compare-and-set publication.

    """
    ordered = tuple(sorted(packages, key=lambda package: package.manifest.extension_id))
    directory = DirectorySnapshot(
        catalog_revision=catalog_revision, runtime_revision=runtime_revision,
        entries=tuple(package.entry for package in ordered),
    )
    _require_unique(ordered)
    registry_validation.validate_installed(ordered)
    schemas = SchemaSet(tuple(schema for package in ordered for schema in package.manifest.schemas))
    for package in ordered:
        registry_validation.validate_package(package, schemas, runtime_revision)
    return RuntimeSnapshot(directory, ordered, schemas, activation_order(tuple(
        active.manifest for active in ordered if active.entry.state == "enabled"
    )))


def _require_unique(packages: tuple[RegistryPackage, ...]) -> None:
    if len({package.manifest.extension_id for package in packages}) != len(packages):
        message = "registry package IDs must be unique"
        raise ValueError(message)
