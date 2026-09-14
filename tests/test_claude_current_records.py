# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the Claude fields observed in the main daemon."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from api.controls.models.launch_session_request import LaunchSessionRequest
from domain.ids import WindowId
from harness.impl.claude_code.canonical import record_tool_response
from harness.impl.claude_code.controls import controller_send, controller_send_operations
from harness.impl.claude_code.hooks.gateway import ClaudeHookGateway
from harness.impl.claude_code.model import ClaudeCodeModel
from harness.models import controls, hooks
from harness.services.terminal_driver import TerminalDriver


def test_working_directory_hook_fields() -> None:
    """Keep both paths from a working-directory change."""
    record = record_tool_response.HookPayload.model_validate({"old_cwd": "/work", "new_cwd": "/work/next"})
    assert record.old_cwd == "/work"
    assert record.new_cwd == "/work/next"


def test_instruction_hook_parent_path() -> None:
    """Accept the parent path without losing the hook's original bytes."""
    payload = json.dumps({
        "hook_event_name": "InstructionsLoaded",
        "session_id": "instruction-session",
        "transcript_path": "/work/session.jsonl",
        "file_path": "/work/rules.md",
        "parent_file_path": "/work/CLAUDE.md",
    }).encode()
    response = ClaudeHookGateway().receive_hook(hooks.HarnessHookRequest(payload, None, None, None, None))
    assert response.raw_events[0].payload == payload
    record = record_tool_response.HookPayload.model_validate_json(response.raw_events[0].payload)
    assert record.parent_file_path == "/work/CLAUDE.md"


def test_fable_five_one_model_is_accepted() -> None:
    """Accept the model name in current assistant records."""
    assert ClaudeCodeModel("claude-fable-5-1") == ClaudeCodeModel.CLAUDE_FABLE_FIVE_ONE


def test_first_send_can_create_the_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read from the start when a fresh session has no transcript yet."""
    path = str(tmp_path / "new-session.jsonl")
    context = Mock(spec=controls.ControlContext, session=Mock(source_reference=path))
    monkeypatch.setattr(controller_send_operations, "native_text_state", Mock(return_value=None))
    submit = Mock(return_value=True)
    confirmed = Mock(return_value="sent")
    monkeypatch.setattr(controller_send, "_type_native_text", submit)
    monkeypatch.setattr(controller_send, "_wait_for_native_text_state", confirmed)
    outcome = controller_send.deliver_native_text(
        context,
        Mock(spec=TerminalDriver),
        WindowId("1"),
        "hello",
    )
    assert outcome == ("sent", None)
    confirmed.assert_called_once_with(path, 0, "hello")
    submit.assert_called_once()


def test_launch_resolves_a_directory_alias(tmp_path: Path) -> None:
    """Use the same path that the native session reports."""
    target = tmp_path / "project"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    request = LaunchSessionRequest(harness="claude_code", working_directory=str(alias)).request()
    assert request.working_directory == str(target.resolve())
