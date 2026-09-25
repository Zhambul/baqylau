# Copyright (c) 2026 Zhambyl Yermagambet
"""Present an active extension terminal view at the pane size."""

from dataclasses import replace
from http import HTTPStatus
from typing import Annotated

from baqylau_extension_api.terminal.models import MAX_VIEWPORT_SIZE, TerminalSelection, TerminalViewport
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from api.extensions import terminal_models
from api.extensions.scope_documents import request_scope
from app.provider_extension_registry import Registry
from app.provider_extension_terminal import Snapshots
from extensions.terminal_presentation import ViewSelection, present_view
from extensions.terminal_views import TerminalViewNotFoundError

router = APIRouter()
TERMINAL_ROUTE = "/api/extension-terminal"


def view_selection(
    extension_id: str, view_id: str, scope: Annotated[str, Query(min_length=1)],
    columns: Annotated[int, Query(ge=1, le=MAX_VIEWPORT_SIZE)],
    rows: Annotated[int, Query(ge=1, le=MAX_VIEWPORT_SIZE)],
) -> ViewSelection:
    """Read the view, its scope, and the pane size from the request.

    Returns:
        The checked selection.

    """
    return ViewSelection(extension_id, view_id, request_scope(scope), TerminalViewport(columns=columns, rows=rows))


def view_focus(
    block_id: Annotated[str | None, Query(alias="block", min_length=1)] = None,
    item_id: Annotated[str | None, Query(alias="item", min_length=1)] = None,
) -> TerminalSelection | None:
    """Read the focused block and item of the pane, if any.

    Returns:
        The focus, or None.

    """
    return None if block_id is None else TerminalSelection(block_id=block_id, item_id=item_id)


@router.get(f"{TERMINAL_ROUTE}/views/{{extension_id}}/{{view_id}}")
def extension_terminal_view(
    selection: Annotated[ViewSelection, Depends(view_selection)],
    focus: Annotated[TerminalSelection | None, Depends(view_focus)],
    registry: Registry, snapshots: Snapshots, response: Response,
) -> terminal_models.TerminalViewResponse:
    """Present one active view at the pane size.

    Returns:
        The checked view.

    Raises:
        HTTPException: If no active package declares the view; the pane then ends.

    """
    response.headers["Cache-Control"] = "no-store"
    with registry.read_snapshot() as read:
        try:
            view = present_view(read.snapshot.packages, snapshots, replace(selection, focus=focus))
        except TerminalViewNotFoundError as error:
            raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    return terminal_models.TerminalViewResponse(view=view)
