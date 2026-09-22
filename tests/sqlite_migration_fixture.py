# Copyright (c) 2026 Zhambyl Yermagambet
"""Use real SQLite files to check schema changes and rollback."""

import sqlite3
from contextlib import closing
from pathlib import Path

from repository.impl.sqlite.connection import SqliteDatabase

INITIAL_VERSION = 1
TARGET_VERSION = 2
SOURCE_BYTES = b"\x00original\xff\n"
INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    version INTEGER NOT NULL,
    applied_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS observations(
    observation_id INTEGER PRIMARY KEY,
    payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS source_links(
    observation_id INTEGER NOT NULL REFERENCES observations(observation_id),
    source_position TEXT NOT NULL
);
"""
CHANGED_SHAPES = (
    'INSERT INTO "schema_version"',
    "CREATE TABLE interpretation_journals",
    'INSERT INTO "interpretation_journals"',
    "CREATE TABLE session_entries",
    "CREATE INDEX index_session_entries_session",
)
COPY_MIGRATION = (
    "CREATE TABLE observation_copy(observation_id INTEGER PRIMARY KEY, payload BLOB NOT NULL)",
    "INSERT INTO observation_copy SELECT * FROM observations",
    "CREATE INDEX index_source_position ON source_links(source_position)",
)


def populated(directory: Path) -> SqliteDatabase:
    """Store original bytes and a source link.

    Returns:
        The initial database with committed test data.

    """
    database = SqliteDatabase(str(directory / "migration.db"), INITIAL_SCHEMA, INITIAL_VERSION)
    with database.write() as connection:
        connection.execute("INSERT INTO observations VALUES(1, ?)", (SOURCE_BYTES,))
        connection.execute("INSERT INTO source_links VALUES(1, 'position:7')")
    return database


def upgrade(database: SqliteDatabase, statements: tuple[str, ...]) -> SqliteDatabase:
    """Keep the test migration separate from the base schema.

    Returns:
        An unopened database with one pending migration.

    """
    return SqliteDatabase(database.path, INITIAL_SCHEMA, TARGET_VERSION, migrations={TARGET_VERSION: statements})


def snapshot(database: SqliteDatabase) -> tuple[str, ...]:
    """Read schema, exact bytes, source links, and the version through a new connection.

    Returns:
        The complete logical database dump in SQLite order.

    """
    with closing(sqlite3.connect(database.path)) as connection:
        return tuple(connection.iterdump())


def retained_lines(snapshot_lines: tuple[str, ...]) -> tuple[str, ...]:
    """Keep every old statement except the shapes a later migration rebuilds.

    Returns:
        The statements and rows which must still exist after the upgrade.

    """
    return tuple(line for line in snapshot_lines if not line.startswith(CHANGED_SHAPES))


def require_copied(database: SqliteDatabase) -> None:
    """Check the complete successful result and its foreign keys."""
    with database.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        copied = connection.execute("SELECT payload FROM observation_copy WHERE observation_id=1").fetchone()
        links = connection.execute("SELECT source_position FROM source_links WHERE observation_id=1").fetchone()
        errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert version["version"] == TARGET_VERSION
    assert copied["payload"] == SOURCE_BYTES
    assert links["source_position"] == "position:7"
    assert not errors
