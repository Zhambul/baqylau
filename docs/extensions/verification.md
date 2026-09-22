# Test design and acceptance checks

Status: proposed

## Test ownership

Current management and cleanup update: The final Python run passes 3,259 tests with 26 warnings in 261.83 seconds, using six workers and the normal non-Kitty/non-live selection. Dashboard and web SDK gates pass with 134 and 28 unit tests. All 54 browser cases passed in Chromium and WebKit; the 10 actual-daemon management cases passed again on the final build. The approved cleanup change stores observations before physical close and lease release. Tests retain exact unresolved job IDs across restart without inventing job outcomes. The original three-worker exit case passes with process and environment closure checks. See the [management work record](phases/06-web-and-settings.md#work-record--dashboard-management) and [cleanup verification](worker-cleanup.md#verification-result). Earlier counts below describe their original work steps.

The main repository owns runtime, protocol, storage, UI host, and conformance tests. Each external extension owns feature tests and E2E scenarios. The shared test kit supplies fixtures and public API access. Product-specific parsers and assertions stay in the feature package.

Tests start `api.runtime.DashboardApplication` through the existing isolated process path. The SDK supplies typed reads and controls. Port 8377, normal data directories, real Slack writes, and external Git remotes are not used by repeatable tests.

White-box unit and repository tests may inspect storage. E2E tests use public typed APIs and observable web or terminal behavior. Extend the diagnostic API so E2E can check drops, transform failures, pending jobs, cursor progress, and active revisions without reading a database.

## Conformance matrix

Use these case IDs in task verification and feature test metadata:

| ID | Setup and action | Required result |
| --- | --- | --- |
| C01 | Install an external built package into a private extension root | Host discovers it without importing backend code in the daemon. |
| C02 | Enable, disable, re-enable, and restart the private daemon | Settings persist; actual state matches the completed request; resources do not duplicate. |
| C03 | Reload a new package while events arrive | Each batch uses one runtime revision; old replies are rejected; no missing original input. |
| C04 | Fail package validation, preparation, or settings migration | Prior active package and data remain usable. |
| C05 | Change settings from two browser clients | The stale write is rejected; all clients receive the accepted revision. |
| C06 | Return keep, replace, drop, and insert at each transform stage | Exact expected output and ordered audit decisions; originals remain readable. |
| C07 | Repeat input and retry after a crash | Logical facts, entries, source positions, and command jobs do not duplicate. |
| C08 | Suppress every event in a batch | Processing reaches the next input; drop is an intentional decision. |
| C09 | Return an invalid schema, identity, scope, or lifecycle reference | Full invalid extension result is discarded; valid input continues; diagnostic names the failure. |
| C10 | Hang, crash, or flood a worker | Deadline and output limits apply; core processing continues; cleanup is visible. |
| C11 | Run a slow extension command while its transforms receive events | Transform delivery and transport callbacks continue. |
| C12 | Rebuild derived state twice from the same facts | Equal content and logical IDs; no observers or external actions run. |
| C13 | Build and switch a canonical history candidate | Prior history remains readable; candidate is validated; clients reset to its revision. |
| C14 | Disable or remove an extension with stored history | Host decodes history and shows a generic fallback without worker code. |
| C15 | Run each of two extensions alone and together in both activation orders | Base features work alone; cooperation works when the declared service is active. |
| C16 | Remove a required or optional dependency | Required dependents stop consistently; optional consumers lose only that integration. |
| C17 | Introduce dependency, service-call, or generated-event cycles | Host reports the cycle or bound violation and continues unrelated work. |
| C18 | Load an extension web view from installed assets | No host source edit or build; correct route, data, style, and action behavior. |
| C19 | Disable during web mount, query, or navigation | Late results are disposed or ignored; no stale actions or subscriptions remain. |
| C20 | Open a Kitty extension pane, resize, act, reload, and disable | Correct screen content, wrapping, focus, cleanup, and command scope. |
| C21 | Use a repository extension without an active session | Repository data and commands work without a synthetic session. |
| C22 | Lose a command reply after an external write | Reconciliation reports the result or `outcome_unknown`; it does not repeat the write. |
| C23 | Supply a stale repository or projection revision to a write | Reject stale work or recompute before execution; do not apply an obsolete selection. |
| C24 | Attempt a write in read-only mode or call a disabled contribution | Standard host policy rejects the operation. |
| C25 | Request an undeclared asset or escape the package path | Asset handler rejects it; a declared asset is served with correct type and cache identity. |
| C26 | Run a package outside a host checkout with only installed SDKs | Build, lint, test, and E2E work with declared dependencies only. |
| C27 | Place a known type, lint, design, dead-code, or coverage failure in a fixture package | The corresponding shared gate fails. |
| C28 | Change an extension's web and terminal code, then rebuild and reload it | Main source and dashboard artifact hashes remain unchanged. |

## Fixture packages

Create small external fixture packages through the SDK template. Keep their source and installed output separate from the host production package. Host test fixtures may contain fixture source, but tests must build and install it in a temporary external root.

Use one transformer fixture with all four data operations, one view fixture, and one cooperating service fixture. Add explicit modes for timeout, crash, invalid output, stale replies, duplicate identities, and failed cleanup. A fixture must fail because the host observed the test condition, not because its declaration was invalid before the test started.

Build a second clean package in a temporary repository with no host root on `PYTHONPATH` or TypeScript aliases. This is the independence check. Run its own test entry points from the installed package metadata.

## E2E declarations

Each scenario declares a stable ID, feature IDs, surfaces, harnesses, dependencies, and whether it needs live credentials. Validate these declarations against the manifest. Data-only scenarios can declare no frontend. A web or Kitty feature needs a scenario that checks that surface.

Reuse the existing discovered harness list. A scenario with a harness limit must record the exact limit and reason. Do not silently remove a harness row to make the test pass.

Use deterministic CLI fixtures for ordinary adapters tests. Use actual temporary Git repositories and a local bare remote for Git tests. Live adapters and harness tests are separate checks for format changes. Do not report fixture coverage as proof of a live service contract.

## Browser and terminal checks

Browser E2E builds the main production bundle once, then installs independent extension bundles. Chromium and WebKit check routes, paging, reconnect, settings, keyboard access, errors, and image or stylesheet loading. Use host snapshots plus extension-owned view snapshots where layout is part of the feature.

Terminal unit checks cover block validation and width calculations. Live Kitty E2E checks the actual pane screen and user actions. The shared Kitty instance requires serial execution, as in the existing suite. Use private application data and restore the test's windows after the case.

## Performance evidence

Record a fixed representative input capture and its digest. Measure the host with zero extensions, one no-op extension, and two active extensions. Record event throughput, input-to-display delay, queue depth, process memory, and idle wakeups.

Repeat the measurement with a slow command, an unresponsive worker, and a large output stream. Choose processing deadlines and queue limits from those results. Add regression limits only after a baseline is measured. Store the machine and runtime versions with the report.

## Task evidence format

Each completed task records:

- Commit or worktree revision and changed paths.
- Exact commands and exit results.
- Test case IDs and relevant output or artifact paths.
- Platform, host API version, package digest, and quality policy release.
- Checks that were not run, the reason, and the remaining release gate.

Documentation checks for this plan validate files, links, task IDs, dependency IDs, required fields, and status values. They do not count as implementation tests.

## Current host discovery evidence

The [P03 catalog record](phases/03-runtime.md#work-record--application-discovery-and-catalog), [capture record](phases/03-runtime.md#work-record--fixed-package-capture), [environment record](phases/03-runtime.md#work-record--private-python-environments), [worker record](phases/03-runtime.md#work-record--managed-private-workers), [registry record](phases/03-runtime.md#work-record--active-registry-and-peer-reads), [lifecycle record](phases/03-runtime.md#work-record--durable-lifecycle-state), [joined commit record](phases/03-runtime.md#work-record--prepared-runtime-and-joined-commit), and [manager record](phases/03-runtime.md#work-record--manager-and-daemon-ownership) have the earlier commands and limits. The [user-control record](phases/03-runtime.md#work-record--user-lifecycle-controls) adds the public lifecycle API. The latest [settings record](phases/03-runtime.md#work-record--ordinary-settings-controls) adds ordinary reads and checked scope changes. The real private-daemon cases cover catalog reads, rescan, captured packages, enable, disable, reload, settings, dependent confirmation, exact retry, source-free restart, read-only admission, worker failure, and shutdown. These results do not complete C02–C05 or any frontend feature case.

The discovery fixture writes a valid package layout outside the checkout and a backend import marker. Its E2E entry is only an existing file for manifest validation. Do not report that file as an executed feature-owned E2E scenario. Separate worker fixtures now execute copied SDK example code from real private environments. They check flat and `src/` layouts, callbacks, lifecycle and transform calls, changed mutable source, failed starts, runtime failure, shutdown, and retained bounded evidence. The daemon now owns the active-set manager. Public lifecycle and ordinary settings requests now exist. Related-scope settings, secrets, migrations, durable health, and the package-owned release runner remain required in P03 and P08.

Private environment tests build a fresh SDK wheel with setuptools. They use the standard wheel packer to make offline test wheels from installed mandatory dependencies. The dependency graph comes from the fresh SDK build metadata, not possibly stale editable-install metadata. This makes ordinary tests independent of a network or shared download cache. These repacked test dependencies are not published release artifacts; release wheelhouse creation and target-platform tests remain P02/P08 work.

The schema migration uses the complete schema-26 DDL from committed revision `6a9e497`, with existing data. Failure injection tests transaction rollback after catalog row replacement. Concurrent scans test revision comparison inside the real SQLite transaction. No test uses the user's live database or restarts the normal LaunchAgent.

The registry adds 35 host cases. Local protocol doubles check stable dependency order, required-dependent removal, inactive metadata, captured settings, invalid identities, nested reader release, concurrent publication, stale revisions, runtime ID reuse, and query authority. Three cases run the production worker factory with independent captured packages and private environments. They prove service reads in both preparation orders and an optional consumer alone. A held-query case uses explicit thread events to prove publication stays busy through the complete peer call. The later user-control subset tests required removal as a daemon operation. Runtime restart recovery also has later evidence. Removal notices and product-owned E2E remain open.

Lifecycle storage adds 36 host cases. They use actual SQLite files to test concurrent admission, replay, stale state, reserved runtime IDs, manager replacement, late completion, failed reload, settings commit and reset, default inheritance, disabled-package settings, and rollback after partial writes. An independent schema-27 fixture is assembled from the stored schema-26 fixture plus the catalog-only schema-27 DDL. It retains populated core and catalog rows through upgrade. One size-limit test reduces the bound to prove admission revalidation; it is not a performance measurement at the 8 MiB release bound. These tests explicitly submit storage completion without a worker, so they do not prove daemon activation or joined registry/database atomicity.

The manager and daemon subset adds 41 host cases. They exercise asynchronous preparation, exact retries, busy readers, failed SQL commit, retained candidates, lost manager generations, stopped preparation, startup work retention, registry closure, unresolved cleanup, and thread startup/shutdown order. Two cases use the real daemon and external worker markers to prove restoration, source-free restart, deactivation, process exit, and released process ownership. The final full Python run passed 2,652 tests. Strict types passed for 2,529 files. Full lint still reports 19 missing application uses and six unrelated Wemake findings. See the manager record for commands, failed first checks, corrections, and remaining limits.

Complete runtime preparation and joined commit add a separate set of tests under `tests/extension_host/test_runtime_*.py`. These use the production preparer, worker factory, artifact store, registry, and lifecycle repository. They check cooperating external packages after a stored commit, failed preparation with the old set still available, a ready candidate held across a busy publication, immutable captured source, empty and web-only candidates, and rejected activation. They are private process integration tests; a test helper performs the manager steps explicitly. They do not complete daemon enable/disable or feature-owned release E2E.

The joined commit cases inject a failure from the actual SQLite connection's COMMIT method and verify rollback plus retry. A held commit adapter proves that a new reader cannot enter between the database commit and pointer replacement. Separate processes exit without cleanup on both sides of commit. Restart reads and claims the resulting stored state. Native ownership tests cover concurrent threads and processes, stable lock-file identity, missing native support, forked copies, and process death. They do not test disk power loss, a network filesystem, or orphan worker recovery. See the [prepared runtime and commit record](phases/03-runtime.md#work-record--prepared-runtime-and-joined-commit) for exact counts and remaining work.

## Current lifecycle HTTP evidence

The user-control subset adds 53 host cases and raises the full Python result to 2,705 passed. The [P03 record](phases/03-runtime.md#work-record--user-lifecycle-controls) gives commands, corrections, limits, and next work. Strict types pass for 2,562 source files. Root Ruff, focused Wemake, and policy parity pass. Dashboard types, lint, 114 unit tests, and the production build pass.

The tests check public API bytes through the typed SDK and real private daemons. They distinguish admission from completion, require dependent confirmation, reject stale or malformed requests, retain exact request identity across restart, and keep private settings out of state and operation reads. A private backend process proves activation and release. Failed reload keeps the old active runtime.

The general request-only HTTP fixture validates the declared unavailable error for runtime state; it does not start a fake manager or skip that route. Real daemon tests validate successful state. The test SDK imports API models only. API enum parity checks cover each host state and generated enum value.

Full lint is still incomplete. Fourteen extension methods or functions need application callers, and six unrelated Codex Wemake findings remain. Dynamic HTTP fields and enum members have narrow framework roots, not missing-service exemptions. Browser, real Kitty, live harness, and complete feature-owned release checks remain open. No phase or additional subtask is complete.

## Current settings HTTP evidence

The settings subset adds 50 host cases. The [P03 settings record](phases/03-runtime.md#work-record--ordinary-settings-controls) records implementation paths, commands, corrected failures, and limits. The final full run passed 2,755 tests, including all 405 extension-host cases. Strict types pass for 2,594 source files.

Tests cover accepted default and override reads, exact scope reset, stale forms, disabled-owner storage, source removal, digest pinning, shared request identity, concurrent retry, and restart. Real private workers receive captured settings. A rejected activation leaves prior values and the old worker available. State and operation responses omit documents. Successful settings GET uses `Cache-Control: no-store`.

The scope-bound test uses 1,000 actual saved overrides, then attempts an additional scope through the service. It checks unchanged durable state and no admitted operation. Separate cases replace and reset at the limit. Operation-bound tests use a reduced byte limit, not a release-size performance claim. Both lifecycle and settings planners use the same bounded request-error path.

Strict types, root Ruff, focused Wemake, policy parity, and dashboard types, lint, formatting, 114 unit tests, and production build checks pass for the settings subset. The same 14 missing application uses and six unrelated Codex Wemake findings prevent full lint completion. Secret storage, related-scope inheritance, migrations, browser forms, Kitty, live harness, and feature-owned release E2E remain open. Ordinary settings do not redact arbitrary secrets placed in their documents.

## Current candidate settings migration evidence

The [P03 migration record](phases/03-runtime.md#work-record--candidate-settings-migration) adds 23 host cases. The full Python run passes 2,778 tests, including all 428 extension-host cases. Strict types pass for 2,609 files. Root Ruff, focused Wemake, shared policy parity, and diff checks pass. Full lint retains the same 14 missing application uses and six unrelated Codex Wemake findings.

Real workers and private-daemon HTTP tests prove conversion before activation, complete scoped output, inherited defaults, explicit reverse paths, missing-path rejection, failure retention, busy publication, exact retry, and source-free restart. An unchanged feature migration marker checks that restart does not repeat conversion. No public operation or runtime response contains the source or converted settings documents.

Repository cases verify immutable request bytes, complete result requirements, exact runtime/catalog/package identities, retained override scopes, raw/effective agreement, target schema validation, old-manager fencing, and rollback after COMMIT failure. The schema-28 fixture is independent of the new schema definition. Upgrade preserves populated lifecycle records and creates the separate result table without changing the original runtime table.

The first full run found one naming error and 21 failures in older simulated-schema tests. The corrected design uses a separate `extension_runtime_resolutions` table instead of adding a nullable result column to the original input table. Earlier fixtures and their migration assertions were not weakened. The corrected focused and full runs pass.

This backend-only subset does not rerun the earlier dashboard checks or prove browser, Kitty, live harness, feature-owned release, record migration, history rollback, secret handling, or related-scope inheritance. P05-T05 is in progress for its settings subset. The remaining P01–P10 scope stays open, with no complete phase.

## Current event migration safety evidence

The [P04 migration record](phases/04-events.md#work-record--migration-transaction-safety) starts P04-T01. It adds 18 SQLite regression cases. All 12 initial cases failed before the correction. The corrected runner locks before reading the version and commits the complete pending migration chain together.

Tests compare full logical dumps after schema and data failures. They check actual deferred foreign-key failure at COMMIT, retry without manual repair, and two concurrent initializers with explicit barriers. Main-schema tests retain compressed raw bytes, canonical cursor order, repeated observations, interpretation links, ignored and pending input, sessions, and shell-output follow records. These are real SQLite and repository tests, not extension transform or process-kill tests.

At this step, the full Python suite passed 2,796 cases. Strict types passed for 2,616 files. Root Ruff, focused Wemake, and shared policy parity passed. The same 14 missing application uses and six unrelated Wemake findings remained. The migration-safety subset kept schema 29. It added no event table, host transform consumer, browser feature, or Kitty feature. No phase or additional subtask was marked done.

## Current scoped observation evidence

The [P04 storage record](phases/04-events.md#work-record--scoped-original-observations) adds schema 30, the explicit observation repository protocol, and mixed original-input storage. It adds 62 extension-host cases and one schema architecture case. The final full suite passes 2,859 tests, including all 490 extension-host cases. Strict types pass for 2,633 files. Root Ruff, focused Wemake, and shared policy parity pass.

Tests verify exact original bytes, scoped IDs, one arrival order and pending queue, manager/runtime rejection, retained first capture, invalid document rejection, rollback, and indexed scope reads. An independent populated schema-29 fixture survives the actual upgrade. Failure after each of its 28 statements retains the complete old logical database and permits a clean retry. SQL checks keep core identity fields mandatory and extension core fields absent. A negative architecture case proves that moving raw DDL into a named constant does not remove it from the table check.

The external fixture is a retained package declaration with a committed runtime selection. It is not a running source worker or a package-owned E2E result. Source checkpoints, daemon source registration, mixed interpreter dispatch, canonical history, transforms, public mixed audits, browser, and Kitty remain open. Full lint has 22 missing-application-use findings and the same six unrelated Wemake findings. No additional phase or subtask is done, and no live database or daemon was changed.

## Current canonical history evidence

The [P04 history record](phases/04-events.md#work-record--canonical-history-storage-and-live-isolation) adds schema 31. Canonical facts and interpretation links have history-aware identity. Existing rows move to the default history. Current core reads, audits, diagnostics, observation causes, and both session lifecycle triggers exclude candidate facts. The core schema and extension SQL branch stay separate.

The new set has 64 cases. Together with the earlier observation and table checks, 127 focused tests pass. Tests preserve old IDs, bytes, source links, ordering, deleted cursor limits, and session state. Every one of the 33 migration statements has a rollback case. A separate deferred foreign-key failure checks actual COMMIT rollback. Old forensic reads do not migrate the database. Intentional suppression gets a complete verdict and is not a diagnostic failure.

Final full verification passes 2,923 tests, including all 554 extension-host cases. Strict types pass for 2,641 source files. Root Ruff, focused Wemake, and shared policy parity pass. Full lint retains the same 22 missing application uses and six unrelated Codex Wemake findings. The Python run excludes Kitty and `tests/e2e`. This does not complete the mixed canonical writer, interpretation steps, transforms, runtime capture, source checkpoints, history publication, or application E2E. No phase or additional subtask is done.

## Current mixed interpretation evidence

The [P04 interpretation record](phases/04-events.md#work-record--mixed-interpretation-storage) adds schema 32 and the explicit `InterpretationRepository` protocol. Complete writes bind actual raw input and retained runtime state. The journal keeps ordered requests, applied or failed replies, intermediate facts, and later proposals. Facts, source links, verdict, decoder state, and live pending changes form one transaction.

The focused set passes 53 new cases: 52 extension-host cases and one mapper architecture case. The combined storage and affected architecture set passes 67 cases. Checks cover raw and canonical changes, drops, additions, failure fallback, first acceptance, exact retries, empty verdicts, stale runtime and state, core output, mixed order, and replay isolation. Failure at each write and COMMIT preserves the full logical database. A populated independent schema-31 fixture survives upgrade; all four actual migration statements and a deferred COMMIT failure have rollback cases.

Final full verification passes 2,976 Python tests, including all 606 extension-host cases. Strict types pass for 2,679 files. Root Ruff, focused Wemake, and shared policy parity pass. Full lint has 32 missing application uses and six unrelated Codex Wemake findings. No new dead-code or feature-document exemption was added. Three exact typed indexes follow the existing registry rule. This does not prove complete transform selection, engine or source integration, public mixed audits, package-owned E2E, dashboard views, or Kitty views. No phase or additional subtask is done.

## Current source transaction evidence

The [source transaction record](phases/04-events.md#work-record--atomic-source-reads-and-storage-review) adds 41 source cases and 15 final interpretation boundary cases. All 56 pass. They close P04-T01 storage review and start P04-T02. P04-T04 is also in progress because its schema-32 transaction subset exists. No phase is complete.

Source tests use real private SQLite files and retained package declarations, not running workers. They prove atomic originals and checkpoints, exact retry behavior, empty-read progress, stale-state rejection, full scope separation, reload resume, work notices, and stored reads after owner removal. Every source write and the actual COMMIT boundary have rollback coverage. A populated independent schema-32 fixture retains its old rows and DDL through schema 33. Injected failure after each migration statement and a deferred foreign-key COMMIT failure retain the old database.

These tests do not complete C07 or C21 through the application. Source watches, worker calls, the mixed interpreter, failure diagnostics, package-owned E2E, and frontend views remain open. Final full verification passes 3,032 tests, including all 662 extension-host cases. Strict types pass for 2,700 files. Root Ruff, focused Wemake, and policy parity pass. Full dead code has 39 missing application uses, and full Wemake has six unrelated Codex findings. No new exemption was added. The live daemon and user database were not changed.

## Current engine source evidence

The [engine source record](phases/04-events.md#work-record--engine-source-integration) connects private source workers, native watches, exact deadlines, scope ownership, and atomic checkpoints to the actual engine. The engine retains one runtime through the complete source/core-processing pass. Extension originals remain pending because mixed interpretation and transforms are not connected yet.

The new set has 63 cases. Sixty-one focused cases use real source storage and registry reads with explicit local source probes, controlled core consumers, and native file watches. Checks cover initial and failed plans, watch-before-read order, grants, complete call retention, bounded pages, timer postponement and cancellation, no idle polling, scope selection, release/reentry, publication during a batch, stop between pages, file replacement, missing parents, directory children, and symlinks.

Two separate cases build an SDK-only journal backend in an external temporary package with real locked offline dependencies. The actual private daemon enables it through the public lifecycle API. Native notices drive stored complete lines and replaced-file input. Restart restores the retained package after source-package removal and sends the saved checkpoint to its new worker. Disable stops later source input. Test inspection uses read-only SQLite access, so this is host integration evidence, not package-owned E2E through public diagnostics.

The focused 61-case run and both daemon cases pass. The first full run passed 3,094 cases and found one typed-parameter naming violation. After its correction, the final full run passes 3,095 tests, including all 703 extension-host cases, with 26 warnings in 251.72 seconds. Strict types pass for 2,733 files. Root Ruff, focused Wemake, and shared policy parity pass. Full dead code has 36 missing application uses, and full Wemake has six unrelated Codex findings. No rule or missing-use exemption changed. Main schema remains 33.

No phase or additional subtask is complete. Complete pass-time/fairness bounds, actual repository-view scope owners, complete source failure diagnostics, mixed interpretation, frontend views, and C06–C10/C21 remain open. The Python run excludes Kitty and `tests/e2e`. No browser, Kitty, live harness, process-kill, power-loss, or complete extension release check was run. The live user daemon and database were not changed.

## Current mixed engine evidence

The [mixed engine record](phases/04-events.md#work-record--mixed-engine-interpretation) connects the mixed pending queue, selected raw and canonical transforms, source-owned decoders, and complete interpretation transaction to the actual engine. `CoreInterpretation` retains the existing harness decoder and new-only post-commit input reactions. Storage now rejects omitted eligible transforms and false preflight failure claims.

This step adds 23 cases: six coverage checks, five preflight checks, seven complete local pipeline cases, three actual core adapter cases, and two new private-daemon cases. A focused run passes 102 cases. Four private-daemon tests pass, including the two earlier source tests now extended to inspect facts. They verify actual source-to-fact processing, original retention, convergence, restart without changed journals, and progress after a forbidden pure-lane host call. Local pipeline mocks are not worker evidence; the daemon cases use actual external SDK-only workers and offline environments.

Final full verification passes 3,118 Python tests, including all 726 extension-host cases, with 26 warnings in 198.54 seconds. Strict types pass for 2,758 files. Root Ruff, changed-file Wemake, policy parity, and `git diff --check` pass. The plan checks pass for 10 phases, 53 task records, dependencies, and 96 local links. Full Wemake retains six unrelated Codex findings. Dead code reports 24 findings: 22 missing application uses and two serialized preflight fields. No new missing-use exemption was added. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run.

Core raw changes remain rejected until lifecycle input protection is complete. Prior state is a bounded scope prefix, not a complete long-history snapshot. Aggregate byte and journal admission, complete multi-extension and failure conformance, mixed consumer progress, public diagnostics, and all frontend/release checks remain open. P04-T03 is now in progress; no phase or new subtask is complete. The live user daemon and database were not changed.

## Current bounded prior-state evidence

The [bounded prior-state record](phases/04-events.md#work-record--bounded-prior-state) adds explicit snapshot coverage, exact scoped capture, and limits for both fact count and complete UTF-8 size. Capture uses one SQL read transaction. Interpretation acceptance checks complete claims inside the write transaction, after checking each supplied body and its storage metadata. The count check reads at most the supplied count plus one.

This step adds 47 cases: 40 host cases and seven SDK cases. The focused set passes 49 tests. Checks include all scope keys, separate histories, stale and unknown heads, 1,000/1,001 facts, exact byte boundaries, Unicode and escaping, oversized-body avoidance, ordered prefixes, false complete claims, full rollback, failed transforms, older journal decoding, and an overlapping write from a second real connection. The actual batch supplies these snapshots to local controlled capabilities. An existing external-worker test now checks that both completeness values cross the process boundary. These are separate forms of evidence; the local mocks do not prove worker behavior.

Final full verification passes 3,165 Python tests, including all 766 extension-host cases, with 26 warnings in 253.18 seconds. Strict types pass for 2,768 files. Root Ruff, changed-file Wemake, policy parity, and `git diff --check` pass. All phase and task fields, dependencies, and 98 local links pass validation. Full Wemake retains six unrelated Codex findings. Dead code reports 26 findings: 24 findings for missing application uses and two serialized preflight fields. Two findings reappeared when prior capture stopped using the general scoped-page method; its protocol and implementation still need a public application caller. No exemption was added.

This change does not complete P04-T03, permit core raw changes, solve aggregate journal admission, or add application frontend and release E2E checks. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. Main schema remains 33. No live user daemon or database was changed.

## Current mixed core consumer evidence

The [mixed core consumer record](phases/04-events.md#work-record--mixed-core-consumption) connects the core reaction loop to a narrow mixed fact reader. Only core facts enter core reactions. A checked transaction advances past extension facts without session rows or display notices. It cannot skip an unprocessed core fact. A failed core write stops the batch and reaches the existing engine retry; the accepted prefix keeps its checkpoint and notices.

This step adds 31 cases: 30 host cases and one SDK case. Forty-three existing mapper round trips now use the production committed mapper. The focused run passes 181 tests. Checks include all three supported source scopes, more than one page, later core work, candidate exclusion, retry, rebuild, prefix failure, and real SQL INSERT, UPDATE, and COMMIT rollback. The private-daemon case uses an actual external worker and verifies source-to-fact consumption, no false display rows, disable, and restart. Seeded local read fixtures do not claim worker acceptance.

Full verification passes 3,196 Python tests, including all 796 extension-host cases, with 26 warnings in 240.46 seconds. Strict types pass for 2,775 files. Root Ruff, changed-file Wemake, and policy parity pass. Full Wemake retains six unrelated Codex findings. Dead code reports 28 findings: 26 missing application uses and two serialized preflight fields. No exemption was added. The old core-only page protocol and implementation no longer have an application caller.

This does not implement extension projectors, observers, candidate history rebuilds, or exactly-once side effects. The core legacy side effects still precede the read-model write and can repeat after write failure. Mixed pages still have no aggregate byte bound. Main schema remains 33. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No phase or additional subtask is complete. No live user daemon or database was changed.

## Current mixed page content evidence

The [page content record](phases/04-events.md#work-record--mixed-page-content-limits) adds a stored-content budget to mixed live and exact-scope pages. SQL counts bytes before returning bodies to Python. It selects an ordered prefix of at most 4 MiB, or one oversized first fact. This keeps progress possible without dropping or reordering facts. It is not a hard process-memory or complete-response-size limit.

The 22 new host cases cover exact stored byte boundaries, Unicode, escaping, requested counts, large middle and first facts, decoder calls, default limits, scope and history isolation, and actual core consumer draining. Two real-connection cases commit between body and head reads and verify one SQL snapshot. Two query-plan cases verify history and scoped cursor indexes. The focused page and mixed-reaction run passes 43 tests. The stored data is seeded for read tests; these cases do not claim worker acceptance or package-owned feature E2E.

Final full verification passes 3,218 Python tests, including all 818 extension-host cases, with 26 warnings in 234.04 seconds. Root Ruff, changed-file Wemake, strict types for 2,780 files, shared policy parity, and `git diff --check` pass. All 10 phases, 53 task records, dependencies, and 102 local plan links pass validation. Full Wemake retains six unrelated Codex findings. Full dead code retains 28 findings: 26 missing application uses and two serialized preflight fields. No exemption was added. Main schema remains 33. No phase or additional subtask is complete. Aggregate journal admission, complete pass-time limits, public diagnostics, and frontend/release checks remain open. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No live user daemon or database was changed.

## Current cooperative mixed scheduling evidence

The [mixed scheduling record](phases/04-events.md#work-record--cooperative-mixed-raw-scheduling) replaces complete mixed raw draining with one page per engine pass. The batch checks application stop before every original and checks a separate one-second monotonic predicate only after one original is complete. This preserves progress after slow page reads without overriding application stop. The engine owns a RAW continuation after a positive result; empty input clears it even after expiry. Core reactions and runtime release occur before the next pass.

This step adds 17 host cases. Local engine/storage cases prove complete journals before yield, ordered pending input, runtime release, exact resume, progress after expiry, no idle loop on empty input, wall-clock independence, stop before and after the first original, and normal failure retry. Controlled scheduler cases prove one page before reactions and exact interval expiry. Real queue cases verify one continuation, no idle timer after empty input, and preservation of another producer's retry. A private daemon and external SDK-only worker complete a 101-record file backlog without another file change and retain journals after disable and restart. These are not package-owned feature E2E checks.

Final full verification passes 3,235 Python tests, including all 835 extension-host cases, with 26 warnings in 221.45 seconds. The focused engine, daemon, and architecture set passes 34 tests. Strict types pass for 2,786 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. All 10 phases, 53 task records, dependencies, and 104 local plan links pass validation. An earlier parameter-naming violation was corrected. Full Wemake retains six unrelated Codex findings, and dead code retains 28 findings. No exemption or SQL migration was added. The cooperative interval does not bound one worker call, a complete interpretation, source passes, or core reaction draining. Aggregate journal admission and complete processing limits remain open. No phase or additional subtask is complete. The Python run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. No live user daemon or database was changed.

## Current ordered-worker and process-failure evidence

The [ordered-worker record](phases/04-events.md#work-record--ordered-workers-and-process-failure) adds seven private-daemon cases. Each uses an external source package and two external transform packages in separate locked worker environments. Six cases pass: declared order in both activation orders, raw and canonical all-dropped output, invalid raw source bytes, and invalid canonical causes. They verify exact output order, generated data reaching later workers, unchanged originals, complete reply evidence, and retained earlier decisions. The order cases also preserve journals after restart.

The seventh case verifies that a real worker process exits, later processing completes, and the next original reaches the core checkpoint. Its shutdown assertion then fails. One failed deactivation retains the complete runtime resource stack; the private application fixture must force termination after its stop deadline. This is an unresolved release defect. The test remains in the normal suite without a skip or expected-failure marker. The proposed cleanup policy change needs user approval.

The focused run reports six passed and one failed in 67.73 seconds. The full Python run reports 3,241 passed and one failed, with 26 warnings in 271.10 seconds. A separate architecture and retirement run passes 27 cases. Strict types pass for 2,793 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. Full Wemake retains six unrelated findings and dead code retains 28 findings. No production code, protocol, schema, quality exemption, or task status changed. Main schema remains 33.

These tests use read-only private SQL inspection and are host integration evidence, not package-owned feature E2E through public diagnostics. The full run excludes Kitty and `tests/e2e`; browser and Kitty checks were not run. The live user daemon, user database, and external services were not changed. Full C06–C10, P03 cleanup, journal admission, core lifecycle protection, and all frontend and release work remain open.
