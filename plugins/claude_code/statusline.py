# plugins/claude_code/statusline.py — the status-line shim's capture half.
#
# Claude Code exposes per-session rate-limit data (5-hour + 7-day windows) to
# ONE place only: the status-line command's stdin JSON, after each API response
# (`rate_limits.{five_hour,seven_day}.{used_percentage,resets_at}`). It is NOT
# in any hook payload, the transcript, or OTEL — verified — so the only way to
# read it is to BE the status-line command. Since Claude Code allows a single
# status-line command and the user already runs one (a HUD), the shim wraps it:
# read the stdin once, stash the rate limits + account into this session's state
# DB, then hand the SAME stdin to the real status-line command and forward its
# output verbatim (bin/claude-statusline.py). The capture is tokenless — the
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

from core import paths as P
from core import state as S
from core.noaudit import load_audit
from plugins.claude_code import account as ACC

A = load_audit()


def _epoch_s(v):
    """A rate-limit `resets_at` → epoch SECONDS (float), or None. Claude Code
    has sent this as either seconds or milliseconds across versions; >1e12 is
    unambiguously milliseconds (a seconds value that large is year ~33000)."""
    if not isinstance(v, (int, float)) or v <= 0:
        return None
    return v / 1000.0 if v > 1e12 else float(v)


def _pct(v):
    """A used_percentage → an int 0..100, or None (absent/garbage)."""
    if not isinstance(v, (int, float)):
        return None
    return max(0, min(100, int(round(v))))


def parse_usage(data):
    """The stdin JSON → the `usage` kv shape, or None when no rate-limit block
    is present (a fresh account before its first API response — leave the last
    good value in place rather than overwrite it with nulls)."""
    rl = (data or {}).get("rate_limits") or {}
    fh, sd = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    fh_pct, sd_pct = _pct(fh.get("used_percentage")), _pct(sd.get("used_percentage"))
    if fh_pct is None and sd_pct is None:
        return None
    return {"five_hour": fh_pct, "five_hour_reset": _epoch_s(fh.get("resets_at")),
            "seven_day": sd_pct, "seven_day_reset": _epoch_s(sd.get("resets_at"))}


def capture(raw):
    """Best-effort: parse the raw stdin bytes and stash account + usage into
    this session's state DB. Writes ONLY when the DB already exists (the mirror
    created it at SessionStart) — never creates it, so a probe can't fake the
    session-alive signal a parked/headless session relies on. Silent on every
    failure; the caller runs the delegate regardless."""
    try:
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            return
        sid = (data.get("session_id") or "").strip()
        if not sid:
            return
        log = P.mirror_log(sid)
        if not os.path.isfile(P.state_db(log)):
            return                              # no live DB → nothing to attach to
        acc = ACC.current()
        S.kv_set(log, "account", acc)
        usage = parse_usage(data)
        if usage is not None:
            usage["ts"] = data.get("_ts") or _now()
            S.kv_set(log, "usage", usage)
    except Exception:
        try:
            A.error("", "statusline capture")
        except Exception:
            pass


def _now():
    import time
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
    """The bin/claude-statusline.py entry: read stdin, capture, delegate.
    argv[1:] is the real status-line command (the user's HUD invocation)."""
    raw = sys.stdin.buffer.read()
    capture(raw)
    sys.exit(run(sys.argv[1:], raw))
