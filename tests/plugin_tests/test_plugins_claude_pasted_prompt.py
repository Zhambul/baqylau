# Copyright (c) 2026 Zhambyl Yermagambet
"""A pasted Claude prompt is recorded as the text that was pasted."""

from domain import event_conversation
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of

PASTED = "Ask which colour to use.\nOffer Blue and Green."


def test_pasted_prompt_keeps_only_its_text() -> None:
    """Claude Code's own paste tag is not part of the prompt."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "pasted-prompt",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: f'<pasted_content id="482f">\n{PASTED}\n</pasted_content id="482f">',
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="pasted-prompt",
        ),
    )
    messages = [created.payload for created in payloads(translation, event_conversation.MessageCreated)]
    assert [text_of(message.content) for message in messages] == [PASTED]
