# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep observer recovery separate from execution, including after worker loss."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.models.observer_jobs import ObservationReconcileRequest
from baqylau_extension_api.runtime.models import ExtensionTransportError
from baqylau_extension_api.runtime.observers import RemoteObserver

from tests.extension_api import observer_process, observer_samples, operation_process


def test_observer_recovery_does_not_execute(tmp_path: Path) -> None:
    """A separate recovery call checks proof without dispatching observe again."""
    asyncio.run(_recover(tmp_path))


async def _recover(directory: Path) -> None:
    async with observer_process.running_observer(directory) as caller:
        request = observer_samples.request('"unknown"')
        uncertain = await asyncio.to_thread(RemoteObserver(caller).observe, request)
        assert uncertain.status == "outcome_unknown"
        recovery = ObservationReconcileRequest(
            observation=request, receipt=uncertain.receipt,
        )
        recovery = _recovery_attempt(recovery)
        response = await asyncio.to_thread(RemoteObserver(caller).reconcile_observation, recovery)
        assert response.status == "succeeded"
        assert response.binding == recovery.observation.binding
        assert response.observations[0].causes == (request.binding.event_id,)
        assert await operation_process.read_counts(caller) == "executions:1;active:0;peers:1"
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteObserver(caller).observe, request)


def test_lost_observer_proof_does_not_execute(tmp_path: Path) -> None:
    """Lost in-memory proof produces uncertainty, not another external action."""
    recovery = asyncio.run(_capture_uncertain(tmp_path))
    asyncio.run(_after_restart(tmp_path, recovery))


async def _capture_uncertain(directory: Path) -> ObservationReconcileRequest:
    async with observer_process.running_observer(directory) as caller:
        request = observer_samples.request('"unknown"')
        response = await asyncio.to_thread(RemoteObserver(caller).observe, request)
        assert response.status == "outcome_unknown"
        binding = request.binding.model_copy(update={"call_id": "recover-2"})
        return ObservationReconcileRequest(
            observation=request.model_copy(update={"binding": binding}), receipt=response.receipt,
        )


async def _after_restart(directory: Path, request: ObservationReconcileRequest) -> None:
    async with observer_process.running_observer(directory) as caller:
        response = await asyncio.to_thread(RemoteObserver(caller).reconcile_observation, request)
        assert response.status == "outcome_unknown"
        assert response.receipt == request.receipt
        assert await operation_process.read_counts(caller) == "executions:0;active:0;peers:1"


def _recovery_attempt(request: ObservationReconcileRequest) -> ObservationReconcileRequest:
    binding = request.observation.binding.model_copy(update={"call_id": "recover-1"})
    observation = request.observation.model_copy(update={"binding": binding})
    return request.model_copy(update={"observation": observation})
