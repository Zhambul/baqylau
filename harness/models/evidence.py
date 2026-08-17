"""Raw evidence and what translating it produces.

The floor of the harness contract: one observation as recorded bytes, the
decision a translator reached about it, and the two constructors that keep
event identity and envelope stamping in one place.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Literal, TypeAlias

from domain.events import (
    CanonicalEvent,
    EventPayload,
    OperationOutputLocated,
)
from domain.ids import (
    ActorId,
    RawEventId,
    SessionId,
    TurnId,
    stable_event_id,
)

TranslationDecision: TypeAlias = Literal["translated", "ignored_unknown", "ignored_nonsemantic"]
RecordedTranslationDecision: TypeAlias = TranslationDecision | Literal["translation_failed"]


@dataclass(frozen=True)
class RawEvent:
    raw_event_id: RawEventId
    harness: str
    source_type: str
    source_name: str
    source_position: str
    session_id: SessionId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    observed_at: float
    encoding: str
    payload: bytes
    # Which observer produced this. It is the resume key: the recorder stores it,
    # and a pulled source is resumed from the `source_position` of the LAST
    # recorded raw event carrying its identity. Pushed observers (hooks) have no
    # resume and may leave it at their source_type.
    source_identity: str = ""
    # Set only on hook evidence, None everywhere else. Flat and typed: a hook
    # delivery is the one observation made from INSIDE the session's terminal
    # window and process tree, so what it saw around itself rides its row.
    terminal_window_id: str | None = None
    harness_process_id: int | None = None
    account_id: str | None = None
    account_display_name: str | None = None


@dataclass(frozen=True)
class TranslationResult:
    canonical_events: tuple[CanonicalEvent[EventPayload], ...]
    decision: RecordedTranslationDecision
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision == "translated" and not self.canonical_events:
            raise ValueError("translated observations must produce at least one canonical event")
        if self.decision != "translated" and self.canonical_events:
            raise ValueError("ignored observations cannot produce canonical events")


def canonical_event(
    raw_event: RawEvent,
    subject_type: str,
    subject_id: str,
    phase: str,
    payload: EventPayload,
    *,
    turn_id: TurnId | None = None,
    occurred_at: float | None = None,
) -> CanonicalEvent[EventPayload]:
    """One fact from one observation: the identity converges across sources and
    the envelope carries where the observation was made from."""
    return CanonicalEvent(
        event_id=stable_event_id(
            harness=raw_event.harness,
            session_id=raw_event.session_id,
            actor_id=raw_event.actor_id,
            subject_type=subject_type,
            subject_id=subject_id,
            phase=phase,
        ),
        session_id=raw_event.session_id,
        actor_id=raw_event.actor_id,
        turn_id=turn_id,
        parent_actor_id=raw_event.parent_actor_id,
        harness=raw_event.harness,
        occurred_at=occurred_at,
        terminal_window_id=raw_event.terminal_window_id,
        harness_process_id=raw_event.harness_process_id,
        payload=payload,
    )


class TranslationError(ValueError):
    def __init__(self, reason: str, *, context: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.context = context


@dataclass(frozen=True)
class RawEventSourceContext:
    session_id: SessionId
    lead_actor_id: ActorId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    source_reference: str


# --- Operation output directives ----------------------------------------------
#
# A hook that makes a command's output observable cannot follow the file itself —
# it must exit immediately. So the gateway records an output-location directive:
# a raw event carrying the typed `OperationOutputLocated` payload. The core
# translator turns it into the fact, the reaction starts the following, and the
# collect phase reads the file's chunks as their own evidence.

OUTPUT_LOCATION_SOURCE_TYPE = "output_location"
LIVENESS_SOURCE_TYPE = "liveness"
INTERRUPT_SOURCE_TYPE = "interrupt"


def output_location_raw_event(
    context: RawEventSourceContext,
    harness: str,
    located: OperationOutputLocated,
    actor_id: ActorId | None = None,
    parent_actor_id: ActorId | None = None,
) -> RawEvent:
    document = asdict(located)
    document["operation_id"] = str(located.operation_id)
    return RawEvent(
        raw_event_id=RawEventId(
            f"{harness}:output_location:{context.session_id}:{located.operation_id}"
        ),
        harness=harness,
        source_type=OUTPUT_LOCATION_SOURCE_TYPE,
        source_name=located.source_path,
        source_position="located",
        session_id=context.session_id,
        actor_id=actor_id or context.actor_id,
        parent_actor_id=parent_actor_id if actor_id else context.parent_actor_id,
        observed_at=time.time(),
        encoding="json",
        payload=json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        # NOT the chunk source's identity: the chunk reader resumes from the last
        # raw event under its own identity, and a directive there would
        # masquerade as a read position.
        source_identity=(
            f"{harness}:output_location:{context.session_id}:{located.operation_id}:directive"
        ),
    )
