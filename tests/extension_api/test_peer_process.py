# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify real peer queries, factory resolution, and guarded nested calls."""

import asyncio
import sys
from pathlib import Path

import pytest
from baqylau_extension_api.runtime.service_provider import ServiceProvider

from tests.extension_api import peer_process, service_samples as fixtures


@pytest.mark.parametrize("reverse", [False, True])
def test_two_workers_share_a_public_read(tmp_path: Path, *, reverse: bool) -> None:
    """Either load order permits a declared query with no private peer import."""
    asyncio.run(_round_trip(tmp_path, reverse=reverse))
    assert "peer_backend" not in sys.modules


async def _round_trip(directory: Path, *, reverse: bool) -> None:
    async with peer_process.running_peers(directory, reverse=reverse) as peers:
        expected = "available" if reverse else "not_enabled"
        assert await peer_process.read_alpha(peers, "factory") == expected
        assert await peer_process.read_alpha(peers) == fixtures.PEER_VALUE
        assert await peer_process.read_alpha(peers, "echo") == "test.alpha:echo"


def test_removed_peer_is_explicitly_unavailable(tmp_path: Path) -> None:
    """A disabled optional provider does not look like an empty successful read."""
    asyncio.run(_disable(tmp_path))


async def _disable(directory: Path) -> None:
    async with peer_process.running_peers(directory) as peers:
        assert await peer_process.read_alpha(peers) == fixtures.PEER_VALUE
        provider = peers.providers.get_service_provider(fixtures.BETA, fixtures.resolve_request().binding.scope)
        assert provider is not None
        peers.providers.replace(ServiceProvider(manifest=provider.manifest, schemas=provider.schemas))
        peers.calls.revoke_runtime(fixtures.environment(fixtures.BETA))
        assert await peer_process.read_alpha(peers) == "not_enabled"
