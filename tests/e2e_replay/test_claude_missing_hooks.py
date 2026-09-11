# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay Claude records when a hook delivery is missing."""

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from domain.ids import SessionId
from engine.interpret.loop import Interpreter
from tests import http_test_assets, http_test_controls, provider_graph
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests.support_events import raw_event


# Harness limit: claude_code only. Claude can record an answer without its result hook.
def test_transcript_answer_clears_red_status() -> None:
    """Read the answer through the application and its HTTP view."""
    application = replay("audit_claude_question_answer.jsonl", CLAUDE_CODE_HARNESS, "transcript")
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert '"awaiting_attention"' not in response.body.raw.decode()
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert '"labels":["Own"]' in response.body.raw.decode()
    entries = application.session_data.entries_page(SessionId("session-one"), limit=100).entries
    assert any(entry.entry_type == "question_answered" for entry in entries)


# Harness limit: claude_code only. Later Claude hooks carry the missing session details.
@pytest.mark.parametrize("hook_name", ["UserPromptSubmit", "PreToolUse"])
def test_later_hook_creates_missing_session(
    tmp_path: Path,
    hook_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create a visible session without a SessionStart hook."""
    monkeypatch.setattr("engine.interpret.liveness.process_alive", Mock(return_value=True))
    (tmp_path / "session.jsonl").write_text(
        '{"type":"user","uuid":"prompt-one",'
        '"message":{"role":"user","content":"Investigate the thread."}}\n',
    )
    application = provider_graph.ProviderGraph()
    application.raw_events.record((replace(raw_event(
        {
            "hook_event_name": hook_name,
            "session_id": "session-one",
            "transcript_path": str(tmp_path / "session.jsonl"),
            "cwd": str(tmp_path),
            "tool_name": "Read",
            "tool_use_id": "read-one",
            "tool_input": {"file_path": str(tmp_path / "notes.md")},
        },
        harness=CLAUDE_CODE_HARNESS,
        source_type="hook",
        raw_event_id="later-hook",
    ), harness_process_id=os.getpid()),))
    application.provider("interpreter", Interpreter).tick()
    application.provider("interpreter", Interpreter).tick()
    application.reaction_loop.tick()
    assert application.session_data.read(SessionId("session-one")) is not None
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert '"session_id":"session-one"' in response.body.raw.decode()
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert "Investigate the thread." in response.body.raw.decode()
