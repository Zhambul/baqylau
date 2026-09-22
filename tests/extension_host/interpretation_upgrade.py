# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the independent schema saved before interpretation journals were added."""

from pathlib import Path

from repository.impl.sqlite.connection import SqliteDatabase
from tests import sqlite_migration_events as events
from tests.extension_host import canonical_history_fixture as histories

PREVIOUS_VERSION = 31
TARGET_VERSION = 32


def previous(directory: Path) -> SqliteDatabase:
    """Populate old core and extension branches without using the new schema.

    Returns:
        A populated schema-31 database with live and candidate facts.

    """
    path = Path(__file__).with_name("fixtures") / "main-schema-31.sql"
    database = SqliteDatabase(
        str(directory / "main.db"), path.read_text(encoding="utf-8"), PREVIOUS_VERSION,
    )
    database.initialize()
    events.populate(database)
    histories.insert_extension_raw(database)
    histories.insert_extension(database, "extension-live")
    histories.insert_extension(database, "extension-candidate", histories.CANDIDATE)
    return database
