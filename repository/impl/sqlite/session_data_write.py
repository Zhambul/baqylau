# Copyright (c) 2026 Zhambyl Yermagambet
"""Write session data changes through one SQLite connection."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from domain.entries import ENTRY_TYPES
from repository.impl.sqlite import goal_dismissals
from repository.mapper.documents import encode_document

if TYPE_CHECKING:
    import sqlite3

    from domain.ids import SessionId
    from repository.contract.session_data import SessionDataChanges

_ENTRY_COLUMNS = (
    "cursor, entry_id, session_id, entry_type, actor_id, parent_actor_id, turn_id, occurred_at, summary, payload"
)


def apply_changes(
    connection: sqlite3.Connection,
    session_id: SessionId,
    session_data_changes: SessionDataChanges,
    canonical_cursor: int,
) -> None:
    """Write one canonical change set and its progress mark."""
    if session_data_changes.entry is not None:
        entry = session_data_changes.entry
        connection.execute(
            f"INSERT OR IGNORE INTO session_entries({_ENTRY_COLUMNS}) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                canonical_cursor,
                str(entry.entry_id),
                str(entry.session_id),
                ENTRY_TYPES[type(entry.body)],
                str(entry.actor_id),
                str(entry.parent_actor_id) if entry.parent_actor_id else None,
                str(entry.turn_id) if entry.turn_id else None,
                entry.occurred_at,
                entry.summary,
                encode_document(entry.body).decode("utf-8"),
            ),
        )
    if session_data_changes.session is not None:
        goal_dismissals.expire(connection, session_id, session_data_changes.session.goal, canonical_cursor)
        connection.execute(
            "INSERT INTO session_data(session_id, revision, payload) VALUES(?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision, payload=excluded.payload",
            (
                str(session_id),
                canonical_cursor,
                encode_document(session_data_changes.session).decode("utf-8"),
            ),
        )
    for actor in session_data_changes.actors:
        connection.execute(
            "INSERT INTO session_data_actors(session_id, actor_id, revision, payload) "
            "VALUES(?, ?, ?, ?) ON CONFLICT(session_id, actor_id) DO UPDATE SET "
            "revision=excluded.revision, payload=excluded.payload",
            (
                str(session_id),
                str(actor.actor_id),
                canonical_cursor,
                encode_document(actor).decode("utf-8"),
            ),
        )
    connection.execute(
        "INSERT INTO reaction_progress(id, canonical_cursor, updated_at) "
        "VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "canonical_cursor=excluded.canonical_cursor, updated_at=excluded.updated_at",
        (canonical_cursor, time.time()),
    )


def clear_read_model(connection: sqlite3.Connection) -> None:
    """Clear derived session data and its progress mark."""
    connection.execute("DELETE FROM session_entries")
    connection.execute("DELETE FROM session_data_actors")
    connection.execute("DELETE FROM session_data")
    connection.execute("DELETE FROM reaction_progress")
    connection.execute("DELETE FROM sqlite_sequence WHERE name='session_entries'")
