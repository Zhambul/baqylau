# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for conversation translation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import (
    event_shell as shell_events,
    outcomes,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
    from harness.models.raw_events import RawEvent, TranslationResult
    from tests.plugin_tests.support_values import JsonValue

CLAUDE_MESSAGE_ID = "claude-message"


def line_end_positions(lines: tuple[str, ...]) -> tuple[str, ...]:
    """Measure the cumulative UTF-8 length of each line.

    Returns:
        The byte position after each line, as decimal strings.

    """
    positions = []
    position = 0
    for line in lines:
        position += len(line.encode())
        positions.append(str(position))
    return tuple(positions)


def claude_background_outcome(status: str) -> outcomes.Outcome:
    """Translate a background completion with the supplied native status.

    Returns:
        The required shell-output completion outcome.

    """
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: f"background-completion-{status}",
                fixture.ORIGIN_FIELD: fixture.task_notification_origin(),
                fixture.PROMPT_SOURCE_FIELD: fixture.SYSTEM,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: (
                        "<task-notification><task-id>bkdr7jbeo</task-id>"
                        "<tool-use-id>background-op-one</tool-use-id>"
                        f"<status>{status}</status>"
                        '<summary>Background command "Count" completed (exit code 0)</summary>'
                        "</task-notification>"
                    ),
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=f"background-completion-{status}",
        ),
    )
    outcome = payloads(translation, shell_events.ShellOutputFinished)[0].payload.outcome
    assert outcome is not None
    return outcome


def translate_agent_notification(
    translator: ClaudeCanonicalTranslator,
    event_id: str,
    result: str,
    record_type: str = fixture.QUEUE_OPERATION_ID,
) -> TranslationResult:
    """Translate a completed-agent notification from a queue or user record.

    Returns:
        The translation result for the supplied notification text.

    """
    content = (
        "<task-notification><task-id>child-one</task-id>"
        "<tool-use-id>agent-tool-one</tool-use-id>"
        "<status>completed</status>"
        '<summary>Agent "background worker" finished</summary>'
        f"<result>{result}</result></task-notification>"
    )
    document: dict[str, JsonValue] = (
        {
            fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
            fixture.OPERATION_FIELD: fixture.ENQUEUE,
            fixture.CONTENT_FIELD: content,
        }
        if record_type == fixture.QUEUE_OPERATION_ID
        else {
            fixture.TYPE_FIELD: fixture.USER,
            fixture.UUID_FIELD: event_id,
            fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
            fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: content},
        }
    )
    return translator.translate(
        raw_event(
            document,
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=event_id,
        ),
    )


def codex_rollout_event(document: JsonValue, source_position: int) -> RawEvent:
    """Encode a document as a Codex rollout event.

    Returns:
        The raw event with identity and position derived from the supplied offset.

    """
    return raw_event(
        document,
        harness=CODEX_HARNESS,
        source_type=fixture.ROLLOUT_SOURCE,
        raw_event_id=f"codex-bg-{source_position}",
        source_position=str(source_position),
    )


def translate_codex_rollout(
    translator: CodexCanonicalTranslator,
    document: JsonValue,
    raw_event_id: str,
    source_position: int,
) -> TranslationResult:
    """Translate a Codex rollout document at the supplied position.

    Returns:
        The result from the supplied translator.

    """
    return translator.translate(
        raw_event(
            document,
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=raw_event_id,
            source_position=str(source_position),
        ),
    )
