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
`agentblocks.py` (the CHILD-AGENT stream presenter, one tier above streamfmt:
`AgentStream` builds every block a host's child paints — the `⇢ prompt` launch
card, `⇠ result`, `✎ message`, `⋯ reasoning`, mail in both directions, a
generic `· <tool>` call, a `▶/▷` shell block, a file one-liner, a compaction
notice, the run footer — and owns the STAMP POLICY on them: which carry `web`
(surface in the LEAD's mirror), their `note` wording, `bubbled`, and the
`who`/`tags` fields. The shapes were already shared; the policy was not, and a
codex-native subagent's copy of the rules was silently missing its launch AND
result cards. Pure — the builders return op lists, never emit, take the copy
group from the caller and text already capped (the CAP_* tables stay per-host)
— so a host adapter keeps its own machinery (tee hand-offs, pend ledgers,
scoreboard bumps, click-to-view stashes) wrapped around these calls; its three
REGISTERS (`REG_AGENT`/`REG_TEAM`/`REG_CODEX`) select only which word the web
calls the child. It is ALSO the one owner of the register TABLE the read side
keys on — `REGISTERS`, mapping each register to its `src` stamp prefix, its web
activity-class token, and whether `as_lead` recolours its headers — read through
`src_prefixes()`/`src_acts()`/`src_stamp()`/`lead_src_prefixes()` by
`read/mirror.agent_scope` and `opshtml/actclass`, which each used to spell the
same closed list out; a fourth host adds a row instead of an edit in three
packages, and the failure mode of missing one is silent (an unmatched prefix
renders that agent's mirror BLANK)),
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
open/close the mirror pane + scoreboard bar, create/restore/park the state DB,
and `host_end(fe, sid, log, reason, win=)` — the ONE session-END owner both hosts
route through: session-end audit → close panes → park the state DB → optionally
clear the tab, in Claude Code's historical SessionEnd order; shared by BOTH hosts,
Claude Code's `split.py` and standalone codex's `session.py`. Frontend-INJECTED:
core imports no frontend, so every terminal-touching function takes the caller's
`fe` as its first arg), `copy.py` (the ⧉
copy-link handler behind the `claude-copy.py` entry — reads a block's
group-tagged ops read-only and pipes command/output text to the clipboard; see
[click-to-view.md](click-to-view.md)), `tabs.py`
(the tab-state vocabulary: state constants, the `COLORS` hex table every
frontend paints from, and the global window-keyed tab DB + watcher pid locks),
`tabpaint.py` (the tool-AGNOSTIC tab PAINT engine — `paint(fe, win, state,
reason, …)`: the dedup against the persisted tab row, the frontend
`set_tab_color`/`clear_tab_color` call, the persist-only-on-`rc==0` rule, and the
`tab_transitions` audit on every path; frontend-INJECTED like `hostpane.py`, so a
tab producer contributes only its `{event → (state, reason)}` decision + a window
resolver and reuses the engine — `plugins/claude_code/tabstatus.py` is the
reference producer, standalone codex the second),
and `sessionapi.py` (the READ-SIDE session-data API — the one door for
consumers: presentation-channel delegations to `core.state` (the mirror/
scorebar's whole diet — same function objects, zero behavior change) plus a
read model over the state DB (live + parked), the audit
`sessions`/`streams`/`otel`/`errors` tables (fork-aware via `sid_chain()`),
the tab DB, and — plugin-side, through the registry's read fan-outs — the
transcripts, a session's `account`/`usage`/`costs` among them, since each of
those is one HOST's vocabulary (subscription slugs, rate-limit windows, a
telemetry taxonomy) and core answering them meant answering for every host with
one host's shapes; see [sessionapi.md](sessionapi.md)).

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
from the status-line stdin, behind `bin/claude-statusline.py`), `usage.py` (the
LIMITS / ACCOUNTS / COSTS read model over what statusline/relimit stashed and
what the OTLP receiver banked: Anthropic's window LENGTHS + the rolled-over,
effective-5h, perishability, limit-still-active and logged-out-still-active
arithmetic, the per-account strip rows, and the OTEL `query_source` cost query —
all of it MOVED here from `core/sessionapi.py`, which is tool-agnostic and was
spelling one vendor's window lengths and one CLI's status-line timing as core
constants; behind the `plugins.usage_strip`/`session_usage`/`session_account`/
`session_costs` fan-outs, and the numbers the rate-limit migration picker runs
on), `host.py` (the
`plugins.host.HostControl` adapter — Claude Code drives every control gesture,
so its derived caps read all-True; behind the `host` provider), the seven hook-handler bodies (`cmd_pre`, `cmd_fmt`,
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
manager, streaming just this codex session's own rollout AND every subagent it
spawns — `spawn(subagent=True)`, which stamps `sub:<aid>` and hands out a SUB
palette colour, because a native subagent is a CHILD AGENT rather than a codex
run), `stream.py` (one tailer per codex run — the paint half, in one of three
REGISTERS: standalone / sidecar / subagent, the last driving the shared
`core/agentblocks.py` so it paints the same cards a Claude subagent does, see
[codex.md](codex.md) › *Three registers*), `rollout.py`
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
**The registry also OWNS the default host's name.** `plugins.default_host()` is
the one answer to "which tool does a session behave as when its owner can't be
proven, and which does a launch that names none pick" — derived from the registry
(`all_plugins()` is host-first; the first LAUNCHABLE `host` adapter is the
default), not authored. It replaced four independent spellings of the literal in
the dashboard tier plus a fifth in its notifier; `tests/test_l1i_host_contract.py`
greps that tier for host-name literals against a shrinking allowlist so they
cannot come back.

**The SID-KEYED fan-outs are ownership-ROUTED** (`_first_owner`), which is the
same argument one key over: resolve the session's transcript path, and ask ONLY
the host that owns it (the default host when the path is empty or unclaimed — the
same fail-OPEN rule `session_caps` applies, so a daemon-origin session with no
transcript keeps working). A session belongs to exactly ONE host, so
first-plugin-wins here is not merely imprecise, it is a claim by the wrong host
about someone else's session: `ask_preamble` answers `""` for ANY sid, and a
non-None result ENDS the fan-out before the owner is reached. This covers the
limits/account/cost facets, the three SESSION-STATE facets
(`tasks`/`compacting`/`fg_running` — what a session is doing right now, which the
dashboard used to read as raw kv rows by name), `conversation` for a session's own
thread, `ask_preamble` and `pending_dialog`.

**But the KEY decides, and an AGENT-keyed read may cross hosts.**
`conversation(sid, pos, agent_id)` and `agent_usage(sid, agent_id)` stay
first-wins when an `agent_id` is given, because a child need not share its
parent's host — a codex run sidecar'd inside a Claude session is a codex agent
under a claude_code sid, and routing by the SESSION's owner asks Claude about a
codex rollout, which declines, losing the agent entirely. First-wins is safe
there for the same reason it is unsafe above: the agent id is itself the
discriminator, since each host recognizes only ids it issued. A caller that has
already resolved the owner (the SSE tick, once per pass) may pass it as a `host=`
hint, so an ownership-routed read on a fast cadence costs no extra row lookup.

**Core's read model reaches the registry, in two places.** `sessionapi.agents()`
splices `plugins.runs(sid)` (a host's own NESTED runs — codex answers with its
sidecar rollouts, claude_code declines because a subagent is already a `streams`
row) and `sessionapi.nested_owners()` memoizes `plugins.nested_owners(sid)` (who
launched a background job or monitor, recovered from a HOST's launch-hook payload
shapes — claude_code answers, codex declines). Both were host-shaped code INSIDE
core: a `codex_runs()` named after one tool, and Claude Code's PostToolUse JSON
paths embedded in core SQL. The imports are lazy, and this is the ONE core module
allowed to call the registry root (docs/styleguide.md *Layering*).

**The provider surface is DECLARED.** `plugins.PROVIDERS` lists the optional
functions a plugin may expose and the arity each fan-out calls it with,
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
implements only what it has something to say about — and WHICH plugin answers
which name is declared too, in `tests/test_l1i_host_contract.py`'s coverage
MATRIX: one cell per (provider, host), each either implemented or DECLINED with
the plugin's own written reason. A running count in prose used to stand in for
that and was already stale.

**The HOST-tool CONTROL interface.** Reads have `PROVIDERS`; writes have
`plugins/host.py`. A host tool's CONTROL plane — the whole gestures the
dashboard drives (interrupt, send, rename, rewind, migrate, compact, model,
effort, ask, plan) — lives behind ONE class, `plugins.host.HostControl`, the
write-side twin of the `Frontend` base: one class, inert defaults, a contract
test. The gestures are WHOLE gestures, not keystroke atoms, so a future
app-server-backed host (codex's app server, an SDK transport) implements them
without pretending to press keys. The load-bearing rule is that a host's
CAPABILITIES are DERIVED from which gestures its subclass OVERRODE
(`caps()` compares each `GESTURES` method against `HostControl`'s own) — never
an authored `{name: bool}` a new gesture can silently drift out of sync with.
A plugin exposes its adapter through the `host` provider, and the registry root
resolves it: `plugins.hosts()` enumerates the tools AND each one's whole
new-session vocabulary (the picker, both option menus, their defaults, the
account/mention flags, the rewind modes and the quick commands — see below),
`plugins.host_named(name)` / `host_of(path)` / `host_for(sid)`
hand back the adapter, and `plugins.host_caps(name)` its derived caps. The
dashboard serves those caps per session (`read/session.session_caps`) and GATES
every control button on them, client-side (`capOk` greys it) and server-side
(`http/base._caps_guard` 409s it) — so a session owned by a tool that leaves a
gesture inert degrades cleanly (the button greys, the POST refuses) rather than
firing a command the tool ignores. claude_code drives every gesture, so its caps
are all-True and the gate is a no-op for a Claude session.

**Every host's POST handler dispatches through those gestures** (as of P2). A
control handler is guards + `host.<gesture>(...)` + the HTTP mapping; there is no
inline body for any host left in the dashboard tier. Three things came with that:

- Four **cap SHARERS** — real gestures that ride another's cap because a cap must
  map to exactly one method or the derivation stops being the truth: `autoname`
  (under `rename`), `rewind_to` (under `rewind`), `plan_options` (under `plan`),
  `deliver` (under `ask`). A host may implement one half and not the other, and
  the inert base's `unsupported` RESULT is what turns that into a 409 naming the
  capability — distinct from a `_rejected()` result, which means "tried and
  failed" and is a 502.
- Per-host **request VOCABULARIES** the dashboard validates against instead of
  spelling: `ask_declines()` / `plan_decisions()` / `rewind_modes()` /
  `mention(path)` / `title_key(tpath)` / `paste_grabs_clipboard_image` /
  `clear_input` / `turn_live` — plus the read-only screen probes `input_box` /
  `ask_region` / `typed_input`, whose inert defaults are what make "no probe for
  a tool we cannot read" the default rather than a host-name check in the read
  model.
- The same class carries the **words the PAGE puts on screen** (as of P5), so the
  client stops carrying a table per host: `model_choices` / `effort_choices` and
  their `model_default` / `effort_default`, `model_match` (how a menu row matches
  a running model id — `family` for Claude Code's alias rows, `exact` for codex's
  full ids; a rule DECLARED because sniffing the id mis-reads any host that
  didn't coin it), `rewind_mode_label(mode)` beside `rewind_modes()`, and
  `command_floor(cmd)` — the measured refusal floor of each `QUICK_COMMANDS` wire
  word. One builder, `plugins.host_vocabulary(host)`, serves the whole set on
  `/api/hosts` (per tool, for the new-session form) and on the session payload
  (per owner, for the header bar); WHICH quick commands a host offers is derived
  from its overrides exactly as caps are. What this deleted from
  `dashboard/static/` is the last place a host was known by NAME: four
  host-name-keyed option tables whose fallback handed an unknown tool Claude
  Code's models and defaults, and a `shortModel` that branched two hosts' model-id
  grammars inline (`tests/test_l1i_host_contract.py` ratchets both tiers'
  remaining literals).
- Each host's **screen drivers moved into its own package** (see below), which is
  what made the routing possible at all.

**A host's SCREEN DRIVERS live in its plugin.** `plugins/<tool>/` holds
everything that KNOWS one agent tool — including the code that drives its TUI.
Claude Code's five drivers (`askdialog.py`, `plandialog.py`, `rewindmenu.py`,
`confirmdialog.py`, `suggestion.py`) sit in `plugins/claude_code/` beside the
codex drivers that already worked this way (`plugins/codex/dialog.py` &
siblings), because the dashboard reaches a host ONLY through `HostControl`: the
whole gesture, screen driver included, sits behind `host.<gesture>` so the
consumer tier never imports a driver. Two pieces they share are tool-agnostic and
live in `core/`: `core/screendrive.py` (the screen re-read poll loop +
`StepError`, frontend-INJECTED like `core/hostpane.py` and `core/tabpaint.py`)
and `core/clipimg.py` (the macOS clipboard IMAGE probe/wipe — the MECHANISM is
OS-level, the POLICY is the host's `paste_grabs_clipboard_image` declaration).
The dependency rule is what forced both moves and is enforced BOTH ways: no
plugin module may import `dashboard/` (`tests/test_l1i_host_contract.py` walks
every plugin's imports), and the dashboard may reach into `plugins/<tool>/` only
through the declared `DASHBOARD_PLUGIN_REACHES` list.

**Adding support for another agent tool** = a new `plugins/<tool>/` directory
implementing whichever hooks it needs (`on_session_start` for a secondary
source; its own entry scripts + hook wiring for a hook-driven host — Claude
Code and now codex are both hosts, both driving the shared `core/hostpane.py`
lifecycle) + one line in `all_plugins()` — core and the frontends don't change.
A provider it wants that no fan-out has yet is a `PROVIDERS` row plus the
fan-out; a provider it simply doesn't implement stays absent, as before. To be
a controllable HOST in the dashboard it also ships a `host` provider returning a
`plugins.host.HostControl` subclass, overriding exactly the gestures it can
drive — the caps it does NOT declare grey the corresponding dashboard buttons
automatically, which is what makes adding copilot/opencode a new package rather
than an edit to the control plane.

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
