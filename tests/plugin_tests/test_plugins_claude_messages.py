# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude message translation tests from native fixture shapes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.event_conversation import MessageCreated, ReasoningCreated
from domain.event_session import ModelChanged
from domain.event_telemetry import ContextReported, UsageReported
from domain.usage import TokenUsage
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from domain.messaging import MessagePhase


def _assistant_message_phases(
    stop_reason: str | None,
    blocks: list[JsonValue],
) -> list[MessagePhase | None]:
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.ASSISTANT_ONE,
                "apiBlockIndex": 0,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: blocks,
                    fixture.STOP_REASON_FIELD: stop_reason,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=f"assistant-{stop_reason}-{len(blocks)}",
        ),
    )
    return [payload.payload.phase for payload in payloads(translation, MessageCreated)]


def test_claude_assistant_preserves_reasoning() -> None:
    """Verify claude assistant preserves reasoning model and session usage."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.ASSISTANT_ONE,
                fixture.MESSAGE_FIELD: {
                    fixture.MODEL: fixture.CLAUDE_OPUS_FOUR_EIGHT,
                    fixture.CONTENT_FIELD: [
                        {fixture.TYPE_FIELD: "thinking", "thinking": "Inspect the failure"},
                        {fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "I found it"},
                    ],
                    "usage": {
                        fixture.INPUT_TOKENS_ID: 10,
                        fixture.OUTPUT_TOKENS_ID: 3,
                        "cache_read_input_tokens": 7,
                        "cache_creation_input_tokens": 6,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 4,
                            "ephemeral_1h_input_tokens": 2,
                        },
                    },
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.ASSISTANT,
        ),
    )

    reasoning = payloads(translation, ReasoningCreated)[0].payload
    model = payloads(translation, ModelChanged)[0].payload
    context = payloads(translation, ContextReported)[0].payload
    usage = payloads(translation, UsageReported)[0].payload
    _assert_assistant_reasoning_and_model(reasoning, model)
    _assert_assistant_context_and_usage(context, model, usage)


def _assert_assistant_reasoning_and_model(
    reasoning: ReasoningCreated,
    model: ModelChanged,
) -> None:
    """Verify the assistant reasoning and selected model."""
    assert text_of(reasoning.content) == "Inspect the failure"
    assert model.current.name == fixture.CLAUDE_OPUS_FOUR_EIGHT
    assert model.current.display_name == "opus-4.8"


def _assert_assistant_context_and_usage(
    context: ContextReported,
    model: ModelChanged,
    usage: UsageReported,
) -> None:
    """Verify the assistant context and usage facts."""
    assert context.used_tokens == fixture.CONTEXT_INPUT_TOKENS
    assert context.window_tokens == fixture.LARGE_CONTEXT_WINDOW_TOKENS
    assert context.model == model.current
    assert usage.tokens == TokenUsage(
        input_tokens=10,
        output_tokens=3,
        cache_read_tokens=7,
        cache_write_tokens=4,
        one_hour_cache_write_tokens=2,
    )
    assert usage.cost_in_usd is None


def test_claude_marks_where_model_stopped() -> None:
    """`stop_reason` is the only structural tell, and it belongs to the RESPONSE.

    So a response that broke off to call a tool ends no turn, and of a response
    that DID stop only its last text block does — the earlier blocks are prose the
    model wrote on the way there. Measured against claude-code 2.1.233.
    """
    one_block: list[JsonValue] = [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "Hi"}]
    two_blocks: list[JsonValue] = [
        {fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "Working on it"},
        {fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "Done"},
    ]

    assert _assistant_message_phases(fixture.END_TURN_ID, one_block) == [fixture.END_TURN_ID]
    assert _assistant_message_phases(fixture.TOOL_USE_ID, one_block) == ["intermediate"]
    assert _assistant_message_phases(fixture.END_TURN_ID, two_blocks) == ["intermediate", fixture.END_TURN_ID]
    assert _assistant_message_phases(None, one_block) == ["intermediate"]
