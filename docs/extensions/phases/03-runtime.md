# P03 — Runtime and cooperation

Status: in_progress

Owner: Codex

Depends on: P01, P02

Context: Current harness discovery runs at startup. External extensions need runtime replacement, typed proxy capabilities, and dependency-aware activation.

Task: Add discovery, managed worker processes, lifecycle transitions, dependency ordering, and versioned service access.

Outcomes: A host `ExtensionManager` and process adapters that satisfy the public protocols. Enable, disable, and reload work during normal daemon operation.

Verification: C01–C04, C10, C11, and C15–C17 pass. No extension import occurs in the daemon. Worker resources are released after shutdown.

Evidence: The application catalog, fixed package capture, private dependency environments, managed worker launch, active registry, and lifecycle repository are implemented. The daemon opens `ExtensionManager` before its engine starts. The manager restores the last committed selection asynchronously, publishes at the engine boundary, and owns cleanup. Schema 29 adds complete migration results to the schema-28 lifecycle storage. Checked lifecycle and ordinary settings HTTP controls now exist. Declared settings migrations run before candidate activation and publish with the runtime. Private daemon tests verify workers, failure retention, restart, and exit. Related-scope settings, secrets, record migrations, durable job draining, and application health policy remain open. See the work records below.

## Read first

Current cleanup update: The user approved resource/evidence separation. Schema 34 and the manager now store unresolved work before physical close and store the close result before lease release. The public runtime response exposes the last observation after restart. See the [cleanup implementation record](../worker-cleanup.md#implementation--2026-09-15). This does not complete job recovery or health policy.

Read [architecture](../architecture.md), [protocols](../protocols.md), current `api/runtime.py`, `api/workers.py`, `api/worker_plans.py`, `app/injection.py`, and `core/work_queue.py`.

### P03-T01 — Discover and validate external packages

Status: in_progress

Owner: Codex

Depends on: P02-T01

Context: Installed package discovery must not execute an unvalidated manifest entry. The normal application and tests need different extension roots.

Task: Add explicit extension roots to `ApplicationConfig` and runtime dependencies. Read manifests at startup and on rescan. Resolve development links once, validate asset and executable paths, check duplicate IDs, and compute immutable package digests. Add repository contracts and migrations for package metadata, schemas, settings, and runtime revisions. Keep invalid packages visible with typed reasons.

Outcomes: A data-only package catalog, durable management storage, and typed discovery errors. Tests can use a private root. Backend-free packages are supported.

Code areas: Proposed `extensions/discovery.py`, package models, management repositories, `app/provider_extensions.py`; current SQLite schema, `api/runtime.py`, and CLI configuration parsing.

Verification: C01 finds a valid external package. Invalid manifests, duplicates, missing files, symlink escapes, and incompatible API versions are reported. Prove that discovery did not execute a backend marker. Rescan does not activate a disabled package. Migrate a populated host database and verify that existing data and new management records survive restart.

Evidence: Explicit roots, startup discovery, revision-checked rescan, fixed package capture, private environments, catalog storage, and lifecycle storage now have application callers. The daemon keeps discovery separate from activation. Public catalog reads and rescans do not import feature code. Populated schema-26 and schema-27 migration tests retain existing data. See the discovery, capture, environment, manager, and user-control records below.

Remaining: Complete related-scope settings resolution, secret references, and retention policy. Schema 29 retains candidate settings conversion results separately from their source plans. Enable, disable, reload, and ordinary settings changes use the manager. Exact scope overrides and declared settings conversion are supported; related-scope inheritance is not.

### P03-T02 — Implement worker transport and protocol proxies

Status: in_progress

Owner: Codex

Depends on: P03-T01, P01-T05

Context: Engine and UI services must call typed protocols while worker processes remain replaceable. A single long command must not block the same extension's transform path.

Task: Implement process launch, the selected RPC library, typed proxy methods, message validation, request IDs, deadlines, stderr diagnostics, and callback dispatch. Define a bounded worker scheduler with separate pure-processing and slow-work execution. Keep one transport reader active. Reject runtime-revision mismatches.

Outcomes: `ProcessExtensionPlugin` and capability proxies implement the public protocols. Workers use private dependency environments and a known executable. No pickle or arbitrary host object transfer.

Code areas: Proposed `extensions/impl/process/`, SDK worker entry, process resources, and protocol codec.

Verification: C10 rejects malformed, oversized, timed-out, and stale replies. C11 runs a long process command while transforms complete. Bidirectional callbacks do not deadlock. Stop closes pipes and the owned process group. Strict type and protocol checks pass.

Evidence: `ProcessExtensionPlugin` exposes all 12 typed capability adapters through the SDK transport. The host owns private environments, process groups, bounded logs, request deadlines, and cleanup. The daemon manager uses this factory for restore and lifecycle requests. Tests cover pure, live, and reserved control lanes, callbacks, slow work, failed startup, crash, flood, and process exit. See the private-environment, managed-worker, and user-control records below.

Remaining: Persist diagnostics and health, connect application query/command admission and job ownership, and complete C10/C11 at the application level. Source calls now use actual host root grants under the engine's retained registry read. Current SDK, worker, and source tests are not the full release gate.

### P03-T03 — Implement dependency and service registries

Status: in_progress

Owner: Codex

Depends on: P03-T02

Context: Extensions must detect peers and work together. Load order and service availability must be explicit.

Task: Validate required and optional dependencies, service version ranges, and transform order constraints. Use the standard library topological sorter for the dependency graph with a stable ID order for equal nodes. Publish discovery and service contracts through narrow host proxies. Add caller, scope, deadline, and service-call cycle checks.

Outcomes: A stable active capability plan and versioned public service lookup. Installed, enabled, failed, and incompatible states are distinct.

Code areas: Proposed `extensions/registry.py`, ordering service, service-call adapter, and lifecycle event models.

Verification: C15 tests each package alone and both activation orders. C16 tests required and optional removal. C17 rejects dependency and service-call cycles. An absent optional service returns a typed unavailable result. No private peer import is needed.

Evidence: `ActiveExtensionRegistry` supplies fixed provider snapshots and a read boundary held through complete peer queries. SDK ordering and `HostCallLedger` check dependencies, scope, runtime, deadlines, and call cycles. Private worker tests cover both preparation orders and optional absence. The lifecycle planner uses retained active manifests. HTTP tests require exact confirmation for transitive required removal and keep optional consumers selected.

Remaining: Add service-removal notices, root application query admission, durable peer commands, and complete application C15–C17. Required removal now works through the daemon; metadata-only tests do not prove that a live optional consumer handles a removal notice.

### P03-T04 — Implement atomic lifecycle changes

Status: in_progress

Owner: Codex

Depends on: P03-T03

Context: The active package set must not change halfway through an event batch. Worker preparation or settings migration can fail.

Task: Implement requested and actual state, preparation, active runtime revisions, draining, disable, reload, and revision-checked settings activation. Prepare replacement resources before switching. Use the engine work boundary for the switch. Track all sources, handles, subscriptions, panes, and jobs owned by a runtime revision.

Outcomes: One manager operation record for each state change. Failed preparation preserves the active package. Required dependents transition together. Late results cannot restore removed contributions.

Code areas: Proposed lifecycle services and repository protocol; current engine worker boundary and application change signals.

Verification: C02–C04 cover enable, disable, restart, and reload under input. Interrupt preparation and confirm the old revision works. Submit concurrent changes and reject stale requests. A busy write command delays replacement or reports unresolved work instead of being repeated.

Evidence: Schema 28 stores requests, complete candidates, raw settings, manager generations, and operation results. `StoredRegistryCommit` joins durable completion to registry publication. `ExtensionManager` prepares complete replacements and switches at the engine boundary. `ExtensionLifecycleControl` plans user enable, disable, and reload requests. `ExtensionSettingsControl` supplies ordinary reads and scope changes through the same manager. Public routes are tested with private daemons. Exact retries survive restart; failed reload or settings activation retains the old active worker. See the lifecycle, joined-commit, manager, user-control, and settings records below.

Remaining: Add related-scope settings, secret references, candidate record migrations, durable job draining before replacement, removal notices, and complete C02–C04 under event input. Settings migration now runs on a candidate and publishes atomically. A stored operation result is not proof that every feature resource has drained.

### P03-T05 — Add failure control and application cleanup

Status: in_progress

Owner: Codex

Depends on: P03-T04

Context: A broken extension must not stop input collection, hold shutdown, or leave background processes running.

Task: Add bounded queues, consecutive-failure tracking, automatic failure state, source detachment, and shutdown cleanup. Configure limits through host policy. Record typed diagnostics and health summaries. Register the runtime in the existing worker lifecycle and release resources on daemon exit.

Outcomes: Recoverable extension failures have clear state and diagnostics. Queued original input remains durable. The daemon shuts down with no owned worker process left behind.

Code areas: Proposed runtime health and shutdown services; current `api/workers.py`, `api/worker_plans.py`, audit contracts, and work queue.

Verification: C10 floods and hangs a worker. Confirm the core queue still progresses and originals remain recorded. Stop the private daemon during work and check child-process cleanup. Repeated failures disable the extension and required dependents through a recorded revision change.

Evidence: The manager records preparation failures, retains busy candidates, and closes registry admission before shutdown. The approved cleanup change separates resource closure from unresolved-job evidence. Schema 34 stores immutable observations before physical close and before lease release. The public runtime report includes the last shutdown observation across restart. See the [cleanup implementation and checks](../worker-cleanup.md). The full Python suite passes 3,259 cases. Consecutive-failure policy, durable health, job reconciliation, and full C10 remain open.

## Work record — Application discovery and catalog

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: P01 and P02 supply enough draft manifest, model, and quality code for data-only discovery. This work starts P03-T01 before those phases are complete. It does not depend on pending live host services and does not freeze the draft SDK.

Task: Connect external package discovery to normal application startup, stored management reads, and an explicit rescan. Keep feature imports and activation outside this path.

Outcomes:

- `ApplicationConfig.extension_roots` selects private or normal roots. The public import remains `api.runtime.ApplicationConfig`; its implementation is now in `api/runtime_config.py`. The default is `<data_directory>/extensions`. An explicit empty tuple disables discovery. `BAQYLAU_EXTENSION_ROOTS` uses the platform path separator. Repeated `--extension-root DIR` options preserve paths with spaces when the CLI forwards them to the daemon.
- Each root contains immediate child built-package directories with `extension.json`. A root entry can link to an external build directory. The scanner resolves that entry once. Internal file and directory links are rejected. The scanner neither creates roots nor imports a backend.
- Manifest size is limited to 8 MiB. Each package inventory is limited to 10,000 paths and 256 MiB of regular file bytes. Each root is limited to 1,000 entries, including ignored regular files. These are draft bounds, not performance measurements. Pipes cannot block file reads. The digest includes relative paths, lengths, file hashes, and executable bits.
- Declared web assets must have exact hashes. Each E2E entry must exist. A backend module must resolve to exactly one owned Python file in flat or `src/` layout. Duplicate extension IDs invalidate all owners; no package wins by load order. Bad metadata remains visible with a typed error and without copying invalid document values into diagnostics.
- `extension_catalog_head`, `extension_packages`, `extension_catalog_errors`, and `extension_package_manifests` are current schema-27 tables. The last table retains validated manifests and their bundled schemas after source removal. It does not retain file bytes. The other tables describe discovery, not enabled state.
- A repository read captures one SQLite snapshot. A write compares the expected revision inside the transaction and replaces the catalog atomically. Unchanged scans keep the revision. A failed root scan keeps the last complete entry set and stores root errors. A complete later scan clears those errors. File reads occur outside the write transaction.
- Startup scans after both application databases are initialized. `GET /api/extensions` reads stored metadata only. `POST /api/extensions/rescan` requires `expected_revision`; stale state returns 409. Successful synchronous scans return 200. The summary omits full manifests, schemas, E2E declarations, and settings. The typed Python HTTP SDK exposes `client.extensions.catalog()` and `.rescan()`.
- Rescan requires JSON and rejects a foreign browser Origin. A local API request can omit Origin. This is admission for this route, not authentication for untrusted local software. Inspection found no working general read-only switch despite old comments in `api/config.py`. Lifecycle and command policy must be implemented and tested before writes are exposed; C24 is not complete.
- The dashboard OpenAPI types were regenerated directly from the application factory, without starting the live daemon. The generated diff adds the catalog models and routes. It also removes an old harness-route docstring claim to match the current source.

Verification:

- `.venv/bin/python -m pytest tests/extension_host -q`: 56 passed. The fixtures use temporary package roots and databases. The two process cases use the real daemon and public HTTP SDK to check startup, rescan, restart, invalid peers, and backend-free metadata.
- `tests/extension_host/fixtures/main-schema-26.sql` contains the complete committed schema from `6a9e497`. The migration test populates that schema, upgrades it to 27, and checks retained data. The rollback test fails after row replacement and proves that both the catalog and its revision remain unchanged.
- The backend fixture contains a module-level marker write. The marker remains absent after startup and rescan. Its declared E2E file is only a file-existence fixture. It is not proof that a feature-owned E2E runner exists or that a product extension works.
- `make typecheck`: passed for 2,374 source files. New host tests use strict settings. The shared-policy comparison was updated only for their additional strict root.
- `make test-frontend` and `make lint-frontend`: passed. The dashboard has 114 passing unit tests and the web SDK has 28. Shared wire generation and format checks passed. `make build-frontend` passed; the live LaunchAgent was not restarted.
- The full regression run first found four integration failures. Naming rules and the new strict test root were corrected. The HTTP contract test now validates actual JSON response bytes, so strict tuple models use the JSON boundary rather than a decoded Python list. It still checks every selected read route.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,399 passed after those fixes. Warnings concern existing pytest helper imports and Starlette's deprecated `httpx` test-client path. Live harness, live Kitty, and feature-extension E2E cases were not run.
- `make policy-check`: passed with shared policy digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. Ruff and focused Wemake checks pass. Full dead-code checks report 27 missing application callers: the prior 25 plus the retained-manifest contract and implementation. Full Wemake still reports six unrelated findings in user-changed Codex control and test files. Full lint is not green.

Remaining work: Complete artifact capture, executable environments, settings and active-revision repositories, and P03-T02–T05. No event pipeline, extension worker, frontend slot, or Kitty view uses this catalog yet. The retained-manifest read method has tests but no history caller. Do not hide that missing caller with a dead-code exception. The two `PackageValues` field exceptions describe real positional SQLite reads, not unused runtime functions.

Next action: Capture a package into host-owned storage, verify the complete captured inventory against the selected catalog revision and digest, and prepare the private worker environment from that copy. Reject changed sources before any factory import. Then connect the existing SDK proxies to host supervision and lifecycle state.

## Work record — Fixed package capture

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: The first catalog checks source files, but their paths can change before worker startup or asset delivery. Runtime consumers need a fixed copy of the selected bytes. This record supersedes the artifact-capture gap in the earlier work record; it does not close all of P03-T01.

Task: Capture complete package files through an explicit protocol. Connect capture to the application scanner. Reject changed sources and corrupt copies before use, without importing feature code.

Outcomes:

- `ExtensionArtifacts` defines typed `capture_package(PackageCaptureRequest) -> PackageArtifact` and `read_artifact(package_digest) -> PackageArtifact` methods. The request binds the resolved source, expected digest, and expected manifest. The result contains the checked directory, digest, and manifest.
- `FilesystemExtensionArtifacts` stores copies at `<data_directory>/extension-artifacts/<digest>`. This root is separate from discovery and future dependency environments. The provider creates no directory when there is no package to capture. Tests seed an explicit private store.
- Capture first checks the complete source inventory against the selected digest. It writes new regular files into an owned temporary directory under the artifact root. Each bounded copy checks bytes and executable bits against the selected inventory. It follows no final file links and cannot wait on a pipe. It uses no hard links. Empty source directories have no file identity and are not copied.
- The complete copy is checked again against the digest and manifest. Files become read-only while their executable bits remain unchanged. Directories become read-only. File data and directories are synced before publication. Publication renames the complete directory within the same parent. This is required on macOS for read-only directories.
- Concurrent captures can prepare separate copies. The atomic rename preserves a complete existing winner. macOS may report EACCES for a read-only winner; this is accepted only when a target directory exists, and the complete winner is then checked. Invalid, linked, or corrupt targets are rejected, not overwritten. Ordinary failures remove only the capture operation's temporary directory.
- A new digest creates a new path. Prior artifact bytes remain available after source update or removal. Existing-copy reuse and reads check the complete stored digest; they do not require the old source. The host does not edit published packages or delete them on rescan. A corrupt artifact is a visible failure, not a reason to silently load newer source code.
- `app/provider_extension_artifacts.py` composes the store and `CapturingExtensionScanner`. Startup and rescan capture only valid entries from a complete root scan. Failed capture becomes `capture_failed`, with bounded diagnostic text. The catalog's existing compare-and-set transaction remains after file work. A losing catalog write can leave an unused valid artifact; it cannot activate it.
- The capture path has application callers. The standalone artifact read method still awaits worker preparation and asset delivery. No dependency environment is created, no package is enabled, and no backend factory is imported by this change. The generated dashboard API includes the new discovery failure code.

Verification:

- `tests/extension_host/` now has 83 passing cases. `test_artifacts.py` checks independent file bytes, executable bits, read-only permissions, reuse, source removal, backend-free copies, and retained old digests.
- `test_artifact_failures.py` checks changed source content and inventory, invalid digest paths, corrupt copies, a linked store root, and recursive capture prevention. `test_artifact_transactions.py` holds two complete copies at the publication boundary, changes source files after inventory validation, injects publication failure, and preserves invalid existing targets.
- `test_catalog_capture.py` checks copy creation through the real application, visible failure and rescan recovery, and the separate private daemon path. Import markers remain absent. These tests do not count as feature-owned E2E or active-worker evidence.
- The focused host and file-boundary run passed 90 cases. Strict type checks passed for 2,387 source files. Ruff and focused Wemake checks pass. The only new file-access exception names the directory-sync leaf; no broad file or JSON exception was added.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,426 passed on macOS with Python 3.12.1. The warning classes are unchanged. No live harness, real Kitty, external service, or feature-owned release E2E was run.
- `make build-frontend` and dashboard `check`, `lint`, and `format:check` passed after the generated discovery error type changed. The live daemon was not restarted. The shared policy version and tool pins did not change.
- The full dead-code gate reports 29 missing application callers: the prior 27 plus the artifact read contract and implementation. They remain required integration work, not new exceptions. Full Wemake has the same six unrelated findings after the capture code's own design checks were fixed. Full lint is not green.

Limits: Read-only file permissions are an ownership rule, not an OS sandbox. Software with the same user identity can change permissions or files; consumers must use checked reads and failure policy. This implementation has no power-loss test or cleanup of staging directories left by SIGKILL. Old-copy retention is deliberate. Release storage limits and owned orphan cleanup remain P03-T05/P08 work.

Next action: Define and check the private Python environment from the captured package's standard packaging metadata and locked dependencies. Bind it to the package digest and SDK release. Use the selected environment and fixed copy for the SDK worker, then connect typed proxies, process-group cleanup, and lifecycle state. Do not use the daemon's dependency environment or mutable source path as an activation fallback.

## Work record — Private Python environments

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: Fixed artifacts now retain backend and dependency bytes. Worker preparation must not use mutable source, the daemon's site-packages, or an inherited installer configuration. This subset continues P03-T01 and starts P03-T02. It does not complete either task.

Task: Add explicit preparation and resource-ownership protocols, install locked wheels in a new private environment, check its identity, and bound preparation processes. Keep activation separate from catalog discovery.

Outcomes:

- `BackendEnvironment` adds `requirements` and `wheelhouse` relative paths to the draft SDK manifest. Discovery checks the declared lock and at least one wheel file without importing code. Older draft declarations can omit the field and remain discoverable, but environment preparation rejects them. There is no fallback to host dependencies.
- `ExtensionEnvironments`, `WorkerEnvironment`, and `PreparationRunner` are explicit protocols in `extensions/environment_contract.py`. `LocalExtensionEnvironments` reads the selected artifact, checks the lock, creates a unique owned directory, prepares `venv`, and returns its exact executable and artifact. The caller must stop all workers before `close()`. Normal failures remove only this owned directory. Active environment reuse and persisted environment state are not implemented.
- The host invokes its installed `uv` package with an explicit Python path and isolated module mode. `requirements.txt` declares `uv>=0.7.4,<1`; this verification used 0.12.13. Commands use offline mode, no configuration or cache, no Python downloads, a fixed Python selection, hash checks, wheel-only installation, and file copies. They inherit no daemon, Python, or installer environment variables. The command environment contains only explicit PATH, TMPDIR, and locale values.
- The accepted lock syntax uses the packaging library for requirements. It permits exact `==` pins, extras, markers, full SHA-256 hashes, comments, and continuations. It rejects direct URLs and file references, source and editable paths, nested requirements, and installer directives. Lock reads have a 1 MiB bound. The host does not implement a dependency resolver.
- The standard dependency check rejects incomplete installs. The SDK's isolated `runtime.environment_probe` reports the executable, private and base prefixes, Python version, SDK version, and SDK directory. The host checks them against its selected environment and installed SDK version. This probe imports no feature module and adds no feature or host source path.
- Environment directories stay at their original paths. Resolving the Python symlink would select the base interpreter, so executable selection retains the private path. One environment can be closed without changing another or the retained artifact.
- `BoundedPreparationRunner` uses AnyIO to drain stdout and stderr together. Each command has a 120-second default deadline and 1 MiB combined output bound. Commands have no shell or input stream and run in their own process group. Success, failure, timeout, and output overflow release that group and close the pipes. Tests cover descendants that close their output pipes and would otherwise survive the parent.
- Offline test fixtures build the SDK with its standard setuptools backend and pack installed dependencies with the standard wheel tool. Fresh build metadata selects the dependency graph. The host dev requirements now include these two build tools; shared quality pins and the policy digest are unchanged. These are test wheels, not published release artifacts.

Verification:

- `.venv/bin/python -m pytest tests/extension_host -q`: 136 passed. This includes all 83 prior discovery, capture, catalog, and private-daemon checks plus 53 preparation checks. Private installs, missing SDK and dependencies, wrong hashes, missing wheels, manifest paths, lock syntax and bounds, source changes, private imports, version checks, output limits, deadlines, and child cleanup are covered.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,479 passed in 66.57 seconds on macOS with Python 3.12.1. The existing warning classes are unchanged. No live harness, live Kitty, external service, or feature-owned release E2E was run.
- `make typecheck`: passed for 2,409 source files. `make policy-check`: passed with shared policy digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. Focused Ruff and Wemake checks passed after local findings were corrected.
- The web SDK's wire generation check, type check, lint, and 28 unit tests passed. The new manifest field does not change its exported wire models. No dashboard bundle or live process was changed by this subset.
- The first real install tests found a conflicting `uv` flag pair and stale editable SDK dependency metadata in the test fixture. The installer now uses wheel-only mode without the conflicting flag. The test wheel graph now comes from fresh SDK build metadata. The full tests passed after both corrections.
- The full dead-code gate reports 31 missing application callers: the artifact read methods now have preparation callers, while four new preparation entries still need lifecycle composition. No new dead-code exception hides them. Full Wemake retains six unrelated findings in user-changed Codex controls and tests. Full lint is not green.

Limits: These checks do not activate a feature, enforce an OS sandbox, persist operation diagnostics, recover directories after SIGKILL, test every supported platform, or provide worker runtime cancellation. Environment preparation is synchronous and must be called from the lifecycle's blocking-work boundary, not an active async event loop. Long-lived RPC worker launch, clean installed-SDK feature path loading, bounded stderr diagnostics, lifecycle state, and atomic replacement remain open. No live daemon was restarted.

Next action: Start the installed SDK worker with the selected private executable and checked feature path. Reuse the SDK's typed transport and proxies, keep one reader, bind calls to runtime revisions, and own process-group shutdown. Then connect preparation to lifecycle operations and durable settings and active-revision storage. Do not add a preparation HTTP endpoint just to create an application caller.

## Work record — Managed private workers

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: Private environment preparation can run the SDK probe, but no host adapter could start the real worker or own its long-lived resources. P01 already supplies the RPC library adapter, typed capability proxies, execution lanes, and call ledger. This work continues P03-T02. The full phase and application lifecycle remain incomplete.

Task: Start the installed SDK from a checked private environment, load only the selected package path, expose the existing capability protocols, and own process failure and shutdown. Keep discovery and active-set changes separate.

Outcomes:

- `extensions/worker_contract.py` defines `ExtensionWorkers` and `ExtensionWorker`. `ProcessExtensionWorkers` validates the load request and host services, prepares its exact package digest, rechecks the manifest selection, and returns an owned worker only after a valid ready reply. `ProcessExtensionPlugin` implements the public `ExtensionPlugin` and composes all 12 existing SDK proxies. No feature implementation is imported in the parent.
- The process adapter is in `extensions/impl/process/`. It uses the standard AnyIO blocking portal for one reader thread per worker, the SDK's existing JSON-RPC channel, and one inherited socket. Python runs with isolated mode, no bytecode writes, and unbuffered output. Only the selected socket descriptor is passed. Standard output and error remain separate from RPC. The explicit worker environment contains PATH and locale, not inherited daemon or installer settings.
- The installed SDK entry now requires `--package-directory`. It checks the declaration before selecting exactly one backend file from flat or `src/` layout. It rejects entry links, ambiguous entry files, and names that shadow installed SDK or standard modules. The chosen source root is appended after the installed SDK has started. Both the SDK process fixture and independent shared-policy fixture now use this entry directly, without a `-c` path bootstrap.
- Directory and optional service-access callbacks are registered before the load request. The existing execution lanes and request validation are reused. A real private worker can call the directory from its factory and can transform while a live lifecycle call is blocked. The host did not add a second message format or copy proxy methods.
- `WorkerPolicy` currently defaults to a 30-second request deadline, a 2-second graceful process-exit period, and a 1 MiB combined stdout/stderr budget for one process generation. Concurrent stream readers keep that evidence bounded. Exceeding the budget closes the connection and kills the owned group. These are explicit draft limits; performance measurements and final release limits remain P08 work.
- Worker monitors watch the process, transport, and log streams. Failure revokes that worker's call grants and closes the channel. Normal close revokes grants, closes monitors and transport, stops the owned process group, closes the reader loop, and then removes the private environment. Repeated close is safe. A pending call receives a transport failure. Saved proxies fail cleanly after the loop closes and do not leave unawaited coroutine warnings.
- `WorkerDiagnostics` retains the process ID, return code, bounded bytes, and observed failure category. `WorkerStartError` holds this evidence after failed startup cleanup; its public exception message contains no feature log text. There is no durable diagnostic repository or HTTP diagnostic route yet.
- Preparation and worker shutdown now share `extensions/process_groups.py`. It rejects non-positive group targets. After `EPERM`, it checks whether a live member remains before accepting an exited-only group. Other permission failures remain errors. A real single-threaded probe reproduced macOS `EPERM` for an owned zombie-only group. The behavior matches [XNU's group signal filter](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_sig.c).

Verification:

- The full run includes 158 passing extension-host cases: the prior 136 plus 22 managed-worker and group-cleanup checks. New cases cover flat and `src/` loading, real private SDK execution, factory callbacks, lifecycle and raw transform calls, unchanged captured source, installed-module collisions, failed startup, runtime crash and flood, concurrent live and pure work, pending-call shutdown, closed proxies, grant revocation, and zombie-only group cleanup.
- `.venv/bin/python -m pytest tests/extension_api tests/dev_tools -q -n 4`: 1,129 passed. This verifies the new direct SDK entry across the existing source, translation, projection, migration, observer, command, terminal, and peer fixtures, as well as the independent shared-policy fixture. The new SDK source-selection unit cases are included. These are not product adapters or Git extension releases.
- The first full regression run passed 2,502 cases and failed one existing preparation-output case because cleanup masked the expected output-limit error with macOS `EPERM`. The reproduced zombie-only state and five new group-cleanup tests support the correction. The check still rejects a live member whose signal permission is denied.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,508 passed in 66.18 seconds after the correction. The platform is macOS Darwin 25.6.0, arm64, Python 3.12.1. The existing warning classes are unchanged. Live harness, live Kitty, external services, and feature-owned release E2E were not run.
- `make typecheck`: passed for 2,436 source files. Ruff and focused Wemake passed after local findings were corrected. `make policy-check` passed with unchanged policy digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`.
- The web SDK's wire generation check, type check, lint, and 28 unit tests passed. Worker runtime changes do not change its generated public wire models. No dashboard build or live process restart was needed for this subset.
- The full dead-code gate now reports 18 missing application callers. The new process adapter consumes the SDK proxies and call revocation path, but the environment services, worker factory, retained history, and processing mappers still need application composition. No new dead-code exception hides that work. Full Wemake retains the six unrelated findings in user-changed Codex controls and tests. Full lint is not green.

Limits: The worker factory is not a daemon activation operation. It does not commit enabled state, switch an active capability plan, persist settings or diagnostics, own pane registrations, enforce application read-only policy, or reconcile external write jobs. Host callbacks must remain bounded. The process boundary is not an OS security sandbox. Normal close cancels log readers before process cleanup, so final shutdown log bytes are not guaranteed. Orphan recovery after host SIGKILL, final output policy, and application-scope C10/C11 checks remain open. No user database, live daemon, remote Git repository, or external service was changed.

Next action: Implement the active dependency and service registry with immutable provider snapshots and explicit required/optional dependency behavior. Then add durable lifecycle operations, settings and runtime revisions, and atomic activation at the engine work boundary. Connect the tested worker factory to those operations; do not activate packages during discovery or add a standalone prepare endpoint.

## Work record — Active registry and peer reads

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: Managed private workers exist, but their SDK test provider has no safe active-set publication boundary. The host must retain a selected provider through a complete query, not just through metadata lookup. This work starts P03-T03 and does not complete daemon activation.

Task: Add an explicit registry protocol, checked immutable selections, stable dependency order, required-dependent removal planning, and service adapters that hold the active read boundary.

Outcomes:

- `extensions/registry_contract.py` defines `ExtensionRegistry` and `RegistryRead`. `ActiveExtensionRegistry` implements a non-blocking compare-and-set publication. Readers hold a fixed selection in a context manager. A mutex protects only the head and reader count, never a worker call. Nested reads release on normal and failed exit. Publication returns `busy` while any reader remains, or `stale` after another writer wins.
- `prepare_snapshot()` validates installed identities, manifests, schemas, settings, active environments, and plugin capabilities. It reuses SDK `activation_order()` for required and optional dependencies, service versions, explicit order, and exclusive view checks. Input order does not change the active plan. Publication rechecks a directly constructed snapshot instead of trusting a supplied order or schema object.
- Public metadata distinguishes inactive states from enabled entries. An inactive package cannot expose an environment or plugin. Enabled backend-free packages need no worker. Each active environment uses the selected runtime revision and exact package identity. A prior runtime ID cannot be published again during this registry lifetime. Catalog revision cannot move backwards. Durable uniqueness after daemon restart remains a repository task.
- `RuntimeSettings` supplies one fixed owner revision, a complete fallback value, and complete effective values for exact scopes. All declared documents and scopes are checked before publication. This is captured input, not a persistence service, secret service, partial JSON merge, or scope inheritance resolver.
- `RegistryDirectory` copies metadata while holding a read. `RegistryServiceAccess` retains the read through peer authorization, execution, and reply validation. It reuses `HostServiceAccess` and `HostCallLedger`. Metadata can be read during preparation. Queries require the exact currently active caller and a host grant. A saved callback from a removed caller is rejected, even when a grant still exists.
- `removal_order()` selects transitive required dependents in reverse active order. Optional consumers remain. Missing or repeated requested owners are errors. This function does not stop a worker or write requested state. The future manager must publish the complete removal and notify optional consumers.
- The registry borrows capabilities. Worker ownership stays with the future manager. Application root calls must retain a registry read for their full operation. The manager must prepare candidates outside the read boundary and retry publication at the engine work boundary. It must not close old workers after a busy or stale result. No active pointer or lock is held across a feature factory import in the daemon; all feature imports remain in private workers.

Verification:

- `tests/extension_host/test_registry*.py` adds 35 cases. Local protocol doubles cover stable order, duplicate owners, invalid worker identity and state, backend-free packages, settings selection and failure, required removal, optional absence, cycles, concurrent writers, nested readers, failed-reader cleanup, old catalog rejection, runtime ID reuse, and grant checks.
- `test_registry_services.py` holds an actual provider call with thread events. Another thread receives `busy` until the full call returns. The test has a bounded wait and always releases its hold. This proves no registry lock is held across the query and no normal publication can retire its provider early.
- `test_registry_process.py` has three passing real-process cases. Two independent packages use the production capture, offline environment, worker factory, registry directory, and service adapter. Both preparation orders complete a peer query after publication. A consumer runs alone with typed optional unavailability. Factory-time reads do not see unpublished candidates. The feature module is absent from the parent's imports. These are runtime integration tests, not package-owned product E2E.
- `.venv/bin/python -m pytest tests/extension_host tests/test_architecture_api.py tests/test_architecture_protocols.py -q -n 4`: 207 passed, including all 193 extension-host cases.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,543 passed in 78.70 seconds. Existing warning classes remain. No live harness, live Kitty, adapters CLI, remote Git, or external service tests were run.
- `make typecheck`: passed for 2,453 source files. Root Ruff and focused Wemake passed. `make policy-check` passed with unchanged policy digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. No shared rule or exception was weakened.
- Full dead-code checks report 24 missing application uses, including the new registry publication and removal paths. No exception hides these missing callers. Full Wemake still reports the same six unrelated findings in changed Codex controls and tests. Full lint is not green. No web wire model changed, so no frontend regeneration or live daemon restart was required.

Limits: There is no daemon lifecycle caller, persisted active head, requested-state store, settings repository, automatic failure transition, removal notification, or root operation admission service yet. The registry does not map invalid discovery rows into lifecycle states, select candidates from the durable catalog, drain write jobs, or provide durable peer commands. A crashed worker can still interrupt a held read. Returned capability objects must not be used after the read context ends. P03-T03 stays in progress.

Next action: Add the lifecycle repository and `ExtensionManager` operations. Store requested state, settings, operation results, and unique runtime revisions. Select exact captured packages from the catalog, prepare owned workers, and publish at the engine work boundary. Preserve the old set on preparation failure. Connect this path to daemon startup and shutdown before adding enable/disable HTTP controls. Keep write policy and unresolved external jobs explicit.

## Work record — Durable lifecycle state

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: The worker and registry services have no durable lifecycle caller. A manager needs separate requested state, immutable candidate selections, raw settings choices, and recorded outcomes before it can prepare workers and switch a running set. This work starts P03-T04. It does not complete daemon activation.

Task: Add an explicit lifecycle repository and schema migration. Reserve candidates before preparation. Preserve the accepted runtime on failure. Retain default inheritance and reject old manager completions. Test transactions with real private databases.

Outcomes:

- `repository/contract/extension_lifecycle.py` defines `ExtensionLifecycleRepository`. Its six methods read current state, claim a manager generation, accept and finish an operation, and read operation or runtime history. `SqliteExtensionLifecycleRepository` owns the complete transaction for each method. No caller receives a connection and no repository operation runs feature code.
- Schema 28 adds `extension_lifecycle_head`, `extension_runtime_revisions`, `extension_lifecycle_operations`, `extension_requests`, and `extension_settings`. The schema-27 catalog tables are unchanged. Typed selection, proposal, failure, and override documents retain exact host data. Runtime and operation IDs have persistent unique keys. A failed or interrupted candidate keeps its runtime reservation.
- `LifecycleProposal` pins the complete ordered runtime candidate and affected requested states. `RuntimePackageSelection` carries exact package identity and effective settings. Admission checks the lifecycle head, manager ID, catalog revision, and all changed settings revisions inside the write transaction. Retained manifests, dependency order, identity, intent, and settings must agree. A complete operation document is bounded at 8 MiB.
- Admission writes the reserved runtime, operation, requested state, and pending head together. It returns `accepted`, `replayed`, `stale`, or `busy`. An exact request retry reads the prior operation without another reservation. Changed request bytes cannot reuse the operation ID. Only one lifecycle operation can be pending. Package rescans after admission do not change its already captured candidate.
- `LifecycleCompletion` cannot replace the candidate. Success commits its stored runtime, raw settings changes, terminal operation outcome, and head together. Failure records a bounded typed reason and keeps the previous committed runtime and settings. Requested enable state remains visible after failure. A completed retry is accepted only while its completed head is unchanged; it cannot authorize a rollback after newer work.
- `ManagerClaim` compares the expected lifecycle head, selects a new manager ID, marks prior pending preparation as `interrupted`, and clears the pending head in one transaction. Old completions are rejected. The last committed runtime remains available for restoration. A claim is allowed only after exclusive daemon ownership has been established; the repository itself is not a process lock and does not prove the old process has stopped.
- `SettingsOverrides` stores explicit installation choices and complete exact-scope overrides. An absent installation choice inherits the selected manifest default. `capture_settings()` creates effective runtime values without changing those raw choices. Reset retains that distinction. Disabled packages can save settings without being enabled. Related-scope inheritance, secret-reference handling, and actual migration calls remain open.
- Package identity and effective settings validation now belong to the model layer. The registry and repository share these checks through `RuntimePackageSelection.validate_manifest()` and `RuntimeSettings.validate_declaration()`. The repository imports no registry service or worker adapter. The temporary service-level settings helper was moved into `extensions/models/settings.py`; its behavior remains available as one shared pure function.

Verification:

- `.venv/bin/python -m pytest tests/extension_host/test_lifecycle*.py -q`: 36 passed before the final model-layer move. The later focused and full checks below cover that move as well.
- The new cases cover admission and completion replay, concurrent writers using two repository objects, stale lifecycle/catalog/manager/settings revisions, persistent runtime reservations, wrong package identity, invalid settings, invalid timestamps, request-size revalidation, manager restart claims, old completion rejection, failed reload, atomic settings activation, reset, changed defaults, and disabled-package settings.
- `test_lifecycle_transactions.py` injects an exception at the final head write during admission, completion, and manager claim. Prior SQL changes roll back, including settings, operation status, runtime commit time, requested state, and reserved candidate rows where applicable. The previous head stays readable.
- `test_lifecycle_migration.py` creates a populated independent schema-27 database from `fixtures/main-schema-26.sql` and `fixtures/extension-catalog-schema-27.sql`. It upgrades to the current schema and checks the retained catalog, retained manifest, core pane setting, and version. The old catalog migration case also continues to upgrade schema 26 to the current version.
- The first broad host, repository, protocol, and SQLite check passed 257 cases. The first full run then passed 2,576 and failed three architecture gates. These identified repository imports of registry services and two naming-rule groups. The shared pure checks were moved to model classes, identifier annotations were corrected, and repository parameter names were made explicit. No architecture exception was added.
- `.venv/bin/python -m pytest tests/extension_host/test_lifecycle*.py tests/extension_host/test_registry*.py tests/test_canonical_naming_identifiers.py tests/test_canonical_naming_parameters.py tests/test_architecture_domain.py -q -n 4`: 80 passed after those changes.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,579 passed in 71.22 seconds. The run includes all 229 extension-host cases. Existing warning classes remain. Live harness, live Kitty, adapters CLI, remote Git, external services, and package-owned release E2E were not run.
- `make typecheck`: passed for 2,476 source files. Root Ruff and focused Wemake passed. `make policy-check` passed with unchanged policy digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. Full dead-code checks report 37 missing application uses, including the lifecycle repository methods. Full Wemake still has the same six unrelated findings in changed Codex controls and tests. Full lint is not green; no exception hides the missing manager caller.

Limits: A stored completion is not worker readiness proof. These storage tests explicitly submit completion without a worker. No manager prepares the candidate, drains existing work, joins database commit with registry publication, restores live workers, or provides enable/disable routes yet. `committed_runtime` is not an observed running state. Runtime state and settings history are retained, but event processing, history rebuilds, jobs, frontends, adapters, and Git remain open. No user database, live daemon, or external service was changed.

Next action: Implement `ExtensionManager` with explicit process ownership and bounded preparation. Connect the existing private worker factory, registry callbacks, and lifecycle repository. Extend the registry publication boundary so it can commit the accepted database candidate while readers are excluded, then install the prepared in-memory selection before releasing that boundary. Prepare all data and resources before this short section; it must make no feature call. Test database failure, stale completion, and process exit on both sides of commit. Then connect publication to the engine work boundary and daemon startup/shutdown. Do not claim readiness from the stored head alone or expose an independent prepare endpoint.

## Work record — Prepared runtime and joined commit

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: Storage, individual workers, and the registry existed as separate services. A complete candidate needed one preparation owner and a stored commit before publication. Manager generation claims also needed an explicit native process-ownership service.

Task: Prepare whole runtime candidates, join database completion with the registry read boundary, and provide exclusive per-directory ownership. Verify failed preparation, busy readers, stale state, SQL commit failure, and process exit without changing the live daemon.

Outcomes:

- `ExtensionRuntimePreparation` and `PreparedExtensionRuntime` are explicit host protocols. `RuntimePreparation` reads all fixed artifacts and checks the complete candidate before starting workers. It validates package identity, settings, peer schemas, and exact dependency order. It prepares fresh workers in dependency order and sends each its captured activation settings. Backend-free and empty candidates need no Python process.
- All replacement workers use the new common runtime revision. No old worker is relabeled or reused. Each candidate has a separate cleanup stack. Failed load or activation closes only new resources, in reverse order. `OwnedPreparedRuntime` owns the ready workers; a registry snapshot borrows their capabilities. Closing an owner prevents further snapshot access. The manager must remove and drain a published set before closing its owner.
- `ExtensionRegistry.publish_snapshot()` now requires `RegistryCommit`. There is no production default which skips storage. `StoredRegistryCommit` rejects a changed candidate and completes the exact accepted operation. Busy or stale registry publication does not call storage. New read state and the reply are prepared before commit. The registry mutex excludes readers until the accepted stored head is installed in memory.
- `RuntimeSnapshot.runtime_selection()` captures enabled package identities and settings in checked active order. Standalone registry unit tests explicitly use a test-only `MemoryRegistry`; the joined tests use the real registry and SQLite adapter. No application dead-code exception was added for these new services.
- The SQLite write helper now rolls back when `connection.commit()` itself raises. Previously that failure could leave its thread-local connection in a transaction. A real connection subclass injects this failure after writes. The test verifies the old stored state, old registry, released transaction, and successful retry.
- `ExtensionRuntimeOwnership` and `ExtensionRuntimeLease` are explicit process-ownership protocols. `FilesystemRuntimeOwnership` uses `filelock>=3.32.6,<4`, native locking, one non-blocking attempt, mode 0600, no soft fallback, and a retained lock path. The resolved data directory, not the HTTP port, selects ownership. No stale PID marker is removed.
- Lease operations reject closed and forked copies before taking their local mutex. An ownership context blocks close from another thread until the state operation returns. The library releases the native lock on process death. Tests verify that a forked child cannot enter a held parent mutex or release the parent's native lock.

Verification:

- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,611 passed in 95.78 seconds on macOS with Python 3.12.1. This includes all 261 extension-host cases. The 32 new runtime cases also passed together in a focused run. Existing warning classes remain.
- The first full check for this subset passed 2,599 tests before the last 12 new cases were added. A later run passed 2,610 and failed the activation test's outdated expected public error message. The corrected focused and final full runs passed. No production validation was weakened.
- New cases use private SQLite files and actual offline worker environments. They test both discovery orders, peer queries after commit, failed reload after another candidate worker starts, successful reload after a busy read, changed source after capture, backend-free and empty sets, invalid candidate order and identity, and rejected activation.
- Commit tests cover busy readers, stale writers, a substituted candidate, an old manager, an actual COMMIT exception, and a new reader held between database commit and registry pointer replacement. Child processes exit immediately before or after durable completion; restart reads and claims the correct stored state.
- Ownership tests cover independent instances, directory aliases, separate stores, repeated close, cross-thread close, close during an active ownership context, unsupported native locking, unexpected lock paths, private mode, stable inode, process death, and fork inheritance.
- A wrong activation revision is rejected by the SDK worker dispatch and returned as a bounded `ExtensionTransportError`. The first activation test expected the host's readiness error instead. Its expected exception and public message were corrected; no production check was relaxed.
- `make typecheck` passed for 2,496 source files. Root Ruff, focused Wemake, and shared policy parity passed. The policy digest remains `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. Full dead-code checks report 39 missing application uses. Full Wemake retains the same six unrelated findings in changed Codex controls and tests. Full lint is not green.

Limits: These are connected runtime services and private process checks, not daemon activation or C02–C04 completion. No engine work boundary, asynchronous manager operation loop, installed-inactive directory merge, runtime restore, removal notice, job drain, health policy, or lifecycle HTTP route calls them yet. The preparer must run outside the engine and async request loop. The lease must cover the whole manager lifetime, including cleanup. A failed pending operation is completed explicitly by the test; automatic failure recording remains manager work. No real Git, adapters, Slack, live Kitty, or feature-owned release E2E was run. No live process or user database was changed.

Next action: Compose `ExtensionManager` with the ownership lease, lifecycle repository, preparation service, and active registry. Acquire ownership before the stored claim. Prepare asynchronously, preserve installed inactive metadata, and switch only at an explicit engine boundary. Keep busy candidates owned for retry. Remove and drain old references before closing workers. Restore the last committed selection under a fresh runtime revision after restart. Do not expose enable/disable routes before this path and write policy exist.

## Work record — Manager and daemon ownership

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: The complete preparer, stored commit, registry, and native lease had tests but no application owner. Preparation and worker cleanup could not run on the engine thread. Startup also needed to distinguish the last committed selection from a pending or failed request.

Task: Implement explicit manager protocols, asynchronous preparation, engine-boundary publication, retained cleanup, and daemon startup/shutdown. Preserve request-only application behavior. Verify real worker restoration and exit without changing the user's daemon or database.

Outcomes:

- `extensions/manager_contract.py` defines `ExtensionManager` and the smaller `ExtensionRuntimeBoundary`. `ManagedExtensions` explicitly implements both. Typed methods read manager state and operation history, admit a complete host proposal, publish ready work, retry retained cleanup, and close resources. Catalog reads and rescans keep their existing smaller protocol. User enable/disable/reload/settings requests still need a checked request-planning service; HTTP must not pass a complete client-supplied runtime proposal directly to the manager.
- `ExtensionManagerFactory` acquires the native data-directory lease before the stored manager claim. It starts one preparation executor and accepts one restore operation with a fresh runtime ID. Restore selects the last committed packages and effective settings, not an uncommitted enable intent. An empty installation also publishes a checked empty runtime.
- Admission checks manager, lifecycle, and catalog state before scheduling preparation. Exact retries return the existing operation, including during old-runtime cleanup. Only one new preparation can be pending. An unavailable executor produces a recorded preparation failure, not a permanently pending request. Preparation runs without the manager mutex and cannot block normal state reads.
- `manager_directory.py` merges valid inactive catalog metadata with the active prepared set. Invalid discovery rows stay in the management catalog. Inactive rows expose no worker or environment. A retained active digest wins over a changed source version until a new complete operation selects it. Fixed artifacts remain the source of runtime bytes.
- `publish_ready()` keeps a ready candidate when registry readers make publication busy. A database exception leaves that same candidate ready for retry. Successful publication uses the joined `StoredRegistryCommit`, then transfers the candidate to active ownership. Old-resource cleanup is scheduled on the background executor. A stale manager generation rejects publication and stops processing through this boundary.
- `EngineExtensionBoundary` handles `WorkKind.EXTENSIONS` before an ordered core batch. It retains core notices while initial restoration is pending. Preparation completion signals the existing work queue. Busy publication and failed commits schedule a short retry. Reload preparation leaves the current runtime available. A recorded initial preparation failure permits core fallback; P04 must still define stored runtime stamps and full event-hook reads.
- `ExtensionRegistry.close_registry()` closes new admission and waits for borrowed reads to leave. Manager close requests cooperative preparation stop, waits for preparation and cleanup, and retains active or unpublished resources until they can be released. Preparation checks the stop event between controlled steps; an active installer or RPC keeps its own deadline. No fixed total shutdown deadline is claimed.
- `RetirementOwner` calls deactivation in reverse active order. Unresolved job IDs or a failed deactivation retain the owner. Retry calls only unresolved packages. A failed resource close remains an explicit uncertainty; a later no-op close cannot hide it. `ManagerSnapshot` exposes cleanup issues separately from stored lifecycle state. These observations are not durable health or job reconciliation.
- `app/provider_extension_registry.py` and `app/provider_extension_runtime.py` compose production services. `api/workers.py` opens and seeds the manager before engine construction. Request-only applications and standalone engine tests use an empty `RuntimeManager` and start no extension worker. Artifact storage, default package roots, private environments, and the native lock use the actual main database directory.
- Shutdown stops the engine before closing extension resources. `WorkerPlan.requires_join` makes a timed-out engine join an explicit failure which retains the manager. Other existing daemon workers retain their earlier timed-join behavior. Partial thread startup stops the threads already started. Normal manager shutdown preserves the stored selection for restart.
- The architecture rules admit exactly `extensions.manager_contract` in the engine and the new application repository provider. No extension implementation import is permitted in the engine. No feature-specific host branch, frontend wire change, or dead-code exception was added.

Verification:

- The manager, engine-boundary, and existing engine subset passed 26 checks. The real-daemon, provider, and controlled-preparation subset passed eight checks. The retirement and worker-shutdown subset passed seven checks. These focused groups overlap; do not add their counts as distinct cases.
- Two new real-daemon tests seed a committed worker selection in private data, then start `DashboardApplication` through the existing process fixture. Feature-owned markers prove that activation runs in a new process, not the test or daemon. They verify exclusive ownership, source-free restoration, repeated restart, deactivation, actual worker exit, and removal of the owned environment. These are host integration tests, not the future extension release E2E runner.
- Local tests cover non-blocking preparation, exact retry, busy readers, failed SQL commit, lost manager generation, unavailable scheduling, startup work retention, core fallback, read-drain timeout, cooperative stop, unresolved jobs, wrong deactivation revision, failed deactivation, failed close, engine join failure, and partial thread-start failure. Job results in these local tests are controlled protocol replies; no external write runs.
- Initial checks found an incomplete engine test double, two missing architecture entries, a fixture artifact-root mismatch, and an over-broad join requirement for the unrelated usage worker. The test double now includes the real interpreter's failure recorder. Architecture changes name only the approved provider and protocol. The fixture now uses the daemon's artifact root. The engine has the explicit join requirement; other worker policy is unchanged.
- `.venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,652 passed with 26 warnings in 106.07 seconds. This includes all 302 extension-host cases, with 41 new cases in this subset. The first full run passed 2,650 and failed the two architecture entries; the corrected focused and full runs passed. Verification used macOS, Python 3.12.1, and the dirty worktree over `6a9e497`. No commit was made.
- `make typecheck`: passed for 2,529 source files. Root Ruff, focused Wemake, shared policy parity, and `git diff --check` passed. The policy remains `baqylau-dev 0.1.0a1`, SHA-256 `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. Full dead-code checks report 19 missing application uses; full Wemake retains the same six unrelated findings in changed Codex files. Full lint is not green.
- No frontend wire model changed in this subset. The earlier 28 web-SDK and 114 dashboard unit results were not rerun here. Browser, live Kitty, live harness, and feature-owned release E2E remain separate open checks.

Limits: No lifecycle HTTP or settings mutation route exists. Complete request planning, settings migrations, durable health and failure policy, root query/command admission, source detach, removal notices, and durable job draining remain open. Cleanup currently retains unresolved old workers after publication; P05 must add write-job draining and reconciliation before replacement. P04 still must connect raw, canonical, and projection processing. C02–C04 are not complete. No live LaunchAgent, user database, Git remote, adapters service, Slack, Kitty, or feature-owned release E2E was used.

Next action: Add typed lifecycle request planning and read-only admission policy. Build enable, disable, reload, and settings proposals from host-owned catalog and state; include required dependents and revision conflicts. Add public operation and manager-state reads, then mutation routes with private-daemon tests. Complete durable health and removal notices, and coordinate write-job draining with P05. Keep the full P01–P10 scope open.

## Work record — User lifecycle controls

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: The daemon manager could restore and publish complete host proposals. It had no safe public request planner. Clients must not supply manager identity, complete runtime contents, or private settings for a lifecycle action.

Task: Add checked enable, disable, and reload requests, dependent previews, stable retries, extension write policy, and public state and operation routes. Test these through the real private daemon. This continues P03-T03 and P03-T04; it does not complete a phase.

Outcomes:

- `ExtensionLifecycleControl` is an explicit protocol. `LifecycleControl` reads the manager and catalog through contracts. `lifecycle_plan_reads.py` captures retained active manifests and current discovery. `lifecycle_planner.py` checks the complete dependency order and settings before admission. No worker call or database write occurs during preview.
- Enable and reload require the observed lifecycle revision, catalog revision, and exact discovered package digest. Enable requires an inactive target. Reload requires a committed active target. Missing or incompatible required providers are rejected; the host does not enable another package without its own request.
- Disable uses retained active manifests, even after source removal. The preview includes transitive required dependents in reverse active order. Mutation requires exact confirmation of those dependents, excluding the target. Optional consumers remain selected. A failed enable intent can be cleared through disable.
- `request_id` is a stable client key. The host derives operation and runtime IDs from it. `LifecycleProposal.request_origin` stores the exact target and body in the existing operation JSON. No schema migration is needed for this optional field. Exact replay returns the original operation before current-state planning, including after restart. Key reuse with another target or body is rejected. Concurrent replay can recover the operation admitted after the first lookup.
- `ExtensionControlPolicy` and `ApplicationConfig.extension_read_only` provide extension-only write admission. `BAQYLAU_EXTENSION_READ_ONLY` accepts exactly `0` or `1`, with `0` as default. Mutation and rescan check the policy; preview and reads remain available. Startup discovery and restore still run. No unrelated application control changes policy.
- `ManagerSnapshot.directory` reports the metadata of the owned active runtime. HTTP responses keep it separate from committed package references and requested state. State and operation reads omit settings documents, complete proposals, and private manager identity. A bounded host failure is not a feature log dump.
- `api/extensions/lifecycle_routes.py` adds runtime state, operation reads, preview, and lifecycle changes. Preview returns 200. New and replayed admission return 202. Invalid input returns 400, read-only or foreign Origin returns 403, missing operations return 404, conflicts return 409, non-JSON media type returns 415, and missing manager ownership returns 503. Acceptance is not completion.
- API models, enums, mappers, and guards remain in the API layer. `LifecycleChangeRequest` converts only the JSON array container for dependent IDs; other values remain strict. The test SDK uses only API contracts. It supplies state, operation, preview, and change methods plus a preview request helper. A request-only app resolves its providers without creating workers.
- Dashboard OpenAPI types were generated from the local application schema without a live HTTP server. No settings page, package asset route, web view host, or Kitty view was added. Feature code remains outside the main application.

Verification:

- Added 53 host cases. They cover pure preview, valid and invalid target states, selected digests, stale catalogs, explicit dependency confirmation, optional preservation, source removal, exact retries, concurrent retries, restart, read-only admission, strict JSON, private-value omission, and API enum parity.
- Real private-daemon cases cover enable, disable, reload, re-enable, source-free restart, operation lookup, failed enable, dependent removal, and read-only controls. Worker markers prove actual separate-process activation and release. A failed reload leaves the old active worker available. Tests use no live user database, service, or Git remote.
- The first full run passed 2,696 and failed six checks. Four failures identified API layer, naming, and vocabulary violations. The SDK now imports API models, preview returns an API-owned model, the route parameter has its full type name, and response vocabularies use enums. The request-only read test now validates the declared 503 error; private-daemon tests validate successful state. The frontend build stamp was refreshed after the build. Architecture rules were not weakened for these corrections.
- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,705 passed, 26 warnings, 141.43 seconds. This includes all 355 extension-host cases. Verification used macOS, Python 3.12.1, and the dirty worktree over `6a9e497`. No commit was made. The direct installed command-line tools were used because the system tool shims reported an Xcode licence requirement; no licence or system setting was changed.
- Strict types passed for 2,562 source files. Root Ruff, focused Wemake, policy parity, and `git diff --check` passed. The policy remains `baqylau-dev 0.1.0a1`, SHA-256 `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`. Two new test style findings and a missing set annotation were corrected before the final focused checks.
- The dead-code gate still reports 14 missing application uses: cleanup retry, core mappers, projection capture, translation candidates, and retained runtime reads. Exact HTTP response fields and enum members have framework roots in `vulture_allowlist.py`; real HTTP and host-vocabulary parity tests verify them. No unused host service was exempted. Full Wemake retains six unrelated findings in the changed Codex files. Full lint is not green.
- Dashboard `npm run check`, `npm run lint`, `npm run test`, and `npm run build` passed. There are 114 dashboard unit tests. Browser, live Kitty, live harness, and the feature-owned release E2E runner were not run for this subset. No new feature E2E completion is claimed.

Limits: Settings mutation, related-scope resolution, secret references, settings migrations, durable health, automatic failure policy, removal notices, root queries, source attachment, and durable job draining remain open. Cleanup retains unresolved old workers after publication; P05 must coordinate writes before replacement. P04–P10 remain required. C02–C04 and C16 have more evidence, but their full acceptance conditions are not complete. The runtime is for trusted local packages, not untrusted code.

Next action: Add the checked settings service and public read/change models through the same manager path. For enabled packages, select the retained active digest; a settings change must not silently reload newer source. Resolve defaults separately from installation and exact scope overrides. Check lifecycle, catalog, package, and owner settings revisions. Do not save a changed override before successful runtime publication. Cover stale writes, reset, disabled packages, restart, invalid documents, and failed preparation. Keep secret values outside public responses and snapshots; complete candidate migrations before claiming upgrade support. Then continue health, removal notices, and the full P01–P10 scope.

## Work record — Ordinary settings controls

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: Schema 28 could commit raw settings with a runtime, but users had no settings request service. A settings edit must not select newer package bytes, expose pending values, or lose exact retry identity. This work continues P03-T01 and P03-T04. It supplies a dependency for P06, not a dashboard settings page.

Task: Add an explicit settings protocol, accepted-value reads, checked scope replacement and reset, public HTTP routes, and typed SDK access. Reuse normal runtime preparation and durable publication. Test real private workers and failure recovery.

Outcomes:

- `extensions/settings_control_contract.py` defines `ExtensionSettingsControl`. `SettingsControl` explicitly implements it. The service receives manager and catalog contracts. Selection, document checks, read views, and candidate planning have separate modules. The application provider creates no manager or worker as an HTTP dependency.
- `SettingsReadRequest`, `SettingsRequest`, and `SettingsSnapshot` keep scope, package, values, and revisions explicit. The read returns the selected declaration, bundled schemas, effective document, and only that scope's explicit override. Pending changes do not replace accepted values.
- Enabled targets use the retained committed manifest and digest, including after source removal or a rescan. Inactive targets use one valid current catalog entry. A different digest cannot turn a settings edit into a package reload.
- Each write carries lifecycle, catalog, package, and owner settings selections. It changes one complete document. Explicit null resets that scope; an omitted document is invalid. Exact scope values override the installation value, which overrides the manifest default. Other raw overrides remain unchanged. The host does not merge arbitrary JSON fields.
- A disabled owner can save values without being enabled. The complete runtime candidate retains selected package order and all other owners' captured settings. The current manager prepares fresh workers for the full active set on every accepted settings operation, including an edit to an inactive owner. The stored commit publishes the new settings and runtime together. Failure preserves both prior values and prior workers.
- `ControlRevisions` and `control_admission.submit_request()` serve lifecycle and settings requests. `ManagementRequestOrigin` stores either strict request shape in the existing operation JSON. Exact retries use one operation before planning, including after restart and during concurrent admission. A settings request key cannot be reused for another lifecycle action. Existing lifecycle records remain readable; no DDL change was needed.
- Candidate validation failures become bounded request errors before manager admission. The 1,000-scope and 8 MiB complete-operation limits do not become internal HTTP failures. Error text omits model input values. Edits and resets of existing scopes remain possible at the limit. The helper does not catch validation errors from manager execution as if they were user errors.
- `GET /api/extensions/{extension_id}/settings` returns accepted ordinary settings and write policy with `Cache-Control: no-store`. Scope defaults to installation. The optional scope query is public-model JSON with a 16,384-character bound. The optional digest pins the expected package.
- `PUT` on the same path uses strict `SettingsChangeRequest`, JSON media checks, same-origin checks, and extension-only read-only policy. It returns 202 with the normal operation reference. Poll completion before treating values as saved. Invalid requests return 400; stale selections return 409; missing manager ownership returns 503. State and operation reads still omit settings documents.
- `sdk.client_extension_settings.py` provides `client.extensions.settings.read()` and `.change()`. It imports public API and SDK models only. HTTPX encodes query parameters. The dashboard OpenAPI types were generated offline. No feature-specific host import, dashboard form, asset route, or Kitty view was added.

Verification:

- Added 50 host cases: 45 settings cases and five candidate-limit cases. They cover defaults, installation and exact scope values, reset, disabled owners, immutable package selection, invalid schemas, three revision conflicts, strict input, shared request keys, concurrent retry, restart, read-only policy, and accepted-value reads during preparation.
- Private-daemon HTTP tests use the typed SDK. External worker markers contain the actual activation request. They prove settings delivery to a new worker, old process exit after success, and old worker survival after failed activation. The failed candidate exits. Its private failure text stays out of public operation responses.
- The full scope test seeds exactly 1,000 overrides through the actual manager and repository. It then uses the normal settings service to reject a new scope and accept replacement or reset. Two operation-size tests reduce the model bound to exercise rejection before admission. They are not performance evidence at 8 MiB. The full lifecycle state remains unchanged after rejection.
- Initial checks found an unsupported FastAPI JSON-union query declaration and a raw tuple record in SDK query encoding. The query now uses the public Pydantic scope adapter; the SDK uses HTTPX `QueryParams`. The first full run passed 2,749 tests and failed the raw-record architecture check. The affected nine-case check then passed. No architecture rule was weakened.
- The corrected full run passed 2,750 tests with 26 warnings in 155.32 seconds. It included all 400 extension-host cases before the five limit cases were added. The limit cases then passed separately. The final `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e` run passed 2,755 tests with 26 warnings in 126.55 seconds. This includes all 405 extension-host cases.
- `.venv/bin/python -m baqylau_dev check --gate types` passed for 2,594 source files. Root Ruff, focused Wemake, policy parity, and dashboard `npm run check`, `npm run lint`, `npm run format:check`, and `npm run test` passed. The dashboard has 114 unit tests. The production dashboard build and its stamp were refreshed after wire generation.
- `git diff --check` passed. The documentation check verified 10 phases, 53 unique tasks, required task fields, dependency IDs, and 72 local links. Status remains three phases in progress and seven not started; tasks remain 15 in progress, one done, and 37 not started. A process check found no remaining SDK worker after tests.
- Full lint remains incomplete: 14 extension methods or functions need application callers, and six unrelated Codex Wemake findings remain. `SettingsSnapshot.lifecycle_revision` has one narrow serialized-response root, checked by the settings client tests. No unused host service was exempted. The shared policy remains `baqylau-dev 0.1.0a1`, digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`.
- Verification uses macOS, Python 3.12.1, and the dirty worktree over `6a9e497`. Tests use private roots and databases. The installed command-line tools supply Git because the system shim requires an Xcode licence. No licence, system setting, live LaunchAgent, user database, Git remote, or external service was changed. No commit was made.

Limits: These are ordinary schema-checked settings, not secret storage. An arbitrary credential placed in an ordinary document is not detected or redacted. Related workspace/session/repository inheritance, credential references and resolution, settings schema migrations, history rebuild effects, and custom forms remain open. Runtime health, removal notices, root queries, and durable job draining also remain open. Browser, live Kitty, live harness, and feature-owned release E2E were not run for this subset. C02–C05 are not complete. No phase or additional subtask is marked done.

Next action: Add candidate settings migration through the declared SDK migration protocol. Capture old raw choices and schemas, convert only on a prepared candidate, and publish converted values with the new runtime. Reject missing paths, failed or partial conversion, and stale source state without changing accepted settings or workers. Do not infer reverse conversions. Coordinate this with P05-T05 before claiming upgrade support. Then finish related-scope resolution and credential services, health and removal behavior, and the remaining P04–P10 work. The adapters and Git feature packages remain in scope.

## Work record — Candidate settings migration

Status: in_progress

Owner: Codex

Date: 2026-09-15

Context: Ordinary settings controls required every value to match the selected package before admission. A package with a new settings schema could not prepare a conversion. The host must retain exact old choices without pretending that the target values already exist. This work continues P03-T01/T04 and starts the settings subset of P05-T05. Record migration still needs P04 and P05-T01–T04.

Task: Capture a typed migration input, execute the existing pure migration protocol before activation, and commit only a complete checked result with the runtime. Keep old values and workers available on failure. Verify real worker, HTTP, storage, and schema upgrade behavior.

Outcomes:

- `extensions/models/runtime_candidates.py` defines `RuntimeCandidate`, `MigratingRuntimeSelection`, and `MigratingRuntimePackage`. Complete requests retain the existing `RuntimeSelection`. A migrating package contains exact raw source overrides, not placeholder target settings. The committed-state type remains `RuntimeSelection` only.
- The lifecycle planner selects migration by exact schema identity in explicit overrides. It checks source documents, retained schemas, declared scopes, and exact migration paths without a worker. Admission compares the full source with accepted storage. Missing paths are rejected before preparation. No implicit forward, reverse, or intermediate conversion is selected.
- `runtime_plan.py` checks all fixed artifacts before starting workers. `RuntimePackagePreparation` owns package preparation, migration, and activation. `runtime_settings_migration.py` calls the existing `ExtensionMigrations.migrate_settings` protocol. Both the host and worker validate the request and complete reply, including scope, runtime, candidate, call identity, source revision, and target schema. Host code does not decode feature JSON or import feature modules.
- The same new worker activates with the converted installation value. Its registry entry receives complete scoped settings. Only explicit overrides are converted. Their scope order and inheritance remain unchanged. A package with no explicit values uses its new default without conversion. A successful conversion advances the owner settings revision once.
- `RuntimeResolution` binds complete ready settings and raw changes to the reserved plan. It has an 8 MiB encoded bound. It cannot change runtime or catalog identity, selected packages, order, unaffected values, or override scopes. Ordinary complete requests cannot accept an unrequested resolution. An incomplete migration cannot become a committed runtime.
- Schema 29 adds `extension_runtime_resolutions`, keyed by a foreign key to the reserved runtime. The original `selection` and operation proposal remain unchanged. Only successful completion inserts a result. Earlier schema-28 selections need no result row. No table is deleted or rebuilt by this schema change.
- `StoredRegistryCommit` carries the prepared resolution through the existing publication boundary. Repository completion rechecks source values and revisions, target schemas, raw/effective agreement, and the exact plan. It commits the result, raw settings, operation outcome, and head in one transaction. A failed COMMIT leaves the same candidate available for retry.
- Pending conversion and busy publication leave accepted reads unchanged. Failure closes candidate resources and keeps old settings and workers. Exact retries retain one request and result. Restart interrupts incomplete conversion and restores the last complete runtime. A completed conversion restores from retained bytes without repeating migration.
- Existing HTTP routes are sufficient: enable or reload can require migration, return 202, and expose its normal operation status. Public state and operation responses omit source and converted documents. The ordinary settings GET returns only accepted values. No frontend wire model, dashboard source, or public SDK capability was changed.

Verification:

- Added 23 host cases: five runtime cases, eight stored-result cases, three rollback/schema cases, one independent schema-upgrade case, two HTTP cases, and four selection/publication cases. These counts are distinct. Fixtures keep backend source, schemas, dependencies, and feature markers outside the checkout. They are host integration tests, not the future package-owned release runner.
- Real workers prove successful conversion before activation, workspace inheritance, explicit reverse conversion, invalid output, explicit refusal, forbidden live callbacks, prior worker survival, and failed candidate exit. Tests also reject a missing settings path while the package retains valid record migration paths. A busy registry holds converted values outside accepted reads.
- Real private-daemon HTTP tests cover successful and failed upgrades, exact retry, source-free restart, private-value omission from operation responses, and actual activation values. A retained feature process marker checks that restart does not call conversion again.
- Repository tests reject missing results, changed runtime/catalog/package sets, lost overrides, stale manager results, inconsistent raw/effective values, and schema-invalid target values. The schema-invalid case changes both raw and effective values consistently so the actual target schema check must reject it. COMMIT failure rolls back settings, result, outcome, and head; retry then succeeds. Tests compare the original stored request bytes after success.
- `fixtures/extension-lifecycle-schema-28.sql` is an independent pre-resolution DDL fixture. With the schema-26 and catalog-27 fixtures, it creates a populated schema-28 database. Upgrade retains the prior operation, runtime, settings, and revision, and creates an empty result table. Existing earlier-schema tests also pass.
- The first full run passed 2,749 tests and failed 22. One failure found a parameter naming rule. The other failures came from old test fixtures that retain new columns while lowering the stored version. The initial result-column design was replaced with the separate input/result table design. This keeps the original table unchanged and requires no weakened migration rule or edit to those older fixtures.
- `PATH=/Library/Developer/CommandLineTools/usr/bin:$PATH .venv/bin/python -m pytest -q -m 'not kitty' -n 6 --ignore=tests/e2e`: 2,778 passed, 26 warnings, 179.87 seconds. This includes all 428 extension-host cases. The independent focused architecture group passed 31 checks. The corrected migration/naming group passed 36 checks; these groups overlap the full result.
- Strict types passed for 2,609 source files. Root Ruff, focused Wemake, shared policy parity, and `git diff --check` passed. After adding the explicit unchanged-marker and stored-byte assertions, the HTTP and stored-result group passed all 10 cases in 23.57 seconds; types and focused lint passed again. The policy remains `baqylau-dev 0.1.0a1`, digest `b2c10030a1d1b2407bb2407d85a852d90c3fdd15992bf764116d64e1a69dce72`.
- The documentation check verified 10 phases, 53 unique tasks, required fields, dependency IDs, and 75 local links. Four phases and 16 tasks are in progress; one task is done and 36 are not started. No SDK worker remained after tests. Verification used macOS, Python 3.12.1, and the dirty worktree over `6a9e497`; no commit was made. The installed command-line tools supplied Git without changing the Xcode licence or system settings.
- Full lint still reports the same 14 missing application uses and six unrelated Codex Wemake findings. No dead-code exception was added. This subset changes no frontend source or wire model; the previous 114 dashboard unit tests and build evidence were not rerun here. Browser, live Kitty, live harness, and feature-owned release checks remain open.

Limits: This is ordinary settings migration, not record migration or a complete data rollback system. Secret references, related-scope inheritance, health policy, removal notices, and write-job draining remain open. Conversion uses the normal per-call deadline and checks stop between calls. Total migration time, large-output performance, disk power loss, and live feature release behavior are not proven. No phase or additional subtask is complete. P05-T05 is now in progress because its settings implementation has started.

Next action: Begin P04-T01 scoped raw and canonical storage using the verified runtime publication boundary. Preserve original bytes, core identities, and existing visible order. P03 health, removal, related-scope settings, and credential work remain required; job draining and record migration must join P05 storage and execution. Do not report those dependencies as complete. Continue the full P01–P10 scope, including adapters and Git. No live daemon, user database, external service, or Git remote was changed in this work.
