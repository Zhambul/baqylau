# Copyright (c) 2026 Zhambyl Yermagambet
"""Stream records changed at each committed extension boundary."""

from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.models.scopes import ExtensionScope
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter, ValidationError

from api.extensions.change_frames import ChangeStream, change_frames
from api.extensions.change_models import ExtensionChangeQuery
from api.sse import EVENT_STREAM, NO_STORE
from app.provider_projections import ExtensionChanges, ExtensionRecords, Projections

router = APIRouter()
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)


def change_query(
    scope: Annotated[str, Query(min_length=1)],
    history_revision: Annotated[str, Query()] = "default",
    projection_generation: Annotated[str, Query()] = "default",
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> ExtensionChangeQuery:
    """Collect the checked change stream query parameters.

    Returns:
        The complete change selection.

    """
    return ExtensionChangeQuery(
        scope=scope,
        history_revision=history_revision,
        projection_generation=projection_generation,
        cursor=cursor,
    )


ChangeQuery = Annotated[ExtensionChangeQuery, Depends(change_query)]


@router.get("/api/extensions/{extension_id}/changes")
def extension_changes(
    extension_id: str,
    records: ExtensionRecords,
    changes: ExtensionChanges,
    projections: Projections,
    query: ChangeQuery,
) -> StreamingResponse:
    """Stream typed records changed at each committed boundary.

    Returns:
        The server-sent change stream.

    Raises:
        HTTPException: If the scope document is invalid.

    """
    try:
        selected = SCOPE_ADAPTER.validate_json(query.scope)
    except ValidationError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "scope must be a valid extension scope document") from error
    stream = ChangeStream(
        records=records,
        changes=changes,
        history_revision=projections.history_revision,
        generation=projections.generation,
    )
    return StreamingResponse(
        change_frames(stream, extension_id, selected, query),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )
