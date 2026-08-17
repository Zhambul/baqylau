"""Pid-liveness claims over SQLite.

Lives in the runtime directory, which is why it is its own file: a claim
surviving a reboot would name a pid that has since been reused, and the
liveness probe would answer "alive" for an unrelated process.

Every failure degrades to `unavailable` rather than raising: a lock this cannot
reach is a lock nobody holds, and the caller decides what that means.
"""

from __future__ import annotations

import sqlite3

from core.process import process_is_alive
from domain.locks import LockOutcome
from repository.contract.locks import ProcessLockRepository
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import locks as mapper


class SqliteProcessLockRepository(ProcessLockRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def acquire(self, key: str, process_id: int) -> LockOutcome:
        try:
            with self.database.write() as connection:
                row = connection.execute(
                    "SELECT pid FROM claims WHERE key=?", (key,)
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO claims(key, pid, claimed_at) "
                        "VALUES(?, ?, strftime('%s','now'))",
                        (key, process_id),
                    )
                    return mapper.claimed()
                holder = int(row["pid"] or 0)
                if holder and holder != process_id and process_is_alive(holder):
                    return mapper.denied(holder)
                connection.execute(
                    "UPDATE claims SET pid=?, claimed_at=strftime('%s','now') WHERE key=?",
                    (process_id, key),
                )
                return mapper.claimed() if holder == process_id else mapper.stolen_stale(holder)
        except (sqlite3.Error, OSError):
            return mapper.unavailable()

    def holder(self, key: str) -> int | None:
        if not self.database.exists():
            return None
        try:
            with self.database.read() as connection:
                row = connection.execute(
                    "SELECT pid FROM claims WHERE key=?", (key,)
                ).fetchone()
        except (sqlite3.Error, OSError):
            return None
        return int(row["pid"]) if row is not None and row["pid"] is not None else None

    def release(self, key: str, process_id: int) -> None:
        if not self.database.exists():
            return
        try:
            with self.database.write() as connection:
                # Only if still ours: a claim stolen from us must not be dropped
                # by the process that lost it.
                connection.execute(
                    "DELETE FROM claims WHERE key=? AND pid=?", (key, process_id)
                )
        except (sqlite3.Error, OSError):
            pass
