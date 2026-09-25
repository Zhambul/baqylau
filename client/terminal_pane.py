#!/usr/bin/env python3
# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint one session mirror or scoreboard in a terminal pane."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _extension_sections
import _handoff
import _model
import _pane_connection
import _pane_rendering
import _pane_signals
import _pane_state

RECONNECT_DELAY_SECONDS = 2.0
CLOCK_TICK_SECONDS = 1.0
PANE_ARGUMENT_COUNT = 4


class Pane(_pane_rendering.PaneRendering, _pane_signals.PaneSignals, _pane_state.PaneState):
    """Hold state for one terminal-pane connection."""

    def __init__(self, kind: str, session_id: str, sections: _extension_sections.SectionCache) -> None:
        """Initialize the object."""
        self.kind = kind
        self.session_id = session_id
        self.sections = sections
        self.model = _model.SessionModel()
        self.width = _pane_rendering.terminal_width()
        self._busy = False
        self._resized = False
        self._opened = _handoff.opened(session_id, kind)
        self._published: dict[str, str] = {}


def main(arguments: list[str]) -> None:
    """Run the command."""
    host, port, session_id, kind = _arguments(arguments)
    _run_pane(host, port, session_id, kind)


def _arguments(arguments: list[str]) -> tuple[str, int, str, str]:
    if len(arguments) != PANE_ARGUMENT_COUNT:
        sys.exit("usage: terminal_pane.py HOST PORT SESSION_ID KIND")
    host, port_text, session_id, kind = arguments
    if kind not in {"mirror", "scoreboard"}:
        sys.exit(f"unknown pane kind: {kind}")
    return host, int(port_text), session_id, kind


def _run_pane(host: str, port: int, session_id: str, kind: str) -> None:
    while True:
        pane = Pane(kind, session_id, _extension_sections.SectionCache(host, port, session_id, kind))
        signal.signal(signal.SIGWINCH, pane.resized)
        signal.signal(_handoff.REPAINT_SIGNAL, pane.expanded)
        _handoff.hold(session_id, kind)
        if kind == "scoreboard":
            signal.signal(signal.SIGALRM, pane.ticked)
            signal.setitimer(signal.ITIMER_REAL, CLOCK_TICK_SECONDS, CLOCK_TICK_SECONDS)
        try:
            if _pane_connection.connect(pane, host, port, session_id):
                pane.paint()
                _pane_connection.follow(pane, host, port, session_id)
        except (OSError, ValueError):
            time.sleep(RECONNECT_DELAY_SECONDS)
            continue
        time.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    main(sys.argv[1:])
