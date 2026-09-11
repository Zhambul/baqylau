# Copyright (c) 2026 Zhambyl Yermagambet
"""Test control effect queue reaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from dashboard.services.queued_prompts import QueuedPromptCanonicalEventReaction
from domain import (
    composer,
    content as domain_content,
    event_base,
    event_conversation,
    ids as domain_ids,
)
from tests.harness_names import CODEX_HARNESS

if TYPE_CHECKING:
    from repository.contract.workspace import SessionWorkspaceRepository

from tests import control_effect_stores as stores, control_effect_values as control_values


def test_message_queued_fact_updates_reload_safe() -> None:
    """Verify a message queued fact updates the reload safe mirror."""
    workspaces = stores.Workspaces()
    reaction = QueuedPromptCanonicalEventReaction(cast("SessionWorkspaceRepository", workspaces))

    reaction.react(
        event_base.CanonicalEvent(
            event_id=domain_ids.CanonicalEventId("message-queued"),
            session_id=control_values.TEST_SESSION_ID,
            actor_id=control_values.TEST_ACTOR_ID,
            turn_id=control_values.TEST_TURN_ID,
            parent_actor_id=None,
            harness=CODEX_HARNESS,
            occurred_at=1.0,
            terminal_window_id=None,
            harness_process_id=None,
            payload=event_conversation.MessageQueued(
                control_values.TEST_REQUEST_ID,
                domain_content.TextContent(control_values.NEXT_PROMPT),
            ),
        ),
    )

    assert workspaces.queued == [
        (
            control_values.TEST_SESSION_ID,
            composer.QueuedMessage(control_values.TEST_REQUEST_ID, control_values.NEXT_PROMPT),
            "harness",
        ),
    ]
