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
| `sessions` | Claude session | cwd, transcript_path, mirror_log, kitty_window_id, started_at/ended_at, end_reason, env (JSON of CLAUDE_MIRROR_*/KITTY_* seen at start) |
| `hook_events` | hook invocation | hook, tool_name, agent_id ('' = main session), handler (script), **decision** (what the handler chose to do), **payload** (full hook stdin JSON, verbatim) |
| `tab_transitions` | tab-colour decision | dispatch (raw arg: pretool/stop/bg-recheck/bg-watch/notify/…), prev_state → new_state, applied (0 = skipped/bailed), **reason** |
| `slots` | marker-file event | kind (bg/monitor/fg/sub), slot_n, agent_id, owner_pid, action (claim/claim-id/steal-stale/claim-denied/release/release-id/set-owner), marker_path |
| `streams` | detached tailer/streamer/watcher | kind (fg/bg/monitor/subagent/teammate/codex/codex-watcher), agent_id/task_id, src_path, pid, started_at/ended_at, **end_reason** (writer-gone/sentinel/stop-sentinel/stoppedByUser/converted-ctrl-b/backstop-timeout/crash/…), lines_emitted |
| `ops` | paint op written to the mirror log | producer (script), op (the JSON paint op — full pane reconstruction, survives SessionEnd) |
| `errors` | swallowed exception | script, func, **traceback** (full), context (JSON of args in hand) |
| `spawns` | detached process launch | parent_script, child_pid, argv, purpose |
| `state_files` | coordination-file transition | path, action (write/remove/remove-stale), content (.done sentinels, .fg-live markers, sub.done sentinels) |

## Triage order

1. **`python3 claude_audit.py anomalies <sid>`** — canned queries for known bug
   signatures: swallowed errors, streams that never ended, slot claims without
   release, tab left on a busy colour, duplicate SubagentStart, start-without-stop,
   failed tools, spawns that never registered a stream. Start here; a non-empty
   section usually IS the bug.
2. **`python3 claude_audit.py errors <sid>`** — full tracebacks for every swallowed
   exception. An error just before the symptom's timestamp is the prime suspect.
3. **`python3 claude_audit.py timeline <sid>`** — the merged chronological story
   (hooks, tab transitions, slots, streams, spawns, state files, errors). Find the
   symptom's moment, then read the surrounding ~30 lines both ways.
4. **Free-form**: `python3 claude_audit.py sql "<query>"` — e.g. pull the full
   payload of one hook event, or diff `ops` against what the pane actually showed.

## Known bug shapes → what to look for

- **Tab stuck blue** — a `slots` claim (bg/fg/monitor/sub) with no release + a
  `streams` row with `ended_at IS NULL`, or a `tab_transitions` `bg-recheck`/`bg-watch`
  row with `applied=0` whose reason explains why it refused to clear.
- **Tab stuck magenta** — last transition is thinking/working and no later Stop:
  check `hook_events` for a missing Stop (cancelled turn — no hook fires) and whether
  `interrupt-watch` recorded a bailed flip.
- **Tab flips green too early** — a `bg-recheck`/`bg-watch`/`notify` transition with
  `applied=1` while a `streams` row was still open; the reason column shows what it
  (wrongly) concluded.
- **Mirror block never closes / frozen pane** — the `streams` row's end_reason
  (backstop-timeout = the completion signal never came; crash = see `errors`);
  `state_files` shows whether the `.done` / `sub.done` sentinel was ever written.
- **Command never appeared in the mirror** — `hook_events` decision column: was it
  "ignored: a live fg block is already in flight" (stale `.fg-live`), "ignored:
  agent_id", or did the hook never fire at all?
- **Double-rendered subagent** — duplicate SubagentStart in `hook_events` where the
  second's decision is NOT "ignored: duplicate".
- **Cross-session contamination** — the same task_id/marker_path appearing under two
  session_ids.

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table),
(3) the code path responsible (file + mechanism), (4) a suggested fix. If the
evidence is inconclusive, say exactly which signal is missing and what extra
instrumentation would capture it next time.
