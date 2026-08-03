# baqylau v3 — the domain-engine architecture (Claude variant)

Status: **PROPOSED** — this document supersedes [rewrite-design.md](rewrite-design.md)
(the event-sourced v2 proposal, revision 3) as the working design direction.
The v2 document and its consolidated review
(`rewrite-design-review.md`) remain in the tree as the record of that design,
its 20 findings, and the measured v1 lessons — most of which this design
ports unchanged. The 2026-08-02 "move the event store to PostgreSQL" ruling
is **mooted**: this design needs no Postgres, and SQLite stays.

Provenance: this is the outcome of the 2026-08-03 design session (Claude
Fable 5) that began from the owner's judgment that event sourcing does not
cleanly map to the tree shape of the conversation and that the
`eventsourcing` library's SQLite limitations were load-bearing. That judgment
survived scrutiny and is generalized in §2. Two rulings by the owner shaped
the final form: **no replay capability** (§2.4), and **the engine writes a
domain model directly; the API maps it to DTOs** (§3). New empirical
measurements taken during the session (transcript/rollout write granularity,
headless stream cadence) are recorded in §11 — they exist nowhere else.

---

## 1. Verdict in one paragraph

baqylau is an **observability and control plane over systems of record it
does not own**. Claude Code owns the transcript (a tree); codex owns
rollouts; the OS owns process liveness. Event sourcing is a pattern for
systems that are the writer of truth and need transactional invariants over
aggregates; applied to externally-owned truth it produced a two-log
architecture whose every mismatch surfaced as a named review finding. v3
replaces the spine — everything else survives: the supervised daemon, the
ports, the closers catalogue, the alert semantics, the pane-host, the
gesture model. The new spine: **edge transports → per-session engines that
interpret tool-specific input into a tool-neutral domain model, persisted
transactionally in SQLite → an API that maps the domain model to DTOs →
surfaces (read) and reactors (write), behind ports.** The audit trail
survives as an evidence log written in the same transaction as every domain
change — kept for debugging, not for replay.

---

## 2. Why not event sourcing — the analysis

### 2.1 The category error

Event sourcing (DDD-style: aggregates, domain events, optimistic
concurrency, "the log is the truth") answers a problem this system does not
have: multiple writers needing transactional invariants over state the
application owns. Here:

- **Truth is external.** The transcript, the rollout, the process table.
  v2's `SessionLog` events were *interpretations* of raw payloads — and the
  design itself admitted interpretations can be wrong (that is what "remap"
  was: rewriting truth, "a migration with a decision"). A store that can be
  wrong and needs rewriting is a cache, whatever it is called. Half of v2's
  ceremony (remap vs rebuild, policy-emissions-never-rebuilt, the
  `agent_attention` runbook exception) existed to defend the pretense.
- **There is exactly one writer** (the daemon), so aggregates and
  optimistic-concurrency version chains solve nothing.

### 2.2 The four mismatches, each mapped to review findings

1. **The global total order was the root performance bug.** Sessions are
   independent; no consumer ever needed cross-session ordering. The single
   global notification log created head-of-line blocking under an output
   flood (review 3.4: ~16× row multiplication, a build log starving an
   unrelated session's tab paint), dissolved the browser-checkpoint keystone
   across three incomparable position spaces (3.1), and made every rebuild
   walk the entire store forever (3.5).
2. **The tree does not linearize.** The transcript is a DAG with legitimate
   forks (~30 per ordinary session, measured — parallel tool results,
   attachments). Flattening it into a linear log forced the
   `BranchDiscarded` compensation machinery, whose detection rule as written
   re-created a bug class v1 had already fixed (4.1: ~30 spurious discards
   per session, the ctx bar repeatedly un-honoring a valid compaction).
3. **Mapper working state was self-inflicted fragility.** The architecture
   made per-session state a byproduct of folding a log, then discovered the
   state was the load-bearing thing (2.1, all three reviewers, critical):
   open correlations lived in RAM outside the exactly-once guarantee.
4. **The library's constraints were load-bearing.** No `subscribe()` on
   SQLite recorders, rowid positions unstable under VACUUM, immutable rows
   blocking the recorded-reply rule (2.2). The Postgres ruling existed
   purely to rescue the library — buying a server dependency, per-commit
   fsync, and an exclusive insert lock for a single-user tool.

### 2.3 What the new architecture does to the review's findings

Dissolved (the problem ceases to exist rather than being solved):

| Finding | Why it dissolves |
|---|---|
| 2.1 mapper state lost on restart | open facts are domain rows, written in the same transaction as everything else (§6.4) |
| 2.2 sync reply can't be recorded | we own the schema; the reply is a column written with the observation (§9) |
| 2.3 poison payload wedges all sessions | interpretation failures are per-session; the engine records + skips; siblings unaffected (§6.7) |
| 3.1 browser checkpoint dissolves | one revision space per session; no cross-log vector (§8.1) |
| 3.3 `agent_attention` not rebuildable | it is an in-process state machine over domain rows; nothing re-emits history (§6.5) |
| 3.4 head-of-line blocking | no global order; bulk bytes never travel the row path (§4.3) |
| 3.5 truth log grows forever | there is no truth log; audit is bounded, domain rows prunable per session (§5) |
| 4.1 BranchDiscarded mis-detection | the tree is an index; discards are v1's narrow rule acting as recomputation triggers, not truth events (§7.2) |
| 4.2 no prober tier | engines schedule their own probes — they know what is open (§6.6) |

Still real, ported as work items (the architecture does not change them):
4.3 (compaction display expiry — a view rule), 4.4 (lineage fork evidence +
a recorded repair), 4.5 (Notification demux), 4.6 (structural vs volumetric
subagent ordering), 4.7 (the tee/reporter regressions — port v1's shape
verbatim), 4.8 (the two usage ledgers, §12), the §5 feature-gap list
(agent scope, composer contract, pane lifecycle, codex tab title, ⧉ copy,
the audit CLI successor), and both security findings (socket trust tiers,
layered edge auth).

### 2.4 The replay ruling

The owner ruled: **no replay capability.** What replay was serving, and
what substitutes for each job:

1. **Crash recovery** → does not need replay. It needs open facts persisted
   as rows (§6.4). v1 already works this way (take-once hand-offs, slots).
2. **Debuggability** ("what did the tool send, why did we decide X") → the
   evidence log (§5), kept. This is not replay; it is the audit culture
   that cracked the no-hook-on-cancel bug class. Non-negotiable.
3. **Reprocessing after logic fixes** → dropped, consciously. A mapper bug's
   wrong rows are healed by hand (SQL fixup / migration script), exactly as
   v1 lives today. The risk is smaller than v2 assumed: most of v1's wrong
   numbers came from **twenty writers** each calling `bump()` on its own
   path; v3 has one writer per session, one implementation per rule, with
   fixtures. Single-writer + fixtures removes most of the bug class replay
   was bought to fix.
4. **One free safety net remains**: the conversation part of the domain
   model is an *index over the transcript*, and the tool keeps the
   transcript. The worst conversation-mapper bug is healed by dropping the
   session's index and re-reading the file. Costless, and not replay
   machinery — a property of tools that own their artifacts. What has no
   net: hook-only facts, screen probes, expired output blobs. Exactly what
   the evidence log preserves.
5. **Derived arithmetic** → computed on read where cheap (cost from tokens,
   §12.4), so a price-table fix heals history without any replay.

The lost discipline is named honestly: "every view must rebuild
identically" was also a correctness check. Its replacement: one owner per
number, engine fixtures from measured sessions, and the evidence log.

---

## 3. Architecture overview

```
EDGE (dumb transports; spool to disk when the daemon is away)
  baqylau-shim (hooks: claude_code, codex, statusline mode)
  baqylau-tee / reporter (in-shell fg capture — v1's brace-group shape kept)
  opencode plugin (TS)
        │ frames over unix socket (answerable kinds answered, §9)
        ▼
DAEMON — one supervised process
  ingest adapters (socket server · file watchers · OTLP listener §12 · gesture POSTs)
        │ observations, routed by session
        ▼
  SESSION ENGINES — one asyncio task per live session (§6)
    mappers · closers · classifiers (named, versioned, fixtured)
    conversation-tree index · open correlations · attention machine · facets
        │ ONE transaction per observation:
        │   domain rows + audit row + decision row + revision bump
        ▼
STORE — SQLite WAL (§4)
  domain model (tool-neutral) · evidence log (bounded) · blob/spool store · prefs
        │
        ├─▶ API: domain model → DTOs; REST + SSE keyed (session, revision) (§8)
        │     └─▶ SURFACES: web SPA · pane-host · CLI · MCP readers
        └─▶ OUTBOX → REACTORS → PORTS (§10): Terminal · AgentControl ·
              AlertChannel · ProcessRunner   (effects → effect records → back in)
```

Layer rules (each kills a v1 or v2 bug class):

- Edges are logic-free; all interpretation is engine-side. The moment
  per-tool logic wants to live at the edge, the answer is "no — mapper".
- The domain model is **tool-neutral and presentation-free**: no glyphs, no
  RGB, no per-surface flags in domain rows (v1's paint-op lesson — every
  time a consumer needed to *understand* rather than *display*, a baked
  string had to be promoted). Mappers put tool knowledge in; DTO mappers
  take surface shape out; nothing else.
- One writer per session's state; engines share nothing mutable.
- Ports on the outbound side, capability-declared; a missing capability is
  a named refusal (v1's implemented/DECLINED matrix, kept).

---

## 4. The store

### 4.1 Four schema tiers, declared per table

| Tier | Contents | Durability |
|---|---|---|
| domain | sessions, aliases, tree index, blocks/timeline, correlations, facets, counters, usage ledgers, arms, outbox, leases | the product; prunable per session |
| evidence | audit rows (raw payloads / blob refs), decision rows | bounded retention, by class |
| prefs | mutes, hidden dirs, drafts, new-session prefs, pane widths | tiny, durable, cross-device |
| blob/spool | command output, file bodies, diffs, plans, message bodies (content-addressed) | class-based retention: output expires, file/diff/plan/message kept |

Tool artifacts (transcripts, rollouts) are a fifth tier the system does not
own: watched, indexed, never copied wholesale.

### 4.2 SQLite, and why it is enough

One writer per session; per-session ordering only; WAL with
`synchronous=NORMAL`; bulk bytes off the row path. v1's measured scale —
3.7GB audit DB, 505k hook rows, ~20 events/minute/session at peak — is
comfortably inside this envelope. Every reason v2 needed Postgres
(`recorder.subscribe()`, stable global notification ids, the exclusive
insert lock) was an artifact of the global log. Physical layout: one DB for
cross-session tables + evidence, one DB per session for its domain rows
(mirrors v1's per-session state DB, keeps park/archive trivial), decided
finally by benchmark, not doctrine.

### 4.3 The bulk-bytes rule

Command output chunks and file bodies go to the blob/spool store; domain
rows carry references + offsets + sizes. Rationale is v2 review 3.4: rows
fan out to consumers, bulk bytes must not. Chunking bounds (ingestion,
never lossy up to the runaway cap) are vocabulary-separated from display
caps (per-surface), per review O10. A runaway guard (~50MB/command) is
honestly reported on the block when it truncates.

---

## 5. The evidence log (audit ≠ replay)

Written by the engine **in the same transaction** as the domain change it
justifies:

- **audit row**: raw payload (or blob ref), source, tool, sid, time, flags
  (`late`, `spooled`).
- **decision row**: rule name, rule version, input observation ids, result,
  one-line reason. This generalizes v1's `decision` column — the thing that
  made `hook_events` diagnostic rather than a log.

Nothing reads these at runtime. Retention is bounded and class-based. The
v1 audit CLI's successor (sessions / anomalies / errors / timeline / sql)
queries these tables plus the domain model; the canned anomaly queries port
with their signatures. The one-transaction rule means the audit can never
disagree with the domain model about what happened — they commit together.

Swallow discipline is unchanged from v1's crown invariant: every caught
exception records an evidence row before it is swallowed, and the warning
surface (⚠ chip, errors view) reads the same table. The recursion guard
ports: the error path's own failure is recorded at most once per process.

---

## 6. The session engine

### 6.1 Ownership

One engine per live session. One asyncio task owns it; only that task
writes that session's state. Engines communicate with nothing except the
store and the outbox. Sync work (SQLite commits, `kitten @` calls in
reactors) runs in executors; anyio task groups supervise.

### 6.2 What the engine holds (all mirrored in domain rows)

- identity: lineage, sid aliases (§7.1);
- the conversation-tree index: node uuid, parent uuid, kind, ts, blob ref;
  current leaf; live branch (§7.2);
- open correlations: commands (tid, wrapped?, interp-hint), agents,
  monitors, dialogs (ask/plan with tool_use id), the open message (§11.2);
- attention state (v1's set: idle · working · executing · awaiting-bg ·
  asking · done) with v1's precedence rule — `asking > executing >
  awaiting-bg > working > done` — and the actor filter (a child's inner
  events never drive the host state);
- facets: title (the five-step ladder + renamed override), model/effort +
  fallback, context occupancy, tasks, goal, compaction;
- counters and ledgers: commands, files, tools, tokens (per bucket and per
  source, §12), active time.

### 6.3 Rules: mappers, closers, classifiers

All named, versioned, individually fixtured from v1's measured sessions
(ported, never rewritten from memory). The full v2 §6.2 closers catalogue
carries over verbatim — interrupt record matching (as a RECORD, never raw
bytes; check what FOLLOWS for the queued-delivery case), denied/never-ran
Bash, killed/rejected/API-error subagents, monitor process-liveness,
host-death, codex rollout-EOF, plan/ask declines by tool_use id with
bounded lookbehind, duplicate-hook dedup on correlation keys,
`PostToolUseFailure` subscribed wherever `PostToolUse` is. Closers are
evidence-triggered, never idle timeouts; the one sanctioned timeout-ish
check remains the web-interrupt recheck (an event we generated), bailing on
any movement. Classification (command-is-really-a-read, the memory-vault
plane with its filesystem proof) is decided once, engine-side, and recorded
as domain facts linked to their evidence.

### 6.4 Open facts are rows — the crash-recovery story

Every open correlation is a domain row written at open time and closed by a
closer. The in-memory engine object is a cache of those rows. Restart =
rehydrate engines for sessions with live evidence, from rows. No checkpoint
machinery, no warm-up replay, no exactly-once gymnastics — those existed in
v2 only to protect in-memory state the architecture had refused to persist.

### 6.5 The attention machine

A pure function module over the engine's state, ported from v1's dispatch
table (docs/tab-colors.md) with its measured rules: what a finished stream
proves (bg/monitor end may go done; subagent end → working; fg command end
→ working, NEVER done), permission events drive red alongside dialogs, the
Notification demux (review 4.5) lands here as an engine rule with the raw
message preserved in evidence. State transitions update the domain row,
bump the revision, and enqueue tab-paint intents. Nothing re-emits
transition history on restart because nothing replays.

### 6.6 Probes

The engine schedules probes for its own open items — it knows what is
open: process liveness for monitors and hosts (kqueue `EVFILT_PROC` where
possible, polling fallback, v1's ps normalizations), screen probes for
dialogs/suggestions/draft-sync, the OAuth per-model window poll (its result
feeding a display-only table, never the migration path — v1's tokenless
doctrine). Probe results enter as ordinary observations with evidence rows.
This is v2 review 4.2's "prober tier", but it needs no tier: probing is a
natural capability of a component that owns "what is open".

### 6.7 Failure isolation

A rule failure records an anomaly and skips — the engine loop never wedges,
and the skip is visible (⚠ count). An engine crash restarts that engine
from rows; siblings unaffected. Repeated crash parks the session with a
visible error flag. This restores v1's per-hook-process blast radius inside
one daemon.

### 6.8 Timers

Arms are truth, timers are doorbells (v2 §9.6, kept whole): a deadline is
a durable arm row appended before any timer exists; restart rehydrates open
arms (future → schedule, past → fire late, never lost); firing re-checks
conditions; emission is idempotent on the arm.

### 6.9 Order, duplicates, lateness

Per-session order is arrival order at the daemon. A subagent's story:
**structural records in transcript order** (the only in-order source),
**volumetric events attach on arrival by correlation key** — review 4.6's
split, stated as the rule, preserving live subagent fg output. Spooled/late
frames merge by time with a `late` flag; every rule tolerates duplicates
and lateness by construction.

### 6.10 Session end, park, resume

End evidence → end closers close everything open, final state written,
timeline sealed, engine task released. A parked session is fully readable
(same tables). Resume rehydrates an engine and continues the same lineage.
The v1 session-end ordering (close panes → park → clear tab) is expressed
as ordered effect intents from the one end path.

---

## 7. Identity and the tree

### 7.1 Lineage

The domain key is a lineage id minted at first sight; runtime sids are
aliases (`--resume` and backgrounding fork the sid with no SessionStart —
v1 measured). Fork evidence is v1's, ported exactly (the take-once cwd
note, the absence of an instructions-loaded mark, predecessor liveness),
**without** v2's false absolute ("no event is ever appended under a wrong
lineage" — review 4.4 showed the evidence cannot support it). A
mis-assignment is repaired by a recorded merge/split correction applied by
a maintenance command; the evidence log makes the repair auditable.

### 7.2 The tree, and branch discards

The conversation is indexed as what it is: a DAG keyed by record uuid →
parent uuid. The live branch is the leaf's ancestry, computed on read where
needed. Branch discard detection is v1's narrow rule only — **two user
prompt records sharing one parentUuid** (all but the last dead, each taking
its whole subtree), validated across 30 transcripts — plus a `cause` where
a corroborating gesture exists (rewind, take-back, compaction-revert). A
discard is a **recomputation trigger** for the facets that declared
themselves live-branch-true; it is not a truth event, so a mis-detection
cannot be baked anywhere permanent.

Branch classes, declared per facet/counter (v2's good idea, kept):
**cumulative** (tokens, commands, time — discards don't un-count),
**current-state** (goal, tasks, title, context — recompute against the live
branch on discard; the ctx fold keeps v1's `_boundary_live` semantics: a
compaction boundary is honored only while it is on the live branch),
**read-filtered** (conversation, mirror history — ancestry filter at query
time).

---

## 8. The read side

### 8.1 Positions

The session document (facets) carries a **revision** that only increases;
timeline blocks carry a per-session **sequence**. A consumer's position is
`(session, revision)` + `(session, seq)`. There is no cross-session
position because no consumer needs one. SSE events carry these ids;
reconnect resumes from them; the list page is a query over the session
index table. The static-asset half of v1's boot-id survives as-is
(cache-busted assets + the "updated — refresh" toast).

### 8.2 Backlog vs live

The initial mirror backlog is a compressed plain GET (v1 measured 8–9×
gzip on 100–400KB backlogs; SSE frames cannot compress), newest-first with
`before=` cursors, **cut at block boundaries** (a cut mid-command splits a
block — v1 measured; the block identity is the tid/copy-group). SSE carries
increments only. This is v1's shape, kept deliberately (review 3.2).

### 8.3 DTOs

The API maps domain rows to per-surface DTOs. Facets are declared per
host: a host without tasks/monitors returns *absent*, not zero, and the
card hides (the DECLINED matrix). Read-time extras that are nobody's state
are computed in the API layer with TTL caches: git chips (the one
sanctioned `git status` subprocess), slash menus per host vocabulary,
prompt counts for compact-enablement.

### 8.4 Materialization is an optimization, not a tier

Cross-session reads (list page, stats heatmaps, account usage) are SQL over
domain tables with TTL memo caches first — v1's exact approach. A rollup
table appears only when a specific query is measured too slow, is written
by one owner, and is declared derived. The v2 fold registry dissolves into
this rule.

### 8.5 Cost is computed on read

Tokens are stored as facts; cost is arithmetic over the price table at read
time (cached). The price table was v1's most-corrected artifact; computing
on read means a price fix heals all history — the cheapest possible
replacement for replay, for the one number that needed it most.

---

## 9. Synchronous answers (the `updatedInput` rewrite)

Answerable frames (the fg command rewrite) are answered on the socket
path: compute from the frame + in-memory engine state (the v1 gate set is
the port checklist: `read_command` collapse, existing-redirect,
env gates, the subagent variant — both hooks must agree on the predicate),
write observation + answer in one local commit (target <5ms, measured),
then reply within the ~200ms hook budget. If the daemon cannot answer in
time, the edge proceeds unwrapped and the PostToolUse witness covers the
outcome — the tool never waits on us and never fails because of us.
Injection remains auto-approval on tools where `updatedInput` requires an
allow decision (v1's known trade), per-tool config.

---

## 10. Effects

Reactors consume outbox intents and act through ports: `Terminal` (kitty ·
null — an anchorless/daemon-origin session SKIPS the pane lifecycle, v1's
phantom-mirror lesson), `AgentControl` (per-tool gestures with v1's
semantics ported as fixtures: interrupt's queue-drain stop rule + take-back
read, rename's live/parked split, the clipboard-image guard, screen-driver
discipline), `AlertChannel` (webpush · telegram · toast; retraction
requires the channel receipt), `ProcessRunner`, `Clock`, `FileWatcher`
(v1's byte-discipline: read exactly `size−pos`, truncation restart,
complete lines only, no line cap on JSONL, watch-path refresh, polling
supplement).

Contract: at-least-once + idempotent; non-idempotent effects (send a
Telegram, type Escapes, launch a tab) take a **durable lease** first
(intent + idempotency key), so a crash between act and record resolves to
"in doubt — reconcile", never a silent duplicate. Every outcome returns as
an **effect record** observation; the engine consumes it (tab paint dedup
is against the last *verified* paint — persisting a failed paint strands a
colour, v1's tabpaint core rule). The pane lifecycle gets its owner
(review 5.3): a pane supervisor reactor consuming session lifecycle, with
open/close/resize effects, remembered widths in prefs, and the v1
end-ordering. The codex tab title gets its port operation and reactor
(review 5.4 — codex's TUI emits no OSC title, so here the rename gesture
retitles; for Claude Code the terminal keeps following the tool's own
title, one writer).

Gestures: POST → observation + 202 with gesture id → engine validates
capability/state (refusals are named 409s) → reactor drives the TUI →
effect record → revision bump → the surface sees the outcome by gesture id
over SSE. Driving a TUI takes seconds and can fail after acceptance; the
asynchronous shape is honest (v2 §12.3, kept). The browser-side transport
audit (`client:*` begin/ok/fail records) ports unchanged — the server only
sees requests that arrive.

---

## 11. Streaming

### 11.1 The open-block primitive

A streaming assistant message is the same object as a streaming foreground
command: an **open block** that grows by appends and is finalized by an
authoritative record.

```
block opened (kind: command | monitor | message | thinking)
  → deltas appended (coalesced ~50–100ms; resident cap; overflow → spool)
  → finalized (authoritative content ref)  |  aborted (partial kept, marked)
```

Rules that make it correct:

- **Deltas are evidence, never truth.** Tokens are not journaled; the
  delta accumulation lives in engine memory + spool. The authoritative
  record (transcript assistant record, keyed by message id + block index)
  **supersedes** the accumulated deltas at finalize. If they disagree, the
  transcript wins.
- **Every closer flushes open blocks.** An interrupt that finds an open
  message closes it as *partial, interrupted* — a feature gain over v1,
  which loses what the model was mid-writing at Esc.
- Read side needs nothing new: the same grow-by-append channel commands
  already use (revision bump + SSE delta; catch-up serves accumulated bytes
  then splices).

### 11.2 Measured write granularity (2026-08-03, this machine)

Method: throwaway sessions in kitty; a 50ms `stat`+read watcher on the
transcript/rollout; a 1s `kitten @ get-text` screen sampler; headless runs
timestamped per line. Claude Code TUI = Opus 5; codex TUI = gpt-5.6-terra.

**Claude Code TUI transcript — flush unit is the API message, not the
block, not the turn:**

- A tool-free turn produced one assistant message: a thinking block (its
  record's own `timestamp` 03:06:55.709) and a 3,401-char text block
  (03:07:11.642). **Both JSONL lines hit the file together at 03:07:11.8**
  — one 50ms window. The thinking record sat unwritten for ~16s while the
  text streamed. Writes are atomic full lines (`partial=0` throughout).
- Meanwhile the TUI screen streamed visibly: 1081 → 4322 sampler chars
  over ~14s, in ~500–700-char repaint steps every 2–3s.
- A tool-using turn confirmed **per-round progressive writes**: round 1's
  thinking+tool_use records (block timestamps :40.7/:41.1) flushed
  together at :41.4, its tool_result immediately after; round 2 flushed at
  :48.3–:48.4; the final text-only message at :50.6. So multi-round turns
  render progressively, round by round; a long prose answer lands all at
  once at its end.
- Corollary: each buffered record carries its block-completion
  `timestamp`, so the engine can backfill true block timing at flush —
  truthful "thinking :51→:55, text :55→:11" chips, learned late.

**codex TUI rollout — message granularity:** the user_message record lands
instantly; then 15.6s of file silence while the screen visibly streams;
then the complete `agent_message` + `token_count` + `task_complete` in one
burst.

**`claude -p --verbose --output-format stream-json
--include-partial-messages` — true token streaming:** `message_start`
+1.7s; per-block `content_block_start/stop`; **36 `content_block_delta`
events over 16.2s, avg 97 chars/delta (max 140), median gap 471ms (~2Hz),
p90 508ms**; a complete `assistant` record also lands per block
mid-stream; `result` carries totals. Gotcha for the port checklist: an
argv prompt through the account-alias chain was swallowed (the model
answered its greeting, twice); the prompt must ride **stdin**.

**`codex exec --json` — no partials:** `turn.started` +0.04s; 21.6s of
silence; `item.completed(agent_message)` with the entire answer;
`turn.completed`.

Capability matrix (declared per host adapter, surfaces render whatever
arrives): **claude-headless = token (~2Hz)** · **claude-TUI = round** ·
**codex TUI/exec = message/item**. The headless runner mode is where
first-class streaming lives — it is not a degraded deployment.

### 11.3 The tap ladder for TUI sessions (why files cannot do better)

On this machine the stream exists in exactly three places: inside TLS,
inside the CLI's process memory, and as width-wrapped styled repaints on
the PTY. Everything the tools voluntarily write is message-granular
(measured above); no hook fires per token. The interception options,
priced:

1. **Screen polling (`get-text` diff)** — SHIP THIS. Approximate text at
   1–2Hz; zero critical-path risk (a dead tap changes nothing);
   reconstruction imperfection is absorbed by reconcile-supersede; the
   TUI's own visible stream is chunky (~2–3s steps, measured) so 1–2Hz
   subjectively matches the terminal; gated polling only while the engine
   knows a message is open. Declared `granularity: approx-live`.
2. **API relay tap** (`ANTHROPIC_BASE_URL` → local dumb relay that tees
   SSE) — the named future upgrade, opt-in per tool. Yields the identical
   semantic deltas the headless run showed, in TUI mode, zero
   reconstruction, survives any TUI redesign. Costs: MITM on the auth
   path, and the relay sits on the session's **critical path** — a wedged
   relay breaks the session, the one line baqylau has never crossed. Held
   to the edge standard (tiny, logic-free, supervised); preceded by a
   subscription-auth passthrough measurement. codex is harder (ChatGPT-plan
   auth is not a clean OpenAI-compatible endpoint).
3. **PTY wrapper at launch** — REJECTED: pays the relay's load-bearing
   price (wrapper owns the pty; its crash kills the session) plus the
   screen tap's reconstruction price (a terminal emulator to un-render
   paints), and captures keystrokes besides.
4. **Process injection** (`NODE_OPTIONS --require` into the CLI) —
   REJECTED: breaks on every release, Node-only (codex is a hardened Rust
   binary), and is exactly the logic-at-the-edge the design forbids.

---

## 12. OTEL

No edge binary: Claude Code's own exporter pushes OTLP over HTTP when
`CLAUDE_CODE_ENABLE_TELEMETRY=1`; the daemon opens one OTLP listener on
127.0.0.1 (an ingest adapter, same category as file watchers), and the
launch env points sessions at it. v1's SessionStart-spawned singleton
receiver + pid lock is deleted — the daemon is already the machine
singleton.

Pipeline: batch → decode (total: a malformed batch gets an evidence row,
never a crash) → split datapoints by `session.id` → alias-resolve to
lineage → the engine writes evidence + usage rows + revision in one
transaction. Unknown sids (stray `claude -p` runs) get evidence rows only.

Domain rules, each a measured v1 lesson:

- **Sum deltas, never read gauges** (delta temporality; the silent
  order-of-magnitude error).
- **Buckets, not agents**: `main | subagent | auxiliary`. The auxiliary
  bucket is why OTEL exists — hidden summarizer/title runs write no
  transcript (11.6% of one measured session's cost).
- **Two ledgers, never mixed** (review 4.8): billing totals from OTEL
  only; per-agent live display figures from that agent's transcript,
  stamped `source=transcript`, excluded from totals.
- **Watermark + fallback**: at session end with no OTEL ever seen, fold
  usage from the transcript; if OTEL was seen, the fallback never runs; a
  late export after a fallback replaces it — the sources never add.
- **Post-end amendment**: the final OTLP flush may land after SessionEnd;
  the usage mapper may amend a parked session's rows and bump its
  revision — the one sanctioned post-end write.
- **Cost on read** (§8.5). Claude's own cost metric goes to evidence for
  cross-check, never to the ledger.

codex feeds the same tool-neutral ledger from its rollout `token_count`
records; a future tool feeds it from whatever it has. Surfaces never know
which adapter it was.

---

## 13. Extensibility — the future goals, mapped

The owner's forward list, checked against the architecture (these are
examples of variance, not committed features):

- **Plugins/extensions.** Three plugin seams, all capability-declared and
  contract-tested (the DECLINED matrix precedent): tool adapters (mapper +
  control + probes), terminal packages (port adapter + pane-host), surface
  extensions (the v1 web-ext registry shape) + alert channels. Core is
  capability-driven and imports no plugin.
- **No-terminal mode.** Session *hosting* is itself a port: a terminal tab
  and a headless runner (`claude -p` stream-json, Agent SDK, `codex exec
  --json`) are two adapters feeding the same engine. The null Terminal
  already covers anchorless sessions. Per §11.2, the runner mode gets the
  *best* streaming, not a degraded one.
- **Configurable backends/accounts.** The SPA's only coupling is the
  daemon API, so multi-backend = the frontend speaking to N daemons;
  account lists are adapter config (the `accounts.tsv` shape), not code.
- **Provider handover (Claude ⇄ codex ⇄ …).** The tool-neutral
  conversation domain model is the interchange: handover = export the
  normalized conversation → the target adapter's import/launch path. The
  relimit migration becomes one instance of a general mechanism. A
  claude-shaped event log would have fought this; the domain model makes
  it a mapping problem.
- **Session-to-session discovery and messaging.** The daemon is the
  mediated hub (v2 §14 kept whole: MCP tools, provenance framing — a peer
  message is untrusted model output, never auto-executed, never able to
  answer a permission prompt; quotas, human-principal rules; discovery →
  mailboxes → claims, off any critical path).

---

## 14. What survives, what is dropped

**Ported unchanged (the real value of v2 + v1):** the supervised daemon;
the edge fleet and its non-blocking discipline (+ the disk spool for
outages — v1's audit-spool pattern generalized); the closers catalogue
with fixtures; the reporter/tee shape with review 4.7's four fixes (the
blank line before `}`, one tee for both streams, the exit/flush handshake,
`$?` preservation); the alert policy's measured semantics (asking holds /
done settles 20s / retraction classes / presence-MRU routing / escalation);
presence + the terminal device rule; sanitize-at-the-leaf; the pane-host
split and renderer library with the reflow contract; the click transport;
the screen-driver discipline; the gesture 202 model; blob retention
classes; the migration flag-per-plane discipline; the security posture
(edge identity primary, bearer as depth, 127.0.0.1 bind, READONLY switch).

**Dropped:** the `eventsourcing` library and the Postgres dependency; the
Intake/SessionLog two-log topology; global notification order; the
follower taxonomy as separate log-consumers (folds/policies/reactors
survive as in-process roles: *pure state modules / decision modules with
clock / effect modules*); remap-vs-rebuild; `BranchDiscarded` as a truth
event; replay as a capability; the "browser is a follower of the truth
log" keystone (replaced by per-session revisions).

---

## 15. Invariants

1. The system never blocks a tool and never fails a tool.
2. Evidence is written in the same transaction as the domain change it
   justifies; every decision has a decision row.
3. One writer per session's state.
4. Open facts are rows, not memory.
5. Only evidence closes a fact — no idle timeouts (the web-interrupt
   recheck is the one sanctioned exception; display expiry is a view rule).
6. Bulk bytes never travel the row path.
7. The domain model is tool-neutral and presentation-free; sanitization
   happens at the leaf of every renderer.
8. Every effect is idempotent or leased; every outcome returns as an
   effect record; dedup is against verified outcomes only.
9. Deltas are provisional; the tool's artifact supersedes them.
10. A missing capability is a named refusal, never a silent absence.
11. Wrong derived data is repaired by recorded, auditable corrections
    (there is no replay).

---

## 16. Risks and open questions

- **The replay-less bargain.** Correctness now rests on single-writer +
  one-owner-per-number + fixtures. A mapper bug's wrong rows persist until
  hand-repaired. Accepted by ruling; the evidence log bounds the damage
  (the raw inputs are there to diff against).
- **In-process coupling.** With folds/policies/reactors as in-process
  roles, discipline (not process boundaries) keeps them from sharing
  state. The styleguide's single-owner vocabulary and grep-tests carry
  this.
- **Engine rehydration cost** for sessions with huge trees — bound with
  the same resident caps the renderer uses; the tree index is rows, not
  RAM.
- **Relay tap auth** (§11.3): verify subscription-OAuth passthrough before
  promising TUI token streaming beyond the screen tap.
- **Socket trust** (review O19): presence/viewing accepted only from the
  authenticated HTTP plane, never the socket; peer-cred stamping for edge
  frames; the model-can-forge-frames residual is recorded, not solved.
- **Benchmarks before build** (v2's entry criterion, kept): tab-paint p99
  *while* a synthetic reporter flood runs against a second session; the
  <5ms sync-answer commit; SSE fan-out under one busy session.
- **Migration**: this shape is deliberately close to v1 (per-session state
  DB ≈ domain rows; `sessionapi` ≈ the DTO layer; audit ≈ evidence), so
  the strangler runs subsystem-by-subsystem rather than across a paradigm
  chasm: daemon+engine reading v1's hooks first (dual-emit), then the
  reporter (the `updatedInput` slot is singular — per-session owner flag),
  then surfaces, each behind the flag-per-plane rule with v1 read-only
  until its gate passes. The v1→v2 coverage appendix recommended by the
  review (every CLAUDE.md feature/env knob/doc lesson → its v3 home or an
  explicit drop) is the migration checklist and remains to be written.
