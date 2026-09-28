# Copyright (c) 2026 Zhambyl Yermagambet
"""List the extension terminal views that the pane selector offers for one window."""

from typing import Annotated

from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope
from fastapi import APIRouter, Query, Response

from api.extensions import terminal_pane_models as terminal_models
from app.provider_extension_registry import Registry
from app.provider_session_storage import SessionDataStore
from app.provider_terminal import Terminal
from domain.ids import WindowId
from extensions.repository_scopes import session_scopes
from extensions.terminal_views import available_views
from repository.contract.session_data import SessionDataRepository
from terminal.adapter import TerminalAdapter

router = APIRouter()


@router.get("/api/extension-terminal/views")
def extension_terminal_views(
    registry: Registry, terminal: Terminal, sessions: SessionDataStore, response: Response,
    window_id: Annotated[str | None, Query(min_length=1)] = None,
) -> terminal_models.AvailableViewsResponse:
    """Offer each active view for the installation, and for the window's session and its repository.

    Without a window, only installation views are offered.

    Returns:
        The views in package and view order, each with the scope it opens in.

    """
    response.headers["Cache-Control"] = "no-store"
    scopes = window_scopes(terminal, sessions, None if window_id is None else WindowId(window_id))
    with registry.read_snapshot() as read:
        views = available_views(read.snapshot.packages, scopes)
    return terminal_models.AvailableViewsResponse(views=tuple(
        terminal_models.AvailableViewResponse(
            extension_id=view.extension_id, view_id=view.view_id, title=view.title,
            scope=view.scope.model_dump_json(),
        )
        for view in views
    ))


def window_scopes(
    terminal_adapter: TerminalAdapter, session_data_repository: SessionDataRepository, window_id: WindowId | None,
) -> tuple[ExtensionScope, ...]:
    """Name the scopes of one window: the installation, then the window's session and its repository.

    Returns:
        The scopes, in that order.

    """
    session_id = None if window_id is None else terminal_adapter.session_for_window(window_id)
    session = None if session_id is None else session_data_repository.read(session_id)
    if session is None:
        return (InstallationScope(),)
    return (InstallationScope(), *session_scopes(session.session))
