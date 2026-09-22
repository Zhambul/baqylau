# Copyright (c) 2026 Zhambyl Yermagambet
"""Run package-owned migrations in the installed SDK's separate worker."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from baqylau_extension_api.runtime import bridge, methods, worker_models
from baqylau_extension_api.runtime.migrations import RemoteMigrations
from pydantic import TypeAdapter

from tests.extension_api import migration_samples, process_fixture, samples


@asynccontextmanager
async def running_migrations(directory: Path, *, downgrade: bool = False) -> AsyncIterator[RemoteMigrations]:
    """Load an external feature through its manifest and public factory.

    Yields:
        A protocol proxy with no backend import in the caller.

    """
    request = worker_models.WorkerLoadRequest(
        manifest=migration_samples.manifest(downgrade=downgrade), environment=samples.worker_environment(),
    )
    async with process_fixture.running_worker(directory) as worker:
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(worker_models.WorkerReady))
        assert ready.capabilities == ("lifecycle", "migrations")
        yield RemoteMigrations(bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3))
