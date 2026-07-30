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
| `hook_events` | hook invocation | hook, tool_name, agent_id ('' = main session), handler (script), **decision** (what the handler chose to do), **payload** (full hook stdin JSON, verbatim). Since the single-dispatcher refactor, **all hook events are wired to one entry (`claude-hook.py` → `plugins/claude_code/dispatch.py`)** which fans out in-process; `handler` is NOT argv[0] (that would be `claude-hook.py` for everything) but an explicit per-subsystem override the dispatcher stamps (`audit.set_handler`), so the vocabulary below is unchanged (`claude-cmd-fmt.py`, `claude-tab-status.py` transitions, etc.). The universal subscriber row (handler = 'subscriber', empty decision) is now written **in-process by the dispatcher** at the end of `route()` rather than by a separate async settings entry — same row, same coverage. **New tell:** a `subscriber` row for an event that SHOULD have a functional handler row (e.g. a `PostToolUse`+`Bash` with a `subscriber` row but no `claude-cmd-fmt.py` decision row) = the dispatcher dropped/crashed that step — check `errors` for a `script='claude-cmd-fmt.py'` (or `script='dispatch'`) row. ALL hook events are recorded via the subscriber (handler = 'subscriber', empty decision) — incl. PermissionRequest/Denied, PostToolBatch, MessageDisplay, TeammateIdle, ConfigChange, CwdChanged, FileChanged, Elicitation\*, Setup, UserPromptExpansion, InstructionsLoaded — EXCEPT `WorktreeCreate`/`WorktreeRemove`, which are deliberately UNWIRED (since 2026-07-15): they are delegating hooks — registering one overrides Claude Code's native worktree creation and must print the worktree path, so the dispatcher's silent exit-0 failed every `EnterWorktree` ("hook succeeded but returned no worktree path"); a Worktree\* row in a pre-2026-07-15 session is the old (broken) wiring — on top of the mirror handlers' own decision-carrying rows for the events they process. So "did event X even fire?" is always answerable from the subscriber rows, and a handler row can be cross-checked against the subscriber's independent record. Since 2026-07-07 a `codex-session` handler also appears — the STANDALONE codex host's own SessionStart hook (`claude-codex-session.py`), the one `hook_events` row keyed to a *codex* session id rather than a Claude one; decisions: `standalone-open (<fate>, host_pid=N)`, `nested-skip (host mirror <sid> present)` (codex ran as a Claude subagent — that session's watcher already streams it), `no session_id`, `no usable frontend`. Since 2026-07-08, `claude-stop-fmt.py` also produces `stopfail: …` decisions (mirroring `claude-subagent-fmt.py`'s `stop: …` set — `done flag set, streamer will finalise` / `SAFETY NET footer …` / `no-op …` / `never started …`): a `StopFailure` carrying an `agent_id` is a subagent turn that DIED on an API error and fired no `SubagentStop`, so stop-fmt hands it to the shared subagent finaliser instead of ignoring it — the `stopfail:` prefix distinguishes this recovery from a normal `SubagentStop`. Since 2026-07-16 a `claude-file-fmt.py` `rendered: …` decision carries a `[scratch]`/`[out]` location tag when the file lies outside the session cwd (a session-scratchpad file / anywhere else) — it mirrors the painted display (`streamfmt.file_display`: ✎ icon / dim abbreviated dir); no tag = an in-project op, the unchanged bare-basename display. |
| `tab_transitions` | tab-colour decision | dispatch (raw arg: pretool/stop/bg-recheck/bg-watch/notify/escape-recheck/…), prev_state → new_state, applied (0 = skipped/bailed **or the kitten @ call failed** — reason then carries "kitten @ failed rc=N"), **reason**. Literal-state dispatches (SessionStart `idle`, SessionEnd `clear`) are sid-attributed since 2026-07; in older sessions those rows have `session_id=''`, so a per-sid query missed the final clear — the "tab left on a busy colour" anomaly can false-flag those old sessions. That anomaly's resting-state exclusion set also includes `awaiting-command` (red, the permission prompt — a session can legitimately sit on it, like green) since 2026-07-15. A STANDALONE CODEX host's transitions carry a codex-prefixed `dispatch` label (`codex-thinking`/`codex-pretool`/`codex-posttool`/`codex-permission`/`codex-compact`/`codex-stop`/`codex-subagent`/`codex-interrupt`/`codex-clear`), so `dispatch LIKE 'codex%'` selects them |
| `slots` | palette/liveness-slot event (rows of the session state DB's `live` table — were marker files) | kind (bg/monitor/fg/sub), slot_n, agent_id, owner_pid, action (claim/claim-id/**claim-pid**/steal-stale/**release-stale**/claim-denied/release/release-id/**release-pid**/set-owner), marker_path (now an opaque `<log>::live:<kind>.<key>` token). `steal-stale` is an ACQUISITION (the anomaly's claim/release pairing counts it as a claim since 2026-07-15); each steal is preceded by a synthesized **`release-stale`** row for the displaced DEAD holder (owner_pid = the dead pid), so a healthy steal balances — pre-2026-07-15 sessions lack release-stale rows and can flag "claims without a release" on every steal (historical, not a live bug). To see the CURRENT slot state: `sqlite3 /tmp/claude-mirror-<sid>.log.state.db "SELECT * FROM live"` |
| `streams` | detached tailer/streamer/watcher | kind (fg/bg/monitor/subagent/teammate/codex/codex-watcher/**bg-watch/interrupt-watch/codex-interrupt-watch/relimit**), **agent_id** (since 2026-07-27 this is MEANINGFUL ON `bg`/`fg`/`monitor` TOO — the agent that launched a nested job, stamped via `hookkit.stream_env(agent=)` → `CLAUDE_STREAM_AGENT`; '' = the LEAD's own. Before that date every nested tailer recorded '' whoever launched it, which is why a parked session's Jobs/Monitors tabs cannot be partitioned from the row alone — the read model falls back to the launch `hook_events` payload, `sessionapi.nested_owners`, and the *nested job/monitor with no resolvable owner* anomaly flags a task neither source can attribute), task_id, src_path, pid, started_at/ended_at, **end_reason** (writer-gone/sentinel/stop-sentinel/stoppedByUser/**parent-task-resolved**/converted-ctrl-b/backstop-timeout/crash/state-moved-on/cleared-to-green/killed-or-crashed/state-db-parked/**parked-before-start (no state DB)**/…), lines_emitted. `parked-before-start (no state DB)` (codex-watcher) = the session parked before the watcher's first state-DB write (slow spawn vs fast SessionEnd); the watcher exits immediately without creating anything — healthy, not a hang. A `codex` end_reason may carry a `· malformed-lines:N` suffix — the run's count of complete-but-unparseable rollout lines (first one has a full `errors` row, func `codex rollout parse`; the rest are only counted — flood-capped by design). `parent-task-resolved` (subagent/teammate) = a REJECTED/abandoned Task recovered via the parent transcript's `tool_result` (no `SubagentStop`, no `stoppedByUser` ever fired) — the streamer keyed on the agent's `meta.json` `toolUseId`; `… (rejected)` when that result was `is_error`. NB an ASYNC (background) agent's Task resolves the parent `tool_result` IMMEDIATELY with a synthetic *"Async agent launched successfully"* ack (`is_error` absent) meaning launched-not-finished — `parent_tool_result()` ignores that ack (else the streamer ended ~2s in with `lines_emitted=0` and the agent's whole transcript never rendered; the `async launch-ack ended the substream early` anomaly flags a `parent-task-resolved`/0-lines stream whose agent later got a real `SubagentStop`). It pairs with a `SubagentStart without SubagentStop` (that anomaly still fires — Claude Code emitted no stop — but the stream properly ENDED, so it is the RECOVERED case, not a hang). A `fg` stream with `.subfg.<tid>.out` in `src_path` is a SUBAGENT's foreground command tailed live (spawned by `claude-substream.py`), not a main-session fg command. `output-file-not-found` on an `fg` stream = the command's output file (its own redirect target, or the tee file) never appeared before the command finished; since 2026-07-15 the fg tailer waits on command LIVENESS (the PostToolUse outcome hand-off, `wait_fg_src`) rather than the flat `FIND_S`/`CLAUDE_STREAM_FIND_S` (~12 s) deadline `bg` uses — so a late-created redirect target (`sleep 45; cmd > out`, a retry loop) no longer flips the tab off blue mid-command. A pre-fix (or regressed) `fg` `output-file-not-found` whose command's `PostToolUse` fired AFTER the stream ended is the bug (the `fg tailer gave up on a late redirect target` anomaly); a genuinely fileless command ends after its Post. An open row from a dead pid = the watcher/tailer died — for bg-watch that IS the stuck-blue bug. A `codex-watcher` whose `src_path` starts `standalone:` is a STANDALONE codex host manager (spawned with a `HOST_PID`): it streams only its own session's rollout and owns teardown when the codex process dies (the codex analogue of SessionEnd — see the standalone shape below). Since the OTEL cost pipeline, a `kind='otlp'` row is the GLOBAL (per-machine, not per-session) OTLP metrics receiver — `session_id='otlp-receiver'`, `src_path='127.0.0.1:<port>'`; it outlives individual sessions and idle-exits, so an OPEN otlp row while it runs is NORMAL (like a live codex-watcher), and a `duplicate (…)` end_reason is a second receiver that correctly lost the singleton guard, not a bug. A `kind='dashboard'` row (session_id `''`, `src_path='http://127.0.0.1:<port>'`) is the web-dashboard server (docs/dashboard.md) — also global and long-lived, so an OPEN row while it serves is NORMAL; `end_reason` `stopped` = clean CLI stop/SIGTERM, `port-busy` = the second-guard bind failed (paired `errors` row carries the port), `crash` = the serve loop died (traceback in `errors`); "dashboard not reachable / toasts stopped" with NO open dashboard row = the server isn't running (`bin/claude-dashboard.py status`), and request-level failures audit as `errors` rows with func `dashboard request` (the path is in context) or `dashboard notifier` (the toast watcher's poll — its failure backs off 5s, it never spin-audits) |
| `ops` | paint op written to the mirror log | producer (script), op (the JSON paint op — full pane reconstruction, survives SessionEnd; a `src` field inside the JSON = producer-source stamp `sub:<agent_id>`/`team:<agent_id>`/`codex:<aid>` — absent means the main session's own op. The PREFIX is the REGISTER, and since 2026-07-31 the codex watcher mints `sub:` too: a codex SIDECAR (a run inside a Claude host) is `codex:<codex_aid>`, a codex-NATIVE SUBAGENT (`spawn(subagent=True)` from a standalone host) is `sub:<codex_aid>` — it is a child agent and classifies/folds as one, so `codex:` on a *subagent's* ops means either pre-2026-07-31 history or a watcher that did not pass `subagent=True`. A `chrome` field = the HOST's scaffolding around a child's stream (run banner, `⚙ model · effort`, run footer, the lead's `▶ <type> · <desc>` launch header): the terminal paints it, EVERY web view drops it, so "the terminal shows a banner the web doesn't" is this flag working, not a bug; the web dashboard drops stamped ops, the terminal paints all, so "block on terminal but missing on web" is answered by this field; beside it, `who` = the producing agent's NAME and `tags` = its model/ctx chips, both FIELDS since 2026-07-27 rather than text baked into `s` — the terminal composes them at paint time (`core/streamfmt.compose`), the web's agent scope drops them. An op with the name inside `s` and no `who` key is PRE-FIELD history: agent scope undoes that read-side, but a body line there keeps its tags (docs/dashboard.md *Agent scope*, "Known gaps in history"), so "the scoped mirror still shows the agent name / tags" is answered by whether these keys exist) |
| `errors` | swallowed exception | script, func, **traceback** (full), context (JSON of args in hand). **`func='dashboard prefs <get\|set\|mutate\|connect>'`** (since 2026-07-25) = the durable GLOBAL dashboard prefs store (`dashboard/prefs.py`, `~/.claude/baqylau-dash-prefs.db` — the new-session prefs/drafts, hidden dirs, notify mutes, the global alerts switch, push subs + VAPID keys, the `renamed-title` override) swallowed a failure; context carries the kv `key` + `db` path. These were fully SILENT before, which is why "the toggle didn't stick" was undebuggable: `mutate_map` returns the *intended* map on failure, so the handler still answers `ok` and writes an `ok:True` `web-*` row. WRITE failures (`set`/`mutate`) are audited every time; READ failures (`get`/`connect` on a read) at most ONCE per (operation, key) per process — reads run on nearly every request/SSE tick and a `session_id=''` row lights errwatch's `⚠ global:` chip in EVERY session's scorebar (errwatch's own audit-at-most-once reasoning), so a single `get` row means that key has been unreadable since its timestamp, not that it failed once. **`func='webpush keypair (corrupt record — regenerating)'`** = the stored VAPID keypair was unloadable and a NEW one was generated, which silently orphans every existing push subscription (context carries the `subs` count that just went dead) |
| `spawns` | detached process launch | parent_script, child_pid, argv, purpose. Since 2026-07-15 the tab-status recovery watchers spawn through this too: purpose `watcher:bg-watch` / `watcher:interrupt-watch`; a FAILED watcher spawn is an `errors` row func `spawn claude-tab-status.py` — no spawn row AND no such error row = the watcher was genuinely never requested (before this, a failed spawn was indistinguishable from never-requested). The codex watcher's purposes name the REGISTER it spawned a run in: `stream:codex <label>` (a sidecar / a standalone host's own run) vs **`stream:codex-subagent <label>`** (a native child agent) — the one place the audit records which register a run got, since the env that carries it (`CLAUDE_CODEX_SUBAGENT`) is not itself a column |
| `state_files` | coordination-file transition | path, action (write/remove/remove-stale/**copy/bump/bump-agent/bump-transcript/msg-transitions (the ✉ tracker's inbox diff — content `events`: per event `kind` new|read, `from`/`to`, `summary`, `msg_id`, and `chars` = the LENGTH of the message body that event painted, never the body; plus `now` = the cumulative delivered/read)/resume/final/reconcile/keep-history/restore-history/reuse-live-db/fresh-db/web-send/web-command/web-command-confirm/web-rename/web-stop (content `phase`: `attempt` before close_tab, `done` after with `ok` — a lone `attempt` = the close hung)/web-interrupt (content `phase`: absent on the press row — which carries `ok`/`tab`/`attempts`/`stopped`/`drained`/`probes` — and `restore` on the SECOND row an interrupt writes when the input box came back holding the message it just took back, `restored: true/false`; see the take-back bug shape. **`drained`** names the transcript tell that STOPPED the re-press loop — `"dequeue"`/`"queued_command"` = Claude Code drained its message queue, i.e. the turn boundary happened and the queued prompt is now running; `""` = the loop stopped on a static screen instead. A multi-press row with `drained: ""` taken while a message was queued is the "my queued message vanished when I hit stop" shape; a CODEX web-interrupt press row INSTEAD carries `host:'codex'` + `status` + `cid` + `verified` (a `turn_aborted` RECORD was matched in the rollout — codex fires NO Stop hook, so that record IS the confirmation, not a screen delta) + `steered` (a queued prompt was delivered on the abort → a new turn owns the tab, NOT a miss) + `tries`, and NO `probes`/`drained`/`stopped` — codex is a SINGLE Esc, no re-press loop, no take-back)/interrupt-probe/web-rewind (idle-only since 2026-07-25 — the mid-turn `mode: cancel-edit` fork is GONE with the ⊘ cancel button; a busy attempt is now an `ok:false` + `refused: "busy"` row)/web-rewind-to/web-answer/ask-pending/ask-draft/**compacting** (the compaction latch behind the dashboard's animated ctx bar — content `{action: write|remove, trigger: manual|auto}` and, on the remove, `took_s`. `plugins/claude_code/compact_fmt.py` writes it on PreCompact and deletes it on PostCompact; the PAIR is the only record of how long a compaction took, since compaction emits no tool call, no reply and no transcript growth in between. A `write` with no later `remove` = the compaction never finished — see the bug shape. Since P4 a STANDALONE CODEX host writes the identical row (`plugins/codex/facets.py`, routed by the codex dispatcher — same key, same content shape, deliberately so a row reads the same whichever host produced it); tell the two apart by the session, or by the paired `hook_events` handler (`claude-codex-hook.py` vs `claude-compact-fmt.py`). A NESTED codex run must produce NO such row — if one appears on a CLAUDE session's state DB with a codex handler, the standalone gate leaked)/composer-draft/composer-queue/web-viewmode (the session's web mirror DENSITY — verbose|default|focus, docs/dashboard.md *View modes*: content `{sid, mode}`, a durable `dashboard/prefs.py` write with NO terminal or session-state effect, so it explains a web stream that shows FEWER blocks than the terminal one)/web-taskshide (the pinned tasks CARD dismissed — content `{sid, hidden, ids}`, another durable `dashboard/prefs.py` write with NO effect on any task: `ids` is the all-completed list the dismissal covers, and it stops applying the moment the list gains an id, which is why the card re-appears with no un-hide gesture; explains a session whose tasks card is absent while its `tasks` writes say it has tasks)/web-hint/web-clientfail/web-reject (a `_post_guard` rejection BEFORE any handler — path=the rejected request path, content `{code, why}`: missing header / cross-origin / read-only / bad body; the trace a control POST left when it arrived but the guard bounced it; its INPUT-VALIDATION sibling is an `ok:False`+`why` row under the HANDLER'S OWN action — `_reject_input`, a bad/empty body field a handler ran and disliked, NOT an errors row — used by web-launch/web-command/hide-dir/notify-mute/web-dictate AND, since 2026-07-23, the session-scoped web-send/web-rename/web-upload/web-clipboard/web-rewind-to/composer-draft/composer-queue/web-hint/web-answer/ask-draft/web-plan/web-taskshide (previously those input rejects were a silent 4xx; now filed under the session via the helper's `sid=` channel, which resolves through `_audit_target` so a handler's reject row and its SUCCESS row can't land under different sids for a forked session — before 2026-07-25 they passed a re-derived `P.mirror_log(sid)` and carried no `path`); the stash-race 409s `no pending question`/`ask expired` deliberately stay row-less — legit when the dialog was answered at the terminal, covered by the ask-pending/plan-pending lifecycle)/**web-client** (the FRONTEND audit: one row per browser-side event the server can't see — content `{ev, client, device, t, …scalars, conn{online,view,es,conn}}` (`device` since 2026-07-24 — the stable per-DEVICE id on EVERY row, so any frontend event is attributable to a device; map it to a platform via the `boot` row's `dlabel`); `ev` is dotted: `<gesture>.begin`/`.ok`/`.fail` for a tagged control POST (close/send/command/interrupt/rename/migrate/rewind/rewind-to/answer/plan/new/resume-send), `notify.recv` (a notification toast SSE reached this device — `{kind, shown, vis, focus}`; `shown:false` = gated because you weren't looking here, the frontend bracket around the backend `notify-route`), `close.reconciled`, `sse.open`/`sse.drop` per stream, `js.error`/`js.reject`, `boot`/`hello`/`stale` (build lifecycle — `boot.build`≠`hello.boot` = stale cached JS), `meta.stuck`/`meta.resolved`/`meta.fail` (session-view load + launch tag-race), `launch.arm` (its **`pend: true`** = the waiting room was armed at the CLICK, with the POST still in flight — so this row lands BEFORE that launch's `new.begin`; an arm AFTER `new.ok` is the old dead-air ordering)/`launch.hit`/`launch.timeout`, `backlog.fail`, **`dictate.start`** (`{rate, native, arm_ms, open_ms, preroll_s}` — `rate` is what goes on the wire, 16000 unless the hardware is already lower; a `rate` equal to a 44100/48000 `native` means the worklet's resampler did NOT engage, the 768 kbps-uplink regression. `arm_ms` = press → CAPTURING, the wait the user actually feels; `open_ms` = press → socket open, the wait they used to feel; `preroll_s` = the speech held between the two, which would have been LOST before instant-on. A large `open_ms` is now normal and harmless — an `arm_ms` in the same range is the regression, i.e. capture went back to waiting on the connection) / **`dictate.lag`** (`{queue_s, svc_s, sent_s, buffered}`, one per 5s of an open mic — the ONLY evidence for "dictation is slow", since the server mints a token and never sees the audio: `queue_s` = seconds of audio stuck in the page's own ws send buffer (a saturated uplink, OURS, and it GROWS across the samples — that compounding is the tell), `svc_s` = seconds the network took that Deepgram hasn't accounted for against its own `Results` audio clock (THEIRS, roughly constant); they add up to the delay the user sees) / **`dictate.backlog`** (one-shot past `DICT_BACKLOG_WARN_S`, the toast's row) / **`dictate.stop`** (`{rate, spoke_s, max_queue_s, max_svc_s}` — the MAXIMA, so two dictations are comparable), all under `session_id=''` when the new-session form is the one dictating, `attach.paste` (`{n, resolved}` — a FILE paste; `resolved>0` = the host's pasteboard resolved them and their PATHS were spliced into the box (kitty parity), `resolved:0` = they were uploaded as attachments instead (a screenshot, or a device whose clipboard isn't the host's); pair it with the server's `web-clipboard` row, which shows what was ASKED but not which branch the page took); a `close.begin` with no `.ok`/`.fail` = the /stop left the page but no response came (tunnel/upstream drop), and recurring `js.error` with NO `close.begin` = the ✕ handler threw before closeSession (the uninitialized-S.closePend bug); scoped to the event's own sid, blank sid = a boot/launch)/web-plan/plan-pending/tasks/**tasks-dir** (the task-dir KEY-DRIFT pin, since 2026-07-30 — content `{action: "pin", dir, sid_dir, task_id, subject}` or `{action: "unpin", dir, sid_dir}`: a resumed Claude Code process keys the on-disk task dir by a FRESH internal id, not the sid, so `task_fmt.resolve_dir` picks the dir holding the FRESHEST copy of the event's own task — recency, not candidate order, since a TaskUpdate probes by integer id alone — pinning a sibling-scan hit in the `tasks-dir` kv and un-pinning on a fresh sid-dir win; see the web-tasks key-drift bug shape)/**memory** (a memory-wiki op reached the Memory tab's kv — the action column is the kv KEY, and the CONTENT's own `action` says which half: `write` = a note touched, `{verb, path, agent, notes}` (the running distinct-note count); `search` = a vault question asked, `{kind, sub, query, hits, agent, searches}` (`kind`/`sub` e.g. qmd/query, `hits` = result rows PARSED out of the command's output, `searches` = the running count, capped at `memory.SEARCH_MAX`). Written by BOTH planes — a Read/Write/Edit tool op via `file_fmt`/`substream_render`, and a shell command via `cmd_fmt`/`cmd_pre`'s `memcmd` plane, which is where most vault recall actually happens; no rows at all for a session that used the shell is the canned anomaly "Bash commands naming the memory wiki but NO memory records")/web-launch/web-launch-wake/web-launch-steal-watch/web-upload/ns-prefs/**ns-draft** (log/path-empty like `ns-prefs` — the new-session form's UNSENT first prompt, the GLOBAL sibling of `composer-draft` for the box that has no session yet, `dashboard/prefs.py` `new-session-draft` — one draft PER DIRECTORY, `{cwd: {text, seq}}` pruned to `NS_DRAFT_MAX` by recency; content `{action: write|clear|stale, cwd, chars, seq}`, the TEXT deliberately never recorded — `write` on every debounced edit + the close flush + the park half of a directory switch, `clear` when a launch consumed it, `stale` when that directory's wall-clock `seq` guard dropped a straggler; the switch itself is a FRONTEND `web-client` row, `ev=nsdraft.dir` `{from, to, carried, chars}`)/web-dictate/**web-clipboard** (a FILE paste asking the server to resolve the pasted basenames against the HOST's pasteboard — content `{sid, names, matched, paths}`; `matched>0` = the page pasted those paths instead of uploading, `matched:0` = no such file on the host's clipboard, so the page uploaded the bytes as an attachment)/session-paths/limit-hit/logged-out/relimit-pick/relimit-launch/web-migrate/web-copy/web-view/notify-mute/notify-global/telegram-notify/notify-suppress/notify-arm/notify-route/notify-retract/web-push**), content (state-DB records — path is a `state:` key: `state:fg-live` (the take-once in-flight-foreground-command hand-off behind the web's live ⏱ elapsed chip — `write` when the command starts, `remove`/`remove-own`/`remove-stale` when it ends; its `tid` IS the mirror block's copy-group id. For CLAUDE the writer is the PreToolUse hook `cmd_pre.py`, whose `tool_use_id` is that group. For a STANDALONE CODEX host, since P4, the writer is the ROLLOUT STREAM (`plugins/codex/facets.py fg_open`/`fg_close`), because codex's hook ids, its rollout `call_id`s and its block groups are three DISJOINT id spaces — a hook-stamped codex record would name no block, so if you ever see one, the chip is ticking on nothing. `pid` is the owning tailer's, and a dead pid IS the retirement for a command cancelled with no hook), `state:done:<token>`, `state:subfg:<tid>` (subagent live-fg tee hand-off: `write` by cmd-pre, `remove` when the substream consumes it), `state:agent.<id>`, and **proc-found** (path `monitor:<taskid>`, content the pid) = the monitor tailer latched its command process — the moment completion detection is keyed to a real pid, and **open** (path `tail:<taskid>`, content path + `pos0`) = a skip-existing tailer (Ctrl+B hand-off / `>>` append) adopted its start offset — for a "Ctrl+B block missing its first lines" report, compare `pos0` against the launcher-measured CLAUDE_STREAM_POS0 expectation (a pos0 larger than the hand-off-moment size = the old open-time measurement regressed); for bump\* actions: the scoreboard deltas + resulting totals — the trail for wrong-scoreboard-number bugs). **bump-otel** (path = the state DB file) = the OTLP receiver's aggregated per-POST write: content carries the summed `deltas` (`tk_*`/`cost`/`tokens`/`otel_cost:<query_source>`) + resulting `now` totals. This is the PRIMARY cost producer now (the raw datapoints behind it are in the `otel` table). **drop-otel-parked** (path = the state DB file) = a straggler OTLP export arrived for a session that had already PARKED: the receiver drops the deltas (never connects — a connect would recreate the DB whose existence is the session-alive signal) and this row carries the dropped `deltas` + raw datapoints verbatim (they are deliberately NOT written to the `otel` table, so `SUM(otel.value)` keeps equalling the live counters). **drop-otel-noconn** (path = the state DB file, since 2026-07-15) = the same audited drop for a connect FAILURE past the parked check (locked/perms/corrupt live DB): the row carries the dropped `deltas` + raw datapoints, nothing reaches the `otel` table — this drop was previously fully invisible (the SUM(otel)==counters invariant still held, so no anomaly could see it). **evict-parked** (path = the state DB file) = the receiver's per-batch/per-tick sweep closed its cached state-DB connection for a session that parked (`state.evict` — without it every ended session pinned a conn + WAL/SHM fds until the receiver's idle exit). **bump-agent** is now ONLY codex (its separate process can't export OTEL, so it keeps its own rollout fold); a Claude subagent no longer bump-agents (OTEL's `query_source=subagent` books it). **bump-agent** = an agent streamer's spend bump, `meta` carries agent_id/kind/model + the in/out/cache/create split that was priced (since 2026-07-08 also `create_1h`, the 1-hour-TTL cache-write share — it bills 2× input where 5m bills 1.25×, so re-pricing needs it) — attribution and re-pricing need no timestamp correlation; `meta.kind` is `subagent`/`teammate` (priced by `accounting.cost_usd`) or, since 2026-07-07, `codex` (a rollout run's cumulative `token_count` fold, priced by the codex plugin's own `CODEX_PRICES`; `meta.src` is the rollout path); **reconcile** (path = `state:agent.<id>`) = `claude-subagent-fmt.py` at SubagentStop folded the agent's transcript and recorded the residual over the `billed:<agent>` baseline. Since the OTEL pipeline it NO LONGER bumps counters (OTEL's `query_source=subagent` books agent spend live, including a crashed streamer's tail) — the row is now a pure OTEL-vs-transcript CROSS-CHECK (content: `residual`, `cost`, transcript `true` total). Idempotent — a clean finish leaves `true` == baseline, so no row. **bump-transcript** (the transcript fold) is now a FALLBACK ONLY — it fires from `claude-stop-fmt.py` on `SessionEnd` and ONLY when the OTLP receiver wrote nothing for the session (`otel_seen==0`: telemetry off / receiver down / machine without the env). In the normal path there are NO bump-transcript rows at all (OTEL owns cost); a bump-transcript row means the session ran without telemetry and the fold recovered it. It carries `d_split` (`tk_in`/`tk_out`/`tk_read`/`tk_create`) and `d_create_1h` alongside `d_tokens`/`d_cost`. A bump-transcript row AND bump-otel rows for the SAME session = the `otel_seen` gate broke (double-count regression — its own anomaly). The per-category counters live in the state DB (`SELECT key,val FROM counters WHERE key LIKE 'tk_%'`); `tk_in+tk_create+tk_out` == the billed `tokens` counter (which backs `cost`; no longer shown on the `▪` row), and `+tk_read` is the Σ total. Scorebar `paused`-only ticks are NOT audited (1/s noise; the total rides every other bump's `now`). **errseen** (path = the state DB file) = the audit WARNING LIGHT (`core/errwatch.py`, polled by the scorebar every 5s) advanced its last-seen `errors`-rowid checkpoint after emitting `⚠ audit:` mirror one-liners; content carries `last` (the rowid consumed up to) and `new` (how many rows that poll emitted — >3 were flood-collapsed into one CLI-pointer line). Since 2026-07-15 the light also surfaces GLOBAL `errors` rows (`session_id=''` — auditor-outage rows from `audit._connect`, pre-session/CLI errors): the chip count includes them, each is emitted as a `⚠ audit: global: <script>: …` one-liner (flood pointer targets `errors ''`), and their checkpoint is a SEPARATE per-session kv (`errseen-global`) whose advances land as `errseen` rows with `"global": true` in the content. Every session's scorebar shows the same global rows (an audit outage affects all sessions) — that's by design, not a duplication bug. Which errors ever reached the mirror, and whether one was shown twice or never, is reconstructible from these rows against the `errors` rowids. **resume/final** (path = `state:agent.<id>`) bracket each substream streamer: what checkpoint + dedup state it adopted (or `fresh: <why>`) and what it left behind — a successor's `resume` disagreeing with its predecessor's `final` is a broken handoff. **adopt** (path = the NEW sid's state DB file, since 2026-07-11) = sid-fork adoption (`plugins/claude_code/adopt.py`): a `--resume` whose SessionStart fired under the OLD sid — or a BACKGROUNDED session continuing under its background-job id — while every later event carries a NEW sid; the fork's first event moves the predecessor's state DB to the new sid's path (hardlink + atomic symlink swap since 2026-07-14 — the old path is never absent mid-move; symlinks left at the old paths) and retags the panes; content carries `from` (the old sid), `moved` (which of db/-wal/-shm moved) and `retagged` (which pane vars were re-pointed). It pairs with a `hook_events` decision row, handler `claude-hook.py`, decision `adopt: sid forked — adopted <old>` — the ONE functional decision that handler name carries (adoption is dispatcher plumbing, not a subsystem). The registry behind it (`sids` = sids whose OWN start was seen — marked on `SessionStart` AND the earlier-firing `InstructionsLoaded`, which a fork never emits, closing a TOCTOU where a new session's pre-SessionStart event adopted a concurrent same-cwd session; `adopt_pending` = the take-once cwd-keyed note every HOSTED SessionStart leaves (split.cmd_open)) lives in the global tab DB `/tmp/claude-kitty-tab.db`. **session-paths** (path = the NEW transcript path, since 2026-07-19) = the dispatcher's per-event `A.session_paths` refresh caught the sessions row's location columns going stale and folded the payload's values in: Claude Code RELOCATES the transcript when the session's cwd moves to another project dir (measured 2026-07-18 via EnterWorktree — the file moves to the worktree cwd's `projects/` slug dir); content carries `cwd`/`transcript_path` (new) + `cwd_old`/`transcript_path_old`, so every relocation moment is a visible row, not a silent UPDATE. agent_id events never write one (an isolated subagent's cwd is its OWN worktree — skipped by design), and an unchanged payload writes nothing, so a busy session has at most a handful. A session that entered a worktree with NO session-paths row = the refresh regressed (its anomaly: *"sessions row transcript_path stale vs latest hook payload"*). An `adopt` decision on a sid that ALSO has its own `SessionStart` is a MIS-adoption (a real independent session stole a same-cwd predecessor's panes) — its own anomaly, *"adopted a predecessor despite having its OWN SessionStart (mis-adoption — pane theft)"*. **keep-history/restore-history/reuse-live-db/fresh-db/park-failed (kept live)/restore-failed (park kept)** (path = the DURABLE park `~/.claude/baqylau-mirror-history/<sid>.state.db` since 2026-07-14 — `core/paths.parked_db`; older rows carry the in-place `<log>.state.db.keep`; content = the SessionStart `source`) trace the session state DB's lifecycle: SessionEnd MOVES the live `/tmp` DB out to that durable park (`keep-history`); SessionStart either restores it back to the live path (`restore-history`, resume of the same sid — honours a legacy in-place `.keep` too), leaves a live DB alone (`reuse-live-db`, compact or resume-after-crash), or starts fresh (`fresh-db`). The park is under `~/.claude`, NOT `/tmp`, precisely so a machine reboot (macOS wipes `/tmp`) between SessionEnd and a `--resume` can't drop the history and force a `fresh-db`. The state DB IS the mirror content (its `ops` table) — so these rows are the resume-history trail. Since 2026-07-15 the park FAILURE paths are audited instead of swallowed: **park-failed (kept live)** = the MAIN DB move failed at SessionEnd (paired `errors` row func `park_db (main move — DB kept live)`) — the live DB path persists, so the scorebar/codex-watcher pollers keep running as orphans and a same-sid resume sees `reuse-live-db`; **restore-failed (park kept)** = the resume's main move-back failed (`errors` func `decide_log_fate (restore move main)`) — the park stays intact, the session starts fresh. A sidecar-only park failure still logs `keep-history` but leaves an `errors` row func `park_db (sidecar move -wal/-shm)` (safe: park_db checkpoints the WAL — `wal_checkpoint(TRUNCATE)` — before moving, so the parked main file is self-contained). **copy** (path = the state DB file) = a ⧉ copy-link click handled by `claude-copy.py` — content carries `gid` (the block's copy-group id: the Bash tool_use_id or the backgroundTaskId), `what` (`cmd`/`out`) and `chars` (0 = the group held nothing of that type); every FAILED click lands in `errors` instead, func `copy (bad url)` / `copy (state DB gone — session over?)` / `copy (read ops)` / `copy (no clipboard tool)`. **web-copy** (path = the state DB file, since 2026-07-23) = the DASHBOARD twin of that click (`GET /api/session/<sid>/copy/<gid>/<what>`): the web page calls `core.copy.collect` DIRECTLY (browser owns the clipboard — no `to_clipboard`, no pane feedback), BYPASSING every `claude-copy.py` row above, so it writes its own — content `gid`/`what` (`cmd`/`out`/`all`)/`chars` (0 = nothing of that type); a gone session DB or a read failure lands `errors` func `dashboard copy (state DB gone|read ops)` and returns empty. **web-view** (path = the state DB file, since 2026-07-23) = the dashboard twin of the ⧉view toggle (`GET .../view/<gid>`, fires once per EXPAND — collapse is client-side): content `gid`/`ok` (`false` = no `view:<gid>` stash → 404, the same no-op the terminal shows). A web copy/expand with NO web-copy/web-view row at all = a pre-fix server (these were an audit blind spot before 2026-07-23 — the flow was fully traced in the terminal but silent on the web). **web-send** (path = the session's state DB file) = a dashboard CONTROL-PLANE message POST (`dashboard/server.py` `post_message`): content carries `win` (the kitty window typed into, `""` = headless → the row records a rejected attempt), `chars` (message length) and `ok` (did the send succeed — send_text is TWO kitten writes, the message then a gap-separated CR (`SEND_ENTER_GAP_S`, the split-Enter fix), and `ok` requires both), plus (since 2026-07-25) **`live`** + **`queued`** — the VERIFIED mid-turn verdict. `tab` is the raw colour; `queued` is what the PAGE was promised (and pins a ⧗ chip on), and a `QUEUE_TABS` colour alone can't promise it: Claude Code fires no hook on cancel, so a terminal-side Esc-Esc freezes the tab on magenta while the TUI sits idle. So `post_message` probes the screen first (`_turn_live` — two `get_text` captures `QUEUE_VERIFY_GAP_S` apart, the interrupt's marker-free screen-delta): `live: true` = still animating → `queued: true`; **`live: false` = STATIC, i.e. the colour was stale and the message went straight through → `queued: false`** (the row that proves a stale colour, and the fix for the stuck-chip shape below); `live: null` = settled tab (no probe) or an unreadable screen (the colour's verdict is kept, so a probe failure never loses a real queue). A `web-send` with NO `live` field = a pre-2026-07-25 server (restart `claude-dashboard.py`). Plus (since 2026-07-18) `clear_draft` — TRUE when the page resent an EDITED message after a mid-turn cancel-edit: the row's send first kills the restored draft (ctrl+u+ctrl+k — since 2026-07-29 PER LINE of the `tui-draft` stash, a backspace between kills joining lines, capped `DRAFT_CLEAR_LINES_MAX`; the row's **`draft_lines`** is the kill count — 0 = no clear ran, 1 on a body-flag-only clear, and a `draft_lines: 1` next to a multi-line take-back is a pre-2026-07-29 server, the glued-first-line shape) and delivers via `Frontend.paste_text` (a BRACKETED paste, not send_text — a raw send into the just-cleared input drops leading bytes, the measured mangle; the bracketed paste lands clean). A `web-send clear_draft:true` is the edit-and-resend-from-web path; garbled resends on an OLD build (no bracketed paste) are its regression. A failed/rejected attempt also lands an `errors` row (funcs `dashboard message (no terminal|send failed)`). **web-command** (path = the session's state DB file, since 2026-07-18) = a dashboard quick-command POST (`post_command` — the scoreboard's second action row: ⊜ compact / ✦ model / ⚡ effort): types the TUI's OWN slash command (`/compact`, `/model <arg>`, `/effort <arg>`) via the same bracketed paste+CR as a composer send; content carries `win`, `cmd`, `arg`, `ok` and `tab` (the tab state at send time — ∈ thinking/working/executing means the command QUEUED in the TUI and runs at the turn boundary; `tab: awaiting-command` with `ok: false` = refused because a modal dialog was up and pasted text would land IN the dialog). Off-vocabulary requests never paste — they land ONLY as an `errors` row func `dashboard command (bad cmd)` with the exact received bytes (repr); other failures pair funcs `dashboard command (no terminal|send failed)`. "I clicked model/effort/compact and nothing changed": `tab` in the row answers it — queued mid-turn (wait for the boundary), or check the transcript for the command's own record after an `ok: true` on an idle tab. **web-rename** (path = the session's state DB file, since 2026-07-18) = a dashboard rename POST (`post_rename` — the ✎ header button). TWO CHANNELS since 2026-07-29, named by the row's **`channel`**, and which one ran is the first thing to read: **`channel: "tui"`** (a LIVE session) pastes Claude Code's OWN `/rename <name>` into the window and writes NOTHING itself — content `win`, `chars`, `ok` (did the paste land), `tab`, **`queued`** (a busy tab: the command sits in the TUI's message queue and applies at the TURN BOUNDARY, so a title that hasn't changed yet is expected, not a lost rename), `clip` (the clipboard-image guard), `reason: "dialog open"` on the red-tab 409; a paste failure pairs an `errors` row func `dashboard rename (send failed)`. **`channel: "transcript"`** (a PARKED session — `win` is always `""`, deliberately NOT an error) appends the `agent-name` record itself (`plugins/claude_code/transcript.set_session_title`; the ONE control-plane session-state write) — content `chars`, `ok` (did the append land), `override` (was the durable prefs override recorded); an append failure pairs `errors` func `dashboard rename (append failed)`. Refusals BEFORE the branch carry `reason`: `no transcript` (no/missing transcript path recorded), `unsupported` (not a Claude `projects/` transcript — a codex standalone host's rollout, which must receive neither a record nor a typed command; its window carries the same `claude_session` tag, hence the pre-branch gate `plugins.renameable`). A row with NO `channel` field is a PRE-2026-07-29 server (it appended on both paths and set a sticky tab title — see the reverting-rename bug shape; `tab_retitled` is that build's field and is gone). **web-launch** (log/path empty — no session exists yet) = a dashboard new-session POST (`post_new_session`). A resume REFUSED before any tab files one too, named by `why`: `transcript missing` (410), `unsupported tool` (409, since 2026-07-30 — plus `tool` = the owning plugin's name, empty when NO plugin claims the transcript: `claude --resume <sid>` is a Claude argv and the sid may name another tool's conversation, e.g. a parked codex host's rollout, which would open a tab resuming nothing; the gate is `plugins.owns_by(transcript_path)` against `RESUME_TOOL`), or a live `win` (409, the duplicate-resume guard). Content carries `cwd`, `ok` (did `Frontend.launch_tab` succeed) and **`ms`** — the per-step latency map (`fe` frontend resolve / `row`+`livewin` a resume's transcript-row read + `kitten @ ls` scan / `front` `lsappinfo` frontmost / `clip` the osascript clipboard-image probe / `tab` `kitten @ launch` / `all` the total, milliseconds, each stamped as its step completes), which is what makes "the launch took N seconds" attributable to a STEP from the DB alone; the launched session shows up later via its own SessionStart, so there is no adopt/fork relationship to this row. **web-launch-steal-watch** (log/path empty, since 2026-07-18) = the PASSIVE macOS focus watch that follows every web launch whose frontend has an OS app id (`Frontend.app_id()`) and whose capture-time frontmost app wasn't the terminal: a ~30s daemon-thread watch (`dashboard/server.py steal_watch`) over the frontmost app (`lsappinfo`) that RECORDS each transition onto the terminal app and never touches focus; content carries `before` (the browser's bundle id at click time), `terminal`, and `steals` (seconds-into-watch of each takeover; `[]` = clean). The steal's root cause was fixed at the source the same day: the SessionStart pane opens passed kitty's `--keep-focus`, whose focus-restore raises the OS window whenever the app is in the background — `frontends/kitty.py launch_pane` now passes the flag only while kitty is frontmost (`kitten_app_focused`), so a non-empty `steals` on a current build = some launch path still activates the terminal, and the offsets name the second (compare against the startup sequence: tab launch ≈0s, mirror/scorebar opens ≈2-6s after claude boots). Historical: rows with action **web-launch-refocus** (2026-07-18 only) are from the reverted ACTIVE bounce-back variant, which `open -b`'d the browser back on every takeover — reverted because it cannot distinguish kitty stealing focus from the user deliberately switching to kitty, and yanked the user back; do not re-add it. "kitty jumps to the front when I start a session from the dashboard" → this row: missing entirely = the guard never armed (no app_id / terminal already frontmost / pre-fix server), `clean` while the user SAW the steal = the steal came slower than the watch window or from something else entirely, `bounced xREFOCUS_MAX` = the cap was hit (something kept stealing past the pane opens — worth a look at what), `activate failed` = the hand-back itself broke. NB kitty's `--keep-focus` launch flag is deliberately NOT used — on a background kitty it *causes* the steal (verified 2026-07-18; docs/dashboard.md *The focus-bounce guard*). **web-stop** (path = the session's state DB file) = a dashboard stop POST (`post_stop` — the CLOSE button): content carries `win` (the tab-owning window closed via `Frontend.close_tab`, `""` = refused: headless or the live `claude_session` tag was gone) and `ok`; a graceful close — Claude Code exits on the HUP and SessionEnd runs the normal lifecycle, so a `web-stop ok: true` should be followed by that session's park/`sessions.ended_at` rows (missing = the HUP didn't reach the TUI). **web-interrupt** (path = the session's state DB file) = a dashboard interrupt POST (`post_interrupt` — the STOP button / the page's Esc key): an Escape key EVENT via `Frontend.send_key` (send-text bytes would bypass the kitty keyboard protocol); content carries `win`, `ok`, `tab` (the tab state at press time — what the Escape landed on; note kitty's send-key reports no per-window delivery errors, so `ok: true` only says kitty accepted the call) and, since 2026-07-24, **`attempts`** + **`stopped`** + **`probes`** — the ROBUST VERIFY-AND-RE-PRESS trail. ROOT CAUSE (found 2026-07-24): a single Esc does NOT reliably stop a busy turn — send-key is ~2/3 reliable AND with the user's `editorMode: vim` the input is modal, so during the THINKING phase the first Esc only leaves INSERT mode (`-- INSERT --`) and never reaches the interrupt handler (proof: every single-Esc interrupt on a `thinking` tab ran to natural `Stop`; a mid-STREAM Esc landed; cancel-edit's TWO Escapes are reliable because the 1st exits INSERT, the 2nd interrupts). So the endpoint re-presses Esc WHILE the turn is still LIVE, where live = the screen is still CHANGING between two `get_text` captures INTERRUPT_RETRY_S apart (robust across thinking levels — NO marker string). `stopped: true` = the screen went static (dead/interrupted), `false` = it kept animating after every re-press so the Esc never landed (endpoint returns 502 + spawns NO escape-recheck — flipping green would mask a live turn; paired `errors` func `dashboard interrupt (not stopped)`), `null` = idle press / unreadable. `attempts` counts Escapes sent; `probes` is the per-capture phase snapshot list (`at`/`insert`/`toks`/`spin`/`tail`), cross-referenced with the `interrupt-probe` rows. A `web-interrupt stopped:false` IS the "stop did nothing, the turn kept running" bug — read `probes` for the phase (insert/thinking/streaming) it was stuck in. The session must stay up: a `web-interrupt` followed by the session ENDING is the tell that Esc landed somewhere unintended. **web-rewind** (path = the session's state DB file) = a dashboard rewind POST (`post_rewind` — the ↶ button / a rapid double Esc on the page), whose `content.mode` mirrors Claude Code's state-dependent double-Esc: `rewind` (IDLE tab) TYPES `/rewind` via send_text (documented identical to double-Esc; synthesized double-press key events measured only ~2/3 reliable at any gap, typed command 100%) — NO Escape, so NO escape-recheck spawn; `cancel-edit` (a BUSY tab — thinking/working/executing/awaiting-bg/awaiting-command) sends TWO Escape key events (cancels the turn + restores the last message for editing, measured 3/3 mid-turn) and, on magenta, DOES spawn escape-recheck. So a `web-rewind` with `mode:rewind` + an escape-recheck spawn is a regression, but `mode:cancel-edit` on magenta SHOULD have one. content carries `win`, `ok`, `tab`, `mode`. NB the escape-recheck now bails only on a new `"type":"user"` transcript record (not raw growth) — the cancel-edit's trailing `ai-title`/`last-prompt` metadata used to false-bail it, leaving the tab stuck magenta (its tell: an escape-recheck `state_files`/tab_transitions bail reason mentioning transcript growth with no user prompt actually submitted). **web-rewind-to** (path = the session's state DB file, since 2026-07-18) = the FULL web rewind (`post_rewind_to` — a prompt bubble's ↶ / picking mode): the server drives Claude Code's own checkpoint menu in the session's window (`plugins/claude_code/rewindmenu.py` — typed `/rewind`, screen-verified `up` navigation to the target prompt's menu entry, restore option picked by parsed LABEL since the numbering shifts with content, digit key selects). content on success: `win`, `ok: true`, `tab`, `mode` (conversation/both/code), `ups` (the page's jump hint), `steps` (extra scan presses the text-verify needed — a big value = the page's view was stale, e.g. a kitty-side rewind it never saw), `digit` (which option number was pressed), `degraded` (true = a `both` request at a no-code-change checkpoint auto-degraded to the conversation restore — the code options were absent because the code was already in the target state, verified against the confirm screen's "The code will be unchanged." line; since 2026-07-18). On a bail: `ok: false` + `step` naming the failed stage (`busy` = refused outright on a busy tab; `open`/`find`/`confirm`/`option`/`close` = the menu step that never verified — each also pairs an `errors` row func `dashboard rewind-to (<step>)`, and the driver Escape-closes the menus before returning, so a session left SITTING in an open rewind menu after one of these rows is its own bug). A rewind restores conversation state ONLY inside the live TUI — it writes NOTHING to the transcript until the next send forks it (a user record whose parentUuid points back at the fork point), so "the dashboard rewound but the transcript still shows the turns" is EXPECTED, not a bug; the fork record arriving later is the on-disk confirmation the rewind really happened. **ask-pending** (path = the state DB file, since 2026-07-18) = the AskUserQuestion pending-state stash behind the web ask card (`plugins/claude_code/ask_fmt.py`, entry `claude-ask-fmt.py`): action content `{action: "write", tool_use_id, questions: N}` on PreToolUse(AskUserQuestion), `{action: "remove", reason}` on the clears — reason `answered`/`failed` (the tool's PostToolUse/Failure), `turn ended` (Stop/StopFailure) or `new prompt` (UserPromptSubmit). The turn-boundary clears exist because EVERY decline path (Esc, "Chat about this", empty-"Type something" Enter) fires NO closing hook at all (measured 2026-07-18). Each write/remove pairs a `hook_events` decision row under handler `claude-ask-fmt.py`. A `write` with no eventual `remove` while the session keeps taking turns = the clear routing broke (the card would sit stale on the page — though /answer still screen-verifies, so it can only 409, never mis-answer). No rows at all for a session that definitely asked = the session is UNHOSTED (no state DB — deliberate: the stash never creates the DB whose existence is the session-alive signal) or the ask came from a subagent (agent_id — ignored by design). **web-answer** (path = the state DB file, since 2026-07-18) = a dashboard ask-card POST (`post_answer` → `dashboard/askdialog.drive`, which drives the REAL TUI dialog with screen-verified keys): content `{win, ok, chat, tool_use_id}`, failures `{…, ok: false, step}` where step names the unverified stage (`open` = no dialog on screen — answered/declined in the terminal first; `question`/`cursor`/`options`/`type`/`advance`/`review`/`submit`/`chat` = a dialog step (`advance` = a multiSelect pane failed to move to the next tab via its "Next"/"Submit" row — the custom-text-advance bug, below); each pairs an `errors` row func `dashboard answer (<step>)`). The driver NEVER presses Escape on a bail (Escape would DECLINE the questions — opposite of rewindmenu's bail), so after a failed row the dialog is still open and re-answerable. An `ok: true` should be followed by the ask's PostToolUse hook_events row + the `ask-pending` remove (reason answered) — missing = the dialog submitted something other than what the driver thought (screen-model drift; compare the PostToolUse payload's `answers` against the intent). **plan-pending** (path = the state DB file, since 2026-07-18) = the ExitPlanMode half of the same modal-dialog tracker (ask_fmt handles BOTH tools): `{action: "write", tool_use_id, what: "N-char plan"}` on PreToolUse (tool_input carries the plan markdown + planFilePath), removes with the same reason vocabulary as ask-pending plus `web open-bail` (the dashboard's self-heal: an /answer, /plan-options or /plan-decision found NO dialog on screen while the stash lingered — resolved in the terminal before the turn-boundary clear fired — and dropped the stash via `state.kv_del_at`, the fresh-connection explicit-path delete that exists because kv_del's cached conn is thread-bound and silently no-ops on a dashboard handler thread). Clears are TOOL-SCOPED: the plan's PostToolUse never drops a co-pending ask stash and vice versa; turn boundaries drop both. **web-plan** (path = the state DB file, since 2026-07-18) = a dashboard plan-card POST (`post_plan_decision` → `dashboard/plandialog`): content `{win, ok, kind: decide|feedback|dismiss, label, tool_use_id}`, failures `{…, ok: false, step}` + an `errors` row func `dashboard plan (<step>)`. `decide` presses a decision digit ONLY after the screen still shows the requested label on it (labels vary with the session's permission mode and are fetched live via /plan-options — never hardcoded); `feedback` types into the "Tell Claude what to change" row (digit focuses, text inline, Enter submits the rejection-with-feedback); `dismiss` is the dialog's own Esc reject. Like web-answer, a bail leaves the dialog OPEN (an Escape bail would REJECT the plan). An `ok: true` decide should pair the tool's PostToolUse + the `plan-pending` remove (reason answered); feedback/dismiss pair NO hook (declines are hookless) — their stash clears at the next boundary or the revision's overwrite. **memory** (path = the state DB file, since 2026-07-21) = a file op under the memory wiki (`~/wiki/01`, `plugins/claude_code/memory.py`) was snapshotted into the `memory` kv the dashboard's Memory tab reads (docs/dashboard.md *Memory tab*): content `{action: "write", verb (Read/Update/Write), path, agent (subagent name or "main"), notes (distinct-note count so far)}`. Written by BOTH `claude-file-fmt.py` (main agent) and `claude-substream.py` (a subagent — team-wide, unlike the main-agent-only mirror), so a note the note-writer touched still shows. No rows for a session that clearly edited the wiki = the session is UNHOSTED (no state DB — `record` is `parked`-guarded, never creates the DB) OR the path wasn't under the hardcoded root (a vault at a different location than `~/wiki/01`, or the `BAQYLAU_MEMORY_ROOT` test seam not set); a memory op that painted its 🧠 marker in the mirror but left no row is the `record` write failing (paired `errors` func `memory.record`). **view-stash** (path = the state DB file) = a file-op producer (`claude-file-fmt.py`, or `claude-substream.py` for a subagent — then content also carries `agent`) pre-rendered a Read/Update/Write's click-to-view block into the kv row `view:<tool_use_id>`; content: `gid`/`tool`/`ops` count. A row from `claude-cmd-fmt.py` instead (`tool: Bash`, `kind: read`) is a file-READING command collapsed to a Read one-liner, and its `render` names WHICH renderer built the body (`code` = raw + a paint-time `lex`; `md` = the markdown AST render — a `sed`/`grep` slice of a `.md`). **view** (path = the state DB file) = a click on a file-op line's `/view` hyperlink: `claude-copy.py` toggled the gid in the `view-open` kv set (content: `gid` + `open` true/false; `open: null` = no stash existed, feedback no-op) and SIGWINCH-nudged the renderer via the `renderer-pid` kv row; failures land in `errors` funcs `view (…)` / `view-stash (…)` / `viewport_anchor (…)` / `toggle_scroll (view toggle)`. **view-reflow** (path = the state DB file) = the renderer processed that toggle: content carries `gid`, `idx` (the clicked line's offset; null = op not in the render window), `anchor` (the recovered viewport-top offset — a GLOBAL text match since 2026-07-12, `locate_viewport`, with the capture retried 3× under load and TWIN DISAMBIGUATION: near-best matches are tie-broken toward the caller's prior — the clicked line for the anchor, the restore target for the verify, the previous sample for the drift watch — because a buffer full of repeated content matched at multiple offsets and the restore teleported to the wrong copy while the verify confirmed that same wrong copy: an audit-PERFECT row for a real user-visible jump, THE root cause of the "hide jumps to a random location" reports; impossible there-and-back drift bounces (4808→1270→4880 in 400ms) are the misread signature; null = capture/match failed → fell back to clicked-line-at-top AND left an `errors` row func `viewport_anchor (no window|no capture|empty capture|no match)` — no-match carries cap/rows/best/score detail; a null with NO paired errors row = pre-fix renderer), `cap0` (the first line of the pre-toggle capture — what the user actually saw), `up` (the restore scroll amount; the restore is ABSOLUTE — scroll-to-end then up, so `up` counts from the bottom, whose frame top is `total+1-h`, the +1 being the cursor row), `applied`, `dsr` (did kitty's cursor-report handshake confirm the frame was parsed before the scroll — false = the scroll may have raced the parse), `landed` (where the viewport VERIFIABLY ended — the same global text-match as the anchor; the ground truth), `retried` (a landed≠target miss was CONVERGED onto the target — up to 3 passes, each scrolling by the measured error, never the same absolute amount re-run, because kitty scrolls VISUAL lines while the row math counts logical rows and wrapped rows make the same restore reproduce the same miss; a first miss >400 rows = momentum raced the restore itself → the absolute restore is redone once, then delta passes; "in place" means ZERO rows off — a 17-row near-miss reads as a lost scroll position; a PERSISTENT landed≠anchor with `up` ≈ scrollback_lines = the restore clamped at the scrollback ceiling — the frame outgrew the buffer, see ROW_BUDGET) and `follow` (the pre-toggle viewport was AT the bottom, so the restore targeted the NEW bottom to keep tail-following instead of pinning) — THE row for any "the view jumped on expand" report: `anchor: null` on a visible-line click is the tell (get-text broken or rows drifted from the painted text), and a `view` row with NO `view-reflow` row means the renderer never processed the toggle (dead/stale renderer — check `renderer-pid`). **view-drift** (path = the state DB file) = the post-toggle DRIFT WATCH caught the viewport moving: for 8s after every toggle the renderer re-locates the viewport each 200ms tick and records every change (`from`/`to` offsets + `left_ms` watch time remaining + `corrected`) — the evidence for "the toggle verified its landing but the pane ended up somewhere else moments later": a user wheel-scroll shows as gradual steps, a bug as one instant leap (e.g. `to` ≈ 0 = something scrolled to buffer start; observed live: a verified landing yanked 969 rows within one tick — only on real mouse clicks, never on socket-driven sim toggles). `corrected: true` = the SETTLE GUARD fired: for ~700ms after a landing (sampled at ~80ms) the position belongs to the toggle's INTENDED anchor (`home` on the reflow row — never the measured landing, which in-flight momentum can corrupt; observed adopted 1176 off) — a displacement >5 rows in that window is the user's RESIDUAL TRACKPAD MOMENTUM (they flick-scrolled to the line, clicked, and the leftover momentum applied on top of the fresh restore — the root cause of every "hide jumped ~1000 rows" trace: huge displacement within 1-2 ticks, decaying step series, never reproducible without a human hand, kitty itself verified exact 12/12 in a sterile window) and is snapped back by an ABSOLUTE restore (recomputed against current content — a relative fix against a still-moving target amplifies), max 2 per toggle. Deliberate post-click navigation (observed starting at +1100ms) is outside the window and never fought. No view-drift rows after a toggle = the pane genuinely stayed where `landed` says. **paint** (path = the state DB file) = one row per full-reflow decision the renderer made: `kind` (`repaint`/`toggle`/`skip` — `skip` = a WINCH at an UNCHANGED size with no toggle plan, deliberately painted nothing: a repaint there clamps a scrolled-up viewport to the bottom), `w` (width), `rows` (newlines actually written; capped by `ROW_BUDGET`, default 4800 / env `CLAUDE_MIRROR_SCROLLBACK` — the ops list is trimmed so the frame fits kitty's scrollback, because rows beyond it are unreachable after any reflow), `ops`, `open` (expanded view blocks) — the ground truth against the toggle math: a `view-reflow` whose `up` disagrees with the painted `rows` is a model-vs-buffer divergence. **render:\<taskid\>** (path) = a `claude-stream.py` content-rendering stream — markdown (`cat`/`head`/`tail` of a `.md`, `CLAUDE_MIRROR_MD`), JSON (`cat` of a `.json`, `CLAUDE_MIRROR_JSON`), YAML (`.yml`/`.yaml`, `CLAUDE_MIRROR_YAML`) source code (`.py`/`.java`/`.kt`/`.sh` etc, `CLAUDE_MIRROR_CODE` — `kind` is `code:<lexer>`), or a fg stream whose OUTPUT was sniffed to contain a fenced code block (no filename hint, `CLAUDE_MIRROR_MD_SNIFF` — `kind` is `md-sniff`). ALL filename-keyed detection runs in the tailer itself, from the raw command every launch site passes via `CLAUDE_STREAM_CMD` (`hookkit.stream_env`) — so it covers a SUBAGENT's live-fg command too (the substream-spawned tailer), and these `render:` rows are the ONE render-decision evidence (no launcher decision suffix): action `start` (content `kind`, + `wenmode` = was the md parser importable, else it degraded to the `render.markdown()` subset) and action `done` (content `kind` + `blocks` = how many rendered gut ops it emitted; JSON/YAML/code are 1). `blocks: 0` from a stream that ran = a render failure (its own anomaly, below). Only markdown fenced code blocks render as a full-width panel — an `ops` gut row with a `bg` field; JSON/YAML/code colour on the normal gutter (no `bg`). **composer-draft** (path = the session's state DB file, since 2026-07-19) = the web composer's UNSENT-message draft (`dashboard/server.py post_composer_draft`): content `{action: "write", chars, seq, origin}` on each debounced edit, `{action: "clear", chars:0, seq, origin}` on send OR an emptied box (an empty-text TOMBSTONE, not a delete — its `seq` must survive to reject a straggler), and `{action: "stale", seq, have, origin}` when a write was DROPPED for arriving older than the stored `seq` (the 2026-07-19 clear-vs-save race guard: a debounced save landing after the send's clear would resurrect the just-sent draft; `seq` is the page's `Date.now()` at dispatch, and the clear always carries a later one). A run of `stale` rows around a send = the tunnel reordered writes and the guard did its job. Unlike `ask-draft` it has NO plugin-side lifecycle — a message draft has no turn boundary, so it lives until sent/overwritten and the dashboard fully owns both write and clear; the SSE `composer-draft` event re-broadcasts it with `origin`-echo suppression. Persists for LIVE and PARKED sessions (`state_db_for` resolves the parked copy). A `write` with no eventual `clear` is normal (the draft is meant to survive); "my draft vanished" = no `write` row (the POST never landed) or a stray `clear`; "the draft did NOT clear after I sent" = a `stale` row swallowed the clear's winner (the guard mis-ordered — inspect the `seq`/`have`) OR the clear's POST never landed (no `clear` row). **composer-queue** (path = the session's state DB file, since 2026-07-19) = the web composer's PENDING queued-message chips (`post_composer_queue`, the ⧗ list for mid-turn messages the TUI queued but hasn't delivered): content `{action: "write", n, origin}` (the whole chip list re-persisted on every mutation — a queued send, a delivery drain, a ✕-hide) or `{action: "remove", origin}` when the list empties. Display-persistence only (a reload used to lose the chips though the message stayed in the TUI's queue — the "gone even from the queue after refresh" report); the message itself is NOT here. SSE `composer-queue` re-broadcasts with `origin`-echo suppression. RELATED: a `web-send` with `blocked: modal` + `ok: false` = a composer send REFUSED because an ask/plan dialog was up (it would paste INTO the dialog and be lost); since 2026-07-30 the two DECLINE paths drop their own stash (`plan-decision` kind `feedback`/`dismiss`, `answer` with `chat`) as a `plan-pending`/`ask-pending` `{action: remove, reason: "web decline (…)"}` row — a decline fires no hook, so before that the gate stayed shut and blocked every send until the next turn boundary; a `web-send` with `via: ask-chat` = the message the ask card delivered after routing a typed preview-question answer through "chat about this". **ns-prefs** (log/path EMPTY — GLOBAL, no session, since 2026-07-19) = the new-session form remembered its last-used `{cwd, model, effort}` in the durable global prefs DB (`dashboard/prefs.py`, `~/.claude/baqylau-dash-prefs.db`, `POST /api/ns-prefs`): content the stored record + `action: write`. Moved off per-browser `localStorage` so the value is cross-device; model/effort are re-validated against the launch allowlists (a bad value is DROPPED, never stored — so a corrupt pref can't feed the launch path). No session relationship — it only pre-selects the NEXT launch's form. **notify-mute** (log/path EMPTY — GLOBAL prefs, since 2026-07-20) = a dashboard `POST /api/session/<sid>/notify` opted a session in/out of the deferred Telegram alert (docs/dashboard.md *Telegram alerts*): content `{sid, muted}`, stored in the `notify-muted` kv map of the global prefs DB (`dashboard/prefs.set_notify_muted`). Global (a dashboard pref, not session state) so it works live AND parked; a bad `muted` (non-bool) lands ONLY as an `ok:False` reject row, action `notify-mute`. **notify-global** (log/path EMPTY — GLOBAL, since 2026-07-24) = the list-page `#notifytoggle` master switch was flipped via `POST /api/notify`: content `{enabled}`, stored as the bare `notify-enabled` bool in the global prefs DB (`dashboard/prefs.set_notify_enabled`, default ON). When `enabled:false` the `Notifier.scan()` transition site suppresses EVERYTHING (toast + Telegram + web-push) and stamps a `notify-suppress reason:global-off` per transition, overriding per-session mutes; a bad `enabled` (non-bool) lands ONLY as an `ok:False` reject row, action `notify-global`. **telegram-notify** (log/path EMPTY — GLOBAL, since 2026-07-20) = the off-device alert actually FIRED: the tab went red/green and presence said you weren't there (since 2026-07-28 that is decided on the transition tick — `CLAUDE_DASH_NOTIFY_DELAY_S` defaults to 0). TWO transports since 2026-07-27, and WHICH one ran decides whether the alert can be taken back: with `~/.config/telegram/{bot-token,chat-id}` configured the dashboard calls the Bot API in-process and keeps the `message_id` (content carries `ok`, `status`, `message_id` and **`retractable:true`**); unconfigured it degrades to Popen'ing the reused `notify` skill (`CLAUDE_DASH_NOTIFY_CMD`), whose id goes to DEVNULL — `retractable:false`, `transport:"script"`, and NO `notify-retract` row can ever follow. content `{sid, kind, reason}` (`asking`/`done`; `reason` since 2026-07-24 = **escalation** (the 5-min nudge after an on-device push you ignored) / **no-device** (nobody push-subscribed — the immediate fallback) / **always** (`_ALWAYS` forced both) / — since 2026-07-28 — **terminal** (presence routed you to the KITTY TERMINAL, which Telegram is the only channel for: the normal same-second alert when you were last at the terminal, and it arms NO escalation) / **push-off** (presence DID pick a browser but the push channel couldn't deliver — `CLAUDE_DASH_NOTIFY_WEBPUSH=0` or no crypto backend; kept apart from `no-device` because reading it as that sends you hunting for a subscription that exists) — so a Telegram alert is never an unexplained duplicate, nor an unexplained ROUTE). "no Telegram arrived" → NO telegram-notify row for the sid = it never armed/fired (session muted — check the `notify-muted` map / prefs DB, OR `CLAUDE_DASH_NOTIFY_TELEGRAM=0`, OR you reacted within the delay so the arm was cancelled — reacting now INCLUDES reacting AT THE TERMINAL without moving the tab — answering an AskUserQuestion or typing a reply into the `❯` box — OR simply LOOKING at the session at send time — the kitty tab frontmost / a browser viewing it — all of which land a **notify-suppress** row, below), a row PRESENT but no message = the notify script itself failed (its own transport error, outside the audit) or the launch raised (a paired `errors` row func `dashboard telegram notify`). **notify-suppress** (log/path EMPTY — GLOBAL, since 2026-07-22) = an alert was suppressed. The `reason:global-off` variant (since 2026-07-24) is the ODD one out — it fires at the TRANSITION, before any arm, when the global `#notifytoggle` was OFF (see `notify-global`), and NOTHING arms. The other reasons mean an ARMED alert was DROPPED before it could be sent — FIVE of them, all through the ONE disarm site `Notifier._drop(win, reason)`. `reason: muted` (since 2026-07-25) is the session's own opt-out (the ◉/○ header toggle — `notify-muted`) checked at SEND time; before that date it dropped with NO row, so a muted session's swallowed alert was indistinguishable from you reacting (the deliberate no-row drops are: the tab left the armed state / the session ended / a web composer draft is in progress — read `tab_transitions`, `sessions`, `composer-draft` — plus the two SEND paths, recorded by `telegram-notify` / `web-push` instead). The other FOUR mean you were plainly ON the session during the grace window. Two are reacting AT THE TERMINAL (the only signal terminal reacting leaves, since it moves neither the tab nor the transcript), dropped mid-grace: `reason: dialog-activity` (content `{sid, kind: asking, ...}`) = a red `asking` arm whose on-screen AskUserQuestion dialog CHANGED (typing a free-text answer / toggling a selection — `askdialog.region` diff over the frontend `get_text`); `reason: terminal-input` (content `{sid, kind: done, ...}`, since 2026-07-23) = a green `done` arm where you typed a reply into the `❯` input box — REAL non-faint text there (`suggestion.typed` over the ANSI `get_text`; a settled tab pre-fills only a FAINT ghost, ignored). Two more are you LOOKING AT the session — for a `done` arm checked EVERY scan while armed ('if I saw the final message, don't tell me' — one glance anytime in the grace cancels, even after you left; since 2026-07-24), for an `asking` arm checked only at SEND time (seeing a question isn't answering it, so a glance-then-leave still fires): `reason: tab-focused` = the kitty tab is frontmost (`Frontend.tab_focused` — `is_focused`, NOT `is_active`, so a dashboard-spawned tab in a backgrounded kitty doesn't count); `reason: web-viewing` = a browser reported viewing it within `CLAUDE_DASH_VIEW_TTL_S` (the `/api/presence` heartbeat, cadence DERIVED from that TTL since 2026-07-25, in-memory `_VIEWING`, sent only while the page is visible+focused+on that session — NO per-beat audit row, only this suppress outcome); `reason: device-active` (since 2026-07-28) = a browser reported ITSELF in use within the same TTL, from ANY view, so the in-page toast already told you — the one MACHINE-WIDE suppressor here (it doesn't name a session), which makes a forgotten awake device the first thing to check when NOTHING alerts anywhere (`anomalies` counts these). Since 2026-07-28 all four only CANCEL a `done` arm; an `asking` arm is HELD instead (a `notify-arm phase:hold` row) and still fires once you leave. "I was answering/replying/watching and STILL got pinged" → NO notify-suppress row = nothing registered you: no terminal channel resolved (dashboard started outside kitty — `Notifier.fe` is None), OR (asking) the red tab wasn't an AskUserQuestion (a permission/plan prompt has no `☐`/`☒` header chip, so `region` is "" and the guard is a deliberate no-op), OR (done) the box held only a ghost / was empty, OR the browser tab was open but NOT focused/visible (no beat sent) OR the kitty tab was merely active in a backgrounded kitty (`is_focused` false), OR the presence beat LAPSED between beats — `CLAUDE_DASH_VIEW_TTL_S` set below the page's beat cadence, which before 2026-07-25 was a hard-coded 8s that no TTL change could move (it is now DERIVED from the TTL served by `GET /api/limits`, TTL/2.5 floored at 2s — docs/dashboard.md *Served limits*; for such a build check the knob's value, there is no per-beat row to read), OR you thought/watched-elsewhere without any of these signals (indistinguishable from walking away — with the delay now 0 there is no thinking window at all, so set `CLAUDE_DASH_NOTIFY_DELAY_S` back up if you want one). **notify-arm** (log/path EMPTY — GLOBAL, since 2026-07-24) = the deferred-alert lifecycle ANCHOR. `phase: arm` (content `{sid, kind, phase, delay_s}`) fires on the red/green transition when the alert is armed — **`delay_s` is PER KIND since 2026-07-29** (`notifier.alert_delay`): an `asking` arm carries `CLAUDE_DASH_NOTIFY_DELAY_S` (default 0, fires the same tick) while a `done` arm carries the SETTLE window (`CLAUDE_DASH_NOTIFY_SETTLE_S`, default 20 — the max of the two), so a `done` arm with NO send for 20s is CORRECT and a `done` arm whose `delay_s` reads 0 on a current build means the settle was switched off or the server predates it; `phase: hold` (`{sid, kind, phase, reason}`, since 2026-07-28) fires ONCE per arm when a look DEFERS an `asking` alert (you are watching it — it stays armed and goes out when you stop); `phase: escalate` (`{sid, kind, phase, in_s}`) fires when the stage-1 on-device push arms the Telegram escalation. Use it to anchor a session's notify story: an `arm` with NO following suppress/route/telegram AND the tab moved off red/green (a `tab_transitions` row) = you reacted (the arm was silently cancelled — the healthy path); an `arm`+`escalate` with no later `telegram-notify reason:escalation` and no suppress = the escalation was armed but never fired (server restarted mid-window — `pending` is in-memory). **notify-route** (log/path EMPTY — GLOBAL, since 2026-07-24) = the DEVICE-ROUTING decision at stage 1 (docs/dashboard.md *Presence routing*): content `{sid, kind, target, target_label, n_subs, legacy, candidates:[{device, label, age_s}]}`. Written for EVERY alert since 2026-07-28 (it used to be skipped when nothing was subscribed) — with the TERMINAL in the running there is always a choice being made. `target: "terminal"` is the reserved device id for kitty, whose "beat" is the notifier's own `app_focused` poll (~5s while kitty is frontmost) and which is reached by TELEGRAM, not push; it wins only when strictly newer than every browser, so ties and a cold start go to a quiet push. THIS is the "wrong device buzzed" evidence — `target` is the chosen device, `candidates` lists every push-subscribed device with its presence `age_s` (seconds since its last `/api/presence` beat; `None` = never beat this run), so you can see it picked the freshest. `legacy:true` = untagged subs (pre-routing client) → sent to all. Cross-check `target` against the frontend `boot` row's `device`→`dlabel` map to name the physical device. **notify-retract** (log/path EMPTY — GLOBAL, since 2026-07-27) = a DELIVERED alert was taken back, or given up on (docs/dashboard.md *Alert retraction*): content `{sid, kind, channel, reason, outcome, ok, age_s}`. ONE action with ONE writer (the notifier — `channels.retract` deliberately files no row, so the lifecycle has one shape and the expiries, which never reach a channel, land here too). `channel` is `telegram` (the message was `deleteMessage`d) or `webpush` (a `type:"resolve"` push closed the banner — its per-device wire detail is the paired `web-push` `action: resolve` row). `reason` is the name `notifier.retracts(kind, reason)` accepted — `tab-moved` (you answered / the tab left the alerted state — by far the common one), `session-ended`, `composing` (RETRACT_REASONS, either kind), PLUS, on a `done` alert ONLY, a per-session LOOK: `tab-focused`/`web-viewing` (SEEN_REASONS, since 2026-07-29) — or `ttl`/`capped` for an expiry. Deliberately NARROWER than the `notify-suppress` reason set, and the narrowing is KIND-DEPENDENT: a glance retracts a delivered `done` alert (a finished turn's final message is on screen the moment the tab goes green, so looking IS reading it — and since green is the RESTING state of a finished turn, nothing else would ever resolve it) but must NEVER retract an `asking` one (you'd delete your only reminder while the tab is still red asking). Machine-wide `device-active` retracts NEITHER (one awake iPad would delete every session's alert), and the screen-scraped `dialog-activity`/`terminal-input` are skipped because pass 4 runs `screen=False` (hours of tracking, no `kitten @ get-text` per tick). A `done` alert on a PRE-2026-07-29 build could only end `ttl`/`capped` unless you sent something — that is the "I opened the session and the notification stayed" shape, and it is a stale dashboard (which does not hot-reload), not a live bug. `outcome`: `ok` (retracted) / `gone` (already out of the chat — same thing) / `failed` (the wire refused; the alert is STILL out there) / `expired` (nothing resolved it inside `CLAUDE_DASH_RETRACT_S`, default 24h, or `SENT_CAP` dropped it). `ok:false` is the canned anomaly "off-device alert left behind" — but note the query matches the sid INSIDE the JSON, since these rows are global. NO notify-retract row at all for an alert you DID answer has three readings, in order: the send wasn't retractable (check the `telegram-notify` `retractable` flag), the dashboard RESTARTED (handles are in-memory, exactly like `pending` — restarts strand deliveries silently), or nothing resolved it yet. **web-push** (log/path EMPTY — GLOBAL, since 2026-07-23) = the ON-DEVICE Web Push channel (docs/dashboard.md *Web push*), the iOS/desktop twin of the Telegram alert, fired at the SAME deferred point (same grace window + suppress logic + 🔕 mute). FOUR actions (`resolve` since 2026-07-27): `action: subscribe`/`unsubscribe` (content `{action, endpoint, device, label}` — since 2026-07-24 subscribe carries the `device` id + `label` used to ROUTE the push to the most-recently-used device) = a browser registered/dropped its push subscription (`POST /api/push/subscribe`|`/unsubscribe`, stored in the `push-subs` kv of the global prefs DB — per-DEVICE, not per-session); `action: send` (content `{sid, kind, status, ok, gone, badge, device, endpoint}` — `device` names the ROUTED target, one per subscription of that device only — not every sub) = one push delivery attempt from `Notifier._webpush_send` (a detached daemon thread per fire, one row per subscription); `badge` is the needs-you count the push carried for the app-icon Badging API (docs/dashboard.md *Installed-app polish* — the badge is otherwise a CLIENT-only concern with no rows of its own, so a "wrong badge while the app was closed" trace lives here). "no iPad notification arrived" → NO `subscribe` row for the device = it never subscribed (a plain Safari tab, NOT the installed home-screen app — iOS exposes Push only in standalone; permission not granted; `webpush.enabled()` False = no `cryptography` backend; `CLAUDE_DASH_NOTIFY_WEBPUSH=0`), a `send` row with `ok:false` = the push service rejected it (`status` says why — a 4xx VAPID/JWT problem, a 5xx transient), a `send` with `gone:true` = the subscription was pruned (browser uninstalled/cleared it — the device must re-subscribe), and NO `send` row at all despite `subscribe` rows = the alert was suppressed/muted upstream exactly like Telegram (check `notify-suppress`/`notify-muted`, same predicates). `action: resolve` (content `{sid, kind, status, ok, gone, badge, device, endpoint}` — the same fan-out, same fields) = the RETRACTION push: a `type:"resolve"` message that makes `sw.js` close the banner under tag `claude-<sid>` and show NOTHING (docs/dashboard.md *Alert retraction*). It is the ONE push that deliberately raises no notification, which iOS's `userVisibleOnly` contract tolerates only on a budget — hence at most one `resolve` per delivered alert, and `CLAUDE_DASH_RESOLVE_PUSH=0` to switch it off. A `resolve` with `ok:true` and a banner that STAYED means WebKit refused it silently; the page's own foreground sweep clears it on next open, so the visible symptom is a banner that lingers until you open the app. Pair it with the `notify-retract` row (`channel: webpush`) that ordered it. A bad subscribe body lands ONLY as an `ok:False` reject row, action `web-push` Every web-* CONTROL row (web-interrupt/web-command/web-rename/web-answer) additionally carries, for a session whose owning tool is NOT claude_code, `host` (claude_code|codex), `status` (acknowledged|rejected|indeterminate — the HostControl gesture result), and `cid` (a per-gesture correlation id): a codex session's interrupt/compact/rename/ask route through its HostControl (plugins/codex/hostctl.py), not the claude inline path, so those rows name the host + outcome; a claude_code row omits them (its inline body is byte-identical). |
| `pane_events` | mirror/scoreboard pane operation | action (open/close/toggle-on/toggle-off/grow/shrink/reset/setpct/**close-stale**/**focus-host**), **ok** (verified against kitty — 0 means the pane genuinely isn't there), detail (bias/resulting width). First stop for "frozen/missing pane" reports. **close-stale** (since 2026-07-11) = `close_stale_mirrors` swept a different-sid mirror out of the session's tab, detail `closed sid=<sid> win=<id>` — the previously-invisible op behind every vanished-mirror report; sweeping a still-OPEN session's mirror is the `pane hijack` anomaly. **focus-host** (since 2026-07-19) = `open_mirror` handed inner-tab focus back to the host pane after splitting the mirror/scoreboard in (detail `win=<anchor>`, the host window id) — an inner-tab `action first_window`, never an OS-window raise; `ok=0` means the `kitten @ action` call failed, so the tab may still show "▪ session" instead of the host's ai-title. Only emitted when `open_mirror` actually created a pane AND had a host anchor. An `open` with detail `skipped: no host pane (daemon/headless session)` = a SessionStart with no `KITTY_WINDOW_ID` and no `claude_session`-tagged window (an agents-view/`claude daemon run` session or headless `claude -p`) deliberately opened nothing. **`skipped: nested in live host <sid>`** (since 2026-07-22) = the nested-host guard fired — a `claude` launched inside another LIVE session's tab (it inherited the outer pane's `KITTY_WINDOW_ID`) skipped the whole lifecycle instead of sweeping the outer mirror and re-tagging the host window (the nested-claude pane-hijack, below); `<sid>` is the outer owner. Pruned with the other per-session tables (was once omitted — unbounded growth) |
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

### A codex session's ctx bar never breathes while it compacts

- The web animates the ctx bar for the ~2 minutes a compaction runs, driven by
  the `compacting` latch — and codex only started writing one in P4. So FIRST
  establish the build: `SELECT * FROM state_files WHERE session_id=? AND
  action='compacting'`. **No rows at all** on a session that definitely
  compacted = either a pre-P4 dashboard/dispatcher, or the event never reached
  the facet subscriber.
- Then read the paired `hook_events`: `SELECT hook, decision FROM hook_events
  WHERE session_id=? AND handler='claude-codex-hook.py' AND hook LIKE
  '%Compact%'`. The decision string says exactly what the subscriber chose:
  - `compacting armed (manual)` / `compacting cleared (manual, 104.3s)` —
    working; if the bar still didn't move the problem is on the READ side (see
    below), not here.
  - `nested-skip (…)` — the standalone gate refused it. Correct for a
    `codex exec` inside a Claude session; WRONG for a real host, and the cause
    is a missing `codex_host_mark` row (its SessionStart bailed — look for the
    `codex-session` handler's `no kitty window` / `nested-skip` decision).
  - `compact ignored (no state DB)` — the session is unhosted or already parked.
  - `compact ignored (agent_id present)` — a codex SUBAGENT's event, correctly
    dropped (a child has no compaction of its own).
  - `compact latch failed (PreCompact)` — paired `errors` row, func
    `codex compact latch (PreCompact)`.
- **A `write` with no `remove`** means the same thing it does for Claude: the
  compaction died on an API error or was interrupted, and NEITHER tool fires a
  closing hook. That is not a leak — the READ side ages the latch out past
  `config.COMPACT_MAX_S`, deliberately, because an animation must fail OFF and
  the process that armed it has long exited. A bar animating *forever* is the
  bug, and it is a read-side one.
- If the rows are right and the bar still never moves, the remaining suspect is
  ROUTING: `plugins.compacting(sid)` is ownership-routed, so it asks whichever
  host owns `sessions.transcript_path`. A codex session whose row carries a
  CLAUDE transcript path (or none) is answered by claude_code, which reads its
  own kv and finds nothing. Check `SELECT transcript_path FROM sessions WHERE
  session_id=?` resolves to a `.codex/sessions/…/rollout-*.jsonl`.

### A codex session's running command shows no live ⏱ elapsed chip

- The chip needs a `state:fg-live` record whose `tid` is the MIRROR BLOCK's
  copy-group id. For codex that record is written by the ROLLOUT STREAM, not by
  a hook — so the first question is whether the stream is the standalone one:
  `SELECT kind, src_path, ended_at FROM streams WHERE session_id=?`. Only a
  `codex-watcher` with a `standalone:` `src_path` (and the per-run `codex`
  stream it spawns) writes these.
- Then: `SELECT action, content FROM state_files WHERE session_id=? AND
  path='state:fg-live'`. A `write`/`remove` pair per command is healthy.
  - **No rows while commands clearly ran** = the run is a SIDECAR/SUBAGENT
    register, not standalone (correct — a nested codex must never write the
    host's fg record, or it collides with Claude's own), or a pre-P4 build.
  - **A `write` whose `tid` looks like `exec-<uuid>` or `call_<…>`** is the
    diagnostic jackpot: something stamped a HOOK or ROLLOUT id instead of the
    block's copy group. Those are disjoint id spaces (measured 2026-07-31), so
    the chip is anchored to a block that does not exist and will never paint.
    The group id is an `ops.new_group()` integer.
  - **A `write` with no `remove`** = the turn was aborted mid-exec (codex writes
    no closing exec record and fires no hook — `turn_aborted` is a rollout note,
    not an event). The chip retires anyway via the record's `pid`: the stream
    dies with the run and `pid_alive` fails. A chip that ticks forever means the
    pid is somehow still alive — check that the `codex` stream row has an
    `ended_at`.
- Same routing caveat as above: `plugins.fg_running(sid)` asks the OWNING host,
  so verify `sessions.transcript_path` is a codex rollout.
- NB the tasks card being absent on a codex session is NOT this bug class and
  NOT a bug: codex has no task-list tool, so `plugins.tasks` is a declared
  DECLINE (`tests/test_l1i_host_contract.py` COVERAGE) and the card is hidden
  by design.

### A codex SUBAGENT renders as a "codex run" (no launch/result cards, raw JS commands, folds into "ran N codex runs")
- The symptom is a REGISTER that never got selected. A codex-native subagent
  (`collaboration.spawn_agent`, cli 0.146+) must stream in the SUBAGENT register:
  its ops stamped `sub:<codex_aid>`, painted in the SUB palette, with an
  `Agent "<nickname>" launched` card and an `Agent "<nickname>" finished · <dur>`
  card (docs/codex.md *A codex-native subagent IS a child agent*).
- **Start at `spawns`**: the purpose must read `stream:codex-subagent <label>`. A
  `stream:codex <label>` for a child rollout means the watcher took the plain
  path (`standalone_scan` didn't match `parent_thread_id` to this SID, or
  `rollout_subagent` returned `(None, None)` because the `session_meta` wasn't
  written yet and was never retried) — so no `CLAUDE_CODEX_SUBAGENT=1`, and the
  stream painted a sidecar.
- **Then the `ops` `src` stamps**: `SELECT DISTINCT json_extract(op,'$.src') FROM
  ops WHERE session_id=…`. A subagent's ops stamped `codex:<aid>` = the same
  miss (or simply PRE-2026-07-31 history, which is expected and stays as
  written — parked ops are never re-stamped). `sub:<aid>` = the register worked.
- **No launch card** (`note` on the `⇢ prompt` op absent) with the register
  otherwise correct: the brief came back empty — `rollout.subagent_brief` reads
  the LAST non-synthetic role=user turn of the replayed-parent prefix, because
  the child's own NEW_TASK payload is `encrypted_content` and unreadable. A
  prefix with no plaintext human turn yields no card by design (a card with
  nothing behind the click is not a card).
- **The result card is missing but a `✎ message` sits where it should be**: the
  final message was flushed as an INTERMEDIATE one — something in `_FLUSH_BEFORE`
  (a block-opening record) arrived between the last message and `task_complete`.
- Cross-check the palette from the ops themselves: a subagent's chips wearing
  `CODEX_PALETTE` rather than `SUB_PALETTE` is the same register miss seen from
  the paint side.

### The ⚠ warning light itself misbehaves (chip stuck / missing / mirror lines duplicated)
- The light is `core/errwatch.py`, polled+emitted by the scorebar. **No chip despite `errors` rows**: check `errors` for a `func` containing `errwatch.poll` — the watcher's OWN failure is audited exactly ONCE per process (recursion guard) and then goes silent, so a single such row means the light has been dark since that timestamp (restart the scorebar via a mirror toggle). Also check the scorebar is running at all (`streams`/pane state — no scorebar, no poll).
- **`⚠ audit: global:` lines in several sessions at once**: not a duplication bug — GLOBAL rows (`session_id=''`: an audit outage, a pre-session/CLI error) are shown by EVERY live session's light (each dedupes via its own `errseen-global` kv). Pull them with `bin/claude-audit.py errors ''`. A pre-2026-07-15 session showing NO trace of a known audit outage is the old blind spot (the light only counted per-sid rows), not a lost row. NB a fresh session's `errseen-global` checkpoint starts at 0, so stale global rows re-surface once in every NEW session — junk global rows are worth deleting (`sql-write`). One known junk shape (leak fixed 2026-07-16): `script='-c'`, func `spawn … (script missing)` — the TEST SUITE's own deliberate degrade row, written by an in-process unit test that bypassed the hermetic `CLAUDE_AUDIT_DIR` (conftest now sandboxes in-process writes too; `-c` is the pytest-xdist worker's argv[0]).
- **A `⚠ audit:` line reading `<script>: NoneType: None`**: pre-2026-07-16 display of a DELIBERATE no-exception degrade row (`A.error` outside an except block stores format_exc's `NoneType: None` sentinel). Current builds show the row's `func` string instead (`⚠ audit: <script>: spawn nope.py (script missing)`); the sentinel appearing on a current build means the row had an empty func too.
- **A mirror `⚠ audit:` line duplicated or missing**: compare the `state_files` `action='errseen'` checkpoint rows (`last`/`new`) against the `errors` rowids — a gap that was never covered by an `errseen` advance was never emitted (emit failed AFTER the checkpoint moved: at-most-once by design; the paired ops should be in the audit `ops` table if they made it out); an overlap means the kv checkpoint was lost (state DB recreated mid-session — cross-check the fresh-db/adopt trail).
- **A flood**: >3 new rows in one 5s poll collapse into one `⚠ audit: N new errors …` line by design — not a missing-lines bug.
- **A benign, expected-outcome degrade-audit persistently lighting the chip in every session** (a `NoneType: None` row for a `func` whose code path is a documented normal return, not a failure — e.g. an optional widget that just doesn't attach): that signature belongs on `core/errwatch.py`'s `IGNORE_FUNCS` set, which drops it from the chip COUNT and the painted `err_ops` while STILL writing the row to `errors` (queryable via `… errors ''`). Deciding fix-vs-ignore and adding the signature is the **global-errors skill** (`.claude/skills/global-errors/SKILL.md`). Ignore only a genuine expected-outcome path — a real stack trace gets FIXED.

- **A dashboard preference didn't stick, though the page reported success** —
  the global alerts ◉/○ back ON after a reload, a hidden directory re-appearing,
  a new-session draft or launch default gone, a web rename reverting, a
  `notify-muted` session alerting anyway, a push subscription that vanished.
  These all live in the ONE durable global store (`dashboard/prefs.py` →
  `~/.claude/baqylau-dash-prefs.db`), whose writes are BEST-EFFORT: `mutate_map`
  returns the *intended* map even when the write was lost, so the handler answers
  `ok` and its `state_files` row (`notify-global`, `notify-mute`, `ns-draft`,
  `ns-prefs`, `web-push subscribe`, …) says `ok:True` — the row proves the
  gesture ARRIVED, never that it PERSISTED. The tell is an `errors` row
  `func='dashboard prefs mutate'` (or `set`/`connect`) at the same instant,
  carrying the kv `key` at stake: **an `ok:True` gesture row next to a
  `dashboard prefs <op>` row IS the "it didn't stick" signature.** No prefs row
  either → the write really landed, so look client-side instead (the page's own
  `web-client` `<gesture>.begin`/`.ok` pair, a stale cached SPA part per
  `boot.build` ≠ `hello.boot`, or a `stale`-guard drop for the draft endpoints).
  Note the read side is audited at most once per (op, key) per process, so ONE
  `dashboard prefs get` row means that key has been unreadable ever since — every
  read of it has been silently returning the default (which for
  `notify-enabled` means alerts read as ON regardless of the stored value).
  Rows before 2026-07-25 don't exist at all: that build swallowed all five sites
  silently, so absence of evidence on an old session proves nothing.

- **The dashboard's ⧉ copy copies nothing / click-to-view 404s — but only on
  blocks that streamed in LIVE, and a reload fixes it.** Look for `errors` rows
  `func='dashboard copy (state DB gone)'` and `state_files` `action='web-view'`
  with `ok:False`, then read the `web-copy` row's own `gid` — if the group id is
  real but the request still found no state DB, the SESSION KEY in the link was
  wrong, not the group. Every rendered op is stamped with the session's
  mirror-log key (`data-cc="<key>/<g>/<what>"`); the backlog path stamps it
  correctly, so a symptom that vanishes on reload means the LIVE tick was
  stamping something else. Shipped exactly once (2026-07-25 → fixed 2026-07-26):
  a `for key, count in <badge table>.items()` stanza inside `sse_session`'s tick
  loop rebound the loop's `key` local, so from the second tick on every live
  block carried `data-cc="memory/…"` — plus it rebound the tick counter to the
  memory-note count, so the SLOW cadence (`git status`, transcript probes, the
  ghost-suggestion screen scrape) could run on every 0.6s tick. The pushed
  fields are a channel TABLE now (docs/dashboard.md, *The stream's pushed fields
  are a channel table*); a recurrence would mean a new loop variable in that
  body. Note the spurious `dashboard copy (state DB gone)` rows light the ⚠ chip
  in EVERY session, so this can also present as unexplained global errors.

- **A block shows in the terminal mirror but is missing from the web dashboard's
  stream (or the reverse asymmetry)**: FIRST rule out the VIEW MODE (docs/dashboard.md
  *View modes*, since 2026-07-25) — in `default` the web stream collapses runs of
  read/command/agent blocks into one clickable summary line, and in `focus` almost
  everything folds, so "my commands disappeared from the dashboard" is expected
  there and one click on the summary (or the `verbose` segment) brings them back.
  The mode is per session and durable, so it PERSISTS across reloads and devices:
  `sql "SELECT ts, content FROM state_files WHERE action='web-viewmode' ORDER BY ts"`
  shows every switch (content `{sid, mode}`), and the live value is the `view-mode`
  map in `~/.claude/baqylau-dash-prefs.db` (absent entry = `verbose` = nothing
  hidden). It writes no session state and touches no terminal, which is exactly why
  it leaves no other trace — and note the collapse hides nothing from the OPS
  stream, so if the ops rows are there and the mode is `verbose`, it is a real bug:
  check the op JSON for a `src` field. Stamped ops (`sub:<agent_id>` / `team:<agent_id>` / `codex:<aid>`)
  are dropped by the SESSION-scope web mirror BY DESIGN (main-agent-only; the
  terminal paints everything) — a subagent/teammate/secondary-codex block absent
  there with a correctly-stamped `src` is not a bug, it lives in that AGENT's
  scope (docs/dashboard.md *Agent scope*: click the agent, or add `?agent=<id>`).
  Since agent scope inverts the same filter, the `src` stamp now decides BOTH
  views: an op stamped wrong is missing from one and wrongly present in the
  other. The bug shapes are the stamp being WRONG:
  a main-session op carrying a `src` (a hook process inherited a stray
  `$CLAUDE_OPS_SRC` — check `sessions.env`) hides lead activity from the web; an
  agent-stream op with NO `src` (a tailer spawned outside `stream_env`'s environ
  copy, or a standalone-codex misdetect in `watch.spawn`) leaks agent noise into
  the web stream. `sql "SELECT json_extract(op,'$.src'), producer, count(*) FROM
  ops WHERE session_id='<sid>' GROUP BY 1,2"` shows the per-producer stamp pattern
  at a glance (producer `claude-substream.py`/`claude-codex-stream.py` rows should be
  stamped; `claude-cmd-fmt.py` etc. should not — except `claude-monitor-fmt.py`,
  whose agent-launched monitors are stamped via the explicit `emit(src=)`).

- **An agent's monitors/jobs are missing from the Jobs/Monitors tabs — or a row
  shows with a BLANK command** *(agent attribution, 2026-07-27)*: since agent
  scope, the session-level tabs are the LEAD's own work and an agent's live
  under that agent (click it, or `?agent=<id>`), so "my subagent's 32 background
  jobs vanished from the session tab" is the DESIGN, not a bug. What IS a bug is
  a job/monitor that appears under neither: the *nested job/monitor with no
  resolvable owner* anomaly flags exactly that. Ownership has two sources, in
  order — the tailer's own `streams.agent_id` (`CLAUDE_STREAM_AGENT`, stamped by
  the three nested launch sites: the substream's bg/monitor and live-fg tailers,
  and `monitor_fmt` for an agent-launched Monitor), and, for HISTORY whose rows
  predate that stamp, the launch `hook_events` payload
  (`sessionapi.nested_owners`, which reads `agent_id` + the task id + the command
  together). So: `sql "SELECT kind, task_id, agent_id FROM streams WHERE
  session_id='<sid>' AND kind IN ('bg','monitor')"` — an unstamped row on a
  CURRENT session means a launch site lost the `agent=` argument. A BLANK COMMAND
  is the same fact showing on screen: an agent's bg job paints its `code` op under
  the *tool_use_id* while its stream row is keyed by the *backgroundTaskId*, so
  `core.copy.group_commands` misses it, and an agent's monitor is absent from the
  main transcript `session_monitors` parses — both are recovered from the launch
  hook, so a blank command now means that hook row is missing too (check the
  `PostToolUse` subscriber row for the task).

- **An agent's scoped mirror is EMPTY though the agent clearly worked** — almost
  always PRE-STAMP HISTORY, and expected: agent scope keeps only ops carrying a
  `src` stamp, and ops written before that stamp existed carry none. Confirm in
  one query: `sql "SELECT json_extract(op,'$.src'), count(*) FROM ops WHERE
  session_id='<sid>' GROUP BY 1"` — all-NULL on a session with subagents is the
  gap (the drill-down timeline was the only view of that history and is gone). On
  a session whose ops ARE stamped, an empty scope means the id doesn't match: for
  a subagent/teammate the scope is `sub:<id>`/`team:<id>`, and a CODEX run is
  stamped `<register>:<codex_aid>` — the SAME id, so `read/mirror.agent_scope`
  accepts all three prefixes for it and needs no lookup (a SIDECAR is `codex:`,
  a NATIVE SUBAGENT `sub:`). Ops stamped `codex:<label>` (the display label, not
  the rollout basename) are pre-2026-07-14 history, where an id mismatch really
  did yield an empty scope.

- **An agent's scoped mirror shows its prompts / messages / result TWICE, or its
  Read/Update lines lead with the agent's name** *(pre-field history, 2026-07-27)*:
  one root cause. Agent scope reads the agent's conversation from its own
  transcript and drops the substream's prose OPS in exchange — but it recognises
  a block by the MARKER its text opens with (`⇢ ✎ ⇠ ✉`), and ops written before
  `who` became a FIELD open with the agent's NAME instead. Nothing matched, so
  every prose block stayed in the stream beside the bubble the transcript
  produced. Fixed read-side (`actclass.lead_head` cuts to the marker,
  `streamfmt.strip_who` undoes the coloured body prefix), so seeing it again means
  those ran and failed. One query tells you which era an op is from: `sql "SELECT
  json_extract(op,'$.who'), substr(json_extract(op,'$.s'),1,30) FROM ops WHERE
  session_id='<sid>' AND json_extract(op,'$.src') LIKE 'sub:%' LIMIT 10"` — a NULL
  `who` with the name inside `s` is pre-field, a populated `who` with the text
  opening at the marker is current. The one thing NOT recovered is the
  `opus-5·high  ctx 5%` tags on a pre-field BODY line (they sit at the end with no
  boundary to key on — docs/dashboard.md *Agent scope*, "Known gaps in history");
  seeing tags on a CURRENT session's scoped file line is a real bug (the producer
  baked them into `s` instead of passing `tags=`).

- **One launch shows TWO `Agent "<name>" launched` notes, and only one of them
  expands onto the brief** (reported 2026-07-27; fixed same day): not a duplicate
  hook or a double-spawned tailer — Claude Code opens a subagent's transcript with
  **two `type=user` records**, the brief and then one that is *nothing but* the
  addressable-teammates roster `<system-reminder>`. Both parse as `prompt`, so the
  substream painted a `⇢ prompt` block for each, and the reminder-only one's body is
  stripped away on the web, leaving a note with nothing behind the click. The tell is
  in the ops rows, one query: `sql "SELECT id, json_extract(op,'$.g'),
  json_extract(op,'$.src'), substr(json_extract(op,'$.s'),1,40) FROM ops WHERE
  session_id='<sid>' AND json_extract(op,'$.s') LIKE '%⇢ prompt%' ORDER BY id"` —
  **two prompt labels with DIFFERENT `g` and the SAME `src`** is this shape (read the
  next row after each: one body is the brief, the other opens `<system-reminder>`).
  Current builds emit neither op (`substream_render.render_prompt` returns when the
  strip leaves nothing) and drop the stale pair read-side, so seeing it means the ops
  predate the fix (a parked or long-running session) or the tailer is running old
  code — check `streams.started_at` against the fix. A DOUBLED tailer looks different:
  every block duplicates, not just the launch, and there are two `streams` rows for
  the one `agent_id`.

- **A message the terminal TOOK BACK is still in the web stream** (the user
  cancelled a prompt with Esc-Esc right after sending, or rewound, and the
  dashboard still shows it): this leaves NO audit rows — the evidence is the
  transcript itself (`sessions.transcript_path`), because Claude Code never
  rewrites that file. A discarded turn is dropped by RE-PARENTING around it, so
  the tell is **two `type=user` prompt records sharing one `parentUuid`**: all
  but the last are dead, along with everything descending from them.
  `python3 -c` over the jsonl (print `uuid`/`parentUuid`/`promptId` around the
  ghost) shows the fork in one look; a discarded prompt's text usually also
  PREFIXES the next real prompt, since Esc-Esc hands it back to the TUI input.
  `transcript._dead_uuids` prunes these (docs/dashboard.md, *Discarded
  prompts*), so a ghost that survives means either the fork isn't
  prompt-vs-prompt (only those count — attachments and parallel tool_results
  fork legitimately) or the page never re-read: the live prune is client-side
  (`dropSuperseded` off `data-par`) and a stale open tab keeps the bubble until
  the next full read. Check `web-client` rows for a `boot`/`sse.open` after the
  discard before suspecting the server.

- **A session shows NO messages on the web AT ALL — its own first prompt
  included — and its ⧗ queued chips never drain** *(a take-back flag that ate
  the tree, reported 2026-07-30 on session `7cb52905`, fixed same day in
  `3c93a8c`)*. In `default` view mode this reads as "only the summary line",
  which sends triage at the view-mode collapse; it is NOT that (the collapse
  never folds prompts, and `verbose` shows the same nothing). It is ONE flag:
  the take-back stash (`takeback` kv — the uuid a web interrupt stashed when
  the terminal handed a prompt back, see the shape above) is an ADVISORY
  suspect, and `_dead_uuids` expands every dead uuid over the whole tree — so a
  suspect wrongly held dead prunes the entire conversation descending from it.
  The rescue ("a suspect is dead only while NOTHING descends from it") read the
  PARSED-record view of the tree while the expansion walks RAW lines; the
  suspect's only child was an `attachment` record, which `parse_line` drops, so
  the rescue never saw it and the expansion followed it into everything below
  (130 of 184 records). Two tells, in order: the session was INTERRUPTED from
  the web near its start (a `web-interrupt` row with `phase: "restore"`,
  `restored: true` — its `uid` is the suspect), and the kv is non-empty
  (`sqlite3 <state.db> "SELECT val FROM kv WHERE key='takeback'"`). CONFIRM by
  differencing the stash out — this is the one query that names it:
  `T.conversation(path, 0, T.taken_back(sid))` vs `T.conversation(path, 0)`
  (records 0 vs 15 in the reported case). No audit row says any of this: the
  prune is read-side, so the stash + the interrupt row are the whole trail.
  On a current build a suspect is rescued off the same raw-line child map the
  expansion uses, so a recurrence means those two tree views diverged again.
  NB the STUCK CHIPS are downstream of this, not a second bug — `composer_queue`
  drops a chip whose text matches a DELIVERED prompt, and with the conversation
  pruned to nothing there are no delivered prompts to match (`_delivered_prompts`
  returns []); they drain by themselves once the conversation is whole.

- **The session's FIRST message is missing on the web, and it opened with a
  `/slash-command` or a SKILL — the first bubble is the skill's own SKILL.md
  text** *(the command wrapper read as plumbing, reported 2026-07-30 on sessions
  `63209ded` + `d88abe11`, fixed same day)*. Do NOT spend the take-back shape
  above on this: it presents identically ("my first message isn't there") but the
  audit is CLEAN — no anomalies, no `errors`, **no `takeback` kv and no
  `web-interrupt` row at all**, which is the fastest way to tell them apart
  (`sqlite3 <state.db> "SELECT val FROM kv WHERE key='takeback'"` empty +
  `action='web-interrupt'` absent ⇒ it is this one). There is **no audit row for
  it whatsoever** — the conversation reader is read-side, like the ctx/goal
  probes — so the evidence is the transcript plus the read model, in two steps:
  (1) the session's first `type:"user"` record has content
  `<command-message>…</command-message>\n<command-name>/foo</command-name>\n<command-args>…</command-args>`
  and parses `kind:prompt, meta:False` (a REAL user turn); (2)
  `T.conversation(path, 0)` does not contain it — for a skill, `records[0]` is
  instead the `isMeta` prompt opening `Base directory for this skill: …`. Root
  cause: `_Conv.add_prompt` dropped every prompt whose text starts with `<` as an
  envelope, but the args of a command turn ARE the typed message. Current builds
  unwrap that one shape (`_command_text`, gated on the `<command-name>` tag so
  `<local-command-caveat>`/`<local-command-stdout>` stay dropped), so a
  recurrence means that gate or the unwrap regressed — check the running
  dashboard is past the fix FIRST, since it does not hot-reload. Related, same
  record: a title showing a bare `/foo` where the command had arguments is the
  newline-free `_CMD_ARGS_RE` (multi-line args matched nothing).

- **"I stopped it and my message vanished" / "the web composer stayed empty
  after a stop"**: stopping a turn EARLY makes Claude Code discard the prompt
  and hand it back to the terminal's input box — a take-back, not a bug, and
  the same outcome the retired ⊘ cancel button chased with two Escapes
  (docs/dashboard.md, *Interrupt*). The evidence is a SECOND `web-interrupt`
  `state_files` row with `phase: "restore"`: `restored: true` = the server read
  the box, matched it against the last prompt, and returned the text for the
  composer; `restored: false` = the box held something else (the user's own
  terminal draft) and was deliberately left alone; NO restore row at all = the
  box was empty (a plain stop, work kept) or the probe raised — check `errors`
  for `dashboard web-interrupt (restore probe)`. The same row's `uid` names the
  record, and `flagged`/`noted` say the two kv writes landed — a False pair
  also raises an `errors` row `dashboard web-interrupt (take-back stash)`.
  Those writes go through `kv_set_at` because `kv_set` from a request THREAD
  silently writes nothing and returns False (the dashboard is a
  ThreadingHTTPServer); a random-looking reappearance with `flagged: true` on
  a pre-2026-07-25 build is that bug. **If the bubble REAPPEARS after a reload,
  those flags are what to check** — until the
  replacement message arrives the prompt has no sibling, so the kv is the only
  thing that knows (`bin/claude-audit.py sql` the row, then read the kv). A
  take-back also leaves the prompt orphaned in the transcript once the next
  message lands (see the ghost-message shape above), so the rows read together
  tell the whole story of one Escape.

- **"I hit stop and my QUEUED message disappeared" / "stop killed the message
  I'd queued instead of running it"** *(the interrupt's re-press loop vs. a
  queued delivery, fixed 2026-07-27 — docs/dashboard.md *Interrupt*)*: pressing
  Esc with a message queued does NOT idle the session — Claude Code delivers the
  queued prompt the instant the turn ends, so the stop hands the session over to
  your message. That makes the screen go on animating, and the screen-DELTA
  liveness the loop uses reads it as "the Esc missed" and presses again — which
  interrupts the freshly delivered turn, and since it has produced nothing, its
  prompt is DISCARDED into the TUI's input box, invisible to the web (no ⧗
  drain, no composer prefill: the restore probe matches against the last
  *transcript* prompt and a queued message never became one). Evidence, in
  order: a `web-send` row with `queued: true` (the enqueue), then a
  `web-interrupt` press row — read **`attempts`** and **`drained`** together.
  `drained: "dequeue"`/`"queued_command"` with `attempts` small = healthy (the
  loop saw the queue boundary in the transcript and stopped); `attempts` > 1
  with `drained: ""` on a session that had a message queued IS the bug shape
  (the canned anomaly *stop re-pressed with a message QUEUED…* flags exactly
  this). Confirm in the transcript: a `{"type":"queue-operation","operation":
  "dequeue"}` record with NO delivered prompt behind it, and the user re-sending
  the same text minutes later — often DOUBLED, because the take-back left the
  message in the box and `clear_draft`'s Ctrl+U/Ctrl+K only kills one line (see
  the glued-message shape below). Session `3266f418` (2026-07-27) is the
  reference case: 4 Escapes, 94 chars queued, re-sent by hand. A row with no
  `drained` key at all is a pre-fix build.

- **A composer draft appeared / vanished on its own**: since 2026-07-25 the
  `composer-draft` kv has a SECOND writer — the terminal→web sync mirrors what
  the user typed in the kitty `❯` box (docs/dashboard.md, *Terminal draft
  sync*). `composer-draft` rows carry `action`: `write`/`clear` are a page's own
  save, **`terminal`** is the sync, `stale` is a seq-rejected straggler. So "a
  draft I didn't type" is answered by a `terminal` row, and the draft record's
  `origin` field (`terminal` vs a page's client id) says the same thing live. A
  draft VANISHING should never be the sync's doing unless the box was emptying
  text it had itself synced — the clear is deliberately one-directional — so a
  `terminal` row with `chars: 0` next to a draft the user typed elsewhere is a
  real bug, not the design.

- **A web-sent message arrived with the PREVIOUS one glued to its front**
  (`testingtesting2`): the input box still held a message the web had put there
  — an interrupt's take-back or a rewind restore — and the send pasted after it
  instead of replacing it. The `web-send` row says which: `clear_draft: false`
  with a take-back (`web-interrupt` `phase: "restore"`) shortly before it is the
  bug's signature. Since 2026-07-25 the server owns that fact (`tui_draft` on
  the same row = it knew the box was dirty and cleared the line first), so a
  recurrence means the `tui-draft` kv write or read failed, not that the page
  forgot — the old failure was exactly the page forgetting across a reload.
  A `clear_draft: true` row that STILL glued — but only the draft's FIRST
  line(s), its last line missing from the glue — is the MULTI-LINE variant
  (session 8b9f870b, fixed 2026-07-29): Ctrl+U/Ctrl+K kill one line and the
  take-back held several, so only the line under the cursor died. The fix
  kills per line off the stash's newline count; the row's `draft_lines` says
  what ran — a multi-line take-back whose resend row reads `draft_lines: 1`
  (or has no `draft_lines` key) is a pre-fix server (restart
  `claude-dashboard.py`). The transcript tell is the same re-parented fork as
  the take-back shape: two prompts sharing one parentUuid, the delivered one
  = the old draft minus its cursor line + the new text.

- **A web rewind failed with `step: "open"` ("checkpoint menu never appeared")
  with NO stray chat message**: suspect MARKER DRIFT before anything else — the
  menu very likely DID open and `menu_open` stopped recognizing it. The paired
  `errors` row (func `dashboard rewind-to (open)`) carries a clipped `screen`
  (the capture the step gave up on — its context column, stored untruncated):
  if it shows the `Rewind` list, the detector is stale, not the terminal. TWO
  measured instances: the FOOTER (Claude Code composes it at runtime —
  `<chord> to <action>`, chord label ∈ `Enter`/`enter`/`⏎` — so a version bump
  changes it without changing any literal in the binary; v2.1.220 broke the
  title-case match, fixed 2026-07-25 by matching only the `to continue` tail),
  and the HEADER (the TUI pads the row — `"  Rewind \n"` with a trailing
  space — which the byte-exact `menu_region` anchor missed, fixed 2026-07-29
  on session 69caa362 with a whitespace-tolerant line match; the tell was a
  `screen` showing BOTH the menu AND the healthy footer). Re-measure against a
  live window; never guess a new marker. `errors` rows from before 2026-07-25
  carry no `screen`, so they can't be told apart. NB the screen was BRIEFLY
  (2026-07-25 → 2026-07-29) also duplicated onto the `web-rewind-to`
  state_files row, where it blew `A.state_file`'s 2000-byte content cap: such
  a row is truncated MID-JSON on disk, and it used to abort the whole
  `anomalies` run with `sqlite3.OperationalError: malformed JSON` at the first
  `json_extract` section — the CLI now guards `state_files.content` extracts
  with `json_valid` (unreadable rows are skipped) and degrades a failing
  section to a `QUERY FAILED (…)` line instead of dying, so on a current build
  that crash means a NEW unguarded `json_extract` was added.

- **A web rewind failed with `step: "open"` AND a nonsense fragment appeared as
  a chat message**: the classic shape is
  a rewind (or any slash command) issued shortly AFTER a web interrupt. The
  interrupt presses Escape, `editorMode: vim` makes the input box modal, and a
  typed `/rewind` in NORMAL mode is vim COMMANDS — the menu never opens and the
  keystroke tail gets submitted (observed as the message `nd`, 2026-07-25).
  Look for a `web-interrupt` row shortly before the failing `web-rewind-to`
  row, and check the transcript for a short junk prompt between them. Fixed by
  pasting every slash command (`tui.type_command`, docs/dashboard.md *Slash
  commands are pasted*), so a RECURRENCE means something reached the TUI as
  typed text again — check whether the failing site calls `send_text` with a
  `/…` string.

- **No Telegram alert for a session left red/green on the dashboard** (docs/dashboard.md
  *Presence routing* / *Telegram alerts* — the off-device notification): since 2026-07-28
  the alert fires ON THE TRANSITION TICK (`CLAUDE_DASH_NOTIFY_DELAY_S` default **0**, no
  grace window — a build still waiting 60s is an old process, restart it) and goes to the
  device your PRESENCE says you were last at, so Telegram is the channel for the TERMINAL,
  not a late nudge: a `telegram-notify` row with `reason: terminal` IS the normal
  same-second alert, and it arms no escalation (Telegram already reaches every device you
  own, so a 5-min duplicate says nothing new). Look for a `state_files` `telegram-notify`
  row (`{sid, kind}`)
  for the sid: PRESENT = it fired (a missing Telegram message past that is the notify
  script's own transport, outside this audit — or a paired `errors` row func
  `dashboard telegram notify` if the Popen raised). ABSENT = it never armed/fired — check,
  in order: **presence routed you to a BROWSER instead** — read the `notify-route` row
  (it always exists at stage 1, naming the `target` and every candidate's `age_s`): a
  browser target means a `web-push` went out and Telegram only ESCALATES
  `CLAUDE_DASH_ESCALATE_S` later — default 300s — if you STILL did nothing with the session.
  So no `telegram-notify` within 5 min of a `web-push send` is EXPECTED — you either acted
  (the arm was cancelled) or it hasn't escalated yet. A reaction/look drops the arm, so a
  Telegram nudge means genuine inaction. `CLAUDE_DASH_NOTIFY_TELEGRAM_ALWAYS=1` fires both
  at once, no escalation wait; with NO subscription at all Telegram is the immediate
  stage-1 fallback); the session's
  `notify-muted` state — since 2026-07-25 a mute that swallows an armed alert says so DIRECTLY, a `notify-suppress` `reason: muted` row at send time (before that it dropped silently, so you had to infer it from the `notify-mute` `state_files` row or the `notify-muted` map in `~/.claude/baqylau-dash-prefs.db` — still the check for an alert armed by an OLDER build); `CLAUDE_DASH_NOTIFY_TELEGRAM=0`
  (env master off); **the GLOBAL alerts toggle is OFF** — the list-page `#notifytoggle`
  master switch (a `notify-global` `{enabled:false}` state_files row is the last write to the
  `notify-enabled` key in `~/.claude/baqylau-dash-prefs.db`): when off it short-circuits
  the ONE `Notifier.scan()` transition site, so NOTHING arms (no toast, no Telegram, no
  web-push) and EVERY red/green transition writes a `notify-suppress` `reason: global-off`
  row — a machine-wide switch that overrides the per-session mute, so "no alerts on ANY
  session" points here first; whether you REACTED within the delay (the tab left red/green — an arm is
  cancelled the moment the state moves, so a quickly-answered/closed session correctly
  gets none); whether you reacted AT THE TERMINAL without moving the tab OR were simply
  looking at the session — a `notify-suppress` `state_files` row names which: `reason:
  global-off` (the GLOBAL alerts toggle was off at transition time — see the master-switch
  cause above; written at the transition, before any arm) / `reason:
  dialog-activity` (a red `asking` arm — you edited the AskUserQuestion dialog) /
  `terminal-input` (a green `done` arm — you typed into the `❯` box, `suggestion.typed`
  saw real non-faint text) / `tab-focused` (the kitty tab was frontmost) /
  `web-viewing` (a browser was viewing the session — a fresh `/api/presence` beat carrying
  the sid, or the legacy `/api/session/<sid>/viewing`) / **`device-active`** (since
  2026-07-28: a browser reported itself visible+focused, so the in-page toast already told
  you — this one is machine-wide, NOT per-session, and is the new #1 cause of "no alert
  anywhere": an iPad left awake with the dashboard in front beats forever and suppresses
  every session. `anomalies` counts these; a whole session's alert history being
  `device-active` rows is the bug, a handful is you genuinely watching); and the dashboard
  actually running (no open `kind='dashboard'` streams row = the notifier isn't polling at
  all). Note a `done` arm is CANCELLED by any of those looks, while an `asking` arm is only
  HELD — a `notify-arm` `phase: hold` row (with the `reason`, written once per arm) means
  the alert is still pending and will go out the moment you stop being there, so an alert
  that arrives minutes late with no matching transition is explained by its hold row, not
  by a delay. The reverse — an alert you DIDN'T want — is the per-session mute (the 🔕
  toggle), or presence failing to notice you: check the `notify-route` candidates' `age_s`.

- **No on-device (iPad/desktop) push notification** (docs/dashboard.md *Web push* — the
  ON-DEVICE twin of the Telegram alert, same deferred fire point, same suppress + mute
  predicates): it is a SEPARATE channel, so first split the question. Does a `state_files`
  `web-push` `action: subscribe` row exist for the device? ABSENT = the browser never
  subscribed — the #1 cause on iPad is opening a plain Safari TAB instead of the INSTALLED
  home-screen app (iOS exposes `PushManager`/`Notification` only in a standalone web app);
  others: permission not granted (the enable-notifications button), `webpush.enabled()`
  False (no `cryptography` backend — `GET /api/push/config` reports `enabled:false`), or
  `CLAUDE_DASH_NOTIFY_WEBPUSH=0`. PRESENT but still no banner → for a WRONG/UNEXPECTED
  device, read the **`notify-route`** row (since 2026-07-24; written for EVERY alert since
  2026-07-28) for that sid: it names the
  chosen `target` device AND every `candidate` with its presence `age_s`, so "why the iPad
  and not the Mac" is answerable — the routed device is the one whose `/api/presence` beat
  was freshest (the smallest `age_s`). `target: "terminal"` is the reserved id for the
  KITTY TERMINAL, which competes as an ordinary device (its beat is the notifier's own
  `app_focused` poll, every ~5s while kitty is frontmost) and is reached by Telegram, not
  push — so a Telegram alert where you expected a push means you were at the terminal more
  recently than at any browser. Ties go to the browser. Then the `action: send` rows (each
  carries the target `device`) confirm delivery. `legacy:true` in notify-route = untagged subs
  (pre-routing client) → sent to all. To name a device id, cross-map it to the frontend
  `boot` row's `device`→`dlabel`. NONE despite the arm firing = suppressed/muted upstream
  EXACTLY like Telegram (same
  `notify-suppress`/`notify-muted` evidence as the shape above — the channels share the
  arm), OR no device is subscribed at all (then Telegram fired instead — see the shape
  above); `ok:false` = the push service rejected the
  send (`status` names it — a 401/403 is a VAPID/JWT problem, a 5xx transient); `gone:true`
  = the subscription was pruned (browser cleared/uninstalled it → the device must
  re-subscribe, which happens on next load if permission is still granted). A `send` row
  with `ok:true` but no visible notification is iOS/OS-side (Focus/DND, notifications
  disabled for the installed app) — past the audit's reach. The VAPID keypair
  (`vapid-keypair` in `~/.claude/baqylau-dash-prefs.db`) must stay stable — if it were
  rotated, every prior `subscribe` silently orphans and all sends `ok:false`. For the
  IMMEDIATE in-page toast (a different channel than push — SSE, only the focused device
  shows it) the frontend witness is the `web-client` `notify.recv` row: `shown:false`
  (with `vis`/`focus`) on a device = it received the toast SSE but gated it because you
  weren't looking there; NO `notify.recv` from a device = its page wasn't connected. The
  whole lifecycle is anchored by `notify-arm` (`phase:arm` on the transition, `phase:hold`
  when a look defers an `asking` alert, `phase:escalate` when the push arms the Telegram
  nudge).

- **"I hardly ever get a native `done` notification, and the ones I do get vanish"**
  (docs/dashboard.md *The settle window* / *Presence ends when the page says so*, both
  2026-07-29). Do NOT read this as a delivery failure before checking the two POLICY
  causes — the wire is usually perfect (`web-push send` rows all `status:201, ok:true,
  gone:false` was the reported case). Ask the two questions separately:
  * **Why so few?** Count `notify-suppress` rows against `web-push send` rows for
    `kind:done`. A large suppressed majority is the design working (you were looking) —
    EXCEPT where nothing reached you at all, which is the bug that was fixed: join each
    suppress to the `web-client` `notify.recv` beacon within a few seconds of it and read
    `shown`. **`shown:false` with `focus:false` next to a `web-viewing`/`device-active`
    suppress IS the presence-lag shape** (measured 20 of 99): the page had already stopped
    toasting while the server still honoured a beat inside `CLAUDE_DASH_VIEW_TTL_S`. On a
    current build the page posts `away:true` on blur/hide, so a recurrence means that
    listener or `presence.mark_away` regressed — check the browser's `boot` row for stale
    cached JS first, since the fix is half client-side. A suppress with NO `notify.recv`
    row at all is the same class with the page gone entirely.
  * **Why do the ones that arrive vanish?** Read `notify-retract` `age_s` for `kind:done`,
    `reason:tab-moved`. A MEDIAN in the ~15s range is the pre-settle shape: the alert
    fired the instant the turn ended and the session was busy again before the banner
    settled, so the retraction correctly deleted a notification never seen. The fix is the
    SETTLE window, not the retraction — on a current build those alerts are never sent
    (the arm is cancelled during the settle and leaves no send at all), so a fresh crop of
    sub-20s `tab-moved` retractions means `CLAUDE_DASH_NOTIFY_SETTLE_S` is 0 / the server
    predates it. Cross-check the `notify-arm` row's `delay_s` (20 on a `done` arm = the
    settle is on).

- **A Telegram message / iPad banner that never went away after I dealt with the session**
  (docs/dashboard.md *Alert retraction* — since 2026-07-27 a delivered alert is TAKEN BACK
  once the session stops needing you: the message is `deleteMessage`d, a resolve push
  closes the banner). **Check the credentials FIRST** — the canned anomaly **"Telegram alert
  sent that can never be retracted (no bot credentials)"** (`telegram-notify` with
  `retractable:false`). Without `~/.config/telegram/{bot-token,chat-id}` the send degrades to
  the detached `notify` script, whose `message_id` goes to DEVNULL, so NO Telegram message
  can ever be deleted and no `notify-retract channel:telegram` row will ever exist. This was
  the cause the first time it was reported (2026-07-28), it is silent, and it disables the
  headline behaviour — so rule it out before suspecting the retraction machinery. Fix: create
  the two files AND restart the dashboard (they are read per call, but the handles for
  already-sent alerts live in the old process's memory, so in-flight alerts stay unretractable).
  Then read the **`notify-retract`** row for the sid (global rows — match
  `json_extract(content,'$.sid')`, or run the canned anomaly **"off-device alert left
  behind (notify-retract not ok)"**), in this order:
  - **A row with `outcome: failed`** = we tried and the wire refused. For `channel:
    telegram` that is the Bot API (a >48h-old message can no longer be deleted —
    `telegram.DELETE_WINDOW_S`; check `age_s`); for `channel: webpush` the paired
    `web-push` `action: resolve` row carries the `status`/`gone`.
  - **`outcome: expired`** (`reason: ttl`/`capped`) = nothing ever resolved it inside
    `CLAUDE_DASH_RETRACT_S` (default 24h). Legitimate if you really did leave the session
    red for a day; suspicious otherwise — it means the tab never moved off its alerted
    state, so cross-check `tab_transitions`.
  - **`outcome: ok` but the banner is still there** = the retraction ran. For Telegram
    that's conclusive (the message IS gone). For web push it is NOT: `ok` only means the
    resolve was DISPATCHED, and iOS may have refused to act on a push that shows nothing.
    Expected recovery is the page's foreground sweep on next open.
  - **NO row at all** — three readings, in order: (1) the send was never retractable —
    check the `telegram-notify` row's **`retractable`** flag; `false`/`transport:"script"`
    means the legacy `notify` skill sent it and the `message_id` went to DEVNULL, so
    nothing could ever delete it (fix: configure `~/.config/telegram/{bot-token,chat-id}`);
    (2) the dashboard RESTARTED between the send and your reaction — handles live in
    memory like `pending`, so a restart strands every tracked delivery and leaves no row;
    (3) nothing has resolved it yet (the tab is still red/green — the alert is still TRUE).
    **For a `done` alert, reading (3) has a fourth sibling worth checking first: is the
    dashboard running code from before 2026-07-29?** Until then NO look retracted a
    delivered alert, and a green tab never moves on its own — so "I opened the session
    from the push and the message stayed" was the guaranteed outcome of every `done`
    alert you resolved by simply reading it (reported 2026-07-29 on session `1259df34`;
    the tell is a `notify-arm kind:done` + `telegram-notify`/`web-push send` with no
    retract row, while the tab sat on `awaiting-response` for the rest of the session and
    OTHER alerts in the same session retracted cleanly on `tab-moved`). The server does
    not hot-reload; check `bin/claude-dashboard.py status` against the fix before
    triaging further.
  - **The reverse — an alert vanished before I got to it**: check the `reason` AND the
    `kind` together, because the rule is per-kind (`notifier.retracts`). On an `asking`
    alert it should only ever be `tab-moved`/`session-ended`/`composing` — a glance
    (`tab-focused`/`web-viewing`) appearing there is the SEEN_REASONS table having leaked
    across kinds, a real regression and not a tuning question: looking at a question is
    not answering it, so a look must cancel a PENDING asking alert and never delete a
    DELIVERED one. On a `done` alert a glance reason is CORRECT and expected (you read the
    final message). `device-active` on either kind is a regression — it names no session.

- **A web/`/rename` reverts after a while — the session shows its auto title
  again** (docs/session-naming-findings.md §4 + *fallback ladder*,
  docs/dashboard.md *Web rename*). **Read the `web-rename` row's `channel`
  first**, it splits this into two different bugs:

  **`channel: "tui"` or a LIVE session (current build).** The rename is Claude
  Code's own `/rename`, so a title that hasn't moved is usually not a revert at
  all: check **`queued`** — `true` means the command is in the TUI's message
  queue and applies at the TURN BOUNDARY (and never, if the user Escaped the
  queue out; the page deliberately shows the old name until the `title` SSE
  lands). `ok:false` + an `errors` row `dashboard rename (send failed)` = the
  paste never landed. `reason: "dialog open"` = refused, a modal was up. If the
  paste landed on an IDLE tab and the name still never changed, the TUI rejected
  the command itself — that leaves no audit row by design (the TUI stays
  authoritative), so read the transcript for the `/rename` turn.

  **NO `channel` field = a PRE-2026-07-29 server, and this is THE bug it had**
  (found on session `6ad5823e`; a dashboard that does not hot-reload can still
  be running it — check `bin/claude-dashboard.py status` against the fix). That
  build APPENDED the `agent-name` record on the live path too, and Claude Code
  re-emits its own in-memory `agent-name` at every turn boundary — so the
  appended record was clobbered within one turn, AND the re-emitted record then
  stood the durable override down (the reconcile drops it whenever the tail
  carries any `agent-name`, a rule written when only a rename could write one).
  The signature is a row with `ok:true` **and** `override:true` on a session
  that reverted anyway, following an earlier ✦ auto rename (a `web-command`
  row with `cmd:"rename"`) — the auto rename is what gave Claude Code a name to
  re-emit. Confirm from the transcript, which is the only place this is
  visible: list every naming record with its byte offset and look for the manual
  name appearing ONCE, followed by the auto name repeating to EOF. The two live
  channels agree with the transcript and not with the dashboard: `kitten @ ls`
  shows the *window* title (Claude Code's OSC) still on the auto name, and
  `json_extract(payload,'$.session_title')` on the session's `UserPromptSubmit`
  `hook_events` rows — the cheapest read of Claude Code's authoritative in-memory
  name — reports it too. The kitty TAB showing the manual name is NOT a
  contradiction: that build set it with a sticky `set_tab_title`, a write-once
  channel that never re-reads (which is exactly why the tab and the dashboard
  could disagree).

  **`channel: "transcript"` (a PARKED rename).** The original tail-window
  rollback still applies here and only here: that record is written once, and
  once it scrolls past `TITLE_TAIL_B` (64KB) behind EOF the ladder drops to the
  newest `ai-title`. `override:true` means the durable, tail-window-proof
  override was recorded (`renamed-title` map in
  `~/.claude/baqylau-dash-prefs.db`, keyed by the transcript `.jsonl` stem), so
  a still-reverting DASHBOARD title means the reconcile mis-fired; a row with no
  `override` field at all is a pre-2026-07-22 rename (re-rename to seed it).

  In every case the `--resume` picker does a full read and keeps whatever the
  last record says, so "the picker is right but the dashboard is wrong" narrows
  it to the reconcile, not the rename.

- **An OPTIMISTIC web UI stays GREYED and never resolves** — a composer message
  stuck as a grey stand-in, the ask/plan card stuck "submitting…/sending…", or a
  session list-card stuck "closing…" (docs/dashboard.md *Optimistic UI & the
  web-hint audit*). All four are client-only DOM shown the instant the user acts
  and reconciled by a REAL async SSE confirmation; each lifecycle is beaconed as
  `web-hint` `state_files` rows carrying an **`op`** (composer | close | answer |
  plan) + `phase` (shown | reconciled | dropped | stale). A **`phase='stale'`**
  row (the "optimistic web action never reconciled" anomaly) is the stuck signal
  — the ~20s watchdog fired with no `reconciled`. Read `op` to know which, then:
  - **`op=composer`** — the prompt stand-in never matched its transcript prompt.
    (1) the send never went out: check the paired `web-send` row (`ok:false` / an
    `A.error` "dashboard message (send failed)"). (2) it landed but the reconcile
    text-match missed: `web-send` `ok:true` with the prompt visibly in `msgs`
    means shown≠recorded text (attachments prepend `@path\n`, or whitespace) so
    `real===shown || real.endsWith("\n"+shown)` failed.
  - **`op=close`** — the tab never parked: the sessions snapshot kept the sid
    live. `web-stop` rows carry a **`phase`**: an `attempt` (written before
    close_tab) then a `done` (`ok:false` = the HUP didn't issue). A lone
    **`attempt` with no paired `done`** (the "dashboard close entered but never
    completed" anomaly) = `close_tab` HUNG and never returned (an unbounded
    kitten socket connect) — the session stays live and the client's greyed
    'closing…' hangs to its 20s watchdog; workaround is closing the kitty tab
    manually (Cmd+W). NO `web-stop` row at all (only the `web-hint`) means the
    POST never reached `post_stop`; the **`web-client` `close.*` rows are now the
    primary evidence** — read them first (`sql "SELECT ts, content FROM
    state_files WHERE session_id='<sid>' AND action='web-client' ORDER BY ts"`):
    - **`ev:"js.error"` rows (check FIRST)** with NO `close.begin` = the ✕ click
      handler THREW before `closeSession` ran — it builds `S.closePend[sid] =
      optPending(...)` then calls `closeSession` after, so an uncaught handler
      exception (the shipped-once uninitialized `S.closePend` → a TypeError at
      `app.js:<line>` firing on every sessions tick; the `optPending` that ran
      first still leaves the `web-hint shown`+`stale`) means /stop is NEVER sent.
      That is a CLIENT JS bug (fix + a `test_app_js_initializes_close_state`-style
      static guard), NOT transport — the whole original "still not closing" saga;
    - a **`close.begin` with a paired `close.ok`** = the /stop landed and returned
      200 (so the tab-close side is where to look, not transport);
    - a **`close.fail kind:http`** = the server rejected it (a `web-reject` on
      `/…/stop` = the guard bounced it — missing header / cross-origin /
      read-only, read its `why`; or a handler 409/502);
    - a **`close.fail kind:transport aborted:true`** = OUR `CLOSE_POST_MS` timeout
      fired — the fetch genuinely hung (a `web-clientfail gesture:close` pairs it);
    - a **`close.begin` with NEITHER `.ok` nor `.fail`** = the request left the
      page but no response ever came AND the timeout didn't fire — a tunnel /
      upstream drop. THIS was the long "still not closing" bug: it was made WORSE
      by routing the close through `navigator.sendBeacon` (which returns `true`
      when merely queued, then the tunnel silently dropped the queued beacon —
      no `close.fail`, no `web-stop`, no `web-reject`). The close is back on the
      plain-`fetch` channel (`postJSON`, the transport PROVEN to traverse the
      tunnel — `/hint-audit` + `/message` ride it and land), tagged
      `audit:"close"`. A recurring `close.begin`-only shape through the tunnel
      while `127.0.0.1:8377` (no proxy between) closes fine points at the
      PROXY→127.0.0.1 upstream pool (proxy config), not the app — cross-check the
      batch's `conn.es` (SSE streams held open) and any `sse.drop` rows near the
      close time for the connection-starvation signal.
    Also NEITHER can be the LAUNCH TAG-RACE (a just-launched session
    rendered `live:true` with a blank `kitty_window_id`, so the composer locked
    and the ✕ close button never rendered until a reload; fixed by showSession's
    meta re-fetch, docs/dashboard.md *The launch tag-race*). Also check whether a
    SessionEnd/park ever landed (`sessions.ended_at`).
  - **`op=answer` / `op=plan`** — the answer/decision was driven but the modal
    stash never dropped (no SSE `ask`/`plan` clear to swap the card away). Check
    the paired `web-answer`/`web-plan` row for `ok:true` and whether the
    answer's/approval's PostToolUse fired (the stash-drop hook).

  A **`shown` with no later `reconciled`/`dropped`/`stale`** means the page was
  closed/navigated before it resolved (leaveSession disarms the ask/plan/composer
  watchdogs; close pends keep reconciling from the poll) — not a bug. `dropped`
  (`reason: queued | send-failed | failed | <dialog step>`) is the clean teardown
  (a queued send, or a failed POST). `reconciled` carries `wait_ms` (the
  shown→confirm latency) — a large distribution is "the grey lingers too long"
  perception, not a stuck state. Pull them with `sql "SELECT ts, content FROM
  state_files WHERE session_id='<sid>' AND action='web-hint' ORDER BY ts"`.
- **ANY web control gesture that "didn't land" (a send/command/rename/rewind/
  answer/migrate/close that seemed to do nothing) — read the `web-client`
  frontend audit** (docs/dashboard.md *Frontend audit (clientlog)*). It is the
  browser reporting what IT did with a request the server may never have seen —
  the transport truth beneath `web-hint`/`web-clientfail`. Every tagged gesture
  logs `<gesture>.begin` → `.ok` (ms + `status`) or `.fail` (ms + `kind`
  http|transport + `status`/`error` + `aborted` for a timeout). The decisive
  read: a **`.begin` with no `.ok`/`.fail`** = the request left the page but no
  response returned (a tunnel/upstream drop — invisible server-side, which is why
  it's the browser's job to record it); a `.fail kind:http` pairs a server-side
  reject row; a `.fail kind:transport` pairs a `web-clientfail`. The batch's
  `conn` snapshot (`online`, `view`, `es` = SSE streams held open, `conn` = global
  stream up) plus `sse.open`/`sse.drop` rows are the connection-health context;
  `js.error`/`js.reject` rows catch a handler that threw (a formerly silent
  product bug); the per-load `boot` row carries the `origin` (`127.0.0.1` vs the
  tunnel — the axis that mattered for the close bug). Pull with `sql "SELECT ts,
  content FROM state_files WHERE session_id='<sid>' AND action='web-client' ORDER
  BY ts"` (session-less events — `boot`, a launch — are under `session_id=''`).
- **A "send failed" (or "resume failed") toast appeared but the message
  actually WENT THROUGH — the `web-send` row even reads `ok:true`** (docs/dashboard.md
  *Client-observed send failures*, since 2026-07-22): the toast is a purely
  CLIENT-side reaction to the send `fetch` PROMISE rejecting, and the server audits
  the `web-send` row + returns 200 BEFORE that response reaches the browser — so a
  response LOST in transit (a dashboard restart, a tunnel/proxy reset, a dropped
  connection, a slept laptop) toasts a failure over a send that SUCCEEDED. This
  used to be audit-invisible (server-side auditing is pre-response). Now the page
  beacons what IT saw as a **`web-clientfail`** `state_files` row: `gesture`
  (send | resume), `kind` (**`transport`** = the fetch itself rejected — the
  request or its response never completed, the audit-blind class | **`http`** =
  the server returned an error status, so a paired `web-send ok:false` /
  `A.error "dashboard message (send failed)"` row DOES exist), `error` (the toast
  text), `status` (on `http`), `chars`. Triage: a `web-clientfail kind:transport`
  at the same second as a `web-send ok:true` IS the lost-response case — the
  message went through, no resend needed (this session's shape: 3× `web-send
  ok:true`, bubbles `reconciled`, yet a failed toast). A `kind:http` points you at
  the paired server-side failure row for the REAL refusal (503 no-terminal / 409
  no-window / 409 modal-blocked / 502 send-failed). Because the beacon rides the
  SAME tunnel that may have failed (best-effort, `.catch(()=>{})`), a MISSING
  `web-clientfail` row for a failed toast the user swears they saw is itself the
  tell of a TOTAL outage (nothing could reach the server) — pair it with the
  `dashboard` `streams` rows (a server restart at that ts) and the browser
  console. Pull them with `sql "SELECT ts, content FROM state_files WHERE
  session_id='<sid>' AND action='web-clientfail' ORDER BY ts"`.
- **"Dictation is SLOW — the words lag behind my voice"** (docs/dashboard.md
  *Dictation lag*). Do NOT triage this off the `web-dictate` mint rows: the
  server trades a token and **never sees the audio stream**, so the mint rows
  can only tell you dictation STARTED. The evidence is the `web-client`
  frontend audit, where the browser splits the delay in two — both in
  seconds-of-audio, and they ADD UP to what the user sees:
  - **`queue_s`** (audio stuck in the page's own `ws.bufferedAmount`) = a
    saturated **uplink**, OURS. Its signature is that it **GROWS** across the
    `dictate.lag` samples of one session — that compounding is the whole tell,
    because a slow API is a roughly CONSTANT delay. A `dictate.backlog` row
    (one-shot, past `DICT_BACKLOG_WARN_S`) is the same fact called out loud.
  - **`svc_s`** (audio the network took that Deepgram hasn't accounted for,
    measured against Deepgram's own `Results` audio clock) = **theirs**. Flat
    `queue_s` with a large `svc_s` is a genuine Deepgram problem; there is
    nothing to fix on this machine.

  **"The mic takes forever to be READY" is a THIRD question** — not lag, not
  the mint. Read `arm_ms` on `dictate.start`/`dictate.stop`: that is press →
  capturing, and it should be the mic-permission grant alone (order of
  100ms), because capture no longer waits on the token or the socket
  (docs/dashboard.md *Instant-on mic*). `open_ms` being much larger is
  EXPECTED and harmless — the preroll covers it, and `preroll_s` says how much
  speech it covered. An `arm_ms` that tracks `open_ms` means capture went back
  to waiting on the connection, i.e. the three-leg parallel start regressed to
  a chain. A `dictate.stop` with `open_ms: 0` means the socket never came up
  at all (the mint failed or stop beat it).

  Also check `dictate.start` `{rate, native}`: `rate` must be 16000 whenever
  `native` is above it — a `rate` equal to a 44100/48000 `native` means the
  worklet's resampler never engaged on that device, which is the 2026-07-27
  regression (768 kbps of uplink) coming back, and the paired `web-dictate`
  mint row will show the same wrong `rate`. `dictate.stop` closes each session
  with `{spoke_s, max_queue_s, max_svc_s}` — the MAXIMA, which is what makes
  one dictation comparable to the next. Pull with `sql "SELECT ts, content FROM
  state_files WHERE action='web-client' AND content LIKE '%dictate.%' ORDER BY
  ts DESC LIMIT 40"` (no sid filter — the new-session form dictates under
  `session_id=''`).
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
- **Codex tab stuck magenta/blue after a turn.** Codex fires NO Stop on interrupt (its only cancel trace is a `turn_aborted` RECORD in the rollout). Recovery is the detached `codex-interrupt-watch` stream — check its `end_reason`: `interrupt-detected-flipped-green` = it recovered; `turn-over` = the turn actually ended via Stop; `no-interrupt-within-30m` / `killed-or-crashed` / `session-parked` = the watcher gave up or died (row may be left open → also shows in 'streams that never ended'). If a `task_started` + `user_message` followed the `turn_aborted`, it was a STEER (queue+Esc delivered the queued prompt) and the new turn correctly owns the tab — not a bug. Separately: a `codex-*` tab transition (`dispatch LIKE 'codex%'`) with NO `codex-session` hook_event whose decision starts `standalone-open` = the NESTED-GUARD leak (a codex-inside-Claude `codex exec` run repainted the Claude host's tab) — the anomaly 'codex tab painted with no standalone codex host (nested-guard leak)' fires on exactly this. The standalone-vs-nested bit lives in the tab DB `codex_hosts` table (core/tabs.py), recorded once at a non-nested codex SessionStart. **A THIRD stuck-magenta shape (fixed 2026-07-30): a codex SUBAGENT's `SubagentStop` repainting the resting tab.** A codex subagent (cli 0.146+ `collaboration.spawn_agent`) fires `SubagentStart`/`SubagentStop` hooks that BOTH carry the child's `agent_id`, and the SubagentStop can arrive AFTER the turn's real `Stop`. TELL: a `codex-subagent` tab transition (`dispatch='codex-subagent'`, hook `SubagentStop`) flipping `awaiting-response → working` timestamped just AFTER a `codex-stop` transition, with no further event to clear it. This is a REGRESSION signature — the codex producer must ignore any event carrying an `agent_id` (the shared `core/tabpaint.agent_inner_event` doctrine; the hook_event decision now reads `codex: agent_id inner call — main tab untouched` and NO tab transition is written). Seeing a `codex-subagent → working` transition at all means the gate is not being applied.
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
- **Web STOP "did nothing" — the turn KEPT RUNNING** *(vim-mode single-Esc miss,
  root-caused 2026-07-24: a16a181f / 3d70feca / 69ced56e)* — the STOP button is
  an INTERRUPT (`web-interrupt`), not the close. ROOT CAUSE: a single synthesized
  Escape does not stop a busy turn — send-key is ~2/3 reliable AND the user runs
  Claude Code with **`editorMode: vim`** (in `~/.claude/settings.json` + the c1/c2
  configs), so the input box is modal: while a turn runs it is in INSERT mode
  (`-- INSERT --`), and during the THINKING phase the first Esc only exits INSERT
  (INSERT->NORMAL) — it never reaches the interrupt handler, so the turn runs to
  completion. The pre-fix tell: a `web-interrupt {ok:true, tab:thinking}`, an
  `escape-recheck` that flipped `thinking -> awaiting-response` (masking it), yet
  `MessageDisplay` hook rows keep firing for tens of seconds AFTER the press and
  the real `Stop` fires at natural completion (a16a181f: +53s; 3d70feca: +22s).
  This also explains why cancel-edit's TWO Escapes are "3/3 reliable" (1st exits
  INSERT, 2nd interrupts) and why a mid-STREAM single Esc DOES land (the stream
  phase routes Esc to interrupt). On a current build the endpoint re-presses Esc
  WHILE the turn is still LIVE (the screen still animating between two captures --
  a marker-free, thinking-level-robust signal) and records `attempts`/`stopped`/
  `probes` + `interrupt-probe` rows; a `web-interrupt stopped:false` (+ `errors`
  func `dashboard interrupt (not stopped)`, a 502) is the verify correctly
  reporting a miss, and the `probes`/`interrupt-probe` phase flags say whether it
  was stuck in insert/thinking/streaming. A `web-interrupt` with NO `probes`
  field at all = a pre-fix server (restart `claude-dashboard.py`). The MIRROR
  IMAGE of this shape — the re-press landing too MANY times, because a delivered
  queued message kept the screen alive — is the "my queued message disappeared"
  shape above; `drained` on the same row tells the two apart.
- **Codex web STOP not confirmed — the codex turn may still be running.** The codex twin of the vim-single-Esc miss above, but codex's composer is NOT modal: a codex interrupt is a SINGLE Escape, VERIFIED not by a screen delta but by the rollout's `turn_aborted` RECORD (codex fires no Stop hook — that record is the only signal; plugins/codex/hostctl.CodexHost.interrupt). TELL: a `web-interrupt` row with `host:'codex'` + `ok:true` + `verified:false` (no turn_aborted inside the bounded wait), paired with an `errors` row — `codex interrupt (no turn_aborted)` (the verify timed out) or `dashboard interrupt (codex send failed)`. The canned anomaly *codex web interrupt not confirmed* fires on exactly that row. NOT a failure: `verified:true` + `steered:true` is codex's queue+Esc STEER — a queued message delivered as a new turn on the abort, so the ⧗ chip drains via normal conversation reconciliation. Distinct from the *Codex tab stuck magenta/blue* shape above (the codex-interrupt-WATCH recovering a TERMINAL interrupt, no web gesture); this is the WEB stop gesture. A failed codex web ANSWER/compact/rename is the same pattern: a `web-answer`/`web-command`/`web-rename` row with `host:'codex'` + `status:'indeterminate'`/`ok:false` + an `errors` row `codex answer (<step>)` / `codex compact (send failed)` / `codex rename (send failed)`.
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
  A second green-too-early shape, and the one to check FIRST when the session
  was never interrupted at all (fixed 2026-07-25, session 2e9b57e4): **a
  merely QUOTED interrupt marker**. `interrupt-watch` scanned raw transcript
  growth for the bytes `[Request interrupted by user]`, so ANY growth that
  quotes the marker read as a cancel and flipped the tab green MID-TURN. The
  live trigger was a **`nested_memory` attachment** injecting a worktree's
  `CLAUDE.md` — this repo's own CLAUDE.md documents the marker in its
  "Hard-won invariants" section — so a mid-turn memory load did it, three
  times in one session; a `Read` of `tabstatus.py`, a grep hit, or an
  audit-CLI paste landing as a `tool_result` are the same class. The tell:
  an APPLIED `interrupt-watch` transition to `awaiting-response` reason
  "interrupt-watch: [Request interrupted by user] in transcript" that is
  **corrected seconds later by the next `pretool`** row (working again, no
  `Stop`, no new `UserPromptSubmit`), plus a matching `streams` row
  `interrupt-detected-flipped-green`. CONFIRM IT FROM THE TRANSCRIPT, per
  occurrence — the audit has no row for the marker itself: grep the
  `sessions.transcript_path` for the marker and read the record's `type`.
  `type: "attachment"` (or the marker sitting inside a `tool_result`'s
  content) = this false positive; a `type: "user"` record whose content IS
  the marker = a real cancel. A whole-file scan finding NO `type:"user"`
  marker record while the audit shows green flips is conclusive. Note the
  bogus green also arms a `notify-arm kind:done` (a false "done" push if it
  outlives the grace window). On a current build the watcher matches the
  marker as a RECORD (`tabstatus.is_interrupt_line`), so a recurrence means
  that predicate regressed — or Claude Code changed the record shape, in
  which case real cancels would ALSO stop being detected (the paired
  symptom: tabs stuck magenta after an Esc).
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
- **The web mirror's live ⏱ elapsed chip is missing, stuck, or on the wrong
  block** — it is driven by that SAME `state:fg-live` row, which now carries
  `ts` (the command's start) beside `tid` (the mirror block's copy-group id):
  `sessionapi.fg_running` peeks the record and the dashboard ticks the seconds
  client-side (docs/dashboard.md, *Live command elapsed*). So: a `write` row
  whose content has NO `ts` = a pre-2026-07 producer (or a re-encoded record) —
  the chip can't render, by design. A chip that ticks forever = the record was
  never consumed; the same `write` → `remove`/`remove-own`/`remove-stale`
  sequence above is the evidence (a `write` with no terminator is the bug, and
  it also wedges every later command out of live streaming). A chip on the wrong
  block = the `tid` in the row is not the tool_use_id stamped on the block's
  ops. The read side is read-only and writes NO rows of its own — if the record
  looks healthy, the fault is in the browser (check `web-client` rows for
  `sse.drop`: a dropped stream stops delivering the `fgrun` clear).
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
  ⧉out the ANSI-stripped concatenation of the group's `gut` ops. **On the WEB
  DASHBOARD the same click is a different row** — `web-copy` (gid/what/chars),
  not `copy`, and the failure funcs are `dashboard copy (state DB gone|read
  ops)` — because the server calls `core.copy.collect` directly (no kitty
  handler, no clipboard: the browser writes the clipboard, so "copy failed —
  needs https" is a NON-secure-context browser limitation, not a server row);
  `chars: 0` in a `web-copy` row means the group genuinely held nothing of that
  type, same as the terminal. The dashboard click-to-view expand is `web-view`
  (gid/ok) — `ok: false` = no `view:<gid>` stash (404). Neither web row present
  for a web click at all = a pre-fix server (before 2026-07-23 the web copy/view
  reads were audit blind spots).
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
- **A pasted FILE was uploaded as an attachment instead of pasting its path**
  (the message went out as `@/…/baqylau-uploads/<uuid>-name.py`) *(Web
  attachments → Pasting a copied FILE, 2026-07-25)* — pair the server's
  `web-clipboard` row (`{names, matched, paths}` — what the page asked, what
  the host's pasteboard held) with the browser's `attach.paste` beacon
  (`{n, resolved}` — which branch actually ran):
  - `matched > 0` + `resolved > 0` = working as intended, the paths went into
    the box and no `web-upload` row should follow.
  - `matched: 0` = the host's pasteboard did not hold those basenames, so the
    page uploaded (expect a `web-upload ok:true` right after). Either the
    pasting device is NOT the host — a phone over the tunnel, the correlation
    guard doing its job — or the clipboard moved between copy and paste, or the
    bytes have no file behind them at all (a screenshot; correct to upload).
  - NO `web-clipboard` row at all, but a `web-upload` = the client is running
    STALE JS (cross-check the same device's `boot` row) or it is a DROP /
    paperclip pick, both of which upload by design.
  - An `errors` row `dashboard clipboard (read failed)` = no pyobjc / no
    pasteboard on the host; the feature degrades to uploading.
  **Do not** read a `web-upload ok:false, why:"empty file"` row as evidence
  here. That is just a 0-byte file (an empty `__init__.py` package marker is
  the real-world case) and the picker/`add()` now refuses it client-side with a
  toast. It was once misdiagnosed as a clipboard "file promise" and two fixes
  were built on that false premise — the actual bug was that ordinary
  non-empty files uploaded fine and were never supposed to upload at all.
- **Quick-command button (compact / model / effort) "did nothing"** *(since
  2026-07-18)* — pull the session's `web-command` `state_files` rows. `ok: true`
  with `tab` ∈ thinking/working/executing = the command QUEUED in the TUI's own
  message queue and runs at the turn boundary (expected, the page toasts it);
  `ok: false` + `tab: awaiting-command` = refused because a dialog was up (by
  design — its digits would decide the dialog); an `ok: false` + `why: "bad cmd"`
  web-command row (via `_reject_input`, NOT an `errors` row — an expected client
  4xx) = an off-vocabulary request (nothing was typed);
  `ok: true` on an idle tab with no effect = FIRST check the paired
  `web-command-confirm` row (model/effort, non-queued only — the TUI can
  interpose a Yes/No switch-confirm menu, the prompt-cache warning, and the
  server auto-presses its Yes via `plugins/claude_code/confirmdialog.py`): `confirm:
  confirmed` = menu answered; `confirm: none` = no menu appeared (applied
  outright); `confirm: failed` (+ an `errors` func `dashboard command
  (confirm failed)`) = the menu is still open in the terminal awaiting the
  user. A QUEUED model/effort has NO confirm row by design (the menu only
  opens at the turn boundary) — an unanswered late menu shows as a red tab.
  Otherwise check the transcript for the slash command's record (the TUI
  parses it — a typo'd model alias errors in-chat, invisible to the audit by
  design: the TUI stays authoritative).
- **Renamed on the web but the name didn't stick / the kitty tab didn't
  change** *(since 2026-07-18; two channels since 2026-07-29)* — pull the
  session's `web-rename` `state_files` rows and read `channel` first.
  Pre-branch refusals are shared: `ok: false` + `reason: no
  transcript|unsupported` = nothing happened at all (missing transcript path,
  or a codex rollout — renames only speak Claude `projects/` transcripts, and
  such a session gets neither a record nor a typed command).
  **`channel: "tui"`** (live): `ok: false` + `errors` func `dashboard rename
  (send failed)` = the paste never reached the window; `reason: "dialog open"`
  = refused on a red tab; `ok: true` + `queued: true` = it is in the TUI's
  message queue and applies at the turn boundary, so "nothing happened yet" is
  expected until the turn ends. The kitty TAB follows Claude Code's own OSC
  re-emit on this path — the dashboard no longer sets a tab title, so a tab
  that DIDN'T move means the rename itself didn't apply (not a separate tab
  failure), EXCEPT on a tab made sticky by a pre-2026-07-29 web rename, which
  stays frozen on that old name for the rest of the session (kitty stickiness
  is per tab and permanent — a fresh tab is unaffected).
  **`channel: "transcript"`** (parked): `ok: false` with `errors` func
  `dashboard rename (append failed)` = the append failed (perms/disk); the name
  reaches the tab only when the session is next resumed. A dashboard title later
  REVERTING to an auto title on this path = the `agent-name` record scrolled
  past `transcript.TITLE_TAIL_B` (64KB) behind a fresher `ai-title`, covered by
  the durable `override` — see the reverting-rename shape above for the full
  split, including the pre-2026-07-29 build where a LIVE rename reverted because
  Claude Code overwrote it. Verify what's actually in the file with
  `tail -c 65536 <transcript> | grep -a agent-name`, and what Claude Code
  itself thinks the name is with `json_extract(payload,'$.session_title')` over
  the session's `UserPromptSubmit` `hook_events` rows.
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
- **ctx bar / substream ctx tag reads far too FULL (pegged red, or >100%) on a
  brand-new model** *(a model generation missing from `KNOWN_1M`, fixed for Opus 5
  2026-07-25)* — the ctx path deliberately writes NO audit rows, so this one is
  triaged from the transcript, not the DB. The window is derived from the model
  **id** (`plugins/claude_code/model.py` `context_window` → `KNOWN_1M`, substring
  match), and `KNOWN_1M` is a hand-maintained list: a new generation whose id is
  absent falls through to the 200k default, so a 1M model's occupancy is divided
  by 200k — up to a 5× over-read that pegs every bar. The bare `opus`/`sonnet`
  alias resolves to 1M, but the transcript records the PINNED id (`claude-opus-5`),
  and that id is what the probe passes — so the alias path masks the gap. Tell:
  `SELECT transcript_path FROM sessions WHERE session_id=?`, then
  `tail -c 65536 <transcript> | grep -ao '"model":"[^"]*"' | tail -1` for the id
  actually in play, and check it against
  `python3 -c 'from plugins.claude_code import model as M; print(M.context_window("<id>"))'`.
  200k for a model that ships 1M = add the id substring to `KNOWN_1M` (and its
  default reasoning level to `model_default_effort`). Same failure, opposite
  direction: `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` in the env caps every model at
  200k on purpose — check it before blaming the table.
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
  drifted (env `CLAUDE_CONFIG_DIR` differs between the hook and the TUI).
  **A card frozen on a DEAD list after a `--resume` — every `tasks` write
  re-stashing the IDENTICAL count/breakdown while TaskCreated/TaskCompleted
  events name tasks NOT in the kv — is the KEY-DRIFT shape** (session
  `6e58ae19`, fixed 2026-07-30): a resumed Claude Code process mints a fresh
  internal list id and writes `tasks/session-<other-segment>` (matching no
  sid/subagent/prompt_id) while the sid-keyed snapshot kept re-reading the
  pre-resume dir. On a current build `task_fmt.resolve_dir` follows the dir
  holding the FRESHEST copy of the event's own task (`_match_mtime` over the
  sid dir + the pinned drift dir, widened to a newest-mtime sibling scan when
  neither copy is younger than `RECENT_S`) and PINS a scan hit as a `tasks-dir`
  `{"action":"pin", "dir", "sid_dir", "task_id", "subject"}` state_files row +
  the `tasks-dir` kv (a FRESH win by the sid dir writes the `{"action":
  "unpin"}` twin) — so a drifted session shows a pin row and its later
  snapshots track the pinned dir. RECENCY is load-bearing, not a detail: the
  first cut ordered the candidates (sid dir first) and regressed the SAME DAY
  on the SAME session — a `TaskUpdate` probes by taskId ALONE, integer ids
  exist in every list, so the dead sid dir "matched" every id-only probe and
  its snapshot kept landing LAST, re-stashing the dead list over the correct
  TaskCreated/TaskCompleted ones (the tell: alternating counts in back-to-back
  `tasks` writes at the same second). A frozen card WITHOUT a pin row on a
  current build means the event carried no usable probe (no task_id/subject)
  or the scan cap (`SCAN_MAX` newest dirs) missed the drifted dir. A
  parked session showing an EMPTY card that had tasks → the last snapshot ran
  after Claude Code's session-end cleanup emptied the dir (should be impossible
  — no hook fires at cleanup; if seen, check what event triggered the last
  `tasks` write). Remember the on-disk dir itself reads empty for every ended
  session — the kv is the only surviving record, so never "verify" against the
  dir post-hoc.
- **The web tasks card VANISHED with tasks still in it — or a dismissed card
  won't go away / came back by itself** *(the card's ✕, since 2026-07-27)* —
  the ✕ is PURELY VISUAL: it writes only the `tasks-hidden` key of the durable
  global prefs store (`dashboard/prefs.py`), never a task, so a "my tasks
  disappeared" report is about the card, not the list (the `tasks` kv rows above
  prove the list is intact). Evidence: the `web-taskshide` `state_files` rows
  (content `{sid, hidden, ids}` — `ids` is the finished list the dismissal
  covers). Card gone unexpectedly → a `web-taskshide hidden:true` row for that
  sid, and the ids tell you WHICH list was dismissed; since the dismissal only
  holds while every task is completed AND every current id is in that set, a card
  that hid "on its own" means the last task completed under a dismissal that
  already covered its id. Dismissal that didn't stick / the card returned →
  either the id set moved on (a new TaskCreate is the DESIGNED un-hide — look for
  a `tasks` write with a higher count right before it) or the prefs write
  degraded (a `dashboard prefs mutate` errors row at the same instant, the
  "reported success but didn't stick" signature above — the endpoint answers ok
  either way). ✕ refused (409, an `ok:False` + `why: tasks unfinished`
  `web-taskshide` reject row) → the list still had a pending/in_progress task at
  the server, i.e. the page's card was stale.
- **Memory tab empty / a touched note missing / wrong verb label** *(memory tab,
  since 2026-07-21)* — the tab is fed by the `memory` state-DB kv, which
  `plugins/claude_code/memory.py` merges on every memory op: `record()` for a
  touched note, `record_search()` for a vault search. Evidence: the `memory`
  `state_files` rows (`action:"write"` carries `verb`/`path`/`agent`/`notes`-count;
  `action:"search"` carries `kind`/`sub`/`query`/`hits`/`searches`-count) and the
  producers' `hook_events` decisions (the `+... [mem:<who>]` fragment on
  `claude-file-fmt.py` / `claude-cmd-fmt.py`, or the substream's render for a
  subagent).
  **START HERE when notes are missing and the session used the SHELL** *(the Bash
  plane, since 2026-07-30)*: a memory op does not have to be a Read/Write/Edit TOOL
  call, and most are not — `cd ~/wiki/01 && cat platform/…md`, `cat a.md b.md
  c.md`, `find . -name x.md -exec cat {} \;`, `qmd query "…"`. Before the Bash
  plane existed NONE of those recorded (session d8dc5a67: ten notes read, two
  searches run, zero records, and the tab looked simply unused). The canned
  anomaly **"Bash commands naming the memory wiki but NO memory records"** is the
  direct test. Capture is `plugins/claude_code/memcmd.py` `plan()` reached through
  the `fileobs` COMMAND plane, and it can legitimately decline: a statement under
  an untrackable `cd` (`cd "$DIR"`) is skipped, a token must be a REAL file under
  the vault (a flag or a `*.md` glob is not — `--include=*.md` used to be mis-read
  as a filename), the statement must contain a READER (`ls`/`rm`/`git add` of a
  note record nothing — the plane is scoped to reads), and a bare `x.md` basename
  resolves through the vault index ONLY when the statement runs inside the vault.
  Reproduce a verdict directly: `python3 -c "from plugins.claude_code import
  memcmd; print(memcmd.plan(<cmd>, <cwd>))"` against the command in the payload.
  A SEARCH recorded with no hits → the output was truncated past its results
  (`| head -40`), it was a background command (its bytes go to the tailer, never
  to the hook), or the command ran MORE THAN ONE search (hits are attached only to
  a single-search command, on purpose — qmd's output says nothing about which
  block belongs to which query).
  Tab MISSING entirely → the session
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
  exact op sequence. A memory op that painted its ❖ marker in the mirror but never
  reached the tab → the write raised (paired `errors` func `memory.record` for a
  note, `memory.record_search` for a search). A note or search that VANISHED from a
  long session → the searches list is capped at `memory.SEARCH_MAX` (40, oldest
  dropped); the notes list is uncapped.
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
  Since 2026-07-22 the open-check POLLS up to `STEP_TIMEOUT_S` (it was the ONE
  step that read the screen once with no retry — a capture taken while the
  dialog was still rendering, e.g. right after a `--resume` into a fresh
  window, or a transient blank/partial `get_text`, bailed instantly with
  step:open on a genuinely-open, never-answered ask; session 0247ebb2,
  2026-07-21), and the bail now records the SCREEN it saw: the `dashboard
  answer (open)` `errors` row carries a `screen` field (`clip_screen`: the
  head AND tail of `get_text`, `SCREEN_CLIP`=2000 — NOT a plain `[-2000:]`
  tail, so a wide window's on-screen chip bar at the TOP is never truncated
  away and misread as off-screen) — read it to tell the causes apart. If the
  `screen` shows the option rows + the `"Enter to select … Esc to cancel"`
  footer but **NO `☐`/`☒` chip bar at all**, that IS the **chip-bar-scrolled-
  off** shape (session 819627e5, FIXED 2026-07-23): on a narrow/short window a
  tall dialog (wrapped multi-line option descriptions) overflows the viewport
  and the chip bar scrolls off the TOP while the footer survives — `region()`
  anchored ONLY on the `☐`/`☒` bar returned "" so `dialog_open` was False and
  `drive` false-bailed `step:open` on a dialog the user was staring at. Fixed
  by `askdialog.region` falling back to the whole screen when there's no chip
  bar but a `FOOT`/`REVIEW` footer is present; a footer-but-no-chip-bar
  `step:open` on a CURRENT build is the regression. Other causes: a
  footer-string DRIFT after a Claude Code upgrade (`FOOT`/`REVIEW` in
  `askdialog.py` no longer match — the `screen` shows a footer whose text
  differs) · a blank/partial capture (empty/tiny `screen`). `step:
  question`/`review` bails carry the same `screen` field now.
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
  (docs/dashboard.md *Web ask*). (3) **The FORWARD-ONLY navigation bug**
  (session 3fd325d9, fixed 2026-07-22 — supersedes the intermediate
  "custom-advance" fix earlier the same day): live probing of the stuck
  dialog proved `left`/`right`/`Tab` do NOT switch questions in this Claude
  Code build AT ALL — they are inert (or caret movement on a focused text
  row), from EVERY row. The dialog is forward-only: the only way to a later
  question is answering the current one (single-select auto-advance / the
  multiSelect "Next" row's Enter). This broke TWO ways: (3a) a MIDDLE
  multiSelect answered with a custom "other" — the blind `right` advance was
  swallowed, so the pane never left it and the NEXT question bailed
  `step: question` one tab later (the `errors` `screen` shows the dialog STILL
  on the multiSelect with a checked custom row cursored, chip bar that
  question ☒ but its pane displayed); (3b) on a RETRY, the driver's old
  `left`×len "normalize to question 1" no-oped (left is inert), so a dialog
  stuck/partway on a LATER question could never be walked back and the very
  first wait bailed **`step: question` "question 1 never became current"**
  even though question 1 was long answered — the `screen` shows the dialog on
  a LATER question than the one the bail names. The fix drops the left-normalize
  entirely: `drive` is now forward-only, answering whatever question is
  CURRENTLY shown (which also RECOVERS a stuck dialog), and a multiSelect
  advances via its "Next"/"Submit" row (`_advance_multi`), bailing its own
  **`step: advance`** ("dialog did not advance past question N") instead of a
  misleading `question` one tab later. So on a current build: a `step: advance`
  row IS an advance-failure directly; a `step: question` "question 1 never
  became current" whose `screen` shows a LATER question is the pre-fix retry
  shape (a stale server — restart `claude-dashboard.py`). The FakeAsk harness
  now models forward-only (left/right/Tab inert), and
  `test_post_answer_recovers_dialog_stuck_midflow` +
  `test_post_answer_middle_multiselect_custom_advances` reproduce both.
  This class of bug can ONLY be caught by
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
- **New-session form lost the half-typed FIRST PROMPT (closed the form and it
  came back blank), shows a prompt that was already launched, or shows ANOTHER
  PROJECT's prompt** → the draft is GLOBAL and keyed by DIRECTORY, not by
  session: log/path-empty `ns-draft` `state_files` rows (`sql "SELECT * FROM
  state_files WHERE action='ns-draft' ORDER BY id DESC"`), content
  `{action, cwd, chars, seq}` (docs/dashboard.md *New-session draft*). ALWAYS
  read `cwd` first — every claim below is per directory. A close with NO `write`
  row for that cwd (or one with a smaller `chars` than what was typed) ⇒ the
  flush never landed — `closeNewSession`'s immediate save didn't reach the
  server (check the frontend-audit `web-client` rows for the page's
  `sse.drop`/`js.error` around that moment; the POST is untagged, so absence is
  the signal). A draft that came BACK after a successful launch ⇒ a missing
  `clear` (the launch's close flush didn't fire) or a `stale` row that dropped
  the clear — compare its `seq` against the neighbouring `write` for the SAME
  cwd, the newest `seq` is what the box restores. The WRONG PROJECT's prompt in
  the box ⇒ read the `web-client` `ev=nsdraft.dir` rows (`{from, to, carried,
  chars}`): `carried:1` is the deliberate follow-the-text rule (the target
  directory had no draft of its own), and a switch that never fired at all means
  the directory field never blurred — the box legitimately still belongs to
  `from`, which is also the cwd the close/launch flush writes. A draft missing
  for an OLD directory with two dozen newer ones ⇒ `NS_DRAFT_MAX` pruning, by
  design. And a draft that vanished while the form was OPEN on another device is
  the on-open reconcile fetch adopting a newer server entry — expected, and the
  `write` rows show whose was newer.
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
  The `waited_s` clock starts when `kitten @ launch` RETURNS, so it says nothing
  about the time before that: for "the click itself hung / the form froze before
  the spinner", read the `web-launch` row's **`ms`** step map instead
  (`fe`/`row`/`livewin`/`front`/`clip`/`tab`/`all`, milliseconds — the handler's
  four subprocess round-trips). Typical: `clip` ~150 ms (osascript), `front`
  ~20 ms, `livewin` ~30 ms (resume only), `tab` ~200 ms, `all` under ~0.5 s. One
  step dominating names the culprit — a multi-second `clip` is a wedged
  osascript / a permissions prompt, a multi-second `tab` is a slow kitty socket,
  and a row whose `ms` STOPS at a step (missing `all`) can't happen (the row is
  written after `tab`), so the missing row itself means the handler never got
  past `kitten @ launch`. Pair it with the browser's `new.begin`/`new.ok`
  clientlog (`ev` in `web-client` rows): `new.ok` `ms` minus the row's `all` is
  the HTTP/tunnel share, not the server's.
- **"I hit launch and nothing happened for a second, THEN the loading screen
  appeared"** *(fixed 2026-07-28)* — the pending view used to mount in the POST's
  `.then`, so the whole `new.ok` latency was dead air with the form frozen. The
  DB tell is the ORDER of the `web-client` rows for one launch: a `launch.arm`
  AFTER that launch's `new.ok` = the old behaviour (a dashboard running pre-fix
  code — restart it, and have the user hard-reload for the new JS). Fixed looks
  like `launch.arm` (with **`pend: true`**) → `new.begin` → `new.ok`. If the arm
  is correctly first and the user STILL reports a freeze, it is not this bug —
  the waiting room mounted, so look at render/route errors (`js.error`) instead.
- **One session shows up in TWO kitty tabs / messaging it opens a duplicate
  tab each time / sends land in the "wrong" (older) tab** *(duplicate
  resume-launch, guard added 2026-07-19)* — the page resume-launches a
  session it believes is PARKED, but the session already had a LIVE tab, so a
  second `claude --resume <sid>` runs against the SAME transcript (both panes
  tagged `claude_session=<sid>`; `kitten @ ls` shows the sid on two windows,
  and `live_windows`/`window_for_session` keep the FIRST-iterated one, so
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

- **"Resume & send" on a parked CODEX session is refused ("resume not yet
  supported for this session's tool") — or, on an older build, opens a tab that
  resumes nothing while the card shows Claude-shaped numbers** *(wrong-tool
  resume + shadowed read providers, guard added 2026-07-30)* — the session
  cards and the composer are tool-agnostic, so a parked codex standalone host
  offers the same resume button; but its sid is a rollout uuid, and
  `claude --resume <sid>` finds no Claude conversation (the dead tab of the
  shape above, with a perfectly healthy transcript on disk). Tell that the
  guard FIRED (healthy): a `web-launch` `ok: false`, `why: unsupported tool`
  row plus an `errors` row `dashboard new-session (resume unsupported tool)`.
  An `ok: true` resume launch for a session whose `sessions.transcript_path` is
  NOT a `~/.claude/projects/<hash>/<sid>.jsonl` file = a pre-2026-07-30 server
  (restart `claude-dashboard.py`). The READ side of the same ownership
  question leaves no rows at all (the path-keyed fan-outs are read-only, like
  ctx/goal), so it is diagnosed from the SCREEN: a non-Claude session showing
  Claude-shaped facts — classically its ⊜ compact button live, because
  `prompt_count` returns its cap (8) for any file over `PROMPT_SCAN_B` without
  reading a byte, and a 429KB codex rollout measured exactly that. Current
  builds gate every path-keyed fan-out on `plugins.owns(path)`
  (`plugins._first_path`), so those numbers should be blank, not Claude's.
- **A web-sent/launched message carried an image the user never attached (e.g.
  `say test[Image #1]`, a stray screenshot)** *(guard added 2026-07-23)* — this
  is Claude Code's TUI auto-attaching whatever image is on the macOS clipboard on
  ANY bracketed paste (and on an argv-prompt startup); NOT a baqylau attachment
  (the `web-send`/`web-launch` row shows `attachments: 0`, no `web-upload`). The
  fix empties an image clipboard before each bracketed paste / prompt launch
  (`clear_clipboard_image`, macOS-only, only when an image is present). Tell it
  RAN: the `web-send`/`web-command`/`web-launch` row carries **`clip: true`** (it
  found + cleared an image). `clip: false` = a text/empty clipboard (nothing to
  clear) OR off macOS. If a stray image STILL rides a message on a current build:
  the guard was skipped (a paste site with no `clear_clipboard_image` before it),
  the `osascript` clear failed (rare — the clipboard held an image, `clip` was
  attempted but CC still attached: a race where the user re-copied an image in the
  ~1-2s before CC's startup read), or a pre-fix server is running (restart
  `claude-dashboard.py`). Cross-check the message's image `source` — CC's own
  `image-cache/<sid>/N.png` = a clipboard paste, NOT the dashboard's
  `UPLOADS_DIR` `@path` attachment (those are legit, and unaffected: they never
  use the clipboard).
- **Clicking a Read/Update/Write line doesn't expand (or won't collapse)** — the
  click-to-view chain is stash → toggle → reflow; check it in that order. (1) Stash:
  a `state_files` `view-stash` row (content: gid = the op's tool_use_id, tool, ops
  count; subagent stashes also carry `agent`) must exist from `claude-file-fmt.py`
  or `claude-substream.py` (or `claude-cmd-fmt.py` with `tool: Bash, kind: read`,
  plus `render: code|md` — a file-reading command, `sed`/`grep`/`cat`/`head`/`tail`
  of a source file or a `sed`/`grep` SLICE of a markdown one, rendered AS a Read
  one-liner instead of a `▶ foreground` block; its healthy trace is a
  `claude-cmd-pre.py` decision `<kind> reader …` (`code reader`/`md reader` —
  streaming SKIPPED) + a `claude-cmd-fmt.py` decision `rendered as Read (<kind>) …`,
  and its ⧉cmd/⧉out copy reads the command/output from THIS stash, not the ops
  table — `core.copy.collect`'s fallback; disable via `CLAUDE_MIRROR_CMD_READ=0`,
  or per kind via that kind's `CLAUDE_MIRROR_MD`/`_CODE`. A whole-document
  `cat x.md` is NOT collapsed by design — it streams, so look for a `streams` row
  and `CLAUDE_STREAM_MD` instead) — no row + no `errors`
  `view-stash (…)` means the op
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
  record's done flag (`state:agent.<id>`) was ever written.
  **A BACKGROUND block that shows the `▷ background` header + command but no
  output, and whose `bg` stream stays OPEN indefinitely (0 `lines_emitted`, no
  end_reason), while the tab is pinned on `awaiting-bg` (blue)** = a long-lived bg
  command that writes its real output ELSEWHERE (a `--output <file>` flag, a poll
  harness) and NOTHING to the task-output file baqylau tails: that 0-byte
  task-output file is held open for write by the still-running process (its
  stdout/stderr redirect), so `has_writer` stays True and `writer-gone` never
  fires — correct-by-design (a running bg job legitimately keeps the tab blue),
  NOT a stuck tailer. Confirm with `lsof` on the `tasks/<taskid>.output` path (a
  live writer pid = the job is still running) and the bg tailer pid (tailing a
  0-byte file). When such a job finally ENDS having streamed zero lines, the block
  now paints a dim `(no output)` placeholder before the `■ background finished`
  chip (`stream.py` `drain`, gated `run.lines == 0`) — a finished bg block that is
  still a bare header + chip with no `(no output)` gut op is the pre-fix shape
  ('no command and no output' in a report). For a MONITOR block:
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
- **Session vanished from the web dashboard AND its mirror toggles up EMPTY —
  while the kitty tab is still running claude fine** *(nested-claude pane
  hijack, fixed 2026-07-22)* — a `claude` launched INSIDE the session's own tab
  (a `/goal`-style test spawning `claude` in a scratch dir like
  `/private/tmp/goaltest`, or any shell-out to `claude` from within the session)
  inherits the outer pane's `KITTY_WINDOW_ID`, so its SessionStart anchored on
  the OUTER session's window and ran the full lifecycle: `close_stale_mirrors`
  swept the outer mirror as "stale", it re-tagged the host window
  `claude_session=<nested>`, then untagged + closed the panes at its own
  (seconds-later) SessionEnd. Net effect: the outer session's mirror panes are
  gone, the host window has NO `claude_session` tag, so the dashboard's
  `live_windows` scan finds no live pane and demotes it to `live:false`
  (`/api/sessions` shows `live:false, parked:false` — the "disappeared" limbo),
  and toggling the mirror re-opens against the empty/parked nested sid — while
  the outer session's real state DB (its `ops`) sits intact and untouched.
  Tells: the `anomalies` **"pane hijack"** section is non-empty naming a DIFFERENT
  sid that closed THIS sid's mirror; multiple `sessions` rows sharing the same
  `kitty_window_id` where the "nesting" ones are short-lived (`ended_at` ~1s
  after `started_at`) in an UNRELATED cwd; each nested sid's `pane_events` shows
  `close-stale` (naming the outer sid) → `focus-host` → `open` → `close session
  end`. On a current build the nested SessionStart is caught by the nested-host
  guard (`split.cmd_open` consulting `hostpane.tab_host_sid` — the same guard the
  codex host uses): its ONLY `pane_events` row is `open` with detail `skipped:
  nested in live host <outer sid>`, so the anomaly stays empty. A non-empty pane
  hijack from a nested same-window claude = the guard regressed. RECOVERY for an
  already-hijacked live session: re-tag its host window
  (`kitten @ set-user-vars -m id:<win> claude_session=<sid>`) and toggle the
  mirror — the history is intact.
- **A session card MOMENTARILY flashed "gone" on the dashboard, then healed on
  its own, while the session kept working** *(empty-`ls` can't-tell, fixed
  2026-07-24)* — a card shows **gone** when it's `live:false && parked:false`
  (`app.js`: `row.parked ? "parked" : "gone"`). `live` is decided by
  `live_windows()` scanning kitty for `claude_session` tags. `kitten_ls`
  swallows EVERY transient failure (timeout / rc≠0 / socket hiccup) into an
  empty list and NEVER raises (`frontends/kitty.py`), so a failed scan returned
  `{}` (not `None`) — `{} is not None` passed the demotion guard and flipped
  every running session to not-live for one `_LIVE_TTL` (5s) tick, then healed.
  There is NO audit row for this read-side demotion; the CORRELATING TELL is a
  **`tab_transitions` `kitten @ failed rc=N — state row unchanged`** row near the
  flash moment (the SAME socket hiccup, audited on the tab-status side) — and
  the `sessions` row stays perfectly live throughout (no SessionEnd, no park, no
  hijack, single session on the window). Distinguish from the nested-claude pane
  hijack above (persistent limbo, not a momentary flash; there a DIFFERENT sid's
  `pane_events` closed this one's mirror). On a current build `live_windows`
  maps an empty/failed `ls()` to `None` (can't-tell → keep the state-DB signal),
  reserving `{}` for a real non-empty tree with no tags — so a momentary flash
  recurring on a current build means that empty→None guard regressed.
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
- **A mail row on the web shows no message / the summary says "passed N messages"
  with the wrong N** — the same `msg-transitions` rows, now per event: `msg_id` plus
  `chars`, the LENGTH of the body the row painted (never the body itself). A `new`
  event with `chars: 0` had no `text` in the inbox record, so the mail block has
  nothing behind its click — that is the record, not the renderer (`msgs._scan_inbox`
  reads `text`; Claude Code writes it). Every event of one message carries the SAME
  `msg_id`, which is what the web counts distinct messages by (the op's `mid`) — if a
  run's summary counts an arrival and its read as two, the `mid` stamp is missing from
  the ops (pre-2026-07-27 history has none, and legacy rows are counted per row).
  **Check the `summary` first: an EMPTY one usually means the "message" was a teammate
  LIFECYCLE FRAME** (an idle notification and friends — JSON in the record's `text`,
  no summary; 10 of the 12 arrivals in one reviewed lead session). Those are worded from
  their type now (`Mail … · idle`) and deliberately have no body; before that they painted
  nothing at all, which is the "I can't read the message" report. A pre-2026-07-27
  frame row on disk is indistinguishable from a prose arrival — nothing in the op says
  which it was — so history keeps the `Message <frm> → <to>` wording.
- **A message is missing from the web mirror entirely / the mail row has no text** —
  since 2026-07-27 the MESSAGE row comes from the `SendMessage` hook, not the poller:
  `hook_events` where `handler='claude-mail-fmt.py'`, one row per send, its `decision`
  carrying `<from> → <to> (N chars, msg_id …)`. No row for a message you can see in the
  transcript means the hook never ran (check the dispatcher's `subscriber` row for the
  same `tool_use_id`: if that exists and mail-fmt's does not, the step crashed — look in
  `errors` under `script='claude-mail-fmt.py'`); an `ignored: send failed` decision means
  Claude Code refused the send, and `ignored: no state DB` means the session was
  unhosted or already parked. The poller's `msg-transitions` rows are the SECOND source
  and now deliberately carry no text — a mail row with no message behind it is expected
  there and is not the bug (docs/dashboard.md *Team mail*).
- **A skill invocation is missing from the mirror / shows no args** — since 2026-07-27
  a `Skill` call gets its own row (`⏺ Skill(<name>)`, the args behind the click):
  `hook_events` where `handler='claude-skill-fmt.py'`, one row per call, its `decision`
  carrying `skill <name> (N chars of args)`. Both tool hooks fire for this tool, so a
  MISSING row with a `subscriber` row for the same `tool_use_id` means the step crashed
  (`errors` under `script='claude-skill-fmt.py'`); `ignored: subagent event` is correct
  and expected for an agent's own skill call (the substream renders that stream), and
  `ignored: no state DB` means the session was unhosted or already parked. `0 chars of
  args` is not a bug — a skill invoked without args has nothing to put behind the click,
  and the line is deliberately unclickable rather than opening an empty panel. Note the
  skill's BODY is never in the payload: Claude Code injects the loaded `SKILL.md` as a
  user-shaped turn (the mirror's `data-injected`), so "the row doesn't show the skill's
  instructions" is by design (docs/dashboard.md *Skills*).
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
  the chosen **`model`**). Since 2026-07-23 the `web-migrate` row also carries a
  **`pick`** sub-object — the SAME `pick_target` trace as `relimit-pick`
  (`branch`/`cur_model`/`candidates`/`chosen`), so a MANUAL `no target` refusal
  is reconstructible without re-deriving it by hand (read `pick.candidates` for
  the per-account reject reasons; `pick.branch=fallback` = the transcript model
  was unreadable, same tell as the automatic path). This closed the manual
  twin of the original migration blind spot. Both paths now walk the same fable→opus→sonnet ladder
  (`account.pick_target(cur_slug, cur_model)`, `usage.model_available` per
  rung), so a `no target`/`no fallback account` means NO account can serve any
  rung: check each account's `state_files` `limit-hit` content — an
  ACCOUNT-WIDE stamp (`model: null`) bars every rung, a model-scoped stamp bars
  only its own family, and an over-ceiling 5h bars the automatic path (manual
  drops the ceiling). **Do NOT re-derive the refusal by hand — read the
  `state_files` `relimit-pick` row** (the automatic path's full `pick_target`
  trace, since 2026-07-23): its `candidates` list names every account weighed at
  every rung with its `eff5h`, `limit_hit` scope, and the exact `reject` reason,
  plus the resolved `cur_model`, the `branch`, and `chosen`. Two tells to check
  FIRST: (1) `session_model: null` → `branch: "fallback"` means the running
  model couldn't be read off the transcript, so the picker took the COARSE
  fallback branch, which rejects ANY account with an active limit-hit — even one
  scoped to a DIFFERENT model that the ladder branch would have used for a lower
  rung (the reported *"idle account, still didn't migrate"* bug: a near-idle
  account refused with `reject` = `any active limit-hit (fallback branch:
  cur_model unknown)` over a stale Fable stamp, while `model_available(stamp,
  "opus")` was True — the fallback is stricter than the ladder ON PURPOSE, so
  chase WHY `model.session_model` returned null before blaming the picker);
  (2) a candidate rejected `over N% 5h ceiling` whose `eff5h` reads high on a
  snapshot that has since ROLLED — the refusal was correct at decision time
  (a live `/api/accounts` showing that account idle NOW is the window having
  reset since; compare the `limit-hit`/`usage` reset epochs). A MANUAL migrate
  leaves NO `relimit-pick` row (its trail is the `web-migrate` row — the
  dashboard doesn't record the trace). A downgrade landing on the WRONG model =
  the picker chose a rung whose account was mis-read: cross the
  `relimit-launch`/`web-migrate` `model` field against the accounts' `limit-hit`
  scopes and 5h `usage`. The
  chip
  on the WRONG account = the stamp's own `slug` field vs the session's
  `account` kv (after a migration the adopted session's DB carries the OLD
  account's stamp under the NEW account — `account_usage` must file by the
  stamp's slug; compare the `state_files` `limit-hit` content's slug with the
  pill showing it). A usage bar stuck at `resets now` = a stale snapshot
  served raw — `/api/accounts` serves `usage.effective_usage` (ANY
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
- **"The account's 5h bar shows a LOW % (e.g. 25%) even though it's at its
  limit" / "5h and 7d read the same"** — the tokenless status-line snapshot is
  STALE. Worst after a rate-limit MIGRATION: the blocked account's state DB is
  re-stamped to the NEW account (the `adopt` row), so the old account's freshest
  `usage` kv is whatever a stale/older session last captured — measured 98 min
  old / 25% while c2 sat at its cap, and 5h==7d because that one frozen snapshot
  happened to be equal. `accounts_payload` now pegs the served `five_hour` to
  100% whenever an ACCOUNT-WIDE `limit-hit` (no `model`) is active (presentation
  -only; `five_hour_eff` and the relimit picker stay on the honest snapshot). So
  a low 5h bar under a "limit hit" chip on a CURRENT build = the stamp isn't
  active (check `limit_hit_active`: reset passed, or a model-scoped stamp which
  deliberately does NOT peg 5h) — compare the `state_files` `limit-hit` content
  (`resets_at`/`model`) against the account's `usage` kv `ts` (how stale) and
  the `relimit-pick` row's `eff5h` (what the picker saw at migration time).
- **Migration started but the session never came back** — the `streams` row
  kind `relimit` names the failed leg via `end_reason`: `close-failed` /
  `close-timeout` (the tab wouldn't close or SessionEnd never parked the state
  DB — the migrator then deliberately does NOT launch), `window-gone` (tab
  vanished while the DB stayed live — bailed; **AUTO only** — a `mode=manual`
  migrate deliberately launches over a stranded-live DB instead, the
  logged-out-account recovery, so it never shows `window-gone`),
  `launch-failed` (kitten refused
  the tab; the `relimit-launch` state_files row has `ok: false`). A `launched`
  end with no later SessionStart under the sid = the relaunch died inside the
  login shell (bad alias, keychain prompt, `claude` not on PATH) — the canned
  anomaly "rate-limit migration incomplete" flags both cases; from there it's
  outside the audit's sight (check the new tab's shell by hand).
- **"An account is/isn't showing ⚠ logged out on the dashboard"** — the flag is
  EVENT-driven, not a probe. A session under a logged-out account dies on a
  `StopFailure error='authentication_failed'` (message "Please run /login · …
  OAuth access token has been revoked"), which `relimit` stamps as a
  `state_files` `action='logged-out'` row (content `{slug, ts, msg}`) + the
  `logged-out` kv, decision `auth_failed: stamped logged-out` under handler
  `claude-relimit.py` (the canned anomaly "session died logged out" surfaces the
  row). **Badge MISSING though the account is logged out**: no such row = no
  session has DIED on it since it was logged out (the flag only lights on the
  event — launch a session under it, or it stays clean), OR the session was
  UNHOSTED (no state DB — the stamp is skipped with `auth_failed: no live state
  DB`, never creating the DB), OR a `usage` snapshot for the slug that post-dates
  the stamp by more than `plugins.claude_code.usage.LOGGED_OUT_GRACE_S` (60s) cleared it — the
  pill clears on the next successful/`/login` session by design, so compare the
  `logged-out` stamp `ts` against the account's newest `usage` kv `ts`
  (`kv_at(<state db>, 'usage')` across the slug's recent sessions, newest wins).
  A snapshot only a FRACTION of a second newer is the dying session's own
  post-turn status-line render, NOT a re-login: that used to self-clear the badge
  ~0.3s after it was stamped (session `518b6f4d`, fixed 2026-07-26 with the grace
  margin — docs/relimit.md *Why the grace margin*), and it is the reason the write
  half can look perfect in the audit (stamp row + decision + anomaly hit) while
  the dashboard shows nothing. **Badge STUCK after re-login**: the clearing usage
  snapshot never landed (no status-line capture under the account since — the
  account kv `usage.ts` is older than the stamp), or it landed INSIDE the grace
  margin (a `/login` in the same session, cleared on the next render past it). Do NOT reach for a token probe: probing is unreliable
  (a rotated cached access token reads valid post-logout) and dangerous (a
  refresh-grant call can rotate/orphan the keychain token and log the account
  out) — docs/relimit.md *Logged-out accounts*.
- **"a codex session shows no limits / its cost reads $0"** — these are READ-side
  and add NO audit rows, so the DB will not have the answer; check the sources
  instead. Limits: a codex session's `usage` comes from its ROLLOUT, not from a kv
  (codex writes none) — `plugins/codex/read.usage` scans the tail for the last
  `token_count` whose `rate_limits` is NON-NULL. Nothing shown means the rollout
  holds no such record: grep it (`grep -c '"rate_limits"' <rollout>`), and note
  the field is NULLABLE, so a run that has not talked to the API recently can
  have trailing usage events without one. The LIST-page row is different
  machinery (`codex app-server account/rateLimits/read`); when THAT is missing,
  the app-server degrade IS audited — an `errors` row script `codex-usage`, func
  "codex app-server account/rateLimits/read", once per TTL window (usually a
  stripped `PATH` that finds neither `codex` nor its `node` — `usage.codex_spawn_env`).
  Cost $0: codex never writes the `otel` table (that receiver is Claude Code's
  telemetry), so its spend is the state DB's own scoreboard counters, priced by
  `CODEX_PRICES` as the stream folded each turn — read `kv`/stats off the state DB
  (`bin/claude-audit.py`), and check the `bump` rows with `meta.kind='codex'`. A
  `total_usd: 0.0` from the OTEL sum for a codex sid was the pre-P3 bug; the
  payload now routes through `plugins.session_costs`, which asks the OWNING host.
  The corpus-wide Stats page is still OTEL-only and legitimately undercounts
  non-Claude hosts (documented in docs/dashboard.md, not a bug to chase).
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
  it). **But check the gate isn't STALE first** — see the shape below; (b) a
  `web-send queued: true` (a busy `tab` whose screen probe read
  `live: true`) = it QUEUED
  in the TUI and delivers at the turn boundary (the ⧗ chip is now persisted in
  the `composer-queue` kv so a reload keeps showing it — a pre-fix reload lost
  the chip, reading as "gone even from the queue"; a busy `tab` with `live:
  false` is the OPPOSITE verdict — see the stuck-⧗ shape below); (c) an AskUserQuestion
  ANSWER not showing in the mirror feed was a RENDER gap (the answer is a
  tool_result, dropped by the conversation stream) — fixed by surfacing it as
  an `answer` record, no audit row (it's a read-side transcript render).
- **"I couldn't send you a message" right after DECIDING an ask/plan card**
  *(fixed 2026-07-30; session e683c445)* — the composer's modal gate keys on the
  `ask-pending`/`plan-pending` kv, and a DECLINE fires **no closing hook**, so
  the stash outlived its dialog and 409'd every send. The tell is a `web-send
  {"ok": false, "blocked": "modal"}` whose most recent `web-plan`/`web-answer`
  row is an `ok: true` DECISION — the dialog was decided, so the block is stale
  by definition (canned query: *"send refused by a STALE modal gate"*). The
  measured trace: `plan-pending stashed` 10:36:17 → `web-plan ok:true
  kind:dismiss` 10:36:21 → **no hook_events row after it** → two `blocked:
  modal` sends at 10:36:51/54 → `web-clientfail` → the message dropped, and it
  only unblocked 9 minutes later on an unrelated turn boundary. Both endpoints
  now drop their own stash on a decline (`plan-decision` for `feedback`/
  `dismiss`, `answer` for `chat`), audited as a `plan-pending`/`ask-pending`
  `{"action": "remove", "reason": "web decline (…)"}` row — **so on a current
  build, look for that remove row between the decision and the send**: present =
  a different bug, absent = the drop itself failed (check `errors` for
  "dashboard stash heal"). The companion symptom is a `web-hint {"op":
  "plan"|"answer", "phase": "stale"}` — the same lingering stash also leaves the
  card greyed on the page (docs/dashboard.md, *A DECLINE drops the stash
  itself*).
- **"a message shows as ⧗ QUEUED but it was never queued" / a ⧗ chip that never
  goes away** *(stale tab colour + a too-strict drain, both fixed 2026-07-25;
  session bdeca061)* — the chip is pinned when `/message` replies `queued`, and
  dropped when the delivered prompt matches it. Two independent defects made it
  stick, so check both. **(1) Was it really queued?** Read the `web-send` row's
  new **`live`** field (docs/dashboard.md *The tab colour alone cannot promise
  `queued`*): `live: false` = the tab colour was STALE and the server correctly
  reported `queued: false` (healthy). NO `live` field at all = a pre-fix server
  that trusted the colour alone — and the colour lies after a **terminal-side
  Esc-Esc cancel**, because Claude Code fires no hook on cancel, so nothing
  repaints the tab off magenta. Confirm from the timeline: a `web-send tab:
  thinking|working|executing` whose `UserPromptSubmit` fires within ~a second
  (bdeca061: +0.105s) was NEVER queued — a genuinely queued message's prompt
  arrives only at the turn boundary, seconds-to-minutes later. Cross-check that
  the preceding turn's last `tab_transitions` row is a busy state with no `stop`
  after it while the `interrupt-watch` stream ran on to `turn-over` (nothing saw
  the cancel). **(2) Why didn't it drain?** The match is a SUFFIX match
  (`chip_delivered` / app.js `promptMatches`) because the delivery can carry a
  prefix. Pull the chip text (`sqlite3 <state.db> "SELECT val FROM kv WHERE
  key='composer-queue'"`) and the delivered prompt and compare: if the prompt is
  `<something><chip text>` the message DID land — a pre-fix build only tolerated
  a `\n`-separated prefix (the attachment `@path\n` shape), so a
  terminal-restored draft glued on with NO separator (bdeca061: `testing` +
  the sent text as one prompt) never matched and the chip pinned forever, on
  every device and across reloads (the server-side reconcile `composer_queue`
  used the same rule). On a current build both are self-healing; a recurrence
  means the shared match regressed (grep test
  `test_app_js_drains_through_the_shared_prompt_match`) or the message genuinely
  never reached the TUI (the user Esc'd it out of the queue; the chip's ✕ hides
  it, the web can't unqueue). **(3) Is there anything to match AGAINST?** The
  match runs over the DELIVERED prompts, so a conversation pruned to nothing
  pins every chip with no defect of its own — see the take-back shape above
  (`_delivered_prompts` returning [] on a session that plainly has prompts is
  the tell). NB the ground truth for "was it delivered" is the TRANSCRIPT, not
  the hooks: a message queued MID-TURN is delivered into the RUNNING turn as a
  `queue-operation` `remove` + a `queued_command` `attachment` record, and fires
  NO `UserPromptSubmit` at all (measured on `7cb52905`, v2.1.220 — the enqueue
  is its own `queue-operation` `enqueue` record). So grep the jsonl for
  `queue-operation`/`queued_command` before concluding a queued message was
  lost; absence of a `UserPromptSubmit` proves nothing about it.
- **"the ctx bar animated forever" / "the ctx bar never animated" / "the ctx
  number was wrong right after a compaction"** — three different failures with
  one evidence trail, the `compacting` `state_files` rows plus the `PreCompact`/
  `PostCompact` `hook_events` (docs/dashboard.md *Compaction on the ctx bar*).
  A healthy compaction is a `write` row and a `remove` row ~2 minutes apart
  (104-139s across the measured runs); `bin/claude-audit.py anomalies <sid>`
  flags a `write` with no `remove`. Read them in this order:
  * **a `write` with no `remove`** = the compaction died on an API error or was
    interrupted, neither of which fires PostCompact (the no-hook-on-cancel
    invariant). NOT itself a broken bar — the dashboard ages the latch out
    after `config.COMPACT_MAX_S` — but if the user says the bar animated for
    *far* longer than 15 minutes, the SERVER is stale, not the latch: it does
    not hot-reload, so check it is running code that has `session_compacting`.
  * **no rows at all, but `PreCompact` is in `hook_events`** = the latch step
    didn't run. Check `errors` for `script='claude-compact-fmt.py'`, and check
    the handler wrote nothing because the session is UNHOSTED (decision `no
    state DB (unhosted session)` — a headless `claude -p`/daemon session has no
    pane and no web card, which is correct).
  * **rows present and the bar still never moved** = the read or the wire, not
    the producer. `curl /api/session/<sid>` for `compacting`, and remember the
    page caches its JS: a dashboard restart only bumps `BOOT_ID`, the user must
    hard-reload.
  * **the NUMBER was wrong (the bar drained to its pre-compaction figure, or
    didn't drain at all)** = `transcript.context_probe`, not the latch. A
    compaction writes no assistant record, so the probe reads the last PRE-
    compaction usage until the next real turn — measured 22 records of lag —
    unless it honours the `compact_boundary`'s `postTokens`. Confirm by
    tail-scanning the transcript for the last `compact_boundary` and comparing
    its `postTokens` against what the bar showed.
- **"the draft didn't clear after I sent"** — the `composer-draft` rows: a
  `stale` row around the send = the debounced save/clear reordered over the
  tunnel and the `seq` guard dropped the loser (working as intended); a MISSING
  `clear` row (only `write`s) = the clear POST never landed (network/JS).

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table),
(3) the code path responsible (file + mechanism), (4) a suggested fix. If the
evidence is inconclusive, say exactly which signal is missing and what extra
instrumentation would capture it next time.
