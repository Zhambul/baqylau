# api/terminal/panes.py — the pane keybinding gestures, one endpoint per
# command (the URL is the discriminator; the old single endpoint took a
# `command` field).
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.dependencies import ApplicationGraph
from api.guard import control_plane
from api.terminal.models.panes.grow_request import GrowPaneRequest
from api.terminal.models.panes.pane_command_response import PaneCommandResponse
from api.terminal.models.panes.reset_request import ResetPaneRequest
from api.terminal.models.panes.set_percent_request import SetPanePercentRequest
from api.terminal.models.panes.shrink_request import ShrinkPaneRequest
from api.terminal.models.panes.toggle_request import TogglePanesRequest

router = APIRouter(dependencies=[Depends(control_plane())])


def _execute(application, command, body, columns=None, percent=None) -> JSONResponse:
    outcome = application.pane_commands.execute(
        command,
        body.window_id,
        body.working_directory,
        columns=columns,
        percent=percent,
    )
    reply = PaneCommandResponse(
        handled=outcome.handled, succeeded=outcome.succeeded, reason=outcome.reason
    )
    status = 409 if outcome.handled and not outcome.succeeded else 200
    return JSONResponse(reply.model_dump(), status)


@router.post("/api/terminal/panes/toggle")
def toggle_panes(body: TogglePanesRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, "toggle", body)


@router.post("/api/terminal/panes/grow")
def grow_pane(body: GrowPaneRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, "grow", body, columns=body.columns)


@router.post("/api/terminal/panes/shrink")
def shrink_pane(body: ShrinkPaneRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, "shrink", body, columns=body.columns)


@router.post("/api/terminal/panes/reset")
def reset_pane(body: ResetPaneRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, "reset", body)


@router.post("/api/terminal/panes/set-percent")
def set_pane_percent(body: SetPanePercentRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, "setpct", body, percent=body.percent)
