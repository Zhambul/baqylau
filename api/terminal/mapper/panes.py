"""A pane command's outcome to the gesture's reply model."""

from __future__ import annotations

from api.terminal.models.panes.pane_command_response import PaneCommandResponse
from terminal.panes.commands import PaneCommandOutcome


def pane_command(outcome: PaneCommandOutcome) -> PaneCommandResponse:
    return PaneCommandResponse(
        handled=outcome.handled, succeeded=outcome.succeeded, reason=outcome.reason
    )
