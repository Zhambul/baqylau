# v4 rewrite design — review 6 (fable)

Reviewed document: `docs/rewrite-design-v4-codex.md` (14,052 lines, read in full,
including the five prior legacy-coverage closure layers §38, §40, §41, §42).

Method note. This is the sixth coverage pass, and the bar for a "new gap" is
correspondingly high: five adversarial reviews are already folded into the
design as normative closure sections, so most obvious findings are closed.
This review was produced by a full read of the document plus targeted
verification of ~30 candidate gaps against the legacy code (grep both sides,
cite file:line). A planned five-subagent parallel audit was cut off by an
account rate limit mid-run; the verification below was completed in-session
instead, so Part 1/2 favors precision over exhaustiveness. To make the
negative results trustworthy, §1.6 lists checks that came back **covered**.

Structure: Part 1 — legacy features not covered; Part 2 — features the design
describes but with gaps or internal contradictions; Part 3 — overall review
(architecture, performance, overcomplication).

---

## Part 1 — Legacy features not covered by the design

After five closure passes the design is a near-superset of the legacy system.
The remaining genuine holes are few and narrow — but two of them are
load-bearing.

### 1.1 Audit evidence loses its outage-degradation path (HIGH — decided, but re-confirm)

Legacy `core/audit.py` never loses evidence when its database is unavailable:
it degrades to an append-only spool (`spool.jsonl`, `core/audit.py:26,71`),
re-ingests the spool on the next successful open, and *audits the outage
itself* so audit downtime is visible after the fact (`core/audit.py:155–177`).
The design explicitly forbids any edge-side spool (§38.19: "no edge audit
spool exists: daemon outage can lose all observations during that interval";
§26.2 "Daemon unavailable"; restated in §42.4). The replacement is an
`ingestion_gap` row recording only that a gap existed.

This is a recorded product decision, not an oversight — but it deserves to be
re-confirmed with eyes open, because it inverts the repo's own core principle
("a mechanism that leaves no rows is undebuggable after the fact",
CLAUDE.md). v4 introduces a single point of failure (the daemon) *and*
removes the evidence trail in exactly that component's failure mode. The
first crash-looping-daemon bug will be debugged blind: hooks that fired
during the outage leave nothing, and the daemon's own supervisor loop is the
only witness. The design's stated reason (edges must not become "a second
reduced Baqylau") argues against edge *decision-making*, but an append-only
file the daemon ingests at startup is not decision-making — it is exactly
what legacy `audit.py` already does, in ~60 lines. Recommendation: either
adopt a minimal write-only edge spool for observation payloads (no reads, no
fallback behavior, ingest-on-restart), or have the product owner re-affirm
the loss explicitly against the audit-coverage invariant it violates.

### 1.2 Terminal control-socket discovery for an env-less daemon (MEDIUM)

Legacy resolves the kitty control socket from `$KITTY_LISTEN_ON`, falling
back to a ppid walk (`/tmp/kitty-<pid>` for each ancestor —
`frontends/kitty.py:256–267`). That works because every legacy process is a
descendant of kitty. The v4 daemon is launchd/systemd-started at login
(§26.3): it is *not* a kitty descendant, inherits no kitty environment, and
hooks no longer talk to kitty directly. Neither the `TerminalDiscovery` role
(§20.1) nor the environment-snapshot allowlist (§10.3 — a closed list that
names no terminal variables) says how the daemon's kitty adapter finds the
control socket(s), or how it handles several concurrent kitty instances
(each has its own socket). Under the design's own §0.2 standard ("an
implementor can write the code without making a decision") this is an
unmade decision on a mandatory Phase 3–5 path (tab paint, pane lifecycle,
screen drivers all need it). Cheapest fix: add `KITTY_LISTEN_ON` (or a
provider-neutral terminal-socket hint) to the §10.3 allowlist so each
AgentSession attempt carries its own terminal endpoint, plus a
socket-enumeration rule for sessions with no snapshot.

### 1.3 `CLAUDE_MIRROR_LIVE_FG_SUB` gate unregistered (LOW)

The subagent foreground live-tee is gated by `CLAUDE_MIRROR_LIVE_FG_SUB` in
legacy (CLAUDE.md; `plugins/claude_code/cmd_pre.py`). The design covers the
mechanism (subagent commands are eligible answerable transforms; §11.2 lists
"environment flags, nested/subagent mode" among eligibility gates) but never
names this gate or its default, while sibling knobs are imported by name
(`CLAUDE_MIRROR_STEP`/`BIAS`/`SCROLLBACK`, `CLAUDE_RELIMIT`, `CLAUDE_AUDIT`,
`CLAUDE_MIRROR_FORMAT`, `CLAUDE_DASH_*`). Either register it in the Claude
edge manifest's eligibility configuration or record its retirement in the
Phase 0 drop manifest.

### 1.4 Session header-bar composition contract (LOW)

Legacy pins user-visible header-bar behavior with tests
(`tests/jsdom/headeract.js`): exact control ordering by reach (✦ ✧ ⊜, then
✎ ⇆ ◉ ↶ ■ ✕, destructive last), the greyed-never-gone rule via one
`gate(btn, ok, why)`, and the two information-bearing stand-down reversals
(⛶ shown while fullscreen is engaged; the conn dot shown once it is *not*
green). §17.4 covers visible-but-disabled reachability generally, and §40.4
demonstrates the design *does* specify client-only contracts when they
encode decisions (keep-awake, PWA shortcuts, live-session strip) — but this
one is absent. Low severity (surfaces own presentation), worth one
paragraph in §40.4 since the ordering and stand-downs are decisions, not
styling.

### 1.5 Historical `sids`/tab-DB registries (INFO — subsumed, confirm in Phase 0)

The legacy global tab DB (`/tmp/claude-kitty-tab.db`: tab colour rows,
watcher pid locks, `sids` registry, `adopt_pending` notes) has no single
successor; its facts are distributed across `terminal_bindings`,
`tab_paint` state, `session_adoption_notes`, and `slot_allocations`. The
mapping looks complete, but Phase 0's feature-to-owner inventory should
name it explicitly so no row kind falls between the four successors.

### 1.6 Checks that came back COVERED (verified, for calibration)

- **Description hand-off queue** (`core/state.py:890`,
  `subagent_fmt.py:203/249` — PreToolUse(Task) pushes the description,
  SubagentStart pops it): covered as the scoped FIFO launch-correlation row,
  §38.1 rule 4 + `ChildLaunchCorrelationStore` + fixture
  `claude_pretool_task_fifo_binds_subagent_start`.
- **Parity constants spot-check — 12/12 match the code**:
  `COMPACT_MAX_S=900` (`dashboard/config.py:233`), settle 20
  (`config.py:154`), escalation 300 (`config.py:177`), retractability 86400
  (`config.py:201`), `POLL_S=0.4` / `BACKSTOP_S=21600` (`core/tail.py:21,23`),
  `FG_BACKSTOP_S=7200` (`stream.py:255`), step 4 / bias 25 (`split.py:77–78`),
  scrollback 4800 (`bin/claude-mirror.py:111`), `CLEAR_GAP_S=0.15`
  (`tui.py:16`), `NS_DRAFT_MAX=24` (`prefs.py:230`). §38.10/§38.16/§42.2 are
  accurate.
- **Interrupt-marker-as-record + queued-follow check** → §38.6; **meta.json
  fields incl. `stoppedByUser` latch and torn-read retry** → §38.37.6;
  **adoption races incl. InstructionsLoaded negative-start** → §38.6/§38.27;
  **compact boundary as record + revert graph walk + fail-open** → §38.2/
  §38.13; **memory Bash plane, qmd searches, tree collapse** → §40.2;
  **tasks dismissal digest self-unhide** → §40.1; **relimit ladder + exact
  nudge text + manual-migration differences** → §38.18; **sampled mail
  census honesty** → §40.3; **Σ-row arithmetic (fresh input vs gross)** →
  §38.17/§40.3/§42.2 (mutually consistent); **retraction matrix incl.
  done-only SEEN reasons and device_active-never-retracts** → §38.16;
  **parked-DB + audit/prefs/counters/KV import** → §38.3/§41.5.

The constant-level accuracy across every spot-check is genuinely unusual for
a design document and raises confidence in the closure sections overall.

---

## Part 2 — Described features with gaps or internal contradictions

### 2.1 §40.6 vs §42.4: is a small-payload inline threshold allowed? (MEDIUM)

§40.6 forbids replacing the content-addressed blob store "with inline
payloads"; §42.4 permits the pre-Phase-1 storm profile to "tune batching,
checkpointing, indexes, and **payload thresholds**". An implementor cannot
tell whether storing a ≤4 KiB observation payload inline in SQLite (digest
still recorded) is a forbidden simplification or an allowed tuning. This
ambiguity sits precisely on the design's own top-listed risk: the
small-fact storm profile stipulates 95% of payloads are 0.5–8 KiB, and each
one currently costs exclusive-create + stream/hash + fsync(temp) + rename +
fsync(dir) + `blob_objects` + `blob_references` trigger before the
observation row is even written (§38.34). One sentence deciding this —
ideally in favor of an inline threshold, see Part 3 — removes the
contradiction.

### 2.2 The performance-gate no-exit loop (MEDIUM)

If the §38.30 gates fail: §16.2 says a release that cannot pass on one
machine-wide SQLite "must not ship"; §27.4 forbids PostgreSQL as a
corrective; §16.2 forbids per-Conversation SQLite; §38.28/§40.6 forbid
removing the retained mechanisms or weakening durability; §42.4 restricts
the storm profile to tuning. The only stated escape is "returns to the
product owner for an explicit design revision" (§40.6). The design should
name, now, which decision is reopened first if tuning is insufficient
(candidates in order of least semantic loss: inline payload threshold →
synchronous local-effect path → memory-resident structural feed → trigger
thinning). Otherwise the program has a designed deadlock where every exit
door is individually bolted.

### 2.3 Embedded schema-digest literals (LOW)

§38.35 prints an "intermediate" SHA-256
(`7c0b…50001` — note the suspicious trailing pattern) as the computed hash
of the document's own normalized SQL, then §38.39/§40.7 "replace" it. A
document cannot truthfully embed a hash over text that includes editing the
hash; the normalize-to-zeroes rule handles self-reference but not ordinary
doc edits, so the literal is stale the moment any DDL line changes. §0.3
already treats the OpenAPI artifact as generator output — do the same for
every digest: state the algorithm, mark the value `<generated>`, and let CI
own the literal. Same treatment for the thrice-repeated "113 endpoints" and
"36 events" counts (§38.24/§38.36/§38.22): the counts are enforced by a CI
set-equality test anyway, and a prose literal will drift on the first added
endpoint.

### 2.4 §10.3 environment allowlist is a category list, not a contract (LOW)

Everything around the edge contract is exact (manifest rows, the six
verified telemetry values, deadline splits), but the environment-snapshot
allowlist itself is "includes provider-specific mappings for:
account/profile identity; provider config directory; …" — categories, not
keys. The snapshot is load-bearing: continuation attempts *inherit* it
(§38.6), `attempt_environment_values` stores per-key presence states, and
§38.2 hangs context-window resolution on one specific key
(`CLAUDE_CODE_DISABLE_1M_CONTEXT`). The exact per-provider key table should
be normative (or explicitly delegated to the provider manifest with a
fixture requirement), matching the precision of everything around it.

### 2.5 Deliberate divergences are well-marked (positive note)

Where the design departs from legacy it says so and why: `sql-write`
replaced by `repair scaffold` (§38.19), extension HTTP routes narrowed to
`routes=[]` (§42.1), PostToolBatch inference retired (§38.6), sticky tab
retitle stays dead (§38.2). No silent regressions of this class were found.

---

## Part 3 — Overall review

### 3.1 What is genuinely excellent

- **The domain model is right.** Conversation / Node / AgentSession /
  Operation / Stream, plus the four-relationship distinction (dialogue
  ancestry ≠ work containment ≠ causal contribution ≠ runtime lineage),
  names precisely the confusions the legacy system spent years discovering
  empirically — sid forks, child results landing after the parent's answer,
  codex's three disjoint id spaces, teammates re-tasked by mail. Actor
  tracks (§38.1) are the standout addition: they solve the "child prose
  competes with the lead head" problem that earlier drafts genuinely had.
- **The epistemology survived the rewrite.** Laws 11–12, 22–24, 38–47 are
  the legacy repo's hard-won invariants correctly generalized: silence
  never proves success; acknowledgement ≠ completion; desired state is
  never persisted as observed; markers match parsed records, never bytes;
  an effect's own byproducts can't reconcile it; typing is destructive.
  These are the crown jewels of the legacy system, and the design treats
  them as such.
- **Not event-sourced, for the right reasons** (§2): provider artifacts are
  already the native truth; observations are evidence, not domain events;
  repairs are named and provenance-tracked instead of global replay.
- **Prepare-then-answer** (§11) is the correct formalization of the tee
  rewrite: "never rewrite a command unless certain you can observe the
  rewritten execution," with declared rollback per preparation step.
- **The review discipline is rare.** Five adversarial passes with a
  contradiction-resolution table (§38.32), measured-counterexample law
  (law 53), and fixture-first drift handling (§38.29). Part 1.6's 12/12
  constants match is the visible result.
- **§40.6's revised migration staging** (Phase 2a: read-only replacement of
  list/session/activity/memory/tasks/scoreboard/insights while all controls
  stay legacy, seven-day parity, then remove legacy read paths) is the most
  realistic thing in the migration chapter — it produces a genuinely
  deleted subsystem early.

### 3.2 Architecture critique

**(a) The daemon inverts the legacy availability model, and the design
underweights it.** Legacy is ~20 short-lived hook processes plus detached
tailers coordinating through SQLite: no single component whose death takes
tab colors, mirror, audit, and alerts down *together*; every feature
degrades independently, and the audit spool means even the degradation is
recorded. v4 concentrates everything in one supervised process and accepts
total feature loss plus permanent evidence loss during outage (§26.2). The
decision is recorded honestly, but its interaction with 1.1 above is the
worst-case story: the component whose failure you most need to debug is the
only component that can record debugging evidence. At minimum, the
supervisor's crash-loop state (§26.3) should specify the user-visible
surface ("visible crash-loop state" — where? the daemon serves the UI) and
the offline diagnostic path (supervisor logs + the CLI against the
database) as a first-class flow, not a footnote.

**(b) Fail-closed provider-version support fights the product.** §0.3/§36.2/
§38.37.9: "an unregistered build is unsupported until its exact measured
build and byte fixtures are checked in; the implementor may not guess or
silently broaden support." Claude Code ships new builds roughly weekly, and
this tool's entire purpose is to observe it. As written, every provider
update turns the cockpit off (or into an "unsupported" refusal) until the
maintainer re-captures fixtures — for a single-user tool this converts a
version bump into a maintenance chore *before the tool works again*.
Legacy fails open per-feature and degrades. The design already contains the
right instrument for safe fail-open: law 15's default mapping (unknown
activity → generic Operation, never silence) and provenance marking.
Recommendation: fail-closed only for **answerable/delegating** families
(where a wrong guess rewrites user commands or takes actions), fail-open
with `unverified_build` provenance for **observational** mapping. The
current rule contradicts the spirit of law 15 for the sake of a purity the
observational path doesn't need.

**(c) Spec-maximalism is a method choice with compounding costs.** §0.2's
goal — zero implementor decisions — produced 152 `CREATE TABLE`s, 140
triggers, 96 indexes, 113 endpoints, 36 events, per-worker backoff
schedules, byte-level frame formats, and **five overlay layers each of which
"wins over earlier wording"** (§38, §40, §41, §42, plus the superseded §28
inventory that must not be executed). Costs: (i) internal contradictions
become statistically inevitable and are now spec bugs (2.1, 2.3 above);
(ii) an implementor must topologically sort five authority layers to answer
any question — the assembly of the *schema itself* takes five ordered units
with a cross-unit trigger replacement (§38.35/§38.39/§40.7), which is an
artifact of the document's accretion history, not of the domain; (iii) every
implementation discovery "reopens the design" (§0.2), a heavy loop for a
single-maintainer project. **Concrete recommendation, cheap and high-value:
before Phase 1, mechanically flatten the document** — apply the closure
sections onto the body, delete §28, produce one authority layer and one DDL
unit — and demote all generated literals per 2.3. This is editorial, not
architectural, and it removes an entire error class.

**(d) Speculative generality at v1 scale.** The clean install creates
handover checkpoints, invitation credentials, certificate revocations,
collaboration roles, remote-backend and untrusted-plugin tables; the API
fixes SPIFFE mTLS roles, credential-pepper rotation, and invitation flows —
for a tool with exactly one user on localhost. §0.1 makes this a deliberate
decision ("full-scope") and phasing defers the *code* — but not the schema
(installed in v1), not the OpenAPI surface, and not the contract-test
matrix, all of which must be carried through every refactor before a single
user of those features exists. The §34 tradeoff ledger does not price this
honestly. The design's own §35 (deferred promotions with named triggers)
shows the authors know the alternative pattern; it simply wasn't applied to
auth/remote/handover/collaboration. I would cut the v1 install to the
tables Phases 1–5 write and keep the future contracts as ADR-grade
appendices with their trigger conditions — the multi-principal *port
shapes* can stay (they're cheap); the 40-odd tables and 25-odd endpoints
should not be schema-final years before first use.

**(e) Double-enforced integrity on hot paths.** ~140 triggers re-check
scope invariants the application layer (and its tests, and the negative
fixtures) also enforce. Defense-in-depth is fine for low-rate semantic
invariants (head committedness, purge authorization, lead-track identity).
But `blob_state_requires_zero_references` runs a 17-table UNION scan on
every blob state flip, and `materialized_activity`/`notification_intents`
inserts each run multiple correlated EXISTS probes inside the single
writer's transaction — §40.6 itself names "trigger VM time" a top risk.
Choose one enforcement layer per invariant *class*: keep triggers where a
violation is catastrophic and rare, drop them where the storage-port
methods are the only writers anyway and the benchmark says they cost.

### 3.3 Performance critique

- **The write path is the risk, and the design knows it but bolts the
  exits.** Legacy: one hook event ≈ 1–3 row writes into a per-session /tmp
  SQLite. v4: the same event ≈ blob write (up to 4 fsyncs) + observation
  row + N consumer transactions × (canonical + provenance + decision +
  projection + feed + outbox rows) through **one** FIFO-serialized writer on
  one machine-wide database — a 5–15× amplification before triggers. The
  §38.30/§40.6 gates are well-constructed (compound load, storm profile,
  measured boundaries), but per 2.2 every structural mitigation is
  forbidden. The single highest-leverage change available: **an inline
  small-payload threshold** (resolve 2.1 in its favor) — it collapses the
  storm profile's dominant cost with zero semantic loss (digest and
  retention classes unchanged).
- **Answerable-hook budget needs the measured deadline before Phase 1, not
  after.** The edge spends 80% of the native hook deadline on the daemon
  round-trip (§38.4), which must cover socket RT + eligibility + tee-file
  preparation + a `BEGIN IMMEDIATE` on the contended writer with
  `busy_timeout=0` and a 50 ms retry cap (§38.34). The <0.1% pass-through
  gate and the 100 ms p99 writer-admission gate are jointly satisfiable
  only if the native deadline is comfortably above ~500 ms. The manifests
  defer the deadline as a "measured implementation input" — measure it
  *first*; if Claude's PreToolUse deadline is 100 ms-class, the whole
  synchronous lane needs a different shape (e.g., pre-claimed tee slots).
- **The durable structural feed is the one retained mechanism with no
  user-visible behavior behind it.** Every mutation writes
  `structural_changes` rows per scope through the outbox, retained
  24 h/100 k per scope (§38.22) — a second durable write stream shadowing
  the first. Clients must implement snapshot + resnapshot regardless (the
  protocol demands it on overflow/expiry), so an in-memory ring with
  snapshot fallback delivers the same client contract minus the write
  amplification. §38.28 calls the cost "an accepted reliability choice";
  it's the acceptance I'd revisit first when the benchmark complains.
- **The read/delivery side is well designed**: materialized activity with
  whole-block pagination and bounded invalidation (§38.21), per-stream
  range fetches, the live-facet plane (a genuine simplification — ghost
  text and elapsed chips stay out of SQLite entirely), coalesced overview
  revisions with explicit tunnel size budgets (§42.1). The pane reflow
  gates (100 k items, 500 ms p95 first paint, in Python) are credible only
  because the render-cache identity rules are specified — they are.
- **Local idempotent effects through the full outbox pipeline** threaten
  their own gates: a tab paint is canonical tx → outbox row → dispatcher
  claim (250 ms wake) → attempt → kitten call → receipt observation →
  consumer tx, against a 200 ms p95 *verified* gate. §38.28 already permits
  optimizing idempotent actions inside workers while retaining rows — make
  that explicit for tab/pane effects (synchronous attempt within the
  requesting flow, same durable rows) or the gate fails on wakeup latency
  alone.

### 3.4 Overcomplicated — ranked, with what's justified

Overcomplicated for insufficient reason, in descending order of carrying
cost:

1. **Full multi-principal auth + collaboration + mTLS surface in the v1
   schema and API** (3.2d) — defer the schema, keep the port shapes.
2. **Durable structural feed** vs. memory ring + snapshot (3.3).
3. **Content-addressed blob ceremony for ≤8 KiB payloads** (2.1, 3.3).
4. **Trigger duplication of app-enforced scope checks on hot tables**
   (3.2e).
5. **Outbox/lease/worker pipeline for idempotent loopback terminal
   effects** (3.3, last bullet).
6. **Schema-digest and count literals embedded in prose** (2.3).
7. **Five-unit DDL assembly with cross-unit trigger replacement** — an
   accretion artifact; flattening (3.2c) dissolves it.

Equally important — mechanisms that *look* baroque but are justified, each
traceable to a measured legacy bug, and should be kept exactly as
specified: the three-axis notification model with the arm table
(holding-vs-suppression bugs, 20/99 lost alerts); adoption notes with
negative-start evidence (the sid-fork regression class); per-field
positive-delta usage credit (the message.id double-count bug); framed
staging files with torn-tail recovery; invertible command transforms
(wrapped commands leaking into classification); the interaction drivers'
forward-only reread-before-keystroke rule; source epochs/ordinals over
wall-clock. Simplifying any of these would re-introduce a specific,
documented production bug.

### 3.5 Bottom line

The domain model, the uncertainty laws, and the provider-mapping closure are
excellent — better than the legacy architecture deserves to be replaced by,
and the constant-level fidelity to legacy behavior (1.6) is exceptional.
The risks are not in the model; they are program-level:

1. a single maintainer carrying a zero-decision 14 k-line spec through a
   migration in which the legacy system keeps changing (§38.29 requires
   triple-maintenance: legacy + fixtures + v4) — the realistic failure mode
   is not a wrong design but an unshipped one;
2. the write-amplification-vs-forbidden-mitigations deadlock (2.2);
3. two decided regressions worth re-deciding: audit-outage evidence loss
   (1.1) and fail-closed observational provider support (3.2b).

Recommended pre-Phase-1 actions, in order: flatten the document into one
authority layer (3.2c); decide the inline-payload question in favor of an
inline threshold (2.1); trim the v1 clean install to the tables Phases 1–5
write (3.2d); soften fail-closed to observational-fail-open with provenance
(3.2b); specify terminal-socket discovery (1.2); and re-confirm or reverse
the no-spool audit decision (1.1). None of these touches the core model;
all of them shorten the distance to Phase 2a — the first moment the rewrite
deletes something real.
