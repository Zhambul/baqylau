#!/usr/bin/env python3
"""Ship one Claude Code hook delivery to the daemon and print the reply.

~/.claude/settings.json names THIS FILE, once per hook event, and a harness
captures that path at session start and caches it for the process lifetime — so
the name is a published API. Add a new path first, repoint the config, and
remove the old file only once those sessions have ended.

It reads its stdin, stamps the flat values only this process can observe, POSTs
the exact bytes and writes back whatever comes. It parses nothing, decides
nothing and records nothing: `harness/impl/claude_code/hooks/gateway.py` says
what the delivery meant. A daemon that does not answer means this hook did
nothing, which is why every failure here is silence and exit 0 — a hook must
never fail its harness.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _http                                                     # noqa: E402

HARNESS = "claude_code"


def main() -> None:
    payload = sys.stdin.buffer.read()
    # The daemon's own read-only probe of this harness: its hooks are not a
    # raw event of anybody's session, so they are read and dropped.
    if os.environ.get(_http.PROBE_VARIABLE):
        return
    reply = _daemon.post(_http.HOOK_PATH % HARNESS, payload, {
        _http.TERMINAL_WINDOW_HEADER: _http.window_id(os.environ),
        # Our OWN pid, not the CLI's: the daemon walks up from here while this
        # process is still blocked on the response, so the ancestry it reads is
        # provably alive — and this client stays free of a `ps` fork.
        _http.CLIENT_PROCESS_HEADER: str(os.getpid()),
        _http.LAUNCH_MODEL_HEADER: os.environ.get(_http.LAUNCH_MODEL_VARIABLE, ""),
        _http.LAUNCH_EFFORT_HEADER: os.environ.get(_http.LAUNCH_EFFORT_VARIABLE, ""),
    })
    if reply:
        sys.stdout.buffer.write(reply)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                                    # never fail the harness
