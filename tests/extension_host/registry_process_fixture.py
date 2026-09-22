# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare private feature workers with production registry service callbacks."""

from contextlib import ExitStack, closing
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import directory, lifecycle
from baqylau_extension_api.runtime import call_grants, worker_models

from extensions.impl.process.factory import ProcessExtensionWorkers
from extensions.registry_package import RegistryPackage
from extensions.registry_services import RegistryDirectory, RegistryServiceAccess
from tests.extension_api import service_samples as peers
from tests.extension_host import environment_fixture, package_fixture
from tests.extension_host.registry_memory_fixture import MemoryRegistry

EXAMPLE = Path(__file__).parents[1] / "extension_api" / "peer_example.py"


@dataclass(frozen=True)
class RegistryWorkerHost:
    """Keep the real ledger and registry shared by independently started workers."""

    root: Path
    registry: MemoryRegistry
    calls: call_grants.HostCallLedger

    def prepare_peer(self, owner: str, wheels: Path, cleanup: ExitStack) -> RegistryPackage:
        """Capture and prepare one feature without publishing it as active.

        Returns:
            The exact activated worker identity and public capability proxies.

        """
        environments, artifact = environment_fixture.prepare_store(
            self.root / owner, write_peer(self.root / owner, wheels, owner),
        )
        identity = peers.environment(owner).extension_info.model_copy(update={
            "package_digest": artifact.package_digest,
        })
        request = worker_models.WorkerLoadRequest(manifest=artifact.manifest, environment=(
            peers.environment(owner).model_copy(update={"extension_info": identity})
        ))
        worker = cleanup.enter_context(closing(ProcessExtensionWorkers(environments, self.calls).prepare_worker(
            request, self.host_services(request),
        )))
        assert worker.plugin.capabilities.lifecycle.activate(lifecycle.ActivationRequest(
            runtime_revision=request.environment.runtime_revision, settings_revision=0,
        )).kind == "ready"
        return RegistryPackage(
            manifest=request.manifest, entry=directory.DirectoryEntry(extension_info=identity, state="enabled"),
            environment=request.environment, plugin=worker.plugin,
        )

    def host_services(self, request: worker_models.WorkerLoadRequest) -> ExtensionHostServices:
        """Bind callbacks before the feature factory starts.

        Returns:
            A live registry directory and checked service access for this worker.

        """
        return ExtensionHostServices(
            RegistryDirectory(self.registry), request.environment,
            RegistryServiceAccess(request, self.registry, self.calls),
        )


def write_peer(root: Path, wheels: Path, owner: str) -> Path:
    """Write a complete peer package outside the host source tree.

    Returns:
        The mutable package path before capture.

    """
    source = environment_fixture.write_package(root, wheels)
    original = package_fixture.read_manifest(source)
    manifest = peers.manifest(owner)
    assert original.backend is not None and manifest.backend is not None
    backend = manifest.backend.model_copy(update={"environment": original.backend.environment})
    package_fixture.save_manifest(source, manifest.model_copy(update={"backend": backend}))
    package_fixture.write_file(source, "src/peer_backend.py", EXAMPLE.read_bytes())
    return source
