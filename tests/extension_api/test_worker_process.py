# Copyright (c) 2026 Zhambyl Yermagambet
"""Call an external backend through the same protocols as a local instance."""

import asyncio
import sys
from pathlib import Path

import pytest
from baqylau_extension_api.models.lifecycle import ActivationReady, ActivationRequest, DeactivationRequest
from baqylau_extension_api.models.transforms import Drop
from baqylau_extension_api.runtime import methods, models
from baqylau_extension_api.runtime.bridge import RpcBridge
from baqylau_extension_api.runtime.proxies import RemoteCanonicalTransformer, RemoteLifecycle, RemoteRawTransformer
from baqylau_extension_api.runtime.worker_models import WorkerReady
from pydantic import TypeAdapter

from tests.extension_api import process_fixture, samples, worker_samples


def test_external_worker_protocols(tmp_path: Path) -> None:
    """Load outside the repo, call back from the factory, and transform raw input."""
    asyncio.run(_external_round_trip(tmp_path))
    assert "sample_backend" not in sys.modules


async def _external_round_trip(directory: Path) -> None:
    async with process_fixture.running_worker(directory) as worker:
        request = worker_samples.load_request()
        ready = await worker.channel.call(methods.LOAD, request, TypeAdapter(WorkerReady))
        assert ready.extension_info == samples.worker_environment().extension_info
        assert ready.capabilities == ("lifecycle", "raw_transformer", "canonical_transformer")
        caller = RpcBridge(worker.channel, asyncio.get_running_loop(), 3)
        await _exercise_protocols(caller)


async def _exercise_protocols(caller: RpcBridge) -> None:
    lifecycle = RemoteLifecycle(caller)
    request = ActivationRequest(runtime_revision=samples.RUNTIME_REVISION, settings_revision=0)
    active = await asyncio.to_thread(lifecycle.activate, request)
    assert active == ActivationReady(runtime_revision=samples.RUNTIME_REVISION)
    await _transform(caller)
    await _canonical_transform(caller)
    stopped = await asyncio.to_thread(
        lifecycle.deactivate, DeactivationRequest(runtime_revision=samples.RUNTIME_REVISION, reason="shutdown"),
    )
    assert not stopped.pending_job_ids


async def _transform(caller: RpcBridge) -> None:
    transformer = RemoteRawTransformer(caller)
    transformed = await asyncio.to_thread(transformer.transform, worker_samples.raw_request())
    assert transformed.operations == (Drop(input_id="raw-1", reason="sample suppression"),)


async def _canonical_transform(caller: RpcBridge) -> None:
    transformer = RemoteCanonicalTransformer(caller)
    transformed = await asyncio.to_thread(transformer.transform, worker_samples.canonical_request())
    assert transformed.operations == (Drop(input_id="core-1", reason="sample suppression"),)
    complete = await asyncio.to_thread(transformer.transform, worker_samples.canonical_request(complete=True))
    assert complete.operations == (Drop(input_id="core-1", reason="complete prior state"),)


def test_worker_rejects_reload_and_inner_revision(tmp_path: Path) -> None:
    """Reject a second load and a stale request inside a valid transport envelope."""
    asyncio.run(_rejected_calls(tmp_path))


async def _rejected_calls(directory: Path) -> None:
    async with process_fixture.running_worker(directory) as worker:
        request = worker_samples.load_request()
        await worker.channel.call(methods.LOAD, request, TypeAdapter(WorkerReady))
        with pytest.raises(models.ExtensionTransportError):
            await worker.channel.call(methods.LOAD, request, TypeAdapter(WorkerReady))
        lifecycle = RemoteLifecycle(RpcBridge(worker.channel, asyncio.get_running_loop(), 3))
        stale = ActivationRequest(runtime_revision="stale-runtime", settings_revision=0)
        with pytest.raises(models.ExtensionTransportError):
            await asyncio.to_thread(lifecycle.activate, stale)
