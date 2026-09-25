# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe discovery without disclosing settings documents or running code."""

from baqylau_extension_api.manifest.data import CapabilityName
from baqylau_extension_api.models.base import Digest, ExtensionId, NonemptyText, Revision, WireModel
from baqylau_extension_api.versions import PackageVersion

from extensions.models.catalog import DiscoveryIssue, RootDiscoveryIssue
from extensions.models.extension_health import ExtensionHealth


class ExtensionPackageResponse(WireModel):
    """Show one valid or invalid package source, not its runtime activation state."""

    source_path: NonemptyText
    resolved_path: NonemptyText | None
    extension_id: ExtensionId | None
    name: NonemptyText | None
    package_version: PackageVersion | None
    package_digest: Digest | None
    capabilities: tuple[CapabilityName, ...]
    issue: DiscoveryIssue | None


class ExtensionCatalogResponse(WireModel):
    """Read a durable catalog revision and any failed root scans."""

    revision: Revision
    entries: tuple[ExtensionPackageResponse, ...]
    root_issues: tuple[RootDiscoveryIssue, ...]


class RescanExtensionsRequest(WireModel):
    """Reject stale management writes before package files are read."""

    expected_revision: Revision


class ExtensionHealthResponse(WireModel):
    """Report the durable health of every extension that has failed at least once."""

    failure_limit: int
    extensions: tuple[ExtensionHealth, ...]
