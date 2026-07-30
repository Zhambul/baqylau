# The session-data read API (`core/sessionapi.py`)

The single sanctioned door for **consumers** of session data: the pane
renderers (`claude-mirror.py`, `claude-scorebar.py`), tooling, and any future
dashboard/TUI. It unifies **access**, not storage — one interface over the
stores that already exist, with deliberately **no new write path**.

## The shape

Two kinds of surface in one module:

- **The presentation channel** — thin delegations to `core.state`
  (`ops_after`, `stats`, `kv_get`/`kv_set`, `version`, `parked`, `tab_state`,
  `db_path`, `evict`, plus the `*_at` historical twins). The mirror and
  scorebar consume *only* this channel; the delegations are the same function
  objects (`sessionapi.ops_after is state.ops_after`), so re-pointing the
  renderers changed zero behavior — pinned by
  `test_l0_sessionapi.test_presentation_channel_is_the_same_functions` and the
  single-door grep test next to it.
- **The read model** — queries composed over the four existing stores:

  | Store | What it answers |
  |---|---|
  | per-session state DB (live `/tmp`, parked `HISTORY_DIR`) | scoreboard stats, agents table, ops replay |
  | audit DB `sessions` / `streams` / `otel` / `errors` | discovery, agent↔transcript mapping + final status, costs, swallowed errors |
  | global tab DB | current tab state |
  | transcripts | full-fidelity drill-down (parsed plugin-side — see below) |

  Functions: `sessions()`, `session(sid)`, `session_row(sid)`,
  `state_db_for(sid)`, `session_db(row)`, `agents(sid)`,
  `agent_transcript(sid, agent_id)`,
  `costs(sid)`, `errors(sid)`,
  `sid_chain(sid)`, `running(sid)` / `fg_running(sid)`.

  `agent_transcript(sid, agent_id)` is filtered to `TRANSCRIPT_KINDS`
  (`subagent`/`teammate`) rather than taking the agent's newest stream row of any
  kind. Once nested tailers began carrying their owner
  (`hookkit.stream_env(agent=)` — the attribution agent scope is built on), an
  agent's `fg` rows sit under its own id with the command's `.subfg.<tid>.out`
  tee file as their `src_path`, and the unfiltered "newest row" resolved to that:
  `transcript.agent_path` failed its isfile test and answered None, so an agent
  that had run a shell command lost its WHOLE conversation in agent scope (brief,
  messages and result all gone — ops only), while an agent that had only run
  background jobs was fine, because a `bg` row's src_path is empty and the
  subagents/ layout fallback caught it. "The keystone maps an agent to its
  transcript" only holds if the query says which kinds it means.

  `running(sid)` and `fg_running(sid)` are the two grains of "what is executing
  right now", and are also deliberately kept apart. `running` reads the `live`
  slot table: it answers *how many* things of each kind are alive (the header's
  running-now ribbon), but a slot is keyed by palette index and carries no
  tool_use_id, so it can never say which mirror BLOCK a command belongs to.
  `fg_running` reads the take-once `fg-live` hand-off, whose `tid` IS the
  block's copy-group id, and returns `{g, start_ts}` — the block-grained answer
  the web mirror's live elapsed chip needs (docs/dashboard.md, *Live command
  elapsed*). It peeks and never takes: consuming a hand-off from the read side
  would strand the real consumer.

  `state_db_for(sid)` and `session_db(row)` are the two spellings of the same
  live-DB-else-park choice, deliberately kept apart: the sid form returns
  **falsy when neither file exists** (its callers treat that as "no session
  state — no card, no ops"), the row form always returns a usable path for a
  caller that already holds the row and only stats/reads it opportunistically
  (the list + resume payloads' `last_active` / `stats_at`). Neither may be
  re-encoded inline — `P.state_db(...) if isfile else P.parked_db(...)` at a
  call site is the bug this pair exists to prevent.

## The `streams` table is the keystone

The audit `streams` table already records, for every detached
tailer/streamer, the `session_id` + `agent_id` + `src_path` (**the agent's
transcript path**) + `end_reason` (**the agent's final status** — carrying
every cancellation-recovery outcome the hook-time logic fought for:
`stop-sentinel`, `stoppedByUser (manual cancel)`,
`parent-task-resolved (rejected)`, `backstop-timeout`,
`state-db-parked (session end)`). The API *reads* that column instead of
re-deriving `subagent_fmt.finalize`'s event logic after the fact. The one
genuinely unknowable case stays unknowable by design: cancel-before-first-hook
leaves no signal anywhere (the documented invariant), and shows up as a
streams row with `ended_at IS NULL`.

## Fork-aware queries (`sid_chain`)

`adopt.py` renames the **state DB** at a sid fork, but pre-fork **audit** rows
stay under the old sid — a naive sid-keyed audit query silently truncates at
the fork and OTEL costs split across sids. Every audit-backed function here
therefore resolves the adopt chain first: each adoption leaves a
`state_files` row (`action='adopt'`, content `{"from": <old>}`, session_id =
the new sid); `sid_chain()` walks those rows both directions and queries
`session_id IN (<chain>)`. `state_db_for()` walks the chain newest→oldest
because after adoption the unified DB lives under the newest sid.

Because *every* audit-backed function resolves the chain first, the adopt
lookup sits on every read path — the dashboard alone runs it ~16× per
`/api/session` request and a few times per SSE tick. It is covered by a
dedicated audit index (`ix_state_act` on `state_files(action)`); without it
the `action='adopt'` predicate full-scans the audit's second-largest table
(~19ms warm, ~700ms with a cold page cache at 1GB), which once put the
session endpoint at 300–1000ms.

## The parse/paint split (`plugins/claude_code/transcript.py`)

Drill-down fidelity lives in the transcripts, and the only code that
understood their record grammar was welded into the mirror renderer.
`transcript.py` is the extracted **parse half**: `parse_line()` is the one
owner of the record shapes (type discrimination, teammate-message unwrapping,
content-block walk, `result_text` normalisation), and ONE presenter consumes its
records: `substream_render.Renderer.handle_line` — the mirror's capped, styled
paint (the existing substream suites are the equivalence pin). Side effects stay
in the paint/lifecycle half: spawning a live fg tailer is something `_use_bash`
does *with* a record, never something parsing does.

There were TWO until 2026-07-27. The other was `transcript.timeline()` — the
uncapped drill-down entries behind a `plugins.activity()` fan-out, a second
vocabulary for the same records, rendered by its own client stack. The dashboard
now shows an agent by SCOPING the mirror it already paints (docs/dashboard.md
*Agent scope*), so that read model, its codex twin (`rollout.timeline()`), both
fan-outs and the endpoints they served are gone. Two presenters of one grammar
is a drift hazard the registry tests existed to contain; one presenter needs no
containing.

What survives of it is deliberately narrow: **`plugins.agent_usage(sid,
agent_id)`** — a registry fan-out like `census()` (optional per-plugin attr,
first non-None wins) returning one agent's `{model, usage}`, folded through the
same `accounting.usage_fold` both accountants use, for the web's per-agent
scoreboard. It reads only assistant usage where the timeline built every entry
in the file to arrive at the same two numbers. This is also how the dependency
rule is honored: `core/sessionapi.py` imports no plugin; the tool-specific
parsing stays in `plugins/claude_code/`, which imports the core API for path
resolution (`transcript.agent_path`: audit `streams` first, the
`subagents/agent-<id>.jsonl` layout derivation as fallback). **codex declines
the fan-out** — a run's tokens are folded from its rollout and priced at its
footer, so there is nothing for the web to re-price.

**codex has the same parse/paint split** (`plugins/codex/rollout.py` — the one
owner of the rollout record shapes; `stream.py`'s `Renderer.feed_rollout` paints
its typed records byte-identically to the pre-split renderer, pinned by the e2e
codex suite). There is still no durable sid→rollout index, and none is needed:
recovery goes through the audit `streams` rows (`kind='codex'`, `src_path` =
the rollout), read via the `plugins.runs(sid)` fan-out (plugins/codex/nested.py). Because codex tailers
record no hook `agent_id`, the read model synthesizes one —
`paths.codex_aid()`, the `src_path` basename with the extension
stripped — and `agents()` lists codex runs in the same row shape
(kind `codex`, `desc` = the run label), so the codex provider resolves the
id straight back to its rollout. A companion job's `.log` run is listed but
declines drill-down (its activity log is not a rollout); a STANDALONE codex
session's own rollout answers `activity(sid)` with no agent_id — the
rollout filename uuid IS the sid.

`session_runs()` DROPS a standalone host's OWN run from the agent list, precisely
because the rollout uuid is the sid: that run's rollout equals the session's own
`transcript_path`, and its ops are UNSTAMPED (codex is the main agent there, so
there is no `codex:<label>` src on them). Listed as a scoped agent, clicking it
would scope the mirror to `{codex:<label>}` and match ZERO ops — an empty mirror
(the self-run empty-scope bug). Excluding it leaves only genuine SIDECAR runs
(inside a Claude host, whose rollout differs from the Claude session's own
transcript), whose stamped ops the scope resolves correctly; the codex
`conversation` provider then re-bubbles a rollout-backed sidecar's prose from its
own rollout, exactly as a Claude subagent's transcript does (docs/codex.md
*Sidecar → subagent parity*).

## Fidelity ladder (what drill-down can and cannot show)

The read model's fidelity limit is its sources', stated rather than hidden:

- **Live session**: full fg output exists in the `/tmp` tee files; transcripts
  are current.
- **Parked/old session**: transcripts + audit + parked state DB survive reboot
  (all under `~/.claude`); the tee'd `.out` files do not. Large tool outputs
  are truncated **by Claude Code at the source** (and a subagent tool_result
  rarely carries Read content), so full historical fg output is *out of
  scope* — no store of record holds it.
- The mirror's ops stream stays a capped presentation summary either way; the
  timeline never reads it.

## Why not an events table (rejected design)

The obvious alternative — producers double-writing semantic events next to
paint ops, with ops as a materialized projection — was designed and then
rejected on adversarial review:

1. **The writers are the wrong writers.** Claude Code fires no hook on
   cancel/interrupt, so a hook-time event log goes blank on exactly the cases
   this repo's recovery machinery (stoppedByUser, parent tool_result,
   StopFailure) was built for. Transcripts are written by Claude Code
   unconditionally and the audit writes from every recovery path — the event
   record already exists, written by more reliable hands.
2. **A third source of truth drifts.** Transcripts + audit already answer
   "what happened"; a second write path means two half-authoritative copies
   (the single-owner rule, at data scale).
3. **The economics are upside down.** Streamed chunks are the dominant write
   volume — an events table either duplicates them wholesale or forces a
   normalisation through every tailer, all inside the hooks-must-never-block
   hot path, for data *less* complete than what's already on disk.

If a materialized store is ever needed (query performance over huge
histories), build it as a **derived cache** the API can rebuild from sources
at any time — never as a source of truth.

## The web dashboard rides this API

[dashboard.md](dashboard.md) — the dashboard is exactly the thin server this
API was shaped for: every settled decision from the design review (read-only
127.0.0.1; per-request `mode=ro` reads, not the OTLP receiver's
single-threaded loop; explicit serve lifecycle, not idle-exit; `A.spawn` +
`stream_lifecycle` audit shape; HTML-escaping as the `neutralize()` analog) is
implemented there, with the rationale recorded next to each choice.
