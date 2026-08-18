# api/dashboard/sessions.py — the session read API: the list, one session's
# snapshot pair, and the activity backlog.
#
# Responses ARE the frozen projection dataclasses. Each route names the type it
# answers with and returns that object; FastAPI serializes it against that type,
# so the published schema and the bytes on the wire are one statement.
from __future__ import annotations

from fastapi import APIRouter, Query

from api.common.models.fields import SessionIdPath
from app.providers import DashboardActivity, DashboardSessions, SessionApplication, Sessions
from domain.errors import UnknownReference
from domain.ids import ActorId, SessionId
from api.dashboard.mapper import activity as activity_mapper
from api.dashboard.mapper import sessions as mapper
from api.dashboard.models.sessions.activity_page import ActivityPageResponse
from api.dashboard.models.sessions.session_list_item import SessionListItemResponse
from api.dashboard.models.sessions.session_snapshot_response import SessionSnapshotResponse
from engine.projections import ActivityScope

router = APIRouter()

DEFAULT_ACTIVITY_BLOCK_COUNT = 100


def _scope(sessions: Sessions, session_id: SessionId, actor_id: str | None) -> ActivityScope:
    session = sessions.find(session_id)
    if session is None:
        # By type, not a bare KeyError: this is the caller naming a session that
        # does not exist, and it is the reason the 400 handler exists at all.
        raise UnknownReference(f"unknown session: {session_id}")
    return ActivityScope(
        actor_id=ActorId(actor_id) if actor_id else session.lead_actor_id
    )


@router.get("/api/sessions")
def session_list(dashboard: DashboardSessions) -> tuple[SessionListItemResponse, ...]:
    return tuple(mapper.session_list_item(row) for row in dashboard.sessions())


@router.get("/api/sessions/{session_id}")
def session_snapshot(
    session_id: SessionIdPath,
    sessions: Sessions,
    dashboard: DashboardSessions,
    workspace: SessionApplication,
    actor_id: str | None = None,
) -> SessionSnapshotResponse:
    session = SessionId(session_id)
    scope = _scope(sessions, session, actor_id)
    return mapper.session_snapshot(
        dashboard.snapshot(session, scope), workspace.snapshot(session)
    )


@router.get("/api/sessions/{session_id}/activity")
def session_activity(
    session_id: SessionIdPath,
    sessions: Sessions,
    activity: DashboardActivity,
    block_count: int = Query(DEFAULT_ACTIVITY_BLOCK_COUNT, gt=0),
    before_cursor: int | None = None,
    actor_id: str | None = None,
) -> ActivityPageResponse:
    session = SessionId(session_id)
    scope = _scope(sessions, session, actor_id)
    return activity_mapper.activity_page(
        activity.backlog(session, before_cursor, scope, block_count)
    )
