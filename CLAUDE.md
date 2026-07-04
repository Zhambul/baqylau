# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A kitty-terminal integration for Claude Code, built entirely out of Claude Code **hooks** plus detached background processes. Three user-facing features:

1. **Tab colors** (`claude-tab-status.py`) — the kitty tab color reflects the session state (grey idle · magenta busy · blue running/awaiting · red asking-you · green your-turn) via `kitten @ set-tab-color` over the socket in `$KITTY_LISTEN_ON`.
2. **Command mirror pane** (`claude-split.py` + `claude-mirror.py`) — a right-side vertical split showing every command, file op, subagent, teammate, monitor, and codex run as colored streaming blocks, plus a 5-row scoreboard window (`claude-scorebar.py`) underneath (session id · ✉ message census · ▪ summary · Σ token breakdown · tools).
3. **Audit trail** (`claude_audit.py`) — always-on SQLite recording of every hook event, tab transition, slot claim, stream lifecycle, paint op, and swallowed exception, at `~/.claude/kitty-audit/audit.db`.

There is no build system, package manifest, or test suite. Scripts are invoked directly by hooks wired in `~/.claude/settings.json` (the hook table is in README.md § Wiring — the settings file itself is *not* in this repo). Python scripts target the system `python3`; only `pygments` is an (optional, probed-for) dependency.

## Commands

```sh
# Audit CLI — the primary debugging tool (run from repo root)
python3 claude_audit.py sessions            # recent sessions
python3 claude_audit.py anomalies <sid>     # canned queries for known bug signatures — start here
python3 claude_audit.py errors    <sid>     # swallowed exceptions, full tracebacks
python3 claude_audit.py timeline  <sid>     # merged chronological story of a session
python3 claude_audit.py sql "<query>"       # free-form SQL

# Manual smoke test — cycle the tab colors (~3s each)
for s in idle thinking working executing awaiting-bg awaiting-command awaiting-response; do
  ./claude-tab-status.py "$s"; ping -c 4 127.0.0.1 >/dev/null
done
./claude-tab-status.py clear

# Mirror pane controls
./claude-split.py toggle|grow|shrink|reset|setpct <N>
```

To debug a reported session bug, prefer the **`audit-debug` skill** (`.claude/skills/audit-debug/SKILL.md`) — it walks the triage order (anomalies → errors → timeline → targeted SQL) and documents the full audit schema.

Script edits take effect immediately (hooks re-exec them). Only `kitty.conf` changes (e.g. `listen_on`) need a full kitty restart, and a renderer/interpreter change needs a mirror toggle off/on.

## Architecture

**Producer/renderer split via paint ops.** Hook handlers and tailers never print to the pane. They append width-INDEPENDENT **paint ops** (`rule`/`label`/`code`/`gut`/`line`, built by `claude_ops.py`) as rows of the per-session state DB's `ops` table (`/tmp/claude-mirror-<session_id>.log.state.db` — the `.log` path is only the KEY everything derives from; no log file exists). The single renderer `claude-mirror.py`, running inside the pane, polls new rows by id, paints ops at the live pane width and re-renders everything on SIGWINCH so content reflows. Consequence: anything width-dependent (wrapping, gutters, dividers) belongs in the renderer; anything width-independent (syntax highlighting, one-liner reflow) runs once at op creation.

**Process model.** ~20 short-lived hook processes plus detached long-lived tailers/watchers, coordinating through SQLite: a per-session state DB (`claude_state.py` — ops, scoreboard, slots, hand-offs; parked as `*.keep` at SessionEnd and restored on resume, so a resumed session replays its mirror history), a global window-keyed tab DB (`/tmp/claude-kitty-tab.db` — tab colour + watcher pid locks), and `~/.claude/kitty-mirror.db` (remembered pane sizes). The only plain files left are what physics demands: the fg tee'd `.out` streams + `.done` sentinels (written by the rewritten command itself). Everything is Python; `claude-tab-status.py` reads the state/tab DBs read-only (`mode=ro`, so a probe can never create a DB whose existence is a liveness signal):
- `claude-cmd-pre.py` (PreToolUse Bash) rewrites foreground commands via `updatedInput` to tee output into a side file so it streams live; `claude-cmd-fmt.py` (PostToolUse) hands the real outcome to the tailer via a take-once hand-off record in the state DB.
- `claude-stream.py` tails background/monitor/fg output files; `claude-substream.py` tails a subagent/teammate transcript (`subagents/agent-<id>.jsonl`) — the only in-order source of its prompt/messages/tools/result; `claude-codex-watch.py` (one per session) discovers every codex run from two global directories and spawns `claude-codex-stream.py` per run.
- Detached processes are spawned with `start_new_session=True` — spawning from a hook with bash `&` leaves them in the hook's process group, which Claude Code waits to drain (this hung SessionStart once; see `claude-codex-launch.py`).

**Slot rows** (`claude_slots.py`, the state DB's `live` table) do double duty: they assign each concurrent stream a palette color (5-color palettes per kind: bg/monitor/subagent/teammate/codex) AND they are the tab tracker's liveness signal — `claude-tab-status.py stop` keeps the tab blue exactly while a live-pid row (kind `bg`/`monitor`/`fg`/`sub.pid`) exists. Rows store the owner pid and are liveness-checked (`claude_state.pid_alive` — the ONE probe, where EPERM = exists-but-foreign-owned = alive) before being trusted; stale ones are stolen.

**Shared modules:** `claude_ops.py` (paint ops, scoreboard `bump()`, transcript token/cost accounting deduped by `message.id`, model pricing, `parse_redirect`, `claude_dirs` ancestor-`.claude/` walking, plus the shared vocabulary producers must not re-encode: the semantic colour table `SLATE/ORANGE/RED/GREEN/YELLOW/BLUE/AMBER`, the file-op `FILE_LABEL`/`FILE_RGB` maps, `fmt_dur`, `kfmt`; also `bump_transcript`'s fold now sums a per-category token split into the `tk_in`/`tk_out`/`tk_read`/`tk_create` counters that `token_parts()` renders as the scorebar's Σ row — the sole token display, reconciling with `claude --resume`'s "Usage by model" total (the `▪` row dropped its billed-spend `tok` chip, though the `tokens` counter still backs the cost figure)), `claude_model.py` (model/effort/context-window resolution for agents — pure functions over agent defs / meta.json / settings / the parent transcript), `claude_msgs.py` (the agent-team message tracker behind the scorebar's ✉ row), `claude_kitty.py` (kitten binary lookup, silenced `kitten @` calls, `iter_windows` over `kitten @ ls`, `set_tab_color`), `claude_state.py` (per-session RUNTIME state in SQLite at `/tmp/claude-mirror-<sid>.log.state.db` — the mirror's `ops` stream, scoreboard counters, team-message tracker, per-agent records incl. the stop flag + resume checkpoint, description queue, take-once hand-offs, pid-liveness locks (`lock_acquire`/`lock_release`), the `live` slot table; also the transaction primitives every writer uses — `immediate()` BEGIN-IMMEDIATE context manager, `counter_add/set/get`, `transcript_fold` (the token-cursor transaction `claude_ops.bump_transcript` parses/prices into) — and `tab_state()`, the one sanctioned reader of the tab DB owned by `claude-tab-status.py`; load-bearing for behavior and deliberately separate from the audit DB; its file-existence is the session-alive signal watchers poll), `claude_render.py` (pygments highlighting, ANSI-aware wrap, gutters, escape-sequence unescape, section-banner emphasis, the renderers' `term_width`/`fit`), `claude_slots.py` (transactional slot claim/release on the `live` table), `claude_audit.py` (audit writes — degrade to a spool file, never raise into a hook), `claude_paths.py` (stdlib-only leaf: the ONE owner of the `/tmp/claude-mirror-<key>.log` path format — sanitize/slug/derive/parse; every other module gets the format from here, never re-encodes it), `claude_hook.py` (the hook-handler harness: `run()` audit-then-swallow entry, `read_payload()`, `ignore()`, `is_failure()`, `spawn_streamer()` with the load-bearing `start_new_session=True` — new hook handlers go through it; the `agent_id` guard stays per-handler because `claude-monitor-fmt.py` deliberately has none; `claude-stop-fmt.py` is the `Stop`/`StopFailure` producer that folds the final-turn tail into the scoreboard, the one sanctioned Stop-time state-DB writer — distinct from `claude-tab-status.py`, which stays `mode=ro`), `claude_tail.py` (the tailer skeleton: `FileTailer` byte-position line pump with the read-exactly-`size-pos` subtlety and the `consumed` checkpoint offset, `wait_for()`, `POLL_S`/`BACKSTOP_S`, and `stream_lifecycle` — the context manager owning `A.stream_start`/`stream_end` + crash-audit-then-swallow; new detached tailers go through it, which satisfies the stream-audit rule below by construction).

**Everything is keyed by `session_id`** — the state DB, pane kitty vars (`claude_mirror`/`claude_session`), sidecars — so parallel sessions never collide. Exception: background-job detection is per-project (temp slug from cwd), so two sessions in one directory can cross-talk.

## Hard-won invariants (violating these reintroduces fixed bugs)

- **Hooks must never block or fail.** Every hook path exits 0 and swallows exceptions — but every swallow site must record to the audit first (`claude_audit` errors table). The tab-color path writes audit rows fire-and-forget.
- **Claude Code fires NO hook on cancel/interrupt** — no Stop, nothing. Every cancellation path needs its own recovery signal: writer-liveness for commands, `meta.json` `stoppedByUser` for subagents, the transcript's `[Request interrupted by user]` line for plain replies (`interrupt-watch`). Cancel-before-first-hook has no signal at all and is deliberately left unhandled — do not re-add an idle-timeout backstop; it false-positived on every long think.
- **Main session only:** any hook event carrying an `agent_id` is a subagent/teammate inner call — tab dispatch ignores it, and cmd/file formatters skip it (the substream owns subagent rendering; handling both would duplicate/mis-order).
- **Release slot rows *before* calling `bg-recheck`**, or the recheck sees its own row. `bg-recheck` flips only a currently-blue tab and only when no live row remains.
- **Duplicate events are real:** `SubagentStart` and `SubagentStop` can each fire more than once for background agents — both handlers guard on slot state.
- **Failures arrive on `PostToolUseFailure`, not `PostToolUse`** — any new PostToolUse hook must be wired to both or failures silently vanish.
- Empirically-confirmed but undocumented Claude Code behaviors this repo depends on (`updatedInput` command rewriting, Ctrl+B's `backgroundTaskId`+`backgroundedByUser` payload, `stoppedByUser` in meta.json, the interrupted-transcript line) are called out in README.md — check there before assuming a payload field exists or not.

## Every new feature must be audit-covered

The audit trail is only useful if it has no blind spots — a mechanism that leaves no rows is undebuggable after the fact (the no-hook-on-cancel bug class was only cracked once auditing existed). When adding or changing a feature, wire it into `claude_audit.py` **in the same commit**:

- **New hook handler** → call `A.hook_event(payload, handler=, decision=)` with a `decision` string that says what the handler chose to do and why (the decision column is what makes `hook_events` diagnostic, not just a log).
- **New detached process / tailer** → `A.stream_start(...)` on spawn and `A.stream_end(stream_id, end_reason=)` on every exit path, plus `A.spawn(...)` at the launch site (wrap the tailer in `claude_tail.stream_lifecycle` and spawn via `claude_hook.spawn_streamer` and all of this comes for free). A stream with `ended_at IS NULL` is an anomaly signal — don't create streams that legitimately never end.
- **New coordination/marker/sentinel file** → `A.state_file(log, path, action, content)` on write and remove; new slot kinds go through `claude_slots.py` so `A.slot(...)` rows come for free.
- **New tab-state input** → record via `A.transition(...)` with `applied` and a `reason`, including (especially) the bailed/skipped paths.
- **New swallow site** → `A.error(...)` before the `except: pass` (existing invariant above — it applies to new code too).
- **New accounting/derived numbers** (scoreboard fields, cost math) → make the inputs reconstructible: either the raw source is already audited (e.g. the transcript path in `sessions`) or add rows for it. "The number is wrong" must be answerable from the DB plus the named source, as the `message.id` token-dedup bug was.
- Then extend the **`anomalies` canned queries** in `claude_audit.py` if the feature has a known failure signature, and update **`.claude/skills/audit-debug/SKILL.md` in BOTH places**: the **schema table** (what the new rows/columns/kinds are) *and* the **"Known bug shapes" playbook** (which symptom should now consult that evidence, and what the tell-tale value looks like). These drift independently — the schema table alone got updated once while the playbook kept pointing at the old sources, which makes the skill *look* current while it triages blind. A new evidence source that no bug shape references is a smell: either wire it into an existing shape or add the shape it exists for. New tables/columns belong in the same migration style as the existing ones (WAL-safe, spool-file fallback, never raise into a hook).

## README.md is the design doc

The README (~1000 lines) is the authoritative, exhaustively-detailed record of how every mechanism works *and why the alternatives failed*. When changing behavior, update the corresponding README section in the same commit — the "why not X" notes there are what prevents regressing to already-rejected designs.
