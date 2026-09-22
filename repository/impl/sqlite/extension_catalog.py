# Copyright (c) 2026 Zhambyl Yermagambet
"""Store revision-checked discovery without changing active runtime state."""

from dataclasses import dataclass

from baqylau_extension_api.manifest.package import ExtensionManifest

from extensions.models.catalog import CatalogWriteResult, ExtensionCatalogSnapshot, PackageScan
from repository.contract.extension_catalog import ExtensionCatalogRepository
from repository.impl.sqlite import extension_catalog_rows as rows
from repository.impl.sqlite.connection import SqliteDatabase


@dataclass(frozen=True)
class SqliteExtensionCatalogRepository(ExtensionCatalogRepository):
    """Keep catalog replacement and retained schema declarations atomic."""

    database: SqliteDatabase

    def read_extension_catalog(self) -> ExtensionCatalogSnapshot:
        """Read current metadata without checking or executing package files.

        Returns:
            One consistent catalog snapshot.

        """
        with self.database.read() as connection:
            return rows.read_catalog(connection)

    def replace_extension_catalog(self, expected_revision: int, scan: PackageScan) -> CatalogWriteResult:
        """Keep a concurrent scan from replacing newer accepted metadata.

        Returns:
            Accepted metadata or the unchanged current catalog on conflict.

        """
        checked = PackageScan.model_validate(scan)
        with self.database.write() as connection:
            current = rows.read_catalog(connection)
            if current.revision != expected_revision:
                return CatalogWriteResult(accepted=False, snapshot=current)
            selected = rows.accepted_snapshot(current, checked)
            if selected != current:
                rows.write_catalog(connection, selected)
            return CatalogWriteResult(accepted=True, snapshot=selected)

    def retained_extension_manifest(self, package_digest: str) -> ExtensionManifest | None:
        """Keep schema declarations readable after their source package is removed.

        Returns:
            The retained manifest, or no record for an unknown package digest.

        """
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT manifest FROM extension_package_manifests WHERE package_digest=?", (package_digest,),
            ).fetchone()
        return None if row is None else ExtensionManifest.model_validate_json(str(row["manifest"]))
