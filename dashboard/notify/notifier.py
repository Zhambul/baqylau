# dashboard/notify/notifier.py — the tab-diff watcher.
#
# One daemon thread diffs the global tab DB once a second, pushes the in-page
# toast/OS-notification on every asking/done transition, and drives the deferred
# device-first / Telegram-if-ignored off-device alert (armed on the transition,
# sent only if you didn't react within the grace window). Reads the notify knobs
# LIVE from config (config.NOTIFY_*) and the "need alerting" signals from
# presence — so a test patches config / presence, not this module.
#
# The /events FAN-OUT it publishes on is broker.py, not this class: sse_global
# and launch_wake want a bus, not a watcher.
import os
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
from dashboard.notify.broker import BROKER, Broker
from dashboard.read.meta import canon_cwd, session_title, group_dir

A = load_audit()


class Notifier:
    """The tab-DB diff watcher. Once a second it diffs the global tab table and
    publishes ('notify', payload) on its Broker for every asking/done
    transition (the in-page toast + OS notification). Also keeps the win ->
    session map the payloads are named from (refreshed on the slow cadence —
    sessions come and go rarely).

    It ALSO drives the deferred off-device Telegram alert: each asking/done
    transition arms `self.pending[win]`; a later scan SENDS it iff the tab is
    still in that state after NOTIFY_DELAY_S (you didn't react) and the session
    isn't muted — otherwise the entry is dropped when the tab moves off that
    state, the session ends (you closed it / moved on), or you're composing a
    reply to it (an unsent web draft = you're already on it)."""

    def __init__(self, broker=None):
        # The bus this watcher publishes on. NOTIFIER takes the process-wide
        # singleton; a test-constructed Notifier gets its OWN, so a suite's
        # instances can't feed each other's queues.
        self.broker = broker if broker is not None else Broker()
        self.prev = None               # None = not yet baselined; distinct from
        #                                {} (a real empty screen — all tabs gone)
        self.winmap = {}
        self.pending = {}              # win -> dict(payload, armed_at, state)
        self.fe = None                 # cached Frontend for the dialog-region
        #                                read (refreshed on the slow cadence)

    # The bus surface, kept as delegations: a Notifier IS a publisher to its
    # callers, and the watcher's own pushes read better as self.push(...).
    def register(self):
        return self.broker.register()

    def unregister(self, q):
        self.broker.unregister(q)

    def push(self, event, payload):
        self.broker.push(event, payload)

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
        self.fe = launch.frontend()

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
        is actively viewing the session (a fresh `web_viewing` heartbeat).
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
        if presence.web_viewing(sid):
            return "web-viewing"
        return None

    def _drop(self, win, reason=None):
        """Disarm `self.pending[win]` — the ONE way an armed deferred alert ends
        without being sent. `reason` names it in a `notify-suppress` row; None is
        the deliberate no-row case (you REACTED — the tab left the armed state,
        the session ended, or you're composing a web reply — each of which the
        `tab_transitions` / `sessions` / `composer-draft` rows already explain,
        and the notify-arm anchor documents as the silent-disappearance case).

        Every OTHER drop owes a row: `notify-arm` promises each arm ends in
        exactly one of suppress / route+send / telegram, so an unaudited drop
        for a reason nothing else records is indistinguishable from that
        silent case — which is how a MUTED session's dropped alert used to read
        as "you reacted" (it now files `reason='muted'`)."""
        entry = self.pending.pop(win, None)
        if entry is not None and reason:
            A.state_file("", "", "notify-suppress",
                         {"sid": entry.get("sid"), "kind": entry.get("kind"),
                          "reason": reason})

    def _payload(self, kind, state, row):
        # a worktree session's toast names the PROJECT it groups under, not the
        # worktree dir — the SAME group_dir resolution the list page uses (the
        # frozen start_cwd -> its worktree owner), so a session that cd'd away
        # is still named by where it started (_git_resolve is cached, cheap)
        cwd = canon_cwd(row.get("cwd") or "")
        home = group_dir(canon_cwd(row.get("start_cwd") or "") or cwd)
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
        """One 1 s tick: diff the tab DB, then walk the armed entries twice.
        THREE passes, in this order and no other (each depends on the previous
        one's edits to self.pending):
          1. _arm_transitions — the tab diff: toast every new asking/done
             transition and arm its deferred off-device alert.
          2. _cancel_armed — drop the arms you already reacted to, all BEFORE
             any delay elapses.
          3. _fire_due — send what survived the grace window (on-device push,
             then the Telegram escalation).
        `tree` (one `kitten @ ls`) is resolved once between 1 and 2 and shared by
        both later passes' tab-focus checks — else it costs one subprocess per
        armed session per second."""
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
        self._arm_transitions(cur, prev, now)
        # one ls per scan, shared by every armed entry's tab-focus check (both
        # the done 'seen it' branch and the asking send-time check) — avoids a
        # kitten @ ls per armed session per second. Best-effort.
        try:
            tree = self.fe.ls() if (self.fe and self.pending) else None
        except Exception:
            tree = None
        self._cancel_armed(cur, tree)
        self._fire_due(now, tree)

    def _arm_transitions(self, cur, prev, now):
        """PASS 1 — the tab diff. For every window that just ENTERED an
        asking/done state: push the immediate in-page toast + OS notification,
        and arm `self.pending[win]` for the deferred off-device alert. The global
        alerts switch gates BOTH here (the one suppression site)."""
        for win, state in cur.items():
            kind = NOTIFY_STATES.get(state)
            if not kind or prev.get(win) == state:
                continue
            row = self.winmap.get(win)
            if not row:
                continue
            payload = self._payload(kind, state, row)
            if not prefs.notify_enabled():
                # global alerts toggle is OFF (docs/dashboard.md, *Global alerts
                # toggle*) — the ONE gate covers BOTH the immediate toast/OS
                # notif AND the deferred arm below, and OVERRIDES any per-session
                # mute. Audited so "no alerts at all" is answerable from the DB.
                A.state_file("", "", "notify-suppress",
                             {"sid": payload.get("sid"), "kind": kind,
                              "reason": "global-off"})
                continue
            self.push("notify", payload)   # immediate in-page toast + OS notif
            if config.NOTIFY_TELEGRAM or config.NOTIFY_WEBPUSH:   # arm the deferred off-device
                # `notified`/`escalate_at` are seeded HERE, at the arm, even
                # though only the stage-1 push sets them for real: _fire_due
                # reads entry["escalate_at"] on the escalating branch, and
                # leaving the key absent made that read safe only by an
                # invariant held in another method (escalating <=> notified is
                # not None <=> escalate_at was written). An armed entry now has
                # its whole shape from the start.
                self.pending[win] = dict(payload, armed_at=now, state=state,
                                         notified=None, escalate_at=0.0)
                # ANCHOR the deferred lifecycle: every armed alert ends in
                # exactly one of suppress / route+send (+escalate) / telegram,
                # all keyed back to this `notify-arm` row (a silent disappearance
                # instead = you reacted, the tab moved off red/green — see the
                # paired tab_transitions row). `_drop` is the ONE disarm site and
                # what keeps that promise true.
                A.state_file("", "", "notify-arm",
                             {"sid": payload.get("sid"), "kind": kind,
                              "phase": "arm", "delay_s": config.NOTIFY_DELAY_S})

    def _cancel_armed(self, cur, tree):
        """PASS 2 — cancel the arms you reacted to / are already handling, all
        BEFORE the delay elapses: the tab left its armed state (answered → busy,
        or the win vanished = tab gone), the session ENDED (you closed / quit it
        — moved on, and the alert's deep link would open a dead session), OR
        you're actively COMPOSING a reply to it (a non-empty unsent web draft is
        "I'm on it" — don't nag). ended_at is the robust signal the win-vanish
        check can miss: a stale tab row can linger, and a reused window id can
        even re-match the armed state under a DIFFERENT session. Then the two
        per-kind TERMINAL-activity signals (see the branches)."""
        for win in list(self.pending):
            entry = self.pending[win]
            sid = entry.get("sid")
            if (cur.get(win) != entry["state"]
                    or presence.session_ended(sid) or presence.composing(sid)):
                self._drop(win)                # you reacted — no row (see _drop)
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
                        self._drop(win, "dialog-activity")
            # A green `done` tab is your turn; you replying AT THE TERMINAL —
            # typing a message into the `❯` input box — likewise moves neither
            # the tab off green nor the transcript until you submit, so the
            # checks above miss it. Its trace is REAL (non-faint) content in the
            # input box (a settled tab pre-fills only a FAINT ghost suggestion,
            # which `suggestion.typed` ignores). Drop the arm the moment any is
            # there: you're continuing the conversation in the kitty tab.
            elif entry.get("kind") == "done":
                if self._input_typed(win):
                    self._drop(win, "terminal-input")
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
                        self._drop(win, seen)

    def _fire_due(self, now, tree):
        """PASS 3 — fire the arms that persisted past the grace window (once
        each) — unless, at THIS moment, you're looking at the session (the kitty
        tab is frontmost, or a browser is actively viewing it): then you don't
        need an off-device ping, so drop it with a notify-suppress row. In
        practice that send-time check now matters for `asking` arms: a `done` arm
        that was ever seen was already dropped in _cancel_armed (the 'seen it'
        rule), so a done arm reaching here was never looked at.

        DEVICE-FIRST, TELEGRAM-IF-IGNORED. Two stages per armed entry:
          1. after the grace window, the ON-DEVICE push goes to the one device
             you most recently used (_webpush → mru_push_targets); the entry
             STAYS armed, now with an escalate_at ESCALATE_S in the future.
          2. if it survives to escalate_at — you STILL did nothing with the
             session (any reaction / look already dropped it in _cancel_armed) —
             Telegram nudges you, in case you're away from that device.
        Telegram is instead the IMMEDIATE fallback when there's no device to push
        to (nobody subscribed); `_ALWAYS` fires both at stage 1."""
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
                self._drop(win, watching)
                continue
            if prefs.notify_muted(sid):
                # audited (`muted`) — see _drop: an unaudited drop here read as
                # "you reacted", so a per-session mute silently swallowing the
                # off-device alert was indistinguishable from you answering it.
                self._drop(win, "muted")
                continue
            # the two SEND paths disarm with no suppress row — they are not
            # suppressions, and their own `telegram-notify` / `web-push` rows
            # are the record `notify-arm` promised (see _drop).
            if escalating:                         # stage 2: the Telegram nudge
                self._drop(win)
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
            self._drop(win)
            if config.NOTIFY_TELEGRAM:                     # no device to push to, or _ALWAYS
                self._telegram(entry, "always" if pushed else "no-device")

    def _alert_text(self, entry):
        """The alert pieces both notify channels (Telegram + Web Push) build
        the same way from one `entry`: the 🔴/🟢 headline (project +
        needs-you/is-done), the detail line (the session title, or a
        kind-specific fallback), and the ?s=<sid> deep link. Returns the three
        RAW strings only — each channel composes them differently (Telegram
        joins them into one message; Web Push splits them across the payload's
        title/body), so the joining/escaping stays at the call site.

        ?s=<sid>, NOT the app's #/s/<sid> hash route: Telegram's auto-linker
        drops the URL fragment, so a #-link opens the dashboard ROOT on the
        phone, not the session. The sid rides a query param (linkified whole);
        the page translates ?s=<sid> back into the hash route on load."""
        asking = entry.get("kind") == "asking"
        proj = entry.get("project") or entry.get("sid") or "session"
        head = ("🔴 %s needs you" if asking else "🟢 %s is done") % proj
        detail = entry.get("title") or (
            "Claude is asking a question" if asking else "finished — your turn")
        url = "%s/?s=%s" % (config.NOTIFY_URL_BASE, quote(entry.get("sid") or ""))
        return head, detail, url

    def _telegram(self, entry, reason=None):
        """Send the deferred alert via the reused `notify` skill (Telegram),
        detached so a slow round-trip never stalls the 1 s watcher. Best-effort
        + audited; never raises into the loop. `reason` (in the audit row) says
        WHY Telegram fired: `escalation` (the 5-min nudge after an on-device push
        you ignored), `no-device` (nobody was push-subscribed — the immediate
        fallback), or `always` (`_ALWAYS` forced both) — so a Telegram alert is
        never an unexplained duplicate."""
        head, title, url = self._alert_text(entry)
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
        recently used (`mru_push_targets`) — NOT every subscription, so a
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
        subs, decision = presence.mru_push_targets()
        # The routing decision is audited whenever there was ANYTHING to weigh
        # (at least one subscription) — even the no-target edge — so a missing
        # push is never a mystery. No subs at all = nothing to route, no row.
        if decision.get("n_subs"):
            A.state_file("", "", "notify-route",
                         dict(decision, sid=entry.get("sid"), kind=entry.get("kind")))
        if not subs:
            return False
        title, body, url = self._alert_text(entry)
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



NOTIFIER = Notifier(BROKER)   # publishes on the process-wide bus
