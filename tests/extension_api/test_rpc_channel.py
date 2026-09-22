# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify typed calls over the existing JSON-RPC stream implementation."""

import asyncio

import pytest
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import rpc_samples


def test_rpc_round_trip_preserves_unicode() -> None:
    """Use real framed streams with non-ASCII and multiline content."""
    asyncio.run(_round_trip())


async def _round_trip() -> None:
    async with rpc_samples.connected_channels() as pair:
        pair.worker.register(rpc_samples.ECHO_METHOD, ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, rpc_samples.echo,
        ), "pure")
        request = rpc_samples.EchoRequest(text="Жамбыл\nsecond line")
        response = await pair.host.call(rpc_samples.ECHO_METHOD, request, rpc_samples.ECHO_ADAPTER)
        assert response == request


def test_rpc_rejects_stale_revision() -> None:
    """Reject a request from another runtime before it invokes a handler."""
    asyncio.run(_stale_revision())


async def _stale_revision() -> None:
    async with rpc_samples.connected_channels(worker_revision="runtime-2") as pair:
        pair.worker.register(rpc_samples.ECHO_METHOD, ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, rpc_samples.echo,
        ), "pure")
        request = rpc_samples.EchoRequest(text="test")
        with pytest.raises(ExtensionTransportError, match="RPC call failed"):
            await pair.host.call(rpc_samples.ECHO_METHOD, request, rpc_samples.ECHO_ADAPTER)


def test_rpc_rejects_unknown_method() -> None:
    """Do not dispatch an undeclared remote method."""
    asyncio.run(_unknown_method())


async def _unknown_method() -> None:
    async with rpc_samples.connected_channels() as pair:
        request = rpc_samples.EchoRequest(text="test")
        with pytest.raises(ExtensionTransportError, match="RPC call failed"):
            await pair.host.call("missing", request, rpc_samples.ECHO_ADAPTER)
