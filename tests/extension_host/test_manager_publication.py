# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep owned candidates across reader delays and failed durable publication."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from extensions.models.lifecycle_state import ManagerClaim
from repository.impl.sqlite.connection import SqliteDatabase
from tests.extension_host import manager_fixture as fixtures, runtime_commit_fixture as commits


def test_reader_delays_candidate_publication(tmp_path: Path) -> None:
    """A ready candidate cannot replace a runtime while a read still owns it."""
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        previous = host.controller.read_state()
        host.controller.submit_operation(host.proposal("reload"))
        host.wait_ready()
        with host.runtime.preparation.registry.read_snapshot() as selected:
            assert host.controller.publish_ready().status == "busy"
            assert host.controller.read_state().active_runtime == previous.active_runtime
            assert selected.snapshot.directory.runtime_revision == previous.active_runtime
        host.finish()
        assert host.controller.read_state().active_runtime == "runtime-reload"


def test_commit_failure_retains_ready_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry the same prepared set after rollback, with no second activation."""
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        previous = host.controller.read_state()
        host.controller.submit_operation(host.proposal("reload"))
        host.wait_ready()
        fault = commits.fault_connection(host.runtime.store.database)
        with closing(fault) as connection, monkeypatch.context() as patch:
            patch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
            with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
                host.controller.publish_ready()
            assert host.controller.read_state().active_runtime == previous.active_runtime
            assert host.controller.read_state().phase == "awaiting_boundary"
            assert host.controller.publish_ready().status == "published"
        host.wait_cleanup()
        assert host.controller.read_state().active_runtime == "runtime-reload"


def test_stale_manager_cannot_publish(tmp_path: Path) -> None:
    """A changed stored generation fences the ready candidate and keeps the old set."""
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        previous = host.controller.read_state()
        host.controller.submit_operation(host.proposal("stale"))
        host.wait_ready()
        selected = host.controller.read_state().lifecycle
        host.runtime.store.claim_extension_manager(ManagerClaim(
            expected_revision=selected.revision, manager_id="new-generation", claimed_at=2,
        ))
        assert host.controller.publish_ready().status == "fenced"
        host.wait_cleanup()
        assert host.controller.read_state().active_runtime == previous.active_runtime
        assert not host.controller.publish_ready().allow_processing
        assert host.controller.read_state().lifecycle.committed_runtime == previous.lifecycle.committed_runtime
