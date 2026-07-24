# dashboard/notify/notifier.py — the tab-diff watcher + /events fan-out.
#
# One daemon thread diffs the global tab DB once a second, pushes the in-page
# toast/OS-notification on every asking/done transition, and drives the deferred
# device-first / Telegram-if-ignored off-device alert (armed on the transition,
# sent only if you didn't react within the grace window). Reads the notify knobs
# LIVE from config (config.NOTIFY_*) and the "need alerting" signals from
# presence — so a test patches config / presence, not this module.
import os
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import quote

from core import sessionapi as API
from core.noaudit import load_audit
from dashboard import askdialog, config, prefs, suggestion, webpush
from dashboard.config import (GLOBAL_TICK_S, NOTIFY_STATES,
                              SESSIONS_LIMIT, SLOW_EVERY)
from dashboard.control import launch
from dashboard.notify import presence
from dashboard.read.meta import canon_cwd, session_title, _group_dir

A = load_audit()


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
        if presence._web_viewing(sid):
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
            if config.NOTIFY_TELEGRAM or config.NOTIFY_WEBPUSH:   # arm the deferred off-device
                self.pending[win] = dict(payload, armed_at=now, state=state)
                # ANCHOR the deferred lifecycle: every armed alert ends in
                # exactly one of suppress / route+send (+escalate) / telegram,
                # all keyed back to this `notify-arm` row (a silent disappearance
                # instead = you reacted, the tab moved off red/green — see the
                # paired tab_transitions row).
                A.state_file("", "", "notify-arm",
                             {"sid": payload.get("sid"), "kind": kind,
                              "phase": "arm", "delay_s": config.NOTIFY_DELAY_S})
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
                    or presence._session_ended(sid) or presence._composing(sid)):
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
            due = entry["escalate_at"] if escalating else entry["armed_at"] + config.NOTIFY_DELAY_S
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
                if config.NOTIFY_TELEGRAM:
                    self._telegram(entry, "escalation")
                continue
            # stage 1: on-device push to the most-recently-used device
            pushed = self._webpush(entry) if config.NOTIFY_WEBPUSH else False
            if pushed and not config.NOTIFY_TELEGRAM_ALWAYS:
                entry["notified"] = now            # arm the escalation, keep pending
                entry["escalate_at"] = now + config.ESCALATE_S
                A.state_file("", "", "notify-arm",
                             {"sid": sid, "kind": entry.get("kind"),
                              "phase": "escalate", "in_s": config.ESCALATE_S})
                continue
            del self.pending[win]
            if config.NOTIFY_TELEGRAM:                     # no device to push to, or _ALWAYS
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
        url = "%s/?s=%s" % (config.NOTIFY_URL_BASE, quote(entry.get("sid") or ""))
        msg = "%s — %s\n%s" % (head, title, url)
        try:
            subprocess.Popen(
                [sys.executable or "python3", config.NOTIFY_CMD, msg],
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
        subs, decision = presence._mru_push_targets()
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
        url = "%s/?s=%s" % (config.NOTIFY_URL_BASE, quote(entry.get("sid") or ""))
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
