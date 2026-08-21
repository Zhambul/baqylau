# api/terminal/panes.py — the pane keybinding gestures, one endpoint per
# command (the URL is the discriminator; the old single endpoint took a
# `command` field).
from __future__ import annotations

from fastapi import APIRouter, Response

from app.providers import PaneCommands
from api.responses import with_body
from api.terminal.models.panes.grow_request import GrowPaneRequest
from api.terminal.mapper import panes as mapper
from api.terminal.models.panes.pane_command_response import PaneCommandResponse
from api.terminal.models.panes.pane_gesture_request import PaneGestureRequest
from api.terminal.models.panes.reset_request import ResetPaneRequest
from api.terminal.models.panes.set_percent_request import SetPanePercentRequest
from api.terminal.models.panes.shrink_request import ShrinkPaneRequest
from api.terminal.models.panes.toggle_request import TogglePanesRequest
from domain.ids import WindowId
from terminal.panes.commands import PaneCommand, PaneCommandService

router = APIRouter()

# Same rule as the control plane: the verdict is the status, the body is
# unchanged. `handled=False` (no session in this window) is a 200 — nothing was
# asked of the terminal; a 409 is the terminal refusing.
PANE_RESPONSES = with_body(PaneCommandResponse, {
    409: "Handled by this window's session, and the terminal refused it.",
})


def _execute(
    pane_command_service: PaneCommandService,
    pane_command: PaneCommand,
    pane_gesture_request: PaneGestureRequest,
    response: Response,
    columns: int | None = None,
    percent: int | None = None,
) -> PaneCommandResponse:
    """The status is set ON the injected response, so the handler can return the
    reply model itself rather than a hand-serialized copy of it."""
    outcome = pane_command_service.execute(
        pane_command,
        WindowId(pane_gesture_request.window_id) if pane_gesture_request.window_id else None,
        pane_gesture_request.working_directory,
        columns=columns,
        percent=percent,
    )
    response.status_code = 409 if outcome.handled and not outcome.succeeded else 200
    return mapper.pane_command(outcome)


@router.post("/api/terminal/panes/toggle", responses=PANE_RESPONSES)
def toggle_panes(
    toggle_panes_request: TogglePanesRequest, panes: PaneCommands, response: Response
) -> PaneCommandResponse:
    return _execute(panes, PaneCommand.TOGGLE, toggle_panes_request, response)


@router.post("/api/terminal/panes/grow", responses=PANE_RESPONSES)
def grow_pane(
    grow_pane_request: GrowPaneRequest, panes: PaneCommands, response: Response
) -> PaneCommandResponse:
    return _execute(panes, PaneCommand.GROW, grow_pane_request, response, columns=grow_pane_request.columns)


@router.post("/api/terminal/panes/shrink", responses=PANE_RESPONSES)
def shrink_pane(
    shrink_pane_request: ShrinkPaneRequest, panes: PaneCommands, response: Response
) -> PaneCommandResponse:
    return _execute(panes, PaneCommand.SHRINK, shrink_pane_request, response, columns=shrink_pane_request.columns)


@router.post("/api/terminal/panes/reset", responses=PANE_RESPONSES)
def reset_pane(
    reset_pane_request: ResetPaneRequest, panes: PaneCommands, response: Response
) -> PaneCommandResponse:
    return _execute(panes, PaneCommand.RESET, reset_pane_request, response)


@router.post("/api/terminal/panes/set-percent", responses=PANE_RESPONSES)
def set_pane_percent(
    set_pane_percent_request: SetPanePercentRequest, panes: PaneCommands, response: Response
) -> PaneCommandResponse:
    return _execute(
        panes, PaneCommand.SETPCT, set_pane_percent_request, response, percent=set_pane_percent_request.percent
    )
