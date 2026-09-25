# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and write the mirror records and feed rows of one candidate projection generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models import record_changes, records

from repository.impl.sqlite import connection, extension_records, session_data_write

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from repository.contract.session_data import SessionDataChanges

_CANDIDATE_STATE_SQL = (
    "SELECT record_key, state, revision, schema_ref, document, summary FROM extension_candidate_records "
    "WHERE generation=? AND owner=? AND collection=? AND scope=? AND record_key=?"
)
_CANDIDATE_PUT_SQL = (
    "INSERT INTO extension_candidate_records(generation, owner, collection, record_key, scope, state, revision, "
    "schema_ref, document, summary) VALUES(?, ?, ?, ?, ?, 'stored', ?, ?, ?, ?) "
    "ON CONFLICT(generation, owner, collection, scope, record_key) DO UPDATE SET state='stored', "
    "revision=excluded.revision, schema_ref=excluded.schema_ref, document=excluded.document, summary=excluded.summary"
)
_CANDIDATE_DELETE_SQL = (
    "UPDATE extension_candidate_records SET state='deleted', revision=?, document=NULL, summary=NULL "
    "WHERE generation=? AND owner=? AND collection=? AND scope=? AND record_key=?"
)
_CANDIDATE_ENTRY_SQL = (
    "INSERT OR IGNORE INTO extension_candidate_entries(generation, owner, commit_cursor, position, entry_id, "
    "session_id, entry_type, actor_id, parent_actor_id, turn_id, occurred_at, summary, payload) "
    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def candidate_record_states(
    connection_handle: sqlite3.Connection, generation: str, keys: Sequence[records.RecordKey],
) -> tuple[records.RecordState, ...]:
    """Read every selected key of one candidate generation, including explicit missing rows.

    Returns:
        The captured state per key, in the supplied order.

    """
    return tuple(_candidate_state(connection_handle, generation, key) for key in keys)


def write_candidate_changes(
    connection_handle: sqlite3.Connection,
    generation: str,
    owner: str,
    session_data_changes: SessionDataChanges,
    commit_cursor: int,
) -> None:
    """Write one candidate commit's feed rows and record changes after the revision checks.

    Raises:
        ValueError: If a selected candidate key changed since it was captured.

    """
    for change in session_data_changes.records:
        if _candidate_state(connection_handle, generation, change.key).revision != change.expected_revision:
            message = "candidate extension record changed since it was captured"
            raise ValueError(message)
        _write_candidate_record(connection_handle, generation, change, commit_cursor)
    for position, entry in enumerate(session_data_changes.entries):
        leading = (generation, owner, commit_cursor, position)
        session_data_write.insert_entry(connection_handle, _CANDIDATE_ENTRY_SQL, leading, entry)


def _candidate_state(
    connection_handle: sqlite3.Connection, generation: str, key: records.RecordKey,
) -> records.RecordState:
    row = connection_handle.execute(_CANDIDATE_STATE_SQL, (
        generation, key.owner, key.collection, key.scope.model_dump_json(), key.key,
    )).fetchone()
    if row is None:
        return records.MissingRecord(key=key)
    return extension_records.row_state(key.owner, key.collection, key.scope, row)


def _write_candidate_record(
    connection_handle: sqlite3.Connection, generation: str, change: record_changes.RecordChange, commit_cursor: int,
) -> None:
    key = change.key
    scope_text = key.scope.model_dump_json()
    if isinstance(change, record_changes.PutRecord):
        connection_handle.execute(_CANDIDATE_PUT_SQL, (
            generation, key.owner, key.collection, key.key, scope_text, commit_cursor,
            change.document.schema_ref.model_dump_json(), change.document.json_text, change.summary,
        ))
        return
    connection_handle.execute(_CANDIDATE_DELETE_SQL, (
        commit_cursor, generation, key.owner, key.collection, scope_text, key.key,
    ))


@dataclass(frozen=True)
class SqliteCandidateRecordReader:
    """Capture record keys of one candidate generation for its rebuild."""

    database: connection.SqliteDatabase
    generation: str

    def record_states(self, keys: Sequence[records.RecordKey]) -> tuple[records.RecordState, ...]:
        """Return the candidate states in the supplied key order.

        Returns:
            The captured state per key, including explicit missing rows.

        """
        with self.database.read() as connection_handle:
            return candidate_record_states(connection_handle, self.generation, keys)
