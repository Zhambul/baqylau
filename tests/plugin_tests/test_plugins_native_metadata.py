# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness native metadata translation tests."""

from domain.event_conversation import MessageCreated
from domain.event_session import SessionTitleChanged
from domain.work_state import TitleOrigin
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event


def test_native_instruction_wrappers_are_canon() -> None:
    """Verify native instruction wrappers are canonical system messages."""
    codex = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.MESSAGE_FIELD,
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: "<environment_context>facts</environment_context>",
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="codex-system-message",
        ),
    )
    claude = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "claude-system-message",
                fixture.IS_META: True,
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "Continue from where you left off."},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="claude-system-message",
        ),
    )

    assert payloads(codex, MessageCreated)[0].payload.role == fixture.SYSTEM
    assert payloads(claude, MessageCreated)[0].payload.role == fixture.SYSTEM


def test_claude_title_records_preserve_native() -> None:
    """Verify claude title records preserve native title origin."""
    translator = ClaudeCanonicalTranslator()
    custom = translator.translate(
        raw_event(
            {fixture.TYPE_FIELD: "agent-name", "agentName": "Chosen name"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="custom-title",
        ),
    )
    automatic = translator.translate(
        raw_event(
            {fixture.TYPE_FIELD: "ai-title", "aiTitle": "Generated name"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="automatic-title",
        ),
    )

    assert custom.canonical_events[0].payload == SessionTitleChanged("Chosen name", TitleOrigin.CUSTOM)
    assert automatic.canonical_events[0].payload == SessionTitleChanged("Generated name", TitleOrigin.AUTOMATIC)
