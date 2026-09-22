# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep core read-model progress separate from display changes and extension projections."""

import sqlite3
import time
from typing import Annotated

from pydantic import Field, TypeAdapter

MAX_SQL_CURSOR = 9_223_372_036_854_775_807
ProgressCursor = Annotated[int, Field(strict=True, gt=0, le=MAX_SQL_CURSOR)]
CURSOR = TypeAdapter[ProgressCursor](ProgressCursor)


def read_progress(connection: sqlite3.Connection) -> int:
    """Read the core consumer's durable checkpoint.

    Returns:
        Zero before the first accepted change or explicit skip.

    """
    found = connection.execute("SELECT canonical_cursor FROM reaction_progress WHERE id=1").fetchone()
    return 0 if found is None else int(found["canonical_cursor"])


def write_progress(connection: sqlite3.Connection, cursor: int) -> None:
    """Write progress in the same transaction as its accepted change."""
    connection.execute(
        "INSERT INTO reaction_progress(id, canonical_cursor, updated_at) "
        "VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "canonical_cursor=excluded.canonical_cursor, updated_at=excluded.updated_at",
        (cursor, time.time()),
    )


def advance_extensions(connection: sqlite3.Connection, canonical_cursor: int) -> None:
    """Validate the target and the whole gap before advancing this core-only consumer.

    Raises:
        ValueError: If the target is absent, is not a live extension fact, or skips core work.

    """
    cursor = CURSOR.validate_python(canonical_cursor)
    target = connection.execute(
        "SELECT session_id FROM current_canonical_events WHERE cursor=?", (cursor,),
    ).fetchone()
    if target is None or target["session_id"] is not None:
        message = "core progress can skip only a recorded live extension fact"
        raise ValueError(message)
    previous = read_progress(connection)
    if cursor <= previous:
        return
    pending_core = connection.execute(
        "SELECT 1 FROM current_canonical_events WHERE cursor>? AND cursor<=? "
        "AND session_id IS NOT NULL LIMIT 1", (previous, cursor),
    ).fetchone()
    if pending_core is not None:
        message = "extension progress cannot skip unprocessed core facts"
        raise ValueError(message)
    write_progress(connection, cursor)
