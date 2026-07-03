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
| `hook_events` | hook invocation | hook, tool_name, agent_id ('' = main session), handler (script), **decision** (what the handler chose to do), **payload** (full hook stdin JSON, verbatim). ALL 30 hook events are recorded via a universal async subscriber (handler = 'subscriber', empty decision) — incl. PermissionRequest/Denied, PostToolBatch, MessageDisplay, TeammateIdle, Pre/PostCompact, ConfigChange, CwdChanged, FileChanged, Worktree\*, Elicitation\*, Setup, UserPromptExpansion, InstructionsLoaded — on top of the mirror handlers' own decision-carrying rows for the events they process. So "did event X even fire?" is always answerable from the subscriber rows, and a handler row can be cross-checked against the subscriber's independent record. |
| `tab_transitions` | tab-colour decision | dispatch (raw arg: pretool/stop/bg-recheck/bg-watch/notify/…), prev_state → new_state, applied (0 = skipped/bailed **or the kitten @ call failed** — reason then carries "kitten @ failed rc=N"), **reason** |
| `slots` | marker-file event | kind (bg/monitor/fg/sub), slot_n, agent_id, owner_pid, action (claim/claim-id/steal-stale/claim-denied/release/release-id/set-owner), marker_path |
| `streams` | detached tailer/streamer/watcher | kind (fg/bg/monitor/subagent/teammate/codex/codex-watcher/**bg-watch/interrupt-watch**), agent_id/task_id, src_path, pid, started_at/ended_at, **end_reason** (writer-gone/sentinel/stop-sentinel/stoppedByUser/converted-ctrl-b/backstop-timeout/crash/state-moved-on/cleared-to-green/killed-or-crashed/…), lines_emitted. An open row from a dead pid = the watcher/tailer died — for bg-watch that IS the stuck-blue bug |
| `ops` | paint op written to the mirror log | producer (script), op (the JSON paint op — full pane reconstruction, survives SessionEnd) |
| `errors` | swallowed exception | script, func, **traceback** (full), context (JSON of args in hand) |
| `spawns` | detached process launch | parent_script, child_pid, argv, purpose |
| `state_files` | coordination-file transition | path, action (write/remove/remove-stale/**bump/bump-transcript/msg-transitions**), content (.done sentinels, .fg-live markers, sub.done sentinels; for bump\* actions: the scoreboard deltas + resulting totals — the trail for wrong-scoreboard-number bugs) |
| `pane_events` | mirror/scoreboard pane operation | action (open/close/toggle-on/toggle-off/grow/shrink/reset/setpct), **ok** (verified against kitty — 0 means the pane genuinely isn't there), detail (bias/resulting width). First stop for "frozen/missing pane" reports |

## Triage order

1. **`python3 claude_audit.py anomalies <sid>`** — canned queries for known bug
   signatures: swallowed errors, streams that never ended, slot claims without
   release, tab left on a busy colour, duplicate SubagentStart, start-without-stop,
   failed tools, spawns that never registered a stream, pane operations that
   failed, tab applies where `kitten @` failed. Start here; a non-empty
   section usually IS the bug.
2. **`python3 claude_audit.py errors <sid>`** — full tracebacks for every swallowed
   exception. An error just before the symptom's timestamp is the prime suspect.
3. **`python3 claude_audit.py timeline <sid>`** — the merged chronological story
   (hooks, tab transitions, slots, streams, spawns, state files, pane ops, errors).
   Find the symptom's moment, then read the surrounding ~30 lines both ways.
4. **Free-form**: `python3 claude_audit.py sql "<query>"` — e.g. pull the full
   payload of one hook event, or diff `ops` against what the pane actually showed.

## Known bug shapes → what to look for

- **Tab stuck blue** — a `slots` claim (bg/fg/monitor/sub) with no release + a
  `streams` row with `ended_at IS NULL`, or a `tab_transitions` `bg-recheck`/`bg-watch`
  row with `applied=0` whose reason explains why it refused to clear. Also check the
  `bg-watch` **stream row itself**: `killed-or-crashed` / still-open = the watcher died
  and nothing was left to clear the blue; and an apply whose reason says
  "kitten @ failed rc=N" = the green WAS decided but never reached kitty.
- **Tab stuck magenta** — last transition is thinking/working and no later Stop:
  check `hook_events` for a missing Stop (cancelled turn — no hook fires), the
  `interrupt-watch` **stream row's end_reason** (`no-interrupt-within-30m` vs
  `killed-or-crashed` vs a bailed flip), and whether the final apply carried a
  "kitten @ failed" reason.
- **Tab flips green too early** — a `bg-recheck`/`bg-watch`/`notify` transition with
  `applied=1` while a `streams` row was still open; the reason column shows what it
  (wrongly) concluded.
- **Tab shows a colour the audit says it shouldn't** — trust `applied=1` rows only:
  any transition with "kitten @ failed rc=N" in the reason means the script decided a
  colour but kitty never showed it (dead socket, closed tab), and the persisted state
  file may now disagree with the real tab.
- **Mirror block never closes** — the `streams` row's end_reason
  (backstop-timeout = the completion signal never came; crash = see `errors`);
  `state_files` shows whether the `.done` / `sub.done` sentinel was ever written.
- **Frozen / missing / doubled pane** — `pane_events` first: an `open`/`toggle-on`
  with `ok=0` means the mirror (or the scoreboard bar — see detail) genuinely never
  opened; a resize whose detail shows an unchanged resulting width did nothing. Then
  cross-check `spawns` (was the renderer launched?) and `errors` (renderer crash).
- **Wrong scoreboard numbers** — replay the `state_files` `bump` / `bump-transcript`
  rows: each carries the delta AND the resulting totals, so find the exact bump where
  the running total diverges from what the session actually did (`hook_events` is the
  ground truth to diff against); `bump-transcript` rows also carry the `txpos` cursor —
  a cursor that jumps backwards or re-covers a range = double-counting. `msg-transitions`
  rows are the same trail for the ✉ census.
- **Codex run missing from (or duplicated across) same-repo sessions** — `slots` rows
  with kind `codex-claim`: `claim` = this session owns the run, `claim-denied` (+ the
  holder pid) = another session's watcher took it, `steal-stale` = a dead session's
  claim was taken over.
- **Command never appeared in the mirror** — `hook_events` decision column: was it
  "ignored: a live fg block is already in flight" (stale `.fg-live`), "ignored:
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

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table),
(3) the code path responsible (file + mechanism), (4) a suggested fix. If the
evidence is inconclusive, say exactly which signal is missing and what extra
instrumentation would capture it next time.
