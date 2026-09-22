# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise actual private worker activation through user lifecycle HTTP requests."""

import os
from pathlib import Path

from tests.extension_api import service_samples
from tests.extension_host import (
    lifecycle_http_fixture as fixture,
    manager_process_fixture,
    package_fixture,
    process_fixture,
)

OWNER = service_samples.ALPHA
SUCCEEDED = "succeeded"


def test_http_starts_and_stops_a_private_worker(tmp_path: Path, runtime_wheels: Path) -> None:
    """The public request controls a separate process and releases it on disable."""
    manager_process_fixture.marker_peer(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "start-worker")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        worker = manager_process_fixture.worker_id(tmp_path)
        assert worker not in {os.getpid(), client.application.wait_until_ready().process_id}
        request = fixture.lifecycle_request(client, OWNER, "disable", "stop-worker")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        manager_process_fixture.require_stopped(tmp_path, worker)


def test_failed_http_reload_keeps_old_worker(tmp_path: Path, runtime_wheels: Path) -> None:
    """Preparation failure remains visible without replacing the running generation."""
    source = manager_process_fixture.marker_peer(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        request = fixture.lifecycle_request(client, OWNER, "enable", "start")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == SUCCEEDED
        previous = client.extensions.lifecycle.state().active_runtime
        package_fixture.write_file(source, "src/peer_backend.py", b"raise RuntimeError('private feature failure')\n")
        client.extensions.rescan(client.extensions.catalog().revision)
        request = fixture.lifecycle_request(client, OWNER, "reload", "failed-update")
        admitted = client.extensions.lifecycle.change(OWNER, request)
        assert fixture.wait_operation(client, admitted.operation.operation_id).status == "failed"
        assert client.extensions.lifecycle.state().active_runtime == previous
    manager_process_fixture.require_stopped(tmp_path, manager_process_fixture.worker_id(tmp_path))
