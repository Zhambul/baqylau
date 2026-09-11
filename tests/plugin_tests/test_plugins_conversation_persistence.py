# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude persisted conversation translation tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from domain import (
    event_actor as actor_events,
    event_conversation as conversation_events,
    event_shell as shell_events,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from pathlib import Path


def test_claude_resumed_child_assignment(tmp_path: Path) -> None:
    """Verify claude resumed child assignment correlation survives restart."""
    launch_result = (
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "agent-result",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: fixture.AGENT_TOOL_ONE_ID,
                            fixture.CONTENT_FIELD: "Async agent launched successfully.",
                        },
                    ],
                },
                fixture.TOOL_USE_RESULT: {
                    "isAsync": True,
                    fixture.STATUS_FIELD: "async_launched",
                    "agentId": fixture.CHILD_ONE_ID,
                },
            },
        )
        + "\n"
    )
    notification_document: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.USER,
        fixture.UUID_FIELD: fixture.RESUMED_TASK_NOTIFICATION_ID,
        fixture.ORIGIN_FIELD: {fixture.KIND_FIELD: fixture.TASK_NOTIFICATION_ID},
        fixture.MESSAGE_FIELD: {
            fixture.CONTENT_FIELD: (
                "<task-notification><task-id>child-one</task-id>"
                "<tool-use-id>send-message-one</tool-use-id>"
                "<status>completed</status>"
                '<summary>Agent "Wait for follow-up" finished</summary>'
                "<result>FOLLOWUP_MARKER_417</result></task-notification>"
            ),
        },
    }
    transcript_path = tmp_path / "parent.jsonl"
    transcript_path.write_text("".join((launch_result, json.dumps(notification_document), "\n")))
    notification_raw = replace(
        raw_event(
            notification_document,
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.RESUMED_TASK_NOTIFICATION_ID,
            source_position=str(len(launch_result.encode())),
        ),
        source_name=str(transcript_path),
    )

    finished = payloads(
        ClaudeCanonicalTranslator().translate(notification_raw),
        actor_events.ActorAssignmentFinished,
    )[0]
    assert finished.payload.assignment_id == fixture.AGENT_TOOL_ONE_ID
    assert text_of(finished.payload.result) == "FOLLOWUP_MARKER_417"


def test_claude_fg_shell_completion_survives(tmp_path: Path) -> None:
    """Verify claude foreground shell completion survives translator restart."""
    call: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.ASSISTANT,
        fixture.UUID_FIELD: "assistant-before-restart",
        fixture.MESSAGE_FIELD: {
            fixture.CONTENT_FIELD: [
                {
                    fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                    fixture.ID_FIELD: fixture.SHELL_BEFORE_RESTART_ID,
                    fixture.NAME_FIELD: fixture.BASH_TOOL,
                    fixture.INPUT_FIELD: {fixture.COMMAND_FIELD: "python -c 'pass'"},
                },
            ],
        },
    }
    result: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.USER,
        fixture.UUID_FIELD: "result-after-restart",
        fixture.MESSAGE_FIELD: {
            fixture.CONTENT_FIELD: [
                {
                    fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                    fixture.TOOL_USE_ID_FIELD: fixture.SHELL_BEFORE_RESTART_ID,
                    fixture.CONTENT_FIELD: "",
                },
            ],
        },
    }
    source = tmp_path / "claude-restart.jsonl"
    source.write_text(
        f"{json.dumps(call)}\n{json.dumps(result)}\n",
        encoding=fixture.TEXT_ENCODING,
    )

    started = ClaudeCanonicalTranslator().translate(
        replace(
            raw_event(
                call,
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id="claude-call-before-restart",
                source_position=fixture.ZERO_TEXT,
            ),
            source_name=str(source),
        ),
    )
    finished = ClaudeCanonicalTranslator().translate(
        replace(
            raw_event(
                result,
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id="claude-result-after-restart",
                source_position=str(len(f"{json.dumps(call)}\n".encode())),
            ),
            source_name=str(source),
        ),
    )

    assert (
        payloads(finished, shell_events.ShellFinished)[0].payload.shell_id
        == payloads(
            started,
            shell_events.ShellStarted,
        )[0].payload.shell_id
    )


def test_claude_turn_completion_survives(tmp_path: Path) -> None:
    """Verify claude turn completion survives translator restart."""
    documents: tuple[dict[str, JsonValue], ...] = (
        {
            fixture.TYPE_FIELD: fixture.USER,
            fixture.UUID_FIELD: fixture.PROMPT_BEFORE_RESTART_ID,
            fixture.MESSAGE_FIELD: {
                fixture.ROLE_FIELD: fixture.USER,
                fixture.CONTENT_FIELD: "Run one command",
            },
        },
        {
            fixture.TYPE_FIELD: fixture.ASSISTANT,
            fixture.UUID_FIELD: fixture.CALL_BEFORE_RESTART_ID,
            fixture.PARENT_UUID: fixture.PROMPT_BEFORE_RESTART_ID,
            fixture.MESSAGE_FIELD: {
                fixture.ROLE_FIELD: fixture.ASSISTANT,
                fixture.CONTENT_FIELD: [
                    {
                        fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                        fixture.ID_FIELD: fixture.SHELL_BEFORE_RESTART_ID,
                        fixture.NAME_FIELD: fixture.BASH_TOOL,
                        fixture.INPUT_FIELD: {fixture.COMMAND_FIELD: "python -c 'pass'"},
                    },
                ],
                fixture.STOP_REASON_FIELD: fixture.TOOL_USE_ID,
            },
        },
        {
            fixture.TYPE_FIELD: fixture.USER,
            fixture.UUID_FIELD: "result-after-restart",
            fixture.PARENT_UUID: fixture.CALL_BEFORE_RESTART_ID,
            fixture.MESSAGE_FIELD: {
                fixture.ROLE_FIELD: fixture.USER,
                fixture.CONTENT_FIELD: [
                    {
                        fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                        fixture.TOOL_USE_ID_FIELD: fixture.SHELL_BEFORE_RESTART_ID,
                        fixture.CONTENT_FIELD: "",
                    },
                ],
            },
        },
        {
            fixture.TYPE_FIELD: fixture.ASSISTANT,
            fixture.UUID_FIELD: "answer-after-restart",
            fixture.PARENT_UUID: "result-after-restart",
            fixture.MESSAGE_FIELD: {
                fixture.ID_FIELD: "answer-after-restart",
                fixture.ROLE_FIELD: fixture.ASSISTANT,
                fixture.CONTENT_FIELD: [{fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "done"}],
                fixture.STOP_REASON_FIELD: fixture.END_TURN_ID,
            },
        },
    )
    lines = tuple(f"{json.dumps(document)}\n" for document in documents)
    source = tmp_path / "claude-turn-restart.jsonl"
    source.write_text("".join(lines), encoding=fixture.TEXT_ENCODING)
    finished = ClaudeCanonicalTranslator().translate(
        replace(
            raw_event(
                documents[-1],
                harness=CLAUDE_CODE_HARNESS,
                source_type=fixture.TRANSCRIPT_SOURCE,
                raw_event_id="claude-answer-after-restart",
                source_position=str(
                    sum(len(line.encode()) for line in lines[:-1]),
                ),
            ),
            source_name=str(source),
        ),
    )

    final_message = payloads(finished, conversation_events.MessageCreated)[0]
    assert final_message.turn_id == domain_ids.TurnId(fixture.PROMPT_BEFORE_RESTART_ID)
    assert payloads(finished, conversation_events.TurnFinished)[0].turn_id == domain_ids.TurnId(
        fixture.PROMPT_BEFORE_RESTART_ID,
    )
    assert (
        payloads(
            finished,
            conversation_events.TurnFinished,
        )[0].payload.final_message_id
        == final_message.payload.message_id
    )
