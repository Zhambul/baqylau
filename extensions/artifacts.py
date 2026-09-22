# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep checked package copies separate from changing development files."""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from baqylau_extension_api.models.base import Digest
from pydantic import TypeAdapter

from extensions import artifact_files, discovery_manifest
from extensions.artifact_contract import ExtensionArtifacts
from extensions.discovery_files import inventory_digest, package_files
from extensions.models.artifacts import PackageArtifact, PackageCaptureRequest

PRIVATE_DIRECTORY = 0o700


@dataclass(frozen=True)
class FilesystemExtensionArtifacts(ExtensionArtifacts):
    """Store content-addressed packages without importing or updating their source."""

    root: Path

    def capture_package(self, package_capture_request: PackageCaptureRequest) -> PackageArtifact:
        """Capture exact selected bytes and reuse only a valid complete copy.

        Returns:
            A checked published artifact.

        """
        request = PackageCaptureRequest.model_validate(package_capture_request)
        root = _store_root(self.root)
        target = root / request.expected_digest
        if target.exists() or target.is_symlink():
            return _matching_artifact(target, request)
        _publish_copy(root, target, request)
        return _matching_artifact(target, request)

    def read_artifact(self, package_digest: str) -> PackageArtifact:
        """Recheck a published artifact without requiring its development source.

        Returns:
            The validated package copy.

        """
        checked = TypeAdapter[Digest](Digest).validate_python(package_digest)
        return _read_artifact(_store_root(self.root) / checked, checked)


def _store_root(root: Path) -> Path:
    normalized = root.expanduser().absolute()
    if normalized.is_symlink():
        message = "the artifact root cannot be a file-system link"
        raise ValueError(message)
    return normalized.resolve()


def _source_directory(source_path: str, root: Path) -> Path:
    source = Path(source_path)
    resolved = source.resolve(strict=True)
    if not source.is_absolute() or resolved != source or not source.is_dir():
        message = "package capture requires the resolved source directory"
        raise ValueError(message)
    if root.is_relative_to(source):
        message = "artifact storage cannot be inside the source package"
        raise ValueError(message)
    return source


def _publish_copy(root: Path, target: Path, request: PackageCaptureRequest) -> None:
    source = _source_directory(request.source_path, root)
    root.mkdir(mode=PRIVATE_DIRECTORY, parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".capture-", dir=root) as temporary:
        staged = Path(temporary)
        _prepare_copy(source, staged, request)
        artifact_files.publish_directory(staged, target)


def _prepare_copy(source: Path, staged: Path, request: PackageCaptureRequest) -> None:
    inventory = package_files(source)
    if inventory_digest(inventory) != request.expected_digest:
        message = "package source changed after discovery"
        raise ValueError(message)
    artifact_files.capture_inventory(source, staged, inventory)
    _matching_artifact(staged, request)
    artifact_files.seal_directory(staged)


def _matching_artifact(directory: Path, request: PackageCaptureRequest) -> PackageArtifact:
    artifact = _read_artifact(directory, request.expected_digest)
    if artifact.manifest != request.expected_manifest:
        message = "captured manifest does not match the selected catalog"
        raise ValueError(message)
    return artifact


def _read_artifact(directory: Path, expected_digest: str) -> PackageArtifact:
    if directory.is_symlink() or not directory.is_dir():
        message = "a package artifact must be a regular directory"
        raise ValueError(message)
    manifest, encoded = discovery_manifest.read_manifest(directory)
    digest = discovery_manifest.checked_package_digest(directory, manifest, encoded)
    if digest != expected_digest:
        message = "package artifact does not match its expected digest"
        raise ValueError(message)
    return PackageArtifact(directory=str(directory), package_digest=digest, manifest=manifest)
