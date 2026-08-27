"""Server-side execution of the terminal pane keybinding gestures.

The keybinding process is a thin HTTP client (`terminal/panes/client.py`): it can
only observe its own environment — the terminal window the keypress landed in
and the working directory — and ships both here. Everything the gesture *does*
(session lookup, pane control, remembered widths) runs in the daemon, on the
one application graph.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from audit.models import AuditDocument
from audit.recorder import AuditRecorder
from domain.ids import SessionId, WindowId
from terminal.services.panes import PaneWidthService
from terminal.adapter import TerminalAdapter


class PaneCommand(StrEnum):
    TOGGLE = "toggle"
    GROW = "grow"
    SHRINK = "shrink"
    RESET = "reset"
    SETPCT = "setpct"


@dataclass(frozen=True)
class PaneCommandOutcome:
    handled: bool
    succeeded: bool
    reason: str | None = None


class PaneCommandAudit(AuditDocument):
    command: PaneCommand
    window_id: WindowId
    session_id: SessionId
    ok: bool
    why: str


class PaneCommandService:
    def __init__(
        self,
        terminal_adapter: TerminalAdapter,
        pane_width_service: PaneWidthService,
        audit_recorder: AuditRecorder,
    ) -> None:
        self._terminal = terminal_adapter
        self._widths = pane_width_service
        self._audit = audit_recorder

    def toggle(self, window_id: WindowId | None, working_directory: str) -> PaneCommandOutcome:
        return self._audited(
            PaneCommand.TOGGLE,
            window_id,
            working_directory,
            lambda session_id: self._toggle(session_id, working_directory),
        )

    def grow(
        self, window_id: WindowId | None, working_directory: str, columns: int | None = None
    ) -> PaneCommandOutcome:
        return self._audited(
            PaneCommand.GROW,
            window_id,
            working_directory,
            lambda session_id: self._resize(session_id, working_directory, columns, grow=True),
        )

    def shrink(
        self, window_id: WindowId | None, working_directory: str, columns: int | None = None
    ) -> PaneCommandOutcome:
        return self._audited(
            PaneCommand.SHRINK,
            window_id,
            working_directory,
            lambda session_id: self._resize(session_id, working_directory, columns, grow=False),
        )

    def reset(self, window_id: WindowId | None, working_directory: str) -> PaneCommandOutcome:
        return self._audited(
            PaneCommand.RESET,
            window_id,
            working_directory,
            lambda session_id: self._set_width(
                session_id, working_directory, self._widths.configured_width_percent()
            ),
        )

    def set_percent(self, window_id: WindowId | None, working_directory: str, percent: int) -> PaneCommandOutcome:
        return self._audited(
            PaneCommand.SETPCT,
            window_id,
            working_directory,
            lambda session_id: self._set_width(session_id, working_directory, percent),
        )

    # The one core every public method flows through: it resolves the
    # session for the window, runs the gesture, and writes the ONE audit row
    # for this command. No public method may write its own audit row.
    def _audited(
        self,
        pane_command: PaneCommand,
        window_id: WindowId | None,
        working_directory: str,
        gesture: Callable[[SessionId], PaneCommandOutcome],
    ) -> PaneCommandOutcome:
        if not working_directory:
            raise ValueError("working_directory is required")
        session_id = self._terminal.session_for_window(window_id or None)
        outcome = (
            # A keypress in a tab hosting no session is not an error — the
            # binding is global and simply does nothing there.
            PaneCommandOutcome(False, True)
            if session_id is None
            else gesture(session_id)
        )
        self._audit.state_file(
            "",
            working_directory,
            "pane-command",
            PaneCommandAudit(
                command=pane_command,
                window_id=window_id or WindowId(""),
                session_id=session_id or SessionId(""),
                ok=outcome.succeeded,
                why=outcome.reason or "",
            ),
        )
        return outcome

    def _toggle(self, session_id: SessionId, working_directory: str) -> PaneCommandOutcome:
        result = self._terminal.toggle_session_panes(session_id, self._widths.width_percent(working_directory))
        return PaneCommandOutcome(True, result.succeeded, result.reason)

    def _resize(
        self, session_id: SessionId, working_directory: str, columns: int | None, grow: bool
    ) -> PaneCommandOutcome:
        step = self._widths.resize_columns() if columns is None else columns
        if step <= 0:
            raise ValueError("pane resize columns must be positive")
        result = self._terminal.resize_activity_pane(session_id, step if grow else -step)
        if result.succeeded:
            self._remember_current_width(session_id, working_directory)
        return PaneCommandOutcome(True, result.succeeded, result.reason)

    def _set_width(self, session_id: SessionId, working_directory: str, width_percent: int) -> PaneCommandOutcome:
        result = self._terminal.set_activity_pane_width(session_id, width_percent)
        if result.succeeded:
            self._widths.remember_width(working_directory, width_percent)
        return PaneCommandOutcome(True, result.succeeded, result.reason)

    def _remember_current_width(self, session_id: SessionId, working_directory: str) -> None:
        geometry = self._terminal.activity_pane_geometry(session_id)
        if geometry is None:
            return
        current_columns, total_columns = geometry
        if total_columns:
            self._widths.remember_width(
                working_directory,
                round(100 * current_columns / total_columns),
            )
