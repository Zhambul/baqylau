# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect separate feature workers through checked host service callbacks."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.runtime import bridge, call_grants, methods, queries, worker_models
from baqylau_extension_api.runtime.service_access import register_service_access
from baqylau_extension_api.runtime.service_dispatch import HostServiceAccess
from baqylau_extension_api.runtime.service_provider import ServiceProvider
from baqylau_extension_api.schemas import SchemaSet
from pydantic import TypeAdapter

from tests.extension_api import process_fixture, service_providers, service_samples as fixtures


@dataclass(frozen=True)
class PeerWorkers:
    """Keep parent-side transport proxies and host authority separate from feature code."""

    alpha: bridge.RpcBridge
    beta: bridge.RpcBridge
    calls: call_grants.HostCallLedger
    providers: service_providers.FixtureProviders


@asynccontextmanager
async def running_peers(
    directory: Path, *, reverse: bool = False, cycle: bool = False,
) -> AsyncIterator[PeerWorkers]:
    """Prepare two independent processes and checked callbacks for each connection.

    Yields:
        A read test host, not a daemon lifecycle or durable registry implementation.

    """
    providers = service_providers.FixtureProviders(tuple(ServiceProvider(
        manifest=fixtures.manifest(owner, cycle=cycle),
        schemas=SchemaSet((fixtures.schema(owner),)),
    ) for owner in (fixtures.ALPHA, fixtures.BETA)))
    calls = call_grants.HostCallLedger()
    order = (fixtures.BETA, fixtures.ALPHA) if reverse else (fixtures.ALPHA, fixtures.BETA)
    async with AsyncExitStack() as stack:
        started = (
            await _start_peer(directory / order[0], providers, calls, stack),
            await _start_peer(directory / order[1], providers, calls, stack),
        )
        if reverse:
            started = (started[1], started[0])
        yield PeerWorkers(*started, calls, providers)


async def _start_peer(
    directory: Path, providers: service_providers.FixtureProviders,
    calls: call_grants.HostCallLedger, stack: AsyncExitStack,
) -> bridge.RpcBridge:
    await asyncio.to_thread(directory.mkdir, exist_ok=True)
    provider = providers.get_service_provider(directory.name, fixtures.resolve_request().binding.scope)
    assert provider is not None
    worker = await stack.enter_async_context(process_fixture.running_worker(directory))
    load = worker_models.WorkerLoadRequest(manifest=provider.manifest, environment=fixtures.environment(directory.name))
    register_service_access(worker.channel, HostServiceAccess(load, providers, calls))
    ready = await worker.channel.call(methods.LOAD, load, TypeAdapter(worker_models.WorkerReady))
    assert ready.capabilities == ("lifecycle", "raw_transformer", "queries")
    caller = bridge.RpcBridge(worker.channel, asyncio.get_running_loop(), 3)
    providers.replace(ServiceProvider(
        manifest=provider.manifest, schemas=provider.schemas,
        environment=load.environment, queries=queries.RemoteQueries(caller),
    ))
    return caller


async def read_alpha(peers: PeerWorkers, mode: str = "peer") -> str:
    """Issue a real host grant before alpha makes a nested service call.

    Returns:
        The value read through the public query and service protocols.

    """
    request = fixtures.query_request(fixtures.ALPHA, mode)
    with peers.calls.root(fixtures.environment(fixtures.ALPHA), request.binding.scope, 3):
        response = await asyncio.to_thread(queries.RemoteQueries(peers.alpha).query, request)
    assert response.status == "ready"
    return TypeAdapter(str).validate_json(response.document.json_text)
