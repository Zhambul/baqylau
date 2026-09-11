# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude blocking feedback tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.event_conversation import MessageCreated, TurnFinished, TurnStarted
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models.raw_events import (
    TranslationResult,
)
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue

if TYPE_CHECKING:
    from domain.ids import (
        TurnId,
    )

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


@dataclass(frozen=True)
class _BlockingFeedbackSequence:
    prompt: TranslationResult
    stopped: TranslationResult
    feedback: TranslationResult
    continued: TranslationResult
    final_stop: TranslationResult


def _blocking_feedback_sequence() -> _BlockingFeedbackSequence:
    translator = ClaudeCanonicalTranslator()
    return _BlockingFeedbackSequence(
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.USER,
                    fixture.UUID_FIELD: fixture.FIRST_PROMPT_ID,
                    fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "do the work"},
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id=fixture.FIRST_PROMPT_ID,
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
                    fixture.HOOK_EVENT_ID_FIELD: fixture.FIRST_STOP_ID,
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id=fixture.FIRST_STOP_ID,
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.USER,
                    fixture.UUID_FIELD: "stop-feedback",
                    fixture.IS_META: True,
                    fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "Stop hook feedback:\nContinue the work."},
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id="stop-feedback",
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                    fixture.TOOL_USE_ID_FIELD: "continued-command",
                    fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                    fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: fixture.PRINT_DIRECTORY_COMMAND},
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id="continued-command",
            ),
        ),
        translator.translate(
            raw_event(
                {fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK, fixture.HOOK_EVENT_ID_FIELD: "final-stop"},
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id="final-stop",
            ),
        ),
    )


def _assert_blocking_feedback_sequence(sequence: _BlockingFeedbackSequence) -> None:
    first_turn = payloads(sequence.prompt, TurnStarted)[0].turn_id
    resumed_turn = payloads(sequence.feedback, TurnStarted)[0].turn_id
    _assert_blocking_feedback_start(resumed_turn, first_turn, sequence.feedback, sequence.continued)
    _assert_blocking_feedback_stops(first_turn, resumed_turn, sequence.stopped, sequence.final_stop)


def _assert_blocking_feedback_start(
    resumed_turn: TurnId | None,
    first_turn: TurnId | None,
    feedback: TranslationResult,
    continued: TranslationResult,
) -> None:
    """Verify the synthetic feedback continuation turn."""
    feedback_message = payloads(feedback, MessageCreated)[0]
    assert resumed_turn is not None
    assert resumed_turn != first_turn
    assert feedback_message.turn_id == resumed_turn
    assert (feedback_message.payload.role, feedback_message.payload.phase) == (fixture.SYSTEM, "synthetic")
    assert all(event.turn_id == resumed_turn for event in continued.canonical_events)


def _assert_blocking_feedback_stops(
    first_turn: TurnId | None,
    resumed_turn: TurnId | None,
    stopped: TranslationResult,
    final_stop: TranslationResult,
) -> None:
    """Verify the initial and continuation stop facts."""
    assert payloads(stopped, TurnFinished)[0].turn_id == first_turn
    assert payloads(final_stop, TurnFinished)[0].turn_id == resumed_turn


def test_claude_blocking_stop_feedback_starts() -> None:
    """Verify claude blocking stop feedback starts the continuation turn."""
    _assert_blocking_feedback_sequence(_blocking_feedback_sequence())
