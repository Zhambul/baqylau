"""Structured pipeline diagnostics from the two application databases."""

from __future__ import annotations

from domain.ids import SessionId
from repository.contract.diagnostics import (
    AuditProblem,
    DiagnosticsCheckpoint,
    DiagnosticsReport,
    InterpretationProblem,
)
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper.raw_payloads import restored


class SqliteDiagnosticsRepository:
    def __init__(
        self,
        main_sqlite_database: SqliteDatabase,
        audit_sqlite_database: SqliteDatabase,
    ) -> None:
        self.main_database = main_sqlite_database
        self.audit_database = audit_sqlite_database

    def checkpoint(self) -> DiagnosticsCheckpoint:
        with self.main_database.read() as connection:
            raw = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS cursor FROM raw_events"
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM pending_raw_events"
            ).fetchone()
            canonical = connection.execute(
                "SELECT COALESCE(MAX(cursor), 0) AS cursor FROM canonical_events"
            ).fetchone()
            reaction = connection.execute(
                "SELECT canonical_cursor FROM reaction_progress WHERE id=1"
            ).fetchone()
        audit_cursor = 0
        if self.audit_database.exists():
            with self.audit_database.read() as connection:
                audit = connection.execute(
                    "SELECT COALESCE(MAX(id), 0) AS cursor FROM errors"
                ).fetchone()
                audit_cursor = int(audit["cursor"])
        return DiagnosticsCheckpoint(
            raw_event_cursor=int(raw["cursor"]),
            audit_error_cursor=audit_cursor,
            canonical_cursor=int(canonical["cursor"]),
            reaction_cursor=int(reaction["canonical_cursor"]) if reaction is not None else 0,
            pending_raw_event_count=int(pending["count"]),
        )

    def report(
        self,
        *,
        after_raw_event: int,
        through_raw_event: int,
        after_audit_error: int,
        through_audit_error: int,
    ) -> DiagnosticsReport:
        with self.main_database.read() as connection:
            raw_rows = connection.execute(
                "SELECT raw_events.id, raw_events.source_type, raw_events.source_position, "
                "raw_events.payload, raw_events.payload_codec, "
                "interpretations.decision, interpretations.reason "
                "FROM raw_events LEFT JOIN interpretations USING(raw_event_id) "
                "WHERE raw_events.id>? AND raw_events.id<=? ORDER BY raw_events.id",
                (after_raw_event, through_raw_event),
            ).fetchall()
        problems = tuple(
            InterpretationProblem(
                raw_event_cursor=int(row["id"]),
                source_type=str(row["source_type"]),
                source_position=str(row["source_position"]),
                decision=None if row["decision"] is None else str(row["decision"]),
                reason=None if row["reason"] is None else str(row["reason"]),
                payload=restored(bytes(row["payload"]), str(row["payload_codec"]))[:300].decode(
                    "utf-8", "replace"
                ),
            )
            for row in raw_rows
            if row["decision"] not in ("translated", "ignored_nonsemantic")
        )
        errors: tuple[AuditProblem, ...] = ()
        if self.audit_database.exists():
            with self.audit_database.read() as connection:
                error_rows = connection.execute(
                    "SELECT id, session_id, script, func, context FROM errors "
                    "WHERE id>? AND id<=? ORDER BY id",
                    (after_audit_error, through_audit_error),
                ).fetchall()
            errors = tuple(
                AuditProblem(
                    error_cursor=int(row["id"]),
                    session_id=SessionId(str(row["session_id"])),
                    component=str(row["script"]),
                    action=str(row["func"]),
                    context=str(row["context"]),
                )
                for row in error_rows
            )
        return DiagnosticsReport(
            raw_event_count=len(raw_rows),
            verdict_count=sum(row["decision"] is not None for row in raw_rows),
            interpretation_problems=problems,
            audit_problems=errors,
        )
