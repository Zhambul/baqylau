# Copyright (c) 2026 Zhambyl Yermagambet
"""Check peer-service replies before returning them to an extension."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.models.services import (
    ServiceQueryRequest,
    ServiceQueryResponse,
    ServiceQueryResult,
    ServiceResolved,
    ServiceResolveRequest,
    ServiceResolveResult,
)
from baqylau_extension_api.operations.queries import validate_query_response
from baqylau_extension_api.runtime.service_requests import target_query_request

MAX_SERVICE_RESPONSE_BYTES = 4_194_304


def validate_service_resolution(request: ServiceResolveRequest, response: ServiceResolveResult) -> ServiceResolveResult:
    """Keep service metadata tied to the exact requested peer and scope.

    Returns:
        Current read metadata or a typed unavailable result.

    Raises:
        ExtensionContractError: If the response changes identity, scope, or bounds.

    """
    checked = TypeAdapter[ServiceResolveResult](ServiceResolveResult).validate_python(response)
    if checked.binding != ServiceResolveRequest.model_validate(request).binding:
        message = "service resolution changed its requested binding"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_SERVICE_RESPONSE_BYTES:
        message = "service resolution exceeds its encoded size limit"
        raise ExtensionContractError(message)
    if isinstance(checked, ServiceResolved):
        _require_resolved_queries(checked)
    return checked


def _require_resolved_queries(response: ServiceResolved) -> None:
    rules.require_unique((query.name for query in response.queries), "service query IDs")
    rules.require_owned((query.name for query in response.queries), response.binding.owner)
    if any(response.binding.scope.kind not in query.scopes for query in response.queries):
        message = "service query metadata changed its selected scope"
        raise ExtensionContractError(message)


def validate_service_query_response(request: ServiceQueryRequest, response: ServiceQueryResult) -> ServiceQueryResult:
    """Reuse query result checks without disclosing a peer's settings values.

    Returns:
        A checked query outcome or an explicit unavailable service.

    Raises:
        ExtensionContractError: If the service or captured request changes.

    """
    checked_request = ServiceQueryRequest.model_validate(request)
    checked = TypeAdapter[ServiceQueryResult](ServiceQueryResult).validate_python(response)
    if checked.binding != checked_request.binding:
        message = "service query changed its requested binding"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_SERVICE_RESPONSE_BYTES:
        message = "service query exceeds its encoded size limit"
        raise ExtensionContractError(message)
    if isinstance(checked, ServiceQueryResponse):
        if checked.service_revision != checked_request.service_revision:
            message = "service query changed its selected worker or version"
            raise ExtensionContractError(message)
        validate_query_response(target_query_request(checked_request, checked.settings_revision), checked.result)
    return checked
