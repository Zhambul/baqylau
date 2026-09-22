# Copyright (c) 2026 Zhambyl Yermagambet
"""Close actual worker resources and expose prior shutdown evidence after restart."""

from pathlib import Path

import pytest

from tests.extension_host import (
    ordered_processing_fixture as ordered,
    process_fixture,
    shutdown_process_fixture as fixtures,
)


@pytest.mark.parametrize("mode", ["hang", "wrong_runtime", "jobs"])
def test_uncertain_worker_shutdown_and_restart(tmp_path: Path, runtime_wheels: Path, mode: str) -> None:
    """A lost reply, wrong runtime, or pending job cannot keep drained resources open."""
    ordered.installed(tmp_path, runtime_wheels)
    fixtures.configure(tmp_path, mode)
    with process_fixture.running_catalog(tmp_path) as client:
        fixtures.enable_fault(client)
        owned = fixtures.worker_processes(tmp_path, expected=1)
    record = fixtures.require_closed(tmp_path, owned)
    fixtures.require_owner_issue(record, mode)
    with process_fixture.running_catalog(tmp_path) as client:
        assert client.extensions.lifecycle.state().last_shutdown == record
        assert all(not worker.is_running() for worker in owned)
