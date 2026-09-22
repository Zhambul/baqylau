# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep package discovery, revisions, and retained declarations durable."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from extensions.models.catalog import DiscoveryIssue, PackageScan, RootDiscoveryIssue
from tests.extension_host import catalog_fixture as catalog, package_fixture as packages

PACKAGE_DIRECTORY = "packages"


def test_catalog_survives_repository_restart(tmp_path: Path) -> None:
    """A new repository reads the same accepted package and schema declarations."""
    directory = packages.write_package(tmp_path / PACKAGE_DIRECTORY)
    service = catalog.service(tmp_path)
    accepted = service.rescan_packages(0)
    restarted = catalog.repository(tmp_path)
    assert accepted.accepted and accepted.snapshot.revision == 1
    assert restarted.read_extension_catalog() == accepted.snapshot
    digest = accepted.snapshot.entries[0].package_digest
    assert digest is not None
    assert restarted.retained_extension_manifest(digest) == packages.read_manifest(directory)


def test_unchanged_scan_keeps_revision(tmp_path: Path) -> None:
    """Repeated scans and process restarts do not create false catalog changes."""
    packages.write_package(tmp_path / PACKAGE_DIRECTORY)
    service = catalog.service(tmp_path)
    first = service.rescan_packages(0)
    second = service.rescan_packages(first.snapshot.revision)
    assert second.accepted
    assert second.snapshot == first.snapshot


def test_stale_scan_does_not_read_files(tmp_path: Path) -> None:
    """An obsolete management request is rejected before the file scanner runs."""
    packages.write_package(tmp_path / PACKAGE_DIRECTORY)
    scanner = catalog.RecordedScanner()
    service = catalog.service(tmp_path, scanner)
    accepted = service.rescan_packages(0)
    rejected = service.rescan_packages(0)
    assert accepted.accepted
    assert not rejected.accepted
    assert rejected.snapshot == accepted.snapshot
    assert scanner.calls == 1


def test_removed_package_keeps_prior_manifest(tmp_path: Path) -> None:
    """Removal changes discovery, not the retained schema and package history."""
    directory = packages.write_package(tmp_path / PACKAGE_DIRECTORY)
    service = catalog.service(tmp_path)
    first = service.rescan_packages(0)
    directory.rename(tmp_path / "removed-package")
    removed = service.rescan_packages(first.snapshot.revision)
    assert removed.accepted and not removed.snapshot.entries
    digest = first.snapshot.entries[0].package_digest
    assert digest is not None
    assert service.repository.retained_extension_manifest(digest) is not None


def test_failed_root_preserves_catalog_rows(tmp_path: Path) -> None:
    """An unreadable root reports its failure without replacing the last complete catalog."""
    packages.write_package(tmp_path / PACKAGE_DIRECTORY)
    service = catalog.service(tmp_path)
    first = service.rescan_packages(0)
    failure = RootDiscoveryIssue(root_path=str(tmp_path / PACKAGE_DIRECTORY), issue=DiscoveryIssue(
        code="root_unavailable", detail="Test root failure.",
    ))
    second = service.repository.replace_extension_catalog(first.snapshot.revision, PackageScan(root_issues=(failure,)))
    assert second.accepted
    assert second.snapshot.entries == first.snapshot.entries
    assert second.snapshot.root_issues == (failure,)
    assert catalog.repository(tmp_path).read_extension_catalog() == second.snapshot


def test_concurrent_scans_accept_one_revision(tmp_path: Path) -> None:
    """The real write transaction compares revision before changing any rows."""
    packages.write_package(tmp_path / PACKAGE_DIRECTORY)
    service = catalog.service(tmp_path)
    scan = service.scanner.scan_packages(service.roots)
    service.repository.read_extension_catalog()
    replace_catalog = partial(service.repository.replace_extension_catalog, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(replace_catalog, (scan, scan)))
    assert sum(result.accepted for result in outcomes) == 1
    assert all(result.snapshot.revision == 1 for result in outcomes)
    assert service.catalog_snapshot().entries == scan.entries
