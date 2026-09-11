# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex interruption tests."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from domain import (
    event_resource,
    event_shell,
    outcomes,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.impl.codex.controls.controller_rollout import rollout_abort_state
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from pathlib import Path


def test_codex_unmapped_tool_is_unknown_evidence() -> None:
    """Verify codex unmapped tool is unknown evidence not a failure.

    An unmapped tool is a hole in this translator, not bad evidence: the
        verdict says `ignored_unknown` so the audit can name it, and the rest of
        the session carries on.
    """
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "unknown-one",
                    fixture.INPUT_FIELD: "const result = await tools.unknown_tool({}); text(result);",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="unknown-tool",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_UNKNOWN
    assert "unmapped Codex tool" in (translation.reason or "")


def test_codex_deferred_tool_wait_is_known() -> None:
    """Verify codex deferred tool wait is known nonsemantic orchestration."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: "wait",
                    fixture.CALL_ID_FIELD: "wait-one",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            "cell_id": fixture.ONE_TEXT,
                            "yield_time_ms": 30000,
                            "max_tokens": 2000,
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="deferred-wait",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_interrupt_detects_queued_turn(tmp_path: Path) -> None:
    """Verify codex interrupt detects a queued turn after abort."""
    rollout_path = tmp_path / fixture.ROLLOUT_JSONL_PATH
    rollout_path.write_text(
        json.dumps({
            fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
            fixture.PAYLOAD_FIELD: {fixture.TYPE_FIELD: fixture.TURN_ABORTED_ID},
        })
        + "\n"
        + json.dumps(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
                    fixture.TURN_ID_FIELD: "turn-two",
                },
            },
        )
        + "\n",
    )

    assert rollout_abort_state(str(rollout_path), 0) == (True, True)


def test_codex_abort_cancels_its_unfinished_exec() -> None:
    """Verify codex abort cancels its unfinished exec."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.SLEEP_ONE,
                    fixture.INPUT_FIELD: 'tools.exec_command({cmd:"sleep 60"})',
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="sleep-started",
            source_position=fixture.TEN_TEXT,
        ),
    )
    aborted = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TURN_ABORTED_ID,
                    fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    fixture.REASON_FIELD: fixture.INTERRUPTED,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="turn-aborted",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )

    started_shell = payloads(started, event_shell.ShellStarted)[0].payload.shell_id
    finished = payloads(aborted, event_shell.ShellFinished)[0].payload
    assert finished.shell_id == started_shell
    assert finished.outcome == outcomes.Outcome.CANCELLED


def test_codex_abort_cancels_exec_that_yielded() -> None:
    """Verify codex abort cancels an exec that yielded before the abort."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.SLEEP_ONE,
                    fixture.INPUT_FIELD: ('tools.exec_command({cmd:"sleep 60",yield_time_ms:1000})'),
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="sleep-started",
            source_position=fixture.TEN_TEXT,
        ),
    )
    yielded = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.SLEEP_ONE,
                    fixture.OUTPUT_FIELD: "",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="sleep-yielded",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )
    aborted = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TURN_ABORTED_ID,
                    fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    fixture.REASON_FIELD: fixture.INTERRUPTED,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="turn-aborted",
            source_position=fixture.TWELVE_TEXT,
        ),
    )

    started_shell = payloads(started, event_shell.ShellStarted)[0].payload.shell_id
    assert payloads(yielded, event_shell.ShellBackgrounded)[0].payload.shell_id == started_shell
    assert payloads(aborted, event_shell.ShellFinished)[0].payload == event_shell.ShellFinished(
        started_shell,
        outcomes.Outcome.CANCELLED,
        None,
        None,
    )
    assert payloads(aborted, event_shell.ShellOutputFinished)[0].payload == event_shell.ShellOutputFinished(
        started_shell,
        outcomes.Outcome.CANCELLED,
    )


def test_codex_abort_cancels_its_unfinished_skill() -> None:
    """Verify codex abort cancels its unfinished skill load."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.SKILL_ONE,
                    fixture.INPUT_FIELD: ('tools.exec_command({cmd:"cat /work/.agents/skills/audit-skill/SKILL.md"})'),
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="skill-started",
            source_position=fixture.TEN_TEXT,
        ),
    )
    aborted = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.TURN_ABORTED_ID,
                    fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    fixture.REASON_FIELD: fixture.INTERRUPTED,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="turn-aborted",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )

    started_skill = payloads(started, event_resource.SkillStarted)[0].payload.skill_id
    finished = payloads(aborted, event_resource.SkillFinished)[0].payload
    assert finished.skill_id == started_skill
    assert finished.outcome == outcomes.Outcome.CANCELLED


def test_codex_interrupted_exec_wrapper_cancels() -> None:
    """Verify codex interrupted exec wrapper cancels the command."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.SLEEP_ONE,
                    fixture.INPUT_FIELD: 'tools.exec_command({cmd:"sleep 60"})',
                    fixture.CHAT_METADATA_PASSTHROUGH_KIND: {
                        fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="sleep-started",
            source_position=fixture.TEN_TEXT,
        ),
    )
    interrupted = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: fixture.SLEEP_ONE,
                    fixture.OUTPUT_FIELD: "aborted by user after 1.1s",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="sleep-interrupted",
            source_position=fixture.ELEVEN_TEXT,
        ),
    )

    started_shell = payloads(started, event_shell.ShellStarted)[0].payload.shell_id
    finished = payloads(interrupted, event_shell.ShellFinished)[0].payload
    assert finished.shell_id == started_shell
    assert finished.outcome == outcomes.Outcome.CANCELLED
    assert payloads(interrupted, event_shell.ShellBackgrounded) == []
    assert (
        payloads(
            translator.translate(
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
                                fixture.AGGREGATED_OUTPUT_ID: "should-not-replace-cancellation\n",
                                fixture.EXIT_CODE: 0,
                            },
                        },
                    },
                    harness=CODEX_HARNESS,
                    source_type=fixture.ROLLOUT_SOURCE,
                    raw_event_id="late-process-completion",
                    source_position=fixture.TWELVE_TEXT,
                ),
            ),
            event_shell.ShellFinished,
        )
        == []
    )
