# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex completed shell translation tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import (
    event_shell as shell_events,
    outcomes,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.conversation_support import (
    translate_codex_rollout,
)
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of

if TYPE_CHECKING:
    from harness.models.raw_events import TranslationResult

FAILED_COMMAND_EXIT_CODE = 7


def test_codex_fast_exec_uses_authoritative_item() -> None:
    """Verify codex fast exec uses the authoritative item exit code."""
    translator = CodexCanonicalTranslator()
    call = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "call-fast",
                    fixture.INPUT_FIELD: 'const r = await tools.exec_command({cmd:"exit 7"}); text(r.output);',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="fast-call",
        ),
    )
    completed = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                        fixture.ID_FIELD: "exec-fast",
                        fixture.PROCESS_ID: "818",
                        fixture.COMMAND_FIELD: [fixture.BIN_ZSH_PATH, fixture.LOGIN_SHELL_OPTION, "exit 7"],
                        fixture.STATUS_FIELD: fixture.FAILED,
                        fixture.STDOUT: "",
                        "stderr": "failed\n",
                        fixture.AGGREGATED_OUTPUT_ID: "failed\n",
                        fixture.EXIT_CODE: FAILED_COMMAND_EXIT_CODE,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="fast-item",
        ),
    )
    wrapper = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "call-fast",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\nfailed\n",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="fast-output",
        ),
    )

    assert len(payloads(call, shell_events.ShellStarted)) == 1
    finish = payloads(completed, shell_events.ShellFinished)[0].payload
    assert (finish.outcome, finish.exit_code, text_of(finish.result)) == (
        outcomes.Outcome.FAILED, FAILED_COMMAND_EXIT_CODE, "failed\n",
    )
    assert wrapper.decision == fixture.IGNORED_NONSEMANTIC
    assert wrapper.canonical_events == ()


def test_codex_native_command_text_closes_right() -> None:
    """Verify codex native command text closes the right exec when several are pending.

    A completed command has no wrapper call id. Its native command text is
        the stable correlation when a prior parallel cell left several wrappers
        pending. A blank yielded wrapper must not reopen the completed command as
        background work.
    """
    translator = CodexCanonicalTranslator()

    parallel = translate_codex_rollout(
        translator,
        {
            fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
            fixture.PAYLOAD_FIELD: {
                fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                fixture.NAME_FIELD: fixture.EXEC,
                fixture.CALL_ID_FIELD: "parallel-cell",
                fixture.INPUT_FIELD: (
                    "const [a,b] = await Promise.all(["
                    'tools.exec_command({cmd:"printf alpha"}),'
                    'tools.exec_command({cmd:"printf beta"})]);'
                    'text("done");'
                ),
            },
        },
        "parallel-call",
        10,
    )
    assert [event.payload.shell_id for event in payloads(parallel, shell_events.ShellStarted)] == [
        "parallel-cell:1",
        "parallel-cell:2",
    ]

    assert (
        payloads(
            translate_codex_rollout(
                translator,
                {
                    fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                        fixture.ITEM_FIELD: {
                            fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                            fixture.ID_FIELD: "native-beta",
                            fixture.PROCESS_ID: "902",
                            fixture.COMMAND_FIELD: [
                                fixture.BIN_ZSH_PATH,
                                fixture.LOGIN_SHELL_OPTION,
                                "printf beta",
                            ],
                            fixture.STATUS_FIELD: fixture.COMPLETED,
                            fixture.AGGREGATED_OUTPUT_ID: "beta",
                            fixture.EXIT_CODE: 0,
                        },
                    },
                },
                "beta-finished",
                fixture.FIRST_CONVERSATION_CURSOR,
            ),
            shell_events.ShellFinished,
        )[0].payload.shell_id
        == "parallel-cell:2"
    )

    blank_call = translate_codex_rollout(
        translator,
        {
            fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
            fixture.PAYLOAD_FIELD: {
                fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                fixture.NAME_FIELD: fixture.EXEC,
                fixture.CALL_ID_FIELD: fixture.BLANK_CALL_ID,
                fixture.INPUT_FIELD: (
                    'const r = await tools.exec_command({cmd:"true",yield_time_ms:30000});text(r.output);'
                ),
            },
        },
        fixture.BLANK_CALL_ID,
        fixture.SECOND_CONVERSATION_CURSOR,
    )
    assert payloads(blank_call, shell_events.ShellStarted)[0].payload.shell_id == fixture.BLANK_CALL_ID

    blank_finished = translate_codex_rollout(
        translator,
        {
            fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
            fixture.PAYLOAD_FIELD: {
                fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                fixture.ITEM_FIELD: {
                    fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                    fixture.ID_FIELD: "native-blank",
                    fixture.PROCESS_ID: "903",
                    fixture.COMMAND_FIELD: [fixture.BIN_ZSH_PATH, fixture.LOGIN_SHELL_OPTION, "true"],
                    fixture.STATUS_FIELD: fixture.COMPLETED,
                    fixture.AGGREGATED_OUTPUT_ID: "",
                    fixture.EXIT_CODE: 0,
                },
            },
        },
        "blank-finished",
        fixture.THIRD_CONVERSATION_CURSOR,
    )
    assert payloads(blank_finished, shell_events.ShellFinished)[0].payload.shell_id == fixture.BLANK_CALL_ID

    wrapper = translate_codex_rollout(
        translator,
        {
            fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
            fixture.PAYLOAD_FIELD: {
                fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                fixture.CALL_ID_FIELD: fixture.BLANK_CALL_ID,
                fixture.OUTPUT_FIELD: [
                    {
                        fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                        fixture.TEXT_FIELD: "Script completed\nWall time 0.1 seconds\nOutput:\n",
                    },
                    {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: ""},
                ],
            },
        },
        "blank-wrapper",
        fixture.FOURTH_CONVERSATION_CURSOR,
    )
    _assert_blank_wrapper_output(wrapper)


def _assert_blank_wrapper_output(wrapper: TranslationResult) -> None:
    """Verify that a blank completed wrapper does not restart its shell."""
    assert payloads(wrapper, shell_events.ShellBackgrounded) == []
    output_finished = payloads(wrapper, shell_events.ShellOutputFinished)
    assert len(output_finished) == 1
    assert output_finished[0].payload.shell_id == fixture.BLANK_CALL_ID


def test_codex_dynamic_exec_uses_authoritative() -> None:
    """Verify codex dynamic exec uses authoritative command items.

    A code-mode loop has no literal command in its wrapper. Each native
        command item still supplies a complete and distinct command lifecycle.
    """
    translator = CodexCanonicalTranslator()

    wrapper = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "dynamic-cell",
                    fixture.INPUT_FIELD: (
                        "for (const item of values) { await tools.exec_command({cmd: `printf ${item}`}); }"
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="dynamic-wrapper",
        ),
    )
    assert wrapper.decision == fixture.IGNORED_NONSEMANTIC

    completed = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                        fixture.ID_FIELD: "dynamic-command-01",
                        fixture.PROCESS_ID: "991",
                        fixture.COMMAND_FIELD: [
                            fixture.BIN_ZSH_PATH,
                            fixture.LOGIN_SHELL_OPTION,
                            "printf history-01",
                        ],
                        fixture.STATUS_FIELD: fixture.COMPLETED,
                        fixture.AGGREGATED_OUTPUT_ID: "history-01",
                        fixture.EXIT_CODE: 0,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="dynamic-completed",
        ),
    )

    _assert_dynamic_shell(completed)


def _assert_dynamic_shell(completed: TranslationResult) -> None:
    """Verify the shell lifecycle from an authoritative native command."""
    started = payloads(completed, shell_events.ShellStarted)
    finished = payloads(completed, shell_events.ShellFinished)
    assert len(started) == len(finished) == 1
    finished_event = finished[0]
    assert started[0].payload.shell_id == finished_event.payload.shell_id
    assert text_of(started[0].payload.command) == "printf history-01"
    assert finished_event.payload.result is not None
    assert text_of(finished_event.payload.result) == "history-01"
