# Copyright (c) 2026 Zhambyl Yermagambet
"""The verdicts a store hands out, and the operations they compose into.

The event itself, stored or not, is `domain/events.py`'s `CanonicalEvent` — one
class end to end, with `cursor`/`accepted_at`/`raw_event_ids` filled in once it
is written. What is left here is what happened AROUND an event: the verdict
reached about one raw observation, and the outcome of writing a batch of them.
The audit read models live in `domain/audit_records.py`.
"""

from dataclasses import dataclass

from domain.audit_records import (
    CanonicalStorageResult as CanonicalStorageResult,
    InterpretationAudit as InterpretationAudit,
    InterpretationAuditEvent as InterpretationAuditEvent,
    InterpretationAuditStep as InterpretationAuditStep,
    RecordedTranslationDecision as RecordedTranslationDecision,
)
from domain.event_base import CanonicalEvent, EventPayload
from domain.ids import CanonicalEventId, RawEventId


@dataclass(frozen=True)
class InterpretationRecord:
    """The verdict reached about one raw observation."""

    raw_event_id: RawEventId
    translator_version: str
    decision: RecordedTranslationDecision
    reason: str | None
    completed_at: float


@dataclass(frozen=True)
class InterpretationEventRecord:
    """One canonical event emitted by an interpretation, and what storing it did."""

    event_id: CanonicalEventId
    raw_event_id: RawEventId
    event_order: int
    storage_result: CanonicalStorageResult


@dataclass(frozen=True)
class TranslationOutcome:
    """What `record_translation` did, in one value.

    `accepted` is the NEWLY committed events only — a re-observation converging
    on an existing fact adds an interpretation event and is not returned, so reactions run
    once per fact.
    """

    accepted: tuple[CanonicalEvent[EventPayload], ...]
    deduplicated: tuple[CanonicalEvent[EventPayload], ...]
