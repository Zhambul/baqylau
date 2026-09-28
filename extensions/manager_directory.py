# Copyright (c) 2026 Zhambyl Yermagambet
"""Merge installed inactive metadata without replacing a selected active package."""

from baqylau_extension_api.models.directory import DirectoryEntry
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.versions import API_VERSION

from extensions.models.catalog import ExtensionCatalogSnapshot, PackageCandidate
from extensions.models.settings import SettingsOverrides, capture_settings
from extensions.registry_package import RegistryPackage
from extensions.registry_snapshot import RuntimeSnapshot, prepare_snapshot


def include_inactive(snapshot: RuntimeSnapshot, catalog: ExtensionCatalogSnapshot) -> RuntimeSnapshot:
    """Use the admission catalog, not a newer source scan after worker preparation.

    Returns:
        A complete public directory with unchanged enabled identities and settings.

    """
    active = {package.manifest.extension_id for package in snapshot.packages}
    inactive = tuple(
        _inactive(entry) for entry in catalog.entries
        if entry.issue is None and entry.manifest is not None and entry.manifest.extension_id not in active
    )
    return prepare_snapshot(
        snapshot.directory.catalog_revision, snapshot.directory.runtime_revision, (*snapshot.packages, *inactive),
        snapshot.relations,
    )


def _inactive(candidate: PackageCandidate) -> RegistryPackage:
    if candidate.manifest is None or candidate.package_digest is None:
        message = "inactive registry metadata requires a complete captured package"
        raise ValueError(message)
    identity = ExtensionInfo(
        extension_id=candidate.manifest.extension_id, package_version=candidate.manifest.package_version,
        package_digest=candidate.package_digest, api_version=API_VERSION,
    )
    return RegistryPackage(
        manifest=candidate.manifest, entry=DirectoryEntry(extension_info=identity, state="disabled"),
        settings=capture_settings(candidate.manifest, SettingsOverrides()),
    )
