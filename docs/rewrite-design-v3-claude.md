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
Later the same day, six read-only subagents audited the entire v1 codebase
against this document; **§17** records that gap audit and its amendments.
Where §17 tightens an earlier section, §17 wins; the earlier sections carry
pointers.

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

Retention is bounded and class-based, and pruning may never silently
decrement a user-visible count (§17.3). The evidence tables have exactly
one sanctioned RUNTIME read plane — the warning light (the ⚠ chip and
per-session error lines, §17.3) — plus the offline CLI: the v1 audit CLI's
successor (sessions / anomalies / errors / timeline / sql) queries these
tables plus the domain model; the canned anomaly queries port with their
signatures. The one-transaction rule means the audit can never disagree
with the domain model about what happened — they commit together on the
happy path; the degrade ladder when the store itself is unwritable is
§17.3's spool rule.

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
reactors) runs in executors; anyio task groups supervise. Not every fact is
session-scoped: machine-, account- and window-scoped facts live in named
machine-scope services beside the engines (§17.1), still inside the one
daemon.

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
and lateness by construction. Arrival order governs TOOL observations only:
a surface-authored write (drafts, queue pins) is ordered by its AUTHOR's
sequence — CAS on an author seq, tombstones over deletes (§17.12).

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

Amended by the gap audit: arrival ids remain the cursor backbone, but
DISPLAY placement is an item attribute, and the live stream carries
amendment frames (retract / supersede / move) beside appends; agent scope
is a subscription dimension with its own cursors — §17.4.

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

Amended by the gap audit: the answer is the LAST step of an all-or-nothing
PREPARE with rollback, not a pure computation — §17.6.

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
  **supersedes** the accumulated deltas at finalize — for MESSAGE blocks.
  Finalize authority is declared PER KIND: a command block's streamed bytes
  are themselves the authoritative copy, and the tool's capture is a
  fallback only when nothing streamed (§17.7). The lifecycle also has a
  third terminal outcome, ownership TRANSFER (Ctrl+B — §17.7).
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
   justifies (happy path — the spool degrade ladder is §17.3, and
   invariant 1 outranks this one); every decision has a decision row.
3. One writer per session's state; machine-, account- and window-scoped
   facts have one named machine-scope owner (§17.1).
4. Open facts are rows, not memory (declared live-only facets excepted —
   §17.7).
5. Silence alone never closes a fact; closing needs evidence, and a
   sampler channel's named grace/debounce/ceiling constants are part of
   its evidence rule (§17.8). Display expiry is a view rule; the
   web-interrupt recheck stays the one event-we-generated exception.
6. Bulk bytes never travel the row path.
7. The domain model is tool-neutral and free of colours, glyphs and
   layout; audience/register facts are domain facts (§17.10);
   sanitization happens at the leaf of every renderer.
8. Every effect is idempotent or leased; every outcome returns as an
   effect record; dedup is against verified outcomes only.
9. Deltas are provisional; the block's declared authority finalizes them
   (§17.7).
10. A missing capability is a named refusal, never a silent absence — at
    read time as a reachability map, at drive time as a verdict (§17.5).
11. Wrong derived data is repaired by recorded, auditable corrections
    (there is no replay).
12. A closer closes only a correlation whose identity it matched (§17.7).

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

---

## 17. Amendments from the legacy gap audit (2026-08-03)

Method: six read-only subagents (five Opus, one Sonnet) read this document,
then the whole v1 codebase in slices — core; the claude_code hook side; the
claude_code control/read side; codex + otel + frontends; the dashboard
backend; the SPA — each hunting for mechanisms this design cannot express,
covers lossily, or is silent about. ~65 verified findings consolidated into
the amendments below (file:line citations are the auditors', checked
against the code). Where an amendment tightens an earlier section, the
amendment wins.

### 17.1 The scope model: not every fact is session-scoped

The audit's largest single result: v1 holds load-bearing facts whose scope
is the MACHINE, the ACCOUNT, or the terminal WINDOW, and the spine as
written ("one engine per session, engines share nothing") had no owner for
any of them.

- Machine: the alert watcher is ONE 1s tick diffing the whole tab table
  across sessions (`notify/notifier.py:448-494`) with a cross-session
  needs-you badge (a per-session count was measured wrong — 148 shown with
  one asking session), ONE `kitten @ ls` per scan amortized over every
  armed entry, and a terminal-presence prober that runs continuously
  precisely to build HISTORY ("I was at the terminal two minutes ago"
  cannot be recovered later). codex-run discovery watches two GLOBAL
  directories and only secondarily decides which session (if any) owns a
  discovered rollout, across a grace window (`plugins/codex/watch.py`).
- Account: quota windows, limit-hit/logged-out latches, the burn-aware
  default-account picker, the per-model OAuth poll (§17.9).
- Window: tab state keys on the kitty window id, which OUTLIVES any one
  session (`core/tabs.py:87-99`); "flip only a currently-blue tab" is a
  window-arbitration rule; the scorebar's `paused` counter integrates over
  the tab colour.

Amendment. The daemon gains a small set of NAMED **machine-scope
services** beside the session engines — the attention/alert watcher (the
one tick, the one device map, the amortized terminal probes), the account
service (§17.9), the discovery service (codex global dirs; its claim
arbitration DISSOLVES — v1's per-repo claims DB existed because many
processes raced; one daemon arbitrates in memory over domain rows, with
audit rows keeping the outcome auditable), and the window registry
(window↔session binding, tab-state rows, the arbitration rules). Each
service is one owner with its own domain rows and probes (invariant 3
amended). Engines never reach into another session; engines and services
meet only in the store.

### 17.2 The session-env contract

BLOCKER (control-plane audit): several facts exist ONLY in the session
process's environment, which a machine-global daemon cannot see — the
subscription account slug/label (`account.py:31-39`, THE account
contract), `CLAUDE_CONFIG_DIR`, `CLAUDE_PROJECT_DIR`, the effort override,
the 1M-context kill switch (the ctx facet's DENOMINATOR), `CLAUDE_RELIMIT`,
and the per-project mirror/read gates (settings.json env blocks, layered
project-over-global; v1 already re-layers them out-of-process in
`model.settings_env`, having hit this exact problem once).

Amendment. The edge frame contract gains a declared **env snapshot**: an
allowlisted variable set harvested by the shim, shipped with
SessionStart-class frames and re-stamped on change, stored as session
facts. Per-project configuration is resolved engine-side by the ported
settings-layering walk over the session's cwd — never from the daemon's
own environment. The allowlist is part of protocol conformance tests; an
unlisted variable is invisible by design.

### 17.3 Evidence-log corrections

1. **Own failure domain.** v1's audit degrades to an append-only spool
   and — critically — spools STATE TRANSITIONS, not just inserts:
   pseudo-table rows replay as UPDATEs on re-ingest, which is what keeps
   `ended_at IS NULL` anomaly signatures honest across an outage
   (`core/audit.py:184-270`). §5's one-transaction rule is the happy path
   only: the evidence writer keeps the spool degrade with UPDATE replay,
   its own failure is recorded at most once per process, and an engine
   whose store is unwritable keeps observing into the spool rather than
   stopping — invariant 1 outranks invariant 2.
2. **The warning-light plane.** "Nothing reads these at runtime" was false
   in v1 and stays false: the ⚠ chip and per-session error lines are a
   live 5s read of the errors table with a persisted at-most-once emission
   checkpoint, flood collapse past FLOOD_N, a GLOBAL-rows second plane
   (machine-wide failures surface in every session), and a
   benign-signature suppression list — `A.error` is also called for
   EXPECTED degrades (`core/errwatch.py`). Amendment: the plane is
   sanctioned and specified; evidence rows carry a SEVERITY/VISIBILITY
   class so an expected 4xx refusal is evidence but never lights the chip
   (v1's `_reject_input` rule); and the chip reads a domain counter
   maintained at write time, so class-based pruning can never silently
   decrement it.
3. **The promotion list.** v1 serves several RUNTIME reads out of audit
   payloads because domain rows didn't exist: nested bg/monitor ownership
   (`json_extract` over hook payloads per poll — `nested.py:49-75`), the
   streams row as the agent-identity/end-reason keystone, the frozen
   `start_cwd` the list groups by. In v3 these are domain rows written at
   observation time; the evidence log returns to being evidence.

### 17.4 The read model: display order is not arrival order

BLOCKERs (SPA + backend audits). One append-only `(session, seq)` with
increments-only SSE cannot express four measured contracts:

- **Late structural records.** The feed interleaves an op backbone with
  transcript records merged BY TIMESTAMP — and §11.2's own measurement
  makes lateness the NORMAL case (a thinking record lands ~16s after its
  content, timestamped earlier). v1 rides two cursors (op id + merge pos).
- **Slot adoption.** A child task's result renders BEFORE the parent's
  final answer whatever the clocks say; v1 moves the RECORD, never the op
  — "op ids are the slot backbone every cursor rides on"
  (`read/mirror.py:123-176`) — applied to the backlog merge AND the live
  delta AND already-painted DOM.
- **Un-painting.** Three live paths remove/move bubbles already on
  screen: a superseded prompt (same-parentUuid replacement), an interrupt
  take-back, a rewind truncation. A query-time ancestry filter heals a
  reload; it says nothing to a browser holding the painted node.
- **Agent scope** is a second read model, not a filter: inverted src
  filter on the bare agent id, a DIFFERENT conversation source (the
  agent's own transcript + synthesized outgoing-mail records that exist in
  no transcript), different window boundaries per scope, a vocabulary
  normalization stage (`as_lead`), and mixed scoping in one payload
  (jobs/monitors scoped; errors/memory session-wide) on one SSE
  connection.

Amendment to §8. (a) Arrival ids stay the cursor backbone; DISPLAY
placement is an item attribute — a ts plus an optional "renders in the
slot of X" adoption — and one shared placement rule is applied by backlog
cut, live merge and browser alike. (b) The live stream carries a small
closed set of AMENDMENT frames beside appends — `retract(id)`,
`supersede(old→new)`, `move(id, anchor)`, `amend(id)` — each idempotent,
each derivable from a full re-read. (c) Agent scope is a declared
SUBSCRIPTION DIMENSION: cursors are (session, scope, backbone-id); a scope
names its conversation source and its normalization; badges declare
scoped-vs-session-wide per facet. (d) With no replay, every field added
later is NULL on prior rows: the read model declares a per-field FALLBACK
LADDER with a precedence rule (v1 has five independent instances of this
discipline).

### 17.5 Gestures: verdicts, preconditions, late refusals, delivery proof

- **Verdict payloads.** v1 gestures return DATA the page acts on:
  `queued` (measured on the screen BEFORE the paste — our own paste is
  screen motion), `restored` (the take-back text), `confirm`, live
  plan-option labels (they vary with permission mode), rename's
  `{queued, channel, title}`. Amendment: a gesture's completion carries a
  TYPED VERDICT; screen-measured preconditions are part of the gesture
  body, ordering included; interactions that only read (plan-options, the
  take-back box read) are declared CONTROL READS, not effects.
- **Late refusals.** A cap-sharer (`rewind_to`→`rewind`,
  `autoname`→`rename`) is only discoverable at drive time — after the
  202. The named-refusal shape applies to completion verdicts too; the
  SPA's "greyed, never gone" bar is served by a READ-side per-gesture
  reachability map (caps + refusal floors + tab state), the 409 remaining
  the backstop, not the affordance.
- **Delivery proof.** A composer send is proven delivered ONLY by a
  transcript prompt record matching by SUFFIX (attachments prepend; a
  terminal-restored draft glues with no separator), pinned durably across
  reloads/devices. Amendment: an **outbound-message correlation** —
  opened by the send gesture, closed by transcript evidence, surviving
  restarts — distinct from the paste effect record, with the suffix rule
  owned once and exported to the client as data.
- **Optimistic-UI evidence.** The browser beacons
  shown→reconciled/dropped(+stale-watchdog) lifecycles for its optimistic
  stand-ins — evidence justifying NO domain change. Amendment:
  surface-reported evidence is a sanctioned free-standing evidence class;
  it matters MORE under a 202 model, not less.

### 17.6 The synchronous answer is a prepared commit

BLOCKER (hook-side audit). v1's PreToolUse answer is the LAST step of an
all-or-nothing sequence — create the tee file, claim the fg slot, spawn
the tailer, write the hand-off record, THEN reply with `updatedInput`;
any failure rolls back (unlink, release) and answers no-rewrite, encoding
one rule: **never rewrite a command you are not certain you can tail**
(`cmd_pre.py:94-306`). The reply also fixes the block's copy-group
identity and the fg-liveness record the elapsed chip peeks. Amendment to
§9: the responder is a PREPARE-THEN-ANSWER transaction with declared
rollback; the <5ms figure covers the commit, the budget covers the
prepare; a "yes" that cannot guarantee a watcher is a "no". The v1 gate
set (read-command collapse, existing-redirect, env gates via §17.2, the
subagent variant, in-flight/stale reclaim) is the conformance checklist.

### 17.7 Streaming and correlation corrections

- **Finalize authority is PER KIND.** Message blocks: the transcript
  record supersedes deltas. Command blocks: the streamed bytes ARE the
  authoritative copy; the tool's `tool_response` is a fallback only when
  nothing streamed (v1 `fallback_body`). "Transcript wins" applied to
  commands would replace a 50MB streamed log with a truncated capture.
- **A third outcome: ownership TRANSFER.** Ctrl+B converts a running fg
  command into a bg job mid-flight (undocumented `backgroundTaskId` +
  `backgroundedByUser`; duration covers only up to the keypress; the
  pinned sentinel→pos0→spawn hand-off order). The lifecycle gains
  `transferred(new_owner)`.
- **Closers must MATCH before closing.** v1's take-once hand-off consumes
  only on identity match — without it a cancelled command's surviving
  record was consumed by the NEXT call's PostToolUse, cross-wiring two
  commands (`state.py:960-984`). "Every closer flushes open blocks" is
  amended: a closer flushes only blocks whose identity it matched; an
  orphan is closed by ITS OWN closer, never a neighbor's. Peek-vs-consume
  is part of the model (the elapsed chip's liveness IS the record's
  unconsumed presence), and the never-ran inference reads the ABSENCE of
  a consumption, which take-once semantics make expressible.
- **Completion gates on drained ingestion.** A completion signal must not
  finalize a block while its source's tail is behind (`capped` — the
  writer can be long gone while unread bytes remain; `tail.py:92-98`);
  resume checkpoints record the last SURFACED line (`consumed`), not the
  last read byte.
- **Live-only facets.** The ghost suggestion and `fg_running` are
  deliberately ephemeral — never persisted, absent after restart (a
  rehydrated engine must not re-serve a suggestion no longer on screen).
  Invariant 4 gains the declared exception.

### 17.8 The liveness vocabulary: graces are evidence rules, not timeouts

Stated absolutely, "no idle timeouts" deletes a family of MEASURED rules
that are part of how evidence is READ: burst-scoped markers need
N-consecutive-miss debounces (a teammate drops its marker between tasks —
`BG_MISS_GRACE_N=4`, the early-green bug); writer-gone needs idle≥grace
AND no lsof write-holder, a hung lsof reading "assume still writing";
monitors whose process was never located have an idle-fallback that is
the ONLY closer they get; give-up ceilings bound watchers; the compaction
latch may NEVER receive closing evidence and expires on the read side.
Amendment: invariant 5 recast — SILENCE ALONE never closes a fact; where
the evidence channel is a sampler, its named grace/debounce/ceiling
constants (each with its measured rationale) are part of the evidence
rule. The mail census is the extreme case: a poll-diff sampler where
disappearance-between-samples IS the read signal and most transitions are
structurally missed (2 of 33 messages left a poller row) — declared
cumulative-over-a-lossy-sampler, a class §7.2 now names. The monitor
ownership INVERSION is also declared: a child's monitor is observed by
the HOST (the child's in-order source never carries it) and attributed by
an out-of-band stamp — the one sanctioned exception to "the child owns
its story".

### 17.9 Accounts, credentials, and the relimit saga

- **The account domain model.** Quota windows (5h/7d, per-model
  weeklies), window-rollover arithmetic (a stale snapshot whose window
  rolled reads 0, not its frozen %), limit-hit and logged-out latches
  with measured graces, and the vendor-message parsers (scope + reset
  prose → epoch) become account-scoped domain rows owned by the account
  service — single-owner, because drift here "silently migrates onto a
  blocked account". The statusline shim's contract is stated: `statusLine`
  is a SINGULAR slot already occupied by the user's HUD, so the shim
  captures-and-DELEGATES — exec the real HUD with the same stdin, relay
  verbatim, never fail the render path.
- **The credential port.** The per-model weekly poll uses Claude Code's
  own OAuth tokens from the keychain and — the hard-won part — WRITES THE
  ROTATED TOKEN BACK into Claude Code's keychain entry, merging over the
  prior blob (Anthropic revokes the whole family on refresh replay; the
  mirror-entry design caused daily 401 /login loops —
  `model_usage.py:222-250`). Amendment: a narrow **credential port**
  (read + writeback, leased, audited) owned by the account service; its
  result feeds a display-only table; the migration path stays tokenless.
- **The relimit saga.** Nothing is exported in an account migration:
  accounts are symlink farms over one `~/.claude`, `--resume` forks the
  sid, adopt/lineage carries history. The flow is a cross-session SAGA —
  stamp limit-hit, cooldown-gate, pick target, announce BEFORE park (so
  the successor replays it), detach, wait for the dying session's park,
  relaunch under the new account — beginning at the exact moment §6.10
  releases the engine, spanning one dead session and one not yet born.
  Amendment: sagas are machine-scope service state (durable step rows,
  arms for cooldowns); §13's framing is corrected — relimit is a
  same-artifact RELAUNCH saga, provider handover is the export path: two
  mechanisms, one saga skeleton.
- **Session-less observations.** Launch/upload/notify/prefs evidence
  precedes or outlives any session (a launch's outcome IS the birth of a
  lineage, matched by window id/cwd over a wake poll; v1 measured a
  log-less upload filing rows into an unrelated session's timeline).
  Machine-scope evidence rows are first-class — no fake sid — and the
  launch lease closes on lineage-birth correlation.

### 17.10 Presentation vs audience facts; the slot allocator

The "presentation-free domain" rule over-reached in three measured places:

- **Audience facts.** `chrome` (host scaffolding every web view drops),
  `bubbled` (prose the scoped view trades away), and producer-authored
  REGISTER wordings (the web's quiet ⏺ register has no colour channel, so
  the producer writes the word where the terminal used the glyph's
  colour) are SEMANTIC facts about audience and register, decided where
  the tool knowledge is — a DTO layer cannot reconstruct them from the
  rendered artifact. The domain model carries audience/register facts;
  the ban stays on colours, glyphs and layout.
- **The slot allocator.** Stable per-entity palette assignment is a
  persisted, liveness-contended ALLOCATION (persisted round-robin so a
  freed colour is not immediately reused; a resumed teammate re-pins its
  original slot; the same rows are the tab tracker's liveness signal —
  `core/slots.py`). A stateless DTO mapper would recolour siblings as
  jobs finish. Amendment: the engine owns a slot-allocation fact per
  concurrent stream (a stable small integer, kind-scoped); leaves map
  slot→palette; liveness stays on the correlation row it already shares a
  transaction with.
- **The no-mapper default.** An observation with NO mapper must still
  render (v1's measured allowlist-silence bug: WebFetch/Grep produced
  nothing). The mapper registry declares a complement/default case
  producing the generic tool fact.

### 17.11 Terminal port additions

The Terminal port was written effect-only. It now declares: window
ENUMERATION with user-var tag read/WRITE (pane identity, retagged on
`SidAliased` — how an internal lineage alias reaches a terminal that only
knows the tool's sid); the audited stale-mirror sweep ("one tab holds
exactly one host session" — an unaudited sweep is how a cross-session
pane hijack stays invisible); the NESTED-HOST refusal as a three-valued
answer (host / nested / host-without-window — a `claude` launched inside
another session's tab inherits the outer window id and would sweep the
outer session's panes; distinct from the anchorless case); tab-LIVENESS
reads (the pane's user-var is the authoritative live signal; an empty
`kitten @ ls` is tri-state can't-tell, never "everything died"; a
just-started session gets a grace because its audit row precedes its pane
tag); focus/frontmost probes (presence; `--keep-focus` passed only while
kitty is frontmost — the active bounce-back was shipped and reverted
same-day); and the launch guards as port semantics: login-shell argv,
registry-vetted barewords, wrong-tool 409 with the gone-transcript 410
checked FIRST, live-lineage resume 409 off a fresh window scan, the
DECLARED clipboard-image clear (the OS clipboard is a shared resource —
its wipe is a leased effect behind a host declaration), and the
trust-prompt hazard (an untrusted cwd stalls a launch that neither
succeeds nor fails). The pid-liveness probe keeps v1's ONE rule:
`kill(pid, 0)` with EPERM = exists-but-foreign = ALIVE (kqueue
`EVFILT_PROC` cannot attach to foreign pids, so the fallback carries the
rule).

### 17.12 The web-local plane

v2 had a §12.5 this document dropped; it returns, with its concurrency
model:

- **Uploads are NOT blobs**: a staged attachment needs a STABLE,
  tool-readable absolute path outside any repo working tree, living the
  conversation's lifetime — the opposite of a content-addressed expiring
  store. The realpath jail (`_attachment_paths`) and the mention grammar
  being the RECEIVING host's are ported verbatim. Clipboard file-paste
  resolution stays server-side with the basename-agreement rule (a remote
  paste can never be answered with a host path). Dictation: the API key
  never leaves the server; short grant JWTs; keyterms layered
  project-first.
- **Prefs concurrency.** Surface-authored state (composer draft, ask
  draft, queue pins, view mode, tasks-dismissal, per-DIRECTORY ns-drafts
  with blur-settle) is multi-writer: every save is CAS on an author
  wall-clock seq, a clear is a TOMBSTONE (never a delete — the seq must
  survive to reject a straggler), `origin` echo suppresses the writer's
  own SSE reflection, and the TERMINAL is a second writer of the composer
  draft with an asymmetric rule (non-empty box wins; empty clears only
  what we synced; per-connection first-probe adoption).
- **The session-less API plane** (ns-prefs/drafts, hosts, limits,
  notify-config, commands, stats, presence, clipboard, dictate) with its
  GLOBAL SSE channel is a declared surface beside the per-session
  document; prefs∩facet joins are named patterns (tasks-hidden is the
  SERVER's verdict; hidden-dirs' expiry is a client predicate over
  `started_at`, with the live-session 409 reading the SAME list payload
  the page shows).

### 17.13 Usage and pricing corrections

- Token facts are FIVE categories, not one: in, out, cache-read,
  cache-create-5m, cache-create-1h (the 1h tier is +0.75× vs +0.25× —
  pricing everything at the 5m rate measurably undercounted). Cost-on-read
  (§8.5) therefore needs the categories stored AND a TIME-INDEXED price
  table (an introductory rate expires; the spend's own timestamp picks
  the row).
- Temporality is DECLARED PER SOURCE: OTLP is delta (sum, never gauge);
  Claude transcripts repeat a GROWING usage snapshot per content-block
  line (the `message.id` dedup, v1's 2.2× fix); codex `token_count` is a
  CUMULATIVE snapshot that must be DIFFED against the last-seen snapshot
  (`rollout.py:304-313`) — "sum deltas" applied to it recreates the
  order-of-magnitude bug in the other direction. The ledger records
  tokens per (source, temporality-rule); codex is priced by its own
  table, on read, like everything else.
- The post-end amendment (§12) names its owner: a parked session's ENGINE
  is rehydrated for the write (single-writer preserved), never written
  around.

### 17.14 Small corrections, one line each

- The task list is an artifact the tool DELETES at session end, and its
  directory key DRIFTS after resume: the engine's snapshot-on-observation
  is the ONLY durable copy — a declared VOLATILE-artifact class ("the
  tool keeps its artifacts" does not hold here), with key re-resolution
  by content recency.
- The subagent description hand-off is a deliberately UNKEYED FIFO (the
  payloads share no id; a resumed teammate must NOT pop) — correlation
  rows may be queue-shaped.
- Duplicate-stop dedup is an ATOMIC TAKE (the DELETE's rowcount is the
  once-only licence), and a stop for an agent that NEVER started (hidden
  summarizers: SubagentStop only, no transcript) is a third shape, not a
  duplicate.
- Memory SEARCHES are domain objects extracted AT INGEST (qmd stdout
  exists only at PostToolUse; hits attach only to a single-search
  command), and per-feature project SCOPE GATES exist.
- `set_session_title` is the ONE sanctioned WRITE into a tool artifact
  (parked rename), keyed by the filename stem, gated by `renameable` —
  §4.1's "never written" gains this carve-out.
- The ctx compaction animation needs an atomicity rule: no ctx
  recomputation is published while the latch is set, and the latch clear
  + post-compaction occupancy share one revision.
- SSE facets declare channel classes (fast / slow / inline-gated
  expensive producers), and the list stream ships snapshot + delta with
  diff blinding for continuously-clocked fields (a full snapshot per tick
  measured 2.2 MB/min per remote viewer).
- External-RPC reads (codex's app-server rate-limit handshake) join
  git-status as named read-time subprocess classes (TTL-cached,
  evidence-free on success).
- The ⧉ copy source must OUTLIVE the session and the blob horizon for
  blocks the user can still scroll to: copyability is a retention CLASS
  on the timeline's resident window, and the click channel is a
  terminal-originated gesture (OSC8 → handler → daemon), declared in §3's
  ingest list.
