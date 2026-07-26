# dashboard/prefs.py — the GLOBAL, cross-session, cross-device web-dashboard
# preferences store (docs/dashboard.md, *New-session prefs*). A tiny durable
# kv table (key TEXT PRIMARY KEY, val JSON) at core.paths.DASH_PREFS_DB
# (~/.claude), the single owner of dashboard-wide UI state that isn't tied to
# any one session:
#
#   new-session        →  {cwd, model, effort}  (the launch form's last-used values)
#   new-session-draft  →  {cwd: {text, seq}}    (its UNSENT first-prompt drafts,
#                                                one per directory)
#   view-mode          →  {sid: mode}           (each session's mirror density —
#                                                verbose | default | focus)
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
#
# But degrading SILENTLY is what this module used to do, and that broke the
# audit-before-swallow invariant at every one of its five swallow sites: a
# locked/corrupt/unwritable prefs DB lost the toggle, the draft or the rename
# with NO row anywhere, and mutate_map's callers report their gesture as
# SUCCEEDED (see its own note) — so "my global alerts switch didn't stick" /
# "my launch draft vanished" was undebuggable from the DB, the exact blind spot
# the audit exists to close. Every swallow now reports through _audit_fail
# first.
import json
import os
import sqlite3

from core import paths as P
from core.noaudit import load_audit

A = load_audit()   # always-on audit trail; inert stub if it can't import

# The (op, key) pairs whose READ failure has already been reported this process.
# Writes are user gestures (bounded, each worth a row), but reads run on nearly
# every request and SSE tick — a permanently broken DB would append an `errors`
# row per tick forever, and since these are session_id='' rows, errwatch
# surfaces each as a `⚠ global:` in EVERY session's scorebar. One row per
# operation per key per process is enough to name the fault; the same reasoning
# as errwatch's own audit-at-most-once recursion guard. Keyed by the PAIR, not
# the key alone, so a swallowed read doesn't then mask a different failure
# (a connect) against the same key.
#
# CAREFUL: this module's public `set(key, obj)` SHADOWS the builtin for
# everything below it, so this line must stay ABOVE that def — moved under it,
# `set()` would resolve to prefs.set and raise at IMPORT time, taking the whole
# dashboard down. (The shadowing itself stays: `prefs.set`/`prefs.get` is the
# read-well kv API from every call site outside.)
_READ_FAILED = set()


def _audit_fail(op, key, once=False):
    """Report the currently-handled exception from one of this module's swallow
    sites to the audit `errors` table (never raising — A.error degrades to its
    spool file). `op` names the operation (`get`/`set`/`mutate`/`connect`), `key`
    the kv key at stake. `once=True` is the read-path flood guard above."""
    if once:
        if (op, key) in _READ_FAILED:
            return
        _READ_FAILED.add((op, key))
    A.error("", "dashboard prefs %s" % op,
            {"key": key, "db": P.DASH_PREFS_DB})


def _connect():
    """A fresh rwc connection to the durable prefs DB, schema ensured. WAL so a
    read never blocks a concurrent write from another request thread."""
    path = P.DASH_PREFS_DB
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT)")
    return conn


def _upsert(conn, key, obj):
    """Write `obj` (JSON) under `key` on an OPEN connection — the one spelling
    of this store's upsert, shared by set() and mutate_map(). Deliberately NOT
    shared with core/state.py's identical statement: that is a DIFFERENT
    database with a different lifecycle (per-session, never created by a read),
    and the two kv tables match by convention, not by contract — a column added
    to one must not silently propagate to the other. `ensure_ascii=False`
    matches state's for the same reason it matters there: the values carry
    non-ASCII prose and glyphs, and an escaped copy would not compare equal."""
    conn.execute("INSERT INTO kv(key, val) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
                 (key, json.dumps(obj, ensure_ascii=False)))


def get(key, default=None):
    """The stored value for `key` (JSON-decoded), or `default` when absent /
    unreadable."""
    try:
        conn = _connect()
    except Exception:
        _audit_fail("connect", key, once=True)
        return default
    try:
        row = conn.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    except Exception:
        _audit_fail("get", key, once=True)
        return default
    finally:
        conn.close()


def set(key, obj):
    """Upsert `obj` (JSON-encoded) under `key`. True on write, else False — and a
    False is audited (a lost durable write is never silent)."""
    try:
        conn = _connect()
    except Exception:
        _audit_fail("connect", key)
        return False
    try:
        _upsert(conn, key, obj)
        conn.commit()
        return True
    except Exception:
        _audit_fail("set", key)
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
    intended map even if the write degraded — `fn` is called once either way).

    That optimistic return is deliberate — the page keeps the draft/toggle it
    just made and a 500 would only throw it away — but it means the CALLER can't
    tell a persisted write from a lost one, and answers `ok` either way. So the
    audited failure below is the ONLY trace a degraded write leaves: a gesture
    whose `web-*` state_files row says ok:True next to a `dashboard prefs mutate`
    error row at the same instant IS the "it didn't stick" signature."""
    try:
        conn = _connect()
    except Exception:
        _audit_fail("connect", key)
        conn = None
    if conn is not None:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
            d = json.loads(row[0]) if row else {}
            if not isinstance(d, dict):
                d = {}
            fn(d)
            _upsert(conn, key, d)
            conn.commit()
            return d
        except Exception:
            _audit_fail("mutate", key)
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


# --- the new-session form's unsent first prompts (one per directory) -------------
# The launch form's first-prompt box is a DRAFT like the composer's (docs/
# dashboard.md, *New-session draft*): closing the form — deliberately, with Esc,
# or by a stray click on the backdrop — must not throw the text away, and the
# next open restores it. Stored under one kv key as a PER-DIRECTORY map,
# {cwd: {text, seq}} — different projects hold different half-typed prompts, and
# switching the form's directory switches which one is in the box (the single
# shared draft this started as bled one project's prompt into the next).
#
# The cwd KEY is whatever the page sends (`app.09-newsession.js` nsDirKey — the
# form's own notion of "the same folder": trimmed, trailing slashes dropped),
# stored verbatim here; the server is a dumb kv for it deliberately, so the
# normalization has ONE implementation instead of two that can disagree. "" is a
# legitimate key (the form opened with no directory yet).
#
# `seq` is the writer's wall clock, same STALE-WRITE GUARD as the composer draft
# (dashboard/http/post.py post_composer_draft), applied PER ENTRY: a debounced
# save in flight when the launch clears the box must not resurrect it by landing
# later. A clear is a TOMBSTONE (empty text at the newer seq), never a delete, so
# its seq survives to reject that straggler.
#
# The map is PRUNED to the NS_DRAFT_MAX most recent entries by seq (tombstones
# included — recency, not emptiness, is what decides): the form is opened against
# a handful of projects in practice, and an unbounded map would accumulate a row
# per directory ever typed into, forever.
NS_DRAFT_KEY = "new-session-draft"
NS_DRAFT_MAX = 24


def _ns_entry(d):
    """One stored draft normalized to {text, seq} — the shape every reader gets,
    whatever junk the value holds."""
    if not isinstance(d, dict):
        return {"text": "", "seq": 0}
    text = d.get("text")
    seq = d.get("seq")
    return {"text": text if isinstance(text, str) else "",
            "seq": seq if isinstance(seq, (int, float)) else 0}


def ns_drafts():
    """Every unsent new-session prompt as {cwd: {text, seq}} ({} when none /
    unreadable). The page caches this whole map so opening the form seeds the
    box synchronously — it is bounded by NS_DRAFT_MAX."""
    d = get(NS_DRAFT_KEY, {})
    if not isinstance(d, dict):
        return {}
    if isinstance(d.get("text"), str):
        return {}          # the pre-per-directory single-draft shape: drop it
    #                        (one stale prompt, not worth a migration path)
    return {str(k): _ns_entry(v) for k, v in d.items()}


def set_ns_draft(cwd, text, seq):
    """Persist `text` at `seq` as the draft for directory `cwd`, DROPPING a write
    older than that directory's stored seq (the stale-write guard above — per
    entry, so two directories' saves never fight) and pruning the map back to
    NS_DRAFT_MAX. Atomic read-modify-write (mutate_map — one BEGIN IMMEDIATE, so
    the compare, the set and the prune can't straddle a peer request thread's
    write). Returns the stored entry, with `stale` True when this write was
    rejected; best-effort like set()."""
    key = str(cwd)
    keep = {}

    def _apply(d):
        if isinstance(d.get("text"), str):
            d.clear()                  # ditch the pre-per-directory shape (see
            #                             ns_drafts) before it becomes junk keys
        cur = _ns_entry(d.get(key))
        if seq < cur["seq"]:
            keep["stale"] = True
            return
        d[key] = {"text": text, "seq": seq}
        if len(d) > NS_DRAFT_MAX:
            # oldest-first by seq, keeping this write (it has the newest clock)
            for k in sorted(d, key=lambda k: _ns_entry(d[k])["seq"]
                            )[:len(d) - NS_DRAFT_MAX]:
                d.pop(k, None)
    rec = mutate_map(NS_DRAFT_KEY, _apply)
    return dict(_ns_entry(rec.get(key)), stale=bool(keep.get("stale")))


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


# --- the mirror's VIEW MODE, per session (the filter bar's 3-way control) --------
# Which of Claude Code's three transcript densities the session's web mirror is
# rendered at (docs/dashboard.md, *View modes*), stored under one kv key as a
# {session_id: mode} map.
#
# DELIBERATELY per-session and NOT a global default: switching one session to
# focus must not silently re-render every other one (a mode is a per-session
# reading choice, unlike the alerts switch, which is one machine-wide policy).
#
# A session nobody switched opens at DEFAULT — the same mode Claude Code's own
# `viewMode` defaults to, so the dashboard reads like the TUI it mirrors. It
# shipped defaulting to `verbose` (the mode that hides nothing) while the collapse
# was new and unproven; nothing is actually lost at `default` — every collapsed
# run is one click from expanded, and mutations, messages and the ⚠ warning line
# never fold at all — so the cautious default outlived its reason.
#
# This ONLY ever changes what the browser paints. Claude Code has its own
# `viewMode` setting for the TUI and this store is not it: nothing here is
# written into any settings.json, and the terminal mirror keeps painting
# everything at every mode.
#
# Global like the other dashboard prefs (it survives park, so a parked session
# re-opens at the mode you last read it in), and a mode set back to the default
# DELETES the key so the map stays the small set of overridden sessions.
VIEW_MODE_KEY = "view-mode"
# The mode vocabulary, in CONTROL order — the segmented control reads
# verbose → default → focus, densest to sparsest, which is why the default is not
# simply the first entry (the two are deliberately decoupled).
VIEW_MODES = ("verbose", "default", "focus")
VIEW_DEFAULT = "default"


def view_mode(sid):
    """The stored view mode for `sid`, or VIEW_DEFAULT when unset / unreadable /
    junk (an unknown stored value can never make the page hide content)."""
    d = get(VIEW_MODE_KEY, {})
    mode = d.get(str(sid)) if isinstance(d, dict) else None
    return mode if mode in VIEW_MODES else VIEW_DEFAULT


def set_view_mode(sid, mode):
    """Persist `mode` as the view mode for `sid`; returns the updated map.
    Atomic read-modify-write (mutate_map) so two sessions' concurrent switches
    can't lose each other's entry; best-effort like set(). The default mode is
    stored as an ABSENCE (see the note above)."""
    def _apply(d):
        if mode == VIEW_DEFAULT:
            d.pop(str(sid), None)
        else:
            d[str(sid)] = mode
    return mutate_map(VIEW_MODE_KEY, _apply)


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
