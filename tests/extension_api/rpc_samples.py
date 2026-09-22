# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect the public runtime over local streams without a daemon."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from baqylau_extension_api.models.base import NonemptyText, WireModel
from baqylau_extension_api.runtime.channel import RpcChannel, stream_channel
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES
from pydantic import TypeAdapter

REVISION = "runtime-1"


class EchoRequest(WireModel):
    """Supply a strict string argument for transport tests."""

    text: NonemptyText


ECHO_ADAPTER = TypeAdapter(EchoRequest)
ECHO_METHOD = "echo"


@dataclass(frozen=True)
class ChannelPair:
    """Expose both stream peers for symmetric request and callback tests."""

    host: RpcChannel
    worker: RpcChannel


@asynccontextmanager
async def connected_channels(
    request_seconds: float = 2.0, worker_revision: str = REVISION,
) -> AsyncIterator[ChannelPair]:
    """Start paired socket streams with the same framing as worker pipes.

    Yields:
        A started host and worker channel with bounded input buffers.

    """
    sockets = socket.socketpair()
    host_streams = await asyncio.open_connection(sock=sockets[0], limit=MAX_HEADER_BYTES)
    worker_streams = await asyncio.open_connection(sock=sockets[1], limit=MAX_HEADER_BYTES)
    pair = ChannelPair(
        host=stream_channel(*host_streams, REVISION, request_seconds),
        worker=stream_channel(*worker_streams, worker_revision, request_seconds),
    )
    pair.host.start()
    pair.worker.start()
    try:
        yield pair
    finally:
        closing = (pair.host.close(), pair.worker.close())
        await asyncio.gather(*closing, return_exceptions=True)


def echo(request: EchoRequest) -> EchoRequest:
    """Return the immutable typed request through a model response boundary.

    Returns:
        The same text without type conversion or mutation.

    """
    return request
