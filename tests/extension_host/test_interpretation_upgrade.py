# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain all old rows and reject partial schema-32 upgrades."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from repository.impl.sqlite.schema import MAIN_MIGRATIONS, MAIN_SCHEMA, MAIN_SCHEMA_VERSION
from tests import (
    sqlite_migration_events as events,
    sqlite_migration_fixture as snapshots,
    sqlite_test_fixtures,
    storage_reads,
)
from tests.extension_host import interpretation_upgrade as fixtures, observation_upgrade_fixture as upgrades

MIGRATION = MAIN_MIGRATIONS[fixtures.TARGET_VERSION]


def test_upgrade_retains_existing_rows_and_ddl(tmp_path: Path) -> None:
    """The additive journal migration does not invent old processing steps or alter accepted facts."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    current = upgrades.upgraded(previous)
    events.require_original(current)
    assert set(snapshots.retained_lines(before)) <= set(snapshots.snapshot(current))
    store = SqliteInterpretationRepository(current)
    assert storage_reads.find_interpretation(store, "default", sqlite_test_fixtures.a_raw_event().raw_event_id) is None
    with current.read() as connection:
        assert not connection.execute("SELECT * FROM interpretation_steps").fetchall()
        assert not connection.execute("SELECT * FROM extension_translation_state").fetchall()
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("failed_after", range(1, len(MIGRATION) + 1))
def test_journal_migration_rollback(tmp_path: Path, failed_after: int) -> None:
    """Failure after each actual statement preserves the full old database and allows a retry."""
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


def test_journal_migration_commit_failure(tmp_path: Path) -> None:
    """An actual deferred foreign-key COMMIT failure restores the prior schema and data."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    candidate = SqliteDatabase(previous.path, MAIN_SCHEMA, MAIN_SCHEMA_VERSION, migrations={
        **MAIN_MIGRATIONS,
        fixtures.TARGET_VERSION: (*MIGRATION,
            (
                "CREATE TABLE commit_guard(history_revision TEXT REFERENCES canonical_histories(history_revision) "
                "DEFERRABLE INITIALLY DEFERRED)"
            ),
            "INSERT INTO commit_guard VALUES('missing-history')",
        ),
    })
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        candidate.initialize()
    assert snapshots.snapshot(previous) == before
    events.require_original(upgrades.upgraded(previous))
