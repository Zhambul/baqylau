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
    guards against a duplicate SessionStart. The claim is **non-creating**
    (`lock_acquire(create=False)` → `state.connect_existing`, a sqlite
    `mode=rw` open): the claim is the watcher's FIRST state-DB write, and on a
    loaded machine the spawn can lose the race against a fast SessionEnd — a
    creating open resurrected the just-parked DB, whose file-existence is the
    session-alive signal, so the watcher's own `parked()` loop never fired and
    it spun forever as an orphan (the CI f10b timeout). With no DB it exits
    immediately, audited as end_reason `parked-before-start (no state DB)`.
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
    **Parse/paint split**: rollout-record parsing lives in
    `plugins/codex/rollout.py` — the ONE owner of the rollout record shapes
    (the `turn_context`/`event_msg`/`response_item`/top-level grammar,
    exec-arguments decode, patch line counts, exit extraction, the
    synthetic-message vocabulary, and `usage_split`, the one
    `total_token_usage` → fresh/out/cached mapping) — mirroring
    `plugins/claude_code/transcript.py`. Presenters consume its typed
    records — there may be MORE THAN ONE, and the grep contract
    (`test_renderer_consumes_the_parser`) pins only that no presenter
    re-walks the raw grammar, not that a single one exists. Today:
    the stream's `Renderer.feed_rollout` (the mirror's capped,
    styled paint — byte-identical to the pre-split renderer, pinned by the
    e2e codex suite); a dashboard `conversation` provider is the second.
    `feed_rollout` dispatches on a `kind` TABLE and silently ignores every
    kind it has no handler for, so a record added for another presenter never
    changes the mirror. There was a third — `rollout.timeline()`, the uncapped
    drill-down read model behind a codex `plugins.activity()` provider — and it
    is gone with that whole read model: a codex run's web view is now the mirror
    it already paints, scoped (docs/dashboard.md *Agent scope*), resolved from
    the audit `streams` keystone (`kind='codex'`, `src_path` = the rollout) via
    `plugins.runs()` (plugins/codex/nested.py);
    the run's identity in the `agents()` list is `paths.codex_aid()` —
    the src basename, extension stripped — since a codex run carries no hook
    `agent_id`. Companion `.log` runs are listed but have no drill-down
    (their activity log is not a rollout); a standalone codex session's own
    rollout answers the main-thread `activity(sid)` (uuid == sid). The
    companion `[ts]`-log parse stays in `stream.py` — a pre-digested display
    stream, not a record grammar worth a second module.
  - **Kind drift contract.** `parse`/`parse_line` grew across versions and
    kinds got ADDED faster than the mirror renderer learned to paint them, so
    codex content silently vanished — `feed_rollout` drops any `kind` absent
    from `_RO` by design. The safety net is a single-owner set + a
    both-directions test: **`rollout.KINDS`** is the ONE owner of the complete
    kind vocabulary `parse` can return (hand-maintained — a handler's kind is
    not its registry key, e.g. `user_message` → `prompt`, `patch_apply_end` →
    `patch` — so it can't be derived off the `_EVENT`/`_RESP`/`_CALL`/`_TOP`
    tables), and **`stream.IGNORE_KINDS`** enumerates the kinds the mirror
    deliberately does NOT paint (the `chat`/`think` conversation register, the
    `patch_call` patch-lifecycle marker already covered by
    `patch`, the `compact_boundary` covered by the event_msg `compact`, the
    `stdin` backgrounded-exec poll, the `ask` question card, and the `bad`
    malformed line), each with an inline reason. `tests/test_l1f_codex_rollout.py`
    pins that EVERY `KINDS` member is either a `Renderer._RO` key or an
    `IGNORE_KINDS` member (nothing undecided), that no handler names a kind the
    parser never emits (no stale/typo'd key), and that the two sets are
    disjoint — so a new or renamed parser kind fails the suite until someone
    decides render-vs-ignore. `IGNORE_KINDS` is documentation + the contract's
    ignore-side only; `feed_rollout` never consults it (adding a kind there
    changes no paint behaviour).
  - **Two registers — deliberately not unified.** A codex rollout says most
    things TWICE: once as an `event_msg` (codex's own digested UI stream) and
    once as a `response_item` (the model-API record the conversation is
    rebuilt from on resume). The MIRROR paints the event_msg register
    (`prompt` / `message` / `reasoning`); a CONVERSATION presenter reads the
    response_item register (`chat` / `think`), which is the complete,
    in-order, resume-restored one and the ONLY source of a **post-abort or
    queued prompt** (codex writes it as a `response_item/message` and no
    `user_message` event ever fires). Giving the second register its OWN
    record kinds is what keeps the mirror from painting every message and
    every think twice — a shared `message`/`reasoning` kind would have, since
    `feed_rollout`'s table already handles those.
    A `chat` record carries `role` (assistant/user/developer) and a
    `synthetic` flag: codex re-injects its own context blocks *as
    user/developer messages* every turn, so a presenter must drop them from the
    bubbles (else a subagent's input reads `<recommended_plugins>…` instead of
    its real prompt). The rule is STRUCTURAL, not an allowlist (`is_synthetic(text,
    role)`, the one owner): **(1)** role `developer`/`system` is the system channel
    → always synthetic (catches `<multi_agent_mode>`, `<permissions instructions>`,
    … with no list); **(2)** a role=`user` `<tag>` wrapper is a system injection BY
    DEFAULT — robust to new tags like `<recommended_plugins>` — EXCEPT an
    `INPUT_WRAPPER` (`<task>`, the wrapper codex delivers a subagent's task in),
    which is KEPT and unwrapped to its inner text (`strip_input_wrapper`) so the
    bubble reads as the prompt; a real prompt is free prose. `SYNTHETIC_PREFIXES`
    shrank to the two NON-tag supplements (`Approved command prefix saved:`,
    `# AGENTS.md instructions`) — the `<…>` markers are now caught by rule (2).
    A real prompt always survives via the CLEAN event_msg register even if its
    response_item twin is a `<tag>` block, so the deny-by-default can't hide input.
  - **The rest of the response_item grammar** (parsed for the same
    conversation presenter; none of it reaches the mirror):
    - **`custom_tool_call` / `custom_tool_call_output`** — codex ≥ 0.13x runs
      BOTH `apply_patch` AND `exec` through custom tools (the `custom_tool_call`
      `name` disambiguates), and the OUTPUT record carries no name at all:
      - `name:"exec"` — the 0.14x+ command channel (what a real `run ls` uses,
        verified 0.144.1): the command lives in a JS `input`
        (`tools.exec_command({cmd:"ls",…})`), pulled out with `_JS_CMD` into an
        `exec` record — the reason a codex command showed NO block before was
        that the parser knew only the older `function_call`/`exec_command`
        channel.
      - `name:"apply_patch"` — a LIGHTWEIGHT `patch_call` marker (the raw
        `*** Begin Patch` text + `call_id`); the file ops come from
        `patch_apply_end`, so counting the call too would duplicate.
      - `custom_tool_call_output` (nameless) parses to an **`exec_result`**
        (`exit`/`output`, paired by `call_id`; codex's `…Output:\n` status
        preamble stripped, the list-of-parts form joined). The renderer pairs
        it by `call_id`: an exec's closes its command block, an apply_patch's is
        an orphan surfaced only on a FAILED exit (its file ops already came from
        `patch_apply_end`), so one `exec_result` shape serves both with no
        double-render and no `patch_result` kind. **The double-count question:**
      `patch_apply_end` stays the AUTHORITATIVE file-op record — it alone has
      resolved ABSOLUTE paths and per-file diffs — so the call records
      deliberately produce **no file rows and no scoreboard bumps**; they say
      only "a patch call started / it succeeded or failed". Rendering the
      repo-relative patch text as a second set of file ops is exactly the
      duplication the original "apply_patch response_item is ignored" rule
      forbade; the marker exists so a conversation view can show the tool
      call in order without re-deriving what the event already resolved.
    - **`function_call` beyond `exec_command`** — `shell` is the pre-0.1x
      spelling of the same `{command:[…]}` shape and yields the same `exec`
      record; `write_stdin` (the backgrounded-exec continuation poll) yields
      a light `stdin` record so its `function_call_output` is not orphaned (a
      presenter pairs the two by `call_id`); `request_user_input` — codex's
      EXPERIMENTAL question tool, plan-mode-only in practice, whose schema is
      Claude's AskUserQuestion in codex spelling — yields an `ask` record
      (`questions[{id, header, question, options[{label, description}]}]`)
      for a later question card. An unlisted name is `None`.
  - **Top-level records.** `type:"compacted"` (NOT the `event_msg`
    `context_compacted` notice the mirror paints as ⟳) is the compaction
    BOUNDARY itself → `compact_boundary` with `message` (usually `""`: the
    summary is encrypted), `window_id`/`previous_window_id`, and `replaced`,
    the LENGTH of `replacement_history` — the rewritten history itself is
    deliberately not carried into a record shape. `type:"world_state"` (a
    large periodic snapshot of open files / shell sessions / todos) is
    EXPLICITLY ignored rather than left to fall through, so the next reader
    of the table knows it was considered. Both spellings are handled: the
    fields sit under `payload` in the enveloped form and at the top level in
    the older bare-item one.
  - **Version fragility is the design constraint.** The grammar drifted
    across codex 0.95 → 0.144 in the local corpus (bare items → enveloped;
    `shell` → `exec_command`; `apply_patch` as a `function_call` then a
    `custom_tool_call`; `session_id` added; credits reshaped; the turn's
    `reasoning_effort` moved from a bare top-level `effort` under
    `collaboration_mode.settings`, and **both** are read). So an unknown
    `type`/`payload.type` and a missing field must always degrade to `None`,
    never to an exception — pinned by
    `test_unknown_shapes_never_raise`.
  - **Timestamps.** `task_started`/`task_complete` frequently carry NO
    `started_at`/`completed_at`; the ENVELOPE's `timestamp` is then the only
    clock, so both records carry it as a separate `ts` field. It is never
    folded into `at` — `at` is the numeric field the mirror footer subtracts
    for a duration, the envelope's is an ISO string.
  - **`token_count` keeps three things**, not one: the cumulative
    `total_token_usage` (the footer rollup), plus `last_token_usage` and
    `model_context_window`. The cumulative total NEVER resets across a
    compaction, so it is useless for saturation — a ctx bar needs the last
    turn's `total_tokens` over the window.
    The ROLLOUT side additionally renders, from codex's own event stream
    (shapes verified against real `~/.codex/sessions` rollouts, 2026-07):
    - **file ops** from `patch_apply_end` — the authoritative record (resolved
      ABSOLUTE paths + per-file `unified_diff`/`content`), one
      `Update(name) +a -r` / `Write(name) +n` / `Delete(name)` line per changed
      file in the Claude file-op look, each fed to the scoreboard exactly like
      a subagent's file ops (unique-path `files` set, ± line sums, Edit/Write
      tool tallies). The `apply_patch` call itself is deliberately NOT a file
      op — it only carries repo-relative patch text, and counting both would
      duplicate; it parses to the lightweight `patch_call`
      marker the mirror never paints (*Two registers* above). A
      `success:false` patch paints a red `■ patch failed` and bumps nothing.
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
    **Per-subagent codex streams** (was the roadmap item; landed once codex
    started emitting per-agent records): the 2026-07-07 survey found ZERO
    subagent/collab events, but codex-cli 0.146+ now emits them —
    `collaboration.spawn_agent`/`wait_agent` tool calls, `SubagentStart`/`Stop`
    hooks carrying an `agent_id`, and a **child rollout** whose first
    `session_meta` links back via `parent_thread_id` (`thread_source ==
    "subagent"`, plus a `source.subagent.thread_spawn{agent_nickname, agent_path}`
    block). A STANDALONE codex host's watcher discovers those children through
    that parent link (`watch.rollout_subagent`; `standalone_scan` streams every
    rollout whose parent is our SID, gated on creation time so a resume doesn't
    replay a prior run's subagents) and streams each **stamped**
    (`spawn(subagent=True)` → `$CLAUDE_OPS_SRC = codex:<nickname>`, NOT the
    standalone main-agent flag). Everything downstream is the existing subagent
    machinery: the stamped ops drop from the main-agent-only web mirror,
    `plugins.runs()` (plugins/codex/nested.py) mints the clickable agent card (its transcript ≠ the
    session's own, so the self-run empty-scope drop keeps it), and
    `read/mirror.agent_scope` matches `codex:<nickname>` to show the run's full
    activity + re-bubbled conversation — visually a Claude subagent, no
    codex-specific UI (*Sidecar → subagent parity* below). The companion log's
    `Subagent …` head (one `✎ sub` chip) still never occurs in any job log on
    disk.
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
      tears the session down when that pid dies — even on a hard Ctrl-C (which fires
      no hook at all). This is *more* robust than the Claude path: the pid is always
      a truthful end-of-session signal. Teardown now routes through the ONE
      host-teardown owner **`core.hostpane.host_end`** (the same door Claude's
      SessionEnd uses), passing the tab `win`, so besides parking the DB + closing
      the panes it also stamps **`session_end`** (the codex `sessions` row's
      `ended_at` used to stay NULL — a "stream never ended"-shaped gap) and clears
      the tab (the tab DB row + a `codex-clear` paint back to the theme default —
      the tab used to linger red/green forever). The standalone-host registry row
      (`core/tabs.codex_host_*`) is dropped last.
    - **Tab colours.** A standalone codex host colours its kitty tab through the
      shared paint engine, via codex's own hook events (`claude-codex-hook.py`) — see
      [tab-colors.md](tab-colors.md) › *Codex* for the event→state map, the
      standalone-only nested guard, and the `turn_aborted` interrupt-recovery
      watcher (codex fires no Stop on interrupt).
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

## Codex in the web dashboard (a first-class HOST + read source)

codex is a **host tool**, not only a mirror stream: it OWNS its rollouts and
answers the same read fan-outs a Claude session does (title, ctx bar,
conversation, the compact gate), plus two codex-specific ones. Every provider is
**read-side** and adds NO audit rows (like ctx/goal), except `usage_windows`,
which audits a degrade.

- **Ownership (`plugins/codex/rollout.owns`, the single-owner recogniser).** A
  `rollout-<ts>-<uuid>.jsonl` under a `sessions/` tree is codex's; anything else is
  not (a Claude transcript above all — a bare `<uuid>.jsonl`). It is a PURE
  filename/layout test (no file read): ownership must be answerable once per
  session per poll, and the `rollout-…-<full uuid>` stem is codex-specific, so the
  vocabularies cannot collide. This is what gates every path-keyed read fan-out
  (`plugins._first_path`) so a codex rollout never reaches a Claude parser and
  vice-versa, and what makes `plugins.owns_by(rollout)` name `codex` (so
  `session_caps` attributes the session to the codex host).
- **The codex HostControl (`plugins/codex/hostctl.CodexHost`).** `name="codex"`,
  `launchable=True`, `resume_words → ["resume", sid]`, and `launch_words(opts)` —
  the codex argv the web new-session launch composes: `resume <sid>` (+`-C`/`-m`/
  `-c model_reasoning_effort=`/prompt) or a fresh `-C <cwd> -m <model>
  -c model_reasoning_effort=<eff> "<prompt>"` (codex has NO `--effort` flag and NO
  `--continue`; verified against codex-cli 0.144.1). The command WORD `plugins.
  launch_argv` fixes is just `codex` (the base `HostControl.launch_cmd` default
  over `name` — codex has no account switcher, unlike claude_code, whose
  `launch_cmd` resolves the `c1`/`c2` alias). It drives its SUPPORTED control
  GESTURES (P5, *Codex control gestures* below) — interrupt/compact/rename/ask —
  so those caps read **True** and the dashboard un-greys their buttons; the ones
  it cannot drive (rewind/plan/migrate/model/effort) stay inert and read **False**
  (greyed). Launch/resume are lifecycle plumbing, NOT gesture-gated, so they work
  regardless.
- **The Claude screen-scrapes are the HOST's, so codex simply has none.** Once codex writes
  `awaiting-response`/`awaiting-command` tab rows (its tab producer, above), three
  Claude-GEOMETRY screen scrapes would start firing on codex panes and return
  garbage: the ghost-suggestion probe (`dashboard/read/session.input_box`) and the
  notifier's dialog-region + terminal-input reads
  (`dashboard/notify/notifier._dialog_region` / `_input_typed`). Each is now a
  METHOD ON THE OWNING HOST (`input_box` / `ask_region` / `typed_input`, P2) whose
  inert base returns nothing, so codex declines them by not implementing them —
  no name check anywhere, and a new host is silent by default rather than
  scraped through Claude's geometry. (An unprovable/empty path still resolves to
  the default host, so a daemon-origin Claude session is unaffected; the read
  model also skips resolving a frontend at all when the host declares no
  `input_box`, via `HostControl.implements`.) The host-agnostic alert signals
  (tab-moved / focus / composing) still resolve a codex alert, so **cross-session
  toast/Telegram/Web-Push notifications fire for a codex tab going red/green with
  no notifier change beyond this gating** — the codex `sessions` row carries the
  `kitty_window_id` env-stamped at SessionStart, which the notifier's winmap maps
  to the tab.
- **Read providers (`plugins/codex/read.py`, over `rollout.parse`).**
  - `context(path)` — the last `token_count`'s **`last_token_usage.total_tokens`**
    over `model_context_window` (the cumulative `total_token_usage` never resets
    across compaction and must NOT be used), plus `model` AND `effort` from the
    last `turn_context` (the reversed tail scan finds the NEWEST, so a mid-session
    `/model` or effort switch is reflected). Feeds the ctx bar on session/agent
    cards. `effort` on the ctx is codex's ONLY effort source — the dashboard
    prefers `ctx.effort` over `effort_default` for a codex session (below), so the
    ✧ label shows the run's REAL level rather than a stale cwd default.
  - `prompts(path)` — non-synthetic user `chat` turns (capped, fail-open None like
    Claude's), from the RESPONSE_ITEM register so a post-abort/queued prompt still
    counts. Feeds the ⊜ compact gate.
  - `conversation(sid, pos, agent_id)` — maps the RESPONSE_ITEM register onto the
    dashboard's conversation records: a non-synthetic `chat` → a `prompt` (user)
    or `message` (assistant) bubble, a `think` → a `message` bubble. Resolves a
    SIDECAR run by `agent_id` (`plugins/codex/nested.session_runs`) and the STANDALONE host's
    own thread otherwise. This is the core of sidecar → subagent parity below.
- **Titles (`plugins/codex/title.py`, the single-owner naming source).** codex
  keeps the session name in its per-machine sqlite index
  `~/.codex/state_<N>.sqlite` (`threads.title`, keyed by the thread uuid == the
  rollout uuid), NOT in the rollout — so `title_and_rename` returns an empty
  tail-rename (nothing in-file to reconcile the durable web-rename override
  against), `session_title` falls back to the first real user prompt, and
  `set_session_title` writes `threads.title` (the PARKED web-rename path; a LIVE
  rename is P5's `HostControl.rename`). The numbered filename is version-fragile,
  so the index is resolved by globbing the highest `N` and every read degrades to
  "" on any error.
- **`pending_dialog(sid)`** — the OPEN `request_user_input` (codex's plan-mode
  question) derived READ-side from the rollout tail: the newest `ask` record with
  no following `function_call_output` for its call id. The web question card's
  read surface (P5 drives the answer).
- **`usage_windows()`** — codex account rate limits over the APP SERVER (`codex
  app-server` JSON-RPC `account/rateLimits/read`, a bounded TTL-cached spawn that
  needs no live session — the stable source, unlike the nullable per-session
  `token_count.rate_limits`). Normalised to `{planType, windows:[{used_pct,
  window_mins, resets_at}]}`; degrades to None on any failure and audits it once
  (`A.error`), never raises. The spawn goes through **`usage.codex_spawn_env()`**,
  which PREPENDS the common node/codex install dirs to PATH: codex is a node
  script (`#!/usr/bin/env node`), and the launchd dashboard runs with a stripped
  PATH (`/usr/bin:/bin:…`) that finds neither `codex` NOR its `node` — so the
  spawn failed silently and the usage strip hid ("codex missing from the accounts
  list"). It's the `find_kitten` candidate-list idiom (existing dirs only,
  `$CODEX_BIN_DIR` overrides), and the ONE owner of "how a server-side codex
  subprocess finds its binary" — the P4 error channel reuses it.
- **effort** — a codex session's effort is a per-turn ROLLOUT fact, surfaced on
  `context().effort` (above) from the last `turn_context`; the dashboard's
  `data["effort"]` prefers it (`data["ctx"].effort or effort_default(...)`).
  `plugins.effort_default` (`model_reasoning_effort` from `~/.codex/config.toml`)
  is only the launch-form default — codex has no per-project/account effort config
  and never persists a `/effort`, so using it for a LIVE session showed a stale or
  foreign level (e.g. `high` on a `low` run).

### Tab states — wired + the exec colour

The codex TAB PRODUCER (`plugins/codex/tabstatus.py`) maps every codex hook event
to a colour (`UserPromptSubmit`→thinking · `PreToolUse`→executing/working/asking ·
`Stop`→green · `PermissionRequest`→red), over the shared `core/tabpaint` engine.
Two facts make it actually paint:
- **The events must be WIRED.** `~/.codex/config.toml` needs `[hooks] hooks =
  true` AND `~/.codex/hooks.json` must route all NINE non-SessionStart events to
  `claude-codex-hook.py` (docs/wiring.md). With only `SessionStart` wired the tab
  never changed — the mapping code ran for nobody (zero `tab_transitions` for any
  codex session, the tell in the audit).
- **codex sends CLAUDE-COMPATIBLE tool names.** A shell command's `PreToolUse`
  `tool_name` is `Bash` (not `exec_command`), so `EXEC_TOOLS` includes the
  claude-compat `Bash`/`Task`/`Agent` (blue executing, mirroring claude) beside
  the codex-native spellings; `ASK_TOOLS` likewise includes `AskUserQuestion`.
  Changing `hooks.json` re-triggers codex's "Hooks need review" trust prompt (the
  hash changed) — expected on the next launch, one-time.

### Codex control gestures (the P5 write plane)

codex drives the control buttons it CAN — `interrupt`, `compact`, `rename`,
`ask`, and `plan` — through its `HostControl` (`plugins/codex/hostctl.CodexHost`).
Overriding each gesture is what flips its DERIVED cap **True** (plugins.host), so
the dashboard un-greys those buttons and `_caps_guard` lets them through; the ones
codex cannot drive stay inert and read **False**: no `rewind` (codex has no
checkpoint menu), no `migrate` (no account switcher), no `model` (codex's `/model`
is an INTERACTIVE picker, not a `/model <arg>` we can drive blind — deferred), no
`effort` (a launch-time `-c model_reasoning_effort` only, no live `/effort`).
`send` IS overridden (P2) even though nothing gates it — see below.

The dashboard's control handlers route EVERY session through its owning host
(`_gesture_host(sid)` — `plugins.owns_by` → the host object, the DEFAULT host for
an unprovable path). Until P2 that was a branch with an inline Claude body on the
other side; those bodies now live in `plugins/claude_code/hostctl.py` with the
five Claude screen drivers, so codex and Claude Code reach their TUIs through the
same seam and each gesture writes its OWN `web-*` audit row. The gesture bodies
use ONLY the
frontend (`fe.paste_text`/`send_key`/`get_text`) + codex's own `rollout.parse`,
never dashboard code — so the whole gesture, screen driver included, sits behind
`HostControl` and a future codex **app-server transport** (turn/interrupt,
thread/name/set, the request_user_input reply) replaces the screen-drive without
touching the dashboard.

- **`interrupt`** — a **SINGLE Escape** (codex's composer is NOT modal like
  Claude's vim, so no double-Esc), VERIFIED by the `turn_aborted` RECORD appearing
  in the rollout: codex fires **no Stop hook**, no take-back to the input box, and
  gets **no escape-recheck** (all Claude-only). The gesture reads the rollout size
  before the press, then polls (bounded, one retry — a single synthesized Esc is
  only ~2/3 reliable per kitty window) for a `turn_aborted` record matched through
  `rollout.parse` (never a raw byte scan). A QUEUED message delivered right after
  the abort (`task_started` + `prompt`) is a **STEER** — reported `steered=True`,
  the ⧗ chip draining via the normal conversation reconciliation, NOT a plain
  stop. Result `{status, ok, verified, steered, tries}`: **acknowledged** when the
  record was seen, **indeterminate** (audited `codex interrupt (no turn_aborted)`,
  the *codex web interrupt not confirmed* anomaly) when the Esc landed but nothing
  appeared, **rejected** when nothing could be pressed. The `web-interrupt` row
  carries `host:codex` + `verified` + `steered`.
- **`send`** (P2) — a PLAIN bracketed paste, and deliberately nothing else. It
  is not caps-gated (the composer is always reachable), but it is a real gesture
  now, and the override exists so that reachability is honest: without a body the
  inert base would answer `unsupported` and 409 every codex message. Each thing
  Claude Code's send does AROUND the paste is a declared absence here — no
  clipboard-image wipe (`paste_grabs_clipboard_image` is **False**; codex's TUI
  does not auto-attach the board, so that ~150 ms osascript round-trip ran on
  every message for nothing), no Ctrl+U/Ctrl+K line kill (`clear_input` stays
  inert — codex's composer is a different input model and blind line-kill
  keystrokes into it are a guess), no `tui-draft` stash to consume, and no
  screen-delta `turn_live` probe (its liveness has a better source in the rollout;
  the inert `None` means "trust the tab", which is what codex did before).
  Attachments arrive as BARE PATHS: `mention(path)` is `""` for codex, because
  `@path` is Claude Code's TUI grammar and a foreign sigil would land as literal
  text.
- **`compact`** — paste codex's own `/compact` (fires Pre/PostCompact); no
  Claude switch-confirm menu, no clipboard-image guard (codex doesn't auto-attach
  a clipboard image on paste).
- **`rename`** — a LIVE `/rename <name>` paste (the title lands in
  `~/.codex/state_<N>.sqlite threads.title`); the PARKED path stays P3's
  `title.set_session_title`. Works live AND parked, both gated by
  `plugins.renameable`.
- **`ask`** — drive codex's own `request_user_input` dialog. Its geometry differs
  from Claude's (a `Question N/M` header, numbered options with a `›` cursor, an
  `enter to submit answer` footer), so Claude's `askdialog.region()` returns "" on
  it — codex needs its OWN driver, **`plugins/codex/dialog.py`** (the single-owner
  codex dialog driver, sibling of `plugins/claude_code/askdialog.py` but in the PLUGIN
  because the gesture drives it and a plugin can't import the dashboard). It walks
  the `›` cursor with DOWN/UP onto the chosen option and presses ENTER per
  question, screen-verified each step; a step that never verifies degrades to
  **indeterminate** with the dialog LEFT OPEN (never Escape-closed — codex's Esc
  ABORTS the turn). The web question card renders codex's `pending_dialog` through
  the SAME `data["ask"]` a Claude ask uses (`read/session.ask_pending` is
  host-aware: a codex session with no hook-stashed `ask-pending` kv derives its
  open request_user_input from the rollout tail via `plugins.pending_dialog`), and
  the same card JS (`{header, question, options[{label, description}]}`) renders
  it. `request_user_input` is plan-mode-only and model-nondeterministic (the model
  sometimes answers in prose instead of raising the tool), so the card appears
  rarely — that is expected, not a gap. Codex's "chat about this" / free-text notes
  are best-effort (no codex analog to Claude's decline).
- **`model` / `effort`** — codex has NO `/model <arg>` and NO `/effort`: both axes
  are set through ONE interactive 3-step `/model` picker, driven by
  **`plugins/codex/modeldialog.py`** (Step 1 `Select Model` → `All models`; Step 2
  the model list; Step 3 `Select Reasoning Level` — the same `›`-cursor / `enter to
  confirm` geometry as the plan picker). Step 3 is model-dependent: some models
  (gpt-5.6-sol) list all six levels directly, others (gpt-5.6-terra) collapse
  **Max/Ultra** behind a `More reasoning…` row that opens an `Advanced Reasoning`
  sub-step — the driver (`_pick_level`) picks a level directly when present, else
  opens that sub-step and picks there (a reported effort→max failure: it only
  looked for a direct `Max` row). The ✦ **model** button changes the model
  and PRESERVES the current reasoning level (the ✦/✧ axes are independent, so a
  switch must not silently reset effort — the gesture reads the current effort from
  the rollout, `read.codex_effort`, and re-selects it at Step 3, falling back to
  the new model's default only when the effort can't be read); the ✧ **effort**
  button KEEPS the current model (its `(current)` row) and changes only the level. `dashboard/http/post/typing.post_command` routes both through
  `HostControl.model`/`effort` (no `/model <arg>` paste, no Claude
  confirm menu — the gesture screen-verifies its own steps), and the arg is
  validated by the LIVE picker (label-matched, `Extra high` for the `xhigh` token),
  not Claude's `MODEL_ARG_OK`/`EFFORTS`. The ✦/✧ MENUS offer codex's own
  vocabulary: `CodexHost.model_choices()`/`effort_choices()` (from
  `modeldialog.MODEL_CHOICES`/`EFFORT_CHOICES`) ride the session payload
  (`data["model_choices"]`/`["effort_choices"]`), and the client's `hostChoices()`
  uses them when present, else its Claude defaults — so a codex session's picker
  lists `gpt-5.6-sol/terra/luna/…` and `low…ultra`, no codex-specific menu code.
  Verified live: a switch to `gpt-5.6-terra` (accepting its default `medium`) and
  an effort change to `xhigh` keeping the model.

**What codex REFUSES, and how** (P2). Three answers the dashboard used to give
wrongly for codex are now the host's own declarations, each surfacing as a 409
that names the vocabulary instead of a foreign command or a silent drop:

| request | codex's answer | what it used to do |
|---|---|---|
| `POST /command {"cmd":"rename"}` (the ✦ auto-rename) | **409 `cap:"rename"`** — codex's `/rename` takes a NAME; `autoname` is a cap SHARER it declines | bracket-pasted Claude Code's argless `/rename` into codex's composer, plus a clipboard wipe it never needed |
| `POST /answer {"chat":true}` | **409** — `ask_declines()` is `()`; codex's dialog has no decline row (its Esc ABORTS the turn) | the flag was silently dropped and the question got ANSWERED instead of dodged |
| `POST /plan-decision {"feedback":…}` | **409** naming `digit+label or dismiss` — `plan_decisions()` is `("decide","dismiss")`, its picker has no free-text row | a generic 400 "no action", and the text the user typed vanished |

`rewind_modes()` is likewise `()` (codex has no checkpoint menu) and
`title_key(tpath)` is its rollout's `.jsonl` stem, so a PARKED codex rename gets
the same durable prefs override a Claude one does, under a key its own host
derived. `lifecycle_end` is a documented no-op: a web-closed codex tab kills the
codex process and this plugin's watcher notices its host pid is gone and runs
`core.hostpane.host_end` on its own — the same teardown a terminal-side exit
takes.

### Launching & resuming codex from the web

The new-session form's **tool picker** (claude_code / codex, `GET /api/hosts` →
`plugins.hosts`; the row hides on a single-host machine) chooses the host for a
FRESH launch; a RESUME is routed by the OWNING host instead — `post_new_session`
resolves `plugins.owns_by(transcript)` and composes through THAT host's
`launch_words`, so a parked codex session comes back with `codex resume <sid>`
while a claude one stays `claude --resume <sid>` (byte-identical to before). The
form's model/effort ride a resume only when the picked tool MATCHES the owner — a
codex resume must never receive a claude `--model` — so the common `resume & send`
(default tool = claude) drops them and codex keeps its own model. codex's model
options are the `gpt-*-codex` family (an empty "codex default" leaves `-m` off),
its effort is the `-c model_reasoning_effort` level (low/medium/high), and the
account picker HIDES for codex (no subscription switcher). The web-launch audit
row names the launching `tool`. An owner the dashboard can't launch (an unclaimed
transcript, a tool with no host) is a 409, never the wrong tool.

codex's model options are the `gpt-5.6-sol`/`terra`/`luna` + `gpt-5.5` + `gpt-5.4`/
`-mini` family (`gpt-5.6-sol` the default) and its effort is the FULL codex enum
`low/medium/high/xhigh/max/ultra` (server `EFFORTS`) — every option EXPLICIT, no
"codex default" pseudo-option (matching Claude's dropdowns, so you always know
what you launched). The old `gpt-5-codex`/`gpt-5.1-codex` options are GONE: they
400 on a ChatGPT account ("model not supported when using Codex with a ChatGPT
account", reproduced live) — a dead turn with no assistant reply, which read on
the dashboard as an empty session. Both `-m` and `-c model_reasoning_effort=` now
always ride a fresh codex launch (no reliance on the config default).

### Codex in the usage strip (one payload, one painter)

Codex contributes ONE row to the dashboard's single usage strip — not a strip of
its own. `plugins/codex/usage.usage_strip()` maps the app-server rate limits
above into the shared usage-window vocabulary (`plugins.usage_strip`,
docs/dashboard.md *One usage-window vocabulary, every host*) and the row rides
`GET /api/accounts` beside the Claude accounts.

What makes it a different KIND of row is two fields: `switchable: False` (codex
has no account switcher, so it is not something the new-session picker can offer
you a launch under) and an empty `slug`. The account-switcher fields
(`usage`/`limit_hit`/`logged_out`) are served as the honest empty so one painter
reads every row the same way. Labelled `codex · <planType>`; no windows (codex
unconfigured/unreachable) means no row at all, rather than an empty pill.

Codex names a window by its DURATION, because that is all it reports — there is
no key like Claude's `five_hour`, just `primary`/`secondary` and a length in
minutes. The WORD for that duration is shared, not codex's own:
`usage.window_label` asks `plugins.window_label(mins)` first — 300 → `5h`,
10080 → `7d` — and falls through to codex's own ladder (`_derived_label`:
1440 → `1d`, 20160 → `2w`) only for a duration that table does not name. This
row used to say `1w` where Claude said `7d` for the very same 10080 minutes, on
the argument that each host should speak the way its own UI does; the strip
lays its columns out BY DURATION, so those two bars are ONE column
(docs/dashboard.md *Row alignment*) and it read as a column that renames itself
halfway down. Every codex
window is account-wide (`scope: "account"`), so each keeps its own reset column;
codex reports no per-model cap. Percentages are rounded server-side (codex
reports floats, Claude ints — the painter should not have to know which).

This used to be a whole parallel surface: `/api/codex-usage`,
`codex_usage_payload`, `renderCodexUsage`, `#codexusage`, its own CSS and its own
poll, on the argument that "an account registry and a single host reading are
different shapes". They are — by two fields. The split's real cost was that the
codex strip had NO SSE channel (the `accounts` event carries the accounts
payload), so its bars moved only on the 60s fallback poll while the Claude ones
moved live. Folding it in fixed that for free.

### Per-session rate limits (the rollout probe)

The app server answers for the ACCOUNT as it stands NOW, which is the right
answer for the list strip and the wrong one for a session you are looking back
at. So a codex session's own limits come from its ROLLOUT:
`plugins/codex/read.usage(path)` — a bounded tail scan, the same probe family as
`context()`, behind `plugins.session_usage(sid)`. A PARKED run therefore still
shows where its limits stood, and a machine with no codex installed can still
open the session.

The scan looks for the last `token_count` whose **`rate_limits` is NON-NULL**,
not simply the last `token_count`. The field is nullable and codex emits usage
events without it, so "the newest event" and "the newest event that says anything
about limits" are different records. It is also NOT part of the parsed `usage`
record: codex emits a `token_count` with `info: null` on a RATE-LIMIT-ONLY event,
which `_ev_token_count` drops entirely (it has no `total_token_usage` to report)
— the limits ride an independently-nullable field of the same event, so
`rollout.rate_limits(payload)` reads them on their own.

Measured shape (rollout `019fb363`, 2026-07-30 — 12 of 12 `token_count` records
carried one): snake_case `used_percent` / `window_minutes` / `resets_at` (epoch
seconds) / `plan_type`, with `secondary: null` on a plan with one window. That is
the same information the app server returns in camelCase
(`usage._normalize`), and both are mapped to ONE codex-internal shape so a single
strip mapper serves both.

`plugins.session_account(sid)` is the minimal honest companion: no slug (there is
nothing to switch to, and a slug is what the migrate/launch paths key on), just
`{slug: "", label: "codex · <plan>"}` so the header chip reads `◈ codex · plus`.
A rollout naming no plan yields `{}` and the chip is absent — better than a bare
"Codex" claiming a subscription reading nobody has.

### Codex session costs come from its own scoreboard

`plugins.session_costs(sid)` for a codex session reports its state-DB scoreboard
counters, already priced by `CODEX_PRICES` when the stream folded each turn.
Codex never reaches the audit `otel` table — that receiver is Claude Code's
telemetry, and its `query_source` split (main/subagent/auxiliary) is Claude
Code's taxonomy — so the OTEL sum that answers for a Claude session returned a
truthful-looking `total_usd: 0.0` for a codex run that really cost money. The
envelope is the same; the single `query_source` is named for the host, since
codex has no such split to report. (The corpus-wide Stats page still sums OTEL
only — documented in docs/dashboard.md.)

### The "/" menu speaks codex (host-scoped slash commands)

A codex session's composer completes against **codex's** slash commands, not
Claude's. `plugins/codex/commands.py` is the codex twin of
`plugins/claude_code/slashcmds.py`: a curated `BUILTINS` snapshot of the codex
TUI's palette (`/plan`, `/approvals`, `/review`, `/new`, `/init`, `/compact`,
`/undo`, `/diff`, `/mention`, `/status`, `/usage`, `/skills`, `/mcp`, `/logout`,
`/quit`, `/model`) plus discovered `$CODEX_HOME/prompts/*.md` user prompts (the
codex analog of a user-level `.claude/commands` dir). The fan-out
`plugins.slash_commands(cwd, host)` is now **host-scoped** (`_named` routes to
the one owning plugin) — a codex session gets exactly codex's vocabulary, never
Claude's `/goal`/`/rewind` mixed in, and vice-versa. The composer passes its
`sid`; the server resolves the owner via `owns_by`. Same authority model as
Claude's: the TUI executes the command, the menu only completes against it, so
`BUILTINS` drift is harmless. This closed the reported "`/plan` isn't
recognized" gap (docs/dashboard.md *The "/" menu*).

### Plan mode — the plan card + the decision picker + Q/A

codex has plan mode (`/plan` / shift+tab) and it reaches **full parity** with
Claude's web plan flow — the plan card, real approve/reject buttons, and
clarifying-question support — even though codex exposes NONE of Claude's plan
machinery (no `ExitPlanMode` tool, no approval hook). Three verified codex facts
make it work:

1. **The plan is a STRUCTURED record.** A plan-mode turn ends with an
   `event_msg`/`item_completed` whose `item.type == "Plan"` carries the full plan
   as markdown (`item.text`) + a stable `id` — parsed by `rollout._ev_item_completed`
   into a `plan` kind (mirror-ignored: it's a card, not a block). Not prose.
2. **The decision is an on-screen PICKER**, not a tool. After the plan codex
   shows `Implement this plan?` with numbered rows — `Yes, implement this plan`
   (Switch to Default and start coding), `Yes, clear context and implement`, and
   `No, stay in Plan mode` — the SAME `N. label` / `›`-cursor / `enter to confirm`
   geometry as the `/model` picker, and driveable the same way.
3. **Pending is read-side.** `read.pending_dialog` returns whichever modal is
   open (ask OR plan) with a `kind`: a `Plan` item is pending until a newer
   `task_started`/`user_message` decides it (clicking implement opens a fresh
   default-mode turn; typing a follow-up starts its own turn). This drives the
   card exactly as Claude's `plan-pending` kv does — `session.plan_pending` is
   host-aware (the `_host_dialog` helper it shares with `ask_pending`), so a codex
   plan renders in the SAME plan card, titled by the host label.

**The card + gesture.** The card paints codex's two APPROVE rows directly from
the pending read model (`plandialog.APPROVE_OPTIONS` — static; the picker is pure
TUI, re-verified live at decide time), so no screen read is needed to render;
`keep planning` maps to the `No, stay in Plan mode` row (an explicit choice, not
an Esc — codex's Esc only steps BACK). codex's picker has no free-text
"what to change" row, so the card hides the feedback box off-Claude. A decision
POSTs `/plan-decision`, routed through `HostControl.plan` (keyed on
`plan_id` not `tool_use_id`) → `plugins/codex/plandialog.py`, which navigates the
`›` cursor to the label-matched row (label-keyed, so codex reordering can't press
the wrong one) and ENTERs, screen-verified.

**Clarifying questions (full Q/A).** In plan mode codex may raise a
`request_user_input` dialog FIRST — often **multi-question** (`Question N/M`).
`pending_dialog` surfaces every question, and the web ask card drives them all
through `HostControl.ask` → `plugins/codex/dialog.py`, one Enter-advance per
question. The last question's footer switches from `enter to submit answer` to
`enter to submit all`; the driver's footer detector matches the common
`to submit` stem so a single `drive()` carries through to the final submit (a
live-verified bug: keyed on the exact `submit answer`, the driver bailed on the
last question, leaving it unanswered). Verified end-to-end against a real
plan-mode session: two multiple-choice questions asked, both answered from the
web, then codex produced the plan and its decision picker.

### View modes — a codex run is its own act

A codex block wears the **codex palette** (`core/slots.CODEX_PALETTE`, disjoint
from every other), so `opshtml.actclass` classifies it `ACT_CODEX`. This is the
right treatment for a SIDECAR codex run inside a Claude session — a foldable
sub-run, like a subagent (`ran N codex runs`). It is the WRONG treatment for a
STANDALONE codex session, where the codex activity IS the session — see below.

### Session-state facets: the hook dispatcher grows a second subscriber

`plugins/codex/dispatch.py` used to fan its nine events out to exactly one
subscriber, the tab producer. It now has two, and the new one —
`plugins/codex/facets.py` — owns codex's answers to the dashboard's
**session-state facets** (`plugins.compacting` / `plugins.fg_running` /
`plugins.tasks`; the shared design is docs/dashboard.md *Session-state facets*).

**`compacting` — from the hook pair.** `PreCompact` arms a `{ts, trigger}` kv
latch, `PostCompact` clears it, exactly as `plugins/claude_code/compact_fmt.py`
does; the web's ctx bar breathes for a codex compaction the same way it does for
a Claude one. The hooks are the only signal there is — a compaction emits no
tool call, no reply and no rollout growth for its whole duration — and the
rollout's `context_compacted` record is only the after-the-fact crosscheck. The
TTL that ages an un-cleared latch out lives with the READER
(`config.COMPACT_MAX_S`), because an interrupted codex compaction fires no
closing hook either and an animation must fail OFF.

Ordering inside `route()` is deliberate: the facet step runs **before** the tab
step, and outside its `fe.usable()` check. A latch is a state-DB write with
nothing to do with a window; behind the frontend resolve it would be lost to a
terminal that failed to resolve, for no reason.

**`fg_running` — from the ROLLOUT STREAM, not the hook.** The record must name
the mirror block the ⏱ chip ticks on, and the hook cannot: measured 2026-07-31,
the hook's `tool_use_id` (`exec-<uuid>`), the rollout's `call_id` (`call_<…>`)
and the block's copy group (an `ops.new_group()` integer) are three disjoint id
spaces. Claude Code stamps from its hook only because *its* `tool_use_id` IS the
copy group. So `stream.py`'s standalone `_ro_exec` calls `facets.fg_open(LOG,
gid, ts)` right after painting the block and `_exec_close` calls `fg_close` right
before the finish chip; the stream's own pid is the liveness backstop for a turn
aborted mid-exec, which writes no closing record and fires no hook
(`turn_aborted` is a rollout note, not an event). There is deliberately **no
`tool_name` allowlist** for "which codex tools are the foreground command": the
rollout's `exec` record is the shell family by construction, and the hook's
`exec-` id prefix is not a shell marker (`webrun` and the MCP tools wear it too).

**`tasks` — DECLINED.** No task-list tool exists in codex's rollout vocabulary
(`exec_command` / `write_stdin` / `wait` / `spawn_agent` / `wait_agent` /
`send_message` / `apply_patch` / `exec`, over an 80-rollout corpus) or in its
hook `tool_name`s. There is no material, so the provider is absent and the card
stays presence-hidden — the honest answer, recorded as a DECLINED cell in
`tests/test_l1i_host_contract.py`'s coverage matrix rather than as an absence
nobody notices. **Monitors and background jobs decline for the same reason**: no
codex mechanism exists, so those counts stay an honest zero rather than fake
data. (codex's long-running `write_stdin`/`wait` execs are the raw material a
jobs analog would be built from, if one is ever wanted.)

**The nested guard covers both halves.** A `codex exec` inside a Claude session
fires these hooks too, and its LOG is the CLAUDE host's state DB — where both
keys belong to Claude's own hooks. So the hook half stays behind the existing
`tabs.codex_host_win` standalone gate, and the stream half runs only in the
`REG_STANDALONE` register. An `agent_id` on the event is ignored as well (a
child has no compaction of its own — `compact_fmt`'s own rule).

### `plugins.on_session_start` finally runs for a codex host

`plugins/codex/session.py` used to `subprocess.run` the watcher launcher
directly, so the SessionStart plugin fan-out — which
`plugins/claude_code/split.py` has always called — **had never run for a codex
session at all**. Two consequences: every cross-cutting plugin was invisible to a
codex host (today that is `plugins/otel`, whose provider spawns the per-machine
OTLP receiver only under `CLAUDE_CODE_ENABLE_TELEMETRY` — harmless, and a codex
session that sets it now joins the same singleton every Claude session shares),
and codex's OWN `on_session_start` provider was dead code on the one path it most
obviously belonged to.

`session.py` now calls `plugins.on_session_start(log, cwd, sid)` and nothing
else. The watcher is still started exactly **once**, because the provider decides
which of its two roles to start rather than the caller: `watch.py` selects the
STANDALONE host manager over the secondary discovery watcher by whether a
`HOST_PID` rides `argv[4]`, and the provider reads the standalone-host mark
(`tabs.codex_host_mark`, stamped by `session.py` just above the call) to know
which this is, appending `session.codex_pid()` itself when it is a host. Both
hosts therefore call the same one line, and the fan-out's audit-and-swallow means
a failing plugin can never break a SessionStart.

### Only a codex IN A KITTY WINDOW gets a mirror (the ChatGPT-app skip)

`plugins/codex/session.py` stands up a mirror + scoreboard + `sessions` row only
when the codex is running in a REAL kitty window — resolved BEFORE `A.session_start`
as its `KITTY_WINDOW_ID` env (a codex tab always carries it; codex is not
daemon-spawned) OR a prior `claude_session=<sid>` tag (a resume into an existing
tab). A codex started OUTSIDE kitty has NEITHER and the handler SKIPS the whole
lifecycle (audited `no kitty window (headless / ChatGPT app) — skip`). This is the
codex twin of Claude's daemon-origin skip (CLAUDE.md): the **ChatGPT desktop app
runs the codex CLI**, which shares `~/.codex/hooks.json`, so our SessionStart fires
for it too — and without the guard it wrote a `sessions` row and painted a mirror
into whatever kitty tab happened to be focused (a phantom dashboard card for a
session that isn't in the terminal at all). The old "anchorless standalone start
is still the user's own tab" fallback was removed — it predated the app case.

### Standalone mirror parity (a codex session reads like a Claude session)

A standalone codex session's dashboard view must look like a Claude session's:
your message + codex's reply as conversation BUBBLES, real activity (commands,
file ops) inline — never a session that shows only `■ codex cli ended` + "ran N
codex runs". Two bugs made it show nothing:

1. **The conversation was empty.** `plugins.conversation` is a first-non-None
   `_first`, and `claude_code` is asked FIRST — it resolved the session's codex
   rollout, parsed it as a Claude transcript, and returned `[]` (a non-None
   answer) that SHADOWED codex. Fixed by an OWNS GATE in
   `transcript.conversation_for`: it returns None for a transcript it doesn't own
   (the same `owns()` predicate the path-keyed fan-outs use), so the fan-out
   reaches codex. AND codex's `read.conversation` was reading only the
   response_item register (`chat`/`think`) — but an interactive `codex` writes a
   turn's prose ONLY in the event_msg register (`prompt`/`message`/`reasoning`),
   so it returned nothing. It now reads BOTH registers, de-doubled by text (codex
   writes each turn in both), and stamps the assistant bubble `who="codex"` so
   the reply reads "codex", not msg_html's default "claude".

2. **The prose folded into "ran N codex runs".** The prose OPS (⇢/✎/⋯/⇠) still
   wore the codex palette → `ACT_CODEX` → folded. Both the prose and the codex
   CHROME (the `codex ▶ <label>` banner, the `⚙ model` tag, the run footer) are
   dropped from the session view, exactly as agent scope drops an agent's prose:
   the prose comes back as bubbles (1), and the banners are sub-run scaffolding a
   standalone session doesn't need (the model shows in the scoreboard). Command
   and file ops STAY.

   It took a per-host flag to do that — `op_items(codex_lead=True)`, later
   `host_lead=`, resolved from the owning host's `lead_prose` trait. It no longer
   does, and no host declares anything: a LIVE standalone run emits no prose ops
   at all (`stream.py` returns early in that register), the runs that DO emit
   prose stamp `bubbled` on it, and the frame is stamped `chrome` — three
   producer-side facts where there was one host-side flag. What survives is
   `actclass.codex_prose`/`codex_chrome` as PARKED-history sniffers, palette-
   gated so they match only codex's own unstamped ops, and frozen: a new host
   stamps the flags from its first op and needs no sniffer of its own. Measured
   over the 187-session parked corpus, the session view is byte-identical for
   every session, the 25 standalone codex ones included.

### Standalone command parity (a codex command reads like a Claude command)

The prose fix above still left a standalone codex session's COMMANDS wearing the
codex palette, so a command-heavy session folded them into "ran N codex runs" and
the block itself was a bare one-line chip (no output, no exit, no elapsed) — the
"foreground command is not showing up in the same style" report. The fix paints a
standalone codex exec **exactly as Claude's own foreground block**, reusing the
SAME components so a future change lands in one place:

- **The block shape is shared.** `core/streamfmt` grew `command_open` /
  `command_close` / `command_block` + `finish_chip` + `no_output_body` + the
  semantic colour names `CMD_OK`/`CMD_BG`/`CMD_FAIL` — extracted from
  `cmd_fmt._render_finished`, which now paints through them (byte-identical; the
  L2 command goldens are the pin). `plugins/codex/stream.py` paints through the
  same builders, so Claude's Bash block and codex's exec block are one anatomy.
- **Semantic colours, not the codex palette.** The header is `▶ foreground` in
  slate, the outcome (slate ok / red failed / orange interrupted) rides the
  gutter + `■ finished · Ns` chip — the Claude LIVE-block split, opened the
  instant the `exec` record lands (so a long-running command shows at once) and
  closed when its `exec_result` (matched by `call_id`) appends the output + chip.
  Because the block wears a SEMANTIC colour, `actclass` reads it as `ACT_BASH`
  (ordinary command activity) — no fold, no web special-casing, the renderer
  auto-paints the ⧉cmd/⧉out links onto the g-tagged header.
- **It times itself.** A codex exec carries no duration, so `rollout.parse` now
  stamps the ENVELOPE `ts` on the `exec`/`exec_result` pair (the same `ts` the
  task-lifecycle records already carry); the block subtracts exec.ts →
  exec_result.ts for the elapsed, `?` when the clock is missing.
- **Both exec channels feed it.** The command must first PARSE to an `exec`
  record. codex ≥ 0.14x runs `exec` as a `custom_tool_call` (a JS
  `tools.exec_command({cmd:…})` snippet), not the older `function_call`
  `exec_command` — the parser now recognises BOTH (the custom_tool_call exec
  channel, above); the
  first live standalone session showed NO command block at all because the
  custom-tool channel was unparsed, so the block gets no data no matter how it is
  painted.
- **The codex run FOOTER is dropped too** — alongside the banner + `⚙` tag. A
  Claude session has no per-session footer, and its token rollup is redundant
  with the scoreboard, so it was exactly the codex-specific chrome to remove
  ("no codex specific ui"). A live run stamps all three `chrome` and every web
  view drops them; `actclass.codex_chrome` matches the same three shapes for ops
  written before that flag.
- **Gated on STANDALONE.** The watcher passes `CLAUDE_CODEX_STANDALONE=1` in the
  stream's env for a standalone host (where codex IS the main agent). A codex run
  INSIDE a Claude session is being folded into the SUBAGENT abstraction — no
  codex-specific UI, it renders like any subagent (*Sidecar → subagent parity*
  below); until that lands it keeps the per-run palette chip. An ORPHAN
  `exec_result` (a backgrounded `write_stdin` poll's output, whose `call_id` is
  the stdin call's) has no open block, so — as before — only its failed exit is
  surfaced; richer backgrounded-exec rendering (the `stdin` kind) stays a
  follow-up.

### Standalone streams the whole session (never ends on a per-turn grace)

A codex run's stream (`plugins/codex/stream.py`) was built for a discrete SIDECAR
task (a `/codex:review`, a `codex exec`): it ends `CLAUDE_CODEX_GRACE_S` after the
last `task_complete` with no new turn, folds the run's cumulative tokens, and
paints the `■ codex … ended` footer. For a STANDALONE host that is WRONG — the
rollout is the whole multi-turn session, so ending on the first turn's grace froze
the mirror: the stream exited, the standalone watcher never respawns it
(`standalone_scan` streams a uuid once), and every later command went unstreamed.
Found by self-testing (a session that sits idle between commands), invisible to a
single-command check.

Fix — a STANDALONE stream tails until SESSION END, like a Claude mirror:
- The loop skips the task-complete grace AND the stuck-run backstop when
  `STANDALONE`; only the parked state DB (session end / host-pid teardown) stops
  it. No per-task footer is emitted (it exits at the parked branch, which returns
  before the footer — and the footer is dropped on the web anyway).
- Tokens fold INCREMENTALLY instead of once at that footer: `_ro_usage` folds each
  `token_count`'s DELTA over what's already folded (the totals are cumulative), so
  the scoreboard stays live across turns — the same shape the OTLP receiver gives
  a Claude session. `_fold_bump` is the one owner of that bump; the sidecar footer
  still calls it once with the cumulative total (unchanged). A SIDECAR run is
  untouched — it still ends on grace with its footer.

### Host-labeled UI copy (no hardcoded "Claude")

The dashboard was built for one host, so a pile of user-facing strings hardcoded
"Claude" — the ask-card title ("claude is asking"), the auto-rename tips, the
rename-sent toast, and the new-session pending/fail/placeholder copy. On a codex
session those read WRONG (a codex question titled "claude is asking"), and the
launch-failure card blamed "claude" even when the tab running is codex.

The fix routes every one of those through the OWNING host's label:
- **`read.session.host_label(tpath)`** — the display label ("Codex" /
  "Claude Code") of the host that `owns_by` claims, defaulting to the default
  host for an unprovable/empty path (the same "behave as today" rule
  `session_caps` uses). The session payload carries it as **`meta.host_label`**;
  the client reads it for the ask card + rename/send tips. Distinct from
  `session_caps`'s host NAME, which stays "" for a non-default tool (its
  attribution semantics) — the label wants the real owner regardless.
- **New-session copy** rides the PICKED tool's label (the form knows it before
  any session exists): `syncTool` repaints the first-prompt placeholder
  (`nsPromptPlaceholder`), and the launch stashes `show.toolLabel` for the
  pending "&lt;tool&gt; is booting" / "&lt;tool&gt; may have failed to start" cards.
- **Notification fallbacks** (the `alert_text` detail line, the SSE toast) have
  no host in the `entry`, and the title is only a FALLBACK for a missing session
  title, so they go host-NEUTRAL ("a question is waiting") rather than plumb the
  host through the notify path — correct for every tool.

The jsdom `newsession` verdict asserts the placeholder says "Codex" (never
"Claude") after the tool switch.

### Three registers: standalone / sidecar / SUBAGENT

WHO a codex run is to the session is the only thing its paint forks on, so the
stream names that fact once and reads it everywhere (`stream.py` `REGISTER`,
selected by the watcher through an explicit env flag each — the rollout itself
cannot say which role its watcher spawned it for):

| register | who it is | env | stamp | palette |
|---|---|---|---|---|
| `standalone` | a codex host on its OWN — the session's MAIN agent | `CLAUDE_CODEX_STANDALONE=1` | none (unstamped) | semantic colours |
| `sidecar` | a codex run INSIDE a Claude host | — | `codex:<aid>` | CODEX_PALETTE |
| `subagent` | a codex-NATIVE child of a standalone host | `CLAUDE_CODEX_SUBAGENT=1` | `sub:<aid>` | SUB_PALETTE |

STANDALONE paints like a main agent: no banner, no `⚙` line, no prose (that
comes back as conversation bubbles), commands in Claude's own semantic colours,
file ops as bare `line`s. SIDECAR is the historical shape: its own bracketed
sub-stream in the codex palette, banner and footer included. SUBAGENT is the
subject of the next section.

### A codex-native subagent IS a child agent (not a codex run)

**Rejected design: folding a native subagent into the codex-run vocabulary.** It
was the shape the code had, and it was wrong in a way that only shows in the
product. A codex subagent used to be stamped `codex:<aid>`, painted in the codex
palette, opened with a `codex ▶` banner, and classified `ACT_CODEX` — so the web
folded an agent's entire run into "Ran 1 codex run" no matter what it did, gave it
no launch card and no result card (its `⇢ prompt` came from a `chat`-register
record inside the trimmed parent prefix, so no op was ever emitted, and there was
no result twin at all), and rendered its tool calls as `▶ cmd` blocks of raw
JavaScript. Measured side by side against a Claude session running the same
prompt, the two were unrecognisable as the same kind of thing. The fold is
rejected because a native subagent is not a *run the lead made* — it is a CHILD
AGENT, exactly what a Claude subagent is, and the moment it says so every stage
downstream (scope, classify, quiet register, view-mode fold, the cards) covers it
with no codex special-casing. `codex:` now means exactly one thing: a SIDECAR run
inside a Claude host, which is the only thing the web still counts as
"ran N codex runs".

So the SUBAGENT register builds every block through **`core/agentblocks.py`** —
the same `AgentStream` the Claude substream paints its agent with, bound to this
run's identity (`label` = the agent nickname, `rgb` = its SUB_PALETTE colour,
`tags` = the model·effort `turn_context` keeps current, `agent_dur` = its task
timing). The mapping:

| rollout record | what it paints |
|---|---|
| the replayed-parent prefix | dropped (the gate below) |
| the child's bootstrap `task_started` | the LAUNCH CARD, once — `blocks.launch(subagent_brief(...))` |
| `turn_context` / `settings` | model/effort TAGS only — no `⚙` line (a Claude child has none) |
| `exec` | `blocks.cmd_open` + the pending ledger; closed by its result |
| `tool` | `blocks.tool_open` — the quiet `· <name>` block (see *A non-shell tool call* below) |
| `exec_result` | `blocks.cmd_close` / `tool_close`, paired by `call_id` |
| `patch` | `blocks.file_line` per file + the usual scoreboard bump |
| `message` | BUFFERED — see below |
| `reasoning` / mid-run `prompt` / `compact` / `search` | `blocks.reasoning` / `blocks.prompt` (a follow-up task, NOT a second launch) / `blocks.compact` / a `· search` tool block |
| grace end | `blocks.footer(state, dur, tok_rollup + cost)`; `_fold_bump` unchanged |

**The message is BUFFERED because the last one is the RESULT.** A child's final
message is what it returned, and that is a different block from an intermediate
one (the `⇠ result` card carries `web=1` and the `Agent "<name>" finished · <dur>`
note; a `✎ message` carries neither). Which one it is cannot be known when the
message arrives — only when something follows it. So the message is held and
committed by whichever comes first: the next record that OPENS a block
(`_FLUSH_BEFORE` — deliberately not "any record", because a `token_count` always
trails the last message and flushing on one would demote the result card to an
ordinary message), or `task_complete`, which flushes it as the RESULT. This is
the substream's `flush_msg` discipline, arrived at from the same constraint.

**Lifecycle:** a subagent is a discrete TASK, like a sidecar — the per-turn grace
end, the stuck-run backstop and the footer all apply. Only STANDALONE has the
never-ends rule (its rollout IS the whole session).

**`subagent_brief` — and where the brief actually is.** The launch card needs the
task behind its click, and the child's own NEW_TASK record cannot give it: codex
delivers the task as a `response_item/agent_message` whose plaintext is only the
envelope (`Message Type: NEW_TASK / Task name: /root/bali_weather / Sender: /root
/ Payload:`) — the payload itself is an `encrypted_content` part, so it is not
readable here at all (measured on the real cli 0.146 child rollout). What IS in
plaintext is the fork PREFIX: a subagent rollout opens by replaying the parent
thread, and the LAST REAL HUMAN TURN in that replay is the task the parent was
working on when it spawned the child. That is the closest available statement of
why the child exists, and it is what `rollout.subagent_brief` returns (bounded
head read, fail-open ""). The 2.1KB team-scaffolding message ("You are an agent in
a team of agents…") needs no preamble heuristic to exclude — it is `role=developer`,
codex's system channel, and carries no task text whatsoever, so the structural
synthetic rule already drops it along with `<environment_context>`. A
`<task>…</task>` delivery (the UNencrypted shape) is kept and unwrapped by the
shared `strip_input_wrapper`. The same brief is prepended as the first bubble of
the scoped `conversation()` — it cannot double with the card, because the card is
`bubbled`.

**The parity is CONTRACT-TESTED, not asserted.** `tests/test_l1h_child_agent_parity.py`
drives ONE synthetic sequence (launch → tool req+result → command ok → command
failed → file op → intermediate message → result → footer) through BOTH adapters —
`substream_render.Renderer` over transcript records and `stream.Renderer` in the
SUBAGENT register over rollout records — and compares them after normalising
identity away: same op kinds, same block markers, same `web`/`bubbled`/`chrome`/`lk`
stamps, same notes modulo the child's name, same copy-group topology, then the
DERIVED layer (identical `actclass.classify` sequences, identical keep/drop through
`op_items` in both views, identical quiet-register eligibility). Its docstring says
how a third adapter (opencode) passes it. Two differences are DECLARED: a codex
result carries an exit code where a Claude tool_result does not (`■ failed (exit 1)`
vs `■ failed`), and the tool NAME is per-host.

### Sidecar → subagent parity

A codex run launched INSIDE a Claude session must read like a subagent in agent
scope: its intermediate messages/reasoning/commands all visible. Four parts:
0. **The replayed-parent PREFIX is trimmed.** A subagent rollout OPENS with a
   burst replaying the PARENT thread's history as of the fork — two `session_meta`
   records (the child's `thread_source=="subagent"`, then the parent's), the
   parent's replayed turn(s), then the child's own work. Left in, that prefix
   DOUBLES the parent's prose + exec into the subagent's scoped mirror AND bubbles
   (the bug: clicking a subagent looked identical to the lead). `rollout.py` owns
   the boundary: the parent's replayed `task_started` carries a `started_at` from
   BEFORE the fork, while the child's OWN bootstrap `task_started` carries
   `started_at >= the fork` (`subagent_fork_epoch` = the child `session_meta`'s
   timestamp). Everything after that bootstrap task_started is the child's turn.
   TWO callers share the predicate `is_child_bootstrap`, applied in the shape their
   context needs (a deliberately-different pair): the live op stream GATES
   per-record (`stream.py` `Renderer.feed_rollout`, race-safe — each record
   self-decides as it arrives, so a burst still being written can't mis-cut); the
   web `conversation` seeks a byte OFFSET (`subagent_body_offset`, a random-access
   read of a complete file). Both fail OPEN (show everything) when the boundary
   can't be found, never an empty scope.
1. **Grammar** — the rollout `chat`/`think`/exec/patch records already parse.
2. **`conversation()`** — the run's PROSE becomes bubbles from its rollout,
   exactly as a Claude subagent's does.
3. **Prose-op drop via the unified `bubbled` FLAG.** In scope, a codex run's prose
   ops (`⇢`/`✎`/`⇠`/`⋯`) are dropped so the bubbles don't DOUBLE them, while its
   exec/patch ops STAY. The signal is `core/ops.py`'s **`bubbled`** field, set by
   the PRODUCER (`stream.py` `_ro_prompt`/`_ro_message`/`_ro_reasoning`) — and ONLY
   for a ROLLOUT-backed run, because only a rollout re-bubbles through
   `conversation()`; a **companion `.log`** run sets no `bubbled` and its prose
   stays as ops. `opshtml.op_items` drops a `bubbled` op directly. This is the SAME
   flag a Claude subagent's prose carries (`substream_render`) — ONE cross-tool
   signal, retiring the old codex-only `codexprose:<label>` scope marker and the
   per-tool `prose_block` arms (`prose_block` survives as the legacy fallback for
   parked pre-flag ops). See *The unified agent-scope render* in docs/dashboard.md.
4. **Clean input/output + working view modes** (a codex subagent must read AND fold
   like a Claude subagent):
   - **Clean bubbles** — its input is the real prompt, not codex scaffolding. The
     synthetic rule (*Two registers* above) is STRUCTURAL: role developer/system and
     role=user `<tag>` wrappers (except the `<task>` INPUT wrapper, kept + unwrapped)
     are dropped, so `<recommended_plugins>`/`<multi_agent_mode>` never bubble.
   - **A foldable LEAD card** — `_ro_prompt` sets `web=1` + a `Codex "<label>" ran`
     note (`streamfmt.codex_note`), so the run's `⇢ prompt` surfaces in the LEAD
     mirror as an `ACT_CODEX` card (the codex twin of a Claude subagent's launch
     card), which default folds into "ran N codex runs" and focus/verbose
     fold/expand — a delegating lead that was pure bubbles now has foldable activity.
   - **Tool activity in scope** — a NON-shell tool call is its own record and its
     own block (*A non-shell tool call is a TOOL* below), so a web/MCP lookup
     (`tools.web__run`) renders as the quiet `· <name>` block with its arguments
     behind the click; default folds it, verbose expands it.
   - **No terminal chrome on the web** — the run banner, the `⚙ model · effort`
     line and the `■ codex … ended` footer are stamped **`chrome`** by the
     PRODUCER (core/ops.py), and every web view drops them; the model + duration
     live on the agent card, exactly as a Claude subagent scope has no such inline
     lines. `actclass.codex_chrome` (a text sniff) survives as the legacy fallback
     for ops ALREADY ON DISK, which carry no flag — the same role `prose_block`
     plays beside `bubbled`.

### A non-shell tool call is a TOOL, not a laundered command

codex ≥ 0.146 runs MANY tools through the SAME `exec` custom tool: a shell command
is `tools.exec_command({cmd:…})`, but a web/MCP lookup is
`const r = await tools.web__run({…}); text(JSON.stringify(r))`. The parser used to
launder the second into the exec/command shape and hand the whole JS expression
over as the "command" — which is how a codex subagent's entire real work came to
render as `▶ cmd` blocks of raw JavaScript (measured: five such calls in the real
child rollout, not one shell command among them).

It is now its OWN record — `{"kind":"tool","name","args","call_id"}` — so a
presenter can paint the block every generic tool call in this repo gets: the quiet
`· <name>` with the ARGUMENTS behind the click and the answer behind the same one
(paired by `call_id`, because codex returns every custom-tool output through one
record that carries no tool name). `rollout.js_tool_call` cuts the args at the
call's MATCHING close paren, quote-aware: codex's wrapper tail VARIES per call
(`text(JSON.stringify(r))`, `text(r.content.map(x=>x.text||"").join("\n"))`), and
the previous fixed suffix list matched NONE of the five real calls, so the whole
`; text(…)` tail was riding into the rendered command.

STANDALONE paints it in the SEMANTIC command colour, so it classifies `ACT_TOOL`
and folds into the quiet `⏺` register like the lead's own activity. That is
deliberately MORE than the Claude lead shows — Claude's hooks paint no generic
tool block at all — because here the record exists, and hiding a run's real work
for symmetry would be the same mistake the laundering made by another route.

**`web_search_end` is where a codex search actually is.** The real child rollout
carries FIVE `web_search_end` events and ZERO `web_search_call` response_items, so
without parsing the event a codex web search rendered nothing. Only a search that
NAMES a query yields a record — four of those five are the web tool's non-search
actions (`action.type == "other"`, empty query). `search` is now the one kind BOTH
registers can answer, so the RENDERER collapses an immediately-repeated query
(adjacency, not a seen-set, so a genuine later repeat still gets its own block);
the parser keeps reporting what the file says.

**Known issue (undecided):** a custom-exec output whose preamble says
`Script failed` but carries no `Exit code:` / `Process exited with code` line is
NOT marked failed — the exit extraction is the only failure signal, and codex's
JS-runtime errors don't use it. The block still shows the error text, just without
the red mark. Pre-existing for exec blocks, inherited by tool blocks; left alone
rather than guessed at.

**Deliberately unparsed (future work):** `sub_agent_activity` (a
`{kind:"interacted"}` ping about a child thread — the child has its own rollout and
its own stream, so parsing it here would only duplicate) and
`inter_agent_communication_metadata` (`{trigger_turn:true}` — plumbing). Both are
measured and pinned by a test so the next reader sees a decision rather than a gap.
The interesting one is the NEW_TASK/`agent_message` channel behind them: if codex
ever emits those payloads in plaintext, inter-agent mail maps cleanly onto the
existing mail builder (`agentblocks.mail`, the `✉ from|to <peer>` block) rather
than onto anything codex-specific.

### The unified scope key — and the PREFIX is the register

A Claude subagent stamps its ops `sub:<aid>`/`team:<aid>` — the SAME id
`read/mirror.agent_scope` keys on. A codex run used to be stamped `codex:<label>`
(the display label) while its card's agent id was the rollout basename
(`paths.codex_aid`), so `agent_scope` had a codex-only branch that looked the label
up off the run's row — a mismatch there silently yielded an EMPTY scoped mirror.
Now `watch.spawn` stamps `<register>:<codex_aid(srcfile)>`, so the op stamp EQUALS
the agent id and `agent_scope` is one tool-agnostic rule — the ID ITSELF, matched
against `src.split(":", 1)[-1]`, no lookup and no prefix list. `paths.codex_aid`
is the single owner of that id (both the producer and plugins/codex/nested.py
stamp off it). Resolving the id into the SET of prefixes it might wear was the
step before this one: right for the registers in the table, and silently BLANK
for a host outside it, which is the same empty-mirror failure by another route.

The PREFIX carries the second fact: a SIDECAR stamps `codex:<aid>`, a native
SUBAGENT stamps `sub:<aid>` — the very prefix a Claude child uses. That is what
makes one child-agent vocabulary cover both tools. It is no longer what CLASSIFIES
a block, though: the producer stamps the `act` field (core/ops.py) and
`actclass._classify` reads that first, with the register→act map and the palette
behind it as the parked-history fallback for ops written before the stamp — a
standalone host's own (unstamped by design) and every parked op.

Re-pointing the stamp was safe because nothing keys on `codex:` to FIND a run:
`agent_scope` matches the id whatever prefix precedes it (so scoping keeps
working for NEW and PARKED ops alike), and `plugins.runs()` reads the audit
`streams` rows rather than the op stamp — so a native subagent still lists as a
clickable card, it simply classifies and folds as the agent it is. Parked
`codex:`-stamped subagent ops from before the refactor still resolve, still
classify `ACT_CODEX`, and still render through the legacy sniffers; their raw-JS
`▶ cmd` blocks stay as written, because history is not rewritten.

### The standalone self-run empty-scope fix

A codex running on its OWN writes its session transcript AS a rollout (uuid ==
sid), and the standalone watcher streams that very rollout under the audit
`streams` kind `codex` — so it used to appear in `plugins.runs()` (plugins/codex/nested.py) as a
clickable "agent". But it is the SESSION itself, and a standalone run's ops are
UNSTAMPED (codex is the main agent), so clicking it scoped to `{codex:<label>}`
matched no op and yielded an EMPTY mirror. `session_runs()` now drops the run whose
rollout IS the session's own `transcript_path`, so only genuine SIDECAR runs
(inside a Claude host, a different transcript) list as agents.
