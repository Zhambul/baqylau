# P08 — E2E and host release

Status: in_progress

Depends on: P01–P07

Context: The extension system is complete only when independent packages can test it and use both frontends without host changes. The main test kit currently lives with the host tests.

Task: Package shared test support, complete conformance and cooperation tests, measure performance, and release the first host API.

Outcomes: A tested external extension workflow, complete host regression evidence, and author documentation for API version 1.

Verification: Applicable C01–C28 cases pass. The release record distinguishes repeatable checks, real Kitty checks, and live integrations.

Evidence: Not recorded.

## Read first

Read [verification](../verification.md), [quality](../quality.md), current `docs/testing.md`, `sdk/`, `tests/e2e/testkit/`, replay tests, production browser fixtures, and live Kitty journeys.

### P08-T01 — Package the shared extension test kit

Status: done

Owner: Claude Code

Depends on: P02-T06, P05-T02

Context: External tests need the same isolated application, typed client, diagnostic signoff, browser, and terminal support as host tests.

Task: Extract the reusable fixture contracts and typed client access into `baqylau-extension-testkit`. Add an explicit installed host executable or artifact input. Keep process ownership separate from typed API reads. Provide private roots, automatic ports, workspaces, and notification sinks. Keep product parsers out of the kit.

Outcomes: An external package can start and verify a private host with installed test support. Normal application data is not reachable through default test fixture values.

Code areas: Current `sdk/` and `tests/e2e/testkit/`; proposed test kit package and external fixture configuration.

Verification: C26 runs a package's tests outside the host checkout. Assert that port 8377 and normal data paths are rejected by default fixtures. Start two fixtures together and check isolation. Diagnostic signoff drains raw, canonical, projection, and job work.

Evidence: `packages/extension-testkit/` builds `baqylau-extension-testkit` (httpx, pydantic, pytest; no host module). The host executable is an explicit input: `BAQYLAU_HOST_EXECUTABLE` must name the installed `baqylau-dashboard`, and the kit never guesses a checkout path. `HostProcess` owns the child process (`serve --port --data-dir --extension-root --log`, SIGTERM then a process-group kill), and `HostClient` only sends typed requests; the two are separate classes. `PrivateRoots` gives each host its own data, package root, workspace, home, and log under the test directory. The child environment is built from nothing: only `PATH`, `LANG`, `LC_ALL`, and `TMPDIR` pass; `HOME`, `XDG_*`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `OPENCODE_CONFIG_DIR`, and the Telegram directory are private; the terminal is `none`, Telegram and Web Push are off, and the keyring backend refuses every secret. Port 8377 and the user's data directory (or a directory in it) are refused (`UnsafeFixtureError`). The kit has its own reply models that ignore new fields, so a newer host does not break it; `tests/extension_testkit/test_testkit_models.py` checks every field that the kit reads against the host's published OpenAPI schemas. Signoff (`baqylau_extension_testkit.signoff`) waits until the diagnostics checkpoint has no pending raw events and the new `GET /api/diagnostics/extension-work` is empty, then requires a verdict for every raw event and no recorded host error. The new route (`extensions/work_backlog.py`) reads, for the active packages, the projector and observer owners that have facts after their cursors (the same pending-scope query as the engine passes) and the open jobs. The pytest plugin (entry point `pytest11`) gives the `baqylau_host` fixture.

Tests: `tests/extension_testkit/test_isolation.py` (port, data, environment, executable input), `tests/extension_host/test_testkit_hosts.py` (two hosts at the same time with their own ports, data, and packages; a real source package's input is drained and signed off in one host while the other has no input; port 8377 is refused), `tests/test_extension_work_backlog.py` (an observer with unread facts, its accepted job, then an empty backlog), and `tests/extension_testkit/test_outside_checkout.py` (C26: a clean venv gets the kit from local wheels only, and an external package's test starts a private host, sees only its own package, and signs off, with no host module importable).

Finding: a bare Claude Code hook for a session without a terminal window records an "interpreter (source construction)" host error on every pull, because the liveness source needs a window. The signoff reports it; the kit tests use extension source input instead. This is existing core behavior and is not changed here.

Data reads (`baqylau_extension_testkit.data_reads`): `records`, `query`, `command`, and `wait_for_job`. These use the public SDK's own wire models (record states, query results, command and observer results, diagnostics), so the kit does not copy them; the kit depends on `baqylau-extension-api` at the same version. `HostClient` takes an optional httpx transport, so `tests/extension_testkit/test_data_reads.py` checks the paths, the query parameters, the SDK result, and the job wait with `httpx.MockTransport`. `tests/extension_host/test_testkit_commands.py` runs a real worker's command through a private host: the job succeeds with the worker's reply, the record page is empty, and the host signs off. The model check also covers the record, job, and command replies. The C26 external test reads records, so it uses the SDK wheel from the local wheelhouse.

Notification sinks: the kit sends no notification to a real account. Telegram and Web Push are off, and the Telegram directory is private. The host's own E2E fixtures do the same, and API version 1 gives extensions no notification operation, so the kit has no recording sink. Browser and Kitty helpers are P08-T02.

Done on 2026-09-24.

### P08-T02 — Enforce package-owned E2E declarations

Status: in_progress

Owner: Claude Code

Depends on: P08-T01, P06-T06, P07-T04

Context: Each extension needs its own E2E cases, including coverage for the frontends and harnesses it declares.

Task: Load test metadata from package declarations and validate feature, surface, harness, and dependency coverage. Reuse the current discovered harness list and documented limit rules. Add browser and Kitty helper APIs that target stable product identities. Keep scenario assertions in the external package.

Outcomes: The runner discovers and runs external test suites. Missing required coverage fails CI. Skipped live tests are reported separately from passed repeatable tests.

Code areas: Shared architecture checks, proposed test manifest model and runner, external fixture feature scenarios.

Verification: C27 fails a package with a declared web feature but no browser case, or a Kitty feature with only a unit test. Add a discovered harness and require coverage or an explicit valid limit. Run production browser and real Kitty scenarios through installed package entry points.

Evidence (partial, 2026-09-25): Declarations: the SDK's `E2eCase` is now in `baqylau_extension_api.manifest.e2e`. It adds `harness_limit` (the only harnesses that the case tests, or none, with a reason that ends as a sentence) and `peers` (declared dependencies that the case covers). Manifest validation refuses a limit that names other harnesses than the case and a peer that is not a dependency (`tests/extension_api/test_manifest_e2e.py`). Coverage (`baqylau_extension_testkit.coverage`): a web view needs a `web` case, a terminal view needs a `kitty` case, and a backend needs a `worker` case, so a unit test is not coverage; each case file must exist; a case that does not test every discovered harness needs a limit, and a limit on a case that tests every harness is stale; each dependency needs a peer case. The harness list is the host's own discovered list (`GET /api/harnesses` on a private host), the same registry that the host's feature rules use. `tests/extension_testkit/test_coverage.py` covers C27: a web view with only an API case, a terminal view with only a worker case, a missing and a stale limit, a newly discovered harness, a dependency without a peer case, and a missing case file.

Runner: `python -m baqylau_extension_testkit.runner PACKAGE [--live]` reads the manifest, checks coverage, and exits 3 before any case runs when coverage is incomplete. It then runs the declared case files with pytest; a case marked `baqylau_live` is skipped without `--live`, and the summary counts live skips apart from repeatable results. `tests/extension_testkit/test_runner.py` runs the installed module in a subprocess against a real host: complete coverage reports "repeatable passed 1 … live skipped 1", and a missing limit stops the run.

Surface helpers: `baqylau_extension_testkit.browser` builds the dashboard's workspace, session, and settings routes and finds a mounted view by `data-extension-view`, which the web SDK sets on each view root (a view ID starts with its owner; Playwright sees into the open shadow root). Playwright is the kit's optional `browser` extra. `baqylau_extension_testkit.terminal_panes.terminal_view` reads the checked view that a pane paints, and `open_pane` opens a pane beside a live window. `tests/extension_host/test_testkit_surfaces.py` opens a real workspace route in headless Chromium and finds the package's view, and reads a real worker's terminal view.

Installed entry points: `tests/extension_testkit/test_runner_browser.py` gives a web package its own browser case, which uses the `baqylau_host` fixture, the lifecycle helper, and the browser helpers. The installed runner module checks coverage and runs it: the package's view mounts in headless Chromium on its workspace route. The scenario's assertions stay in the package's case file.

Open: a real Kitty case through the runner. It opens terminal windows, so it needs the user's approval.

### P08-T03 — Complete failure, history, and cooperation conformance

Status: in_progress

Owner: Claude Code

Depends on: P08-T02

Context: Single-package happy paths do not prove correct ordering, replacement, replay, or failure recovery.

Task: Complete C01–C28 using independent fixture packages. Exercise every transform operation, revisions, dependency changes, service and event cycles, failed migrations, stale views, command reconciliation, and generic history reads. Record public diagnostic assertions for each failure mode.

Outcomes: Conformance tests cover the complete extension contract. Both activation orders and operation races have repeatable cases.

Code areas: Host conformance suite, external fixture packages, public diagnostic API and test kit.

Verification: Run the complete matrix. Confirm failure fixtures reached their intended runtime condition. Inspect returned evidence for pending work, active revisions, and resource counts. Require C28 to prove unchanged host source and bundle hashes after an extension code update.

Evidence (partial, 2026-09-25): A search of the phase records and tests mapped C01–C28 to their tests. C01, C07, C08, and C12 are complete; the others had gaps. This work closes these gaps:

- C10, transform workers: `extensions/interpretation_health.py` now counts every raw transform, translation, and canonical transform step in the owner's durable health, as source reads and projections already were. A failed or rejected reply is a failure; core steps are not counted. Before this, a crashed or hung transform worker failed every later input without end. `tests/extension_host/test_transform_health_daemon.py`: a real transform worker hangs (`"hang"` input in `tests/extension_api/ordered_raw_example.py`), the 3-second call deadline ends its calls, the other worker's output is kept, the owner becomes failed and is disabled through a recorded failure operation at the limit, and later input runs without it.
- C06 and C09, public evidence: `tests/extension_host/public_audit_checks.py` reads each raw event's steps through `GET /api/diagnostics/raw-events/{id}` and requires the journal's stage order. The raw drop, replace, and insert daemon tests assert the public operation kinds, and the invalid-reply test asserts the public `failed` outcome with `processing_call_failed`.
- C03: `tests/extension_host/test_reload_under_input_daemon.py` writes input every 0.1 seconds while a changed transform package is rescanned and reloaded, and continues after the reload. Every input is stored and processed once, each input's worker calls use one runtime revision, the revision changes exactly once and never back, and every worker step is applied.
- C11, application level: `tests/extension_host/test_slow_command_daemon.py` gives a transform worker a slow read command (`tests/extension_api/slow_command_example.py`). Input is processed by both transform workers while the command is still running, and the command then succeeds.
- C28, host source: `tests/extension_host/test_terminal_fallback_daemon.py` now hashes every host source directory and the built dashboard files, not only the client, before and after the package's layout update.
- C23: the SDK requires an expected state revision for every write command, and the host passes it to the worker. Only the feature can compare it with its real state (for example a repository HEAD), so the real stale-revision case is in the Git package (P10).

- C17: `tests/extension_host/test_cycle_daemon.py` enables one of two web packages whose load orders point at each other. The second enable is refused with 400 and a reason that names the cycle, the first package stays active, and the source package keeps processing new input. The lifecycle planner now adds the SDK contract message to the refusal (`the selected extension set is not valid: …`); before this, every contract failure had the same generic message. Other value errors keep the generic message.
- C02 and C04 under input: `tests/extension_host/test_reenable_daemon.py` disables the source package, writes input while it is disabled, and re-enables it: the input is read once, and one worker runs; a daemon restart reads new input once. A reload whose new code cannot load fails while input arrives, and the old worker reads all of it.
- C05: `dashboard/frontend/tests/extension-settings.spec.ts` opens the settings page in two browser pages. The first save is accepted; the second page's save of its older form is refused with the stale-write message, and after a reload it shows the accepted value. It passes in Chromium and WebKit.
- C14, web: `tests/extension_host/test_stored_entry_fallback.py` installs a real projector (`tests/extension_api/session_card_example.py`) that adds a feed card for each finished turn. A headless browser shows the card in the session feed; after the package is disabled, the reloaded page shows the same stored card with no worker.

Fixed defect (C13, C14, live feed): an open session page did not show a projector's feed entries when they were committed, only after a reload. A projected entry keeps the canonical cursor of its fact (`session_entries.commit_cursor`), and the core entries of the same fact had already moved the client's stream cursor to that value; the delta read only `commit_cursor > cursor`. A history switch showed the same defect, because the re-projected entries keep old cursors. Fix: the session delta also follows the entry rows (`repository/impl/sqlite/session_entry_rows.py`): it reads entries after the canonical cursor or after the reader's highest entry row, and it returns the new highest row (`SessionDelta.entry_cursor`). The stream keeps that row for its connection. A client may send its highest row as `after_entry=`; without it, the stream starts from the highest row at or before the client's canonical cursor. The web session view and the Kitty pane send `after_entry`, and both already apply an entry once by its ID. The Kitty model no longer raises its canonical cursor from entry rows; it keeps them as `entry_cursor`. Tests: `tests/test_sqlite_late_entries.py`, the stream URL case in `src/api/session-stream.test.ts`, and `tests/extension_host/test_live_entries_browser.py`: a card for a turn that finishes while the page is open shows without a reload, and after a history switch the open page shows the replayed session's card (C13).

- C15 and C16, and a fixed defect: an HTTP query never opened a root call grant, so a worker's read of a declared peer service through `POST /api/extensions/{id}/queries/{query}` always failed with "peer query has no matching host call authority". `api/extensions/query_authority.py` now opens a root grant from the shared call ledger for the query's package and scope, bounded by the host call deadline. `tests/extension_host/test_peer_cooperation_daemon.py` runs two real peer workers in both activation orders while a source package processes input: alpha alone reads that the peer is not enabled, with beta active it reads beta's service, and after beta is disabled only that read changes; alpha stays active and the source keeps processing.

Shutdown bound, decided as recommended (`docs/extensions/worker-cleanup.md`): a daemon stop drains a running command up to the call deadline (30 seconds), then deactivates (2 seconds), then stops each worker (2 seconds each before a kill). A running write is not stopped before its deadline.

C21–C23 with the real Git package (2026-09-25): C21 reads a repository with no session through a private host (P10-T01); C22 sends a repeated commit and a repeated push request, and each is one job (P10-T03, P10-T05); C23 refuses a commit, an unstage, and a push whose expected state is stale (P10-T03, P10-T05). The cases are in the `baqylau-git` package and pass through the kit runner.

C20 in a real Kitty (2026-09-25, run after the user approved real Kitty runs): `tests/extension_host/test_extension_pane_kitty.py` (marker `kitty`, opt-in with `CLAUDE_E2E_KITTY=1`) starts a private host with the real Kitty terminal. It opens its own tab and asks the host to open an extension pane beside it. It then reads the pane's real screen with `kitten @ get-text`. The pane draws the view's query result, draws again after `kitten @ resize-window`, takes focus on a second open request, draws again after a reload, and stops drawing the view after disable. Only the test's own tab is closed. It passes (about 20 seconds), and `tests/test_terminal_contract_tabs.py::test_real_tab_launch` also passes.
Fixed test defect found on the way: the browser fixture set its fixed repository state under the provider wrapper, but the instance registry is keyed by the provider's `build` function. The live Git state of the checkout was therefore in every screenshot. Now the fixture state (`main`, not dirty) takes effect, and the baselines were updated once for this state and the extension Settings button. In `dashboard.spec.ts`, the resume cases used the displayed project directory, and that is the main checkout in a linked worktree. They now use the fixture's working directory (`fixtureWorkingDirectory`). All 26 `dashboard.spec.ts` cases pass in the worktree.
Open: the Kitty parts of C14, C24, and C28.

### P08-T04 — Measure performance and set supported limits

Status: done

Owner: Claude Code

Depends on: P08-T03

Context: Each transform crosses a process boundary. Slow jobs and large outputs can affect latency and memory if limits are incorrect.

Task: Run the fixed-capture measurements from [verification](../verification.md). Measure zero, one, and two extensions; large content; worker timeout; and slow commands. Set documented message, queue, nesting, and deadline limits from evidence. Optimize batching or caching only where measurement identifies a problem.

Outcomes: A reproducible performance report and supported resource limits. Regression checks target the measured failure risks.

Code areas: Proposed performance fixtures, process policy, presentation cache, source scheduling, and diagnostic counters.

Verification: Record input digest, machine, runtime versions, throughput, delay, memory, queue depth, and idle wakeups. Confirm bounded failure recovery and continued core processing. Repeat only the cases changed by an optimization and compare results.

Evidence: `tests/perf/extension_benchmark.py` measures zero, one, and two extensions, a long session, large content, and a slow worker with two deadlines, and records the capture digest, machine, and runtime versions. The results, findings, and limits are in [the performance report](../performance.md). The measurements found two costs that need a contract decision: the prior state of each canonical call grows with the session (2.5 events per second at 1,000 prior facts with one no-op transformer, compared with 95 without extensions), and a slow pure transform blocks core input until the 30-second deadline. `tests/extension_host/test_benchmark_smoke.py` keeps the benchmark working.

Changes from the evidence: canonical transformers declare `prior_state` in their processing selection, and only a declaring transformer gets prior facts (`extensions/prior_state_selection.py`); the host no longer captures or checks them otherwise. Pure engine-thread calls have their own 5-second deadline (`BAQYLAU_EXTENSION_TRANSFORM_SECONDS`), never more than the call deadline. The changed cases were measured again at 1,000 events: one no-op extension goes from 2.45 to 88 events per second (757 ms to 25 ms delay), and two extensions reach 59. Tests: `tests/extension_api/test_prior_state_selection.py`, the undeclared case in `tests/extension_host/test_processing_prior.py`, and `tests/test_extension_worker_policy.py`. Done on 2026-09-25.

### P08-T05 — Complete regression gates and publish author instructions

Status: in_progress

Owner: Claude Code

Depends on: P08-T04

Context: The first public API becomes a dependency for external packages. Authors need exact build, test, upgrade, and lifecycle rules.

Task: Run host and shared package gates. Add author instructions for the factory, protocols, manifest, transforms, schemas, settings, web mounts, Kitty blocks, commands, cooperation, and migrations. Include a small external example. Record compatibility and policy release metadata. Build release artifacts; publication follows the user's release workflow.

Outcomes: API version 1 artifacts and a complete release record. P01–P08 outcomes are verified. The host and extensions use the same quality policy.

Code areas: Host and package CI, author documentation, examples, compatibility fixtures, and this phase's evidence record.

Verification: Run `make lint`, `make test`, applicable replay and browser gates, and real Kitty E2E. Run configured live checks separately and record any unavailable environment. Install release artifacts into a clean example package and run its own checks. Do not mark the release ready while a required gate remains unverified.

Evidence (partial, 2026-09-25): Author instructions: `docs/extensions/authoring.md` covers the package layout, the manifest, the factory and each protocol, host rules (deadlines, prior state, failure limit, whole-result checks, write commands and reconciliation), cooperation, web mounts, Kitty blocks and actions, settings and migrations, tests and the runner, and the environment build. The SDK README now points to it and states the current limits. External example: `examples/hello-extension/` is a complete package (a feed-card projector, a query, a terminal view, and a web view) under the shared policy; `tests/extension_testkit/test_example_package.py` builds its wheelhouse and lock and runs its own case through the installed runner against a private host. Release: `make release-artifacts` builds the three Python wheels and the two npm packages with `SHA256SUMS` and the policy report; `docs/extensions/release.md` records the artifacts, digests, compatibility, policy digest, and gates. Gates on 2026-09-25: `make lint` passes; 3,587 Python cases pass; 164 dashboard unit tests pass; the 32 extension browser cases pass in Chromium and WebKit.

CI on Linux (2026-09-25): the first CI runs of this branch found faults that do not show on macOS. A Linux container (SQLite 3.40, inotify) reproduced each one, and each fix was checked there:
- An input that is missing at the top of the file system (such as a transcript folder that does not exist) made the host watch `/`. On Linux the recursive inotify walk failed, so every source stage failed; on macOS FSEvents watched the whole disk. The host now never watches the file system root (`core/input_events.py`, `test_missing_top_source_keeps_watches`).
- SQLite 3.40 gives `json_valid(NULL) = 0`, so a nullable JSON column refused `NULL`. The six nullable checks are now `CHECK(col IS NULL OR json_valid(col))`.
- A history switch did not wake the engine, so the projectors did not run for the switched history until an unrelated notice. The switch now writes with `WorkKind.CANONICAL`.
- The testkit tests started `bin/baqylau-dashboard`, whose shebang is `python3` on the PATH in CI. They now use `tests/host_launcher.py`, which starts the host with the test interpreter.
- The Linux job now installs the Python Playwright Chromium for the Python browser tests.
- The dev-tools format check no longer reads the test sample, which only the checks test installs.
- Two browser races in the extension settings page: the web view catalog refresh after a lifecycle change was cancelled when the settings page closed, so a view page opened at once showed "not available" for up to 15 seconds (`WebViewCatalog.refreshNow` now uses the catalog's own lifetime). The recent operations list was read beside the operation, so it could stay at `preparing` after the operation ended (the page now polls until no listed operation is preparing). The extension views spec passed 120 of 120 repeated runs in Chromium and WebKit after the fixes.

Open (Linux watches): Linux has no open-file notices, and inotify follows a replaced directory's inode. So on Linux a core file is seen only through its source directory, and a replaced additional directory is seen at the next source read. `test_directory_replacement_keeps_child_writes` and `test_removing_extension_watch_keeps_core_file` run on macOS only. A complete Linux fix needs a small change of the `InputEvents` design (a flat parent watch and inode checks), and that needs the user's decision.

Live checks (2026-09-26, approved by the user): `make e2e` passes on the branch: 50 audit replay cases, 255 live harness cases, 83 real Kitty cases, 49 live browser cases, and 76 static browser cases. The run found harness drift (new Claude Code 2.1 and Codex 0.156 record fields, pasted text wrappers, Codex paged rollouts, and new Codex screen texts) and some race faults. Each one is fixed in its own commit. The live suite now also has `tests/e2e/features/extensions.feature`. It installs the hello example, adapters, and Git packages into the live application, and real Codex, Claude Code, and OpenCode sessions give them their facts (see P09-T06 and P10-T06). All 10 scenarios pass.
- On macOS CI, `tests/extension_host/test_feed_views_browser.py` expected an open page to lose a disabled package's view at once. The page learns of a disable at the web view catalog's 15-second refresh, so the check now waits 30 seconds.
