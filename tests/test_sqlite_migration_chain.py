# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish all pending migration versions in one transaction."""

import sqlite3
from pathlib import Path

import pytest

from repository.errors import SchemaVersionMismatchError
from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_fixture as fixtures


def test_later_version_failure_restores_chain(tmp_path: Path) -> None:
    """A failed upgrade does not publish an earlier part of the pending chain."""
    database = fixtures.populated(tmp_path)
    before = fixtures.snapshot(database)
    candidate = SqliteDatabase(database.path, fixtures.INITIAL_SCHEMA, fixtures.TARGET_VERSION + 1, migrations={
        fixtures.TARGET_VERSION: fixtures.COPY_MIGRATION,
        fixtures.TARGET_VERSION + 1: ("INSERT INTO missing_migration_table VALUES(1)",),
    })
    with pytest.raises(sqlite3.OperationalError, match="missing_migration_table"):
        candidate.initialize()
    assert fixtures.snapshot(database) == before


def test_missing_version_restores_pending_chain(tmp_path: Path) -> None:
    """A missing migration cannot leave a partly upgraded file."""
    database = fixtures.populated(tmp_path)
    before = fixtures.snapshot(database)
    candidate = SqliteDatabase(database.path, fixtures.INITIAL_SCHEMA, fixtures.TARGET_VERSION + 1, migrations={
        fixtures.TARGET_VERSION: fixtures.COPY_MIGRATION,
    })
    with pytest.raises(SchemaVersionMismatchError):
        candidate.initialize()
    assert fixtures.snapshot(database) == before


def test_complete_chain_applies_each_step_once(tmp_path: Path) -> None:
    """A second open does not repeat a committed migration."""
    database = fixtures.populated(tmp_path)
    candidate = SqliteDatabase(database.path, fixtures.INITIAL_SCHEMA, fixtures.TARGET_VERSION + 1, migrations={
        fixtures.TARGET_VERSION: fixtures.COPY_MIGRATION,
        fixtures.TARGET_VERSION + 1: ("ALTER TABLE observation_copy ADD COLUMN history_revision TEXT",),
    })
    candidate.initialize()
    before = fixtures.snapshot(candidate)
    SqliteDatabase(candidate.path, candidate.schema, candidate.schema_version).initialize()
    assert fixtures.snapshot(candidate) == before
