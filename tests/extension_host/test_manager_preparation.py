# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare on a separate thread and retain each accepted request exactly once."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import patch

from tests.extension_host import manager_control_fixture as control, manager_fixture as fixtures, runtime_host_fixture


def test_startup_does_not_wait_for_preparation(tmp_path: Path) -> None:
    """Admission and reads remain available while the preparation thread is held."""
    runtime = runtime_host_fixture.host(tmp_path, claimed=False)
    preparation = control.ControlledPreparation(runtime.preparation)
    with ExitStack() as cleanup:
        host = cleanup.enter_context(closing(fixtures.start_manager(runtime, preparation)))
        cleanup.callback(preparation.released.set)
        assert preparation.entered.wait(5)
        assert host.controller.publish_ready().status == "preparing"
        assert not host.controller.publish_ready().allow_processing
        assert host.controller.submit_operation(host.proposal("busy")).status == "busy"
        preparation.released.set()
        host.finish()
        assert len(preparation.revisions) == 1


def test_stopped_preparation_cannot_publish(tmp_path: Path) -> None:
    """Shutdown requests cooperative stop before the held preparer creates workers."""
    runtime = runtime_host_fixture.host(tmp_path, claimed=False)
    preparation = control.ControlledPreparation(runtime.preparation)
    with ThreadPoolExecutor(max_workers=1) as pool, ExitStack() as cleanup:
        host = cleanup.enter_context(closing(fixtures.start_manager(runtime, preparation)))
        cleanup.callback(preparation.released.set)
        assert preparation.entered.wait(5)
        preparation.close_when_stopped(host.controller, pool)
        assert host.controller.read_state().lifecycle.committed_runtime is None
        assert not host.controller.publish_ready().allow_processing


def test_rejected_schedule_has_stored_failure(tmp_path: Path) -> None:
    """A failed executor does not leave an accepted operation pending forever."""
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        with patch.object(ThreadPoolExecutor, "submit", side_effect=RuntimeError("test closed executor")):
            assert host.controller.submit_operation(host.proposal("no-executor")).status == "accepted"
        host.finish("failed")
        operation = host.controller.read_operation("no-executor")
        assert operation is not None and operation.failure is not None
        assert operation.failure.code == "preparation_failed"
        assert host.controller.read_state().lifecycle.pending_operation is None
