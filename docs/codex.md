# Codex streams (secondary source + standalone host)

Every codex run — however launched — streams into the mirror; and codex hosts
its own mirror when run standalone (wiring in [wiring.md](wiring.md)).

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
    exit shows a red `■ exit N` (on the rollout side parsed from
    `function_call_output`'s "Exit code / Process exited with code" head lines).
    The ROLLOUT side additionally renders, from codex's own event stream
    (shapes verified against real `~/.codex/sessions` rollouts, 2026-07):
    - **file ops** from `patch_apply_end` — the authoritative record (resolved
      ABSOLUTE paths + per-file `unified_diff`/`content`), one
      `Update(name) +a -r` / `Write(name) +n` / `Delete(name)` line per changed
      file in the Claude file-op look, each fed to the scoreboard exactly like
      a subagent's file ops (unique-path `files` set, ± line sums, Edit/Write
      tool tallies). The `apply_patch` response_item is deliberately IGNORED —
      it only carries repo-relative patch text, and rendering both would
      duplicate. A `success:false` patch paints a red `■ patch failed` and
      bumps nothing.
    - **token accounting** from `token_count` — codex reports a CUMULATIVE
      `total_token_usage` snapshot (input incl. cached / cached / output), so
      the stream keeps only the last one and folds it into the scoreboard ONCE
      at the footer (a `bump-agent` row, meta `kind:"codex"` + model + the
      split — re-derivable from the audit DB alone, same rule as agent spend).
      The footer gains `· <in> in · <out> out · cache N%` and, when the model
      is priced, `≈ $X`. Pricing is the PLUGIN'S own `CODEX_PRICES` table
      (cached input 0.1×), matched by version-exact prefix — an unverified
      newer version (e.g. `gpt-5.3-codex`) deliberately shows NO cost rather
      than silently pricing at an older rate. No fold on the parked-DB exit,
      and none for companion (`.log`) runs — their usage isn't in the activity
      log and their rollout is deliberately not adopted (dedup).
    - **`⚙ model · effort`** (dim, once per change) from `turn_context`,
      **`⌕ search`** + query from `web_search_call`, and **`⟳ compacted`**
      from `context_compacted` — matching the substream's compact treatment.
    **Why no per-subagent codex streams** (the roadmap item): a survey of every
    rollout on the dev machine (33 files, 2026-07-07) found ZERO
    subagent/collab events in codex's vocabulary — the full event set is
    task/turn lifecycle, messages, reasoning, exec, apply_patch, web_search,
    token_count, compaction. The companion log's `Subagent …` head (rendered as
    one `✎ sub` chip) likewise never occurs in any job log on disk. There is
    nothing to attach a per-subagent stream to; revisit when codex actually
    emits per-agent records.
    It never writes after the state DB is parked: the
    header emit re-checks the DB file right before painting (SessionEnd can park it
    during the tailer's wait-for-source window, and `core.state`'s connect would
    *create* a missing DB — resurrecting the session-alive signal the watcher polls,
    which then never exits), and a park detected mid-stream skips the footer rather
    than writing it into the `*.keep` snapshot via the cached connection.
  - **Session/cwd-attributed, not nested.** A codex run is keyed to the Claude
    `sessionId` (source A) or the repo `cwd` (source B), not the launching `agent_id`,
    so it reads as its own **top-level** stream rather than nested under the teammate
    that launched it — the deliberate trade for a global, zero-per-launcher design. (Two
    Claude sessions in the same repo both show a source-B run, the same per-project
    caveat as background-job detection.)
  - **Standalone codex — codex as its OWN host (no Claude session).** Everything
    above renders codex *into a hosting Claude session's* mirror. When you run
    `codex` on its own in a kitty tab there is no Claude SessionStart, so nothing
    used to stand up a pane. Codex now hosts its own mirror via its **native hook
    system** (CLI ≥ 0.142, `[features] hooks = true` + `~/.codex/hooks.json` — the
    same Claude-compatible stdin-JSON hooks, see [wiring.md](wiring.md)):
    - **`SessionStart` → `claude-codex-session.py`** (`plugins/codex/session.py`).
      The payload (`session_id`/`cwd`/`source`, drop-in compatible with Claude's)
      drives the SAME `core/hostpane.py` lifecycle Claude's `split.py` does: create/
      restore the state DB, open the mirror + scoreboard, then detach this session's
      watcher in **standalone mode**. `source:"resume"` restores the parked `*.keep`
      DB, so a `codex resume` replays its mirror history exactly like a Claude resume.
      The mirror width honours `CLAUDE_MIRROR_BIAS` from the **env only** (inherited
      when the launching shell exports it), else the shared `hostpane.DEFAULT_BIAS`.
      Deliberately not Claude's settings.json layering: that reader is
      `plugins/claude_code/model.settings_env`, which the dependency rule forbids the
      codex plugin importing — a bias set only in Claude's settings.json does not
      reach a standalone codex host (known limitation; moving the settings reader
      into core just for this knob wasn't worth making core Claude-settings-aware).
    - **Standalone watcher** (`watch.py` with a `HOST_PID` argv). It streams
      *exactly this session's own rollout* — the rollout filename's `<uuid>` **is**
      the `session_id`, so it matches `rollout-*-<sid>.jsonl` precisely and **adopts
      it even though the originator is `codex-tui`** (the human-driven TUI IS this
      session — the opposite of the secondary-source rule, which drops `codex-tui`
      as belonging to no Claude session). Pinning to the session id means two
      standalone codex tabs in one repo never cross-stream.
    - **Teardown without a SessionEnd hook.** Codex fires no session-end event (only
      `Stop`, per-turn) — the same class as "Claude fires nothing on cancel", so the
      same doctrine applies: teardown rides a **liveness signal**. `session.py`
      resolves the codex process pid (ppid walk) and hands it to the watcher, which
      parks the DB + closes the panes when that pid dies — even on a hard Ctrl-C
      (which fires no hook at all). This is *more* robust than the Claude path: the
      pid is always a truthful end-of-session signal.
    - **Nested vs standalone.** Codex ALSO runs as a Claude subagent (`codex exec`),
      inheriting Claude's pane — so its `SessionStart` hook fires there too. But that
      Claude session's watcher already streams the run (source B, `codex_exec`
      originator). So `session.py` detects it is nested — the tab already carries a
      live `claude_mirror` (`hostpane.tab_host_sid`) — and does **nothing**: no
      second pane, no double stream. Only a truly standalone codex opens its own.
      *Why not a shell wrapper around `codex`:* rejected — it can't distinguish
      nested from standalone, needs a per-user rc edit, and misses codex launched
      any other way. The native hook fires for every entry point and carries the
      session identity the wrapper lacked.
