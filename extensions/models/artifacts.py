# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep capture requests and checked installed paths explicit."""

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.base import Digest, NonemptyText, WireModel


class PackageCaptureRequest(WireModel):
    """Select exact catalog bytes; do not discover a newer source during capture."""

    source_path: NonemptyText
    expected_digest: Digest
    expected_manifest: ExtensionManifest


class PackageArtifact(WireModel):
    """Return one checked host-owned copy, not a mutable development source."""

    directory: NonemptyText
    package_digest: Digest
    manifest: ExtensionManifest
