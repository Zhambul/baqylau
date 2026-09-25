# Extension protocol contracts

Status: proposed

## Contract shape

Use Python `Protocol` classes for the extension boundary. The main interface is `ExtensionPlugin`. Small protocols describe optional capabilities. This follows the current harness and terminal pattern, with an explicit main protocol as requested.

The examples describe the full intended API. Draft SDK version `0.1.0a1` has the main plugin, factory, all 12 backend capability protocols, peer directory reads, and declared peer service reads. Several narrow host-service protocols and application callers remain open. See [the SDK source](../../packages/extension-api/src/baqylau_extension_api/contracts/plugin.py) for current signatures. Referenced request and response types belong to SDK model submodules; the model package root does not re-export them. P01 must define every field and validation rule before freezing API version 1.

Current naming decisions: Use `extension_info`, not `info`, to meet the existing Wemake name rule. Use `schema_ref`, not `schema`, because Pydantic already owns a `schema` member. Transform payloads use `document`, not `value`, to meet the same name rule. Raw and canonical requests have `inputs`. `CanonicalFact` is now the tagged union of `CoreFact` and `ExtensionFact`. `CoreFact.payload` is a closed union of all 43 core event types. Acceptance metadata lives in `CommittedFact`, not in transform candidates.

Python type annotations support static checks; they do not validate incoming process messages. Use strict mypy checks for implementations and schema validation for messages. See the [Python Protocol documentation](https://docs.python.org/3.12/library/typing.html#typing.Protocol).

## Main protocol

```python
from dataclasses import dataclass
from typing import Protocol

from baqylau_extension_api.models.lifecycle import (
    ActivationRequest,
    ActivationResult,
    DeactivationRequest,
    DeactivationResult,
    ExtensionInfo,
)


@dataclass(frozen=True)
class ExtensionCapabilities:
    lifecycle: "ExtensionLifecycle"
    sources: "ExtensionSources | None" = None
    translator: "ExtensionTranslator | None" = None
    raw_transformer: "ExtensionRawTransformer | None" = None
    canonical_transformer: "ExtensionCanonicalTransformer | None" = None
    projector: "ExtensionProjector | None" = None
    projection_transformer: "ExtensionProjectionTransformer | None" = None
    observer: "ExtensionObserver | None" = None
    commands: "ExtensionCommands | None" = None
    queries: "ExtensionQueries | None" = None
    migrations: "ExtensionMigrations | None" = None
    terminal: "ExtensionTerminalPresenter | None" = None


class ExtensionPlugin(Protocol):
    @property
    def extension_info(self) -> ExtensionInfo:
        """Return the declared extension identity and API version."""
        ...

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """Return the capabilities implemented by this extension."""
        ...


class ExtensionLifecycle(Protocol):
    def activate(self, request: ActivationRequest) -> ActivationResult:
        """Prepare extension resources for one runtime revision."""
        ...

    def deactivate(self, request: DeactivationRequest) -> DeactivationResult:
        """Release resources and report any work that remains."""
        ...
```

Each backend package exports `build_extension(services: ExtensionHostServices) -> ExtensionPlugin`. Only the worker SDK calls the factory. The host validates a data-only manifest before it starts the worker. The returned identity and capabilities must match the manifest.

The host builds `ProcessExtensionPlugin(ExtensionPlugin)`. Each capability is a typed process proxy. For example, `ProcessCanonicalTransformer(ExtensionCanonicalTransformer)` implements `transform()`. The engine receives `ExtensionCanonicalTransformer`, with no knowledge of processes.

Concrete classes explicitly name their protocols, as required by the current architecture tests. A frozen dataclass groups capabilities; it does not replace the main protocol. Avoid a base class that requires an extension to implement unused methods.

Current host preparation contracts are in `extensions/environment_contract.py`. `ExtensionEnvironments.prepare_environment(package_digest)` returns an owned `WorkerEnvironment` with a checked artifact, private executable, and `close()` method. `PreparationRunner.run_preparation(command)` runs explicit argument, environment, directory, deadline, and output-limit values. The concrete services explicitly implement these protocols. These are host resource contracts, not new feature capabilities. A lifecycle owner must stop workers before it closes their environment.

`extensions/worker_contract.py` adds `ExtensionWorkers.prepare_worker(request, services)` and the owned `ExtensionWorker`. The latter exposes `plugin`, bounded `diagnostics()`, and idempotent `close()`. `ProcessExtensionWorkers` implements this host contract. `ProcessExtensionPlugin` implements the public SDK plugin and constructs the existing remote proxies only for verified capabilities. `WorkerStartError` retains bounded process evidence after a failed start; its message does not contain feature log text. The daemon manager now calls this factory for restoration and complete lifecycle changes.

## Processing protocols

All methods in this table use immutable request and result models. Each method has one request argument and one result type:

| Protocol | Method signature | Behavior |
| --- | --- | --- |
| `ExtensionSources` | `describe(SourceContext) -> SourcePlan` | Declare watched resources and resumable source IDs. |
| `ExtensionSources` | `read(SourceReadRequest) -> SourceBatch` | Return observations after a committed source position. |
| `ExtensionSources` | `release(SourceReleaseRequest) -> SourceReleaseResult` | Release a scope or source. |
| `ExtensionTranslator` | `translate(ExtensionTranslationRequest) -> ExtensionTranslationResult` | Decode a declared extension observation into candidate facts. |
| `ExtensionRawTransformer` | `transform(RawTransformRequest) -> RawTransformResult` | Transform derived translation input; preserve original raw storage. |
| `ExtensionCanonicalTransformer` | `transform(CanonicalTransformRequest) -> CanonicalTransformResult` | Transform candidate canonical facts before commit. |
| `ExtensionProjector` | `select_records(ProjectionSelectionRequest) -> ProjectionReadSet` | Select owned record keys from captured facts before the host snapshot read. |
| `ExtensionProjector` | `project(ProjectionRequest) -> ProjectionResult` | Build entries and extension-owned records from committed facts. |
| `ExtensionProjectionTransformer` | `transform(ProjectionTransformRequest) -> ProjectionTransformResult` | Change proposed derived data before one write. |
| `ExtensionObserver` | `observe(ObservationJobRequest) -> ObservationJobResult` | Handle a durable post-commit job and return new observations. |
| `ExtensionObserver` | `cancel_observation(ObservationCancelRequest) -> ObservationCancelResult` | Request a stop for the exact job attempt. |
| `ExtensionObserver` | `reconcile_observation(ObservationReconcileRequest) -> ObservationJobResult` | Inspect uncertain outcome evidence without repeating execution. |

A concrete canonical transform contract has this form:

```python
from typing import Protocol

from baqylau_extension_api.models.transforms import (
    CanonicalTransformRequest,
    CanonicalTransformResult,
)


class ExtensionCanonicalTransformer(Protocol):
    def transform(
        self,
        canonical_request: CanonicalTransformRequest,
    ) -> CanonicalTransformResult:
        """Return operations for the supplied canonical event batch."""
        ...
```

The method is synchronous from the engine's point of view. The process adapter enforces a deadline. Do not convert the full engine to async solely for extension transport. The worker scheduler must still let its transport reader receive replies and cancellation while work runs.

Current process adapters: `runtime.proxies` supplies `RemoteLifecycle`, `RemoteRawTransformer`, and `RemoteCanonicalTransformer`. `RpcBridge` submits synchronous calls from a different thread to the channel's event loop. `WorkerBootstrap` imports one package off that loop, so the factory can call `RemoteDirectory` while loading. `ExtensionHostServices.environment` supplies the host-selected package identity and runtime revision. The worker checks those values against the manifest and rejects unsupported capabilities before importing feature code. The host manager and mixed engine now use these adapters. Feature code is not imported into the engine.

## Required model fields

The field names below are proposed. Store them as declared models, not dictionaries:

| Model | Required fields | Rule |
| --- | --- | --- |
| `ExtensionInfo` | `extension_id`, `package_version`, `api_version`, `package_digest` | Worker and installed manifest must agree. |
| `SchemaRef` | `owner`, `name`, `version`, `digest` | Resolve from the validated installed or persisted schema set. |
| `EncodedDocument` | `schema_ref`, `json_text` | Validate in the codec before use; decode to a concrete model in the owning extension. |
| `ProcessingContext` | `extension_id`, `runtime_revision`, `history_revision`, `scope`, `input_cursor`, `settings_revision`, `settings`, `mode` | `mode` distinguishes live work and replay. No live services are attached. |
| `RawTransformRequest` | `context`, `inputs`, `content_snapshot` | Each input includes source identity, position, exact content reference, and origin link. Every reference resolves within the supplied immutable byte snapshot. |
| `CanonicalTransformRequest` | `context`, `inputs`, `prior_state` | Core events use a typed union; extension events use a schema reference. |
| `CoreStateSnapshot` | `after_cursor`, `facts`, `complete` | True proves complete state in the request scope through this cursor. False, including an omitted older field, makes no completeness claim. |
| Transform operation | `kind`, `input_id` when applicable, `output_key` when applicable, `document` when applicable, `reason` for drop | Validate operation-specific fields through a tagged union. |
| Transform result | `operations`, `diagnostics` | Host applies all valid operations from one extension together. |
| `ProjectionBinding` | `context`, `snapshot`, `after_input_cursor` | Distinguish the prior canonical checkpoint from the derived-data snapshot commit. |
| `ProjectionSelectionRequest` | `binding`, `events` | Pure record selection receives only captured committed facts. |
| `ProjectionReadSet` | `binding`, `keys` | Name each owned collection key once in the selected scope. |
| `ProjectionRequest` | `binding`, `events`, `prior_records` | Capture every selected record at the exact snapshot, including absent and deleted keys. |
| `ProjectionResult` | `binding`, `entries`, `record_changes`, `diagnostics` | Keep the exact input binding and validate the whole ordered proposal. |
| `ProjectedEntry` | `entry_key`, `source_event_id`, `entry_type`, `document`, `summary`, `occurred_at` | One input fact can produce several distinct extension-owned feed rows. |
| `RecordKey` | `owner`, `collection`, `scope`, `key` | A collection belongs to its declared owner. |
| `RecordState` | Tagged `MissingRecord`, `StoredRecord`, or `DeletedRecord` | Absence has revision zero; stored rows and deletion markers have positive host revisions. |
| `RecordChange` | Tagged `PutRecord` or `DeleteRecord`, with `key` and `expected_revision` | No worker-assigned commit revision; every changed key must be captured. |
| `ProjectionTransformRequest` | `binding`, `events`, `before_core`, `prior_records`, `changes` | Includes complete captured core state and typed core and extension write proposals. |
| `ProjectionTransformResult` | `binding`, `operations`, `diagnostics` | Keep the exact processing and storage snapshot binding. |
| `SnapshotCursor` | `scope`, `history_revision`, `projection_generation`, `commit_cursor` | Select one consistent stored view. |
| `EntryPageCursor` | `snapshot`, `entry_position` | Resume within a commit that produced several entries. |
| `SourceContext` | `binding`, `settings_revision`, `settings` | Select one owner, scope, runtime, call, and captured settings. |
| `SourceReadRequest` | `context`, `source`, `after_position`, `limit` | Read a declared source after committed progress. |
| `SourceBatch` | `status`, `binding`, `observations`, `next_position`, `has_more`, `next_due_at` | Commit source progress only with observations or an explicit empty-read checkpoint. |
| `SourcePlan` | `binding`, `sources` | Each descriptor has its own watch paths, optional deadline, and typed source state. |
| `ExtensionTranslationRequest` | `context`, `inputs`, `state`, `content_snapshot` | Supply recorded bytes and host-selected decoder state; no live services. |
| `ExtensionTranslationResult` | `context`, `state_revision`, `decisions`, `next_state` | Repeat the selected boundary and cover every input once in input order. |

Core identity columns and stored cursors are host-owned. Reject worker attempts to set them. A transform can replace event data, but cannot claim another extension's event type or change the input scope.

`Insert` adds an item before or after a named input position. Require a stable `output_key` for each addition. An added item is visible to subsequent transforms only. The same rule applies to added entries. Document empty-batch and duplicate-key behavior in schema fixtures.

Current canonical operation rules:

- Omitted input decisions mean keep. An empty result keeps the batch; explicit drops can produce an empty batch.
- Input IDs are unique within one request. Inputs and prior facts use the request scope. Prior cursors are unique, increasing, and within the snapshot boundary.
- The host captures prior state through `InterpretationRepository.capture_prior_state(PriorStateRequest)`. The request binds exact history, scope, accepted head, and count/byte limits. Live capture stops at 1,000 facts or 1 MiB of complete encoded snapshot data. It returns an ordered prefix with explicit completeness. Absence from an incomplete snapshot does not prove that a fact is absent from storage. Pure transforms cannot make a live call to fetch more facts.
- Storage repeats prior body, cursor, time, and completeness checks before accepting the complete interpretation. Failed transform records have the same evidence checks. The new field is part of the unfrozen draft API. Older journals decode with no completeness claim; this is not a promise that older alpha worker wheels accept the new field. Rebuild draft extension wheels with the current SDK before testing them.
- Each input has at most one keep, drop, or replace operation. An insertion key is unique relative to its input. Different inputs can use the same key.
- Apply operations in input order. For each input, preserve response order among before additions, then apply the base decision, then preserve response order among after additions. Additions remain valid when their anchor is dropped. Additions cannot become anchors in that same response.
- `DerivedIdentity` and `derived_event_id()` allocate an insertion ID from the owner, input ID, and output key. The host must check the same ID. Runtime and history revisions do not enter this logical ID.
- Replacements keep envelope kind, event ID, scope, and source metadata. Core replacements can change their typed payload and source time. A peer extension document can change only within its original event type and exact schema. It cannot change owner or causes. Manifest permissions must still authorize that transform.
- New extension facts use the caller's event namespace and schema owner and name their anchor as a cause. New core facts need a core anchor and keep its source metadata. An extension cannot create a session by adding a core fact in repository scope.
- The pure canonical processor validates the whole proposal before returning an output tuple. It rejects duplicate output IDs and oversized output. It does not write storage or prove that a cause reference exists in host history.

`export_schema()` exports a Pydantic adapter's public JSON Schema with an exact digest. `CORE_SCHEMA_VERSION` is 1 and is separate from the host's private database schema version. `SchemaSet` resolves local pointers and anchors at registration with the [referencing registry API](https://referencing.readthedocs.io/en/stable/api/); it does not configure a remote retriever.

Current raw operation rules:

- `ContentBlob` carries canonical base64 and a checked byte length and SHA-256 digest. `ContentBundle` rejects repeated content IDs. The draft limit is 1 MiB per object and 8 MiB per snapshot; these are safety bounds, not measured performance claims.
- `RawTransformRequest.content_snapshot` must resolve every input reference. A raw transformer can inspect bytes without a live callback or a host path.
- `RawTransformResult` lives in `models.raw_transforms`. It returns operations and optional new content in `content_snapshot`. The generic operations remain in `models.transforms`.
- Raw replacements can change only content. Raw additions use `derived_input_id()` and preserve the anchor's scope, source, owner, origin, and translator type. Raw and canonical IDs use separate prefixes.
- `apply_raw_transform()` validates the complete response, applies the same input-relative ordering as canonical operations, and returns the next immutable request snapshot. New bytes must be supplied, used, and within the bounds. An all-dropped result has no translation inputs or content, but retains the processing checkpoint context. Original storage is outside this pure function.
- The host must keep oversized original observations and apply its recorded failure policy. It must not truncate JSON or lose source progress to make a worker request fit.

The JSON codec parses text and then validates the decoded value. Both steps are required: the library's fast `JsonValue` JSON path does not itself enforce finite floats. Tests cover non-finite literals and numeric overflow under a schema that permits all other data.

## Sources and recorded translation

These draft contracts exist in `contracts.sources`. `runtime.sources` provides `RemoteSources` and `WorkerSources`. `runtime.translation` provides `RemoteTranslator` and `WorkerTranslator`. The worker checks that each package supplies the protocols named in its manifest. Host discovery, atomic source storage, engine source calls, and mixed decoder dispatch now work together. Full processing conformance remains open.

Source rules:

- `SourcePlan` is a complete plan for one selected scope. Source IDs are unique within that plan. A descriptor names a registered source type, normalized absolute watch paths, an optional finite nonnegative deadline, and optional state with an owned schema. The host must validate actual paths and watch policy. The SDK does not register watches.
- `SourceReadBinding` repeats the call binding, source identity, source type, and `after_position`. Replies cannot change them. Positions are opaque text; only the owning source interprets their order and meaning.
- `PositionedObservation` pairs an original observation with its resume position. Every observation keeps the selected source identity, type, and scope and uses its declared schema. Observation keys and positions are unique within the batch. Reads have a strict limit from 1 to 256.
- A nonempty batch ends at its final observation's position. No observation repeats the already committed position. An empty batch can keep progress or supply an explicit new checkpoint. `has_more=True` requires a changed checkpoint. An existing checkpoint cannot be reset to `None`.
- `SourceReadFailed` has a diagnostic and no next checkpoint. The host must preserve its current progress. A release result is `released` or `pending`, with the exact selected source or complete scope.
- Plans are limited to 128 descriptors and 1 MiB encoded. Each descriptor has at most 64 watch paths. Read replies are limited to 4 MiB encoded. These are draft safety bounds. Large-content storage and reference resolution still require the remaining host services.

The host storage contract is `repository/contract/source_reads.py:ExtensionSourceRepository`. Its methods are `source_checkpoint(SourceKey) -> SourceCheckpoint`, `record_source_read(SourceReadCommit) -> SourceReadOutcome`, and `find_source_read(runtime_revision, call_id) -> SourceReadCommit | None`. The source read commit wraps the exact SDK request and successful reply; it does not define a second feature source API. Schema 33 stores originals, pending input, read evidence, and source progress in one transaction. A full captured checkpoint and settings comparison rejects stale calls. Exact retries retain first acceptance without moving progress again. See the [source transaction boundary](architecture.md#source-transaction-boundary).

Current host source contracts are separate from the public feature API:

| Contract | Method | Ownership rule |
| --- | --- | --- |
| `ExtensionProcessing` | `capture_batch() -> AbstractContextManager[ExtensionProcessingBatch \| None]` | Borrow one runtime until all engine stages finish. Never retain the batch after context exit. |
| `ExtensionProcessingBatch` | `interpret_pending(core, stopped) -> int` | Read one ordered mixed page, record complete interpretations, and call core input reactions after commit. Also implements `ExtensionSourceBatch`. |
| `CoreInterpretation` | `translate_input(raw_event, source, bundle)` and `accept_interpretation(original, outcome)` | Keep the existing harness decoder and core input reactions under engine ownership. |
| `ExtensionSourceBatch` | `read_sources(watches, stopped, *, refresh_plans) -> float \| None` | Validate plans, set complete watches, perform bounded reads, and return the next absolute source deadline. |
| `ExtensionSourceWatches` | `watch_sources(frozenset[Path]) -> None` | Change only additional subscriptions, before reading source input. |
| `ExtensionSourceScopes` | `source_scopes() -> tuple[ExtensionScope, ...]` | Select complete host identities in stable order. |
| `ExtensionScopeRegistry` | `hold_scope(scope) -> AbstractContextManager[None]` | Retain one host view/job scope without a fake coding session. |

These protocols are in `extensions/processing_contract.py`, `interpretation_contract.py`, `source_processing_contract.py`, and `source_scope_contract.py`. Implementations declare their protocols explicitly, as the harness and terminal implementations do. The engine imports the named host contracts, data models, and core mapper, not worker or runtime services. Application providers compose those services. Feature implementations continue to use the public SDK protocols.

`ExtensionProcessingBatch.interpret_pending` has separate `stopped` and keyword-only `yield_requested` predicates. The latter defaults to no timed yield, so existing direct callers keep their bounded page behavior. Application stop is checked before every original. Timed yield is checked only after at least one original is complete. Neither predicate interrupts a journal or transaction. A slow page read cannot prevent all progress, and a timed yield cannot override application stop. The engine invokes this method at most once per pass and currently supplies a one-second monotonic interval. It owns the RAW continuation after a positive result. An empty result clears that owned timer, even after interval expiry; application stop does not schedule more work. The runtime remains retained through the following core reactions. This changes neither the public feature protocols nor the worker deadline, and it does not provide a hard time bound on a complete pass.

`repository.contract.interpretations.CanonicalFactReader` is the host's narrow mixed read protocol. `current_fact_page(after_cursor, limit)` returns accepted core and extension facts from one live history and SQL snapshot. `InterpretationRepository` also implements that read contract. The core reaction loop uses the narrow contract without receiving interpretation write access. It advances to consumed rows, not the page head. `private_committed()` converts a stored core fact to the private core model and preserves cursor and acceptance time; it rejects an extension fact.

Both `current_fact_page` and `facts_for_scope` return at most the requested count and apply a 4 MiB budget to stored UTF-8 payload, scope, and extension metadata. They return the first fact alone if it exceeds that budget. This is not a complete encoded-response or process-memory limit. A page can be shorter than `limit` while more facts remain. Only an empty page proves no remaining facts in the selected stream snapshot. A consumer continues from the last returned cursor. SQL filters history, scope, and the starting cursor before counting bytes; excluded scopes and candidate histories cannot consume the selected page budget.

`SessionDataWrite.advance_past_extensions(canonical_cursor)` advances only the core consumer. The repository checks that the live target is an extension fact and that the unprocessed gap contains no core fact. Check and write share one transaction. Candidate targets are rejected. Exact and older retries retain stored state. The method creates no display rows or reader notices. Extension projection and observer progress require their own P05 transactions. These host contracts are not public extension capabilities.

The coordinator selects actual active providers and declared scope kinds. It gives every describe, read, and release call its own ledger grant. Captured settings and runtime identity remain fixed through the batch. Source ID and checkpoint identity are independent of runtime replacement. The default policy permits four read pages per source pass, a 0.01-second continuation, a one-second retry, and the current 30-second worker call limit. Complete pass bounds and runtime cost measurements remain open.

The source deadline notice does not scan core harness inputs or poll idle watched files. A file notice refreshes plans. A deadline-only pass uses healthy retained plans and retries failed plans when due. A successful retried plan preserves its outstanding file read. Native watches keep core and extension selections separate and include lexical links and resolved targets. Removed sources and scopes use checked release replies; pending cleanup stays scheduled. Worker replacement uses complete lifecycle deactivation and closure, not separate source release calls in the old runtime. See [engine source ownership](architecture.md#source-processing-in-the-engine).

Translation rules:

- `TranslationInput` links a `RawInput` to its stored schema reference, source time, and cause IDs. The raw input must have extension origin, the selected owner, and the selected scope. Source schemas must be declared. The immutable content snapshot must contain the exact referenced bytes. Source metadata remains linked to the original observation after a raw transform changes its content.
- `TranslationState` is a host-owned revision plus an optional encoded document. The result repeats the revision and supplies the complete proposed next document, including `None` to clear state. The host must compare the revision and commit next state with the interpretation. No worker writes active decoder state.
- Every input receives one `TranslatedInput`, `IgnoredInput`, `UnsupportedInput`, or `FailedInput` decision in input order. A translated verdict requires at least one fact. Intentional ignore, unknown input, and decoder failure remain distinct for audit and E2E checks. An empty raw batch returns no decisions but keeps its explicit state boundary.
- `TranslatedFact.fact_key` is owned by the package within the complete scope. It must include the distinctions that identify the feature's logical fact. `TranslationIdentity` and `translated_event_id()` derive a `translated:v1:` ID from owner, scope, and key. Raw row IDs, runtime revision, history revision, and settings revision do not enter that identity. A fixed encoding fixture protects this draft algorithm.
- Keys are unique within one input decision. Several input observations can propose the same logical ID. The complete result retains all proposals and source decisions. `translated_candidates()` selects the first proposed body for the canonical transform batch. The host must store all source links and the full proposal and preserve first acceptance within a history revision.
- Extension facts use registered event types and owned schemas and retain original canonical cause references. Core facts remain the closed typed session branch and keep the exact original raw source reference. All candidates keep the selected scope and derived logical ID. Host actor, lifecycle, stored-cause, and current-revision checks remain required before commit.
- The result is limited to 1,000 total candidate proposals and 4 MiB encoded. Translation runs in the ordered pure lane and cannot call live SDK services. Capture all decoder state and required input for replay. The existing harness translators are unchanged by this new extension contract.

The SDK-only journal fixture runs outside the checkout. It verifies partial lines, file replacement, bounded read failures, resume after a new worker starts, source release, repeated logical facts, and translation after the source file is removed. Separate host tests now connect that backend to an actual private daemon, native watches, and atomic source storage. They check complete lines, file replacement, retained-package restart, saved progress, and disable. Direct host scope-lease tests store repository observations without sessions. No actual Git feature or repository-view consumer exists yet. These host tests inspect storage and are not complete package-owned C07/C21 E2E checks.

## Projection and owned records

These draft contracts exist in `contracts.projection`, `models.projections`, `models.records`, `models.record_changes`, and `models.projection_entries`. `runtime.projection` supplies `RemoteProjector` and `WorkerProjector`. Both explicitly implement `ExtensionProjector`. Selection and projection run in the pure lane. Core derived-data mapping and the projection-transform worker now exist; host storage and application callers remain pending.

The read sequence keeps feature key rules out of the host:

1. The host selects immutable committed facts and a prior projection snapshot. `ProcessingContext.input_cursor` is the target canonical checkpoint. `ProjectionBinding.after_input_cursor` is the prior canonical checkpoint. `SnapshotCursor.commit_cursor` belongs to the derived-data store; it need not equal either canonical cursor.
2. `select_records()` returns declared, owned collection keys. It cannot read live data. Keys must be unique and keep the request binding and scope.
3. The host captures those keys at the selected snapshot in one read transaction. It returns a complete row, a deletion marker, or explicit missing-key proof for each key. It must retry selection with a new binding if the selected snapshot is no longer available. Do not hold a database transaction open across a worker call.
4. `capture_projection_request()` checks exact key coverage and order and all captured row boundaries. It does not perform the read or prove that the caller used a real database snapshot.
5. `project()` returns one complete proposal. The worker checks input and output; the host must repeat acceptance checks against its current runtime and storage state before writing.

Current rules:

- Facts keep the context scope. Their IDs are unique, their cursors increase, and each cursor is greater than the prior canonical checkpoint and at most the target checkpoint. Selected facts can omit unrelated fact types. Empty input and empty output keep the checkpoint binding so the host can advance progress.
- The manifest's projector selection declares input types and scopes. Core inputs keep the closed `CoreFact` model. Extension input documents use registered schemas. Prior record state belongs to the projector. Peer facts can support cooperation; direct writes to peer-owned records are not permitted.
- Each prior record key occurs once. Its revision cannot exceed the projection snapshot commit cursor. A missing key is not the same as an omitted key or a deleted row. Stored rows contain a document and safe fallback summary. Deletion markers retain the exact collection schema and last host revision.
- A result repeats the entire binding. Every write names a captured key and its exact expected revision. Put can create, replace, or restore a row. Delete requires a stored row, not an absent key or an existing deletion marker. One result cannot write the same key twice.
- `Contributions.entry_types` declares feed schemas separately from canonical event schemas. Each entry has a safe summary and a source fact in this request. Entry keys are unique per source fact. Response order is display order. The same key can appear for two different source facts.
- `ProjectionEntryIdentity` and `projected_entry_id()` use owner, scope, source fact ID, and entry key. The fixed V1 encoding excludes settings, runtime, history, projection generation, display content, and commit revisions. The host assigns the resulting storage ID and feed position.
- A request has at most 1,000 facts and 1,000 captured rows, with an 8 MiB encoded limit. A read set has at most 1,000 keys and a 1 MiB encoded limit. A result has at most 1,000 feed rows and record writes combined, at most 1,000 diagnostics, and a 4 MiB encoded limit. Do not truncate captured evidence to fit these limits. Host batching and the recorded failure policy remain required.
- Validation checks the complete proposal before returning it. A malformed entry or stale record write rejects the whole result. No helper in this SDK commits a cursor, proves an atomic host transaction, or modifies the original canonical fact.

This projector creates extension feed rows and changes its own records. The distinct projection-transform capability below can change proposals from core writers and earlier extensions. The current host still has its single-entry `SessionDataChanges` model. P05 must replace that model and commit core changes, extension records, feed rows, and consumer progress together.

## Core derived data and projection transforms

Current draft: `contracts.projection.ExtensionProjectionTransformer` defines `transform(ProjectionTransformRequest) -> ProjectionTransformResult`. `runtime.projection_transforms` supplies its local validation adapter and remote proxy. The method runs in the pure lane and cannot call live SDK services.

The wire models keep core data closed:

| Model | Contents |
| --- | --- |
| `CoreAggregateState` | Optional complete session row and complete actor rows. Nested usage, background work, and calculation fields remain typed. |
| `CoreSessionEntry` | Entry identity, session, actor, parent, turn, time, summary, and one of the 25 closed `CoreEntryBody` variants. It has no host commit cursor. |
| `ProjectionChange` | Tagged session, actor, core feed, extension feed, or extension record proposal, with a distinct change ID. |
| `ProjectionTransformRequest` | Exact `ProjectionBinding`, committed `events`, `before_core`, captured `prior_records`, and ordered `changes`. |
| `ProjectionTransformResult` | The unchanged binding, explicit transform operations, and diagnostics. |

`extensions/mapper/core_aggregates.py` and `core_entries.py` map all current private fields without loss. A new private entry has cursor zero; the host must assign its stored cursor. `CORE_PROJECTION_SCHEMA_VERSION` is 1 and is independent of the private storage version. Field, enum, and body-registry tests detect drift.

Operation rules:

- Omitted input is kept. Keep, replace, drop, and insert use the existing explicit operation model. Additions can precede or follow an original change, even when that change is dropped. An addition is not an anchor in the same pass.
- Core session and actor replacements can change display fields. Identity, lifecycle, active-work references, and internal calculation inputs must remain unchanged. Dropping a display-only update is permitted. Dropping required lifecycle progress, or creating a session or actor, requires canonical processing instead.
- Core feed rows can be replaced or suppressed. A replacement retains entry identity and source session, actor, parent, and turn. A new core feed row must use a core feed anchor and its captured cause.
- Inserted change IDs use `derived_projection_change_id()` with extension ID, input change ID, and output key. A new core entry uses that ID as its entry ID. A new extension feed row uses it as its local key; storage derives its final feed ID from owner, scope, cause, and local key.
- A transform can change or suppress peer feed and record proposals. Replacements retain peer ownership, exact schemas, record keys, and expected revisions. New extension feed and record proposals must use the current package's declarations. A record insert still requires a captured key. Record selection must happen before this pure call.
- A dropped record proposal means no write, not deletion. Delete remains an explicit operation against a captured stored row. All proposed record changes use the same revision checks as projectors.
- The complete result rejects duplicate write targets, unknown causes, wrong scopes, conflicting operations, invalid schemas, and cycles in the combined actor graph. An actor can refer to a parent not yet present in the captured history. This matches current core processing.
- Requests allow at most 1,000 facts, 1,000 captured records, 1,000 proposed changes, and 1,000 captured actors, with an 8 MiB total encoded limit. Replies allow at most 1,000 operations and 100 diagnostics, with a 4 MiB encoded limit. Kept and inserted output combined cannot exceed 1,000 changes.

These checks validate supplied data, not host authority. The host must validate each producer's declarations before composing peer proposals, capture one real snapshot, reject stale runtime or storage state, retain the audit, and commit all accepted changes atomically. None of those storage steps is implemented by this pure SDK path. The external worker fixture proves package-local feature code and process calls, not a daemon transaction or product E2E.

## Commands and queries

Use separate query and command protocols:

| Protocol | Method signature | Behavior |
| --- | --- | --- |
| `ExtensionQueries` | `query(QueryRequest) -> QueryResult` | Read current stored extension data or a declared service result. |
| `ExtensionCommands` | `execute(CommandRequest) -> CommandResult` | Run one accepted durable job. |
| `ExtensionCommands` | `cancel(CommandCancelRequest) -> CommandCancelResult` | Request cancellation and report whether it is complete. |
| `ExtensionCommands` | `reconcile(CommandReconcileRequest) -> CommandResult` | Determine the result after a lost worker or reply. |

`ExtensionMigrations(Protocol)` now offers `migrate_settings(SettingsMigrationRequest) -> SettingsMigrationResult` and `migrate_records(RecordMigrationRequest) -> RecordMigrationResult`. Requests contain source and target schema versions and captured values. Results contain validated candidate values. Both methods run in the pure worker lane and cannot use live SDK services. Host upgrade transactions remain open.

A backend that declares an extension observation type also supplies its `ExtensionTranslator`. A source provider is optional if observations arrive only through commands or submitted input. The manifest validator checks this relationship.

`CommandRequest` contains extension ID, registered command ID, scope, request key, job ID, typed arguments, runtime revision, and expected state revision. Commands return typed results and content references. Large results do not travel as unbounded messages.

Current implementation: `contracts.operations` defines `ExtensionQueries` and `ExtensionCommands`. `runtime.queries` and `runtime.commands` provide local validation adapters and remote proxies. Their methods use these draft rules:

- `OperationBinding` carries owner, operation ID, scope, runtime revision, and call ID. Replies repeat the exact binding. Each new call has a new call ID.
- `QueryRequest` includes arguments, captured settings, settings revision, an optional selected snapshot, an optional page cursor, and a strict limit from 1 to 200. `QueryReady` has a schema-checked document and snapshot. `QueryFailed` has a diagnostic. A failed read is not a successful empty result.
- `QueryPageCursor` contains the selection, snapshot, and opaque page position. The selection hashes the complete encoded argument document, including its schema reference. It also contains owner, operation, scope, runtime, and settings revision. It excludes call ID and page size. A reply cannot change the selected snapshot or return the same next-page cursor. Stored snapshot cursors must use the selected scope.
- `CommandBinding` adds stable `job_id` and `request_key`. A dispatch or reconciliation attempt has its own call ID. Cancellation names the exact executing attempt. Success, failure, canceled, and `outcome_unknown` are separate tagged results.
- A command declaration supplies argument and result schemas, scopes, effect classification, and reconciliation support. A write request must include `expected_state_revision`. Only the future host job service can compare it with current state, apply read-only policy, and deduplicate durable requests.
- A cancellation result distinguishes `requested`, `canceled`, `not_running`, and `outcome_unknown`. A request acknowledgment does not become the stored final result. Reconciliation calls only the capability's `reconcile()` method; the proxy does not repeat `execute()`.
- `ObservationCandidate` supplies a stable source identity and observation key, owned source type, scope, encoded document, source time, and cause IDs. A command result permits at most 256 observations and 128 content references. Source keys and content IDs are unique in the result. Registered source schemas and the accepted operation scope are checked before the host receives the result.
- Encoded query replies are limited to 2 MiB; command replies are limited to 4 MiB. These draft bounds are not performance claims. Content storage and reference resolution remain host work.

The worker uses one ordered pure thread, four live threads, and one reserved control thread. Each lane permits 16 running or queued calls. Commands, queries, and reconciliation use the live lane. Cancellation uses the control lane. A full live lane does not prevent cancellation, but a feature can still ignore a stop request or block its control thread. Host supervision must handle that failure.

The external process fixture verifies a query and host directory callback during a slow command, a raw transform during that command, stale cancellation rejection, and reconciliation without a second execution. Its proof is held in memory. It does not prove restart recovery, atomic observation storage, or exactly-once external writes.

Store command definitions with argument and result schemas, scope kinds, write classification, and reconciliation support. Add and test host read-only admission before exposing write commands. A browser must not bypass it by calling a route directly. Current source comments mention a read-only switch, but inspection found no working general switch. This is required implementation work, not an existing protection.

## Durable observer protocol

`ExtensionObserver` now has a draft protocol, complete request and result models, and local and remote adapters. All 12 backend capability names in the manifest have worker support. Application supervision and most host-service protocols remain open.

An `ObserverSelection` uses the `observer` tag in `contributions.processing`. It declares input types, scopes, required read or write effect classification, and optional reconciliation support. Pure selections remain `ProcessingSelection` values and cannot acquire observer write fields. Each capability has at most one selection. Declaration checks run before feature import.

`ObservationJobRequest` contains one committed core or extension fact. Its binding contains owner, scope, runtime revision, history revision, job ID, call ID, and trigger event ID. The trigger ID and scope must match the binding. The request also has captured settings, a settings revision, a finite positive deadline, and an optional expected state revision. Write-class observers require that state revision. The only valid mode is `live`; a replay request is rejected before feature execution.

The host selects one stable job for owner, scope, and committed cause. It must check that the input is truly committed in the selected history, authorize the current attempt, enforce read-only policy, check expected state, and enforce the deadline. A supplied job ID or positive cursor does not prove those facts. Reconciliation has its own transport deadline and can inspect an original request whose execution deadline has passed.

`ObservationJobResult` is a tagged success, known failure, canceled result, or uncertain outcome. Every result retains the exact binding. Success can contain no observations. An uncertain result can include an owned, schema-valid receipt. New observations must use registered owned source schemas, keep the operation scope, have unique source keys, and include the committed trigger ID as a cause. Content reference IDs are unique. The complete request is limited to 8 MiB, complete response to 4 MiB, and output to 256 observations and 128 content references.

Observe and reconciliation run in the live lane, separate from pure processing. Cancellation runs in the reserved control lane. A stop acknowledgment does not assign the final durable job state. The proxy calls the declared recovery method; it does not call observe again. The feature must inspect proof rather than repeat an external write. Worker processes are not an OS security boundary.

Separate-package process tests cover live callbacks, concurrent queries and transforms, exact cancellation, rejected replay and invalid schema input, recovery without a second execution, and loss of proof after a new worker starts. The fixture keeps proof in memory and reports uncertainty after restart. It does not prove durable job recovery or exactly-once external effects.

P05 must commit observer job insertion with its consumer cursor, and result observations with the final job outcome. It must resolve all stored causes, deduplicate results and jobs, apply generated-observation cycle limits, keep replay from creating jobs, and preserve uncertain jobs across failure. The current application does not dispatch observers.

## Settings and record migrations

The manifest's `migration_paths` declares each exact supported conversion. A settings path names source and target `SchemaRef` values. A record path also names its owned collection. References include owner, name, version, and digest. Both schemas must be registered; the target must match the package's current settings or collection schema. Duplicate paths, unchanged schemas, foreign owners, missing schemas, and unregistered targets are rejected. A `migrations` capability requires at least one declared path.

No reverse conversion is inferred. A package with current schema version one can explicitly declare a source-version-two to target-version-one path. Each path leads directly to the current target. A package can perform intermediate conversion steps inside its pure implementation; the host does not select an implicit chain.

`MigrationBinding` contains owner, scope, prepared runtime revision, candidate ID, and call ID. A reply retains the complete binding. The SDK validates supplied data; the host remains responsible for the existence and authority of that candidate.

`SettingsMigrationRequest` adds source and target schema references, a captured settings revision, and the complete old document. A ready result repeats the source revision and supplies the target document. A failed result has a typed diagnostic and no partial document. Credential values have no separate request field. Host settings and credential services must still keep secrets out of ordinary settings documents and reports.

`RecordMigrationRequest` adds a collection, source and target schemas, a source snapshot, and an ordered tuple of stored rows. All rows must have the selected owner, collection, and scope. Their revisions cannot be newer than the snapshot. Duplicate keys are rejected. Ready output contains one `PutRecord` candidate per input row, in the same order, with the same key and expected revision. It can change the value and safe fallback summary, but cannot allocate a new stored revision, add a row, drop a row, or change a key. Failure returns no partial rows. Deleted and missing rows need no value conversion and do not enter this protocol.

Complete input is limited to 8 MiB, output to 4 MiB, and record batches to 1,000 rows. Each document also retains the common document bound. Runtime adapters check source schemas before the callback and every target document before returning the result. The real worker tests cover restart, explicit downgrade, failed conversions, malformed target values, stale runtimes, and blocked live callbacks for both methods.

These SDK contracts do not read or write host storage. The daemon now invokes `migrate_settings` during package enable or reload when explicit saved values need a declared conversion. It captures old raw values, checks exact paths, converts in the pure worker lane, and activates with complete output. Schema 29 stores the checked result separately from its immutable input. The runtime and raw values publish together. The daemon also invokes `migrate_records` for each stored collection whose schema changed, into a migrating projection generation that the lifecycle commit makes live; see the architecture section on candidate settings migrations. Secret references remain open in P05.

## Host services

Pass `ExtensionHostServices`, a frozen group of narrow service protocols, to the factory. Each service is restricted to declared access and scope. These checks help prevent mistakes in trusted extensions; they are not an OS sandbox.

| Service protocol | Public work |
| --- | --- |
| `ExtensionDirectory` | Read installed and active metadata; subscribe to lifecycle changes. |
| `ExtensionServiceAccess` | Resolve a declared versioned public service; call its query or command. |
| `ExtensionRecordReader` | Read paged, schema-validated records through supported filters and cursors. |
| `ExtensionObservationSink` | Submit new observations with source and cause IDs. |
| `ExtensionProcessService` | Run declared commands with argument arrays, cwd, deadlines, output references, and cancellation. |
| `ExtensionInferenceService` | Call the existing model service through a typed request. |
| `ExtensionCredentialService` | Resolve or update a named secret reference without placing secret values in settings reads. |
| `ExtensionAuditService` | Record typed extension diagnostics with job or event context. |

Transform and projection methods must not call live services. Supply all their required data through the request snapshot. Pure methods may use local computation and the SDK codecs. Tests detect forbidden service calls and compare repeated results.

## Declared peer service reads

The read subset of `ExtensionServiceAccess` now exists in `contracts.service_access`. It has `resolve_service(ServiceResolveRequest) -> ServiceResolveResult` and `query_service(ServiceQueryRequest) -> ServiceQueryResult`. `ExtensionHostServices.service_access` is absent when the manifest declares no consumed services. Peer commands and directory lifecycle notifications remain open.

Current models in `models.services`:

| Model | Fields and rules |
| --- | --- |
| `ServiceBinding` | Target owner, public service ID, scope, and feature call ID. This call ID binds the result; it is not a grant. |
| `ServiceRevision` | Exact package version, service version, and runtime revision selected by the host. |
| `ServiceResolved` | Exact binding, service revision, and public query definitions valid for this scope. Private queries and settings values are absent. |
| `ServiceUnavailable` | Exact binding and one reason: `not_installed`, `not_enabled`, `not_provided`, `incompatible`, or `stale_handle`. |
| `ServiceQueryRequest` | Binding, service revision, declared query ID, arguments, optional snapshot and page cursor, and limit from 1 to 200. No caller, route, or peer settings fields. |
| `ServiceQueryResponse` | Exact binding and service revision, host-selected settings revision, and the typed query result. It does not return settings values. |

`HostServiceAccess` is built for one validated `WorkerLoadRequest`. It authenticates the caller through that connection, not through fields sent by the feature. It checks the exact consumed owner and service before reading provider metadata. `ServiceProviderLookup` supplies the target manifest, schemas, active environment, query capability, and captured effective settings. Package and service version ranges must match. Only queries explicitly exposed by that service and valid for the scope can run. Arguments and successful output must satisfy their registered schemas. Normal snapshot and page-cursor rules still apply. Service replies have a 4 MiB encoded bound; the contained query reply retains its 2 MiB bound.

Resolution reads metadata only. It can run during factory preparation before the package has query authority. An installed inactive peer returns `not_enabled`. The feature can resolve again later. This does not grant execution or replace future lifecycle notifications.

Execution uses host-held authority:

1. The host authorizes a root query or accepted live job with `HostCallLedger.root()`. The grant fixes caller environment, scope, and a monotonic deadline. Root authorization still belongs to the application; a feature request cannot create it.
2. RPC carries an opaque `host_call_id` outside the feature request. Executor threads and nested callbacks retain it. Replies must retain the same ID. The host verifies it against the active ledger; possession alone is insufficient.
3. A service callback requires the exact caller environment and scope, a live deadline, and all active parents. Missing, fabricated, released, revoked, or expired authority is rejected before peer execution.
4. `forward()` creates a child for the selected peer. Scope and deadline do not change. The stored route cannot repeat an owner or exceed 16 levels. The ledger allows at most 1,024 active grants. Feature payloads cannot set this route or these parent links.
5. Transport waits use the shorter configured timeout or remaining host deadline. After a peer result, the host checks authority again before returning it. Context exit releases the grant. `revoke_runtime()` removes matching active grants and invalidates children through their parent links.

Pure transforms, projections, translations, migrations, and terminal presentation cannot call the live service proxy. The separate-worker test returns proof of the exact pure guard, so an earlier request identity error cannot count as success. Two package processes also test both load orders, absent authority, an inactive peer, and a nested call cycle. The cycle case tests this guard independently of active-set dependency validation; it is not a valid activated package graph.

These components are not a daemon service registry. P03 must coordinate immutable provider reads with activation, create authorized roots, prevent new calls to stopped runtimes, revoke active calls, and publish lifecycle notices. A grant does not prove durable job acceptance or read-only policy. Peer commands must later use P05's durable acceptance, status, cancellation, and recovery path. Do not route a write through a query or call `ExtensionCommands.execute()` directly from service access.

## Host lifecycle contract

Application control uses `ExtensionManager(Protocol)`. The implemented contract separates complete runtime operations from catalog reads and user request planning. It offers `read_state`, `read_operation`, `submit_operation`, `publish_ready`, `retry_cleanup`, and `close`. The engine consumes only the narrower `ExtensionRuntimeBoundary.publish_ready` protocol. `ExtensionLifecycleControl` turns enable, disable, and reload requests into checked host proposals, including captured settings migration input. `ExtensionSettingsControl` supplies ordinary settings reads and checked scope edits. Record migrations run with lifecycle preparation. Secret handling and related-scope resolution remain open. HTTP must not accept a feature-selected runtime identity or a complete `LifecycleProposal` as user authority.

`ExtensionManager` is a host contract, not a method exposed to every extension. Feature extensions can inspect peers through `ExtensionDirectory`; they do not enable or disable other packages.

Current host subset: `ExtensionCatalog(Protocol)` offers `catalog_snapshot() -> ExtensionCatalogSnapshot` and `rescan_packages(expected_revision) -> CatalogWriteResult`. `ExtensionPackageScanner(Protocol)` returns a typed `PackageScan`. The application uses these smaller contracts for data-only discovery. The manager does not duplicate these methods. A catalog entry does not claim that a package is enabled or ready.

`ManagedExtensions` explicitly implements both manager protocols. `ExtensionManagerFactory.open_manager()` acquires process ownership, claims a fresh stored manager generation, and submits a restore operation. `submit_operation()` returns durable admission, not activation success. One executor prepares the candidate and signals the engine. `ManagerSnapshot` separates stored lifecycle state, the actual active runtime, preparation phase, and retained cleanup issues. `ManagerProgress.allow_processing` lets startup hold core work until restoration succeeds or records its failure. A lost manager generation stops processing through this boundary.

`publish_ready()` does not wait for preparation or call feature cleanup. Busy publication keeps the same candidate. A failed database commit leaves it ready for retry. Successful publication transfers the candidate to active ownership and schedules old-resource cleanup outside the engine thread. Cleanup reports unresolved jobs, failed deactivation, or failed resource closure through `RetirementIssue`. This state is not yet durable health or job reconciliation. Public lifecycle routes now use the manager through `ExtensionLifecycleControl`. Ordinary settings GET and PUT routes now use `ExtensionSettingsControl` and the same manager.

Approved shutdown change: `RetirementOwner.deactivate`, `close_resources`, and `observation` separate work acknowledgement from physical resource closure. `ExtensionLifecycleRepository.record_extension_shutdown(ShutdownRecord) -> bool` retains immutable observations for a known manager without changing runtime intent or job outcomes. The manager requires its native lease before each write. Schema 34 stores the observation before close and again after close. `LifecycleState.last_shutdown` and `ExtensionRuntimeResponse.last_shutdown` expose the latest record after restart. A failed storage write or physical close retains native ownership. Unresolved jobs alone do not prevent physical shutdown after processing has stopped. They remain explicit evidence for later reconciliation. Replacement behavior, worker wire models, and package layout are unchanged.

`ExtensionArtifacts(Protocol)` now captures and reads fixed package copies. `PackageCaptureRequest` contains the resolved source path, expected manifest, and expected digest. `PackageArtifact` contains the checked published directory, manifest, and digest. The application capture scanner requires a valid copy before accepting successful package metadata. A failure is reported as `capture_failed`. This does not prepare a Python environment or activate a worker.

The active registry is a separate host protocol in `extensions/registry_contract.py`. `read_snapshot() -> AbstractContextManager[RegistryRead]` holds a fixed capability set for a complete operation. `publish_snapshot(expected_revision, snapshot, commit) -> RegistryPublication` rejects stale writers or active readers before committing and returns `accepted`, `stale`, or `busy`. `RegistryCommit.commit_registry(selection) -> bool` is required. It must accept the exact runtime durably before returning true. It must not call workers or re-enter the registry. `StoredRegistryCommit` supplies the lifecycle repository adapter. The mutex stays held through the stored commit and prepared pointer replacement. `RegistryRead` binds the local registry revision to `RuntimeSnapshot`. Capabilities must not escape the read context. The manager retains worker ownership.

`ExtensionRegistry.close_registry(timeout_seconds)` stops new reads and publication, then waits for admitted reads without keeping the registry mutex held. A timeout returns false and leaves admission closed. Existing borrowed selections remain valid until context exit. `ExtensionManager.close()` retains its native lease if those readers or unresolved workers remain. It does not save an empty runtime selection. A later start restores the last committed set under a new runtime ID.

`RuntimeSnapshot` implements the existing SDK `ExtensionDirectory` and `ServiceProviderLookup` protocols. `RegistryDirectory` and `RegistryServiceAccess` acquire reads before feature callbacks. The service adapter keeps its read until the full peer query and authority checks finish. It permits declared metadata lookup during preparation, but a live query requires the exact active caller environment and a host grant. The manager now owns the registry; root application query and command calls remain open. No SDK wire protocol change was needed for this registry subset.

Current durable lifecycle contract: `ExtensionLifecycleRepository` reads lifecycle state, claims a manager generation, accepts and finishes an operation, and reads retained operation or runtime history. `LifecycleProposal` pins the full candidate, affected requested states, and revision-checked raw settings changes. `LifecycleCompletion` identifies the accepted operation. Only a migrating candidate permits a `RuntimeResolution`, with exact package identities and complete converted overrides. It cannot substitute another runtime or change unaffected values. `LifecycleAdmission` distinguishes `accepted`, `replayed`, `stale`, and `busy`. `LifecycleOperation` distinguishes `preparing`, `succeeded`, `failed`, and `interrupted`; successful migrations retain their exact resolution.

`LifecycleState.committed_runtime` means the last accepted stored selection, not a live process claim. `RuntimeSelection` contains ordered `RuntimePackageSelection` values with exact package identity and captured settings. `OwnerSettings` stores `SettingsOverrides`, whose absent installation value inherits the manifest default. Manager and operation IDs use the existing SDK identifier type at repository boundaries. The runtime ID is reserved durably at admission. The operation document has an 8 MiB host bound. These are host models, not a new feature callback API.

`RuntimeCandidate` is the union of complete and migrating selections. A `MigratingRuntimeSelection` contains at least one `MigratingRuntimePackage`, whose `source` is the exact saved raw settings. It is not a committed runtime. `RuntimeResolution` supplies one complete `RuntimeSelection` and ordered `SettingsChange` values only for those migrating owners. Repository completion checks target schemas and captured effective values. `read_extension_runtime` returns the unresolved reservation before success or the complete result after success. The committed-state field remains strictly `RuntimeSelection`.

Current complete preparation contract: `ExtensionRuntimePreparation.prepare_runtime(selection, stop_requested)` accepts a `RuntimeCandidate` and returns `PreparedExtensionRuntime`, which exposes a checked `snapshot`, optional `resolution`, and `close()`. `stop_requested` is a host thread event, not a feature wire field. Preparation checks it before planning, between packages and conversion calls, after worker ownership, and before returning. This is cooperative cancellation; an active install or worker request keeps its own timeout. Preparation checks every fixed artifact before starting workers. A ready candidate owns fresh workers for its exact runtime revision. Failure releases only that candidate's resources. Preparation does not write lifecycle state or publish the registry. The manager retains a busy candidate for retry and closes old owners only after removal and drain.

Current process-ownership contract: `ExtensionRuntimeOwnership.acquire_runtime()` returns `ExtensionRuntimeLease` or fails without waiting. `hold_ownership()` rejects closed or inherited leases and prevents concurrent close during one manager operation. This context is not reentrant. `close()` releases ownership; call it only after all contexts and owned worker work have stopped. The native file-lock implementation uses the resolved data directory, not a server port. The daemon manager acquires this lease before claiming its stored generation. Request-only applications do not open a manager.

## HTTP contract

The API uses typed request and response models. The table includes the target API. The current discovery, lifecycle, and ordinary settings routes are specified below; other routes remain proposed. Include each implemented route in generated API types:

| Route | Request or selection | Result |
| --- | --- | --- |
| `GET /api/extensions` | Optional scope filter | Installed packages, active contributions, and catalog revision |
| `GET /api/extensions/{id}` | Extension ID | Manifest summary, actual and requested state, health, dependencies |
| `POST /api/extensions/rescan` | Expected catalog revision | Updated catalog or a revision conflict |
| `GET /api/extensions/state` | No body | Actual runtime metadata, committed references, requested state, cleanup, and write policy |
| `POST /api/extensions/{id}/lifecycle/preview` | Action, expected lifecycle and catalog revisions, exact digest for enable/reload | Checked affected-owner set without admission |
| `POST /api/extensions/{id}/lifecycle` | Enable, disable, or reload; expected revision; selected dependent changes | Accepted operation ID and current state |
| `GET /api/extensions/operations/{operation_id}` | Operation ID | Pending or final lifecycle result |
| `GET /api/extensions/{id}/settings` | Typed scope | Effective settings, overrides, schema, and revision; no secret values |
| `PUT /api/extensions/{id}/settings` | Scope, expected revision, typed values or secret updates | Validated settings and activation operation where needed |
| `POST /api/extensions/{id}/queries/{query_id}` | Scope, typed arguments, page or snapshot cursor | Validated declared query result |
| `POST /api/extensions/{id}/commands/{command_id}` | Scope, request key, expected state, typed arguments | Accepted durable job ID |
| `GET /api/extensions/{id}/jobs/{job_id}` | Job ID | Progress, outcome, or reconciliation state |
| `POST /api/extensions/{id}/jobs/{job_id}/cancel` | Expected job revision | Cancellation request state |
| `GET /api/extensions/{id}/changes` | Scope and revision cursor | Typed SSE frames or snapshot-reset instruction |
| `POST /api/extensions/rebuilds` | Scope, rebuild kind, target runtime revision, expected active head | Candidate rebuild operation ID |
| `GET /api/extensions/rebuilds/{rebuild_id}` | Rebuild ID | Progress, validation, and paged comparison with the active revision |
| `POST /api/extensions/rebuilds/{rebuild_id}/activate` | Expected active head and validated candidate ID | Atomic switch result or a state conflict |
| `GET /extensions/{id}/{digest}/{asset_path}` | Declared asset path | Validated static file with content type and digest caching |

Current discovery API:

- `GET /api/extensions` returns `ExtensionCatalogResponse` with `revision`, package `entries`, and `root_issues`. It has no scope filter or active contributions yet. Entries contain checked identity, source paths, capabilities, digest, and an optional discovery issue. Full manifests, schemas, E2E declarations, and settings are not returned.
- `POST /api/extensions/rescan` accepts `RescanExtensionsRequest(expected_revision)`. A complete synchronous scan returns 200; a stale revision returns 409. It never starts workers or changes requested activation. Foreign browser Origins return 403; a non-JSON content type returns 415. Local clients can omit Origin. `BAQYLAU_EXTENSION_READ_ONLY=1` rejects rescan with 403. This is extension-only write policy, not a general application switch.
- `sdk.client.BaqylauClient.extensions` provides typed catalog and rescan methods. Its `lifecycle` resource provides state, operation, preview, and change methods. The SDK imports API contracts, not host services or repositories. Dashboard OpenAPI types include these routes. No settings page or dynamic application view is implemented yet.

Current lifecycle API:

- `GET /api/extensions/state` returns `ExtensionRuntimeResponse`. Active directory metadata is distinct from the last committed selection. The response also has requested enable state, the pending operation, retained cleanup issues, and `read_only`. It omits settings documents.
- `GET /api/extensions/operations/{operation_id}` returns `ExtensionOperationResponse`, or 404 for an absent operation. It exposes pending or final status, request and runtime references, timestamps, and a bounded host failure. It omits the complete proposal and private values.
- `POST /api/extensions/{extension_id}/lifecycle/preview` accepts `LifecyclePreviewRequest` and returns `LifecyclePlanResponse` with 200. It checks the same selection as admission but does not record or prepare work.
- `POST /api/extensions/{extension_id}/lifecycle` accepts `LifecycleChangeRequest`. Fields are `action`, `expected_revision`, `expected_catalog_revision`, `package_digest`, `request_id`, and `confirmed_dependents`. Enable and reload need an exact digest; disable must omit it. Only the JSON array container is converted to a tuple. IDs, revisions, and other values remain strict. Extra authority fields are rejected.
- Accepted and exact-replayed requests return `LifecycleAdmissionResponse` with 202. Poll the operation to distinguish `preparing`, `succeeded`, `failed`, and `interrupted`. An exact retry keeps the original operation across restart. A different body or target cannot reuse its request key.
- Foreign Origin returns 403, non-JSON media type returns 415, and invalid input returns 400. Revision or confirmation conflict returns 409. A missing or unavailable daemon manager returns 503. Read-only policy denies lifecycle mutation with 403, including exact retries, but permits preview and reads.
- API response enums have explicit host-value parity tests. HTTP models, request guards, and mappers remain in `api/extensions/`. The app provider constructs a lazy control service without starting workers. The domain planner remains in `extensions/`.

Current ordinary settings API:

- `GET /api/extensions/{extension_id}/settings` returns `ExtensionSettingsResponse(settings, read_only)`. The nested `SettingsSnapshot` includes selected package identity, scope, lifecycle/catalog/settings revisions, pending operation, declaration, bundled schemas, exact override, and effective document. `selected_from_committed` means stored package selection, not worker health.
- GET defaults to installation scope. Optional `scope` is a JSON-encoded public `ExtensionScope`, limited to 16,384 characters. Optional `package_digest` pins the expected bytes. A Pydantic scope adapter decodes the query; malformed content returns a bounded 400 error. Successful reads set `Cache-Control: no-store`.
- At most 1,000 exact non-installation overrides can be saved for an owner. A new override above this bound, or a complete operation above 8 MiB, is rejected before admission with a bounded 400 error. Edits and resets remain available at the scope limit. The operation bound covers the full candidate and request, not only the changed document.
- `PUT /api/extensions/{extension_id}/settings` accepts `SettingsChangeRequest`: `action=settings`, `request_id`, `expected_revision`, `expected_catalog_revision`, `expected_settings_revision`, `package_digest`, `scope`, and required nullable `document`. The document is a complete `EncodedDocument`; null resets the selected override. Extra authority fields and implicit reset are rejected.
- PUT returns the common `LifecycleAdmissionResponse` with 202. Read the operation result before treating an edit as saved. Invalid schema or scope returns 400; stale revisions or selected bytes return 409; read-only or foreign Origin returns 403; non-JSON media type returns 415; missing daemon ownership returns 503.
- `BaqylauClient.extensions.settings` supplies typed `read()` and `change()` methods. General state and operation responses still omit settings documents. Only the explicit settings read exposes ordinary accepted values.
- `ExtensionSettingsControl.read_settings()` and `change_settings()` are host protocols. `SettingsControl` implements them without direct database or worker access. `control_admission` shares exact retry with lifecycle changes. `ManagementRequestOrigin` retains either strict user request in the existing operation JSON.

These routes do not supply secret writes or reads, related-scope resolution, settings migrations, durable jobs, application queries, removal notices, or full runtime health. A credential placed in an ordinary settings document is not detected or redacted. Secret references need their separate planned service.

Use 202 for accepted work that is not complete, 409 for stale state or conflicting lifecycle changes, and the existing typed error response for failures. Query POST routes are classified as reads for the host read-only policy. Specific error codes are added to the declared error model in P03 and P05. A generic query or command route still validates the registered argument and result schema.

Lifecycle, settings, query, and job reads each use their own typed operation model. Do not return a successful enabled state when the result is only an accepted request.

## Web module interface

The draft public TypeScript interface is implemented in `packages/extension-api-web/src/view.ts`:

```typescript
export type ExtensionViewContext = {
  readonly extensionId: string;
  readonly viewId: string;
  readonly scope: ExtensionScope;
  readonly runtimeRevision: string;
  readonly settingsRevision: number;
  readonly settings: EncodedDocument | null;
  readonly theme: ThemeValues;
  readonly api: ExtensionClient;
  readonly signal: AbortSignal;
};

export type MountedExtensionView = {
  update(context: ExtensionViewContext): void | Promise<void>;
  dispose(): void | Promise<void>;
};

export type ExtensionWebModule = {
  mount(
    target: HTMLElement,
    context: ExtensionViewContext,
  ): MountedExtensionView | Promise<MountedExtensionView>;
};
```

`ExtensionScope`, `ThemeValues`, and `ExtensionClient` are public SDK types. They contain no host store references. The host selects `viewId` from the validated manifest. The module mounts its own component and returns its own cleanup methods. A package can use Svelte's [mount and unmount API](https://svelte.dev/docs/svelte/imperative-component-api).

Current implementation: The client exposes `listExtensions(DirectoryRequest) -> Promise<DirectorySnapshot>` only. Wire aliases now include Python query and command worker models. Browser job acceptance, subscription, and lifecycle operation models must still be defined before their client methods are added. A browser cannot supply host-owned job IDs or runtime authority by sending a worker request directly. Wire aliases are generated from the Python models with `openapi-typescript`; the view lifecycle interface is TypeScript-owned.

`ExtensionViewHost` is a generic SDK export for host use. It checks the asset owner, digest path, and same origin. It mounts package-owned CSS and DOM in a Shadow DOM. Data is copied and frozen. The client is a narrow facade, not the original host service object. Settings and theme updates retain view identity; a changed owner, scope, view ID, or runtime revision requires a new mount. The main dashboard does not yet call this loader.

Subscriptions, action handles, timers created through the SDK, and queries belong to the view's abort signal. The extension must clean up resources that it creates directly. Disable aborts the signal before the host removes the view. A stale asynchronous mount result is disposed immediately.

The loader serializes mount, updates, and disposal. It removes the old root and aborts its signal before waiting for package cleanup. It reports late cleanup errors. Same-page code can still block JavaScript or ignore its abort signal. Shadow DOM gives CSS and DOM ownership; it is not a security sandbox. Do not claim that the loader can terminate arbitrary JavaScript.

## Terminal protocol

`ExtensionTerminalPresenter(Protocol)` offers `present(terminal_request: TerminalViewRequest) -> TerminalView`. This draft protocol and its process proxy now exist. The request includes a `TerminalBinding` with extension ID, view ID, runtime revision, settings revision, and a complete `SnapshotCursor`. The snapshot carries the scope. The request also has a cell viewport, light or dark theme, optional selection, encoded view state, effective settings, and a recorded data document. The response repeats the exact binding and returns a title, typed block tree, and action references.

V1 block types are text spans, sections, tables, file trees, unified diffs, status items, and selectable lists. The current models are in `baqylau_extension_api.terminal`. Sections have typed children. Trees have visible preorder rows with checked directory parents. Diff blocks contain bounded unified diff text and display paths for the existing client diff renderer. Table rows must match the declared columns. List selection refers to an existing local item.

Actions reference package-owned, registered command IDs and schema-validated arguments. Write actions require an expected state revision. They do not contain shell command strings for the client to execute. Navigation state is separate from command execution. The host must check current activation, scope, read-only policy, and expected state again when the user invokes an action.

Current complete-result checks reject a changed binding, duplicate block or row identities, missing action targets, more than 1,024 blocks, depth above eight, and encoded output above 1 MiB. Individual fields also have limits. Display text rejects terminal controls and directional overrides; the Pydantic string validator rejects invalid Unicode. These are draft safety bounds, not measured display capacity claims.

The worker checks view and document declarations before it calls the presenter. It checks returned actions after the call. Presentation runs in the pure execution lane with a fixed data document; it cannot call live SDK services. The host must obtain the data snapshot before presentation. A test backend outside the repository returns all seven block types through an isolated worker. The current Kitty client does not yet consume them.

The extension computes content and layout choices. The terminal client computes display width, wrapping, and safe escape output. Use stored records for fallback when a worker is unavailable. Remote presentation is computed per data or view change and cached by revision; resizing must not require one RPC per visible row.

## Manifest contract

Use a data-only `extension.json`. Its versioned schema declares:

- Identity, package version, API version range, and description.
- Optional Python backend module and factory, loaded only by the SDK worker in the package's private environment. `BackendEnvironment` names a package-owned hashed requirements file and wheelhouse. The host selects the worker executable; the manifest does not supply a shell command.
- Schemas, source types, canonical event types, feed entry types, record collections, transforms, projectors, queries, commands, and terminal views.
- Web entry module, CSS and other assets, view IDs, target slots, titles, and ordering.
- Required and optional dependencies, provided and consumed service versions, and transform order constraints.
- Settings schema, defaults, supported scopes, secret references, and settings migration version.
- E2E scenario entries, supported harnesses and surfaces, and the quality policy release.

Settings and installed state do not live in the manifest. The manifest describes the package; the host stores the user's choices. Package identity is stable across settings changes.

Backend-free packages declare web views and schemas without a backend entry. The host serves those views without constructing an idle `ExtensionPlugin` worker.

Current implementation: `manifest.package.ExtensionManifest` and its typed submodels define these declarations. `validate_manifest()` checks schemas, defaults, namespaces, unique IDs, callback registrations, local service references, and declared view assets. It does not import a backend. The separate worker loader checks all 12 declared backend capabilities. Host discovery checks regular package files, declared asset hashes, E2E file presence, backend source paths, declared runtime file presence, API compatibility, and duplicate owners. The application captures matching package bytes into fixed host-owned storage. Private environment preparation and managed worker launch have real offline process tests. Normal daemon activation now uses the manager and checked lifecycle controls.

`require_compatible_api()` uses [packaging version specifiers](https://packaging.pypa.io/en/stable/specifiers.html). A draft host prerelease must be included explicitly by the package range. `activation_order()` checks required and optional peer versions, required service versions, and exclusive view conflicts. It uses the [standard topological sorter](https://docs.python.org/3.12/library/graphlib.html) with a ready queue ordered by extension ID. These functions prepare data; they do not activate workers. E2E paths and the quality policy version are declarations until the package runner verifies them.
