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

Status: not_started

Depends on: P03-T04, P05-T02

Context: The main dashboard currently serves its own fixed build. Installed extension files must remain outside that build.

Task: Add a typed active-contribution catalog and an asset route keyed by extension ID and package digest. Serve only manifest-declared files inside the resolved package. Use correct MIME types and immutable caching for digest URLs. Publish catalog revisions through application changes. Review service-worker handling and retain assets while active clients use the package revision.

Outcomes: Independent ES modules, CSS, images, and chunks load from the installed package. No feature source or generated extension asset is copied into the main frontend.

Code areas: Proposed extension catalog and asset handlers; current `api/application/static_delivery.py`, frontend build validation, `api/config.py`, and `dashboard/static/sw.js`.

Verification: C18 and C25 load a valid bundle and reject undeclared assets, wrong digests, path escape, and unsupported file types. Test CSP and cache behavior in Chromium and WebKit. Verify no main frontend build is requested after extension installation.

Evidence: Not recorded.

### P06-T02 — Implement the module mount lifecycle

Status: not_started

Depends on: P06-T01

Context: Runtime module loading needs cleanup and race handling. Svelte component instances must remain owned by their package.

Task: Add a generic loader for `ExtensionWebModule`. Supply target, scope, theme, API, and cancellation through the public interface. Validate exports and contain mount or update failures in a recovery view. Abort old work before disposal. Dispose a mount that completes after its view or runtime revision was removed.

Outcomes: View instances have a clear lifecycle. Independent runtime dependencies and scoped styles work. Private host stores never cross the boundary.

Code areas: Proposed `dashboard/frontend/src/extensions/` loader and host component; public TypeScript SDK; external fixture module.

Verification: C19 disables during import, mount, query, and update. Count subscriptions and retained DOM roots after repeated reload. Test a throwing mount and dispose. The main page remains navigable. Run shared frontend type, lint, dead-code, and browser checks.

Evidence: Not recorded.

### P06-T03 — Add routes and display contribution slots

Status: not_started

Depends on: P06-T02

Context: `SESSION_TABS` and view branches are fixed. The host must route extension views without importing their components.

Task: Add extension ID and view ID route forms for session and workspace scopes. Implement tabs, pages, toolbar actions, status items, feed decorations, and explicit replacement targets. Use the common contribution rules for existing surfaces where needed. Validate target IDs and deterministic order. Preserve existing route forms and their tests.

Outcomes: A manifest can add a Logs tab or a Git workspace page. Replacement conflicts are visible. Additive contributions do not depend on activation timing.

Code areas: Current `app/route.ts`, app state, session tabs and views, feed presentation; proposed contribution registry and route models.

Verification: Navigate directly to an enabled extension route, reload it, disable it while open, and navigate back. Test two additive contributions and two exclusive replacements. Core routes and actor scope remain valid. C18, C19, and C28 pass.

Evidence: Not recorded.

### P06-T04 — Add extension management settings

Status: in_progress

Owner: Codex

Depends on: P06-T03, P03-T05

Implementation note: The management subset can use the current catalog, lifecycle, preview, operation, and read-only API without a dynamic feature route or a worker cleanup change. Start that subset now. Keep P06-T03 and P03-T05 open as full-task acceptance dependencies. Do not claim complete health or recent-operation history where the API does not yet supply them.

Context: The user needs a settings page with an extension list and controls. Requested state may differ from actual state during startup, stop, or failure.

Task: Add a top-level Settings route and Extensions page. Show identity, version, path, actual and requested state, health, dependency errors, and recent failures. Add enable, disable, reload, rescan, and operation progress. Display affected required dependents. Use revision-checked typed management routes.

Outcomes: Extension lifecycle is usable from the dashboard. Failed activation shows the reason and retained active version. A pending operation is not shown as complete.

Code areas: Proposed settings components, management API models and handlers; current app navigation and application change stream.

Verification: C02–C05 exercise successful and failed changes from two browsers. Test inaccessible worker, incompatible version, missing dependency, and recovery. C24 rejects forbidden writes through direct API access. Keyboard and accessibility checks pass.

Evidence: The management subset is implemented. See the dashboard management work record below. Complete health, operation history, independent views, and settings forms remain open.

### P06-T05 — Add schema-based and custom settings panels

Status: not_started

Depends on: P06-T04, P05-T05

Context: Each extension may have its own settings. Simple fields should not require a custom frontend, but custom panels must use the same validation and storage rules.

Task: Select an existing compatible schema-form package if it meets the required Svelte integration and quality checks. Otherwise document the exact gap before defining a small supported field set. Support text, number, choice, boolean, list, and secret references. Add custom panel contributions. Implement defaults, installation values, workspace overrides, reset, validation, and optimistic revision checks.

Outcomes: Both standard and custom forms call the same API. Scope precedence is explicit. A settings change reports whether it affects display, future processing, or requires a history rebuild.

Code areas: Proposed settings form adapter and SDK settings methods; extension settings repository and migration service.

Verification: C05 tests default resolution, overrides, stale writes, invalid values, and reset. Save through a custom panel and read through the standard API. Secret GET values remain absent. A failed settings migration retains the prior settings and runtime revision.

Evidence: Not recorded.

### P06-T06 — Complete fallback, streaming, and frontend independence checks

Status: not_started

Depends on: P06-T05, P05-T04

Context: An extension may be disabled while its history remains. Stream reconnect and history revision changes must not show duplicate or stale entries.

Task: Add the generic extension entry renderer and unavailable-view state. Handle entry lists, projection changes, history reset, and stale runtime responses. Add browser tests that build the host once, then change and reload an external package. Check package CSS containment and large-view paging.

Outcomes: History is usable without extension code. Dynamic reload changes extension views only. Frontend lifecycle and stream behavior have production-browser coverage.

Code areas: Current entry translators, feed reducers, stream decoder, browser test fixtures; proposed fallback views and external package scenarios.

Verification: C13, C14, C18, C19, and C28 pass. Compare main source and bundle hashes before and after the extension update. No Git or adapters component import appears in host code. Run `make test-frontend` and the applicable browser suite.

Evidence: Not recorded.

## Work record — Dashboard management

Status: in_progress

Context: Public catalog and lifecycle controls existed, but the dashboard had no extension management page. This subset starts P06-T04 without claiming its dynamic-view dependencies are complete.

Task: Add Settings → Extensions through typed API calls. Preserve actual, requested, committed, and discovered versions. Require a host preview and explicit dependent confirmation before a revision-checked write.

Outcomes: `#/settings/extensions` lists external packages, paths, versions, capabilities, source errors, and retained owners whose source was removed. Enable, disable, reload, rescan, refresh, and pending operation progress work. Lost replies retain the exact request ID and body. A stale confirmation requires refresh and a new preview. Failed activation keeps the old active state visible. Read-only policy disables writes. Requests and progress timers stop on navigation. Idle pages do not poll. A native confirmation dialog has keyboard focus and focus restoration. The last stored shutdown observation separates closed resources from unresolved jobs.

Code areas: `dashboard/frontend/src/api/extensions.ts`, `src/extensions/`, `src/app/route.ts`, `src/app/App.svelte`, `tests/extensions.spec.ts`, and `tests/extension_web/management_server.py`.

Verification: `make test-frontend` and `make lint-frontend` pass with 134 dashboard and 28 web SDK unit tests. The production build and 54 browser cases pass. Ten browser cases use real private daemons in Chromium and WebKit. They cover enable, transitive dependent removal, optional dependency retention, two-page stale confirmation, discovery and preparation errors, keyboard focus, narrow layout, read-only UI and direct API rejection, and accessibility checks. The fixture rejects an external daemon URL for these write tests and requires normal private-daemon exit. Unit tests cover lost-reply retry, cancellation, late response rejection, missing source owners, state separation, and shutdown display. No snapshot was changed and no lint or coverage limit was reduced.

Evidence: Browser feature packages are backend-free control fixtures. They do not prove dynamic web mounting or package-owned adapters/Git E2E. Separate worker process tests cover backend cleanup. A focus defect found in Chromium and WebKit was corrected by opening the modal only when its controls are ready.

Final check: The 10 management cases passed again on the final build in 24.2 seconds, including normal private-daemon shutdown. One earlier invocation had a misspelled Python executable path and did not start a daemon; the corrected invocation passed. Shared types, Ruff, changed-file Wemake, policy parity, and the full 3,259-case Python run also pass. No live application restart was performed.

Status result: P06 and P06-T04 are in progress. No phase or additional task is complete. Schema forms, secret controls, custom panels, full operation history and health, extension assets, and dynamic view slots remain open.

Next action: Complete the remaining P03/P05 dependencies, then implement P06-T01–T03 asset delivery and independent module mounting. Use the existing shared web policy. Do not copy feature components or styles into the host.
