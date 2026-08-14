# baqylau v5 design review and decisions

Status: **IN REVIEW**

Started: **2026-08-05**

This file records our review of the v4 design. We will discuss one point at a
time in plain language. For each point, we will record what we decided and why.
When every point is decided, we will use those decisions to write the v5 design
and its concrete implementation contracts.

The v4 document is not the implementation contract. It changes only when we
separately approve a correction during this review.

## Required completeness of v5

V5 must leave no product, data, storage, protocol, ownership, transaction, or
failure-handling decision to the implementor. The implementor may choose local
coding details that do not change observable behavior, such as private helper
names, but must not have to invent a field, state, endpoint, event, query,
transaction boundary, retry rule, or service responsibility.

V5 is accepted only when all of these contracts are complete and agree with
each other.

### Domain contract

- Every model and value object has exact fields, types, nullability, defaults,
  identity, owner, and mutability rules.
- Every state machine lists all states, allowed transitions, the evidence that
  permits each transition, and the result of an invalid transition.
- Every relationship states its direction, cardinality, deletion behavior, and
  consistency rules.
- Provider-specific values and provider-neutral values have an explicit
  boundary.

### Database contract

- One executable SQLite DDL file defines every table, column, type, default,
  primary key, foreign key, uniqueness rule, check constraint, index, trigger,
  and schema version.
- The DDL defines deletion and retention behavior. No relationship relies on
  an undocumented application convention.
- Every migration has exact forward and rollback behavior, including the point
  after which rollback is no longer safe.
- Example rows cover normal, incomplete, failed, and unknown states.

### Storage contract

- Every application storage method has an exact Python signature, input type,
  return type, and named error results.
- Every method states which tables it reads or writes, its required indexes,
  whether it opens or joins a transaction, and its locking/concurrency rule.
- Every write use case lists the complete atomic transaction: all rows changed
  together or none of them.
- Every list and lookup states its SQL access pattern, ordering, pagination,
  maximum result size, and missing-data behavior.
- Blob and live-output access defines filenames, atomic write rules, cleanup,
  retention, corruption handling, and the SQLite rows that reference files.

### HTTP contract

- Every endpoint has an exact method and path, authorization rule, path/query/
  header inputs, request body, response body, status codes, error codes,
  idempotency behavior, revision checks, pagination, and size limits.
- The API has no vague catch-all response such as `/api/v1/global`.
- Provider/model/effort/runtime choices are present on every operation that can
  start or resume a provider.
- An OpenAPI document contains the exact request, response, and error schemas.

### Live-update contract

- Every SSE endpoint, event name, event payload, event ID, ordering rule, and
  authorization rule is exact.
- Initial connection, snapshot synchronization, reconnect, missed-event,
  slow-client, overflow, and server-restart behavior are fully specified.
- Structural revisions and live-output revisions have one unambiguous
  relationship or are explicitly independent.
- The frontend algorithm for applying every event is specified and fixtured.

### Service and runtime contract

- Every service has one owner, exact public methods, input/output types, stored
  state, dependencies, startup order, shutdown behavior, and failure boundary.
- Every background task states what starts it, how often it runs, how it is
  stopped, whether work survives restart, and what happens after failure.
- Every provider, terminal, storage, alert, credential, and execution-location
  adapter method has an exact contract and capability rule.
- Composition states exactly which implementation is constructed and connected
  for each supported configuration.

### End-to-end contract

- Complete sequences cover daemon start, frontend snapshot and SSE connection,
  Conversation creation, AgentSession start, model/effort selection, message
  delivery, assistant streaming, commands/tools, interaction answers, close,
  crash, reconnect, resume, and archive.
- Each step names the endpoint or event, application method, storage method,
  transaction, affected rows/files, and frontend-visible result.
- Security, authorization, uncertainty, timeout, retry, and data-loss behavior
  are stated at the step where they occur.

### Traceability and verification

- A traceability table maps every endpoint and event to its service method,
  storage method, tables/indexes, state transition, authorization rule, and
  tests.
- Every contract has unit, storage, API, event, adapter, failure, and end-to-end
  acceptance cases where applicable.
- There are no sections labelled “representative,” “possible,” “suggested,” or
  “implementation-defined” for required initial behavior.

### Required v5 artifacts

The final specification will consist of:

```text
docs/rewrite-design-v5.md
docs/rewrite-design-v5-domain.md
docs/rewrite-design-v5-storage.md
docs/rewrite-design-v5-api.md
docs/rewrite-design-v5-events.md
docs/rewrite-design-v5-services.md
docs/rewrite-design-v5-traceability.md
docs/rewrite-design-v5-schema.sql
docs/rewrite-design-v5-openapi.yaml
```

Splitting the contracts keeps the prose readable while making the SQL and HTTP
schemas directly checkable. All files are authoritative and must use the same
names and types.

## Concrete gaps already found in v4

- `/api/v1/global` combines unrelated data and has no exact response contract.
- The endpoint list is explicitly only representative.
- AgentSession start, resume, fork, handover, and account migration do not
  consistently specify model and effort inputs or requested-versus-effective
  runtime results.
- The SSE URL, event schemas, connection handshake, cursor rules, and frontend
  application algorithm are not specified.
- The exact source tree has no location for the web and pane-host clients.
- Execution-location configuration and an active backend adapter have an
  unclear division of responsibility.
- The relational schema is an outline rather than executable, complete DDL.
- Storage ports do not enumerate concrete methods, queries, return types, or
  transaction boundaries.
- Application services do not have complete public method contracts.

## How we will work

For each point:

1. Explain what v4 proposes.
2. Explain why it may be more complicated than necessary.
3. Compare it with a simpler option.
4. Record the decision and the reason.

Possible decisions are `keep`, `simplify`, `remove`, and `defer`.

## Review list

| # | Point | Status |
|---|---|---|
| 1 | The first usable version includes too many systems | Decided |
| 2 | Every incoming event creates too many audit records | Discussing |
| 3 | Every active Conversation gets its own background task | Pending |
| 4 | Live output uses a custom crash-safe file format | Pending |
| 5 | Timeline ordering and updates have a complex protocol | Pending |
| 6 | Clients keep and replay missed updates after reconnecting | Pending |
| 7 | Too many external actions are saved as restart-safe jobs | Pending |
| 8 | Too much temporary state is required to survive a restart | Pending |
| 9 | Drafts and preferences handle complex multi-device conflicts | Pending |
| 10 | Provider and plugin interfaces are designed too early | Pending |
| 11 | Future features shape the initial design too much | Decided |

---

## 1. The first usable version includes too many systems

Status: **DECIDED — KEEP FULL SCOPE**

### What v4 proposes

Before the rewrite proves its basic Conversation model in a usable screen, v4
asks us to build many supporting systems. These include detailed audit records,
error quarantine, health tracking, special storage for live output, Resources,
shared drafts, preferences, timeline composition, reconnect handling, attention
tracking, and terminal-pane support.

### Why this may be a problem

We could spend a long time building supporting machinery before learning whether
the basic Conversation, Node, AgentSession, and Operation model works well in
the real product. If that basic model changes, many supporting systems may need
to change with it.

### Simpler option

Build one small end-to-end version first:

```text
one provider
    -> Conversation, AgentSession, Node, and Operation
    -> SQLite
    -> one read API
    -> one simple screen
```

This first version would read completed provider messages and work records. It
would not yet need live streaming, command rewriting, reconnect recovery,
shared drafts, alerts, handover, or multiple providers.

After the basic model is visible and useful, add one capability at a time.

### Decision

V5 will completely specify the current product, all current provider
integrations, and every future feature included in v4. This includes remote
execution, automatic account migration, cross-provider handover,
collaboration, extensions, multiple surfaces, recovery, and the related
security and operational behavior.

Implementation may be divided into phases, but the design for later phases
must be complete before implementation begins. No feature may use “we will
decide during coding” as a shortcut. Every feature must satisfy the
completeness contract above.

### Reason

The requested result is one complete blueprint for the whole intended product,
not only a first usable slice. This lets implementation proceed in phases
without later phases forcing unplanned changes to the data model, API, or
service boundaries.

---

## 2. Every incoming event creates too many audit records

Status: **DISCUSSING**

V4 stores the raw event, its processing state, the rule that interpreted it,
the rule version, the decision, links to every resulting record, and repair
history. We will decide how much of this is truly needed initially.

### Decision

Pending.

### Reason

Pending.

---

## 3. Every active Conversation gets its own background task

Status: **PENDING**

V4 gives each active Conversation a background task, message queue, cache,
restart behavior, probes, and sleep/wake lifecycle. We will compare this with
processing requests directly and adding a simple lock only where needed.

### Decision

Pending.

### Reason

Pending.

---

## 4. Live output uses a custom crash-safe file format

Status: **PENDING**

V4 designs a special file format for partial assistant text and command output,
including frames, checksums, revisions, repair after crashes, final blob storage,
and cleanup. We will compare this with ordinary append-only files and accepting
that some unfinished output can be partial after a crash.

### Decision

Pending.

### Reason

Pending.

---

## 5. Timeline ordering and updates have a complex protocol

Status: **PENDING**

V4 stores generated timeline positions and supports moving, replacing,
removing, and revising already displayed items. We will compare this with
building the current timeline when requested and refreshing it when late data
arrives.

### Decision

Pending.

### Reason

Pending.

---

## 6. Clients keep and replay missed updates after reconnecting

Status: **PENDING**

V4 keeps update histories and several cursor types so a reconnecting client can
receive exactly what it missed. We will compare this with fetching a fresh
snapshot after reconnecting.

### Decision

Pending.

### Reason

Pending.

---

## 7. Too many external actions are saved as restart-safe jobs

Status: **PENDING**

V4 saves provider controls, launches, terminal painting, pane changes, alerts,
messages, and UI update publication as jobs with attempts and results. We will
decide which important actions must survive a crash and which can simply be
tried again or refreshed.

### Decision

Pending.

### Reason

Pending.

---

## 8. Too much temporary state is required to survive a restart

Status: **PENDING**

V4 persists open commands, monitors, dialogs, stream readers, notification
timers, and other temporary state. We will decide which facts must survive and
which can be observed again or marked unknown after restart.

### Decision

Pending.

### Reason

Pending.

---

## 9. Drafts and preferences handle complex multi-device conflicts

Status: **PENDING**

V4 gives drafts and preferences writer identities, sequence numbers, revisions,
deletion markers, and special conflict rules. We will compare this with local
draft storage or simple last-write-wins behavior.

### Decision

Pending.

### Reason

Pending.

---

## 10. Provider and plugin interfaces are designed too early

Status: **PENDING**

V4 defines many small provider interfaces and a broad plugin contract before
the first integrations have stabilized. We will compare this with one simpler
provider interface and extracting shared pieces only after repeated needs are
visible.

### Decision

Pending.

### Reason

Pending.

---

## 11. Future features shape the initial design too much

Status: **DECIDED — KEEP FULL SCOPE**

Remote execution, automatic account switching, cross-provider handover, and
agent collaboration affect the initial records, interfaces, security rules,
and tests even though they are future features. We will decide which should be
moved into separate future design notes.

### Decision

Keep every future feature from v4 in the v5 design and specify it completely.
Future features may be scheduled for later implementation phases, but they are
not removed or left as vague extension points.

### Reason

The requested v5 outcome is a complete implementation blueprint for the whole
planned system. The data model, endpoints, events, services, storage methods,
security rules, and failure behavior must account for later features before
coding begins.
