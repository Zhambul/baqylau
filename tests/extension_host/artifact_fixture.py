# Copyright (c) 2026 Zhambyl Yermagambet
"""Select artifact requests from actual checked package bytes."""

from pathlib import Path

from extensions.artifacts import FilesystemExtensionArtifacts
from extensions.discovery_manifest import checked_package_digest, read_manifest
from extensions.models.artifacts import PackageCaptureRequest


def capture_request(directory: Path) -> PackageCaptureRequest:
    """Keep the discovered manifest and digest fixed for a later capture.

    Returns:
        The same selection used by the application capture scanner.

    """
    manifest, encoded = read_manifest(directory)
    digest = checked_package_digest(directory, manifest, encoded)
    return PackageCaptureRequest(
        source_path=str(directory.resolve()), expected_digest=digest, expected_manifest=manifest,
    )


def store(directory: Path) -> FilesystemExtensionArtifacts:
    """Use a private artifact root outside all package source directories.

    Returns:
        The real file store.

    """
    return FilesystemExtensionArtifacts(directory / "artifacts")
