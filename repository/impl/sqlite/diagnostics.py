# Copyright (c) 2026 Zhambyl Yermagambet
"""Structured pipeline diagnostics from the two application databases."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.ids import SessionId
from domain.records import RecordedTranslationDecision
from repository.contract.diagnostics import (
    AuditProblem,
    DiagnosticsCheckpoint,
    DiagnosticsReport,
    InterpretationProblem,
)
from repository.impl.sqlite.current_facts import current_tables
from repository.mapper.raw_payloads import restored

if TYPE_CHECKING:
    import sqlite3

    from repository.impl.sqlite.connection import SqliteDatabase

DECISION_COLUMN = "decision"
DIAGNOSTIC_PAYLOAD_BYTE_LIMIT = 300
ACCEPTED_DECISIONS = frozenset((
    RecordedTranslationDecision.TRANSLATED,
    RecordedTranslationDecision.IGNORED_NONSEMANTIC,
    RecordedTranslationDecision.SUPPRESSED,
))


def _diagnostic_payload(row: sqlite3.Row) -> str:
    payload = restored(bytes(row["payload"]), str(row["payload_codec"]))
    return payload[:DIAGNOSTIC_PAYLOAD_BYTE_LIMIT].decode("utf-8", "replace")


class SqliteDiagnosticsRepository:
    """Represent sqlite diagnostics repository."""

    def __init__(
        self,
        main_sqlite_database: SqliteDatabase,
        audit_sqlite_database: SqliteDatabase,
    ) -> None:
        """Initialize the object."""
        self.main_database = main_sqlite_database
        self.audit_database = audit_sqlite_database

    def checkpoint(self) -> DiagnosticsCheckpoint:
        """Return the checkpoint.

        Returns:
            Checkpoint.

        """
        raw, pending, canonical, reaction = self._main_checkpoint_rows()
        audit_cursor = self._audit_cursor()
        return DiagnosticsCheckpoint(
            raw_event_cursor=int(raw["cursor"]),
            audit_error_cursor=audit_cursor,
            canonical_cursor=int(canonical["cursor"]),
            reaction_cursor=0 if reaction is None else int(reaction["canonical_cursor"]),
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
        """Report.

        Returns:
            The diagnostics report.

        """
        with self.main_database.read() as connection:
            raw_rows = connection.execute(
                "SELECT raw_events.id, raw_events.source_type, raw_events.source_position, "  # noqa: S608 -- Fixed repository views.
                "raw_events.payload, raw_events.payload_codec, "
                "interpretations.decision, interpretations.reason "
                f"FROM raw_events LEFT JOIN {current_tables(connection).interpretations} "
                "AS interpretations USING(raw_event_id) "
                "WHERE raw_events.id>? AND raw_events.id<=? ORDER BY raw_events.id",
                (after_raw_event, through_raw_event),
            ).fetchall()
        problems = tuple(
            InterpretationProblem(
                raw_event_cursor=int(row["id"]),
                source_type=str(row["source_type"]),
                source_position=str(row["source_position"]),
                decision=None if row[DECISION_COLUMN] is None else str(row[DECISION_COLUMN]),
                reason=None if row["reason"] is None else str(row["reason"]),
                payload=_diagnostic_payload(row),
            )
            for row in raw_rows
            if row[DECISION_COLUMN] not in ACCEPTED_DECISIONS
        )
        errors: tuple[AuditProblem, ...] = ()
        if self.audit_database.exists():
            with self.audit_database.read() as connection:
                error_rows = connection.execute(
                    "SELECT id, session_id, script, func, context FROM errors WHERE id>? AND id<=? ORDER BY id",
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
            verdict_count=sum(row[DECISION_COLUMN] is not None for row in raw_rows),
            interpretation_problems=problems,
            audit_problems=errors,
        )

    def _main_checkpoint_rows(
        self,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row | None]:
        with self.main_database.read() as connection:
            raw = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS cursor FROM raw_events",
            ).fetchone()
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM pending_raw_events",
            ).fetchone()
            canonical = connection.execute(
                f"SELECT COALESCE(MAX(cursor), 0) AS cursor FROM {current_tables(connection).canonical}",  # noqa: S608 -- Fixed repository view.
            ).fetchone()
            reaction = connection.execute(
                "SELECT canonical_cursor FROM reaction_progress WHERE id=1",
            ).fetchone()
        return raw, pending, canonical, reaction

    def _audit_cursor(self) -> int:
        if not self.audit_database.exists():
            return 0
        with self.audit_database.read() as connection:
            audit = connection.execute(
                "SELECT COALESCE(MAX(id), 0) AS cursor FROM errors",
            ).fetchone()
        return int(audit["cursor"])
