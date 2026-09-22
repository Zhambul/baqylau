# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold a registry read for the full peer callback, not only provider lookup."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.service_access import ExtensionServiceAccess
from baqylau_extension_api.contracts.services import ExtensionDirectory
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.models.services import (
    ServiceQueryRequest,
    ServiceQueryResult,
    ServiceResolveRequest,
    ServiceResolveResult,
)
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.service_dispatch import HostServiceAccess
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions.registry_contract import ExtensionRegistry


@dataclass(frozen=True)
class RegistryDirectory(ExtensionDirectory):
    """Read installed and enabled peer metadata from the active registry boundary."""

    registry: ExtensionRegistry

    def list_extensions(self, request: DirectoryRequest) -> DirectorySnapshot:
        """Copy safe metadata while holding the registry read.

        Returns:
            The exact catalog and runtime revisions of the selected entries.

        """
        with self.registry.read_snapshot() as selected:
            return selected.snapshot.list_extensions(request)


@dataclass(frozen=True)
class RegistryServiceAccess(ExtensionServiceAccess):
    """Bind one worker connection to checked peer reads in one published set."""

    caller: WorkerLoadRequest
    registry: ExtensionRegistry
    calls: HostCallLedger

    def resolve_service(self, service_request: ServiceResolveRequest) -> ServiceResolveResult:
        """Permit metadata lookup before activation, without granting a live query.

        Returns:
            Declared peer metadata or a typed unavailable result.

        """
        with self.registry.read_snapshot() as selected:
            return HostServiceAccess(self.caller, selected.snapshot, self.calls).resolve_service(service_request)

    def query_service(self, service_query: ServiceQueryRequest) -> ServiceQueryResult:
        """Keep the provider alive until authorization and the full peer call finish.

        Returns:
            A checked result from the same runtime selection.

        Raises:
            ExtensionContractError: If the caller is no longer in the active set.

        """
        with self.registry.read_snapshot() as selected:
            provider = selected.snapshot.get_service_provider(
                self.caller.environment.extension_info.extension_id, service_query.binding.scope,
            )
            if provider is None or provider.environment != self.caller.environment:
                message = "peer query caller is not in the active registry selection"
                raise ExtensionContractError(message)
            return HostServiceAccess(self.caller, selected.snapshot, self.calls).query_service(service_query)
