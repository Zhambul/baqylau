# Copyright (c) 2026 Zhambyl Yermagambet
"""Call declared host service routes from an extension worker."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.service_access import ExtensionServiceAccess
from baqylau_extension_api.models.services import (
    ServiceQueryRequest,
    ServiceQueryResult,
    ServiceResolveRequest,
    ServiceResolveResult,
)
from baqylau_extension_api.runtime import channel, codec, methods, service_results
from baqylau_extension_api.runtime.contract import RemoteCaller


@dataclass(frozen=True)
class RemoteServiceAccess(ExtensionServiceAccess):
    """Keep peer discovery and reads behind the same public service protocol."""

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


def register_service_access(rpc: channel.RpcChannel, access: ExtensionServiceAccess) -> None:
    """Register host callbacks bound to one authenticated worker connection."""
    rpc.register(methods.SERVICE_RESOLVE, codec.ModelHandler(
        ServiceResolveRequest, TypeAdapter(ServiceResolveResult), access.resolve_service,
    ), "live")
    rpc.register(methods.SERVICE_QUERY, codec.ModelHandler(
        ServiceQueryRequest, TypeAdapter(ServiceQueryResult), access.query_service,
    ), "live")
