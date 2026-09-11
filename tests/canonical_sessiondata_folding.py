# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata."""

from __future__ import annotations

import typing

from tests import (
    canonical_sessiondata_components as sessiondata_components,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain
from tests.harness_names import CODEX_HARNESS


class CommittedArguments(typing.TypedDict, total=False):
    """Hold optional canonical event fixture fields."""

    actor_id: session_domain.ids.ActorId
    parent_actor_id: session_domain.ids.ActorId | None
    turn_id: session_domain.ids.TurnId | None
    occurred_at: float | None
    accepted_at: float
    cursor: int
    event_id: str | None


def committed(
    payload: session_domain.event_base.EventPayload,
    **arguments: typing.Unpack[CommittedArguments],
) -> session_domain.event_base.CanonicalEvent[session_domain.event_base.EventPayload]:
    """Wrap a payload in a committed test event.

    Returns:
        The canonical event with the supplied fields and fixture defaults.

    """
    cursor = arguments.get("cursor", 1)
    return session_domain.event_base.CanonicalEvent(
        event_id=session_domain.ids.CanonicalEventId(
            arguments.get("event_id") or f"event-{cursor}",
        ),
        session_id=session_values.SESSION,
        actor_id=arguments.get("actor_id", session_values.LEAD),
        turn_id=arguments.get("turn_id"),
        parent_actor_id=arguments.get("parent_actor_id"),
        harness=CODEX_HARNESS,
        occurred_at=arguments.get("occurred_at"),
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
        cursor=cursor,
        accepted_at=arguments.get("accepted_at", 100.0),
    )


def fold(
    *payloads: session_domain.event_base.EventPayload
    | session_domain.event_base.CanonicalEvent[session_domain.event_base.EventPayload],
) -> sessiondata_components.engine.contract.AggregateState:
    """Apply every writer to every fact in order, without a database.

    Returns:
        The aggregate state after all facts have been applied.

    """
    state = sessiondata_components.engine.contract.AggregateState()
    for cursor, payload in enumerate(payloads, start=1):
        event = (
            payload
            if isinstance(payload, session_domain.event_base.CanonicalEvent)
            else committed(payload, cursor=cursor)
        )
        for writer in session_values.WRITERS:
            state = writer.write(event, state)
    return state


def session_from(
    state: sessiondata_components.engine.contract.AggregateState,
) -> session_domain.session_state.SessionFacts:
    """Return the session facts that a started session must have.

    Returns:
        The session facts that a started session must have.

    """
    assert state.session is not None
    return state.session


def session_after(
    *payloads: session_domain.event_base.EventPayload
    | session_domain.event_base.CanonicalEvent[session_domain.event_base.EventPayload],
) -> session_domain.session_state.SessionFacts:
    """Apply facts and read the required session state.

    Returns:
        The session facts produced by the writers.

    """
    return session_from(fold(*payloads))


def actor_from(
    state: sessiondata_components.engine.contract.AggregateState,
    actor_id: session_domain.ids.ActorId = session_values.LEAD,
) -> session_domain.actor_state.ActorFacts:
    """Return the actor facts that an actor start must create.

    Returns:
        The actor facts that an actor start must create.

    """
    actor = state.actor(actor_id)
    assert actor is not None
    return actor


def lead_from(
    state: sessiondata_components.engine.contract.AggregateState,
) -> session_domain.actor_state.ActorFacts:
    """Return the lead actor facts.

    Returns:
        The lead actor facts.

    """
    return actor_from(state, session_values.LEAD)
