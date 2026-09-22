# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject malformed or cross-origin requests before a daemon can admit work."""

from http import HTTPStatus
from pathlib import Path

import pytest

from tests.extension_api import samples
from tests.extension_host import catalog_fixture

PATH = "/api/extensions/test.external/lifecycle"
CONFIRMED = "confirmed_dependents"
REVISION = "expected_revision"
type InvalidValue = str | bool | list[str | bool]


@pytest.mark.parametrize("origin", ["https://foreign.example", "null", "http://testserver/"])
def test_lifecycle_rejects_foreign_origin(tmp_path: Path, origin: str) -> None:
    """Even a malformed foreign request is refused by origin admission first."""
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.post(PATH, json={}, headers={"Origin": origin})
        assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("media_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_lifecycle_rejects_simple_request(tmp_path: Path, media_type: str) -> None:
    """Simple cross-origin form bodies cannot invoke a lifecycle change."""
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.post(PATH, content="{}", headers={"Content-Type": media_type})
        assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.parametrize(("field", "invalid"), [
    ("manager_id", "client-manager"), ("runtime_revision", "client-runtime"),
    (REVISION, "1"), (REVISION, True),
    (CONFIRMED, [False]), (CONFIRMED, "test.external"),
    (CONFIRMED, ["test.child", "test.child"]),
])
def test_lifecycle_rejects_bad_body(tmp_path: Path, field: str, *, invalid: InvalidValue) -> None:
    """HTTP converts the array container only; values and internal authority remain strict."""
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.post(PATH, json={
            "action": "enable", "request_id": "client-request", REVISION: 1,
            "expected_catalog_revision": 0, "package_digest": samples.DIGEST, field: invalid,
        })
        assert response.status_code == HTTPStatus.BAD_REQUEST


def test_valid_request_needs_a_daemon_owner(tmp_path: Path) -> None:
    """A request-only app can validate JSON without opening a worker or manager."""
    with catalog_fixture.web_client(tmp_path) as client:
        response = client.post(PATH, json={
            "action": "enable", "request_id": "client-request", REVISION: 1,
            "expected_catalog_revision": 0, "package_digest": samples.DIGEST, CONFIRMED: [],
        })
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert not (tmp_path / "extension-runtime.lock").exists()
