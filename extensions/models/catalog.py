# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep discovery results separate from requested and active runtime state."""

from typing import Annotated, Literal, Self

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.base import Digest, NonemptyText, Revision, WireModel
from pydantic import Field, model_validator

type DiscoveryCode = Literal[
    "invalid_manifest", "incompatible_api", "invalid_files", "unreadable", "duplicate_id", "root_unavailable",
    "capture_failed",
]


class DiscoveryIssue(WireModel):
    """Report a bounded reason without copying invalid document values."""

    code: DiscoveryCode
    detail: Annotated[NonemptyText, Field(max_length=1000)]


class PackageCandidate(WireModel):
    """Describe checked source bytes, not an active or immutable installed worker."""

    source_path: NonemptyText
    resolved_path: NonemptyText | None = None
    manifest: ExtensionManifest | None = None
    package_digest: Digest | None = None
    issue: DiscoveryIssue | None = None

    def with_issue(self, issue: DiscoveryIssue) -> Self:
        """Keep captured metadata while reporting a validation failure.

        Returns:
            A newly validated failed candidate.

        """
        return self.__class__(
            source_path=self.source_path, resolved_path=self.resolved_path, manifest=self.manifest,
            package_digest=self.package_digest, issue=issue,
        )

    def with_digest(self, package_digest: str) -> Self:
        """Accept the checked file identity without bypassing model validation.

        Returns:
            A complete candidate with no discovery error.

        """
        return self.__class__(
            source_path=self.source_path, resolved_path=self.resolved_path, manifest=self.manifest,
            package_digest=package_digest,
        )

    @model_validator(mode="after")
    def require_complete_candidate(self) -> Self:
        """Require complete metadata when no discovery failure is present.

        Returns:
            The checked discovery entry.

        Raises:
            ValueError: If a valid entry lacks its files or manifest identity.

        """
        if self.issue is None and (
            self.resolved_path is None or self.manifest is None or self.package_digest is None
        ):
            message = "a valid candidate requires a path, manifest, and digest"
            raise ValueError(message)
        return self


class RootDiscoveryIssue(WireModel):
    """Keep a failed root scan visible without discarding the prior catalog."""

    root_path: NonemptyText
    issue: DiscoveryIssue


class PackageScan(WireModel):
    """Return one complete scan or root failures which prevent its replacement."""

    entries: tuple[PackageCandidate, ...] = ()
    root_issues: tuple[RootDiscoveryIssue, ...] = ()

    @model_validator(mode="after")
    def require_unique_sources(self) -> Self:
        """Keep package and root keys unique before a storage write.

        Returns:
            A complete scan with unique row identities.

        Raises:
            ValueError: If a source or root error occurs twice.

        """
        sources = {entry.source_path for entry in self.entries}
        roots = {failure.root_path for failure in self.root_issues}
        duplicate_sources = len(sources) != len(self.entries)
        duplicate_roots = len(roots) != len(self.root_issues)
        if duplicate_sources or duplicate_roots:
            message = "catalog source paths and root failures must be unique"
            raise ValueError(message)
        return self


class ExtensionCatalogSnapshot(PackageScan):
    """Bind one stored catalog and its latest root failures to a revision."""

    revision: Revision = 0


class CatalogWriteResult(WireModel):
    """Distinguish accepted discovery from a stale concurrent update."""

    accepted: bool
    snapshot: ExtensionCatalogSnapshot
