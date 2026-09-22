# Copyright (c) 2026 Zhambyl Yermagambet
"""Prove that durable acceptance precedes the active registry pointer change."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from extensions.models.lifecycle_state import ManagerClaim
from extensions.registry_snapshot import prepare_snapshot
from repository.impl.sqlite.connection import SqliteDatabase
from tests.extension_host import lifecycle_fixture as lifecycle, runtime_commit_fixture as fixtures

ACCEPTED = "accepted"


def test_publication_commits_pending_runtime(tmp_path: Path) -> None:
    """The two heads select the same complete runtime after publication."""
    case = fixtures.accepted(tmp_path)
    assert case.registry.publish_snapshot(0, case.snapshot, case.commit).status == ACCEPTED
    with case.registry.read_snapshot() as selected:
        assert selected.revision == 1
        assert selected.snapshot.runtime_selection() == case.store.read_extension_lifecycle().committed_runtime
    reopened = lifecycle.repository(tmp_path).read_extension_lifecycle()
    assert reopened.committed_runtime == case.snapshot.runtime_selection()


def test_busy_or_stale_registry_cannot_commit(tmp_path: Path) -> None:
    """A refused memory publication leaves the durable operation pending."""
    case = fixtures.accepted(tmp_path)
    before = case.store.read_extension_lifecycle()
    with case.registry.read_snapshot():
        assert case.registry.publish_snapshot(0, case.snapshot, case.commit).status == "busy"
    assert case.registry.publish_snapshot(1, case.snapshot, case.commit).status == "stale"
    assert case.store.read_extension_lifecycle() == before
    assert case.registry.publish_snapshot(0, case.snapshot, case.commit).status == ACCEPTED


def test_changed_candidate_cannot_commit(tmp_path: Path) -> None:
    """A valid but different runtime cannot replace the accepted operation."""
    case = fixtures.accepted(tmp_path)
    before = case.store.read_extension_lifecycle()
    with pytest.raises(ValueError, match="accepted lifecycle candidate"):
        case.registry.publish_snapshot(0, prepare_snapshot(0, "changed", ()), case.commit)
    assert case.store.read_extension_lifecycle() == before
    fixtures.require_initial(case.registry)


def test_old_manager_cannot_publish(tmp_path: Path) -> None:
    """Stored manager replacement also rejects the old in-memory candidate."""
    case = fixtures.accepted(tmp_path)
    replaced = case.store.claim_extension_manager(ManagerClaim(
        expected_revision=case.commit.operation.accepted_revision,
        manager_id="replacement", claimed_at=lifecycle.NOW + 1,
    ))
    assert replaced.accepted
    assert case.registry.publish_snapshot(0, case.snapshot, case.commit).status == "stale"
    assert case.store.read_extension_lifecycle() == replaced.state
    fixtures.require_initial(case.registry)


def test_failed_sql_commit_keeps_old_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed COMMIT rolls back, releases the writer, and permits a safe retry."""
    case = fixtures.accepted(tmp_path)
    before = case.store.read_extension_lifecycle()
    with closing(fixtures.fault_connection(case.store.database)) as connection:
        monkeypatch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.registry.publish_snapshot(0, case.snapshot, case.commit)
        assert not connection.in_transaction
        assert case.store.read_extension_lifecycle() == before
        fixtures.require_initial(case.registry)
        assert case.registry.publish_snapshot(0, case.snapshot, case.commit).status == ACCEPTED


def test_reader_waits_for_joined_publication(tmp_path: Path) -> None:
    """No new reader can enter between the durable commit and pointer replacement."""
    case = fixtures.accepted(tmp_path)
    held = fixtures.HeldCommit(case.commit)
    with ThreadPoolExecutor(max_workers=2) as pool, ExitStack() as cleanup:
        cleanup.callback(held.released.set)
        pending = (
            pool.submit(case.registry.publish_snapshot, 0, case.snapshot, held),
            pool.submit(held.read_after_commit, case.registry),
        )
        assert held.read_started.wait(5)
        assert case.store.read_extension_lifecycle().committed_runtime == case.snapshot.runtime_selection()
        assert not pending[1].done()
        held.released.set()
        assert pending[0].result(timeout=5).status == ACCEPTED
        assert pending[1].result(timeout=5) == case.snapshot.runtime_selection()
