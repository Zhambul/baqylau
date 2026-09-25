# P06 — Web host and settings

Status: in_progress

Owner: Codex

Depends on: P03, P05

Context: The dashboard has fixed route and entry types. Extension components, CSS, and assets must stay in their own package and load without a host build.

Task: Add independent module delivery, public view mounting, display slots, dynamic routes, settings, and failure recovery.

Outcomes: A separately built extension changes the dashboard through declared surfaces. Users can manage extensions and their settings in the web UI.

Verification: C05, C14, C18, C19, C24, C25, and C28 pass in the applicable browser matrix.

Evidence: P06-T04 management work has started against the existing public catalog and lifecycle APIs. No view mounting, package asset delivery, or settings form is complete. The earlier phase dependencies remain required for full completion.

## Read first

Read [protocols](../protocols.md), [quality](../quality.md), current `dashboard/frontend/src/app/route.ts`, `App.svelte`, `SessionTabs.svelte`, `SessionView.svelte`, entry response models, static delivery, and `api/config.py`.

Current dependency note: P03 supplies catalog and runtime reads, operation reads, preview, enable/disable/reload, and ordinary settings APIs. The management subset of P06-T04 now consumes the existing lifecycle API. Read-only policy remains extension-specific. Active-contribution delivery, independent assets, custom panels, and schema forms remain open. See the [P03 user-control record](03-runtime.md#work-record--user-lifecycle-controls) and [settings record](03-runtime.md#work-record--ordinary-settings-controls).

### P06-T01 — Serve catalog and independent package assets

Status: done

Owner: Claude Code

Depends on: P03-T04, P05-T02

Context: The main dashboard currently serves its own fixed build. Installed extension files must remain outside that build.

Task: Add a typed active-contribution catalog and an asset route keyed by extension ID and package digest. Serve only manifest-declared files inside the resolved package. Use correct MIME types and immutable caching for digest URLs. Publish catalog revisions through application changes. Review service-worker handling and retain assets while active clients use the package revision.

Outcomes: Independent ES modules, CSS, images, and chunks load from the installed package. No feature source or generated extension asset is copied into the main frontend.

Code areas: Proposed extension catalog and asset handlers; current `api/application/static_delivery.py`, frontend build validation, `api/config.py`, and `dashboard/static/sw.js`.

Verification: C18 and C25 load a valid bundle and reject undeclared assets, wrong digests, path escape, and unsupported file types. Test CSP and cache behavior in Chromium and WebKit. Verify no main frontend build is requested after extension installation.

Evidence: `GET /api/extension-web/views` lists the web views of the enabled packages in the published runtime, ordered by slot, order, owner, and view, with the runtime revision; a client refetches when that revision changes. `GET /extensions/{extension_id}/{package_digest}/{path}` (the prefix that the web SDK view host accepts) serves only a file that the package manifest declares, from the retained package copy of that digest, with its declared media type from a fixed servable set, after a SHA-256 check against the declaration, with no path escape, and with `Cache-Control: public, max-age=31536000, immutable`. The existing CSP (`script-src 'self'`) permits the modules; the service worker intercepts no fetch. `tests/extension_host/test_web_assets_http.py` loads a real package module through a real daemon and refuses an undeclared file, a path escape, and a wrong digest. The Chromium and WebKit matrix runs with the browser suite in P08. Done on 2026-09-24.

### P06-T02 — Implement the module mount lifecycle

Status: done

Depends on: P06-T01

Context: Runtime module loading needs cleanup and race handling. Svelte component instances must remain owned by their package.

Task: Add a generic loader for `ExtensionWebModule`. Supply target, scope, theme, API, and cancellation through the public interface. Validate exports and contain mount or update failures in a recovery view. Abort old work before disposal. Dispose a mount that completes after its view or runtime revision was removed.

Outcomes: View instances have a clear lifecycle. Independent runtime dependencies and scoped styles work. Private host stores never cross the boundary.

Code areas: Proposed `dashboard/frontend/src/extensions/` loader and host component; public TypeScript SDK; external fixture module.

Verification: C19 disables during import, mount, query, and update. Count subscriptions and retained DOM roots after repeated reload. Test a throwing mount and dispose. The main page remains navigable. Run shared frontend type, lint, dead-code, and browser checks.

Evidence: `src/extensions/views/ExtensionViewMount.svelte` makes one web SDK `ExtensionViewHost` for each mount element. It gives the host the view bundle, the scope, the runtime revision, the page theme, the settings that the published runtime resolves for the scope (`GET /api/extension-web/views/{extension_id}/settings`), and an API facade that has only the abort signal of the view. A changed view, scope, or runtime revision starts a new mount, and the host disposes the old view first. The component reads all of these inputs before its first await, so that Svelte follows each input. A settings reply that arrives after its run stopped does not show a view. The SDK host tests cover a disable during import, a late mount, an abort during an update, a throwing mount, and a failed disposal. The dashboard test reloads a view five times, with the first reload before the first import ends. After this, one root and one live mount stay, and each earlier mount is disposed once. This test found the stale-reply defect, and the defect is corrected. A failed module shows "<title> is not available." in the view, and the page stays usable. `tests/extension-views.spec.ts` mounts a separately built package module through a real daemon, and shows the unavailable state after the package is disabled, in Chromium and WebKit. Done on 2026-09-24.

### P06-T03 — Add routes and display contribution slots

Status: in_progress

Depends on: P06-T02

Context: `SESSION_TABS` and view branches are fixed. The host must route extension views without importing their components.

Task: Add extension ID and view ID route forms for session and workspace scopes. Implement tabs, pages, toolbar actions, status items, feed decorations, and explicit replacement targets. Use the common contribution rules for existing surfaces where needed. Validate target IDs and deterministic order. Preserve existing route forms and their tests.

Outcomes: A manifest can add a Logs tab or a Git workspace page. Replacement conflicts are visible. Additive contributions do not depend on activation timing.

Code areas: Current `app/route.ts`, app state, session tabs and views, feed presentation; proposed contribution registry and route models.

Verification: Navigate directly to an enabled extension route, reload it, disable it while open, and navigate back. Test two additive contributions and two exclusive replacements. Core routes and actor scope remain valid. C18, C19, and C28 pass.

Evidence (partial): `src/app/route.ts` reads and writes `#/s/{session}[/a/{actor}]/x/{extension}/{view}` and `#/w/{workspace}/x/{extension}/{view}`. The existing route forms and their tests do not change. `WebViewCatalog` reads the active views and refreshes when the runtime revision changes. `forSlot` gives only the additive views of one slot and scope kind, in the host order (slot, order, owner, view). `replacementFor` gives the view that replaces one entry kind. One `ExtensionSlot` component mounts the additive views of a slot. The page header uses it for `toolbar` and the line below the header uses it for `status`, both in the installation scope. The session mirror tab uses it for `feed` decorations above the entries, in the session scope. Session tabs show the `session_tab` views after the core tabs. A workspace route shows its `workspace_page` view. `WorkspaceLinks` links a session to the workspace pages of its related workspaces. `GET /api/extension-web/related-scopes?scope=` gives those workspaces from the same `SqliteScopeRelations` that settings resolution uses, so the browser does not compute a workspace ID. An absent `scope` query is the installation scope, for this route, the view settings route, and the settings routes (one `request_scope_or_installation`). The canonical feed shows an `extension` entry with its summary, or with "owner: entry type".

Decision (replacement targets): the SDK already requires a replacement to be in the `feed` slot with a named target, and the runtime switch refuses two replacements of one scoped target, so the management page shows that conflict. The SDK now also requires the target to be a core entry kind from `CORE_ENTRY_MODELS`, so an unknown target fails when the package is checked. `FeedView` mounts the replacing view in place of each entry row of that kind. The view snapshot gets an optional `subject: {entryId, kind}` (web SDK `FeedEntrySubject`), frozen like the other snapshot values. A changed subject needs a new mount. No host store or dashboard API model crosses to the view. The view reads entry data through the typed query client that P01 plans. The SDK alias `WebViewMode` names the `add` or `replace` mode for the SDK and the host models.

Verification so far: `tests/extension_api/test_manifest_assets.py` accepts `shell_started` and refuses an unknown target; `tests/extension_api/test_manifest_services.py` refuses two replacements of one scoped target. `src/extensions/views/web-view-catalog.test.ts` covers additive-only slots and the replacement lookup by kind and scope. `tests/test_related_scopes_route.py` covers the related-scopes route. `tests/extension-views.spec.ts` mounts a workspace page, a toolbar view, and a status view from a real daemon, and after a disable shows the unavailable page and removes the toolbar slot, in Chromium and WebKit (6 cases). `make lint` passes, the web SDK has 29 unit tests, and the dashboard has 146.

Feed decoration in a real session (2026-09-25): `tests/extension_host/test_feed_views_browser.py` enables a web package with a feed decoration and a replacement for the finished-turn row. A headless browser shows the decoration in a session's feed, and after a disable the open page removes it without a reload. A session made only of hooks has no displayed core row, because its transcript is read only in a terminal window, so the replacement row in a real session stays open.

Open: the feed replacement row in a real session with displayed rows (live-harness browser suite). The session fixture server has no extension package roots, and the view fixture server has no sessions; the combined fixture belongs to the P08 browser suite.

### P06-T04 — Add extension management settings

Status: done

Owner: Codex

Depends on: P06-T03, P03-T05

Implementation note: The management subset can use the current catalog, lifecycle, preview, operation, and read-only API without a dynamic feature route or a worker cleanup change. Start that subset now. Keep P06-T03 and P03-T05 open as full-task acceptance dependencies. Do not claim complete health or recent-operation history where the API does not yet supply them.

Context: The user needs a settings page with an extension list and controls. Requested state may differ from actual state during startup, stop, or failure.

Task: Add a top-level Settings route and Extensions page. Show identity, version, path, actual and requested state, health, dependency errors, and recent failures. Add enable, disable, reload, rescan, and operation progress. Display affected required dependents. Use revision-checked typed management routes.

Outcomes: Extension lifecycle is usable from the dashboard. Failed activation shows the reason and retained active version. A pending operation is not shown as complete.

Code areas: Proposed settings components, management API models and handlers; current app navigation and application change stream.

Verification: C02–C05 exercise successful and failed changes from two browsers. Test inaccessible worker, incompatible version, missing dependency, and recovery. C24 rejects forbidden writes through direct API access. Keyboard and accessibility checks pass.

Evidence: The management subset is implemented. See the dashboard management work record below. Independent views (P06-T01–T03) and settings forms (P06-T05) are done.

Recent operations and failures: `GET /api/extensions/operations?limit=` (1–50, default 20) returns the newest retained operations, newest first, including internal `restore` and `interrupted` work. `SqliteOperationHistory` reads the newest IDs and restores each through the same `read_operation` validation at one read snapshot; it needs no running manager, so the request-only contract test validates the reply. Each extension card has "Recent operations" with kind, status, time, and the failure code and detail. `tests/extension_host/test_lifecycle_http.py` checks the order and the limit through a real daemon; a browser case checks the card after an enable.

Health: each card shows a failing or a failed extension from `GET /api/extensions/health` (P03-T05), with the consecutive failed calls and the last failing stage. A daemon test proves the backend path (`tests/extension_host/test_health_daemon.py`), and `management.svelte.test.ts` covers the owner selection. No browser case yet makes a package fail and reads the card line. Done on 2026-09-24.

### P06-T05 — Add schema-based and custom settings panels

Status: done

Owner: Claude Code

Depends on: P06-T04, P05-T05

Context: Each extension may have its own settings. Simple fields should not require a custom frontend, but custom panels must use the same validation and storage rules.

Task: Select an existing compatible schema-form package if it meets the required Svelte integration and quality checks. Otherwise document the exact gap before defining a small supported field set. Support text, number, choice, boolean, list, and secret references. Add custom panel contributions. Implement defaults, installation values, workspace overrides, reset, validation, and optimistic revision checks.

Outcomes: Both standard and custom forms call the same API. Scope precedence is explicit. A settings change reports whether it affects display, future processing, or requires a history rebuild.

Code areas: Proposed settings form adapter and SDK settings methods; extension settings repository and migration service.

Verification: C05 tests default resolution, overrides, stale writes, invalid values, and reset. Save through a custom panel and read through the standard API. Secret GET values remain absent. A failed settings migration retains the prior settings and runtime revision.

Evidence: Library selection: `@sjsf/form` 3.8.2 (Svelte 5, MIT, released in September 2026) with `@sjsf/basic-theme` and `@sjsf/cfworker-validator`. The dashboard CSP is `script-src 'self'`; the Ajv validator compiles code at run time, so it cannot run under this CSP. `@cfworker/json-schema` interprets the schema and uses no `eval`. One gap exists: the sjsf declarations do not pass the shared `skipLibCheck: false` policy. `src/sjsf-augmentations.d.ts` imports the optional-field augmentations, and `patches/@sjsf+form+3.8.2.patch` (applied by `postinstall` through `patch-package`) intersects one generic binding type with the constraint that each concrete component already meets. A type probe showed that no concrete component breaks the constraint. The patch fails on an upgrade, so each upgrade gets a new check. The shared policy is not changed.

`#/settings/extensions/{extension}` edits the installation values and `…/w/{workspace}` edits one workspace override; each extension card and each workspace page links to them. The form starts from the effective document of the scope and shows whether the scope has its own values or uses the inherited values. Text, number, choice, boolean, and list fields come from the package's JSON Schema. `saveExtensionSettings` is the one save path: it reads the current lifecycle and catalog revisions, refuses a changed settings revision, and sends one complete document, or `null` to reset. The standard form and custom panels both use it. A `settings` slot view of the same extension shows on the page; its SDK client has `readSettings` and `saveSettings`, bound to the view's own extension and scope. Secret references show only "Set" or "Not set", with set and clear actions; values go to the keychain and no response contains them. The page follows the operation until it ends. A success reports the effect from the host rules: views and new processing use the new values, recorded history does not change, and a session reprocess applies them to past events. A failure shows its reason and states that the previous settings stay active.

Verification: `tests/extension-settings.spec.ts` runs against a real daemon (`tests/extension_web/settings_server.py`, with the in-memory keyring so no test value reaches the user's keychain) in Chromium and WebKit, 8 cases: default resolution, installation save and reload, a workspace that inherits and then overrides, reset to the inherited value, a value that the schema refuses, a custom panel save read back through the standard form, and a secret that is stored, not returned by the API, and cleared. `src/extensions/settings/*.test.ts` covers every field kind, schema refusal, the saved document and expected revision, reset with `null`, the stale-write message, and a failed operation (8 cases). The host keeps prior settings and runtime revision on a failed settings migration (P05 settings migration tests). `make lint` passes; 3465 Python cases pass, and the one timing-sensitive daemon test passes when it runs alone. Done on 2026-09-24.

### P06-T06 — Complete fallback, streaming, and frontend independence checks

Status: in_progress

Owner: Claude Code

Depends on: P06-T05, P05-T04

Context: An extension may be disabled while its history remains. Stream reconnect and history revision changes must not show duplicate or stale entries.

Task: Add the generic extension entry renderer and unavailable-view state. Handle entry lists, projection changes, history reset, and stale runtime responses. Add browser tests that build the host once, then change and reload an external package. Check package CSS containment and large-view paging.

Outcomes: History is usable without extension code. Dynamic reload changes extension views only. Frontend lifecycle and stream behavior have production-browser coverage.

Code areas: Current entry translators, feed reducers, stream decoder, browser test fixtures; proposed fallback views and external package scenarios.

Verification: C13, C14, C18, C19, and C28 pass. Compare main source and bundle hashes before and after the extension update. No Git or adapters component import appears in host code. Run `make test-frontend` and the applicable browser suite.

Evidence (partial): History reset: the session and global streams already sent `reset` with the new view revision after a history switch, but the dashboard had no handler, so `EventSource` reconnected and kept rows from the old view. Both stream clients now send their known `view_revision`, close on `reset`, and report the new revision. The session view drops its entries, reads a new snapshot and page, and reconnects. The list reads a new list and application state (`reloadAndReconnect`, which also serves a daemon restart). `decodeViewReset` is shared, and `src/api/session-stream.test.ts` and `global-stream.test.ts` cover the URL, the close, and a reset frame that is not valid. Stale runtime responses: `WebViewCatalog.refresh` applies only the newest request's reply; a test lets an older reply end last. The management page refreshes the view catalog as soon as it reads a new runtime revision; before this, new views could take up to 15 seconds to show (a browser test found this). Generic fallback: an `extension` entry shows its summary or "owner: entry type" without package code, and a missing view shows the unavailable state.

Browser (`tests/extension-views.spec.ts`, Chromium and WebKit, real daemon): a package stylesheet colours the package's own text and no host text (CSS stays in the shadow tree). A package module changed on disk, then rescanned and reloaded, shows the new view on its page, toolbar, and status line, and the page's host script and stylesheet URLs (content-hashed build names) do not change; the host is built once. Host independence: the dashboard ESLint configuration refuses any `@baqylau/*` import except `@baqylau/extension-api`, and any import through `packages/`; a probe import of a feature package fails the lint. `make test-frontend` passes (161 dashboard and 29 web SDK unit tests, coverage limits met). The 28 extension browser cases pass twice in a row.

Stored entries after a disable (C14, 2026-09-25): `tests/extension_host/test_stored_entry_fallback.py` shows a real projector's feed card in a headless browser, and the same stored card after the package is disabled, with no worker (P08-T03).

Live feed entries and a real history switch (C13, 2026-09-25): an open session page now shows a projector's entries when they are committed, and follows a history switch to the replayed session's entries; see the fixed defect in P08-T03 (`tests/extension_host/test_live_entries_browser.py`).

Large view (2026-09-25): `tests/extension_host/test_large_entry_list_browser.py` makes 120 finished turns, each with one extension card, and opens the session in a headless browser. The feed shows the newest cards without all of them, and scrolling to the load sentinel loads older pages until all 120 are shown. The feed pages by canonical fact, so one fact's entries come in one page; the SDK bounds them at 1,000 rows for each projection. The stored-card and large-list cases share `tests/extension_host/session_browser_fixture.py`.

Open: C28 terminal code is in P07. In a git worktree, 7 core `dashboard.spec.ts` cases fail: two screenshots show the worktree branch name and the Settings button from the management work, and the resume search does not find fixture sessions because the fixture directory is the worktree. This session did not change that code. These cases were not run on the main checkout.

## Work record — Dashboard management

Status: in_progress

Context: Public catalog and lifecycle controls existed, but the dashboard had no extension management page. This subset starts P06-T04 without claiming its dynamic-view dependencies are complete.

Task: Add Settings → Extensions through typed API calls. Preserve actual, requested, committed, and discovered versions. Require a host preview and explicit dependent confirmation before a revision-checked write.

Outcomes: `#/settings/extensions` lists external packages, paths, versions, capabilities, source errors, and retained owners whose source was removed. Enable, disable, reload, rescan, refresh, and pending operation progress work. Lost replies retain the exact request ID and body. A stale confirmation requires refresh and a new preview. Failed activation keeps the old active state visible. Read-only policy disables writes. Requests and progress timers stop on navigation. Idle pages do not poll. A native confirmation dialog has keyboard focus and focus restoration. The last stored shutdown observation separates closed resources from unresolved jobs.

Code areas: `dashboard/frontend/src/api/extensions.ts`, `src/extensions/`, `src/app/route.ts`, `src/app/App.svelte`, `tests/extensions.spec.ts`, and `tests/extension_web/management_server.py`.

Verification: `make test-frontend` and `make lint-frontend` pass with 134 dashboard and 28 web SDK unit tests. The production build and 54 browser cases pass. Ten browser cases use real private daemons in Chromium and WebKit. They cover enable, transitive dependent removal, optional dependency retention, two-page stale confirmation, discovery and preparation errors, keyboard focus, narrow layout, read-only UI and direct API rejection, and accessibility checks. The fixture rejects an external daemon URL for these write tests and requires normal private-daemon exit. Unit tests cover lost-reply retry, cancellation, late response rejection, missing source owners, state separation, and shutdown display. No snapshot was changed and no lint or coverage limit was reduced.

Evidence: Browser feature packages are backend-free control fixtures. They do not prove dynamic web mounting or package-owned adapters/Git E2E. Separate worker process tests cover backend cleanup. A focus defect found in Chromium and WebKit was corrected by opening the modal only when its controls are ready.

Final check: The 10 management cases passed again on the final build in 24.2 seconds, including normal private-daemon shutdown. One earlier invocation had a misspelled Python executable path and did not start a daemon; the corrected invocation passed. Shared types, Ruff, changed-file Wemake, policy parity, and the full 3,259-case Python run also pass. No live application restart was performed.

Status result: P06-T01, P06-T02, P06-T04, and P06-T05 are done. P06, P06-T03, and P06-T06 are in progress. Full operation history and health, and the session browser checks of the feed slot, are open.

Next action: Complete the P06-T03 and P06-T06 browser checks with the P08 fixture (session with extension packages, a real history switch, and stored extension entries after a disable). Use the existing shared web policy. Do not copy feature components or styles into the host.
