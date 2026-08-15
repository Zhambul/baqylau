"""Transactional storage for canonical interpretations of recorded raw evidence."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

from contracts.harness import RawEvent, TranslationError, TranslationResult
from domain.codec import CanonicalEventCodec
from domain.events import CanonicalEvent, EventPayload
from domain.ids import ActorId, CanonicalEventId, RawEventId, SessionId
from runtime.database import connect, initialize


class CanonicalEventStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredCanonicalEvent:
    cursor: int
    accepted_at: float
    event: CanonicalEvent[EventPayload]
    raw_event_ids: tuple[RawEventId, ...]


@dataclass(frozen=True)
class CanonicalEventPage:
    events: tuple[StoredCanonicalEvent, ...]
    cursor: int
    latest_cursor: int | None
    has_more: bool


class CanonicalEventStore:
    """Writes the three interpretation tables; serves every canonical read.

    Raw evidence arrives through `RawEventRecorder`, never here. The backlog IS
    the queue: `untranslated_raw_events` returns recorded evidence without a
    verdict, and `store_translation` writes the verdict, the canonical events,
    and their provenance in one transaction — so every raw event leaves the
    backlog exactly once, and a crash resumes precisely where it stopped.
    """

    def __init__(
        self,
        database_path: str,
        codec: CanonicalEventCodec | None = None,
        clock=time.time,
    ) -> None:
        self.database_path = initialize(database_path)
        self.codec = codec or CanonicalEventCodec()
        self.clock = clock

    # --- the backlog ---------------------------------------------------------

    def untranslated_raw_events(self, limit: int) -> tuple[RawEvent, ...]:
        """Recorded evidence without a verdict, for REGISTERED sessions only.

        Evidence may precede its session row (a hook can beat the wrapper's
        registration); such rows simply wait here, auditable, and interpret the
        moment registration lands.
        """
        if limit <= 0:
            raise ValueError("backlog limit must be positive")
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT raw_events.* FROM raw_events "
                "LEFT JOIN translation_records USING(raw_event_id) "
                "JOIN session_harness USING(session_id) "
                "WHERE translation_records.raw_event_id IS NULL "
                "ORDER BY raw_events.id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._raw_event(row) for row in rows)

    def unregistered_raw_events(self, limit: int) -> tuple[RawEvent, ...]:
        """Evidence whose session has no row yet, in arrival order.

        This is how sessions launched outside a wrapper become visible: the
        interpreter asks the owning harness to derive the session from this
        evidence and registers it.
        """
        if limit <= 0:
            raise ValueError("orphan limit must be positive")
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT raw_events.* FROM raw_events "
                "LEFT JOIN session_harness USING(session_id) "
                "WHERE session_harness.session_id IS NULL "
                "ORDER BY raw_events.id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._raw_event(row) for row in rows)

    def latest_raw_event(self, session_id: SessionId, source_type: str) -> RawEvent | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM raw_events WHERE session_id=? AND source_type=? "
                "ORDER BY id DESC LIMIT 1",
                (str(session_id), source_type),
            ).fetchone()
        return self._raw_event(row) if row is not None else None

    def store_translation(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation: TranslationResult,
    ) -> tuple[StoredCanonicalEvent, ...]:
        completed_at = self.clock()
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO translation_records("
                "raw_event_id, translator_version, decision, reason, completed_at"
                ") VALUES(?, ?, ?, ?, ?)",
                (
                    str(raw_event.raw_event_id),
                    translator_version,
                    translation.decision,
                    translation.reason,
                    completed_at,
                ),
            )
            accepted: list[StoredCanonicalEvent] = []
            for event_order, event in enumerate(translation.canonical_events):
                stored_event, storage_result = self._record_canonical_event(
                    connection,
                    raw_event,
                    event,
                    completed_at,
                )
                connection.execute(
                    "INSERT INTO canonical_provenance("
                    "event_id, raw_event_id, event_order, storage_result"
                    ") VALUES(?, ?, ?, ?)",
                    (
                        str(event.event_id),
                        str(raw_event.raw_event_id),
                        event_order,
                        storage_result,
                    ),
                )
                if storage_result == "accepted":
                    accepted.append(stored_event)
            return tuple(accepted)

    def store_translation_failure(
        self,
        raw_event: RawEvent,
        translator_version: str,
        error: TranslationError,
    ) -> None:
        reason = error.reason if error.context is None else f"{error.reason}: {error.context}"
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO translation_records("
                "raw_event_id, translator_version, decision, reason, completed_at"
                ") VALUES(?, ?, 'translation_failed', ?, ?)",
                (str(raw_event.raw_event_id), translator_version, reason, self.clock()),
            )

    # --- reads ----------------------------------------------------------------

    def session_ids(self) -> tuple[SessionId, ...]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT session_id FROM canonical_events "
                "WHERE event_type='session.started' "
                "GROUP BY session_id "
                "ORDER BY MAX(COALESCE(occurred_at, accepted_at)) DESC"
            ).fetchall()
        return tuple(SessionId(row["session_id"]) for row in rows)

    def latest_cursor(self) -> int | None:
        with connect(self.database_path) as connection:
            return self._latest_cursor(connection)

    def latest_session_cursor(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> int | None:
        with connect(self.database_path) as connection:
            if through_cursor is None:
                row = connection.execute(
                    "SELECT MAX(cursor) AS latest_cursor FROM canonical_events WHERE session_id=?",
                    (str(session_id),),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT MAX(cursor) AS latest_cursor FROM canonical_events "
                    "WHERE session_id=? AND cursor<=?",
                    (str(session_id), through_cursor),
                ).fetchone()
        return row["latest_cursor"]

    def event(self, event_id: CanonicalEventId) -> StoredCanonicalEvent | None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM canonical_events WHERE event_id=?",
                (str(event_id),),
            ).fetchone()
            return self._stored_event(connection, row) if row is not None else None

    def require_event(self, event_id: CanonicalEventId) -> StoredCanonicalEvent:
        stored_event = self.event(event_id)
        if stored_event is None:
            raise CanonicalEventStoreError(f"unknown canonical event: {event_id}")
        return stored_event

    def after(self, session_id: SessionId, cursor: int, limit: int) -> CanonicalEventPage:
        if limit <= 0:
            raise ValueError("event page limit must be positive")
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? AND cursor>? "
                "ORDER BY cursor LIMIT ?",
                (str(session_id), cursor, limit + 1),
            ).fetchall()
            latest_cursor = self._latest_cursor(connection)
            has_more = len(rows) > limit
            selected = rows[:limit]
            events = self._stored_events(connection, selected)
        page_cursor = events[-1].cursor if events else cursor
        return CanonicalEventPage(events, page_cursor, latest_cursor, has_more)

    def through(self, session_id: SessionId, cursor: int | None = None) -> CanonicalEventPage:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            if cursor is None:
                rows = connection.execute(
                    "SELECT * FROM canonical_events WHERE session_id=? ORDER BY cursor",
                    (str(session_id),),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM canonical_events WHERE session_id=? AND cursor<=? ORDER BY cursor",
                    (str(session_id), cursor),
                ).fetchall()
            latest_cursor = self._latest_cursor(connection)
            events = self._stored_events(connection, rows)
        page_cursor = events[-1].cursor if events else (cursor or 0)
        return CanonicalEventPage(events, page_cursor, latest_cursor, False)

    def tail(
        self,
        session_id: SessionId,
        cursor: int,
        limit: int,
    ) -> CanonicalEventPage:
        if limit <= 0:
            raise ValueError("event tail limit must be positive")
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? AND cursor<=? "
                "ORDER BY cursor DESC LIMIT ?",
                (str(session_id), cursor, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            selected = rows[:limit]
            selected.reverse()
            events = self._stored_events(connection, selected)
            latest_cursor = self._latest_cursor(connection)
        page_cursor = events[-1].cursor if events else cursor
        return CanonicalEventPage(events, page_cursor, latest_cursor, has_more)

    def events_of_types(
        self,
        session_id: SessionId,
        event_types: tuple[str, ...],
        cursor: int,
    ) -> tuple[StoredCanonicalEvent, ...]:
        if not event_types:
            return ()
        placeholders = ",".join("?" for _event_type in event_types)
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? "
                f"AND event_type IN ({placeholders}) AND cursor<=? ORDER BY cursor",
                (str(session_id), *event_types, cursor),
            ).fetchall()
            return self._stored_events(connection, rows)

    def between(
        self,
        session_id: SessionId,
        after_cursor: int,
        through_cursor: int,
    ) -> tuple[StoredCanonicalEvent, ...]:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? "
                "AND cursor>? AND cursor<=? ORDER BY cursor",
                (str(session_id), after_cursor, through_cursor),
            ).fetchall()
            return self._stored_events(connection, rows)

    # --- internals -------------------------------------------------------------

    @staticmethod
    def _raw_event(row: sqlite3.Row) -> RawEvent:
        return RawEvent(
            raw_event_id=RawEventId(row["raw_event_id"]),
            harness=row["harness"],
            source_type=row["source_type"],
            source_name=row["source_name"],
            source_position=row["source_position"],
            session_id=SessionId(row["session_id"]),
            actor_id=ActorId(row["actor_id"]),
            parent_actor_id=(
                ActorId(row["parent_actor_id"]) if row["parent_actor_id"] is not None else None
            ),
            observed_at=row["observed_at"],
            encoding=row["encoding"],
            payload=row["payload"],
            source_identity=row["source_identity"],
        )

    def _record_canonical_event(
        self,
        connection: sqlite3.Connection,
        raw_event: RawEvent,
        event: CanonicalEvent[EventPayload],
        accepted_at: float,
    ) -> tuple[StoredCanonicalEvent, str]:
        if event.session_id != raw_event.session_id:
            raise CanonicalEventStoreError("canonical event does not belong to its raw event session")
        if event.harness != raw_event.harness:
            raise CanonicalEventStoreError("canonical event harness does not match its raw evidence")
        if event.actor_id != raw_event.actor_id:
            raise CanonicalEventStoreError("canonical event actor does not match its raw evidence")
        if event.parent_actor_id != raw_event.parent_actor_id:
            raise CanonicalEventStoreError("canonical event parent actor does not match its raw evidence")
        if event.parent_actor_id == event.actor_id:
            raise CanonicalEventStoreError("an actor cannot be its own parent")
        encoded = self.codec.encode(event)
        document = json.loads(encoded)
        existing = connection.execute(
            "SELECT * FROM canonical_events WHERE event_id=?",
            (str(event.event_id),),
        ).fetchone()
        if existing is not None:
            # A canonical event is an IDEMPOTENT projection: the identity names the fact,
            # so re-observing it is a no-op that only adds provenance. Several independent
            # sources legitimately converge here and may render one fact differently; the
            # first writer stays authoritative. Nothing is lost by not comparing the
            # bodies -- the later rendering is fully recoverable from its own raw event,
            # which is stored verbatim and linked below.
            return self._stored_event(connection, existing), "deduplicated"
        cursor = connection.execute(
            "INSERT INTO canonical_events("
            "event_id, schema_version, event_type, session_id, actor_id, turn_id, parent_actor_id, "
            "harness, occurred_at, accepted_at, payload"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document["event_id"],
                document["schema_version"],
                document["event_type"],
                document["session_id"],
                document["actor_id"],
                document["turn_id"],
                document["parent_actor_id"],
                document["harness"],
                document["occurred_at"],
                accepted_at,
                json.dumps(document["payload"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            ),
        ).lastrowid
        return StoredCanonicalEvent(cursor, accepted_at, event, (raw_event.raw_event_id,)), "accepted"

    def _stored_event(self, connection: sqlite3.Connection, row: sqlite3.Row) -> StoredCanonicalEvent:
        raw_rows = connection.execute(
            "SELECT raw_event_id FROM canonical_provenance WHERE event_id=? ORDER BY raw_event_id",
            (row["event_id"],),
        ).fetchall()
        return StoredCanonicalEvent(
            cursor=row["cursor"],
            accepted_at=row["accepted_at"],
            event=self.codec.decode(self._encoded_row(row)),
            raw_event_ids=tuple(RawEventId(raw_row["raw_event_id"]) for raw_row in raw_rows),
        )

    def _stored_events(
        self,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> tuple[StoredCanonicalEvent, ...]:
        if not rows:
            return ()
        provenance_rows = connection.execute(
            "SELECT canonical_provenance.event_id, canonical_provenance.raw_event_id "
            "FROM canonical_provenance "
            "JOIN canonical_events ON canonical_events.event_id=canonical_provenance.event_id "
            "WHERE canonical_events.session_id=? "
            "AND canonical_events.cursor>=? AND canonical_events.cursor<=? "
            "ORDER BY canonical_provenance.event_id, canonical_provenance.raw_event_id",
            (rows[0]["session_id"], rows[0]["cursor"], rows[-1]["cursor"]),
        ).fetchall()
        raw_event_ids: dict[str, list[RawEventId]] = {}
        for provenance_row in provenance_rows:
            raw_event_ids.setdefault(provenance_row["event_id"], []).append(
                RawEventId(provenance_row["raw_event_id"])
            )
        return tuple(
            StoredCanonicalEvent(
                cursor=row["cursor"],
                accepted_at=row["accepted_at"],
                event=self.codec.decode(self._encoded_row(row)),
                raw_event_ids=tuple(raw_event_ids.get(row["event_id"], ())),
            )
            for row in rows
        )

    @staticmethod
    def _latest_cursor(connection: sqlite3.Connection) -> int | None:
        row = connection.execute("SELECT MAX(cursor) AS latest_cursor FROM canonical_events").fetchone()
        return row["latest_cursor"]

    @staticmethod
    def _encoded_row(row: sqlite3.Row) -> bytes:
        document = {
            "actor_id": row["actor_id"],
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "harness": row["harness"],
            "occurred_at": row["occurred_at"],
            "parent_actor_id": row["parent_actor_id"],
            "payload": json.loads(row["payload"]),
            "schema_version": row["schema_version"],
            "session_id": row["session_id"],
            "turn_id": row["turn_id"],
        }
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
