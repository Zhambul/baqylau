# Copyright (c) 2026 Zhambyl Yermagambet
"""Call declared host service routes from an extension worker."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.service_access import ExtensionServiceAccess
from baqylau_extension_api.models.service_jobs import (
    ServiceCommandRequest,
    ServiceJobCancelRequest,
    ServiceJobCancelResult,
    ServiceJobRequest,
    ServiceJobResult,
)
from baqylau_extension_api.models.services import (
    ServiceQueryRequest,
    ServiceQueryResult,
    ServiceResolveRequest,
    ServiceResolveResult,
)
from baqylau_extension_api.runtime import channel, codec, methods, service_job_results, service_results
from baqylau_extension_api.runtime.contract import EncodedHandler, RemoteCaller


@dataclass(frozen=True)
class RemoteServiceAccess(ExtensionServiceAccess):
    """Keep peer discovery, reads, and job requests behind the same public service protocol."""

    caller: RemoteCaller

    def resolve_service(self, service_request: ServiceResolveRequest) -> ServiceResolveResult:
        """Read host-selected versions and public query declarations.

        Returns:
            Checked metadata or typed unavailability, not a feature object.

        """
        response = self.caller.invoke_typed(
            methods.SERVICE_RESOLVE, service_request, TypeAdapter[ServiceResolveResult](ServiceResolveResult),
        )
        return service_results.validate_service_resolution(service_request, response)

    def query_service(self, service_query: ServiceQueryRequest) -> ServiceQueryResult:
        """Call a peer read while the transport carries the host call reference.

        Returns:
            The exact selected service's checked query result.

        """
        response = self.caller.invoke_typed(
            methods.SERVICE_QUERY, service_query, TypeAdapter[ServiceQueryResult](ServiceQueryResult),
        )
        return service_results.validate_service_query_response(service_query, response)

    def submit_service_command(self, service_command: ServiceCommandRequest) -> ServiceJobResult:
        """Ask the host to accept one public peer command.

        Returns:
            The accepted job reference or an explicit unavailable service.

        """
        response = self.caller.invoke_typed(
            methods.SERVICE_COMMAND, service_command, TypeAdapter[ServiceJobResult](ServiceJobResult),
        )
        return service_job_results.validate_service_job(service_command, response)

    def read_service_job(self, service_job: ServiceJobRequest) -> ServiceJobResult:
        """Read a peer job that this caller submitted.

        Returns:
            The stored job state or an explicit unavailable service.

        """
        response = self.caller.invoke_typed(
            methods.SERVICE_JOB, service_job, TypeAdapter[ServiceJobResult](ServiceJobResult),
        )
        return service_job_results.validate_service_job(service_job, response)

    def cancel_service_job(self, service_job_cancel: ServiceJobCancelRequest) -> ServiceJobCancelResult:
        """Ask the owning peer to stop an attempt that this caller submitted.

        Returns:
            The checked acknowledgment or an explicit unavailable service.

        """
        response = self.caller.invoke_typed(
            methods.SERVICE_JOB_CANCEL, service_job_cancel, TypeAdapter[ServiceJobCancelResult](ServiceJobCancelResult),
        )
        return service_job_results.validate_service_job_cancel(service_job_cancel, response)


def register_service_access(rpc: channel.RpcChannel, access: ExtensionServiceAccess) -> None:
    """Register host callbacks bound to one authenticated worker connection."""
    handlers: tuple[tuple[str, EncodedHandler], ...] = (
        (methods.SERVICE_RESOLVE, codec.ModelHandler(
            ServiceResolveRequest, TypeAdapter(ServiceResolveResult), access.resolve_service,
        )),
        (methods.SERVICE_QUERY, codec.ModelHandler(
            ServiceQueryRequest, TypeAdapter(ServiceQueryResult), access.query_service,
        )),
        (methods.SERVICE_COMMAND, codec.ModelHandler(
            ServiceCommandRequest, TypeAdapter(ServiceJobResult), access.submit_service_command,
        )),
        (methods.SERVICE_JOB, codec.ModelHandler(
            ServiceJobRequest, TypeAdapter(ServiceJobResult), access.read_service_job,
        )),
        (methods.SERVICE_JOB_CANCEL, codec.ModelHandler(
            ServiceJobCancelRequest, TypeAdapter(ServiceJobCancelResult), access.cancel_service_job,
        )),
    )
    for method, encoded_handler in handlers:
        rpc.register(method, encoded_handler, "live")
