# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical facts and their interpretation audits.

`record_translation` owns one complete core interpretation transaction. It
writes the verdict, facts, and source links together. Callers never hold a
connection. Current reads exclude unpublished histories and extension rows.
"""

from __future__ import annotations

from dataclasses import astuple
from typing import TYPE_CHECKING

from core.work_queue import WorkKind
from domain import event_session, ids, records
from repository.contract.facts import CanonicalEventRepository
from repository.impl.sqlite import current_facts, rows
from repository.mapper import facts as mapper

if TYPE_CHECKING:
    import sqlite3

    from domain.event_base import CanonicalEvent, EventPayload
    from harness.models.raw_events import (
        RawEvent,
        TranslationResult,
    )
    from repository.impl.sqlite.connection import SqliteDatabase

_INSERT_COLUMNS = (
    "event_id, schema_version, event_type, session_id, actor_id, turn_id, "
    "parent_actor_id, harness, occurred_at, terminal_window_id, "
    "harness_process_id, accepted_at, payload"
)


class SqliteCanonicalEventRepository(CanonicalEventRepository):
    """Represent sqlite canonical event repository."""

    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        """Initialize the object."""
        self.sqlite_database = sqlite_database

    # --- the one write ---------------------------------------------------------

    def record_translation(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation_result: TranslationResult,
        completed_at: float,
    ) -> records.TranslationOutcome:
        """Record translation.

        Returns:
            The translation outcome.

        """
        record = records.InterpretationRecord(
            raw_event_id=raw_event.raw_event_id,
            translator_version=translator_version,
            decision=translation_result.decision,
            reason=translation_result.reason,
            completed_at=completed_at,
        )
        work_kinds = [WorkKind.CANONICAL] if translation_result.canonical_events else []
        if any(
            isinstance(event.payload, (event_session.SessionStarted, event_session.SessionFinished))
            for event in translation_result.canonical_events
        ):
            work_kinds.append(WorkKind.SOURCES)
        with self.sqlite_database.write(*work_kinds, notify_readers=False) as connection:
            connection.execute(
                "INSERT INTO interpretations("
                "raw_event_id, translator_version, decision, reason, completed_at"
                ") VALUES(?, ?, ?, ?, ?)",
                mapper.interpretation_record_values(record),
            )
            connection.execute(
                "DELETE FROM pending_raw_events WHERE raw_event_id=?",
                (str(raw_event.raw_event_id),),
            )
            return _record_events(
                connection,
                raw_event,
                translation_result,
                completed_at,
            )

    # --- reads -----------------------------------------------------------------

    def find(self, event_id: ids.CanonicalEventId) -> CanonicalEvent[EventPayload] | None:
        """Return find.

        Returns:
            Find.

        """
        with self.sqlite_database.read() as connection:
            tables = current_facts.current_tables(connection)
            row = connection.execute(
                f"SELECT * FROM {tables.canonical} WHERE event_id=? AND session_id IS NOT NULL",  # noqa: S608 -- Fixed repository view.
                (str(event_id),),
            ).fetchone()
            if row is None:
                return None
            interpretation_events = connection.execute(
                f"SELECT raw_event_id FROM {tables.links} WHERE event_id=? ORDER BY raw_event_id",  # noqa: S608 -- Fixed repository view.
                (row["event_id"],),
            ).fetchall()
        return mapper.row_canonical_event(
            rows.canonical_event(row),
            tuple(ids.RawEventId(entry["raw_event_id"]) for entry in interpretation_events),
        )

    def session_ids(self) -> tuple[ids.SessionId, ...]:
        """Return the session ids.

        Returns:
            Session ids.

        """
        with self.sqlite_database.read() as connection:
            tables = current_facts.current_tables(connection)
            found = connection.execute(
                f"SELECT session_id FROM {tables.canonical} "  # noqa: S608 -- Fixed repository view.
                "WHERE event_type='session.started' AND session_id IS NOT NULL "
                "GROUP BY session_id "
                "ORDER BY MAX(COALESCE(occurred_at, accepted_at)) DESC",
            ).fetchall()
        return tuple(ids.SessionId(row["session_id"]) for row in found)


def _record_events(
    connection: sqlite3.Connection,
    raw_event: RawEvent,
    translation_result: TranslationResult,
    completed_at: float,
) -> records.TranslationOutcome:
    accepted: list[CanonicalEvent[EventPayload]] = []
    deduplicated: list[CanonicalEvent[EventPayload]] = []
    for event_order, event in enumerate(translation_result.canonical_events):
        storage_result = _append(connection, event, completed_at)
        connection.execute(
            "INSERT INTO interpretation_events(event_id, raw_event_id, event_order, storage_result) VALUES(?, ?, ?, ?)",
            mapper.interpretation_event_values(
                records.InterpretationEventRecord(
                    event.event_id,
                    raw_event.raw_event_id,
                    event_order,
                    storage_result,
                ),
            ),
        )
        if storage_result == records.CanonicalStorageResult.ACCEPTED:
            accepted.append(event)
        else:
            deduplicated.append(event)
    return records.TranslationOutcome(tuple(accepted), tuple(deduplicated))


def _append(
    connection: sqlite3.Connection,
    event: CanonicalEvent[EventPayload],
    accepted_at: float,
) -> records.CanonicalStorageResult:
    tables = current_facts.current_tables(connection)
    existing = connection.execute(
        f"SELECT session_id FROM {tables.canonical} WHERE event_id=?",  # noqa: S608 -- Fixed repository view.
        (str(event.event_id),),
    ).fetchone()
    if existing is not None:
        if existing["session_id"] is None:
            message = "core canonical identity conflicts with an extension fact"
            raise ValueError(message)
        # Keep the first accepted core body in the current history. A later
        # observation adds its source link, not another accepted body. The
        # mixed interpretation writer must also retain transformed proposals;
        # original raw bytes alone do not record a later transform result.
        return records.CanonicalStorageResult.DEDUPLICATED
    connection.execute(
        f"INSERT INTO canonical_events({_INSERT_COLUMNS}) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        astuple(mapper.canonical_event_insert_row(event, accepted_at)),
    )
    return records.CanonicalStorageResult.ACCEPTED
