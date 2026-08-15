#!/usr/bin/env python3
"""Render one canonical session inside its terminal mirror pane.

A thin client of the daemon: it copies the ANSI frames streamed by
`/api/sessions/<id>/panes/mirror/stream` to this pane and nothing else — the
presentation runs in the daemon (`app/pane_streams.py`). A pending identity is
resolved by the server and announced on the stream."""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import daemon_client
from terminal.renderer import HEADER


def run(session_id: str) -> None:
    sys.stdout.write("\033[H\033[2J\033[3J" + HEADER + "\n")
    sys.stdout.flush()
    daemon_client.run_pane(session_id, "mirror")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--pending" and sys.argv[2]:
        run(sys.argv[2])
        return
    if len(sys.argv) == 2 and sys.argv[1]:
        run(sys.argv[1])
        return
    raise SystemExit("usage: app/terminal_process.py SESSION_ID | --pending PENDING_ID")


if __name__ == "__main__":
    main()
