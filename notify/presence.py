# dashboard/notify/presence.py — "do you need alerting" presence signals.
#
# The ephemeral, in-memory signals the deferred alert consults to decide whether
# to nag you: whether the session ended, whether you're composing an unsent
# reply, whether a browser is viewing it, and which device you most recently
# used (for on-device push routing). Live-only (no audit rows of their own — the
# SUPPRESS they drive is what lands a notify-suppress row); the singleton server
# means one dict is the whole truth.
import time
from collections import OrderedDict

from core import env as EV
from dashboard import prefs


# Per-session "a browser is LOOKING AT this session right now" presence. The
# page POSTs /api/session/<session_id>/viewing on a heartbeat, but ONLY while it is
# visible + focused + showing that session (app.13-init.js presenceBeat). So the
# mere arrival of a recent beat IS the "you're watching the dashboard" signal
# the deferred Telegram alert suppresses on — the web analog of the terminal
# tab being frontmost. In-memory + TTL'd: this is ephemeral live-only presence
# (like the SSE connection, it earns NO per-beat audit row — the SUPPRESS it
# drives is what lands a notify-suppress row), and the singleton server means
# one dict is the whole truth. A plain dict get/set is atomic enough for the
# 1 s watcher read vs the request-thread writes (no torn state, worst case a
# beat lands a tick late).
# served to the page in the global application snapshot: the
# beat cadence is derived from this, so the knob must reach the browser — a
# matching literal there silently broke suppression whenever this was lowered.
VIEW_LIFETIME_SECONDS = EV.env_float("BAQYLAU_DASHBOARD_VIEW_LIFETIME_SECONDS", 20)
_VIEWING: dict[str, float] = {}    # session_id -> monotonic deadline (last beat + TTL)


def mark_viewing(session_id):
    """Record a viewing heartbeat for `session_id` — presence is fresh for VIEW_LIFETIME_SECONDS.

    Also SWEEPS the expired entries, which is what keeps this dict bounded in a
    days-long singleton: `web_viewing` only ever drops the ONE key it was asked
    about, and the notifier only asks about ARMED sessions, so every session you
    ever opened and never got an alert for used to sit here for the life of the
    process — the same key-set leak `read/cache.py` bounds its memos with
    API.BoundedLRU for. A sweep (not an LRU) because the bound here can be
    EXACT: an entry past its deadline is dead by definition, so nothing live is
    ever dropped, and what remains is one key per session actually being
    watched. O(n) over that handful, on a per-device heartbeat."""
    if not session_id:
        return
    now = time.monotonic()
    for k in [k for k, deadline in list(_VIEWING.items()) if deadline <= now]:
        _VIEWING.pop(k, None)
    _VIEWING[session_id] = now + VIEW_LIFETIME_SECONDS


def web_viewing(session_id):
    """True when a browser reported viewing `session_id` within the last VIEW_LIFETIME_SECONDS
    (visible + focused + on that session). Read-only; also GC's the stale key."""
    if not session_id:
        return False
    deadline = _VIEWING.get(session_id)
    if deadline is None:
        return False
    if deadline <= time.monotonic():
        _VIEWING.pop(session_id, None)
        return False
    return True


# Per-DEVICE presence: the last monotonic time each device reported itself in
# use. A BROWSER reports for itself — a stable device id minted in localStorage
# (app.js DEVICE_ID) POSTed on the /api/presence beat while the page is visible
# + focused (ANY view, not just a session — so it records "you were on this
# device" even from the list). The TERMINAL cannot report for itself, so it
# would have to be POLLED under the reserved id below — nothing does that today,
# and the terminal contract offers no such read until this lands. Once stamped
# it is just another device here, and that is the whole point: ONE map,
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


class RecentDevices(OrderedDict):
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > DEVICE_SEEN_CAP:
            self.popitem(last=False)


_DEVICE_SEEN = RecentDevices()   # device_id -> monotonic last-seen
# The devices that have reported themselves AWAY (`mark_away`) since their last
# beat — a separate set rather than an eviction from _DEVICE_SEEN precisely
# because those two answer different questions: "are you here now"
# (`device_active`) and "where were you last" (`route`). Going away must end the
# first without touching the second.
_AWAY: set[str] = set()

# The reserved device id the TERMINAL is stamped under. A browser's DEVICE_ID is
# a random base36 string, so a collision needs a client to CLAIM this name —
# which `mark_device` refuses, because a device that can impersonate the
# terminal could route every alert to Telegram.
TERMINAL = "terminal"


def mark_device(device):
    """Record a presence beat from `device` (a browser's stable id). A beat is
    the opposite of `mark_away`, so it clears the away flag: the page only beats
    while visible + focused."""
    if device and device != TERMINAL:
        _DEVICE_SEEN[device] = time.monotonic()
        _AWAY.discard(device)


def mark_away(device, session_id=None):
    """The page reports it has STOPPED being present — it lost focus or was
    hidden. The explicit end of a beat, and the fix for a gap the TTL cannot
    close on its own.

    A beat says "I was here within the last VIEW_LIFETIME_SECONDS", which the alert path
    reads as "you are here NOW". Those differ by up to the whole TTL, and the
    page's own gate is INSTANT: it stops toasting the moment `document.hasFocus`
    goes false. So for the 20 s after you clicked away from the dashboard, the
    server suppressed the off-device alert ("a focused page already toasted
    you") while the page refused to toast ("I'm not focused") — measured
    2026-07-29: 20 of 99 suppressed `done` alerts had a `notify.recv` beacon
    from that very device reading `shown:false, focus:false`, i.e. they reached
    the user through NO channel at all. Halving the TTL would only halve the
    window; only the page knows the instant it ends, so the page now says so.

    Clears the two "right now" facts and DELIBERATELY not the third: `_VIEWING`
    (you are no longer watching that session) and the device's ACTIVE flag (no
    longer a browser in your hands), but never `_DEVICE_SEEN`, which is the
    monotonic-max ROUTING pick — where you last were is still true after you
    look away, and forgetting it would send the next alert to a staler device."""
    if device and device != TERMINAL and device in _DEVICE_SEEN:
        # bounded by construction: only a device already in the (capped) seen
        # map can be marked away, and an entry the LRU evicted is pruned here
        _AWAY.add(device)
        _AWAY.intersection_update(set(_DEVICE_SEEN.keys()))
    if session_id:
        _VIEWING.pop(session_id, None)


def device_active():
    """True when a BROWSER reported itself visible + focused within VIEW_LIFETIME_SECONDS —
    "you are on a browser RIGHT NOW", whichever view it shows.

    The freshness question `device_seen`'s monotonic-max deliberately isn't, and
    the web half of "don't alert me about a device I'm holding": a focused page
    shows the in-page toast for EVERY session, so an off-device push would be a
    second copy of a notification you just got. The terminal is excluded because
    its analog is NOT symmetric — the terminal being frontmost tells you nothing about
    the tab you're not on, so at the terminal only `tab_focused` (this session's
    tab, in front of you) counts as seeing it.

    A device that reported itself AWAY is excluded even while its last beat is
    still inside the TTL — that report is strictly newer information than the
    beat, and honouring the beat over it is what silently swallowed alerts
    through no channel at all (see `mark_away`)."""
    now = time.monotonic()
    return any(device_id != TERMINAL and device_id not in _AWAY and now - seen <= VIEW_LIFETIME_SECONDS
               for device_id, seen in list(_DEVICE_SEEN.items()))


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

    A subscribed device that never beat this run has `age_s:None` and remains
    selectable because it is still the last device the application knew."""
    subs = prefs.push_subscriptions()
    now = time.monotonic()

    def cand(device_id, label=None):
        seen = device_seen(device_id)
        return {"device": device_id, "label": label,
                "age_s": (None if seen == float("-inf") else round(now - seen, 1))}

    term_seen = device_seen(TERMINAL)
    term = [cand(TERMINAL, "terminal")] if term_seen != float("-inf") else []

    def decision(target, candidates, label=None):
        return {"target": target, "target_label": label,
                "subscription_count": len(subs), "candidates": candidates + term}

    best = max((subscription["device"] for subscription in subs),
               key=device_seen, default=None)
    if best is not None and device_seen(best) >= term_seen:
        targets = [subscription for subscription in subs
                   if subscription["device"] == best]
        return best, targets, decision(best,
                                       [cand(subscription["device"], subscription.get("label"))
                                        for subscription in subs],
                                       targets[0].get("label"))
    cands = [cand(subscription["device"], subscription.get("label"))
             for subscription in subs]
    if term_seen == float("-inf"):     # nothing subscribed, terminal never seen
        return None, [], decision(None, cands)
    return TERMINAL, [], decision(TERMINAL, cands, "terminal")
