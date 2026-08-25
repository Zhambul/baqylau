"""The `sessions` table over SQLite."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace

from domain.ids import HarnessName, SessionId
from harness.models import Session
from harness.registry import HarnessRegistry
from repository.contract.sessions import SessionRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper

_COLUMNS = (
    "session_id, lead_actor_id, harness, harness_session_id, source_reference, "
    "working_directory, project_directory, terminal_window_id, harness_process_id, created_at"
)


class SqliteSessionRepository(SessionRepository):
    """Constructed with a `HarnessRegistry`, every session it hands out carries
    its `.plugin`. Recorder-side callers construct it without one and get
    plugin-less sessions, which is all a recorder may need."""

    def __init__(self, sqlite_database: SqliteDatabase, harness_registry: HarnessRegistry | None = None) -> None:
        self.sqlite_database = sqlite_database
        self.harness_registry = harness_registry

    def save(self, harness: HarnessName, session: Session) -> None:
        with self.sqlite_database.write() as connection:
            connection.execute(
                f"INSERT INTO sessions({_COLUMNS}) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "  project_directory = COALESCE(sessions.project_directory, excluded.project_directory),"
                "  terminal_window_id = excluded.terminal_window_id,"
                "  harness_process_id = excluded.harness_process_id",
                mapper.session_values(harness, session, time.time()),
            )

    def find(self, session_id: SessionId) -> Session | None:
        with self.sqlite_database.read() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE session_id=?", (str(session_id),)).fetchone()
        return self._session(row) if row is not None else None

    def watchable(self) -> tuple[Session, ...]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM sessions WHERE lifecycle = 'running' "
                "ORDER BY created_at DESC",
            ).fetchall()
        return tuple(self._session(row) for row in found)

    def _session(self, row: sqlite3.Row) -> Session:
        session = mapper.session(rows.session(row))
        if self.harness_registry is None:
            return session
        return replace(session, plugin=self.harness_registry.plugin(HarnessName(row["harness"])))
