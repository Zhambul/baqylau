# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop a pending call on timeout, cancellation, or a closed peer."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from jsonrpcpeer import JsonRpcPeer

from baqylau_extension_api.runtime.codec import encode_parameters
from baqylau_extension_api.runtime.host_context import remaining_call_seconds
from baqylau_extension_api.runtime.models import ExtensionTransportError, RpcEnvelope


async def send(peer: JsonRpcPeer, method: str, envelope: RpcEnvelope) -> RpcEnvelope:
    """Validate the remote reply without exposing library objects to callers.

    Returns:
        The reply with the expected runtime revision.

    Raises:
        ExtensionTransportError: If a reply is invalid, late, or unavailable.

    """
    try:
        response = await _until_closed(peer, method, envelope)
    except Exception as exc:
        message = "extension RPC call failed or timed out"
        raise ExtensionTransportError(message) from exc
    if response.runtime_revision != envelope.runtime_revision:
        message = "extension reply has a stale runtime revision"
        raise ExtensionTransportError(message)
    if response.host_call_id != envelope.host_call_id:
        message = "extension reply changed its host call reference"
        raise ExtensionTransportError(message)
    return response


async def _request(peer: JsonRpcPeer, method: str, envelope: RpcEnvelope) -> RpcEnvelope:
    response: object = await peer.send_request(method, encode_parameters(envelope))
    return RpcEnvelope.model_validate(response)


async def _until_closed(peer: JsonRpcPeer, method: str, envelope: RpcEnvelope) -> RpcEnvelope:
    async with asyncio.timeout(remaining_call_seconds(peer.request_timeout)):
        return await _wait_for_reply(peer, method, envelope)


async def _wait_for_reply(peer: JsonRpcPeer, method: str, envelope: RpcEnvelope) -> RpcEnvelope:
    async with _pending_request(peer, method, envelope) as pending:
        await asyncio.wait((pending, peer.completion), return_when=asyncio.FIRST_COMPLETED)
        if not pending.done():
            message = "extension RPC peer closed during a call"
            raise ExtensionTransportError(message)
        return await pending


@asynccontextmanager
async def _pending_request(
    peer: JsonRpcPeer, method: str, envelope: RpcEnvelope,
) -> AsyncIterator[asyncio.Task[RpcEnvelope]]:
    pending = asyncio.create_task(_request(peer, method, envelope))
    try:
        yield pending
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
