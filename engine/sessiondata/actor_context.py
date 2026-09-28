# Copyright (c) 2026 Zhambyl Yermagambet
"""Actor context."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, override

from domain import (
    actor_state,
    event_conversation,
    event_telemetry,
)
from engine.sessiondata import contract

if TYPE_CHECKING:
    from domain import event_base


class ContextWriter(contract.SessionDataWriter):
    """How full the window is, and whether it is being emptied.

    A compaction ends with its finish fact. The end of the actor's turn also
    ends it, because a turn cannot end while its context is emptied. So a finish
    record that a source lost or refused cannot keep the actor compacting.
    """

    @override
    def write(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        aggregate_state: contract.AggregateState,
    ) -> contract.AggregateState:
        """Write actor context state.

        Returns:
            The aggregate state.

        """
        actor = aggregate_state.actor(canonical_event.actor_id)
        context = None if actor is None else _context(actor.context, canonical_event.payload)
        if actor is None or context is None:
            return aggregate_state
        return aggregate_state.with_actor(replace(actor, context=context))


def _context(context: actor_state.ActorContext, payload: event_base.EventPayload) -> actor_state.ActorContext | None:
    if isinstance(payload, event_telemetry.ContextReported):
        return actor_state.ActorContext(
            used_tokens=payload.used_tokens, window_tokens=payload.window_tokens, compacting=context.compacting,
        )
    if isinstance(payload, (event_conversation.TurnFinished, event_conversation.TurnAborted)):
        return replace(context, compacting=False)
    if isinstance(payload, event_telemetry.CompactionStarted):
        return replace(context, compacting=True)
    if isinstance(payload, event_telemetry.CompactionFinished):
        used_tokens = context.used_tokens if payload.after_tokens is None else payload.after_tokens
        return replace(context, compacting=False, used_tokens=used_tokens)
    return None
