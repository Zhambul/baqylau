# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata fixtures."""

from __future__ import annotations

from tests import (
    canonical_sessiondata_components as sessiondata_components,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain


def succeeded_turn() -> session_domain.event_conversation.TurnFinished:
    """Return a successful turn finish.

    Returns:
        A successful turn finish.

    """
    return session_domain.event_conversation.TurnFinished(None, session_domain.outcomes.Outcome.SUCCEEDED)


def required_data(
    read_model: sessiondata_components.repository.session_data.SqliteSessionDataRepository,
) -> session_domain.session_state.SessionData:
    """Return the stored session data that the loop must create.

    Returns:
        The stored session data that the loop must create.

    """
    session_record = read_model.read(session_values.SESSION)
    assert session_record is not None
    return session_record


def started() -> session_domain.event_session.SessionStarted:
    """Read the fixed session-start payload.

    Returns:
        The session-start payload used by the writer tests.

    """
    return session_values.A_START


def alive() -> tuple[session_domain.event_base.EventPayload, ...]:
    """Build the initial session and actor facts.

    Returns:
        The session-start and lead-actor-start payloads, in that order.

    """
    return (
        started(),
        session_domain.event_actor.ActorStarted(
            session_values.CLAUDE_ACTOR_NAME, session_domain.messaging.ActorRole.LEAD,
        ),
    )


def fold_after(
    *payloads: session_domain.event_base.EventPayload,
) -> sessiondata_components.engine.contract.AggregateState:
    """Apply the supplied facts to a started session.

    Returns:
        The aggregate state after the facts.

    """
    return folding.fold(*alive(), *payloads)


def status_after(
    *payloads: session_domain.event_base.EventPayload,
) -> session_domain.actor_state.ActorStatus | None:
    """Apply the supplied facts to a started session.

    Returns:
        The resulting lead actor status, which can be None.

    """
    actor = folding.lead_from(fold_after(*payloads))
    return actor.status


def session_account_changed_fixture() -> session_domain.event_session.SessionAccountChanged:
    """Build a fixed account-change payload.

    Returns:
        The account-change payload for the test account.

    """
    return session_domain.event_session.SessionAccountChanged(
        session_domain.references.AccountReference(session_domain.ids.AccountId("acc-1"), "zhambyl"),
    )
