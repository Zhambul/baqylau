# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query through the active worker."""

from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import ExtensionScope
from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter, ValidationError

from api.extensions import query_service
from api.extensions.query_models import ExtensionQueryRequest, ExtensionQueryResult
from app.provider_extension_policy import WorkerLimits
from app.provider_extension_registry import CallLedger, Registry
from extensions.query_authority import QueryAuthority
from extensions.query_calls import QueryNotFoundError

router = APIRouter()


def query_authority(ledger: CallLedger, limits: WorkerLimits) -> QueryAuthority:
    """Use the shared call ledger and the host's call deadline.

    Returns:
        The query authority.

    """
    return QueryAuthority(ledger, limits.request_seconds)


Authority = Annotated[QueryAuthority, Depends(query_authority)]
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


@router.post("/api/extensions/{extension_id}/queries/{query_id}")
def extension_query(
    extension_id: str,
    query_id: str,
    extension_query_request: ExtensionQueryRequest,
    registry: Registry,
    authority: Authority,
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
                read.snapshot.packages, query_service.QuerySelection(extension_id, query_id, scope),
                extension_query_request, authority,
            )
        except QueryNotFoundError as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
        except ExtensionContractError as error:
            raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error
