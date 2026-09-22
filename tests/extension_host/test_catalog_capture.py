# Copyright (c) 2026 Zhambyl Yermagambet
"""Check fixed package copies through the normal application catalog path."""

from pathlib import Path

from api.extensions.models import ExtensionCatalogResponse
from extensions.artifacts import FilesystemExtensionArtifacts
from tests.extension_host import catalog_fixture as catalog, package_fixture as packages, process_fixture

ARTIFACT_DIRECTORY = "extension-artifacts"
CATALOG_PATH = "/api/extensions"


def test_application_captures_before_catalog(tmp_path: Path) -> None:
    """A valid application catalog row has a checked copy outside its source root."""
    source = packages.write_package(tmp_path / "packages")
    store = FilesystemExtensionArtifacts(tmp_path / ARTIFACT_DIRECTORY)
    with catalog.web_client(tmp_path) as client:
        response = ExtensionCatalogResponse.model_validate_json(client.get(CATALOG_PATH).content)
        digest = response.entries[0].package_digest
        assert digest is not None
        assert store.read_artifact(digest).manifest == packages.read_manifest(source)
        source.rename(tmp_path / "removed-source")
        assert client.get(CATALOG_PATH).content == response.model_dump_json().encode()
        assert store.read_artifact(digest).package_digest == digest


def test_capture_error_is_visible_and_recoverable(tmp_path: Path) -> None:
    """A bad artifact root reports a package error, then a rescan can recover."""
    packages.write_package(tmp_path / "packages")
    root = tmp_path / ARTIFACT_DIRECTORY
    root.write_bytes(b"not a directory")
    with catalog.web_client(tmp_path) as client:
        first = ExtensionCatalogResponse.model_validate_json(client.get(CATALOG_PATH).content)
        assert first.entries[0].issue is not None
        assert first.entries[0].issue.code == "capture_failed"
        root.rename(tmp_path / "old-root")
        response = client.post("/api/extensions/rescan", json={"expected_revision": first.revision})
        accepted = ExtensionCatalogResponse.model_validate_json(response.content)
        assert accepted.entries[0].issue is None
        assert accepted.revision == first.revision + 1


def test_private_daemon_retains_package_copy(tmp_path: Path) -> None:
    """The real application startup creates files only under its private data root."""
    source = packages.write_package(tmp_path / "packages")
    with process_fixture.running_catalog(tmp_path) as client:
        entry = client.extensions.catalog().entries[0]
        assert entry.issue is None and entry.package_digest is not None
        copied = tmp_path / ARTIFACT_DIRECTORY / entry.package_digest
        assert (copied / packages.BACKEND_PATH).read_bytes() == (source / packages.BACKEND_PATH).read_bytes()
        assert not (copied / "uninstalled_feature" / packages.MARKER_NAME).exists()
    assert copied.is_dir()
