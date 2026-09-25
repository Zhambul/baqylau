# Copyright (c) 2026 Zhambyl Yermagambet
"""Open, find, and close extension panes by their window tags; unrelated windows are never touched."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from core import clients
from terminal.models import panes as pane_models
from terminal.models.values import WindowId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from terminal.contract import TerminalPanes
    from terminal.models.pane_results import PaneOpenResponse
    from terminal.models.values import WindowInfo

EXTENSION_PANE_CLIENT = "terminal_extension_pane.py"
EXTENSION_TAG = "baqylau_extension"
VIEW_TAG = "baqylau_extension_view"
SCOPE_TAG = "baqylau_extension_scope"
RUNTIME_TAG = "baqylau_extension_runtime"
PANE_PERCENT = 40
IDENTITY_TAGS = (EXTENSION_TAG, VIEW_TAG, SCOPE_TAG)


@dataclass(frozen=True)
class ExtensionPaneRef:
    """Name one pane by its extension, view, and scope; the runtime revision is only a label."""

    owner: str
    view: str
    scope: str
    runtime_revision: str
    title: str

    def tags(self) -> Mapping[str, str]:
        """Write every identity of the pane on its window.

        Returns:
            The read-only window tags.

        """
        identity = (self.owner, self.view, self.scope, self.runtime_revision)
        tags = dict(zip((*IDENTITY_TAGS, RUNTIME_TAG), identity, strict=True))
        return MappingProxyType(tags)

    def owns(self, window_info: WindowInfo) -> bool:
        """Match a window by extension, view, and scope; a new runtime reuses the same pane.

        Returns:
            True for this pane's window.

        """
        found = tuple(window_info.tags.get(tag) for tag in IDENTITY_TAGS)
        return found == (self.owner, self.view, self.scope)


@dataclass(frozen=True)
class ExtensionPaneOpen:
    """Open one pane beside a window, or focus it when it is already open."""

    ref: ExtensionPaneRef
    anchor_window_id: WindowId
    working_directory: str


def pane_command(extension_pane_ref: ExtensionPaneRef) -> tuple[str, ...]:
    """Launch the extension pane client for one view.

    Returns:
        The command.

    """
    ref = extension_pane_ref
    return clients.command(EXTENSION_PANE_CLIENT, ref.owner, ref.view, ref.scope)


def open_extension_pane(
    terminal_panes: TerminalPanes, windows: tuple[WindowInfo, ...], extension_pane_open: ExtensionPaneOpen,
) -> PaneOpenResponse | None:
    """Focus the existing pane, which makes a reopen idempotent across restarts, or open a new one.

    Returns:
        The open response, or None when an existing pane took focus.

    """
    request = extension_pane_open
    existing = next((window for window in windows if request.ref.owns(window)), None)
    if existing is not None:
        terminal_panes.focus_window(pane_models.WindowFocusRequest(existing.window_id))
        return None
    return terminal_panes.open_pane(pane_models.PaneOpenRequest(
        command=pane_command(request.ref), working_directory=request.working_directory,
        title=request.ref.title, split=pane_models.SplitAxis.VERTICAL, size_percent=PANE_PERCENT,
        anchor=pane_models.PaneAnchor(window_id=request.anchor_window_id),
        same_tab_as=request.anchor_window_id, tags=request.ref.tags(),
    ))
