# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep slow work separate from pure transforms and the transport reader."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from baqylau_extension_api.runtime.context import processing_scope
from baqylau_extension_api.runtime.contract import EncodedHandler
from baqylau_extension_api.runtime.models import ExecutionKind, ExtensionTransportError

MAX_PENDING_CALLS = 16


class ExecutionLane:
    """Bound queued and running calls, including calls whose wait was canceled."""

    def __init__(self, workers: int, *, pure: bool) -> None:
        """Create an executor with no worker threads until it receives work."""
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extension-call")
        self._pure = pure
        self._pending = 0
        self._closed = False

    async def run(self, dispatch: EncodedHandler, encoded: str) -> str:
        """Submit work without blocking the JSON-RPC reader.

        Returns:
            The encoded capability result.

        Raises:
            ExtensionTransportError: If the lane is closed or full.

        """
        if self._closed or self._pending >= MAX_PENDING_CALLS:
            message = "extension execution lane is closed or full"
            raise ExtensionTransportError(message)
        self._pending += 1
        context = copy_context()
        future = asyncio.get_running_loop().run_in_executor(
            self._executor, context.run, self._invoke, dispatch, encoded,
        )
        future.add_done_callback(self._completed)
        return await asyncio.shield(future)

    def close(self) -> None:
        """Reject new work and cancel queued work; running threads need worker cleanup."""
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _invoke(self, dispatch: EncodedHandler, encoded: str) -> str:
        with processing_scope(pure=self._pure):
            return dispatch.invoke(encoded)

    def _completed(self, future: asyncio.Future[str]) -> None:
        self._pending -= 1
        if not future.cancelled():
            # Retrieve abandoned exceptions after a caller timeout.
            future.exception()


class WorkerLanes:
    """Reserve separate capacity for transforms, live work, and cancellation."""

    def __init__(self) -> None:
        """Create independent execution capacity for transforms and live calls."""
        self._pure = ExecutionLane(1, pure=True)
        self._live = ExecutionLane(4, pure=False)
        self._control = ExecutionLane(1, pure=False)

    async def run(self, kind: ExecutionKind, dispatch: EncodedHandler, encoded: str) -> str:
        """Run a callback in its declared execution lane.

        Returns:
            The callback's encoded result.

        """
        if kind == "pure":
            return await self._pure.run(dispatch, encoded)
        if kind == "control":
            return await self._control.run(dispatch, encoded)
        return await self._live.run(dispatch, encoded)

    def close(self) -> None:
        """Close all execution lanes without waiting on untrusted work."""
        self._pure.close()
        self._live.close()
        self._control.close()
