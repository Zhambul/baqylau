# claude-kitty

**A kitty-terminal cockpit for Claude Code — built entirely out of hooks.**

Tab colors that track what Claude is doing, a live mirror pane streaming every
command and agent, and an always-on SQLite audit trail. No daemon, no build
step — just Python scripts fired by Claude Code hooks, coordinating through
SQLite.

<!-- demo screenshot / recording placeholder -->

## Features

- **🎨 Tab colors** — the kitty tab reflects the session state at a glance,
  even from another tab: grey idle · magenta busy · blue running/awaiting ·
  red asking-*you* · green your-turn. Handles the hard part: Claude Code fires
  *no hook* on cancel/interrupt, so every cancellation path has its own
  recovery signal. → [docs/tab-colors.md](docs/tab-colors.md)
- **🪞 Command mirror pane** — a right-side split showing everything Claude
  does as colored streaming blocks: foreground/background commands (live
  output, syntax-highlighted), monitors, subagents and teammates (full
  transcript: prompt, messages, tools, result), and every codex run. Command
  blocks carry clickable ⧉ copy links; file-op one-liners click-to-expand
  their content in place (highlighted code, line-numbered diffs). A 5-row
  scoreboard underneath tracks messages, activity, tokens, and cost.
  → [docs/mirror-pane.md](docs/mirror-pane.md)
- **🔍 Audit trail** — every hook event, tab transition, stream lifecycle, and
  swallowed exception recorded to SQLite, so any bug is debuggable after the
  fact. → [docs/audit.md](docs/audit.md)

## Requirements

- [kitty](https://sw.kovidgoyal.net/kitty/) with remote control enabled
- [Claude Code](https://claude.com/claude-code)
- System `python3` (no package manifest; `pygments` and `wenmode` are optional
  runtime extras for syntax highlighting and markdown rendering)
- Optional: codex CLI ≥ 0.142 for the standalone codex host

## Installation

1. Clone the repo — the scripts run in place, nothing to build or install.
2. Enable kitty remote control (`~/.config/kitty/kitty.conf`, then fully
   restart kitty):
   ```
   allow_remote_control yes
   listen_on unix:/tmp/kitty
   ```
3. Wire the hooks: point **every** hook event in `~/.claude/settings.json` at
   the single dispatcher entry:
   ```json
   "hooks": { "PostToolUse": [ { "hooks": [
       { "type": "command", "command": "/ABS/PATH/kitty/bin/claude-hook.py" } ] } ],
       "…every other event…": [ "… same single entry …" ] }
   ```
4. Wire the ⧉ copy links (`~/.config/kitty/open-actions.conf`):
   ```
   protocol claude-copy
   action launch --type=background /ABS/PATH/kitty/bin/claude-copy.py ${URL}
   ```
5. Using pyenv? Run `./bin/retarget-python.py` once to skip the ~140ms/process
   shim tax.

The full hook/routing table, the telemetry env for OTEL-accurate cost
tracking, the mirror keybindings, and the codex host wiring are in
**[docs/wiring.md](docs/wiring.md)**.

## Usage

Everything activates automatically per session — the mirror opens on
`SessionStart`, the tab colors follow the hooks. Manual controls:

```sh
# Mirror pane
./bin/claude-split.py toggle|grow|shrink|reset|setpct <N>

# Smoke-test the tab colors (~3s each)
for s in idle thinking working executing awaiting-bg awaiting-command awaiting-response; do
  ./bin/claude-tab-status.py "$s"; ping -c 4 127.0.0.1 >/dev/null
done
./bin/claude-tab-status.py clear

# Audit CLI — the primary debugging tool
python3 bin/claude-audit.py sessions            # recent sessions
python3 bin/claude-audit.py anomalies <sid>     # canned queries for known bug signatures
python3 bin/claude-audit.py errors    <sid>     # swallowed exceptions, full tracebacks
python3 bin/claude-audit.py timeline  <sid>     # merged chronological story of a session
python3 bin/claude-audit.py sql "<query>"       # free-form read-only SQL (sql-write for fixups)
```

## Architecture

Producer/renderer split over SQLite: ~20 short-lived hook processes plus
detached tailers append width-independent *paint ops* to a per-session state
DB; a single renderer inside the pane paints them at the live width and
reflows on resize. The code is layered so agent tools and terminals are both
pluggable:

```
core/        tool- and terminal-agnostic runtime   (imports only core)
frontends/   terminal adapters — kitty today       (import core at most)
plugins/     one adapter per agent tool:
             claude_code · codex · otel            (import core + frontends)
bin/         executable entry scripts (claude-*.py) — filenames are load-bearing
```

Details, module map, and the dependency rules:
[docs/architecture.md](docs/architecture.md).

## Testing

```sh
make test        # hermetic e2e suite (fake kitten, per-test tmp dirs; parallel via pytest-xdist)
make test-seq    # same, sequential (debugging / no xdist)
make test-all    # + opt-in real-kitty smoke tests
```

Dev-only deps in `requirements-dev.txt`; see
[docs/testing.md](docs/testing.md).

## Documentation

The **[docs/](docs/README.md)** directory is the design doc — an exhaustive
record of how every mechanism works *and why the alternatives failed*:

- [Tab colors](docs/tab-colors.md) · [Architecture](docs/architecture.md) ·
  [Wiring](docs/wiring.md)
- [Mirror pane](docs/mirror-pane.md) · [Copy links & click-to-view](docs/click-to-view.md) ·
  [Command streaming](docs/streaming.md)
- [Subagents & teams](docs/subagents.md) · [Scoreboard](docs/scoreboard.md) ·
  [OTEL cost pipeline](docs/otel.md) · [Codex](docs/codex.md)
- [Audit system](docs/audit.md) · [Testing](docs/testing.md)
