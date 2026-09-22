# Copyright (c) 2026 Zhambyl Yermagambet
"""Map the closed extension catalog fields to and from SQLite rows."""

import sqlite3
from typing import NamedTuple

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.base import ExtensionId
from pydantic import TypeAdapter

from extensions.models.catalog import DiscoveryCode, DiscoveryIssue, PackageCandidate, RootDiscoveryIssue


class PackageValues(NamedTuple):
    """Name the complete SQL parameter row."""

    source_path: str
    resolved_path: str | None
    package_digest: str | None
    extension_id: ExtensionId | None
    manifest: str | None
    issue_code: str | None
    issue_detail: str | None


def package_values(candidate: PackageCandidate) -> PackageValues:
    """Keep query columns separate from the typed manifest document.

    Returns:
        Complete values for one package source row.

    """
    manifest = candidate.manifest
    issue = candidate.issue
    return PackageValues(
        candidate.source_path, candidate.resolved_path, candidate.package_digest,
        None if manifest is None else manifest.extension_id,
        None if manifest is None else manifest.model_dump_json(),
        None if issue is None else issue.code, None if issue is None else issue.detail,
    )


def package_candidate(row: sqlite3.Row) -> PackageCandidate:
    """Validate a stored manifest and its discovery fields.

    Returns:
        A complete typed discovery entry.

    """
    encoded = _optional_text(row, "manifest")
    manifest = None if encoded is None else ExtensionManifest.model_validate_json(encoded)
    return PackageCandidate(
        source_path=str(row["source_path"]),
        resolved_path=_optional_text(row, "resolved_path"),
        package_digest=_optional_text(row, "package_digest"),
        manifest=manifest, issue=_issue(row),
    )


def root_issue(row: sqlite3.Row) -> RootDiscoveryIssue:
    """Decode a root error without changing its prior package rows.

    Returns:
        The stored root failure.

    Raises:
        ValueError: If a stored root failure has no issue.

    """
    issue = _issue(row)
    if issue is None:
        message = "a stored root failure requires its issue"
        raise ValueError(message)
    return RootDiscoveryIssue(root_path=str(row["root_path"]), issue=issue)


def _issue(row: sqlite3.Row) -> DiscoveryIssue | None:
    if row["issue_code"] is None:
        return None
    code = TypeAdapter[DiscoveryCode](DiscoveryCode).validate_python(str(row["issue_code"]))
    return DiscoveryIssue(code=code, detail=str(row["issue_detail"]))


def _optional_text(row: sqlite3.Row, name: str) -> str | None:
    return None if row[name] is None else str(row[name])
