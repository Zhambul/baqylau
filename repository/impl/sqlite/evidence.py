"""The forensic join: one observation, its verdict, and the facts it produced.

This is the read the audit CLI makes. It used to be hand-written SQL in
`engine/queries/evidence.py` — a module that owned none of the four tables it
joined — and it issued two queries per raw event plus two per canonical event.
A five-thousand-event session was twenty thousand round trips; it is four here.
"""

from __future__ import annotations

import sqlite3

from domain.codec import CanonicalEventCodec
from domain.ids import RawEventId, SessionId
from domain.records import CanonicalEvidence, CanonicalStorageResult, TranslationEvidence
from repository.contract.facts import TranslationEvidenceRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper


class SqliteTranslationEvidenceRepository(TranslationEvidenceRepository):
    def __init__(self, database: SqliteDatabase, codec: CanonicalEventCodec | None = None) -> None:
        self.database = database
        self.codec = codec or CanonicalEventCodec()

    def evidence(self, raw_event_id: RawEventId) -> TranslationEvidence | None:
        with self.database.read() as connection:
            raw = connection.execute(
                "SELECT raw_events.*, translation_records.translator_version, "
                "translation_records.decision, translation_records.reason, "
                "translation_records.completed_at "
                "FROM raw_events LEFT JOIN translation_records USING(raw_event_id) "
                "WHERE raw_event_id=?",
                (str(raw_event_id),),
            ).fetchone()
            if raw is None:
                return None
            canonical = connection.execute(
                "SELECT canonical_events.*, canonical_provenance.event_order, "
                "canonical_provenance.storage_result "
                "FROM canonical_provenance "
                "JOIN canonical_events USING(event_id) "
                "WHERE raw_event_id=? ORDER BY canonical_provenance.event_order",
                (str(raw_event_id),),
            ).fetchall()
        return self._evidence(raw, canonical)

    def evidence_for_session(self, session_id: SessionId) -> tuple[TranslationEvidence, ...]:
        with self.database.read() as connection:
            raw_rows = connection.execute(
                "SELECT raw_events.*, translation_records.translator_version, "
                "translation_records.decision, translation_records.reason, "
                "translation_records.completed_at "
                "FROM raw_events LEFT JOIN translation_records USING(raw_event_id) "
                "WHERE raw_events.session_id=? ORDER BY raw_events.id",
                (str(session_id),),
            ).fetchall()
            canonical_rows = connection.execute(
                "SELECT canonical_provenance.raw_event_id, canonical_events.*, "
                "canonical_provenance.event_order, canonical_provenance.storage_result "
                "FROM canonical_provenance "
                "JOIN canonical_events USING(event_id) "
                "JOIN raw_events ON raw_events.raw_event_id = canonical_provenance.raw_event_id "
                "WHERE raw_events.session_id=? "
                "ORDER BY raw_events.id, canonical_provenance.event_order",
                (str(session_id),),
            ).fetchall()
        by_raw_event: dict[str, list[sqlite3.Row]] = {}
        for row in canonical_rows:
            by_raw_event.setdefault(row["raw_event_id"], []).append(row)
        return tuple(
            self._evidence(raw, by_raw_event.get(raw["raw_event_id"], []))
            for raw in raw_rows
        )

    def _evidence(self, raw: sqlite3.Row, canonical: list[sqlite3.Row]) -> TranslationEvidence:
        raw_row = rows.raw_event(raw)
        return TranslationEvidence(
            raw_event_id=RawEventId(raw_row.raw_event_id),
            session_id=SessionId(raw_row.session_id),
            harness=raw_row.harness,
            source_type=raw_row.source_type,
            source_name=raw_row.source_name,
            source_position=raw_row.source_position,
            actor_id=raw_row.actor_id,
            parent_actor_id=raw_row.parent_actor_id,
            observed_at=raw_row.observed_at,
            encoding=raw_row.encoding,
            payload=raw_row.payload,
            terminal_window_id=raw_row.terminal_window_id,
            harness_process_id=raw_row.harness_process_id,
            account_id=raw_row.account_id,
            account_display_name=raw_row.account_display_name,
            translator_version=raw["translator_version"] or "",
            decision=raw["decision"] or "untranslated",
            reason=raw["reason"],
            completed_at=raw["completed_at"] or 0.0,
            canonical=tuple(
                CanonicalEvidence(
                    event=mapper.stored_canonical_event(
                        rows.canonical_event(row), (), self.codec
                    ).event,
                    accepted_at=row["accepted_at"],
                    event_order=row["event_order"],
                    storage_result=_storage_result(row["storage_result"]),
                )
                for row in canonical
            ),
        )


def _storage_result(value: str) -> CanonicalStorageResult:
    result: CanonicalStorageResult = value  # type: ignore[assignment]
    return result
