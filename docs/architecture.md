# Architecture (core / plugins / frontends)

This file describes *what the pieces are and why*. The normative rules for
writing code that fits them — layering, naming, single-owner vocabularies,
import-time purity, audit coverage, test discipline — live in
[styleguide.md](styleguide.md); new code is expected to follow it.

The codebase is layered so that agent tools (Claude Code, codex, future
similar tools) and terminals (kitty, future iTerm2/ghostty) are both
pluggable. The layers and their one dependency rule:

```
core/        tool- and terminal-agnostic runtime — imports nothing outside core/
frontends/   terminal adapters — import core/ at most
plugins/     one directory per agent tool — import core/ + frontends/,
             never each other
dashboard/   the web dashboard, a CONSUMER package (docs/dashboard.md) —
             imports core/, the plugins registry root (its read fan-outs),
             AND frontends/ (for its control plane — the two write endpoints
             reach the terminal through frontends.get() the same way the bin/
             renderers do); nothing imports it back except its bin/ entry and
             the tests. The bin/ renderers already sit at this height;
             dashboard/ is that tier made importable so the server is testable
             in-process. Decomposed into sub-packages (docs/dashboard.md):
             config / read (the read model) / notify / control / http (the HTTP
             layer, Handler split into base+get+post+sse mixins) / opshtml (the
             web presenter); server.py is a thin re-export facade. Internal
             dependency direction: config <- read/control/notify <- http.
bin/         every executable ENTRY script (`bin/claude-*.py`): the assembly
             layer. They may import anything. Their FILENAMES are load-bearing:
             they name the audit DB's handler/script vocabulary
             (`hook_events.handler`, `errors.script`, spawn parents) — so
             entries keep their historical basenames even as implementations
             move into the packages, and spawn sites join `core/paths.py`'s
             `BIN` with the basename. Naming convention (deliberate): a
             hyphenated `claude-*.py` is an executable entry, un-importable by
             design; an underscored `*.py` inside a package is an importable
             module. The repo root holds no Python files. Since the single-dispatcher refactor ([wiring.md](wiring.md)) the
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
`claude_slots.py`), `locks.py` (pid-liveness locks —
`lock_acquire`/`lock_holder`/`lock_release` on the `claims` table of an
ARBITRARY caller-supplied DB path: the codex per-repo claims DB and watch
lock, the OTLP receiver's per-machine singleton; borrows state.py's
`_connect`/`immediate`/`pid_alive`, moved out of state.py because
arbitrary-path locks were never per-session state), `tail.py` (the tailer skeleton — was `claude_tail.py`),
`spawn.py` (THE detached audited process spawn — `spawn_detached`: Popen with
all stdio to DEVNULL plus the load-bearing `start_new_session=True` (a plain
child sits in the hook's process group, which Claude Code waits to drain —
this hung SessionStart once), and the `A.spawn`/`A.error` rows around it;
extracted from three byte-similar copies — `hookkit.spawn_streamer` (which
stays as the plugin-facing wrapper resolving a bin/ sibling NAME), the codex
launcher, and the codex watcher's per-run stream spawn),
`streamfmt.py` (the shared block-shaping vocabulary of the stream renderers —
`cap`, the `chip`/`gutter`/`dim_gut` op shapes, the ended-footer `tok_rollup`
token fragment, `file_line` — the file-op one-liner
`verb(name)[ extent][ +A -R][ range]` painted identically by `file_fmt.py`,
`substream_render.py`, and the codex patch renderer, each of which used to
hand-build it — and `file_display`, the location-aware name that goes inside
those parens (bare basename under the session cwd, `✎ name` for a session
scratchpad file, dim abbreviated dir + basename for anything else outside the
project); WHO produced a block and its model/ctx TAGS are the op's own fields
(core/ops.py) and `compose`/`strip_who` here are the one owner of how the
terminal paints them back and how history's baked-in copies are undone, while
the remaining per-caller extras — the ✗ mark, the click-to-view hyperlink —
stay caller-side; extracted from the byte-identical copies the renderers each
grew — shared surface lives in core because the dependency rule forbids codex
importing claude_code),
`render.py` (the ANSI rendering PRIMITIVES — was `claude_render.py`: width
math, palette/`pick`, strip/wrap/gutters, the security-critical `neutralize()`,
inline markdown; keeps thin `format_code`/`render` delegating aliases),
`codefmt.py` (the bash/python source tokenizer + pretty-printer split out of
render.py: heredoc/`python -c` segment splitting, command-word marking,
`format_code`, and the highlight-and-wrap `render()` for `code` ops; imports
render's primitives one-directionally), `panescript.py` (the
shared skeleton of the two pane-renderer ENTRY scripts, `claude-mirror.py` and
`claude-scorebar.py`: the `MIRROR_LOG [WIDTH]` argv contract (`parse_argv`),
the `width()` closure (`make_width`), the SIGWINCH flag-setter shape
(`install_winch` — the handler body is just the caller's zero-arg flag-setter;
what the flag drives stays per-script), the `fit` re-export, and
`run_renderer` — the `__main__` crash wrapper whose "main (renderer crashed)"
audit detail string both scripts must keep byte-identical), `mdrender.py` (AST-driven
markdown → styled ANSI for the mirror: an `OpsRenderer(BaseRenderer)` over the
optional `wenmode` CommonMark parser + a block-buffering `MarkdownStreamer`;
supersedes `render.markdown()` and falls back to it when `wenmode` is absent),
`jsonrender.py` / `yamlrender.py` (its JSON/YAML siblings: `JsonStreamer` buffers a
`.json` stream whole and pretty-prints + colours it at completion — stdlib `json`;
`YamlStreamer` colours a `.yml` in place without reformatting — both optional
pygments, no background panel), `coderender.py` (a generic `CodeStreamer(lexer)`
that colours a source file — `.py`/`.java`/`.kt`/`.sh`, the JS/TS family
(`.js`/`.mjs`/`.cjs`/`.jsx`/`.ts`/`.mts`/`.cts`/`.tsx`), and the web-markup pair
`.html`/`.htm` + `.css`/`.scss`/`.less` — via the pygments lexer
named by its extension in `LANGS`; reuses `render.pick`), `audit.py` (the audit
trail's WRITE path — the tables, the migrations, the `A.*` row writers, the
spool degradation; imported by every hook process on every event, which is why
its read/report half is `auditcli.py` — the `bin/claude-audit.py` subcommands,
the `ANOMALY_SECTIONS` catalogue and the row formatters, 638 lines that no hook
runs. `auditcli` imports `audit` through the public `A.connect()`, never the
reverse), `ops.py` (paint ops, `emit`, the scoreboard
counters/parts, the semantic colour table — the tool-agnostic half of the old
`claude_ops.py`), `hostpane.py` (the tool-AGNOSTIC host mirror lifecycle —
open/close the mirror pane + scoreboard bar, create/restore/park the state DB;
shared by BOTH hosts, Claude Code's `split.py` and standalone codex's
`session.py`. Frontend-INJECTED: core imports no frontend, so every terminal-
touching function takes the caller's `fe` as its first arg), `copy.py` (the ⧉
copy-link handler behind the `claude-copy.py` entry — reads a block's
group-tagged ops read-only and pipes command/output text to the clipboard; see
[click-to-view.md](click-to-view.md)), `tabs.py`
(the tab-state vocabulary: state constants, the `COLORS` hex table every
frontend paints from, and the global window-keyed tab DB + watcher pid locks),
and `sessionapi.py` (the READ-SIDE session-data API — the one door for
consumers: presentation-channel delegations to `core.state` (the mirror/
scorebar's whole diet — same function objects, zero behavior change) plus a
read model over the state DB (live + parked), the audit
`sessions`/`streams`/`otel`/`errors` tables (fork-aware via `sid_chain()`),
the tab DB, and — plugin-side, through the registry's read fan-outs — the
transcripts; see [sessionapi.md](sessionapi.md)).

`plugins/claude_code/` is the HOST-tool adapter — everything that reads
Claude Code's own signals: `hookkit.py` (the hook-handler harness, was
`claude_hook.py`, plus `log_path`), `accounting.py` (Anthropic usage-dict
parsing, the `PRICES` table, `cost_usd`, the `usage_fold` message-id dedup,
`fold_usage`, `bump_transcript` — the pricing half of old `claude_ops.py`),
`tools.py` (Claude's built-in tool payload shapes: `parse_redirect`,
`diff_counts`, `read_extent`, `edit_range`, `FILE_LABEL`/`FILE_RGB`),
`model.py` (was `claude_model.py`, plus `claude_dirs`), `msgs.py` (was
`claude_msgs.py`), `slashcmds.py` (slash-command discovery for the web
composer's "/" menu: the curated `BUILTINS` snapshot + the cwd's
`.claude/commands`/`.claude/skills` walk, behind the `plugins.slash_commands`
fan-out — see [dashboard.md](dashboard.md)), `account.py` (the
subscription-account vocabulary: the switcher's env contract + `accounts.tsv`
registry, behind `plugins.accounts`/`account_alias`), `statusline.py` (the
status-line shim's capture half — stashes per-session 5h/7d usage + account
from the status-line stdin, behind `bin/claude-statusline.py`), the seven hook-handler bodies (`cmd_pre`, `cmd_fmt`,
`file_fmt`, `subagent_fmt`, `monitor_fmt`, `task_fmt`, `stop_fmt`), the
single per-event **`dispatch.py`** (behind the `claude-hook.py` entry — reads
the payload once and fans out in-process to the tab dispatch, the right
formatter, and the audit subscriber; matcher routing lives in its `_plan()` —
see [wiring.md](wiring.md)), the two
streamers (`stream.py`, `substream.py` — the latter's block rendering lives
in `substream_render.py`: an import-safe `Renderer` class with per-tool-kind
dispatch tables, into which the lifecycle module injects its identity and
tailer hooks; its LINE PARSING lives in `transcript.py`, the parse half of
the split and now its ONLY presenter — the drill-down timeline that was the
second one is gone with agent scope, see [sessionapi.md](sessionapi.md)),
the tab dispatch (`tabstatus.py` —
maps hook payloads and streamer callbacks onto the `core/tabs.py` states),
and the pane/session lifecycle (`split.py` — now a thin caller into
`core/hostpane.py`, which it shares with the codex host). Each `bin/claude-*.py`
entry is a ~8-line shim importing its plugin module and calling
`entry()`; `bin/claude-mirror.py` and `bin/claude-scorebar.py` keep their
bodies in the entry script (they are assembly-layer renderers, allowed to
import both core and plugins).

`plugins/codex/` is a DUAL-role adapter — a secondary source inside a Claude
session AND a first-class HOST on its own: `launch.py` (the detach-fast
launcher), `watch.py` (the discovery watcher — in a Claude host it streams
every repo codex run; given a `HOST_PID` it becomes a standalone session
manager, streaming just this codex session's own rollout and owning teardown),
`stream.py` (one tailer per codex run — the paint half), `rollout.py`
(rollout-record parsing — the parse half of the codex parse/paint split, one
record-grammar owner for the mirror renderer, see
[sessionapi.md](sessionapi.md)), and
`session.py` (the standalone-host
SessionStart handler — see [codex.md](codex.md) › *standalone*). The three
`claude-codex-*.py` entries plus `claude-codex-session.py` are thin shims in `bin/`.
`plugins/__init__.py` is the registry: `all_plugins()` (host first),
`on_session_start(log, cwd, sid)` (SessionStart fan-out — how codex attaches
its watcher to a Claude host; a plugin failure is audited and never blocks the
host's SessionStart), `census(log)` (the scoreboard's ✉-row fan-out — returning `(parts, ops)`: the row's fragments plus ready-made mirror paint ops for this tick's team-mail transitions, so the tool-agnostic scorebar emits them without knowing the mail vocabulary), and the
read-side fan-outs (`session_title`/`context`/`prompts`/`conversation`/… —
first plugin that recognizes the key wins).
**The PATH-KEYED fan-outs are ownership-gated** (`_first_path`): a plugin that
declares the `owns(path)` provider is asked only about files it owns. First
plugin wins is first PARSER wins, and these parsers are bounded and fail open —
`prompt_count` returns its cap for any file over `PROMPT_SCAN_B` without reading
a byte, so `plugins.prompts()` measured 8 human prompts in a 429KB *codex
rollout*: the size of a file from another tool decided a Claude-shaped answer.
Ownership is opt-in per plugin (a plugin with no `owns` is asked exactly as
before, so nothing regressed while claude_code was the only one to declare it),
because what it buys is precisely the case a parser *cannot* tell — a bounded
read, a byte prefilter, a fast path over a size limit. claude_code answers it
from Claude Code's own on-disk SHAPE (the `projects/<hash>/<sid>.jsonl` layout
and its `subagents/` sidecars, else a bounded head read for a record only
Claude writes — `transcript.owns`), never from the whole file: ownership is
asked once per session per poll. `plugins.owns_by(path)`
names the owning tool for the one CONTROL-plane caller that needs it: the
dashboard refuses to relaunch `claude --resume <sid>` for a session claude_code
does not own (docs/dashboard.md *Resume & send*).
**The provider surface is DECLARED.** `plugins.PROVIDERS` lists the twenty-two
optional functions a plugin may expose and the arity each fan-out calls it with,
and every lookup goes through `plugins.provider(plugin, name)` rather than a
bare `getattr`. This is what `frontends/` has had all along in
`frontends/base.Frontend` plus its contract test, and plugins are the same
problem: a registry of optional duck-typed functions reached by name, where a
misspelled provider or a signature that drifted from its caller is **not an
error anywhere**. It is simply never found, and the feature degrades silently to
"no plugin answered" — the one failure mode a duck-typed registry cannot report
on its own. `provider()` raises `KeyError` on an undeclared name, and
`tests/test_l1_contracts.py` checks the table against reality in both
directions: every name a fan-out reaches for is declared (parsed out of the
fan-outs themselves, so a new one can't skip the table), every declared row is
actually called by one, every row is implemented by at least one plugin, and
every implementation accepts the arity its fan-out passes. A plugin still
implements only what it has something to say about — claude_code 21 of 22, codex
1, otel 1.

**Adding support for another agent tool** = a new `plugins/<tool>/` directory
implementing whichever hooks it needs (`on_session_start` for a secondary
source; its own entry scripts + hook wiring for a hook-driven host — Claude
Code and now codex are both hosts, both driving the shared `core/hostpane.py`
lifecycle) + one line in `all_plugins()` — core and the frontends don't change.
A provider it wants that no fan-out has yet is a `PROVIDERS` row plus the
fan-out; a provider it simply doesn't implement stays absent, as before.

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
kitty-specific `neighbors`/`groups` geometry walk). Latency-critical calls —
`get_text`, the freeze-bracket scrolls, and the hook-path tab paint
(`set_tab_color`/`clear_tab_color`) — go over a raw `@kitty-cmd` unix-socket
exchange (`_rc_raw`, ~0.1 ms) with the `kitten` subprocess (~20-100 ms) as the
no-socket fallback; the tab paint *requests* kitty's `{"ok": …}` response so
callers still get the real exit code (docs/tab-colors.md, *How it works*). `frontends.get()` selects
the active frontend — `$CLAUDE_FRONTEND` pins one, default kitty — so
supporting iTerm2/ghostty later means one new sibling module plus a detection
line, with `claude-tab-status.py` / `claude-split.py` / `claude-scorebar.py`
untouched (they already speak only the interface). Note ghostty has no
remote-control API comparable to kitty's — a ghostty frontend would keep
`available()` truthful and let the pane features degrade to no-ops while tab
colour (if/where possible) still works; the base class's no-op defaults are
designed for exactly that partial-capability case.

**Compat shims: gone.** The historical top-level module names
(`claude_state.py`, `claude_ops.py`, `claude_kitty.py`, …) existed as
sys.modules-redirect shims for out-of-repo muscle memory; nothing in the repo
or the test suite imports them anymore, so they were deleted — import the
package modules directly (`core.state`, `core.ops`, `frontends.kitty`, …).
The audit CLI moved with the entries: `python3 bin/claude-audit.py
sessions|anomalies|…` (formerly root `claude_audit.py`). The ENTRY filenames,
by contrast, are permanent: the audit DB's handler/script vocabulary and the
external wiring (`~/.claude/settings.json`, kitty's `open-actions.conf`,
`~/.codex/hooks.json`) reference them by name, so they moved to `bin/`
unrenamed.
