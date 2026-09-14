# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex shell input edge-case translation tests."""

import json

import pytest

from domain.event_shell import ShellInputProvided, ShellStarted
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.models.raw_events import TranslationError
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_codex_empty_write_stdin_poll_is_raw_only() -> None:
    """Verify codex empty write stdin poll is raw only and ctrl c is input."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.INPUT_FIELD: 'tools.exec_command({"cmd":"sleep 30"})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_FIELD,
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.OUTPUT_FIELD: json.dumps({
                        fixture.SESSION_ID_FIELD: 88,
                        fixture.OUTPUT_FIELD: "",
                    }),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_OUTPUT_ID,
            source_position=fixture.ELEVEN_TEXT,
        ),
    )
    poll = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "poll-one",
                    fixture.INPUT_FIELD: "tools.write_stdin({session_id:88,yield_time_ms:1000})",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="poll",
            source_position=fixture.TWELVE_TEXT,
        ),
    )
    interrupt = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "interrupt-one",
                    fixture.INPUT_FIELD: r'tools.write_stdin({session_id:88,chars:"\u0003",yield_time_ms:1000})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.INTERRUPT,
            source_position="13",
        ),
    )

    assert poll.decision == fixture.IGNORED_NONSEMANTIC
    assert poll.canonical_events == ()
    assert text_of(payloads(interrupt, ShellInputProvided)[0].payload.content) == "\x03"


def test_codex_write_stdin_requires_known_process() -> None:
    """Verify codex write stdin requires a known process session."""
    with pytest.raises(TranslationError, match="unknown process session"):
        CodexCanonicalTranslator().translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                        fixture.NAME_FIELD: "write_stdin",
                        fixture.CALL_ID_FIELD: fixture.INPUT_ONE_ID,
                        fixture.ARGUMENTS_FIELD: json.dumps({
                            fixture.SESSION_ID_FIELD: 99,
                            "chars": fixture.HELLO,
                        }),
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=fixture.STDIN,
            ),
        )


def test_codex_write_stdin_links_deferred_process() -> None:
    """Verify stdin links a deferred process to the one open command."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.INPUT_FIELD: (
                        'const r=await tools.exec_command({cmd:"read value",yield_time_ms:30000});'
                        'if(r.session_id) text(await tools.write_stdin({session_id:r.session_id,chars:""}));'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_FIELD,
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.OUTPUT_FIELD: "Script running with cell ID 1\nWall time 31.0 seconds\nOutput:\n",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_OUTPUT_ID,
            source_position=fixture.ELEVEN_TEXT,
        ),
    )

    provided = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.INPUT_ONE_ID,
                    fixture.INPUT_FIELD: r'tools.write_stdin({session_id:85766,chars:"yes\n"})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.STDIN,
            source_position=fixture.TWELVE_TEXT,
        ),
    )

    shell_id = payloads(started, ShellStarted)[0].payload.shell_id
    input_payload = payloads(provided, ShellInputProvided)[0].payload
    assert input_payload.shell_id == shell_id
    assert text_of(input_payload.content) == "yes\n"


def test_codex_late_write_stdin_does_not_reopen() -> None:
    """Verify codex late write stdin does not reopen a finished command."""
    translator = CodexCanonicalTranslator()
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
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.COMMAND_ONE,
                    fixture.OUTPUT_FIELD: json.dumps({
                        fixture.SESSION_ID_FIELD: 77,
                        fixture.OUTPUT_FIELD: "",
                    }),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.COMMAND_OUTPUT_ID,
        ),
    )
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
                        fixture.AGGREGATED_OUTPUT_ID: "received:yes\n",
                        fixture.EXIT_CODE: 0,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="command-finished",
        ),
    )

    late = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.INPUT_ONE_ID,
                    fixture.INPUT_FIELD: r'tools.write_stdin({session_id:77,chars:"yes\n"})',
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.STDIN,
        ),
    )

    assert late.decision == fixture.IGNORED_NONSEMANTIC
    assert late.canonical_events == ()
