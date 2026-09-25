# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold a registry read for the full peer callback, not only provider lookup."""

from dataclasses import dataclass

from baqylau_extension_api.contracts.service_access import ExtensionServiceAccess
from baqylau_extension_api.contracts.services import ExtensionDirectory
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models import service_jobs, services
from baqylau_extension_api.models.directory import DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.service_dispatch import HostServiceAccess
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions.peer_callers import PeerCaller
from extensions.peer_jobs import PeerJobs
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
    peer_jobs: PeerJobs | None = None

    def resolve_service(self, service_request: services.ServiceResolveRequest) -> services.ServiceResolveResult:
        """Permit metadata lookup before activation, without granting a live query.

        Returns:
            Declared peer metadata or a typed unavailable result.

        """
        with self.registry.read_snapshot() as selected:
            return HostServiceAccess(self.caller, selected.snapshot, self.calls).resolve_service(service_request)

    def query_service(self, service_query: services.ServiceQueryRequest) -> services.ServiceQueryResult:
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

    def submit_service_command(
        self, service_command: service_jobs.ServiceCommandRequest,
    ) -> service_jobs.ServiceJobResult:
        """Accept one public peer command as a durable job in the published set.

        Returns:
            The accepted job reference, or an unavailable service when this host has no job store.

        """
        with self.registry.read_snapshot() as selected:
            if self.peer_jobs is None:
                return services.ServiceUnavailable(binding=service_command.binding, reason="not_provided")
            return self.peer_jobs.submit(PeerCaller(self.caller, self.calls, selected.snapshot), service_command)

    def read_service_job(self, service_job: service_jobs.ServiceJobRequest) -> service_jobs.ServiceJobResult:
        """Read one peer job that this caller submitted.

        Returns:
            The stored job state, or an unavailable service when this host has no job store.

        """
        with self.registry.read_snapshot() as selected:
            if self.peer_jobs is None:
                return services.ServiceUnavailable(binding=service_job.binding, reason="not_provided")
            return self.peer_jobs.read(PeerCaller(self.caller, self.calls, selected.snapshot), service_job)

    def cancel_service_job(
        self, service_job_cancel: service_jobs.ServiceJobCancelRequest,
    ) -> service_jobs.ServiceJobCancelResult:
        """Ask the owning peer to stop an attempt that this caller submitted.

        Returns:
            The checked acknowledgment, or an unavailable service when this host has no job store.

        """
        with self.registry.read_snapshot() as selected:
            if self.peer_jobs is None:
                return services.ServiceUnavailable(binding=service_job_cancel.binding, reason="not_provided")
            return self.peer_jobs.cancel(PeerCaller(self.caller, self.calls, selected.snapshot), service_job_cancel)
