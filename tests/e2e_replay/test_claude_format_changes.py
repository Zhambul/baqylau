# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay Claude format changes from the September audit."""

import json
from http import HTTPStatus
from pathlib import Path

from domain.event_conversation import TurnFinished
from domain.ids import SessionId
from engine.interpret.loop import Interpreter
from tests import http_test_assets, http_test_controls, http_test_pane_models, http_test_preferences
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.provider_graph import ProviderGraph


# Harness limit: claude_code only. These fields are from Claude records.
def test_claude_format_changes_reach_http() -> None:
    """Accept rendered attachments and text error details."""
    application = replay("audit_claude_format_changes.jsonl", CLAUDE_CODE_HARNESS, "transcript")
    for audit in http_test_pane_models.raw_event_audits(application).audits_for_session(SessionId("session-one")):
        assert audit.interpretation is not None
        assert audit.interpretation.decision != "translation_failed"
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert response.status == HTTPStatus.OK
        assert "Check the job" in response.body.raw.decode()
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert "You have reached your limit." in response.body.raw.decode()


def test_rate_limit_hook_is_accepted(tmp_path: Path) -> None:
    """Keep the failed-turn event when StopFailure has text error details."""
    application = ProviderGraph()
    payload = json.dumps({
        "hook_event_name": "StopFailure", "hook_event_id": "rate-limit-hook",
        "session_id": "session-one", "transcript_path": str(tmp_path / "session.jsonl"),
        "cwd": str(tmp_path), "error": "rate_limit", "error_details": "429 rate limit",
    }).encode()
    with http_test_assets.running_server(application) as server:
        status, _ = http_test_preferences.post_hook(server, "claude_code", payload)
        assert status == HTTPStatus.OK
        application.provider("interpreter", Interpreter).translation.translate()
        application.reaction_loop.tick()
        _assert_failed_turn(application)
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert '"state":"finished"' in response.body.raw.decode()


def _assert_failed_turn(application: ProviderGraph) -> None:
    finished = [
        event.payload for event in application.canonical_events.page_from(0, 100)
        if isinstance(event.payload, TurnFinished)
    ]
    assert len(finished) == 1
    assert finished[0].outcome == "failed"
