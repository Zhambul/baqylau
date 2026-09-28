# Copyright (c) 2026 Zhambyl Yermagambet
"""Write session data changes through one SQLite connection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.entries import ENTRY_TYPES
from repository.impl.sqlite import extension_records, goal_dismissals, session_data_progress
from repository.mapper.documents import encode_document

if TYPE_CHECKING:
    import sqlite3

    from domain.entries import SessionEntry
    from domain.ids import SessionId
    from repository.contract.session_data import SessionDataChanges

_ENTRY_SQL = (
    "INSERT OR IGNORE INTO session_entries(commit_cursor, position, entry_id, session_id, entry_type, actor_id, "
    "parent_actor_id, turn_id, occurred_at, summary, payload) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def apply_changes(
    connection: sqlite3.Connection,
    session_id: SessionId,
    session_data_changes: SessionDataChanges,
    canonical_cursor: int,
) -> None:
    """Write one canonical change set and its progress mark."""
    write_changes(connection, session_id, session_data_changes, canonical_cursor)
    session_data_progress.write_progress(connection, canonical_cursor)


def write_changes(
    connection: sqlite3.Connection,
    session_id: SessionId,
    session_data_changes: SessionDataChanges,
    canonical_cursor: int,
    projection_revision: str = extension_records.DEFAULT_PROJECTION_REVISION,
) -> None:
    """Write one change set without advancing the core progress mark.

    Record changes keep the projection generation that produced them.
    """
    for position, entry in enumerate(session_data_changes.entries):
        insert_entry(connection, _ENTRY_SQL, (canonical_cursor, position), entry)
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
    if session_data_changes.records:
        extension_records.apply_record_changes(
            connection, session_data_changes.records, canonical_cursor, projection_revision,
        )


def insert_entry(
    connection: sqlite3.Connection,
    statement: str,
    leading_columns: tuple[str | int, ...],
    session_entry: SessionEntry,
) -> None:
    """Insert one feed row; the statement's first columns take the leading values."""
    parent = None if session_entry.parent_actor_id is None else str(session_entry.parent_actor_id)
    turn = None if session_entry.turn_id is None else str(session_entry.turn_id)
    body = session_entry.body
    payload = encode_document(body).decode("utf-8")
    connection.execute(statement, (
        *leading_columns,
        str(session_entry.entry_id),
        str(session_entry.session_id),
        ENTRY_TYPES[type(body)],
        str(session_entry.actor_id),
        parent,
        turn,
        session_entry.occurred_at,
        session_entry.summary,
        payload,
    ))


def clear_read_model(connection: sqlite3.Connection) -> None:
    """Clear derived session data and its progress mark."""
    connection.execute("DELETE FROM session_entries")
    connection.execute("DELETE FROM session_data_actors")
    connection.execute("DELETE FROM session_data")
    connection.execute("DELETE FROM reaction_progress")
    connection.execute("DELETE FROM sqlite_sequence WHERE name='session_entries'")
