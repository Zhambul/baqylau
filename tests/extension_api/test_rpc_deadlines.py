# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop waits on timeouts, caller cancellation, and lost process connections."""

import asyncio
from threading import Event

import pytest
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import rpc_callbacks, rpc_samples

SLOW_METHOD = "slow"
SHORT_TIMEOUT = 0.05
LONG_TIMEOUT = 30


def test_timeout_does_not_break_next_call() -> None:
    """Ignore a late reply and keep request correlation valid for the next call."""
    asyncio.run(_timeout_then_echo())


async def _timeout_then_echo() -> None:
    async with rpc_samples.connected_channels(request_seconds=SHORT_TIMEOUT) as pair:
        waiting = rpc_callbacks.WaitingCall(Event(), Event())
        pair.worker.register(SLOW_METHOD, waiting, "live")
        request = rpc_samples.EchoRequest(text="late")
        with pytest.raises(ExtensionTransportError, match="timed out"):
            await pair.host.call(SLOW_METHOD, request, rpc_samples.ECHO_ADAPTER)
        assert waiting.entered.is_set()
        waiting.release.set()
        pair.worker.register(rpc_samples.ECHO_METHOD, ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, rpc_samples.echo,
        ), "pure")
        assert await pair.host.call(rpc_samples.ECHO_METHOD, request, rpc_samples.ECHO_ADAPTER) == request


def test_disconnect_stops_pending_call() -> None:
    """A lost peer must stop a wait before its much longer request deadline."""
    asyncio.run(_disconnect())


async def _disconnect() -> None:
    async with rpc_samples.connected_channels(request_seconds=LONG_TIMEOUT) as pair:
        waiting = rpc_callbacks.WaitingCall(Event(), Event())
        pair.worker.register(SLOW_METHOD, waiting, "live")
        request = rpc_samples.EchoRequest(text="disconnect")
        pending = asyncio.create_task(pair.host.call(SLOW_METHOD, request, rpc_samples.ECHO_ADAPTER))
        assert await asyncio.to_thread(waiting.entered.wait, 1)
        await pair.worker.close()
        waiting.release.set()
        async with asyncio.timeout(1):
            with pytest.raises(ExtensionTransportError):
                await pending


def test_caller_cancellation_stays_cancellation() -> None:
    """Do not turn local cancellation into a remote capability failure."""
    asyncio.run(_cancel_call())


async def _cancel_call() -> None:
    async with rpc_samples.connected_channels() as pair:
        waiting = rpc_callbacks.WaitingCall(Event(), Event())
        pair.worker.register(SLOW_METHOD, waiting, "live")
        request = rpc_samples.EchoRequest(text="cancel")
        pending = asyncio.create_task(pair.host.call(SLOW_METHOD, request, rpc_samples.ECHO_ADAPTER))
        assert await asyncio.to_thread(waiting.entered.wait, 1)
        pending.cancel()
        waiting.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
