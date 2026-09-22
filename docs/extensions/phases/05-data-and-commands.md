# P05 — Derived data, commands, and history

Status: in_progress

Owner: Codex

Depends on: P04

Context: Both frontends consume derived session data. Extensions need their own records and commands, multiple entries from one event, and a controlled way to change historical interpretation.

Task: Add extension projection storage, typed query and command services, durable observers, schema migrations, and candidate rebuilds.

Outcomes: Stored extension data is consistent with feed cursors. External actions have durable outcomes. Replay and upgrade preserve usable history.

Verification: C07, C12–C14, C21–C24 pass. A repository feature can operate with no session. Replay produces no external effects.

Evidence: The settings-only subset of P05-T05 now runs through the daemon's lifecycle manager. Schema 29 stores complete migration results separately from immutable source plans. P05-T01–T04 and record migration remain open. This subset starts before P04 and P05-T04 because it uses existing management storage, not event or projection storage. See the [P03 migration record](03-runtime.md#work-record--candidate-settings-migration).

## Read first

Read [architecture](../architecture.md), [protocols](../protocols.md), current `engine/sessiondata/contract.py`, `engine/react/loop_materialization.py`, `engine/react/loop_runtime.py`, `repository/contract/session_data.py`, `api/sessiondata/models/entry.py`, and `inference/contract.py`.

### P05-T01 — Add records and atomic projection changes

Status: in_progress

Depends on: P04-T04

Context: Current `SessionDataChanges` has one optional entry and no extension record collection. Extensions need multiple entries, derived facts, and repository data.

Task: Add typed extension record collections with owner, scope, schema, key, revision, and deletion markers. Change entry output to an ordered tuple. Use the pure projector selection method to obtain feature-owned record keys. Capture those keys at one validated snapshot, including explicit absent rows. Invoke projectors and projection transforms outside database transactions. Validate their results and commit core changes, extension records, entries, and cursor advancement together.

Outcomes: A single event can add several stable entries and records. Extensions can alter valid proposed core derived changes. Empty or rejected extension results do not stop cursor progress.

Code areas: Current session writers, reaction materialization, session data repository, entry storage and mappers; proposed record contracts.

Verification: C06–C09 exercise put, replace, suppress, delete, and multiple entries. Inject a failure and confirm no half-written view. Page through several entries at the same event boundary without omission or duplication. Reject stale prior-state revisions.

Evidence: The P01 SDK prototype supplies `ExtensionProjector`, pure record selection, captured record states, exact expected revisions, ordered extension feed entries, stable feed IDs, and whole-result checks. It also supplies complete core derived-data mappers and `ExtensionProjectionTransformer`, with explicit ordered operations and protected execution state. Reuse these contracts and process proxies. The storage subset, the record repository, and projector invocation are now implemented: schema 36 lets one commit add several ordered entries and keeps the canonical commit cursor for deltas; selected records are captured at the supplied snapshot and committed with the projected entries and the cursor in one transaction. The application projection-transform caller remains open. See the work records below.

### P05-T02 — Add typed query and streaming access

Status: in_progress

Depends on: P05-T01

Context: External web and Kitty views need stable reads. They cannot use private repositories or fetch each row separately.

Task: Add generic typed routes for declared extension queries, paged records, and changes. Use the existing snapshot and change-notice model. Scope cursors by history revision, projection generation, commit cursor, and entry position where applicable. Support only declared indexed filters. Add extension response envelopes and SDK translators without changing the core event union into an untyped payload.

Outcomes: Both frontends can read a consistent snapshot and resume changes. Unknown extension views still have a generic record response. Repository scope works without an actor ID.

Code areas: Proposed `api/extensions/` query routes and models, extension query service, public SDK reads; current SSE and snapshot services.

Verification: C14 and C21 read history without a worker or session. Interleave reads and writes and verify consistent snapshots. Reconnect from a valid cursor, reject a cursor from a different scope, and reset after a revision switch. Check query limits and indexes.

Evidence: Paged record reads and declared queries are now public. The repository returns one ordered record page with an exact continuation key; `GET /api/extensions/{extension_id}/records/{collection}` returns the typed SDK record states; `ExtensionsResource.records()` reads the route. `POST /api/extensions/{extension_id}/queries/{query_id}` borrows one active registry read, resolves the declared query, validates the request and result with the SDK rules, and returns an api-layer response; `ExtensionsResource.queries.query()` runs it. Change notices and generic extension views remain open. See the paged record and declared query records below.

### P05-T03 — Add durable commands and observers

Status: not_started

Depends on: P05-T01, P03-T05

Context: Git commit, push, model generation, and adapters observations can involve slow work or external effects. They cannot run inside transforms or replay.

Task: Add job storage and a bounded executor for command and observer protocols. Deduplicate by owner, scope, and request or cause key. Commit observer job insertion with its consumer cursor. Commit result observations with the job's final outcome. Implement progress, cancellation, result storage, and reconciliation. Adapt existing process and inference services through public host protocols. Apply existing origin and read-only policies before accepting work.

Outcomes: Slow work does not block transforms. Retries do not blindly repeat external writes. New results re-enter the system as recorded observations. A lost result can remain `outcome_unknown`.

Code areas: Proposed job repository, worker plan, command API, process and inference service adapters; current process, inference, audit, and change-notice services.

Verification: C11, C22, C23, and C24 cover slow work, duplicate requests, lost replies, stale state, cancellation, and read-only rejection. Replay cannot enqueue jobs. A committed event creates one observer job across restart. Test generated-observation cycle limits.

Evidence: The P01 SDK prototype now supplies typed command execution, cancellation, reconciliation, and observation candidates. It checks schemas and exact accepted-attempt bindings. Real process tests keep reads and transforms active during a command. A separate control lane remains available when the live lane is full. Reuse these protocols and proxies. No durable host job storage, observer cursor, origin policy adapter, or restart recovery is implemented; the process fixture stores proof in memory only.

Evidence added: `ExtensionObserver` now supplies live job execution, cancellation, and reconciliation with a committed trigger and cause-linked result observations. Its manifest selection requires read/write classification. The SDK rejects replay, missing write preconditions, stale bindings, schema-invalid output, and oversized messages. Separate-worker tests inspect known proof without re-execution and report uncertainty when proof is lost after restart. P05 must still implement every durable acceptance, transaction, policy, cycle, and recovery check listed in this task.

Integration requirement: The draft `ExtensionServiceAccess` currently exposes peer queries only. Extend it with command acceptance and job status/cancellation when this durable service exists. A peer command must pass the same declared-service, caller, scope, origin, read-only, deduplication, and state checks as a frontend command. Return an accepted job reference; do not call a peer's `ExtensionCommands.execute()` directly or expose a write as a query.

### P05-T04 — Add candidate history and projection rebuilds

Status: not_started

Depends on: P05-T02, P05-T03, P04-T05

Context: Current rebuild clears active derived data. Canonical transforms also depend on translator behavior and inputs that may not be replayable after files disappear.

Task: Implement candidate revisions and active-head switching. First rebuild extension projections from stored facts. For canonical reprocessing, inventory translator state, file reads, and schema availability. Use separate translator state for the candidate. Separate pure session metadata reconstruction from launch and cleanup effects. Capture required evidence or report a specific unsupported scope. Restrict V1 reprocessing to the safe scope boundaries in the architecture document. Compare results before activation and reset stream clients after the switch.

Outcomes: Explicit rebuilds preserve current views until a valid candidate is ready. Prior revisions remain recoverable. Original source and decision history stays linked.

Code areas: Current reaction rebuild path and repository progress; proposed revision repositories, rebuild service, comparison API, and stream reset models.

Verification: C12 and C13 repeat rebuilds and compare logical IDs and content. Fail during preparation, catch-up, and switch; current history remains usable. Count observer, notification, process, and inference calls and require zero during rebuild. Missing required input produces a clear refusal with the active head unchanged.

Evidence: Not recorded.

### P05-T05 — Add extension data and settings migrations

Status: in_progress

Owner: Codex

Depends on: P03-T04 for settings; P05-T04 for records and history

Context: A package upgrade can change its settings and stored record schemas. Old records must remain readable after disable or removal.

Task: Add the pure migration capability with explicit source and target schema versions. Run migrations against candidate settings and records before package activation. Persist schemas and fallback summaries. Prevent automatic down-conversion without an explicit supported migration. Keep secret references separate from encoded settings and snapshots.

Outcomes: Upgrade failure preserves the old package and data. Disabled history can be read without importing the old extension. Supported rollback restores the prior head and compatible package.

Code areas: Proposed migration protocols and models, schema repository, settings repository, activation preparation, and fallback mappers.

Verification: C04 and C14 cover valid migration, invalid output, missing migration path, rollback, and package removal. Test that a secret value does not appear in settings GET responses, migration reports, or audit snapshots. Confirm restart resumes or discards an incomplete candidate safely.

Evidence: The daemon now uses the P01 `ExtensionMigrations` capability for settings during enable or reload. Captured source overrides, exact paths, complete output, source revisions, target schemas, and raw/effective agreement are checked. The same prepared worker activates with converted values. Schema 29 retains the input and result separately. A successful transaction commits the new runtime and raw settings; failure keeps the old state. Real worker and HTTP tests cover upgrade, explicit reverse conversion, scope inheritance, failure, exact retry, and source-free restart. Repository tests cover stale managers, invalid output, required resolution, and COMMIT rollback. See the [migration record](03-runtime.md#work-record--candidate-settings-migration).

Remaining: Record migration and history heads depend on P05-T01–T04. Secret references, related-scope settings resolution, explicit history rollback, and complete C04/C14 release evidence remain open. The settings subset is not a complete data upgrade system. No subtask is marked done by these tests.

## Work record — multi-entry commits

Date: 2026-09-20

Owner: Claude Code

Status: in_progress

Context: The read model stored exactly one feed entry per canonical event, and the entry's row cursor doubled as the canonical commit cursor. P05-T01 needs one event to add several entries without losing or repeating any at a page boundary, and a stream cursor that cannot skip a later commit.

Task: Add an ordered entry tuple to the change set, keep the canonical commit boundary on every entry row, and move every stream and aggregate cursor to that boundary. Keep the row cursor as the paging key.

Outcomes: Schema 36 adds `commit_cursor` and `position` to `session_entries` and rebuilds the table in one migration transaction. `SessionDataChanges.entries` is an ordered tuple; `SessionEntryWriter.entries()` returns the tuple. The write mapper inserts each entry with the commit cursor and its position. Session deltas, aggregate cursors, the high-water mark, and the running list use `commit_cursor`; the feed page and its `before=` paging keep the row `cursor`. The same migration creates `extension_records` with its scope index, its stored generated scope columns, and its stored/deleted state check. `ExtensionRecordRepository.record_states()` captures selected keys at one snapshot with explicit missing rows. `SessionDataChanges.records` carries put and delete changes; the session-data transaction checks every expected revision and commits records, entries, and the cursor together.

Code areas: `repository/impl/sqlite/schema.py`, `session_data_write.py`, `session_data.py`, `repository/contract/session_data.py`, `engine/sessiondata/contract.py`, `entries.py`, `engine/react/loop_materialization.py`.

Verification: `tests/test_sqlite_multi_entry.py` proves that one commit adds three ordered entries, that the delta returns them once and advances to the commit cursor, and that paging across the commit returns every entry exactly once. `tests/test_sqlite_extension_records.py` proves the fresh schema carries the record table, its generated scope columns, and its scope index. `tests/test_sqlite_extension_record_store.py` proves that a put stores the exact document at the host commit revision, that a stale expected revision rejects the commit and keeps the newer row, that a delete keeps the schema and last revision, and that a stale record change rolls back its entries in the same transaction. The updated feed, delta, aggregate, high-water, and loop-commit cases pass. The migration and upgrade selection passes 30 cases, and the three schema-32 upgrade cases retain every old statement and row except the changed shapes, which now share one `retained_lines` helper. The main selection passes 2,419 cases with the five known frontend-build environment failures. The complete extension host selection passes 919 cases sequentially in 641.91 seconds. Wemake, Ruff, strict types for 2,878 files, and architecture pass.

Evidence: The row cursor can run ahead of the canonical cursor, so a stream that advanced to a row cursor could skip a later commit whose canonical cursor is lower. Every stream and aggregate read now advances by `commit_cursor`; only paging uses the row cursor. Record writes join the same transaction as the entries and the progress mark, so a rejected capture cannot leave a half-written view. No projector call exists yet.

Status result: The multi-entry commit storage, the record read/write repository, and projector invocation are implemented. The projection transform caller remains open.

## Work record — projector invocation

Date: 2026-09-21

Owner: Claude Code

Status: in_progress

Context: The record repository and the projection cursor had no caller. A projector protocol existed in the SDK, but nothing invoked it, validated its result, or committed its derived entries and records.

Task: Add one projection pass which selects records, captures them at the supplied snapshot, invokes the projector outside every transaction, validates the complete result, and commits entries, records, and the cursor together. Wire it into the engine after the core reaction drain.

Outcomes: `ProjectionPass.run()` reads the facts after one package's cursor for one scope, builds the exact `ProjectionBinding`, calls `select_records` and `project`, and commits once. `run_selected()` discovers the scopes with new facts and projects every enabled package which declares a projector. The extension entry body is now part of the closed feed vocabulary: the domain body, the API response, and the public SDK model all exist, and the SDK fixture set includes it. The projection cursor lives in `extension_projection_cursors`, keyed by owner, scope, history revision, and generation.

Code areas: `extensions/projection_pass.py`, `projection_models.py`, `projection_changes.py`, `engine/projection_stage.py`, `engine/worker.py`, `engine/source_processing.py`, `app/provider_projections.py`, `app/provider_engine.py`, `repository/contract/extension_projections.py`, `repository/impl/sqlite/extension_projections.py`, `domain/entry_extensions.py`, `api/sessiondata/extension_body_mapper.py`, `packages/extension-api/src/baqylau_extension_api/core/entry_extensions.py`.

Verification: `tests/test_projection_pass.py` passes four cases: one projection commits two ordered extension entries, its record, and its cursor together; a second pass over the same facts commits nothing; a selected key owned by another package rejects the whole projection; and an entry which names a fact outside its request rejects the whole projection. `tests/test_sessiondata_extension_entry.py` proves the extension entry round-trips through storage and the API mapper. The SDK vocabulary and API wire-shape completeness cases pass. The main selection passes 2,427 cases with the five known frontend-build environment failures. The complete extension host selection passes 919 cases sequentially in 707.78 seconds. Wemake, Ruff, strict types for 2,892 files, and architecture pass. The dead code gate drops from 29 to 27 findings because the new code has a production caller.

Evidence: The worker runs the pass inside the captured runtime batch, after the core reaction drain, so the registry read and its worker leases are held. Every projected row is checked for owner, scope, source-fact membership, unique keys, and document ownership before any write. A rejected projection leaves no entry, record, or cursor row. No projector failure stops later packages or the engine batch; the worker records it as an extension projection failure.

Status result: Implemented and verified. The projection transform caller is implemented by the record below; the candidate rebuild remains open.

## Work record — projection transform caller

Date: 2026-09-21

Owner: Claude Code

Status: done

Context: The projector's proposed entries and records had no transform stage. The SDK declared `ExtensionProjectionTransformer` and complete validation rules, but nothing invoked them.

Task: Convert one projector result into typed projection changes, invoke every enabled projection transform on the complete proposal, validate with the SDK's own rules, apply the operations, and commit the transformed result.

Outcomes: `projection_changes()` builds `ExtensionEntryChange` and `ExtensionRecordChange` values with stable change identities. The pass calls `select_records`, `validate_read_set`, `capture_projection_request`, `project`, `validate_projection_result`, and `validate_projection_documents` in order. For each enabled transform package it builds a transformer-specific binding, calls `transform`, and applies `apply_projection_transform`, which checks the binding echo, every operation, and the ordered result. The final changes map to the stored entries and record changes.

Code areas: `extensions/projection_pass.py`, `projection_models.py`, `projection_changes.py`, `tests/test_projection_transform.py`, `tests/projection_transform_fixture.py`.

Verification: `tests/test_projection_transform.py` passes two cases: an enabled transform drops the proposed record and keeps both entries, and an enabled transform adds one derived entry between them. `tests/test_projection_pass.py` keeps its four projector cases. The main selection passes 2,429 cases with the five known frontend-build environment failures. Wemake, Ruff, strict types for 2,894 files, and architecture pass. The dead code gate drops to 26 findings.

Evidence: The host keeps only the feed mapping and the session-scope rule; the SDK owns the binding, read-set, result, document, request, reply, and operation rules. A rejected transform leaves no entry, record, or cursor row. The complete extension host selection passes 919 cases sequentially in 689.60 seconds.

Status result: Implemented and verified.

## Work record — paged record reads

Date: 2026-09-21

Owner: Claude Code

Status: done

Context: P05-T02 needs public paged reads for declared extension record collections. The storage could capture named keys, but nothing could page a whole collection or read it over HTTP.

Task: Add one ordered record page at the repository boundary, one typed HTTP route, and one SDK method. Keep the continuation key exact and the response typed.

Outcomes: `ExtensionRecordRepository.record_page()` reads at most `limit + 1` rows in record-key order, returns the page and its last key as `next_key`. `GET /api/extensions/{extension_id}/records/{collection}` accepts the exact scope document, the continuation key, and a bounded limit, and returns the SDK record states. `ExtensionsResource.records()` reads that route with the scope, continuation key, and limit as query parameters. An undecodable scope is a 400.

Code areas: `repository/contract/extension_records.py`, `repository/impl/sqlite/extension_records.py`, `api/extensions/records_routes.py`, `records_models.py`, `app/provider_projections.py`, `api/application_routes.py`, `sdk/client_extension_catalog.py`.

Verification: `tests/test_sqlite_extension_record_pages.py` proves ordered pages and one exact continuation key. `tests/test_http_extension_records.py` proves the route returns stored states in key order and rejects an invalid scope. `tests/test_sdk_extension_records.py` proves the SDK sends the exact scope, continuation key, and limit. The main selection passes 2,433 cases with the five known frontend-build environment failures. The complete extension host selection passes 919 cases sequentially in 667.07 seconds. Wemake, Ruff, strict types for 2,899 files, and architecture pass. The dead code gate holds at 26 findings.

Evidence: The page orders by the record key inside the exact scope JSON, so a reader cannot skip or repeat a row. The response carries the SDK's own `RecordState` models, so no hand-built state vocabulary is added. The route reads the main database through the same record repository the projection pass uses.

Status result: Implemented and verified. Declared queries, change notices, and generic extension views remain open.

## Work record — declared query route

Date: 2026-09-21

Owner: Claude Code

Status: done

Context: P05-T02 needs public typed reads for declared extension queries. The SDK declared `ExtensionQueries` and complete validation rules, but no host route could call one.

Task: Add one request path that resolves the active package, builds the exact checked query request, calls the worker capability outside any host write, validates the complete result, and returns an api-layer response. Add the matching SDK method.

Outcomes: `POST /api/extensions/{extension_id}/queries/{query_id}` decodes the scope document, borrows one registry read, finds the package which declares queries, builds the `QueryRequest` with the declared arguments schema, the captured settings, the page and its snapshot, and the bounded limit, then calls `validate_query_request`, the worker `query`, `validate_query_response`, `validate_query_document`, and the page continuation check. `query_models.py` publishes `ExtensionQueryReadyResponse` and `ExtensionQueryFailedResponse` in the api layer, so the route answers with an api model. `ExtensionsResource.queries.query()` posts the typed request. Unknown packages are 404; an undecodable scope or an undeclared query is 400.

Code areas: `api/extensions/query_routes.py`, `query_service.py`, `query_models.py`, `api/application_routes.py`, `sdk/client_extension_queries.py`, `sdk/client_extension_catalog.py`.

Verification: `tests/test_http_extension_query.py` proves the route resolves an active package and returns its typed result, and rejects an undeclared query. `tests/test_sdk_extension_queries.py` proves the SDK posts the exact scope, arguments, and limit. The main selection passes 2,436 cases with the five known frontend-build environment failures. Wemake, Ruff, strict types for 2,905 files, and architecture pass. The dead code gate holds at 26 findings.

Evidence: The route holds the registry read for the whole worker call, so the package and its worker leases cannot change under the request. Every request and result passes the SDK's own validators before the response leaves the host. The host allocates the call identity and the runtime revision; the client cannot name either.

Status result: Implemented and verified. Change notices and generic extension views remain open.

## Work record — record projection generation

Date: 2026-09-21

Owner: OpenCode

Status: done

Context: P05-T02 change notices need to select one exact projection generation. `ProjectionCommit` and `SnapshotCursor` carry a real `generation`, but `extension_records.projection_revision` was hard-coded to `"default"` on every put and delete. A generation-scoped change read could not work.

Task: Persist the real projection generation with every projected record change. Keep the default for core session-data writes, which have no projection generation.

Outcomes: `apply_record_changes()` takes an optional `projection_revision` that defaults to `DEFAULT_PROJECTION_REVISION`. `_put` and `_delete` store that value. `extension_projections._write_changes()` passes `projection_commit.generation`. The session-data write path keeps the default.

Code areas: `repository/impl/sqlite/extension_records.py`, `repository/impl/sqlite/extension_projections.py`.

Verification: `tests/test_sqlite_extension_record_store.py`, `tests/test_sqlite_extension_records.py`, `tests/test_projection_pass.py`, and `tests/test_projection_transform.py` pass 11 cases. The schema and the write shape are unchanged.

Evidence: The change is write-only value selection. No schema or contract changes. This is a prerequisite, not the change-notice feature.

## Work record — record change read

Date: 2026-09-21

Owner: OpenCode

Status: in_progress

Context: `GET /api/extensions/{id}/changes` must return typed change frames or a snapshot-reset instruction. The record table keeps one row per record at its latest revision, and one commit can change several records at the same `commit_cursor`. A bounded page that cut inside a commit would skip the rest of that commit.

Decision: [Architecture](../architecture.md#current-event-processing) already answers this: "An SSE update contains all changes from its committed boundary and advances once for that boundary." So a change read returns whole commits and advances only to a complete commit boundary. No extra per-row sequence and no mid-commit cursor are needed.

Task: Add the repository change read that returns every record changed at the next committed boundary after a cursor, scoped to one owner, scope, and projection generation.

Outcomes: `ExtensionRecordChanges` carries the whole boundary's `RecordState` values and the boundary cursor. `ExtensionRecordRepository.record_changes()` is the protocol. `SqliteExtensionRecordRepository.record_changes()` finds the smallest `revision` above the cursor and returns every row at that revision in collection and key order. It returns an empty page and keeps the cursor when no later boundary exists.

Code areas: `repository/contract/extension_records.py`, `repository/impl/sqlite/extension_records.py`.

Verification: `tests/test_sqlite_extension_record_changes.py` passes two cases: three records at consecutive cursors return one whole boundary per read and advance once each; another projection generation returns no rows and keeps the cursor. `tests/test_sqlite_extension_record_pages.py` and `tests/test_sqlite_extension_record_store.py` keep their five cases. Ruff passes.

Evidence: The read orders by revision first, so a reader cannot advance past an unread commit. Records changed more than once appear only at their latest boundary, which is the current-state contract.

Status result: The repository change read is implemented and verified. The frame model, the stream route, and the SDK method are implemented by the record below.

## Work record — change stream route and SDK

Date: 2026-09-22

Owner: OpenCode

Status: done

Context: P05-T02 needs a public typed change stream. The repository change read existed, but no frame model, route, or SDK method called it.

Task: Add the typed change frames, the server-sent stream route, the main-database change-signal provider, and the SDK read. Send a reset when the client history revision or projection generation is not the active one.

Outcomes: `api/extensions/change_models.py` defines `ExtensionChangeQuery`, `ExtensionChangeFrame`, `ExtensionChangeReset`, and `ExtensionChangeError`. `api/extensions/change_frames.py` defines `ChangeStream` and the frame loop. It yields one `changes` frame per whole committed boundary, a `reset` frame for an unknown history revision or generation, and a heartbeat when idle. `GET /api/extensions/{extension_id}/changes` streams the frames and rejects an invalid scope with 400. `app/provider_projections.py` exposes `ExtensionChanges` from the main database signal. `ExtensionsResource.changes` in `sdk/client_extension_changes.py` reads the next change or reset frame.

Code areas: `api/extensions/change_models.py`, `change_frames.py`, `change_routes.py`, `api/application_routes.py`, `app/provider_projections.py`, `sdk/client_extension_changes.py`, `sdk/client_extension_catalog.py`.

Verification: `tests/test_extension_change_stream.py` passes three cases: the stream waits for a change notice and then yields the change frame, an unknown generation yields a reset, and an idle stream yields a beat and reads the database once. `tests/test_sdk_extension_changes.py` passes three cases: the typed change frame, the reset frame, and the error frame. `tests/test_http_extension_records.py` still passes with the route registered. The change, record, page, store, and query selection passes 14 cases. Mypy strict passes 2,912 files. Ruff passes.

Evidence: The route holds no host write. The change signal is the main database signal, so a committed projection wakes the stream. The reset uses the active `ProjectionPass` head values, not a client value. `tests/test_http_extension_changes.py` reads a real `changes` frame from the running server and checks the event stream content type. The change stream alone does not complete P05-T02.

Status result: The change stream frame model, route, signal provider, SDK method, and tests are implemented. The read part of C14 is the existing records route: it decodes stored rows from the database and needs no worker. The read part of C21 is the same route with a repository scope. `tests/test_sqlite_extension_record_changes.py` now also proves the interleaved read/write snapshot and the repository-scope read. The change, page, store, stream, SDK, and HTTP cases pass. The P05-T02 read routes are complete. The command half of C21 belongs to P05-T03.

Next action: Move to P05-T03 (durable commands and observers) for the C21 command half. P06 adds the frontend views that consume these routes. P05 remains in progress until P05-T03 and P05-T04 finish.

## Work record — durable job storage

Date: 2026-09-22

Owner: OpenCode

Status: in_progress

Context: P05-T03 needs durable command and observer jobs. No job table existed, so the acceptance, deduplication, and state path had no storage.

Task: Add one schema version and one repository. A command deduplicates on its request key; an observer deduplicates on its cause event. Keep a monotonic revision, a typed binding, the accepted request, and an optional final result.

Approved design: The user approved the table shape by continuing the implementation after the design proposal. The table is `extension_jobs`.

Outcomes: Schema `37` adds `extension_jobs`. Partial unique indexes deduplicate a command on `(owner, scope, request_key)` and an observer on `(owner, scope, cause_event_id)`. A check constraint requires exactly one of the two keys and matches it to `kind`. Generated scope columns support indexed reads. `ExtensionJobRepository` and `CommandJobRequest`, `ObserverJobRequest`, `JobStateChange`, and `ExtensionJob` live in `repository/contract/extension_jobs.py`. `SqliteExtensionJobRepository` accepts a command or observer job, returns the first job for a repeated identity, reads one job, and advances one job from its exact revision.

Code areas: `repository/impl/sqlite/schema.py`, `repository/contract/extension_jobs.py`, `repository/impl/sqlite/extension_jobs.py`, `tests/sqlite_repository_dependencies.py`.

Verification: `tests/test_sqlite_extension_jobs.py` passes four cases: command request-key deduplication, observer cause deduplication with its consumer cursor, state advance plus stale-revision rejection, and an unknown identity. The schema, migration, and migration-chain selection passes 20 cases. Mypy strict passes 2,916 files. Ruff passes.

Evidence: The repository does not dispatch or execute. It stores and advances only.

Added: `GET /api/extensions/{extension_id}/jobs/{job_id}` reads one stored job and returns `ExtensionJobResponse` with the job identity, kind, state, revision, consumer cursor, decoded result, and diagnostic. An unknown job is a 404 and an undecodable scope is a 400. `api/extensions/job_models.py` and `job_service.py` own the mapping; `app/provider_extension_jobs.py` provides the repository; `sdk/client_extension_jobs.py` adds `ExtensionsResource.jobs.read()`. `tests/test_http_extension_jobs.py` passes two cases and `tests/test_sdk_extension_jobs.py` passes one. The architecture repository-builder list names the new provider. Mypy strict passes 2,923 files and the architecture selection passes 89 cases.

Added: `POST /api/extensions/{extension_id}/commands/{command_id}` accepts one command, validates it with the SDK rules, stores it durably, claims it, runs the active package capability, validates the reply, and stores the final state. A repeated request key returns the stored job and does not run again. `api/extensions/command_models.py` and `command_service.py` own the request and dispatch; `sdk/client_extension_commands.py` adds `ExtensionsResource.commands.submit()`. `tests/test_command_service.py` passes three cases (run and store, replay by request key, missing declaration) and `tests/test_sdk_extension_commands.py` passes one. Mypy strict passes 2,929 files and the architecture selection passes 89 cases.

Added: The command route requires a same-origin JSON request (`require_extension_json`). A write-effect command calls `require_extension_write` before acceptance, so `BAQYLAU_EXTENSION_READ_ONLY=1` refuses it with 403 and the app error handler maps the refusal. A read-effect command stays available, matching the query read rule. `tests/test_command_service.py` now passes five cases, including the read-only refusal and the allowed read command.

Status result: The command submission path runs synchronously while the registry read is held. This is bounded by the worker call, but it is not yet the background executor. Cancellation, reconciliation, restart recovery, observers, and C11/C22–C24 remain open.

Added: `POST /api/extensions/{extension_id}/jobs/{job_id}/cancel` reads the stored job, checks the exact revision, decodes its `CommandBinding`, validates the SDK cancel request, calls the active package `cancel`, validates the acknowledgment, and stores a proven stop. A `canceled` or `outcome_unknown` acknowledgment advances the job state; `requested` and `not_running` leave the stored state unchanged. An absent job is 404 and a stale revision is 409. `sdk/client_extension_jobs.py` adds `ExtensionsResource.jobs.cancel()`. `tests/test_command_service.py` passes eight cases, including a proven stop, a stale revision, and a missing job; `tests/test_sdk_extension_jobs.py` passes two.

Added: `POST /api/extensions/{extension_id}/jobs/{job_id}/reconcile` reads the stored job, checks the optional expected revision, decodes the stored `CommandRequest`, validates the SDK reconcile request, calls the active package `reconcile`, validates the resolved `CommandResult`, and stores it with the final state. It never repeats the original command. `sdk/client_extension_jobs.py` adds `ExtensionsResource.jobs.reconcile()`. `tests/test_command_service.py` passes nine cases and `tests/test_sdk_extension_jobs.py` passes three.

Status result: Submission, cancellation, and reconciliation are implemented and verified. The dispatch is still synchronous, and the background executor, observer dispatch, restart recovery, and C11/C22–C24 remain open.

Next action: Add the background executor and observer dispatch. Then cover restart recovery and the C11/C22–C24 cases.
