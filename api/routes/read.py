# api/routes/read.py — the JSON read API: sessions, snapshots, activity,
# harnesses, catalog, insights, resumable sessions, content resolution.
#
# Responses are the frozen projection dataclasses, serialized by their one
# encoder (dashboard.activity.to_wire); the route signatures name the types.
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from api.dependencies import ApplicationGraph
from api.models import HarnessDescription
from contracts.harness import QueryContext
from dashboard.activity import to_wire
from dashboard.diff import source_html, unified_diff_html
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


@router.get("/api/harnesses")
def harnesses(application: ApplicationGraph) -> list[HarnessDescription]:
    return [
        HarnessDescription(
            name=plugin.info.name,
            display_name=plugin.info.display_name,
            launchable=plugin.launcher is not None,
            default_for_launch=plugin.info.default_for_launch,
            supports_attachments=plugin.info.supports_attachments,
            control_names=(
                tuple(sorted(plugin.controller.handlers)) if plugin.controller else ()
            ),
            supports_accounts=plugin.info.supports_accounts,
            supports_terminal_input=plugin.terminal_probe is not None,
        )
        for plugin in application.registry.plugins()
    ]


@router.get("/api/insights")
def insights(application: ApplicationGraph) -> JSONResponse:
    return JSONResponse(to_wire(application.insights.snapshot()))


@router.get("/api/resumable-sessions")
def resumable_sessions(
    application: ApplicationGraph,
    working_directory: str = "",
    search: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        to_wire(application.resumable_sessions.sessions_for(working_directory, search))
    )


@router.get("/api/harnesses/{harness}/catalog")
def catalog(
    harness: str,
    application: ApplicationGraph,
    session_id: str | None = None,
    working_directory: str | None = None,
) -> JSONResponse:
    context = QueryContext(
        session_id=SessionId(session_id) if session_id else None,
        working_directory=working_directory,
    )
    # The menu payload is composed here, from the two places its parts honestly
    # live: the STATIC vocabulary on the plugin's HarnessInfo (built once, as a
    # literal) and the per-directory part from the catalogue. The contract
    # keeps them apart; this endpoint is where the browser wants them together.
    info = application.registry.plugin(harness).info
    payload = to_wire(application.catalog.read(harness, context))
    payload["models"] = to_wire(info.models)
    payload["rewind_modes"] = to_wire(info.rewind_modes)
    return JSONResponse(payload)


@router.get("/api/content/{content_reference:path}")
def content(
    content_reference: str,
    application: ApplicationGraph,
    view: str | None = None,
    path: str | None = None,
):
    text = application.content.resolve(content_reference)
    if view in ("diff", "source"):
        if not path:
            raise ValueError("path is required for file view")
        rendered = unified_diff_html(text, path) if view == "diff" else source_html(text, path)
        return HTMLResponse(rendered)
    return PlainTextResponse(text)


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
