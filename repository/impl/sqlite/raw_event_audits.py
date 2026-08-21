"""The forensic join: one observation, its verdict, and the facts it produced.

This is the read the audit CLI makes. It used to be hand-written SQL in
an engine query module — a layer that owned none of the four tables it
joined — and it issued two queries per raw event plus two per canonical event.
A five-thousand-event session was twenty thousand round trips; it is four here.
"""

from __future__ import annotations

import sqlite3

from domain.ids import RawEventId, SessionId
from domain.records import (
    CanonicalStorageResult,
    InterpretationAudit,
    InterpretationAuditEvent,
    RecordedTranslationDecision,
)
from harness.models import RawEventAudit
from repository.contract.facts import RawEventAuditRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper


class SqliteRawEventAuditRepository(RawEventAuditRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    def audit(self, raw_event_id: RawEventId) -> RawEventAudit | None:
        with self.sqlite_database.read() as connection:
            raw = connection.execute(
                "SELECT raw_events.*, interpretations.translator_version, "
                "interpretations.decision, interpretations.reason, "
                "interpretations.completed_at "
                "FROM raw_events LEFT JOIN interpretations USING(raw_event_id) "
                "WHERE raw_event_id=?",
                (str(raw_event_id),),
            ).fetchone()
            if raw is None:
                return None
            canonical = connection.execute(
                "SELECT canonical_events.*, interpretation_events.event_order, "
                "interpretation_events.storage_result "
                "FROM interpretation_events "
                "JOIN canonical_events USING(event_id) "
                "WHERE raw_event_id=? ORDER BY interpretation_events.event_order",
                (str(raw_event_id),),
            ).fetchall()
        return self._audit(raw, canonical)

    def audits_for_session(self, session_id: SessionId) -> tuple[RawEventAudit, ...]:
        with self.sqlite_database.read() as connection:
            raw_rows = connection.execute(
                "SELECT raw_events.*, interpretations.translator_version, "
                "interpretations.decision, interpretations.reason, "
                "interpretations.completed_at "
                "FROM raw_events LEFT JOIN interpretations USING(raw_event_id) "
                "WHERE raw_events.session_id=? ORDER BY raw_events.id",
                (str(session_id),),
            ).fetchall()
            canonical_rows = connection.execute(
                "SELECT interpretation_events.raw_event_id, canonical_events.*, "
                "interpretation_events.event_order, interpretation_events.storage_result "
                "FROM interpretation_events "
                "JOIN canonical_events USING(event_id) "
                "JOIN raw_events ON raw_events.raw_event_id = interpretation_events.raw_event_id "
                "WHERE raw_events.session_id=? "
                "ORDER BY raw_events.id, interpretation_events.event_order",
                (str(session_id),),
            ).fetchall()
        by_raw_event: dict[str, list[sqlite3.Row]] = {}
        for row in canonical_rows:
            by_raw_event.setdefault(row["raw_event_id"], []).append(row)
        return tuple(
            self._audit(raw, by_raw_event.get(raw["raw_event_id"], []))
            for raw in raw_rows
        )

    def _audit(self, raw: sqlite3.Row, canonical: list[sqlite3.Row]) -> RawEventAudit:
        raw_row = rows.raw_event(raw)
        return RawEventAudit(
            raw_event=mapper.raw_event(raw_row),
            interpretation=(
                InterpretationAudit(
                    translator_version=raw["translator_version"],
                    decision=_decision(raw["decision"]),
                    reason=raw["reason"],
                    completed_at=raw["completed_at"],
                    events=tuple(
                        InterpretationAuditEvent(
                            event=mapper.row_canonical_event(rows.canonical_event(row)),
                            accepted_at=row["accepted_at"],
                            event_order=row["event_order"],
                            storage_result=_storage_result(row["storage_result"]),
                        )
                        for row in canonical
                    ),
                )
                if raw["decision"] is not None
                else None
            ),
        )


def _storage_result(value: str) -> CanonicalStorageResult:
    result: CanonicalStorageResult = value  # type: ignore[assignment]
    return result


def _decision(value: str) -> RecordedTranslationDecision:
    decision: RecordedTranslationDecision = value  # type: ignore[assignment]
    return decision
