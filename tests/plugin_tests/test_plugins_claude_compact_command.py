# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude compact command tests."""

from __future__ import annotations

import pytest

from domain import event_conversation
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


@pytest.mark.parametrize("command", ["/compact", "/compact keep the database schema"])
def test_bare_compact_is_not_prompt(command: str) -> None:
    """Verify a bare compact command emits no prompt or turn."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "compact-command",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: command},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=f"slash-{command}",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC


@pytest.mark.parametrize("text", ["/model high", "/compactness"])
def test_other_bare_slash_is_prompt(text: str) -> None:
    """Verify other bare slash text remains an ordinary prompt."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "other-slash-text",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: text},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=f"prompt-{text}",
        ),
    )

    messages = payloads(translation, event_conversation.MessageCreated)
    assert len(messages) == 1
    assert text_of(messages[0].payload.content) == text


def test_wrapped_compact_is_not_prompt() -> None:
    """Verify a wrapped compact command emits no prompt or turn."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "wrapped-compact-command",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "<command-name>/compact</command-name>"
                        "<command-args>keep the database schema</command-args>"
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="slash-wrapped-compact",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC
