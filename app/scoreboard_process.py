#!/usr/bin/env python3
"""Render one canonical session's scoreboard in its terminal pane."""

from __future__ import annotations

import os
import shutil
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bootstrap import build_default_application
from app import pending_session
from domain.ids import SessionId
from runtime.projections import ActivityScope
from terminal.renderer import TerminalRenderer
from terminal.scoreboard import ScoreboardPresenter, ScoreboardSnapshot

POLL_SECONDS = 0.25


def terminal_width() -> int:
    return shutil.get_terminal_size().columns


def run(session_id: SessionId) -> None:
    application = build_default_application()
    presenter = ScoreboardPresenter()
    renderer = TerminalRenderer(terminal_width())
    rendered_cursor = None
    rendered_second = None
    summary = None
    usage = None
    statistics = None
    active_seconds = 0.0
    measured_at = time.time()
    active = False
    while True:
        cursor = application.canonical_store.latest_session_cursor(session_id) or 0
        current_time = time.time()
        current_second = int(current_time)
        width = terminal_width()
        if cursor != rendered_cursor or current_second != rendered_second or width != renderer.width:
            if cursor != rendered_cursor:
                summary = application.queries.summary(session_id, cursor)
                if summary is None:
                    raise RuntimeError(f"session {session_id} has no canonical start event")
                usage = application.queries.usage(session_id, cursor)
                statistics = application.queries.statistics(
                    session_id,
                    ActivityScope(),
                    cursor,
                )
                active_seconds = application.queries.active_seconds(
                    session_id,
                    current_time,
                    cursor,
                )
                tab_state = application.queries.tab_state(session_id, cursor)
                active = tab_state not in (None, "idle")
                measured_at = current_time
            assert summary is not None and usage is not None and statistics is not None
            snapshot = ScoreboardSnapshot(
                session=summary,
                usage=usage,
                statistics=statistics,
                active_seconds=(
                    active_seconds + current_time - measured_at
                    if active
                    else active_seconds
                ),
            )
            renderer.reflow(width)
            renderer.apply(presenter.present(snapshot, width))
            sys.stdout.write(renderer.ansi())
            sys.stdout.flush()
            rendered_cursor = cursor
            rendered_second = current_second
        time.sleep(POLL_SECONDS)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--pending" and sys.argv[2]:
        sys.stdout.write("\033[H\033[2J\033[3J ⬡ starting session…\033[0m")
        sys.stdout.flush()
        run(pending_session.wait(SessionId(sys.argv[2])))
        return
    if len(sys.argv) == 2 and sys.argv[1]:
        run(SessionId(sys.argv[1]))
        return
    raise SystemExit("usage: app/scoreboard_process.py SESSION_ID | --pending PENDING_ID")


if __name__ == "__main__":
    main()
