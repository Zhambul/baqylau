"""The one state a tab shows, folded from the facts in order.

Last fact wins: this is a replay, not a rule engine, so the ORDER of the
branches below is the whole semantics. Two callers, two starting points — the
full history starts from nothing, a bounded tail starts from `idle`, because a
window that opens mid-session has no `session.started` to learn from.
"""

from __future__ import annotations

from domain.events import (
    AttentionRequested,
    AttentionResolved,
    CompactionStarted,
    MessageCreated,
    OperationFinished,
    OperationStarted,
    ReasoningCreated,
    SessionFinished,
    SessionStarted,
    TurnAborted,
    TurnFinished,
    TurnStarted,
)
from domain.ids import ActorId, AttentionId, OperationId
from engine.projections.models import TabState
from engine.store.canonical import StoredCanonicalEvent


def tab_state(
    stored_events: tuple[StoredCanonicalEvent, ...],
    initial_state: TabState | None,
) -> TabState | None:
    state = initial_state
    background_operations: set[OperationId] = set()
    pending_attention: set[tuple[ActorId, AttentionId]] = set()
    for stored in stored_events:
        event = stored.event
        payload = event.payload
        if isinstance(payload, SessionStarted):
            state = "idle"
        elif isinstance(payload, SessionFinished):
            state = None
        elif isinstance(payload, TurnStarted):
            state = "thinking"
        elif (
            isinstance(payload, MessageCreated)
            and payload.role == "user"
            and payload.phase == "prompt"
        ):
            state = "thinking"
        elif isinstance(payload, ReasoningCreated):
            state = "working"
        elif isinstance(payload, OperationStarted):
            if payload.execution in ("background", "monitor"):
                background_operations.add(payload.operation_id)
            if payload.category == "attention":
                state = "awaiting_attention"
            elif payload.category in ("shell", "task") or payload.execution in (
                "background",
                "monitor",
            ):
                state = "executing"
            else:
                state = "working"
        elif isinstance(payload, OperationFinished):
            background_operations.discard(payload.operation_id)
            state = "awaiting_attention" if pending_attention else "working"
        elif isinstance(payload, AttentionRequested):
            pending_attention.add((event.actor_id, payload.attention_id))
            state = "awaiting_attention"
        elif isinstance(payload, AttentionResolved):
            pending_attention.discard((event.actor_id, payload.attention_id))
            state = "working"
        elif isinstance(payload, CompactionStarted):
            state = "working"
        elif isinstance(payload, (TurnFinished, TurnAborted)):
            state = (
                "awaiting_background"
                if background_operations
                else "awaiting_response"
            )
    return state
