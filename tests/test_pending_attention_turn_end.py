# Copyright (c) 2026 Zhambyl Yermagambet
"""A turn end closes its questions, and only an aborted turn closes its plan."""

import pytest

from domain import content, entries, entry_attention, entry_conversation
from domain.entry_base import EntryBody, TurnState
from domain.ids import ActorId, AttentionId, CanonicalEventId, SessionId

LEAD = ActorId("session-one:lead")
QUESTION = AttentionId("question-one")
PLAN = AttentionId("plan-one")
CASES = ((TurnState.FINISHED, (PLAN,)), (TurnState.ABORTED, ()))


def _entry(entry_id: str, body: EntryBody) -> entries.SessionEntry:
    return entries.SessionEntry(
        CanonicalEventId(entry_id), SessionId("session-one"), LEAD, None, None, 0, None, body,
    )


@pytest.mark.parametrize(("state", "still_open"), CASES)
def test_finished_turn_keeps_its_plan(state: TurnState, still_open: tuple[AttentionId, ...]) -> None:
    """Codex finishes the turn that proposes a plan, and then waits for the decision."""
    feed = (
        _entry("question", entry_attention.QuestionAskedBody(QUESTION, ())),
        _entry("plan", entry_attention.PlanProposedBody(PLAN, content.TextContent("# Plan"))),
        _entry("turn-end", entry_conversation.TurnFinishedBody(state)),
    )
    pending = entries.pending_attention(feed)
    assert tuple(getattr(entry.body, "attention_id", None) for entry in pending) == still_open
