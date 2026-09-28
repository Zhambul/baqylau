# P09 — Adapters extension

Status: in_progress

Depends on: P08

Context: The future extension must show adapters logs, deploy, Jira, metrics, and Slack as distinct activity. It must also publish commit and push activity for the Git extension. Actual CLI output has not been inspected.

Task: Build the adapters feature package in a separate repository with backend, schemas, web views, Kitty views, settings, and E2E cases.

Outcomes: The requested CLI operations have recorded identities, results, and dedicated views. The package uses the published SDK and shared quality policy.

Verification: The package's own repeatable, browser, Kitty, and configured live tests pass. C26 and C28 prove independence. Missing source data is shown as incomplete.

Evidence: The package is `~/code/personal/baqylau-adapters` (`baqylau.adapters`), made with `python -m baqylau_dev new`. P09-T01 and P09-T02 are done. P09-T03 to T06 have their repeatable work done, and each one records what is open. Every open item needs a live harness session, a user decision, or a later task. The CLI was never run: its source was read, and only `--help` was used. Recorded sessions on this machine gave the real output shapes; only structure was read, and nothing was copied.

## Read first

Read the public SDK author instructions from P08, [protocols](../protocols.md), [quality](../quality.md), and current canonical shell payloads in `domain/event_shell.py`. Inspect the actual adapters CLI before defining product schemas.

### P09-T01 — Inspect the CLI and define observation schemas

Status: done

Depends on: P08-T05

Context: A shell command string identifies an invocation but may omit thread messages, metric values, or deployment progress. Several commands can share one shell output stream.

Task: Inspect supported CLI commands, versions, help, structured output, operation IDs, and cancellation behavior. Capture representative successful, failed, partial, and streaming results. Define one invocation identity and operation-specific schemas. Determine when shell evidence is sufficient and when an opt-in structured CLI observation source is required.

Outcomes: A documented CLI contract and fixtures. Each required field has a real source. Any required adapters CLI change is listed as separate external work before its dependent feature starts.

Code areas: External adapters extension schemas and fixtures; adapters CLI source only if separately authorized for a required observation change.

Verification: Parse the captured examples into typed records. Confirm thread content and write outcomes come from recorded output. Verify attribution for several invocations in one shell; if attribution is unavailable, mark it incomplete rather than assign output by guess.

Evidence (2026-09-25): `docs/cli-contract.md` in the package is the CLI contract.
- Sources: the CLI source (read only), `adapters --help`, and the structure of 2,088 recorded shells that ran the CLI on this machine. The CLI was not run against any system.
- It records where each harness puts shell output, the invocation identity (`<shell_id>.<index>`), the supported command forms, the four output attributions and their measured shares, the exit-code outcomes, the typed result of each operation, and what shell evidence cannot give.
- The opt-in structured observation source (one JSON line for each invocation) was separate external work. The user approved it. It is on the CLI branch `observe-invocations` (commit `c00eb08`, not pushed): when `ADAPTERS_OBSERVE_FILE` is set, the CLI runs the command as a child and appends one record (ID, argv, working directory, start and end times, exit code). When the variable is not set, the CLI does not change. Two new tests pass. The full CLI suite has the same 3 failures with and without the change, and those 3 also fail on the clean base. No package feature reads this source yet.
- Typed records: `invocation_documents.py`, `log_documents.py`, `message_documents.py`, `work_documents.py`, and `result_documents.py`. Parsers: `log_results.py`, `message_results.py`, `work_results.py`, `raw_hits.py`, `output_tables.py`, and `results.py`.
- The fixtures in `tests/fixtures/` are written by hand to the source formats and hold no recorded content. `tests/fixtures/results.json` gives the exact typed result of each one.
- A local, read-only run parsed the recorded outputs, and printed counts only. 90% of the successful sole outputs with a typed format parse.
- Output is given to an invocation only when it is the one sole producer of the shell's output. Shared, filtered, and hidden invocations are marked as such, and nothing is assigned by guess.
- `vault`, `env`, `provider-creds`, `settings`, and `01conf` keep no output and no arguments, because they can hold secrets.

### P09-T02 — Implement command detection and correlation

Status: done

Depends on: P09-T01

Context: Shell start, progress, finish, and background completion already have stable shell identities. Command text alone is not a unique operation ID.

Task: Build the external package from the shared template. Recognize adapters commands with the existing shell parser package. Handle literal quoting, wrappers, multiple invocations, and supported executable forms without executing text to parse it. Link invocation, session, actor, shell, and structured operation IDs. Track start, progress, success, failure, and cancellation.

Outcomes: One recorded operation per actual invocation, with stable source links. Repeated observations and restarts do not create duplicate operations. Unsupported syntax has an explicit partial or unknown result.

Code areas: External `backend/` parsers, transforms, projectors, and schemas; package settings and test fixtures.

Verification: Package tests cover quoting, aliases supported by evidence, repeated identical commands, command chains, background output, retries, and restart. Check output ordering and cause IDs. Run shared Python lint, types, design, dead-code, and architecture checks.

Evidence (2026-09-25):
- `command_words.py` and `command_nodes.py` find invocations with `bashlex` (the host's shell parser package) and never execute text. They handle wrappers (`timeout`, `env`, `command`), environment prefixes, paths, lists, pipes, redirects, and quoting. `operations.py` resolves the CLI's group and verb aliases.
- A projector (`projector.py`, `shell_batches.py`, `shell_steps.py`) keeps one session-scoped record for each shell that ran the CLI (`baqylau.adapters.shells`). A record links session, actor, shell, and invocation, and it holds the IDs that the output gives (Jira keys, Slack `ts`, trace IDs).
- The projector reads `shell.started`, `shell.progressed`, `shell.backgrounded`, `shell.finished`, and `shell.output_finished`. It tracks start, output, success, failure, and cancellation. A backgrounded shell ends when its output stream ends.
- Tests: 77 package tests. They cover quoting, wrappers, aliases, chains, pipes, redirects, loops, repeated identical commands (one record for each shell), retries, a replay and a repeated start (no second record), chunk order and replace chunks, background output, output that arrives in `shell.finished` (Codex), and the outcome of each exit code.
- The package gate `baqylau_dev check --gate lint` passes (parity, architecture, types, dead code, WPS, and Ruff).
- The kit runner passes the repeatable case: the worker installs `bashlex` offline and activates, and signoff finds no host error.
- Host change: the kit's `build_environment` now packs the package's own `[project].dependencies` (`baqylau_extension_testkit.dependencies`, `tests/extension_testkit/test_wheelhouse.py`). Before, a worker could have only the SDK's dependencies.
- Open items:
  - Cause IDs come with the feed entries in P09-T03.
  - A repeatable host has no harness session, so an E2E case with real shell facts must be a live case (`baqylau_live`). It needs a live harness session and the user's approval. A synthetic session input in the host would be a new route for facts, and that is a host design decision for the user.

### P09-T03 — Add Logs entries and views

Status: in_progress

Depends on: P09-T02

Context: The first requested workflow is a distinct event for each `adapters logs` call and a Logs tab next to Jobs and Monitors.

Task: Record query, target, time range, ordered output references, and result. Add a feed renderer, Logs session tab, and Kitty log view. Link each view to its invocation and source shell. Use paging and incremental output. Keep user-selected filters in extension view state or settings as appropriate.

Outcomes: Logs operations are visible as distinct entries and in their own view. Large output does not block the main feed. All feature UI code remains in the package.

Code areas: External schemas, backend projector, `web/src/`, terminal layouts, settings, and tests.

Verification: Run package-owned E2E from a captured and a controlled CLI invocation through raw storage to both displays. Check streaming, empty result, failure, cancel, reconnect, and disable. C18, C20, C26, and C28 pass.

Evidence (2026-09-25):
- Feed: the projector gives one entry of type `baqylau.adapters.invocation` for each invocation, when its shell starts (`feed_entries.py`). Its cause is the `shell.started` fact, and its summary names the group, verb, and target only. Entries do not change after the host writes them (`INSERT OR IGNORE`), so the row view (`web/row.js`) reads the live record for the state and the result.
- Reads: `baqylau.adapters.invocations` (by group or by invocation) and `baqylau.adapters.logs` (the Kitty pane's read, with its focus). They read the session's records through the host's record reader, and answer with a state revision of the records.
- Views: a Logs session tab (`web/logs.js`) with the calls and the selected call's hits, which follows record changes. A Kitty Logs pane (`logs_view.py`, `log_blocks.py`) with a list of calls and a table of the focused call's hits, or why there are none.
- Host change: a feed `replace` view can now target an entry type of its own package. The SDK rule is in `manifest/presentation.py` and `tests/extension_api/test_feed_targets.py`. The dashboard lookup is `feed-replacement.ts` with 3 tests. The web SDK's `FeedEntrySubject` has an optional `document`. Before this change, an extension entry could only be a note line.
- Tests:
  - The Python package tests (82) cover the entries and their causes, the reads, the pane focus, the hit table, a running call, and the empty and problem texts.
  - The package's Vitest tests (4, jsdom, a complete fake context) cover the tab and the feed row.
  - The package gates `baqylau_dev check --gate lint` and `make lint-web` (types, ESLint, pins, Prettier, tests) pass.
  - The kit runner passes the repeatable case: worker, the reads through the host API, and the Kitty pane through the host's terminal route.
- Live (2026-09-25, approved by the user): a private host ran on a copy of the local store, not on the real store. A history replay of one recorded session gave the projector its shell facts, because a newly enabled projector does not backfill. The package then read 41 calls: 36 with shared output, 4 with filtered output, and 1 with its own `log_lines` output. The live case `tests/e2e/test_session_views.py` passed through the runner's `--live`: the Logs tab shows the calls with their outcome, and the session feed shows the package's rows. The copy was deleted after the run.
- Open:
  - Streaming, reconnect, and disable were not checked in the live host. The repeatable cases cover disable.

### P09-T04 — Add Deploy, Jira, and Metrics views

Status: in_progress

Depends on: P09-T03

Context: These commands share invocation tracking but have different result schemas and display needs.

Task: Add deployment target, revision, progress, and outcome records; Jira issue read and change records; and metric query, labels, time range, and series records. Provide dedicated tabs and terminal views through the same extension contracts. Use an existing chart package if the required metric display is not covered by the public UI support.

Outcomes: Each operation has a distinct feed entry and its own detailed view. Shared invocation code stays shared while product schemas remain explicit.

Code areas: External operation models, schemas, projectors, web components, terminal layouts, and settings.

Verification: Each declared feature has its own E2E case for both views. Test partial deploy output, failed writes, missing issue fields, empty metric series, and large results. Confirm no operation is reported successful before its outcome arrives. Run all shared quality gates.

Evidence (2026-09-25):
- The views are generic by group, so the Logs code is not copied (`pane_groups.py`). Each group (`logs`, `deploy`, `jira`, `metrics`, `slack`) has a session tab (`<group>-page`, one module `web/operations.js`), a Kitty pane (`<group>-pane`), and a pane read (`baqylau.adapters.<group>`).
- Each result kind has one declared table (`result_tables.py`, and `web/results.js` in the browser): log hits, Jira issues, Slack messages, and the deploy fleet grid. A deploy also lists its `SUMMARY:` lines, a write is one line, and a metrics document is its JSON.
- Tests: `tests/test_group_panes.py` draws a deploy status grid, a partial deploy rerun (summary and failed zone), a Jira JQL table with a missing assignee, a Jira comment, a metrics JSON document, a Slack read, and a failed deploy (one notice line). A running call shows `running` and has no outcome until its shell ends. Each table stops at the block limit (512 rows). The Vitest tests draw a Jira tab from its own group.
- All package gates pass. The kit runner draws all five panes through the host.
- Metrics, as recommended: typed series records, and no chart package. `metric_documents.py` and `metric_results.py` read a Prometheus result into series (labels and points), and an ES|QL result into columns and rows. The Kitty pane and the web tab show a series table (labels, sample count, last reading) or the value table. Any other shape shows its JSON. A chart would need a build step for the web modules, and only one result shape is a time series. Tests: Prometheus and ES|QL fixtures, and a Vitest metrics tab.
- The live `session-views` case passes (see P09-T03). The recorded session had no Deploy, Jira, or Metrics calls, so the live case checks only the Logs tab. The Vitest tests cover these tabs.

### P09-T05 — Add Slack thread records and views

Status: in_progress

Depends on: P09-T02; structured content evidence from P09-T01

Context: The user needs each thread the agent read, its messages, and the content it wrote. Current command detection alone cannot guarantee those fields.

Task: Record channel ID, thread ID, returned messages, read observations, write requests, confirmed writes, and errors. Keep message timestamps and stable message IDs separate from local arrival order. Add a Slack tab and Kitty pane with thread selection and message details. Show which messages were actually returned to the agent.

Outcomes: Read and write activity is visible by thread. Repeated reads merge source links without duplicating messages. Attempted writes and confirmed writes have distinct states.

Code areas: External Slack schemas and projector, thread and message components, terminal list layout, and fixtures.

Verification: Controlled E2E covers multiple threads, repeated reads, partial pages, edited message data where supplied, a successful write, a failed write, and lost write result. No test posts to a real channel by default. Test both frontends and incomplete-content indicators.

Evidence (2026-09-25):
- `threads.py` groups the session's Slack calls into threads (`thread_documents.py`).
  - A thread key is the CLI's `thread` value, or the channel.
  - A message's identity is its permalink, or its thread, time, and sender. A repeated read adds itself to the message's `returned_by`, and it gives no second message.
  - Messages are in message time order, apart from the order of the reads.
- `thread_writes.py` gives each write its thread from the reply's `thread_ts`, and its state. It is `confirmed` only when the reply names the message's `ts`; `attempted` while it runs; `failed` for a failed call; and `unknown` otherwise (a lost result, for example output into a pipe). It also keeps the text that the agent sent.
- Reads and views: `baqylau.adapters.threads` (web) and the Slack pane read (Kitty, with its focus). The Kitty Slack pane (`thread_blocks.py`) shows threads, then the focused thread's messages with how many reads returned each, then its writes. The Slack tab (`web/threads.js`) shows the same.
- Tests: `tests/test_threads.py` covers two threads, two reads of the same messages, a confirmed reply that joins its thread, a failed write, a write with a lost result, and the pane. A Vitest test covers the Slack tab. No test posts to Slack: the package never runs the CLI.
- Incomplete content: a thread is marked `incomplete` when a read kept fewer messages than it returned, or when its output was cut at the limit. Both displays show the marker. `tests/test_thread_edges.py` covers this, and an edit reply that becomes a confirmed write of its channel. The CLI's reply to an edit has no thread.
- The live `session-views` case passes (see P09-T03). The recorded session had no Slack calls, so the Vitest test covers the Slack tab.

### P09-T06 — Publish Git activity and release the package

Status: in_progress

Depends on: P09-T04, P09-T05

Context: The Git extension must detect `adapters commit` and `adapters push` without importing adapters parser code.

Task: Publish a versioned command-activity contract with repository scope, invocation ID, operation type, source links, and outcome. Include commit and push detection. Add optional peer discovery where needed. Build release artifacts with the shared policy and package-owned feature coverage.

Outcomes: Consumers can subscribe to commit and push activity through public events or a declared query service. Adapters works without the Git extension installed.

Code areas: External public schemas, service declarations, package manifest, CI, and E2E integration fixtures.

Verification: C15 and C16 cover the package alone, with a test Git consumer, and after consumer removal. Verify one operation with multiple source links when shell and structured sources overlap. Run package `make lint`, `make test`, and configured `make e2e`; record live CLI version evidence separately.

Evidence (2026-09-25):
- The package publishes the versioned service `baqylau.adapters.git-activity` 1.0.0 (`activity_documents.py`, `activity_reads.py`). It gives the session's `commit`, `push`, `branch`, `tag`, and `merge` calls with invocation ID, source shell, start time, and outcome. A running call has no outcome. A consumer reads it through the service, and never imports this package.
- `tests/e2e/test_git_activity.py` (the runner's `git-activity` case) installs a test consumer package that declares an optional dependency and consumes the service (`>=1,<2`). In a private host:
  - the consumer reads the service (`0 activities` in a session with no calls);
  - with adapters disabled, it gets the explicit reason `not_enabled`;
  - with the consumer disabled, adapters answers its own read.
- `tests/test_git_activity.py` covers the calls, a running push, and the operation filter.
- The package's `make lint` (the Python gate), `make lint-web`, `make test`, and `make e2e` pass: 96 Python tests, 6 web tests, 2 repeatable cases, and 1 live case skipped.
- Scope: the contract is session-scoped. A repository consumer lists the repository's sessions through the new host session list (see P10-T06). The other option was a working directory in core shell facts, but harnesses do not report one for each command.
- Service 1.1.0 (2026-09-26): the service also accepts the repository scope. It lists the repository's sessions itself, and gives each call with its `session_id` (`repository_activity.py`). Reason: the host gives a peer query only the scope of the consumer's own call. A repository read of the Git package cannot make a session-scoped peer query ("peer query has no matching host call authority"). This keeps the host's call-grant rule as it is. A 1.0 consumer ignores the new field. `tests/test_git_activity.py` covers the repository read.
- Live harness E2E (2026-09-26): `tests/e2e/features/extensions.feature` runs in the main repository's live suite. Each scenario copies the package source into the live application, builds its environment, and enables it. Codex, Claude Code, and OpenCode each run the real `adapters` CLI in their shell, with read-only commands only: `adapters logs -e preprod -s 5m -n 3`, and `adapters commit --help` (group `commit`). The package records each finished call and projects its feed entry. A package disabled during a live session stops answering. All 10 scenarios pass.
- Open:
  - The Git package's own consumer is P10-T06.
  - Overlapping sources: the CLI observation source now exists (P09-T01), but the package does not read it yet. So one operation has one source link.
  - The live `session-views` case ran on recorded shells only. The live harness E2E runs the real CLI with read-only commands only. The CLI has no version command, so there is no CLI version evidence.
