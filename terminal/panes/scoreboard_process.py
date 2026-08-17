#!/usr/bin/env python3
"""Render one canonical session's scoreboard in its terminal pane.

A thin client of the daemon: it copies the ANSI frames streamed by
`/api/sessions/<id>/panes/scoreboard/stream` to this pane — the numbers are
computed and rendered in the daemon (`terminal/panes/streams.py`)."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # parents[2], not two dirname() calls: this file sits at
    # terminal/panes/<name>.py, so walking up twice lands on terminal/ and the
    # repo root — where `core` and `terminal` live — never reaches sys.path.
    # A terminal launches these as SCRIPTS, so the guard above is always taken
    # and the import below is the first thing that fails.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
