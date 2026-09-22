# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete raw-event audits from stored rows."""

import sqlite3

from domain.audit_records import (
    CanonicalStorageResult,
    InterpretationAudit,
    InterpretationAuditEvent,
    InterpretationAuditStep,
    RecordedTranslationDecision,
)
from harness.models.raw_events import RawEventAudit
from repository.impl.sqlite import raw_event_audit_queries as queries, rows
from repository.mapper import facts as mapper


def audit(
    raw: sqlite3.Row, canonical: list[sqlite3.Row], steps: tuple[InterpretationAuditStep, ...],
) -> RawEventAudit:
    """Build one raw-event audit from its stored rows.

    Returns:
        The complete bounded audit.

    """
    raw_row = rows.raw_event(raw)
    return RawEventAudit(
        raw_event=mapper.raw_event(raw_row),
        interpretation=(
            None
            if raw["decision"] is None
            else _interpretation(raw, canonical, steps)
        ),
    )


def _interpretation(
    raw: sqlite3.Row, canonical: list[sqlite3.Row], steps: tuple[InterpretationAuditStep, ...],
) -> InterpretationAudit:
    return InterpretationAudit(
        translator_version=raw["translator_version"],
        decision=_decision(raw["decision"]),
        reason=raw["reason"],
        completed_at=raw["completed_at"],
        events=tuple(map(_audit_event, canonical)),
        history_revision=str(raw["history_revision"]),
        runtime_revision=str(raw["runtime_revision"] or ""),
        format_version=int(raw["format_version"] or 1),
        steps=steps[:queries.MAX_AUDIT_STEPS],
        steps_truncated=len(steps) > queries.MAX_AUDIT_STEPS,
    )


def _audit_event(row: sqlite3.Row) -> InterpretationAuditEvent:
    return InterpretationAuditEvent(
        event=mapper.row_canonical_event(rows.canonical_event(row)),
        accepted_at=row["accepted_at"],
        event_order=row["event_order"],
        storage_result=_storage_result(row["storage_result"]),
    )


def _storage_result(stored_result: str) -> CanonicalStorageResult:
    result: CanonicalStorageResult = stored_result  # type: ignore[assignment]
    return result


def _decision(stored_decision: str) -> RecordedTranslationDecision:
    decision: RecordedTranslationDecision = stored_decision  # type: ignore[assignment]
    return decision
