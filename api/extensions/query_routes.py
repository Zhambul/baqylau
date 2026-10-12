# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query through the active worker."""

from http import HTTPStatus

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.runtime.models import ExtensionTransportError
from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter, ValidationError

from api.extensions import query_service
from api.extensions.query_models import ExtensionQueryRequest, ExtensionQueryResult
from app.provider_extension_registry import ExtensionQueryHost, Registry
from extensions.query_authority import QueryHost
from extensions.query_calls import QueryNotFoundError
from extensions.registry_package import RegistryPackage

router = APIRouter()
WORKER_UNAVAILABLE = "the extension worker is busy or unavailable"

SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


@router.post("/api/extensions/{extension_id}/queries/{query_id}")
def extension_query(
    extension_id: str,
    query_id: str,
    extension_query_request: ExtensionQueryRequest,
    registry: Registry,
    host: ExtensionQueryHost,
) -> ExtensionQueryResult:
    """Run one declared read against the active package.

    Returns:
        The typed query result.

    """
    selection = query_service.QuerySelection(extension_id, query_id, _scope(extension_query_request))
    with registry.read_snapshot() as read:
        return _answer(read.snapshot.packages, selection, extension_query_request, host)


def _scope(extension_query_request: ExtensionQueryRequest) -> ExtensionScope:
    try:
        return SCOPE_ADAPTER.validate_json(extension_query_request.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error


def _answer(
    packages: tuple[RegistryPackage, ...],
    selection: query_service.QuerySelection,
    extension_query_request: ExtensionQueryRequest,
    host: QueryHost,
) -> ExtensionQueryResult:
    """Run the query, and give each known failure its status.

    A worker that refuses or drops the call is busy or restarting. The read is
    safe to repeat, so the answer is 503 and not an internal error.

    Returns:
        The typed query result.

    Raises:
        HTTPException: If the package, query, or arguments are invalid, or the worker is unavailable.

    """
    try:
        return query_service.run_declared_query(packages, selection, extension_query_request, host.authority)
    except QueryNotFoundError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    except ExtensionContractError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error
    except ExtensionTransportError as error:
        host.refused(selection.extension_id, selection.query_id)
        raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, WORKER_UNAVAILABLE) from error
