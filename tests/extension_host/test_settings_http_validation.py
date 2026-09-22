# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject malformed settings requests before daemon ownership or storage access."""

from http import HTTPStatus
from pathlib import Path

import pytest

from api.extensions.settings_models import SettingsChangeRequest
from tests.extension_api import samples
from tests.extension_host import catalog_fixture, settings_control_fixture as fixture

PATH = "/api/extensions/test.external/settings"


@pytest.mark.parametrize("scope", [
    "private invalid JSON", '{"kind":"workspace","workspace_id":false}',
    '{"kind":"installation","secret":"private-query-value"}',
])
def test_settings_query_rejects_invalid_scope(tmp_path: Path, scope: str) -> None:
    """The typed JSON query decoder gives no authority to extra or malformed fields."""
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.get(PATH, params={"scope": scope})
        assert response.status_code == HTTPStatus.BAD_REQUEST
        assert "private" not in response.text


@pytest.mark.parametrize(("field", "invalid"), [
    ("expected_settings_revision", True), ("expected_revision", "1"),
    ("manager_id", "client-manager"), ("action", "reload"), ("scope", "installation"),
])
def test_settings_write_rejects_bad_fields(tmp_path: Path, field: str, *, invalid: str | bool) -> None:
    """The body keeps revisions, scope, action, and runtime authority strict."""
    document = request().model_dump(mode="json")
    document[field] = invalid
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.put(PATH, json=document)
        assert response.status_code == HTTPStatus.BAD_REQUEST


def test_settings_reset_needs_explicit_document(tmp_path: Path) -> None:
    """An omitted value must not become an accidental reset."""
    document = request().model_dump(mode="json")
    document.pop("document")
    with catalog_fixture.web_client(tmp_path) as client:
        assert client.put(PATH, json=document).status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize(("headers", "status"), [
    ({"Origin": "https://foreign.example", "Content-Type": "application/json"}, HTTPStatus.FORBIDDEN),
    ({"Content-Type": "text/plain"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE),
])
def test_settings_write_checks_origin_and_json(tmp_path: Path, headers: dict[str, str], status: HTTPStatus) -> None:
    """No cross-origin form or foreign JSON body can invoke settings mutation."""
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.put(PATH, content=request().model_dump_json(), headers=headers)
        assert response.status_code == status


def test_settings_requests_do_not_start_an_owner(tmp_path: Path) -> None:
    """Valid reads and writes cannot create a manager as an HTTP dependency."""
    with catalog_fixture.web_client(tmp_path) as client:
        assert client.get(PATH).status_code == HTTPStatus.SERVICE_UNAVAILABLE
        response = client.put(PATH, json=request().model_dump(mode="json"))
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert not (tmp_path / "extension-runtime.lock").exists()


def request() -> SettingsChangeRequest:
    """Build a valid public reset body without reading a live daemon.

    Returns:
        The exact typed shape which request-only application tests validate.

    """
    return SettingsChangeRequest(
        request_id="test-settings", expected_revision=1, expected_catalog_revision=0,
        expected_settings_revision=0, package_digest=samples.DIGEST, scope=fixture.INSTALLATION, document=None,
    )
