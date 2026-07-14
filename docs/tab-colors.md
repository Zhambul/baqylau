# Tab colors

How the kitty tab colour tracks the Claude Code session state.
Entry: `claude-tab-status.py` / the tab dispatch in `plugins/claude_code/tabstatus.py`.

# kitty tab colors for Claude Code

Makes the **kitty tab color** reflect what Claude Code is doing, so you can tell
a session's state at a glance — even from another tab.

| Tab color | State | Fires on |
|-----------|-------|----------|
| ⬜ grey `#5c6370`    | **idle** — session ready, nothing running                  | `SessionStart` |
| 🟪 magenta `#c678dd` | **busy** — thinking / non-shell tool (Read/Edit/Write/MCP) / writing the reply (merged — no signal tells them apart) / compacting the transcript | `UserPromptSubmit`, `PreToolUse` (main-agent non-Bash), `PostToolUse` (main agent), `PreCompact` |
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
command** (`claude-cmd-pre.py` — see [streaming.md](streaming.md) › *Live foreground streaming*), and
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
  `stoppedByUser`), so they self-heal in about a second — see [streaming.md](streaming.md) › *Live
  foreground streaming* and [subagents.md](subagents.md).
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


## Notes / tweaking

- **`--dangerously-skip-permissions`** (the `claude` alias): permission prompts
  are skipped, so the `Notification` path into red rarely fires — **red almost
  never appears** (it's reserved for Claude asking you a permission/approval
  question). A running background job/monitor is **blue**, not red; and a "waiting
  for your input" notification resolves to **green** (your turn), so finishing a
  turn never leaves the tab red.
- Change colors by editing the `COLORS` table in `claude-tab-status.py`
  (no restart needed).
- **Debugging:** every session is audited into SQLite — see [audit.md](audit.md).
  The old opt-in `CLAUDE_TAB_DEBUG` flat-file logs are gone; `tab_transitions`
  records every colour decision (applied and skipped, with the reason) instead.
- **Background detection is per-project, not per-session:** two Claude sessions
  in the *same* directory share the temp slug, so one's background job can tint
  the other's tab red. One session per directory (the usual case) is unaffected.
- Multiple kitty instances at once: switch `listen_on` to
  `unix:/tmp/kitty-{kitty_pid}` so each gets its own socket.
