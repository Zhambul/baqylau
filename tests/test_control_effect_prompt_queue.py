# Copyright (c) 2026 Zhambyl Yermagambet
"""Test control effect prompt queue."""

from __future__ import annotations

from dashboard.services.queued_prompts import QueuedPromptCanonicalEventReaction
from domain import (
    content as domain_content,
    event_base,
    event_conversation,
    ids as domain_ids,
    messaging,
    outcomes,
)
from tests import control_effect_stores as stores, control_effect_values as control_values
from tests.harness_names import CODEX_HARNESS

QUEUED_PROMPT_COUNT = 2


def test_each_native_prompt_consumes_only_one() -> None:
    """Verify each native prompt consumes only one equal queued send."""
    workspaces = stores.DurableQueue()
    reaction = QueuedPromptCanonicalEventReaction(workspaces)

    for ordinal in (1, 2):
        reaction.react(
            event_base.CanonicalEvent(
                event_id=domain_ids.CanonicalEventId(f"prompt-{ordinal}"),
                session_id=control_values.TEST_SESSION_ID,
                actor_id=control_values.TEST_ACTOR_ID,
                turn_id=domain_ids.TurnId(f"turn-{ordinal}"),
                parent_actor_id=None,
                harness=CODEX_HARNESS,
                occurred_at=float(ordinal),
                terminal_window_id=None,
                harness_process_id=None,
                payload=event_conversation.MessageCreated(
                    domain_ids.MessageId(f"message-{ordinal}"),
                    messaging.MessageRole.USER,
                    domain_content.TextContent("same prompt"),
                    messaging.MessagePhase.PROMPT,
                    None,
                ),
            ),
        )

    assert workspaces.removed == [control_values.TEST_REQUEST_ID_TEXT, "request-two"]
    assert not workspaces.messages


def test_turn_finish_does_not_submit_queued() -> None:
    """Verify a turn finish does not submit a queued prompt."""
    workspaces = stores.DurableQueue()
    reaction = QueuedPromptCanonicalEventReaction(workspaces)
    reaction.react(
        event_base.CanonicalEvent(
            event_id=domain_ids.CanonicalEventId("active-turn-finished"),
            session_id=control_values.TEST_SESSION_ID,
            actor_id=control_values.TEST_ACTOR_ID,
            turn_id=control_values.TEST_TURN_ID,
            parent_actor_id=None,
            harness=CODEX_HARNESS,
            occurred_at=control_values.SHELL_ENTRY_TIME,
            terminal_window_id=None,
            harness_process_id=None,
            payload=event_conversation.TurnFinished(None, outcomes.Outcome.SUCCEEDED),
        ),
    )

    assert not workspaces.removed
    assert len(workspaces.messages) == QUEUED_PROMPT_COUNT
