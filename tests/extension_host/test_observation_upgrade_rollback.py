# Copyright (c) 2026 Zhambyl Yermagambet
"""Fail each actual raw migration step and verify complete rollback plus retry."""

import sqlite3
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.schema import MAIN_MIGRATIONS, MAIN_SCHEMA, MAIN_SCHEMA_VERSION
from tests import sqlite_migration_events as events, sqlite_migration_fixture as snapshots
from tests.extension_host import observation_upgrade_fixture as fixtures

MIGRATION = MAIN_MIGRATIONS[fixtures.TARGET_VERSION]


@pytest.mark.parametrize("failed_after", range(1, len(MIGRATION) + 1))
def test_actual_migration_failure_keeps_old_store(tmp_path: Path, failed_after: int) -> None:
    """Every copy, drop, restore, and cleanup step stays inside the transaction."""
    previous = fixtures.previous(tmp_path)
    before = snapshots.snapshot(previous)
    candidate = SqliteDatabase(previous.path, MAIN_SCHEMA, MAIN_SCHEMA_VERSION, migrations={
        **MAIN_MIGRATIONS,
        fixtures.TARGET_VERSION: (*MIGRATION[:failed_after], "INSERT INTO missing_migration_table VALUES(1)"),
    })
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        candidate.initialize()
    assert snapshots.snapshot(previous) == before
    events.require_original(fixtures.upgraded(previous))
