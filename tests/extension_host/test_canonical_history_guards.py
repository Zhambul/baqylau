# Copyright (c) 2026 Zhambyl Yermagambet
"""Check history keys, strict core fields, and indexed scoped canonical reads."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from tests import sqlite_migration_fixture as snapshots, sqlite_test_fixtures as core
from tests.extension_host import (
    canonical_history_fixture as fixtures,
    observation_fixture,
    observation_requests,
)


@pytest.mark.parametrize("column", ["schema_version", "session_id", "actor_id", "harness"])
def test_canonical_core_fields_remain_required(main: SqliteDatabase, column: str) -> None:
    """The generalized SQL table still rejects an incomplete core envelope."""
    fixtures.insert_core(main, core.a_started_event())
    before = snapshots.snapshot(main)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"), main.write() as connection:
        connection.execute(f"UPDATE canonical_events SET {column}=NULL")  # noqa: S608 -- Fixed test columns.
    assert snapshots.snapshot(main) == before


@pytest.mark.parametrize("column", ["session_id", "actor_id", "harness", "turn_id", "parent_actor_id"])
def test_extension_fact_cannot_supply_core_fields(main: SqliteDatabase, column: str) -> None:
    """Extension facts use their explicit scope, not invented core IDs."""
    fixtures.insert_extension(main, "extension-one")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"), main.write() as connection:
        connection.execute(f"UPDATE canonical_events SET {column}='fake'")  # noqa: S608 -- Fixed test columns.


def test_repeated_identity_is_unique_per_history(main: SqliteDatabase) -> None:
    """One logical ID can have two histories, but not two accepted bodies in one history."""
    event = core.a_started_event()
    fixtures.insert_core(main, event)
    fixtures.insert_core(main, event, "default")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint"):
        fixtures.insert_core(main, event)
    with main.read() as connection:
        histories = connection.execute("SELECT history_revision FROM canonical_events ORDER BY cursor").fetchall()
    assert [row[0] for row in histories] == [fixtures.CANDIDATE, "default"]


def test_cross_history_link_is_rejected(main: SqliteDatabase) -> None:
    """A link cannot use a canonical ID that exists only in another history."""
    fixtures.insert_core(main, core.a_started_event())
    SqliteRawEventRepository(main).record((core.a_raw_event(),))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), main.write() as connection:
        connection.execute(
            "INSERT INTO interpretation_events(event_id, raw_event_id, event_order, storage_result) "
            "VALUES(?, ?, 0, 'accepted')", (core.FIRST_CANONICAL_EVENT_ID, core.FIRST_RAW_EVENT_ID),
        )


def test_scoped_history_page_uses_index(main: SqliteDatabase) -> None:
    """A scoped page uses the history and scope index and retains the original document."""
    fixtures.insert_extension(main, "extension-one")
    with main.read() as connection:
        row = connection.execute("SELECT scope, fact_owner, payload FROM canonical_events").fetchone()
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM canonical_events "
            "WHERE history_revision=? AND scope=? AND cursor>? ORDER BY cursor LIMIT ?",
            ("default", row["scope"], 0, 10),
        ).fetchall()
    assert row["scope"] == '{"kind":"installation"}' and row["fact_owner"] == "test.reader"
    assert row["payload"] == ' \n "exact é text"\r\n'
    assert any("index_canonical_history_scope" in entry["detail"] for entry in plan)


def test_live_cause_excludes_candidate(tmp_path: Path) -> None:
    """A fact that is hidden from the current history cannot cause a live observation."""
    case = observation_fixture.installed(tmp_path)
    fixtures.insert_core(case.store.database, core.a_started_event("candidate-only"))
    request = observation_requests.causes(case.request, ("candidate-only",))
    with pytest.raises(ValueError, match="cause is not recorded"):
        case.store.append_observations(request)
    assert case.store.pending_observations(10) == ()
