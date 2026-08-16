# api/dashboard/sessions.py — the session read API: the list, one session's
# snapshot pair, and the activity backlog.
#
# Responses are the frozen projection dataclasses, serialized by their one
# encoder (dashboard.activity.to_wire); the route signatures name the types.
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from api.dependencies import ApplicationGraph
from dashboard.activity import to_wire
from domain.ids import ActorId, SessionId
from runtime.projections import ActivityScope

router = APIRouter()

DEFAULT_ACTIVITY_BLOCK_COUNT = 100


def _scope(application, session_id: SessionId, actor_id: str | None) -> ActivityScope:
    session = application.sessions.find_by_id(session_id)
    if session is None:
        raise KeyError(f"unknown session: {session_id}")
    return ActivityScope(
        actor_id=ActorId(actor_id) if actor_id else session.lead_actor_id
    )


@router.get("/api/sessions")
def session_list(application: ApplicationGraph) -> JSONResponse:
    return JSONResponse(to_wire(application.dashboard_sessions.sessions()))


@router.get("/api/sessions/{session_id}")
def session_snapshot(
    session_id: str,
    application: ApplicationGraph,
    actor_id: str | None = None,
) -> JSONResponse:
    session = SessionId(session_id)
    scope = _scope(application, session, actor_id)
    return JSONResponse(
        {
            "canonical": to_wire(application.dashboard_sessions.snapshot(session, scope)),
            "application": to_wire(application.session_application.snapshot(session)),
        }
    )


@router.get("/api/sessions/{session_id}/activity")
def session_activity(
    session_id: str,
    application: ApplicationGraph,
    block_count: int = Query(DEFAULT_ACTIVITY_BLOCK_COUNT, gt=0),
    before_cursor: int | None = None,
    actor_id: str | None = None,
) -> JSONResponse:
    session = SessionId(session_id)
    scope = _scope(application, session, actor_id)
    page = application.dashboard_activity.backlog(session, before_cursor, scope, block_count)
    return JSONResponse(to_wire(page))
