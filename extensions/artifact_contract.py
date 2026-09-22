# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate package capture and lookup from runtime consumers."""

from typing import Protocol

from extensions.models.artifacts import PackageArtifact, PackageCaptureRequest


class ExtensionArtifacts(Protocol):
    """Publish fixed package copies and check them before runtime use."""

    def capture_package(self, package_capture_request: PackageCaptureRequest) -> PackageArtifact:
        """Capture the selected source or return the same already checked artifact."""
        ...

    def read_artifact(self, package_digest: str) -> PackageArtifact:
        """Check an existing artifact without reading its former development source."""
        ...
