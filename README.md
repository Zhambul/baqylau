<p align="center"><img src="docs/assets/logo.svg" width="112" alt="baqylau"></p>

# baqylau

**A kitty-terminal cockpit for Claude Code — built entirely out of hooks.**

*baqylau* (Kazakh *бақылау*) means observation — watching over every session.
Formerly known as *claude-kitty*.

Tab colors that track what Claude is doing, a live mirror pane streaming every
command and agent, an always-on SQLite audit trail, and a live web dashboard.
The hook pipeline is Python; the dashboard is a compiled Svelte application.

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
- Python 3.12 and a project virtual environment at `.venv`
- Node.js 22.22.3 and npm 10.9.8 for dashboard builds and tests
- Optional: codex CLI ≥ 0.142 for the standalone codex host

## Installation

1. Clone the repo, create its virtual environment, and install its runtime and
   development dependencies:
   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
   make build-frontend
   ```
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
       { "type": "command", "command": "/ABS/PATH/baqylau/.venv/bin/python /ABS/PATH/baqylau/client/claude_hook.py" } ] } ],
       "…every other event…": [ "… same single entry …" ] }
   ```
4. Wire the ⧉ copy links (`~/.config/kitty/open-actions.conf`):
   ```
   protocol baqylau-content
   action launch --type=background /ABS/PATH/baqylau/.venv/bin/python /ABS/PATH/baqylau/client/terminal_content.py ${URL}
   protocol baqylau-view
   action launch --type=background /ABS/PATH/baqylau/.venv/bin/python /ABS/PATH/baqylau/client/terminal_view.py ${URL}
   ```
5. Run `bin/retarget-python` once to point configured
   Claude hooks directly at the project environment and skip the pyenv shim.
6. Install the OpenCode2 server and terminal plugin for normal CLI launches:
   ```sh
   .venv/bin/python -m harness.impl.opencode2.install
   ```
   This links the package into the native plugin discovery directory. See the
   [OpenCode2 setup](harness/impl/opencode2/README.md) for instance settings.

## Usage

Everything activates automatically per session — the mirror opens on
`SessionStart`, the tab colors follow the hooks. Manual controls:

```sh
# Mirror pane
.venv/bin/python client/terminal_keys.py toggle|grow|shrink|reset|setpct <N>

# Extension views: choose one for this window and open it in a pane (kitty overlay)
# map ctrl+shift+e launch --type=overlay .venv/bin/python client/terminal_extension_selector.py 127.0.0.1 8377

# Smoke-test the tab colors (~3s each)
for s in idle thinking working executing awaiting-bg awaiting-command awaiting-response; do
  ./bin/claude-tab-status.py "$s"; ping -c 4 127.0.0.1 >/dev/null
done
./bin/claude-tab-status.py clear

# Raw-event audit CLI — exact source bytes and their interpretations
bin/baqylau-raw-events-audit session <sid>
bin/baqylau-raw-events-audit raw <raw_event_id>
```

## Architecture

Producer/renderer split over SQLite: ~20 short-lived hook processes plus
detached tailers append width-independent *paint ops* to a per-session state
DB; a single renderer inside the pane paints them at the live width and
reflows on resize. The code is layered so agent tools and terminals are both
pluggable:

```
core/        the floor: what knows the OS, not the domain — env, processes,
             git, where the two databases live, and the daemon client
             daemon's door, both sides)
domain/      the words, stdlib-only: the closed canonical event vocabulary,
             and the application's own value types (preferences, workspace)
repository/  the ONLY thing that opens a database. contract/ (21 Protocols,
             each method one whole transaction), model/ (a row DTO per table),
             mapper/ (row <-> model object, pure), impl/sqlite/
audit/       what the MACHINERY did — the record types, the free-function
             facade every process calls, and browser-reported telemetry
engine/      the neutral middle: engine/interpret/ (the one loop that pulls and
             translates), engine/react/ (the one that reacts and writes),
             engine/sessiondata/ (a writer per aggregate, folded once on arrival)
terminal/    the terminal concern, whole: contract + models, the panes it
             paints, and terminal/impl/<name>/ — one directory per terminal
             (kitty today), the only place a terminal's name appears
harness/     the harness concern, whole: contract + models, the hook channel,
             the services over one plugin, and harness/impl/<name>/ — one
             directory per agent tool (claude_code · codex), the only place a
             harness's name appears
dashboard/   the web surface: frontend/ (Svelte 5, strict TypeScript, Vite),
             static/ (the FastAPI-owned shell and PWA assets), services/ (one
             question, one answer), and its own config, paths, and CLI
notify/      alerts about sessions: when one is owed you (notifier), whether
             you need telling (presence), and where it reaches you (channels/)
api/         the daemon's HTTP layer — one FastAPI app, every request AND
             every response typed, every service it uses named in the handler's
             own signature
app/         the composition root: providers.py declares every node of the
             application (one provider each, wired by its signature and
             resolved by FastAPI), injection.py is the kernel that scopes them
             one-per-application, plus the services that compose concerns the
             engine keeps apart
bin/         the repository's own CLIs (audit, dashboard). Every entry an
             EXTERNAL config names lives with its concern instead —
             harness/impl/<name>/bin/ and terminal/bin/ — so a captured
             path never crosses a concern boundary
```

## Harness discovery

Each harness has one directory under `harness/impl/`. Discovery finds directories
with `plugin.py` in name order. Each directory must also contain `definition.py`.
That file exports `DEFINITION`, a `HarnessDefinition` with the directory name and
a function that returns its default `HarnessRuntimeConfig`.

Keep the declaration small. It must not import or build plugin services.
`plugin.py` exports `build_plugin`, which receives the runtime configuration and
shared dependencies. Its returned plugin must use the declared name.

There is no central harness list. Runtime defaults, CLI name checks, and the E2E
coverage checks use the discovered declarations. Discovery runs at startup; it
does not watch directories or use a timer. Restart after you add a plugin.

## Testing

```sh
make test        # frontend unit/browser gates + hermetic Python suite
make test-frontend # formatting, strict checks, and Vitest coverage
make test-browser  # isolated production daemon; Chromium and WebKit
make e2e         # live harness, live browser, and static Playwright E2E
make test-seq    # Python suite, sequential (debugging / no xdist)
make test-all    # + opt-in real-kitty smoke tests
```
