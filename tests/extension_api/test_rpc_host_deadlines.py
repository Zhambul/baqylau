# Copyright (c) 2026 Zhambyl Yermagambet
"""Enforce the remaining host deadline while transport work is waiting."""

import asyncio
from contextlib import ExitStack
from threading import Event
from time import monotonic

import pytest
from baqylau_extension_api.runtime.host_context import host_call_scope
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import rpc_callbacks, rpc_samples

SHORT_DEADLINE = 0.05
SLOW = "slow"


def test_host_deadline_stops_the_transport_wait() -> None:
    """A longer default RPC timeout cannot extend the host's remaining call time."""
    asyncio.run(_deadline())


async def _deadline() -> None:
    async with rpc_samples.connected_channels(request_seconds=2) as pair:
        waiting = rpc_callbacks.WaitingCall(Event(), Event())
        pair.worker.register(SLOW, waiting, "live")
        request = rpc_samples.EchoRequest(text="deadline")
        with ExitStack() as cleanup:
            cleanup.callback(waiting.release.set)
            with host_call_scope("host-call", expires_at=monotonic() + SHORT_DEADLINE):
                pending = asyncio.create_task(pair.host.call(SLOW, request, rpc_samples.ECHO_ADAPTER))
            assert await asyncio.to_thread(waiting.entered.wait, 1)
            with pytest.raises(ExtensionTransportError):
                await pending


def test_expired_host_deadline_does_not_dispatch() -> None:
    """Reject already expired calls before the peer sees a new request."""
    asyncio.run(_expired())


async def _expired() -> None:
    async with rpc_samples.connected_channels() as pair:
        waiting = rpc_callbacks.WaitingCall(Event(), Event())
        pair.worker.register(SLOW, waiting, "live")
        request = rpc_samples.EchoRequest(text="expired")
        with (
            host_call_scope("expired-call", expires_at=monotonic() - 1),
            pytest.raises(ExtensionTransportError),
        ):
            await pair.host.call(SLOW, request, rpc_samples.ECHO_ADAPTER)
        assert not waiting.entered.is_set()
