# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the bounded audit queries and their exact arguments."""

from domain.ids import RawEventId, SessionId
from repository.impl.sqlite.raw_event_audit_sources import StepSource, step_sources

MAX_AUDIT_STEPS = 1000
type QueryArgument = str | int
SINGLE_AUDIT_CONDITION = "WHERE raw_events.raw_event_id=? AND raw_events.session_id IS NOT NULL"
SESSION_AUDIT_CONDITION = "WHERE raw_events.session_id=? ORDER BY raw_events.id"
STEP_COLUMNS = (
    "journal_step.step_index AS step_index, journal_step.stage AS stage, "
    "json_extract(journal_step.step, '$.request.context.extension_id') AS owner, "
    "json_extract(journal_step.step, '$.extension_id') AS limit_owner, "
    "json_extract(journal_step.step, '$.outcome.kind') AS outcome, "
    "json_extract(journal_step.step, '$.outcome.observed_byte_length') AS observed_byte_length, "
    "json_extract(journal_step.step, '$.outcome.observed_digest') AS observed_digest, "
    "json_extract(journal_step.step, '$.outcome.diagnostic.code') AS diagnostic_code, "
    "json_extract(journal_step.step, '$.reason') AS reason, "
    "(SELECT group_concat(json_extract(operation.value, '$.kind'), ',') "
    "FROM json_each(journal_step.step, '$.outcome.reply.operations') AS operation) AS operation_kinds"
)


def audit_sql(interpretations_table: str, sources: tuple[str, ...], condition: str, *, with_revisions: bool) -> str:
    """Build the raw-event audit query for the present schema.

    Returns:
        The complete query text.

    """
    with_journal = "interpretation_journals" in sources
    format_column = (
        "json_extract(journal.proposal, '$.format_version') AS format_version "
        if with_journal else "NULL AS format_version "
    )
    journal_join = (
        "LEFT JOIN interpretation_journals AS journal "
        "ON journal.raw_event_id = interpretations.raw_event_id "
        "AND journal.history_revision = interpretations.history_revision "
        if with_journal else ""
    )
    revision_columns = (
        "interpretations.history_revision, interpretations.runtime_revision, "
        if with_revisions else "'default' AS history_revision, '' AS runtime_revision, "
    )
    return "".join((
        "SELECT raw_events.*, interpretations.translator_version, interpretations.decision, ",
        f"interpretations.reason, interpretations.completed_at, {revision_columns}{format_column}",
        f"FROM raw_events LEFT JOIN {interpretations_table} AS interpretations USING(raw_event_id) ",
        f"{journal_join}{condition}",
    ))


def single_steps_sql(sources: tuple[str, ...]) -> str | None:
    """Build the bounded step query for one raw event.

    Returns:
        The query text, or None when this database has no step source.

    """
    parts = tuple(_single_step_part(step_source) for step_source in step_sources(sources))
    if not parts:
        return None
    return "".join((" UNION ALL ".join(parts), " ORDER BY step_index LIMIT ?"))


def session_steps_sql(interpretations_table: str, sources: tuple[str, ...]) -> str | None:
    """Build the bounded step query for one session.

    Returns:
        The query text, or None when this database has no step source.

    """
    parts = tuple(_session_step_part(interpretations_table, step_source) for step_source in step_sources(sources))
    if not parts:
        return None
    return "".join((
        "SELECT raw_id, step_index, stage, owner, limit_owner, outcome, observed_byte_length, ",
        "observed_digest, diagnostic_code, reason, operation_kinds FROM (",
        " UNION ALL ".join(parts),
        ") WHERE position <= ? ORDER BY raw_id, step_index",
    ))


def single_step_arguments(
    sources: tuple[str, ...], raw_event_id: RawEventId, history_revision: str,
) -> tuple[QueryArgument, ...]:
    """Build the exact arguments for the single-event step query.

    Returns:
        The identity, revision, and bound arguments.

    """
    arguments: list[QueryArgument] = []
    for _ in step_sources(sources):
        arguments.extend((str(raw_event_id), history_revision))
    arguments.append(MAX_AUDIT_STEPS + 1)
    return tuple(arguments)


def session_step_arguments(sources: tuple[str, ...], session_id: SessionId) -> tuple[QueryArgument, ...]:
    """Build the exact arguments for the session step query.

    Returns:
        The session identity and bound arguments.

    """
    arguments: list[QueryArgument] = []
    arguments.extend(str(session_id) for _ in step_sources(sources))
    arguments.append(MAX_AUDIT_STEPS + 1)
    return tuple(arguments)


def _single_step_part(step_source: StepSource) -> str:
    return "".join((
        f"SELECT {STEP_COLUMNS} FROM {step_source.name} AS journal_step ",  # noqa: S608 -- Fixed source names and columns.
        "JOIN interpretation_journals AS journal USING(history_revision, raw_event_id) ",
        f"WHERE journal.raw_event_id=? AND journal.history_revision=? AND journal.codec_version={step_source.codec}",
    ))


def _session_step_part(interpretations_table: str, step_source: StepSource) -> str:
    return "".join((
        f"SELECT raw_events.id AS raw_id, {STEP_COLUMNS}, ",
        "ROW_NUMBER() OVER (",
        "PARTITION BY journal_step.raw_event_id ORDER BY journal_step.step_index",
        ") AS position ",
        f"FROM {step_source.name} AS journal_step ",
        "JOIN interpretation_journals AS journal USING(history_revision, raw_event_id) ",
        f"JOIN {interpretations_table} AS live ",
        "ON live.raw_event_id = journal_step.raw_event_id ",
        "AND live.history_revision = journal.history_revision ",
        "JOIN raw_events ON raw_events.raw_event_id = journal_step.raw_event_id ",
        f"WHERE raw_events.session_id=? AND journal.codec_version={step_source.codec}",
    ))
