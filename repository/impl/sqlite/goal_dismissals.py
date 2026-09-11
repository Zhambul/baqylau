# Copyright (c) 2026 Zhambyl Yermagambet
"""Store goal dismissal with the read-model position that it applies to."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repository.contract.goal_dismissals import GoalDismissalRepository

if TYPE_CHECKING:
    import sqlite3

    from domain.ids import SessionId
    from domain.session_state import SessionGoal
    from repository.impl.sqlite.connection import SqliteDatabase


class SqliteGoalDismissalRepository(GoalDismissalRepository):
    """Store one dismissal per session."""

    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        """Set the database."""
        self.sqlite_database = sqlite_database

    def hidden(self, session_id: SessionId) -> bool:
        """Read the saved dismissal.

        Returns:
            True if the session has a dismissal.

        """
        with self.sqlite_database.read() as connection:
            return connection.execute(
                "SELECT 1 FROM goal_dismissals WHERE session_id=?", (str(session_id),),
            ).fetchone() is not None

    def dismiss(self, session_id: SessionId, objective: str) -> None:
        """Hide the goal only if it still matches the request.

        Raises:
            ValueError: If the goal changed or is not complete.

        """
        with self.sqlite_database.write() as connection:
            changed = connection.execute(
                "INSERT INTO goal_dismissals(session_id, objective, dismissed_cursor) "
                "SELECT session_id, ?, (SELECT canonical_cursor FROM reaction_progress WHERE id=1) "
                "FROM session_data WHERE session_id=? "
                "AND json_extract(payload, '$.goal.state')='completed' "
                "AND json_extract(payload, '$.goal.objective')=? "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "objective=excluded.objective, dismissed_cursor=excluded.dismissed_cursor",
                (objective, str(session_id), objective),
            ).rowcount
            if not changed:
                msg = "the goal changed or is not completed"
                raise ValueError(msg)


def expire(
    connection: sqlite3.Connection,
    session_id: SessionId,
    session_goal: SessionGoal | None,
    canonical_cursor: int,
) -> None:
    """Restore a changed goal without losing dismissals during a rebuild."""
    objective = session_goal.objective if session_goal is not None and session_goal.state == "completed" else None
    connection.execute(
        "DELETE FROM goal_dismissals WHERE session_id=? AND dismissed_cursor < ? AND objective IS NOT ?",
        (str(session_id), canonical_cursor, objective),
    )
