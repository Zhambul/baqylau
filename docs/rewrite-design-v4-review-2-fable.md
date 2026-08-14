# baqylau v4 design — second review (post-§38 closure)

Reviewer: Claude (Fable 5) · Date: 2026-08-05
Subject: `docs/rewrite-design-v4-codex.md` (11,567 lines, "IMPLEMENTATION-READY DESIGN — VALIDATED SPECIFICATION")

This is a fresh review of the v4 document *after* it absorbed and closed the
first review (`rewrite-design-v4-review-claude.md`, closed by §38 findings
1.1–2.33 and §§38.35–38.39). Nothing already closed by §38 is re-reported here.
Three deliverables, per request:

1. **Legacy features the design does not cover** (the design must be a superset
   of the current system);
2. **Places where the design describes an existing feature with a gap** —
   internally inconsistent, incomplete, or contradicting the measured legacy
   behavior it claims to preserve;
3. **A general review**: architecture critique, performance critique, and
   where the design is overcomplicated for no reason.

Methodology: full read of the v4 document; comparison against CLAUDE.md and the
`docs/` corpus; two parallel sweeps of (a) the docs feature inventory and (b)
the code's user-facing surface (env knobs, HTTP endpoints, SSE events, prefs
keys, state-DB kv keys) against the v4 §38.24/38.22/38.27/38.35 manifests; and
targeted grep verification of every schema-level claim below.

---

## 1. Legacy features not covered by the design

Severity legend: **MISSING** = no home in v4 at all; **UNDERSPECIFIED** = a seam
exists but the feature's own contract does not (which, by v4's own §0.1/§0.2
gates, is a spec failure, not an implementation choice); **MINOR** = small or
arguably presentation-only.

### 1.1 Ghost-suggestion *transport* — MISSING (architectural, not just an omission)

v4 §12.1/§13.3 correctly classifies the TUI ghost suggestion as a live-only
ephemeral facet, and §38.10 specifies the client-side *acceptance* rules. But
nothing carries the suggestion **text** to the browser. Every v4 SSE event is a
durable `structural_changes` row published through the transactional outbox
with a `payload_ref` blob (§38.22, §38.27) — there is **no ephemeral channel in
the architecture**. The 35-event manifest has no `suggestion` event; the 106
endpoints have no suggestion read; `ConversationViewDTO.facets` is fed from
durable projections. A "live-only, no kv" fact that must reach a remote browser
within a second contradicts the delivery design as written. Either (a) an
ephemeral SSE side-channel must be admitted (breaking "all events are durable
feed rows"), or (b) the suggestion becomes a short-TTL durable row (breaking
"live-only, disappears on restart" — and paying a blob+outbox write per screen
poll). The design must pick one; today the legacy `suggestion` SSE event has no
successor. Same tension, milder, for the legacy fast-cadence `fgrun`/
`compacting` channels — those at least have durable owners (`fg-live` →
Operation `started_at`; compaction Operation), but the *fast* cadence rides the
same durable feed and every publish now costs an outbox row + blob.

### 1.2 "Resume & send" on a parked session — MISSING workflow

Legacy: typing into the composer of a PARKED session relaunches
`claude --resume <sid>` with the message riding the launch argv
(docs/dashboard.md *Resume & send*). v4 has `:resume` (no message field in
`ResumeRequest` beyond `continue_interrupted_turn`) and
`POST …/messages` (whose error table — `interaction_owns_input`,
`draft_conflict`, `terminal_unavailable` — never says what happens when the
target AgentSession is parked/ended). The combined gesture — one user action
that resumes *and* delivers — has no endpoint, no saga, no
`message_delivery` state for "waiting on relaunch". Either `messages` must
accept a parked session and internally chain a resume (then its lifecycle needs
a `relaunching` leg), or `ResumeRequest` needs an optional initial message.
Unspecified either way.

### 1.3 ☀ Keep-awake toggle — MISSING

The list-page header's keep-awake control (screen wake lock so the machine
doesn't sleep while sessions run) appears nowhere in v4 — no endpoint, no
preference, no mention (`grep keep-awake/wakeLock` over the design: zero hits).
Small feature, but §0.1 says the *complete current product* is in scope. If it
is intentionally client-only (browser `navigator.wakeLock`, no server state),
the design should say so; note the legacy toggle's state survives reloads.

### 1.4 Frontend telemetry beyond control gestures — UNDERSPECIFIED

Legacy `clog` (docs/dashboard.md *Frontend audit*) records four families:
gesture transport (`<gesture>.begin/.ok/.fail`), **SSE lifecycle**
(`sse.open`/`sse.drop`), **uncaught JS errors** (`js.error`/`js.reject`), and a
per-load **`boot` record** (origin: loopback vs tunnel), each batch stamped
with a `connInfo()` snapshot — plus ad-hoc beacons like `notify.recv
shown:false` (which was the *load-bearing evidence* for the presence
mark-away bug) and `attach.paste`. v4 §38.14 `surface_control_attempts` models
only the gesture family: `phase ∈ begin|ok|fail`, one row per gesture attempt.
SSE drops, JS errors, boots, and notification-receipt beacons do not fit that
shape (they have no gesture, no ok/fail pair), and no other telemetry intake
exists in the 106 endpoints. The "whole bug class invisible in the server-side
audit" argument that motivated clientlog applies equally to a dropped SSE
connection or a swallowed reducer exception in v4's much more complex client.

### 1.5 Tasks-card dismissal semantics — UNDERSPECIFIED

Legacy: the ✕ on a fully-completed provider task list stores a `tasks-hidden`
preference **keyed by the task-ID set**, so it self-expires the moment the list
changes, works cross-device, and needs no un-hide gesture (docs/dashboard.md
*Web tasks*), with the offer gated by the one `tasks_done` predicate (409
otherwise). v4 has `provider_task_snapshots` (§38.2) and a generic preferences
store, but the dismissal contract — the ID-set key, the self-expiry rule, the
all-completed gate and its 409 — is nowhere. This is exactly the kind of
"clock vs identity" rule v4 elsewhere insists on writing down.

### 1.6 The memory web-extension's own contracts — UNDERSPECIFIED (by v4's own gate)

v4 gives the extension *seam* (SurfaceContribution, ExtensionFactSink,
`extension_facts`, `memory_note`/`search_result` Resource kinds) and that is
architecturally sufficient. But §0.1 explicitly says phasing "does not permit
that feature's data, API, event, storage … contract to remain unspecified."
The memory tab's actual contracts are absent: the Bash-plane read detection
(per-statement cwd tracking, `tilde=True` divergence from `parse_redirect`,
the vault name-index fallback, reads-only scope), qmd search-card parsing
(hits keyed `(kind, sub, query)`, rerun-replaces-hits, hits not filed as
reads, single-search-command attachment rule), scope gating
(`memory.in_scope`, hidden tab off-scope), and wikilink/backlink rendering.
These encode a year of measured behavior ("one in-scope session read ten notes
… with an empty tab") and would be reinvented from memory during migration —
the exact failure mode Law 37 forbids.

### 1.7 Scoreboard counters — UNDERSPECIFIED

The 5-row scorebar shows: session id · ✉ message census (delivered/read
lifecycle from inbox polling) · ▪ activity (**command count** + active time) ·
Σ token breakdown + cost · **files touched + aggregate ±line-diff + tools
count**. v4 specifies owners for active time (§38.10) and usage (§38.17) only.
The cumulative command/files/±lines/tools counters have no named projection;
they are derivable from Operations, but "derivable" is what v4 elsewhere
refuses to accept (cf. mandatory `usage_source_rollups` because evidence
prunes). Operation payload evidence expires after 30 days (§38.34) — after
which a parked session's ±line-diff aggregate is *not* reconstructible unless
`FileChangeBlock` extents live in the (canonical) Operation `data`. The design
should say which columns are canonical for these counters. The ✉ census also
needs its lossy-sampler branch class (§13.3) explicitly attached — legacy
documents that the poller sees only 2-of-33 messages' lifecycle.

### 1.8 Alert-retraction kind tables — UNDERSPECIFIED (regression risk)

Legacy makes the reacted≠resolved distinction **declarative**:
`RETRACT_REASONS` = tab-moved/session-ended/composing (either kind);
`SEEN_REASONS` = tab-focused/web-viewing (`done` only); and machine-wide
`device-active` is **in neither** — "or one awake iPad deletes the alerts of
every session you never looked at" (docs/dashboard.md *Alert retraction*).
v4 §38.16 keeps the prose asymmetries and a cancel-reason *precedence* enum
that **includes `device_active`**, and says the winning reason "determines
whether delivered messages retract" — but never tabulates which reasons may
retract a *delivered* alert per kind. The measured invariant that
`device_active` must never retract anything is representable but unstated;
as written, a compliant implementation can re-introduce the iPad bug.

### 1.9 Legacy-transform inversion during migration — MISSING rule

Law 39 / §38.5: every synchronous transform is invertible and inverted at
ingestion before any consumer sees command text. During Phases 1–4 the
**legacy** `claude-cmd-pre.py` still owns the answerable plane and still tees
commands via `updatedInput`; v4's read-only ingestion (deployed against live
legacy traffic per §38.29) will therefore observe *legacy*-rewritten command
bytes. The v4 transform registry covers only v4's own `PreparedTransform`s.
Without registering the legacy tee wrapper as an invertible (or at least
recognized → `unknown_transform`) shape, every foreground command during the
longest migration phases is misclassified, its render-kind detection runs on
wrapper bytes, and Phase 2's parity gate compares poisoned data. One sentence
fixes this; today it is absent.

### 1.10 Dictation and clipboard-file resolution have no endpoints in the closed manifest — MISSING (contradiction)

§20.6 names both features normatively: dictation "can mint short-lived
restricted grants… key terms follow declared configuration layering"
(legacy `POST /api/dictate/token`, key at `~/.config/deepgram/api-key`), and
"clipboard path discovery is a privileged local capability with exact basename
agreement and audit" (legacy `POST /api/clipboard/files` — the server reads
the macOS pasteboard so a pasted *file* becomes its host path instead of an
upload). But the 106-endpoint manifest (§38.24/§38.38) contains **no dictation
and no clipboard endpoint**, and §38.36 enforces exact set-equality and
forbids adding endpoints as an implementation choice. As written, two features
the design *requires* cannot be reached over its API.

### 1.11 Web Push cannot be bootstrapped — MISSING (one read endpoint)

`PushSubscriptionCreateRequest` requires `key_id`, and the browser needs the
active VAPID **public key** to call `pushManager.subscribe()` at all
(legacy `GET /api/push/config`). `push_key_material.public_key` exists in the
DDL, but no manifest endpoint returns the active key/key_id. A compliant
client cannot create the subscription the API demands.

### 1.12 Per-actor context/model facets have no durable home — UNDERSPECIFIED (schema gap)

Legacy shows a ctx-fill tag and model·effort tags on every subagent/teammate
stream and agent card. §38.2's projection table says context is
"AgentSession/actor scoped", but `agent_session_context_state` (and
`agent_session_runtime_revisions`) key on `agent_session_id` alone — and a
Claude subagent is an actor *track* whose `agent_session_id` is nullable
(§38.1). A child with no AgentSession has nowhere to put context
window/used or effective model. Goals got per-track keying
(`conversation_goals`); context and runtime facets did not.

### 1.13 `StatsDTO` cannot rebuild the stats page — UNDERSPECIFIED (closed DTO, real loss)

The legacy Insights page shows a contribution heatmap, a day×hour punch card,
error counts, and per-project 90-day sparklines. `StatsDTO` (§38.38) carries
only conversation/operation counts, `active_ms`, and a `UsageDTO`; `group_by`
has no hour dimension, no error series, no per-project card series. The DTO is
closed (`additionalProperties=false`, generator-enforced), so this is a
feature loss by specification, not an implementation choice.

### 1.14 Silently dropped notification knobs — MISSING (needs a drop decision)

`CLAUDE_DASH_NOTIFY_DELAY_S` (a user-settable global pre-alert debounce,
default 0 but present) has no `notification_settings` field — only
`done_settle_seconds` and `escalation_seconds` survive. And
`CLAUDE_DASH_RETRACT_S` (the 24 h retractability ceiling, deliberately under
Telegram's 48 h Bot-API delete limit — a *measured external constraint*) has
no owner: `notification_deliveries.expires_at` exists but no setting or
channel-adapter contract records the 48 h ceiling. Per §31 Phase 0, dropped
features need an explicit drop entry; neither has one.

### 1.15 Account-registry migration mapping — UNDERSPECIFIED

The live relimit machinery runs on `plugins/claude_code/account.py`'s
`claude-subscription` contract: `accounts.tsv`, `CLAUDE_SUBSCRIPTION_SLUG/
LABEL`, symlink-farm configs, the `c1`/`c2` aliases. v4 models accounts
cleanly (§21, §38.18) and imports parked *mirrors* (§38.3), but nothing maps
the existing registry/alias-launch mechanism into
`account_profiles`/`CredentialImport` at cutover — and relimit continuity
across the migration depends on exactly that mapping.

### 1.16 Stale-frontend signal (BOOT_ID) — MINOR

Legacy: the SSE `hello` carries `BOOT_ID`; a reconnect after a server restart
toasts "dashboard updated — refresh" — the only defense against cached-JS /
new-API skew (the dashboard's documented no-hot-reload contract). v4 has
per-event `schema_version` and `resnapshot_required`, but a resnapshot re-runs
the *same stale reducers*; nothing tells a client its **code** (not its
cursor) is old after an upgrade.

### 1.17 Presentation-adjacent items with no stated home — MINOR (batch)

Each is small; listed because §0.1 claims complete scope and none has either a
contract or an explicit "client-only, no daemon contract" note:
the composer's pinned queue display including messages queued *from the
terminal* (typed into the TUI mid-turn — no v4 `message_delivery` Operation
exists for those, so the queue card loses them); composer ↑/↓ history recall;
the PWA surface (manifest, home-screen shortcuts with `?new=1`/`?attn=1`
deep-link params, push-driven `setAppBadge`, tab-title `(N)` + favicon dot —
§19.3 fixes only "badge counts Conversations"; §38.16's push payload schemas
carry no badge); the list page's live-session strip and the husk-row policy
(legacy `visible_agents()` hides all-empty auxiliary agent rows — a
classification rule with no owner; `audience:hidden` is never stated for
synthesized tracks); and the ☀ keep-awake toggle (§1.3).

---

## 2. Existing features the design describes with internal gaps

These are places where v4 *does* cover the feature but the specification
contradicts itself — precisely the class of defect its own §0.2 gates claim to
have eliminated ("an implementor can write the code without making a …
decision"). Each was grep-verified against the document.

### 2.1 `operations` loses `opener_state` and `abandoned` in the authoritative schema

§7.4 defines the common Operation record with `opener_state present|missing|
unknown` and the common state set including `abandoned`; §38.6 requires a
completion-with-no-opener to be materialized with `opener_state=missing`;
§38.13 requires compaction's `abandoned` terminal state ("maps to common
`abandoned`", §7.4). The **superseded** §28 fragment has both. The
**authoritative** §38.35 Foundation `operations` table has *neither*: its
`state` CHECK is `('pending','running','succeeded','failed','cancelled',
'denied','lost','unknown')` and there is no `opener_state` column. The 38.38
`OperationDTO.state` enum matches the Foundation (also missing `abandoned`).
So the DDL/DTO that CI enforces makes §38.6/§38.13 behavior *unwritable*.
Required tests like `unmatched_hidden_agent_closer_materializes` (2.1) and
`missing_postcompact_expires_latch_not_operation` (2.17) cannot pass against
this schema.

### 2.2 `AgentSessionDTO` lifecycle enums disagree with the lifecycle DDL

`agent_session_lifecycle` (§38.27): `host_state ∈ starting|live|parked|ended|
lost`, `work_state ∈ active|drained|unknown|lost`. `AgentSessionDTO` (§38.38):
`host_state:enum(live,parked,ended,lost)` — **missing `starting`** — and
`work_state:enum(active,drained,unknown)` — **missing `lost`**. A session in
`starting` (every launch, §38.37.3 "a lost receipt leaves the attempt
`starting`") or with lost work (§38.20 archive gate) cannot be serialized.
The OpenAPI generator's set-equality checks will happily enforce the wrong
enum.

### 2.3 Observation dedup identity: two incompatible definitions

§38.27 DDL: `observations.dedup_key TEXT NOT NULL UNIQUE` — one **global**
uniqueness. §38.37.2 access-pattern table: "Observation dedup | exact boundary
identity | unique `(source_kind,source_identity,dedup_key)`" — **composite**
uniqueness. These are different constraints with different collision behavior
(a global key must embed source identity by convention — the kind of unwritten
application convention §0.2 bans from relationships). One must win.

### 2.4 §38.27 is anchored to a section that §38.35 forbids executing

§38.27 opens: "The clean-install schema **in Section 28** must include the
following exact DDL." §38.35 then declares §28 "retained only as review
history … must not be executed or copied into the implementation." The
normative anchor of the second DDL unit dangles. Editorial, but this is a
document whose whole §0.2 premise is that cross-references are load-bearing.

### 2.5 The lead-head invariant lost its enforcement in the authoritative schema

§38.1 rule 1: `conversations.head_node_id` **is exactly** the lead track's
`head_node_id`. The superseded §28 enforced and projected this
(`lead_track_head_projection`, `conversation_head_must_equal_lead_track`).
The §38.35 Foundation kept the committed-membership triggers but **dropped
both** the equality guard and the projection — nothing in the four executed
units maintains or checks head-equality, and no storage-port contract is
assigned the duty (ActorTrackStore.set_head_tx says "lead Conversation head
when applicable" without the invariant). Either restore the triggers or name
the single writer; as shipped, the two heads can silently diverge.

### 2.6 Notification arm/intent creation order is circular as constrained

`notification_intents` insert-trigger requires an existing `arms` row with
`owner_id = NEW.id` (the intent's own id) and `revision = NEW.arm_revision`;
`arms.owner_id` is plain text so the arm *can* be written first inside the
same transaction with a pre-allocated intent UUID — but no section states this
required write order, and `AlertStore.arm_tx` returns a `NotificationIntent`
without mentioning the arm-first choreography. An implementor following
"intent then arm" hits `notification_arm_copy_mismatch` on every arm.

### 2.7 Two owners for the public notification origin

§38.15: "The required notification origin is `notifications.public_base_url`"
(machine configuration). §38.27/38.38: `notification_settings.public_base_url`
is a **per-principal** column with per-principal revisioning. For the
single-owner rule this design otherwise enforces obsessively, the deep-link
origin now has two plausible owners and no precedence rule.

### 2.8 Naming drift: `vendor_cost_minor_by_currency` vs `vendor_cost_json`

§38.17's rollup sketch names the column `vendor_cost_minor_by_currency`; the
executable §38.27 DDL names it `vendor_cost_json`. The generator "fails if a
table … appears in the document but not the generated schema catalog" — prose
and DDL must agree for that check to mean anything. MINOR but easy.

### 2.9 Multiselect drive rule likely misdescribes the TUI

§38.12: "For multiselect dialogs, **Enter** toggles only an option whose
current selected state was positively observed." In Claude Code's dialog,
Space toggles and Enter submits; legacy `askdialog.py` drives the real thing.
The forward-only/observe-before-keystroke *principle* is right; the named key
is wrong, and this document's stated bar is byte-measured fixtures, not
recalled keys. (Law 37, applied to itself.)

### 2.10 Fast-cadence facets vs the durable-only feed

Legacy deliberately splits fast SSE channels (`fgrun`, `compacting`,
`suggestion`) from the slow tick because "the slow tick would keep counting
past the finish chip." v4 funnels *every* event through outbox →
`structural_changes` (+ payload blob) with FIFO publication. The design never
states the added latency/cost budget for high-frequency, low-value events
(a compaction latch toggling, per-revision `stream.changed` metadata) on that
durable path, yet §38.30 demands p95 ≤ 250 ms end-to-end. See also §1.1 and
the performance critique below.

---

## 3. General review

### 3.1 What is genuinely excellent

Credit where due, because the critique below is severe and the foundation does
not deserve to be thrown out with it:

- **The domain split is right.** Conversation/Node/AgentSession/Operation/
  Stream, with actor tracks for child dialogue, is the correct factoring of
  what the legacy system learned the hard way. "Nodes describe content,
  Operations describe work" and the four-relationship distinction (§0) are
  real insights, not architecture-astronaut slogans.
- **The uncertainty discipline is the best part of the document.** Requested/
  effective/observed/unknown/lost as first-class values, closers matching
  correlation identity, "silence never proves success," parsed-record markers,
  the self-caused-effect exception, acceptance ≠ completion — this encodes the
  no-hook-on-cancel bug class, the interrupt-marker false positives, the
  PostToolBatch lesson, and a dozen other scars *as laws*. Law 53 (a rule
  carries its measured counterexample) should be stolen by every design doc.
- **The daemon consolidation is justified.** ~20 short-lived hook processes
  coordinating through SQLite files in /tmp is the legacy's real structural
  debt; one supervised process with durable open facts, a real inbox, and an
  outbox for effects is the right replacement shape.
- **Migration realism.** §38.29 (legacy keeps moving; fixtures pin commits) and
  the read-only shadow phases are more honest than most strangler plans.

### 3.2 Architecture critique

**The core defect is scale mismatch, made irreversible by §38.28.** This is a
single-user, single-machine terminal cockpit. The design specifies a
multi-principal control plane: principals, scopes, role bindings, browser
sessions with CSRF + pepper-rotated HMAC secrets, bearer audiences, mTLS with
SPIFFE SANs and revocation-checked long-lived connections, invitations,
conversation memberships, public links, a remote-backend mutual-TLS protocol,
and a subprocess plugin sandbox — all with executable DDL in the version-1
schema, digest-locked. §35 preaches "new core entities must earn independent
identity," yet ~20 tables for unbuilt Phase-7+ features ship in the clean
install where every later correction is a `restore_required` migration.
Deferring *implementation* while freezing *schema* is the worst of both:
you pay the consistency-maintenance cost now (see §2 — four of the ten defects
above live in exactly this frozen surface) and still get the schema wrong,
because unbuilt features are where guesses live.

**Spec rigidity is the second defect — the overcorrection from v1–v3's
vagueness.** Worker cadences (200 ms poll, 30 s leases, 1/2/5/15/60 backoffs,
batch sizes 20/50/100/500/1,000), the byte-level frame format, and literal
SHA-256 digests of the document's own SQL are *normative*. These are tuning
parameters and build artifacts, not architecture. Every one that turns out
wrong under profiling now requires a design change by the document's own rules
("a change that makes one of those checks fail reopens the design"). The
result is a spec that is simultaneously over-constrained where it should be
loose (constants, digests, worker topology) and — per §1/§2 above — still
under-constrained where it should be tight (suggestion transport, retraction
tables, lead-head ownership).

**Change amplification.** One new DTO field now touches: prose section, DDL
unit + digest, storage port, service method, endpoint manifest row, OpenAPI
generation, event manifest row + reducer, traceability matrix, and named
contract tests — 6–9 artifacts, several enforced by set-equality CI. That is
appropriate for a public API with external consumers; for a personal tool
whose only client ships in the same repo, it converts every small product
iteration (this repo's lifeblood — look at the CLAUDE.md changelog density)
into a process. The legacy system's actual superpower is its iteration speed;
v4 as written trades it away and should at least *acknowledge* the trade in
the ledger (§34 does not).

**Delivery risk.** Eight phases, each gated on sustained parity with a legacy
that keeps evolving (§38.29 rule 3 makes every legacy change a double
implementation), performance gates that ban the two most effective escape
hatches (engine change, partitioning), and a 106-endpoint client rewrite (web
+ pane host) at the end. The honest planning question the document never
answers: what is the smallest deployable unit that *replaces* anything? By its
own phases, user-visible value first arrives in Phase 3–4 after the entire
Phase 1–2 foundation — a very long time to be maintaining two systems plus a
parity harness for one person.

### 3.3 Performance critique

**Blob write amplification is the design's biggest unexamined risk.** Count
the durable writes for one ordinary observation (a PostToolUse for a command):

1. payload blob (temp create + hash + fsync file + rename + fsync dir) —
   §38.34 protocol, enforced by `observation_payload_exists_insert`;
2. `observations` row + trigger-written `blob_references` row;
3. up to 7 `observation_consumers` rows, then **one write transaction per
   consumer** (§38.5 — identity, canonical, attention, paths, tasks, evidence,
   extension each commit independently);
4. canonical rows + provenance + `materialized_activity` row whose
   `payload_ref` is **NOT NULL** → another content-addressed blob per activity
   item;
5. one `structural_changes` row **per feed scope touched**, each with a
   `payload_ref NOT NULL` → another blob per event;
6. outbox row(s) + effect-attempt rows for any effect.

The legacy path for the same event is roughly one INSERT into a per-session
/tmp SQLite and one audit row. v4's own benchmark demands 200 obs/s sustained
with 1,000/s bursts (§38.30): at even 3 blobs/observation that is thousands of
small-file fsync sequences per second on the same disk as the WAL — before the
FIFO single writer (with `busy_timeout=0` and a 50 ms retry cap) admits any of
the ~15 background workers. The gates (writer p99 < 50 ms, admission p95
< 25 ms, tab paint p95 < 200 ms) are plausibly unachievable under this write
schema, and §16.2/§38.30 explicitly forbid the classic remedies. Concretely
recommended: (a) inline small payloads (< ~8 KiB) as a column with the blob
store reserved for genuinely large content — this single change removes the
majority of fsyncs; (b) make `structural_changes.payload_ref` nullable with
payload-by-reference to the entity row (the feed already tells clients to
refetch DTOs); (c) drop the per-item blob in `materialized_activity` for
node/operation items (the payload is derivable from the item row it points
at). None of these change observable behavior; all are currently banned by
"payload_ref NOT NULL" being part of the digest-locked schema.

**The composer-draft path is the absurd case of the same problem.** Legacy: a
debounced kv UPDATE per edit. v4: every draft save mints a new content-
addressed blob (hash + 2 fsyncs + rename), inserts a CAS row, orphans the
previous blob for the GC/quarantine cycle, and emits a feed event with its own
payload blob. Typing a message becomes a small filesystem workload.

**Trigger tax on the single writer.** Hot-path tables (`nodes`, `operations`,
`peer_messages`, `notification_intents`, `materialized_activity`) each run
2–5 correlated EXISTS subselects per insert; `blob_state_requires_zero_
references` runs a 20-branch UNION ALL. All of it executes inside the one
FIFO writer whose p99 budget is 50 ms. Triggers as *backstop* for invariants
is defensible; triggers as the primary enforcement for scope rules the
application layer also validates (§38.27 passim) doubles the cost of every
write for the benefit of catching bugs the app-layer tests must catch anyway.

**Consumer fan-out multiplies writer contention.** Seven independent
transactions per observation is the price of "a task-mapper crash preserves
the attention transition" — a real property, but the common case (all
consumers succeed) pays 7× transaction overhead for the rare case's benefit.
A single transaction with per-consumer savepoints would buy the same isolation
at one commit; the design forbids exploring this (§38.28 "claiming, completion
and quarantine" retained as-is).

**What the performance design gets right:** bulk bytes off the metadata path
(framed staging + coalescing) is exactly correct and directly inherits the
legacy's hardest-won streaming lessons; slow-client resync-not-backpressure is
correct; read-time price arithmetic is correct; lazy coordinators are correct.

### 3.4 Overcomplicated for no reason

Ranked by (cost × how little the product needs it):

1. **The authentication universe** (§38.36): six credential kinds, SPIFFE
   certificate roles, OIDC exchange adapters, pepper rotation with 24 h
   overlap, per-request authorization-revision checks with 5 s caches,
   double authorization at HTTP and effect time. The legacy product: loopback
   bind + one authenticated tunnel. A Unix socket + one browser cookie +
   one static bearer for the tunnel covers every real deployment; the rest
   should be a §35 deferral, not v1 schema.
2. **Collaboration/invitations/memberships/public links in the v1 schema**
   (§38.39) for a Phase-7 feature explicitly "not on the critical path."
3. **Content-addressed blob store as the universal byte container** — SHA-256
   dedup, reachability triggers, GC leases, quarantine renames — applied to
   12-line drafts, 2 KiB observation payloads, and per-event feed payloads
   (see §3.3). Right for sealed streams and uploads; wrong as the only tool.
4. **Self-referential schema digests** — SQL whose own text embeds the SHA-256
   of itself, with a zero-sentinel normalization dance and an intermediate
   digest superseded 400 lines later (§38.35 vs §38.39). A generated schema
   artifact with an *external* checksum file achieves the identical guarantee
   with none of the ceremony, and the two literal digests printed in the
   document cannot be verified by any reader anyway.
5. **The dual timer bookkeeping** (§38.16): `arms` owns scheduling, intents
   carry transactionally-maintained *copies* of due times validated by a
   trigger against the arm revision — plus the §2.6 ordering trap. One owner
   with an index would do; the copy exists to save a join.
6. **Fixed worker topology as spec**: ~18 named workers with normative
   cadences and batch sizes. The *properties* (bounded work, leases, no truth
   in memory) are the design; the numbers are tuning and should be labeled so,
   or every profiling result becomes a spec amendment.
7. **106 endpoints / 35 events for one bundled client.** A third of the
   endpoints are admin plumbing for the deferred features above. The
   legacy dashboard does its whole job with ~40 routes; the honest v1 API is
   maybe 60 rows, and every deleted row deletes a manifest entry, an OpenAPI
   check, contract tests, and a slice of §2-style drift surface.
8. **OpenAPI/set-equality/traceability CI as designed** — the right idea at
   the wrong granularity. Keep generation and schema validation; drop the
   requirement that the design *document* is the generator's source of truth
   (that is what made §2.1–2.3 possible: prose tables and DDL drifting inside
   one 11.5k-line file that no human can hold). The single-source-of-truth
   should be machine-readable files (schema.sql, endpoints.yaml) that the
   document *quotes*, not the reverse.

### 3.5 Verdict

The domain model, the uncertainty laws, the ingestion/closer discipline, and
the streaming design are a genuinely excellent distillation of this system's
history — better than the legacy's own docs at saying *why*. If Phases 1–3
were carved out with a right-sized auth story and a de-ceremonialized storage
profile, this would be a buildable, even elegant system.

As shipped, the document overreaches: it freezes an enterprise control plane's
worth of schema and protocol for a single-user tool, bans its own escape
hatches, and — despite §0.2's promise that no decisions remain — still contains
contradictions precisely where its frozen artifacts overlap (§2.1–2.5 are all
prose-vs-DDL or DDL-vs-DTO drift). The §2 list is fixable in a day. The §3
issues are choices, and the most consequential one is §38.28's blanket refusal
to right-size: it converts every simplification argument, including the
performance-saving ones in §3.3 that change no observable behavior, into a
design violation.

Recommendation, in order:

1. Fix §2 mechanically (schema/DTO/prose drift — every item is a one-line
   edit plus a digest regeneration).
2. Repair the closed-manifest contradictions first among the §1 gaps: 1.10
   (dictation + clipboard endpoints) and 1.11 (push key bootstrap) make the
   design unimplementable as written; 1.1 (suggestion transport) forces an
   architectural decision about an ephemeral channel; 1.2, 1.8, and 1.9 are
   the ones that will bite during migration; 1.12 and 1.13 are schema/DTO
   fixes best made before the digest freezes further.
3. Re-open §38.28 just far enough to admit behavior-preserving storage
   simplifications (§3.3 a–c) and to move the Phase-7 collaboration/public-
   link/remote schema out of the v1 digest.
4. Make the generated artifacts (schema.sql, endpoint/event manifests) the
   machine-readable source of truth and let the 11.5k-line prose *quote* them
   — the §2 defects all live in prose↔DDL↔DTO drift inside one document, and
   no amount of CI on the document's own text fixes the document being the
   wrong medium for that job.
