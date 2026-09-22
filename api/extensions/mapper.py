# Copyright (c) 2026 Zhambyl Yermagambet
"""Select public discovery metadata without returning complete manifests."""

from api.extensions.models import ExtensionCatalogResponse, ExtensionPackageResponse
from extensions.models.catalog import ExtensionCatalogSnapshot, PackageCandidate


def catalog_response(snapshot: ExtensionCatalogSnapshot) -> ExtensionCatalogResponse:
    """Keep the public list bound to one stored revision.

    Returns:
        The safe catalog description.

    """
    return ExtensionCatalogResponse(
        revision=snapshot.revision, root_issues=snapshot.root_issues,
        entries=tuple(_package_response(entry) for entry in snapshot.entries),
    )


def _package_response(candidate: PackageCandidate) -> ExtensionPackageResponse:
    manifest = candidate.manifest
    return ExtensionPackageResponse(
        source_path=candidate.source_path, resolved_path=candidate.resolved_path,
        extension_id=None if manifest is None else manifest.extension_id,
        name=None if manifest is None else manifest.name,
        package_version=None if manifest is None else manifest.package_version,
        package_digest=candidate.package_digest, capabilities=() if manifest is None else manifest.capabilities,
        issue=candidate.issue,
    )
