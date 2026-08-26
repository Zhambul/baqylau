# Dashboard frontend rewrite plan

Status: implemented on `codex/frontend-rewrite-plan`; pending review and cutover

Last reviewed: 2026-08-24

Scope: `dashboard/frontend`, `dashboard/static`, FastAPI asset integration, and frontend tests

Primary constraint: preserve the current design and behavior

## Decision

Rewrite the dashboard as a client-rendered **Svelte 5 application using runes, strict
TypeScript, and Vite**. Keep FastAPI as the backend and HTML host. Use **native CSS with
the existing `dashboard/static/style.css` as the initial source of truth**. Add Vitest,
Testing Library, and Playwright for layered tests.

Do not add Tailwind, a component library, SvelteKit, or a client-side data-fetching
framework during the rewrite.

This is a better fit than React for this dashboard. React remains current and would be a
safe organizational choice, but Svelte maps the existing markup and CSS to components
with less runtime and state-management ceremony. That lowers the number of unrelated
changes needed to preserve the exact DOM, interaction model, and visual design.

### Selected stack

| Concern | Choice | Reason for this project |
| --- | --- | --- |
| UI | Svelte 5, runes mode | Declarative keyed rendering and explicit reactive state without a virtual-DOM/state-library layer |
| Language | TypeScript in strict mode | Makes API, SSE, route, and component contracts explicit |
| Build/dev | Vite | Fast development loop and a production manifest that FastAPI can serve safely |
| CSS | Existing native CSS, then selectively scoped Svelte CSS | Preserves the current cascade, tokens, responsive behavior, and visual identity |
| Unit tests | Vitest | Natural fit with Vite; fast tests for reducers, parsers, routing, and helpers |
| Component tests | Testing Library for Svelte | Tests behavior and accessible roles instead of implementation details |
| Browser/visual tests | Playwright, Chromium and WebKit at minimum | Covers actual DOM/CSS, focus, pointer behavior, PWA flows, and screenshot parity |
| Static checks | `svelte-check`, TypeScript, ESLint, Prettier, and Knip | Converts core conventions into repeatable gates and finds unused frontend files/exports; add Stylelint only when CSS ownership changes |

### Build requirement

Node and the pinned package manager are accepted requirements for frontend development,
tests, and production asset builds. FastAPI remains the only production process. It
serves the generated files and does not run Node.

For normal local production use, the owner runs `make build-frontend` before
`bin/baqylau-dashboard.py serve|start`. The daemon does not consume a CI artifact. It
must fail clearly if the local manifest/build stamp is missing or stale. CI runs the
same frozen build and verifies the manifest and referenced files.

This decision replaces the old statement in `docs/testing.md` that Node could never
become a build requirement. Python-only tests can still run when Node is absent.

### Delivery and rollback strategy

Use one served frontend entry, not a legacy/Svelte runtime switch. Develop the rewrite
on its branch and worktree while `main` stays deployable. For parity checks, start the
legacy checkout and rewrite checkout as separate daemons on different ports, seed both
from the same fixtures, and compare their behavior and screenshots.

Cutover is one reviewed change on `main`. Rollback is a documented Git revert followed
by a daemon restart. This project has one owner and one local deployment, so a second
production entry, selection flag, and dual-entry telemetry would cost more than the
faster in-process rollback they provide.

### Options considered

| Option | What it improves | What it does not improve | Decision |
| --- | --- | --- | --- |
| Keep the current frontend | No migration risk or build step | Script order, global state, manual lifecycle, and real-browser coverage stay as they are | Keep only if the rewrite is cancelled before Phase 1 |
| ES modules with checked JavaScript | Removes load-order globals and adds some type checks with a smaller change | Keeps manual DOM ownership and most lifecycle coupling | Valid fallback and a useful reassessment point |
| Svelte 5 with TypeScript | Adds modules, types, components, keyed rendering, and resource ownership | Does not improve the design or CSS during parity | Selected |

The rewrite is for structural benefits first. It does not make the CSS or visible design
better during the parity phases. The owner can stop after the baseline or typed
foundation if the measured benefit does not justify the remaining work.

### Why not SvelteKit

FastAPI already owns the Content-Security-Policy (CSP), API routes, streaming endpoints,
HTML delivery, and deployment. The application deliberately has no CORS middleware, so
a cross-origin JSON mutation cannot pass its browser preflight. The dashboard does not
need SSR or a second server/router abstraction. Plain Svelte and Vite preserve that
boundary.
Reconsider SvelteKit only if server rendering, public indexable pages, or frontend-owned
server routes become actual requirements.

### Why not Tailwind

Tailwind is not inherently incompatible with pixel-perfect work, but adopting it here
would be a redesign of the styling implementation with no user-facing benefit. Its
normal setup includes Preflight, an opinionated base reset that changes defaults such as
margins, borders, headings, lists, and media display. Disabling Preflight avoids that
part, but converting 2,185 lines of carefully tuned CSS into utilities would still
change selector specificity, class composition, and the cascade.

For this rewrite:

- do not install Tailwind;
- do not replace current classes with utility classes;
- do not introduce a component theme or design-token package;
- keep the existing CSS variables, media queries, safe-area rules, and class names;
- introduce scoped component styles only after the component has passed visual parity.

Native CSS is the modern CSS stack here. The current stylesheet already uses custom
properties, grid/flex layout, `color-mix`, responsive and pointer media queries,
safe-area insets, backdrop effects, and detailed touch behavior. The missing piece is
ownership and enforcement, not a new styling vocabulary.

### Why not a chart library

Keep the insights page's hand-built SVG and CSS charts. A chart dependency would change
their DOM, spacing, labels, and rendering without solving a rewrite problem. Preserve
the local `YYYY-MM-DD` calculation because it must match the server's SQLite
`date(..., 'localtime')` keys.

## Non-negotiable compatibility contract

The rewrite is complete only when these remain compatible:

1. Visual appearance at the supported desktop, tablet, and mobile viewports.
2. All eleven existing hash shapes, deep links, tab and actor scope, drill-downs,
   launch-watch behavior, and back/forward history rules.
3. API request shapes, error behavior, harness-declared control capabilities, the absence
   of CORS response headers, and the dictation-grant contract. There is no origin
   allow-list, mutation-header guard, or read-only switch in the reviewed code.
4. Global and per-session SSE reconnection, cursor, ordering, and deduplication rules.
   The first global stream waits for the list read and uses its cursor on success; on
   failure it opens from zero and adopts streamed sessions. Do not add polling;
   visibility-triggered refreshes are not polls.
5. Keyboard, mouse, touch, paste, drag/drop, fullscreen, wake-lock, notification, and
   clipboard interactions.
6. Service-worker registration at `/sw.js`, Web Push, installed-app behavior, icons,
   manifest, favicon, and safe-area behavior.
7. The production CSP, including `form-action 'none'`, `frame-ancestors 'none'`, the
   narrow Deepgram and Blob exceptions required by dictation, and the inline-style
   exception required by rendered ANSI colours. Keep `X-Frame-Options: DENY` and the
   `connect-src` exfiltration boundary.
8. Server-side cache policy: HTML and the service worker are revalidated/no-store as
   appropriate. Content hashes replace the current `BOOT_ID` stamp only for generated
   frontend files. Icons, `manifest.webmanifest`, and the icon URLs inside that manifest
   keep their current `BOOT_ID` stamps.
9. Existing supported behavior covered by `tests/test_dashboard_dom.py` until an
   equivalent browser or component test replaces each case.
10. The client audit event names and payload fields, including `client_id`, `device_id`,
    connection state, the boot `hello`/`stale` handshake, optimistic beacons, and
    page-lifecycle delivery.
11. Multi-device application state: drafts, queue, view mode, hidden/muted preferences,
    identity, dispatch sequences, origin echo suppression, and immediate clears.

“Same design” means more than keeping the same colors. The rewrite should retain the
same element order, nesting where selectors depend on it, class names, data attributes,
text, spacing, line wrapping, breakpoints, animation timing, overlays, focus visuals,
and stacking behavior. Intentional accessibility fixes may change semantics without
changing pixels.

## Current frontend inventory

At the reviewed commit, the dashboard has:

- 16 classic, order-dependent JavaScript files loaded by `dashboard/static/index.html`;
- 11,047 lines of JavaScript and 2,185 lines of CSS;
- approximately 646 KB of unminified JavaScript and CSS source;
- a mutable global `S` object used across files;
- hand-built DOM creation, replacement, keyed maps, and event delegation;
- one global session-data `EventSource` and a separate current-session stream;
- a hand-written hash router with eleven URL shapes, five session tabs, actor scope,
  monitor/job drill-downs, and a launch waiting-room state machine;
- a canonical actor identity used by the UI's “agent” scope; there is no separate agent
  identity or `AgentId`;
- a substantial wire-to-view-model mapping layer in `canonicalActorRow` and
  `canonicalSessionMeta`, including compatibility aliases and derived fields;
- a table-driven monitor/job section engine with scope-keyed cache invalidation;
- a newest-first feed window that combines live append, command folding, “load older”
  backfill, adaptive page growth, exhaustion, and top-up on tab switches;
- optimistic gestures with `request_id`, snapshot reconciliation, audit events, and an
  indeterminate outcome in addition to transport success/failure;
- custom modal, composer, command, notification, fullscreen, wake-lock, and presence
  behavior;
- microphone dictation with `getUserMedia`, an AudioWorklet loaded from a Blob URL, a
  short-lived grant, an audio buffer, and a direct Deepgram WebSocket;
- persisted device and view state in `localStorage` and server-backed preferences;
- server-persisted composer/new-session/question drafts, queues, view modes, notification
  and task flags, and hidden directories shared across devices;
- `ResizeObserver` and `visualViewport` behavior for feed density and mobile keyboards;
- client error reporting with page-lifecycle delivery, including `sendBeacon`;
- a client audit contract with a ring buffer, connection snapshots, named event payloads,
  per-load `CLIENT_ID`, persisted `DEVICE_ID`, and a stored Push server key;
- a server catalog-backed slash-command menu with keyboard/pointer race handling and a
  textarea highlight overlay that must remain scroll-aligned;
- fourteen control gestures, including rewind, compact, background, rename/auto-name,
  model/effort selection, question answers, and plan decisions;
- eleven server-declared control capabilities that describe which gestures the current
  harness can offer. The legacy UI reads seven; `close`, `autoname`, `answer`, and `plan`
  are derived but not consumed, which is a separate correctness decision;
- a global-stream boot handshake that reloads the page immediately when a reconnect
  reports a different server build;
- a two-tier pasted-file path: resolve host clipboard names to paths first, then upload
  bytes when the clipboard cannot resolve them;
- a resume picker and launch form whose harness, model, effort, account, attachment,
  and waiting-room behavior come from runtime server data;
- a session-wide errors tab built from the selected-session application snapshot;
- server-supplied upload/rename limits and presence TTL-derived heartbeat timing;
- four feed rails and density modes, notice revision/visibility rules, notification
  retraction, favicon attention badge, page-wide readline/session-cycle keys, accounts
  and context gauges, and load-bearing list-snapshot reconciliation order;
- a load-bearing static app shell in `index.html`, including header control order and
  stable element IDs;
- FastAPI routes that allow-list fixed static filenames and add boot cache-busters;
- a Web Push service worker, not a general offline asset cache;
- a Python-driven Node DOM shim test suite, but no package manager, type checker,
  component test runner, or real-browser visual suite.

### What should be preserved

The current implementation is not simply “old JavaScript.” It contains valuable,
project-specific work that the rewrite must carry forward:

- The CSS is cohesive, responsive, and already based on reusable custom properties.
- Text is normally inserted with safe DOM APIs; comments show deliberate attention to
  markup trust boundaries.
- `app.00a-markup.js` and `app.00b-entries.js` are pure, directly exported, and heavily
  tested. Port them to typed modules with minimal logic changes. Do not replace them with
  a component tree only to avoid one controlled HTML-rendering leaf.
- List and feed updates use identities and patching instead of blindly rebuilding every
  node.
- Streaming, cursor, reconnection, presence, visibility, and mobile viewport behaviors
  encode real operational knowledge.
- The existing DOM tests capture many subtle regressions cheaply.
- FastAPI restricts asset serving instead of exposing an arbitrary directory.

These are migration inputs and acceptance tests, not code to discard without mapping.

## Problems and how the stack resolves them

| Current problem | Evidence/impact | Rewrite response |
| --- | --- | --- |
| Hidden script-order dependency | Sixteen classic scripts share names and assume an exact load order. Moving a function can break runtime behavior without an import error. | ES modules and TypeScript imports make dependencies explicit; Vite resolves and bundles the graph. |
| One broadly mutable global state object | `S` mixes server data, selected session, DOM caches, transient modal state, timers, cursors, and capabilities. Any file can mutate it. | A typed app-state instance, scoped through Svelte context, separates canonical data, derived values, view state, and external resources. |
| Rendering and behavior are tightly coupled | Large functions construct markup, register handlers, mutate state, call the API, and patch DOM in one place. This makes isolated change and testing difficult. | Feature components render markup; typed service modules handle I/O; pure reducers and commands contain domain behavior. |
| Manual DOM synchronization is fragile | Maps and patch functions must keep object identity, DOM order, classes, selection, and stale-node cleanup synchronized by hand. | Keyed Svelte `{#each}` blocks use stable session/entry IDs and declaratively update the exact nodes affected. |
| Lifecycle ownership is distributed | Window/document listeners, `EventSource`s, timeouts, intervals, viewport handlers, wake locks, and subscriptions are installed in many files. Cleanup is easy to miss. | Each external resource has one owner and an explicit teardown. Svelte special elements own global listeners; effects are limited to external synchronization and return cleanup. |
| Files have multiple unrelated reasons to change | `app.05-session.js` is 2,019 lines; composer, new-session, and chrome files each exceed 1,100 lines. Some lifecycle behavior lives in surprising modules. | Feature directories and small components/services establish boundaries and enable local tests. |
| Runtime data is structurally untyped | API/SSE payload shape drift becomes an undefined-property or rendering failure deep in the UI. A current example is the scoped scoreboard: it reads `meta.agent_usage`, but neither `canonicalSessionMeta` nor the API response supplies that field, so it silently receives `{}`. | Generated or maintained wire types, runtime boundary validation, and typed wire-to-view-model translators make the missing field a compile-time or boundary-test failure instead of an empty UI. |
| Escaped HTML has an implicit type contract | Pure browser modules create descriptor, plan, ANSI, diff, and command markup. Safety depends on every interpolation using `escapeHtml` or `safeUrl`, but the result is still an ordinary string. | Port the pure builders with minimal changes, return a branded `EscapedHtml` type, and render it through one reviewed `{@html}` leaf. Test hostile inputs at the builder boundary. |
| Router rules are implicit | Hash parsing, stream selection, title updates, and view cleanup are spread across route/init code. | A small typed hash-router module preserves current URLs while exposing a discriminated `Route` value and one navigation lifecycle. |
| Accessibility depends on generic elements and custom behavior | Custom dialogs and clickable spans lack the complete semantics, focus containment/restoration, and keyboard model supplied by native controls or ARIA patterns. | Semantic Svelte components add roles, labels, focus management, and keyboard behavior while reusing the exact current classes/CSS. |
| Tests do not exercise a real layout engine | The DOM shim catches logic regressions but cannot validate CSS, WebKit, touch/hover media queries, focus, pixels, or browser APIs accurately. | Keep fast unit/component tests and add Playwright behavior and deterministic screenshot comparisons. |
| Static delivery assumes fixed hand-authored assets | The current allow-list and boot query stamping do not naturally support content-hashed chunks and imports. | Vite emits a build manifest; FastAPI renders only manifest-listed entry/CSS/preload tags and serves only contained manifest outputs. |
| No compile-time or formatting gate | Misspelled fields, accidental globals, unsafe null assumptions, and inconsistent conventions are caught late or not at all. | Strict TypeScript, `svelte-check`, focused lint rules, formatting, and CI catch them on each main-branch push. |

## Target architecture

The exact names may evolve, but ownership should look like this:

```text
dashboard/
  frontend/
    package.json
    tsconfig.json
    vite.config.ts
    eslint.config.js
    src/
      main.ts
      app/
        App.svelte
        app-state.svelte.ts
        app-context.ts
        route.ts
      api/
        client.ts
        contracts.ts
        errors.ts
        generated/
      view-models/
        actor.ts
        session.ts
      streams/
        session-data-stream.ts
        current-session-stream.ts
        stream-events.ts
      features/
        sessions/
        session/
          feed/
          scope/
          sections/
          tabs/
        composer/
        command-menu/
        controls/
        dictation/
        new-session/
        dialogs/
        stats/
        chrome/
        attention/
        client-log/
      shared/
        components/
        browser/
        format/
        security/
      test/
        fixtures/
        render.ts
  static/
    index.html             # FastAPI-owned host template; Vite never emits it
    style.css              # unchanged CSS source of truth during parity work
    sw.js                  # remains at the root URL contract
    manifest.webmanifest
    ...icons
    build/                 # generated Vite manifest and hashed assets
```

Avoid a generic “utils” dumping ground. A helper belongs with the feature that owns its
vocabulary; move it to `shared` only after at least two independent features use it.

### Dependency direction

```text
components -> feature state/commands -> API and stream interfaces
     |                  |
     +---- pure view models/reducers ----+

browser adapters -> browser APIs
FastAPI -> Vite manifest and generated assets
```

Components must not call arbitrary URLs or create unmanaged `EventSource`s. API and
stream modules must not reach into the DOM. Reducers must not perform I/O.

### State ownership

| State kind | Owner | Rule |
| --- | --- | --- |
| URL/view | Typed hash router | The URL remains the source of truth for navigable state. |
| Server snapshots | App/feature state | Store normalized or raw typed snapshots; replace them only through named commands/reducers. |
| Derived filters/counts/status | `$derived` | Never duplicate a computable value in mutable state. |
| Local edit buffer and open/closed state | Nearest feature component | Keystrokes and ephemeral disclosure state stay local. A local buffer is not the durable draft truth. |
| Shared application UI state | Server-backed preferences adapter | Drafts, queue, ask answers, view mode, muted/hidden state, and per-directory launch drafts are server-owned and echoed to all devices. Stamp writes at dispatch with a monotonic sequence; suppress the same-page echo by origin. An immediate clear must outrank saves already in flight. |
| Harness control capabilities | Session view model | Build the eleven booleans from the current harness catalog. Missing capability data means unsupported. Components must not infer support from which buttons happen to exist. |
| Client identities and Push key | Identity/storage adapter | `ClientId` is new per page load. `DeviceId` persists in `localStorage` and falls back to `ClientId`. Persist the Push server key so rotation causes resubscription. |
| Pending optimistic gestures | Command/reconciliation state | Key each gesture by `RequestId`, paint it once, and settle it from the later canonical snapshot as confirmed, refuted, or indeterminate. |
| SSE connection/cursor/retry | Stream service | One service instance per stream scope; expose typed events/status. |
| Timers and browser resources | Owning adapter/component | Creation and cleanup stay adjacent. |
| DOM measurement | Small component/browser adapter | Do not store element references in application domain state. |

Create the app state with a factory and pass it through typed Svelte context. Do not use
a process-global singleton. Large API snapshots that are replaced rather than deeply
mutated should use `$state.raw`; fine-grained mutable UI data can use `$state`. Shared
reactive state should be a class or factory in a `.svelte.ts` module, not an unstructured
collection of writable stores.

### Domain identity and session scope

Use distinct `SessionId`, `ActorId`, `EntryId`, `TaskId`, `RequestId`, `ClientId`, and
`DeviceId` types. There is no `AgentId`. The existing UI calls actor-scoped navigation
“agent scope”, but `/a/<actorId>` and `sessionView.agent` both carry an `ActorId`.

Actor scope is currently a client-side view over an unscoped session payload. The feed
shows entries whose `actor_id` matches the selected actor; without an explicit scope it
uses the lead actor. If the lead is unknown, it reports the invariant failure and shows
all entries instead of painting a blank feed. Do not model `agent` as a supported server
query parameter: the current FastAPI entries route does not accept it, and the legacy
`agentQ()` helper has no call sites. Omit that helper from the typed client unless a
separate, tested server/cache contract is deliberately introduced.

A session route owns one of five tabs: `mirror`, `agents`, `monitors`, `jobs`, or
`errors`. It may also carry one actor scope and one monitor/job drill-down. Scope changes
on the same session rebuild the scope-relative stream and reset only the cached sections
whose table entry is marked as scoped. Keep monitors and jobs in one typed section table;
do not duplicate their list/detail engine in separate components.

### API boundary

Use one `request` primitive that:

- accepts a typed path/method/body definition;
- sets headers and serializes JSON consistently;
- treats non-2xx responses as typed errors;
- returns generated wire types only to a typed translator or command boundary;
- supports `AbortSignal` where navigation can make a response stale;
- distinguishes network, HTTP, validation, cancellation, and domain errors;
- reads upload size, rename length, and presence TTL from `/api/application`; never
  hardcode server policy in a component;
- never displays raw server error HTML;
- logs safe diagnostics without credentials, prompt contents, or tokens.

Prefer generating TypeScript contracts from the backend OpenAPI schema in a pinned,
repeatable task. Generated files are read-only. If generation cannot express an SSE
event union, maintain that small union manually next to the stream decoder and test it
against backend fixtures.

Do not add handwritten runtime validators for every same-build JSON endpoint. Generated
wire types plus the required translators are the normal boundary, and captured response
fixtures exercise every field those translators read. Add small runtime checks where a
compile-time type cannot protect the page: the SSE/worklet message discriminant and
minimum reducer fields, `localStorage`, the parsed hash, and other unversioned browser
messages. A translator failure is reported at its owning route boundary.

Generated wire types stop at the API boundary. During parity, port `canonicalActorRow`
and `canonicalSessionMeta` as pure, typed wire-to-view-model translators instead of
renaming their live mapped fields throughout components, descriptors, and tests. Test
every mapping and derived field from captured response fixtures.

Do not port dead aliases. The actor row's `id` field has no reader, while `agent_id` has
eight. Replace both with one `actorId: ActorId` view-model field and update its typed
consumers in Phase 2. This change is internal: visible “Agents” labels, CSS classes, and
data attributes stay unchanged. Defer all other actor/agent vocabulary consolidation to
a separate naming-only change after visual parity.

### Commands and optimistic reconciliation

Every control POST carries the current `request_id` idempotency key. The command layer
must preserve transport failure, accepted/pending, confirmed by snapshot, refuted by
snapshot, and indeterminate outcomes. It must not reduce them to `ok` or thrown error.

The behavior map must cover all current controls: send text, interrupt, background,
close, rename, auto-name, open/apply rewind, compact, model, effort, answer question,
read plan choices, and decide plan. Preserve each request shape, optimistic paint,
reconciliation rule, audit event, queued-versus-immediate result, limit, and failure
text. Reuse the existing rewind, compaction, background, and control E2E features.

Capability support is server data, not a fixed frontend list. Translate the current
harness control set into the eleven `caps` values. Preserve the seven live gates. Phase
0 must file a focused decision for the four currently unconsumed flags (`close`,
`autoname`, `answer`, and `plan`) instead of silently changing them inside a component
conversion. After that decision, add a negative test for every capability in the
resolved contract, including missing capability data.

Treat the client audit channel as a schema. Keep its event names and fields stable unless
the server/audit tools change in the same work. Each batch keeps client/device identity,
connection state, boot identity, and the `hello`/`stale` events. Pagehide delivery stays
with the channel owner.

### CSP and development server

The production CSP is part of the application contract. The production build must use
same-origin external module scripts. It must not require inline scripts. Keep `blob:` in
`script-src` for the dictation AudioWorklet. Keep the Deepgram WebSocket origin in
`connect-src`. Keep inline style attributes available for the existing ANSI and file
markup colours.

The Vite development server needs a development-only CSP allowance for its loopback
script origin and hot-reload WebSocket. The configuration must have these properties:

- the allowance is enabled only by an explicit development setting;
- the development server binds to loopback unless the owner asks for remote access;
- production responses never contain the Vite host or its WebSocket origin;
- an HTTP test checks both the development and production policies;
- a production smoke test opens the built page with the real CSP and fails on any CSP
  console error.

Vite hot reload applies only in explicit development mode. Content hashes version
generated production assets, the hand-authored icons, `manifest.webmanifest`, and its
icon URLs. A daemon restart must not change an installed application's identity URLs.

Asset versioning and live-page compatibility are separate. The global stream's first
`ready` event anchors its `boot_id` and writes the `hello` audit event. If a later
`ready` event has a different value, write `stale` with the old/new IDs and call
`location.reload()` immediately. Do not put correctness behind a toast. A reload keeps
the current hash route and loads the sole frontend entry served by the restarted daemon.

### HTML document ownership

Vite never owns or emits `index.html`. FastAPI keeps the hand-authored host template and
reads the Vite manifest to add same-origin CSS, module, and preload tags. The template
keeps the no-store policy, manifest and icon links, favicon data, document metadata, and
one Svelte mount root.

Svelte owns the visible DOM inside that mount root. This includes the header controls,
view root, modal root, and toast root. Move the existing comments about header order to
the Svelte shell component with the markup they explain.

### Streaming boundary

Streaming behavior is one of the highest-risk areas and must be migrated as a domain
subsystem, not reimplemented ad hoc in each component.

- Decode every message into a discriminated union before applying it.
- Keep cursor advancement monotonic and explicit.
- Make event application idempotent by stable event/session/entry identity.
- On first load, read `/sessionData` before opening the global stream. Prefer the
  snapshot cursor so the first stream frame cannot replay the backlog and cause one
  adoption GET per known session. If that read fails, report it and open from cursor
  zero; recover rows through the existing one-at-a-time adoption path.
- Reconnect from the last confirmed cursor using the current server contract.
- Keep ordering and deduplication in pure, fixture-tested reducers.
- Expose connection state; do not let components infer it from missing data.
- Close streams and cancel retry timers when their route/scope ends.
- Protect against late events from an obsolete session stream.
- Do not add polling. Re-read preferences and the session list only on the current
  visibility transition and other existing explicit triggers.
- Test initial snapshot, failed-snapshot adoption, incremental update, duplicate,
  gap/reconnect, error, route switch, hidden/visible, unchanged boot ID, and changed
  boot ID with immediate reload.

### Routing

Retain the complete hash grammar. Model it as `ListRoute | StatsRoute | LaunchingRoute |
SessionRoute`. `SessionRoute` carries `sessionId`, one of the five tabs, optional
`actorId: ActorId`, and an optional monitor/job drill-down with its task ID. Invalid or
retired tabs fall back to mirror, as they do now.

Cover all eleven current shapes: list, stats, launching, plain session, session tab,
unscoped monitor/job detail, actor scope, actor-scoped tab, and actor-scoped monitor/job
detail. Parse and format each shape in round-trip tests.

Routing also owns these transition rules:

- toggle the body `in-session` CSS class;
- hand header-action ownership back only when leaving session routes; a drill-down must
  not clear it;
- keep an armed launch watch alive when the user navigates away, but mark it quiet so
  completion shows a link instead of forcing navigation;
- unquiet the watch when `#/launching` is entered again;
- consume a completed launch once and use `location.replace`, not hash assignment;
- use `location.replace('#/')` for a stale launching bookmark so the waiting room is
  never a back-button destination;
- rebuild the stream and scoped section caches when the actor scope changes.

Do not add a routing dependency. This grammar and transition state machine are specific
to the dashboard and remain small enough for one typed module.

### Existing presentation-tier proposal

`docs/html/presentation-tier.html` predates the current browser-owned renderer and
proposes a Python description layer shared with the terminal surface. This frontend
rewrite does not move browser markup generation back to Python. Phase 0 must mark that
proposal as superseded for browser rendering or update it to describe only shared
semantic vocabulary. The terminal renderer is outside this rewrite.

## Design-preservation strategy

### 1. Build the shared fixture apparatus, then capture baselines just in time

Phase 0 records shared stream fixtures and makes visual tests repeatable. Each later
route phase captures its own legacy behavior and screenshots immediately before it
converts that route. This keeps baselines current while still assembling the complete
matrix before cutover:

- sessions list: loading, empty, populated, active, disconnected, and error states;
- session feed: running, waiting, completed, long content, command output, and plan;
- composer: empty, multiline, attachment, drag-over, disabled, and error states;
- new-session dialog and all nested/select/preview states;
- confirmation, question, and plan dialogs;
- stats, chrome controls, notification/fullscreen states;
- desktop, narrow desktop, tablet portrait/landscape, mobile, coarse-pointer, and
  no-hover modes.

Use stable data, fixed time/timezone, bundled fonts, disabled animation, a fixed device
scale factor, and the same browser/container image for baseline and candidate. Store the
legacy and Svelte images separately until cutover.

Use the repository's real second-daemon interface for browser fixtures. A Python fixture
builder seeds a temporary `--data-dir` through repository/application APIs from
committed fixture descriptions; do not commit SQLite files. Start
`bin/baqylau-dashboard.py serve` with an isolated `--port`, that `--data-dir`, and a log
inside the temporary directory. Point Playwright `baseURL` at it and always stop the
daemon and remove the temporary directory. API-only cases use the cheaper in-process
server pattern from `tests/test_canonical_http.py`.

### 2. Treat current markup as a compatibility interface

For each feature, write a small parity sheet before converting it:

- current root and child element structure;
- classes and state modifier classes;
- `data-*` attributes and selectors used by tests or event delegation;
- tab order, focus target, and keyboard shortcuts;
- API and SSE inputs;
- timers/listeners/browser capabilities;
- screenshots and expected user flows.

The first Svelte version should produce the same relevant markup and class names. Avoid
“cleaning up” wrappers or renaming classes while CSS selectors still depend on them.

### 3. Keep the stylesheet global during parity

Continue serving the current `style.css` unchanged while the component tree is rebuilt.
Svelte scoped CSS adds generated scoping selectors and increases specificity, so moving
rules into components is a separate, screenshot-gated refactor after functional and
visual parity. Do not combine it with the component conversion.

After cutover, rules may be moved incrementally when all of the following are true:

1. the rule has a clear single component owner;
2. cross-component and state selectors have been mapped;
3. the CSS variables and cascade contract remain unchanged;
4. the complete viewport/state screenshot matrix passes;
5. the old global rule is removed in the same change.

### 4. Compare complete applications from separate worktrees

Do not mount Svelte inside a subtree that legacy code also mutates, and do not serve two
frontend entries from one daemon. Run the legacy checkout and rewrite worktree as two
complete applications on separate ports against equivalent seeded data. Compare the
same scenario and screenshots across them.

Keep `main` deployable while conversion happens on the rewrite branch. Avoid large new
dashboard features during the conversion. If a required production bug fix lands on
`main`, merge or port it to the rewrite branch and refresh only the affected just-in-time
baseline.

## Migration magnitude

This is an application rewrite, not a build-tool change. The legacy source mass shows
where review and parity work will concentrate:

| Phase | Main legacy source replaced | Approximate lines |
| --- | --- | ---: |
| 2 — contracts, view models, shell | `app.00-core`, `app.02-router`, `app.12-shell`, `app.13-init` | 1,150 |
| 3 — list, stats, global stream | `app.04-list`, `app.03-stats`, `app.01-attention` | 1,340 |
| 4 — session view and feed | `app.05-session`, `app.11-chrome`, `app.00a-markup`, `app.00b-entries` | 4,470 |
| 5 — composer, controls, dictation | `app.08-composer`, `app.10-control` | 1,860 |
| 6 — dialogs and new session | `app.07-dialogs`, `app.09-newsession` | 2,020 |

Phase 4 is about 40% of the current frontend. About one quarter of that phase is a typed
port of the two pure markup modules, not a component rewrite. Phase 6 is larger than
Phase 5 and must have a similarly detailed contract.

## Migration phases

Each phase must leave the branch buildable and testable. Phase completion is based on
gates, not calendar time.

### Phase 0 — Shared baseline apparatus and behavior map

- Inventory routes, API requests, SSE event types, global listeners, timers, CSS
  selectors, and all uses of `innerHTML`.
- Give explicit owners to dictation, client logging, `localStorage`, server-backed
  preferences, `ResizeObserver`, `visualViewport`, workers, object URLs, the static app
  shell, and service-worker/Web Push state.
- Record the current CSP, absence of CORS middleware/headers, dictation grant, tunnel
  assumption, and `BOOT_ID` behavior as tested contracts. Do not copy the stale comments
  that claim an origin allow-list, mutation-header guard, or read-only switch exists.
- Map every Python test that reads `dashboard/static`, including the dashboard naming
  gates in `tests/test_canonical_architecture.py`, the named lifecycle files, and the
  asset-reference test in `tests/test_canonical_http.py`.
- Define the two target test tiers and their triggers. Measure their duration and set
  timeouts. The fast tier runs on each `main` push; the full browser tier runs on demand
  and before every route signoff.
- Reconcile `docs/html/presentation-tier.html` with the current browser-owned renderer.
- Map the existing DOM-test scenarios to the phase that replaces each one. Start that
  checklist with the seventeen existing real-module scripts: `accounts`,
  `asksubmit`, `composergate`, `ctxbar`, `dictpcm`, `dictstart`, `expand`, `feedscope`,
  `globalstreamsequence`, `headeract`, `liveness`, `newsession`, `pendingprompt`,
  `sections`, `sessionlifecycle`, `taskorder`, and `viewmode`.
- Add all eleven route shapes, fourteen control gestures, five tabs, actor scope, both
  drill-downs, the history window, shared multi-device state, optimistic reconciliation,
  client-audit vocabulary, and first-connect stream ordering to the behavior map.
- Record the two actor-scope ambiguities as separate legacy decisions: unused
  `agentQ()`/unsupported `?agent=` filtering and the missing `agent_usage` scoreboard
  field. File a focused bug/removal change for each; do not silently reproduce or repair
  either inside a component conversion.
- Record that four of eleven derived capability flags have no legacy reader. File the
  target behavior for `close`, `autoname`, `answer`, and `plan` separately from their
  component ports.
- Add the temporary-data-dir fixture builder, second-daemon Playwright startup, and
  in-process API test tier described above. Capture shared stream sequences now.
- Define the per-route parity sheet and screenshot procedure. Do not capture every route
  screenshot now; each conversion phase captures its legacy route baseline immediately
  before changing that route.
- Record supported browser/OS/viewport versions.

Exit gate: every visible feature and external resource has an owner in the map. A seeded
legacy fixture daemon and shared stream fixtures are repeatable in one pinned Linux
environment. Each later route has a named parity checklist, but its images are captured
just in time. The owner has a short comparison of current defects that the rewrite can
prevent, but defect count is not the only reason for the rewrite.

### Phase 1 — Typed build and delivery foundation

- Add the Svelte/Vite/TypeScript project under `dashboard/frontend`.
- Configure strict checks, linting, formatting, Vitest, Playwright, TypeScript unused
  checks, and Knip for unused files, exports, and dependencies.
- Add one minimal Svelte entry on the rewrite branch. Do not add a runtime entry switch.
- Configure Vite to emit a manifest and content-hashed assets.
- Teach FastAPI to render manifest CSS/module/preload tags.
- Configure Vite with a JavaScript entry and build manifest. Vite must not process or
  emit `index.html`; FastAPI remains the only document owner.
- Serve only files referenced by the manifest or contained within the generated build
  root; retain path-containment and content-type checks.
- Update the browser vocabulary and lifecycle architecture tests to inspect the new
  source root in the same change. A source move must not make a gate match zero files.
- Replace the current document-reference test with a manifest-aware test. Replace the
  app-part filename rule and generated-file `BOOT_ID` substitution deliberately; do not
  leave them as patterns that silently match nothing. Keep the icon and web manifest
  versioning content-based.
- Add the development CSP and prove that production never permits the Vite origins.
- Retain the current index/service-worker cache policy and `/sw.js` URL.
- Add development integration between FastAPI and the Vite dev server.
- Make `make build-frontend` write the Vite manifest and an atomic build stamp over all
  frontend build inputs, including the lockfile, Vite/TypeScript config, source, and the
  global stylesheet. Normal `serve` and `start` validate that stamp and fail with a clear
  build command if it or the manifest is missing or stale. Only explicit Vite
  development mode bypasses the production-artifact check.
- Add Node setup and a frozen package install to CI. Keep the existing Python job intact.
- Add `make lint-frontend`, `make test-frontend`, `make build-frontend`, and an on-demand
  `make test-browser` target to the root `Makefile`. Make `make lint` and `make test`
  include the fast frontend targets after Node becomes required. CI must call these Make
  targets instead of a separate list of package-manager commands.

Exit gate: the rewrite branch serves its one Svelte entry, production assets are hashed
and restricted, normal daemon startup rejects missing/stale output, and static-delivery
and security tests cover missing and malicious paths.

### Phase 2 — Contracts, view models, pure logic, and app shell

- Capture the legacy shell, route-transition, loading, empty, and error baselines for
  this phase immediately before conversion.
- Generate/adapt API types and define SSE discriminated unions.
- Port `canonicalActorRow` and `canonicalSessionMeta` as typed wire-to-view-model
  translators. Keep generated snake-case contracts at the API boundary, drop the dead
  actor `id`, and replace live `agent_id` readers with `actorId`.
- Port the harness-catalog-to-session-capabilities mapping as a typed translator for all
  eleven `caps`; missing catalog/control data produces an all-denied capability set.
- Extract and test formatting, route parsing, event reduction, ordering, filtering,
  cursor, and status logic without Svelte components.
- Implement all eleven route shapes and their body/header/launch-watch transition rules.
  Route tests must cover assign versus `location.replace` history behavior.
- Implement typed app context, route ownership, attention/presence adapters, error
  boundary, client-log adapter, and root shell. Preserve the boot `hello`/`stale` audit
  events and immediate-reload protocol.
- Make Svelte own the complete static shell, including the existing header control
  order, stable IDs, brand mark, modal/toast roots, and the comments that explain
  non-obvious control order.
- Preserve title, favicon, viewport, fullscreen, visibility, and resize behavior.
- Preserve page-wide readline keys and previous/next session cycling.
- Implement `ClientId`, persisted `DeviceId`, Push-key rotation, and storage-failure
  fallback in one identity adapter.

Exit gate: pure logic matches legacy fixtures and the empty/loading shell matches the
legacy screenshots.

### Reassessment gate after Phase 2

Before converting product routes, answer these questions from measurements and the
Phase 2 diff:

1. Did typed contracts, translators, explicit ownership, or the new gates catch real
   defects or remove enough script-order/global-state risk to justify their build cost?
2. Is the local edit-check-build loop fast enough for one owner to use on every change?
3. After the detailed Phase 4 and Phase 6 inventories, is converting the remaining
   approximately 9,700 legacy lines still better than stopping at checked modules?

If the answer to the first or second question is no, or the owner answers no to the
third, stop the component rewrite and keep the ES-module/typed-foundation fallback. Do
not continue only because Phase 2 is already complete.

### Phase 3 — Sessions list and global stream

- Capture the legacy list and stats behavior/screenshots for this phase immediately
  before conversion.
- Convert the sessions list, cards, filters, stats, badges, and selection behavior.
- Use stable session IDs in keyed blocks.
- Migrate the global stream to its single typed service and reducer.
- Open it after the list read and normally at that snapshot's cursor. If the read fails,
  report it, open from zero, and recover rows through streamed-session adoption.
  Preserve the no-polling rule and visibility-triggered preference/list refresh.
- Preserve the `ready` boot-ID handshake, `hello`/`stale` audit rows, and immediate
  reload when a reconnect reports a changed build.
- Verify incremental insert/update/remove behavior rather than only full snapshots.
- Preserve the list-snapshot order: launch resolution, header update, then optimistic
  close reconciliation.
- Preserve the accounts strip and context-saturation gauge with their current limits.
- Port the insights SVG/CSS charts without a chart library and test that client-local
  date keys match the server's `localtime` grouping.

Exit gate: list scenarios pass behavior, ordering, reconnect, accessibility, and visual
tests at all target viewports.

### Phase 4 — Session view and feed

- Capture the legacy session, all five tabs, actor scope, drill-down, and long-feed
  behavior/screenshots for this phase immediately before conversion.
- Convert session route loading, feed sections, entries, status, plan/question output,
  command output, timestamps, and scroll anchoring.
- Implement the five tabs, actor scope, actor list/lead selection, scope-relative stream,
  and the shared typed monitor/job `SECTIONS` table. Reset only scope-dependent section
  caches when `ActorId` changes; errors remain session-wide.
- Preserve actor filtering in the client: selected actor, otherwise lead actor, with the
  current report-and-show-all fallback when lead identity is unavailable. Do not rely on
  an unsupported `?agent=` backend filter.
- Migrate the current-session stream with route-scoped cleanup and stale-event guards.
- Model the feed as a newest-first live window with older backfill. Preserve
  `HISTORY_FETCH = 40`, adaptive request growth up to the current cap, exhaustion,
  retry-until-promised-block-count, top-up on tab switch, folding, and scroll anchoring.
- Keep the four rail assignments and make density modes filter rails, not arbitrary
  entry kinds. Preserve grouping facts that intentionally render no line.
- Port `app.00a-markup.js` and `app.00b-entries.js` to TypeScript with minimal logic
  changes. Keep their pure-function tests and return branded `EscapedHtml` values.
- Render their output through one small reviewed `{@html}` leaf. Do not add a sanitizer
  unless a new boundary accepts already-formatted HTML from outside this trusted module.
- Own the errors tab explicitly. Replace its session-wide rows from the selected-session
  application snapshot; show component, action, local timestamp, and traceback; keep the
  empty state; patch `error_count` in the tab and stats badges; and repaint an open errors
  tab when a later snapshot increases the count. Never actor-filter these rows.

Exit gate: long-running and reconnecting sessions preserve order, scroll behavior,
content, and pixel layout under recorded stream fixtures.

### Phase 5 — Composer, controls, and dictation

- Capture the legacy composer, controls, attachment, and dictation behavior/screenshots
  for this phase immediately before conversion.
- Convert prompt editing, resize/paint behavior, attachments, clipboard, paste,
  drag/drop, keyboard shortcuts, send/queue/interrupt controls, and pending prompts.
- Keep high-frequency input state local; do not route keystrokes through global state.
- Persist composer drafts, queue, ask answers, per-directory new-session drafts, view
  mode, notification/task flags, and hidden directories through the server adapter.
  Stamp each write at dispatch, suppress same-client echoes, and send immediate clears
  that outrank debounced saves in flight.
- Use named async commands with visible pending/failure states and duplicate-submit
  protection.
- Preserve `request_id`, optimistic paint, later snapshot reconciliation, audit tags,
  and indeterminate control results for all fourteen control gestures. Read upload and
  rename limits from the application snapshot.
- Preserve the seven live runtime capability gates from the session view model. Apply
  the separately approved contract for `close`, `autoname`, `answer`, and `plan`.
  Missing capability data denies the action. Test every capability in the resolved
  contract in its off state, including paths such as keyboard rewind that do not start
  from the header button.
- Keep the two attachment paths distinct. A pasted file first sends its names to
  `/api/application/clipboard-files`; insert returned host paths at the caret and fire
  the normal input path. On no match or probe failure, upload the bytes through
  `/api/application/uploads`. The picker and drag/drop always upload. Preserve upload
  limits, server-confirmed image treatment, Blob thumbnails, URL cleanup, caret
  placement, and `{n, resolved}` audit data.
- Convert the slash-command catalog and menu as one accessible combobox component. Keep
  server order, working-directory/harness scope, no-cache-on-failure, row cap/scroll,
  keyboard consumption, nearest-row scrolling, below-only placement, and the pointer
  blur race. Keep the textarea highlight overlay pixel-aligned while typing and scrolling.
- Move dictation to a dedicated browser adapter with one owner for microphone tracks,
  the audio context/worklet, the short-lived grant, the Deepgram WebSocket, buffer
  telemetry, timers, and object URLs.
- Preserve the current Blob AudioWorklet shape unless a bundled worklet passes the same
  CSP and browser tests.
- Test permission denial, no input device, worklet failure, grant failure/expiry,
  WebSocket failure/closure, stop during startup, typing during dictation, final/interim
  splice behavior, lag/backlog reporting, cleanup, and the new-session dictation path.

Exit gate: mouse, keyboard, touch, paste, drag/drop, abort, retry, offline/error, and all
dictation scenarios match current behavior and visuals. No microphone track, audio
node, socket, timer, or object URL remains after stop or component teardown.

### Phase 6 — Dialogs and new-session flow

- Capture all legacy dialog, resume-picker, new-session, retry, and waiting-room
  behavior/screenshots for this phase immediately before conversion.
- Convert confirmations, questions, plans, their persisted drafts/optimistic states,
  selectors, previews, and the new-session form.
- Port the resume picker over `/api/resumable-sessions?working_directory=...`: server-side
  search, newest-first result limit of 25, current selection retention, model/effort/
  account metadata, active state, keyboard navigation, and transcript preview. Do not
  port `RESUMABLE_SCAN = 2000`; it is already an unused Python constant.
- Build the harness, model, effort, and account pickers from `/api/harnesses`, each
  harness catalog, and the account snapshot. Preserve per-harness defaults, supported
  attachments/accounts, required-first-message rules, account limit filtering, hidden
  directories, and per-directory launch preferences/drafts. Never borrow another
  harness's choices while its catalog is absent.
- Treat the launch's `202 Accepted` response as success. Arm and render `#/launching` on
  the click before the slow POST finishes; keep its `{mode, model, effort, account,
  prompt}` display data; on failure, remove only that watch and reopen the form with the
  submitted values and prompt restored.
- Preserve the actual launch-match rule: match `resume_session_id` first; otherwise
  match a new or newly-live session in the submitted working directory against the
  before-launch sets. The returned window ID is stored but no longer read by
  `checkJump`; do not carry that dead sharpening path into the rewrite.
- Reuse the Phase 5 attachment, slash-menu, and dictation adapters in the new-session
  form and verify their different session/staging scopes.
- Add dialog semantics, initial focus, contained Tab/Shift+Tab navigation, Escape
  behavior, background inertness, and focus restoration.
- Retain the exact overlay/panel/button CSS and visible text.

Exit gate: visual baselines are unchanged and dialog keyboard/focus tests follow the WAI
modal-dialog interaction model.

### Phase 7 — PWA/browser integration and full cutover

- Capture the legacy installed/PWA, notification, mobile viewport, and browser-resource
  baselines for this phase immediately before conversion.
- Verify service-worker registration/update, Web Push, installed display mode,
  notification deep links, favicon, manifest, safe areas, fullscreen, wake lock,
  presence, `ResizeObserver`, `visualViewport`, pagehide, `sendBeacon`, and client error
  reporting.
- Preserve notice revision deduplication, visible-and-focused toast rules, clickable
  session notices, done-notification retraction, the favicon asking badge, and Push
  resubscription after application-server-key rotation.
- Derive the presence heartbeat from the server TTL and beat immediately on focus or
  return to visibility. Do not hardcode the cadence.
- Run the legacy checkout and rewrite worktree parity suites against equivalent seeded
  fixtures and accept the complete matrix.
- Merge the single Svelte entry as the cutover. Verify a normal production build and
  daemon restart on the real host. Document and rehearse Git revert plus restart before
  deleting unserved legacy files.
- Check client errors, stream reconnect frequency, command failures, and asset 404s
  during a short owner-selected observation window.
- The service worker is push-only and does not cache frontend assets. Verify that the
  existing worker and Push subscriptions survive the single-entry replacement; no
  special asset-cache rollback is required.
- Delete the unserved legacy scripts at branch cutover. Git history is the rollback
  mechanism; the project does not keep a second, unsupported frontend in the served tree.

Exit gate: all required checks pass and the owner sees no meaningful regression during
the observation window.

### Phase 8 — Post-parity cleanup

Only after cutover:

- verify that legacy JavaScript and DOM-shim cases are absent and that replacement
  coverage remains traceable;
- migrate clearly owned CSS rules into components in small visual-test-backed changes;
- review bundle chunks and dependencies;
- keep syntax highlighting out of the parity rewrite. Record it as an optional later
  feature because it changes feed pixels even though the new build could support it;
- update contributor and deployment documentation;
- update `docs/testing.md` when the Svelte suite becomes active and when the hand-written
  SPA description no longer applies.

Do not schedule product redesign into this phase automatically. Design changes require
their own brief, baselines, and approval.

## Code style and engineering practices

This section is normative for the rewrite. “Must” rules should be enforced by a tool or
review checklist.

### Formatting and static checks

- Use Prettier with the Svelte plugin as the only formatting authority.
- Use ESLint with TypeScript and Svelte support for correctness rules, not formatting.
- Add Stylelint only when CSS starts to move out of the frozen global stylesheet. It is
  not required for the initial parity rewrite.
- Run Prettier checking, `svelte-check`, TypeScript, ESLint, Knip, unit/component tests,
  the production Vite build, manifest/static tests, and backend tests in the fast gate.
  Run Playwright in the full route-signoff gate.
- Configure Knip with the Svelte/Vite/Vitest entry points so it checks unused files,
  exports, and dependencies. Keep any ignore list narrow and explain each framework or
  browser callback that static analysis cannot see; a source move must not make the gate
  inspect zero files.
- Do not accept new compiler, accessibility, type, or lint warnings.
- Pin the Node/package-manager version and commit the lockfile. CI uses frozen installs.
- Pin major dependency versions; upgrade in focused changes with the complete suite.

### Vite and build output

- Use `@sveltejs/vite-plugin-svelte` and a JavaScript/TypeScript entry. Do not make the
  FastAPI-owned HTML template a Vite input.
- Import the frozen `dashboard/static/style.css` from the frontend entry so Vite emits a
  content-hashed copy. Keep that file as the source of truth during parity, but do not
  link the unhashed source from the Svelte production document.
- Enable the Vite build manifest. Import `vite/modulepreload-polyfill` in the entry unless
  the polyfill is explicitly disabled for a tested browser-support decision.
- FastAPI emits manifest assets in the documented order: entry CSS, imported-chunk CSS,
  the module entry, then optional module preloads. Traverse imports once and reject
  missing/cyclic manifest references safely.
- Set and document one browser target from the Phase 0 support matrix. Do not silently
  raise it during a Vite upgrade.
- Treat every `VITE_*` value as public client data. Never put API keys, dictation
  credentials, tokens, or other secrets in it. Type the allowed `ImportMetaEnv` keys,
  validate their string values at startup, and ignore local environment files in Git.
- Keep generated assets out of source modules and never edit them by hand. A clean build
  must recreate the manifest and all referenced files.
- Do not add manual chunk rules, dynamic imports, plugins, or bundle analyzers until a
  measured load or cache problem requires them.

### TypeScript

- Enable `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
  `useUnknownInCatchVariables`, `noImplicitOverride`, `noFallthroughCasesInSwitch`,
  `noImplicitReturns`, `noUnusedLocals`, `noUnusedParameters`,
  `verbatimModuleSyntax`, and `isolatedModules`.
- Use an explicit target of at least ES2015, selected from the browser support matrix.
- Prefer TypeScript syntax that erases to JavaScript. Do not use enums, namespaces,
  parameter properties, or other TypeScript-emitted runtime constructs in Svelte files;
  use unions and `as const` objects.
- Use generated wire types and typed translators for same-build API JSON. Runtime-check
  unversioned inputs: SSE/worklet discriminants and required reducer fields, storage
  values, parsed hashes, and other browser messages.
- Prefer discriminated unions and exhaustive switches for routes, request state, stream
  events, entry kinds, and dialog results.
- Do not use `any`, double casts, or non-null assertions to silence a boundary problem.
  A narrow, documented interop exception must be local and tested.
- Use `type` for unions/value composition and `interface` for extendable public object
  contracts; be consistent within a module.
- Represent identifiers with domain aliases or branded types where mixing IDs would be
  dangerous. Use `ActorId` for both canonical actors and UI “agent scope”; do not create
  a duplicate `AgentId`.
- Model absence deliberately. Do not use empty strings, zero, and `undefined`
  interchangeably as sentinel values.
- Functions crossing feature boundaries declare return types. Small private callbacks
  may rely on inference when obvious.
- Type native wrapper component props with the matching interfaces from
  `svelte/elements` instead of recreating DOM attribute types.
- Type the AudioWorklet/worker entry in a separate WebWorker-aware TypeScript context so
  worker globals do not weaken or conflict with DOM types in the main app.
- Spell out local and parameter names while porting (`textarea`, `section`, `element`,
  and `duration`, not legacy abbreviations). Preserve live exported view-model field
  names during parity except for the explicit actor cleanup in Phase 2. Extend the
  browser vocabulary gate to the new source root in the same change.
- Generated API types are never edited manually.

### Svelte 5

- Use runes mode only. Do not introduce legacy `$:` syntax, `export let`, `on:click`,
  slots, or legacy component APIs.
- Do not enable experimental async Svelte during the parity rewrite.
- Use `$state` only for values that drive a template, `$derived`, or an effect.
- Use `$state.raw` for large API snapshots that are replaced as a whole.
- Compute values with `$derived`/`$derived.by`; do not synchronize derived state through
  `$effect`.
- Treat `$effect` as an external-side-effect boundary. It may connect to a browser API,
  subscription, or imperative library and must return cleanup when it creates a
  resource. It must not be the normal way to update application state.
- Put user-triggered work in named event handlers/commands.
- Use `createSubscriber` when several components must observe the same external source.
  Do not use an effect to poll it.
- Use `<svelte:window>` and `<svelte:document>` for declarative global listeners instead
  of installing them from `onMount` or an effect.
- Declare props with a named `Props` type and `$props<Props>()`. Treat props as changing
  inputs; derive dependent values reactively.
- Prefer callback props for child-to-parent events. Avoid event buses.
- Use snippets and `{@render}` for reusable/passed markup. Use `{@attach}` for imperative
  element integration instead of legacy actions. Use arrays/objects in `class` instead
  of the legacy `class:` directive.
- Use `$bindable` only for a control with a real two-way value contract. Prefer a value
  prop plus callback for commands and domain state.
- Use stable, domain-unique keys in every mutable list. Never use the array index as a
  key.
- Use typed `createContext` for app/feature scope. Components must also render in tests
  with an explicit test context.
- Keep a component focused on one visual/behavioral responsibility. Extract when a
  section has an independent state lifecycle, repeated markup, or can be tested as a
  coherent control—not solely because of line count.
- Put runes-bearing reusable state in `.svelte.ts` files. Keep pure logic in normal
  `.ts` files so it is cheap to test.
- Use `$inspect.trace` only for local reactivity diagnosis and remove it before
  acceptance. Put route-level failures in a small `<svelte:boundary>` that reports once;
  do not hide errors with boundaries around every component.

### Effects, listeners, and resources

- Every listener, subscription, timer, observer, `EventSource`, WebSocket, worker,
  microphone track, audio node, object URL, wake lock, or in-flight request has one
  documented owner.
- Resource creation and cleanup stay in the same function or component.
- Never add anonymous global listeners that cannot be removed.
- Prefer `AbortController` for route-scoped requests and combine it with stale-response
  identity checks where needed.
- Fake clocks in unit tests for retries/debounce; do not use arbitrary sleeps.
- Centralize backoff constants and make retry state observable/testable.
- Pause or adapt background work on visibility changes only where current behavior or a
  measured requirement calls for it.

### API, commands, and errors

- Components call named commands such as `sendPrompt` or `interruptSession`, not raw
  `fetch`.
- A command owns validation, request state, cancellation, error mapping, and exactly-once
  UI behavior.
- The command service, not a component, creates and carries `RequestId`. Preserve the
  current idempotency format unless the server contract changes with it.
- A 2xx response does not by itself confirm an optimistic domain result. Keep the pending
  gesture until the canonical snapshot confirms/refutes it, or the control result marks
  it indeterminate.
- The shared-state adapter stamps draft/preference sequences at dispatch time. Immediate
  clears bypass debounce and receive a sequence later than any pending save.
- Always check `response.ok`; never assume JSON is returned on an error.
- Do not catch and ignore errors. Convert expected failures to typed UI state and report
  unexpected failures once at the owning boundary.
- Prevent double submission explicitly while preserving any intentional queue behavior.
- Logs and telemetry must redact secrets and potentially sensitive prompt/session data.
- User-facing error text remains stable during parity unless it is currently misleading
  or unsafe; changes require an updated acceptance fixture.

### SSE reducers

- Event decoders and reducers are pure and exhaustively typed.
- Reducers do not read time, random values, globals, or the DOM. Pass required metadata
  as arguments.
- Applying a duplicate event must not duplicate visible content.
- Cursor advancement happens only after a successfully decoded/applied event according
  to the backend protocol.
- Keep transport reconnect logic separate from domain event application.
- Preserve stable entity identities so Svelte can patch keyed DOM efficiently.
- Add a fixture test for every new event variant before handling it in a component.

### HTML and security

- Render data through Svelte expressions/components by default; Svelte escapes text.
- `{@html}` is forbidden for ordinary content.
- Keep the markup builders pure. They are the only modules allowed to create HTML
  strings. Every content interpolation must use `escapeHtml`; every link target must use
  `safeUrl`.
- Return a branded `EscapedHtml` type from the builders. Only one reviewed leaf
  component may render that type with `{@html}`.
- Test hostile text, hostile URLs, ANSI controls, malformed markdown, attributes, SVG,
  event-handler text, and broken nesting directly against the builders.
- Add a sanitizer only if a future API accepts formatted HTML from an external trust
  boundary. It is not needed for the current pure browser-owned builders.
- Never create HTML strings to render icons; import SVG as a component/asset or express
  it as Svelte markup.
- Do not place tokens, credentials, or unescaped user content in URLs, HTML, logs, or
  error telemetry.

### CSS and visual parity

- `dashboard/static/style.css` is frozen during the component parity phases except for
  the minimum rules needed to preserve an accessibility-semantic element swap.
- Reuse existing custom properties. New color, spacing, radius, typography, layer, or
  motion values require an appropriate token; do not scatter literals.
- Do not use Tailwind, CSS-in-JS, runtime style objects, or a component theme system.
- Do not use `!important` to resolve migration-specific ownership problems.
- Preserve selector specificity and source order during parity.
- Use classes for discrete state and CSS custom properties for genuinely dynamic numeric
  values. Avoid constructing style strings.
- Keep the current controlled inline-style exception for ANSI and file-operation colours.
  The pure markup builder owns these values, and the CSP permits them.
- Scoped component styles must not reach into another component. Pass styling hooks as
  CSS custom properties when a parent legitimately configures a child.
- Include `:focus-visible`, coarse-pointer/no-hover behavior, reduced-motion behavior,
  safe-area insets, dark/light behavior if present, and disabled/busy states in review.
- A CSS refactor is not “non-visual” until the screenshot matrix proves it.

### Accessibility without redesign

- Use native buttons, links, inputs, selects, and dialogs where their behavior matches
  the control. Reset their browser appearance with the existing class when necessary to
  preserve pixels.
- Every interactive control has an accessible name without relying on placeholder text
  or icon shape.
- Do not put click handlers on non-interactive elements. If unavoidable during an
  intermediate phase, keyboard and role behavior must be complete before acceptance.
- Dialogs contain focus, support Escape when dismissal is allowed, set an initial focus
  target, make background content inert, and restore focus to the opener.
- The five session tabs use `tablist`, `tab`, and `tabpanel` relationships. Only the
  active tab is in the page tab order. Left/Right Arrow moves between tabs; Home/End
  moves to the first/last tab. Use automatic activation only if the panel can appear
  without perceptible delay; otherwise Enter/Space activates the focused tab.
- The slash-command menu follows the editable-combobox/listbox pattern without changing
  its layout. DOM focus stays in the composer. The combobox exposes `aria-expanded`,
  `aria-controls`, and `aria-activedescendant`; each result is an option. Arrow keys
  move the active option, Enter accepts it, Escape closes the menu, and ordinary text
  editing keys retain their browser behavior. Pointer selection must still survive the
  current blur-order race.
- If a control cannot implement the complete ARIA keyboard and relationship contract,
  model it as ordinary navigation rather than a partial tab or combobox pattern.
- Async status and errors use appropriate live-region semantics without repeatedly
  announcing streaming noise.
- Preserve visible focus indicators and verify them in Chromium and WebKit.
- Treat Svelte accessibility compiler warnings as errors and add automated checks, but
  retain manual keyboard and screen-reader smoke checks for critical flows.

### Testing style

- Put the largest amount of logic in fast pure unit tests, fewer tests at component
  boundaries, and a focused set of complete user journeys in Playwright.
- Use `vitest` watch mode for local work and `vitest run` in Make/CI. Share the Vite
  plugin configuration, but keep test-only environment settings explicit.
- Reset modules, mocks, fake time, DOM, storage, server fixtures, and application state
  between tests. No test may depend on another test's page, database rows, cursor, or
  local storage.
- When a runes unit uses effects, create it under `$effect.root`, call `flushSync` only
  when a synchronous assertion needs the pending effect, and always run the returned
  cleanup.
- Test observable behavior. Query components/browser pages by role and accessible name;
  do not use CSS classes that exist only for styling.
- Use Testing Library `userEvent` for component interactions. Assert against DOM nodes,
  not Svelte component instances or internal state.
- Use `data-testid` only when there is no meaningful accessible or domain selector.
- Avoid assertions against complete `innerHTML`; they are brittle and conceal semantic
  intent.
- Component snapshots cannot be the sole proof of behavior or design.
- Visual tests use controlled fixtures and compare whole states plus a few high-risk
  component clips. Review and explain every baseline update.
- Use Playwright locators and web-first assertions. Do not call `isVisible()` and then
  assert its boolean, and do not replace auto-waiting with manual retry loops.
- Mock Deepgram, microphone permissions/devices, Push, wake lock, and other third-party
  or nondeterministic browser boundaries. Browser tests must not call live third-party
  services.
- Test Chromium and WebKit for critical flows; add Firefox where inexpensive. Mobile
  emulation must include coarse-pointer/no-hover and relevant viewport/safe-area cases.
- Retain the current DOM tests until a coverage map links each one to a replacement.
  Never delete a regression test merely because the implementation changed.
- A test must fail for the intended regression; validate new parity tests against the
  legacy behavior before relying on them for the rewrite.
- Retain a Playwright trace on the first retry of a failed CI test, not for every passing
  test. Use the trace for DOM, console, and network diagnosis.

### Comments and documentation

- Use ASD-STE100 Simplified Technical English for new comments and documentation, as
  required by the repository. Keep existing user-visible text unchanged during parity.
- Comments explain protocol rules, browser quirks, safety constraints, or why apparently
  simpler code would be wrong. Do not narrate obvious syntax.
- Keep streaming and PWA invariants near their owner and link to backend protocol tests.
- Add a short README to `dashboard/frontend` with commands, architecture boundaries,
  development integration, generated-file rules, and visual-baseline workflow.
- Record material architecture changes as small decision notes rather than allowing this
  plan to become silently stale.

## Verification and cutover gates

### CI shape

The current workflow runs after a push to `main` and on manual dispatch. It has no pull
request trigger. The plan must not call it a pre-merge gate.

Use two unconditional tiers:

1. **Fast, on each `main` push.** Keep the Python test/lint jobs on Ubuntu and macOS.
   Install the pinned Node/npm toolchain in both jobs and use the repository Make targets
   for formatting, TypeScript, `svelte-check`, ESLint, Knip, Vitest, production build,
   and manifest/static-serving tests.
2. **Full, on demand and before each route signoff/cutover.** `make test-browser` starts
   the seeded fixture daemon and runs Chromium plus WebKit behavior, accessibility, and
   screenshot tests on the baseline platform. CI runs this tier on macOS because the
   committed screenshots are platform-specific; Linux still runs the fast frontend and
   complete Python tiers.

Phase 0 measures both tiers and sets timeouts; it does not make either trigger
conditional. A required check must have a real Make command and a named trigger.

### Cutover definition of done

The Svelte frontend can become the default only when:

1. Every item in the behavior map is implemented or explicitly retired with approval.
2. The full visual matrix has no unapproved differences.
3. Existing URLs, API calls, SSE behavior, and PWA/Web Push flows remain compatible.
4. Dialogs and critical controls pass keyboard/focus review.
5. No unreviewed raw-HTML path remains.
6. Static serving remains allow-listed/contained and cache behavior is tested.
7. Normal daemon startup rejects a missing/stale build, and the changed-boot-ID reload
   protocol passes in the production build.
8. Git revert plus daemon restart has been documented and rehearsed on the real host.
9. The repository documents local development, tests, builds, deployment, and baseline
   updates.

### Performance budgets

Use two numeric gates. Before Phase 3, record the legacy value and accepted candidate
maximum for:

- time to the first usable sessions list on a representative seeded dataset;
- feed append/patch cost for a recorded long session.

Record compressed transfer/request count, input latency, retained resources, and
reconnect/duplicate behavior as diagnostics. Promote one to a gate only when a measured
problem justifies it; do not publish budgets nobody runs.

Use stable keys, `$state.raw` for large replace-only snapshots, and measured
virtualization only if the current real datasets require it. Do not add virtualization
preemptively because it can change scroll anchoring and layout.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| “Cleanup” changes the DOM/cascade during conversion | Freeze CSS, document each feature’s DOM contract, and require screenshot parity before and after. |
| Rewritten streams reorder or duplicate content | Port captured sequences into pure reducer tests before wiring components. |
| Svelte reactivity proxies large server payloads unnecessarily | Use normalized identities or `$state.raw` snapshots; profile long sessions. |
| Scoped CSS subtly wins specificity | Keep CSS global through cutover and migrate rules only in separate visual-gated changes. |
| Accessibility semantics change default styling | Apply existing classes and explicit appearance/reset rules; accept semantic changes only with zero unapproved pixel delta. |
| Vite output bypasses the current static allow-list | Resolve assets from the build manifest, enforce build-root containment, and test traversal/cache behavior. |
| The long-lived rewrite branch misses a production fix | Keep `main` deployable, merge or port each relevant fix promptly, and refresh only its affected just-in-time baseline. |
| Browser tests become flaky | Freeze time/data/animations/fonts/environment, use role-based waits, and never use arbitrary sleeps. |
| The rewrite becomes a redesign | Track visual changes separately; parity changes contain no product styling work. |
| Dependency churn creates maintenance work | Keep the dependency set small, pin majors, use a lockfile, and upgrade independently of feature migration. |

## Research basis

The recommendations above were checked against current primary documentation on
2026-08-24:

- [Svelte best practices](https://svelte.dev/docs/svelte/best-practices): runes mode,
  limited `$state`, `$state.raw` for replace-only large data, `$derived` for computed
  values, effects as an escape hatch, keyed lists, typed context, and CSS custom
  properties.
- [Svelte TypeScript support](https://svelte.dev/docs/svelte/typescript): component
  TypeScript and compiler settings such as `verbatimModuleSyntax` and
  `isolatedModules`.
- [Svelte scoped styles](https://svelte.dev/docs/svelte/scoped-styles): generated scope
  classes affect specificity, which is why CSS extraction is delayed until parity.
- [Svelte testing](https://svelte.dev/docs/svelte/testing): Vitest for Vite projects,
  component testing, Testing Library examples, and Playwright for end-to-end tests.
- [Svelte `{@html}`](https://svelte.dev/docs/svelte/%40html): untrusted HTML must be
  escaped or sanitized before rendering.
- [Vite backend integration](https://vite.dev/guide/backend-integration.html): manifest
  generation and backend rendering of hashed CSS, entry, and preload assets.
- [Vite environment variables and modes](https://vite.dev/guide/env-and-mode.html):
  client-exposed `VITE_*` values are bundled as public strings, so secrets stay on the
  backend and allowed variables are typed and validated.
- [TypeScript `strict`](https://www.typescriptlang.org/tsconfig/strict.html) and
  [`noUncheckedIndexedAccess`](https://www.typescriptlang.org/tsconfig/noUncheckedIndexedAccess.html):
  stronger correctness checks and explicit handling of unchecked indexed values.
- [TypeScript `noUnusedLocals`](https://www.typescriptlang.org/tsconfig/noUnusedLocals.html):
  compiler errors for unread local declarations; this complements the project-level
  unused export/file scan.
- [Knip analysis model](https://knip.dev/explanations/how-knip-works): entry-point-based
  unused file, export, and dependency analysis, including Svelte source support.
- [Vitest guide](https://vitest.dev/guide/): Vite-native unit-test configuration and
  distinct watch/local and run/CI modes.
- [Testing Library guiding principles](https://testing-library.com/docs/guiding-principles/):
  tests should exercise rendered behavior through the DOM instead of component
  internals.
- [Playwright best practices](https://playwright.dev/docs/best-practices): isolated
  tests, resilient user-facing locators, web-first assertions, and trace-based failure
  diagnosis.
- [Playwright visual comparisons](https://playwright.dev/docs/test-snapshots): controlled
  screenshot-baseline comparisons.
- [WAI modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/):
  focus containment, Escape behavior, initial focus, roles/properties, and restoring
  focus to the invoking control.
- [WAI tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/): tab/list/panel
  relationships, roving focus, activation, and arrow-key behavior.
- [WAI combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/): editable
  combobox/listbox relationships, keyboard interaction, and active-option focus while
  DOM focus remains in the input.
- [Tailwind Preflight](https://tailwindcss.com/docs/preflight): the base reset changes
  browser defaults, supporting the decision not to introduce it during a no-design-change
  rewrite.

## First implementation change

The first implementation change should contain only Phase 0 artifacts: the shared
behavior/ownership map, deterministic fixture builder, second-daemon browser harness,
captured stream sequences, test-to-phase map, and parity-sheet procedure. It must not
contain Svelte components or months-early screenshots for every route. Each later phase
captures its route's legacy visual baseline immediately before changing that route.
