# Copyright (c) 2026 Zhambyl Yermagambet
"""Run a pure projector whose complete feature code is outside the host tree."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from baqylau_extension_api.models.projections import ProjectionRequest
from baqylau_extension_api.runtime import bridge, methods, worker_models
from baqylau_extension_api.runtime.projection import RemoteProjector
from pydantic import TypeAdapter

from tests.extension_api import operation_samples, process_fixture, projection_samples, samples


@asynccontextmanager
async def running_projector(directory: Path) -> AsyncIterator[RemoteProjector]:
    """Import the SDK and external feature in a separate isolated Python process.

    Yields:
        A public protocol proxy, not the extension implementation.

    """
    request = worker_models.WorkerLoadRequest(
        manifest=projection_samples.manifest(), environment=samples.worker_environment(),
    )
    async with process_fixture.running_worker(directory) as worker:
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(worker_models.WorkerReady))
        assert ready.capabilities == ("lifecycle", "projector")
        yield RemoteProjector(bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3))


def input_mode(mode: str) -> ProjectionRequest:
    """Select fixture behavior through recorded input, never through hidden state.

    Returns:
        A captured request whose source document controls the fixture.

    """
    request = projection_samples.request()
    stored = request.events[0]
    encoded = TypeAdapter(str).dump_json(mode).decode()
    document = operation_samples.query_request(encoded).arguments
    fact = stored.fact.model_copy(update={"document": document})
    return request.model_copy(update={
        "events": (stored.model_copy(update={"fact": fact}),),
    })
