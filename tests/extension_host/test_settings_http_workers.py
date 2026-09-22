# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify private worker settings publication and rollback through the HTTP API."""

import os
from pathlib import Path
from typing import Final

from tests import terminal_pty_waits
from tests.extension_api import service_samples
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    manager_process_fixture,
    process_fixture,
    settings_http_fixture as fixture,
    settings_worker_fixture as workers,
)

OWNER = service_samples.ALPHA
ENABLE: Final = "enable"


def test_http_worker_receives_committed_settings(tmp_path: Path, runtime_wheels: Path) -> None:
    """A settings operation replaces the worker with the exact accepted values."""
    workers.write_worker(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        selected = lifecycle.lifecycle_request(client, OWNER, ENABLE, ENABLE)
        admitted = client.extensions.lifecycle.change(OWNER, selected)
        lifecycle.wait_operation(client, admitted.operation.operation_id)
        previous = manager_process_fixture.worker_id(tmp_path)
        changed = fixture.request(client, OWNER, "new-settings", '"worker setting"')
        admitted = client.extensions.settings.change(OWNER, changed)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"
        assert manager_process_fixture.replacement_worker(tmp_path, previous) != previous
        assert workers.activated(tmp_path).settings == changed.document
        assert workers.activated(tmp_path).settings_revision == 1
        terminal_pty_waits.wait_for_process_exit(previous)


def test_http_worker_refusal_keeps_old_settings(tmp_path: Path, runtime_wheels: Path) -> None:
    """A failed activation cannot publish its runtime, override, or private failure text."""
    workers.write_worker(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        selected = lifecycle.lifecycle_request(client, OWNER, ENABLE, ENABLE)
        admitted = client.extensions.lifecycle.change(OWNER, selected)
        lifecycle.wait_operation(client, admitted.operation.operation_id)
        previous = manager_process_fixture.worker_id(tmp_path)
        changed = fixture.request(client, OWNER, "refused-settings", workers.REFUSE)
        admitted = client.extensions.settings.change(OWNER, changed)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "failed"
        assert client.extensions.lifecycle.state().active_runtime == workers.activated(tmp_path).runtime_revision
        os.kill(previous, 0)
        assert client.extensions.settings.read(OWNER).settings.settings_revision == 0
        assert client.extensions.settings.read(OWNER).settings.effective == (
            workers.activated(tmp_path).settings
        )
        assert "feature rejected private settings" not in client.extensions.lifecycle.operation(
            admitted.operation.operation_id,
        ).model_dump_json()
        terminal_pty_waits.wait_for_process_exit(int((tmp_path / workers.FAILED).read_text(encoding="utf-8")))
