# Subagents, teammates, and agent teams

How a subagent's / teammate's full activity streams into the mirror
(see [mirror-pane.md](mirror-pane.md) for the pane, [scoreboard.md](scoreboard.md)
for the counters these streams feed).

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
  - **Live foreground commands (subagents too).** A subagent's foreground Bash
    command would otherwise only show its output when the `tool_result` lands in
    the transcript — i.e. *after* it finishes — because `claude-cmd-fmt.py` skips
    `agent_id` events ([streaming.md](streaming.md)) and the substream renders from the transcript alone.
    To stream it live like a background job, `claude-cmd-pre.py` applies the **same
    tee-rewrite** it uses for the main session (`updatedInput`, confirmed to work
    for a subagent's PreToolUse too), keyed by `tool_use_id`: it leaves a
    `subfg:<tid>` state-DB hand-off with the tee paths but emits **no header and
    claims no `fg` slot** (the tab is already blue via this agent's `sub.pid` row).
    `claude-substream.py`, when it reaches that `tool_use`, consumes the marker
    (a short bounded wait — the hook can lag the transcript line by a beat) and
    spawns the ordinary `claude-stream.py fg` tailer **double-guttered in the
    subagent's colour** (the foreground analogue of its nested bg/monitor jobs),
    then **suppresses its own output render** so the block isn't drawn twice. At
    `tool_result` it hands the tailer the outcome via the same `done:<path>`
    sentinel the main session uses — carrying only pass/fail (`{"failed": …}`),
    since the tailer owns the duration; a failed command gets a red `■ failed`
    chip. Gated by `CLAUDE_MIRROR_LIVE_FG_SUB` (default on; `=0` opts out, as does
    the parent `CLAUDE_MIRROR_LIVE_FG=0`) — and it inherits the auto-approve
    trade-off ([streaming.md](streaming.md)), now extended to subagent commands (deny rules still apply).
    Content pretty-rendering (markdown/JSON/YAML/source colouring) applies here
    exactly as in the main session: the substream hands the tailer the raw
    command (`hookkit.stream_env` → `CLAUDE_STREAM_CMD`) and the tailer decides
    (see *Where detection runs* in [mirror-pane.md](mirror-pane.md)) — so a subagent's `cat Foo.kt` colours too.
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
    session's `bump_transcript()` ([scoreboard.md](scoreboard.md)) — one assistant message is one JSONL line
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
    cumulative baseline (`billed:<agent>` kv — `{in,out,cache,create,create_1h}` summed
    across the whole streamer chain), and **`claude-subagent-fmt.py`'s `SubagentStop`**, once it
    sees the streamer is gone, folds the agent's *full* transcript to its true total
    (`accounting.fold_usage`, the batch analogue of the inline fold — same `usage_fold`
    dedup) and bumps only `true − baseline` (a `bump-agent` with `meta.reconcile`, plus
    a `reconcile` audit row). Idempotent: a clean finish or a duplicate stop leaves
    `true == baseline` and bumps nothing. This recovers the *transcript-resident*
    shortfall; a separate residual (interrupted/retried turns whose billed usage never
    lands as complete assistant lines on disk) is not transcript-recoverable and leaves
    a transcript-folding scoreboard slightly under `/cost` on cancellation-heavy sessions.
    A much larger structural gap of the same kind: Claude Code runs **hidden
    summarizer-style agents** (one every ~35s while a session is busy) that fire *only*
    `SubagentStop` — no `SubagentStart`, and their payload's `agent_transcript_path` is
    never written to disk — so their full-context billed reads reach `/cost` but no
    transcript any fold can see (~$14 on the session that exposed this). The stop
    handler names them distinctly (`stop: never started (hidden agent) — spend no
    transcript`) instead of the old misleading "duplicate stop", reconciles from the
    payload path when a transcript *does* exist, and the audit gained a
    `SubagentStop without SubagentStart` anomaly so the gap is diagnosable, not silent.
  - **Cost estimate in the footer.** After the rollup the footer appends `· ≈ $X`,
    the summed tokens priced on the resolved model (`accounting.PRICES`, per-MTok
    input/output for the current lineup; `cache_read` billed at ~0.1×, cache writes
    at their per-TTL premium — see *Pricing* in [scoreboard.md](scoreboard.md)). An unknown
    model shows nothing rather than guess. Being last, it truncates before the rest.
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
    **every ancestor `.claude/` dir** (`plugins/claude_code/model.py claude_dirs`, `$CLAUDE_PROJECT_DIR`
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
    The stop decision distinguishes three no-footer cases: a genuine duplicate stop
    (`no-op (already finalised / duplicate stop)`), a **never-started hidden agent**
    (`never started (hidden agent) — spend <reconciled | no transcript>`; the handler
    reads the agent record's `slot` *before* writing the `done` flag — only a real
    `SubagentStart` sets a slot), and the safety-net footer itself; each carries the
    reconcile outcome so a spend gap is attributable from the decision row alone.
    Reconcile prefers the payload's `agent_transcript_path` over the derived
    `subagents/agent-<id>.jsonl` path — that's also what lets a never-started agent's
    spend fold on the one stop it ever fires, if its transcript exists.
    - **Manually cancelling/killing a subagent fires no `SubagentStop` at all** —
      the same no-hook-on-interrupt gap noted throughout this doc (`interrupt-watch`,
      the cancelled-foreground-command fix in [streaming.md](streaming.md)). Left alone, the streamer would
      hang on the sentinel until its 6h backstop, leaving the tab **stuck blue**
      the whole time (`sub.pid.<agent_id>`'s pid stays alive — a subagent has no
      OS process of its own to go liveness-check). But Claude Code *does* stamp
      `stoppedByUser: true` onto `meta.json` the moment a cancel happens
      (confirmed empirically) — so the streamer polls that alongside the sentinel
      and exits within its next 0.3s tick, releasing the slot and triggering the
      usual `bg-recheck` handoff to green. The footer reads `■ <type> cancelled ·
      Ns` instead of `ended` in this case.
    - **Rejecting a Task at the permission prompt** (or otherwise abandoning it
      mid-run) is a *fourth* end-shape that neither of the above covers: Claude
      Code fires **no `SubagentStop`** *and* writes **no `stoppedByUser`** stamp
      (the tool_result reads "The user doesn't want to proceed with this tool
      use"), so the streamer — and the `sub.pid` `live` row that keeps the tab
      blue — would hang until the 6h backstop. The recovery signal is the **parent
      transcript**: the Task/Agent tool_use resolves there into a `tool_result`
      keyed by the agent's `meta.json` `toolUseId`, the instant the call ends
      (completed, rejected, or cancelled). The streamer tails the parent transcript
      from its end (the result lands later) as a fallback poll — checked *below*
      the sentinel and `stoppedByUser` (so a normal finish still exits on its
      authoritative signal) and lightly throttled (every ~2s) so a chatty parent
      isn't re-scanned each tick. It exits `parent-task-resolved` (`… (rejected)`
      when the result `is_error`), footer `cancelled` for a reject. *Why not an
      idle timeout:* this is an **event** (the result appearing), not "quiet for N
      seconds" — the banned `idle-watch` backstop false-positived on long thinks;
      keying on the parent's own completion record has no such failure mode. The
      detector is `model.parent_tool_result` (a pure function, sibling to
      `parent_resolved_model`, which already scans the parent for this agent's Task
      result). **The async-launch-ack exception:** an **async (background) agent**'s
      Task resolves the parent `tool_result` *immediately* — but with a synthetic
      *"Async agent launched successfully"* ack (`is_error` absent) that means
      *launched*, not *finished*; the agent then runs for minutes writing its whole
      transcript. `parent_tool_result` explicitly returns `None` for that ack (a
      falsy `is_error` **and** `"launched successfully"` text), so the streamer keeps
      tailing to the real `SubagentStop`. Treating the ack as a resolution ended the
      streamer ~2s in with **zero lines rendered** — the async agent's entire block
      never reached the mirror (found 2026-07-11, session `1c5e842c`; anomaly *"async
      launch-ack ended the substream early"*). *Cancel-before-first-`SubagentStart`*
      still has no streamer to recover and is left unhandled, as elsewhere.
    - **A subagent turn that dies on an API error** (e.g. `529 Overloaded`) is a
      *fifth* end-shape. Claude Code fires **`StopFailure` carrying the subagent's
      `agent_id`** — not `SubagentStop`, and no `stoppedByUser` stamp (confirmed
      empirically: payload `error: "server_error"`, `last_assistant_message` the
      API-error text). For an **async background agent** the rejected-Task recovery
      above also can't help: its parent `tool_result` is only the "Async agent
      launched successfully" ack, which the detector deliberately ignores (see the
      async-launch-ack exception above), so the streamer had *no* other end signal
      and hung on its 6h backstop, `sub.pid` `live` row keeping the tab **stuck
      blue** the whole time. `StopFailure` is wired to
      `claude-stop-fmt.py`, which ignores an `agent_id` payload for accounting (the
      inner turn is the substream's to bill) — but a `StopFailure` *is* the agent's
      only stop signal, so stop-fmt now hands it to the same finaliser
      `claude-subagent-fmt.py`'s `SubagentStop` uses (`subagent_fmt.finalize`): set the
      agent record's `done` flag (the streamer polls it and exits `stop-sentinel`,
      releasing the slot → `bg-recheck` to green), with the crash-tail reconcile +
      safety-net footer if the streamer already died. Its decisions carry a
      `stopfail:` prefix (vs `SubagentStop`'s `stop:`) so the two are distinguishable
      in the audit. A *plain* `Stop` with an `agent_id` stays ignored — that's an
      inner turn boundary, and `SubagentStop` still owns finalisation.
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
      also polls `state.parked()` (the shared probe the codex tailers, the
      bg/monitor stream, the codex watcher and the scorebar all run) and
      exits `state-db-parked (session end)` instead of spinning to its 6h
      backstop as a zombie whose checkpoint writes mutate the parked `*.keep`
      snapshot through its cached connection. No footer/bumps after that: any
      write would either land in the snapshot or recreate the DB file — whose
      existence IS the session-alive signal watchers poll. The streamer's
      `cleanup()` (its `stream_lifecycle` on_exit) honours the same rule: once
      parked it returns without its `release_id`/`agent_set`/`pid_del` writes
      — through the cached connection they deleted the slot rows out of the
      parked snapshot, and with no cached connection they'd recreate the live
      DB — and without spawning the `bg-recheck` (the session is over and
      SessionEnd already cleared the tab).
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
  - Highlighting/wrapping/gutter/unescape primitives live in **`render.py`**
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
