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


# Per-DEVICE presence: the last monotonic time each device reported itself in
# use. A BROWSER reports for itself — a stable device id minted in localStorage
# (app.js DEVICE_ID) POSTed on the /api/presence beat while the page is visible
# + focused (ANY view, not just a session — so it records "you were on this
# device" even from the list). The TERMINAL cannot report for itself, so the
# notifier POLLS it (`mark_terminal`, keyed on the reserved id below) — but once
# stamped it is just another device here, and that is the whole point: ONE map,
# one most-recently-seen pick, and the alert goes wherever you last were
# (docs/dashboard.md *Presence routing*). This is how an alert routes to the ONE
# device you most recently used rather than fanning out to all: `route()` picks
# the device with the newest beat. Never TTL-expired for that choice (we want the
# LAST device you used even if a while ago); it's a monotonic-max pick, not a
# freshness gate — `device_active` is the separate freshness question.
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

# The reserved device id the TERMINAL is stamped under. A browser's DEVICE_ID is
# a random base36 string, so a collision needs a client to CLAIM this name —
# which `mark_device` refuses, because a device that can impersonate the
# terminal could route every alert to Telegram.
TERMINAL = "terminal"


def mark_device(device):
    """Record a presence beat from `device` (a browser's stable id)."""
    if device and device != TERMINAL:
        _DEVICE_SEEN[device] = time.monotonic()


def mark_terminal():
    """Record that you are AT THE TERMINAL right now — the terminal's analog of
    a browser's /api/presence beat. It cannot beat for itself (nothing runs in
    the terminal to POST for it), so the notifier POLLS the frontend's
    `app_focused` and calls this; the stamp is otherwise an ordinary device
    presence and competes with the browsers on plain recency."""
    _DEVICE_SEEN[TERMINAL] = time.monotonic()


def device_active():
    """True when a BROWSER reported itself visible + focused within VIEW_TTL_S —
    "you are on a browser RIGHT NOW", whichever view it shows.

    The freshness question `device_seen`'s monotonic-max deliberately isn't, and
    the web half of "don't alert me about a device I'm holding": a focused page
    shows the in-page toast for EVERY session, so an off-device push would be a
    second copy of a notification you just got. The terminal is excluded because
    its analog is NOT symmetric — kitty being frontmost tells you nothing about
    the tab you're not on, so at the terminal only `tab_focused` (this session's
    tab, in front of you) counts as seeing it."""
    now = time.monotonic()
    return any(dev != TERMINAL and now - seen <= VIEW_TTL_S
               for dev, seen in list(_DEVICE_SEEN.items()))


def device_seen(device):
    """The last-seen monotonic for `device`, or -inf (never seen / no id)."""
    if not device:
        return float("-inf")
    return _DEVICE_SEEN.get(device, float("-inf"))


def route():
    """WHICH DEVICE you are most likely at right now, and what can reach it.
    Returns `(target, targets, decision)`:

      target    the device that won — TERMINAL, a browser `device` id, or None
                (nothing to weigh: no subscriptions AND no terminal presence)
      targets   that device's push subscriptions; EMPTY when the terminal won,
                because a terminal is reached by Telegram, not by push. Mapping
                a target to a CHANNEL is the caller's business (channels.py owns
                that vocabulary and imports this module, not the other way).
      decision  the `notify-route` audit dict — the winner plus EVERY candidate
                with its presence age, so "why did the iPad and not my Mac
                buzz" is answerable from the DB after the fact.

    One most-recently-seen pick over `_DEVICE_SEEN`, where the terminal is just
    another row. WEB WINS TIES — the terminal must be STRICTLY newer to take it
    — which is what makes the all-unseen case (a fresh server, nothing has
    beaten yet) route to a quiet on-device push rather than to Telegram.

    Degrades safely: with NO device tags at all (legacy subs from before device
    routing) every sub is returned (`legacy:True`), so nothing is silently lost;
    a subscribed device that never beat this run has `age_s:None` (still
    selectable — it's the last device you had)."""
    subs = prefs.push_subscriptions()
    now = time.monotonic()

    def cand(dev, label=None):
        seen = device_seen(dev)
        return {"device": dev, "label": label,
                "age_s": (None if seen == float("-inf") else round(now - seen, 1))}

    term_seen = device_seen(TERMINAL)
    term = [cand(TERMINAL, "terminal")] if term_seen != float("-inf") else []

    def decision(target, legacy, cands, label=None):
        return {"target": target, "target_label": label, "legacy": legacy,
                "n_subs": len(subs), "candidates": cands + term}

    tagged = [s for s in subs if isinstance(s, dict) and s.get("device")]
    if subs and not tagged:            # legacy: no device ids → can't route, send all
        cands = [cand(None, s.get("label") if isinstance(s, dict) else None)
                 for s in subs]
        # An untagged sub has no presence of its own, so the terminal beats it
        # whenever the terminal has ANY presence — the same strictly-newer rule,
        # with the web side reading as never-seen.
        if term_seen == float("-inf"):
            return None, subs, decision(None, True, cands)
        return TERMINAL, [], decision(TERMINAL, True, cands, "terminal")
    best = max((s.get("device") for s in tagged), key=device_seen, default=None)
    if best is not None and device_seen(best) >= term_seen:
        targets = [s for s in tagged if s.get("device") == best]
        return best, targets, decision(best, False,
                                       [cand(s.get("device"), s.get("label"))
                                        for s in tagged],
                                       targets[0].get("label"))
    cands = [cand(s.get("device"), s.get("label")) for s in tagged]
    if term_seen == float("-inf"):     # nothing subscribed, terminal never seen
        return None, [], decision(None, False, cands)
    return TERMINAL, [], decision(TERMINAL, False, cands, "terminal")
