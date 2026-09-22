# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate file discovery and catalog access from application composition."""

from typing import Protocol

from extensions.configuration import ExtensionRoots
from extensions.models.catalog import CatalogWriteResult, ExtensionCatalogSnapshot, PackageScan


class ExtensionPackageScanner(Protocol):
    """Read package files without importing feature code."""

    def scan_packages(self, roots: ExtensionRoots) -> PackageScan:
        """Inspect the configured package roots."""
        ...


class ExtensionCatalog(Protocol):
    """Read and replace discovery metadata without enabling a package."""

    def catalog_snapshot(self) -> ExtensionCatalogSnapshot:
        """Read the current durable catalog."""
        ...

    def rescan_packages(self, expected_revision: int) -> CatalogWriteResult:
        """Check files and accept the result only at the expected revision."""
        ...
