# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep failed startup and closed worker calls outside the active application state."""

from pathlib import Path
from threading import enumerate as thread_inventory

import psutil
import pytest
from baqylau_extension_api.models.lifecycle import ActivationRequest
from baqylau_extension_api.runtime.models import ExtensionTransportError

from extensions.models.workers import WorkerPolicy
from extensions.worker_errors import WorkerStartError
from tests.extension_host import worker_fixture

FAILURE_PREFIXES = (
    "raise RuntimeError('fixture load failure')\n",
    "import time\ntime.sleep(60)\n",
    "import os\nwhile True:\n    os.write(1, b'x' * 4096)\n",
    "import os\nos._exit(7)\n",
)
OUTPUT_LIMIT = 4096
POLICY = WorkerPolicy(request_seconds=1.0, stop_seconds=0.1, output_limit=OUTPUT_LIMIT)


@pytest.mark.parametrize("prefix", FAILURE_PREFIXES)
def test_failed_worker_start_releases_resources(tmp_path: Path, runtime_wheels: Path, prefix: str) -> None:
    """A factory error, hang, flood, or process exit leaves no private environment."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels, prefix)
    factory, request = worker_fixture.worker_factory(tmp_path, source, POLICY)
    before = tuple(thread.name for thread in thread_inventory())
    with pytest.raises(WorkerStartError) as caught:
        factory.prepare_worker(request, worker_fixture.services(request))
    assert not tuple((tmp_path / "environments").iterdir())
    assert source.is_dir()
    assert tuple(thread.name for thread in thread_inventory()) == before
    assert not psutil.pid_exists(caught.value.diagnostics.process_id)
    assert caught.value.diagnostics.return_code is not None


def test_worker_rejects_changed_manifest(tmp_path: Path, runtime_wheels: Path) -> None:
    """A catalog request cannot select new metadata for an existing package digest."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    factory, request = worker_fixture.worker_factory(tmp_path, source)
    manifest = request.manifest.model_copy(update={"name": "Changed"})
    changed = request.model_copy(update={"manifest": manifest})
    with pytest.raises(ValueError, match="manifest"):
        factory.prepare_worker(changed, worker_fixture.services(changed))
    assert not tuple((tmp_path / "environments").iterdir())


def test_closed_worker_rejects_calls(tmp_path: Path, runtime_wheels: Path) -> None:
    """Saved proxies cannot use a closed event loop or restart a stopped process."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    factory, request = worker_fixture.worker_factory(tmp_path, source)
    worker = factory.prepare_worker(request, worker_fixture.services(request))
    worker.close()
    activation = ActivationRequest(runtime_revision=request.environment.runtime_revision, settings_revision=0)
    with pytest.raises(ExtensionTransportError, match="closed"):
        worker.plugin.capabilities.lifecycle.activate(activation)
