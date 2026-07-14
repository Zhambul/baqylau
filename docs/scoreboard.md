# Session scoreboard

The 5-row scoreboard window under the mirror (`claude-scorebar.py`) and the
token/cost accounting behind it. Token/cost counters are OTEL-authoritative —
see [otel.md](otel.md).

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
  - **The `Σ` row is the token display: a per-category breakdown with an all-in total.**
    The `Σ` row (`claude_ops.token_parts()`) shows the four raw
    categories — **input · output · cache read · cache write** — plus a **total** that
    ADDS cache-read replay, so it reconciles with what `claude --resume`'s "Usage by
    model" reports (that total is dominated by cache read on a long session, so it far
    exceeds *billed* spend — different metrics, on purpose). The OTLP receiver feeds the
    four dedicated counters (`tk_in`/`tk_out`/`tk_read`/`tk_create`) straight from
    OTEL's `token.usage` `type` attribute (input→tk_in, output→tk_out, cacheRead→tk_read,
    cacheCreation→tk_create), so `tk_in + tk_create + tk_out` equals the billed `tokens`
    counter and `+ tk_read` is the Σ total's extra — and the display code is unchanged
    from when the fold fed the same counters. Total-first so a narrow pane keeps the
    headline.
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
  - **Pricing** (`claude_ops.PRICES`, verified against the published 2026-07 list):
    Fable/Mythos 10/50 · Opus 4.6-4.8 5/25 · Sonnet 3/15 · Haiku 4.5 1/5 · legacy
    Opus 4.1/4.0/3 15/75 per MTok in/out. The table keys are **substrings of the
    real model ids** — the legacy rows are `opus-4-1` / `opus-4-2025`
    (`claude-opus-4-20250514`) / `3-opus` (`claude-3-opus-…`); the earlier
    `opus-4-0`/`opus-3` keys appeared in *no real id*, so every legacy-Opus run
    fell through to the generic 5/25 row (a silent 3× undercount). Cache reads bill
    0.1× input; cache **writes bill by TTL** — 5-minute 1.25×, **1-hour 2×**. The
    usage dict carries the per-TTL split
    (`cache_creation.ephemeral_{5m,1h}_input_tokens`), which `usage_fields` reads as
    a fifth field so `cost_usd` can add the 1h share's extra +0.75×; pricing every
    write at 1.25× undercounted a session whose writes were all 1h by ~$0.9 (a
    breakdown-less usage prices as all-5m, i.e. exactly the old math). There is **no
    long-context premium** — the 1M window bills at these flat rates (a >200k-context
    agent run is priced the same; confirmed on the published price page). Sonnet 5's
    introductory 2/10 rate is used automatically through 2026-08-31, then reverts to
    the 3/15 sticker. An unknown model counts tokens but adds no cost rather than
    guess.
