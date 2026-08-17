# api/terminal/views.py — the click-to-open toggle for terminal content views.
#
# A route, and nothing else. It used to call the storage module and write the
# audit row itself, which made it the one route in the tree that was its own
# service; both now belong to `terminal/services/views.py`.
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.providers import ContentViews
from api.guard import control_plane
from api.responses import GUARDED
from api.terminal.models.views.opened_response import OpenedResponse
from api.terminal.models.views.toggle_view_request import ToggleViewRequest

router = APIRouter(dependencies=[Depends(control_plane())], responses=GUARDED)


@router.post("/api/terminal/views")
def toggle_terminal_view(
    body: ToggleViewRequest,
    views: ContentViews,
) -> OpenedResponse:
    return OpenedResponse(opened=views.toggle(body.content_reference))
