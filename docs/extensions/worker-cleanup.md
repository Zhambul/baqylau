# Worker cleanup design decision

Status: in_progress

Owner: Codex

Date: 2026-09-15

Decision: Approved by the user on 2026-09-15. Resource and evidence separation is implemented. The full Python suite passes 3,259 cases.

Related tasks: P03-T05 and P04-T04. Durable job recovery remains P05-T03 work.

Context: A new three-worker daemon test proves that event processing continues after one transform worker exits. The daemon then fails to stop normally. Its runtime owner requires every deactivation acknowledgement before closing any worker resources in that runtime.

Task: Separate physical resource cleanup from evidence about unresolved work. Do not treat a lost reply or process exit as proof that an external job completed. Preserve the registry drain, native ownership, and first-accepted fact rules.

Outcomes: A failed deactivation no longer prevents physical closure after processing stops. Uncertain work is stored before close, then the close result is stored before native ownership is released. This does not resolve or repeat an external job.

## Original reproduction before the fix

Run:

```sh
.venv/bin/python -m pytest -q tests/extension_host/test_ordered_processing_failures.py::test_worker_exit_keeps_later_processing
```

The test creates one source package and two transform packages outside the checkout. All three use real private SDK worker processes and the actual daemon. It enables them through the public lifecycle API, then adds two original records: `crash` and `next`.

The first transform worker returns its raw result with its process ID in a fixture diagnostic. During its canonical call it executes `os._exit(7)`. The test proves that this process ID no longer exists. The second worker completes the canonical stage. Both original records receive complete stored journals. The core consumer reaches cursor three without fake session rows. These processing assertions pass.

On context exit, the unchanged application fixture sends SIGTERM. Application shutdown reports `ManagerCleanupError`: extension workers still need cleanup; runtime ownership remains held. The daemon does not stop within the fixture's 15-second deadline. The fixture uses forced termination and fails the test. This is not a successful shutdown or a complete C10 result.

The focused run has six passing cases and this one failing case. The failure is not skipped, marked expected, or excluded from normal Python regression selection. Test reads use private read-only SQLite access. This is host integration evidence, not package-owned feature E2E through public diagnostics.

## Original cause

`RetirementOwner.retire()` in `extensions/manager_retirement.py` attempts deactivation in reverse active order. It keeps packages with failed acknowledgements or unresolved jobs. If any issue remains, it returns before `PreparedExtensionRuntime.close()`.

`OwnedPreparedRuntime` owns the whole runtime's resource stack. Thus one failed acknowledgement prevents closure of the other, acknowledged workers too. `ManagedExtensions.close()` then retains the issue and raises before releasing native ownership. The process test confirms the application cannot finish this path normally.

Existing unit tests deliberately require failed deactivation and unresolved jobs to retain ownership. Update them to the approved separation of resource closure and unresolved-work evidence. Do not make the new daemon test ignore shutdown or treat uncertain external jobs as complete.

## Approved boundary

Keep separate records for these facts:

| Fact | Required evidence |
| --- | --- |
| Processing stopped | Registry admission is closed and borrowed calls have left |
| Deactivation acknowledged | Exact worker, runtime, and acknowledged remaining jobs |
| Process resources closed | Owned process group, transport, readers, and private environment have completed cleanup |
| External work resolved | Durable job outcome or explicit unresolved state, not process liveness |
| Native ownership released | The shutdown policy permits a new manager without concurrent old execution |

On shutdown, first drain admitted processing. Attempt bounded deactivation and retain its complete result or failure. Close eligible physical resources without discarding job evidence. The final outcome must distinguish a clean stop from a stop with unresolved work. Persist the evidence needed across restart before releasing ownership or claiming recovery is possible. Decide how this uses the existing lifecycle storage and the later durable job store; do not invent successful job outcomes in either.

Runtime replacement needs its own drain rule. Do not apply a shutdown-only forced-close rule to active reads or running writes. Do not restart or repeat an uncertain command. An actual resource-close failure remains a failure even if a second close is an idempotent no-op.

Prefer the existing worker and prepared-runtime contracts. If a contract must change, document its exact ownership and retry behavior before adding it. Keep API and storage changes within the approved cleanup boundary.

## Verification

- Make the existing process-exit regression pass without removing any processing or shutdown assertion.
- Prove all owned worker processes, transport readers, and environments close after application shutdown.
- Retain failed deactivation and unresolved job identities across the required recovery boundary.
- Prove a lost reply cannot turn an uncertain external write into success, failure, or repeated execution.
- Preserve active-reader drain, preparation stop, reverse deactivation order, and exact retry behavior.
- Test one failed worker among several healthy workers, a hung worker, wrong runtime acknowledgement, pending jobs, and actual resource-close failure.
- Verify native ownership remains held while old execution can still continue, and can be acquired when the accepted shutdown rule permits restart.
- Run manager, retirement, worker, private-daemon, architecture, shared quality, and full Python regression checks.

Status result: P03-T05 and P04-T04 remain in progress. The cleanup change is implemented. Health policy, complete job recovery, total shutdown bounds, and full C10 remain open.

Decision (2026-09-25), taken as recommended under the user's standing "do the recommended" instruction: the total shutdown bound is the sum of the existing policy bounds.
- The registry drain waits up to `drain_seconds` (30 seconds, the host call deadline), so an admitted call finishes or reaches its own deadline.
- Deactivation has `deactivation_seconds` (2 seconds).
- Each worker has `stop_seconds` (2 seconds) to exit, and then the host kills its process group.

A stop therefore takes at most about 32 seconds plus 2 seconds for each worker. Shutdown does not stop a running write before its deadline, because a stopped push or commit can leave work that nobody can recover. If admitted calls remain after the drain, the host keeps the runtime ownership and records the reason. It never records an invented job outcome.

## Implementation — 2026-09-15

Status: in_progress

Context: The user approved physical cleanup after processing stops, with unresolved-job and failed-shutdown evidence retained.

Task: Keep the registry and executor drain first. Separate deactivation, durable evidence, physical closure, and native lease release. Preserve conservative replacement behavior.

Outcomes: `RetirementOwner` now has separate deactivation, resource-close, and typed observation methods. Runtime replacement still retains workers while acknowledgements or jobs remain unresolved. Application shutdown uses a shared two-second deactivation deadline through the existing host call context. It then stores an observation, closes drained resources, and stores the result. A storage error retains the lease. A physical close error remains an error on later calls; an idempotent no-op cannot hide it. A closed owner is never deactivated again. The manager releases the lease only after successful physical closure and evidence storage. Shutdown can return normally with unresolved work, with a log warning and a stored record. This is not a clean job outcome.

Code areas: `extensions/manager_retirement.py`, `manager_shutdown.py`, `manager_resources.py`, `models/cleanup.py`, the lifecycle repository contract and SQLite implementation, schema 34, public runtime response, and dashboard shutdown display.

Storage: `extension_shutdown_records` is an append-only host observation history. Each record has a stable identity, manager identity, timestamp, and typed runtime observations. Exact record retries retain one first body; conflicting bodies fail. A known prior manager may append cleanup evidence after it is fenced, but cannot change active intent through this method. The native lease remains required by the manager before each write. Current lifecycle state and the public runtime API return the latest record, including after restart. Earlier records remain stored even after a later clean stop. No job result, command retry, or external write is created by this storage method. Worker wire models and extension package layout are unchanged.

Verification: Added separate storage, physical-close, migration, and actual-process cases. These check failures before and after close, exact record identity, retained history, unknown manager rejection, known fenced-manager evidence, pending jobs, failed resource closure, native lock retention, a hung worker, wrong runtime replies, and public evidence after restart. The original worker-exit regression retains all processing assertions and now also checks surviving worker processes and private environments after stop. Schema-33 migration tests use independent saved DDL and cover old-row retention, statement rollback, and deferred COMMIT failure. An older schema-32 seed fixture omits only the new shutdown read while it creates old data; the new reader is restored before migration and assertions.

Limits: The deadline bounds deactivation RPC waits, not the earlier registry drain, executor completion, all process-group closes together, storage I/O, or arbitrary local protocol doubles. Existing worker resource limits still apply. There is no full historical cleanup API, job reconciliation implementation, power-loss proof, or orphan recovery after host SIGKILL. The latest-record display does not claim that older unresolved work was resolved. The full Python suite passes 3,259 cases, with 26 warnings in 261.83 seconds.

Next action: Implement the separately approved harness lifecycle split and journal body-reference storage. Keep P05-T03 job recovery and P03 health/removal policy open.

## Verification result

Status: done

Context: The approved cleanup change is complete. Its parent tasks still include health and complete job recovery.

Task: Verify resource closure, retained uncertainty, old storage, active-reader ownership, and regressions without changing test time limits or excluding failures.

Outcomes: The full non-Kitty Python selection passes 3,259 tests with 26 warnings in 261.83 seconds using six pytest workers. The original three-worker exit regression passes with its processing assertions and added process/environment closure checks. Three single-worker fault-and-restart cases cover hung deactivation, wrong runtime replies, and the exact retained external job ID. Three inventory tests ignore exited unrelated children while still requiring every expected worker. Shared strict types pass for 2,807 files. Root Ruff, changed-file Wemake, shared policy parity, and `git diff --check` pass. Full Wemake retains the six unrelated Codex findings; full dead-code analysis retains the prior 28 findings. No new exemption or test timeout was added.

Verification: An early full run had 3,248 passes and eight failures. Corrections covered a banned repository term, a stale frontend build, and a test-only storage trigger that was local to the wrong connection. Four cases reached time limits while other large test runs were active. A separate corrected group passed 14 storage/migration/naming checks and 12 daemon cases. The next full run had 3,254 passes and two failures in new tests: the combined three-worker fault/restart case exceeded the 30-second test limit at its second stop, and the next test encountered an exited child during process inventory. Fault/restart cases now select one worker; the separate three-worker exit regression retains mixed-runtime coverage. Inventory handles process disappearance but checks the exact expected worker count. The unchanged 15-second daemon stop check still applies. The final full run passes.

Evidence: No SDK worker from the private test roots remained after the full run. Verification used private databases and package roots, the installed SDK, macOS, Python 3.12.1, and the dirty worktree over `6a9e497`. The main schema is 34. No live user daemon, database, service, or Git remote was changed. No commit was made.

Status result: The approved cleanup subset is implemented and verified. P03-T05 and P04-T04 remain in progress. Continue with the already approved harness lifecycle split and shared-body journal storage.
