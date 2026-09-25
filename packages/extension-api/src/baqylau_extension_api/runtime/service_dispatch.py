# Copyright (c) 2026 Zhambyl Yermagambet
"""Authorize peer reads against host metadata, active grants, and declared schemas.

Peer jobs need durable host storage, so the daemon registry access accepts them.
"""

from dataclasses import dataclass

from baqylau_extension_api.contracts.service_access import ExtensionServiceAccess
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.service_jobs import (
    ServiceCommandRequest,
    ServiceJobCancelRequest,
    ServiceJobCancelResult,
    ServiceJobRequest,
    ServiceJobResult,
)
from baqylau_extension_api.models.services import (
    ServiceQueryRequest,
    ServiceQueryResponse,
    ServiceQueryResult,
    ServiceResolved,
    ServiceResolveRequest,
    ServiceResolveResult,
    ServiceUnavailable,
)
from baqylau_extension_api.operations import queries
from baqylau_extension_api.runtime import service_results, service_selection
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.grant_models import HostCallGrant
from baqylau_extension_api.runtime.service_provider import ServiceProvider, ServiceProviderLookup
from baqylau_extension_api.runtime.service_requests import target_query_request
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest


@dataclass(frozen=True)
class HostServiceAccess(ExtensionServiceAccess):
    """Bind a callback connection to its caller, not to a claimed payload identity."""

    caller: WorkerLoadRequest
    providers: ServiceProviderLookup
    calls: HostCallLedger

    def resolve_service(self, service_request: ServiceResolveRequest) -> ServiceResolveResult:
        """Resolve declared metadata, including during a factory's preparation.

        Returns:
            Active public read definitions or an explicit unavailable reason.

        """
        request = ServiceResolveRequest.model_validate(service_request)
        service_selection.require_service_consumer(self.caller.manifest, request.binding)
        provider = self.providers.get_service_provider(request.binding.owner, request.binding.scope)
        response = service_selection.resolve_provider(self.caller.manifest, request.binding, provider)
        return service_results.validate_service_resolution(request, response)

    def query_service(self, service_query: ServiceQueryRequest) -> ServiceQueryResult:
        """Require live host authority before looking up and executing a peer query.

        Returns:
            A validated read result or a stale or unavailable service.

        """
        request = ServiceQueryRequest.model_validate(service_query)
        grant = self.calls.require_call(self.caller.environment, request.binding.scope)
        service_selection.require_service_consumer(self.caller.manifest, request.binding)
        provider = self.providers.get_service_provider(request.binding.owner, request.binding.scope)
        resolution = service_selection.resolve_provider(self.caller.manifest, request.binding, provider)
        if isinstance(resolution, ServiceUnavailable):
            return resolution
        if resolution.service_revision != request.service_revision:
            return ServiceUnavailable(binding=request.binding, reason="stale_handle")
        _require_public_query(request, resolution)
        response = self._query(request, provider, grant)
        return service_results.validate_service_query_response(request, response)

    def submit_service_command(self, service_command: ServiceCommandRequest) -> ServiceJobResult:
        """Report that this access has no durable job host.

        Returns:
            An unavailable result; the daemon's registry access accepts peer jobs.

        """
        return ServiceUnavailable(binding=service_command.binding, reason="not_provided")

    def read_service_job(self, service_job: ServiceJobRequest) -> ServiceJobResult:
        """Report that this access has no durable job host.

        Returns:
            An unavailable result; the daemon's registry access reads peer jobs.

        """
        return ServiceUnavailable(binding=service_job.binding, reason="not_provided")

    def cancel_service_job(self, service_job_cancel: ServiceJobCancelRequest) -> ServiceJobCancelResult:
        """Report that this access has no durable job host.

        Returns:
            An unavailable result; the daemon's registry access stops peer jobs.

        """
        return ServiceUnavailable(binding=service_job_cancel.binding, reason="not_provided")

    def _query(
        self, request: ServiceQueryRequest, provider: ServiceProvider | None, grant: HostCallGrant,
    ) -> ServiceQueryResponse:
        if provider is None or provider.environment is None or provider.queries is None:
            message = "active peer service has no query capability"
            raise ExtensionContractError(message)
        target = target_query_request(request, provider.settings_revision, provider.settings)
        queries.validate_query_request(provider.manifest, provider.schemas, target)
        with self.calls.forward(grant, provider.environment):
            response = provider.queries.query(target)
            self.calls.require_call(provider.environment, request.binding.scope)
        queries.validate_query_response(target, response)
        queries.validate_query_document(provider.manifest, provider.schemas, response)
        return ServiceQueryResponse(
            binding=request.binding, service_revision=request.service_revision,
            settings_revision=provider.settings_revision, result=response,
        )


def _require_public_query(request: ServiceQueryRequest, resolution: ServiceResolved) -> None:
    if not any(query.name == request.query_id for query in resolution.queries):
        message = "query is not exposed by this service for the selected scope"
        raise ExtensionContractError(message)
