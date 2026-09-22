# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep transforms, shutdown, and failure evidence working after worker readiness."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.transforms import Drop
from baqylau_extension_api.runtime.models import ExtensionTransportError

from extensions.models.workers import WorkerPolicy
from extensions.worker_contract import ExtensionWorker
from tests.extension_api import samples, worker_samples
from tests.extension_host import worker_fault_fixture as faults, worker_fixture

OUTPUT_LIMIT = 4096
REQUEST_SECONDS = 2.0
POLICY = WorkerPolicy(request_seconds=REQUEST_SECONDS, stop_seconds=0.1, output_limit=OUTPUT_LIMIT)


def test_slow_live_call_does_not_hold_transforms(tmp_path: Path, runtime_wheels: Path) -> None:
    """The host reader and pure worker lane remain available during slow live work."""
    source = faults.write_package(tmp_path, runtime_wheels, "hang")
    factory, request = worker_fixture.worker_factory(tmp_path, source, POLICY)
    with closing(factory.prepare_worker(request, worker_fixture.services(request))) as worker:
        _exercise_concurrent_calls(worker)


@pytest.mark.parametrize("mode", ["exit", "flood"])
def test_runtime_failure_stops_worker(tmp_path: Path, runtime_wheels: Path, mode: str) -> None:
    """A crash or log flood cannot retain a live connection or unbounded log bytes."""
    source = faults.write_package(tmp_path, runtime_wheels, mode)
    factory, request = worker_fixture.worker_factory(tmp_path, source, POLICY)
    with closing(factory.prepare_worker(request, worker_fixture.services(request))) as worker:
        with pytest.raises(ExtensionTransportError):
            worker.plugin.capabilities.lifecycle.activate(faults.activation())
        faults.wait_for_exit(worker)
        evidence = worker.diagnostics()
        assert evidence.failure is not None
        assert len(evidence.stdout) + len(evidence.stderr) <= OUTPUT_LIMIT


def test_close_revokes_worker_call_authority(tmp_path: Path, runtime_wheels: Path) -> None:
    """A retained host grant cannot authorize a peer query after worker removal."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    factory, request = worker_fixture.worker_factory(tmp_path, source, POLICY)
    with (
        closing(factory.prepare_worker(request, worker_fixture.services(request))) as worker,
        factory.ledger.root(request.environment, samples.processing_context().scope, 10),
    ):
        worker.close()
        with pytest.raises(ExtensionContractError, match="matching host call"):
            factory.ledger.require_call(request.environment, samples.processing_context().scope)


def _exercise_concurrent_calls(worker: ExtensionWorker) -> None:
    with ThreadPoolExecutor(max_workers=1) as callers:
        pending = callers.submit(worker.plugin.capabilities.lifecycle.activate, faults.activation())
        faults.wait_for_activation(worker)
        transformer = worker.plugin.capabilities.raw_transformer
        assert transformer is not None
        assert transformer.transform(worker_samples.raw_request()).operations == (
            Drop(input_id="raw-1", reason="sample suppression"),
        )
        with pytest.raises(ExtensionTransportError):
            pending.result(timeout=3)


def test_close_releases_an_active_call(tmp_path: Path, runtime_wheels: Path) -> None:
    """Shutdown rejects a pending wait and kills the worker's blocked feature thread."""
    source = faults.write_package(tmp_path, runtime_wheels, "hang")
    factory, request = worker_fixture.worker_factory(tmp_path, source, POLICY)
    with closing(factory.prepare_worker(request, worker_fixture.services(request))) as worker:
        _close_during_call(worker)
    assert not tuple((tmp_path / "environments").iterdir())


def _close_during_call(worker: ExtensionWorker) -> None:
    with ThreadPoolExecutor(max_workers=1) as callers:
        pending = callers.submit(worker.plugin.capabilities.lifecycle.activate, faults.activation())
        faults.wait_for_activation(worker)
        worker.close()
        with pytest.raises(ExtensionTransportError):
            pending.result(timeout=3)
        assert worker.diagnostics().return_code is not None
