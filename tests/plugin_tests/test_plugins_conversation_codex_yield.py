# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex yielded shell translation tests."""

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from domain import (
    event_shell as shell_events,
    ids as domain_ids,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.codex_exec_support import (
    assert_finished_background_shell,
    background_shell_id,
    yielded_exec_fixture,
    yielded_shell_id,
)
from tests.plugin_tests.conversation_support import (
    codex_rollout_event,
)
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from tests.plugin_tests.support_values import JsonValue


CODEX_HARNESS = domain_ids.HarnessName("codex")


def test_codex_exec_that_outlives_its_yield() -> None:
    """Verify codex exec that outlives its yield is announced as background once.

    Codex's background terminal: the exec handed back a live session (the cell
        id `/ps` lists) with no exit code. Every continuation poll reports it again,
        and the fact is about the operation, not about the poll.
    """
    translator = CodexCanonicalTranslator()

    translator.translate(
        codex_rollout_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.CALL_ONE_ID,
                    fixture.INPUT_FIELD: 'const r = await tools.exec_command({"cmd":"sleep 30","yield_time_ms":250});',
                },
            },
            10,
        ),
    )
    first = translator.translate(
        codex_rollout_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.CALL_ONE_ID,
                    fixture.OUTPUT_FIELD: '{"output":"","session_id":4242}',
                },
            },
            fixture.BACKGROUND_START_POSITION,
        ),
    )
    assert not payloads(
        translator.translate(
            codex_rollout_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                        fixture.CALL_ID_FIELD: fixture.CALL_ONE_ID,
                        fixture.OUTPUT_FIELD: '{"output":"still going","session_id":4242}',
                    },
                },
                fixture.BACKGROUND_PROGRESS_POSITION,
            ),
        ),
        shell_events.ShellBackgrounded,
    )

    shell_id = background_shell_id(first)

    finished = translator.translate(
        codex_rollout_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.CALL_ONE_ID,
                    fixture.OUTPUT_FIELD: '{"output":"done","exit_code":0}',
                },
            },
            fixture.BACKGROUND_FINISH_POSITION,
        ),
    )
    assert_finished_background_shell(finished, shell_id)


@pytest.mark.parametrize(
    ("call_result", "call_output"),
    [
        (
            "text(JSON.stringify(r));",
            ('Script completed\nOutput:\n{"session_id":22816,"output":"","wall_time_seconds":1.0}'),
        ),
        (
            "text(r.output || `session_id:${r.session_id}`);",
            "Script completed\nOutput:\nsession_id:22816",
        ),
        (
            "text(r.output); if (r.session_id) text(`SESSION_ID:${r.session_id}`);",
            "Script completed\nOutput:\nSESSION_ID:22816",
        ),
    ],
)
def test_codex_yielded_exec_closes_original_shell(
    tmp_path: Path,
    call_result: str,
    call_output: str,
) -> None:
    """Verify codex yielded exec closes the original shell after translator restart."""
    exec_fixture = yielded_exec_fixture(tmp_path, call_result, call_output)
    shell_id = yielded_shell_id(exec_fixture)

    after_restart = CodexCanonicalTranslator().translate(
        replace(
            raw_event(
                exec_fixture.completed,
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="completion-after-restart",
                source_position=exec_fixture.positions[2],
            ),
            source_name=str(exec_fixture.source),
        ),
    )

    assert payloads(after_restart, shell_events.ShellStarted) == []
    assert payloads(after_restart, shell_events.ShellFinished)[0].payload.shell_id == shell_id
    assert payloads(after_restart, shell_events.ShellOutputFinished)[0].payload.shell_id == shell_id


def test_codex_fg_exec_closes_original_shell(
    tmp_path: Path,
) -> None:
    """Verify codex foreground exec closes the original shell after translator restart."""
    call: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
            fixture.NAME_FIELD: fixture.EXEC,
            fixture.CALL_ID_FIELD: fixture.CALL_BEFORE_RESTART_ID,
            fixture.INPUT_FIELD: (
                "const r = await tools.exec_command({cmd:\"python -c 'pass'\",yield_time_ms:30000});text(r.output);"
            ),
        },
    }
    completed: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
            fixture.ITEM_FIELD: {
                fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                fixture.ID_FIELD: "native-after-restart",
                fixture.PROCESS_ID: "22817",
                fixture.COMMAND_FIELD: [
                    fixture.BIN_ZSH_PATH,
                    fixture.LOGIN_SHELL_OPTION,
                    "python -c 'pass'",
                ],
                fixture.STATUS_FIELD: fixture.COMPLETED,
                fixture.AGGREGATED_OUTPUT_ID: "",
                fixture.EXIT_CODE: 0,
            },
        },
    }
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    source.write_text(
        f"{json.dumps(call)}\n{json.dumps(completed)}\n",
        encoding=fixture.TEXT_ENCODING,
    )

    started = CodexCanonicalTranslator().translate(
        replace(
            raw_event(
                call,
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="codex-call-before-restart",
                source_position=fixture.ZERO_TEXT,
            ),
            source_name=str(source),
        ),
    )
    finished = CodexCanonicalTranslator().translate(
        replace(
            raw_event(
                completed,
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="codex-completion-after-restart",
                source_position=str(len(f"{json.dumps(call)}\n".encode())),
            ),
            source_name=str(source),
        ),
    )

    assert payloads(finished, shell_events.ShellStarted) == []
    assert (
        payloads(finished, shell_events.ShellFinished)[0].payload.shell_id
        == payloads(
            started,
            shell_events.ShellStarted,
        )[0].payload.shell_id
    )
