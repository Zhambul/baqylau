# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject service replies which change bindings, versions, exposure, or schemas."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.services import ServiceQueryResponse, ServiceResolved
from baqylau_extension_api.runtime import service_results

from tests.extension_api import service_checks, service_samples as fixtures

BINDING = "binding"
SCOPE = fixtures.resolve_request().binding.scope


@pytest.mark.parametrize("change", [BINDING, "version", "query"])
def test_peer_query_response_stays_bound(change: str) -> None:
    """A valid-looking result cannot claim another service, call, or operation."""
    response = _query_response()
    if change == BINDING:
        binding = response.binding.model_copy(update={"call_id": "other"})
        response = response.model_copy(update={BINDING: binding})
    if change == "version":
        revision = response.service_revision.model_copy(update={"service_version": "2.0.0"})
        response = response.model_copy(update={"service_revision": revision})
    if change == "query":
        operation = response.result.binding.model_copy(update={"operation_id": f"{fixtures.BETA}.private"})
        query_result = response.result.model_copy(update={BINDING: operation})
        response = response.model_copy(update={"result": query_result})
    with pytest.raises(ExtensionContractError):
        service_results.validate_service_query_response(fixtures.service_query(), response)


@pytest.mark.parametrize("change", ["duplicate", "owner", "scope"])
def test_service_metadata_cannot_widen_access(change: str) -> None:
    """Returned query definitions stay owned, unique, and inside the selected scope."""
    host = service_checks.test_host()
    response = host.access.resolve_service(fixtures.resolve_request())
    assert isinstance(response, ServiceResolved)
    definition = response.queries[0]
    if change == "owner":
        definition = definition.model_copy(update={"name": "other.query"})
    if change == "scope":
        definition = definition.model_copy(update={"scopes": ("workspace",)})
    queries = (definition, definition) if change == "duplicate" else (definition,)
    with pytest.raises(ExtensionContractError):
        service_results.validate_service_resolution(
            fixtures.resolve_request(), response.model_copy(update={"queries": queries}),
        )


def test_peer_output_schema_is_checked_by_host() -> None:
    """A type-correct encoded document can still violate the declared query schema."""
    host = service_checks.test_host()
    host.target.wrong_document = True
    with (
        host.access.calls.root(fixtures.environment(fixtures.ALPHA), SCOPE, 3),
        pytest.raises(ExtensionContractError),
    ):
        host.access.query_service(fixtures.service_query())
    assert len(host.target.received) == 1


def _query_response() -> ServiceQueryResponse:
    host = service_checks.test_host()
    with host.access.calls.root(fixtures.environment(fixtures.ALPHA), SCOPE, 3):
        response = host.access.query_service(fixtures.service_query())
    assert isinstance(response, ServiceQueryResponse)
    return response
