# P07 — Kitty host

Status: in_progress

Depends on: P03, P05

Context: The current pane client selects mirror or scoreboard output. It is a standalone client program. Extension layouts must stay external while the client continues to own terminal output.

Task: Add typed extension display blocks, owned panes, view state, and input actions through the daemon.

Outcomes: An extension changes the mirror, scoreboard, or a named pane without changing or importing client source.

Verification: C14, C20, C24, and C28 pass. Real Kitty tests cover the visible interface and cleanup.

Evidence: P01 now supplies the public terminal protocol, seven typed block models, complete-result validation, and a separate worker prototype. See the P01 terminal work record. No Kitty host behavior is implemented yet.

## Read first

Read [protocols](../protocols.md), current `terminal/contract.py`, `terminal/panes/`, `client/_pane_rendering.py`, `client/_render_rows.py`, `client/_render_styles.py`, `client/terminal_view.py`, and client architecture tests.

### P07-T01 — Define and render terminal display blocks

Status: done

Owner: Claude Code

Depends on: P01-T05, P05-T02

Context: Current render primitives already handle text, wrapping, and diffs. Extensions need a public layout contract without sending arbitrary escape sequences.

Task: Implement typed text, section, table, tree, diff, status, and selectable-list blocks. Map them to existing render primitives. Validate size, nesting, links, and action references. Keep terminal control sequences in the client renderer. Define narrow and wide screen behavior and large-content paging.

Outcomes: The client can render extension data without feature-specific Python imports. The contract can display logs, a repository tree and diff, and a Slack thread.

Code areas: Proposed SDK terminal models and API responses; current client render modules and wire models.

Verification: Render the same blocks at narrow, normal, and wide sizes. Check wrapping, Unicode width, empty content, long paths, and control-character input. Compare semantic output and focused screen snapshots. Shared Python and client architecture checks pass.

Evidence: Reuse `baqylau_extension_api.terminal` and `contracts.presentation.ExtensionTerminalPresenter`. P01 tests cover block shapes, Unicode and control rejection, bounds, stable view binding, local identities, registered action schemas, and an isolated worker round trip. Client (2026-09-24): the pane reads the view with its own pydantic models (`client/_model_terminal*.py`) and never imports the SDK. `client/_render_extension_blocks.py` maps each block kind to a painter over the pane's primitives: text and sections wrap under a two-column indent for each depth; a table aligns its columns when their natural widths fit, else shows each cell as a `column: value` line with a hanging indent; a file tree and a status line keep one row each and cut at the pane edge; a diff uses the pane's diff painter under an `old → new` path line; a list marks the selected entry and shows its detail below. The client measures text in terminal columns (`client/_render_width.py`, standard library `unicodedata`: combining and format characters are 0, East Asian wide and full-width characters are 2); the pane's wrap and cut use it, and a wide character on a one-column line still takes one row. Every control character from an extension is drawn as U+FFFD, even if the daemon failed to refuse it; the pane alone writes control sequences. Paging: the view is painted from the top and bounded by the host limits; the pane window and its scroll position are view state in P07-T03. `tests/test_client_extension_blocks.py` paints a fixture view (`tests/client_fixtures/terminal_views.json`) at 20, 60, and 120 columns: every row fits, each block's meaning is visible, columns align when wide and stack when narrow, wide and combining characters are measured, control characters are marked, and empty content paints only what exists. Client boundary and architecture checks pass. Done on 2026-09-24.

### P07-T02 — Add pane contributions and ownership

Status: in_progress

Owner: Claude Code

Depends on: P07-T01, P03-T04

Context: The terminal service already supports open, close, resize, and focus. Extension panes need stable identity and lifecycle cleanup.

Task: Add named pane registrations, mirror sections, and scoreboard contributions. Route opening through the existing terminal service. Tag each pane with extension, view, scope, and runtime revision. Add a pane selector and preserve existing mirror and scoreboard behavior.

Outcomes: The user can open and select extension panes. Reopening the same pane is idempotent. Disable removes owned contributions and closes or replaces the owned pane according to a documented fallback rule.

Code areas: Current terminal pane services and metadata, client pane selection and model; proposed extension pane coordinator.

Verification: C20 opens the same pane twice, changes size, switches scope, reloads the extension, and disables it. Check actual Kitty windows and focus. Daemon restart identifies existing owned panes without duplication. Unrelated panes remain intact.

Evidence (partial, 2026-09-24): `GET /api/extension-terminal/views/{extension}/{view}?scope=&columns=&rows=` calls the active package's presenter (`extensions/terminal_presentation.py`) with the owner's committed projection cursor in its live generation, the resolved settings of the scope, and the pane size, then checks the complete result with the SDK validator. It answers 404 when no active package declares the view for the scope kind. `POST /api/extension-terminal/panes` opens the view beside a window through the existing terminal service (`terminal/extension_panes.py`). The pane window carries the tags `baqylau_extension`, `baqylau_extension_view`, `baqylau_extension_scope`, and `baqylau_extension_runtime`. A reopen matches extension, view, and scope, so it focuses the existing pane, also after a daemon restart and after a new runtime; another scope opens its own pane. The pane runs `client/terminal_extension_pane.py HOST PORT EXTENSION VIEW SCOPE`, which paints with the P07-T01 renderer, repaints on a resize or every 2 seconds, shows a waiting line while the daemon is down, and ends when the host answers 404. Fallback rule: a disabled or removed view ends its pane, and the terminal closes the window; windows without these tags are never touched. Tests: `tests/test_extension_panes.py` (tags, idempotent reopen, restart lookup, scope, unrelated windows), `tests/test_client_extension_pane.py` (paint, waiting, end on 404), and `tests/extension_host/test_terminal_view_daemon.py` (a real worker presents every block kind through the daemon; after a disable the host answers 404).

Pane selector: `client/terminal_extension_selector.py HOST PORT`, run by a terminal key binding as an overlay (README), lists the active views that `GET /api/extension-terminal/views?window_id=` offers for its window: every view that declares the installation scope, and every view that declares the session scope when the window belongs to a session (the session's lead actor and harness). Up and down (or `k` and `j`) move the selection, Enter opens the view through `POST /api/extension-terminal/panes`, and `q` or Esc closes the selector; the terminal mode is restored in every case. Tests: `tests/test_client_extension_selector.py` (window query, movement, marks, empty list) and the daemon test (the list offers the enabled view in its scope).

Mirror sections and scoreboard contributions: a view whose manifest `pane` is `mirror` or `scoreboard` is a section of that core pane for the session's lead actor. `GET /api/extension-terminal/sessions/{session}/{mirror|scoreboard}?columns=&rows=` presents them (`extensions/terminal_sections.py`); a view whose presenter fails is reported by title and never breaks the core pane. The mirror paints each section in full after the task panel, where the reader looks, and one "<title> is not available." line for each failed view (`client/_render_compose.py` now splits `mirror_rows` and `mirror_screen`). The scoreboard keeps its fixed height (`SCOREBOARD_HEIGHT`): each section is one chip in the detail row, its first status block, else its title; a failed view is a red chip. `client/_extension_sections.py` refreshes the sections at most every 2 seconds and at once after a width change, and keeps the last sections while the daemon does not answer. A mirror with no new session events repaints its sections on the next repaint. Tests: `tests/test_terminal_sections.py` and `tests/test_client_extension_sections.py`.

Open: a real Kitty run of C20 (focus, resize, and the window list). The `kitty`-marked tests open real terminal windows, so they need the user's approval before they run on this machine.

### P07-T03 — Connect data, selection, and input actions

Status: in_progress

Owner: Claude Code

Depends on: P07-T02, P05-T03

Context: Thread lists and file trees need selection and actions. Client input must call the registered extension command with the correct scope.

Task: Add a view-state model for selection, expansion, scroll, and content revision. Read data through the extension query stream. Map keys and clicks to typed command references. Recheck extension state and scope on the server. Cache presentation by data and view revision; avoid an RPC per row or character.

Outcomes: A tree or thread can be navigated and acted on in Kitty. Extension code supplies layout choices and action definitions. The client supplies input dispatch and display mechanics.

Code areas: Current client stream and input handling, `terminal_view.py`, terminal action service; proposed presentation cache and view-state models.

Verification: C20 navigates a tree, opens a diff, selects a thread, and calls an action. C24 rejects disabled and read-only writes. Ignore a stale response after a scope switch. Resize does not flood RPC calls. A slow command leaves the pane responsive.

Evidence (partial, 2026-09-24): The extension pane keeps its view state on the client (`client/_extension_pane.py` `PaneState`): the focused block and item, the scroll offset, a status line, and the focusable items. Focus moves over list entries, tree nodes, and table rows in paint order, also inside sections; it stops at the ends and moves to the first item when its item is gone (`client/_extension_focus.py`). The view request carries the focus (`block`, `item`), so the presenter can show details of the focused item; the list marks the focused entry and trees and tables show `›` in a two-column mark lane. Keys (`client/_extension_input.py`): up and down or `k` and `j` move the focus, PgUp and PgDn scroll one page, Enter runs the focused item's action, and `q` closes the pane. One request follows each focus change or resize burst, not each row or character.

Actions: `POST /api/extension-terminal/actions` takes only the view, scope, pane size, focus, action ID, and a request key. The host presents the view again at the same focus (`extensions/terminal_actions.py`), finds the action, and accepts its registered command with the action's arguments and expected state revision through the normal command admission, so the command declaration, scope, argument schema, and write policy are rechecked on the server. The job runs on the background executor, so a slow command leaves the pane responsive; the pane shows "Sent", a refusal with its status, or that the daemon is not available. Tests: `tests/test_client_extension_input.py` (focus order and ends, a removed focus, page scroll and the status line, Enter without an action, the three action results) and `tests/extension_host/test_terminal_action_daemon.py`: a real worker lists a read and a write action; the read action's command job succeeds, an unknown action is 404, and after a read-only restart the write action is refused (C24) while the read action is still accepted.

Data and cache: the pane follows the extension's record change stream (`GET /api/extensions/{extension}/changes?scope=`) on a daemon thread (`client/_extension_changes.py`) and repaints after each reset or change frame. It keeps the live generation and the last cursor, so a reconnect after a daemon restart does not read old changes again. An error frame ends the connection, and the follower connects again after 2 seconds; it stops when the pane closes. A slow 10-second timer shows settings and runtime changes, which are not record changes. The host keeps the 64 most recent checked views (`extensions/presentation_cache.py`) by the complete presenter request: runtime revision, settings revision, recorded snapshot, pane size, focus, and resolved settings. A repaint, a section refresh, or an action lookup with no new data does not call the worker. Tests: `tests/test_presentation_cache.py` (same request, new cursor or size, oldest view leaves first) and `tests/test_client_extension_changes.py` (a repaint for each boundary but not for a heartbeat, reconnect position, error frame).

Open: a real Kitty run of C20. The `kitty`-marked tests open real terminal windows, so they need the user's approval before they run on this machine.

### P07-T04 — Prove cleanup, fallback, and external ownership

Status: in_progress

Owner: Claude Code

Depends on: P07-T03, P05-T05

Context: Worker failure or package removal must not leave broken output or live subscriptions in a pane.

Task: Add generic stored-entry fallback and failed-view status. Release pane subscriptions and action handles on close, disable, reload, and daemon stop. Add package-owned real Kitty scenarios through the shared test kit. Update an external layout without a host build.

Outcomes: Kitty remains usable when an extension is unavailable. Extension terminal code and tests remain in the external package.

Code areas: Client fallback renderer, pane connection cleanup, test kit terminal journeys, external fixture package.

Verification: C14, C20, and C28 cover crash, removal, history display, and live layout update. Check no leftover test window or worker process. Compare main source and frontend artifact hashes. Run the applicable client tests and serial live Kitty E2E.

Evidence (partial, 2026-09-24): Stored-entry fallback: the session mirror shows a stored `extension` entry without extension code, as one line with its summary, else "owner: entry type" (`client/_render_entries.py`); before this, the mirror drew nothing for it. Failed-view status: `present_view` turns every presenter failure or result that is not valid into `TerminalViewFailedError` with one host message; worker text never reaches the pane. The view and action routes answer 503 (`api/extensions/error_handlers.py`), and the pane shows "This extension view failed" and stays open to try again after the next change. A mirror or scoreboard section reports the failed view by title. Failures are not cached. Release: the change follower stops when the pane closes and connects again after a daemon stop; a disabled or removed view answers 404, which ends the pane and its follower; an action is one request with no kept handle. Live layout update: `tests/extension_host/test_terminal_fallback_daemon.py` changes the package's own presenter, rescans, and reloads; the pane shows the new layout, and the hash of every host client file is the same before and after. The same test makes the presenter fail: the host answers 503 without the worker's text, and the pane shows the failure line. Tests: `tests/test_client_extension_entries.py` and the new 503 case in `tests/test_client_extension_pane.py`.

Open: package-owned real Kitty scenarios through the shared test kit, and C14/C20/C28 in live Kitty with a check for leftover windows and worker processes. The `kitty`-marked tests open real terminal windows, so they need the user's approval before they run on this machine.
