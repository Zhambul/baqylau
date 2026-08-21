"""Server-side execution of the terminal pane keybinding gestures.

The keybinding process is a thin HTTP client (`terminal/panes/client.py`): it can
only observe its own environment — the terminal window the keypress landed in
and the working directory — and ships both here. Everything the gesture *does*
(session lookup, pane control, remembered widths) runs in the daemon, on the
one application graph.
"""

from __future__ import annotations

from dataclasses import dataclass

from audit.recorder import AuditRecorder
from domain.ids import SessionId
from terminal.services.panes import PaneWidthService
from terminal.adapter import TerminalAdapter

COMMANDS = frozenset({"toggle", "grow", "shrink", "reset", "setpct"})


@dataclass(frozen=True)
class PaneCommandOutcome:
    handled: bool
    succeeded: bool
    reason: str | None = None


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

    def execute(
        self,
        command: str,
        window_id: str | None,
        working_directory: str,
        columns: int | None = None,
        percent: int | None = None,
    ) -> PaneCommandOutcome:
        if command not in COMMANDS:
            raise ValueError(f"unknown pane command: {command}")
        if not working_directory:
            raise ValueError("working_directory is required")
        session_id = self._terminal.session_for_window(window_id or None)
        outcome = (
            # A keypress in a tab hosting no session is not an error — the
            # binding is global and simply does nothing there.
            PaneCommandOutcome(False, True)
            if session_id is None
            else self._execute(command, session_id, working_directory, columns, percent)
        )
        self._audit.state_file(
            "",
            working_directory,
            "pane-command",
            {
                "command": command,
                "window_id": window_id or "",
                "session_id": str(session_id or ""),
                "ok": outcome.succeeded,
                "why": outcome.reason or "",
            },
        )
        return outcome

    def _execute(
        self,
        command: str,
        session_id: SessionId,
        working_directory: str,
        columns: int | None,
        percent: int | None,
    ) -> PaneCommandOutcome:
        if command == "toggle":
            result = self._terminal.toggle_session_panes(
                session_id,
                self._widths.width_percent(working_directory),
            )
        elif command in ("grow", "shrink"):
            step = self._widths.resize_columns() if columns is None else columns
            if step <= 0:
                raise ValueError("pane resize columns must be positive")
            result = self._terminal.resize_activity_pane(
                session_id,
                step if command == "grow" else -step,
            )
            if result.succeeded:
                self._remember_current_width(session_id, working_directory)
        else:
            if command == "setpct":
                if percent is None:
                    raise ValueError("setpct requires a percentage")
                width_percent = percent
            else:
                width_percent = self._widths.configured_width_percent()
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
