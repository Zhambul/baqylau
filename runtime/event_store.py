"""Transactional storage for raw observations and canonical interpretations."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass

from contracts.harness import (
    IngestionResult,
    RawEvent,
    RecognizedSession,
    TranslationError,
    TranslationResult,
)
from domain.codec import CanonicalEventCodec, SCHEMA_VERSION
from domain.events import (
    ActorStarted,
    CanonicalEvent,
    EventPayload,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    SessionWorkingDirectoryChanged,
)
from domain.ids import ActorId, CanonicalEventId, RawEventId, SessionId
from runtime.database import connect


class EventStoreError(RuntimeError):
    pass


class EventIdentityConflict(EventStoreError):
    pass


class HarnessOwnershipConflict(EventStoreError):
    pass


@dataclass(frozen=True)
class StoredEvent:
    cursor: int
    accepted_at: float
    event: CanonicalEvent[EventPayload]
    raw_event_ids: tuple[RawEventId, ...]


@dataclass(frozen=True)
class EventRecordResult:
    accepted: tuple[StoredEvent, ...]
    duplicate_event_ids: tuple[CanonicalEventId, ...]
    latest_cursor: int | None


@dataclass(frozen=True)
class EventPage:
    events: tuple[StoredEvent, ...]
    cursor: int
    latest_cursor: int | None
    has_more: bool


SCHEMA = """
CREATE TABLE IF NOT EXISTS event_store_metadata(
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_harness(
    session_id TEXT PRIMARY KEY,
    lead_actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    working_directory TEXT,
    native_process_id INTEGER
);

CREATE TABLE IF NOT EXISTS raw_events(
    raw_event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_position TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    observed_at REAL NOT NULL,
    encoding TEXT NOT NULL,
    payload BLOB NOT NULL,
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS translation_records(
    raw_event_id TEXT PRIMARY KEY,
    translator_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(
        decision IN ('translated', 'ignored_unknown', 'ignored_nonsemantic', 'translation_failed')
    ),
    reason TEXT,
    completed_at REAL NOT NULL,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS canonical_events(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    turn_id TEXT,
    parent_actor_id TEXT,
    harness TEXT NOT NULL,
    occurred_at REAL,
    accepted_at REAL NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS index_canonical_session_type
    ON canonical_events(session_id, event_type, cursor);

CREATE INDEX IF NOT EXISTS index_canonical_session_actor
    ON canonical_events(session_id, actor_id, cursor);

CREATE INDEX IF NOT EXISTS index_canonical_session_cursor
    ON canonical_events(session_id, cursor);

CREATE INDEX IF NOT EXISTS index_raw_session
    ON raw_events(session_id, observed_at);

CREATE TABLE IF NOT EXISTS canonical_provenance(
    event_id TEXT NOT NULL,
    raw_event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    storage_result TEXT NOT NULL CHECK(storage_result IN ('accepted', 'deduplicated')),
    PRIMARY KEY(event_id, raw_event_id),
    UNIQUE(raw_event_id, event_order),
    FOREIGN KEY(event_id) REFERENCES canonical_events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS actor_harness(
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    PRIMARY KEY(session_id, actor_id),
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_checkpoints(
    source_identity TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    position TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS session_application_state(
    session_id TEXT PRIMARY KEY,
    composer_text TEXT NOT NULL DEFAULT '',
    composer_origin TEXT NOT NULL DEFAULT '',
    composer_sequence REAL NOT NULL DEFAULT 0,
    queued_messages TEXT NOT NULL DEFAULT '[]',
    queue_origin TEXT NOT NULL DEFAULT '',
    dialog_attention_id TEXT,
    dialog_answers TEXT NOT NULL DEFAULT '[]',
    dialog_origin TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);
"""


def _raw_identity(raw_event: RawEvent) -> tuple[object, ...]:
    return (
        str(raw_event.session_id),
        raw_event.harness,
        raw_event.source_type,
        raw_event.source_name,
        raw_event.source_position,
        str(raw_event.actor_id),
        str(raw_event.parent_actor_id) if raw_event.parent_actor_id is not None else None,
        raw_event.encoding,
        raw_event.payload,
    )


class EventStore:
    def __init__(
        self,
        database_path: str,
        codec: CanonicalEventCodec | None = None,
        clock=time.time,
    ) -> None:
        self.database_path = os.path.abspath(database_path)
        self.codec = codec or CanonicalEventCodec()
        self.clock = clock
        directory = os.path.dirname(self.database_path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with connect(self.database_path) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            row = connection.execute(
                "SELECT value FROM event_store_metadata WHERE name='schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO event_store_metadata(name, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif row["value"] != str(SCHEMA_VERSION):
                raise EventStoreError(f"unsupported event-store schema version: {row['value']}")
        os.chmod(self.database_path, 0o600)

    def register_session(self, harness: str, session: RecognizedSession) -> None:
        values = (
            str(session.lead_actor_id),
            harness,
            session.native_session_id,
            session.source_reference,
            session.working_directory,
            session.native_process_id,
        )
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lead_actor_id, harness, native_session_id, source_reference, working_directory, "
                "native_process_id "
                "FROM session_harness WHERE session_id=?",
                (str(session.session_id),),
            ).fetchone()
            if row is not None:
                existing_owner = tuple(row)[:3]
                if existing_owner != values[:3]:
                    raise HarnessOwnershipConflict(f"conflicting owner for session {session.session_id}")
                connection.execute(
                    "UPDATE session_harness SET source_reference=?, working_directory=?, "
                    "native_process_id=COALESCE(?, native_process_id) "
                    "WHERE session_id=?",
                    (
                        session.source_reference,
                        session.working_directory,
                        session.native_process_id,
                        str(session.session_id),
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO actor_harness(session_id, actor_id, harness) VALUES(?, ?, ?)",
                    (str(session.session_id), str(session.lead_actor_id), harness),
                )
                return
            connection.execute(
                "INSERT INTO session_harness("
                "session_id, lead_actor_id, harness, native_session_id, source_reference, working_directory, "
                "native_process_id) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (str(session.session_id), *values),
            )
            connection.execute(
                "INSERT INTO actor_harness(session_id, actor_id, harness) VALUES(?, ?, ?)",
                (str(session.session_id), str(session.lead_actor_id), harness),
            )

    def session_harness(self, session_id: SessionId) -> str | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT harness FROM session_harness WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        return row["harness"] if row is not None else None

    def session_ids(self) -> tuple[SessionId, ...]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT session_id FROM canonical_events "
                "WHERE event_type='session.started' "
                "GROUP BY session_id "
                "ORDER BY MAX(COALESCE(occurred_at, accepted_at)) DESC"
            ).fetchall()
        return tuple(SessionId(row["session_id"]) for row in rows)

    def recently_observed_session_ids(self, limit: int) -> tuple[SessionId, ...]:
        if limit <= 0:
            raise ValueError("session limit must be positive")
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT session.session_id "
                "FROM session_harness AS session "
                "LEFT JOIN raw_events AS raw ON raw.session_id=session.session_id "
                "GROUP BY session.session_id "
                "ORDER BY MAX(raw.observed_at) DESC, session.rowid DESC "
                "LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(SessionId(row["session_id"]) for row in rows)

    def session_is_finished(self, session_id: SessionId) -> bool:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM canonical_events "
                "WHERE session_id=? AND event_type='session.finished' LIMIT 1",
                (str(session_id),),
            ).fetchone()
        return row is not None

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

    def recognized_session(self, session_id: SessionId) -> RecognizedSession | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT lead_actor_id, native_session_id, source_reference, working_directory, "
                "native_process_id "
                "FROM session_harness WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        if row is None:
            return None
        return RecognizedSession(
            session_id=session_id,
            lead_actor_id=ActorId(row["lead_actor_id"]),
            native_session_id=row["native_session_id"],
            source_reference=row["source_reference"],
            working_directory=row["working_directory"],
            native_process_id=row["native_process_id"],
        )

    def actor_harness(self, session_id: SessionId, actor_id: ActorId) -> str | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT harness FROM actor_harness WHERE session_id=? AND actor_id=?",
                (str(session_id), str(actor_id)),
            ).fetchone()
        return row["harness"] if row is not None else None

    def delete_session(self, session_id: SessionId) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM session_harness WHERE session_id=?",
                (str(session_id),),
            )

    def event(self, event_id: CanonicalEventId) -> StoredEvent | None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM canonical_events WHERE event_id=?",
                (str(event_id),),
            ).fetchone()
            return self._stored_event(connection, row) if row is not None else None

    def require_event(self, event_id: CanonicalEventId) -> StoredEvent:
        stored_event = self.event(event_id)
        if stored_event is None:
            raise EventStoreError(f"unknown canonical event: {event_id}")
        return stored_event

    def record(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation: TranslationResult,
    ) -> EventRecordResult:
        completed_at = self.clock()
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_raw = connection.execute(
                "SELECT session_id, harness, source_type, source_name, source_position, "
                "actor_id, parent_actor_id, "
                "encoding, payload FROM raw_events WHERE raw_event_id=?",
                (str(raw_event.raw_event_id),),
            ).fetchone()
            if existing_raw is not None:
                if tuple(existing_raw) != _raw_identity(raw_event):
                    raise EventIdentityConflict(f"raw event identity reused: {raw_event.raw_event_id}")
                return self._verify_recorded_translation(
                    connection,
                    raw_event,
                    translator_version,
                    translation,
                )

            self._verify_registered_session(connection, raw_event)
            self._insert_raw(connection, raw_event)
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
            accepted: list[StoredEvent] = []
            duplicate_event_ids: list[CanonicalEventId] = []
            for event_order, event in enumerate(translation.events):
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
                else:
                    duplicate_event_ids.append(event.event_id)
            latest_cursor = self._latest_cursor(connection)
            connection.commit()
            return EventRecordResult(tuple(accepted), tuple(duplicate_event_ids), latest_cursor)

    def record_failure(
        self,
        raw_event: RawEvent,
        translator_version: str,
        error: TranslationError,
    ) -> IngestionResult:
        reason = error.reason if error.context is None else f"{error.reason}: {error.context}"
        completed_at = self.clock()
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_raw = connection.execute(
                "SELECT session_id, harness, source_type, source_name, source_position, "
                "actor_id, parent_actor_id, "
                "encoding, payload FROM raw_events WHERE raw_event_id=?",
                (str(raw_event.raw_event_id),),
            ).fetchone()
            if existing_raw is not None:
                if tuple(existing_raw) != _raw_identity(raw_event):
                    raise EventIdentityConflict(f"raw event identity reused: {raw_event.raw_event_id}")
                record = connection.execute(
                    "SELECT translator_version, decision, reason FROM translation_records WHERE raw_event_id=?",
                    (str(raw_event.raw_event_id),),
                ).fetchone()
                if record is None or tuple(record) != (translator_version, "translation_failed", reason):
                    raise EventIdentityConflict(f"translation changed for raw event {raw_event.raw_event_id}")
            else:
                self._verify_registered_session(connection, raw_event)
                self._insert_raw(connection, raw_event)
                connection.execute(
                    "INSERT INTO translation_records("
                    "raw_event_id, translator_version, decision, reason, completed_at"
                    ") VALUES(?, ?, 'translation_failed', ?, ?)",
                    (str(raw_event.raw_event_id), translator_version, reason, completed_at),
                )
            latest_cursor = self._latest_cursor(connection)
            connection.commit()
        return IngestionResult(
            raw_event_id=raw_event.raw_event_id,
            translation_decision="translation_failed",
            accepted_event_ids=(),
            deduplicated_event_ids=(),
            latest_cursor=latest_cursor,
        )

    def after(self, session_id: SessionId, cursor: int, limit: int) -> EventPage:
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
        return EventPage(events, page_cursor, latest_cursor, has_more)

    def through(self, session_id: SessionId, cursor: int | None = None) -> EventPage:
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
        return EventPage(events, page_cursor, latest_cursor, False)

    def tail(
        self,
        session_id: SessionId,
        cursor: int,
        limit: int,
    ) -> EventPage:
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
        return EventPage(events, page_cursor, latest_cursor, has_more)

    def events_of_types(
        self,
        session_id: SessionId,
        event_types: tuple[str, ...],
        cursor: int,
    ) -> tuple[StoredEvent, ...]:
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
    ) -> tuple[StoredEvent, ...]:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT * FROM canonical_events WHERE session_id=? "
                "AND cursor>? AND cursor<=? ORDER BY cursor",
                (str(session_id), after_cursor, through_cursor),
            ).fetchall()
            return self._stored_events(connection, rows)

    def _verify_registered_session(self, connection: sqlite3.Connection, raw_event: RawEvent) -> None:
        row = connection.execute(
            "SELECT lead_actor_id, harness FROM session_harness WHERE session_id=?",
            (str(raw_event.session_id),),
        ).fetchone()
        if row is None:
            raise EventStoreError(f"unregistered session: {raw_event.session_id}")
        if str(raw_event.actor_id) == row["lead_actor_id"] and raw_event.harness != row["harness"]:
            raise HarnessOwnershipConflict(f"conflicting owner for lead actor {raw_event.actor_id}")
        actor_owner = connection.execute(
            "SELECT harness FROM actor_harness WHERE session_id=? AND actor_id=?",
            (str(raw_event.session_id), str(raw_event.actor_id)),
        ).fetchone()
        if actor_owner is not None and actor_owner["harness"] != raw_event.harness:
            raise HarnessOwnershipConflict(f"conflicting owner for actor {raw_event.actor_id}")
        connection.execute(
            "INSERT OR IGNORE INTO actor_harness(session_id, actor_id, harness) VALUES(?, ?, ?)",
            (str(raw_event.session_id), str(raw_event.actor_id), raw_event.harness),
        )

    @staticmethod
    def _insert_raw(connection: sqlite3.Connection, raw_event: RawEvent) -> None:
        connection.execute(
            "INSERT INTO raw_events("
            "raw_event_id, session_id, harness, source_type, source_name, "
            "source_position, actor_id, parent_actor_id, observed_at, encoding, payload"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(raw_event.raw_event_id),
                str(raw_event.session_id),
                raw_event.harness,
                raw_event.source_type,
                raw_event.source_name,
                raw_event.source_position,
                str(raw_event.actor_id),
                str(raw_event.parent_actor_id) if raw_event.parent_actor_id is not None else None,
                raw_event.observed_at,
                raw_event.encoding,
                raw_event.payload,
            ),
        )

    def _record_canonical_event(
        self,
        connection: sqlite3.Connection,
        raw_event: RawEvent,
        event: CanonicalEvent[EventPayload],
        accepted_at: float,
    ) -> tuple[StoredEvent, str]:
        if event.session_id != raw_event.session_id:
            raise EventStoreError("canonical event does not belong to its raw event session")
        if event.harness != raw_event.harness:
            raise EventStoreError("canonical event harness does not match its raw evidence")
        if event.actor_id != raw_event.actor_id:
            raise EventStoreError("canonical event actor does not match its raw evidence")
        if event.parent_actor_id != raw_event.parent_actor_id:
            raise EventStoreError("canonical event parent actor does not match its raw evidence")
        session_owner = connection.execute(
            "SELECT lead_actor_id, harness FROM session_harness WHERE session_id=?",
            (str(event.session_id),),
        ).fetchone()
        if session_owner is None:
            raise EventStoreError(f"unregistered session: {event.session_id}")
        is_session_event = isinstance(
            event.payload,
            (
                SessionStarted,
                SessionTitleChanged,
                SessionWorkingDirectoryChanged,
                SessionAccountChanged,
                SessionFinished,
            ),
        )
        if is_session_event and str(event.actor_id) != session_owner["lead_actor_id"]:
            raise EventStoreError("session events must use the lead actor")
        if isinstance(event.payload, ActorStarted):
            is_lead_actor = str(event.actor_id) == session_owner["lead_actor_id"]
            if (event.payload.role == "lead") != is_lead_actor:
                raise EventStoreError("lead actor role does not match recognized session ownership")
        if event.parent_actor_id == event.actor_id:
            raise EventStoreError("an actor cannot be its own parent")
        actor_owner = connection.execute(
            "SELECT harness FROM actor_harness WHERE session_id=? AND actor_id=?",
            (str(event.session_id), str(event.actor_id)),
        ).fetchone()
        if actor_owner is not None and actor_owner["harness"] != event.harness:
            raise HarnessOwnershipConflict(f"conflicting owner for actor {event.actor_id}")
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
            # which is stored verbatim and linked below. Raising on a disagreement instead
            # used to abort the whole observation pass and killed the scheduler thread.
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
        connection.execute(
            "INSERT OR IGNORE INTO actor_harness(session_id, actor_id, harness) VALUES(?, ?, ?)",
            (str(event.session_id), str(event.actor_id), event.harness),
        )
        return StoredEvent(cursor, accepted_at, event, (raw_event.raw_event_id,)), "accepted"

    def _verify_recorded_translation(
        self,
        connection: sqlite3.Connection,
        raw_event: RawEvent,
        translator_version: str,
        translation: TranslationResult,
    ) -> EventRecordResult:
        record = connection.execute(
            "SELECT translator_version, decision, reason FROM translation_records WHERE raw_event_id=?",
            (str(raw_event.raw_event_id),),
        ).fetchone()
        expected_record = (translator_version, translation.decision, translation.reason)
        if record is None or tuple(record) != expected_record:
            raise EventIdentityConflict(f"translation changed for raw event {raw_event.raw_event_id}")
        provenance = connection.execute(
            "SELECT event_id, event_order, storage_result FROM canonical_provenance "
            "WHERE raw_event_id=? ORDER BY event_order",
            (str(raw_event.raw_event_id),),
        ).fetchall()
        if [row["event_id"] for row in provenance] != [str(event.event_id) for event in translation.events]:
            raise EventIdentityConflict(f"canonical output changed for raw event {raw_event.raw_event_id}")
        for event in translation.events:
            canonical_row = connection.execute(
                "SELECT * FROM canonical_events WHERE event_id=?",
                (str(event.event_id),),
            ).fetchone()
            if canonical_row is None or self._encoded_row(canonical_row) != self.codec.encode(event):
                raise EventIdentityConflict(f"canonical output changed for raw event {raw_event.raw_event_id}")
        return EventRecordResult(
            accepted=(),
            duplicate_event_ids=tuple(event.event_id for event in translation.events),
            latest_cursor=self._latest_cursor(connection),
        )

    def _stored_event(self, connection: sqlite3.Connection, row: sqlite3.Row) -> StoredEvent:
        raw_rows = connection.execute(
            "SELECT raw_event_id FROM canonical_provenance WHERE event_id=? ORDER BY raw_event_id",
            (row["event_id"],),
        ).fetchall()
        return StoredEvent(
            cursor=row["cursor"],
            accepted_at=row["accepted_at"],
            event=self.codec.decode(self._encoded_row(row)),
            raw_event_ids=tuple(RawEventId(raw_row["raw_event_id"]) for raw_row in raw_rows),
        )

    def _stored_events(
        self,
        connection: sqlite3.Connection,
        rows: list[sqlite3.Row],
    ) -> tuple[StoredEvent, ...]:
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
            StoredEvent(
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
