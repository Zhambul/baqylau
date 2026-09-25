# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose fixed package storage before application catalog replacement."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from extensions.artifact_contract import ExtensionArtifacts
from extensions.artifacts import FilesystemExtensionArtifacts
from extensions.capture_scanner import CapturingExtensionScanner
from extensions.configuration import ARTIFACTS_DIRECTORY
from extensions.discovery import FilesystemExtensionScanner
from extensions.discovery_contract import ExtensionPackageScanner


@singleton
def extension_artifacts(database: MainDb) -> ExtensionArtifacts:
    """Select application-owned storage outside the package discovery roots.

    Returns:
        A store which creates its root only when it must capture a package.

    """
    return FilesystemExtensionArtifacts(Path(database.path).parent / ARTIFACTS_DIRECTORY)


Artifacts = Annotated[ExtensionArtifacts, Depends(extension_artifacts)]


@singleton
def extension_scanner(artifacts: Artifacts) -> ExtensionPackageScanner:
    """Capture only complete valid discovery results.

    Returns:
        The application scanner with no worker activation.

    """
    return CapturingExtensionScanner(FilesystemExtensionScanner(), artifacts)


Scanner = Annotated[ExtensionPackageScanner, Depends(extension_scanner)]
