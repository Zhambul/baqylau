# Copyright (c) 2026 Zhambyl Yermagambet
"""The core harness branch of the append-only raw store and its pending queue."""

from __future__ import annotations

from dataclasses import astuple
from typing import TYPE_CHECKING

from core.work_queue import WorkKind
from repository.contract.facts import RawEventRepository
from repository.errors import EventIdentityConflictError
from repository.impl.sqlite import connection as sqlite_connection, rows
from repository.mapper import facts as mapper

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence

    from domain.ids import RawEventId
    from harness.models.raw_events import (
        RawEvent,
    )

_INSERT_COLUMNS = (
    "raw_event_id, session_id, harness, source_type, source_identity, "
    "source_name, source_position, actor_id, parent_actor_id, "
    "observed_at, encoding, payload, payload_codec, terminal_window_id, "
    "harness_process_id, account_id, account_display_name"
)


def _record_one(connection: sqlite3.Connection, raw_event: RawEvent) -> None:
    existing = connection.execute(
        "SELECT * FROM raw_events WHERE raw_event_id=?",
        (str(raw_event.raw_event_id),),
    ).fetchone()
    if existing is not None:
        if existing["session_id"] is None:
            message = f"raw event identity reused: {raw_event.raw_event_id}"
            raise EventIdentityConflictError(message)
        stored_event = mapper.raw_event(rows.raw_event(existing))
        if mapper.raw_event_identity(stored_event) != mapper.raw_event_identity(raw_event):
            message = f"raw event identity reused: {raw_event.raw_event_id}"
            raise EventIdentityConflictError(message)
        return
    inserted = connection.execute(
        f"INSERT INTO raw_events({_INSERT_COLUMNS}) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        astuple(mapper.raw_event_insert_row(raw_event)),
    )
    connection.execute(
        "INSERT INTO pending_raw_events(raw_event_row_id, raw_event_id) VALUES(?, ?)",
        (inserted.lastrowid, str(raw_event.raw_event_id)),
    )


class SqliteRawEventRepository(RawEventRepository):
    """Represent sqlite raw event repository."""

    def __init__(self, sqlite_database: sqlite_connection.SqliteDatabase) -> None:
        """Initialize the object."""
        self.sqlite_database = sqlite_database

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        """Record raw events."""
        if not raw_events:
            return
        with self.sqlite_database.write(WorkKind.RAW, notify_readers=False) as connection:
            for raw_event in raw_events:
                _record_one(connection, raw_event)

    def find(self, raw_event_id: RawEventId) -> RawEvent | None:
        """Return find.

        Returns:
            Find.

        """
        with self.sqlite_database.read() as connection:
            row = connection.execute(
                "SELECT * FROM raw_events WHERE raw_event_id=? AND session_id IS NOT NULL",
                (str(raw_event_id),),
            ).fetchone()
        return None if row is None else mapper.raw_event(rows.raw_event(row))

    def unverdicted(self, limit: int) -> tuple[RawEvent, ...]:
        """Return the unverdicted.

        Returns:
            Unverdicted.

        Raises:
            ValueError: If an input value is not valid.

        """
        if limit <= 0:
            message = "backlog limit must be positive"
            raise ValueError(message)
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT raw_events.* FROM pending_raw_events "
                "JOIN raw_events ON raw_events.id = pending_raw_events.raw_event_row_id "
                "WHERE raw_events.session_id IS NOT NULL "
                "ORDER BY pending_raw_events.raw_event_row_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(mapper.raw_event(rows.raw_event(row)) for row in found)

    def latest_positions(self, source_identities: Sequence[str]) -> Mapping[str, str]:
        """Return the latest positions.

        Returns:
            Latest positions.

        """
        if not source_identities:
            return {}
        placeholders = ",".join("?" for _identity in source_identities)
        with self.sqlite_database.read() as connection:
            # MAX(id) picks the last recorded event per source, and the join
            # reads that row's position. One query for every source the
            # interpreter is about to poll, instead of one query each.
            found = connection.execute(
                "SELECT latest.source_identity, raw_events.source_position "  # noqa: S608 -- Only ? placeholders vary.
                "FROM (SELECT source_identity, MAX(id) AS id FROM raw_events "
                f"      WHERE session_id IS NOT NULL AND source_identity IN ({placeholders}) "
                "       GROUP BY source_identity) AS latest "
                "JOIN raw_events ON raw_events.id = latest.id",
                tuple(source_identities),
            ).fetchall()
        return {row["source_identity"]: row["source_position"] for row in found}
