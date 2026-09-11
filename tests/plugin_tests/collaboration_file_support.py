# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for file convergence tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain import (
    event_base,
    event_shell,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models.raw_events import (
    RawEvent,
    TranslationResult,
)
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import encoded_event, payloads, raw_event

if TYPE_CHECKING:
    from tests.plugin_tests.support_values import JsonValue


def started_file_translators() -> tuple[
    ClaudeCanonicalTranslator,
    ClaudeCanonicalTranslator,
]:
    """Start two translators with the same file-read call.

    Returns:
        The two translators after the call has been translated.

    """
    translators = ClaudeCanonicalTranslator(), ClaudeCanonicalTranslator()
    call: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.ASSISTANT,
        fixture.UUID_FIELD: fixture.ASSISTANT_ONE,
        fixture.MESSAGE_FIELD: {
            fixture.ID_FIELD: "api-message",
            fixture.CONTENT_FIELD: [
                {
                    fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                    fixture.ID_FIELD: fixture.TOOL_ONE_ID,
                    fixture.NAME_FIELD: fixture.READ_TOOL,
                    fixture.INPUT_FIELD: {
                        fixture.FILE_PATH_FIELD: fixture.WORK_A_PY_PATH,
                    },
                },
            ],
        },
    }
    for translator in translators:
        translator.translate(
            raw_event(
                call,
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id="start",
            ),
        )
    return translators


@dataclass(frozen=True)
class ToolFinishEvidence:
    """Hold hook and transcript records and their matching shell-finish event."""

    hook_raw: RawEvent
    transcript_raw: RawEvent
    hook: TranslationResult
    transcript: TranslationResult
    hook_finished: event_base.CanonicalEvent


def matching_tool_finish(
    hook: TranslationResult,
    transcript: TranslationResult,
) -> event_base.CanonicalEvent:
    """Check that hook and transcript shell-finish events have the same stored form.

    Returns:
        The hook's shell-finish event.

    """
    hook_finished = payloads(hook, event_shell.ShellFinished)[0]
    transcript_finished = payloads(transcript, event_shell.ShellFinished)[0]
    assert encoded_event(hook_finished) == encoded_event(transcript_finished)
    return hook_finished


def tool_finish_evidence() -> ToolFinishEvidence:
    """Translate a shell call and both native completion records.

    Returns:
        The raw records, translations, and matching shell-finish event.

    """
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: fixture.PRINT_DIRECTORY_COMMAND},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="hook-start",
        ),
    )
    hook_raw = raw_event(
        {
            fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
            fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
            fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
            fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: fixture.PRINT_DIRECTORY_COMMAND},
            fixture.TOOL_RESPONSE_FIELD: fixture.OUTPUT_FIELD,
        },
        harness=CLAUDE_CODE_HARNESS,
        source_type=fixture.HOOK_SOURCE,
        raw_event_id="hook-finish",
    )
    transcript_raw = raw_event(
        {
            fixture.TYPE_FIELD: fixture.USER,
            fixture.UUID_FIELD: "result-one",
            fixture.MESSAGE_FIELD: {
                fixture.CONTENT_FIELD: [
                    {
                        fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                        fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
                        fixture.CONTENT_FIELD: fixture.OUTPUT_FIELD,
                    },
                ],
            },
        },
        harness=CLAUDE_CODE_HARNESS,
        source_type=fixture.TRANSCRIPT_SOURCE,
        raw_event_id="transcript-finish",
    )
    hook = translator.translate(hook_raw)
    transcript = translator.translate(transcript_raw)
    assert translator._toolcalls.calls == {}  # noqa: SLF001 -- Verify completed calls release cached state.
    return ToolFinishEvidence(
        hook_raw,
        transcript_raw,
        hook,
        transcript,
        matching_tool_finish(hook, transcript),
    )
