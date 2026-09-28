# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one session's original input and facts across all its actor scopes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repository.impl.sqlite import interpretation_codec, observation_codec

if TYPE_CHECKING:
    from domain.ids import SessionId
    from extensions.models.interpretations import StoredCanonicalFact
    from extensions.models.observations import StoredObservation
    from repository.impl.sqlite import connection

_SESSION_RAW_SQL = (
    "SELECT * FROM raw_events WHERE (session_id=? OR json_extract(scope, '$.session_id')=?) "
    "AND id>? ORDER BY id LIMIT ?"
)
_SESSION_FACTS_SQL = (
    "SELECT * FROM canonical_events WHERE history_revision=? "
    "AND (session_id=? OR json_extract(scope, '$.session_id')=?) ORDER BY cursor"
)


class SqliteSessionHistoryReader:
    """Read a session's input in arrival order and its facts of one history."""

    def __init__(self, database: connection.SqliteDatabase) -> None:
        """Store the database handle."""
        self.database = database

    def session_observations(
        self, session_id: SessionId, after_cursor: int, limit: int,
    ) -> tuple[StoredObservation, ...]:
        """Read every original input of one session, of all its actors, in arrival order.

        Returns:
            At most the limit, after the arrival cursor.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(_SESSION_RAW_SQL, (session_id, session_id, after_cursor, limit)).fetchall()
        return tuple(observation_codec.stored_observation(row) for row in rows)

    def session_facts(self, session_id: SessionId, history_revision: str) -> tuple[StoredCanonicalFact, ...]:
        """Read every fact of one session in one history, in cursor order.

        Returns:
            The session's facts of both branches.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(_SESSION_FACTS_SQL, (history_revision, session_id, session_id)).fetchall()
        return tuple(interpretation_codec.stored_fact(row) for row in rows)
