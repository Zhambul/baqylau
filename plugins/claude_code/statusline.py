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
import re
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


# Window-key hygiene for parse_usage: `rate_limits` is external input riding
# straight into a kv the dashboard renders — keys must look like window names
# (never `ts`, the snapshot's own stamp), and a garbage payload must not bloat
# the kv (MAX_WINDOWS caps it; the account-wide pair is always kept first).
_KEY_OK = re.compile(r"^[a-z0-9_]{1,40}$")
KNOWN_WINDOWS = ("five_hour", "seven_day")
MAX_WINDOWS = 8


def parse_usage(data):
    """The stdin JSON → the `usage` kv shape, or None when no rate-limit
    window parses (a fresh account before its first API response — leave the
    last good value in place rather than overwrite it with nulls). GENERIC
    over windows: every `rate_limits.<key>.{used_percentage, resets_at}`
    entry becomes `<key>`: pct + `<key>_reset`: epoch — the account-wide
    five_hour/seven_day pair always first, then any OTHER window sorted by
    key. As of CLI 2.1.215 only the account-wide pair exists (the /usage
    screen's per-model weekly bar has NO statusline counterpart — verified
    against live payloads 2026-07-19); when Claude Code starts reporting a
    model-scoped window (e.g. `seven_day_fable`), it flows through here, the
    kv, and the dashboard's per-window bars with no code change."""
    rl = (data or {}).get("rate_limits") or {}
    known = [k for k in KNOWN_WINDOWS if k in rl]
    extra = sorted(k for k in rl if isinstance(k, str) and k not in KNOWN_WINDOWS)
    out, nwin = {}, 0
    for key in known + extra:
        if nwin >= MAX_WINDOWS:
            break
        w = rl.get(key)
        if not _KEY_OK.match(key) or key == "ts" or not isinstance(w, dict):
            continue
        pct = _pct(w.get("used_percentage"))
        if pct is None:
            continue
        out[key], nwin = pct, nwin + 1
        reset = _epoch_s(w.get("resets_at"))
        if reset is not None:
            out[key + "_reset"] = reset
    return out or None


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
