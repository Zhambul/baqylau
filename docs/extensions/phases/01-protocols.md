# P01 — Protocols and models

Status: in_progress

Owner: Codex

Depends on: None

Context: Harnesses and terminals use typed capabilities, but the event and display models have fixed types. External packages need a stable contract that does not expose private host modules.

Task: Define the extension protocols, package manifest, public models, scope rules, and process boundary. Test the key design choices before the full runtime is built.

Outcomes: Versioned contract drafts, schema fixtures, an explicit dependency map, and a small process and view prototype.

Verification: All tasks in this phase pass. The prototype can express data transforms, a diff, a file tree, and a thread view. Record design acceptance before the application refactor starts.

Evidence: The first draft SDK and its host re-exports are implemented in the working tree. See the work record below. P01 is not complete.

## Read first

Read [architecture](../architecture.md), [protocols](../protocols.md), and the current `harness/contract.py`, `harness/contracts/plugin.py`, `terminal/contract.py`, `domain/event_base.py`, and `tests/test_architecture_protocols.py`.

### P01-T01 — Fix ownership and the import boundary

Status: done

Owner: Codex

Depends on: None

Context: The current composition root builds harness and terminal services. External code must remain outside that import graph.

Task: Define the proposed `extensions/` host concern and public API package. List allowed imports for host services, SDK code, process adapters, and external feature code. Keep database construction in `app/` and database access in `repository/`. Identify the architecture checks that must accept the new concern.

Outcomes: A reviewed import map and exact package source locations. One authoritative public contract definition; `extensions/contract.py` contains explicit re-exports only.

Code areas: Proposed `extensions/contract.py`, `packages/extension-api/`; current `app/provider_harness_registry.py`, `app/provider_terminal.py`, `tests/architecture_packages.py`.

Verification: Trace a canonical transform call from an engine constructor through a protocol to a process adapter. Trace an external package import without the host source on its path. No step requires a feature import in the daemon.

Evidence: `packages/extension-api/src/baqylau_extension_api/` is independently installable. `extensions/contract.py` uses explicit aliases to the same protocol objects. `tests/extension_api/test_boundaries.py` checks SDK imports, host re-exports, JSON boundaries, and inclusion in the existing protocol scan. Isolated Python imports and a built-wheel check pass without host packages on the import path.

Remaining: Complete the canonical-transform application trace and final import-boundary review. Runtime providers and engine constructors now consume narrow host protocols. The P04 source path traces a real engine call through the public source protocol to a separate worker and atomic storage. This does not yet prove the canonical-transform path. See the [engine source record](04-events.md#work-record--engine-source-integration).

Status result (2026-09-25): Canonical-transform trace, from the engine to a separate worker: `engine/worker.py` calls `mixed_processing.read_mixed` with an `ExtensionProcessingBatch`, the protocol in `extensions/processing_contract.py`; `extensions/processing_batch.py` builds `InterpretationPipeline`, and `extensions/interpretation_canonical.py` calls `plugin.capabilities.canonical_transformer.transform(request)` through the public `ExtensionCanonicalTransformer` protocol. For an installed package, `extensions/impl/process/capabilities.py` gives that capability as the SDK's `RemoteCanonicalTransformer` (`runtime/proxies.py`), which sends `CANONICAL_TRANSFORM` over the worker channel; the worker's `runtime/dispatch.py` checks the pure context and calls the package's own class. Import review: `engine/` imports only extension contracts and host services (`processing_contract`, `manager_contract`, `job_scheduling_contract`, `pass_health`, `observer_pass`, interpretation models), never feature code or `extensions/impl/process`, which only the application wiring selects. The architecture tests (`tests/test_architecture_*.py`) and the SDK boundary tests (`tests/extension_api/test_boundaries.py`) enforce these rules, and external packages build and run with no host source on their path (the kit's `test_outside_checkout.py`, and the separate `baqylau-git` package).

### P01-T02 — Define core, extension, and scope models

Status: done

Owner: Codex

Depends on: P01-T01

Context: Current event envelopes require a harness, session, and actor. The Git page also needs repository events without a session. The JSON rules require declared documents.

Task: Define the core and extension envelope union, typed scopes, schema references, content references, runtime and history revisions, logical IDs, transform operations, and result models. Keep core session requirements strict. Define ownership and validation for every field. Specify how generated IDs and multiple output entries are ordered.

Outcomes: Immutable public models and schemas with examples for session, repository, and installation scope. A typed `EncodedDocument` codec boundary with no open dictionaries in engine contracts.

Code areas: Proposed SDK models and schemas; current `domain/event_base.py`, `domain/events.py`, `harness/models/raw_events.py`, `repository/model/facts.py`.

Verification: Validate valid and invalid examples for every scope. Check repeated output IDs, missing actor references, wrong schema owners, invalid JSON, schema reference escape, and unbounded content. Cases C07, C09, and C21 have executable model fixtures.

Evidence: The SDK has frozen, strict Pydantic models for four scopes, schema and content references, candidate extension facts, processing context, cursors, and tagged keep/drop/replace/insert operations. Tests reject incomplete session context, unknown fields, type coercion, invalid package versions, wrong event namespaces, repeated or self cause IDs, non-finite timestamps, and oversized inline documents. Raw transforms round-trip through typed JSON models.

Remaining: Define stored cause resolution and host history and runtime authority checks. Typed core derived changes and projection-transform checks now exist. Draft extension feed entries have explicit response order and stable scoped identities. Host commit positions, paging, and atomic storage remain pending. Raw content delivery and operation checks exist, with separate raw and canonical derived IDs. The full core union and mapper, opaque IDs, lexical repository path rules, canonical operation ownership, insertion keys, and batch scope checks also exist. Current checks cover parts of C06, C09, and C21; they do not complete these conformance cases end to end.

Status result (2026-09-25): The remaining items are closed by later work: stored cause existence and cycle checks and host commit positions with atomic storage (P04-T04), history revisions (P05-T04), and runtime authority through host call grants (P03-T03). C06 and C09 are complete with public evidence (P08-T03), and C21 is proven by the `baqylau-git` package (P10-T01, P10-T02).

### P01-T03 — Define protocol capabilities and typed adapters

Status: done

Owner: Codex

Depends on: P01-T02

Context: The user requires a `Protocol` abstraction like the harness and terminal boundaries. A dictionary of callbacks would not meet that requirement.

Task: Implement the draft `ExtensionPlugin`, `ExtensionCapabilities`, lifecycle, source, translator, transform, projection, observer, query, command, migration, and terminal protocols from [protocols](../protocols.md). Define narrow host service protocols. Add the typed factory entry. Require explicit protocol bases on concrete implementations.

Outcomes: Public signatures with complete request and result models. Small test implementations and process proxy skeletons satisfy the same protocols. Unused capabilities are absent rather than implemented as empty methods.

Code areas: Proposed SDK contracts and `extensions/impl/process/`; current protocol architecture checks.

Verification: Strict mypy accepts valid implementations and rejects incorrect signatures. Architecture checks reject an undeclared protocol implementation. Runtime message validation rejects a type-correct-looking object with invalid data. Test a backend-free manifest separately.

Evidence: `ExtensionPlugin`, `ExtensionCapabilities`, `ExtensionFactory`, `ExtensionLifecycle`, `ExtensionRawTransformer`, `ExtensionDirectory`, and `ExtensionHostServices` exist. The main protocol uses `extension_info` to meet the current Wemake name rule. `tests/extension_api/example.py` is a backend fixture with SDK imports only. It selects an optional raw transformer from active peer metadata. Tests cover lifecycle results, absent capabilities, frozen capabilities, typed drop operations, and active versus disabled peers. Strict mypy rejects an incorrect external transform return type. The architecture check rejects a matching method without its explicit protocol base.

Remaining: The only capability without a host consumer is `terminal`; it is connected in P07. The dashboard mounts extension views since P06-T02, and every other capability has an application consumer (sources and translator in P04, transforms in P04, projector, projection transformer, observer, queries, commands, and migrations in P05). All host services exist: directory, peer access, credentials, processes, inference, record reader, observation sink, and audit, with removal notices at activation and durable peer command acceptance (see the host services work record, P05-T05, P03-T03, P05-T03). All 12 backend capability protocols and process adapters now exist in draft form. The canonical transformer contract uses the full fact union. Add application consumers and the full service access checks.

Evidence added: Typed lifecycle, raw, and canonical proxies now call a separate worker through the same protocols as local implementations. The SDK-only fixture implements and tests all three across the process boundary. Factory peer lookup also crosses that boundary. `ExtensionHostServices.environment` supplies the host-selected identity. No engine constructor uses these proxies yet.

Evidence added: `ExtensionTerminalPresenter` and its local and remote adapters now use complete typed view models. A separate SDK-only backend supplies all seven display block types. Request checks cover owner, registered view, scope, data and settings schemas, and runtime revision. Result checks cover the exact binding, layout limits, local identities, action references, command declarations, and argument schemas. The Kitty client and command execution path are still pending.

Evidence added: `ExtensionQueries` and `ExtensionCommands` now have complete draft request, result, cancellation, and reconciliation models. Local adapters validate registered schemas and exact bindings. Remote proxies use the same protocols. An SDK-only backend outside the checkout verifies live reads, peer callbacks, a command, concurrent transforms, cancellation, and reconciliation. Strict mypy rejects incorrect transform, query, and command return types. Durable host jobs, browser acceptance routes, and recorded observation processing remain pending.

Evidence added: `ExtensionSources` and `ExtensionTranslator` have complete draft requests and results, explicit protocol implementations, and process proxies. Source replies bind observations to the selected source and prior position. Translation uses captured bytes and versioned decoder state. It returns explicit decisions and stable scoped fact IDs. Real file fixtures outside the checkout test partial lines, replacement, worker restart, replay after file removal, and forbidden live callbacks. Strict mypy rejects incorrect source and translator return types. P04 now connects source watches, deadlines, worker calls, atomic source storage, mixed decoding, and selected transforms through the engine. Core raw lifecycle protection is complete; full conformance remains open.

Evidence added: `ExtensionProjector` now has pure record selection and projection methods. Typed input separates canonical progress from projection commit cursors. Typed output supports several feed rows and checked record put or delete operations. A separate SDK-only backend owns record-key rules, row content, and write behavior. Process tests cover selection, output suppression, deletion, replay after worker restart, forbidden live callbacks, and stale runtime or record revisions. The host repository remains pending.

Evidence added: `ExtensionProjectionTransformer` now uses closed session, actor, core feed, extension feed, and record proposals. Its worker validates explicit operations, exact bindings, stable insertion IDs, source links, peer ownership, schemas, and protected core execution state. The host mappers preserve every current core derived field and all 25 feed body types. External worker tests change titles, actor names, feed rows, and records, and repeat the output after restart. Application storage and producer authority checks remain pending.

Evidence added: `ExtensionMigrations` now has typed settings and record requests, complete candidate results, explicit failure branches, and local and remote adapters. The manifest declares exact owned schema paths, including any permitted downgrade. Separate workers validate schemas before and after pure feature calls. Tests cover complete batches, all four scopes, stale candidate and source bindings, real message limits, restart, invalid output, and blocked live callbacks. Host candidate storage, credential filtering, and atomic activation remain pending.

Evidence added: `ExtensionObserver` now binds one committed trigger to an accepted job, with explicit effect policy, complete results, cancellation, and reconciliation. Separate workers test live callbacks, concurrent transforms, exact stops, replay rejection, and recovery without another execution. Results retain trigger cause links and obey schema and size limits. Host job acceptance, cursor transactions, read-only policy, stored cause and cycle checks, and durable outcome recovery remain pending.

Evidence added: `ExtensionServiceAccess` now resolves declared peer services and calls their public queries through typed callbacks. Caller identity comes from the worker connection. Host-held grants keep scope, runtime, original deadline, and active parent links outside feature payloads. Separate workers cooperate in both load orders and reject missing authority, pure calls, and service cycles. Tests also cover version changes, hidden queries, schemas, reply bindings, expiry, revocation, and bounds. Peer commands, the daemon catalog, removal notices, and atomic lifecycle coordination remain pending.

Status result (2026-09-25): Every capability has an application consumer: the terminal presenter is connected in P07 (`extensions/terminal_presentation.py`), and since P10-T02 a terminal view can also read its package's query. Service access checks are complete: declared peer services resolve through `HostCallLedger` grants, and HTTP queries open a root grant (P08-T03).

### P01-T04 — Define manifests and API compatibility

Status: done

Owner: Codex

Depends on: P01-T02, P01-T03

Context: Discovery must inspect packages without executing them. The private canonical schema version is currently separate from a public extension API.

Task: Define the manifest schema, schema registry, API range rules, capability registration, public service versions, E2E declarations, and settings scopes. Define a versioned public mapping for core events. Generate or export schemas from authoritative typed models. Keep API version, package version, and internal storage schema version distinct.

Outcomes: Manifest examples for backend-only, web-only, and full packages. Compatibility rules for missing capabilities, new optional fields, incompatible required fields, and unavailable event schemas. Canonical mapping fixtures cover the complete current vocabulary.

Code areas: Proposed SDK manifest and schema modules; current `domain/events.py`, `repository/mapper/canonical_codec.py`, `api/sessiondata/models/entry.py`.

Verification: Compare every core event's public encoding and decoded private value. Reject duplicate IDs, invalid assets, unmet API ranges, undeclared callbacks, and unknown required features. Read persisted extension data using stored schemas after package removal.

Evidence: `SchemaSet` validates JSON Schema 2020-12 definitions with `jsonschema` and `referencing`. It checks exact SHA-256 digests, rejects duplicate owner/name/version registrations, and permits references only within the registered schema set. Tests cover local fragments, registered peer schemas, nested reference escapes, stored schema reload, wrong document digests, invalid JSON, and non-finite JSON numbers. Reference-shaped default data is not treated as a schema. Package version strings use `packaging` and PEP 440.

Evidence added: Data-only manifests, API range checks, capability and service declarations, settings scopes, E2E declarations, and active-set ordering now exist. Backend-only, web-only, and combined manifest fixtures round-trip and validate against the exported manifest schema. Tests cover duplicate IDs, unsafe paths, undeclared callbacks, missing dependencies, incompatible versions, schema dependency escapes, cycles, and conflicting feed replacements.

Remaining: Compare installed files with their declarations. Verify processing selectors against the installed host and extension vocabulary. Connect compatibility checks to discovery and persisted schema records. Define the release compatibility matrix after the remaining host-service contracts. Do not mark this task done from data-only validation alone.

Evidence added: The separate worker loader checks identity and all 12 implemented capability groups against the manifest. It rejects invalid API, owner, and version declarations before feature import. Installed file and application compatibility checks are still pending.

Status result (2026-09-25): Installed files are compared with their declarations at discovery (asset hashes, E2E files, and the backend module; P03-T01), and incompatible API versions are reported there. Processing selectors are now checked against the vocabulary (`manifest/selections.py`): a fact selection must name a core event kind, an owned declared event type, or another package's namespace, and a present package must declare the type; `tests/extension_api/test_selection_vocabulary.py`. The check found two test manifests that selected feed entry kinds (`shell_started`, `session_title_changed`) as fact types, which matched no fact. Stored schemas serve data after package removal (P05 retained owners). The release compatibility matrix for the first version is in `docs/extensions/release.md`.

### P01-T05 — Test process transport and independent views

Status: in_progress

Owner: Codex

Depends on: P01-T03, P01-T04

Context: Process replacement and independent frontend builds are key design choices. Their cost and lifecycle behavior must be checked before implementing the full host.

Task: Test an existing JSON-RPC implementation over local streams. Fix framing, request correlation, logging, callback handling, message limits, and cancellation. Build a small external Svelte module with Vite library mode. Mount and dispose it through the proposed web interface. Exercise the terminal block draft with a diff, tree, and thread list.

Outcomes: A recorded library decision, a working small prototype, and any required contract corrections. No new custom RPC implementation or separate frontend framework.

Code areas: Proposed SDK prototype tests and process adapter; a temporary external package; current frontend build and terminal render primitives.

Verification: A worker can service host callbacks without deadlock. A slow command does not block message receipt. A changed bundle loads at a new digest URL. Dispose releases its effects. Record message overhead and view lifecycle results; do not set performance claims from a single run.

Evidence: The SDK worker prototype uses `jsonrpcpeer==0.2.0` and an inherited local socket. Real process tests copy the SDK-only backend outside the repository, run isolated Python, reject private host imports, and exercise factory callbacks, activation, raw and canonical transforms, and deactivation. Stream tests cover Unicode, stale revisions, unknown methods, pure-service guards, separate execution lanes, timeouts, cancellation, disconnects, invalid headers, truncated bodies, and message limits. Canceled waits remain in the execution limit until actual work ends.

Evidence added: `packages/extension-api-web/` provides the view protocol and a generic Shadow DOM loader. `tests/extension_web/fixture/` owns its module and tests. A fresh package outside the repository installs the SDK and shared tools from npm artifacts. Its unit test and Chromium and WebKit tests pass. They cover mount, settings update, CSS isolation, listener cleanup, and a changed digest URL without rebuilding the small test host. See the P02 work record for exact artifacts and commands. This is not integration with the main dashboard.

Evidence added: The terminal prototype models and real worker test now cover text, sections, tables, file trees, diffs, status, and thread lists. The SDK has 50 new terminal cases, including controls, Unicode, bounds, stale bindings, settings, schemas, and actions. A freshly installed SDK wheel passes the worker round trips.

Remaining: Connect the display contract to the client render primitives in P07 and verify actual widths and screens. A small-call transport measurement is now recorded below; representative package and load measurements remain for P08. Complete supervisor log limits, process termination, and durable job recovery in P03. These tests do not complete C01 or the other host conformance cases.

## Work record — 2026-09-14

Scope: First P01 implementation subset. Source is in the uncommitted working tree based on `6a9e497`. No application behavior, database schema, daemon configuration, or frontend source was changed. No daemon restart was performed.

Platform: macOS, Python 3.12.1. Draft SDK API and distribution version: `0.1.0a1`. There is no published shared quality policy release yet.

Changed areas:

- `packages/extension-api/`: standard Python package, `py.typed`, first contracts and models, schema validation, and draft limits.
- `extensions/contract.py`: public protocol aliases; no runtime implementation.
- `tests/extension_api/`: 69 focused tests and SDK-only backend fixture.
- `Makefile`, `mypy.ini`, `tests/architecture_test_tables.py`, and `vulture_extension_api.py`: include SDK source in current checks and keep new tests strict. Public dead-code roots are explicit; a test checks that they exist. Full stale-exemption checks remain P02 work.
- `requirements.txt`, `requirements-dev.txt`, and `.gitignore`: install the local SDK, add JSON Schema type stubs, and ignore package build output.

Checks:

| Command | Result |
| --- | --- |
| `make lint` | Passed: frontend ESLint and Knip; strict mypy on 1,982 source files; Vulture; Wemake; Ruff. |
| `make test-extension-api` | 69 passed; two existing pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,281 passed; 20 pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest tests/test_architecture_*.py tests/test_canonical_architecture.py tests/test_client_architecture.py -q` | 64 passed; two pytest plugin rewrite warnings. |
| `git diff --check` | Passed for tracked changes. |

Distribution check:

1. `.venv/bin/python -m pip wheel ./packages/extension-api --no-deps --wheel-dir /tmp/baqylau-extension-api.JMRb7j` passed.
2. Installed the wheel with `pip install --no-index --no-deps --target /tmp/baqylau-extension-api.JMRb7j/installed` and the exact wheel path.
3. Ran Python with `-I`, added only the wheel install directory, imported SDK contracts and codecs, and loaded `tests/extension_api/example.py` with `runpy.run_path`. Checked that the SDK module came from the wheel directory and that `app` and `domain` could not be found. Passed. Third-party dependencies came from the existing virtual environment; this was not a clean dependency-resolution test.
4. Artifact: `/tmp/baqylau-extension-api.JMRb7j/baqylau_extension_api-0.1.0a1-py3-none-any.whl`; SHA-256 `5e712caa641420be44ccc6f6eb6c2c2ef48de3cad7ac7ccd6a8bb9ffad84f414`.

Not run: Full browser E2E, live harness tests, real Kitty tests, or extension-runtime E2E. The first three surfaces have no behavior changes in this subset. Extension-runtime E2E cannot run before the runtime exists. The focused fixture proves SDK imports and typed calls, not C01, C06, C15, C26, or C27 end to end.

Next action: Complete P01-T02's public core mapping and identity rules. Use those types to finish P01-T03 and the manifest. Then execute P01-T05. Keep all phase and task statuses open until their complete verification criteria pass.

## Goal work record — core mapping and canonical operations

Scope: Continue the full “do all tasks” goal. This record adds to the first P01 subset; it does not complete P01 or narrow the goal. No extension runtime is active in the daemon.

Implemented:

- `baqylau_extension_api/core/` contains all 43 core payload types, nested content, account and model references, attention data, nonnegative token counts, and closed state values. The SDK keeps no private host imports.
- `CoreFact`, `ExtensionFact`, and `CommittedFact` separate the canonical branches and host acceptance metadata. The host mapper covers every current core field. Tests preserve source IDs, Unicode and path-like opaque IDs, actor and parent context, accepted cursors and times, and exact decimal costs.
- `ExtensionCanonicalTransformer` and its request/result models use the full canonical union. The canonical method names its argument `canonical_request`; this also keeps the current name-based protocol scan distinct from raw transforms.
- Canonical operation application supports keep, replace, drop, and insert, with atomic validation, stable ordering, scoped input anchors, protected origin fields, output bounds, and duplicate ID checks. This code is pure and does not write host storage.
- Public schema export and schema version 1 are separate from private storage schema version 19. Registry construction checks local reference targets, including pointers and named anchors. Recursive validation failure stays inside the contract error boundary.
- Repository paths have normalized absolute POSIX syntax. Filesystem and repository identity checks still belong to the future host runtime.

Verification from the current working tree:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/extension_api -q` | 259 passed; two pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,474 passed; 20 pytest plugin rewrite warnings. This tree also contains concurrent Codex control changes. |
| `make typecheck` | Passed on 2,020 source files. |
| Ruff and Wemake on SDK source, `extensions/`, and `tests/extension_api/` | Passed. |
| `.venv/bin/python -m ruff check .` | Passed on the checked tree. |
| `make deadcode` | Not passed: `public_committed` and `private_candidate` in the host mapper still need production callers. No unused host code exemption was added. |
| `.venv/bin/python -m flake8 . --config setup.cfg` | Found Wemake errors in other changed Codex control and test files. Those files were not edited for this task. |

The earlier wheel artifact describes only the first SDK subset. It is not release evidence for these new models. Browser, Kitty, worker transport, and extension-runtime E2E have not run.

Next action: Finish the source and derived-data contracts, then implement manifest validation and the worker/view prototypes. Connect the host mapper through the runtime processing path; the two dead-code findings must disappear through real callers, not through an exemption. Keep concurrent Codex control changes intact.

## Goal work record — raw processing and package declarations

Scope: Continue the full P01–P10 goal. The working tree remains based on `6a9e497`. No daemon, database, HTTP, frontend, or Kitty behavior changed. P01 remains in progress, and no runtime or release phase is complete.

Implemented:

- `models/content.py`, `models/raw_transforms.py`, and `processing/raw*.py` provide exact immutable raw content, digest and byte bounds, source ownership checks, atomic operation validation, stable ordering, and a complete next-stage snapshot. Original observations are not changed. Raw insertion IDs cannot collide with canonical insertion IDs with the same owner and output key.
- `manifest/` defines data-only package identity, backend factory, assets, schemas, settings, E2E, processing, operation, view, dependency, and service declarations. Validation checks cross-references before any backend import. The backend executable remains host-owned.
- `manifest.activation.activation_order()` checks a proposed active set. It uses `graphlib.TopologicalSorter` and a stable ready queue. Tests cover missing or incompatible required peers, absent or incompatible optional peers, service versions, schema dependencies, cycles, and exclusive feed replacements.
- Public structured core content now checks JSON syntax and size. A new test found that the library's fast `JsonValue` parse path did not enforce finite floats. The codec now validates the parsed value as well as the JSON text. Tests reject non-finite values under an otherwise unrestricted schema. Registry entry points also revalidate copied Pydantic models.
- SDK-only manifest fixtures cover backend-only, web-only, and combined packages. The manifest schema exports from the authoritative typed model. These fixtures are declaration tests, not installed feature packages or their E2E results.

Verification from the current working tree:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/extension_api -q` | 356 passed; two existing pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,571 passed; 20 pytest plugin rewrite warnings. This includes the concurrent Codex control worktree changes. |
| `make typecheck` | Passed on 2,053 source files. |
| Ruff and Wemake on SDK source, `extensions/`, SDK tests, and the public root list | Passed with the main repository rules. |
| `.venv/bin/python -m ruff check .` | Passed on the checked tree. |
| `make deadcode` | Still reports only the two host mapper functions without production callers: `public_committed` and `private_candidate`. No host exemption was added. |
| Full repository Wemake | Still reports findings in concurrent Codex control and test changes. Those files and the changed IDE project file were not edited for the extension work. |
| `git diff --check` | Passed. |

Current distribution check:

1. Built `/tmp/baqylau-extension-api.na0Ofi/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `4f3bb907dd45a8bf35300da644f72072d4364462646cf99ab165dcbf06f3a7c8`.
2. Installed that wheel with its dependencies into a new virtual environment under the same temporary directory. No host packages or system site packages were supplied. Main resolved dependencies: Pydantic 2.13.5, JSON Schema 4.26.0, referencing 0.37.0, and packaging 26.3. The repository test environment still uses Pydantic 2.13.4.
3. Ran the new environment's Python with `-I`. Confirmed the SDK came from the wheel environment and that `app` and `domain` could not be found. Checked all 43 public core types, core schema export and registration, manifest activation order, exact binary content, and the SDK-only backend fixture. Passed.
4. `pip check` in that environment passed. This is an isolated install and smoke check, not a second full test run against every allowed dependency version.

Not run: Worker transport E2E, independent web mount tests, real Kitty tests, extension settings UI, live harness tests, or the adapters and Git extension E2E suites. Their required runtime and feature code do not exist yet. E2E and quality policy fields in manifests are declarations; P02 and P08 must execute and enforce them.

Next action: Finish P01-T02 and P01-T03 source, translation, derived data, command, query, migration, terminal, and host service request/result contracts. Then execute P01-T05's worker and independent-view prototypes. Connect manifest checks and the core mapper through the real runtime before clearing the remaining host dead-code findings. Keep the full goal and all unfinished task statuses open.

## Goal work record — separate worker prototype

Scope: Continue the full P01–P10 goal. P01-T05 is now in progress. No engine, storage, HTTP, frontend, Kitty, or daemon behavior changed. The working tree remains uncommitted and includes separate Codex control changes that were not edited for this work.

Implemented:

- `runtime/channel.py`, `sender.py`, and `codec.py` use the existing typed [jsonrpcpeer 0.2.0 package](https://pypi.org/project/jsonrpcpeer/0.2.0/). The library owns framing, request IDs, reply dispatch, and JSON-RPC errors. The SDK adds strict public models, runtime revisions, complete-call deadlines, and prompt failure on disconnect. No private library methods or custom RPC parser were added.
- `RpcBridge` keeps protocol calls synchronous while another thread runs the stream reader. Host callbacks can complete while the original call waits. Pure work has one thread; live work has four. Each lane permits at most 16 queued or running calls. A canceled wait retains its slot until its actual work ends. The live service guard applies to each pure call.
- `WorkerBootstrap` and `runtime/worker.py` load one external backend on an inherited local socket. The process opens no network listener. The factory runs off the reader loop. Package API, identity, and supported capabilities are checked before import. Returned identity and handler declarations are checked before readiness. The supported set is lifecycle, raw transform, and canonical transform; other declared capabilities are rejected until implemented.
- `ExtensionHostServices.environment` supplies the host-selected package identity and runtime revision. The SDK-only sample factory uses this identity and reads peer metadata through a real host callback. Its feature module is copied outside the repository for process tests. Isolated Python cannot find `app` or `domain`, and the parent does not import that feature module.
- Tests exercise activation, raw and canonical suppression, deactivation, duplicate load rejection, stale inner and outer revisions, Unicode, unknown methods, separate execution lanes, timeouts, cancellation, disconnect, invalid and oversized frames, truncated bodies, and queue bounds. Log text that looks like an RPC header does not affect the separate RPC stream.

Checks after the final fixture correction:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/extension_api -q` | 382 passed; two existing pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,597 passed; 20 pytest plugin rewrite warnings. The test tree includes the separate Codex control changes. |
| `make typecheck` | Passed on 2,086 source files. |
| Ruff and Wemake on SDK source, `extensions/`, SDK tests, and public roots | Passed with the main repository rules. No rule relaxation was added for runtime code. |
| `.venv/bin/python -m ruff check .` | Passed. |
| `make deadcode` | Not passed: the two host mapper entry points and the three host-facing process proxy classes still have no production callers. No exemption was added for these findings. |
| Full repository Wemake | Six findings remain in the separate Codex control and test changes. No extension-code finding remains in the scoped check. |
| `git diff --check` | Passed. |

Current distribution check:

1. Built `/tmp/baqylau-extension-worker.Cy7r7w/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `d0602bb32a3b9e8c9bdb2a38abc9adef9506250e255c916faae29e8829daed2e`.
2. Installed it and its dependencies into a new virtual environment under the same directory. `pip check` passed. The new environment uses Pydantic 2.13.5 and jsonrpcpeer 0.2.0. The repository environment uses Pydantic 2.13.4.
3. Ran that environment's Python with `-I`. The worker module came from its wheel install, and private host packages could not be found.
4. Used `process_fixture.running_worker(directory, executable)` with the new environment's Python. Factory peer lookup, lifecycle, raw and canonical transforms, log separation, and clean shutdown passed through real process streams.
5. A separate smoke run made 100 sequential raw calls with one five-byte content object. Median round-trip time was 0.464 ms; maximum was 0.807 ms on this macOS/Python 3.12.1 host. This includes the synchronous bridge and typed validation, but excludes worker startup. It is a small local measurement, not a representative workload or performance guarantee.

Known limits: Worker shutdown cannot stop an already running Python thread. Request cancellation does not reverse external effects. P03 must supervise and terminate owned processes, drain logs with bounds, limit outstanding work at host entry points, and reconcile uncertain durable jobs. The current fixture uses small finite log output; it is not a production log collector. Header-line and body limits are tested; aggregate header traffic and idle peer policy still need host lifecycle limits. The SDK guard is for trusted extensions and is not an OS sandbox.

Not run or implemented: Independent web module builds and mount/dispose tests, terminal diff/tree/thread blocks, daemon activation, settings UI, persisted extension data, shared external quality runner, full host conformance, or the adapters and Git feature packages. P01 and all later phases remain open.

Next action: Complete the remaining public source, translation, derived-data, command, query, migration, terminal, and host-service contracts. Continue P01-T05 with the independent web and Kitty view prototypes. Then connect the tested process components to P03's real supervisor and P04's engine processing path. Remove dead-code findings through those production callers, not through exemptions. Keep the full goal active.

## Goal work record — terminal presentation contract

Date: 2026-09-14. Source: uncommitted worktree based on `6a9e497`. This record adds to the earlier worker prototype. It does not complete P01 or change the full P01–P10 goal.

Implemented:

- `contracts.presentation.ExtensionTerminalPresenter` joins the frozen capability group and host contract aliases. Local and process-backed implementations use the same `present(terminal_request)` method.
- `baqylau_extension_api.terminal` defines text, sections, tables, file trees, unified diffs, status, and selectable lists. Layout responses retain an exact binding to owner, view, runtime, settings, scope, and data cursor.
- The validators reject control text, invalid Unicode, malformed table and tree rows, duplicate IDs, stale bindings, missing actions, unknown command declarations, wrong scopes, invalid schemas, and write actions without an expected state revision. Depth, count, field, and encoded-response limits are explicit.
- The worker validates request registration before feature code runs. Presentation uses the pure lane and a fixed recorded document. Returned actions are data only; no command is executed by this prototype.
- `terminal_example.py` owns the feature layout with SDK imports only. The process fixture copies it outside the repository. Its worker returns all seven block types. Tests cover the full wire round trip and repeated equal output.
- The browser wire generator exports the same terminal models. Asset owner validation now matches the Python SDK syntax, and credential-bearing asset URLs are rejected.
- The dashboard build stamp now includes shared build-policy inputs and npm link configuration. Six tests check those dependencies. The new policy tests use strict mypy rules. The frontend was rebuilt, but the main daemon was not restarted.

Verification:

| Check | Result |
| --- | --- |
| `make test-extension-api` | 432 passed, including 50 terminal cases; two existing pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,653 passed; 20 pytest plugin rewrite warnings. |
| `make typecheck` | Passed on 2,108 files. |
| Scoped Ruff and Wemake for SDKs, host extension code, new tests, and build-input changes | Passed with the existing rules. |
| Full repository Ruff | Passed. |
| Full repository Wemake | Six existing findings in the separate Codex control and test changes. No changed extension or policy file has a remaining scoped finding. |
| `make deadcode` | Six findings remain: two private host mapper functions and four host-facing proxy classes need production callers. They were not exempted. Exact public terminal wire fields were added to the existing SDK entry list. |
| Browser SDK lint, types, format, generation drift, and unit coverage | Passed. Latest unit count: 28; statements 87.50%, branches 80.00%, functions 88.57%, lines 87.76%. Shared thresholds are unchanged. |
| `make build-frontend` | Passed. No dashboard route or feature component changed. |
| `git diff --check` | Passed. |

The first full Python run found a test error-text mismatch and a stale frontend build. The test now accepts the library's Unicode validation error. The build-input dependency check was extended, and the frontend was rebuilt. The next full Python run passed.

Installed Python artifact:

- Path: `/tmp/baqylau-extension-terminal.3xkMIL/baqylau_extension_api-0.1.0a1-py3-none-any.whl`.
- SHA-256: `1c3511791cb6d3f51563ee502c411a02a8d6858c6a6ca0b570cbf440e78325db`.
- A new virtual environment under the same directory installed the wheel and its dependencies. `pip check` passed. Pydantic 2.13.5 and jsonrpcpeer 0.2.0 were resolved.
- Isolated Python imported all 95 SDK submodules. Private host `app` and `domain` packages were absent. The terminal schema exported successfully.
- Real worker tests with that installed interpreter passed lifecycle, raw and canonical transforms, peer callbacks, terminal presentation, log separation, and clean exit.

Limits: The current daemon still has no extension manager, durable extension storage, or settings routes. Kitty has no block decoder, renderer integration, pane coordinator, or action dispatch yet. The terminal process checks are not C20 or real Kitty E2E. No live harness, real Kitty, adapters service, or Git feature test was claimed. The remaining capability and host-service contracts are still required.

Next action: Define the source, translation, derived-data, query, command, migration, and remaining host-service contracts. Complete the shared tooling and package template. Use the tested process and view components in the planned host runtime. Keep all open task statuses until their complete outcomes and checks exist.

## Goal work record — query and command contracts

Date: 2026-09-14. Scope: Continue the full P01–P10 goal, including the external adapters and Git packages. This work adds a draft SDK subset. It does not complete P01 or activate extensions in the daemon. The working tree is still based on `6a9e497`. No live daemon, user database, Git write, or external service was changed.

Implemented:

- `contracts/operations.py` adds explicit query and command protocols. `ExtensionCapabilities` and the host contract aliases expose them. Worker loading checks their declarations and method sets before reporting readiness.
- `models/operations.py`, `queries.py`, `commands.py`, `command_results.py`, and `observations.py` define exact call bindings, selected snapshots, page positions, accepted job attempts, cancellation, reconciliation, bounded outcomes, and proposed original observations.
- `operations/` checks registration, owner, scope, arguments, captured settings, continuation, reply identity, result schemas, receipt ownership, source declarations, and output bounds. Terminal presentation reuses the common settings check.
- `runtime/queries.py` and `runtime/commands.py` implement the same public protocols on each side of the process boundary. Reconciliation never calls execute through the proxy. The host still must provide durable job acceptance and recovery.
- Worker scheduling now has pure, live, and control lanes. Each lane has a limit of 16 running or queued calls. One pure thread preserves transform order. Four live threads run reads and jobs. One reserved control thread accepts cancellation when all live capacity is full. Canceling an RPC wait does not release capacity while its work still runs.
- `tests/extension_api/operation_example.py` is an SDK-only external feature fixture. Tests copy it outside the checkout and use isolated Python. Queries call the host directory while commands run. A raw transform still completes. A stale cancellation does not stop the active attempt. A valid stop acknowledgment is followed by a separate canceled result. Reconciliation checks in-memory proof without a second execution. Invalid inputs do not reach execution.
- The TypeScript SDK exports generated query and command wire types. Browser command-acceptance models and client methods are still pending; browsers cannot allocate host job IDs or claim runtime authority.

Verification from this working tree:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/extension_api -q` | 507 passed; two existing pytest plugin rewrite warnings. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,728 passed; 20 existing pytest plugin rewrite warnings. This includes the concurrent Codex worktree changes. |
| `make typecheck` | Passed on 2,135 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, SDK tests, browser schema exporter, and public root declarations | Passed with the main repository rules. |
| `make test-extension-web` and browser SDK lint | Passed. Generated types match Python. All 28 SDK unit tests pass. Shared coverage thresholds pass. |
| Independent npm artifact install | Formatting, types, ESLint, Knip, one unit case, and both Chromium and WebKit cases pass. See the P02 record. |
| `make deadcode` | Not passed: two host mapper functions and six remote capability classes still need production callers. No private-code exemption was added. |
| Full repository Wemake | Not passed: six findings remain in concurrent Codex control and test changes. Those files were not edited for this work. |
| `git diff --check` | Passed. |

The eight missing production callers are `public_committed`, `private_candidate`, `RemoteLifecycle`, `RemoteRawTransformer`, `RemoteCanonicalTransformer`, `RemoteTerminalPresenter`, `RemoteQueries`, and `RemoteCommands`. Public wire fields are listed explicitly in `vulture_extension_api.py`, and the existing root test checks each name against SDK source.

Distribution evidence:

- Built `/tmp/baqylau-extension-operations.FRxJm7/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `c88c735e209a107d10d13bc1a17f4899f01b6cb195328a263f8304c3e3e156ed`.
- Installed it with dependencies in a fresh `venv` under that directory. `pip check` passed. Python `-I` imported all 111 SDK modules and could not find `app` or `domain`.
- Reused the real process tests with the fresh wheel environment as the worker executable. Lifecycle, raw and canonical transforms, terminal presentation, queries, commands, cancellation, and reconciliation passed. This is an isolated wheel smoke check, not a second full dependency-matrix run.
- The wheel worker resolved Pydantic 2.13.5; the main test environment uses 2.13.4. These local artifacts are draft checks, not published releases. Later README edits do not change the tested source behavior.

Small-call measurement: macOS, Python 3.12.1, one isolated local worker using the editable SDK, one warm-up and 100 sequential calls for each method. The measurement includes the synchronous bridge, thread scheduling, encoding, worker dispatch, and response validation. Worker start plus load took 228.868 ms in this run. A query with a directory callback and a 413-byte request had median 1.668 ms and p95 1.846 ms. A one-input raw transform with a 1,028-byte request had median 0.449 ms and p95 0.496 ms. These are local observations, not latency targets or loaded-system results.

Next action: Finish source, translator, projection, observer, migration, and remaining host-service contracts. Then connect the tested proxies through application-owned discovery and supervision. Durable jobs must enforce read-only policy, expected state, request deduplication, cause resolution, atomic observation storage, and restart reconciliation. The current in-memory fixture does not satisfy those requirements. Keep P01, P02, P03, and P05 open until their full verification fields pass.

## Goal work record — sources and recorded translation

Date: 2026-09-14. Scope: Continue the full P01–P10 goal. This work adds source and translation contracts and their process adapters. It does not complete P01, narrow the adapters or Git package requirements, or activate an extension in the daemon. No user database, live daemon, harness source, or external service was changed.

Implemented:

- `contracts/sources.py` defines `ExtensionSources` and `ExtensionTranslator`. The main capability group and host aliases expose the same protocol objects. Worker loading checks their actual implementations against the manifest.
- `models/sources.py` and `source_results.py` define source plans, normalized watch paths, finite deadlines, owned source state, bounded reads, exact source bindings, positioned original observations, failed reads, and release results. `sources/` validates complete plans and progress before any proposed write.
- Source observations retain their selected source identity, type, and scope. A nonempty batch checkpoints its final observation. Empty reads can keep progress or make an explicit checkpoint. Immediate continuation requires advancement. A failed read has no new checkpoint.
- `models/translation_inputs.py` and `translation_results.py` define captured source bytes, schema metadata, cause links, decoder state revisions, explicit per-input verdicts, logical fact keys, and proposed next state. `translation/` validates complete decisions, registered schemas, scope, source references, stable identities, and output bounds.
- `translation_identity.py` derives version-one IDs from owner, scope, and logical fact key. Repeated observations can propose one logical fact. The complete response keeps every input decision; `translated_candidates()` retains the first proposed body for the next canonical stage. The host must still persist the complete audit and source links.
- `runtime/sources.py` runs describe, read, and release through the live lane. `runtime/translation.py` runs recorded translation through the pure lane. All methods use the public protocols, checked message models, exact reply bindings, and the selected runtime revision.
- `manifest/lookup.py` provides shared source and event declaration lookup. Command observation checks reuse the same source registration rules.
- `tests/extension_api/source_example.py` is an SDK-only journal feature. Process tests copy it outside the checkout. It reads bounded complete lines, holds partial lines behind the checkpoint, detects file replacement, and closes each file after a read. It does not retain a host repository or file handle.
- Tests cover source resume after a new worker starts, file replacement, oversized and shortened input, stale source calls, source release, duplicate logical facts, translation after source-file removal, and forbidden live SDK callbacks during translation. Static type tests reject incorrect source and translator return types. The protocol architecture scan explicitly requires all current extension protocols.

Verification from this working tree:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/extension_api -q` | 595 passed; two existing pytest plugin rewrite warnings. This adds 88 cases to the previous 507-case SDK run. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,816 passed; 20 existing pytest plugin rewrite warnings. The earlier run had 1,814 cases before the two new negative type cases. |
| `make typecheck` | Passed on 2,167 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, host extension source, SDK tests, and public root declarations | Passed with the main repository rules. |
| `make test-extension-web` | Passed. Generated browser declarations still match their Python inputs. All 28 browser SDK unit cases and shared coverage thresholds pass. No browser implementation changed in this step. |
| `make deadcode` | Not passed: 11 components still need production callers. No new private-code exemption was added. |
| Full repository Wemake | Not passed: the same six findings remain in concurrent Codex control and test changes. Those files were not edited for this work. |
| `git diff --check` | Passed. |

The 11 missing production callers are the two host mapper functions, eight remote capability classes, and `translation.results.translated_candidates`. The new remote classes are `RemoteSources` and `RemoteTranslator`. Public wire deadlines and verdict fields are listed explicitly in `vulture_extension_api.py`; the root test checks that each declared public name exists.

Distribution evidence:

- Built `/tmp/baqylau-extension-sources.RNP0uo/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `d705bb96403ee4c154156e246db7e87329754ceca3813a2ee5b831758696cb35`.
- A fresh virtual environment under that directory installed the wheel and dependencies. `pip check` passed. Python `-I` imported all 130 SDK modules, with no private `app` or `domain` package available.
- Ten real process smoke checks passed with that installed interpreter. They cover all current capabilities, source restart and replacement, partial lines, recorded translation after file removal, live-service rejection from the pure lane, and the earlier command and view protocols.
- The isolated environment resolved Pydantic 2.13.5; the main test environment uses 2.13.4. This is a local wheel smoke check, not a complete dependency-matrix or published-release result. Later README changes clarify the tested behavior without changing the wheel's source code.

Limits: The journal source is a protocol fixture, not a product file decoder. Its byte-offset and file-generation rules do not define every future source format. Its repository scope does not prove that a real Git repository exists. Tests capture observations in memory and pass the saved position to another worker; they do not prove a host checkpoint transaction or daemon restart. No source watch, deadline scheduler, raw storage conversion, decoder-state repository, canonical dispatcher, or source health manager is active in the application. Source and translation output must still pass those host checks before production acceptance.

Next action: Finish projector and projection-transform models, observer and migration protocols, and the remaining host services. Then finish the shared quality runner and connect these tested capabilities through application-owned discovery, supervision, and storage. Preserve the existing harness translators until the distinct extension-origin branch is integrated. Keep every incomplete phase open; P09 adapters and P10 Git remain part of the full goal.

## Goal work record — owned records and projection

Date: 2026-09-14. Scope: Continue the full P01–P10 goal and provide the requested overall status report. This work adds the projector contract and checks. It does not complete a phase or change the live daemon, user database, existing session writers, adapters CLI, or Git repository state.

Implemented:

- `contracts/projection.py` defines `ExtensionProjector.select_records()` and `project()`. The main capability group and `extensions/contract.py` use the same public protocol. Both methods have negative static-type tests and take part in the existing explicit-protocol architecture scan.
- `models/records.py` defines owned collection keys, explicit absent rows, stored rows, and deletion markers. Stored rows carry safe fallback text. `models/record_changes.py` defines put and delete proposals with exact expected revisions and no worker-assigned commit revision.
- `models/projections.py` separates canonical input progress from the derived-data snapshot cursor. Requests contain committed facts and captured rows. Results contain ordered extension feed entries, record changes, and diagnostics. `models/projection_entries.py` keeps feed rows separate from canonical facts.
- `projection/` checks selected input types and scopes, schema declarations, owned records, ordered source cursors, captured read coverage, exact output bindings, expected revisions, source links, duplicate keys, and whole-request and whole-result size limits.
- Record-key selection is feature code. The host does not need a feature-specific record-key function. `capture_projection_request()` requires every selected row once and in order, including explicit proof of absence. The host must still obtain a real consistent snapshot and check it before commit.
- `projection_identity.py` derives stable feed IDs from owner, scope, source fact, and local row key. A fixed encoding fixture checks the V1 algorithm. Runtime, history, settings, projection generation, and display content do not enter this identity.
- Manifests now declare `entry_types` independently of canonical `event_types`. Shared declaration lookup and schema checks cover owned entry and record types. The existing JSON boundary test permits one typed record lookup index; it does not permit open payload dictionaries.
- `runtime/projection.py` runs both methods through the pure worker lane. The loader checks the explicit protocol and matching manifest capability. The external fixture owns its key selection, feed rows, and record writes in one copied backend file.
- Process tests verify selection, two feed rows from one fact, suppression, record deletion, replay after worker restart, invalid expected revisions, stale runtime replies, empty output, and rejected live host calls. Model tests also cover create, replace, restore, wrong schemas, unsafe text, strict revisions, and size bounds.

Verification from this working tree:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/extension_api` | 699 passed; two existing pytest plugin rewrite warnings. This adds 102 projection cases and two negative type cases to the previous 595-case run. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 1,920 passed; 20 existing pytest plugin rewrite warnings. |
| `make typecheck` | Passed on 2,195 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, host extension source, and SDK tests | Passed with the main repository rules. No design-rule thresholds were changed. |
| `make test-extension-web` | Passed. Generated declarations match their Python inputs; all 28 web SDK unit cases and shared coverage thresholds pass. No web runtime implementation changed in this step. |
| `make deadcode` | Not passed: 14 components still need production callers. No private-code exemption was added. |
| Full repository Wemake | Not passed: the same six findings remain in concurrent Codex control and test files. This work did not edit those files. |
| `git diff --check` | Passed. |

The 14 missing production callers are the two host mapper functions, nine remote capability classes, `translated_candidates`, `capture_projection_request`, and `projected_entry_id`. These are explicit remaining host integration tasks, not completed application paths.

Distribution evidence:

- Built `/tmp/baqylau-extension-projection.rV1M64/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `7cf0000925a41df6cca17fa40a8b959667e5a6a46eed988c9c6409b28ef82e54`.
- A fresh virtual environment under that directory installed the wheel and dependencies. `pip check` passed. Python `-I` imported all 144 SDK modules without finding private `app` or `domain` packages.
- Eighteen real worker smoke checks passed using that installed interpreter. They cover all nine supported capabilities, command cancellation under load, source restart and replacement, recorded translation, pure projection, and both pure-call failure checks.
- The isolated worker uses Pydantic 2.13.5; the main tests use 2.13.4. This is a local install and worker smoke check, not a complete dependency matrix or a published release.

Overall status reported to the user: P01 and P02 are in progress; no phase or subtask is complete. P03–P10 remain required. Independent Python and web feature ownership is demonstrated by prototypes, but the daemon does not load extensions, the web dashboard has no extension management page, and Kitty does not render extension blocks. Shared Python tooling, durable storage, jobs, migrations, rebuilds, the test kit, adapters, and Git remain open.

Next action: Define typed core session, actor, and feed changes for `ExtensionProjectionTransformer`; keep the current closed models instead of introducing an open core payload. Then finish observer and migration contracts and the remaining host services. Complete the shared quality runner, discovery, supervision, atomic storage, and frontend integration. Retain the full P09 adapters and P10 Git scope. Do not treat these pure validation tests as evidence of a host transaction or product E2E.

## Goal work record — core derived data and projection transforms

Date: 2026-09-14. Scope: Continue the full P01–P10 goal. This step implements the next protocol subset and updates the overall status. It does not complete a phase. The live daemon, user database, existing writers, dashboard routes, and Kitty renderer are unchanged.

Implemented:

- `core/actor_state.py`, `session_state.py`, and `aggregate.py` preserve the complete current session and actor state, including nested usage, work references, and internal calculation inputs. `core/entries.py` and the entry body modules cover all 25 current feed body types. Public models remain closed and typed.
- `extensions/mapper/core_aggregates.py`, `core_entries.py`, and `core_entry_bodies.py` map the private rows without loss. Tests compare every private field and enum, the complete body registry, exact decimal values, full encoded fixtures, and exported schemas. Host commit cursors are not writable through the public entry model.
- `contracts/projection.py` adds `ExtensionProjectionTransformer`. The main capability group, host aliases, explicit-protocol scan, negative static-type case, loader, and worker registration use the same protocol. Ten capability names now have worker support.
- `models/projection_changes.py` defines tagged core session, actor, core feed, extension feed, and extension record proposals. `models/projection_transforms.py` binds the captured core state and ordered proposals to canonical input progress and the derived-data snapshot.
- `projection_transform/` validates the complete request and explicit keep, replace, drop, and insert operations. Checks cover exact bindings, scopes, source links, duplicate write targets, captured record revisions, peer ownership and schemas, stable insertion IDs, combined actor cycles, and whole-message bounds. The existing ordering helper also enforces the combined kept-and-inserted output limit.
- Display changes cannot create or remove core execution state. Tests reject lost active shells, background counts, compaction state, internal calculation inputs, source metadata, and lifecycle transitions. Valid title, name, count, and feed changes remain supported. A new core actor requires canonical processing.
- An extension can change or suppress peer proposals without taking their ownership. Inserted extension rows belong to the current package. Dropping a record proposal suppresses the write; deletion remains explicit. The host still must check each producer's declarations before composing peer proposals.
- `runtime/projection_transforms.py` provides local and remote adapters in the pure lane. `tests/extension_api/projection_transform_example.py` owns the external feature behavior. Process tests copy that file outside the checkout, change core display fields and extension records, replace a core feed row with an owned row, replay after restart, and reject stale runtimes and live SDK callbacks.
- `vulture_extension_api.py` names the public projection schema version constant. No mapper or remote runtime class was exempted from the missing-caller check. No lint or design threshold was changed.

Verification from this working tree, based on `6a9e497`:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/extension_api` | 864 passed; two existing pytest plugin rewrite warnings. This adds 165 cases to the previous 699-case run. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 2,085 passed; 20 existing pytest plugin rewrite warnings. This tree also contains unrelated Codex control changes. |
| `make typecheck` | Passed on 2,240 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, host extension source, SDK tests, and public root declarations | Passed with the main repository rules. |
| `make test-extension-web` | Passed. Generated wire declarations, formatting, TypeScript checks, 28 unit tests, and shared coverage thresholds pass. No web runtime code changed in this step. |
| `make deadcode` | Not passed: 20 components still need production callers. |
| Full repository Wemake | Not passed: six findings remain in unrelated Codex control and test changes. Those files were not edited for this work. |
| `git diff --check` | Passed. |

The 20 missing production callers are eight host mapper functions, ten remote capability classes, `capture_projection_request`, and `translated_candidates`. They must gain real runtime and storage callers, not private-code exemptions.

Distribution evidence:

- Built `/tmp/baqylau-extension-core-projection.fuu1Jk/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `b474152a98e83835e9fe65c1007288c762b4385085d17f973ee91c0126eda183`.
- A new virtual environment under that directory installed the wheel and dependencies. `pip check` passed. Python `-I` imported all 170 SDK modules from the installed wheel without finding private `app` or `domain` packages.
- Twenty-five real worker tests passed using that installed interpreter. These are the seven `test_*process.py` modules for base workers, terminal views, operations, sources, translation, projection, and projection transforms. The parent test runner uses the host test environment; only workers use the fresh wheel environment. The fixture's `running_worker` executable was selected in memory for this check.
- The isolated worker uses Pydantic 2.13.5; the main test environment uses 2.13.4. This is an independent install and worker check, not a complete dependency matrix or published release.

Limits: No application component invokes the new projection-transform path. The current `SessionDataChanges` model still permits one core feed row per event. Tests prove pure validation and worker behavior, not producer authority, a real snapshot read, an atomic repository commit, audit retention, or durable replay. No live harness, real Kitty, dashboard management, adapters, or Git feature E2E ran. P03–P10 remain open, and no phase or subtask is complete.

Next action: Finish the observer, migration, and remaining host-service contracts. Complete P02's shared Python quality runner and external package template. Then use the tested protocols in application-owned discovery, supervision, settings, atomic processing, and UI integration. The priority is to obtain real application callers and a usable extension path. Keep the adapters and Git packages and their package-owned E2E suites in scope.

## Goal work record — settings and record migrations

Date: 2026-09-15. Scope: Continue the full P01–P10 goal. This step adds the migration protocol and its worker checks. It does not complete a phase. The working tree is based on `6a9e497`. No live daemon, user database, dashboard route, Kitty renderer, external service, or Git state was changed.

Implemented:

- `contracts/migrations.py` defines `ExtensionMigrations` with separate settings and record methods. The capability group, public host aliases, explicit-protocol scan, negative type tests, loader, and worker registration use the same protocol. The worker now supports 11 capability names.
- `manifest/migrations.py` and `migration_rules.py` declare exact source and target schema paths. Both schemas belong to the package and must be registered. Each target is the current settings or collection schema. A downgrade requires its own declared path. The host does not infer reverse or intermediate conversions.
- `models/migrations.py` and `migration_results.py` define immutable candidate bindings, captured source revisions, complete ready results, and typed failures without partial output. Each reply retains the exact owner, scope, runtime revision, candidate, call, and source revision or snapshot.
- `migrations/` validates the input and complete output. A record batch preserves every key, input order, and expected revision. It can change stored values and fallback text, but cannot add, drop, or move rows. Requests have an 8 MiB bound, replies have a 4 MiB bound, and batches have a 1,000-row bound.
- `runtime/migrations.py` supplies local and remote adapters. The worker validates old documents before the feature call and new documents before reply. Both methods use the pure lane, which rejects live SDK callbacks. A worker process is not an OS security boundary.
- `tests/extension_api/migration_example.py` is a feature fixture with SDK imports only. Tests copy it outside the checkout. It owns forward and reverse value conversion. Real worker cases cover restart, complete conversion, explicit failure, invalid output, stale runtime input, and blocked live calls for both methods.
- Tests also cover all four scopes, missing or duplicate paths, invalid source data, changed bindings, partial or reordered output, and actual encoded messages above the input and output bounds. Two static-type cases reject incorrect migration return types.

Verification from this working tree:

| Check | Result |
| --- | --- |
| `make test-extension-api` | 927 passed; two existing pytest plugin rewrite warnings. This adds 63 cases to the previous 864-case SDK suite. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 2,227 passed; 20 existing pytest plugin rewrite warnings. The tree includes unrelated Codex control changes. |
| `make typecheck` | Passed on 2,295 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, host extension source, SDK tests, and public root declarations | Passed with the main repository rules. No design limit was changed. |
| `make test-extension-web` | Passed: generated declarations, formatting, types, 28 unit tests, and shared coverage limits. No browser implementation changed in this step. |
| `make deadcode` | Not passed: 21 components still need production callers. `RemoteMigrations` is the new finding. No private-code exemption was added. |
| Full repository Wemake | Not passed: six findings remain in unrelated Codex control and test files. This work did not edit those files. |
| Plan record check | Passed: 10 phases and 53 unique subtasks retain their required fields. Two phases are in progress; eight are not started. Ten subtasks are in progress, one is done, and 42 are not started. |
| `git diff --check` | Passed. |

Distribution evidence:

- Built `/tmp/baqylau-extension-migrations.SEsdvf/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `64195a1f10a908dabe7e2032bc9a264abd4e271f70baf6d1b5a1c05238d07159`.
- A fresh virtual environment under that directory installed the wheel and its dependencies. `pip check` passed. Python `-I` imported all 180 SDK modules without finding private `app` or `domain` packages.
- All 33 worker checks passed with that environment as the worker executable. The selection was `tests/extension_api/test_*process.py` and the failed-settings case in `test_migration_failures.py`. The parent test runner used the main test environment. Results are in `/tmp/baqylau-extension-migrations.SEsdvf/worker-results.xml`.
- The fresh worker uses Pydantic 2.13.5; the main test environment uses 2.13.4. This is a local install and worker check, not a full dependency matrix or published release. A later docstring correction states that the request has no credential service field; it does not change behavior.

Limits: Candidate IDs and source revisions are supplied inputs. The SDK does not prove that a real inactive candidate or current source snapshot exists. The host must capture data, keep secret values out of settings documents, retain old schemas and data, accept all batches, reject stale state, and switch active heads atomically. No application storage path invokes these methods. SDK tests do not complete P05-T05, C04, or migration recovery E2E.

Overall status: P01 and P02 remain in progress. P02-T02 is the only completed subtask; no phase is complete. P03–P10 remain required. The daemon has no extension manager or extension settings page, and Kitty has no extension renderer. The adapters and Git packages and their own E2E suites remain in scope.

Next action: Finish the observer and remaining host-service contracts. Complete the remaining shared package checks and template. Connect the tested protocols to application-owned discovery, supervision, durable storage, settings, and display clients. Do not replace real integration work with more public dead-code exemptions.

## Goal work record — post-commit observer protocol

Date: 2026-09-15. Scope: Continue the full P01–P10 goal after the completed migration protocol step. This work adds the remaining backend capability protocol. It does not complete P01 or activate extensions in the application. The tree is based on `6a9e497`; no live daemon, user database, external service, or Git write was changed.

Implemented:

- `contracts/observers.py` defines `ExtensionObserver.observe()`, `cancel_observation()`, and `reconcile_observation()`. The capability group, public host aliases, explicit-protocol scan, negative type tests, loader, and worker registration use the same protocol. All 12 manifest backend capability names now have draft protocols and adapters.
- `manifest/observers.py` adds an explicitly tagged observer selection with input types, scopes, required read/write classification, and declared recovery support. `Contributions.processing` uses a typed union. Pure selections cannot receive observer effect fields. Manifest round-trip and rejection tests cover missing, duplicate, invalid, and undeclared selections.
- `models/observer_jobs.py` binds one accepted job attempt to owner, scope, runtime, history, and one committed trigger. It supplies captured settings, a finite positive deadline, and an expected state revision for write-class work. Replay is not a valid mode. The request does not prove real host acceptance, current state, or a current deadline.
- `models/observer_results.py` separates completed, failed, canceled, and uncertain outcomes. Output observations retain the trigger as a cause. An uncertain outcome can have an owned receipt. A stop acknowledgment is not a final durable job result.
- `observers/` validates registrations, the exact trigger, settings and source schemas, write preconditions, recovery declarations and receipts, complete reply bindings, source and content identities, and output limits. Tests cover all four scopes and both core and extension triggers. Actual large-message cases enforce the 8 MiB request and 4 MiB reply limits. Existing common limits bound observation and content-reference counts.
- `runtime/observers.py` supplies local and remote adapters. Observe and recovery use the live lane; cancellation uses the reserved control lane. The proxy does not retry observe or convert recovery into execution. Feature code remains responsible for checking external proof; this is not an OS security boundary.
- `tests/extension_api/observer_example.py` owns an SDK-only feature with an observer, count query, raw transform, and audit-only source decoder. Tests copy the package file outside the checkout. A live directory callback works during observe. A slow observer allows queries, transforms, and an exact stop request to complete.
- Process tests reject replay, stale runtime input, and invalid source documents before execution. They verify cause-linked output, stale cancellation rejection, proof lookup without another execution, and an explicit uncertain result after worker restart removes in-memory proof. This fixture is not durable job storage.

Verification from this working tree:

| Check | Result |
| --- | --- |
| `make test-extension-api` | 994 passed; two existing pytest plugin rewrite warnings. This adds 67 cases to the previous 927-case SDK suite, including three negative type cases. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 2,294 passed; 20 existing pytest plugin rewrite warnings. The tree includes unrelated Codex control changes. |
| `make typecheck` | Passed on 2,314 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, host extension source, and SDK tests | Passed with the shared main-repository rules. No design limit or exemption was changed. |
| `make test-extension-web` | Passed: generated declarations, formatting, types, all 28 unit cases, and shared coverage limits. No browser implementation changed. |
| `make deadcode` | Not passed: 22 components still need application callers. `RemoteObserver` is the new finding. No private-code exemption was added. |
| `make wemake` | Not passed: the same six findings remain in unrelated Codex control and test files. Those files were not edited for this work. |
| Main and fresh-worker `pip check` | Both passed. |
| Plan record check | Passed: 10 phases and 53 unique subtasks retain their required fields. Two phases are in progress and eight are not started. Ten subtasks are in progress, one is done, and 42 are not started. |
| `git diff --check` | Passed. |

Distribution evidence:

- Built `/tmp/baqylau-extension-observers.cKtnkD/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `1fcd4f40a530b4d9e6dfdb144a634b9edc41517777a47360eebdca20b4619747`.
- A fresh virtual environment under that directory installed the wheel and dependencies. Isolated Python imported all 189 SDK modules without private `app` or `domain` packages. The 12 `ExtensionCapabilities` type annotations exactly match the worker's supported capability names.
- All 40 worker checks passed using that environment as the worker executable. The selection was `tests/extension_api/test_*process.py` and the failed-settings case in `test_migration_failures.py`. The parent test runner used the main environment. Results are in `/tmp/baqylau-extension-observers.cKtnkD/worker-results.xml`.
- The fresh worker resolved Pydantic 2.13.5; the main environment uses 2.13.4. This is a local install and process check, not a full dependency matrix or published release.

Limits: The application still has no observer consumer cursor, job acceptance transaction, durable executor, origin or read-only policy adapter, generated-observation cycle check, or restart recovery service. It must verify actual trigger history and authority, capture current state, commit job insertion with cursor advancement, and commit result observations with the final stored outcome. The SDK's retained direct cause link does not prove a bounded stored cause chain. No C11, C12, C22, C23, or C24 host conformance case is complete from these prototype tests alone.

Overall status: P01 and P02 remain in progress, with P02-T02 the only completed subtask. No phase is complete. The settings page, runtime management, event and record storage, Kitty integration, full extension test kit, adapters package, and Git package remain required. No live harness, real Kitty, external adapters service, or Git product E2E ran in this step.

Next action: Implement the remaining narrow host-service models, protocols, and typed callbacks. Finish the shared package checks and template. Then connect these tested components to application-owned discovery, supervision, storage, settings, and display clients. Keep package-owned adapters and Git E2E in the full goal, and remove missing-caller findings through real application integration.

## Goal work record — declared peer reads and call authority

Date: 2026-09-15. Scope: Continue the full P01–P10 goal and report overall status. This step adds the read subset of the peer service protocol. It does not complete P01 or provide a working application extension system. The worktree remains based on `6a9e497`. No live daemon, user database, external service, or Git write was changed.

Implemented:

- `contracts/service_access.py` defines `ExtensionServiceAccess.resolve_service()` and `query_service()`. The frozen host-service group supplies it only for declared consumers. Public host aliases and negative type checks use the same protocol definition.
- `models/services.py` separates service selection, exact package/service/runtime versions, public query metadata, query calls, and typed unavailable results. Requests cannot contain peer settings, caller identity, or a claimed call route. Results retain the exact service binding. Query output does not disclose peer settings values.
- `runtime/service_selection.py`, `service_requests.py`, `service_results.py`, and `service_dispatch.py` check declared consumption before provider lookup, compatible versions, public query exposure, scope, captured host settings, argument schemas, output schemas, reply bindings, and encoded limits. The host binds the caller to its worker load and callback connection. `ServiceProviderLookup` is a typed host boundary, not a feature import.
- `runtime/call_grants.py` keeps authority in host memory. A root fixes caller environment, scope, and a monotonic deadline. Child grants retain scope and deadline and require all parents to remain active. Released, expired, revoked, fabricated, and wrong-caller grants fail. Repeated owners, more than 16 call levels, and more than 1,024 active grants fail. These limits are draft safety bounds, not measured performance results.
- `runtime/host_context.py` carries an opaque call reference across RPC, callbacks, and executor threads. The wire ID does not grant permission. Replies must retain the same reference. Host transport waits use the remaining original deadline. Pure processing continues to reject live service calls.
- `runtime/service_access.py` and `worker_services.py` supply typed callbacks and a remote service proxy. Resolution can run during factory preparation without query authority. Execution requires a current host grant. Peer command execution is not exposed; it still needs the durable job acceptance path in P05.
- `tests/extension_api/peer_example.py` owns feature behavior with SDK imports only. Two copies run in separate external folders and processes. They cooperate in both load orders without importing each other. Tests cover an inactive peer, absent authority, and nested service-call cycles. The cycle fixture tests the call guard independently of dependency activation; it is not a valid active package graph.
- The pure-call test now uses the correct extension identity and checks the exact guard message returned by feature code. An earlier identity rejection can no longer count as proof that the pure guard ran. Other tests cover context isolation, changed reply IDs, real deadline expiry, released parents, stale service versions, hidden queries, invalid schemas, and forbidden host-owned fields.

Verification from this worktree:

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest tests/extension_api -q` | 1,043 passed; two existing pytest plugin rewrite warnings. This adds 49 cases to the prior 994-case SDK suite. |
| `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` | 2,343 passed; 20 existing pytest plugin rewrite warnings. The tree includes unrelated Codex control changes. |
| `make typecheck` | Passed on 2,340 source files. |
| Root Ruff | Passed. |
| Wemake on SDK source, host extension source, SDK tests, and public root declarations | Passed with the shared main-repository rules. No rule strength or design limit changed. |
| `make policy-check` | Passed with policy digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72` and all required Python tool pins. |
| `make test-extension-web` | Passed: generated declarations, formatting, types, 28 unit cases, and shared coverage limits. No browser implementation changed. |
| `make deadcode` | Not passed: 25 components need application callers. The new findings are `HostServiceAccess`, `register_service_access`, and `revoke_runtime`. Two public data fields were added to the checked SDK root list. No private-code exemption was added. |
| `.venv/bin/python -m flake8 . --config setup.cfg` | Not passed: six findings remain in unrelated Codex control and test files. This work did not edit those files. |
| Main and fresh-worker `pip check` | Both passed. |
| Plan record check | Passed: required fields, unique IDs, status values, and local link files. Ten phases and 53 subtasks remain. Two phases are in progress; eight are not started. Ten subtasks are in progress, one is done, and 42 are not started. |
| `git diff --check` | Passed. |

Distribution evidence:

- Final wheel: `/tmp/baqylau-extension-services.BU5zt1/release/baqylau_extension_api-0.1.0a1-py3-none-any.whl`. SHA-256: `d6e4f3bb5acd696380a0cd4f5759efe7e242fbcbb07f47f93baeaa7fa37f5f7e`.
- A fresh virtual environment under that directory installed the SDK and its declared dependencies. Isolated Python imported all 201 SDK modules without private `app` or `domain` packages. The 12 `ExtensionCapabilities` annotations match the worker's supported capability names.
- All 46 worker checks passed. The selection is every `tests/extension_api/test_*process.py` module plus `test_migration_failures.py::test_failed_settings_have_no_partial_value`. Workers use the fresh interpreter; the parent test runner uses the main environment. The final result is recorded in `release/worker-results.xml` under the artifact directory.
- The fresh worker uses Pydantic 2.13.5; the main environment uses 2.13.4. This is a local install and process check, not a full dependency matrix or published release.

Limits: The daemon still has no package discovery service, active provider catalog, extension manager, accepted root-call path, or lifecycle revocation caller. P03 must coordinate provider snapshots with activation and prevent new calls to stopped runtimes. In-memory call authority does not prove durable job acceptance, current repository state, read-only policy, or recorded observation causes. Peer command acceptance, job status/cancellation, and lifecycle notifications remain required. No full C15–C17 host conformance case is complete from these tests.

Overall status: P01 and P02 remain in progress. P02-T02 is the only complete subtask, and no phase is complete. The settings page, web and Kitty application hosts, storage integration, complete extension test kit, adapters package, and Git package remain open. No live harness, real Kitty, external adapters service, or Git product E2E ran.

Next action: Finish the remaining host-service contracts and external quality/template work, then connect the tested protocols to application-owned discovery and supervision. Keep the full event, storage, settings, frontend, adapters, and Git work in scope. A tested SDK protocol is not a substitute for a working host path.

## Work record — process, inference, and record host services

Date: 2026-09-24

Owner: Claude Code

Status: done

Context: The Git and adapters packages (P09, P10) need to run programs, call the user's models, and read their own records.

Decisions:
1. A manifest declares `contributions.processes`: a name, one bare executable name, and a maximum run time of at most 600 seconds. `ExtensionProcessService.run_process` takes a declared name, an argument array, a working directory, and an optional shorter deadline. The host finds the executable on `PATH`, never runs a shell, runs it in its own process group through the existing bounded runner, and returns the exit code and output, or `timed_out`, `output_limit`, or `not_found`. An undeclared name or a missing directory is refused.
2. A manifest sets `contributions.uses_inference`. `ExtensionInferenceService.infer` names a size class (small, mid, big) and a bounded prompt; the host sends it to the user's configured model of that class and returns the text or `unavailable`, without provider details.
3. `ExtensionRecordReader.read_records` reads one ordered page of a declared collection of the calling package; the owner is always the worker's own package.
4. Each service is in `ExtensionHostServices` only when the manifest declares it. All three are live-lane RPC calls; pure calls cannot reach them. `extensions/worker_host_services.py` assembles the services for one worker.

Verification: `tests/test_process_access.py` runs a program with shell syntax in its arguments, a timeout, a missing executable, an undeclared name, and a missing directory. `tests/test_inference_access.py` selects the size class and reports an unavailable model. `tests/test_record_access.py` reads a page and refuses an undeclared collection. `tests/extension_host/test_process_worker.py` runs `echo` for a real worker at activation. The main selection passes 3,459 cases.

Status result: The process, inference, and record reader services are done.

Addendum (same date): `ExtensionObservationSink.submit_observations` appends a worker's new originals in one scope, with their causes, through the same validation as source and observer appends; only a worker of the committed runtime can submit, as its committed manager. It exists when the manifest declares source types. `ExtensionAuditService.record_diagnostic` stores a typed diagnostic with its optional scope, job, and event in the audit errors table, named `extension:<owner>` with the diagnostic code; it exists for every worker. `tests/test_observation_sink.py` and `tests/test_audit_access.py` cover them. The main selection passes 3,462 cases.
