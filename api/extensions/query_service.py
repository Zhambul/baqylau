# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query against an active package."""

from dataclasses import dataclass

from baqylau_extension_api.models.scopes import ExtensionScope

from api.extensions import query_models
from extensions import query_calls
from extensions.query_authority import QueryAuthority
from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class QuerySelection:
    """Name one declared query of one package in one scope."""

    extension_id: str
    query_id: str
    scope: ExtensionScope


def run_declared_query(
    packages: tuple[RegistryPackage, ...],
    query_selection: QuerySelection,
    document: query_models.ExtensionQueryRequest,
    query_authority: QueryAuthority,
) -> query_models.ExtensionQueryResult:
    """Run one declared read against the active package.

    Returns:
        The typed api response.

    """
    package, capability = query_calls.query_package(packages, query_selection.extension_id)
    binding = query_calls.query_binding(package, query_selection.query_id, query_selection.scope)
    request = query_calls.query_request(package, binding, document.arguments, document.page, document.limit)
    result = query_authority.run(
        package, query_selection.scope, lambda: query_calls.checked_query(package, capability, request),
    )
    return query_models.query_response(result)
