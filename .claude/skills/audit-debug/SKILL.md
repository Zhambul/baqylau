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
| `hook_events` | hook invocation | hook, tool_name, agent_id ('' = main session), handler (script), **decision** (what the handler chose to do), **payload** (full hook stdin JSON, verbatim). Since the single-dispatcher refactor, **all hook events are wired to one entry (`claude-hook.py` → `plugins/claude_code/dispatch.py`)** which fans out in-process; `handler` is NOT argv[0] (that would be `claude-hook.py` for everything) but an explicit per-subsystem override the dispatcher stamps (`audit.set_handler`), so the vocabulary below is unchanged (`claude-cmd-fmt.py`, `claude-tab-status.py` transitions, etc.). The universal subscriber row (handler = 'subscriber', empty decision) is now written **in-process by the dispatcher** at the end of `route()` rather than by a separate async settings entry — same row, same coverage. **New tell:** a `subscriber` row for an event that SHOULD have a functional handler row (e.g. a `PostToolUse`+`Bash` with a `subscriber` row but no `claude-cmd-fmt.py` decision row) = the dispatcher dropped/crashed that step — check `errors` for a `script='claude-cmd-fmt.py'` (or `script='dispatch'`) row. ALL 30 hook events are recorded via the subscriber (handler = 'subscriber', empty decision) — incl. PermissionRequest/Denied, PostToolBatch, MessageDisplay, TeammateIdle, Pre/PostCompact, ConfigChange, CwdChanged, FileChanged, Worktree\*, Elicitation\*, Setup, UserPromptExpansion, InstructionsLoaded — on top of the mirror handlers' own decision-carrying rows for the events they process. So "did event X even fire?" is always answerable from the subscriber rows, and a handler row can be cross-checked against the subscriber's independent record. Since 2026-07-07 a `codex-session` handler also appears — the STANDALONE codex host's own SessionStart hook (`claude-codex-session.py`), the one `hook_events` row keyed to a *codex* session id rather than a Claude one; decisions: `standalone-open (<fate>, host_pid=N)`, `nested-skip (host mirror <sid> present)` (codex ran as a Claude subagent — that session's watcher already streams it), `no session_id`, `no usable frontend`. Since 2026-07-08, `claude-stop-fmt.py` also produces `stopfail: …` decisions (mirroring `claude-subagent-fmt.py`'s `stop: …` set — `done flag set, streamer will finalise` / `SAFETY NET footer …` / `no-op …` / `never started …`): a `StopFailure` carrying an `agent_id` is a subagent turn that DIED on an API error and fired no `SubagentStop`, so stop-fmt hands it to the shared subagent finaliser instead of ignoring it — the `stopfail:` prefix distinguishes this recovery from a normal `SubagentStop`. |
| `tab_transitions` | tab-colour decision | dispatch (raw arg: pretool/stop/bg-recheck/bg-watch/notify/…), prev_state → new_state, applied (0 = skipped/bailed **or the kitten @ call failed** — reason then carries "kitten @ failed rc=N"), **reason**. Literal-state dispatches (SessionStart `idle`, SessionEnd `clear`) are sid-attributed since 2026-07; in older sessions those rows have `session_id=''`, so a per-sid query missed the final clear |
| `slots` | palette/liveness-slot event (rows of the session state DB's `live` table — were marker files) | kind (bg/monitor/fg/sub), slot_n, agent_id, owner_pid, action (claim/claim-id/**claim-pid**/steal-stale/claim-denied/release/release-id/**release-pid**/set-owner), marker_path (now an opaque `<log>::live:<kind>.<key>` token). To see the CURRENT slot state: `sqlite3 /tmp/claude-mirror-<sid>.log.state.db "SELECT * FROM live"` |
| `streams` | detached tailer/streamer/watcher | kind (fg/bg/monitor/subagent/teammate/codex/codex-watcher/**bg-watch/interrupt-watch**), agent_id/task_id, src_path, pid, started_at/ended_at, **end_reason** (writer-gone/sentinel/stop-sentinel/stoppedByUser/**parent-task-resolved**/converted-ctrl-b/backstop-timeout/crash/state-moved-on/cleared-to-green/killed-or-crashed/state-db-parked/…), lines_emitted. `parent-task-resolved` (subagent/teammate) = a REJECTED/abandoned Task recovered via the parent transcript's `tool_result` (no `SubagentStop`, no `stoppedByUser` ever fired) — the streamer keyed on the agent's `meta.json` `toolUseId`; `… (rejected)` when that result was `is_error`. NB an ASYNC (background) agent's Task resolves the parent `tool_result` IMMEDIATELY with a synthetic *"Async agent launched successfully"* ack (`is_error` absent) meaning launched-not-finished — `parent_tool_result()` ignores that ack (else the streamer ended ~2s in with `lines_emitted=0` and the agent's whole transcript never rendered; the `async launch-ack ended the substream early` anomaly flags a `parent-task-resolved`/0-lines stream whose agent later got a real `SubagentStop`). It pairs with a `SubagentStart without SubagentStop` (that anomaly still fires — Claude Code emitted no stop — but the stream properly ENDED, so it is the RECOVERED case, not a hang). A `fg` stream with `.subfg.<tid>.out` in `src_path` is a SUBAGENT's foreground command tailed live (spawned by `claude-substream.py`), not a main-session fg command. An open row from a dead pid = the watcher/tailer died — for bg-watch that IS the stuck-blue bug. A `codex-watcher` whose `src_path` starts `standalone:` is a STANDALONE codex host manager (spawned with a `HOST_PID`): it streams only its own session's rollout and owns teardown when the codex process dies (the codex analogue of SessionEnd — see the standalone shape below). Since the OTEL cost pipeline, a `kind='otlp'` row is the GLOBAL (per-machine, not per-session) OTLP metrics receiver — `session_id='otlp-receiver'`, `src_path='127.0.0.1:<port>'`; it outlives individual sessions and idle-exits, so an OPEN otlp row while it runs is NORMAL (like a live codex-watcher), and a `duplicate (…)` end_reason is a second receiver that correctly lost the singleton guard, not a bug |
| `ops` | paint op written to the mirror log | producer (script), op (the JSON paint op — full pane reconstruction, survives SessionEnd) |
| `errors` | swallowed exception | script, func, **traceback** (full), context (JSON of args in hand) |
| `spawns` | detached process launch | parent_script, child_pid, argv, purpose |
| `state_files` | coordination-file transition | path, action (write/remove/remove-stale/**copy/bump/bump-agent/bump-transcript/msg-transitions/resume/final/reconcile/keep-history/restore-history/reuse-live-db/fresh-db**), content (state-DB records — path is a `state:` key: `state:fg-live`, `state:done:<token>`, `state:subfg:<tid>` (subagent live-fg tee hand-off: `write` by cmd-pre, `remove` when the substream consumes it), `state:agent.<id>`; for bump\* actions: the scoreboard deltas + resulting totals — the trail for wrong-scoreboard-number bugs). **bump-otel** (path = the state DB file) = the OTLP receiver's aggregated per-POST write: content carries the summed `deltas` (`tk_*`/`cost`/`tokens`/`otel_cost:<query_source>`) + resulting `now` totals. This is the PRIMARY cost producer now (the raw datapoints behind it are in the `otel` table). **bump-agent** is now ONLY codex (its separate process can't export OTEL, so it keeps its own rollout fold); a Claude subagent no longer bump-agents (OTEL's `query_source=subagent` books it). **bump-agent** = an agent streamer's spend bump, `meta` carries agent_id/kind/model + the in/out/cache/create split that was priced (since 2026-07-08 also `create_1h`, the 1-hour-TTL cache-write share — it bills 2× input where 5m bills 1.25×, so re-pricing needs it) — attribution and re-pricing need no timestamp correlation; `meta.kind` is `subagent`/`teammate` (priced by `accounting.cost_usd`) or, since 2026-07-07, `codex` (a rollout run's cumulative `token_count` fold, priced by the codex plugin's own `CODEX_PRICES`; `meta.src` is the rollout path); **reconcile** (path = `state:agent.<id>`) = `claude-subagent-fmt.py` at SubagentStop folded the agent's transcript and recorded the residual over the `billed:<agent>` baseline. Since the OTEL pipeline it NO LONGER bumps counters (OTEL's `query_source=subagent` books agent spend live, including a crashed streamer's tail) — the row is now a pure OTEL-vs-transcript CROSS-CHECK (content: `residual`, `cost`, transcript `true` total). Idempotent — a clean finish leaves `true` == baseline, so no row. **bump-transcript** (the transcript fold) is now a FALLBACK ONLY — it fires from `claude-stop-fmt.py` on `SessionEnd` and ONLY when the OTLP receiver wrote nothing for the session (`otel_seen==0`: telemetry off / receiver down / machine without the env). In the normal path there are NO bump-transcript rows at all (OTEL owns cost); a bump-transcript row means the session ran without telemetry and the fold recovered it. It carries `d_split` (`tk_in`/`tk_out`/`tk_read`/`tk_create`) and `d_create_1h` alongside `d_tokens`/`d_cost`. A bump-transcript row AND bump-otel rows for the SAME session = the `otel_seen` gate broke (double-count regression — its own anomaly). The per-category counters live in the state DB (`SELECT key,val FROM counters WHERE key LIKE 'tk_%'`); `tk_in+tk_create+tk_out` == the billed `tokens` counter (which backs `cost`; no longer shown on the `▪` row), and `+tk_read` is the Σ total. Scorebar `paused`-only ticks are NOT audited (1/s noise; the total rides every other bump's `now`). **resume/final** (path = `state:agent.<id>`) bracket each substream streamer: what checkpoint + dedup state it adopted (or `fresh: <why>`) and what it left behind — a successor's `resume` disagreeing with its predecessor's `final` is a broken handoff. **keep-history/restore-history/reuse-live-db/fresh-db** (path = `<log>.state.db.keep`, content = the SessionStart `source`) trace the session state DB's lifecycle: SessionEnd parks it as `*.keep` (`keep-history`); SessionStart either restores it (`restore-history`, resume of the same sid), leaves a live DB alone (`reuse-live-db`, compact or resume-after-crash), or starts fresh (`fresh-db`). The state DB IS the mirror content (its `ops` table) — so these rows are the resume-history trail. **copy** (path = the state DB file) = a ⧉ copy-link click handled by `claude-copy.py` — content carries `gid` (the block's copy-group id: the Bash tool_use_id or the backgroundTaskId), `what` (`cmd`/`out`) and `chars` (0 = the group held nothing of that type); every FAILED click lands in `errors` instead, func `copy (bad url)` / `copy (state DB gone — session over?)` / `copy (read ops)` / `copy (no clipboard tool)`. **render:\<taskid\>** (path) = a `claude-stream.py` content-rendering stream — markdown (`cat`/`head`/`tail` of a `.md`, `CLAUDE_MIRROR_MD`), JSON (`cat` of a `.json`, `CLAUDE_MIRROR_JSON`), YAML (`.yml`/`.yaml`, `CLAUDE_MIRROR_YAML`) source code (`.py`/`.java`/`.kt`/`.sh` etc, `CLAUDE_MIRROR_CODE` — `kind` is `code:<lexer>`), or a fg stream whose OUTPUT was sniffed to contain a fenced code block (no filename hint, `CLAUDE_MIRROR_MD_SNIFF` — `kind` is `md-sniff`, and there is no cmd-pre `[*-render]` decision because the decision was made from content, not the command): action `start` (content `kind`, + `wenmode` = was the md parser importable, else it degraded to the `render.markdown()` subset) and action `done` (content `kind` + `blocks` = how many rendered gut ops it emitted; JSON/YAML/code are 1). `blocks: 0` from a stream that ran = a render failure (its own anomaly, below). Only markdown fenced code blocks render as a full-width panel — an `ops` gut row with a `bg` field; JSON/YAML/code colour on the normal gutter (no `bg`) |
| `pane_events` | mirror/scoreboard pane operation | action (open/close/toggle-on/toggle-off/grow/shrink/reset/setpct), **ok** (verified against kitty — 0 means the pane genuinely isn't there), detail (bias/resulting width). First stop for "frozen/missing pane" reports. Pruned with the other per-session tables (was once omitted — unbounded growth) |
| `otel` | ONE raw OpenTelemetry metric datapoint | metric (`token`/`cost`), query_source (**`main`/`subagent`/`auxiliary`** — auxiliary = Claude Code's hidden summarizer/title agents), model, type (`input`/`output`/`cacheRead`/`cacheCreation`; empty for cost), value, pid. Written by the global OTLP receiver (`plugins/otel/`, entry `claude-otlp-receiver.py`), one row per datapoint per POST, so the scoreboard cost/token counters are fully reconstructible: `SELECT type, SUM(value) FROM otel WHERE session_id=? AND metric='token' GROUP BY type` == the `tk_*` counters, and `SUM(value) WHERE metric='cost'` == the `cost` counter (incl. the auxiliary share transcript folding never saw). Summarised by `python3 claude_audit.py otel <sid>`. This IS the cost ground truth now — the transcript is only a fallback source (see the cost shapes below). NB the receiver's `bump-otel` `now` totals are read from whatever DB the receiver's cached connection points at, so they can look healthy while the LIVE state DB (what the scorebar reads) accrues nothing — a park+resume inode swap that stranded the receiver on the `*.keep` file (the blank-Σ shape; `anomalies` cross-checks the live DB's `tk_*`/`tokens` counters against the presence of `bump-otel` rows) |

New always-audited swallow sites (previously silent — their absence used to make these symptoms triage-blind): `errors` rows for `release`/`release_id`/`pid_del` (failed slot release = stuck blue), `spawn <script> (script missing)` + `notify_tab <dispatch>` from claude_hook (block never streams / dropped tab dispatch), `update_messages` from the scorebar (frozen ✉ row), `format_code` from core/ops (commands paint verbatim), and `lsof failed/missing` from claude-stream (see the stream-ended-too-early shape).

## Triage order

1. **`python3 claude_audit.py anomalies <sid>`** — canned queries for known bug
   signatures: swallowed errors, streams that never ended, slot claims without
   release, tab left on a busy colour, duplicate SubagentStart, start-without-stop,
   **stop-without-start (hidden agents — spend likely missing from the scoreboard)**,
   failed tools, spawns that never registered a stream, pane operations that
   failed, tab applies where `kitten @` failed, a resume that lost its mirror
   history, **OTLP writes stranded on a parked inode (bump-otel rows but the live
   state DB has no token counters — the blank-Σ/breakdown bug)**. Start here; a
   non-empty section usually IS the bug. (The hook-counting
   queries filter `handler != 'subscriber'` where a per-event count matters — the
   universal subscriber writes a second row for every event, which once made every
   normally-started agent read as "duplicate SubagentStart".)
2. **`python3 claude_audit.py errors <sid>`** — full tracebacks for every swallowed
   exception. An error just before the symptom's timestamp is the prime suspect.
3. **`python3 claude_audit.py timeline <sid>`** — the merged chronological story
   (hooks, tab transitions, slots, streams, spawns, state files, pane ops, errors).
   Find the symptom's moment, then read the surrounding ~30 lines both ways.
4. **Free-form**: `python3 claude_audit.py sql "<query>"` — e.g. pull the full
   payload of one hook event, or diff `ops` against what the pane actually showed.

## Known bug shapes → what to look for

- **A whole event's effect is missing — no block, no tab change, no formatting**
  (since the single-dispatcher refactor: all events run through `claude-hook.py` →
  `dispatch.py`). Cross-check the event's `hook_events` rows: the `subscriber` row
  should ALWAYS be there (the dispatcher writes it last, so its presence proves the
  dispatcher ran and parsed the payload); the **functional handler row is what's
  missing** (e.g. a `PostToolUse`+`Bash` with a `subscriber` row but no
  `claude-cmd-fmt.py` decision row, or a `Stop` with no `claude-tab-status.py`
  transition). That means the dispatcher dropped/crashed that one step — look in
  `errors` for a row whose `script` is that subsystem's entry filename
  (`claude-cmd-fmt.py`, `claude-tab-status.py`, …) or `script='dispatch'` (the
  dispatcher's own top-level swallow). If EVEN the `subscriber` row is missing, the
  hook never fired at all (wiring/cancel — see the no-hook shapes below), not a
  dispatch bug. Note `handler` is stamped explicitly by the dispatcher, so it still
  reads `claude-cmd-fmt.py` etc., never `claude-hook.py`.
- **Timings or paths look impossible** (grace periods way too short, state DBs
  not under `/tmp/claude-mirror-…`, a tailer that gave up in under a second) —
  check the session's `sessions.env` column for test-suite seams
  (`CLAUDE_MIRROR_TMPDIR`, `CLAUDE_TAIL_*`, `CLAUDE_STREAM_*`,
  `CLAUDE_WATCH_*`, README § Testing): the "session" is probably a test run,
  not a real one.
- **Tab stuck blue** — a `slots` claim (bg/fg/monitor/sub) with no release (cross-check
  the live truth: `sqlite3 .../claude-mirror-<sid>.log.state.db "SELECT * FROM live"` —
  a row whose pid is dead is stale-but-harmless, it's ignored by liveness checks) + a
  `streams` row with `ended_at IS NULL`, or a `tab_transitions` `bg-recheck`/`bg-watch`
  row with `applied=0` whose reason explains why it refused to clear. Also check the
  `bg-watch` **stream row itself**: `killed-or-crashed` / still-open = the watcher died
  and nothing was left to clear the blue; and an apply whose reason says
  "kitten @ failed rc=N" = the green WAS decided but never reached kitty.
  A specific stuck-blue shape: a **live `sub.pid` slot row whose (real, alive) pid is a
  `claude-substream.py` process** for an agent with `SubagentStart` but **no `SubagentStop`**
  (`hook_events`) — that agent's Task was **rejected at the permission prompt** (parent
  transcript: `tool_result … is_error=True … "doesn't want to proceed"`), which fires no
  `SubagentStop` and stamps no `meta.json` `stoppedByUser`. On a current build the streamer
  recovers via the parent transcript and the `subagent`/`teammate` stream ends
  `parent-task-resolved (rejected)`; an **open** substream stream for such a rejected agent
  (no `parent-task-resolved` end, streamer still tailing hours later) = that recovery
  regressed — check `meta.json` actually carries a `toolUseId` and the parent transcript
  holds the matching `tool_result`.
  A second stuck-blue shape (same live `sub.pid` + open substream, but a DIFFERENT
  cause): a subagent turn that **died on an API error** — its `hook_events` show a
  `StopFailure` carrying that `agent_id` (payload `error:"server_error"`,
  `last_assistant_message` an `API Error: 529 Overloaded …`) and **no `SubagentStop`
  ever** (the `SubagentStart without SubagentStop` anomaly). Claude Code fires no
  `SubagentStop` and stamps no `stoppedByUser`, and for an ASYNC background agent the
  parent `tool_result` is only the "Async agent launched successfully" ack
  (`is_error` absent → `parent-task-resolved` never fires) — so on a pre-fix build the
  streamer had NO end signal and hung. On a current build `claude-stop-fmt.py` hands
  that `StopFailure` to the subagent finaliser (`subagent_fmt.finalize`), which sets the
  agent's `done` flag → the streamer exits `stop-sentinel` and releases the slot. The
  handler's decision carries a `stopfail:` prefix (recovered). The `anomalies`
  **"StopFailure carrying an agent_id NOT handed to the finaliser"** section flags ONLY
  the regressed case — a `StopFailure`+`agent_id` whose decision is NOT `stopfail:`
  (the old `ignored: agent_id (substream owns agent accounting)`) — so a healthy
  recovered session stays clean and a non-empty row there IS the stuck-blue bug. Confirm
  by whether the `subagent` stream ended (`stop-sentinel`) or is still open.
- **An async (background) subagent barely appears in the mirror — its block is empty
  / cut off almost immediately** *(async launch-ack, fixed 2026-07-11)* — the parent
  transcript resolves an ASYNC agent's Task IMMEDIATELY with a synthetic *"Async agent
  launched successfully"* `tool_result` (`is_error` absent) that means LAUNCHED, not
  finished. `parent_tool_result()` (`plugins/claude_code/model.py`) must ignore that
  ack; treating it as a resolution ended the substream ~2s after launch via
  `parent-task-resolved` with `lines_emitted=0`, so the agent's whole (later) transcript
  never rendered. Tell: a `subagent`/`teammate` `streams` row ending `parent-task-resolved`
  (NOT `(rejected)`) with **`lines_emitted=0`** while a real `SubagentStop` for that agent
  fired LATER in `hook_events` (and its `state:agent.<id>` `final` row shows a `pos` far
  short of the on-disk `subagents/agent-<id>.jsonl` size). The `anomalies`
  **"async launch-ack ended the substream early (0 lines rendered)"** section flags exactly
  this; a non-empty row on a current build is the regression (the launch-ack guard broke).
  Distinct from the rejected shape below — that one is `parent-task-resolved (rejected)`
  with `is_error=True` and no later SubagentStop.
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
- **A `.md`/`.json`/`.yml`/source file rendered raw (or garbled) instead of
  pretty** — content render mode (markdown: `.md`; JSON: `.json`; YAML:
  `.yml`/`.yaml`; source code: `.py`/`.java`/`.kt`/`.sh` etc; README § Command
  mirror pane). First confirm it was even engaged: the cmd-pre `hook_events`
  decision ends in `[md-render]`/`[json-render]`/`[yaml-render]`/`[code-render:<lexer>]`,
  and a `state_files` `render:<taskid>` `start` row exists (content `kind`) — if
  neither, the `*_source` detector didn't match (piped/redirected/chained command,
  a non-allowlisted tool like `bat`/`glow`/`jq`/`yq`, running the file rather than
  reading it — `python foo.py` — `head`/`tail` of a `.json` since JSON needs the
  whole file, or `CLAUDE_MIRROR_MD`/`_JSON`/`_YAML`/`_CODE=0`), so raw is expected.
  A command that *prints* markdown to stdout (no `.md` file) is caught only by the
  content sniff (`kind: md-sniff`, `CLAUDE_MIRROR_MD_SNIFF`): it needs a real
  fenced code block (` ```lang `) in the **first** data-bearing read — a fence in a
  later chunk is missed by design (liveness > late detection), so prose streamed
  before the fence renders verbatim. No `md-sniff` `start` row + raw markdown in
  the mirror = no fence in the first read (expected), not a bug. If engaged but the
  output is missing/garbled: for markdown the `start` row's `wenmode` field says
  whether the parser was importable (`false` → fell back to the `render.markdown()`
  subset — line-oriented, no tables/fenced blocks/nesting; install `wenmode`); for
  JSON, an invalid/truncated document renders verbatim by design (no panel). The
  `done` row's `blocks` count — **`blocks: 0`** (surfaced by `anomalies`) means the
  renderer produced nothing (a parse crash — check `errors` for `claude-stream`, or
  an empty source). A stray literal `#`/`**`/raw JSON in the mirror with NO
  `render:` rows is just a normal verbatim stream, not a bug.
- **⧉ copy link does nothing / copies the wrong thing** — a healthy click leaves a
  `state_files` row, action `copy` (content: gid/what/chars). NO row at all for the
  click means kitty never launched the handler — the `open-actions.conf` wiring
  (README § Wiring), not this repo's code; otherwise check `errors` for func
  `copy (…)`: `bad url` (renderer built a malformed link), `state DB gone` (clicked
  after SessionEnd — expected no-op), `read ops` / `no clipboard tool`. `chars: 0`
  with what=`out` on a still-running block just means no output had streamed yet.
  Wrong TEXT copied: compare the group's ops (`SELECT op FROM ops` in the state DB,
  filter `"g"` = the gid from the audit row) — ⧉cmd must equal the `code` op's `s`
  (the WYSIWYG pretty-printed form, deliberately NOT the pre-reflow original) and
  ⧉out the ANSI-stripped concatenation of the group's `gut` ops.
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
- **Wrong scoreboard COST/TOKENS** — cost/tokens are OTEL-authoritative now. Start
  from `python3 claude_audit.py otel <sid>`: the raw `otel` datapoints ARE the ground
  truth (they mirror what `/cost` bills, `main`/`subagent`/`auxiliary` broken out).
  `SUM(otel.value) GROUP BY type` must equal the `tk_*` counters and the `bump-otel`
  running totals; a divergence there is a receiver write bug. If the `otel` table is
  EMPTY for a busy session, the receiver never got the metrics — check (1) the
  telemetry env in `~/.claude/settings.json` (`CLAUDE_CODE_ENABLE_TELEMETRY=1`,
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:<CLAUDE_OTEL_PORT>`), (2) a `kind='otlp'`
  `streams` row (did the receiver spawn? — `plugins/otel/on_session_start` only spawns
  when telemetry is enabled), (3) `errors` for `func` `otel …`. With telemetry OFF the
  SessionEnd transcript FALLBACK should have fired instead (a `bump-transcript` row +
  a `claude-stop-fmt.py` `otel absent — folded transcript fallback` decision); its
  absence too means cost is genuinely $0/unrecorded. For a wrong COST with right
  tokens on a codex run, the model fell through `CODEX_PRICES` (codex keeps its own
  fold). The pre-OTEL transcript-fold shapes below (final-turn tail, hidden-agent gap,
  Σ-short) only apply to a FALLBACK fold or a pre-migration session.
- **No token/Σ breakdown at all despite OTEL data present** *(receiver stranded on a
  parked inode, fixed 2026-07-11)* — the scorebar's `Σ`/cost row is blank (or frozen at
  a stale value) even though `python3 claude_audit.py otel <sid>` shows healthy datapoints
  and `bump-otel` `state_files` rows report climbing `now.tokens`/`now.cost`. The tell is
  a DIVERGENCE the audit trail alone hides: the `bump-otel` rows look fine (their `now`
  is read from whatever DB the receiver writes), but the **LIVE state DB the scorebar
  reads has no `tk_*`/`tokens`/`cost` counters** — check directly:
  `sqlite3 /tmp/claude-mirror-<sid>.log.state.db "SELECT key,val FROM counters WHERE key LIKE 'tk_%' OR key='tokens'"`
  (empty = stranded). Root cause: the long-lived singleton OTLP receiver cached its
  SQLite connection by PATH, but a `--compact`/`--resume` cycle parked the DB
  (`os.replace(db, db+".keep")` — an inode rename) and created a fresh live DB at the
  same path; the receiver's cached fd followed the OLD inode to `*.keep` and its counter
  writes landed there silently (no error — both are valid DBs), invisible to the scorebar.
  Confirm decisively: `lsof -nP | grep 'mirror-<sid>.log.state.db'` — the receiver pid
  holding a `…state.db.keep` fd while the renderer/scorebar hold the live `…state.db` IS
  the bug. Fixed in `core/state._connect` by revalidating the cached connection against
  `os.stat(path).st_ino` (reconnect on a fresh inode; keep the stale conn — never recreate
  — when the path is merely parked/gone). The `anomalies` command flags it directly:
  **"OTLP writes stranded on a parked inode (bump-otel rows but live DB has no token
  counters)"** — a non-empty row on a current build is the regression. Note the pre-park
  spend survives in `…state.db.keep`; only counters after the swap are diverted.
- **Wrong scoreboard FILES/COMMANDS counts** — replay the `state_files` `bump`
  rows: each carries the delta AND the resulting totals, so find the exact bump where
  the running total diverges from what the session actually did (`hook_events` is the
  ground truth to diff against). Plain `bump`
  rows carrying `files`/`added`/`removed` deltas come from TWO producers now: the main
  session's `claude-file-fmt.py` AND each agent's `claude-substream.py` `render_file`
  (team-wide file accounting — a `bump` with a `Read`/`Edit`/`Write` tool + file/line
  deltas but NO matching main-session PostToolUse hook_event is the substream feeding it,
  not an anomaly). **`commands`/`failed` are team-wide the same way** (fixed 2026-07-06):
  the substream's `on_tool_result` bumps `tool=Bash, commands=1` (+`failed=1` on
  `is_error`) for each subagent Bash call, since `claude-cmd-fmt.py` skips `agent_id`
  events — so a `bump` with `tool=Bash` + a `commands`/`failed` delta and NO matching
  main-session PostToolUse(Bash) hook_event is a SUBAGENT command (its `PostToolUse`/
  `PostToolUseFailure` carries an `agent_id`), not a lost or phantom bump. Before the
  fix the `▪` row's `N cmds (M✗)` counted the LEAD's Bash only — a session whose failures
  were all inside subagents showed `(0✗)` (or no `failed` counter at all) despite
  `hook_events` holding `PostToolUseFailure` rows with an `agent_id`; that mismatch on a
  pre-fix build is the tell. The
  `files` counter is a session-wide UNIQUE-path set, so its total can be LOWER than the
  count of file `bump` rows (same path touched by main + agents counts once) — that's
  correct, not a lost bump. `msg-transitions`
  rows are the same trail for the ✉ census (the tracker keys per `(recipient,
  msg_id)` copy — a broadcast to N teammates is N `new` events; one event for N
  copies, or `read` events exceeding deliveries, means the per-recipient keying
  regressed).
- **Mirror resizes to the wrong width / preset lands far off** — the geometry
  walk (`frontends/kitty.py` `split_geometry`, reached via
  `plugins/claude_code/split.py mirror_geometry`) resolves the mirror's
  `neighbors` chain through the tab's `groups` map; `pane_events` resize rows whose detail
  shows a target % wildly different from the visible pane (with the shell side
  hsplit) means the group-id resolution or the one-window-per-segment walk
  regressed to the old sum-all-columns behavior.
- **Codex run missing from (or duplicated across) same-repo sessions** — `slots` rows
  with kind `codex-claim`: `claim` = this session owns the run, `claim-denied` (+ the
  holder pid) = another session's watcher took it, `steal-stale` = a dead session's
  claim was taken over. NB `codex-claim` rows are permanent OWNERSHIP records, not
  slot lifecycles — the `slot claims without a matching release` anomaly excludes
  them (and `claim-denied` generally: nothing acquired, nothing to release); a
  current build flagging one there means that exclusion regressed.
- **Codex tokens/cost missing from Σ (or wrong)** — a ROLLOUT-sourced codex run
  folds its cumulative `token_count` usage into the scoreboard ONCE at its footer:
  a `bump-agent` `state_files` row with `meta.kind: "codex"` (model + in/out/cache
  split, `src` = the rollout path — re-derivable ground truth). Missing row with a
  `streams` kind=`codex` row ending normally = the fold regressed; missing for a
  COMPANION (.log) run is by design (its usage isn't in the activity log). Tokens
  right but no `≈ $` on the footer = the model fell through `CODEX_PRICES`
  (plugins/codex/stream.py — version-exact prefix match; unverified newer versions
  deliberately show no cost). Codex file edits (`patch_apply_end`) bump
  files/±/Edit/Write as plain `bump` rows — file deltas, exempt from the
  unattributed-bump anomaly, same as substream file ops.
- **Standalone codex: mirror never appeared / never closed** — a `codex` run on
  its OWN (no Claude session) is hosted by codex's native SessionStart hook
  (`claude-codex-session.py`). Triage in order: (1) **did the hook fire?** — a
  `hook_events` `codex-session` row keyed to the codex session id. Absent = the
  codex-side wiring is off (`~/.codex/config.toml` `[features] hooks`, `~/.codex/
  hooks.json`, or the hook was never trusted via `/hooks` — codex silently skips
  untrusted hooks). (2) **decision** — `no usable frontend` (not in kitty / no
  remote control), `nested-skip …` (correct when codex ran under Claude — that
  session's watcher shows it), or `standalone-open (<fate>, host_pid=N)` (opened).
  (3) **never closed** — the standalone `codex-watcher` (`src_path` `standalone:…`)
  tears down when `host_pid` dies; an open `streams` row for it with the codex
  process long gone = the pid-liveness teardown didn't fire (the DB never got
  parked → the scoreboard bar also never exited). A `pane_events` `close` row with
  detail `standalone codex host exited` + a `keep-history` state row (content
  `codex host pid gone`) is the healthy teardown trail; their absence pinpoints it.
- **Command never appeared in the mirror** — `hook_events` decision column: was it
  "ignored: a live fg block is already in flight" (stale `fg-live` state record), "ignored:
  agent_id", or did the hook never fire at all?
- **A subagent's foreground command doesn't stream live (output only at the end)** —
  expect, in order: a `claude-cmd-pre.py` decision `subagent live fg: marker written`
  on the `agent_id` event, a `state:subfg:<tid>` `write` then `remove`, and a `streams`
  `fg` row with `.subfg.<tid>.out` in `src_path`. A missing decision (or `ignored:
  agent_id (CLAUDE_MIRROR_LIVE_FG_SUB=0)`) = feature off (check `sessions.env`), the
  by-design at-completion fallback. A `write` with no `remove` = the substream never
  spawned the tailer. Output appearing twice = suppression failed (kind wasn't `fg-live`).
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
  bug (`PRICES`), not a counting bug. Two fixed pricing instances: legacy Opus ids
  falling through to the generic 5/25 row (`opus-4-2025`/`3-opus` keys), and 1-hour
  cache writes priced at the 5m 1.25× instead of 2× *(fixed 2026-07-08 — usage's
  `cache_creation.ephemeral_1h_input_tokens` is now the 5th `usage_fields` field and
  rides bump meta as `create_1h`; a session whose writes are ALL 1h — the shape that
  exposed it — undercounted ~$0.9)*. Two fixed instances of the counting shape
  (usage summed per JSONL *line*, but one message = one line per content block):
  `bump_transcript()` *(fixed, `message.id` dedup + `txlast`)* and the agent
  streamers' footer rollup in `claude-substream.py` *(fixed 2026-07-04, `usage_last`
  + checkpoint line 2 — was ×2.24 on multi-block agents)*. Both now share ONE fold,
  `plugins/claude_code/accounting.py` `usage_fold()` (carry record
  `{"id","f":[in,out,cache,create,create_1h]}` — `txlast`/`usage_last` both persist
  this shape; a 4-int `f` is the pre-create_1h shape, zero-padded by the fold; a
  `{"id","tok","usd"}` record is
  the pre-refactor shape, converted once by a compat branch), so a recurrence means
  either the shared fold itself or a producer bypassing it. For a suspected handoff
  double-count, diff the streamer's `resume` row against its predecessor's `final`
  row (path `sub.pos.<agent>`). The `anomalies` command flags any token/cost delta
  arriving as plain `bump` (unattributed producer) on a current build.
- **Scoreboard UNDER `/cost`, an AGENT's spend short** *(streamer crash lost the tail,
  fixed 2026-07-06)* — the streamer bumps an agent's spend only at its footer, so a
  crash/kill *before* the footer drops the un-bumped tail. Tell: a `streams` row for a
  `subagent`/`teammate` ending `crash` (+ an `errors` row from `claude-substream.py`),
  and that agent's summed `bump-agent` deltas falling short of its own transcript
  (`meta.src`) deduped to EOF. Now recovered at SubagentStop by `reconcile_spend`
  (`claude-subagent-fmt.py`): look for a **reconcile** `state_files` row (path
  `state:agent.<id>`) — its `residual` is the recovered tail, `true` the transcript
  total, and a following `bump-agent` with `meta.reconcile` true carries it into the
  scoreboard. On a current build, a crashed agent streamer with NO reconcile row *and*
  a `bump-agent` baseline short of its deduped transcript = the recovery regressed
  (or the SubagentStop hook never fired — check `hook_events` for a
  `claude-subagent-fmt.py` `stop` decision). NB the `.strip()`-on-dict crash at the
  old `on_tool_use` SendMessage path was the original trigger — a substream `errors`
  row with `'dict' object has no attribute 'strip'` on a current build is that
  regression. This is a *transcript-resident* shortfall; a shortfall vs `/cost` with
  the transcripts THEMSELVES short of `/cost` (no compaction, dedup correct) is the
  separate interrupted/retried-turn gap — billed usage that never lands as complete
  assistant lines, which a transcript-folding scoreboard structurally can't recover.
- **Scoreboard well UNDER `/cost` (tens of %), transcripts clean** *(hidden
  summarizer agents, found 2026-07-08 — FIXED 2026-07-10 by the OTEL pipeline)* — this
  gap is now CLOSED: the OTLP receiver books hidden-agent spend as `query_source=auxiliary`
  in the `otel` table (verify: `python3 claude_audit.py otel <sid>` shows a non-trivial
  `auxiliary` cost), so a current telemetry-on session does NOT under-count. The shape
  below is the pre-OTEL diagnosis and still applies to a FALLBACK-only session (telemetry
  off → transcript fold, which structurally can't see these). Claude Code runs hidden agents that fire
  ONLY `SubagentStop`: no `SubagentStart` (so no substream, no `bump-agent`), no
  inner tool events, one stop each on a ~35s cadence while the session is busy, a
  one-line session summary as `last_assistant_message`, and an
  `agent_transcript_path` that was NEVER written (the `subagents/` dir mtime doesn't
  move). Their full-context billed reads reach `/cost` but no transcript any fold
  can see — a $53.85 session showed $39 (~$14 across 38 such agents). Tell: the
  `SubagentStop without SubagentStart` anomaly is non-empty, and those stops'
  `claude-subagent-fmt.py` decisions read `stop: never started (hidden agent) —
  spend no transcript` (pre-2026-07-08 builds misfiled them as `no-op (already
  finalised / duplicate stop)` — the old decision on a session with stop-only
  agent_ids is this shape, not a duplicate-stop storm). `spend reconciled` instead
  means the transcript DID exist and the spend was folded — no gap. This gap is
  structural (nothing on disk to fold); diagnose it, don't chase the fold.
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
  **Residual final-message tail** *(fixed 2026-07-10)* — the VERY LAST turn's Stop
  can read the transcript a beat BEFORE Claude Code flushes that turn's closing
  assistant line, so even with the Stop fold the final `bump-transcript` `txpos`
  lands one message short of EOF (seen: `7acc012d` scoreboard $3.64 vs `/cost` $3.86,
  the $0.055 tail one un-folded `claude-opus-4-8` reply). Now `claude-stop-fmt.py` is
  ALSO wired to `SessionEnd` (dispatch.py, ordered BEFORE the split-close/park step —
  no longer racing it), so the fully-flushed tail is folded before the state DB is
  parked. Tell on a current build: a `SessionEnd` with a `claude-stop-fmt.py` decision
  row whose `txpos` == EOF; its ABSENCE (SessionEnd subscriber row but no stop-fmt
  decision), or a `txpos` still short of EOF after it, is the regression. Note the
  hidden-summarizer gap (below) is a SEPARATE, larger, unrecoverable cause of the same
  symptom — rule it out via the `SubagentStop without SubagentStart` anomaly first.

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table),
(3) the code path responsible (file + mechanism), (4) a suggested fix. If the
evidence is inconclusive, say exactly which signal is missing and what extra
instrumentation would capture it next time.
