# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude clear command tests."""

from __future__ import annotations

from domain import (
    event_conversation,
    event_session,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_claude_clear_does_not_hold_next_prompt() -> None:
    """Verify claude clear does not hold the next prompt in its turn."""
    translator = ClaudeCanonicalTranslator()
    cleared = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SYSTEM,
                fixture.SUBTYPE: "local_command",
                fixture.UUID_FIELD: "clear",
                fixture.CONTENT_FIELD: (
                    "<command-name>/clear</command-name>"
                    "<command-message>clear</command-message>"
                    "<command-args></command-args>"
                ),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="slash-clear",
        ),
    )
    prompt = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.PROMPT_AFTER_CLEAR_ID,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "answer after clear"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.PROMPT_AFTER_CLEAR_ID,
        ),
    )

    assert cleared.canonical_events == ()
    assert payloads(prompt, event_conversation.TurnStarted)[0].turn_id == domain_ids.TurnId(
        fixture.PROMPT_AFTER_CLEAR_ID,
    )
    assert payloads(prompt, event_conversation.MessageCreated)[0].turn_id == domain_ids.TurnId(
        fixture.PROMPT_AFTER_CLEAR_ID,
    )


def test_claude_prompt_quoting_command_envelope() -> None:
    # the anchor: a message ABOUT a slash command has prose in front of the tag
    """Verify claude prompt quoting a command envelope stays a prompt."""
    for content in (
        "why is <command-name>/model</command-name> in my transcript?",
        "explain <local-command-stdout>Set model to Opus 5</local-command-stdout>",
    ):
        content_prefix = content[:6]
        translation = ClaudeCanonicalTranslator().translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.USER,
                    fixture.UUID_FIELD: "quote",
                    fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: content},
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id=f"quote-{content_prefix}",
            ),
        )
        message = payloads(translation, event_conversation.MessageCreated)[0].payload
        assert message.role == fixture.USER
        assert text_of(message.content) == content
        assert not payloads(translation, event_session.ModelChanged)
