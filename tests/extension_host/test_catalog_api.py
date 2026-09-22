# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the real application routes and lifespan with private package files."""

from http import HTTPStatus
from pathlib import Path

import pytest

from api.extensions.models import ExtensionCatalogResponse, RescanExtensionsRequest
from tests.extension_host import catalog_fixture as catalog, package_fixture as packages

CATALOG_PATH = "/api/extensions"
RESCAN_PATH = "/api/extensions/rescan"
EXPECTED_REVISION = 2


def test_startup_discovers_external_package(tmp_path: Path) -> None:
    """Application startup stores discovery before its public catalog is read."""
    directory = packages.write_package(tmp_path / "packages")
    with catalog.web_client(tmp_path) as client:
        response = client.get(CATALOG_PATH)
        body = ExtensionCatalogResponse.model_validate_json(response.content)
        assert response.status_code == HTTPStatus.OK
        assert body.revision == 1 and body.entries[0].extension_id == packages.OWNER
        assert body.entries[0].issue is None
        assert not (directory / "uninstalled_feature" / packages.MARKER_NAME).exists()
        assert all(field not in response.text for field in ('"settings"', '"schemas"', '"e2e"'))


def test_rescan_updates_catalog_on_request(tmp_path: Path) -> None:
    """GET does not read changing package files; POST checks and stores a new revision."""
    directory = packages.write_package(tmp_path / "packages")
    with catalog.web_client(tmp_path) as client:
        original = client.get(CATALOG_PATH).content
        packages.write_file(directory, "data.txt", b"changed source")
        assert client.get(CATALOG_PATH).content == original
        response = client.post(RESCAN_PATH, json={"expected_revision": 1})
        body = ExtensionCatalogResponse.model_validate_json(response.content)
        assert response.status_code == HTTPStatus.OK
        assert body.revision == EXPECTED_REVISION
        assert client.get(CATALOG_PATH).content == response.content


def test_stale_rescan_returns_conflict(tmp_path: Path) -> None:
    """A repeated obsolete management request cannot overwrite a newer scan."""
    packages.write_package(tmp_path / "packages")
    with catalog.web_client(tmp_path) as client:
        before = client.get(CATALOG_PATH).content
        response = client.post(RESCAN_PATH, json={"expected_revision": 0})
        assert response.status_code == HTTPStatus.CONFLICT
        assert client.get(CATALOG_PATH).content == before


@pytest.mark.parametrize("origin", ["https://other.example", "null", "http://testserver/", "http://other.testserver"])
def test_cross_origin_rescan_is_rejected(tmp_path: Path, origin: str) -> None:
    """Browser origin admission is enforced before a catalog scan."""
    with catalog.web_client(tmp_path) as client:
        response = client.post(RESCAN_PATH, json={"expected_revision": 0}, headers={"Origin": origin})
        assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("origin", [None, "http://testserver"])
def test_local_json_rescan_is_accepted(tmp_path: Path, origin: str | None) -> None:
    """Local API clients and same-origin browser JSON calls can rescan."""
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    document = RescanExtensionsRequest(expected_revision=0).model_dump_json()
    with catalog.web_client(tmp_path) as client:
        response = client.post(RESCAN_PATH, content=document, headers=headers)
        assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_simple_form_rescan_is_rejected(tmp_path: Path, content_type: str) -> None:
    """A cross-origin simple request cannot use the management endpoint."""
    with catalog.web_client(tmp_path) as client:
        response = client.post(RESCAN_PATH, content='{"expected_revision": 0}', headers={"Content-Type": content_type})
        assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
