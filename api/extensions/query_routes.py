# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query through the active worker."""

from http import HTTPStatus

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import ExtensionScope
from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter, ValidationError

from api.extensions import query_service
from api.extensions.query_models import ExtensionQueryRequest, ExtensionQueryResult
from app.provider_extension_registry import Registry

router = APIRouter()
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


@router.post("/api/extensions/{extension_id}/queries/{query_id}")
def extension_query(
    extension_id: str,
    query_id: str,
    extension_query_request: ExtensionQueryRequest,
    registry: Registry,
) -> ExtensionQueryResult:
    """Run one declared read against the active package.

    Returns:
        The typed query result.

    Raises:
        HTTPException: If the scope, package, query, or arguments are invalid.

    """
    try:
        scope = SCOPE_ADAPTER.validate_json(extension_query_request.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    with registry.read_snapshot() as read:
        try:
            return query_service.run_declared_query(
                read.snapshot.packages, extension_id, query_id, scope, extension_query_request,
            )
        except query_service.QueryNotFoundError as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
        except ExtensionContractError as error:
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error
