# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay a prompt that Claude Code queued and then delivered."""

import json
from dataclasses import replace
from http import HTTPStatus

import pytest

from domain.ids import SessionId
from harness.models.raw_events import RawEvent
from tests import http_test_assets, http_test_controls, http_test_pane_models
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests.support_events import raw_event

QUEUED_TEXT = "Check the result"
REQUEST_ID = "c0660e5a-6ddf-4b92-b7d2-9c432e74d2d8"


# Harness limit: claude_code only. These deliveries are from Claude Code transcripts.
@pytest.mark.parametrize(
    "fixture",
    [
        # Claude Code 2.1.284 starts the queued prompt as the next turn.
        "audit_claude_queued_prompt.jsonl",
        # Claude Code 2.1.293 gives the queued prompt to the running turn.
        "audit_claude_absorbed_prompt.jsonl",
    ],
)
def test_queued_prompt_leaves_the_queue(fixture: str) -> None:
    """Remove a queued prompt when Claude Code delivers it."""
    application = replay(fixture, CLAUDE_CODE_HARNESS, "transcript", before=(queued_message(),))
    for audit in http_test_pane_models.raw_event_audits(application).audits_for_session(SessionId("session-one")):
        assert audit.interpretation is not None
        assert audit.interpretation.decision != "translation_failed"
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/api/sessions/session-one/application")
        assert response.status == HTTPStatus.OK
        assert json.loads(response.body.raw)["composer"]["queue"] is None
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert QUEUED_TEXT in response.body.raw.decode()


def queued_message() -> RawEvent:
    """Build the control evidence that Claude Code queued the prompt.

    Returns:
        The raw event that puts the prompt in the session queue.

    """
    return replace(
        raw_event(
            {"request_id": REQUEST_ID, "text": QUEUED_TEXT},
            harness=CLAUDE_CODE_HARNESS,
            source_type="control",
            raw_event_id="audit-message-queued",
            source_position=REQUEST_ID,
        ),
        source_name="message_queued",
        encoding="json",
    )
