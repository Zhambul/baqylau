# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind every RPC reply to the same host call reference as its request."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import pytest
from baqylau_extension_api.runtime.codec import encode_parameters
from baqylau_extension_api.runtime.contract import AsyncReceiver
from baqylau_extension_api.runtime.host_context import host_call_scope
from baqylau_extension_api.runtime.models import ExtensionTransportError, RpcEnvelope

from tests.extension_api import rpc_samples


@dataclass(frozen=True)
class ChangedContextReply(AsyncReceiver):
    """Return valid data with a missing or changed call reference."""

    changed_id: str | None

    async def receive(self, packet: Mapping[str, str]) -> Mapping[str, str]:
        """Keep body and runtime valid to isolate the call-reference check.

        Returns:
            A deliberately invalid correlated reply.

        """
        request = RpcEnvelope.model_validate(packet)
        return encode_parameters(request.model_copy(update={"host_call_id": self.changed_id}))


@pytest.mark.parametrize("changed_id", [None, "different"])
def test_reply_cannot_change_host_call(changed_id: str | None) -> None:
    """RPC method and runtime matches are insufficient if call context changed."""
    asyncio.run(_invalid_reply(changed_id))


async def _invalid_reply(changed_id: str | None) -> None:
    async with rpc_samples.connected_channels() as pair:
        pair.worker.register_async("changed", ChangedContextReply(changed_id))
        with host_call_scope("expected"), pytest.raises(ExtensionTransportError, match="host call reference"):
            await pair.host.call("changed", rpc_samples.EchoRequest(text="valid"), rpc_samples.ECHO_ADAPTER)
