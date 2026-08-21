"""Canonical facts and their interpretation audits.

`record_translation` is the one multi-table write in the system. It is a single
method so that the transaction is decided here rather than by the caller: three
tables, one `BEGIN IMMEDIATE`, and nothing above the contract line ever holds a
connection.
"""

from __future__ import annotations

import sqlite3

from domain.events import CanonicalEvent, EventPayload
from domain.ids import CanonicalEventId, RawEventId, SessionId
from domain.records import (
    CanonicalStorageResult,
    InterpretationEventRecord,
    TranslationOutcome,
    InterpretationRecord,
)
from harness.models import RawEvent, TranslationResult
from repository.contract.facts import CanonicalEventRepository
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper

_INSERT_COLUMNS = (
    "event_id, schema_version, event_type, session_id, actor_id, turn_id, "
    "parent_actor_id, harness, occurred_at, terminal_window_id, "
    "harness_process_id, accepted_at, payload"
)


class SqliteCanonicalEventRepository(CanonicalEventRepository):
    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        self.sqlite_database = sqlite_database

    # --- the one write ---------------------------------------------------------

    def record_translation(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation_result: TranslationResult,
        completed_at: float,
    ) -> TranslationOutcome:
        accepted: list[CanonicalEvent[EventPayload]] = []
        deduplicated: list[CanonicalEvent[EventPayload]] = []
        record = InterpretationRecord(
            raw_event_id=raw_event.raw_event_id,
            translator_version=translator_version,
            decision=translation_result.decision,
            reason=translation_result.reason,
            completed_at=completed_at,
        )
        with self.sqlite_database.write() as connection:
            connection.execute(
                "INSERT INTO interpretations("
                "raw_event_id, translator_version, decision, reason, completed_at"
                ") VALUES(?, ?, ?, ?, ?)",
                mapper.interpretation_record_values(record),
            )
            for event_order, event in enumerate(translation_result.canonical_events):
                storage_result = self._append(connection, event, completed_at)
                connection.execute(
                    "INSERT INTO interpretation_events("
                    "event_id, raw_event_id, event_order, storage_result"
                    ") VALUES(?, ?, ?, ?)",
                    mapper.interpretation_event_values(
                        InterpretationEventRecord(
                            event.event_id, raw_event.raw_event_id, event_order, storage_result
                        )
                    ),
                )
                (accepted if storage_result == "accepted" else deduplicated).append(event)
        return TranslationOutcome(tuple(accepted), tuple(deduplicated))

    def _append(
        self,
        connection: sqlite3.Connection,
        event: CanonicalEvent[EventPayload],
        accepted_at: float,
    ) -> CanonicalStorageResult:
        existing = connection.execute(
            "SELECT 1 FROM canonical_events WHERE event_id=?", (str(event.event_id),)
        ).fetchone()
        if existing is not None:
            # A canonical event is an IDEMPOTENT projection: the identity names
            # the fact, so re-observing it is a no-op that only adds an interpretation event.
            # Several independent sources legitimately converge here and may
            # render one fact differently; the first writer stays authoritative.
            # Nothing is lost by not comparing the bodies — the later rendering
            # is fully recoverable from its own raw event, stored verbatim and
            # linked by the interpretation-event row written beside this.
            return "deduplicated"
        connection.execute(
            f"INSERT INTO canonical_events({_INSERT_COLUMNS}) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            mapper.canonical_event_values(event, accepted_at),
        )
        return "accepted"

    # --- reads -----------------------------------------------------------------

    def find(self, event_id: CanonicalEventId) -> CanonicalEvent[EventPayload] | None:
        with self.sqlite_database.read() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_events WHERE event_id=?", (str(event_id),)
            ).fetchone()
            if row is None:
                return None
            interpretation_events = connection.execute(
                "SELECT raw_event_id FROM interpretation_events WHERE event_id=? "
                "ORDER BY raw_event_id",
                (row["event_id"],),
            ).fetchall()
        return mapper.row_canonical_event(
            rows.canonical_event(row),
            tuple(RawEventId(entry["raw_event_id"]) for entry in interpretation_events),
        )

    def session_ids(self) -> tuple[SessionId, ...]:
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT session_id FROM canonical_events "
                "WHERE event_type='session.started' "
                "GROUP BY session_id "
                "ORDER BY MAX(COALESCE(occurred_at, accepted_at)) DESC"
            ).fetchall()
        return tuple(SessionId(row["session_id"]) for row in found)

    def page_from(self, cursor: int, limit: int) -> tuple[CanonicalEvent[EventPayload], ...]:
        if limit <= 0:
            raise ValueError("event page limit must be positive")
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM canonical_events WHERE cursor>? ORDER BY cursor LIMIT ?",
                (cursor, limit),
            ).fetchall()
        return tuple(mapper.row_canonical_event(rows.canonical_event(row)) for row in found)

