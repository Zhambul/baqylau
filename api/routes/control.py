# api/routes/control.py — the control plane: launching sessions, the gesture
# funnel, and the terminal pane/view commands the kitty handlers post.
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.dependencies import ApplicationGraph
from api.guard import control_plane
from api.models import (
    ControlBody,
    LaunchBody,
    PaneCommandBody,
    PaneCommandReply,
    TerminalViewBody,
    Opened,
)
from app import terminal_views
from core import audit as A
from dashboard.activity import to_wire
from domain.ids import SessionId

router = APIRouter(dependencies=[Depends(control_plane())])

LAUNCH_STATUS = {"started": 202, "rejected": 409}
CONTROL_STATUS = {"acknowledged": 200, "indeterminate": 202, "rejected": 409}


@router.post("/api/sessions")
def launch(body: LaunchBody, application: ApplicationGraph) -> JSONResponse:
    result = application.launcher.launch(body.harness, body.request())
    return JSONResponse(to_wire(result), LAUNCH_STATUS[result.status])


@router.post("/api/sessions/{session_id}/controls")
def control(session_id: str, body: ControlBody, application: ApplicationGraph) -> JSONResponse:
    outcome = application.controls.execute(body.request(SessionId(session_id)))
    return JSONResponse(to_wire(outcome), CONTROL_STATUS[outcome.status])


@router.post("/api/terminal/panes")
def pane_command(body: PaneCommandBody, application: ApplicationGraph) -> JSONResponse:
    outcome = application.pane_commands.execute(
        body.command,
        body.window_id,
        body.working_directory,
        columns=body.columns,
        percent=body.percent,
    )
    reply = PaneCommandReply(
        handled=outcome.handled, succeeded=outcome.succeeded, reason=outcome.reason
    )
    status = 409 if outcome.handled and not outcome.succeeded else 200
    return JSONResponse(reply.model_dump(), status)


@router.post("/api/terminal/views")
def toggle_terminal_view(body: TerminalViewBody) -> Opened:
    opened = terminal_views.toggle(body.content_reference)
    A.state_file("", body.content_reference, "terminal-view", {"opened": opened})
    return Opened(opened=opened)
