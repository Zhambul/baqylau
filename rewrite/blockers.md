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
  /Users/z.yermagambet/code/personal/baqylau2; resolved by
  03a-contract-blocker-resolution, final commit 84b809d
  (branch step-03a-contract-blockers); index status: completed
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

## BLOCKER-STEP03A-001 (BC-1): §38.24's endpoint table lacks the `getLimits` row

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `open`, non-blocking (workaround derives the row from §43.4 with a citation
  and a test that fails loudly if §38.24 ever grows a conflicting row)
- Affected: step 03a (contract-blocker-resolution), coverage rows for §38.24/§38.36
- Design references: §43.4 item 3; §38.36 line 10120 (114-endpoint set-equality gate);
  §38.24 (lines 6491–6699), no `/api/v1/limits` row present
- Observed: §38.24 and §38.38 would disagree (113 vs 114) without the derived row;
  `extract_api.extract_binding_endpoints`/`merge_binding_endpoints` in
  /Users/z.yermagambet/code/personal/baqylau2/phase0/tools/extract_api.py implement the
  workaround and raise if §38.24 ever gains its own conflicting row
- Required decision: add the `GET /api/v1/limits` row to §38.24's table in the design
- Owner: impl-03a; resolution owner: design-doc maintainer

## BLOCKER-STEP03A-002 (BC-2): §38.38's `getLimits` row names a durable owner table that does not exist

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `open`, non-blocking (served response is built from configuration, not the
  named table; nothing depends on the table today)
- Affected: step 03a/04, storage matrix, generated endpoint manifest
- Design references: §38.38's `getLimits` row, trace cell
  `DiagnosticService.limits / DiagnosticStore.read_limits / runtime_limits / -`
- Observed: `runtime_limits` appears in none of the 152 schema tables, the generated
  catalog, or the storage matrix; only the generated endpoint manifest carries it as
  provenance
- Required decision: whether `runtime_limits` is a real table a later step must create,
  or a design transcription error that should read `-`
- Owner: impl-03a; resolution owner: whoever owns persisted client limits (unassigned)

## BLOCKER-STEP03A-003 (BC-3): the unresolved-parity override is advisory, not enforced

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `open`, non-blocking (correct and tested today; risk is in a future consumer)
- Affected: step 10 (migration, parity, and cutover) — future
- Design references: §43.4 item 9 ("silently mark parity passed" forbidden)
- Observed: `docs/parity/unresolved-legacy-dimensions.json` correctly marks
  `legacy_jobs`/`legacy_monitor_streams`/`legacy_running`/`legacy_errors` as
  `unknown`/`may_be_used_as_golden: false`, but `phase0/parity/index.json` and the
  frozen oracle files still say `status: "captured"` with an empty golden value, and
  nothing forces a future parity loader to consult the override
- Required decision: either the future parity loader must treat the override as a
  mandatory input (with a test proving a loader that ignores it fails), or
  `capture_parity.py` gains a status value for "the reader and its own audit rows
  disagree" and the corpus is recaptured on the original capture machine
- Owner: impl-03a; resolution owner: step 10 implementor

## BLOCKER-STEP03A-004 (BC-4): `phase0/run_all.sh --verify` cannot pass off the original capture machine

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `open`, non-blocking (design-derived half is reproducible and green; only the
  live-recapture half is machine-dependent)
- Affected: phase0 CI/verification tooling
- Observed: measured on one machine, one legacy checkout: baseline `cdf54a1` and
  step-03a HEAD both differ from a fresh recapture by 563 lines, of which 0 are
  design-citation lines; remaining drift is live provider-build/payload churn
  (Claude Code 2.1.222 → 2.1.223 mid-session)
- Required decision: split the gate into a design-derived half (reproducible, CI-suitable)
  and a live-recapture half (capture-machine only)
- Owner: impl-03a; resolution owner: phase0 tooling maintainer
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP05-000: step 05 status of the three carried step-04 blockers

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `resolved` (record only; no decision required)
- Affected: step 05 (streaming-and-rendering), implementation branch
  `step-05-streams-render` in /Users/z.yermagambet/code/personal/baqylau2
- BLOCKER-STEP04-001 (§38.9's fold table names a provider): **re-verified sound, still
  open as a design question.** The exception in
  `tests/architecture/test_import_direction.py:252-265` is still scoped to the two named
  symbols `FOLD_TABLE` and `ActivityItemDTO`, still requires a `§38.9` citation in the
  module docstring, and
  `test_a_symbol_scoped_exemption_does_not_cover_the_rest_of_its_file` still proves that
  dispatch beside the table is caught. `test_every_provider_knowledge_exception_is_documented_and_bounded`
  additionally forbids any `if`/`match`/comparison on a provider literal in an exempt
  module. No new file-level exemption was added by step 05: the same token appears in
  `src/baqylau/presentation/blocks.py::Register` as a closed §38.38 wire enum with the
  identical citation, and the two new provider-flavoured spellings step 05 needed
  (§38.10's `claude_scorebar` window tag and §38.10's `claude_session` user variable)
  were placed in the kitty *adapter* instead, with the generic geometry function taking
  the tag as an argument - so the count of exceptions in domain/application is unchanged
  at two. The narrowest decision §38.9 still needs is unchanged.
- BLOCKER-STEP04-002 (§38.2 compact-boundary fail-open): **confirmed implemented and
  tested, unchanged.** `src/baqylau/application/session_facets/context.py` still applies
  the boundary without a positive branch proof, and
  `tests/parity/test_read_model_parity.py::TestCompactionParity` still asserts it against
  legacy's own measured 522,826/8,969 pair plus the `_boundary_live` source citation.
  Nine tests, all passing. No new work.
- BLOCKER-STEP04-004 (`conversations.active_agent_session_id` has no writer): **correctly
  deferred; step 05 needed nothing from it.** Step 05 touches no AgentSession lifecycle or
  title ownership. §38.20's `host_state`/`work_state` pair, which step 05 does consume for
  tailer ownership, is a different column set on a different table
  (`agent_session_lifecycle`) and is unaffected by the trigger this blocker describes.
  The step-05 read paths that could plausibly have needed a live provider title
  (scorebar session row, overview coalescing) take the already-composed title revision as
  input and never select one, so the deferral to step 06 stands.
- Owner: impl-05 (step 05 implementor)
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP05-001: §20.1's terminal role names did not match the declared port stub

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `resolved` (fixed in step 05; no design decision required)
- Affected: step 05, coverage rows for §20.1/§20.2; `src/baqylau/application/ports/terminals.py`
- Design references: §20.1 lines 2632-2641 name ten roles: `TerminalPresence`,
  `TerminalDiscovery`, `TerminalDisplay`, `TerminalInput`, `PaneManager`,
  `ViewportReader`, `FocusProbe`, `Clipboard`, `WindowTagger`, `OpenActionChannel`.
- Observed behavior: the step-02 placeholder declared six names -
  `TerminalDiscovery`, `TerminalBindingVerifier`, `TabPainter`, `PaneHost`,
  `InputDriver`, `OpenActionChannel`. Four of those (`TerminalBindingVerifier`,
  `TabPainter`, `PaneHost`, `InputDriver`) appear nowhere in the design; a full-document
  grep finds only §20.1's ten plus §38.14's `TerminalInput` and §38.9's
  `OpenActionChannel`.
- Expected behavior: the port list matches §20.1 exactly, so it can be checked against it.
- Impact: none outstanding. Left unfixed it would have made the ownership manifest's
  "terminal role" rows uncheckable.
- Action taken: replaced the invented names with §20.1's ten, with no aliases -
  an alias would keep the unfindable name checkable and defeat the point.
  `tests/contract/terminals/test_kitty_and_null.py::TestDeclaredRoles` asserts the exact
  tuple in the design's order.
- Owner: impl-05 (step 05 implementor)
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP05-002: §40.3's "prints the token Σ row first" is ambiguous about row order

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `resolved` (resolver confirmed the implemented reading; no code change was
  required, and the row order was verified to already match legacy parity)
- Affected: step 05, coverage rows for §40.3/§42.2; `src/baqylau/presentation/scorebar.py`
- Design references:
  - §40.3 line 12644: "The pinned renderer prints the token `Σ` row first so narrow-pane
    tail dropping preserves the headline. It shows total, **fresh input**, output, cache
    read, and cache write".
  - §42.2 line 14074: "The scorebar Σ row displays total, fresh input, output, cache read,
    cache write, and a trailing approximate cost segment."
  - §38.10 line 5410: "The five-row scoreboard is a separate pinned terminal window".
- Observed: "prints the token `Σ` row first" can mean (a) the Σ row is scorebar row 0, or
  (b) the Σ row's *total* segment is printed first within that row. Tail dropping removes
  trailing segments from a row that does not fit - it is a within-row operation - and the
  next clause lists the segments starting with the total, which §42.2 repeats with the cost
  "trailing". Under reading (a) the sentence's stated purpose ("so narrow-pane tail dropping
  preserves the headline") does not follow, because reordering whole rows does not change
  what a too-narrow row drops.
- Impact: none blocking. Under reading (a) the five rows would be permuted; every figure,
  arithmetic rule, and drop order is identical either way.
- Decision taken (documented, not approved): reading (b). Segment order inside the Σ row is
  total, fresh input, output, cache read, cache write, cost, with the total marked
  non-droppable; the five rows keep the measured legacy order (`⬡ ✉ ▪ Σ` files), which is
  what §30.7's parity comparison compares against. `ROW_ORDER` names the choice explicitly
  so it is inspectable, and `presentation/scorebar.py`'s module docstring records both
  readings.
- Narrowest decision needed: whether §40.3's "first" refers to the row's position among the
  five or the total's position within the row. Nothing else is implied.
- **Resolution (2026-08-07, resolver):** reading (b) is correct. §40.3's "first" describes the
  *total's* position within the Σ row's segments, not the Σ row's vertical position. The
  resolver traced §40.3 lines 12644-12651 back to the legacy docstring it paraphrases and
  confirmed it verbatim: `bin/claude-scorebar.py:225-227` reads "Row 3: Σ token breakdown +
  cost — total-first so a narrow pane keeps the headline; the `≈ $` cost ... goes LAST so
  tail-drop sheds it before the token breakdown", and `compose`'s own docstring at
  `bin/claude-scorebar.py:268-272` fixes the vertical order as "Row 0 is the always-on ⬡
  session id; row 1 is the ✉ message census; row 2 is the ▪ activity summary; row 3 is the Σ
  token breakdown ... row 4 is the unique-file count". §42.2 line 14074 agrees by listing "a
  trailing approximate cost segment" last.
- Verification performed: **no code change was required.** `ROW_ORDER` in
  `src/baqylau/presentation/scorebar.py` already encoded the five rows' *vertical* positions
  as `(session, messages, activity, tokens, files)`, placing Σ at index 3 - not row 0 - and
  the Σ row's segment order was already total-first with the cost trailing. Two hardening
  changes were made so the distinction cannot be lost later: a named `SIGMA_ROW_INDEX = 3`
  constant, and two tests -
  `test_the_sigma_row_is_vertically_fourth_not_first` (asserts the full tuple, the index, and
  the composed row at that index) and `test_the_total_leads_the_sigma_rows_segments` (asserts
  the six segment keys in order). The module docstring now records both readings, why the row
  reading does not follow from §40.3's stated purpose, and the legacy citations above.
- Retry condition: n/a. `tests/unit/presentation/test_formatter_and_scorebar.py::TestScorebarRows`
  now fails loudly if either the vertical order or the segment order is changed.
- Owner: impl-05 (step 05 implementor); resolved by resolver
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP05-003: the `clients/web` browser application is deferred, not implemented

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `resolved` (accepted, disclosed deferral; recorded because the deferral was a scope
  narrowing that should have been stopped-and-reported rather than decided silently)
- Affected: step 05 (streaming-and-rendering); §29's `clients/web/` tree
- Design references:
  - §29 lines 3886-3898 print `clients/web/` with `package.json` and
    `src/{api,sse,state,views,controls,notifications,extensions,auth}` plus `tests/`.
  - §29 line 3936: "This layout is required, not illustrative."
  - §29 line 3977: "It does not make those features mandatory if a later design decision
    removes them."
  - §20.5: "web escapes/sanitizes and allowlists link schemes"; §38.22's client application
    rules; §42.1's overview feed size budget.
- Observed behavior: no `clients/web/` directory exists. The **server** side of every
  web-facing contract is implemented and tested in Python: the escaped-HTML emitter with a
  link-scheme allowlist (`presentation/ansi.py::to_html`), §38.22's client reducers
  (`entrypoints/http/reducers.py`, step 04), cursor validation/replay/resnapshot and the
  three routed SSE endpoints (`entrypoints/http/{feeds,sse_routes}.py`), presence generation
  compare-and-set, and §42.1's overview budgets.
- Expected behavior: a TypeScript browser application under `clients/web/`.
- Impact: the "web reconnect" and "SSE replay/resnapshot" completion gates are provable
  server-side and are proved there
  (`tests/integration/test_view_mode_and_reconnect.py`,
  `tests/end_to_end/test_streaming_plane_is_wired.py::TestRealConnectionSequence`). No Python
  gate covers browser code, and none is claimed to. Nothing in steps 06-10 is blocked: the
  daemon serves the contract a browser would consume.
- Attempted alternatives: (1) building a minimal TypeScript SPA - rejected as unverifiable in
  this step, since no TS test runner, bundler, or lint gate is configured in this repository
  and an unexercised client would be a larger untracked risk than an absent one;
  (2) implementing the browser reducers a second time in Python as a "reference client" -
  rejected because §38.22's reducers already exist once in `entrypoints/http/reducers.py` and
  a second copy is the duplication that lets two implementations disagree.
- Narrowest decision needed: which step owns `clients/web/`, and whether a TS toolchain gate
  (tsc/vitest/eslint) is added to the project's quality bar when it lands. No change to any
  server contract is implied either way.
- Process note: this deferral was disclosed in the step-05 report but was decided rather than
  escalated. Under the current blocker protocol it should have been stopped-and-reported at
  the moment the scope narrowing was chosen; recorded here so the process gap is on the record
  and not only the technical one.
- Retry condition: re-open when a step is assigned `clients/web/`; the server contracts it
  consumes are pinned by the tests named above and should not need to change.
- Owner: impl-05 (step 05 implementor); resolution owner: rewrite plan maintainer
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP05-004: the sanitizer neutralized escape sequences but not bare control bytes

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `resolved` (fixed in step 05; recorded because it was a security defect with a
  working exploit, found by review rather than by the tests written alongside it)
- Affected: step 05; `presentation/ansi.py`, `application/resources.py`
- Design references:
  - §38.8: at presentation "parsed ANSI SGR colour/style and OSC 8 links are allowed. Cursor
    movement, screen erase, device control, title change, clipboard OSC, and every unknown
    escape are neutralized into visible harmless text."
  - §20.5: "Sanitize at every rendering leaf"; terminal output "never forwards raw producer
    controls".
- Observed behavior, as measured before the fix:
  ```
  parse_spans("admin\x08\x08\x08\x08\x08guest")  -> the five backspaces survived verbatim
  parse_spans("a\x9b31mb")                        -> 0x9B (8-bit CSI) survived verbatim
  parse_spans("a\x9d0;t\x9cb")                    -> 0x9D (8-bit OSC) survived verbatim
  parse_spans("a\x1b]0;\x1b[31m\x07b")            -> re-emitted a *live* SGR from inside a
                                                      neutralized OSC title change
  ```
  The backspace payload renders as `guest` on a real terminal while the stored, copied, and
  logged text reads `admin...guest`: the bytes an auditor reads and the bytes a user sees
  disagree. `0x9B`/`0x9D` are the CSI/OSC introducers in 8-bit mode, so real cursor-movement
  and screen-erase sequences bypassed a sanitizer that only checked the 7-bit `ESC [`/`ESC ]`
  forms.
- Root cause: the sanitizer treated "control" as a synonym for "escape sequence". The tests
  written with it exercised only escape sequences, so all four cases passed.
- Fix: every C0 byte except newline and tab, `DEL`, and the whole `0x80`-`0x9F` C1 range is
  neutralized to a `<0xNN>` marker at both leaves; OSC/DCS/SOS/PM/APC bodies are consumed to
  their terminator so nothing inside one is rescanned; `Span.__post_init__` refuses any raw
  control byte, so the invariant holds for a hand-built span too.
- Regression introduced and caught while fixing: the byte-level pass initially read UTF-8
  continuation bytes as C1 controls, corrupting `Ā` (`0xC4 0x80`) and `日` (`0xE6 0x97 0xA5`).
  `_utf8_sequence_length` now validates and skips whole sequences, with
  `test_the_byte_neutralizer_does_not_corrupt_utf8` and
  `test_a_truncated_utf8_sequence_does_not_swallow_what_follows` pinning both directions.
- Tests: `tests/unit/presentation/test_ansi_and_cells.py` -
  `test_bare_c0_controls_are_neutralized`,
  `test_eight_bit_c1_controls_do_not_bypass_the_sanitizer`,
  `test_a_span_cannot_be_built_around_a_raw_control_byte`,
  `TestTheTwoNeutralizersAgree`. Fixture:
  `tests/fixtures/legacy_coverage/producer_color_survives_unsafe_controls_do_not/`, generated
  by `tools/write_sanitization_fixture.py` so the control bytes are exact rather than
  hand-typed into JSON.
- Owner: impl-05 (step 05 implementor)
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP05-005: the SSE-wiring fix (BLOCKER-STEP05-004's B1) introduced a single-connection DoS

- Timestamps: 2026-08-06T00:00:00Z opened, 2026-08-06T00:00:00Z resolved
- Status: `resolved` (fixed in step 05; recorded because it was a security/availability
  defect introduced mid-step and caught by independent review, not by the implementor)
- Affected: step 05 (streaming-and-rendering), §38.22 SSE plane
- Design references: §38.22 (durable feed/live-frame delivery); the design does not
  specify a concurrency model, but implicitly requires the read plane to remain
  available to other clients while any one SSE connection is open
- Observed: after wiring the previously-inert SSE plane, `entrypoints/http/server.py`
  served connections serially on one thread, and the SSE frame-emission loop was an
  unbounded busy-spin with no sleep in the production (`max_frames=None`) path.
  Independent review measured: one open SSE stream pegged a full CPU core (3.013s
  CPU over a 3s window); a second client got zero bytes for the full duration
  (starved); recovery only occurred ~12s after the holder disconnected (bounded by
  the 15s heartbeat interval); exactly one concurrent SSE connection was possible.
  Root cause of why 2546 passing tests missed it: every SSE test passed an explicit
  `max_frames=N`, the only thing that terminated the loop; nothing exercised the real
  unbounded production path or opened a real socket with two concurrent connections.
- Expected: multiple concurrent SSE connections; an open feed must not block ordinary
  reads; a hung-up client must be detected promptly, not only at the next heartbeat.
- Impact: a trivial local denial-of-service of the entire read plane by any single
  client opening an SSE endpoint and doing nothing.
- Fix: `src/baqylau/entrypoints/http/server.py` — thread-per-connection dispatch with
  a bounded `MAX_CONCURRENT_CONNECTIONS=64` (honest `503`/`overloaded` on overflow,
  not silent unbounded thread spawning); `feeds.py`/`sse_routes.py` — `SubscriberQueue`
  now blocks on a `threading.Condition` with a timeout instead of spinning; `sse.py` —
  an `IDLE_TICK` zero-byte sentinel lets an idle stream hand control back without
  putting bytes on the wire; `server.py` — prompt peer-hangup detection via
  `MSG_PEEK|MSG_DONTWAIT`; subscriber unregistration on every stream-exit path via
  `finally`. Three additional thread-safety races the fix itself exposed were found
  and fixed in the same pass: unsynchronized diagnostic counters, an unlocked
  `LiveFacetService` generation-mint race (verified under 32-thread/12,800-mint
  contention: zero duplicates after the fix), and `stop()` not joining connection
  threads before socket teardown.
- Verification: independent review re-ran the original measurements against the fix
  and confirmed full inversion — CPU 3.013s→0.001s, starvation 0 bytes→200 in <2s,
  outage 12s→0s, concurrency 1→64-bounded-with-503. New test suite
  `tests/contract/http/test_stream_concurrency.py` (4 tests) drives real unbounded
  streams over a real socket (no `max_frames`) and was verified to fail against the
  pre-fix commit and pass against the fix.
- Owner: impl-05b (continuation implementor); verified by review1-05
- Last updated: 2026-08-06T00:00:00Z

## BLOCKER-STEP06-001: §19.1's `attention_transitions.cause` prose tuple conflicts with §38.27's DDL column `cause_operation_id`

- Timestamps: 2026-08-06T00:00:00Z opened, 2026-08-06T00:00:00Z resolved
- Status: `resolved` (dedicated resolver confirmed prose is a truncated sketch; DDL is
  authoritative per §43.4.4; no schema change, migration, or digest regeneration required)
- Affected: step 06 (projections-and-machine-services), attention/notification services
- Design references:
  - §19.1 (design lines ~2579-2581) prints `attention_transitions(..., cause, ...)` and
    `attention_projection(scope_type, scope_id, state, source_revision, ...)` — the
    latter literally ends in `...` and both tuples omit real columns present in the
    executable DDL (e.g. `source_revision` is omitted from the transitions tuple despite
    being a real DDL column).
  - §38.27/§40.7's executable DDL for `attention_transitions` (design lines ~9631-9642,
    inside the §38.35 DDL unit #1, not §38.27 as originally miscited) has no `cause` text
    column — only a nullable `cause_operation_id` FK to `operations`.
  - §41.4 (design line ~13950) requires "AttentionService... writes a reason string for
    every probe/timer transition" — a requirement that cannot be satisfied by an FK alone,
    since most probe/timer transitions have no associated Operation row.
  - §43.4.4 (design lines ~14150-14153): "the generated schema artifact and its SHA-256
    digest are authoritative. Prose... is regenerated from that artifact and cannot
    override it." Matches the precedent set by the frame-header-length resolution
    (BLOCKER-STEP02-001/§43.4 item 1).
- Observed: attempting to write a `cause` column via 3 of 285 declared statements failed
  with `OperationalError: table attention_transitions has no column named cause` against
  the installed, digest-sealed schema.
- Required decision: whether to (a) amend the DDL to add a real `cause TEXT` column
  (schema/digest/migration change) or (b) treat §19.1's tuple as an imprecise sketch and
  route the reason string through existing durable homes.
- Resolution: (b). No DTO or event payload anywhere in §38.36-38.38 surfaces an attention
  reason (`ConversationDTO.attention` is a bare state token); the reason now lives on the
  in-memory `AttentionVerdict` plus routes through four existing durable homes depending
  on transition-trigger type: `notification_intents.cause` (alert-arming transitions),
  `ingestion_decisions.decision_code` under `consumer_kind='attention'` (observation-driven
  transitions, using §38.5's registered decision-code vocabulary), the existing
  `attention_transitions.cause_operation_id`/`attention_projection.source_transition_id`
  (Operation-caused transitions), and `notification_intents.cancel_reason` against the
  seeded registry (alert cancellation). Pure timer/probe transitions with no registered
  consumer are log-only, which is the documented residual case, not a narrowing.
- Evidence: `cause` removed from the 3 failing statements; all 285 now EXPLAIN against
  the installed schema. `TransitionTrigger`→`ReasonHome` routing implemented in
  `application/machine/attention.py`, reusing `ConsumerKind.ATTENTION` and the existing
  `DecisionCode` enum rather than parallel vocabularies. Guards added:
  `test_the_installed_table_has_no_cause_column`,
  `test_no_declared_statement_writes_a_cause_column`,
  `TestTheSeededCancelReasonGuardMatchesTheSchema` — so this cannot be mistaken for a
  lost column later.
- Update (2026-08-07): review found only 2 of the 4 durable homes had a real
  executor at first (`notification_intents.insert` and its `cancel_reason` update
  had none, because the alert plane's SQLite writers did not yet exist). All four
  homes are reachable as of commit `7faef22`: `AlertPolicyService`
  (`application/notifications/delivery.py`) now arms intents through
  `SqliteAlertStore`, so both notification homes have real callers.
  `tests/contract/storage/test_machine_stores.py::TestTheFacetAndAlertWritersHaveRealCallers`
  reads an armed intent back from real storage. `tests/architecture/test_storage_matrix.py`
  additionally enforces that every declared statement has an executor or a recorded
  reason, and that every store class executing SQL is actually constructed somewhere
  (a reachability check, not just AST-scanning for `.execute()` syntax).
- Owner: impl-06; resolved by resolver-06-1
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP06-002: §29's printed `runtime/` file list has no home for `PresenceExpiryWorker`/`TerminalFrontmostPoller`

- Timestamps: 2026-08-06T00:00:00Z opened, 2026-08-06T00:00:00Z resolved
- Status: `resolved` (dedicated resolver confirmed a new file is the correct, non-ADR-triggering
  addition; implemented and self-guarded)
- Affected: step 06 (projections-and-machine-services), runtime worker layout
- Design references:
  - §29 (design lines ~3863-3881) prints an exact 18-file `runtime/` list containing
    neither worker, and states (lines ~3936-3938) "this layout is required, not
    illustrative. New top-level source directories require an architecture decision
    that updates this section."
  - §38.26 (design lines ~6995-6999) names both `PresenceExpiryWorker` and
    `TerminalFrontmostPoller` as runtime tasks but assigns no file/module ownership.
  - §38.10 (design lines ~5434-5446) gives `TerminalFrontmostPoller`'s full behavioral
    spec (1s/5s cadence, reserved `terminal` device, verified tab-focus fact,
    `unknown` on enumeration failure, no HTTP-callable trusted write path).
- Observed: every other §38.26 runtime task maps unambiguously onto one of §29's 18
  printed files; these two do not, and folding them into an existing file
  (`projection_workers.py`/`observation_workers.py`) would misstate ownership since
  neither produces presence truth.
- Required decision: add a new file to `runtime/`, fold into an existing file, or
  confirm an existing file was simply misread as not fitting.
- Resolution: add `src/baqylau/runtime/presence_workers.py` owning both workers. §29's
  escape-valve clause gates only NEW TOP-LEVEL DIRECTORIES, not new files inside an
  already-approved package, so this addition does not trip the formal ADR gate — but
  it is still a real, recorded deviation from a list the design calls "required."
- Evidence: `tests/architecture/test_package_tree.py`'s
  `RUNTIME_FILES_ADDED_BEYOND_SECTION_29` records the file with its citation, and
  `test_no_undocumented_runtime_file_was_added` fails on any OTHER unlisted runtime
  file, so the precedent cannot be silently extended. Verified non-vacuous (a planted
  unlisted file fails the guard by name).
- Owner: impl-06; resolved by resolver-06-2
- Last updated: 2026-08-06T00:00:00Z

## BLOCKER-STEP07-001: outbox row cannot durably store principal/credential/policy/authorization revision per §38.36; §17.2's "unknown" outcome not in the outbox.state CHECK

- Timestamps: 2026-08-06T00:00:00Z opened, 2026-08-06T00:00:00Z resolved
- Status: `resolved` (dedicated resolver confirmed both readings; no schema change needed)
- Affected: step 07 (controls-and-effects), outbox/effect-attempt machinery
- Design references:
  - §38.36 (design lines ~10323-10327): "Each outbox row stores principal, credential,
    required policy, and authorization revision" vs the closed `outbox` DDL (§38.35,
    lines ~9590-9611, 15 columns) which has none of these as dedicated columns.
  - §17.2 (design lines ~2267-2291) never actually uses the word "unknown" — its
    outcome enum is `succeeded | failed_before_action | rejected | indeterminate`.
    The real "unknown" language is §38.26 (lines ~7021-7025): after ten inconclusive
    reconciliation attempts an effect is left "`unknown` with manual guidance," which
    is a described terminal condition, not an enumerated `outbox.state` value.
  - Separately identified during resolution: `rejected` appears in §17.2's enum and
    in AttemptDTO's wire enum but is absent from the `effect_attempts.state` CHECK.
- Resolution: (1) auth context is carried in the sealed, immutable payload Blob the
  outbox row already references (NOT NULL REFERENCES blob_objects ON DELETE RESTRICT),
  since the digest-pinned schema cannot be amended unilaterally and the design itself
  uses both "real column" and "opaque payload carrier" patterns elsewhere for similar
  needs. (2) §38.26's "unknown" is represented as terminal `indeterminate` plus a
  `health_errors` row carrying the manual-guidance detail, with a claim-predicate count
  bound (ten attempts) reproducing §38.26's own threshold exactly. (3) `rejected` is
  correctly mapped to `failed_before_action` in storage (the CHECK has no `rejected`
  value) with a recovery function (`attempt_wire_outcome`) that reconstructs the wire
  outcome from the attempt's receipt/outbox-state so the distinction isn't lost on
  read-back.
- Evidence: `SqliteEffectPlane.cancel_safe_pending_effects` implements §38.36's
  "cancels safe pending effects" sweep (found to be entirely missing on first
  self-review, then built); `attempt_wire_outcome` in
  `application/effects/service.py` recovers `rejected` distinctly from a transient
  `failed_before_action`, with `reconciled_failed` deliberately never promoted to
  `rejected` (a probe cannot distinguish a refusal from a never-landed attempt) and
  unregistered receipt words ignored rather than corrupting the closed wire enum.
- Owner: impl-07; resolved by resolver-07-1
- Last updated: 2026-08-06T00:00:00Z

## BLOCKER-STEP07-002 (non-blocking, tracked): outbox.finish's lease-expiry guard is currently vacuous

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `open`, non-blocking (safe today — no runtime lease reaper exists; fails in
  the safe direction; hardening added for a future reaper is currently ineffective)
- Affected: step 07 (controls-and-effects), outbox lease/settle machinery
- Observed: `outbox.finish`'s `lease_expires_at > :now` guard is structurally always
  true because `OutboxEffectDispatcher.run_once` captures one `now` at pass start and
  passes that same value to both `claim_batch` (writes `lease_expires_at = now + 30`)
  and `settle` — so the comparison is always `now + 30 > now` regardless of real
  elapsed execution time. Reviewer proved it: claimed a batch with `now` 600s in the
  past, settled with the same stale `now`, guard did not fire.
- Impact: none today (no runtime lease reaper calls `recover_expired_leases` outside
  startup, which is correctly ordered after orphan detection). Gives false confidence
  to whoever adds a runtime reaper later; `outbox.recover_expired_leases` itself still
  has no attempt-ledger guard, and there is no lease-renewal mechanism, so a 100-item
  batch against a 30-second lease means the lease does not actually bound the holding
  period in practice.
- Required decision: read the real clock inside `settle`/`begin_attempt` instead of
  reusing the pass-start `now`, or add lease renewal before each row — either makes
  the guard real. Small, non-blocking fix.
- Owner: impl-07; identified by review1-07
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP08-001: §36.2's registered-build gate has no design-specified channel for the provider's *running* build

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `open`, non-blocking (implemented with a three-valued verdict and a measured
  discovery mechanism; the narrow decision below is still owed)
- Affected: step 08 (providers-backends-and-accounts), coverage rows for §36.2 and §38.37.9;
  branch `step-08-providers-backends-accounts` in
  /Users/z.yermagambet/code/personal/baqylau2
- Design references:
  - §36.2 (design lines ~4451-4458): "An unregistered build is fail-closed for `answerable`
    and `delegating` families: no command rewrite, launch, or control is attempted.
    Observational families fail open to a typed generic record with
    `provenance=unverified_build` ... This distinction is a closed manifest rule, not an
    implementation choice."
  - §38.37.9 (design line ~10975): "Provider version discovery is a measured implementation
    input ... Until captured, that adapter version is unsupported."
  - §10.3 (lines 1605-1640): the environment allowlist contains no version key for any
    provider.
  - §38.37.6's status-line row: "exact session/account plus usage windows" - no version.
- Observed behavior: the rule needs to compare the build the provider is *running* against
  the set of builds the repository has captured fixtures for, and no design-named field
  carries the former.
  ```
  $ python3 -c "…read phase0/fixtures/provider_records/claude_hook_payloads/input/
      one_payload_per_family.json and print each family's payload keys…"
  # 24 families, zero version/build fields in any payload
  $ cd ../baqylau && grep -n "version" plugins/claude_code/statusline.py
  35:    has sent this as either seconds or milliseconds across versions; >1e12 is
  # the legacy status-line consumer reads no version field either
  ```
  Codex rollouts do carry `session_meta.cli_version`, but that is the version that wrote a
  possibly old file - the frozen June rollout records `0.136.0` while the capture build was
  `0.146.0` - so it cannot answer §36.2's question.
- Expected behavior: a named, observed source for the running provider build, or an explicit
  statement that the daemon discovers it itself.
- Impact: does not block. It does mean §36.2's fail-closed half can only fire when discovery
  succeeded, and it forced a third registration state the design does not name.
- Attempted alternatives:
  1. Read the build from the hook payload. Rejected on measurement: no family carries one.
  2. Read it from `session_meta.cli_version` for Codex. Rejected: wrong question (see above),
     and there is no equivalent for Claude at all.
  3. Treat "not discovered" as "unregistered". Rejected: it would disable foreground capture
     and every control on any machine where a `--version` probe failed, on the strength of the
     daemon's own ignorance rather than evidence about the provider - and §38.37.1's
     typed-absence rule forbids collapsing a fact we failed to look up into one we measured.
- Decision taken (documented, not approved): the daemon discovers the build with a
  `<binary> --version` shell-out - the same mechanism `phase0/tools/capture_fixtures.py`
  already uses to produce the `provider_builds` values the adapters' `SUPPORTED_BUILDS` are
  asserted against - refreshed by the §38.26 `ProviderEdgeVerifier` at startup and every ten
  minutes. `Registration` is three-valued: `registered` / `unregistered` / `undiscovered`.
  Only `unregistered` triggers §36.2's rule; `undiscovered` proceeds with provider-edge health
  degraded and no fixture-registration task, because §36.2's task means "capture fixtures for
  build X" and there is no X yet.
  Code: `src/baqylau/application/provider_edges/manager.py` (`Registration`, `gate_family`,
  `ProviderBuildGate`), `src/baqylau/adapters/providers/version_probe.py`.
  Tests: `tests/unit/application/test_step08_build_gate.py` asserts the whole three-by-three
  table, including reading the rule out of
  `phase0/fixtures/negative/unknown_provider_build/input/contract.json` rather than retyping it.
- Narrowest decision needed: whether the running provider build is (a) a daemon-performed
  version discovery, as implemented, or (b) a field the edge must start reporting - in which
  case §10.3's key table or the status-line row needs it added. Nothing about the gate's
  outcomes changes either way.
- Autonomous next action: none; the gate is implemented and wired at both call sites.
- Retry condition: re-run `tests/unit/application/test_step08_build_gate.py` if a version
  channel is added; the `undiscovered` branch should become unreachable in production.
- Owner: impl-08 (step 08 implementor)
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP08-002: two planning artifacts disagree about who owns backends and the remote protocol

- Timestamps: 2026-08-07T00:00:00Z opened; 2026-08-07T08:30:00Z resolved
- Status: `resolved`
- Affected: steps 08 and 09; `src/baqylau/application/ports/backends.py`,
  `src/baqylau/application/ports/relay.py`, `src/baqylau/runtime/remote_connection_workers.py`
- References:
  - `rewrite/index.md` line 105: "Step 08 owns remote backend/provider protocol adapters;
    Step 09 owns only future-feature contract rows and plugin/relay/collaboration workflows."
  - `rewrite/08-providers-backends-and-accounts/task.md`: "local/remote backends, execution
    targets, connected-only remote protocol, mTLS, liveness, file transfer, no replay".
  - `rewrite/09-handover-collaboration-and-future-features/task.md`: "public links/deep links,
    remote connected-only backend, mTLS, certificate rotation/revocation, capabilities, and file
    transfer" - i.e. step 09's own bundle also claimed the same three nouns.
  - `src/baqylau/application/ports/backends.py:19` and
    `src/baqylau/runtime/remote_connection_workers.py:21`: `IMPLEMENTED_BY_STEP: Final = "09"`.
- **Correction to this entry's original premise.** As first filed, this blocker claimed "The
  placeholders were written by step 02, before `index.md`'s ownership note." That is false, and
  the correction matters because it changes what kind of artifact the marker was. Measured:
  ```
  $ cd baqylau && git log --format="%h %ci" -S "Step 08 owns remote backend" -- rewrite/index.md
  8c67811 2026-08-06 01:27:45 +0800
  $ cd baqylau2 && git log --format="%h %ci %s" -S 'IMPLEMENTED_BY_STEP: Final = "09"' \
      -- src/baqylau/application/ports/backends.py
  591f4f5 2026-08-06 08:58:55 +0800  Phase 1 step 02: supervised daemon and machine-wide ...
  ```
  The ownership boundary landed **7 hours 31 minutes before** the placeholder. So the `"09"` was
  a wrong guess by an implementor who - by `index.md`'s own rule that implementors are not told
  step numbers - had no way to know, not a stale pre-arbitration artifact. Same resolution
  either way; recorded so the false premise is not repeated.
- Resolution (orchestrator, 2026-08-07): **step 08 owns it.** `index.md` line 105 is the
  arbitration layer and step 08's own bundle independently agrees; step 09's overlapping bullet
  is a loose sketch of what a later slice touches rather than an assignment - it also re-claims
  public/deep links, which steps 06/07 already own.
  - Step 08 builds: all of §38.33's online remote backend protocol, plus §22's
    Backend/ExecutionTarget services with the probe/start/read/write/control separation.
  - **Certificate revocation checking is step 08's**, not step 09's: §38.36 requires it on
    connection and on every request, with long-lived connections rechecking at least every 15
    seconds, and that check lives inside the transport. Step 09 gets the rotation *operation*
    only.
  - The CA/serial/validity model had to be **overlap-capable from the start** (two concurrently
    valid certificates per backend), so step 09's rotate operation is additive rather than a
    migration landing on a mid-rotation connection.
  - `ports/relay.py` stays at `"09"`: relay is explicitly step 09's.
- Delivered on branch `step-08-providers-backends-accounts`, commit `63ac918`:
  `application/backends/service.py`, `application/backends/remote_protocol.py`, and
  `runtime/remote_connection_workers.py` as a real §38.26 liveness worker. Both stale markers
  now read `"08"`. Tests: `tests/unit/application/test_step08_backends.py` (118) and the
  remote-disconnect half of `tests/integration/test_step08_recovery_scenarios.py`.
- **One factual correction to the ruling itself**, recorded because acting on it as written would
  have deleted correct code. The ruling stated that "SPIFFE appears nowhere in the design" and
  that an identity scheme should not be invented. Measured:
  ```
  $ grep -in "spiffe" docs/rewrite-design-v4-codex.md
  10300:   `spiffe://baqylau/<role>/<principal-uuid>` where role is `edge`, `terminal`,
  $ grep -in "pinned controller" docs/rewrite-design-v4-codex.md
  8723:Controller and agent use mutual-TLS WebSocket with pinned controller/agent
  ```
  Both appear, once each, and they are complementary rather than alternative: §38.36 mechanism 5
  fixes the SAN grammar and says "remote-agent certificates can use the remote protocol only",
  while §38.33 additionally requires pinning. Pinning answers "is this the exact configured
  certificate"; the SAN answers "which principal and role does it claim". Pinning alone would let
  a pinned *edge* certificate drive the remote protocol, which mechanism 5 forbids. Both are
  therefore implemented, which satisfies the ruling's intent (follow §38.33's pinning; invent no
  identity scheme) without removing a §38.36 requirement.
- Owner: impl-08b; resolution owner: orchestrator (ruled)
- Last updated: 2026-08-07T09:00:00Z

## BLOCKER-STEP08-003: step 08 is delivered partially, and the shortfall is a scope narrowing rather than a design problem

- Timestamps: 2026-08-07T00:00:00Z opened
- Status: `needs-user-decision`
- Affected: step 08 (providers-backends-and-accounts), branch
  `step-08-providers-backends-and-accounts` in /Users/z.yermagambet/code/personal/baqylau2
- Observed: the step-08 bundle covers three provider integrations plus backend, account,
  credential, and authentication infrastructure. This branch delivers §36.2's build gate wired
  at both real call sites, §38.4's installation/trust/health machinery and its §38.26 verifier,
  §10.3's key-table move into the adapters, §41.1's measured input-field table, and §41.2's
  Codex parse/admission/boundary/discovery rules driven by the six frozen Phase 0 rollouts.
  It does not deliver: Codex/OpenCode `ObservationDecoder`s, `AccountLaunchService` (§41.3),
  the pricing/effort ladders (§41.3), §41.1's child metadata reader, local/remote backends and
  §38.33's protocol, §38.36's authentication model, §38.37.9's 38 named fixtures, or the
  account-migration and remote-disconnect recovery scenarios.
- Why this is recorded here: BLOCKER-STEP05-003's process note established that a scope
  narrowing must be stopped-and-reported rather than decided. It was reported to the
  orchestrator while the work was still in progress, and it is recorded here so the shortfall
  is on the record independently of that message.
- Enforcement: the shortfall is a committed artifact, not prose.
  `tests/architecture/test_step08_reachability.py` holds `DEFERRED_COMPONENTS` (exists,
  tested, no production consumer - checked in both directions) and `NOT_DELIVERED` (absent
  entirely - each entry asserted *absent*, so it cannot quietly describe something that later
  lands), with a design citation required on every entry.
- Narrowest decision needed: whether the remaining items become a step-08 continuation or a
  separate step, and whether the completion gate is re-run against the whole bundle before
  step 09 starts.
- Owner: impl-08; resolution owner: orchestrator
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP08-003 update (2026-08-07): items 1, 2, and 4 delivered on the orchestrator's ruling

- The orchestrator chose option (b) from the capacity report: finish the "required provider
  rows" half - Codex/OpenCode `ObservationDecoder`s, `AccountLaunchService`, and §41.1's child
  metadata reader - and hand backends/auth/fixtures/recovery-tests to a continuation implementor.
- Delivered in commit on branch `step-08-providers-backends-accounts`:
  `adapters/providers/codex/decoder.py`, `adapters/providers/opencode/decoder.py`,
  `adapters/providers/claude_code/child_metadata.py`, `application/machine/launch.py`. All three
  bundled providers now satisfy §10.5's "an ObservationDecoder or HistoryReader" and are
  registered with `IngestionService`, so the ingestion fan-out is no longer Claude-only.
- Still not delivered, and now the *whole* of `NOT_DELIVERED` in
  `tests/architecture/test_step08_reachability.py`: `application/backends/service.py`,
  `application/backends/remote_protocol.py` (§38.33), `application/auth/{service,sessions,
  certificates}.py` (§38.36), `application/machine/discovery.py` (§29), §41.3's pricing/effort
  ladder, §38.37.9's 38 named fixtures, and the account-migration/remote-disconnect recovery
  tests (§30.6). Each entry is asserted *absent* by a test, so none can be silently claimed as
  done.
- One process note worth recording: the reachability metric in that artifact was wrong twice in
  the direction that understated delivered work, and both times a test caught it rather than a
  reviewer. First it reported the decoders as unreached because the composition root builds them
  into a local dict before handing it over; then, after that was fixed, it reported them
  unreached again because a formatting pass turned the dict assignment into an annotated one and
  the fixed point only walked `ast.Assign`. Recorded because "the metric changed its answer
  because of a `black` run" is exactly the kind of measurement this project has been burned by.
- Owner: impl-08; resolution owner: orchestrator (continuation implementor for the remainder)
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP08-003 update (2026-08-07, impl-08b continuation): all but the fixture corpus delivered

- The continuation implementor picked up the remainder after the orchestrator's option-(b) ruling
  and, following the BLOCKER-STEP08-002 ruling above, the expanded backends/remote-protocol scope.
- Delivered on branch `step-08-providers-backends-accounts`, commits `00a219e`, `96fd731`,
  `a34e293`, `63ac918`:
  - `application/auth/{service,sessions,certificates}.py` plus
    `adapters/storage/sqlite/stores/{auth,auth_plane}.py` and 22 declared statements - all six
    §38.36 mechanisms, HMAC-SHA-256 credential pepper with §38.36's 24-hour rotation overlap.
  - `application/backends/{service,remote_protocol}.py` and
    `runtime/remote_connection_workers.py` (§22, §38.33).
  - `application/machine/model_resolution.py` - §41.3's price prefix resolution and the
    effort/context-window ladders, consumed by `usage/queries.py`'s `price_table` and
    `AccountLaunchService.plan_launch`.
  - `application/machine/discovery.py` - §38.37.5's placement/order/collision rules. Disclosed
    unreached, with the reason measured rather than asserted.
  - `tests/integration/test_step08_recovery_scenarios.py` - real fork+`SIGKILL` recovery for
    account migration, credentials, launch, and remote disconnect.
- **Two step-07 disclosures closed as a consequence.** `test_step07_reachability.py`'s
  `DEFERRED_ENTRY_POINTS` went from three entries to one: `principal_now` closed because
  `bootstrap._local_owner_principal` is gone and the pre-effect recheck reads a real `principals`
  row, and `check_csrf` closed because a browser session can now exist. `revoke_principal`
  remains, but its reason is rewritten - it now performs four of §38.36's five consequences and
  only its *caller* is missing.
- **A production defect the recovery work found.** `SqliteMigrationSagaPlane.persist` opened a
  machine-scope write while both `account_migration_details` and `account_migration_checkpoints`
  are declared `conversation_write` in the storage matrix, so `_require_boundary` refused the very
  first `append_checkpoint_in` and the §21.4 migration runner could not persist a single
  checkpoint. Invisible because nothing in this build creates a migration saga, so `claim_batch`
  always returned empty. Fixed in `a34e293`. Filed separately as BLOCKER-STEP08-004.
- **Still not delivered: §38.37.9's 38 named provider fixtures**, and this is now the *whole* of
  `NOT_DELIVERED` in `tests/architecture/test_step08_reachability.py`. The blocking fact is
  measured, not an excuse:
  ```
  $ cat phase0/fixtures/provider_records/opencode_state/manifest.json
    authenticity = "derived"            # not "captured"
    description  = "OpenCode SQLite schema and non-secret configuration."
    uncertainty  = "...A static capture cannot show a reset, so the reset fixture stays synthetic."
  $ ls phase0/fixtures/provider_records/opencode_state/input/
    opencode.db.schema.json  opencode-next.db.schema.json     # schema only
  $ python -c "print(sorted(json.load(open(...))))"
    ['object_count', 'objects', 'row_counts_omitted']
  ```
  Phase 0 captured **no OpenCode records at all**, so 8 of the 38 cannot be built from real frozen
  data. Claude (6 real transcripts, 24 real hook families) and Codex (6 real rollouts plus
  history/session_index) do have captures, so 30 of the 38 are buildable today.
  Narrowest decision needed: either a new Phase 0 capture run against a live OpenCode, or an
  explicit ruling that the 8 OpenCode fixtures may be synthetic with `authenticity` recorded as
  such. §38.37.9 calls provider capture "a measured implementation input" that "cannot be
  truthfully invented in an architecture document", which is why an implementor did not decide it.
- Owner: impl-08b; resolution owner: orchestrator
- Last updated: 2026-08-07T09:15:00Z

## BLOCKER-STEP08-004: the account-migration saga runner could never persist a checkpoint

- Timestamps: 2026-08-07T08:45:00Z opened and fixed
- Status: `resolved` (fix landed); recorded because the *class* of bug is a process finding
- Affected: `src/baqylau/adapters/storage/sqlite/stores/effect_plane.py`
  (`SqliteMigrationSagaPlane.persist`); step 07 wrote it, step 08's completion gate found it
- Design references:
  - storage matrix: `account_migration_details` and `account_migration_checkpoints` are both
    `conversation_write` (§21.4; §38.18 continuation).
  - §38.37.1 fixes the `MachineWrite.owner` set and the conversation/machine scope split.
- Observed: `persist` opened `machine_write(owner="account")`. `_require_boundary` refused the
  first `append_checkpoint_in` with
  `ScopeMismatch: account_migration_checkpoints.insert: expected scope conversation_write, got
  machine`. The §21.4 runner therefore could not advance a saga at all.
- Why it went unnoticed: nothing in this build creates a migration saga, so
  `SqliteMigrationSagaPlane.claim_batch` always returned an empty tuple and `persist` was never
  reached. Every existing test passed. It surfaced only when the step-08 recovery scenario seeded
  a real saga - real AgentSession through the real inbox, real selection-evidence Blob, real
  Operation, satisfying all three RESTRICT foreign keys.
- Fix: open the scope the statements themselves declare, resolving the Conversation from the
  saga's AgentSession (`account_migration_details` has no `conversation_id` column; §38.35 keys it
  by `operation_id` and reaches the Conversation through `agent_session_id`).
- Process note worth keeping: a worker that *cannot run*, in a build where nothing invokes it, is
  invisible to every gate this project currently runs. The same mismatch may exist in the
  handover, backup, and restore saga reducers; it is unchecked because §31 phases 7-8 have not
  written them.
- Owner: impl-08b
- Last updated: 2026-08-07T09:15:00Z

## BLOCKER-STEP08-005: §38.36's prose tables disagree with §38.35's authoritative DDL in three places

- Timestamps: 2026-08-07T06:00:00Z opened
- Status: `open`, non-blocking (all three resolved in favour of the DDL and documented in code)
- Affected: `src/baqylau/application/auth/service.py`, `.../certificates.py`,
  `adapters/storage/sqlite/stores/auth.py`
- Observed, with §38.35's DDL as the authoritative side (it is the transcription in `schema.sql`
  that every generated artifact is checked against, and §43.2 makes the digests generated outputs
  over it):
  1. §38.36 lists `principals(... authorization_revision ...)` and its `PrincipalDTO` prints
     `state:enum(active,suspended,revoked)`. The DDL has **no** `authorization_revision` column
     and its CHECK is `('active','disabled','revoked')`. Resolved: `suspended` is the domain word
     and `disabled` the stored one (one mapping, `STORED_STATES`), and the revision is *derived*
     in one SQL read from the facts §38.36 itself names as revision-advancing. Adding the column
     would change the design's printed DDL and the five-unit digest.
  2. §38.36 lists `auth_credentials(... audience, scopes ...)`. The DDL has neither. Resolved:
     scopes are derived from principal kind plus machine role bindings, which additionally makes
     §38.36's "`machine.admin` never follows from collaboration" structural - the bindings table
     has no `conversation_id`.
  3. §38.36 requires "serial, validity, SPKI digest, role, and principal must match
     `auth_credentials`", and the DDL has exactly one verifier column, `secret_digest`. Resolved:
     the five-field match is one keyed HMAC over canonical material, which is stronger than five
     comparisons because no check can be skipped and no field forgotten.
- Narrowest decision needed: whether §38.36's prose tables should be amended to match §38.35, so
  the next reader does not re-derive these three resolutions. No behaviour changes either way.
- Owner: impl-08b; resolution owner: design maintainer
- Last updated: 2026-08-07T09:15:00Z

## BLOCKER-STEP08-006: §38.37.1's machine-scope owner set has no `auth` member

- Timestamps: 2026-08-07T06:15:00Z opened
- Status: `open`, non-blocking (implemented under `account` and documented)
- Affected: `src/baqylau/adapters/storage/sqlite/stores/auth.py`, `.../auth_plane.py`
- Observed: §38.37.1 prints `MachineWrite.owner` as a closed `Literal` of eight values -
  `account`, `backend`, `backup`, `collaboration`, `diagnostics`, `extension`, `maintenance`,
  `provider_edge` - and authentication is not among them, while §38.36 requires its tables in the
  same SQLite database. "A workflow without a listed owner has no machine scope", so the choice
  was one of the eight or none.
- Decision taken (documented, not approved): `account`, because it is the owner whose subject
  matter is the identity a credential belongs to; §40.5 pairs privileged local features with
  account cutover, and `accounts` already shares the `identity_and_security_evidence` retention
  class the matrix assigns to every §38.36 table. Recorded as `AUTH_SCOPE_OWNER` in one place so a
  future `auth` owner is a one-line change.
- Narrowest decision needed: add `auth` to §38.37.1's owner set, or ratify `account`.
- Owner: impl-08b; resolution owner: design maintainer
- Last updated: 2026-08-07T09:15:00Z

## BLOCKER-STEP08-007: the `__Host-` cookie prefix and §38.36's loopback-development profile are incompatible

- Timestamps: 2026-08-07T06:20:00Z opened
- Status: `open`, non-blocking (resolved in favour of keeping `Secure` semantics)
- Affected: `src/baqylau/application/auth/sessions.py`
- Observed: §38.36 mechanism 2 requires the cookie `__Host-baqylau_session` and says it "is
  `Secure; HttpOnly; SameSite=Strict; Path=/` **outside the declared loopback-development
  profile**". RFC 6265bis §4.1.3.1 makes the `__Host-` prefix *require* `Secure` - a browser
  rejects such a cookie without it - so a profile that drops `Secure`, as §38.36 permits, cannot
  keep the prefix.
- Decision taken: the development profile drops the prefix along with the attribute, keeping
  `HttpOnly; SameSite=Strict; Path=/`. Dropping the prefix is the lesser loss: `Secure` keeps the
  cookie off a plaintext hop, while the prefix only stops a sibling subdomain shadowing the name,
  and a loopback development origin has no siblings. Production is verbatim §38.36.
- Narrowest decision needed: confirm, or state that the development profile keeps `Secure` (which
  is in fact workable, since browsers treat `http://localhost` as a secure context) and therefore
  keeps the prefix too.
- Owner: impl-08b; resolution owner: design maintainer
- Last updated: 2026-08-07T09:15:00Z

## BLOCKER-STEP08-008: §38.37.9's 38 provider fixtures cannot all be built from real Phase 0 captures

- Timestamps: 2026-08-07T00:00:00Z opened, 2026-08-07T00:00:00Z resolved
- Status: `resolved` (orchestrator ruling: label the uncapturable fixtures honestly
  synthetic using this project's own established authenticity vocabulary, rather
  than blocking or inventing fake-real data)
- Affected: step 08 (providers-backends-and-accounts), §38.37.9's provider fixture
  corpus at tests/fixtures/providers/
- Design references: §38.37.9 requires provider capture as "a measured
  implementation input" that "cannot be truthfully invented in an architecture
  document," and states "until captured, that adapter version is unsupported."
- Observed: Phase 0 (step 01) captured no OpenCode records at all —
  phase0/fixtures/provider_records/opencode_state/manifest.json records
  authenticity="derived" (not "captured") and its own uncertainty field already
  concedes "a static capture cannot show a reset, so the reset fixture stays
  synthetic." Separately, 5 non-OpenCode fixtures (a deleted transcript, a deleted
  remote thread, a child meta.json Phase 0 never froze, a cwd-adoption note written
  by the legacy host into its own state directory, and an app-server-reconnect
  scenario needing a live socket) are uncapturable by construction — there is no
  real artifact to freeze, not a Phase 0 gap.
- Measured final split (verified by impl-08b, re-checked against Phase 0's actual
  manifests): 38 fixtures total — claude_code 18 (12 captured, 3 derived,
  3 synthetic), codex 12 (6 captured, 4 derived, 2 synthetic), opencode 8
  (8 synthetic). 25 fixtures are built from real captures, 13 are honestly labeled
  synthetic with a specific reason each.
- Required decision: (a) run a new Phase 0 capture against a live OpenCode
  instance, (b) label the uncapturable fixtures synthetic with authenticity
  recorded honestly, or (c) declare OpenCode unsupported for fixture parity in
  this slice.
- Decision taken: (b), applied consistently to all 13 uncapturable fixtures (8
  OpenCode + 5 non-OpenCode), using Phase 0's own established `authenticity`/
  `synthetic_reason` field vocabulary (verified identical field names to
  phase0/fixtures/negative/malformed_inputs, which already documents "negative
  cases are synthesized by definition" as the same class of justification).
- Evidence: tools/generate_provider_fixtures.py + tools/provider_fixture_specs.py
  generate all 238 files byte-exactly from frozen Phase 0 captures where real data
  exists, with a `--check` mode asserted by
  `tests/contract/providers/test_step08_provider_fixture_corpus.py::test_the_committed_corpus_matches_what_the_generator_produces`
  (this repository has no CI pipeline; the check is carried by the test suite, not
  a pipeline); corpus names verified against §38.37.6-38.37.8's
  printed fixture blocks with set equality in both directions;
  `test_phase_zero_still_has_no_opencode_records` re-measures the OpenCode-absence
  justification on every run and fails (forcing the labels to be revisited) if a
  real OpenCode capture ever lands, so the synthetic label cannot silently outlive
  its justification.
- Owner: impl-08b; resolution owner: team-lead (orchestrator ruling)
- Last updated: 2026-08-07T00:00:00Z

## BLOCKER-STEP04-005: `getOperation` is declared in the read manifest but served by nothing, so its `404` carries no information

- Timestamps: 2026-08-08T00:00:00Z opened
- Status: `open` (recorded, not fixed; fixing it is step 04's read surface, not step 09's)
- Affected: step 04 (canonical read model); surfaced by step 09's manual test
  (28/28 checks passed; this was the one thing the tester flagged as pre-existing)
- Design references: §38.38 declares `getOperation` as `GET /api/v1/operations/{id}`,
  policy `CR`, application owner `OperationQueryService.get`, storage port
  `OperationStore.get`, durable owners `operations,operation_details,effect_attempts`,
  error `404 operation_not_found`. §17.3 tells a caller that a `202` returns an Operation
  id and that completion arrives through the structural feed.
- Observed: `src/baqylau/entrypoints/http/read_endpoints.json` carries the `getOperation`
  row and it is in no exclusion class, but no handler is supplied for it anywhere in
  `src/`. `HttpServer._compile` routes only the manifest rows it was handed a handler for,
  so the path is never routed and answers `404` for every id. Measured: 41 declared read
  rows, 8 served (`listConversations`, `getConversationActivity`, `getLimits`, and step
  09's five §38.39 workflow reads), 33 unserved. The gap predates step 09 —
  `read_endpoints.json` was last touched by `3201fcc`, an ancestor of step 09's base
  `97d43fc`.
- Second observation, narrower and worth fixing on its own: `HttpServer._compile`'s
  docstring claims "`tests/contract/http/test_served_read_endpoints.py` reports the gap
  instead of it being invisible." That test only asserts routed ⊆ manifest. Nothing
  asserts manifest ⊆ routed ∪ exclusions, which is the direction that would have caught
  this. The docstring describes a check that does not exist.
- Why it matters: step 09's BC-1 discloses that a machine-scoped workflow's `202` returns
  an Operation id that resolves to nothing, and names the `404` from `getOperation` as the
  observable consequence. Because `getOperation` `404`s for *every* id, that signal is
  currently indistinguishable from "this endpoint is not served at all". A future caller
  probing the endpoint could read BC-1 as fixed, or as broader than it is, and be wrong
  either way. Step 09 could not fix this without implementing another step's endpoint.
- Required decision: (a) implement `getOperation` as part of step 04's read surface,
  (b) add an explicit exclusion-class row for the unserved manifest entries so the gap is
  declared rather than silent, or (c) both — and separately, either add the
  manifest ⊆ routed ∪ exclusions assertion or correct `_compile`'s docstring so it stops
  claiming a check that is not written.
- Decision taken: none. Recorded only. Step 09 added a note to BC-1 in
  `baqylau2:docs/operations/future-feature-contracts.md` so the `404` is not misread, and
  deliberately did **not** add the missing assertion, because it would fail on 33
  pre-existing rows and turn another step's disclosed shortfall into step 09's red suite.
- Owner: implementor-09 (recorded); resolution owner: step 04 read-surface owner
- Last updated: 2026-08-08T00:00:00Z
