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
  immediately, claims an `fg.<n>` slot (so the tab tracker sees it — [tab-colors.md](tab-colors.md)), and
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
