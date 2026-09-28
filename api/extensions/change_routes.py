# Copyright (c) 2026 Zhambyl Yermagambet
"""Stream records changed at each committed extension boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from api.extensions.change_frames import change_frames
from api.extensions.change_models import ExtensionChangeQuery
from api.extensions.change_stream import ChangeStream
from api.extensions.scope_documents import request_scope
from api.sse import EVENT_STREAM, NO_STORE
from app.provider_extension_sources import ScopeRegistry
from app.provider_projections import ExtensionChanges, ExtensionRecords, Generations, Projections

router = APIRouter()


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


def change_stream(
    records: ExtensionRecords, changes: ExtensionChanges, projections: Projections, generations: Generations,
    scopes: ScopeRegistry,
) -> ChangeStream:
    """Build the change stream services for one request.

    Returns:
        The records, the change signal, the history, the live generation reader, and the scope holds.

    """
    return ChangeStream(
        records=records, changes=changes, history_revision=projections.history_revision, heads=generations,
        scopes=scopes,
    )


Stream = Annotated[ChangeStream, Depends(change_stream)]


@router.get("/api/extensions/{extension_id}/changes")
def extension_changes(extension_id: str, stream: Stream, query: ChangeQuery) -> StreamingResponse:
    """Stream typed records changed at each committed boundary.

    Returns:
        The server-sent change stream.

    """
    return StreamingResponse(
        change_frames(stream, extension_id, request_scope(query.scope), query),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )
