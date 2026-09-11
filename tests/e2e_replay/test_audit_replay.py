# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay recorded failure shapes through storage, translation, and HTTP."""

from __future__ import annotations

from http import HTTPStatus

import pytest

from domain.entries import EntryTypeName
from domain.ids import SessionId
from engine.interpret.loop import Interpreter
from repository.impl.sqlite.audit_read import SqliteAuditReadRepository
from tests import http_test_assets, http_test_controls
from tests.e2e_replay.audit_replay_support import command_inputs
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests.support_events import raw_event
from tests.provider_graph import ProviderGraph

SESSION_ID = SessionId("session-one")
FINISHED_COMMAND_COUNT = 2
RESTART_INDEX = 2


@pytest.fixture(params=["audit_command_batch.jsonl", "audit_mixed_command_batch.jsonl"])
def batch_filename(request: pytest.FixtureRequest) -> str:
    """Select a command fixture.

    Returns:
        The fixture file name.

    """
    return str(request.param)


@pytest.fixture(params=[False, True], ids=["continuous", "restart"])
def replayed_application(request: pytest.FixtureRequest, batch_filename: str) -> ProviderGraph:
    """Read the recorded command sequence into a separate application.

    Returns:
        The application after both event loops process the input.

    """
    application = ProviderGraph()
    for index, record in enumerate(command_inputs(batch_filename)):
        if request.param and index == RESTART_INDEX:
            application = ProviderGraph()
        application.raw_events.record((record,))
        application.provider("interpreter", Interpreter).tick()
        application.reaction_loop.tick()
    return application


# Harness limit: codex only. The audit contains a Codex command batch.
def test_command_batch_keeps_process_results(replayed_application: ProviderGraph) -> None:
    """Keep each command result and resolve a later process continuation."""
    entries = replayed_application.session_data.entries_page(SESSION_ID, limit=100).entries
    assert [entry.entry_type for entry in entries].count(EntryTypeName.SHELL_FINISHED) == FINISHED_COMMAND_COUNT
    assert not replayed_application.provider("audit_reads", SqliteAuditReadRepository).errors_for_session(SESSION_ID)
    with http_test_assets.running_server(replayed_application) as server:
        status, _, body = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert status == HTTPStatus.OK
        assert "ready" in body.raw.decode()
        assert "done" in body.raw.decode()


# Harness limit: claude_code only. The audit contains a Claude output chunk.
def test_claude_newline_has_no_http_error() -> None:
    """Keep a recorded newline without a false empty-body error."""
    application = ProviderGraph()
    application.raw_events.record((raw_event(
        {"content_base64": "Cg==", "shell_id": "newline-shell", "ordinal": 211, "stream": "output"},
        harness=CLAUDE_CODE_HARNESS,
        source_type="foreground_output",
        raw_event_id="audit-newline",
    ),))
    application.provider("interpreter", Interpreter).tick()
    application.reaction_loop.tick()
    assert not application.provider("audit_reads", SqliteAuditReadRepository).errors_for_session(SESSION_ID)
    with http_test_assets.running_server(application) as server:
        status, _, body = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert status == HTTPStatus.OK
        assert "shell_output" in body.raw.decode()
        assert r"\n" in body.raw.decode()
