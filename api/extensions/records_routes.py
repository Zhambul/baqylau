# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one ordered page of a declared extension record collection."""

from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.models.scopes import ExtensionScope
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter, ValidationError

from api.extensions.records_models import ExtensionRecordPageQuery, ExtensionRecordPageResponse
from app.provider_projections import ExtensionRecords

router = APIRouter()
DEFAULT_RECORD_PAGE = 50
MAX_RECORD_PAGE = 1000
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


def record_page_query(
    scope: Annotated[str, Query(min_length=1)],
    after: Annotated[str, Query()] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_RECORD_PAGE)] = DEFAULT_RECORD_PAGE,
) -> ExtensionRecordPageQuery:
    """Collect the checked record page query parameters.

    Returns:
        The complete query.

    """
    return ExtensionRecordPageQuery(scope=scope, after=after, limit=limit)


RecordPage = Annotated[ExtensionRecordPageQuery, Depends(record_page_query)]


@router.get("/api/extensions/{extension_id}/records/{collection}")
def extension_records(
    extension_id: str,
    collection: str,
    records: ExtensionRecords,
    query: RecordPage,
) -> ExtensionRecordPageResponse:
    """Read one ordered record page in its exact scope.

    Returns:
        The typed record page.

    Raises:
        HTTPException: If the scope document is invalid.

    """
    try:
        selected = SCOPE_ADAPTER.validate_json(query.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    page = records.record_page(extension_id, collection, selected, query.after, query.limit)
    return ExtensionRecordPageResponse(records=page.records, next_key=page.next_key)
