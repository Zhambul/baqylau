# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay a background command that a hook rejects before it starts."""

from domain.ids import SessionId
from tests import http_test_assets, http_test_controls
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CLAUDE_CODE_HARNESS


# Harness limit: claude_code only. This is a Claude rejected background command.
def test_rejected_background_does_not_stay_blue() -> None:
    """Close a background command when no background job starts."""
    application = replay(
        "audit_claude_rejected_background.jsonl",
        CLAUDE_CODE_HARNESS,
        "transcript",
    )
    state = application.session_data.read(SessionId("session-one"))
    assert state is not None
    actor = state.actors[0]
    assert not actor.background.running_shell_ids
    assert actor.status == "awaiting_response"
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert '"status":"awaiting_response"' in response.body.raw.decode()
