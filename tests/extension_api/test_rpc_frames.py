# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid and oversized frames without allocating the claimed body."""

import asyncio

import pytest
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES, MAX_RPC_BYTES

from tests.extension_api import rpc_frames

OVERSIZED_LENGTH = MAX_RPC_BYTES + 1


@pytest.mark.parametrize("frame", [
    f"Content-Length: {OVERSIZED_LENGTH}\r\n\r\n".encode(),
    b"Content-Length: -1\r\n\r\n",
    b"Content-Length: invalid\r\n\r\n",
    b"".join((b"X-Long: ", b"x" * MAX_HEADER_BYTES, b"\r\n\r\n")),
])
def test_invalid_frame_closes_reader(frame: bytes) -> None:
    """End the reader on size or header failures within a fixed test deadline."""
    asyncio.run(_reject_frame(frame))


async def _reject_frame(frame: bytes) -> None:
    async with rpc_frames.receiving_channel() as peer:
        peer.writer.write(frame)
        await peer.writer.drain()
        async with asyncio.timeout(1):
            await asyncio.gather(peer.channel.completion, return_exceptions=True)
        assert peer.channel.completion.done()


def test_truncated_body_closes_reader() -> None:
    """Do not leave a partial message waiting after the process stream closes."""
    asyncio.run(_truncated_body())


async def _truncated_body() -> None:
    async with rpc_frames.receiving_channel() as peer:
        peer.writer.write(b"Content-Length: 10\r\n\r\n{}")
        await peer.writer.drain()
        peer.writer.close()
        async with asyncio.timeout(1):
            with pytest.raises(asyncio.IncompleteReadError):
                await peer.channel.completion
