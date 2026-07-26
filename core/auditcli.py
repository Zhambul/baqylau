# core/auditcli.py — the audit trail's READ/REPORT tier: every
# `bin/claude-audit.py` subcommand (sessions / timeline / errors / anomalies /
# otel / sql / prune) plus the CLI write entry points that shell out to the
# writer.
#
# Split out of core/audit.py, which EVERY hook process imports on EVERY event:
# 638 of its 1417 lines were this — the ANOMALY_SECTIONS catalogue, the row
# formatters, the command table, main(). None of it runs in a hook; all of it was
# parsed by ~20 short-lived processes a turn, and it read as part of the runtime
# to anyone opening the file. The split is about COHESION first — the write path
# is now readable on its own, and a query added here can't perturb the path that
# must never fail. The import saving is real but small (.pyc caching means only
# the first import pays the parse); the confusion saving is the point.
#
# Dependency direction: auditcli imports audit, never the reverse. `A.connect()`
# is the ONE door into the writer's connection (schema ensured, spool ingested).
#
# The audit-debug skill (.claude/skills/audit-debug/SKILL.md) documents the
# schema and the bug-shape playbook these queries serve; the anomalies registry
# below is its automated half.
"""claude-audit.py — query and write the baqylau audit trail."""
import json
import os
import sys
import time

from core import audit as A
from core import paths as P

def _read_stdin_json():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _fmt_ts(ts):
    if not ts:
        return "?"
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d}"
    except Exception:
        return str(ts)


def _print_rows(rows, headers):
    if not rows:
        print("(no rows)")
        return
    print(" | ".join(headers))
    for r in rows:
        print(" | ".join("" if v is None else str(v) for v in r))


def cli_timeline(sid, limit=2000, with_ops=False, with_otel=False):
    """Merged chronological view across all event tables for one session.
    `ops` and `otel` are opt-in (--ops / --otel): they dwarf the event tables
    (one row per paint op / per metric datapoint) and would drown the story."""
    conn = A.connect()
    if conn is None:
        print("audit db unavailable"); return
    extra = ""
    if with_ops:
        extra += """
      UNION ALL
      SELECT ts, 'op', producer || ': ' || substr(op, 1, 160), session_id
        FROM ops"""
    if with_otel:
        extra += """
      UNION ALL
      SELECT ts, 'otel', metric || ' ' || query_source ||
             CASE WHEN type != '' THEN ' ' || type ELSE '' END || '=' || value, session_id
        FROM otel"""
    q = """
    SELECT ts, src, detail FROM (
      SELECT ts, 'hook' AS src,
             hook || ' ' || tool_name ||
             CASE WHEN agent_id != '' THEN ' agent=' || substr(agent_id, 1, 8) ELSE '' END ||
             ' [' || handler || '] ' || decision AS detail, session_id
        FROM hook_events
      UNION ALL
      SELECT ts, 'tab', dispatch || ': ' || prev_state || ' -> ' || new_state ||
             CASE WHEN applied = 1 THEN '' ELSE ' (skipped)' END ||
             CASE WHEN reason != '' THEN ' — ' || reason ELSE '' END, session_id
        FROM tab_transitions
      UNION ALL
      SELECT ts, 'slot', action || ' ' || kind ||
             CASE WHEN slot_n IS NOT NULL THEN '.' || slot_n ELSE '' END ||
             CASE WHEN agent_id != '' THEN ' agent=' || substr(agent_id, 1, 8) ELSE '' END ||
             ' pid=' || COALESCE(owner_pid, '?'), session_id
        FROM slots
      UNION ALL
      SELECT started_at, 'stream', 'start ' || kind || ' pid=' || pid ||
             CASE WHEN task_id != '' THEN ' task=' || task_id ELSE '' END ||
             CASE WHEN agent_id != '' THEN ' agent=' || substr(agent_id, 1, 8) ELSE '' END, session_id
        FROM streams
      UNION ALL
      SELECT ended_at, 'stream', 'end ' || kind || ' pid=' || pid || ' reason=' ||
             COALESCE(end_reason, '?') || ' lines=' || COALESCE(lines_emitted, '?'), session_id
        FROM streams WHERE ended_at IS NOT NULL
      UNION ALL
      SELECT ts, 'ERROR', script || ' ' || func || ': ' ||
             replace(substr(traceback, 1, 400), char(10), ' ⏎ '), session_id
        FROM errors
      UNION ALL
      SELECT ts, 'spawn', parent_script || ' -> pid=' || child_pid || ' ' || purpose, session_id
        FROM spawns
      UNION ALL
      SELECT ts, 'file', action || ' ' || path ||
             CASE WHEN content != '' THEN ' :: ' || substr(content, 1, 120) ELSE '' END, session_id
        FROM state_files
      UNION ALL
      SELECT ts, 'pane', action || CASE WHEN ok = 1 THEN '' ELSE ' FAILED' END ||
             CASE WHEN detail != '' THEN ' — ' || detail ELSE '' END, session_id
        FROM pane_events""" + extra + """
    ) WHERE session_id = ? ORDER BY ts LIMIT ?"""
    for ts, src, detail in conn.execute(q, (sid, limit)):
        print(f"{_fmt_ts(ts)}  {src:<7} {detail}")


def cli_errors(sid):
    conn = A.connect()
    if conn is None:
        print("audit db unavailable"); return
    rows = conn.execute("SELECT ts, script, func, traceback, context FROM errors "
                        "WHERE session_id=? ORDER BY ts", (sid,)).fetchall()
    if not rows:
        print("(no recorded errors)")
    for ts, script, func, tb, ctx in rows:
        print(f"--- {_fmt_ts(ts)}  {script} {func}")
        if ctx:
            print(f"context: {ctx[:500]}")
        print(tb)


# The anomalies registry — the canned queries `cli_anomalies` runs, IN ORDER.
# Each entry is either
#   (title, sql, nparams)   — sql takes the sid repeated nparams times, printed
#                             as a counted section (empty = clean), or
#   a callable(conn, section, sid) — a special-case section that needs more than
#                             one query (e.g. a cross-DB check); it must print its
#                             own `== title: N` section via the passed `section`
#                             helper or an equivalent print.
# CLAUDE.md tells contributors to extend this list when a feature has a known
# failure signature — add the entry here (with the why-comment above it) and
# poison-test it in tests/test_l7_audit.py.

# Window for the duplicated-ops signature: the fixed-2026-07-04 tailer bug
# (unbounded read() + pos=size) re-emitted the same chunk on the NEXT poll, so
# genuine duplicates land seconds apart; identical long lines hours apart are
# just a command printing the same thing twice.
DUP_OPS_WINDOW_S = 5.0

ANOMALY_SECTIONS = [
    ("swallowed errors",
     "SELECT ts, script, func FROM errors WHERE session_id=? ORDER BY ts", 1),
    ("streams that never ended (crashed/stuck tailer)",
     "SELECT id, kind, pid, task_id, agent_id, started_at FROM streams "
     "WHERE session_id=? AND ended_at IS NULL", 1),
    # kind='codex-claim' is EXCLUDED: those rows are permanent cross-session
    # OWNERSHIP records (which session shows a codex run), not slot lifecycles
    # — no release ever follows, so counting them false-fired on every adopted
    # rollout. 'claim-denied' is likewise not an acquisition (nothing was
    # taken, so nothing will be released). 'steal-stale' IS an acquisition (the
    # new holder takes the slot) — it was once counted on the release side,
    # which balanced out a stealer that then leaked its slot (steal-then-leak
    # escaped). The displaced dead holder's missing release is synthesized at
    # steal time (core/slots.py 'release-stale'), so a healthy steal still
    # balances; pre-2026-07-15 sessions have steal rows without release-stale
    # and may flag here — historical data, not a live bug.
    ("slot claims without a matching release",
     "SELECT kind, slot_n, agent_id, COUNT(*) FROM slots WHERE session_id=? "
     "AND kind != 'codex-claim' "
     "GROUP BY kind, COALESCE(slot_n, -1), agent_id "
     "HAVING SUM(CASE WHEN (action LIKE 'claim%' AND action != 'claim-denied') "
     "               OR action LIKE 'steal%' THEN 1 ELSE 0 END) > "
     "       SUM(CASE WHEN action LIKE 'release%' THEN 1 ELSE 0 END)",
     1),
    # 'awaiting-command' (red — the permission prompt) is a RESTING user-blocked
    # state, exactly like green: a session can legitimately sit on it for hours,
    # so it's excluded alongside the green/idle/clear set. NB pre-2026-07
    # sessions wrote their literal-state dispatches (SessionStart idle /
    # SessionEnd clear) under session_id='' — this per-sid query misses that
    # final clear, so old sessions can false-flag here.
    ("tab left on a busy colour (last transition not green/idle/clear)",
     "SELECT ts, dispatch, prev_state, new_state, reason FROM tab_transitions "
     "WHERE session_id=? AND applied=1 AND ts = (SELECT MAX(ts) FROM "
     "tab_transitions WHERE session_id=? AND applied=1) AND new_state NOT IN "
     "('awaiting-response', 'awaiting-command', 'idle', 'clear', '')", 2),
    # An Esc-sending web gesture (interrupt / cancel-edit / rewind) must NEVER
    # reach the terminal on a red awaiting-command tab: a modal ask/plan/
    # permission dialog is open there and an Escape DECLINES it (the "User
    # declined to answer questions" bug, fixed 2026-07-20 via _dialog_open_guard,
    # which refuses instead with an `ok:false, step:dialog` row). A row here — a
    # gesture whose recorded tab was awaiting-command yet was NOT the guard's
    # refusal — means the guard regressed (removed, or a NEW red-bearing state
    # slipped past it) and an Esc landed in an open dialog, declining the ask.
    ("web Esc gesture fired on a red dialog-open tab (declines the ask)",
     "SELECT ts, action, content FROM state_files WHERE session_id=? "
     "AND action IN ('web-interrupt', 'web-rewind', 'web-rewind-to') "
     "AND json_extract(content, '$.tab') = 'awaiting-command' "
     "AND COALESCE(json_extract(content, '$.step'), '') != 'dialog'", 1),
    # A `web-hint` row with phase='stale' is an OPTIMISTIC web action (op =
    # composer | close | answer | plan — docs/dashboard.md, *Optimistic UI &
    # the web-hint audit*) whose greyed client-side UI the page never reconciled
    # to the real SSE confirmation within the ~20s watchdog: a stuck greyed
    # state. Read the `op` in content to know which. `composer`: the prompt
    # stand-in never matched its transcript prompt (attachment/whitespace
    # mangled the text, or the `web-send` paste failed). `close`: the tab never
    # parked (the sessions snapshot kept the sid live — check the `web-stop`
    # row). `answer`/`plan`: the card's answer/decision landed but the stash
    # never dropped (no SSE `ask`/`plan` clear — check the paired
    # `web-answer`/`web-plan` row for ok, and whether the PostToolUse fired).
    ("optimistic web action never reconciled (stuck greyed UI: web-hint stale)",
     "SELECT ts, content FROM state_files WHERE session_id=? "
     "AND action='web-hint' AND json_extract(content, '$.phase')='stale'", 1),
    # A `web-stop` ATTEMPT with no paired `done` — post_stop entered close_tab
    # and it never returned (an unbounded kitten socket connect, a stuck close).
    # This is the SERVER-SIDE counterpart the `web-hint op=close … stale`
    # anomaly above tells you to look for: the tab won't close from the
    # dashboard and the client's greyed 'closing…' hangs to its 20s watchdog.
    # (Before the attempt row existed, the only web-stop row was written AFTER
    # close_tab, so a hung close left NOTHING here and the diagnosis dead-ended.)
    ("dashboard close entered but never completed (web-stop attempt, no done)",
     "SELECT a.ts, a.content FROM state_files a WHERE a.session_id=? "
     "AND a.action='web-stop' AND json_extract(a.content, '$.phase')='attempt' "
     "AND NOT EXISTS (SELECT 1 FROM state_files d WHERE d.session_id=a.session_id "
     "  AND d.action='web-stop' AND json_extract(d.content, '$.phase')='done' "
     "  AND json_extract(d.content, '$.win')=json_extract(a.content, '$.win') "
     "  AND d.ts >= a.ts)", 1),
    # A dashboard STOP (interrupt) whose verify never saw the working spinner
    # clear — the synthesized Escape (only ~2/3 reliable) never reached the TUI,
    # so the turn kept running. `stopped:false` is the endpoint reporting the
    # miss (a 502 to the page); its presence IS the "stop did nothing" bug.
    ("web interrupt never landed (web-interrupt stopped:false — the Esc missed)",
     "SELECT ts, content FROM state_files WHERE session_id=? "
     "AND action='web-interrupt' "
     "AND json_extract(content, '$.stopped')=0", 1),
    # handler != 'subscriber': the universal async subscriber records EVERY hook
    # event alongside the handler's own decision row, so counting both made every
    # normally-started agent look started-twice (a false positive on all sessions
    # since the subscriber landed).
    ("duplicate SubagentStart (same agent started twice)",
     "SELECT agent_id, COUNT(*) FROM hook_events WHERE session_id=? AND "
     "hook='SubagentStart' AND agent_id != '' AND handler != 'subscriber' "
     "GROUP BY agent_id HAVING COUNT(*) > 1", 1),
    ("SubagentStart without SubagentStop",
     "SELECT DISTINCT h.agent_id FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SubagentStart' AND h.agent_id != '' AND h.agent_id NOT IN "
     "(SELECT agent_id FROM hook_events WHERE session_id=? AND hook='SubagentStop')",
     2),
    # The inverse is the scoreboard-under-/cost signature: Claude Code runs hidden
    # summarizer-style agents that fire ONLY SubagentStop — no SubagentStart, no
    # substream, and (usually) no transcript file, so their billed spend never
    # reaches the scoreboard. Since the OTEL cost pipeline, a hidden agent's spend
    # IS captured (the OTLP receiver folds query_source=auxiliary/subagent live), so
    # this is now informational, not a spend gap. The stop handler's decision row
    # still says whether a transcript existed to cross-check ("never started …").
    ("SubagentStop without SubagentStart (hidden agent — spend now captured via OTEL)",
     "SELECT DISTINCT h.agent_id FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SubagentStop' AND h.agent_id != '' AND h.agent_id NOT IN "
     "(SELECT agent_id FROM hook_events WHERE session_id=? AND hook='SubagentStart')",
     2),
    # A subagent turn that dies on an API error (e.g. 529 Overloaded) fires
    # StopFailure carrying its agent_id and NO SubagentStop — the agent's only stop
    # signal. claude-stop-fmt.py must hand it to the subagent finaliser (a
    # 'stopfail: …' decision); the pre-fix behaviour ('ignored: agent_id …') left the
    # streamer's slot claimed forever and wedged the tab blue. This flags only the
    # UNrecovered case — a StopFailure+agent_id whose decision is NOT 'stopfail:' — so
    # a healthy recovered session stays clean and a non-empty row IS the regression.
    ("StopFailure carrying an agent_id NOT handed to the finaliser (stuck-blue regression)",
     "SELECT ts, agent_id, decision FROM hook_events WHERE session_id=? AND "
     "hook='StopFailure' AND agent_id != '' AND handler != 'subscriber' "
     "AND decision NOT LIKE 'stopfail:%' ORDER BY ts", 1),
    # An ASYNC (background) agent's Task resolves IMMEDIATELY in the parent
    # transcript with a synthetic "Async agent launched successfully" tool_result
    # (is_error absent) meaning launched-not-finished. parent_tool_result() must
    # ignore that ack; treating it as a resolution ended the substream ~2s in with
    # 0 lines rendered, so the agent's whole transcript never reached the mirror.
    # Tell: a subagent/teammate stream ending 'parent-task-resolved' (NOT rejected)
    # with lines_emitted=0 while a real SubagentStop later fired for that agent.
    ("async launch-ack ended the substream early (0 lines rendered)",
     "SELECT s.agent_id, s.ended_at, s.end_reason FROM streams s WHERE "
     "s.session_id=? AND s.kind IN ('subagent','teammate') AND "
     "s.end_reason='parent-task-resolved' AND COALESCE(s.lines_emitted,0)=0 "
     "AND s.agent_id IN (SELECT agent_id FROM hook_events WHERE session_id=? "
     "AND hook='SubagentStop')", 2),
    # Claude Code creates tasks/<id>.output LAZILY, on the monitor's first output
    # byte — a quiet persistent monitor has no file for minutes or hours. The
    # monitor tailer waits for it keyed on the monitor PROCESS's liveness
    # (stream.py monitor_wait_file); a monitor stream ending plain
    # 'output-file-not-found' is the pre-fix bounded 12s give-up — the block closed
    # "■ output not found" and the tab cleared to green while the monitor ran on.
    # Post-fix the only legitimate not-found end carries the
    # '(monitor process never found)' suffix (nothing to key liveness on), so a
    # bare match here IS the regression.
    ("monitor gave up on a lazily-created output file (tab wrongly cleared)",
     "SELECT id, task_id, started_at, ended_at FROM streams WHERE session_id=? "
     "AND kind='monitor' AND end_reason='output-file-not-found'", 1),
    # The SAME shape for a foreground command that redirects to its own file
    # (`cmd > out`): a command whose file is created only late (`sleep 45; cmd >
    # out`, a retry loop) is still running, so the fg tailer must wait on command
    # LIVENESS (the PostToolUse outcome hand-off), not the flat FIND_S deadline
    # (stream.py wait_fg_src). A pre-fix fg stream gave up at ~12s with
    # 'output-file-not-found', released the fg slot, and bg-recheck cleared the
    # tab off blue while the command ran on (its Post fired seconds later). A bare
    # fg 'output-file-not-found' whose command's PostToolUse arrived AFTER the
    # stream ended is that regression; a genuinely fileless command ends after its
    # Post, not before.
    ("fg tailer gave up on a late redirect target (tab wrongly cleared)",
     "SELECT id, task_id, started_at, ended_at FROM streams WHERE session_id=? "
     "AND kind='fg' AND end_reason='output-file-not-found'", 1),
    # Since the single-dispatcher refactor every event runs through claude-hook.py
    # -> dispatch.py. A crash in the DISPATCHER itself (not a subsystem) records
    # script='dispatch' — that means route() threw before/around fanning out, so a
    # whole event may have produced no tab change / no block. A subsystem crash keeps
    # its own entry-filename script (surfaced by "swallowed errors" above); this
    # isolates the dispatcher-level failure, which should essentially never fire.
    ("dispatcher-level crash (route() threw — whole event may be lost)",
     "SELECT ts, func, substr(traceback,1,120) FROM errors WHERE session_id=? "
     "AND script='dispatch' ORDER BY ts", 1),
    ("failed tools (PostToolUseFailure)",
     "SELECT ts, tool_name, decision FROM hook_events WHERE session_id=? AND "
     "hook LIKE '%Failure%' ORDER BY ts", 1),
    # A content-render stream (claude-stream.py MD/JSON mode: cat/head/tail of a .md,
    # cat of a .json; decision '[md-render]'/'[json-render]' in hook_events) records a
    # 'done' state_file row (path render:<taskid>) with the block count it emitted.
    # Zero blocks from a stream that ran means the renderer produced nothing — a
    # wenmode/json parse failure or an empty fallback. The paired 'start' row records
    # the kind (md/json). See core/mdrender.py / core/jsonrender.py.
    ("content-render streams that emitted zero blocks (render failure)",
     "SELECT ts, path, content FROM state_files WHERE session_id=? AND "
     "path LIKE 'render:%' AND action='done' AND content LIKE '%\"blocks\": 0%' "
     "ORDER BY ts", 1),
    ("spawned processes that never registered a stream",
     "SELECT s.ts, s.child_pid, s.purpose FROM spawns s WHERE s.session_id=? "
     "AND s.purpose LIKE 'stream%' AND s.child_pid NOT IN "
     "(SELECT pid FROM streams WHERE session_id=?)", 2),
    ("pane operations that failed",
     "SELECT ts, action, detail FROM pane_events WHERE session_id=? AND ok=0 "
     "ORDER BY ts", 1),
    # close_stale_mirrors audits every window it sweeps (action=close-stale,
    # detail "closed sid=<sid> win=<id>"). Sweeping a mirror whose session is
    # still OPEN is the cross-session pane-hijack shape (a daemon-origin
    # SessionStart anchored to the wrong tab — the agents-view bug); the benign
    # exception is a predecessor that crashed without SessionEnd in the same tab.
    # The LIKE join parses the swept sid out of `detail` and is deliberately
    # non-sargable: pane_events is per-session-pruned and close-stale rows are
    # rare, so the scan is tiny — a dedicated swept_sid column (the audit DB's
    # first ALTER-style migration) was judged not worth it. If close-stale
    # volume ever grows, add the column at the write site
    # (core/hostpane.py close_stale_mirrors) instead of tuning this query.
    ("stale-mirror sweep closed a LIVE session's mirror (pane hijack)",
     "SELECT p.ts, p.session_id, p.detail FROM pane_events p JOIN sessions s "
     "ON p.detail LIKE ('closed sid=' || s.session_id || ' %') "
     "WHERE p.action='close-stale' AND s.ended_at IS NULL "
     "AND s.session_id != p.session_id "
     "AND (p.session_id=? OR s.session_id=?) ORDER BY p.ts", 2),
    ("tab colour applies where kitten @ failed",
     "SELECT ts, dispatch, new_state, reason FROM tab_transitions "
     "WHERE session_id=? AND reason LIKE '%kitten @ failed%' ORDER BY ts", 1),
    # A --resume/--continue SessionStart should find the parked *.keep state DB and
    # log a `restore-history` (or, after a crash with no SessionEnd, find the DB
    # still live: `reuse-live-db`). A `fresh-db` row on a source=resume start
    # means the history was lost — the mirror came back empty.
    # park_db/decide_log_fate audit their move failures as DISTINCT fates now
    # (2026-07-15): 'park-failed (kept live)' = SessionEnd could not move the
    # state DB out (ENOSPC/EPERM/blocked destination — the paired errors row has
    # the traceback), so the live path persists, parked() never fires, and the
    # scorebar/codex-watcher pollers keep running as orphans; 'restore-failed
    # (park kept)' = the resume's move-back failed, the park stays for a later
    # try and the session started fresh. Either row is a real filesystem problem.
    ("state-DB park/restore move failed (orphaned pollers / history not restored)",
     "SELECT ts, action, content FROM state_files WHERE session_id=? AND "
     "action IN ('park-failed (kept live)', 'restore-failed (park kept)') "
     "ORDER BY ts", 1),
    # The reuse-live-db zombie shape (docs/mirror-pane.md): a bg/fg tailer that
    # outlived SessionEnd's park kept pumping, and its first post-park emit
    # RECREATED an empty state DB at the live path — the next resume then saw
    # 'reuse-live-db' and trusted the empty DB while the real history sat in the
    # park. Current builds exit 'state-db-parked (session end)' before pumping,
    # so a bg/fg stream that ended AFTER the session's keep-history park with any
    # OTHER reason is the regression. (No keep-history row -> subquery NULL ->
    # comparison false -> clean, by construction.)
    ("bg/fg tailer outlived the park (zombie recreated the state DB)",
     "SELECT s.id, s.kind, s.end_reason, s.ended_at FROM streams s WHERE "
     "s.session_id=? AND s.kind IN ('bg','fg') AND s.ended_at IS NOT NULL "
     "AND COALESCE(s.end_reason,'') != 'state-db-parked (session end)' "
     "AND s.ended_at > (SELECT MAX(ts) FROM state_files WHERE session_id=? "
     "AND action='keep-history')", 2),
    ("resume that lost its mirror history (fresh-db on source=resume)",
     "SELECT h.ts FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SessionStart' AND json_extract(h.payload,'$.source')='resume' "
     "AND EXISTS (SELECT 1 FROM state_files f WHERE f.session_id=h.session_id "
     "AND f.action='fresh-db' AND abs(f.ts - h.ts) < 30)", 1),
    # Claude Code can FORK the sid on --resume: SessionStart fires under the OLD
    # sid while every later event carries a NEW sid that never gets its own
    # SessionStart (see plugins/claude_code/adopt.py). On a current build the
    # fork's first event ADOPTS the predecessor — renaming its state DB,
    # retagging the panes, and writing the sessions row the fork never got.
    # Functional hook traffic under a sid with NO sessions row means the fork
    # was never adopted: its events fed a state DB nothing renders while the
    # old sid's mirror/scorebar/tab froze (the 19a42746→ebcecfcc shape). Every
    # legitimate session — interactive, headless, agents-view — gets a sessions
    # row from its own SessionStart (A.session_start runs before the pane-skip
    # check), so this only fires on an unadopted fork.
    ("hook traffic under a sid with no sessions row (resume fork never adopted)",
     "SELECT MIN(ts), MAX(ts), COUNT(*) FROM hook_events WHERE session_id=? "
     "AND handler='subscriber' AND NOT EXISTS "
     "(SELECT 1 FROM sessions WHERE session_id=?) HAVING COUNT(*) > 0", 2),
    # Claude Code RELOCATES the transcript when the session's cwd moves to another
    # project dir (measured 2026-07-18 via EnterWorktree: the file moves to the
    # worktree cwd's projects/ slug dir), and every later hook payload carries the
    # new path. A.session_paths (run by the dispatcher on every event) keeps the
    # sessions row in step; the row disagreeing with the LATEST subscriber
    # payload means that refresh regressed — every consumer of the row (dashboard
    # title/ctx-probe/git chips, web rename, sessionapi) points at a dead path.
    # Pre-2026-07-18 sessions that entered a worktree flag here historically (the
    # refresh didn't exist yet) — fix those rows by hand via sql-write.
    ("sessions row transcript_path stale vs latest hook payload (relocation refresh regressed)",
     "SELECT s.transcript_path, json_extract(h.payload,'$.transcript_path') "
     "FROM sessions s JOIN hook_events h ON h.id = "
     "(SELECT MAX(id) FROM hook_events WHERE session_id=s.session_id "
     "AND handler='subscriber' "
     "AND COALESCE(json_extract(payload,'$.transcript_path'),'') != '') "
     "WHERE s.session_id=? "
     "AND json_extract(h.payload,'$.transcript_path') != s.transcript_path", 1),
    # A genuine sid-fork NEVER gets its own SessionStart — that is the whole basis
    # for adoption. So a sid that ADOPTED a predecessor yet ALSO has its own
    # SessionStart is a MIS-adoption: an independent new session wrongly consumed a
    # concurrent same-cwd session's adopt_pending note and stole its panes (live
    # bug: 507fc4c8's pre-SessionStart InstructionsLoaded adopted the unrelated live
    # db081e65 — toggling 507's mirror then toggled db081e65's). Fixed by marking the
    # sid on InstructionsLoaded (adopt.py); a non-empty row here is the regression.
    ("adopted a predecessor despite having its OWN SessionStart (mis-adoption — pane theft)",
     "SELECT a.ts, a.decision FROM hook_events a WHERE a.session_id=? "
     "AND a.decision LIKE 'adopt:%' AND EXISTS (SELECT 1 FROM hook_events s "
     "WHERE s.session_id=a.session_id AND s.hook='SessionStart') ORDER BY a.ts", 1),
    # Token/cost spend must arrive as an ATTRIBUTED action: 'bump-otel' (the OTLP
    # receiver, keyed by session.id + query_source) or 'bump-agent' (codex's own
    # rollout fold — codex runs in a separate process OTEL can't see). A plain 'bump'
    # carrying a tokens/cost delta means some producer bypassed attribution — the
    # scoreboard number it fed can only be traced by timestamp correlation.
    ("unattributed token/cost bumps (should be bump-agent with meta)",
     "SELECT ts, content FROM state_files WHERE session_id=? AND action='bump' "
     "AND (json_extract(content, '$.deltas.tokens') IS NOT NULL "
     "OR json_extract(content, '$.deltas.cost') IS NOT NULL) ORDER BY ts", 1),
    # Cost is OTEL-authoritative; the transcript fold survives ONLY as a SessionEnd
    # fallback that must fire ONLY when the receiver wrote nothing (otel_seen==0). If a
    # session has BOTH a 'folded transcript fallback' SessionEnd decision AND bump-otel
    # rows, the fallback fired despite OTEL data — a double-count regression (the
    # otel_seen gate in stop_fmt broke). A healthy session has exactly one source.
    ("SessionEnd transcript fallback fired despite OTEL data (double-count regression)",
     "SELECT ts, decision FROM hook_events WHERE session_id=? AND "
     "handler='claude-stop-fmt.py' AND decision LIKE 'otel absent%' AND EXISTS "
     "(SELECT 1 FROM state_files f WHERE f.session_id=? AND f.action='bump-otel') "
     "ORDER BY ts", 2),
    # The inverse wiring failure: SessionEnd fired (the subscriber row proves the
    # dispatcher ran) but claude-stop-fmt.py never wrote its SessionEnd decision —
    # the stop-fold step was dropped from the dispatch plan. stop-fmt ALWAYS
    # decides on SessionEnd ('otel authoritative … fold skipped' or 'otel absent —
    # folded transcript fallback'), so its absence is the tell. Scoped to sessions
    # with NO bump-otel rows: with OTEL data the cost is intact anyway; without it
    # the missing fallback fold means the session's cost was silently lost.
    ("SessionEnd fired but the stop-fold never ran (fallback dropped — cost lost)",
     "SELECT h.ts FROM hook_events h WHERE h.session_id=? AND "
     "h.hook='SessionEnd' AND h.handler='subscriber' AND NOT EXISTS "
     "(SELECT 1 FROM state_files f WHERE f.session_id=? AND f.action='bump-otel') "
     "AND NOT EXISTS (SELECT 1 FROM hook_events e WHERE e.session_id=? AND "
     "e.hook='SessionEnd' AND e.handler='claude-stop-fmt.py')", 3),
    # Cross-session contamination: everything is keyed by session_id, EXCEPT
    # background-job detection, which is per-project (cwd slug) — two sessions in
    # one directory can cross-talk (CLAUDE.md). A task_id (streams) or slot claim
    # token (slots.marker_path — it embeds the mirror-log path, so a foreign sid
    # can only appear via a mis-keyed write) under >1 session_id is that shape.
    # Scoped to groups involving THIS sid so `anomalies <sid>` stays per-session.
    # Benign exception: a codex run taken over from a DEAD session
    # (codex-claim steal-stale) legitimately streams under the new sid.
    ("cross-session contamination (task_id/slot token under more than one sid)",
     "SELECT src, key, sids FROM ("
     "  SELECT 'stream-task' AS src, task_id AS key, "
     "         GROUP_CONCAT(DISTINCT session_id) AS sids, "
     "         SUM(session_id=?) AS mine FROM streams WHERE task_id != '' "
     "  GROUP BY task_id HAVING COUNT(DISTINCT session_id) > 1 "
     "  UNION ALL "
     "  SELECT 'slot-token', marker_path, GROUP_CONCAT(DISTINCT session_id), "
     "         SUM(session_id=?) FROM slots WHERE marker_path != '' "
     "  GROUP BY marker_path HAVING COUNT(DISTINCT session_id) > 1"
     ") WHERE mine > 0", 2),
    # The fixed-2026-07-04 duplicated-block shape: a tailer's unbounded read()
    # with pos=size re-read bytes appended mid-read, painting the same chunk
    # twice on the NEXT poll — identical ops seconds apart. Scoped to gut ops
    # (block body lines) long enough (>60 chars) that an identical repeat within
    # DUP_OPS_WINDOW_S is a re-read, not a command printing a short line twice.
    ("duplicated mirror ops (identical block lines painted twice within %gs)"
     % DUP_OPS_WINDOW_S,
     "SELECT substr(op,1,80), COUNT(*) FROM ops WHERE session_id=? "
     "AND op LIKE '%\"gut\"%' AND length(op) > 60 "
     "GROUP BY op HAVING COUNT(*) > 1 AND MAX(ts) - MIN(ts) < "
     + str(DUP_OPS_WINDOW_S), 1),
    # The OTLP receiver is a long-lived singleton that caches its state-DB
    # connection. A park (os.replace db -> db.keep) + resume swaps the inode under
    # the path, so a receiver that didn't revalidate kept writing token counters to
    # the ORPHANED *.keep inode while the scorebar read the fresh live DB — a silent
    # divergence (no error; both files are valid DBs). Tell: bump-otel rows exist for
    # the session (OTEL landed) yet the LIVE state DB has no tk_read/tokens counter.
    # core/state._connect now revalidates by st_ino, so a non-empty row here is that
    # regression (or the receiver holding an fd on a *.keep path — check `lsof`).
    # A session that DIED because its account was LOGGED OUT: relimit stamped a
    # `logged-out` state_file off a StopFailure error='authentication_failed'
    # (the CLI's "Please run /login · … OAuth access token has been revoked").
    # Not a code bug — the account's OAuth login was revoked/expired — but the
    # tell for "the session ended and the dashboard flagged the account ⚠ logged
    # out (and the migration picker skips it)". content = {slug, ts, msg}; the
    # flag clears read-side once a fresher usage snapshot for the slug lands (a
    # re-login session — sessionapi.logged_out_active). docs/relimit.md.
    ("session died logged out (account login revoked — authentication_failed)",
     "SELECT ts, content FROM state_files WHERE session_id=? "
     "AND action='logged-out'", 1),
    # A rate-limit migration (plugins/claude_code/relimit.py, docs/relimit.md)
    # that didn't complete: the `relimit` stream's end_reason names which leg
    # failed — 'close-failed'/'close-timeout' (the old tab wouldn't close or
    # its SessionEnd never parked the state DB), 'window-gone' (tab vanished
    # while the session stayed live — AUTO only; a manual ⇆ launches over a
    # stranded-live DB instead), 'launch-failed' (kitten refused the
    # resume tab). 'launched' is the ONLY healthy end, and even then the
    # --resume must fire a SessionStart under this (old) sid within the launch
    # window — a 'launched' row with no later SessionStart means the relaunch
    # died in the shell (bad alias, keychain prompt, claude not on PATH).
    ("rate-limit migration incomplete (relimit stream failed, or launched with no successor SessionStart)",
     "SELECT s.id, s.end_reason, s.started_at, s.ended_at FROM streams s "
     "WHERE s.session_id=? AND s.kind='relimit' AND s.ended_at IS NOT NULL "
     "AND (COALESCE(s.end_reason,'') != 'launched' "
     "OR NOT EXISTS (SELECT 1 FROM hook_events h WHERE h.session_id=s.session_id "
     "AND h.hook='SessionStart' AND h.ts > s.ended_at))", 1),
    lambda conn, section, sid: _section_otel_stranded(conn, section, sid),
]


def cli_anomalies(sid):
    """Canned queries for known bug signatures (the ANOMALY_SECTIONS registry
    above). Each prints a section; empty = clean."""
    conn = A.connect()
    if conn is None:
        print("audit db unavailable"); return

    def section(title, q, params=()):
        rows = conn.execute(q, params).fetchall()
        print(f"== {title}: {len(rows)}")
        for r in rows:
            print("   " + " | ".join("" if v is None else str(v) for v in r))

    for entry in ANOMALY_SECTIONS:
        if callable(entry):
            entry(conn, section, sid)
        else:
            title, q, nparams = entry
            section(title, q, (sid,) * nparams)


def _section_otel_stranded(audit_conn, section, sid):
    """Cross-check the audit's bump-otel trail against the LIVE state DB counters —
    the only decisive signal for a receiver whose writes were diverted to a parked
    *.keep inode (see the caller's note). Reads the state DB read-only; degrades to
    a clean section if it isn't present (parked/ended session — nothing to check)."""
    import sqlite3
    n_otel = audit_conn.execute(
        "SELECT COUNT(*) FROM state_files WHERE session_id=? AND action='bump-otel'",
        (sid,)).fetchone()[0]
    hits = []
    if n_otel:
        db = P.state_db(P.mirror_log(sid))
        if os.path.exists(db):
            try:
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
                have = c.execute(
                    "SELECT COUNT(*) FROM counters WHERE key IN ('tokens','tk_read')"
                ).fetchone()[0]
                c.close()
                if have == 0:
                    hits.append((n_otel, db))
            except Exception:
                pass
    print(f"== OTLP writes stranded on a parked inode "
          f"(bump-otel rows but live DB has no token counters): {len(hits)}")
    for h in hits:
        print(f"   bump-otel={h[0]} but no tokens/tk_read in {h[1]}")


def cli_otel(sid):
    """The OTEL cost/token breakdown for one session, straight from the raw `otel`
    datapoints the receiver captured — grouped by query_source × type (so the hidden
    `auxiliary` agents' share is explicit), plus total cost per query_source. This IS
    the ground truth the scoreboard counters aggregate; SUM here == the counter."""
    conn = A.connect()
    if conn is None:
        print("audit db unavailable"); return
    print("== token datapoints (SUM value) by query_source × type ==")
    rows = conn.execute(
        "SELECT query_source, type, COUNT(*), SUM(value) FROM otel WHERE session_id=? "
        "AND metric='token' GROUP BY query_source, type ORDER BY query_source, type",
        (sid,)).fetchall()
    _print_rows(rows, ["query_source", "type", "n", "tokens"])
    print("\n== cost (USD) by query_source ==")
    rows = conn.execute(
        "SELECT query_source, COUNT(*), SUM(value) FROM otel WHERE session_id=? "
        "AND metric='cost' GROUP BY query_source ORDER BY query_source", (sid,)).fetchall()
    _print_rows(rows, ["query_source", "n", "cost_usd"])
    tot = conn.execute("SELECT SUM(value) FROM otel WHERE session_id=? AND metric='cost'",
                       (sid,)).fetchone()
    print(f"\ntotal cost = ${(tot and tot[0]) or 0:.4f}")


def cli_sessions(limit=20):
    conn = A.connect()
    if conn is None:
        print("audit db unavailable"); return
    rows = conn.execute(
        "SELECT session_id, project_slug, datetime(started_at, 'unixepoch', 'localtime'),"
        " CASE WHEN ended_at IS NULL THEN '(open)' ELSE"
        " datetime(ended_at, 'unixepoch', 'localtime') END, end_reason"
        " FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    _print_rows(rows, ["session_id", "project", "started", "ended", "reason"])


def cli_sql(argv):
    """`sql` — free-form READ-ONLY SQL. Opens the DB `mode=ro` so a debugging
    query can never mutate the evidence (or create the file); ad-hoc fixups go
    through the explicit `sql-write` command instead."""
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{A.db_path()}?mode=ro", uri=True, timeout=3.0)
    except Exception:
        print("audit db unavailable"); return
    q = argv[2] if len(argv) > 2 else ""
    try:
        cur = conn.execute(q)
        headers = [c[0] for c in cur.description] if cur.description else []
        _print_rows(cur.fetchall(), headers)
    except Exception as e:
        print(f"sql error: {e}")
    finally:
        conn.close()


def cli_sql_write(argv):
    """`sql-write` — free-form READ-WRITE SQL for deliberate manual fixups
    (e.g. closing a stuck "(open)" session row). Separate from `sql` so a
    routine debugging query can never mutate the audit trail by accident."""
    conn = A.connect()
    if conn is None:
        print("audit db unavailable"); return
    q = argv[2] if len(argv) > 2 else ""
    try:
        cur = conn.execute(q)
        headers = [c[0] for c in cur.description] if cur.description else []
        _print_rows(cur.fetchall(), headers)
        conn.commit()
    except Exception as e:
        print(f"sql error: {e}")


# --------------------------------------------------------------- CLI dispatch
# Each handler owns its own argv parsing (argv is the FULL argv; argv[1] is the
# command name). write=True marks the fire-and-forget entry points hooks/shell
# invoke — the bin/claude-audit.py CLI derives its never-fail-loudly swallow set
# from WRITE_COMMANDS, so the two can't drift apart again.

def _cmd_session_start(argv):
    A.session_start(_read_stdin_json())


def _cmd_session_end(argv):
    A.session_end(_read_stdin_json())
    A.prune()


def _cmd_hook(argv):
    # hook <handler> [<decision>], payload on stdin
    A.hook_event(_read_stdin_json(), handler=(argv[2] if len(argv) > 2 else None),
               decision=(argv[3] if len(argv) > 3 else ""))


def _cmd_transition(argv):
    # transition <sid> <win> <dispatch> <prev> <new> <applied> [reason]
    a = argv[2:] + [""] * 7
    A.transition(a[0], a[1], a[2], a[3], a[4], a[5] == "1", a[6])


def _cmd_error(argv):
    # error <sid> <script> <message>
    a = argv[2:] + [""] * 3
    # getppid, unlike every other writer's getpid: this runs in a short-lived
    # `bin/claude-audit.py error …` CLI subprocess invoked FROM a shell script — the
    # diagnostic identity is the invoking shell process, not this throwaway
    # python pid (which is gone before anyone could correlate it).
    A.event("errors", session_id=a[0], script=a[1] or "shell", func="",
          traceback=a[2], context="", pid=os.getppid())


def _cmd_pane(argv):
    # pane <sid> <action> <ok 0|1> [detail]
    a = argv[2:] + [""] * 4
    A.pane(a[0], a[1], a[2] == "1", a[3])


def _cmd_state_file(argv):
    # state-file <log> <path> <action> [content]
    a = argv[2:] + [""] * 4
    A.state_file(a[0], a[1], a[2], a[3])


def _cmd_sessions(argv):
    cli_sessions(int(argv[2]) if len(argv) > 2 else 20)


def _cmd_timeline(argv):
    # timeline <sid> [limit] [--ops] [--otel]
    flags = {a for a in argv[2:] if a.startswith("--")}
    args = [a for a in argv[2:] if not a.startswith("--")]
    cli_timeline(args[0] if args else "",
                 int(args[1]) if len(args) > 1 else 2000,
                 with_ops="--ops" in flags, with_otel="--otel" in flags)


def _cmd_errors(argv):
    cli_errors(argv[2] if len(argv) > 2 else "")


def _cmd_anomalies(argv):
    cli_anomalies(argv[2] if len(argv) > 2 else "")


def _cmd_otel(argv):
    cli_otel(argv[2] if len(argv) > 2 else "")


def _cmd_prune(argv):
    n = A.prune(int(argv[2]) if len(argv) > 2 else A.PRUNE_DAYS)
    print(f"pruned {n} session(s)")


# NB: the old `stream-start`/`stream-end` CLI branches were removed — every
# tailer records its lifecycle in-process via stream_start()/stream_end()
# (core/tail.py stream_lifecycle); no repo script, ~/.claude/settings.json
# entry, or open-actions.conf action invoked them.
COMMANDS = {
    # write entry points (fired from hooks/shell — must never fail loudly)
    "session-start": (_cmd_session_start, True),
    "session-end":   (_cmd_session_end,   True),
    "hook":          (_cmd_hook,          True),
    "transition":    (_cmd_transition,    True),
    "error":         (_cmd_error,         True),
    "pane":          (_cmd_pane,          True),
    "state-file":    (_cmd_state_file,    True),
    # read/query commands (interactive — errors should surface)
    "sessions":      (_cmd_sessions,      False),
    "timeline":      (_cmd_timeline,      False),
    "errors":        (_cmd_errors,        False),
    "anomalies":     (_cmd_anomalies,     False),
    "otel":          (_cmd_otel,          False),
    "sql":           (cli_sql,            False),
    "sql-write":     (cli_sql_write,      False),
    "prune":         (_cmd_prune,         False),
}

WRITE_COMMANDS = frozenset(name for name, (_, write) in COMMANDS.items() if write)


def _usage():
    # Derived from COMMANDS so the list can never go stale; the docstring above
    # carries the prose + per-command arg synopsis.
    reads = sorted(n for n, (_, w) in COMMANDS.items() if not w)
    writes = sorted(n for n, (_, w) in COMMANDS.items() if w)
    return ((__doc__ or "").rstrip()
            + "\n\nquery commands:  " + " ".join(reads)
            + "\nwrite commands:  " + " ".join(writes))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    entry = COMMANDS.get(cmd)
    if entry is None:
        print(_usage())
        return
    entry[0](argv)


# The CLI entry point lives in bin/claude-audit.py (main() above
# is what it calls) — a package module can't be executed directly.
