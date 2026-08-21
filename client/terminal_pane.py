#!/usr/bin/env python3
"""Paint one session's mirror or scoreboard in this terminal, forever.

    terminal_pane.py HOST PORT SESSION_ID KIND

Both panes are this file, as before, but the daemon no longer renders: it serves
the session's aggregate and an append-only feed of entries, and the drawing
happens here (`_render.py`) over a local model (`_model.py`). Three consequences,
and they are the point of the change:

- A resize is a repaint, not a reconnect. The model is width-independent, so
  SIGWINCH redraws what is already in memory and nothing crosses the socket.
- The daemon holds no per-pane state at all: no shared block model, no lock, no
  keep-warm timer. Two panes on one session are two independent readers.
- A reconnect resumes from a cursor instead of re-rendering a session. The
  snapshot, one page of entries and the stream are read at one instant, and
  entry application is idempotent by entry_id, so an overlap after a drop is
  applied twice and shows once.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _handoff                                                  # noqa: E402
import _model                                                    # noqa: E402
import _render                                                   # noqa: E402
import _wire                                                     # noqa: E402

RECONNECT_DELAY_SECONDS = 2.0
# The scoreboard shows a running clock, and a clock has to be redrawn to move.
# The daemon used to re-render it once a second for exactly this reason; now the
# pane wakes itself instead, and only the pane that HAS a clock does — a mirror
# repaint is the whole feed, and there is nothing on it that changes with time.
CLOCK_TICK_SECONDS = 1.0
# A read that stalls this long means the daemon is GONE — that is the only thing
# this guard is for. It has to clear two missed heartbeats with slack: the stream
# sends a comment every 15 s when there is no news (api/sse.py), so anything
# shorter would make one lost heartbeat on an idle session look like a dead
# daemon and reconnect the pane on a schedule forever.
STREAM_STALL_SECONDS = 35.0
FALLBACK_WIDTH = 80


class Pane:
    """One connection's worth of state: the model, the width, and the screen.

    A class rather than a generator because the resize path needs to reach the
    model from a signal handler — a repaint has to be possible at a moment when
    the process is blocked on a socket read, and that is the whole reason a
    width change no longer costs a round trip.
    """

    def __init__(self, kind: str, session_id: str) -> None:
        self.kind = kind
        self.session_id = session_id
        self.model = _model.SessionModel()
        self.width = _width()
        self._busy = False
        self._resized = False
        # What the reader has expanded, and what the click handlers can copy.
        # Both live on disk between this process and the two programs the
        # terminal launches for a click (client/_handoff.py explains why).
        self._opened = _handoff.opened(session_id, kind)
        self._published: dict[str, str] = {}

    def paint(self) -> None:
        """One whole screen, in one write.

        Guarded against itself, because the resize path is a SIGNAL HANDLER: it
        runs on this same thread between two bytecodes, so a handler that painted
        while a paint was already in progress would interleave two screens' worth
        of escape sequences on one stdout. The flag covers the model read as well
        as the write — half a frame applied is as wrong to draw as half a screen.
        """
        if self._busy:
            self._resized = True
            return
        self._busy = True
        try:
            self.width = _width()
            if self.kind == "mirror":
                picture = _render.mirror(
                    self.model,
                    self.width,
                    copy=self._copy_link,
                    view=self._view_link,
                    opened=self._opened,
                )
                self._publish()
            else:
                picture = _render.scoreboard(self.model, self.width)
            sys.stdout.write(picture)
            sys.stdout.flush()
            self._resized = False
        finally:
            self._busy = False

    def _copy_link(self, name: str) -> str:
        return _render.COPY_SCHEME % (self.session_id, self.kind, name)

    def _view_link(self, entry_id: str) -> str:
        return _render.VIEW_SCHEME % (self.session_id, self.kind, entry_id)

    def _publish(self) -> None:
        """Hand the click handlers what this screen can copy.

        Only when it CHANGED: a paint happens several times a second on a busy
        session and the payload is the output of every command on screen, so
        rewriting an identical file each time would be the one wasteful thing in
        this loop.
        """
        targets = _render.copy_targets(self.model)
        if targets != self._published:
            _handoff.publish(self.session_id, self.kind, targets)
            self._published = targets

    def ticked(self, _signal_number: int = 0, _frame: object = None) -> None:
        """SIGALRM: a second passed, so the clock has moved.

        The same reentrancy rule as a resize — `paint` declines and defers if one
        is already running — and the same reason for being a signal at all: this
        process spends its life blocked on a socket read.
        """
        self.paint()

    def expanded(self, _signal_number: int = 0, _frame: object = None) -> None:
        """SIGUSR1: a click toggled a file open or closed.

        The handler wrote the new set and signalled us, so the state is on disk
        and this only has to re-read it. Re-read rather than tracked, because the
        handler is a different process and its file is the truth.
        """
        self._opened = _handoff.opened(self.session_id, self.kind)
        self.paint()

    def resized(self, _signal_number: int = 0, _frame: object = None) -> None:
        """SIGWINCH: the terminal changed shape.

        Paints straight from the handler, because the process is normally blocked
        on a socket read here and waiting for the next frame would leave the pane
        wrongly wrapped for as long as the session stays quiet. `paint` declines
        and defers if it is already running.
        """
        self.paint()

    def apply(self, event: str, data: str) -> None:
        self._busy = True
        try:
            if event == "sessionData":
                self.model.apply_frame(json.loads(data))
        finally:
            self._busy = False
        self.paint()

    def deferred_repaint(self) -> None:
        """A resize that arrived while a paint or an apply was running."""
        if self._resized:
            self.paint()


def _width() -> int:
    return shutil.get_terminal_size((FALLBACK_WIDTH, 24)).columns or FALLBACK_WIDTH


def _document(path: str, host: str, port: int) -> dict[str, Any] | None:
    payload = _daemon.get(path, host, port)
    if payload is None:
        return None
    try:
        document: dict[str, Any] = json.loads(payload)
    except ValueError:
        return None
    return document


def connect(pane: Pane, host: str, port: int, session_id: str) -> bool:
    """The aggregate and one page of the feed, at one instant.

    The page is read `at` the snapshot's cursor, so the two describe the same
    moment and the stream opened from that cursor picks up exactly where the page
    stops — no gap to fill and no overlap to reconcile.
    """
    snapshot = _document(_wire.SESSION_DATA_PATH % session_id, host, port)
    if snapshot is None:
        return False
    pane.model.apply_snapshot(snapshot)
    page = _document(
        _wire.SESSION_ENTRIES_PATH % (session_id, pane.model.cursor), host, port
    )
    if page is not None:
        pane.model.apply_page(page)
    return True


def follow(pane: Pane, host: str, port: int, session_id: str) -> None:
    """Apply the stream until it ends. Raises OSError to `main`, whose job is to
    reconnect."""
    event = ""
    for line in _daemon.lines(
        _wire.SESSION_STREAM_PATH % (session_id, pane.model.cursor),
        host,
        port,
        STREAM_STALL_SECONDS,
    ):
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            if event == "error":
                return
            pane.apply(event, line[len("data: "):])
        else:
            # A heartbeat comment, or the blank line that ends a frame. Both are
            # a chance to catch a resize that arrived while the read was blocked.
            pane.deferred_repaint()


def main(arguments: list[str]) -> None:
    if len(arguments) != 4:
        raise SystemExit("usage: terminal_pane.py HOST PORT SESSION_ID KIND")
    host, port_text, session_id, kind = arguments
    if kind not in ("mirror", "scoreboard"):
        raise SystemExit("unknown pane kind: %s" % kind)
    port = int(port_text)
    while True:
        # A fresh model per connection: a daemon that restarted may have
        # rebuilt its read model, and resuming a cursor into a rebuilt one
        # would paint two histories at once.
        pane = Pane(kind, session_id)
        signal.signal(signal.SIGWINCH, pane.resized)
        signal.signal(_handoff.REPAINT_SIGNAL, pane.expanded)
        # Claim this session-and-kind as the pane a click wakes. Taken once and
        # held for the life of the process: the lock is what tells a click
        # handler that the pid beside it is still ours to signal.
        _handoff.hold(session_id, kind)
        if kind == "scoreboard":
            signal.signal(signal.SIGALRM, pane.ticked)
            signal.setitimer(signal.ITIMER_REAL, CLOCK_TICK_SECONDS, CLOCK_TICK_SECONDS)
        try:
            if connect(pane, host, port, session_id):
                pane.paint()
                follow(pane, host, port, session_id)
        except (OSError, ValueError):
            pass
        time.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    main(sys.argv[1:])
