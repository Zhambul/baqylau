# Copyright (c) 2026 Zhambyl Yermagambet
"""Enable, disable, and reload through the real daemon's public request surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sdk.client_extension_lifecycle import preview_request
from tests.extension_host import lifecycle_http_fixture as fixture, package_fixture, process_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from sdk.client import BaqylauClient

OWNER = package_fixture.OWNER
SUCCEEDED = "succeeded"
RECENT_LIMIT = 10


def test_http_enable_disable_and_exact_retry(tmp_path: Path) -> None:
    """An accepted HTTP request changes the runtime only after the engine publishes it."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "http-enable")
        assert client.extensions.lifecycle.preview(OWNER, preview_request(request)).affected_extensions == (OWNER,)
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        assert client.extensions.lifecycle.change(OWNER, request).status == "replayed"
        request = fixture.lifecycle_request(client, OWNER, "disable", "http-disable")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED


def finish(client: BaqylauClient, action: Literal["enable", "disable"]) -> str:
    """Change the fixture package and wait until the operation ends.

    Returns:
        The operation ID.

    """
    request = fixture.lifecycle_request(client, OWNER, action, f"history-{action}")
    operation_id = client.extensions.lifecycle.change(OWNER, request).operation.operation_id
    fixture.wait_operation(client, operation_id)
    return operation_id


def test_http_recent_operations_are_newest_first(tmp_path: Path) -> None:
    """The history lists each finished operation, newest first, within the limit; startup restore is older."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        enabled = finish(client, "enable")
        disabled = finish(client, "disable")
        recent = client.extensions.lifecycle.recent_operations(RECENT_LIMIT).operations
        assert [operation.operation_id for operation in recent[:2]] == [disabled, enabled]
        assert len(client.extensions.lifecycle.recent_operations(1).operations) == 1


def test_http_reload_uses_new_selected_bytes(tmp_path: Path) -> None:
    """Reload prepares the digest selected after explicit discovery, not mutable source."""
    source = package_fixture.write_package(tmp_path / "packages", web=True)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "first")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        package_fixture.write_file(source, "new-file.txt", b"a new captured version")
        client.extensions.rescan(client.extensions.catalog().revision)
        request = fixture.lifecycle_request(client, OWNER, "reload", "second")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        selected = client.extensions.lifecycle.state().committed_packages[0]
        assert selected.extension_info.package_digest == request.package_digest
