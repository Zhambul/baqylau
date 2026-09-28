# Copyright (c) 2026 Zhambyl Yermagambet
"""Present the extension views that a core pane shows as sections for one session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.manifest.views import TerminalView as TerminalContribution
from baqylau_extension_api.terminal.models import TerminalView

from extensions.terminal_presentation import ViewSelection, present_view
from extensions.terminal_views import TerminalViewFailedError

if TYPE_CHECKING:
    from baqylau_extension_api.models.scopes import SessionScope
    from baqylau_extension_api.terminal.models import TerminalViewport

    from extensions.presentation_snapshots import PresentationSnapshots
    from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class PaneSections:
    """Keep the presented sections and the titles of views that could not be presented."""

    views: tuple[TerminalView, ...]
    unavailable: tuple[str, ...]


def pane_sections(
    packages: tuple[RegistryPackage, ...], snapshots: PresentationSnapshots, pane: str,
    scope: SessionScope, viewport: TerminalViewport,
) -> PaneSections:
    """Present every active session view that names the core pane, in package order.

    A view whose presenter fails is reported by title, so one extension never breaks a core pane.

    Returns:
        The checked views and the titles of the failed ones.

    """
    sections: list[TerminalView] = []
    unavailable: list[str] = []
    for found in _pane_views(packages, pane, scope):
        selection = ViewSelection(found.owner, found.view.view_id, scope, viewport)
        try:
            sections.append(present_view(packages, snapshots, selection))
        except TerminalViewFailedError:
            unavailable.append(found.view.title)
    return PaneSections(tuple(sections), tuple(unavailable))


@dataclass(frozen=True)
class _PaneView:
    owner: str
    view: TerminalContribution


def _pane_views(
    packages: tuple[RegistryPackage, ...], pane: str, scope: SessionScope,
) -> tuple[_PaneView, ...]:
    return tuple(
        _PaneView(package.manifest.extension_id, view)
        for package in packages
        for view in package.manifest.contributions.terminal
        if view.pane == pane and scope.kind in view.scopes
    )
