# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect the SDK transport and only the selected typed host callbacks."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.channel import RpcChannel, stream_channel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES
from baqylau_extension_api.runtime.service_access import register_service_access
from pydantic import TypeAdapter


@asynccontextmanager
async def worker_channel(
    endpoint: socket.socket, services: ExtensionHostServices, seconds: float,
) -> AsyncIterator[RpcChannel]:
    """Register callbacks before starting the single socket reader.

    Yields:
        The revision-bound channel without feature code in the host.

    """
    streams = await asyncio.open_connection(sock=endpoint, limit=MAX_HEADER_BYTES)
    channel = stream_channel(*streams, services.environment.runtime_revision, seconds)
    async with AsyncExitStack() as cleanup:
        cleanup.push_async_callback(channel.close)
        _register_callbacks(channel, services)
        channel.start()
        yield channel


def _register_callbacks(channel: RpcChannel, services: ExtensionHostServices) -> None:
    channel.register(methods.DIRECTORY, ModelHandler(
        DirectoryRequest, TypeAdapter(DirectorySnapshot), services.directory.list_extensions,
    ), "live")
    if services.service_access is not None:
        register_service_access(channel, services.service_access)
