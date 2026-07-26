# dashboard/http/sse.py — the Server-Sent-Events streams.
#
# sse_global (the sessions list + notification fan-out), sse_session (one
# session's mirror/scoreboard/cards deltas), sse_agent (a subagent's timeline) —
# long-lived generators polling the read model + the notify BROKER's queue.
import collections
import queue
import time

import plugins
from core import paths as P
from core import sessionapi as API
from core import tabs
from core.noaudit import load_audit
from dashboard import config
from dashboard import prefs
from dashboard.config import (BOOT_ID, HEARTBEAT_S, SLOW_EVERY, TICK_S)
from dashboard.control import launch
from dashboard.notify.broker import BROKER
from dashboard.read.lists import (sessions_payload,
                                  row_key, wire_row)
from dashboard.read.meta import (cmd_names, git_info, session_ctx, session_goal,
                                 session_title, session_slug)
from dashboard.read.mirror import (merged_backlog, merge_live, enrich_entries)
from dashboard.read.session import (BADGES, agents_ctx, agents_model_effort,
                                    visible_agents, ask_draft,
                                    ask_pending, ask_wire, composer_draft, composer_queue,
                                    plan_pending, session_tasks,
                                    input_box, SUGGEST_TABS)

A = load_audit()

_UNSET = object()               # "no explicit payload" — _push_changed sends the compared value


# The tab-badge counts pushed on the slow cadence ride the read model's own
# BADGES table (dashboard/read/session.py) — one row per badge carrying BOTH
# names the fact travels under: the payload field (`error_count`) and the SSE
# event (`errors`). Each is a CHEAP count (an audit COUNT / the streams
# keystone / a kv read — never a transcript parse), wired here to a
# {"count": n} event, sent only on change.
#
# Four copies of one shape were four near-identical stanzas 30 lines long; as a
# table, adding a badge is a row and the shape can't drift. It used to be a
# table HERE, though, which left the counts enumerated twice server-side — once
# as events in this file and once as `*_count` keys in session_payload — so a
# new badge meant two edits in two vocabularies with nothing saying so. The
# table moved to the read model (which owns the memory badge's project SCOPE
# gate anyway) and this file now only teaches the stream how to push it.


# --- the per-session stream's pushed CHANNELS ---------------------------------
# One row per field sse_session pushes on change: the `prev`-map key holding its
# last-sent value, the SSE event name, a producer over the per-tick context
# (_Tick), and the wire WRAPPER — a field name when the value rides inside a
# one-key dict ({"tab": tab}), None when it goes on the wire verbatim.
#
# A TABLE rather than twenty hand-written `if not self._push_changed(...):
# return` stanzas (styleguide: registries over ladders). Three things follow
# from it: the `prev` map is DERIVED (`_prev_map`) instead of a hand-maintained
# literal that had already drifted — `view_mode` was pushed but never listed —
# adding a channel is one row, and the loop body stops carrying twenty locals in
# one flat 200-line scope. That scope was not cosmetic: the badge stanza it
# replaces was a `for key, count in ...: n = count(...)` nested INSIDE the tick
# loop, and it clobbered both the session's mirror-log `key` (so every
# live-streamed block's ⧉ copy / click-to-view link was stamped with the last
# badge name instead of the session) and the tick counter `n` (so the slow
# cadence became a function of the memory-note count). As rows there are no
# loop variables left to collide.
_Chan = collections.namedtuple("_Chan", "key event value wrap")


def _badge_chan(badge):
    """One badge as a channel row ({"count": n}, event named for the badge). A
    named factory, not an inline lambda: the producer must close over THIS
    row's callable, not over the comprehension's loop variable (ruff B023).
    The `prev` key is the badge's PAYLOAD field, so a connection's last-sent map
    reads in the same vocabulary the initial payload used."""
    return _Chan(badge.field, badge.event,
                 lambda c: badge.count(c.sid, c.cwd), "count")


def _badge_chans():
    """The tab-badge counts as channel rows — derived from the read model's
    BADGES table, which owns the (event, field, count) triple."""
    return tuple(_badge_chan(b) for b in BADGES)


# SLOW cadence (every SLOW_EVERY ticks): re-resolves, transcript probes, counts
# and convenience state — nothing here is something a user waits on. ORDER is
# preserved from the stanzas these replace (a client reads the events in
# arrival order).
_SLOW_CHANS = (
    _Chan("agents", "agents",
          lambda c: agents_model_effort(
              agents_ctx(visible_agents(API.agents(c.sid))), c.eff), None),
    # the main thread's context saturation — the stats row's ctx chip, live
    # (the transcript grew → the (path, size) cache re-probes)
    _Chan("ctx", "ctx", lambda c: session_ctx(c.tpath, main=True), "ctx"),
    # the header's title — a web rename or a fresh auto ai-title (the
    # (path, size)-cached session_title makes the probe a getsize when
    # nothing grew)
    _Chan("title", "title", lambda c: session_title(c.tpath), "title"),
    # the header's git chip — a checkout/branch switch, or a removed worktree
    _Chan("git", "git", lambda c: git_info(c.cwd), "git"),
    # the effort quick-button — a terminal-side /effort saves to settings and
    # shows up here (c.eff is resolved once per slow tick, before the agent
    # cards' inherit-default stamp reads the same value)
    _Chan("effort", "effort", lambda c: c.eff, "effort"),
    _Chan("costs", "costs", lambda c: API.costs(c.sid), None),
    _Chan("running", "running", lambda c: API.running(c.sid), None),
) + _badge_chans() + (
    # the pinned tasks card — a task create / status flip re-stashes the
    # `tasks` kv (task_fmt.py). Slow: tasks change per-hook, not per-keystroke,
    # and nobody is blocked waiting on this card, unlike ask/plan
    _Chan("tasks", "tasks", lambda c: session_tasks(c.sid), "tasks"),
    # the pinned goal card — the active `/goal` scanned from the transcript
    # tail (read-side, no hook fires). Slow, for the same reason as tasks
    _Chan("goal", "goal", lambda c: session_goal(c.tpath), "goal"),
    # the unsent composer draft — so a composer open on ANOTHER device tracks
    # this one's edits (the writer suppresses its own echo by `origin`; the
    # page skips the repaint while its own box has focus). Slow: a draft is
    # convenience state, no one is blocked on it (unlike the dialogs)
    _Chan("composer_draft", "composer-draft",
          lambda c: composer_draft(c.sid), "draft"),
    # the pending queued-message chips — so a reload / another device restores
    # what the TUI still holds unqueued (convenience state, like the draft)
    _Chan("composer_queue", "composer-queue",
          lambda c: composer_queue(c.sid), "queue"),
    # the mirror's VIEW MODE — so switching density on the phone re-renders the
    # desktop page already open on this session, instead of it holding the old
    # mode until a reload. The pref itself has always been server-side and
    # per-session (dashboard/prefs.py, docs/dashboard.md *View modes*); this is
    # only what makes an OPEN page follow it. A prefs read is a tiny kv SELECT
    _Chan("view_mode", "view-mode", lambda c: prefs.view_mode(c.sid), "mode"),
)

# FAST cadence (every tick): the two fields whose LATENCY is the point. `fgrun`
# resembles the `running` ribbon but cannot join it — the elapsed counts
# client-side, so what the event is really for is the START and the END, and on
# the slow cadence a finished command would keep counting for seconds after its
# "■ finished · 3.2s" chip already landed. One hand-off peek per tick (a single
# indexed SELECT + a pid probe), pushed only on change.
_FAST_CHANS = (
    _Chan("tab", "tab", lambda c: c.tab, "tab"),
    _Chan("fgrun", "fgrun", lambda c: API.fg_running(c.sid), "fg"),
)

# Channels that stay INLINE in the loop and so own their own `prev` slot: the
# dialogs (`ask`'s wire payload is a transcript read, built only when the raw
# stash changed; `ask_draft` is meaningful only while an ask is open), the
# gated ghost `suggestion`, `stats` (pushed off the conversation cursor, not a
# cadence), and `term_box` — which is never SENT at all, only remembered, as
# the terminal-draft sync's previous-value slot.
_INLINE_KEYS = ("stats", "ask", "ask_draft", "plan", "suggestion", "term_box")


def _prev_map():
    """A fresh last-sent map for one connection: every channel key plus the
    inline ones, all None. DERIVED from the tables — the literal it replaces
    was hand-maintained and had already lost `view_mode`."""
    return dict.fromkeys(
        [c.key for c in _SLOW_CHANS + _FAST_CHANS] + list(_INLINE_KEYS))


class _Tick:
    """The per-tick facts the channel producers read — the single mutable
    context object a named-phase loop shares (styleguide, *Module shape*).
    `sdb`/`tab` are refreshed every tick; the slow prologue additionally
    re-resolves the session row and stamps `win`/`cwd`/`tpath`/`eff`."""

    __slots__ = ("sid", "sdb", "win", "cwd", "tpath", "eff", "tab")

    def __init__(self, sid, row):
        self.sid = sid
        self.sdb = self.tab = self.win = self.eff = ""
        self.adopt(row)

    def adopt(self, row):
        """Take the per-session facts off a freshly-read `sessions` row. `win`
        is sticky: a resume moves the session to a NEW kitty window, but a row
        that momentarily reports none must not blank the one we have."""
        self.win = str(row.get("kitty_window_id") or "") or self.win
        self.cwd = row.get("cwd") or ""
        self.tpath = row.get("transcript_path") or ""


class _SseMixin:

    def _push_changed(self, prev, key, event, value, payload=_UNSET):
        """Push-if-changed: send `event` iff `value` differs from prev[key],
        stamping prev BEFORE the send (exactly as the folded inline blocks did,
        so a client-drop mid-send leaves prev advanced the same way). `payload`
        is what goes on the wire — it defaults to `value` for the sites that
        send the compared value verbatim; the sites that wrap the value in a
        dict pass it explicitly (a plain literal over the already-computed
        local, so building it every tick is side-effect free). Returns False
        when the client dropped — the caller MUST `return` on False.

        Sites whose payload is EXPENSIVE or gated (e.g. `ask`'s ask_wire
        transcript read, built only when the ask changed) stay INLINE — folding
        them here would build the payload on every unchanged tick."""
        if value != prev.get(key):
            prev[key] = value
            if not self._sse(event, value if payload is _UNSET else payload):
                return False
        return True

    def _push_chans(self, chans, prev, ctx):
        """Push every channel in `chans` that changed, in table order. Returns
        False when the client dropped — the caller MUST `return` on False, the
        same contract as _push_changed and _keepalive."""
        for ch in chans:
            value = ch.value(ctx)
            payload = _UNSET if ch.wrap is None else {ch.wrap: value}
            if not self._push_changed(prev, ch.key, ch.event, value, payload):
                return False
        return True

    def _keepalive(self, beat, force=False):
        """The per-tick keep-alive: send a heartbeat comment iff HEARTBEAT_S has
        passed since `beat` (or `force` — sse_global beats right after it drains
        the notifier queue, so an idle proxy sees traffic on the same tick it
        forwarded an event). Returns (new beat, client-still-there); the caller
        MUST `return` on a False, same contract as _push_changed and for the same
        reason — every one of the three loops owed both halves of this, and an
        unchecked _sse_beat leaks a dead connection's thread until the next
        write."""
        now = time.monotonic()
        if not force and now - beat <= HEARTBEAT_S:
            return beat, True
        return now, self._sse_beat()

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
        (row_key) and rows are wire-stripped (wire_row)."""
        self._sse_start()
        q = BROKER.register()
        try:
            if not self._sse("hello", {"boot": BOOT_ID}):
                return
            beat = time.monotonic()
            wire = [wire_row(r) for r in sessions_payload()]
            if not self._sse("sessions", wire):
                return
            keys = {r["sid"]: row_key(r) for r in wire}
            while True:
                drained = False
                try:
                    while True:
                        ev, payload = q.get(timeout=config.GLOBAL_TICK_S)
                        drained = True
                        if not self._sse(ev, payload):
                            return
                except queue.Empty:
                    pass
                wire = [wire_row(r) for r in sessions_payload()]
                cur = {r["sid"]: row_key(r) for r in wire}
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
                beat, alive = self._keepalive(beat, drained)
                if not alive:
                    return
        finally:
            BROKER.unregister(q)

    def sse_session(self, sid, after, mpos=0):
        """One session's live stream. The `ops` event carries rendered HTML —
        ops AND the main-thread conversation from byte cursor `mpos`,
        interleaved by ts via merge_live so a turn's text keeps its place
        relative to its command; a FRESH connection (after=0, mpos=0) gets the
        ts-merged backlog as its first one and a reconnect resumes both cursors.
        The delta merge is the increment-side twin of the backlog merge, so live
        and reload agree (they diverged once — see docs/dashboard.md, the
        ts-interleave note).

        Everything else the stream pushes is a CHANNEL — see `_SLOW_CHANS` /
        `_FAST_CHANS` for the table and its cadences, and `_INLINE_KEYS` for the
        four that stay in this loop because their payload is expensive, gated,
        or (term_box) never sent at all. Every channel is sent only on change.

        The loop is three named phases per tick: `mirror` (the two cursors),
        `slow`/`fast` (the channel tables), `dialogs` (the inline four)."""
        self._sse_start()
        last = after
        prev = _prev_map()
        row = API.session_row(sid) or {}
        ctx = _Tick(sid, row)
        # The mirror-log KEY of this session — what the ⧉ copy / click-to-view
        # links in every rendered op are stamped with. A tick-loop LOCAL once,
        # which the badge stanza's `for key, ...` then clobbered from the second
        # tick on; it lives on nothing but this name now, and no loop in here
        # binds it (see the _SLOW_CHANS header).
        key = P.sid_from_log(row.get("log") or P.mirror_log(sid))
        if not after and not mpos:
            last, mpos, oldest, items = merged_backlog(sid, key)
            if items and not self._sse("ops", {"last": last, "mpos": mpos,
                                               "oldest": oldest, "items": items}):
                return
        tick, beat = 0, time.monotonic()
        while True:
            # -- mirror: the two cursors, interleaved into ONE ops event ------
            ctx.sdb = API.state_db_for(sid)
            last2, ops = API.ops_at(ctx.sdb, last) if ctx.sdb else (last, [])
            # Poll BOTH cursors, then interleave the delta by ts into ONE event
            # (merge_live) — emitting ops and msgs as two separate arrival-order
            # events prepended a turn's preceding text ABOVE its command in the
            # newest-top feed (the "messages come after commands" inversion; the
            # backlog path already ts-merges, so only the live tick was wrong).
            got = plugins.conversation(sid, mpos)
            recs = []
            if got:
                recs, mpos = got            # advance the transcript cursor always
            if ops or recs:
                # the prompt bubbles' `/command` tint is resolved per tick off
                # the cwd (a TTL memo — a command file added mid-session starts
                # tinting without a reconnect), never per bubble
                if not self._sse("ops", {"last": last2, "mpos": mpos,
                                         "items": merge_live(ops, recs, key,
                                                             cmd_names(ctx.cwd))}):
                    return
                last = last2
            if recs:
                st = API.stats_at(ctx.sdb)
                if not self._push_changed(prev, "stats", "stats", st):
                    return
            slow = tick % SLOW_EVERY == 0
            # -- slow channels -------------------------------------------------
            if slow:
                # a resume moves the session to a NEW kitty window (the
                # SessionStart upsert refreshes the sessions row) — re-resolve,
                # or a stream opened before the move polls the dead window's
                # lingering tab state forever (green while kitty is magenta)
                ctx.adopt(API.session_row(sid) or {})
                # resolved up front so the agent cards' inherit-default effort
                # matches the effort quick-button pushed below (one resolve)
                ctx.eff = plugins.effort_default(ctx.cwd, session_slug(sid))
                if not self._push_chans(_SLOW_CHANS, prev, ctx):
                    return
            # -- fast channels -------------------------------------------------
            ctx.tab = (API.tab_states().get(ctx.win) or "") if ctx.win else ""
            if not self._push_chans(_FAST_CHANS, prev, ctx):
                return
            # -- dialogs + the gated ghost suggestion --------------------------
            # the pending modal-dialog cards (fast cadence — the dialog just
            # appeared and the user is waiting); None clears each card
            # change-detect on the RAW stash (a cheap kv read); enrich with the
            # preamble only when it actually changed (ask_wire reads the
            # transcript — see there)
            ask = ask_pending(sid)
            if ask != prev["ask"]:
                prev["ask"] = ask
                if not self._sse("ask", {"ask": ask_wire(sid, ask)}):
                    return
            # the unsubmitted-selections draft — so a card open on ANOTHER
            # device tracks this one's edits (the writer suppresses its own
            # echo by `origin`). Only meaningful while an ask is open;
            # ask_draft returns None once it's gone, clearing the peer.
            draft = ask_draft(sid, ask) if ask else None
            if not self._push_changed(prev, "ask_draft", "ask-draft", draft, {"draft": draft}):
                return
            plan = plan_pending(sid)
            if not self._push_changed(prev, "plan", "plan", plan, {"plan": plan}):
                return
            # the greyish input-box ghost suggestion (docs/dashboard.md, *Web
            # ghost suggestion*) — the faint "suggested answer" the TUI
            # pre-fills when a turn settles. Screen-scraped (no hook fires for
            # it), so gated hard AND throttled to the slow cadence: only when
            # the tab is settled (done/idle), no modal dialog is pending, and
            # the web composer box is empty (else there's nothing to surface, or
            # the probe would fight a draft the user is editing elsewhere).
            # A MODAL dialog (red tab / pending ask/plan) makes the `❯` region
            # the DIALOG's input, not the message box — never read it as one.
            if slow and (ctx.tab != tabs.AWAITING_COMMAND
                         and ask is None and plan is None):
                ghost, box = input_box(sid)
                # the ghost only exists on a SETTLED tab, and only matters while
                # the web box is empty (a draft the user is editing elsewhere
                # would fight it)
                sug = (ghost if (ctx.tab in SUGGEST_TABS
                                 and prev["composer_draft"] is None) else None)
                # …the typed half is wanted on ANY tab: typing a follow-up while
                # Claude works is the main reason to reach for another device,
                # and gating this on a settled tab meant the sync did nothing
                # exactly then (reported 2026-07-25). launch.sync_terminal_draft
                # owns the push/clear asymmetry.
                launch.sync_terminal_draft(sid, box, prev["term_box"],
                                           prev["composer_draft"])
                if box is not None:
                    prev["term_box"] = box
            else:
                sug = prev["suggestion"]
            if not self._push_changed(prev, "suggestion", "suggestion", sug, {"suggestion": sug}):
                return
            beat, alive = self._keepalive(beat)
            if not alive:
                return
            tick += 1
            time.sleep(TICK_S)

    def sse_agent(self, sid, aid, pos):
        """One agent's LIVE drill-down timeline (docs/dashboard.md): appends
        `entries` (new increment entries, server-enriched exactly like the REST
        /agent endpoint — the shared enrich_entries) and `resolve`
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
                    enrich_entries(entries)
                    if not self._sse("entries", {"pos": pos,
                                                 "entries": entries}):
                        return
                if resolutions:
                    if not self._sse("resolve", {"pos": pos,
                                                 "resolutions": resolutions}):
                        return
            beat, alive = self._keepalive(beat)
            if not alive:
                return
            time.sleep(TICK_S)
