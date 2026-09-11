# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex shell input lifecycle translation tests."""

import json
from dataclasses import dataclass

from domain.event_shell import ShellFinished, ShellInputProvided, ShellProgressed, ShellStarted
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.models.raw_events import TranslationResult
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of

COMMAND_OUTCOMES = ((0, fixture.SUCCEEDED, "ok"), (2, fixture.FAILED, "bad"))


@dataclass(frozen=True)
class _WriteStdinSequence:
    started: TranslationResult
    initial_output: TranslationResult
    provided: TranslationResult
    continued_output: TranslationResult
    finished: TranslationResult


def test_codex_write_stdin_continues_original() -> None:
    """Verify codex write stdin continues the original operation."""
    translator = CodexCanonicalTranslator()

    sequence = _WriteStdinSequence(
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                        fixture.NAME_FIELD: fixture.EXEC,
                        fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                        fixture.INPUT_FIELD: 'tools.exec_command({"cmd":"read value"})',
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=fixture.COMMAND_FIELD,
                source_position=fixture.FORTY_TEXT,
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                        fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                        fixture.OUTPUT_FIELD: json.dumps({
                            fixture.SESSION_ID_FIELD: 77,
                            fixture.OUTPUT_FIELD: "waiting\n",
                        }),
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=fixture.COMMAND_OUTPUT_ID,
                source_position=fixture.FORTY_ONE_TEXT,
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                        fixture.NAME_FIELD: fixture.EXEC,
                        fixture.CALL_ID_FIELD: fixture.INPUT_ONE_ID,
                        fixture.INPUT_FIELD: r'tools.write_stdin({session_id:77,chars:"yes\n",yield_time_ms:1000})',
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=fixture.STDIN,
                source_position="42",
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                        fixture.CALL_ID_FIELD: fixture.INPUT_ONE_ID,
                        fixture.OUTPUT_FIELD: "accepted\n",
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="stdin-output",
                source_position="43",
            ),
        ),
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                        fixture.ITEM_FIELD: {
                            fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                            fixture.ID_FIELD: "execution-one",
                            fixture.PROCESS_ID: 77,
                            fixture.STATUS_FIELD: fixture.COMPLETED,
                            fixture.AGGREGATED_OUTPUT_ID: "waiting\naccepted\n",
                            fixture.EXIT_CODE: 0,
                        },
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="command-finished",
                source_position="44",
            ),
        ),
    )

    shell_id = payloads(sequence.started, ShellStarted)[0].payload.shell_id
    assert shell_id == fixture.COMMAND_ONE
    _assert_write_stdin_progress(sequence, shell_id)
    _assert_write_stdin_finish(sequence, shell_id)


def _assert_write_stdin_progress(
    sequence: _WriteStdinSequence,
    shell_id: str,
) -> None:
    """Verify the progress and input facts for one shell."""
    assert payloads(sequence.initial_output, ShellProgressed)[0].payload.shell_id == shell_id
    input_payload = payloads(sequence.provided, ShellInputProvided)[0].payload
    assert input_payload.shell_id == shell_id
    assert text_of(input_payload.content) == "yes\n"
    assert input_payload.closed is False
    assert payloads(sequence.continued_output, ShellProgressed)[0].payload.shell_id == shell_id


def _assert_write_stdin_finish(
    sequence: _WriteStdinSequence,
    shell_id: str,
) -> None:
    """Verify the completion facts for one shell."""
    finished_payload = payloads(sequence.finished, ShellFinished)[0].payload
    assert finished_payload.shell_id == shell_id
    assert text_of(finished_payload.result) == "waiting\naccepted\n"
    # zero is a real exit code: a falsy-int coercion once dropped it and marked
    # the clean exit "failed" (session 01a009e1, 2026-08-16)
    assert finished_payload.exit_code == 0
    assert finished_payload.outcome == fixture.SUCCEEDED


def test_codex_command_completion_outcome_follows() -> None:
    """Verify codex command completion outcome follows the integer exit code."""
    translator = CodexCanonicalTranslator()
    for exit_code, expected_outcome, suffix in COMMAND_OUTCOMES:
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                        fixture.NAME_FIELD: fixture.EXEC,
                        fixture.CALL_ID_FIELD: f"command-{suffix}",
                        fixture.INPUT_FIELD: 'tools.exec_command({"cmd":"run"})',
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=f"command-{suffix}",
                source_position=fixture.FORTY_TEXT,
            ),
        )
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                        fixture.CALL_ID_FIELD: f"command-{suffix}",
                        fixture.OUTPUT_FIELD: json.dumps({
                            fixture.SESSION_ID_FIELD: suffix,
                            fixture.OUTPUT_FIELD: "running\n",
                        }),
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=f"command-output-{suffix}",
                source_position=fixture.FORTY_ONE_TEXT,
            ),
        )
        finished_payload = payloads(
            translator.translate(
                raw_event(
                    {
                        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                        fixture.PAYLOAD_FIELD: {
                            fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                            fixture.ITEM_FIELD: {
                                fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                                fixture.ID_FIELD: f"execution-{suffix}",
                                fixture.PROCESS_ID: suffix,
                                fixture.STATUS_FIELD: fixture.COMPLETED,
                                fixture.AGGREGATED_OUTPUT_ID: fixture.DONE_TEXT,
                                fixture.EXIT_CODE: exit_code,
                            },
                        },
                    },
                    harness=CODEX_HARNESS,
                    source_type=fixture.ROLLOUT_SOURCE,
                    raw_event_id=f"command-finished-{suffix}",
                    source_position="42",
                ),
            ),
            ShellFinished,
        )[0].payload
        assert finished_payload.exit_code == exit_code
        assert finished_payload.outcome == expected_outcome
