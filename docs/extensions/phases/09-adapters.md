# P09 — Adapters extension

Status: not_started

Depends on: P08

Context: The future extension must show adapters logs, deploy, Jira, metrics, and Slack as distinct activity. It must also publish commit and push activity for the Git extension. Actual CLI output has not been inspected.

Task: Build the adapters feature package in a separate repository with backend, schemas, web views, Kitty views, settings, and E2E cases.

Outcomes: The requested CLI operations have recorded identities, results, and dedicated views. The package uses the published SDK and shared quality policy.

Verification: The package's own repeatable, browser, Kitty, and configured live tests pass. C26 and C28 prove independence. Missing source data is shown as incomplete.

Evidence: Not recorded.

## Read first

Read the public SDK author instructions from P08, [protocols](../protocols.md), [quality](../quality.md), and current canonical shell payloads in `domain/event_shell.py`. Inspect the actual adapters CLI before defining product schemas.

### P09-T01 — Inspect the CLI and define observation schemas

Status: not_started

Depends on: P08-T05

Context: A shell command string identifies an invocation but may omit thread messages, metric values, or deployment progress. Several commands can share one shell output stream.

Task: Inspect supported CLI commands, versions, help, structured output, operation IDs, and cancellation behavior. Capture representative successful, failed, partial, and streaming results. Define one invocation identity and operation-specific schemas. Determine when shell evidence is sufficient and when an opt-in structured CLI observation source is required.

Outcomes: A documented CLI contract and fixtures. Each required field has a real source. Any required adapters CLI change is listed as separate external work before its dependent feature starts.

Code areas: External adapters extension schemas and fixtures; adapters CLI source only if separately authorized for a required observation change.

Verification: Parse the captured examples into typed records. Confirm thread content and write outcomes come from recorded output. Verify attribution for several invocations in one shell; if attribution is unavailable, mark it incomplete rather than assign output by guess.

Evidence: Not recorded.

### P09-T02 — Implement command detection and correlation

Status: not_started

Depends on: P09-T01

Context: Shell start, progress, finish, and background completion already have stable shell identities. Command text alone is not a unique operation ID.

Task: Build the external package from the shared template. Recognize adapters commands with the existing shell parser package. Handle literal quoting, wrappers, multiple invocations, and supported executable forms without executing text to parse it. Link invocation, session, actor, shell, and structured operation IDs. Track start, progress, success, failure, and cancellation.

Outcomes: One recorded operation per actual invocation, with stable source links. Repeated observations and restarts do not create duplicate operations. Unsupported syntax has an explicit partial or unknown result.

Code areas: External `backend/` parsers, transforms, projectors, and schemas; package settings and test fixtures.

Verification: Package tests cover quoting, aliases supported by evidence, repeated identical commands, command chains, background output, retries, and restart. Check output ordering and cause IDs. Run shared Python lint, types, design, dead-code, and architecture checks.

Evidence: Not recorded.

### P09-T03 — Add Logs entries and views

Status: not_started

Depends on: P09-T02

Context: The first requested workflow is a distinct event for each `adapters logs` call and a Logs tab next to Jobs and Monitors.

Task: Record query, target, time range, ordered output references, and result. Add a feed renderer, Logs session tab, and Kitty log view. Link each view to its invocation and source shell. Use paging and incremental output. Keep user-selected filters in extension view state or settings as appropriate.

Outcomes: Logs operations are visible as distinct entries and in their own view. Large output does not block the main feed. All feature UI code remains in the package.

Code areas: External schemas, backend projector, `web/src/`, terminal layouts, settings, and tests.

Verification: Run package-owned E2E from a captured and a controlled CLI invocation through raw storage to both displays. Check streaming, empty result, failure, cancel, reconnect, and disable. C18, C20, C26, and C28 pass.

Evidence: Not recorded.

### P09-T04 — Add Deploy, Jira, and Metrics views

Status: not_started

Depends on: P09-T03

Context: These commands share invocation tracking but have different result schemas and display needs.

Task: Add deployment target, revision, progress, and outcome records; Jira issue read and change records; and metric query, labels, time range, and series records. Provide dedicated tabs and terminal views through the same extension contracts. Use an existing chart package if the required metric display is not covered by the public UI support.

Outcomes: Each operation has a distinct feed entry and its own detailed view. Shared invocation code stays shared while product schemas remain explicit.

Code areas: External operation models, schemas, projectors, web components, terminal layouts, and settings.

Verification: Each declared feature has its own E2E case for both views. Test partial deploy output, failed writes, missing issue fields, empty metric series, and large results. Confirm no operation is reported successful before its outcome arrives. Run all shared quality gates.

Evidence: Not recorded.

### P09-T05 — Add Slack thread records and views

Status: not_started

Depends on: P09-T02; structured content evidence from P09-T01

Context: The user needs each thread the agent read, its messages, and the content it wrote. Current command detection alone cannot guarantee those fields.

Task: Record channel ID, thread ID, returned messages, read observations, write requests, confirmed writes, and errors. Keep message timestamps and stable message IDs separate from local arrival order. Add a Slack tab and Kitty pane with thread selection and message details. Show which messages were actually returned to the agent.

Outcomes: Read and write activity is visible by thread. Repeated reads merge source links without duplicating messages. Attempted writes and confirmed writes have distinct states.

Code areas: External Slack schemas and projector, thread and message components, terminal list layout, and fixtures.

Verification: Controlled E2E covers multiple threads, repeated reads, partial pages, edited message data where supplied, a successful write, a failed write, and lost write result. No test posts to a real channel by default. Test both frontends and incomplete-content indicators.

Evidence: Not recorded.

### P09-T06 — Publish Git activity and release the package

Status: not_started

Depends on: P09-T04, P09-T05

Context: The Git extension must detect `adapters commit` and `adapters push` without importing adapters parser code.

Task: Publish a versioned command-activity contract with repository scope, invocation ID, operation type, source links, and outcome. Include commit and push detection. Add optional peer discovery where needed. Build release artifacts with the shared policy and package-owned feature coverage.

Outcomes: Consumers can subscribe to commit and push activity through public events or a declared query service. Adapters works without the Git extension installed.

Code areas: External public schemas, service declarations, package manifest, CI, and E2E integration fixtures.

Verification: C15 and C16 cover the package alone, with a test Git consumer, and after consumer removal. Verify one operation with multiple source links when shell and structured sources overlap. Run package `make lint`, `make test`, and configured `make e2e`; record live CLI version evidence separately.

Evidence: Not recorded.
