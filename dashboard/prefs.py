# dashboard/prefs.py — the GLOBAL, cross-session, cross-device web-dashboard
# preferences store (docs/dashboard.md, *New-session prefs*). A tiny durable
# kv table (key TEXT PRIMARY KEY, val JSON) at core.paths.DASH_PREFS_DB
# (~/.harness), the single owner of dashboard-wide UI state that isn't tied to
# any one session:
#
#   new-session        →  {working_directory, harness, model, effort}
#   new-session-draft  →  {working_directory: {text, sequence}}
#                                                one per directory)
#   view-mode          →  {session_id: mode}           (each session's mirror density —
#                                                verbose | default | focus)
#   tasks-hidden       →  {session_id: {ids, ts}}      (each session's DISMISSED tasks
#                                                card — purely visual)
#
# This is DELIBERATELY unlike the per-session kv helpers in core/state.py:
#   - it is GLOBAL (one row set per machine), not keyed by session_id;
#   - it CREATES its DB on demand (mode=rwc) — a per-session state DB must never
#     be created by a reader because its existence is the session-alive signal,
#     but a global prefs DB has no such meaning, so a first-ever write just makes
#     it.
# Every call opens a fresh short-lived connection because the dashboard is a
# ThreadingHTTPServer and sqlite connections are thread-bound. The store has one
# current schema and fails clearly on connection, decoding, and write errors.
import json
import os
import sqlite3
import time

from dashboard import paths


def _connect():
    """A fresh writable connection to the durable preferences database. WAL keeps
    reads from blocking concurrent writes from other request threads."""
    path = paths.DASHBOARD_PREFERENCES_DATABASE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, val TEXT)")
    return connection


def _upsert(connection, key, value):
    """Write one JSON value through the store's single upsert statement."""
    connection.execute(
        "INSERT INTO kv(key, val) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET val = excluded.val",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def get(key, default=None):
    """Return the decoded current-schema value, or `default` when absent."""
    connection = _connect()
    try:
        row = connection.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
    finally:
        connection.close()


def set(key, value):
    """Upsert `value` and return only after the transaction commits."""
    connection = _connect()
    try:
        _upsert(connection, key, value)
        connection.commit()
        return True
    finally:
        connection.close()


def mutate_map(key, mutator):
    """Atomically read-modify-write the DICT stored under `key`: load it (or a
    fresh object), apply `mutator` in place, and persist inside one
    BEGIN IMMEDIATE transaction. The get()+set() pattern its callers used spans
    TWO short-lived connections, so two concurrent control-plane POSTs (each its
    own request thread + connection) could both read the old map and the second
    write clobber the first. BEGIN IMMEDIATE takes the write lock before the
    read, so a racing mutation observes the committed map. Returns the committed
    map and raises if any part of the transaction fails."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
        document = json.loads(row[0]) if row else {}
        if not isinstance(document, dict):
            raise TypeError(f"preference {key!r} must contain an object")
        mutator(document)
        _upsert(connection, key, document)
        connection.commit()
        return document
    finally:
        connection.close()


def _stored_object(key):
    document = get(key, {})
    if not isinstance(document, dict):
        raise TypeError(f"preference {key!r} must contain an object")
    return document


# --- hidden directories (the list page's ✕) ------------------------------------
# The set of project directories the user hid from the crowded list page
# (docs/dashboard.md, *Hidden directories*), stored under one kv key as
# {group_key: hidden_at_epoch}. Non-destructive: nothing is closed, the group
# just disappears from view. It re-appears the moment a session STARTED after
# hidden_at shows up in it — but that comparison is CLIENT-side (app.js
# dirHidden, over each wire row's started_at); this store only holds the stamp.
HIDDEN_KEY = "hidden-dirs"


def hidden_dirs():
    """The {group_key: hidden_at_epoch} map."""
    return _stored_object(HIDDEN_KEY)


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
# {cwd: {text, sequence}} — different projects hold different half-typed prompts, and
# switching the form's directory switches which one is in the box (the single
# shared draft this started as bled one project's prompt into the next).
#
# The cwd KEY is whatever the page sends (`app.09-newsession.js` nsDirKey — the
# form's own notion of "the same folder": trimmed, trailing slashes dropped),
# stored verbatim here; the server is a dumb kv for it deliberately, so the
# normalization has ONE implementation instead of two that can disagree. "" is a
# legitimate key (the form opened with no directory yet).
#
# `sequence` is the writer's wall clock, same STALE-WRITE GUARD as the composer draft
# (dashboard/http/post/state.py post_composer_draft), applied PER ENTRY: a debounced
# save in flight when the launch clears the box must not resurrect it by landing
# later. A clear is a TOMBSTONE (empty text at the newer sequence), never a delete, so
# its sequence survives to reject that straggler.
#
# The map is PRUNED to the NS_DRAFT_MAX most recent entries by sequence (tombstones
# included — recency, not emptiness, is what decides): the form is opened against
# a handful of projects in practice, and an unbounded map would accumulate a row
# per directory ever typed into, forever.
NEW_SESSION_DRAFT_KEY = "new-session-draft"
NEW_SESSION_DRAFT_LIMIT = 24


def _new_session_draft(document):
    """Validate and return one stored {text, sequence} draft."""
    if not isinstance(document, dict):
        raise TypeError("new-session draft must contain an object")
    text = document.get("text")
    sequence = document.get("sequence")
    if not isinstance(text, str):
        raise TypeError("new-session draft text must be a string")
    if not isinstance(sequence, (int, float)) or isinstance(sequence, bool):
        raise TypeError("new-session draft sequence must be a number")
    return {"text": text, "sequence": sequence}


def new_session_drafts():
    """Every unsent new-session prompt as {cwd: {text, sequence}} ({} when none /
    unreadable). The page caches this whole map so opening the form seeds the
    box synchronously — it is bounded by NS_DRAFT_MAX."""
    document = _stored_object(NEW_SESSION_DRAFT_KEY)
    return {
        str(working_directory): _new_session_draft(draft)
        for working_directory, draft in document.items()
    }


def set_new_session_draft(working_directory, text, sequence):
    """Persist `text` at `sequence` as the draft for directory `cwd`, DROPPING a write
    older than that directory's stored sequence (the stale-write guard above — per
    entry, so two directories' saves never fight) and pruning the map back to
    NS_DRAFT_MAX. Atomic read-modify-write (mutate_map — one BEGIN IMMEDIATE, so
    the compare, the set and the prune can't straddle a peer request thread's
    write). Returns the stored entry, with `stale` True when this write was
    rejected; best-effort like set()."""
    key = str(working_directory)
    keep = {}

    def _apply(d):
        current = (
            _new_session_draft(d[key])
            if key in d
            else {"text": "", "sequence": 0}
        )
        if sequence < current["sequence"]:
            keep["stale"] = True
            return
        d[key] = {"text": text, "sequence": sequence}
        if len(d) > NEW_SESSION_DRAFT_LIMIT:
            # oldest-first by sequence, keeping this write (it has the newest clock)
            for k in sorted(
                d,
                key=lambda draft_key: _new_session_draft(d[draft_key])["sequence"],
            )[:len(d) - NEW_SESSION_DRAFT_LIMIT]:
                d.pop(k, None)
    record = mutate_map(NEW_SESSION_DRAFT_KEY, _apply)
    return dict(_new_session_draft(record.get(key)), stale=bool(keep.get("stale")))


# --- notification mute (the session header's ◉/○ opt-out) ------------------------
# The set of sessions the user opted OUT of the deferred Telegram alert
# (docs/dashboard.md, *Telegram alerts*), stored under one kv key as a
# {session_id: True} map. Global like hidden-dirs — the mute is a dashboard
# preference, not session state, so it survives park and applies live or parked.
# An un-mute DELETES the key so the map stays the small set of muted sids, never
# a row per session ever seen.
NOTIFY_MUTE_KEY = "notify-muted"


def notify_muted(session_id):
    """True when `session_id` is opted out of the deferred Telegram alert."""
    value = _stored_object(NOTIFY_MUTE_KEY).get(str(session_id), False)
    if not isinstance(value, bool):
        raise TypeError("notification mute must be a boolean")
    return value


def set_notify_muted(session_id, muted):
    """Mute (or un-mute) the Telegram alert for `session_id`; returns the updated map.
    Atomic read-modify-write (mutate_map) so two concurrent mute toggles can't
    lose each other's change; best-effort like set()."""
    def _apply(d):
        if muted:
            d[str(session_id)] = True
        else:
            d.pop(str(session_id), None)
    return mutate_map(NOTIFY_MUTE_KEY, _apply)


# --- the mirror's VIEW MODE, per session (the view bar's 3-way control) ----------
# Which of harness TUI's three native history densities the session's web mirror is
# rendered at (docs/dashboard.md, *View modes*), stored under one kv key as a
# {session_id: mode} map.
#
# DELIBERATELY per-session and NOT a global default: switching one session to
# focus must not silently re-render every other one (a mode is a per-session
# reading choice, unlike the alerts switch, which is one machine-wide policy).
#
# A session nobody switched opens at DEFAULT — the same mode harness TUI's own
# `viewMode` defaults to, so the dashboard reads like the TUI it mirrors. It
# shipped defaulting to `verbose` (the mode that hides nothing) while the collapse
# was new and unproven; nothing is actually lost at `default` — every collapsed
# run is one click from expanded, and mutations, messages and the ⚠ warning line
# never fold at all — so the cautious default outlived its reason.
#
# This ONLY ever changes what the browser paints. harness TUI has its own
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


def view_mode(session_id):
    """The stored view mode for `session_id`, or VIEW_DEFAULT when unset."""
    mode = _stored_object(VIEW_MODE_KEY).get(str(session_id), VIEW_DEFAULT)
    if mode not in VIEW_MODES:
        raise ValueError(f"unknown stored view mode: {mode!r}")
    return mode


def set_view_mode(session_id, mode):
    """Persist `mode` as the view mode for `session_id`; returns the updated map.
    Atomic read-modify-write (mutate_map) so two sessions' concurrent switches
    can't lose each other's entry; best-effort like set(). The default mode is
    stored as an ABSENCE (see the note above)."""
    def _apply(d):
        if mode == VIEW_DEFAULT:
            d.pop(str(session_id), None)
        else:
            d[str(session_id)] = mode
    return mutate_map(VIEW_MODE_KEY, _apply)


# --- global alerts toggle (the list page's ◉/○, next to "+ session") -------------
# The ONE master switch over every dashboard notification — the cross-session
# toasts / OS notifications AND the deferred Telegram / web-push alerts
# (docs/dashboard.md, *Global alerts toggle*). Stored under one kv key as a bare
# bool. GLOBAL like the other dashboard prefs: it lives at DASH_PREFS_DB
# (~/.harness), independent of any repo checkout, so the one flag governs every
# session — live or parked, in the main checkout or any git worktree. DEFAULT ON:
# an absent key reads True, so a fresh install alerts until the user opts out.
# When OFF it OVERRIDES the per-session notify_muted map (everything suppressed);
# when ON the per-session mutes still apply.
NOTIFY_ENABLED_KEY = "notify-enabled"


def notify_enabled():
    """The global alerts toggle, defaulting to on when absent."""
    enabled = get(NOTIFY_ENABLED_KEY, True)
    if not isinstance(enabled, bool):
        raise TypeError("notification preference must be a boolean")
    return enabled


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
    """The list of stored push subscriptions."""
    subscriptions = list(_stored_object(PUSH_SUBS_KEY).values())
    if not all(isinstance(subscription, dict) for subscription in subscriptions):
        raise TypeError("push subscriptions must contain objects")
    return subscriptions


def add_push_subscription(sub, device, label=None):
    """Upsert one subscription (its wire JSON: {endpoint, keys:{p256dh, auth}}),
    keyed by endpoint so a repeat subscribe from the same browser replaces its
    prior entry. `device` (the browser's stable id) + `label` (a friendly name)
    are stored ALONGSIDE the wire fields so the notifier can route the on-device
    push to the most-recently-used device (webpush.send ignores the extra keys).
    Returns the updated map; best-effort like set()."""
    endpoint = sub["endpoint"]
    record = dict(sub)
    record["device"] = str(device)
    if label:
        record["label"] = str(label)
    return mutate_map(
        PUSH_SUBS_KEY,
        lambda document: document.__setitem__(str(endpoint), record),
    )


def remove_push_subscription(endpoint):
    """Drop the subscription for `endpoint` (an unsubscribe, or a prune after the
    push service reports it gone). Returns the updated map; best-effort."""
    return mutate_map(PUSH_SUBS_KEY, lambda d: d.pop(str(endpoint), None))


# --- the pinned tasks card's ✕ (a finished list, dismissed) ---------------------
# Which sessions had their tasks card DISMISSED (docs/dashboard.md, *Web tasks*),
# stored under one kv key as {session_id: {ids, ts}}. PURELY VISUAL, like
# view-mode and unlike every other ✕ on the page: no task is completed, deleted
# or touched by this — harness TUI's own task records are never written by the
# dashboard at all (they are the TUI's, and the `tasks` kv is only a snapshot of
# them). The card just stops being painted.
#
# Global like the other dashboard prefs precisely BECAUSE the dismissal has to
# follow you: hiding a finished list on the phone must not leave it pinned on the
# desktop, and localStorage would give every device its own answer. It survives
# park, like the list it hides.
#
# The stored value is the ID SET that was hidden, not a bare True, because the
# card must COME BACK when the list moves on. The ✕ is only offered once EVERY
# task is completed (the server enforces that, not just the button's disabled
# state), so "still hidden" means "still that same finished list": a new task —
# or a completed one re-opened — makes the current list differ and the card
# re-appears on its own. That is the whole un-hide story; a bare flag would need
# a button to undo it, and a card you dismissed would otherwise swallow the next
# thing harness TUI planned.
#
# `ts` is the hide time, kept only to PRUNE: the map would otherwise gain a row
# per session whose list was ever dismissed, forever (renamed-title accepts that
# because a rename is a handful of sessions; a finished task list is most of
# them). Same recency prune as the new-session drafts.
TASKS_HIDE_KEY = "tasks-hidden"
TASKS_HIDE_MAX = 200


def _tasks_hide_entry(d):
    """Validate and return one stored {ids, ts} dismissal."""
    if not isinstance(d, dict):
        raise TypeError("task dismissal must contain an object")
    ids = d.get("ids")
    ts = d.get("ts")
    if not isinstance(ids, list) or not all(isinstance(task_id, str) for task_id in ids):
        raise TypeError("task dismissal ids must be strings")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        raise TypeError("task dismissal timestamp must be a number")
    return {"ids": ids, "ts": ts}


def tasks_hidden_ids(session_id):
    """The task ids `session_id`'s dismissed card covered ([] when never dismissed /
    unreadable). The CALLER decides whether that dismissal still applies to the
    current list — see dashboard/read/session.py tasks_hidden, the one predicate
    (this store is a dumb kv, like hidden-dirs)."""
    document = _stored_object(TASKS_HIDE_KEY)
    record = document.get(str(session_id))
    return _tasks_hide_entry(record)["ids"] if record is not None else []


def set_tasks_hidden(session_id, ids, ts=None):
    """Dismiss `session_id`'s tasks card for exactly the task `ids` (a list), or RESTORE
    it when `ids` is None (the entry is deleted, so the map stays the small set of
    dismissed sessions). Prunes to TASKS_HIDE_MAX by recency. Returns the updated
    map; atomic read-modify-write (mutate_map), best-effort like set()."""
    stamp = time.time() if ts is None else float(ts)

    def _apply(d):
        if ids is None:
            d.pop(str(session_id), None)
            return
        d[str(session_id)] = {"ids": [str(i) for i in ids], "ts": stamp}
        if len(d) > TASKS_HIDE_MAX:
            # oldest-first by ts, keeping this write (it has the newest clock)
            for k in sorted(d, key=lambda k: _tasks_hide_entry(d[k])["ts"]
                            )[:len(d) - TASKS_HIDE_MAX]:
                d.pop(k, None)
    return mutate_map(TASKS_HIDE_KEY, _apply)
