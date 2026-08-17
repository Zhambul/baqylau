# harness/impl/claude_code/hooks/statusline.py — the status-line shim's capture half.
#
# Claude Code exposes per-session rate-limit data (5-hour + 7-day windows) to
# ONE place only: the status-line command's stdin JSON, after each API response
# (`rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}`). It is NOT
# in any hook payload, the transcript, or OTEL — verified — so the only way to
# read it is to BE the status-line command. Since Claude Code allows a single
# status-line command and the user already runs one (a HUD), the shim wraps it:
# read the stdin once, stash the rate limits + account into this session's state
# DB, then hand the SAME stdin to the real status-line command and forward its
# output verbatim (harness/impl/claude_code/hooks/statusline.py). The capture is tokenless — the
# number is per-account for free, no user:profile scope, no API call (this is
# exactly how the account switcher's own usage cache is populated).
#
# HARD rule: the shim must NEVER break the status line. Every capture failure is
# swallowed (audited first) and the delegate still runs; the delegate's stdout
# and exit code are what Claude Code sees.

import json
import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.daemon import client as daemon_client
from diagnostics import record as A
from harness.models import TELEMETRY_KIND_HEADER
from harness.impl.claude_code import account as ACC
import time

DELIVERY_PATH = "/api/harnesses/claude_code/telemetry"
# The shim must NEVER hold up the status line: a wedged daemon costs this pause
# per render, and no more.
DELIVERY_TIMEOUT_SECONDS = 1.0



def capture(raw):
    """Best-effort: ship the stdin bytes to the daemon as a telemetry delivery.

    The shim is a THIN CLIENT, like a hook process: it stamps the two facts only
    it can observe — the account its own environment selects, and the moment it
    read them — and the daemon decides what the rate-limit windows mean
    (`harness/impl/claude_code/otel/gateway.py`). It used to open a database and
    write the snapshot itself, which made the status line a store writer.

    Silent on every failure; the caller runs the delegate regardless."""
    try:
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            return
        if not (data.get("session_id") or "").strip():
            return
        acc = ACC.current(os.environ)
        body = dict(
            data,
            _account_id=acc.get("slug") or "",
            _account_name=acc.get("label") or acc.get("slug") or "default",
            _ts=data.get("_ts") or _now(),
        )
        daemon_client.post_bytes(
            DELIVERY_PATH,
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json", TELEMETRY_KIND_HEADER: "statusline"},
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
    except Exception:
        try:
            A.error("", "statusline capture")
        except Exception:
            pass


def _now() -> float:
    return time.time()


def run(argv, stdin_bytes):
    """Run the delegate status-line command (`argv`) with `stdin_bytes` on its
    stdin, inheriting stdout/stderr so its output is what Claude Code renders.
    Returns the delegate's exit code (0 when there is no delegate — a bare
    shim install still succeeds). Capture happens first but is independent:
    even a capture crash (swallowed) cannot stop the delegate."""
    if not argv:
        return 0
    try:
        return subprocess.run(argv, input=stdin_bytes).returncode
    except Exception:
        try:
            A.error("", "statusline delegate", {"argv0": argv[0]})
        except Exception:
            pass
        return 0                                # never fail the status line


def main():
    """Read stdin, capture usage, then invoke the configured status line.
    argv[1:] is the real status-line command (the user's HUD invocation)."""
    raw = sys.stdin.buffer.read()
    capture(raw)
    sys.exit(run(sys.argv[1:], raw))


if __name__ == "__main__":
    main()
