#!/usr/bin/env python3
"""Capture Claude Code's rate-limit windows, then run the real status line.

Claude Code exposes per-account rate limits (`rate_limits.<window>.
{used_percentage,resets_at}`) to ONE place: the status-line command's stdin JSON,
after each API response. Not in any hook payload, not in the transcript, not in
OTEL — so the only way to read it is to BE the status-line command. Claude Code
allows a single one and the user already runs a HUD, so this shim wraps it: read
the stdin once, ship it to the daemon, then hand the SAME bytes to the real
command (argv[1:]) and let its output and exit code be what Claude Code sees.

HARD rule: the shim must NEVER break the status line. The capture is silent on
every failure and the delegate runs regardless.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _http                                                     # noqa: E402

HARNESS = "claude_code"
# The status line must never be held up: a wedged daemon costs this pause per
# render, and no more.
DELIVERY_TIMEOUT_SECONDS = 1.0


def capture(raw: bytes) -> None:
    """Ship the stdin bytes to the daemon as a `statusline` telemetry delivery.

    Two things are stamped on the way past, both raw: the account this process's
    own environment selects, and the moment it was read. What the rate-limit
    windows MEAN is decided daemon-side
    (`harness/impl/claude_code/otel/gateway.py`).
    """
    try:
        document = json.loads(raw or b"{}")
        if not isinstance(document, dict) or not (document.get("session_id") or "").strip():
            return
        body = dict(
            document,
            _account_id=os.environ.get(_http.ACCOUNT_SLUG_VARIABLE, ""),
            _account_name=os.environ.get(_http.ACCOUNT_LABEL_VARIABLE, ""),
            _ts=document.get("_ts") or time.time(),
        )
        _daemon.post(
            _http.TELEMETRY_PATH % HARNESS,
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            {_http.TELEMETRY_KIND_HEADER: "statusline"},
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
    except Exception:
        pass                                    # never break the status line


def delegate(argv: list[str], stdin_bytes: bytes) -> int:
    """Run the real status-line command with the same stdin, inheriting stdout
    and stderr so its output is what Claude Code renders. 0 when there is no
    delegate — a bare shim install still succeeds."""
    if not argv:
        return 0
    try:
        return subprocess.run(argv, input=stdin_bytes, check=False).returncode
    except Exception:
        return 0                                # never break the status line


def main() -> None:
    raw = sys.stdin.buffer.read()
    capture(raw)
    sys.exit(delegate(sys.argv[1:], raw))


if __name__ == "__main__":
    main()
