"""Application-owned open/closed state for terminal content views."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing

from app.data import data_directory


def _database_path() -> str:
    return os.path.join(data_directory(), "terminal-views.db")


def opened() -> frozenset[str]:
    database_path = _database_path()
    if not os.path.isfile(database_path):
        return frozenset()
    with closing(
        sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, timeout=0.2)
    ) as connection:
        try:
            rows = connection.execute("SELECT content_reference FROM opened_views").fetchall()
        except sqlite3.OperationalError:
            return frozenset()
    return frozenset(row[0] for row in rows)


def toggle(content_reference: str) -> bool:
    if not content_reference:
        raise ValueError("content reference is required")
    database_path = _database_path()
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    with closing(sqlite3.connect(database_path, timeout=0.2)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS opened_views("
            "content_reference TEXT PRIMARY KEY)"
        )
        existing = connection.execute(
            "SELECT 1 FROM opened_views WHERE content_reference=?", (content_reference,)
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO opened_views(content_reference) VALUES(?)", (content_reference,)
            )
            return True
        connection.execute(
            "DELETE FROM opened_views WHERE content_reference=?", (content_reference,)
        )
        return False
