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

**The paint itself no longer spawns that `kitten` subprocess on the happy
path.** The block above is the *semantic* contract; the wire is
`frontends/kitty.py set_tab_color`, which writes the equivalent `@kitty-cmd`
DCS straight to the `$KITTY_LISTEN_ON` unix socket (`_rc_raw` — the same raw
path get-text and the mirror's freeze-bracket scroll already use). Measured:
~0.1 ms per paint vs ~20-100 ms for the subprocess spawn — and the paint runs
on the **blocking** hook path several times per turn. Two things are
deliberate, not incidental:

- **The raw exchange requests and reads kitty's response** (`no_response:
  false`, `{"ok": …}` reply) and maps it to the historical exit-code contract
  (`ok` → 0). Fire-and-forget would have reported success optimistically, and
  the tab DB row is persisted **only on rc == 0** — an optimistic 0 would
  reintroduce the stranded-colour dedup bug below. In the payload, colours
  travel as 24-bit RGB integers and `NONE` as JSON `null` (captured live from
  the real kitten client; `frontends/kitty.py _tab_color_int`).
- **The `kitten` subprocess survives as the fallback**, taken only when the
  raw exchange yields *no answer* (no socket, connect timeout, garbled reply).
  A definitive `ok: false` from kitty is the answer — rc 1, no slow retry.

Besides literal states, hooks pass these **dispatch modes**:

Each mode is one row of `plugins/claude_code/tabstatus.py`'s `DISPATCHES` table
(it was a single 215-line if-ladder once). A handler is called `handler(args)`,
where `args` is the dispatch's extra argv words — parsed ONCE by `entry()` from
`sys.argv[2:]` (or handed to `dispatch()` by an in-process caller) and passed
down. It returns the literal state to paint, `(state, reason)` when it has
something to say about WHY, or `None` for "no change / exit silently"; every
bail path audits itself first.

*Why not globals.* Five handlers used to re-read `sys.argv[2]`/`[3]`/`[4]`
inside their bodies, which made the table's uniform zero-arg signature a lie and
made those five untestable without patching a process-global. The **reason**
was worse: it was a module global `REASON`, written by four handlers and read
by `main()` when it wrote its one `tab_transitions` row — an out-of-band return
channel that `dispatch()` (the in-process entry, called on hook events) did not
save and restore the way it does the injected payload, so a second dispatch in
one process could attribute a stale reason to a fresh transition. `AUDIT_SID`
and `MLOG` stay module state on purpose: they are the invocation's session
IDENTITY, which `audit_tx()` and the long watcher loops read well after a
handler returned. Pinned by
`test_dispatch_handlers_take_args_and_return_their_reason`.

The tab tracks the **main session only**: any hook event carrying an `agent_id`
is a subagent's / teammate's *own* inner call and is **ignored**, so it never flips
the tab while the main session is thinking or has handed back to you. The main
session still goes blue while it *awaits* an agent (see below).

This rule has ONE owner both producers consult — `core/tabpaint.agent_inner_event`
(the tab ENGINE, so a third tool inherits it). Claude's handlers call it
(`pretool`/`posttool`/`stop`); the codex producer calls it at the top of
`resolve()`. Codex first shipped its tab as a stateless `event→colour` map WITHOUT
this rule and mapped `SubagentStop → working` — and because a codex SubagentStop
carries the child's `agent_id` and can arrive *after* the turn's real Stop, it
repainted **working** over the resting **green** with nothing left to clear it: a
standalone codex host stuck magenta forever (the fix is exactly the shared gate —
the lead's own `wait_agent` tool events hold the tab busy while it awaits the
subagent, and its final Stop turns it green).

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
  window, guarded by a pid row in the tab DB. Both watcher spawns go through
  `core.spawn.spawn_detached`, so the launch is audit-covered: a `spawns` row
  (purpose `watcher:bg-watch` / `watcher:interrupt-watch`) on success, an
  `errors` row on failure — a raw Popen inside `except: pass` used to make a
  failed spawn indistinguishable from a watcher never requested, exactly the
  non-firing-invisible failure class these recovery watchers exist to close.

#### A Bash call that never ran (`PostToolBatch` → `claude-cmd-blocked.py`)

That last bullet's inference — *a finished `fg` tailer means the command was
CANCELLED, so the turn is over, so paint green* — is the one place `bg-recheck`
guesses rather than reads an event, and there is a second way for a foreground
command's writer never to appear: **the call was resolved without ever running.**
Two outcomes do that, and neither fires `PostToolUse`:

- another `PreToolUse` hook **denies** it (`tool_response` = `PreToolUse:Bash hook
  error: […]: Blocked: …`), or
- the permission prompt is **rejected** (`The user doesn't want to proceed with
  this tool use.`).

`claude-cmd-pre.py` has already committed by then: header painted, command teed,
fg slot claimed, tailer spawned, tab **blue**. With no `PostToolUse`, nothing hands
that tailer an outcome — so it waits out `CLAUDE_STREAM_GRACE_S` (2s), ends
`writer-gone`, paints a fake `■ foreground finished · 0.0s`, releases its slot and
calls `bg-recheck`, which reads the vanished writer as a cancel and paints the tab
**green — "your turn" — in the middle of a live turn.** Measured on session
`674d78d1` (2026-07-31): deny at 13:59:10, tailer gave up 13:59:12, tab green
13:59:16, the model's next command 13:59:27. Eleven seconds of a lie, and the same
shape fired seven more times in a sibling session that used the same project hook.

The fix is an **event**, per the rule that every no-hook path needs a real signal
and never an idle timeout: **`PostToolBatch`** fires once the batch has resolved
and carries every call of it *with its `tool_response`*, including the blocked one.
`plugins/claude_code/cmd_blocked.py` runs there and

1. tests for "never ran" **without matching the response wording** — that string is
   version-fragile and there are two of them. The exact local fact is the
   **fg-live hand-off**: `cmd_pre` writes one per foreground command and `cmd_fmt`
   consumes it at `PostToolUse`; by `PostToolBatch` every call in the batch has
   resolved, so a record still sitting there is a call whose `PostToolUse` never
   fired. That covers every present and future reason Claude Code resolves a call
   without executing it;
2. hands the orphaned tailer an outcome on the same take-once `done:` key
   `cmd_fmt` uses, so the block closes at once with `■ blocked · never ran` (the
   one finish chip carrying **no duration** — the command never ran, so any elapsed
   figure would be the tailer's own idle wait) and the refusal text as its body;
3. paints the tab `posttool` → **WORKING**, exactly what the missing `PostToolUse`
   would have. That alone makes the tailer's later `bg-recheck` a no-op ("tab not
   on a bg-running colour").

The hand-off also carries a `blocked` flag, which is the belt to that braces: the
tailer ends `blocked-before-it-ran` and **skips its `bg-recheck` call entirely**, so
even if this handler ever lost the race to the 2s `writer-gone` the green cannot be
painted. The slot is still released, so a genuinely running sibling job clears its
own blue when *it* finishes.

**Why a cancel can't be confused with this.** An interrupt kills the turn, so no
`PostToolBatch` fires for that batch at all — and even if one somehow did, painting
WORKING is the recoverable direction (`interrupt-watch` flips green off the
transcript's own interrupt record, next section), whereas painting green over a
live turn is not. That asymmetry is why the test is "did this call reach
`PostToolUse`", not "is the turn dead".

The regression signature is its own canned anomaly, **`bg-recheck painted green
mid-turn (the turn ran on after it)`**: an applied `bg-recheck` green whose next
turn-boundary-or-tool transition is a `pretool`/`posttool` rather than a
`thinking`. A legitimate green is always followed by the next prompt.

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

**Where this lives.** The DECISION half — mapping Claude Code's hook payloads and
streamer callbacks onto a `(state, reason)` (the `DISPATCHES` table, the `d_*`
handlers, the recovery watchers) plus resolving the window — is Claude-specific and
stays in `plugins/claude_code/tabstatus.py`. The PAINT half above — the dedup, the
`set_tab_color`/`clear_tab_color` call, the persist-only-on-`rc==0` rule, and the
`tab_transitions` audit on every applied/skipped/failed path — is tool-AGNOSTIC and
lives in `core/tabpaint.py` (`paint(fe, win, state, reason, sid=, dispatch=)`,
frontend-INJECTED like `core/hostpane.py`). A SECOND tab producer (standalone codex,
a future hookless polled producer) contributes just its own decision table + window
resolver and reuses the engine — it is NOT reimplemented, because a second copy
would drift and lose the `rc==0` rule this whole section documents (see
docs/styleguide.md, the single-owner table).

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
  command finished, had no recovery at all: stuck magenta.) Green/idle/cleared
  only count once the watcher has seen a **mid-turn state this run**: it is
  spawned *before* `d_thinking`'s paint, and the tab row is written only on an
  *applied* paint — so a THINKING paint that failed (transient socket error) or
  lagged past the first 0.5s tick left the previous turn's green in the row, the
  ungated watcher exited `turn-over` immediately, and a cancel later that turn
  (after a later paint succeeded) had no recovery at all. Writing the row before
  the paint is not an option — persisting failed paints stranded colours (the
  dedup bug above). The gate's cost: a turn whose paints *all* fail keeps the
  watcher alive until its 30m ceiling, which is harmless (the next prompt reuses
  it via the pid lock). The stale sample is audited once
  ("stale pre-turn row — paint failed/lagged"). On seeing the
  interrupt line it first checks what FOLLOWS it in the transcript (after one
  settle tick): a **queued message** changes what the interrupt means — Claude
  Code interrupts the running turn and *immediately delivers* the queued
  prompt, so a new turn starts thinking right away, repaints magenta within
  the 0.5s tick, and a green flip would paint "done" over a live think (stuck
  green until the first tool event repainted — reported live from the web stop
  button). A plain cancel leaves the interrupt line as the transcript's LAST
  record; a queued delivery appends the user-prompt record right after it —
  in that case the watcher audits ("queued prompt delivered"), advances past
  it and **keeps watching** (the delivered turn is mid-flight and deserves the
  same recovery). The dashboard's own stop gesture reasons from the same fact
  on the way IN: its re-press loop stops the moment the transcript shows the
  queue draining, or it would interrupt the message it just delivered
  (docs/dashboard.md, *Interrupt*). Otherwise it re-checks the state: green/idle means the turn
  already resolved (do nothing); blue means a live command/agent whose own
  writer-liveness recovery is faster and authoritative (defer, or it would race
  `bg-recheck` and could paint "done" over a still-live bg job); magenta or red
  has no other signal, so it flips green.

  **The marker must be MATCHED AS A RECORD, never as bytes.** The watcher
  originally scanned the raw transcript growth for the literal
  `[Request interrupted by user]`, which false-positived on any growth that
  merely *quoted* it — and the tab flipped green **mid-turn**. The live trigger
  (session `2e9b57e4`, three times in one session, each corrected only by the
  next tool call 6-20s later): a **`nested_memory` attachment** injecting a
  worktree's `CLAUDE.md` — *this repo's own CLAUDE.md documents the marker*, so
  every mid-turn memory load read as a cancel. Same class: a `Read` of
  `tabstatus.py`, a grep hit, an audit-CLI paste landing as a `tool_result`.
  The queued-prompt guard does not catch it either (the attachment's trailing
  `last-prompt`/`ai-title`/`mode`/`permission-mode` records contain no
  `"type":"user"`, so it reads as a plain cancel). So `is_interrupt_line()`
  parses each appended line and requires the marker to be the **content of a
  `type:"user"` record** — a bare string or a `text` block; an `attachment`
  record and a `tool_result` block are quotes, not cancels. Consequences worth
  knowing: only **complete** lines are decidable, so the cursor advances to the
  last newline and a torn tail is re-read whole next tick (the byte scan needed
  no framing); and the tool-call variant `[Request interrupted by user for tool
  use]` now counts too — it is a real cancel that the old closing-bracket scan
  never detected.
- **Cancelling before the model has produced anything at all** (mid-thinking,
  before the turn's first hook) is the one case with **no signal whatsoever** —
  confirmed empirically: the harness silently rewinds the turn for editing, and
  *nothing* is written anywhere (no transcript line, no sidecar file). For a
  TERMINAL Esc this case is **deliberately left unhandled**: the tab stays
  magenta until the next interaction resets it. A timeout backstop
  (`idle-watch`, "fully quiet for `CLAUDE_TAB_IDLE_SECS` → green") existed for
  it and was **removed** — long thinking fires zero hooks and writes nothing,
  which is *exactly* the same signature as the cancel, so any timeout short
  enough to be useful (30s) false-positived on every long thinking stretch,
  turning the tab green mid-turn. That false "your turn" fired on *every* long
  think and actively misled; the stale magenta it protected against is rare,
  happens with the user at the keyboard (they just pressed Esc), and
  self-corrects at the next prompt — which the cancelling user is typically
  about to type anyway. **A WEB interrupt is the exception that CAN be
  handled** (`escape-recheck`, spawned by the dashboard's `/interrupt`
  endpoint — docs/dashboard.md): unlike a terminal Esc, the press itself is an
  event *we* generated — we know an Escape reached a busy tab, where the TUI's
  meaning of Esc is turn-interrupt — so a backstop keyed to that press honours
  the events-never-timeouts rule. It waits `ESCAPE_GRACE_S` (2s) and flips the
  magenta green only on **silence**: any tab-state movement bails (a real
  signal handled it), and a new `"type":"user"` transcript RECORD past the
  press-time baseline bails too — the state poll alone is NOT enough, because
  a new prompt submitted within the grace repaints the same magenta invisibly
  (the paint dedup skips identical colours), which would put green over a live
  think; the prompt's user record is what makes it visible. It matches a user
  record specifically, NOT raw byte growth: the gesture that killed the turn
  (docs/dashboard.md, *Interrupt*) appends pure metadata
  (`ai-title`, `last-prompt`) right after killing the turn, and a raw-growth
  bail false-positived on the gesture's own records, leaving the tab stuck
  magenta (observed live). Magenta only: blue and red keep their own
  recoveries, and any cancel that wrote the interrupt line is
  `interrupt-watch`'s. And on RED `awaiting-command` the escape-recheck never
  arises, because a web interrupt or rewind is REFUSED there
  outright (`_dialog_open_guard`, docs/dashboard.md): red means a modal dialog
  is open, where Esc would DECLINE the ask/plan/permission rather than
  interrupt a turn — the dashboard's ask/plan/confirm cards are the response
  path, and no Esc is ever sent into an open dialog.


## Codex — the second tab producer

A **standalone codex** host (codex run on its own in a kitty tab, docs/codex.md)
colours its tab through the SAME `core/tabpaint.py` engine — it is the second
producer the split-out was for. It contributes only its own decision table +
window resolver (`plugins/codex/tabstatus.py`), driven by the codex hook
DISPATCHER `plugins/codex/dispatch.py` (entry `claude-codex-hook.py`, wired to
codex's nine non-SessionStart events, docs/wiring.md). The dedup +
persist-on-`rc==0` + `tab_transitions` audit are the engine's, unchanged.

The mapping (codex hook event → state), each a pure function of that event so
duplicate/out-of-order hooks only re-assert what the tab shows:

| Codex event | State | Colour |
|-------------|-------|--------|
| `UserPromptSubmit` | `thinking` | magenta |
| `PreToolUse` `request_user_input` | `awaiting-command` | red (codex asking you) |
| `PreToolUse` shell/exec/`apply_patch` | `executing` | blue |
| `PreToolUse` (other) · `PostToolUse` · `PreCompact` · `SubagentStart/Stop` | `working` | magenta |
| `PermissionRequest` | `awaiting-command` | red |
| `Stop` (per turn) | `awaiting-response` | green |
| `PostCompact` | *(no-op — the next event repaints)* | — |

The `tab_transitions` rows carry a codex-prefixed `dispatch` label
(`codex-pretool`/`codex-stop`/`codex-interrupt`/…) so the existing
tab-left-on-busy anomaly and the audit-debug playbook work unchanged.

**The nested guard.** Those nine events fire for a **codex-inside-Claude**
subagent run (`codex exec`, e.g. the codex:rescue skill in this repo) too, whose
tab belongs to the Claude host. Only a standalone host may paint. Deciding
standalone-vs-nested per event would cost a `kitten @` subprocess (`hostpane.
tab_host_sid`) on every `PreToolUse`, so `plugins/codex/session.py` decides it
**once** at SessionStart and records the standalone host + its tab window in the
global tab DB (`core/tabs.codex_host_*`); the dispatcher reads that as a cheap
sqlite lookup and BAILS (audited `nested-skip`) when the sid is not a known
standalone host. Cleared at teardown.

**Interrupt recovery — the codex twin of `interrupt-watch`.** Codex fires **no
Stop on interrupt** (the same no-hook-on-cancel gap): a turn cancelled at the
terminal writes a `turn_aborted` RECORD to the rollout and nothing else, so the
tab would sit magenta/blue forever. `UserPromptSubmit` arms one detached
`codex-interrupt-watch` per window (re-invoking `claude-codex-hook.py
interrupt-watch <rollout> <sid> <win>`, an audited `streams` row via
`core.tail.stream_lifecycle`). It tails the rollout and, on a `turn_aborted`
matched **as a record through `rollout.parse` — never a raw byte scan** (growth
that merely quotes the marker is not a cancel, the same invariant as claude's
`is_interrupt_line`), flips the stale busy colour green through the engine —
**unless a new turn STEERS off it**: codex's queue+Esc delivers the queued prompt
the instant the abort lands, appending a `task_started` + a `user_message`
(`prompt`) record right after the abort line, and a green flip there would paint
"done" over the delivered turn's think. So it settles one tick, checks what
FOLLOWS the abort, and on a steer advances past it and keeps watching (the same
"check what follows the record" logic claude's watcher uses for a queued prompt).
End reasons mirror claude's (`interrupt-detected-flipped-green` / `turn-over` /
`no-interrupt-within-30m` / `session-parked`). The rollout is located by
`transcript_path` when it is a rollout, else a bounded glob by `uuid == sid`.

The mid-thinking cancel gap (an Esc before the model produced anything, which
writes no `turn_aborted`) stays deliberately unhandled, exactly as for a terminal
Esc in Claude — no idle-timeout backstop.

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
