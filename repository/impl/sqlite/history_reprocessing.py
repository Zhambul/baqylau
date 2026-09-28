# Copyright (c) 2026 Zhambyl Yermagambet
"""Record candidate histories of closed sessions, and switch one with the default history."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from core.work_queue import WorkKind
from domain.ids import SessionId
from repository.contract.history_reprocessing import HistoryComparison, HistoryReprocessing, ReprocessingState
from repository.impl.sqlite import history_checks, history_switch

if TYPE_CHECKING:
    import sqlite3

    from repository.contract.folded_session import FoldedSession
    from repository.impl.sqlite import connection

DEFAULT_HISTORY = history_checks.DEFAULT_HISTORY
_ROW_SQL = "SELECT * FROM history_reprocessings WHERE history_revision=?"
_BUSY_SQL = (
    "SELECT 1 FROM history_reprocessings WHERE session_id=? AND state IN ('building', 'ready', 'switching')"
)
_CREATE_SQL = (
    "INSERT INTO history_reprocessings(history_revision, session_id, state, replay_cursor, live_head, "
    "created_at, updated_at) VALUES(?, ?, 'building', 0, ?, ?, ?)"
)
_SETTLE_SQL = (
    "UPDATE history_reprocessings SET state=?, live_head=?, comparison=COALESCE(?, comparison), "
    "diagnostic=?, updated_at=? WHERE history_revision=?"
)


class SqliteHistoryReprocessingRepository:
    """Create, advance, settle, and switch candidate histories of closed sessions."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def create(self, session_id: SessionId) -> HistoryReprocessing:
        """Start one candidate history after the V1 boundary checks.

        Returns:
            The new building candidate.

        """
        history_revision = f"history-{uuid4().hex}"
        with self.database.write(WorkKind.CANONICAL, notify_readers=False) as connection_handle:
            history_checks.require_closed(connection_handle, session_id)
            runtime = connection_handle.execute(
                "SELECT committed_runtime FROM extension_lifecycle_head WHERE id=1",
            ).fetchone()
            if runtime is None or runtime["committed_runtime"] is None:
                history_checks.refuse("reprocessing needs an active extension runtime")
            if connection_handle.execute(_BUSY_SQL, (session_id,)).fetchone() is not None:
                history_checks.refuse("the session already has a candidate history")
            now = time.time()
            connection_handle.execute(
                "INSERT INTO canonical_histories(history_revision, created_at) VALUES(?, ?)", (history_revision, now),
            )
            live_head = history_checks.session_head(connection_handle, session_id, DEFAULT_HISTORY)
            connection_handle.execute(_CREATE_SQL, (history_revision, session_id, live_head, now, now))
            return _row(connection_handle, history_revision)

    def read(self, history_revision: str) -> HistoryReprocessing | None:
        """Read one candidate history.

        Returns:
            The candidate, or None when it does not exist.

        """
        with self.database.read() as connection_handle:
            row = connection_handle.execute(_ROW_SQL, (history_revision,)).fetchone()
        return None if row is None else _decode(row)

    def in_state(self, reprocessing_state: ReprocessingState) -> tuple[HistoryReprocessing, ...]:
        """Read the candidate histories in one state, oldest first.

        Returns:
            The matching candidates.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(
                "SELECT * FROM history_reprocessings WHERE state=? ORDER BY created_at", (reprocessing_state,),
            ).fetchall()
        return tuple(_decode(row) for row in rows)

    def advance(self, history_revision: str, replay_cursor: int) -> None:
        """Record the last replayed original input."""
        with self.database.write(notify_readers=False) as connection_handle:
            connection_handle.execute(
                "UPDATE history_reprocessings SET replay_cursor=?, updated_at=? WHERE history_revision=?",
                (replay_cursor, time.time(), history_revision),
            )

    def settle(
        self,
        history_revision: str,
        reprocessing_state: ReprocessingState,
        history_comparison: HistoryComparison | None = None,
        diagnostic: str | None = None,
    ) -> None:
        """Record a replayed, failed, or switch-requested candidate.

        A switch request records the live head at that moment, and the switch
        refuses if the session's live history changes after it.
        """
        comparison = None if history_comparison is None else history_comparison.model_dump_json()
        with self.database.write(WorkKind.CANONICAL, notify_readers=False) as connection_handle:
            stored = _row(connection_handle, history_revision)
            live_head = stored.live_head
            if reprocessing_state == ReprocessingState.SWITCHING:
                live_head = history_checks.session_head(connection_handle, stored.session_id, DEFAULT_HISTORY)
            connection_handle.execute(_SETTLE_SQL, (
                reprocessing_state, live_head, comparison, diagnostic, time.time(), history_revision,
            ))

    def switch(self, history_revision: str, folded_session: FoldedSession) -> HistoryReprocessing:
        """Make the candidate the session's default history and keep the previous one for recovery.

        Returns:
            The candidate after the switch.

        """
        # The switch resets the session's projector cursors, so the projectors must run again.
        with self.database.write(WorkKind.CANONICAL) as connection_handle:
            connection_handle.execute("PRAGMA defer_foreign_keys = ON")
            candidate = _row(connection_handle, history_revision)
            if candidate.state != ReprocessingState.SWITCHING:
                history_checks.refuse("only a requested switch can change the session's history")
            history_switch.switch_history(connection_handle, candidate, folded_session)
            return _row(connection_handle, history_revision)


def _row(connection_handle: sqlite3.Connection, history_revision: str) -> HistoryReprocessing:
    row = connection_handle.execute(_ROW_SQL, (history_revision,)).fetchone()
    if row is None:
        history_checks.refuse("candidate history does not exist")
    return _decode(row)


def _decode(row: sqlite3.Row) -> HistoryReprocessing:
    comparison = row["comparison"]
    return HistoryReprocessing(
        history_revision=row["history_revision"],
        session_id=SessionId(row["session_id"]),
        state=ReprocessingState(row["state"]),
        replay_cursor=int(row["replay_cursor"]),
        live_head=int(row["live_head"]),
        comparison=None if comparison is None else HistoryComparison.model_validate_json(comparison),
        diagnostic=row["diagnostic"],
    )
