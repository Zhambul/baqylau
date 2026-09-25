# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and rescan the extension catalog without enabling feature code."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.extensions import lifecycle_mapper, lifecycle_models
from api.extensions.admission import require_catalog_write
from api.extensions.mapper import catalog_response
from api.extensions.models import ExtensionCatalogResponse, ExtensionHealthResponse, RescanExtensionsRequest
from api.responses import errors
from app.provider_extension_health import Health
from app.provider_extensions import Catalog
from app.provider_operation_history import OperationHistory

router = APIRouter()
MAX_RECENT_OPERATIONS = 50
DEFAULT_RECENT_OPERATIONS = 20
CATALOG_RESPONSES = errors({
    403: "The browser origin is not accepted.",
    409: "The catalog revision changed.",
    415: "The request is not application/json.",
})


@router.get("/api/extensions")
def extensions(catalog: Catalog) -> ExtensionCatalogResponse:
    """Read the current validated and invalid package descriptions.

    Returns:
        A revisioned catalog with explicit discovery failures.

    """
    return catalog_response(catalog.catalog_snapshot())


@router.post(
    "/api/extensions/rescan", dependencies=[Depends(require_catalog_write)],
    responses=CATALOG_RESPONSES,
)
def rescan_extensions(
    rescan_extensions_request: RescanExtensionsRequest, catalog: Catalog,
) -> ExtensionCatalogResponse:
    """Recheck configured files without changing requested or active runtime state.

    Returns:
        The accepted catalog revision.

    Raises:
        HTTPException: If another scan already changed the catalog.

    """
    result = catalog.rescan_packages(rescan_extensions_request.expected_revision)
    if not result.accepted:
        raise HTTPException(HTTPStatus.CONFLICT, "extension catalog revision changed; read it before retrying")
    return catalog_response(result.snapshot)


@router.get("/api/extensions/operations")
def recent_extension_operations(
    history: OperationHistory,
    limit: Annotated[int, Query(ge=1, le=MAX_RECENT_OPERATIONS)] = DEFAULT_RECENT_OPERATIONS,
) -> lifecycle_models.ExtensionOperationsResponse:
    """Read the newest retained operations, including failures and interrupted work.

    Returns:
        At most `limit` operations, newest first.

    """
    return lifecycle_models.ExtensionOperationsResponse(
        operations=tuple(lifecycle_mapper.operation_response(operation) for operation in history.recent(limit)),
    )


@router.get("/api/extensions/health")
def extension_health(health: Health) -> ExtensionHealthResponse:
    """Read each extension's consecutive worker failures; an extension without a row has none.

    Returns:
        The failure limit and the stored health rows in extension order.

    """
    return ExtensionHealthResponse(failure_limit=health.policy.failure_limit, extensions=health.store.read_health())
