# v4 rewrite design — review 5 (fable)

Reviewed artifact: `docs/rewrite-design-v4-codex.md` (13,932 lines, read in full).
Method: full read of the design, then three targeted code audits of the legacy
implementation to verify the design's factual claims (hook wiring / tab &
control plane; dashboard endpoint + SSE + config inventory; producer caps,
codex, scorebar, notifications, viewport). Every legacy citation below was
verified against the working tree at review time.

Context: this is the *fifth* review pass. Reviews 1–4 are already folded into
the design as §38, §40, and §41, so the cheap findings are gone. This review
answers three questions:

1. **Coverage** — is v4 a superset of the legacy system's features?
2. **Fidelity** — where the design describes an existing feature, does it
   describe it *as implemented*, or with gaps/wrong values?
3. **Judgment** — an independent architecture, performance, and
   complexity critique, written to feed the open items in
   `rewrite-design-v5-decisions.md` (points 2–10 there overlap almost exactly
   with the over-complication findings here).

---

## Part 1 — Legacy features not covered by v4

**Headline verdict: v4 is, at this point, a near-complete superset.** After
four folded review rounds, I could not find a whole missing *feature*. I
specifically tested five features I suspected were design inventions — the
verbose/default/focus view modes, composer ↑/↓ history recall, the PWA
manifest/shortcuts/app badge, the live-session strip, and actor-scoped per-tab
badges — and **all five are real in the legacy** (`app.05-session.js:996`,
`app.08-composer.js:700`, `static/manifest.webmanifest:15`,
`app.01-attention.js:166`, `read/session.py:141`). The design covers them
correctly. What remains are narrow residual gaps:

1. **The §41.1 Claude hook manifest is wrong as a closed list.** It names 13
   families and omits five that the legacy dispatcher *functionally routes*
   and that the rest of the v4 document itself depends on:
   - `SessionEnd` (`dispatch.py:168` — parking, the whole §12.7 lifecycle);
   - `StopFailure` (`dispatch.py:213` — relimit trigger §38.18 and the
     subagent-API-death closer §38.37.6);
   - `PostCompact` (`dispatch.py:196` — the compaction latch clear §38.13);
   - `PostToolBatch` (`dispatch.py:204` — the §38.6 "not used to infer
     success" rule still requires *receiving* it);
   - `InstructionsLoaded` (handled by the adopter on every event,
     `adopt.py:75` — the §38.6 negative-start evidence depends on it).
   Meanwhile it lists `PermissionRequest`, which on the Claude side has **no
   functional handler** (audit-subscriber row only; the red-tab permission
   signal is `Notification` — `dispatch.py:166`, `docs/wiring.md:55,59`).
   `PermissionRequest` is a real handled event only for the **codex** host
   (`plugins/codex/tabstatus.py:109`). Since §41.1 is normative and manifests
   are fail-closed ("unknown families remain disabled"), implementing this
   list as written would disable session parking, relimit, and compaction
   closing. One point in the design's favor: `TaskCreated`/`TaskCompleted`
   *are* real first-class hooks (`dispatch.py:184,214`, `task_fmt.py:8`), so
   that part of §41.1 is right and CLAUDE.md's own "a status flip fires NO
   dedicated hook" wording is the thing that's easy to misread.

2. **The launch waiting room and the `wake` event have no named owner.** The
   legacy `POST /api/sessions/new` flow pushes a global `wake {sid,win,cwd}`
   event (`control/launch.py:450`) that drives an optimistic `#/launching`
   waiting room with quiet-mode handoff (`app.02-router.js:184`). v4's
   `createConversation`/`startAgentSession` + `conversation.overview.changed`
   can express this, but no section pins the "the page you launched from
   jumps into the new session the moment it exists" behavior, and it is
   exactly the kind of glue that silently dies in a rewrite.

3. **Web copy/view actions lose their audit trail.** Legacy
   `/api/session/<sid>/copy/<gid>/…` and `/view/<gid>` write `web-copy` /
   `web-view` audit rows *because they bypass the terminal's own audit path*
   (`http/get.py:331-373`). v4's `copyStream`/`getStreamContent` are read-only
   GETs, and v4's rule is that read endpoints add no audit rows. That
   deliberately reverses a deliberate legacy decision; if it stands it should
   be recorded as an accepted difference, not fall out silently.

4. **The generic extension *route* seam is narrower.** The legacy
   `dashboard/ext/` registry gives an extension four route registries
   (`fixed_gets`/`session_gets`/`fixed_posts`/`session_posts`) plus SSE
   channels and badge rows, all contract-tested. v4 promoted the memory
   extension's routes into first-class core endpoints
   (`getAgentSessionMemory`/`…/memory/note`) and gives extensions only typed
   `SurfaceContribution` data + subprocess RPC calls — an extension can no
   longer contribute an endpoint at all. Probably the right call
   (unsanitized extension endpoints are a liability), but it is a seam
   removal that §24 never states.

5. **Client-constant single-ownership (`/api/limits`) is regressed.** The
   legacy serves `upload_max`, `rename_max`, `view_ttl_s` to the browser
   precisely because duplicating them client-side drifted once
   (`app.00-core.js:100-123`). v4 bakes the same numbers into the closed
   request schemas (§38.38) with no config endpoint for the client. Minor,
   but it re-creates a bug class the legacy explicitly fixed.

6. **Bandwidth economy of the overview feed.** Legacy `/events` sends a full
   `sessions` snapshot only on membership change and `sessions-delta`
   otherwise — measured 2.2 MB/min → a few KB/min per remote viewer
   (`sse.py:299-323`). v4's `conversation.overview.changed` "one complete
   replacement DTO per revision, coalesced to 1/s" is per-conversation, so it
   is probably fine — but nobody has done the arithmetic for 20 live
   conversations over a phone tunnel, and `ConversationOverviewDTO` is much
   fatter than a legacy delta row. Worth a stated size budget.

7. **Ambiguity at the red tab.** §38.14 allows interrupt Escapes "only while
   native attention is busy or the screen probe still reports movement." The
   legacy is stricter and the distinction is load-bearing: `QUEUE_TABS =
   (thinking, working, executing)` and the red `awaiting-command` tab is
   deliberately in *neither* busy set, because an Escape there declines a
   dialog — it once killed a live web ask answer (`config.py:235-255`). The
   design should say "red/asking is excluded" explicitly, not leave it inside
   the word "busy."

---

## Part 2 — Where the design mis-describes the existing system

This design pins constants and rules as normative ("frozen into fixture
expectations"). Every wrong pin below therefore becomes a wrong *fixture*,
which is worse than vagueness — parity tests will enforce the wrong value
against a legacy oracle that disagrees. Verified mismatches:

| # | Design claim | Legacy reality | Evidence |
|---|---|---|---|
| 1 | §38.2: "provider constant `COMPACT_MAX_S=120`" | **900 s** (`15*60`), and it is deliberately a **read-side** knob — `compact_fmt.py` refuses to apply a TTL at the latch ("Ageing a latch out is the READ side's job") | `dashboard/config.py:233`, `compact_fmt.py:47-56`, applied at `read/session.py:519`. The 120 looks like a misreading of the measured "~2 minutes (104–139 s)" comment right above the real constant. §38.2's "provider constant" wording also contradicts §38.13's own read-side-expiry rule. |
| 2 | §38.14: interrupt sends "up to four Escape presses" | Up to **five**: one unconditional blind press outside the loop + `INTERRUPT_TRIES = 4` re-presses | `hostctl.py:63,182,187-211`. The gating description (busy tab, screen-delta, `queue_drained` outranking a stale screen) is otherwise accurate. |
| 3 | §40.4: `NS_DRAFT_MAX=50` | **24** | `dashboard/prefs.py:230` |
| 4 | §38.10: mirror width bias "default 0" | **25** everywhere — and the design *also* says 25 in §40.7 (`pane_state.bias_percent … DEFAULT 25`) and §41.4, so this is both a wrong claim and an internal contradiction | `core/hostpane.py:42`, `split.py:77`, `plugins/codex/session.py:104`, `tests/test_l0_units.py:1166` |
| 5 | §38.9: "derive the row budget from the terminal's configured scrollback minus one screenful (legacy fallback 4,800)" | The legacy performs **no runtime derivation** — it reads a flat `env_int("CLAUDE_MIRROR_SCROLLBACK", 4800)`; "5000 − 200" is prose telling the *user* how to set the env | `bin/claude-mirror.py:101-111`. Auto-deriving would be a behavior change shipped under the parity flag. |
| 6 | §38.24: presence "heartbeat interval 15 s" | Derived, not fixed: `view_ttl_s / 2.5` floored at 2 s (~8 s at the default 20 s TTL), re-armed when `/api/limits` lands — the fixed beat was a bug the legacy explicitly fixed | `app.13-init.js:165-174`, `presence.py:49`. A fixed 15 s beat against a 20 s TTL leaves a ~5 s margin for one dropped packet. |
| 7 | §40.3: mail census "unread <60 s, stale ≥60 s" | Boundary inverted at exactly 60: legacy is `now - ts > STALE_S` — unread **≤60**, stale **>60** | `msgs.py:48,351-352` |
| 8 | §38.37.7: codex slug "`basename(git_root) + "-" + sha256(realpath(git_root))[:16]`, byte-for-byte" | Missing the actual rule: basename of `root.rstrip("/")`, sanitized `re.sub(r"[^A-Za-z0-9._-]+","-",…).strip("-")` with `"workspace"` fallback, hash over realpath while the basename uses the *non*-realpath root, and `git_root` falls back to CWD | `plugins/codex/watch.py:100-119`. "Byte-for-byte" + the wrong formula = guaranteed discovery mismatch on any repo with a symlinked path or non-slug characters. |
| 9 | §40.3: Σ row shows "total, fresh input, output, cache read, cache write" | Correct, but the legacy row appends a trailing `≈ $` **cost segment** the design omits; and the total is computed as fresh+out+read+create (four counters), equivalent to the design's three-term formula only if "input" means *gross* input — an easy off-by-cache-creation trap | `bin/claude-scorebar.py:224-234`, `core/ops.py:609-648` |
| 10 | §38.9: viewport restore "at most three delta corrections … a first miss above 400 rows performs the absolute restore once more" | The loop is `range(3)` **total** passes and the gross-miss absolute re-restore consumes pass 0 — so at most **two** delta corrections follow a gross miss | `bin/claude-mirror.py:686-694` |
| 11 | §40.3: teammate rows "reuse the same allocated slot" with two palettes | True, with an arity wrinkle the design misses: slots round-robin over 5 (the `sub` palette) but `TEAM_PALETTE` has **4** colours, so a 5th concurrent teammate wraps to slot 0's hue | `core/slots.py:47-51,67-69,214` |
| 12 | §38.6: "foreground capture has the liveness safety backstop `FG_BACKSTOP_S=7200`" | Value right, owner wrong: it lives in `plugins/claude_code/stream.py:255` (fg-only, not env-overridable); the shared tailer cap is `BACKSTOP_S = 21600` and `POLL_S = 0.4` in `core/tail.py:21-28` | as cited |
| 13 | §41.1 hook manifest | see Part 1 item 1 | `dispatch.py:129-222` |

Claims I verified that **do** match, to be fair about fidelity: all six
excerpt caps (24/24/12/10/60/8 and uncapped MESSAGE/RESULT,
`substream_render.py:59-64`); `CLEAR_GAP_S=0.15`; relimit `COOLDOWN_S=600`;
the exact nudge text and its manual-mode omission; the fable→opus→sonnet
ladder with per-rung account exhaustion and no rung below sonnet
(`model.py:400`, `account.py:165-221`); escape-recheck ≈2 s; settle 20 s /
escalation 300 s / retraction 24 h under Telegram's 48 h; browser-wins-ties
MRU (`presence.py:251`); the `>1e12`-is-milliseconds rule; DSR
arrival-only handshake and the 400/8.0/0.7/2/5 viewport constants; the
subagent palette's no-red/green rule; every SSE event name. The design's
overall fidelity is genuinely high — which is exactly why the residual wrong
pins are dangerous: they are surrounded by enough correct detail to be
believed.

### Internal contradictions (the document against itself)

1. **The §38.36 authentication model vs the §40.7 DDL.** §38.36 (normative)
   defines `principals.kind ∈ {human, service, edge, terminal, remote_agent}`,
   credential kinds `{unix_peer, browser_session, bearer, client_certificate,
   invitation}`, and `browser_sessions` with device id, secret hash, CSRF
   hash, absolute *and* idle expiry. The §40.7 "fourth-review schema closure"
   creates `principals.kind ∈ {user, service, device}`,
   `auth_credentials.kind ∈ {password, api_token, mtls, bootstrap}`, and a
   `browser_sessions` table with none of the device/secret/idle columns, plus
   `principal_role_bindings.role ∈ {admin, operator, viewer, device}` which
   matches nothing in §38.36's scope model. These cannot both be the clean
   install.
2. **Three incompatible collaboration role vocabularies.**
   API/DTOs (§38.38): `viewer|editor|driver|admin`. §38.39 DDL
   `collaboration_invitations.invited_role`: `viewer|participant|actor`.
   §38.39 `conversation_memberships.role`: `owner|viewer|participant|actor`.
   `acceptCollaborationInvitation` cannot write a row that satisfies both its
   request schema and the CHECK constraints.
3. **The Foundation DDL cannot store what the closed API requires.**
   §38.35's final `backends` (kind/display_name/config_json/state) lacks
   `label`, `adapter_id`, `endpoint_config_ref`, `trust_class`, `enabled` —
   all required by `BackendDTO`/`createBackend` (§38.38). Same for
   `execution_targets` (no `label`, `default_mode`, `workspace_root_ref`,
   `enabled` vs `ExecutionTargetDTO`). The superseded §28 fragment actually
   matched the API better than the authoritative replacement.
4. **Mirror bias 0 vs 25** (§38.10 vs §40.7/§41.4 — see Part 2 #4).
5. **`COMPACT_MAX_S` as a write-side "provider constant" (§38.2) vs the
   read-side display-expiry rule (§38.13)** — the legacy settles this
   argument explicitly in favor of read-side (`compact_fmt.py:47-56`).
6. Cosmetic but telling: there is no §39 (numbering jumps 38 → 40, while
   "§38.39" exists as a subsection); `view-mode.changed` breaks the
   dot-separated event naming convention; and the document embeds **three
   generations of its own schema digest** (`7c0b…`, `7238…`, `d55b…`), two of
   them explicitly superseded in-place.

The pattern behind contradictions 1–3 is worth naming: **they are all in
future features** (auth, collaboration) that nothing currently exercises.
The measured, implemented features survived four reviews nearly intact; the
speculative ones accumulated confidently-wrong detail because no fixture can
contradict them. That is the strongest empirical argument in this review for
how v5 should treat future-feature specification (see Part 3).

---

## Part 3 — Independent review

### 3.1 What is genuinely right

- **The core five-entity model is the correct shape.** Conversation / Node /
  AgentSession / Operation / Stream, with actor tracks (§38.1) bolted on,
  directly dissolves the legacy's three hardest structural problems: the
  sid-fork/adopt dance (identity is finally provider-independent), the
  agent-scope hack (`src.split(":",1)[1]` string surgery becomes a real
  subscription dimension), and the "everything is a paint op" flattening that
  makes the legacy's history a rendering log rather than a data model.
- **The uncertainty discipline is the best part of the document.** "Silence
  never proves success," per-kind stream authority, requested-vs-effective
  everywhere, `unknown ≠ zero ≠ unsupported`, closers matching identity
  before consuming — these encode a year of the legacy's hard-won invariants
  (no-hook-on-cancel, the fg-live orphan, the interrupt-marker-as-record
  rule) into laws instead of tribal comments. Law 53 ("a rule without its
  failed alternative is incomplete") is the repo's docs/ culture, formalized.
- **Correctly refusing event sourcing** (§2) for a system whose truth already
  lives in provider artifacts, and correctly refusing PTY wrappers, provider
  transcript conversion, and universal event buses.
- **The legacy import and behavioral-parity machinery** (§38.3, §30.7,
  §38.29) treats the existing system as a measured oracle rather than a
  memory. That is rare and valuable — and it is exactly why the wrong pins in
  Part 2 matter so much.

### 3.2 Architecture critique

**The daemon trades away the legacy's best operational property.** Today the
system is ~20 independent short-lived hook processes plus detached tailers:
no component's death takes down another, tab colors work when the dashboard
is down, and "restart" is not a concept most of the system has. v4 makes one
supervised daemon the availability boundary for *everything* — tab paint,
tee capture, audit, alerts — with an explicit no-spool decision, so every
daemon restart (and on a personal machine that is every upgrade, every debug
session, every crash-loop) is a hole in capture that can never be backfilled.
The decision is recorded as final (§0), so I won't re-litigate it; I will
record its concrete daily cost: the legacy's edge is *already* the
degraded-mode implementation v4 refuses to keep, and after migration Phase 8
deletes it, the first multi-hour daemon outage will lose data the current
system would have kept. At minimum, the health/`ingestion_gap` surface must
be loud enough that the user knows during the outage, not after.

**The specification became a build artifact, and it is failing as one.**
Executable DDL split across four sections in dependency order, three
in-place-superseded schema digests, a 113-row endpoint manifest whose count
is enforced by set-equality CI against a *prose table*, and hand-maintained
duplicate copies of the auth model — the contradictions in Part 2 are not
editorial bad luck, they are the predictable failure mode of stating the
same fact normatively in two prose locations of one 14k-line file. The
v5-decisions plan to split into `schema.sql` / `openapi.yaml` /
per-concern docs is the right fix; go further: the `.sql` and `.yaml` files
should be the *only* normative source for schema/API facts, prose should
cite them, and no digest literal should live in prose at all.

**Future-feature specification produced negative value where it was
untestable.** The decided position (v5 points 1 and 11: keep full scope) is
that later phases must not force invented architecture. Fine — but this
review measured the outcome: every internal contradiction found is inside
remote backends, multi-principal auth, collaboration, or public links. A
complete-but-wrong spec is worse than a stub, because it carries authority.
If full scope stays, the future features need the same treatment the
measured features got: either an executable check (generated schema, compiled
OpenAPI, a walking skeleton) or a stated "shape only, fields not yet
normative" marker. What they cannot be is "closed" (§0.2's word) while
disagreeing with their own DDL.

**Layering is sound; granularity is not.** The hexagonal split, provider
knowledge jail, frontend-injected terminal capabilities, and single-owner
facts are all direct upgrades of the legacy's already-good discipline. But
~30 storage-port protocols, ~15 application services, ~20 worker families,
and a saga runner is a *lot* of moving parts for a single-user tool whose
legacy equivalent is "SQLite + a poll loop." Each worker family carries a
lease schema, backoff ladder, and shutdown contract. The invariants they
protect are real; the number of independent mechanisms protecting them is a
choice, and v5 points 3/7/8 are right to question it.

### 3.3 Performance critique

**Write amplification is the design's biggest unpriced risk.** Follow one
`PostToolUse` through v4: payload blob (exclusive-create + hash + fsync file
+ fsync dir) → `blob_objects` + `blob_references` (trigger) → `observations`
row → up to 7 `observation_consumers` rows, each finishing in its own write
transaction → canonical rows + provenance + decisions → `materialized_activity`
row *with a blob-backed payload* → `structural_changes` row *with a
blob-backed payload* → outbox row (blob-backed) for feed publication →
per-insert BEFORE-trigger EXISTS subqueries on nearly every table — versus
the legacy's single `INSERT INTO ops`. That is plausibly 10–30× the
fsync/write count per event, on a single FIFO SQLite writer with
`busy_timeout=0`. The design knows (§40.6's small-fact-storm profile exists
because review 2 flagged it), but the gates run *after* the full schema is
built, and the only permitted responses exclude every structural remedy
(§38.30: no inline payloads, no removing mechanisms). Specific pressure
points:

- **Blob-backed feed/activity payloads.** `structural_changes.payload_ref`
  and `materialized_activity.payload_ref` mean every SSE event and every
  timeline item is a content-addressed *file* with its own fsync, reference
  rows, and eventual GC. For payloads that are overwhelmingly 0.5–8 KiB
  (the design's own storm profile says 95%), an inline `BLOB` column with a
  size threshold for spilling to CAS would remove two fsyncs and two trigger
  cascades per event. This single change probably decides whether the §38.30
  gates pass.
- **The `blob_state_requires_zero_references` trigger** unions ~20 table
  scans on every blob state flip; `blob_references` doubles the row writes of
  every payload-bearing insert. Reachability could be a GC-time query instead
  of a per-write trigger.
- **Feed retention** (24 h AND newest 100k per scope, blob-backed) for three
  scopes × N conversations is a standing write/GC tax whose only consumer is
  reconnect replay — which the client must *also* be able to survive without
  (resnapshot is mandatory anyway). See "simplify" below.
- **The answerable hook budget** gives a daemon round-trip (socket + identity
  + eligibility + tee prep + `BEGIN IMMEDIATE` commit) 80% of the provider
  deadline with a <0.1% pass-through gate. The legacy does this in-process
  with zero IPC. It can work on NVMe, but a WAL checkpoint stall or GC burst
  eats the budget; the design's own 256 MiB/5-min checkpoint alarm is an
  admission. The pass-through fallback is safe, but every pass-through is a
  permanently uncaptured command.
- **The benchmark manifest itself** (1M nodes / 2M operations / 20 GiB blobs
  / 15-minute runs / slowest supported machine on two OSes) is a
  release-gating test lab for a personal tool. Right idea, over-sized
  ceremony; a nightly job on the actual dev machine with the storm + churn
  profiles would catch the same regressions.

### 3.4 Over-complicated for no (sufficient) reason

Mapped to the open v5-decisions points, with a recommendation each:

| v5 pt | Mechanism | Critique | Recommendation |
|---|---|---|---|
| 2 | Per-Observation consumer matrix (7 kinds × leases × backoff × quarantine rows) | The isolation goal ("a task-snapshot crash must not lose the attention transition") is real, and legacy achieves it with audit-before-swallow inside one process. Seven durable state machines per event is the maximal solution. | **Simplify**: one ingestion transaction with per-facet try/except; quarantine as a single row on failure. Keep the raw Observation + one decision row. |
| 3 | Coordinator actor per Conversation (mailbox, overflow policy, rehydration, parking) | The invariant is only "mutations to one Conversation are serialized" + "open facts are durable rows." | **Simplify**: per-Conversation `asyncio.Lock` in a shared pool. §38.28 already concedes the lock is the semantic core by forbidding it — that prohibition is backwards. |
| 4 | BQSF framed staging files (magic, version, CRC32 header + SHA-256 per ≤256 KiB frame, quarantine rename protocol) | The crash property actually needed is "drop a torn tail, never a false seal." Appending length-prefixed records with one trailing-truncation rule gives exactly that; interior single-frame corruption on a local disk is not a failure mode SQLite itself defends against either. | **Simplify** the frame format; keep the DB-never-ahead ordering rule, which is the load-bearing part. |
| 5 | Activity generations + append/amend/move/supersede/retract + generation CAS | The ordering model (source positions, causal placement, child-task order) is right and hard-won. The five-verb amendment algebra over materialized generations is the expensive half. | **Keep** the composer + materialization; **simplify** live corrections to amend + resnapshot_required. Legacy ships the semantic-order fix with exactly that pair. |
| 6 | Cursor replay on three feed scopes, 24 h/100k retention, transactional outbox publication | Every client must implement full resnapshot anyway (cursor expiry, generation change, auth revision). So replay is an optimization, not a correctness mechanism — but it is priced like truth. | **Simplify**: durable replay for the Conversation scope only (where mid-stream gaps are user-visible); machine/principal scopes reconnect via snapshot. Drop outbox-mediated publication for feed rows written in the same transaction. |
| 7 | Outbox + leases + attempts + reconciler for *every* effect incl. tab paints and pane resizes | Right for non-idempotent effects (typing, launching, push messages, Telegram). Tab paint is idempotent, cheap, and *self-healing by re-derivation* — a desired-state reconcile loop is simpler and strictly more robust than a durable job queue replaying stale paints after restart. | **Split the effect taxonomy**: outbox for non-idempotent effects; reconcile-loop for idempotent presentation effects. |
| 8 | Restart-safe *everything* (arms, latches, input modality, occupancy, TUI drafts…) | Mostly justified — the legacy's take-once hand-offs and kv latches are the same idea. But several rows (input modality, occupancy) duplicate what the next screen probe re-derives in <1 s. | **Keep** durable arms/correlations/drafts; let probe-rederivable facts be cache with `unknown` after restart. |
| 9 | Draft/preference CAS + author sequences + origins + tombstones | This one the legacy actually validates: composer drafts, ns-drafts, origin-echo suppression, and the settle-on-blur directory switch all exist and all bit users before the machinery existed. | **Keep** (rare case where v4's complexity is measured, not speculative). |
| 10 | ~10 provider capability protocols + full untrusted-plugin subprocess contract now | Two providers exist; the third (OpenCode) is speculative; the plugin sandbox (Seatbelt/bubblewrap, JSON-RPC framing, resource quotas) serves zero current plugins. The §38.36/§40.7 divergence shows what specifying unexercised contracts produces. | **Defer** the untrusted-plugin host to a design revision at the first real third-party plugin; keep the port *shapes* for providers but mark them draft until the second provider's port lands. |
| — | Static policy-as-DDL (`notification_retraction_policy` with update/delete-forbidding triggers, seeded rows, startup equality vs a code registry) | The policy table exists in code *and* SQL, with a startup check that they agree — i.e., the SQL copy is pure redundancy whose only effect is that changing a policy row is a schema migration. | Keep the code registry + startup assertion; drop the immutable SQL twin. |
| — | Multi-principal auth (mTLS SPIFFE roles, invitation credentials, CSRF, bearer audiences, pepper rotation) for a loopback single-user tool | Scope decision is made; the concrete finding stands: it is the least-consistent part of the document (Part 2, contradictions 1–2). | If kept: generate both the DDL and the DTOs from one machine-readable auth model, or the two will diverge again. |

### 3.5 Recommendations, in order

1. **Fix the Part 2 mismatches before any fixture is frozen** — each is one
   line in the design, and each otherwise becomes a parity test enforcing the
   wrong behavior against a legacy oracle that will "fail" correctly.
   Priority: §41.1 hook manifest (breaks parking/relimit/compaction outright),
   COMPACT_MAX_S ownership+value, codex slug rule, interrupt press count,
   stale boundary, NS_DRAFT_MAX, bias default, heartbeat derivation,
   scrollback non-derivation.
2. **Reconcile the four internal contradictions** (auth model, collaboration
   roles, backends/targets DDL-vs-DTO, bias) — and treat *where* they
   occurred as evidence for how to specify future features (executable or
   explicitly non-normative, never "closed" prose).
3. **Adopt v5's artifact split aggressively**: `schema.sql` and
   `openapi.yaml` as the only normative sources of their facts; no digests,
   DDL, or endpoint tables maintained by hand in prose.
4. **Re-price the write path before accepting the storage design**: inline
   small payloads with a CAS spill threshold, demote blob reachability from
   per-write triggers to GC-time queries, and run the §40.6 storm profile
   against a schema spike *before* Phase 1, not as its exit gate.
5. **Feed the v5 simplification table above into points 2–8** — the pattern
   across all of them: keep every *invariant* v4 states (they are the
   legacy's real scars), and cut the number of independent durable mechanisms
   enforcing each one to the minimum that still proves it.
6. **Add the missing owners from Part 1**: the §41.1 corrected manifest, the
   launch-wake flow, the web-copy/view audit decision (keep or record as an
   accepted difference), the red-tab interrupt exclusion, and a stated
   overview-feed size budget.

### 3.6 Closing judgment

v4 is an unusually serious design: the domain model is right, the uncertainty
discipline is exceptional, and its fidelity to a very intricate legacy system
is — measured against the code — far better than typical rewrite documents.
Its two real defects are symmetrical. Where the system exists, the document
occasionally *over-trusts its own precision* (wrong constants pinned as
fixtures). Where the system doesn't exist yet, it *manufactures precision*
(closed contracts that contradict their own DDL). Both defects have the same
cure, which should be v5's organizing principle: nothing is normative unless
it is either measured from the legacy (with a citation) or executable (schema,
OpenAPI, fixture) — prose gets to explain, never to pin.
