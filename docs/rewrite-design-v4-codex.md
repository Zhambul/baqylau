# baqylau v4 — Transactional Conversation Architecture

Status: **IMPLEMENTATION-READY DESIGN — VALIDATED SPECIFICATION**

Date: **2026-08-05**

This document is the complete implementation specification for the baqylau
rewrite. It defines the system's domain vocabulary, ownership
rules, runtime coordination, persistence, ingestion, streaming, effects,
queries, extension boundaries, recovery model, migration strategy, and
acceptance gates. The independent coverage, API/model, and schema/storage
reviews are closed by Sections 38.35–38.39; generated code artifacts and
measured provider fixtures remain implementation outputs, not open design
choices.

The existing implementation remains authoritative for user-visible behavior
until a migration gate explicitly transfers ownership of that behavior. This
document is authoritative for the structure of the replacement.

---

## 0. Executive decision

baqylau will be a **transactional modular monolith** consisting of:

- one supervised Python daemon;
- provider-, terminal-, backend-, and surface-independent domain modules;
- a small relational core centered on a provider-neutral Conversation;
- serialized mutation coordinators keyed by Conversation;
- named machine-, account-, and terminal-window-scope services;
- SQLite WAL as the initial metadata database;
- a durable Observation inbox and transactional outbox;
- framed staging files and content-addressed blobs for high-volume bytes;
- server-owned activity composition and CQRS-style projections where useful;
- capability-declared provider and terminal adapters; and
- bounded evidence, provenance, and named repair facilities.

The following product decisions are final for v4:

- the supervised daemon is the required availability boundary; when it is
  unavailable, every Baqylau feature may be unavailable while provider-native
  behavior continues through edge pass-through;
- the complete current and future product remains in detailed design scope;
- metadata remains in one machine-wide SQLite database; v4 does not introduce
  per-Conversation databases or a partition trigger; and
- the coordinator, durable inbox, provenance, framed staging, materialized
  activity, cursor replay, transactional outbox, restart-safe temporary state,
  draft CAS, capability interfaces, and content-addressed blob design are
  retained. Their costs are accepted and their contracts must be completed,
  not simplified away.

The core model has five concepts:

1. **Conversation** — provider-independent continuity and the active semantic
   head.
2. **Node** — one semantic message in the Conversation tree.
3. **AgentSession** — one provider-native incarnation attached to a
   Conversation.
4. **Operation** — structured work, interaction, delivery, or control with a
   lifecycle.
5. **Stream** — incremental content owned by a Node or Operation.

The central distinctions are:

> **Nodes describe conversation content. Operations describe work.**

> **A Conversation is not a provider session, process, account, terminal, or
> database partition.**

> **Dialogue ancestry, work containment, causal contribution, and runtime
> lineage are different relationships.**

The daemon uses Conversation coordinators as an execution mechanism, not as a
second domain model. A coordinator serializes mutations for one Conversation,
caches durable open facts, and schedules Conversation-local probes. It never
owns facts that exist only in memory.

The system does **not** use event sourcing. Raw Observations are boundary
evidence, not domain events. The structural change feed is a bounded delivery
mechanism, not truth. There is no global domain sequence and no requirement to
replay all history.

### 0.1 Full-scope decision

This specification covers the complete intended product, not only the first
implementation phase. Every current behavior, current provider integration,
and future feature described by v4 remains in design scope. This includes:

- Claude Code, Codex, OpenCode, and future provider integrations;
- interactive, headless, SDK, server, local, and remote execution;
- terminal panes, web, CLI, phone, MCP, and future surfaces;
- live assistant content, command/tool output, interactions, controls, drafts,
  preferences, attention, alerts, Resources, usage, and diagnostics;
- close, resume, fork, rewind, account migration, and cross-provider handover;
- collaboration, extensions, untrusted out-of-process plugins, and their
  security boundaries;
- crash recovery, backups, repairs, retention, remote uncertainty, and database
  evolution; and
- every capability currently described as future, deferred, optional, or
  triggered by later scale.

Implementation may proceed in phases. Phasing changes when a feature is built;
it does not permit that feature's data, API, event, storage, service, security,
or failure contract to remain unspecified. Later implementation phases must not
force implementors to invent missing architecture.

### 0.2 Implementation-completeness gates

V4 is complete only when an implementor can write the code without making a
product, data, storage, protocol, ownership, transaction, retry, or failure-
handling decision. Private helper names and equivalent local algorithms remain
implementation choices only when they cannot change observable behavior.

#### Models and state machines

- Every model and value object has an exact name, owner, fields, types,
  nullability, defaults, identity, mutability, validation, and serialization.
- Every relationship has exact direction, cardinality, foreign-key behavior,
  deletion behavior, and cross-scope rules.
- Every state machine lists every state and allowed transition, the evidence or
  command that permits it, the atomic writes it causes, and its invalid-
  transition result.
- Requested, attempted, effective, observed, unknown, failed, lost, and
  unavailable values are never collapsed into one optimistic value.
- Provider/model/effort/runtime choices are present on every start, resume,
  fork, handover, and migration contract, including requested-versus-effective
  results.

#### HTTP API

- Every endpoint has one exact method and path, authorization rule, path/query/
  header inputs, body schema, response schema, status codes, error codes,
  idempotency behavior, revision checks, pagination, ordering, and size limits.
- Catch-all payloads such as `/api/v1/global` are forbidden. Configuration,
  health, accounts, usage, notifications, presence, and statistics have
  separately owned endpoints.
- Core semantic controls have exact contracts. No required control is hidden
  behind an unspecified generic action payload.
- One authoritative OpenAPI 3.1 document is generated from or checked against
  this section. Examples never replace schemas.

#### Live updates

- Every SSE endpoint and event has an exact name, payload, event ID, scope,
  authorization rule, ordering rule, and retention rule.
- Initial snapshot synchronization, connection establishment, reconnect,
  cursor expiry, missed events, slow clients, overflow, daemon restart, and
  client resnapshot behavior are exact.
- Structural delivery cursors and per-Stream revisions are either related by an
  exact rule or explicitly independent.
- The frontend algorithm for applying, ignoring, deduplicating, correcting,
  and resynchronizing every event is part of the contract.

#### SQLite and file storage

- The document contains one executable SQLite DDL schema for every table,
  column, type, default, primary key, foreign key, uniqueness rule, check,
  index, trigger, and schema-version record.
- DDL defines deletion and retention behavior; no relationship relies on an
  unwritten application convention.
- Every migration has exact forward behavior, compatibility behavior, rollback
  behavior, and a declared point after which rollback is unsafe.
- Blob, upload, evidence, backup, and live-output storage have exact paths,
  formats, atomic write rules, permissions, size limits, cleanup, retention,
  corruption handling, and SQLite references.

#### Storage access

- Every application storage method has an exact Python signature, input and
  return types, named error results, and missing-data behavior.
- Every method names the tables and indexes it uses, its ordering and maximum
  result size, whether it opens or joins a transaction, and its concurrency or
  locking rule.
- Every write use case lists its complete atomic transaction. All listed rows
  change together or none change.
- Every query defines its SQL access pattern, stable pagination key, snapshot
  consistency, and behavior when referenced blobs or evidence have expired.
- Application code never constructs adapter-specific SQL outside the SQLite
  adapter.

#### Services, adapters, and runtime tasks

- Every application and machine service has one owner, exact public methods,
  inputs, results, dependencies, stored state, startup order, shutdown order,
  and failure boundary.
- Every background task states what starts it, how it is supervised, what wakes
  it, its bounded work unit, how it stops, whether work survives restart, and
  what happens after repeated failure.
- Every provider, terminal, storage, alert, credential, backend, delivery, and
  extension adapter method has an exact contract and capability rule.
- Composition states exactly which implementation is constructed and connected
  for every supported configuration. Registration alone is not described as a
  network connection or provider launch.

#### End-to-end traceability

- Complete flows cover daemon start and recovery; frontend HTTP snapshot and
  SSE connection; Conversation creation; provider discovery/start; model and
  effort selection; message delivery; streaming; commands/tools; interactions;
  close; reconnect; resume; archive; failure; and every future workflow.
- Every flow step names the endpoint or inbound Observation, event, application
  method, storage method, transaction, affected rows/files, and frontend-visible
  result.
- A traceability matrix maps every endpoint and event to its service method,
  storage method, tables/indexes, state transition, authorization rule, and
  required tests.
- Required behavior never uses “representative,” “possible,” “suggested,”
  “optional later,” or “implementation-defined” in place of a decision.

The gates above are met by the normative closure sections and their executable
checks. A change that makes one of those checks fail reopens the design and may
not be delegated to an implementor as an incidental coding choice.

### 0.3 Completion record and required implementation inputs

The live-system review and three independent Sol/high audits produced the
following closure record:

1. Sections 38.35, 38.39, and 40.7 define the five-unit, dependency-ordered,
   executable clean-install schema, migrations, deletion law, future workflow
   tables, integrity fixtures, and canonical digest.
2. Sections 38.36 and 38.38 define the closed field-level schema registry,
   authentication model, 115-endpoint manifest, 36-event manifest, error and
   pagination rules, and endpoint/event traceability. `api/openapi-v1.yaml` is
   generated mechanically from those sections and must pass exact set/schema
   equality; it is not a second design authority.
3. Sections 38.25 and 38.37 define storage signatures, units of work, access
   patterns, transaction contents, services, adapters, and workflow ports.
4. Section 38.37 defines Claude Code, Codex, and OpenCode identity, resume,
   fork, branch, ordering, deletion, and fixture contracts. An adapter version
   is unsupported until its exact measured build and byte fixtures are checked
   in; the implementor may not guess or silently broaden support.
5. Sections 38.36 and 38.38 define Unix-owner, browser, CSRF, bearer, mTLS,
   invitation, authorization-revision, and revocation behavior.

The generated OpenAPI file, provider fixture bytes, packaged SQLite-version
matrix, and code/catalogue cross-reference results are required implementation
artifacts. Their acceptance rules are fixed here; producing measured bytes or
generated files does not require a product or architecture decision.

---

## 1. Goals, constraints, and non-goals

### 1.1 Goals

The architecture must provide:

1. A stable provider-neutral core for Claude Code, Codex, OpenCode, and future
   runtimes.
2. A direct representation of branching semantic conversation history.
3. A separate representation of commands, tools, agents, monitors,
   interactions, controls, and other work.
4. Terminal independence, with no-terminal execution as a first-class mode.
5. Surface independence across terminal panes, web, CLI, phone, and MCP.
6. Live assistant streaming and operational output without putting bulk bytes
   on the metadata path.
7. Durable ingestion, crash recovery, explicit uncertainty, and safe
   pass-through when baqylau is unavailable.
8. Configurable providers, backends, accounts, and execution modes.
9. A credible cross-provider handover mechanism.
10. Stable identity for nested runtimes, child work, and future collaboration.
11. Extension seams that do not require provider-name branches in the core or
    surfaces.
12. Per-Conversation failure isolation and bounded latency interference under
    concurrent load.
13. An audit trail that explains what arrived, which rule interpreted it, and
    which external effect was attempted.
14. A migration path that preserves the measured behavior of the existing
    terminal cockpit and dashboard.

### 1.2 Constraints

The design accepts these facts:

- Hook processes must not block or fail the provider tool.
- The daemon can crash or restart during active work.
- Some providers omit cancellation, death, denial, or completion events.
- Provider payloads and file formats are undocumented or version-fragile.
- Inputs can arrive late, duplicated, and out of order.
- Some controls are exposed only through a TUI and screen driving.
- Provider-native sessions are not mutually compatible.
- Interactive TUIs may expose only approximate screen snapshots, not semantic
  token deltas.
- Command output is qualitatively different from relational metadata.
- SQLite serializes writers even in WAL mode.
- Some important facts are machine-, account-, device-, or terminal-window-
  scoped rather than Conversation-scoped.
- Some session configuration exists only in the provider process environment.
- An external action can succeed even when its receipt is lost.
- Not all wrong canonical mappings can be repaired from retained evidence
  forever.

Capabilities and uncertainty must expose these constraints rather than hide
them behind universal interfaces or optimistic booleans.

### 1.3 Non-goals

The initial rewrite will not:

- deploy as microservices;
- require Kafka, Redis, Celery, or PostgreSQL;
- use a globally ordered event stream as the system of record;
- guarantee full replay from the beginning of time;
- preserve provider-private reasoning or hidden model state;
- manufacture a native Claude session from a Codex transcript or vice versa;
- transfer credentials, approvals, PIDs, terminal state, or running processes
  during handover;
- make arbitrary in-process third-party plugins safe;
- retain unlimited output forever;
- expose a universal event bus or arbitrary graph database as the plugin API;
- model every possible future concept as a core entity; or
- copy existing paint operations, CSS classes, ANSI, glyphs, or terminal
  layout into the domain model.

---

## 2. Why the persistence model is not event sourced

Event sourcing can represent a tree, but it does not match the ownership,
topology, volume, or repair properties of baqylau.

### 2.1 Provider-native history already exists

Claude transcripts, Codex rollouts or threads, OpenCode records, and process
state already contain provider-native truth. A second immutable interpretation
log would create two representations that can disagree and would permanently
promote mapper decisions into truth.

baqylau instead stores:

- the raw Observation received at its boundary;
- a provider-neutral canonical interpretation;
- provenance linking the interpretation to evidence and rule version; and
- explicit correction history when the interpretation is repaired.

Provider-native history and baqylau canonical history are different facts with
different owners. There is no claim that one is a lossless copy of the other.

### 2.2 Global order is not a domain invariant

Meaningful orders are local:

- Node ancestry within one Conversation;
- provider source position within one AgentSession;
- lifecycle order within one Operation;
- revision and byte order within one Stream;
- workflow step order within one saga; and
- attempt order within one external effect.

A total order across unrelated Conversations is not a business fact. The
delivery layer may use an implementation cursor for bounded notification
catch-up, but no domain rule may infer causality or truth from it.

### 2.3 Data has several natural shapes

The system contains:

- immutable committed semantic messages arranged as a tree;
- mutable operation and workflow lifecycles;
- high-volume appendable bytes;
- current configuration and preferences;
- retryable external effects;
- diagnostic evidence;
- provider-native records; and
- disposable query projections.

One append-only abstraction makes several of these unnatural. Each receives a
storage shape that matches its behavior while preserving atomic transactions
for facts that must change together.

### 2.4 Recovery and correction

The architecture supports:

- idempotent ingestion;
- retry of unprocessed or quarantined Observations;
- targeted remapping within retained evidence;
- per-Conversation projection rebuilds;
- provider-source reimport where the artifact exists;
- ordinary schema and data migrations; and
- named repair commands with before/after provenance.

It does not promise arbitrary whole-history replay. Open facts are durable
rows, so crash recovery does not need replay. Projections declare their rebuild
scope. Canonical mistakes are repaired explicitly rather than by silently
rerunning every historical mapper.

---

## 3. Architectural style and dependency direction

### 3.1 Transactional modular monolith

The daemon is one deployable process. Modules have explicit ownership and
public interfaces, but synchronous in-process calls are allowed when a
transaction or invariant requires them.

This avoids network protocols between internal features, distributed
transactions, process-per-feature supervision, and operational dependencies
that the product does not need.

### 3.2 Hexagonal boundaries

Source-code dependencies point toward application and domain abstractions:

```text
surfaces and edge transports --> application public interfaces

application use cases and coordinators --> domain model and pure rules

application use cases and coordinators --> outbound semantic ports
                                             ^
                                             |
                                provider · terminal · storage
                                backend · delivery adapters
```

Inbound adapters translate external requests and observations into application
use cases. Application code invokes pure domain rules and, when it needs an
external capability, depends on an outbound semantic port expressed in baqylau
terms. Provider, terminal, storage, backend, and delivery adapters implement
those ports and therefore depend on the port contract; the port does not depend
on its adapters. One adapter may have both inbound and outbound roles.

This diagram describes source-code dependencies, not a claim that runtime data
only moves in one direction. The domain model and pure rules import neither
ports nor adapters. The core imports no Claude, Codex, OpenCode, kitty, HTTP,
tunnel, or concrete database implementation. The composition root selects and
connects concrete adapters at startup.

### 3.3 Functional core, imperative shell

Prefer pure functions for:

- provider-record classification;
- live-branch selection;
- Operation transition validation;
- attention derivation;
- title and facet precedence;
- usage and price arithmetic;
- activity placement;
- capability validation; and
- handover compilation.

Use imperative services for:

- transactions;
- coordinator mailboxes;
- supervision;
- file and process watching;
- timers and probes;
- terminal and provider control;
- stream staging;
- network delivery; and
- SDK, app-server, or backend calls.

### 3.4 One authoritative owner per fact

The rule is not one table or component for everything. The rule is that every
fact has exactly one authoritative owner.

| Fact | Owner |
|---|---|
| Provider-native transcript record | Provider artifact plus retained Observation/index |
| Logical active head | Conversation |
| Committed semantic message | Node plus sealed content |
| Provider-native session identity | AgentSession and aliases |
| Command/tool/subagent lifecycle | Operation |
| Live assistant or command bytes | Stream |
| Current attention | Attention projection |
| Account quota state | Account service |
| Terminal window binding/state | Window registry |
| External action request | Outbox/control Operation |
| External attempt and receipt | Effect-attempt infrastructure |
| Composer draft | InputBuffer |
| Stable concurrent-item slot | Slot allocation supporting state |
| Rendered HTML, ANSI, color, and layout | Surface |

No module writes another module's tables directly. Cross-module mutations use
application services and one transaction boundary.

---

## 4. System overview

```text
                                 INBOUND

 hooks · transcript/rollout watchers · SDK/app-server streams · OTLP
 status line · process/screen probes · web/MCP commands · effect receipts
                                      |
                                      v
                     edge and provider/backend adapters
                                      |
                                      v
                         durable Observation inbox
                                      |
                                      v
                            identity resolution
                                      |
                     +----------------+----------------+
                     |                                 |
                     v                                 v
          Conversation coordinator          machine-scope services
          provider mapper + rules       account · window · alerts · discovery
                     |                                 |
                     +----------------+----------------+
                                      |
                                      v
                       short atomic storage transaction
                                      |
          +---------------------------+---------------------------+
          |                           |                           |
          v                           v                           v
 Conversation/Node/          evidence/provenance         transactional outbox
 AgentSession/Operation/     and repair records                    |
 Stream metadata                     |                             v
          |                           |                     effects and ports
          +---------------+-----------+                             |
                          v                                         v
              queries and ActivityComposer                 receipt Observation
                          |
             +------------+-------------+
             |                          |
             v                          v
      structural state feed       per-Stream live feed
             |                          |
             +------------+-------------+
                          v
              web · pane · CLI · MCP · phone
```

There are two client delivery planes:

1. **Structural plane** — durable entity changes, projection revisions,
   workflows, activity amendments, and effect outcomes.
2. **Stream plane** — coalesced live text/output addressed by Stream ID,
   revision, and range.

Bulk data never forces latency-sensitive state through a per-chunk relational
or structural-feed path.

Edges do not persist their own delivery queues. If the daemon is unavailable,
synchronous hooks pass through unchanged, callers that support errors receive
an unavailable result, and best-effort observations may be lost.

---

## 5. Core vocabulary

| Term | Meaning |
|---|---|
| **Conversation** | Provider-independent logical continuity shown as one body of work |
| **Node** | One semantic message in the Conversation tree |
| **AgentSession** | One provider-native conversation/runtime incarnation |
| **Operation** | Structured work, interaction, delivery, or control lifecycle |
| **Stream** | Incremental content owned by a Node or Operation |
| **Observation** | Raw input received from an external boundary |
| **Provenance** | The rule/version/decision linking evidence to canonical state |
| **Projection** | Derived query state with a declared owner and rebuild scope |
| **Resource** | Stable handle for a file, image, diff, plan, log, or other artifact |
| **ExecutionTarget** | Provider + backend + optional account + execution mode |
| **Surface** | Web, terminal pane, CLI, phone, MCP, or another consumer |
| **Coordinator** | Runtime serializer/cache for mutations to one Conversation |
| **Machine service** | Named owner of cross-Conversation machine/account/window facts |

The user interface may continue to display the word “session.” Internally, a
list card represents a Conversation and its currently relevant AgentSessions.

---

## 6. Scope and runtime ownership

### 6.1 Conversation coordinator

Each active Conversation has at most one coordinator task in the daemon. The
coordinator:

- receives routed Observation IDs through a bounded mailbox;
- loads the Observation and current durable state;
- invokes the correct provider mapper;
- validates the typed mutation proposal with domain/application rules;
- submits one short atomic unit of work through the storage port;
- maintains hot caches of open Operations, aliases, heads, and capabilities;
- schedules Conversation-local probes for open facts;
- handles post-end amendments by rehydrating temporarily; and
- releases memory when the Conversation is parked and has no live work.

The coordinator is not authoritative storage. Every load-bearing open fact is
a row or Stream frame. Restart rehydrates from durable state without replaying
all historical Observations.

Concurrent provider inputs contributing to the same Conversation are
serialized by this coordinator. Provider source positions preserve their
native local order. If two AgentSessions append from the same semantic head,
the result is explicit divergence; mailbox arrival order does not decide which
branch wins.

### 6.2 Machine-scope services

Some facts cannot be owned by a Conversation coordinator. The daemon has named
services for:

- **Attention and alert watcher** — one machine tick, cross-Conversation
  attention aggregation, presence routing, settle windows, and alert arms;
- **Account service** — account profiles, quota windows, credential access,
  target selection, and migration sagas;
- **Discovery service** — global provider artifact directories and
  provisional ownership association;
- **Window registry** — terminal window-to-AgentSession bindings, verified tab
  state, arbitration, liveness, and pane lifecycle coordination;
- **Device/presence service** — authenticated device, web-view, terminal, and
  composing presence; and
- **Maintenance service** — retention, blob collection, backups, health, and
  repair scheduling.

Each service is a single named owner with durable rows. A coordinator never
reaches into another Conversation's in-memory state. Coordinators and services
communicate through application calls, database state, and outbox work.

### 6.3 Author-scoped mutable surface state

Drafts, queue pins, and certain preferences can have several writers. They are
not routed through a Conversation coordinator merely to fake single-writer
ownership. They use explicit compare-and-set rules, author sequences, origins,
and tombstones as defined later.

### 6.4 Transaction discipline

Application use cases declare short atomic units of work through semantic
storage ports. The initial SQLite adapter is responsible for connections,
locking, retries, and any serialization required by SQLite's single physical
writer. These are storage implementation details, not application-level
priority lanes or domain concepts.

Conversation coordinators provide logical serialization within a Conversation.
No file, subprocess, terminal, network, or provider I/O occurs inside a database
transaction. Maintenance writes are bounded and batched so that one transaction
does not monopolize the writer. Synchronous hooks use their fixed edge timeout
and safe pass-through behavior rather than a privileged database queue.

---

## 7. Core domain model

```text
Conversation
  |
  +-- head_node_id -----------------------+
  |                                       |
  +-- Node(parent_id) <-------------------+
  |      +-- ordered content parts
  |      +-- zero or more Streams
  |
  +-- AgentSession
         +-- process attempts
         +-- terminal binding
         +-- aliases and runtime-lineage links
         +-- Operation(parent_operation_id?)
                +-- zero or more Streams

Node / AgentSession / Operation / Resource
  +-- typed causal and lineage links
  +-- provider-local source position where applicable
```

Relationships:

- A Conversation has zero or more Nodes.
- A committed Node belongs to exactly one Conversation.
- A Node has at most one semantic parent.
- A Conversation points to one active committed head.
- A Conversation has one or more AgentSessions over its lifetime.
- An AgentSession belongs to one Conversation.
- An Operation belongs to one Conversation and normally one AgentSession.
- An Operation can be contained by another Operation.
- A Stream has exactly one Node or Operation owner.
- An owner can have several named Streams.
- A Node has one or more ordered content parts.
- Non-containment relationships use registered typed links.

Supporting tables can carry parts, aliases, attempts, provenance,
configuration, workflows, context checkpoints, resources, usage, preferences,
and projections without becoming additional core entities.

### 7.1 Conversation

A Conversation owns:

- stable baqylau identity;
- the current semantic head;
- a monotonic semantic revision;
- title and logical project association;
- creation/archive lifecycle; and
- the collection of participating AgentSessions.

It does not own provider IDs, PIDs, terminal windows, credentials, command
state, or streamed bytes.

Required record:

```text
conversations
  id                  UUID primary key
  title               nullable text
  head_node_id        nullable Node ID
  active_agent_session_id nullable designated interactive AgentSession
  revision            integer, monotonic on semantic mutation
  project_ref         nullable logical project reference
  created_at          timestamp
  updated_at          timestamp
  archived_at         nullable timestamp
```

Changing the head never deletes Nodes or undoes Operations that physically
occurred. It increments the Conversation revision, invalidates branch-sensitive
projections, and records provenance.

More than one AgentSession can attach to a Conversation. One may be designated
as the active interactive continuation, but simultaneous contributions are
allowed and divergence is explicit.

Physical workspace placement is supporting state:

```text
conversation_workspaces
  conversation_id
  backend_id
  workspace_ref
  role                  primary | source | handover_target | archived
  revision_ref          nullable
  dirty status          not stored here; Section 38.23 owns a three-valued TTL
                        cache keyed by backend/workspace
  active
  provenance_id
  observed_at
```

`project_ref` is logical identity; a local absolute path is not globally valid.

### 7.2 Node

A Node is one semantic message in the human/agent narrative.

Initial roles:

- `user`
- `assistant`
- `system`
- `summary`

Role alone is insufficient. Nodes also carry:

```text
semantic_kind: prompt | message | summary | recap | system
origin: human | provider | baqylau | peer | imported
```

Commands, tool calls, file edits, dialogs, subagents, and controls are
Operations, not Nodes.

Required record:

```text
nodes
  id                    UUID primary key
  conversation_id       foreign key
  parent_id             nullable Node ID
  agent_session_id      nullable producing AgentSession
  role                  user | assistant | system | summary
  semantic_kind         prompt | message | summary | recap | system
  origin                human | provider | baqylau | peer | imported
  state                 streaming | committed | aborted
  source_external_id    nullable provider record/message ID
  source_position       nullable provider-local sortable position
  turn_key              nullable correlation key
  actor_key             nullable provider-scoped actor key
  completion_reason     nullable complete/interrupted/failed/unknown
  source_timestamp      nullable timestamp
  created_at            timestamp
  committed_at          nullable timestamp
```

Node content is ordered and multimodal:

```text
node_parts
  node_id
  ordinal
  kind                  text | image | file | artifact | structured
  media_type            nullable
  content_ref           nullable sealed BlobRef
  stream_id             nullable provisional Stream
  resource_id           nullable Resource
  metadata              bounded validated JSON
```

Before commitment, a provisional Node and its Streams can grow or reconcile.
After commitment, role, semantic parent, and content are immutable. A
correction creates a replacement/superseding Node or a named repair record.

Only committed Nodes can become the Conversation head. An aborted provisional
Node does not move the head. An interrupted Node may be committed only when
provider evidence shows that the partial content remains in provider context.

The semantic parent relation is not copied blindly from provider record
parentage. Attachments, parallel tool results, and metadata can be native
siblings without being semantic branches.

### 7.3 AgentSession

An AgentSession represents one provider-native incarnation attached to a
Conversation, such as:

- a Claude resumable session;
- a Codex thread or rollout;
- an OpenCode session;
- a headless persisted invocation;
- an SDK/app-server thread; or
- an explicitly ephemeral invocation.

Required record:

```text
agent_sessions
  id                    UUID primary key
  conversation_id       foreign key
  provider_id           plugin ID
  execution_target_id   persisted frontend-selected target
  backend_id            configured backend
  mode                  interactive | headless | sdk | server | remote
  state                 starting | active | idle | ended | lost | archived
  resumable             boolean
  persistence_kind      native_local | native_remote |
                        baqylau_captured | ephemeral
  source_ref            nullable provider artifact/thread reference
  started_at            timestamp
  last_seen_at          timestamp
  ended_at              nullable timestamp
  end_reason            nullable code
```

Account and process placement are temporal attempt facts rather than mutable
history on the AgentSession:

```text
agent_session_attempts
  id
  agent_session_id
  backend_id
  account_id
  mode
  pid
  host_instance_id
  runtime_handle_ref
  started_at
  ended_at
  exit_status
  observation_quality
```

Resuming the same provider artifact normally reuses the AgentSession and opens
a new attempt. A provider fork that creates a genuinely new native thread
creates a new AgentSession. Provider-specific identity policy decides this and
records its evidence.

`agent_session_lifecycle` is the canonical lifecycle owner. The `state` column
on `agent_sessions` is a same-transaction query projection with this exact
mapping:

| Canonical lifecycle | Projected `agent_sessions.state` |
|---|---|
| host `starting` | `starting` |
| host `live`, work `active` | `active` |
| host `live`, work `drained` | `idle` |
| host `parked`, any work state | `idle` (detail still says parked/background-active) |
| host `ended`, work `drained` | `ended` |
| host/work `lost`, or `unknown` beyond its freshness horizon | `lost` |
| Conversation archived and host terminal with no active work | `archived` |

Host transitions are `starting -> live|ended|lost`,
`live -> parked|ended|lost`, and `parked|ended|lost -> starting` only when a
new resume attempt is created. Work transitions are
`drained -> active`, `active -> drained|unknown|lost`,
`unknown -> active|drained|lost`, and `lost -> active` only on later positive
evidence or a new attempt. Every transition atomically updates canonical axes,
the projected state, attempt facts, attention, revisions, and feed rows.
Anything else returns `409 invalid_session_transition`; only a named repair may
bypass the table.

Aliases are namespaced and validity-bounded:

```text
agent_session_aliases
  agent_session_id
  backend_id
  provider_id
  identity_kind
  external_id
  confidence
  valid_from
  valid_until           nullable
  active
  provenance_id
```

A mistaken association can be repaired by a recorded split/merge or alias
reassignment. Cwd alone is never sufficient evidence for a merge.

An AgentSession has at most one active provider-native session alias. The
identity kind is `native_session_id`; the clean-install schema enforces this
with the partial unique index `one_active_provider_native_alias_per_session`
inside the same transaction that attaches an alias. `AgentSessionStore` also
checks the active rows before the insert: a replay of the same native identity
is idempotent, a different native identity for the session is a
`provider_native_alias_conflict`, and an already ambiguous session is a
`multiple_active_provider_native_aliases` anomaly. Reverse lookup ambiguity or
an identity claimed by another session fails closed; it never chooses a row or
creates a second active alias. Other alias kinds retain the
`active_session_alias` uniqueness scope of backend, provider, identity kind,
and external ID.

An AgentSession does not require a terminal. Interactive sessions may have a
verified terminal binding; programmatic sessions do not.

### 7.4 Operation

An Operation represents structured work, interaction, delivery, or control
with a useful lifecycle.

Initial kinds include:

- `command`
- `tool`
- `file_read`
- `file_edit`
- `agent_task`
- `monitor`
- `compaction`
- `interaction`
- `message_delivery`
- `control`
- `rewind`
- `account_migration`
- `handover`

Required record:

```text
operations
  id                    UUID primary key
  conversation_id       foreign key
  agent_session_id      nullable/usually present
  anchor_node_id        nullable semantic anchor
  parent_operation_id   nullable containing Operation
  turn_key              nullable provider-turn correlation
  task_key              nullable assignment correlation
  actor_key             nullable provider-scoped actor
  source_position       nullable provider-local position
  kind                  registered core or namespaced kind
  state                 pending | running | succeeded | failed |
                        cancelled | denied | abandoned | lost | unknown
  origin                observed | requested | inferred | imported
  opener_state          present | missing | unknown
  schema_version        version
  data                  bounded validated kind data
  result_ref            nullable BlobRef/manifest
  source_timestamp      nullable timestamp
  started_at            timestamp
  ended_at              nullable timestamp
```

The common columns support cross-kind queries. Frequently queried fields or
substantial invariants use one-to-one detail tables such as
`command_details`, `interaction_details`, or `handover_details`. `data` must
not become an unvalidated junk drawer or hold unbounded bytes.

General lifecycle rules:

- terminal states do not silently reopen;
- duplicate starts are idempotent on correlation identity;
- absence of a closer produces `lost` or `unknown`, not success;
- inferred transitions record rule and provenance;
- authoritative later evidence can resolve `unknown`;
- every closer matches correlation identity before it closes anything; and
- completion cannot finalize a Stream while its source reader has unread
  durable bytes.

Kind-detail states map to the common Operation state; they are not additional
common states. `message_delivery.accepted|waiting_for_resume|relaunching|
dispatching|queued_at_provider|observed_in_history` map to `pending|running`,
`delivered` maps to `succeeded`,
and its cancel/lost/unknown states map by name. Interaction open/partial/
submitting map to `running`; answered maps to `succeeded`; declined/dismissed
map to `denied|cancelled`; expired/lost map to `lost`. A compaction whose closer
will never arrive maps to common `abandoned`. Invalid kind-detail/common pairs
return `409 invalid_operation_transition` and are guarded by detail-table
triggers plus application state-machine tests.

`parent_operation_id` means containment only. Other relationships use typed
links:

```text
activity_links
  from_type             node | operation | agent_session | resource
  from_id
  to_type               node | operation | agent_session | resource
  to_id
  relation              result_of | contributes_to | caused_by |
                        supersedes | summarizes | delivered_as |
                        produced | consumed
  provenance_id
```

This is a bounded adjacency index, not a generic graph database.

`turn_key`, `task_key`, and `actor_key` are distinct. One child actor can
perform several tasks; grouping results only by actor is invalid.

### 7.5 Stream

A Stream is incremental content with its own revision, storage, and retention.
It is a storage primitive, not an event bus.

Owners include provisional assistant Nodes, command Operations, tool output,
and agent progress. An owner may have several Streams for stdout, stderr,
reasoning summary, progress, structured data, and multiple content blocks.

Required record:

```text
streams
  id                    UUID primary key
  owner_type            node | operation
  owner_id              owner ID
  channel               text | stdout | stderr | reasoning | progress |
                        structured | namespaced
  ordinal               integer within owner/channel
  kind                  assistant_text | command_output | tool_output |
                        agent_progress | extension
  state                 open | sealed | aborted | lost
  mode                  ordered_delta | snapshot_revision
  revision              monotonic per Stream
  byte_length           current materialized length
  staging_ref           open framed-file reference
  final_ref             nullable sealed BlobRef
  retention_class       named policy
  created_at
  updated_at
  sealed_at             nullable
```

Normalized operations are:

- `append(offset, bytes)`
- `replace(start, end, bytes)`
- `reset(bytes)`
- `seal(final_bytes?)`
- `abort(reason)`
- `transfer(new_owner)` for supported ownership handoff

Final authority is declared per Stream kind:

- assistant-message Streams reconcile to authoritative provider-final content;
- command-output Streams treat captured streamed bytes as authoritative, with
  provider tool response only as fallback when no stream was captured;
- snapshot screen streams remain approximate until reconciled;
- provider-specific structured channels declare their own authority rule.

No universal “final record always wins” rule is allowed.

---

## 8. Supporting domain and persistence records

### 8.1 Provider-native record index

The semantic Node tree is deliberately not a lossless provider record graph.
Where provider artifacts exist, adapters maintain a rebuildable native index:

```text
native_records
  agent_session_id
  source_position
  external_id
  parent_external_id
  record_kind
  source_timestamp
  payload_ref/provenance_id
```

The index supports narrow branch rules, source discovery, audit, reimport, and
provider-specific views. It is not a second canonical conversation tree and
does not expose provider grammar to the core.

### 8.2 Context checkpoints

The semantic history and what a provider currently remembers are different.
Compaction and imported summaries require:

```text
context_checkpoints
  id
  agent_session_id
  at_node_id
  source_position
  summary_node_id        nullable
  summary_ref            nullable provider-only evidence
  covers_from_node_id
  covers_through_node_id
  context_window
  context_used
  state                  observed | inferred | superseded | unknown
  provenance_id
```

A checkpoint never deletes semantic history. It records believed provider
context coverage and supports context display and handover compilation.

### 8.3 Resources

Files, images, diffs, plans, logs, memory notes, and search results need stable
handles:

```text
resources
  id
  kind                  file | image | diff | plan | log | memory_note |
                        search_result | extension
  backend_id
  workspace_ref
  canonical_uri         nullable within its trust boundary
  media_type
  current_version_id
  retention_class
  created_at

resource_versions
  id
  resource_id
  content_ref
  digest
  byte_length
  source_operation_id
  provenance_id
  created_at
```

A path is always backend/workspace-scoped. Expiry leaves an honest unavailable
Resource; it never turns a dangling string into apparently complete content.

Uploads are staged Resources with stable, provider-readable absolute paths in
an owner-only area outside repository working trees. They are not ordinary
expiring blobs. Resolution back to a provider enforces a realpath jail and the
receiving provider's attachment grammar.

### 8.4 Interactions

Questions, permissions, plan approval, and confirmation menus are
`Operation(kind=interaction)` with structured details:

```text
interaction_details
  operation_id
  agent_session_id
  interaction_kind      question | permission | plan | confirm
  external_key
  prompt_ref
  options_ref
  response_ref
  response_revision
  state                  open | submitting | answered | dismissed |
                        expired | lost
```

Responses use compare-and-set against identity and revision. A stale browser
card cannot answer a newer provider dialog. Dynamic provider labels are read
from current state and validated at drive time.

### 8.5 Input buffers and preferences

Durable user-authored UI state is neither canonical conversation history nor a
rebuildable projection.

```text
input_buffers
  id
  kind                  composer | new_session | interaction
  conversation_id       nullable
  interaction_id        nullable
  project_ref           nullable
  text_ref
  revision              server monotonic revision
  author_id
  author_sequence
  origin                surface | device | terminal
  tombstone             boolean
  updated_at

preferences
  namespace
  scope_type            principal | device | conversation | project | global
  scope_id
  key
  schema_version
  value                 bounded validated JSON
  revision
  author_id
  author_sequence
  tombstone
  updated_at
```

Writes use compare-and-set and author ordering. Clears are tombstones so late
saves cannot resurrect sent text. Live reflections include `origin` so a
writer can suppress its own echo.

Terminal draft observation is asymmetric:

- non-empty observed text may update a terminal-origin buffer;
- observed empty clears only the lineage previously synchronized by that
  terminal writer;
- unreadable is not empty;
- an initial probe may adopt existing text; and
- recently pasted baqylau text is correlation-suppressed.

### 8.6 Slot allocations and audience facts

Stable palette/register assignment for concurrent commands, agents, or streams
is durable supporting state:

```text
slot_allocations
  scope_id
  entity_kind
  entity_id
  slot_number
  owner_pid             nullable for non-process ownership
  owner_host_instance_id nullable
  last_verified_at      nullable
  lease_expires_at      nullable for local live PID
  allocated_at
  released_at           nullable
```

The domain stores the stable small integer, not a color. Surfaces map the slot
to their own palettes. Persisted round-robin and repinning prevent visual
identity from changing as siblings finish.

Audience and register are semantic facts when the producer knows information a
presenter cannot reconstruct, such as host chrome, bubbled prose, or quiet
register. Colors, glyphs, CSS, and layout remain presentation-only.

### 8.7 Usage and quota facts

Usage is source-labelled before totals are projected:

```text
usage_facts
  id
  conversation_id       nullable
  agent_session_id      nullable
  actor_key             nullable
  account_id            nullable
  source                provider | otel | transcript | imported
  ledger                billing | per_actor_display | quota
  temporality           delta | cumulative_snapshot | message_snapshot
  model
  input_tokens
  output_tokens
  cache_read_tokens
  cache_create_5m_tokens
  cache_create_1h_tokens
  cache_create_unclassified_tokens
  vendor_cost_minor     nullable
  vendor_currency       nullable
  vendor_cost_source    nullable
  source_position
  dedup_key
  observed_at

quota_windows
  account_id
  provider_id
  scope_key
  used_percent
  resets_at
  state                  available | limited | logged_out | unknown
  observed_at
  provenance_id
```

Temporality is declared per source. OTLP deltas are summed. Repeated
message-level snapshots use the durable per-field positive-delta credit rule in
Section 38.17. Cumulative counters are differenced against the previous source
position.

Billing and per-actor display ledgers never add together. Price is computed on
read using a time-indexed provider/model price table. A source that cannot
separate 5-minute from 1-hour cache creation records the unclassified category;
it never guesses. Provider-reported and Baqylau-calculated cost are preserved
side by side. Unknown classification/pricing preserves tokens and returns an
unknown value or range.

### 8.8 Notifications

Alert policy is a durable workflow rather than an incidental rendering side
effect. The initial single-state inventory was insufficient; Section 38.16 is
the authoritative three-axis intent/arm/delivery model and Section 38.27
supplies its supporting DDL. Durable delivery handles allow restart-safe
escalation and retraction.

### 8.9 Repairs

Routine corrections use named application commands, not unrestricted SQL.
Every repair records:

- affected entity;
- old and new values or references;
- operator/tool identity;
- reason;
- evidence consulted;
- rule/code version; and
- timestamp.

Supported repair classes include alias reassignment, Conversation split/merge,
Activity placement correction, canonical supersession, projection rebuild,
and retained-evidence remap. An expert-only local diagnostic CLI may expose SQL
read access and a separately guarded repair escape hatch.

---

## 9. Durable ingestion and evidence

### 9.1 Pipeline

```text
external source
      |
      v
edge/backend adapter
      |
      v
insert deduplicated Observation
      |
      v
resolve scope and coordinator
      |
      v
register named Observation consumers
      |
      v
identity consumer resolves stable scope first
      |
      v
each remaining consumer independently:
  provider mapper returns its typed CanonicalBatch
  -> validate identities, transitions, head, capabilities, provenance
  -> transaction:
       that consumer's canonical mutations
       + provenance/decision links
       + consumer processing state
       + projection/revision changes required atomically
       + outbox/change rows
     COMMIT
```

This provides exactly-once canonical effects per
`(Observation, consumer_kind)` through ordinary uniqueness and transactions.
One consumer failure does not erase valid sibling concerns; Section 38.5 owns
claiming, completion, and quarantine. This does not imply exactly-once external
delivery.

### 9.2 Observation record

```text
observations
  id
  scope_kind            conversation | machine | account | window | unknown
  scope_id              nullable
  source_kind
  backend_id
  provider_id
  source_identity
  source_sequence
  dedup_key
  source_timestamp
  ingested_at
  payload_ref
  schema_hint
  edge_instance
  flags                 late | approximate
  processing_state      pending | processing | complete |
                        complete_with_quarantine | quarantined_identity |
                        ignored
  mapper_name
  mapper_version
  error_ref
```

An Observation says what a boundary reported. It is not a domain event and is
not assumed correct.

### 9.3 Deduplication

Prefer source identity. `dedup_key` is one globally unique canonical boundary
key, not a value that relies on a separate composite SQL scope. Its exact
serialization is:

```text
"obs:v1:" + lowercase_hex(sha256(
  utf8_len_prefixed(source_kind, backend_id-or-empty, provider_id-or-empty,
                    source_identity-or-empty, source_sequence-or-empty,
                    stable_record_id-or-empty, payload_sha256,
                    bounded_correlation_sha256)))
```

When unavailable, use a conservative versioned key based on source kind,
provider identity, stable record ID, payload digest, and bounded correlation
context. False deduplication is more damaging than duplicate evidence, so
uncertain observations remain distinct and canonical correlation handles
idempotency. Length prefixes are unsigned 32-bit big-endian byte lengths. An
observed digest collision with different canonical components is fatal evidence
corruption and quarantines the new Observation rather than deduplicating it.

Some once-only facts require atomic-take semantics. A delete/update row count
is the licence to consume a handoff or stop marker exactly once. Peek and take
are separate operations because unconsumed presence can itself be meaningful
liveness evidence.

### 9.4 Mapper contract

Provider mappers do not write tables directly. Each named consumer returns a
typed proposal for the facts it owns:

```python
@dataclass(frozen=True)
class CanonicalBatch:
    conversation_changes: tuple[ConversationChange, ...]
    node_changes: tuple[NodeChange, ...]
    agent_session_changes: tuple[AgentSessionChange, ...]
    operation_changes: tuple[OperationChange, ...]
    stream_changes: tuple[StreamChange, ...]
    supporting_changes: tuple[SupportingChange, ...]
    decisions: tuple[Decision, ...]
```

The application layer validates:

- identity namespaces and association confidence;
- Node parent/head membership;
- committed Node immutability;
- Operation and Stream transitions;
- expected Conversation revision/head;
- idempotency and correlation identity;
- capability constraints;
- schema versions;
- scope ownership; and
- provenance completeness.

Every registered Observation family has a default/complement mapping. An
unknown tool must produce a generic Operation or explicit ignored decision; it
must not silently disappear because no specialized mapper matched it.

### 9.5 Provenance and decisions

```text
provenance
  id
  observation_id
  rule_name
  rule_version
  decision
  reason
  created_at

provenance_links
  provenance_id
  entity_type
  entity_id
```

Every inferred or classified fact identifies the rule and evidence that
justifies it. Provider payload retention can expire independently, but durable
canonical facts retain their rule/version and an honest missing-evidence
marker.

### 9.6 Poison observations

A malformed input must not wedge a Conversation or the daemon:

1. Decode expected invalid data into an anomaly/unknown form where safe.
2. Catch unexpected failure at the Observation boundary.
3. Record the exception, mapper version, payload reference, and source.
4. Mark the failing consumer quarantined; quarantine the whole Observation only
   when identity/decoding failed and no consumer can proceed safely.
5. Advance unrelated work.
6. Surface degradation in health projections.
7. Permit explicit retry of that consumer with a repaired mapper.

The anomaly path has a recursion guard and does not depend on the failed
mapper. A coordinator crash restarts that coordinator. Repeated
identity/canonical-consumer crashes can park the Conversation with a visible
error; a repeated noncritical facet crash degrades only that facet. Other
consumers and Conversations continue.

### 9.7 Evidence failure domain

The happy path commits canonical state, evidence decisions, and outbox work
together. If the database is unavailable, provider safety outranks audit
atomicity:

- answerable hooks pass through unchanged;
- the daemon rejects new state-changing controls;
- callers that support errors receive an unavailable result;
- edges do not persist undelivered Observations; and
- one-shot or transient Observations during the outage may be lost.

A hook never fails its provider because baqylau storage is unavailable.

### 9.8 Warning-light plane

Evidence is primarily diagnostic, but runtime health is a sanctioned read:

- every caught-and-swallowed unexpected exception records an anomaly;
- severity and visibility distinguish expected degradation from product error;
- an unsuppressed raw health counter may be maintained at write time;
- class-based evidence pruning never decrements raw facts or that raw counter;
- the visible effective count applies the current versioned benign-signature
  registry at read time, including to old rows, as defined in Section 38.19;
- Conversation-local and machine-global failures remain distinct;
- benign signatures can be suppressed by versioned policy; and
- flood collapse prevents one repeating fault from overwhelming surfaces.

The diagnostic CLI supports session/conversation lists, anomalies, errors,
timeline, decisions, effect attempts, repairs, and read-only SQL.

---

## 10. Provider integration and environment contract

### 10.1 Narrow capability objects

A provider plugin composes narrow protocols rather than inheriting one large
base class:

```python
class ObservationDecoder(Protocol):
    def decode(self, observation: Observation) -> CanonicalBatch: ...

class InputTransformer(Protocol):
    async def prepare(self, request: AnswerableRequest) -> PreparedTransform: ...

class HistoryReader(Protocol):
    def discover(self, scope: DiscoveryScope) -> Iterable[NativeSession]: ...
    def import_history(self, native: NativeSession) -> CanonicalBatch: ...

class RuntimeDriver(Protocol):
    async def start(self, target: ExecutionTarget,
                    bootstrap: BootstrapInput) -> RuntimeHandle: ...
    async def resume(self, session: AgentSession,
                     bootstrap: BootstrapInput | None) -> RuntimeHandle: ...
    async def control(self, session: AgentSession,
                      action: ControlAction) -> ControlResult: ...

class LiveSource(Protocol):
    async def observations(self, handle: RuntimeHandle) \
            -> AsyncIterator[Observation]: ...

class SourceReader(Protocol):
    async def follow(self, source: SourceDescriptor,
                     cursor: SourceCursor) -> AsyncIterator[Observation]: ...

class AttachmentEncoder(Protocol):
    def encode(self, resources: Sequence[Resource],
               mode: str) -> BootstrapInput: ...

class HandoverTarget(Protocol):
    def capabilities(self) -> HandoverCapabilities: ...
    async def deliver(self, session: AgentSession,
                      package: HandoverPackage) -> DeliveryReceipt: ...

class UsageSource(Protocol):
    async def read_usage(self, scope: UsageScope) -> UsageObservation: ...
```

Implementation presence is the capability. Additional limitations are data,
not duplicated booleans. Capabilities can vary by provider version, backend,
account, execution mode, terminal availability, configuration, and runtime
probe.

Surfaces branch on capability data, never provider names. Routing follows the
AgentSession or actor that produced the item; a Codex child inside a Claude
host still uses Codex capabilities.

### 10.2 Provider knowledge jail

Only provider plugins know:

- hook payload and native record grammar;
- transcript/rollout layout;
- provider identity and branch rules;
- source discovery and cursor rules;
- screen anatomy and control vocabulary;
- account and usage vocabulary;
- native launch/resume arguments; and
- native import/export acceleration.

The core knows only canonical values, plugin IDs, and declared capabilities.

### 10.3 Environment snapshot

A machine-global daemon cannot infer configuration that exists only in a
provider process environment. Session-start-class edge frames include an
allowlisted environment snapshot, restamped whenever relevant values change.

The allowlist is an exact provider-owned key table, not a category wildcard.
Claude's registered keys are:

`CLAUDE_ACCOUNT`, `CLAUDE_PROFILE`, `CLAUDE_CONFIG_DIR`, `CLAUDE_PROJECT_DIR`,
`PWD`, `OLDPWD`, `CLAUDE_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL`,
`CLAUDE_CODE_DISABLE_1M_CONTEXT`, `CLAUDE_RELIMIT`, `CLAUDE_AUDIT`,
`CLAUDE_MIRROR_FORMAT`, `CLAUDE_MIRROR_STEP`, `CLAUDE_MIRROR_BIAS`,
`CLAUDE_MIRROR_SCROLLBACK`, `CLAUDE_MIRROR_LIVE_FG_SUB`, `KITTY_LISTEN_ON`,
`CLAUDE_DASH_*`, `CLAUDE_CODEX_GRACE_S`, and the registered execution/source
correlation keys. `KITTY_LISTEN_ON` is captured as a terminal endpoint hint,
never as a secret. The Codex and OpenCode manifests provide their own exact
key tables and fixtures; an unlisted variable is invisible by design.

The table covers these semantic categories:

- account/profile identity;
- provider config directory;
- logical project directory and cwd;
- model and effort overrides;
- context-window feature flags;
- account-migration/relaunch flags;
- mirror/read/feature gates; and
- execution mode and source correlation hints.

The snapshot is stored as AgentSession/attempt facts with provenance. Secrets
are not included. An unlisted environment variable is invisible by design.

Project configuration is resolved daemon-side through a versioned layering
algorithm using the observed cwd/project reference and stored snapshot. The
daemon's own environment is never substituted for the provider process's
environment.

Status-line integration respects singular ownership: if the provider exposes
one status-line slot already used by the user, the edge captures input and then
delegates to the configured real status line with identical stdin/stdout
behavior. Failure never breaks the provider's display path.

### 10.4 Provider mapping outline

Claude Code mapping:

| Native source | Canonical target |
|---|---|
| User/assistant semantic message | Node |
| Tool use/result | Operation |
| Bash execution/reporting | command Operation + Streams |
| Input rewrite | prepared transform + command correlation |
| Subagent/team lifecycle and semantic prose | agent_task Operations, actor tracks, actor-scoped Nodes, peer messages, and AgentSession links |
| Session/transcript identity | AgentSession + aliases/source reference |
| Headless semantic deltas | provisional Node Streams |
| Compaction/summary | compaction Operation + ContextCheckpoint + optional summary Node |
| Ask/plan/permission | interaction Operation |

Codex mapping:

| Native source | Canonical target |
|---|---|
| Rollout/app-server user/assistant item | Node |
| Command/tool/file item | Operation |
| Agent-message delta | provisional Node Stream |
| Thread/rollout identity | AgentSession |
| Turn identity | turn_key |
| Native child/collaboration item | nested Operation + actor/task keys |
| Thread fork | AgentSession lineage + semantic head relationship |
| Child result | result Operation + contributes_to link |

OpenCode mapping follows the same rules: messages become Nodes, tools become
Operations, part updates become Stream operations, and its native session
becomes an AgentSession.

### 10.5 Unknown providers and extension kinds

Adding a provider requires:

1. a manifest and stable plugin ID;
2. at least one discover/start/import path;
3. an ObservationDecoder or HistoryReader;
4. a RuntimeDriver for controllable modes;
5. canonical mapping and schema versions;
6. contract fixtures;
7. explicit absence for unsupported capabilities; and
8. no edits to provider-name switch tables in core or surfaces.

Namespaced Operation kinds and link relations must register bounded schemas.
If a kind needs indexes or significant invariants, it owns a detail table.

---

## 11. Synchronous answerable requests

Most ingestion is asynchronous. A narrow synchronous lane exists for provider
hooks that require a reply before work begins, especially foreground-command
rewriting.

### 11.1 Prepare-then-answer protocol

```text
edge hook with one fixed provider-safe timeout
  -> AnswerableRequest(request_id)
  -> authenticate and resolve AgentSession/Conversation
  -> evaluate deterministic eligibility gates
  -> PREPARE external capture resources
       create tee/staging file
       claim correlation and foreground slot
       install/start source reader or prove watcher readiness
       write handoff/liveness state
  -> short database transaction
       Observation + decision + correlation + answer
  -> reply with provider-native TransformResult
```

The reply is the final step. The invariant is:

> **Never rewrite a command unless baqylau is certain it can observe the
> rewritten execution.**

Preparation can perform bounded local filesystem/process setup outside the
SQLite transaction. It performs no network or terminal I/O. Every preparation
step has declared rollback. On any failure, the adapter removes prepared
resources, releases claims, emits best-effort diagnostics, and returns the
unmodified/pass-through answer.

### 11.2 Rules

1. Only registered Observation kinds are answerable.
2. Eligibility is provider/version/config scoped and fixtured.
3. Gates include existing redirects, read-command collapse, environment flags,
   nested/subagent mode, in-flight claims, and stale reclaim. For Claude,
   `CLAUDE_MIRROR_LIVE_FG_SUB` is an explicit registered gate for subagent
   foreground live tee (default disabled); the effective value and its
   provenance are recorded in the eligibility decision.
4. The edge has a strict provider-safe deadline.
5. Timeout, daemon absence, database failure, or invalid reply always means
   pass-through.
6. The later provider result reconciles whether the command actually ran.
7. Rewriting that changes provider permission behavior is explicit user-visible
   configuration.
8. The edge applies one timeout to the request. The daemon does not divide it
   into estimated per-step time budgets.
9. The daemon's answer is stored with the Observation, but is not by itself
   proof that the provider used the rewrite.

If the daemon replies after the edge timeout, the edge ignores the late answer
and passes the command through unchanged. The stored decision does not prove
that the provider used a rewrite; the later provider result reconciles what
actually ran. Prepared resources that were not used are reclaimed normally.

This is not a general synchronous RPC escape hatch.

---

## 12. Runtime correlation, closers, probes, and time

### 12.1 Open facts are durable

Open commands, agent tasks, monitors, dialogs, message deliveries, Stream
sources, workflow steps, and alert arms are rows written when opened. In-memory
coordinator objects are caches only.

Restart loads open rows, source cursors, staging Stream frames, due arms, and
pending effects. It does not replay the Conversation from birth.

Some screen-only facets are intentionally ephemeral, including provisional
ghost suggestions and observations whose truth is “currently visible on this
screen.” They are explicitly marked live-only and disappear on restart.

### 12.2 Correlation identity

Every opener and closer uses the strongest available identity:

- provider tool/operation ID;
- command transaction ID;
- Stream/source descriptor;
- agent task key;
- interaction external key;
- message-delivery idempotency key;
- process identity plus start evidence; or
- bounded provider-specific fallback correlation.

A closer first peeks and validates identity, then atomically consumes or closes
the matching row. A surviving record from a cancelled command must never be
consumed by the next command's result.

Some provider handoffs have no stable key and are honestly modeled as FIFO
correlation queues with strict scope rules. They are never generalized into
false identity.

### 12.3 Closer catalogue discipline

Provider plugins register closers for all known terminal outcomes, including:

- matching normal result;
- post-tool failure;
- denied or never-ran execution;
- interruption and queued-delivery cancellation;
- process death;
- provider turn abort/task complete;
- subagent killed, rejected, or API failed;
- monitor writer disappearance;
- host death;
- provider artifact EOF where authoritative;
- interaction decline by stable tool/dialog ID;
- duplicate stop via atomic take; and
- source ownership transfer.

Every subscribed success hook family must subscribe to its failure counterpart
where the provider exposes one.

Completion is gated on drained ingestion. A writer can be gone while unread
bytes remain. Source checkpoints distinguish the last read byte from the last
content surfaced to clients.

### 12.4 Probers

Probers are inbound adapters that emit Observations. Conversation coordinators
schedule local probes because they know which facts are open; machine services
schedule cross-scope probes.

Examples:

- PID/process-exit watchers;
- terminal focus and screen probes;
- monitor/source writer liveness;
- provider history growth;
- dialog and suggestion state;
- terminal input draft;
- provider account/quota polling; and
- global rollout discovery.

Process existence uses the rule that permission-denied liveness checks mean
“exists but foreign,” therefore alive. An unavailable terminal enumeration is
`unknown`, never proof that every session ended.

### 12.5 Silence and grace rules

Silence alone never proves successful completion. Sampler-based evidence can,
however, require measured grace/debounce/ceiling rules:

- N consecutive missing marker observations;
- idle duration plus absence of a write holder;
- monitor discovery give-up ceiling resulting in `lost`/`unknown`;
- short launch grace before terminal binding is expected; and
- display-only latch expiration.

Every such constant is part of a named evidence rule with fixtures and
rationale. A timeout can expire presentation; it cannot fabricate domain
success. The only exception is a registered bounded reconciliation probe for
an effect Baqylau itself caused; Section 38.6 defines its admissible baseline,
evidence, and refusal guards. That probe still requires positive observed state
change and does not turn elapsed time into success.

### 12.6 Timers

Durable arms are truth; in-memory timers are wake-up optimizations.

The authoritative arm fields and states are defined by the executable `arms`
DDL in Section 38.27. Notification intent holding/delivery is not encoded by
the arm state; Section 38.16 owns that separate state machine.

On restart, future arms are scheduled, overdue arms fire late, and every firing
rechecks current conditions. Emission is idempotent on the arm.

### 12.7 Lifecycle and parking

End evidence causes one ordered workflow:

1. close or resolve every open correlation with the appropriate end reason;
2. drain and seal/abort Streams;
3. finalize AgentSession attempt and state;
4. persist terminal/pane close intents;
5. archive/park readable state;
6. clear verified tab presentation; and
7. release the coordinator when no other AgentSession/work remains active.

Post-end telemetry and authoritative late records can amend parked state by
temporarily rehydrating the coordinator. They do not reactivate the runtime.

---

## 13. Identity, conversation branches, and context

### 13.1 Identity resolution

At first sight, uncertain provider identities may remain provisional. Identity
resolution uses provider-native IDs, artifact references, process/window
evidence, explicit launch correlation, predecessor liveness, and provider-
specific resume/fork rules.

Wrong association is possible and must be repairable. A recorded split/merge
or alias reassignment updates canonical association and affected projections
without rewriting raw evidence.

Session-less Observations are first-class. Launch, upload, preference, alert,
and discovery evidence can precede the birth of a Conversation or outlive it.
They use machine/account/window scope rather than a fake Conversation ID.

### 13.2 Semantic tree and live branch

The semantic Conversation is a tree through `Node.parent_id`. The live branch
is the ancestor chain of `Conversation.head_node_id`.

A rewind moves the head to an ancestor after provider evidence confirms the
semantic change. A later prompt under that ancestor forms a new branch. Nodes
on abandoned branches remain inspectable.

Native record siblinghood is insufficient branch evidence. Provider adapters
apply narrow measured semantic rules. For Claude-style records, two genuine
user prompts sharing the same semantic parent can indicate replacement or
fork; tool results, attachments, and parallel operations do not.

No permanent “branch discarded” truth row is required. Head changes and
supersession relations are canonical; branch-sensitive projections recompute
against the active ancestor chain.

### 13.3 Branch classes

Every fact/projection declares one class:

- **Cumulative** — tokens, executed commands, elapsed time, alerts delivered;
  head movement does not undo them.
- **Branch-sensitive current state** — goal, task state, title inputs, context,
  plan; recompute against the selected head/checkpoint.
- **Read-filtered** — semantic conversation and branch views; filter by
  ancestry at query time.
- **Lossy-sampler cumulative** — facts whose source is explicitly a poll-diff
  channel that can miss intermediate transitions.
- **Live-only** — current screen suggestion or similar ephemeral state.

### 13.4 Provider context

A ContextCheckpoint answers “what this provider is believed to remember,” not
“what happened.” Compaction can create a summary Node, a compaction Operation,
and a ContextCheckpoint in one rule. Earlier semantic Nodes remain visible.

While a compaction latch is active, no intermediate context occupancy is
published. When a provider closing record exists, latch clear and
post-compaction occupancy commit under one Conversation revision. If that
record never arrives, the display latch expires independently on the read side
without closing the compaction Operation; later provider evidence updates
occupancy and lifecycle honestly as defined in Section 38.13.

---

## 14. Activity graph and canonical presentation order

### 14.1 Why the Node tree is insufficient

The Conversation tree represents dialogue ancestry. Activity around it is an
ordered, causally linked graph:

- tools occur between assistant messages;
- child work is contained by task Operations;
- a child result can contribute to a parent answer even if its completion card
  arrives later;
- sidecar and nested AgentSessions cross provider boundaries;
- late transcript records can have source positions before already displayed
  activity; and
- pagination must preserve whole command/copy blocks.

Forcing these facts into Node ancestry or timestamp order is incorrect.

### 14.2 Source positions and causal placement

Each adapter supplies the strongest available local position:

- transcript byte/record position;
- rollout item position;
- app-server sequence;
- structured stream item index; or
- daemon-assigned per-AgentSession ingest position.

Source timestamp is diagnostic, not the sole order key.

The ActivityComposer orders by:

1. selected Conversation branch;
2. semantic turn constraints: a child task's result is placed before the final
   response of the parent turn it contributed to, regardless of timestamps or
   later lifecycle arrival;
3. explicit causal links such as `contributes_to`;
4. provider source position within AgentSession/turn;
5. provider and handover/nesting boundaries;
6. declared slot adoption (“render in the slot of X”); and
7. local ingest position as deterministic fallback.

A placement correction moves the materialized activity record; it never changes
the Operation identity, because Operation IDs are cursor and correlation
anchors. Claude parent turns are inert for ordering after their final response:
late child lifecycle bookkeeping may amend the child card but cannot move the
parent final response past unrelated later turns.

One server-owned placement algorithm is used for initial backlog, older-page
pagination, live updates, and every surface. Clients never merge Node and
Operation lists by timestamp.

### 14.3 Materialized activity

```text
conversation_activity
  conversation_id
  branch_head_id
  generation
  local_seq
  item_type             node | operation | notice
  item_id
  block_key
  placement_kind
  placement_anchor      nullable
  source_revision
```

`local_seq` is scoped to a projection generation. Late structural evidence can
create a new generation or targeted placement amendment. Page tokens include
Conversation, head, generation/source revision, and whole-block boundary.

### 14.4 Live amendments

The structural feed has a small closed set of idempotent activity operations:

- `append(item, placement)`
- `retract(item_id)`
- `supersede(old_id, new_id)`
- `move(item_id, anchor)`
- `amend(item_id, revision)`
- `resnapshot_required(generation)`

Each operation is derivable from a full snapshot. These are delivery
instructions over canonical state, not domain truth.

Agent/actor scope is a subscription dimension, not merely a client filter. A
scope declares its conversation source, normalization, scoped facets, and
cursor. Session-wide health or memory can coexist with actor-scoped work in a
single DTO only when every field declares its scope.

---

## 15. Streaming architecture

### 15.1 Source capability levels

Providers/modes declare:

1. `ordered_delta` — semantic item identity and ordered deltas;
2. `snapshot_revision` — approximate current display snapshots;
3. `round_or_item` — complete blocks/items during a turn; or
4. `final_only` — content appears only at provider commit.

The product degrades honestly. Final-only mode shows activity followed by the
final message; it does not pretend to stream.

### 15.2 Open Stream lifecycle

```text
open owner + Stream
  -> append/replace/reset coalesced frames
  -> publish Stream revisions
  -> seal with per-kind authority
     | abort with partial retained
     | mark lost
     | transfer ownership
```

Assistant response lifecycle:

1. Create a provisional Node and one or more Streams.
2. Coalesce source deltas and append staging frames.
3. Publish Stream revision/length.
4. On provider-final evidence, reconcile content.
5. Seal Streams and store BlobRefs.
6. Commit the Node and move the head if expected-head validation succeeds.
7. Emit one structural commit plus Stream-sealed frames.

A provider turn can alternate Nodes and Operations. One mutable “turn response”
does not absorb all assistant blocks, tools, and results.

### 15.3 Staging files

Open bytes do not create one SQLite row per token or output chunk. Each Stream
uses an owner-only framed staging file:

```text
runtime/streams/<stream-id>.open
```

Frames contain local revision, source sequence range, operation type, offsets,
payload length, checksum, and bytes. Complete frames survive a crash; a torn
final frame is discarded.

Coalescing uses a short latency window, byte threshold, semantic boundaries,
and a hard latency ceiling. Metadata is checkpointed coarsely: open, periodic
length/revision, truncation marker, and seal.

### 15.4 Source-reader discipline

Readers:

- consume complete frames/lines only;
- retain exact byte/source cursors;
- detect truncation, replacement, and inode changes;
- bound each pump for fairness; parsed semantic-record readers remain lossless,
  while command-output presentation uses the explicit 64 KiB surfaced-line cap
  and honest elision marker in Section 38.8;
- distinguish last read from last surfaced content;
- preserve stdout/stderr combination policy; and
- record runaway truncation on the owning Stream.

Command output can transfer from foreground to background ownership. The
transfer records the new owner/source, flush boundary, and elapsed semantics;
it does not fabricate a completed command.

### 15.5 TUI stream tap ladder

For interactive tools that persist only complete messages:

1. **Screen snapshot polling** is the default approximate-live source. It is
   active only while other evidence indicates an open message and reconciles
   to provider-final content.
2. **Provider/API relay tapping** is the opt-in adapter specified in Section
   38.33. Its authentication, byte-forwarding independence, failure behavior,
   and semantic delta output are fixed even when implementation is phased.
3. PTY wrappers and process injection are not default integration strategies;
   they create load-bearing crash and compatibility risks.

Headless or app-server modes with true semantic deltas are first-class and can
provide better streaming than interactive TUI observation.

### 15.6 Backpressure and client recovery

Slow clients never block source ingestion. Each client has a bounded queue. On
overflow:

- drop queued incremental deltas for that client;
- send `stream.changed` with Stream ID/current revision and
  `resync_required=true`;
- let the client fetch current content/range; and
- continue later revisions.

Structural and Stream recovery are independent. A lost Stream delta does not
require replaying the whole Conversation.

### 15.7 Retention and copyability

Presentation caps do not silently change stored canonical content. Separate
policies define:

- runaway capture caps with honest truncation markers;
- open staging retention;
- sealed blob retention by content class;
- resident timeline horizon; and
- copyability horizon.

If a block remains scrollable and advertises ordinary Copy, its immutable
visible WYSIWYG copy source must exist. Raw download is a separately advertised
capability and can expire with `410 raw_content_expired`; it never rereads a
transient tee or changed workspace file.

---

## 16. Persistence architecture

### 16.1 Storage tiers

| Tier | Contents | Durability |
|---|---|---|
| Canonical | Conversations, Nodes, AgentSessions, Operations, Stream metadata, links, open correlations | Product state; prunable only through declared archive policy |
| Supporting | Resources, checkpoints, attempts, aliases, slots, usage, notifications, preferences, arms | Product/workflow state with class-specific retention |
| Evidence | Observations, provenance, decisions, anomalies, effects, repairs | Bounded by source/class; diagnostic |
| Blob/Stream | Open frames, output, message bodies, files, diffs, plans, handover bundles | Class-based retention |
| Provider artifacts | Transcripts, rollouts, threads, provider files | Externally owned; watched/indexed, not copied wholesale |

### 16.2 Metadata database

Initial choice:

- one SQLite database;
- WAL mode;
- foreign keys enabled;
- `synchronous=NORMAL` unless durability testing requires otherwise;
- explicit short transactions;
- migrations committed with the application;
- transaction boundaries exposed through semantic storage ports; and
- no event-sourcing persistence library.

One database is the default because it simplifies atomic cross-module changes,
provider handover, machine services, backup, queries, and repair.

The database is permanently machine-wide. Per-Conversation SQLite files and a
catalog-plus-partitions layout are out of scope. If compound benchmarks miss a
latency gate, the required response is to reduce transaction duration, batch
derived work, tune indexes and checkpoints, isolate bulk bytes, coalesce
writes. A release that cannot pass on one machine-wide SQLite database does not
conform to v4 and must not ship under this design.
Splitting SQLite by Conversation is not an allowed implementation choice.

### 16.3 Blob store

Sealed immutable content is addressed by digest:

```text
blobs/<sha256-prefix>/<sha256>
```

Blob metadata records digest, length, media class, compression, creation time,
retention class, expiry, and reachability/reference information. Blob writes
are staged, fsynced according to class, atomically renamed, then referenced by
the metadata transaction. Orphans are collected after a grace period.

### 16.4 Evidence retention

Retention is class-based:

- identity and repair evidence is long-lived;
- command/tool payloads can expire after diagnostic and parity horizons;
- security/control evidence follows security policy;
- high-volume output uses Blob retention, not Observation-row duplication;
- surface telemetry is short-lived; and
- user-visible cumulative counts are canonical facts and never recomputed from
  a pruned evidence count.

### 16.5 Backups and corruption

The daemon supplies:

- SQLite online backup or verified snapshot procedure;
- blob reachability manifest;
- restore validation;
- schema-version compatibility checks;
- corruption health state that disables mutations;
- documented staging recovery; and
- a local health/repair command.

Provider hooks continue pass-through during database failure. Observations
that cannot be delivered during the outage are not retained by edge clients.

---

## 17. Effects, controls, and workflows

### 17.1 Transactional outbox

Any canonical change requiring external action inserts an outbox row in the
same transaction:

```text
outbox
  id
  kind
  entity_type
  entity_id
  payload_ref/data
  idempotency_key
  priority
  state
  available_at
  attempt_count
  created_at
```

Examples include provider control, launch/resume, terminal paint, pane
lifecycle, alert delivery/retraction, peer messaging, and structural-feed
publication.

### 17.2 Attempts and truthful outcomes

```text
effect_attempts
  id
  outbox_id
  adapter_id
  attempt_number
  lease_id
  started_at
  ended_at
  outcome               succeeded | failed_before_action |
                        rejected | indeterminate
  receipt_ref
  error_ref
```

Workers claim with durable leases. Idempotent effects can retry. Non-idempotent
effects—typing keys/text, launching a tab, sending a push/peer message—record a
pre-effect attempt. A crash after possible action becomes `indeterminate` and
is reconciled, never blindly retried.

Every effect outcome returns through the Observation pipeline. Desired state
is not treated as observed state. Terminal paint deduplication compares the
last verified receipt, not the last attempted color.

### 17.3 Semantic controls

Application controls include:

- send;
- interrupt;
- rename/autoname;
- answer interaction;
- decide plan;
- rewind/fork;
- compact;
- switch model/effort;
- close;
- resume;
- account migrate; and
- provider handover.

The application never exposes “press key X” as its port contract. A provider
RuntimeDriver can implement the action using terminal input, RPC, SDK,
app-server, process signal, or verified provider artifact mutation.

Control flow:

```text
surface request
  -> authenticate and authorize
  -> validate Conversation/AgentSession revision and capability
  -> create Operation(kind=control, pending)
  -> enqueue outbox effect
  -> RuntimeDriver attempt
  -> receipt/prober/history Observation
  -> Operation succeeded/failed/denied/unknown
```

The HTTP response normally returns `202` plus Operation/gesture ID. Completion
arrives through the structural feed.

### 17.4 Typed verdicts and reachability

Control completion carries a typed verdict, not one boolean. Verdicts can
include:

- restored draft text;
- queue state;
- effective title/channel;
- live plan/interaction options;
- confirmation required only for an unexpected or separately consent-worthy
  provider choice; expected model/effort/rewind confirmation is completed
  inside the initiating gesture as specified in Section 38.12;
- delivery proof status;
- refusal code and current capability floor; and
- indeterminate/reconciliation guidance.

The read API supplies a per-action reachability map combining registered
capabilities, configuration, current AgentSession/window state, and known
refusal floors. Actions remain visible but disabled with reason. A late
drive-time refusal remains possible and is returned as a typed verdict.

The initial Claude refusal floors are exact: `compact` requires at least two
committed prompt Nodes on the live branch; an argumentless `rename`/autoname
requires at least one. Their inputs are the branch projection at the request's
validated revision, not visible-row count or terminal text. A provider plugin
must register numeric floors and their counted semantic kind before exposing
the action; an omitted floor means no pre-refusal claim, not zero inferred by
the UI.

Capability-file/read failure degrades open: a read error cannot disable a real
provider session's control plane, so the action remains visible/reachable with
`freshness=unknown` and is revalidated at drive time. Positive evidence that a
capability is unsupported and negative reachability evidence for the current
binding fail closed. Thus `unknown because read failed` is distinct from
`unsupported` and `unreachable`.

### 17.5 Message delivery

A surface send creates `Operation(kind=message_delivery)` with an idempotency
key and intended content/resource manifest. It does not immediately create a
committed user Node.

Lifecycle:

```text
accepted -> dispatching -> queued_at_provider -> observed_in_history -> delivered
    |
    +-> waiting_for_resume -> relaunching -> dispatching
                                      \-> cancelled | lost | unknown
```

`POST .../messages` is also the one-gesture **resume and send** workflow. Its
`parked_policy` is `reject | resume`. A live session requires `reject` and no
runtime body. A parked or ended-but-resumable session with
`parked_policy=resume` requires a complete fresh `RuntimeRequest`; the service
creates one `message_delivery` Operation in `waiting_for_resume` and stores the
message, Resources, target runtime, and original idempotency key before any
effect. The saga moves to `relaunching`, executes the normal resume proof, then
either passes the message as the provider's declared launch-time initial input
or dispatches it after the resumed binding becomes live. Provider capability
selects one measured path; it cannot send twice. The user makes one request and
receives one Operation ID.

If relaunch fails before action, delivery fails without sending. A possibly
accepted relaunch becomes `unknown` and reconciles before message dispatch. If
resume is verified but later message delivery fails, the AgentSession remains
resumed and the message Operation reports that split outcome. A non-resumable
or missing artifact returns `410 session_artifact_gone`;
`parked_policy=reject` returns `409 session_not_live` without an effect.

The provider prompt record creates the canonical Node and links it with
`delivered_as`. For a baqylau-captured headless mode with no native history, a
durable RuntimeDriver input-acceptance receipt is authoritative and can commit
the Node.

The outbound-message correlation survives restarts. Matching uses provider ID
where possible; provider-specific normalized suffix matching is permitted when
attachments prepend content or restored terminal input joins the message. The
rule and ambiguity are recorded once in the provider adapter.

Paste success is not delivery proof. Browser optimistic bubbles and
shown/reconciled/dropped telemetry are surface evidence, never canonical
Nodes.

### 17.6 Rewind

Conversation rewind and workspace restoration are separate planes. A rewind
Operation records:

- target Node/provider checkpoint;
- requested mode: conversation, workspace, or both;
- pre-action Conversation revision/head;
- pre-action workspace revision/fingerprint;
- provider control attempt;
- observed post-action provider head;
- observed post-action workspace fingerprint; and
- split/indeterminate outcome.

The Conversation head moves only on provider/history evidence. Workspace state
changes only on verified backend evidence. Partial success is explicit.

### 17.7 Sagas

Handover, account migration, rewind-both, and close-then-resume are durable
multi-step workflows. There is no generic workflow language initially. Each
Operation kind owns a versioned detail/checkpoint reducer and uses common
outbox/attempt/arm machinery.

Every step records precondition revision, requested effect, attempt, receipt or
reconciliation evidence, next safe step, loop/cooldown guard, and manual
recovery guidance. Restart resumes from durable checkpoints without duplicating
launch or delivery.

---

## 18. Queries, projections, and client delivery

### 18.1 CQRS-lite

Writes use application services and domain rules. Reads use:

- canonical tables directly for simple entity/history queries;
- indexed projections for expensive/current facets;
- ActivityComposer for mixed semantic activity;
- Blob/Stream range reads for large content; and
- named live adapter reads only for external facts that should not be stored.

A read-only SQL query does not need to pass through a domain repository when
module ownership and authorization remain clear.

### 18.2 Projection contract

Every projection declares:

- owning module;
- source entities/revisions;
- branch class;
- rebuild scope;
- consistency requirement;
- synchronous or asynchronous update mode;
- indexes; and
- supported/empty/unknown/unsupported semantics.

Critical cheap projections can update in the canonical transaction. Other
projections expose their source revision and staleness. Rebuilding a current
projection never replays historical external effects or notifications.

Materialize only when a measured query or correctness contract justifies it.
Direct indexed SQL plus small TTL caches remains preferred for simple
cross-Conversation lists and statistics.

### 18.3 HTTP API contract

Section 38.24 is the authoritative fixed endpoint inventory; Sections 38.36
and 38.38 close its field schemas, authentication, errors, and traceability.
It deliberately has no `/api/v1/global` catch-all and no generic `actions`
endpoint. Each view may still receive one composed snapshot from its owning
endpoint so avoiding a catch-all does not force a browser to make dozens of
small requests.

### 18.4 Backlog and pagination

Initial history uses compressed HTTP, not SSE:

- newest useful whole-block window;
- backward pagination;
- server-composed Activity items;
- page tokens containing head, generation/source revision, and boundary;
- no cut inside a command/copy/activity block; and
- explicit resnapshot if late evidence invalidates a token.

Compressed HTTP is the backlog plane. SSE/WebSocket-like delivery carries only
incremental changes.

### 18.5 Structural feed

Structural frames carry entity IDs/revisions or typed Activity amendments.
The feed is bounded and may use an implementation `change_id` for transport
catch-up. That ID is never a domain order, never spans into Stream byte order,
and never decides canonical causality.

Primary correctness cursors are scoped:

- `(Conversation, semantic revision)`;
- `(Conversation, activity generation/local position)`;
- `(Conversation, actor scope, backbone position)`;
- `(Stream, revision, byte/range position)`; and
- a separate global-plane cursor for machine service state.

When a retained cursor expires, clients resnapshot explicitly.

### 18.6 Registration race

For a Conversation view, the server reads the composed snapshot and feed
high-water cursor in one SQLite read snapshot. The client applies it, fetches
required open Stream content, then opens SSE with that cursor in
`Last-Event-ID`. The server replays strictly after the cursor before splicing
into live delivery. This is the exact Section 38.22 protocol; registration does
not require a live connection to exist before the HTTP snapshot.

If a Stream queue overflows during catch-up, refetch that Stream only.

### 18.7 DTO and fallback discipline

The API maps canonical/supporting rows into typed surface-neutral DTOs. Missing
capability is explicit; unsupported is not represented as zero or empty.

Because historical rows may predate new fields and there is no universal
replay, every newly introduced DTO field declares a fallback ladder and
precedence rule. Read-time extras with no durable owner—such as TTL-cached git
status or provider RPC limit reads—are named query adapters, never hidden
domain mutations.

---

## 19. Attention, presence, and alerts

### 19.1 Attention projection

Attention is derived from AgentSession state, open Operations, interactions,
turn/actor correlations, provider notification classification, and current
activity.

Provider-neutral precedence, highest first:

```text
asking/permission
executing
awaiting background or agent work
working/thinking
done
idle
```

Rules include:

- child inner events never drive host-main attention;
- foreground command completion returns to working, never automatically done;
- background/monitor completion can drive done only under explicit host rules;
- subagent completion normally returns host to working;
- unknown provider notifications do not mean done;
- permissions participate in asking state; and
- open blocking work prevents false done.

Attention is computed first per AgentSession/actor scope. Conversation-level
attention is an explicit aggregation policy, normally the active interactive
AgentSession plus any blocking interaction in another attached session.

```text
attention_projection(scope_type, scope_id, state, source_revision, ...)
attention_transitions(id, conversation_id, agent_session_id,
                      from_state, to_state, cause, created_at)
```

Current attention can rebuild. Historical notification delivery does not
re-fire because notification intents are durable workflow facts.

### 19.2 Presence

Presence is authenticated, scoped, TTL-bound evidence:

- device active;
- browser viewing a Conversation;
- terminal application frontmost;
- terminal tab focused;
- composing an InputBuffer; and
- provider screen interaction observed.

Presence from an unauthenticated ingestion edge is not accepted as user
presence. Device-wide presence and Conversation viewing are not interchangeable.

### 19.3 Alert semantics

Policy preserves these asymmetries:

- asking can alert immediately because the provider is blocked;
- done waits for a settle window;
- looking at a done response can resolve/retract it;
- looking at an unanswered question does not resolve it;
- composing can cancel or retract relevant reminders;
- transient done never sends;
- session end/state change cancels stale intents;
- channel escalation creates separate attempts and handles; and
- global toggle is evaluated when arming, mute at every send/escalation, and
  presence at the kind-specific cadences in Section 38.16; these clocks must
  not be collapsed into one generic “decision time.”

The machine watcher amortizes terminal/device probes across all armed
Conversations. A needs-you badge counts Conversations, not raw events.

---

## 20. Terminal and surface architecture

### 20.1 Terminology and ports

Use **Surface** for web/pane/CLI/phone/MCP, **Terminal adapter** for kitty or
future terminals, **Provider adapter** for agent tools, and **Backend** for the
machine/service connection.

Terminal roles are narrow protocols:

- `TerminalPresence`
- `TerminalDiscovery`
- `TerminalDisplay`
- `TerminalInput`
- `PaneManager`
- `ViewportReader`
- `FocusProbe`
- `Clipboard`
- `WindowTagger`
- `OpenActionChannel`

A terminal implements only supported roles. The null terminal is normal.

The kitty adapter has an explicit discovery contract for a daemon that is not
a kitty descendant. An AgentSession attempt first uses its captured
`KITTY_LISTEN_ON` endpoint hint. If absent, `TerminalDiscovery` enumerates
only the current user's known kitty sockets (`$TMPDIR/kitty-*` and the
provider's registered socket directory), probes each with a read-only identity
request, and matches the returned OS window/tab identity to the persisted
terminal binding. Multiple matches are an `ambiguous_terminal_focus` error;
zero matches are `terminal_unavailable`. A socket is never selected by file
name alone, and discovery never scans another user's temporary directory.
The resolved endpoint is stored on the binding revision and revalidated before
every external effect.

### 20.2 Terminal binding and verification

A terminal binding belongs to an AgentSession attempt, not Conversation
identity:

```text
terminal_bindings
  agent_session_attempt_id
  terminal_adapter_id
  backend_id
  window_id
  user_var_tag
  state
  observed_at
```

Window enumeration and user-var tag read/write support identity adoption and
resume. A stale-pane sweep is audited and verifies that one provider tab hosts
the intended AgentSession before closing or retagging anything.

Nested-host detection returns three values:

- host with verified window;
- nested runtime inheriting another host's window; or
- anchorless host without a window.

Nested and anchorless are different. A nested runtime must not sweep the outer
host's panes.

Terminal enumeration failure is unknown. A new session receives a measured
binding grace. Focus/frontmost and tab selection are separate observations.

### 20.3 Pane host

The pane host is a thin process inside the pane. It owns stdout/scrollback,
SIGWINCH, local width, renderer caches, open-action link generation, and
mandatory viewport restoration for every full repaint. Actual clicks arrive
out of band through the separately registered `OpenActionChannel`; mouse
reporting is never enabled. The pane consumes snapshots, structural frames,
and Stream updates. The daemon sends semantic blocks, never prewrapped ANSI.

Pane lifecycle is an effect workflow:

- headless/anchorless AgentSessions create no phantom pane;
- creation anchors to the verified provider tab, not current focus;
- helper panes do not steal application focus;
- remembered width is a scoped preference;
- resize is observed/reconciled because it is asynchronous;
- stale panes close only after binding verification;
- lifecycle ending preserves close/park/tab-clear order; and
- failed terminal effects never become observed state.

### 20.4 Presentation blocks

The presenter maps domain state to semantic blocks:

```text
MessageBlock
CommandBlock(header, request, Streams, outcome, Resource links)
FileChangeBlock(Resource, verb, extent, additions, removals, diff)
AgentTaskBlock(task_key, actor, phase, result)
InteractionBlock(identity, state, options, response)
NoticeBlock(severity, text, provenance link)
GenericActivityBlock(title, summary, activity_class, register, audience)
MultiResourceOutputBlock(Resources[], command, Stream, activity_class)
```

Blocks expose opaque semantic actions such as copy request/output, expand
Resource, open file, or inspect evidence. They contain no trusted HTML. Parsed
producer SGR colour and OSC 8 links are represented as safe semantic spans;
every other producer terminal control is neutralized. Stored `render_kind` and
language choose Markdown/JSON/YAML/source behavior consistently, while web and
terminal independently choose glyphs, folding, and width. A multi-file command
uses one `MultiResourceOutputBlock`; the presenter never invents which output
belongs to which file.

### 20.5 Sanitization

Sanitize at every rendering leaf:

- terminal re-emits canonical renderer sequences plus parsed/allowlisted
  producer SGR and OSC 8 semantics; it never forwards raw producer controls;
- web escapes/sanitizes and allowlists link schemes;
- blobs use safe content type/disposition and `nosniff`;
- CLI normalizes untrusted controls; and
- every new surface supplies a sanitizer contract.

Raw evidence is never destructively rewritten merely for one surface.

### 20.6 Clipboard, uploads, and dictation

Clipboard path discovery is a privileged local capability with exact basename
agreement and audit. Remote clients cannot cause arbitrary controller paths to
be sent to a provider. Clipboard images are cleared only by provider adapters
whose declared send semantics require it, using a leased effect.

Uploads enforce name, size, media, realpath-jail, backend, and lifecycle rules.
Dictation keeps provider/API secrets server-side and can mint short-lived
restricted grants. Project-specific key terms follow declared configuration
layering.

---

## 21. Accounts, credentials, and usage

### 21.1 Account model

Account profiles are provider/backend-scoped configuration:

```text
account_profiles
  id
  provider_id
  backend_id
  label
  credential_ref
  enabled
  metadata
```

Credentials remain in provider-native/keychain storage where possible.
`credential_ref` is not the secret.

Quota state includes account-wide and model-specific windows, reset times,
logged-out and limited latches, stale-window rollover arithmetic, measured
graces, and vendor-message parsing provenance.

### 21.2 Credential port

The account service owns a narrow audited credential port for providers that
require it:

- read provider-native credentials;
- refresh through provider-supported flow;
- write rotated credential state back atomically/merged into the provider's
  canonical store; and
- lease refresh to prevent replay races.

Credentials never enter Observation payloads, handover bundles, or surface
DTOs. Usage polling and automatic migration remain separate from credential
material.

### 21.3 Target selection

The target selector is a pure, fixtured policy over account profiles, quota
windows, model availability, reset horizon, effective burn, user policy, and
cooldown state. It records the full ranked decision and refusal reasons.

It does not read keychains, provider files, or terminals internally.

### 21.4 Same-provider account migration

Account migration is a relaunch/resume saga, not cross-provider export:

1. record limit/logout evidence;
2. apply cooldown/loop guard;
3. select target and optional model fallback;
4. announce before source parking when continuation semantics require it;
5. settle/detach the source attempt;
6. wait for verified park/end, except the manual logged-out recovery path whose
   authoritative authentication-failure evidence licenses bypass;
7. resume the same provider artifact when supported, otherwise create a
   provider-defined successor AgentSession;
8. open a new attempt with target account placement; and
9. activate only after provider identity/history evidence arrives.

Historical usage/effects retain the attempt account under which they occurred.
No old row's account is mutated to rewrite history.

### 21.5 Telemetry and cost

Provider telemetry enters as Observations. For OTLP:

- malformed batches produce evidence and do not crash the listener;
- datapoints route by provider session identity/alias;
- unknown identities remain machine evidence until associated;
- delta temporality is summed;
- hidden auxiliary provider work has its own bucket;
- transcript fallback runs only if billing telemetry was never seen;
- a late authoritative export replaces fallback rather than adding; and
- post-end facts amend parked usage through the coordinator.

Calculated cost is read-time arithmetic against a time-indexed price table, so
price fixes heal historical display without rewriting usage facts. Provider-
reported cost is stored separately and remains the provider headline value when
present; divergence is visible as specified in Section 38.17.

---

## 22. Configurable backends and execution targets

### 22.1 Backend

A Backend is stored configuration describing a place where a provider may run
or be observed. The frontend chooses an ExecutionTarget. SQLite stores that
choice and its configuration. Merely loading configuration at daemon startup
does not connect, launch, authenticate, mount, or probe anything.

```text
backends
  id
  label
  adapter_id
  endpoint/config_ref
  trust_class
  enabled
  last_health
```

An active Backend adapter is invoked only by an explicit application operation:
target probe, provider start/resume/control, workspace query/transfer, artifact
read, or liveness reconciliation. It returns observed reachability and
capabilities separately from stored configuration. The frontend and core do not
bake in one host, tunnel, or filesystem topology.

### 22.2 ExecutionTarget

An ExecutionTarget is a persisted, frontend-selectable provider placement:

```text
execution_targets
  id
  label
  backend_id
  provider_id
  default_mode
  workspace_root_ref
  provider_config
  enabled
  revision
```

It never contains an account, model, or effort; those are per-start/runtime
choices. A Backend stores connection/location configuration. An ExecutionTarget
chooses one provider on that Backend. `RuntimeRequest.execution_target_id`
therefore determines provider/backend and does not repeat them. The application
rejects a separately supplied provider/backend field as unknown input.

### 22.3 Remote backends

Do not distribute the canonical database initially. A remote adapter can
launch/observe processes, frame Observations to the controller while connected,
expose terminal/runtime capabilities, and transfer selected workspace
artifacts.

Remote disconnection creates explicit liveness/control uncertainty, and
transient remote Observations may be lost. V4 remote execution is deliberately
online-only and uses the Section 38.33 protocol. It has no remote canonical
store, offline autonomous decisions, spool, or replay. Those would require a
new architecture version and are not an unspecified future v4 feature.

---

## 23. Cross-provider handover

### 23.1 Definition

Cross-provider handover creates a new target AgentSession attached to the same
Conversation and bootstrapped from a versioned provider-neutral snapshot of one
semantic head and relevant work state.

It is not provider mutation, raw transcript conversion, process transfer,
hidden-state transfer, credential transfer, or command replay.

### 23.2 Workflow

`Operation(kind=handover)` owns the saga:

1. **Validate and settle** — target capability, authority, source head,
   workspace, interactions, and in-flight non-portable work.
2. **Snapshot** — immutable Conversation revision/head, live ancestry,
   trustworthy ContextCheckpoint, workspace revision/fingerprint, open/unknown
   Operations, and retention/redaction policy.
3. **Compile** — objective, constraints, decisions, open questions, older
   summary, recent dialogue, tasks, work ledger, Resources, workspace state,
   omissions, and provenance.
4. **Budget** — select one branch, use checkpoint coverage to avoid duplicate
   context, summarize older material, preserve recent tail, and encode
   Resources through target capability.
5. **Prepare workspace** — verify same workspace or transfer revision, dirty
   diff, required untracked files, and artifacts.
6. **Create AgentSession** — create a fresh target provider identity in
   `starting` state.
7. **Deliver** — use native foreign import, structured history, MCP Resource,
   bootstrap file, structured input, or compact prompt in that order of
   truthful capability.
8. **Acknowledge** — validate structured target understanding and workspace
   revision where supported.
9. **Activate** — mark target active, source idle/resumable, retain the same
   Conversation head, and append the first genuine target Node under it.

The bootstrap is infrastructure input, not a fake human Node.

### 23.3 Transfer boundary

Transfers:

- selected semantic branch;
- decisions, constraints, questions, goal, and plan;
- changed files/diffs and selected Resources;
- relevant completed/open Operation facts;
- test/build summaries and output references;
- workspace revision/fingerprint; and
- explicit uncertainty and omissions.

Does not transfer:

- hidden reasoning;
- prompt cache or provider compaction internals;
- credentials or approvals;
- PIDs, monitors, shell-local state, or terminal input;
- native tool IDs as target-native IDs; or
- provider-specific subagent runtime state.

If the source changes after snapshot, mark divergence and offer a delta or
separate branch. If target delivery fails, the source remains intact and no
Conversation head moves.

---

## 24. Extensions and collaboration

### 24.1 Plugin trust and manifest

Initial plugins are trusted in-process Python packages. Installation is an
administrative action. A manifest declares stable ID/version, capability
implementations, protocol compatibility, configuration schema, Observation
schemas, Operation kinds, migrations/detail tables, surface contributions,
and permissions.

Untrusted marketplace plugins use the mandatory subprocess/RPC and permission
model in Section 38.33. Implementation is phased, but the boundary is not left
for the implementor to invent.

### 24.2 Extension rules

1. Core fields remain provider-neutral.
2. Extension data is namespaced and version-validated.
3. Indexed extension state uses an owned detail table.
4. Plugins never mutate another module's tables directly.
5. Cross-module changes use public application services.
6. Arbitrary EAV is not the default.
7. Surface extensions provide typed data, never unsanitized HTML.
8. Provider-name literals are absent from core and generic surfaces except
   declared registries.

### 24.3 Collaboration

Collaboration is a first-party module over stable Conversation and
AgentSession IDs:

```text
peer_messages
  id
  from_conversation_id
  from_agent_session_id
  to_conversation_id
  to_agent_session_id       nullable
  external_message_id       nullable
  body_ref
  state                     pending | sent | delivered | read | failed |
                            unknown
  reply_to_id               nullable
  created_at

work_claims
  id
  conversation_id
  resource
  expires_at
  state
```

Peer content is untrusted, cannot approve permissions, is never auto-executed,
is quota/loop limited, and is visible/auditable. Broadcast state is tracked per
recipient. One child/peer can receive several distinct task assignments.

Actor remains a value until it earns independent identity, profiles,
permissions, mailboxes, and lifecycle beyond AgentSession/task keys.

---

## 25. Security boundaries

### 25.1 Threat model

Provider and terminal controls can execute code. Protect HTTP/MCP mutation,
ingestion sockets, backend/provider credentials, terminal input, blobs,
uploads, plugins, and handover inputs.

### 25.2 HTTP and MCP

- bind loopback by default;
- use authenticated edge/proxy identity for remote exposure;
- retain application credentials as defense in depth;
- use secure HttpOnly SameSite browser cookies;
- enforce origin/custom-header/JSON CSRF protections;
- do not enable permissive CORS;
- authorize every mutation by principal and capability;
- support a read-only deployment mode; and
- distinguish human-principal controls from model/peer capabilities.

The machine setting `control_plane.read_only` (including imported
`config.READONLY`) blocks every endpoint, MCP tool, repair, provider-edge
installer, terminal write, and external effect that can mutate product or
provider state with `403 control_plane_read_only`. Observation ingestion,
projection maintenance required to make those observations readable, health,
backups, and all authorized reads remain enabled. The setting is returned by
health/config DTOs and checked again by the outbox worker so work queued before
the switch cannot execute afterward.

### 25.3 Ingestion socket

Filesystem permissions alone do not distinguish same-user processes. Use
per-edge identity/secret where possible, peer credentials and PID ancestry as
provenance, source-kind allowlists, daemon-minted-only presence/control
receipts, and anomaly detection for implausible source/provider combinations.

A compromised same-user process may remain able to forge some payloads. The
architecture makes forgery attributable/detectable where possible and never
treats the ingestion socket as authenticated human presence.

### 25.4 Handover and Resources

Handover redacts known secrets, excludes credentials/approvals, includes only
selected Resources, labels provenance, and requires target revalidation.
Cross-backend transfer requires explicit authority.

Uploads and Resources enforce size/media/path/trust boundaries. Active content
is downloaded safely rather than rendered directly. A handover bundle is
context, never authorization.

### 25.5 Credentials and plugins

Credential ports return narrow typed results and never expose secrets to
surfaces or generic mappers. Refresh/writeback is leased and audited without
logging material.

Trusted in-process plugins have daemon authority. Their manifests disclose
filesystem/network/binary/credential/control needs. Third-party distribution
is deferred until isolation exists.

---

## 26. Crash recovery and degradation

### 26.1 Startup recovery

On daemon startup:

1. acquire single-instance/supervisor ownership;
2. open/validate SQLite and migrations;
3. recover or report database health;
4. scan open Stream staging files and discard torn final frames;
5. reload pending/quarantined Observations;
6. recover expired outbox leases;
7. reload open Operations, arms, workflows, and notifications;
8. restart durable source-reader registrations and probes;
9. reconcile active AgentSessions and terminal bindings;
10. publish health/degradation revisions.

Coordinators are started lazily for active/open work rather than for every
archived Conversation.

### 26.2 Failure cases

**Source outage:** record degraded freshness and stop claiming confident live
state after the declared horizon.

**Lost closer:** probe or later history can resolve it; otherwise mark
`lost`/`unknown`. Quiet time cannot manufacture success.

**Stream crash:** recover complete frames, reconcile with provider final if
available, continue from cursor when possible, otherwise preserve partial and
mark aborted/lost.

**Database failure:** hooks pass through, state-changing APIs refuse, transient
Observations may be lost, and health is loud.

**Daemon unavailable:** this is an accepted product-level availability
boundary. All Baqylau behavior may stop: ingestion, audit capture, tab paint,
pane updates, HTTP/SSE, alerts, controls, and recovery evidence. Provider edge
programs must still return their provider-safe pass-through result within their
deadline. They do not spool, replay, paint tabs independently, write fallback
databases, or implement a second reduced Baqylau. Evidence generated during the
outage may be permanently absent. After restart, adapters reconcile only from
provider artifacts and other sources that still exist; the UI labels the gap
instead of implying complete capture.

**Coordinator/plugin failure:** supervise independently; quarantine the
Observation, restart/park the affected Conversation, and leave siblings live.

**Remote disconnect:** retain last freshness, mark controls/liveness uncertain,
accept that transient remote evidence may be lost, and reconcile current state
on reconnect.

### 26.3 Supervisor contract

The supported desktop deployment uses a concrete OS supervisor: `launchd` on
macOS and a user `systemd` unit on Linux. Packaging installs exactly one of
those units. Both start the daemon at login, restart it after an abnormal exit,
use a 1-second initial delay with exponential backoff capped at 60 seconds,
enter a visible crash-loop state after 10 exits in 10 minutes, and expose the
same `baqylau daemon status|start|stop|restart|logs` commands. There is no
edge-side fallback service. Socket activation is not used in v4.

The visible crash-loop state is a machine health banner served from the last
readable snapshot (or the static client shell when no daemon response is
possible). It shows the boot ID, exit count/window, last error reference, and
the exact recovery commands. `baqylau daemon logs` reads supervisor logs
without the daemon; `baqylau daemon status --offline` reads SQLite health and
the latest `ingestion_gap`. No provider action is claimed successful while the
daemon is unavailable.

An upgrade performs: stop accepting new mutations; finish or abort within the
10-second shutdown deadline; take the SQLite online backup and blob manifest;
install code and provider-edge files; run schema migration; run edge trust and
configuration verification; start the daemon; wait up to 30 seconds for the
health endpoint; and revert code plus any rollback-safe migration if health
does not become ready. Log, unit-file, socket, database, blob, backup, and edge
installation paths are platform configuration values printed by
`baqylau daemon status`.

Safe shutdown stops accepting mutations, drains short transactions, persists
source cursors and open Stream frames, releases or shortens leases, and exits
before the supervisor deadline. Provider hooks retain pass-through behavior.

---

## 27. Performance design

### 27.1 Principles

1. Bulk bytes bypass metadata fan-out.
2. SQLite transactions remain short and contain no external I/O.
3. Source deltas are coalesced.
4. Maintenance and bulk-derived metadata writes use bounded batches so no
   transaction monopolizes the writer.
5. Answerable hooks use a fixed timeout and safe pass-through; they do not
   rely on special database scheduling.
6. Reads use indexed shapes and scoped rebuilds, not history replay.
7. Slow clients resync rather than backpressure ingestion.
8. Performance gates measure competing workloads together.

### 27.2 Required indexes

At minimum:

- Node by Conversation/parent/source identity/position;
- AgentSession alias by provider/backend/external identity and validity;
- Operation by Conversation, AgentSession, state, kind, task/turn/actor key;
- activity links by both endpoints and relation;
- materialized Activity by Conversation/head/generation/local sequence;
- open Stream and Operation by owner/state;
- Observation dedup/source cursor/processing state;
- outbox by state/priority/available time;
- projection by source revision;
- Resource by backend/workspace/URI/version;
- InputBuffer/preference by scope/revision/author sequence;
- usage by ledger/source/dedup/account/session;
- notifications/arms by state/due time; and
- provenance by entity/Observation.

### 27.3 Compound benchmarks

The acceptance workload combines:

1. one Conversation streaming a large build log;
2. another transitioning to asking/executing;
3. web and pane live clients;
4. concurrent assistant streaming;
5. an answerable foreground-command hook; and
6. a control gesture.

Measure:

- Observation-to-attention p50/p95/p99;
- answerable response latency and timeout/pass-through rate against the
  provider deadline;
- SQLite transaction duration, lock waits, and busy failures;
- tab paint verified completion;
- assistant Stream display latency;
- control acceptance/completion;
- client delta drops/resyncs;
- CPU, memory, and disk throughput;
- daemon-restart catch-up; and
- cross-Conversation latency interference.

Also benchmark a long Activity backlog with late child results, alternate
branches, several AgentSessions, Resources, and whole-block pagination. The
ActivityComposer must not perform an O(nodes × operations) merge.

Pass thresholds are written before production migration.

### 27.4 Post-v4 database change

PostgreSQL is not a v4 corrective action. Multiple controller writers,
multi-user shared deployment, remote shared storage, or HA may justify a future
architecture version and ADR, but that version is not allowed to satisfy or
waive a v4 SQLite acceptance failure.

---

## 28. Superseded relational inventory

The SQL below is retained only as review history and vocabulary provenance. It
must not be executed or copied into the implementation. The authoritative
dependency-ordered clean install is the five-unit schema in Sections 38.35,
38.39, and 40.7, whose digest and executable checks supersede this early
fragment.

```sql
CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT,
  head_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
  active_agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  project_ref TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  archived_at REAL
);

CREATE TABLE backends (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  endpoint_config_ref TEXT NOT NULL,
  trust_class TEXT NOT NULL,
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);

CREATE TABLE execution_targets (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  provider_id TEXT NOT NULL,
  default_mode TEXT NOT NULL CHECK(default_mode IN
    ('interactive','headless','sdk','server','remote')),
  workspace_root_ref TEXT,
  provider_config TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(provider_config)),
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(backend_id, provider_id, label)
);

CREATE TABLE agent_sessions (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  provider_id TEXT NOT NULL,
  execution_target_id TEXT NOT NULL
    REFERENCES execution_targets(id) ON DELETE RESTRICT,
  backend_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  state TEXT NOT NULL,
  resumable INTEGER NOT NULL,
  persistence_kind TEXT NOT NULL,
  source_ref TEXT,
  started_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  ended_at REAL,
  end_reason TEXT,
  UNIQUE(id, conversation_id)
);

CREATE TABLE conversation_actor_tracks (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  actor_key TEXT NOT NULL,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  parent_track_id TEXT REFERENCES conversation_actor_tracks(id) ON DELETE SET NULL,
  lifecycle_operation_id TEXT REFERENCES operations(id) ON DELETE SET NULL,
  track_kind TEXT NOT NULL CHECK(track_kind IN
    ('lead','subagent','teammate','sidecar','peer')),
  state TEXT NOT NULL CHECK(state IN ('active','idle','ended','lost')),
  head_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  ended_at REAL,
  UNIQUE(id, conversation_id),
  UNIQUE(conversation_id, actor_key),
  CHECK((track_kind = 'lead') = (actor_key = 'baqylau:lead')),
  CHECK((state IN ('ended','lost')) = (ended_at IS NOT NULL))
);
CREATE UNIQUE INDEX one_lead_track_per_conversation
  ON conversation_actor_tracks(conversation_id)
  WHERE track_kind = 'lead';
CREATE INDEX actor_tracks_list
  ON conversation_actor_tracks(conversation_id, state, created_at, id);

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  actor_track_id TEXT NOT NULL REFERENCES conversation_actor_tracks(id),
  parent_id TEXT REFERENCES nodes(id),
  agent_session_id TEXT REFERENCES agent_sessions(id),
  role TEXT NOT NULL,
  semantic_kind TEXT NOT NULL,
  origin TEXT NOT NULL,
  state TEXT NOT NULL,
  source_external_id TEXT,
  source_position TEXT,
  turn_key TEXT,
  actor_key TEXT,
  branch_visibility TEXT NOT NULL DEFAULT 'normal'
    CHECK(branch_visibility IN ('normal','suspect_retracted','superseded')),
  branch_evidence_revision INTEGER NOT NULL DEFAULT 0
    CHECK(branch_evidence_revision >= 0),
  completion_reason TEXT,
  source_timestamp REAL,
  created_at REAL NOT NULL,
  committed_at REAL,
  UNIQUE(id, actor_track_id)
);
CREATE INDEX nodes_track_parent ON nodes(actor_track_id, parent_id);

CREATE TABLE operations (
  id TEXT PRIMARY KEY,
  conversation_id TEXT REFERENCES conversations(id),
  subject_type TEXT,
  subject_id TEXT,
  agent_session_id TEXT REFERENCES agent_sessions(id),
  anchor_node_id TEXT REFERENCES nodes(id),
  parent_operation_id TEXT REFERENCES operations(id),
  turn_key TEXT,
  task_key TEXT,
  actor_key TEXT,
  source_position TEXT,
  native_operation_key TEXT,
  kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('pending','running','succeeded','failed','cancelled','denied','abandoned',
     'lost','unknown')),
  origin TEXT NOT NULL,
  opener_state TEXT NOT NULL DEFAULT 'present'
    CHECK(opener_state IN ('present','missing','unknown')),
  schema_version TEXT NOT NULL,
  data TEXT NOT NULL,
  result_ref TEXT,
  source_timestamp REAL,
  started_at REAL NOT NULL,
  ended_at REAL,
  CHECK(
    (conversation_id IS NOT NULL AND subject_type IS NULL AND subject_id IS NULL)
    OR
    (conversation_id IS NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL)
  )
);
CREATE UNIQUE INDEX operations_native_key
  ON operations(agent_session_id, kind, native_operation_key)
  WHERE native_operation_key IS NOT NULL;

CREATE TRIGGER track_scope_insert
BEFORE INSERT ON conversation_actor_tracks
BEGIN
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'track_agent_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.parent_track_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks p
    WHERE p.id = NEW.parent_track_id
      AND p.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'parent_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.lifecycle_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.lifecycle_operation_id
      AND o.conversation_id = NEW.conversation_id
      AND o.kind = 'agent_task')
  THEN RAISE(ABORT, 'track_lifecycle_operation_mismatch') END;
  SELECT CASE WHEN NEW.head_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.id
      AND n.state = 'committed')
  THEN RAISE(ABORT, 'actor_track_head_not_committed_member') END;
END;

CREATE TRIGGER track_scope_update
BEFORE UPDATE OF conversation_id, agent_session_id, parent_track_id,
                 lifecycle_operation_id, head_node_id
ON conversation_actor_tracks
BEGIN
  SELECT CASE WHEN NEW.conversation_id <> OLD.conversation_id
  THEN RAISE(ABORT, 'actor_track_conversation_is_immutable') END;
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'track_agent_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.parent_track_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks p
    WHERE p.id = NEW.parent_track_id
      AND p.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'parent_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.lifecycle_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.lifecycle_operation_id
      AND o.conversation_id = NEW.conversation_id
      AND o.kind = 'agent_task')
  THEN RAISE(ABORT, 'track_lifecycle_operation_mismatch') END;
  SELECT CASE WHEN NEW.head_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.id
      AND n.state = 'committed')
  THEN RAISE(ABORT, 'actor_track_head_not_committed_member') END;
END;

CREATE TRIGGER agent_session_target_match
BEFORE INSERT ON agent_sessions BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM execution_targets t
    WHERE t.id = NEW.execution_target_id
      AND t.provider_id = NEW.provider_id
      AND t.backend_id = NEW.backend_id)
  THEN RAISE(ABORT, 'agent_session_target_mismatch') END;
END;

CREATE TRIGGER agent_session_target_match_update
BEFORE UPDATE OF provider_id, backend_id, execution_target_id
ON agent_sessions BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM execution_targets t
    WHERE t.id = NEW.execution_target_id
      AND t.provider_id = NEW.provider_id
      AND t.backend_id = NEW.backend_id)
  THEN RAISE(ABORT, 'agent_session_target_mismatch') END;
END;

CREATE TRIGGER node_scope_insert
BEFORE INSERT ON nodes
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'node_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.parent_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes p
    WHERE p.id = NEW.parent_id
      AND p.actor_track_id = NEW.actor_track_id)
  THEN RAISE(ABORT, 'node_parent_track_mismatch') END;
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'node_agent_session_scope_mismatch') END;
END;

CREATE TRIGGER node_scope_update
BEFORE UPDATE OF conversation_id, actor_track_id, parent_id, agent_session_id
ON nodes BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'node_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.parent_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes p
    WHERE p.id = NEW.parent_id
      AND p.actor_track_id = NEW.actor_track_id)
  THEN RAISE(ABORT, 'node_parent_track_mismatch') END;
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'node_agent_session_scope_mismatch') END;
END;

CREATE TRIGGER operation_scope_insert
BEFORE INSERT ON operations BEGIN
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_agent_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.anchor_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.anchor_node_id
      AND n.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_anchor_scope_mismatch') END;
  SELECT CASE WHEN NEW.parent_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations p
    WHERE p.id = NEW.parent_operation_id
      AND p.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_parent_scope_mismatch') END;
END;

CREATE TRIGGER operation_scope_update
BEFORE UPDATE OF conversation_id, agent_session_id, anchor_node_id,
                 parent_operation_id
ON operations BEGIN
  SELECT CASE WHEN NEW.conversation_id <> OLD.conversation_id
  THEN RAISE(ABORT, 'operation_conversation_is_immutable') END;
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_agent_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.anchor_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.anchor_node_id
      AND n.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_anchor_scope_mismatch') END;
  SELECT CASE WHEN NEW.parent_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations p
    WHERE p.id = NEW.parent_operation_id
      AND p.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_parent_scope_mismatch') END;
END;

CREATE TRIGGER actor_track_head_update
BEFORE UPDATE OF head_node_id ON conversation_actor_tracks
WHEN NEW.head_node_id IS NOT NULL
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.id
      AND n.state = 'committed')
  THEN RAISE(ABORT, 'actor_track_head_not_committed_member') END;
END;

CREATE TRIGGER lead_track_head_projection
AFTER UPDATE OF head_node_id ON conversation_actor_tracks
WHEN NEW.track_kind = 'lead'
BEGIN
  UPDATE conversations
  SET head_node_id = NEW.head_node_id,
      revision = revision + 1,
      updated_at = unixepoch('subsec')
  WHERE id = NEW.conversation_id;
END;

CREATE TRIGGER conversation_head_is_derived
BEFORE UPDATE OF head_node_id ON conversations
WHEN NEW.head_node_id IS NOT (
  SELECT head_node_id FROM conversation_actor_tracks
  WHERE conversation_id = NEW.id AND track_kind = 'lead')
BEGIN
  SELECT RAISE(ABORT, 'conversation_head_must_equal_lead_track');
END;

CREATE TRIGGER conversation_active_session_scope
BEFORE UPDATE OF active_agent_session_id ON conversations
WHEN NEW.active_agent_session_id IS NOT NULL
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.active_agent_session_id
      AND s.conversation_id = NEW.id)
  THEN RAISE(ABORT, 'active_agent_session_scope_mismatch') END;
END;

CREATE TABLE streams (
  id TEXT PRIMARY KEY,
  owner_type TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  ordinal INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  mode TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  byte_length INTEGER NOT NULL DEFAULT 0,
  staging_ref TEXT,
  final_ref TEXT,
  retention_class TEXT NOT NULL,
  media_type TEXT,
  render_kind TEXT NOT NULL DEFAULT 'plain'
    CHECK(render_kind IN ('plain','markdown','json','yaml','source','extension')),
  language TEXT,
  render_detection_source TEXT
    CHECK(render_detection_source IS NULL OR render_detection_source IN
      ('raw_command','provider_metadata','explicit','fallback')),
  visible_copy_ref TEXT,
  raw_copy_state TEXT NOT NULL DEFAULT 'never_captured'
    CHECK(raw_copy_state IN ('available','expired','never_captured')),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  sealed_at REAL,
  UNIQUE(owner_type, owner_id, channel, ordinal)
);

CREATE TABLE node_parts (
  node_id TEXT NOT NULL REFERENCES nodes(id),
  ordinal INTEGER NOT NULL,
  kind TEXT NOT NULL,
  media_type TEXT,
  content_ref TEXT,
  stream_id TEXT REFERENCES streams(id),
  resource_id TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY(node_id, ordinal)
);

CREATE TABLE activity_links (
  from_type TEXT NOT NULL,
  from_id TEXT NOT NULL,
  to_type TEXT NOT NULL,
  to_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  provenance_id TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY(from_type, from_id, to_type, to_id, relation)
);
```

Supporting migrations define Observations/provenance, attempts/aliases,
native-record index, workspaces, title facts, context checkpoints, Resources,
interaction details, InputBuffers/preferences, slot allocations, usage/quotas,
notifications, Activity, arms, outbox/effect attempts, and repair records.

Polymorphic Stream/link ownership is validated by registered application
services and optionally closed-type SQLite triggers. Plugins never write core
tables directly.

JSON discipline:

- validate by kind and schema version;
- never store unbounded output bytes;
- index core identities in columns;
- promote frequently queried fields to detail tables;
- preserve compatible additive extension fields; and
- never use canonical JSON as a substitute for retained raw evidence.

---

## 29. Python package structure

The rewrite uses this source layout:

```text
src/
  baqylau/
    domain/
      ids.py
      conversation.py
      node.py
      agent_session.py
      operation.py
      stream.py
      capabilities.py
      rules.py

    application/
      ports/
        storage.py
        providers.py
        terminals.py
        backends.py
        alerts.py
        credentials.py
        delivery.py
        extensions.py
        backups.py
        repairs.py
        relay.py

      conversations/
        coordinator.py
        commands.py
        queries.py

      ingestion/
        service.py
        inbox.py
        provenance.py
        answerable.py

      activity/
        composer.py
        projection.py
        queries.py

      effects/
        service.py
        outbox.py
        arms.py

      machine/
        accounts.py
        attention.py
        discovery.py
        windows.py
        presence.py
        maintenance.py

      auth/
        service.py
        authorization.py
        sessions.py
        certificates.py

      diagnostics/
        anomaly_catalog.py
        schema_catalog.py
        service.py

      actor_tracks/
        commands.py
        queries.py
        projection.py

      notifications/
        policy.py
        routing.py
        delivery.py

      usage/
        accounting.py
        queries.py

      migration/
        accounts.py
        legacy_parked.py

      session_facets/
        service.py
        context.py
        titles.py
        tasks.py
        artifacts.py

      provider_edges/
        manager.py

      rewind/
        service.py

      tui_drafts/
        service.py

      collaboration/
        service.py
        delivery.py

      backups/
        service.py

      repairs/
        registry.py
        service.py

      backends/
        service.py
        remote_protocol.py

      extensions/
        service.py

      controls.py
      interactions.py
      resources.py
      input_buffers.py
      handover.py

    adapters/
      providers/
        claude_code/
        codex/
        opencode/

      terminals/
        kitty/
        none/

      storage/
        sqlite/
          connection.py
          schema.sql
          migrations.py
          unit_of_work.py
          stores/
        blob_files/
        stream_files/

      backends/
        local/
        remote/

      relay/
        provider_api/

      alerts/
      credentials/
      delivery/

      extensions/
        subprocess_host/

      observations/
        hooks/
        watchers/
        probers/

    entrypoints/
      edge_socket/
      http/
      mcp/
      cli/

    presentation/
      blocks.py
      activity.py

    extensions/
      registry.py

    runtime/
      supervisor.py
      observation_workers.py
      source_readers.py
      outbox_workers.py
      effect_reconciler.py
      alert_workers.py
      feed_workers.py
      projection_workers.py
      blob_gc.py
      stream_recovery.py
      saga_workers.py
      backup_workers.py
      database_maintenance.py
      provider_edge_verifier.py
      slot_reaper.py
      retention_workers.py
      remote_connection_workers.py
      plugin_supervisor.py

    bootstrap.py
    main.py

clients/
  web/
    package.json
    src/
      api/
      sse/
      state/
      views/
      controls/
      notifications/
      extensions/
      auth/
    tests/

  pane_host/
    pyproject.toml
    src/
      baqylau_pane/
        client.py
        renderer.py
        viewport.py
        open_actions.py
        controls.py
    tests/

docs/
  operations/
    audit-debug.md
    global-errors.md

tests/
  unit/
    domain/
    application/

  contract/
    providers/
    terminals/
    storage/
    backends/
    delivery/
    extensions/
    auth/

  integration/
  end_to_end/
  diagnostics/
    fixtures/
```

This layout is required, not illustrative. Every Python package directory has
an `__init__.py`. New top-level source directories require an architecture
decision that updates this section.

Ownership is explicit:

- `domain` contains provider-independent values and pure rules;
- `application` contains use cases, coordinators, authorization policy, and the
  interfaces it needs from outside systems;
- `adapters` contains provider, terminal, storage, backend, relay, delivery,
  extension-host, alert, credential, hook, watcher, and prober implementations;
- `entrypoints` contains the protocols through which callers enter the daemon;
- `presentation` converts application results into surface-neutral display
  blocks;
- `clients/web` is the browser implementation and owns snapshot/SSE state,
  view rendering, browser controls, toasts, Web Push registration, and browser
  drafts;
- `clients/pane_host` is the terminal mirror client and owns block painting,
  viewport restoration, OSC 8 actions, and pane gestures;
- `runtime` contains process supervision only; and
- `bootstrap.py` is the only module that connects concrete adapters to the
  application.

Imports obey these rules:

```text
domain      -> domain only
application -> domain and application ports
adapters    -> domain and application ports
entrypoints -> application public interfaces
presentation -> domain and application result types
bootstrap   -> may import all packages to connect the process
```

The `services`, `infrastructure`, global `projections`, and `transport`
directories are forbidden. Their names hide ownership or separate code from
the feature that owns it. A projection stays beside its application feature.
SSE belongs to the HTTP entrypoint. SQLite-specific code stays under
`adapters/storage/sqlite`.

This structure assigns locations to features present in this design. It does
not make those features mandatory if a later design decision removes them.

Use Pydantic at protocol/config/plugin boundaries. Domain values can use
dataclasses/enums. Database rows, domain values, mutation proposals, and API
DTOs are distinct types.

---

## 30. Testing strategy

### 30.1 Architecture tests

- import direction;
- no provider/terminal literals in generic domain/surfaces;
- capability manifest completeness;
- plugin schema registration;
- no direct cross-module table mutation;
- no external I/O in transactions;
- null/headless substitutability; and
- all catch-and-swallow boundaries record evidence.

### 30.2 Mapper fixtures

Every provider has fixtures of:

```text
raw Observation sequence
  -> expected Nodes and native-record index
  -> expected AgentSession identity/attempt/aliases
  -> expected Operations and links
  -> expected Stream frames/final authority
  -> expected supporting facts
  -> expected provenance decisions
```

Port measured transcripts, rollouts, hook payloads, source files, and historical
bug cases verbatim. Do not reconstruct tricky semantics from memory.

### 30.3 Domain and property tests

- Node tree/head invariants;
- committed Node immutability;
- rewind/fork and stale-head divergence;
- branch-sensitive versus cumulative facts;
- AgentSession resume/fork/alias repair;
- Operation transition tables and matched closers;
- duplicate ingestion and atomic-take correlations;
- source-reader cursor/truncation/drain gates;
- Stream framing/torn-write/reconciliation/transfer;
- multi-part Nodes and multi-channel Streams;
- provider-local and causal Activity ordering;
- child contribution before consuming answer;
- repeated tasks by one child stay distinct;
- ContextCheckpoint coverage;
- message delivery cannot commit early;
- interaction stale-card refusal;
- InputBuffer tombstone/author races;
- outbox lease/idempotent/indeterminate cases;
- notification restart/retraction semantics; and
- handover snapshot immutability/divergence.

### 30.4 Projection and presentation tests

- attention precedence/actor filtering;
- title/tasks/goal/context/compaction;
- usage temporality, ledger separation, and pricing time;
- supported-empty-unknown-unsupported facets;
- projection source revision/staleness;
- Activity backlog/live/amendment equivalence;
- whole-block pagination;
- scoped actor views;
- semantic PresentationBlocks;
- terminal/web sanitization and width reflow; and
- health/error flood/suppression rules.

### 30.5 Adapter contracts

- provider discovery and identity ownership;
- RuntimeDriver semantic actions and dynamic refusals;
- capability differences by mode/version;
- prepare-then-answer rollback/pass-through/deadline;
- foreground tee/reporter stdout/stderr/exit-code fidelity;
- attachment encoding and realpath jail;
- source cursor/inode/truncation behavior;
- terminal role protocols and binding verification;
- backend disconnect/reconnect uncertainty;
- credential refresh/writeback lease;
- account/capability enumeration; and
- handover delivery/acknowledgement.

### 30.6 End-to-end scenarios

- interactive Claude with terminal;
- headless Claude streaming;
- interactive and programmatic Codex;
- OpenCode server mode;
- daemon killed mid-Stream and mid-effect;
- daemon unavailable during hook;
- answerable hook under SQLite contention and prepare failure;
- foreground/background/monitor/subagent closer matrix;
- foreground-to-background ownership transfer;
- malformed Observation quarantine;
- same-provider resume and account migration crash recovery;
- cross-provider handover and source divergence;
- simultaneous providers diverging from one head;
- large build flood plus unrelated asking/control latency;
- web reconnect with open Streams and activity amendments;
- pane resize/reflow/sanitize and stale-binding refusal;
- shared draft device/terminal race;
- queued/unknown/eventually-delivered message;
- interactions and stale responses;
- rewind conversation/workspace/both with partial failure;
- Resource upload/expand/copy/expiry;
- child/sidecar/repeated-task activity ordering;
- notification escalation/retraction across restart; and
- null-terminal execution.

### 30.7 Behavioral parity

During migration, old and new paths consume the same live traffic/fixtures and
compare:

- identity/start/end/resume/adoption;
- semantic live branch and compaction;
- command/tool/file summaries and raw copy sources;
- agent/subagent/sidecar activity and causal order;
- attention/tab output;
- usage/context/tasks/title/model/effort/account limits;
- terminal and web blocks/backlog/live pagination;
- drafts/preferences/attachments;
- queued and optimistic delivery state;
- interactions and controls;
- presence/alerts/retraction;
- account migration;
- audit explanations and failure visibility; and
- headless/provider-programmatic behavior.

Every difference requires an explicit product decision.

---

## 31. Migration strategy

Use a strangler migration. Existing behavior remains authoritative until each
plane passes its gate.

### Phase 0 — inventory and benchmarks

- Map every existing feature, environment gate, and measured lesson to a new
  owner or explicit drop.
- The legacy global tab registry (`/tmp/claude-kitty-tab.db`) is explicitly
  inventoried: tab colors and watcher locks map to `terminal_bindings` and
  `tab_paint_*`; `sids` maps to AgentSession/provider identity; and
  `adopt_pending` maps to `session_adoption_notes`. No legacy registry row may
  be left without one of these owners or a recorded drop decision.
- Freeze the fixture corpus.
- Define compound performance thresholds.
- Decide retention, backup, supervisor, install, and rollback contracts.

### Phase 1 — daemon and evidence foundation

- supervisor/composition root;
- SQLite migrations and storage adapter;
- Observation inbox, provenance, quarantine, and health;
- Blob/Stream staging and recovery;
- Resource/InputBuffer/preference stores;
- answerable transport in pass-through mode;
- provider Observation capture;
- no production external effects.

Gate: accepted Observation durability across restart, poison isolation, and
performance baseline. Observations that never reach the daemon are outside
this guarantee.

### Phase 2 — canonical read model

- Conversation/Node/AgentSession/Operation mapping for one provider;
- native record index, aliases, attempts, parts, links, checkpoints;
- ActivityComposer and snapshot API;
- read-only surface compatibility;
- branch/head/title parity.

Gate: live and parked history parity including late records, child scope,
interactions, compaction, and whole-block pagination.

### Phase 3 — streaming and coordination

- Conversation coordinator lifecycle;
- provisional Node Streams;
- command output Streams and source readers;
- prepare-then-answer foreground capture;
- closer/prober matrix;
- structural and Stream feeds;
- client resync and pane/web rendering.

Gate: fidelity, recovery, sanitization, resize, and compound-load latency.

### Phase 4 — projections and machine services

- attention and alerts;
- account/window/discovery/presence services;
- usage/context/tasks/title/stats/health;
- slot allocations and audience facts;
- Resource/memory/input-buffer views;
- capability absence semantics.

Gate: sustained old/new observable agreement with explained differences.

### Phase 5 — controls and effects

- transactional outbox and attempts;
- RuntimeDriver controls;
- message delivery and interaction workflows;
- rewind two-plane workflow;
- notification intents/deliveries;
- terminal/pane controls;
- programmatic no-terminal controls;
- capability-driven UI.

Every exclusive effect plane has one durable owner/lease during coexistence.

### Phase 6 — providers, backends, and accounts

- additional provider modes;
- OpenCode/programmatic adapters;
- backend/account configuration;
- credential port and quota polling;
- same-provider account migration;
- provider contract proof without generic core/surface edits.

### Phase 7 — handover and collaboration

- handover compiler/package/workspace verification;
- target delivery/acknowledgement/divergence;
- peer-message and task delivery foundations.

These are not on the initial replacement critical path unless product priority
changes.

### Phase 8 — retire old planes

Deletion follows disablement. Preserve import/read compatibility for archived
history through a declared sunset. A plane is replaceable only with a named
owner, evidence path, mapper/domain/presenter fixtures, adapter tests, crash and
uncertainty behavior, performance threshold, security review, accepted
differences, and operational rollback.

---

## 32. Implementation order

The first vertical slice proves the architecture with minimal breadth:

1. SQLite migrations, semantic storage ports, and the SQLite adapter.
2. Observation inbox, provenance, quarantine, and health.
3. Blob/Resource/Stream staging and torn-frame recovery.
4. Conversation, Node/parts, AgentSession/attempt/aliases, Operation, links.
5. Conversation coordinator and one provider decoder/history subset.
6. Native record positions and one ActivityComposer backlog.
7. Snapshot showing committed/provisional Nodes and Operations.
8. One semantic assistant Stream.
9. Prepare-then-answer plus one command-output Stream.
10. Structural amendments and Stream resync.
11. Attention projection and one machine watcher.
12. InputBuffer and provider-confirmed message delivery.
13. Null/headless RuntimeDriver.
14. One verified terminal adapter and pane proof.

Do not begin with handover, all legacy features, PostgreSQL, third-party plugin
loading, edge-language optimization, or a full surface rewrite.

First prove tree/head correctness, Activity order, durable mapping, Stream
recovery, synchronous fallback, mixed-load SQLite performance, provider/
terminal independence, and crash isolation.

---

## 33. Architectural laws

These rules are normative:

1. A Conversation is not a provider session, process, account, terminal, or
   partition.
2. A Node is semantic conversation content; an Operation is work.
3. Dialogue ancestry, work containment, causal contribution, and runtime
   lineage are distinct.
4. Native provider siblings do not automatically form semantic branches.
5. Only committed Nodes can become the Conversation head.
6. Committed Node parent/content/role are immutable.
7. AgentSession identity is namespaced by provider and backend; attempts carry
   temporal process/account placement.
8. Terminal binding is optional and verified before destructive control.
9. Every load-bearing open fact is durable; declared live-only facets are the
   exception.
10. Every closer matches identity before consuming or closing.
11. Silence alone never proves success; sampler graces are named evidence
    rules.
12. Missing evidence yields `unknown` or `lost`, not invented success.
13. Raw Observation, canonical fact, provenance decision, effect request, and
    effect receipt are distinct layers.
14. Provider mappers return typed proposals and never mutate storage directly.
15. A default mapping prevents unknown supported activity from disappearing.
16. Bulk bytes never travel through per-token metadata transactions.
17. Stream final authority is declared per kind.
18. Slow clients resync and never backpressure source ingestion.
19. Conversation coordinators serialize logical mutation. Application use
    cases declare atomic units of work through semantic storage ports;
    database-specific writer handling stays inside the storage adapter.
20. Machine/account/window facts have named machine-service owners.
21. External effects use outbox, leases, attempts, and truthful indeterminate
    outcomes.
22. Desired external state is never persisted as verified observed state.
23. A surface send does not commit a user Node before provider/history or an
    authoritative baqylau-owned input receipt proves acceptance.
24. Answerable hooks always have provider-safe pass-through; rewritten input is
    returned only after capture preparation succeeds.
25. Rewind moves semantic head and workspace only through separately observed
    outcomes.
26. Projections declare owner, source revision, branch class, and rebuild scope.
27. Activity order uses provider position and causality; wall clock alone is
    never canonical.
28. Structural delivery cursors are not domain truth.
29. Provider adapters translate and drive; they do not define core semantics.
30. Capabilities come from implementations/probes, never provider-name branches
    in generic surfaces.
31. Surfaces own presentation and sanitize at the leaf.
32. Stable slot numbers can be durable; colors and glyphs cannot.
33. Usage preserves source, ledger, and temporality before totals are computed.
34. Cross-provider handover creates a target AgentSession and transfers context,
    never authority.
35. Repairs are named, scoped, and auditable; there is no universal replay.
36. New core entities must earn independent identity, lifecycle, invariants,
    and queries.
37. Existing measured behavior is ported through fixtures and parity, not
    memory.
38. Provider markers match parsed records and fields, never raw substrings;
    evidence created by the effect being reconciled is excluded unless the
    provider contract explicitly admits it.
39. Every synchronous transform is invertible and is inverted at the ingestion
    boundary before any semantic consumer sees command text.
40. An acknowledgement is not completion. Acceptance and completion remain
    different for inbound and outbound lifecycles.
41. An unmatched closer materializes honest observed work; it is never dropped.
42. A registered closer may fail to fire, so absence of that closer must have
    an independently safe verdict.
43. Provider subscription manifests classify observational, answerable, and
    delegating families. Delegating families are disabled unless Baqylau fully
    and explicitly owns the delegated action.
44. A self-caused effect can license only its registered bounded reconciliation
    probe; this is not general permission to infer success from silence.
45. A failed interaction driver leaves the dialog untouched unless that
    provider declares its cancel action semantically neutral.
46. Typing is destructive. Every input effect requires a freshly resolved and
    verified terminal/runtime binding.
47. Attention is computed from post-mutation state, after closer effects such
    as slot release are visible in the same transaction.
48. Each actor track owns its semantic head; child dialogue never competes with
    the lead Conversation head.
49. Current context occupancy is AgentSession state, not evidence that a
    compaction occurred.
50. A provider source-position order is used only within that source; a
    registered authority table arbitrates duplicate facts from different
    sources.
51. Notification truth, scheduling/holding, and deliveries are independent
    state axes.
52. Runtime requested values and effective values are recorded separately for
    start, resume, change, migration, fork, fallback, and handover.
53. A measured counterexample and its fixture ID are part of the rule that it
    justified; prose without its failed alternative is incomplete.

---

## 34. Tradeoff ledger

| Gained | Paid |
|---|---|
| Provider-neutral Conversation identity | More explicit mapping between native sessions and product continuity |
| Semantic Node tree | Native record detail needs a separate rebuildable index/evidence layer |
| Explicit Operations and causal links | More supporting records than one generic timeline table |
| Serialized Conversation coordination | Coordinator lifecycle/mailbox/supervision complexity |
| Named machine services | Scope ownership must be designed explicitly rather than assumed per session |
| Durable Observation inbox | Processing/quarantine infrastructure and storage |
| Targeted repair without global replay | Canonical mistakes require repair commands and retained evidence limits matter |
| Multi-channel framed Streams | Coordinating metadata, staging files, blobs, and recovery |
| Stable server-owned Activity order | Projection generations and live amendment semantics |
| Provider/terminal/headless independence | Capability objects and broad contract testing |
| Durable effects and alerts | Outbox, attempts, leases, reconciliation, and unknown states |
| Surface-neutral semantics | Presenters/renderers retain real complexity |
| One SQLite metadata DB | Write contention and compound-workload benchmark risk |
| Honest uncertainty | Product and UI must represent `unknown`, stale, lost, and indeterminate |
| Portable handover | Context rehydration is not exact native resume |

---

## 35. Deferred promotions and triggers

**Branch entity:** add only for named branches, several user-managed heads,
branch permissions, or branch merge operations. Until then use Node tree plus
Conversation head.

**Turn entity:** add only when turns gain user-facing lifecycle or indexed
cross-process invariants. Until then use `turn_key`.

**Actor entity:** add only when actors need durable cross-session identity,
profiles, permissions, independent lifecycle, or mailbox ownership. Until then
use provider-scoped `actor_key`, AgentSession, and task keys.

**Dedicated Handover entity:** add a detail table when indexed reporting,
approval, partial/delta transfer, or workflow complexity outgrows Operation
details.

**General Resource graph:** the basic Resource/version index is required.
Arbitrary relationships, permissions, global browsing, and full-text knowledge
graph behavior remain deferred.

**Out-of-process plugins:** require marketplace/untrusted code, language-
independent adapters, or proven crash-isolation need.

**Distributed canonical stores:** require offline autonomous backend decisions,
multiple controllers, or real replicated/multi-user operation.

**Different database engine:** requires a post-v4 ADR and architecture version;
it is not a deferred implementation choice inside v4.

---

## 36. Residual risks and open decisions

### 36.1 Residual risks

1. TUI assistant streaming remains approximate when only screen snapshots and
   final messages exist.
2. Synchronous input transformation is provider-fragile and can alter
   permission behavior.
3. Provider context remains partly unknowable; checkpoints are evidence, not
   hidden truth.
4. Activity causal placement depends on provider evidence and must fall back
   honestly when absent.
5. One machine-wide SQLite database may miss compound latency targets; this is
   addressed by tuning. Failure to pass blocks v4 release; neither another
   engine nor per-Conversation SQLite is a conforming escape hatch.
6. In-process plugins are trusted and can crash or compromise the daemon.
7. Remote disconnects may lose transient observations and cannot guarantee
   real-time control.
8. Resource/evidence retention can remove material needed for later repair or
   handover.
9. Cross-provider handover cannot transfer hidden state, approvals, processes,
   or exact native context.
10. Workspace rewind and handover can partially apply external changes.
11. Semantic mapping can be lossy even when the provider-native index is
    retained.
12. Machine-service and Conversation-coordinator transactions can become
    contention points if ownership boundaries are ignored.

### 36.2 Schema-lock decisions are closed

Performance thresholds are fixed in Section 38.30; retention and physical
storage are fixed in Section 38.34; Activity generation ownership is fixed in
Section 38.21; and structural-feed retention/resnapshot behavior is fixed in
Section 38.22. They are no longer open decisions.

Authentication is fixed in Sections 38.36 and 38.38. Provider identity and
mapping are fixed in Section 38.37. Upgrade, rollback, backup, and the ordered
schema are fixed in Sections 38.35 and 38.39.

The in-process plugin allowlist is exactly the bundled `claude_code`, `codex`,
and `opencode` provider plugins whose code and manifests are covered by the
same signed Baqylau application package. The operating-system package signature
is the trust root; there is no independent runtime plugin-signing root in v4.
Rotating that root is an application update with the normal verified backup and
rollback procedure. Every separately installed or third-party plugin is
untrusted and must use the Section 38.33 subprocess boundary even if it carries
its own signature. Administrative enable/disable never promotes trust class.

Exact external provider builds and raw payload bytes are measured fixture
inputs. An unregistered build is fail-closed for `answerable` and `delegating`
families: no command rewrite, launch, or control is attempted. Observational
families fail open to a typed generic record with
`provenance=unverified_build`, preserving raw evidence and visible uncertainty
without guessing a semantic kind. The provider edge health is degraded and a
fixture-registration task is emitted. This distinction is a closed manifest
rule, not an implementation choice.

---

## 37. Final architecture outcome

The architecture is a transactional modular monolith whose durable center is:

```text
Conversation
  provider-independent continuity and semantic head

Node
  immutable committed semantic message tree with ordered parts

AgentSession
  provider/backend-specific native incarnation with temporal attempts

Operation
  typed lifecycle for work, interactions, deliveries, and controls

Stream
  named incremental bytes with per-kind final authority
```

Around that center:

- Conversation coordinators serialize live local mutation and cache durable
  open facts.
- Machine services own accounts, discovery, windows, presence, and alert
  workflows that cross Conversations.
- Observations and provenance explain every boundary input and mapping
  decision.
- Short storage transactions commit canonical state, evidence decisions,
  revisions, and outbox work atomically; database-specific writer handling
  remains inside the storage adapter.
- Framed Stream files and blobs keep bulk bytes off the relational path.
- The native-record index preserves provider topology without becoming the
  canonical semantic tree.
- Activity positions and causal links let one server composer produce the same
  timeline for backlog, live updates, and all surfaces.
- Structural amendments and per-Stream resync make late/corrected data honest.
- Input buffers, Resources, context checkpoints, usage, slots, notifications,
  and repairs have narrow supporting ownership.
- Outbox attempts and effect Observations distinguish requested, attempted,
  accepted, observed, and committed states.
- Provider and terminal capabilities support interactive, headless, server,
  remote, and future integration styles without provider-name branching in the
  core or surfaces.
- Cross-provider handover attaches a new AgentSession to the same Conversation
  using a versioned portable context package.

This structure matches the actual product: a conversation tree surrounded by
causally linked work, provider-native runtimes, high-volume live streams,
multi-scope operational state, and external effects whose outcomes are not
always knowable immediately.

---

## 38. Normative closure of the live-system coverage review

This section resolves the findings in
`rewrite-design-v4-review-claude.md`. It is normative. When an earlier section
is less specific or conflicts with this section, this section wins and the
earlier text must be brought into line before v4 is declared complete.

The four product decisions recorded at the start of this document are not open
questions: daemon unavailability is accepted, every future feature remains in
detailed scope, metadata stays in one SQLite database, and the existing
reliability machinery is retained. The remainder of the review is accepted and
specified below.

### 38.1 Actor tracks: the semantic home of every agent's dialogue

A Conversation has one **lead track** and zero or more **actor tracks**. A track
is the message tree and current head for one provider-scoped actor. It solves a
concrete omission in Section 7: a child agent's own prose is not a divergent
candidate for the lead head and must not disappear merely because several
children speak concurrently.

```text
conversation_actor_tracks
  id                      UUID primary key
  conversation_id         Conversation ID, required
  actor_key                provider-scoped actor key, required
  agent_session_id         producing AgentSession, nullable
  parent_track_id          nullable; lead/actor that launched this actor
  lifecycle_operation_id  nullable agent_task Operation
  track_kind               lead | subagent | teammate | sidecar | peer
  state                    active | idle | ended | lost
  head_node_id             nullable committed Node on this track
  revision                 non-negative integer, starts at 0
  created_at               timestamp
  ended_at                 nullable timestamp
```

Identity is `(conversation_id, actor_key)`. The lead track uses the reserved
actor key `baqylau:lead`. A provider's native lead actor can be stored as an
alias of that key. `nodes.actor_track_id` is required for every newly created
Node. Imported Nodes whose producer cannot be determined attach to the lead
track and carry `actor_key = NULL` plus an `unknown_actor` provenance decision.

Rules:

1. `conversations.head_node_id` is exactly the lead track's `head_node_id`.
   The actor-track row is the single write owner. `ActorTrackStore.set_head_tx`
   updates only the track; the authoritative schema trigger projects the lead
   value and Conversation revision. Conversation creation preallocates both
   IDs, inserts the Conversation with a null head, then inserts the reserved
   lead track in the same deferred-FK transaction. No application method writes
   `conversations.head_node_id` directly.
2. A committed Node belongs to one track. Its `parent_id`, if present, belongs
   to the same track. Cross-actor causality uses `activity_links`, never
   `nodes.parent_id`.
3. Each track advances independently. Concurrent child output therefore does
   not move or compete with the lead head.
4. Child launch is a two-record correlation when the provider does not emit an
   actor identity in its launch opener. Claude `PreToolUse(Task)` creates the
   `agent_task` Operation plus a scoped FIFO launch-correlation row; it cannot
   create the final actor track because it has no `agent_id`. The matching
   `SubagentStart` consumes exactly one eligible FIFO row and creates/binds the
   actor track and Operation in one transaction. A resumed teammate start with
   no eligible opener uses its persisted actor description and never pops
   another launch. If a completion arrives without either record, ingestion
   creates a synthetic track and Operation with `origin=observed` and
   `launch_evidence=missing`; it does not drop the completion.
5. The lead view reads the ancestry of the lead head and composes child work at
   causal activity positions. Actor scope reads the ancestry of that actor's
   head and the activity produced by that actor.
6. Ending an actor ends its track but never deletes its Nodes. A later native
   continuation of the same actor reopens the track only when provider identity
   rules prove continuity; otherwise it creates another actor key.
7. A Conversation may have many active actor tracks. This is not semantic
   branch divergence.

Peer mail addresses actor keys, not AgentSession IDs:

```text
peer_messages
  id                      UUID primary key
  from_conversation_id    required
  from_agent_session_id   nullable
  sender_actor_track_id   required
  sender_actor_key        required
  to_conversation_id      required
  to_agent_session_id     nullable delivery hint
  recipient_actor_track_id nullable for broadcast
  recipient_actor_key     nullable for broadcast
  kind                    prose | task_assignment | idle | lifecycle |
                          termination | acknowledgement | extension
  body_ref                nullable BlobRef; required only for prose-like kinds
  task_operation_id       required for task_assignment
  external_message_id     nullable
  state                   pending | sent | delivered | read | failed | unknown
  reply_to_id             nullable peer message
  source_position         nullable
  source_timestamp        nullable
  provenance_id           required
```

Actor keys are interpreted inside their corresponding `from_` or
`to_conversation_id`; the same string in another Conversation is a different
address. A null recipient actor is a broadcast within `to_conversation_id`,
with one durable recipient-delivery row per resolved actor/device. Lifecycle
mail is never rendered as prose. The provider adapter declares
whether the host transcript contains lead sends, child sends, or both; the
mapper uses that declaration to avoid duplicates while still showing every
direction in actor scope.

Claude mailbox JSON lifecycle frames are rendered by one shared pane/web
wording function. The initial type phrases are:
`idle_notification -> idle`, `task_assignment -> task assigned`,
`task_completed -> task completed`, `teammate_terminated -> terminated`,
`shutdown_rejected -> shutdown refused`, and
`permission_request -> permission asked`. `idleReason` or `completedStatus` is
shown in parentheses only when it is not the ordinary `available`/`resolved`
outcome. The body is the first non-empty string among `summary`, `description`,
`message`, `taskSubject`, `reason`, and `failureReason`. An unknown string type
still renders one line using that type; raw frame JSON is never painted.

### 38.2 Current context, model changes, and branch-sensitive facets

Context occupancy is current session state, not a compaction checkpoint:

```text
agent_session_context_state
  agent_session_id         primary key
  context_window_tokens    nullable non-negative integer
  context_used_tokens      nullable non-negative integer
  current_model            nullable text
  occupancy_state          observed | stale | unavailable | unknown
  source_kind              transcript | provider_api | statusline | imported
  source_registration_id   nullable durable reader registration
  source_epoch             non-negative integer
  source_ordinal           non-negative integer within epoch
  source_position          nullable provider-native diagnostic value
  observed_at              timestamp
  provenance_id            required
```

The ordinary current value is the newest valid last-assistant/provider record
according to `(source_epoch, source_ordinal)`, never `observed_at`. A
`compact_boundary` newer than that assistant record temporarily outranks it.
The boundary is the safe fail-open value: it is applied even while the native
parent walk has not yet produced a descendant. It is discarded only when the
record graph positively proves that the boundary was reverted or belongs to a
different branch. It projects the checkpoint's post-compaction occupancy until
a newer assistant/provider usage record arrives. The
source-registration service assigns those comparable values after proving
continuity across relocation; a delayed record from an older epoch cannot
replace newer truth. A Conversation that has never
compacted can still have context occupancy. `context_checkpoints` own the
conditional boundary override above, while `agent_session_context_state` owns
the projected current result. A missing/unreadable branch proof fails open to
the boundary value, not the stale assistant value. A missing occupancy probe writes `unknown` or retains
a value as `stale`; it never writes zero.

Actor-specific facets have separate durable owners because a provider child
may have no AgentSession. `actor_track_context_state` has the same occupancy,
source-order, freshness, and provenance fields keyed by `actor_track_id`.
`actor_track_runtime_revisions` is the append-only actor equivalent of the
session runtime history and includes requested/effective model and effort plus
the provider/source order. `ActorTrackDTO` reads these rows. When the lead track
has no actor-specific row it may project the active AgentSession value with
`source=session_projection`. A non-lead track never inherits host **context**.
Model and effort follow the provider's registered inheritance rules: Claude
`model: inherit` and an absent child effort fall through to the effective host
model/effort, while `toolUseResult.resolvedModel` from the parent transcript
overrides that fallback at completion. Missing evidence becomes `unknown` only
after the registered inheritance ladder is exhausted.

Claude context-window resolution checks
`CLAUDE_CODE_DISABLE_1M_CONTEXT` first. A true value caps the effective window
at the provider's non-1M value even when model suffix, status line, parent
`resolvedModel`, or account capability otherwise advertises 1M. The model
catalogue owns `model_match` (`exact | family`), `model_short`, default effort,
and window size so the model menu can mark the running row without string
sniffing.

The compaction display latch uses the read-side safety bound
`COMPACT_MAX_S=900` (15 minutes). The provider formatter never ages the latch
out while writing it. Native completion/revert clears it earlier; a read after
that bound returns inactive and records `compact_latch_expired`, even if the
closing hook was lost. This display expiry does not mark the compaction
Operation successful.

Every requested or effective runtime change is append-only history:

```text
agent_session_runtime_revisions
  id                      UUID primary key
  agent_session_id        required
  attempt_id              nullable
  requested_provider_id   nullable
  requested_execution_target_id nullable
  requested_account_id    nullable
  requested_mode          nullable
  requested_model         nullable
  requested_effort        nullable
  effective_provider_id   nullable
  effective_execution_target_id nullable
  effective_account_id    nullable
  effective_mode          nullable
  effective_model         nullable
  effective_effort        nullable
  reason                  start | resume | user_change | provider_fallback |
                          fork | migration | handover | observation
  source_registration_id nullable
  source_epoch            non-negative integer
  source_ordinal          non-negative integer
  source_position         nullable
  observed_at             timestamp
  provenance_id           required
```

A provider `model_refusal_fallback` or equivalent creates a
`provider_fallback` revision even though no hook fired and no user requested a
change. The source reader maintains a forward cursor over the artifact because
this record can occur once in the middle of a large file. The warning remains
active while the latest effective model equals the fallback target and clears
only after newer evidence proves another effective model.

The following projections are required and have exact owners:

| Projection | Durable owner | Source and update rule | Branch rule |
|---|---|---|---|
| Current goal | `conversation_goals` | Last valid `goal_status` attachment by provider source order; scan from retained cursor on every artifact change because `/goal` has no dedicated hook | One current row per actor track; lead view uses lead track |
| Provider tasks | `provider_task_snapshots` and `provider_tasks` | Read the provider task directory on every hook family declared `may_touch_tasks`, before session-end cleanup can erase it | Snapshot belongs to actor track and source position; latest complete snapshot wins |
| Current plan | latest committed plan Resource linked from `interaction_details` or plan-producing Operation | Preserve plan text from the provider tool-use record before a verdict is applied | Actor-track local; lead view includes a child plan only through causal placement |
| Context/runtime | `agent_session_context_state`, `agent_session_runtime_revisions`, `actor_track_context_state`, `actor_track_runtime_revisions` | Newest valid value by source order; lead-only session projection fallback above | AgentSession or actor-track scoped; children never inherit host facets |
| Title | `conversation_title_revisions` + `conversation_title_current` | Ownership transfer rule below | Conversation scoped |

```text
conversation_goals
  conversation_id, actor_track_id, goal_ref, status, source_registration_id,
  source_epoch, source_ordinal, source_position, observed_at, provenance_id

provider_task_snapshots
  id, conversation_id, actor_track_id, source_registration_id, source_epoch,
  source_ordinal, source_position, completeness, captured_at, provenance_id

provider_tasks
  snapshot_id, provider_task_key, parent_task_key, title, status,
  owner_actor_key, ordinal, metadata

conversation_title_revisions
  id, conversation_id, owner_agent_session_id, value, owner, state,
  source_registration_id, source_epoch, source_ordinal, source_position,
  supersedes_id, requested_at, effective_at, provenance_id

conversation_title_current
  conversation_id, title_revision_id, revision
```

Title revisions are append-only. `owner` is `provider_live` while the
Conversation's designated `active_agent_session_id` is live and
`baqylau_parked` after it is parked. `surface_user` is allowed for an
initial/manual title revision when no provider session is designated
(`active_agent_session_id IS NULL`); that revision has no owning AgentSession
and no provider source registration. The current-title selector accepts a
`surface_user` revision only in that no-session state. Provider-owned revisions
must identify an AgentSession in the same Conversation, and a provider source
registration must belong to that AgentSession and epoch; mismatches fail closed
through the title scope triggers.

A live rename asks the provider to rename itself and becomes effective only when
provider evidence shows the new title. Baqylau does not keep a competing sticky
live title. A parked rename writes a durable override and, when the provider
format supports it, appends the native record. Resume transfers ownership back
to the provider but preserves the parked override revision as history.
Simultaneous non-active AgentSessions may append their own provider title
revisions but cannot move `conversation_title_current`. Changing
`active_agent_session_id` selects that session's newest effective provider
revision by source order; while it has no effective title, the prior current
value remains visible as `stale`. With no active live session, the newest
effective parked override wins. At every resume/control gesture, `resumable` is
re-proved from the provider artifact; a missing artifact returns
`410 session_artifact_gone` before any terminal effect.

### 38.3 Workspace identity, relocation, grouping, hiding, and legacy import

`start_cwd` and the current artifact/workspace are different facts:

```text
agent_session_artifacts
  id, agent_session_id, artifact_kind, current_ref, previous_ref,
  workspace_ref, observed_at, source_position, provenance_id

agent_session_grouping
  agent_session_id primary key, frozen_start_cwd, group_dir,
  resolution_state, resolved_at, provenance_id
```

The first trustworthy host event freezes `frozen_start_cwd`. `group_dir` is
that directory resolved through a linked worktree's `.git` ownership to the
owning main checkout. It never changes because of `cd`, current cwd, or an
actor's worktree. Empty or unresolvable group keys are omitted from overview
lists but remain addressable by direct Conversation URL.

Every host-level provider event re-evaluates the current transcript/rollout
path and current cwd. A changed artifact path atomically closes the old source
registration at its last cursor, updates `agent_session_artifacts`, and starts
the reader at the proven corresponding position in the new file. Events carrying
a child `agent_id` may update the child's actor track/workspace but must not
restamp the host AgentSession's cwd, grouping, or transcript path.

Hidden project groups are timestamped preferences:

- `POST /api/v1/project-groups/{group_id}:hide` accepts
  `{expected_revision}` and returns the updated group preference;
- it returns `409 project_has_live_session` while any active AgentSession uses
  the group;
- a group is hidden only from sessions created at or before `hidden_at`;
- a newer session makes the group visible automatically; and
- there is intentionally no separate unhide gesture, although deleting the
  preference through the general preference API has the same effect for
  administrative clients.

Legacy parked mirrors are imported by
`baqylau import legacy-parked --source <absolute-directory>`. The importer:

1. scans each legacy state database read-only;
2. records source path, inode/size/mtime, schema fingerprint, and import run;
3. creates or resolves the Conversation and AgentSession through the same
   provider identity rules as live ingestion;
4. converts stored operations and monitor/background output into Operations,
   Streams, and Resources with `origin=imported`;
5. preserves legacy row IDs as namespaced source IDs for idempotency;
6. imports failures as evidence rather than discarding the whole database;
7. never mutates or deletes the legacy source; and
8. produces counts of imported, duplicate, unavailable, and quarantined rows.

The import is restartable. Identity is `(source_database_fingerprint,
legacy_table, legacy_row_id)`. Phase 2 parity cannot pass until all discovered
parked databases are either imported or named in an explicit product-drop
manifest.

The discovery set is not limited to parked Conversation databases. It also
includes `~/.claude/baqylau-audit/audit.db`, the legacy dashboard-preferences
database, every discovered `counters` table, and legacy key/value facets. The
importer has named readers for audit sessions, OTLP usage, swallowed errors,
alerts, Conversation mutes, hidden directories, completed-task dismissals,
namespace preferences and drafts, counters, and scorebar facets. It preserves
their original timestamps and namespaced identities and writes the canonical
tables/projections defined here; unsupported rows are quarantined rather than
silently dropped. For old operations that predate explicit `audience` and
`register` fields, the legacy adapter runs the four checked-in, fixture-backed
fallback classifiers for bubbled prose and provider chrome, stores the derived
values with `origin=imported`, and records which classifier decided them. A
missing classifier is an import error, because defaulting would duplicate prose
or expose host chrome.

### 38.4 Provider-edge subscription, installation, trust, and latency

Every provider plugin ships a versioned subscription manifest. Each native
event family is classified as exactly one of:

- `observational`: subscribing only reports an event;
- `answerable`: the provider waits for a bounded response but pass-through
  preserves provider behavior; or
- `delegating`: subscribing transfers responsibility for an action to the
  subscriber.

The manifest contains `native_event`, `class`, `edge_handler`,
`response_schema`, `deadline_ms`, `may_touch_tasks`, and `enabled`. Unknown
families default to disabled. A delegating event is disabled unless a dedicated
feature specification defines the full replacement action and its failure
behavior. Claude Code `WorktreeCreate` and `WorktreeRemove` are explicitly
disabled and installation must prove that they are absent from the generated
settings. A universal edge may share an executable, but it may not universally
subscribe.

```text
provider_edge_installations
  provider_id, backend_id, config_ref, installed_version, desired_version,
  config_digest, executable_digest, trust_key, trust_state, last_verified_at,
  state, last_error
```

`trust_state` is `trusted | review_required | rejected | unknown |
not_applicable`. Installation is a managed workflow:

1. read and validate the provider configuration;
2. render the exact manifest without delegating families;
3. write a sibling temporary file with owner-only permissions;
4. preserve a `.bak` of the previous provider configuration;
5. atomically replace the configuration;
6. verify the provider sees the expected handlers;
7. verify executable/configuration digests and provider trust state; and
8. mark ingestion healthy only after verification.

An upgrade that changes a hash-trusted executable enters `review_required`.
The daemon and UI must report that provider ingestion is blind until a person
completes the provider's trust prompt and
`POST /api/v1/provider-edges/{provider_id}:verify-trust` succeeds. It must not
report the provider as healthy merely because the daemon is running. Revert
restores both the previous executable and the `.bak` configuration, then
verifies them.

For Claude Code, the managed configuration is one atomic document containing
both the hook subscription manifest and these required settings:

```text
env.CLAUDE_CODE_ENABLE_TELEMETRY = "1"
env.OTEL_METRICS_EXPORTER = "otlp"
env.OTEL_EXPORTER_OTLP_PROTOCOL = "http/json"
env.OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4319"
env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE = "DELTA"
statusLine.command = <absolute packaged capture-then-delegate shim path>
```

Installation preserves unrelated settings, backs up the exact previous bytes,
and verifies all six values after the atomic replace. The status-line shim first
captures the complete payload for Baqylau, then invokes the pre-existing
status-line command with the same stdin/stdout/exit semantics. Uninstall/revert
restores the prior command and environment entries exactly; it does not merely
delete them. Provider-edge health is degraded if any required value, delta
temporality, or shim digest differs.

OTLP has a separate loopback-only ingestion entrypoint at
`127.0.0.1:4319`. It accepts `POST /v1/metrics` with OTLP/JSON bodies, including
`Content-Encoding: gzip` and HTTP/1.1 chunked transfer encoding. It is not part
of `/api/v1`, does not require a Baqylau principal, does not use the product API
envelope, and is never exposed through the remote listener. The decompressed
body cap is 8 MiB and the header cap is 32 KiB. The listener always returns
HTTP 200 with an empty body after consuming the request, including for malformed
payloads: non-200 makes the exporter retry the same delta and can double count.
Parse/validation failures still create a bounded health error and diagnostic
evidence row.

Each accepted HTTP request receives a daemon-boot-scoped monotonically
increasing `receipt_sequence`, durably checkpointed before mapping. An OTLP
Observation identity includes `(listener_instance_id,receipt_sequence)`, never
the body hash alone, so byte-identical delta exports remain distinct. Within a
request, `source_record_key` is
`<receipt_sequence>:<resource_index>:<scope_index>:<metric_index>:<datapoint_index>`.
Resource attributes supply only process-wide defaults. The per-datapoint
attributes (`session.id`, `query_source`, `model`, and `type`) are read for each
datapoint and override those defaults; one export body may therefore credit
main, subagent, and auxiliary work. The durable dedup key additionally stores
`startTimeUnixNano` and `timeUnixNano`; an identical window is a retry and does
not credit twice, while a new delta window remains a new observation.
Unresolved facts remain machine-scoped diagnostic evidence and are not guessed
into a Conversation.

The exact edge runtime is the packaged CPython interpreter and a minimal
stdlib-only edge module; it must not resolve pyenv or import the daemon package
graph on each invocation. Packaging stores the absolute interpreter and module
paths in provider configuration. Each manifest supplies its measured native
deadline. The edge reserves 20% of that deadline for provider return and uses
the remaining 80% as one fixed end-to-end daemon request timeout. Non-answerable
events perform one send attempt and never wait for semantic processing.

Acceptance gates are per provider and per hook family. Under the Section 27.3
compound workload, answerable pass-through caused by Baqylau latency must be
below 0.1%, p99 edge-to-answer must use less than 70% of the native deadline,
and a non-answerable edge process must return within 50 ms p99 on the supported
packaged runtime. These gates are tested on every supported OS.

### 38.5 Answerable transforms and independent ingestion consumers

Every synchronous command transform is an invertible pair:

```text
PreparedTransform
  kind
  original_bytes
  transformed_bytes
  inverse_version
  capture_ref
  eligibility_evidence
```

`apply(original) -> transformed` and `invert(transformed) -> original` are
fixture-tested. Inversion is blind-applicable: a consumer need not have the
original request or in-memory preparation object. The first ingestion step for
every later provider payload applies the registered inverse before command
classification, audit display, deduplication, rendering-kind detection, or
semantic mapping. Failure to recognize a transform preserves the received
bytes as evidence, returns `unknown_transform`, and forbids confident command
classification.

An Observation fans out into named consumers rather than one all-or-nothing
mapper batch:

```text
observation_consumers
  observation_id
  consumer_kind          identity | canonical | attention | paths | tasks |
                         evidence | extension
  state                  pending | processing | applied | skipped |
                         quarantined
  decision_code
  attempt_count
  next_attempt_at
  last_error_ref
  PRIMARY KEY(observation_id, consumer_kind)
```

The identity consumer runs first. Each remaining consumer commits its own
short transaction containing its canonical changes, provenance, projection
revision, outbox rows, and consumer result. A failure in task snapshotting does
not roll back a valid attention transition or the raw evidence row. A consumer
may depend on `identity=applied`, but sibling consumers do not depend on one
another unless their registered contract says so. The Observation becomes
`complete` when every required consumer is `applied` or `skipped`, and
`complete_with_quarantine` when at least one required consumer is quarantined
and all others are terminal. Identity/decoding failure becomes
`quarantined_identity`; quarantine is visible per consumer.

Every deliberate skip writes `decision_code`, including `ignored_child_event`,
`feature_disabled`, `cooldown`, `unsupported_family`, and
`insufficient_evidence`. Providers forward every observational family listed
in their manifest even when no semantic consumer exists; the evidence consumer
then records `no_semantic_consumer`. This preserves evidence that something
arrived without forcing every family into the domain model.

### 38.6 Correlation and closer rules

Closer matching uses parsed provider records, not raw byte substring searches.
A marker is admissible only when it appears in the expected field and record
kind. Nested memory, documentation text, tool output, and an effect's own
byproducts are excluded unless a provider contract explicitly includes them.

The closer catalogue contains, per operation kind:

- opener identities and fields;
- acceptance/acknowledgement records;
- completion records;
- cancellation and denial records;
- unmatched-closer materialization rule;
- source authority and source ordering;
- probes allowed when the closer is absent; and
- the safe final state when neither closer nor probe exists.

Acceptance is never completion. An async “launched successfully” record can
move an Operation to `running`; it cannot seal its Stream or end the Operation.
A completion with no opener creates an Operation with
`origin=observed`, `opener_state=missing`, the real closer identity, and the
honest lifecycle result. It is never discarded as already closed.

A registered closer is not assumed to fire. The result when it is absent must
be independently safe: normally `unknown`, `lost`, or `running` with stale
freshness. PostToolBatch is not used to infer foreground success. A blocked or
parallel batch that never emits its hook therefore cannot produce false green
attention.

Interruption and queued-delivery cancellation settle for one provider event
loop and inspect the next parsed record. If a new prompt follows the interrupt
marker, observation continues because queued work became active. Quiet time by
itself never proves completion.

Monitor process matching first compares the complete normalized command, then
falls back to argv identity. Normalization is whitespace-insensitive and treats
shell-rendered newline spellings `$'\\n'`, `\\012`, and `\\n` as the same
separator. More than one match is `not_found_ambiguous`, allowing the registered
idle fallback; it never chooses an arbitrary PID. Writer-liveness (`lsof` or
adapter equivalent) is asynchronous, throttled, and fail-open: timeout/error
means “may still be writing,” never “no writer.” Foreground capture has the
liveness safety backstop `FG_BACKSTOP_S=7200`; background and monitor captures
have no flat time backstop and close only from their registered semantic,
process, writer, or explicit abort evidence.

A self-caused effect may license a bounded reconciliation probe because the
system knows the cause it introduced. For a web interrupt the coordinator:

1. captures the parsed-record and attention baseline before sending Escape;
2. sends through a freshly verified terminal binding;
3. waits for provider evidence;
4. if no event arrives, performs the provider's bounded escape-recheck; and
5. applies a result only when the recheck proves the expected state change.

The recheck is not run when evidence shows the turn refused to stop, because a
green/idle screen observation could hide a still-running turn. This exception
does not authorize general silence-based success.

Provider-fork adoption uses one durable, take-once predecessor note plus
negative independent-start evidence. Claude emits no separate positive
continuation record for this shape. A new native session ID may adopt a
predecessor only when the new event's backend/provider/workspace/canonical-cwd
matches exactly one pending hosted predecessor note and it has no independent
start evidence. `InstructionsLoaded` and every
provider-equivalent early-start record set the independent-start mark before
`SessionStart` can arrive. Arrival order is not a tie-breaker: the fork's first
event may precede the predecessor's start. Adoption is a compare-and-set over
the predecessor note and the new ID's negative-start marker.

Continuation attempts inherit the predecessor attempt's environment snapshot
when the new event has a scrubbed or absent environment. Missing is represented
as unknown, not as an empty map and not as “not in a terminal.” Explicitly
present empty values can clear only fields whose provider contract permits
clearing.

The adoption race is durable:

```text
session_adoption_notes
  id, predecessor_agent_session_id, candidate_backend_id,
  candidate_provider_id, workspace_identity, cwd_realpath,
  candidate_external_id nullable until consumption, predecessor_attempt_id,
  expected_revision, state, created_at, expires_at, consumed_at,
  decision_provenance_id

session_start_evidence
  backend_id, provider_id, external_id, independent_start_seen,
  first_event_kind, source_epoch, source_ordinal, observed_at, provenance_id

attempt_environment_values
  attempt_id, key, presence_state, value, inherited_from_attempt_id,
  source_epoch, source_ordinal, provenance_id
```

`session_adoption_notes.state` is `pending|consumed|rejected|expired`.
Only a verified hosted start may create a note. Creating another note for the
same backend/provider/workspace/cwd replaces the older pending note. The live
predecessor writes the note; the successor may consume it while that
predecessor is still live. A daemon/headless start or a backgrounding fork with
no successor start creates no note. The successor lookup compare-and-sets one
note by that scope, requires the predecessor to remain live (a missing state DB
is stale and is rejected), writes the newly known external ID during
consumption, and cannot consume the note twice. A parked note has no arbitrary
expiry; it is retired only by consumption, explicit rejection, or a cleanup
decision recorded with its evidence.
InstructionsLoaded atomically inserts/updates `session_start_evidence` before
adoption lookup. Adoption consumes one pending note only if its revision still
matches and `independent_start_seen=false`; otherwise it rejects the note with
the conflicting evidence. Environment `presence_state` is
`present|explicit_empty|absent|inherited`. `absent` never overwrites a previous
value. A continuation materializes `inherited` rows pointing to the predecessor
attempt. Only `explicit_empty` clears a key whose provider manifest permits it.

### 38.7 Authority table for duplicate provider evidence

Each provider plugin registers a table keyed by semantic family. The initial
Claude Code policy is:

| Family | Canonical authority | Other source |
|---|---|---|
| Foreground/background command result | Child transcript/provider result; captured stream owns already surfaced bytes | Hook prepares capture and supplies lifecycle hints |
| Monitor output and lifecycle | Hook/task output and liveness probe | Child transcript deliberately paints no duplicate stream |
| Peer `SendMessage` | Hook event, including child `agent_id` events | Transcript is used only where the plugin proves the direction is absent from hooks |
| Assistant prose | Provider transcript final record | Screen tap is provisional only |
| Interaction verdict | Parsed provider dialog/tool records plus verified drive result | Screen state is a drive precondition, not durable truth |

The initial Codex policy is separate because `event_msg` and `response_item`
are two deliberately non-equivalent registers:

| Family | Canonical authority | Other source |
|---|---|---|
| User prompt | `response_item`; it alone owns post-abort and queued prompts | matching `event_msg` is duplicate lifecycle evidence |
| Assistant prose | `response_item` message/delta identity and revision | `event_msg` supplies progress only |
| Child result | `phase=final_answer` for the exact child/task | `last_agent_message` is fallback only for a measured build lacking `final_answer` |
| Child lifecycle | `task_started`/`task_complete` | lifecycle cannot choose result text |
| Command/tool result | `response_item` stable item ID | matching `event_msg` cannot create a second Operation |

Pending child prose flushes only immediately before a record that opens another
semantic block. `token_count`, bookkeeping, and task completion do not open a
block and cannot demote the preceding `final_answer` result.

Source position wins only between records from the same ordered source. Arrival
time never arbitrates two different sources. A new family cannot ship until its
authority row and duplicate fixtures exist.

### 38.8 Stream bounds, rendering kinds, ANSI, and copy behavior

Stream capture and surfaced presentation use three separate bounds:

- one source-reader pump reads at most 256 KiB;
- one newline-free surfaced line retains at most 64 KiB and inserts an honest
  `… (N bytes elided)` marker for the dropped middle; and
- one rendered operation block contains at most 128 KiB before it is split into
  another block.

These defaults are configurable only through validated deployment settings and
are frozen into fixture expectations. JSONL/native-record readers do not use
the surfaced-line cap because truncating a parsed record would corrupt it.
When a pump hits its bound, it sets `more_available=true` and schedules itself
again before evaluating completion. No unbounded `pending` buffer is allowed.

File tailers read exactly `min(current_size - position, 256 KiB)` bytes from a
size snapshot; they never issue an unbounded read that can consume bytes
appended after that snapshot. If the file shrinks, the tailer restarts at byte
zero. Its restart-safe checkpoint is
`position - len(pending_bytes) - dropped_bytes`, not merely the last read or
last surfaced position. Foreground-to-background handoff is ordered
`write sentinel -> measure launch-site byte offset -> spawn reader`; the offset
is measured at the launch site, never when the reader later opens the file.
Losing output is worse than a bounded duplicate, so this ordering may not be
rearranged. After truncating a surfaced line, the ANSI neutralizer runs on the
retained bytes and drops a dangling terminal ESC byte rather than carrying a
partial escape into the next chunk.

The full captured byte Stream may be retained according to its retention class,
but the ordinary Copy action is WYSIWYG: it copies the immutable visible block
content, including an elision marker, rather than re-reading a transient tee
file or a changed workspace file at click time. A separate “download retained
raw output” action may exist only while `raw_copy_state=available`; after expiry
it returns `410 raw_content_expired`. Visible Copy remains stable for as long as
the block itself is retained.

`streams` gains:

```text
  media_type              nullable IANA media type
  render_kind             plain | markdown | json | yaml | source | extension
  language                nullable canonical language identifier
  render_detection_source raw_command | provider_metadata | explicit |
                          fallback
  visible_copy_ref        nullable sealed BlobRef
  raw_copy_state          available | expired | never_captured
```

Render detection is a provider-owned priority registry, not a per-surface
guess. It runs on the raw original command before any tee transform in the
process that prepares capture. The initial Claude registry is `markdown`,
`json`, `yaml`, then `source`; explicit fences and provider metadata participate
according to the registered priority. Both terminal and web presenters consume
the stored result.

Provider-specific semantic excerpt caps apply after byte bounding. Claude uses
24 lines for a prompt, 24 for a teammate message, 12 for an outgoing
`SendMessage`, 10 for a generic tool request, 60 for a command body, and 8 for
a job note. Agent `MESSAGE` and `RESULT` bodies are deliberately uncapped by
line count; the byte/block limits still apply. Codex retains its own registered
cap table and is not forced to use Claude values. Every capped excerpt renders
`N of M lines shown` and an expansion control.

Text layout is measured in terminal cells, not Unicode code points or grapheme
count. Tabs expand to the next 8-cell stop before width calculation. Wrapping
prefers word boundaries, hard-breaks a single overlong word, and reasserts the
currently active canonical SGR state at every wrapped row. The renderer, which
knows final width and cell widths, owns gutters and panel fill; producers never
pre-pad semantic content.

Code-operation text is formatted once when the immutable Operation presentation
is created. Formatter failure preserves the original text and records a
diagnostic; it never drops the operation. View-body syntax highlighting happens
at paint time from the stored `render_kind`/`language`, using a process-wide
lexer singleton cache. A producer is allowed to lack the highlighting package;
the pane host detects that case and re-execs the packaged compatible interpreter
that contains it. Late markdown-fence detection may amend `render_kind` with a
new item revision, which invalidates the render cache.

Escape handling has two distinct steps:

1. At the producer boundary, textual escape spellings `^[`, `\\033`, `\\x1b`,
   `\\e`, and `<ESC>` are restored only for command-output sources whose provider
   contract declares that encoding. This operation is not applied to prose,
   JSON records, paths, or user input.
2. At presentation, parsed ANSI SGR colour/style and OSC 8 links are allowed.
   Cursor movement, screen erase, device control, title change, clipboard OSC,
   and every unknown escape are neutralized into visible harmless text.

Web output converts allowed SGR and OSC 8 into escaped semantic HTML. Terminal
output re-emits a canonical allowlisted sequence; it never forwards the
producer's raw control bytes. Thus legitimate `git`, `make`, and `pytest`
colour survives without allowing terminal control.

### 38.9 Presentation blocks, activity classes, pagination, and viewport

The presenter supports these blocks in addition to the existing named types:

```text
GenericActivityBlock
  operation_id, title, summary, details_ref, activity_class, register,
  audience, copy_ref

MultiResourceOutputBlock
  operation_id, ordered_resource_ids[], command_ref, output_stream_id,
  activity_class, register, audience
```

Unknown registered Operations render as `GenericActivityBlock`; they never
vanish. Parser kinds and renderer kinds are checked for set equality in both
directions. Every block carries `activity_class`, the server-defined category
used for view filtering and summaries such as “used 3 tools.” It also carries
`register` (`host | agent | team | codex | quiet | extension`) and `audience`
(`lead | actor | both | hidden`) when the producer knows something the
presenter cannot infer.

Each AgentSession has one server-side, cross-device `view_mode` preference:
`verbose | default | focus`, defaulting to `default`. It survives park/resume
and is not stored in browser local storage. The exact fold table is:

| Mode | Folded activity classes |
|---|---|
| `verbose` | none |
| `default` | `bash`, `read`, `monitor`, `task`, `mail`, `codex` |
| `focus` | `bash`, `read`, `bg`, `monitor`, `edit`, `write`, `agent`, `team`, `task`, `mail`, `skill`, `tool`, `codex` |

File edits/writes, peer messages, and warning-light lines never fold in
`default`. In `focus`, prompts and final replies remain full strength. During
a live turn, only the newest provisional assistant reply is retained and
dimmed; older mid-turn replies are hidden. Once the turn has a final reply,
that reply is retained at full strength. Dimming is a paint-only property and
cannot split or recompute semantic runs. A folded group displays
`N of M shown`. Expansion/collapse choices remain local to that surface and are
not preference deltas. Changing the mode compare-and-sets the stored revision
and emits the dedicated durable `view-mode.changed` Conversation SSE event to
all other surfaces. The
requesting surface suppresses its own echo by `client_mutation_id`.

A single command reading several files produces one
`MultiResourceOutputBlock` with all file Resources in command order. Baqylau
does not inject delimiters, reread files from disk, or split output among files,
because the source output does not prove that mapping.

Whole-block pagination is authoritative, but concurrent source interleaving is
handled explicitly. The stable page key is
`(activity_generation, position_key, local_sequence, item_id)`. A page boundary
never splits the rendered content of one item. When older fetched items belong
to a group already present in the live page, the client folds them into that
existing block by `group_id`; server source interleaving may therefore make a
logical group non-contiguous without violating item-boundary pagination.

Terminal viewport restoration is mandatory for every full repaint caused by
expand, collapse, resize, or backfill:

1. Before repaint, capture whether the pane is following the bottom and capture
   the first fully visible plain-text anchor plus its row offset.
2. Read the configured `CLAUDE_MIRROR_SCROLLBACK` value (legacy default 4,800);
   do not derive it from terminal scrollback. Trim with hysteresis before
   measuring the anchor, and retain at most 8,000 operation records.
3. If follow mode was active, remain at the bottom.
4. Otherwise find the anchor by global normalized-text search over the complete
   rendered buffer with the registered confidence threshold. If text occurs
   more than once, choose the match closest to the caller's prior row. Every
   null/low-confidence path records a reason; a windowed search is forbidden.
5. Put the controlling tty in no-echo, noncanonical mode and issue a bounded
   DSR request. Only arrival of the reply is used as an ordering handshake; its
   row/column value is ignored. DEC 2026 synchronized output must not wrap this
   handshake because buffered bytes would not yet be parsed. Timeout records
   degraded restoration and never blocks the render loop indefinitely.
6. Establish a deterministic base by scrolling to END, then move up
   `total_rows + 1 - viewport_height - anchor_row_offset`, clamped to the
   retained row budget. Recompute follow-bottom after repaint.
7. Verify the landing and apply at most three total passes, each scrolling
   only the measured error rather than repeating the absolute move. A first
   miss above 400 rows consumes pass 0 for one absolute restore; at most two
   delta corrections then follow. Watch the
   intended anchor for 8 seconds after a 0.7-second settle and correct drift up
   to two times with 5-row slack.

The pane repaints the whole retained buffer on a toggle, backfill, width change,
or item amendment. Ordinary append writes only newly rendered rows. A SIGWINCH
whose measured rows and columns are unchanged is audited as `paint_skipped` and
does not clear/repaint scrollback. Render-cache identity is
`(item_id,item_revision,width)`, because activity items can be amended or
moved. Inherited expansion state initializes a newly attached pane but is not
itself emitted as a user delta. “Incremental renderer state” refers only to
parsing/caching plus the ordinary append path; it never authorizes partial
replacement for a full-reflow cause.

Click-to-view uses OSC 8 links and the terminal's configured open-action
handler. Mouse reporting is forbidden because it steals text selection and
depends on row geometry invalidated by reflow. The terminal capability contract
therefore includes:

```text
OpenActionChannel.register(url_scheme, executable)
OpenActionChannel.verify_registration() -> Verification
OpenActionChannel.handle(signed_action_url) -> ActionResult
```

Links appear only on the unwrapped label operation and are omitted when the
pane width is below 34 columns. Signed action URLs contain an expiring nonce and
resolve the current Conversation/Operation server-side before applying an
action.

### 38.10 Pane controls, terminal focus, slots, active time, and commands

Pane controls are first-class terminal controls:

- `POST /api/v1/terminal/panes:toggle`
- `POST /api/v1/terminal/panes:grow`
- `POST /api/v1/terminal/panes:shrink`
- `POST /api/v1/terminal/panes:reset`
- `PUT /api/v1/terminal/panes/percentage` with `{percentage}` where the value is
  an integer from 10 through 90.

The request may omit a Conversation ID. In that case the terminal adapter reads
the currently focused terminal tab/window and its trusted `claude_session` (or
provider-equivalent) variable, resolves the active AgentSession alias, and then
resolves the Conversation. No request environment is trusted for this mapping.
Pre-effect failures are `404 focused_session_not_found`,
`409 ambiguous_terminal_focus`, or `503 terminal_unavailable`. Acceptance is
`202` with the pane Operation, Conversation ID, verified binding revision, and
requested pane state. The outbox attempt's receipt later publishes verified
previous/resulting pane state through `operation.changed`; the HTTP response
never claims an external result before that receipt.

`grow` and `shrink` move by the resolved cell step, default 4 columns. Settings
resolve in this order: request override (when the endpoint permits it),
per-principal preference, machine configuration, imported
`CLAUDE_MIRROR_STEP`, then default. The companion initial-width bias resolves
the same way from `pane.bias`/`CLAUDE_MIRROR_BIAS`, default 25 percent. Absolute
percentage is implemented by measuring the whole terminal split tree,
excluding any pane tagged `claude_scorebar`, then issuing the adapter's required
relative resize steps until the measured mirror percentage is within one cell;
kitty does not provide a truthful absolute-resize primitive.

The five-row scoreboard is a separate pinned terminal window immediately below
the mirror, tagged `claude_scorebar`. It is never painted inside the mirror
scrollback and DECSTBM is not used. Geometry discovery and resize calculations
exclude that tag so the shared column is not counted twice.

`slot_allocations` additionally stores `owner_pid`, `owner_host_instance_id`,
and `last_verified_at`. Before allocation, the slot service tests the owner PID
on the recorded host: success or EPERM means alive; ESRCH means dead. Dead
owners are released and their slots can be stolen in the same transaction.
Remote/unknown liveness does not reclaim a slot until its lease expires. A
normal closer explicitly releases its slot before attention is recomputed.

The scoreboard's active-time value is derived from
`attention_transitions`. It accumulates wall-clock time only while attention is
not `done`/green, pauses on entry to `done`, resumes on exit, and freezes at
AgentSession end. The projection stores `accumulated_ms` and
`last_resumed_at`; rebuild folds transitions in source order.

```text
agent_session_active_time
  agent_session_id, accumulated_ms, last_resumed_at, source_revision,
  projection_revision, updated_at
```

`TerminalFrontmostPoller` is a machine-scope worker. At the shared presence
cadence (one second while any hosted session is live, five seconds otherwise),
it enumerates supported terminal applications, records whether a terminal app
is frontmost into reserved device `terminal`, and separately records which
verified provider tab is focused. Frontmost updates
`device_presence.last_active_at` for notification routing; tab focus updates
only the relevant presence/suppression fact. Enumeration failure produces
`unknown` and never manufactures activity. HTTP callers cannot invoke this
trusted write path or impersonate device `terminal`.

Every tab-paint reconciliation writes an attempt row for `applied`,
`skipped_already_verified`, `skipped_unreachable`, or `failed`, even when the
presentation state row is unchanged. Dedup compares against the last
successfully verified painted presentation, never the last requested color.
The stored verified state includes active and inactive-tab colors; inactive
color is mandatory because reading another tab's state is the feature's
purpose.

Provider adapters expose:

```text
CommandVocabulary.list(workspace, actor_key) -> CommandVocabularySnapshot
```

The snapshot contains curated built-ins and workspace commands/skills with
`name`, `description`, `argument_hint`, `source`, and `availability`. Host and
actor vocabularies are not concatenated: a scoped view asks for exactly its
actor/host vocabulary. A host with none returns an empty list, not the lead's
commands. Changes advance a vocabulary revision and emit
`provider.command_vocabulary.changed`. The current complete result is durable
in `command_vocabulary_snapshots`, keyed by provider/target/workspace/actor
scope with a sealed payload reference, freshness, and monotonic revision. A
complete scan advances the pointer; a partial/unavailable scan retains the
prior payload as stale.

Ghost-suggestion **acceptance** is entirely client-side; its observed text
arrives through the ephemeral live-facet SSE plane in Sections 38.22 and 40.8.
It is offered only when attention is settled, no interaction/modal is open,
and the relevant draft is empty. Accept copies the suggestion into the surface
composer; it does not type into the provider TUI and does not create a Node or
delivery Operation until the user explicitly sends it.

Web extension contributions use a registered DTO:

```text
SurfaceContribution
  extension_id, contribution_kind, placement, entity_type, entity_id,
  badge, payload, source_revision, schema_version
```

The contribution and its badge are produced from one projection row and one
source revision, so SSE cannot pair a new badge with an old payload. Provider
observers publish typed extension facts through `ExtensionFactSink`; provider
adapters never import a web presenter. `surface_contributions` stores this
complete DTO, projection revision, and tombstone under
`(extension_id, placement, entity_type, entity_id)`. Badge and payload update
in one row/transaction before the registered SSE event.

### 38.11 Exclusive input ownership and daemon-authored TUI drafts

One interactive AgentSession has one physical provider input channel. The
durable owner is:

```text
agent_session_input_occupancy
  agent_session_id         primary key
  occupancy_kind           free | interaction | daemon_draft | unknown
  interaction_operation_id nullable
  tui_draft_id              nullable
  revision                  non-negative integer
  observed_at               timestamp
  provenance_id             required
```

While occupancy is `interaction`, message send, quick command, generic
interrupt, rewind, model/effort change, and direct text paste return
`409 interaction_owns_input`. The only allowed controls are the typed response
for that exact interaction and a provider-specific cancel explicitly declared
safe for that interaction kind. Digits, Escape, or pasted text are never sent
through a generic control while an interaction is open.

While occupancy is `unknown`, every input-producing or destructive control
fails closed with `409 input_occupancy_unknown`. The only allowed action is the
registered read-only occupancy/modality reconciliation probe. Positive probe
evidence must compare-and-set a new occupancy revision before any waiting
control is retried; controls are never queued behind unknown state.

Text placed into the provider input box by Baqylau is stored separately from
surface drafts:

```text
tui_drafts
  id, agent_session_id, text_ref, line_count, clear_extent_lines,
  source_operation_id, state, revision, created_at, consumed_at, provenance_id
```

States are `occupying | consuming | consumed | cleared | unknown`. Interrupt
take-back and rewind restore create an `occupying` row and set input occupancy
to `daemon_draft`. The next send must compare the intended message with this
row and replace/clear the existing provider input instead of appending. Clear
extent is exactly the stored physical line count, bounded by the adapter's
declared `max_safe_clear_lines`; a larger draft requires verified chunked
clears or returns `409 draft_clear_unverified`. Successful provider acceptance
atomically marks the TUI draft consumed and frees occupancy.

An observed terminal draft never overwrites a daemon-authored TUI draft unless
the screen probe proves an input exists and its key matches the transcript
text. The comparison key removes all whitespace and uses the first 40
characters, so wrapping and a clipped tail do not create a false mismatch. The
screen supplies existence only; the provider transcript supplies authoritative
text/revision. Unreadable remains unknown. This record is provider-input
occupancy; `input_buffers` remain user-editable surface state.

### 38.12 Interaction model and driving rules

`interaction_details` is expanded to preserve the complete outcome:

```text
  operation_id
  interaction_kind        question | permission | plan | confirm
  external_key
  prompt_ref
  options_ref
  plan_ref                 nullable Resource
  response_ref             nullable
  response_revision        non-negative integer
  state                     open | partially_answered | submitting |
                            answered | declined | dismissed | expired | lost
  verdict                   nullable approved | changes | rejected | confirmed |
                            denied | answered | dismissed
  edited                    nullable boolean
  current_question_index    nullable non-negative integer
  answered_question_count   non-negative integer default 0
  total_question_count      nullable non-negative integer
  driver_layout             nullable provider-layout identifier
```

Plan `changes` feedback is stored in `response_ref`; plan text is retained from
the original tool-use record in `plan_ref`. Approval, changes, and rejection
remain distinguishable even when the provider emits no response body.

Question drivers are forward-only. Before every keystroke they reread the
current screen/native interaction and compute the difference between desired
and observed state. For Claude Code multiselect dialogs, Space toggles only an
option whose current selected state was positively observed; Enter submits.
The provider layout manifest owns these measured keys and a byte/screen fixture
must prove them for every supported build; no blind toggle is allowed. A
partially answered dialog persists its current question and resumes from the
provider's current position.

A typed answer that a provider layout can express only as decline plus normal
message performs two explicit Operations: the interaction transitions to
`declined`, then a linked `message_delivery` sends the answer. The response
reports both operation IDs. This is selected by the provider/layout capability,
not guessed by the general application service.

A stale `response_revision` returns `409 interaction_revision_conflict`. A
fresh revision is still insufficient if the provider's current dialog identity
or position differs; that returns `409 interaction_moved` with the new safe
snapshot. A failed ask/plan/confirm drive leaves the dialog exactly as found.
The generic cancel/Escape key is forbidden as cleanup because it may decline or
reject. Rewind is the sole initial driver whose declared bail action is Escape
to close the rewind menu it opened.

The initiating click is consent for the provider's model/effort switch
confirmation and for a rewind mode that the API explicitly described. When a
provider shows its expected confirmation menu immediately, the adapter may
verify and press Yes within the same Operation. When the command is queued
mid-turn, the Operation records `confirmation_state=expected_after_queue`; it
does not wait for a menu that cannot exist yet. The later verified menu is
answered by the same Operation after queue delivery. An unrelated/changed menu
is never answered. The surface is not asked for a second consent.

### 38.13 Prompt take-back, rewind, and provisional branch correction

A provider may discard a prompt before a replacement sibling exists. Nodes
therefore gain:

```text
  branch_visibility        normal | suspect_retracted | superseded
  branch_evidence_revision integer
```

Verified take-back sets `suspect_retracted`. Read projections hide it from the
live branch but retain it in history with an explanatory marker. The flag is
self-correcting: if any later native record descends from that prompt, ingestion
atomically returns it to `normal`; if a sibling prompt arrives, normal branch
selection marks the old subtree `superseded`.

Claude rewind has an evidence gap between driving the in-memory menu and the
next written prompt. During that gap:

```text
rewind_details
  operation_id, requested_mode, effective_mode, checkpoint_external_id,
  state, pre_rewind_head_id, provisional_head_id, restored_draft_id,
  provider_evidence_state, created_at, resolved_at
```

`state` is `driving | provisionally_applied | confirmed | degraded | failed |
indeterminate`. A verified menu result moves to `provisionally_applied` and
sets `provisional_head_id` for the live read view without rewriting the durable
Conversation head. The next provider record either confirms the fork and moves
the durable head or contradicts it and clears the provisional view. Sending in
this window uses the provisional checkpoint and is the action expected to
produce confirming native evidence.

Requesting `both` at a checkpoint with no code change returns
`effective_mode=conversation`, `state=degraded`, and a named
`workspace_restore_not_applicable` explanation;
it is not an error. A provider without a programmatic restore API never claims
confirmed rewind before native evidence exists.

Branch selection uses these exact rules:

- among prompt-bearing siblings, the last by provider source order is live;
- a dead prompt removes its whole descendant subtree from the live view;
- `_prompt_bearing` distinguishes actual prompt text from tool-result/user
  wrapper records; and
- an arriving record whose parent is an ancestor of the current head is valid
  fork evidence and bypasses ordinary expected-head rejection through a named
  `branch_reselection` command.

Compaction revert is detected from native parent links, even when no hook or
new compaction record exists. Branch-sensitive projections carry the native
index revision they were built from. Discovery of a late revert increments the
Conversation revision, corrects the head, invalidates affected projections,
and emits amendments/resnapshot as required. The UI marks the interval before
detection as `branch_freshness=unverified`; no bounded scan may claim that a
revert is impossible.

An interrupted compaction may omit PostCompact. `compaction` Operations have an
`abandoned` terminal result reached only by provider evidence or a read-side
expiry rule registered for that provider. The display latch expires on read
independently of Operation closure. Clearing a display latch does not mark the
Operation successful.

### 38.14 Control telemetry, input primitives, and truthful message states

Every surface emits typed frontend telemetry. Control attempts keep their
paired table because unmatched `begin` is a correctness signal; the remaining
families use `surface_telemetry`:

```text
surface_control_attempts
  id, principal_id, device_id, surface_id, gesture, client_attempt_id,
  conversation_id, agent_session_id, phase, http_request_id, error_code,
  client_timestamp, received_at, metadata

surface_telemetry
  id, principal_id, device_id, surface_id, client_record_id,
  family                 sse_lifecycle | js_error | js_rejection | boot |
                         notification_receipt | attachment_paste
  event_name, conversation_id, agent_session_id, conn_info,
  client_timestamp, received_at, payload
```

`phase` is `begin | ok | fail`. `POST /api/v1/client-telemetry` accepts bounded
batches of at most 100 union records. Control records are idempotent on
`(surface_id,client_attempt_id,phase)`; other records are idempotent on
`(surface_id,client_record_id)`. Every record carries the same bounded
`conn_info` snapshot: daemon boot ID, API build, SSE connection generation,
transport kind (`loopback|tunnel|remote`), online state, page visibility, and
current Conversation if any. Required event names are `sse.open`, `sse.drop`,
`js.error`, `js.reject`, `boot`, `notify.recv`, and `attach.paste`; unknown
names are rejected rather than silently becoming arbitrary logs. A `begin`
without `ok` or `fail` after 60 seconds is a queryable anomaly proving the
gesture may never have reached the daemon. `sse.drop`, reducer exceptions, and
`notify.recv` with `shown=false` are independently queryable anomalies. Beacon
delivery is best-effort and the client also retries unacknowledged telemetry at
its next connection; telemetry never causes the control itself to be retried.
Rows retain for seven days.

`TerminalInput` is three capability methods:

```text
send_keys(binding, keys[])          # discrete, unacknowledged key events
send_raw_text(binding, bytes)       # permitted only where byte loss is safe
send_bracketed_paste(binding, text) # atomic text insertion with verified mode
```

Text delivery uses bracketed paste. Raw text is not used for messages or
commands. Every input-producing request freshly resolves and verifies the
terminal binding immediately before the effect; a cached window ID is never
trusted for a write. Typing is classified as destructive because a stale
binding can corrupt another session.

Bracketed paste sends the opening marker, UTF-8 body, and closing marker as one
write, then sends the submit carriage return outside the paste. Clearing an
existing input waits `CLEAR_GAP_S=0.15` seconds before the paste; without that
gap terminals have been observed to turn `test` into `t`. `send_raw_text` is
therefore forbidden as a shortcut even when a one-byte smoke test appears to
work.

The shared screen-motion probe takes two ANSI-neutralized screen captures 0.5
seconds apart. Equal captures mean `not_moving`; unequal captures mean
`moving`; capture failure is `unknown`. It never searches for literal spinner,
busy, or success marker strings. The interrupt driver is allowed one initial
Escape press plus up to four re-presses (five total), one at a time, only while
native attention is busy or the screen probe still reports movement. A
red/asking tab is explicitly excluded: it owns a dialog and Escape may decline
it. Each press is followed by a provider
event-loop settle and a new probe; this permits the first press to leave a vim
insert mode without falsely claiming the turn stopped. It never presses Escape
on an idle/unknown tab and never sends another press after queued content has
started delivery.

Provider input mode is stateful across gestures. After any failed or partial
drive, the adapter records durable `input_mode=unknown`; the next gesture must normalize
and verify the provider mode using its own safe protocol before typing. It may
not assume the prior gesture left the TUI in normal insert mode.

```text
agent_session_input_modality
  agent_session_id primary key
  mode             insert | normal | interaction | command | unknown
  revision         monotonic
  source           probe | verified_effect | failure | imported
  observed_at
  provenance_id
```

Unknown modality blocks typing even when input occupancy says `free`.
Normalization records the attempted sequence as an Operation/effect, then only
a different positive probe or provider receipt can set a known mode.

`message_delivery` may enter `queued_at_provider` only from an independent
screen/native queue probe captured before the paste. Derived attention and
motion caused by Baqylau's own paste are inadmissible. Interrupt retry checks
the delivery history: `queue_drained` evidence outranks a stale screen, and no
additional Escape may be sent after queued content began delivery. Accepted,
queued, delivered into context, and semantically completed remain different
states.

### 38.15 Presence, in-page toasts, push subscriptions, and public links

Presence keeps “here now” separate from “last seen on this device”:

```text
presence_sessions
  id, principal_id, device_id, surface_id, conversation_id,
  viewing_now, device_active_now, terminal_tab_focused_now,
  connection_generation, current_connection_id,
  started_at, last_heartbeat_at, ended_at, revision

device_presence
  principal_id, device_id, device_kind, last_seen_at, last_active_at,
  last_conversation_id, routing_channel, platform, revision
```

An SSE disconnect, page visibility loss, explicit away signal, or heartbeat
expiry ends `viewing_now` and `device_active_now` immediately. `POST
/api/v1/presence-sessions/{presence_session_id}:away` is the explicit end-of-presence
operation. It does not erase `device_presence.last_seen_at`; freshness for
routing and current suppression are different facts.

Terminal frontmost and terminal-tab-focused are also different facts:
frontmost participates only in device routing; tab-focused participates only
in suppression for that Conversation. Terminal signals do not create a
machine-wide device-active twin.

The server emits `notification.toast` on the system SSE feed with
`intent_id`, Conversation summary, kind, deep link, and transition revision.
The page shows it only while visible and focused. This in-page toast is the
premise for suppressing a duplicate external notification on an active device.
The global notification toggle gates both toast publication and external alert
arming in the same policy transaction.

Web Push uses durable subscriptions:

```text
push_subscriptions
  id, principal_id, device_id, endpoint, p256dh, auth_secret_ref,
  user_agent, created_at, last_success_at, failure_count, state

push_key_material
  key_id primary key, public_key, private_secret_ref, state,
  created_at, retired_at
```

`POST /api/v1/push-subscriptions` registers/upserts one subscription and
returns its device and key IDs. `DELETE /api/v1/push-subscriptions/{id}` removes
it after principal/device ownership verification. A 404 or 410 from the push
service marks the subscription expired and removes it from selection. The VAPID
keypair is backed up and stable across upgrades; rotation is an explicit
workflow that reports how many subscriptions will be orphaned and requires
re-subscription.

The sole owner of the notification origin is the machine configuration key
`notifications.public_base_url`, projected with a revision into
`notification_origin_config`; it is not a per-principal preference and cannot
be changed by `PUT /notification-settings`. It is never inferred from the
loopback bind address. It is validated as an externally usable HTTPS origin
except in a declared local-development profile. Deep links use
`?s=<conversation_id>` rather than a URL fragment so Telegram and similar
channels preserve the target. Missing production public origin makes external
notification health degraded and prevents delivery; it never sends a
127.0.0.1 link.

The reserved device ID `terminal` can be written only by the trusted terminal
adapter. HTTP clients cannot register or impersonate it.

Notification configuration is durable and revisioned:

```text
notification_settings
  principal_id, enabled, toast_enabled, web_push_enabled, telegram_enabled,
  telegram_always, resolve_push_enabled, pre_alert_delay_seconds=0,
  done_settle_seconds=20, escalation_seconds=300,
  retractability_seconds=86400, revision, updated_at

notification_origin_config
  singleton=1, public_base_url, revision, updated_at

conversation_notification_mutes
  principal_id, conversation_id, muted, revision, updated_at
```

The machine configuration service validates the HTTPS origin. The settings
service validates non-negative timing; channel secrets remain CredentialPort
references. `pre_alert_delay_seconds` is the global pre-arm debounce inherited
from `CLAUDE_DASH_NOTIFY_DELAY_S`. `retractability_seconds` defaults to 86,400
seconds and is capped by each adapter's declared remote deletion ceiling;
Telegram declares 172,800 seconds. A delivery stores
`expires_at = sent_at + min(setting, channel_ceiling)`. A mute row is read at
every send and escalation as required below.

### 38.16 Alert state machine and routing

Alert truth, timer/arm state, and delivery state are separate axes:

```text
notification_intents
  id, conversation_id, agent_session_id, attention_transition_id, kind,
  truth_state             true | false | unknown
  arm_state               armed | holding | held | disarmed | expired
  hold_audit_state        unrecorded | recorded
  due_at                  nullable
  escalation_due_at       nullable
  policy_version, cause, created_at, updated_at

notification_deliveries
  id, intent_id, stage, channel_id, device_id, effect_attempt_id,
  external_handle_ref, delivery_state, retractable, expires_at,
  sent_at, retracted_at
```

An intent can therefore have a sent push delivery while its arm remains active
for possible Telegram escalation. `delivery_state` is `pending | sending |
sent | failed | unknown | retracting | retracted | expired`. `unknown` is the
terminal honest result of an indeterminate non-reconcilable send and must never
be retried as though no external action occurred. `stage` is 1 or 2.

The clocks are deliberately different:

- the global feature toggle is read when arming; off means no intent and no
  external delivery;
- a per-Conversation mute is read at every send/escalation attempt, so muting
  an already held alert still disarms it;
- a `done` intent uses a 20-second settle period and checks “was this seen?” on
  every one-second scan during the period;
- an `asking` intent checks presence at send time; if the user is currently
  viewing/answering, it enters `holding`, records the transition once, then
  `held`; its general arm becomes `suspended` with no due time and the same
  revision is copied to `notification_intents.arm_revision`; presence end
  re-arms it with `due_at=now` if the question is still true; and
- while held, expensive terminal answering probes are suppressed until a
  presence change or truth change wakes the intent.

For `done`, the due time is `transition_time + max(pre_alert_delay_seconds,
done_settle_seconds)`, never the sum of the two delays. The held-current flag
and the fired-once audit latch are separate stored fields: leaving presence
clears current holding and re-arms the intent without erasing the fact that a
hold transition was already audited. Suppressed terminal-answer probes resume
when that re-arm occurs.

Seeing a question does not disarm it. Answering, submitting text into the
question region, editing a non-faint terminal input on the relevant green tab,
attention leaving the watched state, session end, mute, or explicit reaction
can disarm it. Provider-specific red ask-region difference and green
typed-versus-faint probers are registered consumers of alert cancellation.

Stage-1 routing chooses exactly one device from the principal's MRU
`device_presence` map. Newest activity wins; browser wins an exact timestamp
tie. If the winner is a browser with a live push subscription, stage 1 is Web
Push. If the winner is `terminal`, or no selected browser can receive push,
stage 1 is Telegram. There is no fan-out-to-all default.

A stage-1 push keeps the arm active and schedules stage-2 Telegram after 300
seconds. A reaction, truth change, viewing event, mute, or answer before then
cancels escalation and retracts the delivered push when supported. A stage-1
Telegram has no escalation because it already reaches all configured Telegram
devices. The explicit `telegram_always` policy sends both at stage 1 and records
that policy choice.

Every routing decision writes one `notification_route_decisions` row containing
the winner, every candidate, freshness, presence state, subscription state,
selected channel, exclusions, and policy version. Cancel reasons use this
evidence-strength precedence: `truth_changed`, `answered`, `session_ended`,
`muted`, `tab_moved`, `composing`, `tab_focused`, `web_viewing`,
`device_active`, `expired`, `unknown`. Thus typing or moving the tab away from
the alerted state can replace the weaker fact that the page is merely open.
The first applicable higher-priority reason replaces a lower one. Retraction is
not inferred from that ordering; the policy table is:

| Cancel reason | `asking` delivered alert | `done` delivered alert |
|---|---|---|
| `truth_changed`, `answered`, `session_ended`, `muted` | retract while channel permits | retract while channel permits |
| `tab_moved`, `composing` | retract while channel permits | retract while channel permits |
| `viewing`/tab focused | do not disarm or retract merely because the question was seen | mark seen and retract while channel permits |
| `device_active` | never retract | never retract |
| `expired`, `unknown` | no remote retraction attempt | no remote retraction attempt |

The executable owner of this matrix is the seeded, immutable
`notification_retraction_policy(cancel_reason,notification_kind)` table in
Section 40.7. Presence evidence normalizes generic `viewing` into
`tab_focused` or `web_viewing` before lookup. There is exactly one row for
every active reason/kind pair; startup degrades notification health if the
registry and table sets differ. Core reasons are seeded and immutable.
Provider extensions register a namespaced reason in
`notification_cancel_reason_definitions` and insert its policy rows in the same
installation transaction; the schema does not CHECK provider reason strings
against the core list. The delivery transaction reads
`retract_delivered` and `marks_seen` from that row rather than duplicating the
matrix in channel code.

A delivered alert may be retracted for `answered` only from durable provider
records or bounded parsed ask/input-region evidence captured for the live
Conversation. Long-lived delivered-alert sweeps never screen-scrape once per
second. Screen probes are used only while an alert is currently armed/held and
are stopped after delivery unless a registered bounded reaction operation
explicitly requests one.

Machine-wide device activity affects routing eligibility only. It never marks
a Conversation alert seen and never retracts a delivery. This rule is tested
with an active unrelated device and an unseen alert in another Conversation.

The general `arms` table owns scheduling; notification intents reference an
arm ID rather than owning a second independent timer. `due_at` and
`escalation_due_at` above are denormalized query fields maintained by the same
transaction and checked against `arm_revision`. Holding uses the arm's
`suspended` state rather than inventing a notification-private timer.

Arm creation has one mandatory write order. `AlertArmSpec` contains
preallocated `intent_id` and `arm_id`. In one `ConversationUnitOfWork`,
`AlertStore.arm_tx` first inserts `arms(id=arm_id,
owner_kind='notification_intent',owner_id=intent_id,revision=0)`, then inserts
`notification_intents(id=intent_id,arm_id=arm_id,arm_revision=0,due_at=<same>)`,
then writes routing/feed outbox rows. The intent trigger validates the existing
arm. Failure of either insert rolls back both. No caller may insert the intent
first or construct these two rows through separate store calls.

Push retraction uses a silent resolve push only while the subscription's
resolve budget is healthy: at most three silent resolve attempts in a rolling
24-hour window, and suspension after two consecutive resolve failures or one
platform `userVisibleOnly` refusal/visible-placeholder report. The subscription
stores window start, attempt/failure counts, state, last error/time, and
`pending_stale_cleanup`. A successful foreground subscription refresh resets
the consecutive failure count but not the rolling attempt count. On exhaustion
or refusal, `sweepStale` removes local state on the next foreground connection,
clears the pending marker, and records `remote_retraction_unavailable` on the
delivery. Telegram/fire-and-forget delivery stores `retractable=false` when no
remote handle exists; the UI does not promise retraction.

### 38.17 Usage facts, exact deduplication, cost, and stable rollups

For `temporality=message_snapshot`, `dedup_key` is the provider message ID.
One message can emit one record per content block with repeated usage and
growing totals. The durable credit state is:

```text
usage_credit_state
  source, ledger, query_source, usage_scope_key, dedup_key,
  input_tokens, output_tokens, cache_read_tokens,
  cache_create_5m_tokens, cache_create_1h_tokens,
  cache_create_unclassified_tokens, vendor_cost_minor, vendor_currency,
  source_position,
  PRIMARY KEY(source, ledger, query_source, usage_scope_key, dedup_key)
```

For each incoming snapshot and each numeric field, credited delta is
`max(0, incoming - already_credited)`. The same transaction appends the source
fact, increments that source's durable rollups by those deltas, and replaces the
credit state with the per-field maximum and newest source position. This works across
batches and restarts; coordinator memory is not authoritative. Keep-first and
sum-all are forbidden.

OTLP exposes one combined cache-creation value. Such facts write the total to
`cache_create_unclassified_tokens` and leave both 5-minute and 1-hour fields
unknown; they do not pretend the total is all 5-minute. Transcript facts may
populate the split. Cost projection returns a range or `unknown` when an
unclassified amount could receive different prices.

`usage_facts` also stores nullable `vendor_cost_minor`, `vendor_currency`, and
`vendor_cost_source`. The headline provider cost uses the provider's own cost
metric when present and labels it `vendor_reported`. Baqylau's price-table
calculation is returned alongside it as `calculated_cost`; disagreement is
preserved and queryable, not silently reconciled.

Prunable facts are not the owner of lifetime totals. Canonical rollups remain
separate by source; an authority pointer selects exactly one source for a
billing scope so a late OTLP export replaces rather than adds to transcript
fallback:

```text
usage_source_authority
  usage_scope_key, ledger, query_source, selected_source, revision,
  evidence_ref, selected_at

usage_source_rollups
  usage_scope_key, source, ledger, query_source, provider_id, model, pricing_epoch,
  input_tokens, output_tokens, cache_read_tokens,
  cache_create_5m_tokens, cache_create_1h_tokens,
  cache_create_unclassified_tokens, vendor_cost_json,
  error_count, updated_at

daily_usage_source_rollups
  day, usage_scope_key, source, ledger, query_source, provider_id, model, pricing_epoch,
  token columns..., vendor_cost_by_currency, error_count
```

`usage_scope_key` is non-null and canonical:
`conversation:<uuid>`, `session:<uuid>`, `account:<uuid>`, or
`machine:<machine-id>`. Source precedence for billing is provider cost/usage,
then OTLP, then transcript, then imported. Per-actor display can select a
different source but never combines it with billing. When a higher-authority
source first becomes usable, one transaction updates its source rollup and the
authority pointer; no negative correction of the old source is needed because
queries join only the selected source. Both sources remain diagnosable.

`query_source` is a required dimension with values
`main | subagent | auxiliary`. It is present on facts, credit state, source
authority, source rollups, daily rollups, scoreboard inputs, query DTO keys,
and every uniqueness key. `auxiliary` is not folded into `main`; it remains
queryable after raw fact retention expires. When an exporter lacks the
attribute, provider-specific mapping may infer it only from a registered
process/task identity; otherwise the fact is `auxiliary`, the conservative
bucket, with provenance.

All source adapters normalize token fields before authority selection. The
canonical `input_tokens` is gross input and includes cache creation. For a
scoreboard, `fresh_input_tokens = max(0, input_tokens -
cache_create_5m_tokens - cache_create_1h_tokens -
cache_create_unclassified_tokens)` and
`total_tokens = input_tokens + output_tokens + cache_read_tokens`. Transcript
gross values pass through; OTLP net input adds its cache-creation values;
providers such as Codex that report no cache creation write zero only when the
provider contract proves zero, otherwise unknown. An authority switch cannot
change the meaning of a displayed column.

Rollups update in the same transaction as accepted usage credit and are never
recomputed from prunable evidence. Stats/Insights joins the authority row to
source rollups. Calculated cost is never persisted in an aggregate: it is
computed at read time from provider/model/pricing-epoch token rows, so price
table corrections heal history. Only provider-reported vendor cost is stored.
A repair
can rebuild them only while complete source coverage is proved; otherwise it
returns `rebuild_evidence_incomplete`.

### 38.18 Quotas, account migration, fallback models, and continuation

Rate-limit and logout triggers are provider events, not inferred percentage
thresholds:

- Claude `StopFailure(error=rate_limit)` creates the limit-hit fact;
- Claude `StopFailure(error=authentication_failed)` creates the logged-out
  fact; and
- status-line/OAuth percentages provide windows and selection context but do
  not manufacture either trigger.

Quota source capabilities distinguish push and pull:

```text
UsageSource.ingest_push(payload, source_context)
UsageSource.fetch(account, scopes[]) -> QuotaSnapshot
```

Claude status-line stdin is the push source for 5-hour/7-day windows. The
credentialed OAuth adapter is a separate pull source for model-scoped weekly
windows. Each stores freshness and errors independently. Reset fallback is
allowed only from another observation with the same provider, account, scope
key, model scope, and window duration; a 5-hour cadence may never fill a weekly
reset.

Status-line timestamps named `resets_at` are seconds unless the numeric value
is greater than `1e12`, in which case they are milliseconds. A null/missing
window never overwrites a previously good window; it only changes freshness.
Unknown provider window keys are accepted only after schema-key hygiene and at
most 32 generic windows per account/session, with overflow diagnosed.

The parsed limit message text is a third evidence source alongside structured
StopFailure and quota windows. Its registered parser returns `limit_model` and
timezone-aware `limit_reset`; failure to parse the timezone leaves reset
unknown rather than a permanently lit estimate. The hit is durably stamped
before checking `CLAUDE_RELIMIT`, before target selection, and before any
effect. Automatic retries have a per-session `COOLDOWN_S=600`. The continuation
nudge explicitly mentions the model downgrade when the effective family
changed.

Automatic migration policy is exact:

1. Trigger on the authoritative limit/logout event.
2. Mark the current account/model scope unavailable.
3. Try the same current model on every eligible account in target-selection
   order.
4. If none can run it, try model families in `fable -> opus -> sonnet` order,
   never below Sonnet, exhausting every eligible account at one model before
   downgrading again.
5. Respect whether the limit is account-wide or model-scoped; a model-scoped
   hit disqualifies only that model family on that account.
6. Prepare target arguments/resources without launching, request source park,
   wait for verified source park/end, then launch/resume and verify the new
   attempt before activation. Automatic migration never uses the logged-out
   bypass. The new attempt includes the fixed continuation nudge:
   “Continue where you left off — the previous turn was cut off by the
   account's rate limit and this session was resumed on another account.”
7. The nudge is part of the relaunch input and is delivered only by automatic
   migration.

`CLAUDE_RELIMIT=0` still records the limit-hit fact and selection evidence but
ends with `migration_disabled`; it suppresses only the migration effect.

Manual migration is a distinct API/workflow, not a flag on automatic policy:

- explicit user choice outranks utilization ceilings but still rejects an
  account with authoritative logged-out or relevant limit evidence;
- it sends no continuation nudge;
- the migrator announces the change;
- it can launch from a live provider state database after the terminal window
  disappeared; and
- if authentication failure means no SessionEnd will arrive, verified
  logged-out evidence licenses bypass of the normal wait-for-park step.

After activation, automatic migration re-drives the interrupted turn through
the nudge; manual migration does not. Every path returns requested and
effective account, model, effort, mode, and execution target plus every
rejected candidate and reason.

Provider-side artifact relocation follows Section 38.3 throughout migration.
An actor child's path/cwd never moves the host grouping row.

### 38.19 Audit, anomalies, health, and diagnostic artifacts

The anomaly catalogue is a registered application artifact at
`src/baqylau/application/diagnostics/anomaly_catalog.py`. Each definition has:

```text
AnomalyDefinition
  code, title, severity, explanation, sql, parameters, owner, fixture_ids,
  introduced_by_bug, remediation
```

All JSON-reading SQL guards with `json_valid` before extraction. A malformed
evidence row may itself be reported but cannot abort the complete anomaly run.
A commit that introduces a new known failure signature must add its definition,
why-comment, fixture, positive test, and nearby negative test in that commit.
`baqylau anomalies` enumerates or runs the catalogue; the HTTP diagnostics API
returns the same registered codes.

There is no unrestricted `sql-write` command. Its successor for an
unanticipated fix is `baqylau repair scaffold <code>`: it creates a disabled
repair definition and demands a typed argument schema, preview query, bounded
application method, verification query, rollback class, fixtures, and operator
reason before the definition can be enabled. Execution then uses the ordinary
repair Operation, backup/evidence requirements, and immutable before/after
record. Emergency arbitrary SQL remains outside the product contract and
requires stopping the daemon plus an operator-managed database copy.

The checked-in anomaly catalogue is the source of the DTO explanation and
remediation; `anomaly_definitions` mirrors those fields and their digest for
startup equality checking. Phase 0 has a manifest row for every legacy
`ANOMALY_SECTIONS` entry with disposition `ported` or
`obsolete_by_construction` and a reason/fixture. Missing classifications fail
the behavioral-parity gate.

The triage playbooks live with the code they interpret:

```text
docs/operations/audit-debug.md
docs/operations/global-errors.md
src/baqylau/application/diagnostics/schema_catalog.py
tests/diagnostics/fixtures/
```

Architecture tests fail if a schema/table change is not reflected in the schema
catalog and playbooks. `baqylau otel <agent-session-id>` is a required CLI
command for source comparison and cost diagnosis.

`CLAUDE_AUDIT=0` disables optional diagnostic provenance and surface telemetry
but never canonical ingestion, operations, usage rollups, control attempts, or
security evidence required for correctness. Product health errors, anomaly
detection, the scorebar warning chip, and the once-per-error warning line stay
enabled; they are not optional audit provenance. The effective setting is visible
in health. As already decided, no edge audit spool exists: daemon outage can
lose all observations during that interval and startup records an
`ingestion_gap` with known start/end times, never invented missing rows.

Health stores raw error facts. Benign-signature suppression is applied at read
time from a versioned registry, including to already existing rows. The warning
light query computes the effective count under the current registry; adding a
suppression can therefore turn the light off without deleting or decrementing
raw facts. Materialized raw counters may only accelerate the unsuppressed scan
and are never shown directly.

Machine-global anomalies are included in each relevant Conversation's warning
count but surfaced as ambient pane messages at most once per Conversation. The
`last_surfaced_global_error_id` checkpoint advances before emission, explicitly
preferring at-most-once ambient warning over repeated noise.

Warning-line emission has its own process-local recursion guard in addition to
mapper recursion protection. It claims an error ID before producing the
one-line `⚠` Activity item, and the warning producer is excluded from the error
poll that triggered it. A failure while formatting/emitting the warning is
stored in health but cannot recursively create another warning line in that
process.

Every once-only hand-off records both production and consumption. Atomic take
writes a `consumed_at`, `consumer_operation_id`, and decision row in the same
transaction. An unconsumed-record anomaly is therefore possible without
reconstructing application memory.

Identity repair reads use one registered `AgentSessionIdentityResolver` that
follows active alias/split/merge links. The schema includes indexes for both
directions and every storage method accepting a provider session ID calls this
resolver; ad-hoc `sid_chain` queries are forbidden.

### 38.20 Parking while background work remains

Parking the interactive provider host does not imply that child processes,
background commands, or monitors have stopped. AgentSession state therefore
separates `host_state` from `work_state`:

```text
agent_session_lifecycle
  agent_session_id, host_state, work_state, park_requested_at,
  host_ended_at, work_drained_at, freshness, provenance_id
```

`host_state` is `starting | live | parked | ended | lost`; `work_state` is
`active | drained | unknown | lost`. Source readers continue ingesting durable background output
after host parking while liveness or source evidence says work is active. The
Conversation is shown as parked with active background work. Archive/retention
cannot seal those Streams until work becomes drained/lost or the user executes
an explicit force-abort command that records the dropped source readers.

### 38.21 Materialized activity, correction, and bounded rebuild

Materialized Activity remains required. Every item stores:

```text
materialized_activity
  conversation_id, head_node_id, actor_track_id, generation,
  position_key, local_sequence, item_type, item_id, group_id,
  activity_class, register, audience, source_revision, payload_ref

activity_projection_state
  conversation_id, actor_track_id, head_node_id, active_generation,
  building_generation, freshness, source_revision, invalid_from_position,
  revision
```

Late branch evidence, rewind, compaction revert, or child output invalidates
from the earliest affected `position_key`, not blindly from row one. Rebuild
runs in transactions of at most 500 items or 25 ms of writer time, whichever
comes first. Every query joins `activity_projection_state.active_generation`;
it never infers the active generation with `MAX`. Until complete, the old generation remains readable with
`projection_freshness=stale`; the new generation becomes active atomically only
after its terminal chunk and the pointer update commit together. A correction that can be expressed without
full rebuild emits `amend`, `move`, or `supersede`; otherwise it emits
`resnapshot_required`.

Attention is recomputed from the post-transaction canonical state. A closer
transaction releases slots, commits operation/stream state, then computes the
new attention from those committed-in-transaction values before emitting
projection/outbox work. It never rechecks against a pre-release snapshot.

### 38.22 Structural SSE and cursor replay

The retained structural feed is kept. It is durable delivery history, not
canonical truth. Publication through the transactional outbox is required even
though clients can recover by fetching a snapshot; this cost is an accepted
reliability choice.

The same SSE connections also carry a separate **live facet plane** for facts
whose contract explicitly disappears on disconnect or daemon restart. Live
frames never enter `structural_changes`, the outbox, SQLite, or the Blob store;
they have no SSE `id`, no replay cursor, and no delivery guarantee. This is not
an edge spool or a second source of truth. The registered live frame set is:

| Live frame | Payload and client rule |
|---|---|
| `system.connection` | `daemon_boot_id`, `api_build_id`, `minimum_client_build_id`, `connection_generation`; first frame on every SSE connection |
| `client_upgrade_required` | required API/client build pair and reload URL; client stops reducers, reloads the versioned asset URL, and does not reconnect with the stale build |
| `live.suggestion.changed` | Conversation/session/actor, suggestion identity/revision, text up to 16 KiB, availability and observed time; replace the current ghost or clear it |
| `live.foreground.changed` | Conversation/session/actor, foreground-running boolean/unknown, observed time and live revision; display hint only, canonical Operation state wins when present |
| `live.compaction.changed` | Conversation/host session, host-only latch state and observed time; display hint only, compaction Operation remains durable truth |

The daemon owns one bounded in-memory `LiveFacetHub` per active Conversation.
It retains only the newest frame per
`(frame_name,agent_session_id,actor_key_or_host_scope)`,
caps total suggestion text at 256 KiB per Conversation, and evicts a suggestion
two seconds after its source stops reporting it. Slow subscribers drop live
frames before any durable feed work; they never backpressure ingestion.

Connection setup registers the durable and live subscribers before taking the
HTTP snapshot high-water mark. After the snapshot and `system.connection`, the
server sends one live snapshot containing the hub's current values, then normal
frames. A disconnect immediately clears all live facets in the client. A
reconnect to the same boot may repopulate them from the hub; a different
`daemon_boot_id` starts empty. Suggestion acceptance still follows Section
38.10: the browser may use the text as its own composer value, but never asks
the terminal driver to accept a stale provider ghost.

The durable event manifest contains the 37 feed/event pairs below. Live frames
are checked by a separate five-frame protocol manifest and tests for ordering,
disconnect clearing, restart clearing, size bounds, stale-client rejection,
and lack of SQLite/outbox writes.

There are three endpoints:

```text
GET /api/v1/system/events
GET /api/v1/events
GET /api/v1/conversations/{conversation_id}/events
```

All use `text/event-stream`. `/system/events` is owner/admin machine scope and
carries health, configuration, provider-edge, backend, and extension-install
changes. `/events` is principal scope and carries only that principal's
accounts, project groups, presence, notifications, vocabulary, contributions,
and Conversation overviews. The Conversation endpoint requires view authority
for that Conversation and carries its revisions, activity,
Nodes, Operations, interactions, usage, attention, and Stream metadata. Stream
bytes are fetched separately by revision and are never embedded in structural
SSE.

The cursor scope identity is `(scope_type, scope_id, principal_id,
authorization_revision)`. A cursor from another principal or from before a
permission change is wrong-scope and returns `409 feed_cursor_scope_mismatch`.
Rows are written to machine, principal, or Conversation feeds at the canonical
transaction; delivery never filters one shared machine row after cursor
assignment.

Browser SSE requires `X-Presence-Session-ID` and
`X-Presence-Connection-Generation`. Attaching a new socket compare-and-sets the
generation and stores its connection ID. Disconnect ends current presence only
if that ID/generation is still current; a superseded socket disconnect cannot
end its replacement. Reconnect has a five-second overlap grace, during which
the old socket remains non-authoritative but presence stays current.

Every composed snapshot response includes:

```json
{
  "snapshot_revision": 42,
  "feed_cursor": "opaque-scope-cursor",
  "data": {}
}
```

The snapshot transaction reads the data and current cursor from one SQLite read
snapshot. The client then opens SSE with `Last-Event-ID: <feed_cursor>`. The
server first replays events strictly after that cursor, then sends live events.
Each SSE frame has:

```text
id: opaque-scope-cursor
event: registered.event.name
data: {"schema_version":1,"scope_revision":43,"entity_id":"...",
       "previous_entity_revision":6,"entity_revision":7,
       "operation":"upsert","snapshot_url":"...","payload":{...}}
```

Cursor order is total only within one feed scope. Events are retained for 24
hours and at least the newest 100,000 events per scope; maintenance may discard
older events only after both limits are exceeded. Unknown, wrong-scope, or
expired cursors return HTTP `409 feed_cursor_expired` before SSE headers with a
fresh snapshot URL. Daemon restart preserves feed rows and cursors.

A subscriber queue holds at most 1,000 structural events or 4 MiB. Overflow
enqueues one `resnapshot_required` event when possible and closes the stream.
It never backpressures ingestion. A heartbeat comment is sent every 15 seconds;
absence for 45 seconds makes the client reconnect.

Client application rules:

1. Deduplicate by SSE `id`.
2. Ignore an already-applied event ID. For `upsert|delete`, require the locally
   applied revision to equal `previous_entity_revision`; a lower event is stale
   and ignored, a higher/gap event resnapshots `snapshot_url`. `delete` carries
   `{id,deleted_at,reason,revision}` and replaces the entity with a tombstone.
3. Apply `append`, `amend`, `move`, and `supersede` only when their stated base
   generation/revision matches; otherwise resnapshot.
4. On `resnapshot_required`, cursor expiry, schema-version mismatch, the
   previous-revision gap defined above, or three consecutive reducer errors, stop applying events,
   fetch a fresh snapshot, replace scoped state, and reconnect from its cursor.
5. Reconnect transport failures from the last successfully applied SSE ID.
6. Never infer missing Stream bytes from structural order. When Stream metadata
   reports a higher revision, fetch the missing byte/replacement operations by
   Stream revision.

The initial registered event set is:

| Feed | Event names |
|---|---|
| Machine | `system.health.changed`, `provider.changed`, `provider.edge.changed`, `backend.changed`, `execution_target.changed`, `extension.installation.changed`, `resnapshot_required` |
| Principal | `account.changed`, `quota.changed`, `project_group.changed`, `conversation.overview.changed`, `provider.command_vocabulary.changed`, `extension.contribution.changed`, `notification.toast`, `notification.changed`, `presence.changed`, `resource.changed`, `input_buffer.changed`, `resnapshot_required` |
| Conversation | `conversation.changed`, `actor_track.changed`, `node.appended`, `node.corrected`, `operation.changed`, `interaction.changed`, `activity.append`, `activity.amend`, `activity.retract`, `activity.move`, `activity.supersede`, `stream.changed`, `attention.changed`, `usage.changed`, `resource.changed`, `input_buffer.changed`, `view-mode.changed`, `extension.contribution.changed`, `resnapshot_required` |

Entity `changed/appended/corrected` payloads use the same DTO returned by their
owned HTTP query. Instruction payloads are exact exceptions:

| Event family | Required payload and reducer |
|---|---|
| `activity.*` | `generation`, `base_generation`, `item_id`, `item_revision`, `placement`, `anchor_id?`, `replacement_id?`; apply the named append/amend/retract/move/supersede reducer only when base generation matches |
| `stream.changed` | `stream_id`, `previous_revision`, `revision`, `byte_length`, `state`, `resync_required`; metadata reducer then fetches content when required |
| `notification.toast` | `intent_id`, `conversation_summary`, `kind`, `deep_link`, `attention_transition_revision`; display only if current visibility policy still permits |
| `provider.command_vocabulary.changed` | provider/target/workspace/actor scope, previous/current vocabulary revision and vocabulary snapshot URL; replace the whole scoped vocabulary |
| `extension.contribution.changed` | complete `SurfaceContribution` including previous/current source revision; upsert or tombstone its placement key |
| `view-mode.changed` | `agent_session_id`, `view_mode`, `previous_revision`, `revision`, `client_mutation_id`; apply unless it is this surface's already-applied mutation echo |
| `resnapshot_required` | `scope_type`, `scope_id`, `reason`, `snapshot_url`, `last_safe_cursor`; stop reducers, fetch, replace scope, reconnect |

Every event is registered with one applied-revision key. Entity events key by
`(event family, entity_id)`, activity by actor-track/generation/item, Stream by
Stream ID, vocabulary by provider/target/workspace/actor scope, and extension
contributions by extension/placement/entity. No client invents a reducer.

### 38.23 Dirty workspace state

Git branch/worktree identity and dirty status are separate. Branch/worktree
identity may be a durable observed workspace fact. Dirty status comes from one
named query adapter and is stored only as a three-valued cache:

```text
workspace_dirty_cache
  backend_id, workspace_ref, state, fingerprint, observed_at, expires_at,
  error_code, PRIMARY KEY(backend_id, workspace_ref)
```

`state` is `dirty | clean | unknown`; unknown is never rendered as clean.
`conversation_workspaces.dirty_fingerprint` is removed in favor of this cache.
The local adapter uses direct `.git` file reads for branch/worktree identity
and one sanctioned TTL-bounded subprocess for dirty status. Expiry returns
unknown and schedules refresh; it does not convert the cached value into a
domain fact.

`workspace_git_identity` stores branch name or detached SHA, discovered
worktree root, and owning repository root. Worktree identity groups by the
owner root while preserving the worktree path for execution. Dirty cache TTL
is exactly 10 seconds, aligned with the slow Conversation-overview SSE cadence.
Timeout/error stores and returns `unknown` until that TTL expires; it never
reuses the words clean/dirty from an older fingerprint as current truth. The
complete identity, dirty tri-state, and revision are projected into
`ConversationOverviewDTO`, avoiding a per-card query.

One `conversation_overviews.revision` covers the complete closed DTO. Any
visible field change increments it once in the projection transaction and
emits one complete replacement DTO. High-rate stream byte/context ticks are
coalesced to at most one overview revision per second per Conversation, but a
state, attention, title, resumability, warning, or deletion change flushes
immediately. The final value is never omitted; coalescing only replaces an
unsent intermediate projection request.

### 38.24 Fixed endpoint inventory

This section fixes methods, paths, workflow separation, and principal inputs.
Sections 38.36 and 38.38 are its field-level companion contract: they close
every named DTO, authorization class, status/error, limit, ordering rule, and
OpenAPI-generation mapping. The operation pairs in both sections must remain
exactly equal.

Common rules:

- JSON uses UTF-8 and timestamps use RFC 3339 UTC with microseconds.
- Local Unix-socket callers authenticate as the owning OS user. TCP callers use
  a session cookie or bearer token. Every browser mutation also requires the
  CSRF header. Remote callers need an explicit principal and scope.
- Every effect-producing `POST` or `PUT` requires `Idempotency-Key` (1–128 printable
  ASCII characters). Reuse with a different body returns
  `409 idempotency_mismatch`; reuse with the same body returns the original
  status/body.
- Mutable entity commands use exactly one concurrency token. Ordinary entity
  mutations require `If-Match: "<revision>"`; missing returns
  `428 revision_required` and stale returns `412 revision_conflict` with the
  safe current DTO. Interaction responses, InputBuffer/preference writes, and
  presence heartbeats are explicit exceptions because their typed bodies
  already carry `response_revision` or `expected_revision`; they must not also
  send `If-Match`, and stale returns their named `409 ..._revision_conflict`.
  Endpoint rows identify every exception; no endpoint accepts both mechanisms.
- Errors are `application/problem+json` with `type`, `title`, `status`, `code`,
  `detail`, `request_id`, and optional `current`/`retry_after_ms`.
- List `limit` defaults to 50 and is at most 200. Cursors are opaque and bound
  to filters/order. Invalid or expired cursors return `400 invalid_cursor` or
  `409 cursor_expired`.
- Mutation acceptance is `202` with `{operation, requested_runtime?,
  effective_runtime?}` unless the operation completes wholly in the database,
  in which case it is `200`/`201` as stated.

#### Authentication bootstrap endpoints

These three loopback routes are outside `/api/v1` because they establish, refresh, or end
the browser credential used by that API. They are still part of the complete
endpoint inventory and OpenAPI parity check.

| Method and path | Takes | Returns and important errors |
|---|---|---|
| `POST /auth/bootstrap` | loopback request with exact allowed `Origin`; `{secret,device_id}` where secret is the one-use launcher credential | `204`, browser session cookie, and `X-Baqylau-CSRF`; `400 invalid_bootstrap`, `401 bootstrap_expired|bootstrap_consumed` |
| `POST /auth/csrf` | current browser session, exact `Origin`, and `Sec-Fetch-Site: same-origin`; no body | `204` and fresh `X-Baqylau-CSRF`; `401 authentication_required|credential_expired|credential_revoked`; rotates only that session's CSRF digest |
| `POST /auth/logout` | current browser session and valid CSRF header; no body | `204` and expired cookie; `401 authentication_required|credential_revoked`; atomically revokes only that session and closes SSE subscribers carrying its credential ID |

Runtime selection schema used by start/resume/fork/migration/handover:

```text
RuntimeRequest
  execution_target_id     required
  account_id               nullable only when provider selects from policy
  mode                     interactive | headless | sdk | server | remote
  model                    required explicit model ID or "provider_default"
  effort                   required explicit effort ID or "provider_default"
  runtime_options_revision required revision returned by runtime-options

RuntimeResult
  requested                exact RuntimeRequest
  effective_provider_id
  effective_execution_target_id
  effective_account_id     nullable only when provider has no accounts
  effective_mode
  effective_model
  effective_effort
  differences[]            {field, requested, effective, reason}
```

Every endpoint accepting `RuntimeRequest` compares the revision for the exact
provider/target/account/mode tuple. Stale options return
`409 runtime_options_changed` with the complete fresh options DTO and its new
revision; no provider launch/control is enqueued.

#### System, providers, targets, accounts, and diagnostics

| Method and path | Takes | Returns and important errors |
|---|---|---|
| `GET /api/v1/health` | no body | `HealthDTO`: daemon/database/blob/supervisor/provider-edge/projection/notification health and ingestion gaps; `200` even when degraded, `503` only before reads are safe |
| `GET /api/v1/providers` | `execution_target_id?` | provider DTOs, modes, installed/edge/trust state, and capability revisions |
| `GET /api/v1/providers/{provider_id}/runtime-options` | required `execution_target_id`; optional `account_id`, `mode` | exact available models/efforts/defaults/refusal reasons and options revision; `404 provider_not_found` |
| `GET /api/v1/providers/{provider_id}/command-vocabulary` | required `execution_target_id`, `workspace_ref`; optional `actor_key` | `CommandVocabularySnapshot`; empty list is valid |
| `GET /api/v1/provider-edges` | filters `provider_id?`, `state?` | edge installation/trust DTOs |
| `POST /api/v1/provider-edges/{provider_id}:install` | `{backend_id, expected_config_digest?}` | `202` edge-install Operation; `409 delegating_hook_present` or `409 provider_running_requires_review` |
| `POST /api/v1/provider-edges/{provider_id}:verify-trust` | `{backend_id, observed_trust_key}` | verified installation DTO or `409 trust_not_granted` |
| `POST /api/v1/provider-edges/{provider_id}:revert` | `{backend_id,target_installed_version,expected_config_digest,reason}`; owner/admin | `202` revert Operation that restores executable/config backup and re-verifies trust; `409 backup_unavailable|provider_edge_changed` |
| `GET /api/v1/backends` | no body; owner/admin | stored backend connection/location DTOs plus separately labelled observed health |
| `POST /api/v1/backends` | `{label,adapter_id,endpoint_config_ref,trust_class,enabled}`; owner/admin | `201 BackendDTO`; stores configuration only, performs no connection |
| `PATCH /api/v1/backends/{id}` | merge patch plus `If-Match`; owner/admin | updated stored DTO; `409 backend_in_use` for incompatible active change |
| `DELETE /api/v1/backends/{id}` | `If-Match`; owner/admin | `204`; `409 backend_in_use` while targets/sessions reference it |
| `GET /api/v1/execution-targets` | no body | persisted target DTOs plus live reachability/freshness from their backend adapters |
| `POST /api/v1/execution-targets` | `{label,backend_id,provider_id,default_mode,workspace_root_ref,provider_config,enabled}` | `201 ExecutionTargetDTO`; stores configuration only and does not connect/start a provider |
| `PATCH /api/v1/execution-targets/{id}` | merge patch plus `If-Match` | updated DTO; changing active target identity returns `409 target_in_use` |
| `DELETE /api/v1/execution-targets/{id}` | `If-Match` | `204`; `409 target_in_use` while referenced by active work |
| `POST /api/v1/execution-targets/{id}:probe` | no body | `202` backend-probe Operation and later reachability evidence |
| `GET /api/v1/accounts` | filters `provider_id?`, `execution_target_id?` | account DTOs without credential secrets, quota summaries, selection eligibility |
| `POST /api/v1/accounts` | provider/target ID, label, credential import method/reference, policy priority | `201 AccountDTO`; secret goes directly to CredentialPort |
| `PATCH /api/v1/accounts/{id}` | label/enabled/priority only | updated DTO; credentials use rotate endpoint |
| `POST /api/v1/accounts/{id}:rotate-credential` | credential import method/reference | `202` credential workflow; never returns secret |
| `GET /api/v1/quota-windows` | `account_id?`, `provider_id?` | source-labelled windows and freshness |
| `GET /api/v1/usage` | required `from`, `through`; optional conversation/account/provider/model/ledger grouping | canonical daily/lifetime rollups, vendor and calculated costs, unknown fields |
| `GET /api/v1/stats` | required range; optional project/provider/model grouping | composed Insights DTO from canonical rollups, not prunable evidence |
| `GET /api/v1/anomalies` | `code?`, `severity?`, `conversation_id?`, cursor | registered anomaly results; malformed evidence is returned as a result, not a 500 |
| `GET /api/v1/ingestion-gaps` | range/cursor | known daemon/database outage gaps and affected sources |
| `POST /api/v1/client-telemetry` | `{records:[ControlAttempt|SseLifecycle|JsError|JsRejection|Boot|NotificationReceipt|AttachmentPaste]}` max 100, each with `conn_info` and client identity | `202 {accepted, duplicate, rejected}`; surface telemetry scope required |
| `GET /api/v1/system/events` | `Last-Event-ID`; owner/admin; browser also sends presence session/generation | machine SSE scope in Section 38.22 |
| `GET /api/v1/events` | `Last-Event-ID`; browser presence session/generation | current-principal SSE scope in Section 38.22 |

#### Conversation and actor views

| Method and path | Takes | Returns and important errors |
|---|---|---|
| `GET /api/v1/conversations` | project/provider/account/state/attention filters, `sort=updated_desc|created_desc|active_time_desc`, cursor | overview DTO page and one snapshot cursor; hidden empty-key rules from Section 38.3 |
| `POST /api/v1/conversations` | `{title?, project_ref?, workspace:{execution_target_id,workspace_ref}?}` | `201` empty Conversation, lead actor track, revision 0; does not start a provider |
| `GET /api/v1/conversations/{id}` | optional `actor_key` | complete view snapshot: Conversation, selected track ancestry, sessions, capabilities, open/recent Operations, interactions, activity window, Stream revisions, facets, reachability, freshness, and feed cursor |
| `PATCH /api/v1/conversations/{id}` | title when parked, archive preference, project label | updated DTO or `409 live_title_owned_by_provider` |
| `POST /api/v1/conversations/{id}:archive` | `{force_abort_background:false}` | archive Operation; `409 background_work_active` unless explicit true |
| `POST /api/v1/conversations/{id}:unarchive` | no body | updated Conversation DTO |
| `GET /api/v1/conversations/{id}/actor-tracks` | state/kind filters | actor track DTOs with heads and lifecycle links |
| `GET /api/v1/conversations/{id}/nodes` | `actor_key`, `head_node_id?`, backward cursor, limit | committed semantic ancestry page; provisional/suspect state explicitly labelled |
| `GET /api/v1/conversations/{id}/operations` | actor/kind/state/turn/task filters, cursor | stable Operation page |
| `GET /api/v1/conversations/{id}/activity` | required `actor_scope=lead|<actor_key>`; view filters, backward cursor | whole-block `ActivityPage` with generation and folding group IDs |
| `POST /api/v1/conversations/{id}/tasks:dismiss` | `{snapshot_id,sorted_task_ids,task_set_digest,expected_preference_revision}` | `200 TaskDismissalDTO`; only a complete all-done snapshot is dismissible; `409 tasks_not_done|task_snapshot_changed` |
| `GET /api/v1/conversations/{id}/events` | `Last-Event-ID`; browser presence session/generation; Conversation view authority | Conversation SSE contract in Section 38.22 |
| `POST /api/v1/conversations/{id}/agent-sessions` | `RuntimeRequest`, `from_node_id`, optional `actor_key` | `202` start Operation plus requested/effective runtime; validates runtime-options revision |
| `PUT /api/v1/agent-sessions/{id}/view-mode` | `{view_mode,expected_revision,client_mutation_id}` | `200 AgentSessionDTO`; persists cross-device mode and emits `view-mode.changed`; `409 view_mode_revision_conflict` |
| `POST /api/v1/conversations/{id}/messages` | `{agent_session_id,text,resource_ids[],tui_draft_revision?,client_message_id,parked_policy:reject|resume,runtime?}` | `202 message_delivery`; parked `resume` chains verified relaunch and send; `409 session_not_live|interaction_owns_input|draft_conflict`, `410 session_artifact_gone`, `503 terminal_unavailable` |
| `POST /api/v1/conversations/{id}/forks` | `{from_node_id, runtime:RuntimeRequest, title?}` | `202` fork Operation, target AgentSession/runtime and provisional branch details |
| `POST /api/v1/conversations/{id}/rewinds` | `{agent_session_id, target_node_id, mode:conversation|workspace|both, restore_draft}` | `202` rewind with requested/effective mode; errors from Section 38.13 |
| `POST /api/v1/conversations/{id}/handovers` | source session/node, `RuntimeRequest`, context budget, Resource include/exclude list, workspace policy, approval policy | `202` handover saga, target session/runtime, package manifest and checkpoint |
| `POST /api/v1/conversations/{id}/collaborators` | principal or invitation, role, actor permissions, expiry | `201` participant/invitation; remote auth and collaboration scope required |
| `DELETE /api/v1/conversations/{id}/collaborators/{participant_id}` | `If-Match` | `204`; active contributions remain attributed |

#### Agent sessions, controls, Operations, and interactions

| Method and path | Takes | Returns and important errors |
|---|---|---|
| `GET /api/v1/agent-sessions/{id}` | no body | session, attempts, aliases, artifacts, runtime revisions, context, lifecycle, binding, capabilities, reachability |
| `GET /api/v1/agent-sessions/{id}/memory` | no body | `MemoryViewDTO`: scope, badge, complete touch/search facts, compressed tree, revision and freshness; `404 session_not_found` |
| `GET /api/v1/agent-sessions/{id}/memory/note` | exactly one query field `path` or `stem` | `MemoryNoteDTO` after fresh vault-jail proof; an unresolved/deleted note is `200 missing=true`; `403 memory_off_scope`, `422 note_selector_invalid` |
| `POST /api/v1/agent-sessions/{id}:resume` | `RuntimeRequest`, optional `from_node_id`, `continue_interrupted_turn` | `202` resume Operation and requested/effective runtime; proves artifact at gesture time |
| `POST /api/v1/agent-sessions/{id}:interrupt` | `{take_back_queued_message}` | `202` control Operation; blocked by incompatible interaction and governed by escape-recheck |
| `POST /api/v1/agent-sessions/{id}:compact` | `{instructions?}` | `202` compaction Operation; `409 interaction_owns_input` |
| `POST /api/v1/agent-sessions/{id}:close` | `{park, force}` | `202` close Operation; response separates host closure and background work |
| `POST /api/v1/agent-sessions/{id}:rename` | `{title?, mode:auto|explicit}` | `202`; live provider owns title, parked explicit rename may be synchronous |
| `POST /api/v1/agent-sessions/{id}:set-runtime` | `{model, effort, runtime_options_revision}` | `202`, expected confirmation is handled within same gesture, returns requested/effective values |
| `POST /api/v1/agent-sessions/{id}:migrate-account` | target account plus model/effort/mode/target override | `202` manual migration defined in Section 38.18 |
| `GET /api/v1/operations/{id}` | no body | complete Operation/detail/attempt/receipt/uncertainty DTO |
| `POST /api/v1/operations/{id}:cancel` | `{reason}` | `202` typed cancel Operation; not available for every kind |
| `GET /api/v1/interactions/{id}` | no body | full prompt/options/plan/verdict/progress and current driver revision |
| `POST /api/v1/interactions/{id}/responses` | `{response_revision, answers, verdict?, edited?, feedback?}` | `202` answer Operation; exact conflicts from Section 38.12 |

#### Streams, resources, drafts, preferences, panes, and notifications

| Method and path | Takes | Returns and important errors |
|---|---|---|
| `GET /api/v1/streams/{id}` | no body | Stream metadata, render fields, revision, retained ranges, copy states |
| `GET /api/v1/streams/{id}/content` | `from_revision`, optional byte range | ordered delta/replace/reset operations through current revision; `409 stream_revision_expired`, `410 content_expired` |
| `GET /api/v1/streams/{id}/copy` | `kind=visible|raw` | immutable bytes with media type; raw may return `410 raw_content_expired` |
| `GET /api/v1/resources/{id}` | optional version | metadata and availability; never silently follows a changed path |
| `GET /api/v1/resources/{id}/content` | version/range | retained bytes; `410 resource_content_expired` |
| `POST /api/v1/uploads` | multipart one file, max configured size | `201` staged upload Resource with provider-safe path token; media/digest/length returned |
| `POST /api/v1/dictation/grants` | `{device_id,conversation_id?,project_ref?,language?,key_terms[]}`; current browser/device and CSRF | `201` short-lived restricted `DictationGrantDTO`; `403 dictation_unavailable`, `422 terms_out_of_scope` |
| `POST /api/v1/clipboard/files:resolve` | `{basenames[]}` max 100 from a current user paste gesture | exact ordered local file Resources/path tokens only when the live pasteboard file basenames match exactly; `403 local_capability_required`, `409 pasteboard_changed`, `422 basename_mismatch|unsafe_path` |
| `GET /api/v1/input-buffers` | exact scope tuple and kind | buffer DTO or explicit absent result |
| `PUT /api/v1/input-buffers/{id}` | `{text, expected_revision, author_sequence, origin}` | updated buffer; CAS/tombstone rules apply |
| `DELETE /api/v1/input-buffers/{id}` | expected revision/author sequence | tombstone DTO, never physical immediate delete |
| `GET /api/v1/preferences` | exact namespace/scope/key filters | owned preference DTOs |
| `PUT /api/v1/preferences/{namespace}/{scope_type}/{scope_id}/{key}` | versioned value, expected revision/author sequence | validated preference DTO |
| `DELETE /api/v1/preferences/{namespace}/{scope_type}/{scope_id}/{key}` | expected revision/author sequence | tombstone DTO |
| `POST /api/v1/project-groups/{group_id}:hide` | expected revision | `group_id` is the opaque ID returned by the overview; rule in Section 38.3 |
| `POST /api/v1/terminal/panes:toggle` | optional `{conversation_id,agent_session_id}`; absent resolves focused tab | `202` pane Operation/requested state; verified prior/result arrives by SSE |
| `POST /api/v1/terminal/panes:grow` | same | same asynchronous receipt contract |
| `POST /api/v1/terminal/panes:shrink` | same | same asynchronous receipt contract |
| `POST /api/v1/terminal/panes:reset` | same | same asynchronous receipt contract |
| `PUT /api/v1/terminal/panes/percentage` | optional IDs plus integer `percentage` 10–90 and `Idempotency-Key` | `202` pane Operation/requested percentage; verified result by SSE |
| `GET /api/v1/notification-settings` | no body | principal settings plus read-only effective machine `public_base_url`, `pre_alert_delay_seconds:0`, `done_settle_seconds:20`, `escalation_seconds:300`, `retractability_seconds:86400`, mutes and revision |
| `PUT /api/v1/notification-settings` | complete mutable settings excluding `public_base_url`, plus `expected_revision`; channel secret references only | updated settings; raw secrets never returned |
| `GET /api/v1/notifications` | state/kind/conversation/cursor | intent, routing, delivery, escalation and retraction DTOs |
| `POST /api/v1/notifications/{id}:react` | `{reaction:viewing|answered|dismissed}` | updated intent/arm plus retraction Operation if required |
| `GET /api/v1/push-subscriptions` | current principal; optional `device_id` | owned subscriptions without auth secret, key ID/state |
| `GET /api/v1/push-config` | current principal/device | active VAPID `{key_id,public_key}` and push availability; never private key material |
| `POST /api/v1/push-subscriptions` | `{device_id,endpoint,p256dh,auth,key_id,platform,user_agent}` | `201` subscription/device DTO; terminal ID forbidden |
| `DELETE /api/v1/push-subscriptions/{id}` | ownership and `If-Match` | `204` or `404 subscription_not_found` |
| `POST /api/v1/push-keys:rotate` | `{expected_active_key_id,confirm_orphan_count}`; owner/admin | `202` key-rotation Operation, affected subscription count and re-subscription plan; `409 orphan_count_changed` |
| `POST /api/v1/presence-sessions` | `{device_id,surface_id,conversation_id?,visibility,focused}` | `201` presence session, heartbeat interval `max(2,floor(view_ttl_s/2.5))`, revision, connection generation |
| `PUT /api/v1/presence-sessions/{id}` | `{conversation_id?,visibility,focused,device_active,expected_revision}` | updated current and device-history DTO |
| `POST /api/v1/presence-sessions/{id}:away` | `{expected_revision}` | ended presence session; last-seen history retained |
| `PUT /api/v1/terminal-presence/{terminal_binding_id}` | `{frontmost,tab_focused,conversation_id?,binding_revision}` from trusted adapter | updated terminal device history and suppression fact |

#### Handover, collaboration, extensions, repairs, and administration

| Method and path | Takes | Returns and important errors |
|---|---|---|
| `GET /api/v1/handovers/{operation_id}` | no body | saga checkpoints, package manifest, approvals, target runtime and partial/unknown outcomes |
| `POST /api/v1/handovers/{operation_id}:approve` | package revision and allowed Resource IDs | resumed saga; stale package returns `412` |
| `POST /api/v1/handovers/{operation_id}:retry-step` | exact failed/indeterminate step and operator reason | only steps whose reconciliation proves retry-safe |
| `GET /api/v1/collaboration/invitations/{token}` | token | non-secret invitation summary and expiry |
| `POST /api/v1/collaboration/invitations/{token}:accept` | authenticated principal/device key | participant/actor membership and feed scope |
| `POST /api/v1/conversations/{id}/peer-messages` | sender actor, recipient actor/broadcast, kind, body/task reference | `202` delivery Operation; sender permission and actor identity required |
| `GET /api/v1/extensions` | no body | manifests, trust, capabilities, health and surface contribution schemas |
| `POST /api/v1/extensions/{id}:enable` | expected manifest digest and granted capabilities | `202` installation/start workflow; untrusted extensions remain out-of-process |
| `POST /api/v1/extensions/{id}:disable` | reason | `202`; canonical namespaced facts remain readable |
| `GET /api/v1/repairs` | entity/code/operator/cursor filters | repair history, never unrestricted SQL |
| `POST /api/v1/repairs` | registered repair code, typed arguments, evidence IDs, reason | `202` guarded repair Operation; admin scope required |
| `POST /api/v1/backups` | `{label?}` | `202` online backup workflow and later verified manifest |
| `GET /api/v1/backups` | cursor | backup manifests and restore compatibility |
| `POST /api/v1/backups/{id}:verify` | no body | `202` verification workflow |
| `POST /api/v1/backups/{id}:restore` | `{expected_current_schema_version,verified_manifest_digest,confirmation}`; local owner/admin only | `202` restore Operation; daemon enters maintenance, takes pre-restore backup, verifies, restores SQLite/blobs, and restarts; `409 backup_unverified|schema_incompatible|active_mutations` |

Legacy import is intentionally CLI-only because it accepts a local filesystem
root and is an operator migration action, not a remote product API.

### 38.25 Storage access required by the review closure

Application code uses the following semantic methods. `ConversationUnitOfWork`
means the caller owns that Conversation coordinator; `MachineUnitOfWork` means
the caller owns the named machine-service lock. Both wrap one short SQLite write
transaction. A multi-Conversation workflow acquires coordinator locks in UUID
byte order, then the machine lock, and uses one transaction; failure to acquire
all within 250 ms returns/requeues `coordination_busy` without holding any.
`UnitOfWork` below is the union of those scope-aware types. Methods suffixed
`_tx` never commit. Claim/start/finish methods documented without `_tx` open one
`BEGIN IMMEDIATE` transaction internally, perform a compare-and-set/lease
write, and commit before return. Read methods open one read snapshot unless
passed an existing `ReadView`.

```python
class ActorTrackStore(Protocol):
    def get_or_create_tx(self, uow: UnitOfWork, spec: ActorTrackSpec) \
        -> ActorTrack: ...
    def append_node_tx(self, uow: UnitOfWork, spec: NodeSpec,
                       expected_track_revision: int) -> NodeAndTrack: ...
    def set_head_tx(self, uow: UnitOfWork, track_id: UUID, node_id: UUID,
                    expected_revision: int, evidence: EvidenceRef) \
        -> ActorTrack: ...
    def list_tracks(self, conversation_id: UUID,
                    states: frozenset[TrackState], limit: int,
                    cursor: str | None) -> Page[ActorTrack]: ...

class SessionFacetStore(Protocol):
    def put_context_tx(self, uow: UnitOfWork, value: ContextStateSpec) \
        -> SessionContextState: ...
    def append_runtime_revision_tx(self, uow: UnitOfWork,
                                   value: RuntimeRevisionSpec) \
        -> RuntimeRevision: ...
    def put_artifact_relocation_tx(self, uow: UnitOfWork,
                                   value: ArtifactRelocationSpec) \
        -> ArtifactRelocation: ...
    def freeze_grouping_tx(self, uow: UnitOfWork, value: GroupingSpec) \
        -> SessionGrouping: ...
    def replace_task_snapshot_tx(self, uow: UnitOfWork,
                                 value: TaskSnapshotSpec) \
        -> TaskSnapshot: ...
    def put_goal_tx(self, uow: UnitOfWork, value: GoalSpec) -> Goal: ...
    def append_title_revision_tx(self, uow: UnitOfWork,
                                 value: TitleRevisionSpec) -> TitleRevision: ...
    def select_current_title_tx(self, uow: UnitOfWork,
                                conversation_id: UUID,
                                expected_revision: int) -> CurrentTitle: ...

class ObservationConsumerStore(Protocol):
    def register_consumers_tx(self, uow: UnitOfWork, observation_id: UUID,
                              kinds: tuple[ConsumerKind, ...]) -> None: ...
    def claim_next(self, kind: ConsumerKind, now: datetime,
                   claimant_id: str, lease_for: timedelta) \
        -> ConsumerClaim | None: ...
    def finish_tx(self, uow: UnitOfWork, claim: ConsumerClaim,
                  result: ConsumerResult) -> None: ...
    def quarantine_tx(self, uow: UnitOfWork, claim: ConsumerClaim,
                      error: StoredError) -> None: ...

class InputOccupancyStore(Protocol):
    def get(self, agent_session_id: UUID) -> InputOccupancy: ...
    def compare_and_set_tx(self, uow: UnitOfWork, agent_session_id: UUID,
                           expected_revision: int,
                           next_value: InputOccupancySpec) \
        -> InputOccupancy: ...
    def create_tui_draft_tx(self, uow: UnitOfWork, spec: TuiDraftSpec,
                            expected_occupancy_revision: int) -> TuiDraft: ...
    def consume_tui_draft_tx(self, uow: UnitOfWork, draft_id: UUID,
                             expected_revision: int,
                             delivery_operation_id: UUID) -> TuiDraft: ...
    def get_modality(self, agent_session_id: UUID) -> InputModality: ...
    def compare_and_set_modality_tx(self, uow: UnitOfWork,
                                    agent_session_id: UUID,
                                    expected_revision: int,
                                    next_value: InputModalitySpec) \
        -> InputModality: ...

class TerminalBindingStore(Protocol):
    def upsert_observation_tx(self, uow: UnitOfWork,
                              spec: TerminalBindingSpec) -> TerminalBinding: ...
    def get_verified(self, agent_session_id: UUID) \
        -> TerminalBinding | NotFound | EvidenceUnavailable: ...
    def resolve_focused(self, terminal_adapter_id: str) \
        -> TerminalBinding | NotFound | Ambiguous: ...
    def record_focus_tx(self, uow: MachineUnitOfWork,
                        observation: TerminalFocusSpec) -> TerminalBinding: ...

class PaneStore(Protocol):
    def get(self, agent_session_id: UUID) -> PaneState | NotFound: ...
    def plan_tx(self, uow: UnitOfWork, command: PaneCommand,
                binding: TerminalBinding,
                expected_revision: int | None) -> OperationAndPaneState: ...
    def record_receipt_tx(self, uow: UnitOfWork, operation_id: UUID,
                          receipt: PaneReceipt) -> PaneState: ...

class AttentionProjectionStore(Protocol):
    def put_tx(self, uow: UnitOfWork,
               value: AttentionProjectionSpec) -> AttentionProjection: ...
    def get(self, scope: AttentionScope) \
        -> AttentionProjection | NotFound: ...

class ChildLaunchCorrelationStore(Protocol):
    def enqueue_tx(self, uow: UnitOfWork,
                   spec: ChildLaunchSpec) -> ChildLaunchCorrelation: ...
    def consume_next_tx(self, uow: UnitOfWork, scope: ChildLaunchScope,
                        child_actor_key: str) \
        -> ChildLaunchCorrelation | NotFound | Ambiguous: ...
    def close_unmatched_tx(self, uow: UnitOfWork,
                           spec: UnmatchedChildCloserSpec) -> Operation: ...

class OtlpReceiptStore(Protocol):
    def allocate(self, listener_instance_id: str, envelope: OtlpEnvelopeMeta) \
        -> OtlpReceipt: ...
    def finish_tx(self, uow: MachineUnitOfWork, receipt: OtlpReceipt,
                  parse_state: OtlpParseState,
                  health_error_id: UUID | None) -> OtlpReceipt: ...

class ConversationOverviewStore(Protocol):
    def replace_tx(self, uow: UnitOfWork,
                   value: ConversationOverviewSpec,
                   expected_revision: int | None) -> ConversationOverview: ...
    def list(self, view: ReadView, principal_id: UUID,
             query: ConversationListQuery, limit: int,
             cursor: str | None) -> Page[ConversationOverview]: ...

class DiagnosticSuppressionStore(Protocol):
    def put_tx(self, uow: MachineUnitOfWork,
               value: DiagnosticSuppressionSpec,
               expected_revision: int | None) -> DiagnosticSuppression: ...
    def list_enabled(self, view: ReadView, scope: DiagnosticScope) \
        -> tuple[DiagnosticSuppression, ...]: ...

class TabPaintStore(Protocol):
    def begin_attempt_tx(self, uow: UnitOfWork,
                         spec: TabPaintAttemptSpec) -> TabPaintAttempt: ...
    def finish_attempt_tx(self, uow: UnitOfWork, attempt_id: UUID,
                          result: TabPaintResult) -> TabPaintAttempt: ...
    def get_verified(self, view: ReadView,
                     terminal_binding_id: UUID) -> TabPaintState | NotFound: ...

class WorkspaceGitStore(Protocol):
    def put_identity_tx(self, uow: UnitOfWork,
                        value: WorkspaceGitIdentitySpec) -> WorkspaceGitIdentity: ...
    def put_dirty_tx(self, uow: UnitOfWork,
                     value: WorkspaceDirtySpec) -> WorkspaceDirtyState: ...
    def get(self, view: ReadView, backend_id: UUID,
            workspace_ref: str) -> WorkspaceGitSnapshot: ...

class AlertStore(Protocol):
    def arm_tx(self, uow: UnitOfWork, spec: AlertArmSpec) \
        -> ArmedNotification: ...  # returns the atomically created arm+intent
    def claim_due_arms(self, now: datetime, claimant_id: str, limit: int,
                       lease_for: timedelta) -> tuple[DueArmClaim, ...]: ...
    def hold_tx(self, uow: UnitOfWork, intent_id: UUID,
                expected_revision: int, reason: HoldReason) \
        -> NotificationIntent: ...
    def route_tx(self, uow: UnitOfWork, decision: RouteDecisionSpec) \
        -> RouteDecision: ...
    def add_delivery_tx(self, uow: UnitOfWork, spec: DeliverySpec) \
        -> NotificationDelivery: ...
    def disarm_tx(self, uow: UnitOfWork, intent_id: UUID,
                  expected_revision: int, reason: CancelReason) \
        -> NotificationIntent: ...

class UsageStore(Protocol):
    def credit_snapshot_tx(self, uow: UnitOfWork,
                           fact: UsageSnapshotSpec) -> UsageCreditResult: ...
    def apply_delta_tx(self, uow: UnitOfWork,
                       fact: UsageDeltaSpec) -> UsageCreditResult: ...
    def select_source_authority_tx(self, uow: UnitOfWork,
                                   spec: UsageAuthoritySpec,
                                   expected_revision: int) \
        -> UsageAuthority: ...
    def read_rollups(self, query: UsageRollupQuery) -> UsageRollupResult: ...

class StructuralFeedStore(Protocol):
    def append_tx(self, uow: UnitOfWork, change: StructuralChangeSpec) \
        -> FeedCursor: ...
    def high_water(self, scope: FeedScope, read_view: ReadView) \
        -> FeedCursor: ...
    def replay_after(self, scope: FeedScope, cursor: FeedCursor,
                     limit: int) -> tuple[StructuralChange, ...]: ...

class ProviderEdgeStore(Protocol):
    def get(self, provider_id: str, backend_id: UUID) \
        -> EdgeInstallation | None: ...
    def record_install_tx(self, uow: UnitOfWork, spec: EdgeInstallSpec) \
        -> EdgeInstallation: ...
    def record_trust_tx(self, uow: UnitOfWork, spec: EdgeTrustSpec) \
        -> EdgeInstallation: ...

class LegacyImportStore(Protocol):
    def start_or_resume(self, fingerprint: str, source_path: Path) \
        -> ImportRun: ...
    def has_row(self, fingerprint: str, table: str, row_id: str) -> bool: ...
    def commit_batch_tx(self, uow: UnitOfWork, batch: LegacyImportBatch) \
        -> ImportBatchResult: ...
    def finish(self, run_id: UUID, result: ImportRunResult) -> ImportRun: ...
```

Named errors are `NotFound`, `RevisionConflict(current)`,
`InvariantViolation(code)`, `EvidenceUnavailable(code)`, `CursorExpired`,
`LeaseLost`, and `StorageDegraded`. Missing current context/title/goal returns a
typed absent/unknown value; it never raises or supplies a zero/empty default.

Access patterns and indexes:

- track ancestry uses `(actor_track_id, parent_id)` plus primary-key walking;
  track lists use `(conversation_id, state, created_at, id)`;
- context current writes compare-and-set on `(source_epoch, source_ordinal)`;
  runtime/title/task latest reads use the corresponding descending source-order
  indexes. `observed_at` is diagnostic and never selects truth;
- native forward scans use
  `(agent_session_id, source_registration_id, source_epoch, source_ordinal)`
  and persist the last fully parsed byte/provider position;
- due alerts use `(arm_state, due_at, id)`; active deliveries use
  `(intent_id, delivery_state, stage)`;
- usage credit is one primary-key lookup; rollups use their primary keys and
  date/group indexes;
- structural replay uses `(scope_type, scope_id, change_id)` and reads at most
  1,000 rows per call;
- consumer claiming uses `(consumer_kind, state, next_attempt_at,
  observation_id)`; and
- legacy import dedup uses its three-column identity
  `(source_database_fingerprint, legacy_table, legacy_row_id)` and commits at most
  500 legacy rows per transaction.

Required atomic transactions:

1. **Child launch:** Operation + actor track + causal link + provenance +
   projection revision + structural outbox.
2. **Track message commit:** Node/parts + Stream seal references + track head +
   lead Conversation head when applicable + invalidation + provenance + feed.
3. **Interaction open/close:** Operation/details + input occupancy + attention +
   notification arm/disarm + outbox/feed.
4. **Daemon draft:** TUI draft + occupancy + control/delivery correlation +
   feed; consumption clears all three together.
5. **Usage credit:** fact + per-source credit state + per-source lifetime/daily
   rollups + authority selection when precedence changes + usage feed. A source
   change never adds two billing sources together.
6. **Alert route/send request:** intent/arm revision + route decision + delivery
   row + outbox effect.
7. **Closer:** Operation and Stream changes + slot release + post-state
   attention + alert changes + feed.
8. **Artifact relocation:** artifact history + live source registration/cursor
   handoff + provenance; filesystem watcher changes happen after commit through
   outbox and reconcile back as evidence.
9. **Planned rollout source binding:** the launch-planned AgentSession,
   nullable source registration, discovered concrete artifact path, and
   provenance are joined by `set_source_ref_tx` in one Conversation
   transaction before a source reader is armed.

No transaction contains filesystem, terminal, network, credential, process, or
provider I/O.

### 38.26 Service ownership and runtime tasks added by the review

The following services are application owners; adapters only implement their
ports:

| Service | Public responsibility and methods | Durable state |
|---|---|---|
| `ActorTrackService` | `ensure_actor`, `commit_actor_node`, `end_actor`, `actor_snapshot` | actor tracks, Nodes, peer links |
| `SessionFacetService` | `observe_context`, `observe_runtime_change`, `capture_tasks`, `observe_goal`, `observe_title`, `relocate_artifact` | Section 38.2–38.3 records |
| `ProviderEdgeManager` | `plan_install`, `install`, `verify`, `verify_trust`, `revert` | edge installation/workflow/outbox |
| `OtlpIngestionService` | `accept_receipt`, `record_parse_failure`, `map_datapoints` | OTLP receipts, Observations, health |
| `InteractionService` | `open`, `respond`, `observe_progress`, `observe_verdict`, `mark_lost` | interaction/details/input occupancy |
| `TuiDraftService` | `stash`, `prepare_replace`, `consume`, `reconcile_probe` | TUI drafts and occupancy |
| `ViewModeService` | `get`, `put`, `project_fold_state` | AgentSession view preference and structural event |
| `ConversationProjectionService` | `request_refresh`, `replace_overview`, `list_overviews` | complete per-principal Conversation overviews |
| `RewindService` | `request`, `record_drive`, `confirm_native_branch`, `contradict` | rewind details, provisional branch |
| `PresenceService` | `start`, `heartbeat`, `mark_away`, `mark_terminal_focus`, `expire` | presence sessions/device presence |
| `AlertPolicyService` | `consider_transition`, `scan_due`, `hold`, `route`, `react`, `retract`, `escalate` | intents, arms, routes, deliveries |
| `UsageAccountingService` | `ingest_delta`, `ingest_snapshot`, `ingest_vendor_cost`, `query_rollups` | facts, credit state, rollups |
| `AccountMigrationService` | `start_auto`, `start_manual`, `resume_checkpoint`, `reconcile_attempt` | migration Operation/details/outbox |
| `LegacyParkedImporter` | `discover`, `import_batch`, `finish`, `report` | import runs/rows plus imported domain state |
| `DiagnosticService` | `run_anomalies`, `health`, `record_control_attempts`, `otel_report` | errors, suppressions, telemetry, anomaly results |

Runtime tasks are exact:

- `ObservationConsumerWorker[kind]` starts after database recovery, claims at
  most 100 consumers or 25 ms of work, uses a 30-second lease, and backs off
  1/2/5/15/60 seconds after repeated failure before quarantining at attempt 10.
- `SourceReaderSupervisor` starts after edge/provider discovery, one task per
  durable registration, wakes on filesystem notification plus a 200 ms polling
  fallback, and persists a cursor after every committed batch.
- `AlertScanner` starts after presence and delivery recovery, wakes every one
  second or at the nearest arm time, claims at most 100 rows from
  `arms(state,due_at,id)`, joins each owner intent, and compares the intent's
  copied `arm_revision` before policy evaluation. Claim, arm revision advance,
  and intent copy update are one transaction. It never performs delivery I/O
  inside that transaction.
- `PresenceExpiryWorker` wakes every five seconds and ends sessions whose
  heartbeat horizon has passed; explicit away is immediate.
- `TerminalFrontmostPoller` uses the one/five-second cadence and trusted writes
  specified in Section 38.10; it is the sole producer for reserved device
  `terminal` and verified terminal-tab focus.
- `OtlpLoopbackListener` binds only `127.0.0.1:4319`, enforces the Section 38.4
  gzip/chunk/body contract, durably assigns a receipt sequence, and returns
  HTTP 200 after every consumed request. Mapping runs asynchronously through
  ordinary Observation consumers.
- `StructuralFeedPublisher` claims feed outbox rows in change order per scope,
  publishes to in-memory subscribers, marks publication attempts, and retains
  the durable feed independently of connected clients.
- `ProjectionRebuilder` processes at most 500 items/25 ms per transaction and
  yields between chunks.
- `ProviderEdgeVerifier` runs at startup, after upgrade, on provider config
  change, and every ten minutes; it never automatically grants trust.
- `SlotReaper` runs every 30 seconds and applies the PID/host/lease rules in
  Section 38.10.
- `LegacyImportWorker` exists only for an explicit CLI run and stops after the
  selected source root is exhausted or the operator cancels.
- `OutboxEffectDispatcher[adapter_kind]` starts after recovery, wakes on outbox
  commit notification or every 250 ms, claims at most 100 rows with a 30-second
  lease in `(priority DESC, available_at, id)` order, and dispatches outside the
  transaction. Failed-before-action retries at 1/2/5/15/60 seconds up to the
  kind's limit; possibly-acted becomes `indeterminate` and goes to reconciliation
  without automatic retry.
- `EffectReconciler` wakes on indeterminate attempts and every second, claims at
  most 50 with a 30-second lease, runs the kind's independent receipt/prober,
  and resolves or reschedules. After ten inconclusive attempts it leaves the
  effect `unknown` with manual guidance; it never invents failure or success.
- `NotificationDeliveryWorker` is an outbox adapter worker with a 30-second
  lease and batches at most 20 channel sends. It applies subscription 404/410,
  resolve-budget, external-handle, and retraction evidence in one result
  Observation per delivery.
- `StructuralFeedRetentionWorker` runs every five minutes, deletes in batches
  of 1,000 only rows outside both the 24-hour and newest-100,000-per-scope
  guarantees, and yields after 25 ms of writer time.
- `BlobGarbageCollector` runs hourly and `UploadRetentionWorker` every ten
  minutes. Each claims at most 100 eligible objects with a 10-minute GC lease,
  rechecks reachability in a transaction, quarantines by atomic rename, then
  deletes outside the transaction and records completion/recovery.
- `StreamSealerRecoveryWorker` runs at startup and on seal work. It owns one
  Stream lease, reconciles file-ahead/DB-ahead states, seals at most 20 Streams
  per batch, and never deletes an `.open` file until Blob metadata and Stream
  final reference commit.
- `SagaRunner[kind]` starts after outbox recovery, wakes on workflow checkpoint
  change, claims at most 20 handover/migration/rewind/backup/restore sagas with
  30-second leases, executes one idempotent checkpoint step, and persists the
  next safe step before releasing the lease.
- `BackupWorker` is the backup/restore Saga adapter. It permits one machine-
  global backup lease, uses SQLite online backup in 1,000-page steps with
  50-ms yields, verifies DB and Blob manifest, and records an immutable result.
  Restore requires maintenance mode and never runs concurrently with mutations.
- `DatabaseMaintenanceWorker` owns WAL checkpoint scheduling: passive check
  every 30 seconds, restart checkpoint at 64 MiB WAL, and a health alarm at
  256 MiB or five minutes without progress. It never blocks an answerable edge
  on a checkpoint.

Shutdown stops new claims, lets a current short transaction finish, shortens
leases to startup-recoverable time, flushes framed Stream writers, persists
cursors, and exits inside the ten-second daemon deadline. No worker owns truth
solely in memory.

### 38.27 Executable SQLite additions for the accepted review findings

The authoritative clean-install assembly in Sections 38.35, 38.39, and 40
includes the following exact DDL as its second unit.
These are not optional example tables. Migration from an earlier development
schema rebuilds affected tables inside one migration transaction so new
`NOT NULL` and `CHECK` constraints apply to old rows after backfill.

A planned rollout registration may begin with `source_ref` NULL because a
provider creates the concrete transcript/rollout only after launch. The source
reader binds that discovered artifact through
`SourceRegistrationStore.set_source_ref_tx` in the owning Conversation
transaction. The method accepts a non-empty concrete source path only while the
registration is active and unbound; a failed compare, closed registration, or
conflicting rebind is reported as a failed binding and the reader is not
re-armed. Durable recovery therefore reads only a concrete bound artifact, not
the launch-time directory root.

```sql
CREATE TABLE source_registrations (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  source_kind TEXT NOT NULL,
  source_ref TEXT,
  epoch INTEGER NOT NULL CHECK(epoch >= 0),
  last_source_ordinal INTEGER NOT NULL DEFAULT 0 CHECK(last_source_ordinal >= 0),
  byte_cursor INTEGER CHECK(byte_cursor >= 0),
  state TEXT NOT NULL CHECK(state IN
    ('active','superseded','ended','lost')),
  continuity_evidence_ref TEXT,
  created_at REAL NOT NULL,
  superseded_at REAL,
  UNIQUE(agent_session_id, source_kind, epoch),
  CHECK(source_ref IS NULL OR length(source_ref) > 0)
);
CREATE UNIQUE INDEX one_active_source_registration
  ON source_registrations(agent_session_id, source_kind)
  WHERE state = 'active';

CREATE TABLE session_adoption_notes (
  id TEXT PRIMARY KEY,
  predecessor_agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  candidate_backend_id TEXT NOT NULL,
  candidate_provider_id TEXT NOT NULL,
  workspace_identity TEXT NOT NULL,
  cwd_realpath TEXT NOT NULL,
  candidate_external_id TEXT,
  predecessor_attempt_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  state TEXT NOT NULL CHECK(state IN
    ('pending','consumed','rejected','expired')),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  consumed_at REAL,
  decision_provenance_id TEXT,
  UNIQUE(candidate_backend_id,candidate_provider_id,candidate_external_id),
  CHECK(expires_at > created_at),
  CHECK((state='pending' AND candidate_external_id IS NULL AND consumed_at IS NULL) OR
        (state='consumed' AND candidate_external_id IS NOT NULL AND consumed_at IS NOT NULL) OR
        state IN ('rejected','expired'))
);
CREATE UNIQUE INDEX one_pending_adoption_by_scope
  ON session_adoption_notes(candidate_backend_id,candidate_provider_id,
                            workspace_identity,cwd_realpath)
  WHERE state='pending';

CREATE TABLE session_start_evidence (
  backend_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  independent_start_seen INTEGER NOT NULL
    CHECK(independent_start_seen IN (0,1)),
  first_event_kind TEXT NOT NULL,
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(backend_id, provider_id, external_id)
);

CREATE TABLE attempt_environment_values (
  attempt_id TEXT NOT NULL,
  key TEXT NOT NULL,
  presence_state TEXT NOT NULL CHECK(presence_state IN
    ('present','explicit_empty','absent','inherited')),
  value TEXT,
  inherited_from_attempt_id TEXT,
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(attempt_id, key),
  CHECK((presence_state IN ('present','explicit_empty') AND
         inherited_from_attempt_id IS NULL) OR
        (presence_state = 'inherited' AND inherited_from_attempt_id IS NOT NULL) OR
        presence_state = 'absent')
);

CREATE TABLE agent_session_context_state (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  context_window_tokens INTEGER CHECK(context_window_tokens >= 0),
  context_used_tokens INTEGER CHECK(context_used_tokens >= 0),
  current_model TEXT,
  occupancy_state TEXT NOT NULL CHECK(occupancy_state IN
    ('observed','stale','unavailable','unknown')),
  source_kind TEXT NOT NULL CHECK(source_kind IN
    ('transcript','provider_api','statusline','imported')),
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  CHECK(context_window_tokens IS NULL OR context_used_tokens IS NULL OR
        context_used_tokens <= context_window_tokens)
);
CREATE TRIGGER context_source_scope_insert
BEFORE INSERT ON agent_session_context_state BEGIN
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM source_registrations r
    WHERE r.id = NEW.source_registration_id
      AND r.agent_session_id = NEW.agent_session_id
      AND r.epoch = NEW.source_epoch)
  THEN RAISE(ABORT, 'context_source_registration_mismatch') END;
END;
CREATE TRIGGER context_source_scope_update
BEFORE UPDATE OF agent_session_id, source_registration_id, source_epoch
ON agent_session_context_state BEGIN
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM source_registrations r
    WHERE r.id = NEW.source_registration_id
      AND r.agent_session_id = NEW.agent_session_id
      AND r.epoch = NEW.source_epoch)
  THEN RAISE(ABORT, 'context_source_registration_mismatch') END;
END;

CREATE TABLE agent_session_runtime_revisions (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  attempt_id TEXT,
  requested_provider_id TEXT,
  requested_execution_target_id TEXT,
  requested_account_id TEXT,
  requested_mode TEXT,
  requested_model TEXT,
  requested_effort TEXT,
  effective_provider_id TEXT,
  effective_execution_target_id TEXT,
  effective_account_id TEXT,
  effective_mode TEXT,
  effective_model TEXT,
  effective_effort TEXT,
  reason TEXT NOT NULL CHECK(reason IN
    ('start','resume','user_change','provider_fallback','fork','migration',
     'handover','observation')),
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL
);
CREATE INDEX as_runtime_latest
  ON agent_session_runtime_revisions
    (agent_session_id, source_epoch DESC, source_ordinal DESC, id DESC);
CREATE TRIGGER runtime_source_scope_insert
BEFORE INSERT ON agent_session_runtime_revisions BEGIN
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM source_registrations r
    WHERE r.id = NEW.source_registration_id
      AND r.agent_session_id = NEW.agent_session_id
      AND r.epoch = NEW.source_epoch)
  THEN RAISE(ABORT, 'runtime_source_registration_mismatch') END;
END;

CREATE TABLE conversation_goals (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  goal_ref TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN
    ('pending','active','blocked','achieved','cancelled','unknown')),
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(conversation_id, actor_track_id)
);
CREATE TRIGGER conversation_goal_scope_insert
BEFORE INSERT ON conversation_goals BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'conversation_goal_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM source_registrations r
    JOIN agent_sessions s ON s.id = r.agent_session_id
    WHERE r.id = NEW.source_registration_id
      AND r.epoch = NEW.source_epoch
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'conversation_goal_source_scope_mismatch') END;
END;
CREATE TRIGGER conversation_goal_scope_update
BEFORE UPDATE OF conversation_id, actor_track_id ON conversation_goals BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'conversation_goal_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM source_registrations r
    JOIN agent_sessions s ON s.id = r.agent_session_id
    WHERE r.id = NEW.source_registration_id
      AND r.epoch = NEW.source_epoch
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'conversation_goal_source_scope_mismatch') END;
END;

CREATE TABLE provider_task_snapshots (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  completeness TEXT NOT NULL CHECK(completeness IN
    ('complete','partial','unavailable')),
  captured_at REAL NOT NULL,
  provenance_id TEXT NOT NULL
);
CREATE INDEX task_snapshot_latest
  ON provider_task_snapshots
    (actor_track_id, source_epoch DESC, source_ordinal DESC, id DESC);
CREATE TRIGGER task_snapshot_scope_insert
BEFORE INSERT ON provider_task_snapshots BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'task_snapshot_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM source_registrations r
    JOIN agent_sessions s ON s.id = r.agent_session_id
    WHERE r.id = NEW.source_registration_id
      AND r.epoch = NEW.source_epoch
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'task_snapshot_source_scope_mismatch') END;
END;
CREATE TRIGGER task_snapshot_scope_update
BEFORE UPDATE OF conversation_id, actor_track_id
ON provider_task_snapshots BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'task_snapshot_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM source_registrations r
    JOIN agent_sessions s ON s.id = r.agent_session_id
    WHERE r.id = NEW.source_registration_id
      AND r.epoch = NEW.source_epoch
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'task_snapshot_source_scope_mismatch') END;
END;

CREATE TABLE provider_tasks (
  snapshot_id TEXT NOT NULL
    REFERENCES provider_task_snapshots(id) ON DELETE CASCADE,
  provider_task_key TEXT NOT NULL,
  parent_task_key TEXT,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  owner_actor_key TEXT,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
  PRIMARY KEY(snapshot_id, provider_task_key)
);

CREATE TABLE conversation_title_revisions (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  owner_agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  value TEXT NOT NULL,
  owner TEXT NOT NULL CHECK(owner IN
    ('provider_live','baqylau_parked','surface_user')),
  state TEXT NOT NULL CHECK(state IN
    ('requested','effective','rejected','stale','unknown')),
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  supersedes_id TEXT REFERENCES conversation_title_revisions(id),
  requested_at REAL,
  effective_at REAL,
  provenance_id TEXT NOT NULL,
  CHECK((owner = 'provider_live') = (owner_agent_session_id IS NOT NULL))
);
CREATE INDEX title_revisions_source
  ON conversation_title_revisions
    (conversation_id, owner_agent_session_id, source_epoch DESC,
     source_ordinal DESC, id DESC);

CREATE TABLE conversation_title_current (
  conversation_id TEXT PRIMARY KEY
    REFERENCES conversations(id) ON DELETE CASCADE,
  title_revision_id TEXT NOT NULL
    REFERENCES conversation_title_revisions(id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision >= 0)
);

CREATE TRIGGER title_revision_scope_insert
BEFORE INSERT ON conversation_title_revisions BEGIN
  SELECT CASE WHEN NEW.owner = 'surface_user' AND EXISTS (
    SELECT 1 FROM conversations c
    WHERE c.id = NEW.conversation_id
      AND c.active_agent_session_id IS NOT NULL)
  THEN RAISE(ABORT, 'title_surface_user_scope_mismatch') END;
  SELECT CASE WHEN NEW.owner = 'surface_user' AND NEW.source_registration_id IS NOT NULL
  THEN RAISE(ABORT, 'title_surface_user_source_mismatch') END;
  SELECT CASE WHEN NEW.owner_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.owner_agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'title_owner_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_title_revisions r
    WHERE r.id = NEW.supersedes_id
      AND r.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'title_supersession_scope_mismatch') END;
  SELECT CASE WHEN NEW.source_registration_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM source_registrations r
    JOIN agent_sessions s ON s.id = r.agent_session_id
    WHERE r.id = NEW.source_registration_id
      AND r.epoch = NEW.source_epoch
      AND s.conversation_id = NEW.conversation_id
      AND (NEW.owner_agent_session_id IS NULL OR
           r.agent_session_id = NEW.owner_agent_session_id))
  THEN RAISE(ABORT, 'title_source_registration_mismatch') END;
END;
CREATE TRIGGER title_revisions_are_append_only
BEFORE UPDATE ON conversation_title_revisions BEGIN
  SELECT RAISE(ABORT, 'title_revision_is_append_only');
END;
CREATE TRIGGER title_current_scope_insert
BEFORE INSERT ON conversation_title_current BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM conversation_title_revisions r
    JOIN conversations c ON c.id = NEW.conversation_id
    WHERE r.id = NEW.title_revision_id
      AND r.conversation_id = NEW.conversation_id
      AND ((r.owner IN ('baqylau_parked','surface_user') AND
            c.active_agent_session_id IS NULL) OR
           r.owner_agent_session_id = c.active_agent_session_id))
  THEN RAISE(ABORT, 'current_title_owner_or_scope_mismatch') END;
END;
CREATE TRIGGER title_current_scope_update
BEFORE UPDATE OF conversation_id, title_revision_id
ON conversation_title_current BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM conversation_title_revisions r
    JOIN conversations c ON c.id = NEW.conversation_id
    WHERE r.id = NEW.title_revision_id
      AND r.conversation_id = NEW.conversation_id
      AND ((r.owner IN ('baqylau_parked','surface_user') AND
            c.active_agent_session_id IS NULL) OR
           r.owner_agent_session_id = c.active_agent_session_id))
  THEN RAISE(ABORT, 'current_title_owner_or_scope_mismatch') END;
END;
CREATE TRIGGER title_current_projects_insert
AFTER INSERT ON conversation_title_current BEGIN
  UPDATE conversations
  SET title = (SELECT value FROM conversation_title_revisions
               WHERE id = NEW.title_revision_id),
      updated_at = unixepoch('subsec')
  WHERE id = NEW.conversation_id;
END;
CREATE TRIGGER title_current_projects_update
AFTER UPDATE OF title_revision_id ON conversation_title_current BEGIN
  UPDATE conversations
  SET title = (SELECT value FROM conversation_title_revisions
               WHERE id = NEW.title_revision_id),
      updated_at = unixepoch('subsec')
  WHERE id = NEW.conversation_id;
END;
CREATE TRIGGER conversation_title_is_derived_insert
BEFORE INSERT ON conversations
WHEN NEW.title IS NOT NULL BEGIN
  SELECT RAISE(ABORT, 'conversation_title_must_be_created_through_revision');
END;
CREATE TRIGGER conversation_title_is_derived_update
BEFORE UPDATE OF title ON conversations
WHEN NEW.title IS NOT (
  SELECT r.value
  FROM conversation_title_current c
  JOIN conversation_title_revisions r ON r.id = c.title_revision_id
  WHERE c.conversation_id = NEW.id)
BEGIN
  SELECT RAISE(ABORT, 'conversation_title_must_equal_current_revision');
END;

CREATE TABLE agent_session_artifacts (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  artifact_kind TEXT NOT NULL,
  current_ref TEXT NOT NULL,
  previous_ref TEXT,
  workspace_ref TEXT,
  observed_at REAL NOT NULL,
  source_position TEXT,
  provenance_id TEXT NOT NULL,
  superseded_at REAL
);
CREATE UNIQUE INDEX active_session_artifact
  ON agent_session_artifacts(agent_session_id, artifact_kind)
  WHERE superseded_at IS NULL;
CREATE INDEX session_artifact_history
  ON agent_session_artifacts(agent_session_id, artifact_kind,
                             observed_at DESC, id DESC);

CREATE TABLE agent_session_grouping (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  frozen_start_cwd TEXT,
  group_dir TEXT,
  resolution_state TEXT NOT NULL CHECK(resolution_state IN
    ('resolved','empty','unavailable','unknown')),
  resolved_at REAL NOT NULL,
  provenance_id TEXT NOT NULL
);
CREATE INDEX session_group_dir ON agent_session_grouping(group_dir);

CREATE TABLE peer_messages (
  id TEXT PRIMARY KEY,
  from_conversation_id TEXT NOT NULL
    REFERENCES conversations(id) ON DELETE CASCADE,
  from_agent_session_id TEXT
    REFERENCES agent_sessions(id) ON DELETE SET NULL,
  sender_actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE RESTRICT,
  sender_actor_key TEXT NOT NULL,
  to_conversation_id TEXT NOT NULL
    REFERENCES conversations(id) ON DELETE CASCADE,
  to_agent_session_id TEXT
    REFERENCES agent_sessions(id) ON DELETE SET NULL,
  recipient_actor_track_id TEXT
    REFERENCES conversation_actor_tracks(id) ON DELETE RESTRICT,
  recipient_actor_key TEXT,
  kind TEXT NOT NULL CHECK(kind IN
    ('prose','task_assignment','idle','lifecycle','termination',
     'acknowledgement','extension')),
  body_ref TEXT,
  task_operation_id TEXT REFERENCES operations(id) ON DELETE RESTRICT,
  external_message_id TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('pending','sent','delivered','read','failed','unknown')),
  reply_to_id TEXT REFERENCES peer_messages(id) ON DELETE SET NULL,
  source_position TEXT,
  source_timestamp REAL,
  provenance_id TEXT NOT NULL,
  CHECK((kind = 'prose' AND body_ref IS NOT NULL) OR kind <> 'prose'),
  CHECK((kind = 'task_assignment' AND task_operation_id IS NOT NULL) OR
        (kind <> 'task_assignment' AND task_operation_id IS NULL)),
  CHECK((recipient_actor_track_id IS NULL) = (recipient_actor_key IS NULL))
);
CREATE INDEX peer_messages_actor
  ON peer_messages(to_conversation_id, recipient_actor_key, state,
                   source_position);
CREATE UNIQUE INDEX peer_external_message
  ON peer_messages(from_conversation_id, external_message_id)
  WHERE external_message_id IS NOT NULL;
CREATE TRIGGER peer_message_actor_scope
BEFORE INSERT ON peer_messages BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.sender_actor_track_id
      AND t.conversation_id = NEW.from_conversation_id
      AND t.actor_key = NEW.sender_actor_key)
  THEN RAISE(ABORT, 'peer_sender_scope_mismatch') END;
  SELECT CASE WHEN NEW.recipient_actor_track_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.recipient_actor_track_id
      AND t.conversation_id = NEW.to_conversation_id
      AND t.actor_key = NEW.recipient_actor_key)
  THEN RAISE(ABORT, 'peer_recipient_scope_mismatch') END;
  SELECT CASE WHEN NEW.from_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.from_agent_session_id
      AND s.conversation_id = NEW.from_conversation_id)
  THEN RAISE(ABORT, 'peer_sender_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.to_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.to_agent_session_id
      AND s.conversation_id = NEW.to_conversation_id)
  THEN RAISE(ABORT, 'peer_recipient_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.task_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.task_operation_id
      AND o.conversation_id = NEW.from_conversation_id
      AND o.kind = 'agent_task')
  THEN RAISE(ABORT, 'peer_task_operation_scope_mismatch') END;
END;
CREATE TRIGGER peer_message_actor_scope_update
BEFORE UPDATE OF from_conversation_id, from_agent_session_id,
                 sender_actor_track_id, sender_actor_key,
                 to_conversation_id, to_agent_session_id,
                 recipient_actor_track_id, recipient_actor_key,
                 task_operation_id
ON peer_messages BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.sender_actor_track_id
      AND t.conversation_id = NEW.from_conversation_id
      AND t.actor_key = NEW.sender_actor_key)
  THEN RAISE(ABORT, 'peer_sender_scope_mismatch') END;
  SELECT CASE WHEN NEW.recipient_actor_track_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.recipient_actor_track_id
      AND t.conversation_id = NEW.to_conversation_id
      AND t.actor_key = NEW.recipient_actor_key)
  THEN RAISE(ABORT, 'peer_recipient_scope_mismatch') END;
  SELECT CASE WHEN NEW.from_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.from_agent_session_id
      AND s.conversation_id = NEW.from_conversation_id)
  THEN RAISE(ABORT, 'peer_sender_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.to_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.to_agent_session_id
      AND s.conversation_id = NEW.to_conversation_id)
  THEN RAISE(ABORT, 'peer_recipient_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.task_operation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.task_operation_id
      AND o.conversation_id = NEW.from_conversation_id
      AND o.kind = 'agent_task')
  THEN RAISE(ABORT, 'peer_task_operation_scope_mismatch') END;
END;

CREATE TABLE peer_message_deliveries (
  peer_message_id TEXT NOT NULL REFERENCES peer_messages(id) ON DELETE CASCADE,
  recipient_actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN
    ('pending','sent','delivered','read','failed','unknown')),
  effect_attempt_id TEXT,
  updated_at REAL NOT NULL,
  PRIMARY KEY(peer_message_id, recipient_actor_track_id)
);

CREATE TABLE provider_edge_installations (
  provider_id TEXT NOT NULL,
  backend_id TEXT NOT NULL,
  config_ref TEXT NOT NULL,
  installed_version TEXT,
  desired_version TEXT NOT NULL,
  config_digest TEXT,
  config_backup_ref TEXT,
  telemetry_env_digest TEXT,
  statusline_command_digest TEXT,
  delegated_statusline_ref TEXT,
  executable_digest TEXT,
  trust_key TEXT,
  trust_state TEXT NOT NULL CHECK(trust_state IN
    ('trusted','review_required','rejected','unknown','not_applicable')),
  last_verified_at REAL,
  state TEXT NOT NULL CHECK(state IN
    ('absent','installing','installed','degraded','reverting','failed')),
  last_error TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  PRIMARY KEY(provider_id, backend_id)
);

CREATE TABLE observations (
  id TEXT PRIMARY KEY,
  scope_kind TEXT NOT NULL CHECK(scope_kind IN
    ('conversation','machine','account','window','unknown')),
  scope_id TEXT,
  source_kind TEXT NOT NULL,
  backend_id TEXT REFERENCES backends(id) ON DELETE SET NULL,
  provider_id TEXT,
  source_identity TEXT,
  source_sequence TEXT,
  dedup_key TEXT NOT NULL UNIQUE,
  source_timestamp REAL,
  ingested_at REAL NOT NULL,
  payload_ref TEXT NOT NULL,
  schema_hint TEXT,
  edge_instance TEXT NOT NULL,
  flags TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(flags)),
  processing_state TEXT NOT NULL DEFAULT 'pending' CHECK(processing_state IN
    ('pending','processing','complete','complete_with_quarantine',
     'quarantined_identity','ignored')),
  mapper_name TEXT,
  mapper_version TEXT,
  claimant_id TEXT,
  lease_expires_at REAL,
  error_ref TEXT,
  CHECK((scope_kind = 'unknown') OR scope_id IS NOT NULL),
  CHECK((claimant_id IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX observation_claim
  ON observations(processing_state, lease_expires_at, ingested_at, id);
CREATE INDEX observation_source_order
  ON observations(backend_id, provider_id, source_identity, source_sequence,
                  id);

CREATE TABLE observation_consumers (
  observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
  consumer_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('pending','processing','applied','skipped','quarantined')),
  decision_code TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  next_attempt_at REAL,
  lease_id TEXT,
  lease_expires_at REAL,
  last_error_ref TEXT,
  PRIMARY KEY(observation_id, consumer_kind)
);
CREATE INDEX observation_consumer_claim
  ON observation_consumers(consumer_kind, state, next_attempt_at,
                           observation_id);

CREATE TABLE interaction_details (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
  agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  interaction_kind TEXT NOT NULL CHECK(interaction_kind IN
    ('question','permission','plan','confirm')),
  external_key TEXT NOT NULL,
  prompt_ref TEXT NOT NULL,
  options_ref TEXT,
  plan_ref TEXT,
  response_ref TEXT,
  response_revision INTEGER NOT NULL DEFAULT 0 CHECK(response_revision >= 0),
  state TEXT NOT NULL CHECK(state IN
    ('open','partially_answered','submitting','answered','declined','dismissed',
     'expired','lost')),
  verdict TEXT CHECK(verdict IS NULL OR verdict IN
    ('approved','changes','rejected','confirmed','denied','answered',
     'dismissed')),
  edited INTEGER CHECK(edited IS NULL OR edited IN (0,1)),
  current_question_index INTEGER CHECK(current_question_index >= 0),
  answered_question_count INTEGER NOT NULL DEFAULT 0
    CHECK(answered_question_count >= 0),
  total_question_count INTEGER CHECK(total_question_count >= 0),
  driver_layout TEXT,
  UNIQUE(agent_session_id, external_key),
  CHECK(total_question_count IS NULL OR
        answered_question_count <= total_question_count)
);
CREATE TRIGGER interaction_operation_session_match
BEFORE INSERT ON interaction_details BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.operation_id
      AND o.agent_session_id = NEW.agent_session_id
      AND o.kind = 'interaction')
  THEN RAISE(ABORT, 'interaction_operation_session_mismatch') END;
END;

CREATE TABLE slot_allocations (
  scope_id TEXT NOT NULL,
  entity_kind TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  slot_number INTEGER NOT NULL CHECK(slot_number >= 0),
  owner_pid INTEGER CHECK(owner_pid > 0),
  owner_host_instance_id TEXT,
  last_verified_at REAL,
  lease_expires_at REAL,
  allocated_at REAL NOT NULL,
  released_at REAL,
  PRIMARY KEY(scope_id, entity_kind, entity_id)
);
CREATE UNIQUE INDEX one_live_slot_number
  ON slot_allocations(scope_id, slot_number)
  WHERE released_at IS NULL;
CREATE INDEX reclaim_slots
  ON slot_allocations(released_at, lease_expires_at, owner_host_instance_id,
                      owner_pid);

CREATE TABLE agent_session_active_time (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  accumulated_ms INTEGER NOT NULL DEFAULT 0 CHECK(accumulated_ms >= 0),
  last_resumed_at REAL,
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  projection_revision INTEGER NOT NULL DEFAULT 0 CHECK(projection_revision >= 0),
  updated_at REAL NOT NULL
);

CREATE TABLE command_vocabulary_snapshots (
  provider_id TEXT NOT NULL,
  execution_target_id TEXT NOT NULL
    REFERENCES execution_targets(id) ON DELETE CASCADE,
  workspace_ref TEXT NOT NULL,
  actor_key TEXT NOT NULL DEFAULT '',
  payload_ref TEXT NOT NULL,
  freshness TEXT NOT NULL CHECK(freshness IN
    ('fresh','stale','unavailable')),
  revision INTEGER NOT NULL CHECK(revision >= 0),
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(provider_id, execution_target_id, workspace_ref, actor_key)
);

CREATE TABLE plugin_installations (
  plugin_id TEXT NOT NULL,
  version TEXT NOT NULL,
  manifest_digest TEXT NOT NULL,
  executable_digest TEXT NOT NULL,
  trust_class TEXT NOT NULL CHECK(trust_class IN
    ('bundled_in_process','trusted_in_process','untrusted_subprocess')),
  granted_permissions TEXT NOT NULL CHECK(json_valid(granted_permissions)),
  state TEXT NOT NULL CHECK(state IN
    ('disabled','starting','active','degraded','crash_loop','failed')),
  protocol_version TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  last_health TEXT,
  PRIMARY KEY(plugin_id, version)
);

CREATE TABLE surface_contributions (
  extension_id TEXT NOT NULL,
  placement TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  contribution_kind TEXT NOT NULL,
  badge TEXT,
  payload TEXT NOT NULL CHECK(json_valid(payload)),
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  projection_revision INTEGER NOT NULL CHECK(projection_revision >= 0),
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  tombstone INTEGER NOT NULL DEFAULT 0 CHECK(tombstone IN (0,1)),
  PRIMARY KEY(extension_id, placement, entity_type, entity_id)
);

CREATE TABLE tui_drafts (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  text_ref TEXT NOT NULL,
  line_count INTEGER NOT NULL CHECK(line_count > 0),
  clear_extent_lines INTEGER NOT NULL CHECK(clear_extent_lines > 0),
  source_operation_id TEXT NOT NULL REFERENCES operations(id),
  state TEXT NOT NULL CHECK(state IN
    ('occupying','consuming','consumed','cleared','unknown')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  consumed_at REAL,
  provenance_id TEXT NOT NULL
);

CREATE TABLE agent_session_input_occupancy (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  occupancy_kind TEXT NOT NULL CHECK(occupancy_kind IN
    ('free','interaction','daemon_draft','unknown')),
  interaction_operation_id TEXT REFERENCES operations(id),
  tui_draft_id TEXT REFERENCES tui_drafts(id),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  CHECK((occupancy_kind = 'free' AND interaction_operation_id IS NULL
                              AND tui_draft_id IS NULL) OR
        (occupancy_kind = 'interaction' AND interaction_operation_id IS NOT NULL
                                        AND tui_draft_id IS NULL) OR
        (occupancy_kind = 'daemon_draft' AND tui_draft_id IS NOT NULL
                                         AND interaction_operation_id IS NULL) OR
        occupancy_kind = 'unknown')
);

CREATE TABLE agent_session_input_modality (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK(mode IN
    ('insert','normal','interaction','command','unknown')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  source TEXT NOT NULL CHECK(source IN
    ('probe','verified_effect','failure','imported')),
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL
);

CREATE TABLE rewind_details (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
  requested_mode TEXT NOT NULL CHECK(requested_mode IN
    ('conversation','workspace','both')),
  effective_mode TEXT CHECK(effective_mode IN
    ('conversation','workspace','both')),
  checkpoint_external_id TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('driving','provisionally_applied','confirmed','degraded','failed',
     'indeterminate')),
  pre_rewind_head_id TEXT REFERENCES nodes(id),
  provisional_head_id TEXT REFERENCES nodes(id),
  restored_draft_id TEXT REFERENCES tui_drafts(id),
  provider_evidence_state TEXT NOT NULL CHECK(provider_evidence_state IN
    ('absent','provisional','confirmed','contradicted','unknown')),
  created_at REAL NOT NULL,
  resolved_at REAL
);

CREATE TABLE surface_control_attempts (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  surface_id TEXT NOT NULL,
  gesture TEXT NOT NULL,
  client_attempt_id TEXT NOT NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  phase TEXT NOT NULL CHECK(phase IN ('begin','ok','fail')),
  http_request_id TEXT,
  error_code TEXT,
  client_timestamp REAL NOT NULL,
  received_at REAL NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
  UNIQUE(surface_id, client_attempt_id, phase)
);
CREATE INDEX unmatched_control_attempt
  ON surface_control_attempts(surface_id, client_attempt_id, phase, received_at);

CREATE TABLE notification_settings (
  principal_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  toast_enabled INTEGER NOT NULL CHECK(toast_enabled IN (0,1)),
  web_push_enabled INTEGER NOT NULL CHECK(web_push_enabled IN (0,1)),
  telegram_enabled INTEGER NOT NULL CHECK(telegram_enabled IN (0,1)),
  telegram_always INTEGER NOT NULL CHECK(telegram_always IN (0,1)),
  resolve_push_enabled INTEGER NOT NULL CHECK(resolve_push_enabled IN (0,1)),
  pre_alert_delay_seconds REAL NOT NULL DEFAULT 0
    CHECK(pre_alert_delay_seconds >= 0 AND pre_alert_delay_seconds <= 3600),
  done_settle_seconds REAL NOT NULL DEFAULT 20 CHECK(done_settle_seconds >= 0),
  escalation_seconds REAL NOT NULL DEFAULT 300 CHECK(escalation_seconds >= 0),
  retractability_seconds REAL NOT NULL DEFAULT 86400
    CHECK(retractability_seconds >= 0 AND retractability_seconds <= 172800),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  updated_at REAL NOT NULL
);

CREATE TABLE conversation_notification_mutes (
  principal_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  muted INTEGER NOT NULL CHECK(muted IN (0,1)),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  updated_at REAL NOT NULL,
  PRIMARY KEY(principal_id, conversation_id)
);

CREATE TABLE presence_sessions (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  surface_id TEXT NOT NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  viewing_now INTEGER NOT NULL CHECK(viewing_now IN (0,1)),
  device_active_now INTEGER NOT NULL CHECK(device_active_now IN (0,1)),
  terminal_tab_focused_now INTEGER NOT NULL
    CHECK(terminal_tab_focused_now IN (0,1)),
  connection_generation INTEGER NOT NULL DEFAULT 0
    CHECK(connection_generation >= 0),
  current_connection_id TEXT,
  started_at REAL NOT NULL,
  last_heartbeat_at REAL NOT NULL,
  ended_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE INDEX live_presence
  ON presence_sessions(principal_id, ended_at, last_heartbeat_at);

CREATE TABLE device_presence (
  principal_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  device_kind TEXT NOT NULL CHECK(device_kind IN
    ('browser','terminal','phone','cli','other')),
  last_seen_at REAL NOT NULL,
  last_active_at REAL,
  last_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  routing_channel TEXT,
  platform TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  PRIMARY KEY(principal_id, device_id)
);
CREATE INDEX device_presence_mru
  ON device_presence(principal_id, last_active_at DESC, device_id);

CREATE TABLE push_key_material (
  key_id TEXT PRIMARY KEY,
  public_key TEXT NOT NULL,
  private_secret_ref TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','retiring','retired')),
  created_at REAL NOT NULL,
  retired_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE UNIQUE INDEX one_active_push_key
  ON push_key_material(state) WHERE state = 'active';

CREATE TABLE push_subscriptions (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  key_id TEXT NOT NULL REFERENCES push_key_material(key_id),
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth_secret_ref TEXT NOT NULL,
  platform TEXT NOT NULL,
  user_agent TEXT,
  created_at REAL NOT NULL,
  last_success_at REAL,
  failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
  resolve_window_started_at REAL,
  resolve_attempt_count INTEGER NOT NULL DEFAULT 0
    CHECK(resolve_attempt_count BETWEEN 0 AND 3),
  resolve_consecutive_failures INTEGER NOT NULL DEFAULT 0
    CHECK(resolve_consecutive_failures >= 0),
  resolve_state TEXT NOT NULL DEFAULT 'healthy'
    CHECK(resolve_state IN ('healthy','suspended','unavailable')),
  pending_stale_cleanup INTEGER NOT NULL DEFAULT 0
    CHECK(pending_stale_cleanup IN (0,1)),
  last_resolve_at REAL,
  last_resolve_error TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  state TEXT NOT NULL CHECK(state IN ('active','expired','disabled')),
  CHECK(device_id <> 'terminal')
);
CREATE INDEX push_device ON push_subscriptions(principal_id, device_id, state);

CREATE TABLE arms (
  id TEXT PRIMARY KEY,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  owner_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  arm_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('armed','claimed','suspended','fired','cancelled','expired')),
  due_at REAL,
  suspended_reason TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  lease_id TEXT,
  lease_expires_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  CHECK((state IN ('armed','claimed') AND due_at IS NOT NULL) OR
        state NOT IN ('armed','claimed'))
);
CREATE INDEX due_arms ON arms(state, due_at, id);

CREATE TABLE notification_intents (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  attention_transition_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('asking','done')),
  truth_state TEXT NOT NULL CHECK(truth_state IN ('true','false','unknown')),
  arm_state TEXT NOT NULL CHECK(arm_state IN
    ('armed','holding','held','disarmed','expired')),
  hold_audit_state TEXT NOT NULL DEFAULT 'unrecorded'
    CHECK(hold_audit_state IN ('unrecorded','recorded')),
  arm_id TEXT REFERENCES arms(id) ON DELETE RESTRICT,
  arm_revision INTEGER CHECK(arm_revision >= 0),
  due_at REAL,
  escalation_due_at REAL,
  policy_version INTEGER NOT NULL CHECK(policy_version > 0),
  cause TEXT NOT NULL,
  cancel_reason TEXT,
cancelled_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  CHECK((arm_id IS NULL AND arm_revision IS NULL) OR
        (arm_id IS NOT NULL AND arm_revision IS NOT NULL))
);
CREATE INDEX due_notification_intents
  ON notification_intents(arm_state, due_at, id);
CREATE TRIGGER notification_intent_scope_insert
BEFORE INSERT ON notification_intents BEGIN
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'notification_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.arm_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM arms a
    WHERE a.id = NEW.arm_id
      AND a.scope_type = 'conversation'
      AND a.scope_id = NEW.conversation_id
      AND a.owner_kind = 'notification_intent'
      AND a.owner_id = NEW.id
      AND a.revision = NEW.arm_revision
      AND a.due_at IS NEW.due_at)
  THEN RAISE(ABORT, 'notification_arm_copy_mismatch') END;
END;
CREATE TRIGGER notification_intent_scope_update
BEFORE UPDATE OF conversation_id, agent_session_id, arm_id, arm_revision, due_at
ON notification_intents BEGIN
  SELECT CASE WHEN NEW.conversation_id <> OLD.conversation_id
  THEN RAISE(ABORT, 'notification_conversation_is_immutable') END;
  SELECT CASE WHEN NEW.agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'notification_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.arm_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM arms a
    WHERE a.id = NEW.arm_id
      AND a.scope_type = 'conversation'
      AND a.scope_id = NEW.conversation_id
      AND a.owner_kind = 'notification_intent'
      AND a.owner_id = NEW.id
      AND a.revision = NEW.arm_revision
      AND a.due_at IS NEW.due_at)
  THEN RAISE(ABORT, 'notification_arm_copy_mismatch') END;
END;

CREATE TABLE notification_deliveries (
  id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL REFERENCES notification_intents(id) ON DELETE CASCADE,
  stage INTEGER NOT NULL CHECK(stage IN (1,2)),
  channel_id TEXT NOT NULL,
  device_id TEXT,
  collapse_tag TEXT,
  effect_attempt_id TEXT,
  external_handle_ref TEXT,
  delivery_state TEXT NOT NULL CHECK(delivery_state IN
    ('pending','sending','sent','failed','unknown','retracting','retracted','expired')),
  retractable INTEGER NOT NULL CHECK(retractable IN (0,1)),
  expires_at REAL,
  sent_at REAL,
  retracted_at REAL,
  remote_retraction_unavailable INTEGER NOT NULL DEFAULT 0
    CHECK(remote_retraction_unavailable IN (0,1))
);
CREATE INDEX intent_deliveries
  ON notification_deliveries(intent_id, delivery_state, stage);

CREATE TABLE notification_route_decisions (
  id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL REFERENCES notification_intents(id) ON DELETE CASCADE,
  stage INTEGER NOT NULL CHECK(stage IN (1,2)),
  winner_device_id TEXT,
  selected_channel TEXT,
  candidates_json TEXT NOT NULL CHECK(json_valid(candidates_json)),
  exclusions_json TEXT NOT NULL CHECK(json_valid(exclusions_json)),
  policy_version INTEGER NOT NULL,
  decided_at REAL NOT NULL
);

CREATE TABLE usage_facts (
  id TEXT PRIMARY KEY,
  usage_scope_key TEXT NOT NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  actor_key TEXT,
  account_id TEXT,
  source TEXT NOT NULL CHECK(source IN
    ('provider','otel','transcript','imported')),
  ledger TEXT NOT NULL CHECK(ledger IN
    ('billing','per_actor_display','quota')),
  query_source TEXT NOT NULL CHECK(query_source IN
    ('main','subagent','auxiliary')),
  temporality TEXT NOT NULL CHECK(temporality IN
    ('delta','cumulative_snapshot','message_snapshot')),
  model TEXT,
  input_tokens INTEGER CHECK(input_tokens >= 0),
  output_tokens INTEGER CHECK(output_tokens >= 0),
  cache_read_tokens INTEGER CHECK(cache_read_tokens >= 0),
  cache_create_5m_tokens INTEGER CHECK(cache_create_5m_tokens >= 0),
  cache_create_1h_tokens INTEGER CHECK(cache_create_1h_tokens >= 0),
  cache_create_unclassified_tokens INTEGER
    CHECK(cache_create_unclassified_tokens >= 0),
  vendor_cost_minor INTEGER CHECK(vendor_cost_minor >= 0),
  vendor_currency TEXT,
  vendor_cost_source TEXT,
  source_position TEXT,
  source_record_key TEXT NOT NULL,
  dedup_key TEXT NOT NULL,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  UNIQUE(source, ledger, query_source, usage_scope_key, dedup_key,
         source_record_key)
);
CREATE INDEX usage_query
  ON usage_facts(ledger, source, query_source, account_id, agent_session_id,
                 observed_at);

CREATE TABLE quota_windows (
  account_id TEXT NOT NULL,
  provider_id TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  model_scope TEXT NOT NULL DEFAULT '',
  window_minutes INTEGER NOT NULL CHECK(window_minutes > 0),
  used_percent REAL CHECK(used_percent >= 0 AND used_percent <= 100),
  resets_at REAL,
  state TEXT NOT NULL CHECK(state IN
    ('available','limited','logged_out','unknown')),
  source_kind TEXT NOT NULL CHECK(source_kind IN ('push','pull','imported')),
  source_key TEXT NOT NULL,
  freshness TEXT NOT NULL CHECK(freshness IN ('fresh','stale','unavailable')),
  last_error_code TEXT,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(account_id, provider_id, scope_key, model_scope, window_minutes,
              source_kind, source_key)
);
CREATE INDEX effective_quota_window
  ON quota_windows(account_id, provider_id, scope_key, model_scope,
                   window_minutes, freshness, observed_at DESC);

CREATE TABLE usage_credit_state (
  source TEXT NOT NULL,
  ledger TEXT NOT NULL,
  query_source TEXT NOT NULL CHECK(query_source IN
    ('main','subagent','auxiliary')),
  usage_scope_key TEXT NOT NULL,
  dedup_key TEXT NOT NULL,
  input_tokens INTEGER CHECK(input_tokens >= 0),
  output_tokens INTEGER CHECK(output_tokens >= 0),
  cache_read_tokens INTEGER CHECK(cache_read_tokens >= 0),
  cache_create_5m_tokens INTEGER CHECK(cache_create_5m_tokens >= 0),
  cache_create_1h_tokens INTEGER CHECK(cache_create_1h_tokens >= 0),
  cache_create_unclassified_tokens INTEGER
    CHECK(cache_create_unclassified_tokens >= 0),
  vendor_cost_minor INTEGER CHECK(vendor_cost_minor >= 0),
  vendor_currency TEXT,
  source_position TEXT,
  PRIMARY KEY(source, ledger, query_source, usage_scope_key, dedup_key)
);

CREATE TABLE usage_source_authority (
  usage_scope_key TEXT NOT NULL,
  ledger TEXT NOT NULL,
  query_source TEXT NOT NULL CHECK(query_source IN
    ('main','subagent','auxiliary')),
  selected_source TEXT NOT NULL CHECK(selected_source IN
    ('provider','otel','transcript','imported')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  evidence_ref TEXT NOT NULL,
  selected_at REAL NOT NULL,
  PRIMARY KEY(usage_scope_key, ledger, query_source)
);

CREATE TABLE usage_source_rollups (
  usage_scope_key TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN
    ('provider','otel','transcript','imported')),
  ledger TEXT NOT NULL,
  query_source TEXT NOT NULL CHECK(query_source IN
    ('main','subagent','auxiliary')),
  provider_id TEXT NOT NULL,
  model TEXT NOT NULL,
  pricing_epoch TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
  cache_read_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_read_tokens >= 0),
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_5m_tokens >= 0),
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_1h_tokens >= 0),
  cache_create_unclassified_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_unclassified_tokens >= 0),
  vendor_cost_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(vendor_cost_json)),
  error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
  updated_at REAL NOT NULL,
  PRIMARY KEY(usage_scope_key, source, ledger, query_source, provider_id, model,
              pricing_epoch)
);

CREATE TABLE daily_usage_source_rollups (
  day TEXT NOT NULL,
  usage_scope_key TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN
    ('provider','otel','transcript','imported')),
  ledger TEXT NOT NULL,
  query_source TEXT NOT NULL CHECK(query_source IN
    ('main','subagent','auxiliary')),
  provider_id TEXT NOT NULL,
  model TEXT NOT NULL,
  pricing_epoch TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
  cache_read_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_read_tokens >= 0),
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_5m_tokens >= 0),
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_1h_tokens >= 0),
  cache_create_unclassified_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_unclassified_tokens >= 0),
  vendor_cost_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(vendor_cost_json)),
  error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
  PRIMARY KEY(day, usage_scope_key, source, ledger, query_source, provider_id, model,
              pricing_epoch)
);
CREATE INDEX daily_usage_range
  ON daily_usage_source_rollups(day, usage_scope_key, query_source, provider_id,
                                model);

CREATE TABLE agent_session_lifecycle (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  host_state TEXT NOT NULL CHECK(host_state IN
    ('starting','live','parked','ended','lost')),
  work_state TEXT NOT NULL CHECK(work_state IN
    ('active','drained','unknown','lost')),
  park_requested_at REAL,
  host_ended_at REAL,
  work_drained_at REAL,
  freshness TEXT NOT NULL CHECK(freshness IN ('fresh','stale','unknown')),
  provenance_id TEXT NOT NULL
);

CREATE TABLE workspace_dirty_cache (
  backend_id TEXT NOT NULL,
  workspace_ref TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('dirty','clean','unknown')),
  fingerprint TEXT,
  observed_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  error_code TEXT,
  PRIMARY KEY(backend_id, workspace_ref)
);
CREATE INDEX dirty_cache_expiry ON workspace_dirty_cache(expires_at);

CREATE TABLE structural_changes (
  change_id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_type TEXT NOT NULL CHECK(scope_type IN
    ('system','principal','conversation')),
  scope_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  authorization_revision INTEGER NOT NULL CHECK(authorization_revision >= 0),
  event_name TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  scope_revision INTEGER NOT NULL CHECK(scope_revision >= 0),
  entity_type TEXT,
  entity_id TEXT,
  entity_revision INTEGER,
  previous_entity_revision INTEGER NOT NULL DEFAULT 0
    CHECK(previous_entity_revision >= 0),
  operation TEXT NOT NULL DEFAULT 'upsert' CHECK(operation IN
    ('upsert','delete','append','amend','retract','move','supersede','instruction')),
  payload_ref TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX structural_replay
  ON structural_changes(scope_type, scope_id, principal_id,
                        authorization_revision, change_id);
CREATE INDEX structural_retention ON structural_changes(created_at, change_id);

CREATE TABLE materialized_activity (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  head_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
  actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  generation INTEGER NOT NULL CHECK(generation >= 0),
  position_key TEXT NOT NULL,
  local_sequence INTEGER NOT NULL CHECK(local_sequence >= 0),
  item_type TEXT NOT NULL CHECK(item_type IN ('node','operation','notice')),
  item_id TEXT NOT NULL,
  group_id TEXT,
  activity_class TEXT NOT NULL,
  register_name TEXT NOT NULL,
  audience TEXT NOT NULL CHECK(audience IN ('lead','actor','both','hidden')),
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  payload_ref TEXT NOT NULL,
  PRIMARY KEY(conversation_id, actor_track_id, generation, position_key,
              local_sequence, item_id)
);
CREATE INDEX activity_page
  ON materialized_activity(conversation_id, actor_track_id, generation,
                           position_key DESC, local_sequence DESC, item_id DESC);
CREATE TRIGGER materialized_activity_scope_insert
BEFORE INSERT ON materialized_activity BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'activity_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.head_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.actor_track_id)
  THEN RAISE(ABORT, 'activity_head_scope_mismatch') END;
END;
CREATE TRIGGER materialized_activity_scope_update
BEFORE UPDATE OF conversation_id, actor_track_id, head_node_id
ON materialized_activity BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'activity_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.head_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.actor_track_id)
  THEN RAISE(ABORT, 'activity_head_scope_mismatch') END;
END;

CREATE TABLE activity_projection_state (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  head_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
  active_generation INTEGER NOT NULL CHECK(active_generation >= 0),
  building_generation INTEGER CHECK(building_generation >= 0),
  freshness TEXT NOT NULL CHECK(freshness IN ('fresh','stale','rebuilding')),
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  invalid_from_position TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  PRIMARY KEY(conversation_id, actor_track_id),
  CHECK(building_generation IS NULL OR
        building_generation <> active_generation)
);
CREATE TRIGGER activity_projection_scope_insert
BEFORE INSERT ON activity_projection_state BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'activity_projection_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.head_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.actor_track_id)
  THEN RAISE(ABORT, 'activity_projection_head_scope_mismatch') END;
END;
CREATE TRIGGER activity_projection_scope_update
BEFORE UPDATE OF conversation_id, actor_track_id, head_node_id
ON activity_projection_state BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'activity_projection_track_scope_mismatch') END;
  SELECT CASE WHEN NEW.head_node_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.actor_track_id)
  THEN RAISE(ABORT, 'activity_projection_head_scope_mismatch') END;
END;

CREATE TABLE legacy_import_runs (
  id TEXT PRIMARY KEY,
  source_database_fingerprint TEXT NOT NULL UNIQUE,
  source_path TEXT NOT NULL,
  source_stat_json TEXT NOT NULL CHECK(json_valid(source_stat_json)),
  schema_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('running','completed','completed_with_errors','cancelled','failed')),
  started_at REAL NOT NULL,
  finished_at REAL,
  result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
  UNIQUE(id, source_database_fingerprint)
);

CREATE TABLE legacy_import_rows (
  run_id TEXT NOT NULL,
  source_database_fingerprint TEXT NOT NULL,
  legacy_table TEXT NOT NULL,
  legacy_row_id TEXT NOT NULL,
  result_kind TEXT NOT NULL CHECK(result_kind IN
    ('imported','duplicate','quarantined','unavailable')),
  canonical_type TEXT,
  canonical_id TEXT,
  error_ref TEXT,
  imported_at REAL NOT NULL,
  FOREIGN KEY(run_id, source_database_fingerprint)
    REFERENCES legacy_import_runs(id, source_database_fingerprint)
    ON DELETE RESTRICT,
  PRIMARY KEY(source_database_fingerprint, legacy_table, legacy_row_id)
);
```

The notification tables in the final clean DDL use the three-axis model in
Section 38.16 rather than the single `state` inventory in Section 8.8. The final
schema also adds the Section 38.12 columns to `interaction_details`, the
Section 38.8 columns to `streams`, and the Section 38.13 columns to `nodes`.
Migration backfills every existing Node into the lead actor track, maps old
notification states into truth/arm/delivery axes without inventing delivery,
and maps missing values to `unknown`, never to false or zero.

### 38.28 Accepted complexity: retained mechanisms

The review proposed simplifying several mechanisms. Those proposals are
rejected for v4. The implementation must retain and finish these designs:

| Mechanism retained | Required concrete behavior |
|---|---|
| Evidence and provenance for every Observation | Raw Observation, consumer decisions, provenance links, skip decisions, quarantine, retention, and repair remain separate records. High volume is controlled by retention and bounded payload references, not by deleting the model. |
| Supervised coordinator per Conversation | Each has a bounded mailbox, overflow policy, durable rehydration, probe scheduling, parking, restart, and health. A simple lock is not a conforming replacement. |
| Checksummed framed staging files | Every open Stream uses versioned frames, checksums, torn-tail recovery, durable cursors, and final sealing. Plain unframed append files are not conforming. |
| Activity generations and amendments | `append`, `amend`, `move`, `supersede`, generation rebuild, and `resnapshot_required` remain supported and tested. |
| Structural cursor replay | The 24-hour/100,000-event retained feed and exact replay protocol in Section 38.22 remain required. |
| Transactional outbox for all external effects and structural publication | No direct terminal paint, pane operation, feed publication, alert, launch, provider control, or peer delivery bypasses the outbox. Idempotent actions can be optimized inside workers but retain durable request/attempt/receipt rows. |
| Restart-safe temporary state | Open Operations, correlations, arms, notifications, latches, source registrations, attempts, workflows, and declared durable drafts survive restart. Only facets explicitly declared live-only may disappear. |
| Draft CAS/tombstones/origin echo | Full Section 8.5 behavior remains required. |
| Provider and terminal capability interfaces | All early interfaces remain. Missing terminal roles found by the review are added; they are not collapsed to one legacy surface. |
| Content-addressed blob store | SHA-256 addressing, manifests, reachability, orphan collection, retention and integrity verification remain. Per-stream plain files are not a replacement for sealed blobs. |
| One machine-wide SQLite database | All metadata and workflow tables remain in one WAL database. Per-Conversation database files are forbidden. |

These choices intentionally accept more code, rows, and operational machinery.
Performance work may optimize implementations without removing observable
states, evidence, recovery, or protocol behavior.

### 38.29 Migration discipline while the legacy system is still changing

The migration does not assume the legacy implementation is static. The rule is:

1. Phase 0 freezes a versioned fixture/oracle corpus from live artifacts and
   records the exact legacy commit that produced each expected result.
2. Until an ownership plane transfers, the legacy implementation remains the
   production owner.
3. Every user-visible legacy change after Phase 0 must update the v4 rule and
   fixture in the same change. Once the corresponding v4 plane exists, that
   change must also update the new implementation before merge.
4. Provider drift discovered in production first becomes a minimized raw
   fixture and measured counterexample. Code changes follow the fixture.
5. During a sustained-parity gate, the compared legacy commit, v4 commit,
   provider version, fixture set, allowed differences, and date range are
   frozen in the gate report. New unrelated behavior restarts only the affected
   gate, not the whole migration.
6. After a plane transfers, legacy receives only rollback-critical fixes for
   that plane and is otherwise frozen until removal.

The read-only Phase 1–2 path is deployed against live legacy traffic before any
provider-driving effect transfers. It validates Conversation/actor identity,
branching, Activity, usage, attention, and parked import without risking user
input or provider state.

During every shadow phase, the transform registry also registers the exact
legacy `claude-cmd-pre.py` tee wrapper emitted through `updatedInput`, including
each measured historical wrapper version. Ingestion recognizes and inverts that
legacy wrapper before deduplication, command classification, render detection,
or parity comparison, exactly like a v4 `PreparedTransform`. A wrapper shape
that is not in the measured registry yields `unknown_transform`, preserves raw
bytes, and forbids confident command/render classification. Phase 2 cannot pass
while any supported legacy fixture reaches semantic consumers still wrapped.

### 38.30 Concrete performance gates

The Section 27.3 compound benchmark runs on the slowest supported packaged
macOS machine and the slowest supported packaged Linux machine with 20 active
Conversations, one 50 MiB build stream, five simultaneous assistant streams,
two active SSE clients, one pane client, alert scanning, and projection rebuild.

The checked-in benchmark manifest fixes: 15-minute duration; a seeded database
of 10,000 Conversations, 1,000,000 Nodes, 2,000,000 Operations, 100,000 open or
retained feed rows per hot scope, and 20 GiB retained blobs; warm-cache and
cold-start runs; NVMe-class local storage; exact OS/Python/SQLite versions; a
10 MiB/s build stream with 50 MiB/s 2-second bursts; five assistant streams at
20 revisions/s each; 200 Observations/s with 1,000/s 10-second bursts; and
fixed measurement boundaries from edge receipt/outbox commit to verified
surface application. Results record CPU model, RAM, filesystem, free space,
power mode, and database page/WAL settings.

An invalidation-storm phase uses a 100,000-item Conversation and injects, every
two seconds for five minutes, alternating earliest-position rewind, late child
result, compaction revert, and branch reselection while all other load remains.
The team phase adds one Conversation with 20 concurrent actor tracks, 20
assistant streams, lifecycle mail, scorebar updates, and child completion
reordering. The pane phase measures a 100,000-item view-mode toggle, a
width-changing repaint with syntax highlighting, and 100 ordinary appends.

The release gates are:

- answerable-hook Baqylau pass-through rate below 0.1% over 10,000 eligible
  requests and p99 end-to-end use below 70% of that hook's provider deadline;
- zero SQLite `BUSY` errors escaping the adapter and p99 writer-lock wait below
  50 ms;
- writer admission wait p95 below 25 ms and p99 below 100 ms, transaction p99
  below 50 ms/max below 250 ms, no admitted FIFO writer waits more than 500 ms,
  and retry counts reported by work kind for diagnosis only; work kind does not
  change queue order;
- observation-to-attention p95 below 150 ms and p99 below 500 ms;
- command-output byte availability at the pane p95 at or below 250 ms and p99
  below 500 ms from edge receipt;
- assistant Stream availability at web and pane p95 below 250 ms and p99 below
  500 ms;
- an ordinary pane append performs no full repaint and applies within 100 ms
  p95/250 ms p99; view-mode toggle or width reflow reaches first stable paint
  within 500 ms p95/1.5 s p99 on the 100,000-item fixture;
- cached syntax-highlight repaint is below 250 ms p95 and cache-miss highlight
  below 1 s p95 for a 10,000-line source block; repeated numeric live-facet
  updates coalesce to the newest value per animation frame without dropping a
  structural revision;
- verified tab paint p95 below 200 ms and p99 below 750 ms;
- accepted control HTTP response p95 below 150 ms, excluding provider
  completion, and no control silently lost;
- no subscriber can raise ingestion latency by more than 5%; overflow must
  close/resnapshot that subscriber;
- daemon restart exposes read health within 2 seconds and completes recovery of
  10,000 open/pending durable records within 30 seconds; and
- p99 latency in an idle Conversation rises by less than 20% while another
  Conversation runs the 50 MiB build.
- invalidation backlog drains within 10 seconds after the storm, stale Activity
  age stays below 15 seconds, and rebuild never violates the writer-admission
  gates;
- WAL remains below 256 MiB, a passive/restart checkpoint makes progress at
  least every five minutes, and a five-minute long reader produces a loud
  health alarm without exhausting disk;
- forced termination during frame append, seal, checkpoint, Blob GC, and online
  backup recovers with no falsely sealed content, reachable Blob loss, or
  duplicate external effect; and
- online backup completes within 60 seconds on the seeded database, raises
  foreground p99 by less than 20%, Blob/staging growth matches accepted input
  plus 5%, and orphan GC reclaims all eligible test objects within two cycles.
- the 20-actor team phase loses or duplicates zero messages/results, child
  result placement converges within 500 ms p99, and its idle-Conversation p99
  remains inside the same 20% isolation gate;
- database health reports main DB, WAL, staging, and Blob bytes plus growth/day
  and estimated exhaustion. Facts are classified `canonical_lifetime`,
  `retained_delivery`, `diagnostic_bounded`, or `rebuildable_projection`; only
  the last three can have a retention delete rule, and every rule names the
  lifetime owner it preserves.

Failing a gate requires profiling and an explicit corrective design. Allowed
responses include batching, coalescing, indexes, shorter transactions, reader
isolation, and checkpoint tuning. Another database engine,
per-Conversation SQLite databases, and removal of retained mechanisms are not
allowed responses within v4.

### 38.31 Review-finding traceability and required acceptance fixtures

Every accepted review finding has an owner and a required test. Test names
below are normative behaviors; fixture files use the corresponding stem under
`tests/fixtures/legacy_coverage/`.

| Finding | Resolution | Required acceptance test |
|---|---|---|
| 1.1 | Actor tracks, Section 38.1 | `child_dialogue_has_independent_head` and `lead_view_places_child_causally` |
| 1.2 | Current context owner, Section 38.2 | `context_without_compaction_is_visible` |
| 1.3 | Goal/task/plan/title projection contracts, Section 38.2 | `goal_tail_without_hook`, `tasks_captured_before_session_end_wipe` |
| 1.4 | Runtime revision/fallback forward scan, Section 38.2 | `mid_file_model_fallback_updates_effective_model` |
| 1.5 | Focus resolution and pane APIs, Section 38.10 | `payloadless_pane_gesture_resolves_focused_session` |
| 1.6 | Stored render kind, Section 38.8 | `raw_pre_transform_command_sets_shared_render_kind` |
| 1.7 | Producer unescape plus safe presentation, Section 38.8 | `textual_escape_restored_only_for_command_output` |
| 1.8 | Attention-gated active clock, Section 38.10 | `active_clock_pauses_while_done` |
| 1.9 | PID/host slot reclaim, Section 38.10 | `dead_slot_owner_is_reclaimed` and `eperm_owner_is_alive` |
| 1.10 | Subscription manifest and disabled delegating hooks, Section 38.4 | `worktree_delegating_hooks_never_installed` |
| 1.11 | Edge install/trust/upgrade/revert, Section 38.4 | `hash_change_requires_human_retrust` |
| 1.12 | Artifact relocation and frozen grouping, Section 38.3 | `enter_worktree_moves_reader_not_group` and `child_cwd_does_not_flap_host` |
| 1.13 | Auto/manual migration differences and nudge, Section 38.18 | `auto_migration_redrives_turn`, `manual_migration_bypasses_dead_park` |
| 1.14 | Non-answerable edge gate, Sections 38.4/38.30 | `non_answerable_edge_p99_under_50ms` |
| 1.15 | Parked corpus importer, Section 38.3 | `legacy_parked_import_is_idempotent_and_preserves_output` |
| 1.16 | Worktree grouping/hide semantics, Section 38.3 | `hidden_group_reappears_for_new_session` and `live_group_cannot_hide` |
| 1.17 | Scoped command vocabulary, Section 38.10 | `empty_child_vocabulary_does_not_inherit_host` |
| 1.18 | Canonical usage rollups, Section 38.17 | `stats_survive_evidence_pruning` |
| 1.19 | Version-coherent extension contribution, Section 38.10 | `extension_badge_and_payload_share_revision` |
| 1.20 | Exclusive input occupancy, Section 38.11 | `message_during_interaction_is_rejected_without_typing` |
| 1.21 | Daemon-authored TUI draft, Section 38.11 | `next_send_replaces_takeback_draft_line_by_line` |
| 1.22 | Suspect retraction, Section 38.13 | `takeback_hides_prompt_until_descendant_or_sibling` |
| 1.23 | Surface control telemetry, Section 38.14 | `begin_without_arrival_is_anomaly` |
| 1.24 | Self-caused escape-recheck, Sections 38.6/38.14 | `interrupt_recheck_uses_pre_key_baseline` and `refused_stop_skips_recheck` |
| 1.25 | Client-only ghost acceptance, Section 38.10 | `ghost_accept_never_types_into_tui` |
| 1.26 | Toast premise/global gate, Section 38.15 | `focused_page_toasts_and_suppresses_duplicate_external` |
| 1.27 | Push registry/VAPID stability, Section 38.15 | `push_410_expires_subscription` and `key_rotation_reports_orphans` |
| 1.28 | Public query deep link, Section 38.15 | `external_alert_never_uses_loopback_or_fragment` |
| 1.29 | Resolve budget, route evidence, terminal reservation, retraction truth, Sections 38.15–38.16 | `silent_resolve_budget_falls_back`, `terminal_device_cannot_be_impersonated`, `nonretractable_delivery_is_labelled` |
| 1.30 | Registered anomaly catalogue, Section 38.19 | `malformed_json_does_not_abort_anomalies` |
| 1.31 | Coupled triage artifacts, Section 38.19 | `schema_catalog_and_playbooks_cover_schema` |
| 1.32 | OTEL CLI, kill switches, no-spool outage decision, Sections 38.18–38.19 | `relimit_off_stamps_without_migrating`, `daemon_outage_records_gap_without_replay` |
| 2.1 | Ack/completion and unmatched closer, Section 38.6 | `async_launch_ack_does_not_close` and `unmatched_hidden_agent_closer_materializes` |
| 2.2 | Parsed-record markers/self-byproduct exclusion, Section 38.6 | `nested_marker_text_does_not_interrupt` and `effect_byproduct_cannot_reconcile_itself` |
| 2.3 | Invertible transforms, Section 38.5 | `wrapped_command_is_inverted_before_all_consumers` |
| 2.4 | Missing registered closer safe default, Section 38.6 | `missing_post_tool_batch_never_marks_success` |
| 2.5 | Negative start evidence and arrival race, Section 38.6 | `instructions_loaded_blocks_adoption_before_session_start` |
| 2.6 | Scrubbed continuation environment, Section 38.6 | `fork_inherits_snapshot_and_absent_is_unknown` |
| 2.7 | Consumer failure isolation, Section 38.5 | `task_mapper_crash_preserves_attention_and_evidence` |
| 2.8 | Per-family source authority, Section 38.7 | `hook_transcript_race_uses_family_authority` |
| 2.9 | Successor record, post-state attention, and parked background behavior, Sections 38.6/38.20/38.21 | `queued_prompt_after_interrupt_keeps_watching`, `slot_released_before_attention`, `parked_monitor_keeps_ingesting` |
| 2.10 | Three stream caps and WYSIWYG copy, Section 38.8 | `huge_newline_free_output_is_bounded_and_honest` |
| 2.11 | Safe SGR/OSC 8 preservation, Section 38.8 | `producer_color_survives_unsafe_controls_do_not` |
| 2.12 | Generic block/activity class, Section 38.9 | `parser_renderer_kind_sets_are_equal` and `unknown_tool_renders_generic` |
| 2.13 | Multi-resource output block, Section 38.9 | `multi_file_read_remains_one_block` |
| 2.14 | OSC 8/open-action click channel, Section 38.9 | `click_handler_does_not_enable_mouse_reporting` |
| 2.15 | Mandatory viewport restore, Section 38.9 | `global_anchor_restores_after_full_reflow` |
| 2.16 | Typed actor-addressed peer mail, Section 38.1 | `lifecycle_mail_not_rendered_as_prose` and `all_mail_directions_visible_once` |
| 2.17 | Late compaction correction/abandoned compaction, Section 38.13 | `parent_graph_reveals_revert_late` and `missing_postcompact_expires_latch_not_operation` |
| 2.18 | Last prompt sibling and subtree selection, Section 38.13 | `tool_result_wrapper_is_not_prompt_branch` and `ancestor_parented_fork_is_accepted` |
| 2.19 | Full interaction verdict/progress/decline-plus-delivery, Section 38.12 | `plan_changes_preserves_feedback` and `typed_preview_answer_creates_two_operations` |
| 2.20 | No generic cancel cleanup, Section 38.12 | `failed_plan_drive_never_presses_escape` |
| 2.21 | Provisional rewind/degraded both/auto confirmation, Section 38.13 | `rewind_gap_uses_provisional_head`, `both_degrades_honestly`, `clicked_switch_confirms_expected_menu` |
| 2.22 | Queue evidence, retry stop, input primitives/mode/fresh binding, Section 38.14 | `pre_paste_probe_owns_queued_state`, `queue_drained_stops_escape_retry`, `stale_window_never_receives_text`, `failed_drive_forces_mode_normalization` |
| 2.23 | Title ownership transfer/artifact proof, Section 38.2 | `live_title_remains_provider_owned`, `missing_artifact_returns_410_before_control` |
| 2.24 | Held notification state, Section 38.16 | `viewing_question_holds_then_fires_after_leaving` |
| 2.25 | Three alert clocks, Section 38.16 | `done_seen_during_settle_disarms`, `mute_applies_to_held_send` |
| 2.26 | Explicit presence end and last-seen split, Section 38.15 | `away_stops_suppression_but_keeps_routing_history` |
| 2.27 | One-device MRU/escalation/delivery-arm split, Section 38.16 | `browser_wins_tie`, `push_escalates_once`, `telegram_does_not_escalate`, `sent_delivery_can_remain_armed` |
| 2.28 | Per-field durable snapshot credit, Section 38.17 | `growing_message_snapshots_credit_only_positive_delta_across_restart` |
| 2.29 | Unknown cache split and vendor cost, Section 38.17 | `otel_cache_creation_stays_unclassified`, `vendor_and_calculated_cost_both_visible` |
| 2.30 | Read-time suppressions/global warning/consumption evidence, Section 38.19 | `new_suppression_clears_existing_warning`, `global_warning_at_most_once`, `atomic_take_records_consumer` |
| 2.31 | Event-triggered migration, logout, ladder, push/pull quotas, Section 38.18 | `percent_never_triggers_migration`, `authentication_failure_is_logout`, `accounts_exhaust_before_model_downgrade` |
| 2.32 | Skip evidence, universal observations, identity resolver, scoped resets, Sections 38.5/38.18–38.19 | `every_skip_has_decision`, `unknown_event_is_audited`, `repaired_alias_uses_indexed_resolver`, `weekly_reset_never_uses_5h_cadence` |
| 2.33 | Group folding pagination and three-valued dirty cache, Sections 38.9/38.23 | `older_page_folds_into_live_group`, `dirty_unknown_never_renders_clean` |

The third legacy-coverage review adds these mandatory grouped fixtures. A group
is complete only when every named assertion passes; grouping does not permit an
implementor to omit an individual behavior described in its owner section.

| Review-3 area | Owner | Required acceptance fixtures |
|---|---|---|
| View modes and resume search | Sections 38.9, 38.22, 38.24, 38.38 | `view_mode_cross_device_own_echo_suppressed`, `focus_dims_without_recutting_runs`, `resume_search_scans_title_and_native_sid_before_limit` |
| OTLP wire/install/delta identity | Sections 38.4, 38.26 | `otlp_gzip_chunked_always_200`, `identical_delta_receipts_credit_twice`, `claude_install_verifies_telemetry_temporality_and_statusline` |
| Terminal presence, layout, highlighting | Sections 38.8, 38.10 | `terminal_frontmost_updates_reserved_device`, `wide_tabbed_sgr_wrap_uses_cells`, `highlight_cache_keys_item_revision_and_width` |
| Claude/Codex child correlation and result authority | Sections 38.1, 38.6, 38.7, 38.37 | `claude_pretool_fifo_binds_start`, `claude_torn_meta_retries_same_signature`, `codex_final_answer_survives_trailing_token_count` |
| Sidecar and lifecycle frames | Sections 38.1, 38.37 | `codex_slug_and_atomic_identityless_claim`, `lifecycle_frame_words_unknown_type_without_raw_json` |
| Tailer/monitor/interrupt | Sections 38.6, 38.8, 38.14 | `tail_checkpoint_includes_pending_and_dropped`, `lsof_failure_is_writer_alive`, `vim_interrupt_uses_at_most_four_busy_escapes` |
| Viewport and pinned scorebar | Sections 38.9–38.10, 40.3 | `dsr_arrival_orders_restore_without_using_value`, `gross_miss_repeats_absolute_once_then_delta_corrects`, `scorebar_window_excluded_from_resize_geometry` |
| Usage attribution and Σ normalization | Sections 38.17, 40.3 | `auxiliary_usage_survives_fact_pruning`, `gross_input_normalizes_before_authority_swap`, `scoreboard_total_adds_cache_read_and_keeps_four_cache_fields` |
| Alert precedence and push collapse | Sections 38.15–38.16, 40.4 | `composing_overrides_web_viewing_and_retracts`, `done_delay_is_max_not_sum`, `push_alert_and_resolve_share_conversation_tag` |
| Complete legacy import | Section 38.3 | `audit_prefs_counters_and_kv_import_idempotently`, `pre_flag_audience_register_fallback_matches_corpus` |
| Physical schema owners and storage ports | Sections 38.25, 38.27, 38.35, 40.7 | `clean_install_has_every_trace_table`, `schema_sql_executes_and_digest_matches`, `storage_manifest_methods_resolve_to_real_tables` |
| Overview, git, stats, memory, badges and drafts | Sections 38.23, 38.38, 40.2–40.4 | `overview_single_revision_contains_full_card`, `git_timeout_caches_unknown_for_ten_seconds`, `pulse_counts_lost_sessions_inactive`, `namespace_draft_blur_prunes_oldest_settled` |
| Controls, repair and diagnostic honesty | Sections 17.4, 25.2, 38.19 | `readonly_rechecked_before_outbox_effect`, `repair_scaffold_has_preview_verify_and_rollback`, `audit_off_keeps_health_warning_light`, `warning_line_cannot_recurse` |
| Provider edge cases | Sections 38.2, 38.18, 38.37 | `disable_1m_outranks_all_context_inputs`, `statusline_ms_and_null_preservation`, `relimit_stamps_before_disable_and_honors_cooldown`, `subagent_stop_agent_type_never_blocks_close` |
| Display/probe honesty | Sections 38.10, 40.3–40.4 | `tab_paint_skip_and_failure_are_audited`, `blocked_chip_has_no_duration`, `ghost_probe_rejects_modal_input_box` |

### 38.32 Resolution of the eight internal contradictions

| Contradiction | Final decision |
|---|---|
| Write-time health counter vs later benign suppression | Raw errors/counters never decrement; the visible effective count applies the current suppression registry at read time (Section 38.19). |
| Prunable usage evidence vs stable cumulative totals | Same-transaction per-source lifetime/daily token rollups survive pruning; an authority pointer selects one billing source and calculated cost remains read-time (Section 38.17). |
| Whole-block page boundary vs interleaved group rows | Pages never split an item; `group_id` folds non-contiguous older items into a logical block (Section 38.9). |
| Durable dirty fingerprint vs TTL query | Remove the workspace column; `workspace_dirty_cache` is the sole three-valued TTL owner (Section 38.23). |
| Feed publication outbox vs feed not being truth | Publication remains durable outbox work; clients still treat snapshots/canonical tables as truth and resnapshot on invalid replay (Section 38.22). |
| General arms vs notification-local due time | `arms` owns timers; notification due fields are transactionally maintained query copies tied to the arm revision (Section 38.16). |
| Materialize only when measured vs mandatory Activity table | Cross-surface stable ordering, live amendments, and actor/branch correction are the correctness contract that justifies mandatory materialization (Section 38.21). |
| Head moves only after evidence vs delayed/ancestor-parented evidence | A provisional view can exist without moving the durable head; registered branch-reselection accepts late ancestor evidence and then corrects head/projections (Section 38.13). |

### 38.33 Fully specified phased capabilities

These capabilities may be implemented in later phases, but their contracts are
fixed now.

#### Provider/API relay tap

`RelayTap` is an ExecutionTarget capability, disabled by default. Configuration
stores upstream origin, provider ID, credential reference, TLS policy, capture
classes, and revision. The relay is a supervised sidecar, not the daemon's HTTP
listener. It exposes one loopback-only proxy endpoint to the provider process,
opens the upstream connection itself, and forwards request/response bytes
without waiting for semantic decoding or daemon acknowledgement.

The sidecar sends framed copies of supported semantic deltas to the daemon
ingestion socket with request ID, direction, provider item ID, source ordinal,
media type, and redaction class. Credentials/authorization headers are removed
before capture. Backpressure or daemon outage drops the tap copy and records a
gap when possible; upstream forwarding continues. If the sidecar cannot bind or
reach upstream during preflight, launch fails before the provider starts. If
the capture path fails mid-request, provider traffic continues and the Stream
degrades to snapshot/final reconciliation.

```text
RelayTap.preflight(config) -> RelayPreflight
RelayTap.start(attempt, config_revision) -> RelayHandle
RelayTap.stop(handle) -> RelayStopResult
RelayTap.health(handle) -> RelayHealth
```

No generic relay may modify provider payloads. A provider plugin separately
declares which framed records become provisional Stream operations and their
final authority. Contract tests cover byte-identical forwarding, chunking,
TLS/auth failure, daemon loss, redaction, bounded memory (4 MiB per connection),
and final reconciliation.

#### Online remote backend protocol

V4 remote execution is connected-only. The controller remains the sole owner
of the one SQLite database. A remote agent has no canonical database, spool,
offline queue, or replay log.

Controller and agent use mutual-TLS WebSocket with pinned controller/agent
certificates. Frames are length-bounded JSON control envelopes plus binary file
chunks. Registered messages are `hello`, `capabilities`, `heartbeat`,
`effect_request`, `effect_receipt`, `observation`, `probe_request`,
`probe_result`, `file_manifest`, `file_chunk`, `file_ack`, and `close`.
Every frame has protocol version, backend ID, connection ID, monotonic
connection sequence, request ID, and 8 MiB maximum payload. Binary transfers
use 1 MiB chunks with SHA-256 per chunk and whole-manifest digest.

Remote effects require a live connection and durable controller outbox attempt.
A disconnect before agent acceptance is `failed_before_action`; after possible
acceptance it is `indeterminate` and reconciled on a new connection from remote
live state only. Observations not delivered before disconnect may be lost; the
controller records a freshness gap and never asks the agent to replay. File
transfer resumes only from chunks the controller durably acknowledged; this is
transfer resumption, not Observation replay.

The remote agent exposes only allowlisted process launch/control, workspace
read/write, file transfer, and liveness capabilities rooted in configured
realpath jails. It cannot call controller HTTP as a human principal, read
credentials not explicitly brokered for one launch, or approve permissions.
Certificate rotation is an owner operation with old/new overlap and revocation.

#### Untrusted out-of-process plugins

Bundled signed plugins on the installation allowlist may run in-process.
Every third-party/untrusted plugin runs as a subprocess using JSON-RPC 2.0 over
stdio with `Content-Length` framing, a 1 MiB message limit, protocol handshake,
manifest digest, and per-call deadline. Supported calls are
`initialize`, `capabilities`, `decode_observation`, `map_proposal`,
`render_contribution`, `runtime_options`, `health`, and `shutdown`.

An untrusted plugin returns validated proposals; it never receives a database
connection, storage path, credential, terminal object, outbox writer, or raw
human authorization token. The host brokers only manifest-granted filesystem
Resource reads, outbound origins, provider binaries, and namespaced settings.
macOS packaging requires a Seatbelt profile; Linux requires bubblewrap with no
ambient filesystem/network. If the platform sandbox is unavailable, enabling
an untrusted plugin fails closed.

```text
plugin_installations
  plugin_id, version, manifest_digest, executable_digest, trust_class,
  granted_permissions, state, protocol_version, revision, last_health
```

The supervisor permits one process per plugin version, 256 MiB memory, one CPU,
32 child descriptors, no child processes unless granted, a five-second init
deadline, and per-call deadlines from the capability manifest. Three crashes in
five minutes trip `crash_loop`; affected provider capabilities become
unavailable while stored namespaced facts remain readable. Core migrations are
forbidden; indexed plugin data uses a versioned namespaced table owned by the
extension module. Disable sends shutdown, kills after two seconds, revokes
brokers, and retains audit/history.

#### Exact internal ports for phased workflows

```text
HandoverCompiler.compile(snapshot, target_caps, budget, redactions)
  -> HandoverPackage | PackageTooLarge | MissingRequiredResource
HandoverTransport.deliver(target_attempt, package_manifest)
  -> AcceptedReceipt | Rejected | Indeterminate
CollaborationDelivery.send(peer_message, recipient_delivery)
  -> AcceptedReceipt | Rejected | Indeterminate
ExtensionHost.start/stop/call/health(plugin_installation)
BackupPort.create/verify/restore(manifest, maintenance_lease)
RepairRegistry.plan/apply/verify(registered_code, typed_args, evidence)
```

All are invoked through Operations, outbox attempts, receipts, and the saga
runner. Handover never activates before target acknowledgement; peer delivery
never grants permission; restore never runs outside maintenance; repairs never
accept arbitrary SQL; and an indeterminate non-idempotent step always reconciles
before retry.

### 38.34 Exact physical storage, retention, and SQLite concurrency

#### Retention classes

| Class | Minimum/default retention and deletion rule |
|---|---|
| Canonical Conversation/Node/Operation identity | Until explicit owner purge; archive never deletes it |
| Identity, alias, adoption, repair, security/control evidence | 365 days after associated canonical purge |
| Raw provider Observations and ordinary provenance | 30 days after processing |
| Command/tool payload evidence and raw output | 30 days after Stream seal |
| Assistant/user semantic content and visible WYSIWYG copy | Until Conversation purge |
| Surface control telemetry | 7 days after receipt |
| Usage source rollups, authority, vendor cost | Until account/Conversation purge plus 365 days |
| Notification intents/routes/deliveries | 180 days after terminal state |
| Structural changes | Section 38.22's 24-hour and newest-100,000-per-scope joint floor |
| Upload Resource | While referenced, then 30 days after last reference |
| Open Stream staging | While open; after a Stream becomes lost, 7 days for repair |
| Orphan Blob/temp/quarantine | 24-hour grace after last reachability check |
| Verified backups | Newest 10 and every backup younger than 30 days; explicit pins never expire |

Retention settings may lengthen these values. Shortening a minimum requires a
schema/config migration that reports affected bytes/records and explicit owner
confirmation. Purge is a named local-owner Operation, takes a verified backup,
emits a manifest of removed IDs/digests, and never follows arbitrary paths.

#### Paths, permissions, and Blob write protocol

The data root is `~/Library/Application Support/baqylau` on macOS and
`${XDG_DATA_HOME:-~/.local/share}/baqylau` on Linux. The entire root and every
directory are mode `0700`; files are `0600`; startup refuses a symlink in any
root component or a root owned by another user.

```text
<data-root>/metadata.sqlite3
<data-root>/blobs/sha256/<first-two-hex>/<64-hex-digest>
<data-root>/blob-tmp/<uuid>.partial
<data-root>/blob-quarantine/<digest>.<gc-lease-id>
<data-root>/runtime/streams/<stream-uuid>.open
<data-root>/uploads/<resource-uuid>/<sanitized-basename>
<data-root>/backups/<backup-uuid>/metadata.sqlite3
<data-root>/backups/<backup-uuid>/manifest.json
```

Blob identity is SHA-256 of the exact uncompressed bytes. V4 stores Blob bytes
uncompressed (`compression=none`); HTTP compression is transport-only. Write:
exclusive-create temp; stream while hashing and enforcing class size; `fsync`
temp; if destination exists verify length and digest; otherwise atomic rename;
`fsync` destination directory; then insert Blob metadata/reference in SQLite.
Crash before metadata leaves a 24-hour orphan. A digest collision or mismatched
existing length is fatal storage corruption and disables mutation.

GC first claims a durable Blob lease, rechecks zero reachability in a write
transaction, atomically renames to quarantine, commits `quarantined`, then
unlinks and fsyncs the directory. A new reference can attach only to
`available`; it cancels a claimed-but-not-renamed lease. Startup restores a
quarantined file whose metadata gained reachability or finishes deletion when
it did not.

Uploads use exclusive creation and a basename stripped of separators/control
characters; the provider receives only the jailed absolute path resolved from
Resource ID. Maximum is 100 MiB per file and 1 GiB total unexpired uploads per
principal. Executable bit is always cleared. Media sniffing never executes or
renders active content inline.

#### Framed Stream format and crash order

Every frame is big-endian:

```text
magic[4]="BQSF" | version:u8=1 | op:u8 | header_len:u16=104
stream_uuid[16] | revision:u64 | source_start:u64 | source_end:u64
replace_start:u64 | replace_end:u64 | payload_len:u32
payload_sha256[32] | header_crc32:u32 | payload[payload_len]
```

Ops are `1=append`, `2=replace`, `3=reset`, `4=seal`, `5=abort`,
`6=transfer`. Payload is at most 256 KiB; larger input becomes consecutive
frames. `header_crc32` covers the header through `payload_sha256` with the CRC
field treated as zero. One Stream lease permits one writer. Reader accepts only
complete headers/payloads with correct CRC/SHA and strictly increasing revision;
it truncates only a torn final frame, never skips an interior corrupt frame.

Before SQLite publishes revision N, frame N is written and `fdatasync`ed. Thus
DB-ahead is forbidden. File-ahead is legal: recovery validates later complete
frames and either advances metadata through the normal reducer or quarantines
them if source ownership no longer matches. Seal writes/fsyncs the seal frame,
creates/fsyncs the Blob, commits Stream `sealed` plus final Blob reference, then
renames the `.open` file to quarantine; deletion occurs after 24 hours. Missing
file with DB `open` becomes `lost`; missing file with a verified sealed Blob is
healthy.

#### SQLite connection and writer policy

- SQLite uses the packaged version, 4 KiB pages, WAL, foreign keys on,
  `synchronous=NORMAL`, `journal_size_limit=268435456`, and
  `wal_autocheckpoint=0`.
- One adapter-owned write connection is protected by one FIFO async lock. This
  is ordinary adapter serialization, not a prioritized transaction gateway.
  Queue wait and SQLite lock wait are measured separately.
- Writes use `BEGIN IMMEDIATE`; no filesystem/network/process I/O occurs before
  commit/rollback. The connection is never handed to application code.
- Read connections are query-only pooled connections. A request read snapshot
  lasts at most two seconds; longer exports/page walks reopen at a stable
  application cursor.
- `busy_timeout` is zero so hidden SQLite sleeping cannot consume provider
  deadlines. Unexpected external contention retries after 1/2/5/10 ms, capped
  at 50 ms or the caller's earlier deadline, then returns `StorageBusy`.
- The maintenance worker owns checkpoints under Section 38.26. Online backup
  uses a separate read connection and page-stepped backup. Schema migration
  takes the daemon maintenance lease and is the only `BEGIN EXCLUSIVE` user.
- Every connection runs `foreign_key_check` after migration and
  `quick_check` at startup; failure disables mutations and exposes repair-only
  health.

### 38.35 Authoritative clean-install schema, migrations, and deletion law

This section closes the schema-assembly part of Section 0.3.1. It is normative
over the inventory fragment in Section 28. A clean install is one SQLite schema,
in one machine-wide `metadata.sqlite3`; it is never a catalog for
per-Conversation databases. The adapter executes these units in this exact
order:

1. apply the **foundation DDL** below with foreign keys enabled;
2. apply the complete Section 38.27 DDL block, unchanged and in its printed
   order;
3. apply the **post-extension integrity DDL** below;
4. apply the Section 38.39 **future workflow DDL**, which installs the remaining
   workflow tables and replaces the first intermediate schema digest;
5. apply the Section 40.7 **second-review DDL**, which installs the remaining
   concrete legacy-feature contracts and replaces the second intermediate
   schema digest;
6. run `PRAGMA foreign_key_check`, set/verify `PRAGMA user_version = 1`, and
   commit; and
7. on a new read connection, require `PRAGMA quick_check` to return exactly
   `ok` before mutations are enabled.

The five DDL units plus the migration row are the authoritative schema version
1 clean install. The core Conversation/track/Node/Operation tables are one
declared strongly connected component: their foreign keys are
`DEFERRABLE INITIALLY DEFERRED`, so their rows can be created in dependency
order inside one transaction even though their table declarations necessarily
contain forward references. Acyclic tables follow their parents; only the
declared Resource/current-version pair and Foundation evidence references to
Section 38.27 Observations cross that order, using deferred FKs. The application
never disables foreign keys to escape this ordering.

#### Foundation DDL

```sql
-- 38.35 FOUNDATION BEGIN
PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY CHECK(version > 0),
  migration_id TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND
    sha256 NOT GLOB '*[^0-9a-f]*'),
  installed_at REAL NOT NULL,
  min_reader_version INTEGER NOT NULL CHECK(min_reader_version > 0),
  min_writer_version INTEGER NOT NULL CHECK(min_writer_version > 0),
  rollback_class TEXT NOT NULL CHECK(rollback_class IN
    ('transactional','restore_required','irreversible')),
  rollback_deadline_version INTEGER,
  CHECK(rollback_deadline_version IS NULL OR
        rollback_deadline_version >= version)
);

CREATE TABLE schema_metadata (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  current_version INTEGER NOT NULL CHECK(current_version > 0),
  oldest_compatible_reader INTEGER NOT NULL CHECK(oldest_compatible_reader > 0),
  oldest_compatible_writer INTEGER NOT NULL CHECK(oldest_compatible_writer > 0),
  clean_install_sha256 TEXT NOT NULL CHECK(length(clean_install_sha256) = 64 AND
    clean_install_sha256 NOT GLOB '*[^0-9a-f]*'),
  updated_at REAL NOT NULL,
  FOREIGN KEY(current_version) REFERENCES schema_migrations(version)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE maintenance_leases (
  lease_kind TEXT PRIMARY KEY CHECK(lease_kind IN
    ('schema_migration','restore','backup','blob_gc','retention')),
  lease_id TEXT NOT NULL UNIQUE,
  owner_instance_id TEXT NOT NULL,
  acquired_at REAL NOT NULL,
  expires_at REAL NOT NULL CHECK(expires_at > acquired_at)
);

CREATE TABLE backends (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('local','remote')),
  display_name TEXT NOT NULL,
  label TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  endpoint_config_ref TEXT,
  trust_class TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json)),
  state TEXT NOT NULL CHECK(state IN
    ('active','degraded','disabled','unavailable')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE execution_targets (
  id TEXT PRIMARY KEY,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  provider_id TEXT NOT NULL,
  label TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN
    ('interactive','headless','sdk','server','remote')),
  default_mode TEXT NOT NULL CHECK(default_mode IN
    ('interactive','headless','sdk','server','remote')),
  workspace_root_ref TEXT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json)),
  state TEXT NOT NULL CHECK(state IN
    ('available','degraded','disabled','unavailable')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX execution_targets_backend
  ON execution_targets(backend_id, provider_id, state, id);

CREATE TABLE provider_credential_references (
  reference TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  method TEXT NOT NULL CHECK(method IN
    ('keychain','provider_store','environment','interactive')),
  state TEXT NOT NULL CHECK(state IN
    ('present','missing','unreadable','needs_review','revoked')),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX provider_credential_reference_provider
  ON provider_credential_references(provider_id,state,reference);

CREATE TABLE accounts (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  execution_target_id TEXT NOT NULL REFERENCES execution_targets(id)
    ON DELETE RESTRICT,
  credential_ref TEXT REFERENCES provider_credential_references(reference)
    ON DELETE RESTRICT,
  label TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  priority INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL CHECK(state IN
    ('available','limited','logged_out','disabled','unknown')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX accounts_provider_state ON accounts(provider_id, state, id);
CREATE UNIQUE INDEX accounts_label_unique ON accounts(label);
CREATE INDEX accounts_target_enabled ON accounts(execution_target_id,enabled,priority,id);

CREATE TABLE backend_health (
  execution_target_id TEXT PRIMARY KEY
    REFERENCES execution_targets(id) ON DELETE CASCADE,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE CASCADE,
  reachability TEXT NOT NULL CHECK(reachability IN
    ('available','unavailable','unknown')),
  capabilities TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(capabilities)),
  detail TEXT,
  observed_at REAL NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE INDEX backend_health_backend
  ON backend_health(backend_id, observed_at DESC, execution_target_id);

CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  lead_actor_track_id TEXT NOT NULL,
  head_node_id TEXT,
  active_agent_session_id TEXT,
  title TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  project_ref TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  archived_at REAL,
  purge_state TEXT NOT NULL DEFAULT 'retained' CHECK(purge_state IN
    ('retained','purge_authorized')),
  UNIQUE(id, lead_actor_track_id),
  FOREIGN KEY(lead_actor_track_id)
    REFERENCES conversation_actor_tracks(id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(head_node_id, lead_actor_track_id)
    REFERENCES nodes(id, actor_track_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(active_agent_session_id, id)
    REFERENCES agent_sessions(id, conversation_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  CHECK(archived_at IS NULL OR archived_at >= created_at)
);

CREATE TABLE agent_sessions (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED,
  provider_id TEXT NOT NULL,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  execution_target_id TEXT REFERENCES execution_targets(id) ON DELETE RESTRICT,
  mode TEXT NOT NULL CHECK(mode IN
    ('interactive','headless','sdk','server','remote')),
  state TEXT NOT NULL CHECK(state IN
    ('starting','active','idle','ended','lost','archived')),
  resumable INTEGER NOT NULL CHECK(resumable IN (0,1)),
  persistence_kind TEXT NOT NULL CHECK(persistence_kind IN
    ('native_local','native_remote','baqylau_captured','ephemeral')),
  source_ref TEXT,
  started_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  ended_at REAL,
  end_reason TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(id, conversation_id),
  CHECK(last_seen_at >= started_at),
  CHECK(ended_at IS NULL OR ended_at >= started_at),
  CHECK((state IN ('ended','lost','archived')) OR ended_at IS NULL)
);
CREATE INDEX agent_sessions_conversation_state
  ON agent_sessions(conversation_id, state, started_at, id);

CREATE TABLE agent_session_lineage (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  predecessor_agent_session_id TEXT
    REFERENCES agent_sessions(id) ON DELETE RESTRICT,
  source_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  relation TEXT NOT NULL CHECK(relation IN
    ('fork','resume','migration','handover')),
  created_at REAL NOT NULL,
  CHECK(predecessor_agent_session_id IS NULL OR
        predecessor_agent_session_id <> agent_session_id)
);
CREATE INDEX agent_session_lineage_predecessor
  ON agent_session_lineage(predecessor_agent_session_id,created_at,agent_session_id);

CREATE TABLE agent_session_attempts (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  execution_target_id TEXT REFERENCES execution_targets(id) ON DELETE RESTRICT,
  account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
  mode TEXT NOT NULL,
  pid INTEGER CHECK(pid > 0),
  host_instance_id TEXT,
  runtime_handle_ref TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('preparing','running','ended','failed','lost','unknown')),
  started_at REAL NOT NULL,
  ended_at REAL,
  exit_status INTEGER,
  observation_quality TEXT NOT NULL CHECK(observation_quality IN
    ('authoritative','partial','inferred','unknown')),
  UNIQUE(id, agent_session_id),
  CHECK(ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX attempts_session_time
  ON agent_session_attempts(agent_session_id, started_at DESC, id DESC);
CREATE UNIQUE INDEX one_running_attempt_per_session
  ON agent_session_attempts(agent_session_id)
  WHERE state IN ('preparing','running');

CREATE TABLE agent_session_aliases (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  provider_id TEXT NOT NULL,
  identity_kind TEXT NOT NULL,
  external_id TEXT NOT NULL,
  confidence TEXT NOT NULL CHECK(confidence IN
    ('authoritative','strong','weak','unknown')),
  valid_from REAL NOT NULL,
  valid_until REAL,
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  provenance_id TEXT,
  UNIQUE(backend_id, provider_id, identity_kind, external_id, valid_from),
  CHECK(valid_until IS NULL OR valid_until >= valid_from),
  CHECK((active = 1) = (valid_until IS NULL))
);
CREATE UNIQUE INDEX active_session_alias
  ON agent_session_aliases(backend_id, provider_id, identity_kind, external_id)
  WHERE active = 1;
CREATE UNIQUE INDEX one_active_provider_native_alias_per_session
  ON agent_session_aliases(agent_session_id)
  WHERE active = 1 AND identity_kind = 'native_session_id';
CREATE INDEX aliases_reverse
  ON agent_session_aliases(agent_session_id, active, identity_kind, id);

CREATE TABLE conversation_actor_tracks (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED,
  actor_key TEXT NOT NULL,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  parent_track_id TEXT,
  lifecycle_operation_id TEXT,
  track_kind TEXT NOT NULL CHECK(track_kind IN
    ('lead','subagent','teammate','sidecar','peer')),
  state TEXT NOT NULL CHECK(state IN ('active','idle','ended','lost')),
  head_node_id TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  ended_at REAL,
  UNIQUE(id, conversation_id),
  UNIQUE(id, actor_key),
  UNIQUE(conversation_id, actor_key),
  FOREIGN KEY(parent_track_id, conversation_id)
    REFERENCES conversation_actor_tracks(id, conversation_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(lifecycle_operation_id, conversation_id)
    REFERENCES operations(id, conversation_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(head_node_id, id)
    REFERENCES nodes(id, actor_track_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  CHECK((track_kind = 'lead') = (actor_key = 'baqylau:lead')),
  CHECK((state IN ('ended','lost')) = (ended_at IS NOT NULL))
);
CREATE UNIQUE INDEX one_lead_track_per_conversation
  ON conversation_actor_tracks(conversation_id) WHERE track_kind = 'lead';
CREATE INDEX actor_track_list
  ON conversation_actor_tracks(conversation_id, state, created_at, id);

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  actor_track_id TEXT NOT NULL,
  parent_id TEXT,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system','summary')),
  semantic_kind TEXT NOT NULL CHECK(semantic_kind IN
    ('prompt','message','summary','recap','system')),
  origin TEXT NOT NULL CHECK(origin IN
    ('human','provider','baqylau','peer','imported')),
  state TEXT NOT NULL CHECK(state IN ('streaming','committed','aborted')),
  source_external_id TEXT,
  source_position TEXT,
  turn_key TEXT,
  actor_key TEXT,
  branch_visibility TEXT NOT NULL DEFAULT 'normal' CHECK(branch_visibility IN
    ('normal','suspect_retracted','superseded')),
  branch_evidence_revision INTEGER NOT NULL DEFAULT 0
    CHECK(branch_evidence_revision >= 0),
  completion_reason TEXT CHECK(completion_reason IS NULL OR completion_reason IN
    ('complete','interrupted','failed','unknown')),
  source_timestamp REAL,
  created_at REAL NOT NULL,
  committed_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(id, conversation_id),
  UNIQUE(id, actor_track_id),
  FOREIGN KEY(actor_track_id, conversation_id)
    REFERENCES conversation_actor_tracks(id, conversation_id) ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(parent_id, actor_track_id)
    REFERENCES nodes(id, actor_track_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  CHECK((state = 'committed') = (committed_at IS NOT NULL))
);
CREATE INDEX nodes_track_parent ON nodes(actor_track_id, parent_id, id);
CREATE INDEX nodes_conversation_source
  ON nodes(conversation_id, source_external_id, source_position, id);

CREATE TABLE blob_objects (
  digest TEXT PRIMARY KEY CHECK(length(digest) = 64 AND
    digest NOT GLOB '*[^0-9a-f]*'),
  byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
  media_class TEXT NOT NULL,
  compression TEXT NOT NULL DEFAULT 'none' CHECK(compression = 'none'),
  retention_class TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('available','gc_claimed','quarantined','deleted','corrupt')),
  created_at REAL NOT NULL,
  expires_at REAL,
  last_reachability_check_at REAL,
  quarantine_key TEXT,
  CHECK(expires_at IS NULL OR expires_at >= created_at),
  CHECK((state = 'quarantined') = (quarantine_key IS NOT NULL))
);
CREATE INDEX blob_retention_scan
  ON blob_objects(state, expires_at, last_reachability_check_at, digest);

CREATE TABLE blob_gc_leases (
  digest TEXT PRIMARY KEY REFERENCES blob_objects(digest) ON DELETE CASCADE,
  lease_id TEXT NOT NULL UNIQUE,
  owner_instance_id TEXT NOT NULL,
  claimed_at REAL NOT NULL,
  expires_at REAL NOT NULL CHECK(expires_at > claimed_at)
);

CREATE TABLE blob_references (
  owner_table TEXT NOT NULL,
  owner_key TEXT NOT NULL,
  role TEXT NOT NULL,
  digest TEXT NOT NULL REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  retention_class TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(owner_table, owner_key, role)
);
CREATE INDEX blob_reachability ON blob_references(digest, owner_table, owner_key);

CREATE TABLE operations (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  subject_type TEXT CHECK(subject_type IN
    ('machine','backend','execution_target','account','provider_edge','push_key')),
  subject_id TEXT,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  actor_track_id TEXT,
  anchor_node_id TEXT,
  parent_operation_id TEXT,
  turn_key TEXT,
  task_key TEXT,
  actor_key TEXT,
  source_position TEXT,
  native_operation_key TEXT,
  kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('pending','running','succeeded','failed','cancelled','denied','abandoned',
     'lost','unknown')),
  opener_state TEXT NOT NULL DEFAULT 'present'
    CHECK(opener_state IN ('present','missing','unknown')),
  origin TEXT NOT NULL CHECK(origin IN
    ('observed','requested','inferred','imported')),
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  data TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(data)),
  result_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  source_timestamp REAL,
  started_at REAL NOT NULL,
  ended_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(id, conversation_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(actor_track_id, conversation_id)
    REFERENCES conversation_actor_tracks(id, conversation_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(anchor_node_id, conversation_id)
    REFERENCES nodes(id, conversation_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY(parent_operation_id, conversation_id)
    REFERENCES operations(id, conversation_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  CHECK(
    (conversation_id IS NOT NULL AND subject_type IS NULL AND subject_id IS NULL)
    OR
    (conversation_id IS NULL AND subject_type IS NOT NULL AND subject_id IS NOT NULL)
  ),
  CHECK(ended_at IS NULL OR ended_at >= started_at),
  CHECK((state IN ('pending','running')) OR ended_at IS NOT NULL)
);
CREATE INDEX operations_conversation_state
  ON operations(conversation_id, state, kind, started_at, id);
CREATE INDEX operations_session_source
  ON operations(agent_session_id, source_position, id);
CREATE INDEX operations_correlation
  ON operations(conversation_id, turn_key, task_key, actor_key, id);
CREATE UNIQUE INDEX operations_native_key
  ON operations(agent_session_id, kind, native_operation_key)
  WHERE native_operation_key IS NOT NULL;

CREATE TABLE streams (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  owner_type TEXT NOT NULL CHECK(owner_type IN ('node','operation')),
  owner_node_id TEXT,
  owner_operation_id TEXT,
  owner_id TEXT GENERATED ALWAYS AS
    (coalesce(owner_node_id, owner_operation_id)) STORED,
  channel TEXT NOT NULL,
  ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
  kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('open','sealed','aborted','lost')),
  mode TEXT NOT NULL CHECK(mode IN ('ordered_delta','snapshot_revision')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  byte_length INTEGER NOT NULL DEFAULT 0 CHECK(byte_length >= 0),
  staging_key TEXT,
  final_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  retention_class TEXT NOT NULL,
  media_type TEXT,
  render_kind TEXT NOT NULL DEFAULT 'plain' CHECK(render_kind IN
    ('plain','markdown','json','yaml','source','extension')),
  language TEXT,
  render_detection_source TEXT CHECK(render_detection_source IS NULL OR
    render_detection_source IN
      ('raw_command','provider_metadata','explicit','fallback')),
  visible_copy_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  raw_copy_state TEXT NOT NULL DEFAULT 'never_captured' CHECK(raw_copy_state IN
    ('available','expired','never_captured')),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  sealed_at REAL,
  UNIQUE(owner_type, owner_id, channel, ordinal),
  FOREIGN KEY(owner_node_id, conversation_id)
    REFERENCES nodes(id, conversation_id) ON DELETE CASCADE,
  FOREIGN KEY(owner_operation_id, conversation_id)
    REFERENCES operations(id, conversation_id) ON DELETE CASCADE,
  CHECK((owner_type = 'node' AND owner_node_id IS NOT NULL AND
         owner_operation_id IS NULL) OR
        (owner_type = 'operation' AND owner_operation_id IS NOT NULL AND
         owner_node_id IS NULL)),
  CHECK((state = 'open' AND staging_key = id || '.open' AND
         final_blob_digest IS NULL AND sealed_at IS NULL) OR
        (state = 'sealed' AND staging_key IS NULL AND
         final_blob_digest IS NOT NULL AND sealed_at IS NOT NULL) OR
        state IN ('aborted','lost'))
);
CREATE INDEX streams_owner_state
  ON streams(owner_type, owner_id, state, channel, ordinal);
CREATE INDEX streams_open ON streams(state, updated_at, id);

CREATE TABLE stream_writer_leases (
  stream_id TEXT PRIMARY KEY REFERENCES streams(id) ON DELETE CASCADE,
  lease_id TEXT NOT NULL UNIQUE,
  owner_instance_id TEXT NOT NULL,
  acquired_at REAL NOT NULL,
  expires_at REAL NOT NULL CHECK(expires_at > acquired_at),
  last_fsynced_revision INTEGER NOT NULL CHECK(last_fsynced_revision >= 0)
);

CREATE TABLE conversation_workspaces (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  workspace_ref TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN
    ('primary','source','handover_target','archived')),
  revision_ref TEXT,
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  provenance_id TEXT,
  observed_at REAL NOT NULL,
  PRIMARY KEY(conversation_id, backend_id, workspace_ref)
);
CREATE UNIQUE INDEX one_primary_workspace
  ON conversation_workspaces(conversation_id)
  WHERE role = 'primary' AND active = 1;

CREATE TABLE native_records (
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT NOT NULL,
  external_id TEXT,
  parent_external_id TEXT,
  record_kind TEXT NOT NULL,
  source_timestamp REAL,
  payload_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  provenance_id TEXT,
  PRIMARY KEY(agent_session_id, source_epoch, source_ordinal),
  UNIQUE(agent_session_id, source_epoch, source_position)
);
CREATE INDEX native_records_external
  ON native_records(agent_session_id, external_id, source_epoch, source_ordinal);

CREATE TABLE context_checkpoints (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  at_node_id TEXT REFERENCES nodes(id) ON DELETE RESTRICT,
  source_position TEXT,
  summary_node_id TEXT REFERENCES nodes(id) ON DELETE RESTRICT,
  summary_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  covers_from_node_id TEXT REFERENCES nodes(id) ON DELETE RESTRICT,
  covers_through_node_id TEXT REFERENCES nodes(id) ON DELETE RESTRICT,
  context_window_tokens INTEGER CHECK(context_window_tokens >= 0),
  context_used_tokens INTEGER CHECK(context_used_tokens >= 0),
  state TEXT NOT NULL CHECK(state IN
    ('observed','inferred','superseded','unknown')),
  provenance_id TEXT,
  created_at REAL NOT NULL,
  CHECK(context_window_tokens IS NULL OR context_used_tokens IS NULL OR
        context_used_tokens <= context_window_tokens)
);
CREATE INDEX context_checkpoint_latest
  ON context_checkpoints(agent_session_id, created_at DESC, id DESC);

CREATE TABLE resources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN
    ('file','image','diff','plan','log','memory_note','search_result','extension')),
  owner_principal_id TEXT REFERENCES principals(id) ON DELETE RESTRICT,
  execution_target_id TEXT REFERENCES execution_targets(id) ON DELETE RESTRICT,
  workspace_ref TEXT,
  canonical_uri TEXT,
  media_type TEXT,
  current_version_id TEXT,
  retention_class TEXT NOT NULL,
  availability TEXT NOT NULL CHECK(availability IN
    ('available','expired','unavailable','unknown')),
  created_at REAL NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(id, current_version_id),
  FOREIGN KEY(current_version_id, id)
    REFERENCES resource_versions(id, resource_id) ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED,
  CHECK((execution_target_id IS NULL) = (workspace_ref IS NULL))
);

CREATE TABLE resource_versions (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  storage_kind TEXT NOT NULL CHECK(storage_kind IN
    ('blob','upload','provider_external','unavailable')),
  blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  upload_basename TEXT,
  provider_ref TEXT,
  digest TEXT,
  byte_length INTEGER CHECK(byte_length >= 0),
  source_operation_id TEXT REFERENCES operations(id) ON DELETE SET NULL,
  provenance_id TEXT,
  created_at REAL NOT NULL,
  expires_at REAL,
  UNIQUE(id, resource_id),
  CHECK((storage_kind = 'blob' AND blob_digest IS NOT NULL AND
         upload_basename IS NULL AND provider_ref IS NULL) OR
        (storage_kind = 'upload' AND upload_basename IS NOT NULL AND
         upload_basename NOT GLOB '*[/\\]*' AND blob_digest IS NULL AND
         provider_ref IS NULL) OR
        (storage_kind = 'provider_external' AND provider_ref IS NOT NULL AND
         blob_digest IS NULL AND upload_basename IS NULL) OR
        (storage_kind = 'unavailable' AND blob_digest IS NULL AND
         upload_basename IS NULL AND provider_ref IS NULL)),
  CHECK(expires_at IS NULL OR expires_at >= created_at)
);
CREATE INDEX resource_lookup
  ON resources(execution_target_id, workspace_ref, canonical_uri, kind, id);
CREATE INDEX resource_versions_resource
  ON resource_versions(resource_id, created_at DESC, id DESC);

CREATE TABLE resource_path_grants (
  token_sha256 TEXT PRIMARY KEY CHECK(length(token_sha256) = 64),
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  resource_version_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK(purpose IN ('upload','clipboard')),
  state TEXT NOT NULL CHECK(state IN ('active','consumed','expired','revoked')),
  use_count INTEGER NOT NULL DEFAULT 0 CHECK(use_count >= 0),
  max_uses INTEGER NOT NULL DEFAULT 1 CHECK(max_uses BETWEEN 1 AND 100),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  consumed_at REAL,
  FOREIGN KEY(resource_version_id, resource_id)
    REFERENCES resource_versions(id, resource_id) ON DELETE CASCADE,
  CHECK(expires_at >= created_at),
  CHECK(consumed_at IS NULL OR consumed_at >= created_at)
);
CREATE INDEX resource_path_grant_expiry
  ON resource_path_grants(state, expires_at, token_sha256);

CREATE TABLE node_parts (
  node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  kind TEXT NOT NULL CHECK(kind IN
    ('text','image','file','artifact','structured')),
  media_type TEXT,
  storage_kind TEXT NOT NULL CHECK(storage_kind IN ('blob','stream','resource')),
  content_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  stream_id TEXT REFERENCES streams(id) ON DELETE RESTRICT,
  resource_version_id TEXT REFERENCES resource_versions(id) ON DELETE RESTRICT,
  metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
  PRIMARY KEY(node_id, ordinal),
  CHECK((storage_kind = 'blob' AND content_blob_digest IS NOT NULL AND
         stream_id IS NULL AND resource_version_id IS NULL) OR
        (storage_kind = 'stream' AND stream_id IS NOT NULL AND
         content_blob_digest IS NULL AND resource_version_id IS NULL) OR
        (storage_kind = 'resource' AND resource_version_id IS NOT NULL AND
         content_blob_digest IS NULL AND stream_id IS NULL))
);

CREATE TABLE activity_links (
  from_type TEXT NOT NULL CHECK(from_type IN
    ('node','operation','agent_session','resource')),
  from_id TEXT NOT NULL,
  to_type TEXT NOT NULL CHECK(to_type IN
    ('node','operation','agent_session','resource')),
  to_id TEXT NOT NULL,
  relation TEXT NOT NULL CHECK(relation IN
    ('result_of','contributes_to','caused_by','supersedes','summarizes',
     'delivered_as','produced','consumed')),
  provenance_id TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY(from_type, from_id, to_type, to_id, relation)
);
CREATE INDEX activity_links_reverse
  ON activity_links(to_type, to_id, relation, from_type, from_id);

CREATE TABLE provenance_records (
  id TEXT PRIMARY KEY,
  observation_id TEXT REFERENCES observations(id) ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED,
  rule_code TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  confidence TEXT NOT NULL CHECK(confidence IN
    ('authoritative','strong','weak','unknown')),
  evidence_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  created_at REAL NOT NULL
);
CREATE INDEX provenance_observation
  ON provenance_records(observation_id, created_at, id);

CREATE TABLE provenance_links (
  provenance_id TEXT NOT NULL REFERENCES provenance_records(id) ON DELETE CASCADE,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  PRIMARY KEY(provenance_id, entity_type, entity_id, relation)
);
CREATE INDEX provenance_entity
  ON provenance_links(entity_type, entity_id, provenance_id);

CREATE TABLE ingestion_decisions (
  id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED,
  consumer_kind TEXT NOT NULL,
  decision_code TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN
    ('applied','skipped','quarantined','ignored','unknown')),
  detail_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  created_at REAL NOT NULL,
  UNIQUE(observation_id, consumer_kind, decision_code)
);

CREATE TABLE input_buffers (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('composer','new_session','interaction')),
  conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
  interaction_operation_id TEXT REFERENCES operations(id) ON DELETE CASCADE,
  project_ref TEXT,
  text_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  author_id TEXT NOT NULL,
  author_sequence INTEGER NOT NULL CHECK(author_sequence >= 0),
  origin TEXT NOT NULL CHECK(origin IN ('surface','device','terminal')),
  tombstone INTEGER NOT NULL DEFAULT 0 CHECK(tombstone IN (0,1)),
  updated_at REAL NOT NULL,
  UNIQUE(author_id, author_sequence),
  CHECK(
    (kind = 'composer' AND conversation_id IS NOT NULL AND
      interaction_operation_id IS NULL AND project_ref IS NULL)
    OR
    (kind = 'new_session' AND conversation_id IS NULL AND
      interaction_operation_id IS NULL AND project_ref IS NOT NULL)
    OR
    (kind = 'interaction' AND conversation_id IS NULL AND
      interaction_operation_id IS NOT NULL AND project_ref IS NULL)
  ),
  CHECK((tombstone = 1 AND text_blob_digest IS NULL) OR tombstone = 0)
);
CREATE INDEX input_buffers_scope
  ON input_buffers(kind, conversation_id, interaction_operation_id,
                   project_ref, updated_at, id);

CREATE TABLE preferences (
  namespace TEXT NOT NULL,
  scope_type TEXT NOT NULL CHECK(scope_type IN
    ('principal','device','conversation','project','global')),
  scope_id TEXT NOT NULL,
  key TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  value_json TEXT CHECK(value_json IS NULL OR json_valid(value_json)),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  author_id TEXT NOT NULL,
  author_sequence INTEGER NOT NULL CHECK(author_sequence >= 0),
  tombstone INTEGER NOT NULL DEFAULT 0 CHECK(tombstone IN (0,1)),
  updated_at REAL NOT NULL,
  PRIMARY KEY(namespace, scope_type, scope_id, key),
  CHECK((tombstone = 1 AND value_json IS NULL) OR
        (tombstone = 0 AND value_json IS NOT NULL))
);
CREATE INDEX preferences_author_order
  ON preferences(author_id, author_sequence, namespace, scope_type, scope_id, key);

CREATE TABLE outbox (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  scope_type TEXT NOT NULL CHECK(scope_type IN
    ('machine','conversation','account','backend')),
  scope_id TEXT NOT NULL,
  effect_kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_blob_digest TEXT NOT NULL REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN
    ('pending','claimed','succeeded','failed','indeterminate','cancelled')),
  available_at REAL NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  lease_id TEXT,
  lease_expires_at REAL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  CHECK((state = 'claimed') = (lease_id IS NOT NULL)),
  CHECK((lease_id IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX outbox_fifo_claim
  ON outbox(state, available_at, sequence);

CREATE TABLE effect_attempts (
  id TEXT PRIMARY KEY,
  outbox_id TEXT NOT NULL REFERENCES outbox(id) ON DELETE CASCADE,
  attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
  state TEXT NOT NULL CHECK(state IN
    ('started','accepted','succeeded','failed_before_action','indeterminate',
     'reconciled_failed','reconciled_succeeded')),
  request_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  receipt_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  external_handle_ref TEXT,
  started_at REAL NOT NULL,
  finished_at REAL,
  UNIQUE(outbox_id, attempt_number),
  CHECK(finished_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX effect_attempts_state
  ON effect_attempts(state, started_at, id);

CREATE TABLE attention_transitions (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  cause_operation_id TEXT REFERENCES operations(id) ON DELETE SET NULL,
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  created_at REAL NOT NULL
);
CREATE INDEX attention_conversation_time
  ON attention_transitions(conversation_id, created_at, id);

CREATE TABLE repair_records (
  id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE RESTRICT,
  repair_code TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  before_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  after_blob_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  operator_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX repairs_entity ON repair_records(entity_type, entity_id, created_at, id);

CREATE TABLE purge_authorizations (
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
  purge_operation_id TEXT NOT NULL,
  backup_id TEXT NOT NULL,
  manifest_blob_digest TEXT NOT NULL REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  authorized_at REAL NOT NULL,
  expires_at REAL NOT NULL CHECK(expires_at > authorized_at),
  consumed_at REAL
);

CREATE TRIGGER conversation_delete_requires_purge_authorization
BEFORE DELETE ON conversations BEGIN
  SELECT CASE WHEN OLD.purge_state <> 'purge_authorized' OR NOT EXISTS (
    SELECT 1 FROM purge_authorizations p
    WHERE p.conversation_id = OLD.id
      AND p.consumed_at IS NOT NULL
      AND p.expires_at >= unixepoch('subsec'))
  THEN RAISE(ABORT, 'conversation_delete_requires_authorized_purge') END;
END;

CREATE TRIGGER conversation_lead_identity_insert
BEFORE INSERT ON conversation_actor_tracks WHEN NEW.track_kind = 'lead' BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversations c
    WHERE c.id = NEW.conversation_id AND c.lead_actor_track_id = NEW.id)
  THEN RAISE(ABORT, 'lead_track_must_match_conversation') END;
END;
CREATE TRIGGER conversation_lead_identity_update
BEFORE UPDATE OF conversation_id, track_kind, actor_key
ON conversation_actor_tracks BEGIN
  SELECT CASE WHEN NEW.track_kind = 'lead' AND NOT EXISTS (
    SELECT 1 FROM conversations c
    WHERE c.id = NEW.conversation_id AND c.lead_actor_track_id = NEW.id)
  THEN RAISE(ABORT, 'lead_track_must_match_conversation') END;
END;

CREATE TRIGGER actor_track_session_scope_insert
BEFORE INSERT ON conversation_actor_tracks WHEN NEW.agent_session_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'actor_track_session_scope_mismatch') END;
END;
CREATE TRIGGER actor_track_session_scope_update
BEFORE UPDATE OF agent_session_id, conversation_id ON conversation_actor_tracks
WHEN NEW.agent_session_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'actor_track_session_scope_mismatch') END;
END;

CREATE TRIGGER node_session_scope_insert
BEFORE INSERT ON nodes WHEN NEW.agent_session_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'node_session_scope_mismatch') END;
END;
CREATE TRIGGER node_session_scope_update
BEFORE UPDATE OF agent_session_id, conversation_id ON nodes
WHEN NEW.agent_session_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'node_session_scope_mismatch') END;
END;

CREATE TRIGGER operation_session_scope_insert
BEFORE INSERT ON operations WHEN NEW.agent_session_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_session_scope_mismatch') END;
END;
CREATE TRIGGER operation_session_scope_update
BEFORE UPDATE OF agent_session_id, conversation_id ON operations
WHEN NEW.agent_session_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.agent_session_id
      AND s.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'operation_session_scope_mismatch') END;
END;

CREATE TRIGGER conversation_head_requires_committed_node_insert
BEFORE INSERT ON conversations WHEN NEW.head_node_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.lead_actor_track_id
      AND n.state = 'committed')
  THEN RAISE(ABORT, 'conversation_head_must_be_committed_lead_node') END;
END;
CREATE TRIGGER conversation_head_requires_committed_node_update
BEFORE UPDATE OF head_node_id, lead_actor_track_id ON conversations
WHEN NEW.head_node_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM nodes n
    WHERE n.id = NEW.head_node_id
      AND n.actor_track_id = NEW.lead_actor_track_id
      AND n.state = 'committed')
  THEN RAISE(ABORT, 'conversation_head_must_be_committed_lead_node') END;
END;
CREATE TRIGGER conversation_head_equals_lead_track_update
BEFORE UPDATE OF head_node_id, lead_actor_track_id ON conversations
WHEN NEW.head_node_id IS NOT (
  SELECT t.head_node_id FROM conversation_actor_tracks t
  WHERE t.id = NEW.lead_actor_track_id
    AND t.conversation_id = NEW.id
    AND t.track_kind = 'lead')
BEGIN
  SELECT RAISE(ABORT, 'conversation_head_must_equal_lead_track');
END;
CREATE TRIGGER lead_track_projects_head_insert
AFTER INSERT ON conversation_actor_tracks
WHEN NEW.track_kind = 'lead' BEGIN
  UPDATE conversations
  SET head_node_id = NEW.head_node_id
  WHERE id = NEW.conversation_id;
END;
CREATE TRIGGER lead_track_projects_head_update
AFTER UPDATE OF head_node_id ON conversation_actor_tracks
WHEN NEW.track_kind = 'lead' BEGIN
  UPDATE conversations
  SET head_node_id = NEW.head_node_id,
      revision = revision + 1,
      updated_at = unixepoch('subsec')
  WHERE id = NEW.conversation_id;
END;
CREATE TRIGGER current_head_node_cannot_uncommit
BEFORE UPDATE OF state ON nodes WHEN NEW.state <> 'committed' BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM conversations c WHERE c.head_node_id = OLD.id
    UNION ALL
    SELECT 1 FROM conversation_actor_tracks t WHERE t.head_node_id = OLD.id)
  THEN RAISE(ABORT, 'current_head_node_cannot_uncommit') END;
END;

CREATE TRIGGER stream_blob_refs_insert
AFTER INSERT ON streams BEGIN
  INSERT INTO blob_references(owner_table, owner_key, role, digest,
                              retention_class, created_at)
    SELECT 'streams', NEW.id, 'final', NEW.final_blob_digest,
           NEW.retention_class, NEW.created_at
    WHERE NEW.final_blob_digest IS NOT NULL;
  INSERT INTO blob_references(owner_table, owner_key, role, digest,
                              retention_class, created_at)
    SELECT 'streams', NEW.id, 'visible_copy', NEW.visible_copy_blob_digest,
           NEW.retention_class, NEW.created_at
    WHERE NEW.visible_copy_blob_digest IS NOT NULL;
END;
CREATE TRIGGER stream_blob_refs_update
AFTER UPDATE OF final_blob_digest, visible_copy_blob_digest ON streams BEGIN
  DELETE FROM blob_references
    WHERE owner_table = 'streams' AND owner_key = NEW.id
      AND role IN ('final','visible_copy');
  INSERT INTO blob_references(owner_table, owner_key, role, digest,
                              retention_class, created_at)
    SELECT 'streams', NEW.id, 'final', NEW.final_blob_digest,
           NEW.retention_class, NEW.created_at
    WHERE NEW.final_blob_digest IS NOT NULL;
  INSERT INTO blob_references(owner_table, owner_key, role, digest,
                              retention_class, created_at)
    SELECT 'streams', NEW.id, 'visible_copy', NEW.visible_copy_blob_digest,
           NEW.retention_class, NEW.created_at
    WHERE NEW.visible_copy_blob_digest IS NOT NULL;
END;
CREATE TRIGGER stream_blob_refs_delete AFTER DELETE ON streams BEGIN
  DELETE FROM blob_references WHERE owner_table = 'streams' AND owner_key = OLD.id;
END;

-- 38.35 FOUNDATION END
```

The Resource/current-version and evidence/Observation pairs cross the
Foundation-to-Section-38.27 boundary and therefore use deferred forward
references. Clean-install verification must prepare every statement and inspect
`PRAGMA foreign_key_list` for every declared relationship.

#### Post-extension integrity DDL

Section 38.27 contains review-specific tables whose original standalone block
could not inline references to Foundation tables. The final assembly therefore
adds the following triggers after that block. A referenced Blob row remains
after bytes expire (`state=deleted`), so historical rows keep an honest
unavailable reference rather than dangling text.

```sql
-- 38.35 FINALIZATION BEGIN
CREATE TRIGGER observation_payload_exists_insert
BEFORE INSERT ON observations BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.payload_ref AND b.state = 'available')
  THEN RAISE(ABORT, 'observation_payload_blob_unavailable') END;
END;
CREATE TRIGGER observation_payload_exists_update
BEFORE UPDATE OF payload_ref ON observations BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.payload_ref AND b.state = 'available')
  THEN RAISE(ABORT, 'observation_payload_blob_unavailable') END;
END;
CREATE TRIGGER observation_blob_ref_insert AFTER INSERT ON observations BEGIN
  INSERT INTO blob_references(owner_table, owner_key, role, digest,
                              retention_class, created_at)
  VALUES('observations', NEW.id, 'payload', NEW.payload_ref,
         'observation_raw', NEW.ingested_at);
END;
CREATE TRIGGER observation_blob_ref_update AFTER UPDATE OF payload_ref ON observations BEGIN
  UPDATE blob_references SET digest = NEW.payload_ref
  WHERE owner_table = 'observations' AND owner_key = NEW.id AND role = 'payload';
END;
CREATE TRIGGER observation_blob_ref_delete AFTER DELETE ON observations BEGIN
  DELETE FROM blob_references
  WHERE owner_table = 'observations' AND owner_key = OLD.id;
END;

CREATE TRIGGER interaction_refs_exist_insert
BEFORE INSERT ON interaction_details BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.prompt_ref AND b.state = 'available')
  THEN RAISE(ABORT, 'interaction_prompt_blob_unavailable') END;
  SELECT CASE WHEN NEW.options_ref IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.options_ref AND b.state = 'available')
  THEN RAISE(ABORT, 'interaction_options_blob_unavailable') END;
  SELECT CASE WHEN NEW.response_ref IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM blob_objects b WHERE b.digest = NEW.response_ref)
  THEN RAISE(ABORT, 'interaction_response_blob_missing') END;
  SELECT CASE WHEN NEW.plan_ref IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM resources r WHERE r.id = NEW.plan_ref)
  THEN RAISE(ABORT, 'interaction_plan_resource_missing') END;
END;

CREATE TRIGGER peer_body_blob_exists_insert
BEFORE INSERT ON peer_messages WHEN NEW.body_ref IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.body_ref AND b.state = 'available')
  THEN RAISE(ABORT, 'peer_message_body_blob_unavailable') END;
END;

CREATE TRIGGER tui_draft_blob_exists_insert
BEFORE INSERT ON tui_drafts BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.text_ref AND b.state = 'available')
  THEN RAISE(ABORT, 'tui_draft_blob_unavailable') END;
END;

CREATE TRIGGER notification_attention_exists_insert
BEFORE INSERT ON notification_intents BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM attention_transitions a
    WHERE a.id = NEW.attention_transition_id
      AND a.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'notification_attention_scope_mismatch') END;
END;

CREATE TRIGGER attempt_environment_attempt_exists_insert
BEFORE INSERT ON attempt_environment_values BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_session_attempts a WHERE a.id = NEW.attempt_id)
  THEN RAISE(ABORT, 'environment_attempt_missing') END;
  SELECT CASE WHEN NEW.inherited_from_attempt_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_session_attempts a
    WHERE a.id = NEW.inherited_from_attempt_id)
  THEN RAISE(ABORT, 'inherited_environment_attempt_missing') END;
END;

CREATE TRIGGER runtime_revision_attempt_scope_insert
BEFORE INSERT ON agent_session_runtime_revisions
WHEN NEW.attempt_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_session_attempts a
    WHERE a.id = NEW.attempt_id
      AND a.agent_session_id = NEW.agent_session_id)
  THEN RAISE(ABORT, 'runtime_revision_attempt_scope_mismatch') END;
END;

CREATE TRIGGER legacy_canonical_target_insert
BEFORE INSERT ON legacy_import_rows WHEN NEW.canonical_id IS NOT NULL BEGIN
  SELECT CASE WHEN NEW.canonical_type NOT IN
    ('conversation','agent_session','node','operation','stream','resource')
  THEN RAISE(ABORT, 'legacy_canonical_type_unknown') END;
END;

CREATE TRIGGER blob_state_requires_zero_references
BEFORE UPDATE OF state ON blob_objects
WHEN NEW.state IN ('quarantined','deleted') BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM blob_references r WHERE r.digest = OLD.digest
    UNION ALL SELECT 1 FROM operations x
      WHERE x.result_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM native_records x
      WHERE x.payload_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM context_checkpoints x
      WHERE x.summary_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM resource_versions x
      WHERE x.blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM node_parts x
      WHERE x.content_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM provenance_records x
      WHERE x.evidence_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM ingestion_decisions x
      WHERE x.detail_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM input_buffers x
      WHERE x.text_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM outbox x
      WHERE x.payload_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM effect_attempts x
      WHERE x.request_blob_digest = OLD.digest OR x.receipt_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM repair_records x
      WHERE x.before_blob_digest = OLD.digest OR x.after_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM purge_authorizations x
      WHERE x.manifest_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM interaction_details x
      WHERE x.prompt_ref = OLD.digest OR x.options_ref = OLD.digest OR
            x.response_ref = OLD.digest
    UNION ALL SELECT 1 FROM peer_messages x WHERE x.body_ref = OLD.digest
    UNION ALL SELECT 1 FROM tui_drafts x WHERE x.text_ref = OLD.digest
    UNION ALL SELECT 1 FROM command_vocabulary_snapshots x
      WHERE x.payload_ref = OLD.digest
    UNION ALL SELECT 1 FROM structural_changes x WHERE x.payload_ref = OLD.digest
    UNION ALL SELECT 1 FROM materialized_activity x WHERE x.payload_ref = OLD.digest
    UNION ALL SELECT 1 FROM legacy_import_rows x WHERE x.error_ref = OLD.digest)
  THEN RAISE(ABORT, 'blob_still_referenced') END;
END;

INSERT INTO schema_migrations(
  version, migration_id, sha256, installed_at, min_reader_version,
  min_writer_version, rollback_class, rollback_deadline_version)
VALUES(
  1, '0001_v4_clean',
  '7c0b08fb8cf20a575c694f5ae8f990da07e4281c6640b59fca9a9fc33fc50001',
  unixepoch('subsec'), 1, 1, 'restore_required', 1);

INSERT INTO schema_metadata(
  singleton, current_version, oldest_compatible_reader,
  oldest_compatible_writer, clean_install_sha256, updated_at)
VALUES(
  1, 1, 1, 1,
  '7c0b08fb8cf20a575c694f5ae8f990da07e4281c6640b59fca9a9fc33fc50001',
  unixepoch('subsec'));

PRAGMA user_version = 1;
-- 38.35 FINALIZATION END
```

The intermediate three-unit schema hash is computed over UTF-8, LF-terminated
Foundation + Section 38.27 + Finalization SQL, in that order, with no Markdown
fences/marker comments and with both embedded schema-hash literals normalized
to 64 ASCII zeroes. That intermediate digest is
`7c0b08fb8cf20a575c694f5ae8f990da07e4281c6640b59fca9a9fc33fc50001`.
Section 38.39 extends the hash input, updates both rows, and declares the final
version-1 digest. The checked-in `.sql` artifact embeds only that final value;
startup rejects an all-zero, intermediate, or mismatched hash. The artifact
generator fails if a table, index, trigger, or migration appears in the
document but not the generated schema catalog.

#### Migration, compatibility, and rollback protocol

Every migration is one immutable, checksummed file named
`NNNN_<migration_id>.sql` plus a manifest containing `from_version`,
`to_version`, minimum reader/writer versions, rollback class, estimated writer
time, required free bytes, affected tables, and backup requirement. Startup
compares both `PRAGMA user_version` and `schema_metadata.current_version`; a
mismatch is corruption and enables repair-only mode.

Migration execution is exact:

1. refuse new API mutations and worker claims; drain the single FIFO writer;
2. acquire `maintenance_leases('schema_migration')`, prove no other daemon
   instance, and take/verify the required online backup and Blob manifest;
3. reject an unknown applied migration, checksum mismatch, database newer than
   the binary, or insufficient free space;
4. open the sole writer, enable foreign keys, run `BEGIN EXCLUSIVE`, execute
   statements without implicit commits, backfill in declared bounded steps,
   rebuild affected SQLite tables, run `foreign_key_check`, insert the migration
   row, update metadata and `user_version`, then commit;
5. reopen connections and run `quick_check` plus the migration's semantic
   postconditions before releasing maintenance; and
6. if any pre-commit step fails, roll back the transaction. If post-commit
   verification fails, keep mutations disabled and follow the declared rollback
   class.

`transactional` means the inverse migration is executable while no writer newer
than `rollback_deadline_version` has committed. `restore_required` means code
rollback restores the verified pre-migration database and Blob manifest; it
never runs ad-hoc inverse SQL. `irreversible` requires explicit owner approval,
a second verified backup, and a release whose old binary refuses the new schema.
Version 1 is `restore_required`: its rollback is removal of the new data root or
restoration of a pre-v4 backup, never reinterpretation by legacy code.

Rolling upgrades are not supported for the local singleton. Readers older than
`oldest_compatible_reader` and writers older than
`oldest_compatible_writer` fail before opening a request. Plugin migrations run
only inside a registered namespaced extension migration after core migration;
they cannot alter core tables.

#### Deletion and physical-reference law

Archive is an update to `archived_at`; it deletes nothing. Physical
Conversation deletion is reachable only through the owner-authorized purge
workflow: verified backup, immutable manifest Blob, `purge_authorizations` row,
`purge_state='purge_authorized'`, consumed authorization, then one short delete
transaction. Conversation-local canonical/supporting rows cascade only from
that guarded root delete. Cross-retention evidence uses `SET NULL` or retains a
namespaced entity ID. Identity/repair/security records are separately retained
for the periods in Section 38.34 and are not accidental cascade children.

`RESTRICT` is intentional for heads, causal parents, retained Resources, Blob
references, outbox effects, repairs, and active workflow history. A purge must
first replace or explicitly remove those references in its manifest; SQLite
never silently nulls semantic ancestry. AgentSession deletion is possible only
inside Conversation purge: attempts, aliases, source registrations, and current
facets cascade; canonical Nodes/Operations use their Conversation ownership and
retain nullable producer/session attribution as specified by their composite
FKs.

Every durable byte reference is either an inline FK to `blob_objects.digest` or
is checked by a post-extension trigger and included in the Blob state-change
reachability assertion. High-volume Stream and Observation references are also
mirrored in `blob_references` so GC does not scan their owner tables.
`blob_objects` is never physically deleted: byte expiry moves it to `deleted`
after normalized reachability reaches zero, preserving digest, length, media,
retention, and unavailable state. Upload paths are derived only as
`uploads/<resource-id>/<upload_basename>` from a `resource_versions` row; no
database column contains an arbitrary upload path. Open Stream paths are
derived only from `streams.id` and the checked `<id>.open` staging key.

#### Clean-install verification and future-workflow extension

CI must extract Foundation, Section 38.27, Finalization, the Section 38.39
Future Workflow block, and the Section 40.7 Second Review block, execute them
in that order against the oldest and newest supported packaged SQLite, then
run:

```sql
PRAGMA foreign_key_check;
PRAGMA integrity_check;
SELECT current_version FROM schema_metadata WHERE singleton = 1;
```

It also executes negative fixtures for cross-Conversation heads/parents,
duplicate lead tracks, non-committed heads, missing Blob/Resource references,
slot reuse after release, stale arm revisions, source-registration scope,
unauthorized purge, and Blob GC with live reachability.

Section 38.39 extends the intermediate verified core with the handover,
account-migration, backup/restore, collaboration, public-link, extension,
repair, anomaly, and legacy-registry tables and recomputes the intermediate
four-unit digest. Section 40.7 adds the final legacy-feature tables and
canonical five-unit digest. Unknown legacy fingerprints fail closed; a measured Phase 0
fixture and mapping must be registered before any import begins.

### 38.36 OpenAPI schemas, authentication, and endpoint/event traceability

This section closes the field-schema, authentication, and traceability gaps for
the fixed 115 endpoints in Section 38.24. It includes the client-limits
operation defined in §42.1 and does not change the daemon outage,
one-SQLite-database, retained-complexity, or no-edge-spool decisions. The
operation manifest below contains exactly one row for each of those 115
method/path pairs. An architecture test extracts the
pairs from Sections 38.24 and 38.38, rejects duplicates, and requires set
equality before the generated OpenAPI artifact can be accepted.

The authoritative generated artifact is `api/openapi-v1.yaml`, OpenAPI 3.1.0.
Generation is deterministic from this section and the registered provider,
extension, preference, repair, and Operation-detail schema catalogues. CI
validates the artifact with a 3.1 validator, compares every method/path,
operation ID, security requirement, parameter, request body, response, and SSE
event against this section, and fails on an unreferenced or missing schema.

#### Contract notation and universal wire rules

The schema declarations below use this normative shorthand:

- `T?` is nullable; omission is allowed only when the field also has `optional`.
- `text[a..b]` counts Unicode scalar values; `bytes[a..b]` counts decoded bytes.
- `list<T>[a..b]` is ordered and has `uniqueItems=false` unless `unique` appears.
- `set<T>[a..b]` has `uniqueItems=true`; server output is lexicographically sorted.
- `map<K,V>[0..N]` is an object with at most N properties and keys matching K.
- `{a:T,b:U}` is a closed object: `additionalProperties=false`, both fields
  required unless marked `optional`, and unknown fields return
  `400 unknown_field`.
- `enum(...)` is case-sensitive. IDs are lowercase canonical UUID strings unless
  the field explicitly says opaque/provider ID. Revisions are JSON integers in
  `0..9007199254740991`.
- A request has no body when its request schema is `-`. JSON request bodies are
  at most 1 MiB after content decoding. Query strings are at most 16 KiB.
- All `text` is valid UTF-8, contains no NUL, and is NFC-normalized except user
  message/draft text, which preserves exact code points other than rejecting NUL.
- `Content-Type` is exact: JSON is `application/json`, errors are
  `application/problem+json`, byte responses use the stored media type,
  uploads use `multipart/form-data`, and SSE uses `text/event-stream`.
- All GET/HEAD responses use `Cache-Control: no-store`. Mutations also return
  `Cache-Control: no-store`. Byte content may use `private, max-age=0` and ETag.
- Every response includes `X-Request-ID`; callers may supply a UUID
  `X-Request-ID`, otherwise the daemon creates one. Invalid supplied IDs return
  `400 invalid_request_id`.
- `Idempotency-Key` is required only where the operation manifest includes `IK`.
  It is 1..128 printable ASCII excluding whitespace at either end. The digest is
  over method, canonical path, authenticated principal, content type, and exact
  decoded body bytes. Records live 24 hours or for the complete Operation
  lifetime, whichever is longer.
- `If-Match` is required only where the manifest includes `IM`; its sole valid
  form is a quoted decimal revision. `CAS` means the named body revision is the
  only concurrency token and `If-Match` is forbidden.
- `Range` accepts one `bytes=start-end` range only. Multiple/suffix/open-ended
  ranges return `416 invalid_range`. Successful ranges return `206` and exact
  `Content-Range`; an unsatisfiable valid range returns `416 range_not_satisfiable`.
- List order is fixed by the operation profile below. Page cursors bind the
  principal, authorization revision, complete normalized filter, order, page
  size, and read-model generation. They are authenticated, opaque, expire after
  24 hours, and never authorize data by themselves.

Common scalar schemas are:

```text
UUID = text matching ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
Revision = integer[0..9007199254740991]
Timestamp = RFC3339 UTC text matching YYYY-MM-DDTHH:MM:SS.ffffffZ
Cursor = text[16..2048]
ProviderId = text[1..128] matching ^[a-z0-9][a-z0-9._-]*$
ActorKey = text[1..256]
WorkspaceRef = text[1..4096]
MediaType = text[1..255]
Digest = text matching ^sha256:[0-9a-f]{64}$
SchemaKey = text[1..160] matching ^[a-z0-9][a-z0-9._/-]*$
JsonValue = null | boolean | finite-number | text | list<JsonValue> | map<text,JsonValue>
RegisteredValue = {schema_key:SchemaKey,schema_version:integer[1..2147483647],value:JsonValue}
RuntimeMode = enum(interactive,headless,sdk,server,remote)
Freshness = enum(fresh,stale,unavailable,unknown)
Availability = enum(available,unavailable,unsupported,unknown)
```

`JsonValue` is bounded to depth 16, 4,096 total members, 64 KiB canonical JSON,
and 16 KiB per string. A `RegisteredValue` is additionally validated against the
catalogued schema named by `(schema_key,schema_version)`. Built-in catalogue
entries are materialized as OpenAPI discriminator `oneOf` branches. Unknown
catalogue entries return `422 schema_not_registered`; this is not an
unvalidated catch-all.

#### Exact principal, credential, session, certificate, and revocation model

The authorization owner is `AuthorizationService`; HTTP adapters authenticate
transport credentials and pass an immutable `AuthorizationContext` to the
application. Application services never inspect cookies, bearer strings,
certificate bytes, proxy headers, or Unix credentials.

```text
PrincipalDTO = {
  id:UUID, kind:enum(human,service,edge,terminal,remote_agent),
  display_name:text[1..200], state:enum(active,suspended,revoked),
  authorization_revision:Revision, created_at:Timestamp, revoked_at:Timestamp?
}
AuthorizationContext = {
  principal:PrincipalDTO, credential_id:UUID,
  credential_kind:enum(unix_peer,browser_session,bearer,client_certificate,invitation),
  device_id:text[1..128]?, scopes:set<Scope>[1..128],
  issued_at:Timestamp, expires_at:Timestamp?, authentication_revision:Revision
}
Scope = enum(
  self.read,self.write,surface.telemetry,
  conversation.read,conversation.write,conversation.drive,conversation.admin,
  account.read,account.write,notification.read,notification.write,
  machine.read,machine.admin,terminal.presence,edge.ingest,remote.execute
)
```

Durable authentication state is stored in the same SQLite database:

```text
principals(id, kind, display_name, state, authorization_revision,
           created_at, revoked_at)
principal_role_bindings(principal_id, role, scope_id, revision, created_at,
                        revoked_at)
auth_credentials(id, principal_id, kind, secret_hash_or_spki_digest, audience,
                 scopes, issued_at, expires_at, state, revision, revoked_at)
browser_sessions(id, principal_id, device_id, secret_hash, csrf_hash,
                 issued_at, absolute_expires_at, idle_expires_at, last_seen_at,
                 authentication_revision, state, revoked_at)
certificate_authorities(id, purpose, public_bundle_ref, state, not_before,
                        not_after, revision)
certificate_revocations(serial, ca_id, principal_id, reason, revoked_at,
                        revision)
invitation_credentials(id, token_hash, conversation_id, offered_role,
                       offered_actor_permissions, expires_at, state,
                       accepted_principal_id, revision)
```

Secret and token hashes are HMAC-SHA-256 under the active credential-pepper key;
raw secrets are never stored or logged. Pepper keys are CredentialPort secrets,
backed up, versioned, and rotated with a 24-hour verification overlap. Audit rows
store credential ID and digest prefix only.

Authentication mechanisms are exact:

1. **Unix owner.** The HTTP Unix socket is mode `0600`. The server reads
   `getpeereid`/`SO_PEERCRED`; the UID must equal the data-root owner. This maps
   to the single local human owner principal with all scopes. No request header
   can assert or override Unix identity. Unix-owner requests are not subject to
   CSRF because browsers cannot reach the socket.
2. **Browser session.** The desktop launcher obtains a one-use, 60-second
   bootstrap secret over the owner Unix socket and opens the loopback UI with
   the secret in the URL fragment. Fragment JavaScript exchanges it through the
   non-product bootstrap route `POST /auth/bootstrap`; that route accepts only
   loopback, exact allowed `Origin`, and `{secret,device_id}` and returns 204
   while setting `__Host-baqylau_session=<256-bit-random>` plus a response-only
   `X-Baqylau-CSRF` secret. The fragment is cleared before any subresource load.
   The cookie is `Secure; HttpOnly; SameSite=Strict; Path=/` outside the declared
   loopback-development profile; it has a 12-hour absolute and 30-minute idle
   lifetime. The CSRF secret lives only in browser memory and is never a cookie.
   Remote browser sessions use the same route only after upstream OIDC or mTLS
   has supplied a verified human principal to the daemon's configured auth
   adapter; arbitrary identity headers are rejected unless the connection is
   from the pinned proxy certificate.
3. **CSRF.** Every cookie-authenticated POST/PUT/PATCH/DELETE requires
   `Origin` equal to the configured UI origin, `Sec-Fetch-Site: same-origin`,
   `Content-Type` allowed by the operation, and `X-Baqylau-CSRF` whose hash
   matches the session. Missing/invalid values return `403 csrf_failed` before
   body processing. Bearer, certificate, invitation, and Unix authentication
   do not use CSRF and cannot be combined with a session cookie.
4. **Bearer.** Tokens are `bqv4_` plus 43 base64url characters encoding 256
   random bits. Only `Authorization: Bearer <token>` is accepted. Each token has
   one audience (`api`, `mcp`, or `bootstrap`), an exact scope set, a maximum
   90-day lifetime, and a credential revision. Query/cookie bearer tokens are
   rejected. The daemon accepts no JWT directly; an OIDC adapter must exchange
   it for this opaque credential after issuer, audience, nonce, signature, and
   expiry verification.
5. **Mutual TLS.** Non-loopback machine connections require TLS 1.3 and a
   client certificate chaining to the configured Baqylau CA. SAN is exactly
   `spiffe://baqylau/<role>/<principal-uuid>` where role is `edge`, `terminal`,
   `remote-agent`, or `proxy`; EKU includes clientAuth; serial, validity, SPKI
   digest, role, and principal must match `auth_credentials`. Edge certificates
   can call ingestion only, terminal certificates can call terminal presence
   only, remote-agent certificates can use the remote protocol only, and proxy
   certificates can supply the configured verified-principal assertion only.
   A human certificate maps to a human principal and still requires endpoint
   scopes.
6. **Invitation.** The invitation path token is 256 random bits, base64url,
   stored only as a hash, expires in at most seven days, and is one-use on
   accept. GET may authenticate solely with the token and returns only the
   redacted invitation DTO. Accept also requires an active human credential;
   token and human identities are both recorded. Responses use
   `Referrer-Policy: no-referrer`; access logs redact the token path segment.

Only one primary authentication mechanism is allowed. Multiple mechanisms,
credential/principal mismatch, or a proxy assertion without the pinned proxy
certificate returns `400 ambiguous_authentication`. Authentication failures use
`401 authentication_required|credential_expired|credential_revoked` with
`WWW-Authenticate`; authenticated lack of scope uses `403 insufficient_scope`.
Conversation/account/resource ownership failures return `404 not_found` to
avoid existence disclosure. `machine.admin` never follows from collaboration.

Authorization is evaluated twice for effects: at HTTP acceptance and immediately
before the outbox worker performs the effect. Each outbox row stores principal,
credential, required policy, and authorization revision. Revocation before an
effect starts yields `failed_before_action`; revocation after possible action
does not rewrite history and reconciliation determines the truthful result.

Suspending/revoking a principal increments its authorization revision, revokes
all sessions/credentials, invalidates its feed cursors, cancels safe pending
effects, and closes its SSE connections within one heartbeat interval. A role,
collaborator, token-scope, or Conversation-permission change also increments the
affected authorization revision. Every request checks current durable revision;
positive authorization caches live at most five seconds and are keyed by the
revision. Certificate revocation is checked on connection and on every request;
long-lived remote/SSE connections recheck at least every 15 seconds. Revocation
never deletes canonical attribution or raw evidence.

Authorization policy codes used below are:

| Code | Exact policy |
|---|---|
| `PUB` | Invitation token grants only the redacted GET; no principal required |
| `SELF-R` | active principal with `self.read`; result restricted to itself |
| `SELF-W` | active principal with `self.write`; result restricted to itself |
| `SURF` | browser/bearer human with `surface.telemetry` for its own `surface_id` |
| `CR` | `conversation.read` and active viewer/editor/driver/admin membership, or local owner |
| `CW` | `conversation.write` and editor/driver/admin membership, or local owner |
| `CD` | human `conversation.drive` and driver/admin membership, or local owner; peer/service credentials cannot satisfy it |
| `CA` | `conversation.admin` and Conversation admin membership, or local owner |
| `AR` | `account.read` for the same principal/account, or `machine.admin` |
| `AW` | `account.write` for the same principal/account, or `machine.admin` |
| `NR` | `notification.read` for the current principal, or `machine.admin` |
| `NW` | `notification.write` for the current principal, or `machine.admin` |
| `MR` | local owner or `machine.read` bearer/human certificate |
| `MA` | local owner or human `machine.admin`; service/peer credentials never satisfy it |
| `TERM` | trusted terminal client certificate with `terminal.presence`; binding owner must match certificate principal |

### 38.37 Storage, workflow ports, and provider mapping closure

This section closes the remaining storage and internal-contract work identified
in Section 0.3 for pre-existing v4 features. It is normative. Section 38.25
continues to own storage added directly by the live-system review; this section
owns the previously described core, handover, collaboration, backup, repair,
extension, backend, and provider-mapping paths. Clean DDL and OpenAPI assembly
must reproduce these contracts without changing them.

Here **compare-and-set (CAS)** means “write only if the stored revision equals
the supplied expected revision.” A **read view** is one SQLite read transaction
that gives all its queries the same database snapshot. A **lease** is a durable,
expiring claim with a random ID; only the current lease holder may finish work.

#### 38.37.1 Transaction scopes and common results

```python
@dataclass(frozen=True)
class ConversationWrite:
    conversation_id: UUID
    expected_revision: int | None

@dataclass(frozen=True)
class MachineWrite:
    owner: Literal["account", "backend", "backup", "collaboration",
                   "diagnostics", "extension", "maintenance", "provider_edge"]
    expected_revision: int | None

class Storage(Protocol):
    @contextmanager
    def conversation_write(self, spec: ConversationWrite) \
            -> Iterator[ConversationUnitOfWork]: ...
    @contextmanager
    def machine_write(self, spec: MachineWrite) \
            -> Iterator[MachineUnitOfWork]: ...
    @contextmanager
    def read_view(self) -> Iterator[ReadView]: ...
```

A Conversation coordinator is required before a
`ConversationUnitOfWork`. Machine services use `MachineUnitOfWork` without a
fake Conversation. A workflow touching several Conversations acquires their
coordinator mutation leases in ascending UUID-byte order, then opens one
`MachineUnitOfWork`; leases are released in reverse order after commit. It may
not nest SQLite transactions or hold a coordinator lease across terminal,
network, provider, credential, or filesystem I/O. Failure to acquire all
coordinators in 250 ms returns `ScopeBusy(retry_after_ms)` before any write.

Every method returns a typed value or one of:

```text
NotFound(entity,id) | AlreadyExists(entity,identity)
RevisionConflict(entity,expected,current)
InvalidTransition(entity,from_state,command)
ScopeMismatch(entity,expected_scope,actual_scope)
LeaseLost(entity,lease_id) | ScopeBusy(retry_after_ms)
CursorInvalid | CursorExpired
EvidenceUnavailable(code) | BlobUnavailable(code)
StorageBusy(retry_after_ms) | StorageDegraded(code)
```

No missing row, expired Blob, or unknown external fact becomes empty text,
zero, `False`, or success. A list is empty only after its query succeeds.

#### 38.37.2 Exact storage ports for pre-existing features

`_tx` methods join the supplied transaction and never commit. Read methods do
not mutate. `claim_*` opens one short adapter-owned write transaction and
returns a lease-bearing claim.

```python
class ConversationStore(Protocol):
    def create_tx(self, uow: ConversationUnitOfWork,
                  spec: ConversationCreate) -> Conversation: ...
    def get(self, view: ReadView, conversation_id: UUID) \
            -> Conversation | NotFound: ...
    def update_tx(self, uow: ConversationUnitOfWork,
                  change: ConversationChange,
                  expected_revision: int) -> Conversation: ...
    def set_lead_head_tx(self, uow: ConversationUnitOfWork, node_id: UUID,
                         expected_head_id: UUID | None,
                         evidence: EvidenceRef) -> Conversation: ...
    def list_overviews(self, view: ReadView, query: ConversationListQuery,
                      limit: int, cursor: str | None) \
            -> Page[ConversationOverview]: ...

class NodeStore(Protocol):
    def insert_provisional_tx(self, uow: ConversationUnitOfWork,
                              spec: ProvisionalNodeSpec) -> Node: ...
    def commit_tx(self, uow: ConversationUnitOfWork, node_id: UUID,
                  parts: tuple[NodePartSpec, ...],
                  expected_track_revision: int) -> Node: ...
    def abort_tx(self, uow: ConversationUnitOfWork, node_id: UUID,
                 reason: str) -> Node: ...
    def ancestry(self, view: ReadView, track_id: UUID, head_id: UUID,
                 limit: int, before: SourceOrderKey | None) -> tuple[Node, ...]: ...

class AgentSessionStore(Protocol):
    def create_tx(self, uow: ConversationUnitOfWork,
                  spec: AgentSessionCreate) -> AgentSession: ...
    def get(self, view: ReadView, session_id: UUID) \
            -> AgentSessionAggregate | NotFound: ...
    def resolve_external(self, view: ReadView, identity: ProviderIdentity) \
            -> IdentityResolution: ...
    def attach_alias_tx(self, uow: ConversationUnitOfWork,
                        spec: AliasSpec) -> AgentSessionAlias: ...
    def record_start_evidence_tx(self, uow: ConversationUnitOfWork,
                                 spec: StartEvidenceSpec) -> StartEvidence: ...
    def adopt_predecessor_tx(self, uow: ConversationUnitOfWork,
                             spec: AdoptionSpec,
                             expected_note_revision: int) -> AgentSession: ...
    def open_attempt_tx(self, uow: ConversationUnitOfWork,
                        spec: AttemptSpec) -> AgentSessionAttempt: ...
    def finish_attempt_tx(self, uow: ConversationUnitOfWork, attempt_id: UUID,
                          result: AttemptResult) -> AgentSessionAttempt: ...
    def transition_lifecycle_tx(self, uow: ConversationUnitOfWork,
                                command: SessionLifecycleCommand,
                                expected_revision: int) \
            -> AgentSessionLifecycle: ...

class SourceRegistrationStore(Protocol):
    """Durable cursors for provider artifacts and planned source roots."""

    def register_tx(self, uow: ConversationUnitOfWork,
                    spec: SourceRegistration) -> SourceRegistration: ...
    def advance_cursor_tx(self, uow: ConversationUnitOfWork,
                          registration_id: UUID,
                          expected_revision: int,
                          ordinal: int,
                          byte_offset: int) -> SourceRegistration: ...
    def set_source_ref_tx(self, uow: ConversationUnitOfWork,
                          *, registration_id: UUID,
                          source_ref: str) -> bool: ...
    def list_durable(self, view: ReadView) -> tuple[SourceRegistration, ...]: ...

class OperationStore(Protocol):
    def open_tx(self, uow: ConversationUnitOfWork,
                spec: OperationOpen) -> Operation: ...
    def materialize_unmatched_closer_tx(
            self, uow: ConversationUnitOfWork,
            spec: UnmatchedCloserSpec) -> Operation: ...
    def transition_tx(self, uow: ConversationUnitOfWork, operation_id: UUID,
                      command: OperationCommand,
                      expected_revision: int) -> Operation: ...
    def get(self, view: ReadView, operation_id: UUID) \
            -> OperationAggregate | NotFound: ...
    def list(self, view: ReadView, query: OperationQuery,
             limit: int, cursor: str | None) -> Page[Operation]: ...

class StreamMetadataStore(Protocol):
    def open_tx(self, uow: ConversationUnitOfWork,
                spec: StreamOpen) -> Stream: ...
    def checkpoint_tx(self, uow: ConversationUnitOfWork, stream_id: UUID,
                      expected_revision: int, file_revision: int,
                      byte_length: int) -> Stream: ...
    def seal_tx(self, uow: ConversationUnitOfWork, stream_id: UUID,
                expected_revision: int, final_blob: BlobRef) -> Stream: ...
    def abort_tx(self, uow: ConversationUnitOfWork, stream_id: UUID,
                 expected_revision: int, reason: str) -> Stream: ...
    def transfer_tx(self, uow: ConversationUnitOfWork, stream_id: UUID,
                    expected_revision: int,
                    new_owner: StreamOwner) -> Stream: ...

class ObservationStore(Protocol):
    def ingest(self, spec: ObservationIngest) \
            -> ObservationAccepted | ObservationDuplicate | StorageDegraded: ...
    def get(self, view: ReadView, observation_id: UUID) \
            -> Observation | NotFound: ...
    def claim_pending(self, consumer: ConsumerKind, now: datetime,
                      lease_for: timedelta, limit: int) \
            -> tuple[ObservationClaim, ...]: ...
    def finish_consumer_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                           claim: ObservationClaim,
                           result: ConsumerResult) -> Observation: ...

class EvidenceStore(Protocol):
    def record_decision_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                           decision: DecisionSpec) -> ProvenanceRef: ...
    def link_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                provenance: ProvenanceRef,
                entities: tuple[EntityRef, ...]) -> None: ...
    def record_anomaly_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                          anomaly: AnomalySpec) -> Anomaly: ...

class ResourceStore(Protocol):
    def create_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                  spec: ResourceCreate) -> Resource: ...
    def add_version_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                       resource_id: UUID, blob: BlobRef,
                       expected_revision: int) -> ResourceVersion: ...
    def get(self, view: ReadView, resource_id: UUID,
            version_id: UUID | None) -> ResourceResult: ...
    def mark_content_expired_tx(self, uow: MachineUnitOfWork,
                                version_id: UUID,
                                expected_blob: BlobRef) -> ResourceVersion: ...

class MutableSurfaceStore(Protocol):
    def get_buffer(self, view: ReadView,
                   identity: InputBufferIdentity) -> InputBufferResult: ...
    def put_buffer_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                      write: InputBufferWrite) -> InputBuffer: ...
    def tombstone_buffer_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                            write: InputBufferTombstone) -> InputBuffer: ...
    def get_preference(self, view: ReadView,
                       identity: PreferenceIdentity) -> PreferenceResult: ...
    def put_preference_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                          write: PreferenceWrite) -> Preference: ...
    def tombstone_preference_tx(
            self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                          write: PreferenceTombstone) -> Preference: ...

class ViewModeStore(Protocol):
    def get(self, view: ReadView, agent_session_id: UUID) \
            -> ViewModePreference | NotFound: ...
    def put_tx(self, uow: ConversationUnitOfWork,
               agent_session_id: UUID, mode: ViewMode,
               expected_revision: int, client_mutation_id: str,
               principal_id: UUID) -> ViewModePreference: ...

class OutboxStore(Protocol):
    def enqueue_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                   spec: OutboxSpec) -> OutboxItem: ...
    def claim(self, worker_kind: str, now: datetime, lease_for: timedelta,
              limit: int) -> tuple[OutboxClaim, ...]: ...
    def begin_attempt_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                         claim: OutboxClaim) -> EffectAttempt: ...
    def finish_attempt_tx(self, uow: ConversationUnitOfWork | MachineUnitOfWork,
                          attempt_id: UUID,
                          result: EffectAttemptResult) -> EffectAttempt: ...
```

Limits are exact: overview and Operation pages accept `1..200`; Node ancestry
accepts `1..500`; Observation/outbox claims accept `1..100`; one Blob/Stream
range is at most 4 MiB. Cursors bind the complete filter digest and stable final
key and are rejected with different filters.

| Query | Stable order/key | Required index |
|---|---|---|
| Conversation overview | `updated_at DESC,id DESC` | `conversations(updated_at DESC,id DESC)` plus project/archive indexes |
| Track ancestry | parent walk from validated head | `nodes(id,actor_track_id,conversation_id)` and `(actor_track_id,parent_id)` |
| Native scan | `source_epoch,source_ordinal` | `native_records(agent_session_id,source_epoch,source_ordinal)` |
| Operations | `source_position DESC,id DESC` | `(conversation_id,kind,state,source_position DESC,id DESC)` |
| Open Operations | `started_at,id` | partial `(conversation_id,started_at,id)` over nonterminal rows |
| Stream recovery | `updated_at,id` | partial `(state,updated_at,id)` over open rows |
| Observation dedup | globally canonical `dedup_key` from Section 9.3 | unique `observations(dedup_key)` |
| Outbox claim | `priority DESC,available_at,id` | partial index over claimable rows |
| Resource URI | backend/workspace/URI/version | `(backend_id,workspace_ref,canonical_uri,current_version_id)` |
| Draft/preference CAS | exact identity/revision | unique scope identity plus `(author_id,author_sequence)` |

#### 38.37.3 Atomic transaction catalogue

| Use case | Rows changed together |
|---|---|
| Create Conversation | Conversation + reserved lead track + initial workspace/grouping + provenance + structural change/outbox |
| Commit message | Node/parts + sealed Stream references + track head/revision + lead Conversation head/revision when applicable + invalidation + provenance + feed outbox |
| Start/resume/fork | control Operation/details + requested runtime revision + AgentSession/alias/lineage + `starting` attempt + environment inheritance + launch outbox |
| Provider record | native index + one consumer's canonical changes + source cursor + authority decision + projection revisions + feed outbox |
| Close/park | close checkpoint + host lifecycle + open-work accounting + pane/tab intents + source-reader decisions; Stream seal waits for drain evidence |
| Handover snapshot | immutable package/manifest + selected Node/Operation/Resource refs + checkpoint + provenance; no target I/O |
| Peer send | peer message + recipient deliveries + delivery Operation + outbox + structural change |
| Backup start | backup Operation + maintenance lease + manifest placeholder + outbox |
| Repair | repair Operation + before image + evidence + canonical changes + invalidations + after image + feed |
| Extension enable/disable | installation revision + grants + checkpoint + outbox; process receipt arrives later |
| Backend create/update | configuration revision + secret references only + provenance + system change; no connection attempt |

Start/resume/fork never copies requested values into effective values. The
provider identity/history receipt transaction writes effective runtime,
attempt handle, alias, and active lifecycle together. A lost receipt leaves the
attempt `starting` and Operation `unknown` until reconciliation.

#### 38.37.4 Workflow services and adapters

```python
class HandoverService(Protocol):
    def request(self, command: HandoverRequest) -> AcceptedOperation: ...
    def approve(self, operation_id: UUID, package_revision: int,
                resource_ids: frozenset[UUID]) -> HandoverCheckpoint: ...
    def resume(self, operation_id: UUID) -> HandoverCheckpoint: ...
    def reconcile(self, operation_id: UUID,
                  receipt: HandoverReceiptObservation) -> HandoverCheckpoint: ...

class CollaborationService(Protocol):
    def invite(self, command: InviteCommand) -> Invitation: ...
    def accept(self, command: AcceptInvitationCommand) -> Participant: ...
    def revoke(self, command: RevokeParticipantCommand) -> Participant: ...
    def send(self, command: PeerMessageCommand) -> AcceptedOperation: ...
    def receive(self, observation: PeerMessageObservation) -> PeerMessage: ...
    def acknowledge(self, observation: PeerReceiptObservation) \
            -> RecipientDelivery: ...

class BackupService(Protocol):
    def create(self, command: BackupCreate) -> AcceptedOperation: ...
    def verify(self, backup_id: UUID) -> AcceptedOperation: ...
    def restore_plan(self, backup_id: UUID) -> RestorePlan: ...
    def restore(self, command: RestoreCommand) -> AcceptedOperation: ...
    def reconcile(self, observation: BackupObservation) -> BackupManifest: ...

class RepairService(Protocol):
    def plan(self, code: str, arguments: Mapping[str, JSONValue],
             evidence_ids: tuple[UUID, ...]) -> RepairPlan: ...
    def apply(self, command: RepairApply) -> AcceptedOperation: ...
    def verify(self, repair_id: UUID) -> RepairVerification: ...

class ExtensionService(Protocol):
    def inspect(self, package: ExtensionPackageRef) -> ExtensionPlan: ...
    def enable(self, command: ExtensionEnable) -> AcceptedOperation: ...
    def disable(self, command: ExtensionDisable) -> AcceptedOperation: ...
    def reconcile(self, observation: ExtensionHostObservation) \
            -> ExtensionInstallation: ...

class BackendService(Protocol):
    def create(self, command: BackendCreate) -> BackendConfiguration: ...
    def update(self, command: BackendUpdate) -> BackendConfiguration: ...
    def delete(self, backend_id: UUID, expected_revision: int) -> None: ...
    def probe(self, backend_id: UUID) -> AcceptedOperation: ...
    def list_targets(self, principal: Principal) \
            -> tuple[ExecutionTargetOption, ...]: ...
    def reconcile(self, observation: BackendObservation) -> BackendHealth: ...
```

Handover input contains source Conversation/session/head revisions, exact
`RuntimeRequest`, context budget, Resource allow/deny sets, workspace policy,
redaction policy, and approval policy. Compilation is deterministic on that
snapshot. Activation requires target identity and, where supported, an
acknowledgement matching package digest and workspace revision. Rejection
leaves the source unchanged; indeterminate delivery probes before retry.

An invitation stores issuer, destination, role, actor permissions,
Conversation, nonce digest, expiry, state, and revision. Its clear token is
returned once and never stored. Broadcast expands to durable recipient rows in
the send transaction. A peer cannot approve, execute, type, access credentials,
or widen scope. Inbound dedup is `(transport_id,sender_identity,
external_message_id)`; body text alone is never a dedup key.

Backup obtains the maintenance lease, runs stepped SQLite online backup,
captures a Blob reachability manifest at the database high-water revision, and
verifies SQLite plus every digest. Restore requires maintenance mode, a safety
backup, owner confirmation of the plan digest, compatible schema, and zero
active attempts. It stages a sibling data root, verifies it, atomically swaps
roots, and retains the old root until 30 seconds of healthy operation.

A repair plan names affected entities/revisions, before values/digests,
evidence, expected after values, invalidated projections, and reversibility.
Apply rejects revision drift. Arbitrary SQL, paths, and unregistered JSON are
forbidden. An irreversible repair requires a verified backup ID.

Extension enable validates manifest/package/executable digests, protocol,
trust, schemas, permissions, migrations, and conflicts before desired state is
written. It becomes enabled only after the exact digest handshakes and reports
health. Disable revokes calls, waits two seconds, terminates if necessary, and
retains namespaced canonical data. Backend configuration stores no connection
or secret; probe/start/read/write/control are separate Operations. Delete
returns `BackendInUse` while any active attempt, workspace, Resource, account,
or workflow references it. Disconnect means reachability/control `unknown`,
not ended processes.

```python
class HandoverDelivery(Protocol):
    async def deliver(self, target: PreparedTarget,
                      package: HandoverPackage) \
            -> AcceptedReceipt | RejectedReceipt | IndeterminateReceipt: ...
    async def probe(self, correlation: HandoverCorrelation) \
            -> DeliveryProbeResult: ...

class CollaborationTransport(Protocol):
    async def send(self, envelope: SignedPeerEnvelope) \
            -> AcceptedReceipt | RejectedReceipt | IndeterminateReceipt: ...
    async def retract(self, handle: ExternalHandle) \
            -> RetractReceipt | Unsupported | IndeterminateReceipt: ...

class BackupAdapter(Protocol):
    async def create(self, plan: BackupPlan) -> BackupFilesReceipt: ...
    async def verify(self, manifest: BackupManifest) -> VerificationReport: ...
    async def stage_restore(self, plan: RestorePlan) -> StagedRestoreReceipt: ...

class ExtensionHost(Protocol):
    async def start(self, plan: ExtensionStartPlan) -> ExtensionHostReceipt: ...
    async def call(self, handle: ExtensionHandle, request: ExtensionCall) \
            -> ExtensionReply | ExtensionCallFailure: ...
    async def stop(self, handle: ExtensionHandle) -> StopReceipt: ...

class BackendAdapter(Protocol):
    async def probe(self, config: BackendConfiguration) -> BackendProbe: ...
    async def start(self, request: BackendStartRequest) -> RuntimeStartReceipt: ...
    async def resume(self, request: BackendResumeRequest) -> RuntimeStartReceipt: ...
    async def control(self, binding: RuntimeBinding,
                      action: SemanticControl) -> ControlReceipt: ...
    async def read_artifact(self, ref: BackendArtifactRef,
                            cursor: SourceCursor, max_bytes: int) \
            -> ArtifactChunk | ArtifactUnavailable: ...
    async def transfer(self, request: ResourceTransferRequest) \
            -> TransferReceipt: ...
```

Every adapter request stores its deadline. It returns before that deadline or
`IndeterminateReceipt`; no hidden unbounded retry is allowed. Non-idempotent
retry requires negative reconciliation or a provider idempotency key.

The saga runner starts after outbox recovery, leases at most 50 checkpoints for
30 seconds, executes at most one external step per workflow per claim, and
backs off 1/2/5/15/60 seconds. It stops automatic retry after ten
failed-before-action attempts or the first unresolved indeterminate attempt.
Backup/restore also requires the maintenance lease. Backend probes run only on
request or while active work needs freshness; stored configuration makes no
network connection.

#### 38.37.5 Common provider identity and source order

Every mapper first produces:

```text
ProviderRecordKey
  provider_id, backend_id, native_session_id, source_kind
  source_epoch          changes on replacement or unlinked relocation
  source_ordinal        provider monotonic integer inside the epoch
  native_record_id, native_parent_id, record_kind
  actor_key, turn_key, task_key
```

Order is lexicographic `(source_epoch,source_ordinal)` only after the adapter
proves both epochs are one relocation/continuation lineage. `observed_at`, file
mtime, and hook arrival never choose a branch or current facet. Duplicate
native IDs in one session/epoch must normalize identically; disagreement is
`provider_identity_collision` and quarantined.

```text
same AgentSession  same provider/backend/native identity and proven continuation
new AgentSession   native fork/successor in a known Conversation, or handover target
new Conversation   independent start with no continuation/fork evidence
provisional        insufficient evidence; destructive control and merge forbidden
```

Provider deletion never deletes canonical Nodes, Operations, usage, or audit.
It marks source unavailable and `resumable=false` after gesture-time proof.
User product purge is a separate retention Operation.

#### 38.37.6 Claude Code mapping

| Native field/record | Validation/order | Canonical result |
|---|---|---|
| hook `session_id` | non-empty ID in Claude/backend namespace | AgentSession alias, never Conversation ID alone |
| `transcript_path` | absolute and jailed; host event only | artifact registration; relocation epoch by inode/prefix proof |
| hook `cwd` | absolute; child `agent_id` cannot restamp host | current workspace; first host value freezes grouping |
| `InstructionsLoaded` | parsed record for native SID | independent-start evidence before adoption |
| `SessionStart` | parsed start plus environment presence map | attempt opener; blocks predecessor adoption |
| `PreToolUse(Task)` | `tool_use_id`, description, launch scope; no `agent_id` assumed | enqueue child-launch correlation and open `agent_task` Operation |
| `SubagentStart` | `agent_id`; consume eligible launch FIFO unless proven resume | create/bind actor track; persisted description on resume |
| `SubagentStop` | `agent_id`, transcript path, stop payload | normal child closer plus background-task facts |
| `agent_id` | non-empty Claude/backend actor identity | actor track; child path/cwd stays child-scoped |
| JSONL `uuid`/`parentUuid` | record IDs; absent parent only at root | native identity/ancestry, not blindly semantic parent |
| `type=user` prompt | exclude tool results, attachments, nested memory | prompt Node; last prompt-bearing sibling is live |
| assistant message | message ID + block ordinal | provisional/final Node and snapshot usage |
| `tool_use`/`tool_result` | stable tool ID; launch ack is acceptance | Operation opener/result; semantic closer owns completion |
| `model_refusal_fallback` | parsed system record | `provider_fallback` runtime revision via forward scan |
| `goal_status` attachment | parsed attachment | actor-track goal |
| compaction/summary | parsed records plus parent graph | Operation/checkpoint/summary and late revert correction |
| `StopFailure` | `agent_id` presence is tested before error enum | with `agent_id`: child API-failure closer; otherwise exact rate-limit/logout event; percentage is not trigger |
| child `meta.json` | stat-gated complete JSON; `agentId`, `toolUseId`, `agent_transcript_path`, `stoppedByUser` | launch join, transcript registration, and user-killed closer |
| parent `tool_result` | exact `tool_use_id`; `is_error=true` | rejected-Task child closer even when no child Stop hook exists |
| status-line payload | exact session/account plus usage windows | context/runtime/quota observation under Section 38.4 transport rules |

`SubagentStop.background_tasks` is mapped as a complete child-owned background
task snapshot before the actor is closed. `agent_type` is retained as metadata
but never filters closure or finalization; orchestrators must close too. The
closed Claude schema registry also includes the complete child `meta.json`
fields above and the Ctrl+B/backgrounding payload (`session_id`, cwd,
transcript path, task/tool identity, target background path, byte offset, and
result), so those known fields cannot be rejected as unsupported.

The child `meta.json` watcher polls no faster than every 0.4 seconds and reads
only after a stat signature change. It advances the durable signature only
after complete JSON parse and identity validation; a torn/partial read leaves
the prior signature and monotonic `stoppedByUser` latch unchanged so the same
bytes are retried. Once true, that latch never returns to false for the child.
Duplicate `SubagentStart` for an already bound actor updates evidence/freshness
without rendering a second header or replaying the transcript.

Claude ordinal is the zero-based complete JSONL record number within an epoch;
byte offset is evidence. A torn last line gets no ordinal. Relocation preserving
inode or verified prefix continues the ordinal; otherwise a new epoch records
the predecessor digest/ordinal and needs identity proof before comparison.

Resume reuses the AgentSession only when the same SID/artifact is readable at
gesture time. A new SID adopts a predecessor only with the cwd-scoped hosted
single-use note and no `InstructionsLoaded`/`SessionStart` mark; Claude emits no
additional positive continuation event for this path. Explicit fork creates a
new AgentSession in the same Conversation.
Unannounced branching remains in one AgentSession and reselects the actor head
when a later prompt-bearing record points to an ancestor. Lost transcript
returns `410 session_artifact_gone` without deleting canonical history.

Fixtures:

```text
claude_independent_instructions_loaded_blocks_adoption
claude_fork_event_precedes_predecessor_start
claude_scrubbed_fork_inherits_environment_presence
claude_enter_worktree_relocates_reader_not_group
claude_child_cwd_does_not_relocate_host
claude_last_prompt_sibling_prunes_old_subtree
claude_tool_result_wrapper_not_prompt_bearing
claude_compaction_revert_arrives_late
claude_mid_file_model_refusal_fallback
claude_async_launch_ack_not_completion
claude_pretool_task_fifo_binds_subagent_start
claude_resumed_teammate_does_not_pop_launch_fifo
claude_meta_stopped_by_user_closes_child
claude_parent_tool_result_rejects_child_by_tool_use_id
claude_stop_failure_agent_id_closes_child_before_error_mapping
claude_cwd_note_consumes_before_successor_sid_known
claude_backgrounding_fork_leaves_no_adoption_note
claude_deleted_transcript_returns_410_without_history_loss
```

#### 38.37.7 Codex mapping

| Native field/item | Validation/order | Canonical result |
|---|---|---|
| `thread_id` | non-empty backend/account-scoped ID | AgentSession identity |
| rollout/thread reference | jailed path or opaque server ref | source registration, not project identity |
| `turn_id` | thread-scoped stable ID | `turn_key`; several items may share it |
| item ID/index | ID unique in thread; index monotonic | native ID/source ordinal |
| user item | parsed kind, not embedded text | prompt Node |
| assistant delta/message | item ID + block ordinal/revision | provisional Stream then Node |
| `event_msg` | authority by registered semantic family; duplicate with `response_item` suppressed | secondary lifecycle/progress evidence |
| `response_item` | primary for prompts, including post-abort/queued prompt absent from `event_msg` | canonical prompt/response item |
| `phase=task_started` | child/task identity and ordered item | open child task block |
| `phase=final_answer` | exact task/actor identity | canonical child result and parent-turn final-turn input |
| `phase=task_complete` | lifecycle only; never chooses pending prose as result | close child task after pending-buffer rule |
| `last_agent_message` | fallback only when manifest explicitly proves no `final_answer` support | lower-authority result candidate |
| command/tool/file item | registered kind; complement handles unknown | Operation/Stream/Resource |
| `turn_aborted` | parsed kind/field | interruption closer; substring inadmissible |
| child actor ID | thread-scoped namespace | actor track with separate task/turn keys |
| app-server sequence | monotonic per connection generation | ordinal; reconnect links only after thread proof |

Rollout order is complete JSONL item number; app-server order is
`(connection_generation,server_sequence)`. Reconnect continues an epoch only
for the same thread and a sequence after the durable cursor; reset/overlap uses
a new epoch and item-ID dedup. Same thread resumes an AgentSession. Explicit
fork with source thread/item creates a new AgentSession in the same
Conversation. An unlinked thread is a new Conversation unless a handover
Operation supplies continuity. Branch moves require explicit fork or native
ancestor parent evidence, never arrival order. Remote deletion preserves
canonical history and disables resume.

Codex local sidecar discovery uses `git_root` when available and CWD otherwise.
The basename is taken from `git_root.rstrip("/")` before resolving symlinks,
sanitized with `re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")`, and
falls back to `workspace` when empty. The suffix is
`sha256(realpath(git_root)).hexdigest()[:16]`; only the suffix hashes the
realpath. This exact rule is shared by companion discovery and claim files.
Companion-job and native-rollout discovery deduplicate when the rollout UUID
equals the sidecar `threadId`. A rollout with no AgentSession identity is owned
through an atomic per-repository claim file; only the winning watcher maps it.
In secondary/companion mode the originator skips its own rollout because the
primary source owns it. In standalone mode the watcher adopts that rollout
because no primary exists. Claim identity and origin mode are persisted in the
source registration, and a restart cannot replay the same rollout into every
same-repository session.

Fixtures:

```text
codex_same_thread_resume_opens_attempt
codex_explicit_thread_fork_same_conversation
codex_unlinked_thread_new_conversation
codex_app_server_reconnect_overlap_deduplicates
codex_turn_aborted_record_not_nested_text
codex_unknown_rollout_kind_renders_generic
codex_child_actor_multiple_tasks_distinct
codex_response_item_owns_post_abort_prompt
codex_final_answer_owns_child_result
codex_task_complete_does_not_steal_pending_message
codex_token_count_after_final_answer_does_not_demote_result
codex_deleted_thread_preserves_history
```

#### 38.37.8 OpenCode mapping

| Native field/part | Validation/order | Canonical result |
|---|---|---|
| session ID | non-empty backend/account/server-scoped ID | AgentSession identity |
| message ID/parent ID | stable inside session | native index and semantic candidates |
| role | exact provider `user|assistant|system` enum | Node role after mapping |
| part ID/index/revision | message-scoped; monotonic revision | Node part or Stream mutation |
| tool call ID/state | stable tool identity | Operation transition |
| provider/model metadata | parsed fields | requested/effective runtime observation |
| server sequence | monotonic per connection generation | source ordinal; reset starts epoch |
| parent/fork session ID | structured provider field only | lineage and same-Conversation fork |

Server order is `(connection_generation,server_sequence)`; persisted artifacts
use complete record number. Part replacement requires the same part ID and a
higher revision. Same session ID resumes and opens an attempt. Structured
parent/fork link creates a new AgentSession in the predecessor Conversation.
Without it, a new session is independent even at the same cwd. Branches use
structured parent message IDs; absent parent/fork evidence stays linear rather
than guessing from time. Deletion disables resume and retains canonical data.

Fixtures:

```text
opencode_same_session_resume_opens_attempt
opencode_structured_parent_creates_fork
opencode_same_cwd_without_parent_independent
opencode_part_revision_replaces_same_part_only
opencode_connection_reset_new_source_epoch
opencode_unknown_tool_transition_rejected
opencode_missing_parent_linear_not_guessed
opencode_deleted_session_preserves_history
```

#### 38.37.9 Fixture artifact and residual external fact

Each named fixture is stored at
`tests/fixtures/providers/<provider>/<fixture-name>/` with:

```text
manifest.json          exact provider build, adapter manifest, source kind
input/                 byte-exact hooks, records, source files
expected-domain.json   deterministic fixture UUIDs
expected-decisions.json
expected-effects.json  empty unless effects are exercised
README.md              measured counterexample and rejected alternative
```

CI runs every fixture against every provider version claimed by the adapter. A
new record/event/version cannot ship until its field mapping, source-order rule,
deletion behavior, positive fixture, and malformed/negative fixture exist.

Provider version discovery is a measured implementation input: the repository
must capture the exact supported Claude Code, Codex, and OpenCode build numbers
and byte-exact payloads in those manifests. They cannot be truthfully invented
in an architecture document. Until captured, that adapter version is
unsupported. The five-unit DDL and OpenAPI source contracts are closed by
Sections 38.35–40.

### 38.38 OpenAPI component schemas and operation manifest

This section completes the normative Section 38.36 contract. All
authentication, wire, and generator rules in Section 38.36 remain in force.

#### Closed component schema registry

All entity DTOs contain the listed fields and no others. `EntityRef` is
`{type:SchemaKey,id:UUID,revision:Revision}`. `BlobRef` is
`{digest:Digest,byte_length:integer[0..9007199254740991],media_type:MediaType,
availability:Availability}`. `PageMeta` is
`{next_cursor:Cursor?,limit:integer[1..200],order:text[1..64]}`.
`Snapshot<T>` is `{snapshot_revision:Revision,feed_cursor:Cursor,data:T}`.
`Page<T>` is `{items:list<T>[0..200],page:PageMeta}`; a snapshot page is
`Snapshot<Page<T>>`. Empty arrays are authoritative empty results; unavailable
facets use `Freshness`/`Availability`, never an invented zero.

```text
Problem = {
  type:text[1..512], title:text[1..200], status:integer[400..599],
  code:text[1..128], detail:text[0..4000], request_id:UUID,
  current:RegisteredValue? optional, retry_after_ms:integer[0..86400000]? optional,
  errors:list<{path:text[1..512],code:text[1..128],message:text[1..1000]}>[0..100] optional
}
RuntimeRequest = {
  execution_target_id:UUID, account_id:UUID? optional, mode:RuntimeMode,
  model:text[1..200], effort:text[1..100], runtime_options_revision:Revision
}
RuntimeDifference = {field:enum(execution_target_id,account_id,mode,model,effort),requested:text[0..256]?,effective:text[0..256]?,reason:text[1..256]}
RuntimeResult = {
  requested:RuntimeRequest,effective_provider_id:ProviderId,
  effective_execution_target_id:UUID,effective_account_id:UUID?,
  effective_mode:RuntimeMode,effective_model:text[1..200],
  effective_effort:text[1..100],differences:list<RuntimeDifference>[0..16]
}
OperationAccepted = {operation:OperationDTO,requested_runtime:RuntimeRequest? optional,effective_runtime:RuntimeResult? optional,provisional_branch:{source_node_id:UUID,target_agent_session_id:UUID,durable_head_moved:boolean}? optional}
DeleteTombstone = {id:UUID,deleted_at:Timestamp,reason:text[1..256],revision:Revision}

HealthPart = {state:enum(healthy,degraded,unavailable,unknown),checked_at:Timestamp,code:text[1..128]?,detail:text[0..1000]?}
HealthDTO = {revision:Revision,daemon:HealthPart,database:HealthPart,blob:HealthPart,supervisor:HealthPart,provider_edges:HealthPart,projections:HealthPart,notifications:HealthPart,storage:StorageFootprintDTO,control_plane_read_only:boolean,ingestion_gap_count:integer[0..9007199254740991],audit_enabled:boolean}
StorageFootprintDTO = {database_bytes:integer[0..9007199254740991],wal_bytes:integer[0..9007199254740991],staging_bytes:integer[0..9007199254740991],blob_bytes:integer[0..9007199254740991],growth_bytes_per_day:integer[0..9007199254740991]?,estimated_exhaustion_at:Timestamp?,free_bytes:integer[0..9007199254740991]?,checked_at:Timestamp}
ProviderDTO = {id:ProviderId,label:text[1..200],version:text[1..100],modes:set<RuntimeMode>[1..5],state:enum(installed,degraded,unavailable,disabled),edge_state:text[1..64],trust_state:text[1..64],capability_revision:Revision}
RuntimeOptionDTO = {id:text[1..200],label:text[1..200],available:boolean,reason:text[1..256]?}
RuntimeOptionsDTO = {provider_id:ProviderId,execution_target_id:UUID,account_id:UUID?,mode:RuntimeMode?,models:list<RuntimeOptionDTO>[0..500],efforts:list<RuntimeOptionDTO>[0..100],default_model:text[1..200]?,default_effort:text[1..100]?,revision:Revision,freshness:Freshness}
CommandDTO = {name:text[1..200],description:text[0..1000],argument_hint:text[0..500]?,source:enum(builtin,workspace,skill,extension),availability:Availability}
CommandVocabularyDTO = {provider_id:ProviderId,execution_target_id:UUID,workspace_ref:WorkspaceRef,actor_key:ActorKey?,commands:list<CommandDTO>[0..2000],revision:Revision,freshness:Freshness}
ProviderEdgeDTO = {provider_id:ProviderId,backend_id:UUID,installed_version:text[1..100]?,desired_version:text[1..100],config_digest:Digest?,executable_digest:Digest?,trust_key:text[1..512]?,trust_state:text[1..64],state:text[1..64],last_verified_at:Timestamp?,last_error:text[0..2000]?,revision:Revision}
BackendDTO = {id:UUID,label:text[1..200],adapter_id:SchemaKey,endpoint_config:RegisteredValue,trust_class:enum(local,trusted_remote,untrusted_remote),enabled:boolean,observed_health:HealthPart,revision:Revision}
ExecutionTargetDTO = {id:UUID,label:text[1..200],backend_id:UUID,provider_id:ProviderId,default_mode:RuntimeMode,workspace_root_ref:WorkspaceRef?,provider_config:RegisteredValue,enabled:boolean,reachability:Availability,freshness:Freshness,revision:Revision}
AccountDTO = {id:UUID,provider_id:ProviderId,execution_target_id:UUID,label:text[1..200],enabled:boolean,priority:integer[-100000..100000],credential_state:enum(present,expired,missing,unknown),quota_summary:list<QuotaWindowDTO>[0..100],selection_eligible:boolean,refusal_reasons:list<text[1..256]>[0..100],revision:Revision}
QuotaWindowDTO = {account_id:UUID,provider_id:ProviderId,scope_key:text[1..200],model_scope:text[0..200],window_minutes:integer[1..525600],used_percent:number[0..100]?,resets_at:Timestamp?,state:enum(available,limited,logged_out,unknown),source_kind:enum(push,pull,imported),observed_at:Timestamp,freshness:Freshness,revision:Revision}
MoneyDTO = {currency:text matching ^[A-Z]{3}$,minor_units:integer[0..9007199254740991],kind:enum(vendor_reported,calculated,lower_bound,upper_bound)}
UsageBucketDTO = {key:map<text,text>[0..8],input_tokens:integer[0..9007199254740991],output_tokens:integer[0..9007199254740991],cache_read_tokens:integer[0..9007199254740991],cache_create_5m_tokens:integer[0..9007199254740991]?,cache_create_1h_tokens:integer[0..9007199254740991]?,cache_create_unclassified_tokens:integer[0..9007199254740991],costs:list<MoneyDTO>[0..20],error_count:integer[0..9007199254740991],updated_at:Timestamp}
UsageDTO = {from:Timestamp,through:Timestamp,ledger:enum(billing,per_actor_display,quota),group_by:list<enum(conversation,account,provider,model,day)>[0..5],buckets:list<UsageBucketDTO>[0..10000],freshness:Freshness}
StatsDTO = {from:Timestamp,through:Timestamp,conversation_count:integer[0..9007199254740991],active_conversation_count:integer[0..9007199254740991],operation_count:integer[0..9007199254740991],active_ms:integer[0..9007199254740991],pulse:{days_7:StatsCounterDTO,days_30:StatsCounterDTO,all:StatsCounterDTO},usage:UsageDTO,contribution_heatmap:list<{day:Date,conversation_count:integer[0..9007199254740991],operation_count:integer[0..9007199254740991],active_ms:integer[0..9007199254740991]}>[0..3660],hourly_punchcard:list<{weekday:integer[0..6],hour:integer[0..23],operation_count:integer[0..9007199254740991],active_ms:integer[0..9007199254740991]}>[168],error_series:list<{day:Date,info:integer[0..9007199254740991],warning:integer[0..9007199254740991],error:integer[0..9007199254740991],critical:integer[0..9007199254740991]}>[0..3660],project_series:list<{project_ref:text[1..512],days:list<{day:Date,operation_count:integer[0..9007199254740991],active_ms:integer[0..9007199254740991],tokens:integer[0..9007199254740991]}>[0..90]}>[0..1000],revision:Revision}
StatsCounterDTO = {conversation_count:integer[0..9007199254740991],operation_count:integer[0..9007199254740991],active_ms:integer[0..9007199254740991],tokens:integer[0..9007199254740991],costs:list<MoneyDTO>[0..20]}
AnomalyDTO = {id:UUID,code:text[1..128],severity:enum(info,warning,error,critical),title:text[1..200],explanation:text[1..4000],entity:EntityRef?,evidence_ids:list<UUID>[0..100],detected_at:Timestamp,remediation:text[0..4000]?,revision:Revision}
IngestionGapDTO = {id:UUID,source_kind:text[1..128],source_id:text[1..256]?,started_at:Timestamp,ended_at:Timestamp?,known_affected:list<EntityRef>[0..1000],cause:text[1..256],revision:Revision}

ConversationDTO = {id:UUID,title:text[0..500]?,head_node_id:UUID?,lead_track_id:UUID,revision:Revision,project_ref:text[1..512]?,created_at:Timestamp,updated_at:Timestamp,archived_at:Timestamp?,host_state:text[1..64],work_state:text[1..64],attention:text[1..64],active_time_ms:integer[0..9007199254740991],freshness:Freshness}
ConversationOverviewDTO = {conversation:ConversationDTO,provider_ids:set<ProviderId>[0..32],account_ids:set<UUID>[0..32],group_id:text[1..512]?,resume_provider_id:ProviderId?,resume_tool:enum(claude,codex,other)?,resume_command:text[1..2048]?,context_used_tokens:integer[0..9007199254740991]?,context_window_tokens:integer[0..9007199254740991]?,git_branch_or_detached_sha:text[1..512]?,git_owner_root:WorkspaceRef?,git_dirty:enum(dirty,clean,unknown),command_count:integer[0..9007199254740991],token_count:integer[0..9007199254740991],costs:list<MoneyDTO>[0..20],last_activity_at:Timestamp?,open_operation_count:integer[0..1000000],unread_notification_count:integer[0..1000000],warning_count:integer[0..1000000],revision:Revision}
ActorTrackDTO = {id:UUID,conversation_id:UUID,actor_key:ActorKey,agent_session_id:UUID?,parent_track_id:UUID?,lifecycle_operation_id:UUID?,track_kind:enum(lead,subagent,teammate,sidecar,peer),state:enum(active,idle,ended,lost),head_node_id:UUID?,context:ContextDTO,current_runtime:RuntimeResult?,scoreboard:ScoreboardDTO?,revision:Revision,created_at:Timestamp,ended_at:Timestamp?}
NodePartDTO = {ordinal:integer[0..1000000],kind:enum(text,image,file,artifact,structured),media_type:MediaType?,content:BlobRef?,stream_id:UUID?,resource_id:UUID?,metadata:RegisteredValue?}
NodeDTO = {id:UUID,conversation_id:UUID,actor_track_id:UUID,parent_id:UUID?,agent_session_id:UUID?,role:enum(user,assistant,system,summary),semantic_kind:enum(prompt,message,summary,recap,system),origin:enum(human,provider,baqylau,peer,imported),state:enum(streaming,committed,aborted),branch_visibility:enum(normal,suspect_retracted,superseded),completion_reason:enum(complete,interrupted,failed,unknown)?,parts:list<NodePartDTO>[0..10000],source_position:text[0..512]?,created_at:Timestamp,committed_at:Timestamp?,revision:Revision}
OperationDTO = {id:UUID,scope:EntityRef,conversation_id:UUID?,agent_session_id:UUID?,anchor_node_id:UUID?,parent_operation_id:UUID?,kind:SchemaKey,state:enum(pending,running,succeeded,failed,cancelled,denied,abandoned,lost,unknown),opener_state:enum(present,missing,unknown),origin:enum(observed,requested,inferred,imported),detail:RegisteredValue,result:BlobRef?,started_at:Timestamp,ended_at:Timestamp?,revision:Revision}
MessageDeliveryDetailDTO = {operation_id:UUID,agent_session_id:UUID,text:BlobRef,resource_ids:list<UUID>[0..100],client_message_id:text[1..128],parked_policy:enum(reject,resume),runtime_request:RuntimeRequest? required-iff-parked_policy-resume,detail_state:enum(accepted,waiting_for_resume,relaunching,dispatching,queued_at_provider,observed_in_history,delivered,cancelled,lost,unknown),resume_operation_id:UUID?,provider_delivery_key:text[1..512]?,revision:Revision}
AttemptDTO = {id:UUID,operation_id:UUID?,agent_session_id:UUID?,adapter_id:SchemaKey,attempt_number:integer[1..1000000],outcome:enum(pending,succeeded,failed_before_action,rejected,indeterminate),started_at:Timestamp,ended_at:Timestamp?,receipt:RegisteredValue?,error:Problem?}
AgentSessionDTO = {id:UUID,conversation_id:UUID,provider_id:ProviderId,execution_target_id:UUID,mode:RuntimeMode,view_mode:enum(verbose,default,focus),view_mode_revision:Revision,resumable:boolean,persistence_kind:enum(native_local,native_remote,baqylau_captured,ephemeral),host_state:enum(starting,live,parked,ended,lost),work_state:enum(active,drained,unknown,lost),current_runtime:RuntimeResult?,context:ContextDTO,attempts:list<AttemptDTO>[0..1000],capabilities:map<SchemaKey,Availability>[0..256],reachability:map<SchemaKey,text>[0..256],revision:Revision,freshness:Freshness}
ContextDTO = {context_window_tokens:integer[0..9007199254740991]?,context_used_tokens:integer[0..9007199254740991]?,current_model:text[1..200]?,state:enum(observed,stale,unavailable,unknown),observed_at:Timestamp?,revision:Revision}
InteractionDTO = {operation:OperationDTO,kind:enum(question,permission,plan,confirm),prompt:BlobRef,options:RegisteredValue?,plan_resource_id:UUID?,response:BlobRef?,response_revision:Revision,state:enum(open,partially_answered,submitting,answered,declined,dismissed,expired,lost),verdict:enum(approved,changes,rejected,confirmed,denied,answered,dismissed)?,edited:boolean?,current_question_index:integer[0..10000]?,answered_question_count:integer[0..10000],total_question_count:integer[0..10000]?,driver_layout:text[1..200]?}
ActivityPlacementDTO = {position_key:text[1..512],local_sequence:integer[0..9007199254740991],group_id:text[1..256]?}
ActivityItemDTO = {item_type:enum(node,operation,notice),item_id:UUID,activity_class:SchemaKey,register:enum(host,agent,team,codex,quiet,extension),audience:enum(lead,actor,both,hidden),placement:ActivityPlacementDTO,payload:RegisteredValue,revision:Revision}
ActivityPageDTO = {actor_track_id:UUID,head_node_id:UUID?,generation:Revision,freshness:Freshness,items:list<ActivityItemDTO>[0..200],page:PageMeta}
ConversationViewDTO = {conversation:ConversationDTO,selected_track:ActorTrackDTO,nodes:list<NodeDTO>[0..500],sessions:list<AgentSessionDTO>[0..100],operations:list<OperationDTO>[0..500],interactions:list<InteractionDTO>[0..100],activity:ActivityPageDTO,streams:list<StreamDTO>[0..500],resources:list<ResourceDTO>[0..500],facets:map<SchemaKey,RegisteredValue>[0..128],capabilities:map<SchemaKey,Availability>[0..256],feed_cursor:Cursor}

StreamDTO = {id:UUID,owner:EntityRef,channel:text[1..128],ordinal:integer[0..1000000],kind:SchemaKey,state:enum(open,sealed,aborted,lost),mode:enum(ordered_delta,snapshot_revision),revision:Revision,byte_length:integer[0..9007199254740991],media_type:MediaType?,render_kind:enum(plain,markdown,json,yaml,source,extension),language:text[1..100]?,retained_from_revision:Revision,visible_copy_available:boolean,raw_copy_state:enum(available,expired,never_captured),updated_at:Timestamp}
StreamOpDTO = {revision:Revision,op:enum(append,replace,reset),offset:integer[0..9007199254740991]?,end:integer[0..9007199254740991]?,bytes_base64:text[0..349528]}
StreamContentDTO = {stream_id:UUID,from_revision:Revision,through_revision:Revision,operations:list<StreamOpDTO>[0..1000],more:boolean,next_revision:Revision?}
ResourceVersionDTO = {id:UUID,digest:Digest,byte_length:integer[0..9007199254740991],content:BlobRef,source_operation_id:UUID?,created_at:Timestamp,availability:Availability}
ResourceDTO = {id:UUID,kind:SchemaKey,execution_target_id:UUID?,workspace_ref:WorkspaceRef?,canonical_uri:text[1..4096]?,media_type:MediaType,current_version:ResourceVersionDTO?,retention_class:SchemaKey,created_at:Timestamp,revision:Revision}
InputBufferDTO = {id:UUID,kind:enum(composer,new_session,interaction),conversation_id:UUID?,interaction_id:UUID?,project_ref:text[1..512]?,text:text[0..1000000],revision:Revision,author_id:UUID,author_sequence:integer[0..9007199254740991],origin:enum(surface,device,terminal),tombstone:boolean,updated_at:Timestamp}
PreferenceDTO = {namespace:SchemaKey,scope_type:enum(principal,device,conversation,project,global),scope_id:text[1..512],key:SchemaKey,schema_version:integer[1..2147483647],value:RegisteredValue?,revision:Revision,author_id:UUID,author_sequence:integer[0..9007199254740991],tombstone:boolean,updated_at:Timestamp}
PaneStateDTO = {conversation_id:UUID,agent_session_id:UUID,binding_revision:Revision,visible:boolean,percentage:integer[10..90],previous_visible:boolean,previous_percentage:integer[10..90],verification:enum(verified,failed,indeterminate),revision:Revision}
NotificationSettingsDTO = {enabled:boolean,toast_enabled:boolean,web_push_enabled:boolean,telegram_enabled:boolean,telegram_always:boolean,resolve_push_enabled:boolean,public_base_url:text[1..2048]? read-only-machine-value,pre_alert_delay_seconds:integer[0..3600],done_settle_seconds:integer[0..3600],escalation_seconds:integer[1..86400],retractability_seconds:integer[0..172800],conversation_mutes:set<UUID>[0..10000],revision:Revision}
NotificationDeliveryDTO = {id:UUID,stage:integer[1..2],channel_id:SchemaKey,device_id:text[1..128]?,state:enum(pending,sending,sent,failed,unknown,retracting,retracted,expired),retractable:boolean,expires_at:Timestamp?,sent_at:Timestamp?,retracted_at:Timestamp?,revision:Revision}
NotificationDTO = {id:UUID,conversation_id:UUID,agent_session_id:UUID?,kind:enum(asking,done),truth_state:enum(true,false,unknown),arm_state:enum(armed,holding,held,disarmed,expired),due_at:Timestamp?,escalation_due_at:Timestamp?,cause:text[1..512],deliveries:list<NotificationDeliveryDTO>[0..20],revision:Revision,created_at:Timestamp,updated_at:Timestamp}
PushSubscriptionDTO = {id:UUID,device_id:text[1..128],endpoint_origin:text[1..512],key_id:UUID,platform:enum(webpush,ios,android,other),created_at:Timestamp,last_success_at:Timestamp?,failure_count:integer[0..1000000],state:enum(active,expired,disabled),revision:Revision}
PushConfigDTO = {available:boolean,key_id:UUID?,public_key:text[1..1024]?,reason:enum(ready,disabled,key_unavailable,unsupported),revision:Revision}
PresenceDTO = {id:UUID,principal_id:UUID,device_id:text[1..128],surface_id:text[1..128],conversation_id:UUID?,viewing_now:boolean,device_active_now:boolean,terminal_tab_focused_now:boolean,started_at:Timestamp,last_heartbeat_at:Timestamp,ended_at:Timestamp?,heartbeat_interval_seconds:integer[1..300],connection_generation:Revision,revision:Revision}
TerminalPresenceDTO = {terminal_binding_id:UUID,agent_session_id:UUID,conversation_id:UUID?,frontmost:boolean,tab_focused:boolean,observed_at:Timestamp,binding_revision:Revision,freshness:Freshness}
UploadDTO = {resource:ResourceDTO,provider_path_token:text[32..2048],digest:Digest,byte_length:integer[0..104857600],media_type:MediaType}
ClipboardFilesDTO = {resources:list<ResourceDTO>[0..100],provider_path_tokens:list<text[32..2048]>[0..100],pasteboard_fingerprint:Digest}
DictationGrantDTO = {grant_token:text[43..2048] response-only-sensitive,provider:SchemaKey,request_url:text[1..4096] response-only-sensitive,model:text[1..200],sample_rate_hz:integer[8000..192000],expires_at:Timestamp,conversation_id:UUID?,project_ref:text[1..512]?,language:text[1..64]?,key_terms:list<text[1..128]>[0..200]}

HandoverDTO = {operation:OperationDTO,source_agent_session_id:UUID,source_node_id:UUID,package_revision:Revision,package_manifest:RegisteredValue,approval_state:enum(not_required,pending,approved,rejected),target_runtime:RuntimeResult?,checkpoints:list<RegisteredValue>[0..100],outcome:enum(pending,succeeded,failed,partial,unknown)}
InvitationDTO = {id:UUID,conversation_id:UUID,conversation_title:text[0..500]?,offered_role:enum(viewer,editor,driver,admin),actor_permissions:set<SchemaKey>[0..64],expires_at:Timestamp,state:enum(pending,accepted,expired,revoked),revision:Revision}
ParticipantDTO = {id:UUID,conversation_id:UUID,principal_id:UUID,role:enum(viewer,editor,driver,admin),actor_permissions:set<SchemaKey>[0..64],expires_at:Timestamp?,revision:Revision}
PeerMessageDTO = {id:UUID,conversation_id:UUID,sender_actor_key:ActorKey,recipient_actor_key:ActorKey?,kind:enum(prose,task_assignment,idle,lifecycle,termination,acknowledgement,extension),body:BlobRef?,task_operation_id:UUID?,source_timestamp:Timestamp?,revision:Revision}
ExtensionDTO = {id:ProviderId,version:text[1..100],manifest_digest:Digest,trust_class:enum(bundled,third_party),state:enum(disabled,starting,active,degraded,crash_loop),capabilities:set<SchemaKey>[0..256],granted_permissions:set<SchemaKey>[0..256],health:HealthPart,revision:Revision}
RepairDTO = {id:UUID,code:SchemaKey,entity:EntityRef?,operator_principal_id:UUID,arguments:RegisteredValue,evidence_ids:list<UUID>[0..1000],reason:text[1..4000],state:enum(pending,running,succeeded,failed,indeterminate),operation_id:UUID,created_at:Timestamp,revision:Revision}
BackupDTO = {id:UUID,label:text[0..200]?,schema_version:integer[1..2147483647],manifest_digest:Digest,state:enum(creating,available,verifying,verified,invalid,restoring,failed),created_at:Timestamp,verified_at:Timestamp?,byte_length:integer[0..9007199254740991],pinned:boolean,revision:Revision}
TelemetryAckDTO = {accepted:integer[0..100],duplicate:integer[0..100],rejected:list<{client_record_id:text[1..128],family:SchemaKey,code:text[1..128]}>[0..100]}
TaskDismissalDTO = {conversation_id:UUID,snapshot_id:UUID,task_set_digest:Digest,preference_revision:Revision,hidden:boolean}
ScoreboardDTO = {delivered_messages:integer[0..9007199254740991]?,read_messages:integer[0..9007199254740991]?,current_unread:integer[0..9007199254740991]?,current_stale:integer[0..9007199254740991]?,message_census_state:enum(observed,sampled,unavailable,unknown),command_count:integer[0..9007199254740991],failed_command_count:integer[0..9007199254740991],active_ms:integer[0..9007199254740991],input_tokens:integer[0..9007199254740991],fresh_input_tokens:integer[0..9007199254740991],output_tokens:integer[0..9007199254740991],cache_read_tokens:integer[0..9007199254740991],cache_create_5m_tokens:integer[0..9007199254740991],cache_create_1h_tokens:integer[0..9007199254740991],cache_create_unclassified_tokens:integer[0..9007199254740991],total_tokens:integer[0..9007199254740991],vendor_cost:map<Currency,integer>[0..32],files_touched:integer[0..9007199254740991],added_lines:integer[0..9007199254740991],removed_lines:integer[0..9007199254740991],tool_count:integer[0..9007199254740991],tool_counts:map<SchemaKey,integer[0..9007199254740991]>[0..256],source_revision:Revision,freshness:Freshness}
MemoryTouchDTO = {canonical_path:text[1..4096],relative_path:text[1..4096],name:text[1..255],verb:enum(read,update,write),actor_track_id:UUID?,count:integer[1..9007199254740991],first_touched_at:Timestamp,last_touched_at:Timestamp}
MemorySearchHitDTO = {rank:integer[0..49],canonical_path:text[1..4096]?,relative_path:text[1..4096]?,name:text[1..255]?,line:integer[1..9007199254740991]?,title:text[0..1000]?,score:number?,snippet:text[0..16384]?,viewable:boolean}
MemorySearchDTO = {id:UUID,kind:enum(search,query),subcommand:text[1..128],query:text[1..4096],rewrites:list<{kind:enum(lex,vec,hyde),text:text[1..4096]}>[0..32],count:integer[1..9007199254740991],answer_state:enum(captured,ambiguous_multi_search,background_unavailable,partial),first_searched_at:Timestamp,last_searched_at:Timestamp,hits:list<MemorySearchHitDTO>[0..50]}
MemoryTreeRowDTO = {row_kind:enum(folder,note),row_key:text[1..4096],depth:integer[0..64],display_path:text[1..4096],folder_path:text[0..4096],note:MemoryTouchDTO?,subtree_note_count:integer[0..100000],subtree_changed_count:integer[0..100000]}
MemoryViewDTO = {agent_session_id:UUID,in_scope:boolean,badge_count:integer[0..100100],touches:list<MemoryTouchDTO>[0..100000],searches:list<MemorySearchDTO>[0..100],tree_rows:list<MemoryTreeRowDTO>[0..100100],revision:Revision,freshness:Freshness}
MemoryNoteDTO = {name:text[1..255],canonical_path:text[1..4096]?,relative_path:text[1..4096]?,frontmatter:RegisteredValue,html:text[0..4194304],backlinks:list<{stem:text[1..255],name:text[1..255],relative_path:text[1..4096],viewable:boolean}>[0..10000],missing:boolean}
```

For schemas that contain `RegisteredValue`, the enclosing field remains closed;
only the catalogued `value` branch varies. Credential import references,
notification channel secret references, `p256dh`, push `auth`, bearer tokens,
CSRF secrets, and provider-readable upload paths are write-only and carry
`writeOnly: true`. They are never serialized in the corresponding DTO.
`DictationGrantDTO.grant_token` is the deliberate exception: it is a sensitive
response field returned exactly once after minting, marked `no-store` in the
response contract, and never persisted or logged in clear form.

The following request schemas are also closed:

```text
EdgeInstallRequest={backend_id:UUID,expected_config_digest:Digest? optional}
EdgeTrustRequest={backend_id:UUID,observed_trust_key:text[1..512]}
EdgeRevertRequest={backend_id:UUID,target_installed_version:text[1..100],expected_config_digest:Digest,reason:text[1..2000]}
BackendCreateRequest={label:text[1..200],adapter_id:SchemaKey,endpoint_config:RegisteredValue,trust_class:enum(local,trusted_remote,untrusted_remote),enabled:boolean}
BackendPatchRequest={label:text[1..200] optional,endpoint_config:RegisteredValue optional,trust_class:enum(local,trusted_remote,untrusted_remote) optional,enabled:boolean optional}
TargetCreateRequest={label:text[1..200],backend_id:UUID,provider_id:ProviderId,default_mode:RuntimeMode,workspace_root_ref:WorkspaceRef? optional,provider_config:RegisteredValue,enabled:boolean}
TargetPatchRequest={label:text[1..200] optional,default_mode:RuntimeMode optional,workspace_root_ref:WorkspaceRef? optional,provider_config:RegisteredValue optional,enabled:boolean optional}
CredentialImport={method:enum(keychain,provider_store,environment,interactive),reference:text[1..2048]}
AccountCreateRequest={provider_id:ProviderId,execution_target_id:UUID,label:text[1..200],credential:CredentialImport,priority:integer[-100000..100000]}
AccountPatchRequest={label:text[1..200] optional,enabled:boolean optional,priority:integer[-100000..100000] optional}
CredentialRotateRequest={credential:CredentialImport}
ConnInfo={daemon_boot_id:UUID?,api_build_id:text[1..128]?,sse_connection_generation:Revision?,transport:enum(loopback,tunnel,remote),online:boolean,page_visibility:enum(visible,hidden,prerender),conversation_id:UUID?}
ControlTelemetryPayload={gesture:SchemaKey,client_attempt_id:text[1..128],phase:enum(begin,ok,fail),http_request_id:text[1..128]? optional,http_status:integer[100..599]? optional,error_code:text[1..128]? optional,elapsed_ms:integer[0..3600000]? optional}
SseTelemetryPayload={scope:enum(machine,principal,conversation),endpoint:text[1..2048],last_event_id:text[0..512]? optional,reconnect_attempt:integer[0..1000000],close_kind:enum(opened,network,error,server_close,client_close,upgrade_required),http_status:integer[100..599]? optional}
JsTelemetryPayload={name:text[1..256],message:text[0..4000],stack:text[0..16000]? optional,source_url:text[0..2048]? optional,line:integer[0..10000000]? optional,column:integer[0..10000000]? optional,reducer_event:text[0..256]? optional}
BootTelemetryPayload={page_url:text[1..2048],referrer:text[0..2048],asset_build_id:text[1..128],service_worker_build_id:text[1..128]? optional,launch_source:enum(browser,pwa,new_shortcut,attention_shortcut,push,unknown)}
NotificationReceiptTelemetryPayload={notification_id:UUID,delivery_id:UUID? optional,channel:enum(toast,web_push,telegram,unknown),shown:boolean,reason:text[0..256]? optional,permission:enum(granted,denied,default,unsupported,unknown)}
AttachmentPasteTelemetryPayload={file_count:integer[0..100],basenames:list<text[1..255]>[0..100],clipboard_kind:enum(files,text,mixed,unknown),resolution_attempted:boolean,result:enum(resolved,upload_fallback,rejected,failed,not_attempted),error_code:text[1..128]? optional}
ClientTelemetryRecord=discriminatedOneOf<
  {family:control,event_name:enum(control.begin,control.ok,control.fail),payload:ControlTelemetryPayload},
  {family:sse_lifecycle,event_name:enum(sse.open,sse.drop),payload:SseTelemetryPayload},
  {family:js_error,event_name:js.error,payload:JsTelemetryPayload},
  {family:js_rejection,event_name:js.reject,payload:JsTelemetryPayload},
  {family:boot,event_name:boot,payload:BootTelemetryPayload},
  {family:notification_receipt,event_name:notify.recv,payload:NotificationReceiptTelemetryPayload},
  {family:attachment_paste,event_name:attach.paste,payload:AttachmentPasteTelemetryPayload}
> plus {client_record_id:text[1..128],device_id:text[1..128],surface_id:text[1..128],conversation_id:UUID? optional,agent_session_id:UUID? optional,client_timestamp:Timestamp,conn_info:ConnInfo}
ClientTelemetryRequest={records:list<ClientTelemetryRecord>[1..100]}

ConversationCreateRequest={title:text[0..500]? optional,project_ref:text[1..512]? optional,workspace:{execution_target_id:UUID,workspace_ref:WorkspaceRef}? optional}
ConversationPatchRequest={title:text[0..500]? optional,project_ref:text[1..512]? optional,archive_preference:boolean optional}
ArchiveRequest={force_abort_background:boolean}
StartSessionRequest={runtime:RuntimeRequest,from_node_id:UUID,actor_key:ActorKey? optional}
ViewModePutRequest={view_mode:enum(verbose,default,focus),expected_revision:Revision,client_mutation_id:text[1..128]}
ResourceAttachmentRequest={resource_id:UUID,provider_path_token:text[32..2048] write-only}
MessageRequest={agent_session_id:UUID,text:text[1..1000000],resources:list<ResourceAttachmentRequest>[0..100],tui_draft_revision:Revision? optional,client_message_id:text[1..128],parked_policy:enum(reject,resume),runtime:RuntimeRequest? required-iff-parked_policy-resume}
TaskDismissRequest={snapshot_id:UUID,sorted_task_ids:list<text[1..256]>[1..10000],task_set_digest:Digest,expected_preference_revision:Revision}
MemoryNoteQuery=oneOf<{path:text[1..4096]},{stem:text[1..255]}> exactly one branch
ForkRequest={from_node_id:UUID,runtime:RuntimeRequest,title:text[0..500]? optional}
RewindRequest={agent_session_id:UUID,target_node_id:UUID,mode:enum(conversation,workspace,both),restore_draft:boolean}
HandoverCreateRequest={source_agent_session_id:UUID,source_node_id:UUID,runtime:RuntimeRequest,context_budget_tokens:integer[1..10000000],include_resource_ids:set<UUID>[0..1000],exclude_resource_ids:set<UUID>[0..1000],workspace_policy:enum(same,transfer,none),approval_policy:enum(always,if_resources,never)}
CollaboratorCreateRequest={principal_id:UUID? optional,invitation:boolean,role:enum(viewer,editor,driver,admin),actor_permissions:set<SchemaKey>[0..64],expires_at:Timestamp? optional}

ResumeRequest={runtime:RuntimeRequest,from_node_id:UUID? optional,continue_interrupted_turn:boolean}
InterruptRequest={take_back_queued_message:boolean}
CompactRequest={instructions:text[0..100000]? optional}
CloseRequest={park:boolean,force:boolean}
RenameRequest={title:text[0..500]? optional,mode:enum(auto,explicit)}
SetRuntimeRequest={model:text[1..200],effort:text[1..100],runtime_options_revision:Revision}
MigrateAccountRequest={target_account_id:UUID,model:text[1..200]? optional,effort:text[1..100]? optional,mode:RuntimeMode? optional,execution_target_id:UUID? optional,runtime_options_revision:Revision}
CancelRequest={reason:text[1..2000]}
InteractionResponseRequest={response_revision:Revision,answers:RegisteredValue,verdict:enum(approved,changes,rejected,confirmed,denied,answered,dismissed)? optional,edited:boolean? optional,feedback:text[0..100000]? optional}

InputBufferPutRequest={text:text[0..1000000],kind:enum(composer,new_session,interaction)? required-on-create,conversation_id:UUID? required-iff-kind-composer,interaction_id:UUID? required-iff-kind-interaction,project_ref:text[1..512]? required-iff-kind-new_session,expected_revision:Revision,author_sequence:integer[0..9007199254740991],origin:enum(surface,device,terminal)}
DeleteCASRequest={expected_revision:Revision,author_sequence:integer[0..9007199254740991]}
PreferencePutRequest={schema_version:integer[1..2147483647],value:RegisteredValue,expected_revision:Revision,author_sequence:integer[0..9007199254740991]}
GroupHideRequest={expected_revision:Revision}
PaneTargetRequest={conversation_id:UUID? optional,agent_session_id:UUID? optional}
PanePercentageRequest={conversation_id:UUID? optional,agent_session_id:UUID? optional,percentage:integer[10..90]}
NotificationSettingsPutRequest={enabled:boolean,toast_enabled:boolean,web_push_enabled:boolean,telegram_enabled:boolean,telegram_always:boolean,resolve_push_enabled:boolean,pre_alert_delay_seconds:integer[0..3600],done_settle_seconds:integer[0..3600],escalation_seconds:integer[1..86400],retractability_seconds:integer[0..172800],conversation_mutes:set<UUID>[0..10000],channel_secret_refs:map<SchemaKey,text[1..2048]>[0..16],expected_revision:Revision}
NotificationReactRequest={reaction:enum(viewing,answered,dismissed)}
PushSubscriptionCreateRequest={device_id:text[1..128],endpoint:text[1..4096],p256dh:text[1..1024],auth:text[1..1024],key_id:UUID,platform:enum(webpush,ios,android,other),user_agent:text[0..1000]? optional}
PushKeyRotateRequest={expected_active_key_id:UUID,confirm_orphan_count:integer[0..10000000]}
DictationGrantRequest={device_id:text[1..128],conversation_id:UUID? optional,project_ref:text[1..512]? optional,language:text[1..64]? optional,key_terms:list<text[1..128]>[0..200]}
ClipboardFilesRequest={basenames:list<text[1..255]>[1..100]}
PresenceCreateRequest={device_id:text[1..128],surface_id:text[1..128],conversation_id:UUID? optional,visibility:enum(visible,hidden),focused:boolean}
PresencePutRequest={conversation_id:UUID? optional,visibility:enum(visible,hidden),focused:boolean,device_active:boolean,expected_revision:Revision}
PresenceAwayRequest={expected_revision:Revision}
TerminalPresencePutRequest={frontmost:boolean,tab_focused:boolean,conversation_id:UUID? optional,binding_revision:Revision}

HandoverApproveRequest={package_revision:Revision,allowed_resource_ids:set<UUID>[0..1000]}
HandoverRetryRequest={step:SchemaKey,reason:text[1..2000]}
InvitationAcceptRequest={device_public_key:text[32..4096]}
PeerMessageRequest={sender_actor_key:ActorKey,recipient_actor_key:ActorKey? optional,kind:enum(prose,task_assignment,idle,lifecycle,termination,acknowledgement,extension),body:text[0..1000000]? optional,task_operation_id:UUID? optional}
ExtensionEnableRequest={manifest_digest:Digest,granted_capabilities:set<SchemaKey>[0..256]}
ExtensionDisableRequest={reason:text[1..2000]}
RepairCreateRequest={code:SchemaKey,arguments:RegisteredValue,evidence_ids:list<UUID>[1..1000],reason:text[1..4000]}
BackupCreateRequest={label:text[0..200]? optional}
BackupRestoreRequest={expected_current_schema_version:integer[1..2147483647],verified_manifest_digest:Digest,confirmation:text matching ^RESTORE [0-9a-f-]{36}$}
```

#### Error, query, pagination, and response profiles

Every operation has the universal errors `400 invalid_request|unknown_field`,
`401 authentication_required|credential_expired|credential_revoked`,
`403 insufficient_scope|csrf_failed`, `413 request_too_large`,
`429 rate_limited`, `500 internal_error`, and
`503 daemon_draining|storage_unavailable`. A row's `Errors` cell adds the only
other codes that operation may return; unlisted domain failures are mapped to
`500 contract_violation` and treated as a defect. `IM` adds
`428 revision_required` and `412 revision_conflict`; `CAS` adds the named 409
revision error in the row. `IK` adds `409 idempotency_mismatch` and
`409 effect_authorization_changed`. Byte operations additionally declare 206,
410, and 416 explicitly. A 204 response has no body or content type.

The following closed query schemas are used by the manifest. All optional
parameters default to absent. `limit` defaults to 50. Repeated set parameters
are encoded as comma-separated, percent-decoded tokens with no empty token.

```text
QNone={}
QPage={limit:integer[1..200]?,cursor:Cursor?}
QProviders={execution_target_id:UUID?,limit:integer[1..200]?,cursor:Cursor?}
QRuntime={execution_target_id:UUID,account_id:UUID?,mode:RuntimeMode?}
QVocabulary={execution_target_id:UUID,workspace_ref:WorkspaceRef,actor_key:ActorKey?}
QEdges={provider_id:ProviderId?,state:text[1..64]?,limit:integer[1..200]?,cursor:Cursor?}
QAccounts={provider_id:ProviderId?,execution_target_id:UUID?,limit:integer[1..200]?,cursor:Cursor?}
QQuota={account_id:UUID?,provider_id:ProviderId?,limit:integer[1..200]?,cursor:Cursor?}
QUsage={from:Timestamp,through:Timestamp,conversation_id:UUID?,account_id:UUID?,provider_id:ProviderId?,model:text[1..200]?,query_source:enum(main,subagent,auxiliary)?,ledger:enum(billing,per_actor_display,quota)?,group_by:set<enum(conversation,account,provider,model,query_source,day)>[0..6]?}
QStats={from:Timestamp,through:Timestamp,project_ref:text[1..512]?,provider_id:ProviderId?,model:text[1..200]?}
QAnomalies={code:text[1..128]?,severity:enum(info,warning,error,critical)?,conversation_id:UUID?,limit:integer[1..200]?,cursor:Cursor?}
QGaps={from:Timestamp,through:Timestamp,limit:integer[1..200]?,cursor:Cursor?}
QEvents={last_event_id:Cursor? header,presence_session_id:UUID? header,presence_connection_generation:Revision? header}
QConversations={project_ref:text[1..512]?,provider_id:ProviderId?,account_id:UUID?,state:set<text[1..64]>[0..16]?,attention:set<text[1..64]>[0..16]?,search:text[1..500]?,sort:enum(updated_desc,created_desc,active_time_desc) default updated_desc,limit:integer[1..200]?,cursor:Cursor?}
QConversation={actor_key:ActorKey?}
QTracks={state:set<enum(active,idle,ended,lost)>[0..4]?,kind:set<enum(lead,subagent,teammate,sidecar,peer)>[0..5]?,limit:integer[1..200]?,cursor:Cursor?}
QNodes={actor_key:ActorKey,head_node_id:UUID?,limit:integer[1..200]?,cursor:Cursor?}
QOperations={actor_key:ActorKey?,kind:set<SchemaKey>[0..64]?,state:set<text[1..64]>[0..16]?,turn_key:text[1..256]?,task_key:text[1..256]?,limit:integer[1..200]?,cursor:Cursor?}
QActivity={actor_scope:text[1..256],activity_class:set<SchemaKey>[0..64]?,register:set<text[1..64]>[0..16]?,audience:set<text[1..64]>[0..4]?,limit:integer[1..200]?,cursor:Cursor?}
QStreamContent={from_revision:Revision,range:text? header}
QStreamCopy={kind:enum(visible,raw),range:text? header}
QResource={version:UUID?}
QResourceContent={version:UUID?,range:text? header}
QInputBuffers={kind:enum(composer,new_session,interaction),conversation_id:UUID?,interaction_id:UUID?,project_ref:text[1..512]?,author_id:UUID?}
QPreferences={namespace:SchemaKey?,scope_type:enum(principal,device,conversation,project,global)?,scope_id:text[1..512]?,key:SchemaKey?,limit:integer[1..200]?,cursor:Cursor?}
QNotifications={state:set<text[1..64]>[0..16]?,kind:set<enum(asking,done)>[0..2]?,conversation_id:UUID?,limit:integer[1..200]?,cursor:Cursor?}
QPushSubscriptions={device_id:text[1..128]?,limit:integer[1..200]?,cursor:Cursor?}
QRepairs={entity_type:SchemaKey?,entity_id:UUID?,code:SchemaKey?,operator_id:UUID?,limit:integer[1..200]?,cursor:Cursor?}
QBackups={limit:integer[1..200]?,cursor:Cursor?}
```

Fixed list order is: provider/backend/target/account/edge/extension lists by
`(label ASC,id ASC)`; quota by `(account_id,provider_id,scope_key,model_scope,
window_minutes)`; anomalies and gaps by `(detected_or_started_at DESC,id DESC)`;
Conversations by the requested sort plus `id DESC`; tracks by
`(created_at ASC,id ASC)`; Nodes and Activity are backward by their semantic
page key; Operations/notifications/repairs/backups are
`(created_at DESC,id DESC)`; input buffers/preferences/subscriptions are by
their complete primary-key tuple. Usage and stats are not cursor-paged and cap
at 10,000 buckets, otherwise `422 result_too_large` asks for a narrower range.

`QConversations.search` is normalized Unicode case-folded substring search over
the complete authorized history's title and provider-native session IDs. It is
performed server-side before limit/cursor slicing. Each result includes the
provider/tool and complete escaped resume command so the resume picker never
guesses between `claude --resume`, `codex resume`, or an extension command.

#### Complete 115-endpoint manifest and endpoint traceability

`Trace` is `ApplicationOwner.method / StoragePort.method / durable owners /
structural event`. A trailing `-` means the endpoint emits no structural event.
Every mutation trace includes the transactional outbox even when abbreviated.
Every row requires `contract_http_<operationId>`, authorization allow/deny,
unknown-field, error-schema, and OpenAPI snapshot tests; `IM`, `CAS`, `IK`,
pagination, and byte rows additionally require their respective standard
contract suites. `GET` rows read one SQLite snapshot. Mutation store methods
ending `_tx` join one short write transaction and never commit themselves.

##### Machine, provider, backend, account, and diagnostics operations

| operationId | Method and path | Auth / concurrency | Query or body -> success | Errors | Trace |
|---|---|---|---|---|---|
| `getHealth` | `GET /api/v1/health` | `MR` | `QNone -> 200 HealthDTO` | `503 reads_not_safe` | `DiagnosticService.health / DiagnosticStore.read_health / health_errors,ingestion_gaps / -` |
| `getLimits` | `GET /api/v1/limits` | `MR` | `QNone -> 200 LimitsDTO` (`upload_max`, `rename_max`, `view_ttl_s`, `presence_heartbeat_s`) | `503 reads_not_safe` | `DiagnosticService.limits / DiagnosticStore.read_limits / runtime_limits / -` |
| `listProviders` | `GET /api/v1/providers` | `MR` | `QProviders -> 200 Page<ProviderDTO>` | - | `ProviderCatalog.list / ProviderStore.list / provider_plugins,provider_edge_installations / -` |
| `getRuntimeOptions` | `GET /api/v1/providers/{provider_id}/runtime-options` | `MR` | `QRuntime -> 200 RuntimeOptionsDTO` | `404 provider_not_found,target_not_found` | `RuntimeOptionService.get / RuntimeOptionStore.read / execution_targets,accounts,quota_windows / -` |
| `getCommandVocabulary` | `GET /api/v1/providers/{provider_id}/command-vocabulary` | `CR` when actor workspace belongs to Conversation, otherwise `MR` | `QVocabulary -> 200 CommandVocabularyDTO` | `404 provider_not_found,target_not_found`, `403 workspace_forbidden` | `CommandVocabularyService.get / VocabularyStore.read / command_vocabulary_snapshots / -` |
| `listProviderEdges` | `GET /api/v1/provider-edges` | `MR` | `QEdges -> 200 Page<ProviderEdgeDTO>` | - | `ProviderEdgeManager.list / ProviderEdgeStore.list / provider_edge_installations / -` |
| `installProviderEdge` | `POST /api/v1/provider-edges/{provider_id}:install` | `MA IK` | `EdgeInstallRequest -> 202 OperationAccepted` | `404 provider_not_found,backend_not_found`, `409 delegating_hook_present,provider_running_requires_review` | `ProviderEdgeManager.install / ProviderEdgeStore.plan_install_tx / provider_edge_installations,operations,outbox / provider.edge.changed` |
| `verifyProviderEdgeTrust` | `POST /api/v1/provider-edges/{provider_id}:verify-trust` | `MA IK` | `EdgeTrustRequest -> 200 ProviderEdgeDTO` | `404 provider_not_found,backend_not_found`, `409 trust_not_granted,edge_not_installed` | `ProviderEdgeManager.verify_trust / ProviderEdgeStore.record_trust_tx / provider_edge_installations,provenance / provider.edge.changed` |
| `revertProviderEdge` | `POST /api/v1/provider-edges/{provider_id}:revert` | `MA IK` | `EdgeRevertRequest -> 202 OperationAccepted` | `404 provider_not_found,backend_not_found`, `409 backup_unavailable,provider_edge_changed` | `ProviderEdgeManager.revert / ProviderEdgeStore.plan_revert_tx / provider_edge_installations,operations,outbox / provider.edge.changed` |
| `listBackends` | `GET /api/v1/backends` | `MR` | `QPage -> 200 Page<BackendDTO>` | - | `BackendService.list / BackendStore.list / backends,backend_health / -` |
| `createBackend` | `POST /api/v1/backends` | `MA` | `BackendCreateRequest -> 201 BackendDTO` | `409 backend_label_exists`, `422 config_schema_invalid` | `BackendService.create / BackendStore.create_tx / backends / backend.changed` |
| `patchBackend` | `PATCH /api/v1/backends/{id}` | `MA IM` | `BackendPatchRequest -> 200 BackendDTO` | `404 backend_not_found`, `409 backend_in_use`, `422 config_schema_invalid` | `BackendService.patch / BackendStore.patch_tx / backends / backend.changed` |
| `deleteBackend` | `DELETE /api/v1/backends/{id}` | `MA IM` | `- -> 204` | `404 backend_not_found`, `409 backend_in_use` | `BackendService.delete / BackendStore.delete_tx / backends / backend.changed(delete)` |
| `listExecutionTargets` | `GET /api/v1/execution-targets` | `MR` | `QPage -> 200 Page<ExecutionTargetDTO>` | - | `ExecutionTargetService.list / ExecutionTargetStore.list / execution_targets,backend_health / -` |
| `createExecutionTarget` | `POST /api/v1/execution-targets` | `MA` | `TargetCreateRequest -> 201 ExecutionTargetDTO` | `404 backend_not_found,provider_not_found`, `409 target_label_exists`, `422 config_schema_invalid` | `ExecutionTargetService.create / ExecutionTargetStore.create_tx / execution_targets / execution_target.changed` |
| `patchExecutionTarget` | `PATCH /api/v1/execution-targets/{id}` | `MA IM` | `TargetPatchRequest -> 200 ExecutionTargetDTO` | `404 target_not_found`, `409 target_in_use`, `422 config_schema_invalid` | `ExecutionTargetService.patch / ExecutionTargetStore.patch_tx / execution_targets / execution_target.changed` |
| `deleteExecutionTarget` | `DELETE /api/v1/execution-targets/{id}` | `MA IM` | `- -> 204` | `404 target_not_found`, `409 target_in_use` | `ExecutionTargetService.delete / ExecutionTargetStore.delete_tx / execution_targets / execution_target.changed(delete)` |
| `probeExecutionTarget` | `POST /api/v1/execution-targets/{id}:probe` | `MA IK` | `- -> 202 OperationAccepted` | `404 target_not_found`, `409 probe_already_running` | `ExecutionTargetService.probe / ExecutionTargetStore.plan_probe_tx / operations,outbox,backend_health / execution_target.changed` |
| `listAccounts` | `GET /api/v1/accounts` | `AR` | `QAccounts -> 200 Page<AccountDTO>` | - | `AccountService.list / AccountStore.list / accounts,quota_windows / -` |
| `createAccount` | `POST /api/v1/accounts` | `AW` | `AccountCreateRequest -> 201 AccountDTO` | `404 provider_not_found,target_not_found`, `409 account_label_exists`, `422 credential_import_invalid` | `AccountService.create / AccountStore.create_tx / accounts,credential_references / account.changed` |
| `patchAccount` | `PATCH /api/v1/accounts/{id}` | `AW IM` | `AccountPatchRequest -> 200 AccountDTO` | `404 account_not_found` | `AccountService.patch / AccountStore.patch_tx / accounts / account.changed` |
| `rotateAccountCredential` | `POST /api/v1/accounts/{id}:rotate-credential` | `AW IK` | `CredentialRotateRequest -> 202 OperationAccepted` | `404 account_not_found`, `409 credential_rotation_running`, `422 credential_import_invalid` | `AccountService.rotate_credential / AccountStore.plan_credential_rotation_tx / accounts,operations,outbox / account.changed` |
| `listQuotaWindows` | `GET /api/v1/quota-windows` | `AR` | `QQuota -> 200 Page<QuotaWindowDTO>` | - | `QuotaService.list / QuotaStore.list / quota_windows / -` |
| `getUsage` | `GET /api/v1/usage` | `AR`, `CR` when conversation filter supplied | `QUsage -> 200 UsageDTO` | `404 conversation_not_found,account_not_found`, `422 invalid_range,result_too_large` | `UsageAccountingService.query_rollups / UsageStore.read_rollups / usage_source_authority,usage_source_rollups,daily_usage_source_rollups / -` |
| `getStats` | `GET /api/v1/stats` | `SELF-R` | `QStats -> 200 StatsDTO` | `422 invalid_range,result_too_large` | `InsightService.query / InsightStore.read_stats / daily_insight_rollups,daily_usage_source_rollups,agent_session_scoreboards,health_errors / -` |
| `listAnomalies` | `GET /api/v1/anomalies` | `MR` | `QAnomalies -> 200 Page<AnomalyDTO>` | `404 anomaly_code_not_found` | `DiagnosticService.run_anomalies / DiagnosticStore.list_anomalies / anomaly_results,evidence / -` |
| `listIngestionGaps` | `GET /api/v1/ingestion-gaps` | `MR` | `QGaps -> 200 Page<IngestionGapDTO>` | `422 invalid_range` | `DiagnosticService.list_gaps / DiagnosticStore.list_gaps / ingestion_gaps / -` |
| `recordClientTelemetry` | `POST /api/v1/client-telemetry` | `SURF IK` | `ClientTelemetryRequest -> 202 TelemetryAckDTO` | `422 record_principal_mismatch,unknown_telemetry_event,telemetry_payload_invalid` | `DiagnosticService.record_client_telemetry / ClientTelemetryStore.record_batch_tx / surface_control_attempts,surface_telemetry / -` |
| `streamSystemEvents` | `GET /api/v1/system/events` | `MA` | `QEvents -> 200 text/event-stream` | `409 feed_cursor_expired,feed_cursor_scope_mismatch` | `StructuralFeedService.subscribe_machine / StructuralFeedStore.replay_after / structural_changes / registered machine events` |
| `streamPrincipalEvents` | `GET /api/v1/events` | `SELF-R` | `QEvents -> 200 text/event-stream` | `409 feed_cursor_expired,feed_cursor_scope_mismatch,presence_generation_conflict` | `StructuralFeedService.subscribe_principal / StructuralFeedStore.replay_after / structural_changes,presence_sessions / registered principal events` |

##### Conversation and actor-view operations

| operationId | Method and path | Auth / concurrency | Query or body -> success | Errors | Trace |
|---|---|---|---|---|---|
| `listConversations` | `GET /api/v1/conversations` | `SELF-R` | `QConversations -> 200 Snapshot<Page<ConversationOverviewDTO>>` | - | `ConversationQueryService.list / ConversationStore.list_overviews / conversation_overviews,conversations,conversation_actor_tracks,attention_projection,agent_session_active_time / -` |
| `createConversation` | `POST /api/v1/conversations` | `SELF-W` | `ConversationCreateRequest -> 201 ConversationDTO` | `404 target_not_found`, `409 workspace_already_primary` | `ConversationService.create / ConversationStore.create_tx / conversations,conversation_actor_tracks,conversation_workspaces,provenance / conversation.overview.changed` |
| `getConversation` | `GET /api/v1/conversations/{id}` | `CR` | `QConversation -> 200 Snapshot<ConversationViewDTO>` | `404 conversation_not_found,actor_not_found` | `ConversationQueryService.snapshot / ConversationStore.read_snapshot / conversation aggregate and active projections / -` |
| `patchConversation` | `PATCH /api/v1/conversations/{id}` | `CW IM` | `ConversationPatchRequest -> 200 ConversationDTO` | `404 conversation_not_found`, `409 live_title_owned_by_provider` | `ConversationService.patch / ConversationStore.patch_tx / conversations,conversation_title_facts,preferences / conversation.changed` |
| `archiveConversation` | `POST /api/v1/conversations/{id}:archive` | `CW IM IK` | `ArchiveRequest -> 202 OperationAccepted` | `404 conversation_not_found`, `409 background_work_active,archive_running` | `ConversationService.archive / ConversationStore.plan_archive_tx / conversations,operations,outbox,source_registrations / conversation.changed` |
| `unarchiveConversation` | `POST /api/v1/conversations/{id}:unarchive` | `CW IM` | `- -> 200 ConversationDTO` | `404 conversation_not_found`, `409 conversation_not_archived` | `ConversationService.unarchive / ConversationStore.unarchive_tx / conversations / conversation.changed` |
| `listActorTracks` | `GET /api/v1/conversations/{id}/actor-tracks` | `CR` | `QTracks -> 200 Page<ActorTrackDTO>` | `404 conversation_not_found` | `ActorTrackService.list / ActorTrackStore.list_tracks / conversation_actor_tracks / -` |
| `listNodes` | `GET /api/v1/conversations/{id}/nodes` | `CR` | `QNodes -> 200 Page<NodeDTO>` | `404 conversation_not_found,actor_not_found,head_not_found`, `409 cursor_branch_changed` | `ActorTrackService.list_nodes / ActorTrackStore.list_ancestry / nodes,node_parts,conversation_actor_tracks / -` |
| `listConversationOperations` | `GET /api/v1/conversations/{id}/operations` | `CR` | `QOperations -> 200 Page<OperationDTO>` | `404 conversation_not_found,actor_not_found` | `OperationQueryService.list / OperationStore.list / operations,operation_details / -` |
| `getConversationActivity` | `GET /api/v1/conversations/{id}/activity` | `CR` | `QActivity -> 200 Snapshot<ActivityPageDTO>` | `404 conversation_not_found,actor_not_found`, `409 cursor_generation_changed` | `ActivityQueryService.page / ActivityStore.read_page / activity_projection_state,materialized_activity / -` |
| `dismissCompletedTasks` | `POST /api/v1/conversations/{id}/tasks:dismiss` | `CW CAS IK` | `TaskDismissRequest -> 200 TaskDismissalDTO` | `404 conversation_not_found,snapshot_not_found`, `409 tasks_not_done,task_snapshot_changed,preference_revision_conflict` | `TaskFacetService.dismiss_completed / TaskPreferenceStore.dismiss_completed_tx / provider_task_snapshots,provider_tasks,preferences / conversation.changed` |
| `streamConversationEvents` | `GET /api/v1/conversations/{id}/events` | `CR` | `QEvents -> 200 text/event-stream` | `404 conversation_not_found`, `409 feed_cursor_expired,feed_cursor_scope_mismatch,presence_generation_conflict` | `StructuralFeedService.subscribe_conversation / StructuralFeedStore.replay_after / structural_changes,presence_sessions / registered Conversation events` |
| `startAgentSession` | `POST /api/v1/conversations/{id}/agent-sessions` | `CD IM IK` | `StartSessionRequest -> 202 OperationAccepted` | `404 conversation_not_found,node_not_found,actor_not_found`, `409 runtime_options_changed,interaction_owns_input,session_start_running`, `422 runtime_unavailable` | `AgentSessionService.start / AgentSessionStore.plan_start_tx / agent_sessions,agent_session_runtime_revisions,operations,outbox / operation.changed,conversation.changed` |
| `putAgentSessionViewMode` | `PUT /api/v1/agent-sessions/{id}/view-mode` | `CW CAS IK` | `ViewModePutRequest -> 200 AgentSessionDTO` | `404 session_not_found`, `409 view_mode_revision_conflict` | `ViewModeService.put / ViewModeStore.put_tx / agent_session_view_preferences,structural_changes,outbox / view-mode.changed` |
| `sendConversationMessage` | `POST /api/v1/conversations/{id}/messages` | `CD IM IK` | `MessageRequest -> 202 OperationAccepted` | `404 conversation_not_found,session_not_found,resource_not_found`, `409 session_not_live,runtime_options_changed,interaction_owns_input,draft_conflict,delivery_already_active`, `410 session_artifact_gone,resource_content_expired`, `503 terminal_unavailable` | `MessageDeliveryService.send / MessageDeliveryStore.plan_send_tx / operations,message_delivery_details,tui_drafts,input_occupancy,outbox / operation.changed` |
| `forkConversation` | `POST /api/v1/conversations/{id}/forks` | `CD IM IK` | `ForkRequest -> 202 OperationAccepted` | `404 conversation_not_found,node_not_found`, `409 runtime_options_changed,fork_running`, `422 runtime_unavailable` | `AgentSessionService.fork / AgentSessionStore.plan_fork_tx / operations,agent_sessions,runtime_revisions,outbox / operation.changed,conversation.changed` |
| `rewindConversation` | `POST /api/v1/conversations/{id}/rewinds` | `CD IM IK` | `RewindRequest -> 202 OperationAccepted` | `404 conversation_not_found,session_not_found,node_not_found`, `409 interaction_owns_input,rewind_running,draft_clear_unverified`, `410 session_artifact_gone`, `422 mode_unsupported` | `RewindService.request / RewindStore.plan_tx / operations,rewind_details,input_occupancy,outbox / operation.changed,conversation.changed` |
| `createHandover` | `POST /api/v1/conversations/{id}/handovers` | `CD IM IK` | `HandoverCreateRequest -> 202 OperationAccepted` | `404 conversation_not_found,session_not_found,node_not_found,resource_not_found`, `409 runtime_options_changed,handover_running`, `410 resource_content_expired`, `422 budget_impossible,runtime_unavailable` | `HandoverService.start / HandoverStore.plan_tx / operations,handover_details,workflow_checkpoints,outbox / operation.changed` |
| `createCollaborator` | `POST /api/v1/conversations/{id}/collaborators` | `CA IM` | `CollaboratorCreateRequest -> 201 oneOf<ParticipantDTO,InvitationDTO>` | `404 conversation_not_found,principal_not_found`, `409 participant_exists`, `422 invalid_expiry,invalid_actor_permission` | `CollaborationService.add / CollaborationStore.add_tx / participants,invitations,role_bindings / conversation.changed` |
| `deleteCollaborator` | `DELETE /api/v1/conversations/{id}/collaborators/{participant_id}` | `CA IM` | `- -> 204` | `404 conversation_not_found,participant_not_found`, `409 last_admin` | `CollaborationService.remove / CollaborationStore.remove_tx / participants,role_bindings,auth_revision / conversation.changed` |

##### AgentSession, control, Operation, and interaction operations

| operationId | Method and path | Auth / concurrency | Query or body -> success | Errors | Trace |
|---|---|---|---|---|---|
| `getAgentSession` | `GET /api/v1/agent-sessions/{id}` | `CR` through owning Conversation | `QNone -> 200 AgentSessionDTO` | `404 session_not_found` | `AgentSessionQueryService.get / AgentSessionStore.get / agent_sessions,attempts,aliases,artifacts,runtime_revisions,context,lifecycle / -` |
| `getAgentSessionMemory` | `GET /api/v1/agent-sessions/{id}/memory` | `CR` through owning Conversation | `QNone -> 200 MemoryViewDTO` | `404 session_not_found` | `MemoryQueryService.snapshot / MemoryStore.read_snapshot / agent_sessions,memory_touches,memory_searches,memory_search_hits,blob_objects / -` |
| `getAgentSessionMemoryNote` | `GET /api/v1/agent-sessions/{id}/memory/note` | `CR` through owning Conversation | `MemoryNoteQuery -> 200 MemoryNoteDTO` | `403 memory_off_scope`, `404 session_not_found`, `422 note_selector_invalid` | `MemoryQueryService.note / MemoryStore.read_scope plus MemoryVaultPort.read_note / agent_sessions,agent_session_artifacts / -` |
| `resumeAgentSession` | `POST /api/v1/agent-sessions/{id}:resume` | `CD IM IK` on session revision | `ResumeRequest -> 202 OperationAccepted` | `404 session_not_found,node_not_found`, `409 runtime_options_changed,interaction_owns_input,resume_running`, `410 session_artifact_gone`, `422 runtime_unavailable` | `AgentSessionService.resume / AgentSessionStore.plan_resume_tx / operations,attempts,runtime_revisions,outbox / operation.changed,conversation.changed` |
| `interruptAgentSession` | `POST /api/v1/agent-sessions/{id}:interrupt` | `CD IM IK` | `InterruptRequest -> 202 OperationAccepted` | `404 session_not_found`, `409 interaction_owns_input,interrupt_not_reachable,interrupt_running`, `503 terminal_unavailable` | `ControlService.interrupt / ControlStore.plan_interrupt_tx / operations,control_details,outbox / operation.changed` |
| `compactAgentSession` | `POST /api/v1/agent-sessions/{id}:compact` | `CD IM IK` | `CompactRequest -> 202 OperationAccepted` | `404 session_not_found`, `409 interaction_owns_input,compaction_running`, `410 session_artifact_gone`, `422 compact_unsupported` | `ControlService.compact / ControlStore.plan_compact_tx / operations,compaction_details,outbox / operation.changed` |
| `closeAgentSession` | `POST /api/v1/agent-sessions/{id}:close` | `CD IM IK` | `CloseRequest -> 202 OperationAccepted` | `404 session_not_found`, `409 close_running,background_work_active` | `AgentSessionService.close / AgentSessionStore.plan_close_tx / operations,agent_session_lifecycle,outbox / operation.changed,conversation.changed` |
| `renameAgentSession` | `POST /api/v1/agent-sessions/{id}:rename` | `CD IM IK` | `RenameRequest -> 202 OperationAccepted` | `404 session_not_found`, `409 rename_running,live_title_owned_by_provider`, `410 session_artifact_gone` | `SessionFacetService.rename / SessionFacetStore.plan_title_change_tx / operations,conversation_title_revisions,outbox / operation.changed,conversation.changed` |
| `setAgentSessionRuntime` | `POST /api/v1/agent-sessions/{id}:set-runtime` | `CD IM IK` | `SetRuntimeRequest -> 202 OperationAccepted` | `404 session_not_found`, `409 runtime_options_changed,interaction_owns_input,runtime_change_running`, `422 runtime_unavailable` | `AgentSessionService.set_runtime / AgentSessionStore.plan_runtime_change_tx / operations,agent_session_runtime_revisions,outbox / operation.changed` |
| `migrateAgentSessionAccount` | `POST /api/v1/agent-sessions/{id}:migrate-account` | `CD IM IK` | `MigrateAccountRequest -> 202 OperationAccepted` | `404 session_not_found,account_not_found,target_not_found`, `409 runtime_options_changed,migration_running,account_limited,account_logged_out`, `410 session_artifact_gone`, `422 runtime_unavailable` | `AccountMigrationService.start_manual / MigrationStore.plan_manual_tx / operations,migration_details,attempts,outbox / operation.changed,account.changed` |
| `getOperation` | `GET /api/v1/operations/{id}` | `CR` through owning Conversation | `QNone -> 200 {operation:OperationDTO,attempts:list<AttemptDTO>[0..1000]}` | `404 operation_not_found` | `OperationQueryService.get / OperationStore.get / operations,operation_details,effect_attempts / -` |
| `cancelOperation` | `POST /api/v1/operations/{id}:cancel` | `CD IM IK` | `CancelRequest -> 202 OperationAccepted` | `404 operation_not_found`, `409 cancel_unsupported,operation_terminal,cancel_running` | `OperationService.cancel / OperationStore.plan_cancel_tx / operations,outbox / operation.changed` |
| `getInteraction` | `GET /api/v1/interactions/{id}` | `CR` through owning Conversation | `QNone -> 200 InteractionDTO` | `404 interaction_not_found` | `InteractionService.get / InteractionStore.get / operations,interaction_details / -` |
| `respondInteraction` | `POST /api/v1/interactions/{id}/responses` | `CD CAS IK` | `InteractionResponseRequest -> 202 OperationAccepted` | `404 interaction_not_found`, `409 interaction_revision_conflict,interaction_moved,interaction_not_open,response_not_expressible`, `503 terminal_unavailable` | `InteractionService.respond / InteractionStore.plan_response_tx / operations,interaction_details,input_occupancy,outbox / interaction.changed,operation.changed` |

##### Stream, Resource, draft, preference, pane, notification, push, and presence operations

| operationId | Method and path | Auth / concurrency | Query or body -> success | Errors | Trace |
|---|---|---|---|---|---|
| `getStream` | `GET /api/v1/streams/{id}` | `CR` through owner | `QNone -> 200 StreamDTO` | `404 stream_not_found` | `StreamQueryService.get / StreamStore.get / streams / -` |
| `getStreamContent` | `GET /api/v1/streams/{id}/content` | `CR` through owner | `QStreamContent -> 200 StreamContentDTO` | `404 stream_not_found`, `409 stream_revision_expired`, `410 content_expired`, `416 invalid_range,range_not_satisfiable` | `StreamQueryService.content / StreamStore.read_operations / streams,stream_frames,blobs / -` |
| `copyStream` | `GET /api/v1/streams/{id}/copy` | `CR` through owner | `QStreamCopy -> 200 bytes or 206 ranged bytes` | `404 stream_not_found`, `409 copy_not_ready`, `410 raw_content_expired,visible_content_expired`, `416 invalid_range,range_not_satisfiable` | `StreamQueryService.copy / StreamStore.open_copy / streams,blobs / -` |
| `getResource` | `GET /api/v1/resources/{id}` | `CR` through owner | `QResource -> 200 ResourceDTO` | `404 resource_not_found,resource_version_not_found` | `ResourceService.get / ResourceStore.get / resources,resource_versions / -` |
| `getResourceContent` | `GET /api/v1/resources/{id}/content` | `CR` through owner | `QResourceContent -> 200 bytes or 206 ranged bytes` | `404 resource_not_found,resource_version_not_found`, `410 resource_content_expired`, `416 invalid_range,range_not_satisfiable` | `ResourceService.content / ResourceStore.open_content / resource_versions,blobs / -` |
| `createUpload` | `POST /api/v1/uploads` | `SELF-W IK` | `multipart {file:binary[1..104857600],filename:text[1..255]} -> 201 UploadDTO` | `409 upload_quota_exceeded`, `415 media_type_forbidden`, `422 unsafe_filename` | `UploadService.stage / ResourceStore.commit_upload_tx / resources,resource_versions,blobs,outbox / resource.changed` |
| `mintDictationGrant` | `POST /api/v1/dictation/grants` | `SELF-W` plus CSRF/device binding | `DictationGrantRequest -> 201 DictationGrantDTO` | `403 dictation_unavailable`, `404 conversation_not_found`, `422 terms_out_of_scope,dictation_scope_ambiguous` | `DictationService.mint_grant / DictationGrantStore.create_tx / dictation_grants,security_audit / -` |
| `resolveClipboardFiles` | `POST /api/v1/clipboard/files:resolve` | `SELF-W IK` plus local capability and CSRF | `ClipboardFilesRequest -> 200 ClipboardFilesDTO` | `403 local_capability_required`, `409 pasteboard_changed`, `422 basename_mismatch,unsafe_path` | `ClipboardService.resolve_files / ClipboardResolutionStore.persist_success_tx / resources,resource_versions,security_audit / resource.changed` |
| `getInputBuffer` | `GET /api/v1/input-buffers` | `SELF-R`, plus `CR` for Conversation scope | `QInputBuffers -> 200 InputBufferDTO or 204 absent` | `400 ambiguous_buffer_scope`, `404 conversation_not_found,interaction_not_found` | `InputBufferService.get / InputBufferStore.get / input_buffers / -` |
| `putInputBuffer` | `PUT /api/v1/input-buffers/{id}` | `SELF-W CAS` plus `CW` for Conversation scope | `InputBufferPutRequest -> 200 InputBufferDTO` | `404 input_buffer_not_found`, `409 input_buffer_revision_conflict,author_sequence_replayed`, `422 buffer_scope_mismatch` | `InputBufferService.put / InputBufferStore.compare_and_set_tx / input_buffers / input_buffer.changed` |
| `deleteInputBuffer` | `DELETE /api/v1/input-buffers/{id}` | `SELF-W CAS` plus `CW` for Conversation scope | `DeleteCASRequest -> 200 InputBufferDTO` | `404 input_buffer_not_found`, `409 input_buffer_revision_conflict,author_sequence_replayed` | `InputBufferService.tombstone / InputBufferStore.tombstone_tx / input_buffers / input_buffer.changed` |
| `listPreferences` | `GET /api/v1/preferences` | `SELF-R`; `CR` for Conversation scope; `MA` for global scope | `QPreferences -> 200 Page<PreferenceDTO>` | `403 preference_scope_forbidden` | `PreferenceService.list / PreferenceStore.list / preferences / -` |
| `putPreference` | `PUT /api/v1/preferences/{namespace}/{scope_type}/{scope_id}/{key}` | `SELF-W CAS`; `CW` for Conversation, `MA` for global | `PreferencePutRequest -> 200 PreferenceDTO` | `404 preference_schema_not_found`, `409 preference_revision_conflict,author_sequence_replayed`, `422 preference_schema_invalid` | `PreferenceService.put / PreferenceStore.compare_and_set_tx / preferences / project_group.changed,conversation.changed as scoped` |
| `deletePreference` | `DELETE /api/v1/preferences/{namespace}/{scope_type}/{scope_id}/{key}` | `SELF-W CAS`; `CW` for Conversation, `MA` for global | `DeleteCASRequest -> 200 PreferenceDTO` | `404 preference_not_found`, `409 preference_revision_conflict,author_sequence_replayed` | `PreferenceService.tombstone / PreferenceStore.tombstone_tx / preferences / project_group.changed,conversation.changed as scoped` |
| `hideProjectGroup` | `POST /api/v1/project-groups/{group_id}:hide` | `SELF-W CAS` | `GroupHideRequest -> 200 PreferenceDTO` | `404 project_group_not_found`, `409 project_group_revision_conflict,project_has_live_session` | `ProjectGroupService.hide / PreferenceStore.hide_group_tx / preferences,agent_session_grouping / project_group.changed` |
| `togglePane` | `POST /api/v1/terminal/panes:toggle` | `CD IK` | `PaneTargetRequest -> 202 OperationAccepted` | `404 focused_session_not_found,session_not_found`, `409 ambiguous_terminal_focus,interaction_owns_input`, `503 terminal_unavailable` | `PaneService.toggle / PaneStore.plan_tx / operations,pane_state,outbox / operation.changed` |
| `growPane` | `POST /api/v1/terminal/panes:grow` | `CD IK` | `PaneTargetRequest -> 202 OperationAccepted` | `404 focused_session_not_found,session_not_found`, `409 ambiguous_terminal_focus,pane_at_maximum`, `503 terminal_unavailable` | `PaneService.grow / PaneStore.plan_tx / operations,pane_state,outbox / operation.changed` |
| `shrinkPane` | `POST /api/v1/terminal/panes:shrink` | `CD IK` | `PaneTargetRequest -> 202 OperationAccepted` | `404 focused_session_not_found,session_not_found`, `409 ambiguous_terminal_focus,pane_at_minimum`, `503 terminal_unavailable` | `PaneService.shrink / PaneStore.plan_tx / operations,pane_state,outbox / operation.changed` |
| `resetPane` | `POST /api/v1/terminal/panes:reset` | `CD IK` | `PaneTargetRequest -> 202 OperationAccepted` | `404 focused_session_not_found,session_not_found`, `409 ambiguous_terminal_focus`, `503 terminal_unavailable` | `PaneService.reset / PaneStore.plan_tx / operations,pane_state,outbox / operation.changed` |
| `setPanePercentage` | `PUT /api/v1/terminal/panes/percentage` | `CD IK` | `PanePercentageRequest -> 202 OperationAccepted` | `404 focused_session_not_found,session_not_found`, `409 ambiguous_terminal_focus`, `503 terminal_unavailable` | `PaneService.set_percentage / PaneStore.plan_tx / operations,pane_state,outbox / operation.changed` |
| `getNotificationSettings` | `GET /api/v1/notification-settings` | `NR` | `QNone -> 200 NotificationSettingsDTO` | - | `AlertPolicyService.get_settings / AlertStore.get_settings / notification_settings,conversation_mutes / -` |
| `putNotificationSettings` | `PUT /api/v1/notification-settings` | `NW CAS` | `NotificationSettingsPutRequest -> 200 NotificationSettingsDTO` | `409 notification_settings_revision_conflict`, `422 channel_configuration_invalid,retractability_exceeds_channel_limit` | `AlertPolicyService.put_settings / AlertStore.put_settings_tx / notification_settings,conversation_mutes / notification.changed` |
| `listNotifications` | `GET /api/v1/notifications` | `NR` | `QNotifications -> 200 Page<NotificationDTO>` | `404 conversation_not_found` | `AlertPolicyService.list / AlertStore.list / notification_intents,notification_deliveries,notification_route_decisions,arms / -` |
| `reactNotification` | `POST /api/v1/notifications/{id}:react` | `NW IM IK` | `NotificationReactRequest -> 202 {notification:NotificationDTO,operation:OperationDTO? optional}` | `404 notification_not_found`, `409 notification_terminal,reaction_already_applied` | `AlertPolicyService.react / AlertStore.react_tx / notification_intents,notification_deliveries,arms,operations,outbox / notification.changed` |
| `listPushSubscriptions` | `GET /api/v1/push-subscriptions` | `NR` | `QPushSubscriptions -> 200 Page<PushSubscriptionDTO>` | - | `PushSubscriptionService.list / PushStore.list / push_subscriptions,push_key_material / -` |
| `getPushConfig` | `GET /api/v1/push-config` | `NR` | `QNone -> 200 PushConfigDTO` | - | `PushSubscriptionService.get_config / PushStore.get_active_public_key / push_key_material / -` |
| `createPushSubscription` | `POST /api/v1/push-subscriptions` | `NW` | `PushSubscriptionCreateRequest -> 201 PushSubscriptionDTO` | `404 push_key_not_found`, `409 endpoint_owned_by_other_principal`, `422 push_key_invalid,endpoint_invalid` | `PushSubscriptionService.upsert / PushStore.upsert_tx / push_subscriptions,device_presence / notification.changed` |
| `deletePushSubscription` | `DELETE /api/v1/push-subscriptions/{id}` | `NW IM` | `- -> 204` | `404 subscription_not_found` | `PushSubscriptionService.delete / PushStore.delete_tx / push_subscriptions / notification.changed` |
| `rotatePushKey` | `POST /api/v1/push-keys:rotate` | `MA IK` | `PushKeyRotateRequest -> 202 OperationAccepted` | `404 push_key_not_found`, `409 orphan_count_changed,rotation_running` | `PushSubscriptionService.rotate_key / PushStore.plan_rotation_tx / push_key_material,push_subscriptions,operations,outbox / notification.changed` |
| `createPresenceSession` | `POST /api/v1/presence-sessions` | `SELF-W` | `PresenceCreateRequest -> 201 PresenceDTO` | `409 surface_session_exists`, `422 terminal_device_reserved` | `PresenceService.start / PresenceStore.start_tx / presence_sessions,device_presence / presence.changed` |
| `putPresenceSession` | `PUT /api/v1/presence-sessions/{id}` | `SELF-W CAS` | `PresencePutRequest -> 200 PresenceDTO` | `404 presence_session_not_found`, `409 presence_revision_conflict,presence_session_ended` | `PresenceService.heartbeat / PresenceStore.heartbeat_tx / presence_sessions,device_presence / presence.changed` |
| `awayPresenceSession` | `POST /api/v1/presence-sessions/{id}:away` | `SELF-W CAS` | `PresenceAwayRequest -> 200 PresenceDTO` | `404 presence_session_not_found`, `409 presence_revision_conflict` | `PresenceService.mark_away / PresenceStore.mark_away_tx / presence_sessions,device_presence / presence.changed` |
| `putTerminalPresence` | `PUT /api/v1/terminal-presence/{terminal_binding_id}` | `TERM CAS` | `TerminalPresencePutRequest -> 200 TerminalPresenceDTO` | `404 terminal_binding_not_found`, `409 binding_revision_conflict`, `503 terminal_unavailable` | `PresenceService.mark_terminal_focus / PresenceStore.put_terminal_tx / terminal_bindings,device_presence / presence.changed` |

##### Handover, collaboration, extension, repair, and backup operations

| operationId | Method and path | Auth / concurrency | Query or body -> success | Errors | Trace |
|---|---|---|---|---|---|
| `getHandover` | `GET /api/v1/handovers/{operation_id}` | `CR` through owning Conversation | `QNone -> 200 HandoverDTO` | `404 handover_not_found` | `HandoverService.get / HandoverStore.get / operations,handover_details,workflow_checkpoints / -` |
| `approveHandover` | `POST /api/v1/handovers/{operation_id}:approve` | `CD CAS IK` | `HandoverApproveRequest -> 202 OperationAccepted` | `404 handover_not_found,resource_not_found`, `409 package_revision_conflict,handover_not_waiting`, `410 resource_content_expired` | `HandoverService.approve / HandoverStore.approve_tx / handover_details,workflow_checkpoints,operations,outbox / operation.changed` |
| `retryHandoverStep` | `POST /api/v1/handovers/{operation_id}:retry-step` | `CA IM IK` | `HandoverRetryRequest -> 202 OperationAccepted` | `404 handover_not_found,step_not_found`, `409 retry_unsafe,step_not_failed,reconciliation_pending` | `HandoverService.retry_step / HandoverStore.plan_retry_tx / workflow_checkpoints,operations,outbox / operation.changed` |
| `getCollaborationInvitation` | `GET /api/v1/collaboration/invitations/{token}` | `PUB` | `QNone -> 200 InvitationDTO` | `404 invitation_not_found`, `410 invitation_expired,invitation_consumed` | `CollaborationService.get_invitation / CollaborationStore.get_by_token_hash / invitations / -` |
| `acceptCollaborationInvitation` | `POST /api/v1/collaboration/invitations/{token}:accept` | active human plus invitation `IK` | `InvitationAcceptRequest -> 201 ParticipantDTO` | `404 invitation_not_found`, `409 invitation_already_accepted,participant_exists`, `410 invitation_expired`, `422 device_key_invalid` | `CollaborationService.accept_invitation / CollaborationStore.accept_tx / invitations,participants,role_bindings,auth_revision / conversation.changed` |
| `sendPeerMessage` | `POST /api/v1/conversations/{id}/peer-messages` | `CW IM IK` plus sender actor permission | `PeerMessageRequest -> 202 OperationAccepted` | `404 conversation_not_found,actor_not_found,task_not_found`, `409 sender_actor_inactive,peer_delivery_running`, `422 body_required,body_forbidden` | `CollaborationService.send_peer_message / CollaborationStore.plan_delivery_tx / peer_messages,peer_message_deliveries,operations,outbox / operation.changed` |
| `listExtensions` | `GET /api/v1/extensions` | `MR` | `QPage -> 200 Page<ExtensionDTO>` | - | `ExtensionService.list / ExtensionStore.list / plugin_installations,surface_contributions / -` |
| `enableExtension` | `POST /api/v1/extensions/{id}:enable` | `MA IM IK` | `ExtensionEnableRequest -> 202 OperationAccepted` | `404 extension_not_found`, `409 manifest_digest_changed,extension_already_enabled,sandbox_unavailable`, `422 capability_not_declared,permission_forbidden` | `ExtensionService.enable / ExtensionStore.plan_enable_tx / plugin_installations,operations,outbox / extension.installation.changed` |
| `disableExtension` | `POST /api/v1/extensions/{id}:disable` | `MA IM IK` | `ExtensionDisableRequest -> 202 OperationAccepted` | `404 extension_not_found`, `409 extension_already_disabled,disable_running` | `ExtensionService.disable / ExtensionStore.plan_disable_tx / plugin_installations,operations,outbox / extension.installation.changed` |
| `listRepairs` | `GET /api/v1/repairs` | `MA` | `QRepairs -> 200 Page<RepairDTO>` | `404 repair_code_not_found` | `RepairService.list / RepairStore.list / repairs,repair_decisions / -` |
| `createRepair` | `POST /api/v1/repairs` | `MA IK` | `RepairCreateRequest -> 202 OperationAccepted` | `404 repair_code_not_found,evidence_not_found`, `409 repair_conflict,repair_running`, `410 evidence_expired`, `422 repair_arguments_invalid` | `RepairService.create / RepairStore.plan_tx / repairs,repair_decisions,operations,outbox / operation.changed and affected entity event` |
| `createBackup` | `POST /api/v1/backups` | `MA IK` | `BackupCreateRequest -> 202 OperationAccepted` | `409 backup_running,maintenance_active`, `507 backup_storage_full` | `BackupService.create / BackupStore.plan_create_tx / backups,operations,outbox / system.health.changed` |
| `listBackups` | `GET /api/v1/backups` | `MA` | `QBackups -> 200 Page<BackupDTO>` | - | `BackupService.list / BackupStore.list / backups / -` |
| `verifyBackup` | `POST /api/v1/backups/{id}:verify` | `MA IM IK` | `- -> 202 OperationAccepted` | `404 backup_not_found`, `409 backup_busy,maintenance_active`, `410 backup_content_expired` | `BackupService.verify / BackupStore.plan_verify_tx / backups,operations,outbox / system.health.changed` |
| `restoreBackup` | `POST /api/v1/backups/{id}:restore` | local Unix owner only `IM IK` | `BackupRestoreRequest -> 202 OperationAccepted` | `404 backup_not_found`, `409 backup_unverified,schema_incompatible,active_mutations,maintenance_active`, `410 backup_content_expired` | `BackupService.restore / BackupStore.plan_restore_tx / backups,maintenance_leases,operations,outbox / system.health.changed` |

#### Authentication bootstrap and administrative contracts

The three launcher/session routes are transport bootstrap routes rather than
versioned product operations. They are still part of the 115-endpoint equality
check, are included as Path Items in `api/openapi-v1.yaml` under the
`authentication` tag, and have exact contracts:

| operationId | Method and path | Request / authentication | Success | Errors / durable effect |
|---|---|---|---|---|
| `bootstrapBrowserSession` | `POST /auth/bootstrap` | loopback plus exact Origin; `{secret:text[43..128],device_id:text[1..128]}`; no existing credential | `204`, session cookie and `X-Baqylau-CSRF` response header | `400 invalid_bootstrap`, `401 bootstrap_expired,bootstrap_consumed`; atomically consumes `bootstrap_credentials` and inserts `browser_sessions` |
| `refreshBrowserCsrf` | `POST /auth/csrf` | live browser session plus exact Origin and same-origin Fetch Metadata; no body | `204` and a fresh `X-Baqylau-CSRF` response header | `401 authentication_required,credential_expired,credential_revoked`; conditionally rotates only that session's `csrf_digest` |
| `logoutBrowserSession` | `POST /auth/logout` | browser session plus valid CSRF; no body | `204`, cookie expired with identical attributes | `401 authentication_required,credential_revoked`; atomically revokes only that browser session and closes subscribers carrying its credential ID |

Bootstrap secrets are inserted by
`AuthorizationService.create_browser_bootstrap(local_owner, device_id) ->
{secret, expires_at}` over the Unix-socket launcher port. At most one unconsumed
secret exists per device; creating another revokes the old one. The daemon
stores only its hash, attempt count, creation/expiry, and consumed/revoked time.
Five failed exchanges revoke it and return the same generic error thereafter.

Owner-only CLI administration uses the Unix socket and these exact service
methods; it does not edit SQLite or credential files directly:

```text
baqylau auth token create --principal UUID --audience api|mcp
                          --scope SCOPE... --expires-at TIMESTAMP
  -> AuthorizationService.issue_bearer(...) -> {credential_id,token_once,expires_at}
baqylau auth credential revoke CREDENTIAL_UUID --reason TEXT
  -> AuthorizationService.revoke_credential(...) -> RevocationReceipt
baqylau auth principal suspend|revoke PRINCIPAL_UUID --reason TEXT
  -> AuthorizationService.change_principal_state(...) -> RevocationReceipt
baqylau auth session revoke SESSION_UUID --reason TEXT
  -> AuthorizationService.revoke_session(...) -> RevocationReceipt
baqylau auth certificate issue --role edge|terminal|remote-agent|proxy
                               --principal UUID --expires-at TIMESTAMP
  -> AuthorizationService.issue_certificate(...) -> {credential_id,certificate_once,ca_chain}
baqylau auth certificate revoke CREDENTIAL_UUID --reason TEXT
  -> AuthorizationService.revoke_certificate(...) -> RevocationReceipt
```

`RevocationReceipt` is
`{principal_id:UUID,credential_id:UUID? optional,previous_revision:Revision,
revision:Revision,revoked_sessions:integer[0..1000000],cancelled_effects:
integer[0..1000000],closed_connections:integer[0..1000000],recorded_at:Timestamp}`.
Issuance prints a raw bearer/private certificate exactly once to the owner-only
TTY or an explicitly supplied mode-0600 output descriptor; refusal to prove a
private destination returns `credential_output_unsafe`. Revocation is
idempotent by target and reason, and every command writes security audit,
credential/session state, authorization revision, pending-effect decisions,
and feed invalidation in one transaction before connection closure.

#### SSE event traceability

All events use the frame envelope and reducer rules in Section 38.22. `Snapshot`
names the HTTP operation whose DTO replaces the affected client scope. `Auth`
is re-evaluated when the feed row is created and when it is delivered. Feed rows
store the authorization revision; revocation invalidates the cursor rather than
silently filtering replay. Each row requires
`contract_sse_<feed>_<event_name>`, producer-transaction, authorization,
deduplication, revision-gap, replay, cursor-expiry, and reducer tests.

| Feed / event | Canonical producer and source transition | Durable write and outbox | Auth / Snapshot / frontend reducer |
|---|---|---|---|
| Machine `system.health.changed` | `DiagnosticService.recompute_health`; any health-part revision | `health_errors,ingestion_gaps,structural_changes,outbox` | `MA / getHealth / replace HealthDTO` |
| Machine `provider.changed` | `ProviderCatalog.refresh`; provider capability/install state revision | `provider_plugins,structural_changes,outbox` | `MA / listProviders / upsert/delete ProviderDTO` |
| Machine `provider.edge.changed` | `ProviderEdgeManager`; installation/trust/revert transition | `provider_edge_installations,structural_changes,outbox` | `MA / listProviderEdges / upsert ProviderEdgeDTO` |
| Machine `backend.changed` | `BackendService`; backend create/patch/delete/health revision | `backends,backend_health,structural_changes,outbox` | `MA / listBackends / upsert/delete BackendDTO` |
| Machine `execution_target.changed` | `ExecutionTargetService`; target config/reachability revision | `execution_targets,backend_health,structural_changes,outbox` | `MA / listExecutionTargets / upsert/delete ExecutionTargetDTO` |
| Machine `extension.installation.changed` | `ExtensionService`; install/start/disable/crash-loop revision | `plugin_installations,structural_changes,outbox` | `MA / listExtensions / upsert ExtensionDTO` |
| Machine `resnapshot_required` | feed overflow, schema mismatch, or machine projection replacement | `structural_changes,outbox` | `MA / getHealth plus named snapshot_url / stop reducers and replace scope` |
| Principal `account.changed` | `AccountService` or quota/migration owner; visible Account revision | `accounts,quota_windows,structural_changes,outbox` | `AR / listAccounts / upsert AccountDTO` |
| Principal `quota.changed` | `QuotaService`; accepted push/pull quota revision | `quota_windows,structural_changes,outbox` | `AR / listQuotaWindows / upsert QuotaWindowDTO` |
| Principal `project_group.changed` | `ProjectGroupService`; grouping/hide preference revision | `agent_session_grouping,preferences,structural_changes,outbox` | `SELF-R / listConversations / replace affected overview groups` |
| Principal `conversation.overview.changed` | `ConversationProjectionService`; visible overview revision | `conversation_overviews,structural_changes,outbox` | `SELF-R / listConversations / upsert/delete ConversationOverviewDTO` |
| Principal `provider.command_vocabulary.changed` | `CommandVocabularyService`; exact provider/target/workspace/actor vocabulary revision | `command_vocabulary_snapshots,structural_changes,outbox` | `CR or MR / getCommandVocabulary / replace complete scoped vocabulary` |
| Principal `extension.contribution.changed` | `ExtensionContributionService`; placement source revision | `surface_contributions,structural_changes,outbox` | `SELF-R / getConversation when Conversation-scoped, listExtensions otherwise / upsert or tombstone placement key` |
| Principal `notification.toast` | `AlertPolicyService`; newly armable visible intent transition | `notification_intents,structural_changes,outbox` | `NR / listNotifications / display only after current focus/visibility policy check` |
| Principal `notification.changed` | `AlertPolicyService`; intent/arm/route/delivery/settings revision | `notification_intents,notification_deliveries,arms,notification_settings,structural_changes,outbox` | `NR / listNotifications or getNotificationSettings by entity_type / upsert DTO` |
| Principal `presence.changed` | `PresenceService`; presence/device revision | `presence_sessions,device_presence,structural_changes,outbox` | `SELF-R / createPresenceSession response or current scoped snapshot_url / upsert PresenceDTO` |
| Principal `resource.changed` | `ResourceService`; local upload/clipboard metadata or availability revision | `resources,resource_versions,resource_path_grants,structural_changes` | `SELF-R / getResource / upsert ResourceDTO` |
| Principal `resnapshot_required` | feed overflow, authorization revision, or principal projection replacement | `structural_changes,outbox` | `SELF-R / listConversations plus named snapshot_url / stop reducers and replace scope` |
| Conversation `conversation.changed` | `ConversationService`; Conversation/head/title/archive/facet revision | `conversations,conversation_title_revisions,structural_changes,outbox` | `CR / getConversation / replace ConversationDTO and declared facets` |
| Conversation `actor_track.changed` | `ActorTrackService`; track lifecycle/head revision | `conversation_actor_tracks,structural_changes,outbox` | `CR / listActorTracks / upsert ActorTrackDTO` |
| Conversation `node.appended` | `ActorTrackService.commit_actor_node`; committed Node and track-head advance | `nodes,node_parts,conversation_actor_tracks,structural_changes,outbox` | `CR / listNodes / append NodeDTO when parent/base revision matches` |
| Conversation `node.corrected` | repair/branch reducer; visibility or supersession revision | `nodes,repairs,structural_changes,outbox` | `CR / listNodes / replace NodeDTO or tombstone branch placement` |
| Conversation `operation.changed` | Operation owner; common/detail lifecycle revision | `operations,operation_details,structural_changes,outbox` | `CR / getOperation / upsert OperationDTO and attempts` |
| Conversation `interaction.changed` | `InteractionService`; interaction progress/verdict revision | `operations,interaction_details,input_occupancy,structural_changes,outbox` | `CR / getInteraction / upsert InteractionDTO` |
| Conversation `activity.append` | `ActivityProjectionService`; item added to active generation | `materialized_activity,activity_projection_state,structural_changes,outbox` | `CR / getConversationActivity / apply append instruction on matching base generation` |
| Conversation `activity.amend` | `ActivityProjectionService`; item payload revision | same | `CR / getConversationActivity / apply amend on matching item/base revision` |
| Conversation `activity.retract` | `ActivityProjectionService`; item no longer live in generation | same | `CR / getConversationActivity / retract item on matching base generation` |
| Conversation `activity.move` | `ActivityProjectionService`; corrected causal placement | same | `CR / getConversationActivity / move item to exact placement on matching base generation` |
| Conversation `activity.supersede` | `ActivityProjectionService`; replacement item chosen | same | `CR / getConversationActivity / atomically replace old item ID with new item` |
| Conversation `stream.changed` | `StreamService`; metadata revision after durable frame or seal/abort/lost | `streams,stream_frames,structural_changes,outbox` | `CR / getStream and getStreamContent / update metadata then fetch missing revisions` |
| Conversation `attention.changed` | `AttentionService`; post-transaction attention revision | `attention_transitions,attention_projection,structural_changes,outbox` | `CR / getConversation / replace attention facet` |
| Conversation `usage.changed` | `UsageAccountingService`; accepted credit and rollup revision | `usage_facts,usage_credit_state,usage_source_authority,usage_source_rollups,daily_usage_source_rollups,structural_changes,outbox` | `CR / getUsage / replace affected query-source bucket and totals` |
| Conversation `resource.changed` | `ResourceService`; metadata/version/availability revision | `resources,resource_versions,structural_changes,outbox` | `CR / getResource / upsert ResourceDTO` |
| Principal `input_buffer.changed` | `InputBufferService`; CAS/tombstone revision for `new_session` project-scoped buffers that have no Conversation ID | `input_buffers,structural_changes,outbox` | `SELF-R / getInputBuffer / upsert InputBufferDTO including tombstone` |
| Conversation `input_buffer.changed` | `InputBufferService`; CAS/tombstone revision | `input_buffers,structural_changes,outbox` | `SELF-R plus CR / getInputBuffer / upsert InputBufferDTO including tombstone` |
| Conversation `view-mode.changed` | `ViewModeService`; accepted cross-device mode CAS | `agent_session_view_preferences,structural_changes,outbox` | `CR / getAgentSession / replace view-mode fields unless own mutation echo` |
| Conversation `extension.contribution.changed` | `ExtensionContributionService`; Conversation placement source revision | `surface_contributions,structural_changes,outbox` | `CR / getConversation / upsert or tombstone SurfaceContribution placement` |
| Conversation `resnapshot_required` | projection generation switch, feed overflow, schema mismatch, or authorization revision | `activity_projection_state,structural_changes,outbox` | `CR / getConversation / stop reducers and atomically replace Conversation scope` |

#### Generation, traceability, and acceptance gates

The OpenAPI generator applies these exact rules:

1. Every operation row becomes one Path Item Operation with the listed
   `operationId`, policy-derived `security`, closed query/header/path parameters,
   request body, success response, universal errors, and row errors.
2. UUID path parameters are required. Provider IDs and invitation tokens use
   their declared scalar schemas. Path parameters are decoded once; encoded
   slash, NUL, dot-segment, or non-canonical UUID returns `400 invalid_path`.
3. `IM`, `IK`, `CAS`, browser presence headers, SSE `Last-Event-ID`, byte
   `Range`, content type, and CSRF headers are emitted only for rows/profiles
   that require them. The generator rejects an operation accepting both IM and
   CAS.
4. Each JSON success and error body references a component above. Inline closed
   success objects in the manifest generate named components using operation ID
   plus `Response`. 204/SSE/byte responses generate no JSON schema.
5. Every paged operation references `PageMeta` and its fixed order. Every
   `Snapshot` reads data and feed high-water in one SQLite read transaction.
6. Every effect-accepted response references the durable Operation created in
   the same transaction as its outbox row. A 202 never asserts provider effect
   completion.
7. A build extracts all Section 38.24 method/path pairs and all manifest pairs,
   requires exactly 115 unique pairs in each set and equality between sets,
   then requires every registered Section 38.22 feed/event pair in the SSE
   traceability table exactly once.
8. A second build joins the manifest to the service and storage catalogues.
   Every `Owner.method` and `Store.method` must exist with matching input/result,
   every table/index must exist in the clean schema, every emitted event must be
   registered, and every operation/event must have the named generated contract
   tests. Missing joins are specification failures, not implementation choices.

The complete end-to-end trace for an HTTP mutation is therefore fixed:

```text
transport authentication -> AuthorizationContext/revision -> operation policy
-> closed request schema -> concurrency/idempotency check
-> named ApplicationOwner.method -> named Store.method transaction
-> canonical/detail/provenance rows + Operation/outbox + structural change
-> exact HTTP success/problem -> effect-time authorization recheck
-> attempt/receipt Observation -> lifecycle transition -> registered SSE event
-> named frontend reducer or snapshot replacement
```

Read traces omit the outbox/effect steps. SSE traces begin at the same canonical
transaction, replay by the scoped cursor, recheck authorization revision, and
end at the registered reducer. Daemon outage creates no alternative path: HTTP
and SSE are unavailable, edge behavior remains pass-through, and no edge spool
or second store is introduced.

### 38.39 Future workflow detail schema

This section extends Section 38.35 and removes the future-workflow table
residual recorded there. It installs the first four SQL units, in this exact
order, inside the same `BEGIN EXCLUSIVE` transaction; Section 40.7 then adds
the fifth and final unit:

1. Section 38.35 Foundation;
2. Section 38.27 review-closure DDL;
3. Section 38.35 Finalization; and
4. the Future Workflow block below.

This fourth unit drops and replaces only the Blob reachability assertion created
by Finalization, adds all future workflow tables and their indexes/triggers,
updates both schema digest records, and leaves `user_version=1`. After it runs,
the installer performs the same `foreign_key_check`, commit, fresh-connection
`quick_check`, and semantic negative fixtures required above. No table in this
block authorizes a second SQLite file or a writer outside the adapter's one FIFO
lock.

```sql
-- 38.35 FUTURE WORKFLOWS BEGIN
CREATE TRIGGER blob_reference_requires_available_blob
BEFORE INSERT ON blob_references BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM blob_objects b
    WHERE b.digest = NEW.digest AND b.state = 'available')
  THEN RAISE(ABORT, 'blob_reference_requires_available_blob') END;
END;

CREATE TABLE handover_details (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
  source_conversation_id TEXT NOT NULL
    REFERENCES conversations(id) ON DELETE RESTRICT,
  source_agent_session_id TEXT
    REFERENCES agent_sessions(id) ON DELETE SET NULL,
  target_agent_session_id TEXT
    REFERENCES agent_sessions(id) ON DELETE SET NULL,
  target_execution_target_id TEXT NOT NULL
    REFERENCES execution_targets(id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN
    ('compiling','awaiting_approval','delivering','accepted','activated',
     'rejected','failed','indeterminate','cancelled')),
  package_revision INTEGER NOT NULL CHECK(package_revision > 0),
  package_manifest_digest TEXT
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  portable_context_digest TEXT
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  approval_state TEXT NOT NULL CHECK(approval_state IN
    ('not_required','pending','approved','rejected','expired')),
  requested_runtime_json TEXT NOT NULL CHECK(json_valid(requested_runtime_json)),
  effective_runtime_json TEXT CHECK(effective_runtime_json IS NULL OR
    json_valid(effective_runtime_json)),
  current_checkpoint INTEGER NOT NULL DEFAULT 0 CHECK(current_checkpoint >= 0),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  CHECK(state = 'compiling' OR
        (package_manifest_digest IS NOT NULL AND
         portable_context_digest IS NOT NULL))
);
CREATE INDEX handover_state
  ON handover_details(state, updated_at, operation_id);

CREATE TABLE handover_resources (
  operation_id TEXT NOT NULL
    REFERENCES handover_details(operation_id) ON DELETE CASCADE,
  resource_version_id TEXT NOT NULL
    REFERENCES resource_versions(id) ON DELETE RESTRICT,
  disposition TEXT NOT NULL CHECK(disposition IN
    ('required','included','excluded','redacted','unavailable')),
  approval_required INTEGER NOT NULL CHECK(approval_required IN (0,1)),
  approval_state TEXT NOT NULL CHECK(approval_state IN
    ('not_required','pending','approved','rejected')),
  target_path_token TEXT,
  reason_code TEXT,
  PRIMARY KEY(operation_id, resource_version_id)
);

CREATE TABLE handover_checkpoints (
  operation_id TEXT NOT NULL
    REFERENCES handover_details(operation_id) ON DELETE CASCADE,
  checkpoint_number INTEGER NOT NULL CHECK(checkpoint_number >= 0),
  checkpoint_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('pending','running','succeeded','failed','indeterminate','skipped')),
  effect_attempt_id TEXT REFERENCES effect_attempts(id) ON DELETE SET NULL,
  evidence_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  started_at REAL,
  finished_at REAL,
  PRIMARY KEY(operation_id, checkpoint_number),
  CHECK(finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);

CREATE TRIGGER handover_operation_kind
BEFORE INSERT ON handover_details BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.operation_id
      AND o.kind = 'handover'
      AND o.conversation_id = NEW.source_conversation_id)
  THEN RAISE(ABORT, 'handover_operation_scope_or_kind_mismatch') END;
  SELECT CASE WHEN NEW.source_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.source_agent_session_id
      AND s.conversation_id = NEW.source_conversation_id)
  THEN RAISE(ABORT, 'handover_source_session_scope_mismatch') END;
  SELECT CASE WHEN NEW.target_agent_session_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM agent_sessions s
    WHERE s.id = NEW.target_agent_session_id
      AND s.conversation_id = NEW.source_conversation_id)
  THEN RAISE(ABORT, 'handover_target_session_scope_mismatch') END;
END;

CREATE TABLE account_migration_details (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
  agent_session_id TEXT NOT NULL
    REFERENCES agent_sessions(id) ON DELETE RESTRICT,
  trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('automatic','manual')),
  trigger_evidence_code TEXT NOT NULL,
  requested_account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
  requested_model TEXT,
  requested_effort TEXT,
  requested_mode TEXT,
  requested_execution_target_id TEXT
    REFERENCES execution_targets(id) ON DELETE SET NULL,
  effective_account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
  effective_model TEXT,
  effective_effort TEXT,
  effective_mode TEXT,
  effective_execution_target_id TEXT
    REFERENCES execution_targets(id) ON DELETE SET NULL,
  selection_evidence_digest TEXT NOT NULL
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  launch_arguments_digest TEXT
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  continuation_nudge INTEGER NOT NULL CHECK(continuation_nudge IN (0,1)),
  state TEXT NOT NULL CHECK(state IN
    ('selecting','preparing','launching','verifying','parking_old','activating',
     'succeeded','failed','indeterminate','migration_disabled','cancelled')),
  checkpoint TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  CHECK((trigger_kind = 'automatic' AND continuation_nudge = 1) OR
        (trigger_kind = 'manual' AND continuation_nudge = 0))
);
CREATE INDEX account_migration_state
  ON account_migration_details(state, updated_at, operation_id);

CREATE TABLE account_migration_candidates (
  operation_id TEXT NOT NULL
    REFERENCES account_migration_details(operation_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  account_id TEXT REFERENCES accounts(id) ON DELETE SET NULL,
  model TEXT NOT NULL,
  execution_target_id TEXT REFERENCES execution_targets(id) ON DELETE SET NULL,
  decision TEXT NOT NULL CHECK(decision IN
    ('eligible','selected','rejected','unavailable','unknown')),
  reason_code TEXT NOT NULL,
  evidence_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  PRIMARY KEY(operation_id, ordinal)
);

CREATE TABLE account_migration_checkpoints (
  operation_id TEXT NOT NULL
    REFERENCES account_migration_details(operation_id) ON DELETE CASCADE,
  checkpoint_number INTEGER NOT NULL CHECK(checkpoint_number >= 0),
  checkpoint_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('pending','running','succeeded','failed','indeterminate','skipped')),
  effect_attempt_id TEXT REFERENCES effect_attempts(id) ON DELETE SET NULL,
  evidence_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  recorded_at REAL NOT NULL,
  PRIMARY KEY(operation_id, checkpoint_number)
);
CREATE UNIQUE INDEX one_selected_migration_candidate
  ON account_migration_candidates(operation_id)
  WHERE decision = 'selected';

CREATE TRIGGER account_migration_operation_kind
BEFORE INSERT ON account_migration_details BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM operations o
    JOIN agent_sessions s ON s.id = NEW.agent_session_id
    WHERE o.id = NEW.operation_id
      AND o.kind = 'account_migration'
      AND o.conversation_id = s.conversation_id)
  THEN RAISE(ABORT, 'account_migration_operation_scope_or_kind_mismatch') END;
END;

CREATE TABLE backups (
  id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id) ON DELETE RESTRICT,
  label TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('planned','copying','manifesting','verifying','verified','failed',
     'indeterminate','expired')),
  database_relative_path TEXT NOT NULL,
  manifest_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  database_sha256 TEXT,
  database_byte_length INTEGER CHECK(database_byte_length >= 0),
  pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
  started_at REAL NOT NULL,
  finished_at REAL,
  verified_at REAL,
  error_code TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK(database_relative_path = 'backups/' || id || '/metadata.sqlite3'),
  CHECK(finished_at IS NULL OR finished_at >= started_at),
  CHECK((state IN ('verified','expired')) = (verified_at IS NOT NULL))
);
CREATE INDEX backup_retention
  ON backups(pinned DESC, verified_at DESC, id);

CREATE TABLE backup_blob_manifest (
  backup_id TEXT NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
  digest TEXT NOT NULL REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
  retention_class TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  PRIMARY KEY(backup_id, digest),
  CHECK(relative_path = 'blobs/sha256/' || substr(digest,1,2) || '/' || digest)
);

CREATE TABLE restore_details (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE RESTRICT,
  backup_id TEXT NOT NULL REFERENCES backups(id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN
    ('planned','maintenance','verifying','restoring','reopening','succeeded',
     'failed','indeterminate','cancelled')),
  pre_restore_backup_id TEXT REFERENCES backups(id) ON DELETE RESTRICT,
  verification_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  expected_schema_version INTEGER NOT NULL CHECK(expected_schema_version > 0),
  started_at REAL NOT NULL,
  finished_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK(finished_at IS NULL OR finished_at >= started_at)
);

CREATE TRIGGER backup_operation_kind
BEFORE INSERT ON backups BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.operation_id AND o.kind = 'backup')
  THEN RAISE(ABORT, 'backup_operation_kind_mismatch') END;
END;
CREATE TRIGGER restore_operation_kind
BEFORE INSERT ON restore_details BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.operation_id AND o.kind = 'restore')
  THEN RAISE(ABORT, 'restore_operation_kind_mismatch') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM backups b
    WHERE b.id = NEW.backup_id AND b.state = 'verified')
  THEN RAISE(ABORT, 'restore_requires_verified_backup') END;
END;

CREATE TABLE collaboration_invitations (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  token_sha256 TEXT NOT NULL UNIQUE CHECK(length(token_sha256) = 64 AND
    token_sha256 NOT GLOB '*[^0-9a-f]*'),
  invited_role TEXT NOT NULL CHECK(invited_role IN
    ('viewer','editor','driver','admin')),
  inviter_principal_id TEXT NOT NULL,
  recipient_hint TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('pending','accepted','revoked','expired')),
  invitation_payload_digest TEXT
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL CHECK(expires_at > created_at),
  accepted_at REAL,
  accepted_principal_id TEXT,
  revoked_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK(state <> 'accepted' OR
        (accepted_at IS NOT NULL AND accepted_principal_id IS NOT NULL)),
  CHECK((state = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE INDEX collaboration_invitation_expiry
  ON collaboration_invitations(state, expires_at, id);

CREATE TABLE conversation_memberships (
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  principal_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('viewer','editor','driver','admin')),
  actor_track_id TEXT REFERENCES conversation_actor_tracks(id) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN ('active','revoked','left')),
  invitation_id TEXT REFERENCES collaboration_invitations(id) ON DELETE SET NULL,
  authorization_revision INTEGER NOT NULL DEFAULT 0
    CHECK(authorization_revision >= 0),
  joined_at REAL NOT NULL,
  ended_at REAL,
  PRIMARY KEY(conversation_id, principal_id),
  CHECK((state = 'active') = (ended_at IS NULL))
);
CREATE INDEX memberships_principal
  ON conversation_memberships(principal_id, state, conversation_id);

CREATE TABLE collaboration_delivery_receipts (
  peer_message_id TEXT NOT NULL REFERENCES peer_messages(id) ON DELETE CASCADE,
  recipient_principal_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('accepted','delivered','read','rejected','failed','indeterminate')),
  external_receipt_ref TEXT,
  evidence_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  received_at REAL NOT NULL,
  PRIMARY KEY(peer_message_id, recipient_principal_id)
);

CREATE TRIGGER membership_actor_scope
BEFORE INSERT ON conversation_memberships WHEN NEW.actor_track_id IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM conversation_actor_tracks t
    WHERE t.id = NEW.actor_track_id
      AND t.conversation_id = NEW.conversation_id)
  THEN RAISE(ABORT, 'membership_actor_scope_mismatch') END;
END;

CREATE TABLE public_links (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  token_sha256 TEXT NOT NULL UNIQUE CHECK(length(token_sha256) = 64 AND
    token_sha256 NOT GLOB '*[^0-9a-f]*'),
  permission TEXT NOT NULL CHECK(permission = 'read_snapshot'),
  state TEXT NOT NULL CHECK(state IN ('active','revoked','expired')),
  created_by_principal_id TEXT NOT NULL,
  public_base_url_revision INTEGER NOT NULL CHECK(public_base_url_revision >= 0),
  snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision >= 0),
  snapshot_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  created_at REAL NOT NULL,
  expires_at REAL,
  revoked_at REAL,
  last_accessed_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK(expires_at IS NULL OR expires_at > created_at),
  CHECK((state = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE INDEX public_link_state
  ON public_links(conversation_id, state, expires_at, id);

CREATE TABLE extension_namespaces (
  namespace TEXT PRIMARY KEY,
  plugin_id TEXT NOT NULL,
  plugin_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN
    ('active','read_only','disabled','orphaned')),
  current_schema_version INTEGER NOT NULL CHECK(current_schema_version > 0),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  FOREIGN KEY(plugin_id, plugin_version)
    REFERENCES plugin_installations(plugin_id, version) ON DELETE RESTRICT
);

CREATE TABLE extension_installation_details (
  plugin_id TEXT NOT NULL,
  plugin_version TEXT NOT NULL,
  install_operation_id TEXT REFERENCES operations(id) ON DELETE SET NULL,
  config_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  sandbox_profile TEXT,
  granted_brokers_json TEXT NOT NULL DEFAULT '[]'
    CHECK(json_valid(granted_brokers_json)),
  installed_at REAL NOT NULL,
  disabled_at REAL,
  PRIMARY KEY(plugin_id, plugin_version),
  FOREIGN KEY(plugin_id, plugin_version)
    REFERENCES plugin_installations(plugin_id, version) ON DELETE CASCADE
);

CREATE TABLE extension_namespace_migrations (
  namespace TEXT NOT NULL
    REFERENCES extension_namespaces(namespace) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK(version > 0),
  migration_id TEXT NOT NULL,
  migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256) = 64 AND
    migration_sha256 NOT GLOB '*[^0-9a-f]*'),
  manifest_digest TEXT NOT NULL
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  installed_at REAL NOT NULL,
  PRIMARY KEY(namespace, version),
  UNIQUE(namespace, migration_id)
);

CREATE TABLE extension_facts (
  namespace TEXT NOT NULL
    REFERENCES extension_namespaces(namespace) ON DELETE RESTRICT,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  fact_key TEXT NOT NULL,
  fact_revision INTEGER NOT NULL CHECK(fact_revision >= 0),
  payload_digest TEXT NOT NULL
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  tombstone INTEGER NOT NULL DEFAULT 0 CHECK(tombstone IN (0,1)),
  created_at REAL NOT NULL,
  PRIMARY KEY(namespace, entity_type, entity_id, fact_key, fact_revision)
);
CREATE INDEX extension_fact_current
  ON extension_facts(namespace, entity_type, entity_id, fact_key,
                     fact_revision DESC);

CREATE TABLE repair_definitions (
  repair_code TEXT PRIMARY KEY,
  definition_sha256 TEXT NOT NULL CHECK(length(definition_sha256) = 64 AND
    definition_sha256 NOT GLOB '*[^0-9a-f]*'),
  owner TEXT NOT NULL,
  argument_schema_json TEXT NOT NULL CHECK(json_valid(argument_schema_json)),
  rollback_class TEXT NOT NULL CHECK(rollback_class IN
    ('transactional','compensating','restore_required')),
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  registered_at REAL NOT NULL
);

CREATE TABLE repair_execution_results (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE RESTRICT,
  repair_code TEXT NOT NULL
    REFERENCES repair_definitions(repair_code) ON DELETE RESTRICT,
  state TEXT NOT NULL CHECK(state IN
    ('planned','applying','verifying','succeeded','failed','indeterminate',
     'rolled_back')),
  arguments_digest TEXT NOT NULL
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  result_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  verification_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  started_at REAL NOT NULL,
  finished_at REAL,
  CHECK(finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE anomaly_definitions (
  anomaly_code TEXT PRIMARY KEY,
  severity TEXT NOT NULL CHECK(severity IN
    ('info','warning','error','critical')),
  title TEXT NOT NULL,
  explanation TEXT NOT NULL,
  remediation TEXT,
  legacy_disposition TEXT NOT NULL CHECK(legacy_disposition IN
    ('ported','obsolete_by_construction')),
  legacy_section TEXT,
  owner TEXT NOT NULL,
  definition_sha256 TEXT NOT NULL CHECK(length(definition_sha256) = 64 AND
    definition_sha256 NOT GLOB '*[^0-9a-f]*'),
  parameter_schema_json TEXT NOT NULL CHECK(json_valid(parameter_schema_json)),
  fixture_ids_json TEXT NOT NULL CHECK(json_valid(fixture_ids_json)),
  enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
  registered_at REAL NOT NULL
);

CREATE TABLE anomaly_runs (
  id TEXT PRIMARY KEY,
  requested_by_principal_id TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('running','completed','completed_with_errors','failed','cancelled')),
  catalogue_sha256 TEXT NOT NULL CHECK(length(catalogue_sha256) = 64 AND
    catalogue_sha256 NOT GLOB '*[^0-9a-f]*'),
  started_at REAL NOT NULL,
  finished_at REAL,
  CHECK(finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE anomaly_results (
  run_id TEXT NOT NULL REFERENCES anomaly_runs(id) ON DELETE CASCADE,
  anomaly_code TEXT NOT NULL
    REFERENCES anomaly_definitions(anomaly_code) ON DELETE RESTRICT,
  scope_type TEXT NOT NULL CHECK(scope_type IN
    ('machine','conversation','agent_session','operation','resource')),
  scope_id TEXT NOT NULL,
  result_key TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN
    ('info','warning','error','critical')),
  result_digest TEXT NOT NULL
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  detected_at REAL NOT NULL,
  suppressed_by_registry INTEGER NOT NULL DEFAULT 0 CHECK(suppressed_by_registry IN (0,1)),
  PRIMARY KEY(run_id, anomaly_code, scope_type, scope_id, result_key)
);
CREATE INDEX anomaly_result_scope
  ON anomaly_results(scope_type, scope_id, severity, detected_at, anomaly_code);

CREATE TRIGGER repair_execution_operation_kind
BEFORE INSERT ON repair_execution_results BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM operations o
    WHERE o.id = NEW.operation_id AND o.kind = 'repair')
  THEN RAISE(ABORT, 'repair_operation_kind_mismatch') END;
END;

CREATE TABLE legacy_schema_registry (
  schema_fingerprint TEXT PRIMARY KEY CHECK(length(schema_fingerprint) = 64 AND
    schema_fingerprint NOT GLOB '*[^0-9a-f]*'),
  source_family TEXT NOT NULL,
  importer_version TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('supported','blocked','retired')),
  mapping_manifest_digest TEXT NOT NULL
    REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  fixture_id TEXT NOT NULL,
  registered_at REAL NOT NULL,
  retired_at REAL
);

CREATE TABLE legacy_source_discoveries (
  source_database_fingerprint TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  source_stat_json TEXT NOT NULL CHECK(json_valid(source_stat_json)),
  detected_schema_fingerprint TEXT CHECK(detected_schema_fingerprint IS NULL OR
    (length(detected_schema_fingerprint) = 64 AND
     detected_schema_fingerprint NOT GLOB '*[^0-9a-f]*')),
  registry_state TEXT NOT NULL CHECK(registry_state IN
    ('supported','blocked','retired','unknown','unreadable')),
  error_code TEXT,
  first_seen_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  CHECK(last_seen_at >= first_seen_at),
  CHECK((registry_state = 'unreadable') = (error_code IS NOT NULL))
);
CREATE INDEX legacy_discovery_state
  ON legacy_source_discoveries(registry_state, last_seen_at, source_database_fingerprint);

CREATE TRIGGER legacy_import_requires_registered_schema_insert
BEFORE INSERT ON legacy_import_runs BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM legacy_schema_registry r
    WHERE r.schema_fingerprint = NEW.schema_fingerprint
      AND r.state = 'supported')
  THEN RAISE(ABORT, 'legacy_schema_fingerprint_not_supported') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM legacy_source_discoveries d
    WHERE d.source_database_fingerprint = NEW.source_database_fingerprint
      AND d.detected_schema_fingerprint = NEW.schema_fingerprint
      AND d.registry_state = 'supported')
  THEN RAISE(ABORT, 'legacy_source_not_registered_for_import') END;
END;
CREATE TRIGGER legacy_import_schema_is_immutable
BEFORE UPDATE OF source_database_fingerprint, schema_fingerprint
ON legacy_import_runs BEGIN
  SELECT RAISE(ABORT, 'legacy_import_identity_is_immutable');
END;

CREATE TRIGGER future_workflow_blob_refs_handover_insert
AFTER INSERT ON handover_details BEGIN
  INSERT INTO blob_references
    SELECT 'handover_details',NEW.operation_id,'package_manifest',
           NEW.package_manifest_digest,'handover',NEW.created_at
    WHERE NEW.package_manifest_digest IS NOT NULL;
  INSERT INTO blob_references
    SELECT 'handover_details',NEW.operation_id,'portable_context',
           NEW.portable_context_digest,'handover',NEW.created_at
    WHERE NEW.portable_context_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_handover_update
AFTER UPDATE OF package_manifest_digest, portable_context_digest
ON handover_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='handover_details' AND owner_key=NEW.operation_id;
  INSERT INTO blob_references
    SELECT 'handover_details',NEW.operation_id,'package_manifest',
           NEW.package_manifest_digest,'handover',NEW.created_at
    WHERE NEW.package_manifest_digest IS NOT NULL;
  INSERT INTO blob_references
    SELECT 'handover_details',NEW.operation_id,'portable_context',
           NEW.portable_context_digest,'handover',NEW.created_at
    WHERE NEW.portable_context_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_handover_delete
AFTER DELETE ON handover_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='handover_details' AND owner_key=OLD.operation_id;
END;

CREATE TRIGGER future_workflow_blob_refs_handover_checkpoint_insert
AFTER INSERT ON handover_checkpoints WHEN NEW.evidence_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('handover_checkpoints',NEW.operation_id || ':' || NEW.checkpoint_number,
     'evidence',NEW.evidence_digest,'handover',coalesce(NEW.started_at,unixepoch('subsec')));
END;
CREATE TRIGGER handover_checkpoint_evidence_is_immutable
BEFORE UPDATE OF evidence_digest ON handover_checkpoints BEGIN
  SELECT RAISE(ABORT, 'handover_checkpoint_evidence_is_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_handover_checkpoint_delete
AFTER DELETE ON handover_checkpoints BEGIN
  DELETE FROM blob_references
  WHERE owner_table='handover_checkpoints'
    AND owner_key=OLD.operation_id || ':' || OLD.checkpoint_number;
END;

CREATE TRIGGER future_workflow_blob_refs_migration_insert
AFTER INSERT ON account_migration_details BEGIN
  INSERT INTO blob_references VALUES
    ('account_migration_details',NEW.operation_id,'selection_evidence',
     NEW.selection_evidence_digest,'migration',NEW.created_at);
  INSERT INTO blob_references
    SELECT 'account_migration_details',NEW.operation_id,'launch_arguments',
           NEW.launch_arguments_digest,'migration',NEW.created_at
    WHERE NEW.launch_arguments_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_migration_update
AFTER UPDATE OF selection_evidence_digest, launch_arguments_digest
ON account_migration_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='account_migration_details' AND owner_key=NEW.operation_id;
  INSERT INTO blob_references VALUES
    ('account_migration_details',NEW.operation_id,'selection_evidence',
     NEW.selection_evidence_digest,'migration',NEW.created_at);
  INSERT INTO blob_references
    SELECT 'account_migration_details',NEW.operation_id,'launch_arguments',
           NEW.launch_arguments_digest,'migration',NEW.created_at
    WHERE NEW.launch_arguments_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_migration_delete
AFTER DELETE ON account_migration_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='account_migration_details' AND owner_key=OLD.operation_id;
END;

CREATE TRIGGER future_workflow_blob_refs_migration_candidate_insert
AFTER INSERT ON account_migration_candidates WHEN NEW.evidence_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('account_migration_candidates',NEW.operation_id || ':' || NEW.ordinal,
     'evidence',NEW.evidence_digest,'migration',unixepoch('subsec'));
END;
CREATE TRIGGER migration_candidate_evidence_is_immutable
BEFORE UPDATE OF evidence_digest ON account_migration_candidates BEGIN
  SELECT RAISE(ABORT, 'migration_candidate_evidence_is_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_migration_candidate_delete
AFTER DELETE ON account_migration_candidates BEGIN
  DELETE FROM blob_references
  WHERE owner_table='account_migration_candidates'
    AND owner_key=OLD.operation_id || ':' || OLD.ordinal;
END;

CREATE TRIGGER future_workflow_blob_refs_migration_checkpoint_insert
AFTER INSERT ON account_migration_checkpoints WHEN NEW.evidence_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('account_migration_checkpoints',NEW.operation_id || ':' || NEW.checkpoint_number,
     'evidence',NEW.evidence_digest,'migration',NEW.recorded_at);
END;
CREATE TRIGGER migration_checkpoint_evidence_is_immutable
BEFORE UPDATE OF evidence_digest ON account_migration_checkpoints BEGIN
  SELECT RAISE(ABORT, 'migration_checkpoint_evidence_is_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_migration_checkpoint_delete
AFTER DELETE ON account_migration_checkpoints BEGIN
  DELETE FROM blob_references
  WHERE owner_table='account_migration_checkpoints'
    AND owner_key=OLD.operation_id || ':' || OLD.checkpoint_number;
END;

CREATE TRIGGER future_workflow_blob_refs_backup_insert
AFTER INSERT ON backups WHEN NEW.manifest_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('backups',NEW.id,'manifest',NEW.manifest_digest,'backup',NEW.started_at);
END;
CREATE TRIGGER future_workflow_blob_refs_backup_update
AFTER UPDATE OF manifest_digest ON backups BEGIN
  DELETE FROM blob_references
  WHERE owner_table='backups' AND owner_key=NEW.id AND role='manifest';
  INSERT INTO blob_references
    SELECT 'backups',NEW.id,'manifest',NEW.manifest_digest,'backup',NEW.started_at
    WHERE NEW.manifest_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_backup_delete
AFTER DELETE ON backups BEGIN
  DELETE FROM blob_references WHERE owner_table='backups' AND owner_key=OLD.id;
END;
CREATE TRIGGER future_workflow_blob_refs_backup_manifest_insert
AFTER INSERT ON backup_blob_manifest BEGIN
  INSERT INTO blob_references VALUES
    ('backup_blob_manifest',NEW.backup_id || ':' || NEW.digest,'pinned_blob',
     NEW.digest,'backup',unixepoch('subsec'));
END;

CREATE TRIGGER future_workflow_blob_refs_restore_insert
AFTER INSERT ON restore_details WHEN NEW.verification_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('restore_details',NEW.operation_id,'verification',NEW.verification_digest,
     'backup',NEW.started_at);
END;
CREATE TRIGGER future_workflow_blob_refs_restore_update
AFTER UPDATE OF verification_digest ON restore_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='restore_details' AND owner_key=NEW.operation_id;
  INSERT INTO blob_references
    SELECT 'restore_details',NEW.operation_id,'verification',
           NEW.verification_digest,'backup',NEW.started_at
    WHERE NEW.verification_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_restore_delete
AFTER DELETE ON restore_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='restore_details' AND owner_key=OLD.operation_id;
END;

CREATE TRIGGER future_workflow_blob_refs_invitation_insert
AFTER INSERT ON collaboration_invitations
WHEN NEW.invitation_payload_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('collaboration_invitations',NEW.id,'payload',NEW.invitation_payload_digest,
     'collaboration',NEW.created_at);
END;
CREATE TRIGGER invitation_payload_is_immutable
BEFORE UPDATE OF invitation_payload_digest ON collaboration_invitations BEGIN
  SELECT RAISE(ABORT, 'invitation_payload_is_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_invitation_delete
AFTER DELETE ON collaboration_invitations BEGIN
  DELETE FROM blob_references
  WHERE owner_table='collaboration_invitations' AND owner_key=OLD.id;
END;

CREATE TRIGGER future_workflow_blob_refs_collaboration_receipt_insert
AFTER INSERT ON collaboration_delivery_receipts
WHEN NEW.evidence_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('collaboration_delivery_receipts',
     NEW.peer_message_id || ':' || NEW.recipient_principal_id,
     'evidence',NEW.evidence_digest,'collaboration',NEW.received_at);
END;
CREATE TRIGGER collaboration_receipt_evidence_is_immutable
BEFORE UPDATE OF evidence_digest ON collaboration_delivery_receipts BEGIN
  SELECT RAISE(ABORT, 'collaboration_receipt_evidence_is_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_collaboration_receipt_delete
AFTER DELETE ON collaboration_delivery_receipts BEGIN
  DELETE FROM blob_references
  WHERE owner_table='collaboration_delivery_receipts'
    AND owner_key=OLD.peer_message_id || ':' || OLD.recipient_principal_id;
END;

CREATE TRIGGER future_workflow_blob_refs_public_link_insert
AFTER INSERT ON public_links WHEN NEW.snapshot_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('public_links',NEW.id,'snapshot',NEW.snapshot_digest,
     'public_link',NEW.created_at);
END;
CREATE TRIGGER future_workflow_blob_refs_public_link_update
AFTER UPDATE OF snapshot_digest ON public_links BEGIN
  DELETE FROM blob_references
  WHERE owner_table='public_links' AND owner_key=NEW.id;
  INSERT INTO blob_references
    SELECT 'public_links',NEW.id,'snapshot',NEW.snapshot_digest,
           'public_link',NEW.created_at
    WHERE NEW.snapshot_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_public_link_delete
AFTER DELETE ON public_links BEGIN
  DELETE FROM blob_references
  WHERE owner_table='public_links' AND owner_key=OLD.id;
END;

CREATE TRIGGER future_workflow_blob_refs_extension_install_insert
AFTER INSERT ON extension_installation_details WHEN NEW.config_digest IS NOT NULL BEGIN
  INSERT INTO blob_references VALUES
    ('extension_installation_details',NEW.plugin_id || ':' || NEW.plugin_version,
     'config',NEW.config_digest,'extension',NEW.installed_at);
END;
CREATE TRIGGER future_workflow_blob_refs_extension_install_update
AFTER UPDATE OF config_digest ON extension_installation_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='extension_installation_details'
    AND owner_key=NEW.plugin_id || ':' || NEW.plugin_version;
  INSERT INTO blob_references
    SELECT 'extension_installation_details',
           NEW.plugin_id || ':' || NEW.plugin_version,
           'config',NEW.config_digest,'extension',NEW.installed_at
    WHERE NEW.config_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_extension_install_delete
AFTER DELETE ON extension_installation_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='extension_installation_details'
    AND owner_key=OLD.plugin_id || ':' || OLD.plugin_version;
END;

CREATE TRIGGER future_workflow_blob_refs_extension_migration_insert
AFTER INSERT ON extension_namespace_migrations BEGIN
  INSERT INTO blob_references VALUES
    ('extension_namespace_migrations',NEW.namespace || ':' || NEW.version,
     'manifest',NEW.manifest_digest,'extension',NEW.installed_at);
END;
CREATE TRIGGER extension_migration_manifest_is_immutable
BEFORE UPDATE OF manifest_digest ON extension_namespace_migrations BEGIN
  SELECT RAISE(ABORT, 'extension_migration_manifest_is_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_extension_migration_delete
AFTER DELETE ON extension_namespace_migrations BEGIN
  DELETE FROM blob_references
  WHERE owner_table='extension_namespace_migrations'
    AND owner_key=OLD.namespace || ':' || OLD.version;
END;
CREATE TRIGGER future_workflow_blob_refs_backup_manifest_delete
AFTER DELETE ON backup_blob_manifest BEGIN
  DELETE FROM blob_references
  WHERE owner_table='backup_blob_manifest'
    AND owner_key=OLD.backup_id || ':' || OLD.digest;
END;

CREATE TRIGGER future_workflow_blob_refs_extension_fact_insert
AFTER INSERT ON extension_facts BEGIN
  INSERT INTO blob_references VALUES
    ('extension_facts',NEW.namespace || ':' || NEW.entity_type || ':' ||
     NEW.entity_id || ':' || NEW.fact_key || ':' || NEW.fact_revision,
     'payload',NEW.payload_digest,'extension',NEW.created_at);
END;
CREATE TRIGGER future_workflow_blob_refs_extension_fact_delete
AFTER DELETE ON extension_facts BEGIN
  DELETE FROM blob_references
  WHERE owner_table='extension_facts'
    AND owner_key=OLD.namespace || ':' || OLD.entity_type || ':' ||
      OLD.entity_id || ':' || OLD.fact_key || ':' || OLD.fact_revision;
END;
CREATE TRIGGER extension_fact_payload_is_immutable
BEFORE UPDATE OF payload_digest ON extension_facts BEGIN
  SELECT RAISE(ABORT, 'extension_fact_payload_is_immutable');
END;

CREATE TRIGGER future_workflow_blob_refs_repair_insert
AFTER INSERT ON repair_execution_results BEGIN
  INSERT INTO blob_references VALUES
    ('repair_execution_results',NEW.operation_id,'arguments',
     NEW.arguments_digest,'repair',NEW.started_at);
  INSERT INTO blob_references
    SELECT 'repair_execution_results',NEW.operation_id,'result',
           NEW.result_digest,'repair',NEW.started_at
    WHERE NEW.result_digest IS NOT NULL;
  INSERT INTO blob_references
    SELECT 'repair_execution_results',NEW.operation_id,'verification',
           NEW.verification_digest,'repair',NEW.started_at
    WHERE NEW.verification_digest IS NOT NULL;
END;
CREATE TRIGGER future_workflow_blob_refs_repair_update
AFTER UPDATE OF result_digest, verification_digest ON repair_execution_results BEGIN
  DELETE FROM blob_references
  WHERE owner_table='repair_execution_results' AND owner_key=NEW.operation_id
    AND role IN ('result','verification');
  INSERT INTO blob_references
    SELECT 'repair_execution_results',NEW.operation_id,'result',
           NEW.result_digest,'repair',NEW.started_at
    WHERE NEW.result_digest IS NOT NULL;
  INSERT INTO blob_references
    SELECT 'repair_execution_results',NEW.operation_id,'verification',
           NEW.verification_digest,'repair',NEW.started_at
    WHERE NEW.verification_digest IS NOT NULL;
END;
CREATE TRIGGER repair_arguments_are_immutable
BEFORE UPDATE OF arguments_digest ON repair_execution_results BEGIN
  SELECT RAISE(ABORT, 'repair_arguments_are_immutable');
END;
CREATE TRIGGER future_workflow_blob_refs_repair_delete
AFTER DELETE ON repair_execution_results BEGIN
  DELETE FROM blob_references
  WHERE owner_table='repair_execution_results' AND owner_key=OLD.operation_id;
END;

CREATE TRIGGER future_workflow_blob_refs_anomaly_insert
AFTER INSERT ON anomaly_results BEGIN
  INSERT INTO blob_references VALUES
    ('anomaly_results',NEW.run_id || ':' || NEW.anomaly_code || ':' ||
     NEW.scope_type || ':' || NEW.scope_id || ':' || NEW.result_key,
     'result',NEW.result_digest,'diagnostic',NEW.detected_at);
END;
CREATE TRIGGER future_workflow_blob_refs_anomaly_delete
AFTER DELETE ON anomaly_results BEGIN
  DELETE FROM blob_references
  WHERE owner_table='anomaly_results'
    AND owner_key=OLD.run_id || ':' || OLD.anomaly_code || ':' ||
      OLD.scope_type || ':' || OLD.scope_id || ':' || OLD.result_key;
END;
CREATE TRIGGER anomaly_result_payload_is_immutable
BEFORE UPDATE OF result_digest ON anomaly_results BEGIN
  SELECT RAISE(ABORT, 'anomaly_result_payload_is_immutable');
END;

CREATE TRIGGER future_workflow_blob_refs_registry_insert
AFTER INSERT ON legacy_schema_registry BEGIN
  INSERT INTO blob_references VALUES
    ('legacy_schema_registry',NEW.schema_fingerprint,'mapping_manifest',
     NEW.mapping_manifest_digest,'identity_evidence',NEW.registered_at);
END;
CREATE TRIGGER future_workflow_blob_refs_registry_delete
AFTER DELETE ON legacy_schema_registry BEGIN
  DELETE FROM blob_references
  WHERE owner_table='legacy_schema_registry' AND owner_key=OLD.schema_fingerprint;
END;
CREATE TRIGGER legacy_mapping_manifest_is_immutable
BEFORE UPDATE OF mapping_manifest_digest ON legacy_schema_registry BEGIN
  SELECT RAISE(ABORT, 'legacy_mapping_manifest_is_immutable');
END;

DROP TRIGGER blob_state_requires_zero_references;
CREATE TRIGGER blob_state_requires_zero_references
BEFORE UPDATE OF state ON blob_objects
WHEN NEW.state IN ('quarantined','deleted') BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM blob_references r WHERE r.digest = OLD.digest
    UNION ALL SELECT 1 FROM operations x
      WHERE x.result_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM native_records x
      WHERE x.payload_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM context_checkpoints x
      WHERE x.summary_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM resource_versions x
      WHERE x.blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM node_parts x
      WHERE x.content_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM provenance_records x
      WHERE x.evidence_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM ingestion_decisions x
      WHERE x.detail_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM input_buffers x
      WHERE x.text_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM outbox x
      WHERE x.payload_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM effect_attempts x
      WHERE x.request_blob_digest = OLD.digest OR x.receipt_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM repair_records x
      WHERE x.before_blob_digest = OLD.digest OR x.after_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM purge_authorizations x
      WHERE x.manifest_blob_digest = OLD.digest
    UNION ALL SELECT 1 FROM interaction_details x
      WHERE x.prompt_ref = OLD.digest OR x.options_ref = OLD.digest OR
            x.response_ref = OLD.digest
    UNION ALL SELECT 1 FROM peer_messages x WHERE x.body_ref = OLD.digest
    UNION ALL SELECT 1 FROM tui_drafts x WHERE x.text_ref = OLD.digest
    UNION ALL SELECT 1 FROM command_vocabulary_snapshots x
      WHERE x.payload_ref = OLD.digest
    UNION ALL SELECT 1 FROM structural_changes x WHERE x.payload_ref = OLD.digest
    UNION ALL SELECT 1 FROM materialized_activity x WHERE x.payload_ref = OLD.digest
    UNION ALL SELECT 1 FROM legacy_import_rows x WHERE x.error_ref = OLD.digest)
  THEN RAISE(ABORT, 'blob_still_referenced') END;
END;

UPDATE schema_migrations
SET sha256 = '7238fd85e299276c6b301e6a8079af67675b18c4ba90c42094daeefe3b53cfb3'
WHERE version = 1;
UPDATE schema_metadata
SET clean_install_sha256 =
  '7238fd85e299276c6b301e6a8079af67675b18c4ba90c42094daeefe3b53cfb3',
    updated_at = unixepoch('subsec')
WHERE singleton = 1;
PRAGMA user_version = 1;
-- 38.35 FUTURE WORKFLOWS END
```

All rows above are canonical/supporting metadata in the same machine-wide
database. Workflow bodies, manifests, diagnostic results, and extension fact
payloads remain in the one content-addressed Blob store and participate in the
same reachability/retention protocol.

Legacy schema handling is closed and fail-closed. Phase 0 may add a
`legacy_schema_registry` row only in the same commit as a raw fixture, expected
canonical fixture, mapping manifest Blob, importer implementation, and tests.
The registry is empty in this design because no unmeasured fingerprint is
invented. Discovery of an unknown fingerprint writes only
`legacy_source_discoveries(registry_state='unknown')`, degrades import health,
and returns `unsupported_legacy_schema`; it creates no `legacy_import_runs` row
and imports no partial data. A changed source is re-fingerprinted before every
batch; mismatch ends the run as failed and requires a new discovery. `blocked`
and `retired` registry entries remain diagnostic history and can never import.

The intermediate schema hash definition is extended to Foundation + Section 38.27
+ Finalization + Future Workflow SQL, UTF-8/LF, in that order, without marker
comments, with every embedded version-1 schema digest literal normalized to 64
ASCII zeroes. Final packaging replaces the two zero sentinels in the fourth
unit with the digest stated below; the clean database must finish with the same
digest in `schema_migrations.sha256` and
`schema_metadata.clean_install_sha256`. The intermediate four-unit digest is
`7238fd85e299276c6b301e6a8079af67675b18c4ba90c42094daeefe3b53cfb3`;
it supersedes the earlier three-unit digest printed in Section 38.35 and is in
turn superseded by the final five-unit digest in Section 40.7.

---

## 40. Normative closure of the second legacy-coverage review

This section closes `rewrite-design-v4-review-2-fable.md`. It is authoritative
over less-specific earlier wording. The concrete legacy omissions and internal
contradictions are accepted. The review's proposals to remove retained
machinery or future-feature design are not accepted because the product
decisions in Sections 0 and 38.28 remain fixed. Performance risks are answered
with earlier executable gates and a narrower deployable slice, not by adding
spooling, per-Conversation databases, or an unspecified implementation escape.

### 40.1 Completed-task dismissal

The web tasks card exposes Dismiss only for the latest `complete` provider task
snapshot when every task has the provider's registered completed status. The
client sorts task IDs as UTF-8 bytes, rejects duplicates, and computes:

```text
task_set_digest = sha256(utf8_len_prefixed(sorted_task_ids))
preference identity =
  (namespace='tasks', scope_type='conversation', scope_id=<conversation-id>,
   key='hidden:' + lowercase_hex(task_set_digest))
```

`POST /api/v1/conversations/{id}/tasks:dismiss` rereads the current snapshot in
the write transaction, recomputes the digest, proves all tasks complete, and
writes `{snapshot_id,task_set_digest}` using preference CAS. It returns
`409 tasks_not_done` or `409 task_snapshot_changed` without writing when either
proof fails. Reads hide a card only when the current complete snapshot has the
same digest. A changed task ID set therefore self-unhides with no explicit
reset. The preference is principal-wide and cross-device.

### 40.2 Memory extension contract

`baqylau.memory` is a bundled first-party extension with two configured paths:
`vault_root` (legacy default `~/wiki/01`) and `project_root` (legacy default
`~/code/01/aggregator-adapters`). Configuration layering is command/session
environment, project configuration, then machine configuration. Both paths are
resolved without symlinks. The feature is in scope only when the frozen session
cwd is `project_root` or its declared worktree descendant. Off-scope producers
record ordinary file/command behavior, `memory_scope=false`, badge zero, and no
Memory tab; a deep link falls back to the mirror.

Tool Read/Write/Edit under the vault creates one `memory_touches` fact keyed by
AgentSession and canonical path. Repeats increment count; verb escalates only
`read < update < write`; the escalating actor becomes the displayed actor.
Newest touch is diagnostic, while the tree order is folders-before-notes and
alphabetical. Linear folder chains collapse; a lone leaf folder may fold into
its parent labels; the root and sibling forks never collapse. Client fold state
is session-local presentation state keyed by vault-relative folder path.

The Bash plane is reads/searches only. It uses the provider shell parser over
every statement and `statement_cwds(command,cwd,tilde=true)`: bare `~` may
resolve, `~user` remains dynamic. It scans every token, ignores tokens beginning
with `-`, and records a path only when the resolved path is a real regular file
under `vault_root` and the statement contains a registered reader such as
`cat`, `head`, `tail`, `sed`, `grep`, `rg`, `bat`, `glow`, or a measured
`find -exec`/`xargs` reader. A basename may use the names-only vault index only
when that statement's cwd is inside the vault. `qmd get` and `qmd multi-get`
resolve `qmd://` and `:from:count` forms against the configured collection root.
Shell writes/redirections/moves are deliberately not memory touches.

`qmd search|query` produces one `memory_searches` row keyed by
`(agent_session_id,kind,subcommand,query)`. A rerun increments count and replaces
the complete ordered hit set. Hits store path, relative path, name, line, title,
score, bounded snippet, and `viewable`; renamed/deleted hits remain as the answer
the provider saw. `query` also stores the parsed `lex:`, `vec:`, and `hyde:`
rewrites. Hits are not note-read facts. If one foreground command contains more
than one qmd search, or is backgrounded so hook output is unavailable, every
query is recorded without hits; output is never assigned by guess. Search count
is capped at 100 per session, hits at 50 per search, and each snippet at 16 KiB;
oldest searches fall off first.

The note viewer accepts only a recorded path or a stem resolved by the
names-only index, rechecks the realpath jail, and returns name, parsed
frontmatter, safe escaped Markdown HTML, backlinks, and `missing`. Wikilinks are
parsed before Markdown emphasis, resolve by bare stem, remain visibly dead when
unresolved, and never inject raw HTML. Backlinks use a separately cached
content-reading index. Note/search clicks use direct client handlers; breadcrumb
history and expanded-card/folder state are presentation-only. The Memory view,
mirror `memory` classification, badge, tree, viewer, backlinks, and qmd cards
all read these same extension-owned facts. The badge is the number of distinct
touch rows plus the number of distinct search rows; repeat `count` values are
displayed but do not inflate the badge.

Each folder row carries subtree note/change rollups computed bottom-up from the
same stable snapshot. Ordering is deliberately stable: folders before notes,
then Unicode case-folded label, then relative path as a deterministic tie
breaker; a count update never reorders siblings. A captured path outside the
configured vault remains diagnostic and displays its basename instead of an
empty/escaped relative path.

### 40.3 Scoreboard and Insights projections

`agent_session_scoreboards` is the durable owner of the five-row scorebar. It
contains cumulative delivered/read message census, current unread/stale census,
command/failure counts, active time, canonical token categories and vendor cost,
unique files, added/removed lines, and per-tool counts. Command and tool facts
credit once per canonical Operation identity across the whole team. Bash counts
as a command and is omitted from the repeated tool list. Failed file mutations
credit the path/tool but zero line changes. `operation_file_changes` retains the
canonical path and diff extents after raw payload expiry; unique files use the
path identity, not Operation count.

`ScoreboardDTO.tool_counts` is the decoded `tool_counts_json` map and
`tool_count` is exactly the sum of its values. `failed_command_count` is a
subset of `command_count`; `current_unread` and `current_stale` are returned as
unknown together when the sampler is unavailable. They are disjoint buckets:
`current_unread` is pending for at most 60 seconds and `current_stale` is
pending for more than 60 seconds, so their sum is at most delivered minus read.

The mailbox census is explicitly sampled. It keys each observed recipient copy
by `(external_message_id,recipient_actor_key)`, counts delivered once, and marks
read when the copy reports read or disappears after having been observed.
Unobserved deliveries remain unknowable. `message_census_state=sampled` is
therefore visible in `ScoreboardDTO`; the UI never presents the census as a
complete mail ledger. Current unread and stale (unread for more than 60 seconds)
are disjoint and can be lower than the true values.

The pinned renderer prints the token `Σ` row first so narrow-pane tail dropping
preserves the headline. It shows total, **fresh input**, output, cache read, and
cache write (the sum of 5m, 1h, and unclassified creation); the DTO retains all
four cache categories. Fresh input is `max(0, gross input - cache creation)`.
Total is exactly fresh input + output + cache read + cache creation, while the
displayed five figures reconcile without double-displaying cache creation. The
row appends `≈ $<amount>` when pricing is known and omits it when unknown. The
warning row begins with `⚠`. A blocked operation chip never invents a duration,
and a completed background job with zero captured bytes displays `(no output)`.
Subagent/team slot colors share one slot-number namespace but use two palettes;
the subagent palette excludes red and green, and teammate rows reuse the same
allocated slot rather than allocating a second family slot.

`daily_insight_rollups` preserves the contribution heatmap, weekday/hour punch
card, error series, and per-project 90-day series. One
`daily_insight_conversation_credit` row prevents distinct-Conversation double
counting for each project/provider/model slice. The three scope keys use the
same UTF-8 length-prefixed canonical encoding as usage rollups; the reserved
zero-length component means “not attributed,” never “all.” An unfiltered query
sums concrete slices and counts distinct Conversation IDs from the credit
table; it never adds already-aggregated “all” rows. The same canonical
Operation/active-time/usage/error transactions update these rollups.
`StatsDTO` never reconstructs them from expiring raw evidence.

Stats always returns the Pulse counters for trailing 7 days, trailing 30 days,
and all retained history from rollups. “Active” Conversation count requires an
AgentSession with `host_state IN ('starting','live','parked')`; it never uses
`ended_at IS NULL` alone, because lost legacy sessions can lack an end time.

### 40.4 Client-only and presentation contracts

- The keep-awake toggle is browser-only. Its boolean survives reloads in
  origin-scoped local storage key `baqylau.keep_awake`. While enabled and
  visible, the client requests `navigator.wakeLock('screen')`, shows held/lost
  state, and reacquires after `visibilitychange`; unsupported/refused silently
  degrades. It has no daemon state, endpoint, audit, or effect.
- Composer Up/Down recalls only successfully submitted messages from the local
  principal/device history, never drafts or interaction answers. Recall does
  not write until editing or send.
- The pinned queue combines requested `message_delivery` Operations with
  provider-observed queued prompts. Terminal-origin queued text creates an
  observed `message_delivery` Operation with `opener_state=missing`; identity
  correlation merges it with a later native prompt rather than duplicating it.
- The checked-in PWA manifest owns shortcuts `?new=1` and `?attn=1`. The former
  opens the new-session composer; the latter focuses the needs-you strip.
  In-app and service-worker badge values are the count of live Conversations
  needing attention. Every push payload carries `badge`; opening the app
  replaces it from the current Conversation overview snapshot. Asking count
  prefixes the tab title and selects the dotted favicon.
- The live-session strip is derived from the Conversation overview snapshot,
  shows every live session, groups needs-you before busy/running/idle, and sorts
  label then ID inside a group. The current session remains visible but dim.
- The session header keeps controls in this exact reach order: `✦ ✧ ⊜`, then
  `✎ ⇆ ◉ ↶ ■ ✕`, with destructive controls last. Every control remains visible
  when unavailable and uses one `gate(button,ok,why)` reason for disabled state.
  Fullscreen shows `⛶` while engaged; the connection dot remains visible once
  the connection is not green. These are presentation decisions, not styling
  details, and are covered by header reachability fixtures.
- An auxiliary actor row with no kind, description, slot, transcript, start,
  Node, Operation, or peer message is a `hidden_husk` presentation row and is
  omitted from ActorTrackDTO lists. Its evidence remains queryable in audit.
  Any one real field makes the row visible; a thin row stays dim and clickable.
- Ask-card assistant preamble is not a second hidden field. It is the ordinary
  immediately preceding assistant activity item, rendered once by the shared
  activity stream above the interaction card.
- Per-tab badges use only already materialized counts: open foreground command,
  background jobs, monitor jobs, unread mail, and active child/team tasks. Each
  badge value carries `scoped`; actor-scoped counts never fall back to the host
  count, so an actor with one job cannot display the host's nineteen.
- Namespace drafts settle their directory scope on blur. The server retains at
  most `NS_DRAFT_MAX=24` non-empty namespace drafts per principal, deleting the
  oldest settled draft in the same transaction when the cap is exceeded;
  unsettled/current drafts are never selected for pruning.
- Ghost probes are allowed only on a verified green host input box with no
  modal/red interaction region. The suggestion overlay has an inert base and
  cannot type into the TUI. The “typed” half of the comparison may be observed
  on any verified tab for that AgentSession, while the ghost half must come
  from the host-owned input box.

The push adapter and service worker share this closed payload; it is not an
arbitrary map:

```text
PushPayloadDTO = discriminatedOneOf<
  {type:alert,tag:text[1..200],delivery_id:UUID,intent_id:UUID,
   conversation_id:UUID,kind:enum(asking,done),title:text[1..200],
   body:text[0..1000],deep_link:text[1..2048],badge:integer[0..1000000],
   attention_transition_revision:Revision,expires_at:Timestamp?},
  {type:resolve,tag:text[1..200],conversation_id:UUID,
   resolved_through_revision:Revision,badge:integer[0..1000000]}
>
```

The service worker rejects another schema shape, shows at most one
notification per `tag`, applies `setAppBadge(badge)` when supported, and
uses `deep_link` only after same-origin validation. Notification click focuses
an existing same-origin client or opens that deep link; it never executes a
control action.

Both sender and service worker compute `tag = push_tag(conversation_id)` with
the same versioned function. Repeated asking/done alerts replace the prior
notification for that Conversation; a `resolve` payload closes that exact tag
without displaying a new banner. Delivery ID remains effect idempotency, not
browser-notification collapse identity.

### 40.5 Privileged local features and account cutover

`POST /api/v1/dictation/grants` reads the configured dictation credential only through
CredentialPort, applies the same configuration layering as provider process
snapshots, and mints a 60-second provider-restricted grant bound to principal,
device, and optional Conversation/project. Key terms are the stable deduplicated
union of configured global, project, and request terms; request terms outside
the authorized project scope fail. The raw credential is never returned or
stored in Observation/telemetry.

The daemon, not the browser, resolves and binds the dictation provider model,
sample rate, language, and final key-term union. The one-use response includes
the fully assembled provider request URL plus the resolved non-secret fields;
the client cannot substitute another model, sample rate, or query parameter.

`POST /api/v1/clipboard/files:resolve` is local-machine-only and follows a current
browser paste gesture. The adapter reads the macOS file-URL pasteboard once,
normalizes NFC basenames, and succeeds only when the ordered basename multiset
exactly matches the request. Every path must be an existing regular file,
contain no symlink escape, and be representable as a jailed Resource. Failure
returns no partial paths; the browser falls back to ordinary upload. Drag/drop
and the explicit attachment picker always upload.

`GET /api/v1/push-config` returns only the active VAPID public key and key ID. A
subscription request referencing a different/non-active key returns
`404 push_key_not_found` or `409 push_key_rotated` with the new public config.

Legacy account cutover reads `accounts.tsv`, the
`CLAUDE_SUBSCRIPTION_SLUG`/`CLAUDE_SUBSCRIPTION_LABEL` environment fields,
symlink-farm configuration targets, and `c1`/`c2` aliases through a versioned
`claude-subscription-v1` importer. It writes `legacy_account_mappings` plus
`accounts` and CredentialImport results in one machine transaction. Identity is
`(registry_fingerprint,legacy_slug,config_realpath)`. Alias rows may point to
one account but never create a second credential. Conflicting slug/realpath or
unreadable credential becomes `needs_review` and is excluded from automatic
migration. Relimit is enabled only after every eligible legacy entry maps to
exactly one active Account and fixture parity proves the same selection order.

### 40.6 Delivery and performance tradeoff decision

The second review correctly identifies Blob/fsync, trigger, and per-consumer
transaction amplification as the highest implementation risk. The retained
content-addressed Blob store, consumer isolation, structural feed,
materialized Activity, future schemas, and integrity triggers remain required
by the fixed product decision. V4 does not silently replace them with inline
payloads, nullable feed payloads, savepoint fan-in, or deferred-feature tables.

Before Phase 1 schema code is accepted, the Section 38.30 benchmark adds two
mandatory profiles:

1. **small-fact storm:** 200 Observations/s for 15 minutes with 1,000/s
   ten-second bursts, 95% payloads 0.5–8 KiB, all seven consumers, three feed
   scopes, and Activity enabled; record file/directory fsync count, Blob create
   latency, trigger VM time, writer admission, WAL growth, and GC backlog;
2. **composer churn:** ten clients editing at 4 writes/s for five minutes while
   command/assistant streams and alerts run; require no lost CAS/tombstone,
   bounded orphan growth, and the normal writer/SSE latency gates.

Failure blocks Phase 1 and returns to the product owner for an explicit design
revision; it is not permission for the implementor to weaken durability. The
tradeoff is accepted: a new contract field can touch DDL, schema registry,
OpenAPI, event reducers, storage, traceability, and tests. Checked-in generators
must update those mechanical artifacts together, while review remains focused
on the one semantic owner.

The smallest deployable replacement is now explicit: Phase 2a ships the daemon
plus the Claude Code/local-backend read model and replaces the legacy list,
session, Activity, memory, tasks, scoreboard, and Insights read paths while all
controls still route to legacy. It must run for seven days with parity before
Phase 2b removes those legacy read paths. Phase 3 then transfers one control
family at a time. This yields a real removed subsystem before the full control
plane is complete.

### 40.7 Fifth clean-install DDL unit

The following SQL executes after Section 38.39 in the same version-1 clean
install transaction. It closes the concrete storage gaps found by the second
and third legacy-coverage reviews; behavior remains owned by the earlier
domain/application sections rather than by this physical-schema block.

```sql
-- 40 SECOND REVIEW BEGIN
CREATE TABLE notification_origin_config (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  public_base_url TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  updated_at REAL NOT NULL
);

CREATE TABLE notification_cancel_reason_definitions (
  cancel_reason TEXT PRIMARY KEY,
  owner_kind TEXT NOT NULL CHECK(owner_kind IN ('core','provider','extension')),
  owner_id TEXT NOT NULL,
  evidence_strength INTEGER NOT NULL CHECK(evidence_strength BETWEEN 0 AND 1000),
  description TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  registered_at REAL NOT NULL
);
INSERT INTO notification_cancel_reason_definitions
  (cancel_reason,owner_kind,owner_id,evidence_strength,description,registered_at)
VALUES
  ('truth_changed','core','baqylau',1000,'Underlying alert truth became false',0),
  ('answered','core','baqylau',950,'A durable or bounded parsed answer was observed',0),
  ('session_ended','core','baqylau',900,'Agent session ended',0),
  ('muted','core','baqylau',850,'Conversation or global notification setting muted it',0),
  ('tab_moved','core','baqylau',800,'Provider tab moved away from the alerted state',0),
  ('composing','core','baqylau',750,'User is composing in the relevant input',0),
  ('tab_focused','core','baqylau',500,'Relevant terminal tab is focused',0),
  ('web_viewing','core','baqylau',450,'Relevant web view is visible',0),
  ('device_active','core','baqylau',300,'A device is active but not necessarily viewing',0),
  ('expired','core','baqylau',100,'Delivery or truth window expired',0),
  ('unknown','core','baqylau',0,'No stronger registered reason is proven',0);
CREATE TRIGGER notification_intent_cancel_reason_insert
BEFORE INSERT ON notification_intents
WHEN NEW.cancel_reason IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM notification_cancel_reason_definitions d
    WHERE d.cancel_reason=NEW.cancel_reason AND d.enabled=1)
  THEN RAISE(ABORT,'notification_cancel_reason_unregistered') END;
END;
CREATE TRIGGER notification_intent_cancel_reason_update
BEFORE UPDATE OF cancel_reason ON notification_intents
WHEN NEW.cancel_reason IS NOT NULL BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM notification_cancel_reason_definitions d
    WHERE d.cancel_reason=NEW.cancel_reason AND d.enabled=1)
  THEN RAISE(ABORT,'notification_cancel_reason_unregistered') END;
END;

CREATE TABLE notification_retraction_policy (
  cancel_reason TEXT NOT NULL
    REFERENCES notification_cancel_reason_definitions(cancel_reason)
      ON DELETE RESTRICT,
  notification_kind TEXT NOT NULL
    CHECK(notification_kind IN ('asking','done')),
  retract_delivered INTEGER NOT NULL CHECK(retract_delivered IN (0,1)),
  marks_seen INTEGER NOT NULL CHECK(marks_seen IN (0,1)),
  PRIMARY KEY(cancel_reason,notification_kind),
  CHECK(marks_seen = 0 OR notification_kind = 'done')
);
INSERT INTO notification_retraction_policy
  (cancel_reason,notification_kind,retract_delivered,marks_seen) VALUES
  ('truth_changed','asking',1,0), ('truth_changed','done',1,0),
  ('answered','asking',1,0),      ('answered','done',1,0),
  ('session_ended','asking',1,0), ('session_ended','done',1,0),
  ('muted','asking',1,0),         ('muted','done',1,0),
  ('tab_moved','asking',1,0),     ('tab_moved','done',1,0),
  ('composing','asking',1,0),     ('composing','done',1,0),
  ('tab_focused','asking',0,0),   ('tab_focused','done',1,1),
  ('web_viewing','asking',0,0),   ('web_viewing','done',1,1),
  ('device_active','asking',0,0), ('device_active','done',0,0),
  ('expired','asking',0,0),       ('expired','done',0,0),
  ('unknown','asking',0,0),       ('unknown','done',0,0);
CREATE TRIGGER notification_retraction_policy_no_update
BEFORE UPDATE ON notification_retraction_policy BEGIN
  SELECT RAISE(ABORT, 'notification_retraction_policy_is_static');
END;
CREATE TRIGGER notification_retraction_policy_no_delete
BEFORE DELETE ON notification_retraction_policy BEGIN
  SELECT RAISE(ABORT, 'notification_retraction_policy_is_static');
END;

CREATE TABLE security_audit (
  id TEXT PRIMARY KEY,
  principal_id TEXT,
  credential_id TEXT,
  device_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  outcome TEXT NOT NULL CHECK(outcome IN
    ('accepted','rejected','succeeded','failed','indeterminate')),
  reason_code TEXT,
  request_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
  recorded_at REAL NOT NULL
);
CREATE INDEX security_audit_target_time
  ON security_audit(target_type,target_id,recorded_at DESC,id DESC);
CREATE INDEX security_audit_principal_time
  ON security_audit(principal_id,recorded_at DESC,id DESC);
CREATE TRIGGER security_audit_no_update BEFORE UPDATE ON security_audit BEGIN
  SELECT RAISE(ABORT, 'security_audit_is_append_only');
END;
CREATE TRIGGER security_audit_no_delete BEFORE DELETE ON security_audit BEGIN
  SELECT RAISE(ABORT, 'security_audit_is_append_only');
END;

CREATE TABLE health_errors (
  id TEXT PRIMARY KEY,
  health_part TEXT NOT NULL CHECK(health_part IN
    ('daemon','database','blob','supervisor','provider_edges','projections',
     'notifications','ingestion')),
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN
    ('info','warning','error','critical')),
  title TEXT NOT NULL,
  explanation TEXT NOT NULL,
  entity_type TEXT,
  entity_id TEXT,
  evidence_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(evidence_ids_json)),
  traceback_text TEXT,
  remediation TEXT,
  detected_at REAL NOT NULL,
  resolved_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK((entity_type IS NULL) = (entity_id IS NULL)),
  CHECK(resolved_at IS NULL OR resolved_at >= detected_at)
);
CREATE INDEX health_error_open_severity
  ON health_errors(severity,detected_at DESC,id DESC)
  WHERE resolved_at IS NULL;

CREATE TABLE ingestion_gaps (
  id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL,
  source_id TEXT,
  started_at REAL NOT NULL,
  ended_at REAL,
  affected_entities_json TEXT NOT NULL DEFAULT '[]'
    CHECK(json_valid(affected_entities_json)),
  cause TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK(ended_at IS NULL OR ended_at >= started_at)
);
CREATE INDEX ingestion_gap_range
  ON ingestion_gaps(started_at DESC,id DESC);

CREATE TABLE surface_telemetry (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  surface_id TEXT NOT NULL,
  client_record_id TEXT NOT NULL,
  family TEXT NOT NULL CHECK(family IN
    ('sse_lifecycle','js_error','js_rejection','boot',
     'notification_receipt','attachment_paste')),
  event_name TEXT NOT NULL CHECK(event_name IN
    ('sse.open','sse.drop','js.error','js.reject','boot',
     'notify.recv','attach.paste')),
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  agent_session_id TEXT REFERENCES agent_sessions(id) ON DELETE SET NULL,
  conn_info_json TEXT NOT NULL CHECK(json_valid(conn_info_json)),
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
  client_timestamp REAL NOT NULL,
  received_at REAL NOT NULL,
  UNIQUE(surface_id, client_record_id),
  CHECK((family='sse_lifecycle' AND event_name IN ('sse.open','sse.drop')) OR
        (family='js_error' AND event_name='js.error') OR
        (family='js_rejection' AND event_name='js.reject') OR
        (family='boot' AND event_name='boot') OR
        (family='notification_receipt' AND event_name='notify.recv') OR
        (family='attachment_paste' AND event_name='attach.paste'))
);
CREATE INDEX surface_telemetry_family_time
  ON surface_telemetry(family, event_name, received_at, id);

CREATE TABLE message_delivery_details (
  operation_id TEXT PRIMARY KEY REFERENCES operations(id) ON DELETE CASCADE,
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  text_blob_digest TEXT NOT NULL REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  resource_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(resource_ids_json)),
  client_message_id TEXT NOT NULL,
  parked_policy TEXT NOT NULL CHECK(parked_policy IN ('reject','resume')),
  runtime_request_json TEXT CHECK(runtime_request_json IS NULL OR
                                  json_valid(runtime_request_json)),
  detail_state TEXT NOT NULL CHECK(detail_state IN
    ('accepted','waiting_for_resume','relaunching','dispatching',
     'queued_at_provider','observed_in_history','delivered','cancelled',
     'lost','unknown')),
  resume_operation_id TEXT REFERENCES operations(id) ON DELETE RESTRICT,
  provider_delivery_key TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(agent_session_id, client_message_id),
  CHECK((parked_policy = 'resume') = (runtime_request_json IS NOT NULL))
);
CREATE TRIGGER message_delivery_operation_match
BEFORE INSERT ON message_delivery_details BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM operations o JOIN agent_sessions s
      ON s.id = NEW.agent_session_id
    WHERE o.id = NEW.operation_id
      AND o.kind = 'message_delivery'
      AND o.agent_session_id = NEW.agent_session_id
      AND o.conversation_id = s.conversation_id)
  THEN RAISE(ABORT, 'message_delivery_operation_mismatch') END;
END;
CREATE TRIGGER message_delivery_blob_ref_insert
AFTER INSERT ON message_delivery_details BEGIN
  INSERT INTO blob_references(owner_table,owner_key,role,digest,
                              retention_class,created_at)
  SELECT 'message_delivery_details',NEW.operation_id,'text',
         NEW.text_blob_digest,'semantic_content',o.started_at
  FROM operations o WHERE o.id = NEW.operation_id;
END;
CREATE TRIGGER message_delivery_blob_ref_delete
AFTER DELETE ON message_delivery_details BEGIN
  DELETE FROM blob_references
  WHERE owner_table='message_delivery_details' AND owner_key=OLD.operation_id;
END;

CREATE TABLE actor_track_context_state (
  actor_track_id TEXT PRIMARY KEY
    REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  context_window_tokens INTEGER CHECK(context_window_tokens >= 0),
  context_used_tokens INTEGER CHECK(context_used_tokens >= 0),
  current_model TEXT,
  occupancy_state TEXT NOT NULL CHECK(occupancy_state IN
    ('observed','stale','unavailable','unknown')),
  source_kind TEXT NOT NULL CHECK(source_kind IN
    ('transcript','provider_api','statusline','imported','session_projection')),
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  CHECK(context_window_tokens IS NULL OR context_used_tokens IS NULL OR
        context_used_tokens <= context_window_tokens)
);

CREATE TABLE actor_track_runtime_revisions (
  id TEXT PRIMARY KEY,
  actor_track_id TEXT NOT NULL
    REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  requested_provider_id TEXT,
  requested_model TEXT,
  requested_effort TEXT,
  effective_provider_id TEXT,
  effective_model TEXT,
  effective_effort TEXT,
  reason TEXT NOT NULL CHECK(reason IN
    ('start','resume','user_change','provider_fallback','fork','observation')),
  source_registration_id TEXT REFERENCES source_registrations(id),
  source_epoch INTEGER NOT NULL CHECK(source_epoch >= 0),
  source_ordinal INTEGER NOT NULL CHECK(source_ordinal >= 0),
  source_position TEXT,
  observed_at REAL NOT NULL,
  provenance_id TEXT NOT NULL
);
CREATE INDEX actor_runtime_latest
  ON actor_track_runtime_revisions
    (actor_track_id,source_epoch DESC,source_ordinal DESC,id DESC);

CREATE TABLE operation_file_changes (
  operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  actor_track_id TEXT REFERENCES conversation_actor_tracks(id) ON DELETE SET NULL,
  resource_id TEXT REFERENCES resources(id) ON DELETE SET NULL,
  workspace_ref TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  change_kind TEXT NOT NULL CHECK(change_kind IN
    ('read','edit','write','multi_edit','notebook_edit','other')),
  succeeded INTEGER NOT NULL CHECK(succeeded IN (0,1)),
  added_lines INTEGER NOT NULL DEFAULT 0 CHECK(added_lines >= 0),
  removed_lines INTEGER NOT NULL DEFAULT 0 CHECK(removed_lines >= 0),
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(operation_id, ordinal),
  CHECK(succeeded = 1 OR (added_lines = 0 AND removed_lines = 0))
);
CREATE INDEX file_changes_session_path
  ON operation_file_changes(conversation_id,canonical_path,operation_id);

CREATE TABLE agent_session_scoreboards (
  agent_session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
  delivered_messages INTEGER CHECK(delivered_messages >= 0),
  read_messages INTEGER CHECK(read_messages >= 0),
  current_unread INTEGER CHECK(current_unread >= 0),
  current_stale INTEGER CHECK(current_stale >= 0),
  message_census_state TEXT NOT NULL CHECK(message_census_state IN
    ('observed','sampled','unavailable','unknown')),
  command_count INTEGER NOT NULL DEFAULT 0 CHECK(command_count >= 0),
  failed_command_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_command_count >= 0),
  active_ms INTEGER NOT NULL DEFAULT 0 CHECK(active_ms >= 0),
  input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(input_tokens >= 0),
  fresh_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK(fresh_input_tokens >= 0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK(output_tokens >= 0),
  cache_read_tokens INTEGER NOT NULL DEFAULT 0 CHECK(cache_read_tokens >= 0),
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_5m_tokens >= 0),
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_1h_tokens >= 0),
  cache_create_unclassified_tokens INTEGER NOT NULL DEFAULT 0
    CHECK(cache_create_unclassified_tokens >= 0),
  total_tokens INTEGER NOT NULL DEFAULT 0 CHECK(total_tokens >= 0),
  vendor_cost_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(vendor_cost_json)),
  files_touched INTEGER NOT NULL DEFAULT 0 CHECK(files_touched >= 0),
  added_lines INTEGER NOT NULL DEFAULT 0 CHECK(added_lines >= 0),
  removed_lines INTEGER NOT NULL DEFAULT 0 CHECK(removed_lines >= 0),
  tool_counts_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(tool_counts_json)),
  source_revision INTEGER NOT NULL DEFAULT 0 CHECK(source_revision >= 0),
  freshness TEXT NOT NULL CHECK(freshness IN ('fresh','stale','unknown')),
  updated_at REAL NOT NULL,
  CHECK(read_messages IS NULL OR delivered_messages IS NULL OR
        read_messages <= delivered_messages),
  CHECK(current_unread IS NULL OR current_stale IS NULL OR
        delivered_messages IS NULL OR read_messages IS NULL OR
        current_unread + current_stale <= delivered_messages - read_messages),
  CHECK(fresh_input_tokens = MAX(0,input_tokens-cache_create_5m_tokens-
        cache_create_1h_tokens-cache_create_unclassified_tokens)),
  CHECK(total_tokens = input_tokens + output_tokens + cache_read_tokens)
);

CREATE TABLE daily_insight_conversation_credit (
  day TEXT NOT NULL,
  project_scope_key TEXT NOT NULL,
  provider_scope_key TEXT NOT NULL,
  model_scope_key TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  PRIMARY KEY(day,project_scope_key,provider_scope_key,model_scope_key,
              conversation_id)
);
CREATE TABLE daily_insight_rollups (
  day TEXT NOT NULL,
  project_scope_key TEXT NOT NULL,
  provider_scope_key TEXT NOT NULL,
  model_scope_key TEXT NOT NULL,
  weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
  hour INTEGER NOT NULL CHECK(hour BETWEEN 0 AND 23),
  conversation_count INTEGER NOT NULL DEFAULT 0 CHECK(conversation_count >= 0),
  operation_count INTEGER NOT NULL DEFAULT 0 CHECK(operation_count >= 0),
  active_ms INTEGER NOT NULL DEFAULT 0 CHECK(active_ms >= 0),
  token_count INTEGER NOT NULL DEFAULT 0 CHECK(token_count >= 0),
  info_count INTEGER NOT NULL DEFAULT 0 CHECK(info_count >= 0),
  warning_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_count >= 0),
  error_count INTEGER NOT NULL DEFAULT 0 CHECK(error_count >= 0),
  critical_count INTEGER NOT NULL DEFAULT 0 CHECK(critical_count >= 0),
  source_revision INTEGER NOT NULL DEFAULT 0 CHECK(source_revision >= 0),
  PRIMARY KEY(day,project_scope_key,provider_scope_key,model_scope_key,hour)
);
CREATE INDEX insight_project_range
  ON daily_insight_rollups
    (project_scope_key,provider_scope_key,model_scope_key,day,hour);

CREATE TABLE dictation_grants (
  id TEXT PRIMARY KEY,
  token_sha256 TEXT NOT NULL UNIQUE CHECK(length(token_sha256)=64 AND
    token_sha256 NOT GLOB '*[^0-9a-f]*'),
  provider_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
  project_ref TEXT,
  language TEXT,
  model TEXT NOT NULL,
  sample_rate_hz INTEGER NOT NULL CHECK(sample_rate_hz BETWEEN 8000 AND 192000),
  key_terms_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(key_terms_json)),
  state TEXT NOT NULL CHECK(state IN ('active','consumed','expired','revoked')),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL CHECK(expires_at > created_at),
  consumed_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE INDEX dictation_grant_expiry ON dictation_grants(state,expires_at,id);

CREATE TABLE legacy_account_mappings (
  registry_fingerprint TEXT NOT NULL,
  legacy_slug TEXT NOT NULL,
  legacy_label TEXT,
  config_realpath TEXT NOT NULL,
  alias_name TEXT NOT NULL DEFAULT '',
  account_id TEXT REFERENCES accounts(id) ON DELETE RESTRICT,
  credential_import_state TEXT NOT NULL CHECK(credential_import_state IN
    ('imported','needs_review','unavailable','rejected')),
  source_ref TEXT NOT NULL,
  imported_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(registry_fingerprint,legacy_slug,config_realpath,alias_name)
);
CREATE INDEX legacy_account_target
  ON legacy_account_mappings(account_id,credential_import_state,legacy_slug);

CREATE TABLE memory_touches (
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  canonical_path TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  note_name TEXT NOT NULL,
  verb TEXT NOT NULL CHECK(verb IN ('read','update','write')),
  actor_track_id TEXT REFERENCES conversation_actor_tracks(id) ON DELETE SET NULL,
  touch_count INTEGER NOT NULL DEFAULT 1 CHECK(touch_count > 0),
  first_touched_at REAL NOT NULL,
  last_touched_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  PRIMARY KEY(agent_session_id,canonical_path)
);
CREATE INDEX memory_touch_newest
  ON memory_touches(agent_session_id,last_touched_at DESC,canonical_path);

CREATE TABLE memory_searches (
  id TEXT PRIMARY KEY,
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('search','query')),
  subcommand TEXT NOT NULL,
  query TEXT NOT NULL,
  rewrites_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(rewrites_json)),
  search_count INTEGER NOT NULL DEFAULT 1 CHECK(search_count > 0),
  answer_state TEXT NOT NULL CHECK(answer_state IN
    ('captured','ambiguous_multi_search','background_unavailable','partial')),
  first_searched_at REAL NOT NULL,
  last_searched_at REAL NOT NULL,
  provenance_id TEXT NOT NULL,
  UNIQUE(agent_session_id,kind,subcommand,query)
);
CREATE INDEX memory_search_newest
  ON memory_searches(agent_session_id,last_searched_at DESC,id);

CREATE TABLE memory_search_hits (
  search_id TEXT NOT NULL REFERENCES memory_searches(id) ON DELETE CASCADE,
  rank INTEGER NOT NULL CHECK(rank BETWEEN 0 AND 49),
  canonical_path TEXT,
  relative_path TEXT,
  note_name TEXT,
  line_number INTEGER CHECK(line_number > 0),
  title TEXT,
  score REAL,
  snippet_digest TEXT REFERENCES blob_objects(digest) ON DELETE RESTRICT,
  viewable INTEGER NOT NULL CHECK(viewable IN (0,1)),
  PRIMARY KEY(search_id,rank)
);
CREATE TRIGGER memory_hit_blob_ref_insert
AFTER INSERT ON memory_search_hits WHEN NEW.snippet_digest IS NOT NULL BEGIN
  INSERT INTO blob_references(owner_table,owner_key,role,digest,
                              retention_class,created_at)
  SELECT 'memory_search_hits',NEW.search_id || ':' || NEW.rank,'snippet',
         NEW.snippet_digest,'semantic_content',s.last_searched_at
  FROM memory_searches s WHERE s.id=NEW.search_id;
END;
CREATE TRIGGER memory_hit_blob_ref_delete
AFTER DELETE ON memory_search_hits BEGIN
  DELETE FROM blob_references
  WHERE owner_table='memory_search_hits'
    AND owner_key=OLD.search_id || ':' || OLD.rank;
END;

CREATE TABLE terminal_bindings (
  id TEXT PRIMARY KEY,
  agent_session_attempt_id TEXT NOT NULL
    REFERENCES agent_session_attempts(id) ON DELETE CASCADE,
  agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  terminal_adapter_id TEXT NOT NULL,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  window_id TEXT NOT NULL,
  user_var_tag TEXT NOT NULL,
  binding_kind TEXT NOT NULL CHECK(binding_kind IN
    ('host','nested','anchorless')),
  state TEXT NOT NULL CHECK(state IN
    ('grace','verified','stale','lost','closed','unknown')),
  frontmost_state TEXT NOT NULL DEFAULT 'unknown' CHECK(frontmost_state IN
    ('frontmost','background','unknown')),
  tab_focus_state TEXT NOT NULL DEFAULT 'unknown' CHECK(tab_focus_state IN
    ('focused','unfocused','unknown')),
  observed_at REAL NOT NULL,
  last_verified_at REAL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  UNIQUE(terminal_adapter_id,backend_id,window_id),
  UNIQUE(id,revision)
);
CREATE INDEX terminal_binding_session_state
  ON terminal_bindings(agent_session_id,state,observed_at DESC,id);
CREATE TRIGGER terminal_binding_attempt_scope
BEFORE INSERT ON terminal_bindings BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM agent_session_attempts a
    WHERE a.id=NEW.agent_session_attempt_id
      AND a.agent_session_id=NEW.agent_session_id
      AND a.backend_id=NEW.backend_id)
  THEN RAISE(ABORT,'terminal_binding_attempt_scope_mismatch') END;
END;

CREATE TABLE pane_state (
  agent_session_id TEXT PRIMARY KEY
    REFERENCES agent_sessions(id) ON DELETE CASCADE,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  terminal_binding_id TEXT NOT NULL REFERENCES terminal_bindings(id)
    ON DELETE RESTRICT,
  binding_revision INTEGER NOT NULL CHECK(binding_revision >= 0),
  visible INTEGER NOT NULL CHECK(visible IN (0,1)),
  percentage INTEGER NOT NULL CHECK(percentage BETWEEN 10 AND 90),
  previous_visible INTEGER NOT NULL CHECK(previous_visible IN (0,1)),
  previous_percentage INTEGER NOT NULL CHECK(previous_percentage BETWEEN 10 AND 90),
  cell_step INTEGER NOT NULL DEFAULT 4 CHECK(cell_step BETWEEN 1 AND 40),
  bias_percent INTEGER NOT NULL DEFAULT 25 CHECK(bias_percent BETWEEN 0 AND 100),
  verification TEXT NOT NULL CHECK(verification IN
    ('verified','failed','indeterminate')),
  renderer_revision INTEGER NOT NULL DEFAULT 0 CHECK(renderer_revision >= 0),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  updated_at REAL NOT NULL,
  FOREIGN KEY(terminal_binding_id,binding_revision)
    REFERENCES terminal_bindings(id,revision) ON DELETE RESTRICT
);

CREATE TABLE attention_projection (
  scope_type TEXT NOT NULL CHECK(scope_type IN
    ('conversation','agent_session','actor_track')),
  scope_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  state TEXT NOT NULL,
  cause_operation_id TEXT REFERENCES operations(id) ON DELETE SET NULL,
  source_transition_id TEXT REFERENCES attention_transitions(id) ON DELETE SET NULL,
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  projection_revision INTEGER NOT NULL DEFAULT 0 CHECK(projection_revision >= 0),
  freshness TEXT NOT NULL CHECK(freshness IN
    ('fresh','stale','unavailable','unknown')),
  updated_at REAL NOT NULL,
  PRIMARY KEY(scope_type,scope_id)
);
CREATE INDEX attention_projection_conversation
  ON attention_projection(conversation_id,state,projection_revision);

CREATE TABLE conversation_overviews (
  principal_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  title_search TEXT NOT NULL,
  native_session_ids_search TEXT NOT NULL,
  provider_ids_json TEXT NOT NULL CHECK(json_valid(provider_ids_json)),
  account_ids_json TEXT NOT NULL CHECK(json_valid(account_ids_json)),
  group_id TEXT,
  resume_provider_id TEXT,
  resume_tool TEXT CHECK(resume_tool IS NULL OR resume_tool IN
    ('claude','codex','other')),
  resume_command TEXT,
  context_used_tokens INTEGER CHECK(context_used_tokens >= 0),
  context_window_tokens INTEGER CHECK(context_window_tokens >= 0),
  git_branch_or_detached_sha TEXT,
  git_owner_root TEXT,
  git_dirty TEXT NOT NULL CHECK(git_dirty IN ('dirty','clean','unknown')),
  command_count INTEGER NOT NULL DEFAULT 0 CHECK(command_count >= 0),
  token_count INTEGER NOT NULL DEFAULT 0 CHECK(token_count >= 0),
  costs_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(costs_json)),
  last_activity_at REAL,
  open_operation_count INTEGER NOT NULL DEFAULT 0 CHECK(open_operation_count >= 0),
  unread_notification_count INTEGER NOT NULL DEFAULT 0
    CHECK(unread_notification_count >= 0),
  warning_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_count >= 0),
  source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  updated_at REAL NOT NULL,
  PRIMARY KEY(principal_id,conversation_id)
);
CREATE INDEX conversation_overview_updated
  ON conversation_overviews(principal_id,last_activity_at DESC,conversation_id DESC);
CREATE INDEX conversation_overview_search
  ON conversation_overviews(principal_id,title_search,native_session_ids_search,
                            conversation_id);

CREATE TABLE agent_session_view_preferences (
  agent_session_id TEXT PRIMARY KEY REFERENCES agent_sessions(id) ON DELETE CASCADE,
  view_mode TEXT NOT NULL DEFAULT 'default' CHECK(view_mode IN
    ('verbose','default','focus')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  last_client_mutation_id TEXT,
  updated_by_principal_id TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE child_launch_correlations (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE RESTRICT,
  parent_agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id)
    ON DELETE CASCADE,
  launch_scope TEXT NOT NULL,
  tool_use_id TEXT NOT NULL,
  description TEXT NOT NULL,
  task_kind TEXT,
  operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  fifo_ordinal INTEGER NOT NULL CHECK(fifo_ordinal >= 0),
  state TEXT NOT NULL CHECK(state IN
    ('queued','bound','cancelled','expired')),
  child_actor_key TEXT,
  created_at REAL NOT NULL,
  bound_at REAL,
  UNIQUE(parent_agent_session_id,tool_use_id),
  UNIQUE(parent_agent_session_id,launch_scope,fifo_ordinal)
);
CREATE INDEX child_launch_fifo
  ON child_launch_correlations(parent_agent_session_id,launch_scope,state,
                               fifo_ordinal,id);

CREATE TABLE otlp_receipts (
  listener_instance_id TEXT NOT NULL,
  receipt_sequence INTEGER NOT NULL CHECK(receipt_sequence > 0),
  content_encoding TEXT,
  compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes >= 0),
  decompressed_bytes INTEGER CHECK(decompressed_bytes >= 0),
  parse_state TEXT NOT NULL CHECK(parse_state IN
    ('accepted','malformed','too_large','unsupported')),
  window_start_unix_nano INTEGER,
  window_end_unix_nano INTEGER,
  health_error_id TEXT REFERENCES health_errors(id) ON DELETE SET NULL,
  received_at REAL NOT NULL,
  PRIMARY KEY(listener_instance_id,receipt_sequence)
);
CREATE TABLE otlp_listener_state (
  listener_instance_id TEXT PRIMARY KEY,
  next_receipt_sequence INTEGER NOT NULL CHECK(next_receipt_sequence > 0),
  started_at REAL NOT NULL,
  last_receipt_at REAL
);

CREATE TABLE diagnostic_suppressions (
  suppression_id TEXT PRIMARY KEY,
  signature_kind TEXT NOT NULL,
  signature_value TEXT NOT NULL,
  scope_type TEXT NOT NULL CHECK(scope_type IN
    ('machine','provider','backend','conversation','agent_session')),
  scope_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_by_principal_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(signature_kind,signature_value,scope_type,scope_id)
);
CREATE INDEX diagnostic_suppression_lookup
  ON diagnostic_suppressions(enabled,signature_kind,signature_value,
                             scope_type,scope_id);

CREATE TABLE workspace_git_identity (
  backend_id TEXT NOT NULL REFERENCES backends(id) ON DELETE CASCADE,
  workspace_ref TEXT NOT NULL,
  branch_or_detached_sha TEXT,
  worktree_root TEXT,
  owner_root TEXT,
  state TEXT NOT NULL CHECK(state IN
    ('observed','not_git','unknown','unavailable')),
  observed_at REAL NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
  PRIMARY KEY(backend_id,workspace_ref)
);

CREATE TABLE tab_paint_state (
  terminal_binding_id TEXT PRIMARY KEY REFERENCES terminal_bindings(id)
    ON DELETE CASCADE,
  verified_presentation_json TEXT NOT NULL
    CHECK(json_valid(verified_presentation_json)),
  verified_attempt_id TEXT NOT NULL,
  verified_at REAL NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE TABLE tab_paint_attempts (
  id TEXT PRIMARY KEY,
  terminal_binding_id TEXT NOT NULL REFERENCES terminal_bindings(id)
    ON DELETE CASCADE,
  requested_presentation_json TEXT NOT NULL
    CHECK(json_valid(requested_presentation_json)),
  outcome TEXT NOT NULL CHECK(outcome IN
    ('applied','skipped_already_verified','skipped_unreachable','failed')),
  error_code TEXT,
  attempted_at REAL NOT NULL,
  completed_at REAL,
  CHECK(completed_at IS NULL OR completed_at >= attempted_at)
);
CREATE INDEX tab_paint_attempt_binding_time
  ON tab_paint_attempts(terminal_binding_id,attempted_at DESC,id DESC);

-- Fourth-review schema closure: authentication, account pricing, and
-- actor-scoped scoreboards. These are machine-wide tables; no Conversation
-- receives its own database.
CREATE TABLE principals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('human','service','edge','terminal','remote_agent')),
  display_name TEXT,
  state TEXT NOT NULL CHECK(state IN ('active','disabled','revoked')),
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE principal_role_bindings (
  principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('viewer','editor','driver','admin')),
  granted_at REAL NOT NULL,
  revoked_at REAL,
  PRIMARY KEY(principal_id,role),
  CHECK(revoked_at IS NULL OR revoked_at >= granted_at)
);
CREATE TABLE auth_credentials (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN
    ('unix_peer','browser_session','bearer','client_certificate','invitation')),
  secret_digest TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL,
  revoked_at REAL,
  UNIQUE(kind,secret_digest)
);
CREATE TABLE browser_sessions (
  id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  device_id TEXT NOT NULL,
  secret_digest TEXT NOT NULL UNIQUE,
  csrf_digest TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  absolute_expires_at REAL NOT NULL,
  idle_expires_at REAL NOT NULL,
  revoked_at REAL
);
CREATE TABLE certificate_authorities (
  id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  pem_blob TEXT NOT NULL,
  created_at REAL NOT NULL,
  revoked_at REAL
);
CREATE TABLE certificate_revocations (
  serial TEXT PRIMARY KEY,
  authority_id TEXT NOT NULL REFERENCES certificate_authorities(id) ON DELETE RESTRICT,
  revoked_at REAL NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE invitation_credentials (
  id TEXT PRIMARY KEY,
  token_digest TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  consumed_at REAL,
  consumed_by TEXT REFERENCES principals(id) ON DELETE SET NULL
);
CREATE TABLE bootstrap_credentials (
  id TEXT PRIMARY KEY,
  token_digest TEXT NOT NULL UNIQUE,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  consumed_at REAL
);

CREATE TABLE model_price_epochs (
  provider_id TEXT NOT NULL,
  model_prefix TEXT NOT NULL,
  pricing_epoch TEXT NOT NULL,
  valid_from REAL NOT NULL,
  valid_until REAL,
  input_per_million INTEGER,
  output_per_million INTEGER,
  cache_read_multiplier_milli INTEGER,
  cache_create_5m_multiplier_milli INTEGER,
  cache_create_1h_multiplier_milli INTEGER,
  cache_create_unclassified_multiplier_milli INTEGER,
  PRIMARY KEY(provider_id,model_prefix,pricing_epoch),
  CHECK(valid_until IS NULL OR valid_until > valid_from),
  CHECK(input_per_million IS NULL OR input_per_million >= 0),
  CHECK(output_per_million IS NULL OR output_per_million >= 0)
);
CREATE INDEX model_price_epoch_lookup
  ON model_price_epochs(provider_id,model_prefix,valid_from DESC);

CREATE TABLE actor_track_scoreboards (
  actor_track_id TEXT PRIMARY KEY REFERENCES conversation_actor_tracks(id) ON DELETE CASCADE,
  delivered_messages INTEGER,
  read_messages INTEGER,
  current_unread INTEGER,
  current_stale INTEGER,
  message_census_state TEXT NOT NULL,
  command_count INTEGER NOT NULL DEFAULT 0,
  failed_command_count INTEGER NOT NULL DEFAULT 0,
  active_ms INTEGER NOT NULL DEFAULT 0,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  fresh_input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_5m_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_1h_tokens INTEGER NOT NULL DEFAULT 0,
  cache_create_unclassified_tokens INTEGER NOT NULL DEFAULT 0,
  total_tokens INTEGER NOT NULL DEFAULT 0,
  vendor_cost_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(vendor_cost_json)),
  source_revision INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL
);

UPDATE schema_migrations
SET sha256 = 'd55ba54271124062ffd2cc07dbaa981fa3c168c6a76042c233d86ca37e2e7804'
WHERE version = 1;
UPDATE schema_metadata
SET clean_install_sha256 =
  'd55ba54271124062ffd2cc07dbaa981fa3c168c6a76042c233d86ca37e2e7804',
    updated_at = unixepoch('subsec')
WHERE singleton = 1;
PRAGMA user_version = 1;
-- 40 SECOND REVIEW END
```

The final version-1 schema digest is computed across Foundation + Section 38.27
+ Finalization + Future Workflow + Second Review SQL, in that order, with the
same UTF-8/LF, marker-removal, and digest-zero normalization rule. The final
five-unit version-1 digest is
`d55ba54271124062ffd2cc07dbaa981fa3c168c6a76042c233d86ca37e2e7804`;
the clean database must contain that value in both digest columns.

### 40.8 Closed services, ports, live-frame models, and access patterns

The additions above use these exact application/storage contracts. `_tx`
methods join the supplied unit of work and do not commit. Reads never mutate.

```python
class TaskPreferenceStore(Protocol):
    def dismiss_completed_tx(self, uow: ConversationUnitOfWork,
                             command: TaskDismissCommand,
                             principal_id: UUID) -> TaskDismissal: ...

class MemoryStore(Protocol):
    def record_touch_tx(self, uow: ConversationUnitOfWork,
                        fact: MemoryTouchFact) -> MemoryTouch: ...
    def replace_search_tx(self, uow: ConversationUnitOfWork,
                          fact: MemorySearchFact,
                          hits: tuple[MemorySearchHitFact, ...]) \
            -> MemorySearch: ...
    def read_snapshot(self, view: ReadView, agent_session_id: UUID) \
            -> MemorySnapshot | NotFound: ...
    def read_scope(self, view: ReadView, agent_session_id: UUID) \
            -> MemoryScope | NotFound: ...

class MemoryVaultPort(Protocol):
    def read_note(self, scope: MemoryScope,
                  selector: MemoryNoteSelector) -> MemoryNoteResult: ...

class ActorFacetStore(Protocol):
    def observe_context_tx(self, uow: ConversationUnitOfWork,
                           fact: ActorContextFact) -> ActorContextState: ...
    def append_runtime_tx(self, uow: ConversationUnitOfWork,
                          fact: ActorRuntimeFact) -> ActorRuntimeRevision: ...

class ScoreboardStore(Protocol):
    def credit_operation_tx(self, uow: ConversationUnitOfWork,
                            fact: ScoreboardOperationFact) -> Scoreboard: ...
    def apply_mailbox_sample_tx(self, uow: ConversationUnitOfWork,
                                sample: MailboxCensusSample) -> Scoreboard: ...
    def get(self, view: ReadView, agent_session_id: UUID) \
            -> Scoreboard | NotFound: ...

class InsightStore(Protocol):
    def credit_conversation_tx(self, uow: ConversationUnitOfWork,
                               fact: DailyConversationFact) -> bool: ...
    def credit_activity_tx(self, uow: ConversationUnitOfWork,
                           fact: DailyActivityFact) -> DailyInsightRollup: ...
    def credit_health_tx(self, uow: MachineUnitOfWork,
                         fact: DailyHealthFact) -> DailyInsightRollup: ...
    def read_stats(self, view: ReadView,
                   query: StatsQuery) -> StatsResult: ...

class ClientTelemetryStore(Protocol):
    def record_batch_tx(self, uow: MachineUnitOfWork,
                        principal_id: UUID,
                        records: tuple[ClientTelemetryRecord, ...]) \
            -> TelemetryBatchResult: ...

class MessageDeliveryStore(Protocol):
    def plan_send_tx(self, uow: ConversationUnitOfWork,
                     command: MessageCommand) -> AcceptedOperation: ...
    def mark_resume_ready_tx(self, uow: ConversationUnitOfWork,
                             operation_id: UUID,
                             resume_operation_id: UUID,
                             expected_revision: int) -> MessageDelivery: ...
    def apply_provider_receipt_tx(self, uow: ConversationUnitOfWork,
                                  receipt: DeliveryReceiptFact) \
            -> MessageDelivery: ...

class DictationGrantStore(Protocol):
    def create_tx(self, uow: MachineUnitOfWork,
                  spec: DictationGrantSpec,
                  token_sha256: str) -> DictationGrant: ...
    def consume_tx(self, uow: MachineUnitOfWork, token_sha256: str,
                   observed_binding: GrantBinding) -> DictationGrant: ...
    def revoke_expired_tx(self, uow: MachineUnitOfWork,
                          now: datetime, limit: int) -> tuple[UUID, ...]: ...

class DictationProviderPort(Protocol):
    def mint_restricted_grant(self, credential: SecretHandle,
                              spec: DictationGrantSpec,
                              lifetime: timedelta) -> OneTimeSecret: ...

class ClipboardPort(Protocol):
    def read_file_urls_once(self, gesture: LocalPasteGesture) \
            -> ClipboardFileSnapshot: ...

class ClipboardResolutionStore(Protocol):
    def persist_success_tx(self, uow: MachineUnitOfWork,
                           resolution: ValidatedClipboardResolution) \
            -> ClipboardFiles: ...
    def record_rejection_tx(self, uow: MachineUnitOfWork,
                            audit: ClipboardRejectionAudit) -> None: ...

class PushStore(Protocol):
    def get_active_public_key(self, view: ReadView) -> PushPublicConfig: ...

class NotificationPolicyStore(Protocol):
    def retraction_rule(self, view: ReadView, reason: CancelReason,
                        kind: NotificationKind) -> RetractionRule: ...

class LegacyAccountMappingStore(Protocol):
    def import_registry_tx(self, uow: MachineUnitOfWork,
                           registry: MeasuredLegacyAccountRegistry,
                           mappings: tuple[LegacyAccountMappingSpec, ...]) \
            -> LegacyAccountImportResult: ...
```

The application owners are `TaskFacetService.dismiss_completed`,
`MemoryProjectionService.record_touch|replace_search`,
`MemoryQueryService.snapshot|note`,
`ActorFacetService.observe_context|observe_runtime`,
`ScoreboardProjectionService.credit_operation|apply_mailbox_sample`,
`InsightService.query|credit_conversation|credit_activity|credit_health`,
`DiagnosticService.record_client_telemetry`,
`MessageDeliveryService.send|continue_after_resume|observe_delivery`,
`DictationService.mint_grant`, `ClipboardService.resolve_files`,
`PushSubscriptionService.get_config`, and
`LegacyAccountImportService.import_registry`. The HTTP adapters call those
owners; they do not call stores or privileged ports directly.

`DictationService.mint_grant` resolves configuration and the credential,
validates the principal/device/project binding, and calls
`DictationProviderPort` outside a SQLite transaction. It then stores only the
returned token's SHA-256 and returns the clear token once. If that final write
fails, the clear token is discarded and expires within 60 seconds. This route
does not promise idempotent replay of a secret response; an HTTP retry mints a
new independently restricted grant. `ClipboardService.resolve_files` reads one
pasteboard snapshot outside a transaction, validates the complete ordered set,
then creates all Resources, versions, path-token grants, and one security audit
record atomically; a validation failure writes only the audit record and
returns no Resource.

Required transactions are exact:

1. task dismissal locks the current task snapshot, proves the submitted sorted
   IDs/digest and all-done predicate, then CAS-writes the preference and
   Conversation structural change;
2. a memory touch upserts one path and advances the contribution revision; a
   search rerun updates its count, deletes all prior hits, inserts the complete
   new hit set, trims searches beyond 100, and advances that same contribution
   revision;
3. one accepted Operation credits its scoreboard row, canonical file-change
   rows, daily usage/Insight rows, and projection revision once; the unique
   Operation identities make retry a no-op;
4. a mailbox sample replaces the current fresh/stale census and applies all
   newly proved delivered/read transitions before advancing the score revision;
5. resume-and-send creates the message-delivery Operation/detail, resume
   Operation/detail, runtime request revision, attempt, and launch outbox in one
   transaction; only a matching launch receipt changes the delivery from
   `relaunching` to `dispatching` and enqueues paste;
6. an actor facet observation compares source epoch/ordinal, records
   provenance, changes the actor row/projection, and emits
   `actor_track.changed` together; and
7. legacy account import writes mapping rows, Accounts, credential-import
   receipts, provenance, and relimit eligibility together. No partial registry
   becomes selectable.

The exact read/write keys are:

| Access | Key/order | Required schema object |
|---|---|---|
| memory touches | newest `last_touched_at DESC,canonical_path`; tree is built from the complete bounded set | `memory_touch_newest` |
| memory searches | newest `last_searched_at DESC,id`; hits `rank ASC` | `memory_search_newest`, primary key `(search_id,rank)` |
| actor context | primary-key track, source order compared in the update predicate | `actor_track_context_state` primary key |
| actor runtime | newest source epoch/ordinal/ID | `actor_runtime_latest` |
| scoreboard | exact AgentSession | `agent_session_scoreboards` primary key |
| file census | Conversation/path/Operation | `file_changes_session_path` and `(operation_id,ordinal)` |
| daily Insights | date/project/hour range | `insight_project_range`, rollup primary key, conversation-credit primary key |
| telemetry dedup | `(surface_id,client_record_id)`; family/time diagnostics | unique constraint and `surface_telemetry_family_time` |
| dictation expiry | `state,expires_at,id`, at most 100 per claim | `dictation_grant_expiry` |
| legacy Account identity | fingerprint/slug/realpath/alias | mapping primary key and `legacy_account_target` |

The separate live-facet protocol has this closed envelope and payload registry:

```text
LiveFrame = {event:enum(system.connection,client_upgrade_required,
  live.suggestion.changed,live.foreground.changed,live.compaction.changed),
  data:oneOf<ConnectionLiveDTO,ClientUpgradeLiveDTO,SuggestionLiveDTO,
             ForegroundLiveDTO,CompactionLiveDTO>}
ConnectionLiveDTO = {daemon_boot_id:UUID,api_build_id:text[1..128],
  minimum_client_build_id:text[1..128],connection_generation:Revision}
ClientUpgradeLiveDTO = {api_build_id:text[1..128],
  minimum_client_build_id:text[1..128],reload_url:text[1..2048]}
SuggestionLiveDTO = {conversation_id:UUID,agent_session_id:UUID?,
  actor_key:ActorKey,suggestion_id:text[1..128],revision:Revision,
  text:text[0..16384],available:boolean,observed_at:Timestamp}
ForegroundLiveDTO = {conversation_id:UUID,agent_session_id:UUID?,
  actor_key:ActorKey,running:boolean?,copy_group_id:text[1..256]?,
  operation_id:UUID?,started_at:Timestamp?,revision:Revision,
  observed_at:Timestamp}
CompactionLiveDTO = {conversation_id:UUID,agent_session_id:UUID,
  scope:enum(host),state:enum(inactive,compacting,unknown),revision:Revision,
  observed_at:Timestamp}
```

The compaction bar freezes geometry while active and changes only brightness in
a bounded breathing animation; width never pulses. The presenter remembers the
last settled width under `s:<agent_session_id>` and eases a real post-compaction
drop from that width. It rejects non-host/actor compaction frames, so an agent
bar never replays the host's compaction animation.

`LiveFacetService.observe_suggestion|observe_foreground|observe_compaction`
validates provider source order and replaces the keyed in-memory value;
`subscribe(scope,principal,client_build)` produces `system.connection`, an
optional `client_upgrade_required`, then the current bounded live snapshot.
The SSE encoder omits `id:` for these five event names. A protocol test rejects
an `id`, an outbox/SQLite write, a payload over its bound, a frame delivered
before `system.connection`, or a non-empty client live state after disconnect.

`SurfaceTelemetryRetentionWorker` deletes at most 1,000 telemetry rows older
than seven days per transaction and yields after 25 ms. `DictationGrantReaper`
marks at most 100 elapsed active grants expired per transaction and never
retains a clear token. Memory pruning happens only in the search-write
transaction described above; touches survive with the AgentSession.

## 41. Normative closure of the fourth legacy-coverage review

This section is part of v4 and wins over earlier wording where the two differ.
The review found omissions in provider parsing, account/auth persistence,
rendering, retention, and test safety; each rule below is normative and has an
acceptance fixture. The fourth-review SQL in Section 40.7 contains the
authentication, pricing, account, and actor-scoreboard owners; the clean
install catalog and digest are regenerated from that expanded unit.

### 41.1 Claude hook manifest and child metadata

The Claude manifest has explicit rows for `SessionStart`, `SessionEnd`,
`InstructionsLoaded`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PostToolBatch`, `Stop`, `StopFailure`, `Notification`,
`PreCompact`, `PostCompact`, `TaskCreated`, `TaskCompleted`, `SubagentStart`,
and `SubagentStop`. Each row names its input fields, semantic
authority, closer, and native deadline. `PostToolUseFailure` closes a failed
tool and can never be treated as `PostToolUse` success. `Stop` is the host-side
`background_tasks` authority; a child stop payload describes only that child.
`PreCompact` arms and `PostCompact` clears the compaction latch. `SessionEnd`
closes the hosted attempt and parks the source; `StopFailure` records provider
failure and is a relimit/child-API-death closer; `PostToolBatch` is received and
audited but never used to infer tool success. `InstructionsLoaded` writes
negative-start evidence before adoption lookup. A mid-session `SessionStart` is
classified from session/file evidence and cannot open a second attempt merely
because its `source` differs. Unknown families remain disabled.
Claude permission/asking state comes from `Notification`; Claude's
`PermissionRequest` is audit-only and is not a Claude semantic row. The
Codex adapter may register `PermissionRequest` for its own host-specific
attention mapping.

The child metadata reader accepts `agentId`, `toolUseId`,
`agent_transcript_path`, `stoppedByUser`, `taskKind`, `customAgentType`, and
`agent_type`. `taskKind == in_process_teammate` maps to `track_kind=teammate`
and register `team`; all other in-process children map to `track_kind=subagent`
and register `agent`. `customAgentType` selects an agent definition but never
decides lifecycle. A stop with no opener and no readable transcript is an
auxiliary observation with anomaly `child_artifact_missing`, not a successful
subagent. Teammate mail is a typed `user` record containing
`<teammate-message teammate_id=...>`, stored with sender and recipient.

### 41.2 Codex boundaries, discovery, and parse judgements

Codex child discovery accepts `session_meta.thread_source == subagent` and
`source.subagent.thread_spawn`. Admission uses the rollout filename timestamp
(inode birth time only when unavailable), never mtime. The child reader stores
`subagent_fork_epoch` and `subagent_body_offset`; replayed parent records before
the boundary are excluded from the child view, while the parent turn is taken
from the last replayed `task_started` immediately before the boundary. Both
streaming and random-access readers fail open to showing a record, never to an
empty scope.

Synthetic records are structural: developer/system records are synthetic; user
tag wrappers are synthetic except `<task>`; `<proposed_plan>` is a retained,
verdict-less `plan` record. A `/plan` prompt is de-duplicated only against the
adjacent slash-stripped prompt after `turn_aborted`; it is not a generic
interruption closer. Known non-shell `exec` tools are parsed by their schema,
not rendered as shell commands. `web_search_end` owns web-search output.
Native Codex children stamp `sub:<agent-id>` and `track_kind=subagent`; the
`codex:` register is reserved for a Claude-hosted sidecar. Standalone Codex is
a host: it has no session-end hook, uses a ppid-walk process owner, tails the
whole rollout, skips nested Claude launches and ChatGPT-app windows, and adopts
native `codex-tui` rollouts.

### 41.3 Model, pricing, and account launch

Non-inheriting children load their definition from every ancestor
`.claude/agents` directory, including `CLAUDE_PROJECT_DIR`. Effort precedence
is environment > definition frontmatter > settings `effortLevel` > model
default. The child's context window follows the same definition ladder and a
parent `resolvedModel` may upgrade it at completion. Unknown or newer model
prices are unknown, never guessed; prefix matching is version-exact and
specific-before-general. Cache multipliers and validity epochs are stored in
`model_price_epochs`, so historical cost is stable.

`AccountLaunchService` owns placement. It validates the registry alias,
resolves `config_dir_for(slug)`, and launches
`[$SHELL, -lic, '<validated-alias> "$@"']`; a bare provider word is not a
substitute. A symlinked settings file is edited through its resolved target
using a symlink-preserving temporary sibling and atomic replacement; the
symlink is never replaced by a regular file.

### 41.4 Adoption, bindings, attention, and presentation

The live predecessor writes the adoption note and the successor may consume it
while that predecessor is live. A missing predecessor state DB is stale and is
rejected. Notes have no arbitrary expiry; they retire only on consumption,
explicit rejection, or an evidenced cleanup decision. `terminal_bindings` are
leases: resume/adopt/`/clear` creates a new revision for the same window and
marks the old lease superseded. Paint attempts remain historical and are not
cascaded. Mirror bias is a percentage (default 25); observed resize values are
not constrained by the request clamp.

`AttentionService` parses notifications and writes a reason string for every
probe/timer transition. Logged-out clears only after a status-line snapshot at
least `LOGGED_OUT_GRACE_S` newer than the fact reports authenticated state;
refreshing the failed turn is not success evidence. `tab_focused` means
`is_focused`, not merely selected/active. Machine-wide `device_active` holds
pending intents but never retracts delivered notifications. `composing` means
web-surface input only; terminal typing is a separate `terminal-input` reason.
A terminal drive that proves a dialog disappeared releases input ownership,
including a decline. Plan options are read before creating a decision
Operation. Stable JSX literal marker fragments are matched, never composed
runtime phrases.

Copy affordances are painted on block headers; expansion links are producer
declared on the block's own line/gut item, including file one-liners. Mail
plumbing folds in `default`; peer message bubbles remain visible. Fold summaries
count semantic items and print `N of M shown`. Focus mode retains prompts and
final replies at full strength, keeps only the newest provisional reply dimmed
during a live turn, and hides older provisional replies.

### 41.5 Rendering, viewport, live frames, retention, and tests

The formatter breaks top-level `&&`, `||`, and `|`, makes `;` a newline,
preserves command-position indentation, excludes braces, retags command words,
and segments embedded Python with `ast`; `CLAUDE_MIRROR_FORMAT=0` disables
reflow. Failure is an audited raw fallback. Global anchor search is reserved
for user click-to-view repaint, uses cached probe rows/ANSI-stripped lines, and
is abandoned when an amendment changes content above the anchor. Append and
ordinary SIGWINCH paths stay incremental. `ForegroundLiveDTO` carries
`copy_group_id`, `operation_id`, and `started_at`, allowing the elapsed chip to
attach to the correct block.

Retention owns raw observations/provenance, ingestion decisions, command/tool
evidence, health errors, anomaly runs, effect attempts, notification
intents/routes/deliveries, and OTLP receipts. Receipts are pruned in bounded
batches after canonical credit; listener state retains the last sequence and a
bounded failure ring. Every new audit table declares a retention class or
schema CI fails.

Tests use an isolated temporary home, `CLAUDE_CONFIG_DIR`, credentials,
Telegram/Web Push transports, and database path outside the repository. The
fallback transport is isolated too; tests never page a real device or rewrite
the developer's settings symlink. Test-only environment knobs are stamped in
`sessions.env`, shipped behavior is unchanged, and async tests use bounded
`wait_until`.

The importer covers parked DBs, audit/preferences/counters/KV facets, OTLP,
errors, alerts/mutes/hidden directories/tasks, namespace preferences, drafts,
and scorebar state. Fallback classification additionally covers legacy note
rewordings, `lead_head`/`strip_who`, `cmd_note`, `mail:<row-id>` subjects,
`diffstat`, and `nf`; unknown rows are quarantined without mutating sources.

### 41.6 Completeness gate

The schema catalog must resolve every manifest name to a table or view,
including authentication, `model_price_epochs`, actor scoreboards, and repair
and control owners. `stream_frames` is a BQSF file stream, not a SQLite table.
The manifest's historical aliases resolve exactly as follows:
`credential_references -> provider_credential_references`,
`conversation_title_facts -> conversation_title_projection`,
`control_details -> control_operation_details`,
`compaction_details -> compaction_facts`,
`provider_plugins -> provider_plugin_registry`,
`backend_health -> backend_health_projection`,
`repairs -> repair_records`, `repair_decisions -> repair_decision_records`,
`workflow_checkpoints -> workflow_checkpoint_facts`, and `blobs -> blob_objects`.
Each right-hand owner is a real table or view in the generated catalog; an
unresolved alias fails generation rather than silently becoming a text-only
manifest entry.
The accounts table includes the columns and uniqueness required by list/create/
patch endpoints. CI executes clean install, `foreign_key_check`, `quick_check`,
endpoint-manifest equality, and the expanded five-unit digest. Required
fixtures cover hook success/failure pairing, teammate metadata, Codex boundary
and synthetic parsing, auth bootstrap, price unknowns, account symlink launch,
binding rebind, logged-out clearing, focus selection, OTLP retry windows,
retention ownership, isolated live-fire tests, and importer parity.

## 42. Normative closure of the fifth legacy-coverage review

### 42.1 Launch, copy, limits, and extension seams

Creating a session has an explicit optimistic waiting-room flow. The launch
command writes a launch intent and emits `wake` with `{sid,win,cwd}`. The
launching page enters `#/launching`, follows that intent, and switches to the
new Conversation as soon as the daemon publishes its first overview. Quiet
mode is transferred to the new surface; a failed or expired launch returns the
origin surface to its previous route and records the failure.

`GET /api/v1/streams/{id}/copy` and the corresponding view action are read
operations but still create one audit record with kind `web-copy` or
`web-view`, because these actions bypass terminal audit. The audit is
deduplicated by `(principal_id,stream_id,action,client_mutation_id)` and does
not change canonical content. This is an intentional exception to the normal
read-endpoint rule.

The extension boundary is intentionally narrower than legacy: extensions may
publish typed contributions, badges, and subprocess-RPC facts, but cannot add
arbitrary HTTP routes or SSE channels. This accepted compatibility difference
is exposed in the extension manifest as `routes=[]`; a future route-bearing
extension requires a new design and security review.

The daemon owns client limits through `GET /api/v1/limits`. The response
contains `upload_max`, `rename_max`, `view_ttl_s`, and the derived presence
heartbeat `max(2,floor(view_ttl_s/2.5))`. Request validation and client display
both consume this response; the browser does not duplicate these constants.

Overview feed updates have a size budget: a complete replacement
`ConversationOverviewDTO` is capped at 64 KiB, a principal feed at 256 KiB per
revision, and coalescing is at most once per second per Conversation. A 20-live
Conversation phone-tunnel profile must stay below 256 KiB/minute average and
1 MiB/minute p99; over-budget revisions emit `resnapshot_required` with a
compact overview summary rather than repeatedly sending the full card.

### 42.2 Exact remaining parity constants

`COMPACT_MAX_S=900` is a read-side latch expiry, not a provider write-side
constant. `CLAUDE_MIRROR_SCROLLBACK` is read directly with default 4,800; it is
not derived from terminal scrollback. Viewport restoration performs three
total passes, so a gross miss consumes the first pass and leaves two delta
corrections. `FG_BACKSTOP_S=7200` belongs to the Claude foreground stream;
the shared tailer has `BACKSTOP_S=21600` and `POLL_S=0.4`. Namespace drafts
retain 24 entries. Mail census uses unread `<=60s` and stale `>60s`. Teammate
slot numbers round-robin over five, while the four-color teammate palette
wraps by color index.

The scorebar Σ row displays total, fresh input, output, cache read, cache
write, and a trailing approximate cost segment. Its arithmetic is explicit:
`total = fresh_input + output + cache_read + cache_write`; gross input is
retained separately and never substituted for fresh input in the display.

### 42.3 Authentication and collaboration source of truth

The §38.36 authentication model is authoritative. Principals use kinds
`human|service|edge|terminal|remote_agent`; credentials use
`unix_peer|browser_session|bearer|client_certificate|invitation`; browser
sessions store device ID, secret hash, CSRF hash, absolute expiry, and idle
expiry. The fourth-review convenience tables are renamed/converted during
schema generation and cannot define a second vocabulary. Collaboration roles
are `viewer|editor|driver|admin` everywhere: invitation requests, invitation
rows, membership rows, and DTOs use the same CHECK set.

`backends` stores `kind`, `display_name`, `label`, `adapter_id`,
`endpoint_config_ref`, `trust_class`, `enabled`, `config_json`, and `state`.
`execution_targets` stores `label`, `default_mode`, `workspace_root_ref`, and
`enabled` in addition to provider/backend identity. Generated OpenAPI and SQL
artifacts are the only normative owners of these fields; prose cites them and
does not create alternate definitions.

### 42.4 Complexity decision

The review's simplification proposals are not adopted because the project has
already chosen to retain restart safety, durable effects, one machine-wide
SQLite database, no spooling, and complete future-feature contracts. The
invariants remain mandatory. Performance risk is handled by the existing
Section 38.30 gates plus a pre-Phase-1 storm profile measuring small-payload
write amplification, blob fsyncs, feed publication, and hook deadline misses.
The profile may tune batching, checkpointing, indexes, and CAS frame/chunk
thresholds; it may not store semantic payloads inline in SQLite, remove the
content-addressed blob/reference path, or remove the declared durability,
audit, correlation, or retention mechanisms. A digest-bearing payload always
follows the same blob ownership and retention rules regardless of size.

The daemon remains the accepted single point of failure. During an outage the
health projection and `ingestion_gap` surface are updated immediately on
reconnection; no replay or client spool is introduced. The client shows the
last known state with an explicit outage indicator and never claims that an
unobserved provider action succeeded.

## 43. Normative closure of the sixth legacy-coverage review

### 43.1 Performance gate escalation and payload policy

The content-addressed blob store remains mandatory for semantic payloads;
there is no inline SQLite payload threshold. “Payload threshold” in the storm
profile means only CAS frame/chunk sizing and batching, not a second storage
owner. If the Section 38.30 gates fail after batching, checkpoint, index, CAS
frame, and trigger-plan tuning, the performance decision reopens in this
order: (1) CAS frame/chunk sizing and checkpoint policy; (2) synchronous
execution of idempotent local presentation effects while retaining their
durable records; (3) structural-feed publication coalescing while preserving
snapshot/resnapshot semantics; (4) trigger thinning for invariants already
proved by storage ports. Removing audit, correlation, retention, durability,
the one SQLite database, or the no-spool decision is not an allowed response.
The gate cannot deadlock without a named next decision.

### 43.2 Generated artifacts and exact-count ownership

Schema digests, endpoint counts, event counts, table/index/trigger counts, and
similar numbers in this document are generated verification outputs, not
normative hand-maintained facts. The generator extracts the five ordered SQL
units, normalizes digest fields to zeroes, computes the digest, and writes the
digest into the executable migration artifact and database metadata. CI also
derives endpoint/event counts from the generated OpenAPI/SSE registries. A
prose count is never used as an implementation input and may be omitted when
the generated artifact is available.

### 43.3 Terminal, provider, and outage acceptance fixtures

The acceptance suite adds: daemon-launched kitty socket discovery with no
environment and multiple kitty instances; `KITTY_LISTEN_ON` binding capture;
`CLAUDE_MIRROR_LIVE_FG_SUB=0/1` eligibility; header control ordering and
stand-down states; legacy tab-registry ownership mapping; observational
unregistered-build generic records versus answerable fail-closed behavior;
crash-loop banner and offline status; and performance-gate escalation order.
Each fixture records the failed alternative and the provenance that made the
decision, so a future provider update cannot silently broaden support.

### 43.4 Binding resolutions for implementation blockers

This subsection is normative and supersedes any earlier conflicting prose.

1. **Framed streams.** The header length is 104 bytes. The value follows the
   complete field list in §38.34; 84 was a transcription error. Implementations
   must reject any other version-1 header length.
2. **Operation idempotency.** `operations.native_operation_key` is a nullable
   text column. When present, it is unique for
   `(agent_session_id, kind, native_operation_key)` through the partial unique
   index `operations_native_key`. The application still performs the lookup
   before insert so a duplicate becomes the existing Operation, while the
   database is the final authority under concurrency. Existing JSON detail is
   retained for provider-specific fields but is not the uniqueness mechanism.
3. **Client limits.** `GET /api/v1/limits` is the 114th endpoint. Its closed
   `LimitsDTO` contains `upload_max`, `rename_max`, `view_ttl_s`, and
   `presence_heartbeat_s = max(2, floor(view_ttl_s / 2.5))`. The generated
   OpenAPI artifact and endpoint inventory must contain this row; no exclusion
   is permitted. The CSRF reload recovery contract added in §43.5 is endpoint 115.
4. **Schema evidence.** The generated schema artifact and its SHA-256 digest
   are authoritative. Prose, including Phase 0 README values, is regenerated
   from that artifact and cannot override it. A stale digest is a verification
   failure, not a second accepted value.
5. **OpenCode edge.** OpenCode uses a bundled TypeScript plugin implementing
   `@opencode-ai/plugin` hooks. `event`, `chat.message`,
   `tool.execute.before`, `tool.execute.after`, and `command.execute.before`
   are observational and are captured as evidence. `permission.ask`,
   `chat.params`, and `chat.headers` are delegating and remain unregistered;
   unsupported delegating behavior fails closed. The allowlist is
   `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_MODEL`, `OPENCODE_AGENT`,
   `PWD`, `OLDPWD`, and `KITTY_LISTEN_ON`. The plugin manifest and fixtures are
   the normative OpenCode edge contract.
6. **Claude families.** `MessageDisplay`, `CwdChanged`, `ConfigChange`,
   `TeammateIdle`, and `UserPromptExpansion` are registered observational
   families. Their mappings are the table in the first-vertical-slice
   contract; unknown future families remain generic evidence and never vanish.
7. **Local socket authentication.** Peer UID, owner-only socket permissions,
   source-kind allowlisting, and recorded edge provenance are the required
   local authentication mechanism. A per-edge secret is not required for the
   current local protocol. Platforms unable to report peer identity must refuse
   the connection.
8. **Assistant reconciliation.** Until transcript ingestion is live,
   `MessageDisplay` identities and transcript identities remain distinct. The
   transcript-reader task must add an explicit correlation key before enabling
   both semantic paths; it may not silently merge by text or timestamp.
9. **Legacy audit discrepancy.** The conflicting legacy reader/audit result is
   retained as an unresolved parity fixture. Step 10 must report it as
   `unknown`/withheld with both source references; it may not invent a golden
   output or silently mark parity passed.


### 43.5 Browser-serving topology and frontend-readiness gate

This subsection is normative and supersedes any earlier prose that treated a
declared HTTP contract, a constructed server object, and a browser-reachable
service as equivalent.

#### 43.5.1 Listener and process ownership

The daemon owns two distinct HTTP listeners in the same process:

1. the existing mode-0600 owner Unix socket, authenticated only from kernel
   peer identity; and
2. one TCP listener bound to the exact numeric loopback host and port from
   `ui.origin`.

`ui.origin` is an origin only: `http|https://<numeric-loopback>:<port>`, with
no path, query, fragment, wildcard, hostname resolution, or ephemeral port.
The TCP listener is disabled when this setting is absent or invalid. An
`http` origin selects the explicitly named loopback-development cookie
profile; production uses `https`, TLS, and the
`__Host-baqylau_session; Secure; HttpOnly; SameSite=Strict; Path=/` cookie.
The development cookie is `baqylau_session; HttpOnly; SameSite=Strict; Path=/`;
the authentication parser must accept exactly the cookie name selected at
boot. The TCP listener never derives Unix-owner authority from its process UID
or from a request header.

After startup recovery has opened and verified storage, registered the local
owner, and initialized read/control/feed services, the daemon starts the Unix
HTTP listener, structural-feed publisher, and loopback listener before it
reports ready. Shutdown reverses that exposure order: stop accepting loopback
requests, close SSE subscribers, stop the publisher, stop the Unix listener,
then close storage. A constructed listener that was never started is not a
running service.

The daemon is the only production application server. It serves the static web
build and `/api/v1`, `/auth`, and SSE from the same origin. A Vite development
proxy is development tooling and is not a production component. CORS is not
enabled for product API routes.

#### 43.5.2 Launcher and bootstrap transport

`baqylau web` is the launcher contract. It calls the owner-only, non-product
`POST /auth/launch` route on the Unix socket. That route mints a one-use
60-second bootstrap credential and returns the configured loopback URL with
`#bootstrap=<secret>`. `baqylau web --print-url` prints it; without that flag
the platform launcher opens it. The URL and secret must not be logged or
stored in shell history by the daemon.

The first response for `/` is a self-contained bootstrap document with no
external subresource, preload, redirect, or telemetry reference. Its inline
script reads the fragment, immediately calls `history.replaceState` to remove
it, exchanges `{secret,device_id}` at `POST /auth/bootstrap`, retains the
returned CSRF secret only in memory, and only then loads the content-hashed
application entrypoint. The bootstrap route accepts only a loopback peer,
exact `Origin`, `Sec-Fetch-Site: same-origin`, JSON content type, and the
closed request object. It returns 204, the selected session cookie, and
`X-Baqylau-CSRF`.

`POST /auth/logout` requires the current browser session plus the normal
same-origin CSRF proof, revokes only that session with a conditional database
update, expires the selected cookie, closes SSE subscriptions for the session,
and returns 204. Bootstrap and logout handlers are real HTTP handlers, not
application services that tests invoke directly.

The CSRF secret is memory-only while the session cookie is `HttpOnly`, so a
reload cannot recover the original value. `POST /auth/csrf` is the closed
recovery exchange: it requires an existing live browser session, exact
`Origin`, and `Sec-Fetch-Site: same-origin`, accepts no authority from a body,
rotates only that session's stored CSRF digest, and returns `204` plus a fresh
`X-Baqylau-CSRF`. Logout closes subscribers carrying that session credential
ID; it does not advance the principal authorization revision or revoke sibling
sessions. Bootstrap, CSRF recovery, and logout are real transport handlers.

#### 43.5.3 Static application serving and outage truth

The packaged frontend consists of an inline bootstrap shell, an asset manifest,
and immutable content-hashed files under `/assets/`. Asset lookup uses a
closed manifest and cannot traverse the filesystem. Hashed assets use
`Cache-Control: public, max-age=31536000, immutable`; the bootstrap shell uses
`no-store`. SPA fallback applies only to navigation requests outside
`/api/v1`, `/auth`, and `/assets`; an unknown API route remains a JSON 404.
The response carries a restrictive CSP and no inline script except the
digest- or nonce-authorized bootstrap script.

The statement that a static shell can show while the daemon is unavailable is
limited to a shell already loaded in an open tab or explicitly cached by the
installed PWA. A fresh HTTP navigation cannot be served while the daemon that
owns the listener is down and must never be documented or tested as if it can.
A service worker may cache the shell and hashed assets, but never authenticated
API/SSE responses and never a success response that conceals daemon outage.

#### 43.5.4 Browser authentication and SSE transport

Every non-`PUB` browser read and SSE connection authenticates the selected
session cookie, checks current principal state and authorization revision, and
evaluates the route policy before loading entity data. Mutation requests use
the same authentication followed by Origin, Fetch Metadata, CSRF,
authorization, read-only, idempotency, and concurrency checks in the already
specified order.

The browser SSE client uses `fetch` plus `ReadableStream`, not native
`EventSource`, because the request must carry credentials,
`Last-Event-ID`, `X-Client-Build-ID`, and presence session/generation
headers and must observe pre-stream 409 refusals. Connection setup implements
the specified presence attach/CAS/disconnect lifecycle. On disconnect it
clears live-only state immediately.

Retained `structural_changes` rows are the publisher and replay source.
The storage schema must persist `operation` and
`previous_entity_revision` as well as scope revision, entity revision, and
payload reference. Principal feeds also persist their principal identity. A schema
version that cannot distinguish delete from upsert may not enable delete
writers or reconstruct the value during delivery. Each subscriber registration
holds its authenticated principal and current authorization revision; delivery
encodes those values into that subscriber's cursor and never derives either from
`scope_id` or reuses one principal's encoded frame for another. Production replay must survive daemon restart; process-only rows
are permitted only as an explicit test seam.

The durable stream is not an audit-log dump. A publisher loads the immutable
payload Blob named by the row and emits the registered DTO. If that Blob is
missing, corrupt, not an object, has the wrong entity revision, or fails the
registered event/DTO required-field validation, the publisher emits `resnapshot_required` at the same durable cursor.
It never substitutes `{}` or a partial mutation request into an upsert reducer.
Delete tombstones are exempt from the DTO-revision field because the row's
delete operation and entity revision carry that contract.

#### 43.5.5 Static-contract and served-surface gates

The generated OpenAPI 3.1 artifact is `api/openapi-v1.yaml`. Its operation IDs
must equal the endpoint inventory in both directions. The HTTP route registry
must also equal that set in both directions: “every routed operation is
declared” is insufficient unless “every declared operation is routed” is
checked in the same gate. Bootstrap, SSE, error schemas, security schemes,
cookies, CSRF headers, cursor headers, examples, and all closed DTOs are
included or linked through generated companion schemas.

The daemon is frontend-ready only when one executable acceptance target proves
all of the following:

- every inventoried operation has a real handler backed by its declared
  application/storage owner, and no extra product route exists;
- the Unix launcher, loopback bind, bootstrap exchange, authenticated read,
  authenticated mutation, logout, and static asset paths pass through real
  sockets;
- a canonical transaction publishes a durable frame, a browser reconnects from
  its last applied cursor after daemon restart, and expiry, wrong scope,
  overflow, delete, and authorization-revision change force the specified
  outcomes;
- OpenAPI generation, TypeScript client generation, reducer fixtures, and
  static packaging are current;
- startup readiness is withheld if a required listener or publisher fails, and
  shutdown leaves no listener, subscriber, or session-specific live state
  behind; and
- the generated readiness artifact reports zero missing routes and every
  browser foundation as true.

A partial vertical slice may use a named endpoint allowlist for development,
but it must continue to publish the complete missing-operation set and may not
claim that the daemon is frontend-ready.

### 43.6 Operation scope and transport-complete browser mutations

The endpoint manifest contains asynchronous machine, backend, execution-target,
account, provider-edge, and push-key actions as well as conversation actions.
Consequently an accepted operation is not necessarily owned by a Conversation.
`operations.conversation_id` is nullable and every row has exactly one scope:
either a non-null `conversation_id`, or the pair `(subject_type, subject_id)`.
`subject_type` is closed by the clean-install DDL. Conversation rows keep all
existing composite foreign-key guarantees; machine-scoped rows cannot own
conversation-only Streams, Nodes, Interactions, or actor tracks.

`OperationDTO.scope` is always present. For a conversation operation it is the
Conversation `EntityRef`; otherwise it names the row's subject. Its revision is
the subject revision observed by the planning transaction. `conversation_id`
is present only for conversation scope. `GET /api/v1/operations/{id}` reads
both forms from the single table, and cancellation is permitted only when the
operation kind declares a compensating or interrupt action.

The browser mutation transport is route-shaped, not globally JSON-shaped.
JSON routes enforce their declared closed object and configured maximum.
`createUpload` alone accepts `multipart/form-data`, streams one `file` part to
staging with the 100 MiB limit, permits one UTF-8 JSON `metadata` part, rejects
all other parts, and promotes the staged blob only after its Resource planning
transaction commits. Synchronous 200/201/204 operations do not create a fake
accepted operation and do not require an idempotency key unless their manifest
row says `IK`.

Every successful synchronous mutation and every planning transaction appends
its declared structural event in the same database transaction as the canonical
change. A handler that only updates a table, returns placeholder projections,
or raises `operation_not_implemented` is missing for the §43.5 readiness gate,
even if its path appears in the route registry.

The mutable title accepted by `ConversationPatchRequest` is represented as an
append-only `conversation_title_revisions` fact owned by `surface_user`; an
initial/manual value is eligible to become current only while no provider
session is designated. It has no AgentSession owner or provider source
registration and is never written directly as an unproven provider title. A
target probe persists
its `ObservedBackend` result in `backend_health`, keyed by execution target, and
increments that observation revision on every completed probe. Backend and
execution-target reads join this table and expose `unknown` when it has no row;
they do not infer reachability from configuration state.

An InputBuffer UUID does not encode its scope. `InputBufferPutRequest` therefore
carries `kind` and the matching scope key when it creates revision zero; later
updates may omit them and inherit the immutable stored scope. The DDL enforces
the three legal shapes. Without those fields the declared PUT was incapable of
creating the first composer/new-session/interaction buffer.

Terminal focus is an observation of a TerminalBinding, not a browser
`PresenceSession`; the reserved terminal device has no browser session id,
surface id, heartbeat interval, or connection generation. Consequently
`putTerminalPresence` returns `TerminalPresenceDTO` and never fabricates the
fields of `PresenceDTO`.

### 43.7 Frontend execution, secret, resource, and worker closure

The browser surface is not ready merely because its planning write succeeds.
Every accepted effect kind used by the frontend has a registered executor, the
daemon lifecycle calls its dispatcher and reconciler, and the receipt settles
the canonical Operation and subject state. A provider launch planning
transaction creates the AgentSession, a `preparing` attempt, runtime revision,
Operation, and outbox row together. The external process is started only by the
outbox executor with the provider process environment recorded in the launch
plan, never by merging the daemon environment. Its receipt records the PID,
changes the attempt to `running`, changes the session to `active`, and settles
the Operation in one Conversation transaction; failure terminates the attempt
and session with a named reason. Startup must drive outbox dispatchers,
reconciliation, sagas, provider-edge verification, and their leases rather
than only constructing those objects.

A launch-planned AgentSession cannot be joined by provider-native session ID:
that ID does not exist until after the provider starts. The launch environment
therefore carries a daemon-issued, non-secret, 128-bit
`BAQYLAU_LAUNCH_AGENT_SESSION_ID`, and every bundled edge copies it into the
evidence envelope as `launch_agent_session_id`. The identity consumer verifies
that the planned session exists and has the same provider, then attaches the
first native alias, provenance, and source registrations in that session's
Conversation transaction. A missing, mismatched, or already-claimed
correlation is quarantined; it never creates a second Conversation. The
correlation is not an authority token and grants no control capability.

The provider adapter, not the HTTP owner or account-placement core, owns the
native arguments for start and fork. For Claude Code, start forwards
`--session-id <planned-agent-session-uuid>` and fork requires the predecessor's
active native alias and forwards `--resume <native-id> --fork-session`; for
Codex, fork forwards the native `fork <native-id>` subcommand. Non-default
model and effort selections are rendered with that provider's measured flags
(`--model`/`--effort` for Claude Code and `-m`/`-c
model_reasoning_effort=...` for Codex). The launch request is refused as
`runtime_unavailable` when the predecessor has no active native identity or the
provider adapter declares no measured launch contract. Recording fork lineage
while launching the provider's ordinary start command is forbidden.

An interactive local target launches in a real terminal tab, not as a detached
process with null stdin/stdout. Its `workspace_root_ref` is an absolute existing
directory and is the process cwd; an account configuration directory is not a
workspace. Target configuration may select one owned Kitty endpoint, otherwise
exactly one discovered owner socket is required and zero/multiple endpoints
yield `terminal_unavailable`/`ambiguous_terminal_focus`. The launcher uses
Kitty's supported remote-control client and DCS protocol, sets the correlation
as the window user variable, passes the recorded environment delta, then
read-only probes the returned window identity. That proof records the endpoint
hint and a verified TerminalBinding; an unverified launch receipt remains in
binding grace and cannot receive input. The launch effect is `unknown`, and the
planned AgentSession remains `starting`, until the durable effect reconciler
re-probes the returned window ID plus user variable and atomically promotes the
binding/session/Operation. A definite missing launcher or rejected launch is a
failure-before-action; it must not be conflated with a window that Kitty may
already have created. A newline-delimited JSON exchange sent
directly to a Kitty socket is not the Kitty protocol and is forbidden. A
headless/server target must declare provider-native noninteractive arguments or
RPC ownership; the daemon never starts an interactive CLI with null input and
calls it live.

Provider process completion remains a lifecycle fact after launch acceptance.
For detached processes the attempt handle is reaped and records exit status;
for terminal-hosted processes SessionEnd/process evidence closes the attempt.
Loss of both sources produces `unknown`/`lost`, never success. The launch
Operation settles on verified process/window acceptance, while the AgentSession
lifecycle may later end independently and publishes `conversation.changed`.

The first clean boot creates exactly one active P-256 VAPID key and stores its
private half in an owner-only SecretStore before `getPushConfig` can report the
daemon ready. A partial unique index enforces one active key. A missing private
half is not silently replaced under the same public key: the old key is retired,
its subscriptions expire, and a new key is inserted atomically. Subscription
authentication material is also stored by reference in the SecretStore, never
inline in SQLite. Delivery resolves those references only in the effect
executor, applies RFC 8188 `aes128gcm` encryption and VAPID authentication, and
does not put either secret in an outbox payload or receipt.

Machine configuration supplies `notifications.vapid_subject` and, when Telegram
is enabled, `notifications.telegram_chat_id`; the bot token remains a credential
reference. A Telegram channel with no non-empty destination is unavailable and
must be excluded before routing rather than enqueueing a knowingly undeliverable
effect. Deployments may supply `provider_edges.installer_path`; it must resolve
to an executable regular file before edge installation is advertised as
available. Repository-relative build outputs are development fallbacks only and
are not an installed-daemon packaging contract.

`ResourceDTO.execution_target_id` is nullable for daemon-local uploads and
clipboard imports. Remote/workspace Resources persist an execution-target ID,
not a backend ID presented under the wrong wire name. Upload and clipboard
responses return 256-bit random provider path tokens exactly once. SQLite
stores only each token's SHA-256 in `resource_path_grants`, tied by a composite
foreign key to the exact ResourceVersion, principal, purpose, expiry, and use
budget. Blob metadata, Resource, ResourceVersion, grant, security audit, and
structural event commit together. Clipboard rejection commits only a security
audit row. Clipboard paths must be regular no-follow file descriptors beneath
the data root or an enabled execution target's configured real workspace root;
validation never treats `/` or the daemon's ambient working directory as a
jail. File bytes are streamed into Blob staging rather than read in full.

`MessageRequest.resources` carries Resource ID/token pairs. Before opening the
Conversation write transaction, the service hashes each token, verifies through
a read snapshot that it names the paired Resource, belongs to the authenticated
principal, is active and unexpired, and that its immutable Blob exists. The
Conversation transaction consumes every grant with a compare-and-set and stores
only the Resource IDs in `message_delivery_details`; any failed comparison rolls
the whole acceptance back. Clear tokens and provider-readable paths never enter
SQLite, the outbox, logs, receipts, or structural events. At effect execution the
provider attachment adapter resolves those already-authorized Resource IDs to
their pinned ResourceVersions and materializes provider-native attachment input
in memory. A provider/target that cannot safely access a Resource is refused
before acceptance with `409 resource_not_reachable`; it is never sent a daemon
path that is outside its execution boundary.

Both HTTP listeners stream multipart bodies to a bounded owner-controlled
temporary file while reading the socket. The route parser receives a path plus
file offset and length, and Blob staging reads that segment in bounded chunks.
`Transfer-Encoding` is refused until a bounded chunked decoder exists. A
transport refusal still produces a correlated Problem response and deletes the
temporary file. This temporary request body is not an offline client/edge
queue and is deleted at the end of the request on every path.

The provider-edge installer executable and its signed artifacts are packaged
with the daemon or named by validated configuration; accepting an install when
no executable is available is a readiness failure, not a deferred worker
error. Provider-scoped revert cannot remove unrelated provider installations.
After install/revert, the executor records its receipt and the verifier runs in
the daemon lifecycle before the Operation is settled.

Every durable transcript/capture reader stores device+inode continuity evidence
with its byte cursor. First observation initializes that evidence. Replacement
or truncation closes the old registration with `superseded_at`, opens epoch
`N+1` at ordinal/byte zero, and only then resumes ingestion. A daemon restart
reuses the active cursor when identity and size still agree. Reopening a changed
artifact at byte zero while retaining the old epoch/ordinal is forbidden because
the monotonic cursor would silently reject every subsequent advance.
