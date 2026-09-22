# Copyright (c) 2026 Zhambyl Yermagambet
"""Select live fact views while retaining read-only access to older databases."""

import sqlite3
from typing import NamedTuple


class CurrentFactTables(NamedTuple):
    """Supply fixed SQL names, never names received from a caller."""

    canonical: str
    interpretations: str
    links: str


def current_tables(connection: sqlite3.Connection) -> CurrentFactTables:
    """Select views for versioned storage and original tables for old storage.

    Returns:
        The three fixed names that select only the current history.

    """
    versioned = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_histories'",
    ).fetchone()
    if versioned is None:
        return CurrentFactTables("canonical_events", "interpretations", "interpretation_events")
    return CurrentFactTables("current_canonical_events", "current_interpretations", "current_interpretation_events")
