# Copyright (c) 2026 Zhambyl Yermagambet
"""Limit stored content in a mixed page without blocking an oversized first fact."""

import sqlite3

MAX_PAGE_CONTENT_BYTES = 4_194_304

LIVE_PAGE = (
    "WITH page_metadata AS ("
    "SELECT cursor, length(CAST(payload AS BLOB)) + length(CAST(scope AS BLOB)) + "
    "COALESCE(length(CAST(extension_metadata AS BLOB)), 0) AS content_bytes FROM canonical_events "
    "WHERE history_revision=? AND cursor>? ORDER BY cursor LIMIT ?"
    "), page_sizes AS ("
    "SELECT cursor, SUM(content_bytes) OVER (ORDER BY cursor) AS total_bytes, "
    "ROW_NUMBER() OVER (ORDER BY cursor) AS position FROM page_metadata"
    ") SELECT facts.* FROM page_sizes JOIN canonical_events AS facts USING(cursor) "
    "WHERE total_bytes<=? OR position=1 ORDER BY cursor"
)
SCOPED_PAGE = (
    "WITH page_metadata AS ("
    "SELECT cursor, length(CAST(payload AS BLOB)) + length(CAST(scope AS BLOB)) + "
    "COALESCE(length(CAST(extension_metadata AS BLOB)), 0) AS content_bytes FROM canonical_events "
    "WHERE history_revision=? AND scope=? AND cursor>? ORDER BY cursor LIMIT ?"
    "), page_sizes AS ("
    "SELECT cursor, SUM(content_bytes) OVER (ORDER BY cursor) AS total_bytes, "
    "ROW_NUMBER() OVER (ORDER BY cursor) AS position FROM page_metadata"
    ") SELECT facts.* FROM page_sizes JOIN canonical_events AS facts USING(cursor) "
    "WHERE total_bytes<=? OR position=1 ORDER BY cursor"
)


def page_rows(
    connection: sqlite3.Connection, history_revision: str, after_cursor: int, limit: int, scope: str | None,
) -> list[sqlite3.Row]:
    """Select an ordered prefix from bounded metadata before returning fact bodies.

    The content budget counts UTF-8 payload, scope, and extension metadata.
    It is not a serialized response size limit. One oversized first fact is
    returned alone so that the reader can always make progress.

    Returns:
        Up to the requested count, with a content budget or one oversized row.

    """
    if scope is None:
        return connection.execute(
            LIVE_PAGE, (history_revision, after_cursor, limit, MAX_PAGE_CONTENT_BYTES),
        ).fetchall()
    return connection.execute(
        SCOPED_PAGE, (history_revision, scope, after_cursor, limit, MAX_PAGE_CONTENT_BYTES),
    ).fetchall()
