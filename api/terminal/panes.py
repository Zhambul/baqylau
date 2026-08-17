# api/terminal/panes.py — the pane keybinding gestures, one endpoint per
# command (the URL is the discriminator; the old single endpoint took a
# `command` field).
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.providers import PaneCommands
from api.guard import control_plane
from api.responses import GUARDED, with_body
from api.terminal.models.panes.grow_request import GrowPaneRequest
from api.terminal.models.panes.pane_command_response import PaneCommandResponse
from api.terminal.models.panes.reset_request import ResetPaneRequest
from api.terminal.models.panes.set_percent_request import SetPanePercentRequest
from api.terminal.models.panes.shrink_request import ShrinkPaneRequest
from api.terminal.models.panes.toggle_request import TogglePanesRequest

router = APIRouter(dependencies=[Depends(control_plane())], responses=GUARDED)

# Same rule as the control plane: the verdict is the status, the body is
# unchanged. `handled=False` (no session in this window) is a 200 — nothing was
# asked of the terminal; a 409 is the terminal refusing.
PANE_RESPONSES = with_body(PaneCommandResponse, {
    409: "Handled by this window's session, and the terminal refused it.",
})


def _execute(panes, command, body, columns=None, percent=None) -> JSONResponse:
    outcome = panes.execute(
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


@router.post("/api/terminal/panes/toggle", response_model=PaneCommandResponse,
             responses=PANE_RESPONSES)
def toggle_panes(body: TogglePanesRequest, panes: PaneCommands) -> JSONResponse:
    return _execute(panes, "toggle", body)


@router.post("/api/terminal/panes/grow", response_model=PaneCommandResponse,
             responses=PANE_RESPONSES)
def grow_pane(body: GrowPaneRequest, panes: PaneCommands) -> JSONResponse:
    return _execute(panes, "grow", body, columns=body.columns)


@router.post("/api/terminal/panes/shrink", response_model=PaneCommandResponse,
             responses=PANE_RESPONSES)
def shrink_pane(body: ShrinkPaneRequest, panes: PaneCommands) -> JSONResponse:
    return _execute(panes, "shrink", body, columns=body.columns)


@router.post("/api/terminal/panes/reset", response_model=PaneCommandResponse,
             responses=PANE_RESPONSES)
def reset_pane(body: ResetPaneRequest, panes: PaneCommands) -> JSONResponse:
    return _execute(panes, "reset", body)


@router.post("/api/terminal/panes/set-percent", response_model=PaneCommandResponse,
             responses=PANE_RESPONSES)
def set_pane_percent(body: SetPanePercentRequest, panes: PaneCommands) -> JSONResponse:
    return _execute(panes, "setpct", body, percent=body.percent)
