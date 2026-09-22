# Copyright (c) 2026 Zhambyl Yermagambet
"""The forensic join: one observation, its verdict, its steps, and the facts it produced.

This is the read the audit CLI makes. It used to be hand-written SQL in
an engine query module — a layer that owned none of the four tables it
joined — and it issued two queries per raw event plus two per canonical event.
A five-thousand-event session was twenty thousand round trips; it is five here.
Processing steps are bounded per journal and never carry their complete bodies.
Older forensic databases without the journal tables keep their original read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repository.contract.facts import RawEventAuditRepository
from repository.impl.sqlite import (
    raw_event_audit_builder as builder,
    raw_event_audit_queries as queries,
    raw_event_audit_reads as reads,
    raw_event_audit_sources as sources_module,
)
from repository.impl.sqlite.current_facts import current_tables

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping

    from domain.audit_records import InterpretationAuditStep
    from domain.ids import RawEventId, SessionId
    from harness.models.raw_events import RawEventAudit
    from repository.impl.sqlite.connection import SqliteDatabase
    from repository.impl.sqlite.raw_event_audit_rows import StepGroup


class SqliteRawEventAuditRepository(RawEventAuditRepository):
    """Represent sqlite raw event audit repository."""

    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        """Initialize the object."""
        self.sqlite_database = sqlite_database

    def audit(self, raw_event_id: RawEventId) -> RawEventAudit | None:
        """Return the audit.

        Returns:
            Audit.

        """
        with self.sqlite_database.read() as connection:
            return _read_audit(connection, raw_event_id)

    def audits_for_session(self, session_id: SessionId) -> tuple[RawEventAudit, ...]:
        """Return the audits for session.

        Returns:
            Audits for session.

        """
        with self.sqlite_database.read() as connection:
            return _read_session_audits(connection, session_id)


def _read_audit(connection: sqlite3.Connection, raw_event_id: RawEventId) -> RawEventAudit | None:
    """Read one complete bounded audit.

    Returns:
        The stored audit, or None for an unknown raw event.

    """
    tables = current_tables(connection)
    sources = sources_module.available_sources(connection)
    raw = connection.execute(
        queries.audit_sql(
            tables.interpretations, sources, queries.SINGLE_AUDIT_CONDITION,
            with_revisions=reads.with_revisions(connection, tables),
        ),
        (str(raw_event_id),),
    ).fetchone()
    if raw is None:
        return None
    canonical = reads.canonical_rows(connection, tables, raw_event_id)
    steps = reads.read_single_steps(connection, sources, raw_event_id, str(raw["history_revision"]))
    return builder.audit(raw, canonical, steps)


def _read_session_audits(connection: sqlite3.Connection, session_id: SessionId) -> tuple[RawEventAudit, ...]:
    """Read every bounded audit of one session.

    Returns:
        The stored audits in raw-event cursor order.

    """
    tables = current_tables(connection)
    sources = sources_module.available_sources(connection)
    raw_rows = connection.execute(
        queries.audit_sql(
            tables.interpretations, sources, queries.SESSION_AUDIT_CONDITION,
            with_revisions=reads.with_revisions(connection, tables),
        ),
        (str(session_id),),
    ).fetchall()
    canonical_rows = reads.session_canonical_rows(connection, tables, session_id)
    step_groups = reads.read_session_steps(connection, tables.interpretations, sources, session_id)
    return _session_audits(raw_rows, canonical_rows, step_groups)


class _StepCursor:
    """Walk the ordered step groups once as the raw events arrive."""

    def __init__(self) -> None:
        """Start before the first step group."""
        self.position = 0

    def take(self, raw_id: int, step_groups: tuple[StepGroup, ...]) -> tuple[InterpretationAuditStep, ...]:
        """Return this raw event's steps and advance past their group.

        Returns:
            The matched steps, or no steps for an unmatched raw event.

        """
        current = step_groups[self.position] if self.position < len(step_groups) else None
        if current is None or current.raw_id != raw_id:
            return ()
        self.position += 1
        return current.steps


def _session_audits(
    raw_rows: list[sqlite3.Row],
    canonical_rows: list[sqlite3.Row],
    step_groups: tuple[StepGroup, ...],
) -> tuple[RawEventAudit, ...]:
    """Build every audit of one session with its bounded steps.

    Returns:
        The audits in raw-event cursor order.

    """
    by_raw_event = _events_by_raw_event(canonical_rows)
    audits: list[RawEventAudit] = []
    cursor = _StepCursor()
    for raw in raw_rows:
        steps = cursor.take(int(raw["id"]), step_groups)
        _append_audit(audits, raw, by_raw_event, steps)
    return tuple(audits)


def _events_by_raw_event(canonical_rows: list[sqlite3.Row]) -> Mapping[str, list[sqlite3.Row]]:
    by_raw_event: dict[str, list[sqlite3.Row]] = {}
    for row in canonical_rows:
        by_raw_event.setdefault(row["raw_event_id"], []).append(row)
    return by_raw_event


def _append_audit(
    audits: list[RawEventAudit],
    raw: sqlite3.Row,
    by_raw_event: Mapping[str, list[sqlite3.Row]],
    steps: tuple[InterpretationAuditStep, ...],
) -> None:
    """Append one audit with the canonical events of its raw event."""
    events = by_raw_event.get(raw["raw_event_id"], [])
    audits.append(builder.audit(raw, events, steps))
