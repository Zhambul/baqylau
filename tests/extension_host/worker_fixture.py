# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare real installed workers from captured external fixture code."""

from pathlib import Path

from baqylau_extension_api.contracts.services import ExtensionDirectory, ExtensionHostServices
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions.impl.process.factory import ProcessExtensionWorkers
from extensions.models.workers import WorkerPolicy
from tests.extension_api import worker_samples
from tests.extension_host import environment_fixture, package_fixture

EXAMPLE = Path(__file__).parents[1] / "extension_api" / "example.py"
SOURCE_PATH = "src/sample_backend.py"
DEFAULT_POLICY = WorkerPolicy()
PREFIX_PATH = Path(__file__).with_name("fixtures") / "worker_prefix.py.txt"


class WorkerDirectory(ExtensionDirectory):
    """Provide the peer used by the fixture's factory callback."""

    def list_extensions(self, request: DirectoryRequest) -> DirectorySnapshot:
        """Read the fixed fixture directory from a real worker callback.

        Returns:
            The declared peer that enables the raw transformer.

        """
        return worker_samples.directory_snapshot(request)


def write_package(directory: Path, wheels: Path, prefix: str | None = None) -> Path:
    """Put backend source, metadata, and all dependencies outside the host repository.

    Returns:
        A complete mutable source package ready for fixed capture.

    """
    source = environment_fixture.write_package(directory, wheels)
    original = package_fixture.read_manifest(source)
    request = worker_samples.load_request()
    assert original.backend is not None and request.manifest.backend is not None
    backend = request.manifest.backend.model_copy(update={"environment": original.backend.environment})
    package_fixture.save_manifest(source, request.manifest.model_copy(update={"backend": backend}))
    _write_source(source, prefix)
    return source


def worker_factory(
    directory: Path, source: Path, policy: WorkerPolicy = DEFAULT_POLICY,
) -> tuple[ProcessExtensionWorkers, WorkerLoadRequest]:
    """Use real captured bytes and a separate private environment for each worker.

    Returns:
        The production process factory and matching selected load request.

    """
    environments, artifact = environment_fixture.prepare_store(directory, source)
    original = worker_samples.load_request()
    identity = original.environment.extension_info.model_copy(update={"package_digest": artifact.package_digest})
    environment = original.environment.model_copy(update={"extension_info": identity})
    return (
        ProcessExtensionWorkers(environments, HostCallLedger(), policy),
        WorkerLoadRequest(manifest=artifact.manifest, environment=environment),
    )


def services(request: WorkerLoadRequest) -> ExtensionHostServices:
    """Bind the real callback adapter to the exact selected worker environment.

    Returns:
        Only the directory callback needed by this package.

    """
    return ExtensionHostServices(WorkerDirectory(), request.environment)


def _write_source(source: Path, prefix: str | None) -> None:
    selected = PREFIX_PATH.read_text(encoding="utf-8") if prefix is None else prefix
    code = EXAMPLE.read_text(encoding="utf-8")
    package_fixture.write_file(source, SOURCE_PATH, f"{selected}\n{code}".encode())
