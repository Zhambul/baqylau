# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject partial schema changes before extension event storage is added."""

import sqlite3
from pathlib import Path

import pytest

from tests import sqlite_migration_fixture as fixtures

FAIL_STATEMENT = "INSERT INTO missing_migration_table VALUES(1)"
FAILED_DDL = (
    "CREATE TABLE incomplete_copy(payload BLOB)",
    "ALTER TABLE observations ADD COLUMN history_revision TEXT NOT NULL DEFAULT 'initial'",
    "ALTER TABLE observations RENAME TO old_observations",
    "CREATE INDEX incomplete_index ON observations(payload)",
    "CREATE TRIGGER incomplete_trigger AFTER INSERT ON observations BEGIN SELECT 1; END",
    "DROP TABLE source_links",
)


@pytest.mark.parametrize("statement", FAILED_DDL)
def test_failed_ddl_restores_complete_database(tmp_path: Path, statement: str) -> None:
    """A failure after the first schema change retains the complete prior database."""
    database = fixtures.populated(tmp_path)
    before = fixtures.snapshot(database)
    candidate = fixtures.upgrade(database, (statement, FAIL_STATEMENT))
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        candidate.initialize()
    assert fixtures.snapshot(database) == before
    fixtures.require_copied(fixtures.upgrade(database, fixtures.COPY_MIGRATION))


@pytest.mark.parametrize("failed_after", range(1, len(fixtures.COPY_MIGRATION) + 1))
def test_failed_copy_can_retry_without_repair(tmp_path: Path, failed_after: int) -> None:
    """Failure before or after a data copy leaves no partial table or index."""
    database = fixtures.populated(tmp_path)
    before = fixtures.snapshot(database)
    statements = (*fixtures.COPY_MIGRATION[:failed_after], FAIL_STATEMENT)
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        fixtures.upgrade(database, statements).initialize()
    assert fixtures.snapshot(database) == before
    fixtures.require_copied(fixtures.upgrade(database, fixtures.COPY_MIGRATION))


def test_deferred_constraint_failure_rolls_back(tmp_path: Path) -> None:
    """A real SQLite COMMIT failure must undo DDL and data changes."""
    database = fixtures.populated(tmp_path)
    before = fixtures.snapshot(database)
    statements = (*fixtures.COPY_MIGRATION, (
        "CREATE TABLE deferred_link(observation_id INTEGER REFERENCES observations(observation_id) "
        "DEFERRABLE INITIALLY DEFERRED)"
    ), "INSERT INTO deferred_link VALUES(999)")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        fixtures.upgrade(database, statements).initialize()
    assert fixtures.snapshot(database) == before
    fixtures.require_copied(fixtures.upgrade(database, fixtures.COPY_MIGRATION))
