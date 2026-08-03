# Consolidated review report — rewrite-design r3, all three reviewers

Reviewed artifact: `docs/rewrite-design.md` revision 3 (commit `0f64a26`).
Reviewers: **C** = codex (gpt-5 high, 11 findings), **F** = fable (14 findings),
**O** = opus (23 findings, verified against the eventsourcing 9.5.4 wheel
source). A fourth agent ("the verifier") ran executed experiments against the
library in a throwaway venv. Review date: 2026-08-02.

**Standing decision applied throughout: the event store moves to PostgreSQL**
(ruled 2026-08-02). Section 1 lists what that resolves; every later finding is
written under that assumption.

This report is written for a reader who has NOT read the design doc — each
finding carries the design context it needs. It supersedes nothing; the three
full per-reviewer reports live in the session transcript, and the design doc
itself is unmodified pending rulings.

---

## 0. Background: the architecture being reviewed (read this first)

The v2 design replaces v1's ~20 short-lived hook processes with **one
long-lived daemon**. The moving parts, and the vocabulary the findings use:

- **Edge**: small Rust binaries at the boundary. A **shim** is what Claude
  Code's hooks invoke — it forwards the raw hook JSON to the daemon over a
  unix socket and exits. For command capture, the shim rewrites each Bash
  command (via Claude Code's `updatedInput` hook feature) so that it runs
  inside the session's persistent shell wrapped as
  `{ cmd } > >(baqylau-tee --tid T --out) 2> >(baqylau-tee --tid T --err); baqylau-report --tid T --exit $?`
  — the **tee** streams output chunks to the daemon socket (~8KB/50ms
  flushes), the **reporter** sends the exit code. Edges are logic-free by
  decree: all interpretation happens daemon-side.
- **Envelopes**: every inbound thing (hook payload, output chunk,
  transcript-file change, OTEL metric, statusline JSON, web gesture, browser
  beacon) becomes an "envelope" — raw bytes + metadata — appended verbatim to
  **Intake**, an append-only event log with bounded retention.
- **SessionLog**: the single writer of truth. It consumes Intake's log in
  order; for each envelope it runs per-tool **mapper** code that interprets
  the raw payload into typed **domain events** (`CommandStarted`,
  `CommandOutput`, `AgentFinished`, `SessionEnded`, …). The eventsourcing
  library commits the emitted events *and* the bookmark ("I've processed
  Intake up to position N") in one transaction — so each envelope is
  interpreted exactly once, even across crashes. Events are keyed by
  **lineage**: a stable session identity, because Claude Code forks its
  session id on `--resume` and on backgrounding (the conversation continues
  under a new sid with no SessionStart).
- **Followers**: consumers of SessionLog's log, each with its own bookmark.
  Three kinds by rule: **folds** (pure functions building materialized
  tables — counters, session index, context saturation, usage), **policies**
  (decision-makers that may read the clock and their own state — alerting,
  rate-limit migration), **reactors** (the only components allowed to touch
  the outside world — paint the kitty tab, push SSE, deliver a Telegram
  alert, launch a terminal tab; each may append events *about its own
  effect*, e.g. `TabPainted`). One sanctioned exception: **agent_attention**,
  a fold that also *emits* `AgentAttentionChanged` events others consume — it
  ports v1's tab-color state machine (idle/working/executing/asking/done).
- **Closers**: Claude Code fires **no hook at all** on cancel/interrupt (a
  v1-measured fact), so the design carries a catalogue of named inference
  rules that close open facts from indirect evidence — e.g. "a `PostToolUse`
  never arrived but the batch resolved ⇒ the command never ran".
- **Blob store**: large payloads (command output, file bodies, diffs) are
  stored content-addressed; events carry references. Blobs have class-based
  expiry (command output expires; file/diff/plan/message blobs are kept).
- **API**: FastAPI. History = `GET /events?after=N` (N is a log position);
  live = SSE where each event's `id:` is its log position and reconnect
  resumes via the standard `Last-Event-ID` header; facets (usage, tasks,
  goal, context…) are reads of the materialized tables. Gestures (interrupt,
  send, rename…) return 202 immediately and their outcome arrives as effect
  events.
- **Pane-host**: one thin process inside each kitty pane; it subscribes to
  the daemon from a log position and renders locally (rendering / wrapping /
  highlighting lives in a terminal-agnostic renderer library, not in the
  daemon).
- **Migration**: strangler-fig; v1 and v2 run side by side per plane, with
  per-session flags handing over exclusive resources (only one hook may
  rewrite a command via `updatedInput`).

---

## 1. Resolved by the PostgreSQL decision

- **O1 — the library's materialized-view machinery doesn't run on SQLite.**
  The `Projection`/`ProjectionRunner` path requires `recorder.subscribe()`,
  which SQLite's recorder raises `NotImplementedError` on. Postgres
  implements it (LISTEN/NOTIFY push). Gone.
- **O2 (main part) — positions were SQLite implicit rowids**, which VACUUM
  may renumber, silently invalidating every follower bookmark and browser
  cursor. On Postgres, `notification_id` is a real `bigserial` column: stable
  forever, deletes + VACUUM safe. The commit-order hazard (a lower position
  committing after a higher one was already read, so a `> N` cursor skips it)
  is closed by the library taking `LOCK TABLE … IN EXCLUSIVE MODE` per insert
  — verified in `postgres.py`. Gone, at the cost of serialized writers and
  real fsync per commit (feeds finding 3.4).
- **F7 (retention half)** — deleting old Intake rows is no longer an off-book
  hazard.
- **C11 (partially)** — codex claimed tracking grows one row per processed
  notification; the verifier measured the 9.5.4 default
  (`single_row_tracking=True`): one upsert row per upstream, exactly as the
  design doc says. What survives of C11: **pin the library version** and note
  the correct base classes (there is no `EventSourcedApplication`; the real
  bases are `ProcessApplication`/`EventSourcedProjection`).

New obligations Postgres brings: a server process as a hard dependency
(install/upgrade/backup story needed in the doc), and re-doing the throughput
math with per-commit fsync + the exclusive insert lock.

---

## 2. Crash recovery and exactly-once

### 2.1 Mapper working state is lost on daemon restart — C2 (critical), F4 (major), O6 (critical). All three reviewers.

**Problem.** Mappers need memory between envelopes: which commands are
currently open (tid → the `CommandStarted` it emitted, whether it was
wrapped), which subagent events are buffered waiting for transcript order,
which ask-dialog is pending. The design says this state is "derived —
reconstructible from intake within the retention window — never load-bearing
on its own", and keeps it in process memory. But the exactly-once guarantee
covers only what is written in the tracking transaction. Walk the failure:
SessionLog processes envelope N (`CommandStarted` for tid T), commits events +
bookmark, keeps the correlation in RAM. Daemon crashes. On restart the library
resumes *after* N — it will never re-read it (`pull_and_process` starts from
the bookmark). RAM is empty. When `report:exited` for T arrives, the mapper
has no record of T: either the closer never fires (the command shows "running"
forever, and since the attention state machine counts open commands, the
session's tab state never returns to idle), or it emits an orphan
`CommandFinished` nothing can attach to. Same for the resequencing buffer
(ordering silently changes at the restart boundary) and pending-dialog state.
v1 has no such state to lose — every correlation there is a durable take-once
record in a per-session SQLite DB.

**Solutions.**

- **(a) Put open correlations into the `Session` aggregate itself.**
  `CommandStarted` opens a correlation as aggregate state;
  `CommandFinished/Aborted` closes it. On restart, rehydrating the aggregate
  (snapshot + event tail) recovers "what is open" for free, because it *is*
  truth now. Tradeoffs: the aggregate stops being a passive event container —
  snapshot design and aggregate size start to matter; and it admits that some
  mapper state is load-bearing (arguably the honest position).
- **(b) A mapper-state table written inside the same tracking transaction.**
  The transaction that appends `CommandStarted` also inserts the correlation
  row; restart reads the table. The verifier proved the mechanism (a recorder
  subclass writing an extra table atomically with events + tracking) works.
  Tradeoffs: a second schema to version; a mapper logic fix meets stale
  persisted state, so you need a version stamp and a rebuild-from-intake
  escape hatch; one extra write per envelope.
- **(c) Warm-up replay at startup.** On boot, re-read the last W minutes of
  Intake in a no-emit mode purely to rebuild buffers, then resume normally.
  Tradeoffs: a second code path through the mapper exercised only at startup
  (rarely tested; a bug there produces duplicate events — the one thing the
  architecture promises can't happen); W is a guess that any long-running
  command/monitor exceeds; correlations older than intake retention are
  unrecoverable by construction. If chosen, F4's variant: make W a declared
  per-mapper bound and *enforce* it by force-closing older correlations
  (`CommandAborted(why="correlation-expired")`), plus a determinism fixture
  (same intake slice ⇒ identical buffer state).

### 2.2 The synchronous rewrite reply cannot be "recorded into the intake row" — F11 (minor), O8 (major).

**Problem.** The command-capture rewrite must be answered synchronously (the
hook blocks Claude Code, ~200ms budget). The design's rule: the reply (the
rewritten command) is computed as a pure function of the envelope bytes and
"recorded into the intake row beside the envelope", so when the mapper later
processes the envelope in order it reads the recorded reply instead of
re-deciding. But an intake row is a stored event, and stored events are
immutable — the library has no update path (verified: zero `UPDATE`/`DELETE`
on event tables in the codebase). So it's either respond-before-append (crash
window: the command runs wrapped with tid T while the daemon has no durable
record of T — orphan chunks after restart, compounding 2.1) or
append-a-second-event (not "in the row"; the crash window moves between reply
and second append). Also unstated: whether the append must be fsynced inside
the 200ms budget. O8 adds: the decision is **not** actually pure in v1 —
cmd-pre also consults `read_command` (a file-reading command like
`sed -n 1,120p f` is deliberately NOT wrapped, so PostToolUse can render it as
a collapsed `Read(name)` one-liner — and both hooks must agree on that
predicate or you strand a streamed header with no body) and `parse_redirect`
(a command already redirecting to a file isn't wrapped; the tailer follows
that file), plus env gates and the subagent path. The design omits all of
these from the responder.

**Solutions.**

- **(a) Append first, respond second.** Write the envelope with the
  pre-computed reply as a field, then print the reply. A crash before the
  append means no reply, which the shim already degrades on (command runs
  unwrapped; the PostToolUse hook is the witness for output). Tradeoffs: a
  durable Postgres commit now sits inside the 200ms hook budget, and per
  finding 3.4 the write path can be busy — needs a dedicated intake writer or
  priority lane. (F11's variant: a companion table `envelope_id → reply`
  written in the same transaction via a recorder subclass — same idea, no
  event mutation.)
- **(b) Move the whole decision to the edge; no reply needed.** The shim
  mints the tid itself and applies the rewrite as a pure function of the
  payload — no synchronous round-trip at all; the daemon learns about T
  whenever the envelope lands. Tradeoffs: puts per-tool logic (the
  read_command/parse_redirect predicates!) into the Rust edge, which the
  design forbids by decree — you'd be carving a named exception for the one
  decision that must be synchronous, and porting nontrivial bash-parsing to
  Rust.
- **(c) Make the wrapped command self-describing.** The injected string
  carries everything needed to reconstruct the correlation (tid, session,
  original command) as arguments to `baqylau-report`, so an orphaned exit
  report after a restart suffices alone. Tradeoffs: a longer wrapper string
  visible in the transcript the model reads; mapping already strips it, but
  it leaks internals.

Whichever is chosen, the responder must implement v1's full gate set
(read-command collapse, existing-redirect, env gates, subagent variant) —
that's a port checklist item, not a design choice.

### 2.3 No poison-event policy; a malformed payload can wedge truth for every session — F3 (major), O4 (critical), plus C8/O17 context.

**Problem.** When a follower's `policy()` raises, the transaction rolls back,
the bookmark does not advance, and the runner retries the same event forever.
Two blast radii: a leaf fold wedges (its facet freezes — stale tab colors if
it's `agent_attention` — with no in-band signal); and **SessionLog is itself a
follower of Intake**, so one malformed tool payload — which the design's own
premise says is the norm ("tool payloads are undocumented and
version-fragile") — wedges the single writer of truth for **all sessions at
once**. That's a strict regression from v1, where a bad payload killed one
20ms hook process and the crown invariant ("every swallow site must record to
the audit `errors` table first") plus the `⚠` scorebar chip made it visible
within 5 seconds. v2 has an `AnomalyDetected` event and an `errors` fold in
its registry, but **nothing is licensed to emit it**: SessionLog is written
only by its mappers, folds must be pure, reactors may only describe their own
effects. There is no skip/retry/quarantine rule anywhere.

**Solutions.**

- **(a) Quarantine-and-advance.** The runner wrapper catches, appends
  `AnomalyDetected(rule="policy-crash", …)` + a quarantine row (upstream
  position, exception, payload ref), advances the bookmark. Pair with a
  coding rule that mappers/folds are total functions (raising is a bug, not a
  data path). Tradeoffs: a skipped truth event is a permanent hole (a skipped
  PostToolUse = an open command — but the closer catalogue exists for exactly
  that shape); quarantined envelopes remain replayable within the intake
  window.
- **(b) Never skip: retry + circuit-breaker + loud degradation.** A wedged
  follower flips a health flag surfaced in the ⚠ chip, a dashboard banner,
  and the CLI; you ship a code fix and reprocessing succeeds. Tradeoffs: no
  silent holes ever, but the wedged plane is frozen meanwhile (a stuck tab
  color for hours) — and per-follower isolation is real only for siblings; a
  wedged SessionLog still starves everything.
- **(c) Two-tier trust (recommended-shaped by both reviewers).** SessionLog's
  mapping catches everything internally and emits `AnomalyDetected` **in the
  same transaction** (it can — it is the writer), so truth-mapping never
  wedges; downstream followers get (a) or (b). Tradeoffs: concentrates the
  totality burden exactly where payload chaos arrives (correct); the anomaly
  path itself must be bulletproof — v1's errwatch has a recursion guard (its
  own failure is audited at most once per process) that must be ported.
- O4 adds an orthogonal channel choice for *who may record errors at all*:
  **(d)** a second, non-truth log (`Ops`) that anything may append to
  (errors, degrades, dead-letters) — doesn't violate single-writer because it
  isn't truth; discipline required that it never feeds decisions. Or **(e)**
  failures loop back as `error:*` envelopes through Intake — stays inside the
  one-writer rule, but the channel is least likely to work exactly when the
  daemon is unhealthy, and needs a recursion guard.

### 2.4 Routine daemon restarts are guaranteed observation gaps; no spool at the edge; "supervised" is undefined — F6 (major).

**Problem.** The design accepts that a daemon outage loses pushed envelopes
("watcher sources re-cover most of it after recovery"). That prices outages
as rare crashes — but v2's own rule "all evolution happens daemon-side" makes
restarts *routine*: every code change is a restart, and this repo changes
constantly. Every deploy drops whatever hook envelopes fire in the window —
and the watcher back-fill argument is weakest precisely for hook-only facts
(permission events, tool failures, statusline rate-limit frames), which have
no file to re-read. Ironically v1 holds its *audit* path to a higher standard
than v2 proposes for the entire system of record: v1's audit degrades to an
append-only spool file re-ingested on the next successful open. And "one
supervised daemon" never names the supervisor, restart policy, crash-loop
behavior, or upgrade procedure.

**Solutions.**

- **(a) Shim-side disk spool.** On connect failure/timeout, the shim appends
  the frame to an owner-only spool dir; the daemon ingests at startup and
  periodically, marking envelopes `late=true` (mapping must tolerate
  arrival≠commit order anyway, because watcher sources lag). Tradeoffs: the
  edge gains a write path — bending "logic-free", though spooling is arguably
  transport; needs a timestamp-merge rule for spooled vs live frames. This is
  v1's own audit-spool pattern, ported.
- **(b) Zero-downtime handover for planned restarts.** launchd socket
  activation (or fd-passing exec handover) keeps the socket open across the
  swap; frames queue in the kernel buffer for the seconds a restart takes.
  Tradeoffs: covers deploys, not crashes; platform-specific; best combined
  with (a).
- **(c) Keep the acceptance but make it concrete.** Name the supervisor
  (launchd KeepAlive + backoff), add a CLI `deploy` that waits for a quiet
  moment (no in-flight turns), and emit a first-class
  `ObservationGap(from_ts, to_ts)` event so downstream consumers (attention,
  alerts, stats) can mark uncertainty instead of confidently reporting wrong
  state. Tradeoffs: cheapest; quiet moments are rare on a busy machine; but
  the gap becomes visible data instead of silent wrongness.

---

## 3. Log topology and consumers

### 3.1 "One truth log" is contradicted by the follower topology, and the browser-checkpoint idea dissolves — C3 (critical), F2 (critical), O13 (facet half). Postgres does not help.

**Problem.** The design's API keystone is "notification IDs extend to clients
— a browser is a follower with a checkpoint": each SSE event's `id:` is a log
position, reconnect resumes via `Last-Event-ID`. Three facts break it.
(1) **Positions are per-application.** The browser needs SessionLog events
(mirror/conversation), `AgentAttentionChanged` (which lives in the
agent_attention follower's own log — it's the sanctioned intermediate
producer), and `Alert.*` lifecycle events (the alert policy's log). Three
logs, three incomparable position spaces; one `Last-Event-ID` cannot
checkpoint them, and the design never says which log the SSE `id:` comes
from. (2) **Materialized folds emit nothing followable** — that's the point
of materializing them — yet the SSE plane's consumers (live scorebar
counters, ctx bars, tasks/goal cards) are precisely consumers of those
tables' *changes*; nothing exists to push them. (3) **The `sse` reactor races
the folds**: it's declared a *sibling* follower of SessionLog, prompted by
the same append, so it can read a facet table before the owning fold's
transaction commits — pushing stale facets. Mid-stream the next event heals
it; after a terminal event (`SessionEnded` → final counters) nothing ever
re-triggers, and the card is permanently stale.

**Solutions.**

- **(a) Single-log discipline + fold-position gating.** SSE serves only
  SessionLog positions; attention/alert changes reach clients as facet
  updates, not as their own logs; the broker delays each push until every
  relevant fold's bookmark ≥ the announced position. Tradeoffs: standard
  client resumption survives; every push waits on the slowest relevant fold;
  the broker needs a fold→facet ownership map; attention transitions lose
  event identity client-side.
- **(b) Composite checkpoint.** `Last-Event-ID` encodes a vector
  (`app:pos,app:pos,…`), or the broker keeps a server-side per-connection
  cursor set keyed by a resumption token; catch-up merges logs by timestamp.
  Tradeoffs: no longer standard `Last-Event-ID` (custom reconnect logic —
  losing the simplicity being sold); merging by ts re-imports the cross-log
  ordering problem; per-client server state.
- **(c) A dedicated outbound feed log.** One `feed` follower — a second
  sanctioned intermediate producer — consumes SessionLog + attention + alerts
  + the fold deltas clients need, and appends presentation-neutral feed
  events into its own application. SSE serves exactly that log: one ID space,
  and "a browser is a follower" becomes literally true. Tradeoffs: partially
  un-does the materialization savings (fold changes become events again,
  once, at the feed tier); one more hop of latency; but it is the only option
  under which the headline sentence is true — and it gives O13's facet
  problem the same answer.
- C3 adds a variant worth naming: **(d)** followers report outcomes by
  submitting envelopes back through Intake, making SessionLog the sole public
  stream. One cursor, one audit trail; extra async hop; external effects stay
  at-least-once.

### 3.2 The client transport can't express the mirror backlog — O13 (major).

**Problem.** The design's single history route is `GET /events?after=N`, and
SSE carries catch-up. v1 measured its way off exactly that shape: a long
session's initial backlog is 100–400KB of rendered HTML; SSE frames are never
compressed (compressing a held-open stream would buffer it), so v1 serves the
initial slice over a plain GET that gzips 8–9× (391KB → 44KB measured), then
connects SSE with the returned cursors. Also: v1 pages **newest-first** (last
80 blocks, older loaded on demand via `before=` cursors) — `?after=N` is
forward-only and cannot ask for "the last 80". And cursors must snap to
**block boundaries**: a command block is `CommandStarted` +
N×`CommandOutput` + `CommandFinished`; a cut at an arbitrary position lands
mid-block, splitting its rendering or double-counting interleaved
conversation records (v1 hit exactly this and moved to whole-slot windows).

**Solutions.**

- **(a) Add the two routes v1 has**: gzipped `GET …/backlog?limit=` (newest
  window + cursors) and `GET …/history?before=&limit=`, both snapped to block
  boundaries; SSE carries increments only. Tradeoffs: "one route" becomes
  three; block-snapping needs a block-identity concept in the read model (v2
  has one — the tid/copy-group — so it's bookkeeping).
- **(b) One route with a window spec** (`?before=`, `?limit=`,
  `?order=desc`) fetched over plain GET with compression. Tradeoffs:
  semantics become "a paginated view over the log" rather than "the log,
  filtered" — fine, but the doc's simplicity claim should say so.
- **(c) Pre-rendered backlog blob per session**, maintained by the
  mirror-feed reactor; the page fetches one immutable, cacheable blob then
  subscribes. Tradeoffs: caches one surface's presentation server-side, which
  the design's own presentation-ownership rule forbids.

### 3.3 `agent_attention` is the most complex fold and the only non-rebuildable one — O14 (major).

**Problem.** Rebuild ("drop a follower's tables, zero its bookmark, replay")
is the design's primary repair tool, sold as routine. The one follower
exempted is agent_attention: because it *emits* events, replaying it re-emits
its transition history to consumers that treat transitions as news (the alert
policy would re-arm alerts for questions answered last week); rebuilding it
is declared a runbook operation requiring quiescing its four dependents (tab,
alert, session_index, active_time). But the attention state machine ports
v1's tab-color logic — by a wide margin the most-revised, most-bug-ridden
subsystem in the repo. The design makes the component most likely to need
iterative correction the one that can't receive it — and the migration's
parity gate ("v1 and v2 agree on tab outputs for days") demands exactly that
iterate-and-rebuild loop.

**Solutions.**

- **(a) Split it**: a pure `attention_state` fold (materialized, freely
  rebuildable) + a thin transition emitter that appends `Changed` only on
  actual state deltas, with consumers deduping on `(lineage, seq)`. This
  promotes the design's own deferred "durable emission dedup" to day one.
  Tradeoffs: adds the dedup machinery; the one place a fold needs its own
  stable sequence number.
- **(b) Make consumers replay-tolerant.** The tab reactor is already
  idempotent (paints, dedups by last-verified paint); guard the alert policy
  with an event-timestamp staleness window ("ignore transitions older than N
  minutes" — arguably needed for cold start anyway). Tradeoffs: a time-based
  guard inside the component the design most wants deterministic, invisible
  until the day you rebuild.
- **(c) Flatten the graph**: no intermediate producer — tab, alert,
  session_index, active_time each fold attention directly from SessionLog,
  sharing one pure state-machine module. Tradeoffs: four executions of the
  same computation (the design's stated fear: two independently derived
  liveness answers was a measured v1 bug), mitigated only by the shared
  module being *the* implementation; the alert policy must derive transitions
  itself by keeping prev-state.

### 3.4 Head-of-line blocking, write amplification, and caps-as-truncation — C10, F7, O3 (critical), O10 (major). Postgres changes the numbers, not the shape.

**Problem.** All sessions share one SessionLog. The reporter flushes
~8KB/50ms, so a 10MB build log ≈ 1,250 frames at 20Hz (v1's tailer: ~80 ops
at 2.5Hz — a ~16× row multiplier). Each frame costs: one Intake append + one
blob write + one SessionLog transaction + **one transaction per subscribed
follower per event** (the library processes one event per tracking commit;
catch-up batches are 10 per SELECT by default). The latency-critical chain
for an *unrelated* session — envelope → map → attention → tab paint (v1:
~0.1ms raw-socket) — queues behind the flood, all through one serialized
writer (on Postgres: the exclusive insert lock + per-commit fsync). The
design's entry benchmark measures tab-paint latency and a synthetic stream
*separately*; the failure mode is the interaction. O10 adds a data-loss
twist: the design cites v1's three caps as precedent for ingestion truncation
(`CommandOutput(truncated=true)`) — but v1's caps (per-pump read ceiling,
per-op split, display-only line cap) never lose a byte; every byte reaches
the ops table, which is what makes `⧉out` on scrolled-back/resumed blocks
copyable. And v1's per-block CAP_* excerpts are *presentation* (the web
deliberately shows the full text); moving them to ingestion destroys the full
text before any surface sees it — in a design whose thesis is that v1
wrongly fused presentation with semantics.

**Solutions.**

- **(a) Keep bulk output out of the event log entirely** (the option three
  findings independently converge on). Chunks append to a growing blob
  (staging file per tid); the log carries only coarse cursor events
  (`CommandOutputProgressed(tid, offset)` at ~v1's cadence); the pane-host
  and SSE read bytes from the blob by offset. Truncation becomes a blob-size
  policy, orthogonal to events. Tradeoffs: output stops being
  position-ordered with everything else, so interleaving with surrounding
  events becomes a read-time merge (which v1's web already does today); old
  replays degrade when command-output blobs expire (already accepted by the
  retention design).
- **(b) Coalesce at intake.** The socket server aggregates chunk frames per
  tid within a ~200ms window into one envelope. Tradeoffs: live-mirror
  latency 50→200ms (imperceptible); intake stops being byte-faithful per
  *frame* while staying byte-faithful in content; ~4× fewer notifications —
  a mitigation, not a fix.
- **(c) Keep one log, engineer the lanes, and fix the gate.** Per-application
  DBs (the config option exists — make it default) so chunk floods contend
  only with their consumers; put attention/tab/alert on their own runner
  thread with tight topic filters; use the library's `New*` runners (the
  verifier: they process recordings directly with **zero SELECTs**, vs one
  SELECT per prompt in the old runners); batch appends (verifier: one
  `save()` of 100 events is ~5× cheaper per event); and change the migration
  entry criterion to a single **compound** benchmark — tab-paint p99 measured
  *while* the synthetic build flood runs, plus a second concurrent session,
  with a stated pass threshold. Tradeoffs: buys headroom, not isolation;
  topic filters have no index, and a rare-topic follower re-walks unmatched
  tails on every prompt.
- **(O10, regardless of choice)**: split the vocabulary — "chunking bounds"
  (ingestion, never lossy) vs "display caps" (per-surface); add a
  separately-named runaway-guard truncation limit (e.g. 50MB/command) that is
  honestly reported.

### 3.5 SessionLog grows without bound; every rebuild replays all of it — O16 (major). Postgres makes deletes safe but doesn't shrink rebuild cost.

**Problem.** Intake and command-output blobs are bounded; SessionLog — the
truth — is never pruned, by design. Measured v1 scale on this machine: 3.7GB
audit DB, 319MB parked mirror history, 505k hook_events rows; v2 stores
strictly more (payloads as intake + domain events + blobs) plus the chunk
multiplier. Two uncosted consequences: **rebuild time grows monotonically
forever** (after a year, fixing a counter bug means replaying every event
ever recorded — eroding "derived state is disposable", the design's headline
benefit); and **lineage-scoped rebuild doesn't scope the read** — the
notification log is global, followers select by `position > bookmark` with at
most a topic filter, no lineage predicate, so "rebuild just this session's
fold rows" still walks the entire store.

**Solutions.**

- **(a) Epoch the log.** Roll SessionLog into a new application (= new
  table/DB) periodically or at a size threshold, rolling only at lineage
  boundaries so no session spans epochs; ended lineages live in closed
  epochs, candidates for archive/compression. Rebuilds replay only relevant
  epochs. Tradeoffs: cross-epoch reads need federation; the single events
  route gains an epoch dimension.
- **(b) A lineage index side table** `(lineage, notification_id)` maintained
  on append, so a scoped rebuild selects only that lineage's positions.
  Tradeoffs: a hand-maintained index outside the library's tables
  (recorder-subclass work — proven possible by the verifier); helps scoped
  rebuilds only, not global ones.
- **(c) Declare a truth-retention horizon.** Sessions older than N months
  export to cold archive and leave the live log; the dashboard reads the
  archive for history, no follower does. Tradeoffs: contradicts "every input
  replayable" past the horizon; on Postgres at least the deletes are now
  safe.

---

## 4. Domain-rule correctness

### 4.1 The `BranchDiscarded` detection rule is the one v1 measured and rejected — O5 (critical), plus C4 (major).

**Problem.** Context: Claude Code's transcript is a **tree**, not a list —
every record has a `parentUuid`. A rewind (checkpoint restore) or an Esc-Esc
prompt take-back abandons a branch: the file keeps every record, and the next
send just parents itself to an older node. The design detects this as: "the
next transcript record attaching to an older-than-leaf parent ⇒ emit
`BranchDiscarded(leaf_uuid, new_parent_uuid)`", and wires current-state folds
to compensate (a goal set on the dead branch must clear; the ctx fold must
stop honoring a compaction boundary that was rewound past). But v1's docs
record that the tree forks *legitimately* all the time — an attachment hangs
off the record it annotates; **parallel tool calls each parent their result
to the same assistant message** — measured ~30 such forks in a 250-record
session with no rewind at all. v1's actual rule is deliberately narrow: only
**two user PROMPT records sharing one parentUuid** count (all but the last
dead, each taking its whole subtree), validated across 30 transcripts as
"drops exactly the two known discards and nothing else". As written, v2 fires
~30 spurious `BranchDiscarded` per ordinary session — and the folds *act* on
it: the ctx bar repeatedly discards a valid compaction boundary (re-creating
the exact measured bug class v1 fixed: 523k shown against a context holding
9k), the goal card keeps clearing. Secondary problems: an Esc-Esc take-back
also produces a discard, but the design names the event "(rewind / reverted
compaction)" and gives ctx a rewind-specific response — wrong for a
take-back; and only transcript-derived events carry tree ancestry — commands,
monitors, file ops, usage have none, so "filter the mirror to the live branch
at query time" is undefined for ~80% of mirror content. C4 makes the
fold-side half concrete: `tasks`/`goal`/`title`/`model_state`/`compaction`
are declared live-branch-true, but clearing them from
`(leaf_uuid, new_parent_uuid)` requires each fold to have stored a branch
anchor per fact and to prove membership in the discarded subtree — machinery
the design doesn't supply.

**Solutions.**

- **(a) Port v1's rule verbatim, with its fixtures, plus a `cause` field**
  (`prompt-takeback` | `rewind` | `compaction-revert`) so each fold responds
  only to causes that mean what it thinks. Payload = the dead subtree's root;
  folds walk descendants. Tradeoffs: essentially none architecturally — this
  is what the design *meant*; the risk is the current wording is what gets
  built.
- **(b) Split the event**: `PromptDiscarded(dead_uuid)` for the
  high-confidence prompt-sibling case; `BranchRewound(...)` emitted only when
  a rewind gesture's own completion event corroborates. Tradeoffs: a
  terminal-side rewind has no gesture to corroborate, so you need the sibling
  rule anyway as fallback; two events where one would do.
- **(c) No event at all — pure read-side filtering.** The query service
  computes the live-branch uuid set from the transcript at read time (v1's
  approach), and ctx/goal fold over transcript-derived events that carry
  ancestry, filtering as they go. Tradeoffs: contradicts the
  compensation-by-subscription model and reintroduces read-time work the
  design wants gone — but it is provably immune to mis-detection, because
  there is no detection.
- **(C4's fold-side options, needed under (a) or (b))**: anchor every
  branch-sensitive event with a transcript uuid or branch epoch and index
  ancestry per fold (precise; not every observation has a natural transcript
  record) — or recompute the affected facets from the live transcript on each
  discard (simple compensation; costlier, tool-specific) — or reclassify
  unanchorable facts as "last observed" rather than live-branch-true (honest;
  a UI demotion).

### 4.2 Liveness closers have no evidence source; there is no prober tier at all — F1 (critical), O11 (major), O12 (major).

**Problem.** Several closers require actively probing the OS: **monitors** (a
monitor writes in bursts holding no file handle, so only its command
*process* proves life — v1 finds it via `ps` with hard-won normalization
fixes and polls it); **session host death** (kill -9 or terminal crash fires
no SessionEnd; v1 infers from pid death); **standalone codex end** (rollout
EOF + pid death). But the architecture gives these nowhere to run: mapping
executes only when an envelope arrives, and a dying process emits **no
envelope**; the FileWatcher port only reports file changes, and a dead
monitor stops changing files — silence is the signal; and the design bans
idle timeouts. Consequences it names itself: a dead session suppresses
alerts, inflates active time, and blocks relaunch via the live-lineage guard;
the relimit relaunch waits on `SessionEnded` forever. O11 generalizes: v1 has
five load-bearing **periodic probers** with no v2 home — the ghost-suggestion
screen read, the terminal-draft-sync screen read (an entire subsystem: the
text you typed into the kitty box but didn't send, synced to every device's
composer — absent from the design entirely), the ask-region screen diff
(which the design assigns to the alert *policy*, violating its own "policies
decide, reactors act" rule by putting a seconds-long `kitten @ get-text`
subprocess inside a supposedly deterministic decision component), codex's
screen reads, and (O12) the per-model weekly limit poll. O12's own finding:
the design sources per-model windows from the statusline mapper, but the
statusline **verifiably does not carry them** (v1 checked live payloads); v1
gets them from an undocumented OAuth endpoint via Claude Code's keychain
tokens with a delicate token-rotation-writeback protocol (the naive version
caused daily 401 login loops) — none of which has an envelope kind or
component in v2. Worse, v2 puts the result inside `account_usage`, which the
relimit policy reads to pick migration targets — v1 deliberately forbids the
migration path from depending on an API ("core stays tokenless").

**Solutions.**

- **(a) Add a prober tier** (the merged F1a/O11a recommendation): a fourth
  component class — supervised, timer-driven, effect-performing — whose
  outputs enter as envelopes (`obs:liveness`, `obs:screen`,
  `probe:oauth-usage`). The watch supervisor registers pid watches on
  `MonitorStarted`/`SessionStarted` (macOS kqueue `EVFILT_PROC`/`NOTE_EXIT`
  gives true event semantics, polling fallback); screen probes emit only on
  change (v1's discipline). Mappers close on the evidence like any other; it
  lands in intake so replay/remap can reproduce why a closer fired.
  Tradeoffs: a fourth kind in a proudly three-kind taxonomy; a registry of
  active watches; pid-reuse races on attach.
- **(b) Fold probing into existing kinds**: a liveness *policy* that
  schedules clock callbacks and probes directly (no new envelope kind; but a
  policy now acts on the world, and the evidence never enters intake — remap
  can't reproduce it); or classify probers as *reactors* with a widened
  emission license (a screen read is its effect; but that makes reactors an
  input source, muddying "reactors are the outbound edge").
- **(c) Push liveness to the edge**: baqylau-exec/watchdog processes whose
  socket EOF is the death evidence. Tradeoffs: covers only what can be
  wrapped (Claude Code's Monitor tool commands may not be injectable), and
  re-introduces the fleet of detached processes v2 exists to remove.
- **(O12 specifically)**: give the OAuth poll its own prober + its own fold,
  consumed only by the dashboard strip, keeping `account_usage` tokenless as
  the relimit policy's sole source (two tables the UI joins); or keep one
  table with the field marked advisory + a test forbidding relimit from
  reading it (fragile); or defer per-model bars entirely (visible regression;
  the rotation protocol gets rediscovered later).

### 4.3 Interrupted compaction has no closer — F8 (major).

**Problem.** Claude Code emits `PreCompact` when context compaction starts
and `PostCompact` when it ends; compaction runs ~2 minutes emitting nothing
else. The dashboard animates the ctx bar during it. An Esc mid-compaction (or
a crash) fires **no** `PostCompact` — v1's fix is a read-side expiry: the
"compacting" latch expires after 15 minutes because "an animation must fail
OFF". v2 has `CompactionStarted/Ended` and a compaction fold, but no closer
for the interrupted case — and its "closers are evidence-triggered, never
idle timeouts" rule bans the shape of v1's own fix. Result: the fold row
sticks "started" forever; the ctx bar breathes violet indefinitely.

**Solutions.**

- **(a) Port the read-side expiry verbatim**: the facet query treats
  `CompactionStarted` older than COMPACT_MAX_S with no `Ended` as off. The
  log honestly records "started, never observed ending"; no event is
  fabricated. Tradeoffs: philosophically a display timeout — but so was v1's,
  deliberately, because "was it interrupted?" is genuinely unknowable from
  available signals. Apply it in the facet query (one place).
- **(b) An evidence-triggered closer**: any next transcript record or hook
  that is not `PostCompact` after a `CompactionStarted` proves the compaction
  ended or was interrupted → `CompactionEnded(reason="inferred")`. Tradeoffs:
  preserves the no-timeout purity and lands truth in the log; needs careful
  fixtures against the completion race (the transcript's compact-boundary
  record and the PostCompact hook arrive in some order — firing on the
  boundary record itself would mislabel a *successful* compaction). New
  inference v1 never validated.
- **(c) Both**: closer for the common case, expiry as the fail-OFF backstop —
  matching the design's belt-and-suspenders pattern elsewhere. Two mechanisms
  to test; but "must fail OFF" deserves a backstop independent of inference
  correctness.

### 4.4 The lineage fork-detection evidence is misstated, and its guarantee is unachievable — O7 (major).

**Problem.** The design claims lineage resolution happens before any append
using "the evidence (resume source, background-job id, cwd, transcript path)
in the envelope itself", concluding "no event is ever appended under a wrong
lineage." But per v1's measurements: on `--resume`, the SessionStart hook
fires under the **old** sid with `source=resume` — evidence about the
predecessor, under the predecessor's identity; the successor's first envelope
is an arbitrary mid-conversation hook carrying an unknown sid, no marker,
scrubbed env. On backgrounding there's no SessionStart at all. v1's actual
evidence is: a take-once note keyed by **cwd**, written by the predecessor at
its own start; the *absence* of an `InstructionsLoaded` mark for the new sid;
and a liveness check on the predecessor's DB — and v1 records this as
**insufficient**: a live mis-adoption happened (one session's early event
adopted an unrelated concurrent same-cwd session and stole its panes). Two
same-cwd sessions starting simultaneously are genuinely ambiguous. And v2
raises the stakes: in v1 a mis-adoption is a runtime mistake (rename a DB
back, retag panes); in v2 the lineage is the aggregate key, so a wrong
assignment writes another session's events into your aggregate
**permanently** — repairable only by remap.

**Solutions.**

- **(a) Port v1's evidence exactly, drop the absolute, add a repair path**:
  state the residual ambiguity, and add a first-class
  `LineageReassigned(from, to, from_version)` event so a mis-assignment is
  corrected by *appending*; folds handle it like `BranchDiscarded`.
  Tradeoffs: a second compensation event class; cumulative folds must decide
  whether to move counters.
- **(b) Delay the decision.** Buffer a never-before-seen sid's envelopes in
  Intake (they're durable there) and append to SessionLog only once an
  `InstructionsLoaded`/`SessionStart` arrives for it (⇒ new lineage) or a
  short evidence window closes (⇒ adopt). Decides on positive evidence.
  Tradeoffs: a latency window exactly when the tab most wants to repaint, and
  a timeout in a design that bans them.
- **(c) Strengthen the evidence with the transcript path** — *if* a
  resumed/backgrounded continuation keeps writing the predecessor's
  transcript file, path identity discriminates concurrent same-cwd sessions.
  Tradeoffs: unverified today (v1 not using it is weak evidence against);
  needs a measurement step the migration plan currently lacks.

### 4.5 The `Notification` hook has no domain event for two of its three branches — O15 (major).

**Problem.** Claude Code's `Notification` hook carries one free-text
`message`, and v1 demultiplexes it into four outcomes: a permission/approval
match (regex) → red tab (this is the **only** source of red besides ask/plan
dialogs); mid-turn → ignore (teammate idle pings fire notifications
constantly; treating them as "your turn" turned the tab green while the team
worked — measured); bg job running → blue; bg just finished → magenta ("it's
taking over, not your turn"); otherwise → green ("your turn"). v2's
vocabulary has only `PermissionRequested/Resolved`. The state-dependent
branches rightly belong in the attention fold — but the raw notification has
no event to reach it. And the permission branch is a regex over undocumented
free text: as a mapper decision, a Claude Code wording change silently turns
every permission prompt into a green "your turn" **baked into truth**,
fixable only by remap.

**Solutions.**

- **(a) A low-interpretation event**: `AttentionRequested(kind, message)`
  with `kind ∈ permission|idle|agent-ping|unknown`; the fold applies the
  state-dependent rules. The regex still runs at map time but degrades to
  `unknown` instead of the wrong color, and the raw message rides along so
  re-classification needs no remap. Tradeoffs: a message string in the truth
  vocabulary — defensible as evidence, not display.
- **(b) Emit the raw hook** (`HookObserved("Notification", payload)`) and let
  the attention fold interpret. Maximum fidelity, zero remap risk. Tradeoffs:
  a hole in the mappers-own-interpretation contract big enough to drive the
  whole design through; makes the fold tool-aware.
- **(c) Permission-only event; derive green from `Stop`/`TurnEnded`.** More
  principled ("green = the turn ended"). Tradeoffs: v1's green demonstrably
  needs the Notification path too (Stop doesn't fire in every case that
  leaves you holding the conversation), and it loses the separately-earned
  bg-finished/teammate-ping distinctions.

### 4.6 Strict transcript-ordering un-ships live subagent output — O22 (moderate).

**Problem.** The design's subagent rule: a subagent's story is emitted in
transcript order (the transcript is the only in-order source of its
prompt/messages/tools/result); its hook/report envelopes "corroborate but
never reorder", via a resequencing buffer. But v1 deliberately excepts one
case: a subagent's foreground Bash command **is** teed and streamed live
(keyed by tool_use_id; the substream spawns the tailer when it reaches that
tool_use and suppresses its own render). Under the v2 rule as written, a
subagent's output chunks wait in the buffer until the transcript's
tool_result lands — i.e., until the command finishes. That's the pre-fix
behavior v1 specifically built `CLAUDE_MIRROR_LIVE_FG_SUB` to eliminate.
Also: the buffer is unbounded in the failure case — a subagent killed
mid-tool leaves buffered evidence no transcript record will ever release; the
agent-death closers don't say they flush it.

**Solutions.**

- **(a) State the split**: structural events (prompt, message, tool_use,
  result) are transcript-ordered; volumetric events (chunks, exit codes) emit
  on arrival, attached by tid; presenters place them inside the structural
  frame. Plus a rule that any agent closer flushes the buffer. Tradeoffs:
  none — this is what v1 does; it needs writing down.
- **(b) Emit everything on arrival; ordering becomes read-side** (presenters
  already do semantic ordering for child-task cards). Tradeoffs: every
  surface must implement the ordering — the duplication the shared child-task
  module was extracted to prevent; but it deletes a stateful buffer (helping
  2.1).
- **(c) Accept the latency** and document that subagent fg output isn't
  live. A real, measured feature regression.

### 4.7 The in-shell reporter regresses four measured properties of v1's tee — O9 (major), F12 (minor).

**Problem.** (1) The design's injected snippet is missing v1's **blank line
before `}`** — load-bearing: a command ending in a line-continuation
backslash eats the newline, welding `}` onto the last line — a syntax error
for a command that ran fine unwrapped. An implementor copying the snippet
reintroduces a fixed bug. (2) v1 tees stdout+stderr into **one file in append
mode**, so interleaving is preserved by the filesystem; the design pipes them
to two tee processes → two socket streams with independent flush timers — the
daemon cannot recover the order, and mirror golden-file parity will fail.
(3) The shell doesn't wait for process substitutions, so
`baqylau-report --exit $?` can reach the daemon **before** the tees flush
their last chunks — truncated blocks, finish chip above late output. v1 has
no such race (the command itself writes the done-sentinel after the tee,
in-line). (4) `; baqylau-report …` **clobbers `$?`** in the persistent shell
— an agent that runs a command and checks `$?` in its next tool call silently
reads the reporter's exit status.

**Solutions.**

- **(a) Port v1's shape and add transport minimally**: one tee receiving both
  streams (stderr via a second fd), order stamped locally; keep the blank
  line; trailer becomes `rc=$?; baqylau-report --tid T --exit $rc; (exit $rc)`
  so `$?` survives; the reporter blocks until the tee flushes (tiny
  handshake), or the daemon closes blocks on exited-after-both-EOFs with a
  grace fallback. Tradeoffs: a few ms per command teardown; a wedged tee
  needs a timeout that closes the block anyway.
- **(b) Tee to a file; the daemon tails it** with the FileWatcher port (which
  already specifies v1's full byte-discipline). Interleaving/ordering/flush
  solved by the filesystem; the socket carries only started/exited frames.
  Tradeoffs: reintroduces plain files the design wants gone; harder if
  capture ever needs to work remotely.
- **(c) Merge streams in the shell** (`2>&1` into one tee) and lose the
  out/err distinction — named mostly to record the trilemma: {distinct
  streams, preserved order, live streaming} — pick two. v1 chose order+live
  over distinct; the design should choose explicitly.

### 4.8 Per-agent live usage has no source under the OTEL-watermark rule — F13 (minor).

**Problem.** Token accounting: OTEL (Claude Code's telemetry export) is
authoritative because it sees hidden "auxiliary" agents that never write
transcripts (11.6% of one session's cost, invisible to transcript folding).
The design gates transcript-derived usage behind "SessionEnd fallback only,
if no OTEL seen" to prevent double-counting. But OTEL attributes only
`main|subagent|auxiliary` — never a specific agent id — while v1's
agent-scoped scoreboard folds *one agent's* tokens live from its transcript.
Suppress transcript usage until SessionEnd and the agent-scoped view has no
live numbers; emit it live and the usage fold double-counts against OTEL's
subagent bucket.

**Solutions.**

- **(a) Two declared ledgers**: billing totals from OTEL only; per-agent
  display figures from transcript events stamped `source=transcript`,
  excluded from totals; reconcile at SessionEnd. Honest about what each
  source can actually attribute; surfaces must respect a "which number is
  this" rule.
- **(b) One ledger with fold-side dedup** preferring OTEL where both cover
  the same work — but OTEL carries no correlation key, so "same work" is a
  time-bucket heuristic, re-risking the double-count class.
- **(c) Drop live per-agent tokens** (ctx only on agent cards, tokens at
  finish) — declared regression, cheap and safe.

---

## 5. Feature gaps (v1 features with no v2 home)

### 5.1 Agent scope, per-agent scoreboards, jobs and monitors views — C5 (major).

**Problem.** In v1's dashboard, clicking a subagent re-points the whole
session view at that agent: its mirror, its jobs, its monitors, a swapped
scoreboard, per-agent ctx bars. This is *derived state*: agent rows are built
by merging stream-lifecycle records with state; jobs own their output;
monitors are their own list. The design offers only an `actor=` filter on the
events route and a lineage-level facet bundle — no
`agents`/`jobs`/`monitors`/per-agent-usage projections exist in the fold
registry, and raw filtered events cannot serve those cards.

**Solutions.**

- **(a) Actor-keyed projections** (agent lifecycle, usage/ctx, monitors,
  jobs, output groups) plus scoped routes — full parity, a substantially
  bigger registry.
- **(b) A generic actor-scoped query service** over SessionLog with indexed
  event tables and declared scope rules — fewer bespoke folds, risks
  request-time folding cost.
- **(c) Narrow v2 agent scope** to event browsing and document the
  regression.

### 5.2 Composer queue, drafts, and terminal draft sync — C6 (major), O23.

**Problem.** v1's composer is not a text box; it's a correctness contract: a
message sent mid-turn lands in Claude Code's own queue and the page pins a
bubble **until a transcript record proves delivery** (survives reloads);
unsent drafts persist per-session server-side (device switches restore them)
with versioned writes so a stale save can't resurrect sent text; and the
**terminal draft sync** subsystem (screen-scraped, eight earned rules) moves
text typed in the kitty box to every device. The design says only
"composer/new-session drafts" are web-local state. Without the contract:
queued sends vanish on reload, terminal drafts don't travel, stale writes
resurrect text.

**Solutions.**

- **(a) A durable composer projection** with queue entries, seq/tombstone
  conflict resolution, origin metadata, transcript-based delivery
  reconciliation — full fidelity; stateful web infrastructure outside the
  log.
- **(b) Promote sends/queue state to domain events** and derive the UI queue
  — auditable and replayable, but records private unsent prose in truth and
  blurs the web-local boundary.
- **(c) Browser-only drafts**, dropping cross-device sync and queue
  persistence — a deliberate regression. (Terminal draft sync additionally
  needs the prober tier from 4.2 regardless.)

### 5.3 Pane lifecycle has no owner — F9 (major).

**Problem.** The design ports the pane *renderer* and click transport
meticulously but nothing opens, closes, or resizes panes: no reactor in the
table opens a mirror at SessionStart, closes it at SessionEnd, remembers
per-project widths, or handles the `toggle|grow|shrink|reset|setpct`
keybindings. v1's equivalent is a bug-scarred subsystem (anchoring rules, the
nested-host guard preventing a codex-inside-Claude from opening a second
mirror, the session-end ordering: close panes → park → clear tab).

**Solutions.**

- **(a) A `pane_supervisor` reactor** consuming
  `SessionStarted/Ended/SidAliased`, effecting open/close/resize + pane-host
  spawn, with `PaneOpened/PaneClosed/PaneOpFailed` effect events; sizes in a
  prefs store; geometry gestures added to a capability vocabulary. The v1
  end-ordering must be re-expressed across now-independent reactors.
- **(b) The terminal package owns lifecycle** (terminals/kitty subscribes to
  session lifecycle itself) — geometry knowledge stays jailed, but creates an
  effectful component outside the reactor discipline.
- **(c) Panes become user-initiated only** — deletes the subsystem; a real
  UX regression that should be a product decision, not an omission.

### 5.4 Codex tab title — C7 (major).

**Problem.** For Claude Code, v1 deliberately never sets the kitty tab title
(Claude Code emits its own OSC title; a second writer disagrees — measured
bug). For **codex the argument inverts**: its TUI emits no OSC title, so v1's
rename gesture must retitle the tab itself — the one sanctioned
`set_tab_title` caller. v2's Terminal port has tab *paint* but no tab *title*
operation, and only a tab-color reactor exists: a codex rename updates the
web card and leaves the terminal tab stale.

**Solutions.**

- **(a)** Add `set_tab_title` to the Terminal port + a codex-scoped title
  reactor with verified effect events.
- **(b)** Make title sync a pane-host/terminal-package capability — smaller
  core port, per-terminal divergence.
- **(c)** Leave codex tabs unmanaged — visible standalone-codex regression.

### 5.5 The ⧉ copy verb — O21 (moderate).

**Problem.** Mirror blocks carry ⧉cmd/⧉out links: clicking pipes text to the
OS clipboard (`pbcopy` etc.) plus a feedback line. The design's click
transport covers only expand/collapse-with-viewport-restore. No port has a
clipboard operation, no reactor owns copy. Semantic wrinkle: v1 copies **what
is displayed** (the pretty-printed reflowed command — "WYSIWYG; equivalent
runnable bash"). In v2 the pretty-printed form exists only inside the
pane-host's renderer library — a daemon-side copy would silently copy the raw
command instead.

**Solutions.**

- **(a) Route copy to the pane-host**: it has the rendered block; it shells
  out to the clipboard. Preserves WYSIWYG; the pane-host gains an inbound
  channel and stops being perfectly thin; copy fails while the pane-host is
  dead.
- **(b) `set_clipboard(text)` on the Terminal port + a copy reactor** copying
  the raw fact text — simplest, fits the architecture, but changes the
  feature (raw one-liner instead of the pretty-printed form) — a small,
  daily-visible regression.
- **(c) The daemon imports the renderer library for copy only** — needs the
  pane width to reproduce the display; violates "every surface owns its own
  presentation".

### 5.6 The audit's diagnostic layer — C8 (major), O17 (major).

**Problem.** v1's debuggability rests on: every hook event recorded with a
**decision string** ("what the handler chose and why" — the column that makes
the table diagnostic, per CLAUDE.md), canned anomaly queries (77 named bug
signatures), a merged timeline command, and a triage skill — all SQL over one
database. v2 offers "intake + structlog" and defers the event→envelope/rule
provenance table until "the first painful mapper-debugging session". But
intake says what arrived and events say what was concluded — the **decision**
(which closer fired, which rule version, why the paint was skipped, why a
lineage was adopted) lives only in a text log with no correlation id to
either. The canned anomalies have no successors; the migration — the period
with the most novel bugs — runs without the instrument that made v1
debuggable. C8 adds the durability angle: with intake bounded and command
blobs expiring, "which raw payload arrived / was the effect verified" stops
being answerable after the horizon.

**Solutions.**

- **(a) Promote the provenance table to day one**: every appended event
  records `(position, envelope_ids, rule_name, rule_version, decision_text)`
  — the decision column generalized, making the closer catalogue
  self-describing. Cost: one row per event (interacts with 3.4's volume);
  cheap to write, needs discipline to keep honest.
- **(b) A `why` field on domain events themselves** — no join, versioned with
  the event; puts diagnostic prose into the truth vocabulary forever and
  inflates every event.
- **(c) An `explain` fold** materializing timeline/anomaly tables —
  retroactively extensible over old data, but can only see what events
  contain, so it needs (a) or (b) underneath for mapper-level questions.
  (C8's additional option: keep the v1 audit running independently until v2
  demonstrably matches it — two audit systems for longer, lowest risk.)

### 5.7 Migration holes — C9 (major), F10 (major), O18 (major).

**Problem.** Four distinct gaps. (1) **The `updatedInput` hand-off**: only
one hook may rewrite commands; the design transfers ownership per-session via
a flag, but there's no durable compare-and-set lease, no daemon-health gate,
and v1's cmd-pre must be *modified* to consult the flag (unowned work); with
both hooks wired, Claude Code's merge semantics for two hooks returning
`updatedInput` are version-dependent and unmeasured; and if the shim answers
with `permissionDecision:"allow"` when it isn't the owner, it changes
permission behavior during the very phase whose gate is "v1 and v2 agree". A
flag flip can also disable v1's capture and then lose v2's when the daemon
dies — rollback can't reconstruct the gap. (2) **No v1 data import**: on
cutover the stats page (computed over v1's audit since inception) starts
empty; parked v1 sessions resume with no mirror backlog; the parity gates
can't even test parked-history behavior. (3) **The SPA**: v2 mandates
TypeScript; v1's SPA is 10,304 lines of behavior-dense JS — plausibly the
single largest work item, invisible in the plan. (4) **The test suite**:
36,787 lines whose fixtures the design says are "ported, never rewritten",
with no step, ordering, or acceptance criterion — in a plan gated on
cross-system agreement.

**Solutions.**

- **(Hand-off)** (a) a durable lease keyed by lineage + sid aliases, acquired
  only after a daemon health check, released on supervised shutdown; (b) v1
  keeps sole ownership until v2 can consume a durable v1-compatible feed —
  slower, avoids the riskiest hand-off; (c) the edge spools to disk *before*
  any ownership decision and both consumers dedup by envelope id. Plus,
  regardless: a measured checklist item for dual-hook `updatedInput` merge
  semantics, and the responder consults the owner flag before rewriting.
- **(Data)** (a) a one-shot importer (`baqylau import-v1`): audit rows +
  parked state DBs → backfill envelopes marked `source=backfill`, mapped into
  reduced-fidelity lifecycle/usage events — doubles as the largest mapper
  test corpus; (b) federate reads for a sunset window (query service merges
  v2 tables with v1's audit.db until attrition) — no import, but v1 schema
  knowledge lives inside v2 for a year; (c) declare the reset explicitly —
  cheapest, guts the longitudinal stats page.
- **(SPA)** (a) an explicit SPA step with its own gate; (b) keep the v1 SPA
  and re-point it at v2 via an adapter serving v1's payload shapes —
  apples-to-apples parity in the same browser, adapter is throwaway; (c)
  accept a long dual-running period.
- **(Tests)** add the port as a step with per-plane acceptance criteria.

### 5.8 Security — O19 (moderate), O20 (moderate).

**Problem.** (O19) The daemon socket trusts filesystem permissions (0600)
plus two semantic guards — but every process running as you includes **the
Bash tool of every Claude session**. A model can forge `hook:Stop` for any
lineage (flip tabs, fire "done" alerts), forge `report:exited` (fabricated
exit codes), and forge `presence:` beats — which *gate alert suppression*, so
it can silence its own alerts — all appended to immutable truth. The
threat-model section covers HTTP carefully and never mentions the socket, now
the more privileged surface. (O20) The auth section reads as replacing v1's
edge identity (Cloudflare Access with MFA/revocation, in front of a
documented reaching-the-control-plane-is-RCE surface) with a static bearer
token in a cookie.

**Solutions.**

- **(O19)** (a) split the socket by trust tier with a daemon-issued token for
  edge components — fiddly distribution, and a token readable by the model
  isn't a boundary against the model; (b) make forgery *detectable*: stamp
  envelopes with peer pid/ancestry (`getpeereid`), anomaly-rule on
  implausible senders — after-the-fact, version-fragile; (c) accept and
  harden the one silent channel: presence/viewing comes only from the
  authenticated HTTP API, never the socket (the same daemon-minted-only
  treatment the `terminal` device id already gets).
- **(O20)** (a) rewrite the section as layered: edge identity required and
  primary, app token as depth, POST guard as browser-vector defense,
  127.0.0.1 bind non-negotiable — same mechanisms, correctly ranked; (b)
  split read vs control tokens — probably unusable from a phone where control
  gestures are the point; (c) no app credential, daemon refuses public-origin
  requests lacking the edge's identity header — couples to one edge product.

### 5.9 Small gaps, one list — F14, O23.

Each is cheap now, a bug report later:

- **keep-awake** is named as a pref but is an *effect* with no owning
  reactor;
- **alert deep links** need the public proxied origin (a 127.0.0.1 link is
  dead on a phone) — the config knob isn't ported;
- **dictation** omits the layered keyterms contract (project
  `.claude/deepgram-keyterms` walk, then global);
- the **web launcher** omits the trust-prompt hazard (an untrusted cwd stalls
  a launched session on Claude Code's "do you trust this folder?" dialog — a
  v1 hard-won rule);
- **`close`** (kill the tab) is missing from the gesture vocabulary;
- **usage dedup by `message.id`** (v1's measured 2.2× token inflation fix —
  one assistant message spans multiple transcript lines each repeating usage)
  is not restated as the mapper's dedup key;
- **cross-reactor effect ordering** is unstated (the tab reactor follows two
  logs — a late attention `Changed` arriving after `SessionEnded`'s clear
  repaints a dead session's tab; v1's session-end ordering
  close-panes→park→clear has no cross-reactor equivalent);
- **`PermissionRequested`'s envelope source** is never named;
- **prefs** (mutes, hidden dirs, the global alerts switch) are web-local yet
  the alert policy reads three of them — decisions now depend on state that
  is neither replayable nor backed up;
- the **cost estimate** ("2–3× of patching v1") has no baseline (v1 measures
  ~49.5k lines Python + 10.3k JS + 36.8k tests);
- a **section-numbering** nit (§11 doesn't exist).

**Solutions.** (a) An editorial pass housing each in its owning section.
(b) **The one both reviewers recommended**: a mechanical **v1→v2 coverage
appendix** — every CLAUDE.md feature, env knob, and doc-recorded lesson
mapped to its v2 section or an explicit "dropped, because" — doubling as the
migration gates' checklist. The three legacy inventories produced during this
review round (terminal-side, infra-side, dashboard-side) are the raw
material. (c) Explicitly move any of these into the tradeoff ledger as
conscious drops.

---

## 6. Suggested order of rulings

The findings constrain each other, so decide in this order:

1. **3.4 output-in-or-out-of-the-log** — it reshapes volume, and with it
   3.1's broker load, 2.2's write budget, and 3.5's growth.
2. **3.1 the client checkpoint model** (fold-gating vs composite vs feed log)
   — it fixes the API's keystone sentence and decides what SSE serves.
3. **2.1 + 2.2 + 2.3 together** — they're all "what is durable at which
   instant"; solution (a)/(b) choices here should share the recorder-subclass
   mechanism the verifier proved.
4. **4.1 and 4.4** — pure correctness ports; near-free to rule (both have a
   "port v1's rule verbatim" option).
5. **4.2 prober tier yes/no** — unlocks F1, O11, O12, and 5.2's draft sync at
   once.
6. Everything in section 5 is independent and can be ruled feature by
   feature.
