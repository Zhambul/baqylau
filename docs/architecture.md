# Architecture (core / plugins / frontends)

The codebase is layered so that agent tools (Claude Code, codex, future
similar tools) and terminals (kitty, future iTerm2/ghostty) are both
pluggable. The layers and their one dependency rule:

```
core/        tool- and terminal-agnostic runtime — imports nothing outside core/
frontends/   terminal adapters — import core/ at most
plugins/     one directory per agent tool — import core/ + frontends/,
             never each other
claude-*.py  repo-root entry scripts: the assembly layer. They may import
             anything. Their FILENAMES are load-bearing: they name the audit
             DB's handler/script vocabulary (`hook_events.handler`,
             `errors.script`, spawn parents) — so entries stay at the root
             under their historical names even as implementations move into
             the packages. Since the single-dispatcher refactor ([wiring.md](wiring.md)) the
             HOOK wiring in ~/.claude/settings.json points every event at ONE
             entry, `claude-hook.py`, which runs each subsystem in-process — so
             argv[0] is `claude-hook.py` for all of them. The vocabulary is
             preserved by the dispatcher stamping `audit.set_handler(name)`
             around each call (an explicit override, no longer argv[0] alone).
             The per-script shims (`claude-cmd-fmt.py` …) still exist and still
             run standalone — the e2e tests drive them directly.
```

`core/` holds: `paths.py` (the mirror-log path format — was
`claude_paths.py`), `state.py` (per-session runtime SQLite — was
`claude_state.py`; also `parked()` — THE session-alive probe: True once
SessionEnd parked the state DB file away, polled by every detached
tailer/watcher completion loop, a bare `os.path.exists` that can never create
the file it probes), `slots.py` (palette/liveness slots — was
`claude_slots.py`), `tail.py` (the tailer skeleton — was `claude_tail.py`),
`streamfmt.py` (the shared block-shaping vocabulary of the stream renderers —
`cap`, the `chip`/`gutter`/`dim_gut` op shapes, the ended-footer `tok_rollup`
token fragment, and `file_line` — the file-op one-liner
`verb(name)[ extent][ +A -R][ range]` painted identically by `file_fmt.py`,
`substream_render.py`, and the codex patch renderer, each of which used to
hand-build it; the per-caller extras — who-prefix, model/ctx tags, ✗ mark, the
click-to-view hyperlink — stay caller-side; extracted from the byte-identical
copies the renderers each grew — shared surface lives in core because the
dependency rule forbids codex importing claude_code),
`render.py` (ANSI rendering — was `claude_render.py`), `mdrender.py` (AST-driven
markdown → styled ANSI for the mirror: an `OpsRenderer(BaseRenderer)` over the
optional `wenmode` CommonMark parser + a block-buffering `MarkdownStreamer`;
supersedes `render.markdown()` and falls back to it when `wenmode` is absent),
`jsonrender.py` / `yamlrender.py` (its JSON/YAML siblings: `JsonStreamer` buffers a
`.json` stream whole and pretty-prints + colours it at completion — stdlib `json`;
`YamlStreamer` colours a `.yml` in place without reformatting — both optional
pygments, no background panel), `coderender.py` (a generic `CodeStreamer(lexer)`
that colours a source file — `.py`/`.java`/`.kt`/`.sh` — via the pygments lexer
named by its extension in `LANGS`; reuses `render.pick`), `audit.py` (the audit
trail — was `claude_audit.py`), `ops.py` (paint ops, `emit`, the scoreboard
counters/parts, the semantic colour table — the tool-agnostic half of the old
`claude_ops.py`), `hostpane.py` (the tool-AGNOSTIC host mirror lifecycle —
open/close the mirror pane + scoreboard bar, create/restore/park the state DB;
shared by BOTH hosts, Claude Code's `split.py` and standalone codex's
`session.py`. Frontend-INJECTED: core imports no frontend, so every terminal-
touching function takes the caller's `fe` as its first arg), `copy.py` (the ⧉
copy-link handler behind the `claude-copy.py` entry — reads a block's
group-tagged ops read-only and pipes command/output text to the clipboard; see
[click-to-view.md](click-to-view.md)), and `tabs.py`
(the tab-state vocabulary: state constants, the `COLORS` hex table every
frontend paints from, and the global window-keyed tab DB + watcher pid locks).

`plugins/claude_code/` is the HOST-tool adapter — everything that reads
Claude Code's own signals: `hookkit.py` (the hook-handler harness, was
`claude_hook.py`, plus `log_path`), `accounting.py` (Anthropic usage-dict
parsing, the `PRICES` table, `cost_usd`, the `usage_fold` message-id dedup,
`fold_usage`, `bump_transcript` — the pricing half of old `claude_ops.py`),
`tools.py` (Claude's built-in tool payload shapes: `parse_redirect`,
`diff_counts`, `read_extent`, `edit_range`, `FILE_LABEL`/`FILE_RGB`),
`model.py` (was `claude_model.py`, plus `claude_dirs`), `msgs.py` (was
`claude_msgs.py`), the seven hook-handler bodies (`cmd_pre`, `cmd_fmt`,
`file_fmt`, `subagent_fmt`, `monitor_fmt`, `task_fmt`, `stop_fmt`), the
single per-event **`dispatch.py`** (behind the `claude-hook.py` entry — reads
the payload once and fans out in-process to the tab dispatch, the right
formatter, and the audit subscriber; matcher routing lives in its `_plan()` —
see [wiring.md](wiring.md)), the two
streamers (`stream.py`, `substream.py` — the latter's block rendering lives
in `substream_render.py`: an import-safe `Renderer` class with per-tool-kind
dispatch tables, into which the lifecycle module injects its identity and
tailer hooks), the tab dispatch (`tabstatus.py` —
maps hook payloads and streamer callbacks onto the `core/tabs.py` states),
and the pane/session lifecycle (`split.py` — now a thin caller into
`core/hostpane.py`, which it shares with the codex host). Each repo-root `claude-*.py`
entry is now a ~8-line shim importing its plugin module and calling
`entry()`; `claude-mirror.py` and `claude-scorebar.py` keep their bodies at
the root (they are assembly-layer renderers, allowed to import both core and
plugins). `claude_ops.py` remains as a compat AGGREGATOR (a namespace copy
re-exporting all three homes — unlike the other shims there is no single
module to alias to).

`plugins/codex/` is a DUAL-role adapter — a secondary source inside a Claude
session AND a first-class HOST on its own: `launch.py` (the detach-fast
launcher), `watch.py` (the discovery watcher — in a Claude host it streams
every repo codex run; given a `HOST_PID` it becomes a standalone session
manager, streaming just this codex session's own rollout and owning teardown),
`stream.py` (one tailer per codex run), and `session.py` (the standalone-host
SessionStart handler — see [codex.md](codex.md) › *standalone*). The three
`claude-codex-*.py` entries plus `claude-codex-session.py` remain as shims.
`plugins/__init__.py` is the registry: `all_plugins()` (host first),
`on_session_start(log, cwd, sid)` (SessionStart fan-out — how codex attaches
its watcher to a Claude host; a plugin failure is audited and never blocks the
host's SessionStart), and `census(log)` (the scoreboard's ✉-row fan-out).
**Adding support for another agent tool** = a new `plugins/<tool>/` directory
implementing whichever hooks it needs (`on_session_start` for a secondary
source; its own entry scripts + hook wiring for a hook-driven host — Claude
Code and now codex are both hosts, both driving the shared `core/hostpane.py`
lifecycle) + one line in `all_plugins()` — core and the frontends don't change.

`frontends/` is the terminal layer. `frontends/base.py` defines the
`Frontend` interface, organised into role slices with each slice's consumers
documented inline — presence (`available`/`usable`/`current_window`/
`export_env`), tab colour (`set_tab_color`/`clear_tab_color`), window
enumeration (`ls`/`iter_windows`/`find_window`/`window_for_session`), pane
management (`launch_pane`/`close_pane`/`resize_pane`/`set_user_vars`/
`goto_splits_layout`), viewport scroll/read (`scroll_window`[`_fast`/`_end`]/
`get_text` — the mirror renderer's slice), and geometry (`split_geometry`) —
and doubles as the inert "none" frontend (every op a silent no-op with the
callers' expected failure value). That contract is pinned by
`tests/test_l0_frontends_contract.py`: the stub's every public method is
exercised for its inert default, kitty is checked to add no public API beyond
the interface (only the documented `listen`/`kitten` constructor attrs), and a
grep-style test keeps every module outside `frontends/` off kitty-only
internals (the tabstatus `FE.listen` leak class).
`frontends/kitty.py` is the kitty implementation (absorbing the old
`claude_kitty.py` helpers, `claude-split.py`'s socket resolution, and the
kitty-specific `neighbors`/`groups` geometry walk). `frontends.get()` selects
the active frontend — `$CLAUDE_FRONTEND` pins one, default kitty — so
supporting iTerm2/ghostty later means one new sibling module plus a detection
line, with `claude-tab-status.py` / `claude-split.py` / `claude-scorebar.py`
untouched (they already speak only the interface). Note ghostty has no
remote-control API comparable to kitty's — a ghostty frontend would keep
`available()` truthful and let the pane features degrade to no-ops while tab
colour (if/where possible) still works; the base class's no-op defaults are
designed for exactly that partial-capability case.

**Compat shims.** Every historical top-level module name still works:
`claude_state.py` and friends remain at the repo root as five-line shims that
replace themselves in `sys.modules` with the package module, so
`import claude_state` yields the *same module object* as
`from core import state` (shared `_CONNS`, shared globals — not a copy).
`claude_audit.py` additionally stays the documented CLI entry point
(`python3 claude_audit.py sessions|anomalies|…` and the
`claude_audit.py hook subscriber` write entry hooks invoke). Why shims rather
than a clean break: the hook table in `~/.claude/settings.json` lives outside
this repo, and the audit DB's historical vocabulary + the test suite's
subprocess imports all reference the old names — a rename would silently
orphan all three.
