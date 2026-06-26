# kitty tab colors for Claude Code

Makes the **kitty tab color** reflect what Claude Code is doing, so you can tell
a session's state at a glance — even from another tab.

| Tab color | State | Fires on |
|-----------|-------|----------|
| ⬜ grey `#5c6370`    | **idle** — session ready, nothing running                  | `SessionStart` |
| 🟪 magenta `#c678dd` | **busy** — thinking / non-shell tool (Read/Edit/Write/MCP) / writing the reply (merged — no signal tells them apart) | `UserPromptSubmit`, `PreToolUse` (main-agent non-Bash), `PostToolUse` (main agent) |
| 🟦 blue `#61afef`    | **the main session is running / awaiting** — a foreground shell command (`executing`), or the main session **awaiting an agent** (a foreground subagent/teammate keeps the turn blocked → blue; a background one → `awaiting-bg`) or a background command / monitor (`awaiting-bg`) | `PreToolUse` Bash/Task/Agent · `Stop` w/ a bg job/monitor/agent running |
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

`claude-tab-status.sh <state>` calls kitty remote control:

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
  `awaiting-bg` (a live `sub.pid.*` marker).
- **`stop`** — `awaiting-response` (green) normally, but `awaiting-bg`
  (**blue** — the main session is awaiting that job, not you) if a background command /
  monitor / **agent** this session launched is still running.
- **`notify`** — reads the Notification message: a permission/approval prompt →
  `awaiting-command` (red — Claude is asking you); anything else → green.

### Detecting a running background command / agent (`stop`)

There is no Claude Code hook for "background command/agent finished," so the
`stop` dispatch detects it directly — via the command mirror's live tailer **slot
markers**. Each tailer owns a marker holding its pid, removed when it exits:
`…/<mirror-log>.slots/bg.<n>` / `monitor.<n>` for a background command/monitor
(its `claude-stream.py`), and `sub.pid.<agent_id>` for a background **agent** (its
`claude-substream.py`). So a marker with a **live pid** means that job/agent is
still running → the tab stays **blue** (`awaiting-bg`). (A foreground agent's
`sub.pid` marker has already been removed by `Stop` time — the turn blocked on
it — so only background agents linger.)

> Earlier this scanned `tasks/<id>.output` write-holders with `lsof`. That turned
> out to be unreliable: in current Claude Code, **foreground commands also hold a
> `tasks/<id>.output` file** while they run, so an async `bg-recheck` that happened
> to fire while a foreground command was running would mis-count it and refuse to
> clear the blue (a stuck-colour bug). Slot markers are created only by background/
> monitor tailers — never by foreground commands — so they can't be fooled.

There is no "background finished" hook, so the tab can't be flipped back the
instant a job ends — but it no longer has to wait for the next exchange either:
- When `claude-stream.py` finishes a job it **releases its slot marker first**,
  then calls `claude-tab-status.sh bg-recheck`, which flips the **stale blue**
  (`awaiting-bg`) back to green — but only if the tab is *currently* in that state
  (so it never overrides a working/idle/executing colour) and no other tailer
  marker is still live. (Releasing before the recheck is essential, or it would
  see its own marker.)
- As a backstop for an *untracked* finished job (a tailer that died without
  rechecking), the `stop` dispatch — when it goes blue — also spawns **one detached
  `bg-watch` watcher** that polls until no live marker remains, then flips the
  stale blue green (and exits immediately if a new turn starts). One watcher per
  window, lock-guarded.

Each color-set persists the state to `/tmp/claude-tab-state-<window_id>` so
`bg-recheck`/`bg-watch` can make the "is it currently red?" decision.

## Wiring

- **`~/.config/kitty/kitty.conf`** (appended at the end):
  ```
  allow_remote_control yes
  listen_on unix:/tmp/kitty
  ```
- **`~/.claude/settings.json`** — a `hooks` block:

  | Hook | Matcher | Runs |
  |------|---------|------|
  | `SessionStart`     | —      | `claude-tab-status.sh idle` + `claude-split.sh open` |
  | `UserPromptSubmit` | —      | `claude-tab-status.sh thinking` |
  | `PreToolUse`       | `.*`   | `claude-tab-status.sh pretool` |
  | `PreToolUse`       | `Task\|Agent` | `claude-subagent-log.sh push` (stashes the Task description for the upcoming `SubagentStart` header) |
  | `PostToolUse`      | `.*`   | `claude-tab-status.sh posttool` (ignored if the event carries an `agent_id` — a subagent/teammate inner call — else magenta) |
  | `PostToolUse`      | `Bash` | `claude-cmd-log.sh` (writes command + output + elapsed to the mirror log) |
  | `PostToolUse`      | `Read\|Edit\|Write\|MultiEdit\|NotebookEdit` | `claude-file-log.sh` (writes a one-line `Read(name)`/`Update(name)`/`Write(name)` to the mirror log) |
  | `PostToolUse`      | `Monitor` | `claude-monitor-log.sh` (monitor header + spawns `claude-stream.py` to tail the event stream) |
  | `PostToolUseFailure` | `.*` / `Bash` / `Read\|Edit\|…` / `Monitor` | same handlers as `PostToolUse` — a tool that **fails** (e.g. a non-zero-exit command) fires this event, *not* `PostToolUse`, so it must be wired too or failures never reach the mirror |
  | `SubagentStart`    | —      | `claude-subagent-log.sh start` (subagent header `▶ <type> · <desc>` + claims its colour slot; in-process **agent-team teammates** arrive here too) |
  | `SubagentStop`     | —      | `claude-subagent-log.sh stop` (subagent footer `■ <type> ended · Ns` + releases the slot) |
  | `TaskCreated`      | —      | `claude-task-log.sh` (agent-team shared task list: writes `✚ task #N · <subject>` to the mirror) |
  | `TaskCompleted`    | —      | `claude-task-log.sh` (writes `✓ task #N · <subject>` to the mirror) |
  | `Notification`     | —      | `claude-tab-status.sh notify` (reads the message: a permission/approval prompt → red `awaiting-command`; a "waiting for your input" notice → green `awaiting-response`, since that's just your turn) |
  | `Stop`             | —      | `claude-tab-status.sh stop` |
  | `StopFailure`      | —      | `claude-tab-status.sh stop` (turn ended on an API error — keep the tab from getting stuck on the "busy" colour) |
  | `SessionEnd`       | —      | `claude-tab-status.sh clear` + `claude-split.sh close` |

  Agent-team support also needs the experimental feature itself enabled, via an
  `env` entry in the same `settings.json` (read at session start):
  ```json
  "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" }
  ```

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
  ./claude-tab-status.sh "$s"; ping -c 4 127.0.0.1 >/dev/null   # ~3s each
done
./claude-tab-status.sh clear
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
| **✚ / ✓ task #N** | an agent-team **shared-task-list** event — created (`✚`, amber) / completed (`✓`, green) | a fixed one-line accent (not a stream) |

Output lines carry a **colour-coded `│ ` gutter** — a per-**stream** tag — so
several jobs running in parallel stay distinguishable when they interleave in the
shared log. Foreground uses one status colour; background, monitor, and subagents
each draw from their own 5-colour palette (so up to 5 concurrent jobs of each kind
— 15 streams plus foreground — get distinct colours; beyond 5 of a kind, colours
reuse). When a background/monitor job launches, its hook **claims a free palette
slot** (an atomic marker file, liveness-checked by pid, released when the streamer
exits), colours the header chip with it, and hands the slot to the streamer for
the gutter + finish — so the whole block is one colour and concurrent jobs of the
same kind never collide. A subagent claims its slot by **`agent_id`** (so its
`SubagentStart` header, its streamer's body, and its footer all share one colour).
A subagent's nested background/monitor job carries **two** gutters — the subagent's
colour outside, the job's own palette colour inside. The colours across all four
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

**Foreground vs background output.** A *foreground* command's output is never
written to any file an outside process can read — Claude Code streams it back
through a private pipe, so the only place it (and the exact duration) is
available is the **`PostToolUse` hook payload**, which fires when the command
completes. A *background* command (and a Monitor stream) is the opposite: the
hook fires at *launch* with no output, but the live output **is** written to a
`tasks/<id>.output` file. So the mirror uses both routes — the hook for the
foreground block, and a detached tailer for background/monitor streams (below).
The mirror is driven by the hook:

- **`claude-cmd-log.sh`** (a `PostToolUse` Bash hook) is a thin wrapper: it
  finds the live mirror-pane width (pipes `kitten @ ls` through
  **`claude-mirror-width.py`**, fallback 53) and hands the hook payload to the
  formatter.
- **`claude-cmd-fmt.py`** does the work — reads the payload
  (`tool_input.command`, `tool_response.stdout`/`stderr`, `duration_ms`), pretty-
  prints + syntax-highlights the command (pygments `BashLexer` + `PythonLexer`
  for embedded python), word-wraps to the given width, and appends the formatted
  block to `/tmp/claude-mirror-<slug>.log`. It lives in its own file (not an
  inline `python3 -c '…'`) so its regexes can use both quote characters without
  bash-quoting hazards. For a **background** command it writes a single
  `▷ background` chip + the command and spawns the tailer below (which appends
  the live output directly under it).
- **`claude-stream.py`** (spawned detached, in its own session, by the launch
  hook) tails a background job's / monitor's `tasks/<id>.output` file — located
  by globbing the unique id — and appends each new line to the mirror log with a
  `│ ` gutter coloured from its kind's palette slot, then writes a
  `■ background finished · Ns` / `■ monitor ended · Ns` line when done. **Completion
  is detected differently per kind** (there is no hook for it — `TaskCompleted`
  is for the TodoWrite list, not background commands):
  - **background** — the command holds its output file open the whole time, so
    the write-holder vanishing (`lsof`) is a definitive signal (works even for a
    long silent `sleep 3600; echo done`). The tailer only *reads*, so it never
    counts itself.
  - **monitor** — writes its file in bursts with gaps (no held handle), so the
    write-holder trick fails. Instead the tailer tracks the monitor's **command
    process**: a monitor runs as `zsh -c … eval '<command>'`, a persistent process
    whose argv contains the command. The launcher passes a distinctive token from
    the command; the tailer finds that process (`ps`) and watches it — it exits
    exactly when the monitor ends, so completion is exact at **any cadence** (1s
    or 1h between ticks) with no grace/idle guess. A short idle fallback only
    applies if the process can't be found.
- **`claude-monitor-log.sh`** + **`claude-monitor-fmt.py`** (a `PostToolUse` hook
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
    kind keyword), file ops (`Read(name)` …), other tools, and the subagent's
    **returned result** — its final message, labelled `<type> ⇠ result` to set it
    apart from intermediate `✎ message` chatter — then the `■ <type> ended · Ns`
    footer. All in the subagent's colour. (Messages are committed one event late
    so the last one can be tagged `⇠ result`.)
  - **`claude-subagent-log.sh`** + **`claude-subagent-fmt.py`** drive the frame:
    `SubagentStart` claims the colour slot (keyed by `agent_id` so header, body,
    and footer match; parallel subagents differ), writes the `▶ <type> · <desc>`
    header, and launches the streamer; `SubagentStop` writes a sentinel the
    streamer watches for (its authoritative end signal — *not* `meta.json`, which
    is written at subagent **start**). The streamer is the **sole footer writer**;
    `SubagentStop` only closes the block itself as a safety net, and **only when a
    colour slot is still claimed** (the streamer died mid-run without finalising).
    A background agent's `SubagentStop` can fire **more than once** ("may notify
    more than once") — after the first, the streamer has finalised and freed its
    slot, so the duplicate finds no slot and does nothing. (Without that guard a
    duplicate stop printed a spurious slot-0 indigo `■ agent ended`.)
  - **Nested background / monitor → double gutter.** When a subagent launches a
    `run_in_background` command (or a Monitor), the streamer extracts the task id
    from the tool_result and spawns `claude-stream.py` with the subagent's colour
    as an *outer* gutter on top of the job's own palette-slot *inner* gutter
    (`│ │ …`). So a subagent's several background jobs share its outer colour but
    differ by inner colour, and stay distinct from other subagents' jobs.
  - The **description** isn't in the `SubagentStart` payload, and the on-disk
    `agent-<id>.meta.json` that has it is written at subagent *start* with no end
    marker (so it can't signal completion). So a `PreToolUse` hook on the
    Task/Agent tool (`claude-subagent-log.sh push`) stashes the description in a
    tiny FIFO and the next `SubagentStart` pops it — exact for sequential
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
    `TaskCompleted` hooks (`claude-task-log.sh` → `claude-task-fmt.py`) as a compact
    `✚ task #N · <subject>` (amber) / `✓ task #N · <subject>` (green) line — there
    is no readable per-task file on disk, so the hook payload is the source. (The
    payload fields are `task_id` + `task_subject` + `task_description`, *not* the
    `task_title`/`task_status` the docs list.)
  - Out of scope: **split-pane** teammate mode (tmux/iTerm2) runs each teammate as
    its own process/session rather than an in-process subagent, so it wouldn't flow
    through these hooks; the default in-process mode is what's supported here.
- **`claude-pane-width.sh`** prints the mirror pane's current column width
  (`kitten @ ls` → `claude-mirror-width.py`, fallback 53); shared by the Bash and
  Monitor hook wrappers.
- **`claude-mirror.sh [slug]`** runs inside the pane and simply `tail -F`s that
  log (truncating it on open for a fresh start). One `tail` — no file-switching,
  byte-offsets, `lsof`, birth-time selection, or orphaned tails (all of which
  earlier designs needed and which caused interleaving/stale-replay bugs).
- **`claude-file-log.sh`** (a `PostToolUse` hook for `Read`/`Edit`/`Write`/
  `MultiEdit`/`NotebookEdit`) logs file operations as compact one-liners showing
  just the verb + basename — `Read(README.md)`, `Update(README.md)`,
  `Write(new.py)` — interleaved with the command blocks so the pane reads as a
  running log of what Claude did. Verbs mirror Claude Code's own UI (Edit/
  MultiEdit → **Update**, colour-coded: read blue, update yellow, write green);
  formatting lives in **`claude-file-fmt.py`**.
- **`claude-split.sh open|close|toggle|grow|shrink|reset`** manages the pane:
  `open` switches the tab to the `splits` layout and launches the split at
  `${CLAUDE_MIRROR_BIAS:-25}` percent
  (`kitten @ launch --location=vsplit --bias … --keep-focus`), tagged with the
  window var `claude_mirror=1` so it is reused, never duplicated. `toggle`
  closes it if present else opens it; `grow`/`shrink [N]` resize by N cells
  (default `${CLAUDE_MIRROR_STEP:-4}`); `reset` restores it to the configured
  `${CLAUDE_MIRROR_BIAS}`% width (computed from the live tab geometry, since
  kitty's own `--axis reset` only snaps to a 50/50 split). Wired to
  `SessionStart` (open) and `SessionEnd` (close); `toggle`/`grow`/`shrink`/
  `reset` are bound to keys (below). When invoked from a keybinding (a
  background `launch` that doesn't inherit `KITTY_LISTEN_ON`, runs in `$HOME`,
  and has no Claude env), the script makes itself self-sufficient: it resolves
  the kitty socket by walking its ancestor pids to the controlling `kitty`
  (whose pid names `/tmp/kitty-<pid>`, falling back to the lone socket); it runs
  with `--cwd current` so `$PWD` is the project; and it reads `CLAUDE_MIRROR_BIAS`
  / `CLAUDE_MIRROR_STEP` by merging the global `~/.claude/settings.json` with the
  project `.claude/settings*.json` (project wins) — the single source of truth,
  read in one place (`read_setting`), no value hardcoded in the script.

Behaviour & limits:
- **Foreground commands** show in full (output + accurate elapsed). The block
  appears when the command **completes**, not live — foreground output doesn't
  exist anywhere until then. Even instant commands show (the hook fires
  regardless of speed).
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
  (`Read(name)` …), and its **final message / result**, then `■ <type> ended · Ns`
  — all in the subagent's colour. Several subagents in parallel interleave in the
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
  soft-wrapping and losing the gutter on the continuation.
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
- **Divider** spans the pane's current width (read live from `kitten @ ls`),
  matching the command's wrap width so nothing wraps to a second line.
- **Pretty-print needs `pygments`** (already present); without it the command
  still shows with its line structure intact, just uncoloured.
- **Cost**: the tab uses the `splits` layout, leaving the Claude pane at ~75%
  (or `100 − CLAUDE_MIRROR_BIAS`%).
- **Sizing & on/off — settings + keys.** The default width is `CLAUDE_MIRROR_BIAS`
  (percent, default `25`) and the resize step is `CLAUDE_MIRROR_STEP` (cells,
  default `4`). Set either in the `env` block of Claude's `settings.json` —
  **both the global `~/.claude/settings.json` and the project `.claude/`
  settings are read, with the project overriding the global** (Claude's own
  layering: `settings.local.json` > `settings.json` > global). `claude-split.sh`
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
- Opened on `SessionStart`; if you close it, it reopens next session. Toggle it
  yourself any time with the key above (or `./claude-split.sh open` / `close` /
  `toggle`).

## Notes / tweaking

- **`--dangerously-skip-permissions`** (the `claude` alias): permission prompts
  are skipped, so the `Notification` path into red rarely fires — **red almost
  never appears** (it's reserved for Claude asking you a permission/approval
  question). A running background job/monitor is **blue**, not red; and a "waiting
  for your input" notification resolves to **green** (your turn), so finishing a
  turn never leaves the tab red.
- Change colors by editing the `set_color` lines in `claude-tab-status.sh`
  (no restart needed).
- **Debug log:** off by default. Run with `CLAUDE_TAB_DEBUG=1` to append every
  invocation to `claude-tab-status.log` and confirm which hooks fire.
- **Background detection is per-project, not per-session:** two Claude sessions
  in the *same* directory share the temp slug, so one's background job can tint
  the other's tab red. One session per directory (the usual case) is unaffected.
- Multiple kitty instances at once: switch `listen_on` to
  `unix:/tmp/kitty-{kitty_pid}` so each gets its own socket.
