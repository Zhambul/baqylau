# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep failed discovery distinct from package removal and normal recovery."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from extensions.models.catalog import DiscoveryIssue, PackageScan, RootDiscoveryIssue
from tests.extension_host import catalog_fixture as catalog, package_fixture as packages

DIGEST_LENGTH = 64
MISSING_DIGEST = "0" * DIGEST_LENGTH


def test_recovered_root_clears_prior_error(tmp_path: Path) -> None:
    """A complete later scan replaces retained rows and clears root failures."""
    directory = packages.write_package(tmp_path / "packages")
    service = catalog.service(tmp_path)
    first = service.rescan_packages(0)
    directory.parent.rename(tmp_path / "moved")
    directory.parent.write_bytes(b"not a directory")
    failed = service.rescan_packages(first.snapshot.revision)
    assert failed.snapshot.entries == first.snapshot.entries
    assert failed.snapshot.root_issues
    directory.parent.rename(tmp_path / "old-root-file")
    recovered = service.rescan_packages(failed.snapshot.revision)
    assert recovered.accepted and not recovered.snapshot.entries
    assert not recovered.snapshot.root_issues
    assert catalog.repository(tmp_path).read_extension_catalog() == recovered.snapshot


def test_invalid_entry_survives_restart(tmp_path: Path) -> None:
    """Invalid file metadata stays visible without being retained as a valid manifest."""
    directory = packages.write_package(tmp_path / "packages")
    (directory / packages.BACKEND_PATH).rename(directory / "missing-backend")
    service = catalog.service(tmp_path)
    accepted = service.rescan_packages(0)
    assert accepted.snapshot.entries[0].issue is not None
    assert accepted.snapshot.entries[0].manifest == packages.read_manifest(directory)
    assert catalog.repository(tmp_path).read_extension_catalog() == accepted.snapshot
    assert service.repository.retained_extension_manifest(MISSING_DIGEST) is None


def test_duplicate_root_failures_are_rejected() -> None:
    """A repeated root key cannot reach SQL replacement."""
    failure = RootDiscoveryIssue(root_path="/test", issue=DiscoveryIssue(
        code="root_unavailable", detail="Test root failure.",
    ))
    with pytest.raises(ValidationError, match="unique"):
        PackageScan(root_issues=(failure, failure))
