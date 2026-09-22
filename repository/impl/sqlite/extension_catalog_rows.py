# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and write complete extension catalog rows inside repository transactions."""

import sqlite3

from extensions.models.catalog import ExtensionCatalogSnapshot, PackageScan, RootDiscoveryIssue
from repository.impl.sqlite import extension_catalog_codec as mapper


def read_catalog(connection: sqlite3.Connection) -> ExtensionCatalogSnapshot:
    """Read the catalog head, package rows, and root failures at one snapshot.

    Returns:
        A complete stored catalog.

    """
    row = connection.execute("SELECT revision FROM extension_catalog_head WHERE id=1").fetchone()
    entries = tuple(mapper.package_candidate(package) for package in connection.execute(
        "SELECT * FROM extension_packages ORDER BY source_path",
    ))
    failures = tuple(mapper.root_issue(failure) for failure in connection.execute(
        "SELECT * FROM extension_catalog_errors ORDER BY root_path",
    ))
    revision = 0 if row is None else int(row["revision"])
    return ExtensionCatalogSnapshot(revision=revision, entries=entries, root_issues=failures)


def write_catalog(connection: sqlite3.Connection, snapshot: ExtensionCatalogSnapshot) -> None:
    """Replace discovery rows and the revision in the caller's transaction."""
    connection.execute("DELETE FROM extension_packages")
    connection.executemany(
        "INSERT INTO extension_packages("
        "source_path, resolved_path, package_digest, extension_id, manifest, issue_code, issue_detail"
        ") VALUES(?, ?, ?, ?, ?, ?, ?)",
        tuple(mapper.package_values(entry) for entry in snapshot.entries),
    )
    connection.execute("DELETE FROM extension_catalog_errors")
    connection.executemany(
        "INSERT INTO extension_catalog_errors(root_path, issue_code, issue_detail) VALUES(?, ?, ?)",
        tuple(_issue_values(failure) for failure in snapshot.root_issues),
    )
    connection.execute(
        "INSERT INTO extension_catalog_head(id, revision) VALUES(1, ?) "
        "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision", (snapshot.revision,),
    )
    _retain_manifests(connection, snapshot)


def accepted_snapshot(current: ExtensionCatalogSnapshot, scan: PackageScan) -> ExtensionCatalogSnapshot:
    """Retain prior package metadata if a root scan was incomplete.

    Returns:
        The same revision for unchanged data, or the next complete revision.

    """
    entries = current.entries if scan.root_issues else scan.entries
    unchanged = entries == current.entries and scan.root_issues == current.root_issues
    revision = current.revision if unchanged else current.revision + 1
    return ExtensionCatalogSnapshot(revision=revision, entries=entries, root_issues=scan.root_issues)


def _retain_manifests(connection: sqlite3.Connection, snapshot: ExtensionCatalogSnapshot) -> None:
    for entry in snapshot.entries:
        if entry.issue is None and entry.manifest is not None and entry.package_digest is not None:
            connection.execute(
                "INSERT OR IGNORE INTO extension_package_manifests(package_digest, manifest) VALUES(?, ?)",
                (entry.package_digest, entry.manifest.model_dump_json()),
            )


def _issue_values(failure: RootDiscoveryIssue) -> tuple[str, str, str]:
    return failure.root_path, failure.issue.code, failure.issue.detail
