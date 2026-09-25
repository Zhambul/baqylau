# Copyright (c) 2026 Zhambyl Yermagambet
"""Check strict branch constraints, bounded paging, and original runtime retention."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from tests import sqlite_migration_fixture as snapshots, sqlite_test_fixtures as core, storage_reads
from tests.extension_host import observation_fixture as fixtures, observation_requests as requests


@pytest.mark.parametrize("column", ["session_id", "actor_id", "harness"])
def test_core_identity_columns_remain_required(main: SqliteDatabase, column: str) -> None:
    """Nullable SQL columns do not permit an incomplete core observation."""
    SqliteRawEventRepository(main).record((core.a_raw_event(),))
    before = snapshots.snapshot(main)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"), main.write() as connection:
        connection.execute(f"UPDATE raw_events SET {column}=NULL")  # noqa: S608 -- Fixed test columns.
    assert snapshots.snapshot(main) == before


def test_extension_cannot_supply_core_columns(tmp_path: Path) -> None:
    """An extension scope cannot be replaced with a made-up harness session."""
    case = fixtures.installed(tmp_path)
    storage_reads.append_observations(case.store, case.request)
    before = snapshots.snapshot(case.store.database)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"), case.store.database.write() as connection:
        connection.execute("UPDATE raw_events SET session_id='fake'")
    assert snapshots.snapshot(case.store.database) == before


def test_runtime_change_retains_first_capture(tmp_path: Path) -> None:
    """A new runtime cannot overwrite the accepted raw bytes or their first provenance."""
    case = fixtures.installed(tmp_path)
    first = storage_reads.append_observations(case.store, case.request).accepted
    current = requests.reload_request(case)
    repeated = storage_reads.append_observations(case.store, current)
    assert not repeated.accepted and repeated.repeated == first
    assert fixtures.original(repeated.repeated[0]).runtime_revision != current.runtime_revision
    with pytest.raises(ValueError, match="committed runtime"):
        storage_reads.append_observations(case.store, case.request)


def test_scope_page_uses_indexed_scope(tmp_path: Path) -> None:
    """Scope paging seeks by exact scope and arrival ID rather than scanning history."""
    case = fixtures.installed(tmp_path)
    storage_reads.append_observations(case.store, case.request)
    with case.store.database.read() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM raw_events WHERE scope=? AND id>? ORDER BY id LIMIT ?",
            (case.request.scope.model_dump_json(), 0, 10),
        ).fetchall()
    assert any("index_raw_by_scope" in row["detail"] for row in plan)
