# core/paths.py — the ONE owner of the mirror-log path format.
# (Importable as `claude_paths` via the top-level compat shim.)
#
# Everything in this project is keyed by the mirror-log path
# /tmp/claude-mirror-<key>.log (the "<key>.log" is a KEY, not a file — see
# claude_state.py). The key is the sanitized session_id, or a cwd slug when a
# payload lacks one. Before this module, the format was encoded independently in
# claude_ops.log_path, claude_audit.sid_from_log, claude-split.proj_slug and
# claude-tab-status's fallback — four regexes that MUST agree (the audit's
# mirror_log column joins on it; the fallback state-DB paths must line up).
# Stdlib-only leaf module: importable by claude_audit/claude_state without cycles.
import os
import re

# CLAUDE_MIRROR_TMPDIR relocates EVERYTHING derived from these two roots (state
# DBs, .out/.done sidecars, .keep parks, the tab DB) — it exists solely so the
# test suite can run hermetically; nothing sets it in real sessions. Read at
# import: every hook is a fresh process, so a per-process env is a per-run root.
_TMP = os.environ.get("CLAUDE_MIRROR_TMPDIR") or "/tmp"

PREFIX = _TMP + "/claude-mirror-"

# The GLOBAL window-keyed tab DB (colour state + watcher pid locks). Owned by
# claude-tab-status.py (schema + writes); claude_state.tab_state is the one
# sanctioned reader. Window-keyed, not session-keyed — a kitty window outlives
# any one session. In /tmp so it self-clears on reboot.
TAB_DB = _TMP + "/claude-kitty-tab.db"

# The GLOBAL (per-machine, not per-session) OTLP-receiver singleton lock DB. The
# OTEL metrics receiver is one process per machine — the OTLP endpoint is a
# process-global env var, so a single receiver serves every session. Its pid-lock
# lives here (mirrors TAB_DB's convention); relocated by CLAUDE_MIRROR_TMPDIR so
# the test suite stays hermetic.
OTLP_DB = _TMP + "/claude-kitty-otlp.db"


def sanitize_sid(sid):
    """A session id as it appears in the mirror-log key."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", sid)


def cwd_slug(cwd=None):
    """Fallback key when no session_id is available: the (real)path as a slug.
    Per-PROJECT, not per-session — two sessions in one directory share it (the
    known bg-detection cross-talk caveat in CLAUDE.md)."""
    try:
        return re.sub(r"[/.]", "-", os.path.realpath(cwd or os.getcwd()))
    except OSError:
        return ""


def mirror_log(sid=None, cwd=None):
    """The mirror-log path for a session id (or the cwd-slug fallback)."""
    sid = (sid or "").strip()
    key = sanitize_sid(sid) if sid else cwd_slug(cwd)
    return PREFIX + key + ".log"


def sid_from_log(log):
    """Recover the key from a mirror-log path (or any derived path — the
    non-greedy match handles suffixed forms like <log>.state.db). Returns the
    key verbatim (the cwd-slug fallback included), or the input unchanged."""
    m = re.match(r".*/claude-mirror-(.+?)\.log", log or "")
    return m.group(1) if m else (log or "")


def state_db(log):
    """The per-session runtime state DB for a mirror log (see claude_state.py)."""
    return log + ".state.db"
