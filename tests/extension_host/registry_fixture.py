# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply local protocol doubles for registry checks, not process isolation evidence."""

from dataclasses import replace
from typing import Literal

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models.directory import DirectoryEntry
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions.query_authority import QueryAuthority
from extensions.registry import ActiveExtensionRegistry
from extensions.registry_package import RegistryPackage
from extensions.registry_services import RegistryDirectory, RegistryServiceAccess
from extensions.registry_snapshot import RuntimeSnapshot, prepare_snapshot
from tests.extension_api import peer_example, samples, service_samples

QUERY_SECONDS = 5.0


def peer(owner: str, revision: str = samples.RUNTIME_REVISION) -> RegistryPackage:
    """Use the SDK example as a local protocol double with a checked identity.

    Returns:
        An enabled fixture selection without process ownership.

    """
    environment = service_samples.environment(owner).model_copy(update={"runtime_revision": revision})
    services = ExtensionHostServices(RegistryDirectory(ActiveExtensionRegistry("empty")), environment)
    return RegistryPackage(
        manifest=service_samples.manifest(owner),
        entry=DirectoryEntry(extension_info=environment.extension_info, state="enabled"),
        environment=environment, plugin=peer_example.PeerExample(services, "not_declared"),
    )


def inactive(
    package: RegistryPackage, state: Literal["disabled", "failed", "incompatible", "preparing"] = "disabled",
) -> RegistryPackage:
    """Retain installed metadata and remove live capabilities.

    Returns:
        A distinct non-active state at the same package identity.

    """
    entry = DirectoryEntry(extension_info=package.entry.extension_info, state=state)
    return replace(package, entry=entry, environment=None, plugin=None)


def snapshot(*packages: RegistryPackage, revision: str = samples.RUNTIME_REVISION) -> RuntimeSnapshot:
    """Capture a fixture catalog and proposed active set.

    Returns:
        A validated snapshot with stable activation order.

    """
    return prepare_snapshot(1, revision, packages)


def service_access(
    registry: ActiveExtensionRegistry, caller: RegistryPackage,
) -> RegistryServiceAccess:
    """Bind service calls to the fixture's real registry read boundary.

    Returns:
        The production host callback adapter with an independent grant ledger.

    """
    assert caller.environment is not None
    return RegistryServiceAccess(
        WorkerLoadRequest(manifest=caller.manifest, environment=caller.environment), registry, HostCallLedger(),
    )


def query_authority() -> QueryAuthority:
    """Give host-started queries their own call ledger and a short deadline.

    Returns:
        The authority.

    """
    return QueryAuthority(HostCallLedger(), QUERY_SECONDS)
