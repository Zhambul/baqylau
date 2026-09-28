# Copyright (c) 2026 Zhambyl Yermagambet
"""Present the extension sections of a session's mirror or scoreboard pane."""

from dataclasses import dataclass
from typing import Annotated

from baqylau_extension_api.models.scopes import SessionScope
from baqylau_extension_api.terminal.models import MAX_VIEWPORT_SIZE, TerminalViewport
from fastapi import APIRouter, Depends, Query, Response

from api.extensions import terminal_models
from app.provider_extension_registry import Registry
from app.provider_extension_terminal import Snapshots
from app.provider_session_storage import SessionDataStore
from domain.ids import SessionId
from extensions.terminal_sections import pane_sections

router = APIRouter()
type ViewportSize = Annotated[int, Query(ge=1, le=MAX_VIEWPORT_SIZE)]


@dataclass(frozen=True)
class SectionRequest:
    """Name the session's lead actor scope, the core pane, and its size."""

    scope: SessionScope | None
    pane: terminal_models.CorePane
    viewport: TerminalViewport


def section_request(
    session_id: str, pane: terminal_models.CorePane, columns: ViewportSize, rows: ViewportSize,
    sessions: SessionDataStore,
) -> SectionRequest:
    """Read the session scope of the pane, or none for an unknown session.

    Returns:
        The checked request.

    """
    session = sessions.read(SessionId(session_id))
    facts = None if session is None else session.session
    scope = None if facts is None else SessionScope(
        session_id=facts.session_id, actor_id=facts.lead_actor_id, harness=facts.harness,
    )
    return SectionRequest(scope, pane, TerminalViewport(columns=columns, rows=rows))


@router.get("/api/extension-terminal/sessions/{session_id}/{pane}")
def extension_pane_sections(
    request: Annotated[SectionRequest, Depends(section_request)], registry: Registry, snapshots: Snapshots,
    response: Response,
) -> terminal_models.PaneSectionsResponse:
    """Present the active session views that name this core pane.

    Returns:
        The sections for the session's lead actor, or none for an unknown session.

    """
    response.headers["Cache-Control"] = "no-store"
    if request.scope is None:
        return terminal_models.PaneSectionsResponse(views=(), unavailable=())
    with registry.read_snapshot() as read:
        found = pane_sections(read.snapshot.packages, snapshots, request.pane, request.scope, request.viewport)
    return terminal_models.PaneSectionsResponse(views=found.views, unavailable=found.unavailable)
