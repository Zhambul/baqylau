"""What a stored canonical fact looks like once it is read back.

The event itself is `domain/events.py`'s business. These are the shapes the
STORE hands out around it: the cursor and acceptance time an event acquired by
being written, the page a range read returns, the verdict recorded against one
observation, and the link between the two.

They used to live inside the SQLite store class, which meant every consumer of
a stored event imported the storage module to name its type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from domain.events import CanonicalEvent, EventPayload
from domain.ids import CanonicalEventId, RawEventId

CanonicalStorageResult: TypeAlias = Literal["accepted", "deduplicated"]
RecordedTranslationDecision: TypeAlias = Literal[
    "translated", "ignored_unknown", "ignored_nonsemantic", "translation_failed"
]


@dataclass(frozen=True)
class StoredCanonicalEvent:
    cursor: int
    accepted_at: float
    event: CanonicalEvent[EventPayload]
    raw_event_ids: tuple[RawEventId, ...]


@dataclass(frozen=True)
class CommittedEvent:
    """One accepted fact, as the reaction loop reads it.

    The fact, when we recorded it, and its place in the one order everything
    after it depends on. Deliberately NOT a `StoredCanonicalEvent`: that one
    also carries the raw evidence its fact was derived from, which the audit
    needs and a reaction never does — and reading it would mean a second query
    per page, across sessions, for provenance nobody looks at.
    """

    cursor: int
    accepted_at: float
    event: CanonicalEvent[EventPayload]

    @property
    def happened_at(self) -> float:
        """When it happened, or failing that when we heard about it.

        `occurred_at` is nullable BY DESIGN — it is what the source said, and
        sources that carry no clock leave it empty. Every fold that measures or
        orders needs one number, and this is that number."""
        return self.event.occurred_at if self.event.occurred_at is not None else self.accepted_at


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


@dataclass(frozen=True)
class InterpretationAuditEvent:
    event: CanonicalEvent[EventPayload]
    accepted_at: float
    event_order: int
    storage_result: CanonicalStorageResult


@dataclass(frozen=True)
class InterpretationAudit:
    """The verdict for one raw event and every canonical event it emitted."""

    translator_version: str
    decision: RecordedTranslationDecision
    reason: str | None
    completed_at: float
    events: tuple[InterpretationAuditEvent, ...]
