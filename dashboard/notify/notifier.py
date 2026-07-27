# dashboard/notify/notifier.py — the tab-diff watcher: WHEN an alert happens.
#
# One daemon thread diffs the global tab DB once a second, pushes the in-page
# toast on every asking/done transition, drives the off-device alert — routed to
# the device you were LAST ON and fired AT ONCE unless you're plainly there
# (config.NOTIFY_DELAY_S, default 0), then escalated to Telegram if you keep
# ignoring it — and RETRACTS a delivered alert once what it told you stops being
# true. Reads the notify knobs LIVE from config (config.NOTIFY_*) and the "need
# alerting" signals from presence — so a test patches config / presence, not
# this module.
#
# The grace window is what presence REPLACED. It used to be the whole test:
# wait 60 s, and if the tab was still red you must not have reacted. That is a
# guess about attention made by a clock, and it paid for itself twice over —
# once in latency (an alert about a finished turn arriving a minute late), once
# in wrongness (you had walked away 59 s ago; the delay changed nothing except
# when you found out). Presence answers the same question directly — are you at
# this device, is this session in front of you — so the delay's default is now
# 0 and the knob survives only as a debounce for anyone who wants one.
#
# HOW an alert reaches you (and how it is taken back) is channels.py: this
# module never touches a socket, a subprocess or a payload shape. It holds two
# collections and the rules that move entries between them —
#   self.pending  armed, not yet delivered   -> cancelled, or sent
#   self.sent     delivered, still retractable -> retracted, or expired
#
# The /events FAN-OUT it publishes on is broker.py, not this class: sse_global
# and launch_wake want a bus, not a watcher.
import os
import time

from core import sessionapi as API
from core.noaudit import load_audit
from dashboard import askdialog, config, prefs, suggestion
from dashboard.config import (GLOBAL_TICK_S, NOTIFY_STATES,
                              SESSIONS_LIMIT, SLOW_EVERY)
from dashboard.control import launch
from dashboard.notify import channels, presence
from dashboard.notify.broker import BROKER, Broker
from dashboard.read.meta import canon_cwd, session_title, group_dir

A = load_audit()

# ---------------------------------------------------------------- reactions
#
# `_reaction` names every way you can make a red/green alert moot. What differs
# is who ACTS on each name, and these two tables are that difference — the one
# place the distinction is written down rather than implied by control flow.
#
# SILENT: cancelling a pending alert for this reason files no notify-suppress
# row, because something else already records it (a `tab_transitions` row, the
# session's `ended_at`, the `composer-draft` kv). Every other reason owes a row
# — see `_drop`.
SILENT_REASONS = frozenset(("tab-moved", "session-ended", "composing"))
# RETRACT: the reasons that also take back an alert ALREADY DELIVERED. Narrower
# than the cancel set on purpose. A pending alert is cancelled by anything that
# means "you don't need to be told" — including a mere glance (tab-focused /
# web-viewing). A delivered alert may only be retracted by something that means
# "what you were told is no longer true". Glancing at a red tab and walking away
# is the counter-example that decides it: treating a look as a resolution would
# delete your only reminder while the tab is still sitting there asking. The
# screen-scraped signals (dialog-activity / terminal-input) are excluded for a
# duller reason — they cost a `kitten @ get-text` per record per tick, and a
# delivered alert is tracked for hours, not for the 60 s grace window; answering
# at the terminal moves the tab off red seconds later anyway, so `tab-moved`
# catches it for free.
RETRACT_REASONS = frozenset(("tab-moved", "session-ended", "composing"))


def telegram_reason(pushed, target, targets):
    """WHY Telegram fired at stage 1, for its `telegram-notify` row — so a
    Telegram alert is never an unexplained duplicate, and a Telegram alert you
    expected to be a push is never an unexplained ROUTE. Four distinct answers,
    and the last two are the pair worth keeping apart: `push-off` means presence
    DID pick a device and the push channel couldn't deliver (switched off, or no
    crypto backend), `no-device` means there was nothing subscribed to pick."""
    if pushed:
        return "always"                    # _ALWAYS forced both channels
    if target == presence.TERMINAL:
        return "terminal"                  # you were last at the terminal
    if targets:
        return "push-off"                  # a device was routed to; push is off
    return "no-device"                     # nothing subscribed anywhere


class Notifier:
    """The tab-DB diff watcher. Once a second it diffs the global tab table and
    publishes ('notify', payload) on its Broker for every asking/done
    transition (the in-page toast — shown only by the FOCUSED page; the
    on-device system notification is Web Push, at the deferred fire point).
    Also keeps the win -> session map the payloads are named from (refreshed on the slow cadence —
    sessions come and go rarely).

    It ALSO drives the off-device alert: each asking/done transition arms
    `self.pending[win]`, and the same tick SENDS it (NOTIFY_DELAY_S defaults to
    0) unless you are plainly ALREADY THERE — the session's kitty tab in front
    of you, a browser viewing it, or a browser in your hands — and unless the
    session is muted. It goes to the device your PRESENCE says you were last on
    (`presence.route`): a browser gets a Web Push, the terminal gets Telegram.
    An entry is likewise dropped when the tab moves off that state, the session
    ends (you closed it / moved on), or you're composing a reply to it (an
    unsent web draft = you're already on it) — which, after a push, is what the
    5-minute Telegram escalation waits through.

    And it RETRACTS: a sent alert moves to `self.sent` with the handle its
    channel returned, and once the session stops needing you (RETRACT_REASONS)
    the Telegram message is deleted and a resolve push closes the on-device
    banner. Retraction is best-effort and IN-MEMORY: a dashboard restart forgets
    the handles, so an alert delivered before it stays in the chat — the same
    bargain `pending` already makes, and the reason the page's own foreground
    sweep exists as a second line of defence."""

    def __init__(self, broker=None):
        # The bus this watcher publishes on. NOTIFIER takes the process-wide
        # singleton; a test-constructed Notifier gets its OWN, so a suite's
        # instances can't feed each other's queues.
        self.broker = broker if broker is not None else Broker()
        self.prev = None               # None = not yet baselined; distinct from
        #                                {} (a real empty screen — all tabs gone)
        self.winmap = {}
        self.pending = {}              # win -> dict(payload, armed_at, state)
        # Delivered alerts still worth taking back: a LIST, not a win-keyed map
        # like `pending`. One window can hold two at once — stage 1's push and
        # stage 2's Telegram escalation are separate deliveries of the same
        # alert, each with its own handle — and a keyed map would silently drop
        # one of them. Kept in send order, so the cap trims the oldest.
        self.sent = []                 # [dict(payload, sent_at, handle)]
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

    def _poll_terminal(self, tree=None):
        """Record "you are AT THE TERMINAL right now" when the terminal app is
        frontmost — the beat a terminal cannot send for itself, and the reason
        the terminal can compete with your browsers for an alert at all
        (`presence.route`). Gated on `winmap`: with no live session there is
        nothing to alert about, so the poll's subprocess would buy nothing.

        Called twice per lifecycle, both cheap: from `scan` with the `ls()` that
        tick ALREADY paid for (free, and exactly when an alert is being decided
        — the presence that matters most is the presence at the transition), and
        from the SLOW cadence with its own `ls()`, which is what keeps a
        HISTORY: "I was at the terminal two minutes ago" cannot be recovered
        after the fact, and it is the whole answer when the tab goes red while
        you're in another app on the same machine."""
        if not (self.fe and self.winmap):
            return
        try:
            if self.fe.app_focused(tree):
                presence.mark_terminal()
        except Exception:
            pass                           # best-effort: no channel = no presence

    def _watching(self, win, sid, tree=None):
        """You are LOOKING AT this session (or holding the device it would land
        on), so the alert would only nag. Three channels, and the asymmetry
        between them is deliberate:
        - the kitty TAB is frontmost on your screen (`fe.tab_focused` —
          `is_focused`, so a web-spawned synthetic tab in a BACKGROUNDED kitty
          does NOT count, verified empirically),
        - a BROWSER is actively viewing the session (a fresh `web_viewing`
          heartbeat),
        - or ANY browser is in your hands right now (`presence.device_active`)
          — because a focused page shows the in-page toast for EVERY session, so
          an off-device push would be a second copy of a notification you just
          got. There is no terminal equivalent of that third channel: kitty
          being frontmost says nothing about a tab you are NOT on, so at the
          terminal only the per-session `tab_focused` counts as having seen it.
        Returns the suppress reason (`tab-focused` / `web-viewing` /
        `device-active`) or None; best-effort — a terminal read miss / no
        channel degrades to None.

        Called from two places with different meanings (see scan): for a `done`
        arm it runs EVERY scan while armed, so a single glance any time before
        the send ('I saw the final message') cancels the alert even after you
        move on; for an `asking` arm it runs only at SEND time ('are you looking
        RIGHT NOW'), because a glance that didn't ANSWER still needs the ping.
        With the delay at 0 those two collapse into the same instant for the
        FIRST send — the distinction still earns its keep across the escalation
        window, where a glance cancels a `done` nudge and does not cancel an
        unanswered `asking` one.
        `tree` is a pre-fetched `ls()` shared across a scan's entries so the tab
        check costs one `kitten @ ls` per scan, not one per armed session."""
        try:
            if self.fe and win and self.fe.tab_focused(win, tree):
                return "tab-focused"
        except Exception:
            pass
        if presence.web_viewing(sid):
            return "web-viewing"
        if presence.device_active():
            return "device-active"
        return None

    def _reaction(self, entry, cur, tree, screen=True):
        """Has this alert stopped being worth delivering — and if so, WHY? The
        single owner of that question; `_cancel_armed` and `_retract_resolved`
        differ only in which answers they act on (SILENT_REASONS /
        RETRACT_REASONS above). Returns a reason name or None.

        Checked cheapest-first, and the order is also a precedence: the tab
        moving off its alerted state is the strongest signal there is, so it
        wins over anything a screen read might say.

        `screen=False` skips the two scraped signals (each a `kitten @ get-text`
        subprocess). The retraction pass runs with it off — it tracks entries for
        hours, where the cancel pass only ever tracks them across the 60 s grace
        window."""
        win, sid = entry.get("win"), entry.get("sid")
        if cur.get(win) != entry["state"]:
            # answered -> busy, or the win vanished (tab gone). `ended` below is
            # the robust companion this check can miss: a stale tab row can
            # linger, and a REUSED window id can even re-match the armed state
            # under a different session.
            return "tab-moved"
        if presence.session_ended(sid):
            return "session-ended"        # you closed / quit it — moved on, and
            #                               the deep link would open a dead one
        if presence.composing(sid):
            return "composing"            # unsent web draft = "I'm on it"
        if not screen:
            return None
        if entry.get("kind") == "asking":
            # You answering AT THE TERMINAL — typing a free-text answer or
            # toggling a selection — moves neither the tab off red nor the
            # transcript (the dialog is still open, unsubmitted), so nothing
            # above fires. Its ONLY trace is the dialog region changing.
            # Baseline it on first sighting (the untouched dialog), then report
            # the moment it differs: you're on it, don't nag.
            reg = self._dialog_region(win)
            if reg:                          # "" = no ask dialog / read miss
                if entry.get("ask_region") is None:
                    entry["ask_region"] = reg
                elif reg != entry["ask_region"]:
                    return "dialog-activity"
        elif entry.get("kind") == "done":
            # A green `done` tab is your turn; you replying AT THE TERMINAL —
            # typing into the `❯` box — likewise shows up nowhere until you
            # submit. Its trace is REAL (non-faint) content in the input box (a
            # settled tab pre-fills only a FAINT ghost suggestion, which
            # `suggestion.typed` ignores).
            if self._input_typed(win):
                return "terminal-input"
            # "If I've SEEN the final message, no notification." A done tab's
            # final message is on screen the moment it goes green, so ANY glance
            # during the grace means you saw it — checked every scan, so a
            # glance that has since ended still counts. Weakest signal in the
            # table, and the reason RETRACT_REASONS excludes it.
            return self._watching(win, sid, tree)
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

    def _hold(self, entry, reason):
        """Defer an armed alert because you are AT the session right now — the
        third outcome beside `_drop` and a send, and the only one that leaves
        the entry armed. Audited ONCE per arm (`notify-arm` `phase:hold`), not
        once per tick: a hold repeats every second for as long as you sit there,
        and the useful fact is that the alert waited AT ALL, plus what for. It
        rides the ARM vocabulary rather than notify-suppress deliberately — a
        suppress row says "this alert will never be sent", which is the one
        thing a hold does not mean.

        TWO flags, not one, because they answer different questions: `holding`
        is the CURRENT state (pass 2 reads it to skip the screen scrapes) and
        must go false again the moment you leave, while `held` is a latch that
        never resets (the audit fired once). Folding them together let an alert
        that was held for a second lose its terminal-answering cancels for the
        whole escalation window afterwards."""
        entry["holding"] = True
        if entry.get("held"):
            return
        entry["held"] = True
        A.state_file("", "", "notify-arm",
                     {"sid": entry.get("sid"), "kind": entry.get("kind"),
                      "phase": "hold", "reason": reason})

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
        """One 1 s tick: diff the tab DB, then walk the tracked entries.
        FOUR passes, in this order and no other (each depends on the previous
        one's edits to self.pending):
          1. _arm_transitions — the tab diff: toast every new asking/done
             transition and arm its off-device alert.
          2. _cancel_armed — drop the arms you already reacted to. With the
             delay at its default 0 an arm is normally sent the same tick, so
             what this pass really guards is the window AFTER a push, where the
             entry sits armed for its Telegram escalation: reacting there is
             what stops the nudge.
          3. _fire_due — send what survived (routed on-device push, then the
             Telegram escalation), recording each delivery in self.sent.
          4. _retract_resolved — take back the deliveries whose session no
             longer needs you, and expire the rest. Reads self.sent, which is
             disjoint from self.pending, so it is last only for readability:
             an alert delivered by pass 3 this very tick cannot be resolved
             before the next one.
        `tree` (one `kitten @ ls`) is resolved once between 1 and 2 and shared by
        the later passes' tab-focus checks — else it costs one subprocess per
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
        if tree is not None:
            self._poll_terminal(tree)      # free: this tick already paid for ls
        self._cancel_armed(cur, tree)
        self._fire_due(now, tree)
        self._retract_resolved(cur, now)

    def _arm_transitions(self, cur, prev, now):
        """PASS 1 — the tab diff. For every window that just ENTERED an
        asking/done state: push the immediate in-page toast, and arm
        `self.pending[win]` for the deferred off-device alert. The global alerts
        switch gates BOTH here (the one suppression site)."""
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
            self.push("notify", payload)   # immediate in-page toast (focused page)
            if config.NOTIFY_TELEGRAM or config.NOTIFY_WEBPUSH:   # arm the deferred off-device
                # `notified`/`escalate_at` are seeded HERE, at the arm, even
                # though only the stage-1 push sets them for real: _fire_due
                # reads entry["escalate_at"] on the escalating branch, and
                # leaving the key absent made that read safe only by an
                # invariant held in another method (escalating <=> notified is
                # not None <=> escalate_at was written). An armed entry now has
                # its whole shape from the start.
                # `win` rides IN the entry as well as keying the map: an entry
                # outlives that key (a delivered one moves to the unkeyed
                # `self.sent` list), and `_reaction` needs it either way.
                self.pending[win] = dict(payload, win=win, armed_at=now,
                                         state=state, notified=None,
                                         escalate_at=0.0)
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
        BEFORE the delay elapses. Every reason `_reaction` can name cancels a
        PENDING alert (that predicate is exactly "you don't need to be told");
        SILENT_REASONS decides which of them owes a notify-suppress row.

        An entry HOLDING (pass 3 last tick: you are in front of the session) skips
        the screen scrapes, which is what keeps an unbounded hold from costing
        an unbounded `kitten @ get-text` per second. They are redundant exactly
        then, and the argument is worth writing down because it is the whole
        justification: those two signals detect you acting AT THE TERMINAL while
        nothing else can see it — but if you are typing at the terminal, that
        tab is frontmost, which is one of the things holding the alert in the
        first place; and when you finish, the tab leaves red/green and
        `tab-moved` catches it. Their unique contribution is when the terminal
        is NOT in front of you, and then the entry is not held."""
        for win in list(self.pending):
            entry = self.pending[win]
            reason = self._reaction(entry, cur, tree,
                                    screen=not entry.get("holding"))
            if reason:
                self._drop(win, None if reason in SILENT_REASONS else reason)

    def _fire_due(self, now, tree):
        """PASS 3 — fire the arms that are due (once each) — unless, at THIS
        moment, you are plainly there (`_watching`): the kitty tab in front of
        you, a browser viewing the session, or a browser in your hands. Then the
        alert is HELD, not dropped: it stays armed and goes out the moment you
        stop being there.

        Holding rather than dropping is what the zero delay COST. With a 60 s
        grace, "you were looking at the instant we'd have pinged" was a fair
        proxy for "you're handling it". At delay 0 that instant is the
        transition itself, and dropping there means a question that turned red
        while you happened to be at a browser is never mentioned again — you
        glance at the toast, walk away, and the reminder you needed died with
        the glance. Seeing a question is not answering it. So a look now defers
        the alert instead of cancelling it, and closing the laptop is what
        delivers it.

        This is the `asking` rule; a `done` arm never reaches it, because
        _cancel_armed already dropped a seen one per-scan (the "if I've SEEN the
        final message, don't tell me" rule — the deliberate asymmetry between
        the two kinds). Written kind-agnostically anyway: holding a `done` arm
        would be harmless (pass 2 drops it on the next tick), where a stray drop
        here would silently lose an alert.

        PRESENCE-ROUTED, TELEGRAM-IF-IGNORED. Two stages per armed entry:
          1. the alert goes to the device you were LAST ON (`presence.route`) —
             a browser (Web Push to that device's subscriptions) or the TERMINAL
             (Telegram: nothing else reaches a machine whose browser is shut).
             After a push the entry STAYS armed with an escalate_at ESCALATE_S
             in the future; after a Telegram it is done (see below).
          2. if it survives to escalate_at — you STILL did nothing with the
             session (any reaction / look already dropped it in _cancel_armed) —
             Telegram nudges you, in case you're away from that device.
        There is no stage 2 after a stage-1 Telegram, and that is the point
        rather than an omission: escalation exists to reach a channel the first
        send couldn't, and Telegram already reaches every device you own. A
        second identical message five minutes later carries no new information.
        `_ALWAYS` fires both at stage 1."""
        for win in list(self.pending):
            entry = self.pending[win]
            escalating = entry.get("notified") is not None
            due = entry["escalate_at"] if escalating else entry["armed_at"] + config.NOTIFY_DELAY_S
            if now < due:
                continue
            sid = entry.get("sid")
            # you are there RIGHT NOW: hold the alert, don't cancel it (above).
            watching = self._watching(win, sid, tree)
            if watching:
                self._hold(entry, watching)
                continue
            entry["holding"] = False           # …and you've stopped being there
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
            # stage 1: route to the device you were last on. The decision is
            # audited UNCONDITIONALLY (unlike the old push-only routing, which
            # skipped the row when nothing was subscribed): with the terminal in
            # the running there is always a choice being made, and "why did this
            # go to Telegram" deserves the same evidence as "why did the iPad
            # buzz" — one row per alert, naming every candidate's presence age.
            target, targets, decision = presence.route()
            A.state_file("", "", "notify-route",
                         dict(decision, sid=sid, kind=entry.get("kind")))
            pushed = (self._webpush(entry, targets)
                      if (targets and config.NOTIFY_WEBPUSH) else False)
            if pushed and not config.NOTIFY_TELEGRAM_ALWAYS:
                # NOTE the entry stays PENDING here while a delivery of it is
                # already tracked in self.sent — the one moment both collections
                # hold the same alert. That is correct and independent: a
                # reaction now cancels the escalation (pass 2) AND retracts the
                # push already on your phone (pass 4).
                entry["notified"] = now            # arm the escalation, keep pending
                entry["escalate_at"] = now + config.ESCALATE_S
                A.state_file("", "", "notify-arm",
                             {"sid": sid, "kind": entry.get("kind"),
                              "phase": "escalate", "in_s": config.ESCALATE_S})
                continue
            self._drop(win)
            if config.NOTIFY_TELEGRAM:            # the terminal, no device, or _ALWAYS
                self._telegram(entry, telegram_reason(pushed, target, targets))

    def _track(self, entry, handle):
        """Remember one DELIVERY so it can be taken back. `handle` is opaque
        (channels.py owns its shape); None means the channel delivered nothing
        retractable — a legacy script send, or no subscription — and there is
        simply nothing to track.

        The record is a COPY of the entry: pass 3 is about to drop the original
        from `pending`, and for the stage-1 push it keeps mutating it besides."""
        if handle is None:
            return
        self.sent.append(dict(entry, sent_at=time.monotonic(), handle=handle))
        while len(self.sent) > config.SENT_CAP:
            self._forget(self.sent[0], "capped")

    def _forget(self, rec, reason):
        """Stop tracking a delivery WITHOUT retracting it, and say so: the alert
        is still out there, we've just stopped waiting for a resolution. Both
        callers are bounds, not outcomes (the TTL and the cap), so this is the
        one row that means 'a notification was left behind' — and therefore the
        one worth alerting on in the audit."""
        try:
            self.sent.remove(rec)
        except ValueError:                 # already forgotten — nothing to do
            return
        A.state_file("", "", "notify-retract",
                     {"sid": rec.get("sid"), "kind": rec.get("kind"),
                      "channel": (rec.get("handle") or {}).get("ch"),
                      "reason": reason, "outcome": "expired", "ok": False,
                      "age_s": round(time.monotonic() - rec.get("sent_at", 0), 1)})

    def _retract_resolved(self, cur, now):
        """PASS 4 — take back the delivered alerts whose premise is gone
        (docs/dashboard.md, *Alert retraction*). Only RETRACT_REASONS count:
        the narrower question, not the "you don't need to be told" one pass 2
        asks — see the table at the top of this module.

        Runs with `screen=False`: no `kitten @ get-text` per record per tick,
        because these are tracked for hours. A channel that answers PENDING —
        the send thread hasn't got its message id home yet, or the delete is
        still in flight — is simply asked again next tick.

        The TTL is checked on EVERY path, settled or not. It would read more
        naturally as the else-branch of "did it resolve", but then a record that
        answered PENDING forever (a wedged sender thread) would never age out —
        a bound that holds only while another thread behaves is not a bound.
        Telegram's own 48 h delete window is the ceiling config.RETRACT_S sits
        under."""
        for rec in list(self.sent):
            reason = self._reaction(rec, cur, None, screen=False)
            outcome = None
            if reason in RETRACT_REASONS:
                outcome = channels.retract(rec["handle"], reason,
                                           self._needs_you_count())
                if outcome == channels.PENDING:
                    outcome = None         # not settled — let the TTL judge it
            if outcome is None:
                if now - rec.get("sent_at", 0) >= config.RETRACT_S:
                    self._forget(rec, "ttl")
                continue
            try:
                self.sent.remove(rec)
            except ValueError:             # pragma: no cover - concurrent forget
                continue
            if outcome == channels.NOTHING:
                continue                   # nothing was ever delivered, no row
            A.state_file("", "", "notify-retract",
                         {"sid": rec.get("sid"), "kind": rec.get("kind"),
                          "channel": rec["handle"].get("ch"), "reason": reason,
                          "outcome": outcome,
                          "ok": outcome in (channels.OK, channels.GONE),
                          "age_s": round(now - rec.get("sent_at", 0), 1)})

    def _telegram(self, entry, reason=None):
        """Send the alert to Telegram (channels.send_telegram) and track
        whatever it returns. `reason` says WHY Telegram fired — `terminal` (you
        were last at the terminal, and Telegram is what reaches that machine),
        `escalation` (the 5-min nudge after an on-device push you ignored),
        `no-device` (nobody was push-subscribed), `always` (_ALWAYS forced
        both) — so a Telegram alert is never an unexplained duplicate."""
        self._track(entry, channels.send_telegram(entry, reason))

    def _needs_you_count(self):
        """How many LIVE sessions need you (red asking + green done) right now —
        the app-icon badge count (docs/dashboard.md *Installed-app polish*),
        carried in the push so the service worker sets the badge while the app is
        closed.

        The `live` half is load-bearing, not a refinement. The tab DB is keyed by
        kitty WINDOW and its rows outlive the sessions that wrote them: red and
        green are RESTING states, so a session whose terminal went away without a
        SessionEnd (kitty quit, window closed mid-turn) leaves its row sitting on
        one forever. Counting the raw table therefore counts history — measured
        148 on a machine with exactly 1 session actually asking. The badge is
        supposed to answer "how many things are waiting for me", the browser's own
        `needsYouCount` has always filtered on `r.live`, and the pushed number
        silently disagreed with it: an icon stuck at three digits that no
        retraction could ever bring down, since decrementing 149 to 148 is
        invisible. `winmap` is the win -> session map (newest session wins a
        window), so intersecting it with the live tab states asks the same
        question the page does."""
        try:
            states = API.tab_states()
        except Exception:
            return 0
        return sum(1 for win, row in self.winmap.items()
                   if row.get("live") and states.get(win) in NOTIFY_STATES)

    def _webpush(self, entry, targets):
        """Send the on-device alert to `targets` (channels.send_webpush) and
        track it. Returns True iff it DISPATCHED to at least one subscription —
        the signal `_fire_due` uses to hold Telegram back to the escalation
        nudge (the routed device first, Telegram only if you keep ignoring
        it)."""
        handle = channels.send_webpush(entry, targets, self._needs_you_count())
        self._track(entry, handle)
        return handle is not None

    def run(self):
        n = 0
        while True:
            try:
                if n % SLOW_EVERY == 0:
                    self.refresh_winmap()
                    self._poll_terminal()  # …and its own ls, to keep a history
                self.scan()
            except Exception:
                A.error("", "dashboard notifier")
                time.sleep(5)              # a broken poll must not spin-audit
            n += 1
            time.sleep(GLOBAL_TICK_S)



NOTIFIER = Notifier(BROKER)   # publishes on the process-wide bus
