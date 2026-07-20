# dashboard/prefs.py — the GLOBAL, cross-session, cross-device web-dashboard
# preferences store (docs/dashboard.md, *New-session prefs*). A tiny durable
# kv table (key TEXT PRIMARY KEY, val JSON) at core.paths.DASH_PREFS_DB
# (~/.claude), the single owner of dashboard-wide UI state that isn't tied to
# any one session:
#
#   new-session  →  {cwd, model, effort}   (the launch form's last-used values)
#
# This is DELIBERATELY unlike the per-session kv helpers in core/state.py:
#   - it is GLOBAL (one row set per machine), not keyed by session_id;
#   - it CREATES its DB on demand (mode=rwc) — a per-session state DB must never
#     be created by a reader because its existence is the session-alive signal,
#     but a global prefs DB has no such meaning, so a first-ever write just makes
#     it.
# Every call opens a fresh short-lived connection: the dashboard is a
# ThreadingHTTPServer, and sqlite connections are single-thread-bound. Nothing
# here raises — a broken prefs DB degrades to "no remembered preference", never
# into a request handler.
import json
import os
import sqlite3

from core import paths as P


def _connect():
    """A fresh rwc connection to the durable prefs DB, schema ensured. WAL so a
    read never blocks a concurrent write from another request thread."""
    path = P.DASH_PREFS_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT)")
    return conn


def get(key, default=None):
    """The stored value for `key` (JSON-decoded), or `default` when absent /
    unreadable."""
    try:
        conn = _connect()
    except Exception:
        return default
    try:
        row = conn.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        return default
    finally:
        conn.close()


def set(key, obj):
    """Upsert `obj` (JSON-encoded) under `key`. True on write, else False."""
    try:
        conn = _connect()
    except Exception:
        return False
    try:
        conn.execute("INSERT INTO kv(key, val) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                     (key, json.dumps(obj, ensure_ascii=False)))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# --- hidden directories (the list page's ✕) ------------------------------------
# The set of project directories the user hid from the crowded list page
# (docs/dashboard.md, *Hidden directories*), stored under one kv key as
# {group_key: hidden_at_epoch}. Non-destructive: nothing is closed, the group
# just disappears from view. It re-appears the moment a session STARTED after
# hidden_at shows up in it — but that comparison is CLIENT-side (app.js
# dirHidden, over each wire row's started_at); this store only holds the stamp.
HIDDEN_KEY = "hidden-dirs"


def hidden_dirs():
    """The {group_key: hidden_at_epoch} map ({} when unset / unreadable)."""
    d = get(HIDDEN_KEY, {})
    return d if isinstance(d, dict) else {}


def hide_dir(key, ts):
    """Stamp `key` hidden at epoch `ts` and persist; returns the updated map.
    A re-hide (a re-appeared group hidden again) just overwrites with the newer
    time, which is what re-hides it. Best-effort like set() — the returned map
    reflects the intended state even if the write degraded."""
    d = hidden_dirs()
    d[str(key)] = float(ts)
    set(HIDDEN_KEY, d)
    return d


# --- notification mute (the session header's 🔕 opt-out) ------------------------
# The set of sessions the user opted OUT of the deferred Telegram alert
# (docs/dashboard.md, *Telegram alerts*), stored under one kv key as a
# {session_id: True} map. Global like hidden-dirs — the mute is a dashboard
# preference, not session state, so it survives park and applies live or parked.
# An un-mute DELETES the key so the map stays the small set of muted sids, never
# a row per session ever seen.
NOTIFY_MUTE_KEY = "notify-muted"


def notify_muted(sid):
    """True when `sid` is opted out of the deferred Telegram alert."""
    d = get(NOTIFY_MUTE_KEY, {})
    return bool(isinstance(d, dict) and d.get(str(sid)))


def set_notify_muted(sid, muted):
    """Mute (or un-mute) the Telegram alert for `sid`; returns the updated map.
    Best-effort like set() — the returned map reflects the intended state even
    if the write degraded."""
    d = get(NOTIFY_MUTE_KEY, {})
    if not isinstance(d, dict):
        d = {}
    if muted:
        d[str(sid)] = True
    else:
        d.pop(str(sid), None)
    set(NOTIFY_MUTE_KEY, d)
    return d
