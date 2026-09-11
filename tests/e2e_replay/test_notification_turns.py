# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay a background notification that left Claude marked as working."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from domain.entries import EntryTypeName
from domain.ids import HarnessName, SessionId
from engine.interpret.loop import Interpreter
from harness.models.raw_events import RawEvent
from tests import http_test_assets, http_test_controls
from tests.plugin_tests.support_events import raw_event
from tests.provider_graph import ProviderGraph

# Harness limit: claude_code only. These are Claude task-notification records.
CLAUDE_CODE_HARNESS = HarnessName("claude_code")


@pytest.mark.parametrize("stop_first", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_notification_response_finishes(*, stop_first: bool, restart: bool) -> None:
    """Finish the new response, with either Stop arrival order."""
    application = _replay(stop_first=stop_first, restart=restart)
    _assert_status(application)
    entries = application.session_data.entries_page(SessionId("session-one"), limit=100).entries
    finished = [entry for entry in entries if entry.entry_type == EntryTypeName.TURN_FINISHED]
    finished = [entry for entry in finished if entry.turn_id]
    assert [entry.turn_id for entry in finished] == ["prompt-one", "notification-one"]
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert '"status":"awaiting_response"' in response.body.raw.decode()
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert "The job passed." in response.body.raw.decode()


def _replay(*, stop_first: bool, restart: bool) -> ProviderGraph:
    application = ProviderGraph()
    for record in _records():
        if restart and b'"uuid": "answer-two"' in record.payload:
            application = ProviderGraph()
        _apply_record(application, record, stop_first=stop_first)
    return application


def _apply_record(application: ProviderGraph, record: RawEvent, *, stop_first: bool) -> None:
    is_answer = b'"uuid": "answer-' in record.payload
    if stop_first and is_answer:
        _stop(application, str(record.raw_event_id))
    application.raw_events.record((record,))
    application.provider("interpreter", Interpreter).translation.translate()
    application.reaction_loop.tick()
    if is_answer and not stop_first:
        _stop(application, str(record.raw_event_id))


def _records() -> Iterator[RawEvent]:
    path = Path(__file__).parents[1] / "e2e" / "fixtures" / "audit_claude_notification_turns.jsonl"
    with path.open("rb") as source:
        while line := source.readline():
            yield replace(raw_event(
                json.loads(line), harness=CLAUDE_CODE_HARNESS,
                source_type="transcript", raw_event_id=str(source.tell()),
                source_position=str(source.tell() - len(line)),
            ), source_name=str(path))


def _stop(application: ProviderGraph, identity: str) -> None:
    application.raw_events.record((raw_event(
        {"hook_event_name": "Stop", "hook_event_id": identity},
        harness=CLAUDE_CODE_HARNESS, source_type="hook", raw_event_id=f"stop-{identity}",
    ),))
    application.provider("interpreter", Interpreter).translation.translate()
    application.reaction_loop.tick()


def _assert_status(application: ProviderGraph) -> None:
    state = application.session_data.read(SessionId("session-one"))
    assert state is not None
    assert state.actors[0].status == "awaiting_response"
