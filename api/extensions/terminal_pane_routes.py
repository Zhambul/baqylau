# Copyright (c) 2026 Zhambyl Yermagambet
"""Open extension panes through the terminal service; a reopen focuses the existing pane."""

from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException

from api.extensions import terminal_pane_models as terminal_models
from api.extensions.admission import require_extension_json
from api.extensions.scope_documents import request_scope
from app.provider_extension_registry import Registry
from app.provider_runtime import InstalledTerminal
from extensions.registry_contract import ExtensionRegistry
from extensions.terminal_views import TerminalViewNotFoundError, declaring_view
from terminal import extension_panes
from terminal.models.values import WindowId

router = APIRouter()


@router.post("/api/extension-terminal/panes", dependencies=[Depends(require_extension_json)])
def open_extension_pane(
    extension_pane_request: terminal_models.ExtensionPaneRequest, registry: Registry, terminal: InstalledTerminal,
) -> terminal_models.ExtensionPaneResponse:
    """Open the view in a pane beside the window, or focus its pane when one is already open.

    Returns:
        Whether a pane opened or took focus.

    """
    request = extension_panes.ExtensionPaneOpen(
        pane_ref(registry, extension_pane_request), WindowId(extension_pane_request.window_id),
        extension_pane_request.working_directory,
    )
    windows = tuple(terminal.metadata.windows())
    opened = extension_panes.open_extension_pane(terminal.panes, windows, request)
    if opened is None:
        return terminal_models.ExtensionPaneResponse(opened=False, focused=True)
    return terminal_models.ExtensionPaneResponse(
        opened=opened.succeeded, focused=False, window_id=opened.window_id, reason=opened.reason,
    )


def pane_ref(
    registry: ExtensionRegistry, request: terminal_models.ExtensionPaneRequest,
) -> extension_panes.ExtensionPaneRef:
    """Name the pane by the active declaration and the current runtime.

    Returns:
        The pane reference.

    Raises:
        HTTPException: If no active package declares the view for the scope kind.

    """
    scope = request_scope(request.scope)
    with registry.read_snapshot() as read:
        snapshot = read.snapshot
    try:
        _, view = declaring_view(snapshot.packages, request.extension_id, request.view_id, scope.kind)
    except TerminalViewNotFoundError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    return extension_panes.ExtensionPaneRef(
        request.extension_id, request.view_id, scope.model_dump_json(), snapshot.directory.runtime_revision,
        view.title,
    )
