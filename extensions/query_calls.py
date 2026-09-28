# Copyright (c) 2026 Zhambyl Yermagambet
"""Build and run one checked query call against an active package.

The HTTP query route and the terminal view documents use the same request and
the same checks: the request, the result, the result document, and its page
continuation.
"""

from uuid import uuid4

from baqylau_extension_api.contracts.operations import ExtensionQueries
from baqylau_extension_api.models import queries
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.operations import OperationBinding, QueryPageCursor
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.operations import paging, queries as query_operations, registration
from baqylau_extension_api.schemas import SchemaSet

from extensions.registry_package import RegistryPackage


class QueryNotFoundError(LookupError):
    """Reject a read whose package or declared query is not active."""


def query_package(packages: tuple[RegistryPackage, ...], extension_id: str) -> tuple[RegistryPackage, ExtensionQueries]:
    """Find the active package and its query capability.

    Returns:
        The package and its capability.

    Raises:
        QueryNotFoundError: If no active package of that ID has queries.

    """
    for package in packages:
        if package.manifest.extension_id != extension_id:
            continue
        plugin = package.plugin
        if plugin is not None and plugin.capabilities.queries is not None:
            return package, plugin.capabilities.queries
    message = "extension query not found"
    raise QueryNotFoundError(message)


def query_binding(package: RegistryPackage, query_id: str, scope: ExtensionScope) -> OperationBinding:
    """Bind one call of a query to the package's runtime, with a new call ID.

    Returns:
        The binding.

    """
    runtime_revision = "" if package.environment is None else package.environment.runtime_revision
    return OperationBinding(
        extension_id=package.manifest.extension_id, operation_id=query_id, scope=scope,
        runtime_revision=runtime_revision, call_id=uuid4().hex,
    )


def query_request(
    package: RegistryPackage, binding: OperationBinding, arguments: str,
    page: QueryPageCursor | None = None, limit: int = queries.DEFAULT_QUERY_LIMIT,
) -> queries.QueryRequest:
    """Build the request with the query's argument schema and the scope's resolved settings.

    Returns:
        The request, before its checks.

    """
    definition = registration.query_definition(package.manifest, binding)
    return queries.QueryRequest(
        binding=binding,
        arguments=EncodedDocument(schema_ref=definition.arguments, json_text=arguments),
        settings_revision=package.settings.revision,
        settings=package.resolved_settings.for_scope(binding.scope),
        snapshot=None if page is None else page.snapshot,
        page=page,
        limit=limit,
    )


def checked_query(
    package: RegistryPackage, capability: ExtensionQueries, request: queries.QueryRequest,
) -> queries.QueryResult:
    """Check the request, call the worker, and check its complete result.

    Returns:
        The checked result.

    """
    schemas = SchemaSet(package.manifest.schemas)
    checked = query_operations.validate_query_request(package.manifest, schemas, request)
    result = query_operations.validate_query_response(checked, capability.query(checked))
    query_operations.validate_query_document(package.manifest, schemas, result)
    if isinstance(result, queries.QueryReady):
        paging.validate_query_continuation(checked, result)
    return result


def ready_query(
    package: RegistryPackage, capability: ExtensionQueries, request: queries.QueryRequest,
) -> queries.QueryReady:
    """Run a checked query whose caller needs a ready result.

    Returns:
        The ready result.

    Raises:
        TypeError: If the query failed.

    """
    result = checked_query(package, capability, request)
    if not isinstance(result, queries.QueryReady):
        message = "the query did not give a ready result"
        raise TypeError(message)
    return result
