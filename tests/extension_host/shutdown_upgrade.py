# Copyright (c) 2026 Zhambyl Yermagambet
"""Seed independent schema-33 rows before the shutdown-record table exists."""

from pathlib import Path

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_events as events
from tests.extension_host import canonical_history_fixture as histories

PREVIOUS_VERSION = 33
TARGET_VERSION = 34


def previous(directory: Path) -> SqliteDatabase:
    """Populate old facts without the new schema or shutdown reader.

    Returns:
        A separate schema-33 file with core and extension history.

    """
    path = Path(__file__).with_name("fixtures") / "main-schema-33.sql"
    schema = path.read_text(encoding="utf-8")
    database = SqliteDatabase(str(directory / "main.db"), schema, PREVIOUS_VERSION)
    database.initialize()
    events.populate(database)
    histories.insert_extension_raw(database)
    histories.insert_extension(database, "extension-live")
    return database
