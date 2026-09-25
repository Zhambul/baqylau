# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask an active package's presenter for one terminal view at its recorded data revision.

A view that names a query also gets the query's result as its document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.manifest.views import TerminalView
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.terminal import models as terminal_models
from baqylau_extension_api.terminal.validation import validate_terminal_view

from extensions.presentation_snapshots import PresentationSnapshots
from extensions.registry_package import RegistryPackage
from extensions.terminal_documents import view_document
from extensions.terminal_views import TerminalViewFailedError, TerminalViewNotFoundError, declaring_view

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.presentation import ExtensionTerminalPresenter


@dataclass(frozen=True)
class ViewSelection:
    """Name one declared terminal view, its scope, the pane size, and the focused item."""

    extension_id: str
    view_id: str
    scope: ExtensionScope
    viewport: terminal_models.TerminalViewport
    focus: terminal_models.TerminalSelection | None = None


@dataclass(frozen=True)
class ViewSource:
    """Keep the declaring package, its view, and the presenter inputs of one presentation."""

    package: RegistryPackage
    view: TerminalView
    snapshots: PresentationSnapshots


def present_view(
    packages: tuple[RegistryPackage, ...], snapshots: PresentationSnapshots, selection: ViewSelection,
) -> terminal_models.TerminalView:
    """Call the presenter with the recorded snapshot, resolved settings, and view document, then check its result.

    A presenter failure or a result that is not valid is a `TerminalViewFailedError`.

    Returns:
        The checked view.

    Raises:
        TerminalViewNotFoundError: If no active package declares the view for the scope kind.

    """
    package, view = declaring_view(packages, selection.extension_id, selection.view_id, selection.scope.kind)
    presenter = None if package.plugin is None else package.plugin.capabilities.terminal
    if presenter is None:
        message = "the extension has no active terminal presenter"
        raise TerminalViewNotFoundError(message)
    request = _view_request(ViewSource(package, view, snapshots), selection)
    return snapshots.views.presented(request, lambda: _checked_view(presenter, request))


def _view_request(source: ViewSource, selection: ViewSelection) -> terminal_models.TerminalViewRequest:
    package, snapshots = source.package, source.snapshots
    runtime_revision = "" if package.environment is None else package.environment.runtime_revision
    return terminal_models.TerminalViewRequest(
        binding=terminal_models.TerminalBinding(
            extension_id=selection.extension_id, view_id=selection.view_id, runtime_revision=runtime_revision,
            settings_revision=package.settings.revision,
            snapshot=snapshots.snapshot(selection.extension_id, selection.scope),
        ),
        viewport=selection.viewport,
        selection=selection.focus,
        settings=package.resolved_settings.for_scope(selection.scope),
        document=view_document(package, source.view, selection.scope, selection.focus, snapshots.queries),
    )


def _checked_view(
    presenter: ExtensionTerminalPresenter, request: terminal_models.TerminalViewRequest,
) -> terminal_models.TerminalView:
    try:
        return validate_terminal_view(request, presenter.present(request))
    except Exception as error:
        # Any worker failure or refused result is one host message; worker text never reaches the pane.
        message = "the extension view failed"
        raise TerminalViewFailedError(message) from error
