# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep host call references across threads and nested callbacks without leaks."""

import asyncio

import pytest
from baqylau_extension_api.runtime.bridge import RpcBridge
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.host_context import current_host_call_id, host_call_scope
from pydantic import ValidationError

from tests.extension_api import rpc_callbacks, rpc_samples


def test_host_call_scope_restores_parent() -> None:
    """Nested call references must not replace the outer call or later work."""
    assert current_host_call_id() is None
    with host_call_scope("outer"):
        with host_call_scope("inner"):
            assert current_host_call_id() == "inner"
        assert current_host_call_id() == "outer"
        with pytest.raises(ValidationError), host_call_scope("bad reference"):
            pytest.fail("An invalid call reference entered the scope.")
        assert current_host_call_id() == "outer"
    assert current_host_call_id() is None


def test_call_reference_crosses_nested_callback() -> None:
    """The executor and synchronous bridge preserve the host-issued reference."""
    asyncio.run(_round_trip())


async def _round_trip() -> None:
    async with rpc_samples.connected_channels() as pair:
        callback = rpc_callbacks.CallbackEcho(RpcBridge(pair.worker, asyncio.get_running_loop(), 1))
        pair.host.register("host.echo", ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, _read_context,
        ), "live")
        pair.worker.register("worker.echo", ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, callback.echo,
        ), "live")
        request = rpc_samples.EchoRequest(text="expected")
        with host_call_scope("host-call-1"):
            response = await pair.host.call("worker.echo", request, rpc_samples.ECHO_ADAPTER)
        assert response.text == "host-call-1"
        response = await pair.host.call("worker.echo", request, rpc_samples.ECHO_ADAPTER)
        assert response.text == "none"
        assert current_host_call_id() is None


def test_concurrent_call_contexts_remain_separate() -> None:
    """Two jobs on one connection cannot inherit each other's call references."""
    asyncio.run(_concurrent())


async def _concurrent() -> None:
    async with rpc_samples.connected_channels() as pair:
        pair.worker.register("context", ModelHandler(
            rpc_samples.EchoRequest, rpc_samples.ECHO_ADAPTER, _read_context,
        ), "live")
        request = rpc_samples.EchoRequest(text="value")
        with host_call_scope("first"):
            first = asyncio.create_task(pair.host.call("context", request, rpc_samples.ECHO_ADAPTER))
        with host_call_scope("second"):
            second = asyncio.create_task(pair.host.call("context", request, rpc_samples.ECHO_ADAPTER))
        assert (await first).text == "first"
        assert (await second).text == "second"
        assert current_host_call_id() is None


def _read_context(request: rpc_samples.EchoRequest) -> rpc_samples.EchoRequest:
    return request.model_copy(update={"text": current_host_call_id() or "none"})
