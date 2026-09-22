# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep canceled waits in the execution limit until actual work stops."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event

import pytest
from baqylau_extension_api.runtime.execution import MAX_PENDING_CALLS, ExecutionLane
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import rpc_callbacks


def test_canceled_work_retains_queue_capacity() -> None:
    """A caller cannot bypass the worker bound through repeated cancellation."""
    asyncio.run(_bounded_cancel())


async def _bounded_cancel() -> None:
    with _held_lane() as (lane, dispatch):
        pending = tuple(
            asyncio.create_task(lane.run(dispatch, "input")) for _ in range(MAX_PENDING_CALLS)
        )
        await asyncio.sleep(0)
        for call in pending:
            call.cancel()
        outcomes = await asyncio.gather(*pending, return_exceptions=True)
        assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes)
        assert dispatch.entered.is_set()
        with pytest.raises(ExtensionTransportError, match="full"):
            await lane.run(dispatch, "overflow")


def test_closed_lane_rejects_new_work() -> None:
    """Reject a new call after worker teardown."""
    asyncio.run(_closed_lane())


async def _closed_lane() -> None:
    with _held_lane() as (lane, dispatch):
        lane.close()
        with pytest.raises(ExtensionTransportError, match="closed"):
            await lane.run(dispatch, "closed")


@contextmanager
def _held_lane() -> Iterator[tuple[ExecutionLane, rpc_callbacks.WaitingCall]]:
    lane = ExecutionLane(1, pure=False)
    dispatch = rpc_callbacks.WaitingCall(Event(), Event())
    try:
        yield lane, dispatch
    finally:
        lane.close()
        dispatch.release.set()
