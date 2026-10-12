# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a session's entry rows after both a canonical cursor and an entry row.

An entry keeps the canonical cursor of the fact that made it. A projection
commits its entries after the core entries of the same fact, so a reader that
already has that canonical cursor finds them only by their new rows.
"""

from __future__ import annotations

import itertools
import sqlite3
from dataclasses import dataclass
from operator import itemgetter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain.ids import SessionId

ROW = "cursor"
COMMIT = "commit_cursor"
CONTENT_BYTES = "content_bytes"
# The content of one read. A command can write many megabytes, and a reader that
# returns after a long time must not get all of them in one frame.
PAGE_CONTENT_BYTES = 1_048_576
# The largest SQLite integer: the last commit of a read that did not stop.
_NO_PAGE_END = 9_223_372_036_854_775_807
_SIZES_SQL = (
    "SELECT commit_cursor, length(CAST(payload AS BLOB)) AS content_bytes FROM session_entries "
    "WHERE session_id=? AND (commit_cursor > ? OR cursor > ?) ORDER BY commit_cursor, cursor"
)
_PAGE_SQL = (
    "SELECT * FROM session_entries WHERE session_id=? AND (commit_cursor > ? OR cursor > ?) "
    "AND commit_cursor <= ? ORDER BY commit_cursor, cursor"
)


@dataclass(frozen=True)
class EntryRowRead:
    """Keep the entry rows of one read and the highest entry row that the reader then has."""

    rows: list[sqlite3.Row]
    entry_cursor: int
    # The last commit of a read that stopped at its content budget. The read has
    # every entry of that commit and of each commit before it.
    page_end: int | None = None


def read_after(
    connection: sqlite3.Connection, session_id: SessionId, cursor: int, entry_cursor: int | None,
) -> EntryRowRead:
    """Read the entries after the canonical cursor or after the entry row, in feed order.

    Without an entry row, the reader has every row at or before its canonical cursor.
    A read stops after a complete commit when its content is more than the budget.

    Returns:
        The rows, the reader's new highest row, and where a stopped read ends.

    """
    known = known_row(connection, session_id, cursor) if entry_cursor is None else entry_cursor
    after = (str(session_id), cursor, known)
    end = page_end(connection.execute(_SIZES_SQL, after).fetchall(), cursor)
    last_commit = _NO_PAGE_END if end is None else end
    rows = connection.execute(_PAGE_SQL, (*after, last_commit)).fetchall()
    return EntryRowRead(rows, highest_row(known, rows), end)


def page_end(sizes: Iterable[sqlite3.Row], cursor: int) -> int | None:
    """Find the last complete commit that the content budget holds.

    An entry at or before the cursor came after the reader read its commit. Every
    such entry goes in the read, because only its row finds it again. The first
    commit after the cursor also goes in the read, when it is larger than the budget.

    Returns:
        The last commit of a stopped read, or None when every entry is in the read.

    """
    content_bytes = 0
    end = None
    for commit, commit_sizes in itertools.groupby(sizes, key=itemgetter(COMMIT)):
        content_bytes += sum(size[CONTENT_BYTES] for size in commit_sizes)
        if commit > cursor and end is not None and content_bytes > PAGE_CONTENT_BYTES:
            return end
        end = int(commit)
    return None


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
