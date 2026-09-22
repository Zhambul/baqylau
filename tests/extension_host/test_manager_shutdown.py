# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain exclusive process ownership until readers and preparation have stopped."""

from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from extensions.manager_contract import ManagerCleanupError
from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import RuntimeBusyError
from tests.extension_host import manager_fixture as fixtures

DRAIN_SECONDS = 0.01


def test_reader_timeout_keeps_native_ownership(tmp_path: Path) -> None:
    """A caller can retry close after the final admitted reader leaves."""
    with closing(fixtures.open_manager(tmp_path, drain_seconds=DRAIN_SECONDS)) as host:
        host.finish()
        with host.runtime.preparation.registry.read_snapshot(), pytest.raises(ManagerCleanupError, match="admitted"):
            host.close()
        assert host.controller.read_state().phase == "closing"
        with pytest.raises(RuntimeBusyError):
            FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
        host.close()
        with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()):
            assert host.controller.read_state().phase == "closed"


def test_shutdown_interrupts_unpublished_set(tmp_path: Path) -> None:
    """A ready result is not committed when shutdown replaces its engine boundary."""
    with ExitStack() as cleanup:
        host = cleanup.enter_context(closing(fixtures.open_manager(tmp_path)))
        host.finish()
        previous = host.controller.read_state().lifecycle.committed_runtime
        host.controller.submit_operation(host.proposal("unpublished"))
        host.wait_ready()
        host.close()
        operation = host.controller.read_operation("unpublished")
        assert operation is not None
        assert operation.status == "failed"
        assert operation.failure is not None and operation.failure.code == "interrupted"
        assert host.controller.read_state().lifecycle.committed_runtime == previous
