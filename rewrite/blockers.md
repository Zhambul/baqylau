# Rewrite blockers and deferred decisions

This file is the asynchronous handoff to the user. Agents must not ask the
user questions during autonomous execution. They must append a complete entry
here and continue all work that does not depend on the blocker.

## Status values

- `open`: blocks one dependency chain;
- `investigating`: evidence collection is in progress;
- `resolved`: the existing design or a recorded decision resolved it;
- `needs-user-decision`: the user must decide after reviewing the evidence;
- `wont-fix`: explicitly accepted by the design or user.

## Required blocker entry

Every entry must contain:

- unique blocker ID and first-seen UTC timestamp;
- affected task/coverage row, implementation commit, and current index status;
- exact design section/heading and line references;
- exact code/test/fixture/report paths and relevant command output;
- observed behavior and expected behavior;
- why the issue blocks progress and which dependent work is affected;
- all safe alternatives attempted, including their results;
- the narrowest decision needed, without proposing an unapproved redesign;
- autonomous next action and retry condition;
- owner agent and last-updated timestamp.

## Entries

Existing blocker entries follow below.

## Resolution gate

The previously reported Stage 00–04 issues are now assigned to
`03a-contract-blocker-resolution/task.md`. The design amendments in §43.4 are
the target decisions: 104-byte stream headers, database-enforced operation
idempotency, 114 endpoints including `/api/v1/limits`, generated schema
evidence, explicit OpenCode/Claude manifests, peer-UID local authentication,
and an explicitly withheld legacy parity result. These issues remain pending
until that task produces code and verification evidence; they must not be
silently treated as resolved merely because the design now states a decision.

## BLOCKER-ORCH-001: Orchestrator cannot set per-agent reasoning-effort level

- Timestamps: 2026-08-06T00:00:00Z opened
- Affected: orchestration-level requirement in /goal directive ("implementor is always OPUS model with low effort, reviewer1 is always OPUS model with high effort level, reviewer2 is always codex model, manual tester is always a SONNET model with low effort"); not tied to a specific step/coverage row — applies to all steps 00-10.
- Design/code/test references: n/a (tooling constraint, not a design contradiction)
- Command output / observed behavior: The Agent tool available to the orchestrator only accepts a `model` parameter (sonnet | opus | haiku | fable). There is no reasoning-effort / thinking-budget / low-high parameter exposed. Effort is fixed by each agent's own definition frontmatter, not settable by the caller.
- Expected behavior: Orchestrator should be able to launch implementor agents as opus+low-effort and reviewer1 agents as opus+high-effort, distinct from each other.
- Impact: Cannot mechanically guarantee the requested effort differentiation between implementor and reviewer1 (both would run as plain "opus" with whatever default effort their agent definition uses). Model selection (opus/codex/sonnet) itself IS controllable and will be honored exactly.
- Attempted alternatives: Checked Agent tool schema; confirmed only `model` field exists, no effort/reasoning field.
- Required decision: Accept model-only differentiation (opus for implementor and reviewer1, codex agent for reviewer2, sonnet for manual tester) as the closest compliant substitute, OR provide an effort-capable agent definition/tool.
- Next action: Proceeding with model-only control per binding continue-independent-work rule; this blocker does not halt the dependent chain since model selection is still correct and differentiated by role.
- Retry condition: If an effort-parameterized agent/tool becomes available, re-launch future steps' implementor/reviewer1 pairs with explicit low/high effort.
- Owner: orchestrator

## BLOCKER-STEP00-001: Design has no OpenCode edge/plugin contract

- Timestamps: 2026-08-05T17:20:58Z opened
- Status: `resolved` (normative contract added in v4 §43.4; implementation
  verification is owned by `03a-contract-blocker-resolution`)
- Affected: step 00 (rust-shims-and-harness), implementation commit 2272f87 in
  /Users/z.yermagambet/code/personal/baqylau2
- Design references: §10.3 line 1615 promises "The Codex and OpenCode manifests
  provide their own exact key tables and fixtures", but no such OpenCode table
  exists anywhere in the document. §38.4 (lines 4857-4962) specifies the
  provider-edge subscription manifest, trust, and installation workflow only in
  terms of Claude Code hooks and a status line. §41.1 (lines 13856-13887) gives
  Claude Code an explicit per-family hook manifest; §41.2 does the same for
  Codex. There is no equivalent section for OpenCode. §38.37.8 (lines
  10912-10944) covers OpenCode *record mapping* only. §29 line 3811 lists
  `opencode/` as a bundled Python provider adapter package.
- Observed behavior: The design specifies, for OpenCode, only how native records
  map to canonical entities. It does not specify the edge format, the plugin
  contract, the subscribed native event families and their
  observational/answerable/delegating classification, the measured native
  deadlines, or the allowlisted environment-key table.
- Expected behavior: An OpenCode section analogous to §41.1/§41.2, naming the
  subscribed families, their classification and deadlines, and the exact
  environment-key table, plus a statement of the edge's packaging format.
- Impact: None on step 00. The task bundle explicitly required an OpenCode
  TypeScript plugin, so the work proceeded on an engineering decision rather
  than halting. It does affect any later step that must validate OpenCode edge
  behavior against a normative contract.
- Attempted alternatives: (1) Searched the whole design for an OpenCode manifest
  or key table; only the mapping section and the unfulfilled §10.3 promise
  exist. (2) Checked the legacy repository for an OpenCode adapter to mirror;
  `plugins/` contains only `claude_code`, `codex`, and `otel`, so there is no
  prior implementation to match either.
- Decision taken (documented, not approved): Implemented the edge as a
  TypeScript plugin matching OpenCode's real runtime contract
  (`@opencode-ai/plugin@1.14.39`, `Plugin`/`Hooks`), because OpenCode exposes no
  command-hook surface that a Rust executable could subscribe to, so the
  Claude/Codex edge shape is not available. Classified `event`,
  `chat.message`, `tool.execute.before`, `tool.execute.after`, and
  `command.execute.before` as observational; classified `permission.ask`,
  `chat.params`, and `chat.headers` as delegating and left them disabled with no
  handler registered at all, since each can mutate `output` and thereby take
  over a decision. Environment allowlist was chosen as OPENCODE_CONFIG,
  OPENCODE_CONFIG_DIR, OPENCODE_MODEL, OPENCODE_AGENT, PWD, OLDPWD,
  KITTY_LISTEN_ON, mirroring the shape of Claude's registered table.
  Code: crates/baqylau-edge/src/manifest.rs (OPENCODE_ROWS),
  crates/baqylau-edge/src/provider.rs (env_allowlist),
  plugins/baqylau-opencode/src/index.ts.
  Fixtures: fixtures/providers/opencode/{registered_observational_event_is_captured,
  delegating_permission_ask_fails_closed,unregistered_observational_becomes_generic}.
- Required decision: Confirm or replace the classification and environment-key
  table above with a normative OpenCode section, and state whether the OpenCode
  edge is a TypeScript plugin or a bundled Python adapter as §29 implies.
- Next action: None pending; step 00 is complete. Later OpenCode work should
  treat the above as provisional until the design section exists.
- Retry condition: Re-validate the plugin against the design once an OpenCode
  edge/manifest section is added.
- Owner: impl-00 (step 00 implementor)
- Last updated: 2026-08-05T17:20:58Z

## BLOCKER-STEP04-003: §42.1 requires `GET /api/v1/limits`, which the closed 113-endpoint manifest did not contain

- Timestamps: 2026-08-06T00:00:00Z opened
- Status: `resolved` (v4 §43.4 makes this the 114th endpoint; implementation and
  removal of the temporary exclusion are owned by
  `03a-contract-blocker-resolution`)
- Affected: step 04 (canonical-read-model), coverage rows for §§38.24/38.36–38.38
  and §42.1; implementation commit e780fdd in
  /Users/z.yermagambet/code/personal/baqylau2; index status: in progress
- Design references:
  - §42.1 line 14040: "The daemon owns client limits through `GET
    /api/v1/limits`. The response contains `upload_max`, `rename_max`,
    `view_ttl_s`, and the derived presence heartbeat
    `max(2,floor(view_ttl_s/2.5))`. Request validation and client display both
    consume this response; the browser does not duplicate these constants."
  - §38.36 line 10120: "The operation manifest below contains exactly one row
    for each of those 113 method/path pairs. An architecture test extracts the
    pairs from Sections 38.24 and 38.38, rejects duplicates, and requires set
    equality before the generated OpenAPI artifact can be accepted."
  - §38.24 (lines 6491–6699) and §38.38 (lines 11251–11386): neither table
    contains a `/api/v1/limits` row.
  - §43.2 line 14126 makes endpoint counts generated verification outputs.
- Command output / observed behavior:
  ```
  $ grep -n "api/v1/limits" phase0/design/rewrite-design-v4-codex.md
  14040:The daemon owns client limits through `GET /api/v1/limits`. The response
  ```
  ```
  $ python3 -c "import json; d=json.load(open('phase0/inventory/endpoints.json')); \
      rows=d['rows']; print(len(rows), len([r for r in rows if 'limits' in json.dumps(r).lower()]))"
  113 0
  ```
  The generated Phase 0 endpoint inventory contains 113 rows and zero rows
  mentioning `limits`. So §42.1 names an endpoint that the closed manifest
  §38.36 requires to be exactly 113 rows does not include. Adding it makes the
  set 114 and fails §38.36's set-equality gate; omitting it fails §42.1's
  requirement that the daemon own client limits and that the browser not
  duplicate the constants.
- Expected behavior: one of (a) the 113-row manifest contains a `getLimits` row
  and the generated inventory has 114 rows, or (b) §42.1 does not require a
  distinct endpoint and the four limit values are folded into an existing
  response (`HealthDTO` is the only plausible host).
- Impact: blocks nothing today. It does block declaring the §38.36 endpoint
  set-equality gate green, and it blocks step 10's OpenAPI parity check from
  being an exact equality rather than an equality-modulo-exclusions.
- Attempted alternatives:
  1. Searched §38.24 and §38.38 for a limits row under any spelling
     (`limits`, `upload_max`, `rename_max`, `view_ttl`): only §42.1 mentions
     them. Result: no manifest row exists.
  2. Considered folding the four values into `HealthDTO`. Rejected as a
     unilateral schema change: §38.38 declares `HealthDTO` closed with
     `additionalProperties=false`, so adding fields is a design change, not an
     implementation choice.
  3. Considered omitting the endpoint. Rejected because §42.1 states the
     browser "does not duplicate these constants", so dropping the endpoint
     forces exactly the duplication it forbids.
- Narrowest decision needed: whether `GET /api/v1/limits` is a 114th endpoint
  or whether its four values belong on an existing response. No redesign of the
  authentication, pagination, or DTO model is implied either way.
- Autonomous next action: implemented as a served endpoint and recorded in the
  generated read-endpoint artifact under an explicit
  `design_contradiction_exclusions` list, so the cross-check against
  `phase0/inventory/endpoints.json` is an equality-modulo-declared-exclusions
  rather than a silent workaround. The exclusion list names this blocker ID.
- Retry condition: re-run
  `tests/architecture/test_read_endpoint_manifest.py` once the design resolves
  the row; the test fails if the exclusion becomes unnecessary, so a fix cannot
  leave the workaround behind.
- Owner: impl-04 (step 04 implementor)
- Last updated: 2026-08-06T00:00:00Z

## BLOCKER-STEP04-001: §38.9's fold table names a provider inside a core vocabulary

- Timestamps: 2026-08-06T00:00:00Z opened
- Status: `open` (does not block step 04; implemented with a symbol-scoped, cited exception)
- Affected: step 04 (canonical-read-model), coverage rows for §38.9 and §§10.2/24.2;
  implementation commit e39a733 in /Users/z.yermagambet/code/personal/baqylau2
- Design references:
  - §38.9 line 5289 prints the fold table with `codex` as a folded **activity class**
    in both the `default` and `focus` rows.
  - §38.9 line 5281 makes `register` `(host | agent | team | codex | quiet | extension)`.
  - §10.2 lines 1584-1597 confine provider knowledge to plugins: "The core knows only
    canonical values, plugin IDs, and declared capabilities."
  - §24.2 rule 8 line 3000: "Provider-name literals are absent from core and generic
    surfaces except declared registries."
- Command output / observed behavior:
  ```
  $ .venv/bin/python -m pytest tests/architecture/test_import_direction.py -k literals
  AssertionError: baqylau/application/activity/queries.py names providers/terminals: ['codex']
  ```
  The fold table is a normative server-defined table that a surface must be able to
  apply without asking any provider what folds in `default`, so it cannot be
  provider-contributed; but holding it in the application layer puts a provider name
  in generic code.
- Expected behavior: either §38.9's activity-class and register vocabularies are
  provider-neutral (for example `provider_secondary` instead of `codex`), or §24.2
  rule 8 explicitly names this table as one of its "declared registries".
- Impact: blocks nothing. It does mean the architecture test carries a permanent
  exception, and a future provider added to that table needs the same treatment.
- Attempted alternatives:
  1. Make the fold table provider-contributed. Rejected: §38.9 prints it as a fixed
     normative table and a surface asking a provider what folds would make the view
     mode depend on plugin availability.
  2. Rename the class locally to a neutral token and translate at the edge. Rejected:
     the token is on the wire in `ActivityItemDTO.register`, which §38.38 declares
     closed, so renaming it is a schema change rather than an implementation choice.
  3. Whole-file exemption. Implemented first, then rejected during review: it let
     unrelated provider-name dispatch anywhere in the file pass undetected. The
     exemption is now scoped to the named symbols `FOLD_TABLE` and `ActivityItemDTO`,
     with a test proving dispatch beside the table is still caught.
- Narrowest decision needed: whether `codex` in §38.9's two vocabularies is a provider
  identity (in which case the tables need neutral tokens) or a server-defined category
  that happens to be spelled after a provider (in which case §24.2 rule 8 should say so).
- Autonomous next action: implemented with the symbol-scoped exception described above,
  data-only, no provider-name branch; recorded in
  `PROVIDER_KNOWLEDGE_EXCEPTIONS` with a required §38.9 docstring citation. The related
  §38.38 `resume_tool` enum was *not* exempted - the core now stores an opaque provider
  token and the closed enum is generated from the registered renderers.
- Retry condition: re-run `tests/architecture/test_import_direction.py` after any change
  to §38.9's vocabularies; delete the exception if the tokens become neutral.
- Owner: impl-04 (step 04 implementor)
- Last updated: 2026-08-06T00:00:00Z

## BLOCKER-STEP04-002: §38.2 inverts the legacy compact-boundary default

- Timestamps: 2026-08-06T00:00:00Z opened
- Status: `resolved` (the design decides it; recorded because §30.7 requires every
  old/new behavioural difference to be an explicit product decision)
- Affected: step 04 (canonical-read-model), coverage row for §38.2 and the §30.7
  compaction parity dimension; implementation commit e39a733
- Design references:
  - §38.2 lines 4656-4660: "The boundary is the safe fail-open value: it is applied
    even while the native parent walk has not yet produced a descendant. It is discarded
    only when the record graph positively proves that the boundary was reverted or
    belongs to a different branch."
  - §38.2 line 4668: "A missing/unreadable branch proof fails open to the boundary
    value, not the stale assistant value."
  - Legacy `plugins/claude_code/transcript.py` lines 853-860 and `_boundary_live`: the
    legacy reader applies the boundary only *after* positively proving it live.
- Command output / observed behavior:
  ```
  $ grep -n "_boundary_live" plugins/claude_code/transcript.py
  853:    …but ONLY while that compaction is still the conversation's live branch
  ```
  Legacy fails closed (drop the boundary without proof); v4 fails open (apply it
  without proof). The two produce different context figures in the window between a
  compaction and the next assistant record.
- Expected behavior: v4's, per §38.2. The failure it prevents is the one legacy
  measured: 522,826 tokens reported for a context actually holding 8,969.
- Impact: none blocking. It is a visible behavioural difference a parity comparison
  would otherwise flag as a regression, which is why it is recorded.
- Attempted alternatives: implementing legacy's fail-closed default and treating §38.2
  as aspirational. Rejected: §38.2 states the direction twice and names the fail-open
  case explicitly, and the failure mode it avoids is the worse one.
- Narrowest decision needed: none. Recorded for §30.7 traceability.
- Autonomous next action: implemented fail-open, with
  `tests/parity/test_read_model_parity.py::TestCompactionParity` asserting the new
  behaviour against legacy's own measured 522,826/8,969 pair and naming this blocker.
- Retry condition: n/a.
- Owner: impl-04 (step 04 implementor)
- Last updated: 2026-08-06T00:00:00Z

## BLOCKER-STEP04-004: `conversations.active_agent_session_id` has no writer, so `conversation_title_current` can never hold a `provider_live` revision

- Timestamps: 2026-08-06T00:00:00Z opened
- Status: `open` (does not block step 04; the read path degrades honestly, but no
  live provider title can ever become current until a writer exists)
- Affected: step 06 (projections-and-machine-services) owns the missing writer;
  surfaced by step 04 (canonical-read-model), implementation commit cdf54a1 in
  /Users/z.yermagambet/code/personal/baqylau2. Coverage rows for §7.1 and §38.2.
  Current index status: step 04 in progress, step 06 pending.
- Design references:
  - §7.1 line 708 declares the column: `active_agent_session_id  nullable
    designated interactive AgentSession`.
  - §38.2 line 4768: "`owner` is `provider_live` while the Conversation's
    designated `active_agent_session_id` is live and `baqylau_parked` after it is
    parked."
  - §38.2 line 4777: "Changing `active_agent_session_id` selects that session's
    newest effective provider revision by source order; while it has no effective
    title, the prior current value remains visible as `stale`."
  - Schema `title_current_scope_insert` trigger,
    `src/baqylau/adapters/storage/sqlite/schema.sql:1221-1233`, and its `_update`
    twin at 1234-1248: a `conversation_title_current` row is permitted only when
    the referenced revision is either `owner='baqylau_parked'` **and**
    `c.active_agent_session_id IS NULL`, or its `owner_agent_session_id` equals
    `c.active_agent_session_id`.
- Command output / observed behavior:
  ```
  $ python3 -c "... from statements import build_registry; \
      [(s, d.operation) for s, d in build_registry().statements.items() \
       if 'active_agent_session_id' in d.sql]"
  statements mentioning the column: NONE
  conversations write statements: ['conversations.insert']
    conversations.insert -> does not mention
  ```
  ```
  $ python3 -c "PRAGMA table_info(conversations) for active_agent_session_id"
  column: name=active_agent_session_id type=TEXT notnull=0 default=None
  ```
  End-to-end probe (insert a live session, an `owner='provider_live'`
  `state='effective'` title revision, then the current pointer):
  ```
  provider_live revision inserted OK
  ABORTED as predicted: current_title_owner_or_scope_mismatch
  current pointer rows: 0
  ```
  So the column is nullable with no default and no declared statement ever sets
  it; it is therefore permanently NULL, and the trigger's only satisfiable branch
  is the `baqylau_parked` one. A `provider_live` revision can be appended but can
  never be promoted to current.
- Expected behavior: whichever service designates the interactive continuation
  should write `conversations.active_agent_session_id` in the same transaction as
  the designation, so §38.2's ownership-transfer rule and the trigger's second
  branch become reachable.
- Impact: does not block step 04. The step-04 read path already degrades honestly
  rather than fabricating: `_current_title` selects from the append-only revision
  history via `select_current_title`, which falls to the parked-override branch and
  reports `freshness=stale` when no live session owns an effective title. It *does*
  block §38.2's live-title behaviour end to end, and it blocks step 10 from
  claiming title parity for a live provider session, because the legacy system does
  show a live provider title.
- Attempted alternatives:
  1. Writing the column from the step-04 read path. Rejected outright: reads never
     mutate (§38.25), and this would be exactly the accidental write the
     `web-copy`/`web-view` exception machinery exists to forbid.
  2. Adding a declared writer in step 04 anyway. Rejected on ownership:
     `rewrite/index.md` assigns "their fact ingestion/projection writers" to step
     06, and the designation is a lifecycle decision (which session is the
     interactive continuation), not a read-model concern. Guessing the trigger
     condition from the read side would also invent the designation policy.
  3. Relaxing the trigger. Rejected: the trigger is the executable form of §38.2's
     rule that a non-active session "cannot move `conversation_title_current`", so
     weakening it would delete the invariant rather than satisfy it.
- Narrowest decision needed: which service owns writing
  `conversations.active_agent_session_id`, and on which evidence it changes
  (session start, resume, park, and migration are the four candidates §38.2 and
  §21.4 touch). No change to the trigger, the title revision model, or the read
  path is implied.
- Autonomous next action: none required in step 04; the read path is already
  correct for the absent case and
  `tests/unit/application/test_session_facets.py::TestTitleOwnership` covers both
  the live-session and no-live-session branches at the unit level, so the writer
  can be added without changing the read model.
- Retry condition: once a declared statement writes the column, re-run
  `tests/contract/storage/test_read_model_stores.py` and add an end-to-end case
  promoting a `provider_live` revision to current; the abort above should no longer
  occur.
- Owner: impl-04 (step 04 implementor); resolution owner step 06
- Last updated: 2026-08-06T00:00:00Z
