"""Harness-neutral terminal pane commands — the keybinding's thin client.

A kitty keymap launches this per keypress. It observes the two facts only this
process can (the window the keypress landed in, the working directory), ships
them to the daemon's `/api/terminal/panes`, and prints any refusal. The
gesture itself runs in the daemon (`app/pane_commands.py`)."""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COMMANDS = frozenset({"toggle", "grow", "shrink", "reset", "setpct"})


def request_body(arguments: list[str]) -> dict:
    import frontends

    command = arguments[0]
    body = {
        "command": command,
        "window_id": frontends.current_window_id() or "",
        "working_directory": os.getcwd(),
    }
    if command in ("grow", "shrink") and len(arguments) > 1:
        body["columns"] = int(arguments[1])
    if command == "setpct":
        if len(arguments) != 2:
            raise ValueError("setpct requires one percentage")
        body["percent"] = int(arguments[1])
    return body


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if not arguments or arguments[0] not in COMMANDS:
        print(
            "usage: terminal_panes.py toggle|grow|shrink|reset|setpct [number]",
            file=sys.stderr,
        )
        return 2

    from app import daemon_client

    status, payload = daemon_client.post_json("/api/terminal/panes", request_body(arguments))
    if status != 200:
        print(
            payload.get("reason") or payload.get("error") or "terminal pane command failed",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
