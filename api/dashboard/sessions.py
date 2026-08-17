# api/dashboard/sessions.py — the session read API: the list, one session's
# snapshot pair, and the activity backlog.
#
# Responses are the frozen projection dataclasses, serialized by their one
# encoder (dashboard.render.serialize.json_ready); the route signatures name the types.
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from api.common.models.fields import SessionIdPath
from api.dependencies import ApplicationGraph
from dashboard.render.serialize import json_ready
from domain.errors import UnknownReference
from domain.ids import ActorId, SessionId
from api.dashboard.models.sessions.session_snapshot_response import SessionSnapshotResponse
from dashboard.services.models import DashboardActivityPage, DashboardSessionListItem
from engine.projections import ActivityScope

router = APIRouter()

DEFAULT_ACTIVITY_BLOCK_COUNT = 100


def _scope(application, session_id: SessionId, actor_id: str | None) -> ActivityScope:
    session = application.sessions.find(session_id)
    if session is None:
        # By type, not a bare KeyError: this is the caller naming a session that
        # does not exist, and it is the reason the 400 handler exists at all.
        raise UnknownReference(f"unknown session: {session_id}")
    return ActivityScope(
        actor_id=ActorId(actor_id) if actor_id else session.lead_actor_id
    )


@router.get("/api/sessions", response_model=list[DashboardSessionListItem])
def session_list(application: ApplicationGraph) -> JSONResponse:
    return JSONResponse(json_ready(application.dashboard_sessions.sessions()))


@router.get("/api/sessions/{session_id}", response_model=SessionSnapshotResponse)
def session_snapshot(
    session_id: SessionIdPath,
    application: ApplicationGraph,
    actor_id: str | None = None,
) -> JSONResponse:
    session = SessionId(session_id)
    scope = _scope(application, session, actor_id)
    return JSONResponse(
        {
            "canonical": json_ready(application.dashboard_sessions.snapshot(session, scope)),
            "application": json_ready(application.session_application.snapshot(session)),
        }
    )


@router.get("/api/sessions/{session_id}/activity", response_model=DashboardActivityPage)
def session_activity(
    session_id: SessionIdPath,
    application: ApplicationGraph,
    block_count: int = Query(DEFAULT_ACTIVITY_BLOCK_COUNT, gt=0),
    before_cursor: int | None = None,
    actor_id: str | None = None,
) -> JSONResponse:
    session = SessionId(session_id)
    scope = _scope(application, session, actor_id)
    page = application.dashboard_activity.backlog(session, before_cursor, scope, block_count)
    return JSONResponse(json_ready(page))
