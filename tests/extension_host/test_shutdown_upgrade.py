# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain old history and roll back failed shutdown-record migrations."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from repository.impl.sqlite.schema import MAIN_MIGRATIONS, MAIN_SCHEMA, MAIN_SCHEMA_VERSION
from tests import sqlite_migration_events as events, sqlite_migration_fixture as snapshots
from tests.extension_host import observation_upgrade_fixture as upgrades, shutdown_upgrade as fixtures

MIGRATION = MAIN_MIGRATIONS[fixtures.TARGET_VERSION]


def _retained_lines(previous: SqliteDatabase) -> tuple[str, ...]:
    """Read the old statements and rows that must survive the migration.

    Returns:
        Every dump line except the version row and the rebuilt table shapes.

    """
    return snapshots.retained_lines(snapshots.snapshot(previous))


def _journal_columns(database: SqliteDatabase) -> tuple[str, ...]:
    with database.read() as connection:
        return tuple(row[1] for row in connection.execute("PRAGMA table_info(interpretation_journals)"))


def test_shutdown_upgrade_preserves_rows(tmp_path: Path) -> None:
    """An additive table cannot change old DDL, original bytes, or accepted facts."""
    previous = fixtures.previous(tmp_path)
    retained = _retained_lines(previous)
    current = upgrades.upgraded(previous)
    events.require_original(current)
    # The normalized journal adds one column to the existing table. Every other
    # old statement and row stays in place.
    assert set(retained) <= set(snapshots.snapshot(current))
    assert "codec_version" in _journal_columns(current)
    assert SqliteExtensionLifecycleRepository(current).read_extension_lifecycle().last_shutdown is None
    with current.read() as connection:
        assert not connection.execute("SELECT * FROM extension_shutdown_records").fetchall()
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_shutdown_upgrade_statement_rollback(tmp_path: Path) -> None:
    """Failure after table creation restores the complete old file and allows retry."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    candidate = SqliteDatabase(previous.path, MAIN_SCHEMA, MAIN_SCHEMA_VERSION, migrations={
        **MAIN_MIGRATIONS, fixtures.TARGET_VERSION: (*MIGRATION, "INSERT INTO missing_migration_table VALUES(1)"),
    })
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        candidate.initialize()
    assert snapshots.snapshot(previous) == before
    events.require_original(upgrades.upgraded(previous))


def test_shutdown_upgrade_commit_rollback(tmp_path: Path) -> None:
    """A deferred constraint failure at COMMIT rolls back the new table too."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    candidate = SqliteDatabase(previous.path, MAIN_SCHEMA, MAIN_SCHEMA_VERSION, migrations={
        **MAIN_MIGRATIONS, fixtures.TARGET_VERSION: (*MIGRATION,
            (
                "CREATE TABLE commit_guard(record_id TEXT REFERENCES "
                "extension_shutdown_records(record_id) DEFERRABLE INITIALLY DEFERRED)"
            ),
            "INSERT INTO commit_guard VALUES('absent-record')",
        ),
    })
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        candidate.initialize()
    assert snapshots.snapshot(previous) == before
    events.require_original(upgrades.upgraded(previous))
