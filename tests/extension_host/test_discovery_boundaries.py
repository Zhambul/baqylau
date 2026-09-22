# Copyright (c) 2026 Zhambyl Yermagambet
"""Check file types, executable identity, and complete root enumeration."""

import os
import stat
from pathlib import Path

import pytest

from extensions import discovery
from extensions.configuration import ExtensionRoots
from tests.extension_host import package_fixture as packages


@pytest.mark.parametrize("kind", ["link", "fifo"])
def test_manifest_requires_regular_file(tmp_path: Path, kind: str) -> None:
    """A manifest cannot block a reader or load bytes through a file link."""
    directory = packages.write_package(tmp_path)
    path = directory / "extension.json"
    moved = directory / "saved-manifest"
    path.rename(moved)
    if kind == "link":
        path.symlink_to(moved)
    else:
        os.mkfifo(path)
    scanned = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert scanned.entries[0].issue is not None
    assert scanned.entries[0].issue.code == "invalid_manifest"


def test_fifo_resource_cannot_block_scan(tmp_path: Path) -> None:
    """The file reader rejects a package pipe without waiting for a writer."""
    directory = packages.write_package(tmp_path)
    os.mkfifo(directory / "pipe")
    scanned = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert scanned.entries[0].issue is not None
    assert scanned.entries[0].issue.code == "invalid_files"


def test_executable_bits_change_package_digest(tmp_path: Path) -> None:
    """Equal file bytes with different execution permissions have different identity."""
    directory = packages.write_package(tmp_path)
    path = directory / packages.BACKEND_PATH
    scanner = discovery.FilesystemExtensionScanner()
    before = scanner.scan_packages(ExtensionRoots((tmp_path,)))
    path.chmod(path.stat().st_mode ^ stat.S_IXUSR)
    after = scanner.scan_packages(ExtensionRoots((tmp_path,)))
    assert before.entries[0].package_digest != after.entries[0].package_digest
    assert after.entries[0].issue is None


def test_root_limit_counts_regular_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignored root files also count toward the complete scan bound."""
    (tmp_path / "unrelated").write_bytes(b"data")
    monkeypatch.setattr(discovery, "MAX_PACKAGE_ENTRIES", 0)
    scanned = discovery.FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert not scanned.entries
    assert scanned.root_issues[0].issue.code == "root_unavailable"
