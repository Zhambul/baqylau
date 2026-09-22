# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep cancellation usable when every live worker and queue position is full."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Event

import pytest
from baqylau_extension_api.runtime.execution import MAX_PENDING_CALLS, WorkerLanes
from baqylau_extension_api.runtime.models import ExecutionKind, ExtensionTransportError

from tests.extension_api import rpc_callbacks


@dataclass(frozen=True)
class SaturatedWorker:
    """Keep all live threads and pending calls inside one test resource."""

    lanes: WorkerLanes
    waiting: tuple[rpc_callbacks.WaitingCall, ...]
    pending: tuple[asyncio.Task[str], ...]


def test_control_runs_when_live_lane_is_full() -> None:
    """Fill all live capacity and still finish a separate control request."""
    asyncio.run(_saturated_live())


async def _saturated_live() -> None:
    async with _held_workers() as worker:
        waits = (asyncio.to_thread(dispatch.entered.wait, 2) for dispatch in worker.waiting)
        started = await asyncio.gather(*waits)
        assert all(started)
        with pytest.raises(ExtensionTransportError, match="full"):
            await worker.lanes.run("live", rpc_callbacks.ImmediateCall(), "overflow")
        control = worker.lanes.run("control", rpc_callbacks.ImmediateCall(), "stop")
        assert await asyncio.wait_for(control, 1) == "stop"
        assert not any(call.done() for call in worker.pending)


@asynccontextmanager
async def _held_workers() -> AsyncIterator[SaturatedWorker]:
    lanes = WorkerLanes()
    release = Event()
    waiting = tuple(rpc_callbacks.waiting_call(release) for _ in range(4))
    pending = tuple(asyncio.create_task(lanes.run(
        "live", waiting[index % len(waiting)], "input",
    )) for index in range(MAX_PENDING_CALLS))
    try:
        yield SaturatedWorker(lanes, waiting, pending)
    finally:
        release.set()
        await _finish_worker(lanes, pending)


async def _finish_worker(lanes: WorkerLanes, pending: tuple[asyncio.Task[str], ...]) -> None:
    outcomes = await asyncio.gather(*pending, return_exceptions=True)
    lanes.close()
    assert all(isinstance(outcome, str) for outcome in outcomes)


@pytest.mark.parametrize("kind", ["pure", "live", "control"])
def test_closed_worker_rejects_all_lanes(kind: ExecutionKind) -> None:
    """Reject new calls after teardown, including the reserved control lane."""
    asyncio.run(_closed_worker(kind))


async def _closed_worker(kind: ExecutionKind) -> None:
    lanes = WorkerLanes()
    lanes.close()
    with pytest.raises(ExtensionTransportError, match="closed"):
        await lanes.run(kind, rpc_callbacks.ImmediateCall(), "closed")
