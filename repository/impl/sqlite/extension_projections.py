# Copyright (c) 2026 Zhambyl Yermagambet
"""Read projection cursors and commit derived data in one transaction."""

import sqlite3
import time

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from repository.contract.extension_projections import ProjectionCommit
from repository.impl.sqlite import connection, extension_records, session_data_write

_CURSOR_SQL = (
    "SELECT commit_cursor FROM extension_projection_cursors "
    "WHERE owner=? AND scope=? AND history_revision=? AND generation=?"
)
_CURSOR_WRITE_SQL = (
    "INSERT INTO extension_projection_cursors(owner, scope, history_revision, generation, commit_cursor, updated_at) "
    "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(owner, scope, history_revision, generation) DO UPDATE SET "
    "commit_cursor=excluded.commit_cursor, updated_at=excluded.updated_at"
)
MISSING_CURSOR = 0


class SqliteExtensionProjectionRepository:
    """Read projection cursors and commit derived data atomically."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def committed_cursor(self, owner: str, scope: ExtensionScope, history_revision: str, generation: str) -> int:
        """Return the last committed projection cursor.

        Returns:
            The stored commit cursor, or zero before the first commit.

        """
        cursor_values = (owner, scope.model_dump_json(), history_revision, generation)
        with self.database.read() as connection_handle:
            row = connection_handle.execute(_CURSOR_SQL, cursor_values).fetchone()
        return MISSING_CURSOR if row is None else int(row["commit_cursor"])

    def scopes_after(
        self, history_revision: str, after_cursor: int, limit: int,
    ) -> tuple[ExtensionScope, ...]:
        """Read the distinct scopes with accepted facts after a cursor.

        Returns:
            The distinct scopes in a stable order.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(
                "SELECT DISTINCT scope FROM canonical_events "
                "WHERE history_revision=? AND cursor>? ORDER BY scope LIMIT ?",
                (history_revision, after_cursor, limit),
            ).fetchall()
        adapter = TypeAdapter[ExtensionScope](ExtensionScope)
        return tuple(adapter.validate_json(row["scope"]) for row in rows)

    def apply_projection(self, projection_commit: ProjectionCommit) -> None:
        """Write one projection's entries, records, and cursor in one transaction."""
        with self.database.write(notify_readers=not projection_commit.changes.empty) as connection_handle:
            _write_changes(connection_handle, projection_commit)
            connection_handle.execute(_CURSOR_WRITE_SQL, (
                projection_commit.owner,
                projection_commit.scope.model_dump_json(),
                projection_commit.history_revision,
                projection_commit.generation,
                projection_commit.commit_cursor,
                time.time(),
            ))


def _write_changes(connection_handle: sqlite3.Connection, projection_commit: ProjectionCommit) -> None:
    if projection_commit.session_id is not None:
        session_data_write.write_changes(
            connection_handle, projection_commit.session_id, projection_commit.changes, projection_commit.commit_cursor,
        )
    elif projection_commit.changes.records:
        extension_records.apply_record_changes(
            connection_handle,
            projection_commit.changes.records,
            projection_commit.commit_cursor,
            projection_commit.generation,
        )
