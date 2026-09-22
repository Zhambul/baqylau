# Copyright (c) 2026 Zhambyl Yermagambet
"""Measure actual stored content independently of the page SQL calculation."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from extensions.models.interpretation_reads import CanonicalPage
from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests.extension_host import interpretation_snapshot_fixture as storage

BUDGET_FIELD = "MAX_PAGE_CONTENT_BYTES"
SMALL_BUDGET = 2048


def content_sizes(store: SqliteInterpretationRepository) -> tuple[int, ...]:
    """Count UTF-8 bytes from stored text, not from the SQL size expression.

    Returns:
        One size for each fact in accepted cursor order.

    """
    with store.database.read() as connection:
        rows = connection.execute(
            "SELECT payload, scope, extension_metadata FROM canonical_events ORDER BY cursor",
        ).fetchall()
    return tuple(_content_size(row) for row in rows)


def _content_size(row: sqlite3.Row) -> int:
    parts = (str(part).encode("utf-8") for part in row if part is not None)
    return sum(len(part) for part in parts)


def read(
    store: SqliteInterpretationRepository, after: int = 0, limit: int = 100, *, scoped: bool = False,
) -> CanonicalPage:
    """Exercise either public repository page path.

    Returns:
        The exact installation scope or the mixed live stream.

    """
    if scoped:
        return store.facts_for_scope("default", storage.INSTALLATION, after, limit)
    return store.current_fact_page(after, limit)


def identities(page: CanonicalPage) -> tuple[str, ...]:
    """Show cursor order without comparing irrelevant body fields.

    Returns:
        Every returned fact identity in order.

    """
    return tuple(stored.fact.event_id for stored in page.facts)


@dataclass
class ConcurrentPageWriter:
    """Commit from a second connection between the page body read and head read."""

    store: SqliteInterpretationRepository
    head: Callable[[sqlite3.Connection, str], int]
    written: bool = False

    def __call__(self, connection: sqlite3.Connection, history_revision: str) -> int:
        """Write once before reading the first connection's head.

        Returns:
            The head visible to the existing read transaction.

        """
        if not self.written:
            storage.seed(self.store, (storage.fact("concurrent"),))
            self.written = True
        return self.head(connection, history_revision)
