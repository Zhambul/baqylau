# Wiring

- **`~/.config/kitty/kitty.conf`** (appended at the end):
  ```
  allow_remote_control yes
  listen_on unix:/tmp/kitty
  ```
- **`~/.config/kitty/open-actions.conf`** — the ⧉ copy links (see
  [click-to-view.md](click-to-view.md)) resolve through kitty's open-actions machinery; one rule wires the
  custom scheme to the handler (picked up on the next config reload —
  `ctrl+shift+f5` — no kitty restart needed):
  ```
  protocol claude-copy
  action launch --type=background /path/to/repo/bin/claude-copy.py ${URL}
  ```
- **`~/.claude/settings.json`** — a `hooks` block. **Every** hook event points at
  a single entry, **`claude-hook.py`** (→ `plugins/claude_code/dispatch.py`), which
  reads the payload once and fans out **in-process** to whatever that event needs:

  ```json
  "hooks": { "PostToolUse": [ { "hooks": [
      { "type": "command", "command": "/ABS/PATH/kitty/bin/claude-hook.py" } ] } ],
      "Stop": [ { "hooks": [ { "type": "command", "command": ".../bin/claude-hook.py" } ] } ],
      "…every other event…": [ … same single entry … ] }
  ```

  **Exception — `WorktreeCreate`/`WorktreeRemove` must NOT be wired.** They are not
  observational hooks: registering a `WorktreeCreate` hook tells Claude Code "I will
  create the worktree" and the hook must print the worktree path to stdout (or return
  `hookSpecificOutput.worktreePath`). The dispatcher's silent exit-0 reads as "hook
  succeeded but returned no worktree path", failing every `EnterWorktree` / worktree-
  isolated agent spawn on the machine (seen live 2026-07-15, session `a8fe9640`).
  These two events therefore have no subscriber row — "did it fire?" is not
  answerable from the audit for them, by necessity.

  Previously each event listed several separate command entries — the tab-colour
  dispatch, a matcher-gated formatter, and the always-on `async` audit subscriber —
  so Claude Code spawned one python process **per concern per event**. The
  dispatcher collapses that to one entry per event; the **matcher routing and
  fan-out now live in `dispatch.py`'s `_plan()`**, reproducing the old wiring
  exactly (same tools, same order, same subsystem side-effects):

  | Hook | Routes to (in `_plan`) |
  |------|------------------------|
  | `SessionStart`     | tab `idle` + `split.handle("open")` |
  | `UserPromptSubmit` | tab `thinking` |
  | `PreToolUse`       | tab `pretool` (all tools) · `Task\|Agent` → `subagent_fmt.run_phase("push")` (stashes the Task description for the upcoming `SubagentStart`) · `Bash` → `cmd_pre` (rewrites the command to stream live — see [streaming.md](streaming.md) › *Live foreground streaming*; its `updatedInput` JSON is printed to the dispatcher's **stdout**, which is the one Claude Code reads) |
  | `PostToolUse` / `PostToolUseFailure` | tab `posttool` (all tools; ignored for an `agent_id` inner call) · `Bash` → `cmd_fmt` · `Read\|Edit\|Write\|MultiEdit\|NotebookEdit` → `file_fmt` · `Monitor` → `monitor_fmt`. **Failures fire `PostToolUseFailure`, not `PostToolUse`** — the dispatcher routes both identically, so a non-zero-exit command still reaches the mirror |
  | `SubagentStart`    | `subagent_fmt.run_phase("start")` (header `▶ <type> · <desc>` + colour slot; teammates arrive here too) |
  | `SubagentStop`     | `subagent_fmt.run_phase("stop")` (footer + releases the slot) |
  | `TaskCreated` / `TaskCompleted` | `task_fmt` (`✚`/`✓ task #N · <subject>` to the mirror) |
  | `PreCompact`       | tab `working` (compaction is busy with no tool/reply signal of its own — paint the busy magenta so the tab doesn't sit stale through it; `working`, not `thinking`, so no interrupt-watch is started) |
  | `Notification`     | tab `notify` (permission/approval → red `awaiting-command`; "waiting for your input" → green `awaiting-response`) |
  | `Stop`             | tab `stop` + `stop_fmt` (folds the turn's token/cost spend into the scoreboard) |
  | `StopFailure`      | tab `stop` (turn ended on an API error — keep the tab off the "busy" colour) + `stop_fmt` (fold whatever landed in the transcript; and when the payload carries an `agent_id` — a subagent that died on an API error, which fires no `SubagentStop` — finalise that agent's block/slot via `subagent_fmt.finalize`, else its streamer hangs and the tab stays blue) |
  | `SessionEnd`       | tab `clear` + `split.handle("close")` |
  | *every other event* (`Setup`, `PermissionRequest`, …) | no functional handler — records only the universal audit-subscriber row (below) |

  **Why one dispatcher, and how behaviour is preserved:**
  - **Audit vocabulary.** Every subsystem still writes its own audit rows under its
    *entry filename* (`hook_events.handler` / `errors.script` = `claude-cmd-fmt.py`,
    `claude-tab-status.py`, …), never the `claude-hook.py` the process actually runs
    under. The dispatcher stamps `A.set_handler("claude-<x>.py")` around each call
    (argv[0] is no longer the vocabulary — an explicit override is).
  - **The async audit subscriber is gone as a separate entry.** Its universal row
    (`handler="subscriber"`, every event's full payload) is now written *in-process*
    by the dispatcher — `A.hook_event(d, handler="subscriber")`. Audit writes never
    block and spool on a locked DB (the same property `claude-tab-status.py` relies
    on for its in-process transitions), so it stays off the turn's failure path. The
    two-row model (`subscriber` row **+** each handler's own decision row) that the
    audit queries expect (`handler != 'subscriber'`) is unchanged.
  - **Isolation.** Each subsystem runs through `hookkit.run()` (audit-then-swallow),
    exactly the crash isolation separate processes gave it — one failing step never
    blocks the others or the turn.
  - **Lazy handler imports.** Only the on-every-event subsystems (`adopt`,
    `tabstatus`, the `hookkit` harness) import at dispatcher module level; the
    matcher-selected handler modules (`cmd_pre`/`cmd_fmt`/`file_fmt`/
    `monitor_fmt`/`stop_fmt`/`task_fmt`/`subagent_fmt`/`split`) are imported
    inside their step thunks, so an event routed only to the tab dispatch
    (UserPromptSubmit, Notification, most tools' Pre/PostToolUse) pays ~17ms
    of imports instead of the full stack's ~69ms (measured, warm pyc). The
    import happens inside `hookkit.run()` under the step's entry identity, so
    a broken handler module is audited and swallowed per-step.
  - **Injected payload.** Since the dispatcher already consumed stdin, the formatters'
    `hookkit.read_payload()` (and `tabstatus` / `split`'s own readers) return a
    dispatcher-injected payload instead of re-reading an empty stdin. The old
    per-script shims (`claude-cmd-fmt.py` …) still exist and still read stdin — the
    e2e tests drive them directly, and nothing changed for them.

  All seven `*-fmt.py`/`-pre.py` handlers (incl. `claude-stop-fmt.py`) share
  **`hookkit.py`** (historical name `claude_hook.py`) — the harness owning the
  identical per-hook skeleton (stdin payload parse + mirror-log derivation, audited
  ignore-decisions, detached streamer spawn with the load-bearing
  `start_new_session=True`, and the top-level audit-then-swallow). The `agent_id`
  main-session guard is deliberately NOT in the harness: most handlers skip
  agent-inner events, but `claude-monitor-fmt.py` renders subagent monitors on
  purpose, so each handler makes that call explicitly.

  Agent-team support also needs the experimental feature itself enabled, via an
  `env` entry in the same `settings.json` (read at session start):
  ```json
  "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" }
  ```

  **The OTEL cost pipeline (`plugins/otel/`) requires telemetry env** in the same
  `env` block — without it the scoreboard's cost/tokens fall back to the SessionEnd
  transcript fold (which can't see hidden `auxiliary` agents). The receiver
  (`claude-otlp-receiver.py`) is spawned automatically at SessionStart *only when*
  `CLAUDE_CODE_ENABLE_TELEMETRY=1`; it derives its port from `CLAUDE_OTEL_PORT` (must
  match the OTLP endpoint):
  ```json
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4319",
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
    "OTEL_METRIC_EXPORT_INTERVAL": "2000",
    "CLAUDE_OTEL_PORT": "4319"
  }
  ```
  `http/json` (not the OTLP default `grpc`) because the receiver is a stdlib
  `http.server`; `delta` temporality because the receiver sums datapoints; the
  2 s export interval keeps the scoreboard's `≈ $` reasonably live.

  **Opt-in web-dashboard auto-start.** Setting `CLAUDE_DASHBOARD_AUTOSTART=1`
  in the same `env` block makes each hosted SessionStart bring up the
  per-machine web dashboard (`bin/claude-dashboard.py`, [dashboard.md](dashboard.md))
  if it isn't already running — the same spawn-if-not-running shape as the OTLP
  receiver, but auto-START only (the dashboard never idle-exits or auto-stops;
  stop it explicitly with `claude-dashboard.py stop`). OFF by default: without
  the env nothing spawns. `CLAUDE_DASH_PORT` overrides its port (default 8377).
  ```json
  "env": { "CLAUDE_DASHBOARD_AUTOSTART": "1" }
  ```

- **`~/.claude/settings.json` `statusLine`** — the account-usage capture shim
  ([dashboard.md](dashboard.md) › *Accounts & usage*). Claude Code exposes
  per-session rate-limit data (5h/7d) ONLY on the status-line command's stdin, so
  `bin/claude-statusline.py` is installed as the `statusLine.command` with the
  user's real status-line command as its argv — it stashes the usage + account
  into the state DB, then runs the real command with the same stdin and forwards
  its output. Wiring is a one-line PREPEND of the shim path to the existing
  command (backed up to `settings.json.bak-kitty-statusline`); revert by dropping
  the prefix. OPTIONAL: without it the account label still works (captured at
  SessionStart from the switcher env) — only the 5h/7d usage numbers go dark.
  ```json
  "statusLine": { "type": "command",
    "command": "/ABS/PATH/kitty/bin/claude-statusline.py <your real status-line command>" }
  ```

- **`~/.codex/config.toml` + `~/.codex/hooks.json`** — the STANDALONE codex host
  (codex CLI ≥ 0.142). Like Claude's hook table, this wiring lives outside the
  repo. Enable codex's hook system and point its `SessionStart` at the entry:
  ```toml
  # ~/.codex/config.toml
  [features]
  hooks = true          # canonical key (deprecated alias: codex_hooks)
  ```
  ```json
  // ~/.codex/hooks.json  (auto-loaded next to config.toml)
  { "hooks": { "SessionStart": [ {
      "matcher": "startup|resume|clear",
      "hooks": [ { "type": "command",
        "command": "/ABS/PATH/kitty/bin/claude-codex-session.py",
        "statusMessage": "kitty mirror" } ] } ] } }
  ```
  Codex hooks are Claude-compatible (stdin JSON: `session_id`/`cwd`/`source`/…),
  so `claude-codex-session.py` reads the payload exactly as a Claude hook does.
  **One manual trust step:** codex will not run a non-managed hook until it is
  trusted — on the next `codex` launch, run `/hooks` in the TUI and trust it (or
  pass `--dangerously-bypass-hook-trust`); editing the hook re-triggers review
  (trust is keyed to the hook's hash). Codex has **no SessionEnd hook** — teardown
  rides the codex-process liveness signal instead (see [codex.md](codex.md) › *standalone*).

## Interpreter: skip the pyenv shim (`retarget-python.py`)

Every hook fires a fresh `python3`. If that `python3` is the **pyenv shim** — a
bash script that re-runs `pyenv` on every call to pick a version — it costs
**~140ms of pure overhead per process** (measured 0.15s vs 0.01s for the
concrete interpreter it eventually execs). A single `PostToolUse` fans out to
five-plus hook processes, so the shim tax dominates end-to-end hook latency by
an order of magnitude — it swamps the scripts' own ~5ms of imports. (Child
processes are already fast: they spawn via `sys.executable`, which inside a
shim-launched interpreter is already the concrete binary.)

Two top-level entry shapes hit the shim: the `#!/usr/bin/env python3` **shebang**
on the `/abs/path/claude-*.py …` hook commands, and the literal `python3 …`
prefix on the `bin/claude-audit.py hook subscriber` commands in `settings.json`.
**`retarget-python.py`** rewrites both to an absolute concrete-interpreter path
(it takes `sys.executable`, which under the shim already resolves to pyenv's
*active* version, so it honours `pyenv version`):

```sh
./bin/retarget-python.py            # bake in the concrete interpreter (run once at setup)
./bin/retarget-python.py --revert   # restore portable `#!/usr/bin/env python3`
```

It is idempotent — **re-run it after any `pyenv` version change** to re-point the
hooks. Why not a faster startup flag (`-S`/`-I`) instead? Those shave only a
couple ms off interpreter init; the shim's bash+`pyenv` round-trip is the whole
cost, and only bypassing the shim removes it. Why not a `~/.pyenv/shims`-free
`PATH`? Shebangs and the `settings.json` `python3` token don't inherit a
reordered `PATH`, and the concrete path is unambiguous.

## Activating it

`listen_on` is read only at startup, so **fully quit and reopen kitty** (Cmd+Q,
not just a config reload), then start Claude Code in the new window. Color/script
edits take effect immediately (the script is re-read on every hook). Editing the
hook→state *mapping* in `settings.json` is picked up live by Claude Code too.

Verify remote control is live, then watch the colors cycle:

```sh
echo "$KITTY_LISTEN_ON"          # non-empty, e.g. unix:/tmp/kitty-23011
kitten @ ls >/dev/null && echo OK

for s in idle thinking working executing awaiting-bg awaiting-command awaiting-response; do
  ./bin/claude-tab-status.py "$s"; ping -c 4 127.0.0.1 >/dev/null   # ~3s each
done
./bin/claude-tab-status.py clear
```
