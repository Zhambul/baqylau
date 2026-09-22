# Copyright (c) 2026 Zhambyl Yermagambet
"""Check query ownership, schema validation, and exact reply binding."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.operations import QuerySnapshot
from baqylau_extension_api.models.queries import QueryFailed, QueryReady, QueryRequest, QueryResult
from baqylau_extension_api.operations import queries
from baqylau_extension_api.schemas import SchemaSet
from pydantic import TypeAdapter, ValidationError

from tests.extension_api import operation_samples, samples


def test_query_request_uses_registered_schemas() -> None:
    """Accept a complete read without a session or a worker import."""
    manifest = operation_samples.manifest()
    request = operation_samples.query_request()
    assert queries.validate_query_request(manifest, SchemaSet(manifest.schemas), request) == request


@pytest.mark.parametrize("limit", [0, -1, 201, "10", True])
def test_query_limit_is_strict_and_bounded(limit: object) -> None:
    """Reject oversized or coerced query page limits."""
    request = operation_samples.query_request()
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({**request.model_dump(), "limit": limit})


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"operation_id": "test.sample.missing"}, {"scope": samples.SESSION},
])
def test_query_requires_declared_owner_and_scope(change: dict[str, object]) -> None:
    """Reject cross-owner, unknown, and unsupported-scope reads."""
    manifest = operation_samples.manifest()
    request = operation_samples.query_request()
    changed = request.model_copy(update={"binding": request.binding.model_copy(update=change)})
    with pytest.raises(ExtensionContractError):
        queries.validate_query_request(manifest, SchemaSet(manifest.schemas), changed)


def test_query_arguments_are_schema_validated() -> None:
    """Do not accept an encoded document just because its envelope is typed."""
    manifest = operation_samples.manifest()
    with pytest.raises(ExtensionContractError):
        queries.validate_query_request(manifest, SchemaSet(manifest.schemas), operation_samples.query_request("42"))


def test_query_success_and_failure_round_trip() -> None:
    """Keep failed reads distinct from successful empty content."""
    request = operation_samples.query_request()
    responses: tuple[QueryResult, ...] = (
        QueryReady(
            binding=request.binding, document=request.arguments, snapshot=QuerySnapshot(state_revision="state-1"),
        ),
        QueryFailed(
            binding=request.binding, diagnostic=Diagnostic(code="unavailable", message="Data is not available."),
        ),
    )
    adapter = TypeAdapter[QueryResult](QueryResult)
    for response in responses:
        assert adapter.validate_json(response.model_dump_json()) == response
        assert queries.validate_query_response(request, response) == response


@pytest.mark.parametrize("change", [
    {"call_id": "late"}, {"runtime_revision": "stale"}, {"operation_id": "test.sample.other"},
])
def test_query_reply_requires_the_exact_call(change: dict[str, str]) -> None:
    """Do not replace a current view with a valid-looking reply from another call."""
    request = operation_samples.query_request()
    response = QueryFailed(
        binding=request.binding.model_copy(update=change), diagnostic=Diagnostic(code="failed", message="Example"),
    )
    with pytest.raises(ExtensionContractError, match="requested call"):
        queries.validate_query_response(request, response)


def test_query_document_matches_registration() -> None:
    """Reject a successful reply with invalid result content."""
    manifest = operation_samples.manifest()
    request = operation_samples.query_request("42")
    response = QueryReady(
        binding=request.binding, document=request.arguments, snapshot=QuerySnapshot(state_revision="state-1"),
    )
    with pytest.raises(ExtensionContractError):
        queries.validate_query_document(manifest, SchemaSet(manifest.schemas), response)
