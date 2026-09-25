# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect the SDK transport and only the selected typed host callbacks."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.runtime import (
    credential_access,
    methods,
    process_access,
    record_access,
    reporting_access,
    service_access,
    session_access,
)
from baqylau_extension_api.runtime.channel import RpcChannel, stream_channel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES
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
        service_access.register_service_access(channel, services.service_access)
    if services.credentials is not None:
        credential_access.register_credential_access(channel, services.credentials)
    if services.processes is not None:
        process_access.register_process_access(channel, services.processes)
    if services.inference is not None:
        process_access.register_inference_access(channel, services.inference)
    if services.records is not None:
        record_access.register_record_access(channel, services.records)
    if services.sessions is not None:
        session_access.register_session_access(channel, services.sessions)
    reporting_access.register_reporting_access(channel, services.observations, services.audit)
