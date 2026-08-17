"""Canonical facts, their verdicts, and their provenance.

`record_translation` is the one multi-table write in the system. It is a single
method so that the transaction is decided here rather than by the caller: three
tables, one `BEGIN IMMEDIATE`, and nothing above the contract line ever holds a
connection.
"""

from __future__ import annotations

import sqlite3
from typing import Mapping, Sequence

from domain.codec import CanonicalEventCodec
from domain.events import CanonicalEvent, EventPayload
from domain.ids import CanonicalEventId, RawEventId, SessionId
from domain.records import (
    CanonicalEventPage,
    CanonicalStorageResult,
    ProvenanceEntry,
    StoredCanonicalEvent,
    TranslationOutcome,
    TranslationRecord,
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
    def __init__(self, database: SqliteDatabase, codec: CanonicalEventCodec | None = None) -> None:
        self.database = database
        self.codec = codec or CanonicalEventCodec()

    # --- the one write ---------------------------------------------------------

    def record_translation(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation: TranslationResult,
        completed_at: float,
    ) -> TranslationOutcome:
        accepted: list[CanonicalEvent[EventPayload]] = []
        deduplicated: list[CanonicalEvent[EventPayload]] = []
        record = TranslationRecord(
            raw_event_id=raw_event.raw_event_id,
            translator_version=translator_version,
            decision=translation.decision,
            reason=translation.reason,
            completed_at=completed_at,
        )
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO translation_records("
                "raw_event_id, translator_version, decision, reason, completed_at"
                ") VALUES(?, ?, ?, ?, ?)",
                mapper.translation_record_values(record),
            )
            for event_order, event in enumerate(translation.canonical_events):
                storage_result = self._append(connection, event, completed_at)
                connection.execute(
                    "INSERT INTO canonical_provenance("
                    "event_id, raw_event_id, event_order, storage_result"
                    ") VALUES(?, ?, ?, ?)",
                    mapper.provenance_values(
                        ProvenanceEntry(
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
            # the fact, so re-observing it is a no-op that only adds provenance.
            # Several independent sources legitimately converge here and may
            # render one fact differently; the first writer stays authoritative.
            # Nothing is lost by not comparing the bodies — the later rendering
            # is fully recoverable from its own raw event, stored verbatim and
            # linked by the provenance row written beside this.
            return "deduplicated"
        connection.execute(
            f"INSERT INTO canonical_events({_INSERT_COLUMNS}) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            mapper.canonical_event_values(event, accepted_at, self.codec),
        )
        return "accepted"

    # --- reads -----------------------------------------------------------------

    def find(self, event_id: CanonicalEventId) -> StoredCanonicalEvent | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM canonical_events WHERE event_id=?", (str(event_id),)
            ).fetchone()
            if row is None:
                return None
            provenance = connection.execute(
                "SELECT raw_event_id FROM canonical_provenance WHERE event_id=? "
                "ORDER BY raw_event_id",
                (row["event_id"],),
            ).fetchall()
        return mapper.stored_canonical_event(
            rows.canonical_event(row),
            tuple(RawEventId(entry["raw_event_id"]) for entry in provenance),
            self.codec,
        )

    def session_ids(self) -> tuple[SessionId, ...]:
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT session_id FROM canonical_events "
                "WHERE event_type='session.started' "
                "GROUP BY session_id "
                "ORDER BY MAX(COALESCE(occurred_at, accepted_at)) DESC"
            ).fetchall()
        return tuple(SessionId(row["session_id"]) for row in found)

    def latest_cursor(self) -> int | None:
        with self.database.read() as connection:
            return self._latest_cursor(connection)

    def latest_session_cursors(
        self,
        session_ids: Sequence[SessionId],
        through_cursor: int | None,
    ) -> Mapping[SessionId, int]:
        if not session_ids:
            return {}
        placeholders = ",".join("?" for _session_id in session_ids)
        parameters: list[object] = [str(session_id) for session_id in session_ids]
        bound = ""
        if through_cursor is not None:
            bound = "AND cursor<=? "
            parameters.append(through_cursor)
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT session_id, MAX(cursor) AS latest_cursor FROM canonical_events "
                f"WHERE session_id IN ({placeholders}) {bound}"
                "GROUP BY session_id",
                tuple(parameters),
            ).fetchall()
        return {
            SessionId(row["session_id"]): int(row["latest_cursor"])
            for row in found
            if row["latest_cursor"] is not None
        }

    def page_after(self, session_id: SessionId, cursor: int, limit: int) -> CanonicalEventPage:
        if limit <= 0:
            raise ValueError("event page limit must be positive")
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? AND cursor>? "
                "ORDER BY cursor LIMIT ?",
                (str(session_id), cursor, limit + 1),
            ).fetchall()
            latest_cursor = self._latest_cursor(connection)
            has_more = len(found) > limit
            events = self._stored_events(connection, found[:limit])
        page_cursor = events[-1].cursor if events else cursor
        return CanonicalEventPage(events, page_cursor, latest_cursor, has_more)

    def page_through(self, session_id: SessionId, cursor: int | None) -> CanonicalEventPage:
        with self.database.read() as connection:
            if cursor is None:
                found = connection.execute(
                    "SELECT * FROM canonical_events WHERE session_id=? ORDER BY cursor",
                    (str(session_id),),
                ).fetchall()
            else:
                found = connection.execute(
                    "SELECT * FROM canonical_events WHERE session_id=? AND cursor<=? "
                    "ORDER BY cursor",
                    (str(session_id), cursor),
                ).fetchall()
            latest_cursor = self._latest_cursor(connection)
            events = self._stored_events(connection, found)
        page_cursor = events[-1].cursor if events else (cursor or 0)
        return CanonicalEventPage(events, page_cursor, latest_cursor, False)

    def page_tail(self, session_id: SessionId, cursor: int, limit: int) -> CanonicalEventPage:
        if limit <= 0:
            raise ValueError("event tail limit must be positive")
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? AND cursor<=? "
                "ORDER BY cursor DESC LIMIT ?",
                (str(session_id), cursor, limit + 1),
            ).fetchall()
            has_more = len(found) > limit
            selected = found[:limit]
            selected.reverse()
            events = self._stored_events(connection, selected)
            latest_cursor = self._latest_cursor(connection)
        page_cursor = events[-1].cursor if events else cursor
        return CanonicalEventPage(events, page_cursor, latest_cursor, has_more)

    def events_of_types(
        self,
        session_id: SessionId,
        event_types: tuple[str, ...],
        through_cursor: int,
    ) -> tuple[StoredCanonicalEvent, ...]:
        if not event_types:
            return ()
        placeholders = ",".join("?" for _event_type in event_types)
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? "
                f"AND event_type IN ({placeholders}) AND cursor<=? ORDER BY cursor",
                (str(session_id), *event_types, through_cursor),
            ).fetchall()
            return self._stored_events(connection, found)

    def events_between(
        self,
        session_id: SessionId,
        after_cursor: int,
        through_cursor: int,
    ) -> tuple[StoredCanonicalEvent, ...]:
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? "
                "AND cursor>? AND cursor<=? ORDER BY cursor",
                (str(session_id), after_cursor, through_cursor),
            ).fetchall()
            return self._stored_events(connection, found)

    # --- internals -------------------------------------------------------------

    @staticmethod
    def _latest_cursor(connection: sqlite3.Connection) -> int | None:
        row = connection.execute(
            "SELECT MAX(cursor) AS latest_cursor FROM canonical_events"
        ).fetchone()
        latest: int | None = row["latest_cursor"]
        return latest

    def _stored_events(
        self,
        connection: sqlite3.Connection,
        found: list[sqlite3.Row],
    ) -> tuple[StoredCanonicalEvent, ...]:
        if not found:
            return ()
        # One provenance query for the whole cursor range, not one per event.
        provenance = connection.execute(
            "SELECT canonical_provenance.event_id, canonical_provenance.raw_event_id "
            "FROM canonical_provenance "
            "JOIN canonical_events ON canonical_events.event_id=canonical_provenance.event_id "
            "WHERE canonical_events.session_id=? "
            "AND canonical_events.cursor>=? AND canonical_events.cursor<=? "
            "ORDER BY canonical_provenance.event_id, canonical_provenance.raw_event_id",
            (found[0]["session_id"], found[0]["cursor"], found[-1]["cursor"]),
        ).fetchall()
        raw_event_ids: dict[str, list[RawEventId]] = {}
        for entry in provenance:
            raw_event_ids.setdefault(entry["event_id"], []).append(
                RawEventId(entry["raw_event_id"])
            )
        return tuple(
            mapper.stored_canonical_event(
                rows.canonical_event(row),
                tuple(raw_event_ids.get(row["event_id"], ())),
                self.codec,
            )
            for row in found
        )
