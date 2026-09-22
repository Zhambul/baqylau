# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect file discovery to a revision-checked durable catalog."""

from dataclasses import dataclass

from extensions.configuration import ExtensionRoots
from extensions.discovery_contract import ExtensionCatalog, ExtensionPackageScanner
from extensions.models.catalog import CatalogWriteResult, ExtensionCatalogSnapshot
from repository.contract.extension_catalog import ExtensionCatalogRepository


@dataclass(frozen=True)
class ExtensionCatalogService(ExtensionCatalog):
    """Keep discovery outside database transactions and separate from activation."""

    roots: ExtensionRoots
    scanner: ExtensionPackageScanner
    repository: ExtensionCatalogRepository

    def catalog_snapshot(self) -> ExtensionCatalogSnapshot:
        """Read the last stored catalog and any root failures.

        Returns:
            The persisted discovery result.

        """
        return self.repository.read_extension_catalog()

    def rescan_packages(self, expected_revision: int) -> CatalogWriteResult:
        """Inspect files only after checking the caller's expected revision.

        Returns:
            An accepted catalog or an explicit revision conflict.

        """
        current = self.repository.read_extension_catalog()
        if current.revision != expected_revision:
            return CatalogWriteResult(accepted=False, snapshot=current)
        scanned = self.scanner.scan_packages(self.roots)
        return self.repository.replace_extension_catalog(expected_revision, scanned)
