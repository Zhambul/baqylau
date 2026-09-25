# Copyright (c) 2026 Zhambyl Yermagambet
"""Read projection cursors and commit derived data in one transaction."""

import sqlite3
import time

from baqylau_extension_api.models.scopes import ExtensionScope

from repository.contract.extension_projections import ProjectionCommit
from repository.contract.pending_scope_query import PendingScopeQuery
from repository.contract.projection_generations import ProjectionSwitchError
from repository.impl.sqlite import (
    candidate_records,
    connection,
    extension_records,
    generation_heads,
    scope_heads,
    session_data_write,
)

_CURSOR_WRITE_SQL = (
    "INSERT INTO extension_projection_cursors(owner, scope, history_revision, generation, commit_cursor, updated_at) "
    "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(owner, scope, history_revision, generation) DO UPDATE SET "
    "commit_cursor=excluded.commit_cursor, updated_at=excluded.updated_at"
)


class SqliteExtensionProjectionRepository:
    """Read projection cursors and commit derived data atomically."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def committed_cursor(self, owner: str, scope: ExtensionScope, history_revision: str, generation: str) -> int:
        """Return the last committed projection cursor, or the owner's floor before the first commit.

        Returns:
            The stored commit cursor, the floor, or zero.

        """
        cursor_key = (owner, scope.model_dump_json(), history_revision, generation)
        with self.database.read() as connection_handle:
            return scope_heads.consumer_cursor(
                connection_handle, scope_heads.ConsumerCursorTable.PROJECTION, cursor_key,
            )

    def ensure_floor(self, owner: str, history_revision: str, generation: str) -> None:
        """Start the owner at the canonical head on its first live pass; keep an existing floor."""
        with self.database.write(notify_readers=False) as connection_handle:
            scope_heads.ensure_floor(
                connection_handle, scope_heads.ConsumerCursorTable.PROJECTION, (owner, history_revision, generation),
            )

    def pending_scopes(self, pending_scope_query: PendingScopeQuery) -> tuple[ExtensionScope, ...]:
        """Read the declared scopes with facts after this owner's projection cursor.

        Returns:
            The pending scopes, oldest head first.

        """
        with self.database.read() as connection_handle:
            return scope_heads.pending_scopes(
                connection_handle, scope_heads.ConsumerCursorTable.PROJECTION, pending_scope_query,
            )

    def apply_projection(self, projection_commit: ProjectionCommit) -> None:
        """Write one projection's entries, records, and cursor in one transaction.

        A commit of the owner's live generation writes the live tables and
        notifies readers. A commit of another generation writes the mirror
        tables of that candidate and notifies nobody.

        """
        owner, generation = projection_commit.owner, projection_commit.generation
        with self.database.read() as connection_handle:
            candidate = generation_heads.is_candidate(connection_handle, owner, generation)
        live_change = not candidate and not projection_commit.changes.empty
        with self.database.write(notify_readers=live_change) as connection_handle:
            _commit(connection_handle, projection_commit, candidate=candidate)


def _commit(connection_handle: sqlite3.Connection, projection_commit: ProjectionCommit, *, candidate: bool) -> None:
    """Write the commit to the live or mirror tables, then its cursor.

    Raises:
        ProjectionSwitchError: If the owner's live generation changed while this commit was prepared.

    """
    owner, generation = projection_commit.owner, projection_commit.generation
    if generation_heads.is_candidate(connection_handle, owner, generation) != candidate:
        message = "the owner's live projection generation changed during this commit"
        raise ProjectionSwitchError(message)
    if candidate:
        candidate_records.write_candidate_changes(
            connection_handle, generation, owner, projection_commit.changes, projection_commit.commit_cursor,
        )
    else:
        _write_changes(connection_handle, projection_commit)
    connection_handle.execute(_CURSOR_WRITE_SQL, (
        owner,
        projection_commit.scope.model_dump_json(),
        projection_commit.history_revision,
        generation,
        projection_commit.commit_cursor,
        time.time(),
    ))


def _write_changes(connection_handle: sqlite3.Connection, projection_commit: ProjectionCommit) -> None:
    if projection_commit.session_id is not None:
        session_data_write.write_changes(
            connection_handle, projection_commit.session_id, projection_commit.changes, projection_commit.commit_cursor,
            projection_commit.generation,
        )
    elif projection_commit.changes.records:
        extension_records.apply_record_changes(
            connection_handle,
            projection_commit.changes.records,
            projection_commit.commit_cursor,
            projection_commit.generation,
        )
