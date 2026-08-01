# baqylau v2 — Rewrite Design

Status: **PROPOSED** — design complete, not implemented. This document is the
distillation of a long design conversation (2026-07-31 … 2026-08-01) and records
every committed decision, the vocabulary, the full component registry, and the
tradeoff ledger. The current (v1) system stays authoritative until the migration
section's gates are met.

Reviewers: findings go in a separate document; this file is frozen until the
owner decides on them.

---

## 1. Why rewrite

v1 (this repo today) works, but its architecture is accidental in three ways:

1. **The ops stream fuses presentation with semantics.** Paint ops carry glyphs
   (`▶ foreground`), RGB triples, and a growing pile of per-consumer routing
   flags (`web`, `note`, `chrome`, `bubbled`, `who`, `tags`, `act`, `mem`,
   `ctask` …). Every time the web needed to *understand* rather than *display*,
   a baked-in string got promoted to a field — the design converges on semantic
   events anyway, one bug at a time.
2. **No long-lived brain.** ~20 short-lived hook processes coordinate through
   SQLite tables, take-once hand-offs, sentinel files, pid-liveness slot rows
   and detached tailers. That coordination layer (slots, adopt machinery,
   `parked()` probes, inode revalidation, stale-row stealing) IS most of the
   complexity — and most of the bug history.
3. **Derived views are producer-maintained promises.** Twenty callers must each
   call `bump()` correctly; the tab state machine is smeared across dispatch +
   recovery hacks; the dashboard re-parses raw transcripts per request. Wrong
   numbers are permanent (hence `sql-write` fixups) instead of rebuildable.

What a rewrite cannot fix (constraints that survive any architecture): hooks
must never block or fail; Claude Code fires **no hook on cancel/interrupt**;
tool payloads are undocumented and version-fragile; TUI screen-scraping is
irreducible for the control plane. The rewrite *contains* these per-tool; it
does not shrink them.

### Goals

- Separation of concerns / loose coupling / SOLID: agent tools, terminals, and
  presentation surfaces are all pluggable behind small ports.
- Event-driven, async where waiting happens, sync where computing happens.
- Multi-tool: Claude Code, codex, **opencode** (which does not follow the
  Claude Code hooks pattern) — and future tools — as symmetric source adapters.
- Multi-terminal: kitty first, wezterm/others as neighbor packages.
- Multi-surface: terminal mirror+scorebar, web dashboard, notifier — and future
  surfaces — as subscribers over one truth.
- Every derived fact rebuildable; every input replayable; audit = the system
  itself, not a parallel write path.

### Non-goals

- No distributed operation. One machine, one daemon.
- No conceptual purity for its own sake — pragmatic exceptions are recorded
  with their triggers (see §9.6, §18).

---

## 2. System overview

One supervised **daemon** (Python, asyncio at the edges) hosts everything.
Around it: thin **edge components** (Rust) inside the agent tools, and
**adapters** for terminals/channels.

```
                    INBOUND                            CORE (daemon)                       OUTBOUND
 ┌─────────────────────────────┐   ┌────────────────────────────────────────────┐   ┌──────────────────┐
 │ baqylau-shim (Rust)         │──▶│  INTAKE LOG ──▶ MAPPERS ──▶ SESSION LOG    │──▶│ Terminal port    │
 │   claude_code + codex hooks │   │  (envelopes)   (per-tool)  (canonical      │   │   kitty · null   │
 │ baqylau-exec (Rust)         │──▶│                             events)        │   ├──────────────────┤
 │   command wrapper, all tools│   │        │                        │          │   │ AlertChannel port│
 │ opencode plugin (TS)        │──▶│        ▼                        ▼          │   │  webpush·telegram│
 │ FileWatcher observations    │──▶│    BLOB STORE          FOLDS · POLICIES ·  │   │  · toast         │
 │ gestures (web POST / MCP)   │──▶│  (content-addressed)   REACTORS           │──▶│ AgentControl port│
 │ presence beats              │──▶│                        (subscribers)       │   │   per tool       │
 └─────────────────────────────┘   │   QueryService · ControlService · MCP      │   └──────────────────┘
                                   └────────────────────────────────────────────┘
                                        ▲ FastAPI: REST + SSE + gestures ▲
                                        └────── web SPA · CLI · phone ───┘
```

Rules that define the shape:

- **Everything crossing into the core is an envelope** — pushed (shims, wrapper,
  plugin, gestures, presence) or pulled (file watchers). Persisted verbatim
  *before* interpretation.
- **Mappers are the only writers of canonical truth.** They emit facts; they
  never paint, count, or notify.
- **Subscribers never communicate except through the log.** No shared state, no
  direct calls; the one medium gives one ordering, one recovery story, one
  audit trail.
- **Surfaces own presentation entirely.** No producer pre-words or pre-colors
  anything for any consumer.

---

## 3. Storage: two logs, blobs, and what we dropped

### 3.1 Intake log (evidence)

Append-only table of raw **envelopes**:

```
Envelope(tool, kind, payload: bytes (verbatim), env: dict, ts)
```

- `tool` is a routing key only. `env` is ambient facts only (cwd, window id,
  pid, sid-hint) — the moment a shim pre-digests ("this was a failure"),
  vocabulary has leaked to the edge. Forbidden.
- Position-numbered; mappers follow it with exactly-once tracking.
- Own TTL/compaction policy — its value decays; the canonical log is what is
  kept forever.
- Purpose: replayable mapping (a fixed mapper re-runs over stored intake), and
  debugging (intake says what the tool sent; canonical says what we concluded;
  a divergence is located *between two persisted logs*).

### 3.2 Canonical event log (truth)

The `eventsourcing`-library-backed store (§8). Append-only, globally
position-ordered (the notification log), one aggregate stream per session plus
derived and global aggregates. This is the mirror history, the audit trail, and
every consumer's input — one write path.

### 3.3 Blob store

Content-addressed (`sha256`) store for bulk bytes: command output chunks,
file contents, diffs, tool responses, plan texts, peer-message bodies. Events
carry `BlobRef`s; the log stays lean; blobs are immutable so HTTP-cacheable
forever.

### 3.4 Dropped (deliberate, recorded)

- **Provenance table** (event → envelope ids + inference rule + mapper
  version): dropped for now. Intake still preserves the evidence; what we lose
  is the pre-built join — mapper debugging becomes manual archaeology. Cheap to
  re-add later (it was always a side table nothing was allowed to read for
  logic).
- **Shim failure tracking / spool files / IntakeGapMonitor**: dropped. Shims
  are thin; when they fail they must fail **fast and loud** (stderr + visible
  non-zero where the tool surfaces it) — but never *blocking* the tool.
- **LivenessMonitor**: dropped — `baqylau-exec` makes command liveness a
  reported fact. Residual known gap: a session process dying without a
  SessionEnd hook (kill -9, terminal crash) leaves an eternally-"working"
  card; detectable later by a trivial pid check at read time if it ever
  annoys. Recorded, not solved.

---

## 4. Edge components (Rust + one TS exception)

Hot-path, logic-free by decree: all evolution happens daemon-side in mappers.
The moment someone wants per-tool logic in a shim, the answer is "no — mapper".

### 4.1 `protocol/` — the language-neutral contract

Length-prefixed frames over a unix socket. Three implementations bind to it
(Rust edge, Python daemon, TS plugin), so it is written down precisely,
versioned, and conformance-tested from all three sides. Envelope kinds:

```
hook:<EventName>        pushed by baqylau-shim (claude_code, codex)
wrapper:started|chunk|exited   pushed by baqylau-exec
plugin:<event>          pushed by the opencode TS plugin
obs:transcript|rollout  pushed by daemon-side FileWatcher consumers
gesture:<g>.requested|.result  appended by ControlService / MCP
presence:beat|away      pushed by web pages and the terminal-focus prober
```

### 4.2 `baqylau-shim` (Rust)

One binary wired into Claude Code's and codex's hook configs
(`baqylau-shim <tool> <event>`): read stdin, frame as envelope with harvested
env metadata, write to the socket, **print the daemon's reply to stdout if the
kind is answerable**, exit 0 always. Timeout budget ~50ms fire-and-forget,
~200ms for answerable kinds. Zero vocabulary — a tool payload change requires
zero shim changes.

Answerable kinds (request-response exceptions to fire-and-forget):
- `hook:PreToolUse` for Bash — the daemon may reply with the `updatedInput`
  wrapper rewrite (§4.3).

### 4.3 `baqylau-exec` (Rust) — the command wrapper

One sentence: **a transparent exec-level tee whose report channel is allowed to
die and whose pass-through channel is not.**

Invocation (injected per-tool at rewrite time; metadata in env, not argv — no
quoting hazards, nothing leaking into the visible command string):

```
BAQYLAU_SID=<sid> BAQYLAU_TID=<tool_use_id> BAQYLAU_SOCK=<path> \
  baqylau-exec -- <original command string>
```

Behavior, in priority order:

1. **The command's behavior is sacred.** Runs `bash -c "$1"` in its own process
   group — shell semantics (pipes, `&&`, heredocs, globs) preserved exactly
   because we wrap at exec level, never edit the command text. stdout/stderr
   pass through byte-for-byte; exit code propagated exactly; signals forwarded
   to the child's pgroup; child reaped. Commands never had a TTY under these
   tools, so the added pipe hop changes no `isatty` semantics.
2. **Reporting is best-effort, never load-bearing.** Socket unreachable at
   start → run untouched, report nothing. Socket dies mid-stream → keep
   pumping stdout, stop reporting.

Frames: `wrapper:started {sid,tid,pid,pgid,cmd,ts}` ·
`wrapper:chunk {tid,stream,seq,bytes}` (flush at ~8KB or ~50ms) ·
`wrapper:exited {tid,exit,dur,rusage}`. Constant memory (never buffers whole
output); daemon writes chunks to the blob store.

Cross-check for free: PostToolUse still fires and carries the outcome — two
independent witnesses. Where they disagree, or `exited` never arrives (someone
kill-9ed the wrapper), the hook envelope closes the correlation with coarser
data. **The wrapper upgrades fidelity when present; hooks remain sufficient
when it isn't.**

Injection matrix (verified against official docs, 2026-08-01):

| Tool | Injection | Notes |
|---|---|---|
| Claude Code | PreToolUse `updatedInput` | the only sanctioned mechanism; `CLAUDE_CODE_SHELL_PREFIX` exists but is undocumented/enterprise — not production-safe |
| codex | PreToolUse `updatedInput` + `permissionDecision:"allow"` | requires `features.hooks=true`; hooks young & moving — the mapper PROBES injection per session (wrapper's own `started` is the proof) and degrades to rollout-only reconstruction |
| opencode | plugin `tool.execute.before` mutable `output.args.command` | |

Edge cases: backgrounded commands (run_in_background / Ctrl+B) are the payoff —
the wrapper survives and keeps streaming; nothing special. Idempotent injection
by prefix check (never double-wrap). The rewrite is visible in the tool's own
transcript — the mapper strips the prefix so every emitted event carries the
*original* command string; the model seeing the wrapper prefix is the honest
cost (same one v1's tee-rewrite pays). OS sandbox profiles may deny the socket
connect — degrade is automatic (rule 2), but **measure before counting on
wrapper-grade fidelity as the norm**.

### 4.4 opencode plugin (TypeScript — the host-required exception)

opencode plugins execute inside its Bun runtime; no binary-plugin mode. The TS
plugin (~100 lines, dependency-free, dumb) does exactly two things: forward
hook/SSE events to the socket as `plugin:*` envelopes, and rewrite
`tool.execute.before` bash args to inject the Rust `baqylau-exec`. Do NOT build
on opencode's on-disk storage — it is a moving target (JSON-tree → SQLite
migration with data-loss issues); the plugin + its server SSE are the source.

---

## 5. Mappers (Tier 1)

`ProcessApplication`s following the intake log. Stateful processes, not pure
functions: correlation, dedup, inference. Their working state (pending
correlations, watch positions) must be **derived** — reconstructible from
intake — never load-bearing on its own. Only mappers write to `Session(sid)`.

| Mapper | Consumes | Notes |
|---|---|---|
| `ClaudeCodeMapper` | `hook:*`, `wrapper:*`, `obs:transcript` | `respond()` answers PreToolUse Bash with the wrapper rewrite; `inference.py`; `classify.py` |
| `CodexMapper` | `hook:*`, `wrapper:*`, `obs:rollout` | probes wrapper injection per session; rollout parse/paint split lives in `rollout.py` |
| `OpencodeMapper` | `plugin:*`, `wrapper:*` | |
| `GestureMapper` | `gesture:*` | → `InterruptRequested/Confirmed`, `PeerMessageSent`, rename/compact/model outcomes … |
| `PresenceMapper` | `presence:*` | → `DeviceSeen`, `ViewingChanged` |

### 5.1 `inference.py` (claude_code only — earned, not architectural)

The module for **events asserted without the tool saying so** — filling
Claude Code's silences. Every rule is a named, versioned, individually-tested
function from evidence to event. Canonical residents (each a hard-won v1 rule
whose tests get ported, not rewritten from memory):

- **Interrupt**: no hook on Esc. Match the `[Request interrupted by user]`
  *record* (as the content of a `type:"user"` record — never raw bytes: growth
  that merely QUOTES the marker must not trigger), and check what FOLLOWS (a
  queued message delivered on the interrupt means the turn continued).
- **Sid fork**: `--resume` and backgrounding continue the conversation under a
  NEW sid with no SessionStart → emit `SessionForked(old_sid)`; everything
  re-keys. (Replaces v1's adopt machinery: DB renames, symlinks, pane retags.)
- **Never-ran commands**: a Bash call denied by permission fires no
  PostToolUse → close the open correlation as `CommandAborted(why="never-ran")`.
  v1 lesson encoded: do NOT infer "your turn" from it; and PostToolBatch is not
  reliably fired.
- **Cancelled/abandoned/dead subagents**: killed Task → `meta.json`
  `stoppedByUser`; rejected/abandoned Task → the parent transcript's
  `tool_result` (fires neither SubagentStop nor stoppedByUser); died-on-API-
  error → StopFailure carrying the subagent's `agent_id`.
- All are **events, never idle timeouts**. Cancel-before-first-signal remains
  deliberately unhandled for a terminal Esc (v1's idle-timeout backstop
  false-positived on every long think). The one sanctioned timeout-ish check is
  a *web-initiated* interrupt's recheck — an event we generated ourselves.

codex/opencode get no `inference.py` initially: their primary sources are
comprehensive journals (rollout / plugin events) with far less silence. One-off
inferences live inline in their mappers; a *body* of rules earns the named
module the day the third rule shows up (the module is really a test-suite
anchor).

### 5.2 `classify.py` — command interpretation

Certain shell commands are *really* file reads (`sed -n '1,120p' f`, `cat f`,
`find -exec cat`) — v1 learned the hard way that this classification must be
shared, not per-presenter (the memory tab recorded NOTHING for a year while
sessions read notes via `cat`). Rules:

- Classification is a **pure function of the command string** — no filesystem
  probing (decided: trust the text; occasional false positives are cosmetic and
  fixed by tightening the classifier, never by reinstating I/O). Replay-stable
  by construction; tests are `f(command_string) == expected`.
- The mapper emits **both the truth and the interpretation**, linked by `tid`;
  the interpretation only on success:

```
CommandStarted(tid, cmd="sed -n '1,120p' core/ops.py", interp="read")
CommandFinished(tid, exit=0)
FileRead(path="core/ops.py", extent="1-120", via="sed", from_cmd=tid)   # only if exit==0
```

- A failed read-shaped command (`exit!=0`, e.g. no such file) is **just a
  failed command** — no `FileRead`, renders and counts as a failure.
- Command events are never suppressed (it WAS a command; erasing that would
  make the log lie). `interp` on `CommandStarted` is the double-count guard —
  `CommandStatsFold` skips interpreted commands; `FileStatsFold` picks up the
  `FileRead`; `MirrorRenderer` sees both events share a `tid` and paints the
  collapsed one-liner (`Read(ops.py) sed` — `via` is a field; presenters own
  wording).
- The same shape carries the family: memory-vault reads (`FileRead` +
  `MemoryRecalled`), `qmd` searches (`MemorySearched(query, hits)` parsed from
  the command's output at map time — the wrapper streamed it).

Principle: **mappers own interpretation, folds own arithmetic, presenters own
appearance — an interpretation is always an added event linked to its
evidence, never a mutation of it.**

---

## 6. Canonical event vocabulary

All events carry `sid`, `actor` (main | agent-id), `ts`. Written only by
mappers, into `Session(sid)` aggregates.

| Group | Events (key fields) |
|---|---|
| Lifecycle | `SessionStarted(cwd, tool, account)` · `SessionEnded(reason)` · `SessionForked(old_sid)` |
| Turns | `TurnStarted` · `TurnEnded` · `TurnInterrupted` · `PromptSubmitted(text)` |
| Commands | `CommandStarted(tid, cmd, bg, wrapped, interp?)` · `CommandOutput(tid, blob)` · `CommandFinished(tid, exit, dur)` · `CommandAborted(tid, why)` |
| Files | `FileRead(path, extent, via?, from_cmd?)` · `FileEdited(path, add, rem, blob_diff)` · `FileWritten(path, blob)` |
| Tools | `ToolInvoked(name, args_blob, result_blob)` |
| Agents | `AgentSpawned(aid, kind, task)` · `AgentFinished(aid, status, result_blob)` |
| Dialogs | `QuestionAsked(qid, options, multi)` · `QuestionAnswered(qid, answer)` · `PlanProposed(blob)` · `PlanDecided(verdict, feedback, edited)` |
| Control | `InterruptRequested(by)` · `InterruptConfirmed` |
| Meta | `UsageReported(model, in, out, read, create, cost)` · `TaskListChanged(tasks)` · `GoalSet(text)` / `GoalMet` · `CompactionStarted` / `CompactionEnded` · `ModelChanged(model, effort, fallback?)` |
| Memory | `MemoryRecalled(paths, via)` · `MemorySearched(kind, query, hits)` |
| Presence | `DeviceSeen(device)` · `ViewingChanged(device, sid?, viewing)` |
| Cooperation | `PeerMessageSent(from_sid, to_sid, blob)` · `PeerMessageDelivered/Read(msg_id)` · `WorkClaimed(path, ttl)` / `WorkReleased(path)` |

Evolution rule: **additive only**. New event types must be ignorable by old
consumers — which every consumer needs anyway, since old log events never
disappear. (API compatibility and append-only discipline are the same rule.)

Attribution: `actor` distinguishes the main agent from subagents/teammates/
sidecar runs. View scoping (v1's `src`-stamp machinery, `web=1` overrides,
`in_scope` predicates) becomes a `WHERE actor` filter plus per-presenter
selection policy (e.g. the web's main scope additionally selects
`AgentSpawned/Finished` — a line of query logic in the one consumer that wants
it). No producer-written routing flags exist.

---

## 7. Ports (the abstraction budget: each must earn rent)

| Port | Contract | First adapters |
|---|---|---|
| `Terminal` | window discovery/tagging, panes, tab paint, send-text/keys, `get_text(ansi=)`, focus probes, `capabilities()` | kitty (package), null |
| `AgentControl` | per-tool gestures: interrupt/send/rename/ask/plan/rewind/compact/model/effort; capability-declared, missing gesture = named 409 | claude_code, codex, opencode |
| `AlertChannel` | deliver / retract | webpush, telegram, toast |
| `Clock` | `now()`, `call_at()` | asyncio real, fake for tests |
| `FileWatcher` | register/drop path watches → observation envelopes | watchfiles, polling fallback, fake |
| `ProcessRunner` | spawn/supervise/probe subprocesses | real, fake |
| Storage | the `eventsourcing` recorder + blob store behind `core/spine.py` and `core/blobs.py` | SQLite (one file) |

Deliberately NOT ports: config parsing, logging, ID generation, serialization
(pydantic is a decision, not a seam), the event schema itself. Test: *would a
second implementation change what the core does?* If no, it's a library choice.

Storage-port discipline: the contract is append-ordered-read + get/set, nothing
more. No query language crosses it. A consumer needing `WHERE … GROUP BY` is a
fold materializing into its own aggregate — rich queries live above the port.

---

## 8. The spine: `eventsourcing` library

Adopted as the **spine, never the skeleton** — everything imports our ports;
only `core/spine.py` imports the library (the walk-away seam: if the
one-maintainer project stalls, the reimplementation surface is one module and
this document is the spec).

What we lean on (verified against its docs):

- **Recorder** (SQLite) as the event store; the **notification log** as the
  global position-ordered view.
- **Aggregates**: `Session(sid)` — sid as aggregate ID gives per-session
  streams natively; derived aggregates for every fold output; global
  aggregates for the corpus-level facts. Data-only style — no
  command-methods-with-invariants ceremony (a session doesn't validate
  business rules; it accumulates facts).
- **Snapshots**: `snapshotting_intervals` per aggregate class;
  `repository.get()` = snapshot + tail → reading current state is effectively
  O(1). This is what makes **state-as-events** viable: folds don't write to a
  separate state store; they append to snapshot-backed derived aggregates.
- **`ProcessApplication` + tracking records**: state change + upstream
  position committed in one transaction → exactly-once folding; emission dedup
  on replay (the caused-by position is the idempotency key).
- **Runners**: followers are *prompted* on append and *pull* from the
  notification log at their tracked position — the signal-pushes/truth-pulls
  sandwich. A lost prompt only ever means "behind", which self-heals.

Rules layered on top:

- **One aggregate has exactly one writing subscriber** (two writers would fight
  over the version sequence). Corollary: the global read-model splits into
  `SessionIndex` and `StatsRollup`, one fold each.
- **Thin source-of-truth aggregate, thin derived aggregates.** Do not fold
  attention/counters into `Session` itself — a fat session aggregate re-fuses
  concerns, and every fold bug becomes a session-stream migration instead of a
  disposable derived-aggregate rebuild.
- **Fat-state warning** (global aggregates): a snapshot serializes the whole
  state. `StatsRollup` is naturally bounded (day×hour buckets, per-project
  maps). `SessionIndex` grows with the corpus — keep only what the list page
  shows, prune ended sessions past a horizon, snapshot less frequently.
  Thresholds that flip the decision back to a plain indexed table maintained by
  the same fold: tens of thousands of sessions, or any query needing arbitrary
  predicates over the corpus (FTS over titles, "sessions touching file X").
  The swap is invisible above the QueryService.
- **Update-frequency warning**: the global folds debounce/batch on **event
  timestamps** (never wall clock — determinism), so the global streams stay
  low-frequency and log volume doesn't double with bookkeeping events.
- Known friction, accepted: the library is synchronous — the async edges bridge
  via a thread/`to_thread` (one seam, designed once); serialization goes
  through its transcoders (a thin pydantic adapter).
- Runner IO note: the library's runner has each follower pull notifications
  itself (per-follower reads, not a single-reader dispatcher). At our volume
  over one WAL SQLite this is fine (page-cached). If it ever isn't, the named
  escape hatch is a custom runner that reads once and feeds followers — the
  narrow `interested_in` sets make the routing trivial. Deferred, with a name.

Rebuild vs remap semantics (write these on the wall):

- **Rebuild** (a fold): wipe its aggregates + tracking, replay. Free, routine,
  retroactive — every session ever recorded self-corrects.
- **Remap** (a mapper bug): re-run the fixed mapper over stored intake. This
  rewrites truth downstream already consumed — a *migration with a decision*
  (append corrections vs shadow log), not a casual replay. Going forward cheap;
  retroactively surgery. Possible at all only because intake persisted the
  evidence.
- **Policies are never rebuilt-and-re-emitted**: their emissions are historical
  decisions (made with a past clock and past presence), not derivable facts.

---

## 9. Subscribers

Vocabulary (final): everything consuming a log from a checkpoint is a
**subscriber**. Three kinds by *output*, each one rule:

> **Folds compute, policies decide, reactors act.**

| Kind | Rule |
|---|---|
| **Fold** | pure function of its subscription — no clock, no reads of other aggregates; event timestamps allowed (they're in the log). Freely rebuildable. |
| **Policy** | reads clock and/or other aggregates — same events replayed later could decide differently. Emissions are history. |
| **Reactor** | effects only, emits nothing; at-least-once + idempotent (checkpoint after the effect; tolerate replays; dedup by remembering what it last did — ephemeral memory is fine, repaint-once on restart). |

Structural rule: **one subscriber = one output** (one derived aggregate type,
or one emitted event type). `ls daemon/folds/` is the data-lineage map.

### 9.1 Folds (follow `SessionLog` unless noted)

Derived aggregates' own events are mostly a single `Updated(...)`; state is the
product; the canonical log holds the story. One exception, called out.

| Fold | Aggregate (key) | Subscribes to | Notes |
|---|---|---|---|
| `AgentAttentionFold` | `AgentAttention(sid)` | command/turn/dialog/agent events | tracks `open_cmds` itself from starts/ends (mapper guarantees every start closes — that's what inference is for). **Emits `AgentAttentionChanged(state, prev)` — transitions are the product**; the ONE intermediate producer in the graph. Named to not clash with human presence. |
| `CommandStatsFold` | `CommandStats(sid)` | `CommandStarted/Finished/Aborted` | skips `interp`-flagged commands |
| `FileStatsFold` | `FileStats(sid)` | `FileRead/Edited/Written` | unique-file set, ±diff |
| `ToolStatsFold` | `ToolStats(sid)` | `ToolInvoked` | per-tool tallies |
| `ActiveTimeFold` | `ActiveTime(sid)` | `SessionStarted/Ended`, `AgentAttentionChanged` | ⏱ pauses while attention is `done` (green = your turn); event-ts arithmetic only |
| `UsageFold` | `Usage(sid)` | `UsageReported` | token split (`tk_in` subtracts cache-creation — the ONE split, encoded once) + cost |
| `TasksFold` | `Tasks(sid)` | `TaskListChanged` | |
| `GoalFold` | `Goal(sid)` | `GoalSet/Met` | |
| `CompactionFold` | `Compaction(sid)` | `CompactionStarted/Ended` | latch; read-side expiry (an interrupted compaction fires no closing signal — animation must fail OFF) |
| `ModelFold` | `ModelState(sid)` | `ModelChanged` | incl. refusal-fallback flag |
| `PresenceFold` | `Presence` (singleton) | `DeviceSeen`, `ViewingChanged` | device map + viewing map; NOT owned by alerting (shared input) |
| `MailboxFold` | `Mailbox(sid)` | `PeerMessage*` | cooperation |
| `ClaimsFold` | `Claims` (singleton) | `WorkClaimed/Released` | active leases; ships only with claims |
| `SessionIndexFold` | `SessionIndex` (global) | `SessionStarted/Ended/Forked` + `AgentAttentionChanged` (follows `AgentAttentionFold`), event-ts debounced | the list page |
| `StatsRollupFold` | `StatsRollup` (global) | `SessionStarted/Ended`, `UsageReported` (event-ts batched) | the stats page |

Cross-fact consistency is loose by construction (independent folds may
momentarily disagree about "now"); no current consumer cares (scorebar repaints
every second); recorded as a property, not a bug.

### 9.2 Policies

| Policy | Subscribes to | Reads | Emits |
|---|---|---|---|
| `AlertPolicy` (`policies/alert.py`) | `AgentAttentionChanged`, `ViewingChanged` + Clock | `Presence` | `Alert.Armed(sid, kind, due_ts)` · `Held(why)` · `Dispatched(channel, device)` · `Cancelled(why)` · `Retracted(why)` · `Escalated(channel)` — the arm/settle/hold/retract state machine (v1's measured semantics ported: `asking` = blocked, alert promptly, a look HOLDS not cancels; `done` = resting state, 20s settle, a look resolves; retraction when the state stops being true; machine-wide device activity retracts nothing) |
| `ClaimPolicy` (`policies/claims.py`) | `FileEdited/Written` | `Claims` | `ClaimViolated(sid, path, holder_sid)` — ships only with claims; advisory claims WITHOUT a violation detector are worse than none |

### 9.3 Reactors

| Reactor | Consumes | Reads | Effect |
|---|---|---|---|
| `TabReactor` | `AgentAttentionChanged` | — | `Terminal.set_tab_color`, deduped by last-painted memory |
| `MirrorRenderer` | command/file/tool/agent/dialog events | `ModelState` (tags) | mirror pane blocks — owns ALL terminal glyphs/colors/wording |
| `ScorebarRenderer` | 1s tick | `CommandStats/FileStats/ToolStats/ActiveTime/Usage/Mailbox` | scorebar rows — the one legitimately many-reader consumer (it aggregates presentation, produces nothing) |
| `SseBroadcaster` | everything | — | per-connection filtered browser deltas (§11) |
| `AlertDeliverer` (`alerting/delivery.py`) | `Alert.Dispatched/Retracted` | — | `AlertChannel.deliver/retract` — the one owner of HOW an alert is (un)delivered |
| `PeerDelivery` | `PeerMessageSent` | recipient's `AgentControl` caps | inbox notice / turn-boundary injection; active TUI paste is human-initiated only |
| `WatchSupervisor` | `SessionStarted/Ended`, unwrapped `CommandStarted` | — | `FileWatcher` register/drop — the watch set is derived state, rebuilt from the log on restart |

### 9.4 The follow graph (whole system — keep it this flat)

```
Intake ─▶ 5 mappers ─▶ SessionLog ─▶ 15 folds · 2 policies · 7 reactors
                            └─ AgentAttentionFold ─▶ TabReactor · AlertPolicy · SessionIndexFold · SseBroadcaster
```

One sanctioned intermediate. Every additional follows-a-follower hop adds a lag
stage and a rebuild-ordering constraint; a lattice is illegible. New
intermediates need an argument as good as "consumers need ordered transitions,
not current state".

### 9.5 What replaced v1's coordination machinery (for the reviewer's checklist)

- Slot rows / palettes / pid-liveness → gone; the daemon knows what's live;
  palette assignment is `MirrorRenderer`-local.
- Take-once hand-offs (`fg-live`, outcome hand-offs) → mapper correlation
  state.
- `parked()` / DB park+restore / inode revalidation → a parked session is a
  stream that stopped growing; history is the identical query.
- Adopt machinery → `SessionForked` + re-keying in folds.
- `bg-recheck`, escape-recheck, interrupt-watch → `inference.py` rules +
  `AgentAttentionFold`.
- The audit DB as a parallel write path → the logs ARE the audit; the anomaly
  CLI becomes queries over intake+canonical.
- errwatch ⚠ → daemon structured logs (structlog) + an operational health
  endpoint; swallowed-exception accounting is replaced by "the daemon does not
  swallow: subscriber exceptions are supervised, logged, and leave the
  subscriber behind (visible as checkpoint lag), never silent".

### 9.6 Time

Three uses, three treatments:

1. **Deadlines that produce decisions** (settle windows, escalation): *arms are
   truth, timers are doorbells.* Arming is an event (`Alert.Armed(due_ts)`)
   appended BEFORE any timer exists; the `clock.call_at` timer is ephemeral and
   never persisted — on restart the policy rehydrates open arms (future due →
   re-schedule; past due → fire now, late but never lost). Firing re-checks
   conditions at fire time, then emits with idempotency keyed on the arm.
2. **Presentation ticks** (scorebar ⏱): plain loop timers in reactors; a missed
   repaint is repainted next tick.
3. **Timestamps in logic**: always the event's `ts` — folds stay deterministic;
   history renders identically forever.

With `Clock` injected, every deadline behavior is table-driven-testable in
milliseconds (events at t, presence at t+5, clock→t+20, assert exactly one
`Dispatched`) — v1 validated the same semantics by measuring 46 production
pushes.

---

## 10. Delivery mechanics (inside the daemon)

- The runner prompts followers on append; followers pull from their tracking
  position. Prompts carry no data; **delivery is always a log read** — lost
  prompt, slow consumer, crash, restart all reduce to "behind", which
  self-heals (plus a lazy poll backstop).
- Coalescing is automatic (N appends while busy = one catch-up batch). No
  queue-growth/backpressure protocol needed.
- The in-memory fast path is a **cache of the log, never a channel beside it**:
  it may only deliver committed events, in log order, with positions. The
  moment someone publishes pre-commit or reorders, the recovery story silently
  breaks — this is the load-bearing sentence of the section.
- Async policy: asyncio for the waiting (intake socket, watchers, SSE, timers);
  sync for the working (SQLite via the library, reducers, `kitten @` calls in
  executors). anyio task groups supervise subscriber runners and watcher
  lifecycles — no orphaned bare tasks.

---

## 11. API

Three external surfaces + CLI. One idea does most of the work: **the log's
position vocabulary extends to clients — a browser is just another subscriber
with a checkpoint.**

### 11.1 Read side (FastAPI)

```
GET /api/v1/sessions                          → SessionIndex (sorted, grouped)
GET /api/v1/sessions/{sid}                    → aggregate bundle: AgentAttention,
      CommandStats, FileStats, ToolStats, ActiveTime, Usage, Tasks, Goal,
      Compaction, ModelState, Mailbox + AgentControl caps
GET /api/v1/sessions/{sid}/events?after=P&types=…&actor=…   → canonical log page
GET /api/v1/stats                             → StatsRollup
GET /api/v1/blobs/{ref}                       → immutable, cache-forever (ETag=ref)
```

- Aggregate reads are lookups (snapshot+tail); handlers are trivially thin over
  `QueryService`.
- The events endpoint IS the log, filtered: mirror backlog, agent scope
  (`actor=`), parked history — one route, different parameters. View modes are
  queries plus rendering; collapsed/expanded and click-to-view are pure UI
  state (which blob refs are currently fetched); parked is not a mode at all.

### 11.2 SSE

```
GET /api/v1/events?after=P&sid=…      (each event's SSE id: = its log position)
```

Client protocol = a subscriber's: REST backlog to position N, subscribe
`after=N` — no gap, no overlap, by construction. Reconnect resumes via standard
`Last-Event-ID`. Server-side: `SseBroadcaster` with per-connection filter +
bounded queue; overflow → drop queue, client re-pulls from its position (the
same catch-up degrade as internal subscribers, all the way to the browser).
This deletes v1's boot-id/refresh dance and delta-vs-snapshot reconciliation.

### 11.3 Write side — gestures are asynchronous, honestly

```
POST /api/v1/sessions/{sid}/gestures  {kind, args}
  → 202 {gesture_id} | 409 {missing_capability} | 401
```

The POST appends `gesture:*.requested` and returns. Outcomes arrive as events
on SSE (correlated by `gesture_id`) — driving a TUI takes seconds and can fail
after acceptance; a synchronous 200 would be a lie papered over with polling.
`?wait=3s` is sugar (a server-side subscription with timeout) for curl/scripts,
not a second path. Capability discovery rides the session bundle; UIs grey
buttons with the named missing condition (v1's best pattern, kept).

### 11.4 Cross-cutting

- **Auth**: one bearer token on everything incl. SSE — the tunnel makes this
  API internet-reachable; "localhost is trusted" ended when the public URL
  existed. Cookie-set on first visit for browser ergonomics.
- **Schema**: wire types ARE the pydantic event/aggregate structs → JSON Schema
  (`protocol/schemas/`) → generated TS types. The API cannot drift from the
  domain because it has no types of its own.
- **Versioning**: `/v1`, additive-only within it.
- **Consistency**: reads eventually consistent behind folds (tens of ms);
  gesture outcomes via SSE avoid the stale re-GET trap structurally.
- Presence beats, uploads, prefs, drafts, dictation-token: ordinary routes;
  prefs/drafts/uploads are web-local state (`api/weblocal.py`), NOT domain
  events — with presence as the one deliberate promotion to domain (routing
  needs it).

### 11.5 MCP surface (cooperation)

The daemon serves MCP tools to sessions: `sessions.discover(project=…)`,
`sessions.send(sid, blob)`, `sessions.inbox()`, `work.claim(path, ttl)` /
`work.release`. Every call is an ordinary envelope → event. Mediated
hub-topology, never peer-to-peer: observable (traffic in the log),
governable (mutes/quotas/consent are hub policies), tool-agnostic (a Claude
Code session can message a codex session).

Hazards, designed-in:

- **Cross-session prompt injection**: a peer message is untrusted model output
  landing in another model's context. Delivered with provenance framing (treat
  as data; a peer cannot approve actions or grant permissions); never
  auto-executed; never able to answer a pending permission prompt.
- **Runaway loops**: per-session send quotas, thread TTLs; replies don't wake a
  session — they wait for its next natural turn.
- **The human stays the principal**: discovery read-only by default; messaging
  opt-in per session; active TUI delivery human-initiated only.
- Sequencing: discovery + read-only awareness first (fixes the two-sessions-
  one-repo friction with zero injection surface); mailboxes second; claims last
  and only with `ClaimPolicy`.

---

## 12. Alerting slice

`policies/alert.py` decides WHEN/WHETHER (with its policy siblings);
`alerting/` owns the delivery loop:

```
alerting/
├── events.py       Alert aggregate + Armed/Held/Dispatched/Cancelled/Retracted/Escalated
├── delivery.py     AlertDeliverer — HOW an alert is delivered and un-delivered
└── channels/       webpush.py · telegram.py · toast.py   (private to delivery.py)
```

Import rule: `policies/alert.py` emits `alerting.events`; `alerting/` consumes
them; nothing outside `alerting/` may import `alerting.channels` or
`alerting.delivery`. Ported v1 semantics (measured, not re-derived): presence
routing (MRU device pick incl. the reserved `terminal` device; browser wins
ties), zero base delay + per-kind settle (done: 20s knee), hold-don't-cancel
for `asking` on a glance, retraction with kind-dependent seen-reasons,
Telegram escalation, escalate-nothing for stage-1 Telegram.

---

## 13. Terminals

One package per terminal under `daemon/terminals/`; contract-tested against
the `Terminal` port; capability-declared so features degrade per-terminal
(tab colors, hyperlinks, screen read). kitty package layout:

```
terminals/kitty/
├── remote.py     kitten @ / socket RC protocol (timeouts; send-text via STDIN —
│                 never a shell argument nor a kitten escape vector)
├── windows.py    ls-tree walk, user-var tagging (claude_session/claude_mirror),
│                 focus probes (app/tab), ppid-walk socket resolution
├── panes.py      mirror/scorebar split lifecycle
└── screen.py     get-text(ansi=) capture for screen drivers
```

`null/` is the inert stub (headless `claude -p`, daemon-spawned scrubbed-env
sessions): every operation a silent no-op with failure-shaped returns; pane
lifecycle SKIPS when no anchor exists (v1's phantom-session lesson).

Screen-driver discipline (ports of v1's hard-won rules, they do not change):
prefer screen-delta ("is it still changing") over any literal marker; vim
editorMode makes Escape modal (first Esc exits INSERT — interrupt needs a
re-press loop); verify every keypress by re-reading the screen.

---

## 14. File structure

```
baqylau/
├── protocol/
│   ├── envelope-frames.md
│   └── schemas/                     # JSON Schema exported from pydantic → Rust/TS codegen
├── edge/                            # Rust workspace — hot path, logic-free by decree
│   ├── Cargo.toml
│   ├── crates/
│   │   ├── baqylau-proto/           # frame codec + socket client (shared)
│   │   ├── baqylau-exec/
│   │   └── baqylau-shim/
│   └── tests/                       # daemon-killed-mid-command, signal propagation, frame fuzz
├── plugins-ts/
│   └── opencode/                    # the one TS exception (Bun host requirement)
│       ├── package.json
│       └── src/index.ts
├── daemon/                          # Python — ALL change concentrates here
│   ├── core/
│   │   ├── events.py                # canonical vocabulary (pydantic v2) — THE shared language
│   │   ├── envelopes.py
│   │   ├── ports.py                 # ABCs only
│   │   ├── spine.py                 # the ONLY file importing `eventsourcing` (walk-away seam)
│   │   ├── intake.py                # intake application + answerable-kind respond() routing
│   │   └── blobs.py
│   ├── sources/
│   │   ├── claude_code/  mapper.py · classify.py · inference.py · control.py
│   │   ├── codex/        mapper.py · rollout.py · control.py
│   │   ├── opencode/     mapper.py · control.py
│   │   ├── gestures.py
│   │   └── presence.py
│   ├── folds/                       # one file = one subscriber = one aggregate
│   │   ├── agent_attention.py · command_stats.py · file_stats.py · tool_stats.py
│   │   ├── active_time.py · usage.py · tasks.py · goal.py · compaction.py
│   │   ├── model_state.py · presence.py · mailbox.py · claims.py
│   │   ├── session_index.py · stats_rollup.py
│   ├── policies/
│   │   ├── alert.py                 # AlertPolicy (fake-clock tested)
│   │   └── claims.py                # ClaimPolicy (ships only with claims)
│   ├── reactors/
│   │   ├── tab.py · mirror.py · scorebar.py
│   │   ├── sse.py · peer_delivery.py · watch_supervisor.py
│   ├── alerting/
│   │   ├── events.py · delivery.py
│   │   └── channels/  webpush.py · telegram.py · toast.py
│   ├── terminals/
│   │   ├── kitty/    remote.py · windows.py · panes.py · screen.py
│   │   ├── wezterm/                 # (future) a neighbor package
│   │   └── null/
│   ├── adapters/
│   │   ├── clock.py · watchfiles_.py · procrun.py
│   ├── api/                         # FastAPI
│   │   ├── app.py                   # create_app(), DI wiring
│   │   ├── routes/  sessions.py · events.py · gestures.py · stats.py · weblocal.py
│   │   ├── sse.py                   # sse-starlette, position-keyed, Last-Event-ID
│   │   ├── models.py                # thin — wire types are the domain types
│   │   └── auth.py
│   ├── mcp/
│   │   └── server.py
│   ├── web/                         # SPA (TS; types generated from protocol/schemas)
│   │   └── src/ · dist/
│   └── main.py                      # composition root — the ONLY place concretions meet
├── cli/
│   └── baqylau.py                   # start/stop/status · query/debug (audit-CLI successor)
├── tests/
│   ├── contracts/                   # per-port suites × every adapter; frame conformance
│   │                                # driven against the edge binaries + TS plugin
│   ├── folds/                       # table-driven + hypothesis property tests
│   ├── policies/                    # fake-clock scenario tables
│   ├── mappers/                     # envelope fixtures → expected events; classifier tables;
│   │                                # PORTED empirical fixtures from v1's measured bugs
│   └── e2e/                         # real daemon + fake tool → API assertions
├── docs/
│   ├── design.md                    # this document, maintained
│   ├── decisions/                   # ADRs
│   └── runbook.md                   # supervision, rebuild, remap-surgery procedures
├── Makefile                         # build edge, gen schemas/types, test, lint
└── pyproject.toml
```

Structure-enforced rules (pylint plugins, replacing v1's grep-tests with AST
checks): import direction (`core` imports nothing above; tiers import `core`
only; adapters import `core.ports`; `api`/`mcp` import core+query; `main.py`
imports everything); one fold one aggregate; `spine.py` is the only
`eventsourcing` importer; `alerting.channels` private; tool knowledge jailed in
`sources/<tool>/`.

Folder doctrine: tier folders are the default; a feature earns a vertical
folder only when it owns components in ≥3 tiers AND its events have no
consumer outside the slice. Current qualifiers: `alerting/` (delivery side);
a future `cooperation/` slice (mailbox + peer delivery + claims pair) is
pre-approved on the same grounds. `PresenceFold` and `AgentAttentionFold` stay
in `folds/` — shared inputs are not owned by their consumers.

---

## 15. Tech stack

Component-language map: **Rust** — `baqylau-exec`, `baqylau-shim` (hot path,
logic-free, protocol-stable). **TypeScript** — opencode plugin (host
requirement) + the SPA. **Python** — the daemon, where all change concentrates.

| Concern | Library | Note |
|---|---|---|
| HTTP | FastAPI + uvicorn | DI via Depends |
| SSE | sse-starlette | Last-Event-ID + keep-alives done right |
| Types/validation | **pydantic v2** (+ pydantic-settings) | the single type layer: events, envelopes, aggregate state, API models, JSON-Schema export. msgspec dropped — FastAPI is pydantic-native and two schema systems is the drift disease this design exists to kill. Settings replaces the env-knob farm. |
| Async plumbing | anyio | structured concurrency; no orphaned tasks |
| HTTP client/tests | httpx | ASGITransport → in-process API tests |
| Spine | eventsourcing | §8; behind `core/spine.py` |
| File watching | watchfiles | + polling fallback |
| Logging | structlog | bound context (`sid=`, `subscriber=`) — operational log, distinct from the event log |
| Retries | tenacity | channel delivery, kitten RC calls |
| CLI | typer + rich | |
| Tests | pytest + pytest-asyncio + **hypothesis** | property tests over pure folds ("attention never ends `working` with no open commands"; permutation-invariance where claimed) |
| Time in tests | the Clock fake (ours) | deliberately NOT freezegun/time-machine — we designed the seam, use it |
| Packaging | uv + hatchling | lockfile |

Typing/lint: `mypy --strict` from day one (retrofitting strict is the expensive
order) + **pylint** (deep passes in CI, custom plugins for the architecture
rules) + **ruff** (format + fast lint; complement, not rival).

Deliberately not adopting: celery/redis/rabbitmq (the runner + log IS the task
system), SQLAlchemy/alembic (the recorder owns storage; an ORM invites ad-hoc
tables beside the log), DI frameworks (main.py + Depends suffices), APScheduler
(arms-are-truth already solved durable scheduling correctly *for us*; a
scheduler with its own persistence is a competing recovery story),
Kafka-anything (scale cosplay). Meta-rule: adopt libraries for solved generic
problems; never for anything touching the architecture's own guarantees
(delivery, scheduling, storage semantics).

---

## 16. Worked example (the reference flow)

Scenario: a session runs `pytest`; you interrupt from your phone; the tab turns
green; you don't look; 20s later a push fires; the session ends; a week later
you open its history.

1. PreToolUse → shim → envelope 9231 → `ClaudeCodeMapper.respond()` returns the
   `baqylau-exec` rewrite → `CommandStarted(tid, interp=None)`.
2. Wrapper reports `started(pid)` (9235), streams chunks (blobs), Ctrl+B
   backgrounding changes nothing.
3. Phone POST /gestures interrupt → `gesture:interrupt.requested` envelope →
   ControlService drives AgentControl (double-Esc, screen-delta verify) →
   `gesture:interrupt.result` → `InterruptRequested/Confirmed`.
4. Transcript watcher ships the `[Request interrupted]` record (9268); the
   inference rule corroborates and emits `TurnInterrupted` + `CommandAborted`.
5. Runner prompts; `AgentAttentionFold` empties `open_cmds`, concludes green →
   appends `AgentAttentionChanged(done)`.
6. `TabReactor` paints green; `MirrorRenderer` paints the abort footer;
   `SseBroadcaster` updates the phone.
7. `AlertPolicy`: presence says away → `Alert.Armed(due+20s)`; clock fires;
   conditions re-checked → `Alert.Dispatched(webpush)`; `AlertDeliverer`
   delivers. A glance would have emitted `Cancelled(seen)`; post-delivery
   viewing emits `Retracted` and the channel un-delivers.
8. SessionEnd → `SessionEnded`; the "park" is nothing: a stream stopped
   growing.
9. A week later: same queries; renderer improvements apply retroactively.
10. A fold bug found: wipe + replay; every recorded session self-corrects.

---

## 17. Migration strategy (strangler fig — the empirical knowledge must not be
ported from memory)

The classic rewrite failure is rediscovering v1's measured edge cases one
production bug at a time. Sequence:

1. **Daemon + logs first.** Stand up intake + spine + a `ClaudeCodeMapper`
   subset. Wire v1's existing hooks to ALSO post envelopes (dual-emit); v1
   remains the production system.
2. **Web reads events.** Port the dashboard read side onto the QueryService/SSE
   while the terminal mirror still runs on v1 ops. Compare outputs against v1
   daily-driving.
3. **Wrapper + folds + policies.** Inject `baqylau-exec`; port the alerting
   state machine with its measured tables as tests.
4. **Terminal surfaces last** (mirror, scorebar, tab), then delete the v1 ops
   pipeline, slots, adopt machinery, audit write path.
5. Every inference rule and classifier lands with a fixture ported from v1's
   test suite / measured sessions — verified continuously against the running
   old system, not trusted from memory.

Gate for each step: v1 and v2 agree on the observable outputs for the same live
traffic (attention states, counters, alert decisions), for days, before v1's
half is retired.

---

## 18. Tradeoff ledger (the commitments, stated plainly)

| We gain | We pay |
|---|---|
| One source of truth; audit = the system; provenance-by-evidence (intake) | **A supervised daemon as single point of failure.** v1 degraded piecewise. Mitigation: supervision + auto-restart; edge components never block execution; the biggest single commitment. |
| Derived state disposable (rebuild); evidence permanent (remap possible) | Two logs to govern: intake TTL; remap of history is surgery, not replay. |
| New tool/terminal/surface = one adapter; view modes = queries | More upfront structure: ports, contract tests, fakes; a build system (Rust edge) in a repo that had none; ~2–3× the engineering of "patch v1". Pays only if the extension axes get used. |
| Testability: folds pure, policies fake-clocked, no sleeps, no live kitty | Latency chain envelope→map→append→prompt→fold→paint; each hop sub-ms locally, but tab color must stay <100ms — **needs a benchmark, not faith**. |
| Positions: loss-free, restart-proof, late-joiner-proof consumption — extended to browsers | Eventual consistency between views (tens of ms); a property to understand, not a bug. |
| Presentation fully surface-owned (no flag pile) | Some duplication returns: two presenters word `CommandFinished` independently; shared *classifications* must be pushed into the schema deliberately (the `act`/`mem` lesson) or they drift. |
| The empirical tool knowledge concentrated in named, versioned, tested rules | **It does not shrink.** Interrupt inference, vim Escapes, sid forks are as hard as ever — located and evidenced, not smaller. A rewrite re-risks each until its test is ported. |
| Rust edge: ~2ms per-call overhead, protocol-stable | Compiled artifacts, cargo in CI, recompile-to-change shims (deliberate: shims must never change). |
| eventsourcing library: tracking/snapshots/runner shipped, not built | Sync library under async daemon (one bridge seam); its transcoders (thin pydantic adapter); bus-factor-one wrapped behind `spine.py` with this doc as the reimplementation spec. |

Meta-tradeoff: every *subsequent* change becomes local to one mapper, fold, or
presenter — a good trade iff the project keeps growing along the axes made
cheap (more tools, more surfaces, more views). If it is actually
feature-complete, the honest answer remains: don't rewrite.

---

## 19. Deferred / open items (named, with triggers)

- Custom single-reader runner — trigger: measured notification-scan cost.
- `SessionIndex` → plain indexed table — trigger: tens of thousands of
  sessions, or arbitrary-predicate queries (FTS).
- Provenance table — trigger: the first painful mapper-debugging session.
- codex `inference.py` — trigger: its third no-signal rule.
- Zombie-session detection (process died, no SessionEnd) — trigger: the
  eternally-working card annoys in practice.
- `cooperation/` vertical slice — trigger: shipping mailboxes+claims.
- Sandbox interference with the wrapper's socket — **measure during step 3 of
  the migration**, before wrapper-grade fidelity is assumed.
- Rust vs Python for the wrapper's first cut — decided Rust; if cargo friction
  ever dominates, the protocol is the contract and a Python fallback is legal.
