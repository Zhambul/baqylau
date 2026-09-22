# P07 — Kitty host

Status: not_started

Depends on: P03, P05

Context: The current pane client selects mirror or scoreboard output. It is a standalone client program. Extension layouts must stay external while the client continues to own terminal output.

Task: Add typed extension display blocks, owned panes, view state, and input actions through the daemon.

Outcomes: An extension changes the mirror, scoreboard, or a named pane without changing or importing client source.

Verification: C14, C20, C24, and C28 pass. Real Kitty tests cover the visible interface and cleanup.

Evidence: P01 now supplies the public terminal protocol, seven typed block models, complete-result validation, and a separate worker prototype. See the P01 terminal work record. No Kitty host behavior is implemented yet.

## Read first

Read [protocols](../protocols.md), current `terminal/contract.py`, `terminal/panes/`, `client/_pane_rendering.py`, `client/_render_rows.py`, `client/_render_styles.py`, `client/terminal_view.py`, and client architecture tests.

### P07-T01 — Define and render terminal display blocks

Status: not_started

Depends on: P01-T05, P05-T02

Context: Current render primitives already handle text, wrapping, and diffs. Extensions need a public layout contract without sending arbitrary escape sequences.

Task: Implement typed text, section, table, tree, diff, status, and selectable-list blocks. Map them to existing render primitives. Validate size, nesting, links, and action references. Keep terminal control sequences in the client renderer. Define narrow and wide screen behavior and large-content paging.

Outcomes: The client can render extension data without feature-specific Python imports. The contract can display logs, a repository tree and diff, and a Slack thread.

Code areas: Proposed SDK terminal models and API responses; current client render modules and wire models.

Verification: Render the same blocks at narrow, normal, and wide sizes. Check wrapping, Unicode width, empty content, long paths, and control-character input. Compare semantic output and focused screen snapshots. Shared Python and client architecture checks pass.

Evidence: Reuse `baqylau_extension_api.terminal` and `contracts.presentation.ExtensionTerminalPresenter`. P01 tests cover block shapes, Unicode and control rejection, bounds, stable view binding, local identities, registered action schemas, and an isolated worker round trip. Mapping these models to the standalone client, width checks, paging, and screen tests remain open. Do not import the Python SDK into the client.

### P07-T02 — Add pane contributions and ownership

Status: not_started

Depends on: P07-T01, P03-T04

Context: The terminal service already supports open, close, resize, and focus. Extension panes need stable identity and lifecycle cleanup.

Task: Add named pane registrations, mirror sections, and scoreboard contributions. Route opening through the existing terminal service. Tag each pane with extension, view, scope, and runtime revision. Add a pane selector and preserve existing mirror and scoreboard behavior.

Outcomes: The user can open and select extension panes. Reopening the same pane is idempotent. Disable removes owned contributions and closes or replaces the owned pane according to a documented fallback rule.

Code areas: Current terminal pane services and metadata, client pane selection and model; proposed extension pane coordinator.

Verification: C20 opens the same pane twice, changes size, switches scope, reloads the extension, and disables it. Check actual Kitty windows and focus. Daemon restart identifies existing owned panes without duplication. Unrelated panes remain intact.

Evidence: Not recorded.

### P07-T03 — Connect data, selection, and input actions

Status: not_started

Depends on: P07-T02, P05-T03

Context: Thread lists and file trees need selection and actions. Client input must call the registered extension command with the correct scope.

Task: Add a view-state model for selection, expansion, scroll, and content revision. Read data through the extension query stream. Map keys and clicks to typed command references. Recheck extension state and scope on the server. Cache presentation by data and view revision; avoid an RPC per row or character.

Outcomes: A tree or thread can be navigated and acted on in Kitty. Extension code supplies layout choices and action definitions. The client supplies input dispatch and display mechanics.

Code areas: Current client stream and input handling, `terminal_view.py`, terminal action service; proposed presentation cache and view-state models.

Verification: C20 navigates a tree, opens a diff, selects a thread, and calls an action. C24 rejects disabled and read-only writes. Ignore a stale response after a scope switch. Resize does not flood RPC calls. A slow command leaves the pane responsive.

Evidence: Not recorded.

### P07-T04 — Prove cleanup, fallback, and external ownership

Status: not_started

Depends on: P07-T03, P05-T05

Context: Worker failure or package removal must not leave broken output or live subscriptions in a pane.

Task: Add generic stored-entry fallback and failed-view status. Release pane subscriptions and action handles on close, disable, reload, and daemon stop. Add package-owned real Kitty scenarios through the shared test kit. Update an external layout without a host build.

Outcomes: Kitty remains usable when an extension is unavailable. Extension terminal code and tests remain in the external package.

Code areas: Client fallback renderer, pane connection cleanup, test kit terminal journeys, external fixture package.

Verification: C14, C20, and C28 cover crash, removal, history display, and live layout update. Check no leftover test window or worker process. Compare main source and frontend artifact hashes. Run the applicable client tests and serial live Kitty E2E.

Evidence: Not recorded.
