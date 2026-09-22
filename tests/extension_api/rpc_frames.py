# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply raw stream bytes only for transport boundary tests."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from baqylau_extension_api.runtime.channel import RpcChannel, stream_channel
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES

from tests.extension_api import rpc_samples


@dataclass(frozen=True)
class FramePeer:
    """Expose an untrusted byte writer and its receiving SDK channel."""

    writer: asyncio.StreamWriter
    channel: RpcChannel


@asynccontextmanager
async def receiving_channel() -> AsyncIterator[FramePeer]:
    """Use real asyncio buffer limits around the JSON-RPC library reader.

    Yields:
        A started receiver with a separately controlled peer stream.

    """
    sockets = socket.socketpair()
    streams = await asyncio.open_connection(sock=sockets[0], limit=MAX_HEADER_BYTES)
    raw_streams = await asyncio.open_connection(sock=sockets[1], limit=MAX_HEADER_BYTES)
    channel = stream_channel(*streams, rpc_samples.REVISION, 1)
    channel.start()
    try:
        yield FramePeer(raw_streams[1], channel)
    finally:
        await _close_peer(raw_streams[1], channel)


async def _close_peer(writer: asyncio.StreamWriter, channel: RpcChannel) -> None:
    writer.close()
    closing = (writer.wait_closed(), channel.close())
    await asyncio.gather(*closing, return_exceptions=True)
