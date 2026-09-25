# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the action of a view that the host presents; the client names only its ID."""

from __future__ import annotations

from typing import TYPE_CHECKING

from extensions.terminal_presentation import present_view
from extensions.terminal_views import TerminalViewNotFoundError

if TYPE_CHECKING:
    from baqylau_extension_api.terminal.models import TerminalAction

    from extensions.presentation_snapshots import PresentationSnapshots
    from extensions.registry_package import RegistryPackage
    from extensions.terminal_presentation import ViewSelection


def presented_action(
    packages: tuple[RegistryPackage, ...], snapshots: PresentationSnapshots, selection: ViewSelection,
    action_id: str,
) -> TerminalAction:
    """Present the view again at the same focus and size, and find one of its actions.

    Returns:
        The action with its registered command, arguments, and expected state revision.

    Raises:
        TerminalViewNotFoundError: If the view or the action is not presented now.

    """
    view = present_view(packages, snapshots, selection)
    action = next((entry for entry in view.actions if entry.action_id == action_id), None)
    if action is None:
        message = "the presented view has no such action"
        raise TerminalViewNotFoundError(message)
    return action
