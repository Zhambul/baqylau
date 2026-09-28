# Copyright (c) 2026 Zhambyl Yermagambet
"""Inject failures after SQL writes and prove whole lifecycle rollback."""

import sqlite3
from pathlib import Path

import pytest

from extensions.models.lifecycle_state import ManagerClaim
from repository.impl.sqlite import extension_lifecycle_writes as writes
from repository.impl.sqlite.connection import SqliteDatabase
from tests import storage_reads
from tests.extension_host import lifecycle_fixture as fixtures, lifecycle_settings_fixture as settings


def test_failed_acceptance_rolls_back_all_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither runtime reservation nor intent can survive a failed head write."""
    store = fixtures.claimed_repository(tmp_path)
    proposed = fixtures.proposal(store, fixtures.install_package(tmp_path))
    before = store.read_extension_lifecycle()
    monkeypatch.setattr(writes, "write_head", _fail_head)
    with pytest.raises(RuntimeError, match="head write"):
        store.accept_extension_operation(proposed, fixtures.NOW)
    assert store.read_extension_lifecycle() == before
    assert store.read_extension_operation(proposed.operation_id) is None
    assert storage_reads.read_extension_runtime(store, proposed.candidate.runtime_revision) is None


def test_failed_commit_restores_pending_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings, operation outcome, runtime commit time, and head move together."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    proposed = settings.settings_proposal(store, package, settings.changed_overrides(package))
    admitted = store.accept_extension_operation(proposed, fixtures.NOW)
    assert admitted.operation is not None
    monkeypatch.setattr(writes, "write_head", _fail_head)
    with pytest.raises(RuntimeError, match="head write"):
        store.finish_extension_operation(fixtures.completion(admitted.operation))
    assert store.read_extension_lifecycle() == admitted.state
    assert store.read_extension_operation(proposed.operation_id) == admitted.operation
    _assert_not_committed(store.database)


def test_failed_claim_keeps_prior_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manager fencing and pending interruption are one transaction."""
    store = fixtures.claimed_repository(tmp_path)
    admitted = store.accept_extension_operation(fixtures.proposal(store), fixtures.NOW)
    claim = ManagerClaim(expected_revision=admitted.state.revision, manager_id="new-manager", claimed_at=fixtures.NOW)
    monkeypatch.setattr(writes, "write_head", _fail_head)
    with pytest.raises(RuntimeError, match="head write"):
        store.claim_extension_manager(claim)
    assert store.read_extension_lifecycle() == admitted.state
    assert store.read_extension_operation(fixtures.OPERATION) == admitted.operation


def _fail_head(connection: sqlite3.Connection, head: writes.LifecycleHead) -> None:
    assert connection.in_transaction and head.revision > 0
    message = "injected lifecycle head write failure"
    raise RuntimeError(message)


def _assert_not_committed(database: SqliteDatabase) -> None:
    with database.read() as connection:
        committed = connection.execute("SELECT committed_at FROM extension_runtime_revisions").fetchone()
    assert committed["committed_at"] is None
