# Copyright (c) 2026 Zhambyl Yermagambet
"""A turn end ends a compaction whose finish fact is missing.

A Codex 0.156 `compacted` record was refused once, so its session had five
compaction starts and no finish, and it showed "compacting" for days.
"""

from __future__ import annotations

import pytest

from tests import (
    canonical_sessiondata_actor_access as actor_access,
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain

TURN_ENDS = (
    session_domain.event_conversation.TurnFinished(None, session_domain.outcomes.Outcome.SUCCEEDED),
    session_domain.event_conversation.TurnAborted(None),
)


@pytest.mark.parametrize("turn_end", TURN_ENDS)
def test_a_turn_end_ends_an_open_compaction(turn_end: session_domain.event_base.EventPayload) -> None:
    """The actor is not compacting after its turn ends, even with no finish fact."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_telemetry.CompactionStarted(session_values.CONTEXT_USED_TOKENS),
        turn_end,
    )

    assert actor_access.lead_context(state).compacting is False


def test_other_activity_keeps_it_open() -> None:
    """Other activity does not end a compaction; only its finish or the turn end does."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_telemetry.CompactionStarted(session_values.CONTEXT_USED_TOKENS),
        session_domain.event_telemetry.ContextReported(
            session_values.CONTEXT_USED_TOKENS, session_values.CONTEXT_WINDOW_TOKENS, None,
        ),
    )

    assert actor_access.lead_context(state).compacting is True
