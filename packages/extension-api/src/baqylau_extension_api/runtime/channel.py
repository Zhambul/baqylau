# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose typed capability dispatch with the existing JSON-RPC peer library."""

import asyncio

from jsonrpcpeer import JsonRpcPeer
from pydantic import TypeAdapter

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.runtime import sender
from baqylau_extension_api.runtime.contract import AsyncReceiver, EncodedHandler
from baqylau_extension_api.runtime.execution import WorkerLanes
from baqylau_extension_api.runtime.host_context import current_host_call_id
from baqylau_extension_api.runtime.models import (
    MAX_RPC_BYTES,
    ExecutionKind,
    ExtensionTransportError,
    RequestTimeout,
    RpcEnvelope,
)
from baqylau_extension_api.runtime.receiver import RpcReceiver


class RpcChannel:
    """Own one bidirectional reader while all capability work runs elsewhere."""

    def __init__(self, peer: JsonRpcPeer, runtime_revision: str) -> None:
        """Bind a library peer to a prepared runtime revision."""
        self._peer = peer
        self._revision = TypeAdapter(Identifier).validate_python(runtime_revision, strict=True)
        self._lanes = WorkerLanes()
        self._started = False
        self._closed = False

    @property
    def completion(self) -> asyncio.Future[bool]:
        """The library-owned connection completion result."""
        return self._peer.completion

    def start(self) -> None:
        """Start the reader after all initial handlers are registered.

        Raises:
            ExtensionTransportError: If the channel was started or closed.

        """
        if self._started or self._closed:
            message = "extension channel cannot start more than once"
            raise ExtensionTransportError(message)
        self._started = True
        self._peer.start()

    async def close(self) -> None:
        """Stop message receipt and release registered execution queues."""
        self._closed = True
        self._lanes.close()
        if self._started:
            await self._peer.stop()
        else:
            self._peer.writer.close()
            await self._peer.writer.wait_closed()

    def register(self, method: str, dispatch: EncodedHandler, kind: ExecutionKind) -> None:
        """Register one declared method without exposing a callback dictionary.

        Raises:
            ExtensionTransportError: If the channel is already closed.

        """
        if self._closed:
            message = "extension channel is closed"
            raise ExtensionTransportError(message)
        receiver = RpcReceiver(self._revision, self._lanes, dispatch, kind)
        self.register_async(method, receiver)

    def register_async(self, method: str, receiver: AsyncReceiver) -> None:
        """Register a runtime-owned asynchronous receiver for worker bootstrap.

        Raises:
            ExtensionTransportError: If the channel is already closed.

        """
        if self._closed:
            message = "extension channel is closed"
            raise ExtensionTransportError(message)
        self._peer.register_request_handler(method, receiver.receive)

    async def call[Response](
        self, method: str, request: WireModel, response_adapter: TypeAdapter[Response],
    ) -> Response:
        """Call a typed method through the revision-bound process boundary.

        Returns:
            A validated public model, not an RPC library response object.

        Raises:
            ExtensionTransportError: If the reader is not active or the reply is invalid.

        """
        if not self._started or self._closed:
            message = "extension channel is not running"
            raise ExtensionTransportError(message)
        envelope = RpcEnvelope(
            runtime_revision=self._revision, json_text=request.model_dump_json(), host_call_id=current_host_call_id(),
        )
        response = await sender.send(self._peer, method, envelope)
        try:
            return response_adapter.validate_json(response.json_text, strict=True)
        except ValueError as exc:
            message = "extension RPC reply does not match its public model"
            raise ExtensionTransportError(message) from exc


def stream_channel(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, runtime_revision: str, timeout: float,
) -> RpcChannel:
    """Construct the bounded library transport used by host and worker adapters.

    Returns:
        An unstarted channel with a fixed message size and request deadline.

    """
    checked_timeout = TypeAdapter(RequestTimeout).validate_python(timeout, strict=True)
    return RpcChannel(JsonRpcPeer(
        reader, writer, request_timeout=checked_timeout, max_message_size=MAX_RPC_BYTES,
    ), runtime_revision)
