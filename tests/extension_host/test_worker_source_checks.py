# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject feature names that could replace installed SDK or standard modules."""

from pathlib import Path

import pytest

from extensions.worker_errors import WorkerStartError
from tests.extension_host import package_fixture, worker_fixture


@pytest.mark.parametrize("module", ["json", "baqylau_extension_api"])
def test_worker_cannot_shadow_installed_modules(tmp_path: Path, runtime_wheels: Path, module: str) -> None:
    """A colliding package can be inspected but its feature source is never imported."""
    source = worker_fixture.write_package(tmp_path, runtime_wheels)
    _rename_backend(source, module)
    factory, request = worker_fixture.worker_factory(tmp_path, source)
    with pytest.raises(WorkerStartError) as caught:
        factory.prepare_worker(request, worker_fixture.services(request))
    assert b"fixture worker loaded" not in caught.value.diagnostics.stderr
    assert not tuple((tmp_path / "environments").iterdir())


def _rename_backend(source: Path, module: str) -> None:
    manifest = package_fixture.read_manifest(source)
    assert manifest.backend is not None
    backend = manifest.backend.model_copy(update={"module": module})
    package_fixture.save_manifest(source, manifest.model_copy(update={"backend": backend}))
    (source / worker_fixture.SOURCE_PATH).rename(source / "src" / f"{module}.py")
