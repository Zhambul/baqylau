# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the synchronous engine API while a separate loop receives RPC replies."""

import asyncio
from concurrent.futures import Future
from threading import get_ident

from pydantic import TypeAdapter

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.context import require_live_call
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.host_context import remaining_call_seconds
from baqylau_extension_api.runtime.models import ExtensionTransportError, RequestTimeout


class RpcBridge(RemoteCaller):
    """Submit synchronous calls from outside the channel's event loop thread."""

    def __init__(self, channel: RpcChannel, loop: asyncio.AbstractEventLoop, timeout: float) -> None:
        """Create the bridge on the event loop thread that owns the channel."""
        self._channel = channel
        self._loop = loop
        self._timeout = TypeAdapter(RequestTimeout).validate_python(timeout, strict=True)
        self._reader_thread = get_ident()

    def invoke_typed[Response](
        self, rpc_method: str, rpc_request: WireModel, rpc_response_adapter: TypeAdapter[Response],
    ) -> Response:
        """Wait for a typed result without stopping transport message receipt.

        Returns:
            The public model produced by the remote capability.

        Raises:
            ExtensionTransportError: If called from the reader thread or after a timeout.

        """
        require_live_call()
        if get_ident() == self._reader_thread:
            message = "synchronous RPC calls cannot run on the transport reader thread"
            raise ExtensionTransportError(message)
        timeout = remaining_call_seconds(self._timeout)
        pending = self._submit(rpc_method, rpc_request, rpc_response_adapter)
        try:
            return pending.result(timeout=timeout)
        except TimeoutError as exc:
            pending.cancel()
            message = "synchronous extension call timed out"
            raise ExtensionTransportError(message) from exc

    def _submit[Response](
        self, method: str, request: WireModel, adapter: TypeAdapter[Response],
    ) -> Future[Response]:
        operation = self._channel.call(method, request, adapter)
        try:
            return asyncio.run_coroutine_threadsafe(operation, self._loop)
        except RuntimeError as exc:
            operation.close()
            message = "extension transport loop is closed"
            raise ExtensionTransportError(message) from exc
