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
from domain.ids import CanonicalEventId, RawEventId, SessionId

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
class CanonicalEventPage:
    events: tuple[StoredCanonicalEvent, ...]
    cursor: int
    latest_cursor: int | None
    has_more: bool


@dataclass(frozen=True)
class TranslationRecord:
    """The verdict reached about one raw observation."""

    raw_event_id: RawEventId
    translator_version: str
    decision: RecordedTranslationDecision
    reason: str | None
    completed_at: float


@dataclass(frozen=True)
class ProvenanceEntry:
    """One (fact, observation) link, and what storing it did."""

    event_id: CanonicalEventId
    raw_event_id: RawEventId
    event_order: int
    storage_result: CanonicalStorageResult


@dataclass(frozen=True)
class TranslationOutcome:
    """What `record_translation` did, in one value.

    `accepted` is the NEWLY committed events only — a re-observation converging
    on an existing fact adds provenance and is not returned, so reactions run
    once per fact.
    """

    accepted: tuple[CanonicalEvent[EventPayload], ...]
    deduplicated: tuple[CanonicalEvent[EventPayload], ...]


@dataclass(frozen=True)
class CanonicalEvidence:
    event: CanonicalEvent[EventPayload]
    accepted_at: float
    event_order: int
    storage_result: CanonicalStorageResult


@dataclass(frozen=True)
class TranslationEvidence:
    """One observation, its verdict, and the facts it produced — the forensic view."""

    raw_event_id: RawEventId
    session_id: SessionId
    harness: str
    source_type: str
    source_name: str
    source_position: str
    actor_id: str
    parent_actor_id: str | None
    observed_at: float
    encoding: str
    payload: bytes
    terminal_window_id: str | None
    harness_process_id: int | None
    account_id: str | None
    account_display_name: str | None
    translator_version: str
    decision: str
    reason: str | None
    completed_at: float
    canonical: tuple[CanonicalEvidence, ...]
