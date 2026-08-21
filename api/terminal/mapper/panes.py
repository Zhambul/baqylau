"""A pane command's outcome to the gesture's reply model."""

from __future__ import annotations

from api.terminal.models.panes.pane_command_response import PaneCommandResponse
from terminal.panes.commands import PaneCommandOutcome


def pane_command(pane_command_outcome: PaneCommandOutcome) -> PaneCommandResponse:
    return PaneCommandResponse(
        handled=pane_command_outcome.handled,
        succeeded=pane_command_outcome.succeeded,
        reason=pane_command_outcome.reason,
    )
