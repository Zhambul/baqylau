<p align="center"><img src="docs/assets/logo.svg" width="112" alt="baqylau"></p>

# baqylau

**A kitty-terminal cockpit for Claude Code — built entirely out of hooks.**

*baqylau* (Kazakh *бақылау*) means observation — watching over every session.
Formerly known as *claude-kitty*.

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
  recovery signal.
- **🪞 Command mirror pane** — a right-side split showing everything Claude
  does as colored streaming blocks: foreground/background commands (live
  output, syntax-highlighted), monitors, subagents and teammates (full
  transcript: prompt, messages, tools, result), and every codex run. Command
  blocks carry clickable ⧉ copy links; file-op one-liners click-to-expand
  their content in place (highlighted code, line-numbered diffs). A 5-row
  scoreboard underneath tracks messages, activity, tokens, and cost.
- **🔍 Audit trail** — every hook event, tab transition, stream lifecycle, and
  swallowed exception recorded to SQLite, so any bug is debuggable after the
  fact — with a live **⚠ warning light** on the scoreboard (and `⚠ audit:`
  one-liners in the mirror) whenever the session swallows an exception.

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
3. Wire the hooks: point every supported Claude Code hook event directly at
   the Claude Code plugin entry:
   ```json
   "hooks": { "PostToolUse": [ { "hooks": [
       { "type": "command", "command": "python3 /ABS/PATH/baqylau/harness/impl/claude_code/bin/hook.py" } ] } ],
       "…every other event…": [ "… same single entry …" ] }
   ```
4. Wire the ⧉ copy links (`~/.config/kitty/open-actions.conf`):
   ```
   protocol baqylau-content
   action launch --type=background python3 /ABS/PATH/baqylau/terminal/bin/content.py ${URL}
   protocol baqylau-view
   action launch --type=background python3 /ABS/PATH/baqylau/terminal/bin/view.py ${URL}
   ```
5. Using pyenv? Run `./bin/retarget-python.py` once to skip the ~140ms/process
   shim tax.

## Usage

Everything activates automatically per session — the mirror opens on
`SessionStart`, the tab colors follow the hooks. Manual controls:

```sh
# Mirror pane
python3 terminal/bin/panes.py toggle|grow|shrink|reset|setpct <N>

# Smoke-test the tab colors (~3s each)
for s in idle thinking working executing awaiting-bg awaiting-command awaiting-response; do
  ./bin/claude-tab-status.py "$s"; ping -c 4 127.0.0.1 >/dev/null
done
./bin/claude-tab-status.py clear

# Audit CLI — the primary debugging tool
python3 bin/baqylau-audit.py sessions            # recent sessions
python3 bin/baqylau-audit.py anomalies <sid>     # canned queries for known bug signatures
python3 bin/baqylau-audit.py errors    <sid>     # swallowed exceptions, full tracebacks
python3 bin/baqylau-audit.py timeline  <sid>     # merged chronological story of a session
python3 bin/baqylau-audit.py sql "<query>"       # free-form read-only SQL (sql-write for fixups)
```

## Architecture

Producer/renderer split over SQLite: ~20 short-lived hook processes plus
detached tailers append width-independent *paint ops* to a per-session state
DB; a single renderer inside the pane paints them at the live width and
reflows on resize. The code is layered so agent tools and terminals are both
pluggable:

```
core/        the floor: what knows the OS, not the domain — env, processes,
             locks, git, and core/daemon/ (the daemon's door, both sides)
domain/      the facts themselves: the closed canonical event vocabulary
diagnostics/ the operational database, whole: what the MACHINERY did — its
             writers (reached from every process), its reads, its telemetry
engine/      the neutral middle: engine/store/ (one owner per table),
             engine/interpret/ (the one thread that pulls, translates, reacts),
             engine/projections/ (a fold per question), engine/queries/
terminal/    the terminal concern, whole: contract + models, the panes it
             paints, and terminal/impl/<name>/ — one directory per terminal
             (kitty today), the only place a terminal's name appears
harness/     the harness concern, whole: contract + models, the hook channel,
             the services over one plugin, and harness/impl/<name>/ — one
             directory per agent tool (claude_code · codex), the only place a
             harness's name appears
app/         the composition root: the only package that knows WHICH harnesses
             and which terminal are installed, plus the services that compose
             concerns the engine keeps apart
bin/         the repository's own CLIs (audit, dashboard). Every entry an
             EXTERNAL config names lives with its concern instead —
             harness/impl/<name>/bin/ and terminal/bin/ — so a captured
             path never crosses a concern boundary
```

## Testing

```sh
make test        # hermetic e2e suite (fake kitten, per-test tmp dirs; parallel via pytest-xdist)
make test-seq    # same, sequential (debugging / no xdist)
make test-all    # + opt-in real-kitty smoke tests
```