# Copyright (c) 2026 Zhambyl Yermagambet
"""Use actual private daemons for accepted settings, scope reads, reset, and retry."""

from pathlib import Path

from tests import terminal_pty_waits
from tests.extension_host import (
    lifecycle_dependency_fixture as packages,
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
    settings_control_fixture as scopes,
    settings_http_fixture as fixture,
)

OWNER = package_fixture.OWNER
CUSTOM = '"http workspace value"'


def test_http_settings_save_and_reset(tmp_path: Path) -> None:
    """PUT records an operation and GET returns only the accepted scope selection."""
    packages.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_pty_waits.wait_until(lambda: client.extensions.lifecycle.state().phase == "running")
        selected = fixture.request(client, OWNER, "workspace", CUSTOM, scopes.WORKSPACE)
        admitted = client.extensions.settings.change(OWNER, selected)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"
        assert client.extensions.settings.read(OWNER, scopes.WORKSPACE).settings.effective.json_text == CUSTOM
        assert client.extensions.settings.change(OWNER, selected).status == "replayed"
        selected = fixture.request(client, OWNER, "reset", None, scopes.WORKSPACE)
        admitted = client.extensions.settings.change(OWNER, selected)
        lifecycle.wait_operation(client, admitted.operation.operation_id)
        current = client.extensions.settings.read(OWNER, scopes.WORKSPACE).settings
        assert current.override is None and current.effective == current.definition.defaults


def test_http_settings_restore_after_restart(tmp_path: Path) -> None:
    """A new manager uses the saved override and replays the original request."""
    packages.write_settings_package(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        terminal_pty_waits.wait_until(lambda: client.extensions.lifecycle.state().phase == "running")
        selected = fixture.request(client, OWNER, "saved", CUSTOM)
        admitted = client.extensions.settings.change(OWNER, selected)
        lifecycle.wait_operation(client, admitted.operation.operation_id)
    with process_fixture.running_catalog(tmp_path) as client:
        assert client.extensions.settings.change(OWNER, selected).status == "replayed"
        current = client.extensions.settings.read(OWNER).settings
        assert current.effective.json_text == CUSTOM
        assert current.settings_revision == 1
