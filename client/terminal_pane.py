#!/usr/bin/env python3
"""Copy one session's pane frames to this terminal, forever.

Both panes are this file: the daemon renders every frame
(`terminal/panes/streams.py`) and tells the process where to connect and which
stream to open, so mirror and scoreboard differ only in the KIND on the argv.

    terminal_pane.py HOST PORT SESSION_ID KIND

Reconnect on a resize — the server re-renders its shared model at the new width
— and after a pause when the daemon is restarting. The `session` event re-keys
the stream when the daemon reports a different session id for this pane.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _wire                                                     # noqa: E402

RECONNECT_DELAY_SECONDS = 2.0
# The server ticks a pane stream several times a second; a read that stalls this
# long means the daemon died without closing the socket — reconnect.
STREAM_STALL_SECONDS = 10.0


def frames(host: str, port: int, session_id: str, kind: str) -> Iterator[str]:
    """Yield ANSI frames forever."""
    while True:
        width = shutil.get_terminal_size().columns
        resized = False
        event = ""
        try:
            for line in _daemon.lines(
                _wire.PANE_STREAM_PATH % (session_id, kind, width),
                host,
                port,
                STREAM_STALL_SECONDS,
            ):
                if line.startswith("event: "):
                    event = line[len("event: "):]
                elif line.startswith("data: "):
                    data = line[len("data: "):]
                    if event == "frame":
                        yield json.loads(data)["ansi"]
                    elif event == "session":
                        session_id = json.loads(data)["session_id"]
                    elif event == "error":
                        break
                if shutil.get_terminal_size().columns != width:
                    resized = True                          # a resize is a reconnect
                    break
        except (OSError, ValueError):
            pass
        if not resized:
            time.sleep(RECONNECT_DELAY_SECONDS)


def main(arguments: list[str]) -> None:
    if len(arguments) != 4:
        raise SystemExit("usage: terminal_pane.py HOST PORT SESSION_ID KIND")
    host, port, session_id, kind = arguments
    for ansi in frames(host, int(port), session_id, kind):
        sys.stdout.write(ansi)
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
