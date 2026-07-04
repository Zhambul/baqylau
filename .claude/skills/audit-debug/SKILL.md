---
name: audit-debug
description: Diagnose a kitty-mirror/tab-status bug from the always-on SQLite audit trail. Use when the user reports a bug in a session (stuck tab colour, missing/duplicated mirror block, frozen pane, wrong scoreboard numbers) and gives a session id — or asks to investigate "what happened in session X".
---

# audit-debug — root-cause a session bug from the audit DB

Every Claude Code session in this project is audited into SQLite (always on;
`CLAUDE_AUDIT=0` disables). Given a session id, reconstruct what happened and name
the bug **from evidence, not guesswork**.

## Where the data is

- DB: `$CLAUDE_AUDIT_DIR/audit.db`, default `~/.claude/kitty-audit/audit.db` (WAL mode — safe to read while a session runs).
- Query tool: `python3 claude_audit.py <cmd>` from the repo root (`/Users/z.yermagambet/code/personal/kitty`), or raw `sqlite3` on the DB.
- If the user gives a truncated sid (first 8 chars), resolve it: `python3 claude_audit.py sql "SELECT session_id FROM sessions WHERE session_id LIKE '<prefix>%'"`.
- `python3 claude_audit.py sessions` lists recent sessions when no sid was given.

## Schema (all tables carry `session_id`; times are epoch seconds, local tz when displayed)

| table | one row per | key columns |
|---|---|---|
| `sessions` | Claude session | cwd, transcript_path, mirror_log, kitty_window_id, started_at/ended_at, end_reason, env (JSON of CLAUDE_MIRROR_*/KITTY_* seen at start). A SessionEnd that couldn't reach the DB spools a `session_end` pseudo-row (like `stream_end`), ingested later — a session still "(open)" long after it visibly ended now means the end never fired at all, not a locked DB |
| `hook_events` | hook invocation | hook, tool_name, agent_id ('' = main session), handler (script), **decision** (what the handler chose to do), **payload** (full hook stdin JSON, verbatim). ALL 30 hook events are recorded via a universal async subscriber (handler = 'subscriber', empty decision) — incl. PermissionRequest/Denied, PostToolBatch, MessageDisplay, TeammateIdle, Pre/PostCompact, ConfigChange, CwdChanged, FileChanged, Worktree\*, Elicitation\*, Setup, UserPromptExpansion, InstructionsLoaded — on top of the mirror handlers' own decision-carrying rows for the events they process. So "did event X even fire?" is always answerable from the subscriber rows, and a handler row can be cross-checked against the subscriber's independent record. |
| `tab_transitions` | tab-colour decision | dispatch (raw arg: pretool/stop/bg-recheck/bg-watch/notify/…), prev_state → new_state, applied (0 = skipped/bailed **or the kitten @ call failed** — reason then carries "kitten @ failed rc=N"), **reason** |
| `slots` | palette/liveness-slot event (rows of the session state DB's `live` table — were marker files) | kind (bg/monitor/fg/sub), slot_n, agent_id, owner_pid, action (claim/claim-id/**claim-pid**/steal-stale/claim-denied/release/release-id/**release-pid**/set-owner), marker_path (now an opaque `<log>::live:<kind>.<key>` token). To see the CURRENT slot state: `sqlite3 /tmp/claude-mirror-<sid>.log.state.db "SELECT * FROM live"` |
| `streams` | detached tailer/streamer/watcher | kind (fg/bg/monitor/subagent/teammate/codex/codex-watcher/**bg-watch/interrupt-watch**), agent_id/task_id, src_path, pid, started_at/ended_at, **end_reason** (writer-gone/sentinel/stop-sentinel/stoppedByUser/converted-ctrl-b/backstop-timeout/crash/state-moved-on/cleared-to-green/killed-or-crashed/state-db-parked/…), lines_emitted. An open row from a dead pid = the watcher/tailer died — for bg-watch that IS the stuck-blue bug |
| `ops` | paint op written to the mirror log | producer (script), op (the JSON paint op — full pane reconstruction, survives SessionEnd) |
| `errors` | swallowed exception | script, func, **traceback** (full), context (JSON of args in hand) |
| `spawns` | detached process launch | parent_script, child_pid, argv, purpose |
| `state_files` | coordination-file transition | path, action (write/remove/remove-stale/**bump/bump-agent/bump-transcript/msg-transitions/resume/final/keep-history/restore-history/reuse-live-db/fresh-db**), content (state-DB records — path is a `state:` key: `state:fg-live`, `state:done:<token>`, `state:agent.<id>`; for bump\* actions: the scoreboard deltas + resulting totals — the trail for wrong-scoreboard-number bugs). **bump-agent** = an agent streamer's spend bump, `meta` carries agent_id/kind/model + the in/out/cache/create split cost_usd priced — attribution and re-pricing need no timestamp correlation. **bump-transcript** now also carries `d_split` (the per-category token delta `tk_in`/`tk_out`/`tk_read`/`tk_create` feeding the scorebar's Σ row) alongside `d_tokens`/`d_cost` — and these rows are written from `claude-stop-fmt.py` on every `Stop`/`StopFailure` too, not just the cmd/file hooks (the Stop fold is what captures a turn's final tool-less reply). The per-category counters live in the state DB (`SELECT key,val FROM counters WHERE key LIKE 'tk_%'`); `tk_in+tk_create+tk_out` == the billed `tokens` counter (which backs `cost`; no longer shown on the `▪` row), and `+tk_read` is the Σ total. Scorebar `paused`-only ticks are NOT audited (1/s noise; the total rides every other bump's `now`). **resume/final** (path = `state:agent.<id>`) bracket each substream streamer: what checkpoint + dedup state it adopted (or `fresh: <why>`) and what it left behind — a successor's `resume` disagreeing with its predecessor's `final` is a broken handoff. **keep-history/restore-history/reuse-live-db/fresh-db** (path = `<log>.state.db.keep`, content = the SessionStart `source`) trace the session state DB's lifecycle: SessionEnd parks it as `*.keep` (`keep-history`); SessionStart either restores it (`restore-history`, resume of the same sid), leaves a live DB alone (`reuse-live-db`, compact or resume-after-crash), or starts fresh (`fresh-db`). The state DB IS the mirror content (its `ops` table) — so these rows are the resume-history trail |
| `pane_events` | mirror/scoreboard pane operation | action (open/close/toggle-on/toggle-off/grow/shrink/reset/setpct), **ok** (verified against kitty — 0 means the pane genuinely isn't there), detail (bias/resulting width). First stop for "frozen/missing pane" reports. Pruned with the other per-session tables (was once omitted — unbounded growth) |

New always-audited swallow sites (previously silent — their absence used to make these symptoms triage-blind): `errors` rows for `release`/`release_id`/`pid_del` (failed slot release = stuck blue), `spawn <script> (script missing)` + `notify_tab <dispatch>` from claude_hook (block never streams / dropped tab dispatch), `update_messages` from the scorebar (frozen ✉ row), `format_code` from claude_ops (commands paint verbatim), and `lsof failed/missing` from claude-stream (see the stream-ended-too-early shape).

## Triage order

1. **`python3 claude_audit.py anomalies <sid>`** — canned queries for known bug
   signatures: swallowed errors, streams that never ended, slot claims without
   release, tab left on a busy colour, duplicate SubagentStart, start-without-stop,
   failed tools, spawns that never registered a stream, pane operations that
   failed, tab applies where `kitten @` failed, a resume that lost its mirror
   history. Start here; a non-empty section usually IS the bug.
2. **`python3 claude_audit.py errors <sid>`** — full tracebacks for every swallowed
   exception. An error just before the symptom's timestamp is the prime suspect.
3. **`python3 claude_audit.py timeline <sid>`** — the merged chronological story
   (hooks, tab transitions, slots, streams, spawns, state files, pane ops, errors).
   Find the symptom's moment, then read the surrounding ~30 lines both ways.
4. **Free-form**: `python3 claude_audit.py sql "<query>"` — e.g. pull the full
   payload of one hook event, or diff `ops` against what the pane actually showed.

## Known bug shapes → what to look for

- **Tab stuck blue** — a `slots` claim (bg/fg/monitor/sub) with no release (cross-check
  the live truth: `sqlite3 .../claude-mirror-<sid>.log.state.db "SELECT * FROM live"` —
  a row whose pid is dead is stale-but-harmless, it's ignored by liveness checks) + a
  `streams` row with `ended_at IS NULL`, or a `tab_transitions` `bg-recheck`/`bg-watch`
  row with `applied=0` whose reason explains why it refused to clear. Also check the
  `bg-watch` **stream row itself**: `killed-or-crashed` / still-open = the watcher died
  and nothing was left to clear the blue; and an apply whose reason says
  "kitten @ failed rc=N" = the green WAS decided but never reached kitty.
- **Tab stuck magenta** — last transition is thinking/working and no later Stop:
  check `hook_events` for a missing Stop (cancelled turn — no hook fires), the
  `interrupt-watch` **stream row's end_reason** (`no-interrupt-within-30m` vs
  `killed-or-crashed` vs `turn-over` vs a bailed/deferred flip —
  `interrupt-seen-deferred-to-bg-recheck` means it saw the cancel on blue and
  handed recovery to writer-liveness; the watcher now spans the WHOLE turn, so a
  `turn-over` exit *before* the stuck stretch means it was killed or never
  respawned, not that it legitimately stopped at the first tool call), and
  whether the final apply carried a "kitten @ failed" reason.
- **Tab flips green too early** — a `bg-recheck`/`bg-watch`/`notify` transition with
  `applied=1` while a `streams` row was still open; the reason column shows what it
  (wrongly) concluded.
- **Tab shows a colour the audit says it shouldn't** — trust `applied=1` rows only:
  any transition with "kitten @ failed rc=N … state row unchanged" in the reason
  means the script decided a colour but kitty never showed it (dead socket, closed
  tab). The persisted state (the `tab` row in the global /tmp/claude-kitty-tab.db,
  keyed by window id) is written **only on applied paints**, so it always matches
  what the tab really shows and the next same-state event retries the paint —
  `sqlite3 /tmp/claude-kitty-tab.db "SELECT * FROM tab"` shows what's displayed;
  its `watchers` table holds the bg-watch/interrupt-watch pid locks. (A repeated
  "kitten @ failed" run followed by a "skipped: colour already shown" for the SAME
  state would mean the persist-on-failure bug regressed.)
- **Tab lost its red while a team ran** — look for an `agent-start` transition:
  `applied=0` + "red (awaiting-command) wins" is the guard working; an `applied=1`
  `agent-start` → awaiting-bg row while the previous state was awaiting-command
  means the red-wins guard regressed.
- **fg block shows the wrong outcome / a command never rendered** — the `fg-live`
  hand-off is keyed to its tool call (`tid`) and consumed with a matched take;
  check `state_files` `state:fg-live` rows: `write` (with `tid`) → `remove`
  (consumed by that same call's Post) is healthy; a cancelled command's record
  ends in `remove-own` (its exiting tailer reclaimed it) or `remove-stale` (next
  Pre found the pid dead). A `remove` whose consuming hook_event belongs to a
  *different* command means the tid keying regressed (the cross-wire bug).
- **Mirror replays a whole existing file as command output** — parse_redirect
  misread an argument as a redirect: check the cmd-pre `hook_events` decision
  ("tailing command's own redirect" for a command with a quoted `>`/heredoc means
  the quote-aware tokenizer regressed; correct behavior is "rewrote command (tee)").
- **Mirror block never closes** — the `streams` row's end_reason
  (backstop-timeout = the completion signal never came; crash = see `errors`);
  `state_files` shows whether the outcome hand-off (`state:done:<token>`) / the agent
  record's done flag (`state:agent.<id>`) was ever written. For a MONITOR block:
  an `idle-fallback` end is now also the escape for an ambiguous process match
  (multiple token hits, no full-command hit — see CLAUDE_MONITOR_CMD); a monitor
  stream open for hours with a live tailer pid suggests the wrong-pid latch
  regressed. A `■ monitor failed` chip with no stream row is normal — a failed
  Monitor call closes inline, no tailer is spawned. Substream/codex streams
  ending `state-db-parked (session end)` (and codex `(before header)`) are the
  healthy quit-while-running shape — deliberately footer-less, NOT a lost block.
- **Stream ended too early / output missing at the end** — check `errors` for
  "lsof failed — assuming writer still present" (transient lsof trouble is now
  survivable; a `writer-gone` end *without* such an error row and with the
  command demonstrably still running would be a new detection bug) and
  "lsof missing — writer-liveness disabled" (bg/fg completion is then backstop-only).
- **Frozen / missing / doubled pane** — `pane_events` first: an `open`/`toggle-on`
  with `ok=0` means the mirror (or the scoreboard bar — see detail) genuinely never
  opened; a resize whose detail shows an unchanged resulting width did nothing. Then
  cross-check `spawns` (was the renderer launched?) and `errors` (renderer crash).
- **Mirror came back empty after `--resume`/`--continue`** — the `state_files` DB-fate
  row next to the SessionStart tells you what happened to the history: `restore-history`
  = it WAS restored (an empty pane then points at the renderer — check `spawns`/`errors`,
  and whether the restored DB's `ops` table actually has rows);
  `fresh-db` on a `source=resume` start = the `*.keep` was missing (prior SessionEnd
  never ran its `keep-history`, or the 7-day sweep ate it — check the prior session's
  `pane_events` close row and its `keep-history` state row). The `anomalies` command
  flags the `fresh-db`-on-resume case directly. Pre-2026-07-04 builds always
  truncated on SessionStart — empty-on-resume there is the old design, not a bug.
- **Wrong scoreboard numbers** — replay the `state_files` `bump` / `bump-transcript`
  rows: each carries the delta AND the resulting totals, so find the exact bump where
  the running total diverges from what the session actually did (`hook_events` is the
  ground truth to diff against); `bump-transcript` rows also carry the `txpos` cursor —
  a cursor that jumps backwards or re-covers a range = double-counting. Plain `bump`
  rows carrying `files`/`added`/`removed` deltas come from TWO producers now: the main
  session's `claude-file-fmt.py` AND each agent's `claude-substream.py` `render_file`
  (team-wide file accounting — an agent file op has no `tool`-keyed `commands` delta, so
  a `bump` with a `Read`/`Edit`/`Write` tool + file/line deltas but NO matching
  main-session PostToolUse hook_event is the substream feeding it, not an anomaly). The
  `files` counter is a session-wide UNIQUE-path set, so its total can be LOWER than the
  count of file `bump` rows (same path touched by main + agents counts once) — that's
  correct, not a lost bump. `msg-transitions`
  rows are the same trail for the ✉ census (the tracker keys per `(recipient,
  msg_id)` copy — a broadcast to N teammates is N `new` events; one event for N
  copies, or `read` events exceeding deliveries, means the per-recipient keying
  regressed). For a wrong COST with right tokens, check the model id against
  `claude_ops.PRICES` substring keys — a legacy Opus id pricing at 5/25 means the
  `opus-4-2025`/`3-opus` keys regressed (the old `opus-4-0`/`opus-3` keys matched
  no real id).
- **Mirror resizes to the wrong width / preset lands far off** — the geometry
  walk in `claude-split.py mirror_geometry` resolves the mirror's `neighbors`
  chain through the tab's `groups` map; `pane_events` resize rows whose detail
  shows a target % wildly different from the visible pane (with the shell side
  hsplit) means the group-id resolution or the one-window-per-segment walk
  regressed to the old sum-all-columns behavior.
- **Codex run missing from (or duplicated across) same-repo sessions** — `slots` rows
  with kind `codex-claim`: `claim` = this session owns the run, `claim-denied` (+ the
  holder pid) = another session's watcher took it, `steal-stale` = a dead session's
  claim was taken over.
- **Command never appeared in the mirror** — `hook_events` decision column: was it
  "ignored: a live fg block is already in flight" (stale `fg-live` state record), "ignored:
  agent_id", or did the hook never fire at all?
- **Double-rendered subagent** — duplicate SubagentStart in `hook_events` where the
  second's decision is NOT "ignored: duplicate".
- **Cross-session contamination** — the same task_id/marker_path appearing under two
  session_ids.
- **Duplicated block/lines in the mirror** *(fixed 2026-07-04)* — tailers used an
  unbounded `read()` with `pos = size`, so bytes appended during the read were
  re-read next poll. If seen on a current build, check `ops` rows for repeated
  identical payloads seconds apart.
- **Stray `<target>.done` files in the project dir** *(fixed 2026-07-04)* — the fg
  `.done` sentinel used to be derived from the command's redirect target (unexpanded,
  cwd-relative). Now a session-keyed /tmp path; `state_files` shows every sentinel
  write path — any non-/tmp sentinel path on a current build is a regression.
- **Scoreboard tok/cost inflated vs `/cost`** — the trail is `state_files`:
  `bump-agent` rows are agent-streamer bumps (`meta` names the agent, model, and the
  in/out/cache/create split that was priced — pre-2026-07-04 sessions have plain
  `bump` rows instead, attributable only by ts against `streams.ended_at`);
  `bump-transcript` rows are the main session's own turns. Recompute ground truth
  from the named transcript (main: `sessions.transcript_path`; agents: `meta.src` /
  `streams.src_path`) deduped by `message.id` and diff against the bump deltas —
  whichever producer's delta exceeds its deduped source is the culprit. Tokens right
  but dollars wrong = re-run `cost_usd` on `meta.model` + the meta split: a pricing
  bug (`PRICES`), not a counting bug. Two fixed instances of the counting shape
  (usage summed per JSONL *line*, but one message = one line per content block):
  `bump_transcript()` *(fixed, `message.id` dedup + `txlast`)* and the agent
  streamers' footer rollup in `claude-substream.py` *(fixed 2026-07-04, `usage_last`
  + checkpoint line 2 — was ×2.24 on multi-block agents)*. Both now share ONE fold,
  `claude_ops.usage_fold()` (carry record `{"id","f":[in,out,cache,create]}` —
  `txlast`/`usage_last` both persist this shape; a `{"id","tok","usd"}` record is
  the pre-refactor shape, converted once by a compat branch), so a recurrence means
  either the shared fold itself or a producer bypassing it. For a suspected handoff
  double-count, diff the streamer's `resume` row against its predecessor's `final`
  row (path `sub.pos.<agent>`). The `anomalies` command flags any token/cost delta
  arriving as plain `bump` (unattributed producer) on a current build.
- **Scoreboard `Σ` total vs `/cost`'s token count** — the **`Σ` row** (`token_parts()`)
  is the token display: it sums the four `tk_*` counters into an all-in total that
  INCLUDES cache read, so `tk_in+tk_out+tk_read+tk_create` should match `/cost`'s
  four-category sum (dominated by cache read — tens of millions on a long session).
  The `▪` row no longer shows a `tok` chip (billed spend was dropped as redundant with
  Σ); the `tokens` counter still exists and backs the cost figure (`tk_in+tk_create+
  tk_out`). If the Σ total is short of `/cost`, it's the fold, not the metric — next.
- **Scoreboard cost a few % UNDER `/cost`** *(final-turn tail, fixed 2026-07-04)* —
  `bump_transcript` used to run ONLY from the Bash/file PostToolUse hooks, so a turn's
  closing reply (no trailing tool) and the whole last turn of a session were never
  folded; on a cache-heavy (fable) session the dropped final turn is dollars. Tell:
  the last `bump-transcript` row's `txpos` sits short of the transcript's byte size
  (`wc -c` the `sessions.transcript_path`), and re-folding to EOF recovers the gap.
  Fixed by `claude-stop-fmt.py` folding on every `Stop`/`StopFailure` (idempotent via
  the `txpos` cursor). On a current build, a `txpos` short of EOF with no later
  `bump-transcript` = the Stop hook never fired or isn't wired (check `hook_events`
  for a `Stop` subscriber row and a `claude-stop-fmt.py` decision row).

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table),
(3) the code path responsible (file + mechanism), (4) a suggested fix. If the
evidence is inconclusive, say exactly which signal is missing and what extra
instrumentation would capture it next time.
