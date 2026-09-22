# Copyright (c) 2026 Zhambyl Yermagambet
"""Map stored raw-event audits to command-line documents."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, RootModel

from domain.event_base import CanonicalEvent, EventPayload
from domain.ids import ActorId, HarnessName, RawEventId, SessionId

if TYPE_CHECKING:
    from harness.models.raw_events import (
        RawEventAudit,
    )

UNKNOWN_COMPLETION_TIME = float(0)


class CanonicalAuditEntry(BaseModel):
    """Describe one canonical event from a raw event interpretation."""

    model_config = ConfigDict(frozen=True)
    accepted_at: float
    event_order: int
    storage_result: str
    event: CanonicalEvent[EventPayload]


class AuditStepEntry(BaseModel):
    """Describe one recorded processing step without its complete bodies."""

    model_config = ConfigDict(frozen=True)
    step_index: int
    stage: str
    owner: str | None
    outcome: str | None
    observed_byte_length: int | None
    observed_digest: str | None
    diagnostic_code: str | None
    reason: str | None
    operation_kinds: tuple[str, ...]


class RawEventAuditDocument(BaseModel):
    """Describe one raw event and its complete interpretation."""

    model_config = ConfigDict(frozen=True)
    raw_event_id: RawEventId
    session_id: SessionId
    harness: HarnessName
    source_type: str
    source_name: str
    source_position: str
    actor_id: ActorId
    parent_actor_id: ActorId | None
    observed_at: float
    encoding: str
    payload_base64: str
    translator_version: str
    decision: str
    reason: str | None
    completed_at: float
    history_revision: str
    runtime_revision: str
    format_version: int
    canonical: tuple[CanonicalAuditEntry, ...]
    steps: tuple[AuditStepEntry, ...]
    steps_truncated: bool


class RawEventAuditDocuments(RootModel[tuple[RawEventAuditDocument, ...]]):
    """Hold raw-event audit documents for one session."""


def audit_document(raw_event_audit: RawEventAudit) -> RawEventAuditDocument:
    """Map one stored raw-event audit to a command-line document.

    Returns:
        The raw event audit document.

    """
    raw_event = raw_event_audit.raw_event
    interpretation = raw_event_audit.interpretation
    if interpretation is None:
        return _untranslated_document(raw_event_audit)
    return RawEventAuditDocument(
        raw_event_id=raw_event.raw_event_id,
        session_id=raw_event.session_id,
        harness=raw_event.harness,
        source_type=raw_event.source_type,
        source_name=raw_event.source_name,
        source_position=raw_event.source_position,
        actor_id=raw_event.actor_id,
        parent_actor_id=raw_event.parent_actor_id,
        observed_at=raw_event.observed_at,
        encoding=raw_event.encoding,
        payload_base64=base64.b64encode(raw_event.payload).decode("ascii"),
        translator_version=interpretation.translator_version,
        decision=interpretation.decision,
        reason=interpretation.reason,
        completed_at=interpretation.completed_at,
        history_revision=interpretation.history_revision,
        runtime_revision=interpretation.runtime_revision,
        format_version=interpretation.format_version,
        canonical=tuple(
            CanonicalAuditEntry(
                accepted_at=canonical.accepted_at,
                event_order=canonical.event_order,
                storage_result=canonical.storage_result,
                event=canonical.event,
            )
            for canonical in interpretation.events
        ),
        steps=tuple(
            AuditStepEntry(
                step_index=step.step_index,
                stage=step.stage,
                owner=step.owner,
                outcome=step.outcome,
                observed_byte_length=step.observed_byte_length,
                observed_digest=step.observed_digest,
                diagnostic_code=step.diagnostic_code,
                reason=step.reason,
                operation_kinds=step.operation_kinds,
            )
            for step in interpretation.steps
        ),
        steps_truncated=interpretation.steps_truncated,
    )


def session_audit_documents(
    raw_event_audits: tuple[RawEventAudit, ...],
) -> RawEventAuditDocuments:
    """Map all stored raw-event audits for one session.

    Returns:
        The raw event audit documents.

    """
    return RawEventAuditDocuments(
        tuple(audit_document(raw_event_audit) for raw_event_audit in raw_event_audits),
    )


def _untranslated_document(raw_event_audit: RawEventAudit) -> RawEventAuditDocument:
    raw_event = raw_event_audit.raw_event
    return RawEventAuditDocument(
        raw_event_id=raw_event.raw_event_id,
        session_id=raw_event.session_id,
        harness=raw_event.harness,
        source_type=raw_event.source_type,
        source_name=raw_event.source_name,
        source_position=raw_event.source_position,
        actor_id=raw_event.actor_id,
        parent_actor_id=raw_event.parent_actor_id,
        observed_at=raw_event.observed_at,
        encoding=raw_event.encoding,
        payload_base64=base64.b64encode(raw_event.payload).decode("ascii"),
        translator_version="",
        decision="untranslated",
        reason=None,
        completed_at=UNKNOWN_COMPLETION_TIME,
        history_revision="default",
        runtime_revision="",
        format_version=1,
        canonical=(),
        steps=(),
        steps_truncated=False,
    )
