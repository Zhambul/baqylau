# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep bidirectional calls and pure work independent of slow handlers."""

import asyncio
from threading import Event
from typing import Final

import pytest
from baqylau_extension_api.runtime.bridge import RpcBridge
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import rpc_callbacks, rpc_samples

PURE: Final = "pure"
LIVE: Final = "live"


def test_host_callback_does_not_deadlock() -> None:
    """Receive a host callback while the host waits for the original result."""
    asyncio.run(_callback_round_trip())


async def _callback_round_trip() -> None:
    async with rpc_samples.connected_channels() as pair:
        callback = rpc_callbacks.CallbackEcho(RpcBridge(pair.worker, asyncio.get_running_loop(), 1))
        pair.host.register("host.echo", ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, rpc_samples.echo,
        ), LIVE)
        pair.worker.register("worker.echo", ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, callback.echo,
        ), LIVE)
        request = rpc_samples.EchoRequest(text="callback")
        assert await pair.host.call("worker.echo", request, rpc_samples.ECHO_ADAPTER) == request


def test_slow_work_does_not_block_transform() -> None:
    """Use separate worker threads for a held command and a pure transform."""
    asyncio.run(_slow_work())


async def _slow_work() -> None:
    async with rpc_samples.connected_channels() as pair:
        waiting = rpc_callbacks.WaitingCall(Event(), Event())
        pair.worker.register("slow", waiting, LIVE)
        pair.worker.register(PURE, ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, rpc_samples.echo,
        ), PURE)
        request = rpc_samples.EchoRequest(text="independent")
        slow_call = pair.host.call("slow", request, rpc_samples.ECHO_ADAPTER)
        slow = asyncio.create_task(slow_call)
        assert await asyncio.to_thread(waiting.entered.wait, 1)
        assert await pair.host.call(PURE, request, rpc_samples.ECHO_ADAPTER) == request
        assert not slow.done()
        waiting.release.set()
        assert await slow == request


def test_pure_work_cannot_call_live_services() -> None:
    """Apply a per-call service guard without changing the next live call."""
    asyncio.run(_pure_guard())


async def _pure_guard() -> None:
    async with rpc_samples.connected_channels() as pair:
        dispatch = ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, rpc_callbacks.forbidden_service,
        )
        pair.worker.register(PURE, dispatch, PURE)
        pair.worker.register(LIVE, dispatch, LIVE)
        request = rpc_samples.EchoRequest(text="guarded")
        with pytest.raises(ExtensionTransportError):
            await pair.host.call(PURE, request, rpc_samples.ECHO_ADAPTER)
        assert await pair.host.call(LIVE, request, rpc_samples.ECHO_ADAPTER) == request
