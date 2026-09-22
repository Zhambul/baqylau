# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query against an active package."""

from uuid import uuid4

from baqylau_extension_api.contracts.operations import ExtensionQueries
from baqylau_extension_api.models import queries
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.operations import OperationBinding
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.operations import paging, queries as query_operations, registration
from baqylau_extension_api.schemas import SchemaSet

from api.extensions import query_models
from extensions.registry_package import RegistryPackage


class QueryNotFoundError(LookupError):
    """Reject a read whose package or declared query is not active."""


def run_declared_query(
    packages: tuple[RegistryPackage, ...],
    extension_id: str,
    query_id: str,
    scope: ExtensionScope,
    document: query_models.ExtensionQueryRequest,
) -> query_models.ExtensionQueryResult:
    """Run one declared read against the active package.

    Returns:
        The typed api response.

    """
    package, capability = _query_package(packages, extension_id)
    runtime_revision = "" if package.environment is None else package.environment.runtime_revision
    binding = OperationBinding(
        extension_id=extension_id,
        operation_id=query_id,
        scope=scope,
        runtime_revision=runtime_revision,
        call_id=uuid4().hex,
    )
    return _checked_result(package, capability, binding, document)


def _query_package(
    packages: tuple[RegistryPackage, ...], extension_id: str,
) -> tuple[RegistryPackage, ExtensionQueries]:
    for package in packages:
        if package.manifest.extension_id != extension_id:
            continue
        plugin = package.plugin
        if plugin is not None and plugin.capabilities.queries is not None:
            return package, plugin.capabilities.queries
    message = "extension query not found"
    raise QueryNotFoundError(message)


def _checked_result(
    package: RegistryPackage,
    capability: ExtensionQueries,
    binding: OperationBinding,
    document: query_models.ExtensionQueryRequest,
) -> query_models.ExtensionQueryResult:
    schemas = SchemaSet(package.manifest.schemas)
    definition = registration.query_definition(package.manifest, binding)
    request = queries.QueryRequest(
        binding=binding,
        arguments=EncodedDocument(schema_ref=definition.arguments, json_text=document.arguments),
        settings_revision=package.settings.revision,
        settings=package.settings.for_scope(binding.scope),
        snapshot=None if document.page is None else document.page.snapshot,
        page=document.page,
        limit=document.limit,
    )
    checked = query_operations.validate_query_request(package.manifest, schemas, request)
    result = query_operations.validate_query_response(checked, capability.query(checked))
    query_operations.validate_query_document(package.manifest, schemas, result)
    if isinstance(result, queries.QueryReady):
        paging.validate_query_continuation(checked, result)
    return query_models.query_response(result)
