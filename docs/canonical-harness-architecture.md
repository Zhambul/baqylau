# Canonical harness architecture

Status: **IMPLEMENTED**, then partially superseded on 2026-08-15 by
[recorder-interpreter.md](recorder-interpreter.md): session discovery/recognition,
hook intake, checkpoints, lifecycle, and the observation scheduler described below
were replaced (wrappers register sessions at launch; hooks only record; the one
interpreter translates and reacts; positions derive from the evidence itself).
The canonical event model — raw evidence, translation verdicts, idempotent
canonical facts, provenance, the cursor — is unchanged and this document remains
authoritative for it.

This is a standalone proposal derived from the current implementation and the
requirements below.

## Current implementation status

Completed and enforced by executable tests:

- transactional raw evidence, translation decisions, canonical events, and
  provenance in one event database;
- one harness-neutral domain, projection layer, application composition root,
  activity API, and SSE cursor model;
- Claude Code and Codex discovery, parsing, translation, launch, lifecycle,
  control, catalog, usage, and terminal probing behind the same plugin ports;
- plugin-owned Claude foreground streaming, OTLP usage, memory, status-line
  usage, and account behavior;
- dashboard presentation and focused/global application snapshots with no
  native transcript or terminal-record semantic reader;
- one canonical production terminal activity process, scoreboard process, and
  tab-state projection;
- raw-to-canonical audit inspection through `bin/baqylau-audit.py`;
- deletion of the mixed drawing model, old provider fan-out, old semantic HTTP
  routes, native formatter/streamer shims, and duplicate audit CLI;
- a synthetic third harness proving that existing ports reach ingestion,
  projection, terminal presentation, dashboard presentation, SSE storage, and
  raw/canonical audit without a production harness branch;
- dashboard DOM/interaction parity tests for the unchanged browser surface;
- direct browser consumption of typed session-list facts, including canonical
  tab state, statistics, usage, context, repository state, and terminal state;
- browser import/vocabulary enforcement: executable dashboard code contains no
  concrete harness names and no `sid`, `ses`, `op`, or `ops` replacement names;
- explicit resume-only launch semantics; the unused `continue_latest` branch is
  absent from the contract, API, plugins, browser, and tests;
- canonical model references carry native identity, plugin-owned display text,
  and catalog selection identity, so browser code never parses harness model
  identifiers;
- the current canonical schema requires that model shape and rejects older event
  databases at startup; there is no decoder adapter or migration. The one
  sanctioned evolution is ADDITIVE: a payload field with a declared default is
  optional on decode (rows written before the field existed keep decoding, the
  default fills in), while extra fields stay rejected — so a new optional fact
  needs no schema-version bump and no stored-row rewrite;
- canonical context reports drive both harnesses, including Claude assistant
  usage and generic latest-model association;
- native `EventSource` reconnection is the only browser reconnect mechanism and
  the server makes `Last-Event-ID` authoritative;
- Claude-owned discovery canonicalizes native paths before recognition, so
  symlinked project directories cannot register one session twice;
- ordinary SSE client disconnects are ignored at both request and server-loop
  boundaries, while unexpected server exceptions still surface;
- Codex session discovery accepts both object-valued subagent metadata and
  ordinary string-valued native sources without terminating observation;
- Codex `SessionStart` hooks synchronously emit the same stable canonical
  session and lead-actor facts as rollout metadata, so launch visibility and
  terminal lifecycle do not wait behind historical file observation;
- native sources checkpoint bounded batches, and registry discovery
  interleaves newest-first sessions from each harness so one large history
  cannot starve a newly launched session;
- the Codex plugin indexes native parent metadata once per rollout-set change
  and rotates one child rollout source per parent observation pass;
- Claude lead-session facts come from the first native root record carrying a
  working directory, never from position-zero queue plumbing; hook and
  transcript observations therefore encode identical startup facts;
- canonical event pages cache by each session's own latest cursor, append only
  its newly committed cursor range, and batch-load provenance with event pages;
- dashboard list projections are retained per session revision while terminal
  and repository state remain live reads;
- the live dashboard list projects only the 20 most recently started canonical
  sessions, while Insights owns all-time aggregates; current sessions and
  focused activity never wait behind the complete historical event store;
- the test environment selects the inert terminal frontend by default, so
  lifecycle verification cannot create panes in a user's live terminal;
- terminal pane gestures invoke the harness-neutral `app/terminal_panes.py`
  command through an explicit Python interpreter; Codex never enters the
  Claude Code plugin to toggle or resize its panes;
- command-line Codex opens panes under a generic process identity immediately,
  then adopts the native session identity in place when Codex creates its root
  rollout on the first prompt; pane processes are not restarted or duplicated;
- the terminal activity policy hides lead user, assistant, reasoning, and
  system activity already visible in the native harness pane, while retaining
  lead actions and every child actor's transcript, reasoning, and actions;
- the dashboard defaults to the lead actor's complete canonical activity,
  including system messages, and an explicit actor selection returns only that
  actor's activity through the same API and SSE scope;
- native synthetic instructions, environment context, and other injected
  prompts translate to canonical `system` messages instead of presentation
  heuristics;
- session finish closes both panes, clears tab appearance, and removes the lead
  window's session tag so a later harness started in the same shell is not
  mistaken for a nested session;
- strict current-schema preferences with no unreadable-data defaults or unused
  durable rename compatibility store;
- frozen terminal mirror parity at 60 and 100 columns, including layout,
  wrapping, glyphs, RGB colours, backgrounds, and emphasis.
- complete repository verification: Python/JavaScript contract, parity,
  architecture, projection, HTTP, and plugin suites pass; Python lint and every
  production JavaScript syntax check pass.

The implementation is active through the installed LaunchAgent. The executable
DOM and interaction parity suite and the frozen terminal goldens verify the
unchanged UI contract. Final visual inspection is intentionally left to the
user; screenshots are not an implementation gate.

Python signatures are illustrative but intended to be implementable. They
assume `from __future__ import annotations` and Python 3.12.

## 1. Objective

Baqylau should be a harness-neutral observation and control platform. Claude
Code and Codex should plug into the same runtime and be visualized through the
same semantic abstractions. The architecture must leave a complete, stable
extension surface for future agent harnesses.

The defining flow is:

```text
Claude Code plugin ──┐
Codex plugin ────────├
future plugins ────────┘
                        ↓
                 canonical facts
                        ↓
             semantic read models
                  ┌─────┼─────┐
                  ↓     ↓     ↓
           terminal dashboard/SSE other consumers
```

Each harness owns how its native signals are discovered, translated, and
controlled. Shared code owns the meaning of facts after translation. Each output
surface owns its own presentation.

The practical acceptance criterion is:

> Adding a future harness plugin must not require adding a harness branch to the
> domain, runtime, terminal presenter, dashboard presenter, or SSE transport.

Registration and packaging metadata may change to install the plugin. Shared
behavior must not.

## 2. The problem in v1

V1 fuses presentation with semantics. Its common interchange format is a
stream of mixed rendering records. Those records carry both terminal drawing
instructions and facts
that later consumers need to understand:

- glyphs and RGB values;
- ANSI/pre-styled strings;
- gutters, wrapping, panels, and copy-link instructions;
- `web`, `note`, `bubbled`, and `chrome` consumer-routing flags;
- `act`, `who`, `tags`, `src`, `mid`, and `assignment` semantic fields promoted from
  previously baked strings.

This evolution is visible in the currently named
[core/ops.py](../core/ops.py): every time the web
dashboard needed to understand a block rather than merely display it, another
piece of meaning had to be recovered from a glyph/colour/string or promoted to
a structural field.

The dashboard consequently has to combine two incompatible sources:

```text
rendering-record cursor + harness transcript byte cursor
                      ↓
              timestamp/anchor merge
                      ↓
               server-rendered items
```

The live implementation of that merge is in
`dashboard/http/sse.py:sse_session`; its backlog equivalent is in
`dashboard/read/mirror.py`. This makes a presentation artifact part of the
read model and forces the web consumer to know old terminal conventions.

The terminal renderer itself is useful. The problem is that its rendering
records became the closest thing v1 has to a shared model and therefore became
the source from which other consumers infer meaning.

### 2.1 Where current mixed fields go

The target does not rename the existing mixed fields. It separates them by
ownership:

The abbreviated names in this diagnostic table describe code being removed;
none is part of the target API.

| Current field or concept | Target owner |
|---|---|
| `t`, glyphs, RGB, ANSI, `outer`, `bg`, `lex`, line numbers | Terminal presenter |
| `web` | Removed; web scope is a semantic actor/task query |
| `note` | Removed from shared storage; each presenter owns its wording |
| `bubbled` | Removed; one semantic message/activity is projected once per surface |
| `chrome` | Terminal-presenter decision about useful host scaffolding |
| `act` | Canonical event type or operation/activity category |
| `who` | Canonical `actor_id` and actor metadata |
| `tags` | Canonical model, effort, context, and usage facts |
| `src` prefixes | Canonical actor/parent/task relationships |
| `g` | Canonical message, operation, or task identity |
| `lk` | Per-surface copy-affordance policy |
| `mid` | Canonical message/subject identity |
| `assignment` | Canonical actor-assignment and parent-turn relationships |
| `web`/`note` values parsed from strings | Deleted by the refactor |

### 2.2 Existing seams that make the change practical

The current code already has the right outer shape:

```text
Claude transcript line -> transcript.parse_line -> Renderer.handle_line -> rendering records
Codex rollout line     -> rollout.parse         -> Renderer.feed_rollout -> rendering records
```

The proposal inserts canonical translation between each native parser and its
presenter. It does not require replacing native parsers first.

The current audit also already records complete hook payloads through
`core.audit.hook_event`. The missing equivalent is complete raw capture at the
Claude transcript and Codex rollout line pumps. Those two complete-line pumps
are the natural raw-evidence chokepoints; audit calls do not need to be spread
through every formatter.

### 2.3 Second code audit: previously easy-to-miss responsibilities

A second repository-wide audit found responsibilities that cannot be left
behind merely because they do not look like transcript rendering:

| Current responsibility | Current location | Required target |
|---|---|---|
| semantic counters mixed with stored drawing records | `core/state.py`, `core/ops.py` | canonical usage/activity projections; the drawing-record table is deleted |
| raw hook evidence plus synchronous native stdout rewrites | hook entry scripts and Claude hook helpers | `HarnessHook`; exact input is audited before stdout is returned |
| foreground command wrapping performed by a pre-tool hook | `plugins/claude_code/cmd_pre.py` | stays in the Claude plugin and returns native stdout through `HookIntake.output` |
| main, child, sidecar, and cross-harness actor discovery | Claude substreams and Codex watcher/nested modules | plugin-owned `HarnessEventSource`; explicit actor/session relationships only |
| model-refusal changes and account migration | transcript scanning and Claude account helpers | canonical model/account facts plus Claude-owned controls; not an architecture fallback |
| copy/view expansion content | drawing records and dashboard copy routes | uncapped canonical content addressed by `content_reference` |
| terminal draft and suggestion screen parsing | dashboard and harness TUI helpers | owning plugin's `HarnessTerminalProbe` |
| tab colour, liveness, running jobs, monitors, and scoreboard timing | tab/status modules plus dashboard polling | canonical projections combined with explicit terminal application state |
| launch, continue, resume, attachments, model, effort, and account selection | dashboard launch code and provider fan-out | `HarnessLauncher` and `HarnessCatalog` in the owning plugin |
| question, permission, plan, rewind, compact, interrupt, close, and rename effects | dashboard verb handlers and native TUI modules | typed controls routed by `HarnessControlService` |
| session and activity SSE plus application notifications | `dashboard/http/sse.py` | one canonical activity cursor; one complete non-domain application snapshot remains separate on the same connection |
| notification, repository, drafts, queue, preferences, extensions, OTEL, and operational errors | dashboard/application modules | named application services; none is forced into canonical harness events |

This table is a deletion checklist, not a request for adapters. A responsibility
is complete only when its old semantic reader/writer is removed. The target has
no canonical-to-drawing-record converter, old-dashboard-item converter, dual
cursor merger, provider fan-out, or old-schema reader.

### 2.4 Third code audit: omissions found after the first slice

A further audit against the replacement code found these additional
requirements. They are part of the design, not optional cleanup:

| Omission found in code | Required correction |
|---|---|
| a top-level OTEL plugin understands Claude Code metrics and accounts | keep it under `plugins/claude_code/otel`; share only transport code that contains no Claude vocabulary |
| harness-named scripts in `bin/` retain implementation behavior | replace them with package-installed plugin entry points; no Claude/Codex compatibility shim remains in `bin/` |
| `plugins/__init__.py` and `plugins/host.py` remain a second plugin system | delete their fan-out/host APIs after callers use `HarnessPlugin`; `plugins/__init__.py` becomes an empty package marker |
| old dashboard readers, semantic SSE, and `dashboard/opshtml` still infer meaning from drawing records | delete them after canonical and named application routes cover their responsibilities; they are not a fallback |
| the first `DashboardItem` used `group_id` plus HTML fragments | one item becomes one complete top-level dashboard node; the presenter emits the whole card or bubble and the browser reconciles only by `item_id` |
| the first terminal presenter emits only a simplified visual subset | replacement is incomplete until canonical input reproduces the frozen terminal output exactly |
| `HarnessEventSource.run` can mean an endless tail and block other sources | call it `drain`; it performs one finite pass and `ObservationRunner` owns repetition |
| `HarnessCatalog` has six methods although its only consumer needs one snapshot | use `read(context) -> HarnessCatalogSnapshot`; empty tuples express unsupported sections |
| `/api/harnesses` calls a catalog merely to discover capabilities | declare capability metadata in the plugin descriptor and read it without native I/O |
| hook controls execute while their result is discarded | synchronous hook behavior returns required output/result explicitly; a failed effect is not silently reported as success |
| canonical global SSE imports the old notification broker | emit complete application snapshots from named application queries; no replacement broker or event bus is introduced |
| replacement code/tests retain `sid`, `ses`, `fg`, `g`, `t`, and `opshtml` | use complete names in replacement code; delete old modules rather than wrapping their vocabulary |

The audit also found accidental `message_id` and `reply_to_message_id` fields on
`FileAccessed` in an earlier draft. A file access has no message reply
relationship. Those fields are removed; reply identity belongs to
`MessageCreated` and the dashboard message item only.

### 2.5 Fourth code audit: replacement-path gaps

The next audit compared the proposal to the running replacement slice, not only
to the old implementation. It found the following unfinished work:

| Gap proven in the replacement code | Required correction |
|---|---|
| `_CanonicalMixin` still composes with the old GET, POST, and SSE semantic mixins | move the named application routes that the unchanged UI still needs, then delete the old semantic mixins; the final `Handler` has one route implementation |
| the browser still calls old session, command, account, copy/view, and state endpoints beside canonical routes | rewrite its data functions to the resource API in section 15 while preserving the exact DOM, labels, controls, and interactions |
| canonical `content_reference` reaches HTTP but no browser interaction consumes it | bind the existing copy/view affordance directly to `GET /api/content/{content_reference}`; do not recreate a view stash or drawing-record lookup |
| the canonical global stream still imports `dashboard.notify.BROKER` | read complete global application snapshots from named services and delete the broker dependency; do not replace it with another broker |
| `DashboardPresenter` still imports markdown rendering through `dashboard.opshtml` | move the presentation-neutral markdown renderer under the dashboard presenter package, then delete the legacy drawing-record HTML modules when their last callers are gone |
| reasoning was emitted as an unclassified `<div class="reasoning">` | present it through the existing message surface and classify it explicitly; visibility must never depend on an unknown HTML class |
| hook controls were executed while their results were discarded | a synchronous hook fails unless every requested control is acknowledged; rejected or indeterminate work cannot return successful native output |
| Claude `PreToolUse` command rewriting and its synchronous stdout still live only in the old hook path | implement the existing behavior inside `plugins/claude_code/HarnessHook`; the shared hook service only ingests, executes typed controls, and returns bytes |
| `TerminalPresenter` and `TerminalRenderer` are test-only and reproduce only a small visual subset | connect canonical activity to the production terminal surface and reach frozen output parity before deleting the old drawing pipeline |
| source observation asks every plugin for sources for every recognized session | retain the small generic loop because the product supports Codex actors with an explicit native parent inside a Claude-led session; each plugin must prove that relationship itself or return no source, and shared code never guesses from cwd or timing |
| old `plugins/otel`, harness scripts, provider fan-out, drawing storage, and semantic readers remain importable | deletion is part of the implementation, not a later cleanup release |

These corrections do not create a transition architecture. Work may be landed
in source control in small commits, but the installed application is replaced
during one accepted downtime window. There is no live coexistence contract,
compatibility adapter, data converter, backup command, rollback command,
cutover coordinator, or fallback path.

### 2.6 Fifth code audit: production terminal omissions

Auditing the first production terminal connection exposed four more gaps:

| Gap proven in code | Required correction |
|---|---|
| shared pane lookup still used `claude_session`, `claude_mirror`, and `claude_scorebar` | define one harness-neutral terminal tag vocabulary and make all frontends and plugins import it; do not read or write aliases |
| pane startup launched the canonical activity process but the legacy scorebar process | launch a canonical scorebar process beside the activity process; both receive only `session_id` |
| `ActivityStatistics` omitted the per-tool counts shown in scorebar row five | project counts by native operation name as semantic facts and let each presenter select and style them |
| active duration survived only as a mutable legacy sidecar counter | reconstruct lead-active intervals from session, prompt, turn-finish, turn-abort, and session-finish facts |
| Codex hook intake recorded `SessionStart` but declared no lifecycle implementation | implement the existing `HarnessLifecycle` port inside `plugins/codex/`; shared hook code remains branch-free |
| lifecycle implementations had no injected terminal port and therefore re-entered `split.py` and `session.py` | inject `SessionLifecycleContext`; application code owns pane mechanics and plugins retain only native applicability decisions |
| tab color still depended on the legacy tab database and native dispatchers | fold one canonical `TabState` and map it to the unchanged palette in the terminal presenter |
| the pane keybinding entry still imported the state-database lifecycle manager | reduce it to `toggle`, `grow`, `shrink`, `reset`, and `setpct` calls on `ApplicationTerminal`; session hooks never enter that command module |

These are current-product requirements, not generalized recovery machinery.
The canonical scorebar does not poll old counters, emit drawing records, read
old tag names, or invoke the legacy executable when a projection is absent.
The remembered Claude Code pane width has one current-schema plugin-owned
database keyed by working directory. It imports no legacy files or tables.

### 2.7 Sixth code audit: ingestion ownership, liveness, and SSE

The next repository audit followed the running processes rather than individual
classes. It found ownership mistakes that the earlier package-level audit did
not expose:

| Gap proven in code | Required correction |
|---|---|
| both `terminal_process.py` and `scoreboard_process.py` drain the same native sources | exactly one application observation runner drains sources; terminal, scoreboard, dashboard, and SSE are read-only consumers |
| two drainers can load the same file checkpoint and later commit positions out of order | remove consumer-owned draining instead of adding leases, compare-and-swap checkpoints, recovery coordinators, or retry arbitration |
| `EventObserver` exists but no owning application lifecycle starts its repetition | the composition root creates one `ObservationRunner`; the application server starts and stops it with the process |
| Codex opens panes on `SessionStart` but has no replacement-path liveness source that proves process death, emits `session.finished`, and closes the panes | add one Codex-owned liveness source; the common application delivery service maps canonical session start/finish facts to the owning plugin's lifecycle port |
| canonical session SSE chooses `after_cursor` before `Last-Event-ID` | on reconnect, `Last-Event-ID` is authoritative; `after_cursor` is used only for the first connection |
| canonical session SSE emits activity only, while drafts, dialogs, preferences, terminal input, and other application state still change independently | emit one complete session application snapshot when that snapshot changes, beside canonical activity frames |
| a process-local `DashboardEventStream` drops events when a subscriber queue fills | delete it; application snapshots are computed from current named state and therefore need neither replay queues nor overflow behavior |
| the global and session streams still imply a growing registry of application event names | use a single `application` frame shape on each stream; its scope determines whether it contains global or selected-session state |
| `/api/harnesses` exposes overlapping `supports_accounts`, `has_catalog`, and catalog-presence booleans | return one `HarnessDescriptor` with `control_names` and `catalog_sections`; section membership is the only catalog capability vocabulary |
| `HookIntake.lifecycle_requests` duplicates the `SessionStarted` and `SessionFinished` facts already committed by the same intake | remove lifecycle requests from hooks; `ApplicationEventDelivery` derives only those two generic lifecycle actions from committed canonical facts |
| `SessionStarted.source_reference` places a transcript/rollout locator in a semantic event | remove it from the canonical payload; keep it only in `RecognizedSession`, source context, session ownership storage, and raw audit |

The observation runner is deliberately concrete and small. It repeats finite
plugin `drain` passes. It is the only file/poll source scheduler for one
running application database. Hook entry processes may still deliver the exact
hook observation synchronously, but they never drain transcript or rollout
files. Starting two application servers against one database is an operator
error and is rejected by the existing singleton application lock; the design
does not add leader election, leases, takeover, or failover.

This also clarifies process termination. Native process detection and the
evidence that proves death are plugin responsibilities. The application owns
only scheduling, canonical storage, and generic terminal cleanup. No shared
watcher checks executable names or guesses from parent processes.

### 2.8 Seventh code audit: boundary leaks and redundant state

Auditing the replacement path again after the observation and SSE work exposed
another small set of design errors. None requires a new subsystem:

| Gap proven in code | Required correction |
|---|---|
| replaying an existing raw event returned the historical `accepted` provenance result and repeated lifecycle effects | preserve historical provenance, but return every replayed output as deduplicated; lifecycle applies only to facts accepted by the current call |
| `accepted_at` and translation `completed_at` reused the harness observation timestamp | sample the storage clock once per new raw observation; keep source `observed_at` as separate evidence |
| `CanonicalApplication` publicly exposed both `EventPipeline` and `ApplicationEventDelivery` | expose only delivery; application callers must not bypass post-commit lifecycle handling |
| moving markdown alone still loaded the drawing-record package initializer | place ANSI escaping, syntax highlighting, and markdown in dashboard-owned modules with no drawing-record or core-palette dependency |
| the shared ANSI renderer recognized a Claude-only copy-link scheme | describe action links through a harness-neutral value and keep the concrete scheme under `plugins/claude_code/` |
| descriptor discovery no longer calls the legacy provider registry, but Python still executes the 1,062-line `plugins` package initializer before any descriptor module | delete the provider fan-out and leave an empty package marker; do not invent a custom module loader to avoid normal package semantics |
| launch selection had an implicit first-row fallback when no plugin declared a default | require exactly one default when any launchable plugin exists; reject zero or multiple defaults at startup |
| global application SSE carried a separate launch-wake notice in addition to the complete session list | delete the notice; the complete session list is already the current source used by the unchanged jump watcher |
| replacement application snapshots initially omitted UI state and encouraged separate polling endpoints | keep only the state each surface consumes: global sessions, usage, notifications and launch preferences; focused drafts, queue, dialogs, view preferences, terminal state, operational errors and memory status; derive repository and badges in dashboard projections and keep presence as notification write state |
| application state is still held in a module singleton because legacy producers reach into it | inject one application-owned state service into producers; the final composition root owns its lifetime |
| catalog `usage_limits` describes only one unscoped set and cannot represent several switchable accounts plus a harness-wide row | use one explicit typed usage-row model whose optional account identity expresses both cases; plugins compute their own rows |
| teammate-message translation changed the canonical actor while the raw evidence remained attributed to the transcript owner | source intake assigns the exact native sender to the raw envelope before translation; the lead has no parent and a teammate has the recognized lead as parent |
| a lead-authored message in a child transcript could make the lead its own parent or redefine it as a teammate | carry `lead_actor_id` in `EventSourceContext`; never infer actor hierarchy from whichever transcript contains the message |
| catalog reads returned an empty snapshot when the plugin declared no catalog | reject the unsupported catalog request directly; absence of a capability is not an empty-result fallback |
| operation progress could become the displayed text without becoming the item's copy/view content | project one operation-content reference anchored at the current canonical cursor; the content service reconstructs composed progress through the same projector instead of pointing at only the final chunk |
| the content endpoint accepted any payload attribute named in its URL | allow only the declared textual fields of canonical payload types; a content reference is not generic reflection over an event object |
| dynamic native usage reads were placed in `HarnessCatalog` and delayed the first global SSE snapshot | keep configuration vocabulary in `HarnessCatalog`; expose current rate limits through `HarnessUsage` and refresh one application-owned `ApplicationUsageState` outside request handling |
| source checkpoints had no session ownership, so deleting a session left an EOF checkpoint that suppressed normal re-ingestion | store `session_id` on every checkpoint and cascade it with the session's raw, translation, canonical, provenance, and actor ownership rows |
| an HTTP handler built a new application graph when its server lacked one | require the server composition root to inject exactly one application; request handling never creates an observer, database graph, or substitute runtime |
| Codex translation labeled every explicitly parented rollout as a native child, including a Codex sidecar inside a Claude-led session | the Codex source factory records `child` or `sidecar` relation in its raw source type based on native lead ownership; translation stays pure and shared code never branches on either harness |
| Claude `SubagentStart` called every actor a child while teammate transcripts emitted the same actor-start identity with role `teammate` | classify `taskKind == in_process_teammate` during Claude-owned hook/source intake and preserve that relation in raw source type; hook and transcript then translate to the identical actor fact |
| composer drafts, queued-message markers, and dialog drafts still used harness-era per-session key/value databases and ad hoc SSE event names | store them in one typed `session_application_state` row keyed by canonical session ID and deliver them only inside the complete selected-session application snapshot |
| monitors and background jobs still polled legacy audit/state routes even though canonical operations already contain execution mode, actor, progress, result, and lifecycle | project typed monitor/job rows from `OperationActivity` inside the focused canonical snapshot; delete the secondary poll and old `/api/session/{id}/monitors|jobs` readers |
| the errors tab still fetched a singular-session legacy endpoint after the focused snapshot and SSE were connected | include typed operational errors in `SessionApplicationSnapshot`; initial page state and the one session SSE now refresh the same error rows and badge, while errors remain operational diagnostics rather than canonical harness facts |
| unreachable singular-session ops/history/backlog/copy/view routes kept the dashboard coupled to drawing records and rendered HTML stashes | delete those routes and their browser listener; canonical activity pagination owns history, while `content_reference` resolves copy/view text directly from canonical events and each frontend owns its presentation |
| Claude foreground streaming still invoked the old drawing streamer, state handoff, slot allocator, and a top-level `claude-cmd-pre.py` shim | replace it with one plugin-owned transient `HarnessEventSource`: the hook action publishes the source after `OperationStarted` commits, `ObservationRunner` records exact byte chunks and canonical `OperationProgressed`, PostToolUse closes it, and the obsolete shim is deleted |
| canonical Claude lifecycle did not start OTLP intake, while the surviving launcher still targeted top-level Claude shims and the receiver depended on legacy lock/stream audit machinery | start the plugin-owned receiver from committed Claude `SessionStarted`; use port binding as the single singleton rule, direct plugin executable paths, exact raw delivery, and canonical usage translation; delete both bin shims and the old audit/lock lifecycle |
| the Claude-only status-line capture executable still lived in `bin/` | make `plugins/claude_code/statusline.py` its direct configured entry and delete the shim; native rate-limit payload parsing remains fully contained in the Claude plugin |
| canonical file sources and usage refresh were hosted by a web process that remained optional, so terminal-only sessions could stop observing after their short-lived hook exited | treat the existing localhost application/dashboard host as required runtime infrastructure; `HarnessHookService` ensures the singleton host is running after hook facts and controls commit but before source actions publish, so terminal, dashboard, usage and SSE always read one continuously observed store |
| Claude OTLP usage still incremented legacy scoreboard counters outside canonical ingestion | capture each exact OTLP request as raw Claude evidence and translate additive `UsageReported` facts; transcript usage is not a duplicate fallback source |
| canonical hook executables remained under harness-named `bin/` shims | invoke `plugins/claude_code/canonical_hook.py` and `plugins/codex/canonical_hook.py` directly; delete the old names rather than forwarding them |
| launch preferences, first-message drafts, hidden directories, and browser limits still used seven field-specific HTTP routes outside the global stream | add typed `GlobalPreferences` to the complete `GlobalApplicationSnapshot`, write it through three named `/api/application/*` resources, and delete the old reads and writes; every open browser now receives the same current values through the existing global SSE connection |
| the Insights page still derived session, token, cost, and error meaning from legacy audit tables | use a typed `ApplicationInsightsService`: canonical session and usage projections own semantics, terminal state owns actual liveness, operational diagnostics own error counts, and repository queries own worktree grouping; expose only `GET /api/insights` and delete `/api/stats` |
| the resume picker still inferred harness ownership, title, model, effort, and account by reopening native transcript paths through the legacy plugin fan-out | use `ResumableSessionService` over canonical `SessionSummary`, canonical event time, terminal liveness, and application repository queries; expose fully named `GET /api/resumable-sessions?working_directory=...&search=...` and delete the old route and its alternate limit/query vocabulary |
| browser telemetry still used abbreviated payload fields and three legacy audit endpoints | use typed `BrowserEvent`, `OptimisticActionReport`, and `ClientFailureReport` values through one `BrowserTelemetryService`; expose only the named `/api/application/browser-events` and `/api/sessions/{session_id}/application/*` resources |
| dictation vocabulary discovery walked `.claude` directories from shared dashboard code | add `speech_terms` to `HarnessCatalogSnapshot`; the owning catalog discovers native configuration and the dashboard only combines returned terms with application-wide terms |
| the new notifier still derived state through the old session reader and native screen heuristics | derive notification transitions only from canonical `TabState`, combine them with explicit terminal-window presence, and keep browser/device delivery state in the notification application service |
| collapsing the root plugin registry still left provider APIs executable from each harness package initializer | make `plugins/__init__.py` and every harness package `__init__.py` inert package markers; `plugin.py` is the only public descriptor and sibling modules remain private implementation |
| the production POST handler still inherited every legacy control/dialog/state mixin even after the named resources existed | compose only the canonical router and the three remaining byte/application resource handlers; delete unreachable legacy handler modules instead of retaining a second route surface |
| the visible Memory tab still read a Claude-owned key/value database through `dashboard/ext`, and after the route rewrite it no longer fetched at all | replace the generic extension registry with one optional typed `HarnessMemory` port, plugin-owned capture/storage/document resolution, named application status, and dashboard-owned tree/HTML presentation; keep the tab and interactions unchanged |
| Claude status-line capture still wrote legacy mirror-state keys and its usage reader re-entered the deleted provider fan-out | capture rate-limit windows in one Claude-owned application database, expose typed `UsageRow` values directly through `HarnessUsage`, and continue forwarding the user's status-line stdin/stdout verbatim |
| obsolete formatter, watcher, stream, and top-level harness scripts remained on disk after descriptor discovery stopped using them | delete the unreachable drawing-record producers and every remaining `bin/claude-*` entry; native executable entries live directly in their owning plugin and canonical terminal processes live under `app/` |
| Codex emits its native `SessionStart` hook and creates its root rollout only when the first prompt is submitted, so a command-line session has no native session identity at process startup | open panes immediately with a harness-neutral `pending-<native_process_id>` identity; once the wrapper reads the root rollout held by that exact child, bind and retag the existing lead, activity, and scoreboard windows to the canonical session without creating replacement panes |
| Codex exit cleanup depended on a later observer pass, and a resumed native session reused the original session start/finish identities | record the exact process-finish boundary synchronously when the wrapped command exits; key process start and finish facts by native process ID so every resume is a distinct lifecycle epoch while retaining one canonical session ID |
| Claude teammate-message records could emit `actor.started` both at position zero and later with a record timestamp, reusing one semantic ID with different bytes and killing the observation thread | make child transcript startup the single owner inside a position-zero translation; every independently observed sender start uses the same timestamp-free canonical actor fact, so later raw evidence adds provenance instead of changing identity |
| Claude lifecycle could attach a newly observed historical session to a Kitty tab already hosting another session, while Codex lifecycle correctly rejected that ownership conflict | require the same `hosting_session` guard in every pane-opening lifecycle; an occupied tab is never retagged or split for a different canonical session |
| terminal and web consumers had no explicit actor visibility rule, so either the lead transcript was duplicated beside the native TUI or child activity disappeared with it | make visibility a surface policy over canonical actors: terminal suppresses lead transcript/reasoning and system messages but keeps lead actions and all child activity; web defaults to lead-only activity and applies the selected `actor_id` as an exact scope |
| injected Codex instruction wrappers and Claude metadata prompts were represented as user messages and presentation code had to recognize their text | translate native synthetic evidence directly to canonical messages with role `system`; terminal hides that semantic role and web displays it without matching tags or content |

The last three rows are deletion blockers for the old dashboard path. They do
not justify a bridge API. The final HTTP handler still has one resource router,
one global application snapshot, and one selected-session application snapshot.

## 3. Goals

The proposed architecture must provide all of the following:

1. One harness-neutral semantic model for sessions, turns, actors, messages,
   operations, actor assignments, attention requests, file changes, and usage.
2. Complete containment of harness-specific parsing, discovery, vocabulary,
   launch, resume, and control behavior in that harness's plugin package.
3. Independent terminal and dashboard presentation over the same semantic facts.
4. Exact audit evidence for raw harness observations and for Baqylau's
   canonical interpretation of them.
5. Deterministic correlation from every canonical event back to the raw
   evidence that caused it.
6. Replay-safe ingestion of growing transcript/rollout files.
7. A direct downtime deployment with one schema and one code path. There is no
   compatibility mode, dual write, shadow runtime, coordinated cutover, or
   rollback system.
8. A design small enough to understand without a framework or service mesh.
9. A complete existing abstraction set that a future harness can implement
   without redesigning shared code.
10. No user-visible UI change: the terminal mirror, scoreboard, tab colours,
    web dashboard, cards, ordering, labels, controls, animations, and
    interactions must look and behave as they do before the refactor.

“No fallback” in this design forbids alternate architecture paths, guessed
ownership, compatibility readers, and substitute plugin behavior. The existing
user-visible model-refusal fallback and account-migration features remain native
harness facts and controls; their names do not authorize an architectural
fallback path.

## 4. Non-goals

This proposal does **not** require:

- event-sourcing every piece of application state;
- rebuilding all state exclusively by replaying a global log;
- a daemon, message broker, Redis, WebSockets, or PostgreSQL;
- forcing harnesses to expose features they do not support;
- normalizing vendor-specific account, pricing, model-selection, or TUI
  control grammars into false universal concepts;
- sending canonical events directly to browser code;
- replacing the proven terminal wrapping, ANSI, reflow, and click-to-view
  machinery all at once;
- putting drafts, dashboard preferences, terminal geometry, or notification
  delivery into the harness event model.

Canonical events are the authoritative record of harness activity. Ordinary
runtime state may still use purpose-built tables when a mutable row is the
simplest correct representation.

### 4.1 UI preservation is a hard constraint

This is an under-the-hood architecture replacement, not a redesign. “No UI
change” means:

- the same terminal activities produce the same visible lines, glyphs,
  colours, gutters, ordering, wrapping, copy links, and resize behavior;
- the scoreboard keeps the same rows, arithmetic, labels, and update timing;
- tab colours keep the same state transitions and recovery behavior;
- the dashboard keeps the same layout, theme, cards, bubbles, badges, view
  modes, controls, dialog behavior, newest-first ordering, agent scope, lazy
  history, and responsive behavior;
- existing user-facing wording remains unchanged unless correcting a separately
  approved bug;
- no feature disappears because its native source is difficult to normalize;
- performance and perceived update latency must not regress beyond the current
  test/measurement tolerances.

Internal HTTP contracts, SSE names, database schemas, Python APIs, and browser
data handling may change together. The generated DOM, styling, text, ordering,
and interactions must remain the same. Any intentional visual or interaction
change requires a separate proposal and must not be bundled into this
refactor.

## 5. Architectural principles

### 5.1 Facts before presentation

Canonical storage says what happened. It never says how to draw it.

Correct:

```python
OperationFinished(
    operation_id="toolu_123",
    category="shell",
    output="42 tests passed",
    outcome="succeeded",
    exit_code=0,
)
```

Forbidden:

```python
{
    "glyph": "■",
    "rgb": [80, 200, 120],
    "note": "Agent finished",
    "web": True,
    "chrome": False,
}
```

### 5.2 Native input stays native until the plugin boundary

Claude transcript shapes belong to `plugins/claude_code/`. Codex rollout
shapes belong to `plugins/codex/`. A future harness's native shapes will belong
to its own sibling plugin package.

Shared code must never parse a native payload, inspect a native file layout,
or recognize a harness by model-name syntax.

### 5.3 Capabilities, not host-name branches

Harnesses are not symmetric. Unsupported features are represented as absent
capabilities or inert optional ports, not as fake implementations and not as
`if harness == "codex"` in callers.

### 5.4 One fact, one stable identity

Every semantic subject has a stable ID: session, turn, actor, message,
operation, actor assignment, attention request, and usage report. Starts and finishes
join by identity, not by presentation group, timestamp proximity, or string
matching.

### 5.5 Raw evidence and canonical facts commit together

For each observation, exact raw bytes, the translation decision, canonical
facts, and provenance links are recorded in one SQLite transaction. There is
no audit spool, alternate writer, or best-effort second path. A storage failure
fails delivery, leaves the source checkpoint unchanged, and retries the same
stable raw-event identity through the ordinary source loop.

Operational diagnostics such as pane actions and notification attempts remain
separate from harness evidence. They are not runtime truth and are never read
to reconstruct canonical activity.

### 5.6 Transports carry projections

SSE, HTTP, and terminal output are delivery/presentation mechanisms. They do
not define the domain model.

### 5.7 Complexity budget

The essential design consists of:

- two envelopes: `RawEvent` and `CanonicalEvent`;
- one deterministic translation contract;
- one append/read canonical store;
- one ingestion coordinator;
- independent terminal and dashboard presenters;
- linked audit records for raw evidence, translation decisions, and canonical
  interpretations.

There is no internal event bus. A canonical SQLite cursor is the handoff
between ingestion and consumers. Additional interfaces exist only for optional
launch, control, and harness-owned queries.

Interfaces are reserved for real implementation boundaries:

- harness plugins implement recognition, event sources, translation, launch,
  and catalog contracts;
- the runtime implements raw-event delivery and checkpoint storage for plugin
  sources;
- Kitty implements the terminal frontend contract.

The registry, SQLite event store, codec, ingestion pipeline, observation runner,
semantic queries, presenters, renderers, and HTTP services are ordinary concrete classes. Tests
exercise them through temporary storage and fixtures rather than introducing
an interface for every class.

## 6. Proposed package boundaries

The final package shape should be approximately:

```text
domain/
  ids.py                 opaque identity types and stable-ID helpers
  events.py              canonical envelope and event payloads
  values.py              usage, outcome, operation category, relationships

contracts/
  harness.py             all plugin-facing contracts and request/result values
  terminal.py            terminal frontend contract and terminal values

runtime/
  ingest.py              the raw -> canonical pipeline
  event_store.py         canonical evidence append/read implementation
  projections.py         semantic query models and folds
  state.py               non-event runtime coordination state
  audit.py               operational diagnostic records
  registry.py            concrete plugin discovery and lookup

plugins/
  claude_code/           all Claude-specific code
  codex/                 all Codex-specific code
  <future_harness>/       future plugin implementing existing contracts

terminal/
  presenter.py           canonical activity -> terminal updates
  renderer.py            wrapping, layout and ANSI
  kitty.py               terminal frontend implementation

dashboard/
  presenter.py           canonical activity -> dashboard items
  http/                  HTTP and SSE delivery
  control/               application commands through capability ports
  client/                browser assets

app/
  bootstrap.py           composition root; the only implementation-aware tier
  observe.py             one application-owned source observation loop
```

Package boundaries are established directly. A harness-specific implementation
is moved only when its replacement contract exists, and no shared shim remains
after the move.

### Dependency direction

```text
plugins  ──→ contracts ──→ domain
runtime  ──→ contracts ──→ domain
terminal ──→ runtime + contracts + domain
dashboard──→ runtime + contracts + domain
app/bootstrap ──→ plugins + runtime + terminal + dashboard
```

More precisely:

- `domain/` imports only the standard library.
- `contracts/` imports `domain/` and the standard library.
- `runtime/` imports `domain/` and `contracts/`, never a plugin or presenter.
- a plugin imports `domain/` and `contracts/`; it never imports runtime,
  terminal, dashboard, or another plugin.
- a plugin's executable entry module may import `app/bootstrap.py` to assemble
  the process; translation, discovery, control, and presentation modules may
  not. Entry modules contain no harness behavior beyond intake and delegation.
- terminal and dashboard import semantic runtime APIs, never plugin packages.
- dashboard transport imports its presenter and application services, never
  native parsers.
- only the composition root discovers concrete plugins and wires ports.

### Harness containment rule

All code whose behavior depends on Claude Code must live under
`plugins/claude_code/`. All code whose behavior depends on Codex must live
under `plugins/codex/`. “Depends on” includes knowledge of any of the
following:

- native hook names, payload keys, event names, and event ordering;
- transcript, rollout, sidecar, config, account, or session-index formats;
- native tool names and argument/result grammars;
- filesystem locations, environment variables, executable names, and command
  line flags;
- model IDs, effort vocabularies, usage windows, pricing, account behavior,
  and rate-limit signals;
- TUI text, screen geometry, key sequences, prompts, dialogs, and completion
  detection;
- native session, turn, actor, task, message, call, and file identities;
- harness-specific workarounds and timeouts that exist in the current product;
- native human-readable labels and descriptions. The plugin exports these as
  plugin/canonical metadata; shared visual policy belongs to presenters.

Such behavior must not be placed in `domain/`, `contracts/`, `runtime/`,
`terminal/`, `dashboard/`, or other shared packages, even when only one shared caller
currently needs it. The plugin exposes the necessary fact or capability
through a public contract instead.

Shared packages may contain a harness name only in generic metadata values,
configuration/registration data, audit provenance, contract tests, and the
composition root. A harness name must never select shared behavior.

Entry scripts are owned by their harness plugin and installed as commands by
package metadata. There are no harness-named shared shims. Existing shared
storage names containing a harness name are replaced or deleted by the refactor;
they are not carried into the target architecture.

## 7. Raw observations

`RawEvent` is the immutable evidence received from a harness-facing source. A
source may be a hook payload, transcript line, rollout line, sidecar record, or
another explicitly registered harness observation channel.

```python
@dataclass(frozen=True)
class RawEvent:
    raw_event_id: RawEventId
    harness: str
    source_type: str
    source_name: str
    source_position: str
    session_id: SessionId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    observed_at: float
    encoding: str
    payload: bytes
```

Field meanings:

- `raw_event_id` is replay-stable and globally unique for this observation.
- `harness` is the plugin identity, metadata rather than a branch key.
- `source_type` is a small plugin-owned evidence vocabulary such as `hook`,
  `transcript`, `rollout`, `child_rollout`, or `sidecar_rollout`.
- `source_name` identifies the hook channel or file.
- `source_position` identifies the native event ID or byte position.
- `session_id` is known before delivery because event sources are created for a
  recognized session. Translation never guesses session ownership.
- `actor_id` and `parent_actor_id` are the canonical actor context assigned when
  the plugin creates the source. This lets a Codex source attach to a Claude
  Code session without a shared harness branch or an actor guessed from a path.
- `observed_at` is Baqylau's receipt time.
- `encoding` says how to inspect `payload`, for example `json` or `jsonl`.
- `payload` preserves the complete raw bytes, including a JSONL record's line
  terminator. Presentation truncation is never applied here.

For growing files, a suitable stable identity is derived from:

```text
harness + durable source identity + complete-line starting byte offset
```

For hooks, use the harness's native event ID when one exists. Otherwise use a
deterministic digest of the exact payload bytes and the hook channel. Random
identities are forbidden because replaying the same observation must address
the same raw row.

Raw capture occurs at the earliest common chokepoint:

- once in each hook dispatcher, before subscriber routing;
- once in the complete-line tail pump, before native decoding.

Some native facts exist only in the harness process environment rather than in
the hook JSON. Claude Code's active subscription account is the current case.
On `SessionStart`, the Claude plugin records one separate deterministic
`account` raw observation containing exactly the account environment it read;
that observation produces `session.account_changed`. The hook payload remains
unchanged. Shared code neither reads Claude environment variables nor invents
an account from model names.

Unknown or currently irrelevant native records are still raw evidence. They
may produce zero canonical events, but their translation decision is auditable.

### 7.1 Source checkpoint contract

File/poll sources own native checkpoints because only the plugin understands
their record boundaries. They obey a shared delivery rule:

```python
@dataclass(frozen=True)
class SourceCheckpoint:
    session_id: SessionId
    source_identity: str
    position: str


class CheckpointStore(Protocol):
    def load(self, source_identity: str) -> SourceCheckpoint | None: ...
    def commit(self, checkpoint: SourceCheckpoint) -> None: ...
```

The source submits a complete `RawEvent`, waits for
`RawEventDelivery.deliver`, and commits its checkpoint only after canonical
storage atomically records the raw evidence, translation decision, canonical
facts, and provenance. Any event-storage failure prevents checkpoint advance.
A crash before checkpoint commit replays the same stable `raw_event_id`, which
deduplicates safely.

Checkpoints are opaque to runtime. Claude byte offsets and Codex rollout
offsets never escape the owning plugin as interpreted values.

## 8. Canonical event model

### 8.1 Envelope

All canonical events share one envelope:

```python
EventPayloadType = TypeVar("EventPayloadType", bound="EventPayload")


@dataclass(frozen=True)
class CanonicalEvent(Generic[EventPayloadType]):
    event_id: CanonicalEventId
    session_id: SessionId
    actor_id: ActorId
    turn_id: TurnId | None
    parent_actor_id: ActorId | None

    harness: str
    occurred_at: float | None

    payload: EventPayloadType
```

The payload class determines the canonical event type. The shared codec adds
`event_type` and `schema_version` when it serializes the event. Plugins cannot
author those discriminator fields independently, so a `MessageCreated` payload
cannot accidentally claim to be `operation.finished` or use an unsupported
version.

Runtime storage assigns one monotonic database `cursor` when the event is
inserted. `cursor` is the ingestion order; it is not authored by a
plugin and is not part of the event's deterministic identity.

`occurred_at` is the harness/native time when available. Receipt time belongs
to each `RawEvent`, because several observations of one canonical fact can
arrive at different times. Storage adds `accepted_at` when it first accepts the
fact; neither timestamp substitutes for causal relationships.

Provenance is associated transactionally by `EventStore.record`, not
embedded in the immutable event. This avoids two competing provenance lists
when a later hook or transcript record proves the same event. Runtime and audit
both link `event_id` to every `raw_event_id` through dedicated relation tables.

The `harness` field preserves provenance and supports diagnostics. Shared
projectors must not branch on it.

### 8.2 Initial closed vocabulary

The first useful vocabulary is closed and sized for v1 UI parity:

```text
session.started
session.title_changed
session.working_directory_changed
session.account_changed
session.finished

model.changed
effort.changed

actor.started
actor.name_changed
actor.description_changed
actor.finished

turn.started
turn.finished
turn.aborted

message.created
reasoning.created

operation.started
operation.progressed
operation.finished

file.accessed

actor.assignment_started
actor.assignment_finished

task.changed
goal.changed
actor.message_sent

attention.requested
attention.resolved

usage.reported
context.reported
compaction.started
compaction.finished
```

New event types are added only when they express a new fact needed by more than a
single surface or policy. A new glyph, card layout, CSS treatment, or terminal
line is never a reason to add a canonical event type.

### 8.3 Core semantic values

Shared closed vocabularies should include:

```python
OperationCategory = Literal[
    "shell", "file_read", "file_write", "file_edit",
    "search", "network", "workspace", "media", "skill", "task",
    "message", "attention",
]

Outcome = Literal[
    "succeeded", "failed", "cancelled", "rejected", "unknown",
]

ActorRole = Literal[
    "lead", "child", "teammate", "sidecar",
]

ExecutionMode = Literal[
    "foreground", "background", "monitor",
]
```

The model carries a shared operation name alongside its shared category. Each
plugin maps native tool names to this vocabulary. Raw evidence retains the exact
native name for audit. An unmapped semantic tool is a translation failure; there
is no `other`, generic `operation`, or presenter fallback category.

### 8.4 Content fidelity

Text and structured results are lossless at the canonical boundary. Storage
stores them inline in canonical JSON. Canonical data must not be capped to a
terminal width or UI preview length. Dashboard copy/view references are
presentation references derived from an event ID; they are not canonical
content indirection.

### 8.5 Identity and deduplication

Each plugin translates native identities into canonical identities using a
shared stable-ID helper:

```python
event_id = stable_event_id(
    harness="claude_code",
    session_id=session_id,
    actor_id=actor_id,
    subject_type="operation",
    subject_id=tool_use_id,
    phase="started",
)
```

Examples of native inputs:

- Claude: message UUID, `tool_use_id`, agent ID, task tool-use ID;
- Codex: rollout item ID, `call_id`, turn ID, child thread ID;
- a future harness: its corresponding native identifiers.

When a harness exposes the same logical fact through both a hook and a
transcript, both observations map to the same `event_id`. The semantic event
appears once. A separate provenance association records later observations of
the same fact; the accepted event itself remains immutable.

The store enforces uniqueness on `event_id`. Re-reading a transcript prefix or
resuming a tailer therefore cannot duplicate activity.

### 8.6 Ordering and relationships

The database `cursor` provides stable ingestion order. Semantic ordering uses explicit
relationships:

- operation start/finish share `operation_id`;
- child start/finish share `task_id`;
- actor-assignment events use the envelope's `turn_id` for the parent turn when the
  harness can provide it;
- messages name their turn when the harness can provide it;
- parent/child actors use IDs, never `src` prefixes;
- a harness that cannot name a relationship leaves it absent rather than
  guessing from timestamps.

This replaces presentation-time repairs such as recovering copy groups from
short group keys, source scope from string prefixes, or moving a child
completion around a parent answer using baked rendering metadata.

Native subject IDs remain available to the owning plugin for controls. Shared
activity identity is `(actor_id, subject_id)`, because separate actors and
harnesses may both legitimately emit a native `call-1` or `message-1` inside
one canonical session. Event identity remains globally stable through
`stable_event_id`.

### 8.7 Identity and value models

IDs are opaque strings at API boundaries. Plugins may derive them from native
IDs, but shared code compares them only for equality:

```python
SessionId = NewType("SessionId", str)
ActorId = NewType("ActorId", str)
TurnId = NewType("TurnId", str)
RawEventId = NewType("RawEventId", str)
CanonicalEventId = NewType("CanonicalEventId", str)
MessageId = NewType("MessageId", str)
OperationId = NewType("OperationId", str)
AssignmentId = NewType("AssignmentId", str)
TaskId = NewType("TaskId", str)
AttentionId = NewType("AttentionId", str)
```

Shared immutable values used by several event payloads are:

```python
@dataclass(frozen=True)
class ModelReference:
    native_id: str
    display_name: str | None
    selection_id: str | None


@dataclass(frozen=True)
class AccountReference:
    account_id: str
    display_name: str


@dataclass(frozen=True)
class TextContent:
    text: str
    media_type: Literal["text/plain", "text/markdown"]


@dataclass(frozen=True)
class StructuredContent:
    json_text: str


Content = TextContent | StructuredContent


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    one_hour_cache_write_tokens: int = 0


@dataclass(frozen=True)
class AttentionChoice:
    value: str
    label: str
    description: str | None = None


@dataclass(frozen=True)
class AttentionPrompt:
    prompt_id: str
    title: str | None
    prompt: str
    multiple: bool
    choices: tuple[AttentionChoice, ...]


@dataclass(frozen=True)
class AttentionAnswer:
    prompt_id: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class EventPayload:
    """Marker base; contains semantic values only."""
```

`native_id` preserves harness evidence, `display_name` is the plugin-chosen
human label, and `selection_id` names the matching `ModelOption`. The browser
must never parse a native model identifier or implement harness-specific
family matching.

`display_name` is native metadata, not a formatted chip. A presenter may show
it using the existing UI rules. The two content classes make invalid mixed
representations impossible while keeping harness payload grammar out of
consumers. `StructuredContent.json_text` is validated JSON in the codec's
deterministic key order; storing text keeps the frozen value deeply immutable.

### 8.8 Event payload catalogue

The following is the initial public semantic contract. Fields marked optional
are absent when a harness cannot prove them; translators do not synthesize them
from UI text or timing.

```python
@dataclass(frozen=True)
class SessionStarted(EventPayload):
    working_directory: str
    resumed_from: SessionId | None
    title: str | None
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None


@dataclass(frozen=True)
class SessionTitleChanged(EventPayload):
    title: str
    origin: Literal["custom", "automatic", "summary"]


@dataclass(frozen=True)
class SessionWorkingDirectoryChanged(EventPayload):
    working_directory: str


@dataclass(frozen=True)
class SessionAccountChanged(EventPayload):
    account: AccountReference


@dataclass(frozen=True)
class SessionFinished(EventPayload):
    outcome: Outcome
    reason: str | None


@dataclass(frozen=True)
class ModelChanged(EventPayload):
    previous: ModelReference | None
    current: ModelReference
    reason: Literal[
        "selected", "automatic_fallback", "account_migration",
        "reported_by_harness",
    ]


@dataclass(frozen=True)
class EffortChanged(EventPayload):
    previous: str | None
    current: str
    reason: Literal["selected", "account_migration", "reported_by_harness"]


@dataclass(frozen=True)
class ActorStarted(EventPayload):
    name: str
    role: ActorRole


@dataclass(frozen=True)
class ActorNameChanged(EventPayload):
    name: str


@dataclass(frozen=True)
class ActorDescriptionChanged(EventPayload):
    description: str


@dataclass(frozen=True)
class ActorFinished(EventPayload):
    reason: str | None


@dataclass(frozen=True)
class TurnStarted(EventPayload):
    prompt_message_id: MessageId | None


@dataclass(frozen=True)
class TurnFinished(EventPayload):
    final_message_id: MessageId | None
    outcome: Outcome


@dataclass(frozen=True)
class TurnAborted(EventPayload):
    reason: str | None


@dataclass(frozen=True)
class MessageCreated(EventPayload):
    message_id: MessageId
    role: Literal["user", "assistant", "system", "peer"]
    content: Content
    phase: Literal["prompt", "intermediate", "final", "synthetic", "recap"] | None
    reply_to: MessageId | None


@dataclass(frozen=True)
class ReasoningCreated(EventPayload):
    reasoning_id: str
    content: Content
    summary: bool


@dataclass(frozen=True)
class OperationStarted(EventPayload):
    operation_id: OperationId
    category: OperationCategory
    native_name: str
    execution: ExecutionMode
    arguments: Content | None
    description: str | None
    parent_operation_id: OperationId | None


@dataclass(frozen=True)
class OperationProgressed(EventPayload):
    operation_id: OperationId
    ordinal: int
    stream: Literal["output", "error", "status"]
    content: Content
    mode: Literal["append", "replace"]


@dataclass(frozen=True)
class OperationFinished(EventPayload):
    operation_id: OperationId
    outcome: Outcome
    result: Content | None
    exit_code: int | None


@dataclass(frozen=True)
class FileAccessed(EventPayload):
    operation_id: OperationId | None
    path: str
    action: Literal["read", "created", "updated", "deleted", "renamed"]
    previous_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    unified_diff: str | None = None
    content: Content | None = None


@dataclass(frozen=True)
class ActorAssignmentStarted(EventPayload):
    assignment_id: AssignmentId
    brief: Content


@dataclass(frozen=True)
class ActorAssignmentFinished(EventPayload):
    assignment_id: AssignmentId
    outcome: Outcome
    result: Content | None
    reason: str | None


@dataclass(frozen=True)
class TaskChanged(EventPayload):
    task_id: TaskId
    label: str
    subject: str
    description: str | None
    state: Literal["pending", "in_progress", "completed", "deleted"]
    owner_actor_id: ActorId | None


@dataclass(frozen=True)
class TaskListChanged(EventPayload):
    list_id: str
    task_ids: tuple[TaskId, ...]


@dataclass(frozen=True)
class GoalChanged(EventPayload):
    objective: str | None
    state: Literal[
        "active",
        "paused",
        "blocked",
        "usage_limited",
        "budget_limited",
        "completed",
        "cleared",
    ]
    reason: str | None


@dataclass(frozen=True)
class ActorMessageSent(EventPayload):
    message_id: MessageId
    recipient_actor_id: ActorId
    content: Content | None


@dataclass(frozen=True)
class AttentionRequested(EventPayload):
    attention_id: AttentionId
    attention_type: Literal["permission", "question", "plan", "confirmation"]
    prompts: tuple[AttentionPrompt, ...]
    operation_id: OperationId | None


@dataclass(frozen=True)
class AttentionResolved(EventPayload):
    attention_id: AttentionId
    decision: Literal[
        "answered", "approved", "changes_requested", "rejected",
        "confirmed", "denied", "discussed",
    ]
    answers: tuple[AttentionAnswer, ...]
    feedback: str | None
    edited: bool
    outcome: Outcome


@dataclass(frozen=True)
class UsageReported(EventPayload):
    scope: Literal["session", "actor", "turn", "operation"]
    subject_id: str
    model: ModelReference | None
    account: AccountReference | None
    tokens: TokenUsage
    cumulative: bool
    cost_in_usd: Decimal | None


@dataclass(frozen=True)
class ContextReported(EventPayload):
    used_tokens: int
    window_tokens: int
    model: ModelReference | None


@dataclass(frozen=True)
class CompactionStarted(EventPayload):
    before_tokens: int | None


@dataclass(frozen=True)
class CompactionFinished(EventPayload):
    before_tokens: int | None
    after_tokens: int | None
```

`TaskId` is stable canonical identity; `label` is the short native reference a
surface may show. `TaskListChanged.list_id` scopes membership to
one checklist, so a lead actor's new snapshot cannot delete another actor's
tasks. A snapshot emits membership first and then the full `TaskChanged` facts
for its current members. The projection, not either plugin, removes members
that disappeared.

The envelope actor is the sender of `actor.message_sent`; the payload names only
the recipient. One transfer is one fact, even if both sender and recipient
transcripts expose it. Separate compaction payloads avoid a redundant state
field that could disagree with the event type.

`OperationStarted.arguments` is the normalized semantic input a surface should
show, not a copy of a vendor argument object. A shell operation carries its
command text, a search carries its query, and a file operation carries its
path. An operation without a shared primary input may carry structured content.
Complete native arguments remain in raw evidence. Presenters never inspect keys
such as `command`, `file_path`, or `query`, and never branch on a native tool
name to recover the primary input.

Actor lifetime and assignment lifetime are deliberately separate:

- `ActorStarted` creates one actor identity. It does not imply that the actor
  has received work.
- `ActorAssignmentStarted` opens one unit of work. The same actor may receive
  another assignment later, and each assignment has its own opaque identity.
- `ActorAssignmentFinished` closes that unit of work and owns its semantic
  outcome, optional result, and optional explanation. A successful assignment
  may have no result; a failed or cancelled assignment may have only a reason.
- `ActorFinished` means the actor itself is permanently unavailable. Finishing
  an assignment never synthesizes this event. A resumable or repeatedly
  assigned actor therefore remains the same actor.

There are no parallel task-lifecycle events and no generic event fallback. A
harness that supports actors must translate its native lifecycle directly into
these four facts. Unknown native lifecycle records fail translation instead of
becoming an `operation` with guessed semantics. The event envelope supplies the
session, actor, parent actor, turn, harness, and time; these payloads do not
repeat envelope identity.

Codex collaboration uses the same facts and deliberately does not expose its
control calls as operations:

| Codex evidence | Canonical result | Presentation |
|---|---|---|
| child rollout metadata | `ActorStarted` | actor index only |
| child `task_started` | `ActorAssignmentStarted`; assignment identity is the child turn identity | actor-started note in the parent transcript and running actor state |
| child `task_complete` | `ActorAssignmentFinished(succeeded, result)` with the same child turn identity | actor-finished note; expanding it exposes the captured result |
| child `turn_aborted` after `interrupt_agent` | `ActorAssignmentFinished(cancelled, reason="interrupted")` | cancelled actor-assignment note |
| `send_message` plus its recipient activity | `ActorMessageSent`; sender is the envelope actor and recipient is the activity actor | actor-message note |
| `followup_task` | no direct fact; the resulting child `task_started` opens the new assignment | the normal actor-started note |
| `wait_agent` | no canonical event | no transcript item |
| `list_agents` | no canonical event | no transcript item; the actor index remains the source for the Agents surface |
| collaboration acknowledgements | no canonical event | no generic operation block |

Every native call, activity, acknowledgement, child start, completion, and
abort remains in the raw audit stream. The canonical audit stream contains only
the semantic rows above. Codex message bodies are encrypted in the parent
rollout, so `ActorMessageSent.content` is absent; the adapter never invents or
attempts to decrypt content. Ordinary commands and tools executed inside the
child remain plaintext child-rollout operations and are projected normally.

### 8.9 Envelope invariants by event type

- `session.*` events use the lead actor ID; `turn_id` may be absent.
- `actor.*` uses the affected actor in the envelope. If another actor caused the
  lifecycle change, that relationship is represented by `parent_actor_id` or
  raw provenance, not a second actor field.
- message, reasoning, and operation events use the actor that authored or
  executed them.
- operation progress ordinals are monotonic per `(operation_id, stream)` and
  make replay/dedup independent of chunk text.
- finish events may arrive without a captured start. Projections must render
  the known finish honestly rather than inventing a start time.
- repeated cumulative usage replaces the prior cumulative sample for delta
  calculation; non-cumulative usage adds exactly once by `event_id`.
- `FileAccessed.unified_diff` is semantic source content, not syntax-highlighted or
  wrapped presentation.
- attention choices carry stable choice values separately from display labels.
- no event is invalid merely because an optional native concept—especially a
  turn ID—is unavailable.

### 8.10 Serialization and validation

Canonical JSON is a public internal contract, not an untyped dump of Python
objects. One shared codec owns the mapping between `event_type`, `schema_version`,
and its payload class:

```python
class CanonicalEventCodec:
    def encode(self, event: CanonicalEvent) -> bytes: ...
    def decode(self, encoded: bytes | str) -> CanonicalEvent: ...
```

The exhaustive harness-neutral `EVENT_TYPES` mapping maps payload classes to
serialized event types and back. Plugins instantiate registered payloads;
they cannot register a private canonical event carrying a native payload. A
genuinely new cross-harness semantic fact requires a domain schema change,
projector updates, and fixtures.

Encoding rules are deterministic:

- IDs and enum/literal values are JSON strings;
- tuples and frozen sets are JSON arrays, with sets sorted before encoding;
- `Decimal` is encoded as a decimal string, never a binary float;
- absent optional values are encoded consistently according to the schema;
- object keys have a stable order for hashing and fixture diffs;
- unknown event types or unsupported versions are rejected before runtime append;
- extra native fields are retained only in raw audit evidence, not silently
  copied into canonical `payload`.

The codec is the only code allowed to hydrate canonical payload classes from
stored JSON. HTTP, SSE, presenters, and plugins do not each maintain their own
partial deserializer.

The event store calls the codec before append. Encoding validates the complete
closed payload schema, including literal values and exact fields; it never
trusts Python type annotations alone. Anything accepted must immediately
decode with the same schema.

## 9. Harness plugin contracts

The plugin API has ordinary, concrete responsibilities: identify the harness,
recognize its sessions, observe and translate events, perform controls, launch
sessions, expose native menus, and interpret harness-specific live terminal
input. Optional parts exist only for features present in the product today.

### 9.1 Plugin

```python
@dataclass(frozen=True)
class HarnessInfo:
    name: str
    display_name: str
    plugin_version: str
    canonical_version: int
    supports_attachments: bool = False
    default_for_launch: bool = False


@dataclass(frozen=True)
class HarnessPlugin:
    info: HarnessInfo
    sessions: SessionRecognizer
    events: HarnessEvents
    hook: HarnessHook | None = None
    lifecycle: HarnessLifecycle | None = None
    controller: HarnessController | None = None
    launcher: HarnessLauncher | None = None
    catalog: HarnessCatalog | None = None
    usage: HarnessUsage | None = None
    terminal_probe: HarnessTerminalProbe | None = None
```

This descriptor is the only object registered with shared code. The contained
implementations remain in the same plugin folder. Optional components are
absent when the harness does not provide that feature; shared code returns a
plain unsupported result and does not guess or substitute another harness.
Launch support is the presence of `launcher`; control support is exactly the
controller's handler keys; catalog support is the catalog's declared
`sections`; current rate-limit state is the optional `usage` port. Attachment
support is the one explicit launch capability because
it affects upload UI before a launch request exists. Registration rejects
attachment support without a launcher. Capability discovery performs no
native reads.

### 9.2 Session recognition

```python
class SessionRecognizer(Protocol):
    def discover(self) -> tuple[RecognizedSession, ...]: ...
    def recognize(self, candidate: SessionCandidate) -> RecognizedSession | None: ...


@dataclass(frozen=True)
class RecognizedSession:
    session_id: SessionId
    lead_actor_id: ActorId
    native_session_id: str
    source_reference: str
    working_directory: str | None
    native_process_id: int | None


@dataclass(frozen=True)
class SessionCandidate:
    source_reference: str
    working_directory: str | None = None
```

Recognition is deterministic. A plugin either proves ownership or returns
`None`; there is no confidence score, default harness, filename guess in shared
code, or tie-breaking fallback. Zero matches means unsupported. More than one
match is a configuration error and is audited.

Once recognized, the runtime stores the binding. Reads and controls use that
binding and never recognize the same session again.

`discover` covers current on-disk session discovery. `recognize` covers a
specific path or source reported by a hook, launcher, watcher, or restore flow.
The plugin performs native directory walking and file inspection; shared code
only combines the returned sessions and rejects duplicate canonical IDs.

### 9.3 Event observation and translation

```python
class HarnessEvents(Protocol):
    def sources(
        self,
        session: RecognizedSession,
        checkpoints: CheckpointStore,
    ) -> tuple[HarnessEventSource, ...]: ...

    def translate(self, raw_event: RawEvent) -> TranslationResult: ...


class HarnessEventSource(Protocol):
    def drain(self, delivery: RawEventDelivery) -> None: ...


class RawEventDelivery(Protocol):
    def deliver(self, raw_event: RawEvent) -> IngestionResult: ...


@dataclass(frozen=True)
class EventSourceContext:
    session_id: SessionId
    lead_actor_id: ActorId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    source_reference: str


@dataclass(frozen=True)
class TranslationResult:
    events: tuple[CanonicalEvent, ...]
    decision: Literal["translated", "ignored_unknown", "ignored_nonsemantic"]
    reason: str | None = None
```

`translate` is deterministic and side-effect free. It does not write SQLite,
render terminal content, launch processes, or inspect a terminal. Native file
discovery and I/O happen in event sources in the same plugin. Every complete
raw record enters through `RawEventDelivery`.

`HarnessEvents.sources` returns every source that the plugin owns within the
given canonical session. A lead source uses
`RecognizedSession.event_source_context`. A child or sidecar source uses an
`EventSourceContext` naming the recognized lead, its actor, and its parent. The
runtime asks registered plugins for sources; it never infers actor ownership
from native paths or model names. The Claude Code plugin discovers its
`subagents/agent-*.jsonl` children. The Codex plugin discovers rollouts whose
metadata names the recognized native session as parent and records whether
they are a Codex-native child or a cross-harness sidecar. These layout,
relation, and metadata rules stay in their respective plugin folders.

A Codex child rollout begins with a native replay of its parent's history. The
Codex plugin waits until the rollout contains Codex's child-body boundary before
it exposes that source. It then submits the entire file: the initial child
metadata establishes the actor, replayed parent records remain raw evidence
with `ignored_nonsemantic` decisions, and only records after the native boundary
become canonical child activity. Shared code never knows this layout rule, and
there is no missing-boundary fallback that can attribute parent facts to a
child.

Every registered plugin is allowed to contribute sources to a recognized
session because a real session can contain an actor owned by another harness,
such as a Codex sidecar inside a Claude Code session. The source-producing
plugin must prove the relationship from native session or parent metadata and
returns an empty tuple otherwise. Matching only by working directory,
timestamp proximity, process order, or “first watcher wins” is forbidden. A
native run without an explicit relationship remains unattached; shared code
never claims it for a convenient session.

A plugin with many related sources must schedule them fairly inside its own
`HarnessEvents` implementation because only that plugin understands their
native relationships. Codex caches its parent-to-child rollout index until the
native rollout path set changes and contributes one rotating child source per
parent on each observation pass. Checkpoints preserve every byte between turns.
The shared runner never recognizes a Codex child path or carries a Codex source
limit.

Malformed input raises a typed `TranslationError` containing safe diagnostic
context; the pipeline records it and accepts no canonical event. An intentional
ignore is a successful `TranslationResult` with no events and a stable
decision, not an exception and not `None`.

`translated` requires at least one event. Both ignored decisions require zero
events. The event store rejects a repeated `event_id` whose canonical bytes
differ; deduplication is allowed only for the same fact observed again.

`drain` is finite and bounded: it reads at most one batch of currently complete
records and returns. The source waits for `delivery.deliver` to return before
advancing its native checkpoint, so the next pass continues at the exact next
byte. This is scheduling, not dropped history or a second ingestion path.

Each recognizer returns newest native sessions first. The registry interleaves
those ordered results across plugins and stops discovery after four sessions
before the concrete `ObservationRunner` drains them. Every pass therefore
services only recent live sources. A recent Claude transcript and a recent
Codex rollout both receive service in every discovery pass, while old native
history cannot consume the live observer or delay process-exit cleanup. The
runner remains deliberately small:

```python
class ObservationRunner:
    def run_once(self) -> None:
        sessions = self.registry.discover_sessions(limit=4)
        for registered_session in sessions:
            for plugin in self.registry.plugins():
                for source in plugin.events.sources(
                    registered_session.session,
                    self.checkpoints,
                ):
                    source.drain(self.delivery)
```

The application server repeats `run_once` at the existing observation cadence.
It is the sole file/poll observer for the database. Presenters, scoreboards,
HTTP handlers, and SSE handlers only query committed state. There is no source
worker interface, consumer-triggered drain, lease, election, or alternate
observer path.

Read performance follows the same ownership boundary. `SessionQueries` caches
decoded canonical pages by the latest cursor of that session and appends only
the new range when its cursor advances. An event added to one session never
invalidates every other session, and a new event in a large session never
requires decoding its complete history again. `EventStore` loads all raw
provenance for a page or appended range in one ordered query rather than one
query per canonical event. These are transparent read optimizations:
projections, HTTP, SSE, and both frontends still consume the same immutable
canonical facts.

The list service retains each of the 20 recent projected items by that
session's cursor, so a change in one session recomputes only that item.
Terminal and repository values are still read when the list snapshot is built;
they are not frozen into the canonical cache. After the first recent list
exists, an expired list is returned immediately while one serialized background
refresh builds its next revision. HTTP reloads and SSE heartbeats therefore
never wait behind canonical history.

The application host is mandatory even when the web UI is not open. Every
native hook verifies that the one localhost host process is running after its
raw facts and controls commit and before its returned source actions start.
This check is harness-neutral and silent, and singleton ownership remains the
ordinary host process/port rule. A terminal session therefore never depends on
a browser visit to keep transcript, rollout, foreground, usage, or OTLP facts
moving. “Dashboard” is one frontend served by this host, not the owner of the
canonical runtime.

#### One gesture may be several records

A translator must not assume one source record is one fact. The clearest case is
a Claude Code slash command: typing `/model opus` writes **three** user-shaped
transcript records — the `<local-command-caveat>` injection, the
`<command-name>`/`<command-args>` envelope, and the command's echoed
`<local-command-stdout>`. Only the caveat carries a structural flag (`isMeta`),
so a translator that keys on flags alone emitted three `message.created` events
for one keystroke, two of them `role="user"`. Measured in session 6a23d1c5: the
user saw one system block and two "you" bubbles.

`plugins/claude_code/transcript.py` collapses them in the parse stage — the
envelope becomes the `slash_command` record carrying the text as typed, and the
other two return `None`. The translator's `_slash_command` then emits one
`message.created` plus, for a command that settles session state, that state
event.

Two rules generalise from it:

- **Anchor any text-shaped classification at the start of the content.** These
  records carry no flag, so they must be recognised by their text — and a
  message that merely *quotes* an envelope (a paste asking about it, this repo's
  own docs, a grep hit) is byte-identical to the real thing except that it has
  prose in front. Anchoring is what makes reading the text safe. The same
  discipline already governs `_TEAM_ENVELOPE` and the interrupt marker.
- **Record what the source actually said, and let a later source correct it.**
  `/model opus` carries a selection *alias*; the native id (`claude-opus-5`)
  first appears a turn later on the next assistant record. So the command emits
  `ModelChanged(reason="selected")` holding the alias — which is what lets the
  switch be seen at the moment it was made — and the assistant record still
  emits its `reported_by_harness` event with the native id. Two events describe
  one switch; neither invents a fact the source did not carry. Inventing the
  native id (an alias→native table) was rejected: it is a shared fact needing an
  owner and it goes stale the day a new model ships.

### 9.4 Synchronous hook intake

Hooks are not all passive observations. A native hook may require synchronous
stdout, such as a tool-input rewrite, and may initiate an existing plugin-owned
effect. That behavior must not be smuggled into the pure translator or kept by
calling the old dispatcher after canonical ingestion.

```python
@dataclass(frozen=True)
class HookIntake:
    session: RecognizedSession
    raw_events: tuple[RawEvent, ...]
    output: bytes = b""
    controls: tuple[ControlRequest, ...] = ()
    actions: tuple[HookAction, ...] = ()


class HookAction(Protocol):
    def start(self) -> None: ...


@dataclass(frozen=True)
class SessionLifecycleRequest:
    action: Literal["started", "finished"]


class HarnessLifecycle(Protocol):
    def apply(
        self,
        request: SessionLifecycleRequest,
        session: RecognizedSession,
        context: SessionLifecycleContext,
    ) -> None: ...


@dataclass(frozen=True)
class SessionLifecycleContext:
    terminal: SessionTerminal
    panes: SessionPaneControl


class HarnessHook(Protocol):
    def receive(self, payload: bytes) -> HookIntake: ...


class HarnessHookService:
    def receive(self, harness: str, payload: bytes) -> bytes: ...
```

The plugin parses its native hook exactly once and returns the recognized
session, every exact observation produced by that invocation, and the exact
bytes the native harness expects on stdout. It may also request an existing
typed control after ingestion or return a plugin-owned `HookAction`. The
application starts actions only after every raw observation commits and every
typed control is acknowledged. It never inspects the concrete action. Claude
Code uses this narrow interface to start its foreground output observer only
after `OperationStarted` is durable; a future harness can use the same ordering
without adding a harness branch. Claude Code's rate-limit hook uses the ordinary
`MigrateAccount` control rather than retaining a second migrator path.

Lifecycle is not a second fact emitted by the hook. `SessionStarted` and
`SessionFinished` are canonical facts whether they came from a hook, transcript,
rollout, or liveness source. `ApplicationEventDelivery` maps those two facts to
`SessionLifecycleRequest("started")` or `SessionLifecycleRequest("finished")`
after their event transaction commits. The registered `HarnessLifecycle`
implementation stays inside the same plugin folder and decides native
nested/headless applicability. The lifecycle receives the narrow
`SessionTerminal` and `SessionPaneControl` capabilities from the composition
root. It may request generic pane changes, but it cannot import terminal
implementations, runtime state, or old lifecycle modules. A future harness
implements the same lifecycle port instead of adding a shared harness branch.
Codex's native `SessionStart` is not an immediate process-start signal: current
Codex emits it only with the first submitted prompt. The Codex-owned command
boundary therefore observes the exact native child process and the root rollout
that process has open, then emits a raw `process` start before any prompt. The
native hook and rollout metadata still translate into identical `SessionStarted`
and lead `ActorStarted` facts and become additional provenance. Process-boundary
session facts use the native process ID as their semantic identity, so a resume
creates a new start/finish epoch under the same canonical session rather than
reusing the original epoch.
`HarnessHookService` selects the
registered plugin, persists the session binding, delivers every raw event
through the one ingestion transaction path, executes those controls through
`HarnessControlService`, starts the returned actions, and returns the output
only when all of those steps succeed. A `rejected` or `indeterminate` result
fails the synchronous hook invocation and returns no successful stdout. There
is no best-effort hook-control or hook-action mode. The service does not inspect
hook names, payload fields, tee files, or native process arguments.

Native synchronous behavior and effect selection stay in the owning plugin.
Rendering, old state writes, and a second audit call are forbidden. A plugin
without native hooks leaves this component absent; invoking it is an explicit
unsupported error, not a substitute path.

Claude foreground streaming is one direct instance of this rule. The plugin
rewrites the native command to copy stdout and stderr into one transient source,
then exposes that source through the ordinary `HarnessEventSource` interface to
the one application-owned `ObservationRunner`. Each exact byte chunk is one raw
`foreground_output` record containing base64 evidence and translates to an
append-mode `OperationProgressed`. The PostToolUse action closes that source.
The source manifest and completion file coordinate observation only; they are
not semantic state, are never read by a projection, and are deleted by the
observer. Running state and elapsed time come solely from `OperationStarted` and
`OperationFinished`. There is no drawing write, foreground handoff, slot, copy
group, or presentation decision in the hook.

### 9.5 Controls

```python
ControlName = Literal[
    "send_text", "interrupt", "close_session", "rename_session", "auto_name_session",
    "open_rewind", "apply_rewind", "migrate_account", "compact",
    "select_model", "select_effort", "answer_question",
    "read_plan_choices", "decide_plan",
]


@dataclass(frozen=True)
class ControlTarget:
    session_id: SessionId
    request_id: str


@dataclass(frozen=True)
class ControlContext:
    session: RecognizedSession
    terminal: TerminalControl
    current_model: ModelReference | None
    current_effort: str | None
    current_account: AccountReference | None
    pending_attention: AttentionRequested | None


@dataclass(frozen=True)
class AttachmentReference:
    local_path: str
    display_name: str
    media_type: str | None = None


@dataclass(frozen=True)
class SendText(ControlTarget):
    control_name: ClassVar[ControlName] = "send_text"
    text: str
    attachments: tuple[AttachmentReference, ...] = ()
    replace_terminal_draft: bool = False


@dataclass(frozen=True)
class Interrupt(ControlTarget):
    control_name: ClassVar[ControlName] = "interrupt"


@dataclass(frozen=True)
class CloseSession(ControlTarget):
    control_name: ClassVar[ControlName] = "close_session"


@dataclass(frozen=True)
class RenameSession(ControlTarget):
    control_name: ClassVar[ControlName] = "rename_session"
    name: str


@dataclass(frozen=True)
class AutoNameSession(ControlTarget):
    control_name: ClassVar[ControlName] = "auto_name_session"


@dataclass(frozen=True)
class OpenRewind(ControlTarget):
    control_name: ClassVar[ControlName] = "open_rewind"


@dataclass(frozen=True)
class ApplyRewind(ControlTarget):
    control_name: ClassVar[ControlName] = "apply_rewind"
    target_message_id: MessageId
    target_text: str
    newer_prompt_count: int
    mode: str


@dataclass(frozen=True)
class MigrateAccount(ControlTarget):
    control_name: ClassVar[ControlName] = "migrate_account"


@dataclass(frozen=True)
class Compact(ControlTarget):
    control_name: ClassVar[ControlName] = "compact"


@dataclass(frozen=True)
class SelectModel(ControlTarget):
    control_name: ClassVar[ControlName] = "select_model"
    model_id: str


@dataclass(frozen=True)
class SelectEffort(ControlTarget):
    control_name: ClassVar[ControlName] = "select_effort"
    effort: str


@dataclass(frozen=True)
class AnswerQuestion(ControlTarget):
    control_name: ClassVar[ControlName] = "answer_question"
    attention_id: AttentionId
    decision: Literal["answer", "discuss"]
    answers: StructuredContent | None = None
    discussion: str | None = None


@dataclass(frozen=True)
class ReadPlanChoices(ControlTarget):
    control_name: ClassVar[ControlName] = "read_plan_choices"
    attention_id: AttentionId


@dataclass(frozen=True)
class DecidePlan(ControlTarget):
    control_name: ClassVar[ControlName] = "decide_plan"
    attention_id: AttentionId
    decision: str
    feedback: str | None = None


ControlRequest = Union[
    SendText,
    Interrupt,
    CloseSession,
    RenameSession,
    AutoNameSession,
    OpenRewind,
    ApplyRewind,
    MigrateAccount,
    Compact,
    SelectModel,
    SelectEffort,
    AnswerQuestion,
    ReadPlanChoices,
    DecidePlan,
]

@dataclass(frozen=True)
class ControlResult:
    request_id: str
    status: Literal["acknowledged", "rejected", "indeterminate"]
    reason: str | None = None


@dataclass(frozen=True)
class DeliveryResult(ControlResult):
    queued: bool = False
    restored_text: str = ""


@dataclass(frozen=True)
class CommandResult(ControlResult):
    confirmation: Literal["confirmed", "not_needed", "failed"] | None = None


@dataclass(frozen=True)
class RewindResult(ControlResult):
    restored_text: str = ""
    degraded: bool = False


@dataclass(frozen=True)
class MigrationResult(ControlResult):
    target_account_id: str | None = None


@dataclass(frozen=True)
class PlanChoicesResult(ControlResult):
    choices: tuple[AttentionChoice, ...] = ()


ControlOutcome = Union[
    ControlResult,
    DeliveryResult,
    CommandResult,
    RewindResult,
    MigrationResult,
    PlanChoicesResult,
]

ControlHandler = Callable[
    [ControlRequest, ControlContext],
    ControlOutcome,
]


@dataclass(frozen=True)
class HarnessController:
    handlers: Mapping[ControlName, ControlHandler]

    def execute(
        self,
        request: ControlRequest,
        context: ControlContext,
    ) -> ControlOutcome:
        handler = self.handlers.get(request.control_name)
        if handler is None:
            return ControlResult(
                request_id=request.request_id,
                status="rejected",
                reason="unsupported control",
            )
        return handler(request, context)


@dataclass(frozen=True)
class LaunchRequest:
    working_directory: str
    initial_text: str | None
    model_id: str | None
    effort: str | None
    account_id: str | None
    resume_session_id: SessionId | None
    attachments: tuple[AttachmentReference, ...] = ()


@dataclass(frozen=True)
class HarnessLaunchPlan:
    command: str
    arguments: tuple[str, ...]
    title: str


@dataclass(frozen=True)
class LaunchResult:
    status: Literal["started", "rejected"]
    window_id: str | None = None
    reason: str | None = None


class HarnessLauncher(Protocol):
    def prepare(self, request: LaunchRequest) -> HarnessLaunchPlan: ...


@dataclass(frozen=True)
class ModelOption:
    model_id: str
    display_name: str
    default: bool


@dataclass(frozen=True)
class EffortOption:
    value: str
    display_name: str
    default: bool


@dataclass(frozen=True)
class AccountOption:
    account_id: str
    display_name: str
    available: bool


@dataclass(frozen=True)
class CommandOption:
    command: str
    description: str
    minimum_prompt_count: int


@dataclass(frozen=True)
class RewindModeOption:
    value: str
    display_name: str


@dataclass(frozen=True)
class UsageWindow:
    key: str
    label: str
    used_percent: Decimal
    resets_at: float | None
    duration_minutes: int | None
    scope: Literal["account", "model"]
    model_id: str | None


@dataclass(frozen=True)
class UsageBlock:
    model_id: str | None
    message: str | None
    resets_at: float | None


@dataclass(frozen=True)
class UsageRow:
    harness: str
    account_id: str | None
    display_name: str
    switchable: bool
    plan: str | None
    windows: tuple[UsageWindow, ...]
    scheduling_score: Decimal | None
    scheduling_allowed: bool
    limit: UsageBlock | None
    authentication_error: str | None


@dataclass(frozen=True)
class QueryContext:
    session_id: SessionId | None
    working_directory: str | None


class HarnessCatalog(Protocol):
    sections: frozenset[Literal[
        "models", "efforts", "accounts", "commands", "rewind_modes",
        "speech_terms",
    ]]

    def read(self, context: QueryContext) -> HarnessCatalogSnapshot: ...


@dataclass(frozen=True)
class HarnessCatalogSnapshot:
    models: tuple[ModelOption, ...] = ()
    efforts: tuple[EffortOption, ...] = ()
    accounts: tuple[AccountOption, ...] = ()
    commands: tuple[CommandOption, ...] = ()
    rewind_modes: tuple[RewindModeOption, ...] = ()
    speech_terms: tuple[str, ...] = ()


class HarnessUsage(Protocol):
    def read(self) -> tuple[UsageRow, ...]: ...


class HarnessMemory(Protocol):
    def enabled(self, working_directory: str) -> bool: ...
    def item_count(self, session_id: SessionId) -> int: ...
    def snapshot(self, session_id: SessionId) -> HarnessMemorySnapshot: ...
    def document(
        self,
        path: str | None,
        stem: str | None,
    ) -> MemoryDocument: ...


@dataclass(frozen=True)
class TerminalInputState:
    typed_text: str | None
    suggestion: str | None


class HarnessTerminalProbe(Protocol):
    def input_state(
        self,
        screen: TerminalScreen,
        window_id: str,
    ) -> TerminalInputState | None: ...
```

Each request is an immutable data class with `control_name`, `session_id`, and
`request_id`, plus only user intent. Controls target the lead
session. Nested actors are observable facts, not independently controllable
sessions in the current product. The mapping itself is the capability
declaration, so an advertised control cannot drift away from its
implementation. A missing key means unsupported. There is no inert base
implementation and no default handler.

`HarnessControlService` creates `ControlContext` from the recognized session and a
single canonical projection snapshot. This gives a plugin the current model,
effort, account, and the matching pending attention request without letting it
read dashboard state or parse another harness's files. The browser sends only
the attention identity, decision, answers, and optional discussion text; it
never supplies or replaces the prompts used to drive the native dialog. The
plugin owns
whether discussion text is submitted inside the native dialog or immediately
after declining it.

Manual account migration carries no target selected by shared code. The owning
plugin uses `current_model` and `current_account` to apply its existing native
account-selection rules and returns the selected account in `MigrationResult`.
No catalog, dashboard handler, or browser code implements a Claude-specific
selection ladder.

`replace_terminal_draft` preserves the existing interrupt/rewind flow: the
terminal already contains restored text, so the send handler replaces that
draft before delivering the edited text. It is an explicit current control
instruction, not inferred from screen contents in shared code.

This list is intentionally limited to controls that exist in the product now.
Terminal screen probes, key sequences, verification, and dialog grammar are
private details of the registered handler. A speculative future gesture is not
added until the product actually needs it.

`LaunchRequest` covers fresh and explicit-resume launch. There is no
continue-latest mode.
The plugin prepares `HarnessLaunchPlan`, owning native command names, flags,
account aliases, attachment mentions, and resume rules. The application service
owns the live-session guard and asks `SessionTerminal` to open the tab; the
terminal tier owns the login-shell wrapper. Codex therefore never imports a
Claude Code launch helper. Attachments remain structured until the plugin
renders its native file-mention syntax.

The Codex launch plan invokes `plugins/codex/command.py`, and the interactive
shell uses that same entry. This is not a shared launcher heuristic. The module
is Codex-specific because it knows the npm launcher/native-child relationship,
Codex rollout names, and Codex process evidence. It uses `lsof` only to prove
which rollout the exact native process owns; working-directory or timestamp
matching is forbidden. Another harness that lacks an immediate native start
event implements its equivalent launch observation inside its own plugin and
still reports through `RawEventDelivery`, `SessionStarted`, and
`SessionFinished`.

`LaunchResult` does not contain a guessed session. A new native process is only
a canonical session after its hook or source is recognized. A successful result
does contain the terminal `window_id`; a terminal that cannot identify the new
window makes launch fail. The browser waits for the ordinary session projection
to report that same window and never guesses by directory, timing, or list order.

Model IDs, effort values, account registries, native commands, rewind modes,
and TUI screen grammars remain plugin-owned. Shared callers ask the owning
plugin through a catalog or controller; they never infer ownership from
model-name syntax.
Catalog reads stay separate because each menu is an independent capability.
An unsupported menu returns an empty tuple. A failed catalog read is an error;
the application does not substitute another harness, stale values, or guessed
defaults.

`HarnessTerminalProbe` exists for one current read boundary: the dashboard's
terminal draft synchronization and ghost suggestion. `TerminalFrontend` reads
screen text; the owning plugin knows the native input-box geometry and styling
and returns only semantic text. Shared dashboard code never parses a Claude or
Codex screen. A plugin without this current feature omits the probe, and the UI
does not advertise terminal synchronization for that session.

`HarnessMemory` is one optional, named product capability rather than a generic
extension map. Its typed snapshot contains note accesses and searches; its
document result contains unrendered markdown, frontmatter, and backlinks. The
owning plugin captures and stores its native memory evidence after raw hook and
canonical facts commit. The dashboard owns tree layout, safe markdown rendering,
and the existing Memory-tab HTML. Shared code never imports a vault parser or a
concrete plugin, and a harness without this feature omits the port.

### 9.6 Discovery and registry

Concrete plugins are discovered by one local package convention:

```text
plugins/<package>/plugin.py exports plugin: HarnessPlugin
```

`app.plugins.installed_plugins` scans only that immediate path and imports each
descriptor. It contains no harness names. This repository is run directly and
has no Python distribution metadata, so package entry points would add a second
installation system without removing any code. A future harness is installed
by adding its own folder and descriptor; the composition root does not change.
`plugins/__init__.py` is only a package marker. It contains no provider fan-out,
default harness, feature implementation, or concrete harness import; every
harness-owned function lives below its own plugin directory.

The shared registry performs lookup and validation only:

```python
class HarnessRegistry:
    def register(self, plugin: HarnessPlugin) -> None: ...
    def plugin(self, harness: str) -> HarnessPlugin: ...
    def discover_sessions(self) -> tuple[RegisteredSession, ...]: ...
    def recognize(self, candidate: SessionCandidate) -> RegisteredSession: ...
    def plugin_for_session(self, session_id: SessionId) -> HarnessPlugin: ...
@dataclass(frozen=True)
class RegisteredSession:
    plugin: HarnessPlugin
    session: RecognizedSession
```

Registration rejects duplicate names and unsupported canonical versions.
Recognition asks each installed plugin once. Exactly one recognized session
succeeds; zero means unsupported and more than one is a configuration error.
Both failures are typed and audited.

The registry persists the lead-session and lead-actor bindings in the event
database after recognition and before starting event sources. Every accepted
canonical fact persists or verifies `(session_id, actor_id, harness)`, not only
`actor.started`; a first-observed finish is therefore still owned. Actor
ownership keeps mixed-harness activity auditable and prevents one plugin from
taking over another plugin's actor. Controls use the lead-session binding only.
The registry contains no default-harness choice and no native recovery logic.

### 9.7 Coverage against the current product

The contracts cover the behavior that exists today without adding speculative
extension points:

| Current behavior | Target abstraction |
|---|---|
| transcript, child-stream, rollout and watcher observation | `HarnessEventSource` |
| synchronous native hooks, exact hook evidence and native stdout | `HarnessHook` through `HarnessHookService` |
| native record parsing, identity correlation, liveness interpretation | `HarnessEvents.translate` |
| session/path ownership | `SessionRecognizer` |
| send, interrupt, close, rename, automatic name, rewind, account migration, compact, model, effort, question and plan controls | registered `HarnessController.handlers` |
| typed text, uploads and native file mentions | `SendText.attachments` and plugin-owned rendering |
| fresh launch, explicit resume and initial attachments | `HarnessLauncher.prepare` plus `HarnessLauncherService` |
| model, effort, account, command availability and rewind menus | `HarnessCatalog` |
| switchable-account and harness-wide current rate limits | `HarnessUsage` through `ApplicationUsageState` |
| terminal draft synchronization and ghost suggestion | `HarnessTerminalProbe` through `TerminalInputService` |
| conversations, actors, tasks, goals, usage, context, compaction and pending attention | canonical events plus `SessionQueries` |
| prompt count, model-change warning, running work, monitor/job badges | focused canonical projections |
| tab state, scoreboard and pane lifecycle | semantic projections plus `TerminalFrontend` |
| dashboard activity, agent scope, history and live updates | `DashboardActivityService` |
| uploads, drafts, queue, preferences, repository, notifications and operational badges | application APIs outside the canonical model |
| exact native and interpreted evidence | transactional event storage tables |

OTEL receipt, notification delivery, repository state, composer drafts, and
dashboard preferences are application infrastructure rather than harness
plugins. They keep their existing narrow services and may produce canonical
usage events only when they provide harness activity evidence.

The architecture intentionally does not define plugin subprocess isolation,
remote plugins, hot reload, dynamic event schemas, a generic metadata bag, or
automatic recovery for capabilities the harness does not implement. Those are
not present requirements and would make the common path harder to understand.

## 10. Ingestion pipeline

The complete ingestion path is intentionally small:

```python
@dataclass(frozen=True)
class IngestionResult:
    raw_event_id: RawEventId
    translation_decision: Literal[
        "translated", "ignored_unknown", "ignored_nonsemantic",
        "translation_failed",
    ]
    accepted_event_ids: tuple[CanonicalEventId, ...]
    deduplicated_event_ids: tuple[CanonicalEventId, ...]
    latest_cursor: int | None


class EventPipeline:
    def ingest(self, raw_event: RawEvent) -> IngestionResult:
        plugin = self.registry.plugin(raw_event.harness)
        try:
            translation = plugin.events.translate(raw_event)
        except TranslationError as error:
            return self.event_store.record_failure(
                raw_event,
                plugin.info.plugin_version,
                error,
            )

        stored = self.event_store.record(
            raw_event,
            plugin.info.plugin_version,
            translation,
        )
        return IngestionResult(
            raw_event_id=raw_event.raw_event_id,
            translation_decision=translation.decision,
            accepted_event_ids=tuple(
                stored_event.event.event_id
                for stored_event in stored.accepted
            ),
            deduplicated_event_ids=stored.duplicate_event_ids,
            latest_cursor=stored.latest_cursor,
        )
```

The composition root wraps that pure storage pipeline once for current
application effects:

```python
class ApplicationEventDelivery:
    def deliver(self, raw_event: RawEvent) -> IngestionResult:
        result = self.pipeline.ingest(raw_event)
        for event_id in result.accepted_event_ids:
            event = self.event_store.require(event_id).event
            self.session_lifecycle.apply(event)
        return result


class SessionLifecycleService:
    def apply(self, event: CanonicalEvent) -> None:
        if isinstance(event.payload, SessionStarted):
            action = "started"
        elif isinstance(event.payload, SessionFinished):
            action = "finished"
        else:
            return
        plugin = self.registry.plugin_for_session(event.session_id)
        if plugin.lifecycle is not None:
            plugin.lifecycle.apply(
                SessionLifecycleRequest(action),
                self.registry.registered_session(event.session_id).session,
                self.context,
            )
```

`SessionLifecycleService` is a concrete application service, not a generic
effects framework. It knows exactly two canonical event classes and has no
handler registry. It applies lifecycle only for newly accepted facts; repeated
raw provenance cannot reopen or reclose panes. Process epochs have distinct
canonical identities, so resume remains a real new lifecycle fact. Generic pane
open/close is idempotent. Lifecycle failures are operational
failures and are recorded in operational audit; they never alter the committed
raw or canonical evidence. There is no effects queue, compensating action,
rollback, or recovery worker.

Required behavior:

1. Translation is deterministic and has no side effects.
2. `record` stores the raw event, translation decision, canonical events, and
   provenance in one transaction.
3. `record_failure` stores the raw event and failed decision in one transaction;
   it never invents a canonical fact.
4. Duplicate `event_id` values are harmless and reported as deduplicated.
5. Any storage failure raises and prevents source-checkpoint advancement. There
   is no spool, alternate writer, or partial audit state.
6. Projection failure never deletes or mutates committed canonical facts.
7. A raw observation that intentionally produces no canonical event receives
   an explicit translation decision such as `ignored_unknown` or
   `ignored_nonsemantic`.
8. A successfully recorded translation failure is complete and lets the source
   advance. A database failure retries the same `raw_event_id` through normal
   source delivery.

This is not an in-process publish/subscribe bus. Consumers read the canonical
store by cursor. That makes crash recovery the same operation as ordinary
incremental reading.

## 11. Runtime canonical storage

One WAL-backed SQLite database stores event evidence and harness ownership.
This gives the application one transaction boundary and one cursor without
carrying forward per-session database parking.

Every runtime database context owns exactly one connection and closes it on
exit; SQLite's transaction context alone is insufficient because it commits or
rolls back without closing the descriptor. Audit and plugin-owned SQLite stores
follow the same explicit lifetime rule. Session-list discovery selects only
sessions with a canonical `session.started` fact; a registered native candidate
is ownership metadata, not yet a semantic session.

```sql
CREATE TABLE raw_events(
    raw_event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_position TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    parent_actor_id TEXT,
    observed_at REAL NOT NULL,
    encoding TEXT NOT NULL,
    payload BLOB NOT NULL
);

CREATE TABLE translation_records(
    raw_event_id TEXT PRIMARY KEY,
    translator_version TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(
        decision IN (
            'translated', 'ignored_unknown', 'ignored_nonsemantic',
            'translation_failed'
        )
    ),
    reason TEXT,
    completed_at REAL NOT NULL,
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id)
);

CREATE TABLE canonical_events(
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    schema_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    turn_id TEXT,
    parent_actor_id TEXT,
    harness TEXT NOT NULL,
    occurred_at REAL,
    accepted_at REAL NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX index_canonical_session_type
    ON canonical_events(session_id, event_type, cursor);

CREATE INDEX index_canonical_session_actor
    ON canonical_events(session_id, actor_id, cursor);

CREATE INDEX index_raw_session
    ON raw_events(session_id, observed_at);

CREATE TABLE canonical_provenance(
    event_id TEXT NOT NULL,
    raw_event_id TEXT NOT NULL,
    event_order INTEGER NOT NULL,
    storage_result TEXT NOT NULL CHECK(
        storage_result IN ('accepted', 'deduplicated')
    ),
    PRIMARY KEY(event_id, raw_event_id),
    UNIQUE(raw_event_id, event_order),
    FOREIGN KEY(event_id) REFERENCES canonical_events(event_id),
    FOREIGN KEY(raw_event_id) REFERENCES raw_events(raw_event_id)
);

CREATE TABLE session_harness(
    session_id TEXT PRIMARY KEY,
    lead_actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    working_directory TEXT,
    native_process_id INTEGER
);

CREATE TABLE actor_harness(
    session_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    PRIMARY KEY(session_id, actor_id)
);

CREATE TABLE source_checkpoints(
    source_identity TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    position TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);

CREATE TABLE session_application_state(
    session_id TEXT PRIMARY KEY,
    composer_text TEXT NOT NULL DEFAULT '',
    composer_origin TEXT NOT NULL DEFAULT '',
    composer_sequence REAL NOT NULL DEFAULT 0,
    queued_messages TEXT NOT NULL DEFAULT '[]',
    queue_origin TEXT NOT NULL DEFAULT '',
    dialog_attention_id TEXT,
    dialog_answers TEXT NOT NULL DEFAULT '[]',
    dialog_origin TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(session_id) REFERENCES session_harness(session_id) ON DELETE CASCADE
);
```

The public API remains narrow:

```python
class EventStore:
    def record(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation: TranslationResult,
    ) -> EventRecordResult: ...
    def record_failure(
        self,
        raw_event: RawEvent,
        translator_version: str,
        error: TranslationError,
    ) -> IngestionResult: ...
    def after(self, session_id: str, cursor: int, limit: int) -> EventPage: ...
    def through(self, session_id: str, cursor: int | None = None) -> EventPage: ...


@dataclass(frozen=True)
class StoredEvent:
    cursor: int
    accepted_at: float
    event: CanonicalEvent
    raw_event_ids: tuple[RawEventId, ...]


@dataclass(frozen=True)
class EventRecordResult:
    accepted: tuple[StoredEvent, ...]
    duplicate_event_ids: tuple[CanonicalEventId, ...]
    latest_cursor: int


@dataclass(frozen=True)
class EventPage:
    events: tuple[StoredEvent, ...]
    cursor: int
    latest_cursor: int
    has_more: bool
```

Consumers do not execute ad hoc SQL against this table. The store owns schema
creation, decoding, version checks, and cursor behavior.

Every successful or deduplicated record inserts its provenance pair. This
retains all independently observed evidence without mutating an already
accepted event. `canonical_events` is both the runtime fact table and the
canonical audit record; there is no duplicate canonical-audit payload table.

The runtime accepts exactly one canonical schema version. A schema change
replaces the derived event database and all bundled plugins update in the same
release. There is no converter or multi-version reader. Native source evidence
is ingested again by the ordinary plugin sources.

### 11.1 Transaction and cursor contract

- `record(raw_event, translation)` opens one immediate transaction.
- The store samples its clock once per new observation. That value is both the
  translation `completed_at` and every newly accepted event's `accepted_at`;
  `observed_at` remains the source's evidence timestamp.
- It validates that every event belongs to the raw event's session and supported
  schema version before inserting anything.
- It inserts the raw bytes, translation record, new events, and provenance for
  new and duplicate events, then commits once. Partial recording of one raw
  observation is forbidden.
- An existing `raw_event_id` is idempotent only when its session, actor context,
  harness, source coordinates, encoding, and exact payload bytes are identical. The
  first committed `observed_at` is retained; a checkpoint retry does not become
  a new observation merely because its delivery attempt happened later. An
  existing `event_id` is idempotent only when its canonical bytes are
  identical. Conflicts fail the transaction.
- `record_failure(raw_event, error)` uses the same transaction boundary for a
  raw event and its failed translation record.
- Session recognition creates `session_harness` before sources start.
  It also creates the lead `actor_harness` binding. Every accepted actor fact
  creates or verifies its actor binding in the event transaction; a conflicting
  binding rejects the whole record.
- `cursor` is monotonically increasing in the event database. Consumers treat
  it as opaque, always filter by `session_id`, and never assume consecutive
  values because other sessions share the sequence.
- `after(session, cursor, limit)` returns later rows in ascending cursor order.
- `EventPage.cursor` is the highest returned cursor, or the requested cursor
  when the page is empty.
- `latest_cursor` is the highest committed cursor at the query's read
  snapshot and supports deterministic backlog-to-live handoff.
- Session end/resume does not move the event database. Native sources continue
  from their own recorded checkpoints.

### 11.2 Application query APIs

Callers do not consume arbitrary event lists when they need current state. The
runtime exposes focused semantic queries:

```python
class SessionQueries:
    def sessions(self) -> tuple[SessionSummary, ...]: ...
    def summary(self, session_id: SessionId) -> SessionSummary | None: ...
    def actors(self, session_id: SessionId) -> tuple[ActorSummary, ...]: ...
    def activity_after(
        self,
        session_id: SessionId,
        cursor: int,
        scope: ActivityScope,
        limit: int,
    ) -> ActivityPage: ...
    def activity_before(
        self,
        session_id: SessionId,
        before_cursor: int | None,
        scope: ActivityScope,
        block_count: int,
        through_cursor: int | None = None,
    ) -> ActivityWindow: ...
    def usage(self, session_id: SessionId) -> UsageSummary: ...
    def context(self, session_id: SessionId) -> ContextSummary: ...
    def attention(self, session_id: SessionId) -> AttentionState: ...
    def tasks(self, session_id: SessionId) -> tuple[TaskSummary, ...]: ...
    def goal(self, session_id: SessionId) -> GoalState | None: ...
    def background_work(
        self,
        session_id: SessionId,
        scope: ActivityScope,
    ) -> BackgroundWorkSummary: ...


@dataclass(frozen=True)
class ActivityScope:
    actor_id: ActorId | None = None   # None = lead/session view


@dataclass(frozen=True)
class ActivityPage:
    cursor: int
    latest_cursor: int
    activities: tuple[Activity, ...]
```

`SessionQueries` replaces the read-side plugin fan-outs currently used to ask
each harness for conversations, agents, context, tasks, and usage. Plugin-owned
configuration queries remain behind `HarnessCatalog`; observed session facts
come from canonical projections.

### 11.3 Commands versus queries

Application services form the only write API above storage:

```python
class HarnessControlService:
    def execute(self, request: ControlRequest) -> ControlOutcome: ...


class TerminalInputService:
    def read(self, session_id: SessionId) -> TerminalInputState | None: ...
```

The service resolves session ownership, verifies capability presence, calls
the registered plugin handler, and audits the attempt/result. HTTP handlers never reach
plugin controls directly. Read APIs never cause native I/O except through an
explicitly documented `HarnessCatalog` call for configuration data or
`TerminalInputService` for the existing live draft/suggestion feature.
`TerminalInputService` resolves the owning plugin and calls its optional
`HarnessTerminalProbe`; dashboard code never reaches a plugin or parses screen
text itself. The probe receives only `TerminalScreen`, not the full terminal
control interface. Harness screen grammar therefore cannot open panes, send
input, recolor tabs, or acquire any other terminal responsibility.

## 12. Audit of raw and canonical events

### 12.1 Required evidence

Audit must answer:

1. What exact bytes did the harness provide?
2. Where and when were they observed?
3. Which translator version interpreted them?
4. Which canonical events were produced?
5. Which raw observations caused each canonical event?
6. Was a record ignored, rejected, malformed, or deduplicated?

### 12.2 Audit schema

The audit is the storage model from section 11, not a copied second model:

- `raw_events` stores the exact bytes as a BLOB and their source coordinates;
- `translation_records` stores translator version, decision, and reason;
- `canonical_events` stores deterministic canonical JSON and is runtime truth;
- `canonical_provenance` links each interpretation to every raw observation and
  records translation order and whether that observation first accepted or
  deduplicated the fact.

These four tables commit together per observation. Audit cannot be disabled
independently, fall back to another file, or lag behind canonical storage.

One raw observation may yield several canonical rows:

```text
raw_event_id = codex:rollout:<file-id>:18382

raw event           response_item bytes
canonical event 1   operation.finished  accepted
canonical event 2   usage.reported       deduplicated
translation         translated
```

The audit CLI reads these tables and presents raw observation -> translation ->
canonical facts. Replaying the same `raw_event_id` returns its already recorded
result. Reusing an ID with different bytes is a hard error.

The concrete inspection surface is deliberately small:

```text
python3 bin/baqylau-audit.py raw <raw_event_id>
python3 bin/baqylau-audit.py session <session_id>
```

It emits JSON. `payload_base64` preserves the exact raw bytes; each canonical
entry includes its decoded envelope, translation order, accepted timestamp,
and accepted/deduplicated provenance result.

### 12.3 Existing operational audit remains

`hook_events`, `streams`, `tab_transitions`, `spawns`, `errors`,
`state_files`, and `pane_events` describe operational decisions and effects,
which differ from harness facts. They remain separate and are simplified to
direct SQLite writes. The target has no spool, replay worker, or alternate
diagnostic store. A diagnostic-write failure is reported but is not converted
into harness activity.

The complete evidence chain is:

```text
raw observation    exact harness evidence
canonical event    Baqylau's interpretation
decision           accepted/ignored/deduplicated and why
effect             tab/pane/control/spawn outcome
error              failure with traceback/context
```

Canonical events are runtime truth and canonical audit evidence. Operational
diagnostic tables cannot be used as a hidden runtime dependency.

### 12.4 Sensitivity, volume, and retention

Raw harness records and canonical text may contain source code, prompts,
credentials printed by tools, local paths, and other sensitive material. The
event database therefore keeps restrictive local-file permissions and never
exposes raw rows through the dashboard API.

Raw bytes and canonical JSON are stored directly. There is no content
deduplication, blob service, audit-specific retention policy, or pruning worker.
They follow the same session-history lifecycle as the canonical session.

## 13. Semantic projections

Most consumers should not independently join operation starts/finishes or
actor-assignment endpoints. A small shared semantic projection may provide completed
or currently-open activity records.

```python
@dataclass(frozen=True)
class ActivityContext:
    activity_id: str
    source_event_ids: tuple[CanonicalEventId, ...]
    session_id: SessionId
    actor_id: ActorId
    actor_name: str | None
    parent_actor_id: ActorId | None
    turn_id: TurnId | None
    started_at: float | None
    finished_at: float | None


@dataclass(frozen=True)
class MessageActivity:
    context: ActivityContext
    message_id: MessageId
    role: Literal["user", "assistant", "system", "peer"]
    phase: Literal["prompt", "intermediate", "final", "synthetic"] | None
    reply_to: MessageId | None
    content: Content


@dataclass(frozen=True)
class ReasoningActivity:
    context: ActivityContext
    reasoning_id: str
    content: Content
    summary: bool


@dataclass(frozen=True)
class OperationActivity:
    context: ActivityContext
    operation_id: OperationId
    category: OperationCategory | None
    native_name: str | None
    execution: ExecutionMode | None
    arguments: Content | None
    description: str | None
    parent_operation_id: OperationId | None
    progress: tuple[OperationProgressed, ...]
    state: Literal["running", "finished"]
    outcome: Outcome | None
    result: Content | None
    exit_code: int | None


@dataclass(frozen=True)
class FileActivity:
    context: ActivityContext
    file: FileAccessed
    progress: tuple[OperationProgressed, ...]
    outcome: Outcome | None
    result: Content | None
    content_event_id: CanonicalEventId | None
    content_field: str | None


@dataclass(frozen=True)
class AttentionActivity:
    context: ActivityContext
    attention_id: AttentionId
    attention_type: Literal["permission", "question", "plan", "confirmation"] | None
    prompts: tuple[AttentionPrompt, ...]
    phase: Literal["requested", "resolved"]
    decision: Literal[
        "answered", "approved", "changes_requested", "rejected",
        "confirmed", "denied", "discussed",
    ] | None
    answers: tuple[AttentionAnswer, ...]
    feedback: str | None
    edited: bool
    outcome: Outcome | None


@dataclass(frozen=True)
class TaskActivity:
    context: ActivityContext
    change: TaskChanged


@dataclass(frozen=True)
class CompactionActivity:
    context: ActivityContext
    before_tokens: int | None
    after_tokens: int | None


@dataclass(frozen=True)
class ActorAssignmentActivity:
    context: ActivityContext
    assignment_id: AssignmentId
    brief: Content | None
    state: Literal["running", "finished"]
    outcome: Outcome | None
    result: Content | None
    reason: str | None


@dataclass(frozen=True)
class ActorMessageActivity:
    context: ActivityContext
    message_id: MessageId
    recipient_actor_id: ActorId
    content: Content | None


Activity = Union[
    MessageActivity,
    ReasoningActivity,
    OperationActivity,
    FileActivity,
    AttentionActivity,
    TaskActivity,
    CompactionActivity,
    ActorAssignmentActivity,
    ActorMessageActivity,
]


@dataclass(frozen=True)
class SessionSummary:
    session_id: SessionId
    harness: str
    title: str | None
    working_directory: str
    initial_working_directory: str
    started_at: float
    finished_at: float | None
    lead_actor_id: ActorId
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None
    prompt_count: int
    automatic_model_change: ModelChanged | None
    state: Literal["running", "finished"]


@dataclass(frozen=True)
class ActorSummary:
    actor_id: ActorId
    parent_actor_id: ActorId | None
    harness: str
    role: ActorRole
    name: str
    description: str | None
    model: ModelReference | None
    effort: str | None
    state: Literal["running", "finished"]
    started_at: float | None
    finished_at: float | None


@dataclass(frozen=True)
class UsageSummary:
    tokens: TokenUsage
    cost_in_usd: Decimal | None
    by_actor: Mapping[ActorId, TokenUsage]
    by_model: Mapping[str, TokenUsage]


@dataclass(frozen=True)
class PendingAttention:
    actor_id: ActorId
    request: AttentionRequested


@dataclass(frozen=True)
class AttentionState:
    pending: tuple[PendingAttention, ...]


@dataclass(frozen=True)
class ContextWindow:
    used_tokens: int
    window_tokens: int
    model: ModelReference | None


@dataclass(frozen=True)
class ContextSummary:
    by_actor: Mapping[ActorId, ContextWindow]
    compacting_actor_ids: tuple[ActorId, ...]


@dataclass(frozen=True)
class TaskSummary:
    task_id: TaskId
    label: str
    subject: str
    description: str | None
    state: Literal["pending", "in_progress", "completed"]
    owner_actor_id: ActorId | None


@dataclass(frozen=True)
class GoalState:
    objective: str
    state: Literal[
        "active",
        "paused",
        "blocked",
        "usage_limited",
        "budget_limited",
        "completed",
    ]
    reason: str | None


@dataclass(frozen=True)
class BackgroundWorkSummary:
    running_operation_ids: tuple[OperationId, ...]
    monitor_count: int
    background_job_count: int


@dataclass(frozen=True)
class ActivityWindow:
    oldest_cursor: int
    activities: tuple[Activity, ...]
    has_more: bool
```

Activity time is the canonical event's native `occurred_at` when present and
the store's immutable `accepted_at` otherwise. This is recorded observation
time, not guessed lifecycle. It gives every running activity a stable elapsed
anchor without a second foreground-running channel.

This read model is semantic, not visual. It must not contain:

- titles or prose generated for one surface;
- glyphs, RGB, ANSI, CSS classes, or HTML;
- width, wrapping, truncation, or gutters;
- `web`, `bubbled`, `chrome`, or equivalent consumer-routing flags;
- copy-link glyph selection;
- harness-name branches.

If this layer starts accumulating display strings, it has become another
presentation model and must be corrected.

Not every event needs to become an `Activity`. Session metadata, usage totals,
attention state, and tab policy may have their own focused projections.

File-category operations are represented in activity views by their linked
`FileAccessed` facts rather than by a duplicate generic operation item. A patch
therefore yields one file activity per changed file, while a Read/Edit/Write
yields one. Linked progress and finish facts update that file activity with
viewable content and outcome. `content_event_id` plus `content_field` identifies
the canonical field that owns the uncapped content; it is projection metadata,
not a stored presentation copy.

Attention-category operations are also absent as duplicate generic operation
items. The request and its later resolution become two immutable
`AttentionActivity` entries because the existing UI shows a question/answer or
plan/decision exchange. `TaskChanged` and `CompactionFinished` each become
their own activity entry as well. Both frontends receive those semantics
directly; neither may recover them from tool names, strings, or glyphs.

`AttentionResolved` stores the semantic decision, prompt-keyed answers,
optional feedback, and whether an approved plan was edited. It never stores a
native `tool_response` object. This is the minimum information both presenters
need to reproduce structured answer cards and plan verdicts without a
harness-specific parser or wording test.

### 13.1 Projection ownership and update rules

Each projection has one owner and a declared input-event-type set:

| Projection | Consumes | Serves |
|---|---|---|
| session summary | `session.*`, lead `model.changed`, lead `effort.changed`, lead `actor.*`, user prompt messages | session list/header, prompt count and model-change warning |
| actor index | `actor.*`, `model.changed`, `effort.changed` | agent cards and scope |
| activity | messages, reasoning, operations, files, attention exchanges, task changes, compaction finishes, actor assignments, actor messages | terminal/dashboard streams |
| usage | `usage.reported` | scoreboard, costs, stats |
| context | `context.reported`, compaction | context chips/bars |
| attention | `attention.*` | ask/plan cards and tab policy |
| task list | `task.changed` | pinned tasks card |
| goal | `goal.changed` | active goal card |
| background work | background/monitor operations by actor | running ribbon and monitor/job badges |
| tab state | turn/operation/attention/actor lifecycle | tab colour |

Projection code is harness-neutral and exhaustive over the canonical event
types it declares. The canonical schema and projectors change together, so
there is no runtime catch-all that guesses or silently drops a new event type.

Open activities are materialized by semantic ID. A finish updates the existing
activity; it does not append a second unrelated visual block unless the current
UI already represents start and finish separately. That surface behavior is
owned by each presenter and pinned by parity tests.

The activity fold tracks two cursor meanings internally without creating a
second transport cursor. `position_cursor` is the cursor where the activity
first appeared and fixes its history order. `revision_cursor` is the cursor of
its latest contributing event and drives live updates. Backlog pagination uses
position; live pagination sorts and advances by revision. A late finish can
therefore update an old operation without moving it to another history page or
skipping an intervening message. Both values come from the one canonical event
cursor and are never exposed as competing browser cursors.

The prompt count is derived from canonical user messages whose phase is
`prompt`; it is not a plugin query. Command availability compares that count
with `CommandOption.minimum_prompt_count`. The automatic model-change warning
is projected from `model.changed` with `reason="automatic_fallback"`. Error and
extension badges remain application/audit projections; monitor and background
job badges derive from canonical operations and actor scope.

### 13.2 Event-to-existing-surface mapping

The canonical model changes the input to each frontend, not what users see.
For every event family, the presenters reproduce the corresponding current UI:

| Canonical fact | Terminal presenter | Dashboard presenter / focused projection |
|---|---|---|
| `session.*` | existing pane/session lifecycle chrome where currently shown | existing session row, title, running state |
| `actor.*` | existing agent lifecycle lines and scoreboard rows | existing agent card, badge, and scope entry |
| `model.changed`, `effort.changed` | existing scoreboard/header values | existing model/effort labels and menus |
| `turn.*` | existing turn boundaries, finalization, and tab transitions | existing running/final state; no new feed card solely for a boundary |
| `message.created` | existing user/assistant/peer text block | existing bubble/card with identical grouping and HTML |
| `reasoning.created` | existing reasoning block and truncation policy | existing reasoning item and view-mode behavior |
| `operation.*` | existing tool start/progress/result block, replacement rules, glyphs, and colours | existing operation card/item, grouping, expansion, and result state |
| `file.accessed` | existing read/edit/write/diff presentation | existing file item and copy/view behavior |
| `actor.assignment_*` | existing actor-assignment launch/completion lines and source grouping | existing agent card, parent summary, and agent-scope behavior |
| `task.changed` | existing task indicator where currently present | existing pinned task card |
| `goal.changed` | no new terminal output | existing active-goal card |
| `actor.message_sent` | existing actor message display | existing actor-message item |
| `attention.*` | existing tab colour/prompt behavior | existing ask/plan dialog and optimistic interaction |
| `usage.reported` | existing scoreboard arithmetic and labels | existing stats and cost fields |
| `context.reported` | existing context display | existing context chip/bar |
| `compaction.*` | existing compacting status/transitions | existing compacting and refreshed-context events |

“Existing” is deliberately normative here: this proposal does not authorize a
new glyph, copy string, card, bubble, badge, animation, ordering rule, or empty
state. If a canonical fact has no visible representation today, it remains
available to audit/read models without automatically gaining one.

## 14. Terminal presentation

The terminal keeps its appearance and behavior, but its internal input model is
replaced. The useful rendering algorithms remain:

- width-aware wrapping and resize reflow;
- ANSI styling and syntax highlighting;
- gutter/panel layout;
- scrollback behavior;
- click-to-view expansion;
- bounded in-memory rendering history.

The final flow is direct:

```text
canonical activity -> terminal presenter -> terminal update -> renderer
```

There is no shared or persisted intermediate drawing language. The terminal
update model is private to the terminal package and carries only rendering
instructions:

```python
@dataclass(frozen=True)
class TerminalStyle:
    foreground: RGB | None = None
    background: RGB | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    dim: bool = False


@dataclass(frozen=True)
class TerminalText:
    text: str
    style: TerminalStyle
    link_target: str | None = None


@dataclass(frozen=True)
class TerminalLine:
    content: tuple[TerminalText, ...]
    prefix: tuple[TerminalText, ...] = ()
    continuation_prefix: tuple[TerminalText, ...] = ()
    background: RGB | None = None
    layout: Literal["wrap", "truncate", "verbatim"] = "wrap"


@dataclass(frozen=True)
class TerminalRule:
    style: TerminalStyle = TerminalStyle()


@dataclass(frozen=True)
class TerminalBlank:
    pass


TerminalRow = TerminalLine | TerminalRule | TerminalBlank


@dataclass(frozen=True)
class TerminalBlock:
    block_id: str
    rows: tuple[TerminalRow, ...]


@dataclass(frozen=True)
class TerminalUpdate:
    updated_blocks: tuple[TerminalBlock, ...] = ()
    remove_block_ids: tuple[str, ...] = ()
```

Names describe the visible concept directly. There are no abbreviated drawing
record names or consumer-routing flags.

Rows are logical and width-independent. `prefix` is drawn on the first wrapped
line, `continuation_prefix` on each continuation, `background` fills the
physical row, and `layout` states whether content wraps, truncates, or remains
verbatim. This is enough for the existing gutters, panels, rules, code rows,
and copy links without reviving an untyped drawing dictionary. The renderer,
not the presenter, owns the current width, so `reflow(width)` is a real redraw
from retained logical rows rather than a no-op or a request to recover semantics.

The terminal presenter owns:

- glyph choice;
- semantic colour palette and RGB mapping;
- ANSI and hyperlink generation;
- terminal wording;
- gutters, panels, wrapping, capping, and code rendering;
- whether host lifecycle scaffolding is useful in a shared pane;
- terminal copy affordances.

It derives scope and grouping from canonical actor, operation, and task IDs. It
does not receive source prefixes, routing booleans, terminal tags, or baked
display metadata as domain fields.

### 14.1 Frontend versus presenter

The terminal frontend and terminal presenter remain distinct:

```python
@dataclass(frozen=True)
class RGB:
    red: int
    green: int
    blue: int


@dataclass(frozen=True)
class TerminalResult:
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True)
class PaneRequest:
    session_id: SessionId
    command: tuple[str, ...]
    working_directory: str
    title: str


@dataclass(frozen=True)
class PaneResult:
    succeeded: bool
    pane_id: str | None
    window_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class ResizeRequest:
    columns: int | None = None
    rows: int | None = None


@dataclass(frozen=True)
class ScreenText:
    text: str


class TerminalScreen(Protocol):
    def read_screen(
        self,
        window_id: str,
        ansi: bool = False,
    ) -> ScreenText | None: ...


@dataclass(frozen=True)
class TextSubmission:
    text: str
    mode: Literal["type", "paste"]


class TerminalControl(TerminalScreen, Protocol):
    def window_for_session(self, session_id: SessionId) -> str | None: ...
    def submit_text(
        self,
        window_id: str,
        submission: TextSubmission,
    ) -> TerminalResult: ...
    def send_key(self, window_id: str, key: str) -> TerminalResult: ...
    def close_tab(self, window_id: str) -> TerminalResult: ...
    def set_tab_title(self, window_id: str, title: str) -> TerminalResult: ...


class TerminalFrontend(TerminalControl, Protocol):
    def usable(self) -> bool: ...
    def current_window(self) -> str | None: ...
    def tag_window(self, window_id: str, tags: Mapping[str, str]) -> TerminalResult: ...
    def set_tab_color(self, window_id: str, color: RGB) -> TerminalResult: ...
    def clear_tab_color(self, window_id: str) -> TerminalResult: ...
    def open_pane(self, request: PaneRequest) -> PaneResult: ...
    def close_pane(self, pane_id: str) -> TerminalResult: ...
    def resize_pane(self, pane_id: str, request: ResizeRequest) -> TerminalResult: ...


@dataclass(frozen=True)
class SessionPaneRequest:
    session_id: SessionId
    anchor_window_id: str
    activity_width_percent: int


class SessionPaneControl(Protocol):
    def open_session_panes(self, request: SessionPaneRequest) -> TerminalResult: ...
    def close_session_panes(self, session_id: SessionId) -> TerminalResult: ...


class TerminalPresenter:
    def present(
        self,
        activity: Activity,
    ) -> TerminalUpdate: ...


class TerminalRenderer:
    def apply(self, update: TerminalUpdate) -> None: ...
    def reflow(self, width: int) -> None: ...
```

`TerminalFrontend` expresses terminal mechanics only. It has no Claude/Codex
methods, payloads, executable names, or model vocabulary. Kitty implements it;
a future terminal can implement the same contract.

`TerminalControl` is the smaller capability handed to a harness controller. It
can submit text, send semantic key events, close or title the target tab, and
read unparsed screen text. It cannot open or resize panes, tag windows, or paint
tab state. `TextSubmission.mode` distinguishes ordinary typing from the
atomic bracketed-paste delivery required by current TUI controls.

`TerminalScreen.read_screen` returns unparsed terminal text. Only a harness's
`HarnessTerminalProbe` may interpret its native input region. This keeps the
current terminal-draft synchronization and ghost-suggestion behavior without
putting Claude screen rules in the dashboard or terminal frontend.

`TerminalPresenter` owns visual policy. `TerminalRenderer` owns physical layout
and ANSI emission. Neither calls a harness plugin.

`SessionPaneControl` is the application-level pane layout capability injected
only into lifecycle plugins. It owns the stable activity/scoreboard tags,
process commands, split placement, and focus restoration. Plugins choose only
whether lifecycle applies, the anchor window, and the configured activity-pane
width. They never launch a renderer executable or maintain a drawing database.

### 14.2 Host/session lifecycle

Shared application code asks a frontend to open/close/tag panes through opaque
session IDs. The owning harness plugin decides when its session starts, ends,
resumes, or is nested and reports those facts/calls lifecycle services through
contracts. Shared pane code must not detect Codex processes, Claude hooks, or
native environment variables.

Harness-specific terminal variables, executable names, and link schemes belong
to their plugin or terminal presenter. The target architecture does not retain
harness-named shared variables or indirection layers.

### 14.3 Tab colour and scoreboard projections

Tab colour is a focused projection over semantic state:

```python
class TabStateProjector:
    def apply(self, event: StoredEvent, state: TabState) -> TabState: ...


class ScoreboardProjector:
    def apply(self, event: StoredEvent, state: ScoreboardState) -> ScoreboardState: ...
```

The projection state vocabulary preserves today's visible behavior. The
presenter maps those states to the existing colours/labels. Harness plugins
produce facts such as `attention.requested`, `operation.started`,
`operation.finished`, `turn.finished`, and `usage.reported`; they do not choose
tab RGB or scoreboard text.

Native gaps that currently require liveness watchers remain plugin-owned
sources. For example, a plugin may emit a proven `turn.aborted` after observing
its native cancellation signal. The shared tab projector reacts identically
regardless of which plugin proved it.

The concrete shared state vocabulary is `idle`, `thinking`, `working`,
`executing`, `awaiting_background`, `awaiting_attention`, and
`awaiting_response`. The fold uses only canonical session, turn, message,
reasoning, operation, attention, compaction, and background-execution facts.
`terminal.tab_state` maps those values to the existing grey, magenta, blue,
red, and green palette. The production terminal process paints only when the
folded state changes. It does not persist shown state in a tab database or ask a
plugin which color to use.

### 14.4 Terminal UI parity gate

Frozen current-output fixtures and the new presenter are compared during
implementation. The gate
requires:

- identical visible text, glyphs, colours, copy targets, and block order;
- identical open/progress/finish replacement behavior;
- identical wrapping at the tested widths and identical resize reflow;
- identical pane, tab, and scoreboard transitions;
- no extra/missing blank rules or lifecycle chrome;
- no measurable update-latency regression.

ANSI sequences that are semantically equivalent may be normalized for the
comparison, but rendered terminal snapshots must remain visually identical.

## 15. Dashboard presentation

The dashboard is rewritten under the hood to read semantic projections. Its
visible DOM, CSS, text, ordering, and interactions remain unchanged:

```text
canonical activity -> dashboard presenter -> dashboard item -> SSE -> browser
```

The dashboard presenter owns:

- dashboard-specific wording;
- HTML escaping and markup;
- CSS classes and icons;
- collapsed-run summaries and density modes;
- cards, bubbles, and dashboard copy affordances;
- responsive layout.

It does not import native transcript/rollout parsers and does not inspect ANSI,
RGB, glyphs, or terminal rendering flags to recover meaning.

Agent scope is a semantic query over `actor_id` and relationships. Parent
session summaries can deliberately include actor-assignment start/finish facts via a
named query rule; this is not a producer-set `web=True` escape hatch.

### 15.1 Dashboard item model

The server sends a small presentation model with complete names:

```python
@dataclass(frozen=True)
class DashboardItem:
    item_id: str
    item_type: Literal[
        "message", "reasoning", "operation", "file", "attention", "task",
        "compaction", "actor_assignment", "actor_message",
    ]
    summary_kind: Literal[
        "message", "shell", "background", "monitor", "file_read",
        "file_write", "file_edit", "search", "network", "skill", "task",
        "message_delivery", "other", "attention", "compaction", "actor_assignment",
        "actor_message",
    ]
    actor_id: ActorId
    state: Literal["running", "succeeded", "failed", "cancelled"] | None
    html: str
    plain_text: str
    content_reference: str | None
    conversation_kind: Literal[
        "prompt", "message", "system", "question", "answer", "plan",
        "plan_decision", "recap", "actor_message",
    ] | None = None
    turn_id: str | None = None
    final: bool = False
    actor_assignment_id: str | None = None
    actor_assignment_phase: Literal["started", "finished"] | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    message_id: str | None = None
    reply_to_message_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


class DashboardPresenter:
    def present(self, activity: Activity) -> DashboardItem: ...
```

`item_id` is stable across backlog and live delivery. One item is one complete
top-level dashboard node: a bubble, line, card, or collapsible block. The
presenter emits the entire node in `html`; the browser never assembles a card
from heading/body/footer fragments and never groups fragments by a second
identity. An operation's start, progress, and finish revise the same item.
`item_type` and `state` use dashboard-owned vocabularies. `html` is escaped,
trusted presenter output; `plain_text` is the exact copy value. Large expansion
content uses `content_reference`.

`summary_kind` is the presenter's complete, harness-neutral input to collapsed
view wording. It comes from operation category and execution mode, file action,
or the canonical activity type. Browser code never derives it from HTML,
glyphs, colours, native tool names, or harness names.

The remaining optional fields are the browser's explicit inputs for behavior
that exists today: conversation focus, message replies, final-answer and
actor-assignment ordering, file-change summaries, stable replacement, and elapsed
time. They prevent the browser from parsing CSS classes, HTML, glyphs, or
wording to reconstruct those meanings. They are dashboard presentation fields
only and never enter canonical storage or terminal models.

The browser stores and reconciles `DashboardItem` by `item_id`, then applies
the same view modes, expansion, newest-first insertion, and interactions as
today. No field is named with a single letter and no browser code reconstructs
meaning from HTML.

### 15.2 HTTP API

The rewritten browser data layer uses a small resource API:

| Route | Source | Purpose |
|---|---|---|
| `GET /api/sessions` | `SessionQueries` | session list snapshot |
| `POST /api/sessions` | selected `HarnessLauncher` | launch or resume a session |
| `GET /api/sessions/{session_id}` | focused query models | selected session snapshot |
| `GET /api/sessions/{session_id}/activity` | `activity_before` + dashboard presenter | initial and older activity pages |
| `GET /api/sessions/{session_id}/stream` | `activity_after` + focused projections | SSE updates |
| `POST /api/sessions/{session_id}/application/composer-draft` | `SessionApplicationService` | replace the current composer draft when its sequence is newest |
| `POST /api/sessions/{session_id}/application/composer-queue` | `SessionApplicationService` | replace the current queued-message markers |
| `POST /api/sessions/{session_id}/application/dialog-draft` | `SessionApplicationService` | replace answers for the named pending attention request |
| `POST /api/sessions/{session_id}/application/view-mode` | `SessionApplicationService` | set the dashboard density preference |
| `POST /api/sessions/{session_id}/application/notifications-muted` | `SessionApplicationService` | set the session notification preference |
| `POST /api/sessions/{session_id}/application/tasks-hidden` | `SessionApplicationService` | dismiss the current completed task set |
| `GET /api/stream` | application/global projections | session-list, account and notification SSE updates |
| `POST /api/application/notifications` | `GlobalApplicationService` | set the global notification preference |
| `POST /api/application/new-session-preferences` | `GlobalApplicationService` | replace remembered launch inputs |
| `POST /api/application/new-session-drafts` | `GlobalApplicationService` | replace the newest draft for one working directory |
| `POST /api/application/hidden-directories` | `GlobalApplicationService` | hide an inactive project group |
| `POST /api/application/push-subscriptions` | `GlobalApplicationService` | register this browser's Web Push subscription |
| `POST /api/application/presence` | `GlobalApplicationService` | report this browser's current device/session presence |
| `POST /api/application/browser-events` | `BrowserTelemetryService` | record typed browser-only operational events |
| `POST /api/sessions/{session_id}/application/optimistic-actions` | `BrowserTelemetryService` | record optimistic UI lifecycle evidence |
| `POST /api/sessions/{session_id}/application/client-failures` | `BrowserTelemetryService` | record failures observed by the browser |
| `GET /api/application/dictation` | dashboard dictation service | report whether browser dictation is configured |
| `POST /api/application/dictation-token` | dashboard dictation service | mint a short-lived browser dictation grant |
| `POST /api/application/uploads` | attachment service | stage a structured attachment |
| `POST /api/application/clipboard-files` | clipboard service | resolve pasted local file promises |
| `GET /api/application/push-configuration` | notification service | return public Web Push configuration |
| `GET /api/insights` | `ApplicationInsightsService` | typed cross-session activity, usage, liveness, project, and diagnostic aggregates |
| `GET /api/resumable-sessions` | `ResumableSessionService` | directory-scoped canonical sessions for the resume picker |
| `POST /api/sessions/{session_id}/controls` | `HarnessControlService` | one typed control request |
| `GET /api/harnesses` | registry metadata | launchable harnesses and capabilities |
| `GET /api/harnesses/{harness}/catalog` | selected `HarnessCatalog` | models, efforts, accounts, commands and rewind modes |
| `GET /api/content/{content_reference}` | `CanonicalContentService` | full copy/view content |

Requests use `before_cursor`, `after_cursor`, `actor_id`, and `block_count`.
There are no dual cursors, abbreviated session parameters, aliases, or
provider-specific query fields.

Harness discovery has one non-overlapping response model:

```python
@dataclass(frozen=True)
class HarnessDescriptor:
    name: str
    display_name: str
    launchable: bool
    default_for_launch: bool
    supports_attachments: bool
    control_names: tuple[ControlName, ...]
    catalog_sections: tuple[CatalogSection, ...]
    supports_terminal_input: bool
    supports_memory: bool
```

Exactly one launchable plugin declares `default_for_launch`; registry validation
rejects zero or multiple defaults when launchers exist. `launchable`,
`control_names`, `catalog_sections`, and
`supports_terminal_input` derive from the registered optional ports without
native I/O. `supports_memory` derives from the optional typed `HarnessMemory`
port; memory capture, storage, vault discovery, and document resolution stay in
the owning plugin while the application and dashboard consume only typed values.
There is no `has_catalog` or `supports_accounts`; the presence of
`"accounts"` in `catalog_sections` already answers that question. The browser
uses this descriptor only to show the controls that exist today.

The activity service is typed:

```python
@dataclass(frozen=True)
class DashboardActivityPage:
    oldest_cursor: int
    latest_cursor: int
    has_more: bool
    items: tuple[DashboardItem, ...]


class DashboardActivityService:
    def backlog(
        self,
        session_id: SessionId,
        before_cursor: int | None,
        scope: ActivityScope,
        block_count: int,
    ) -> DashboardActivityPage: ...
```

`DashboardSessionService` owns the session-list and selected-session snapshots.
It captures the store's current canonical cursor once and evaluates every
focused projection through that cursor, so one HTTP response cannot combine
state from different ingestion moments.

All focused folds in that evaluation share one decoded event page per session
and cursor. The dashboard also shares the resulting session-list snapshot among
HTTP and global SSE consumers for the existing 250 ms refresh interval. This is
a dashboard presentation cache, not a second semantic store: a new evaluation
still begins from the canonical cursor, and no plugin data enters the cache.

The global application snapshot pairs each `SessionSummary` with its
`TerminalSessionState`. The browser derives live/parked controls from
`terminal.window_id`, never from canonical lifecycle. `SessionFinished` remains
a semantic fact, but terminal presence is still direct application state.

`DashboardActivityService` calls `SessionQueries`, then `DashboardPresenter`. HTTP
handlers only parse requests and serialize results. They contain no canonical
event switch and never access a plugin directly. Control handlers build a
typed `ControlRequest` and pass it to `HarnessControlService`.

The canonical session snapshot includes session/actor summaries, usage,
context, attention, tasks, goal, and background-work counts. Application
badges and other mutable dashboard state live only in the separate application
snapshot. The launch and send bodies carry structured
attachments. Upload handling returns `AttachmentReference` values; only the
owning plugin converts them to native file mentions.

`ActivityStatistics` is one shared semantic projection containing shell-command
count, failed-shell-command count, unique-file count, added/removed lines,
actor-message count, and counts by native operation name. The terminal scoreboard
and dashboard scoreboard consume it; neither keeps semantic counters inside a
drawing model. Operation names remain facts reported by a harness. Each presenter
decides which counts fit and how their labels are styled.

Active time is another projection, not a mutable scorebar counter. It accumulates
the lead actor's active intervals from session start or a user prompt through
`turn.finished`, `turn.aborted`, or session finish. A scorebar restart therefore
reconstructs the same duration from canonical facts without a timing sidecar.

The dashboard attention projection uses complete question/option fields and
presenter-owned `plan_html`. Canonical `AttentionRequested` remains semantic and
contains no HTML; the browser never receives a Claude/Codex pending-dialog
record or reconstructs a plan card from native fields.

The application snapshot includes `TerminalSessionState(window_id,
input_state)`, read through `TerminalInputService`. Terminal liveness and the
native draft/suggestion are application state, not inferred from canonical
session lifecycle and not decoded by HTTP or browser code.

`content_reference` is a dashboard-owned reference derived from
`(event_id, payload_field)`. `CanonicalContentService` resolves it through the
canonical store and returns the original uncapped content. It is not a second
content database.

This table replaces only the harness/session semantic plane. Existing product
features remain application APIs rather than being forced through canonical
events:

| Application API family | Owner |
|---|---|
| uploads, clipboard files and dictation | dashboard application services |
| composer drafts, queued messages, view mode and preferences | dashboard state services |
| repository/worktree information and resumable-session search | application read services |
| presence, notifications, push subscriptions and mute state | notification services |
| errors, telemetry, extensions and operational details | operational audit/application services |

These handlers may be renamed with the rest of the browser data layer because
there is no compatibility requirement. They do not import plugins, append
canonical events, or duplicate canonical session queries.

### 15.3 Dashboard UI parity gate

Frozen current dashboard fixtures and new rendered sessions are compared. The gate
requires identical:

- item order, grouping, activity classification, labels, and HTML;
- cards, bubbles, badges, task/goal state, dialog state, and agent scope;
- collapsed/focus/default view behavior;
- history page boundaries and no duplicate/gapped items;
- copy/view results and control affordance availability;
- browser screenshots at representative desktop/mobile widths;
- SSE-visible latency within current tolerances.

## 16. SSE

SSE remains the dashboard's delivery transport. It sends projected dashboard
items and application state, not raw or canonical events.

### 16.1 One activity cursor

The canonical event cursor is the only activity position:

```text
GET /api/sessions/{session_id}/activity?before_cursor={cursor}
  -> {oldest_cursor, latest_cursor, items}

GET /api/sessions/{session_id}/stream?after_cursor={cursor}
  -> activity frames after that cursor
```

Old cursors are not accepted. After deployment the browser performs a fresh
activity request. There is no cursor translation.

The server advances the cursor over every examined canonical event, including
events filtered from the current agent scope. Otherwise a scoped connection
would repeatedly scan invisible events.

### 16.2 Canonical frame

```text
id: 1843
event: activity
data: {
  "cursor": 1843,
  "items": [...],
  "snapshot": {
    "cursor": 1843,
    "session": {...},
    "actors": [...],
    "usage": {...},
    "context": {...},
    "attention": {...},
    "tasks": [...],
    "goal": null,
    "background_work": {...}
  }
}
```

There is exactly one canonical `activity` frame for a committed cursor range.
`items` may be empty. Every frame carries one complete focused snapshot at the
same cursor. This deliberately avoids a second event-type-to-projection change
table in the transport and avoids using `null` ambiguously for both "unchanged"
and a legitimately cleared value. The straightforward full snapshot prevents
several frames from sharing one SSE ID and keeps browser application atomic.

The SSE `id` equals the canonical cursor and supports `Last-Event-ID`
reconnection. The explicit cursor remains in JSON and the activity API for
non-EventSource clients and diagnostics.

### 16.3 Backlog plus complete live snapshots

Keep the current good reconnect pattern:

1. fetch a compressed backlog/snapshot;
2. receive its `latest_cursor`;
3. open SSE after that cursor;
4. receive changed items plus a complete focused snapshot at each frame cursor;
5. use heartbeats and let `EventSource` reconnect with `Last-Event-ID` on
   transport loss.

SQLite polling after a cursor is sufficient. A broker or WebSocket is
not required.

### 16.4 Canonical-to-SSE mapping

Activity-producing families map to `items`. All focused state projections map
to the complete `snapshot` object:

| Canonical input | Frame key |
|---|---|
| messages, reasoning, operations, files, attention exchanges, task changes, compaction finishes, actor assignments, actor messages | `items` |
| usage reports | `snapshot.usage` |
| context and compaction reports | `snapshot.context` |
| actor lifecycle and metadata | `snapshot.actors` |
| attention requested or resolved | `snapshot.attention` |
| task changes | `snapshot.tasks` |
| session metadata, model, effort and lifecycle | `snapshot.session` |
| goal changes | `snapshot.goal` |
| background and monitor operations | `snapshot.background_work`, including their typed rows, progress and output |

Application-owned data uses one complete frame on each stream:

```text
event: application
data: { ...complete current application snapshot for this stream... }
```

The frame payloads are typed presentation/application models, not generic
dictionaries:

```python
@dataclass(frozen=True)
class SessionApplicationSnapshot:
    composer: ComposerState
    dialog: DialogState
    preferences: SessionPreferences
    terminal: TerminalSessionState
    errors: tuple[ApplicationError, ...]
    memory: MemoryStatus


@dataclass(frozen=True)
class GlobalApplicationSnapshot:
    sessions: tuple[DashboardSessionListItem, ...]
    usage_rows: tuple[UsageRow, ...]
    notifications: GlobalNotificationState
    preferences: GlobalPreferences
```

These nested values are owned by their named application services. They use
the fields the current UI actually consumes; this proposal does not add a
generic extension map, revision protocol, per-field event classes, or a second
cursor. If an existing application feature is not global, it belongs only in
`SessionApplicationSnapshot`.

The selected-session stream includes composer and dialog drafts, queued
messages, view preferences, terminal state, operational errors, and memory
status. Repository state and visual badges are dashboard projections, not
duplicate application state. Browser presence is write-side notification state
and has no UI snapshot. The global stream includes the current session list,
usage rows, global notification state, and launch preferences. `ready` remains
the connection-start frame.

An `application` frame has no SSE `id`; it cannot advance or reset the canonical
activity cursor. The server reads its values from named application services,
compares the complete serialized snapshot with the last one sent on that
connection, and emits only when it changed. This is deliberately polling over
current state. It needs no application event-name registry, broker, subscriber
queue, dropped-event policy, or replay log.

One canonical event may change several focused projections; their complete
values travel in the same frame. Idle connections receive no activity frames.

### 16.5 Non-domain state stays separate

SSE also carries dashboard/application state such as:

- composer and dialog drafts;
- queued messages;
- view preferences;
- notification state;
- terminal input synchronization;
- live tab/pane state.

These remain one application snapshot, separate from the canonical activity
snapshot. They must not be forced into the canonical harness model merely
because they share an SSE connection. The browser replaces its stored
application snapshot atomically and invokes the same existing render/update
functions, so visible behavior does not change.

### 16.6 SSE failure and reconnect rules

- Activity history is read through a fixed `latest_cursor`; live streaming
  starts after exactly that cursor, so an event cannot fall between the initial
  response and stream.
- `Last-Event-ID` wins over `after_cursor` whenever the header is present;
  `after_cursor` is read only on the first connection.
- Projectors advance the cursor across invisible/filtered events.
- Every activity in a canonical frame is projected only through that frame's
  cursor. A later finish cannot leak into an earlier running-operation frame;
  the later frame updates the same stable dashboard item.
- Re-sending a frame after disconnect is safe because dashboard item IDs derive
  from canonical subject IDs and browser reconciliation is idempotent.
- Heartbeat comments and the existing polling cadence remain unless measured
  behavior shows that they need to change.
- Raw canonical envelopes are never sent over SSE.

## 17. Control plane

Canonical activity and control capabilities are separate concerns.

The dashboard sends semantic commands through the owning plugin's typed
capability:

```python
controls.execute(
    SendText(
        session_id=session_id,
        request_id=request_id,
        text=text,
    )
)
controls.execute(
    Interrupt(session_id=session_id, request_id=request_id)
)
controls.execute(
    SelectModel(
        session_id=session_id,
        request_id=request_id,
        model_id=model_id,
    )
)
```

The plugin translates the command into native TUI actions, app-server calls,
or another transport. The result uses a small shared verdict vocabulary such
as acknowledged/rejected/indeterminate and retains correlation IDs for audit.

Control outcomes may arrive as raw harness observations and canonical
events. The control call must not fabricate a successful domain fact merely
because keystrokes were sent.

## 18. Strict restrictions

These are architectural requirements, not suggestions.

### Domain restrictions

- Canonical events **must not** contain glyphs, RGB, ANSI, HTML, CSS, terminal
  width, gutters, wrapping, truncation, or surface-specific prose.
- Canonical events **must not** contain `web`, `note`, `bubbled`, `chrome`,
  `lk`, or equivalent routing/presentation flags.
- Canonical text/results **must not** be presentation-capped.
- Canonical events **must** carry stable semantic identities, and storage must
  link every event to its raw provenance.
- A canonical event must retain the `session_id`, `actor_id`,
  `parent_actor_id`, and `harness` of its `RawEvent`. A translator cannot move
  evidence between actors. The first accepted raw observation establishes that
  actor's plugin ownership even when the observation is intentionally ignored
  or translation fails.
- Missing native relationships **must** remain absent; translators must not infer
  them from timing merely to improve layout.

### Plugin restrictions

- Every Claude Code-dependent implementation **must** live under
  `plugins/claude_code/`; every Codex-dependent implementation **must** live
  under `plugins/codex/`.
- A harness-specific implementation **must not** be moved to a shared package
  merely because the shared package currently has only one caller for it.
  Define or extend a harness-neutral contract and keep the implementation in
  the plugin.
- A plugin **must not** import another plugin.
- Shared runtime, presenters, and dashboard code **must not** import a concrete
  plugin package.
- `plugins/__init__.py` and each harness package `__init__.py` **must** remain
  inert package markers. A harness exposes exactly one shared entry:
  `plugin.py` containing its `HarnessPlugin` descriptor.
- Native payload/file grammars **must** have exactly one owner in their plugin.
- A recognized session has exactly one lead plugin, which owns launch, control,
  catalog, and terminal probing. An explicitly related child actor may be
  observed by another plugin; its raw events and canonical facts remain owned
  by that actor's plugin. No plugin may claim an actor through working-directory
  or timing guesses.
- A plugin translator **must** be deterministic and side-effect free.
- Harness-specific accounts, model menus, effort values, commands, and screen
  parsing **must** stay in the plugin.
- Harness-specific source discovery, ownership detection, lifecycle, liveness,
  parsing, normalization, deduplication, and recovery policy **must** stay in
  the plugin.
- Shared code **must not** use a harness name, native event/tool name, native
  path, executable, model-ID pattern, or environment variable to select
  behavior.
- Unsupported capabilities **must** be absent and reported directly, never
  borrowed from another harness.

### Consumer restrictions

- This refactor **must not** intentionally change terminal or dashboard UI,
  wording, layout, interaction, ordering, timing, or feature availability.
- No consumer may read terminal rendering records to learn semantics.
- No presenter may parse another presenter's strings, colours, or glyphs.
- Shared projectors **must not** branch on harness identity.
- SSE/browser code **must not** interpret native or canonical lifecycle rules.
- Operational diagnostic tables **must not** become runtime state dependencies.
- Terminal presenters, scoreboard presenters, dashboard queries, HTTP handlers,
  and SSE handlers **must not** discover sessions, drain event sources, translate
  raw observations, or advance source checkpoints.
- Exactly one application-owned `ObservationRunner` schedules file and poll
  sources for a database. Synchronous hook processes may deliver their own hook
  observation but must not run the observer.

### Naming and replacement restrictions

- New names use complete domain words. Do not abbreviate `operation` as `op`
  or `ops`, `session_id` as `sid`, `sequence` as `seq`, or use one-letter field
  names such as `g`, `t`, and `d`.
- Names describe facts or responsibilities: `RawEvent`, `CanonicalEvent`,
  `DashboardItem`, `TerminalUpdate`, `HarnessEvents`, and `SessionQueries`.
- The target API has no old parameter aliases, old SSE event names, old browser
  item shapes, harness-named shared shims, or old-history reader.
- Browser rendering functions are changed in place to consume `DashboardItem`.
  A function that converts `DashboardItem` into the old item shape is a
  compatibility adapter and is forbidden, even if it is private or temporary
  in the final code.
- Compatibility behavior is not hidden behind configuration flags. Frozen
  fixtures and screenshots are the comparison oracle; production never runs
  both implementations.

### Storage and audit restrictions

- Raw evidence, translation, canonical facts, and provenance must commit in one
  transaction.
- Every canonical event must have at least one stored raw-event provenance
  link. Application-only state does not become a canonical event.
- Every raw record must receive an audited translation decision.
- Event and source identities must be replay-stable.
- A projector failure must not erase accepted canonical facts.
- Event evidence tables must participate in CLI inspection and schema-contract
  tests and follow the session's lifecycle.

## 19. Enforcement tests

The architecture should be guarded mechanically.

### Import graph tests

- `domain` imports only stdlib.
- `contracts` imports only stdlib/domain.
- `runtime` imports no plugin or presenter.
- presenters/dashboard import no concrete plugin.
- plugins never import siblings.
- no shared module, including the composition root, names a concrete harness;
  folder discovery imports each descriptor by convention.
- an AST/import-and-literal boundary test rejects Claude/Codex native symbols
  outside their plugin, with an allowlist only for registration metadata and
  audit fixtures;
- the allowlist is bidirectional: a listed exception that no longer exists
  fails as stale.
- the final repository contains no imports or calls to the old shared drawing
  model, provider fan-out, transcript/dashboard merge, or old session-semantic
  HTTP handlers; this is checked across all production packages, not only the
  newly added canonical packages.
- only `ObservationRunner`, hook delivery, and plugin source implementations
  may reference source draining or checkpoint commits; terminal, scoreboard,
  dashboard, HTTP, and SSE packages fail the import/AST boundary test if they do.
- no process-local dashboard broker/event-stream class remains; SSE reads named
  application snapshots directly.

### Schema tests

- serialize every canonical event type and round-trip it;
- reject unknown required fields and invalid schema versions;
- reject forbidden presentation fields in canonical payload schemas;
- reject native source locators such as transcript or rollout paths in
  canonical payload schemas;
- require `event_id`, semantic subject IDs, and stored raw-event provenance;
- enforce unique canonical `event_id` and raw-event audit keys.

### Plugin translation tests

Every plugin supplies fixtures covering:

- supported native records -> expected canonical events;
- unknown records -> explicit ignored decision;
- malformed input -> audited translation error and no invented event;
- repeated input -> identical event IDs;
- start/finish correlation;
- parent/child identity;
- missing optional relationships without guesses.

The same cross-harness scenario suite is run against Claude and Codex fixtures.
It is the contract suite a future harness must pass without changing its
expectations:

```text
message
successful shell operation
failed shell operation
file read/edit/write
actor assignment start/finish
usage update
attention request
turn abort
```

### Projector tests

- the same captured session produces the exact existing visible terminal and
  dashboard output, not merely equivalent semantic content;
- presenter output contains no harness-name conditional behavior;
- terminal resizing/reflow remains byte- or snapshot-pinned;
- dashboard backlog and live SSE produce the same ordered items;
- reconnect after any cursor has no gaps or duplicates;
- agent scope advances its cursor across filtered events.
- screenshot/golden tests pin dashboard desktop/mobile appearance and terminal
  output at representative widths;
- browser event names and payloads use the new documented contract only;
- latency benchmarks reject perceptible regressions in first render, live
  activity, tab transitions, and control feedback.

### Audit tests

- raw and canonical rows correlate by `raw_event_id`;
- one raw row can correlate to zero, one, or many canonical rows;
- a database failure leaves the source checkpoint unchanged;
- replay does not duplicate audit or canonical rows;
- replaying an ID with different raw bytes fails;
- the CLI can show raw -> canonical -> decision for a session;
- deleting a session removes its raw, translation, canonical, and provenance
  rows together.

## 20. Implementation and deployment

Implementation replaces each internal boundary directly. There is one finished
runtime path and one deployment; canonical data is never written beside the old
representation.

### Step 0: inventory and invariants

- Freeze representative Claude and Codex raw fixtures.
- Freeze current terminal output and dashboard item/SSE behavior for those
  fixtures.
- Capture terminal goldens and dashboard screenshots at representative widths;
  these are immutable UI parity baselines for the refactor.
- Inventory every existing mixed rendering field and map it to a canonical
  fact, terminal-only presentation, dashboard-only presentation, or deletion.
- Inventory raw sources: hooks, main transcripts, child transcripts, Codex
  rollouts, and sidecars.

### Step 1: foundation

- Add domain IDs, envelopes, initial event payloads, and serialization.
- Add the event storage tables and audit CLI support.
- Add the registry and move harness-specific code behind it immediately.
- Add import, schema, and plugin contract tests.

### Step 2: first vertical slice

Implement both Claude and Codex mappings for:

```text
session.started
message.created
operation.started
operation.finished
session.finished
```

The important proof is cross-harness: no initial canonical event type is accepted
based on Claude alone.

The slice is exercised from frozen raw fixtures. It does not write alongside
the current production path.

Connect one application-owned `ObservationRunner` at this step. The server owns
its repetition and shutdown. Terminal, scoreboard, dashboard, and SSE tests
must fail if those consumers call `HarnessEvents.sources`,
`HarnessEventSource.drain`, or `CheckpointStore.commit`.

### Step 3: terminal presenter

- Implement canonical activity to `TerminalUpdate` presentation.
- Reuse the proven wrapping, ANSI, reflow, and click behavior behind the new
  `TerminalRenderer` interface.
- Compare final rendered output with captured baselines.
- Do not ship a canonical-to-old-record translator.

### Step 4: dashboard and SSE

- Implement canonical activity to `DashboardItem` presentation.
- Rewrite the browser data layer to consume the new HTTP and SSE contracts
  while preserving the exact rendered UI.
- Use one canonical cursor for history and live delivery.
- Send one complete application snapshot only when it changes; delete the old
  notification broker and do not add a replacement event bus.
- Make `Last-Event-ID` authoritative on automatic reconnect.
- Delete timestamp merging, direct native parser imports, old cursor fields,
  and the old stream event names.

### Step 5: remaining semantics

Implement file changes, actor assignments, attention/dialogs, usage, compaction,
reasoning, and progress. For each family:

1. map raw fixtures for both existing harnesses;
2. persist/audit canonical events;
3. add terminal and dashboard projections;
4. compare visible behavior with the captured baseline;
5. remove the corresponding dependency on mixed rendering records.

Implement each harness's existing process evidence in its own plugin during
this step. In particular, Codex process detection and its command wrapper stay
under `plugins/codex/` and emit raw process-start/process-finish evidence that
translates to `SessionStarted` and `SessionFinished`. They do not call old
session/watch modules. `ApplicationEventDelivery` invokes the same generic
lifecycle service used for hook- and rollout-produced session facts.

### Step 6: delete the old shared representation

- Delete the old mixed rendering model and its storage table.
- Keep only terminal-specific `TerminalUpdate` classes in the terminal package.
- Ensure dashboard and read APIs cannot import terminal presentation code.
- Delete old stored presentation history. Existing native Claude transcripts
  and Codex rollouts are read by the normal plugin sources after restart; no
  special old-schema converter or old-history reader exists.

### Step 7: freeze the future-harness contract

OpenCode implementation is explicitly out of scope for this proposal. The
current work ends by freezing and documenting the abstraction set a future
harness will implement:

- `HarnessInfo` and `HarnessPlugin`;
- `SessionRecognizer` and `RecognizedSession`;
- `HarnessEvents` and `HarnessEventSource`;
- `HarnessController`, `HarnessLauncher`, `HarnessCatalog`, and
  `HarnessTerminalProbe`;
- canonical schemas, raw/canonical audit correlation, and projector contracts;
- the cross-harness conformance fixture suite.

The contract is ready only when a synthetic third-harness test plugin can implement
the ports, emit every shared scenario, render on both surfaces, stream through
SSE, and write raw/canonical audit rows without a production-code branch or a
new provider function. This synthetic plugin tests extensibility without
bringing OpenCode implementation into scope.

### Step 8: direct downtime replacement

Deployment is deliberately plain:

1. stop Baqylau processes;
2. install the new code and browser assets;
3. delete the old derived presentation and event databases;
4. start Baqylau;
5. let the normal Claude and Codex event sources ingest available native
   transcripts and rollouts into the new schema.

Facts absent from native sources are not reconstructed. The deployment has no
backup, rollback, coordinated protocol window, converter, runtime switch, or
compatibility reader. Downtime is accepted.

Stopping processes and deleting the obsolete derived databases are operator
deployment actions. The application contains no migration runner, backup
command, rollback command, cutover coordinator, schema converter, or feature
flag for the old path. Startup accepts the one current schema or fails clearly.

## 21. Definition of done

The architecture is complete when all of the following are true:

1. Claude Code and Codex produce the same canonical event types for the same
   semantic activity.
2. Runtime canonical events, not terminal rendering records or native
   transcripts, are the authoritative cross-consumer representation.
3. The terminal and dashboard render harness activity exclusively from
   canonical semantic inputs/read models; application state remains in its
   named application services.
4. The dashboard has one activity cursor and no transcript/render-record merge.
5. Audit can show exact raw evidence and every canonical interpretation linked
   to it.
6. No shared module imports a concrete harness implementation.
7. No presenter parses another presenter's output to recover harness meaning;
   the explicit plugin-owned terminal input probe is the only live-screen read.
8. Terminal updates contain presentation only and are private to the terminal.
9. Adding a harness requires a plugin plus registration/packaging metadata,
   not edits to shared runtime or presenters.
10. Existing terminal behavior, dashboard behavior, transactional audit, and
    control safety remain covered by equivalence and contract tests.
11. All Claude Code- and Codex-specific behavior is contained in its respective
    plugin, with no shared harness-named shims.
12. A synthetic third-harness plugin passes the public conformance suite
    without modifying production domain, runtime, presenter, dashboard, or SSE
    code.
13. Terminal and dashboard UI parity suites show no intentional visible or
    interaction change from the frozen v1 baselines.

The result is not merely a cleaner directory layout. It changes Baqylau's
fundamental contract from “all consumers understand terminal rendering” to:

> Harness plugins report facts. Runtime preserves facts. Each surface presents
> facts in its own language.
