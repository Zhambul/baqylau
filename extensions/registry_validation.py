# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject inconsistent registry selections before they can replace active workers."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.models.directory import DirectoryEntry
from baqylau_extension_api.runtime.capability_checks import validate_handlers
from baqylau_extension_api.runtime.loading import capability_names
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.lifecycle_selection import RuntimePackageSelection
from extensions.registry_package import RegistryPackage


def validate_package(package: RegistryPackage, schemas: SchemaSet, runtime_revision: str) -> None:
    """Check identities, active state, and every captured settings document."""
    DirectoryEntry.model_validate(package.entry)
    RuntimePackageSelection(
        extension_info=package.entry.extension_info, settings=package.settings,
    ).validate_manifest(package.manifest, schemas)
    _validate_active(package, runtime_revision)


def _validate_active(package: RegistryPackage, runtime_revision: str) -> None:
    if package.entry.state != "enabled":
        if package.environment is not None or package.plugin is not None:
            message = "inactive registry packages cannot expose workers or environments"
            raise ExtensionContractError(message)
        return
    if package.environment is None or (
        package.environment.extension_info != package.entry.extension_info
        or package.environment.runtime_revision != runtime_revision
    ):
        message = "active registry environment does not match the selected runtime"
        raise ExtensionContractError(message)
    _validate_backend(package)


def _validate_backend(package: RegistryPackage) -> None:
    if (package.plugin is None) != (package.manifest.backend is None):
        message = "registry plugin presence does not match its backend declaration"
        raise ExtensionContractError(message)
    if package.plugin is None:
        return
    if package.plugin.extension_info != package.entry.extension_info or (
        set(capability_names(package.plugin.capabilities)) != set(package.manifest.capabilities)
    ):
        message = "registry plugin does not match its declared identity or capabilities"
        raise ExtensionContractError(message)
    validate_handlers(package.plugin.capabilities)


def validate_installed(packages: tuple[RegistryPackage, ...]) -> None:
    """Check all installed manifests against their captured peer schema definitions."""
    for package in packages:
        validate_manifest(package.manifest, tuple(
            schema for peer in packages if peer.manifest.extension_id != package.manifest.extension_id
            for schema in peer.manifest.schemas
        ))
