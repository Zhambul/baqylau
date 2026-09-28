# Copyright (c) 2026 Zhambyl Yermagambet
"""Store observer jobs with their cursor, and job outcomes with their output."""

import sqlite3
import time
from dataclasses import dataclass

from baqylau_extension_api.models.scopes import ExtensionScope

from core.work_queue import WorkKind
from domain.ids import CanonicalEventId
from repository.contract.extension_jobs import ExtensionJob
from repository.contract.extension_observers import ObserverAcceptance, ObserverCursor, ObserverSettlement
from repository.contract.pending_scope_query import PendingScopeQuery
from repository.impl.sqlite import (
    connection,
    extension_jobs,
    observation_validation,
    observation_writes,
    scope_heads,
)

_CURSOR_WRITE_SQL = (
    "INSERT INTO extension_observer_cursors(owner, scope, history_revision, generation, commit_cursor, updated_at) "
    "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(owner, scope, history_revision, generation) DO UPDATE SET "
    "commit_cursor=excluded.commit_cursor, updated_at=excluded.updated_at"
)
_CAUSE_DEPTH_SQL = """
WITH RECURSIVE chain(event_id, depth) AS (
    SELECT ?, 0
    UNION
    SELECT cause.value, chain.depth + 1
    FROM chain
    JOIN interpretation_events AS link
        ON link.history_revision = ? AND link.event_id = chain.event_id
    JOIN raw_events AS raw ON raw.raw_event_id = link.raw_event_id
    JOIN json_each(json_extract(raw.extension_metadata, '$.causes')) AS cause
    WHERE raw.extension_metadata IS NOT NULL AND chain.depth < ?
)
SELECT MAX(depth) AS depth FROM chain
"""


@dataclass(frozen=True)
class SqliteExtensionObserverRepository:
    """Read observer cursors and commit observer jobs with them."""

    database: connection.SqliteDatabase

    def committed_cursor(self, owner: str, scope: ExtensionScope, history_revision: str, generation: str) -> int:
        """Return the last committed observer cursor, or the owner's floor before the first commit.

        Returns:
            The stored commit cursor, the floor, or zero.

        """
        cursor_key = (owner, scope.model_dump_json(), history_revision, generation)
        with self.database.read() as connection_handle:
            return scope_heads.consumer_cursor(connection_handle, scope_heads.ConsumerCursorTable.OBSERVER, cursor_key)

    def ensure_floor(self, owner: str, history_revision: str, generation: str) -> None:
        """Start the owner at the canonical head on its first live pass; keep an existing floor."""
        with self.database.write(notify_readers=False) as connection_handle:
            scope_heads.ensure_floor(
                connection_handle, scope_heads.ConsumerCursorTable.OBSERVER, (owner, history_revision, generation),
            )

    def pending_scopes(self, pending_scope_query: PendingScopeQuery) -> tuple[ExtensionScope, ...]:
        """Read the declared scopes with facts after this owner's observer cursor.

        Returns:
            The pending scopes, oldest head first.

        """
        with self.database.read() as connection_handle:
            return scope_heads.pending_scopes(
                connection_handle, scope_heads.ConsumerCursorTable.OBSERVER, pending_scope_query,
            )

    def accept(self, observer_acceptance: ObserverAcceptance) -> ExtensionJob:
        """Store one observer job and advance its cursor in one transaction.

        Returns:
            The accepted or already accepted observer job.

        """
        with self.database.write() as connection_handle:
            job = extension_jobs.accept_observer(connection_handle, observer_acceptance.job)
            _write_cursor(connection_handle, observer_acceptance.cursor)
        return job

    def advance(self, observer_cursor: ObserverCursor) -> None:
        """Advance the cursor past facts that the observer does not select."""
        with self.database.write(notify_readers=False) as connection_handle:
            _write_cursor(connection_handle, observer_cursor)

    def cause_depth(self, event_id: CanonicalEventId, history_revision: str, limit: int) -> int:
        """Count the observer steps behind one fact, at most the limit.

        Returns:
            The longest cause chain found, never more than the limit.

        """
        with self.database.read() as connection_handle:
            row = connection_handle.execute(_CAUSE_DEPTH_SQL, (event_id, history_revision, limit)).fetchone()
        return int(row["depth"])

    def settle(self, observer_settlement: ObserverSettlement) -> ExtensionJob:
        """Store the final job state and its new observations in one transaction.

        Returns:
            The stored job after the change.

        """
        appended = observer_settlement.observations
        work_kinds = () if appended is None else (WorkKind.RAW,)
        with self.database.write(*work_kinds) as connection_handle:
            job = extension_jobs.update_state(connection_handle, observer_settlement.change)
            if appended is not None:
                observation_validation.validate_append(connection_handle, appended)
                observation_writes.append(connection_handle, appended)
        return job


def _write_cursor(connection_handle: sqlite3.Connection, observer_cursor: ObserverCursor) -> None:
    connection_handle.execute(_CURSOR_WRITE_SQL, (
        observer_cursor.owner,
        observer_cursor.scope.model_dump_json(),
        observer_cursor.history_revision,
        observer_cursor.generation,
        observer_cursor.commit_cursor,
        time.time(),
    ))
