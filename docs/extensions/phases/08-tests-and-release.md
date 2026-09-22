# P08 — E2E and host release

Status: not_started

Depends on: P01–P07

Context: The extension system is complete only when independent packages can test it and use both frontends without host changes. The main test kit currently lives with the host tests.

Task: Package shared test support, complete conformance and cooperation tests, measure performance, and release the first host API.

Outcomes: A tested external extension workflow, complete host regression evidence, and author documentation for API version 1.

Verification: Applicable C01–C28 cases pass. The release record distinguishes repeatable checks, real Kitty checks, and live integrations.

Evidence: Not recorded.

## Read first

Read [verification](../verification.md), [quality](../quality.md), current `docs/testing.md`, `sdk/`, `tests/e2e/testkit/`, replay tests, production browser fixtures, and live Kitty journeys.

### P08-T01 — Package the shared extension test kit

Status: not_started

Depends on: P02-T06, P05-T02

Context: External tests need the same isolated application, typed client, diagnostic signoff, browser, and terminal support as host tests.

Task: Extract the reusable fixture contracts and typed client access into `baqylau-extension-testkit`. Add an explicit installed host executable or artifact input. Keep process ownership separate from typed API reads. Provide private roots, automatic ports, workspaces, and notification sinks. Keep product parsers out of the kit.

Outcomes: An external package can start and verify a private host with installed test support. Normal application data is not reachable through default test fixture values.

Code areas: Current `sdk/` and `tests/e2e/testkit/`; proposed test kit package and external fixture configuration.

Verification: C26 runs a package's tests outside the host checkout. Assert that port 8377 and normal data paths are rejected by default fixtures. Start two fixtures together and check isolation. Diagnostic signoff drains raw, canonical, projection, and job work.

Evidence: Not recorded.

### P08-T02 — Enforce package-owned E2E declarations

Status: not_started

Depends on: P08-T01, P06-T06, P07-T04

Context: Each extension needs its own E2E cases, including coverage for the frontends and harnesses it declares.

Task: Load test metadata from package declarations and validate feature, surface, harness, and dependency coverage. Reuse the current discovered harness list and documented limit rules. Add browser and Kitty helper APIs that target stable product identities. Keep scenario assertions in the external package.

Outcomes: The runner discovers and runs external test suites. Missing required coverage fails CI. Skipped live tests are reported separately from passed repeatable tests.

Code areas: Shared architecture checks, proposed test manifest model and runner, external fixture feature scenarios.

Verification: C27 fails a package with a declared web feature but no browser case, or a Kitty feature with only a unit test. Add a discovered harness and require coverage or an explicit valid limit. Run production browser and real Kitty scenarios through installed package entry points.

Evidence: Not recorded.

### P08-T03 — Complete failure, history, and cooperation conformance

Status: not_started

Depends on: P08-T02

Context: Single-package happy paths do not prove correct ordering, replacement, replay, or failure recovery.

Task: Complete C01–C28 using independent fixture packages. Exercise every transform operation, revisions, dependency changes, service and event cycles, failed migrations, stale views, command reconciliation, and generic history reads. Record public diagnostic assertions for each failure mode.

Outcomes: Conformance tests cover the complete extension contract. Both activation orders and operation races have repeatable cases.

Code areas: Host conformance suite, external fixture packages, public diagnostic API and test kit.

Verification: Run the complete matrix. Confirm failure fixtures reached their intended runtime condition. Inspect returned evidence for pending work, active revisions, and resource counts. Require C28 to prove unchanged host source and bundle hashes after an extension code update.

Evidence: Not recorded.

### P08-T04 — Measure performance and set supported limits

Status: not_started

Depends on: P08-T03

Context: Each transform crosses a process boundary. Slow jobs and large outputs can affect latency and memory if limits are incorrect.

Task: Run the fixed-capture measurements from [verification](../verification.md). Measure zero, one, and two extensions; large content; worker timeout; and slow commands. Set documented message, queue, nesting, and deadline limits from evidence. Optimize batching or caching only where measurement identifies a problem.

Outcomes: A reproducible performance report and supported resource limits. Regression checks target the measured failure risks.

Code areas: Proposed performance fixtures, process policy, presentation cache, source scheduling, and diagnostic counters.

Verification: Record input digest, machine, runtime versions, throughput, delay, memory, queue depth, and idle wakeups. Confirm bounded failure recovery and continued core processing. Repeat only the cases changed by an optimization and compare results.

Evidence: Not recorded.

### P08-T05 — Complete regression gates and publish author instructions

Status: not_started

Depends on: P08-T04

Context: The first public API becomes a dependency for external packages. Authors need exact build, test, upgrade, and lifecycle rules.

Task: Run host and shared package gates. Add author instructions for the factory, protocols, manifest, transforms, schemas, settings, web mounts, Kitty blocks, commands, cooperation, and migrations. Include a small external example. Record compatibility and policy release metadata. Build release artifacts; publication follows the user's release workflow.

Outcomes: API version 1 artifacts and a complete release record. P01–P08 outcomes are verified. The host and extensions use the same quality policy.

Code areas: Host and package CI, author documentation, examples, compatibility fixtures, and this phase's evidence record.

Verification: Run `make lint`, `make test`, applicable replay and browser gates, and real Kitty E2E. Run configured live checks separately and record any unavailable environment. Install release artifacts into a clean example package and run its own checks. Do not mark the release ready while a required gate remains unverified.

Evidence: Not recorded.
