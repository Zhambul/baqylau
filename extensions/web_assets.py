# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one declared web asset of a retained package copy, checked against its declared digest."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.manifest.metadata import PackageAsset
from baqylau_extension_api.manifest.package import ExtensionManifest

from extensions.artifact_contract import ExtensionArtifacts

SERVABLE_MEDIA_TYPES = (
    "text/javascript", "application/javascript", "text/css", "application/json",
    "image/png", "image/svg+xml", "image/webp", "font/woff2",
)
MAX_ASSET_BYTES = 16_777_216


class AssetNotFoundError(LookupError):
    """Reject an asset that the package does not declare, cannot serve, or does not match."""


@dataclass(frozen=True)
class WebAsset:
    """Keep the checked bytes and the declared media type of one asset."""

    content: bytes
    media_type: str


@dataclass(frozen=True)
class WebAssets:
    """Serve only manifest-declared files inside a retained package copy."""

    artifacts: ExtensionArtifacts

    def read_asset(self, extension_id: str, package_digest: str, relative_path: str) -> WebAsset:
        """Read one declared asset of one exact package.

        Returns:
            The bytes and the declared media type.

        Raises:
            AssetNotFoundError: If the package, the declaration, the media type, or the bytes do not match.

        """
        try:
            artifact = self.artifacts.read_artifact(package_digest)
        except (OSError, ValueError) as error:
            message = "the package copy is not available"
            raise AssetNotFoundError(message) from error
        declared = _declared(artifact.manifest, extension_id, relative_path)
        content = _read_inside(Path(artifact.directory), relative_path)
        if hashlib.sha256(content).hexdigest() != declared.digest:
            message = "the asset bytes do not match their declaration"
            raise AssetNotFoundError(message)
        return WebAsset(content=content, media_type=declared.media_type)


def _declared(manifest: ExtensionManifest, extension_id: str, relative_path: str) -> PackageAsset:
    declared = next((asset for asset in manifest.assets if asset.path == relative_path), None)
    if manifest.extension_id != extension_id or declared is None or declared.media_type not in SERVABLE_MEDIA_TYPES:
        message = "the package does not declare this asset with a servable media type"
        raise AssetNotFoundError(message)
    return declared


def _read_inside(directory: Path, relative_path: str) -> bytes:
    root = directory.resolve()
    path = (root / relative_path).resolve()
    inside = path.is_relative_to(root) and path.is_file()
    if not inside or path.stat().st_size > MAX_ASSET_BYTES:
        message = "the asset is not a bounded file inside the package"
        raise AssetNotFoundError(message)
    return path.read_bytes()
