# Copyright (c) 2026 Zhambyl Yermagambet
"""Check live authority, pure-call rejection, and service cycles in separate workers."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.runtime.models import ExtensionTransportError
from baqylau_extension_api.runtime.proxies import RemoteRawTransformer
from baqylau_extension_api.runtime.queries import RemoteQueries

from tests.extension_api import peer_process, service_samples as fixtures, worker_samples


def test_peer_query_needs_host_call_authority(tmp_path: Path) -> None:
    """A worker cannot start a new chain by omitting its host call reference."""
    asyncio.run(_missing_authority(tmp_path))


async def _missing_authority(directory: Path) -> None:
    async with peer_process.running_peers(directory) as peers:
        request = fixtures.query_request(fixtures.ALPHA)
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteQueries(peers.alpha).query, request)
        assert await peer_process.read_alpha(peers) == fixtures.PEER_VALUE


def test_pure_work_cannot_resolve_peer_service(tmp_path: Path) -> None:
    """The service proxy uses the same live-call guard as other host services."""
    asyncio.run(_pure_guard(tmp_path))


async def _pure_guard(directory: Path) -> None:
    async with peer_process.running_peers(directory) as peers:
        request = worker_samples.raw_request()
        context = request.context.model_copy(update={"extension_id": fixtures.ALPHA})
        request = request.model_copy(update={"context": context})
        result = await asyncio.to_thread(RemoteRawTransformer(peers.alpha).transform, request)
        assert result.operations[0].kind == "drop"
        assert result.operations[0].reason == "pure extension processing cannot call live host services"
        assert await peer_process.read_alpha(peers) == fixtures.PEER_VALUE


def test_nested_service_cycle_is_rejected(tmp_path: Path) -> None:
    """Check the call-chain guard independently of active-set dependency validation."""
    asyncio.run(_cycle(tmp_path))


async def _cycle(directory: Path) -> None:
    async with peer_process.running_peers(directory, cycle=True) as peers:
        with pytest.raises(ExtensionTransportError):
            await peer_process.read_alpha(peers, "cycle")
        assert await peer_process.read_alpha(peers) == fixtures.PEER_VALUE
