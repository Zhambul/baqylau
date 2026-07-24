# dashboard/http/sse.py — the Server-Sent-Events streams.
#
# sse_global (the sessions list + notification fan-out), sse_session (one
# session's mirror/scoreboard/cards deltas), sse_agent (a subagent's timeline) —
# long-lived generators polling the read model + the NOTIFIER queue.
import queue
import time

import plugins
from core import paths as P
from core import sessionapi as API
from core.noaudit import load_audit
from dashboard import config
from dashboard.config import (BOOT_ID, HEARTBEAT_S, SLOW_EVERY, TICK_S)
from dashboard.notify.notifier import NOTIFIER
from dashboard.read.lists import (sessions_payload,
                                  _row_key, _wire_row)
from dashboard.read.meta import (git_info, session_ctx, session_goal,
                                 session_title, _session_slug)
from dashboard.read.mirror import (merged_backlog, merge_live, _enrich_entries)
from dashboard.read.session import (agents_ctx, agents_model_effort,
                                    visible_agents, _ask_draft,
                                    _ask_pending, _ask_wire, _composer_draft, _composer_queue,
                                    _plan_pending, _session_tasks,
                                    _suggestion, _SUGGEST_TABS)

A = load_audit()


class _SseMixin:

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
                        ev, payload = q.get(timeout=config.GLOBAL_TICK_S)
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
        """One session's live stream: `ops` (rendered HTML — ops AND the
        main-thread conversation from byte cursor `mpos`, interleaved by ts via
        merge_live so a turn's text keeps its place relative to its command),
        `stats`, `agents`, `tab`, `costs`, `running` (the live slot ribbon),
        `errors` (the ⚠ swallowed-error count) — each sent only on change. A
        FRESH connection (after=0, mpos=0) gets the ts-merged backlog as its
        first ops event; a reconnect resumes both cursors. The delta merge is
        the increment-side twin of the backlog merge, so live and reload agree
        (they diverged once — see docs/dashboard.md, the ts-interleave note)."""
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
            last2, ops = API.ops_at(sdb, last) if sdb else (last, [])
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
                if not self._sse("ops", {"last": last2, "mpos": mpos,
                                         "items": merge_live(ops, recs, key)}):
                    return
                last = last2
            if recs:
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
