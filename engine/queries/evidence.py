"""Read-only inspection of raw observations and their canonical interpretations."""

from __future__ import annotations

from dataclasses import dataclass

from domain.events import CanonicalEvent, EventPayload
from domain.ids import ActorId, CanonicalEventId, RawEventId, SessionId
from engine.store.database import connect
from engine.store.canonical import CanonicalEventStore


@dataclass(frozen=True)
class CanonicalEvidence:
    event: CanonicalEvent[EventPayload]
    accepted_at: float
    event_order: int
    storage_result: str


@dataclass(frozen=True)
class TranslationEvidence:
    raw_event_id: RawEventId
    session_id: SessionId
    harness: str
    source_type: str
    source_name: str
    source_position: str
    actor_id: ActorId
    parent_actor_id: ActorId | None
    observed_at: float
    encoding: str
    payload: bytes
    terminal_window_id: str | None
    harness_process_id: int | None
    account_id: str | None
    account_display_name: str | None
    translator_version: str
    decision: str
    reason: str | None
    completed_at: float
    canonical: tuple[CanonicalEvidence, ...]


class EvidenceQueries:
    def __init__(self, canonical_store: CanonicalEventStore) -> None:
        self.canonical_store = canonical_store

    def raw_event(self, raw_event_id: RawEventId) -> TranslationEvidence | None:
        with connect(self.canonical_store.database_path) as connection:
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
            canonical_rows = connection.execute(
                "SELECT canonical_events.*, canonical_provenance.event_order, "
                "canonical_provenance.storage_result "
                "FROM canonical_provenance "
                "JOIN canonical_events USING(event_id) "
                "WHERE raw_event_id=? ORDER BY canonical_provenance.event_order",
                (str(raw_event_id),),
            ).fetchall()
        return TranslationEvidence(
            raw_event_id=RawEventId(raw["raw_event_id"]),
            session_id=SessionId(raw["session_id"]),
            harness=raw["harness"],
            source_type=raw["source_type"],
            source_name=raw["source_name"],
            source_position=raw["source_position"],
            actor_id=ActorId(raw["actor_id"]),
            parent_actor_id=(
                ActorId(raw["parent_actor_id"])
                if raw["parent_actor_id"] is not None
                else None
            ),
            observed_at=raw["observed_at"],
            encoding=raw["encoding"],
            payload=raw["payload"],
            terminal_window_id=raw["terminal_window_id"],
            harness_process_id=raw["harness_process_id"],
            account_id=raw["account_id"],
            account_display_name=raw["account_display_name"],
            translator_version=raw["translator_version"] or "",
            decision=raw["decision"] or "untranslated",
            reason=raw["reason"],
            completed_at=raw["completed_at"] or 0.0,
            canonical=tuple(
                CanonicalEvidence(
                    event=self._stored_event(CanonicalEventId(row["event_id"])).event,
                    accepted_at=row["accepted_at"],
                    event_order=row["event_order"],
                    storage_result=row["storage_result"],
                )
                for row in canonical_rows
            ),
        )

    def session(self, session_id: SessionId) -> tuple[TranslationEvidence, ...]:
        with connect(self.canonical_store.database_path) as connection:
            rows = connection.execute(
                "SELECT raw_event_id FROM raw_events WHERE session_id=? "
                "ORDER BY id",
                (str(session_id),),
            ).fetchall()
        return tuple(
            evidence
            for row in rows
            if (evidence := self.raw_event(RawEventId(row["raw_event_id"]))) is not None
        )

    def _stored_event(self, event_id: CanonicalEventId):
        stored_event = self.canonical_store.event(event_id)
        if stored_event is None:
            raise RuntimeError(f"canonical evidence is missing event {event_id}")
        return stored_event
