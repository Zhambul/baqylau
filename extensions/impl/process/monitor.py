# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop a failed worker while keeping process evidence available to its owner."""

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import partial

from anyio.abc import Process
from baqylau_extension_api.runtime.channel import RpcChannel

from extensions.impl.process.output import WorkerOutput
from extensions.process_groups import kill_process_group


class WorkerStoppedError(RuntimeError):
    """A worker process or transport ended before its owner requested shutdown."""


class WorkerMonitor:
    """Observe process exit, transport exit, and both log streams on the reader loop."""

    def __init__(self, process: Process, channel: RpcChannel, output: WorkerOutput, revoke: Callable[[], None]) -> None:
        """Bind monitor work to one process generation."""
        self.process = process
        self.channel = channel
        self.output = output
        self._revoke = revoke
        self._closing = False
        self._tasks: list[asyncio.Task[None]] = []
        self._watcher: asyncio.Task[None] | None = None

    def start(self, cleanup: AsyncExitStack) -> None:
        """Start log drainage before the worker factory receives its load request."""
        self._tasks.append(asyncio.create_task(self._process_exit()))
        self._tasks.append(asyncio.create_task(self._transport_exit()))
        if self.process.stdout is not None:
            drain = partial(self.output.drain, self.process.stdout, error=False)
            self._tasks.append(asyncio.create_task(drain()))
        if self.process.stderr is not None:
            drain_error = partial(self.output.drain, self.process.stderr, error=True)
            self._tasks.append(asyncio.create_task(drain_error()))
        self._watcher = asyncio.create_task(self._watch())
        cleanup.push_async_callback(self.close)

    async def close(self) -> None:
        """Stop monitor tasks before their channel and process resources are released."""
        self._closing = True
        self._revoke()
        if self._watcher is not None:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
        for pending in self._tasks:
            pending.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _watch(self) -> None:
        finished, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_EXCEPTION)
        for pending in finished:
            if not pending.cancelled():
                pending.exception()
        if not self._closing:
            self._revoke()
            kill_process_group(self.process.pid)
            await self.channel.close()

    async def _process_exit(self) -> None:
        await self.process.wait()
        self.output.exited(expected=self._closing)
        if not self._closing:
            message = "extension worker process exited"
            raise WorkerStoppedError(message)

    async def _transport_exit(self) -> None:
        await asyncio.shield(self.channel.completion)
        if not self._closing:
            self.output.transport_closed()
            message = "extension worker transport closed"
            raise WorkerStoppedError(message)
