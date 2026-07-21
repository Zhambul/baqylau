---
name: audit-debug
description: Diagnose a kitty-mirror/tab-status bug from the always-on SQLite audit trail. Use when the user reports a bug in a session (stuck tab colour, missing/duplicated mirror block, frozen pane, wrong scoreboard numbers) and gives a session id — or asks to investigate "what happened in session X".
---

# audit-debug — root-cause a session bug from the audit DB

Every Claude Code session in this project is audited into SQLite (always on;
`CLAUDE_AUDIT=0` disables). Given a session id, reconstruct what happened and name
the bug **from evidence, not guesswork**.

## Where the data is

- DB: `$CLAUDE_AUDIT_DIR/audit.db`, default `~/.claude/baqylau-audit/audit.db` (WAL mode — safe to read while a session runs).
- Query tool: `python3 bin/claude-audit.py <cmd>` from the repo root (`/Users/z.yermagambet/code/personal/baqylau`), or raw `sqlite3` on the DB.
- If the user gives a truncated sid (first 8 chars), resolve it: `python3 bin/claude-audit.py sql "SELECT session_id FROM sessions WHERE session_id LIKE '<prefix>%'"`.
- `python3 bin/claude-audit.py sessions` lists recent sessions when no sid was given.

## Schema (all tables carry `session_id`; times are epoch seconds, local tz when displayed)

| table | one row per | key columns |
|---|---|---|
| `sessions` | Claude session | cwd, **start_cwd**, transcript_path, mirror_log, kitty_window_id, started_at/ended_at, end_reason, env (JSON of CLAUDE_MIRROR_*/KITTY_* seen at start). A SessionEnd that couldn't reach the DB spools a `session_end` pseudo-row (like `stream_end`), ingested later — a session still "(open)" long after it visibly ended now means the end never fired at all, not a locked DB. Since 2026-07-19 cwd/project_slug/transcript_path are REFRESHED on every event (`A.session_paths`, called by the dispatcher): Claude Code relocates the transcript when the session's cwd moves to another project dir (worktree entry), so the start-time values go stale mid-session — a change lands as a `session-paths` `state_files` row (old → new; agent_id events are skipped: an isolated subagent's payload carries the AGENT's worktree cwd). On a pre-fix session the row keeps the start-time path — a missing-on-disk transcript_path there is the relocation, not a deleted transcript (the real file is at the LATEST hook payload's `transcript_path`). **`start_cwd`** (added by `audit._migrate`) is the opposite: the FROZEN original cwd, set once at SessionStart and NEVER re-stamped (absent from `session_start`'s `ON CONFLICT` update, untouched by `session_paths`) — the dashboard groups on it (server `group_dir`) so a mid-session `cd` can't move a card between list groups. `start_cwd != cwd` on a session that relocated is EXPECTED, not a bug; a NULL `start_cwd` is a row that predates the migration (backfilled to `cwd` where possible) |
| `hook_events` | hook invocation | hook, tool_name, agent_id ('' = main session), handler (script), **decision** (what the handler chose to do), **payload** (full hook stdin JSON, verbatim). Since the single-dispatcher refactor, **all hook events are wired to one entry (`claude-hook.py` → `plugins/claude_code/dispatch.py`)** which fans out in-process; `handler` is NOT argv[0] (that would be `claude-hook.py` for everything) but an explicit per-subsystem override the dispatcher stamps (`audit.set_handler`), so the vocabulary below is unchanged (`claude-cmd-fmt.py`, `claude-tab-status.py` transitions, etc.). The universal subscriber row (handler = 'subscriber', empty decision) is now written **in-process by the dispatcher** at the end of `route()` rather than by a separate async settings entry — same row, same coverage. **New tell:** a `subscriber` row for an event that SHOULD have a functional handler row (e.g. a `PostToolUse`+`Bash` with a `subscriber` row but no `claude-cmd-fmt.py` decision row) = the dispatcher dropped/crashed that step — check `errors` for a `script='claude-cmd-fmt.py'` (or `script='dispatch'`) row. ALL hook events are recorded via the subscriber (handler = 'subscriber', empty decision) — incl. PermissionRequest/Denied, PostToolBatch, MessageDisplay, TeammateIdle, Pre/PostCompact, ConfigChange, CwdChanged, FileChanged, Elicitation\*, Setup, UserPromptExpansion, InstructionsLoaded — EXCEPT `WorktreeCreate`/`WorktreeRemove`, which are deliberately UNWIRED (since 2026-07-15): they are delegating hooks — registering one overrides Claude Code's native worktree creation and must print the worktree path, so the dispatcher's silent exit-0 failed every `EnterWorktree` ("hook succeeded but returned no worktree path"); a Worktree\* row in a pre-2026-07-15 session is the old (broken) wiring — on top of the mirror handlers' own decision-carrying rows for the events they process. So "did event X even fire?" is always answerable from the subscriber rows, and a handler row can be cross-checked against the subscriber's independent record. Since 2026-07-07 a `codex-session` handler also appears — the STANDALONE codex host's own SessionStart hook (`claude-codex-session.py`), the one `hook_events` row keyed to a *codex* session id rather than a Claude one; decisions: `standalone-open (<fate>, host_pid=N)`, `nested-skip (host mirror <sid> present)` (codex ran as a Claude subagent — that session's watcher already streams it), `no session_id`, `no usable frontend`. Since 2026-07-08, `claude-stop-fmt.py` also produces `stopfail: …` decisions (mirroring `claude-subagent-fmt.py`'s `stop: …` set — `done flag set, streamer will finalise` / `SAFETY NET footer …` / `no-op …` / `never started …`): a `StopFailure` carrying an `agent_id` is a subagent turn that DIED on an API error and fired no `SubagentStop`, so stop-fmt hands it to the shared subagent finaliser instead of ignoring it — the `stopfail:` prefix distinguishes this recovery from a normal `SubagentStop`. Since 2026-07-16 a `claude-file-fmt.py` `rendered: …` decision carries a `[scratch]`/`[out]` location tag when the file lies outside the session cwd (a session-scratchpad file / anywhere else) — it mirrors the painted display (`streamfmt.file_display`: ✎ icon / dim abbreviated dir); no tag = an in-project op, the unchanged bare-basename display. |
| `tab_transitions` | tab-colour decision | dispatch (raw arg: pretool/stop/bg-recheck/bg-watch/notify/escape-recheck/…), prev_state → new_state, applied (0 = skipped/bailed **or the kitten @ call failed** — reason then carries "kitten @ failed rc=N"), **reason**. Literal-state dispatches (SessionStart `idle`, SessionEnd `clear`) are sid-attributed since 2026-07; in older sessions those rows have `session_id=''`, so a per-sid query missed the final clear — the "tab left on a busy colour" anomaly can false-flag those old sessions. That anomaly's resting-state exclusion set also includes `awaiting-command` (red, the permission prompt — a session can legitimately sit on it, like green) since 2026-07-15 |
| `slots` | palette/liveness-slot event (rows of the session state DB's `live` table — were marker files) | kind (bg/monitor/fg/sub), slot_n, agent_id, owner_pid, action (claim/claim-id/**claim-pid**/steal-stale/**release-stale**/claim-denied/release/release-id/**release-pid**/set-owner), marker_path (now an opaque `<log>::live:<kind>.<key>` token). `steal-stale` is an ACQUISITION (the anomaly's claim/release pairing counts it as a claim since 2026-07-15); each steal is preceded by a synthesized **`release-stale`** row for the displaced DEAD holder (owner_pid = the dead pid), so a healthy steal balances — pre-2026-07-15 sessions lack release-stale rows and can flag "claims without a release" on every steal (historical, not a live bug). To see the CURRENT slot state: `sqlite3 /tmp/claude-mirror-<sid>.log.state.db "SELECT * FROM live"` |
| `streams` | detached tailer/streamer/watcher | kind (fg/bg/monitor/subagent/teammate/codex/codex-watcher/**bg-watch/interrupt-watch/relimit**), agent_id/task_id, src_path, pid, started_at/ended_at, **end_reason** (writer-gone/sentinel/stop-sentinel/stoppedByUser/**parent-task-resolved**/converted-ctrl-b/backstop-timeout/crash/state-moved-on/cleared-to-green/killed-or-crashed/state-db-parked/**parked-before-start (no state DB)**/…), lines_emitted. `parked-before-start (no state DB)` (codex-watcher) = the session parked before the watcher's first state-DB write (slow spawn vs fast SessionEnd); the watcher exits immediately without creating anything — healthy, not a hang. A `codex` end_reason may carry a `· malformed-lines:N` suffix — the run's count of complete-but-unparseable rollout lines (first one has a full `errors` row, func `codex rollout parse`; the rest are only counted — flood-capped by design). `parent-task-resolved` (subagent/teammate) = a REJECTED/abandoned Task recovered via the parent transcript's `tool_result` (no `SubagentStop`, no `stoppedByUser` ever fired) — the streamer keyed on the agent's `meta.json` `toolUseId`; `… (rejected)` when that result was `is_error`. NB an ASYNC (background) agent's Task resolves the parent `tool_result` IMMEDIATELY with a synthetic *"Async agent launched successfully"* ack (`is_error` absent) meaning launched-not-finished — `parent_tool_result()` ignores that ack (else the streamer ended ~2s in with `lines_emitted=0` and the agent's whole transcript never rendered; the `async launch-ack ended the substream early` anomaly flags a `parent-task-resolved`/0-lines stream whose agent later got a real `SubagentStop`). It pairs with a `SubagentStart without SubagentStop` (that anomaly still fires — Claude Code emitted no stop — but the stream properly ENDED, so it is the RECOVERED case, not a hang). A `fg` stream with `.subfg.<tid>.out` in `src_path` is a SUBAGENT's foreground command tailed live (spawned by `claude-substream.py`), not a main-session fg command. `output-file-not-found` on an `fg` stream = the command's output file (its own redirect target, or the tee file) never appeared before the command finished; since 2026-07-15 the fg tailer waits on command LIVENESS (the PostToolUse outcome hand-off, `wait_fg_src`) rather than the flat `FIND_S`/`CLAUDE_STREAM_FIND_S` (~12 s) deadline `bg` uses — so a late-created redirect target (`sleep 45; cmd > out`, a retry loop) no longer flips the tab off blue mid-command. A pre-fix (or regressed) `fg` `output-file-not-found` whose command's `PostToolUse` fired AFTER the stream ended is the bug (the `fg tailer gave up on a late redirect target` anomaly); a genuinely fileless command ends after its Post. An open row from a dead pid = the watcher/tailer died — for bg-watch that IS the stuck-blue bug. A `codex-watcher` whose `src_path` starts `standalone:` is a STANDALONE codex host manager (spawned with a `HOST_PID`): it streams only its own session's rollout and owns teardown when the codex process dies (the codex analogue of SessionEnd — see the standalone shape below). Since the OTEL cost pipeline, a `kind='otlp'` row is the GLOBAL (per-machine, not per-session) OTLP metrics receiver — `session_id='otlp-receiver'`, `src_path='127.0.0.1:<port>'`; it outlives individual sessions and idle-exits, so an OPEN otlp row while it runs is NORMAL (like a live codex-watcher), and a `duplicate (…)` end_reason is a second receiver that correctly lost the singleton guard, not a bug. A `kind='dashboard'` row (session_id `''`, `src_path='http://127.0.0.1:<port>'`) is the web-dashboard server (docs/dashboard.md) — also global and long-lived, so an OPEN row while it serves is NORMAL; `end_reason` `stopped` = clean CLI stop/SIGTERM, `port-busy` = the second-guard bind failed (paired `errors` row carries the port), `crash` = the serve loop died (traceback in `errors`); "dashboard not reachable / toasts stopped" with NO open dashboard row = the server isn't running (`bin/claude-dashboard.py status`), and request-level failures audit as `errors` rows with func `dashboard request` (the path is in context) or `dashboard notifier` (the toast watcher's poll — its failure backs off 5s, it never spin-audits) |
| `ops` | paint op written to the mirror log | producer (script), op (the JSON paint op — full pane reconstruction, survives SessionEnd; a `src` field inside the JSON = producer-source stamp `sub:<agent_id>`/`team:<agent_id>`/`codex:<label>` — absent means the main session's own op; the web dashboard drops stamped ops, the terminal paints all, so "block on terminal but missing on web" is answered by this field) |
| `errors` | swallowed exception | script, func, **traceback** (full), context (JSON of args in hand) |
| `spawns` | detached process launch | parent_script, child_pid, argv, purpose. Since 2026-07-15 the tab-status recovery watchers spawn through this too: purpose `watcher:bg-watch` / `watcher:interrupt-watch`; a FAILED watcher spawn is an `errors` row func `spawn claude-tab-status.py` — no spawn row AND no such error row = the watcher was genuinely never requested (before this, a failed spawn was indistinguishable from never-requested) |
| `state_files` | coordination-file transition | path, action (write/remove/remove-stale/**copy/bump/bump-agent/bump-transcript/msg-transitions/resume/final/reconcile/keep-history/restore-history/reuse-live-db/fresh-db/web-send/web-command/web-command-confirm/web-rename/web-stop/web-interrupt/web-rewind/web-rewind-to/web-answer/ask-pending/ask-draft/composer-draft/composer-queue/web-plan/plan-pending/tasks/memory/web-launch/web-launch-wake/web-launch-steal-watch/web-upload/ns-prefs/web-dictate/session-paths/limit-hit/relimit-launch/web-migrate/notify-mute/telegram-notify**), content (state-DB records — path is a `state:` key: `state:fg-live`, `state:done:<token>`, `state:subfg:<tid>` (subagent live-fg tee hand-off: `write` by cmd-pre, `remove` when the substream consumes it), `state:agent.<id>`, and **proc-found** (path `monitor:<taskid>`, content the pid) = the monitor tailer latched its command process — the moment completion detection is keyed to a real pid, and **open** (path `tail:<taskid>`, content path + `pos0`) = a skip-existing tailer (Ctrl+B hand-off / `>>` append) adopted its start offset — for a "Ctrl+B block missing its first lines" report, compare `pos0` against the launcher-measured CLAUDE_STREAM_POS0 expectation (a pos0 larger than the hand-off-moment size = the old open-time measurement regressed); for bump\* actions: the scoreboard deltas + resulting totals — the trail for wrong-scoreboard-number bugs). **bump-otel** (path = the state DB file) = the OTLP receiver's aggregated per-POST write: content carries the summed `deltas` (`tk_*`/`cost`/`tokens`/`otel_cost:<query_source>`) + resulting `now` totals. This is the PRIMARY cost producer now (the raw datapoints behind it are in the `otel` table). **drop-otel-parked** (path = the state DB file) = a straggler OTLP export arrived for a session that had already PARKED: the receiver drops the deltas (never connects — a connect would recreate the DB whose existence is the session-alive signal) and this row carries the dropped `deltas` + raw datapoints verbatim (they are deliberately NOT written to the `otel` table, so `SUM(otel.value)` keeps equalling the live counters). **drop-otel-noconn** (path = the state DB file, since 2026-07-15) = the same audited drop for a connect FAILURE past the parked check (locked/perms/corrupt live DB): the row carries the dropped `deltas` + raw datapoints, nothing reaches the `otel` table — this drop was previously fully invisible (the SUM(otel)==counters invariant still held, so no anomaly could see it). **evict-parked** (path = the state DB file) = the receiver's per-batch/per-tick sweep closed its cached state-DB connection for a session that parked (`state.evict` — without it every ended session pinned a conn + WAL/SHM fds until the receiver's idle exit). **bump-agent** is now ONLY codex (its separate process can't export OTEL, so it keeps its own rollout fold); a Claude subagent no longer bump-agents (OTEL's `query_source=subagent` books it). **bump-agent** = an agent streamer's spend bump, `meta` carries agent_id/kind/model + the in/out/cache/create split that was priced (since 2026-07-08 also `create_1h`, the 1-hour-TTL cache-write share — it bills 2× input where 5m bills 1.25×, so re-pricing needs it) — attribution and re-pricing need no timestamp correlation; `meta.kind` is `subagent`/`teammate` (priced by `accounting.cost_usd`) or, since 2026-07-07, `codex` (a rollout run's cumulative `token_count` fold, priced by the codex plugin's own `CODEX_PRICES`; `meta.src` is the rollout path); **reconcile** (path = `state:agent.<id>`) = `claude-subagent-fmt.py` at SubagentStop folded the agent's transcript and recorded the residual over the `billed:<agent>` baseline. Since the OTEL pipeline it NO LONGER bumps counters (OTEL's `query_source=subagent` books agent spend live, including a crashed streamer's tail) — the row is now a pure OTEL-vs-transcript CROSS-CHECK (content: `residual`, `cost`, transcript `true` total). Idempotent — a clean finish leaves `true` == baseline, so no row. **bump-transcript** (the transcript fold) is now a FALLBACK ONLY — it fires from `claude-stop-fmt.py` on `SessionEnd` and ONLY when the OTLP receiver wrote nothing for the session (`otel_seen==0`: telemetry off / receiver down / machine without the env). In the normal path there are NO bump-transcript rows at all (OTEL owns cost); a bump-transcript row means the session ran without telemetry and the fold recovered it. It carries `d_split` (`tk_in`/`tk_out`/`tk_read`/`tk_create`) and `d_create_1h` alongside `d_tokens`/`d_cost`. A bump-transcript row AND bump-otel rows for the SAME session = the `otel_seen` gate broke (double-count regression — its own anomaly). The per-category counters live in the state DB (`SELECT key,val FROM counters WHERE key LIKE 'tk_%'`); `tk_in+tk_create+tk_out` == the billed `tokens` counter (which backs `cost`; no longer shown on the `▪` row), and `+tk_read` is the Σ total. Scorebar `paused`-only ticks are NOT audited (1/s noise; the total rides every other bump's `now`). **errseen** (path = the state DB file) = the audit WARNING LIGHT (`core/errwatch.py`, polled by the scorebar every 5s) advanced its last-seen `errors`-rowid checkpoint after emitting `⚠ audit:` mirror one-liners; content carries `last` (the rowid consumed up to) and `new` (how many rows that poll emitted — >3 were flood-collapsed into one CLI-pointer line). Since 2026-07-15 the light also surfaces GLOBAL `errors` rows (`session_id=''` — auditor-outage rows from `audit._connect`, pre-session/CLI errors): the chip count includes them, each is emitted as a `⚠ audit: global: <script>: …` one-liner (flood pointer targets `errors ''`), and their checkpoint is a SEPARATE per-session kv (`errseen-global`) whose advances land as `errseen` rows with `"global": true` in the content. Every session's scorebar shows the same global rows (an audit outage affects all sessions) — that's by design, not a duplication bug. Which errors ever reached the mirror, and whether one was shown twice or never, is reconstructible from these rows against the `errors` rowids. **resume/final** (path = `state:agent.<id>`) bracket each substream streamer: what checkpoint + dedup state it adopted (or `fresh: <why>`) and what it left behind — a successor's `resume` disagreeing with its predecessor's `final` is a broken handoff. **adopt** (path = the NEW sid's state DB file, since 2026-07-11) = sid-fork adoption (`plugins/claude_code/adopt.py`): a `--resume` whose SessionStart fired under the OLD sid — or a BACKGROUNDED session continuing under its background-job id — while every later event carries a NEW sid; the fork's first event moves the predecessor's state DB to the new sid's path (hardlink + atomic symlink swap since 2026-07-14 — the old path is never absent mid-move; symlinks left at the old paths) and retags the panes; content carries `from` (the old sid), `moved` (which of db/-wal/-shm moved) and `retagged` (which pane vars were re-pointed). It pairs with a `hook_events` decision row, handler `claude-hook.py`, decision `adopt: sid forked — adopted <old>` — the ONE functional decision that handler name carries (adoption is dispatcher plumbing, not a subsystem). The registry behind it (`sids` = sids whose OWN start was seen — marked on `SessionStart` AND the earlier-firing `InstructionsLoaded`, which a fork never emits, closing a TOCTOU where a new session's pre-SessionStart event adopted a concurrent same-cwd session; `adopt_pending` = the take-once cwd-keyed note every HOSTED SessionStart leaves (split.cmd_open)) lives in the global tab DB `/tmp/claude-kitty-tab.db`. **session-paths** (path = the NEW transcript path, since 2026-07-19) = the dispatcher's per-event `A.session_paths` refresh caught the sessions row's location columns going stale and folded the payload's values in: Claude Code RELOCATES the transcript when the session's cwd moves to another project dir (measured 2026-07-18 via EnterWorktree — the file moves to the worktree cwd's `projects/` slug dir); content carries `cwd`/`transcript_path` (new) + `cwd_old`/`transcript_path_old`, so every relocation moment is a visible row, not a silent UPDATE. agent_id events never write one (an isolated subagent's cwd is its OWN worktree — skipped by design), and an unchanged payload writes nothing, so a busy session has at most a handful. A session that entered a worktree with NO session-paths row = the refresh regressed (its anomaly: *"sessions row transcript_path stale vs latest hook payload"*). An `adopt` decision on a sid that ALSO has its own `SessionStart` is a MIS-adoption (a real independent session stole a same-cwd predecessor's panes) — its own anomaly, *"adopted a predecessor despite having its OWN SessionStart (mis-adoption — pane theft)"*. **keep-history/restore-history/reuse-live-db/fresh-db/park-failed (kept live)/restore-failed (park kept)** (path = the DURABLE park `~/.claude/baqylau-mirror-history/<sid>.state.db` since 2026-07-14 — `core/paths.parked_db`; older rows carry the in-place `<log>.state.db.keep`; content = the SessionStart `source`) trace the session state DB's lifecycle: SessionEnd MOVES the live `/tmp` DB out to that durable park (`keep-history`); SessionStart either restores it back to the live path (`restore-history`, resume of the same sid — honours a legacy in-place `.keep` too), leaves a live DB alone (`reuse-live-db`, compact or resume-after-crash), or starts fresh (`fresh-db`). The park is under `~/.claude`, NOT `/tmp`, precisely so a machine reboot (macOS wipes `/tmp`) between SessionEnd and a `--resume` can't drop the history and force a `fresh-db`. The state DB IS the mirror content (its `ops` table) — so these rows are the resume-history trail. Since 2026-07-15 the park FAILURE paths are audited instead of swallowed: **park-failed (kept live)** = the MAIN DB move failed at SessionEnd (paired `errors` row func `park_db (main move — DB kept live)`) — the live DB path persists, so the scorebar/codex-watcher pollers keep running as orphans and a same-sid resume sees `reuse-live-db`; **restore-failed (park kept)** = the resume's main move-back failed (`errors` func `decide_log_fate (restore move main)`) — the park stays intact, the session starts fresh. A sidecar-only park failure still logs `keep-history` but leaves an `errors` row func `park_db (sidecar move -wal/-shm)` (safe: park_db checkpoints the WAL — `wal_checkpoint(TRUNCATE)` — before moving, so the parked main file is self-contained). **copy** (path = the state DB file) = a ⧉ copy-link click handled by `claude-copy.py` — content carries `gid` (the block's copy-group id: the Bash tool_use_id or the backgroundTaskId), `what` (`cmd`/`out`) and `chars` (0 = the group held nothing of that type); every FAILED click lands in `errors` instead, func `copy (bad url)` / `copy (state DB gone — session over?)` / `copy (read ops)` / `copy (no clipboard tool)`. **web-send** (path = the session's state DB file) = a dashboard CONTROL-PLANE message POST (`dashboard/server.py` `post_message`): content carries `win` (the kitty window typed into, `""` = headless → the row records a rejected attempt), `chars` (message length) and `ok` (did the send succeed — send_text is TWO kitten writes, the message then a gap-separated CR (`SEND_ENTER_GAP_S`, the split-Enter fix), and `ok` requires both), plus (since 2026-07-18) `clear_draft` — TRUE when the page resent an EDITED message after a mid-turn cancel-edit: the row's send first kills the restored draft (ctrl+u+ctrl+k) and delivers via `Frontend.paste_text` (a BRACKETED paste, not send_text — a raw send into the just-cleared input drops leading bytes, the measured mangle; the bracketed paste lands clean). A `web-send clear_draft:true` is the edit-and-resend-from-web path; garbled resends on an OLD build (no bracketed paste) are its regression. A failed/rejected attempt also lands an `errors` row (funcs `dashboard message (no terminal|send failed)`). **web-command** (path = the session's state DB file, since 2026-07-18) = a dashboard quick-command POST (`post_command` — the scoreboard's second action row: ⊜ compact / ✦ model / ⚡ effort): types the TUI's OWN slash command (`/compact`, `/model <arg>`, `/effort <arg>`) via the same bracketed paste+CR as a composer send; content carries `win`, `cmd`, `arg`, `ok` and `tab` (the tab state at send time — ∈ thinking/working/executing means the command QUEUED in the TUI and runs at the turn boundary; `tab: awaiting-command` with `ok: false` = refused because a modal dialog was up and pasted text would land IN the dialog). Off-vocabulary requests never paste — they land ONLY as an `errors` row func `dashboard command (bad cmd)` with the exact received bytes (repr); other failures pair funcs `dashboard command (no terminal|send failed)`. "I clicked model/effort/compact and nothing changed": `tab` in the row answers it — queued mid-turn (wait for the boundary), or check the transcript for the command's own record after an `ok: true` on an idle tab. **web-rename** (path = the session's state DB file, since 2026-07-18) = a dashboard rename POST (`post_rename` — the ✎ header button): appends the `agent-name` naming record to the session's transcript (`plugins/claude_code/transcript.set_session_title` — the `/rename` channel; the ONE control-plane session-state write) and retitles a live tab via `Frontend.set_tab_title` (STICKY — that tab stops following auto ai-titles for the rest of the session: expected, not a bug). content: `win` (`""` = parked/terminal-less — deliberately NOT an error, the append still ran), `chars` (name length), `ok` (did the append land), `tab` (tab state at rename time — renames append even mid-turn, this field is the race trail), `tab_retitled` (the kitten set-tab-title rc; `false` with `ok: true` = picker/dashboard renamed but the live tab kept its old title — no window, no terminal, or a name the kitten CLI ate as a flag), `reason` on refusals (`no transcript` = no/missing transcript path recorded; `unsupported` = not a Claude `projects/` transcript, e.g. a codex standalone host's rollout — never append a Claude record there). An append failure pairs an `errors` row func `dashboard rename (append failed)`. **web-launch** (log/path empty — no session exists yet) = a dashboard new-session POST (`post_new_session`): content carries `cwd` and `ok` (did `Frontend.launch_tab` succeed); the launched session shows up later via its own SessionStart, so there is no adopt/fork relationship to this row. **web-launch-steal-watch** (log/path empty, since 2026-07-18) = the PASSIVE macOS focus watch that follows every web launch whose frontend has an OS app id (`Frontend.app_id()`) and whose capture-time frontmost app wasn't the terminal: a ~30s daemon-thread watch (`dashboard/server.py _steal_watch`) over the frontmost app (`lsappinfo`) that RECORDS each transition onto the terminal app and never touches focus; content carries `before` (the browser's bundle id at click time), `terminal`, and `steals` (seconds-into-watch of each takeover; `[]` = clean). The steal's root cause was fixed at the source the same day: the SessionStart pane opens passed kitty's `--keep-focus`, whose focus-restore raises the OS window whenever the app is in the background — `frontends/kitty.py launch_pane` now passes the flag only while kitty is frontmost (`kitten_app_focused`), so a non-empty `steals` on a current build = some launch path still activates the terminal, and the offsets name the second (compare against the startup sequence: tab launch ≈0s, mirror/scorebar opens ≈2-6s after claude boots). Historical: rows with action **web-launch-refocus** (2026-07-18 only) are from the reverted ACTIVE bounce-back variant, which `open -b`'d the browser back on every takeover — reverted because it cannot distinguish kitty stealing focus from the user deliberately switching to kitty, and yanked the user back; do not re-add it. "kitty jumps to the front when I start a session from the dashboard" → this row: missing entirely = the guard never armed (no app_id / terminal already frontmost / pre-fix server), `clean` while the user SAW the steal = the steal came slower than the watch window or from something else entirely, `bounced xREFOCUS_MAX` = the cap was hit (something kept stealing past the pane opens — worth a look at what), `activate failed` = the hand-back itself broke. NB kitty's `--keep-focus` launch flag is deliberately NOT used — on a background kitty it *causes* the steal (verified 2026-07-18; docs/dashboard.md *The focus-bounce guard*). **web-stop** (path = the session's state DB file) = a dashboard stop POST (`post_stop` — the CLOSE button): content carries `win` (the tab-owning window closed via `Frontend.close_tab`, `""` = refused: headless or the live `claude_session` tag was gone) and `ok`; a graceful close — Claude Code exits on the HUP and SessionEnd runs the normal lifecycle, so a `web-stop ok: true` should be followed by that session's park/`sessions.ended_at` rows (missing = the HUP didn't reach the TUI). **web-interrupt** (path = the session's state DB file) = a dashboard interrupt POST (`post_interrupt` — the STOP button / the page's Esc key): an Escape key EVENT via `Frontend.send_key` (send-text bytes would bypass the kitty keyboard protocol); content carries `win`, `ok` and `tab` (the tab state at press time — what the Escape landed on; note kitty's send-key reports no per-window delivery errors, so `ok: true` only says kitty accepted the call). The session must stay up: a `web-interrupt` followed by the session ENDING is the tell that Esc landed somewhere unintended. **web-rewind** (path = the session's state DB file) = a dashboard rewind POST (`post_rewind` — the ↶ button / a rapid double Esc on the page), whose `content.mode` mirrors Claude Code's state-dependent double-Esc: `rewind` (IDLE tab) TYPES `/rewind` via send_text (documented identical to double-Esc; synthesized double-press key events measured only ~2/3 reliable at any gap, typed command 100%) — NO Escape, so NO escape-recheck spawn; `cancel-edit` (a BUSY tab — thinking/working/executing/awaiting-bg/awaiting-command) sends TWO Escape key events (cancels the turn + restores the last message for editing, measured 3/3 mid-turn) and, on magenta, DOES spawn escape-recheck. So a `web-rewind` with `mode:rewind` + an escape-recheck spawn is a regression, but `mode:cancel-edit` on magenta SHOULD have one. content carries `win`, `ok`, `tab`, `mode`. NB the escape-recheck now bails only on a new `"type":"user"` transcript record (not raw growth) — the cancel-edit's trailing `ai-title`/`last-prompt` metadata used to false-bail it, leaving the tab stuck magenta (its tell: an escape-recheck `state_files`/tab_transitions bail reason mentioning transcript growth with no user prompt actually submitted). **web-rewind-to** (path = the session's state DB file, since 2026-07-18) = the FULL web rewind (`post_rewind_to` — a prompt bubble's ↶ / picking mode): the server drives Claude Code's own checkpoint menu in the session's window (`dashboard/rewindmenu.py` — typed `/rewind`, screen-verified `up` navigation to the target prompt's menu entry, restore option picked by parsed LABEL since the numbering shifts with content, digit key selects). content on success: `win`, `ok: true`, `tab`, `mode` (conversation/both/code), `ups` (the page's jump hint), `steps` (extra scan presses the text-verify needed — a big value = the page's view was stale, e.g. a kitty-side rewind it never saw), `digit` (which option number was pressed), `degraded` (true = a `both` request at a no-code-change checkpoint auto-degraded to the conversation restore — the code options were absent because the code was already in the target state, verified against the confirm screen's "The code will be unchanged." line; since 2026-07-18). On a bail: `ok: false` + `step` naming the failed stage (`busy` = refused outright on a busy tab; `open`/`find`/`confirm`/`option`/`close` = the menu step that never verified — each also pairs an `errors` row func `dashboard rewind-to (<step>)`, and the driver Escape-closes the menus before returning, so a session left SITTING in an open rewind menu after one of these rows is its own bug). A rewind restores conversation state ONLY inside the live TUI — it writes NOTHING to the transcript until the next send forks it (a user record whose parentUuid points back at the fork point), so "the dashboard rewound but the transcript still shows the turns" is EXPECTED, not a bug; the fork record arriving later is the on-disk confirmation the rewind really happened. **ask-pending** (path = the state DB file, since 2026-07-18) = the AskUserQuestion pending-state stash behind the web ask card (`plugins/claude_code/ask_fmt.py`, entry `claude-ask-fmt.py`): action content `{action: "write", tool_use_id, questions: N}` on PreToolUse(AskUserQuestion), `{action: "remove", reason}` on the clears — reason `answered`/`failed` (the tool's PostToolUse/Failure), `turn ended` (Stop/StopFailure) or `new prompt` (UserPromptSubmit). The turn-boundary clears exist because EVERY decline path (Esc, "Chat about this", empty-"Type something" Enter) fires NO closing hook at all (measured 2026-07-18). Each write/remove pairs a `hook_events` decision row under handler `claude-ask-fmt.py`. A `write` with no eventual `remove` while the session keeps taking turns = the clear routing broke (the card would sit stale on the page — though /answer still screen-verifies, so it can only 409, never mis-answer). No rows at all for a session that definitely asked = the session is UNHOSTED (no state DB — deliberate: the stash never creates the DB whose existence is the session-alive signal) or the ask came from a subagent (agent_id — ignored by design). **web-answer** (path = the state DB file, since 2026-07-18) = a dashboard ask-card POST (`post_answer` → `dashboard/askdialog.drive`, which drives the REAL TUI dialog with screen-verified keys): content `{win, ok, chat, tool_use_id}`, failures `{…, ok: false, step}` where step names the unverified stage (`open` = no dialog on screen — answered/declined in the terminal first; `question`/`cursor`/`options`/`type`/`review`/`submit`/`chat` = a dialog step; each pairs an `errors` row func `dashboard answer (<step>)`). The driver NEVER presses Escape on a bail (Escape would DECLINE the questions — opposite of rewindmenu's bail), so after a failed row the dialog is still open and re-answerable. An `ok: true` should be followed by the ask's PostToolUse hook_events row + the `ask-pending` remove (reason answered) — missing = the dialog submitted something other than what the driver thought (screen-model drift; compare the PostToolUse payload's `answers` against the intent). **plan-pending** (path = the state DB file, since 2026-07-18) = the ExitPlanMode half of the same modal-dialog tracker (ask_fmt handles BOTH tools): `{action: "write", tool_use_id, what: "N-char plan"}` on PreToolUse (tool_input carries the plan markdown + planFilePath), removes with the same reason vocabulary as ask-pending plus `web open-bail` (the dashboard's self-heal: an /answer, /plan-options or /plan-decision found NO dialog on screen while the stash lingered — resolved in the terminal before the turn-boundary clear fired — and dropped the stash via `state.kv_del_at`, the fresh-connection explicit-path delete that exists because kv_del's cached conn is thread-bound and silently no-ops on a dashboard handler thread). Clears are TOOL-SCOPED: the plan's PostToolUse never drops a co-pending ask stash and vice versa; turn boundaries drop both. **web-plan** (path = the state DB file, since 2026-07-18) = a dashboard plan-card POST (`post_plan_decision` → `dashboard/plandialog`): content `{win, ok, kind: decide|feedback|dismiss, label, tool_use_id}`, failures `{…, ok: false, step}` + an `errors` row func `dashboard plan (<step>)`. `decide` presses a decision digit ONLY after the screen still shows the requested label on it (labels vary with the session's permission mode and are fetched live via /plan-options — never hardcoded); `feedback` types into the "Tell Claude what to change" row (digit focuses, text inline, Enter submits the rejection-with-feedback); `dismiss` is the dialog's own Esc reject. Like web-answer, a bail leaves the dialog OPEN (an Escape bail would REJECT the plan). An `ok: true` decide should pair the tool's PostToolUse + the `plan-pending` remove (reason answered); feedback/dismiss pair NO hook (declines are hookless) — their stash clears at the next boundary or the revision's overwrite. **memory** (path = the state DB file, since 2026-07-21) = a file op under the memory wiki (`~/wiki/01`, `plugins/claude_code/memory.py`) was snapshotted into the `memory` kv the dashboard's Memory tab reads (docs/dashboard.md *Memory tab*): content `{action: "write", verb (Read/Update/Write), path, agent (subagent name or "main"), notes (distinct-note count so far)}`. Written by BOTH `claude-file-fmt.py` (main agent) and `claude-substream.py` (a subagent — team-wide, unlike the main-agent-only mirror), so a note the note-writer touched still shows. No rows for a session that clearly edited the wiki = the session is UNHOSTED (no state DB — `record` is `parked`-guarded, never creates the DB) OR the path wasn't under the hardcoded root (a vault at a different location than `~/wiki/01`, or the `BAQYLAU_MEMORY_ROOT` test seam not set); a memory op that painted its 🧠 marker in the mirror but left no row is the `record` write failing (paired `errors` func `memory.record`). **view-stash** (path = the state DB file) = a file-op producer (`claude-file-fmt.py`, or `claude-substream.py` for a subagent — then content also carries `agent`) pre-rendered a Read/Update/Write's click-to-view block into the kv row `view:<tool_use_id>`; content: `gid`/`tool`/`ops` count. **view** (path = the state DB file) = a click on a file-op line's `/view` hyperlink: `claude-copy.py` toggled the gid in the `view-open` kv set (content: `gid` + `open` true/false; `open: null` = no stash existed, feedback no-op) and SIGWINCH-nudged the renderer via the `renderer-pid` kv row; failures land in `errors` funcs `view (…)` / `view-stash (…)` / `viewport_anchor (…)` / `toggle_scroll (view toggle)`. **view-reflow** (path = the state DB file) = the renderer processed that toggle: content carries `gid`, `idx` (the clicked line's offset; null = op not in the render window), `anchor` (the recovered viewport-top offset — a GLOBAL text match since 2026-07-12, `locate_viewport`, with the capture retried 3× under load and TWIN DISAMBIGUATION: near-best matches are tie-broken toward the caller's prior — the clicked line for the anchor, the restore target for the verify, the previous sample for the drift watch — because a buffer full of repeated content matched at multiple offsets and the restore teleported to the wrong copy while the verify confirmed that same wrong copy: an audit-PERFECT row for a real user-visible jump, THE root cause of the "hide jumps to a random location" reports; impossible there-and-back drift bounces (4808→1270→4880 in 400ms) are the misread signature; null = capture/match failed → fell back to clicked-line-at-top AND left an `errors` row func `viewport_anchor (no window|no capture|empty capture|no match)` — no-match carries cap/rows/best/score detail; a null with NO paired errors row = pre-fix renderer), `cap0` (the first line of the pre-toggle capture — what the user actually saw), `up` (the restore scroll amount; the restore is ABSOLUTE — scroll-to-end then up, so `up` counts from the bottom, whose frame top is `total+1-h`, the +1 being the cursor row), `applied`, `dsr` (did kitty's cursor-report handshake confirm the frame was parsed before the scroll — false = the scroll may have raced the parse), `landed` (where the viewport VERIFIABLY ended — the same global text-match as the anchor; the ground truth), `retried` (a landed≠target miss was CONVERGED onto the target — up to 3 passes, each scrolling by the measured error, never the same absolute amount re-run, because kitty scrolls VISUAL lines while the row math counts logical rows and wrapped rows make the same restore reproduce the same miss; a first miss >400 rows = momentum raced the restore itself → the absolute restore is redone once, then delta passes; "in place" means ZERO rows off — a 17-row near-miss reads as a lost scroll position; a PERSISTENT landed≠anchor with `up` ≈ scrollback_lines = the restore clamped at the scrollback ceiling — the frame outgrew the buffer, see ROW_BUDGET) and `follow` (the pre-toggle viewport was AT the bottom, so the restore targeted the NEW bottom to keep tail-following instead of pinning) — THE row for any "the view jumped on expand" report: `anchor: null` on a visible-line click is the tell (get-text broken or rows drifted from the painted text), and a `view` row with NO `view-reflow` row means the renderer never processed the toggle (dead/stale renderer — check `renderer-pid`). **view-drift** (path = the state DB file) = the post-toggle DRIFT WATCH caught the viewport moving: for 8s after every toggle the renderer re-locates the viewport each 200ms tick and records every change (`from`/`to` offsets + `left_ms` watch time remaining + `corrected`) — the evidence for "the toggle verified its landing but the pane ended up somewhere else moments later": a user wheel-scroll shows as gradual steps, a bug as one instant leap (e.g. `to` ≈ 0 = something scrolled to buffer start; observed live: a verified landing yanked 969 rows within one tick — only on real mouse clicks, never on socket-driven sim toggles). `corrected: true` = the SETTLE GUARD fired: for ~700ms after a landing (sampled at ~80ms) the position belongs to the toggle's INTENDED anchor (`home` on the reflow row — never the measured landing, which in-flight momentum can corrupt; observed adopted 1176 off) — a displacement >5 rows in that window is the user's RESIDUAL TRACKPAD MOMENTUM (they flick-scrolled to the line, clicked, and the leftover momentum applied on top of the fresh restore — the root cause of every "hide jumped ~1000 rows" trace: huge displacement within 1-2 ticks, decaying step series, never reproducible without a human hand, kitty itself verified exact 12/12 in a sterile window) and is snapped back by an ABSOLUTE restore (recomputed against current content — a relative fix against a still-moving target amplifies), max 2 per toggle. Deliberate post-click navigation (observed starting at +1100ms) is outside the window and never fought. No view-drift rows after a toggle = the pane genuinely stayed where `landed` says. **paint** (path = the state DB file) = one row per full-reflow decision the renderer made: `kind` (`repaint`/`toggle`/`skip` — `skip` = a WINCH at an UNCHANGED size with no toggle plan, deliberately painted nothing: a repaint there clamps a scrolled-up viewport to the bottom), `w` (width), `rows` (newlines actually written; capped by `ROW_BUDGET`, default 4800 / env `CLAUDE_MIRROR_SCROLLBACK` — the ops list is trimmed so the frame fits kitty's scrollback, because rows beyond it are unreachable after any reflow), `ops`, `open` (expanded view blocks) — the ground truth against the toggle math: a `view-reflow` whose `up` disagrees with the painted `rows` is a model-vs-buffer divergence. **render:\<taskid\>** (path) = a `claude-stream.py` content-rendering stream — markdown (`cat`/`head`/`tail` of a `.md`, `CLAUDE_MIRROR_MD`), JSON (`cat` of a `.json`, `CLAUDE_MIRROR_JSON`), YAML (`.yml`/`.yaml`, `CLAUDE_MIRROR_YAML`) source code (`.py`/`.java`/`.kt`/`.sh` etc, `CLAUDE_MIRROR_CODE` — `kind` is `code:<lexer>`), or a fg stream whose OUTPUT was sniffed to contain a fenced code block (no filename hint, `CLAUDE_MIRROR_MD_SNIFF` — `kind` is `md-sniff`). ALL filename-keyed detection runs in the tailer itself, from the raw command every launch site passes via `CLAUDE_STREAM_CMD` (`hookkit.stream_env`) — so it covers a SUBAGENT's live-fg command too (the substream-spawned tailer), and these `render:` rows are the ONE render-decision evidence (no launcher decision suffix): action `start` (content `kind`, + `wenmode` = was the md parser importable, else it degraded to the `render.markdown()` subset) and action `done` (content `kind` + `blocks` = how many rendered gut ops it emitted; JSON/YAML/code are 1). `blocks: 0` from a stream that ran = a render failure (its own anomaly, below). Only markdown fenced code blocks render as a full-width panel — an `ops` gut row with a `bg` field; JSON/YAML/code colour on the normal gutter (no `bg`). **composer-draft** (path = the session's state DB file, since 2026-07-19) = the web composer's UNSENT-message draft (`dashboard/server.py post_composer_draft`): content `{action: "write", chars, seq, origin}` on each debounced edit, `{action: "clear", chars:0, seq, origin}` on send OR an emptied box (an empty-text TOMBSTONE, not a delete — its `seq` must survive to reject a straggler), and `{action: "stale", seq, have, origin}` when a write was DROPPED for arriving older than the stored `seq` (the 2026-07-19 clear-vs-save race guard: a debounced save landing after the send's clear would resurrect the just-sent draft; `seq` is the page's `Date.now()` at dispatch, and the clear always carries a later one). A run of `stale` rows around a send = the tunnel reordered writes and the guard did its job. Unlike `ask-draft` it has NO plugin-side lifecycle — a message draft has no turn boundary, so it lives until sent/overwritten and the dashboard fully owns both write and clear; the SSE `composer-draft` event re-broadcasts it with `origin`-echo suppression. Persists for LIVE and PARKED sessions (`state_db_for` resolves the parked copy). A `write` with no eventual `clear` is normal (the draft is meant to survive); "my draft vanished" = no `write` row (the POST never landed) or a stray `clear`; "the draft did NOT clear after I sent" = a `stale` row swallowed the clear's winner (the guard mis-ordered — inspect the `seq`/`have`) OR the clear's POST never landed (no `clear` row). **composer-queue** (path = the session's state DB file, since 2026-07-19) = the web composer's PENDING queued-message chips (`post_composer_queue`, the ⧗ list for mid-turn messages the TUI queued but hasn't delivered): content `{action: "write", n, origin}` (the whole chip list re-persisted on every mutation — a queued send, a delivery drain, a ✕-hide) or `{action: "remove", origin}` when the list empties. Display-persistence only (a reload used to lose the chips though the message stayed in the TUI's queue — the "gone even from the queue after refresh" report); the message itself is NOT here. SSE `composer-queue` re-broadcasts with `origin`-echo suppression. RELATED: a `web-send` with `blocked: modal` + `ok: false` = a composer send REFUSED because an ask/plan dialog was up (it would paste INTO the dialog and be lost); a `web-send` with `via: ask-chat` = the message the ask card delivered after routing a typed preview-question answer through "chat about this". **ns-prefs** (log/path EMPTY — GLOBAL, no session, since 2026-07-19) = the new-session form remembered its last-used `{cwd, model, effort}` in the durable global prefs DB (`dashboard/prefs.py`, `~/.claude/baqylau-dash-prefs.db`, `POST /api/ns-prefs`): content the stored record + `action: write`. Moved off per-browser `localStorage` so the value is cross-device; model/effort are re-validated against the launch allowlists (a bad value is DROPPED, never stored — so a corrupt pref can't feed the launch path). No session relationship — it only pre-selects the NEXT launch's form. **notify-mute** (log/path EMPTY — GLOBAL prefs, since 2026-07-20) = a dashboard `POST /api/session/<sid>/notify` opted a session in/out of the deferred Telegram alert (docs/dashboard.md *Telegram alerts*): content `{sid, muted}`, stored in the `notify-muted` kv map of the global prefs DB (`dashboard/prefs.set_notify_muted`). Global (a dashboard pref, not session state) so it works live AND parked; a bad `muted` (non-bool) lands ONLY as an `ok:False` reject row, action `notify-mute`. **telegram-notify** (log/path EMPTY — GLOBAL, since 2026-07-20) = the deferred off-device alert actually FIRED: the notifier's `_payload` sat red/green past `CLAUDE_DASH_NOTIFY_DELAY_S` (you didn't react) and it Popen'd the reused `notify` skill (`CLAUDE_DASH_NOTIFY_CMD`); content `{sid, kind}` (`asking`/`done`). "no Telegram arrived" → NO telegram-notify row for the sid = it never armed/fired (session muted — check the `notify-muted` map / prefs DB, OR `CLAUDE_DASH_NOTIFY_TELEGRAM=0`, OR you reacted within the delay so the arm was cancelled), a row PRESENT but no message = the notify script itself failed (its own transport error, outside the audit) or the launch raised (a paired `errors` row func `dashboard telegram notify`) |
| `pane_events` | mirror/scoreboard pane operation | action (open/close/toggle-on/toggle-off/grow/shrink/reset/setpct/**close-stale**/**focus-host**), **ok** (verified against kitty — 0 means the pane genuinely isn't there), detail (bias/resulting width). First stop for "frozen/missing pane" reports. **close-stale** (since 2026-07-11) = `close_stale_mirrors` swept a different-sid mirror out of the session's tab, detail `closed sid=<sid> win=<id>` — the previously-invisible op behind every vanished-mirror report; sweeping a still-OPEN session's mirror is the `pane hijack` anomaly. **focus-host** (since 2026-07-19) = `open_mirror` handed inner-tab focus back to the host pane after splitting the mirror/scoreboard in (detail `win=<anchor>`, the host window id) — an inner-tab `action first_window`, never an OS-window raise; `ok=0` means the `kitten @ action` call failed, so the tab may still show "▪ session" instead of the host's ai-title. Only emitted when `open_mirror` actually created a pane AND had a host anchor. An `open` with detail `skipped: no host pane (daemon/headless session)` = a SessionStart with no `KITTY_WINDOW_ID` and no `claude_session`-tagged window (an agents-view/`claude daemon run` session or headless `claude -p`) deliberately opened nothing. Pruned with the other per-session tables (was once omitted — unbounded growth) |
| `otel` | ONE raw OpenTelemetry metric datapoint | metric (`token`/`cost`), query_source (**`main`/`subagent`/`auxiliary`** — auxiliary = Claude Code's hidden summarizer/title agents), model, type (`input`/`output`/`cacheRead`/`cacheCreation`; empty for cost), value, pid. Written by the global OTLP receiver (`plugins/otel/`, entry `claude-otlp-receiver.py`), one row per datapoint per POST, so the scoreboard cost/token counters are fully reconstructible: `SELECT type, SUM(value) FROM otel WHERE session_id=? AND metric='token' GROUP BY type` == the `tk_*` counters, and `SUM(value) WHERE metric='cost'` == the `cost` counter (incl. the auxiliary share transcript folding never saw). Summarised by `python3 bin/claude-audit.py otel <sid>`. This IS the cost ground truth now — the transcript is only a fallback source (see the cost shapes below). NB the receiver's `bump-otel` `now` totals are read from whatever DB the receiver's cached connection points at, so they can look healthy while the LIVE state DB (what the scorebar reads) accrues nothing — a park+resume inode swap that stranded the receiver on the `*.keep` file (the blank-Σ shape; `anomalies` cross-checks the live DB's `tk_*`/`tokens` counters against the presence of `bump-otel` rows) |

New always-audited swallow sites (previously silent — their absence used to make these symptoms triage-blind): `errors` rows for `release`/`release_id`/`pid_del` (failed slot release = stuck blue), `spawn <script> (script missing)` + `notify_tab <dispatch>` from hookkit (block never streams / dropped tab dispatch), `update_messages` from the scorebar (frozen ✉ row), `format_code` from core/ops (commands paint verbatim), and `lsof failed/missing` from claude-stream (see the stream-ended-too-early shape).

## Triage order

0. **If the scorebar shows an amber `⚠ N` chip** (or the mirror shows `⚠ audit:` lines) — the session ITSELF is telling you it has N swallowed exceptions: go straight to `python3 bin/claude-audit.py errors <sid>`. The chip/lines are `core/errwatch.py` reading the same `errors` table these steps query.
1. **`python3 bin/claude-audit.py anomalies <sid>`** — canned queries for known bug
   signatures: swallowed errors, streams that never ended, slot claims without
   release, tab left on a busy colour, **an Esc-sending web gesture that fired on
   a red dialog-open tab (declines the ask — the "User declined to answer
   questions" regression signature)**, duplicate SubagentStart, start-without-stop,
   **stop-without-start (hidden agents — spend likely missing from the scoreboard)**,
   failed tools, spawns that never registered a stream, pane operations that
   failed, tab applies where `kitten @` failed, a resume that lost its mirror
   history, **a monitor/fg tailer that gave up on a late output file (tab wrongly
   cleared to green mid-command)**, **OTLP writes stranded on a parked inode (bump-otel rows but the live
   state DB has no token counters — the blank-Σ/breakdown bug)**, **hook traffic
   under a sid with no sessions row (a resume forked the sid and the fork was
   never adopted — frozen cost/tab/mirror)**, **a bg/fg tailer that outlived the
   park (the reuse-live-db zombie — it recreated the state DB after keep-history)**,
   **SessionEnd fired but the stop-fold never ran (no stop-fmt decision + no OTEL —
   cost silently lost)**, **cross-session contamination (a task_id/slot token under
   more than one sid)**, and **duplicated mirror ops (identical block lines painted
   twice within 5s — the re-read tailer shape)**. Start here; a
   non-empty section usually IS the bug. (The hook-counting
   queries filter `handler != 'subscriber'` where a per-event count matters — the
   universal subscriber writes a second row for every event, which once made every
   normally-started agent read as "duplicate SubagentStart".)
2. **`python3 bin/claude-audit.py errors <sid>`** — full tracebacks for every swallowed
   exception. An error just before the symptom's timestamp is the prime suspect.
3. **`python3 bin/claude-audit.py timeline <sid> [limit] [--ops] [--otel]`** — the
   merged chronological story (hooks, tab transitions, slots, streams, spawns,
   state files, pane ops, errors). Find the symptom's moment, then read the
   surrounding ~30 lines both ways. `--ops` / `--otel` merge those high-volume
   tables into the story too (one row per paint op / metric datapoint — off by
   default so they don't drown the events; use them when the symptom is a
   painted-content or cost-arrival ordering question).
4. **Free-form**: `python3 bin/claude-audit.py sql "<query>"` — e.g. pull the full
   payload of one hook event, or diff `ops` against what the pane actually showed.
   `sql` opens the DB read-only (`mode=ro`) so triage can never mutate the
   evidence; a deliberate manual fixup (e.g. closing a stuck "(open)" session
   row) uses `sql-write` instead.

## Known bug shapes → what to look for

### The ⚠ warning light itself misbehaves (chip stuck / missing / mirror lines duplicated)
- The light is `core/errwatch.py`, polled+emitted by the scorebar. **No chip despite `errors` rows**: check `errors` for a `func` containing `errwatch.poll` — the watcher's OWN failure is audited exactly ONCE per process (recursion guard) and then goes silent, so a single such row means the light has been dark since that timestamp (restart the scorebar via a mirror toggle). Also check the scorebar is running at all (`streams`/pane state — no scorebar, no poll).
- **`⚠ audit: global:` lines in several sessions at once**: not a duplication bug — GLOBAL rows (`session_id=''`: an audit outage, a pre-session/CLI error) are shown by EVERY live session's light (each dedupes via its own `errseen-global` kv). Pull them with `bin/claude-audit.py errors ''`. A pre-2026-07-15 session showing NO trace of a known audit outage is the old blind spot (the light only counted per-sid rows), not a lost row. NB a fresh session's `errseen-global` checkpoint starts at 0, so stale global rows re-surface once in every NEW session — junk global rows are worth deleting (`sql-write`). One known junk shape (leak fixed 2026-07-16): `script='-c'`, func `spawn … (script missing)` — the TEST SUITE's own deliberate degrade row, written by an in-process unit test that bypassed the hermetic `CLAUDE_AUDIT_DIR` (conftest now sandboxes in-process writes too; `-c` is the pytest-xdist worker's argv[0]).
- **A `⚠ audit:` line reading `<script>: NoneType: None`**: pre-2026-07-16 display of a DELIBERATE no-exception degrade row (`A.error` outside an except block stores format_exc's `NoneType: None` sentinel). Current builds show the row's `func` string instead (`⚠ audit: <script>: spawn nope.py (script missing)`); the sentinel appearing on a current build means the row had an empty func too.
- **A mirror `⚠ audit:` line duplicated or missing**: compare the `state_files` `action='errseen'` checkpoint rows (`last`/`new`) against the `errors` rowids — a gap that was never covered by an `errseen` advance was never emitted (emit failed AFTER the checkpoint moved: at-most-once by design; the paired ops should be in the audit `ops` table if they made it out); an overlap means the kv checkpoint was lost (state DB recreated mid-session — cross-check the fresh-db/adopt trail).
- **A flood**: >3 new rows in one 5s poll collapse into one `⚠ audit: N new errors …` line by design — not a missing-lines bug.
- **A benign, expected-outcome degrade-audit persistently lighting the chip in every session** (a `NoneType: None` row for a `func` whose code path is a documented normal return, not a failure — e.g. an optional widget that just doesn't attach): that signature belongs on `core/errwatch.py`'s `IGNORE_FUNCS` set, which drops it from the chip COUNT and the painted `err_ops` while STILL writing the row to `errors` (queryable via `… errors ''`). Deciding fix-vs-ignore and adding the signature is the **global-errors skill** (`.claude/skills/global-errors/SKILL.md`). Ignore only a genuine expected-outcome path — a real stack trace gets FIXED.

- **A block shows in the terminal mirror but is missing from the web dashboard's
  stream (or the reverse asymmetry)**: check the audit `ops` rows' op JSON for a
  `src` field. Stamped ops (`sub:<agent_id>` / `team:<agent_id>` / `codex:<label>`)
  are dropped by the web mirror BY DESIGN (main-agent-only; the terminal paints
  everything) — a subagent/teammate/secondary-codex block absent on the web with a
  correctly-stamped `src` is not a bug. The bug shapes are the stamp being WRONG:
  a main-session op carrying a `src` (a hook process inherited a stray
  `$CLAUDE_OPS_SRC` — check `sessions.env`) hides lead activity from the web; an
  agent-stream op with NO `src` (a tailer spawned outside `stream_env`'s environ
  copy, or a standalone-codex misdetect in `watch.spawn`) leaks agent noise into
  the web stream. `sql "SELECT json_extract(op,'$.src'), producer, count(*) FROM
  ops WHERE session_id='<sid>' GROUP BY 1,2"` shows the per-producer stamp pattern
  at a glance (producer `claude-substream.py`/`claude-codex-stream.py` rows should be
  stamped; `claude-cmd-fmt.py` etc. should not — except `claude-monitor-fmt.py`,
  whose agent-launched monitors are stamped via the explicit `emit(src=)`).

- **No Telegram alert for a session left red/green on the dashboard** (docs/dashboard.md
  *Telegram alerts* — the deferred off-device notification): the alert fires only if the
  tab sat asking/done past `CLAUDE_DASH_NOTIFY_DELAY_S` (default 60s) with no reaction and
  the session isn't muted. Look for a `state_files` `telegram-notify` row (`{sid, kind}`)
  for the sid: PRESENT = it fired (a missing Telegram message past that is the notify
  script's own transport, outside this audit — or a paired `errors` row func
  `dashboard telegram notify` if the Popen raised). ABSENT = it never armed/fired — check,
  in order: the session's `notify-muted` state (a `notify-mute` `state_files` row, or the
  `notify-muted` map in `~/.claude/baqylau-dash-prefs.db`); `CLAUDE_DASH_NOTIFY_TELEGRAM=0`
  (master off); whether you REACTED within the delay (the tab left red/green — an arm is
  cancelled the moment the state moves, so a quickly-answered/closed session correctly
  gets none); and the dashboard actually running (no open `kind='dashboard'` streams row =
  the notifier isn't polling at all). The reverse — an alert you DIDN'T want — is the
  per-session mute (the 🔕 toggle) or a lower `CLAUDE_DASH_NOTIFY_DELAY_S`.

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
  `CLAUDE_WATCH_*`, docs/testing.md): the "session" is probably a test run,
  not a real one.
- **Tab stuck blue** — a `slots` claim (bg/fg/monitor/sub) with no release (cross-check
  the live truth: `sqlite3 .../bin/claude-mirror-<sid>.log.state.db "SELECT * FROM live"` —
  a row whose pid is dead is stale-but-harmless, it's ignored by liveness checks) + a
  `streams` row with `ended_at IS NULL`, or a `tab_transitions` `bg-recheck`/`bg-watch`
  row with `applied=0` whose reason explains why it refused to clear. Also check the
  `bg-watch` **stream row itself**: `killed-or-crashed` / still-open = the watcher died
  and nothing was left to clear the blue; NO bg-watch stream row at all → check the
  `spawns` table for purpose `watcher:bg-watch` and `errors` for func
  `spawn claude-tab-status.py` (since 2026-07-15 the watcher spawn is audited — an
  errors row = the spawn itself failed; neither row = never requested); and an apply
  whose reason says
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
  whether the final apply carried a "kitten @ failed" reason. `turn-over` is
  also GATED on having seen a mid-turn state that run: an immediate turn-over on
  the previous turn's stale green (the watcher spawns before d_thinking's paint,
  and a failed/lagging paint leaves the old row) was the premature-exit race —
  its tell is a `tab_transitions` row with reason "interrupt-watch: stale
  pre-turn row — paint failed/lagged, keep watching" (the gate working); a
  `turn-over` within ~1s of the UserPromptSubmit with NO applied mid-turn paint
  before it means the gate regressed.
  If the stuck stretch FOLLOWED a dashboard stop press, the `web-interrupt`
  state_files row (its `tab` field = what the Esc landed on) should be paired
  with an `escape-recheck` spawn (`spawns` purpose `watcher:escape-recheck`)
  and its `tab_transitions` verdict: an APPLIED "web Esc into … mid-thinking
  cancel gap" row = the recovery worked; a bail row ("state moved on" /
  "transcript moved" / "not on magenta") says which real signal it deferred
  to; a `web-interrupt` on magenta with NO escape-recheck rows at all = the
  spawn never fired (check `errors` func `dashboard interrupt (escape-recheck
  spawn)`) — that IS the stuck-magenta-after-web-stop bug.
- **Tab flips green too early** — a `bg-recheck`/`bg-watch`/`notify` transition with
  `applied=1` while a `streams` row was still open; the reason column shows what it
  (wrongly) concluded.
  A specific green-too-early shape (fixed 2026-07-18): **interrupt with a
  QUEUED message** — Claude Code delivers the queued prompt the instant the
  interrupt lands, a new turn starts thinking, and the interrupt-watch's green
  flip painted "done" over that live think (stuck green until the first tool
  event). The watch now checks what follows the interrupt line in the
  transcript: the healthy trail is a `tab_transitions` row reason
  "interrupt-watch: queued prompt delivered on the interrupt — the new turn
  owns the tab" (applied=0) and the SAME watch stream continuing to the real
  `turn-over`; an APPLIED `interrupt-detected-flipped-green` whose transcript
  shows a user-prompt record right after the interrupt line = the regression.
  escape-recheck is immune by construction (the queued prompt's record is
  transcript growth, which bails it).
- **fg command's output not found + tab goes green while it's still executing**
  *(late redirect target, fixed 2026-07-15)* — a foreground command that creates
  its output file LATE (`sleep 45; cmd > /tmp/out`, a `for … do sleep 40; cmd >
  out` retry loop) — cmd-pre `hook_events` decision `… tailing command's own
  redirect`, so `src_path` is the user's redirect target (e.g. `/tmp/mr3.txt`),
  NOT a `…log.fg.*.out` tee file. Pre-fix the fg tailer waited only the flat
  `FIND_S` (~12 s) for the file to appear, then ended the `fg` stream
  `output-file-not-found`, which released the fg slot → `bg-recheck(fg): no live
  markers remain` cleared the tab off blue (executing → awaiting-response) while
  the command ran on, and the late output never streamed. Tell: an `fg`
  `streams` row ending `output-file-not-found` whose command's `PostToolUse`
  (`claude-cmd-fmt.py` `rendered: …`) fired SECONDS LATER (e.g. stream ended at
  +16 s, Post at +52 s), plus the slot `release` + `bg-recheck` apply in between.
  The `anomalies` **"fg tailer gave up on a late redirect target"** section flags
  the `fg`/`output-file-not-found` row directly. On a current build the fg tailer
  waits on command LIVENESS (`wait_fg_src` polls for the file until it lands OR
  the PostToolUse outcome hand-off arrives), the analogue of the monitor's
  process-liveness wait — so the slot stays held and the tab stays blue for the
  whole command. A non-empty anomaly row on a current build is the regression
  (or a genuinely fileless command, whose stream ends AFTER its Post — check the
  timing).
- **fg mirror block shows `■ output not found` (tab behaved fine)** *(mis-scoped
  redirect, fixed 2026-07-16)* — cmd-pre decision `tailing command's own
  redirect` but the `fg` stream's `src_path` is a file the command never wrote
  at that path: pre-fix, `parse_redirect` took the LAST redirect anywhere in
  the command as the output sink and joined a relative target against the hook
  payload's cwd. Two ways that broke (session cf514935's repro command hit
  both): a `cd` earlier in the command meant the file was created elsewhere
  (the tailer waited on the wrong path via command liveness — the stream ends
  `output-file-not-found` AT its PostToolUse, the tab stayed blue the whole
  run — then painted "output not found"), and a mid-command bookkeeping
  redirect (`… >> summary.txt ) & done↵wait↵sort summary.txt`) isn't the
  visible output sink anyway — the trailing statements print to stdout, which
  redirect-tail mode never captures. Tell: `src_path` = hook-cwd + a relative
  name while the command text contains a `cd`, and/or statements after the
  last redirect. Since 2026-07-16 `parse_redirect` is statement-scoped (only a
  FINAL-statement redirect engages redirect-tail mode; anything else tees,
  which shows everything) and a relative target follows statically resolvable
  top-level `cd`s (`tools._follow_cd`; dynamic/subshell cds → tee). On a
  current build this shape = the scoping/tracking regressed. NB the `fg tailer
  gave up on a late redirect target` anomaly can also surface these rows —
  distinguish by timing: mis-scoped ends AT its Post (the liveness wait
  worked, the path was wrong); a late-redirect regression ends long BEFORE its
  Post.
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
  `.yml`/`.yaml`; source code: `.py`/`.java`/`.kt`/`.sh` etc; docs/mirror-pane.md). Detection runs in the TAILER (from the raw command every launch
  site passes via `CLAUDE_STREAM_CMD` — `hookkit.stream_env`), for main-session
  AND subagent fg commands alike; launcher `hook_events` decisions say nothing
  about render mode (pre-2026-07-12 they carried a `[*-render]` suffix). First
  confirm it was even engaged: a `state_files` `render:<taskid>` `start` row
  exists (content `kind`) — if
  not, the `*_source` detector didn't match (piped/redirected/chained command,
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
  (docs/wiring.md), not this repo's code; otherwise check `errors` for func
  `copy (…)`: `bad url` (renderer built a malformed link), `state DB gone` (clicked
  after SessionEnd — expected no-op), `read ops` / `no clipboard tool`. `chars: 0`
  with what=`out` on a still-running block just means no output had streamed yet.
  Wrong TEXT copied: compare the group's ops (`SELECT op FROM ops` in the state DB,
  filter `"g"` = the gid from the audit row) — ⧉cmd must equal the `code` op's `s`
  (the WYSIWYG pretty-printed form, deliberately NOT the pre-reflow original) and
  ⧉out the ANSI-stripped concatenation of the group's `gut` ops.
- **Dashboard composer message lands in the terminal as a DRAFT with a trailing
  newline, never sent — intermittently** *(split-Enter, fixed 2026-07-18)* — the
  `web-send` row shows `ok: true` (kitten really typed into the right window) yet
  the message never reaches the transcript: pre-fix `kitten_send_text` wrote
  message+CR in ONE write, and Claude Code's chunk-based paste detection
  sometimes read them as one pasted chunk, turning the CR into a draft newline
  instead of a submit (one read vs two is event-loop scheduling → intermittent).
  Since 2026-07-18 the CR is a separate second `send-text` call after
  `SEND_ENTER_GAP_S` (150 ms), so it always arrives as its own stdin read = a
  real Enter. Tell for a recurrence on a current build: a `web-send` `ok: true`
  with NO later `UserPromptSubmit` hook_event carrying that text (and no ⧗-queue
  explanation — its `tab` field ∉ thinking/working/executing). An `ok: false`
  with an `errors` func `dashboard message (send failed)` can now also mean the
  SECOND write failed — text delivered but Enter lost, i.e. a draft left in the
  terminal.
- **A web attachment/screenshot "didn't attach" or the message went out without
  it** *(Web attachments)* — pull the session's `web-upload` `state_files` rows
  (the upload) and the matching `web-send`/`web-launch` row (the delivery). An
  upload row with `ok: false` (+ an `errors` func `dashboard upload (write
  failed)`) means the bytes never landed on disk — the `@`-mention would point
  at nothing. An `ok: true` `web-upload` but a `web-send` with `attachments: 0`
  means the path was DROPPED at send: `_attachment_paths` admits a path only if
  it resolves inside `paths.UPLOADS_DIR` and still exists (a page sending a stale
  path — the week-old `_prune_uploads` sweep already deleted it — or any path
  outside the staging root is silently skipped). `attachments: N>0` with the
  message still un-attached in the transcript is a Claude Code `@`-resolution
  issue (the mention rode the paste fine — check the delivered text), not a
  dashboard one.
- **Quick-command button (compact / model / effort) "did nothing"** *(since
  2026-07-18)* — pull the session's `web-command` `state_files` rows. `ok: true`
  with `tab` ∈ thinking/working/executing = the command QUEUED in the TUI's own
  message queue and runs at the turn boundary (expected, the page toasts it);
  `ok: false` + `tab: awaiting-command` = refused because a dialog was up (by
  design — its digits would decide the dialog); no row at all + an `errors` func
  `dashboard command (bad cmd)` = an off-vocabulary request (nothing was typed);
  `ok: true` on an idle tab with no effect = FIRST check the paired
  `web-command-confirm` row (model/effort, non-queued only — the TUI can
  interpose a Yes/No switch-confirm menu, the prompt-cache warning, and the
  server auto-presses its Yes via `dashboard/confirmdialog.py`): `confirm:
  confirmed` = menu answered; `confirm: none` = no menu appeared (applied
  outright); `confirm: failed` (+ an `errors` func `dashboard command
  (confirm failed)`) = the menu is still open in the terminal awaiting the
  user. A QUEUED model/effort has NO confirm row by design (the menu only
  opens at the turn boundary) — an unanswered late menu shows as a red tab.
  Otherwise check the transcript for the slash command's record (the TUI
  parses it — a typo'd model alias errors in-chat, invisible to the audit by
  design: the TUI stays authoritative).
- **Renamed on the web but the name didn't stick / the kitty tab didn't
  change** *(since 2026-07-18)* — pull the session's `web-rename` `state_files`
  rows. `ok: false` + `reason: no transcript|unsupported` = nothing was
  appended (missing transcript path, or a codex rollout — renames only speak
  Claude `projects/` transcripts); `ok: false` with a paired `errors` func
  `dashboard rename (append failed)` = the append itself failed (perms/disk).
  `ok: true` + `tab_retitled: false` = the picker/dashboard rename landed but
  the live tab kept its old title — expected for a parked/terminal-less rename
  (`win: ""`), otherwise the kitten `set-tab-title` call was refused (a name
  starting with `-` reads as a CLI flag). A tab that no longer follows auto
  ai-title changes AFTER a web rename is the deliberate kitty sticky-title
  override, not a bug. Dashboard title later REVERTING to an auto title while
  `claude --resume` still shows the custom name = the `agent-name` record
  scrolled past `transcript.TITLE_TAIL_B` (64KB) of newer transcript with a
  fresher `ai-title` in the window — the bounded-tail scan's one accepted gap;
  renaming again re-appends at EOF and wins again. Verify what's actually in
  the file with `tail -c 65536 <transcript> | grep -a agent-name`.
- **Session shows NO name on the dashboard (title blank on the card/header) —
  usually after the session entered a worktree; ctx bar frozen and git chip
  wrong the same way** *(stale sessions-row paths, fixed 2026-07-19)* — Claude
  Code RELOCATES the transcript when the session's cwd moves to another
  project dir (EnterWorktree → the file moves to the worktree cwd's
  `projects/` slug dir; every later hook payload carries the new path), and
  the dashboard title/ctx/git/rename/rewind ALL key off the audit `sessions`
  row's start-time `transcript_path`/`cwd` — `session_title` swallows the
  missing-file OSError and returns "" (the e7192407 shape). On a current
  build the dispatcher refreshes the row on every event (`A.session_paths`)
  and the relocation lands as a `session-paths` `state_files` row (old →
  new); the `anomalies` section *"sessions row transcript_path stale vs
  latest hook payload (relocation refresh regressed)"* flags the regressed
  case directly — confirm with `SELECT transcript_path FROM sessions` vs the
  latest subscriber payload's `transcript_path`. A PRE-fix session that
  entered a worktree stays stale forever (no later event will fix an ended
  session): repair the row by hand via `sql-write`, pointing it at the LATEST
  payload's path. NB the row's transcript_path missing on disk is the
  relocation, not a deleted transcript — never "clean up" such a session.
- **Web rewind failed / picked the wrong checkpoint / left the session inside a
  menu** *(full web rewind, since 2026-07-18)* — pull the session's
  `web-rewind-to` `state_files` rows. `step: busy` = refused on a busy tab (by
  design — cancel/stop first). `step: dialog` = refused on a red
  `awaiting-command` tab (a modal ask/plan/permission dialog is open — a typed
  `/rewind` would land IN it; `_dialog_open_guard`, since 2026-07-20 — this
  case used to fall under `step: busy` while `awaiting-command` was in
  `BUSY_TABS`). `step: open` = the typed `/rewind` never opened
  the menu (check the tab really was idle and the window id in `win`); `find` =
  the target prompt matched no menu entry after a full up-then-down scan
  (stale page after a kitty-side rewind, or the prompt text's first line
  diverged from the menu entry — compare `errors` func `dashboard rewind-to
  (find)` detail against `bin/claude-audit.py sql` over the transcript);
  `option` = the requested restore mode wasn't on the confirm menu (a `code`
  request at a no-code-change checkpoint — the error names that reason; a
  `both` request there degrades to the conversation restore instead of
  bailing, row carries `degraded: true`); `confirm`/`close` = a menu
  transition never rendered (kitten latency — the driver polls up to its
  timeouts). EVERY bail Escape-closes the menus before the 409 — a session
  found sitting in an open rewind menu right after a bailed row means _bail
  regressed. On success, sanity-check `steps` (0 = the page's `ups` hint was
  exact; large = the page view was stale but the text scan corrected) and
  `digit` against `mode` (the label→digit mapping — the confirm menu's
  numbering shifts with content, so a fixed digit is the regression to look
  for). A rewind writes NOTHING to the transcript at restore time: the
  conversation fork appears only at the NEXT send (a user record whose
  parentUuid points back), so "the web feed / transcript still shows the
  rewound turns" is expected until then.
- **Web plan card missing / stale / decision failed** *(web plan mode, since
  2026-07-18)* — same triage as the ask card below, over the `plan-pending` +
  `web-plan` rows instead: no write = unhosted/agent_id/routing; stuck card =
  write without remove (declines are hookless — the turn boundary clears, and
  a web interaction's `open` bail self-heals with reason `web open-bail`);
  `step: option` on decide = label drift (the dialog changed between the
  page's /plan-options fetch and the click — benign, the card refetches);
  wrong option pressed is ruled out BY the label verification, so if the
  executed mode disagrees with the user's pick, compare the `web-plan` row's
  `label` against the PostToolUse-time permission mode instead.
- **Web tasks card missing / stale / wrong statuses** *(web tasks, since
  2026-07-18)* — the card is fed by the `tasks` state-DB kv, which
  `claude-task-fmt.py` re-snapshots from Claude Code's on-disk task dir
  (`<config>/tasks/session-<first uuid segment>/`) on every task-touching hook.
  Evidence: the `tasks` `state_files` rows (each write carries the task count +
  a per-status breakdown — `pending:N in_progress:N completed:N`) and the
  handler's `hook_events` decisions (`tasks stashed (…)` on
  PostToolUse(TaskCreate|TaskUpdate) and appended to the `rendered:` decision on
  TaskCreated/TaskCompleted). Card never appeared → no `tasks` write: unhosted
  session (no state DB, by design), the event carried an `agent_id` (ignored),
  or the PostToolUse `TaskCreate|TaskUpdate` routing broke. Statuses stale
  (e.g. stuck pending while the TUI shows in_progress) → the status flip's
  PostToolUse(TaskUpdate) row exists but no matching `tasks` write: the snapshot
  read the dir mid-write (torn read, self-heals next op) or the dir resolution
  drifted (env `CLAUDE_CONFIG_DIR` differs between the hook and the TUI). A
  parked session showing an EMPTY card that had tasks → the last snapshot ran
  after Claude Code's session-end cleanup emptied the dir (should be impossible
  — no hook fires at cleanup; if seen, check what event triggered the last
  `tasks` write). Remember the on-disk dir itself reads empty for every ended
  session — the kv is the only surviving record, so never "verify" against the
  dir post-hoc.
- **Memory tab empty / a touched note missing / wrong verb label** *(memory tab,
  since 2026-07-21)* — the tab is fed by the `memory` state-DB kv, which
  `plugins/claude_code/memory.py` `record()` merges on every file op under the
  memory wiki (`~/wiki/01`). Evidence: the `memory` `state_files` rows (each write
  carries `verb`/`path`/`agent`/`notes`-count) and the producers' `hook_events`
  decisions (the `+... [mem:<who>]` fragment on `claude-file-fmt.py`, or the
  substream's file-op render for a subagent). Tab MISSING entirely → the session
  is OUT OF SCOPE: the feature is enabled only for sessions inside
  `~/code/01/aggregator-adapters` (`memory.in_scope` over the session cwd; the
  server's `memory_scope` flag gates the tab client-side), so a session elsewhere
  has no Memory tab AND records nothing even after editing the wiki — by design.
  Tab present but empty though the wiki was edited
  → no `memory` write: unhosted session (no state DB — `record` is `parked`-guarded
  by design), or the path wasn't under the hardcoded root (`memory.root()` is
  `~/wiki/01` unless the `BAQYLAU_MEMORY_ROOT` test seam is set — a vault elsewhere
  is invisible), or the op carried an `agent_id` AND the substream renderer didn't
  run (subagent capture lives in `substream_render.render_file`, not
  `file_fmt`). A subagent's note missing while the main agent's show → the
  substream never rendered that agent (check its `stream_start`/`stream_end`), since
  that is the ONLY capture path for `agent_id` ops. Wrong/"downgraded" verb (e.g.
  a note you WROTE shows as "read") → verbs ESCALATE by rank (Write > Update >
  Read) and never downgrade, so the stored verb is the most consequential op seen;
  a note both read and written shows `write` — the `state_files` rows replay the
  exact op sequence. A memory op that painted its 🧠 marker in the mirror but never
  reached the tab → the `record` write raised (paired `errors` func `memory.record`).
- **Mic button missing / dictation dead** *(web dictation, since 2026-07-18)*
  — the button renders only when `GET /api/dictate` reports a configured key
  (`~/.config/deepgram/api-key` / `CLAUDE_DICTATE_KEY_FILE`), so "missing"
  usually = no/empty key file, not a bug. "Dead" triages from the
  `web-dictate` `state_files` rows (GLOBAL — `session_id=''`, like
  `web-launch`): every token mint attempt leaves one — `{ok:1, rate, cwd,
  keyterms}` on success ("my project word didn't bias" reads from here: an
  empty `cwd` means the sent directory failed the isdir guard, and
  `keyterms` counts the MERGED project-first list — nearest
  `.claude/deepgram-keyterms` → outer → global, `dictate.keyterms`),
  `why: bad-rate` (client sent a bogus sample_rate),
  `why: no-key` (key vanished between probe and mint), `why: grant` (the
  Deepgram grant call failed — pairs with an `errors` row func `dashboard
  dictate (grant failed)` carrying the exception). NO row at all = the POST
  never reached the handler (guard rejection: READONLY day, missing
  `X-Claude-Dash` header, foreign Origin — same `_post_guard` as every
  control-plane write). Rows ok but no text lands = the failure is
  client-side (mic permission, the browser→Deepgram wss, the audio
  worklet) — that leg deliberately never touches the server, so the audit
  ends at the mint; check the browser console, not the DB.
- **Web ask card missing / stale / answer failed** *(web ask, since 2026-07-18)*
  — three evidence sources: the `ask-pending` `state_files` rows (did the stash
  write? did it clear, and with which reason?), the `web-answer` rows (what the
  page tried, and which dialog step failed), and the tool's own
  `hook_events` (PreToolUse = the dialog opened; PostToolUse = a REAL submit —
  declines fire nothing). Card never appeared → no `ask-pending` write: the
  session is unhosted (no state DB, by design), the ask carried an `agent_id`
  (subagent — ignored), or PreToolUse routing broke. Card stuck after the
  dialog was long gone → a `write` without `remove`: check Stop/UserPromptSubmit
  routing (the decline paths have NO hook of their own — the turn boundary IS
  the clear). Answer 409 `step: open` → the dialog wasn't on screen (usually:
  answered or Esc'd in the terminal, SSE clear raced the click — benign).
  But `step: open` with the tab showing "User declined to answer questions"
  and the user insisting they answered on the web is the **Esc-gesture-declined
  ask** (fixed 2026-07-20, session 7809eaff): a web `interrupt` / `rewind`
  (cancel-edit) fired an Escape into the OPEN ask and declined it before the
  answer POSTed. The tell is a `web-rewind` row `mode: cancel-edit` (or a
  `web-interrupt`) on the SAME `win` between the `ask-pending` write and the
  `web-answer` `step: open`. The fix refuses those gestures on a red
  `awaiting-command` tab (`_dialog_open_guard`), auditing the refusal as a
  `web-interrupt`/`web-rewind`/`web-rewind-to` row `ok: false, step: dialog`
  — so post-fix, a `step: dialog` row is the guard WORKING (by design), not the
  bug; the bug would be an Esc-sending row with NO `step: dialog` landing while
  an ask-pending is live. Other steps name the navigation stage and pair
  `errors` func `dashboard answer (<step>)`; the dialog is left OPEN on every bail (never Escape —
  that declines), so the user can retry or finish in the terminal. A
  `step: question` bail ("question N never became current") has TWO
  known causes. (1) The WRAPPED-QUESTION bug (session 412b980b, pre-2026-07-18
  fix): a 555-char question wraps across screen lines and `current_question`'s
  exact line-set match could never see it — fixed by stripping ALL whitespace
  from both sides before a substring match, with the review pane excluded
  because its recap repeats the question texts. (2) **A Claude Code VERSION
  DRIFT that changed the dialog key model** — the v2.1.215 overhaul (fixed
  2026-07-19, session f43b2137) made digits inert (selection became cursor +
  Enter) and stopped single-select auto-advancing on a digit, so the driver
  answered question 1 with a no-op digit and question 2 never became current;
  the tell is a `step: question` bail on a MULTI-question ask right after a
  Claude Code upgrade, with the FIRST question's answer never landing. The
  fix re-measured the dialog and rewrote `askdialog.py` to cursor + Enter
  (docs/dashboard.md *Web ask*). This class of bug can ONLY be caught by
  driving a live dialog — so on any `step: question`/`step: cursor`/`step:
  options` bail, first confirm the running `claude --version` still matches
  what `askdialog.py`'s header comment was measured against. Otherwise the
  question text genuinely never appeared (dialog gone, or the payload's
  question text diverged from what the TUI renders). Answers WRONG in the
  transcript → compare the PostToolUse `answers` against the `web-answer`
  row's intent; multiSelect Enter TOGGLES the cursored box, so a pre-toggled
  box the page didn't know about points at the screen-diff logic
  (`askdialog._answer_question`). Unsubmitted selections LOST on a device
  switch / reload → the `ask-draft` `state_files` rows: a `write` action
  (path key `ask-draft`) records each debounced persist (`tool_use_id` +
  the page's `origin`); no write ⇒ the POST never landed (guard 409 for a
  stale/gone ask, or the card never called it). A draft that reappeared
  after the question changed ⇒ a missing `remove`: `ask-draft` must clear on
  the SAME boundary as `ask-pending` (its PostToolUse `answered`, or the turn
  boundary), so a `write` without a matching `remove` points at the
  ask_fmt.py clear loop (`DRAFT_KEY` appended only when `ask-pending` is in
  the clear set).
- **Unsent COMPOSER message lost on a device switch / reload / return-to-session**
  → the `composer-draft` `state_files` rows (path key `composer-draft`): a
  `write` (with `chars` + `origin`) records each debounced persist, a `remove`
  the send/empty clear. No `write` ⇒ the POST never landed (the box never
  called it, or the state DB was unreachable — a `dashboard composer-draft
  (write failed)` `errors` row). A draft that REAPPEARED after you sent it ⇒ a
  missing `remove` (send-time `clearComposerDraft` didn't fire) — note on the
  resume-&-send path the draft lives in the PARKED DB that adoption renames to
  the new sid, so a stale draft there re-shows in the resumed composer. Unlike
  `ask-draft` there is deliberately NO turn-boundary clear (a message draft is
  meant to survive), so a lingering `write` is expected, not a bug.
- **New-session form forgot the last directory/model/effort (or disagrees
  across devices)** → `ns-prefs` is GLOBAL now (`dashboard/prefs.py`,
  `~/.claude/baqylau-dash-prefs.db`), not per-browser localStorage. Pull the
  log/path-empty `ns-prefs` `state_files` rows (`sql "SELECT * FROM
  state_files WHERE action='ns-prefs' ORDER BY id DESC"`): the newest is what
  the form pre-selects. A launched value NOT stored = it failed validation and
  was dropped (a bad model/effort — the row shows only the fields that passed)
  or a `dashboard ns-prefs (write failed)` `errors` row. Missing entirely on a
  fresh device = the boot `GET /api/ns-prefs` hasn't primed `S.nsPrefs` yet
  (the form fell back to defaults; it self-corrects on the next open).
- **Starting a session from the dashboard yanks macOS focus to kitty (the user
  wanted to stay in the browser)** *(root-fixed 2026-07-18: launch_pane's
  conditional --keep-focus)* — pull the `state_files` `web-launch-steal-watch`
  row that follows the `web-launch` row (both log/path-empty; `sql "SELECT *
  FROM state_files WHERE action LIKE 'web-launch%' ORDER BY id DESC LIMIT
  10"`). `steals: []` = no takeover during the ~30s startup window (if the
  user still saw one, it landed later or came from something other than the
  terminal app). Non-empty `steals` = a launch path still activates the
  terminal; the offsets name the second — ≈0s is the tab launch itself, ≈2-6s
  matches the SessionStart mirror/scorebar pane opens (the original culprits:
  they passed kitty's `--keep-focus`, whose focus-restore raises the OS window
  whenever the app is in the BACKGROUND — `frontends/kitty.py launch_pane` now
  gates the flag on `kitten_app_focused`, so first check that gate didn't
  regress). No watch row at all = the watch never armed: the frontend had no
  `app_id()`, the terminal was already frontmost at click time, or a pre-fix
  server is still running (restart `claude-dashboard.py`). Two rejected fixes
  (docs/dashboard.md *Web launches must not steal macOS focus*): `--keep-focus`
  on the tab launch CAUSES the steal on a background kitty (verified against
  plain-config kitty 0.45), and the active bounce-back (`web-launch-refocus`
  rows, 2026-07-18 only) yanked users who deliberately switched to kitty —
  do not re-add either.
- **A web launch feels slow / the page's "starting session…" view times out /
  never jumps to the new session** — pull the `web-launch` row and the
  `web-launch-wake` row its watcher writes (both log/path-empty; same
  `action LIKE 'web-launch%'` query as above). The wake row's `waited_s` IS
  the launch→SessionStart-appearance latency: ~1.4-2.1s is claude's own boot
  (measured normal — nothing to fix server-side), 5s+ means claude started
  slow or not at all (check the tab; a `web-launch` with `ok: true` only says
  `kitten @ launch` accepted the call — a command-not-found tab still exits 0).
  `ok: false` + empty `sid` on the wake row = the session NEVER appeared
  within `LAUNCHWAKE_MAX_S`: no SessionStart fired (claude died before hooks,
  wrong account alias, hook wiring broken) — correlate with whether a
  `sessions` row exists at all near that ts. A wake row with a filled `sid`
  but the user STILL reports no jump = the page-side watch mismatched: compare
  the row's `win` against the launched session's `sessions.kitty_window_id`
  (empty `win` = kitty didn't report an id, the page fell back to the cwd
  heuristic — ambiguous when two launches race in one directory). No wake row
  at all next to a `web-launch ok: true` = a pre-fix server is still running
  (restart `claude-dashboard.py`), or the watcher thread died — check `errors`
  func `dashboard launch wake`.
- **One session shows up in TWO kitty tabs / messaging it opens a duplicate
  tab each time / sends land in the "wrong" (older) tab** *(duplicate
  resume-launch, guard added 2026-07-19)* — the page resume-launches a
  session it believes is PARKED, but the session already had a LIVE tab, so a
  second `claude --resume <sid>` runs against the SAME transcript (both panes
  tagged `claude_session=<sid>`; `kitten @ ls` shows the sid on two windows,
  and `_live_windows`/`window_for_session` keep the FIRST-iterated one, so
  web-sends land in whichever tab that is). Tell: two `web-launch` rows for
  one cwd seconds apart — a fresh `resume: ""` then a `resume: <that new
  sid>` — with NO `adopt` row between (this is NOT a sid-fork). Root trigger
  is a STALE browser page (its live/parked snapshot froze — classically after
  the dashboard server restarted and the SSE dropped; check the `dashboard`
  `streams` rows for a restart just before the episode). On a current build
  `post_new_session` REFUSES a resume of an already-live sid: a 409 + a
  `web-launch` `ok: false` row carrying the live `win`. So a
  `web-launch ok:false` with a `resume` set and a `win` filled = the guard
  FIRED (healthy — the duplicate was prevented); a duplicate-tab episode with
  the guard NOT firing (two `ok:true` resume launches) on a current build = the
  guard regressed or a pre-fix server is still running (restart
  `claude-dashboard.py`). Recover a live duplicate by closing the extra tab
  (`kitten @ close-tab --match id:<tabid>`).
- **Resume from the dashboard opens a kitty tab that instantly dies (or "does
  nothing"), often a session that also shows only its sid, no name** *(gone
  transcript, guard added 2026-07-21)* — the resume target's transcript
  `.jsonl` no longer exists, so `claude --resume <sid>` finds no conversation
  and the launched tab exits at once. Tell on a PRE-fix / bypassed path: a
  `web-launch` row with `resume: <sid>`, `ok: true`, a `win` filled — the
  kitten launch SUCCEEDED (a tab really spawned) — but NO SessionStart follows
  (no fresh `sessions` row for a forked sid, no `adopt`, no `web-launch-wake`
  arrival). Confirm the file: the resumed sid's `sessions.transcript_path` is
  absent on disk (`ls` it). On a current build `post_new_session` PRE-REJECTS
  it: a **410** + a `web-launch` `ok: false`, `why: transcript missing` row,
  before any tab — so that row = the guard FIRED (healthy). NB the account is a
  red herring: the switcher symlinks every `configs/<slug>/projects` to the
  shared `~/.claude/projects`, so all accounts resolve the same file (or its
  absence) — do NOT chase "wrong account". The same missing/`.jsonl`-less
  transcript is ALSO why the card shows a bare sid (session_title returns `''`;
  see docs/session-naming-findings.md — a slash-command session that ended
  before an ai-title now falls back to `/command`, but a DELETED transcript
  still has nothing to read).
- **Clicking a Read/Update/Write line doesn't expand (or won't collapse)** — the
  click-to-view chain is stash → toggle → reflow; check it in that order. (1) Stash:
  a `state_files` `view-stash` row (content: gid = the op's tool_use_id, tool, ops
  count; subagent stashes also carry `agent`) must exist from `claude-file-fmt.py`
  or `claude-substream.py` — no row + no `errors` `view-stash (…)` means the op
  failed / had no content / carried no tool_use_id, and the line was deliberately
  left unlinked. (2) Toggle: each click leaves a `state_files` `view` row (content:
  gid + `open: true/false`); `open: null` = clicked an id with NO stash (feedback
  no-op). NO view row at all = kitty never launched the handler (open-actions.conf
  wiring) or `errors` func `view (…)`. (3) Reflow: the renderer mirrors the
  `view-open` kv set (state DB: `SELECT val FROM kv WHERE key='view-open'`) and the
  stashed block lives at kv `view:<gid>`; the instant repaint relies on the
  `renderer-pid` kv row (the copy handler SIGWINCHes it — a stale/dead pid degrades
  to the 200ms poll, i.e. "works but feels slow"; NO renderer-pid row at all = the
  pane is running a pre-feature renderer — toggle the mirror off/on). Expansion
  MOVED the view (the top-line anchor rule says the viewport's top line stays
  exactly where it was across any toggle — EXCEPT an at-bottom click, which
  restores to the new bottom to keep tail-following: `follow: true` on the
  row): read the toggle's `view-reflow` state_files row
  (idx/anchor/cap0/up/applied/dsr/landed/retried/follow — see the schema
  entry) — `anchor: null` = the get-text capture or row match failed and it
  degraded to clicked-line-at-top, and since 2026-07-12 EVERY null path pairs
  with an `errors` row func `viewport_anchor (no window|no capture|empty
  capture|no match)` (a null with no errors row = pre-fix renderer); `landed
  != anchor` with `retried: true` and `up` ≈ 5000 = the SCROLLBACK CEILING
  (the restore target sat above kitty's scrollback_lines and the scroll
  clamped at `total+1-h-scrollback` — the frame outgrew the buffer; the
  `ROW_BUDGET` trim, default 4800 / `CLAUDE_MIRROR_SCROLLBACK`, exists to
  prevent exactly this — check `paint.rows` > budget); `applied: false` with
  `up > 0` = the kitten scroll-window call failed (socket problem); NO
  view-reflow row at all for a `view` row = the renderer never processed the
  toggle. `errors` funcs `viewport_anchor (…)` / `toggle_scroll (view
  toggle)` carry the failure detail. If the reflow row looks PERFECT
  (`anchor == landed`) but the user still reports a jump, read the
  `view-drift` rows that follow it — the 8s post-toggle watch records every
  viewport movement after the verified landing (one instant leap to ≈0 = a
  scroll-to-start executed from somewhere; gradual steps = the user
  scrolling; NO drift rows = the pane stayed put and the report needs
  re-examining). A freshly toggled pane opening SCROLLED
  (not at the bottom) with a phantom view-reflow row that has NO paired
  `view` row = the startup kv-adoption regressed (the renderer treated the
  inherited `view-open` set as a click; fixed 2026-07-12 — VIEW_OPEN is
  seeded from kv on inode change, silently). A `paint` row of kind `skip` is
  healthy: a WINCH at an unchanged size with no toggle plan (stray/duplicate
  click-nudge) deliberately paints nothing — a full repaint there clamps a
  scrolled-up viewport to the bottom with no restore. (Historical: the nudge
  SIGWINCH once set `_resized`, whose guard skipped the whole plan — every
  nudged toggle parked at the bottom with no view-reflow evidence; that
  guard is gone and the row exists so this can't hide again.) Expanded code
  UNHIGHLIGHTED: the stash is raw text + `lex`/`num` fields on the gut op —
  highlighting happens in the renderer, so no colour = the renderer's interpreter
  lacks pygments (its re-exec probe failed), not a stash bug.
- **Mirror scrolls by itself / opens scrolled to the top / view keeps jumping
  despite healthy view-reflow rows** — POISONED OUTPUT: the ops stream carries
  raw command output and every reflow REPLAYS it, so an escape sequence some
  command printed re-executes on every repaint (live case: a tee'd
  `ESC P @kitty-cmd scroll-window` DCS scrolled the pane to the top on each
  reflow — the view-reflow rows looked perfect because the pin worked and the
  replayed op immediately re-scrolled). Since 2026-07-12 the renderer
  neutralizes all op text at paint (`render.neutralize` — only SGR + OSC 8
  survive), so this CAN'T recur while painting goes through `_render`; if the
  symptom appears anyway, first find the payload (`SELECT id, substr(op,1,120)
  FROM ops WHERE op LIKE '%\x1b%'` — or grep for `kitty-cmd`/`[2J`/`[3J` in op
  text) and then check whether some new paint path bypassed neutralize.
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
  regressed. Whether the monitor's process was EVER identified is the
  `state_files` `proc-found` row (path `monitor:<taskid>`, content pid) — an
  idle-fallback end with NO proc-found row = find_proc never matched (it died
  before the tailer ran `ps`, or its argv was unmatchable); one WITH the row =
  the latch broke later. **A monitor that ends `output-file-not-found`/`idle-fallback`
  with "monitor process never found", 0–1 `lines_emitted`, and NO proc-found row —
  while the monitor demonstrably ran fine (nothing in the mirror OR the web
  dashboard) — is the MULTI-LINE/HEREDOC signature** (fixed 2026-07-21): a
  `python3 - <<'PY' … PY` monitor's raw newlines in `CLAUDE_MONITOR_CMD` never
  matched ps's escaped argv rendering (`$'\n'`/`\012`/`\n`), so the full-command
  disambiguation silently failed and find_proc fell to the sig alone; when the
  longest token was just the shared project path (`cd ~/proj && python3 …`) it
  multi-hit every session's shell there → "never found". find_proc now normalizes
  both sides escape-insensitively (`_norm_cmd`) — a recurrence means that
  normalization regressed (or a new ps escape form slipped past `_WS_ESC`). A `■ monitor failed` chip with no stream row is normal — a failed
  Monitor call closes inline, no tailer is spawned. Substream/codex AND (since
  2026-07-14) bg/fg/monitor streams
  ending `state-db-parked (session end)` (and codex `(before header)`) are the
  healthy quit-while-running shape — deliberately footer-less, NOT a lost block
  (before that date a bg/fg tailer outliving SessionEnd spun on, and its first
  post-park emit RECREATED an empty state DB at the live path — see the
  reuse-live-db tell under "Mirror came back empty" below).
- **Stream ended too early / output missing at the end** — check `errors` for
  "lsof failed — assuming writer still present" (transient lsof trouble is now
  survivable; a `writer-gone` end *without* such an error row and with the
  command demonstrably still running would be a new detection bug) and
  "lsof missing — writer-liveness disabled" (bg/fg completion is then backstop-only).
- **Frozen / missing / doubled pane** — `pane_events` first: an `open`/`toggle-on`
  with `ok=0` means the mirror (or the scoreboard bar — see detail) genuinely never
  opened; a resize whose detail shows an unchanged resulting width did nothing. Then
  cross-check `spawns` (was the renderer launched?) and `errors` (renderer crash).
- **Web-launched tab shows "▪ session" / "◧ cmd mirror" instead of the session's
  ai-generated title** *(inner-focus steal, fixed 2026-07-19)* — a background/web
  launch skips `--keep-focus` (it would raise the app — see docs/dashboard.md), so
  the last pane split in holds inner-tab focus and the tab title follows IT.
  `open_mirror` corrects this with an inner-tab `action first_window` right after
  opening; the tell is the session's `pane_events` **focus-host** row. `ok=1` =
  the correction landed (the tab should track the host's title); `ok=0` = the
  `kitten @ action` call failed (tab still mis-titled) — cross-check `errors`. NO
  `focus-host` row at all on a fresh web launch = `open_mirror` returned before the
  focus (no host `anchor`, or it opened no pane) — re-read the `open` row's detail.
- **Mirror vanished when entering the agents view / re-appeared in the wrong tab
  (2 mirrors, one empty)** *(daemon-origin SessionStart, fixed 2026-07-11)* —
  Claude Code's agents view (left arrow) spawns `claude daemon run --origin
  transient`, whose hook children carry a SCRUBBED env: no `KITTY_WINDOW_ID`, no
  `KITTY_LISTEN_ON` (the socket still resolves via the ppid walk, so pane calls
  work — anchorless). It fires (a) SessionStarts for the view's own agent
  sessions (`source=startup`, payload carries `agent_type`, sid has NO pane
  anywhere) and (b) a `source=resume` SessionStart for the real chat on re-entry.
  Pre-fix, the focused-tab fallback let (a) close the focused session's mirror as
  "stale" and vsplit an EMPTY mirror keyed to the phantom sid; (b) then shuffled
  panes wherever focus sat. Tells in the audit: a `sessions` row with NO
  `KITTY_WINDOW_ID` in `env` (and often no window id), a SessionStart whose tab
  transition says `not inside kitty / no remote-control socket` while pane
  `open` rows still succeed seconds apart across two sids, and (post-fix)
  `close-stale` rows naming the swept sid. On a current build (a) must produce
  ONLY an `open` row with `skipped: no host pane (daemon/headless session)` and
  (b) anchors to the `claude_session=<sid>`-tagged window; the `anomalies`
  **"stale-mirror sweep closed a LIVE session's mirror (pane hijack)"** section
  flags the regression directly (benign exception: sweeping a predecessor that
  crashed without SessionEnd in the same tab).
- **Mirror came back empty after `--resume`/`--continue`** — the `state_files` DB-fate
  row next to the SessionStart tells you what happened to the history: `restore-history`
  = it WAS restored (an empty pane then points at the renderer — check `spawns`/`errors`,
  and whether the restored DB's `ops` table actually has rows);
  `fresh-db` on a `source=resume` start = the parked history was missing (prior
  SessionEnd never ran its `keep-history`, or the 7-day sweep ate it — check the prior
  session's `pane_events` close row and its `keep-history` state row). The `anomalies`
  command flags the `fresh-db`-on-resume case directly. **A specific reboot cause
  (fixed 2026-07-14):** before the durable park, SessionEnd renamed the DB to
  `<log>.state.db.keep` in `/tmp`; a **macOS reboot between SessionEnd and the
  `--resume` wiped `/tmp`**, dropping the `.keep`, so the resume started `fresh-db`
  with an empty mirror + zeroed scoreboard (the tell: a `keep-history` row at
  SessionEnd, then a `fresh-db` on the resume minutes later, with no park file left on
  disk and a reboot in between). Now the park lives under `~/.claude/baqylau-mirror-history/`
  (`core/paths.parked_db`), which survives a reboot — a current-build `fresh-db`-on-resume
  is NOT the reboot cause; look to a missing `keep-history` or the sweep instead.
  Pre-2026-07-04 builds always truncated on SessionStart — empty-on-resume there is the
  old design, not a bug. A **`reuse-live-db` on a resume with an EMPTY mirror**
  *(fixed 2026-07-14)* is the zombie-tailer shape: a background job silent
  across SessionEnd printed after the park, the still-running bg/fg tailer's
  emit recreated a fresh empty DB at the live path, and the resume trusted it —
  the real history sits in the park untouched. Tell: a `bg`/`fg` stream row
  whose `ended_at` is AFTER the SessionEnd's `keep-history` row with an
  end_reason other than `state-db-parked (session end)`; current builds exit
  with that reason before pumping, so the shape means a regression — the
  `anomalies` **"bg/fg tailer outlived the park (zombie recreated the state
  DB)"** section flags exactly this stream. A
  **`reuse-live-db` on a resume with the FULL prior mirror** paired with a
  `park-failed (kept live)` state row at the prior SessionEnd is NOT the zombie
  shape — the park itself failed (ENOSPC/EPERM — the paired `errors` row func
  `park_db (main move — DB kept live)` has the traceback) and the resume
  correctly reused the never-parked live DB. `restore-failed (park kept)` on
  the resume = the park exists but couldn't move back (`errors` func
  `decide_log_fate (restore move main)`); the history is safe in the park for
  the NEXT resume.
- **Scorebar/codex-watcher still running long after the session ended** — check
  that session's last `state_files` DB-fate row FIRST, then the DB file itself:
  a live state DB whose creation POSTDATES the `keep-history` park was
  **resurrected** by a poller's first write racing the park (the codex
  watcher's slow spawn losing to a fast SessionEnd was the CI-f10b shape:
  its lock claim recreated the DB, so `parked()` never fired and its stream
  row never ended). Since 2026-07-15 the watcher's lock claim is non-creating
  (`lock_acquire(create=False)`, `state.connect_existing` mode=rw) and it
  exits with end_reason `parked-before-start (no state DB)` — that reason on
  a codex-watcher row is the healthy recovered case, not a bug. Otherwise
  a `park-failed (kept live)`
  (since 2026-07-15; before that, a silent `keep-history` with the live DB
  still on disk) means SessionEnd could not move the state DB out, so the live
  path never vanished and `parked()` — the pollers' one exit signal — never
  fired. The paired `errors` row has the OSError. There is deliberately no
  poller backstop for this state; the audit row + the errwatch `⚠` chip are
  the surface. Kill the orphans / clear the disk, then remove or park the live
  DB by hand.
- **Cost/scoreboard/tab/mirror ALL frozen after a `--resume` — the session
  "works" but nothing updates** *(resume forked the sid, fixed 2026-07-11)* —
  Claude Code fired the `source=resume` SessionStart under the **old** sid (so
  the mirror/scorebar/pane tags keyed to it) while every subsequent hook event
  and OTEL datapoint carries a **new** sid that never got a SessionStart of its
  own (observed: 19a42746 → ebcecfcc; the new sid's `InstructionsLoaded` even
  preceded the old sid's SessionStart by a second). Tells: the old sid receives
  only `ConfigChange` after the resume; a sibling sid has heavy `hook_events`
  traffic (subscriber rows) but **no `sessions` row** and no SessionStart; its
  tab transitions all bail `skipped: not inside kitty / no remote-control
  socket` (the fork's hook processes also carry the scrubbed daemon env — no
  `KITTY_WINDOW_ID`); `bump-otel` rows for the old sid stop at the resume
  moment while `otel` datapoints continue under the new sid. On a current build
  the fork's first event ADOPTS the predecessor (`plugins/claude_code/adopt.py`
  via `dispatch.py`): look for the `state_files` `adopt` row + the
  `claude-hook.py` `adopt:` decision (see the state_files schema row above) —
  the state DB moves to the new sid's path (hardlink + atomic symlink swap
  since 2026-07-14, so the old path exists at every instant — a `parked()`
  poller or old-key writer can no longer race the move; symlinks at the old),
  panes are retagged, and the sessions row is written. A PARTIAL adoption (the
  `adopt` row's `moved` misses `db`, or `retagged` is short) now leaves
  `errors` rows under the NEW sid, funcs `adopt: move state db` /
  `adopt: symlink old path` / `adopt: tmp symlink cleanup` (the swap's
  `.adopt-tmp` scratch link could not be removed after a failed rename — a
  leftover `.adopt-tmp` file next to the state DB is this row's tell) /
  `adopt: retag window` / `adopt: frontend unavailable` — context carries the
  src/dst paths (or the pane var) plus the old sid, so which half failed reads
  directly off the row; a thin `moved`/`retagged` with NO such errors row is a
  pre-fix build (2026-07-14). The `anomalies` **"hook traffic
  under a sid with no sessions row (resume fork never adopted)"** section flags
  the regression directly — run it against the sid CARRYING the traffic, not
  the frozen one. The tab-side half is `tabstatus._ensure_win` (falls back to
  the `claude_session=<sid>`-tagged window when `KITTY_WINDOW_ID` is absent) +
  `frontends.get(resolve=True)`; a current-build session whose transitions
  still say "not inside kitty" despite a tagged window means THAT fallback
  regressed. Pre-fix sessions: the fork's spend is intact in the new sid's
  state DB and `otel` rows (`bin/claude-audit.py otel <new-sid>`), just never
  displayed.
- **Toggling one session's mirror toggles ANOTHER session's mirror (two sessions
  in the same directory)** *(mis-adoption / pane theft, fixed 2026-07-13)* — a NEW
  independent session wrongly adopted a *concurrent* live session that shared its
  cwd, moving that predecessor's `claude_mirror`/`claude_scorebar`/`claude_session`
  pane tags onto the new sid — so `claude_mirror=<new sid>` now resolves to a pane
  in the OTHER session's tab, and a toggle from the new session's real pane operates
  there. Root cause: `InstructionsLoaded` fires ~100ms BEFORE `SessionStart`, and
  adopt.py only marked the sid on SessionStart, so the pre-SessionStart
  `InstructionsLoaded` reached `_maybe_adopt` with `sid_seen` false, no state DB yet,
  and consumed the other session's cwd-keyed `adopt_pending` note. Tells: a
  `state_files` `adopt` row (`from` = the OTHER live session) whose `hook_events`
  `adopt:` decision is carried by an **`InstructionsLoaded`** event, AND the adopting
  sid ALSO has its own `SessionStart` (a genuine fork never does) and a real
  `KITTY_WINDOW_ID` in `sessions.env`; live kitty (`kitten @ ls`) shows the adopting
  sid's `claude_session` tag on TWO windows in two tabs. The `anomalies` **"adopted a
  predecessor despite having its OWN SessionStart (mis-adoption — pane theft)"**
  section flags it directly. Distinct from the resume-fork shape above — there the
  adopting sid has NO SessionStart of its own and the predecessor genuinely stopped;
  here both sessions are live and independent. Manual recovery: retag the real pane
  or restart the mis-adopted session (its state DB is already the merged one).
- **Wrong scoreboard COST/TOKENS** — cost/tokens are OTEL-authoritative now. Start
  from `python3 bin/claude-audit.py otel <sid>`: the raw `otel` datapoints ARE the ground
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
  Σ-short) only apply to a FALLBACK fold or a pre-migration session. A recorded total
  slightly UNDER `/cost` on a session that just ended can also be a dropped STRAGGLER:
  an export that arrived after the park is dropped by design (counters are final) —
  the `state_files` `drop-otel-parked` row carries the exact deltas + raw datapoints
  that were dropped (since 2026-07-15; before that a straggler vanished with no audit
  row at all). A short total on a LIVE session can be the connect-failure sibling:
  `drop-otel-noconn` rows (state DB present but unconnectable) carry exactly the
  deltas that never landed — sum them to reconcile the gap.
- **No token/Σ breakdown at all despite OTEL data present** *(receiver stranded on a
  parked inode, fixed 2026-07-11)* — the scorebar's `Σ`/cost row is blank (or frozen at
  a stale value) even though `python3 bin/claude-audit.py otel <sid>` shows healthy datapoints
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
- **Codex mirror block missing events / rendered thin** — the rollout tailer drops
  complete-but-unparseable JSONL lines with a flood-capped audit: ONE `errors` row
  per run, func `codex rollout parse` (src path + byte offset + a 200-char snippet
  of the FIRST bad line), and the total count stamped onto the `streams`
  end_reason as a `· malformed-lines:N` suffix (e.g. `task-complete ·
  malformed-lines:37`). A codex stream with that suffix = codex's rollout format
  drifted (or a foreign writer corrupted the file) — the snippet in the errors row
  says which. No suffix and no errors row = every line parsed; the thin render is
  a rendering decision, not a parse drop. Related degrade rows: `errors` func
  `codex claims_db makedirs` (the per-repo claims dir couldn't be created — claim
  coordination will fail with the path named) and `otel gzip decompress` (an OTLP
  export claimed gzip but wouldn't gunzip — the receiver audits
  content-encoding + byte count, degrades that POST to an empty batch, and still
  answers 200; repeated rows = a broken exporter, not receiver flapping).
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
  session_ids; the `anomalies` **"cross-session contamination (task_id/slot token
  under more than one sid)"** section flags it directly (streams.task_id and
  slots.marker_path, scoped to groups involving the queried sid). The usual cause
  is the documented per-project bg-detection cross-talk (two sessions in one
  directory); the benign exception is a codex run taken over from a DEAD session
  (`codex-claim` `steal-stale`), which legitimately streams under the new sid.
- **Duplicated block/lines in the mirror** *(fixed 2026-07-04)* — tailers used an
  unbounded `read()` with `pos = size`, so bytes appended during the read were
  re-read next poll. The `anomalies` **"duplicated mirror ops (identical block
  lines painted twice within 5s)"** section flags the tell directly (identical
  long `gut` ops seconds apart — `DUP_OPS_WINDOW_S`); a non-empty row on a
  current build is the re-read regression, though an identical long line a
  command REALLY printed twice within the window can false-positive — read the
  op text before concluding.
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
  in the `otel` table (verify: `python3 bin/claude-audit.py otel <sid>` shows a non-trivial
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
  decision), or a `txpos` still short of EOF after it, is the regression — the
  `anomalies` **"SessionEnd fired but the stop-fold never ran"** section flags the
  wiring-dropped case directly (scoped to sessions with no `bump-otel` rows, where
  the missing fallback means the cost was silently lost; with OTEL data the cost
  is intact and only the decision row is missing). Note the
  hidden-summarizer gap (below) is a SEPARATE, larger, unrecoverable cause of the same
  symptom — rule it out via the `SubagentStop without SubagentStart` anomaly first.

- **Session hit its account rate limit but never migrated (or the account pill
  never showed "limit hit")** *(rate-limit migration, docs/relimit.md, since
  2026-07-19)* — the trigger is a MAIN-session `StopFailure` whose payload
  carries `error="rate_limit"` (`hook_events`, handler `claude-relimit.py`).
  Read its **decision** first — every skip path names itself: `no live state
  DB` (unhosted/headless), `migration off (CLAUDE_RELIMIT=0)`, `cooldown`
  (a second limit within 600s of an attempt — deliberate anti-ping-pong),
  `no hosted tab`, `no fallback account under 90%` (NO account can serve ANY
  rung of the fable→opus→sonnet ladder under 90% — every candidate is over the
  ceiling or its `limit-hit` stamp bars that rung). The go decision names the
  target + effective %, and on a downgrade appends `downgrading <cur>→<rung>`
  (e.g. `downgrading fable→opus`) — that's the tell the session dropped a model
  rather than switched account-only. No relimit
  decision row at all = the StopFailure never carried `error="rate_limit"`
  (check the subscriber row's payload) or the dispatch route regressed
  (`test_plan_sequences_pinned`). The pill's truth is the `limit-hit` kv
  (stamped even when migration is skipped; audited as a `state_files`
  `action='limit-hit'` row) — pill missing with the row present points at
  `/api/accounts` (`sessionapi.limit_hit_active`: an expired stamp is
  deliberately dropped). A MANUAL migrate (the header's ⇆ button) leaves NO
  relimit decision row — its trail starts at the `web-migrate` state_files row
  (ok/from/to/**model**, or the `no target`/`no terminal` reject) and continues
  in the same `relimit` stream (ctx/`relimit-launch` carry `mode: manual` and
  the chosen **`model`**). Both paths now walk the same fable→opus→sonnet ladder
  (`account.pick_target(cur_slug, cur_model)`, `sessionapi.model_available` per
  rung), so a `no target`/`no fallback account` means NO account can serve any
  rung: check each account's `state_files` `limit-hit` content — an
  ACCOUNT-WIDE stamp (`model: null`) bars every rung, a model-scoped stamp bars
  only its own family, and an over-ceiling 5h bars the automatic path (manual
  drops the ceiling). A downgrade landing on the WRONG model = the picker chose
  a rung whose account was mis-read: cross the `relimit-launch`/`web-migrate`
  `model` field against the accounts' `limit-hit` scopes and 5h `usage`. The
  chip
  on the WRONG account = the stamp's own `slug` field vs the session's
  `account` kv (after a migration the adopted session's DB carries the OLD
  account's stamp under the NEW account — `account_usage` must file by the
  stamp's slug; compare the `state_files` `limit-hit` content's slug with the
  pill showing it). A usage bar stuck at `resets now` = a stale snapshot
  served raw — `/api/accounts` serves `sessionapi.effective_usage` (ANY
  window — the 5h/7d pair or a model-scoped one like `seven_day_fable` —
  whose reset passed is zeroed, reset dropped); the raw stash is still
  readable in the session's state-DB `usage` kv for comparison. A MISSING
  per-model bar (e.g. no "7d fable" despite the CLI's /usage screen showing
  a Fable cap): the statusline never carries it (as of CLI 2.1.215 only
  `five_hour`/`seven_day` — `statusline.parse_usage` is generic, but if the
  key isn't in the raw `usage` kv Claude Code never sent it); the bar comes
  from the OAuth fetch (`plugins/claude_code/model_usage.py`), which attaches
  by matching the endpoint's 7d reset epoch against each slug's captured
  snapshot (5h only breaks a 7d tie — requiring 5h always was the 2026-07-20
  first-start-missing-bar bug). An attach failure writes an `errors` row func
  `model_usage._slug_for` (once per process; context lists the tie
  candidates); other funcs `model_usage.*` = keychain/refresh/endpoint
  failures. No errors row + no bar = no matching snapshot at all (the account
  needs one status-line capture within the 7d window) or no full-scope
  keychain login for that account. Note the pill interplay: a model-scoped
  `limit-hit` stamp is DROPPED from `/api/accounts` while the live fetched
  `seven_day_<model>` reads below 100% (mid-week reset override,
  dashboard-presentation only). The stamp also
  carries `model` (`relimit.limit_model` — `fable` for a model-scoped limit,
  null for account-wide): chip says `fable limit hit`, and the new-session
  auto-picker skips the account only for that model — a wrong/missing scope
  traces to the stamp's `msg` field vs the parse (the `state_files`
  `limit-hit` row has both).
- **Migration started but the session never came back** — the `streams` row
  kind `relimit` names the failed leg via `end_reason`: `close-failed` /
  `close-timeout` (the tab wouldn't close or SessionEnd never parked the state
  DB — the migrator then deliberately does NOT launch), `window-gone` (tab
  vanished while the DB stayed live — bailed), `launch-failed` (kitten refused
  the tab; the `relimit-launch` state_files row has `ok: false`). A `launched`
  end with no later SessionStart under the sid = the relaunch died inside the
  login shell (bad alias, keychain prompt, `claude` not on PATH) — the canned
  anomaly "rate-limit migration incomplete" flags both cases; from there it's
  outside the audit's sight (check the new tab's shell by hand).
- **"I couldn't submit my answer to the web ask card"** — look at the
  `web-answer` `state_files` rows (+ paired `errors` func `dashboard answer
  (<step>)`). `ok: false, step: cursor, detail: "cursor never reached Chat
  row"` on a PREVIEW-layout ask (options carry `preview`) was the 2026-07-20
  bug: "Chat about this" is the row BELOW the last option, and when the cursor
  reaches it the preview layout renders `❯` on BOTH the last option AND Chat (a
  highlight bleed) — `_cursor_to` read only the FIRST mark (the option) and
  dead-looped. FIXED: `_cursor_to` now matches the target against ANY cursored
  row. A preview question's TYPED answer is routed through "Chat about this"
  (a `web-answer chat:true` + a `web-send via: ask-chat` delivering the text);
  `_require_type_row` stays a fast-fail (`step: type`) for the free-text path
  the card no longer takes on preview. So a fresh `step: cursor (Chat row)`
  today = a genuinely NEW layout drift (Claude Code changed the dialog again —
  re-probe it live, arrows only, and re-check the two-`❯` assumption);
  `step: open` = answered/declined in the terminal first. `ok: true` = a normal
  option select, or a chat/typed-via-chat answer that drove cleanly.
- **"my message / answer never appeared in the session"** — three distinct
  causes, tell them apart by the rows: (a) a `web-send blocked: modal, ok:
  false` = the send was REFUSED because an ask/plan dialog was up (answer the
  card first — a pre-fix build would have pasted it INTO the dialog and lost
  it); (b) a `web-send tab: thinking|working|executing, ok: true` = it QUEUED
  in the TUI and delivers at the turn boundary (the ⧗ chip is now persisted in
  the `composer-queue` kv so a reload keeps showing it — a pre-fix reload lost
  the chip, reading as "gone even from the queue"); (c) an AskUserQuestion
  ANSWER not showing in the mirror feed was a RENDER gap (the answer is a
  tool_result, dropped by the conversation stream) — fixed by surfacing it as
  an `answer` record, no audit row (it's a read-side transcript render).
- **"the draft didn't clear after I sent"** — the `composer-draft` rows: a
  `stale` row around the send = the debounced save/clear reordered over the
  tunnel and the `seq` guard dropped the loser (working as intended); a MISSING
  `clear` row (only `write`s) = the clear POST never landed (network/JS).

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table),
(3) the code path responsible (file + mechanism), (4) a suggested fix. If the
evidence is inconclusive, say exactly which signal is missing and what extra
instrumentation would capture it next time.
