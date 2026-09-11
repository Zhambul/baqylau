# Copyright (c) 2026 Zhambyl Yermagambet
"""Background hook lifecycle tests."""

import json
from pathlib import Path

import pytest

from domain.event_shell import (
    ShellOutputFinished,
)
from domain.ids import (
    ShellId,
)
from domain.outcomes import Outcome
from harness.impl.claude_code.canonical.records import HookPayload
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.hooks import foreground as claude_foreground
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import support_events, support_terminal, vocabulary as fixture
from tests.plugin_tests.hook_common_support import receive_claude_hook


def test_claude_fg_post_tool_records_no_directive() -> None:
    """Verify claude foreground post tool records no directive.

    The foreground following ends with the committed operation.finished fact,
        not with a directive from the PostToolUse delivery.
    """
    document = {
        fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
        fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
        fixture.CWD_FIELD: fixture.WORK_PATH,
        fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
        fixture.HOOK_EVENT_ID_FIELD: "posttool-one",
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_USE_ID_FIELD: fixture.COMMAND_ONE,
        fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "echo hello"},
        fixture.TOOL_RESPONSE_FIELD: {fixture.STDOUT: fixture.HELLO},
    }

    response = receive_claude_hook(document)

    assert response.reply == b""
    assert [event.source_type for event in response.raw_events] == [fixture.HOOK_SOURCE]


def test_claude_bg_bash_locates_its_native_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude background bash locates its native output file."""
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", tmp_path.as_posix())
    output_path, document = support_terminal.background_post_tool_document(tmp_path)

    response = receive_claude_hook(document)

    directive = response.raw_events[-1]
    assert (response.reply, directive.source_type) == (b"", fixture.OUTPUT_LOCATION_ID)
    body = json.loads(directive.payload)
    assert body["shell_id"] == fixture.BACKGROUND_OP_ONE
    assert body[fixture.SOURCE_PATH_FIELD] == str(output_path.resolve())
    assert body["delete_source"] is False
    # a background launch reports "finished" while output keeps flowing, so the
    # following must outlive the operation and end with the session
    assert body[fixture.UNTIL] == fixture.SESSION_FINISHED_ID


def test_claude_bg_output_requires_native_task(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude background output requires the native task evidence."""
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path))
    _output_path, document = support_terminal.background_post_tool_document(tmp_path)

    foreground_document = json.loads(json.dumps(document))
    foreground_document[fixture.TOOL_INPUT_FIELD].pop(fixture.RUN_IN_BACKGROUND_FIELD)
    assert claude_foreground.background_output(HookPayload.model_validate(foreground_document)) is None

    missing_task = json.loads(json.dumps(document))
    missing_task[fixture.TOOL_RESPONSE_FIELD].pop(fixture.BACKGROUND_TASK_ID_FIELD)
    assert claude_foreground.background_output(HookPayload.model_validate(missing_task)) is None

    no_file_yet = json.loads(json.dumps(document))
    no_file_yet[fixture.TOOL_RESPONSE_FIELD][fixture.BACKGROUND_TASK_ID_FIELD] = "btk-without-a-file"
    assert claude_foreground.background_output(HookPayload.model_validate(no_file_yet)) is None


def test_claude_task_stop_cancels_bg_shell_output() -> None:
    """Verify claude task stop cancels the background shell output."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        support_events.raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_USE_ID_FIELD: fixture.BACKGROUND_OP_ONE,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.COMMAND_FIELD: "sleep 120",
                    fixture.RUN_IN_BACKGROUND_FIELD: True,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="background-start",
        ),
    )
    translator.translate(
        support_events.raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_USE_ID_FIELD: fixture.BACKGROUND_OP_ONE,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.COMMAND_FIELD: "sleep 120",
                    fixture.RUN_IN_BACKGROUND_FIELD: True,
                },
                fixture.TOOL_RESPONSE_FIELD: {fixture.BACKGROUND_TASK_ID_FIELD: fixture.NATIVE_TASK_ONE_ID},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="background-launched",
        ),
    )

    stopped = translator.translate(
        support_events.raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_NAME_FIELD: "TaskStop",
                fixture.TOOL_USE_ID_FIELD: "stop-one",
                fixture.TOOL_INPUT_FIELD: {fixture.TASK_ID: fixture.NATIVE_TASK_ONE_ID},
                fixture.TOOL_RESPONSE_FIELD: {
                    fixture.MESSAGE_FIELD: "Successfully stopped task: native-task-one",
                    fixture.TASK_ID: fixture.NATIVE_TASK_ONE_ID,
                    "task_type": "local_bash",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="background-stopped",
        ),
    )

    finished = support_events.payloads(stopped, ShellOutputFinished)
    assert len(finished) == 1
    assert finished[0].payload.shell_id == ShellId(fixture.BACKGROUND_OP_ONE)
    assert finished[0].payload.outcome == Outcome.CANCELLED


def test_claude_task_stop_recovers_its_bg_shell(tmp_path: Path) -> None:
    """Verify claude task stop recovers its background shell after restart."""
    launch_result = {
        fixture.TYPE_FIELD: fixture.USER,
        fixture.MESSAGE_FIELD: {
            fixture.CONTENT_FIELD: [
                {
                    fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                    fixture.TOOL_USE_ID_FIELD: "background-op-before-restart",
                    fixture.CONTENT_FIELD: "Command running in background with ID: native-task-one",
                },
            ],
        },
        fixture.TOOL_USE_RESULT: {fixture.BACKGROUND_TASK_ID_FIELD: fixture.NATIVE_TASK_ONE_ID},
    }
    transcript_path = tmp_path / fixture.SESSION_JSONL_PATH
    transcript_path.write_text(f"{json.dumps(launch_result)}\n", encoding=fixture.TEXT_ENCODING)

    stopped = ClaudeCanonicalTranslator().translate(
        support_events.raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TRANSCRIPT_PATH: str(transcript_path),
                fixture.TOOL_NAME_FIELD: "TaskStop",
                fixture.TOOL_USE_ID_FIELD: "stop-after-restart",
                fixture.TOOL_INPUT_FIELD: {fixture.TASK_ID: fixture.NATIVE_TASK_ONE_ID},
                fixture.TOOL_RESPONSE_FIELD: {fixture.TASK_ID: fixture.NATIVE_TASK_ONE_ID},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="background-stopped-after-restart",
        ),
    )

    finished = support_events.payloads(stopped, ShellOutputFinished)
    assert len(finished) == 1
    assert finished[0].payload.shell_id == ShellId("background-op-before-restart")
    assert finished[0].payload.outcome == Outcome.CANCELLED
