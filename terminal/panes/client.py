"""Harness-neutral terminal pane commands — the keybinding's thin client.

A terminal keymap launches this per keypress. It observes the two facts only this
process can (the window the keypress landed in, the working directory), ships
them to the daemon's per-gesture pane endpoint, and prints any refusal. The
gesture itself runs in the daemon (`terminal/panes/commands.py`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    # parents[2], not two dirname() calls: this file sits at
    # terminal/panes/<name>.py, so walking up twice lands on terminal/ and the
    # repo root — where `core` and `terminal` live — never reaches sys.path.
    # A terminal launches these as SCRIPTS, so the guard above is always taken
    # and the import below is the first thing that fails.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# keybinding word → the daemon's per-gesture endpoint (one endpoint per
# command; the URL is the discriminator, so the body carries no command word)
COMMAND_PATHS = {
    "toggle": "/api/terminal/panes/toggle",
    "grow": "/api/terminal/panes/grow",
    "shrink": "/api/terminal/panes/shrink",
    "reset": "/api/terminal/panes/reset",
    "setpct": "/api/terminal/panes/set-percent",
}


def request_body(arguments: list[str]) -> dict:
    # Deferred, and one of the two sanctioned direct resolutions: this process
    # runs inside the window the keypress landed in and is the only thing that
    # can observe which one that is.
    from terminal.impl import resolve  # noqa: PLC0415 — one of the two sanctioned direct resolutions; see above

    terminal = resolve()
    command = arguments[0]
    body: dict[str, object] = {
        "window_id": (terminal.metadata.current_window_id() if terminal is not None else None) or "",
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
    if not arguments or arguments[0] not in COMMAND_PATHS:
        print(
            "usage: terminal/bin/panes.py toggle|grow|shrink|reset|setpct [number]",
            file=sys.stderr,
        )
        return 2

    from core.daemon import client as daemon_client  # noqa: PLC0415 — keeps this keypress entry import-thin

    status, payload = daemon_client.post_json(
        COMMAND_PATHS[arguments[0]], request_body(arguments)
    )
    if status != 200:
        print(
            payload.get("reason") or payload.get("error") or "terminal pane command failed",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
