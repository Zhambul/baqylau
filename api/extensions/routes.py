# Copyright (c) 2026 Zhambyl Yermagambet
"""Read and rescan the extension catalog without enabling feature code."""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException

from api.extensions.admission import require_catalog_write
from api.extensions.mapper import catalog_response
from api.extensions.models import ExtensionCatalogResponse, RescanExtensionsRequest
from api.responses import errors
from app.provider_extensions import Catalog

router = APIRouter()
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
