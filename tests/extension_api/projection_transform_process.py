# Copyright (c) 2026 Zhambyl Yermagambet
"""Run projection-transform tests through a real SDK-only worker process."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from baqylau_extension_api.runtime import bridge, methods, worker_models
from baqylau_extension_api.runtime.projection_transforms import RemoteProjectionTransformer
from pydantic import TypeAdapter

from tests.extension_api import process_fixture, projection_transform_samples, samples


@asynccontextmanager
async def running_transformer(directory: Path) -> AsyncIterator[RemoteProjectionTransformer]:
    """Load feature code outside the host checkout using the public capability.

    Yields:
        A typed proxy with no feature imports in the parent.

    """
    request = worker_models.WorkerLoadRequest(
        manifest=projection_transform_samples.manifest(), environment=samples.worker_environment(),
    )
    async with process_fixture.running_worker(directory) as worker:
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(worker_models.WorkerReady))
        assert ready.capabilities == ("lifecycle", "projection_transformer")
        yield RemoteProjectionTransformer(bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3))
