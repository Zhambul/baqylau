# Extension system implementation plan

Status: in_progress

Owner: Codex

Plan date: 2026-09-14

Context: Baqylau has harness and terminal contracts. It does not yet have the external extension system described here. The existing event vocabulary, feed responses, web routes, and terminal views have fixed types.

Task: Add an external extension system with typed protocols, runtime loading, data transforms, web and Kitty views, settings, cooperation, and package-owned tests. Make extensions use the same quality policy as the main repository.

Outcomes: Extensions can implement the requested adapters and Git features in separate repositories. A supported extension update needs no edit or build in the main repository. The host remains usable when an extension fails.

Verification: Complete P01–P10 and their evidence records. The active “do all tasks” goal includes the adapters and Git feature packages in P09 and P10. Implementation started with P01.

## Overall status — 2026-09-15

Approved decisions: The user approved three changes on 2026-09-15. The harness lifecycle protocol and its engine and journal integration are implemented. Size-limit admission and private-process lifecycle acceptance remain open. The shared-body journal refactor remains to be implemented. Worker resource/evidence separation is implemented and verified. These decisions are not awaiting approval. Earlier work records retain their original status as historical evidence.

The system is not ready for the requested product use. No phase is complete. There are 10 phases and 53 subtasks: 20 subtasks are in progress, two are done, and 31 are not started. These are task records, not a completion percentage.

| Phase | Status | Implemented and remaining work |
| --- | --- | --- |
| P01 — Protocols and models | in_progress | All 12 backend capability protocols, strict models, worker adapters, and peer query contracts exist. Complete host services and API freeze remain open. |
| P02 — SDK and shared checks | in_progress | Installable SDKs, shared Python policy, and shared web rules exist. P02-T02 is done. Complete templates, architecture checks, and release runners remain open. |
| P03 — Runtime and cooperation | in_progress | Discovery, fixed package copies, private workers, registry, durable state, lifecycle controls, ordinary settings APIs, and candidate settings migration exist. Related-scope settings, secrets, health policy, removal notices, and job draining remain open. |
| P04 — Event processing | in_progress | Storage, sources, mixed interpretation, and core consumer progress are connected. Selected raw and canonical transforms use complete checked journals. New journals store each body once and refer to it from ordered steps. The raw-event audit reports bounded step metadata and exact revisions through the CLI, the HTTP diagnostics route, and the SDK. Core raw changes are enabled; multi-extension alone cases, the aggregate call budget, and unclean crash recovery pass. C21 needs the P05 command and query surfaces; processing limits remain open. |
| P05 — Data and commands | in_progress | The settings part of P05-T05 and the multi-entry commit, record storage, and projector invocation of P05-T01 now work. Queries, durable commands and observers, history rebuilds, and record migrations remain open. |
| P06 — Web and settings | in_progress | Settings → Extensions has catalog, lifecycle controls, confirmation, revision checks, failure display, and shutdown evidence. Per-extension forms, independent assets, and dynamic view slots remain open. |
| P07 — Kitty | not_started | No application extension pane or block renderer exists yet. |
| P08 — E2E and release | not_started | The complete external test kit, release runner, and application conformance checks remain open. |
| P09 — Adapters | not_started | Logs, deploy, Jira, metrics, Slack, commit, and push features are not implemented. |
| P10 — Git | not_started | Repository views, staging, commit, message generation, push, and adapters cooperation are not implemented. |

### What works now

- The daemon discovers external packages without importing feature code. It retains checked package copies by digest and starts each backend in a private Python environment.
- The runtime manager restores the last committed set, prepares changes in the background, and switches at the engine boundary. Failed preparation keeps the previous active set.
- The public API provides catalog reads, rescan, runtime-state reads, operation reads, lifecycle preview, enable, disable, and reload. Required dependents need explicit confirmation. Exact request retries retain one operation across restart.
- The settings API reads defaults and accepted values. It saves or resets one declared scope with revision checks. Settings publish with a successful runtime change; failed preparation keeps the old values and worker. An enabled package uses its retained bytes, not newer source files.
- A package update can convert saved settings through an exact declared migration path. The new worker converts values before activation. Input and result are stored separately, and only a complete valid result publishes. Restart does not repeat a completed conversion.
- The observation repository checks original extension documents against retained source schemas and the committed manager/runtime. It preserves exact bytes and supplies mixed core/extension reads in arrival order. The daemon now calls sources, decoders, and selected transforms.
- The source repository saves successful read replies, original events, pending work, and resume progress in one transaction. Empty reads can advance progress. Exact retries keep the first result. Stale settings, runtimes, and checkpoints are rejected.
- The engine sets source watches before reads and retains one runtime through source work, core translation, and reactions. Source deadlines do not scan core inputs or poll idle file-only sources. Native watch updates keep core and extension paths separate. Bounded pages, failed-call retries, removed-source release, and saved-position restart have tests, including an actual private daemon.
- Installation sources and active actor sources have host scope selection. A typed host lease can keep a repository source active without a session. Repository/workspace view and job consumers of that lease remain open. Source input now produces extension facts through its declared decoder. No extension dashboard view exists yet.
- Canonical SQL storage separates histories and preserves old facts in the default history. Core reads, audits, diagnostics, and lifecycle triggers exclude candidates. No application history creation or publication route exists yet.
- The engine uses the mixed interpretation transaction for facts, complete processing steps, source links, decoder state, and pending changes. Storage rejects omitted eligible transforms. Failed extension calls retain their input or produce an explicit failed decoder verdict. Core reactions run only after new fact acceptance. Core raw changes are enabled; the required facts come from the stored original before any worker call.
- The core reaction loop reads the mixed stream. It advances past extension facts without creating session rows or sending display notices. A failed core write stops the ordered batch and retains the failed fact and its tail for retry. This checkpoint does not report completion by extension projectors or observers.
- Mixed raw processing starts at most one page per engine pass. A cooperative one-second interval can stop before the next original, but never inside its journal or transaction. An explicit continuation lets core reactions run and releases the retained runtime between passes. A single call or interpretation can still exceed the interval.
- `BAQYLAU_EXTENSION_READ_ONLY=1` blocks extension changes and rescans. It does not make the whole application read-only. Runtime and operation responses omit private settings documents.
- Two external workers can discover each other and use declared peer queries with checked scope, runtime, deadline, and call chains.
- External-package tests keep backend code, web modules, styles, terminal layouts, and feature tests outside the host checkout. The web SDK has an independent loader; the terminal SDK has typed blocks. These tests do not mean that the dashboard or Kitty can load those views yet.
- The host and external Python packages use one shared policy. Web packages share type, lint, formatting, and coverage rules. The complete extension release gate remains open.

### Latest work and evidence

The current work completes the P05-T01 foundation: multi-entry commits, typed records, and projector invocation. Schema 36 gives `session_entries` a canonical `commit_cursor` and a `position`, so one event can add several ordered feed entries. Session deltas, aggregate cursors, the high-water mark, and the running list advance by the commit cursor; only feed paging uses the row cursor. The same migration creates `extension_records` and `extension_projection_cursors`. `ExtensionRecordRepository.record_states()` captures selected keys with explicit missing rows, and record puts and deletes commit inside the session-data transaction after every expected revision is checked. `ProjectionPass` invokes each enabled projector for every scope with new facts, validates the complete result, and commits entries, records, and the cursor together; the engine worker runs it after the core reaction drain inside the captured runtime batch. The extension entry body is part of the closed feed vocabulary in the domain, the API, and the SDK. The projection transform caller now runs every enabled transform over the complete proposal and applies the SDK's own operation rules. The candidate rebuild remains open. See the [projector invocation record](phases/05-data-and-commands.md#work-record--projector-invocation) and the [transform caller record](phases/05-data-and-commands.md#work-record--projection-transform-caller).

The preceding work adds bounded audit steps and their public read to P04-T05. The raw-event audit and its command-line document report each processing step's stage, owner, outcome kind, rejection size and digest, and diagnostic code, with the exact history and runtime revisions. Each journal returns at most 1,000 steps and reports truncation. `GET /api/diagnostics/raw-events/{raw_event_id}` and `DiagnosticsResource.raw_event_audit()` return the same metadata as a typed bounded response: the payload byte length and canonical event count replace the payload bytes and event bodies. Complete bodies, settings documents, credentials, and payload bytes stay out of the public read. The extension host passes 905 cases sequentially, and the main selection passes 2,412 cases. The full Wemake gate passes with zero findings after the stored-step codec and the audit read were split into staged modules; full-tree Ruff and strict types pass.

The preceding work implements the approved [shared-body journal storage](journal-admission.md#implementation-record--normalized-journal-storage). Schema 35 adds immutable bodies, owned steps, and body ownership links. `interpretation_journals.codec_version` selects the inline codec 1 or the normalized codec 2. The write transaction admits the normalized size before any row, and the read path expands codec 2 references with digest and length checks. The reproduced 20-addition case now publishes: its expanded JSON is above the 32 MiB limit, and its normalized journal is below it. Pipeline admission checks the call budget before each extension worker call and records a limit or rejection with exact evidence. Storage replays every claim and rejects a false one. Bounded public reads and private-process acceptance remain open.

The preceding work connects the approved [harness lifecycle protocol](lifecycle-split.md) to the engine and interpretation transaction. New format-2 journals record required lifecycle before raw processing and activity after it. Required facts are not canonical transform input. The final order places starts before activity and finishes after it. Large originals retain their bytes and required facts even when activity exceeds the worker transfer limit. Old format-1 journals remain readable and permit exact retries. The focused processing and storage selection passes 166 cases. The private-process acceptance now passes nine cases through actual daemon and worker processes: drop activity and keep the required session, replace and add activity, drop finish activity and process later input, fail the worker and retry safely, reject an invalid reply and retain it, restart without repeated acceptance, and raw drop, replace, and insert on core input. The guard which rejected core raw changes was removed after those cases passed. The complete extension host selection passes 919 cases sequentially, and the main selection passes 2,412 cases. See the [private-process acceptance](lifecycle-split.md#private-process-acceptance). Core raw changes are enabled. No phase or additional subtask is complete.

The previous work added the dashboard management page and the approved worker cleanup change. The page uses actual lifecycle APIs, keeps requested and active state separate, and checks affected dependents before writes. Shutdown stores deactivation evidence before resource closure and stores the close result before releasing the native lease. Unresolved external jobs remain unresolved. See the [P06 management record](phases/06-web-and-settings.md#work-record--dashboard-management) and [worker cleanup implementation](worker-cleanup.md). That full Python run passed 3,259 cases.

The preceding investigation reproduced a journal admission defect: one valid 20 MiB canonical reply exceeds the 32 MiB journal limit after repeated body copies, leaving its original pending and accepting no facts. The user approved shared immutable bodies and typed internal references. This refactor remains to be implemented. See the [journal admission design and evidence](journal-admission.md).

The preceding work adds cooperative mixed raw scheduling for P04-T02 and P04-T04. A steady raw backlog no longer requires a complete drain before core reactions and runtime release. The engine runs one bounded page, checks elapsed time between complete originals, and owns an explicit continuation timer. A private daemon completes 101 file-source records without another file change. See the [mixed scheduling record](phases/04-events.md#work-record--cooperative-mixed-raw-scheduling).

The preceding work limits mixed page content for P04-T04. Live and scoped reads use SQL metadata to select an ordered prefix with at most 4 MiB of stored content. An oversized first fact returns alone, so the reader does not lose progress. This is not a hard process-memory or serialized-response limit. See the [page content record](phases/04-events.md#work-record--mixed-page-content-limits).

The preceding work connects mixed core consumption for P04-T04. An explicit read protocol supplies both fact branches. Only core facts enter core reactions. The core checkpoint can pass extension facts, but cannot skip an unprocessed core fact. SQL failure and private-daemon restart tests pass. See the [mixed core consumer record](phases/04-events.md#work-record--mixed-core-consumption).

The preceding work adds bounded prior-fact snapshots for P04-T03. The repository captures at most 1,000 facts and 1 MiB of encoded snapshot data from one exact scope and history. The SDK reports whether that snapshot is complete. Storage checks completeness claims again when it accepts an interpretation. The processing batch now uses this reader. See the [prior-state record](phases/04-events.md#work-record--bounded-prior-state).

The preceding work connected mixed interpretation in P04-T02 and started P04-T03 canonical processing. `ExtensionProcessing` and `CoreInterpretation` are explicit host protocols. One retained runtime covers sources, raw transforms, core or extension decoding, canonical transforms, and post-commit core input reactions. Tests check complete selection, preflight failures, transform behavior, core reactions, and actual daemon fact writes. See the [mixed engine record](phases/04-events.md#work-record--mixed-engine-interpretation).

The preceding source integration added 63 cases: 61 unit, native-watch, scope, and engine cases plus two private-daemon cases. See the [engine source record](phases/04-events.md#work-record--engine-source-integration). Those daemon cases now also verify accepted facts.

The preceding work completed P04-T01 storage review and started P04-T02 source storage. Schema 33 and `ExtensionSourceRepository` commit source progress with original input. That source transaction has 41 tests. Another 15 cases closed prior-fact and scoped-page review. See the [source transaction record](phases/04-events.md#work-record--atomic-source-reads-and-storage-review). P04-T04 is in progress for its storage subset; full engine conformance remains open.

The preceding schema-32 work added mixed interpretation storage. A typed protocol owns complete writes and reads. It stores ordered processing steps once, preserves first accepted facts and later proposals, and compares decoder state inside the transaction. See the [P04 interpretation record](phases/04-events.md#work-record--mixed-interpretation-storage).

The preceding schema-31 work added canonical history storage and live-reader isolation. Existing IDs, bodies, source links, cursors, and session states survive migration. Candidate facts cannot affect live session triggers or claim a live canonical ID. See the [P04 history record](phases/04-events.md#work-record--canonical-history-storage-and-live-isolation).

The preceding schema-30 work added scoped raw storage. Core records retain required session fields through branch constraints. Extension observations have explicit scope and schema metadata without fake core fields. Both use the same raw IDs, arrival cursor, and pending queue. See the [P04 raw storage record](phases/04-events.md#work-record--scoped-original-observations).

The preceding [migration safety work](phases/04-events.md#work-record--migration-transaction-safety) corrected partial schema changes after failure. The runner reads the version under a write lock and commits all pending migration steps together.

The previous work added candidate settings migration through the existing pure SDK protocol and manager. See the [P03 migration record](phases/03-runtime.md#work-record--candidate-settings-migration) for storage rules, tests, and limits. This starts the settings subset of P05-T05 before its record/history dependencies are complete.

Latest verification: The full Python suite passes 3,276 tests with 18 warnings in 330.69 seconds using four workers, excluding Kitty and live `tests/e2e`. All 17 new protocol cases and five focused naming checks pass. Strict types pass for 2,814 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. A six-worker run had 3,275 passed tests and one 30-second timeout in an existing three-worker restart case; that case then passed alone in 21.73 seconds. The six-worker timing issue remains open. See the lifecycle split record for exact limits and results. Full Wemake retains six unrelated Codex findings; dead-code analysis retains the prior 28 findings. No test timeout, assertion, skip, or quality exemption was changed. The plan retains 10 phases and 53 tasks.

Previous verification: The cleanup and dashboard work passed 3,259 Python tests with 26 warnings in 261.83 seconds using six workers, excluding Kitty and live `tests/e2e`. Frontend gates passed with 134 dashboard and 28 web SDK unit tests. The complete 54-case Chromium/WebKit run passed; the 10 actual-daemon extension browser cases passed again on the final build in 24.2 seconds. No live user daemon, database, service, or Git remote was changed in the current protocol work.

### Next work

Complete P04 conformance before claiming the event system is ready. Protect core lifecycle input before permitting core raw changes. Add multi-extension and process-failure tests, complete pipeline admission before calls and result application, and add pass-time limits and public processing diagnostics. Mixed raw processing now yields between complete originals and after one page. This is not a hard bound on one worker call, one interpretation, source work, or core reaction draining. Mixed consumer pages limit stored content, with an explicit single-oversized-fact rule. Prior state has a separate hard count/byte bound and explicit coverage. It remains a prefix when a scope exceeds those bounds; a missing fact in an incomplete snapshot does not prove absence. Pure transforms cannot fetch another page through a live callback. History publication stays closed until P05 can switch canonical and projection heads together. Complete the remaining P03 settings scope, credential, health, and removal work as required by their consumers. Ordinary settings documents are not a secret store. The dashboard management subset now works. Complete its remaining forms, independent assets, and dynamic views in P06.

Write-job draining and record migration require the data and execution work in P05. Those dependencies remain open. Continue through P04–P10; the adapters and Git packages remain part of the full scope.

## Start here

This directory is the detailed plan and task record. It replaces the earlier HTML proposal as the implementation reference. All API names, package names, tables, routes, commands, and new paths in this directory are proposed unless marked as current.

Read these files before starting a phase:

1. [Architecture and data rules](architecture.md).
2. [Python and frontend protocols](protocols.md).
3. [Shared tools and quality policy](quality.md).
4. [Test design and acceptance checks](verification.md).
5. The selected phase and its dependencies.

Design acceptance: The user accepted the plan and requested implementation on 2026-09-14 with “Ok now start working on it.” This includes the proposed protocol, packaging, and application boundary changes. Implementation status is recorded in the phase files.

## Phase order

The phase file contains the authoritative status. This table lists dependencies, not a second copy of status:

| Phase | Scope | Depends on |
| --- | --- | --- |
| [P01 — Protocols and models](phases/01-protocols.md) | Public types, ownership, source scopes, manifest, protocol prototype | None |
| [P02 — SDK packages and shared checks](phases/02-sdk-and-quality.md) | Installable SDKs, common lint policy, independent package checks | P01 |
| [P03 — Runtime and cooperation](phases/03-runtime.md) | Discovery, workers, activation, dependencies, services | P01, P02 |
| [P04 — Event processing and storage](phases/04-events.md) | Raw and canonical transforms, validation, audit, stable identity | P01, P03 |
| [P05 — Derived data, commands, and history](phases/05-data-and-commands.md) | Projections, queries, external actions, rebuilds, migrations | P04 |
| [P06 — Web host and settings](phases/06-web-and-settings.md) | Independent modules, routes, display slots, extension management | P03, P05 |
| [P07 — Kitty host](phases/07-kitty.md) | Display blocks, pane lifecycle, input actions, fallback | P03, P05 |
| [P08 — E2E and host release](phases/08-tests-and-release.md) | External test kit, conformance, failure tests, release checks | P01–P07 |
| [P09 — Adapters extension](phases/09-adapters.md) | Logs, deploy, Jira, metrics, Slack, commit and push observations | P08 |
| [P10 — Git extension](phases/10-git.md) | Repository views, staging, commit, generation, push, adapters cooperation | P08; P09 for the final cooperation check |

P06 and P07 can be implemented independently after their prerequisites. P10 read features do not depend on P09. Each task has a stable ID such as `P04-T03`. Use IDs in work records and review descriptions.

## Task record rules

Each phase and subtask has Context, Task, Outcomes, Status, and Verification fields. Tasks also identify dependencies, code areas, and evidence. Keep these fields when editing the plan.

Allowed status values are:

| Status | Meaning |
| --- | --- |
| `not_started` | No implementation work has started. |
| `in_progress` | Work has started. The record names the current owner and remaining work. |
| `blocked` | The record names a specific unmet dependency, its effect, and the required next action. |
| `done` | The stated outcomes exist and the verification evidence is recorded. |

At task start, add `Owner:` and set the status to `in_progress`. At handoff, record changed paths, decisions, checks, failures, and the next action. Do not mark a test as passed if it was skipped. A phase is done only when its required tasks are done.

Use this task record shape:

```text
### P00-T00 — Task title

Status: not_started
Depends on: Task IDs, or None
Context: Existing behavior and the reason for this work.
Task: Specific work and implementation boundaries.
Outcomes: Observable result and required artifacts.
Code areas: Current or proposed paths.
Verification: Inputs, actions, expected results, and relevant commands.
Evidence: Not recorded. Replace with checks, date, revision, and artifact paths.
```

The descriptions in this plan are design instructions. During implementation, replace unknown details with verified decisions. Update affected contracts and dependent tasks together. Do not create a second tracking list with separate status values.

## Current evidence

These files were inspected for this plan:

| Current source | Relevant fact |
| --- | --- |
| [Harness plugin](../../harness/contracts/plugin.py) and [harness protocols](../../harness/contracts/events.py) | One plugin groups typed capabilities. |
| [Terminal contract](../../terminal/contract.py) | Small protocols are composed into a terminal plugin. |
| [Protocol architecture tests](../../tests/test_architecture_protocols.py) | Concrete implementations must declare the protocols they implement. |
| [Translation phase](../../engine/interpret/translation.py) | Translation is stored before input reactions run. |
| [Consistency checks](../../engine/interpret/consistency.py) | Current canonical output must match raw session, harness, and actor identities. |
| [Canonical storage](../../repository/impl/sqlite/canonical_events.py) | Canonical IDs converge. The first accepted body stays authoritative. |
| [Session writers](../../engine/sessiondata/contract.py) and [changes](../../repository/contract/session_data.py) | Current writers produce one session aggregate and at most one entry per event. |
| [Reaction runtime](../../engine/react/loop_runtime.py) | Current rebuild clears live derived data. New candidate rebuilds need a separate path. |
| [Web routes](../../dashboard/frontend/src/app/route.ts) and [pane renderer](../../client/_pane_rendering.py) | Current display selection is fixed. |
| [Makefile](../../Makefile), [CI](../../.github/workflows/test.yml), and [testing guide](../testing.md) | Quality checks include types, design, dead code, browser tests, and separate live test layers. |

## Design assumptions

- The first release runs trusted local packages. Worker processes provide failure control, not an OS security boundary.
- Python backend extensions use the public `ExtensionPlugin` protocol. Web extensions use a TypeScript interface and may use Svelte.
- Packages use installed SDK releases. They do not import private host code or depend on a sibling host checkout.
- Core session events retain their strict models. Extension documents have registered schemas and typed envelopes.
- Dropping an event suppresses downstream processing. Original observations remain available for audit.
- Enable and disable apply to future processing. History changes require an explicit rebuild.
- A Git repository can be viewed without an active coding session.
- No marketplace, remote package install service, or untrusted-code execution system is included in the first release.

The actual adapters CLI output has not been inspected. P09-T01 must resolve its data contract before the product schemas are fixed.
