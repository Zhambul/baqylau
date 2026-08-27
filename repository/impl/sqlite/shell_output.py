"""The follow list over SQLite.

Nothing here touches the filesystem: `remove_expired` returns what it deleted so
the caller unlinks. Deleting a user's file used to be a side effect of listing
the rows.
"""

from __future__ import annotations

from domain.ids import SessionId, ShellId
from domain.shells import ShellOutputFollowing
from repository.contract.shell_output import ShellOutputRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper

_COLUMNS = (
    "session_id, shell_id, harness, actor_id, parent_actor_id, "
    "source_path, chunk_source_type, delete_source, initial_size, "
    "initial_modified_at, wait_for_source_change, until, state, created_at"
)


class SqliteShellOutputRepository(ShellOutputRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def save(self, shell_output_following: ShellOutputFollowing) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                f"INSERT OR IGNORE INTO shell_output({_COLUMNS}) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                mapper.shell_output_values(shell_output_following),
            )

    def find_for_session(self, session_id: SessionId) -> tuple[ShellOutputFollowing, ...]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM shell_output WHERE session_id=? "
                "ORDER BY created_at, shell_id, source_path",
                (str(session_id),),
            ).fetchall()
        return tuple(
            mapper.shell_output_following(rows.shell_output(row)) for row in found
        )

    def mark_shell_finished(self, session_id: SessionId, shell_id: ShellId) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "UPDATE shell_output SET state='finishing' "
                "WHERE session_id=? AND shell_id=? AND until='shell_finished'",
                (str(session_id), str(shell_id)),
            )

    def mark_finishing(self, session_id: SessionId, shell_id: ShellId) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "UPDATE shell_output SET state='finishing' "
                "WHERE session_id=? AND shell_id=?",
                (str(session_id), str(shell_id)),
            )

    def outlive_shell(self, session_id: SessionId, shell_id: ShellId) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "UPDATE shell_output SET until='session_finished', state='active' "
                "WHERE session_id=? AND shell_id=?",
                (str(session_id), str(shell_id)),
            )

    def remove(self, session_id: SessionId, shell_id: ShellId, source_path: str) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                "DELETE FROM shell_output "
                "WHERE session_id=? AND shell_id=? AND source_path=?",
                (str(session_id), str(shell_id), source_path),
            )

    def remove_expired(self, created_before: float) -> tuple[ShellOutputFollowing, ...]:
        with self.sqlite_database.write() as connection:
            found = connection.execute(
                "SELECT * FROM shell_output WHERE created_at < ?", (created_before,)
            ).fetchall()
            if found:
                connection.execute(
                    "DELETE FROM shell_output WHERE created_at < ?", (created_before,)
                )
        return tuple(
            mapper.shell_output_following(rows.shell_output(row)) for row in found
        )
