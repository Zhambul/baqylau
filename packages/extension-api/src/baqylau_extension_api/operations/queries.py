# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate complete query requests and replies at the public boundary."""

from pydantic import TypeAdapter

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.queries import QueryReady, QueryRequest, QueryResult
from baqylau_extension_api.operations import documents, paging, registration
from baqylau_extension_api.schemas import SchemaSet

MAX_QUERY_RESPONSE_BYTES = 2_097_152


def validate_query_request(manifest: ExtensionManifest, schemas: SchemaSet, request: QueryRequest) -> QueryRequest:
    """Check a declared read and every cursor before feature code runs.

    Returns:
        The immutable checked request.

    """
    checked = QueryRequest.model_validate(request)
    definition = registration.query_definition(manifest, checked.binding)
    documents.require_document_schema(checked.arguments, definition.arguments, schemas, "query arguments")
    documents.validate_settings(manifest, checked.settings, schemas)
    paging.validate_query_page(checked)
    return checked


def validate_query_response(request: QueryRequest, response: QueryResult) -> QueryResult:
    """Require the exact call binding and a bounded, consistent page result.

    Returns:
        The checked success or failure result.

    Raises:
        ExtensionContractError: If the reply is stale or exceeds the response limit.

    """
    checked_request = QueryRequest.model_validate(request)
    checked = TypeAdapter[QueryResult](QueryResult).validate_python(response)
    if checked.binding != checked_request.binding:
        message = "query result does not match its requested call"
        raise ExtensionContractError(message)
    if len(checked.model_dump_json().encode("utf-8")) > MAX_QUERY_RESPONSE_BYTES:
        message = "query response exceeds its encoded size limit"
        raise ExtensionContractError(message)
    if isinstance(checked, QueryReady):
        paging.validate_query_continuation(checked_request, checked)
        rules.require_unique((content.content_id for content in checked.content), "query content IDs")
    return checked


def validate_query_document(manifest: ExtensionManifest, schemas: SchemaSet, response: QueryResult) -> None:
    """Validate successful documents against the declared result schema."""
    definition = registration.query_definition(manifest, response.binding)
    if isinstance(response, QueryReady):
        documents.require_document_schema(response.document, definition.result, schemas, "query result")
