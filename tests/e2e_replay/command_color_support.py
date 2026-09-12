# Copyright (c) 2026 Zhambyl Yermagambet
"""Observe tab writes and HTTP state from the same replay."""

import json
from dataclasses import replace

from domain.entries import EntryTypeName
from domain.ids import SessionId
from harness.models.raw_events import RawEvent
from terminal.models.values import RGB
from terminal.tabs import TabColorPainter
from tests import canonical_sessiondata_paint_support as paint_support, http_test_assets, http_test_controls
from tests.provider_graph import ProviderGraph

SESSION = SessionId("session-one")
FINISHED_COMMANDS = 2


def poll_result(raw_event: RawEvent, exit_code: int, output: str) -> RawEvent:
    """Set the final command result for a replay case.

    Returns:
        The record with the selected process outcome.

    """
    document = json.loads(raw_event.payload)
    block = document["payload"]["output"][1]
    block["text"] = json.dumps({"exit_code": exit_code, "output": output})
    return replace(raw_event, payload=json.dumps(document).encode())


def record_colors(provider_graph: ProviderGraph) -> paint_support.RecordingTabs:
    """Attach the real color listener to a recording terminal.

    Returns:
        The terminal color requests.

    """
    tabs = paint_support.RecordingTabs()
    loop = provider_graph.reaction_loop
    loop.dependencies = replace(
        loop.dependencies,
        listeners=(*loop.dependencies.listeners, TabColorPainter(tabs, provider_graph.sessions)),
    )
    return tabs


def check_color(
    provider_graph: ProviderGraph, recording_tabs: paint_support.RecordingTabs, status: str, color: str,
) -> None:
    """Check committed actor state, the terminal request, and the HTTP response."""
    state = provider_graph.session_data.read(SESSION)
    assert state is not None
    assert state.actors[0].status == status
    assert recording_tabs.painted[-1][1] == RGB.from_hex(color)
    if status == "awaiting_response":
        assert sum(
            entry.entry_type == EntryTypeName.SHELL_FINISHED
            for entry in provider_graph.session_data.entries_page(SESSION, limit=100).entries
        ) == FINISHED_COMMANDS
    with http_test_assets.running_server(provider_graph) as server:
        assert status in http_test_controls.get(server, f"/sessionData/{SESSION}").body.raw.decode()
