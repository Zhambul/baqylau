# Copyright (c) 2026 Zhambyl Yermagambet
"""Check independent, persistent package copies without executing feature code."""

import stat
from pathlib import Path

from tests.extension_host import artifact_fixture as artifacts, package_fixture as packages

WRITE_BITS = 0o222
SOURCE_DIRECTORY = "source"


def test_capture_keeps_files_independent(tmp_path: Path) -> None:
    """Source edits cannot change the copied file, and capture uses no hard links."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = artifacts.store(tmp_path)
    captured = store.capture_package(request)
    copied = Path(captured.directory) / packages.BACKEND_PATH
    assert copied.read_bytes() == (source / packages.BACKEND_PATH).read_bytes()
    assert copied.stat().st_ino != (source / packages.BACKEND_PATH).stat().st_ino
    (source / packages.BACKEND_PATH).write_bytes(b"changed source")
    assert copied.read_bytes() == packages.BACKEND_SOURCE.encode(packages.ENCODING)
    assert store.read_artifact(captured.package_digest) == captured
    assert not copied.with_name(packages.MARKER_NAME).exists()


def test_capture_sets_read_only_permissions(tmp_path: Path) -> None:
    """The host removes normal write access while preserving executable identity."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    executable = source / packages.BACKEND_PATH
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    captured = artifacts.store(tmp_path).capture_package(artifacts.capture_request(source))
    directory = Path(captured.directory)
    assert not directory.stat().st_mode & WRITE_BITS
    modes = tuple(path.stat().st_mode for path in directory.rglob("*"))
    assert all(not mode & WRITE_BITS for mode in modes)
    assert (directory / packages.BACKEND_PATH).stat().st_mode & stat.S_IXUSR
    assert source.stat().st_mode & stat.S_IWUSR


def test_same_capture_does_not_replace_files(tmp_path: Path) -> None:
    """A repeated capture and a new store instance reuse the same checked directory."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = artifacts.store(tmp_path)
    captured = store.capture_package(request)
    inode = Path(captured.directory).stat().st_ino
    assert artifacts.store(tmp_path).capture_package(request) == captured
    assert Path(captured.directory).stat().st_ino == inode
    assert tuple(store.root.iterdir()) == (Path(captured.directory),)


def test_copy_survives_source_removal(tmp_path: Path) -> None:
    """Lookup and reuse depend on the artifact bytes, not the development directory."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = artifacts.store(tmp_path)
    captured = store.capture_package(request)
    source.rename(tmp_path / "removed-source")
    assert store.read_artifact(captured.package_digest) == captured
    assert store.capture_package(request) == captured


def test_web_only_package_is_captured(tmp_path: Path) -> None:
    """Independent web bytes need no backend import or idle worker."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY, web=True)
    captured = artifacts.store(tmp_path).capture_package(artifacts.capture_request(source))
    assert captured.manifest.backend is None
    asset = Path(captured.directory) / captured.manifest.assets[0].path
    assert asset.read_bytes() == packages.WEB_SOURCE


def test_new_digest_keeps_previous_artifact(tmp_path: Path) -> None:
    """An update publishes a new path without removing the prior package bytes."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    store = artifacts.store(tmp_path)
    before = store.capture_package(artifacts.capture_request(source))
    packages.write_file(source, "resource.txt", b"new resource")
    after = store.capture_package(artifacts.capture_request(source))
    assert before.package_digest != after.package_digest
    assert store.read_artifact(before.package_digest) == before
    assert not (Path(before.directory) / "resource.txt").exists()
    assert (Path(after.directory) / "resource.txt").read_bytes() == b"new resource"
