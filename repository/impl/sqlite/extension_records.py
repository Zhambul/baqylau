# Copyright (c) 2026 Zhambyl Yermagambet
"""Read captured record states and write record changes inside one commit."""

import sqlite3
from collections.abc import Sequence

from baqylau_extension_api.models import documents, record_changes, records, scopes

from repository.contract.extension_records import ExtensionRecordChanges, ExtensionRecordPage
from repository.impl.sqlite import connection

DEFAULT_PROJECTION_REVISION = "default"
_STATE_SQL = (
    "SELECT record_key, state, revision, schema_ref, document, summary FROM extension_records "
    "WHERE owner=? AND collection=? AND scope=? AND record_key=?"
)
_PAGE_SQL = (
    "SELECT record_key, state, revision, schema_ref, document, summary FROM extension_records "
    "WHERE owner=? AND collection=? AND scope=? AND record_key>? ORDER BY record_key LIMIT ?"
)
_NEXT_BOUNDARY_SQL = (
    "SELECT MIN(revision) AS boundary FROM extension_records "
    "WHERE owner=? AND scope=? AND projection_revision=? AND revision>?"
)
_BOUNDARY_SQL = (
    "SELECT collection, record_key, state, revision, schema_ref, document, summary FROM extension_records "
    "WHERE owner=? AND scope=? AND projection_revision=? AND revision=? ORDER BY collection, record_key"
)
_PUT_SQL = (
    "INSERT INTO extension_records(owner, collection, record_key, scope, state, revision, projection_revision, "
    "schema_ref, document, summary) VALUES(?, ?, ?, ?, 'stored', ?, ?, ?, ?, ?) "
    "ON CONFLICT(owner, collection, scope, record_key) DO UPDATE SET state='stored', "
    "revision=excluded.revision, projection_revision=excluded.projection_revision, schema_ref=excluded.schema_ref, "
    "document=excluded.document, summary=excluded.summary"
)
_DELETE_SQL = (
    "UPDATE extension_records SET state='deleted', revision=?, projection_revision=?, document=NULL, summary=NULL "
    "WHERE owner=? AND collection=? AND scope=? AND record_key=?"
)


class SqliteExtensionRecordRepository:
    """Read captured extension records from one database."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def record_states(self, keys: Sequence[records.RecordKey]) -> tuple[records.RecordState, ...]:
        """Return the captured states in the supplied key order.

        Returns:
            The captured state per key, in the supplied order.

        """
        with self.database.read() as connection_handle:
            return record_states(connection_handle, keys)

    def record_page(
        self, owner: str, collection: str, scope: scopes.ExtensionScope, after_key: str, limit: int,
    ) -> ExtensionRecordPage:
        """Read one ordered record page in its exact scope.

        Returns:
            The ordered page and its continuation key.

        """
        rows = self._page_rows(owner, collection, scope, after_key, limit + 1)
        selected = rows[:limit]
        states = tuple(_row_state(owner, collection, scope, row) for row in selected)
        next_key = None
        if len(rows) > limit and selected:
            next_key = str(selected[-1]["record_key"])
        return ExtensionRecordPage(records=states, next_key=next_key)

    def record_changes(
        self,
        owner: str,
        scope: scopes.ExtensionScope,
        projection_generation: str,
        after_cursor: int,
    ) -> ExtensionRecordChanges:
        """Read every record change at the next committed boundary.

        Returns:
            The whole boundary's changes and its commit cursor. The cursor
            stays when no later boundary exists.

        """
        scope_text = scope.model_dump_json()
        with self.database.read() as connection_handle:
            boundary_row = connection_handle.execute(
                _NEXT_BOUNDARY_SQL, (owner, scope_text, projection_generation, after_cursor),
            ).fetchone()
            if boundary_row is None or boundary_row["boundary"] is None:
                return ExtensionRecordChanges(changes=(), next_cursor=after_cursor)
            boundary = int(boundary_row["boundary"])
            rows = connection_handle.execute(
                _BOUNDARY_SQL, (owner, scope_text, projection_generation, boundary),
            ).fetchall()
        changes = tuple(_row_state(owner, str(row["collection"]), scope, row) for row in rows)
        return ExtensionRecordChanges(changes=changes, next_cursor=boundary)

    def _page_rows(
        self, owner: str, collection: str, scope: scopes.ExtensionScope, after_key: str, limit: int,
    ) -> list[sqlite3.Row]:
        """Read one bounded ordered record row page.

        Returns:
            The raw rows in record-key order.

        """
        page_values = (owner, collection, scope.model_dump_json(), after_key, limit)
        with self.database.read() as connection_handle:
            return connection_handle.execute(_PAGE_SQL, page_values).fetchall()


def record_states(
    sqlite_connection: sqlite3.Connection, keys: Sequence[records.RecordKey],
) -> tuple[records.RecordState, ...]:
    """Read every selected key at one snapshot, including explicit missing rows.

    Returns:
        The captured state per key, in the supplied order.

    """
    return tuple(_state(sqlite_connection, key) for key in keys)


def apply_record_changes(
    sqlite_connection: sqlite3.Connection,
    changes: Sequence[record_changes.RecordChange],
    commit_cursor: int,
    projection_revision: str = DEFAULT_PROJECTION_REVISION,
) -> None:
    """Write one commit's record changes after checking every expected revision.

    The projection revision names the generation that produced the row, so a
    change read can select one exact generation.

    Raises:
        ValueError: If a selected key changed since it was captured.

    """
    for change in changes:
        current = _state(sqlite_connection, change.key)
        if current.revision != change.expected_revision:
            message = "extension record changed since it was captured"
            raise ValueError(message)
        if isinstance(change, record_changes.PutRecord):
            _put(sqlite_connection, change, commit_cursor, projection_revision)
        else:
            _delete(sqlite_connection, change, commit_cursor, projection_revision)


def _state(sqlite_connection: sqlite3.Connection, key: records.RecordKey) -> records.RecordState:
    scope_text = key.scope.model_dump_json()
    key_values = (key.owner, key.collection, scope_text, key.key)
    row = sqlite_connection.execute(_STATE_SQL, key_values).fetchone()
    if row is None:
        return records.MissingRecord(key=key)
    return _row_state(key.owner, key.collection, key.scope, row)


def _row_state(
    owner: str, collection: str, scope: scopes.ExtensionScope, row: sqlite3.Row,
) -> records.RecordState:
    record_key = row["record_key"]
    key = records.RecordKey(owner=owner, collection=collection, scope=scope, key=record_key)
    schema_ref = documents.SchemaRef.model_validate_json(row["schema_ref"])
    if row["state"] == "deleted":
        return records.DeletedRecord(key=key, revision=int(row["revision"]), schema_ref=schema_ref)
    return records.StoredRecord(
        key=key,
        revision=int(row["revision"]),
        document=documents.EncodedDocument(schema_ref=schema_ref, json_text=row["document"]),
        summary=row["summary"],
    )


def _put(
    sqlite_connection: sqlite3.Connection,
    change: record_changes.PutRecord,
    commit_cursor: int,
    projection_revision: str,
) -> None:
    key = change.key
    sqlite_connection.execute(_PUT_SQL, (
        key.owner,
        key.collection,
        key.key,
        key.scope.model_dump_json(),
        commit_cursor,
        projection_revision,
        change.document.schema_ref.model_dump_json(),
        change.document.json_text,
        change.summary,
    ))


def _delete(
    sqlite_connection: sqlite3.Connection,
    change: record_changes.DeleteRecord,
    commit_cursor: int,
    projection_revision: str,
) -> None:
    key = change.key
    sqlite_connection.execute(_DELETE_SQL, (
        commit_cursor,
        projection_revision,
        key.owner,
        key.collection,
        key.scope.model_dump_json(),
        key.key,
    ))
