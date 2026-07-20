# Command streaming (foreground / background / monitor)

How command output reaches the mirror pane live — the producer half of the
producer/renderer split described in [mirror-pane.md](mirror-pane.md).

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
  python), and appends a block of **paint ops** (via `core.ops`) to the
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
    into the mirror as if the command had printed them. The skip offset is
    measured at the LAUNCH site and passed via `CLAUDE_STREAM_POS0`
    (`hookkit.stream_env`); letting the tailer measure at its own open time
    silently skipped output that landed during the tailer's startup — seconds
    under load — a permanently lost line. The adopted offset is audited
    (`state_files` action `open`, path `tail:<taskid>`).

  `│ ` gutter coloured from its kind's palette slot, then writes a
  `■ background finished · Ns` / `■ monitor ended · Ns` line when done. **Completion
  is detected differently per kind** (there is no hook for it — `TaskCompleted`
  is for the TodoWrite list, not background commands):
  - **background** — the command holds its output file open the whole time, so
    the write-holder vanishing (`lsof`) is a definitive signal (works even for a
    long silent `sleep 3600; echo done`). The tailer only *reads*, so it never
    counts itself. The probe is **async and throttled** (`CLAUDE_STREAM_LSOF_S`,
    default 1s): `lsof -- path` scans the whole process fd table (seconds on a
    loaded macOS box), and a synchronous per-poll-tick call both froze the tailer
    loop for the lsof's full duration (fg sentinel hand-offs went unconsumed) and
    let concurrent tailers storm each other slow — once one exceeded the old 5s
    subprocess cap, the failure fallback starved writer-gone indefinitely (the CI
    macOS flake class no wait-ceiling could fix). So `has_writer` starts at most
    one lsof per interval, returns the last known answer immediately, and
    harvests the result on a later tick; the answer lags by at most one lsof
    duration + the interval, which only ever *delays* writer-gone (grace is 2s).
    A **failed/hung** `lsof` (its 30s cap on a badly wedged box) reads as
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
    or 1h between ticks) with no grace/idle guess. The moment the pid latches is
    audited (`state_files` action `proc-found`, path `monitor:<taskid>`) — the
    evidence for "was the process ever identified?", and the observable the e2e
    suite synchronises on before killing its monitor stand-in. A short idle
    fallback only applies if the process can't be found. **Disambiguation:** the launcher also
    passes the *full* command (`CLAUDE_MONITOR_CMD`) and a whole-command argv
    match always wins — the longest-token signature alone can equally match an
    unrelated long-lived process (another tail/editor holding the same file path
    in its argv), and latching onto that pid kept the block open and the tab blue
    forever. With the full command available, ambiguous token-only multi-hits
    return "not found" so the idle fallback closes the block instead. A **failed
    Monitor call** (`PostToolUseFailure` — no `taskId`, nothing will ever stream)
    gets its block closed inline by `claude-monitor-fmt.py` with a
    `■ monitor failed` chip, instead of a dangling open header.
  - **All kinds also poll `state.parked()`** (the shared session-over probe the
    substream, codex tailers, codex watcher and scorebar run): SessionEnd parks
    the state DB away from the live path, and a bg/monitor job can keep printing
    long after — with no session left, the tailer quits footer-less
    (`state-db-parked (session end)`), releasing nothing. The check runs at the
    **top of each loop iteration, before the pump**: a post-park `O.emit` either
    *recreates a fresh empty DB at the live path* — whose absence IS the
    session-alive signal, so the next `--resume`'s `decide_log_fate` reads
    `reuse-live-db` and strands the real history in the park — or, through a
    cached connection, pollutes the parked snapshot. For the same reason the
    tailer's teardown is parked-aware: `slots.release` no-ops once parked (the
    `live` table went with the DB, and its connect would mint the file a
    never-emitted silent tailer has no cached connection to hide), the fg-live
    reclaim is skipped (a state-DB write, same hazard), and the `bg-recheck`
    isn't spawned (the session is over and SessionEnd already cleared the tab)
    — only the removal of our own tee `.out` (a plain /tmp file, not a DB)
    still runs. The substream's `cleanup()` runs the same gate: its
    `release_id`/`agent_set`/`pid_del` are all state-DB writes, and post-park
    it returns without them (and without the recheck).
  - **Teardown is crash-safe.** The whole teardown (`stream.py cleanup()` —
    tee-file removal, fg-live reclaim, slot release, stale-red `bg-recheck`,
    in that order: release-before-recheck so the recheck never sees the
    tailer's own slot marker) is `stream_lifecycle`'s `on_exit`, not just
    main()'s last statement — so it runs on EVERY exit path, crash included.
    Before that, a main() that raised after `open_tailer` (renderer exception,
    signal) had its crash audited and its slot released, but leaked the tee
    `.out` until the 7-day sweep, the `fg-live` record until the next Bash
    `PreToolUse` noticed its dead pid, and never cleared a stale red tab. A
    ran-flag makes cleanup once-only, so the happy path's ordering
    (finish chip → cleanup → `stream_end`) is unchanged.
  - **All tailers handle truncation**: if the tailed file *shrinks* (the command
    runs `> file` again, or the file is rotated in place), `FileTailer.pump`
    restarts from byte 0 — the old offset pointed past EOF, so nothing would ever
    be emitted again (or a regrow would resume mid-content from a stale position).
  - **Worst-case bounds (three named caps).** Pathological output used to be
    unbounded on the verbatim path: a 100MB no-newline line grew `pending` to
    100MB, and a 100MB burst became ONE giant `gut` op the renderer re-wraps
    char-by-char on EVERY reflow (resize, click-to-view toggle, pane re-open) —
    a permanent latency tax, not a one-off. Three caps, each env-overridable
    (docs/testing.md knob table):
    - **Per-pump read ceiling** — `CLAUDE_TAIL_PUMP_MAX_B` (256KB,
      `core/tail.py`). One `pump()` ingests at most this much; reading *less*
      than `size-pos` is always safe under the read-exactly contract (the
      remainder is next pump's), and `tail.capped` tells the caller a backlog
      remains. Every tailer must **keep pumping while capped before trusting a
      completion signal** — the writer can be long gone (idle grace elapsed)
      while unread bytes remain, and a single "final catch-up" pump would
      truncate the drain. `stream.py`'s main loop skips `is_done` + the sleep
      while capped (still checking parked/backstop per chunk) and its drain
      loops to exhaustion; the substream and codex pumps loop internally, so
      their one-call sites stay "caught up". Bounds memory AND makes a burst
      paint progressively as a sequence of ≤256KB batches.
    - **Max surfaced line** — `CLAUDE_TAIL_LINE_MAX_B` (64KB), **opt-in per
      tailer** (`FileTailer(line_max=…)`); only `stream.py`'s fg/bg/monitor
      tailer sets it — the substream/codex tailers parse JSONL transcripts
      where one line is one whole message, and truncation would break
      `json.loads` and silently drop events. An over-cap line is surfaced as
      its first 64KB plus an `… (N bytes elided)` marker, and the over-cap
      middle of a *still-incomplete* line is discarded as it arrives, so
      memory stays bounded before the newline ever shows up. 64KB ≈ 800
      wrapped rows at 80 cols — already far past useful in a monitor pane;
      truncating pathological output is the accepted trade-off (it also caps
      what ⧉out copies — see [click-to-view.md](click-to-view.md)). The
      elision is self-evidencing: the marker lives in the op text itself
      (audited with the ops stream). Content renderers (md/json/yaml) see the
      truncated line too and degrade to their verbatim fallbacks.
    - **Bytes per op** — `CLAUDE_STREAM_OP_MAX_B` (128KB, `stream.py`
      `verbatim_batches`). A capped pump can still hand over 256KB at once;
      the verbatim emitter splits it into multiple ≤128KB `gut` ops — each
      still a multi-line *batch* (never a per-line-op regression; per-line
      emits are the txn overhead batching exists to avoid), so no single op
      ever carries a whole burst through every future reflow.
- **Live foreground streaming (Ctrl+B aware).** `claude-cmd-pre.py` (`PreToolUse`
  Bash) makes a normal foreground command stream live instead of only appearing
  once it completes. It rewrites the command via `PreToolUse`'s `updatedInput`
  (undocumented but confirmed working) to also `tee` its stdout/stderr into a side
  file — `{ <cmd>\n\n} > >(tee -a "$F") 2> >(tee -a "$F" >&2)` (the blank line
  before `}` is load-bearing: a command ending in a line-continuation backslash
  eats the first newline, which used to weld the `}` onto the last line — a
  syntax error for a command that ran fine unwrapped), or the command's own
  redirect target if it already has one — emits the `▶ foreground` header
  immediately, claims an `fg.<n>` slot (so the tab tracker sees it — [tab-colors.md](tab-colors.md)), and
  spawns `claude-stream.py fg` to tail `$F` the same way a background job is
  tailed. `claude-cmd-fmt.py`'s `PostToolUse` handler is the only place the real
  outcome (duration/exit code/interrupted) is known, so it hands that off to the
  tailer via a **state-DB hand-off record** (`core.state` handoffs — was a
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
  - **Waiting for a late-created output file** (`wait_fg_src`). The write-holder
    check above only works once the file *exists*. `$F` — especially the
    command's own redirect target — can appear arbitrarily late: `sleep 45; cmd >
    out`, or a retry loop that only writes on its Nth pass. So the file-appearance
    wait is **liveness-bounded too**, mirroring the monitor's process-liveness
    wait for a lazily-created `tasks/<id>.output`: the `fg` tailer keeps polling
    for `$F` until it lands **or** the `PostToolUse` outcome hand-off arrives
    (the blocking Bash call resolved → the command genuinely finished with no
    file), capped by `FG_BACKSTOP_S` against a wedged tailer — **not** the flat
    `FIND_S` deadline `bg` uses. The rejected design was that flat ~12 s deadline:
    it painted `■ output not found`, released the `fg` slot, and `bg-recheck`
    cleared the tab off blue while the command ran on for another 40 s, its late
    output never streamed (audit tell: an `fg` stream ending `output-file-not-found`
    whose command's `PostToolUse` fired seconds *later* — the
    `fg tailer gave up on a late redirect target` anomaly).
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
    starts from the task output file's size *at the hand-off moment* — measured
    by `claude-cmd-fmt.py` itself (the same task-output glob the tailer uses; 0
    if the file doesn't exist yet) and passed via `CLAUDE_STREAM_POS0` with
    `CLAUDE_STREAM_SKIP_EXISTING` — not from 0, so whatever the `fg` tailer's tee
    copy already showed isn't repeated; and not from the size at the tailer's own
    OPEN time, which skipped (lost forever) any output that landed while the
    tailer was still starting up.
    - **Hand-off ordering is deliberate: sentinel first, then pos0, then
      spawn.** `claude-cmd-fmt.py` writes the `converted` sentinel *before*
      measuring pos0. The theoretical duplicate window is a line that lands in
      the tee file after the fg tailer's last pre-sentinel pump AND in the task
      output file after pos0 — the fg tailer does one final tee pump in its
      drain after taking the sentinel, and the bg tailer replays everything
      past pos0. That window requires the tee to keep receiving output *after*
      the Ctrl+B conversion, which contradicts the observed behavior above
      (post-keypress output stops landing in the tee entirely, and cmd-fmt
      only runs after the keypress) — so it is theoretical, never observed.
      Swapping the order (pos0 before the sentinel) would *widen* it: an
      earlier, smaller pos0 makes the bg tailer replay more of the task file,
      including content the tee copy showed between the two moments. And
      clamping the fg tailer's drain ("emit nothing pumped after the
      sentinel") trades the never-observed dupe for a possible *drop* — a
      line that only ever reached the tee (e.g. on a build where the tee stays
      attached) would then render nowhere, and losing output is worse than
      showing it twice. Current behavior is pinned; don't reorder.
    no-hook-on-interrupt gap noted throughout this doc), so `claude-cmd-fmt.py`'s normal consume of
    the `fg-live` record (a state-DB hand-off, key `fg-live` — was a `.fg-live`
    JSON file) never runs. Left alone, that stale record would make
    `claude-cmd-pre.py` think a live block is *already* in flight forever, and
    silently skip wrapping every later command (the mirror would just stop
    showing anything new). The record stores the tailer's pid, and
    `claude-cmd-pre.py` liveness-checks it (`os.kill(pid, 0)`, the same pattern
    `core.slots` uses for stale slots) before treating an existing claim as
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
  - **Redirect detection is quote-aware** (`tools.parse_redirect`,
    `posix=False` tokens): posix tokenising stripped quotes, so `grep '>' file`
    parsed as a *redirect to `file`* — cmd-pre then skipped the tee rewrite and
    the tailer streamed the whole existing file into the mirror as "command
    output" (tail-from-0 is only correct when a real `>` truncates). Heredocs,
    `>|` clobbers, and `>(…)` process substitution all return None (the body of a
    heredoc tokenises like real redirects and last-wins picked those) — None just
    means falling back to the tee side file, which is always safe.
  - **…and statement-scoped** (2026-07-16): only a redirect in the command's
    *final* statement counts. "Last redirect wins" used to scan the whole
    command, so a mid-command bookkeeping redirect (`for … do ( … >>
    summary.txt ) & done↵wait↵sort summary.txt`) was taken as the output sink
    while the visible output (the trailing `sort`) went to stdout — redirect-tail
    mode then captured nothing the user cared about. When statements follow the
    last redirect, the tee is the correct mode *and* shows everything. A
    RELATIVE target now also resolves against a statically tracked cwd
    (`tools._follow_cd`): `cd build && make > log` tails `build/log`, where
    joining against the hook payload's cwd tailed a path that never existed
    (the tailer waited it out and painted "output not found"). Only plain
    top-level `cd <literal>` statements are followed; a dynamic (`cd "$DIR"`,
    `cd -`, `~`), subshell-scoped (`(cd x; …)`), backgrounded, or flagged cd
    makes the effective cwd unknowable, and a relative target then returns None
    (tee fallback) — never a guess, because tailing a wrong-but-existing file
    replays its entire contents into the mirror as command output. Why not
    follow subshell cds too: shlex glues `(cd` into one token and paren scoping
    isn't reconstructible from a quote-blind statement split; the tee is always
    correct, so ambiguity resolves toward it.
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
  for the `Monitor` tool) writes a cyan `◉ monitor · <description>` header, then
  the **watched command** as a highlighted `code` op right underneath (a
  WebSocket monitor — `ws.url`, no command — shows a `⇄ ws · <url>` line
  instead), then spawns `claude-stream.py` for the monitor's event stream — so
  Monitor output shows in the split too, even though Monitor bypasses the Bash
  tool. The header suffix records the monitor's lifetime (`· persistent`, or
  `· ≤<dur>` for a timeout). Header, command, streamed events, and the finish
  chip all share the `taskId` **copy-group** (`CLAUDE_STREAM_GROUP`), so the
  block carries `⧉cmd`/`⧉out` links exactly like a background command block.

### Monitor events in the transcript

A Monitor's events are delivered to the model mid-turn, and Claude Code records
each one in the session transcript as a **`type: "queue-operation"`** record
(`operation: "enqueue"`) whose `content` is a small `<task-notification>` XML
block — `<task-id>`, `<summary>`, and either `<event>` (a per-event line) or
`<status>completed</status>` (the one final record when the monitor's stream
ends). This is **empirically confirmed, undocumented** Claude Code behavior (like
`updatedInput` command-rewriting and the Ctrl+B payload — check here before
assuming the shape). `transcript.parse_line` turns these into `monitor_event`
records, and the drill-down `timeline()` surfaces them as `{"t": "monitor"}`
entries (the dashboard's per-session activity view — docs/dashboard.md). They
are deliberately **NOT** added to `conversation()` / the dashboard mirror: the
monitor's events already ride the ops stream (the `claude-stream.py` tailer
above), so re-emitting them from the transcript would double them.

**Not every `<task-notification>` is a monitor.** A **background job's**
completion rides the same mechanism (`summary: 'Background command … completed
(exit code N)'`, `status: completed`, and — like a monitor's stream-ended
record — a `<tool-use-id>` + `<output-file>`, but **no `<event>`**). So
`_monitor_note` keeps only the monitor ones — a `<event>` tag, or a `Monitor …`
summary — or a bg completion would parse as a `monitor_event` and show up as a
phantom monitor on the dashboard's monitors tab / mislabel the activity timeline.
Background jobs get their own dashboard tab, sourced from the audit `bg` streams
+ ops, not from these notifications (docs/dashboard.md, *Jobs tab*).
