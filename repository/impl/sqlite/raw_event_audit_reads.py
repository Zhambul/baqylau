# Copyright (c) 2026 Zhambyl Yermagambet
"""Read bounded audit rows from one forensic database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from repository.impl.sqlite import (
    raw_event_audit_queries as queries,
    raw_event_audit_rows as audit_rows,
    raw_event_audit_sources as sources_module,
)

if TYPE_CHECKING:
    import sqlite3

    from domain.audit_records import InterpretationAuditStep
    from domain.ids import RawEventId, SessionId
    from repository.impl.sqlite.current_facts import CurrentFactTables


def read_single_steps(
    connection: sqlite3.Connection, sources: tuple[str, ...], raw_event_id: RawEventId, history_revision: str,
) -> tuple[InterpretationAuditStep, ...]:
    """Read the bounded steps of one raw event.

    Returns:
        The stored step metadata.

    """
    step_sql = queries.single_steps_sql(sources)
    if step_sql is None:
        return ()
    query_arguments = queries.single_step_arguments(sources, raw_event_id, history_revision)
    rows = connection.execute(step_sql, query_arguments).fetchall()
    return tuple(map(audit_rows.audit_step, rows))


def read_session_steps(
    connection: sqlite3.Connection, interpretations_table: str, sources: tuple[str, ...], session_id: SessionId,
) -> tuple[audit_rows.StepGroup, ...]:
    """Read the bounded steps of one session.

    Returns:
        One step group per raw event.

    """
    step_sql = queries.session_steps_sql(interpretations_table, sources)
    if step_sql is None:
        return ()
    query_arguments = queries.session_step_arguments(sources, session_id)
    rows = connection.execute(step_sql, query_arguments).fetchall()
    return audit_rows.session_step_groups(rows)


def canonical_rows(
    connection: sqlite3.Connection, current_fact_tables: CurrentFactTables, raw_event_id: RawEventId,
) -> list[sqlite3.Row]:
    """Read the canonical events of one raw event.

    Returns:
        The accepted event rows in interpretation order.

    """
    return connection.execute(
        "SELECT canonical_events.*, interpretation_events.event_order, "  # noqa: S608 -- Fixed repository views.
        "interpretation_events.storage_result "
        f"FROM {current_fact_tables.links} AS interpretation_events "
        f"JOIN {current_fact_tables.canonical} AS canonical_events USING(event_id) "
        "WHERE raw_event_id=? AND canonical_events.session_id IS NOT NULL "
        "ORDER BY interpretation_events.event_order",
        (str(raw_event_id),),
    ).fetchall()


def session_canonical_rows(
    connection: sqlite3.Connection, current_fact_tables: CurrentFactTables, session_id: SessionId,
) -> list[sqlite3.Row]:
    """Read the canonical events of one session.

    Returns:
        The accepted event rows in raw-event order.

    """
    return connection.execute(
        "SELECT interpretation_events.raw_event_id, canonical_events.*, "  # noqa: S608 -- Fixed repository views.
        "interpretation_events.event_order, interpretation_events.storage_result "
        f"FROM {current_fact_tables.links} AS interpretation_events "
        f"JOIN {current_fact_tables.canonical} AS canonical_events USING(event_id) "
        "JOIN raw_events ON raw_events.raw_event_id = interpretation_events.raw_event_id "
        "WHERE raw_events.session_id=? AND canonical_events.session_id IS NOT NULL "
        "ORDER BY raw_events.id, interpretation_events.event_order",
        (str(session_id),),
    ).fetchall()


def with_revisions(connection: sqlite3.Connection, current_fact_tables: CurrentFactTables) -> bool:
    """Check whether this database stores explicit history and runtime revisions.

    Returns:
        True when both revision columns are present.

    """
    return sources_module.has_columns(
        connection, current_fact_tables.interpretations, ("history_revision", "runtime_revision"),
    )
