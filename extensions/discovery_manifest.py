# Copyright (c) 2026 Zhambyl Yermagambet
"""Check declared package files using the public data-only manifest."""

import hashlib
from pathlib import Path

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest

from extensions.discovery_bytes import read_document
from extensions.discovery_files import PackageFile, inventory_digest, package_files

MANIFEST_NAME = "extension.json"
MAX_MANIFEST_BYTES = 8_388_608


def checked_package_digest(directory: Path, manifest: ExtensionManifest, encoded: bytes) -> str:
    """Validate declared files before returning their complete package digest.

    Returns:
        The digest of the checked source inventory.

    """
    inventory = package_files(directory)
    check_declared_files(manifest, encoded, inventory)
    return inventory_digest(inventory)


def read_manifest(directory: Path) -> tuple[ExtensionManifest, bytes]:
    """Read one bounded regular manifest without running its backend.

    Returns:
        The checked manifest and exact bytes used for the inventory comparison.

    Raises:
        ValueError: If the manifest is linked or exceeds its byte limit.

    """
    path = directory / MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        message = "extension.json must be a regular file"
        raise ValueError(message)
    encoded = read_document(path, MAX_MANIFEST_BYTES)
    return validate_manifest(ExtensionManifest.model_validate_json(encoded)), encoded


def check_declared_files(
    manifest: ExtensionManifest, encoded: bytes, inventory: tuple[PackageFile, ...],
) -> None:
    """Require declared assets, tests, and the backend module in the same package.

    Raises:
        ValueError: If the manifest changed or any declared file is absent or invalid.

    """
    files: dict[str, PackageFile] = {entry.relative_path: entry for entry in inventory}
    original = files.get(MANIFEST_NAME)
    if original is None or original.digest != hashlib.sha256(encoded).hexdigest():
        message = "extension.json changed during discovery"
        raise ValueError(message)
    for asset in manifest.assets:
        entry = files.get(asset.path)
        if entry is None or entry.digest != asset.digest:
            message = "a declared asset is absent or has a different digest"
            raise ValueError(message)
    if any(case.path not in files for case in manifest.e2e):
        message = "a declared E2E entry is absent from the package"
        raise ValueError(message)
    _require_backend(manifest, tuple(files))
    _require_runtime_files(manifest, tuple(files))


def _require_backend(manifest: ExtensionManifest, filenames: tuple[str, ...]) -> None:
    if manifest.backend is None:
        return
    stem = manifest.backend.module.replace(".", "/")
    choices = (f"{stem}.py", f"{stem}/__init__.py", f"src/{stem}.py", f"src/{stem}/__init__.py")
    if sum(path in filenames for path in choices) != 1:
        message = "backend module must resolve to one package-owned Python source file"
        raise ValueError(message)


def _require_runtime_files(manifest: ExtensionManifest, filenames: tuple[str, ...]) -> None:
    if manifest.backend is None or manifest.backend.environment is None:
        return
    environment = manifest.backend.environment
    wheels = tuple(path for path in filenames if path.startswith(f"{environment.wheelhouse}/"))
    has_wheel = any(path.endswith(".whl") for path in wheels)
    if environment.requirements not in filenames or not has_wheel:
        message = "declared runtime requirements and dependency wheels must be package-owned files"
        raise ValueError(message)
