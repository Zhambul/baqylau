# Copyright (c) 2026 Zhambyl Yermagambet
"""Read real state after a private process stops at an activation boundary."""

from contextlib import closing
from pathlib import Path

import pytest

from extensions.models.lifecycle_state import ManagerClaim
from extensions.runtime_ownership import LOCK_NAME, FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import RuntimeBusyError
from tests.extension_host import lifecycle_fixture as lifecycle, runtime_process_fixture as processes


def test_another_process_cannot_take_live_lock(tmp_path: Path) -> None:
    """Process ownership is independent of a dashboard port or manager ID."""
    with processes.lock_process(tmp_path), pytest.raises(RuntimeBusyError):
        FilesystemRuntimeOwnership(tmp_path).acquire_runtime()


def test_process_death_releases_native_lock(tmp_path: Path) -> None:
    """The OS releases the lock; the next owner needs no stale PID-file removal."""
    with processes.lock_process(tmp_path) as process:
        inode = (tmp_path / LOCK_NAME).stat().st_ino
        process.kill()
        process.wait(timeout=5)
        with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()) as lease, lease.hold_ownership():
            assert (tmp_path / LOCK_NAME).stat().st_ino == inode


def test_fork_cannot_use_or_release_parent_lease(tmp_path: Path) -> None:
    """A forked copy neither enters a locked mutex nor releases its parent's file lock."""
    processes.interrupt_commit(tmp_path, "fork")
    with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()) as lease, lease.hold_ownership():
        assert (tmp_path / LOCK_NAME).is_file()


@pytest.mark.parametrize(("stage", "expected"), [("before", "interrupted"), ("after", "succeeded")])
def test_process_exit_at_commit_boundary(tmp_path: Path, stage: str, expected: str) -> None:
    """Restart selects only durable state, never a lost in-memory pointer."""
    processes.interrupt_commit(tmp_path, stage)
    with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()) as lease, lease.hold_ownership():
        store = lifecycle.repository(tmp_path)
        state = store.read_extension_lifecycle()
        claimed = store.claim_extension_manager(ManagerClaim(
            expected_revision=state.revision, manager_id="restart", claimed_at=lifecycle.NOW + 2,
        ))
        assert claimed.accepted and claimed.state.pending_operation is None
        assert (claimed.state.committed_runtime is not None) == (stage == "after")
        operation = store.read_extension_operation(lifecycle.OPERATION)
        assert operation is not None
        assert operation.status == expected
