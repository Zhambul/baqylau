# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject changed sources and damaged copies without publishing partial files."""

import stat
from pathlib import Path

import pytest

from extensions.artifacts import FilesystemExtensionArtifacts
from tests.extension_host import artifact_fixture as artifacts, package_fixture as packages

SOURCE_DIRECTORY = "source"


@pytest.mark.parametrize("change", ["content", "added", "removed", "link"])
def test_changed_source_is_not_published(tmp_path: Path, change: str) -> None:
    """A selected digest cannot silently become a different package at capture."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    _change_source(source, change)
    store = artifacts.store(tmp_path)
    with pytest.raises((ValueError, OSError)):
        store.capture_package(request)
    assert not (store.root / request.expected_digest).exists()
    assert not tuple(store.root.iterdir())


def test_corrupt_copy_is_not_repaired_on_read(tmp_path: Path) -> None:
    """A changed cached file is rejected, not overwritten from a newer source."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = artifacts.store(tmp_path)
    captured = store.capture_package(request)
    changed = Path(captured.directory) / packages.BACKEND_PATH
    changed.chmod(changed.stat().st_mode | stat.S_IWUSR)
    changed.write_bytes(b"corrupt artifact")
    with pytest.raises(ValueError, match="expected digest"):
        store.read_artifact(captured.package_digest)
    with pytest.raises(ValueError, match="expected digest"):
        store.capture_package(request)
    assert changed.read_bytes() == b"corrupt artifact"


@pytest.mark.parametrize("digest", ["../outside", "/outside", "not-a-digest"])
def test_lookup_rejects_invalid_digest(tmp_path: Path, digest: str) -> None:
    """A public artifact lookup cannot select a path instead of a content hash."""
    store = artifacts.store(tmp_path)
    with pytest.raises(ValueError, match="pattern"):
        store.read_artifact(digest)
    assert not store.root.exists()


def test_store_cannot_be_inside_source(tmp_path: Path) -> None:
    """Capture cannot recursively include its own growing output."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = FilesystemExtensionArtifacts(source / "artifacts")
    with pytest.raises(ValueError, match="inside the source"):
        store.capture_package(request)
    assert not store.root.exists()


def test_store_link_is_not_followed(tmp_path: Path) -> None:
    """A redirected artifact root cannot write into the link target."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    store = artifacts.store(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    store.root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="file-system link"):
        store.capture_package(artifacts.capture_request(source))
    assert not tuple(target.iterdir())


def _change_source(source: Path, change: str) -> None:
    path = source / packages.BACKEND_PATH
    if change == "content":
        path.write_bytes(b"new bytes")
    if change == "added":
        packages.write_file(source, "new.txt", b"new file")
    if change in {"removed", "link"}:
        path.rename(source / "saved.py")
    if change == "link":
        path.symlink_to(source / "saved.py")
