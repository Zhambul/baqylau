# core/paths.py — the ONE owner of the mirror-log path format.
# (Historical top-level name: claude_paths.py — that compat shim is deleted.)
#
# Everything in this project is keyed by the mirror-log path
# /tmp/claude-mirror-<key>.log (the "<key>.log" is a KEY, not a file — see
# core/state.py). The key is the sanitized session_id, or a cwd slug when a
# payload lacks one. Before this module, the format was encoded independently in
# claude_ops.log_path, claude_audit.sid_from_log, claude-split.proj_slug and
# claude-tab-status's fallback — four regexes that MUST agree (the audit's
# mirror_log column joins on it; the fallback state-DB paths must line up).
# Stdlib-only leaf module: importable by core.audit/core.state without cycles.
import os
import re

# The repo root. Not the mirror-log format proper, but repo-root derivation is
# a path fact with exactly one correct answer, and this stdlib-only leaf is the
# project's designated path owner — before this, ten modules each re-derived it
# with a depth-sensitive triple-dirname (a moved file silently breaks its own
# copy). This file is one level below the root, so: two dirnames.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# bin/ — where every hyphenated ENTRY script (claude-hook.py, claude-stream.py,
# claude-mirror.py, …) lives. Their FILENAMES are load-bearing (the audit DB's
# handler/script vocabulary), so spawn sites must join THIS directory with the
# historical filename, never re-derive the location.
BIN = os.path.join(ROOT, "bin")

# CLAUDE_MIRROR_TMPDIR relocates EVERYTHING derived from these two roots (state
# DBs, .out/.done sidecars, .keep parks, the tab DB) — it exists solely so the
# test suite can run hermetically; nothing sets it in real sessions. Read at
# import: every hook is a fresh process, so a per-process env is a per-run root.
_TMP = os.environ.get("CLAUDE_MIRROR_TMPDIR") or "/tmp"

PREFIX = _TMP + "/claude-mirror-"

# The GLOBAL window-keyed tab DB (colour state + watcher pid locks). Owned by
# claude-tab-status.py (schema + writes); core.state.tab_state is the one
# sanctioned reader. Window-keyed, not session-keyed — a kitty window outlives
# any one session. In /tmp so it self-clears on reboot.
TAB_DB = _TMP + "/claude-kitty-tab.db"

# Durable park location for parked session state DBs (the mirror/scoreboard
# HISTORY). The LIVE state DB stays under _TMP — its existence is the
# session-alive signal watchers poll, and stale runtime state SHOULD self-clear
# on reboot — but the PARKED history must OUTLIVE a reboot so a --resume after a
# machine restart replays the prior session instead of starting from scratch.
# macOS wipes /tmp on reboot, which silently dropped the old in-place *.keep
# parks; parking here (durable ~/.claude) is the fix. Relocated under the
# hermetic tmpdir when the test seam is set, so the suite never touches real
# ~/.claude.
HISTORY_DIR = os.path.join(
    _TMP if os.environ.get("CLAUDE_MIRROR_TMPDIR")
    else os.path.expanduser("~/.claude"),
    "baqylau-mirror-history",
)

# The GLOBAL (per-machine, not per-session) OTLP-receiver singleton lock DB. The
# OTEL metrics receiver is one process per machine — the OTLP endpoint is a
# process-global env var, so a single receiver serves every session. Its pid-lock
# lives here (mirrors TAB_DB's convention); relocated by CLAUDE_MIRROR_TMPDIR so
# the test suite stays hermetic.
OTLP_DB = _TMP + "/claude-baqylau-otlp.db"

# The GLOBAL web-dashboard singleton lock DB (dashboard/server.py). One dashboard
# process per machine — it serves EVERY session (live and parked), so the lock is
# per-machine like OTLP_DB, and lives in /tmp for the same self-clear-on-reboot
# reason (the pid-lock is runtime state; the second guard is the port bind).
DASH_DB = _TMP + "/claude-baqylau-dash.db"

# The GLOBAL, cross-session web-dashboard PREFERENCES DB (dashboard/prefs.py).
# Unlike a per-session state DB, and unlike DASH_DB (a /tmp runtime lock), this
# is durable UI state shared across every browser/device pointing at this one
# dashboard: the new-session form's last-used directory/model/effort. Durable
# ~/.claude like HISTORY_DIR (same reboot-survival reason — a preference that
# vanished on reboot would be no better than the per-browser localStorage it
# replaced), relocated under the hermetic tmpdir when the test seam is set so
# the suite never touches real ~/.claude.
DASH_PREFS_DB = os.path.join(
    _TMP if os.environ.get("CLAUDE_MIRROR_TMPDIR")
    else os.path.expanduser("~/.claude"),
    "baqylau-dash-prefs.db",
)


# Durable staging area for web-dashboard composer ATTACHMENTS (images/files a
# browser uploads to the running session — dashboard/server.py post_upload). The
# bytes are written here and referenced by ABSOLUTE `@path` in the delivered
# message, so they must live OUTSIDE any repo working tree (keeps `git status`
# clean) yet survive long enough for Claude Code to read them. Durable ~/.claude
# like HISTORY_DIR (same reboot-survival + hermetic-tmpdir-relocation reasons);
# per-session subdirs keep parallel sessions' uploads from colliding.
UPLOADS_DIR = os.path.join(
    _TMP if os.environ.get("CLAUDE_MIRROR_TMPDIR")
    else os.path.expanduser("~/.claude"),
    "baqylau-uploads",
)


def sanitize_sid(sid):
    """A session id as it appears in the mirror-log key."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", sid)


def session_uploads_dir(sid):
    """The per-session composer-attachment staging dir (see UPLOADS_DIR). Returns
    the path; does NOT create it (the caller mkdirs, gated behind the dashboard
    control-plane guard). The sid is sanitized so a hostile key can't escape the
    root; a missing sid falls back to a shared "staging" bucket (the new-session
    form has no sid yet)."""
    return os.path.join(UPLOADS_DIR, sanitize_sid((sid or "").strip()) or "staging")


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


def log_for_key(key):
    """The mirror-log path for an ALREADY-FORMED key, verbatim — no sanitizing.
    Differs from mirror_log(sid), which sanitizes its input: callers that hold a
    key recovered from an existing path/URL (e.g. core/copy.py's hyperlink
    round-trip) must reproduce the exact original path, not a re-derived one —
    re-sanitizing a key that is already a key must never change it, and going
    through mirror_log would hide that assumption."""
    return PREFIX + key + ".log"


def state_db(log):
    """The per-session runtime state DB for a mirror log (see core/state.py)."""
    return log + ".state.db"


def parked_db(log):
    """The DURABLE park path for a session's state DB (see core/hostpane.park_db).
    Base path only — callers append '', '-wal', '-shm' as for state_db(). Lives
    under HISTORY_DIR (~/.claude), NOT next to the live DB, so a machine reboot
    that wipes /tmp cannot drop the parked mirror/scoreboard history."""
    return os.path.join(HISTORY_DIR, sid_from_log(log) + ".state.db")
