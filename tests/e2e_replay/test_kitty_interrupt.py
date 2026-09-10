# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay a Kitty interrupt followed by a task notification and another marker."""

from domain.entries import EntryTypeName
from domain.ids import HarnessName, SessionId
from tests import http_test_assets, http_test_controls
from tests.e2e_replay.audit_replay_support import replay

FINISHED_TURNS = 2


# Harness limit: claude_code only. These markers are from Claude's transcript.
def test_kitty_interrupt_does_not_leave_magenta() -> None:
    """Close the notification turn and keep the marker as system text."""
    application = replay("audit_claude_kitty_interrupt.jsonl", HarnessName.CLAUDE_CODE, "transcript")
    state = application.session_data.read(SessionId("session-one"))
    assert state is not None
    assert state.actors[0].status == "awaiting_response"
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert '"status":"awaiting_response"' in response.body.raw.decode()
    entries = application.session_data.entries_page(SessionId("session-one"), limit=100).entries
    assert [entry.entry_type for entry in entries].count(EntryTypeName.TURN_FINISHED) == FINISHED_TURNS
