# api/terminal/views.py — the click-to-open toggle for terminal content views.
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.guard import control_plane
from api.terminal.models.views.opened_response import OpenedResponse
from api.terminal.models.views.toggle_view_request import ToggleViewRequest
from app import terminal_views
from core import audit as A

router = APIRouter(dependencies=[Depends(control_plane())])


@router.post("/api/terminal/views")
def toggle_terminal_view(body: ToggleViewRequest) -> OpenedResponse:
    opened = terminal_views.toggle(body.content_reference)
    A.state_file("", body.content_reference, "terminal-view", {"opened": opened})
    return OpenedResponse(opened=opened)
