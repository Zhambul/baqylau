# Copyright (c) 2026 Zhambyl Yermagambet
"""Use public operation protocols with an actual external process fixture."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from baqylau_extension_api.runtime import bridge, methods
from baqylau_extension_api.runtime.queries import RemoteQueries
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest, WorkerReady
from pydantic import TypeAdapter

from tests.extension_api import operation_samples, process_fixture, samples


@asynccontextmanager
async def running_operations(directory: Path) -> AsyncIterator[bridge.RpcBridge]:
    """Load only SDK and feature files in an isolated Python process.

    Yields:
        The same typed caller used by the host process adapters.

    """
    async with process_fixture.running_worker(directory) as worker:
        request = WorkerLoadRequest(manifest=operation_samples.manifest(), environment=samples.worker_environment())
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(WorkerReady))
        assert ready.capabilities == ("lifecycle", "raw_transformer", "queries", "commands")
        yield bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3)


async def read_counts(caller: bridge.RpcBridge, encoded: str = '"input"') -> str:
    """Read a query whose implementation calls the host directory service.

    Returns:
        The decoded feature-owned text document.

    """
    request = operation_samples.query_request(encoded)
    response = await asyncio.to_thread(RemoteQueries(caller).query, request)
    assert response.status == "ready"
    assert response.binding == request.binding
    return TypeAdapter(str).validate_json(response.document.json_text)


async def wait_for_active(caller: bridge.RpcBridge) -> None:
    """Wait for a recorded running command, with a strict local deadline."""
    assert await read_counts(caller, '"wait_for_active"') == "executions:1;active:1;peers:1"
