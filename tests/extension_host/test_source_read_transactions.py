# Copyright (c) 2026 Zhambyl Yermagambet
"""Require rollback of original rows, pending work, and source progress at every write."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from extensions.models.source_reads import source_key
from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import runtime_commit_fixture as commits, source_read_fixture as fixtures


@pytest.mark.parametrize("table", [
    "extension_source_reads", "raw_events", "pending_raw_events", "extension_source_checkpoints",
])
def test_failed_source_write_rolls_back(tmp_path: Path, table: str) -> None:
    """A failure in any write leaves the original full logical database unchanged."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    with case.store.database.write() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_source BEFORE INSERT ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'injected source failure'); END",
        )
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(sqlite3.IntegrityError, match="injected source"):
        case.store.record_source_read(request)
    assert snapshots.snapshot(case.store.database) == before


def test_failed_source_commit_permits_exact_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real COMMIT failure cannot advance progress; the same call can then commit once."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    before = snapshots.snapshot(case.store.database)
    with closing(commits.fault_connection(case.store.database)) as connection:
        monkeypatch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.store.record_source_read(request)
        assert snapshots.snapshot(case.store.database) == before
        assert len(case.store.record_source_read(request).observations.accepted) == 1
        assert case.store.source_checkpoint(source_key(case.request)).revision == 1
