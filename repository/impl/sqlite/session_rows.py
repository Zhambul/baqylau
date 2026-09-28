# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the working directory of each stored session from the main database."""

from dataclasses import dataclass

from domain.ids import ActorId, HarnessName, SessionId
from repository.contract.session_rows import SessionRow
from repository.impl.sqlite import connection

SELECT_ROWS = (
    "SELECT session_id, lead_actor_id, harness, working_directory FROM sessions "
    "WHERE working_directory IS NOT NULL AND working_directory != '' ORDER BY created_at DESC, session_id"
)


@dataclass(frozen=True)
class SqliteSessionRows:
    """Read session rows in one read transaction."""

    database: connection.SqliteDatabase

    def session_rows(self) -> tuple[SessionRow, ...]:
        """Read every session with a working directory, newest first.

        Returns:
            The rows.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(SELECT_ROWS).fetchall()
        return tuple(
            SessionRow(
                session_id=SessionId(row["session_id"]), lead_actor_id=ActorId(row["lead_actor_id"]),
                harness=HarnessName(row["harness"]), working_directory=row["working_directory"],
            )
            for row in rows
        )
