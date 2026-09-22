# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise production process ownership and SDK proxies with an external package."""

import sys
from contextlib import closing
from pathlib import Path

import psutil
import pytest
from baqylau_extension_api.contracts.plugin import ExtensionPlugin
from baqylau_extension_api.models.lifecycle import ActivationReady, ActivationRequest, DeactivationRequest
from baqylau_extension_api.models.transforms import Drop

from tests.extension_api import samples, worker_samples
from tests.extension_host import worker_fixture


@pytest.mark.parametrize("layout", ["flat", "src"])
def test_worker_uses_private_installed_sdk(tmp_path: Path, runtime_wheels: Path, layout: str) -> None:
    """Real feature code can call back and transform without a host import path."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    if layout == "flat":
        (source / worker_fixture.SOURCE_PATH).rename(source / "sample_backend.py")
    factory, request = worker_fixture.worker_factory(tmp_path, source)
    with closing(factory.prepare_worker(request, worker_fixture.services(request))) as worker:
        assert worker.plugin.extension_info == request.environment.extension_info
        _exercise_plugin(worker.plugin)
        assert b"this is a log" in worker.diagnostics().stdout
        assert b"fixture worker loaded" in worker.diagnostics().stderr
    assert not psutil.pid_exists(worker.diagnostics().process_id)
    assert "sample_backend" not in sys.modules


def test_worker_uses_capture_after_source_change(tmp_path: Path, runtime_wheels: Path) -> None:
    """Editing a development backend cannot change an already selected package."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    factory, request = worker_fixture.worker_factory(tmp_path, source)
    (source / worker_fixture.SOURCE_PATH).write_text("raise AssertionError('mutable source loaded')", encoding="utf-8")
    with closing(factory.prepare_worker(request, worker_fixture.services(request))) as worker:
        _exercise_plugin(worker.plugin)


def test_worker_close_releases_its_environment(tmp_path: Path, runtime_wheels: Path) -> None:
    """Repeated close is safe and leaves the fixed package and mutable source intact."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    factory, request = worker_fixture.worker_factory(tmp_path, source)
    worker = factory.prepare_worker(request, worker_fixture.services(request))
    worker.close()
    worker.close()
    assert not tuple((tmp_path / "environments").iterdir())
    assert source.is_dir()
    assert worker.diagnostics().return_code == 0


def _exercise_plugin(plugin: ExtensionPlugin) -> None:
    activated = plugin.capabilities.lifecycle.activate(ActivationRequest(
        runtime_revision=samples.RUNTIME_REVISION, settings_revision=0,
    ))
    assert activated == ActivationReady(runtime_revision=samples.RUNTIME_REVISION)
    transformer = plugin.capabilities.raw_transformer
    assert transformer is not None
    assert transformer.transform(worker_samples.raw_request()).operations == (
        Drop(input_id="raw-1", reason="sample suppression"),
    )
    stopped = plugin.capabilities.lifecycle.deactivate(DeactivationRequest(
        runtime_revision=samples.RUNTIME_REVISION, reason="shutdown",
    ))
    assert not stopped.pending_job_ids
