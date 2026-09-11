# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude turn collaboration tests."""

from __future__ import annotations

from domain import (
    event_conversation,
    event_shell,
    outcomes,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.collaboration_assertion_support import (
    assert_queued_turn_chain,
)
from tests.plugin_tests.collaboration_queued_support import (
    assert_queued_attachment_turns,
    assert_queued_turn_sequence,
)
from tests.plugin_tests.support_events import encoded_event, payloads, raw_event


def test_claude_hook_and_transcript_produce() -> None:
    """Verify claude hook and transcript produce identical tool start facts."""
    translator = ClaudeCanonicalTranslator()
    hook = translator.translate(
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
            observed_at=100.0,
        ),
    )
    transcript = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.ASSISTANT_ONE,
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: "api-message",
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: fixture.TOOL_ONE_ID,
                            fixture.NAME_FIELD: fixture.BASH_TOOL,
                            fixture.INPUT_FIELD: {fixture.COMMAND_FIELD: fixture.PRINT_DIRECTORY_COMMAND},
                            "caller": {fixture.TYPE_FIELD: "direct"},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="transcript-start",
            observed_at=fixture.COLLABORATION_EVENT_TIME,
        ),
    )
    assert encoded_event(payloads(hook, event_shell.ShellStarted)[0]) == encoded_event(
        payloads(transcript, event_shell.ShellStarted)[0],
    )


def test_claude_interrupt_marker_aborts_turn() -> None:
    """Verify claude interrupt marker aborts the turn and cancels its shell."""
    translator = ClaudeCanonicalTranslator()
    prompt = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.PROMPT_ONE_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Run a long command",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.PROMPT_KIND,
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.ASSISTANT_ONE,
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: fixture.MESSAGE_ONE_ID,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: fixture.SHELL_ONE_ID,
                            fixture.NAME_FIELD: fixture.BASH_TOOL,
                            fixture.INPUT_FIELD: {fixture.COMMAND_FIELD: "python -c 'import time; time.sleep(60)'"},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="shell-start",
        ),
    )
    shell_result = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.SHELL_RESULT_ID,
                "toolDenialKind": "user-rejected",
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: fixture.SHELL_ONE_ID,
                            fixture.IS_ERROR: True,
                            fixture.CONTENT_FIELD: "User rejected tool use",
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.SHELL_RESULT_ID,
        ),
    )
    aborted = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "interrupt-one",
                fixture.INTERRUPTED_MESSAGE_ID: fixture.MESSAGE_ONE_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                            fixture.TEXT_FIELD: fixture.REQUEST_INTERRUPTED_BY_USER_FOR_TOOL_USE_TEX,
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="turn-abort",
        ),
    )

    assert payloads(shell_result, event_shell.ShellFinished)[0].payload.outcome == outcomes.Outcome.CANCELLED
    aborted_turn = payloads(aborted, event_conversation.TurnAborted)[0]
    assert (
        aborted_turn.turn_id
        == payloads(
            prompt,
            event_conversation.TurnStarted,
        )[0].turn_id
    )


def test_claude_queued_prompt_after_interrupt() -> None:
    """Verify claude queued prompt after interrupt starts a new turn."""
    translator = ClaudeCanonicalTranslator()
    first = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.FIRST_PROMPT_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Run a long command",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.FIRST_PROMPT_ID,
        ),
    )
    queued = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.QUEUED_PROMPT_ID,
                fixture.PROMPT_SOURCE_FIELD: fixture.QUEUED,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Continue after stop",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.QUEUED_PROMPT_ID,
        ),
    )
    marker = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.INTERRUPT_MARKER,
                fixture.INTERRUPTED_MESSAGE_ID: fixture.MESSAGE_ONE_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                            fixture.TEXT_FIELD: fixture.REQUEST_INTERRUPTED_BY_USER_FOR_TOOL_USE_TEX,
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.INTERRUPT_MARKER,
        ),
    )
    answer = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.QUEUED_ANSWER,
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: fixture.QUEUED_ANSWER,
                    fixture.ROLE_FIELD: fixture.ASSISTANT,
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "continued"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.QUEUED_ANSWER,
        ),
    )
    assert_queued_turn_sequence(
        first,
        queued,
        marker,
        answer,
        translator.translate(
            raw_event(
                {
                    fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
                    fixture.HOOK_EVENT_ID_FIELD: fixture.QUEUED_STOP_ID,
                },
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.HOOK_SOURCE,
                raw_event_id=fixture.QUEUED_STOP_ID,
            ),
        ),
    )


def test_claude_queued_attachment_starts_turn() -> None:
    """Verify claude queued attachment starts a turn before the stop hook."""
    translator = ClaudeCanonicalTranslator()
    first = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.FIRST_PROMPT_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Run a command",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.FIRST_PROMPT_ID,
        ),
    )
    queued = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ATTACHMENT,
                fixture.UUID_FIELD: fixture.QUEUED_PROMPT_ID,
                fixture.ATTACHMENT: {
                    fixture.TYPE_FIELD: "queued_command",
                    fixture.PROMPT_KIND: "Reply after the command",
                    "commandMode": fixture.PROMPT_KIND,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.QUEUED_PROMPT_ID,
        ),
    )
    stopped = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
                fixture.HOOK_EVENT_ID_FIELD: fixture.QUEUED_STOP_ID,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=fixture.QUEUED_STOP_ID,
        ),
    )
    answer = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.QUEUED_ANSWER,
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: "queued-response",
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "done"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.QUEUED_ANSWER,
        ),
    )

    assert_queued_attachment_turns(first, queued, stopped, answer)


def test_claude_interrupt_stop_before_answer() -> None:
    """Verify claude interrupt stop before answer keeps the queued turn."""
    translator = ClaudeCanonicalTranslator()
    first = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.FIRST_PROMPT_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Run a long command",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.FIRST_PROMPT_ID,
        ),
    )
    queued = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.QUEUED_PROMPT_ID,
                fixture.PROMPT_SOURCE_FIELD: fixture.QUEUED,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Reply after interrupt",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.QUEUED_PROMPT_ID,
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.INTERRUPT_MARKER,
                fixture.INTERRUPTED_MESSAGE_ID: "first-response",
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                            fixture.TEXT_FIELD: fixture.REQUEST_INTERRUPTED_BY_USER_FOR_TOOL_USE_TEX,
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.INTERRUPT_MARKER,
        ),
    )
    stopped = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
                fixture.HOOK_EVENT_ID_FIELD: fixture.QUEUED_STOP_ID,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=fixture.QUEUED_STOP_ID,
        ),
    )
    answer = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.QUEUED_ANSWER,
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: "queued-response",
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "continued"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.QUEUED_ANSWER,
        ),
    )

    assert_queued_turn_chain(first, queued, stopped, answer)


def test_claude_text_block_prompt_with_image() -> None:
    """Verify claude text block prompt with an image opens its own turn."""
    translator = ClaudeCanonicalTranslator()
    first = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.FIRST_PROMPT_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "Reply first",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.FIRST_PROMPT_ID,
        ),
    )
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
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: "first-answer",
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: "first-response",
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "ready"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="first-answer",
        ),
    )
    image_prompt = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "image-prompt",
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "Inspect the image"},
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="image-prompt",
        ),
    )
    translator.translate(
        raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK, fixture.HOOK_EVENT_ID_FIELD: "image-stop"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="image-stop",
        ),
    )
    answer = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: "image-answer",
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: "image-response",
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "123"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="image-answer",
        ),
    )

    image_turn = payloads(image_prompt, event_conversation.TurnStarted)[0].turn_id
    assert (
        image_turn is not None
    )
    assert (
        image_turn
        != payloads(
            first,
            event_conversation.TurnStarted,
        )[0].turn_id
    )
    assert (
        payloads(
            image_prompt,
            event_conversation.MessageCreated,
        )[0].turn_id
        == image_turn
    )
    assert payloads(image_prompt, event_conversation.TurnStarted)[0].payload.prompt_message_id == (
        payloads(
            image_prompt,
            event_conversation.MessageCreated,
        )[0].payload.message_id
    )
    assert (
        payloads(answer, event_conversation.MessageCreated)[0].turn_id,
        payloads(answer, event_conversation.TurnFinished)[0].turn_id,
    ) == (image_turn, image_turn)


def test_claude_response_closes_turn_when_stop() -> None:
    """Verify claude response closes a turn when the stop hook arrives first.

    A busy daemon can translate the pushed Stop hook before it reads the
        prompt and answer from the transcript. The answer must still close the turn.
    """
    translator = ClaudeCanonicalTranslator()
    early_stop = translator.translate(
        raw_event(
            {fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK, fixture.HOOK_EVENT_ID_FIELD: "early-stop"},
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="early-stop",
        ),
    )
    prompt = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "late-prompt",
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: "reply now",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="late-prompt",
        ),
    )
    answer = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: "late-answer",
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: "late-answer",
                    fixture.ROLE_FIELD: fixture.ASSISTANT,
                    fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "done"}],
                    fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="late-answer",
        ),
    )

    final_message = payloads(answer, event_conversation.MessageCreated)[0]
    assert payloads(early_stop, event_conversation.TurnFinished)[0].turn_id is None
    assert (
        final_message.turn_id
        == payloads(
            prompt,
            event_conversation.TurnStarted,
        )[0].turn_id
    )
    assert (
        payloads(answer, event_conversation.TurnFinished)[0].turn_id
        == payloads(
            prompt,
            event_conversation.TurnStarted,
        )[0].turn_id
    )
    assert (
        payloads(
            answer,
            event_conversation.TurnFinished,
        )[0].payload.final_message_id
        == final_message.payload.message_id
    )
