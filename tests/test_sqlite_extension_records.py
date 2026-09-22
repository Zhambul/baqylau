# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the typed extension record table in the fresh schema."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests import sqlite_repository_dependencies as repository_dependencies

RECORD_COLUMN_NAMES = (
    "owner",
    "collection",
    "record_key",
    "scope",
    "state",
    "revision",
    "projection_revision",
    "schema_ref",
    "document",
    "summary",
    "scope_kind",
    "session_id",
    "repository_id",
)
SCOPE_INDEX = "index_extension_records_scope"


def test_fresh_schema_has_record_table(main: repository_dependencies.SqliteDatabase) -> None:
    """A fresh database carries the typed record table and its scope index."""
    with main.read() as connection:
        # `table_xinfo` includes the stored generated scope columns.
        columns = {row["name"] for row in connection.execute("PRAGMA table_xinfo(extension_records)")}
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(extension_records)")}
    assert columns == set(RECORD_COLUMN_NAMES)
    assert SCOPE_INDEX in indexes
