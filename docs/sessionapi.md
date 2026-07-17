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
  `state_db_for(sid)`, `agents(sid)`, `agent_transcript(sid, agent_id)`,
  `costs(sid)`, `errors(sid)`, `sid_chain(sid)`.

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

## The parse/paint split (`plugins/claude_code/transcript.py`)

Drill-down fidelity lives in the transcripts, and the only code that
understood their record grammar was welded into the mirror renderer.
`transcript.py` is the extracted **parse half**: `parse_line()` is the one
owner of the record shapes (type discrimination, teammate-message unwrapping,
content-block walk, `result_text` normalisation), and two presenters consume
its records:

- `substream_render.Renderer.handle_line` — the mirror's capped, styled paint
  (unchanged output; the existing substream suites are the equivalence pin).
  Side effects stay in the paint/lifecycle half: spawning a live fg tailer is
  something `_use_bash` does *with* a record, never something parsing does.
- `transcript.timeline()` — the **uncapped** drill-down entries, plus a usage
  rollup deduped through the same `accounting.usage_fold` both accountants
  use.

Consumers reach timelines via **`plugins.activity(sid, agent_id=None)`** — a
registry fan-out like `census()` (optional per-plugin attr, first non-None
wins). This is also how the dependency rule is honored: `core/sessionapi.py`
imports no plugin; the tool-specific parsing stays in `plugins/claude_code/`,
which imports the core API for path resolution (audit `streams` first, the
`subagents/agent-<id>.jsonl` layout derivation as fallback). The parent
transcript uses the same grammar, so `activity(sid)` with no agent returns
the main thread's timeline; user turns that arrive as list-content text
blocks (a parent-transcript shape the mirror deliberately never painted) are
surfaced by `timeline()` only.

**codex is deferred**: its stream renderer parses and paints in the same
methods (no split yet), and there is no durable sid→rollout index — after-
the-fact recovery goes through the audit `streams` rows (`kind='codex'`,
`src_path` = the rollout). A codex activity provider needs its own parse
split first; the registry hook is already in place for it.

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

## Web dashboard notes (future work, out of scope here)

The API is transport-agnostic; a dashboard is a thin server over it. Decisions
already settled for whoever builds it: read-only, 127.0.0.1 only; singleton
via `core/locks.py` pid-lock + port bind and audit shape
(`A.spawn` + `stream_lifecycle`) borrowed from the OTLP receiver — but **not**
its request loop (single-threaded, sqlite thread-affine) nor its lifecycle
(900s idle-exit + respawn-on-SessionStart would leave the dashboard down
exactly when browsing parked sessions): use per-request `mode=ro` connections
and an explicit serve lifecycle, spawned via `core/spawn.spawn_detached`.
HTML-escaping is the `neutralize()` analog — op/transcript text is raw
attacker-adjacent bytes in any medium.
