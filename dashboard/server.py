# dashboard/server.py — the web dashboard's HTTP server.
#
# A thin localhost server over the read-side session API (core/sessionapi.py)
# and the plugins.activity() drill-down — the dashboard is a CONSUMER like the
# pane renderers, with a browser instead of a pty. Design decisions inherited
# from docs/sessionapi.md's dashboard notes (each rejects a specific trap):
#
#   * READ-ONLY, bound to 127.0.0.1 — never a routable interface; the page
#     shows raw command output and transcripts.
#   * ThreadingHTTPServer + per-request fresh mode=ro reads — NOT the OTLP
#     receiver's single-threaded loop (sqlite thread-affinity is incompatible
#     with concurrent SSE streams). Every read here goes through the API's
#     *_at()/fresh-conn paths; the server holds no cross-thread connection.
#     In particular ops are read via ops_at() on the RESOLVED DB path, never
#     ops_after() — the live-path readers go through connect(), which CREATES
#     the DB and would fake the session-alive signal for a parked session.
#   * Singleton via core/locks.py pid-lock on paths.DASH_DB plus the port bind
#     as the second guard; explicit serve lifecycle (start/stop/serve CLI) —
#     NOT the receiver's 900s idle-exit + respawn-on-SessionStart, which would
#     leave the dashboard down exactly when browsing parked sessions.
#   * Audit shape: the bin/ entry spawns `serve` via core/spawn.spawn_detached
#     (the A.spawn row) and serve() runs inside core.tail.stream_lifecycle
#     (kind='dashboard'), so the server's lifetime is a streams row with a
#     real end_reason (stopped / lock-denied / port-busy / crash).
#   * HTML-escaping (dashboard/opshtml.py) is the neutralize() analog.
#
# The notification watcher (toasts): one daemon thread diffs the global tab
# DB's whole table (sessionapi.tab_states) once a second and maps windows to
# their NEWEST audited session (sessions rows carry kitty_window_id). A
# transition to awaiting-command (red — Claude is asking you) or
# awaiting-response (green — done, your turn) is pushed to every connected
# /events client, which shows the toast / OS notification. Window-keyed by
# nature: a headless/daemon session has no window and therefore no toasts,
# same as it has no tab colour. The SAME transitions also arm a DEFERRED
# off-device Telegram alert (the reused `notify` skill) that fires only if the
# tab is still in that state after a grace window — you didn't react — and the
# session isn't muted (docs/dashboard.md, *Telegram alerts*).
import base64
import binascii
import gzip
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import frontends  # noqa: F401  -- re-exported as DS.frontends (control plane reaches the terminal via launch)
import plugins
from core import copy as CP
from core import locks
from core import paths as P
from core import sessionapi as API
from core import spawn as SP
from core import state as ST
from core import tabs
from core.noaudit import load_audit
from core.tail import stream_lifecycle
from dashboard import askdialog, confirmdialog, dictate, opshtml, \
    plandialog, prefs, rewindmenu, suggestion, webpush

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


# Config vocabulary lives in dashboard/config.py; server.py re-exports it so the
# historical `server.X` reads (tests, bin) keep resolving, and reads the module
# as `config` for the knobs the notifier consults live.
from dashboard import config  # noqa: F401  -- re-exported as DS.config for live-knob patch targets
from dashboard.config import (  # noqa: F401  -- facade re-export of the config surface
    ALLOWED_ORIGINS, BOOT_ID, BUSY_TABS, CLIENTLOG_FIELD_MAX, CLIENTLOG_MAX,
    CLIENTLOG_STR_MAX, DOUBLE_ESC_GAP_S, DRAFT_CLEAR_GAP_S, ESCALATE_S,
    GLOBAL_TICK_S, GZIP_MIN, HEARTBEAT_S, HOST, IMAGE_MIMES, LOCK_KEY,
    NOTIFY_CMD, NOTIFY_DELAY_S, NOTIFY_STATES, NOTIFY_TELEGRAM,
    NOTIFY_TELEGRAM_ALWAYS, NOTIFY_URL_BASE, NOTIFY_WEBPUSH, POST_HEADER,
    POST_MAX, PORT, QUEUE_TABS, READONLY, RESUMABLE_MAX, RESUMABLE_SCAN,
    SCREEN_CLIP, SESSIONS_LIMIT, SLOW_EVERY, STATIC, STATIC_DIR,
    STATS_TOP_PROJECTS, STATS_TTL_S, TICK_S, UPLOAD_MAX, _SID_OK,
    _clip_screen, extra_origins,
)


# --- notification watcher -----------------------------------------------------------

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


class Notifier:
    """The tab-DB diff watcher + the /events fan-out. Clients register a
    Queue; the watcher thread pushes ('notify', payload) on every asking/done
    transition (the in-page toast + OS notification). Also keeps the win ->
    session map the payloads are named from (refreshed on the slow cadence —
    sessions come and go rarely).

    It ALSO drives the deferred off-device Telegram alert: each asking/done
    transition arms `self.pending[win]`; a later scan SENDS it iff the tab is
    still in that state after NOTIFY_DELAY_S (you didn't react) and the session
    isn't muted — otherwise the entry is dropped when the tab moves off that
    state, the session ends (you closed it / moved on), or you're composing a
    reply to it (an unsent web draft = you're already on it)."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()
        self.prev = None               # None = not yet baselined; distinct from
        #                                {} (a real empty screen — all tabs gone)
        self.winmap = {}
        self.pending = {}              # win -> dict(payload, armed_at, state)
        self.fe = None                 # cached Frontend for the dialog-region
        #                                read (refreshed on the slow cadence)

    def register(self):
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.add(q)
        return q

    def unregister(self, q):
        with self.lock:
            self.clients.discard(q)

    def push(self, event, payload):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass                       # a stalled client just misses toasts

    def refresh_winmap(self):
        m = {}
        for row in API.sessions(SESSIONS_LIMIT):
            win = row.get("kitty_window_id")
            # newest-first: the first (newest) session keeps the window
            if win and win not in m:
                m[win] = row
        self.winmap = m
        # the frontend used to read a red tab's dialog region (below). Resolved
        # here, not per-scan: a hunt for kitty's socket is a subprocess, and a
        # missing terminal control channel degrades cleanly to None → no
        # dialog-activity signal, alerts fire as before.
        self.fe = launch._frontend()

    def _dialog_region(self, win):
        """The AskUserQuestion dialog pane's text on window `win`, or None when
        there's no terminal channel / read miss. `askdialog.region` isolates the
        dialog (from its header-chip bar down), so a live-ticking status line
        below it doesn't register as change — and it's "" for a non-ask red tab
        (a permission / plan prompt has no ☐/☒ chip), so those keep the plain
        grace-window behaviour."""
        fe = self.fe
        if not (fe and win):
            return None
        try:
            return askdialog.region(fe.get_text(win) or "")
        except Exception:
            return None

    def _input_typed(self, win):
        """The REAL (non-faint) text the user has typed into the terminal input
        box on window `win`, or None. The 'done'-arm analog of _dialog_region:
        a green tab you're replying to AT THE TERMINAL leaves no other trace
        (typing into the `❯` box moves neither the tab off green nor the
        transcript until you submit), so this is what tells 'still composing in
        the kitty tab' from 'walked away'. None on no terminal channel / read
        miss / empty-or-ghost box → those keep the plain grace-window behaviour.
        Needs the ANSI capture (faint-SGR detection), unlike _dialog_region."""
        fe = self.fe
        if not (fe and win):
            return None
        try:
            return suggestion.typed(fe.get_text(win, ansi=True) or "")
        except Exception:
            return None

    def _watching(self, win, sid, tree=None):
        """You are LOOKING AT this session, so the deferred alert would only
        nag. Two channels: the kitty TAB is frontmost on your screen
        (`fe.tab_focused` — `is_focused`, so a web-spawned synthetic tab in a
        BACKGROUNDED kitty does NOT count, verified empirically), or a BROWSER
        is actively viewing the session (a fresh `_web_viewing` heartbeat).
        Returns the suppress reason (`tab-focused` / `web-viewing`) or None;
        best-effort — a terminal read miss / no channel degrades to None.

        Called from two places with different meanings (see scan): for a `done`
        arm it runs EVERY scan while armed, so a single glance any time during
        the grace ('I saw the final message') cancels the alert even after you
        move on; for an `asking` arm it runs only at SEND time ('are you looking
        RIGHT NOW'), because a glance that didn't ANSWER still needs the ping.
        `tree` is a pre-fetched `ls()` shared across a scan's entries so the tab
        check costs one `kitten @ ls` per scan, not one per armed session."""
        try:
            if self.fe and win and self.fe.tab_focused(win, tree):
                return "tab-focused"
        except Exception:
            pass
        if _web_viewing(sid):
            return "web-viewing"
        return None

    def _payload(self, kind, state, row):
        # a worktree session's toast names the PROJECT it groups under, not the
        # worktree dir — the SAME group_dir resolution the list page uses (the
        # frozen start_cwd -> its worktree owner), so a session that cd'd away
        # is still named by where it started (_git_resolve is cached, cheap)
        cwd = canon_cwd(row.get("cwd") or "")
        home = _group_dir(canon_cwd(row.get("start_cwd") or "") or cwd)
        return {
            "kind": kind, "state": state, "sid": row.get("sid"),
            "cwd": cwd,
            "project": os.path.basename(home) or row.get("sid"),
            # resolved at push time, not winmap-refresh time: the title is
            # transcript-derived and the transcript just grew ((path, size)
            # cache in session_title keeps this cheap)
            "title": session_title(row.get("transcript_path") or ""),
        }

    def scan(self):
        cur = API.tab_states()
        prev, self.prev = self.prev, cur
        if prev is None:
            return                         # first scan is baseline only, no news
        # NOT `not prev`: when the tab table momentarily empties (all sessions
        # closed), self.prev became {}, and treating an empty prev as a fresh
        # baseline would swallow the very next transition into red/green (its
        # toast AND its Telegram arm). Only the true first scan (prev is None) is
        # a baseline; an empty {} is a real state a transition diffs against.
        now = time.monotonic()
        for win, state in cur.items():
            kind = NOTIFY_STATES.get(state)
            if not kind or prev.get(win) == state:
                continue
            row = self.winmap.get(win)
            if not row:
                continue
            payload = self._payload(kind, state, row)
            self.push("notify", payload)   # immediate in-page toast + OS notif
            if NOTIFY_TELEGRAM or NOTIFY_WEBPUSH:   # arm the deferred off-device
                self.pending[win] = dict(payload, armed_at=now, state=state)
                # ANCHOR the deferred lifecycle: every armed alert ends in
                # exactly one of suppress / route+send (+escalate) / telegram,
                # all keyed back to this `notify-arm` row (a silent disappearance
                # instead = you reacted, the tab moved off red/green — see the
                # paired tab_transitions row).
                A.state_file("", "", "notify-arm",
                             {"sid": payload.get("sid"), "kind": kind,
                              "phase": "arm", "delay_s": NOTIFY_DELAY_S})
        # cancel the ones you reacted to / are already handling, all before the
        # delay: the tab left its armed state (answered → busy, or the win
        # vanished = tab gone), the session ENDED (you closed / quit it — moved
        # on, and the alert's deep link would open a dead session), OR you're
        # actively COMPOSING a reply to it (a non-empty unsent web draft is
        # "I'm on it" — don't nag). ended_at is the robust signal the win-vanish
        # check can miss: a stale tab row can linger, and a reused window id can
        # even re-match the armed state under a DIFFERENT session.
        # one ls per scan, shared by every armed entry's tab-focus check (both
        # the done 'seen it' branch below and the asking send-time check) —
        # avoids a kitten @ ls per armed session per second. Best-effort.
        try:
            tree = self.fe.ls() if (self.fe and self.pending) else None
        except Exception:
            tree = None
        for win in list(self.pending):
            entry = self.pending[win]
            sid = entry.get("sid")
            if (cur.get(win) != entry["state"]
                    or _session_ended(sid) or _composing(sid)):
                del self.pending[win]
                continue
            # You answering AT THE TERMINAL — typing a free-text answer or
            # toggling a selection — doesn't move the tab off red and doesn't
            # grow the transcript (the dialog is still open, unsubmitted), so
            # none of the checks above fire. Its ONLY trace is the dialog region
            # changing. Baseline it on first sighting (the untouched dialog),
            # then drop the arm the moment it differs: you're on it, don't nag.
            if entry.get("kind") == "asking":
                reg = self._dialog_region(win)
                if reg:                          # "" = no ask dialog / read miss
                    if entry.get("ask_region") is None:
                        entry["ask_region"] = reg
                    elif reg != entry["ask_region"]:
                        del self.pending[win]
                        A.state_file("", "", "notify-suppress",
                                     {"sid": sid, "kind": "asking",
                                      "reason": "dialog-activity"})
            # A green `done` tab is your turn; you replying AT THE TERMINAL —
            # typing a message into the `❯` input box — likewise moves neither
            # the tab off green nor the transcript until you submit, so the
            # checks above miss it. Its trace is REAL (non-faint) content in the
            # input box (a settled tab pre-fills only a FAINT ghost suggestion,
            # which `suggestion.typed` ignores). Drop the arm the moment any is
            # there: you're continuing the conversation in the kitty tab.
            elif entry.get("kind") == "done":
                if self._input_typed(win):
                    del self.pending[win]
                    A.state_file("", "", "notify-suppress",
                                 {"sid": sid, "kind": "done",
                                  "reason": "terminal-input"})
                else:
                    # "If I've SEEN the final message, no notification." A done
                    # tab's final message is on screen the moment it goes green,
                    # so ANY glance during the grace — the kitty tab frontmost
                    # or a browser viewing the session — means you saw it. Check
                    # every scan (not just at send time), so a glance that has
                    # since ended still cancels: you don't need to be told about
                    # a result you already read.
                    seen = self._watching(win, sid, tree)
                    if seen:
                        del self.pending[win]
                        A.state_file("", "", "notify-suppress",
                                     {"sid": sid, "kind": "done",
                                      "reason": seen})
        # fire the ones that persisted past the grace window (once each) —
        # unless, at THIS moment, you're looking at the session (the kitty tab
        # is frontmost, or a browser is actively viewing it): then you don't
        # need an off-device ping, so drop it with a notify-suppress row. In
        # practice this send-time check now matters for `asking` arms: a `done`
        # arm that was ever seen was already dropped above (the 'seen it' rule),
        # so a done arm reaching here was never looked at.
        # DEVICE-FIRST, TELEGRAM-IF-IGNORED. Two stages per armed entry:
        #  1. after the grace window, the ON-DEVICE push goes to the one device
        #     you most recently used (_webpush → _mru_push_targets); the entry
        #     STAYS armed, now with an escalate_at ESCALATE_S in the future.
        #  2. if it survives to escalate_at — you STILL did nothing with the
        #     session (any reaction / look already dropped it in the cancel loop
        #     above) — Telegram nudges you, in case you're away from that device.
        # Telegram is instead the IMMEDIATE fallback when there's no device to
        # push to (nobody subscribed); `_ALWAYS` fires both at stage 1.
        for win in list(self.pending):
            entry = self.pending[win]
            escalating = entry.get("notified") is not None
            due = entry["escalate_at"] if escalating else entry["armed_at"] + NOTIFY_DELAY_S
            if now < due:
                continue
            sid = entry.get("sid")
            # looking at it RIGHT NOW = you're handling it; don't ping (the done
            # 'seen it' cancel above already caught it per-scan — this is the
            # asking arm's send-time check, applied at both stages).
            watching = self._watching(win, sid, tree)
            if watching:
                del self.pending[win]
                A.state_file("", "", "notify-suppress",
                             {"sid": sid, "kind": entry.get("kind"),
                              "reason": watching})
                continue
            if prefs.notify_muted(sid):
                del self.pending[win]
                continue
            if escalating:                         # stage 2: the Telegram nudge
                del self.pending[win]
                if NOTIFY_TELEGRAM:
                    self._telegram(entry, "escalation")
                continue
            # stage 1: on-device push to the most-recently-used device
            pushed = self._webpush(entry) if NOTIFY_WEBPUSH else False
            if pushed and not NOTIFY_TELEGRAM_ALWAYS:
                entry["notified"] = now            # arm the escalation, keep pending
                entry["escalate_at"] = now + ESCALATE_S
                A.state_file("", "", "notify-arm",
                             {"sid": sid, "kind": entry.get("kind"),
                              "phase": "escalate", "in_s": ESCALATE_S})
                continue
            del self.pending[win]
            if NOTIFY_TELEGRAM:                     # no device to push to, or _ALWAYS
                self._telegram(entry, "always" if pushed else "no-device")

    def _telegram(self, entry, reason=None):
        """Send the deferred alert via the reused `notify` skill (Telegram),
        detached so a slow round-trip never stalls the 1 s watcher. Best-effort
        + audited; never raises into the loop. `reason` (in the audit row) says
        WHY Telegram fired: `escalation` (the 5-min nudge after an on-device push
        you ignored), `no-device` (nobody was push-subscribed — the immediate
        fallback), or `always` (`_ALWAYS` forced both) — so a Telegram alert is
        never an unexplained duplicate."""
        asking = entry.get("kind") == "asking"
        proj = entry.get("project") or entry.get("sid") or "session"
        head = ("🔴 %s needs you" if asking else "🟢 %s is done") % proj
        title = entry.get("title") or (
            "Claude is asking a question" if asking else "finished — your turn")
        # ?s=<sid>, NOT the app's #/s/<sid> hash route: Telegram's auto-linker
        # drops the URL fragment, so a #-link opens the dashboard ROOT on the
        # phone, not the session. The sid rides a query param (linkified whole);
        # the page translates ?s=<sid> back into the hash route on load.
        url = "%s/?s=%s" % (NOTIFY_URL_BASE, quote(entry.get("sid") or ""))
        msg = "%s — %s\n%s" % (head, title, url)
        try:
            subprocess.Popen(
                [sys.executable or "python3", NOTIFY_CMD, msg],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            A.state_file("", "", "telegram-notify",
                         {"sid": entry.get("sid"), "kind": entry.get("kind"),
                          "reason": reason})
        except Exception:
            A.error("", "dashboard telegram notify",
                    {"sid": entry.get("sid")})

    def _needs_you_count(self):
        """How many tabs are in a needs-you state (red asking + green done) right
        now — the app-icon badge count (docs/dashboard.md *Installed-app polish*),
        carried in the push so the service worker sets the badge while the app is
        closed. Same source as the tab watcher; NOTIFY_STATES is the red/green
        vocabulary."""
        try:
            return sum(1 for st in API.tab_states().values() if st in NOTIFY_STATES)
        except Exception:
            return 0

    def _webpush(self, entry):
        """Send the on-device alert as a Web Push to the ONE device you most
        recently used (`_mru_push_targets`) — NOT every subscription, so a
        session going done/asking buzzes the device you're working on, not your
        iPad and Mac at once (docs/dashboard.md, *Web push* / *Device routing*).
        Dispatched on a detached daemon thread: the crypto + network round-trips
        must never stall the 1 s watcher. Best-effort + audited; a subscription
        the push service reports GONE (404/410) is pruned. No-op when the crypto
        backend is missing or nobody has subscribed.

        Returns True iff it DISPATCHED to at least one subscription — the signal
        the caller uses to hold Telegram back to the escalation nudge (device
        first, Telegram only if you keep ignoring it). Audits the ROUTING
        DECISION (`notify-route`) — the chosen device + every candidate's
        presence age — so "the wrong device buzzed" is answerable from the DB."""
        if not webpush.enabled():
            return False
        subs, decision = _mru_push_targets()
        # The routing decision is audited whenever there was ANYTHING to weigh
        # (at least one subscription) — even the no-target edge — so a missing
        # push is never a mystery. No subs at all = nothing to route, no row.
        if decision.get("n_subs"):
            A.state_file("", "", "notify-route",
                         dict(decision, sid=entry.get("sid"), kind=entry.get("kind")))
        if not subs:
            return False
        asking = entry.get("kind") == "asking"
        proj = entry.get("project") or entry.get("sid") or "session"
        title = ("🔴 %s needs you" if asking else "🟢 %s is done") % proj
        body = entry.get("title") or (
            "Claude is asking a question" if asking else "finished — your turn")
        # same ?s=<sid> deep link the Telegram alert uses (the app translates it
        # to the #/s/<sid> route) — a #fragment wouldn't survive some clients.
        url = "%s/?s=%s" % (NOTIFY_URL_BASE, quote(entry.get("sid") or ""))
        payload = {"title": title, "body": body,
                   "sid": entry.get("sid") or "", "kind": entry.get("kind"),
                   "url": url, "badge": self._needs_you_count()}
        threading.Thread(target=self._webpush_send, args=(subs, payload),
                         daemon=True).start()
        return True

    def _webpush_send(self, subs, payload):
        """The detached fan-out body: deliver `payload` to each subscription,
        audit the outcome (with the target `device` — the on-device analog of
        the route decision), and prune the dead ones. Runs off the watcher
        thread; never raises."""
        for sub in subs:
            try:
                res = webpush.send(sub, payload)
            except Exception:
                A.error("", "dashboard webpush send",
                        {"sid": payload.get("sid")})
                continue
            ep = sub.get("endpoint", "") if isinstance(sub, dict) else ""
            dev = sub.get("device") if isinstance(sub, dict) else None
            if res.gone:
                prefs.remove_push_subscription(ep)
            A.state_file("", "", "web-push",
                         {"sid": payload.get("sid"), "kind": payload.get("kind"),
                          "action": "send", "status": res.status,
                          "ok": res.ok, "gone": res.gone,
                          "badge": payload.get("badge"),
                          "device": dev, "endpoint": ep[:80]})

    def run(self):
        n = 0
        while True:
            try:
                if n % SLOW_EVERY == 0:
                    self.refresh_winmap()
                self.scan()
            except Exception:
                A.error("", "dashboard notifier")
                time.sleep(5)              # a broken poll must not spin-audit
            n += 1
            time.sleep(GLOBAL_TICK_S)


NOTIFIER = Notifier()


# The read-side presentation model lives in dashboard/read/ (lists / session /
# mirror, over meta + cache). server.py (the HTTP layer) re-exports the payload
# builders it serves plus the few the control-plane POSTs and the tests reach.
from dashboard.read import lists, mirror, session  # noqa: F401  -- module handles for tests
from dashboard.read.lists import (  # noqa: F401  -- facade re-export
    accounts_payload, dir_live_sessions, resumable_payload, sessions_payload,
    stats_payload, _row_key, _wire_row,
)
from dashboard.read.meta import (  # noqa: F401  -- facade re-export
    canon_cwd, git_info, session_ctx, session_goal, session_title, _group_dir,
    _session_slug,
)
from dashboard.read.session import (  # noqa: F401  -- facade re-export
    agents_ctx, agents_model_effort, session_payload, visible_agents,
    _ask_draft, _ask_pending, _ask_wire, _chip_delivered, _composer_draft,
    _composer_queue, _delivered_prompts, _dialog_pending, _last_prompt,
    _plan_pending, _session_tasks, _stamp_agent_cost, _suggestion, _SUGGEST_TABS,
)
from dashboard.read.mirror import (  # noqa: F401  -- facade re-export
    history, merged_backlog, note_payload, ops_payload, view_payload,
    HISTORY_BLOCKS, _conv_items, _enrich_entries, _heal_stash, _mdify,
)


# The terminal-facing control machinery lives in dashboard/control/launch.py;
# the control-plane validation constants moved to config.py. Callers reach the
# frontend/live-window resolvers MODULE-QUALIFIED (launch._frontend /
# launch._live_windows) so a test patches the one owning module.
from dashboard.control import launch  # noqa: F401
from dashboard.control.launch import (  # noqa: F401  -- facade re-export
    launch_argv, _clear_clipboard_image, _clip_has_image, _front_app,
    _launch_wake, _steal_watch, _within_live_grace,
)
from dashboard.config import (  # noqa: F401  -- control-plane validation vocabulary
    EFFORTS, RENAME_MAX, _MODEL_ARG_OK, _MODEL_OK, _NAME_CTRL,
)

# --- the HTTP handler ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "claude-dash"

    def log_message(self, *a):              # stdlib logs to stderr — DEVNULL'd
        pass                                # anyway under spawn_detached

    # -- plumbing --
    def _accepts_gzip(self):
        # honour an explicit `gzip;q=0` refusal; otherwise any gzip token wins.
        for tok in self.headers.get("Accept-Encoding", "").lower().split(","):
            tok = tok.strip()
            if tok == "gzip" or tok.startswith("gzip;"):
                return "q=0" not in tok or "q=0." in tok
        return False

    def _send(self, code, body, ctype="application/json"):
        # Everything routed through _send is text (JSON/HTML/CSS/JS/plain), so
        # it all compresses; SSE never comes here (it holds the response open
        # and writes incremental frames, which buffering would break). Vary is
        # set whenever the body could vary by encoding, even when this response
        # stays plain, so a shared cache keys the two variants apart.
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Vary", "Accept-Encoding")
        if len(data) >= GZIP_MIN and self._accepts_gzip():
            data = gzip.compress(data)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass                            # client went away mid-write

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _sse(self, event, obj):
        """One SSE frame; False when the client is gone (ends the loop)."""
        try:
            self.wfile.write(("event: %s\ndata: %s\n\n"
                              % (event, json.dumps(obj, default=str))).encode())
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _sse_beat(self):
        try:
            self.wfile.write(b": beat\n\n")
            self.wfile.flush()
            return True
        except OSError:
            return False

    # -- routing --
    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            self.route(url, parts)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # client disconnect is not an error
        except Exception:
            A.error("", "dashboard request", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    def route(self, url, parts):
        if not parts:
            return self.static("index.html")
        if parts[0] == "static" and len(parts) == 2:
            return self.static(parts[1])
        if parts == ["sw.js"]:
            # the push service worker, served at the root so its scope is the
            # whole origin (docs/dashboard.md *Web push*) — not under /static/,
            # which would scope it to /static/.
            return self.static("sw.js")
        if parts[0] == "events":
            if len(parts) == 1:
                return self.sse_global()
            if len(parts) == 3 and parts[1] == "session" and _sid(parts[2]):
                return self.sse_session(parts[2], _qint(url, "after"),
                                        _qint(url, "mpos"))
            if len(parts) == 4 and parts[1] == "agent" \
                    and _sid(parts[2]) and _sid(parts[3]):
                return self.sse_agent(parts[2], parts[3], _qint(url, "pos"))
            return self._json({"error": "not found"}, 404)
        if parts[0] != "api":
            return self._json({"error": "not found"}, 404)
        api = parts[1:]
        if api == ["sessions"]:
            return self._json([_wire_row(r) for r in sessions_payload()])
        if api == ["accounts"]:
            return self._json(accounts_payload())
        if api == ["stats"]:
            # the GitHub-Insights-style Stats page: cross-session heatmap /
            # pulse / punch card / per-project cards, all server-computed +
            # memo-cached (stats_payload). Read-only, no audit rows.
            return self._json(stats_payload())
        if api == ["dictate"]:
            # feature probe: the page renders mic buttons iff a Deepgram key
            # is configured (docs/dashboard.md *Web dictation*) — no key
            # means the feature is invisible, never a dead button
            return self._json({"available": dictate.available()})
        if api == ["commands"]:
            # the "/" menus (composer + new-session prompt): built-ins + the
            # given directory's discovered .claude commands/skills. cwd-keyed,
            # not sid-keyed — the new-session form completes for a directory
            # that has no session yet; a non-directory degrades to built-ins
            # + user-level entries, never an error.
            cwd = (parse_qs(url.query).get("cwd") or [""])[0]
            if not os.path.isdir(cwd):
                cwd = ""
            return self._json(plugins.slash_commands(cwd))
        if api == ["ns-prefs"]:
            # the new-session form's last-used {cwd, model, effort} — moved off
            # per-browser localStorage into the durable global prefs store so a
            # launch on one device pre-selects on the next (docs/dashboard.md,
            # *New-session prefs*). {} when nothing launched yet.
            return self._json(prefs.get("new-session", {}))
        if api == ["resumable"]:
            # the new-session resume picker's rows for one directory (fetched on
            # open, dir change, and search): recent sessions in `cwd`, each with
            # the model/effort/account it ran under; `q` searches the directory's
            # whole history (docs/dashboard.md *Resume picker*). `limit` clamped to
            # [1, RESUMABLE_MAX]; a blank/unknown dir yields []. Read-only.
            cwd = _qstr(url, "cwd")
            limit = min(RESUMABLE_MAX, max(1, _qint(url, "limit") or RESUMABLE_MAX))
            return self._json(resumable_payload(cwd, limit, _qstr(url, "q")))
        if api == ["push", "config"]:
            # the Web Push feature probe (docs/dashboard.md *Web push*): the page
            # offers the notification opt-in + subscribes only when push is
            # possible AND has an application-server key. `enabled` false (no
            # crypto backend / no key) keeps the feature invisible, never a dead
            # button. The public key is not a secret.
            key = webpush.public_key()
            return self._json({"enabled": bool(webpush.enabled() and key),
                               "key": key})
        if api == ["dirs", "hidden"]:
            # the {group_key: hidden_at_epoch} map the ✕ built (docs/dashboard.md
            # *Hidden directories*); the page seeds S.hidden from this on load —
            # the SSE snapshot carries the session ROWS, not this pref, and only
            # the browser that clicks ✕ mutates it, so no SSE push is needed.
            return self._json(prefs.hidden_dirs())
        if len(api) >= 2 and api[0] == "session" and _sid(api[1]):
            sid, rest = api[1], api[2:]
            if not rest:
                return self._json(session_payload(sid))
            if rest == ["ops"]:
                last, items = ops_payload(sid, _qint(url, "after"))
                return self._json({"last": last, "items": items})
            if rest == ["history"]:
                row = API.session_row(sid)
                key = P.sid_from_log(row["log"]) if row else sid
                # clamp blocks POSITIVE: a negative ?blocks makes _cut_blocks
                # return len(entries), and _snap then indexes entries[len] →
                # IndexError → 500 (crafted-request crash).
                oldest, items = history(sid, key, _qint(url, "before"),
                                        max(1, _qint(url, "blocks")
                                            or HISTORY_BLOCKS))
                return self._json({"oldest": oldest, "items": items})
            if rest == ["backlog"]:
                # The GET twin of the SSE fresh-connect backlog: same
                # merged_backlog output, but through _send — which GZIPS it
                # (8-9x on this HTML; SSE frames are never compressed), so a
                # remote/tunnel page gets its first paint in one compressed
                # round-trip. The page hands the returned cursors to the SSE,
                # which then only streams increments (the reconnect contract).
                row = API.session_row(sid)
                key = P.sid_from_log(row["log"]) if row else sid
                last, mpos, oldest, items = merged_backlog(sid, key)
                return self._json({"last": last, "mpos": mpos,
                                   "oldest": oldest, "items": items})
            if rest == ["activity"]:
                return self._json(_mdify(plugins.activity(sid)) or {"entries": []})
            if len(rest) == 2 and rest[0] == "agent":
                tl = _mdify(plugins.activity(sid, rest[1]))
                if tl is not None:
                    _stamp_agent_cost(tl)
                return self._json(tl if tl is not None else {"entries": []})
            if rest == ["errors"]:
                return self._json(API.errors(sid))
            if rest == ["monitors"]:
                return self._json({"monitors": plugins.monitors(sid) or []})
            if rest == ["jobs"]:
                return self._json({"jobs": API.jobs(sid)})
            if rest == ["memory"]:
                return self._json({"memory": API.memory(sid)})
            if rest == ["note"]:
                return self._json(note_payload(_qstr(url, "path"),
                                               _qstr(url, "stem")))
            if len(rest) == 2 and rest[0] == "view":
                return self.get_view(sid, rest[1])
            if len(rest) == 3 and rest[0] == "copy" \
                    and rest[2] in ("cmd", "out", "all"):
                return self.get_copy(sid, rest[1], rest[2])
        return self._json({"error": "not found"}, 404)

    def get_copy(self, sid, gid, what):
        """Serve a mirror block's ⧉ copy text — the WEB twin of the terminal's
        ⧉ link (core.copy.main / claude-copy.py). READ-ONLY: a mode=ro ops scan
        via core.copy.collect, no clipboard, no pane feedback (the browser copies
        client-side). Audited as a `web-copy` state_files row (gid/what/chars —
        chars 0 = the group held nothing of that type), because the dashboard
        calls collect() DIRECTLY and so bypasses every audit row main() writes:
        a web copy must be as reconstructible as the terminal `copy` row. A gone
        session DB or a read failure lands an A.error, mirroring main()'s
        `copy (state DB gone …)` / `copy (read ops)` paths. Always 200 with the
        text (empty on any miss — the same silent no-op the terminal shows)."""
        row = API.session_row(sid)
        log = (row.get("log") if row else "") or P.mirror_log(sid)
        sdb = API.state_db_for(sid)
        if not sdb:
            A.error(log, "dashboard copy (state DB gone)",
                    {"gid": gid, "what": what})
            return self._send(200, "", "text/plain; charset=utf-8")
        try:
            text = CP.collect(sdb, gid, what)
        except Exception:
            A.error(log, "dashboard copy (read ops)", {"gid": gid, "what": what})
            return self._send(200, "", "text/plain; charset=utf-8")
        A.state_file(log, sdb, "web-copy",
                     {"gid": gid, "what": what, "chars": len(text)})
        return self._send(200, text, "text/plain; charset=utf-8")

    def get_view(self, sid, gid):
        """Serve a click-to-view stash rendered to HTML — the WEB twin of the
        terminal's ⧉view toggle (which audits a `view` row on every click).
        READ-ONLY (collapse is client-side, so this fires once per EXPAND).
        Audited as a `web-view` state_files row (gid/ok) so the web expand
        isn't a blind spot: `ok:false` = no stash (pre-feature line / failed
        stash write) → 404, the same no-op the terminal shows."""
        row = API.session_row(sid)
        log = (row.get("log") if row else "") or P.mirror_log(sid)
        sdb = API.state_db_for(sid)
        html = view_payload(sid, gid)
        A.state_file(log, sdb or "", "web-view",
                     {"gid": gid, "ok": html is not None})
        if html is None:
            return self._json({"error": "no stash"}, 404)
        return self._send(200, html, "text/html; charset=utf-8")

    # -- POST routing (the control plane) --
    # The dashboard is READ-ONLY except these control-plane writes, which TYPE INTO a
    # terminal — a drive-by RCE if a random website could fire them. Any page
    # can send a *simple* cross-origin POST at 127.0.0.1 (no preflight), so the
    # defense is to make these NON-simple: require a JSON content type AND a
    # custom header (each forces a CORS preflight that a cross-origin page can't
    # pass, since we answer OPTIONS with a bare 501 — no Access-Control-Allow-*),
    # and additionally reject any Origin that isn't our own. See docs/dashboard.md.
    def do_POST(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            self.route_post(url, parts)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            A.error("", "dashboard POST", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    def route_post(self, url, parts):
        api = parts[1:] if parts[:1] == ["api"] else None
        if api is None:
            return self._json({"error": "not found"}, 404)
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "message":
            return self.post_message(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "command":
            return self.post_command(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "stop":
            return self.post_stop(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "interrupt":
            return self.post_interrupt(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "rename":
            return self.post_rename(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "migrate":
            return self.post_migrate(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "rewind":
            return self.post_rewind(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "rewind-to":
            return self.post_rewind_to(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "answer":
            return self.post_answer(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "ask-draft":
            return self.post_ask_draft(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "composer-draft":
            return self.post_composer_draft(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "composer-queue":
            return self.post_composer_queue(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "hint-audit":
            return self.post_hint_audit(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "client-fail":
            return self.post_client_fail(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "plan-options":
            return self.post_plan_options(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "plan-decision":
            return self.post_plan_decision(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "notify":
            return self.post_notify_mute(api[1])
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] == "viewing":
            return self.post_viewing(api[1])
        if api == ["presence"]:
            return self.post_presence()
        if api == ["upload"]:
            return self.post_upload()
        if api == ["sessions", "new"]:
            return self.post_new_session()
        if api == ["ns-prefs"]:
            return self.post_ns_prefs()
        if api == ["dirs", "hide"]:
            return self.post_hide_dir()
        if api == ["dictate", "token"]:
            return self.post_dictate_token()
        if api == ["push", "subscribe"]:
            return self.post_push_subscribe()
        if api == ["push", "unsubscribe"]:
            return self.post_push_unsubscribe()
        if api == ["clientlog"]:
            return self.post_client_log()
        return self._json({"error": "not found"}, 404)

    def _reject(self, code, err):
        """A guard rejection: close the connection (an unread body would desync
        a kept-alive connection) and send the JSON error. Returns None (implicit)
        so the caller can `return self._reject(...)` straight out of _post_guard.

        Audited as a `web-reject` state_files row (path = the rejected request
        path, content = code + reason). This is the ONE place a control-plane
        POST could vanish without a trace: _post_guard rejects BEFORE any
        handler runs, so a browser POST that arrives but fails the guard (a
        missing X-Claude-Dash header, a cross-origin Origin, read-only mode) wrote
        nothing — the `/stop that produced a client `web-hint op=close` beacon
        yet no `web-stop` row` blind spot. Audit-only telemetry (not an `errors`
        row — an expected 4xx, same rationale as _reject_input), so it never
        lights the errwatch chip."""
        A.state_file("", self.path[:200], "web-reject",
                     {"code": code, "why": err})
        self.close_connection = True
        self._json({"error": err}, code)

    def _post_guard(self, max_bytes=POST_MAX):
        """Validate a control-plane POST against the browser-vector defense
        (see do_POST) and return its parsed JSON body — or send a 4xx and return
        None (the caller just returns). Order: read-only kill switch, content
        type, custom header, Origin, size cap, then the JSON parse.

        `max_bytes` overrides the default POST_MAX cap — the upload endpoint
        raises it to UPLOAD_MAX to admit a base64-inflated image (every other
        caller stays at the tiny control-plane default).

        Two accepted proofs of a same-origin caller, EITHER suffices:
          * the `X-Claude-Dash` custom header (a cross-origin *simple* POST can't
            set it, and a cross-origin fetch that tries triggers a preflight this
            no-CORS server never answers), OR
          * a present-and-allowlisted `Origin` — because `navigator.sendBeacon`
            CANNOT set a custom header, yet the frontend-audit flush on `pagehide`
            rides sendBeacon (a last-ditch delivery as the tab goes away —
            `flushClog`, docs/dashboard.md *Frontend audit (clientlog)*). (The
            close itself no longer needs this branch: routing it through
            sendBeacon REGRESSED it — queued-then-silently-dropped by the tunnel —
            so it is back on the plain-`fetch` channel that carries the header,
            docs/dashboard.md *Close via the plain-fetch channel*.) A cross-origin
            page cannot forge an allowlisted Origin, and the browser stamps the
            *real* Origin on every cross-origin request, so the Origin allowlist IS
            the CSRF gate here; the header was only ever defence-in-depth. A
            non-allowlisted Origin is still the attack signal and is always
            rejected."""
        if READONLY:
            return self._reject(403, "control plane disabled (read-only)")
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/json":
            return self._reject(415, "content-type must be application/json")
        origin = self.headers.get("Origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return self._reject(403, "cross-origin")
        # header OR allowlisted Origin — the pagehide clientlog sendBeacon carries
        # no header but a real allowlisted Origin (a cross-origin caller can forge
        # neither).
        if self.headers.get(POST_HEADER) != "1" and origin not in ALLOWED_ORIGINS:
            return self._reject(403, "missing %s header" % POST_HEADER)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > max_bytes:
            return self._reject(413, "body too large")
        try:
            raw = self.rfile.read(n) if n else b""
            body = json.loads(raw or b"{}")
        except (ValueError, OSError):
            return self._reject(400, "invalid JSON")
        if not isinstance(body, dict):
            return self._reject(400, "invalid JSON")
        return body

    def post_upload(self):
        """Stage a composer ATTACHMENT (an image/screenshot the browser pasted,
        dropped, or picked, or any other file) on disk, and hand back the
        ABSOLUTE path the composer will inject as an `@path` mention (post_message
        / post_new_session). Body: {sid?, name, mime, data(base64)}.

        Transport is JSON+base64, NOT multipart: it keeps the whole _post_guard
        browser-vector defense (same-origin + custom header + read-only switch)
        with no boundary parser to write; the price is a base64 envelope, which
        UPLOAD_MAX budgets for. The bytes land under paths.UPLOADS_DIR (durable
        ~/.claude, OUTSIDE any repo working tree so `git status` stays clean),
        in a per-session subdir; this mkdir is a sanctioned control-plane write
        (gated by _post_guard/READONLY like every other mutating POST).

        docs/dashboard.md, *Web attachments*."""
        body = self._post_guard(UPLOAD_MAX)
        if body is None:
            return
        sid = body.get("sid")
        sid = sid if isinstance(sid, str) and _sid(sid) else ""
        # A rejected upload files its row under the uploader's session when we
        # have a sid, else the global stream (a log-less staging upload).
        ulog = P.mirror_log(sid) if sid else ""
        name = body.get("name")
        mime = body.get("mime") or ""
        data_b64 = body.get("data")
        if not isinstance(name, str) or not isinstance(data_b64, str) \
                or not isinstance(mime, str):
            return self._reject_input(
                "web-upload", "bad fields", "name, mime, data required",
                {"name": name, "mime": mime}, log=ulog)
        # basename only — strip any path component a hostile name carries, and
        # fall back to a neutral stem so an empty/dotfile name can't produce a
        # bare-uuid or hidden file.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name)).lstrip(".")
        safe = safe[:80] or "attachment"
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            return self._reject_input("web-upload", "bad base64",
                                      "invalid base64", {"name": safe}, log=ulog)
        if not raw:
            return self._reject_input("web-upload", "empty file", "empty file",
                                      {"name": safe}, log=ulog)
        if len(raw) > UPLOAD_MAX:
            return self._reject_input("web-upload", "too large",
                                      "file too large", {"bytes": len(raw)},
                                      code=413, log=ulog)
        is_image = mime in IMAGE_MIMES
        # audit target: the uploader's session log if we have a sid, else the
        # shared staging bucket keyed on the log-less "staging" slug.
        row = API.session_row(sid) or {} if sid else {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        dest_dir = P.session_uploads_dir(sid)
        path = os.path.join(dest_dir, "%s-%s" % (uuid.uuid4().hex[:8], safe))
        try:
            os.makedirs(dest_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
        except OSError as e:
            A.error(log, "dashboard upload (write failed)",
                    {"sid": sid, "name": safe, "err": str(e)})
            A.state_file(log, sdb, "web-upload",
                         {"sid": sid, "name": safe, "bytes": len(raw),
                          "ok": False})
            return self._json({"error": "could not store upload"}, 500)
        A.state_file(log, sdb, "web-upload",
                     {"sid": sid, "name": safe, "bytes": len(raw),
                      "mime": mime, "ok": True})
        return self._json({"ok": True, "path": path, "name": safe,
                           "mime": mime, "is_image": is_image})

    def _attachment_paths(self, body):
        """The vetted `@path` prefix for the delivered message text, from a POST
        body's optional `attachments` list. Each entry MUST be an absolute path
        under paths.UPLOADS_DIR (so a caller can't smuggle an arbitrary
        filesystem path into an `@`-mention) and exist on disk. Returns a list of
        absolute paths (possibly empty); silently drops anything that fails the
        checks — the message still goes, just without the bad attachment."""
        raw = body.get("attachments")
        if not isinstance(raw, list):
            return []
        root = os.path.realpath(P.UPLOADS_DIR) + os.sep
        out = []
        for p in raw:
            if not isinstance(p, str) or not p:
                continue
            rp = os.path.realpath(p)
            if (rp + os.sep).startswith(root) and os.path.isfile(rp):
                out.append(rp)
        return out

    def _with_attachments(self, text, paths):
        """Prepend `@path` mention tokens (one per attachment) to the message
        text — the TUI-native way to attach a file, delivered verbatim over the
        existing paste_text / launch-argv transport. Paths first, then a newline,
        then the typed text (mirrors the TUI's paste-then-type order). No text is
        fine: the mentions alone are a valid message."""
        if not paths:
            return text
        mentions = " ".join("@" + p for p in paths)
        return mentions + ("\n" + text if text else "")

    def post_message(self, sid):
        """Type a message into a session's kitty window (the composer). 4xx when
        the session has no window (headless/daemon) or the text is empty; 503
        when no terminal resolves; else Frontend.send_text. Every attempt is a
        `web-send` state_files row, failures also an A.error.

        `clear_draft` (bool): the page sets it when resending an edited
        message after a mid-turn cancel-edit — the TUI input still holds the
        restored draft, so the send first kills the line (Ctrl+U to start +
        Ctrl+K to end, so the cursor position doesn't matter) and then
        delivers the text as a BRACKETED PASTE (paste_text): a raw send into
        the just-cleared input drops leading bytes (measured — the mangle),
        an atomic paste doesn't. This is what lets you edit AND resend from
        the web without touching the kitty tab (docs/dashboard.md)."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if text is None:
            text = ""
        if not isinstance(text, str):
            return self._reject_input("web-send", "bad text", "empty text",
                                      {"type": type(text).__name__},
                                      log=P.mirror_log(sid))
        # attachments (vetted @-paths) may stand in for text — a screenshot with
        # no words is a valid message; an empty message with neither is not.
        attachments = self._attachment_paths(body)
        if not text.strip() and not attachments:
            return self._reject_input("web-send", "empty text", "empty text",
                                      {"chars": len(text)}, log=P.mirror_log(sid))
        text = self._with_attachments(text, attachments)
        clear_draft = bool(body.get("clear_draft"))
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard message (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-send",
                         {"win": "", "chars": len(text), "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the pane currently tagged claude_session=<sid>,
        # NOT the audit row's stale start-time id (typing into a reused id would
        # land in an unrelated tab — see _live_windows; a fresh scan, never the
        # TTL memo). '' ⇒ nothing to message.
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-send",
                         {"win": "", "chars": len(text), "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        # a message pasted while a MODAL dialog (AskUserQuestion / ExitPlanMode)
        # is up goes INTO the dialog, not the TUI message queue — it perturbs
        # the dialog and the text is lost (the "my queued message vanished mid
        # ask" report, 2026-07-19). Refuse with a clear pointer to the card; the
        # composer keeps its text (the page re-persists the draft on error).
        if _ask_pending(sid) or _plan_pending(sid):
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(text), "ok": False,
                          "blocked": "modal"})
            return self._json({"error": "this session has an open question — "
                               "answer it in the card above (or dismiss it) "
                               "before sending", "modal": True}, 409)
        # the tab state AT SEND TIME decides whether this send starts a turn
        # or lands in the TUI's message queue (QUEUE_TABS); it rides the audit
        # row too — "my message vanished" is answerable as "it queued mid-turn"
        tab = API.tab_states().get(win) or ""
        if clear_draft:
            # kill the restored draft (both directions), settle, then paste
            fe.send_key(win, "ctrl+u")
            fe.send_key(win, "ctrl+k")
            time.sleep(DRAFT_CLEAR_GAP_S)
        # ALWAYS a bracketed paste, not a raw send: a raw send is delivered as
        # fast individual keystrokes and the TUI drops some depending on its
        # input state (reported live: "test" arrived as "t"; measured 8/8
        # clean for a bracketed paste, flaky for raw). The trailing CR is a
        # separate keystroke OUTSIDE the paste, so it still submits — and a
        # multi-line composer message pastes atomically instead of its internal
        # newlines submitting it early.
        # empty an IMAGE clipboard first — a bracketed paste makes Claude Code
        # attach whatever image is on the board (docs/dashboard.md *Clipboard-
        # image guard*); no-op on a text clipboard / off macOS.
        clip = _clear_clipboard_image()
        ok = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, "web-send",
                     {"win": win, "chars": len(text), "ok": ok, "tab": tab,
                      "clear_draft": clear_draft, "attachments": len(attachments),
                      "clip": clip})
        if not ok:
            A.error(log, "dashboard message (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "queued": tab in QUEUE_TABS, "tab": tab})

    def post_command(self, sid):
        """The scoreboard's quick-command row — type one of the TUI's OWN
        slash commands into the session's window: `{"cmd": "compact"}` →
        `/compact`, `{"cmd": "model", "arg": <alias|id>}` → `/model <arg>`,
        `{"cmd": "effort", "arg": <level>}` → `/effort <arg>` (both may open
        the TUI's switch-confirm menu, auto-answered Yes below — the reply's
        `confirm` field). A FIXED
        vocabulary, 400 on anything else — the arg is validated
        (_MODEL_ARG_OK / EFFORTS) precisely because it is typed into a
        terminal, and compact takes no arg (the closed vocabulary IS the
        point; free-form text is the composer's job). Delivery matches
        post_message (bracketed paste + CR via the live claude_session
        window), so mid-turn the command lands in the TUI's message queue and
        runs at the turn boundary (`queued` in the reply) — but a RED tab
        (awaiting-command: a modal dialog is up) is a 409: pasted text would
        land IN the dialog, its digits deciding it. Every attempt is a
        `web-command` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        cmd, arg = body.get("cmd"), body.get("arg")
        if cmd == "compact" and not arg:
            text = "/compact"
        elif cmd == "model" and isinstance(arg, str) \
                and _MODEL_ARG_OK.match(arg):
            text = "/model " + arg
        elif cmd == "effort" and arg in EFFORTS:
            text = "/effort " + arg
        else:
            return self._reject_input("web-command", "bad cmd", "unknown command",
                                {"sid": sid, "cmd": cmd, "arg": arg})
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard command (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-command",
                         {"win": "", "cmd": cmd, "arg": arg or "",
                          "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the live claude_session=<sid> pane tag, same
        # as post_message (a reused stale id would type into an unrelated tab)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-command",
                         {"win": "", "cmd": cmd, "arg": arg or "",
                          "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if tab == tabs.AWAITING_COMMAND:
            A.state_file(log, sdb, "web-command",
                         {"win": win, "cmd": cmd, "arg": arg or "",
                          "ok": False, "tab": tab})
            return self._json({"error": "a dialog is open — answer it first"},
                              409)
        clip = _clear_clipboard_image()      # don't let a clipboard image ride
        ok = bool(fe.paste_text(win, text))  # the slash command (see post_message)
        A.state_file(log, sdb, "web-command",
                     {"win": win, "cmd": cmd, "arg": arg or "", "ok": ok,
                      "tab": tab, "clip": clip})
        if not ok:
            A.error(log, "dashboard command (send failed)",
                    {"sid": sid, "win": win, "cmd": cmd})
            return self._json({"error": "send failed"}, 502)
        res = {"ok": True, "queued": tab in QUEUE_TABS, "tab": tab}
        if cmd in ("model", "effort") and tab not in QUEUE_TABS:
            # newer TUI builds interpose a Yes/No switch-confirm menu (the
            # prompt-cache warning) instead of applying outright — unanswered
            # it makes the click look dead, so press its own Yes (the button
            # IS the consent), screen-verified: dashboard/confirmdialog.py.
            # Mid-turn (queued) the command only runs at the turn boundary,
            # so there is no menu to wait for here — an unanswered late menu
            # surfaces as the red-tab notification.
            try:
                c = confirmdialog.confirm(fe, win)
                res["confirm"] = "confirmed" if c["dialog"] else "none"
            except Exception as e:      # ConfirmError or a frontend hiccup —
                # the menu (if any) is left open for the terminal user
                A.error(log, "dashboard command (confirm failed)",
                        {"sid": sid, "win": win, "cmd": cmd, "err": str(e)})
                res["confirm"] = "failed"
            A.state_file(log, sdb, "web-command-confirm",
                         {"win": win, "cmd": cmd,
                          "confirm": res["confirm"]})
        return self._json(res)

    def post_stop(self, sid):
        """Close a session's kitty tab (Frontend.close_tab — main window +
        mirror + scorebar at once). This is a GRACEFUL stop, not a kill: kitty
        HUPs the tab's processes, Claude Code exits and fires SessionEnd, and
        the normal end-of-session lifecycle (mirror park, audit close) runs on
        its own — verified empirically 2026-07-18 (docs/dashboard.md). 409
        when the session has no window (headless — nothing to close); 503
        when no terminal resolves.

        Every attempt is a `web-stop` state_files row carrying a `phase`:
        `attempt` is written BEFORE the (potentially blocking) close_tab, `done`
        after with the `ok` outcome — a lone `attempt` with no paired `done` is
        the stuck-close signal (close_tab hung on an unbounded kitten socket
        connect, or the thread never returned), which the client only shows as a
        `web-hint op=close … stale` beacon. The early no-terminal / no-window
        rejections are terminal `done` rows. Failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard stop (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-stop",
                         {"win": "", "phase": "done", "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the pane currently tagged claude_session=<sid>,
        # NOT the audit row's stale start-time id. Closing by a reused stale id
        # would close an UNRELATED live tab — the exact bug this fixes (a leaked
        # smoke-test session's reused window id closed the user's own tab).
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-stop",
                         {"win": "", "phase": "done", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        # Audit the ATTEMPT before close_tab: a kitten close that HANGS (an
        # unbounded socket connect) or otherwise never returns must not vanish
        # from the audit — the `web-stop attempt` is the only trace the server
        # ever tried. Same "row before the risky op" discipline as
        # stream_start/stream_end (an attempt with no `done` == the anomaly).
        A.state_file(log, sdb, "web-stop", {"win": win, "phase": "attempt"})
        ok = bool(fe.close_tab(win))
        A.state_file(log, sdb, "web-stop", {"win": win, "phase": "done", "ok": ok})
        if not ok:
            A.error(log, "dashboard stop (close failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "close failed"}, 502)
        return self._json({"ok": True})

    def post_interrupt(self, sid):
        """Press Escape in a session's kitty window (Frontend.send_key) — the
        TUI's own interrupt: stops the current turn in place, the session
        stays up. Distinct from post_stop, which closes the whole tab. Key
        EVENTS, not send_text bytes — a raw \\x1b never reaches a TUI in the
        kitty keyboard protocol as Escape. 409 when the session has no window
        (headless — nothing to interrupt); 503 when no terminal resolves.
        Every attempt is a `web-interrupt` state_files row, failures also an
        A.error."""
        return self._escape_press(sid, "interrupt", "web-interrupt")

    def post_rename(self, sid):
        """Rename a session: append the `agent-name` naming record to its
        transcript (plugins.set_session_title — the /rename channel, docs/
        session-naming-findings.md) and, when a live window exists, also
        Frontend.set_tab_title so the kitty tab moves NOW (sticky — the tab
        stops following auto ai-titles; docs/dashboard.md *Web rename*).
        DELIBERATELY unlike post_message, no terminal / no window is NOT an
        error here — a parked session (or a dashboard outside kitty) still
        gets the JSONL rename and only the tab retitle degrades. Always
        appends, even mid-turn (a single atomic O_APPEND line — the tab state
        rides the audit row so a race is diagnosable). Every post-validation
        attempt is a `web-rename` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        name = body.get("name")
        if not isinstance(name, str):
            return self._reject_input("web-rename", "bad name", "empty name",
                                      {"type": type(name).__name__},
                                      log=P.mirror_log(sid))
        name = _NAME_CTRL.sub(" ", name).strip()[:RENAME_MAX].strip()
        if not name:
            return self._reject_input("web-rename", "empty name", "empty name",
                                      {"raw": body.get("name")},
                                      log=P.mirror_log(sid))
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        tpath = row.get("transcript_path") or ""
        if not tpath or not os.path.isfile(tpath):
            A.state_file(log, sdb, "web-rename",
                         {"win": "", "chars": len(name), "ok": False,
                          "reason": "no transcript"})
            return self._json({"error": "no transcript"}, 409)
        fe = launch._frontend()
        win = (fe.window_for_session(sid) or "") if fe else ""
        tab = (API.tab_states().get(win) or "") if win else ""
        try:
            appended = plugins.set_session_title(tpath, name)
        except OSError:
            A.error(log, "dashboard rename (append failed)", {"sid": sid})
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "tab": tab})
            return self._json({"error": "append failed"}, 502)
        if appended is None:        # no plugin owns the file (a codex rollout)
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "reason": "unsupported"})
            return self._json({"error": "unsupported session"}, 409)
        # DURABLE OVERRIDE: the transcript `agent-name` append is the canonical
        # channel (it reaches `claude --resume`), but that single record scrolls
        # out of session_title's 64KB tail-window in a long session and the
        # rename appears to "roll back" to the auto ai-title. Stash a durable,
        # tail-window-proof override keyed by the transcript stem so the DASHBOARD
        # title never reverts (docs/dashboard.md, *Web rename*). Best-effort like
        # every prefs write — a failure just falls back to the transcript read.
        stem = os.path.basename(tpath)
        stem = stem[:-len(".jsonl")] if stem.endswith(".jsonl") else stem
        stored = prefs.set_renamed_title(stem, name)
        override_ok = isinstance(stored, dict) and stored.get(stem) == name
        tab_retitled = bool(fe.set_tab_title(win, name)) if (fe and win) else False
        A.state_file(log, sdb, "web-rename",
                     {"win": win, "chars": len(name), "ok": True, "tab": tab,
                      "tab_retitled": tab_retitled, "override": override_ok})
        return self._json({"ok": True, "title": name,
                           "tab_retitled": tab_retitled})

    def post_migrate(self, sid):
        """Manually migrate a session to another subscription account — the
        header's ⇆ migrate button (docs/relimit.md *Manual migrate*). Spawns
        the SAME detached migrator the automatic rate-limit path uses
        (bin/claude-relimit.py: close the tab → wait for the SessionEnd park
        → `<alias> claude --resume <sid>` in a new tab; the adopt machinery
        carries the mirror history and the status-line capture flips the
        account chip), with two manual-intent differences baked into `mode=
        manual`: no auto-continue nudge (nothing was cut off — the resumed
        session opens at the prompt) and no 90% usage ceiling on the target
        (plugins.migration_target(manual=True) — an explicit click outranks
        the refuge rule). It runs the SAME fable→opus→sonnet downgrade ladder
        the automatic path does (docs/relimit.md *Model-downgrade ladder*):
        same model on another account when one has quota, else a downgrade rung
        passed through to `--model` (the current model is read off the
        transcript via plugins.context). Immediate, no confirm (user request —
        like ■ stop). Works live AND parked: a parked session skips the close
        leg and just relaunches. 404 for a sid this machine has never seen (no
        audit row, no live/parked state DB — the migrator can't tell "parked"
        from "never existed", so an unknown sid would sail through its park
        check and launch a doomed --resume tab; caught live 2026-07-19); 409
        when no account (any rung) qualifies;
        503 when no terminal resolves. Every attempt is a `web-migrate`
        state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        if not (row or os.path.isfile(P.state_db(log))
                or os.path.isfile(P.parked_db(log))):
            A.state_file(log, "", "web-migrate",
                         {"ok": False, "reason": "unknown sid"})
            return self._json({"error": "unknown session"}, 404)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard migrate (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no terminal"})
            return self._json({"error": "no terminal available"}, 503)
        cur = (API.kv_at(sdb, "account") or {}).get("slug") or ""
        # The model the session is running (off its transcript) feeds the
        # downgrade ladder (docs/relimit.md *Model-downgrade ladder*): a manual
        # ⇆ now downgrades too when no account has the current model free.
        cur_model = (plugins.context(row.get("transcript_path") or "")
                     or {}).get("model") or ""
        # Capture the picker's FULL reasoning (per-account rung/eff5h/limit-hit/
        # reject) so a manual-migrate REFUSAL is reconstructible from the DB —
        # the same subtle gap the automatic path closed with `relimit-pick`
        # (docs/relimit.md *Audit trail*); a bare "no target" is undebuggable.
        pick = {}
        target = plugins.migration_target(cur, cur_model, manual=True,
                                          explain=pick)
        if target is None:
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no target", "from": cur,
                          "pick": pick})
            return self._json({"error": "no other account available"}, 409)
        # target["model"] is the downgrade rung (or "" for a same-model migrate);
        # pick_target already resolved same-vs-downgrade, so forward it verbatim.
        proc = SP.spawn_detached(
            os.path.join(P.BIN, "claude-relimit.py"),
            [log, sid, target["slug"], target["alias"],
             row.get("cwd") or "", "manual", target["model"]],
            log, purpose="relimit:%s (web)" % target["slug"])
        ok = proc is not None
        A.state_file(log, sdb, "web-migrate",
                     {"ok": ok, "from": cur, "to": target["slug"],
                      "model": target["model"], "eff": target["eff"],
                      "cwd": row.get("cwd") or "", "pick": pick})
        if not ok:                       # spawn failure already audited by SP
            return self._json({"error": "migrator spawn failed"}, 502)
        return self._json({"ok": True, "to": target["slug"]})

    def post_rewind(self, sid):
        """The double-Esc GESTURE, whose meaning in Claude Code depends on
        session state — mirrored here by the tab state at gesture time:

        MID-TURN (busy tab): double-Esc CANCELS the current work and
        restores the last message into the input for editing (it leaves the
        conversation). Mirrored with TWO Escape key events
        `DOUBLE_ESC_GAP_S` apart — measured 3/3 reliable mid-turn on a live
        session (2026-07-18), unlike the idle rewind-menu detection — plus
        the magenta escape-recheck (the same experiment showed the tab
        stays stuck thinking after the cancel). Editing then happens in the
        kitty tab.

        IDLE: double-Esc opens the rewind/checkpoint menu — mirrored by
        TYPING `/rewind` (documented identical; synthesized double-press
        key events opened the menu only ~2/3 at the best gap while the
        typed command opened it every time). No Escape pressed ⇒ no
        recheck.

        The response's `mode` (`cancel-edit` | `rewind`) tells the page
        which meaning fired; the same field rides the `web-rewind` audit
        row (`{win, ok, tab, mode}`)."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard rewind (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-rewind", {"win": "", "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-rewind", {"win": "", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if self._dialog_open_guard(tab, log, sdb, win, "web-rewind"):
            return
        restored = ""
        if tab in BUSY_TABS:
            mode = "cancel-edit"
            # the message the cancel restores into the input for editing IS
            # the session's last user prompt — read it BEFORE the Escapes so
            # the page can prefill its composer + drop the cancelled bubble
            restored = _last_prompt(sid)
            tpath = row.get("transcript_path") or ""
            try:
                tsize = os.path.getsize(tpath) if tpath else -1
            except OSError:
                tsize = -1
            ok = bool(fe.send_key(win, "escape"))
            time.sleep(DOUBLE_ESC_GAP_S)
            ok = bool(fe.send_key(win, "escape")) and ok
            if ok and tab in (tabs.THINKING, tabs.WORKING):
                self._spawn_escape_recheck(fe, win, log, tpath, tsize)
        else:
            mode = "rewind"
            ok = bool(fe.send_text(win, "/rewind"))
        A.state_file(log, sdb, "web-rewind",
                     {"win": win, "ok": ok, "tab": tab, "mode": mode})
        if not ok:
            A.error(log, "dashboard rewind (send failed)",
                    {"sid": sid, "win": win, "mode": mode})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "tab": tab, "mode": mode,
                           "restored": restored})

    def post_rewind_to(self, sid):
        """FULL web rewind — restore the session to the checkpoint of a
        SPECIFIC prompt without touching the kitty tab (docs/dashboard.md,
        *Web rewind*): drives Claude Code's own rewind menu in the session's
        window via dashboard/rewindmenu.drive (typed `/rewind`, screen-
        verified navigation, digit resolved from the parsed option labels).

        Body: `text` — the target prompt's full text (menu entries are its
        first line, truncation-aware); `mode` — "conversation" | "both" |
        "code" (rewindmenu.MODE_LABELS); `ups` — the target's `up`-press
        distance from the menu's "(current)" cursor start (newer prompts
        + 1), a jump hint the text-verify scan corrects.

        409 when the tab is BUSY (mid-turn the double-Esc gesture means
        cancel, not rewind — and a typed `/rewind` would just queue as a
        message) or when the step didn't verify (MenuError — menus already
        closed; `step` says which). The response's `restored` echoes `text`
        for conversation restores — Claude Code puts the rewound prompt back
        into the TUI input, so the page prefills its composer and resends
        with clear_draft, the cancel-edit contract. Every attempt is a
        `web-rewind-to` state_files row carrying mode/ups/steps/digit (or
        the failing step), failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        mode = body.get("mode") or "conversation"
        if not isinstance(text, str) or not text.strip():
            return self._reject_input("web-rewind-to", "empty text",
                                      "empty text",
                                      {"type": type(text).__name__},
                                      log=P.mirror_log(sid))
        if mode not in rewindmenu.MODE_LABELS:
            return self._reject_input("web-rewind-to", "bad mode", "bad mode",
                                      {"mode": mode}, log=P.mirror_log(sid))
        try:
            ups = max(0, int(body.get("ups") or 0))
        except (TypeError, ValueError):
            ups = 0
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard rewind-to (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": "", "ok": False, "mode": mode})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": "", "ok": False, "mode": mode})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if self._dialog_open_guard(tab, log, sdb, win, "web-rewind-to"):
            return
        if tab in BUSY_TABS:
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "step": "busy"})
            return self._json(
                {"error": "session is busy — stop or cancel it first",
                 "tab": tab}, 409)
        try:
            res = rewindmenu.drive(fe, win, text, mode, ups=ups)
        except rewindmenu.MenuError as e:
            A.error(log, "dashboard rewind-to (%s)" % e.step,
                    {"sid": sid, "win": win, "mode": mode, "detail": str(e)})
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "ups": ups, "step": e.step})
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-rewind-to",
                     {"win": win, "ok": True, "tab": tab, "mode": mode,
                      "ups": ups, "steps": res["steps"],
                      "digit": res["digit"], "degraded": res["degraded"]})
        restored = text if mode in ("conversation", "both") else ""
        return self._json({"ok": True, "mode": mode, "restored": restored,
                           "degraded": res["degraded"]})

    def post_ask_draft(self, sid):
        """Persist the UNSUBMITTED ask selections (the ask card's in-progress
        answers) to the `ask-draft` kv so another device — or the same one
        after a reload — restores them when it reopens the session (docs/
        dashboard.md, *Web ask*). This types NOTHING into the terminal: it is
        a pure state write, distinct from post_answer (which drives the real
        dialog). The session SSE re-broadcasts the draft as an `ask-draft`
        event so an already-open card on another device updates live; the
        writer suppresses its own echo via `origin`.

        Body: `tool_use_id` (must match the open `ask-pending` stash — a
        draft for a gone/replaced question is refused, 409), `answers` (a
        list aligned with the questions: {selected, other} per question),
        `origin` (an opaque per-page id, echoed back over SSE). ask_fmt.py
        clears the draft on the same boundary as `ask-pending`, so it never
        outlives its question. Best-effort: a write failure is a 500 but the
        card keeps its local state and retries on the next change."""
        body = self._post_guard()
        if body is None:
            return
        pending = _ask_pending(sid)
        if not pending:
            return self._json({"error": "no pending question"}, 409)
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id") or ""):
            return self._json({"error": "ask expired"}, 409)
        answers = body.get("answers")
        questions = pending.get("questions") or []
        if not isinstance(answers, list) or len(answers) != len(questions):
            return self._reject_input(
                "ask-draft", "answer count",
                "answers must match the %d question%s"
                % (len(questions), "" if len(questions) == 1 else "s"),
                {"n_answers": len(answers) if isinstance(answers, list) else None,
                 "n_questions": len(questions)}, log=P.mirror_log(sid))
        # normalize each answer to a dict FIRST: `answers` is only validated for
        # length above, so a non-dict element (adversarial/malformed body) must
        # not reach `.get()`. The old inline `if isinstance(a, dict)` on the
        # `selected` sub-comprehension was inert — the iterable `a.get(...)` was
        # evaluated before that per-element filter, raising AttributeError → 500.
        clean = []
        for a in answers:
            a = a if isinstance(a, dict) else {}
            clean.append({"selected": [str(s) for s in (a.get("selected") or [])],
                          "other": str(a.get("other") or "")})
        draft = {"tool_use_id": pending.get("tool_use_id") or "",
                 "answers": clean,
                 "origin": str(body.get("origin") or "")}
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        if not ST.kv_set_at(sdb, "ask-draft", draft):
            A.error(log, "dashboard ask-draft (write failed)", {"sid": sid})
            return self._json({"error": "draft not saved"}, 500)
        A.state_file(log, sdb, "ask-draft",
                     {"action": "write", "tool_use_id": draft["tool_use_id"],
                      "origin": draft["origin"]})
        return self._json({"ok": True})

    def post_composer_draft(self, sid):
        """Persist the UNSENT composer text (the message box's in-progress
        draft) to the `composer-draft` kv so another device — or the same one
        after a reload / a return to this session from another — restores it
        (docs/dashboard.md, *Web composer draft*). Like post_ask_draft this
        types NOTHING into the terminal: a pure state write, distinct from
        post_message (which sends). The session SSE re-broadcasts the draft as
        a `composer-draft` event so an already-open composer on another device
        updates live; the writer suppresses its own echo via `origin`.

        Body: `text` (the current draft — empty/blank DELETES the stash so the
        box clears everywhere), `origin` (an opaque per-page id, echoed back
        over SSE). Best-effort: a write failure is a 500 but the box keeps its
        local text and retries on the next change. Unlike the ask draft there
        is no tool_use_id / turn-boundary lifecycle — a message draft has no
        natural expiry, so it lives until sent or overwritten (that IS the
        'come back and it's still there' the user asked for)."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if not isinstance(text, str):
            return self._reject_input("composer-draft", "bad text",
                                      "text must be a string",
                                      {"type": type(text).__name__},
                                      log=P.mirror_log(sid))
        origin = str(body.get("origin") or "")
        seq = body.get("seq")
        seq = seq if isinstance(seq, (int, float)) else 0
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        # STALE-WRITE GUARD: a debounced save and the clear-on-send race — over a
        # slow tunnel AND, since the dashboard is a ThreadingHTTPServer, in two
        # concurrent worker threads — and can arrive out of order; an old save
        # landing after the clear would resurrect a just-sent draft (the "draft
        # didn't clear" report, 2026-07-19; the concurrent-thread variant that
        # slipped a lower-seq save past a separate read-then-write, 2026-07-22).
        # Each write carries a wall-clock `seq`; a write older than what's stored
        # is dropped so the newest state stands. The compare-and-set is ATOMIC
        # (one BEGIN IMMEDIATE — read-check-write can't be interleaved), or the
        # guard's read and its write straddle a peer thread's write. A CLEAR
        # stores a whitespace-only box as an empty-text TOMBSTONE (not a delete)
        # so its seq survives to reject a later straggler; _composer_draft reads
        # a tombstone as None.
        draft = {"text": text if text.strip() else "", "origin": origin,
                 "seq": seq}
        res = ST.kv_cas_seq_at(sdb, "composer-draft", draft)
        if res == "stale":
            A.state_file(log, sdb, "composer-draft",
                         {"action": "stale", "seq": seq, "origin": origin})
            return self._json({"ok": True, "stale": True})
        if res is None:
            A.error(log, "dashboard composer-draft (write failed)", {"sid": sid})
            return self._json({"error": "draft not saved"}, 500)
        A.state_file(log, sdb, "composer-draft",
                     {"action": "write" if text.strip() else "clear",
                      "chars": len(text), "seq": seq, "origin": origin})
        return self._json({"ok": True})

    def post_composer_queue(self, sid):
        """Persist the pending queued-message chips (the ⧗ list the composer
        shows for mid-turn messages the TUI queued but hasn't delivered) to the
        `composer-queue` kv, so a reload / another device restores them instead
        of losing the chip (the 'gone even from the queue after refresh'
        report, 2026-07-19; docs/dashboard.md, *Web composer queue*). Types
        NOTHING into the terminal — a pure state write, like the draft
        endpoints. The page sends the WHOLE current chip list on every change
        (queued, delivered-drain, ✕-hide); the SSE re-broadcasts it as a
        `composer-queue` event, the writer suppressing its own echo via
        `origin`.

        Body: `items` (a list of {text}; empty DELETES the stash), `origin`."""
        body = self._post_guard()
        if body is None:
            return
        items = body.get("items")
        if not isinstance(items, list):
            return self._reject_input("composer-queue", "bad items",
                                      "items must be a list",
                                      {"type": type(items).__name__},
                                      log=P.mirror_log(sid))
        # str() the filter side too, not just the value side: a non-string
        # `text` (e.g. a number in a malformed body) makes `(it.get("text") or
        # "").strip()` raise AttributeError → 500.
        clean = [{"text": str(it.get("text") or "")}
                 for it in items if isinstance(it, dict)
                 and str(it.get("text") or "").strip()]
        origin = str(body.get("origin") or "")
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        if clean:
            if not ST.kv_set_at(sdb, "composer-queue",
                                {"items": clean, "origin": origin}):
                A.error(log, "dashboard composer-queue (write failed)",
                        {"sid": sid})
                return self._json({"error": "queue not saved"}, 500)
            A.state_file(log, sdb, "composer-queue",
                         {"action": "write", "n": len(clean), "origin": origin})
        else:
            ST.kv_del_at(sdb, "composer-queue")
            A.state_file(log, sdb, "composer-queue",
                         {"action": "remove", "origin": origin})
        return self._json({"ok": True})

    def post_hint_audit(self, sid):
        """Record one lifecycle transition of an OPTIMISTIC web action (a client
        UI change shown the instant the user acts, whose REAL confirmation
        arrives async over SSE — docs/dashboard.md, *Optimistic UI & the
        web-hint audit*) as a `web-hint` state_files row, purely for
        after-the-fact debugging. `op` says WHICH optimistic action: `composer`
        (the greyed prompt stand-in before its transcript prompt lands — the
        original), `close` (the session card greyed 'closing…' until the tab
        actually parks), `answer` (the ask card greyed until its answer's
        PostToolUse drops the stash), `plan` (same for a plan decision). All
        four are client-only DOM whose lifecycle is INVISIBLE server-side, so a
        stuck greyed state leaves no trace without this beacon. Types NOTHING
        and writes NO session state — audit-only, best-effort.

        Body: `op` — composer | close | answer | plan (default composer);
        `phase` — shown | reconciled | dropped | stale (the stuck-state watchdog
        signal); `chars` (composer only — the message length; the raw prompt
        text is deliberately NOT sent, a length + timing is enough to correlate
        with the session's `web-send` row without storing content); `wait_ms`
        (ms since the optimistic state was shown — the reconcile latency);
        `reason` (for `dropped`: queued | send-failed | failed | a dialog step).
        A bad op/phase is a 400; otherwise always 200 — a telemetry beacon must
        not surface to the page."""
        body = self._post_guard()
        if body is None:
            return
        phase = str(body.get("phase") or "")
        if phase not in ("shown", "reconciled", "dropped", "stale"):
            return self._reject_input("web-hint", "bad phase", "bad phase",
                                      {"phase": phase}, log=P.mirror_log(sid))
        op = str(body.get("op") or "composer")
        if op not in ("composer", "close", "answer", "plan"):
            return self._reject_input("web-hint", "bad op", "bad op",
                                      {"op": op}, log=P.mirror_log(sid))
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        content = {"op": op, "phase": phase}
        for k in ("chars", "wait_ms"):
            v = body.get(k)
            if isinstance(v, (int, float)):
                content[k] = int(v)
        reason = body.get("reason")
        if isinstance(reason, str) and reason:
            content["reason"] = reason
        A.state_file(log, sdb, "web-hint", content)
        return self._json({"ok": True})

    def post_client_fail(self, sid):
        """Record a control-plane failure the PAGE observed but the server
        can't see — a `web-clientfail` state_files row, audit-only.

        A gesture like a composer send audits its outcome server-side BEFORE
        the HTTP response travels back (post_message writes `web-send ok:true`,
        returns 200), so a response LOST in transit (server restart, tunnel /
        proxy reset, dropped connection, a slept laptop) rejects the page's
        fetch and toasts "send failed" while the send actually SUCCEEDED — an
        outcome invisible to the audit until now (the "I saw a failed toast but
        the message went through" report). This beacon closes that blind spot:
        the page posts what IT saw, to be correlated against the paired
        `web-send`/`web-*` row.

        Body: `gesture` (send | resume | queue | … — which action the page was
        attempting), `kind` (transport = the fetch itself rejected, the
        server likely never saw the request OR its response was lost | http =
        the server returned an error status, so a paired failure row should
        exist), `error` (the error text the page had, capped), `status` (the
        HTTP status when `kind='http'`), `chars` (message length, optional).
        Types NOTHING and writes NO session state — best-effort, always 200
        unless the guard rejects (a telemetry beacon must not surface to the
        page; it also rides the SAME tunnel that may have just failed, so a
        missing row is itself expected for a total outage — the toast is the
        user-facing signal, this is only the after-the-fact breadcrumb)."""
        body = self._post_guard()
        if body is None:
            return
        gesture = str(body.get("gesture") or "")[:32]
        kind = str(body.get("kind") or "")
        if kind not in ("transport", "http"):
            kind = "transport"
        content = {"gesture": gesture, "kind": kind}
        err = body.get("error")
        if isinstance(err, str) and err:
            content["error"] = err[:200]
        for k in ("status", "chars"):
            v = body.get(k)
            if isinstance(v, (int, float)):
                content[k] = int(v)
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        A.state_file(log, sdb, "web-clientfail", content)
        return self._json({"ok": True})

    def post_client_log(self):
        """The FRONTEND AUDIT sink (docs/dashboard.md, *Frontend audit
        (clientlog)*): record a BATCH of browser-side events — one `web-client`
        state_files row each — so the page can report what IT actually did with a
        control request the server may never have seen. This closes the whole
        blind spot behind the "still not closing" saga: a `/stop` the browser
        *tried* but that never reached a handler (dropped by the tunnel, starved
        of a connection, queued forever) left NO server trace — only a client-side
        `close.begin` with no `close.ok`/`close.fail` reveals it, and only the
        browser can write that. Distinct from the other two client beacons:
        `web-hint` tracks OPTIMISTIC-UI lifecycle (shown/reconciled/stale),
        `web-clientfail` a single observed gesture failure; `web-client` is the
        general per-gesture transport + connection + JS-error timeline they sit
        on top of.

        Body: `client` (the page's opaque CLIENT_ID — correlates a device's rows
        across a batch), `conn` (a connection-health snapshot: `online`, `vis`,
        `view`, `es` = SSE streams held open, `conn` = global stream up), and
        `events` — a list of `{t, sid, ev, …scalars}`. `ev` is a dotted name:
        `<gesture>.begin`/`.ok`/`.fail` for a tagged control POST (close | send |
        command | interrupt | rename | migrate | rewind | rewind-to | answer |
        plan | new | resume-send), `close.reconciled`; `composer.recall` (an ↑/↓
        history-recall move in the composer — *Web composer history*);
        `sse.open`/`sse.drop` per
        stream; `js.error`/`js.reject` (uncaught); `boot`/`hello`/`stale` (page +
        build lifecycle — a `boot.build` ≠ `hello.boot` mismatch = stale cached
        JS); `meta.stuck`/`meta.resolved`/`meta.fail` (session-view load + the
        launch tag-race); `launch.arm`/`launch.hit`/`launch.timeout` (the launched
        session appearing); `backlog.fail`. Each event becomes one row scoped to its own `sid`
        (so it lands in that session's timeline); a blank/invalid sid is a
        session-less row (a launch, a boot record). Only scalar fields survive,
        strings capped, at most CLIENTLOG_MAX events — a page can't stuff bulk
        into the audit. Always 200 unless the guard rejects (telemetry must not
        surface to the page); rides the same channel a failing gesture might, so a
        missing batch is itself expected for a total outage."""
        body = self._post_guard()
        if body is None:
            return
        events = body.get("events")
        if not isinstance(events, list):
            return self._json({"error": "bad events"}, 400)
        client = str(body.get("client") or "")[:40]
        device = str(body.get("device") or "")[:40]
        conn = body.get("conn") if isinstance(body.get("conn"), dict) else None
        conn = self._clip_scalars(conn) if conn else None
        for e in events[:CLIENTLOG_MAX]:
            if not isinstance(e, dict):
                continue
            ev = str(e.get("ev") or "")[:40]
            if not ev:
                continue
            esid = e.get("sid")
            esid = esid if isinstance(esid, str) and _sid(esid) else ""
            row = API.session_row(esid) if esid else {}
            log = (row or {}).get("log") or (P.mirror_log(esid) if esid else "")
            sdb = (API.state_db_for(esid) or P.state_db(log)) if esid else ""
            content = {"ev": ev}
            if client:
                content["client"] = client
            if device:
                content["device"] = device
            ts = e.get("t")
            if isinstance(ts, (int, float)):
                content["t"] = int(ts)
            for k, v in self._clip_scalars(e).items():
                if k not in ("ev", "sid", "t", "client", "device"):
                    content[k] = v
            if conn:
                content["conn"] = conn
            A.state_file(log, sdb, "web-client", content)
        return self._json({"ok": True})

    @staticmethod
    def _clip_scalars(d):
        """Keep only JSON scalars from a client-supplied dict — bounded count,
        strings capped — so telemetry can't smuggle bulk/nesting into the audit."""
        out = {}
        for k, v in list(d.items())[:CLIENTLOG_FIELD_MAX]:
            if not isinstance(k, str):
                continue
            if isinstance(v, bool) or isinstance(v, (int, float)):
                out[k] = v
            elif isinstance(v, str):
                out[k] = v[:CLIENTLOG_STR_MAX]
        return out

    def post_answer(self, sid):
        """Answer the session's OPEN AskUserQuestion dialog from the web (the
        ask card — docs/dashboard.md, *Web ask*): drives the TUI's own dialog
        with screen-verified key events (dashboard/askdialog.drive).

        Body: `tool_use_id` — must match the `ask-pending` stash (a stale
        card is refused before any key is pressed); either `chat: true` (the
        dialog's own "Chat about this" — declines + invites discussion; the
        page then focuses its composer) or `answers` — a list aligned with
        the stash's questions: {"selected": [labels…], "other": "text"} per
        question (multiSelect may combine both; single-select uses one or
        the other).

        409 on a missing/expired stash, a stash/window mismatch, or any
        dialog step that didn't verify (AskError — the dialog is left OPEN,
        never Escape-closed: Escape would DECLINE the questions; `step` says
        what failed and a retry from the card re-normalizes). Every attempt
        is a `web-answer` state_files row, failures also an A.error. The
        card itself clears via the SSE `ask` event when the answer's
        PostToolUse drops the stash — the true end-to-end signal."""
        body = self._post_guard()
        if body is None:
            return
        chat = bool(body.get("chat"))
        answers = body.get("answers")
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        pending = _ask_pending(sid)
        if not pending:
            return self._json({"error": "no pending question"}, 409)
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id") or ""):
            return self._json({"error": "ask expired — a newer question "
                               "replaced it (refresh)"}, 409)
        questions = pending.get("questions") or []
        if not chat and (not isinstance(answers, list)
                         or len(answers) != len(questions)):
            return self._reject_input(
                "web-answer", "answer count",
                "answers must match the %d question%s"
                % (len(questions), "" if len(questions) == 1 else "s"),
                {"n_answers": len(answers) if isinstance(answers, list) else None,
                 "n_questions": len(questions)}, log=log, path=sdb)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard answer (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-answer",
                         {"win": "", "ok": False, "chat": chat})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-answer",
                         {"win": "", "ok": False, "chat": chat})
            return self._json({"error": "session has no live window"}, 409)
        try:
            askdialog.drive(fe, win, questions, answers or [], chat=chat)
        except askdialog.AskError as e:
            ctx = {"sid": sid, "win": win, "chat": chat, "detail": str(e)}
            if e.screen is not None:      # the pixels the failing step saw
                ctx["screen"] = _clip_screen(e.screen)
            A.error(log, "dashboard answer (%s)" % e.step, ctx)
            A.state_file(log, sdb, "web-answer",
                         {"win": win, "ok": False, "chat": chat,
                          "step": e.step,
                          "tool_use_id": pending.get("tool_use_id") or ""})
            _heal_stash(sid, log, sdb, "ask-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-answer",
                     {"win": win, "ok": True, "chat": chat,
                      "tool_use_id": pending.get("tool_use_id") or ""})
        # a PREVIEW-layout question has no typed-answer row (askdialog
        # _require_type_row), so the card routes a TYPED answer through 'Chat
        # about this' AND carries the typed text here as `message`: once the
        # dialog is dismissed (drive waited for that), deliver it as the
        # follow-up so the user's custom answer reaches the session as a
        # normal message (docs/dashboard.md, *Web ask*). Only with chat.
        msg = body.get("message")
        resp = {"ok": True, "chat": chat}
        if chat and isinstance(msg, str) and msg.strip():
            clip = _clear_clipboard_image()      # clipboard-image guard, as post_message
            sent = bool(fe.paste_text(win, msg))
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(msg), "ok": sent,
                          "via": "ask-chat", "clip": clip})
            if not sent:
                A.error(log, "dashboard answer-chat message (send failed)",
                        {"sid": sid, "win": win})
            resp["message_sent"] = sent
        return self._json(resp)

    def _plan_guard(self, sid):
        """The shared head of the two plan endpoints: guard the POST, match
        the stash, resolve the live window. Returns (body, pending, fe, win,
        log, sdb) — or (None, …) after sending the error response."""
        none = (None,) * 6
        body = self._post_guard()
        if body is None:
            return none
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        pending = _plan_pending(sid)
        if not pending:
            self._json({"error": "no pending plan"}, 409)
            return none
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id")
                                               or ""):
            self._json({"error": "plan expired — a newer plan replaced it "
                        "(refresh)"}, 409)
            return none
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard plan (no terminal)", {"sid": sid})
            self._json({"error": "no terminal available"}, 503)
            return none
        win = fe.window_for_session(sid) or ""
        if not win:
            self._json({"error": "session has no live window"}, 409)
            return none
        return body, pending, fe, win, log, sdb

    def post_plan_options(self, sid):
        """The plan card's decision buttons — the dialog's option labels VARY
        with the session's permission mode ("Yes, and bypass permissions" vs
        "Yes, and auto-accept edits"), so the page fetches them from the live
        screen (plandialog.options — read-only: no key is pressed). An `open`
        bail self-heals the stash (the dialog resolved in the terminal)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        try:
            opts = plandialog.options(fe, win)
        except plandialog.PlanError as e:
            _heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        return self._json({"ok": True, "options": opts})

    def post_plan_decision(self, sid):
        """Decide the OPEN plan dialog from the web (docs/dashboard.md, *Web
        plan mode*): drives the TUI's own dialog via dashboard/plandialog.

        Body (one of, after `tool_use_id` matching the `plan-pending` stash):
        `digit` + `label` — press that decision row, verified against the
        live screen (label drift = 409, nothing pressed); `feedback` — the
        "Tell Claude what to change" row: focus, type, Enter (rejects with
        feedback; newlines collapse — single-line editor); `dismiss: true` —
        Escape, the TUI's own reject-and-keep-planning.

        409 on stash mismatch or any unverified step (PlanError — the dialog
        is left OPEN: an Escape bail would REJECT a plan the user may still
        approve; `open` bails self-heal the stash). Every attempt is a
        `web-plan` state_files row, failures also an A.error. The card
        clears via the SSE `plan` event when the stash drops (approval's
        PostToolUse, or the turn boundary after a reject)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        tid = pending.get("tool_use_id") or ""
        if body.get("dismiss"):
            kind, run = "dismiss", (plandialog.dismiss, (fe, win))
        elif isinstance(body.get("feedback"), str) \
                and body["feedback"].strip():
            kind = "feedback"
            run = (plandialog.feedback, (fe, win, body["feedback"]))
        elif body.get("digit") and isinstance(body.get("label"), str):
            kind = "decide"
            run = (plandialog.decide,
                   (fe, win, str(body["digit"]), body["label"]))
        else:
            return self._reject_input(
                "web-plan", "no action",
                "need digit+label, feedback, or dismiss",
                {"keys": sorted(body)}, log=log, path=sdb)
        try:
            run[0](*run[1])
        except plandialog.PlanError as e:
            A.error(log, "dashboard plan (%s)" % e.step,
                    {"sid": sid, "win": win, "kind": kind,
                     "detail": str(e)})
            A.state_file(log, sdb, "web-plan",
                         {"win": win, "ok": False, "kind": kind,
                          "step": e.step, "tool_use_id": tid})
            _heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-plan",
                     {"win": win, "ok": True, "kind": kind,
                      "label": body.get("label") or "", "tool_use_id": tid})
        return self._json({"ok": True, "kind": kind})

    def _dialog_open_guard(self, tab, log, sdb, win, action):
        """Refuse an Esc-sending gesture (interrupt / cancel-edit / rewind) when
        a MODAL DIALOG is open — the red `awaiting-command` tab means Claude is
        asking YOU (AskUserQuestion / ExitPlanMode / a permission prompt). An
        Esc there does not cancel a turn; it DECLINES/dismisses the dialog,
        which once killed the answer the user was giving through the web ask
        card ("User declined to answer questions", 2026-07-20). The dashboard's
        dedicated cards (ask/plan/confirm) are the response path, so bail with a
        409 and audit it — the same contract post_command uses on a red tab.
        Returns True when it handled (sent) the refusal; False to proceed."""
        if tab != tabs.AWAITING_COMMAND:
            return False
        A.state_file(log, sdb, action,
                     {"win": win, "ok": False, "tab": tab, "step": "dialog"})
        self._json({"error": "a dialog is open — answer it first"}, 409)
        return True

    def _escape_press(self, sid, verb, action):
        """Body of post_interrupt: guard, resolve the LIVE window, press
        Escape, audit as `action`, and spawn the escape-recheck when the
        press landed on magenta. A red (awaiting-command) tab is a 409: a
        dialog is open and the Esc would DECLINE it, not interrupt a turn."""
        body = self._post_guard()
        if body is None:
            return
        row = API.session_row(sid) or {}
        log = row.get("log") or P.mirror_log(sid)
        sdb = API.state_db_for(sid) or P.state_db(log)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard %s (no terminal)" % verb, {"sid": sid})
            A.state_file(log, sdb, action, {"win": "", "ok": False})
            return self._json({"error": "no terminal available"}, 503)
        # AUTHORITATIVE window: the pane currently tagged claude_session=<sid>,
        # NOT the audit row's stale start-time id (an Escape into a reused id
        # would interrupt an unrelated session — see _live_windows; a fresh
        # scan, never the TTL memo).
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, action, {"win": "", "ok": False})
            return self._json({"error": "session has no live window"}, 409)
        tab = API.tab_states().get(win) or ""
        if self._dialog_open_guard(tab, log, sdb, win, action):
            return
        # Press-time transcript size — the escape-recheck's growth baseline
        # (stat'd BEFORE the key lands so even the interrupt line counts as
        # growth). '' / unstat-able transcript degrades to the recheck's own
        # start-time baseline.
        tpath = row.get("transcript_path") or ""
        try:
            tsize = os.path.getsize(tpath) if tpath else -1
        except OSError:
            tsize = -1
        ok = bool(fe.send_key(win, "escape"))
        A.state_file(log, sdb, action, {"win": win, "ok": ok, "tab": tab})
        if ok and tab in (tabs.THINKING, tabs.WORKING):
            # An Esc killed mid-think leaves NO signal anywhere (the known
            # interrupt-watch gap) — but a WEB interrupt is itself an event,
            # so spawn the escape-recheck: flip the dead magenta green unless
            # any real signal (state movement / transcript growth) shows up
            # within its grace. Detached + audited (A.spawn); its verdict
            # lands as tab_transitions rows under DISPATCH escape-recheck.
            self._spawn_escape_recheck(fe, win, log, tpath, tsize)
        if not ok:
            A.error(log, "dashboard %s (send failed)" % verb,
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "tab": tab})

    def _spawn_escape_recheck(self, fe, win, log, tpath, tsize):
        """Detached `claude-tab-status.py escape-recheck <log> <transcript>
        <press-size>` for the session's window. Env carries the window id +
        the terminal-reach vars (fe.export_env — the detached process is
        re-parented, so the ppid socket walk can't find kitty). Spawn failure
        is audited by spawn_detached; assembly failure lands its own A.error
        (the recovery not firing must never be invisible)."""
        try:
            fe.export_env()
            env = dict(os.environ)
            env["KITTY_WINDOW_ID"] = str(win)
            args = ["escape-recheck", log, tpath]
            if tsize >= 0:
                args.append(str(tsize))
            SP.spawn_detached(os.path.join(P.BIN, "claude-tab-status.py"),
                              args, log, env=env,
                              purpose="watcher:escape-recheck")
        except Exception:
            A.error(log, "dashboard interrupt (escape-recheck spawn)",
                    {"win": win})

    def _reject_input(self, action, why, message, detail, code=400,
                      log="", path=""):
        """A control-plane INPUT-validation reject (the client sent a bad
        field). Audited as an `ok:False` state_files row under the handler's own
        `action` vocabulary, carrying the reason (`why`) and the EXACT received
        bytes (repr — a remote client's "but I picked it from the dropdown" is
        undebuggable otherwise, invisible characters included). Deliberately NOT
        an `errors` row: these are expected 4xx from client input (an
        abandoned/partial cwd, a typo'd model, a bad quick-command), not
        swallowed exceptions — their traceback would be a bare `NoneType: None`
        — and must not light the errwatch warning chip, which surfaces every
        session_id='' `errors` row as a `⚠ global:` in EVERY session's
        scorebar. This is the shape `post_dictate_token` already used inline for
        its bad-rate reject; the reject sites that mis-used A.error now share it.
        Distinct from `_reject` (the low-level guard rejection that closes the
        connection because it hasn't read the body — the input body is already
        consumed by `_post_guard` here, so no desync to guard against).

        `log`/`path` file the row under a SESSION (the session-scoped POSTs —
        web-send/web-rename/web-answer/…, so a rejected attempt lands in THAT
        session's audit timeline, not just the global stream); default '' keeps
        the GLOBAL row the session-less endpoints (web-launch/notify-mute/
        hide-dir/dictate) already relied on. Without this every empty-message /
        empty-name / bad-payload reject was a silent 4xx — the same class of
        blind spot `_reject` closed for guard rejections. Returns the response
        so callers stay `return self._reject_input(...)`."""
        A.state_file(log, path, action,
                     dict({"ok": False, "why": why},
                          **{k: repr(v) for k, v in detail.items()}))
        return self._json({"error": message}, code)

    def post_new_session(self):
        """Launch a new session in a new tab (Frontend.launch_tab). 400 when the
        cwd isn't an existing directory or model/effort/resume/continue don't
        validate (model: one clean argv word; effort: the CLI's EFFORTS levels;
        resume: a clean session id, exclusive with continue); 503 when no
        terminal resolves; else the launch, with `--resume <sid>`/`--continue`
        and `--model`/`--effort` riding as positional "$@" words ahead of the
        prompt. The response carries the new tab's window id when the terminal
        reports one, and a _launch_wake watcher thread hurries the session's
        SSE appearance (see its block). Audited as a `web-launch` state_files
        row (no session db exists yet, so its log/path are empty; `win` = the
        launched window)."""
        body = self._post_guard()
        if body is None:
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
            return self._reject_input("web-launch", "bad cwd",
                                "cwd is not an existing directory",
                                {"cwd": cwd})
        model, effort = body.get("model"), body.get("effort")
        if model is not None and (
                not isinstance(model, str) or not _MODEL_OK.match(model)):
            return self._reject_input("web-launch", "bad model", "invalid model",
                                {"model": model})
        if effort is not None and effort not in EFFORTS:
            return self._reject_input("web-launch", "bad effort", "invalid effort",
                                {"effort": effort})
        # resume / continue — the CLI's own conversation-pickup flags. resume
        # carries a session id (one clean argv word, same alphabet as our sid
        # routing); continue is a bare flag. Mutually exclusive, like the CLI.
        # A resumed conversation FORKS to a new sid; the existing adopt
        # machinery and the page's jump watch both handle that on their own.
        resume, cont = body.get("resume"), body.get("continue")
        if resume is not None and (
                not isinstance(resume, str) or not _SID_OK.match(resume)):
            return self._reject_input("web-launch", "bad resume", "invalid resume id",
                                {"resume": resume})
        if cont not in (None, False, True):
            return self._reject_input("web-launch", "bad continue",
                                "invalid continue", {"continue": cont})
        if resume and cont:
            return self._reject_input("web-launch", "resume+continue",
                                "resume and continue are exclusive",
                                {"resume": resume})
        # account: the switcher slug to launch under (default `claude` when
        # absent). Resolved to a registry-vetted command word — never the raw
        # value flows into the launch shell string.
        acct = body.get("account")
        cmd = plugins.account_alias(acct) if acct else "claude"
        if cmd is None:
            return self._reject_input("web-launch", "bad account", "unknown account",
                                {"account": acct})
        prompt = body.get("prompt")
        prompt = prompt if isinstance(prompt, str) else ""
        # attachments ride the launch prompt as leading @-mentions, same as the
        # live composer (covers the new-session form AND the parked "resume &
        # send" path, which both route through here). With no typed prompt, the
        # mentions alone are a valid initial prompt.
        attachments = self._attachment_paths(body)
        prompt = self._with_attachments(prompt, attachments)
        words = ((["--resume", resume] if resume else [])
                 + (["--continue"] if cont else [])
                 + (["--model", model] if model else [])
                 + (["--effort", effort] if effort else [])
                 + ([prompt] if prompt.strip() else []))
        argv = launch_argv(words, cmd)
        opts = {"cwd": cwd, "model": model or "", "effort": effort or "",
                "resume": resume or "", "cont": bool(cont),
                "account": acct or "", "attachments": len(attachments)}
        fe = launch._frontend()
        if fe is None:
            A.error("", "dashboard new-session (no terminal)", {"cwd": cwd})
            A.state_file("", "", "web-launch", dict(opts, ok=False))
            return self._json({"error": "no terminal available"}, 503)
        # Guard: never resume-launch a session that ALREADY has a live tab. A
        # second `claude --resume <sid>` would run a duplicate process against
        # the SAME transcript (two tabs, interleaved writes). The page issues a
        # resume-launch only when it believes the session is PARKED, but a
        # stale page (e.g. after the dashboard restarts and its SSE drops)
        # can misjudge a live session — this is the server-side backstop.
        # window_for_session is a fresh live kitten scan (authoritative over
        # any cached/page state); fresh and --continue launches are unaffected.
        # The page gets the live window back so it can focus/message it instead.
        if resume:
            # A resume target whose transcript .jsonl is GONE can't be resumed:
            # `claude --resume` finds no conversation and the freshly launched
            # tab exits at once — a silent dead tab the user reads as "resume
            # did nothing" (observed live on an aggregator session whose file
            # was never persisted, 2026-07-21). Reject up front when the sid's
            # KNOWN transcript path (its audit row) is absent on disk; an
            # unknown sid (no row / no path) is left to the CLI — we can't prove
            # it's broken. All accounts share ~/.claude/projects (the switcher
            # symlinks each configs/<slug>/projects to it), so the launch
            # account is irrelevant to this check.
            r_tpath = (API.session_row(resume) or {}).get("transcript_path") or ""
            if r_tpath and not os.path.isfile(r_tpath):
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, why="transcript missing"))
                return self._json(
                    {"error": "session transcript is gone — can't resume",
                     "sid": resume}, 410)
            live_win = fe.window_for_session(resume) or ""
            if live_win:
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, win=live_win))
                return self._json(
                    {"error": "session already live", "sid": resume,
                     "win": live_win}, 409)
        # the passive steal watch (see the block above the Handler class):
        # the frontmost app must be captured BEFORE the launch — a steal can
        # land before the kitten call returns. Skipped when the terminal was
        # ALREADY frontmost at click time (nothing to steal) or the frontend
        # has no OS app identity (the inert stub, off-mac).
        term = fe.app_id()
        before = launch._front_app() if term else ""
        # a launch carrying a first prompt makes Claude Code's TUI read the
        # clipboard at startup and attach any image to that auto-submitted
        # message (docs/dashboard.md *Clipboard-image guard*) — empty an image
        # clipboard first so the startup grab finds nothing. Only when there's a
        # prompt (a bare launch auto-submits nothing, so nothing to attach to).
        clip = _clear_clipboard_image() if prompt.strip() else False
        # launch_tab: the new window's id on success when the terminal reports
        # one (kitty prints it), bare True when it doesn't, falsy on failure.
        got = fe.launch_tab(cwd, argv)
        win = got if isinstance(got, str) else ""
        A.state_file("", "", "web-launch",
                     dict(opts, ok=bool(got), win=win, clip=clip))
        if not got:
            A.error("", "dashboard new-session (launch failed)", {"cwd": cwd})
            return self._json({"error": "launch failed"}, 502)
        # the SSE wake watch (see the block above the Handler class): hurry
        # the launched session's appearance to every connected page — and hand
        # the launching page its sid — the moment SessionStart lands.
        threading.Thread(target=_launch_wake, args=(win, cwd, time.time()),
                         daemon=True, name="web-launch-wake").start()
        if before and before != term:
            threading.Thread(target=_steal_watch, args=(before, term),
                             daemon=True, name="web-launch-steal-watch").start()
        # `win` lets the page match the launched session exactly (its jump
        # watch compares kitty_window_id); "" when the terminal didn't report
        # an id — the page falls back to its cwd heuristic.
        return self._json({"ok": True, "win": win})

    def post_ns_prefs(self):
        """Remember the new-session form's last-used {cwd, model, effort} in the
        durable GLOBAL prefs store (dashboard/prefs.py) so the next launch — on
        this device or any other pointing at this dashboard — pre-selects them
        (docs/dashboard.md, *New-session prefs*). The page calls this on a
        successful launch, exactly where it used to write localStorage; the
        BEHAVIOUR is unchanged, only the storage moved to the backend.

        Body: `cwd` (string), `model`/`effort` (validated against the same
        allowlists post_new_session uses — a bad value is dropped, never
        stored, so a corrupt pref can't later feed the launch path). Missing
        fields are simply omitted from the stored record. Best-effort: a write
        failure is a 500 but the launch itself already succeeded."""
        body = self._post_guard()
        if body is None:
            return
        rec = {}
        cwd = body.get("cwd")
        if isinstance(cwd, str) and cwd:
            rec["cwd"] = cwd
        model = body.get("model")
        if isinstance(model, str) and _MODEL_OK.match(model):
            rec["model"] = model
        effort = body.get("effort")
        if effort in EFFORTS:
            rec["effort"] = effort
        if not prefs.set("new-session", rec):
            A.error("", "dashboard ns-prefs (write failed)", {"rec": rec})
            return self._json({"error": "prefs not saved"}, 500)
        # global (no session) — audited with an empty log/path like web-launch
        A.state_file("", "", "ns-prefs", dict(rec, action="write"))
        return self._json({"ok": True})

    def post_notify_mute(self, sid):
        """Opt a session in/out of the deferred Telegram alert (docs/dashboard.md
        *Telegram alerts*) — the header ◉/○ toggle. Body: `muted` (bool).
        Writes the durable global prefs store (dashboard/prefs.py), NOT any
        session/terminal state, so it works live AND parked. Behind _post_guard
        like every control-plane POST; audited as a `notify-mute` state_files row
        (global — empty log/path like hide-dir). Returns the flipped state."""
        body = self._post_guard()
        if body is None:
            return
        muted = body.get("muted")
        if not isinstance(muted, bool):
            return self._reject_input("notify-mute", "bad muted",
                                      "muted must be a boolean", {"muted": muted})
        prefs.set_notify_muted(sid, muted)
        A.state_file("", "", "notify-mute", {"sid": sid, "muted": muted})
        return self._json({"ok": True, "muted": muted})

    def post_viewing(self, sid):
        """Presence heartbeat: the page reports it is looking at session `sid`
        RIGHT NOW (docs/dashboard.md *Telegram alerts*). The client sends it on
        a timer ONLY while the page is visible + focused + showing this session,
        so the mere arrival is the signal — it refreshes the in-memory
        `_VIEWING` deadline (`_mark_viewing`, fresh for VIEW_TTL_S) that the
        deferred Telegram alert checks at send time to tell 'watching the
        dashboard' from 'walked away'. Types NOTHING and writes NO session
        state; NOT audited per-beat (ephemeral live-only presence, like the SSE
        connection — the SUPPRESS it drives lands the notify-suppress row).
        Behind _post_guard like every control-plane POST; always 200 (an empty
        `{}` body is fine — the URL's sid IS the payload)."""
        if self._post_guard() is None:
            return
        _mark_viewing(sid)
        return self._json({"ok": True})

    def post_hide_dir(self):
        """Hide a directory group from the list page (docs/dashboard.md *Hidden
        directories*). Non-destructive: the sessions keep running, their tabs and
        toasts still fire — the group just vanishes from the crowded list until a
        session STARTED after this moment shows up in it (the client compares each
        row's started_at against the stored hide time, so 'start a new session
        there' un-hides it, terminal- or dashboard-launched). Stores {key:
        time.time()} in the durable global prefs store (dashboard/prefs.py),
        keyed by the list's group key (git.root||cwd — the page posts g.cwd,
        already that key). Behind _post_guard like every control-plane POST,
        though it writes only the dashboard's OWN prefs, never a session/terminal.

        A directory with at least one ACTIVE (live) session can't be hidden — a
        409, the authoritative guard behind the disabled ✕ (dir_live_sessions;
        the client also disables the button, but a stale page could still POST).
        Audited as a `hide-dir` state_files row (global — empty log/path like
        ns-prefs). Returns the updated map so the page reconciles S.hidden with
        the server truth."""
        body = self._post_guard()
        if body is None:
            return
        key = body.get("cwd")
        # The EMPTY string is a valid key — it is the list's "no project"
        # aggregate group (sessions with no cwd / git root), which the user can
        # hide like any other. Only a MISSING/non-string cwd (None etc.) is a bad
        # request. repr() in the audit: a reject must keep the EXACT received
        # bytes (same rule as new-session's bad cwd). len cap: a group key is a
        # path — no legitimate one runs long, and the store is not a bucket.
        if not isinstance(key, str) or len(key) > 4096:
            return self._reject_input("hide-dir", "bad key", "cwd must be a string",
                                {"cwd": key})
        # A directory with an active session can't be hidden (409). Not an input
        # error — the key is well-formed — so it's a distinct `why`, but the same
        # audited-reject shape (no errors row / errwatch chip; an expected 4xx).
        live = dir_live_sessions(key)
        if live:
            return self._reject_input(
                "hide-dir", "live session",
                "can't hide a directory with an active session",
                {"cwd": key, "live": len(live)}, code=409)
        ts = time.time()
        m = prefs.hide_dir(key, ts)
        A.state_file("", "", "hide-dir", {"key": key, "hidden_at": ts})
        return self._json({"ok": True, "hidden": m})

    def post_push_subscribe(self):
        """Register a browser's Web Push subscription (docs/dashboard.md *Web
        push*). Body: {subscription: {endpoint, keys:{p256dh, auth}}} — the exact
        PushSubscription.toJSON() the browser produced. Stored (upserted by
        endpoint) in the durable global prefs store; the Notifier fans a push out
        to every stored subscription on the deferred asking/done alert, honoring
        the per-session ○ mute. Behind _post_guard like every control-plane POST,
        though it writes only the dashboard's OWN prefs. Audited as a `web-push`
        state_files row (action subscribe)."""
        body = self._post_guard()
        if body is None:
            return
        sub = body.get("subscription")
        ep = sub.get("endpoint") if isinstance(sub, dict) else None
        keys = sub.get("keys") if isinstance(sub, dict) else None
        if not (isinstance(ep, str) and ep.startswith("https://")
                and isinstance(keys, dict) and keys.get("p256dh") and keys.get("auth")):
            return self._reject_input("web-push", "bad subscription",
                                      "subscription must carry endpoint + keys",
                                      {"has_ep": bool(ep)})
        # `device` (the browser's stable localStorage id) + `label` (a friendly
        # platform string) let the Notifier route the on-device push to the ONE
        # device you most recently used instead of every subscription. Optional
        # (a legacy client omits them → the sub is stored untagged, and routing
        # degrades to send-all for it — see _mru_push_targets).
        dev = body.get("device")
        dev = dev if isinstance(dev, str) and dev else None
        label = body.get("label")
        label = label[:60] if isinstance(label, str) and label else None
        prefs.add_push_subscription(sub, device=dev, label=label)
        A.state_file("", "", "web-push", {"action": "subscribe", "endpoint": ep[:80],
                                          "device": dev, "label": label})
        return self._json({"ok": True})

    def post_presence(self):
        """Device presence heartbeat: the page reports it is visible + focused
        RIGHT NOW on this device (docs/dashboard.md *Device routing*). The client
        sends it on a timer + on focus/reveal, from ANY view (not just a session
        — so 'you're on this device' is recorded even from the list). Body:
        {device, sid?}. `device` (the browser's stable localStorage id) stamps
        `_DEVICE_SEEN` so the on-device push routes to your most-recently-used
        device; `sid` (present only inside a session view) ALSO refreshes the
        `_VIEWING` deadline that suppresses the alert while you watch that
        session — folding the old per-session viewing beat into this one. Types
        NOTHING and writes NO session state; NOT audited per-beat (ephemeral
        presence, like the SSE connection). Behind _post_guard; always 200."""
        body = self._post_guard()
        if body is None:
            return
        dev = body.get("device")
        if isinstance(dev, str) and dev:
            _mark_device(dev)
        sid = body.get("sid")
        if isinstance(sid, str) and sid:
            _mark_viewing(sid)
        return self._json({"ok": True})

    def post_push_unsubscribe(self):
        """Drop a browser's Web Push subscription (docs/dashboard.md *Web push*)
        — the opt-out twin of subscribe. Body: {endpoint}. Idempotent (a missing
        endpoint just no-ops). Audited as a `web-push` state_files row (action
        unsubscribe)."""
        body = self._post_guard()
        if body is None:
            return
        ep = body.get("endpoint")
        if not isinstance(ep, str) or not ep:
            return self._reject_input("web-push", "bad endpoint",
                                      "endpoint required", {})
        prefs.remove_push_subscription(ep)
        A.state_file("", "", "web-push", {"action": "unsubscribe", "endpoint": ep[:80]})
        return self._json({"ok": True})

    def post_dictate_token(self):
        """Mint a short-lived Deepgram grant for the browser's DIRECT wss
        connection (docs/dashboard.md *Web dictation* — the stdlib server
        can't speak WebSocket and must never see audio, so its whole role is
        this trade: on-disk API key → ~30s single-purpose JWT). The response
        carries the token plus the fully-assembled listen URL (model +
        keyterms server-side; the client contributes only its AudioContext
        sample rate). Behind _post_guard like every control-plane POST — on
        READONLY days dictation is off exactly like the composer it feeds.
        Every attempt is a `web-dictate` state_files row (no sid — the
        new-session form dictates too), failures also an A.error. The API
        key never appears in a response or an audit row."""
        body = self._post_guard()
        if body is None:
            return
        rate = body.get("sample_rate")
        if not isinstance(rate, int) or isinstance(rate, bool) \
                or not (dictate.SAMPLE_RATE_MIN <= rate
                        <= dictate.SAMPLE_RATE_MAX):
            A.state_file("", "", "web-dictate",
                         {"ok": False, "why": "bad-rate",
                          "rate": repr(rate)[:40]})
            return self._json({"error": "bad sample_rate"}, 400)
        if not dictate.available():
            # a race fallback only — the page hides the mic button when the
            # /api/dictate probe says unavailable
            A.state_file("", "", "web-dictate", {"ok": False, "why": "no-key"})
            return self._json({"error": "no deepgram key configured"}, 501)
        # optional cwd — keys the PROJECT vocabulary layer (the composer sends
        # its session's cwd, the new-session form its typed dir). A non-string
        # or non-directory degrades to global-only, never an error — the same
        # contract as /api/commands, and for the same reason (arbitrary
        # sessions' dirs come and go).
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            cwd = ""
        try:
            tok = dictate.grant()
        except Exception as e:
            A.error("", "dashboard dictate (grant failed)",
                    {"err": ("%s: %s" % (type(e).__name__, e))[:200]})
            A.state_file("", "", "web-dictate", {"ok": False, "why": "grant"})
            return self._json({"error": "token grant failed"}, 502)
        terms = dictate.keyterms(cwd)
        url = dictate.ws_url(rate, terms)
        A.state_file("", "", "web-dictate",
                     {"ok": True, "rate": rate, "cwd": cwd,
                      "keyterms": len(terms)})
        return self._json({"token": tok["access_token"],
                           "expires_in": tok.get("expires_in"),
                           "ws_url": url})

    def static(self, name):
        ctype = STATIC.get(name)
        if not ctype:
            return self._json({"error": "not found"}, 404)
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as fh:
                data = fh.read()
        except OSError:
            return self._json({"error": "unreadable"}, 500)
        if name == "index.html":
            # CACHE-BUST the sub-resource URLs with BOOT_ID (bumped every
            # restart). The origin sends no-store, but that can't evict an
            # already-cached app.js/style.css in a remote browser (mobile Safari
            # is sticky, and a CDN keys by URL) — so a dashboard update left the
            # phone running old JS forever (the "links don't follow on mobile"
            # report traced here: the fix shipped, the origin served it, the
            # phone kept the pre-fix bytes). index.html itself is served no-store
            # AND is the main document a reload always refetches, so a fresh
            # ?v=<BOOT_ID> reaches the browser and points at a URL nothing has
            # cached. See docs/dashboard.md *Cache-busting*.
            data = data.replace(b"/static/app.js", b"/static/app.js?v=" + BOOT_ID.encode())
            data = data.replace(b"/static/style.css", b"/static/style.css?v=" + BOOT_ID.encode())
        return self._send(200, data, ctype)

    # -- SSE loops --
    def sse_global(self):
        """The all-sessions stream: a `hello` (the server's BOOT_ID — the
        browser's EventSource auto-reconnects when the server restarts, and a
        changed boot id on reconnect is how an OPEN page learns its loaded JS
        may be stale; twice a redeploy shipped while a page sat open and its
        old handlers ran against the new server, audit-visibly), then a
        `sessions` snapshot on connect and whenever MEMBERSHIP or order
        changes, a `sessions-delta` {rows: [changed wire rows]} when only row
        contents moved (SSE frames are never gzipped, and the full 131-row
        snapshot re-sent every active tick measured 2.2MB/min per remote
        viewer — deltas are a few KB/min; the sid set + order pin the list
        layout, so a delta can always merge in place by sid), plus every
        `notify` toast the watcher pushes. Row diffs are paused-blind
        (_row_key) and rows are wire-stripped (_wire_row)."""
        self._sse_start()
        q = NOTIFIER.register()
        try:
            if not self._sse("hello", {"boot": BOOT_ID}):
                return
            beat = time.monotonic()
            wire = [_wire_row(r) for r in sessions_payload()]
            if not self._sse("sessions", wire):
                return
            keys = {r["sid"]: _row_key(r) for r in wire}
            while True:
                drained = False
                try:
                    while True:
                        ev, payload = q.get(timeout=GLOBAL_TICK_S)
                        drained = True
                        if not self._sse(ev, payload):
                            return
                except queue.Empty:
                    pass
                wire = [_wire_row(r) for r in sessions_payload()]
                cur = {r["sid"]: _row_key(r) for r in wire}
                if list(cur) != list(keys):
                    # a session appeared/vanished or the order moved — the
                    # delta contract can't express that; full resync
                    if not self._sse("sessions", wire):
                        return
                    keys = cur
                elif cur != keys:
                    changed = [r for r in wire if cur[r["sid"]] != keys[r["sid"]]]
                    if not self._sse("sessions-delta", {"rows": changed}):
                        return
                    keys = cur
                now = time.monotonic()
                if drained or now - beat > HEARTBEAT_S:
                    beat = now
                    if not self._sse_beat():
                        return
        finally:
            NOTIFIER.unregister(q)

    def sse_session(self, sid, after, mpos=0):
        """One session's live stream: `ops` (rendered HTML), `msgs` (the
        main-thread conversation from byte cursor `mpos`), `stats`, `agents`,
        `tab`, `costs`, `running` (the live slot ribbon), `errors` (the ⚠
        swallowed-error count) — each sent only on change. A FRESH connection
        (after=0, mpos=0) gets the anchor-merged backlog as its first ops
        event; a reconnect resumes both cursors and appends in arrival
        order (interleave is a backfill affordance, not a live guarantee)."""
        self._sse_start()
        last = after
        prev = {"stats": None, "agents": None, "tab": None, "costs": None,
                "running": None, "errors": None, "ask": None, "plan": None,
                "ctx": None, "git": None, "title": None, "effort": None,
                "tasks": None, "ask_draft": None, "composer_draft": None,
                "composer_queue": None, "monitors": None, "jobs": None,
                "memory": None, "suggestion": None, "goal": None}
        row = API.session_row(sid) or {}
        win = str(row.get("kitty_window_id") or "")
        key = P.sid_from_log(row.get("log") or P.mirror_log(sid))
        if not after and not mpos:
            last, mpos, oldest, items = merged_backlog(sid, key)
            if items and not self._sse("ops", {"last": last, "mpos": mpos,
                                               "oldest": oldest, "items": items}):
                return
        n, beat = 0, time.monotonic()
        while True:
            sdb = API.state_db_for(sid)
            if sdb:
                last2, ops = API.ops_at(sdb, last)
                if ops:
                    last = last2
                    if not self._sse("ops", {"last": last,
                                             "items": opshtml.op_items(ops, key)}):
                        return
            got = plugins.conversation(sid, mpos)
            if got:
                recs, mpos = got
                if recs and not self._sse("msgs", {"mpos": mpos,
                                                   "items": _conv_items(recs)}):
                    return
                st = API.stats_at(sdb)
                if st != prev["stats"]:
                    prev["stats"] = st
                    if not self._sse("stats", st):
                        return
            if n % SLOW_EVERY == 0:
                # a resume moves the session to a NEW kitty window (the
                # SessionStart upsert refreshes the sessions row) — re-resolve,
                # or a stream opened before the move polls the dead window's
                # lingering tab state forever (green while kitty is magenta)
                row = API.session_row(sid) or {}
                win = str(row.get("kitty_window_id") or "") or win
                # resolved up front so the agent cards' inherit-default effort
                # matches the effort quick-button pushed below (one resolve)
                eff = plugins.effort_default(row.get("cwd") or "",
                                             _session_slug(sid))
                agents = agents_model_effort(
                    agents_ctx(visible_agents(API.agents(sid))), eff)
                if agents != prev["agents"]:
                    prev["agents"] = agents
                    if not self._sse("agents", agents):
                        return
                # the main thread's context saturation — the stats row's ctx
                # chip, live (the transcript grew → the (path, size) cache
                # re-probes; pushed only on change like everything else here)
                ctx = session_ctx(row.get("transcript_path") or "", main=True)
                if ctx != prev["ctx"]:
                    prev["ctx"] = ctx
                    if not self._sse("ctx", {"ctx": ctx}):
                        return
                # the header's title, live — a web rename or a fresh auto
                # ai-title shows on the slow cadence (the (path, size)-cached
                # session_title makes the probe a getsize when nothing grew)
                t = session_title(row.get("transcript_path") or "")
                if t != prev["title"]:
                    prev["title"] = t
                    if not self._sse("title", {"title": t}):
                        return
                # the header's git chip, live — a checkout/branch switch (or a
                # removed worktree) shows on the slow cadence
                git = git_info(row.get("cwd") or "")
                if git != prev["git"]:
                    prev["git"] = git
                    if not self._sse("git", {"git": git}):
                        return
                # the effort quick-button, live — a terminal-side /effort
                # saves to settings and shows here on the slow cadence
                # (eff resolved above, before the agent-card stamp)
                if eff != prev["effort"]:
                    prev["effort"] = eff
                    if not self._sse("effort", {"effort": eff}):
                        return
                costs = API.costs(sid)
                if costs != prev["costs"]:
                    prev["costs"] = costs
                    if not self._sse("costs", costs):
                        return
                run = API.running(sid)
                if run != prev["running"]:
                    prev["running"] = run
                    if not self._sse("running", run):
                        return
                # the ⚠ error badge, live: a cheap COUNT (no tracebacks) on the
                # slow cadence, pushed only on change (full rows stay behind
                # /errors). The web sibling of the scorebar's errwatch chip.
                ec = API.error_count(sid)
                if ec != prev["errors"]:
                    prev["errors"] = ec
                    if not self._sse("errors", {"count": ec}):
                        return
                # the monitors tab badge, live: the cheap distinct-monitor COUNT
                # (streams keystone, no transcript parse), pushed on change — a
                # new Monitor launch bumps it. Full monitor detail (command,
                # events) stays behind /monitors, fetched when the tab opens.
                mc = API.monitor_count(sid)
                if mc != prev["monitors"]:
                    prev["monitors"] = mc
                    if not self._sse("monitors", {"count": mc}):
                        return
                # the jobs tab badge, live: the cheap distinct background-job
                # COUNT (streams keystone), pushed on change — a new bg launch
                # bumps it. Full job detail (command, output) stays behind /jobs
                # + /copy, fetched when the tab / drill-down opens.
                jc = API.job_count(sid)
                if jc != prev["jobs"]:
                    prev["jobs"] = jc
                    if not self._sse("jobs", {"count": jc}):
                        return
                # the memory tab badge, live: the distinct-note COUNT from the
                # `memory` kv (plugins.claude_code.memory), pushed on change — a
                # new op under ~/wiki/01 bumps it. Full note list stays behind
                # /memory, note bodies behind /note, fetched when the tab opens.
                memc = API.memory_count(sid)
                if memc != prev["memory"]:
                    prev["memory"] = memc
                    if not self._sse("memory", {"count": memc}):
                        return
                # the pinned tasks card, live — a task create / status flip
                # re-stashes the `tasks` kv (task_fmt.py) and shows on the
                # slow cadence (tasks change per-hook, not per-keystroke;
                # nobody is blocked waiting on this card, unlike ask/plan)
                tasks = _session_tasks(sid)
                if tasks != prev["tasks"]:
                    prev["tasks"] = tasks
                    if not self._sse("tasks", {"tasks": tasks}):
                        return
                # the pinned goal card, live — the active `/goal` scanned from
                # the transcript tail (session_goal, read-side, no hook fires).
                # Slow cadence like tasks: a goal changes per-turn, not per-
                # keystroke, and nobody is blocked waiting on this card
                goal = session_goal(row.get("transcript_path") or "")
                if goal != prev["goal"]:
                    prev["goal"] = goal
                    if not self._sse("goal", {"goal": goal}):
                        return
                # the unsent composer draft — so a composer open on ANOTHER
                # device tracks this one's edits (the writer suppresses its own
                # echo by `origin`; the page skips the repaint while its own
                # box has focus). Slow cadence: a draft is convenience state, no
                # one is blocked on it (unlike the ask/plan dialogs below).
                cdraft = _composer_draft(sid)
                if cdraft != prev["composer_draft"]:
                    prev["composer_draft"] = cdraft
                    if not self._sse("composer-draft", {"draft": cdraft}):
                        return
                # the pending queued-message chips — so a reload / another
                # device restores what the TUI still holds unqueued (slow
                # cadence, convenience state like the draft above)
                cqueue = _composer_queue(sid)
                if cqueue != prev["composer_queue"]:
                    prev["composer_queue"] = cqueue
                    if not self._sse("composer-queue", {"queue": cqueue}):
                        return
            tab = (API.tab_states().get(win) or "") if win else ""
            if tab != prev["tab"]:
                prev["tab"] = tab
                if not self._sse("tab", {"tab": tab}):
                    return
            # the pending modal-dialog cards (fast cadence — the dialog just
            # appeared and the user is waiting); None clears each card
            # change-detect on the RAW stash (a cheap kv read); enrich with the
            # preamble only when it actually changed (_ask_wire reads the
            # transcript — see there)
            ask = _ask_pending(sid)
            if ask != prev["ask"]:
                prev["ask"] = ask
                if not self._sse("ask", {"ask": _ask_wire(sid, ask)}):
                    return
            # the unsubmitted-selections draft — so a card open on ANOTHER
            # device tracks this one's edits (the writer suppresses its own
            # echo by `origin`). Only meaningful while an ask is open;
            # _ask_draft returns None once it's gone, clearing the peer.
            draft = _ask_draft(sid, ask) if ask else None
            if draft != prev["ask_draft"]:
                prev["ask_draft"] = draft
                if not self._sse("ask-draft", {"draft": draft}):
                    return
            plan = _plan_pending(sid)
            if plan != prev["plan"]:
                prev["plan"] = plan
                if not self._sse("plan", {"plan": plan}):
                    return
            # the greyish input-box ghost suggestion (docs/dashboard.md, *Web
            # ghost suggestion*) — the faint "suggested answer" the TUI
            # pre-fills when a turn settles. Screen-scraped (no hook fires for
            # it), so gated hard AND throttled to the slow cadence: only when
            # the tab is settled (done/idle), no modal dialog is pending, and
            # the web composer box is empty (else there's nothing to surface, or
            # the probe would fight a draft the user is editing elsewhere).
            if n % SLOW_EVERY == 0:
                sug = (_suggestion(sid)
                       if (tab in _SUGGEST_TABS and ask is None and plan is None
                           and prev["composer_draft"] is None) else None)
            else:
                sug = prev["suggestion"]
            if sug != prev["suggestion"]:
                prev["suggestion"] = sug
                if not self._sse("suggestion", {"suggestion": sug}):
                    return
            now = time.monotonic()
            if now - beat > HEARTBEAT_S:
                beat = now
                if not self._sse_beat():
                    return
            n += 1
            time.sleep(TICK_S)

    def sse_agent(self, sid, aid, pos):
        """One agent's LIVE drill-down timeline (docs/dashboard.md): appends
        `entries` (new increment entries, server-enriched exactly like the REST
        /agent endpoint — the shared _enrich_entries) and `resolve`
        (cross-increment tool resolutions — [(tool_use_id, output, failed), …]
        the client applies by data-tool-id) events as the agent's transcript
        grows from byte cursor `pos`, plus heartbeats; stops cleanly on client
        disconnect. `pos` is the cursor the /agent REST response handed the
        client, so the first increment resumes exactly where the initial fetch
        stopped — no gap, no overlap. A pair with no incremental provider
        (codex declines) yields None forever, so the loop is a heartbeat-only
        keep-alive until the client navigates away."""
        self._sse_start()
        beat = time.monotonic()
        while True:
            got = plugins.activity_since(sid, aid, pos)
            if got is not None:
                entries, resolutions, pos = got
                if entries:
                    _enrich_entries(entries)
                    if not self._sse("entries", {"pos": pos,
                                                 "entries": entries}):
                        return
                if resolutions:
                    if not self._sse("resolve", {"pos": pos,
                                                 "resolutions": resolutions}):
                        return
            now = time.monotonic()
            if now - beat > HEARTBEAT_S:
                beat = now
                if not self._sse_beat():
                    return
            time.sleep(TICK_S)


def _sid(s):
    return bool(_SID_OK.match(s or ""))


def _qint(url, name):
    try:
        return int((parse_qs(url.query).get(name) or ["0"])[0])
    except ValueError:
        return 0


def _qstr(url, name):
    return (parse_qs(url.query).get(name) or [""])[0]


# --- lifecycle ---------------------------------------------------------------------

UPLOAD_TTL_S = 7 * 24 * 3600      # composer attachments older than this are pruned


def _prune_uploads():
    """Best-effort sweep of stale composer attachments (paths.UPLOADS_DIR) at
    server start — the bytes are only needed until Claude Code has read them, so
    a week is generous. Never raises (it's off the request path, and a failed
    prune must not stop the server from booting); empty per-session subdirs are
    removed too."""
    root = P.UPLOADS_DIR
    now = time.time()
    try:
        subs = os.listdir(root)
    except OSError:
        return
    for sub in subs:
        d = os.path.join(root, sub)
        try:
            for name in os.listdir(d):
                f = os.path.join(d, name)
                try:
                    if now - os.path.getmtime(f) > UPLOAD_TTL_S:
                        os.remove(f)
                except OSError:
                    pass
            if not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass


def serve():
    """Run the server in THIS process (the `serve` CLI verb — `start` spawns
    it detached). Singleton: the paths.DASH_DB pid-lock first, the port bind
    as the second guard. The whole run is one audited stream (kind
    'dashboard') so uptime and the exit path are queryable."""
    res = locks.lock_acquire(P.DASH_DB, LOCK_KEY)
    if res.startswith("claim-denied"):
        A.error("", "dashboard serve (lock denied)", {"res": res})
        return 1
    with stream_lifecycle("", "dashboard", src_path="http://%s:%d" % (HOST, PORT),
                          ctx={"port": PORT},
                          on_exit=lambda: locks.lock_release(P.DASH_DB, LOCK_KEY)) as run:
        try:
            httpd = ThreadingHTTPServer((HOST, PORT), Handler)
        except OSError:
            run.end("port-busy")
            A.error("", "dashboard serve (port busy)", {"port": PORT})
            return 1
        httpd.daemon_threads = True
        _prune_uploads()
        threading.Thread(target=NOTIFIER.run, daemon=True).start()

        def _term(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _term)
        run.end("stopped")                  # the expected exit (SIGTERM/^C);
        try:                                # a crash overwrites it via __exit__
            httpd.serve_forever(poll_interval=0.5)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass
    return 0
