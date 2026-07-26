# dashboard/notify/presence.py — "do you need alerting" presence signals.
#
# The ephemeral, in-memory signals the deferred alert consults to decide whether
# to nag you: whether the session ended, whether you're composing an unsent
# reply, whether a browser is viewing it, and which device you most recently
# used (for on-device push routing). Live-only (no audit rows of their own — the
# SUPPRESS they drive is what lands a notify-suppress row); the singleton server
# means one dict is the whole truth.
import time

from core import env as EV
from core import sessionapi as API
from dashboard import prefs
from dashboard.read.session import composer_draft


def session_ended(sid):
    """True when the session has a recorded SessionEnd (audit `ended_at` set) —
    it was closed/quit, so a pending Telegram alert is moot. A MISSING row is
    deliberately NOT ended: a transient read miss must never suppress a live
    session's alert (the fire path re-checks anyway)."""
    if not sid:
        return False
    return bool((API.session_row(sid) or {}).get("ended_at"))


def composing(sid):
    """True when the session has a non-empty UNSENT web composer draft — you're
    actively working on a reply, so a pending alert would just nag you about a
    session you're already handling. `composer_draft` returns None for an empty
    / tombstone draft, so this is exactly 'there is unsent text'. Read-only."""
    return bool(sid and composer_draft(sid))


# Per-session "a browser is LOOKING AT this session right now" presence. The
# page POSTs /api/session/<sid>/viewing on a heartbeat, but ONLY while it is
# visible + focused + showing that session (app.13-init.js presenceBeat). So the
# mere arrival of a recent beat IS the "you're watching the dashboard" signal
# the deferred Telegram alert suppresses on — the web analog of the kitty tab
# being frontmost. In-memory + TTL'd: this is ephemeral live-only presence
# (like the SSE connection, it earns NO per-beat audit row — the SUPPRESS it
# drives is what lands a notify-suppress row), and the singleton server means
# one dict is the whole truth. A plain dict get/set is atomic enough for the
# 1 s watcher read vs the request-thread writes (no torn state, worst case a
# beat lands a tick late).
# SERVED to the page (GET /api/limits, docs/dashboard.md *Served limits*): the
# beat cadence is derived from this, so the knob must reach the browser — a
# matching literal there silently broke suppression whenever this was lowered.
VIEW_TTL_S = EV.env_float("CLAUDE_DASH_VIEW_TTL_S", 20)
_VIEWING = {}                      # sid -> monotonic deadline (last beat + TTL)


def mark_viewing(sid):
    """Record a viewing heartbeat for `sid` — presence is fresh for VIEW_TTL_S.

    Also SWEEPS the expired entries, which is what keeps this dict bounded in a
    days-long singleton: `web_viewing` only ever drops the ONE key it was asked
    about, and the notifier only asks about ARMED sessions, so every session you
    ever opened and never got an alert for used to sit here for the life of the
    process — the same key-set leak `read/cache.py` bounds its memos with
    API.BoundedLRU for. A sweep (not an LRU) because the bound here can be
    EXACT: an entry past its deadline is dead by definition, so nothing live is
    ever dropped, and what remains is one key per session actually being
    watched. O(n) over that handful, on a per-device heartbeat."""
    if not sid:
        return
    now = time.monotonic()
    for k in [k for k, dl in list(_VIEWING.items()) if dl <= now]:
        _VIEWING.pop(k, None)
    _VIEWING[sid] = now + VIEW_TTL_S


def web_viewing(sid):
    """True when a browser reported viewing `sid` within the last VIEW_TTL_S
    (visible + focused + on that session). Read-only; also GC's the stale key."""
    if not sid:
        return False
    dl = _VIEWING.get(sid)
    if dl is None:
        return False
    if dl <= time.monotonic():
        _VIEWING.pop(sid, None)
        return False
    return True


# Per-DEVICE presence: the last monotonic time each browser (a stable device id
# minted in localStorage — app.js DEVICE_ID) reported its dashboard visible +
# focused, via the /api/presence beat (ANY view, not just a session — so it
# records "you were on this device" even from the list). This is how the
# on-device push routes to the ONE device you most recently used rather than
# fanning out to all: `mru_push_targets` picks the subscribed device with the
# newest beat. Never TTL-expired for that choice (we want the LAST device you
# used even if a while ago); it's a monotonic-max pick, not a freshness gate.
#
# So this one can't be swept the way _VIEWING is — no entry is ever "dead" — and
# it is CAPPED instead (BoundedLRU, recency refreshed on write = on beat). It is
# a dict of every browser that ever beat at this server, and while that is
# normally a handful, nothing bounds it: a private window mints a fresh
# DEVICE_ID per session, so a phone/laptop pair is the happy case, not the
# guarantee. Eviction is safe by construction: the LRU drops the
# least-recently-BEATEN device, which is by definition not the MRU target this
# map exists to pick — and an evicted device that beats again is simply re-added
# (a subscription that outlived its presence just reads `age_s: None`, the same
# as a device that hasn't beaten this run).
DEVICE_SEEN_CAP = 64
_DEVICE_SEEN = API.BoundedLRU(DEVICE_SEEN_CAP)   # device_id -> monotonic last-seen


def mark_device(device):
    """Record a presence beat from `device` (a browser's stable id)."""
    if device:
        _DEVICE_SEEN[device] = time.monotonic()


def device_seen(device):
    """The last-seen monotonic for `device`, or -inf (never seen / no id)."""
    if not device:
        return float("-inf")
    return _DEVICE_SEEN.get(device, float("-inf"))


def mru_push_targets():
    """The push subscriptions of the MOST-RECENTLY-USED device — the on-device
    alert goes here, not to every subscription — PLUS a decision dict for the
    audit (`notify-route`), so a "wrong device buzzed" is answerable from the DB:
    the chosen device and every candidate with its presence age. Groups all
    subscriptions by their stored `device` id and picks the group whose device
    has the newest presence beat (`device_seen`). Degrades safely: with NO
    device tags at all (legacy subs from before device routing) it returns every
    sub (`legacy:True`), so nothing is silently lost; a subscribed device that
    never beat this run has `age_s:None` (still selectable — it's the last device
    you had). Returns (targets, decision)."""
    subs = prefs.push_subscriptions()
    now = time.monotonic()

    def cand(s):
        dev = s.get("device") if isinstance(s, dict) else None
        seen = device_seen(dev)
        return {"device": dev, "label": (s.get("label") if isinstance(s, dict) else None),
                "age_s": (None if seen == float("-inf") else round(now - seen, 1))}

    if not subs:
        return [], {"target": None, "legacy": False, "n_subs": 0, "candidates": []}
    tagged = [s for s in subs if isinstance(s, dict) and s.get("device")]
    if not tagged:                     # legacy: no device ids → can't route, send all
        return subs, {"target": None, "legacy": True, "n_subs": len(subs),
                      "candidates": [cand(s) for s in subs]}
    best = max((s.get("device") for s in tagged), key=device_seen)
    targets = [s for s in tagged if s.get("device") == best]
    return targets, {"target": best, "target_label": targets[0].get("label"),
                     "legacy": False, "n_subs": len(subs),
                     "candidates": [cand(s) for s in tagged]}
