# Copyright (c) 2026 Zhambyl Yermagambet
"""Launch the installed SDK with one inherited socket and one fixed package path."""

import os
import socket
from asyncio.subprocess import DEVNULL, PIPE
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import MappingProxyType

import anyio
from anyio.abc import Process

from extensions.environment_contract import WorkerEnvironment
from extensions.impl.process.shutdown import stop_process

LAUNCH_ENVIRONMENT = MappingProxyType({"PATH": os.defpath, "LC_ALL": "C"})


@dataclass(frozen=True)
class WorkerProcess:
    """Keep the direct process and parent socket under the same owner."""

    process: Process
    endpoint: socket.socket


@asynccontextmanager
async def launched_worker(
    environment: WorkerEnvironment, revision: str, stop_seconds: float,
) -> AsyncIterator[WorkerProcess]:
    """Inherit only the private RPC descriptor; keep logs on separate bounded pipes.

    Yields:
        The owned process and parent socket before feature loading.

    """
    endpoints = socket.socketpair()
    with endpoints[0], endpoints[1]:
        process = await _launch(environment, revision, endpoints[1].fileno())
        endpoints[1].close()
        try:
            yield WorkerProcess(process, endpoints[0])
        finally:
            await stop_process(process, stop_seconds)


async def _launch(environment: WorkerEnvironment, revision: str, descriptor: int) -> Process:
    command = (
        str(environment.executable), "-I", "-B", "-u", "-m", "baqylau_extension_api.runtime.worker",
        "--rpc-fd", str(descriptor), "--runtime-revision", revision,
        "--package-directory", environment.artifact.directory,
    )
    return await anyio.open_process(
        command, stdin=DEVNULL, stdout=PIPE, stderr=PIPE, cwd=environment.artifact.directory,
        env=LAUNCH_ENVIRONMENT,
        pass_fds=(descriptor,), start_new_session=True,
    )
