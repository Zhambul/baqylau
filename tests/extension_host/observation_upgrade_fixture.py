# Copyright (c) 2026 Zhambyl Yermagambet
"""Use independent schema-29 DDL to test the actual raw-store rebuild."""

from pathlib import Path

from repository.impl.sqlite import databases
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.schema import MAIN_SCHEMA_VERSION
from tests import sqlite_migration_events as events

PREVIOUS_VERSION = 29
TARGET_VERSION = 30
type RowSnapshot = tuple[object, ...]
type TableSnapshot = tuple[RowSnapshot, ...]
FIXTURES = Path(__file__).with_name("fixtures")
UNCHANGED_QUERIES = (
    (
        "SELECT cursor, event_id, schema_version, event_type, session_id, actor_id, turn_id, parent_actor_id, harness, "
        "occurred_at, terminal_window_id, harness_process_id, accepted_at, payload "
        "FROM canonical_events ORDER BY cursor"
    ),
    "SELECT raw_event_id, translator_version, decision, reason, completed_at FROM interpretations ORDER BY rowid",
    "SELECT event_id, raw_event_id, event_order, storage_result FROM interpretation_events ORDER BY rowid",
    "SELECT * FROM pending_raw_events ORDER BY rowid",
    "SELECT * FROM sessions ORDER BY rowid",
)
RAW_COLUMNS = (
    "id, raw_event_id, session_id, harness, source_type, source_identity, source_name, source_position, "
    "actor_id, parent_actor_id, observed_at, encoding, payload, payload_codec, terminal_window_id, "
    "harness_process_id, account_id, account_display_name"
)


def previous(directory: Path, *, populated: bool = True) -> SqliteDatabase:
    """Open the actual old table definitions, not a downgraded copy of new DDL.

    Returns:
        An independent old database with representative core history.

    """
    names = (
        "main-schema-26.sql", "extension-catalog-schema-27.sql", "extension-lifecycle-schema-28.sql",
        "extension-resolutions-schema-29.sql",
    )
    paths = tuple(FIXTURES / name for name in names)
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    database = SqliteDatabase(str(directory / "main.db"), text, PREVIOUS_VERSION)
    database.initialize()
    if populated:
        events.populate(database)
    return database


def upgraded(database: SqliteDatabase) -> SqliteDatabase:
    """Apply the production migration and check real foreign-key consistency.

    Returns:
        The upgraded database with restored core records.

    """
    current = databases.main_database(database.path)
    current.initialize()
    with current.read() as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    return current


def core_rows(database: SqliteDatabase) -> tuple[TableSnapshot, ...]:
    """Compare original raw columns and complete dependent table rows.

    Returns:
        The original bytes, IDs, source links, and visible canonical order.

    """
    with database.read() as connection:
        query = f"SELECT {RAW_COLUMNS} FROM raw_events ORDER BY id"  # noqa: S608 -- Fixed column names.
        raw = tuple(tuple(row) for row in connection.execute(query))
        related = tuple(
            tuple(tuple(row) for row in connection.execute(query))
            for query in UNCHANGED_QUERIES
        )
    return (raw, *related)


def require_core_metadata(database: SqliteDatabase) -> None:
    """Check the published version and the unchanged strict core branch."""
    with database.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        metadata = connection.execute("SELECT extension_metadata, origin FROM raw_events").fetchall()
    assert version[0] == MAIN_SCHEMA_VERSION
    assert all(tuple(row) == (None, "harness") for row in metadata)
