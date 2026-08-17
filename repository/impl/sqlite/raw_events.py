"""The `raw_events` table over SQLite: append-only, and the backlog."""

from __future__ import annotations

from typing import Mapping, Sequence

from domain.ids import RawEventId
from harness.models import RawEvent
from repository.contract.facts import RawEventRepository
from repository.errors import EventIdentityConflict
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import facts as mapper

_IDENTITY_COLUMNS = (
    "session_id, harness, source_type, source_name, source_position, "
    "actor_id, parent_actor_id, encoding, payload"
)
_INSERT_COLUMNS = (
    "raw_event_id, session_id, harness, source_type, source_identity, "
    "source_name, source_position, actor_id, parent_actor_id, "
    "observed_at, encoding, payload, terminal_window_id, "
    "harness_process_id, account_id, account_display_name"
)


class SqliteRawEventRepository(RawEventRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        if not raw_events:
            return
        with self.database.write() as connection:
            for raw_event in raw_events:
                existing = connection.execute(
                    f"SELECT {_IDENTITY_COLUMNS} FROM raw_events WHERE raw_event_id=?",
                    (str(raw_event.raw_event_id),),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != mapper.raw_identity(raw_event):
                        raise EventIdentityConflict(
                            f"raw event identity reused: {raw_event.raw_event_id}"
                        )
                    continue
                connection.execute(
                    f"INSERT INTO raw_events({_INSERT_COLUMNS}) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    mapper.raw_event_values(raw_event),
                )

    def find(self, raw_event_id: RawEventId) -> RawEvent | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM raw_events WHERE raw_event_id=?", (str(raw_event_id),)
            ).fetchone()
        return mapper.raw_event(rows.raw_event(row)) if row is not None else None

    def unverdicted(self, limit: int) -> tuple[RawEvent, ...]:
        if limit <= 0:
            raise ValueError("backlog limit must be positive")
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT raw_events.* FROM raw_events "
                "LEFT JOIN translation_records USING(raw_event_id) "
                "WHERE translation_records.raw_event_id IS NULL "
                "ORDER BY raw_events.id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(mapper.raw_event(rows.raw_event(row)) for row in found)

    def latest_positions(self, source_identities: Sequence[str]) -> Mapping[str, str]:
        if not source_identities:
            return {}
        placeholders = ",".join("?" for _identity in source_identities)
        with self.database.read() as connection:
            # MAX(id) picks the last recorded event per source, and the join
            # reads that row's position. One query for every source the
            # interpreter is about to poll, instead of one query each.
            found = connection.execute(
                "SELECT latest.source_identity, raw_events.source_position "
                "FROM (SELECT source_identity, MAX(id) AS id FROM raw_events "
                f"      WHERE source_identity IN ({placeholders}) "
                "       GROUP BY source_identity) AS latest "
                "JOIN raw_events ON raw_events.id = latest.id",
                tuple(source_identities),
            ).fetchall()
        return {row["source_identity"]: row["source_position"] for row in found}
