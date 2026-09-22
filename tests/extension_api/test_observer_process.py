# Copyright (c) 2026 Zhambyl Yermagambet
"""Check observer execution, live callbacks, and stops across a real process."""

import asyncio
import sys
from pathlib import Path

import pytest
from baqylau_extension_api.models.observer_jobs import ObservationCancelRequest
from baqylau_extension_api.models.transforms import Keep
from baqylau_extension_api.runtime.bridge import RpcBridge
from baqylau_extension_api.runtime.models import ExtensionTransportError
from baqylau_extension_api.runtime.observers import RemoteObserver
from baqylau_extension_api.runtime.proxies import RemoteRawTransformer

from tests.extension_api import observer_process, observer_samples, operation_process, worker_samples


def test_observer_output_keeps_cause(tmp_path: Path) -> None:
    """The host can receive output without importing the feature backend."""
    asyncio.run(_round_trip(tmp_path))
    assert "observer_backend" not in sys.modules


async def _round_trip(directory: Path) -> None:
    async with observer_process.running_observer(directory) as caller:
        request = observer_samples.request()
        response = await asyncio.to_thread(RemoteObserver(caller).observe, request)
        assert response.status == "succeeded"
        assert response.binding == request.binding
        assert len(response.observations) == 1
        assert response.observations[0].causes == (request.binding.event_id,)
        assert await operation_process.read_counts(caller) == "executions:1;active:0;peers:1"


def test_slow_observer_allows_other_lanes(tmp_path: Path) -> None:
    """Live work must not hold the pure or cancellation lane."""
    asyncio.run(_slow_observer(tmp_path))


async def _slow_observer(directory: Path) -> None:
    async with observer_process.running_observer(directory) as caller:
        request = observer_samples.request('"wait"')
        async with asyncio.TaskGroup() as group:
            pending = group.create_task(asyncio.to_thread(RemoteObserver(caller).observe, request))
            await operation_process.wait_for_active(caller)
            transformed = await asyncio.to_thread(RemoteRawTransformer(caller).transform, worker_samples.raw_request())
            assert transformed.operations == (Keep(input_id="raw-1"),)
            assert not pending.done()
            await _cancel_attempt(caller)
        assert pending.result().status == "canceled"
        assert await operation_process.read_counts(caller) == "executions:1;active:0;peers:1"


async def _cancel_attempt(caller: RpcBridge) -> None:
    request = ObservationCancelRequest(binding=observer_samples.request().binding, reason="Stop fixture work.")
    stale_binding = request.binding.model_copy(update={"call_id": "old"})
    stale = request.model_copy(update={"binding": stale_binding})
    response = await asyncio.to_thread(RemoteObserver(caller).cancel_observation, stale)
    assert response.status == "not_running"
    assert await operation_process.read_counts(caller) == "executions:1;active:1;peers:1"
    response = await asyncio.to_thread(RemoteObserver(caller).cancel_observation, request)
    assert response.status == "requested"


@pytest.mark.parametrize("change", ["runtime", "replay", "schema"])
def test_invalid_input_does_not_run_observer(tmp_path: Path, change: str) -> None:
    """Invalid calls leave no test execution record and do not stop the worker."""
    asyncio.run(_reject_input(tmp_path, change))


async def _reject_input(directory: Path, change: str) -> None:
    async with observer_process.running_observer(directory) as caller:
        request = observer_samples.request("42" if change == "schema" else '"complete"')
        if change == "runtime":
            binding = request.binding.model_copy(update={"runtime_revision": "old"})
            request = request.model_copy(update={"binding": binding})
        if change == "replay":
            request = request.model_copy(update={"mode": "replay"})
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteObserver(caller).observe, request)
        assert await operation_process.read_counts(caller) == "executions:0;active:0;peers:1"
        response = await asyncio.to_thread(RemoteObserver(caller).observe, observer_samples.request())
        assert response.status == "succeeded"
