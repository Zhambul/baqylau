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
    `sessionapi.codex_runs()`;
    the run's identity in the `agents()` list is `sessionapi.codex_aid()` —
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
    `synthetic` flag: codex re-injects its own context blocks *as user
    messages* every turn, so a presenter must drop them from the bubbles.
    The marker list is the module constant `rollout.SYNTHETIC_PREFIXES`
    (`<turn_aborted>`, `<environment_context>`, `<permissions instructions>`,
    `<skills_instructions>`, `<plugins_instructions>`,
    `<collaboration_mode>`, `<model_switch>`, `<app-context>`,
    `Approved command prefix saved:`, `# AGENTS.md instructions`) with
    `is_synthetic()` its one reader — a presenter must not re-encode it.
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
- **Claude screen-scrapers are host-gated OFF codex.** Once codex writes
  `awaiting-response`/`awaiting-command` tab rows (its tab producer, above), three
  Claude-GEOMETRY screen scrapes would start firing on codex panes and return
  garbage: the ghost-suggestion probe (`dashboard/read/session.input_box`) and the
  notifier's dialog-region + terminal-input reads
  (`dashboard/notify/notifier._dialog_region` / `_input_typed`). Each is now gated
  on the session's HOST being `claude_code` (via `owns_by`/`session_caps`; an
  unprovable/empty path stays the claude default, so a daemon-origin Claude session
  is unaffected) — a codex host gets NO ghost-suggestion probe and NO
  askdialog/suggestion notifier probe. The host-agnostic alert signals
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
    SIDECAR run by `agent_id` (`sessionapi.codex_runs`) and the STANDALONE host's
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

codex drives the control buttons it CAN — `interrupt`, `compact`, `rename`, and
`ask` — through its `HostControl` (`plugins/codex/hostctl.CodexHost`). Overriding
each gesture is what flips its DERIVED cap **True** (plugins.host), so the
dashboard un-greys those buttons and `_caps_guard` lets them through; the ones
codex cannot drive stay inert and read **False**: no `rewind` (codex has no
checkpoint menu), no `plan` (no plan-approval tool), no `migrate` (no account
switcher), no `model` (codex's `/model` is an INTERACTIVE picker, not a `/model
<arg>` we can drive blind — deferred), no `effort` (a launch-time `-c
model_reasoning_effort` only, no live `/effort`). `send` is a generic paste, not a
gesture, so it is never caps-gated.

The dashboard's control handlers ROUTE to the gesture when the session's owner
isn't claude_code (`_gesture_host(sid)` — `plugins.owns_by` → the host object, or
`None` for a claude/unprovable session): a codex session takes the gesture path,
a Claude session keeps its exact inline body (byte-identical — the branch is one
`if host is not None:` that is `False` for it). The gesture bodies use ONLY the
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
  codex dialog driver, sibling of `dashboard/askdialog.py` but in the PLUGIN
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

### Codex usage strip

`plugins.usage_windows()` (the app-server rate limits above) renders as its OWN
pill in the dashboard's top usage strip, BESIDE the Claude accounts —
`GET /api/codex-usage` → `dashboard.read.lists.codex_usage_payload`, painted by
`app.01-attention.renderCodexUsage` into `#codexusage`, poll-only on the same
`ACCOUNTS_POLL_MS` fallback cadence (no SSE — the windows are TTL-cached over a
bounded app-server spawn). Deliberately NOT folded into `accounts_payload`: an
account SWITCHER registry and a single host-wide reading are different shapes, and
`plugins.accounts()` is empty for codex. Hidden when codex is
unconfigured/unreachable (empty payload). Labelled `Codex · <planType>`.

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

### View modes — a codex run is its own act

A codex block wears the **codex palette** (`core/slots.CODEX_PALETTE`, disjoint
from every other), so `opshtml.actclass` classifies it `ACT_CODEX`. This is the
right treatment for a SIDECAR codex run inside a Claude session — a foldable
sub-run, like a subagent (`ran N codex runs`). It is the WRONG treatment for a
STANDALONE codex session, where the codex activity IS the session — see below.

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
   wore the codex palette → `ACT_CODEX` → folded. `op_items(codex_lead=True)` —
   set by `mirror.is_codex_lead(sid, agent)` for a codex-owned session's own view
   (not an agent scope) — DROPS a standalone codex session's prose ops
   (`actclass.codex_prose`) AND its codex CHROME (the `codex ▶ <label>` banner +
   the `⚙ model` tag, `actclass.codex_chrome`), exactly as agent scope drops an
   agent's prose. The prose comes back as bubbles (1); the banners are sub-run
   scaffolding a standalone session doesn't need (the model shows in the
   scoreboard). Command / file / footer ops STAY.

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
- **The codex run FOOTER is dropped too.** `actclass.codex_chrome` now also drops
  the `■ codex <label> ended · …` footer in a standalone view (`op_items`
  `codex_lead`), alongside the banner + `⚙` tag — a Claude session has no
  per-session footer, and its token rollup is redundant with the scoreboard, so
  it was exactly the codex-specific chrome to remove ("no codex specific ui").
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

### Sidecar → subagent parity

A codex run launched INSIDE a Claude session must read like a subagent in agent
scope: its intermediate messages/reasoning/commands all visible. Three parts:
1. **Grammar** — the rollout `chat`/`think`/exec/patch records already parse.
2. **`conversation()`** — the run's PROSE becomes bubbles from its rollout,
   exactly as a Claude subagent's does.
3. **Prose-op drop, ROLLOUT-backed only.** In scope, a codex run's prose ops
   (`⇢`/`✎`/`⇠`/`⋯`) are dropped so the bubbles don't DOUBLE them
   (`actclass.prose_block`), while its exec/patch ops STAY. But this drop fires
   ONLY for a run whose transcript is a rollout (`.jsonl`) — a **companion `.log`**
   run has no rollout to re-bubble from, so its prose must stay as ops.
   `read/mirror.agent_scope` signals the difference with a `codexprose:<label>`
   marker in the scope set (present only for a rollout-backed run); `prose_block`
   drops a codex prose op only when that marker is present.

### The standalone self-run empty-scope fix

A codex running on its OWN writes its session transcript AS a rollout (uuid ==
sid), and the standalone watcher streams that very rollout under the audit
`streams` kind `codex` — so it used to appear in `sessionapi.codex_runs()` as a
clickable "agent". But it is the SESSION itself, and a standalone run's ops are
UNSTAMPED (codex is the main agent), so clicking it scoped to `{codex:<label>}`
matched no op and yielded an EMPTY mirror. `codex_runs()` now drops the run whose
rollout IS the session's own `transcript_path`, so only genuine SIDECAR runs
(inside a Claude host, a different transcript) list as agents.
