# Copyright (c) 2026 Zhambyl Yermagambet
"""Count each extension's consecutive worker failures in the main database."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from extensions.models.extension_health import ExtensionHealth, HealthFailure, HealthState
from repository.impl.sqlite.connection import SqliteDatabase

if TYPE_CHECKING:
    import sqlite3


_FAILURE_SQL = (
    "INSERT INTO extension_health(extension_id, state, consecutive_failures, last_failure_where, last_failure_at) "
    "VALUES(?, CASE WHEN ? <= 1 THEN 'failed' ELSE 'failing' END, 1, ?, ?) ON CONFLICT(extension_id) DO UPDATE SET "
    "consecutive_failures=consecutive_failures + 1, last_failure_where=excluded.last_failure_where, "
    "last_failure_at=excluded.last_failure_at, "
    "state=CASE WHEN consecutive_failures + 1 >= ? THEN 'failed' ELSE 'failing' END "
    "RETURNING *"
)
_SUCCESS_SQL = (
    "UPDATE extension_health SET state='healthy', consecutive_failures=0, last_success_at=? WHERE extension_id=?"
)


@dataclass(frozen=True)
class SqliteExtensionHealth:
    """Keep one health row for each extension that has failed at least once."""

    database: SqliteDatabase

    def read_health(self) -> tuple[ExtensionHealth, ...]:
        """Read every stored health row.

        Returns:
            The rows in extension order.

        """
        with self.database.read() as connection:
            rows = connection.execute("SELECT * FROM extension_health ORDER BY extension_id").fetchall()
        return tuple(_health(row) for row in rows)

    def record_failure(self, failure: HealthFailure) -> ExtensionHealth:
        """Add one consecutive failure in one statement.

        Returns:
            The new health; at the limit, the state is failed.

        """
        with self.database.write(notify_readers=False) as connection:
            row = connection.execute(
                _FAILURE_SQL, (failure.extension_id, failure.limit, failure.where, failure.at, failure.limit),
            ).fetchone()
        return _health(row)

    def record_success(self, owner: str, at: float) -> None:
        """Reset the count of one extension."""
        with self.database.write(notify_readers=False) as connection:
            connection.execute(_SUCCESS_SQL, (at, owner))


def _health(row: sqlite3.Row) -> ExtensionHealth:
    return ExtensionHealth(
        extension_id=str(row["extension_id"]), state=HealthState(str(row["state"])),
        consecutive_failures=int(row["consecutive_failures"]),
        last_failure_where=row["last_failure_where"], last_failure_at=row["last_failure_at"],
        last_success_at=row["last_success_at"],
    )
