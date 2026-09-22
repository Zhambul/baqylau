# Copyright (c) 2026 Zhambyl Yermagambet
"""Check discovery through the real private daemon and its public HTTP SDK."""

from pathlib import Path

from tests.extension_host import package_fixture as packages, process_fixture


def test_real_daemon_discovers_without_import(tmp_path: Path) -> None:
    """Startup, rescan, and restart use the real catalog without running the backend."""
    directory = packages.write_package(tmp_path / "packages")
    with process_fixture.running_catalog(tmp_path) as client:
        first = client.extensions.catalog()
        assert first.revision == 1 and first.entries[0].issue is None
        assert client.extensions.rescan(first.revision) == first
    with process_fixture.running_catalog(tmp_path) as client:
        assert client.extensions.catalog() == first
        assert client.application.wait_until_ready().process_id > 0
    assert not (directory / "uninstalled_feature" / packages.MARKER_NAME).exists()


def test_real_daemon_lists_valid_and_bad(tmp_path: Path) -> None:
    """A bad peer remains visible while the backend-free package stays valid."""
    packages.write_package(tmp_path / "packages", "test.web", web=True)
    invalid = packages.write_package(tmp_path / "packages", "test.invalid")
    (invalid / "extension.json").write_bytes(b"invalid private input")
    with process_fixture.running_catalog(tmp_path) as client:
        catalog = client.extensions.catalog()
        assert tuple(entry.extension_id for entry in catalog.entries) == (None, "test.web")
        assert catalog.entries[0].issue is not None
        assert catalog.entries[1].extension_id == "test.web"
        assert not catalog.entries[1].capabilities
