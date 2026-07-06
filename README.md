# kitty tab colors for Claude Code

Makes the **kitty tab color** reflect what Claude Code is doing, so you can tell
a session's state at a glance — even from another tab.

| Tab color | State | Fires on |
|-----------|-------|----------|
| ⬜ grey `#5c6370`    | **idle** — session ready, nothing running                  | `SessionStart` |
| 🟪 magenta `#c678dd` | **busy** — thinking / non-shell tool (Read/Edit/Write/MCP) / writing the reply (merged — no signal tells them apart) | `UserPromptSubmit`, `PreToolUse` (main-agent non-Bash), `PostToolUse` (main agent) |
| 🟦 blue `#61afef`    | **the main session is running / awaiting** — a foreground shell command (`executing`, kept blue for its **whole real duration** even past Ctrl+B — see below), or the main session **awaiting an agent** (a foreground subagent/teammate keeps the turn blocked → blue; a background one → `awaiting-bg`) or a background command / monitor (`awaiting-bg`) | `PreToolUse` Bash/Task/Agent · `Stop` w/ a bg job/monitor/agent running |
| 🟥 red `#e06c75`     | **awaiting-command** — Claude is asking *you* a question | `PreToolUse` `AskUserQuestion`/`ExitPlanMode` · `Notification` (permission/approval message) |
| 🟩 green `#98c379`   | **awaiting-response** — done, your turn                     | `Stop` w/ nothing running · `Notification` ("waiting for your input") |
| (theme default)      | cleared on exit                                            | `SessionEnd` |

The rhythm in a normal turn: **magenta** whenever Claude is busy — reasoning,
using a non-shell tool, or writing your reply → **blue** while something is running
(a foreground shell command, a **subagent** running — foreground or background, or
a background job/monitor Claude is awaiting) → **green** when it hands back to you.
**Red** is reserved for when Claude is asking *you* a question — an
`AskUserQuestion`/`ExitPlanMode` prompt, or a permission/approval notification.
thinking/working were merged into one magenta
"busy" colour because there's no hook signal to tell reasoning, non-shell tool
use, and reply-writing apart; only a *shell command* is separable (blue).

## How it works

`claude-tab-status.py <state>` calls kitty remote control:

```
kitten @ --to "$KITTY_LISTEN_ON" set-tab-color \
  --match window_id:$KITTY_WINDOW_ID active_bg=… inactive_bg=…
```

It targets the tab containing *this* Claude Code window via `$KITTY_WINDOW_ID`,
and talks to kitty over the **socket** in `$KITTY_LISTEN_ON` — not the TTY,
because Claude Code hooks run without a controlling terminal. Color is set for
both active and inactive tabs so background sessions stay visible. The script
no-ops silently when not inside kitty / when remote control is unavailable, and
always exits 0 so it can never block a hook.

Besides literal states, hooks pass these **dispatch modes**:

The tab tracks the **main session only**: any `PreToolUse`/`PostToolUse` carrying an
`agent_id` is a subagent's / teammate's *own* inner tool call and is **ignored**, so
it never flips the tab while the main session is thinking or has handed back to you.
The main session still goes blue while it *awaits* an agent (see below).

- **`pretool`** — reads the hook's stdin JSON. If it carries an `agent_id` (a
  subagent/teammate inner call) → **ignored** (no change). Otherwise by tool name:
  `AskUserQuestion`/`ExitPlanMode` → `awaiting-command` (**red** — Claude is asking
  you); `Bash`/`Task`/`Agent` → `executing` (blue); any other tool → `working`
  (magenta, merged with thinking).
- **`posttool`** (PostToolUse / PostToolUseFailure) — `agent_id` present →
  **ignored**; otherwise → `working` (magenta).
- **Awaiting an agent stays blue without the agent's events:** a **foreground**
  subagent/teammate keeps the main turn *blocked* after its `Task`/`Agent` pretool
  set blue, so blue simply persists; a **background** one is caught by `stop` →
  `awaiting-bg` (a live `sub.pid` row).
- **`stop`** — `awaiting-response` (green) normally, but `awaiting-bg`
  (**blue** — the main session is awaiting that job, not you) if a background command /
  monitor / **agent** this session launched is still running.
- **`agent-start`** (fired by `claude-subagent-fmt.py` on `SubagentStart`) — a
  background teammate began a task, so the main session is awaiting it →
  `awaiting-bg` (blue), even if the lead's turn had already ended green (a
  teammate starting *between* the lead's turns would otherwise leave a stale
  green while the team works). **Exception: red wins.** If the tab is
  `awaiting-command`, Claude is blocked on *your* answer (permission prompt /
  AskUserQuestion) and the teammate's start must not erase that one visual cue —
  the dispatch bails (audited), same as `notify`'s red-wins rule.
- **`notify`** — reads the Notification message: a permission/approval prompt →
  `awaiting-command` (red — Claude is asking you); anything else → green.

### Detecting a running background command / agent / live foreground command (`stop`)

There is no Claude Code hook for "background command/agent finished," so the
`stop` dispatch detects it directly — via the live tailer rows in the session's
**state DB** (`live` table, read directly via Python's `sqlite3`). Each tailer owns a
row holding its pid, deleted when it exits: kind `bg` / `monitor` for a background
command/monitor (its `claude-stream.py`), `fg` for a **live-streamed foreground
command** (`claude-cmd-pre.py` — see *Live foreground streaming* below), and
`sub.pid` (key = agent_id) for a background **agent** (its `claude-substream.py`).
So a row with a **live pid** means that job/command/agent is still running → the
tab stays **blue** (`awaiting-bg`/`executing`). (A foreground agent's `sub.pid`
row has already been deleted by `Stop` time — the turn blocked on it — so only
background agents linger.)

> Earlier this scanned `tasks/<id>.output` write-holders with `lsof`. That turned
> out to be unreliable: in current Claude Code, **foreground commands also hold a
> `tasks/<id>.output` file** while they run, so an async `bg-recheck` that happened
> to fire while a foreground command was running would mis-count it and refuse to
> clear the blue (a stuck-colour bug). Live rows are created only by tailers, so
> they can't be fooled the same way.

There is no "background finished" hook, so the tab can't be flipped back the
instant a job ends — but it no longer has to wait for the next exchange either:
- When `claude-stream.py` finishes a job it **releases its slot row first**,
  then calls `claude-tab-status.py bg-recheck`, which flips a **stale `awaiting-bg`
  OR `executing`** back to green — but only if the tab is *currently* in one of
  those states (so it never overrides a working/idle/awaiting-command colour) and
  no other tailer row is still live. (Releasing before the recheck is essential,
  or it would see its own row.) Recognizing `executing` here (not just
  `awaiting-bg`) is what makes a **manually cancelled** foreground command flip the
  tab green promptly — cancelling fires no hook at all, but the `fg` tailer notices
  its process died (`has_writer` goes false) and calls `bg-recheck` itself.
- As a backstop for an *untracked* finished job (a tailer that died without
  rechecking), the `stop` dispatch — when it goes blue — also spawns **one detached
  `bg-watch` watcher** that polls until no live row remains, then flips the
  stale blue green (and exits immediately if a new turn starts). One watcher per
  window, guarded by a pid row in the tab DB.

Each **applied** color-set persists the state to the **global tab DB**
(`/tmp/claude-kitty-tab.db`, `tab` table keyed by window id — was a
`/tmp/claude-tab-state-<window_id>` file) so `bg-recheck`/`bg-watch` can make the
"is it currently red?" decision. Applied only: persisting a *failed* `kitten @`
paint made the DB claim a colour the tab never showed, and the "colour already
shown" dedup then suppressed every retry of that state — one transient socket
error stranded the old colour until a different state came along. On `rc != 0`
the row is left unchanged (audited as `applied=0 … state row unchanged`), so the
next same-state event retries the paint; the per-window `bg-watch`/`interrupt-watch` pid
locks live in its `watchers` table. Window-keyed state can't live in the
per-session state DB (a window outlives any one session), and /tmp keeps the old
self-clearing-on-reboot lifecycle.

### Recovering from a cancelled turn (`interrupt-watch`)

Claude Code fires **no hook at all** when a turn is cancelled/interrupted — no
`Stop`, no `StopFailure`, nothing. Every cancellation case in this doc ultimately
traces back to that one gap; what differs is how fast each case can be *noticed*:

- **Bash / background / foreground / subagent** cancellations each have a live
  process or file to poll (a tailer's writer-liveness, a subagent's `meta.json`
  `stoppedByUser`), so they self-heal in about a second — see *Live foreground
  streaming* and the subagent section above.
- **Everything else** — cancelling a plain text reply, a non-Bash tool call
  (Read/Edit/Write/MCP), a permission prompt, or the reply written *after* a
  command already finished — has no such process to poll, but Claude Code *does*
  append a synthetic `[Request interrupted by user]` line to the session
  transcript the instant it happens (confirmed empirically, mirroring the
  subagent case). `claude-tab-status.py`'s `thinking` dispatch (`UserPromptSubmit`)
  reads the payload's `transcript_path` and spawns **one detached
  `interrupt-watch` per window** that tails it for that line, polling every 0.5s —
  so this case recovers almost instantly.
  It watches for the **whole turn**, exiting only on green/idle/cleared. (It
  originally exited the moment the state left magenta — but the first Bash/Task
  pretool sets `executing`, so the watcher died at the turn's first tool call and
  a cancel *later* in the same turn, e.g. Esc during the long reply after a
  command finished, had no recovery at all: stuck magenta.) On seeing the
  interrupt line it re-checks the state: green/idle means the turn already
  resolved (do nothing); blue means a live command/agent whose own
  writer-liveness recovery is faster and authoritative (defer, or it would race
  `bg-recheck` and could paint "done" over a still-live bg job); magenta or red
  has no other signal, so it flips green.
- **Cancelling before the model has produced anything at all** (mid-thinking,
  before the turn's first hook) is the one case with **no signal whatsoever** —
  confirmed empirically: the harness silently rewinds the turn for editing, and
  *nothing* is written anywhere (no transcript line, no sidecar file). This case
  is **deliberately left unhandled**: the tab stays magenta until the next
  interaction resets it. A timeout backstop (`idle-watch`, "fully quiet for
  `CLAUDE_TAB_IDLE_SECS` → green") existed for it and was **removed** — long
  thinking fires zero hooks and writes nothing, which is *exactly* the same
  signature as the cancel, so any timeout short enough to be useful (30s)
  false-positived on every long thinking stretch, turning the tab green
  mid-turn. That false "your turn" fired on *every* long think and actively
  misled; the stale magenta it protected against is rare, happens with the user
  at the keyboard (they just pressed Esc), and self-corrects at the next prompt —
  which the cancelling user is typically about to type anyway.

## Architecture (core / plugins / frontends)

The codebase is layered so that agent tools (Claude Code, codex, future
similar tools) and terminals (kitty, future iTerm2/ghostty) are both
pluggable. The layers and their one dependency rule:

```
core/        tool- and terminal-agnostic runtime — imports nothing outside core/
frontends/   terminal adapters — import core/ at most            (in progress)
plugins/     one directory per agent tool — import core/ + frontends/,
             never each other                                    (in progress)
claude-*.py  repo-root entry scripts: the assembly layer. They may import
             anything. Their FILENAMES are load-bearing twice over: the hook
             wiring in ~/.claude/settings.json points at them, and argv[0] is
             the audit DB's handler/script vocabulary (`hook_events.handler`,
             `errors.script`, spawn parents) — so entries stay at the root
             under their historical names even as implementations move into
             the packages.
```

`core/` currently holds: `paths.py` (the mirror-log path format — was
`claude_paths.py`), `state.py` (per-session runtime SQLite — was
`claude_state.py`), `slots.py` (palette/liveness slots — was
`claude_slots.py`), `tail.py` (the tailer skeleton — was `claude_tail.py`),
`render.py` (ANSI rendering — was `claude_render.py`), and `audit.py` (the
audit trail — was `claude_audit.py`).

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

## Wiring

- **`~/.config/kitty/kitty.conf`** (appended at the end):
  ```
  allow_remote_control yes
  listen_on unix:/tmp/kitty
  ```
- **`~/.claude/settings.json`** — a `hooks` block:

  | Hook | Matcher | Runs |
  |------|---------|------|
  | `SessionStart`     | —      | `claude-tab-status.py idle` + `claude-split.py open` |
  | `UserPromptSubmit` | —      | `claude-tab-status.py thinking` |
  | `PreToolUse`       | `.*`   | `claude-tab-status.py pretool` |
  | `PreToolUse`       | `Task\|Agent` | `claude-subagent-fmt.py push` (stashes the Task description for the upcoming `SubagentStart` header) |
  | `PreToolUse`       | `Bash` | `claude-cmd-pre.py` (rewrites the command to stream live — see *Live foreground streaming* below) |
  | `PostToolUse`      | `.*`   | `claude-tab-status.py posttool` (ignored if the event carries an `agent_id` — a subagent/teammate inner call — else magenta) |
  | `PostToolUse`      | `Bash` | `claude-cmd-fmt.py` (writes command + output + elapsed to the mirror log) |
  | `PostToolUse`      | `Read\|Edit\|Write\|MultiEdit\|NotebookEdit` | `claude-file-fmt.py` (writes a one-line `Read(name)`/`Update(name)`/`Write(name)` to the mirror log) |
  | `PostToolUse`      | `Monitor` | `claude-monitor-fmt.py` (monitor header + spawns `claude-stream.py` to tail the event stream) |
  | `PostToolUseFailure` | `.*` / `Bash` / `Read\|Edit\|…` / `Monitor` | same handlers as `PostToolUse` — a tool that **fails** (e.g. a non-zero-exit command) fires this event, *not* `PostToolUse`, so it must be wired too or failures never reach the mirror |
  | `SubagentStart`    | —      | `claude-subagent-fmt.py start` (subagent header `▶ <type> · <desc>` + claims its colour slot; in-process **agent-team teammates** arrive here too) |
  | `SubagentStop`     | —      | `claude-subagent-fmt.py stop` (subagent footer `■ <type> ended · Ns` + releases the slot) |
  | `TaskCreated`      | —      | `claude-task-fmt.py` (agent-team shared task list: writes `✚ task #N · <subject>` to the mirror) |
  | `TaskCompleted`    | —      | `claude-task-fmt.py` (writes `✓ task #N · <subject>` to the mirror) |
  | `Notification`     | —      | `claude-tab-status.py notify` (reads the message: a permission/approval prompt → red `awaiting-command`; a "waiting for your input" notice → green `awaiting-response`, since that's just your turn) |
  | `Stop`             | —      | `claude-tab-status.py stop` **+ `claude-stop-fmt.py`** (folds the turn's token/cost spend into the scoreboard — see below) |
  | `StopFailure`      | —      | `claude-tab-status.py stop` (turn ended on an API error — keep the tab from getting stuck on the "busy" colour) **+ `claude-stop-fmt.py`** (fold whatever landed in the transcript) |
  | `SessionEnd`       | —      | `claude-tab-status.py clear` + `claude-split.py close` |

  All seven `*-fmt.py`/`-pre.py` handlers (incl. `claude-stop-fmt.py`) share **`claude_hook.py`** — the harness
  owning the identical per-hook skeleton (stdin payload parse + mirror-log
  derivation, audited ignore-decisions, detached streamer spawn with the
  load-bearing `start_new_session=True`, and the top-level audit-then-swallow).
  The `agent_id` main-session guard is deliberately NOT in the harness: most
  handlers skip agent-inner events, but `claude-monitor-fmt.py` renders subagent
  monitors on purpose, so each handler makes that call explicitly.

  Agent-team support also needs the experimental feature itself enabled, via an
  `env` entry in the same `settings.json` (read at session start):
  ```json
  "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" }
  ```

### Interpreter: skip the pyenv shim (`retarget-python.py`)

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
prefix on the `claude_audit.py hook subscriber` commands in `settings.json`.
**`retarget-python.py`** rewrites both to an absolute concrete-interpreter path
(it takes `sys.executable`, which under the shim already resolves to pyenv's
*active* version, so it honours `pyenv version`):

```sh
./retarget-python.py            # bake in the concrete interpreter (run once at setup)
./retarget-python.py --revert   # restore portable `#!/usr/bin/env python3`
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
  ./claude-tab-status.py "$s"; ping -c 4 127.0.0.1 >/dev/null   # ~3s each
done
./claude-tab-status.py clear
```

## Command mirror pane (vertical split)

A single vertical split — the right **25%** of the tab (configurable via
`CLAUDE_MIRROR_BIAS`; resizable / toggleable on the fly, see below) — shows each
command Claude runs as a block:

```
 ▶ foreground          (blue chip — see kinds below)
slug="$(pwd -P …)"     ← bash, syntax-highlighted,
for f in *.output; do    real line breaks preserved,
  …                       word-wrapped to pane width
done
─────────────────────  (rule, pane-width)
output
─────────────────────
 ■ finished · 1.2s     (magenta chip)
```

Each block is **bracketed by dividers** (a full-width rule before the header and
after the finish chip) and opens with a **coloured chip naming its kind**, so
foreground, background, and monitor are unmistakable (the generic `command`
label is gone):

The header chip, the output gutter, and the finish chip of a block **all share
one colour**, so you can match every part of a stream — and tell parallel streams
apart at a glance:

| Chip | Kind | Block colour |
|------|------|--------------|
| **▶ foreground** | a normal blocking command — full output, `■ finished` | by status: slate ok / red failed / orange interrupted |
| **▷ background** | a `run_in_background` command — output streams live, `■ background finished` | the job's slot from a **5-colour palette** |
| **◉ monitor** | a Monitor tool stream — events stream live, `■ monitor ended` | the monitor's slot from a **separate 5-colour palette** |
| **▶ \<agent-type\>** | a **subagent** (Task/Agent tool) — its prompt, messages, commands, file ops & result stream in, framed by `▶ <type> · <description>` … `■ <type> ended` | the subagent's slot from a **third 5-colour palette** |
| **▶ \<name\> · teammate** | an **agent-team teammate** — same streaming as a subagent, plus its messages (`✉ from` / `✉ to`), framed by `▶ <name> · teammate · <desc>` … `■ <name> ended` | a **fourth, lighter (pastel) palette** so a teammate reads apart from a subagent |
| **codex ▶ \<label\>** | **any codex run** — a companion job (`Review` / `Adversarial Review` / `Task` / `Stop Gate Review`) or a raw `codex`/`codex exec` (`cli`) — its commands (`▶ cmd`), reasoning (`⋯ reasoning`), messages (`✎ message`), prompt (`⇢ prompt`) & review/result (`⇠ review` / `⇠ result`) stream in, framed by `codex ▶ <label>` … `■ codex <label> ended` | a **fifth palette** (jade · sky · orchid · gold) so a codex stream never reads as one of our own agents |
| **✚ / ✓ task #N** | an agent-team **shared-task-list** event — created (`✚`, amber) / completed (`✓`, green) | a fixed one-line accent (not a stream) |

Output lines carry a **colour-coded `│ ` gutter** — a per-**stream** tag — so
several jobs running in parallel stay distinguishable when they interleave in the
shared log. Foreground uses one status colour; background, monitor, and subagents
each draw from their own 5-colour palette (so up to 5 concurrent jobs of each kind
— 15 streams plus foreground — get distinct colours; beyond 5 of a kind, colours
reuse). When a background/monitor job launches, its hook **claims a free palette
slot** (a row in the state DB's `live` table, claimed in one transaction,
liveness-checked by pid, released when the streamer exits), colours the header
chip with it, and hands the slot to the streamer for
the gutter + finish — so the whole block is one colour and concurrent jobs of the
same kind never collide. A subagent claims its slot by **`agent_id`** (so its
`SubagentStart` header, its streamer's body, and its footer all share one colour).
A subagent's nested background/monitor job carries **two** gutters — the subagent's
colour outside, the job's own palette colour inside. The colours across all five
palettes are chosen to be well-separated, so no two look alike. (An agent-team
**teammate** reuses the subagent slot machinery — same `agent_id`-keyed slot and
`sub.*` markers, so it keeps the tab blue while it runs — but draws its colour from
the lighter teammate palette instead.)

The **finish chip is the same colour as that stream's gutter**, so you can tell
which stream just finished even when several are interleaved. For foreground
that colour encodes status — so a **failed** command turns the header, gutter,
**and** finish chip **red** (`■ failed (exit 5)`, error output as the body), and
an interrupted one orange (`■ interrupted`). For background/monitor the finish
chip uses the job's palette-slot colour.

A background command is **printed once**: the `▷ background` chip, the command,
then its live output streams directly underneath (orange `│ ` gutter).

The command is **pretty-printed**: its real line structure is preserved (not
collapsed to one line), it's **syntax-highlighted** (via `pygments` — keywords
magenta, strings green, `$variables` yellow, numbers orange, comments grey), and
long lines are **word-wrapped to the pane's current width** with a hanging
indent, so even large commands stay readable in the narrow split.

**The command name itself is highlighted (blue), not just shell builtins.**
pygments' `BashLexer` only tags builtins it knows (`echo`/`cd`/… → cyan); every
external command (`python3`, `kitten`, `sed`, `git`, …) is plain text. So the
formatter runs a command-position pass — the first word at the start, after a
separator (`;` `|` `&&` `$(` newline …), or after a command-introducing keyword
(`do`/`then`/`if`/`while`/…) is retagged blue — leaving builtins cyan and
arguments their default colour.

**Embedded Python is highlighted as Python** — both heredocs fed to python
(`python3 … <<PY … PY`) and `python -c '…'` arguments. The command is split into
bash / python segments and each is lexed with its own lexer (so `import`/`def`
read as keywords, `print` as a builtin, etc.), then merged into one wrapped,
coloured block.

**Dense one-liners are pretty-printed before highlighting.** A compressed command
is reflowed into readable multi-line form — **bash** breaks after top-level `&&` /
`||` / `|` and turns `;` into a line break; **embedded Python** (`-c` args + heredoc
bodies) is reformatted via `ast`, so `python3 -c "import os;x=1;print(x)"` becomes
three real lines. It's width-INDEPENDENT (real newlines the renderer still wraps), so
it runs **once at op creation** (`claude_ops.code` → `claude_render.format_code`), not
in the paint loop. Best-effort and conservative — operators inside quotes (`git commit
-m "a && b"`), background `&`, redirections, bash heredocs, `case` bodies, and Python
that carries comments are all left exactly as written; anything it can't confidently
reformat passes through untouched. Set **`CLAUDE_MIRROR_FORMAT=0`** to show commands
verbatim.

**Section banners in output are emphasised.** Lines that scripts (and Claude Code
itself) print to delimit sections — `=== title ===`, `--- title ---`,
`### title ###` — are rendered **bold amber** so section boundaries pop out of a
wall of output. Detection (`claude_render.emphasize`) runs on each line's *visible*
text and is deliberately conservative: the `=` family needs a run of `==`+ followed
by a space or end-of-line (so `x == y` and valgrind's `==123==` are left alone),
and the `-`/`#`/`*`/`~` forms must be **bracketed** on both ends (so a diff header
`--- a/file` and a bare `-----` rule stay plain). It's applied at every real
command-output site — foreground, background/monitor tail, and subagent output —
but *not* to a subagent's messages/prompts (which share the gutter helper).

**Foreground vs background output.** A *foreground* command's output used to be
unavailable anywhere until it finished — Claude Code streamed it back only through
a private pipe, surfaced to a hook for the first time in the **`PostToolUse`
payload** once the command completed. A *background* command (and a Monitor
stream) was always the opposite: the hook fires at *launch* with no output, but
the live output **is** written to a `tasks/<id>.output` file a detached tailer can
follow. **Live foreground streaming** (below) closes that gap by making a
foreground command behave like a background one for mirror purposes, without
changing what Claude Code itself sees. The mirror is driven by the hook:

- **`claude-cmd-fmt.py`** (the `PostToolUse` Bash hook) does the work — it no
  longer needs the pane width (producers emit width-independent paint ops and the
  renderer wraps them at paint time). It reads the payload
  (`tool_input.command`, `tool_response.stdout`/`stderr`, `duration_ms`),
  syntax-highlights the command (pygments `BashLexer` + `PythonLexer` for embedded
  python), and appends a block of **paint ops** (via `claude_ops`) to the
  session's `ops` table — the command as a `code` op, the output as a
  `gut` op, framed by `rule`/`label` ops; the renderer wraps them to the live
  width. It lives in its own file (not an inline `python3 -c '…'`) so its regexes
  can use both quote characters without bash-quoting hazards. For a **background**
  command it writes a single
  `▷ background` chip + the command and spawns the tailer below (which appends
  the live output directly under it).
- **`claude-stream.py`** (spawned detached, in its own session, by the launch
  hook) tails a background job's / monitor's `tasks/<id>.output` file — located
  by globbing the unique id — and appends each new line to the mirror ops with a
  **Redirected output.** If a background command sends stdout to a file
  (`… > deploy.log 2>&1`), the task's own output file stays empty, so there's
  nothing to tail. `claude-cmd-fmt.py` parses the redirect target out of the
  command (stdout / `&>` only; skips `2>`, `/dev/*`, fd-dups; last one wins),
  resolves it against the hook's `cwd`, and passes it to the tailer via
  `CLAUDE_STREAM_SRC` — which then follows **that** file instead, so the
  redirected output streams live too. Completion detection (write-holder gone)
  works unchanged, since the job holds the redirect file open the same way.
  Two guardrails on the parsed target:
  - **Unexpanded shell syntax is rejected.** `shlex` does no expansion, so a
    target containing `$vars`, backticks, globs, or a leading `~` (`> "$OUT"`) is
    *not* the path the shell will actually write — tailing it would follow a file
    that never appears (and used to drop a literal `$OUT.done` sentinel into the
    project directory). Such commands fall back to the tee side file (fg) / the
    task output file (bg) instead.
  - **A `>>` append target is tailed from its size at spawn**
    (`CLAUDE_STREAM_SKIP_EXISTING`, the same mechanism the Ctrl+B hand-off uses),
    not from 0 — otherwise the target file's entire *prior* contents would replay
    into the mirror as if the command had printed them.

  `│ ` gutter coloured from its kind's palette slot, then writes a
  `■ background finished · Ns` / `■ monitor ended · Ns` line when done. **Completion
  is detected differently per kind** (there is no hook for it — `TaskCompleted`
  is for the TodoWrite list, not background commands):
  - **background** — the command holds its output file open the whole time, so
    the write-holder vanishing (`lsof`) is a definitive signal (works even for a
    long silent `sleep 3600; echo done`). The tailer only *reads*, so it never
    counts itself. A **failed** `lsof` (its 5s timeout on a busy box) reads as
    "can't tell — assume still writing", never "no writer": returning False there
    once ended a stream mid-command during a silent phase (premature finish chip,
    tab green, output lost). Only a *missing* lsof binary disables
    writer-liveness outright (audited once).
  - **monitor** — writes its file in bursts with gaps (no held handle), so the
    write-holder trick fails. Instead the tailer tracks the monitor's **command
    process**: a monitor runs as `zsh -c … eval '<command>'`, a persistent process
    whose argv contains the command. The launcher passes a distinctive token from
    the command; the tailer finds that process (`ps`) and watches it — it exits
    exactly when the monitor ends, so completion is exact at **any cadence** (1s
    or 1h between ticks) with no grace/idle guess. A short idle fallback only
    applies if the process can't be found. **Disambiguation:** the launcher also
    passes the *full* command (`CLAUDE_MONITOR_CMD`) and a whole-command argv
    match always wins — the longest-token signature alone can equally match an
    unrelated long-lived process (another tail/editor holding the same file path
    in its argv), and latching onto that pid kept the block open and the tab blue
    forever. With the full command available, ambiguous token-only multi-hits
    return "not found" so the idle fallback closes the block instead. A **failed
    Monitor call** (`PostToolUseFailure` — no `taskId`, nothing will ever stream)
    gets its block closed inline by `claude-monitor-fmt.py` with a
    `■ monitor failed` chip, instead of a dangling open header.
  - **All tailers handle truncation**: if the tailed file *shrinks* (the command
    runs `> file` again, or the file is rotated in place), `FileTailer.pump`
    restarts from byte 0 — the old offset pointed past EOF, so nothing would ever
    be emitted again (or a regrow would resume mid-content from a stale position).
- **Live foreground streaming (Ctrl+B aware).** `claude-cmd-pre.py` (`PreToolUse`
  Bash) makes a normal foreground command stream live instead of only appearing
  once it completes. It rewrites the command via `PreToolUse`'s `updatedInput`
  (undocumented but confirmed working) to also `tee` its stdout/stderr into a side
  file — `{ <cmd>\n\n} > >(tee -a "$F") 2> >(tee -a "$F" >&2)` (the blank line
  before `}` is load-bearing: a command ending in a line-continuation backslash
  eats the first newline, which used to weld the `}` onto the last line — a
  syntax error for a command that ran fine unwrapped), or the command's own
  redirect target if it already has one — emits the `▶ foreground` header
  immediately, claims an `fg.<n>` slot (so the tab tracker sees it, above), and
  spawns `claude-stream.py fg` to tail `$F` the same way a background job is
  tailed. `claude-cmd-fmt.py`'s `PostToolUse` handler is the only place the real
  outcome (duration/exit code/interrupted) is known, so it hands that off to the
  tailer via a **state-DB hand-off record** (`claude_state` handoffs — was a
  `.done` sentinel file polled with exists/read/remove; `hand_take` is the same
  take-once, atomically). The hand-off key is a **session-keyed token** chosen by
  `claude-cmd-pre.py` (stored in the `fg-live` record as `done` and passed to the
  tailer via `CLAUDE_STREAM_DONE`) — deliberately **not** derived from `$F`: when
  `$F` was the command's *own* redirect target, the file-era sentinel derived from
  it dropped stray `<target>.done` files (even literal `$VAR.done`) into the
  project directory. If nothing ever landed in `$F` (e.g. an older Claude Code
  build ignoring `updatedInput`), the hand-off also carries the real output as a
  fallback so nothing is silently lost. The `fg` tailer gives up by **writer-liveness**, like
  `bg`, not a fixed timeout — it keeps the block (and the tab) blue for as long as
  the command is *actually* still running, not for a guessed duration.
  - **Ctrl+B (backgrounding a running command).** Confirmed empirically,
    undocumented anywhere: backgrounding a foreground command with Ctrl+B fires
    that Bash call's `PostToolUse` immediately, with `duration_ms` covering only
    time-up-to-the-keypress, but `tool_response` carries `backgroundTaskId` +
    `backgroundedByUser: true` — and observably, further output stops landing in
    our own tee file and instead appears in Claude Code's own
    `tasks/<backgroundTaskId>.output`, the same file a genuine
    `run_in_background: true` call uses. `claude-cmd-fmt.py` detects this
    (`backgroundTaskId` present despite `run_in_background` being false) and hands
    off: tells the departing `fg` tailer to bow out quietly (a `{"converted":
    true}` sentinel — no chip, no fallback body, so it doesn't race the
    replacement), prints a `▷ backgrounded (ctrl+b) — continuing below` note, and
    spawns a genuine `bg` tailer against the real `backgroundTaskId`, reusing the
    same `_spawn_stream` used for an explicit background command. That tailer
    starts from the *current size* of the task's output file
    (`CLAUDE_STREAM_SKIP_EXISTING`), not from 0, so whatever the `fg` tailer's tee
    copy already showed isn't repeated.
  - **A manually cancelled command** fires no hook at all (the
    no-hook-on-interrupt gap noted throughout this doc), so `claude-cmd-fmt.py`'s normal consume of
    the `fg-live` record (a state-DB hand-off, key `fg-live` — was a `.fg-live`
    JSON file) never runs. Left alone, that stale record would make
    `claude-cmd-pre.py` think a live block is *already* in flight forever, and
    silently skip wrapping every later command (the mirror would just stop
    showing anything new). The record stores the tailer's pid, and
    `claude-cmd-pre.py` liveness-checks it (`os.kill(pid, 0)`, the same pattern
    `claude_slots` uses for stale slots) before treating an existing claim as
    genuinely in-flight — a dead pid means abandoned, so it's cleared and the next
    command streams normally.
  - **The `fg-live` record is keyed to its tool call** (`tid` = the payload's
    `tool_use_id`), and `claude-cmd-fmt.py` consumes it with a *matched* take
    (`hand_take(..., match={"tid": …})`). Without the key, a cancelled command's
    surviving record (its tailer still alive in the writer-gone grace window) was
    consumed by the **next** Bash call's `PostToolUse`, which then wrote its own
    chip and fallback body into the cancelled command's block while itself never
    rendering — two commands cross-wired. A mismatched take leaves the record in
    place and returns None; the exiting `fg` tailer also reclaims **its own**
    record (matched on pid) so a cancelled command's record doesn't linger.
  - **Redirect detection is quote-aware** (`claude_ops.parse_redirect`,
    `posix=False` tokens): posix tokenising stripped quotes, so `grep '>' file`
    parsed as a *redirect to `file`* — cmd-pre then skipped the tee rewrite and
    the tailer streamed the whole existing file into the mirror as "command
    output" (tail-from-0 is only correct when a real `>` truncates). Heredocs,
    `>|` clobbers, and `>(…)` process substitution all return None (the body of a
    heredoc tokenises like real redirects and last-wins picked those) — None just
    means falling back to the tee side file, which is always safe.
  - **The rewrite auto-approves deliberately.** `updatedInput` only takes effect
    with `permissionDecision: "allow"` (auto-approve) or `"ask"` — and `"ask"`
    prompts on *every* rewritten command, even ones your allowlist would pass
    silently (there is no "rewrite, then normal permission rules" option in
    Claude Code today). `"allow"` is the chosen trade-off: rewritten foreground
    commands never permission-prompt (deny rules still apply). This is a
    documented, deliberate decision — not a bug to fix.
  - Escape hatch: `CLAUDE_MIRROR_LIVE_FG=0` disables the command rewrite entirely
    if it ever misbehaves on some pathological command's quoting.
- **`claude-monitor-fmt.py`** (the `PostToolUse` hook
  for the `Monitor` tool) write a cyan `◉ monitor · <description>` header and
  spawn `claude-stream.py` for the monitor's event stream — so Monitor output
  shows in the split too, even though Monitor bypasses the Bash tool.
- **Subagents** (the Task/Agent tool) get **full visibility** by streaming the
  subagent's own transcript, which is the only source that has *everything in
  order*: the prompt it was given, its text messages, every tool_use, and every
  tool_result. (Hooks fire for a subagent's tool calls — tagged `agent_id` —
  but can't show its *messages* and would race/mis-order against the messages, so
  `claude-cmd-fmt.py` / `claude-file-fmt.py` deliberately **skip** `agent_id`
  events; the streamer owns subagent rendering.)
  - **`claude-substream.py`** (spawned detached by `SubagentStart`) tails
    `<dir>/<session>/subagents/agent-<id>.jsonl` and renders, in order: the
    **prompt** (`<type> ⇢ prompt`), each **message** (`<type> ✎ message`), each
    **command** (`<type> ▶ foreground` / `<type> ▷ background` — agent name +
    kind keyword), **file ops** (`<type> Read(name)` / `<type> Update(name) +N -M`
    — led by the agent's name in its colour, so a Read/Update/Write is attributable
    to the subagent or teammate that ran it), other tools, and the subagent's
    **returned result** — its final message, labelled `<type> ⇠ result` to set it
    apart from intermediate `✎ message` chatter — then the `■ <type> ended · Ns`
    footer. All in the subagent's colour. (Messages are committed one event late
    so the last one can be tagged `⇠ result`.)
  - **Per-turn context fill.** Every assistant turn carries a `message.usage`, so the
    streamer prints a colour-coded `<type> ctx N% · used/max` line once per turn —
    `input + cache_creation + cache_read` tokens over the window (**< 30% green,
    < 60% amber, else red**; thresholds tunable via `CLAUDE_MIRROR_CTX_WARN` /
    `CLAUDE_MIRROR_CTX_CRIT`). The **window is derived from the model**, not a flag or
    self-correct: Haiku → 200k; `[1m]` / Opus 4.6-4.8 / Sonnet 5 / Fable 5 / Sonnet 4.6
    → 1M; older/unknown → 200k; `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` caps at 200k. The
    `■ <type> ended` footer closes with the same fill, upgraded to the **authoritative**
    window from the parent transcript's Task result (`resolvedModel`) when it's landed.
    A `compact_boundary` record renders an amber `<type> ⟳ compacted · pre → post
    (trigger)` line (`post` shown as `?` when absent), so the fill drop on the next
    turn makes sense.
  - **Cumulative usage rollup in the footer.** After the final ctx fill, the
    `■ <type> ended` footer appends a whole-run summary: `· <in> in · <out> out ·
    cache N% · K tools`. Unlike the per-turn ctx fill (a single-turn snapshot), these
    sum **every** assistant turn: **in** is fresh billed input (`input_tokens +
    cache_creation` — tokens actually sent, not replayed), **out** is generated
    (`output_tokens`), and **cache %** is `cache_read / (in + cache_read)` — the share
    of all context reads served from cache, i.e. a reuse/thrash signal. **tools** counts
    every `tool_use` block. Usage is deduped by `message.id`, same as the main
    session's `bump_transcript()` below — one assistant message is one JSONL line
    *per content block*, each repeating the message's usage, so summing per line
    inflated the rollup (and the scoreboard bump it feeds) ~2.2× on multi-block
    agents; repeat lines of the same id add only the (output) delta, tracked in
    `usage_last`. That record is persisted in the state DB next to the byte
    checkpoint (the agent record's `pos` + a `usage_last` kv slot) so a successor
    streamer (idle-teammate restart) doesn't recount a message straddling the
    handoff. The rollup is appended last, so on a narrow pane
    it's the first thing the renderer's `fit()` truncates — duration and ctx always
    survive.
  - **Crash-safe spend reconciliation.** The scoreboard bump lives in this footer, so
    a streamer that dies *before* the footer (a crash, or a kill) drops the agent's
    un-bumped token tail — the scoreboard reads under `/cost` by exactly that gap. (The
    real culprit was an `AttributeError: 'dict' object has no attribute 'strip'` in
    `on_tool_use`: a `SendMessage` whose body was a structured content block instead of
    a string; now normalised through `result_text`.) To make the loss unrecoverable-proof
    rather than just fixing the one trigger, the footer now advances a persisted
    cumulative baseline (`billed:<agent>` kv — `{in,out,cache,create}` summed across the
    whole streamer chain), and **`claude-subagent-fmt.py`'s `SubagentStop`**, once it
    sees the streamer is gone, folds the agent's *full* transcript to its true total
    (`claude_ops.fold_usage`, the batch analogue of the inline fold — same `usage_fold`
    dedup) and bumps only `true − baseline` (a `bump-agent` with `meta.reconcile`, plus
    a `reconcile` audit row). Idempotent: a clean finish or a duplicate stop leaves
    `true == baseline` and bumps nothing. This recovers the *transcript-resident*
    shortfall; a separate residual (interrupted/retried turns whose billed usage never
    lands as complete assistant lines on disk) is not transcript-recoverable and leaves
    a transcript-folding scoreboard slightly under `/cost` on cancellation-heavy sessions.
  - **Cost estimate in the footer.** After the rollup the footer appends `· ≈ $X`,
    the summed tokens priced on the resolved model (`claude_ops.PRICES`, per-MTok
    input/output for the current lineup; `cache_read` billed at ~0.1×). An unknown
    model shows nothing rather than guess. Being last, it truncates before the rest.
  - **Session scoreboard (its own window).** A running "so far" summary of the whole
    session, aggregated across the separate hook processes in the **per-session
    state DB** `…/<mirror-log>.state.db` (`claude_state.py`; was an flock'd
    `.stats.json` sidecar — atomic SQL increments replaced the read-modify-write
    JSON dance, so bumps can neither tear nor clobber and reads never see a torn
    write; parked as `*.keep` with the log at SessionEnd and restored on resume, so
    the scoreboard's counters survive a `--resume`/`--continue`). The scorebar repaints when the
    state's change counter moves (a `v` counter bumped by every write — WAL commits
    don't reliably touch the db file's mtime). **`claude-scorebar.py`** renders it in a
    **dedicated 5-row window hsplit under the mirror** (`var:claude_scorebar=<sid>`,
    `BAR_ROWS` in `claude-split.py`, opened/closed with the mirror by it) — an always-on
    session-id line, a team-message census, the session summary, then a token breakdown:

    ```
    ⬡ 95466f49-240b-4b69-92b4-96bd1541a9a9
    ✉ 5 msgs · 1● unread · 2◐ stale · 1◉ read
    ▪ 45 cmds (5✗) · ⏱ 68m24s
    Σ 56M total · 428k in · 197k out · 55M cache · 410k write · ≈ $1.20
      56 files · +791 -29 · Read 34 · Edit 18 · Write 4
    ```

    The **`⬡` session-id row** is always shown (parsed from the mirror-log filename),
    so a pane is identifiable at a glance. The **`✉` message census** gives live
    visibility into the agent-team message flow and is **always shown** (defaults to
    `0 msgs`, even for a non-team session). It comes from `claude_msgs.update_messages()`,
    which — since there is **no hook** for a message being read/consumed — tracks state
    by **stateful polling**: each tick it diffs the team inboxes against the persisted
    state (the state DB's `messages` table, keyed by `(msg_id, recipient)` — was a
    `.msgs.json` sidecar; per RECIPIENT COPY because a broadcast puts the same
    `msg_id` in several inboxes, and collapsing those to one entry made the read
    flag whichever copy the scan saw last: deliveries undercounted, reads
    double-counted or lost) and folds transitions into
    **cumulative** counters, so counts survive a teammate draining its inbox. A message
    is `read` once it flips `read:true` or disappears from the inbox (draining ⇒
    consumed); `msgs` is the cumulative delivered total. `unread` and `◐ stale` are a
    **current-state** split of what's pending right now — `stale` being anything unread
    for more than `STALE_S` (60s), a disjoint group from `unread` (so `unread + stale =
    delivered − read`). Since the team files carry **no liveness flag**, `stale` is also
    the only available (age-based) signal for a message sitting in the inbox of a
    crashed recipient. The same tracker also **emits into the mirror stream** on each
    transition — a `● <from> → <to>` chip (+ summary) when a message is delivered, a
    `◉ read · <from> → <to>` chip when it's consumed — so arrivals/reads interleave with
    the command stream. Both the census and the events miss transitions that happen
    entirely while the mirror is toggled off (nothing is polling then) — an accepted gap
    for an ambient aid.

    A separate window — not lines pinned inside the mirror — because that's the only
    thing that survives **scrolling**: anything drawn in the mirror's own screen
    scrolls away with its history, and a DECSTBM scroll region would keep it pinned
    only by discarding scrolled lines instead of pushing them to scrollback. Styling
    is deliberately muted (no background chips): dim separators, slate words, brighter
    numbers, and colour only where it means something — failures/removed red, added
    green, cost orange. It repaints on every sidecar bump and at least once a second
    (so the `⏱` ticks live). The `⏱` counts **active time**, not wall clock: it
    **pauses while the tab is green** (awaiting-response — Claude is done, your turn)
    and resumes on any other colour. The scorebar maps its sid to the Claude pane's
    kitty window (the `claude_session` user-var tagged at SessionStart), polls that
    window's persisted tab state (the global tab DB's `tab` row), and accumulates
    green ticks into the state's `paused` counter (same atomic `bump()`, so it
    survives a mirror toggle); `scoreboard_parts()` subtracts it from the elapsed
    time. It truncates from the tail on narrow panes, and **exits when the mirror log
    disappears** at SessionEnd, auto-closing its window (`claude-split.py close` is the
    safety net). Each row is grouped by concern: the **`▪` row is just activity**
    (commands + failures + active time); the **`Σ` row is all token counts plus the
    `≈ $` cost** — spend derives from tokens, so it sits here rather than on `▪`, and
    goes **last** so the tail-drop sheds it before the token breakdown; the **last row
    carries every file/line/tool figure** — the unique-`files` count, then the `±`
    line-diff (`+added -removed`, relocated off `▪`), then the tool tallies. The
    structured data comes from `claude_ops.scoreboard_parts()` (which now returns only
    the `▪`-row activity + the tool tallies; the renderer reads `files`/`added`/
    `removed`/`cost` straight off the stats dict for the rows they moved to). The tools
    row **excludes Bash** — its count is already the `cmds` figure (same bump; listing
    it again would just duplicate the head). The unique-`files` count and the `±`
    line-diff **lead that row** (kept when it must drop segments — the tool tallies pop
    from the tail first).
    `files` counts **unique files** (touched paths are deduped in the state DB's
    `files` table; re-editing the same file doesn't inflate it) while the tool counts
    are operations — so `Edit 18` against `5 files` reads as 18 edits across 5 distinct
    files (and `Read 90` against `87 files` is the same file read more than once, not a
    miscount). The file **and command** counters are
    **team-wide**: the main session's own ops feed them via `claude-file-fmt.py` /
    `claude-cmd-fmt.py`, and every **subagent/teammate** op feeds them too —
    `claude-substream.py`'s `render_file` bumps the same `files`/`added`/`removed` (and
    tools) counters for every subagent file tool (Read/Edit/MultiEdit/Write/NotebookEdit),
    matching `claude-file-fmt.py` op-for-op — including a **failed** mutation, which counts
    the path + tool but **0** added/removed (a failed Write never wrote its lines, so
    `render_file` skips `diff_counts` on `is_error`, same as file-fmt's `if not failed`).
    Its `on_tool_result` also bumps `commands`/`failed`/`tool:Bash` for
    each subagent Bash call (a background launch counts at spawn; a foreground call counts
    its `is_error` as a failure) — so the `▪` row's `N cmds (M✗)` covers the whole team,
    mirroring how the ended-footer already folds each agent's *token* spend into the
    scoreboard. (`claude-file-fmt.py` **and** `claude-cmd-fmt.py` deliberately skip any
    `agent_id` call — the substream owns subagent rendering *and* its accounting, so
    there's no double count; without the command half, a session whose command failures
    were all inside subagents showed `(0✗)` despite the failures being real.) Because
    `files` is a unique-path set shared across the
    whole session, an agent re-touching a path the main session already touched never
    inflates it. It's handoff-safe: each transcript line is consumed exactly once
    across the streamer chain (the `pos` checkpoint), so an idle-teammate restart
    can't recount, and the bump lands as a plain `bump` row (deltas are files/lines,
    not the tokens/cost the unattributed-bump anomaly guards).
  - **Tokens + cost cover the whole session.** The `cost` figure prices fresh billed
    input (`input + cache_creation`) plus output plus the cheap cache-read/write rates;
    the underlying `tokens` counter (fresh input + output — cache reads are replay, not
    billed) backs it and the Σ total but is no longer shown on the `▪` row itself (the
    Σ row owns the token display). Two producers feed it: each **agent's** streamer
    bumps its totals when the run ends, and the **main session's own turns** are
    folded in by `claude_ops.bump_transcript()` — called from the cmd/file hooks
    **and from `claude-stop-fmt.py` on every `Stop`/`StopFailure`**, it
    reads the session transcript JSONL forward from a cursor kept in the state DB
    (the `txpos` counter), sums each new assistant turn's usage (skipping sidechain
    records — their own streamer already counts them), and advances the cursor inside
    one `BEGIN IMMEDIATE` transaction so concurrent hooks never double-count. (Before this, cost only moved
    when an agent run ended and sat "stuck" through plain main-session work.) The
    **`Stop` trigger closes the final-turn tail**: the cmd/file hooks only fire on a
    tool call, so a turn's closing reply (no trailing tool) — and the whole last turn
    of a session — was never folded, dropping its tokens and (cache-read-dominated)
    cost and leaving the scoreboard a few % under `claude --resume`'s real total.
    `Stop` fires at the end of every turn, so each is folded before the next begins
    and the last before SessionEnd parks the DB — no SessionEnd fold is needed (it
    would race the park/rename). The fold is idempotent (the `txpos` cursor guards
    re-reads), so a repeated `Stop` never double-counts.
  - **The `Σ` row is the token display: a per-category breakdown with an all-in total.**
    The `Σ` row (`claude_ops.token_parts()`) shows the four raw
    categories — **input · output · cache read · cache write** — plus a **total** that
    ADDS cache-read replay, so it reconciles with what `claude --resume`'s "Usage by
    model" reports (that total is dominated by cache read on a long session, so it far
    exceeds *billed* spend — different metrics, on purpose). Both accountants
    feed four dedicated counters (`tk_in`/`tk_out`/`tk_read`/`tk_create`) from the same
    `usage_fields` split `cost_usd` prices, so `tk_in + tk_create + tk_out` equals the
    billed `tokens` counter and `+ tk_read` is the Σ total's extra. Total-first so a
    narrow pane keeps the headline.
    One assistant **message** is written as one JSONL line **per content block**, each
    repeating the message's usage (input/cache identical, `output_tokens` a growing
    snapshot), so usage is deduped by `message.id` — counted once, from the last line.
    A message whose lines straddle two bump calls is handled by the state's `txlast`
    record (last counted id + what was credited): later lines of the same id add only the
    delta. (Before the dedup, multi-block turns counted 2–3×, inflating a $3.84
    session to a $7.29 scoreboard.) The **agent streamers apply the same dedup** to
    their footer rollup (`usage_last` in `claude-substream.py`, persisted in the
    state DB next to the `pos` checkpoint) — they originally summed per line, which showed up
    as a second instance of the same bug: a session whose four review agents really
    billed ~784k tokens bumped 1.75M (×2.24), turning a $18.76 session into a $23
    scoreboard.
  - **Pricing** (`claude_ops.PRICES`, verified against the published 2026-06 list):
    Fable/Mythos 10/50 · Opus 4.6-4.8 5/25 · Sonnet 3/15 · Haiku 4.5 1/5 · legacy
    Opus 4.1/4.0/3 15/75 per MTok in/out. The table keys are **substrings of the
    real model ids** — the legacy rows are `opus-4-1` / `opus-4-2025`
    (`claude-opus-4-20250514`) / `3-opus` (`claude-3-opus-…`); the earlier
    `opus-4-0`/`opus-3` keys appeared in *no real id*, so every legacy-Opus run
    fell through to the generic 5/25 row (a silent 3× undercount). Cache reads bill 0.1× input and cache
    **writes 1.25×** (the `cache_creation` share is tracked separately so the 0.25×
    premium is applied). Sonnet 5's introductory 2/10 rate is used automatically
    through 2026-08-31, then reverts to the 3/15 sticker. An unknown model counts
    tokens but adds no cost rather than guess.
  - **`<model>·<effort>` on every op header.** Each operation header (prompt, message,
    result, command, file op) is tagged, e.g. `opus-4.8·high`. The **model** comes from
    the agent's own turns (`message.model`); before the first turn lands, the prompt
    line falls back to the agent's configured model (its `meta.json`) or the parent
    session's version (tail-read from the parent transcript), so it's precise from line
    one. **Effort is config-only** — it appears in *no* transcript — resolved in the
    documented precedence: `CLAUDE_CODE_EFFORT_LEVEL` env > agent-def frontmatter
    `effort:` > settings `effortLevel` > the model's default (`high` on Opus 4.8/4.6 ·
    Sonnet 5 · Sonnet 4.6 · Fable 5, `xhigh` on Opus 4.7). A **teammate's** def is found
    via its `meta.json` `customAgentType` (its short type — `container` — doesn't match
    the def's `name:`/filename `task-container`). The def + settings are looked up across
    **every ancestor `.claude/` dir** (`claude_ops.claude_dirs`, `$CLAUDE_PROJECT_DIR`
    honoured, else walk up from cwd, nearest-first, ending at `~/.claude`) — **not** just
    `cwd/.claude`: a teammate/subagent often runs in a subdirectory (a task's
    `.zhambyl/tasks/<t>/db`) or a git worktree, where `cwd/.claude` is absent or a stub
    without `agents/`; collecting *all* ancestors falls through to the repo-root def so
    `effort: high` is read instead of dropping to the session/global `low`. (The defs are
    untracked, so a nested worktree correctly resolves up to the main tree's copy.)
    Caveat: a session-only `/effort max` / `ultracode` / `--effort` that never persists to
    settings can't be seen here.
  - **`claude-subagent-fmt.py`** (the `SubagentStart`/`SubagentStop` hook) drives the frame:
    `SubagentStart` claims the colour slot (keyed by `agent_id` so header, body,
    and footer match; parallel subagents differ), writes the `▶ <type> · <desc>`
    header, and launches the streamer; `SubagentStop` sets the agent record's
    `done` flag in the state DB (was a `sub.done.*` sentinel file), which the
    streamer polls (its authoritative end signal for a **normal finish** —
    *not* `meta.json`, which is written at subagent **start**); the flag is
    cleared again at streamer finalise so a later RESUME of the same agent_id
    doesn't finalise its new streamer instantly. The agent record also carries the
    pinned colour `slot`, the `desc`, and the resume checkpoint `pos` (were
    `sub.slot.*` / `sub.desc.*` / `sub.pos.*` files); the streamer's pid registers
    as a `sub.pid` row in the `live` table (was a `sub.pid.*` marker file) — the
    tab tracker's liveness signal, read by `claude-tab-status.py`. The streamer is
    the **sole footer writer**; `SubagentStop` only closes the block itself as a
    safety net, and **only when a colour slot is still claimed** (the streamer
    died mid-run without finalising). Independent of the footer, `SubagentStop`
    also runs `reconcile_spend` whenever it finds the streamer gone (crashed or
    already finalised): it folds the agent's full transcript to its true token total
    and bumps whatever the streamer chain didn't (see *Crash-safe spend
    reconciliation* above) — this runs even when the crashed streamer's own cleanup
    already released the slot, so it is **not** gated on the safety-net footer.
    - **Manually cancelling/killing a subagent fires no `SubagentStop` at all** —
      the same no-hook-on-interrupt gap noted throughout this doc (`interrupt-watch`,
      the cancelled-foreground-command fix above). Left alone, the streamer would
      hang on the sentinel until its 6h backstop, leaving the tab **stuck blue**
      the whole time (`sub.pid.<agent_id>`'s pid stays alive — a subagent has no
      OS process of its own to go liveness-check). But Claude Code *does* stamp
      `stoppedByUser: true` onto `meta.json` the moment a cancel happens
      (confirmed empirically) — so the streamer polls that alongside the sentinel
      and exits within its next 0.3s tick, releasing the slot and triggering the
      usual `bg-recheck` handoff to green. The footer reads `■ <type> cancelled ·
      Ns` instead of `ended` in this case.
    A background agent's `SubagentStop` can fire **more than once** ("may notify
    more than once") — after the first, the streamer has finalised and freed its
    slot, so the duplicate finds no slot and does nothing. (Without that guard a
    duplicate stop printed a spurious slot-0 indigo `■ agent ended`.) The
    safety-net footer itself is emitted only when this call's `release_id`
    atomically deleted the slot row (its rowcount is the once-only licence) —
    two *overlapping* duplicate stops could both pass the lookup check and both
    paint the footer otherwise.
    - **Quitting Claude Code with a background agent running** leaves a third
      end-shape: the agent is killed with no `SubagentStop` and no
      `stoppedByUser` stamp, but SessionEnd parks the state DB — so the streamer
      also polls the DB file's existence (same check the codex tailers run) and
      exits `state-db-parked (session end)` instead of spinning to its 6h
      backstop as a zombie whose checkpoint writes mutate the parked `*.keep`
      snapshot through its cached connection. No footer/bumps after that: any
      write would either land in the snapshot or recreate the DB file — whose
      existence IS the session-alive signal watchers poll.
  - **Nested background / monitor → double gutter.** When a subagent launches a
    `run_in_background` command (or a Monitor), the streamer extracts the task id
    from the tool_result and spawns `claude-stream.py` with the subagent's colour
    as an *outer* gutter on top of the job's own palette-slot *inner* gutter
    (`│ │ …`). So a subagent's several background jobs share its outer colour but
    differ by inner colour, and stay distinct from other subagents' jobs.
  - The **description** isn't in the `SubagentStart` payload, and the on-disk
    `agent-<id>.meta.json` that has it is written at subagent *start* with no end
    marker (so it can't signal completion). So a `PreToolUse` hook on the
    Task/Agent tool (`claude-subagent-fmt.py push`) stashes the description in a
    tiny FIFO (a `queue` table in the per-session state DB — was an flock'd
    `desc.queue` file) and the next `SubagentStart` pops it — exact for sequential
    subagents (for several same-type subagents launched at once, the worst case is
    two descriptions swapped, purely cosmetic).
  - Highlighting/wrapping/gutter/unescape primitives live in **`claude_render.py`**
    (shared by `claude-cmd-fmt.py` and `claude-substream.py`).
  - **Duplicate `SubagentStart`.** A background agent (and an agent-team teammate
    in particular) can fire `SubagentStart` **more than once**. The start hook only
    writes a header + launches a streamer on the *first* one: if the slot is already
    claimed and the streamer is still live, it returns early — otherwise the whole
    transcript would re-render under a second header.
- **Agent teams.** With `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, an **in-process
  teammate** is, at the hook + storage layer, just a subagent: it fires
  `SubagentStart`/`SubagentStop`, its tool calls carry an `agent_id`, and its
  transcript is the same `subagents/agent-<id>.jsonl` — so the whole subagent
  pipeline above carries it for free (including keeping the tab blue while it runs).
  Three things differ, all keyed off the teammate's `meta.json`
  (`taskKind == "in_process_teammate"`, present already at `SubagentStart`):
  - **Colour + header.** It draws from a fourth, lighter (pastel) palette and its
    header reads `▶ <name> · teammate · <desc>`. (The slot itself is still the
    `agent_id`-keyed `sub` slot, so no extra bookkeeping and the tab logic is
    unchanged.) `claude-subagent-fmt.py` passes the palette name to the streamer so
    header, body, and footer stay one colour.
  - **Messages.** `claude-substream.py` renders inter-agent mail from the same
    transcript: a delivered message (a `user` record wrapped in
    `<teammate-message teammate_id="…">…</teammate-message>`) shows as `✉ from
    <sender>` with the wrapper stripped; an outgoing `SendMessage` tool call shows
    as `✉ to <recipient>` with the body (its `{success:true,…}` ack is suppressed).
  - **Tasks.** The shared task list is rendered straight from the `TaskCreated` /
    `TaskCompleted` hooks (`claude-task-fmt.py`) as a compact
    `✚ task #N · <subject>` (amber) / `✓ task #N · <subject>` (green) line — there
    is no readable per-task file on disk, so the hook payload is the source. (The
    payload fields are `task_id` + `task_subject` + `task_description`, *not* the
    `task_title`/`task_status` the docs list.)
  - Out of scope: **split-pane** teammate mode (tmux/iTerm2) runs each teammate as
    its own process/session rather than an in-process subagent, so it wouldn't flow
    through these hooks; the default in-process mode is what's supported here.
- **Codex streams (global — EVERY codex call).** The mirror shows any codex run,
  however it was launched — a `/codex:review`, an adversarial-review, a `task`, the
  stop-gate, or a **raw `codex` / `codex exec`** in a shell; fired by the **main
  agent, a subagent, an agent-team teammate, a foreground OR background command, or a
  slash subcommand**. Rather than detect the codex *command* at every launch site, a
  per-session watcher tails **two directories** every codex run funnels through, and
  spawns a streamer per run. Nothing is wired per-launcher; new codex entry points are
  covered for free.
  - **`claude-codex-launch.py` → `claude-codex-watch.py`.** `claude-split.py open`
    (SessionStart) runs the tiny **launcher**, whose only job is to `Popen` the watcher
    with `start_new_session=True` and exit in a few ms. This is load-bearing: launching
    the long-lived watcher from the hook with a bash `&` left it in the **hook's process
    group**, which Claude Code waits to drain — so SessionStart hung ("no answer") and
    the watcher orphaned. Detaching it into its own session (the same way the other
    streamers are spawned) makes the hook return instantly. The watcher exits on its own
    when the session's mirror log vanishes at SessionEnd (parked as `*.keep`, so
    the path the watcher polls still disappears); a pid-liveness claim in
    the session state DB (key `codex-watch` — was a `codex.watch.pid` lock file)
    guards against a duplicate SessionStart.
  - **Source A — companion jobs** (`codex-companion.mjs`, the common case). Each job
    writes a human-readable activity log + a status sidecar to
    `$CLAUDE_PLUGIN_DATA/state/<slug>/jobs/<jobId>.{log,json}`. The watcher recomputes
    the `<slug>` exactly as codex does (`basename(git-root)` +
    `sha256(realpath(git-root))[:16]`) and streams each **new** job matched to this
    session by the sidecar `sessionId` (started-after-launch time gate as fallback).
    Completion is the sidecar `status` going `completed`/`failed`/`cancelled`. Labelled
    by job title — "Review", "Adversarial Review", "Task", "Stop Gate Review".
  - **Source B — native rollouts** (catches raw codex the companion never saw). EVERY
    codex run also writes `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`. The
    watcher scans today's + yesterday's dirs, matches a run to this repo by the
    `session_meta` `cwd`, and streams it — parsing the clean `event_msg` records
    (`user_message` → `⇢ prompt`, `agent_reasoning` → `⋯`, `agent_message` → `✎`) plus
    `exec_command` shell calls (`▶ cmd`), labelled `cli`. Completion is a `task_complete`
    with no follow-up turn. **Dedup:** the rollout filename's `<uuid>` *is* the companion
    sidecar's `threadId`, so a run already handled by source A is skipped here (after a
    short grace that lets the sidecar reveal its threadId) — a companion job streams
    once, with its nicer label, never twice. **The predates-this-session filter uses
    the rollout's *creation* time** (the filename timestamp, falling back to inode
    birth time) — deliberately not mtime: a rollout still being *written* refreshes
    its mtime forever, so a long `codex exec` started before this session passed an
    mtime filter, its dead previous claim was stolen, and its entire history
    replayed from byte 0 into the new session's mirror.
  - **`claude-codex-stream.py`** renders both sources into the codex palette (colour
    picked round-robin by the watcher and passed as `r,g,b`; it keeps no slot marker, so
    it never affects the tab colour): `▶ cmd` (syntax-highlighted), `⋯ reasoning`,
    `✎ message`, `⇠ review` / `⇠ result`, framed by a rule-bracketed `codex ▶ <label>`
    … `■ codex <label> ended · Ns`. Successful sub-commands are suppressed; a non-zero
    exit shows a red `■ exit N`. It never writes after the state DB is parked: the
    header emit re-checks the DB file right before painting (SessionEnd can park it
    during the tailer's wait-for-source window, and `claude_state`'s connect would
    *create* a missing DB — resurrecting the session-alive signal the watcher polls,
    which then never exits), and a park detected mid-stream skips the footer rather
    than writing it into the `*.keep` snapshot via the cached connection.
  - **Session/cwd-attributed, not nested.** A codex run is keyed to the Claude
    `sessionId` (source A) or the repo `cwd` (source B), not the launching `agent_id`,
    so it reads as its own **top-level** stream rather than nested under the teammate
    that launched it — the deliberate trade for a global, zero-per-launcher design. (Two
    Claude sessions in the same repo both show a source-B run, the same per-project
    caveat as background-job detection.)
- **`claude-mirror.py LOG`** — the renderer — runs inside the pane (launched
  directly by `claude-split.py`, replacing the old `tail -F`) on that session's
  log KEY (the key is the historical `/tmp/claude-mirror-<sid>.log` path, from
  which the state-DB path derives — no log file exists anymore). At startup it
  re-execs itself into a `pygments`-capable interpreter if the launching one
  lacks it (see *Pretty-print needs pygments* below). The
  renderer polls the structured paint-op rows (the state DB's `ops` table, see
  *Reflow* below), paints each op at the pane's **current** width, and re-renders
  everything on resize (`SIGWINCH`) so content **reflows**. It reads the table
  from id 0 and **never deletes** — so toggling the pane off/on re-shows the whole
  session history (the DB is created fresh only for a genuinely new session and
  parked as `*.keep` at SessionEnd — a `--resume`/`--continue` restores it, so the
  mirror replays the prior session; see `claude-split.py` below), and while off
  there is no process at all. It keeps at most `MAX_OPS` (8000) ops in memory so a
  long session can't grow unbounded. One process — no file-switching,
  byte-offsets, `lsof`, or orphaned tails.
- **`claude-file-fmt.py`** (a `PostToolUse` hook for `Read`/`Edit`/`Write`/
  `MultiEdit`/`NotebookEdit`) logs file operations as compact one-liners showing
  just the verb + basename — `Read(README.md)`, `Update(README.md)`,
  `Write(new.py)` — interleaved with the command blocks so the pane reads as a
  running log of what Claude did. Verbs mirror Claude Code's own UI (Edit/
  MultiEdit → **Update**, colour-coded: read blue, update yellow, write green);
  formatting lives in **`claude-file-fmt.py`**. A mutation also shows its
  added/removed line counts — green `+N` / red `-M`, e.g. `Update(task-manager.md)
  +18 -1` — from a real line-level diff of the tool input (`old_string` vs
  `new_string`, summed over a MultiEdit; the whole body for a Write), the same
  additions/removals Claude Code reports. Reads and failures show none. The shared
  counter is `claude_ops.diff_counts()`, used by both this path and the subagent
  streamer. A mutation also shows the **line range(s) it touched** — a dim
  `start-end` after the counts, e.g. `Update(README.md) +18 -1 445-462`, comma-joined
  for a multi-hunk MultiEdit (capped at 3, `+k` for the rest) — read from the result's
  `structuredPatch` hunks via `claude_ops.edit_range()`; a brand-new Write shows no
  range (its `+N` already says the size). A **Read** instead shows how much of the file it took: a bare
  `Read(name)` means the **whole file**, while a dim `start-end/total` (e.g.
  `Read(big.py) 1-2000/5000`) flags a **partial** read — either an explicit
  `offset`/`limit` slice or a bare read that hit Claude Code's **2000-line cap** on a
  larger file. The extent comes from the result's `startLine`/`numLines`/`totalLines`
  via `claude_ops.read_extent()`.
- **`claude-split.py open|close|toggle|grow|shrink|reset|setpct`** manages the pane,
  **per Claude session**. Everything is keyed by `session_id` so PARALLEL sessions
  never collide: each mirror pane carries `var:claude_mirror=<sid>`, each Claude pane
  carries `var:claude_session=<sid>`, and each session's content is its own state
  DB at `/tmp/claude-mirror-<sid>.log.state.db` (the `.log` path is the KEY the
  scripts pass around; no log file exists anymore). The key format itself —
  sanitizing a session id, the cwd-slug fallback, deriving/parsing the path — is
  owned by ONE stdlib-only module, **`claude_paths.py`**; it used to be encoded in
  four independently-maintained regexes (ops/audit/split/tab-status) that had
  already drifted (audit captured the full sid where `team_dir` captured 8 hex
  chars), and any two of them disagreeing silently breaks the audit join or the
  fallback DB paths. `open` (SessionStart) reads
  the `session_id` from its hook payload, sets up that session's state DB (see
  *history across resume* below), tags the Claude pane, switches the
  tab to the `splits` layout, and launches the split at `${CLAUDE_MIRROR_BIAS:-25}`
  percent, plus the **scoreboard bar** — a ~4-row `claude-scorebar.py` window hsplit
  under the mirror (`--next-to` the mirror window, then resized to exactly
  `BAR_ROWS` since kitty's `--bias` is approximate; excluded from the width math,
  which would otherwise double-count the column it shares with the mirror). It also
  fires **`claude-codex-launch.py`** (see *Codex streams* above),
  which detaches this session's codex watcher and returns immediately. `close`
  (SessionEnd) closes that session's mirror + bar and **parks** its state DB
  (`<log>.state.db*` — ops history, scoreboard, coordination state) as `*.keep`
  files, and sweeps stale debris (parked/orphaned session files older than 7
  days, pre-migration leftovers).

  **History across resume.** `--resume`/`--continue` keeps the same `session_id`,
  so `open` decides the DB's fate purely from **file existence**, never from the
  payload's `source` field (which would miss resume-after-crash):
  - `<db>.keep` exists → a prior SessionEnd parked this sid; move the DB back and
    the renderer replays the entire prior session, scoreboard included
    (**restore-history**).
  - the DB itself exists → SessionStart fired mid-session (`compact`) or the prior
    run crashed without a SessionEnd; leave it alone (**reuse-live-db**).
    (Truncating unconditionally here — the pre-DB design — wiped the live mirror
    on auto-compact.)
  - neither → a genuinely new session: nothing to do, the first writer creates the
    DB (**fresh-db**). The sid-less cwd-slug fallback removes any leftover DB
    instead — it may belong to another session.

  Why *park-and-rename* rather than simply not deleting: the DB **path** vanishing
  is the exit signal the codex watcher and the bar's renderer poll for — leaving
  it in place at SessionEnd would leak both. Each fate is audited as a
  `state_files` row (action = the fate, content = the payload's `source`), so a
  resume that came back empty is a `fresh-db` row on a `source=resume` start — a
  canned `anomalies` query.
  `toggle` closes the pane if present **without** touching the DB, so reopening re-shows
  the whole session history — and while closed there is **no process at all** (no
  resources, nothing to leak). `grow`/`shrink [N]` resize by N cells
  (default `${CLAUDE_MIRROR_STEP:-4}`); `setpct N` sets an absolute width of N%
  of the tab (the size presets) and `reset` is `setpct ${CLAUDE_MIRROR_BIAS}` —
  both computed from live tab geometry and iterated to the exact target, since
  kitty's splits layout only resizes by an inexact relative increment. `open`/`close`
  get the sid from their payload (stdin); the **keybindings have no payload, so they
  recover the sid from the currently focused kitty tab** (`os_window`+`tab` `is_focused`
  → the tab's `claude_session`/`claude_mirror` var). Wired to
  `SessionStart` (open) and `SessionEnd` (close); `toggle`/`grow`/`shrink`/
  `reset`/`setpct` are bound to keys (below). When invoked from a keybinding (a
  background `launch` that doesn't inherit `KITTY_LISTEN_ON`, runs in `$HOME`,
  and has no Claude env), the script makes itself self-sufficient: it resolves
  the kitty socket by walking its ancestor pids to the controlling `kitty`
  (whose pid names `/tmp/kitty-<pid>`, falling back to the lone socket); it runs
  with `--cwd current` so `$PWD` is the project; and it reads `CLAUDE_MIRROR_BIAS`
  / `CLAUDE_MIRROR_STEP` by merging the global `~/.claude/settings.json` with the
  project `.claude/settings*.json` (project wins) — the single source of truth,
  read in one place (`read_setting`), no value hardcoded in the script.

Behaviour & limits:
- **Foreground commands** stream live, same as background: the `▶ foreground`
  header appears immediately and output lines arrive as the command produces
  them, closing with an accurate `■ finished · Ns` (or `■ failed`/`■ interrupted`)
  once the real outcome is known — see *Live foreground streaming* above. Even
  instant commands show correctly (the block still renders in one shot when
  there's nothing to stream). Ctrl+B-backgrounding or cancelling one mid-run is
  also handled (same section).
- **Background commands** stream live: a single `▷ background` chip + the
  command, then `claude-stream.py` appends each output line (`│ ` gutter in the
  job's palette colour) as it arrives, and a matching-colour `■ background
  finished · Ns` line when the job ends — all one block, command printed once.
- **Monitor streams** show too — a `◉ monitor · <description>` header, the events
  as they fire (`│ ` gutter in the monitor's palette colour), and a matching
  `■ monitor ended · Ns` when the monitor's command process exits — exact at any
  tick cadence (seconds or hours apart), no grace. A *persistent* monitor's
  process lives until it's stopped / the session ends, so its block stays open
  until then.
- **Subagents** stream live with full visibility — the `▶ <type> · <desc>` header,
  then the subagent's **prompt** (`⇢ prompt`), its **text messages** (`✎ message`),
  its **commands** (`<type> ▶ foreground` / `▷ background`), **file ops**
  (`Read(name)` …, mutations carrying the same green `+N` / red `-M` line counts and
  touched `start-end` range, reads the same `start-end/total` extent as the main
  session, before the model tag — every file op is deferred to its result so the
  result-only extent/range is known), and its **final message / result**, then
  `■ <type> ended · Ns`
  — all in the subagent's colour, every op header tagged `<model>·<effort>` and each
  turn carrying a colour-coded `ctx N% · used/max` fill line. Several subagents in
  parallel interleave in the
  shared log but stay readable by colour. A subagent's **background command** (or
  monitor) streams with a **double gutter** (`│ │ …`): outer = the subagent's
  colour, inner = that job's own palette colour, so multiple background jobs from
  one subagent (or from different subagents) stay distinct.
- **Gutters as per-stream tags:** output lines are prefixed with a colour-coded
  `│ ` so parallel streams interleaved in the shared log stay distinguishable.
  Foreground uses one status colour (slate ok / red failed / orange interrupted);
  background, monitor, and subagents each draw from a separate 5-colour palette,
  with each running job claiming a free slot — so up to 5 concurrent of each kind
  get distinct colours. All the palette/status colours are chosen to be
  well-separated (min pairwise distance is large), so no two look alike. The finish
  chip reuses the stream's gutter colour so you can tell which one finished.
  Lines wider than the pane are **hard-wrapped** so the gutter repeats on every
  visual row (ANSI-aware — colour is re-asserted across the wrap), rather than
  soft-wrapping and losing the gutter on the continuation. **Tabs are expanded to
  spaces (8-col stops) before any width math**: the terminal advances a raw `\t`
  to the next tab stop but the wrap counters saw 1 cell, so tab-containing output
  (git diff, Makefiles, TSV) overran the pane and broke gutter alignment — and
  exact terminal tab stops are unknowable once the gutter shifts every column
  anyway, so deterministic spaces are the point.
- **Failures** show in red: a non-zero exit / tool error fires `PostToolUseFailure`
  (not `PostToolUse`), whose payload carries the combined output in an `error`
  field prefixed `Exit code N`. The block's header + finish chip turn red and the
  chip shows the code — `■ failed (exit N)`. So, unlike successes, **failed
  commands do show their exit code**.
- **Command output** is shown verbatim (real ANSI passes through and renders).
  Output that prints escape sequences as *text* — `^[`, `\033`, `\x1b`, `\e`, `<ESC>`,
  `` (e.g. from `cat -v` or `sed 's/\x1b/^[/g'`) — is **unescaped back to
  real ESC bytes** so the pane interprets them instead of showing `^[[…m`
  gibberish (`claude-cmd-fmt.py` / `claude-stream.py` `unescape()`). This covers
  **all** sequences, not just colour: a command that emits an escaped cursor-move
  or clear-screen (e.g. `^[[2J`) will have it execute in the pane.
- **Reflow on resize.** Producers write width-INDEPENDENT **paint ops** (rows in
  the state DB's `ops` table via `claude_ops.py`; one transaction per block, so
  concurrent producers' blocks never interleave — the atomicity the old JSONL
  log's single O_APPEND write gave) — `rule` / `label` / `code` / `gut` / `line`, each carrying its
  colours + pre-highlighted text but no baked width. The renderer
  (`claude-mirror.py`, running in the pane) paints them at the pane's **live** width
  (`os.get_terminal_size`, no `kitten @ ls` round-trip), and on resize the pane's
  pty delivers `SIGWINCH` → it clears and **re-renders every op** at the new width,
  so dividers, gutters, and wrapped code/output all re-fit. (Earlier the width was
  baked at write time, so resizing left old blocks frozen.) Cost: a resize
  re-renders the whole history (re-highlighting code) — fine for interactive use.
  All column accounting counts **terminal cells, not code points**
  (`claude_render.dwidth`/`dsplit`, wcwidth-style: CJK/emoji are 2 cells, combining
  marks/ZWJ/VS16 are 0) — with `len()`, any op containing wide text overran the
  pane and knocked the `│ ` gutter out of alignment on wrapped rows.
- **Tailers read exactly the bytes they measured.** Every poll-loop FILE reader
  (`claude-stream.py`, `claude-substream.py`, `claude-codex-stream.py` — the
  renderer reads ops by rowid, which has no such race)
  reads `size - pos` bytes, never an unbounded `read()`: a producer appending
  *during* the read would otherwise hand the tailer bytes past the measured
  `size`, which `pos = size` then fails to account for — so the next poll
  re-read and **duplicated** them (repeated blocks in the pane).
- **Divider** spans the pane's current width and reflows with everything else.
- **Pretty-print needs `pygments`**, and highlighting happens **in the renderer
  process** — so the interpreter running `claude-mirror.py` must be one that can
  `import pygments`. kitty often launches the pane with a `python3` that resolves
  to the bare macOS/Xcode build (no pygments), which would silently drop *all*
  highlighting (bash and embedded python, foreground and background alike — they
  all go through the renderer's `R.render`). So at startup `claude-mirror.py`
  **probes** for a pygments-capable interpreter — `$CLAUDE_MIRROR_PYTHON`, then
  `python3`, then a pyenv shim / newest `~/.pyenv/versions/*`, then
  Homebrew/local — and re-execs itself into it (`os.execv`); if none has it, it
  keeps running uncoloured (this replaced the `claude-mirror.sh` wrapper, whose
  only job was that probe). Without pygments
  the command still shows with its line structure intact, just uncoloured. (A
  change here only takes effect on a **fresh** pane — toggle the mirror off/on, as
  the running renderer keeps its interpreter.)
- **Cost**: the tab uses the `splits` layout, leaving the Claude pane at ~75%
  (or `100 − CLAUDE_MIRROR_BIAS`%).
- **Sizing & on/off — settings + keys.** The default width is `CLAUDE_MIRROR_BIAS`
  (percent, default `25`) and the resize step is `CLAUDE_MIRROR_STEP` (cells,
  default `4`). Set either in the `env` block of Claude's `settings.json` —
  **both the global `~/.claude/settings.json` and the project `.claude/`
  settings are read, with the project overriding the global** (Claude's own
  layering: `settings.local.json` > `settings.json` > global). `claude-split.py`
  resolves this in one place: it uses the value already in its environment (the
  hook path inherits Claude's merged `env`) or, when absent (the keybinding
  path), reads + merges the same files itself — that's why the keybindings pass
  `--cwd current`, so the script sees the project dir. Live controls use a
  `kitty_mod+m` leader (added to `~/.config/kitty/kitty.conf`):
  | Keys | Action |
  | --- | --- |
  | `kitty_mod+m` then `t` | toggle the mirror on/off |
  | `kitty_mod+m` then `=` / `+` | widen by `CLAUDE_MIRROR_STEP` cells |
  | `kitty_mod+m` then `-` | narrow by `CLAUDE_MIRROR_STEP` cells |
  | `kitty_mod+m` then `0` | reset to `CLAUDE_MIRROR_BIAS`% |
  | `kitty_mod+m` then `1` / `2` / `3` | size preset: 75% / 50% / 25% of the tab |

  Presets + reset use `claude-split.py setpct <N>`, which sets an absolute width:
  kitty's splits layout only resizes by a relative increment (and one unit isn't
  exactly one column), so it reads the live geometry and **iterates** toward the
  target until within a cell. The tab's total width comes from walking the
  mirror's `neighbors` chain (whose entries are **group ids** — resolved through
  the tab's `groups` map; confirmed live), summing one window per horizontal
  segment — *not* from summing every window's columns: hsplit-stacked windows
  each report the full column width, so the plain sum double-counted shared
  columns, under-reported the mirror's %, and drove `reset`/`setpct` (and the
  remembered size) far off whenever the shell side was split. (Plain sum remains
  the fallback for a kitty too old to report `neighbors`.)
- **Remembered per project.** Any resize (grow/shrink/preset/reset) records the
  resulting width %, keyed by the project's cwd, in
  `~/.claude/kitty-mirror.db` (`sizes` table — was a directory of one-number
  files, imported once and removed). On the next `SessionStart` the mirror for
  that project opens at the remembered width instead of `CLAUDE_MIRROR_BIAS` (which
  is just the fallback when a project has no saved size). So sizing is sticky across
  restarts, independently per project.
- Opened on `SessionStart`; toggle it off/on any time with the key above (or
  `./claude-split.py toggle`) — reopening re-shows the session's full history, and
  while off nothing runs. **Per session:** each Claude session has its own mirror
  (own content, own size, independent toggle), so running several sessions in
  parallel no longer makes one session's toggle close another's pane.

## Audit system (always on)

Everything above is ~20 short-lived hook processes plus detached tailers/watchers
coordinating through per-session and global SQLite state DBs (plus the few
deliberate files physics demands) — and almost every
failure used to be swallowed (`except Exception: pass`, `2>/dev/null`), so when a tab
stuck blue or a block never closed, the evidence evaporated with the processes.
**Every session is now audited into SQLite** so a bug can be chased after the fact.

- **Where:** `~/.claude/kitty-audit/audit.db` (one global DB, all sessions; override
  the dir with `CLAUDE_AUDIT_DIR`). WAL mode, so the many concurrent short-lived
  writers never block each other. Deliberately *not* under `/tmp` — session artifacts
  there are deleted at SessionEnd, and the audit must survive the session.
- **On/off:** ON by default; set `CLAUDE_AUDIT=0` (env / settings `env` block) to
  disable — every audit call becomes a no-op. The DB and spool are gitignored.
- **Never breaks a hook:** a failed DB write degrades to an append-only
  `spool.jsonl`, re-ingested on the next successful open — including failures of
  the auditor itself. The tab-status path writes fire-and-forget in the background,
  so the latency-sensitive colour path is never blocked.
- **Retention:** sessions older than 30 days are pruned at SessionEnd — every
  per-session table including `pane_events` (once omitted from the prune loops,
  which grew it unboundedly with permanently orphaned rows).

What's recorded (all tables keyed by `session_id`, written by `claude_audit.py`):

| table | one row per |
|---|---|
| `sessions` | Claude session — cwd, transcript, mirror log, window id, start/end, env. A SessionEnd that can't reach the DB spools a `session_end` pseudo-row (same mechanism as `stream_end`), so a locked DB no longer leaves the session "(open)" forever |
| `hook_events` | hook invocation — **full stdin payload** + the handler's **decision** ("ignored: agent_id", "handed off to fg tailer: ■ failed (exit 1)", …) |

`hook_events` is fed two ways. The mirror's own handlers record the events they
process, *with* the decision they took. On top of that, a **universal subscriber**
(`claude_audit.py hook subscriber`, wired **`async`** — non-blocking — into **all 30
hook events** in `~/.claude/settings.json`) records **every** event with its full
payload, `handler = 'subscriber'` — including the ones nothing else listens to:
`PermissionRequest`/`PermissionDenied`, `PostToolBatch`, `MessageDisplay`,
`TeammateIdle`, `Pre`/`PostCompact`, `ConfigChange`, `CwdChanged`, `FileChanged`,
`WorktreeCreate`/`Remove`, `Elicitation`/`ElicitationResult`, `Setup`,
`UserPromptExpansion`, `InstructionsLoaded`. So nothing that happens in a session is
invisible to the audit, and a mirror-handler row can be cross-checked against the
subscriber's independent record of the same event.
| `tab_transitions` | tab-colour decision — dispatch, prev → new, applied *or skipped*, with the **reason** (replaces the old opt-in `CLAUDE_TAB_DEBUG` flat-file logs). "Applied" is **verified against kitty**: the `kitten @` exit code is captured, so a socket call that failed records `applied=0` + a "kitten @ failed rc=N" reason instead of claiming a colour change that never happened |
| `slots` | palette/liveness-slot event (`live`-table rows) — claim / claim-id / claim-pid / steal-stale / claim-denied / release / release-id / release-pid / set-owner |
| `streams` | detached tailer/streamer/watcher lifecycle — with the **end reason** (writer-gone / sentinel / stoppedByUser / converted-ctrl-b / backstop-timeout / crash). Includes the **shell watchers** (`bg-watch`, `interrupt-watch`) — a watcher that dies mid-poll leaves an open row the `anomalies` query flags — and the codex watcher's **cross-session claims** (slots, kind `codex-claim`), so "why didn't session A show that codex run" is answerable. A streamer whose end couldn't reach the DB spools it and ingest applies it later, so it never falsely reads as "never ended" |
| `ops` | paint op written to the mirror log — full pane reconstruction, survives SessionEnd |
| `errors` | **swallowed exception — full traceback + context** (every `except: pass` site records before swallowing) |
| `spawns` | detached process launch — parent, child pid, argv, purpose |
| `state_files` | coordination-file transition — `.done` sentinels, `.fg-live`, `sub.done`, … — plus the **scoreboard sidecar's evolution**: every `bump` (deltas + resulting totals), every agent-spend bump (`bump-agent`: same, plus `meta` with agent_id/kind/model and the in/out/cache/create split `cost_usd` priced — attribution and re-pricing without timestamp correlation), every transcript-spend fold (`bump-transcript`: token/cost delta + cursor), every team-message transition (`msg-transitions`), and each substream streamer's checkpoint bookends (`resume`/`final` on `sub.pos.<agent>`: adopted vs left-behind pos + dedup state — a mismatched pair is a broken idle-restart handoff) — so a wrong scoreboard number is traceable to the exact bump that skewed it. The scorebar's per-second `paused` ticks are deliberately **not** audited (they buried real bumps ~1000:1; the running total rides every other bump row) |
| `pane_events` | mirror/scoreboard **pane operation** — open / close / toggle / resize with `ok` verified against kitty (a mirror that failed to open, or a resize that changed nothing, is recorded — the kitten calls used to be silent) |

Explore it with the CLI (from the repo root):

```sh
python3 claude_audit.py sessions            # recent sessions
python3 claude_audit.py timeline  <sid>     # merged chronological story
python3 claude_audit.py errors    <sid>     # swallowed exceptions, full tracebacks
python3 claude_audit.py anomalies <sid>     # canned queries for known bug signatures
python3 claude_audit.py sql "<query>"       # free-form SQL
python3 claude_audit.py prune [days]        # manual retention pass
```

Or just hand Claude Code a session id: the **`audit-debug` skill**
(`.claude/skills/audit-debug/SKILL.md`) walks the triage — anomalies → errors →
timeline → targeted SQL — and names the bug from the evidence: which rows, which
code path, and a suggested fix.

## Testing

The e2e suite (`tests/`, `make test`) drives the real hook scripts as
subprocesses with synthetic payloads and asserts on the three state surfaces
(session state DB, tab DB, audit DB). To run hermetically and fast it uses four
env knobs that exist **only for the test suite** — nothing sets them in a real
session, and unset they leave shipped behavior bit-identical:

| Env var | Default | Effect |
|---|---|---|
| `CLAUDE_MIRROR_TMPDIR` | `/tmp` | Relocates everything `claude_paths.py` derives: `claude-mirror-<key>.log*` state DBs/sidecars/parks **and** the global `claude-kitty-tab.db` — per-test isolation |
| `CLAUDE_TAIL_POLL_S` / `CLAUDE_TAIL_BACKSTOP_S` | `0.4` / 6 h | `claude_tail.py` poll cadence / absolute tailer cap |
| `CLAUDE_STREAM_GRACE_S` | 2 s (fg/bg) · 8 s (monitor) | `claude-stream.py` idle-grace before writer-gone is definitive |
| `CLAUDE_WATCH_POLL_S` | unset | One value replacing every `claude-tab-status.py` watcher/grace sleep (bg-watch 2 s, interrupt-watch 0.5 s, bg-recheck grace 4 s) |

Any session started with the timing knobs set is self-evident in the audit:
`session_start` captures `CLAUDE_TAIL_*`/`CLAUDE_STREAM_*`/`CLAUDE_WATCH_*`
(and all `CLAUDE_MIRROR*`) into the `sessions.env` column. The fake terminal
side is injected via the pre-existing `KITTY_KITTEN_BIN` override (a recorder
script standing in for `kitten`), so no product code special-cases tests.

## Notes / tweaking

- **`--dangerously-skip-permissions`** (the `claude` alias): permission prompts
  are skipped, so the `Notification` path into red rarely fires — **red almost
  never appears** (it's reserved for Claude asking you a permission/approval
  question). A running background job/monitor is **blue**, not red; and a "waiting
  for your input" notification resolves to **green** (your turn), so finishing a
  turn never leaves the tab red.
- Change colors by editing the `COLORS` table in `claude-tab-status.py`
  (no restart needed).
- **Debugging:** every session is audited into SQLite — see *Audit system* above.
  The old opt-in `CLAUDE_TAB_DEBUG` flat-file logs are gone; `tab_transitions`
  records every colour decision (applied and skipped, with the reason) instead.
- **Background detection is per-project, not per-session:** two Claude sessions
  in the *same* directory share the temp slug, so one's background job can tint
  the other's tab red. One session per directory (the usual case) is unaffected.
- Multiple kitty instances at once: switch `listen_on` to
  `unix:/tmp/kitty-{kitty_pid}` so each gets its own socket.
