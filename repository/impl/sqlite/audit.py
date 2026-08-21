"""The audit database over SQLite.

The write side NEVER RAISES. It is called from `except` blocks in short-lived
hook processes, and an auditor that can fail takes down the thing it exists to
explain. Every method swallows storage failure, and the whole repository is a
no-op when the audit is switched off.

The read side opens the file read-only.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Mapping

from audit.models import (
    ApplicationError,
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamHandle,
    StreamOpened,
)
from domain.ids import SessionId
from repository.contract.audit import (
    AuditReadRepository,
    AuditWriteRepository,
)
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import audit as mapper
from repository.model.sql import SqlValues


def audit_enabled() -> bool:
    return os.environ.get("BAQYLAU_AUDIT", "1") != "0"


class SqliteAuditWriteRepository(AuditWriteRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def record_error(self, application_error_record: ApplicationErrorRecord) -> None:
        self._insert(
            "INSERT INTO errors(ts, session_id, script, func, traceback, context, pid) "
            "VALUES(?,?,?,?,?,?,?)",
            mapper.error_values(application_error_record),
        )

    def record_state_file(self, state_file_record: StateFileRecord) -> None:
        self._insert(
            "INSERT INTO state_files(ts, session_id, path, action, content, script, pid) "
            "VALUES(?,?,?,?,?,?,?)",
            mapper.state_file_values(state_file_record),
        )

    def record_spawn(self, spawn_record: SpawnRecord) -> None:
        self._insert(
            "INSERT INTO spawns(ts, session_id, parent_script, child_pid, argv, purpose) "
            "VALUES(?,?,?,?,?,?)",
            mapper.spawn_values(spawn_record),
        )

    def open_stream(self, stream_opened: StreamOpened) -> StreamHandle | None:
        if not audit_enabled():
            return None
        try:
            with self.sqlite_database.write() as connection:
                cursor = connection.execute(
                    "INSERT INTO streams(session_id, kind, agent_id, task_id, src_path, "
                    "pid, started_at) VALUES(?,?,?,?,?,?,?)",
                    mapper.stream_values(stream_opened),
                )
                # lastrowid is Optional in the DB-API: it is set after this
                # INSERT, but int() on the None branch would raise, and this
                # method is already declared to answer None when it cannot open.
                return StreamHandle(cursor.lastrowid) if cursor.lastrowid else None
        except (sqlite3.Error, OSError):
            return None

    def close_stream(
        self,
        stream_handle: StreamHandle | None,
        end_reason: str,
        lines_emitted: int | None,
    ) -> None:
        if stream_handle is None:
            return
        self._insert(
            "UPDATE streams SET ended_at=?, end_reason=?, lines_emitted=? WHERE id=?",
            (time.time(), end_reason, lines_emitted, stream_handle.stream_id),
        )

    def _insert(self, statement: str, values: SqlValues) -> None:
        if not audit_enabled():
            return
        try:
            with self.sqlite_database.write() as connection:
                connection.execute(statement, values)
        except (sqlite3.Error, OSError):
            # A broken auditor must never take down the thing it exists to
            # explain. There is nowhere left to report this to.
            pass


class SqliteAuditReadRepository(AuditReadRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def errors_for_session(self, session_id: SessionId) -> tuple[ApplicationError, ...]:
        if not self.sqlite_database.exists():
            return ()
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM errors WHERE session_id=? ORDER BY id", (str(session_id),)
            ).fetchall()
        return tuple(mapper.application_error(rows.error(row)) for row in found)

    def error_counts(self) -> Mapping[SessionId, int]:
        if not self.sqlite_database.exists():
            return {}
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT session_id, COUNT(*) AS error_count FROM errors "
                "WHERE session_id != '' GROUP BY session_id"
            ).fetchall()
        return {SessionId(row["session_id"]): int(row["error_count"]) for row in found}
