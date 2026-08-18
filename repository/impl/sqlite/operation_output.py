"""The follow list over SQLite.

Nothing here touches the filesystem: `remove_expired` returns what it deleted so
the caller unlinks. Deleting a user's file used to be a side effect of listing
the rows.
"""

from __future__ import annotations

from domain.ids import OperationId, SessionId
from domain.operations import OperationOutputFollowing
from repository.contract.operations import OperationOutputRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper

_COLUMNS = (
    "session_id, operation_id, harness, actor_id, parent_actor_id, "
    "source_path, chunk_source_type, delete_source, initial_size, "
    "initial_modified_at, wait_for_source_change, until, state, created_at"
)


class SqliteOperationOutputRepository(OperationOutputRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def save(self, following: OperationOutputFollowing) -> None:
        with self.database.write() as connection:
            connection.execute(
                f"INSERT OR IGNORE INTO operation_output({_COLUMNS}) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                mapper.operation_output_values(following),
            )

    def find_for_session(self, session_id: SessionId) -> tuple[OperationOutputFollowing, ...]:
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT * FROM operation_output WHERE session_id=? "
                "ORDER BY created_at, operation_id",
                (str(session_id),),
            ).fetchall()
        return tuple(
            mapper.operation_output_following(rows.operation_output(row)) for row in found
        )

    def mark_operation_finished(self, session_id: SessionId, operation_id: OperationId) -> None:
        with self.database.write() as connection:
            connection.execute(
                "UPDATE operation_output SET state='finishing' "
                "WHERE session_id=? AND operation_id=? AND until='operation_finished'",
                (str(session_id), str(operation_id)),
            )

    def mark_finishing(self, session_id: SessionId, operation_id: OperationId) -> None:
        with self.database.write() as connection:
            connection.execute(
                "UPDATE operation_output SET state='finishing' "
                "WHERE session_id=? AND operation_id=?",
                (str(session_id), str(operation_id)),
            )

    def outlive_operation(self, session_id: SessionId, operation_id: OperationId) -> None:
        with self.database.write() as connection:
            connection.execute(
                "UPDATE operation_output SET until='session_finished', state='active' "
                "WHERE session_id=? AND operation_id=?",
                (str(session_id), str(operation_id)),
            )

    def remove(self, session_id: SessionId, operation_id: OperationId) -> None:
        with self.database.write() as connection:
            connection.execute(
                "DELETE FROM operation_output WHERE session_id=? AND operation_id=?",
                (str(session_id), str(operation_id)),
            )

    def remove_expired(self, created_before: float) -> tuple[OperationOutputFollowing, ...]:
        with self.database.write() as connection:
            found = connection.execute(
                "SELECT * FROM operation_output WHERE created_at < ?", (created_before,)
            ).fetchall()
            if found:
                connection.execute(
                    "DELETE FROM operation_output WHERE created_at < ?", (created_before,)
                )
        return tuple(
            mapper.operation_output_following(rows.operation_output(row)) for row in found
        )
