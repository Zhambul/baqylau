# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain old mixed history and reject partial schema-33 source upgrades."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.schema import MAIN_MIGRATIONS, MAIN_SCHEMA, MAIN_SCHEMA_VERSION
from tests import sqlite_migration_events as events, sqlite_migration_fixture as snapshots
from tests.extension_host import observation_upgrade_fixture as upgrades, source_read_upgrade as fixtures

MIGRATION = MAIN_MIGRATIONS[fixtures.TARGET_VERSION]


def test_source_upgrade_retains_existing_rows(tmp_path: Path) -> None:
    """The additive migration retains old DDL, journals, original bytes, state, and accepted facts."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    current = upgrades.upgraded(previous)
    events.require_original(current)
    # Two migrations change existing shapes: the normalized journal adds one
    # column, and multi-entry commits rebuild `session_entries`. Every other old
    # statement and row stays in place.
    assert set(snapshots.retained_lines(before)) <= set(snapshots.snapshot(current))
    with current.read() as connection:
        assert not connection.execute("SELECT * FROM extension_source_reads").fetchall()
        assert not connection.execute("SELECT * FROM extension_source_checkpoints").fetchall()
        assert connection.execute("SELECT * FROM interpretation_journals").fetchall()
        assert connection.execute("SELECT * FROM extension_translation_state").fetchall()


@pytest.mark.parametrize("failed_after", range(1, len(MIGRATION) + 1))
def test_source_migration_rollback(tmp_path: Path, failed_after: int) -> None:
    """Failure after each actual statement restores the old version and allows a clean retry."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    candidate = SqliteDatabase(previous.path, MAIN_SCHEMA, MAIN_SCHEMA_VERSION, migrations={
        **MAIN_MIGRATIONS,
        fixtures.TARGET_VERSION: (*MIGRATION[:failed_after], "INSERT INTO missing_migration_table VALUES(1)"),
    })
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        candidate.initialize()
    assert snapshots.snapshot(previous) == before
    events.require_original(upgrades.upgraded(previous))


def test_source_migration_commit_failure(tmp_path: Path) -> None:
    """A real deferred foreign-key failure at COMMIT restores all old data and schema."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    candidate = SqliteDatabase(previous.path, MAIN_SCHEMA, MAIN_SCHEMA_VERSION, migrations={
        **MAIN_MIGRATIONS,
        fixtures.TARGET_VERSION: (*MIGRATION,
            (
                "CREATE TABLE commit_guard(runtime_revision TEXT REFERENCES "
                "extension_runtime_revisions(runtime_revision) DEFERRABLE INITIALLY DEFERRED)"
            ),
            "INSERT INTO commit_guard VALUES('missing-runtime')",
        ),
    })
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        candidate.initialize()
    assert snapshots.snapshot(previous) == before
    events.require_original(upgrades.upgraded(previous))
