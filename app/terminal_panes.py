"""Harness-neutral terminal pane commands."""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import pane_preferences

COMMANDS = frozenset({"toggle", "grow", "shrink", "reset", "setpct"})


def _remember_current_width(terminal, session_id) -> None:
    geometry = terminal.activity_pane_geometry(session_id)
    if geometry is None:
        return
    current_columns, total_columns = geometry
    if total_columns:
        pane_preferences.remember_width(
            os.getcwd(),
            round(100 * current_columns / total_columns),
        )


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments or arguments[0] not in COMMANDS:
        print(
            "usage: terminal_panes.py toggle|grow|shrink|reset|setpct [number]",
            file=sys.stderr,
        )
        return 2

    from app.bootstrap import build_default_application

    terminal = build_default_application().terminal
    session_id = terminal.current_session()
    if session_id is None:
        return 0

    command = arguments[0]
    if command == "toggle":
        result = terminal.toggle_session_panes(
            session_id,
            pane_preferences.width_percent(os.getcwd()),
        )
    elif command in ("grow", "shrink"):
        columns = (
            int(arguments[1])
            if len(arguments) > 1
            else pane_preferences.resize_columns()
        )
        if columns <= 0:
            raise ValueError("pane resize columns must be positive")
        result = terminal.resize_activity_pane(
            session_id,
            columns if command == "grow" else -columns,
        )
        if result.succeeded:
            _remember_current_width(terminal, session_id)
    else:
        if command == "setpct":
            if len(arguments) != 2:
                raise ValueError("setpct requires one percentage")
            width_percent = int(arguments[1])
        else:
            width_percent = pane_preferences.configured_width_percent()
        result = terminal.set_activity_pane_width(session_id, width_percent)
        if result.succeeded:
            pane_preferences.remember_width(os.getcwd(), width_percent)

    if not result.succeeded:
        raise RuntimeError(result.reason or "terminal pane command failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
