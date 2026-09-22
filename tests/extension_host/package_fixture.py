# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete external file fixtures whose backend must not run during discovery."""

import hashlib
from pathlib import Path

from baqylau_extension_api.manifest.metadata import PackageAsset
from baqylau_extension_api.manifest.package import ExtensionManifest

from tests.extension_api import manifest_samples

OWNER = "test.external"
ENCODING = "utf-8"
BACKEND_PATH = "uninstalled_feature/backend.py"
MARKER_NAME = "backend-imported"
WEB_SOURCE = b"export const label = 'external feature';\n"
BACKEND_SOURCE = """from pathlib import Path
Path(__file__).with_name("backend-imported").write_text("executed", encoding="utf-8")

def build_extension(services):
    raise RuntimeError("This discovery fixture must not be loaded.")
"""


def write_package(root: Path, owner: str = OWNER, *, web: bool = False) -> Path:
    """Create package bytes outside the host source tree.

    Returns:
        The built package directory, with declared test and asset files.

    """
    directory = root / owner
    manifest = _web_manifest(owner) if web else manifest_samples.backend_manifest(owner)
    directory.mkdir(parents=True)
    save_manifest(directory, manifest)
    if web:
        write_file(directory, manifest_samples.MODULE_PATH, WEB_SOURCE)
    else:
        write_file(directory, BACKEND_PATH, BACKEND_SOURCE.encode(ENCODING))
        write_file(directory, "uninstalled_feature/__init__.py", b"")
    write_file(directory, manifest.e2e[0].path, b"# Package-owned discovery test entry.\n")
    return directory


def save_manifest(directory: Path, manifest: ExtensionManifest) -> None:
    """Write test metadata without importing a feature implementation."""
    (directory / "extension.json").write_text(manifest.model_dump_json(), encoding=ENCODING)


def read_manifest(directory: Path) -> ExtensionManifest:
    """Read fixture metadata as its declared model.

    Returns:
        The current package manifest.

    """
    return ExtensionManifest.model_validate_json((directory / "extension.json").read_bytes())


def write_file(directory: Path, relative_path: str, content: bytes) -> None:
    """Write one test file inside the fixture package."""
    path = directory / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _web_manifest(owner: str) -> ExtensionManifest:
    original = manifest_samples.web_manifest(owner)
    return original.model_copy(update={"assets": (PackageAsset(
        path=manifest_samples.MODULE_PATH, digest=hashlib.sha256(WEB_SOURCE).hexdigest(), media_type="text/javascript",
    ),)})
