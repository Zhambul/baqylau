# P05 — Derived data, commands, and history

Status: done

Owner: Codex, then Claude Code

Depends on: P04

Context: Both frontends consume derived session data. Extensions need their own records and commands, multiple entries from one event, and a controlled way to change historical interpretation.

Task: Add extension projection storage, typed query and command services, durable observers, schema migrations, and candidate rebuilds.

Outcomes: Stored extension data is consistent with feed cursors. External actions have durable outcomes. Replay and upgrade preserve usable history.

Verification: C07, C12–C14, C21–C24 pass. A repository feature can operate with no session. Replay produces no external effects.

Evidence: All five tasks are done. Projections commit several ordered entries and typed records with the cursor (P05-T01). Records, queries, and changes are public typed reads (P05-T02). Commands and observers are durable jobs with one executor, cancellation, and reconciliation (P05-T03). Projection generations, candidate histories, and view resets rebuild and switch derived data (P05-T04). Settings and records migrate before activation, secrets stay in the Keychain, and a session's settings inherit from its workspace (P05-T05). C12, C13, C14, C21, C22, C23, and C24 pass; see the work records below. Done on 2026-09-24.

## Read first

Read [architecture](../architecture.md), [protocols](../protocols.md), current `engine/sessiondata/contract.py`, `engine/react/loop_materialization.py`, `engine/react/loop_runtime.py`, `repository/contract/session_data.py`, `api/sessiondata/models/entry.py`, and `inference/contract.py`.

### P05-T01 — Add records and atomic projection changes

Status: done

Owner: Claude Code

Depends on: P04-T04

Context: Current `SessionDataChanges` has one optional entry and no extension record collection. Extensions need multiple entries, derived facts, and repository data.

Task: Add typed extension record collections with owner, scope, schema, key, revision, and deletion markers. Change entry output to an ordered tuple. Use the pure projector selection method to obtain feature-owned record keys. Capture those keys at one validated snapshot, including explicit absent rows. Invoke projectors and projection transforms outside database transactions. Validate their results and commit core changes, extension records, entries, and cursor advancement together.

Outcomes: A single event can add several stable entries and records. Extensions can alter valid proposed core derived changes. Empty or rejected extension results do not stop cursor progress.

Code areas: Current session writers, reaction materialization, session data repository, entry storage and mappers; proposed record contracts.

Verification: C06–C09 exercise put, replace, suppress, delete, and multiple entries. Inject a failure and confirm no half-written view. Page through several entries at the same event boundary without omission or duplication. Reject stale prior-state revisions.

Evidence: The P01 SDK prototype supplies `ExtensionProjector`, pure record selection, captured record states, exact expected revisions, ordered extension feed entries, stable feed IDs, and whole-result checks. It also supplies complete core derived-data mappers and `ExtensionProjectionTransformer`, with explicit ordered operations and protected execution state. Reuse these contracts and process proxies. The storage subset, the record repository, and projector invocation are now implemented: schema 36 lets one commit add several ordered entries and keeps the canonical commit cursor for deltas; selected records are captured at the supplied snapshot and committed with the projected entries and the cursor in one transaction. The application projection-transform caller remains open. See the work records below.

### P05-T02 — Add typed query and streaming access

Status: done

Owner: Claude Code

Depends on: P05-T01

Context: External web and Kitty views need stable reads. They cannot use private repositories or fetch each row separately.

Task: Add generic typed routes for declared extension queries, paged records, and changes. Use the existing snapshot and change-notice model. Scope cursors by history revision, projection generation, commit cursor, and entry position where applicable. Support only declared indexed filters. Add extension response envelopes and SDK translators without changing the core event union into an untyped payload.

Outcomes: Both frontends can read a consistent snapshot and resume changes. Unknown extension views still have a generic record response. Repository scope works without an actor ID.

Code areas: Proposed `api/extensions/` query routes and models, extension query service, public SDK reads; current SSE and snapshot services.

Verification: C14 and C21 read history without a worker or session. Interleave reads and writes and verify consistent snapshots. Reconnect from a valid cursor, reject a cursor from a different scope, and reset after a revision switch. Check query limits and indexes.

Evidence: Paged record reads and declared queries are now public. The repository returns one ordered record page with an exact continuation key; `GET /api/extensions/{extension_id}/records/{collection}` returns the typed SDK record states; `ExtensionsResource.records()` reads the route. `POST /api/extensions/{extension_id}/queries/{query_id}` borrows one active registry read, resolves the declared query, validates the request and result with the SDK rules, and returns an api-layer response; `ExtensionsResource.queries.query()` runs it. The change stream, the whole-boundary change read, and the reset after a history or generation switch are implemented (see the change stream record). The record page route is the generic record response for any view, and it reads stored rows with no worker, so disabled or removed history stays readable (C14 read part). The records, queries, and changes routes need no session, and `tests/test_command_service.py` now runs a repository-scope command with no session row (C21). Frontend views that use these routes belong to P06 and P07. Done on 2026-09-23.

### P05-T03 — Add durable commands and observers

Status: done

Owner: Claude Code

Depends on: P05-T01, P03-T05

Context: Git commit, push, model generation, and adapters observations can involve slow work or external effects. They cannot run inside transforms or replay.

Task: Add job storage and a bounded executor for command and observer protocols. Deduplicate by owner, scope, and request or cause key. Commit observer job insertion with its consumer cursor. Commit result observations with the job's final outcome. Implement progress, cancellation, result storage, and reconciliation. Adapt existing process and inference services through public host protocols. Apply existing origin and read-only policies before accepting work.

Outcomes: Slow work does not block transforms. Retries do not blindly repeat external writes. New results re-enter the system as recorded observations. A lost result can remain `outcome_unknown`.

Code areas: Proposed job repository, worker plan, command API, process and inference service adapters; current process, inference, audit, and change-notice services.

Verification: C11, C22, C23, and C24 cover slow work, duplicate requests, lost replies, stale state, cancellation, and read-only rejection. Replay cannot enqueue jobs. A committed event creates one observer job across restart. Test generated-observation cycle limits.

Evidence: The P01 SDK prototype now supplies typed command execution, cancellation, reconciliation, and observation candidates. It checks schemas and exact accepted-attempt bindings. Real process tests keep reads and transforms active during a command. A separate control lane remains available when the live lane is full. Reuse these protocols and proxies. No durable host job storage, observer cursor, origin policy adapter, or restart recovery is implemented; the process fixture stores proof in memory only.

Evidence added: `ExtensionObserver` now supplies live job execution, cancellation, and reconciliation with a committed trigger and cause-linked result observations. Its manifest selection requires read/write classification. The SDK rejects replay, missing write preconditions, stale bindings, schema-invalid output, and oversized messages. Separate-worker tests inspect known proof without re-execution and report uncertainty when proof is lost after restart. P05 must still implement every durable acceptance, transaction, policy, cycle, and recovery check listed in this task.

Evidence result: Implemented and verified on 2026-09-23. Durable job storage, one bounded executor, observer acceptance with its cursor, settlement of result observations with the final outcome, cancellation, reconciliation, restart recovery, the chain limit, read-only policy, stale-request handling, and peer commands exist. C11 and C22–C24 pass at host level. See the [job executor record](#work-record--one-job-executor-observer-control-and-peer-commands).

Integration requirement (resolved): The draft `ExtensionServiceAccess` exposed peer queries only. Extend it with command acceptance and job status/cancellation when this durable service exists. A peer command must pass the same declared-service, caller, scope, origin, read-only, deduplication, and state checks as a frontend command. Return an accepted job reference; do not call a peer's `ExtensionCommands.execute()` directly or expose a write as a query.

### P05-T04 — Add candidate history and projection rebuilds

Status: done

Owner: Claude Code

Depends on: P05-T02, P05-T03, P04-T05

Context: Current rebuild clears active derived data. Canonical transforms also depend on translator behavior and inputs that may not be replayable after files disappear.

Task: Implement candidate revisions and active-head switching. First rebuild extension projections from stored facts. For canonical reprocessing, inventory translator state, file reads, and schema availability. Use separate translator state for the candidate. Separate pure session metadata reconstruction from launch and cleanup effects. Capture required evidence or report a specific unsupported scope. Restrict V1 reprocessing to the safe scope boundaries in the architecture document. Compare results before activation and reset stream clients after the switch.

Outcomes: Explicit rebuilds preserve current views until a valid candidate is ready. Prior revisions remain recoverable. Original source and decision history stays linked.

Code areas: Current reaction rebuild path and repository progress; proposed revision repositories, rebuild service, comparison API, and stream reset models.

Verification: C12 and C13 repeat rebuilds and compare logical IDs and content. Fail during preparation, catch-up, and switch; current history remains usable. Count observer, notification, process, and inference calls and require zero during rebuild. Missing required input produces a clear refusal with the active head unchanged.

Evidence: Not recorded.

### P05-T05 — Add extension data and settings migrations

Status: done

Owner: Codex, then Claude Code

Depends on: P03-T04 for settings; P05-T04 for records and history

Context: A package upgrade can change its settings and stored record schemas. Old records must remain readable after disable or removal.

Task: Add the pure migration capability with explicit source and target schema versions. Run migrations against candidate settings and records before package activation. Persist schemas and fallback summaries. Prevent automatic down-conversion without an explicit supported migration. Keep secret references separate from encoded settings and snapshots.

Outcomes: Upgrade failure preserves the old package and data. Disabled history can be read without importing the old extension. Supported rollback restores the prior head and compatible package.

Code areas: Proposed migration protocols and models, schema repository, settings repository, activation preparation, and fallback mappers.

Verification: C04 and C14 cover valid migration, invalid output, missing migration path, rollback, and package removal. Test that a secret value does not appear in settings GET responses, migration reports, or audit snapshots. Confirm restart resumes or discards an incomplete candidate safely.

Evidence: The daemon now uses the P01 `ExtensionMigrations` capability for settings during enable or reload. Captured source overrides, exact paths, complete output, source revisions, target schemas, and raw/effective agreement are checked. The same prepared worker activates with converted values. Schema 29 retains the input and result separately. A successful transaction commits the new runtime and raw settings; failure keeps the old state. Real worker and HTTP tests cover upgrade, explicit reverse conversion, scope inheritance, failure, exact retry, and source-free restart. Repository tests cover stale managers, invalid output, required resolution, and COMMIT rollback. See the [migration record](03-runtime.md#work-record--candidate-settings-migration).

Remaining: None. Record migration, secret references, and related-scope settings are done (see their work records). History heads and explicit history rollback are done in P05-T04. Done on 2026-09-24.

## Work record — multi-entry commits

Date: 2026-09-20

Owner: Claude Code

Status: done

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

Added: `ExtensionJobRepository.uncertain_jobs(limit)` returns accepted and running jobs in update order. `command_service.recover_jobs(jobs, limit)` marks each as `outcome_unknown` with a bounded recovery diagnostic. The daemon calls it once after the manager opens and before the engine workers start, so a restart preserves every uncertain job as explicit evidence. `tests/test_sqlite_extension_jobs.py` and `tests/test_command_service.py` cover the selection and the recovery. The worker and daemon selection passes 166 cases.

Added: `extensions/command_dispatch.py` now owns the host command logic (policy, validation, claim, execute, cancel, reconcile, recovery). `api/extensions/command_service.py` is a thin adapter from the typed API requests. `extensions/command_executor.py` runs accepted jobs on one serial `ThreadPoolExecutor`; its `_run` holds the registry read and calls the host dispatch outside the request thread. `app/provider_extension_commands.py` holds the daemon-owned executor and refuses a request when the daemon did not open one. `api/workers.py` opens the executor after the manager, seeds it, and closes it after the workers stop. `POST .../commands/{command_id}` now accepts the job durably, schedules it, and returns 202 with the accepted job; the SDK accepts 202. `tests/test_command_service.py` passes eleven cases including the executor run.

Status result: The command path now has a bounded background executor. Observer dispatch and the C11/C22–C24 cases remain open. The main dead-code metric rises by two findings for the unused `accept_observer` until observer dispatch calls it.

Added: Schema `38` adds `extension_observer_cursors` keyed by owner, scope, history revision, and generation. `repository/contract/extension_observers.py` defines `ObserverAcceptance` and `ExtensionObserverRepository`. `SqliteExtensionObserverRepository.accept()` stores one observer job and advances its cursor in one transaction, so a replay cannot dispatch the same cause twice and a restart resumes from stored progress. `accept_observer` is now a connection-level function so the cursor and the job share the transaction. `tests/test_sqlite_extension_observers.py` passes two cases: accept stores the job and advances the cursor, and a repeated cause keeps one job and advances the cursor.

Status result: The observer cursor and acceptance storage are implemented and verified. The observer pass that reads facts, builds requests, runs `observe`, stores results, and inserts new observations remains open, as do the C11/C22–C24 cases.

Added: `extensions/observer_pass.py` selects the enabled observer packages, reads committed facts after each package's observer cursor, builds a validated `ObservationJobRequest`, accepts the job with its cursor in one transaction, runs `observe`, validates the result, and stores the final state. `engine/source_processing.py` carries the optional observer pass; `app/provider_projections.py` builds it; `engine/observer_stage.py` runs it after the projection stage; `engine/worker.py` calls it after `projection_stage.project`. `tests/test_observer_pass.py` proves the pass observes one committed fact, stores a success, and advances the cursor. The extension observer module joined the engine import allow list. The dead-code metric returned from 28 to its baseline of 26 because the new code has callers.

Status result: Observer acceptance, the live observe call, result storage, and the engine hook are implemented and verified. Inserting the observer's new observations back as recorded input, observer cancellation and reconciliation, and the C11/C22–C24 cases remain open.

## Work record — observer result insertion

Date: 2026-09-23

Owner: Claude Code

Status: done

Context: An observer result can carry new `ObservationCandidate` values. The plan requires them to re-enter as recorded input with the job's final outcome. `ObservationAppend` needs the committed manager identity and runtime revision, and each `PositionedObservation` needs a position. The earlier record stopped at two decisions.

Decision: The user selected these two rules.
1. The manager identity comes from the captured batch. `ExtensionProcessingBatch.manager_id` returns the identity that `capture_batch()` already checked against the active runtime. Thus the packages and the identity come from the same snapshot. The engine runtime field does not change.
2. The position of an observer output is `f"{job_id}:{index}"`. The job is unique for each owner, scope, and cause, so a retry gives the same position.

Outcomes: `ObserverSettlement` in `repository/contract/extension_observers.py` holds the final `JobStateChange` and the optional `ObservationAppend`. `SqliteExtensionObserverRepository.settle()` advances the job, checks the committed runtime, and appends the observations in one transaction. It signals the raw work queue only when it appends input. `extension_jobs.update_state` is now a connection-level function, the same as `accept_observer`. `ObserverPass.run_selected()` takes the batch. `_settle` stores the checked result. If the host rejects the output (stale runtime, invalid document, or identity conflict), the job becomes `failed` with `host.observations_rejected`, it keeps the result as evidence, and it stores no input. `_state` is removed, because a checked result `status` is always a job state.

Code areas: `extensions/observer_pass.py`, `extensions/processing_contract.py`, `extensions/processing_batch.py`, `engine/observer_stage.py`, `repository/contract/extension_observers.py`, `repository/impl/sqlite/extension_observers.py`, `repository/impl/sqlite/extension_jobs.py`, `tests/observer_pass_fixture.py`, `tests/test_observer_pass.py`.

Verification: `tests/test_observer_pass.py` passes four cases against a real committed runtime and a recorded trigger fact: the pass stores a success and advances the cursor; the output re-enters as pending input at position `job_id:0` with the trigger as its cause; a stale manager identity fails the job and stores no input; a second pass keeps one job and one pending input. The main selection passes 3,389 cases. It has the five known frontend-build environment failures, and it has four naming and typing guard failures that come from the earlier command and job work in this branch. The ordered processing daemon case failed one time under full parallel load, and it passes 6 of 6 cases alone and three times at `-n 4`. Ruff, strict types, architecture (64 cases), and the dead-code gate pass. The wemake gate has 44 findings at `HEAD` and 59 now. The findings in the observer files that remain (`WPS201`, `WPS229`) came before this record.

Evidence: The observer pass reads the manager identity only from the batch. The store checks it again under the write lock with `require_runtime()`, so late output from a replaced runtime cannot enter.

Status result: Observer result insertion is implemented and verified. Observer cancellation and reconciliation, generated-observation cycle limits, and the C11/C22–C24 cases remain open.

Open issue: `engine/projection_stage.py` and `engine/observer_stage.py` give `session_data_repository.progress()` to `scopes_after()` as the floor. The stages run after `reaction_loop.drain()`, so this floor is at the canonical head, and `scopes_after()` can return no scope. The unit tests replace `scopes_after()` with a fixed scope, so they do not see this. This needs a decision about the floor, for example a scope query for each owner against its own cursor.

Next action: Decide the stage floor. Then add observer cancellation, reconciliation, and cycle limits, and cover C11/C22–C24.

## Work record — pending scopes for each consumer

Date: 2026-09-23

Owner: Claude Code

Status: done

Context: The projection and observer stages gave `session_data_repository.progress()` to `scopes_after()` as the floor. The stages run after the core reaction drain, so this floor was at the canonical head, and the passes found almost no scope in the daemon. The unit tests replaced `scopes_after()` with a fixed scope, so they did not see this. Two more defects were hidden by this: the observer sent every fact to `validate_observation_request()`, so a fact type that it did not select stopped its scope permanently; and the projection pass sent unselected fact types and no settings document to the projector and to every transform.

Decision: Each consumer asks for the scopes after its own cursor. Schema `39` adds `canonical_scope_heads(history_revision, scope, head_cursor)`. A trigger updates it on each canonical insert, and the migration fills it from the stored facts. The read is one row for each scope, not a scan of every fact.

Outcomes: `PendingScopeQuery` (owner, declared scope kinds, history, generation, limit) replaces the global floor. `repository/impl/sqlite/scope_heads.py` joins the scope heads with the fixed cursor table of one consumer. `ObserverPass.run_selected()` and `ProjectionPass.run_selected()` iterate packages, then the pending scopes of that package. `extensions/processing_selection.py` selects the declaration for a scope kind and the facts that it selects, with the same first-match rule as the SDK. The observer makes a job only for a selected fact, and `ExtensionObserverRepository.advance()` moves its cursor past the other facts. The projector and each transform get only their selected facts. A page with no selected fact commits only the cursor. The projector, transform, and observer requests now carry the captured scope settings. The SDK has one public `fact_type()`, and its three private copies use it. `ObserverAcceptance` holds an `ObserverCursor`.

Guard work: The earlier job work had failed four guards. `JobKind`, `JobState`, and `JobCancelStatus` are now `StrEnum`s in `domain/extension_jobs.py`. `ExtensionJobId` is a NewType, and cause events use `CanonicalEventId`. The flagged parameters use their class names. The job and command `_state` helpers are gone, because a checked result `status` is always a job state.

Code areas: `repository/impl/sqlite/schema.py`, `scope_heads.py`, `extension_observers.py`, `extension_projections.py`, `extension_jobs.py`, `repository/contract/pending_scope_query.py`, `extension_observers.py`, `extension_projections.py`, `extension_jobs.py`, `domain/extension_jobs.py`, `domain/ids.py`, `extensions/processing_selection.py`, `observer_pass.py`, `projection_pass.py`, `projection_models.py`, `command_dispatch.py`, `command_executor.py`, `engine/observer_stage.py`, `projection_stage.py`, `api/extensions/`, `packages/extension-api/src/baqylau_extension_api/models/canonical.py`.

Verification: `tests/test_sqlite_scope_heads.py` passes three cases against real SQL: a scope stays pending until the consumer's own cursor reaches its head and becomes pending again after a new fact; only declared scope kinds are pending; a candidate-history fact is not pending in the default history. `tests/test_observer_pass.py` adds an unselected fact that makes no job and still moves the cursor. `tests/test_projection_pass.py` adds an unselected page that calls no projector and still moves the cursor. The SDK selection passes 1,061 cases. The main selection passes 3,399 cases; the five frontend-build cases pass after `make build-frontend` in this worktree. The naming, identifier, vocabulary, and loose-annotation guards pass. Ruff and strict types pass.

Status result: Implemented and verified.

## Work record — one job executor, observer control, and peer commands

Date: 2026-09-23

Owner: Claude Code

Status: done

Context: The observer pass called `observe()` in the engine thread, so a slow observer stopped event processing. Observer cancel and reconcile did not exist, and a reconciled result could not add its observations. A lost reply after an external write became `failed`, not `outcome_unknown`. No host path accepted a peer command. Restart recovery marked never-started jobs as unknown.

Decisions: These are recorded here as design decisions of this task.
1. One serial `JobExecutor` runs command and observer jobs. It skips a job that is already queued. For an observer job it holds a registry read, and it takes the manager identity with `snapshot_manager_id()`, the same check that `capture_batch()` uses. The observer pass only accepts jobs with their cursor. The engine stage then schedules the accepted jobs. The observer identity is therefore checked against the snapshot of each run; the earlier batch property was removed.
2. Only `running` jobs become `outcome_unknown` at restart. An `accepted` job never started, because the claim to `running` comes before any call. The daemon schedules it again.
3. A capability call that raises, or a reply that fails its checks, stores `outcome_unknown` with `host.reply_lost`, because the external effect can already exist. Only a checked failed result is `failed`.
4. Read-only mode fails an accepted write command or write observer with `host.read_only` before any call.
5. A write observer gets `expected_state_revision = "{history_revision}:{trigger cursor}"`, the committed boundary that it saw.
6. The executor fails an observer job with `host.observer_chain_limit` when the trigger has 8 or more observer steps behind it. One step is a fact interpreted from an extension observation that names an earlier fact as its cause. A bounded recursive SQL query counts them.
7. A stale command (runtime or settings revision changed after acceptance) fails with `host.stale_request` without a call. A stale observer request is rebuilt for the current runtime and settings at the claim; the claim stores the rebuilt binding and request.
8. Peer commands go through `ExtensionServiceAccess.submit_service_command`, `read_service_job`, and `cancel_service_job`. The host applies the live call grant, the declared consumer, provider resolution and revision, the public command list, the argument schema, the read-only rule, and deduplication by `peer:{caller}:{request_key}`. It returns a job reference and never calls the peer's `execute()` directly. A caller can read or stop only its own jobs.
9. One `JobSchedulerSlot` is the only scheduling entry. Runtime preparation exists before the executor, so the daemon attaches the executor to the slot after it opens and detaches it before it closes.

Outcomes: `extensions/job_executor.py` replaces `command_executor.py`. `extensions/job_control.py` owns cancel, reconcile, and recovery for both kinds. `extensions/observer_execution.py` owns observer run, stop, reconcile, settlement, and the policy and chain checks. `extensions/observer_packages.py` owns observer selection and the one request builder. `extensions/peer_jobs.py` and `RegistryServiceAccess` own peer jobs. `command_dispatch.py` keeps only command-specific logic and takes jobs that are already read. The cancel and reconcile routes moved to `api/extensions/job_routes.py`. `app/provider_extension_policy.py` holds the write policy provider, so that the runtime provider does not import the lifecycle provider. `ExtensionJobRepository.jobs_in_state()` replaces `uncertain_jobs()`, and schema `39` indexes jobs by state. A claim can replace the stored binding and request. The SDK adds `ServiceCommandRequest`, `ServiceJobRequest`, `ServiceJobCancelRequest`, `ServiceJob`, `ServiceJobCancelled`, three `host.service.*` methods, and public commands in `ServiceResolved`. Progress is the stored state change accepted, running, final; every change sends the normal change notice and the job route reads it.

Code areas: `extensions/job_executor.py`, `job_control.py`, `job_scheduler_slot.py`, `job_scheduling_contract.py`, `observer_execution.py`, `observer_packages.py`, `observer_pass.py`, `peer_jobs.py`, `registry_services.py`, `runtime_identity.py`, `runtime_preparation.py`, `command_dispatch.py`, `engine/observer_stage.py`, `engine/source_processing.py`, `api/extensions/job_routes.py`, `command_routes.py`, `command_service.py`, `api/workers.py`, `app/provider_extension_executor.py`, `provider_extension_jobs.py`, `provider_extension_policy.py`, `provider_extension_runtime.py`, `provider_engine.py`, `repository/contract/extension_jobs.py`, `extension_observers.py`, `repository/impl/sqlite/extension_jobs.py`, `extension_observers.py`, `schema.py`, and the SDK `models/services.py`, `contracts/service_access.py`, `runtime/service_access.py`, `service_dispatch.py`, `service_results.py`, `service_selection.py`, `methods.py`.

Verification: C11 — `tests/test_job_executor.py` blocks a command in the executor with the production registry: an engine registry read returns at once, a new command is accepted, a reload gets `busy`, and the blocked job then succeeds. C22 — `tests/test_command_service.py` loses the reply after a write: the job is `outcome_unknown`, reconciliation resolves it, and the write ran once; the observer reconcile case settles its result and adds its observation without a second `observe`. C23 — a command accepted before a settings change fails without a call; an observer job accepted before a settings change runs with the new settings and stores the rebuilt request. C24 — read-only refuses a write command, a write observer, and a peer write; a disabled package exposes no command. C17 (observer part) — the chain limit fails the job without a call, and `tests/test_sqlite_observer_cause_depth.py` checks the real SQL count and bound. Peer commands — `tests/extension_host/test_peer_commands.py` passes five cases: one job for one key and scheduling, no grant refused, a private command refused, read-only write refused, and a frontend job not readable by a peer. Restart recovery keeps an accepted job accepted and marks a running job unknown. The main selection passes 3,421 cases with no failure. Ruff, strict types for 2,138 files, architecture (64 cases), the naming guards, and the dead-code gate at its baseline of 26 pass.

Evidence: The observer `observe()` call no longer runs in the engine thread. Replay cannot enqueue a job, because only the live engine stage accepts jobs and replay has no observer path. Two process-start tests (`test_ordered_processing_daemon`, `test_worker_runtime`) failed one time each under the full six-worker load and passed alone and in repeated parallel runs; this is the known timing issue that P08-T04 must measure.

Status result: P05-T03 is done.

## Work record — projection transforms over core rows

Date: 2026-09-23

Owner: Claude Code

Status: done

Context: P05-T01 requires that extensions can change valid proposed core derived changes. The transform caller ran only over extension projector output. The core aggregate, entry, and event mappers existed but had no caller.

Decision: The engine defines `CoreChangeTransform` in `engine/sessiondata/contract.py`. `extensions/core_projection_transforms.py` implements it. For one core event, it gives the proposed session, actor, and feed rows (with stable change IDs `core:session:…`, `core:actor:…`, `core:entry:…`) and the rows before the event to each enabled transform that selects this core fact type in session scope, in active order. The SDK applies and checks the operations, including the protected execution fields and core entry origins. The host maps the result back and commits it in the same single transaction as before. A transform that fails or is rejected leaves the proposal from before it, records `extension core transform`, and later transforms still run. With no selecting transform, the original changes commit unchanged. The in-batch state becomes the state before the event plus the committed rows, so a dropped update does not change the next event's start state. The live engine passes the transform of its captured batch to `ReactionLoop.drain()`. `rebuild()` does not use transforms yet; P05-T04 must give a rebuild the same active transforms, so that a rebuild and a live pass agree.

Outcomes: `ProjectionPass.core_transform()` and `projection_models.projection_transformer_packages()` build the transform list once for both paths. `engine/projection_stage.core_transform()` gives it to the worker.

Code areas: `engine/sessiondata/contract.py`, `engine/react/loop_materialization.py`, `loop_runtime.py`, `engine/projection_stage.py`, `engine/worker.py`, `extensions/core_projection_transforms.py`, `projection_pass.py`, `projection_models.py`.

Verification: `tests/test_core_projection_transforms.py` passes four cases through the real reaction loop, writers, and SQLite read model: a replaced core feed row commits with the actor state; an inserted extension row follows the core row in one delta; a dropped core row is not stored while the actor state commits; a failing transform keeps the proposal, records one failure, and a later transform still runs. The main selection passes 3,424 cases. The dead-code gate falls from 26 to 19 findings, because the core mappers now have a production caller.

Status result: Implemented and verified. P05-T01 is done.

## Work record — candidate generations, candidate histories, and resets

Date: 2026-09-24

Owner: Claude Code

Status: done

Context: P05-T04 requires explicit rebuilds that keep current views until a valid candidate is ready, candidate history for canonical reprocessing inside safe V1 boundaries, a comparison before activation, recoverable previous revisions, and stream resets after a switch.

Decisions:
1. Projection generations. The live record and feed tables keep only each owner's active generation, because their keys have no generation. A rebuild writes a candidate generation into `extension_candidate_records` and `extension_candidate_entries`. A commit goes to the live tables only when its generation is the owner's head, and the store checks this again inside the write transaction. A switch moves the owner's live rows to the mirror under the previous generation and the candidate rows live in one transaction, sets `extension_projection_heads`, and refuses a candidate that is behind a live cursor. A retired generation catches up with `resume` and switches back the same way.
2. Rebuilds run in the engine thread as a bounded stage after the live projection, so live projection and rebuild never race, and the catch-up continues from the candidate's own cursors. A rebuild calls only pure projectors and transforms.
3. Enable applies to future facts. A live projector or observer gets a floor at the canonical head on its first pass (`extension_consumer_floors`). Without it, an enabled observer would act on all past history. Only a rebuild reads the past.
4. Canonical reprocessing V1 is for one finished session with no pending input, no running jobs, and an active extension runtime. It replays every original of the session, over all its actor scopes, in arrival order, into a candidate history in `replay` mode with the retained runtime. `ReplayInterpretation` uses the live translators and runs no input reactions. The live translators hold no state for a finished session, so the replay's translator state stays separate, and the stage releases it at the end. A candidate that does not end with `session.finished` fails.
5. The switch rewrites which facts are `default` instead of adding a head to every reader. In one transaction with deferred foreign keys, it moves the session's default facts, interpretations, links, journals, and decoder state to a new archive history, moves the candidate into `default`, recomputes the scope heads, replaces the session read model with rows folded by the live writers, recomputes the session lifecycle, moves the core progress mark past the candidate, resets projector cursors of the session to zero, and moves observer cursors past the candidate so that no observer acts on replayed facts. It runs only after the engine drain, and it refuses when the core consumer has an unread live fact or the session's live history changed after the request. The archive is a retired candidate and can switch back.
6. Resets. `read_model_views` counts switches. Session and global streams send one `reset` frame with the new view revision and end; a client can send `view_revision` when it reconnects. The extension change stream reads the owner's live generation on each wake and resets on a change.

Outcomes: Schema `40` adds the generation, head, mirror, floor, reprocessing, and view tables. `repository/impl/sqlite/projection_generations.py`, `history_reprocessing.py`, `read_model_views.py`, `extensions/projection_rebuild.py`, `engine/history_stage.py`, `engine/react/fold.py`, `engine/interpret/replay.py`, `api/extensions/generation_routes.py`, and `history_routes.py` are new. Routes: `POST /api/extensions/{id}/projections/rebuild`, `GET` and `POST …/generations/{generation}/activate|resume`, `POST /api/history/sessions/{session_id}/reprocess`, `GET /api/history/candidates/{history}`, and `POST …/activate`.

Verification: C12 — `tests/test_projection_rebuild.py` passes four cases: a ready candidate compares equal and keeps live views, a switch makes it live and keeps the previous generation, two rebuilds of the same facts store equal logical IDs and content, and a failed rebuild keeps the live generation and refuses the switch. C13 — `tests/extension_host/test_history_reprocessing.py` passes five cases through the real Claude translator, the real batch, and the real writers: the replay builds an equal candidate with no input reaction and releases translator state; the switch makes it default, keeps the old facts readable in the archive, keeps the feed, bumps the view revision, keeps the session finished, leaves nothing for the core consumer, and keeps the journals readable; the archive switches back; an open session is refused; a changed live history refuses the switch and keeps the candidate ready. `tests/test_sqlite_scope_heads.py` proves the first-pass floor. The main selection passes 3,436 cases.

Limits: Core projection transforms run only on live input; a folded candidate uses the core writers only. A rebuild of core rows is by canonical reprocessing only. The frontend must handle the new `reset` frames (P06-T06).

Status result: P05-T04 is done.

## Work record — record migrations through a migrating generation

Date: 2026-09-24

Owner: Claude Code

Status: done

Context: P05-T05 requires stored records to be converted before activation, with the old package and rows kept on failure, and a retained prior head. The user selected the candidate-generation design.

Decisions:
1. The planner reads the distinct owner, collection, and schema of the live stored rows (`StoredRecordSchemaReader`). A collection whose stored schema is not the new manifest's schema gives one `RecordSource`. `MigratingRuntimePackage` pins these sources beside the saved settings. A package migrates settings, records, or both. Each record source needs an exact declared `RecordMigrationPath`. A collection that the new package does not declare keeps its rows unchanged.
2. Preparation copies the owner's live records, extension feed rows, projection cursors, and floor into a new `migrating` generation in one transaction. It then converts each stale scope in pages of at most 1000 rows through the pure `migrate_records` call, with the SDK request, result, and document checks. A converted row keeps its key and revision. Any failure fails the generation and removes its rows. Records convert before settings, on the same prepared worker, before activation.
3. `RuntimeResolution` carries the prepared `record_generations`. The lifecycle commit transaction makes each one live with the same head switch as a projection rebuild (`generation_mirrors.make_live`), without the caught-up check. The copied cursors let the new projector project again every fact after the snapshot, so rows that the old projector wrote during preparation are not used. The old live generation stays `retired` with its rows.
4. The preparation cleanup owns a discard callback, so a lost commit fails the generation. A new manager claim fails every `migrating` generation that a stopped daemon left behind. A discard changes only `migrating` rows, so the callback after a successful switch has no effect.
5. There is no automatic down-conversion. A downgrade needs its own declared reverse path, like settings. The retired generation stays readable through the generation routes.

Outcomes: `extensions/models/record_migration.py`, `extensions/models/resolution_checks.py`, `extensions/runtime_record_migration.py`, `repository/contract/record_migrations.py`, `repository/impl/sqlite/record_migrations.py`, and `record_migration_copy.py` are new. Schema `40` allows the `migrating` generation state.

Verification: C04 — `tests/extension_host/test_record_migration_runtime.py` passes three cases with a real worker and the migration sample: a reload converts every live row to the new schema at its old revision; the converted generation becomes the head with the copied cursor, and the old generation keeps its version-one rows; a rejected batch fails the reload, keeps the old rows live, and leaves a failed generation with no rows. C14 — `tests/test_sqlite_record_migrations.py` passes four storage cases: the copy, the stale page, a replace that needs the captured revision, and a discard by failure and by a new manager claim. The main selection passes 3,442 cases.

Status result: The record part of P05-T05 is done.

## Work record — secret references and related-scope settings

Date: 2026-09-24

Owner: Claude Code

Status: done

Context: P05-T05 requires secret references separate from encoded settings and snapshots, and a secret value must not appear in settings reads, migration reports, or audit snapshots. The user selected macOS Keychain references for secrets, and for settings the order session, then repository, then workspace, then installation, with the workspace named by the session's project directory.

Decisions:
1. A manifest declares `settings.secret_references`. The value is never in a settings document. `KeychainSecretStore` keeps it in the macOS Keychain through the `keyring` library, with one service per owner and one account per reference name. SQLite has no secret column.
2. A worker reads a value at call time through the new SDK service `ExtensionCredentialService.resolve_secret`, a live-lane RPC (`host.credential.resolve`). Pure calls cannot reach it. The host service answers only names that the worker's own manifest declares. `ExtensionHostServices.credentials` exists only when the manifest declares a reference.
3. `GET /api/extensions/{id}/secrets` reports each reference's name, whether it is required, and whether it has a value. `PUT` and `DELETE …/secrets/{name}` store and clear one value under the extension write policy and return the same states. No response contains a value. `client.extensions.secrets` wraps the routes.
4. Tests select an in-memory keyring backend with `PYTHON_KEYRING_BACKEND` in the autouse isolation fixture, so no test or test daemon writes the user's Keychain.
5. Related scopes. `RuntimeSettings.for_scope(scope, related)` tries the exact scope, then each related scope, then the fallback. The published registry snapshot carries one `ScopeRelations` reader, and `prepare_snapshot` gives it to each package. Packages expose `resolved_settings`, so the request builders use the same `settings.for_scope(scope)` call. A session relates to `workspace_scope(project_directory)`, a digest of its stored project directory. The interpretation context captures the related scopes once, so its steps and its checks agree. The repository source check reads the same relation in its own transaction. The settings read API resolves the same way. The repository relation comes with the Git package (P10), before the workspace.

Outcomes: `packages/extension-api/src/baqylau_extension_api/models/credentials.py`, `contracts/credentials.py`, `runtime/credential_access.py`, `extensions/secret_store_contract.py`, `extensions/impl/keychain_secrets.py`, `extensions/secret_access.py`, `extensions/secret_control.py`, `extensions/preparation_services.py`, `extensions/models/scope_relations.py`, `repository/impl/sqlite/scope_relations.py`, `api/extensions/secret_models.py`, `secret_routes.py`, `app/provider_worker_services.py`, and `sdk/client_extension_secrets.py` are new. `requirements.txt` adds `keyring`.

Verification: `tests/extension_host/test_secret_http_workers.py` passes three cases through a real daemon and a real worker: the worker reads the stored value through the host, and the states, the settings read, and the lifecycle operation never contain it; a cleared value is missing for the worker; an undeclared name is not found, and read-only mode refuses a write. `tests/test_secret_access.py` covers the declared-name check, a repeated clear, and a reply with another name. `tests/test_scope_relations.py` covers the lookup order, the session relation from its stored project directory, and a projection request that receives the workspace value. The main selection passes 3,436 or more cases per run; the few failures differ between runs under a machine load average above 50 and pass alone (P08-T04 owns these timing cases).

Status result: P05-T05 is done.
