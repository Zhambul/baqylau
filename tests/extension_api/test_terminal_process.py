# Copyright (c) 2026 Zhambyl Yermagambet
"""Read external terminal layouts through a real isolated SDK worker."""

import asyncio
import sys
from pathlib import Path

import pytest
from baqylau_extension_api.runtime import bridge, methods, models
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.presentation import RemoteTerminalPresenter
from baqylau_extension_api.runtime.worker_models import WorkerReady
from baqylau_extension_api.terminal.layout_rules import walk_blocks
from baqylau_extension_api.terminal.models import TerminalViewRequest
from pydantic import TypeAdapter

from tests.extension_api import process_fixture, terminal_samples


def test_terminal_layout_crosses_process_boundary(tmp_path: Path) -> None:
    """Load only feature files outside the repo and receive typed layout blocks."""
    asyncio.run(_round_trip(tmp_path))
    assert "terminal_backend" not in sys.modules


async def _round_trip(directory: Path) -> None:
    async with process_fixture.running_worker(directory) as worker:
        await _load_terminal(worker.channel)
        caller = bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3)
        presenter = RemoteTerminalPresenter(caller)
        request = terminal_samples.view_request()
        response = await asyncio.to_thread(presenter.present, request)
        assert response.binding == request.binding
        assert {block.kind for block in walk_blocks(response.blocks)} == {
            "section", "text", "status", "table", "file_tree", "diff", "list",
        }
        assert await asyncio.to_thread(presenter.present, request) == response


async def _load_terminal(channel: RpcChannel) -> None:
    request = terminal_samples.load_request()
    ready = await channel.call(methods.LOAD, request, TypeAdapter(WorkerReady))
    assert ready.capabilities == ("lifecycle", "terminal")


@pytest.mark.parametrize("change", [{"runtime_revision": "stale"}, {"view_id": "test.sample.absent"}])
def test_terminal_worker_rejects_invalid_binding(tmp_path: Path, change: dict[str, str]) -> None:
    """Do not render a stale or undeclared view in a valid RPC envelope."""
    request = terminal_samples.view_request()
    changed = request.model_copy(update={"binding": request.binding.model_copy(update=change)})
    asyncio.run(_rejected_binding(tmp_path, changed))


async def _rejected_binding(directory: Path, request: TerminalViewRequest) -> None:
    async with process_fixture.running_worker(directory) as worker:
        await _load_terminal(worker.channel)
        presenter = RemoteTerminalPresenter(bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3))
        with pytest.raises(models.ExtensionTransportError):
            await asyncio.to_thread(presenter.present, request)
