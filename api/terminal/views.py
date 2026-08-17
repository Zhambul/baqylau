# api/terminal/views.py — the click-to-open toggle for terminal content views.
#
# A route, and nothing else. It used to call the storage module and write the
# audit row itself, which made it the one route in the tree that was its own
# service; both now belong to `terminal/services/views.py`.
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.dependencies import ApplicationGraph
from api.guard import control_plane
from api.terminal.models.views.opened_response import OpenedResponse
from api.terminal.models.views.toggle_view_request import ToggleViewRequest

router = APIRouter(dependencies=[Depends(control_plane())])


@router.post("/api/terminal/views")
def toggle_terminal_view(
    body: ToggleViewRequest,
    application: ApplicationGraph,
) -> OpenedResponse:
    return OpenedResponse(opened=application.content_views.toggle(body.content_reference))
