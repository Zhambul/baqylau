# Copyright (c) 2026 Zhambyl Yermagambet
"""Load an observer package outside the checkout through the public worker."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from baqylau_extension_api.runtime import bridge, methods, worker_models
from pydantic import TypeAdapter

from tests.extension_api import observer_samples, process_fixture, samples


@asynccontextmanager
async def running_observer(directory: Path) -> AsyncIterator[bridge.RpcBridge]:
    """Use a separate feature file and the installed SDK, with no private imports.

    Yields:
        The typed caller for observer, query, and pure processing protocols.

    """
    request = worker_models.WorkerLoadRequest(
        manifest=observer_samples.manifest(), environment=samples.worker_environment(),
    )
    async with process_fixture.running_worker(directory) as worker:
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(worker_models.WorkerReady))
        assert ready.capabilities == ("lifecycle", "translator", "raw_transformer", "observer", "queries")
        yield bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3)
