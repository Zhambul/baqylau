# Copyright (c) 2026 Zhambyl Yermagambet
"""Require total rollback of every write in a complete interpretation."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as snapshots
from tests.extension_host import interpretation_fixture as fixtures, runtime_commit_fixture as commits

INSERT = "INSERT"


@pytest.mark.parametrize(("table", "operation"), [
    ("interpretations", INSERT), ("interpretation_journals", INSERT), ("pending_raw_events", "DELETE"),
    ("extension_translation_state", INSERT), ("canonical_events", INSERT), ("interpretation_events", INSERT),
])
def test_failed_write_restores_whole_database(tmp_path: Path, table: str, operation: str) -> None:
    """No verdict, journal, state, pending change, fact, link, or cursor survives a failed write."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    with case.store.database.write() as connection:
        connection.execute(
            f"CREATE TRIGGER fail_interpretation BEFORE {operation} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'injected interpretation failure'); END",
        )
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(sqlite3.IntegrityError, match="injected interpretation"):
        case.store.record_interpretation(request)
    assert snapshots.snapshot(case.store.database) == before


def test_failed_commit_permits_exact_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real connection rolls back after COMMIT fails and accepts the same proposal once."""
    case = fixtures.installed(tmp_path)
    request = fixtures.proposal(case)
    before = snapshots.snapshot(case.store.database)
    with closing(commits.fault_connection(case.store.database)) as connection:
        monkeypatch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.store.record_interpretation(request)
        assert snapshots.snapshot(case.store.database) == before
        assert len(case.store.record_interpretation(request).accepted) == 1
        assert case.store.translator_state(fixtures.state_key(case)).revision == 1
