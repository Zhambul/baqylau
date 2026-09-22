# Copyright (c) 2026 Zhambyl Yermagambet
"""Run a real SDK worker with feature files outside the host import path."""

import asyncio
import shutil
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.runtime.channel import RpcChannel, stream_channel
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES

from tests.extension_api import samples, worker_samples

EXAMPLES = Path(__file__).parent


@dataclass(frozen=True)
class ProcessWorker:
    """Expose the supervised fixture process and its parent-side protocol channel."""

    channel: RpcChannel
    process: asyncio.subprocess.Process


@asynccontextmanager
async def running_worker(directory: Path, executable: str = sys.executable) -> AsyncIterator[ProcessWorker]:
    """Copy only feature code, then run the installed SDK with Python isolated mode.

    Yields:
        A started process whose factory is not imported by the parent.

    """
    _copy_examples(directory)
    sockets = socket.socketpair()
    with sockets[0], sockets[1]:
        process = await _launch(directory, sockets[1].fileno(), executable)
        sockets[1].close()
        try:
            async with _parent_channel(sockets[0]) as channel:
                yield ProcessWorker(channel, process)
        finally:
            await _finish(process)


def _copy_examples(directory: Path) -> None:
    examples = (
        ("example", "sample"), ("terminal_example", "terminal"), ("operation_example", "operations"),
        ("source_example", "source"), ("projection_example", "projection"),
        ("projection_transform_example", "projection_transform"),
        ("migration_example", "migration"), ("observer_example", "observer"), ("peer_example", "peer"),
    )
    for source, target in examples:
        shutil.copyfile(EXAMPLES / f"{source}.py", directory / f"{target}_backend.py")


async def _launch(directory: Path, descriptor: int, executable: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        executable, "-I", "-B", "-m", "baqylau_extension_api.runtime.worker",
        "--package-directory", str(directory),
        "--rpc-fd", str(descriptor), "--runtime-revision", samples.RUNTIME_REVISION,
        pass_fds=(descriptor,), cwd=directory, start_new_session=True,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


@asynccontextmanager
async def _parent_channel(endpoint: socket.socket) -> AsyncIterator[RpcChannel]:
    streams = await asyncio.open_connection(sock=endpoint, limit=MAX_HEADER_BYTES)
    channel = stream_channel(*streams, samples.RUNTIME_REVISION, 3)
    worker_samples.register_directory(channel)
    channel.start()
    try:
        yield channel
    finally:
        await channel.close()


async def _finish(process: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        process.kill()
        await process.wait()
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
