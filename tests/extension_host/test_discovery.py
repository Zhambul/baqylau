# Copyright (c) 2026 Zhambyl Yermagambet
"""Discover complete external packages without executing their feature code."""

import shutil
from pathlib import Path

from extensions.configuration import ExtensionRoots
from extensions.discovery import FilesystemExtensionScanner
from tests.extension_host import package_fixture as packages

PEER_COUNT = 2


def test_backend_discovery_does_not_import(tmp_path: Path) -> None:
    """The declared factory is data during discovery, even when imports have effects."""
    directory = packages.write_package(tmp_path)
    scanned = FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    assert not scanned.root_issues
    assert len(scanned.entries) == 1
    candidate = scanned.entries[0]
    assert candidate.issue is None and candidate.package_digest is not None
    assert candidate.manifest == packages.read_manifest(directory)
    assert not (directory / "uninstalled_feature" / packages.MARKER_NAME).exists()


def test_web_package_needs_no_backend(tmp_path: Path) -> None:
    """A complete independent module is a valid backend-free package."""
    packages.write_package(tmp_path, web=True)
    scanned = FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path,)))
    candidate = scanned.entries[0]
    assert candidate.issue is None
    assert candidate.manifest is not None
    assert candidate.manifest.backend is None
    assert not candidate.manifest.capabilities


def test_digest_is_independent_of_location(tmp_path: Path) -> None:
    """Equal package files have equal identity at two different installation roots."""
    first = packages.write_package(tmp_path / "first")
    shutil.copytree(first, tmp_path / "second" / packages.OWNER)
    scanner = FilesystemExtensionScanner()
    left = scanner.scan_packages(ExtensionRoots((first.parent,)))
    right = scanner.scan_packages(ExtensionRoots((tmp_path / "second",)))
    assert left.entries[0].package_digest == right.entries[0].package_digest
    assert left.entries[0].source_path != right.entries[0].source_path


def test_all_owned_resources_affect_digest(tmp_path: Path) -> None:
    """Backend resources are included even when they are not web assets."""
    directory = packages.write_package(tmp_path)
    scanner = FilesystemExtensionScanner()
    before = scanner.scan_packages(ExtensionRoots((tmp_path,)))
    packages.write_file(directory, "data/resource.txt", b"feature data")
    after = scanner.scan_packages(ExtensionRoots((tmp_path,)))
    assert before.entries[0].package_digest != after.entries[0].package_digest
    assert after.entries[0].issue is None


def test_development_link_resolves_once(tmp_path: Path) -> None:
    """A root entry may link to a built package outside the installation directory."""
    source = packages.write_package(tmp_path / "build")
    installed = tmp_path / "installed"
    installed.mkdir()
    link = installed / "development"
    link.symlink_to(source, target_is_directory=True)
    scanned = FilesystemExtensionScanner().scan_packages(ExtensionRoots((installed,)))
    assert scanned.entries[0].issue is None
    assert scanned.entries[0].source_path == str(link)
    assert scanned.entries[0].resolved_path == str(source.resolve())


def test_duplicate_ids_reject_both_packages(tmp_path: Path) -> None:
    """A second root cannot override the first package with the same owner."""
    first = packages.write_package(tmp_path / "first")
    second = packages.write_package(tmp_path / "second")
    roots = ExtensionRoots((first.parent, second.parent))
    scanned = FilesystemExtensionScanner().scan_packages(roots)
    assert len(scanned.entries) == PEER_COUNT
    issues = tuple(entry.issue for entry in scanned.entries)
    assert all(issue is not None and issue.code == "duplicate_id" for issue in issues)


def test_root_failure_is_distinct_from_absence(tmp_path: Path) -> None:
    """A missing installation is empty; a configured non-directory is a visible failure."""
    scanner = FilesystemExtensionScanner()
    assert not scanner.scan_packages(ExtensionRoots((tmp_path / "absent",))).entries
    root = tmp_path / "file"
    root.write_bytes(b"not a directory")
    scanned = scanner.scan_packages(ExtensionRoots((root,)))
    assert not scanned.entries
    assert scanned.root_issues[0].issue.code == "root_unavailable"
