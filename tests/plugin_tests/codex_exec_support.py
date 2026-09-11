# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for Codex execution translation tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from domain import event_shell, ids
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.conversation_support import line_end_positions
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue

if TYPE_CHECKING:
    from harness.models.raw_events import TranslationResult


CODEX_HARNESS = ids.HarnessName("codex")


def background_shell_id(translation: TranslationResult) -> ids.ShellId:
    """Verify that the yielded event backgrounds one shell.

    Returns:
        The identity of the background shell.

    """
    backgrounded = payloads(translation, event_shell.ShellBackgrounded)
    assert len(backgrounded) == 1
    assert backgrounded[0].payload.shell_id
    assert not payloads(translation, event_shell.ShellFinished)
    return backgrounded[0].payload.shell_id


def assert_finished_background_shell(
    translation: TranslationResult,
    shell_id: ids.ShellId,
) -> None:
    """Verify the completed event finishes the background shell."""
    assert len(payloads(translation, event_shell.ShellFinished)) == 1
    output_finished = payloads(translation, event_shell.ShellOutputFinished)
    assert len(output_finished) == 1
    assert output_finished[0].payload.shell_id == shell_id


@dataclass(frozen=True)
class YieldedExecFixture:
    """Hold shell records, their rollout file, and line-end positions."""

    call: dict[str, JsonValue]
    yielded: dict[str, JsonValue]
    completed: dict[str, JsonValue]
    source: Path
    positions: tuple[str, ...]


def yielded_exec_fixture(
    tmp_path: Path,
    call_result: str,
    call_output: str,
) -> YieldedExecFixture:
    """Write the call, yield, and completion records for a shell fixture.

    Returns:
        The records, rollout path, and byte position after each record.

    """
    call: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
            fixture.NAME_FIELD: fixture.EXEC,
            fixture.CALL_ID_FIELD: fixture.CALL_BEFORE_RESTART_ID,
            fixture.INPUT_FIELD: (
                f'const r = await tools.exec_command({{cmd:"sleep 25",yield_time_ms:1000}});{call_result}'
            ),
        },
    }
    yielded: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
            fixture.CALL_ID_FIELD: fixture.CALL_BEFORE_RESTART_ID,
            fixture.OUTPUT_FIELD: call_output,
        },
    }
    completed: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
            fixture.ITEM_FIELD: {
                fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                fixture.ID_FIELD: "native-after-restart",
                fixture.PROCESS_ID: "22816",
                fixture.COMMAND_FIELD: [fixture.BIN_ZSH_PATH, fixture.LOGIN_SHELL_OPTION, "sleep 25"],
                fixture.STATUS_FIELD: fixture.COMPLETED,
                fixture.AGGREGATED_OUTPUT_ID: fixture.DONE_TEXT,
                fixture.EXIT_CODE: 0,
            },
        },
    }
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    lines = json_lines(call, yielded, completed)
    source.write_text("".join(lines), encoding=fixture.TEXT_ENCODING)
    return YieldedExecFixture(call, yielded, completed, source, line_end_positions(lines))


def json_lines(*documents: dict[str, JsonValue]) -> tuple[str, ...]:
    """Return one JSON-lines record for each document.

    Returns:
        One JSON-lines record for each document.

    """
    return tuple(f"{json.dumps(document)}\n" for document in documents)


def yielded_shell_id(exec_fixture: YieldedExecFixture) -> ids.ShellId:
    """Translate the call and yield records and check their shell identity.

    Returns:
        The identity shared by the start and background events.

    """
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        replace(
            raw_event(
                exec_fixture.call,
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id=fixture.CALL_BEFORE_RESTART_ID,
                source_position=exec_fixture.positions[0],
            ),
            source_name=str(exec_fixture.source),
        ),
    )
    backgrounded = translator.translate(
        replace(
            raw_event(
                exec_fixture.yielded,
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="yield-before-restart",
                source_position=exec_fixture.positions[1],
            ),
            source_name=str(exec_fixture.source),
        ),
    )
    shell_id = payloads(started, event_shell.ShellStarted)[0].payload.shell_id
    assert payloads(backgrounded, event_shell.ShellBackgrounded)[0].payload.shell_id == shell_id
    return shell_id
