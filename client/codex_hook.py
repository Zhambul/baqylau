#!/usr/bin/env python3
"""Ship one Codex hook delivery to the daemon.

~/.codex/hooks.json names THIS FILE, once per hook event, and the path is cached
for the session's lifetime — the same published-API rule as its Claude Code twin
(`claude_hook.py`).

Codex has no accounts and no launch-time selections to observe, so the delivery
carries only the window this process runs in and its own pid; there is no reply
channel either. Everything the delivery means is decided daemon-side in
`harness/impl/codex/hooks/gateway.py`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _wire                                                     # noqa: E402

HARNESS = "codex"


def main() -> None:
    payload = sys.stdin.buffer.read()
    reply = _daemon.post(_wire.HOOK_PATH % HARNESS, payload, {
        _wire.TERMINAL_WINDOW_HEADER: _wire.window_id(os.environ),
        _wire.CLIENT_PROCESS_HEADER: str(os.getpid()),
    })
    if reply:
        sys.stdout.buffer.write(reply)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                                    # never fail the harness
