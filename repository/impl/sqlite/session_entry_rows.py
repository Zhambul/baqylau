# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a session's entry rows after both a canonical cursor and an entry row.

An entry keeps the canonical cursor of the fact that made it. A projection
commits its entries after the core entries of the same fact, so a reader that
already has that canonical cursor finds them only by their new rows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.ids import SessionId

ROW = "cursor"


@dataclass(frozen=True)
class EntryRowRead:
    """Keep the entry rows of one read and the highest entry row that the reader then has."""

    rows: list[sqlite3.Row]
    entry_cursor: int


def read_after(
    connection: sqlite3.Connection, session_id: SessionId, cursor: int, entry_cursor: int | None,
) -> EntryRowRead:
    """Read the entries after the canonical cursor or after the entry row, in feed order.

    Without an entry row, the reader has every row at or before its canonical cursor.

    Returns:
        The rows and the reader's new highest row.

    """
    known = known_row(connection, session_id, cursor) if entry_cursor is None else entry_cursor
    rows = connection.execute(
        "SELECT * FROM session_entries WHERE session_id=? AND (commit_cursor > ? OR cursor > ?) "
        "ORDER BY commit_cursor, position",
        (str(session_id), cursor, known),
    ).fetchall()
    return EntryRowRead(rows, highest_row(known, rows))


def known_row(connection: sqlite3.Connection, session_id: SessionId, cursor: int) -> int:
    """Read the highest entry row at or before a canonical cursor, which a reader at that cursor has.

    Returns:
        The row, or zero.

    """
    found = connection.execute(
        "SELECT MAX(cursor) AS cursor FROM session_entries WHERE session_id=? AND commit_cursor <= ?",
        (str(session_id), cursor),
    ).fetchone()
    highest = None if found is None else found[ROW]
    return 0 if highest is None else int(highest)


def highest_row(entry_row: int, rows: list[sqlite3.Row]) -> int:
    """Name the highest entry row that the reader has after these rows.

    Returns:
        The row.

    """
    delivered = (int(found[ROW]) for found in rows)
    return max((entry_row, *delivered))
