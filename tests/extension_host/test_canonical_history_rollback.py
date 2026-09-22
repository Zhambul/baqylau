# Copyright (c) 2026 Zhambyl Yermagambet
"""Fail each actual canonical migration step and require a clean retry."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.schema import MAIN_MIGRATIONS, MAIN_SCHEMA, MAIN_SCHEMA_VERSION
from tests import sqlite_migration_events as events, sqlite_migration_fixture as snapshots
from tests.extension_host import canonical_history_fixture as fixtures, observation_upgrade_fixture as upgrades

MIGRATION = MAIN_MIGRATIONS[fixtures.TARGET_VERSION]


@pytest.mark.parametrize("failed_after", range(1, len(MIGRATION) + 1))
def test_history_migration_rollback(tmp_path: Path, failed_after: int) -> None:
    """Every copy, drop, restore, view, trigger, and cleanup is part of one transaction."""
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


def test_history_migration_commit_failure(tmp_path: Path) -> None:
    """An actual deferred foreign-key failure at COMMIT restores the old schema and triggers."""
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
