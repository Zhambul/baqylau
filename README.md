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

### Detecting a running background command / agent / live foreground command (`stop`)

There is no Claude Code hook for "background command/agent finished," so the
`stop` dispatch detects it directly — via the command mirror's live tailer **slot
markers**. Each tailer owns a marker holding its pid, removed when it exits:
`…/<mirror-log>.slots/bg.<n>` / `monitor.<n>` for a background command/monitor
(its `claude-stream.py`), `fg.<n>` for a **live-streamed foreground command**
(`claude-cmd-pre.py` — see *Live foreground streaming* below), and
`sub.pid.<agent_id>` for a background **agent** (its `claude-substream.py`). So a
marker with a **live pid** means that job/command/agent is still running → the tab
stays **blue** (`awaiting-bg`/`executing`). (A foreground agent's `sub.pid` marker
has already been removed by `Stop` time — the turn blocked on it — so only
background agents linger.)

> Earlier this scanned `tasks/<id>.output` write-holders with `lsof`. That turned
> out to be unreliable: in current Claude Code, **foreground commands also hold a
> `tasks/<id>.output` file** while they run, so an async `bg-recheck` that happened
> to fire while a foreground command was running would mis-count it and refuse to
> clear the blue (a stuck-colour bug). Slot markers are created only by tailers, so
> they can't be fooled the same way.

There is no "background finished" hook, so the tab can't be flipped back the
instant a job ends — but it no longer has to wait for the next exchange either:
- When `claude-stream.py` finishes a job it **releases its slot marker first**,
  then calls `claude-tab-status.sh bg-recheck`, which flips a **stale `awaiting-bg`
  OR `executing`** back to green — but only if the tab is *currently* in one of
  those states (so it never overrides a working/idle/awaiting-command colour) and
  no other tailer marker is still live. (Releasing before the recheck is essential,
  or it would see its own marker.) Recognizing `executing` here (not just
  `awaiting-bg`) is what makes a **manually cancelled** foreground command flip the
  tab green promptly — cancelling fires no hook at all, but the `fg` tailer notices
  its process died (`has_writer` goes false) and calls `bg-recheck` itself.
- As a backstop for an *untracked* finished job (a tailer that died without
  rechecking), the `stop` dispatch — when it goes blue — also spawns **one detached
  `bg-watch` watcher** that polls until no live marker remains, then flips the
  stale blue green (and exits immediately if a new turn starts). One watcher per
  window, lock-guarded.

Each color-set persists the state to `/tmp/claude-tab-state-<window_id>` so
`bg-recheck`/`bg-watch` can make the "is it currently red?" decision.

### Recovering from a cancelled turn (`interrupt-watch`)

Claude Code fires **no hook at all** when a turn is cancelled/interrupted — no
`Stop`, no `StopFailure`, nothing. Every cancellation case in this doc ultimately
traces back to that one gap; what differs is how fast each case can be *noticed*:

- **Bash / background / foreground / subagent** cancellations each have a live
  process or file to poll (a tailer's writer-liveness, a subagent's `meta.json`
  `stoppedByUser`), so they self-heal in about a second — see *Live foreground
  streaming* and the subagent section above.
- **Everything else** — cancelling a plain text reply, or a non-Bash tool call
  (Read/Edit/Write/MCP) — has no such process to poll, but Claude Code *does*
  append a synthetic `[Request interrupted by user]` line to the session
  transcript the instant it happens (confirmed empirically, mirroring the
  subagent case). `claude-tab-status.sh`'s `thinking` dispatch (`UserPromptSubmit`)
  reads the payload's `transcript_path` and spawns **one detached
  `interrupt-watch` per window** that tails it for that line, polling every 0.5s —
  so this case recovers almost instantly.
  It only watches while the tab is in the magenta `thinking`/`working` phase, and
  re-checks the state right before flipping green so it never clobbers a state
  that already moved on for a legitimate reason (e.g. a tool call started).
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
  | `PreToolUse`       | `Bash` | `claude-cmd-pre.sh` (rewrites the command to stream live — see *Live foreground streaming* below) |
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
| **codex ▶ \<label\>** | **any codex run** — a companion job (`Review` / `Adversarial Review` / `Task` / `Stop Gate Review`) or a raw `codex`/`codex exec` (`cli`) — its commands (`▶ cmd`), reasoning (`⋯ reasoning`), messages (`✎ message`), prompt (`⇢ prompt`) & review/result (`⇠ review` / `⇠ result`) stream in, framed by `codex ▶ <label>` … `■ codex <label> ended` | a **fifth palette** (jade · sky · orchid · gold) so a codex stream never reads as one of our own agents |
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

- **`claude-cmd-log.sh`** (a `PostToolUse` Bash hook) is a thin wrapper that hands
  the hook payload to the formatter. It no longer needs the pane width — producers
  emit width-independent paint ops and the renderer wraps them at paint time.
- **`claude-cmd-fmt.py`** does the work — reads the payload
  (`tool_input.command`, `tool_response.stdout`/`stderr`, `duration_ms`),
  syntax-highlights the command (pygments `BashLexer` + `PythonLexer` for embedded
  python), and appends a block of **paint ops** (via `claude_ops`) to
  `/tmp/claude-mirror-<slug>.log` — the command as a `code` op, the output as a
  `gut` op, framed by `rule`/`label` ops; the renderer wraps them to the live
  width. It lives in its own file (not an inline `python3 -c '…'`) so its regexes
  can use both quote characters without bash-quoting hazards. For a **background**
  command it writes a single
  `▷ background` chip + the command and spawns the tailer below (which appends
  the live output directly under it).
- **`claude-stream.py`** (spawned detached, in its own session, by the launch
  hook) tails a background job's / monitor's `tasks/<id>.output` file — located
  by globbing the unique id — and appends each new line to the mirror log with a
  **Redirected output.** If a background command sends stdout to a file
  (`… > deploy.log 2>&1`), the task's own output file stays empty, so there's
  nothing to tail. `claude-cmd-fmt.py` parses the redirect target out of the
  command (stdout / `&>` only; skips `2>`, `/dev/*`, fd-dups; last one wins),
  resolves it against the hook's `cwd`, and passes it to the tailer via
  `CLAUDE_STREAM_SRC` — which then follows **that** file instead, so the
  redirected output streams live too. Completion detection (write-holder gone)
  works unchanged, since the job holds the redirect file open the same way.

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
- **Live foreground streaming (Ctrl+B aware).** `claude-cmd-pre.py` (`PreToolUse`
  Bash) makes a normal foreground command stream live instead of only appearing
  once it completes. It rewrites the command via `PreToolUse`'s `updatedInput`
  (undocumented but confirmed working) to also `tee` its stdout/stderr into a side
  file — `{ <cmd>; } > >(tee -a "$F") 2> >(tee -a "$F" >&2)`, or the command's own
  redirect target if it already has one — emits the `▶ foreground` header
  immediately, claims an `fg.<n>` slot (so the tab tracker sees it, above), and
  spawns `claude-stream.py fg` to tail `$F` the same way a background job is
  tailed. `claude-cmd-fmt.py`'s `PostToolUse` handler is the only place the real
  outcome (duration/exit code/interrupted) is known, so it hands that off to the
  tailer via a `.done` sentinel next to `$F` instead of re-rendering the block
  itself; if nothing ever landed in `$F` (e.g. an older Claude Code build ignoring
  `updatedInput`), the sentinel also carries the real output as a fallback so
  nothing is silently lost. The `fg` tailer gives up by **writer-liveness**, like
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
    no-hook-on-interrupt gap noted throughout this doc), so `claude-cmd-fmt.py`'s normal cleanup of
    the `.fg-live` marker never runs. Left alone, that stale marker would make
    `claude-cmd-pre.py` think a live block is *already* in flight forever, and
    silently skip wrapping every later command (the mirror would just stop
    showing anything new). The marker now stores the tailer's pid, and
    `claude-cmd-pre.py` liveness-checks it (`os.kill(pid, 0)`, the same pattern
    `claude_slots` uses for stale slots) before treating an existing marker as
    genuinely in-flight — a dead pid means abandoned, so it's cleared and the next
    command streams normally.
  - Escape hatch: `CLAUDE_MIRROR_LIVE_FG=0` disables the command rewrite entirely
    if it ever misbehaves on some pathological command's quoting.
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
    every `tool_use` block. The rollup is appended last, so on a narrow pane it's the
    first thing the renderer's `fit()` truncates — duration and ctx always survive.
  - **Cost estimate in the footer.** After the rollup the footer appends `· ≈ $X`,
    the summed tokens priced on the resolved model (`claude_ops.PRICES`, per-MTok
    input/output for the current lineup; `cache_read` billed at ~0.1×). An unknown
    model shows nothing rather than guess. Being last, it truncates before the rest.
  - **Session scoreboard (its own window).** A running "so far" summary of the whole
    session, aggregated across the separate hook processes in a sidecar
    `…/<mirror-log>.stats.json` (each producer bumps its deltas under an `flock`;
    removed with the log at SessionEnd). **`claude-scorebar.py`** renders it in a
    **dedicated ~4-row window hsplit under the mirror** (`var:claude_scorebar=<sid>`,
    opened/closed with the mirror by `claude-split.sh`) — an always-on session-id
    line, a team-message census, then the session summary:

    ```
    ⬡ 95466f49-240b-4b69-92b4-96bd1541a9a9
    ✉ 5 msgs · 1● unread · 2◐ stale · 1◉ read
    ▪ 45 cmds (5✗) · 56 files · +791 -29 · 1.2M tok · ⏱ 68m24s · ≈ $1.20
      Read 34 · Edit 18 · Write 4
    ```

    The **`⬡` session-id row** is always shown (parsed from the mirror-log filename),
    so a pane is identifiable at a glance. The **`✉` message census** gives live
    visibility into the agent-team message flow and is **always shown** (defaults to
    `0 msgs`, even for a non-team session). It comes from `claude_ops.update_messages()`,
    which — since there is **no hook** for a message being read/consumed — tracks state
    by **stateful polling**: each tick it diffs the team inboxes against a persisted
    sidecar `…/<mirror-log>.msgs.json` (keyed by `msg_id`) and folds transitions into
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
    window's persisted tab state (`/tmp/claude-tab-state-<win>`), and accumulates
    green ticks into the sidecar's `paused` field (same flock'd `bump()`, so it
    survives a mirror toggle); `scoreboard_parts()` subtracts it from the elapsed
    time. It truncates from the tail on narrow panes (cost goes last,
    so it drops first), and **exits when the mirror log disappears** at SessionEnd,
    auto-closing its window (`claude-split.sh close` is the safety net). The
    structured data comes from `claude_ops.scoreboard_parts()`. The tools row
    **excludes Bash** — its count is already the `cmds` figure (same bump; listing it
    again would just duplicate the head). `files` counts **unique files** (touched
    paths are deduped in the sidecar's `file_set`; re-editing the same file doesn't
    inflate it) while the tools row still counts operations — so `Edit 18` against
    `5 files` reads as 18 edits across 5 distinct files.
  - **Tokens + cost cover the whole session.** `tok` sums fresh billed input
    (`input + cache_creation`) plus generated output — cache reads are replay, not
    spend, so they're excluded. Two producers feed it: each **agent's** streamer
    bumps its totals when the run ends, and the **main session's own turns** are
    folded in by `claude_ops.bump_transcript()` — called from the cmd/file hooks, it
    reads the session transcript JSONL forward from a cursor kept in the sidecar
    (`txpos`), sums each new assistant turn's usage (skipping sidechain records —
    their own streamer already counts them), and advances the cursor under the same
    `flock` so concurrent hooks never double-count. (Before this, cost only moved
    when an agent run ended and sat "stuck" through plain main-session work.)
  - **Pricing** (`claude_ops.PRICES`, verified against the published 2026-06 list):
    Fable/Mythos 10/50 · Opus 4.6-4.8 5/25 · Sonnet 3/15 · Haiku 4.5 1/5 · legacy
    Opus 4.1/4.0/3 15/75 per MTok in/out; cache reads bill 0.1× input and cache
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
  - **`claude-subagent-log.sh`** + **`claude-subagent-fmt.py`** drive the frame:
    `SubagentStart` claims the colour slot (keyed by `agent_id` so header, body,
    and footer match; parallel subagents differ), writes the `▶ <type> · <desc>`
    header, and launches the streamer; `SubagentStop` writes a sentinel the
    streamer watches for (its authoritative end signal for a **normal finish** —
    *not* `meta.json`, which is written at subagent **start**). The streamer is
    the **sole footer writer**; `SubagentStop` only closes the block itself as a
    safety net, and **only when a colour slot is still claimed** (the streamer
    died mid-run without finalising).
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
- **Codex streams (global — EVERY codex call).** The mirror shows any codex run,
  however it was launched — a `/codex:review`, an adversarial-review, a `task`, the
  stop-gate, or a **raw `codex` / `codex exec`** in a shell; fired by the **main
  agent, a subagent, an agent-team teammate, a foreground OR background command, or a
  slash subcommand**. Rather than detect the codex *command* at every launch site, a
  per-session watcher tails **two directories** every codex run funnels through, and
  spawns a streamer per run. Nothing is wired per-launcher; new codex entry points are
  covered for free.
  - **`claude-codex-launch.py` → `claude-codex-watch.py`.** `claude-split.sh open`
    (SessionStart) runs the tiny **launcher**, whose only job is to `Popen` the watcher
    with `start_new_session=True` and exit in a few ms. This is load-bearing: launching
    the long-lived watcher from the hook with a bash `&` left it in the **hook's process
    group**, which Claude Code waits to drain — so SessionStart hung ("no answer") and
    the watcher orphaned. Detaching it into its own session (the same way the other
    streamers are spawned) makes the hook return instantly. The watcher exits on its own
    when the session's mirror log is removed at SessionEnd; a pid lock
    (`codex.watch.pid`) guards against a duplicate SessionStart.
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
    once, with its nicer label, never twice.
  - **`claude-codex-stream.py`** renders both sources into the codex palette (colour
    picked round-robin by the watcher and passed as `r,g,b`; it keeps no slot marker, so
    it never affects the tab colour): `▶ cmd` (syntax-highlighted), `⋯ reasoning`,
    `✎ message`, `⇠ review` / `⇠ result`, framed by a rule-bracketed `codex ▶ <label>`
    … `■ codex <label> ended · Ns`. Successful sub-commands are suppressed; a non-zero
    exit shows a red `■ exit N`.
  - **Session/cwd-attributed, not nested.** A codex run is keyed to the Claude
    `sessionId` (source A) or the repo `cwd` (source B), not the launching `agent_id`,
    so it reads as its own **top-level** stream rather than nested under the teammate
    that launched it — the deliberate trade for a global, zero-per-launcher design. (Two
    Claude sessions in the same repo both show a source-B run, the same per-project
    caveat as background-job detection.)
- **`claude-mirror.sh LOG`** runs inside the pane and execs the renderer
  **`claude-mirror.py`** on that session's log (replacing the old `tail -F`),
  choosing a `pygments`-capable interpreter so command highlighting works (see
  *Pretty-print needs pygments* below). The
  renderer reads the structured paint-op log (JSONL, see *Reflow* below), paints each
  op at the pane's **current** width, and re-renders everything on resize (`SIGWINCH`)
  so content **reflows**. It reads the log from the top and **never truncates** — so
  toggling the pane off/on re-shows the whole session history (the log is truncated
  once at SessionStart, removed at SessionEnd), and while off there is no process at
  all. It keeps at most `MAX_OPS` (8000) ops in memory so a long session can't grow
  unbounded. One process — no file-switching, byte-offsets, `lsof`, or orphaned tails.
- **`claude-file-log.sh`** (a `PostToolUse` hook for `Read`/`Edit`/`Write`/
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
- **`claude-split.sh open|close|toggle|grow|shrink|reset|setpct`** manages the pane,
  **per Claude session**. Everything is keyed by `session_id` so PARALLEL sessions
  never collide: each mirror pane carries `var:claude_mirror=<sid>`, each Claude pane
  carries `var:claude_session=<sid>`, and each session's content is its own
  `/tmp/claude-mirror-<sid>.log`. `open` (SessionStart) reads the `session_id` from
  its hook payload, truncates that session's log, tags the Claude pane, switches the
  tab to the `splits` layout, and launches the split at `${CLAUDE_MIRROR_BIAS:-25}`
  percent, plus the **scoreboard bar** — a ~4-row `claude-scorebar.py` window hsplit
  under the mirror (`--next-to` the mirror window, then resized to exactly
  `BAR_ROWS` since kitty's `--bias` is approximate; excluded from the width math,
  which would otherwise double-count the column it shares with the mirror). It also
  fires **`claude-codex-launch.py`** (see *Codex streams* above),
  which detaches this session's codex watcher and returns immediately. `close`
  (SessionEnd) closes that session's mirror + bar and removes its log — which is also
  what stops the watcher (and the bar's renderer, which exits when the log vanishes).
  `toggle` closes the pane if present **without** truncating, so reopening re-shows
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
- **Reflow on resize.** Producers write width-INDEPENDENT **paint ops** (JSONL via
  `claude_ops.py`) — `rule` / `label` / `code` / `gut` / `line`, each carrying its
  colours + pre-highlighted text but no baked width. The renderer
  (`claude-mirror.py`, running in the pane) paints them at the pane's **live** width
  (`os.get_terminal_size`, no `kitten @ ls` round-trip), and on resize the pane's
  pty delivers `SIGWINCH` → it clears and **re-renders every op** at the new width,
  so dividers, gutters, and wrapped code/output all re-fit. (Earlier the width was
  baked at write time, so resizing left old blocks frozen.) Cost: a resize
  re-renders the whole history (re-highlighting code) — fine for interactive use.
- **Divider** spans the pane's current width and reflows with everything else.
- **Pretty-print needs `pygments`**, and highlighting happens **in the renderer
  process** — so the interpreter `claude-mirror.sh` execs must be one that can
  `import pygments`. kitty often launches the pane with a `python3` that resolves
  to the bare macOS/Xcode build (no pygments), which would silently drop *all*
  highlighting (bash and embedded python, foreground and background alike — they
  all go through the renderer's `R.render`). So `claude-mirror.sh` **probes** for a
  pygments-capable interpreter — `$CLAUDE_MIRROR_PYTHON`, then `python3`, then a
  pyenv shim / newest `~/.pyenv/versions/*`, then Homebrew/local — and falls back
  to plain `python3` (still runs, just uncoloured) if none has it. Without pygments
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
  | `kitty_mod+m` then `1` / `2` / `3` | size preset: 75% / 50% / 25% of the tab |

  Presets + reset use `claude-split.sh setpct <N>`, which sets an absolute width:
  kitty's splits layout only resizes by a relative increment (and one unit isn't
  exactly one column), so it reads the live geometry and **iterates** toward the
  target until within a cell.
- **Remembered per project.** Any resize (grow/shrink/preset/reset) records the
  resulting width %, keyed by the project's cwd, under
  `~/.claude/kitty-mirror-sizes/<slug>`. On the next `SessionStart` the mirror for
  that project opens at the remembered width instead of `CLAUDE_MIRROR_BIAS` (which
  is just the fallback when a project has no saved size). So sizing is sticky across
  restarts, independently per project.
- Opened on `SessionStart`; toggle it off/on any time with the key above (or
  `./claude-split.sh toggle`) — reopening re-shows the session's full history, and
  while off nothing runs. **Per session:** each Claude session has its own mirror
  (own content, own size, independent toggle), so running several sessions in
  parallel no longer makes one session's toggle close another's pane.

## Audit system (always on)

Everything above is ~20 short-lived hook processes plus detached tailers/watchers
coordinating through `/tmp` marker files, sidecars, and sentinels — and almost every
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
- **Retention:** sessions older than 30 days are pruned at SessionEnd.

What's recorded (all tables keyed by `session_id`, written by `claude_audit.py`):

| table | one row per |
|---|---|
| `sessions` | Claude session — cwd, transcript, mirror log, window id, start/end, env |
| `hook_events` | hook invocation — **full stdin payload** + the handler's **decision** ("ignored: agent_id", "handed off to fg tailer: ■ failed (exit 1)", …) |
| `tab_transitions` | tab-colour decision — dispatch, prev → new, applied *or skipped*, with the **reason** (replaces the old opt-in `CLAUDE_TAB_DEBUG` flat-file logs) |
| `slots` | marker-file event — claim / claim-id / steal-stale / claim-denied / release / set-owner |
| `streams` | detached tailer/streamer/watcher lifecycle — with the **end reason** (writer-gone / sentinel / stoppedByUser / converted-ctrl-b / backstop-timeout / crash) |
| `ops` | paint op written to the mirror log — full pane reconstruction, survives SessionEnd |
| `errors` | **swallowed exception — full traceback + context** (every `except: pass` site records before swallowing) |
| `spawns` | detached process launch — parent, child pid, argv, purpose |
| `state_files` | coordination-file transition — `.done` sentinels, `.fg-live`, `sub.done`, … |

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

## Notes / tweaking

- **`--dangerously-skip-permissions`** (the `claude` alias): permission prompts
  are skipped, so the `Notification` path into red rarely fires — **red almost
  never appears** (it's reserved for Claude asking you a permission/approval
  question). A running background job/monitor is **blue**, not red; and a "waiting
  for your input" notification resolves to **green** (your turn), so finishing a
  turn never leaves the tab red.
- Change colors by editing the `set_color` lines in `claude-tab-status.sh`
  (no restart needed).
- **Debugging:** every session is audited into SQLite — see *Audit system* above.
  The old opt-in `CLAUDE_TAB_DEBUG` flat-file logs are gone; `tab_transitions`
  records every colour decision (applied and skipped, with the reason) instead.
- **Background detection is per-project, not per-session:** two Claude sessions
  in the *same* directory share the temp slug, so one's background job can tint
  the other's tab red. One session per directory (the usual case) is unaffected.
- Multiple kitty instances at once: switch `listen_on` to
  `unix:/tmp/kitty-{kitty_pid}` so each gets its own socket.
