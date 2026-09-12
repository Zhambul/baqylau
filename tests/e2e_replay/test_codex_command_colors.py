# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep Codex tab colors current without reading known command history again."""

from unittest.mock import Mock

import pytest

from engine.interpret.loop import Interpreter
from harness.impl.codex.canonical import translator_recovery
from tests.e2e_replay import command_color_support
from tests.e2e_replay.audit_replay_support import command_inputs
from tests.provider_graph import ProviderGraph

WAITING_INDEX = 5
RESULT_INDEX = 7
TERMINATED_EXIT = -15


# Harness limit: codex only. Replay a native command completion and a yielded background command.
@pytest.mark.parametrize("restart_index", [None, 3, 6])
@pytest.mark.parametrize(("exit_code", "output"), [(0, "done"), (TERMINATED_EXIT, "")])
def test_command_results_do_not_scan_old_history(
    monkeypatch: pytest.MonkeyPatch, restart_index: int | None, exit_code: int, output: str,
) -> None:
    """Known commands reach the display without a full rollout scan."""
    scan = Mock(wraps=translator_recovery.backward_lines)
    monkeypatch.setattr(translator_recovery, "backward_lines", scan)
    application = ProviderGraph()
    tabs = command_color_support.record_colors(application)
    for index, record in enumerate(command_inputs("audit_codex_command_colors.jsonl")):
        if index == restart_index:
            application = ProviderGraph()
            tabs = command_color_support.record_colors(application)
        application.raw_events.record((
            command_color_support.poll_result(record, exit_code, output)
            if index == RESULT_INDEX else record,
        ))
        application.provider("interpreter", Interpreter).tick()
        application.reaction_loop.tick()
        if index == WAITING_INDEX:
            command_color_support.check_color(application, tabs, "awaiting_background", "#61afef")
    command_color_support.check_color(application, tabs, "awaiting_response", "#98c379")
    if restart_index is None:
        scan.assert_not_called()
