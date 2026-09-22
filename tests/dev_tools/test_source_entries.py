# Copyright (c) 2026 Zhambyl Yermagambet
"""Check nested sources, inherited implementations, and exact file entries."""

from pathlib import Path

import pytest
from baqylau_dev.extension_checks import check_extension
from baqylau_dev.profiles import load_profile
from baqylau_extension_api.manifest.metadata import PackageAsset
from baqylau_extension_api.manifest.package import ExtensionManifest

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"
DIGEST_LENGTH = 64
SOURCE_DIRECTORY = "src"


def test_source_file_link_escape_is_rejected(tmp_path: Path) -> None:
    """A source file inside a selected root cannot link outside the package."""
    root = tmp_path / "package"
    root.mkdir()
    fixtures.create_package(root)
    target = tmp_path / "outside.py"
    target.write_text("import app\n", encoding=ENCODING)
    (root / SOURCE_DIRECTORY / "linked.py").symlink_to(target)
    with pytest.raises(ValueError, match="invalid project path"):
        check_extension(root, load_profile(root))


def test_changed_asset_digest_is_rejected(tmp_path: Path) -> None:
    """A declared asset must have the exact installed bytes."""
    fixtures.create_package(tmp_path)
    (tmp_path / "asset.txt").write_text("changed", encoding=ENCODING)
    manifest_path = tmp_path / "extension.json"
    manifest = ExtensionManifest.model_validate_json(manifest_path.read_bytes())
    changed = manifest.model_copy(update={"assets": (PackageAsset(
        path="asset.txt", digest="a" * DIGEST_LENGTH, media_type="text/plain",
    ),)})
    manifest_path.write_text(changed.model_dump_json(), encoding=ENCODING)
    with pytest.raises(ValueError, match="asset bytes do not match"):
        check_extension(tmp_path, load_profile(tmp_path))


def test_alias_and_concrete_inheritance_pass(tmp_path: Path) -> None:
    """An aliased SDK base and inherited concrete methods remain valid."""
    fixtures.create_package(tmp_path)
    path = tmp_path / SOURCE_DIRECTORY / "feature_backend.py"
    content = path.read_text(encoding=ENCODING).replace(
        "import ExtensionLifecycle", "import ExtensionLifecycle as Lifecycle",
    ).replace("FeatureLifecycle(ExtensionLifecycle)", "FeatureLifecycle(Lifecycle)")
    path.write_text(
        f"{content}\nclass ChildLifecycle(FeatureLifecycle):\n    pass\n\nChildLifecycle()\n", encoding=ENCODING,
    )
    completed = fixtures.invoke(tmp_path, "architecture")
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_relative_imports_resolve_in_package(tmp_path: Path) -> None:
    """Resolve relative bases against a package instead of a host import path."""
    fixtures.create_package(tmp_path)
    package = tmp_path / SOURCE_DIRECTORY / "feature"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding=ENCODING)
    (package / "child.py").write_text(
        "from .base import FeatureLifecycle\n\nclass Child(FeatureLifecycle):\n    pass\n\nChild()\n",
        encoding=ENCODING,
    )
    (tmp_path / SOURCE_DIRECTORY / "feature_backend.py").rename(package / "base.py")
    manifest = tmp_path / "extension.json"
    content = manifest.read_text(encoding=ENCODING).replace("feature_backend", "feature.base")
    manifest.write_text(content, encoding=ENCODING)
    assert check_extension(tmp_path, load_profile(tmp_path))
