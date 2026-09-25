# Architecture and data rules

Status: proposed

## Ownership

Add an `extensions/` host concern with contracts, runtime services, and process adapters. Keep composition in `app/`, HTTP in `api/`, and database access in `repository/`. Extension feature code lives in separate packages outside this repository.

The dependency direction is:

```text
app/ providers
  -> extensions/ services and process adapters
  -> engine/ consumers of extension protocols
  -> api/ and frontend services

extensions/contract.py
  -> installed public extension API package

external extension package
  -> installed public extension API and quality packages
```

`extensions/contract.py` re-exports public protocol types through explicit aliases, as `harness/contract.py` does. It does not define a second copy. Engine consumers receive the capability they need through constructor arguments. They do not discover packages or send JSON-RPC messages.

Use these package boundaries:

| Proposed package | Owns | Does not own |
| --- | --- | --- |
| `baqylau-extension-api` | Python protocols, immutable request and response models, SDK codecs, public wire schemas | Host repositories, FastAPI handlers, private domain implementations |
| `@baqylau/extension-api` | TypeScript interfaces, wire types, view lifecycle, theme contract | Host stores and Svelte component instances |
| `baqylau-extension-testkit` | Typed host client helpers, isolated process fixtures, extension conformance tests | Product data parsers and feature tests |
| `baqylau-dev` and `@baqylau/dev-tools` | One versioned quality policy and tool runners | Extension runtime behavior |
| External feature package | Manifest, backend, schemas, web source and assets, terminal layouts, settings, feature tests | Modifications to the host source tree |

Use standard Python and npm packaging. The main application can retain its existing launch layout. Add packaging only for the shared packages that external projects must install.

Current subset: `packages/extension-api/src/baqylau_extension_api/` is the installable Python SDK source. `extensions/contract.py` contains explicit aliases to its public protocol definitions. All 12 backend capabilities have SDK process adapters. Host providers compose discovery, private preparation, registry, and the manager factory. The daemon owns a manager, and the engine consumes its narrow publication protocol. The SDK import check permits only its own modules, selected standard modules, and its declared validation and version libraries. It rejects host and feature imports. JSON document inspection is restricted to the schema boundary. Existing protocol checks scan the SDK and `extensions/`; dedicated tests check the nested SDK package that the top-level package inventory does not discover.

The SDK also validates data-only manifests, raw and canonical operation results, and a proposed active package order. These are pure functions. They do not replace host supervision or the runtime manager. Several narrow host-service protocols remain pending.

Current host discovery: `extensions/discovery_contract.py` defines `ExtensionPackageScanner` and `ExtensionCatalog`. The application provider composes these with `ExtensionCatalogRepository`. Only the SQLite implementation has database access. HTTP consumers receive the catalog protocol. Discovery reads only data; no module or factory is imported. The [P03 record](phases/03-runtime.md#work-record--application-discovery-and-catalog) defines the current roots, bounds, and failure behavior.

Current artifact capture: The standalone file scanner identifies checked source files. The application wraps it with `CapturingExtensionScanner`, which captures and checks a host-owned copy before accepting valid catalog metadata. `ExtensionArtifacts` is the explicit capture and lookup protocol. Complete read-only directories are published by digest under `<data_directory>/extension-artifacts`; they are separate from discovery and dependency environments. Old copies are retained while stored state refers to them; each daemon start removes the others. Worker preparation and asset delivery must use checked artifact lookup, not the mutable source path. See the [capture record](phases/03-runtime.md#work-record--fixed-package-capture).

Directory traversal and hashing are not an atomic source snapshot or an OS security boundary. Capture succeeds only with a complete copied inventory matching the selected digest. Read-only permissions prevent normal edits, not changes by other software running as the same user. A corrupt stored copy is rejected and is not silently replaced from source.

## Current private dependency environments

`BackendEntry.environment` declares a package-relative requirements file and wheelhouse. This field is optional in the draft manifest so earlier SDK fixtures remain readable. A backend without it cannot pass host environment preparation. Discovery checks file presence only; it does not install dependencies.

`LocalExtensionEnvironments` reads the selected digest through `ExtensionArtifacts`, validates the lock, and creates an owned temporary directory under the supplied environment root. Its `venv` stays at its original path. Python virtual environments contain absolute paths and are not generally portable. See the [Python venv documentation](https://docs.python.org/3.12/library/venv.html#how-venvs-work).

The host uses its installed `uv` dependency through an explicit Python module command. Preparation uses offline mode, no project configuration, no shared cache, no Python downloads, hash checks, wheel-only installs, and copied dependency files. Accepted locks contain exact named requirements, optional extras and markers, SHA-256 hashes, comments, and continuations. Direct URLs, local source paths, editable installs, nested requirements, and installer directives are rejected. The packaging library parses package requirements; `uv` resolves and installs them. See the [uv command reference](https://docs.astral.sh/uv/reference/cli/).

After install, a dependency check and an isolated SDK probe verify the selected Python version, exact SDK version, private prefix, executable path, and SDK location. The probe does not import feature code. A returned `WorkerEnvironment` owns the directory and must be closed after its workers stop. A failed preparation removes only its own directory. The daemon manager now calls this preparation path; environments are not reused. These local processes are failure boundaries, not an OS security sandbox.

`ProcessExtensionWorkers` is the current consumer of that environment contract. It validates the selected manifest, starts an AnyIO blocking portal, and launches the installed SDK with isolated, unbuffered Python. One inherited socket carries RPC; stdout and stderr carry bounded logs. The SDK adds the selected flat or `src/` import root only after declaration checks and rejects names that shadow installed modules. The worker must return the exact selected identity and capability set before the factory returns a public process plugin.

The worker owner closes monitors, transport, process group, reader loop, and environment in resource order. Failure revokes the worker's call grants and closes its connection. Process diagnostics remain available after close. The current policy has a 30-second request deadline, a 2-second graceful process-exit period, and a 1 MiB combined lifetime log budget per generation. These are draft bounds, not measured release limits. The manager now uses this factory for restore, enable, disable, and reload. Persisted health remains open. The portal is the standard [AnyIO thread bridge](https://anyio.readthedocs.io/en/stable/threads.html#using-asynchronous-context-managers), not a second RPC implementation.

## Public and private models

The existing domain dataclasses remain the authority for core domain behavior. The public API publishes a versioned representation of those events. A host mapper converts between the public representation and private types. Schema fixtures check that the mapping is complete and lossless for the supported API version.

Extension-specific documents use `SchemaRef` and `EncodedDocument`. `EncodedDocument` contains schema-validated JSON text. Only codecs inspect the encoded text. Extension authors decode it to their own declared dataclass or Pydantic model. Do not pass `dict[str, Any]` through the engine.

Store extension schemas by owner, name, version, and digest. Validate schemas before registration. Restrict references to bundled schemas in the same validated schema set. Do not fetch a schema from the network during event processing.

This changes the current closed-vocabulary rule in one explicit place: a host-known extension envelope. Core event types remain closed and strict. Add narrow architecture rules for the envelope codec. Do not disable the current JSON or type checks across an entire new package.

## Identity and scope

Use separate types for package identity, logical event identity, and processing versions:

| Type | Meaning | Changes when |
| --- | --- | --- |
| `ExtensionId` | Stable package owner, such as `adapters` | A different extension is installed |
| `PackageDigest` | Hash of an immutable installed artifact | Package content changes |
| `RuntimeRevision` | Active extension set, versions, order, and settings revisions | Activation or processing settings change |
| `HistoryRevision` | One interpretation of a stored scope history | Explicit reprocessing creates a candidate |
| `ProjectionGeneration` | One complete set of derived records for a history revision | A projection rebuild creates a candidate |
| `ProjectionRevision` | One version of derived state | A projection write or rebuild changes it |
| `LogicalEventId` | Stable identity of the observed fact | A different fact occurs |

Scopes are a tagged union: `SessionScope`, `WorkspaceScope`, `RepositoryScope`, and `InstallationScope`. A session scope carries the existing required session and actor context. Repository identity includes the resolved Git directory and worktree. Do not use a path label as a universal repository identity.

Current core raw and canonical envelopes require harness and session fields. Schema 30 adds a distinct extension branch to raw storage with explicit scope and source ownership. Schema 31 adds separate canonical SQL branches and history identity. Schema 32 adds mixed interpretation writes, complete journals, and decoder state. The engine now uses this repository. Do not create fake sessions or harness names for Git page actions. Keep the core session branch's required fields and checks.

V1 transforms preserve the scope of their input. An extension that needs an event in a different scope submits a separate observation with a parent reference. This keeps validation, ordering, and replay boundaries clear.

## Pipeline

The pipeline has explicit stages:

```text
source -> original raw storage
       -> raw transforms -> translator
       -> canonical transforms -> validation -> canonical transaction
       -> core and extension projectors -> projection transforms
       -> projection transaction -> web and Kitty streams

committed facts -> durable observer jobs -> new recorded observations
user actions   -> durable command jobs  -> new recorded observations
```

One ordered dispatcher consumes the mixed canonical stream. Core session reactions and writers receive only supported session events. Extension capabilities receive their declared event types and scopes. Repository events do not enter code that requires a session or actor. The dispatcher advances its progress even when no consumer selects an event.

Raw transforms receive immutable copies of recorded input. They can keep, replace, suppress, or add derived translation inputs. The host retains the original bytes and source cursor. Derived inputs carry their original source reference and the transform step that created them. Added inputs move to the next stage; they do not restart raw processing.

An extension source registers a translator for its source type. A core harness input continues through its harness translator. Validate that a transformed input is valid for the selected translator. A source cannot select another extension's private decoder by name.

Canonical transforms run after translation and before any canonical reaction. Apply each extension to the previous extension's result. Validate the whole result from that extension. On an invalid result, discard its full result and continue with its input. Record the reason. Run core consistency checks again after the final transform.

Use explicit `Keep`, `Replace`, `Drop`, and `Insert` operations with input IDs and output keys. A drop includes a reason. Replacing a record does not change its host-owned identity. The host allocates stored cursors and timestamps. A generated logical ID combines extension ID, input logical identity, and output key. Multiple source observations of the same fact still converge.

Validation checks types, identity ownership, scope, actor and session references, output limits, and required lifecycle consistency. A lifecycle change that leaves invalid state is rejected. A display-only hide operation belongs to presentation or derived display data. It does not remove the state needed for process cleanup.

The existing translators can keep transient state and some inputs come from external files. Preserve the existing translator contract initially. Full retranslation in P05-T04 must inventory those dependencies and record or replace them before it claims deterministic replay.

## Transactions and tables

The table below describes the target storage. P03-T01 has started management storage; P04-T01 must fix the event DDL and indexes against the current schema version before coding:

| Storage | Required data and keys | Transaction rule |
| --- | --- | --- |
| `extension_packages` | Owner, version, digest, manifest, schemas, installed path | Register a complete validated artifact |
| `extension_settings` | Owner, scope, settings version, revision, encoded value | Compare revision and replace atomically |
| `extension_runtime_revisions` | Ordered package digests and settings references | Activate only a prepared revision |
| `raw_events` and pending input | Core or extension origin, scope, source identity and position, exact bytes | Append input and pending work together |
| `interpretations` and step decisions | Raw ID, history revision, runtime revision, ordered transform results and failures | Write verdict, decisions, links, and canonical output together |
| `canonical_events` | Core or extension envelope; unique history revision and logical event ID | Preserve first accepted value within a history revision |
| `extension_records` | Owner, collection, scope, key, schema, value, projection revision, deletion marker | Commit with the corresponding projection cursor |
| `session_entries` and session changes | Ordered entry list and typed session or actor changes | Commit all visible changes for an event together |
| `extension_jobs` | Owner, scope, operation, request key, input, state, result, cancellation state | Accept once; store progress and result durably |
| History and projection heads | Scope and active revision references | Switch candidate revisions atomically |

Current main schema is 33. Management tables from schemas 27–29 remain unchanged. Schema 30 adds scoped original observations. Schema 31 adds versioned canonical SQL storage and the default history backfill. Schema 32 adds interpretation journals and scoped decoder state. Schema 33 adds complete source read records and revision-checked checkpoints. `extension_catalog_head` stores the compare-and-set catalog revision. `extension_packages` stores discovered source rows, including invalid entries; it is not an active installed-artifact registry. `extension_catalog_errors` stores failed root scans. `extension_package_manifests` retains validated manifests and bundled schemas by content digest. A failed root scan retains the last complete entry set. An unchanged scan does not advance the revision.

Schema 28 adds `extension_lifecycle_head`, `extension_runtime_revisions`, `extension_lifecycle_operations`, `extension_requests`, and `extension_settings`. The explicit `ExtensionLifecycleRepository` owns each complete transaction. The last committed runtime is stored separately from requested enable state and pending preparation. No stored row claims that a worker process is currently alive.

Schema 29 adds `extension_runtime_resolutions`. Its runtime ID references the immutable reserved input. A complete settings migration result is inserted only in the successful runtime transaction. Input and result have separate records; migration does not overwrite the accepted request. Earlier complete selections need no result row.

Admission checks manager, lifecycle, catalog, and settings revisions inside `BEGIN IMMEDIATE`. It reserves the runtime ID and complete candidate, records requested state, and selects one pending operation in the same transaction. An exact request retry returns the existing operation. A different request cannot reuse its operation ID. Runtime IDs remain reserved after failure and restart. Selected manifests must be retained, ordered dependencies must be valid, and captured settings must match the selected raw overrides.

Successful completion commits the reserved runtime, settings overrides, operation outcome, and head together. Failed completion stores the failure and leaves the prior committed runtime and settings intact. A duplicate completed result is accepted only at its unchanged completed head; it cannot authorize a later rollback. A new exclusive daemon run can claim a manager generation, mark pending preparation as interrupted, and reject old completions. This repository claim is not a process lock or a distributed lease. The daemon must establish exclusive process ownership first.

`SettingsOverrides` keeps explicit installation and exact non-installation scope values separate from manifest defaults. An absent installation override means inheritance, not a saved copy of the old default. `capture_settings()` in `extensions/models/settings.py` resolves the selected fallback without changing those raw choices. Package identity and effective settings checks are also in the model layer and are shared by the repository and registry. The repository imports no registry service or worker implementation. Settings management routes resolve installation defaults and exact scope overrides through the manager. Declared candidate settings and record migrations now run during enable or reload. Related workspace/session/repository scope resolution and secret references remain open.

The storage and registry commits now share an explicit `RegistryCommit` boundary. `ActiveExtensionRegistry.publish_snapshot()` requires a commit adapter. It rejects active readers and stale writers before calling that adapter. `StoredRegistryCommit` checks the exact accepted candidate and finishes its stored operation. The registry prepares its new read object, revision set, and reply before that call. After successful database commit, it assigns the prepared pointer before releasing the registry mutex. No worker call occurs in this section. A failed SQL commit rolls back the connection and leaves the old registry available. A process exit after the durable commit leaves the new stored selection for restart. Private tests cover both exit boundaries and a reader that arrives between database commit and pointer replacement. The daemon manager now uses this path at the engine boundary.

`ExtensionRuntimePreparation` accepts a `RuntimeCandidate`: either a complete `RuntimeSelection` or an explicit `MigratingRuntimeSelection`. `RuntimePreparation` first reads every selected fixed artifact and checks identity, settings, schemas, dependency order, and any required migration paths. It then starts fresh private workers in that order. A migrating package converts its captured raw overrides through `ExtensionMigrations` before the same worker's activation. Backend-free packages have a selected environment identity but no worker and cannot convert settings. Any failure closes resources from this attempt in reverse order. `PreparedExtensionRuntime` owns the ready snapshot and optional complete resolution; the registry only borrows capabilities. Its owner must remain open while published reads use them. Preparation does not commit storage, replace the registry, cancel external jobs, or change requested state.

This preparer returns active candidate metadata only. The manager must also merge installed inactive package states from the checked catalog before publishing the complete public directory. Factory callbacks read the current registry; candidate workers cannot make live peer queries before publication. All workers in a replacement set receive a fresh common runtime revision, including unchanged packages. Old revision-bound workers are not reused in a new set.

`ExtensionRuntimeOwnership` supplies a separate process lease for the resolved data directory. `FilesystemRuntimeOwnership` uses native `filelock` locking on `extension-runtime.lock`, with one non-blocking attempt, no soft-lock fallback, and a preserved lock path. The dependency is `filelock>=3.32.6,<4`; verification uses 3.32.6. The host uses the library's process and file-lock behavior, not a custom expiring PID file. See the [filelock API](https://py-filelock.readthedocs.io/en/stable/api.html). Directory aliases select the same lock. A closed lease or a forked copy cannot authorize state changes. An ownership context prevents another thread from closing the lease during a manager operation. These contexts are not reentrant; close only after they return.

The manager acquires this lease before claiming its stored generation. It retains ownership through preparation, publication, active work, worker shutdown, and resource cleanup. The database generation check is not a substitute for the process lock. Application startup and shutdown now compose this ownership. Native locks are not a distributed lease or an OS sandbox. Other software must not remove or replace the lock file while an owner is active.

Use repository protocols for complete operations. No caller holds a SQLite connection. Worker calls occur outside database transactions. After the call, a write checks that the expected input and state revisions still apply. A stale result is retried against the correct snapshot or rejected.

Current migration boundary: the shared SQLite runner acquires `BEGIN IMMEDIATE` before reading the stored schema version. It applies all pending migration versions in one transaction. A statement or commit failure retains the old schema, data, and version. A waiting initializer reads the version after acquiring the lock and does not repeat committed work. The [P04 record](phases/04-events.md#work-record--migration-transaction-safety) contains regression evidence. Fresh schema creation and the final idempotent schema script are outside this migration transaction. No extension event DDL is added by that subset.

Current raw storage: `ObservationRepository` supplies extension appends and mixed reads from the existing raw table and pending queue. `StoredObservation` carries a host cursor and either the strict core `RawEvent` or `ExtensionObservation`. SQL branch checks retain required core fields. Typed extension metadata stores scope, schema, causes, and first runtime. Generated columns provide exact scope and owner/source indexes. The write checks the committed manager/runtime and retained source schema inside its transaction. Original bytes and first capture remain unchanged on repetition. See the [scoped observation record](phases/04-events.md#work-record--scoped-original-observations).

The engine now consumes the mixed protocol. Original append does not advance an extension source checkpoint. Source reads use the internal append operation inside their complete result transaction. Future job coordinators must do the same; they must not split original storage from job progress. Mixed interpretation dispatch and stored transform evidence are connected. Public mixed audit routes remain pending.

Current canonical storage: `canonical_histories` registers history IDs. Existing rows belong to `default`, with no invented creation time or runtime stamp. Canonical uniqueness is `(history_revision, event_id)`. Interpretations are unique by history and raw ID. Source links include history in both their unique keys and canonical foreign key. The original global AUTOINCREMENT cursor remains the arrival order across stored histories. Generated origin, owner, and scope columns prepare indexed mixed reads; SQL checks retain required core identity fields and exclude them from the extension branch.

`current_canonical_events`, `current_interpretations`, and `current_interpretation_events` currently select only `default`. Core repository reads use these views and accept only the strict core branch. Older forensic databases use their original tables without a write or migration. Both session lifecycle triggers select only current core facts. A live observation cannot name a candidate-only canonical cause. The core writer still writes only the default history; it does not capture a selected extension runtime or run a mixed transform.

No application operation creates or publishes another history yet. Tests register candidates directly to prove storage isolation. P05 must replace the fixed live selection with checked scoped heads and switch canonical and projection state together. A session rebuild must include its complete actor set. Do not expose a SQL head change before those aggregate and cursor rules exist.

Current interpretation boundary: `InterpretationRepository` is an explicit protocol with complete writes, journal reads, mixed fact reads, exact scope pages, decoder-state reads, and bounded prior-state capture. `SqliteInterpretationRepository` is composed in the fact-storage and extension-processing providers. The engine uses it through `ExtensionProcessing`. `InterpretationCommit` separates host completion time from the exact `InterpretationProposal`. The proposal binds manager, runtime, history, original raw ID and cursor, exact scope, mode, and expected canonical head.

`capture_prior_state(PriorStateRequest)` requires a recorded history and its exact current head. It reads one scope in one SQL read transaction. It selects metadata before loading bodies, then stops at the first fact that exceeds the count or byte budget. It does not skip a large fact to include a later small fact. The live limits are 1,000 facts and 1 MiB for the complete UTF-8 snapshot, including JSON escaping and storage metadata. Smaller host requests are supported. These are draft safety limits, not measured release targets.

`CoreStateSnapshot.complete` is true only when all accepted facts in the selected scope at or before `after_cursor` are present. False makes no completeness claim. An omitted field in an older journal defaults to false. The host reserves the larger false header during byte accounting. Metadata prechecks can return a conservative incomplete prefix. The repository checks every supplied prior fact's body, cursor, and acceptance time again during interpretation acceptance. A true completeness claim also requires the exact scoped count. This count reads at most the supplied count plus one. It does not scan an unbounded history. A failed transform must also have valid prior-state evidence.

Acceptance reads the committed runtime, retained manifests, selected settings, and actual original input under the write lock. Pure model checks reconstruct the supplied raw, translation, and canonical steps with the existing SDK operation helpers. They check selected input, content, owner, schema, scope, source links, prior stored facts, and step order. Each original-derived raw input must be translated once in order, unless a raw transform drops it. Start and finish identities cannot be removed or added by canonical transforms. Full core process-state protection remains P04-T03 work.

One transaction writes the verdict, complete journal, decoder state, accepted or repeated fact links, and mixed canonical facts. It clears live pending input in the same transaction. Decoder state is keyed by owner, history, exact scope, and source identity. Acceptance compares the full captured state, not only its revision. An applied decoder reply advances it once; a failed call does not. Replay writes use a registered non-default history and do not clear live pending input or send live work notices.

`interpretation_journals` stores each complete typed proposal once. `interpretation_steps` is a SQL view over its ordered step array, not a second copy of the documents. Legacy interpretations have no invented journal. Applied replies, failed calls, typed rejected replies, intermediate facts, and later proposals remain inspectable through the repository. Host fact reads retain the first accepted body. A later proposal cannot change its branch, scope, or owner. Exact commit retries return no new acceptance and do not advance decoder state; changed retries are rejected. Core accepted pages omit source links as before; identity lookup and audit records provide them.

These checks validate supplied evidence and require a call record for every eligible active transform. `ProcessingRuntime` holds one registry read through source work, transforms, decoding, and core reactions. Failed transform calls retain their preceding input and complete failure records. Core raw changes are enabled; required lifecycle processing is preserved separately from the stored original before any worker call. No worker call occurs in the storage transaction. Journals have a draft 32 MiB encoded limit; SDK limits still apply to individual calls and content. Bounded prior state does not solve aggregate journal admission: repeated requests and large replies can still exceed the whole-journal bound. P04 must define that behavior without leaving input stuck. P08 must measure the supported limits. Journals are not a credential-redaction boundary.

The current fact store silently converges repeated canonical IDs to the first body. Preserve that behavior within a history revision. Record later observations and proposals for audit. An extension settings change does not rewrite an accepted fact. A rebuild creates a new history revision linked to the same raw inputs.

The current derived-data API permits one entry per event. Replace it with an ordered tuple of entries. Give each generated entry a stable ID and an ordering position. Paging and stream cursors must account for multiple entries in one transaction without losing records at a page boundary.

A snapshot cursor contains history revision, projection generation, and commit cursor. A feed page cursor also contains the entry position within that commit. An SSE update contains all changes from its committed boundary and advances once for that boundary. A projection write increases the normal cursor; it does not force a snapshot reset. Switching history revision or projection generation requires a reset. Validate cursor scope on every read.

Source positions, empty transform results, duplicate facts, and all-suppressed batches must still advance the correct processing cursor. An intentional drop is a stored decision, not an unknown translation or an error.

## Source transaction boundary

`ExtensionSourceRepository` is an explicit host repository protocol. It reads complete checkpoints, accepts successful read results, and reads accepted calls without a worker. `SourceKey` contains the owner, full scope, and stable source identity. It does not contain a runtime revision. Valid reloads retain source progress. Once a source has committed progress, its type cannot change under the same identity.

`SourceCheckpoint` is either revision zero with no type or position, or a positive revision with both fields. A changed opaque position advances the host revision once. An unchanged empty read does not advance it. The revision rejects stale captures even when source positions cycle back to a previous value. A source cannot clear a committed position to `None`.

`SourceReadProposal` retains the exact SDK request, successful reply, manager ID, and captured checkpoint. `SourceReadCommit` keeps the host observation time separate. The full proposal has a draft 8 MiB encoded bound; the SDK reply and document bounds still apply. These bounds are not measured release limits.

The repository checks the current manager and runtime, active owner, retained manifest, source declaration, full effective settings, original documents, and captured checkpoint under the write lock. Original storage, interpretation storage, and source storage share the same data-only runtime validation module. No feature code runs inside SQL.

Schema 33 adds `extension_source_reads`, keyed by runtime and call ID, and `extension_source_checkpoints`, keyed by owner, exact scope, and source identity. A checkpoint references its accepted read. The transaction saves the complete read, calls the existing internal original append, and advances the checkpoint together. Pending input is part of that append. An empty successful read still has a journal, even when it does not change progress.

An exact retry retains the first proposal and observation time. It returns the checkpoint accepted for that call, not a later source head. It does not rewind progress, advance revisions, or add pending work. Changed retries fail. Even exact retries must use the current manager and runtime. Reads remain available after owner removal.

This is the successful-read storage boundary. The source coordinator below now consumes it. `SourceReadFailed` does not enter this success transaction and does not advance progress. Durable complete failure diagnostics and public source audit reads remain open.

## Source processing in the engine

`ProcessingRuntime` implements `ExtensionProcessing`. It replaces the source-only runtime owner and retains one registry read for the complete engine pass. The daemon provider supplies the actual manager, registry, source and interpretation stores, scope selector, call ledger, and failure recorder. Request-only applications and standalone core consumers can have no extension runtime; they need no dummy worker.

`capture_batch()` retains a registry read from before source selection until the last core reaction in that engine pass. It checks that the manager and registry select the same active runtime. Publication stays busy during the pass; the registry mutex does not stay locked across calls. A failed initial restore gives the engine an explicit core-only path. A scope change during core reactions sends another source notice after the pass.

`SessionSourceScopes` selects every running actor from committed core aggregates. Installation scope is always active. `ExtensionScopeRegistry.hold_scope()` provides a reference-counted host lease for an exact repository or workspace scope. First entry and last exit send source notices. The draft limit is 1,000 distinct explicit scopes. Repository and workspace view/job consumers do not exist yet. Tests exercise the lease directly; this does not complete the repository UI or C21. No repository identity is inferred from a working-directory label.

Each source pass selects enabled providers in the active package order and filters scopes by their source declarations. It validates complete plans before changing watches. Describe, read, and release calls each get an exact host grant with captured settings, scope, runtime, and a deadline. Peer queries use the existing ledger. Calls run outside database transactions. Each successful read commits through `ExtensionSourceRepository`; it does not call standalone observation append or save progress separately.

`InputPaths` keeps core subscriptions separate from additional source paths. A core puller update cannot remove extension paths. The engine retains both lexical symlinks and resolved targets. Direct files, directory children, new parents of missing inputs, and replacement paths can send notices. Root validation includes resolved targets and rejects a watch whose nearest existing root is the filesystem root. It is a watch policy, not an OS security boundary or an atomic filesystem snapshot.

`WorkKind.EXTENSION_SOURCES` separates source deadlines from core input scans. A simultaneous `SOURCES` notice absorbs that stage into one source pass. Both feed raw and canonical stages. `WorkQueue.set_deadline()` replaces or clears one owned timer without changing another producer's timer or an already received notice. Timer-only passes do not refresh healthy plans, reset future timers, or read idle file-only sources. Successful plan retry retains the file read that the failed plan could not perform. A complete file-only selection has no idle timer.

Each source can read at most four pages per pass by default. More pages schedule a continuation after 0.01 seconds. A failed plan, read, or release retries after one second. Calls use the current 30-second worker bound. Stop is checked between scopes and pages. These are draft limits, not measured release limits. There is no complete pass-time or fair scheduling policy across a large source set yet.

Removing a source ID calls its release method. Removing an active scope calls whole-scope release. Pending or failed cleanup keeps its state for retry; completed releases are not repeated. Reopening a scope cannot skip pending cleanup. Runtime replacement discards only data-only plans; the manager retains the old worker until publication and owns its lifecycle deactivation and closure. This path does not send separate per-source release calls to a replaced runtime. Extensions must release all remaining resources during deactivation.

Source exceptions use the existing coalesced audit. A structured read failure records its code, not its full diagnostic document. Public source diagnostics and durable complete failure evidence remain P04-T05 work. Successful source journals preserve complete requests and replies and do not redact arbitrary secrets.

The connected engine consumes mixed pending input in arrival order. It selects raw transforms, the original source's core or extension decoder, and canonical transforms. Host checks run before a result changes the batch. The complete transaction rechecks the journal and accepts facts, decoder state, and pending removal together. `CoreInterpretation` maps only newly accepted core facts to existing input reactions and cleanup. Extension documents never enter a core consumer that requires a session. The later reaction stage now advances core consumer progress through both fact branches. Extension projectors and observers remain P05 work. There is no extension view in the dashboard or Kitty yet.

The core reaction loop uses the narrow `CanonicalFactReader` protocol. It reads mixed pages of at most 500 facts without requiring a running extension worker. `private_committed()` converts only core facts and retains their accepted cursor and time. `SessionDataWrite.advance_past_extensions()` checks the exact live target and the entire unprocessed gap inside one write transaction. The target must be an extension fact, and the gap must contain no core fact. Exact and older retries do not change stored state. A successful skip changes only the core checkpoint; it sends no display notice and creates no session row. This is not an extension projection or observer checkpoint.

A failed core write is audited and raised to the engine's existing one-second retry. The loop does not process the remaining tail. An earlier committed prefix retains its checkpoint and actor notices. Core side effects still run before the core write, as they did before this change; a retry can repeat those effects. This does not provide exactly-once external effects. The legacy core view rebuild now reads mixed facts without side effects, but still clears live core views. It is not the candidate history rebuild required by P05.

Mixed live and scoped pages also have a 4 MiB stored-content budget. It counts UTF-8 payload, scope, and extension metadata, not the complete response encoding or Python memory. SQL first selects at most the requested count through the history or exact-scope cursor index. A cumulative size calculation selects an ordered prefix before fact bodies enter Python. An oversized first fact returns alone, including a legacy core fact above the budget. No later small fact can pass it. A byte-shortened page is not an end-of-stream signal; consumers continue from its last returned cursor. Body and head reads retain one SQL transaction. This limit does not change the hard prior-snapshot limit or solve aggregate journal admission.

Current processing limits are incomplete. Each pending read contains at most 100 originals. Pure worker calls retain the current worker deadline. Canonical calls receive an exact-scope prefix bounded by 1,000 facts and 1 MiB of encoded snapshot data, with an explicit completeness flag. This does not provide a complete long-history snapshot when those bounds omit facts. Aggregate journal admission and complete pass-time bounds still need work. An original above the 1 MiB content transfer limit receives a checked failed verdict without losing its original bytes. An extension original whose owner is disabled receives a checked unknown verdict. Both keep content length and digest in a distinct preflight journal step, not a fabricated decoder call. Reprocessing remains an explicit future history action.

The engine now starts at most one mixed raw page per pass. `EngineWorker` supplies separate application-stop and timed-yield predicates through `engine.mixed_processing`. `SelectedProcessingBatch` checks application stop before every original. It checks the one-second monotonic interval only after at least one original is complete. A slow page read therefore cannot cause endless zero-progress retries. Neither predicate interrupts an interpretation, worker call, or transaction. After a positive result, the engine sets its owned RAW continuation for 0.01 seconds, unless application stop was requested. An empty result clears only this owned timer, even if the interval has expired. Other producer deadlines and already received notices remain separate.

The remaining core reactions run before the same retained runtime is released. The next RAW notice selects a fresh runtime and also requests canonical work; it does not scan sources. This lets raw backlogs yield between complete journals without splitting a transaction or repeating an accepted original. A no-runtime fallback keeps its existing core drain; an active runtime with no enabled packages still uses mixed processing. The interval is not a hard latency guarantee. A single call or interpretation can exceed it, source work has separate incomplete pass bounds, and core reaction draining is unchanged. No single-call deadline, journal size rule, or worker protocol changed in this step.

Raw extension changes use the existing SDK processors and source schema checks. Raw core changes are currently rejected because a generic raw transform cannot yet prove that it retains required session lifecycle input. The complete journal records a failed step and continues with unchanged core input. Canonical transforms preserve required core start and finish identity and order. Full lifecycle rules, multiple-extension order, process failures, and public diagnostics remain open.

## Runtime behavior

V1 runs a separate worker process per enabled backend extension. Packages have private dependency environments. A display-only package does not need a worker. The daemon never imports the feature package.

Current manager: `ExtensionManagerFactory` opens an owned `ManagedExtensions`. Its complete host proposals pass stored admission before one background executor prepares them. The preparer merges valid inactive catalog metadata with the selected active set. Source changes do not replace the selected fixed artifacts. Backend-free and empty sets remain valid. The manager restores the last committed selection under a fresh runtime ID on startup, not the last failed enable request.

`EngineExtensionBoundary` consumes the manager's `publish_ready()` protocol before each notified work batch. It retains core notices during startup preparation. A recorded preparation failure allows core fallback; a lost manager generation stops this boundary. Reload preparation leaves the previous set available. Busy readers and failed commits cause a delayed publication retry. Successful publication also requests source work. Preparation and feature cleanup never run on the engine thread. `ProcessingRuntime` retains the selected runtime through the complete source, interpretation, and core reaction pass. Full P04 conformance remains open.

The daemon opens the manager before it builds and starts the engine. A request-only application has no manager and starts no workers. Artifact roots, environment roots, default discovery roots, and the native lock use the actual main database directory. Shutdown signals core workers, requires the engine to stop, then closes extension admission and resources. Other existing daemon workers retain their earlier timed-join policy. A partial thread-start failure stops the workers already started.

`close_registry()` prevents new reads and publication, then waits for current readers to leave. The manager requests cooperative preparation stop, waits for its executor, and keeps all active or unpublished owners. Deactivation runs in reverse active order under a shared shutdown RPC deadline. The manager stores unresolved jobs and failed calls before closing drained resources. It stores the close result before releasing the native lease. Storage failure or failed physical closure retains that lease. Closed workers are not deactivated again. A failed close remains a failure even if a second close would be a no-op. Shutdown does not clear the stored committed selection. Runtime replacement still retains an unresolved old owner for explicit retry.

Schema 34 stores immutable shutdown observations. The public runtime response and Settings → Extensions show the last record after restart, with physical closure and external work as separate facts. Earlier records remain stored. This is not durable job recovery: health policy, command draining before replacement, reconciliation, full history access, and service-removal notices remain open. No application command execution route exists yet. Future root calls must hold a registry read for their entire execution. P05 must not repeat uncertain writes merely because a process has closed. This subset does not claim crash-proof job recovery or a fixed total shutdown deadline.

The host uses typed proxy objects that implement the public protocols. The proxies convert method calls to JSON-RPC 2.0 over process streams. The P01 prototype uses `jsonrpcpeer==0.2.0`, with `Content-Length` framing on an inherited local socket pair. It opens no network listener. The RPC stream is separate from stdout and stderr, so feature log text cannot change frame boundaries. The host supervisor must drain both log streams with bounds. Messages and replies are checked against the request ID, runtime revision, and public model.

Dispatch callbacks while another worker call is waiting. Do not block the transport reader on a callback. A transform receives no live host service objects. Slow commands and observers use separate work scheduling and cannot hold the transform lane. P03-T02 must prove that a long command does not prevent transforms in the same package.

Activation follows `discovered -> disabled -> preparing -> enabled`. Failure produces `failed` or `incompatible`. Disable uses `stopping -> disabled`. Requested state and observed state are separate fields. Show both when an operation is incomplete.

Prepare a replacement worker and validate its contributions before the switch. Finish the current processing batch, then activate one immutable runtime revision. New batches use that revision. Reject late output from old workers. Dispose old sources, views, and services after their active work is released.

An activation failure retains the old worker and data. For a command that can change an external system, drain or reconcile it before a worker replacement. Cancellation means a request to stop; it does not prove that an external operation did not occur.

On transform timeout, crash, or invalid output, preserve the input, record the failure, and continue. Repeated failures disable the affected extension and required dependents through a new runtime revision. Limits are host policy. Measure them in P08; do not publish arbitrary performance claims.

## User lifecycle requests

`ExtensionLifecycleControl` separates user requests from complete manager proposals. `LifecycleControl` checks lifecycle and catalog revisions, the exact selected package digest, dependency order, and captured settings. Enable and reload require one valid current catalog entry. Disable uses retained committed manifests. A missing required provider is not enabled without a separate user request.

A preview returns the affected owners without accepting work. Disable requires the exact transitive required dependent set, excluding the target, in `confirmed_dependents`. The service rejects unrelated confirmations. It rejects new work during preparation or retained cleanup.

A client supplies a stable `request_id`, not a manager or runtime identity. The host derives operation and runtime IDs. `LifecycleProposal.request_origin` retains the exact original body and target in the existing operation JSON. An exact retry returns that operation before current-state planning, including after restart. A reused key with another body or target returns a conflict. Old operation JSON without this optional field remains readable.

`ExtensionControlPolicy` governs extension management writes. `ApplicationConfig.extension_read_only` and the strict `BAQYLAU_EXTENSION_READ_ONLY` values `0` or `1` select it. Lifecycle changes and explicit rescans check this policy. Preview and reads remain available. Startup discovery and restore still run. This policy does not make unrelated application controls read-only and does not sandbox trusted workers.

The API maps host state to separate response models. Active runtime metadata comes from the owned prepared runtime. Committed package references come from storage. General runtime and operation responses include no settings document or complete proposal. The separate settings GET returns accepted ordinary values for its selected scope. HTTP acceptance is 202, not activation success. Request-only application composition starts no manager; manager-dependent requests return 503.

## Settings control

`ExtensionSettingsControl` is the explicit read/change protocol. `SettingsControl` uses the same manager and catalog contracts as lifecycle controls. `ControlRevisions` names the shared lifecycle and catalog selections. `control_admission.submit_request()` supplies one retry path for lifecycle and settings changes. Both use the same durable request-key namespace.

An enabled target uses the retained committed package digest, not newer discovery. An inactive target uses one valid current catalog row. `settings_target()` keeps that selection with accepted raw overrides. A read returns one exact scope, its explicit override if present, its effective document, schema declarations, package identity, and revisions. It does not return another scope's explicit document.

A settings request contains the exact digest, lifecycle revision, catalog revision, owner settings revision, scope, and a complete encoded document. Explicit `null` resets that one override. An omitted document is invalid. There is no partial JSON merge. Exact scope values override installation fallback, which overrides the manifest default. This does not yet resolve a session or repository to its related workspace.

The planner changes one raw override and captures effective settings in a complete runtime candidate. It does not change enable intent. Disabled packages can save settings without activation. The manager currently prepares a fresh complete runtime set for every accepted settings operation, including an edit to a disabled owner. The existing stored commit publishes settings with the new runtime. Pending and failed values remain out of accepted reads.

`SettingsRequestOrigin` and the existing `LifecycleRequestOrigin` form `ManagementRequestOrigin`. Their strict request actions and fields distinguish them in stored JSON. Old lifecycle records and requests remain readable. A request key cannot change from a settings edit into an enable operation. State and operation responses continue to omit candidate settings.

The shared admission service converts candidate model failures to a bounded request error before manager admission. This includes the 1,000-scope override limit and the 8 MiB operation limit. It does not copy model input values into the public error. A full scope set still permits edits and resets of existing overrides. Stored manager failures are not treated as input validation failures.

Settings GET uses `Cache-Control: no-store`. The API accepts a bounded JSON-encoded scope query and decodes it with the public scope model. PUT uses the same JSON, same-origin, and extension-only read-only guards as lifecycle changes. It returns 202 and an operation reference, not saved settings.

This path is for ordinary schema-checked documents. Secret references use the keychain store (P05-T04), a session resolves through its workspace (P05-T05), and the dashboard standard form and custom panels save through the same revision-checked request (P06-T05). Do not place credential values in ordinary settings. The implementation does not detect or redact an arbitrary secret placed in an ordinary document. Settings changes do not reprocess history; P04/P05 must connect processing and rebuild semantics.

## Candidate settings migrations

The lifecycle planner checks each explicit saved document against the selected package's current settings schema. Unchanged schemas use the normal complete selection. Changed schemas require exact declared paths and an available migration backend. `MigratingRuntimePackage` stores the old `SettingsOverrides`, not invented target values. Preview and admission check the source schemas, scope declarations, and paths without running code. The repository compares the full captured source with accepted storage.

`SettingsMigration` calls the existing pure worker protocol for each changed explicit document. It validates requests, complete replies, bindings, source revisions, and target schemas on the host as well as in the SDK worker. A saved scope retains its identity and order. Inherited installation defaults stay absent. Values already in the target schema remain byte-for-byte unchanged. A missing forward or reverse path is rejected; no conversion chain is inferred.

The same prepared worker receives the converted installation value at activation. Its registry entry receives the complete captured scoped settings. The new owner settings revision is the prior revision plus one. With no explicit overrides, reload uses the new manifest default without a migration or an artificial settings revision change.

`RuntimeResolution` contains the complete ready runtime and all converted raw settings changes. It has an 8 MiB encoded bound. Validation preserves runtime ID, catalog revision, owner set, order, package identities, unaffected settings, explicit scopes, and source revisions. `StoredRegistryCommit` accepts only that checked result for a migrating request. The repository rechecks the saved source, target schemas, raw/effective agreement, and selected revisions before inserting the result and changing the head. Ordinary complete requests cannot supply a migration result.

A busy registry retains the prepared result without changing accepted reads. A failed conversion, activation, or database commit retains the prior runtime and settings. Exact retries retain one operation and result. Restart interrupts an incomplete operation and restores the last complete runtime; it does not resume partial conversion. A completed migration restores from retained package bytes and converted settings without rerunning the conversion.

Record migration uses the same preparation. The planner compares each live collection's stored schema with the new manifest. Preparation copies the owner's live records, feed rows, and cursors into a `migrating` projection generation, converts the stale rows through `migrate_records`, and the lifecycle commit makes that generation live. The old generation stays retired. The copied cursors let the new projector project again every fact after the snapshot. A failure, a lost commit, or a restart fails the migrating generation and keeps the live rows. Secret references and related-scope inheritance remain open. Each conversion call has the normal worker deadline. Stop is checked between calls; there is no measured total migration-time limit yet.

## Cooperation

The host registry exposes installed and active package metadata plus versioned public services. Required dependencies form an acyclic graph. Optional dependencies do not block startup. Order transforms by dependency constraints and explicit `before` or `after` rules. Use extension ID as the stable final order rule.

Current active registry: `ActiveExtensionRegistry` implements the explicit `ExtensionRegistry` protocol. `prepare_snapshot()` checks the complete proposed package set and reuses SDK `activation_order()`. It keeps public installed states separate from enabled capabilities. An enabled backend needs matching prepared plugin and environment identities. An enabled backend-free package has an environment identity but no worker. All enabled environments use the selected runtime revision. Inactive entries cannot expose a plugin or environment. Invalid discovery documents remain in the management catalog; they cannot be represented as a checked registry package without complete identity.

`read_snapshot()` holds a borrowed selection until context exit. No lock stays held across a worker call. `publish_snapshot()` rechecks the candidate and returns `accepted`, `stale`, or `busy`. It does not wait for readers. It prevents runtime ID reuse during this registry's lifetime and rejects an older catalog revision. A busy or stale result leaves ownership and the current set unchanged. The manager must retry at its controlled work boundary. It must keep old workers open until publication succeeds and no old work remains. The registry does not own, activate, close, or recover workers.

`RegistryDirectory` copies public metadata inside a read context. `RegistryServiceAccess` holds that context through authorization, provider execution, and result checks. A removed or replaced caller cannot query peers, even with an old host grant. The adapter reuses SDK `HostServiceAccess`; it does not introduce a second service dispatcher. Root application calls must also retain a registry read for their full operation. A worker crash can still interrupt a read; a read context is not a process-health guarantee.

`RuntimeSettings` contains a fixed owner revision, one complete fallback document, and complete effective documents for exact scopes. Publication validates every document and declared scope. This is not a partial JSON merge or a settings repository. `SettingsControl` now resolves defaults, installation values, and exact scope overrides before preparation. Related scope identities still need a host resolver. No secret resolution is added here.

`removal_order()` includes transitive required dependents in reverse active order. Optional consumers remain selected. `LifecycleControl` now turns the confirmed removal plan into one recorded change. It reads retained active manifests even if source packages disappear. Durable requested state, persistent runtime ID reservation, and manager composition exist. Write-job draining, root admission, and removal notices remain open. See the [registry work record](phases/03-runtime.md#work-record--active-registry-and-peer-reads).

An extension declares every service it consumes and provides. The host checks service version, caller, target state, and scope. Workers do not import each other. Use public events for recorded facts and calls for queries or commands. Do not make live service calls inside a transform.

Disable a required dependency and its dependents in one runtime change. Optional consumers receive a service-removal notice and use their base behavior. The settings page shows the affected packages before applying the selected operation.

Avoid loops by recording cause IDs, rejecting repeated cause chains, bounding generated output, and deduplicating durable observer jobs. Cross-extension calls also have deadlines and cycle checks. Required package dependency order alone does not stop a service-call cycle.

Current SDK subset: `ExtensionServiceAccess` resolves declared metadata and calls public peer queries. `HostServiceAccess` binds the caller to its worker connection and reads target metadata through `ServiceProviderLookup`. `HostCallLedger` stores active authority outside feature payloads. It preserves scope and the original deadline across child calls, and rejects repeated owners, expired calls, and released parents. RPC carries only the opaque call ID. The host discovery catalog is not an active service registry. P03 must connect these reads to active providers, coordinate activation, create root grants, revoke stopped runtimes, and publish removal notices. Peer command calls still require P05's durable acceptance path.

## Web and terminal boundaries

Web packages contain all their frontend source, styles, images, dependencies, build configuration, and tests. The host provides named slots and a mount container. Versioned asset URLs map to the installed package files. They do not copy the files into `dashboard/frontend/src` or the host bundle.

The host supplies a narrow API, scope, theme values, and cancellation. It does not expose private Svelte stores. A package builds an independent ES module. Its own mount, update, and dispose functions own its Svelte component instances. Scoped CSS or a shadow root contains its styles. This is style separation, not a security boundary.

The first slots are feed decoration or replacement, session tabs, workspace pages, toolbar actions, status items, settings panels, mirror sections, scoreboard items, and named terminal panes. Two replacements for one exclusive target are a visible configuration conflict. Additive contributions have stable order.

Kitty receives typed display blocks through the daemon. Feature layout code remains in the extension. The client owns width, wrapping, escape output, focus, and input dispatch. No client import of the backend SDK or private host modules is needed. Keep the current standalone client layout.

On disable, remove active contributions and command access. Keep stored data. Existing extension feed entries receive a generic host display with the stored summary and schema information. The host can read that fallback without executing extension code.

## History and external actions

Projection rebuild uses stored facts and pure projectors. Canonical reprocessing uses raw observations and recorded translator inputs. Both write candidate revisions. Do not call the current live-clearing rebuild method on active data.

V1 canonical reprocessing operates on closed session scopes with no active jobs. For repository scopes, suspend source processing at a recorded cursor and queue new observations until the switch. Validate references and catch up the candidate before activation. If required input or an old decoder is unavailable, report that limit and leave the current revision active.

Candidate rebuilds never run observers, notifications, process launches, inference, commit, push, or message writes. After a revision switch, stream clients receive a reset and read a new consistent snapshot. Keep the previous revision for recovery. Do not implement physical history deletion as part of disable.

User commands have a durable request key and explicit scope. Store `queued`, `running`, `succeeded`, `failed`, `cancel_requested`, `canceled`, or `outcome_unknown`. After a lost reply, a worker's reconciliation method checks the external result. If it cannot prove the result, retain `outcome_unknown`. Do not promise exactly-once writes to arbitrary external systems.

Observers use their own durable consumer cursor. Commit observer job insertion and cursor advancement together while reading committed facts. A crash after canonical commit cannot lose the observer job. Commit a finished job's result observations with its final stored outcome so retries cannot record the same completion twice.

## Main costs and design limits

Process messaging, schema mapping, and history revisions add work. They are required to support runtime replacement and recorded transforms. Batch requests, use content references for large output, and keep service access out of pure processing.

The host API defines the supported extension surface. A new general capability can require a host API change. A Git-specific component, command parser, or schema must not require one once the published contracts cover it.
