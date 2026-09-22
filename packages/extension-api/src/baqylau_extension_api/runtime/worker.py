# Copyright (c) 2026 Zhambyl Yermagambet
"""Run the installed SDK worker on an inherited local socket."""

import argparse
import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.bootstrap import WorkerBootstrap
from baqylau_extension_api.runtime.bridge import RpcBridge
from baqylau_extension_api.runtime.channel import RpcChannel, stream_channel
from baqylau_extension_api.runtime.models import MAX_HEADER_BYTES

REQUEST_SECONDS = 30.0


class WorkerArguments(argparse.Namespace):
    """Hold the host-owned socket, revision, and checked package directory."""

    rpc_fd: int
    runtime_revision: str
    package_directory: Path


def main() -> None:
    """Start an isolated worker; stdout and stderr never carry RPC frames."""
    parser = argparse.ArgumentParser(description="Run one Baqylau extension worker.")
    parser.add_argument("--rpc-fd", type=int, required=True)
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--package-directory", type=Path, required=True)
    arguments = WorkerArguments()
    parser.parse_args(namespace=arguments)
    asyncio.run(_serve(arguments))


async def _serve(arguments: WorkerArguments) -> None:
    async with _worker_channel(arguments) as channel:
        caller = RpcBridge(channel, asyncio.get_running_loop(), REQUEST_SECONDS)
        channel.register_async(methods.LOAD, WorkerBootstrap(
            channel, caller, arguments.runtime_revision, arguments.package_directory,
        ))
        channel.start()
        await channel.completion


@asynccontextmanager
async def _worker_channel(arguments: WorkerArguments) -> AsyncIterator[RpcChannel]:
    with socket.socket(fileno=arguments.rpc_fd) as inherited:
        streams = await asyncio.open_connection(sock=inherited, limit=MAX_HEADER_BYTES)
        channel = stream_channel(*streams, arguments.runtime_revision, REQUEST_SECONDS)
        try:
            yield channel
        finally:
            await channel.close()


if __name__ == "__main__":
    main()
