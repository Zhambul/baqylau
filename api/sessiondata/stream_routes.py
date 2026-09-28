# Copyright (c) 2026 Zhambyl Yermagambet
"""Connect session-data event routes to their services."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from api.common.models.fields import SessionIdPath
from api.sessiondata import stream_dependencies
from api.sessiondata.stream_global_frames import global_frames
from api.sessiondata.stream_global_models import GlobalFrameSources
from api.sessiondata.stream_session_frames import session_frames
from api.sessiondata.stream_session_models import SessionStreamPosition, SessionStreamQuery, SessionStreamServices
from api.sse import EVENT_STREAM, NO_STORE
from domain.ids import SessionId

router = APIRouter()


SessionStreamDependency = Annotated[
    SessionStreamServices,
    Depends(stream_dependencies.session_stream_services),
]

GlobalStreamDependency = Annotated[
    GlobalFrameSources,
    Depends(stream_dependencies.global_stream_sources),
]


def from_cursor(last_event_id: str | None, after_cursor: int) -> int:
    """Return the client resume cursor, or the query cursor when it is absent.

    Returns:
        The client resume cursor, or the query cursor when it is absent.

    """
    if last_event_id is None:
        return after_cursor
    try:
        return int(last_event_id)
    except ValueError:
        return after_cursor


def session_stream_query(
    after_cursor: int = 0,
    *,
    include_application: bool = True,
    view_revision: int | None = None,
    after_entry: Annotated[int | None, Query(ge=0)] = None,
) -> SessionStreamQuery:
    """Collect the session stream query parameters.

    A client that knows its view revision sends it, so a switch during a
    disconnect also resets it. A client that knows its highest entry row sends
    it, so an entry that a projection commits later is also sent.

    Returns:
        The complete query.

    """
    return SessionStreamQuery(after_cursor, include_application, view_revision, after_entry)


@router.get("/sessionData/{session_id}/stream")
def session_stream(
    session_id: SessionIdPath,
    services: SessionStreamDependency,
    query: Annotated[SessionStreamQuery, Depends(session_stream_query)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Return the session stream response.

    Returns:
        The session stream response.

    """
    return StreamingResponse(
        session_frames(
            services,
            SessionId(session_id),
            SessionStreamPosition(
                from_cursor(last_event_id, query.after_cursor), query.view_revision, query.after_entry,
            ),
            include_application=query.include_application,
        ),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )


@router.get("/sessionData/stream")
def global_stream(
    sources: GlobalStreamDependency,
    after_cursor: int = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Return the global stream response.

    Returns:
        The global stream response.

    """
    return StreamingResponse(
        global_frames(sources, from_cursor(last_event_id, after_cursor)),
        media_type=EVENT_STREAM,
        headers=NO_STORE,
    )
