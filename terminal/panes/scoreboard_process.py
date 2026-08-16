#!/usr/bin/env python3
"""Render one canonical session's scoreboard in its terminal pane.

A thin client of the daemon: it copies the ANSI frames streamed by
`/api/sessions/<id>/panes/scoreboard/stream` to this pane — the numbers are
computed and rendered in the daemon (`terminal/panes/streams.py`)."""

from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.daemon import client as daemon_client


def run(session_id: str) -> None:
    daemon_client.run_pane(session_id, "scoreboard")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1]:
        run(sys.argv[1])
        return
    raise SystemExit("usage: terminal/panes/scoreboard_process.py SESSION_ID")


if __name__ == "__main__":
    main()
