# Copyright (c) 2026 Zhambyl Yermagambet
"""Protect actual core records while preparing extension event migrations."""

import sqlite3

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_events as events, sqlite_migration_fixture as fixtures


@pytest.mark.parametrize("statement", [
    "ALTER TABLE raw_events RENAME TO previous_raw_events",
    "ALTER TABLE canonical_events RENAME TO previous_canonical_events",
    "DROP TABLE interpretations",
])
def test_failed_event_migration_keeps_history(main: SqliteDatabase, statement: str) -> None:
    """Schema changes cannot lose raw bytes, facts, links, or pending work on failure."""
    events.populate(main)
    before = fixtures.snapshot(main)
    candidate = SqliteDatabase(main.path, main.schema, main.schema_version + 1, migrations={
        main.schema_version + 1: (statement, "INSERT INTO missing_migration_table VALUES(1)"),
    })
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        candidate.initialize()
    assert fixtures.snapshot(main) == before
    events.require_original(main)
