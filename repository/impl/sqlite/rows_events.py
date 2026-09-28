# Copyright (c) 2026 Zhambyl Yermagambet
"""Map SQLite event and session-data rows to repository row objects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repository.impl.sqlite.row_columns import (
    ACTOR_ID_COLUMN,
    CREATED_AT_COLUMN,
    HARNESS_COLUMN,
    ID_COLUMN,
    PARENT_ACTOR_ID_COLUMN,
    PAYLOAD_COLUMN,
    SESSION_ID_COLUMN,
)
from repository.model.facts import (
    CanonicalEventRow,
    RawEventRow,
    SessionDataActorRow,
    SessionDataRow,
    SessionEntryRow,
    ShellOutputRow,
)

if TYPE_CHECKING:
    import sqlite3


def raw_event(row: sqlite3.Row) -> RawEventRow:
    """Return the raw event row.

    Returns:
        The raw event row.

    """
    return RawEventRow(
        id=row[ID_COLUMN],
        raw_event_id=row["raw_event_id"],
        session_id=row[SESSION_ID_COLUMN],
        harness=row[HARNESS_COLUMN],
        source_type=row["source_type"],
        source_identity=row["source_identity"],
        source_name=row["source_name"],
        source_position=row["source_position"],
        actor_id=row[ACTOR_ID_COLUMN],
        parent_actor_id=row[PARENT_ACTOR_ID_COLUMN],
        observed_at=row["observed_at"],
        encoding=row["encoding"],
        payload=row[PAYLOAD_COLUMN],
        payload_codec=row["payload_codec"],
        terminal_window_id=row["terminal_window_id"],
        harness_process_id=row["harness_process_id"],
        account_id=row["account_id"],
        account_display_name=row["account_display_name"],
    )


def canonical_event(row: sqlite3.Row) -> CanonicalEventRow:
    """Return the canonical event row.

    Returns:
        The canonical event row.

    """
    return CanonicalEventRow(
        cursor=row["cursor"],
        event_id=row["event_id"],
        schema_version=row["schema_version"],
        event_type=row["event_type"],
        session_id=row[SESSION_ID_COLUMN],
        actor_id=row[ACTOR_ID_COLUMN],
        turn_id=row["turn_id"],
        parent_actor_id=row[PARENT_ACTOR_ID_COLUMN],
        harness=row[HARNESS_COLUMN],
        occurred_at=row["occurred_at"],
        terminal_window_id=row["terminal_window_id"],
        harness_process_id=row["harness_process_id"],
        accepted_at=row["accepted_at"],
        payload=row[PAYLOAD_COLUMN],
    )


def shell_output(row: sqlite3.Row) -> ShellOutputRow:
    """Return the shell output row.

    Returns:
        The shell output row.

    """
    return ShellOutputRow(
        session_id=row[SESSION_ID_COLUMN],
        shell_id=row["shell_id"],
        harness=row[HARNESS_COLUMN],
        actor_id=row[ACTOR_ID_COLUMN],
        parent_actor_id=row[PARENT_ACTOR_ID_COLUMN],
        source_path=row["source_path"],
        chunk_source_type=row["chunk_source_type"],
        delete_source=row["delete_source"],
        initial_size=row["initial_size"],
        initial_modified_at=row["initial_modified_at"],
        wait_for_source_change=row["wait_for_source_change"],
        until=row["until"],
        state=row["state"],
        created_at=row[CREATED_AT_COLUMN],
    )


def session_data(row: sqlite3.Row) -> SessionDataRow:
    """Return the session data row.

    Returns:
        The session data row.

    """
    return SessionDataRow(
        session_id=row[SESSION_ID_COLUMN],
        revision=row["revision"],
        payload=row[PAYLOAD_COLUMN],
    )


def session_data_actor(row: sqlite3.Row) -> SessionDataActorRow:
    """Return the session data actor row.

    Returns:
        The session data actor row.

    """
    return SessionDataActorRow(
        session_id=row[SESSION_ID_COLUMN],
        actor_id=row[ACTOR_ID_COLUMN],
        revision=row["revision"],
        payload=row[PAYLOAD_COLUMN],
    )


def session_entry(row: sqlite3.Row) -> SessionEntryRow:
    """Return the session entry row.

    Returns:
        The session entry row.

    """
    return SessionEntryRow(
        cursor=row["cursor"],
        commit_cursor=row["commit_cursor"],
        entry_id=row["entry_id"],
        session_id=row[SESSION_ID_COLUMN],
        entry_type=row["entry_type"],
        actor_id=row[ACTOR_ID_COLUMN],
        parent_actor_id=row[PARENT_ACTOR_ID_COLUMN],
        turn_id=row["turn_id"],
        occurred_at=row["occurred_at"],
        summary=row["summary"],
        payload=row[PAYLOAD_COLUMN],
    )
