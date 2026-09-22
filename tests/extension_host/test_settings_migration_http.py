# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the public HTTP SDK for migration, exact replay, and source-free restart."""

from pathlib import Path

from sdk.client import BaqylauClient
from tests import terminal_pty_waits
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    migration_host_fixture as fixture,
    process_fixture,
    settings_http_fixture as settings,
)

OLD = '{"label":"Private choice"}'
NEW = '{"title":"Private choice"}'


def test_http_migration_and_source_free_restart(tmp_path: Path, runtime_wheels: Path) -> None:
    """Upgrade through HTTP, retain exact retries, and restore converted values once."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        _enable_and_save(client, OLD)
        previous = int((tmp_path / fixture.ACTIVE).read_text(encoding="utf-8"))
        fixture.select_version(source, 2)
        client.extensions.rescan(client.extensions.catalog().revision)
        request = lifecycle.lifecycle_request(client, fixture.OWNER, "reload", "http-upgrade")
        admitted = client.extensions.lifecycle.change(fixture.OWNER, request)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"
        assert "Private choice" not in client.extensions.lifecycle.operation(
            admitted.operation.operation_id,
        ).model_dump_json()
        assert client.extensions.settings.read(fixture.OWNER).settings.effective.json_text == NEW
        terminal_pty_waits.wait_for_process_exit(previous)
    (tmp_path / "packages").rename(tmp_path / "removed-packages")
    previous = int((tmp_path / fixture.MIGRATING).read_text(encoding="utf-8"))
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_pty_waits.wait_until(lambda: _is_running(client))
        admitted = client.extensions.lifecycle.change(fixture.OWNER, request)
        assert admitted.status == "replayed"
        _require_activated_values(client, tmp_path)
        assert int((tmp_path / fixture.MIGRATING).read_text(encoding="utf-8")) == previous


def test_http_failed_migration_retains_settings(tmp_path: Path, runtime_wheels: Path) -> None:
    """A failed conversion remains one failed request after an exact HTTP retry."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        _enable_and_save(client, '{"label":"reject"}')
        fixture.select_version(source, 2)
        client.extensions.rescan(client.extensions.catalog().revision)
        request = lifecycle.lifecycle_request(client, fixture.OWNER, "reload", "failed-http-upgrade")
        admitted = client.extensions.lifecycle.change(fixture.OWNER, request)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "failed"
        assert client.extensions.settings.read(fixture.OWNER).settings.effective.json_text == '{"label":"reject"}'
        assert client.extensions.lifecycle.change(fixture.OWNER, request).status == "replayed"


def _enable_and_save(client: BaqylauClient, encoded: str) -> None:
    selected = lifecycle.lifecycle_request(client, fixture.OWNER, "enable", "http-enable")
    admitted = client.extensions.lifecycle.change(fixture.OWNER, selected)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"
    request = settings.request(client, fixture.OWNER, "old", encoded)
    admitted = client.extensions.settings.change(fixture.OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def _is_running(client: BaqylauClient) -> bool:
    return client.extensions.lifecycle.state().phase == "running"


def _require_activated_values(client: BaqylauClient, directory: Path) -> None:
    current = client.extensions.settings.read(fixture.OWNER).settings
    assert current.effective == fixture.activated(directory).settings
