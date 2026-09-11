# Copyright (c) 2026 Zhambyl Yermagambet
"""Current Codex collaboration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import (
    event_resource,
    event_shell,
    outcomes,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.collaboration_assertion_support import (
    assert_ignored_translation,
)
from tests.plugin_tests.collaboration_current_support import (
    assert_completed_shell,
    assert_current_collaboration_item,
    assert_yielded_shell,
    current_collaboration_translator,
)
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from pathlib import Path


def test_codex_tool_batch_tracks_one_exec_and_its() -> None:
    """Verify codex tool batch tracks one exec and its dynamic wait."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "exec-and-wait",
                    fixture.INPUT_FIELD: (
                        'const r = await tools.exec_command({cmd:"sleep 30"});'
                        "if(r.session_id) text(await tools.write_stdin("
                        "{session_id:r.session_id,yield_time_ms:30000}));"
                    ),
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID},
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="exec-and-wait",
            source_position=fixture.TEN_TEXT,
        ),
    )
    shell = payloads(started, event_shell.ShellStarted)[0].payload
    assert text_of(shell.command) == "sleep 30"

    completed = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                        fixture.ID_FIELD: "native-process-one",
                        fixture.STATUS_FIELD: fixture.COMPLETED,
                        fixture.PROCESS_ID: "1234",
                        fixture.AGGREGATED_OUTPUT_ID: fixture.DONE_TEXT,
                        fixture.EXIT_CODE: 0,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="exec-and-wait-completed",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )
    finished = payloads(completed, event_shell.ShellFinished)[0].payload
    assert finished.shell_id == shell.shell_id
    assert finished.outcome == outcomes.Outcome.SUCCEEDED


def test_codex_current_collaboration_wrapper(tmp_path: Path) -> None:
    """Verify codex current collaboration wrapper and item are known."""
    translate_rollout = current_collaboration_translator(tmp_path)

    call_id = "exec-spawn"
    assert_ignored_translation(
        translate_rollout(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.INPUT_FIELD: (
                        "const r = await tools.multi_agent_v2__spawn_agent("
                        '{message:"reply only with the word gathered."}); text(r);'
                    ),
                    fixture.CALL_ID_FIELD: call_id,
                },
            },
            "spawn-call",
            fixture.TEN_TEXT,
        ),
    )

    assert_ignored_translation(
        translate_rollout(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: call_id,
                    fixture.OUTPUT_FIELD: '{"agent_id":"child-one","nickname":"Dirac"}',
                },
            },
            "spawn-output",
            "20",
        ),
    )

    batch_id = "exec-spawn-batch"
    assert_ignored_translation(
        translate_rollout(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.INPUT_FIELD: (
                        "const [a, b] = await Promise.all(["
                        'tools.multi_agent_v2__spawn_agent({message:"alpha"}),'
                        'tools.multi_agent_v2__spawn_agent({message:"beta"})]);'
                        'text("launched");'
                    ),
                    fixture.CALL_ID_FIELD: batch_id,
                },
            },
            "spawn-batch",
            "25",
        ),
    )
    assert_ignored_translation(
        translate_rollout(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: batch_id,
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\nlaunched",
                },
            },
            "spawn-batch-output",
            "27",
        ),
    )
    assert_current_collaboration_item(translate_rollout, call_id)


@pytest.mark.parametrize(
    fixture.OUTPUT_FIELD,
    [
        (
            {
                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_WALL_TIME_OUTPUT_TEXT,
            },
            {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: ""},
        ),
        (
            {
                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_WALL_TIME_OUTPUT_TEXT,
            },
            {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: "session_id:55812"},
        ),
        (
            {
                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_WALL_TIME_OUTPUT_TEXT,
            },
            {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: ""},
            {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: "session_id=55812"},
        ),
        (
            {
                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_WALL_TIME_OUTPUT_TEXT,
            },
            {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: "55812"},
        ),
        (
            {
                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_WALL_TIME_OUTPUT_TEXT,
            },
            {fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: "session 55812"},
        ),
    ],
)
def test_codex_yielded_output_waits_for_completed(
    output: list[JsonValue],
) -> None:
    """Verify codex yielded output waits for the completed item."""
    translator = CodexCanonicalTranslator()
    shell = payloads(
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                        fixture.NAME_FIELD: fixture.EXEC,
                        fixture.CALL_ID_FIELD: "yielded-call",
                        fixture.INPUT_FIELD: (
                            "const r = await tools.exec_command("
                            '{cmd:"sleep 5; echo done",yield_time_ms:1000});'
                            'text(r.output || r.session_id || "");'
                        ),
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="yielded-call",
                source_position=fixture.TEN_TEXT,
            ),
        ),
        event_shell.ShellStarted,
    )[0].payload
    yielded = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "yielded-call",
                    fixture.OUTPUT_FIELD: output,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="yielded-output",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )
    assert_yielded_shell(yielded, shell)

    completed = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.COMMAND_EXECUTION_KIND,
                        fixture.ID_FIELD: "native-yielded-process",
                        fixture.STATUS_FIELD: fixture.COMPLETED,
                        fixture.PROCESS_ID: "4242",
                        fixture.AGGREGATED_OUTPUT_ID: fixture.DONE_TEXT,
                        fixture.EXIT_CODE: 0,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="yielded-completed",
            source_position=fixture.TWELVE_TEXT,
        ),
    )
    assert_completed_shell(completed, shell)


def test_codex_numeric_command_output_is_not() -> None:
    """Verify codex numeric command output is not a process reference."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "numeric-output",
                    fixture.INPUT_FIELD: (
                        'const r = await tools.exec_command({cmd:"printf 55812",yield_time_ms:1000});text(r.output);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="numeric-output",
            source_position="20",
        ),
    )
    shell = payloads(started, event_shell.ShellStarted)[0].payload

    result = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "numeric-output",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\n55812",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="numeric-output-result",
            source_position="21",
        ),
    )

    finished = payloads(result, event_shell.ShellFinished)[0].payload
    assert finished.shell_id == shell.shell_id
    assert finished.result is not None
    assert text_of(finished.result) == "55812"
    assert payloads(result, event_shell.ShellBackgrounded) == []


def test_codex_node_repl_file_read_resolves_its() -> None:
    """Verify codex node repl file read resolves its cwd suffix."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.ID_FIELD: fixture.SESSION_ONE_ID,
                    fixture.CWD_FIELD: fixture.WORK_PATH,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="session-meta",
            source_position=fixture.ZERO_TEXT,
        ),
    )
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "read-cwd",
                    fixture.INPUT_FIELD: (
                        "const r = await tools.mcp__node_repl__js({"
                        'title:"Read README.md",code:`var content = await fs.readFile('
                        'nodeRepl.cwd + "/README.md", "utf8"); nodeRepl.write(content);`});'
                        'for (const c of r.content) if (c.type === "text") text(c.text);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-cwd-start",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "read-cwd",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\n# Guide\nBody\n",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-cwd-result",
        ),
    )

    accessed = payloads(answered, event_resource.FileAccessed)[0].payload
    assert accessed.path == "/work/README.md"
    assert text_of(accessed.content) == "# Guide\nBody\n"
