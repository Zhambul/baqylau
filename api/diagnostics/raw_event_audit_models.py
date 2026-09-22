# Copyright (c) 2026 Zhambyl Yermagambet
"""Bounded public audit of one raw event."""

from pydantic import BaseModel

from domain.records import InterpretationAuditStep
from harness.models.raw_events import RawEventAudit

UNTRANSLATED_DECISION = "untranslated"
DEFAULT_HISTORY_REVISION = "default"
FIRST_FORMAT_VERSION = 1
UNKNOWN_COMPLETION_TIME = float(0)


class RawEventAuditStepResponse(BaseModel):
    """Describe one recorded processing step without its complete bodies."""

    step_index: int
    stage: str
    owner: str | None
    outcome: str | None
    observed_byte_length: int | None
    observed_digest: str | None
    diagnostic_code: str | None
    reason: str | None
    operation_kinds: tuple[str, ...]


class RawEventAuditResponse(BaseModel):
    """Describe one raw event and its bounded interpretation metadata."""

    raw_event_id: str
    session_id: str
    harness: str
    source_type: str
    source_name: str
    source_position: str
    observed_at: float
    encoding: str
    payload_byte_length: int
    translator_version: str = ""
    decision: str = UNTRANSLATED_DECISION
    reason: str | None = None
    completed_at: float = UNKNOWN_COMPLETION_TIME
    history_revision: str = DEFAULT_HISTORY_REVISION
    runtime_revision: str = ""
    format_version: int = FIRST_FORMAT_VERSION
    canonical_event_count: int = 0
    steps: tuple[RawEventAuditStepResponse, ...] = ()
    steps_truncated: bool = False


def raw_event_audit_response(raw_event_audit: RawEventAudit) -> RawEventAuditResponse:
    """Map one stored raw-event audit to a bounded public response.

    Returns:
        The bounded raw-event audit response.

    """
    raw_event = raw_event_audit.raw_event
    interpretation = raw_event_audit.interpretation
    if interpretation is None:
        return _observed_response(raw_event_audit)
    return RawEventAuditResponse(
        raw_event_id=raw_event.raw_event_id,
        session_id=raw_event.session_id,
        harness=raw_event.harness,
        source_type=raw_event.source_type,
        source_name=raw_event.source_name,
        source_position=raw_event.source_position,
        observed_at=raw_event.observed_at,
        encoding=raw_event.encoding,
        payload_byte_length=len(raw_event.payload),
        translator_version=interpretation.translator_version,
        decision=interpretation.decision,
        reason=interpretation.reason,
        completed_at=interpretation.completed_at,
        history_revision=interpretation.history_revision,
        runtime_revision=interpretation.runtime_revision,
        format_version=interpretation.format_version,
        canonical_event_count=len(interpretation.events),
        steps=tuple(_step_response(step) for step in interpretation.steps),
        steps_truncated=interpretation.steps_truncated,
    )


def _observed_response(raw_event_audit: RawEventAudit) -> RawEventAuditResponse:
    raw_event = raw_event_audit.raw_event
    return RawEventAuditResponse(
        raw_event_id=raw_event.raw_event_id,
        session_id=raw_event.session_id,
        harness=raw_event.harness,
        source_type=raw_event.source_type,
        source_name=raw_event.source_name,
        source_position=raw_event.source_position,
        observed_at=raw_event.observed_at,
        encoding=raw_event.encoding,
        payload_byte_length=len(raw_event.payload),
    )


def _step_response(interpretation_audit_step: InterpretationAuditStep) -> RawEventAuditStepResponse:
    return RawEventAuditStepResponse(
        step_index=interpretation_audit_step.step_index,
        stage=interpretation_audit_step.stage,
        owner=interpretation_audit_step.owner,
        outcome=interpretation_audit_step.outcome,
        observed_byte_length=interpretation_audit_step.observed_byte_length,
        observed_digest=interpretation_audit_step.observed_digest,
        diagnostic_code=interpretation_audit_step.diagnostic_code,
        reason=interpretation_audit_step.reason,
        operation_kinds=interpretation_audit_step.operation_kinds,
    )
