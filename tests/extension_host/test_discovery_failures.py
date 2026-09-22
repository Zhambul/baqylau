# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid declarations and unsafe or incomplete package files."""

from pathlib import Path

import pytest

from extensions import discovery, discovery_files, discovery_manifest
from extensions.configuration import ExtensionRoots
from tests.extension_host import package_fixture as packages


@pytest.mark.parametrize("change", ["syntax", "unknown", "owner", "missing"])
def test_invalid_manifest_remains_visible(tmp_path: Path, change: str) -> None:
    """Bad metadata does not remove the package row or expose its invalid input."""
    directory = packages.write_package(tmp_path)
    manifest = directory / "extension.json"
    if change == "syntax":
        manifest.write_bytes(b"{sensitive broken input")
    if change == "unknown":
        manifest.write_bytes(b'{"secret": "sensitive"}')
    if change == "owner":
        original = packages.read_manifest(directory)
        packages.save_manifest(directory, original.model_copy(update={"extension_id": "bad owner"}))
    if change == "missing":
        manifest.rename(directory / "removed-manifest")
    candidate = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,))).entries[0]
    assert candidate.issue is not None
    assert candidate.issue.code == "invalid_manifest"
    assert "sensitive" not in candidate.issue.detail


def test_incompatible_api_keeps_identity(tmp_path: Path) -> None:
    """The user can identify a valid declaration that needs another host API."""
    directory = packages.write_package(tmp_path)
    manifest = packages.read_manifest(directory)
    packages.save_manifest(directory, manifest.model_copy(update={"api_requires": ">=99"}))
    candidate = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,))).entries[0]
    assert candidate.issue is not None and candidate.issue.code == "incompatible_api"
    assert candidate.manifest is not None and candidate.manifest.extension_id == packages.OWNER


@pytest.mark.parametrize("change", ["backend", "test", "asset", "linked_asset", "linked_directory"])
def test_invalid_package_files_are_reported(tmp_path: Path, change: str) -> None:
    """Every declared file stays inside the package and keeps its declared bytes."""
    directory = packages.write_package(tmp_path, web=change in {"asset", "linked_asset"})
    _change_file(directory, change)
    candidate = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,))).entries[0]
    assert candidate.issue is not None
    assert candidate.issue.code == "invalid_files"


def test_manifest_byte_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The host reads at most its bound plus one byte before rejecting a manifest."""
    packages.write_package(tmp_path)
    monkeypatch.setattr(discovery_manifest, "MAX_MANIFEST_BYTES", 10)
    scanned = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert scanned.entries[0].issue is not None
    assert scanned.entries[0].issue.code == "invalid_manifest"


@pytest.mark.parametrize("bound", ["MAX_PACKAGE_BYTES", "MAX_PACKAGE_FILES"])
def test_package_inventory_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str) -> None:
    """Both total bytes and path count stop oversized package scans."""
    packages.write_package(tmp_path)
    monkeypatch.setattr(discovery_files, bound, 1)
    scanned = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert scanned.entries[0].issue is not None
    assert scanned.entries[0].issue.code == "invalid_files"


def test_package_count_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial root listing is never accepted as a complete catalog."""
    packages.write_package(tmp_path)
    monkeypatch.setattr(discovery, "MAX_PACKAGE_ENTRIES", 0)
    scanned = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert not scanned.entries
    assert scanned.root_issues[0].issue.code == "root_unavailable"


def _change_file(directory: Path, change: str) -> None:
    manifest = packages.read_manifest(directory)
    if change in {"backend", "test"}:
        selected = packages.BACKEND_PATH if change == "backend" else manifest.e2e[0].path
        (directory / selected).rename(directory / "removed-file")
    if change == "asset":
        (directory / manifest.assets[0].path).write_bytes(b"changed bytes")
    if change == "linked_asset":
        path = directory / manifest.assets[0].path
        target = directory.parent / "outside.js"
        path.rename(target)
        path.symlink_to(target)
    if change == "linked_directory":
        (directory / "external").symlink_to(directory.parent, target_is_directory=True)
