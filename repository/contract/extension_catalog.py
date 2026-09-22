# Copyright (c) 2026 Zhambyl Yermagambet
"""Store whole extension discovery revisions without exposing transactions."""

from typing import Protocol

from baqylau_extension_api.manifest.package import ExtensionManifest

from extensions.models.catalog import CatalogWriteResult, ExtensionCatalogSnapshot, PackageScan


class ExtensionCatalogRepository(Protocol):
    """Keep current discovery and retained validated package declarations."""

    def read_extension_catalog(self) -> ExtensionCatalogSnapshot:
        """Read one consistent catalog revision."""
        ...

    def replace_extension_catalog(self, expected_revision: int, scan: PackageScan) -> CatalogWriteResult:
        """Replace a catalog atomically, or report the current revision on conflict."""
        ...

    def retained_extension_manifest(self, package_digest: str) -> ExtensionManifest | None:
        """Read a prior validated declaration after package removal."""
        ...
