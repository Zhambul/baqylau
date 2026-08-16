#!/usr/bin/env python3
"""Render one canonical session inside its terminal mirror pane.

A thin client of the daemon: it copies the ANSI frames streamed by
`/api/sessions/<id>/panes/mirror/stream` to this pane and nothing else — the
presentation runs in the daemon (`terminal/panes/streams.py`)."""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.daemon import client as daemon_client
from terminal.mirror.renderer import HEADER


def run(session_id: str) -> None:
    sys.stdout.write("\033[H\033[2J\033[3J" + HEADER + "\n")
    sys.stdout.flush()
    daemon_client.run_pane(session_id, "mirror")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1]:
        run(sys.argv[1])
        return
    raise SystemExit("usage: terminal/panes/mirror_process.py SESSION_ID")


if __name__ == "__main__":
    main()
