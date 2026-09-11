# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness message translation tests."""

from __future__ import annotations

from dataclasses import replace

from domain import (
    event_conversation as conversation_events,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.conversation_support import (
    CLAUDE_MESSAGE_ID,
)
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_claude_prompt_and_codex_prompt_share() -> None:
    """Verify claude prompt and codex prompt share the message model."""
    claude = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: CLAUDE_MESSAGE_ID,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "fix it"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="claude-prompt",
        ),
    )
    codex = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "user_message",
                    fixture.MESSAGE_FIELD: "fix it",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-prompt",
        ),
    )
    claude_message = payloads(claude, conversation_events.MessageCreated)[0].payload
    codex_message = payloads(codex, conversation_events.MessageCreated)[0].payload
    assert claude_message.role == codex_message.role == fixture.USER
    assert claude_message.phase == codex_message.phase == fixture.PROMPT_KIND
    # Claude Code announces no turn of its own, so the prompt opens one and the
    # message rides it; codex names its own turns and needs no such help.
    assert payloads(claude, conversation_events.TurnStarted)[0].payload.prompt_message_id == CLAUDE_MESSAGE_ID
    assert [event.turn_id for event in claude.canonical_events] == [CLAUDE_MESSAGE_ID, CLAUDE_MESSAGE_ID]


def test_codex_user_messages_in_one_turn_keep() -> None:
    """Verify codex user messages in one turn keep separate identities."""
    translator = CodexCanonicalTranslator()
    first = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ID_FIELD: "native-message-one",
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: fixture.TEST},
                    ],
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-prompt-with-turn",
        ),
    )
    second = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ID_FIELD: "native-message-two",
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: "next"}],
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-second-prompt-with-turn",
        ),
    )

    prompts = [
        payloads(first, conversation_events.MessageCreated)[0],
        payloads(second, conversation_events.MessageCreated)[0],
    ]
    assert [prompt.turn_id for prompt in prompts] == [fixture.TURN_ONE_ID, fixture.TURN_ONE_ID]
    assert [prompt.payload.message_id for prompt in prompts] == [
        "native-message-one",
        "native-message-two",
    ]


def test_claude_child_prompt_is_authored() -> None:
    """Verify claude child prompt is authored by the parent agent."""
    child_prompt = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.CHILD_PROMPT_ID,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "inspect it"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.CHILD_PROMPT_ID,
            source_position=fixture.ONE_TEXT,
        ),
        actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    messages = payloads(ClaudeCanonicalTranslator().translate(child_prompt), conversation_events.MessageCreated)

    assert messages[0].payload.role == "parent"


def test_claude_child_final_answer_is_addressed() -> None:
    """Verify claude child final answer is addressed to its parent agent."""
    child_answer = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: "child-answer",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "finished"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="child-answer",
        ),
        actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    message = payloads(
        ClaudeCanonicalTranslator().translate(child_answer),
        conversation_events.MessageCreated,
    )[0]

    assert message.payload.role == fixture.ASSISTANT
    assert message.payload.phase == fixture.END_TURN_ID
    assert message.payload.recipient_actor_id == domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID)


def test_claude_child_message_maps_team_lead() -> None:
    """Verify claude child message maps team lead alias to its parent actor."""
    child_message = replace(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.MESSAGE_ONE_ID,
                fixture.TOOL_NAME_FIELD: "SendMessage",
                fixture.TOOL_INPUT_FIELD: {
                    "to": "team-lead",
                    fixture.MESSAGE_FIELD: "CHILD_TO_LEAD_529",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="child-message",
        ),
        actor_id=domain_ids.ActorId(fixture.CHILD_ONE_ID),
        parent_actor_id=domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
    )

    message = payloads(
        ClaudeCanonicalTranslator().translate(child_message),
        conversation_events.MessageCreated,
    )[0]

    assert message.actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)
    assert message.payload.recipient_actor_id == domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID)
    assert text_of(message.payload.content) == "CHILD_TO_LEAD_529"
