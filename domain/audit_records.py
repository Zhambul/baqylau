# Copyright (c) 2026 Zhambyl Yermagambet
"""The verdicts and bounded processing records one raw event audit reports."""

from dataclasses import dataclass
from enum import StrEnum

from domain.event_base import CanonicalEvent, EventPayload


class CanonicalStorageResult(StrEnum):
    """Show how storage handled a canonical event."""

    ACCEPTED = "accepted"
    DEDUPLICATED = "deduplicated"


class RecordedTranslationDecision(StrEnum):
    """Show how a translator handled one raw event."""

    TRANSLATED = "translated"
    IGNORED_UNKNOWN = "ignored_unknown"
    IGNORED_NONSEMANTIC = "ignored_nonsemantic"
    TRANSLATION_FAILED = "translation_failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class InterpretationAuditEvent:
    """Link one interpreted event to its storage result."""

    event: CanonicalEvent[EventPayload]
    accepted_at: float
    event_order: int
    storage_result: CanonicalStorageResult


@dataclass(frozen=True)
class InterpretationAuditStep:
    """Describe one recorded processing step without its complete bodies."""

    step_index: int
    stage: str
    owner: str | None
    outcome: str | None
    observed_byte_length: int | None
    observed_digest: str | None
    diagnostic_code: str | None
    reason: str | None
    operation_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class InterpretationAudit:
    """The verdict for one raw event and every canonical event it emitted."""

    translator_version: str
    decision: RecordedTranslationDecision
    reason: str | None
    completed_at: float
    events: tuple[InterpretationAuditEvent, ...]
    history_revision: str = "default"
    runtime_revision: str = ""
    format_version: int = 1
    steps: tuple[InterpretationAuditStep, ...] = ()
    steps_truncated: bool = False
