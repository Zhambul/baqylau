# P04 — Event processing and storage

Status: in_progress

Owner: Codex

Depends on: P01, P03

Context: Current canonical storage accepts a fixed event vocabulary. Translation decisions, facts, and source links are already one transaction. Extensions must preserve that audit and identity model.

Task: Add scoped extension observations, raw and canonical transforms, typed storage, complete audit steps, and replay-safe identity.

Outcomes: Extensions can keep, replace, drop, and add events before commit. Original observations and every decision remain inspectable. Normal core processing still works with no extensions.

Verification: C06–C10 and C21 pass. Repository rollback tests prove atomicity. Repeated observations preserve the first accepted fact within a history revision.

Evidence: Migration safety, scoped originals, canonical history isolation, and mixed interpretation storage exist. Schema 32 stores complete journals and decoder state. Schema 33 commits source progress with original input. The engine now uses one retained runtime for source reads and mixed interpretation. It selects raw and canonical transforms and preserves input after a failed extension result. Core raw changes are enabled: the required facts come from the stored original before any worker call. C06–C10 pass through the protocol, storage, pipeline, admission, audit, and private-process cases. C21 needs the P05 command and query surfaces, so the phase stays in progress until those exist. See the work records below.

## Read first

Current decisions: The user approved the harness lifecycle split, shared-body journal storage, and physical worker cleanup with retained uncertainty on 2026-09-15. Do not request these approvals again. The cleanup change is implemented and verified by the full Python suite; see the [cleanup record](../worker-cleanup.md#implementation--2026-09-15). The [harness protocol split](../lifecycle-split.md) now has engine calls and typed lifecycle/activity journal steps. The [shared-body journal refactor](../journal-admission.md), admission checks, and private-process lifecycle acceptance remain open. Older work records below describe their original state, not a current approval requirement.

Read [architecture](../architecture.md), [protocols](../protocols.md), current `engine/interpret/translation.py`, `engine/interpret/consistency.py`, `repository/contract/facts.py`, `repository/impl/sqlite/canonical_events.py`, and `repository/impl/sqlite/schema.py`.

### P04-T01 — Define and migrate extension storage

Status: done

Owner: Codex

Depends on: P03-T04

Context: Current raw records require session and harness IDs. Canonical rows use one accepted event ID. New data needs explicit scope, schema ownership, and history revisions.

Task: Reuse the management metadata and schemas from P03-T01. Fix the DDL and repository models for scoped raw input, interpretation steps, and versioned canonical identity. Add indexes for pending input, source position, scope paging, and audit joins. Select the next schema version from the actual branch. Backfill old data into a default history revision.

Outcomes: Reviewed migrations and typed repository protocols. Existing IDs, bytes, source links, and visible ordering survive migration. Core session fields remain mandatory for core records.

Code areas: Current repository contracts, models, mappers, SQLite schema and migrations; proposed extension repositories.

Verification: Migrate populated old databases with sessions, actors, shell output, ignored input, and repeated observations. Compare content and counts. Inject a migration failure and verify rollback. Test scope and history indexes using representative reads. Run existing raw and canonical repository tests.

Evidence: Schema 30 and `ObservationRepository` store original extension input in the existing raw table and pending queue. Schema 31 adds canonical history keys, history-bound source links, runtime fields, a suppressed verdict, indexed scope columns, and default history backfill. Core readers and lifecycle triggers exclude candidate histories. Schema 32 and `InterpretationRepository` add typed mixed writes, complete journals, ordered step reads, retained later proposals, and decoder-state comparison. Tests cover exact bytes, first capture, manager/runtime checks, indexed paging, and migration rollback. The final review adds 15 cases for actual prior facts, changed prior bodies and acceptance times, strict page bounds, complete scope identities, mixed page order, and reads after owner removal. The storage outcomes are complete. Engine selection, dispatch, public diagnostics, history publication, and full phase conformance remain separate open tasks. See the work records below.

### P04-T02 — Integrate extension sources and raw transforms

Status: done

Owner: Codex

Depends on: P04-T01

Context: The interpreter currently selects a harness plugin for every raw event. Extension sources need their own declared decoder and can be repository-scoped.

Task: Route sources by typed origin. Register source watches and deadlines through existing input notices. Record original observations before transforms. Apply raw transform operations to derived translation inputs, with stable source and step links. Select only the declared translator for each source. Define empty-read checkpoints explicitly without losing source-position consistency.

Outcomes: External sources work without fake harnesses. Raw replace, drop, and insert affect translation input while preserving original bytes. Restart resumes from committed progress.

Code areas: Current `engine/interpret/puller.py`, `engine/interpret/translation.py`, `engine/interpret/translators.py`, work queue, raw repository; proposed source adapters.

Verification: C06 and C08 test all raw operations and all-dropped input. C07 tests replayed source positions after process failure. C21 emits a repository observation without a session. Partial files and source replacement retain the current input-watch behavior. No idle scan is introduced.

Evidence: SDK source and decoder protocols, atomic checkpoints, native watches, deadlines, and host scopes are connected. Mixed pending input now selects its decoder by typed origin. The engine calls selected raw transforms before decoding, keeps exact originals, and records failed replies without applying their output. Private-daemon cases check source-to-fact processing and restart. Mixed raw work now yields after one page or between complete originals after interval expiry. An explicit continuation completes a 101-record file-source backlog without another file change. New three-worker cases verify declared order in both activation orders, raw additions reaching the next worker, all-dropped input, and complete rejection of an invalid later reply. Core raw changes are enabled. The private-process lifecycle acceptance passes nine cases with real workers: canonical drop, replace and add, finish release, failed worker, invalid reply, restart, and raw drop, replace, and insert. See the [private-process acceptance](../lifecycle-split.md#private-process-acceptance). Multi-extension alone cases, the aggregate call budget, and unclean crash recovery are covered by the conformance record below. Timeout conformance keeps its transport-level coverage. See the work records below.

Status result (2026-09-25): C21 is proven by an external package: the `baqylau-git` source watches a repository scope, reads Git's status after each notice, and records a repository observation with no session; its E2E case (`tests/e2e/test_repository_refresh.py`) sees the edit reach the repository's record through the private host (P10-T02). Partial final lines and a replaced source file keep their source tests (`tests/extension_api/test_source_failures.py`).

### P04-T03 — Apply and validate canonical transforms

Status: done

Owner: Codex

Depends on: P04-T02

Context: Canonical input reactions currently run immediately after storage. Extension output must be checked before storage and before any of those reactions.

Task: Add the typed canonical pipeline between translation and `record_translation`. Apply one extension's operations atomically and validate its full result. Preserve input on extension failure. Re-run core identity and lifecycle checks. Add the extension envelope codec and owner checks. Keep stored cursor allocation inside the repository.

Outcomes: Ordered transforms support core and extension facts. Invalid results never reach reactions. Generated facts move forward through the remaining transforms without recursive restart.

Code areas: Current translation phase, consistency checks, domain event mapping, canonical codec, and fact storage; proposed transform coordinator.

Verification: C06 and C09 cover replace, drop, insert, wrong owners, changed scopes, malformed payloads, missing references, and lifecycle corruption. C10 covers timeout with unchanged input. Verify that a dropped display item cannot prevent required source or process cleanup. Run core canonical session tests.

Evidence: The engine now runs selected canonical transforms before the complete interpretation transaction and core input reactions. Shared pure checks validate scope, document declarations, original core references, cause existence, cause cycles, and first-acceptance identity. Invalid replies retain the preceding input. Required core start and finish identities and order are preserved. Tests cover suppression, insertion with a retained intermediate cause, and core post-commit reactions. Prior-state capture now has explicit coverage, count/byte bounds, and transactional completeness checks. New three-worker cases prove generated facts reach later transforms, full-result rejection retains earlier decisions, and canonical suppression permits later original processing. A real worker exit permits later processing. It exposed a daemon shutdown failure; the approved cleanup fix now passes the focused process regression. The full Python suite passes 3,259 cases. The private-process lifecycle acceptance now proves drop, replace and add, finish release, failed worker, invalid reply, and restart through actual daemon and worker processes. Multi-extension alone cases and the aggregate call budget are covered by the conformance record below. Timeout conformance keeps its transport-level coverage. See the work records below.

Status result (2026-09-25): C10 at the daemon level: `tests/extension_host/test_transform_health_daemon.py` gives a transform worker a hang with a 3-second call deadline; the deadline ends the call, the preceding input is kept, and the other worker's output is applied (P08-T03). The finish-release case of the private-process acceptance proves that a dropped display item does not stop the required cleanup.

### P04-T04 — Commit complete decisions and stable identities

Status: done

Owner: Codex

Depends on: P04-T03

Context: The current repository commits one interpretation, its events, and links together. Empty output and repeated input must not leave pending work stuck.

Task: Extend the transaction to store runtime and history revisions, ordered transform decisions, errors, source links, and accepted output. Preserve first-accepted canonical bodies within a history revision. Add an explicit suppressed verdict where required. Allocate stable generated IDs and reject duplicate output keys. Advance pending and consumer cursors for empty and duplicate results.

Outcomes: One raw event has a complete explanation of its processing. Retries do not duplicate facts. Settings changes cannot overwrite already accepted bytes.

Code areas: Current `domain/records.py`, fact repository and mapper, SQLite interpretations, canonical rows, and pending input tables.

Verification: C07 and C08 cover duplicates, all-dropped batches, crash after commit, and crash before commit. Inject failures between each write and confirm total rollback. Send repeated logical facts under changed settings and verify preserved first acceptance plus recorded later proposals.

Evidence: Schema-32 storage implements complete transactions, stable fact identity, empty verdicts, later proposals, and pending removal. The engine now uses this transaction. Storage rejects omitted eligible transforms and false preflight failures. Private-daemon tests verify convergence and unchanged journals after restart. Core consumer progress now advances through extension-only facts, with ordered core failure and SQL rollback checks. Mixed pages limit stored content without blocking a large first fact. Unclean crash recovery is covered by the conformance record below. Extension projection and observer consumers are done in P05 (P05-T01 projections, P05-T03 observers): each keeps its own cursor for each owner, scope, history, and generation, written in the same transaction as its output. Done on 2026-09-24. See the mixed engine, mixed core consumption, and page content records below.

### P04-T05 — Expose diagnostics and processing snapshots

Status: done

Owner: Claude Code

Depends on: P04-T04

Context: Existing E2E signoff requires a verdict for each raw event. Intentional extension drops must be distinct from unknown input and failures.

Task: Extend audit models, typed diagnostic reads, SDK responses, and the raw-event audit CLI with extension steps and revisions. Add bounded inspection of input and output differences, source causes, pending counts, and health. Keep secret values out of diagnostics.

Outcomes: Users and E2E tests can explain each transform result through public typed reads. Existing audit output remains meaningful for core-only sessions.

Code areas: Current `app/raw_events_audit_cli.py`, `app/raw_event_audit_documents.py`, diagnostic repositories, API diagnostic models, and SDK audit access.

Verification: Inspect keep, replacement, drop, addition, worker failure, and unknown source examples. Each reports the correct owner and revision. E2E signoff accepts intentional drops but still fails on unknown and failed processing. Confirm paging and content limits with large output.

Evidence: The raw-event audit now reports bounded step metadata and the exact history and runtime revisions. `tests/extension_host/test_interpretation_audit.py` and `test_interpretation_audit_steps.py` cover every recorded step, limit evidence, rejection evidence, the step bound, the command-line document, a core-only audit with no steps, and a journal with a one-megabyte stored body whose document stays below 16 KiB. `GET /api/diagnostics/raw-events/{raw_event_id}` returns the same bounded metadata through a typed response: the payload byte length and canonical event count replace the payload bytes and event bodies, and an unknown identity is a 404. `sdk.client.DiagnosticsResource.raw_event_audit()` reads that route. `tests/test_http_diagnostics_audit.py` and `tests/test_sdk_resources.py` cover the route and the SDK method. Public step bodies, settings documents, credentials, and payload bytes are not returned. The E2E signoff already accepts `SUPPRESSED` verdicts and flags unknown and failed processing through the diagnostics repository, which also reports pending counts and health. Input and output differences appear as the ordered operation kinds; source causes stay on the accepted facts, which the bounded mixed fact pages return. The public surfaces are bounded: the mixed fact pages use the 4 MiB content budget, and the audit route returns no bodies. A separate paged body route is not needed while no public consumer reads journal bodies; the local CLI keeps the complete forensic document.

## Work record — bounded audit steps and revisions

Date: 2026-09-19

Owner: Claude Code

Status: in_progress

Context: The raw-event audit explained a verdict and its canonical events, but not how extension processing reached that verdict. A complete journal can hold megabytes of bodies, so a diagnostic read cannot return it as one object.

Task: Extend the domain audit, the SQLite audit read, and the command-line document with bounded step metadata and the exact revisions. Keep secret values and complete bodies out of the read.

Outcomes: `InterpretationAudit` gains `history_revision`, `runtime_revision`, `format_version`, `steps`, and `steps_truncated`. `InterpretationAuditStep` carries the step index, stage, owner, outcome kind, observed rejection size and digest, diagnostic code, reason, and the ordered operation kinds. The repository extracts these fields with SQLite JSON functions from both the codec 1 view and the codec 2 step table, in one query per audit and one bounded query per session. Each journal returns at most 1,000 steps and reports truncation. The command-line document carries the same fields.

Code areas: `domain/records.py`, `repository/impl/sqlite/raw_event_audits.py`, and `app/raw_event_audit_documents.py`.

Verification: `tests/extension_host/test_interpretation_audit.py` and `test_interpretation_audit_steps.py` pass eight cases: every recorded step matches its journal step, a limit step keeps its owner and reason, an applied reply reports its ordered operation kinds, a rejection step keeps its exact size, digest, and diagnostic code, a journal above the bound reports truncation, the command-line document carries the steps, a core-only interpretation has no steps, and a journal with a one-megabyte fact body returns a command-line document below 16 KiB. Architecture, naming, audit repository, and foundation checks pass. Strict types and root Ruff pass for the changed files.

Evidence: The session read stays at five queries for any session size. Step metadata is extracted from stored JSON without loading complete bodies. No live user daemon, database, or remote service was changed.

Status result: The bounded step read, its large-output check, and the public typed and SDK audit access are implemented. Operation kinds cover the keep, replace, drop, and insert differences, and source causes stay in the canonical events. The command-line document keeps the full local forensic payload; the HTTP route and the SDK method stay bounded. Pending counts and health already come from the diagnostics repository. Public paged body reads remain open. P04-T05 remains in progress.

Next action: Add paged body reads for public journal consumers, then run the P04 phase verification.

## Work record — bounded public audit access

Date: 2026-09-20

Owner: Claude Code

Status: in_progress

Context: The raw-event audit had a local command-line document only. A remote reader had to choose between the complete forensic document, which carries the raw payload and every canonical body, and no audit at all.

Task: Add a bounded typed HTTP read and its SDK method for one raw event. Keep the complete document as the local forensic tool. Return no payload bytes and no canonical bodies.

Outcomes: `GET /api/diagnostics/raw-events/{raw_event_id}` returns `RawEventAuditResponse` with the raw event metadata, the payload byte length, the decision, the history and runtime revisions, the format version, the canonical event count, and at most 1,000 step records. An unknown identity returns 404. `DiagnosticsResource.raw_event_audit()` reads the route through the SDK adapter.

Code areas: `api/diagnostics/raw_event_audit_models.py`, `api/diagnostics/routes.py`, `app/provider_databases.py`, `sdk/application_models/__init__.py`, `sdk/client_adapters.py`, `sdk/client_service_resources.py`.

Verification: `tests/test_http_diagnostics_audit.py` passes two cases: a journal with a one-megabyte stored body answers below 16 KiB with the exact operation kind, and an unknown raw event answers 404. `tests/test_sdk_resources.py` proves the SDK method calls the route and returns the typed response. `tests/extension_host/test_interpretation_audit_steps.py` now stores the real body rows, so its bounded case proves the read path ignores a large stored body rather than a small fixture row. The main selection excluding the extension host passes 2,412 cases; the extension host passes 905 cases sequentially. Wemake, Ruff, architecture, and strict types pass for the changed files. The dead code gate keeps its 29 known missing application uses; the two serialized response fields joined the existing allowlist section.

Evidence: The route never reads `interpretation_bodies` and never returns the raw payload. The provider opens a read-only handle, so a public read cannot migrate or write the store. No live user daemon or database was changed.

Status result: Implemented and verified. The public surfaces are bounded, so no separate paged body route was added; P04-T05 is complete.

## Work record — private-process lifecycle acceptance

Date: 2026-09-20

Owner: Claude Code

Status: done

Context: The harness lifecycle split had protocol and pipeline coverage with controlled local calls. Nothing proved that the actual daemon runs the required pass before worker calls, protects the required facts from a real external transform, releases a finished session, and keeps its evidence across a restart.

Task: Add external transform fixtures to the existing private-daemon pattern. Use public activation, a private database, native-shaped input, and actual worker processes. Do not use a live user session.

Outcomes: The daemon accepts changed core activity while the required session state stays under host control. A failed worker and an invalid reply both keep the preceding input and record an explicit failure. A restarted daemon reuses the stored journal.

Code areas: `tests/extension_api/lifecycle_canonical_example.py`, `tests/extension_host/lifecycle_daemon_manifest.py`, `tests/extension_host/lifecycle_daemon_fixture.py`, `tests/extension_host/lifecycle_daemon_checks.py`, `tests/extension_host/test_lifecycle_daemon.py`.

Verification: Six cases pass in 49.57 seconds: drop activity and keep the required session, replace and add activity, drop finish activity and process later input, fail the worker and retry safely, reject an invalid reply and retain it, and restart without repeated acceptance. `tests/extension_host/lifecycle_daemon_fixture.py` reads raw event identities, journals, accepted facts, and pending counts from a read-only private handle. Wemake, Ruff, strict types for 2,868 files, and architecture pass. The dead code gate keeps its 29 known missing application uses.

Evidence: The external package imports the SDK and the standard library only. Its owner selects the worker behavior, so one backend file serves all six cases. The required pass runs before the worker call: every case accepts `session.started` and `actor.started` even when the canonical worker drops, fails, or returns an invalid reply. The finish case proves the later input is processed after the session release. The restart case compares the complete stored journals before and after. No live user daemon or database was changed.

Status result: Implemented and verified. Core raw changes are enabled by the separate raw record below.

## Work record — P04 conformance cases

Date: 2026-09-20

Owner: Claude Code

Status: done

Context: P04 proved the pipeline, admission, and lifecycle paths with controlled calls and real workers, but four conformance claims stayed open: each transform owner alone, the aggregate call budget across stages, unclean crash recovery, and the timeout boundary.

Task: Add the missing conformance cases without changing production behavior. Keep the existing worker, storage, and admission code as the subject under test.

Outcomes: The ordered daemon fixture now runs each transform owner alone. The admission fixture measures the exact normalized raw step size and builds a limit which leaves no room for the next call. A private process can be killed after commit and before commit, then restarted.

Code areas: `tests/extension_host/ordered_processing_fixture.py`, `test_ordered_processing_daemon.py`, `interpretation_admission_fixture.py`, `interpretation_admission_replies.py`, `test_interpretation_admission.py`, `test_interpretation_admission_claims.py`, `test_lifecycle_daemon_crash.py`, `process_fixture.py`.

Verification:

- Alone: with only the first owner active, the output is the replaced anchor, its owned addition, and the raw addition without the peer suffix. With only the second owner active, the output is the replaced anchor alone. Both cases pass in 25.01 seconds and keep the stages `raw`, `extension_translation`, `canonical`.
- Aggregate budget: the test measures the exact normalized raw step size from a first run, sets the limit to the reserve plus that size, and proves the translation call is not made while the raw call was. It passes with the other seven admission cases.
- Crash after commit: the daemon is killed with SIGKILL after the journal is stored. The restarted daemon keeps the same journal, accepts no duplicate, and has no pending input.
- Crash before commit: the slow worker holds the input for three seconds. The daemon is killed while it waits. The restarted daemon processes the input once and drains the queue.
- Timeout: the transport deadline is covered by `tests/extension_api/test_rpc_host_deadlines.py` (a longer default RPC timeout cannot extend the host's remaining call time). The host records a timed-out or failed call as an explicit failed step and keeps the preceding input; the private-daemon failed-worker and invalid-reply cases prove that behavior. A full daemon-level hang case needs a shorter worker policy than the fixed 30-second default, which the 30-second per-test limit cannot hold.

Evidence: The crash cases kill the real process and read the stored evidence from a read-only handle. No production behavior changed. The focused cases pass: two alone cases in 25.01 seconds, eight admission cases, and two crash cases in 21.39 seconds. The complete extension host selection passes 919 cases sequentially in 646.85 seconds. Wemake, Ruff, strict types for 2,873 files, and architecture pass. The dead code gate keeps its 29 known missing application uses.

Status result: Implemented and verified. Projection and observer consumers remain P05 work.

## Work record — core raw acceptance and guard removal

Date: 2026-09-20

Owner: Claude Code

Status: done

Context: The engine rejected every core raw change with "core raw changes require source lifecycle protection". The required facts already came from the stored original before any worker call, but no acceptance case proved that a raw drop, replace, or insert on core input keeps them.

Task: Add raw-transform acceptance cases through the private daemon and real worker processes. Then remove the guard and prove the full selection still passes.

Outcomes: The external fixture backend gains a raw transformer whose owner selects drop, replace, or insert. Three cases prove the required facts, the changed translation input, and the exact stored original bytes. The guard is removed.

Code areas: `extensions/models/interpretation_transforms.py`, `tests/extension_api/lifecycle_canonical_example.py`, `tests/extension_host/lifecycle_daemon_manifest.py`, `tests/extension_host/lifecycle_daemon_payloads.py`, `tests/extension_host/test_lifecycle_daemon_raw.py`.

Verification: Before the removal, the raw drop case failed: the rejected change blocked the whole interpretation and no required fact was accepted. After the removal, all three raw cases pass in 20.97 seconds: raw drop keeps exactly `session.started` and `actor.started` with stages `core_lifecycle` and `raw`; raw replace records a new content identity which differs from the request identity; raw insert records two activity steps. Every case reads the stored payload from a read-only handle and requires the delivered hook byte for byte. The complete extension host selection passes 914 cases sequentially in 662.06 seconds. The first full run exposed one factory defect: the fixture backend provided a raw transformer for canonical packages which did not declare that capability, so activation failed with `preparation_failed`. The factory now provides it only for the raw owners, and the six canonical cases pass. Wemake, Ruff, strict types for 2,870 files, and architecture pass. The dead code gate keeps its 29 known missing application uses.

Evidence: The raw step keeps its complete request and reply. The activity translation reads the replaced content, and the inserted input becomes a second activity step. The stored raw event never changes. No live user daemon or database was changed.

Status result: Implemented and verified. Core raw changes are enabled.

## Work record — migration transaction safety

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: P04-T01 requires failed event migrations to preserve the complete old database. The existing runner used a connection context without an explicit transaction before DDL. All 12 initial regression cases failed: tables, columns, indexes, triggers, or earlier migration versions remained after failure. This was reproduced only in private test databases.

Task: Correct the shared migration boundary before adding extension event tables. Read the stored schema version after acquiring the write lock. Publish all pending migration versions together. On a statement error, missing migration, or COMMIT failure, retain the old schema, data, and version.

Outcomes: `SqliteSchemaManager._migrate()` now starts `BEGIN IMMEDIATE` and reads the schema version under that lock. `_migrate_from_version()` runs the complete pending chain in the same transaction. A second initializer reads the committed version after the first finishes. It skips completed work or retries work that rolled back. The normal repository write boundary is unchanged.

Code areas:

- `repository/impl/sqlite/connection.py`.
- `tests/sqlite_migration_fixture.py`, `tests/sqlite_migration_barrier.py`, and `tests/sqlite_migration_events.py`.
- `tests/test_sqlite_migration_rollback.py`, `tests/test_sqlite_migration_chain.py`, `tests/test_sqlite_migration_race.py`, and `tests/test_sqlite_migration_events.py`.

Verification: The first corrected focused run passed 45 existing and new cases. The complete new set passed 18 cases. It covers CREATE, ALTER, rename, index and trigger creation, DROP, copy failure, missing versions, later-version failure, an actual deferred foreign-key COMMIT failure, exact retry, and two concurrent initializers. Core cases use compressed raw bytes, accepted and repeated facts, ignored input, pending input, sessions, and shell-output follow records. Complete logical dumps check retained schema, rows, and links. Repository reads also check decoded bytes, accepted cursor order, source position, and audit access.

Evidence: Worktree based on `6a9e497`, Python 3.12, macOS. Shared policy `0.1.0a1` is unchanged. No live database or daemon was changed. This migration-safety subset added no schema version; its baseline was schema 29. The later storage record below uses schema 30.

Commands and results:

- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,796 passed, 26 warnings, 158.64 seconds. This includes all 428 extension-host cases.
- `.venv/bin/python -m pytest -q tests/test_sqlite_migration_rollback.py tests/test_sqlite_migration_chain.py tests/test_sqlite_migration_race.py tests/test_sqlite_migration_events.py`: 18 passed.
- `.venv/bin/python -m baqylau_dev check --gate types`: passed for 2,616 source files.
- `.venv/bin/python -m baqylau_dev check --gate ruff` and `--gate parity`: passed.
- `.venv/bin/python -m flake8` with `repository/impl/sqlite/connection.py` and the seven new test/helper files above: passed.
- Full `--gate deadcode`: the same 14 missing extension application uses remain. Full `--gate wemake`: the same six unrelated Codex findings remain. No new exemption was added.
- Plan checks: 10 phases, 53 unique task IDs, required fields, valid dependencies, and 78 local links passed. Status counts are five phases in progress and five not started; 17 subtasks in progress, one done, and 35 not started. `git diff --check` passed.

The first strict type check found one fixed-length tuple inference in the concurrency fixture. An explicit variable-length tuple annotation corrected it. Focused Wemake checks also led to simpler test assertions and a test-owned release context. Final checks passed. Browser, Kitty, live harness, and frontend build checks were not run for this storage-only change.

Limits: This change protects the migration chain of an existing versioned database. It does not add extension event storage or make fresh schema creation and the final idempotent schema script part of that transaction. It does not prove process-kill or power-loss recovery, migration performance, or complete P04 acceptance. The SDK and worker contracts are unchanged. P04-T01 remains in progress.

Design reference: Python's connection context does not itself open a transaction, and the legacy transaction mode does not open one for DDL. See the [Python SQLite transaction documentation](https://docs.python.org/3.12/library/sqlite3.html#transaction-control-via-the-isolation-level-attribute). Tests prove the actual failure and correction for this repository.

Next action: Define the mixed core/extension observation and canonical storage DDL, history identity, and typed repository operations. Preserve the strict core fields and existing cursor order. Add a populated independent schema-29 fixture for the actual event migration. Then connect source and transform consumers through the existing fixed runtime read boundary. P01–P10, including the adapters and Git packages, remain open.

## Work record — scoped original observations

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Core raw records require a session, actor, and harness. Extension sources must support installation and repository scopes without fake core identities. Both branches need one shared arrival order. Existing schema-29 raw records and their foreign-key dependents must survive the change with exact original data.

Task: Add a strict extension branch to the original raw store. Validate complete original documents against retained source declarations and the committed manager/runtime. Keep core readers on their strict model. Preserve a single raw cursor and pending queue.

Outcomes:

- Main schema 30 retains `raw_events`, its IDs, and `pending_raw_events`. `extension_metadata` stores the closed `ExtensionObservationMetadata` model. The original document uses the existing lossless byte columns.
- Generated `origin`, `source_owner`, and `scope` columns derive from the stored branch. Scope and owner/source indexes support exact scoped reads. Repository scope includes the worktree and resolved Git directory.
- SQL checks keep session, harness, and actor fields mandatory for core records. Extension rows must omit core identity and hook fields. Core readers cannot decode an extension record or take its position as harness resume progress.
- `ObservationRepository` is an explicit protocol. Its SQLite implementation supplies complete appends, typed identity reads, mixed pending reads, and bounded scope pages. The existing fact-storage provider composes it without importing a feature backend.
- An append validates request size, owner, source declaration, scope, schema, and existing causes. Manager and runtime IDs must match the committed lifecycle head inside the transaction. A replaced manager is rejected even before runtime restoration completes.
- A versioned hash of owner, exact scope, source identity, and observation key produces a typed `RawEventId`. Runtime settings, arrival time, and database cursor do not change that identity. Canonical fact identity stays separate.
- Original rows and pending work commit together. Exact repeats retain the first row, time, and runtime. Changed original bytes, source position, or candidate fields raise an identity conflict. Repeated input is not requeued. A later failure rolls back earlier rows in the append.

Migration rules: The migration copies raw rows, pending rows, interpretations, links, and the raw AUTOINCREMENT high-water mark to temporary tables. It drops dependent tables before rebuilding raw storage, then restores their exact rows and constraints. It keeps foreign keys enabled. Canonical rows and their accepted order remain unchanged. Fresh creation and upgrade use the same named DDL definitions.

Code areas:

- `extensions/models/observations.py` and `extensions/models/observation_identity.py`.
- `repository/contract/observations.py`, `repository/impl/sqlite/observations.py`, and its three `observation_*` adapters.
- `repository/impl/sqlite/schema.py`, core raw and audit readers, and `app/provider_fact_storage.py`.
- `tests/extension_host/observation_*.py`, `tests/extension_host/test_observation_*.py`, and its independent `fixtures/extension-resolutions-schema-29.sql`.
- `tests/architecture_test_controls.py` and `tests/test_architecture_observation_schema.py`.

Verification: 62 new extension-host cases pass. They use real SQLite files and retained external declarations. The fixture commits a runtime through the lifecycle repository; it does not start a worker or claim package-owned E2E. Tests cover mixed order, repository/session scope, core reader isolation, exact UTF-8 bytes, compression, repeats, changed bytes, invalid schemas, missing causes, stale manager/runtime, runtime changes, SQL branch constraints, page bounds, and index use.

An independent schema-29 fixture combines saved schema-26, catalog-27, lifecycle-28, and resolution-29 DDL. It preserves core rows, ignored and pending input, sessions, shell-output follow records, source links, and deleted cursor limits. Failure injection runs after all 28 statements of the actual migration. Every case retains the full logical old database and then completes a normal retry. Separate cases fail pending insertion and the real connection's COMMIT method.

Evidence: Final verification passed. The first full run found three integration-check failures, not storage failures. The final run passes 2,859 cases, including all 490 extension-host cases. Strict types pass for 2,633 files. Root Ruff, focused Wemake, and shared policy parity pass. Platform: macOS, Python 3.12.1, SQLite 3.51.0. Worktree base: `6a9e497`. SDK versions and shared policy `0.1.0a1` are unchanged. No live daemon or database was changed.

Commands and results:

- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,859 passed, 26 warnings, 187.36 seconds.
- `.venv/bin/python -m pytest -q tests/extension_host/test_observation_*.py tests/test_architecture_observation_schema.py`: 63 passed, three warnings, 2.07 seconds.
- `.venv/bin/python -m baqylau_dev check --gate types`: passed for 2,633 files.
- `.venv/bin/python -m baqylau_dev check --gate ruff` and `--gate parity`: passed. Focused `flake8` passed for all changed production and test files.
- Full `--gate deadcode` reports 22 missing-application-use findings: the prior 14 plus the four new observation methods in both protocol and implementation. No missing caller is exempted. Full `--gate wemake` retains the same six unrelated Codex findings.
- Plan validation passed for 10 phases, 53 task IDs, required fields, dependencies, and 81 local links. Status counts remain 17 subtasks in progress, one done, and 35 not started. `git diff --check` passed. No private SDK worker remained after the tests.

Corrections: The provider now stays in the existing fact-storage module. Host IDs use `RawEventId`; constructor names follow the repository rule. The table check recognizes DDL constants and embedded tables. A negative test proves it still rejects a generic value column. SQL parameter binding reads the named fields explicitly. No missing application use was exempted.

Limits: This is original-observation storage, not complete event processing. The interpreter still consumes its narrow core backlog. Extension source watches, deadlines, and checkpoints are not connected. The standalone append must not advance a source checkpoint or command job separately from its result transaction. Its internal append operation can be reused inside those later complete repository transactions. Versioned canonical facts, interpretation steps, history heads, suppressed verdicts, transforms, and public mixed audits remain open.

The migration needs temporary copy space and holds the migration write lock. No production-size timing, process-kill, or power-loss claim is made. Ordinary observations do not provide automatic secret redaction. Browser, Kitty, live harness, and complete package-owned E2E checks were not run for this subset.

Design references: Indexed generated columns follow SQLite's [generated column rules](https://www.sqlite.org/gencol.html). The migration retains normal [foreign-key enforcement](https://www.sqlite.org/foreignkeys.html). Local tests verify the actual DDL and rollback behavior.

Next action: Finish P04-T01 with mixed versioned canonical storage, interpretation steps, and default history backfill. Keep current core lifecycle triggers and reactions from observing an unpublished history candidate. Then add atomic source checkpoints and mixed interpreter dispatch in P04-T02. Hold the selected runtime across each complete batch. All remaining P01–P10 work, including adapters and Git packages, stays open.

## Work record — canonical history storage and live isolation

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Canonical IDs were globally unique, and one raw ID had one interpretation. A second interpretation therefore could not retain another accepted body. Two SQL triggers also changed live session state from canonical inserts. A history candidate must not enter those triggers, core reactions, audits, or diagnostic checkpoints.

Task: Add history keys and default backfill without changing existing acceptance order or source links. Prepare a separate extension SQL branch. Keep candidate storage outside current application reads. Preserve read-only access to old databases. Leave history publication closed until canonical and projection heads can switch together.

Outcomes:

- Main schema 31 registers `canonical_histories`. Existing data belongs to `default`. Its creation time remains NULL because that historical time is not known. Old interpretations have a NULL runtime revision; the migration does not invent an extension selection.
- `canonical_events` is unique by history and logical event ID. Its global AUTOINCREMENT cursor still records arrival order. `interpretations` is unique by history and raw ID. Interpretation links include history in their primary key, output-order key, and canonical foreign key.
- Canonical SQL has distinct core and extension branches. Core schema version, session, actor, and harness remain required by checks. The extension branch has explicit metadata and no fake core fields. Generated origin, owner, and scope support history and scope indexes. The typed extension codec and application write boundary remain open; SQL fixture insertion is not schema-checked extension acceptance.
- Three `current_*` views select only the default history. Core identity, session-list, stream, and audit reads use those views and exclude extension rows. Diagnostics use the current history. Old forensic databases use their original tables without creating a view or changing the file.
- Both session lifecycle triggers exclude candidate facts. Copying old facts during migration cannot reapply the lifecycle trigger. The migration restores triggers only after the complete copy.
- A candidate-only ID cannot deduplicate a live core fact. A core proposal cannot converge to an extension row. That conflict rolls back the interpretation and leaves its input pending. Original extension observations cannot name a candidate-only canonical cause.
- `RecordedTranslationDecision.SUPPRESSED` stores an intentional empty result with its reason. Core storage removes pending input in the same transaction. Diagnostics accept this decision without treating it as unknown input. No transform consumer emits this decision yet.

Migration: The 33-statement migration copies canonical rows, verdicts, links, and the canonical sequence to temporary tables. It removes dependent links and lifecycle triggers before rebuilding storage. It restores the original 14 canonical columns, five interpretation columns, four link columns, and deleted cursor high-water mark. Foreign keys remain enabled. Raw storage, its pending queue, sessions, shell follows, and projection data are not rebuilt. Fresh schema creation uses the same named table, index, view, and trigger definitions.

Code areas:

- `repository/impl/sqlite/schema.py` and `current_facts.py`.
- Core canonical, raw-audit, diagnostic, and observation-cause readers in `repository/impl/sqlite/`.
- `domain/records.py`.
- `tests/extension_host/canonical_history_fixture.py`, `test_canonical_history_*.py`, and saved `fixtures/main-schema-30.sql`.
- `tests/sqlite_schema_fixture.py`, the existing migration test setup, the original observation upgrade checks, and the negative table-check test.

Verification: 64 new history cases and the earlier observation checks pass as a 127-case focused set. Cases cover default backfill, exact old rows, canonical high-water marks, empty old storage, retained extension raw rows, candidate isolation in all current readers, both lifecycle triggers, first live acceptance, old forensic reads, SQL branch constraints, cross-history foreign keys, history/scope index use, suppression, and core/extension identity conflict. Failure after every actual migration statement preserves the complete old logical database and permits a normal retry. A real deferred foreign-key error at COMMIT also rolls back the new schema, data, views, and triggers.

The first full run had 23 failures. Most came from older tests that created current tables and then assigned an old schema version. Their shared setup and five direct setups now use the saved schema-26 baseline before restoring their earlier target fields. Assertions about the old feature migrations remain unchanged. The observation comparison now selects every original column explicitly, rather than comparing new metadata columns with an older row shape. Its failure test still targets schema 30 when the current version advances. The negative table test now verifies both raw and canonical DDL constants.

Evidence: Worktree based on `6a9e497`, Python 3.12.1, macOS. Final full verification passes 2,923 cases, including all 554 extension-host cases. Strict types pass for 2,641 source files. Root Ruff, focused Wemake, and shared policy parity pass. Full dead code retains the same 22 missing application uses. Full Wemake retains the same six unrelated Codex findings. Shared policy `0.1.0a1` and SDK versions are unchanged. No live daemon, user database, remote service, or Git remote was changed.

Commands:

- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,923 passed, 26 warnings, 202.05 seconds.
- `.venv/bin/python -m pytest -q tests/extension_host/test_canonical_history_*.py tests/extension_host/test_observation_*.py tests/test_architecture_observation_schema.py`: 127 passed, three warnings, 3.72 seconds.
- `.venv/bin/python -m baqylau_dev check --gate types`, `--gate ruff`, `--gate parity`, `--gate deadcode`, and `--gate wemake`.
- Focused `flake8` checks pass for the changed production and test files. `git diff --check` passes. Plan validation passes for 10 phases, 53 unique task IDs, required fields, dependencies, and 83 local links. Status counts remain five phases in progress and five not started; 17 subtasks in progress, one done, and 35 not started.

Limits: This is a storage and reader-isolation change. There is no mixed canonical repository protocol, extension canonical codec, complete transform transaction, ordered step log, later-proposal storage, live runtime stamp, or application candidate-creation route yet. No active-head switch is exposed. Candidate test writes go directly through private SQLite fixtures and do not prove replay safety or package-owned E2E. Browser, Kitty, live harness, process-kill, power-loss, and production-size migration timing checks were not run. P04-T01 and all unfinished P01–P10 work remain open.

Next action: Add the typed mixed canonical repository and codec with the existing SDK fact models. Validate retained schemas, owner, scope, causes, and selected runtime inside complete interpretation transactions. Add ordered step records and retained later proposals. Preserve the current reader boundary. Then connect sources, checkpoints, mixed dispatch, and transforms while holding one runtime for the whole batch. P05 must publish complete scoped canonical and projection heads together; session candidates must cover the complete actor set. Adapters and Git packages remain in scope.

## Work record — mixed interpretation storage

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Schema 31 separates canonical histories but has no typed mixed writer or complete transform record. P04-T01 needs a repository boundary that preserves first acceptance, retains later proposals, and commits processing state with the final facts.

Task: Add complete interpretation models and an explicit repository protocol. Reuse the SDK fact models, core mapper, and pure transform helpers. Store exact step requests, applied or failed replies, final decisions, source links, and decoder state in one transaction. Keep workers and engine calls outside storage.

Outcomes:

- Main schema 32 adds `interpretation_journals` and `extension_translation_state`. The `interpretation_steps` view reads the ordered array from the one stored proposal. Documents are not copied into a second step table. The additive migration keeps legacy rows and does not invent old journals.
- `InterpretationRepository` defines six complete operations. The existing fact-storage provider composes its SQLite implementation. Reads work without an active worker. The engine does not yet consume it.
- A proposal records manager, runtime, history, exact scope, original raw ID and cursor, mode, and expected canonical head. Acceptance reads retained package declarations, settings, schemas, and actual original bytes under the write lock. It rejects stale runtime, input, state, and changed retries.
- Pure checks reconstruct the supplied raw, translation, and canonical steps. They preserve selected order and exact content, reject repeated transform owners, check schemas and causes, and require final output to match the trace. Intermediate and later logical proposals remain recorded. A failed transform retains its input and diagnostic. Unknown or failed empty decoder results cannot be relabeled as intentional drops.
- Core and extension facts share one canonical arrival order. First acceptance owns the stored body, cursor, and time. A later observation can link to that body while retaining its changed proposal. It cannot change the fact branch, exact scope, or extension owner. Stored core pages retain their existing no-source-links shape; core identity lookup and audit joins retain source links.
- Decoder state is keyed by owner, history, scope, and source identity. The transaction compares its full captured document and revision. An applied decoder reply advances the revision once. A failed call and an all-dropped raw batch do not. An exact retry returns no newly accepted fact and does not advance state.
- Verdict, journal, pending removal, state, facts, and links commit together. Replay writes require a registered non-default history and do not alter live pending work or live decoder state. There is still no application history creation or publication route.

Code areas:

- `extensions/models/interpretation_*.py`, `interpretations.py`, and `processing_input.py`.
- [Repository protocol](../../../repository/contract/interpretations.py), [SQLite implementation](../../../repository/impl/sqlite/interpretations.py), and its `interpretation_*` modules.
- `repository/impl/sqlite/schema.py` and `app/provider_fact_storage.py`.
- `tests/extension_host/interpretation_*.py`, `test_interpretation_*.py`, and independent `fixtures/main-schema-31.sql`.
- `tests/test_architecture_fact_mappers.py`, repository import checks, and exact typed-index registrations in `tests/test_architecture_api.py`.

Verification: 52 new extension-host cases and one mapper architecture case pass. They cover complete journals, raw and canonical operations, intermediate causes, repeated input, first-body retention, empty verdicts, runtime changes, invalid documents, missing causes, stale decoder state, core-only and mixed output, protected session finish, and replay isolation. SQL failures at each write and a real connection COMMIT failure retain the complete logical database. An independent populated schema-31 fixture survives migration. Failure after each of four actual migration statements and a deferred foreign-key COMMIT failure preserve the prior database and allow a clean retry.

The first full run had six failures. One test expected source links in the normal core page; it now checks core identity lookup. Five architecture checks found unnamed tuple records, raw intermediate dictionaries, untyped ID parameters, a duplicate string vocabulary, and forbidden names. The code now uses named immutable records, typed IDs, and the existing storage-result enum. Only the actual candidate index, retained package index, and graphlib cause index receive exact typed-registry declarations. No feature document or whole module is exempted. The existing pure core mapper gets one exact repository import; a separate architecture test keeps that mapper below services.

Evidence: Worktree based on `6a9e497`, Python 3.12.1, macOS, SQLite 3.51.0. Final full verification passes 2,976 tests, including all 606 extension-host cases. The focused interpretation set passes 53 cases; the combined affected architecture and storage set passes 67 cases. Strict types pass for 2,679 source files. Root Ruff, focused Wemake, and shared policy parity pass. Full dead code reports 32 missing application uses: the mixed repository adds twelve protocol/implementation findings, while two prior mapper/SDK helpers now have real consumers. Full Wemake retains six unrelated Codex findings. Shared policy `0.1.0a1` and SDK versions are unchanged.

Commands:

- `.venv/bin/python -m pytest -q tests/extension_host/test_interpretation_*.py tests/test_architecture_fact_mappers.py`: 53 passed, three warnings, 3.00 seconds.
- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,976 passed, 26 warnings, 198.62 seconds.
- `.venv/bin/python -m pytest -q tests/test_architecture_api.py tests/test_canonical_naming_banned.py tests/test_canonical_naming_identifiers.py tests/test_canonical_naming_vocabulary.py tests/test_raw_record_architecture.py tests/extension_host/test_interpretation_*.py`: 67 passed, three warnings, 11.57 seconds.
- `.venv/bin/python -m baqylau_dev check --gate types`, `--gate ruff`, `--gate parity`, `--gate deadcode`, and `--gate wemake`.
- Focused `flake8` covers the new models, repositories, provider, schema, fixtures, and changed architecture tests.
- Plan validation passes: 10 phases, 53 unique task IDs, required fields, dependencies, and 87 local links. Status counts remain five phases in progress and five not started; 17 subtasks in progress, one done, and 35 not started. `git diff --check` passes.

Limits: These are host repository and pure-model tests, not package-owned E2E. Supplied steps are checked, but the repository does not yet require a record for every eligible transform. P04-T02/T03 must implement complete selection, registry capture, worker failure conversion, and all core process cleanup checks before engine integration. Prior-fact selection, scoped page limits, complete producer checks, and the final storage review remain open. The draft 32 MiB journal limit and existing SDK content limits need P08 measurements. Old core translators can still depend on external reads. Journal content is not automatically redacted. Browser, Kitty, live harness, process-kill, power-loss, and production-scale migration checks were not run. No live daemon, user database, remote service, or Git remote was changed. No phase or additional subtask is complete.

Next action: Finish the storage review and its missing boundary cases. Then connect actual source reads and checkpoints and the mixed interpreter through one fixed runtime batch. Preserve the original observation transaction; do not advance a source checkpoint separately. Keep history publication closed until P05 can publish canonical and derived state together. Continue all unfinished P01–P10 work, including the adapters and Git packages.

## Work record — atomic source reads and storage review

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: The mixed storage review still needed prior-fact and scope-page boundary tests. Extension source reads also had no atomic checkpoint operation. Saving progress separately from original input could lose events after a failure.

Task: Close the P04-T01 storage review. Start P04-T02 with an explicit source repository protocol and a complete successful-read transaction. Reuse the SDK source models, retained declarations, original append, and existing transaction owner. Do not call workers or change the live daemon in this storage step.

Outcomes:

- P04-T01 is done. Fifteen new cases prove exact stored prior facts, rejection of changed prior bodies and acceptance times, strict page bounds, full scope isolation, mixed page order, and reads after owner removal. Complete transform selection and core process cleanup remain P04-T02/T03 work, not storage claims.
- `ExtensionSourceRepository` defines three methods. `SourceReadProposal` records complete request and reply evidence. `SourceCheckpoint` has an explicit initial state and a host revision. `SourceReadCommit` separates the first host time from stable call identity. The existing fact-storage provider composes the SQLite implementation; the engine does not yet consume it.
- Schema 33 adds `extension_source_reads` and `extension_source_checkpoints`. Source progress uses owner, full scope, and source identity, independent of runtime replacement. A committed source type cannot change under the same source identity. Changed positions advance the revision; unchanged empty replies do not.
- Runtime, owner, retained manifest, full settings, source declaration, schema, and captured progress are checked under the write lock. Shared runtime checks now serve original, interpretation, and source writes. No feature code runs in a transaction.
- Read evidence, exact original rows, pending input, and progress commit together. A failed write or COMMIT changes none of them. Exact retries retain first rows and time, do not requeue input, and return that call's original checkpoint without rewinding a later head. Old manager or runtime output is rejected, including an exact old retry.
- Empty reads can save progress without an event. Only committed nonempty reads send raw-work notices. Accepted calls and progress remain readable after restart or owner removal. Valid new runtimes resume the saved source position.
- P04-T04 is now marked in progress to reflect its existing schema-32 transaction subset. Application crash, reaction, and consumer-progress checks remain open after P04-T02/T03 integration.

Code areas:

- `extensions/models/source_reads.py`, `processing_package.py`, and the shared interpretation package context.
- `repository/contract/source_reads.py`, `repository/impl/sqlite/source_read*.py`, and `processing_runtime.py`.
- Original and interpretation acceptance modules, `repository/impl/sqlite/schema.py`, and `app/provider_fact_storage.py`.
- `tests/extension_host/*source_read*.py`, the three prior-fact/page test files, and independent `fixtures/main-schema-32.sql`.

Verification: The final focused set passes 56 cases: 41 source cases and 15 interpretation boundary cases. The source cases cover complete call retention, exact and changed retries, old retries after later progress, empty reads, invalid JSON and schemas, missing causes, undeclared types, changed source types, partial checkpoints, stale managers and runtimes, changed effective settings, stale revisions after repeated opaque positions, complete scope identity, reload resume, work notices, and restart reads after removal. Four write-failure cases and a real connection COMMIT failure prove full rollback. The populated independent schema-32 fixture retains core rows, mixed facts, journals, and decoder state through upgrade. Failure after each of the two migration statements and an actual deferred foreign-key COMMIT failure preserve the old database and allow retry.

Evidence: Worktree based on `6a9e497`, Python 3.12.1, macOS, SQLite 3.51.0. The focused 56-case run passes with three warnings in 6.17 seconds. Final full verification passes 3,032 tests, including all 662 extension-host cases, with 26 warnings in 192.94 seconds. Strict types pass for 2,700 source files. Root Ruff, focused Wemake, and shared policy parity pass. Full dead code reports 39 missing application uses, including seven new source protocol, implementation, and provider findings. Full Wemake retains six unrelated Codex findings. The source work adds no quality exemption and changes no SDK or policy version. Plan validation passes: 10 phases, 53 unique task IDs, required fields, dependency IDs, and 90 local links. Five phases are in progress and five are not started. Subtask counts are 18 in progress, two done, and 33 not started. `git diff --check` passes.

Commands:

- `.venv/bin/python -m pytest -q tests/extension_host/test_source_read*.py tests/extension_host/test_interpretation_prior.py tests/extension_host/test_interpretation_pages.py`.
- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`.
- `.venv/bin/python -m baqylau_dev check --gate types`, `--gate ruff`, `--gate parity`, `--gate deadcode`, and `--gate wemake`.
- Focused `flake8` covers the source models, repositories, shared runtime checks, changed interpretation modules, schema, provider, and all new test files.

Limits: These are host model and repository tests, not package-owned application E2E. No source plan, watch, deadline, worker call, or interpretation dispatch has been added to the engine in this step. Failed reads still need durable public diagnostics. The draft 8 MiB proposal bound and runtime costs need P08 measurement. Journals do not redact arbitrary secrets. Browser, Kitty, live harness, process-kill, and power-loss tests were not run. No live daemon, user database, remote service, or Git remote was changed. The full P01–P10 goal remains open.

Next action: Add the source coordinator through explicit injected contracts. Hold one registry read for the complete batch. Register watches before reading, merge extension paths with core paths, schedule declared deadlines without idle scans, and release removed source scopes. Capture checkpoints before calls and commit successful replies through `ExtensionSourceRepository`. Then connect ordered raw transforms, translation, canonical transforms, and complete interpretation commits while preserving core input reactions and process cleanup. Do not expose history publication before P05 can switch canonical and projection state together.

## Work record — engine source integration

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Source protocols and atomic source storage existed, but the daemon did not call source workers. Connecting only a read callback would omit watches, retained runtime ownership, deadlines, scope lifetime, and restart progress. This step connects those parts before adding mixed interpretation.

Task: Add explicit injected source processing and scope protocols. Compose them with the daemon's actual manager, registry, ledger, failure recorder, work queue, and source repository. Preserve core input subscriptions and stage behavior. Verify actual source ingestion through a private application process. Do not add adapters or Git feature code to the host.

Outcomes:

- `ExtensionSourceProcessing`, `ExtensionSourceBatch`, and `ExtensionSourceWatches` define the engine boundary. `ExtensionSourceScopes` and `ExtensionScopeRegistry` define host scope selection and leases. Implementations declare their protocols explicitly. The SDK feature contracts are unchanged.
- `SourceRuntime.capture_batch()` retains one registry read through source work, existing core translation, and core reactions. It checks the actual active manager/runtime. Publication remains busy until context exit. Initial restore failure retains an explicit core-only path. Worker calls never run under the registry mutex or inside a SQLite transaction.
- The provider selects every running actor, the installation scope, and explicit host scope leases. Repository input can be stored without a session in host integration tests. Views and jobs do not yet consume repository/workspace leases. First and last lease changes notify source processing; callback failure does not leak a lease.
- Describe, read, and release calls use captured settings and separate ledger grants. The host validates both sides. Plans are checked before native watches are set. Reads start from actual stored checkpoints and commit complete successful replies through `ExtensionSourceRepository`. Originals remain exact and source progress survives valid runtime replacement.
- Native subscriptions keep core and additional path sets separate. Lexical links and physical targets remain selected. Missing parents, file replacement, directory children, directory replacement, symlink replacement, and direct writes have tests. A plan cannot select the filesystem root through a direct path, missing top-level path, or symlink target.
- `EXTENSION_SOURCES` provides an independent deadline notice. A simultaneous core source notice absorbs it once. Exact timer replacement and cancellation leave other producers and received notices unchanged. Timer-only passes do not scan harness inputs, refresh healthy source plans, postpone future timers, or poll idle watched files.
- A source reads at most four pages per pass by default. Continuation uses 0.01 seconds; failure retry uses one second; calls retain the current 30-second worker deadline. Stop is checked between pages and scopes. Failed calls preserve progress and do not stop unrelated core stages. A successful plan retry retains the pending file read.
- Removed source IDs and whole scopes receive checked release calls. Pending cleanup keeps its state and retry. Completed releases are not repeated. Scope reentry waits for pending cleanup. Runtime replacement clears only data-only plans; full worker deactivation and closure remain owned by the manager.

Code areas:

- `extensions/source_processing_contract.py`, `source_scope_contract.py`, `source_runtime.py`, `source_scopes.py`, and `models/source_processing.py`.
- Source selection, calls, batch, planner, plan state, reader, and resources under `extensions/source_*.py`.
- `app/provider_extension_sources.py`, `app/provider_engine.py`, `engine/worker.py`, `engine/source_processing.py`, `engine/work_batch.py`, and `engine/extensions_boundary.py`.
- `core/input_paths.py`, `core/input_events.py`, and `core/work_queue.py`.
- Source probes, engine and daemon fixtures, and source processing/runtime/scope/engine/daemon tests under `tests/extension_host/`.
- `tests/additional_input_fixture.py`, `test_additional_input_events.py`, `test_input_paths.py`, `test_work_queue_owned_deadlines.py`, and affected architecture and boundary tests.

Verification: The new set has 63 cases. Sixty-one focused cases pass for source calls, watch-before-read order, captured grants, exact stored call evidence, bounded pages, source and plan failures, deadline changes, idle behavior, source release, scope release/reentry, manager mismatch, runtime publication, stop, active actors, lease bounds, native watches, and engine stage order. Two private-daemon cases use an SDK-only backend package, real offline dependency environments, the public lifecycle API, native input delivery, and a read-only test view of SQLite. They prove complete-line capture, atomic file replacement, source-free package restart, resume from the saved position, and disable. They are host integration tests, not package-owned public-API E2E.

Corrections from verification: A symlink-to-root test first failed because watch validation omitted the physical target. Validation and engine watches now share path expansion. An idle-source test first failed because another source's timer could poll watched files. Read selection now distinguishes file notices from timer work. The first full suite passed 3,094 cases and found one parameter-naming violation; `InputEvents.watch_files` now names its typed argument `input_group`. Test fixtures also use the actual closed scope models and shared type/style rules. No check was weakened.

Evidence: Worktree based on `6a9e497`, Python 3.12.1, macOS, SQLite 3.51.0. Main schema remains 33. The focused run passes 61 cases with three warnings in 4.73 seconds. The separate-daemon run passes two cases with two warnings in 25.11 seconds. The final full run passes 3,095 cases, including all 703 extension-host cases, with 26 warnings in 251.72 seconds. Strict types pass for 2,733 files. Root Ruff, focused Wemake, and shared policy parity pass. Full dead code reports 36 missing application uses, down from 39: the source store and provider now have callers, while the explicit scope lease still lacks its future view/job consumers. Full Wemake has the same six unrelated Codex findings. No SDK version, policy version, or quality exemption changed. Plan checks and `git diff --check` pass.

Commands:

- `.venv/bin/python -m pytest -q` with the source processing, failure, scope, runtime, session-scope, engine, input-path, native-input, and owned-deadline test files listed above: 61 passed.
- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q tests/extension_host/test_source_daemon.py`: two passed.
- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`.
- `.venv/bin/python -m baqylau_dev check --gate types`, `--gate ruff`, `--gate parity`, `--gate deadcode`, and `--gate wemake`.
- Focused `flake8` covers all new source modules, changed engine/input/provider modules, and new tests. The full naming, architecture, and Python checks retain their normal rules.

Limits: Source ingestion is connected, but the mixed interpreter is not. Extension originals remain pending and do not appear as extension facts or frontend entries. Raw and canonical transforms, complete eligible-step journals, mixed reactions, public diagnostics, and full C06–C10/C21 acceptance remain open. Structured source failures record a coalesced code, not a complete durable diagnostic document. Runtime retirement uses lifecycle deactivation and worker closure, not individual old-runtime source releases. Call/page limits and the 1,000-scope limit are draft bounds; complete pass fairness and performance need measurement. Trusted packages are not OS-sandboxed, and ordinary stored documents do not redact arbitrary secrets.

No live user daemon, user database, remote service, or Git remote was changed. Browser, Kitty, live harness, process-kill, power-loss, and complete package-owned E2E checks were not run. Five phases remain in progress and five are not started. Subtask counts remain 18 in progress, two done, and 33 not started. The full P01–P10 goal remains active.

Next action: Route mixed pending originals through the retained runtime. Apply every selected raw transform, select the declared core or extension translator, then apply canonical transforms before the complete interpretation transaction. Preserve failed input, stable identities, decoder state, core input reactions, and cleanup. Reuse existing SDK processors and schema-32 storage; do not add a second pipeline codec or feature-specific host path. Continue the remaining plan through the external adapters and Git packages.

## Work record — mixed engine interpretation

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Source workers could store originals, but extension originals remained pending. Storage could check supplied transform steps but did not reject every omitted eligible transform. The engine still used the core-only translation path.

Task: Connect mixed pending input to the retained runtime, existing pure worker capabilities, and complete interpretation transaction. Keep core decoding and post-commit input reactions behind an explicit engine protocol. Preserve original input and complete failure evidence.

Outcomes:

- `ExtensionProcessing` captures one `ExtensionProcessingBatch`. The batch composes source reads with `interpret_pending`. `ProcessingRuntime` replaces `SourceRuntime`; it does not acquire a second independent runtime. The engine retains the same registry read through sources, interpretation, and core reactions.
- `CoreInterpretation` separates core translation from post-commit reactions. `TranslationPhase` implements it. Existing harness translation and consistency checks remain in use. The mapper retains original raw links and exact accepted cursors and times. Only newly accepted core facts reach core input reactions. Required finish output still releases both translator and source state.
- The mixed pending queue is read in pages of 100. Each original uses the current accepted history head. Active dependency order and declared types/scopes select transforms. Added output moves forward. All-dropped raw input does not call a decoder or canonical transform with an empty batch.
- The source owner's decoder receives recorded bytes, original metadata, captured settings, and actual scoped decoder state. Raw source documents are checked again before the decoder. Complete replies are checked before their facts or state can be accepted. Failed calls retain typed rejected replies when available and store a bounded failure message without copying arbitrary exception text.
- Canonical transforms use the existing SDK processors. Shared pure checks cover document declarations, scope, original core references, cause existence and cycles, and first-acceptance identity. An invalid extension result leaves its whole preceding input unchanged. The complete transaction repeats the checks, writes the journal and facts, updates decoder state, and removes pending work together.
- Storage now checks every eligible raw and canonical stage, not only supplied steps. Scope and type mismatches select no call. It rejects omitted eligible stages, repeated owners, and reversed order. Required core start and finish fact identity and order remain protected at the canonical stage.
- Preflight failure has its own typed journal step. Content above the 1 MiB worker limit keeps its original bytes and receives a failed verdict with exact length and digest. A disabled source owner receives an unknown verdict. Storage recomputes the preflight reason and rejects fabricated failures. These originals do not block later input. An explicit history action is still required for later reprocessing.

Current limits and open work:

- Core raw changes are rejected, with a failed step and unchanged input, until source lifecycle input can be protected. This is a temporary limit. It does not complete the requested core raw replace/drop/insert behavior.
- A canonical transform receives at most the first 1,000 facts from its exact scope. This is not a complete long-history state, and the SDK snapshot does not yet report page coverage. No prior facts are fetched when no active package has a canonical transformer. Prior-state byte limits and aggregate journal admission need explicit rules and tests.
- Per-call worker limits exist, but complete pass-time and fairness bounds remain open. Complete multi-extension order, additions across owners, invalid canonical results, timeout, core raw lifecycle, crash boundaries, and mixed consumer progress need application conformance tests. Existing lower-level tests do not close these checks.
- Extension projectors, public mixed audit routes, source failure documents, dashboard views, Kitty views, and package-owned release E2E remain open. The four private-daemon cases use read-only test inspection of SQLite, not a public diagnostic route. No adapters or Git feature code was added to the host.

Code areas:

- `extensions/processing_contract.py`, `processing_runtime.py`, `processing_batch.py`, and `interpretation_*.py`.
- `extensions/models/interpretation_coverage.py`, `interpretation_unavailable.py`, `interpretation_facts.py`, and the existing trace, step, selection, and verdict models.
- `engine/interpret/translation.py`, `engine/interpret/extension_mapping.py`, `engine/worker.py`, `engine/source_processing.py`, and `app/provider_extension_sources.py`.
- `repository/impl/sqlite/interpretation_facts.py` now keeps only SQL cause lookup and explicit aliases to shared pure rules. No schema change is needed; main schema remains 33.
- `tests/extension_host/test_interpretation_coverage.py`, `test_interpretation_unavailable.py`, `test_processing_pipeline.py`, `test_processing_core.py`, `test_processing_daemon.py`, and their typed fixtures. Existing source engine and daemon tests now check the mixed path.

Verification: 23 new cases cover six transform-coverage cases, five preflight cases, seven complete pipeline cases, three core adapter cases, and two new private-daemon cases. The focused interpretation/pipeline/core/source engine/runtime run passes 102 cases. All four private-daemon cases pass. They use actual external SDK-only backend processes, private offline dependencies, lifecycle API calls, real input notices, and actual fact storage. Checks include changed and dropped raw input, wrong-schema fallback, canonical suppression and insertion, retained intermediate causes, failed decoding, new-only core reactions, cleanup, repeated logical facts, restart without retranslation, and continued processing after a forbidden pure-lane host call.

Corrections from verification: Earlier engine tests expected the old core-only callback; they now check complete commits before core callbacks and retained publication ownership. Architecture checks now admit only the exact new engine contract/model/mapper modules and typed package/fact indexes. The cause graph exemption moved with its pure function. No feature document dictionary or missing application consumer is exempted. A full run passed 3,117 cases and found one prohibited word in a new core mapper comment. The comment was corrected without changing behavior.

Evidence: Final full verification passes 3,118 tests, including all 726 extension-host cases, with 26 warnings in 198.54 seconds. Strict types pass for 2,758 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. All 10 phases, 53 task records, dependencies, and 96 local plan links pass validation. Full Wemake retains six unrelated Codex findings. Full dead code reports 24 findings: 22 missing application uses and two serialized preflight fields. No new dead-code exemption was added. This run excludes Kitty and `tests/e2e`; no browser or Kitty check was run. The live user daemon, database, and remote services were not changed.

Status result: P04-T02 and P04-T04 remain in progress. P04-T03 is now in progress. No phase or new subtask is complete. The plan has 19 in-progress subtasks, two done, and 32 not started. The complete P01–P10 goal remains active.

Next action: Complete the safety and admission rules named above, then add multi-extension and private-process failure conformance. Expose mixed processing diagnostics in P04-T05. Continue to P05 data and commands, the web and Kitty hosts, release E2E, and the external adapters and Git packages. Do not treat the connected pipeline as release completion.

## Work record — bounded prior state

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Canonical transforms received the first 1,000 scoped facts with no byte bound or completeness flag. Large bodies could make the captured page too large. A transform could not distinguish an empty complete scope from omitted history.

Task: Give prior-state capture an explicit repository protocol method. Bind it to one history, exact scope, and accepted head. Bound the complete encoded snapshot and verify completeness claims during interpretation acceptance.

Outcomes:

- `PriorStateRequest` has strict count and byte limits. Live processing uses the default maximum of 1,000 facts and 1 MiB. A host caller can request smaller bounds, down to one fact and 128 bytes.
- `InterpretationRepository.capture_prior_state` performs one SQL read transaction. Unknown histories and stale heads fail. It uses scoped cursor order and metadata checks before decoding bodies. It stops at the first omitted fact and does not skip ahead.
- `CoreStateSnapshot.complete` reports coverage. True means all scoped facts through the selected head are present. False makes no completeness claim. The larger false header is included in byte accounting. JSON escaping and UTF-8 bytes are counted. Metadata checks can return a conservative incomplete prefix.
- The processing batch uses the new repository method. It still avoids prior reads when no active package has a canonical transform capability. An incomplete snapshot is passed explicitly; it is not silently replaced with a claim that the scope is empty.
- Interpretation acceptance checks each supplied fact and then any complete claim. The count check reads no more than the supplied count plus one. It remains inside the complete write transaction and also applies to failed transform records. A false complete claim leaves the entire database unchanged.
- Older journal JSON without the flag still decodes with `complete=false`. No SQL migration is required; main schema remains 33. The API is still an unfrozen draft. Older alpha worker wheels must be rebuilt before testing this new field.

Code areas:

- `packages/extension-api/src/baqylau_extension_api/models/canonical.py`.
- `extensions/models/interpretation_snapshot.py` and `extensions/processing_batch.py`.
- `repository/contract/interpretations.py`, `repository/impl/sqlite/interpretations.py`, `interpretation_snapshot.py`, and `interpretation_acceptance.py`.
- `tests/extension_host/test_interpretation_snapshot*.py`, `test_processing_prior.py`, and their fixtures; `tests/extension_api/test_prior_snapshot.py` and the external worker example.

Verification: 47 new cases cover 40 host cases and seven SDK cases. They check empty and mixed state, accepted metadata, all scope identity parts, history isolation, stale and unknown heads, strict bounds, the 1,000/1,001-fact boundary, byte boundaries, UTF-8 and escaping, an oversized first body that is never decoded, a large middle body, complete claims, rollback, failed transforms, older journal data, and two real connections with an overlapping write. Two batch cases use production processing and real storage with controlled local capabilities. An existing external-worker case now proves that both completeness values reach the worker. The focused set passes 49 tests; local capability mocks are not worker evidence.

Evidence: Final full verification passes 3,165 Python tests, including all 766 extension-host cases, with 26 warnings in 253.18 seconds. Strict types pass for 2,768 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. The plan checks pass for 10 phases, 53 task records, dependencies, and 98 local links. Full dead code reports 26 findings: 24 findings for missing application uses and two serialized preflight fields. The scoped-page protocol and implementation now have no application caller because prior capture uses its own bounded read. Their future public diagnostic consumer remains open; no exemption was added. Full Wemake retains the same six unrelated Codex findings. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No live user daemon, database, or remote service was changed.

Status result: P04-T03 remains in progress. No phase or subtask is complete from this change. The plan remains at 19 in-progress subtasks, two done, and 32 not started.

Next action: Complete aggregate journal admission and pass-time bounds. Resolve the required lifecycle split before permitting core raw changes. Continue multi-extension, process-failure, and mixed-consumer checks, then public processing diagnostics. Web, Kitty, release E2E, and both feature packages remain open.

Pending design choice: Some harness decoders emit required session-start state with normal activity and keep mutable correlation state. Calling those decoders twice to compare lifecycle output is not safe. The proposed change separates required session lifecycle processing from extension-controlled activity through the harness protocol. The user has been asked to approve that change or request its detailed design first. No such harness change was made in this work. Continue independent open tasks while that decision is pending.

## Work record — mixed core consumption

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: The core reaction loop read a SQL page that excluded extension facts. An extension-only tail could not advance its checkpoint. A caught core write error could also let later facts advance past failed work.

Task: Read the ordered mixed stream through an explicit narrow protocol. Apply only core facts to core consumers. Advance extension-only progress in a checked transaction. Stop at a failed core write and retain all later work for retry.

Outcomes:

- `CanonicalFactReader` supplies mixed live pages through the existing interpretation store. Application providers give the core reaction loop this narrow read contract. No running worker is required for stored reads.
- `private_committed()` preserves accepted core cursor and time and rejects extension documents. Both input reactions and the later core loop use this mapper.
- `advance_past_extensions()` validates a positive 64-bit target, checks its live extension branch, and rejects any unprocessed core fact in the gap. Validation and checkpoint write share one transaction. Exact and older retries do not write again.
- Extension-only consumption creates no display row, session, actor, or reader notice. Candidate facts remain outside live progress. The core checkpoint is not proof that an extension projector or observer has completed.
- A core write failure is audited and raised. The engine retains failed canonical work for its existing one-second retry. Earlier committed changes and their actor notices remain valid; later work is not consumed.
- Legacy core rebuild reads both branches without repeating side effects. It still clears live core views and does not complete candidate history rebuilds. Core side effects retain their old before-write order; a failed write retry can repeat those effects.

Code areas:

- `repository/contract/interpretations.py`, `session_data_protocols.py`, and `repository/impl/sqlite/session_data_progress.py`.
- `app/loop_resources.py`, reaction-loop providers, `engine/react/dependencies.py`, `loop_runtime.py`, and `loop_materialization.py`.
- `extensions/mapper/core_events.py`, `engine/interpret/extension_mapping.py`, and core read-model writers.
- `tests/extension_host/test_core_progress*.py`, `test_mixed_react*.py`, and their fixtures; SDK mapper and affected core tests.

Verification: This step adds 31 cases: 30 host cases and one SDK rejection case. It also strengthens 43 existing mapper round trips to use the production committed mapper. Tests cover installation, repository, and session facts; pages over 500 facts; later core work; candidate exclusion; exact retry; restart; core failure; and a committed prefix before failure. Real SQLite INSERT, UPDATE, and COMMIT failures preserve the complete database. An actual private daemon consumes a source-to-fact extension tail without display rows and retains progress after disable and restart. Seeded unit read fixtures are not worker evidence. The focused set passes 181 tests, including that daemon case.

Evidence: Full verification passes 3,196 Python tests, including all 796 extension-host cases, with 26 warnings in 240.46 seconds. Strict types pass for 2,775 files. Root Ruff, changed-file Wemake, and shared policy parity pass. Full Wemake retains six unrelated Codex findings. Full dead code reports 28 findings: 26 missing application uses and two serialized preflight fields. The old core-only page protocol and implementation have lost their application caller; no exemption was added. Main schema remains 33. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No live user daemon, database, or remote service was changed.

Status result: P04-T04 remains in progress. No phase or additional subtask is complete. There are still 19 in-progress subtasks, two done, and 32 not started.

Next action: Bound aggregate mixed page reads without leaving a large first fact stuck. Complete journal admission, pass-time bounds, multi-extension and process-failure checks, and public diagnostics. Resolve the pending harness lifecycle design separately. Extension projections, web, Kitty, release E2E, adapters, and Git remain open.

## Work record — mixed page content limits

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Mixed live and scoped pages fetched every body up to their row-count limit before decoding. A page of large extension documents could transfer much more content than intended. Returning an empty page when the first fact is too large would leave durable consumer progress stuck.

Task: Select a content-bounded ordered prefix in SQL before returning bodies to Python. Keep the count limit, history and scope indexes, and consistent head read. Return an oversized first fact alone so that readers can continue.

Outcomes:

- `interpretation_pages.page_rows()` uses two fixed, parameter-bound SQL queries for live and exact-scope reads. The existing mixed repository methods use this helper. No public method, schema migration, SDK model field, or dependency was added.
- The default budget is 4 MiB of stored UTF-8 payload, scope, and extension metadata. SQL selects at most the requested number of metadata rows through the appropriate index, then computes their cumulative content size. Only the ordered prefix returns complete bodies to Python.
- A first fact above the budget returns alone. A later small fact never passes an omitted large fact. The next read starts after the last returned cursor. Count bounds remain in effect, and a short nonempty page does not mean the stream is finished.
- History, exact scope, and starting cursor filter before byte accounting. Candidate and foreign-scope facts cannot reduce the selected page. Bodies and the history head retain one read transaction during a concurrent commit.
- This is a stored-content budget, not a hard bound on complete response encoding, SQLite memory, or Python memory. One oversized fact can exceed it. Prior-state capture keeps its separate hard bound and completeness contract. Aggregate journal admission and complete pass-time limits remain open.

Code areas:

- `repository/impl/sqlite/interpretation_pages.py` and `interpretation_reads.py`.
- `extensions/models/interpretation_reads.py`.
- `tests/extension_host/interpretation_page_fixture.py`, `test_interpretation_page_bytes.py`, `test_interpretation_page_limits.py`, and `test_interpretation_page_snapshot.py`.

Verification: 22 new host cases cover exact byte boundaries, UTF-8 and escaping, independent stored-text size measurement, row limits, large middle and first facts, excluded-body decoder calls, actual default-size pages, scope/history isolation, and core draining through small pages. Two cases use a second real SQLite connection to commit between body and head reads. Two cases inspect actual query plans for history and scoped indexes. The focused page and mixed-reaction run passes 43 tests. These are storage and consumer tests with seeded facts, not package-owned feature E2E.

Evidence: Final full verification passes 3,218 Python tests, including all 818 extension-host cases, with 26 warnings in 234.04 seconds. The focused set, root Ruff, changed-file Wemake, strict types for 2,780 files, shared policy parity, and `git diff --check` pass. All 10 phases, 53 task records, dependencies, and 102 local plan links pass validation. Full Wemake retains six unrelated Codex findings. Full dead code retains 28 findings: 26 missing application uses and two serialized preflight fields. No exemption was added. Main schema remains 33. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No live user daemon, database, or remote service was changed.

Status result: P04-T04 remains in progress. No phase or additional subtask is complete. The plan remains at 19 in-progress subtasks, two done, and 32 not started.

Next action: Complete aggregate journal admission and pass-time bounds. Preserve a complete explanation when work exceeds a limit; do not silently omit selected transforms or truncate accepted evidence. Continue multiple-extension and process-failure checks, public diagnostics, P05 consumers, both frontend hosts, release E2E, adapters, and Git. The harness lifecycle protocol decision remains separate and pending.

## Work record — cooperative mixed raw scheduling

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: The engine repeatedly called mixed interpretation until its pending queue was empty. A steady stream could delay core reactions and keep an old runtime retained. The batch already checked a stop predicate between originals, but the engine had no cooperative yield or owned continuation at this boundary.

Task: Start no more than one mixed raw page per engine pass. Use monotonic elapsed time to stop before another original when an interval ends. Keep complete journals and transactions intact. Schedule remaining RAW work explicitly, then run core reactions and release the retained runtime.

Outcomes:

- `engine.mixed_processing.read_mixed()` starts one batch with a one-second cooperative interval. The host protocol has separate application-stop and keyword-only timed-yield predicates. The actual batch limits its read to 100 originals, checks stop before every original, and checks timed yield only after one complete original.
- A slow page read cannot cause repeated zero-progress yields. Timed progress cannot override application stop. A positive result requests a RAW continuation after 0.01 seconds. An empty result clears only this owned timer, even after interval expiry. Application stop does not schedule more work.
- The engine continues to core reactions before releasing the selected runtime. A RAW continuation does not scan core or extension sources. Later work selects the current runtime through the normal engine boundary.
- No journal, interpretation transaction, or worker call is interrupted by the predicate. Accepted originals are not repeated on continuation. A commit error uses the existing one-second failure retry and retains pending input.
- The no-runtime fallback keeps its existing core drain. An active runtime with no enabled packages still uses mixed processing. Source passes, single worker calls, complete interpretations, and core reaction draining can exceed this interval. This is not a hard complete-pass deadline.
- The journal review confirms that the current 32 MiB proposal check runs after the pipeline builds its steps. This scheduling change does not implement aggregate journal admission or make an oversized journal acceptable. No journal limit or evidence rule was weakened.

Code areas:

- `engine/mixed_processing.py`, `engine/worker.py`, `extensions/processing_contract.py`, and `extensions/processing_batch.py`.
- `tests/extension_host/mixed_processing_fixture.py`, `test_mixed_processing_slice.py`, `test_mixed_processing_admission.py`, `test_mixed_processing_scheduler.py`, and `test_mixed_processing_daemon.py`.
- The existing `tests/extension_host/test_source_engine.py` now checks both independent source and raw deadlines.

Verification: This step adds 17 host cases. Local tests use the actual engine, retained registry, source storage, and interpretation transactions with controlled source/core calls. They verify complete acceptance before yield, retained pending order, release while input remains, unchanged journals on continuation, first-original progress after expiry, no idle work after an empty read, wall-clock independence, application stop before and after the first original, and normal commit-failure retry. Scheduler cases check one page before reactions, exact expiry, and the actual queue's owned continuation and unrelated retry. A private daemon uses an external SDK-only worker to complete 101 original lines without another file change, then verifies core progress and unchanged journals after disable and restart. These daemon checks are host integration evidence, not package-owned adapters or Git E2E.

Evidence: Final full verification passes 3,235 Python tests, including all 835 extension-host cases, with 26 warnings in 221.45 seconds. The focused engine, daemon, and architecture set passes 34 tests. Strict types pass for 2,786 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. All 10 phases, 53 task records, dependencies, and 104 local plan links pass validation. An earlier full run found a parameter-naming violation; it was corrected without an exemption. Full Wemake retains six unrelated Codex findings. Dead code retains 28 findings: 26 missing application uses and two serialized preflight fields. No exemption was added. Main schema remains 33. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No live user daemon, database, or remote service was changed.

Status result: P04-T02 and P04-T04 remain in progress. No phase or additional subtask is complete. The plan remains at 19 in-progress subtasks, two done, and 32 not started.

Next action: Implement journal admission without omitting selected transforms or losing complete failure evidence. Complete source-pass fairness and single-interpretation time limits. Continue multiple-extension and process-failure conformance, public diagnostics, P05 data and commands, web and Kitty hosts, release E2E, adapters, and Git. Keep required core lifecycle changes paused until the harness protocol decision is approved.

## Work record — journal admission investigation

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: The full proposal byte check follows all pipeline calls. The same admitted facts can appear in a reply, in later requests, and again as final output. This can reject a valid reply and leave its original pending.

Task: Reproduce the failure with real pipeline and schema validation. Define the storage and admission change, its ownership rules, and its verification requirements. Obtain user approval before the storage refactor, as required by the repository instructions.

Outcomes: A temporary local pipeline case produced 20 valid canonical additions. Its typed reply was 20,984,583 bytes, below the current 33,554,432-byte transport limit. The complete proposal failed the 33,554,432-byte journal check. One original remained pending, the accepted canonical head remained zero, and the canonical capability had been called once. The proposed design stores shared typed bodies once and uses versioned references in ordered steps. It checks admission before calls and result application and keeps explicit evidence when a rejected body cannot be retained.

Code areas: Inspected `extensions/models/interpretations.py`, pipeline/call/step models, and SQLite interpretation writes. No production, test, or schema code was changed. The design and reproduction details are in the [journal admission decision](../journal-admission.md).

Verification: The case used `processing_pipeline_fixture.installed()` with distinct generated insertion keys and declared schema, owner, scope, and cause. `PipelineCase.run()` reached the proposal byte check after the canonical result checks. It used a controlled local capability, not a worker process. The temporary database and artifacts were removed by their context. No live user daemon, database, or remote service was changed. The previous code verification remains 3,235 passing Python tests; it was not rerun for this documentation-only investigation.

Evidence: The observed exception was `ValidationError` with the message `interpretation proposal exceeds its encoded size limit`. The decision note records the exact inputs, measured sizes, current paths, proposed storage boundary, failure retention rules, and required checks. The user has been asked to approve the internal storage refactor or request the detailed design first.

Status result: P04-T04 remains in progress. Approval for this storage change is pending. This is not a claim that all independent extension work is blocked. No phase or additional subtask is complete.

Next action: Resolve the journal storage design decision before changing its protocol, codec, or schema. The required core lifecycle split has its own pending approval. Continue the full P01–P10 scope; do not remove web, Kitty, adapters, Git, or package-owned E2E from the goal.

## Work record — normalized journal storage

Date: 2026-09-19

Owner: Claude Code

Status: in_progress

Context: The approved shared-body decision had no implementation. One valid canonical reply above the expanded proposal limit could not publish, and the same facts were copied into the reply, later requests, and the final fact list.

Task: Store each distinct typed body once and refer to it from ordered steps. Admit exact write size at the normalized boundary. Keep codec version 1 journals readable and exactly retryable.

Outcomes: Schema 35 adds `interpretation_bodies`, `interpretation_journal_steps`, and `interpretation_journal_bodies`; `interpretation_journals` gains `codec_version`. `BodyRef` and `BodyStore` intern each distinct value by SHA-256 digest with its kind and exact encoded length. Stored step models replace facts, raw inputs, content bundles, prior snapshots, decoder state, and documents with typed references. The write transaction normalizes the proposal, checks header, step, and distinct-body bytes against the journal limit, and writes bodies once with ownership links. The read path expands codec 2 references and verifies digests and lengths. Codec 1 rows keep the inline proposal and the existing `interpretation_steps` view. `JournalAdmission` checks the call budget before each extension worker call and admits each normalized step before its result changes the candidates. A `LimitStep` records a call that was not invoked; a `RejectedStep` records a reply that was not applied with its exact size and digest. Required core steps are never optional. `validate_admission()` replays every claim and rejects a false limit or rejection record.

Code areas: `extensions/models/interpretation_bodies.py`, `interpretation_storage_steps.py`, `interpretation_normalization.py`, `extensions/models/interpretations.py`, `repository/impl/sqlite/schema.py`, `interpretation_writes.py`, and `interpretation_reads.py`.

Verification: `tests/extension_host/test_interpretation_normalization.py` covers the reproduced 20-addition reply (expanded above the limit, normalized below it), one body version for a repeated fact, a rejected journal with pending input and no rows, and the codec 2 read path. `tests/extension_host/test_interpretation_admission.py` covers an exhausted budget with no worker call, an oversized reply that keeps the earlier facts and records its exact size and digest, false limit and rejection claims, and required core facts under a small budget. Legacy journal, migration, rollback, and extension host selections pass. Strict types and root Ruff pass for the changed files. The `tests/` selection excluding the extension host passes 2,409 tests with 14 warnings in 156.19 seconds, excluding Kitty and live `tests/e2e`. Five frontend-build environment cases fail because this worktree has no installed web dependencies or built bundle; they do not touch the changed code. The full Wemake gate passes with zero findings after the stored-step codec and the audit read were split into staged modules. The full-tree Ruff gate and strict types pass for 2,861 files.

Evidence: The reproduced case produced 21 final facts, one accepted set, an exact read-back, and equal body and ownership-link counts. The old inline journal cases still read and retry without a write. Migration tests cover schema 32 and 33 upgrades and the new column. The exhausted case records two limit steps with no worker invocation; the oversized case records a rejection whose digest matches the received reply bytes. No live user daemon, database, or remote service was changed.

Status result: The approved storage and admission decisions are implemented. Bounded public reads and private-process acceptance remain open. P04-T04 remains in progress.

Next action: Add bounded public reads and the private-process drop, replace, insert, rollback, and restart cases.

## Work record — ordered workers and process failure

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Earlier processing tests mostly used one package or local capability calls. They did not prove complete processing across separately installed transform workers in one daemon.

Task: Add real process cases for declared order, generated input, complete suppression, invalid later results, and worker exit. Use public activation and private data. Keep host integration evidence separate from package-owned feature E2E.

Outcomes:

- Each case installs a source package and two transform packages outside the checkout. Each backend runs in its own locked private environment. Copied feature modules import only the installed SDK, Pydantic, standard modules, and their own package. The host code has no feature branch.
- Two cases activate the transforms in opposite orders. An explicit load-order constraint overrides lexical owner order. Raw additions reach the later raw worker, and canonical additions reach the later canonical worker. Exact final document order and complete five-step journals agree. Original bytes and journals remain unchanged after restart.
- Raw and canonical all-dropped cases produce a suppressed verdict, omit later calls with no selected input, and permit the next original and core checkpoint to complete.
- A later raw reply includes a valid replacement and invalid source bytes. The host rejects the whole reply and retains its typed evidence. A later canonical reply includes valid replacements and a missing cause. Its full result is rejected while the first worker's changes and addition remain accepted.
- A worker exits after its accepted raw reply. The recorded process ID is no longer live. Later canonical work and the next original still complete. The same case fails during daemon shutdown: a failed deactivation keeps the entire runtime resource stack open. The fixture must force termination after its 15-second stop deadline. This is a confirmed P03-T05 defect, not a passing C10 case.

Code areas: New `tests/extension_api/ordered_transform_operations.py`, `ordered_raw_example.py`, `ordered_canonical_example.py`, and `ordered_transform_example.py`; new `tests/extension_host/ordered_processing_fixture.py`, `test_ordered_processing_daemon.py`, and `test_ordered_processing_failures.py`. No production, protocol, or SQL schema change was made. See the [cleanup design decision](../worker-cleanup.md).

Verification: The seven-case daemon run reports six passed and one failed in 67.73 seconds. All event-processing assertions pass in the failing case; the existing stop assertion fails. Twenty-seven naming, protocol, canonical, repository, and retirement cases pass. Strict types pass for 2,793 files. Root Ruff, changed-file Wemake, and shared policy parity pass. Full Wemake retains six unrelated Codex findings; dead code retains 28 findings. The new test has no skip, expected-failure marker, or regression exclusion. No quality rule was weakened.

Evidence: The full Python run reports 3,241 passed and one failed, with 26 warnings in 271.10 seconds. The same shutdown regression fails after all of its processing assertions pass. Command: `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`. The 10 phases, 53 tasks, dependencies, and 110 local links pass validation; `git diff --check` passes. Worktree base is `6a9e497`, main schema is 33, SDK and quality policy remain `0.1.0a1`, and the platform is macOS with Python 3.12.1. Read-only SQL inspection supplies journal and fact evidence; public mixed diagnostics remain open. No package-owned release runner, browser, Kitty, live harness, user daemon, user database, or external remote was used. After the focused failed test, no worker executable from that test's private root remained live.

Status result: No phase or additional subtask is complete. P03-T05 and P04-T02 through P04-T04 remain in progress. The plan retains 19 in-progress subtasks, two done, and 32 not started. The worker cleanup design has been submitted for approval; journal storage and core lifecycle decisions are also pending.

Next action: Resolve the cleanup policy without discarding unresolved-job evidence or weakening the process regression. Complete journal admission and core lifecycle protection under their accepted decisions. Continue public diagnostics, P05 data and commands, dashboard and Kitty integration, release checks, adapters, Git, and package-owned E2E.

## Work record — harness lifecycle protocol

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: Core raw changes are still rejected because one input can contain both required session state and activity. Stateful harness translators cannot safely process a complete original and then process its changed activity again.

Task: Implement the approved protocol split without changing the current production journal format. Keep the existing complete pass until the new required and activity steps have independent storage checks.

Outcomes: `HarnessTranslator` and `CoreTranslator` now accept an explicit `translation_stage`. All three harnesses and the core translators implement it. The required pass preserves session starts, lead actor starts, and session finishes without processing tool, turn, or compaction activity. Added activity cannot produce these required facts. Memory release remains an explicit post-acceptance host action. The existing complete pass remains the default, and core raw changes remain rejected.

Code areas: `harness/contracts/events.py`, `harness/models/translation_stages.py`, Claude Code, Codex, and OpenCode2 translators, core translators, and seven new protocol/support files. The extension worker wire and package layout are unchanged. No SQL schema changed.

Verification: All 17 new protocol cases and five focused naming checks pass. The complete four-worker Python run passes 3,276 tests with 18 warnings in 330.69 seconds, excluding Kitty and live `tests/e2e`. Strict types pass for 2,814 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. Full Wemake retains the same six unrelated Codex findings. Dead-code analysis retains the same 28 prior findings. No rule, timeout, assertion, skip, or exemption was weakened.

Evidence: The [lifecycle split record](../lifecycle-split.md) gives the protocol behavior, changed paths, initial full-run policy failures, the later six-worker restart timeout, and its passing isolated repeat. These are host protocol tests, not package-owned feature E2E. The plan retains 10 phases and 53 tasks: 20 in progress, two done, and 31 not started. No phase or additional subtask is complete.

Next action: Add required and activity journal steps, connect both passes at the engine boundary, and validate the complete transaction independently. Use the approved shared-body journal design to retain required processing and bounded failures. Only then enable core raw changes and run private-process drop, replace, insert, rollback, and restart cases. Keep the six-worker test time limit issue open.

## Work record — lifecycle journal integration

Date: 2026-09-15

Owner: Codex

Status: in_progress

Context: The approved harness protocol had no required-step representation in the mixed journal. A worker content limit could stop processing before a session start or finish was read.

Task: Connect the original lifecycle pass, activity translation, independent trace checks, and post-commit reactions. Keep old inline journals readable. Preserve the core raw guard until complete size-limit admission and private-process acceptance are ready.

Outcomes: New format-2 journals start with one typed lifecycle step bound to the immutable original bytes. Activity steps follow raw processing. Canonical transforms do not receive required facts. Final acceptance orders starts before activity and finishes after it. Large original inputs retain required facts and an explicit unavailable activity step. Database failure causes no input reaction or memory release. Old format-1 reads and exact retries retain their stored bytes and accepted bodies. The worker protocol, package layout, and SQL schema are unchanged.

Code areas: `engine/interpret/translation.py`, its core mapping module, `extensions/interpretation_pipeline.py`, `interpretation_contract.py`, `interpretation_canonical.py`, `processing_batch.py`, typed journal models, trace validation, and lifecycle tests in `tests/extension_host`.

Verification: The focused processing and storage selection passes 166 tests. Strict types pass for 2,825 files. Root Ruff and changed-file Wemake pass. The first full four-worker run had 3,298 passes and three failures. Two architecture errors in the new code are fixed; 30 focused architecture, naming, and lifecycle checks then passed. The existing ordered-worker restart case reached the global 30-second limit in its second shutdown. Full final regression verification is in progress. See the [complete lifecycle record](../lifecycle-split.md#journal-and-transaction-integration).

Evidence: Tests use actual native-shaped input, the production batch and SQLite transaction, controlled worker calls, and an actual COMMIT failure. Old-format fixtures store the pre-split JSON layout through private test SQL, then use the production reader and exact-retry path. These are host integration checks, not external package E2E or proof of core raw mutation. No live daemon, user database, external service, or Git remote was changed.

Next action: Implement shared immutable journal bodies, typed references, and exact admission with required-work reservation. Add complete failure handling for invalid core mapping and fact output. Then enable core raw mutation and test drop, replacement, insertion, worker failure, and restart with actual private processes. Keep the restart-test time limit issue open. No phase or additional subtask is complete.

## Work record — lifecycle wait timeout

Date: 2026-09-21

Owner: OpenCode

Status: done

Context: The sequential extension-host run had one flaky failure: `tests/extension_host/test_ordered_processing_failures.py::test_worker_exit_keeps_later_processing`. The wait failed in `tests/terminal_pty_waits.py:68`. The cause was not the product. The lifecycle HTTP fixture reused the shared 10-second PTY wait timeout for private worker preparation. A loaded machine can exceed 10 seconds while it builds a package environment and starts workers. The case passed alone in 24.59 seconds.

Task: Give the lifecycle operation and startup waits a dedicated timeout. Do not change any assertion or the PTY timeout for real PTY waits.

Outcomes: `terminal_pty_waits.wait_until()` takes an optional `timeout` with the old default. `tests/extension_host/lifecycle_http_fixture.py` defines `OPERATION_TIMEOUT_SECONDS = 120.0` and uses it for the running phase, the operation result, and cleanup-pending waits.

Code areas: `tests/terminal_pty_waits.py`, `tests/extension_host/lifecycle_http_fixture.py`.

Verification: The complete extension-host selection passes 919 cases in 653.75 seconds after the change. Ruff passes for both changed files. No assertion, skip, or quality rule changed. The 30-second global restart-case limit from the earlier note is separate and remains open.

Evidence: The failing test now passes inside the full sequential run. The timeout only applies to host lifecycle reads, not to PTY process waits.
