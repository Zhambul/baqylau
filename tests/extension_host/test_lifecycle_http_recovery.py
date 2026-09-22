# Copyright (c) 2026 Zhambyl Yermagambet
"""Use public lifecycle requests across failure, removal, and daemon restart."""

from pathlib import Path
from typing import Final, Literal

import pytest

from sdk.transport import ApiFailureError
from tests.extension_host import lifecycle_http_fixture as fixture, package_fixture, process_fixture

OWNER = package_fixture.OWNER
SUCCEEDED = "succeeded"
ENABLE: Final = "enable"
PACKAGES = "packages"
ACTIONS: tuple[Literal["enable", "disable", "reload"], ...] = (ENABLE, "disable", ENABLE)


def test_http_retry_survives_daemon_restart(tmp_path: Path) -> None:
    """Restart restores the selection and does not admit the old request again."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, ENABLE, "before-restart")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
    (tmp_path / PACKAGES).rename(tmp_path / "removed-source")
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.lifecycle_request(client, OWNER, "disable", "wait-for-restoration")
        replayed = client.extensions.lifecycle.change(OWNER, request)
        assert replayed.status == "replayed"
        assert replayed.operation.operation_id == admitted.operation.operation_id
        assert client.extensions.lifecycle.state().committed_packages[0].extension_info.package_digest == (
            request.package_digest
        )


def test_http_failed_enable_can_be_disabled(tmp_path: Path) -> None:
    """Missing backend dependencies cause failed preparation, not lost requested state."""
    package_fixture.write_package(tmp_path / PACKAGES)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, ENABLE, "missing-environment")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == "failed"
        assert client.extensions.lifecycle.state().requested[0].enabled
        request = fixture.lifecycle_request(client, OWNER, "disable", "clear-failed-enable")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        assert not client.extensions.lifecycle.state().requested[0].enabled
        assert not client.extensions.lifecycle.state().committed_packages


def test_http_reenable_selects_a_new_runtime(tmp_path: Path) -> None:
    """An explicit re-enable has a fresh runtime identity and no duplicate package."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        revisions: set[str | None] = set()
        for action in ACTIONS:
            request = fixture.lifecycle_request(client, OWNER, action, f"change-{len(revisions)}")
            admitted = client.extensions.lifecycle.change(OWNER, request)
            assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
            revisions.add(client.extensions.lifecycle.state().active_runtime)
        assert len(revisions) == len(ACTIONS)
        assert len(client.extensions.lifecycle.state().committed_packages) == 1


def test_http_unknown_operation_is_not_found(tmp_path: Path) -> None:
    """An absent operation is not a fabricated pending or completed request."""
    with (
        process_fixture.running_catalog(tmp_path) as client,
        pytest.raises(ApiFailureError, match=r"404.*operation not found"),
    ):
        client.extensions.lifecycle.operation("not-admitted")
