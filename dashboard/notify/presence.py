# dashboard/notify/presence.py — "do you need alerting" presence signals.
#
# The ephemeral, in-memory signals the deferred alert consults to decide whether
# to nag you: whether the session ended, whether you're composing an unsent
# reply, whether a browser is viewing it, and which device you most recently
# used (for on-device push routing). Live-only (no audit rows of their own — the
# SUPPRESS they drive is what lands a notify-suppress row); the singleton server
# means one dict is the whole truth.
import os
import time

from core import sessionapi as API
from dashboard import prefs
from dashboard.read.session import _composer_draft


def _session_ended(sid):
    """True when the session has a recorded SessionEnd (audit `ended_at` set) —
    it was closed/quit, so a pending Telegram alert is moot. A MISSING row is
    deliberately NOT ended: a transient read miss must never suppress a live
    session's alert (the fire path re-checks anyway)."""
    if not sid:
        return False
    return bool((API.session_row(sid) or {}).get("ended_at"))


def _composing(sid):
    """True when the session has a non-empty UNSENT web composer draft — you're
    actively working on a reply, so a pending alert would just nag you about a
    session you're already handling. `_composer_draft` returns None for an empty
    / tombstone draft, so this is exactly 'there is unsent text'. Read-only."""
    return bool(sid and _composer_draft(sid))


# Per-session "a browser is LOOKING AT this session right now" presence. The
# page POSTs /api/session/<sid>/viewing on a heartbeat, but ONLY while it is
# visible + focused + showing that session (dashboard/static/app.js). So the
# mere arrival of a recent beat IS the "you're watching the dashboard" signal
# the deferred Telegram alert suppresses on — the web analog of the kitty tab
# being frontmost. In-memory + TTL'd: this is ephemeral live-only presence
# (like the SSE connection, it earns NO per-beat audit row — the SUPPRESS it
# drives is what lands a notify-suppress row), and the singleton server means
# one dict is the whole truth. A plain dict get/set is atomic enough for the
# 1 s watcher read vs the request-thread writes (no torn state, worst case a
# beat lands a tick late).
VIEW_TTL_S = float(os.environ.get("CLAUDE_DASH_VIEW_TTL_S") or 20)
_VIEWING = {}                      # sid -> monotonic deadline (last beat + TTL)


def _mark_viewing(sid):
    """Record a viewing heartbeat for `sid` — presence is fresh for VIEW_TTL_S."""
    if sid:
        _VIEWING[sid] = time.monotonic() + VIEW_TTL_S


def _web_viewing(sid):
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
# fanning out to all: `_mru_push_targets` picks the subscribed device with the
# newest beat. Never TTL-expired for that choice (we want the LAST device you
# used even if a while ago); it's a monotonic-max pick, not a freshness gate.
_DEVICE_SEEN = {}                  # device_id -> monotonic last-seen


def _mark_device(device):
    """Record a presence beat from `device` (a browser's stable id)."""
    if device:
        _DEVICE_SEEN[device] = time.monotonic()


def _device_seen(device):
    """The last-seen monotonic for `device`, or -inf (never seen / no id)."""
    if not device:
        return float("-inf")
    return _DEVICE_SEEN.get(device, float("-inf"))


def _mru_push_targets():
    """The push subscriptions of the MOST-RECENTLY-USED device — the on-device
    alert goes here, not to every subscription — PLUS a decision dict for the
    audit (`notify-route`), so a "wrong device buzzed" is answerable from the DB:
    the chosen device and every candidate with its presence age. Groups all
    subscriptions by their stored `device` id and picks the group whose device
    has the newest presence beat (`_device_seen`). Degrades safely: with NO
    device tags at all (legacy subs from before device routing) it returns every
    sub (`legacy:True`), so nothing is silently lost; a subscribed device that
    never beat this run has `age_s:None` (still selectable — it's the last device
    you had). Returns (targets, decision)."""
    subs = prefs.push_subscriptions()
    now = time.monotonic()

    def cand(s):
        dev = s.get("device") if isinstance(s, dict) else None
        seen = _device_seen(dev)
        return {"device": dev, "label": (s.get("label") if isinstance(s, dict) else None),
                "age_s": (None if seen == float("-inf") else round(now - seen, 1))}

    if not subs:
        return [], {"target": None, "legacy": False, "n_subs": 0, "candidates": []}
    tagged = [s for s in subs if isinstance(s, dict) and s.get("device")]
    if not tagged:                     # legacy: no device ids → can't route, send all
        return subs, {"target": None, "legacy": True, "n_subs": len(subs),
                      "candidates": [cand(s) for s in subs]}
    best = max((s.get("device") for s in tagged), key=_device_seen)
    targets = [s for s in tagged if s.get("device") == best]
    return targets, {"target": best, "target_label": targets[0].get("label"),
                     "legacy": False, "n_subs": len(subs),
                     "candidates": [cand(s) for s in tagged]}

