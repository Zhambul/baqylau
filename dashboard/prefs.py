# dashboard/prefs.py — the GLOBAL, cross-session, cross-device web-dashboard
# preferences store (docs/dashboard.md, *New-session prefs*). A tiny durable
# kv table (key TEXT PRIMARY KEY, val JSON) at core.paths.DASH_PREFS_DB
# (~/.claude), the single owner of dashboard-wide UI state that isn't tied to
# any one session:
#
#   new-session        →  {cwd, model, effort}  (the launch form's last-used values)
#   new-session-draft  →  {text, seq}           (its UNSENT first-prompt draft)
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


def mutate_map(key, fn):
    """Atomically read-modify-write the DICT stored under `key`: load it (or a
    fresh {}), apply `fn(d)` in place, and persist — all inside ONE
    BEGIN IMMEDIATE transaction. The get()+set() pattern its callers used spans
    TWO short-lived connections, so two concurrent control-plane POSTs (each its
    own request thread + connection) could both read the old map and the second
    write clobber the first — one entry silently lost. BEGIN IMMEDIATE takes the
    write lock up front, so a racing mutate blocks (WAL + timeout=5.0) and reads
    the committed map. Returns the updated map (best-effort like set(): the
    intended map even if the write degraded — `fn` is called once either way)."""
    try:
        conn = _connect()
    except Exception:
        conn = None
    if conn is not None:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
            d = json.loads(row[0]) if row else {}
            if not isinstance(d, dict):
                d = {}
            fn(d)
            conn.execute("INSERT INTO kv(key, val) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                         (key, json.dumps(d, ensure_ascii=False)))
            conn.commit()
            return d
        except Exception:
            pass
        finally:
            conn.close()
    d = get(key, {})                       # degraded: reflect intent anyway
    d = d if isinstance(d, dict) else {}
    fn(d)
    return d


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
    time, which is what re-hides it. Atomic read-modify-write (mutate_map) so a
    second concurrent hide can't lose this stamp; best-effort like set()."""
    return mutate_map(HIDDEN_KEY, lambda d: d.__setitem__(str(key), float(ts)))


# --- the new-session form's unsent first prompt ---------------------------------
# The launch form's first-prompt box is a DRAFT like the composer's (docs/
# dashboard.md, *New-session draft*): closing the form — deliberately, with Esc,
# or by a stray click on the backdrop — must not throw the text away, and the
# next open restores it. Stored under one kv key as {text, seq}.
#
# Deliberately GLOBAL and NOT keyed by directory (unlike `composer-draft`, which
# is per-session): the form is ONE transient box, opened from the header or a
# group's "+", and its directory can be re-picked while the prompt stays — a
# per-cwd map would accumulate stale drafts nobody ever sees again, and would
# lose the text the moment you corrected the directory. One draft, restored on
# every open, cleared by the launch that consumes it.
#
# `seq` is the writer's wall clock, same STALE-WRITE GUARD as the composer draft
# (dashboard/http/post.py post_composer_draft): a debounced save in flight when
# the launch clears the box must not resurrect it by landing later. A clear is a
# TOMBSTONE (empty text at the newer seq), never a delete, so its seq survives to
# reject that straggler.
NS_DRAFT_KEY = "new-session-draft"


def ns_draft():
    """The unsent new-session first prompt as {text, seq} ({"text": "", "seq": 0}
    when never written / cleared / unreadable)."""
    d = get(NS_DRAFT_KEY, {})
    if not isinstance(d, dict):
        return {"text": "", "seq": 0}
    text = d.get("text")
    seq = d.get("seq")
    return {"text": text if isinstance(text, str) else "",
            "seq": seq if isinstance(seq, (int, float)) else 0}


def set_ns_draft(text, seq):
    """Persist the new-session draft `text` at `seq`, DROPPING a write older than
    what is stored (the stale-write guard above). Atomic read-modify-write
    (mutate_map — one BEGIN IMMEDIATE, so the compare and the set can't straddle
    a peer request thread's write). Returns the stored record, with `stale` True
    when this write was rejected; best-effort like set()."""
    keep = {}

    def _apply(d):
        cur = d.get("seq")
        cur = cur if isinstance(cur, (int, float)) else 0
        if seq < cur:
            keep["stale"] = True
            return
        d["text"] = text
        d["seq"] = seq
    rec = mutate_map(NS_DRAFT_KEY, _apply)
    return dict(rec, stale=bool(keep.get("stale")))


# --- notification mute (the session header's ◉/○ opt-out) ------------------------
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
    Atomic read-modify-write (mutate_map) so two concurrent mute toggles can't
    lose each other's change; best-effort like set()."""
    def _apply(d):
        if muted:
            d[str(sid)] = True
        else:
            d.pop(str(sid), None)
    return mutate_map(NOTIFY_MUTE_KEY, _apply)


# --- global alerts toggle (the list page's ◉/○, next to "+ session") -------------
# The ONE master switch over every dashboard notification — the cross-session
# toasts / OS notifications AND the deferred Telegram / web-push alerts
# (docs/dashboard.md, *Global alerts toggle*). Stored under one kv key as a bare
# bool. GLOBAL like the other dashboard prefs: it lives at DASH_PREFS_DB
# (~/.claude), independent of any repo checkout, so the one flag governs every
# session — live or parked, in the main checkout or any git worktree. DEFAULT ON:
# an absent key reads True, so a fresh install alerts until the user opts out.
# When OFF it OVERRIDES the per-session notify_muted map (everything suppressed);
# when ON the per-session mutes still apply.
NOTIFY_ENABLED_KEY = "notify-enabled"


def notify_enabled():
    """True unless the global alerts toggle was turned OFF (default ON)."""
    return get(NOTIFY_ENABLED_KEY, True) is not False


def set_notify_enabled(on):
    """Turn all dashboard alerts on/off globally; True on write (best-effort like
    set())."""
    return set(NOTIFY_ENABLED_KEY, bool(on))


# --- web-push subscriptions (the on-device iOS/desktop notification channel) ----
# Every browser that opted into Web Push (docs/dashboard.md, *Web push*) stores
# its push subscription here under one kv key, as {endpoint: subscription-json}
# — keyed by the endpoint URL so a re-subscribe from the same browser upserts in
# place instead of piling up duplicates. Global like the other dashboard prefs:
# a subscription is a per-DEVICE fact, not per-session, and the send honors the
# per-session ○ mute at fire time (same as the Telegram alert). A dead
# subscription (the push service returns 404/410) is pruned by remove_push_sub.
PUSH_SUBS_KEY = "push-subs"


def push_subscriptions():
    """The list of stored push subscriptions (subscription-JSON dicts); [] when
    none / unreadable."""
    d = get(PUSH_SUBS_KEY, {})
    return list(d.values()) if isinstance(d, dict) else []


def add_push_subscription(sub, device=None, label=None):
    """Upsert one subscription (its wire JSON: {endpoint, keys:{p256dh, auth}}),
    keyed by endpoint so a repeat subscribe from the same browser replaces its
    prior entry. `device` (the browser's stable id) + `label` (a friendly name)
    are stored ALONGSIDE the wire fields so the notifier can route the on-device
    push to the most-recently-used device (webpush.send ignores the extra keys).
    Returns the updated map; best-effort like set()."""
    ep = sub.get("endpoint") if isinstance(sub, dict) else None
    if not ep:
        return get(PUSH_SUBS_KEY, {})
    rec = dict(sub)
    if device:
        rec["device"] = str(device)
    if label:
        rec["label"] = str(label)
    return mutate_map(PUSH_SUBS_KEY, lambda d: d.__setitem__(str(ep), rec))


def remove_push_subscription(endpoint):
    """Drop the subscription for `endpoint` (an unsubscribe, or a prune after the
    push service reports it gone). Returns the updated map; best-effort."""
    return mutate_map(PUSH_SUBS_KEY, lambda d: d.pop(str(endpoint), None))


# --- web-rename override (the durable rename that can't "roll back") ------------
# The name the user set via the web rename button (docs/dashboard.md, *Web
# rename*), stored under one kv key as a {session_id: name} map, keyed by the
# TRANSCRIPT STEM sid (basename minus .jsonl — set_session_title's own sessionId,
# adopt/fork-proof). The rename's canonical channel is still the transcript's
# `agent-name` append, but that single record scrolls out of session_title's
# 64KB tail-window in a long session while Claude Code keeps re-emitting
# `ai-title` near EOF — so the auto title would win and the rename appear to
# revert. This durable override is the tail-window-proof stand-in: session_title
# prefers it ONLY when the transcript's tail no longer carries an `agent-name`
# (a fresh in-tail rename — terminal /rename or a re-rename — still wins, so
# "last rename wins" holds). Never deleted (a rename is sticky); a re-rename
# overwrites. Global like notify-muted — a dashboard preference, survives park.
RENAME_KEY = "renamed-title"


def renamed_title(sid):
    """The durable web-rename override for `sid` (the transcript stem), or ''
    when the session was never web-renamed / the store is unreadable."""
    d = get(RENAME_KEY, {})
    name = d.get(str(sid), "") if isinstance(d, dict) else ""
    return name if isinstance(name, str) else ""


def set_renamed_title(sid, name):
    """Persist the web-rename override `name` for `sid`; returns the updated map.
    Atomic read-modify-write (mutate_map) so two concurrent renames of DIFFERENT
    sessions can't lose each other's entry; best-effort like set()."""
    return mutate_map(RENAME_KEY, lambda d: d.__setitem__(str(sid), name))
