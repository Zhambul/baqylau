# baqylau v3 — Transactional, Graph-Aware Rewrite Design

Status: **PROPOSED — LEGACY-AUDITED**

Date: 2026-08-03

Legacy audit revision: **2026-08-03, after code-level parity review**

This document records the revised architectural outcome after reviewing:

- the existing v1 implementation and its measured design lessons;
- `rewrite-design.md`, which proposed an event-sourced v2;
- `rewrite-design-review.md`, which exposed correctness, recovery,
  performance, and topology problems in that proposal; and
- the subsequent design discussion about conversation trees, headless agent
  execution, streaming responses, configurable backends, provider handover,
  collaboration, and future plugins.

It is intentionally an architecture document rather than a complete feature
specification. Existing v1 behavior remains authoritative until an explicit
migration gate replaces it. The detailed empirical rules in the existing
docs—tab transitions, transcript parsing, command streaming, screen driving,
usage accounting, alerting, and dashboard behavior—must be ported through
fixtures rather than reconstructed from this document.

The code-level audit covered the runtime and state primitives in `core/`, both
provider implementations in `plugins/claude_code/` and `plugins/codex/`, the
terminal contracts in `frontends/`, the dashboard read/write/notification
planes, and the behavioral tests. Section 36 records the resulting coverage
matrix and the design corrections. Those corrections are incorporated into the
normative sections rather than left as a detached review.

---

## 0. Executive decision

baqylau v3 will be a **transactional modular monolith** with:

- a long-running Python daemon;
- hexagonal boundaries (ports and adapters);
- a small relational domain model;
- a conversation tree represented directly;
- SQLite WAL as the initial metadata store;
- durable inbox/outbox mechanics for reliability;
- an appendable blob/stream store for high-volume bytes;
- CQRS-style read models where they earn their cost; and
- selective history tables for facts whose transitions are themselves useful.

The system of record will **not** be a globally ordered event stream, and the
core will **not** use event sourcing as its persistence model.

The minimal core model has four domain entities and one shared storage
primitive:

1. **Conversation** — provider-independent logical continuity.
2. **Node** — one semantic message in the conversation tree.
3. **AgentSession** — one provider-native incarnation attached to a
   Conversation.
4. **Operation** — structured work or control performed within a
   Conversation.
5. **Stream** — appendable provisional or bulk content owned by a Node or
   Operation.

Everything else starts as:

- a value or correlation key;
- a typed Operation kind;
- an ordered Node content part;
- a typed causal/lineage link;
- a projection;
- a plugin-owned detail schema; or
- supporting infrastructure.

An idea is promoted into a new core entity only after it acquires independent
identity, lifecycle, invariants, and queries that cannot be expressed cleanly
through the model above.

The central distinction is:

> **Nodes describe conversation content. Operations describe work.**

Provider sessions, terminal processes, tool calls, commands, subagents,
alerts, handovers, and UI projections must not be collapsed into one
all-purpose Session aggregate or one notification log.

The legacy audit adds a second distinction:

> **The conversation is a tree; the activity surrounding it is an ordered,
> causally linked graph.**

Forcing tool work, child-task contribution, provider nesting, compaction, and
delivery attempts into the Node tree would recreate the same mismatch that
motivated rejecting one global event stream.

---

## 1. Goals, constraints, and non-goals

### 1.1 Architectural goals

The rewrite must provide:

1. A stable provider-neutral core supporting Claude Code, Codex, OpenCode,
   and future agent runtimes.
2. Terminal independence: kitty is one adapter, and no-terminal execution is
   a first-class mode.
3. Surface independence: terminal panes, web, CLI, phone, and MCP consume
   semantic APIs rather than paint operations.
4. Direct representation of branching conversation history.
5. Live streaming of assistant responses and operational output.
6. Recoverable ingestion, explicit uncertainty, and durable external-effect
   handling.
7. Configurable execution backends and accounts rather than one hard-coded
   Mac/tunnel/account registry.
8. A credible path to cross-provider handover.
9. A stable address space for future cross-session communication.
10. Extension seams that do not require every new provider or terminal to
    edit the core or frontend.
11. Performance suitable for multiple concurrent sessions, including one
    session emitting large command output while another requires
    latency-sensitive attention updates.
12. An audit trail that explains what arrived, what rule interpreted it, and
    what external effect was attempted.

### 1.2 Constraints inherited from reality

No architecture removes these constraints:

- Hook processes must not block or fail the agent tool.
- A daemon can crash or be restarted during active work.
- Some providers emit no event for cancellation, process death, rejected
  tools, or other important state changes.
- Provider payloads and on-disk formats are undocumented or version-fragile.
- Provider sources can arrive late, duplicated, or out of order.
- Terminal screen driving is unavoidable for features a provider exposes
  only through a TUI.
- Provider-native sessions are not mutually compatible.
- Not every interactive provider exposes semantic token streaming.
- Large output is qualitatively different from domain metadata.
- A local SQLite database serializes writers even in WAL mode.

The design must expose these facts as capability and uncertainty rather than
hide them behind universal interfaces or optimistic guarantees.

### 1.3 Non-goals

The initial rewrite will not:

- provide a distributed microservice deployment;
- guarantee lossless reconstruction of provider-private reasoning;
- manufacture native Claude sessions from Codex transcripts or vice versa;
- transfer terminal state, PIDs, provider approvals, or running processes
  during a handover;
- make arbitrary third-party plugins safe inside the daemon process;
- retain unlimited command output forever;
- require PostgreSQL merely to satisfy a persistence library;
- make every derived value rebuildable from the beginning of time;
- expose a universal event bus as a plugin API; or
- model every aspirational feature before it exists.

---

## 2. Why the event-sourced v2 is not the chosen core

Event sourcing can technically represent mutations to a tree. The rejection
is not based on the claim that trees are impossible to event-source. It is
based on a mismatch between the proposed global event-log topology and the
actual ownership, shape, volume, and recovery properties of baqylau's data.

### 2.1 The provider already owns a richer history

Claude transcripts, Codex rollouts/app-server threads, and OpenCode sessions
already record provider-native history. Reinterpreting each source into a
second immutable truth stream creates:

- two representations that can disagree;
- mapper decisions that become permanent truth;
- remapping surgery after a mapper bug;
- uncertain authority when the provider record and event stream differ; and
- another high-volume write path on top of the provider's own storage.

v3 retains raw evidence and normalizes it into relational state, but the
normalization is repairable in place and scoped to the affected entity.

### 2.2 Global order is not a domain invariant

The meaningful orders are:

- ancestry within one Conversation;
- source sequence within one provider stream;
- lifecycle order within one Operation;
- byte/revision order within one Stream; and
- request/attempt order within one external effect.

A total order across unrelated sessions is not a business fact. Making it the
spine creates head-of-line blocking and encourages consumers to infer
relationships from notification order that do not exist.

### 2.3 Data has several natural storage shapes

The system contains:

- immutable semantic messages arranged as a tree;
- mutable lifecycle state;
- high-volume appendable bytes;
- current configuration;
- retryable external effects;
- diagnostic evidence;
- provider-native records; and
- disposable query projections.

One event abstraction makes at least some of those unnatural. v3 gives each
shape an explicit home while keeping one transaction boundary for state that
must change atomically.

### 2.4 Replay is not the universal repair tool

Full replay becomes slower as history grows and makes a local correction
depend on every historical mapper version and retained input. v3 instead
supports:

- idempotent ingestion;
- versioned mapper decisions;
- targeted remapping from retained evidence;
- per-Conversation projection rebuilds;
- ordinary schema/data migrations;
- explicit repair commands; and
- provider-source re-import where that source still exists.

Rebuildability remains valuable, but only derived projections promise it.

### 2.5 The useful v2 ideas survive

The following are retained:

- one supervised daemon;
- logic-light edge shims;
- raw envelope/observation capture;
- provider-specific parsing behind adapters;
- semantic facts rather than presentation instructions;
- surface-owned presentation and leaf sanitization;
- whole-gesture control ports;
- capability discovery;
- null/headless terminal support;
- content-addressed sealed blobs;
- durable timers represented as state plus ephemeral wakeups;
- idempotent external effects;
- strangler migration;
- contract tests and import-direction enforcement; and
- porting v1's measured fixtures.

The event store is removed; the boundaries are not.

---

## 3. Architectural style

### 3.1 Modular monolith

The daemon is one deployable process and normally uses one metadata database.
Modules have explicit ownership and public interfaces, but synchronous
in-process calls are allowed where a transaction or invariant requires them.

This deliberately avoids:

- one process per feature;
- a log between every internal component;
- distributed transaction problems;
- premature network protocols between modules; and
- operational dependencies such as Kafka, Redis, or Celery.

An adapter or worker can later move out of process without changing the
domain vocabulary, because external interactions already cross ports.

### 3.2 Hexagonal boundaries

The core depends on semantic protocols, never on Claude, Codex, OpenCode,
kitty, HTTP, a tunnel, or a concrete database.

The dependency direction is:

```
surfaces / edge transports
          |
          v
application use cases
          |
          v
domain model and rules
          |
          v
ports (protocols)
          ^
          |
provider / terminal / storage / delivery adapters
```

Adapters translate. They do not define core semantics.

### 3.3 Functional core, imperative shell

Prefer pure functions for:

- provider-record classification;
- live-branch selection;
- attention derivation;
- handover compilation;
- pricing and usage arithmetic;
- rendering classification; and
- capability-based request validation.

Use imperative services for:

- transactions;
- process supervision;
- file watching;
- timers;
- terminal control;
- network delivery;
- staging-stream writes; and
- provider SDK/app-server calls.

### 3.4 Single owner per fact

The rule is not "one storage mechanism for everything." It is:

> **Every fact has one authoritative owner.**

Examples:

| Fact | Owner |
|---|---|
| Provider-native transcript record | Provider source / retained Observation |
| Logical Conversation head | Conversation table |
| Committed semantic message | Node table + sealed content |
| Provider-native session identity | AgentSession + aliases |
| Command lifecycle | Operation |
| Live assistant bytes | Stream |
| External-effect request | Outbox/control Operation |
| External-effect attempt and receipt | Effect-attempt infrastructure |
| Tab colour | Attention projection + terminal effect receipt |
| Rendered HTML/ANSI | Surface only |

---

## 4. System overview

```
                 INBOUND

 hooks · JSONL files · rollouts · SDK/app-server streams
 OpenCode events · OTEL · status line · probes · web/MCP commands
                              |
                              v
                   provider/backend adapters
                              |
                    durable Observation inbox
                              |
                              v
                     application services
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
       Conversation       AgentSession      Operation
          + Node                              + Stream
             |                |                |
             +----------------+----------------+
                              |
                   SQLite metadata + blob store
                              |
                       transactional outbox
                              |
                              v
                 terminal · web · CLI · MCP
                 alerts · provider controls
```

There are two delivery planes:

1. **State plane** — durable structural changes, query snapshots, workflow
   state, and external-effect requests.
2. **Stream plane** — coalesced live assistant text and operational output,
   recoverable by stream revision/range rather than one global event cursor.

High-volume data must not force latency-sensitive state changes through the
same per-item fan-out path.

---

## 5. Core vocabulary

The word "session" was overloaded in v1 and the event-sourced proposal. v3
uses the following vocabulary consistently:

| Term | Meaning |
|---|---|
| **Conversation** | Provider-independent logical body of work shown as one continuous thread |
| **Node** | One semantic user/assistant/system/summary message in the Conversation tree |
| **AgentSession** | One provider-native conversation/runtime incarnation attached to a Conversation |
| **Operation** | Structured work, observation, or control lifecycle within a Conversation |
| **Stream** | Incremental content owned by a provisional Node or Operation |
| **Observation** | Raw input received from a provider, edge, prober, client, or watcher |
| **Projection** | Derived query state, disposable and rebuildable within its declared scope |
| **Execution target** | Configured provider + backend + optional account used to start an AgentSession |
| **Surface** | Web, terminal pane, CLI, phone, MCP, or another consumer |

The UI may continue to use the familiar word "session" in labels. Internally,
the list-card identity is a Conversation, and its currently active provider
attachment is an AgentSession.

In this document, a **Codex rollout** means Codex's provider-native persisted
record for a thread/session. It does not mean a baqylau event log, a software
release rollout, or an application deployment.

---

## 6. The minimal domain model

```
Conversation
  |
  +-- head_node_id --------------------+
  |                                    |
  +-- Node(parent_id) <----------------+
  |      +-- ordered content parts
  |      +-- zero or more Streams
  |
  +-- AgentSession
         +-- execution attempts / terminal binding
         +-- typed links to parent/source AgentSessions
         +-- Operation(parent_operation_id?)
                +-- zero or more Streams

Node / AgentSession / Operation
  +-- typed causal and lineage links
  +-- provider-local source position
```

Relationships:

- A Conversation has zero or more Nodes.
- A committed Node belongs to exactly one Conversation.
- A Node has at most one semantic parent.
- A Conversation points to one active committed head.
- A Conversation has one or more AgentSessions over its lifetime.
- An AgentSession belongs to one Conversation.
- An Operation belongs to one Conversation and normally one AgentSession.
- An Operation can be nested under another Operation.
- A Stream is owned by one Node or one Operation; an owner may have several
  named Streams.
- A Node has one or more ordered content parts; parts are values, not core
  entities.
- Containment is expressed by parent foreign keys. Other relationships—such as
  launched, resumed-from, result-of, contributes-to, or summarizes—use typed
  supporting links rather than overloading containment.

The four entities remain the conceptual core. Content parts, activity
positions, links, context checkpoints, input buffers, resources, and usage
facts are supporting records with deliberately narrow responsibilities. Their
presence does not turn the core back into the earlier all-concepts model.

Supporting tables can carry aliases, attempts, provenance, configuration, and
projection state without becoming new core concepts.

---

## 7. Conversation

### 7.1 Responsibility

A Conversation represents provider-independent continuity.

It owns:

- stable baqylau identity;
- current semantic head;
- monotonic revision;
- human-facing title and project association;
- creation/archive lifecycle; and
- the collection of provider AgentSessions that have participated.

It does not own:

- provider session IDs;
- process PIDs;
- terminal windows;
- account credentials;
- command execution state;
- streamed bytes; or
- provider-native raw history.

### 7.2 Suggested record

```text
conversations
  id                  UUID primary key
  title               nullable text
  head_node_id        nullable Node ID
  revision            integer, incremented on semantic mutation
  project_ref         nullable stable logical project reference
  created_at          timestamp
  updated_at          timestamp
  archived_at         nullable timestamp
```

`revision` is an optimistic-concurrency and cache-invalidation value. It is
not a global log position.

### 7.3 Creation

A Conversation can be created:

- explicitly by a user launch request;
- when an unknown provider session is confidently discovered;
- during import of provider history;
- as the target of a fork; or
- by accepting an inbound peer/collaboration request.

Uncertain discovery must not immediately merge into another Conversation
based only on cwd. A newly observed provider identity can remain an
unassociated or provisional AgentSession until evidence is sufficient.

### 7.4 Active head

`head_node_id` identifies the current semantic path. It moves only to a
committed Node belonging to the same Conversation.

Changing the head:

- does not delete Nodes;
- does not undo Operations that physically occurred;
- increments Conversation revision;
- invalidates branch-sensitive projections; and
- records provenance explaining the change.

### 7.5 Multiple simultaneous providers

More than one AgentSession can be attached to a Conversation, but only one is
normally designated by a projection/configuration row as the active
interactive continuation.

If two AgentSessions append semantic Nodes from the same previous head:

- both Nodes remain;
- they form a real divergence;
- the currently active head is selected explicitly;
- no arrival timestamp silently decides which provider "won"; and
- the other path remains available for later inspection or promotion.

### 7.6 Projects and workspace bindings

`project_ref` is a logical project identity, not an absolute cwd. A Conversation
can move to another worktree/backend during account migration or handover.
Supporting bindings record physical placement:

```text
conversation_workspaces
  conversation_id
  backend_id
  workspace_ref          adapter-stable path/worktree identity
  role                   primary | source | handover_target | archived
  revision_ref           nullable git/content revision
  dirty_fingerprint      nullable
  active
  provenance_id
  observed_at
```

Operations name the binding/cwd under which they executed. Resources are also
backend/workspace-scoped. Surfaces show git/worktree chips from this model and
never assume the controller machine can open a remote path directly.

### 7.7 Titles

`Conversation.title` is the effective cached title. Its inputs are supporting
facts with explicit precedence:

- provider-native explicit rename;
- baqylau/user explicit override;
- provider-generated title/summary;
- first meaningful human prompt;
- project/session fallback.

Title facts record source, AgentSession, source position/revision, and
provenance. A short provider tail-window cannot silently overwrite a durable
explicit rename, while a newer verified native rename can supersede an older
override according to policy.

---

## 8. Node

### 8.1 Responsibility

A Node is one semantic message participating in the human/agent conversation.

The initial role vocabulary is deliberately small:

- `user`
- `assistant`
- `system`
- `summary`

Role is not enough to preserve the legacy distinction between a human prompt,
a provider-injected resume nudge, stop-hook feedback, a slash-command prompt,
and a peer message. A Node therefore also carries a stable semantic kind and
origin. Suggested initial values are intentionally coarse:

```text
semantic_kind: prompt | message | summary | recap | system
origin: human | provider | baqylau | peer | imported
```

Provider-specific record types remain in provenance. These fields preserve
authorship and product behavior without importing provider grammar into the
core.

Tool calls, commands, file edits, subagents, monitors, and control gestures
are Operations, not Nodes. Their results can be referenced by a Node or
handover bundle without forcing provider tool-record grammar into the
conversation tree.

This is a deliberate simplification over a one-to-one normalization of every
provider transcript record. The provider-native graph remains available as
raw evidence.

### 8.2 Suggested record

```text
nodes
  id                    UUID primary key
  conversation_id       foreign key
  parent_id              nullable Node ID
  agent_session_id       nullable producing AgentSession
  role                   user | assistant | system | summary
  semantic_kind          prompt | message | summary | recap | system
  origin                 human | provider | baqylau | peer | imported
  state                  streaming | committed | aborted
  source_external_id     nullable provider record/message ID
  source_position        nullable provider-local sortable position
  turn_key               nullable opaque correlation key
  actor_key              nullable provider-scoped actor key
  completion_reason      nullable complete/interrupted/failed/unknown
  source_timestamp       nullable timestamp
  created_at             timestamp
  committed_at           nullable timestamp
```

A uniqueness constraint should cover `agent_session_id` plus
`source_external_id` when the source supplies a stable ID. Imported records
without an AgentSession use an import-source namespace/correlation table rather
than a bare global message ID.

Node content is an ordered list of value records:

```text
node_parts
  node_id               foreign key
  ordinal               integer
  kind                  text | image | file | artifact | structured
  media_type            nullable MIME/media type
  content_ref           nullable sealed BlobRef
  stream_id             nullable Stream for provisional content
  resource_id           nullable Resource reference
  metadata              bounded validated JSON
  primary key(node_id, ordinal)
```

Most messages have one text part. The extra table is justified by features that
already exist in v1: pasted images, uploaded files, `@path` attachments, and
prompts combining text with provider-native list content. Encoding these as one
magic text blob would make handover and non-Claude providers depend on Claude's
mention syntax. Question/plan cards and file/diff views are derived from
Operations and Resources, not forced into Node content merely because a surface
renders them beside messages.

### 8.3 Tree semantics

The semantic parent relation expresses dialogue ancestry, not arbitrary
provider record parentage.

The provider adapter maps:

- prompts;
- assistant messages;
- provider summaries/compactions that replace prior context; and
- explicit branch/fork evidence

into this semantic tree.

Parallel tool results, attachments, and metadata may share native parents
without creating alternate semantic branches. Because those records become
Operations or provenance, sibling detection in the provider record graph
cannot accidentally discard live conversation.

### 8.4 Provisional streaming Nodes

An assistant Node can be created before its response is complete:

```
Node N20
  parent_id = N19
  role = assistant
  state = streaming
  part[0] = {kind: text, stream_id: S3}
```

While streaming:

- the Node is visible in live views as provisional;
- the Conversation head remains N19;
- content arrives through the appropriate part Stream(s);
- reconnecting clients can fetch S3's current content; and
- the Node is excluded from canonical history queries unless provisional
  content is explicitly requested.

On provider-confirmed completion:

1. seal every required open part Stream;
2. store the final content references/part manifest;
3. change Node state to `committed`;
4. set the completion reason;
5. compare/update the expected Conversation head;
6. increment Conversation revision; and
7. emit one structural change through the outbox/change feed.

If partial text never enters provider history, the Node becomes `aborted` and
the head does not move.

If the provider confirms that interrupted partial content remains in its
context, the Node can be committed with
`completion_reason = interrupted`.

### 8.5 Immutability

Before commitment:

- part Stream content can grow or receive source-supported corrections;
- state can move from streaming to committed/aborted.

After commitment:

- role, semantic parent, and content are immutable;
- corrections create a replacement/superseding Node or an explicit repair;
- provider identity aliases can be repaired without rewriting content; and
- projections can be rebuilt from committed Nodes and Operations.

### 8.6 Rewinds and forks without a Branch entity

The Node parent relation already creates a tree. A rewind changes the
Conversation head to an ancestor. A subsequent prompt creates a new child.

```
N1 -> N2 -> N3 -> N4
             \
              N5 -> N6
```

The active path is the ancestor chain of `head_node_id`.

A separate Branch entity is not required initially. If named branches or
multiple persistent heads become a user-facing feature, add a small
`conversation_heads` table later. The tree itself does not change.

### 8.7 Branch-sensitive and cumulative data

Branch-sensitive data must carry an anchor Node or derive from the active
ancestor path. Examples:

- current goal;
- current plan;
- current context summary;
- pending semantic question.

Cumulative data is tied to Conversation/AgentSession/Operation and does not
disappear when a branch is abandoned:

- tokens consumed;
- commands actually executed;
- files touched;
- elapsed runtime;
- alerts delivered.

This classification is declared by each projection.

---

## 9. AgentSession

### 9.1 Responsibility

An AgentSession represents one provider-native incarnation attached to a
Conversation.

Examples:

- a Claude Code resumable session;
- a Codex thread/rollout;
- an OpenCode session;
- a headless `claude -p` invocation with persistence;
- a `codex exec` thread;
- an SDK/app-server thread;
- an ephemeral non-resumable invocation.

It combines the earlier ProviderThread and Run concepts. Individual process
invocations are retained as operational attempt records only if needed.

### 9.2 Suggested record

```text
agent_sessions
  id                    UUID primary key
  conversation_id       foreign key
  provider_id           plugin ID
  backend_id            configured backend ID
  account_id            nullable configured account/profile ID
  external_id           nullable provider-native primary ID
  mode                  interactive | headless | sdk | server | remote
  state                 starting | active | idle | ended | lost | archived
  resumable             boolean
  persistence_kind      native_local | native_remote |
                        baqylau_captured | ephemeral
  source_ref            nullable transcript/rollout/thread reference
  started_at            timestamp
  last_seen_at           timestamp
  ended_at              nullable timestamp
  end_reason            nullable text/code
```

### 9.3 Process attempts are supporting records

Resuming the same provider thread may launch another OS process. That does not
create a new core identity.

If needed:

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

This table supports debugging and liveness without making process lifetime the
definition of conversational continuity.

### 9.4 External aliases

Provider-native IDs are always namespaced by backend and provider. If one
logical AgentSession is observed through multiple IDs:

```text
agent_session_aliases
  agent_session_id
  backend_id
  provider_id
  identity_kind
  external_id
  confidence
  provenance_id
  valid_from
  valid_until           nullable
  active
  observed_at
```

Association can be provisional. A mistaken alias can be reassigned without
moving already-written aggregate event history, because there is no aggregate
event stream.

### 9.5 Terminal is optional

An AgentSession does not require a terminal.

Interactive TUI control can have a supporting terminal binding:

```text
terminal_bindings
  agent_session_id
  terminal_adapter_id
  backend_id
  window_id
  pane/tab metadata
  observed_at
  state
```

SDK, app-server, ACP, or headless sessions simply have no binding. The
application asks a provider RuntimeDriver for semantic actions; the adapter
may use a terminal, RPC, SDK, process signal, or API.

### 9.6 Authority in headless execution

The ownership rule remains valid for `claude -p`, `codex exec`, and future
programmatic modes, but it needs precise wording:

> The provider owns any provider-native history it persists. baqylau owns its
> canonical Conversation, Node tree, Operations, and the exact inputs/outputs
> it captures at its boundary.

There are three cases:

1. **The provider persists a native thread/transcript.** The native source is
   the authority for provider-specific history and resume semantics. baqylau
   stores its normalized semantic view and retained Observations.
2. **The command exposes structured streaming output but no durable native
   history.** baqylau's captured request, JSONL/deltas, final response, and
   process receipt are the durable evidence available to baqylau. The
   AgentSession uses `persistence_kind = baqylau_captured`.
3. **The invocation is ephemeral or final-only.** baqylau can preserve the
   supplied input and observed output, but must not claim that it captured
   hidden provider context or can natively resume the invocation. The
   AgentSession is marked non-resumable and may use
   `persistence_kind = ephemeral`.

This is not dual ownership of one fact. Provider-native history and baqylau's
canonical semantic history are different facts with explicit provenance. A
later provider import can reconcile or enrich canonical state without silently
overwriting it.

### 9.7 Same-provider resume

When a provider can resume its native session:

- reuse the AgentSession;
- append an attempt record if process-level audit matters;
- refresh aliases/source references;
- transition state back to active; and
- preserve the same Conversation association.

If the provider creates a new native thread during a fork or account
migration, create a new AgentSession attached to the same or a new
Conversation according to the user-visible semantics.

### 9.8 Cross-provider continuation

A handover creates a new AgentSession for the target provider. It never
changes `provider_id` on the source row.

```
Conversation C1
  AgentSession A1: Claude, idle/resumable
  AgentSession A2: Codex, active
```

This makes handover reversible and keeps provider boundaries visible.

### 9.9 Nested runtimes and execution lineage

The legacy code has three materially different Codex placements:

- a standalone Codex host;
- a Codex sidecar launched inside a Claude Conversation; and
- a Codex-native child agent.

Conversation membership alone cannot recover that topology, and an Operation's
`parent_operation_id` cannot express that a whole AgentSession was launched by
another runtime. Use typed supporting links:

```text
agent_session_links
  from_agent_session_id
  to_agent_session_id
  relation               launched | delegated | forked | resumed |
                         migrated | handed_over
  operation_id           nullable launching/handover Operation
  source_position        nullable provider-local position
  provenance_id
  created_at
```

Examples:

```text
Claude AS1 --launched by O20--> Codex sidecar AS2
Codex AS3  --delegated by O41-> Codex child AS4
Claude AS5 --migrated by O63--> Claude/account-2 AS6
```

This is execution lineage, not conversation ancestry. AS2's semantic messages
may appear in an agent-scoped view or contribute results to AS1 without becoming
the active Conversation head.

A new task assigned to an existing child is a new `agent_task` Operation linked
to the same child AgentSession. Agent identity alone must not collapse several
assignments into one result.

### 9.10 Same-provider account migration

The existing `relimit` feature can resume a Claude native session under another
subscription account, sometimes with a model downgrade and an automatic
continuation nudge. Treat that as a durable migration workflow:

1. source AgentSession remains historical and immutable;
2. create `Operation(kind=account_migration)`;
3. close/settle or explicitly declare the source outcome;
4. start a new target AgentSession/attempt with the target account;
5. link it with `relation = migrated`;
6. associate the provider-native external ID with validity/provenance rather
   than assuming it is globally unique forever; and
7. activate only after resume/start evidence arrives.

If the implementation proves that an unchanged AgentSession plus a new attempt
is a better identity for a provider, the attempt row carries account/backend
history. In either representation, never mutate an old row's `account_id` and
thereby rewrite where prior usage/effects occurred.

External aliases therefore need optional validity bounds and an active flag.
An incoming external ID is resolved against active placement plus evidence; a
bare `(provider_id, external_id)` uniqueness constraint is insufficient for
cross-account migration, forks, and imported history.

### 9.11 Provider context checkpoints

The semantic Conversation tree and a provider's current model context are not
the same thing. Compaction proves it: the logical history remains visible while
the provider replaces some of its active context with a summary.

Store supporting checkpoints:

```text
context_checkpoints
  id
  agent_session_id
  at_node_id
  source_position
  summary_node_id        nullable user-visible summary Node
  summary_ref            nullable provider-only summary/evidence
  covers_from_node_id
  covers_through_node_id
  context_window
  context_used
  state                  observed | inferred | superseded | unknown
  provenance_id
  created_at
```

The Node tree answers “what happened.” A ContextCheckpoint answers “what this
provider is believed to remember at this point.” Handover can choose the
semantic branch plus the newest trustworthy checkpoint without deleting or
double-feeding the covered history.

---

## 10. Operation

### 10.1 Responsibility

An Operation represents structured work, observation, or control with a
useful lifecycle.

Initial core kinds include:

- `command`
- `tool`
- `file_read`
- `file_edit`
- `agent_task`
- `monitor`
- `compaction`
- `interaction`
- `message_delivery`
- `account_migration`
- `control`
- `handover`

Additional feature modules may register namespaced kinds without changing the
Operation base table.

### 10.2 Suggested record

```text
operations
  id                    UUID primary key
  conversation_id       foreign key
  agent_session_id       nullable/usually present
  anchor_node_id         nullable semantic message anchor
  parent_operation_id    nullable nested Operation
  turn_key               nullable opaque provider turn correlation
  task_key               nullable distinct assignment/task correlation
  actor_key              nullable provider-scoped actor correlation
  source_position        nullable provider-local sortable position
  kind                   stable core or namespaced extension kind
  state                  pending | running | succeeded | failed |
                         cancelled | denied | lost | unknown
  origin                 observed | requested | inferred | imported
  schema_version         integer/string
  data                   validated JSON for kind-specific fields
  result_ref             nullable BlobRef/manifest
  source_timestamp       nullable timestamp
  started_at             timestamp
  ended_at               nullable timestamp
```

The base columns hold cross-kind queries and invariants. `data` holds
kind-specific detail behind a registered schema.

When a kind becomes heavily queried or gains substantial invariants, it can
add a detail table keyed by Operation ID:

```text
command_details(operation_id, command, cwd, exit_code, execution_mode)
```

This avoids both extremes:

- a table/entity for every hypothetical feature; and
- one unvalidated JSON bag for all semantics forever.

### 10.3 Lifecycle

Allowed transitions are defined per kind but share common terminal states.

General rules:

- terminal states do not silently reopen;
- duplicate starts are idempotent on provider correlation key;
- a missing closer becomes `lost` or `unknown`, not fabricated success;
- inferred transitions record rule/provenance;
- source and ingestion timestamps remain distinct; and
- a later authoritative provider record can resolve an unknown state.

### 10.4 Nested work

`parent_operation_id` represents nested activity:

```
agent_task A1
  +-- file_read A2
  +-- command A3
  +-- file_edit A4
```

An actor is initially a value (`actor_key`), not a core entity. Existing
registers such as `main`, `sub:<id>`, `team:<id>`, and `codex:<id>` can be
normalized into provider-scoped keys.

Promote Actor only if it later needs independent profiles, permissions,
mailboxes, or lifecycles that cannot be expressed through AgentSession and
Operation.

### 10.5 Turns are correlations, not entities

`turn_key` groups Nodes and Operations belonging to one provider turn.

The core does not initially need a Turn table. Attention, presentation, and
handover compilation can group by the key plus timestamps/lifecycle.

Promote Turn only if it gains independent user-facing operations or
cross-process invariants.

`task_key` is separate from `actor_key`: one child/teammate can be assigned
several tasks over its lifetime. Legacy Codex child results demonstrated that
grouping by agent merges distinct assignments and can place a late result after
the parent answer it contributed to.

### 10.6 Tool request versus execution

An Operation distinguishes "the provider proposed a tool call" from "the
tool actually executed":

```
Operation O1
  kind = tool
  state = pending

permission denied
  -> state = denied

or execution starts
  -> state = running
  -> succeeded/failed/lost
```

The surrounding assistant message remains a Node. The tool lifecycle does not
need to masquerade as conversation ancestry.

### 10.7 Handover and control as Operation kinds

A handover or control gesture has the same useful lifecycle:

- requested/pending;
- executing;
- succeeded/failed/indeterminate (represented by unknown where necessary);
- source and target;
- attempt/output references; and
- user-visible audit.

It therefore starts as an Operation kind. If the handover workflow later
develops many indexed fields and constraints, add `handover_details` rather
than replacing the core model.

### 10.8 Containment is not causality

`parent_operation_id` means lifecycle containment: a command ran inside an
agent task. It must not also mean every other relationship.

Use a small typed link table for non-containment relationships:

```text
activity_links
  from_type             node | operation | agent_session | resource
  from_id
  to_type               node | operation | agent_session | resource
  to_id
  relation              result_of | contributes_to | caused_by | supersedes |
                        summarizes | delivered_as | produced | consumed
  provenance_id
  created_at
```

Only registered relation names are allowed. Core relations have domain rules;
plugins can register namespaced relations. This table is an adjacency index,
not a universal event bus and not permission to put arbitrary application
state into a generic graph.

The critical legacy example is:

```text
child task O20 result O24 --contributes_to--> parent final Node N31
```

The result can physically arrive after N31 yet render before it in the semantic
timeline. Wall-clock order alone is wrong.

### 10.9 Provider-local activity order

Nodes and Operations need a stable local position so one query can interleave
conversation and work. A timestamp is diagnostic, not an ordering key: clocks
move, late records arrive, and causality can intentionally override chronology.

Each provider adapter supplies the strongest available `source_position`:

- transcript byte/record position;
- rollout record/item position;
- app-server sequence;
- structured stream item index; or
- a daemon-assigned per-AgentSession ingest position when the source has none.

The canonical timeline composer orders by:

1. selected Conversation branch;
2. provider source position within an AgentSession/turn;
3. explicit causal links such as `contributes_to`;
4. handover/nesting boundaries between AgentSessions; and
5. local ingestion position as a deterministic fallback.

It never uses source timestamp as the sole cursor. A `conversation_activity`
projection materializes the resulting ordered references for fast backlog and
live delivery:

```text
conversation_activity
  conversation_id
  branch_head_id
  local_seq
  item_type             node | operation
  item_id
  block_key             pagination boundary
  source_revision
```

`local_seq` is scoped to a Conversation projection generation. It is not a
cross-session event position and may be rebuilt when late evidence changes
semantic placement. Page tokens include the generation/source revision so an
old cursor cannot silently skip or duplicate items.

### 10.10 Structured interactions

Questions, permissions, plan approval, confirmation menus, and provider
dialogs are `Operation(kind=interaction)` with a registered detail schema:

```text
interaction_details
  operation_id
  interaction_kind      question | permission | plan | confirm
  external_key
  prompt_ref
  options_ref
  response_ref
  response_revision
  state                  open | submitting | answered | dismissed |
                         expired | lost
```

The response is accepted by compare-and-set against the interaction identity
and revision. A stale browser card cannot answer a newer dialog. Provider screen
drivers may implement the effect, but the Operation remains provider-neutral
and exposes pending/unknown outcomes honestly.

The question/plan/result cards are timeline DTOs over this Operation. They do
not need to become fake user/assistant Nodes, but the handover compiler must
include their accepted semantic content.

---

## 11. Stream

### 11.1 Responsibility

A Stream is incremental content with its own local revision and retention.
It is a storage primitive, not an event bus.

Owners:

- a provisional assistant Node;
- command output Operation;
- tool output Operation;
- agent progress Operation; or
- another registered Operation kind.

An owner may have several Streams. This is required for stdout/stderr,
reasoning/progress/final text, several assistant content blocks, and providers
that expose tool output channels independently.

### 11.2 Suggested record

```text
streams
  id                    UUID primary key
  owner_type            node | operation
  owner_id              Node/Operation ID
  channel               text | stdout | stderr | reasoning | progress |
                        structured | namespaced extension
  ordinal               integer within owner/channel
  kind                  assistant_text | command_output |
                        tool_output | agent_progress | extension kind
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

Enforce exactly one owner plus uniqueness on
`(owner_type, owner_id, channel, ordinal)`. A Node part can reference its Stream
for ordered content assembly; Operation views discover their streams by owner
and channel. Streams do not point back through both nullable owner columns.

### 11.3 Normalized stream operations

Adapters can emit:

- `append(offset, bytes)`
- `replace(start, end, bytes)`
- `reset(bytes)`
- `seal(final_bytes?)`
- `abort(reason)`

SDK/app-server sources normally provide ordered deltas. Terminal screen
observations may provide only changing snapshots. Capability-declared modes
avoid pretending snapshots are lossless token streams.

### 11.4 Staging storage

Open Stream bytes do not produce one SQLite row per token or output chunk.
Use an append-only framed staging file per Stream:

```
runtime/streams/<stream-id>.open
```

Each frame records:

- local revision;
- source sequence range;
- operation type;
- offsets/range;
- payload length;
- payload;
- checksum.

Complete frames survive a crash; a torn final frame is discarded during
recovery.

Small coalesced AI text segments could be stored in SQLite, but one shared
framed-stream implementation is preferable because it also handles command
output without database write amplification.

### 11.5 Coalescing

Provider token deltas are coalesced by:

- a short time window (target range roughly 30–100 ms);
- a byte threshold;
- semantic boundaries where the source supplies them; and
- a hard latency ceiling.

This preserves terminal-like responsiveness without turning 100 tokens into
100 transactions and 100 client notifications.

### 11.6 Sealing

On completion:

1. flush and validate staging frames;
2. apply any replacement/snapshot operations;
3. compare with provider-final content when available;
4. record anomalies if the provisional and authoritative forms differ;
5. store the final bytes in the content-addressed blob store;
6. update Stream state/final_ref;
7. finalize the owning Node/Operation transactionally; and
8. remove or archive the staging file according to policy.

### 11.7 Backpressure

Slow surfaces must never block provider ingestion.

Each live client has a bounded queue. On overflow:

- discard queued incremental deltas for that client;
- send a `stream.resync` notification;
- let the client fetch the current Stream snapshot/range; and
- continue delivering later revisions.

Source capture and durable staging remain independent of client speed.

---

## 12. Supporting persistence tiers

The domain model is small; supporting persistence makes it reliable.

### 12.1 Metadata database

Initial choice:

- SQLite;
- WAL mode;
- foreign keys enabled;
- explicit transaction boundaries;
- short writes;
- indexed identity/correlation paths;
- migrations committed with the application; and
- no dependency on an event-sourcing recorder.

One database contains canonical metadata, inbox/outbox state, configuration,
and projections that require atomic joins. Bulk bytes stay outside it.

### 12.2 Blob store

Sealed large or immutable content is content-addressed:

```
blobs/<sha256-prefix>/<sha256>
```

Blob metadata includes:

- digest;
- length;
- media/content class;
- creation time;
- retention class;
- optional expiry;
- compression;
- reference counts or reachability metadata where needed.

The blob store holds:

- committed message text where size/policy justifies it;
- command output;
- tool results;
- diffs;
- file snapshots;
- plans;
- handover bundles;
- subagent results; and
- diagnostic payloads.

An expired blob yields a named unavailable/expired result. Domain rows remain
valid.

### 12.3 Raw Observation store

Raw external inputs are retained as evidence, with bounded class-based
retention:

```text
observations
  id
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
  processing_state
  mapper_name
  mapper_version
  error_ref
```

An Observation is not a domain event. It says what the boundary reported.

### 12.4 Provenance

Canonical facts link to their evidence and decision:

```text
provenance
  id
  observation_id
  rule_name
  rule_version
  decision
  created_at

provenance_links
  provenance_id
  entity_type
  entity_id
```

This generalizes v1's diagnostic decision string and must ship early, not
after the first painful debugging incident.

### 12.5 Projections

Projection tables are explicitly disposable. Each declares:

- owning module;
- source entities/revisions;
- rebuild scope;
- branch sensitivity;
- consistency requirement;
- update mode (same transaction or asynchronous);
- indexes and query contract.

Examples:

- Conversation list/session card;
- attention state;
- usage/cost;
- context saturation;
- tasks/goal;
- current model/effort;
- active time;
- agent/operation summaries;
- account limit strip;
- stats rollups;
- errors/health.

Projection capability is tri-state, not a nullable-value convention:

- `supported` with a value;
- `supported` but currently empty/unknown; or
- `unsupported` for this provider/mode.

The legacy provider fan-outs demonstrate why this matters: an empty tasks list
and a provider that has no task-list concept must not both become `None` and be
silently routed to another provider's reader.

### 12.6 Resource and artifact index

The legacy system already has user-facing artifact behavior: file/diff
expansion, click-to-copy, uploads, images, a memory-note tree, backlinks, and
search-result cards. Therefore the trigger for an artifact catalog has already
been met. Add a supporting Resource module now rather than forcing paths and
blobs into Operation JSON:

```text
resources
  id
  kind                  file | image | diff | plan | log | memory_note |
                        search_result | extension
  backend_id
  workspace_ref
  canonical_uri         nullable path/URI within its trust boundary
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

Resources are not a fifth universal aggregate. They are stable handles for
content already referenced by Nodes and Operations. A file path is never
treated as globally valid: backend/workspace and version/fingerprint travel
with it.

This supports:

- download/copy of raw command or message content;
- in-place file/diff expansion without persisting presentation paint ops;
- uploaded attachment validation;
- handover manifests;
- memory-note relationships and search hits; and
- expiry that leaves an honest unavailable Resource instead of a dangling
  string.

### 12.7 Preferences, input buffers, and surface state

Durable user-authored UI state is not a projection. It cannot be rebuilt from
Conversation history, and pretending it is domain truth would pollute the core.
Store it in an explicit preferences/interaction tier.

Examples from v1:

- composer and new-session drafts;
- a mirrored terminal input draft;
- queued/pending message indicators;
- ask/plan response drafts;
- notification mutes and global alert toggle;
- view mode, hidden directories, dismissed task cards;
- remembered pane size; and
- device push subscriptions.

Use typed records rather than a completely open shared KV:

```text
input_buffers
  id
  kind                  composer | new_session | interaction
  conversation_id       nullable
  interaction_id        nullable
  project_ref           nullable
  text_ref
  revision              server-assigned monotonic revision
  client_sequence       nullable client ordering hint
  origin                surface/device/terminal
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
  updated_at
```

Draft writes use compare-and-set/server revisions. A clear is a tombstone so an
older in-flight save cannot resurrect sent text. Terminal probes distinguish
unreadable from observed-empty, and a terminal-origin clear only clears the
draft lineage it previously produced.

A provider's actual mid-turn message queue is not this table. Sending creates a
`message_delivery` Operation; the UI may show it as queued/pending, and only a
provider-native prompt record commits the eventual user Node.

### 12.8 Usage, cost, and quota facts

Usage is neither an Operation nor a disposable projection at ingestion time.
Store source-labelled facts, then project totals:

```text
usage_facts
  id
  conversation_id       nullable
  agent_session_id       nullable
  actor_key              nullable
  account_id             nullable
  source                 provider | otel | transcript | imported
  ledger                 billing | per_actor_display | quota
  model
  input_tokens
  output_tokens
  cache_read_tokens
  cache_write_tokens
  source_position/dedup_key
  observed_at

quota_windows
  account_id
  provider_id
  scope_key              account/model/window
  used_percent
  resets_at
  state                  available | limited | logged_out | unknown
  observed_at
  provenance_id
```

Rules carried from the legacy implementation:

- OTEL billing and transcript per-agent display ledgers never add together;
- provider-native cumulative counters are delta-folded with source-position
  deduplication;
- cost is computed from token facts and a versioned price table on read;
- unknown models keep tokens but yield unknown cost;
- post-end provider/OTEL flushes may amend usage without reopening the session;
- account/model limit scope and reset time remain separate; and
- automatic target selection records its full reasoning/provenance.

### 12.9 Corrections and repair history

Direct relational persistence needs an explicit correction vocabulary. A
repair command can reassign an alias, repair an Activity link/position, replace
a projection, or supersede a bad canonical mapping. Every repair records:

- affected entity and old/new values or references;
- operator/tool identity;
- reason;
- evidence consulted;
- code/rule version; and
- timestamp.

This is targeted repair, not replay and not unrestricted SQL as a product API.
The audit CLI may retain an expert-only local SQL escape hatch, but routine
repairs should be named application commands.

---

## 13. Durable ingestion

### 13.1 Pipeline

```
external source
      |
      v
edge/backend adapter
      |
      v
INSERT Observation (deduplicated)
      |
      v
provider mapper -> canonical mutation proposal
      |
      v
transaction:
  update Conversation/Node/AgentSession/Operation
  link provenance
  mark Observation processed
  enqueue outbox/change rows
COMMIT
```

This yields exactly-once **canonical effects per Observation** through an
ordinary uniqueness constraint plus transaction, without requiring an
event-sourcing library.

### 13.2 Deduplication

Prefer source-provided identity:

```
(backend_id, provider_id, source_identity, source_sequence)
```

When absent, use a conservative dedup key derived from:

- source kind;
- provider/session identity;
- stable external record ID;
- payload digest; and
- bounded temporal/correlation context.

Dedup policy is versioned and provider-specific. False dedup is more damaging
than harmless duplicate evidence, so uncertain cases should remain distinct
and be reconciled at canonical correlation keys.

### 13.3 Mapper contract

A mapper does not write database tables directly. It returns a typed mutation
proposal:

```python
class CanonicalBatch:
    conversation_changes: list[...]
    node_changes: list[...]
    agent_session_changes: list[...]
    operation_changes: list[...]
    stream_changes: list[...]
    decisions: list[...]
```

The application service validates:

- identities and namespace;
- state transitions;
- expected Conversation revision/head;
- idempotency;
- schema versions;
- capability constraints; and
- provenance completeness.

### 13.4 Poison observations

A malformed provider payload must not wedge all sessions.

Rules:

1. Parse expected invalid data into an anomaly/unknown form where safe.
2. Catch unexpected mapper failure at the Observation boundary.
3. Record the exception, mapper version, payload reference, and source
   identity.
4. Mark the Observation quarantined.
5. Advance unrelated work.
6. Surface the degraded Conversation/provider in health projections.
7. Allow a repaired mapper to retry quarantined Observations explicitly.

The anomaly path has a recursion guard and must not depend on the failed
mapper.

### 13.5 Mapper working state

Load-bearing correlation state must be durable:

- open Operations live in Operation rows;
- Stream offsets/revisions live in Stream metadata/staging frames;
- provider cursors live in adapter cursor tables;
- pending dialog/control state lives in Operation/projection rows;
- source resequencing buffers use bounded durable staging if losing them
  changes semantics.

Ephemeral caches may accelerate these records but are never the sole copy.

### 13.6 Edge spooling

Logic-light does not mean memory-only.

When a shim cannot deliver an Observation within its hard deadline, it
appends the framed payload to an owner-only spool and exits successfully
toward the provider.

The daemon drains spools idempotently. Spool frames carry:

- source timestamp;
- edge instance;
- provider/session hints;
- payload checksum;
- late-delivery marker; and
- protocol version.

Planned daemon restart and unexpected crash therefore become delayed
observations rather than silent gaps where the edge supports spooling.

### 13.7 Probers

Silence/liveness evidence requires active observers. Probers are inbound
adapters, not policies that secretly perform I/O.

Examples:

- process-exit watchers;
- terminal-focus/screen probes;
- monitor liveness;
- provider API/account-usage polling;
- transcript/rollout file watchers;
- terminal draft observation.

They emit Observations through the same inbox, preserving why an inferred
transition occurred.

### 13.8 Answerable edge requests

The normal Observation path is asynchronous. That is insufficient for one
load-bearing legacy feature: Claude Code's `PreToolUse(Bash)` hook must return an
`updatedInput` synchronously to wrap a foreground command with a tee. Without
that reply, foreground output is visible only after the command finishes.

Support a narrow answerable-request lane:

```text
edge hook
  -> framed AnswerableObservation(request_id, hard_deadline)
  -> daemon validates identity + cheap provider transformer
  -> one short transaction stores Observation, decision, correlation, answer
  -> reply TransformResult
  -> edge prints provider-native response
```

Provider capability:

```python
class InputTransformer(Protocol):
    def transform(
        self, observation: Observation, snapshot: TransformSnapshot
    ) -> TransformDecision: ...
```

Rules:

1. Only explicitly registered observation kinds are answerable.
2. The transformer is deterministic, bounded, and performs no network or
   terminal I/O.
3. The relevant correlation/open-state snapshot is already durable and hot in
   memory; no broad query or projection rebuild occurs on the path.
4. The Observation, transform decision, returned payload, and any new
   correlation row commit together.
5. The edge has a strict provider-safe deadline. On timeout, daemon absence, or
   invalid response it proceeds unmodified and never fails the provider tool.
6. The later PostToolUse/transcript evidence reconciles the actual execution.
7. Rewriting that changes provider permission behavior is an explicit,
   provider/version-scoped security setting surfaced to the user. The legacy
   Claude `updatedInput` path requires an allow/ask decision; v3 must not hide
   that tradeoff.

This lane is synchronous request/reply, not event sourcing and not a general RPC
escape hatch. All ordinary hooks remain fire-and-forget/spooled.

### 13.9 Source readers and closer discipline

The legacy implementation has several independent physical stream sources:

- rewritten foreground-command tee files;
- provider background-task output files;
- process-discovered monitor output;
- subagent transcripts;
- Codex rollouts and companion logs;
- status-line and hook payloads; and
- OTLP batches.

A provider plugin registers source readers and closers. The mapper opens an
Operation/Stream only after storing enough durable correlation to rediscover
the source. A closer is driven by positive evidence where available:

- matching PostToolUse/result;
- provider task-complete/turn-aborted record;
- process exit;
- host exit;
- provider history final record; or
- explicit control receipt.

Display expiry may use a timeout; domain success may not. When no authoritative
closer exists, liveness probes can resolve an abandoned item to `lost` or
`unknown`, never `succeeded`.

File readers preserve the existing byte discipline:

- consume only complete frames/lines;
- retain the exact byte cursor;
- detect truncation/replacement/inode changes;
- bound each pump for fairness without imposing a lossy semantic line cap;
- keep stdout/stderr combination policy explicit; and
- record truncation/runaway caps on the owning Stream.

---

## 14. Provider integration architecture

### 14.1 One plugin, several narrow capabilities

A provider plugin is a composition of capability objects, not one growing
base class and not a bag of optional functions discovered by spelling.

Initial protocols:

```python
class ObservationDecoder(Protocol):
    def decode(self, observation: Observation) -> CanonicalBatch: ...

class InputTransformer(Protocol):
    def transform(
        self, observation: Observation, snapshot: TransformSnapshot
    ) -> TransformDecision: ...

class HistoryReader(Protocol):
    def discover(self, scope: DiscoveryScope) -> Iterable[NativeSession]: ...
    def import_history(self, native: NativeSession) -> CanonicalBatch: ...

class RuntimeDriver(Protocol):
    async def start(
        self, target: ExecutionTarget, bootstrap: BootstrapInput
    ) -> RuntimeHandle: ...
    async def resume(
        self, session: AgentSession, input: BootstrapInput | None
    ) -> RuntimeHandle: ...
    async def control(
        self, session: AgentSession, action: ControlAction
    ) -> ControlResult: ...

class LiveSource(Protocol):
    async def observations(self, handle: RuntimeHandle) -> AsyncIterator[Observation]: ...

class AttachmentEncoder(Protocol):
    def encode(self, resources: Sequence[Resource], mode: str) -> BootstrapInput: ...

class SourceReader(Protocol):
    async def follow(
        self, source: SourceDescriptor, cursor: SourceCursor
    ) -> AsyncIterator[Observation]: ...

class HandoverTarget(Protocol):
    def handover_capabilities(self) -> HandoverCapabilities: ...
    async def deliver(
        self, session: AgentSession, package: HandoverPackage
    ) -> DeliveryReceipt: ...

class UsageSource(Protocol):
    async def read_usage(self, scope: UsageScope) -> UsageObservation: ...
```

A plugin implements only capabilities that are truthful for that provider and
mode.

### 14.2 Capabilities are objects, not duplicated booleans

The presence of a registered implementation is the capability. Additional
constraints are data returned by it:

```json
{
  "start": ["interactive", "headless", "server"],
  "control": ["send", "interrupt", "rename", "compact"],
  "answerable_input": ["pre_tool_transform"],
  "attachments": ["path_mention", "image", "file"],
  "streaming": "ordered_delta",
  "resume": true,
  "foreign_history_import": false,
  "handover_delivery": ["mcp_resource", "bootstrap_file"],
  "terminal_required": false
}
```

Capabilities may differ by:

- provider version;
- backend;
- execution mode;
- account/plan;
- terminal availability;
- feature flags; and
- runtime probes.

Read facets also advertise supported/empty/unsupported explicitly. Capability
routing is by the AgentSession or actor that produced the item, not merely the
Conversation's current host: a Codex child inside a Claude host must be read by
the Codex adapter.

The UI renders these returned capabilities. It never branches on provider
names to decide what should work.

### 14.3 Provider knowledge stays jailed

Everything that knows a provider's:

- transcript/rollout record grammar;
- hook payloads;
- on-disk layout;
- session identity rules;
- screen anatomy;
- control commands;
- account/usage vocabulary;
- native import/export formats; or
- launch arguments

lives in that provider plugin.

The core knows only canonical types, provider/plugin IDs, and declared
capabilities.

### 14.4 Claude Code mapping

Conceptually:

| Claude source | Canonical target |
|---|---|
| User/assistant transcript message | Node |
| Tool use/result | Operation |
| Bash lifecycle/reporting | command Operation + Stream |
| PreToolUse `updatedInput` tee rewrite | InputTransformer decision + command correlation |
| Subagent/team lifecycle | agent_task Operation tree |
| Session UUID/transcript identity | AgentSession + alias/source_ref |
| Streaming Agent SDK/stream-json assistant text | provisional Node Stream |
| Hook/control result | Observation / control Operation |
| Compaction/summary | summary Node or compaction Operation, according to semantic effect |
| Ask/plan/permission dialogs | interaction Operation + response detail |
| Pasted/uploaded files | Resource + Node part, encoded as provider mention at delivery |

The transcript parser retains the measured narrow branch rule. It does not
turn arbitrary native siblings into semantic branches.

### 14.5 Codex mapping

| Codex source | Canonical target |
|---|---|
| Rollout/app-server user and assistant items | Node |
| Command/tool/file items | Operation |
| `item/agentMessage/delta` or exec JSONL deltas | provisional Node Stream |
| Thread ID/rollout UUID | AgentSession |
| Turn ID | turn_key |
| Native subagent/collaboration item | nested Operation + actor_key |
| Thread fork/import | new AgentSession and Node/head relationship |
| Child task start/result | task-keyed Operations + contributes-to link |
| Reasoning/progress channel | separate Operation/Node Stream when exposable |

Codex can be integrated as:

- an interactive TUI observed from rollouts/hooks;
- `codex exec --json`;
- app-server; or
- another supported programmatic interface.

Each mode produces the same canonical types but advertises different
stream/control capabilities.

### 14.6 OpenCode mapping

| OpenCode source | Canonical target |
|---|---|
| Session messages/parts | Nodes and Operations |
| `message.part.updated` | Stream operation |
| Tool hooks/events | Operation lifecycle |
| Session ID | AgentSession |
| Server SSE | Observation stream |
| Session export | HistoryReader input |
| ACP/server execution | RuntimeDriver/LiveSource |

OpenCode's server and event model may permit a cleaner programmatic adapter
than a hook/TUI integration. The architecture does not require all providers
to resemble Claude Code.

### 14.7 Unknown providers

Adding a provider requires:

1. plugin manifest;
2. at least one discovery/start path;
3. ObservationDecoder or HistoryReader;
4. RuntimeDriver for controllable sessions;
5. declared canonical mapping/version;
6. contract fixtures;
7. explicit declines for unsupported optional capabilities; and
8. no edits to core semantic or surface provider-name tables.

---

## 15. Runtime and control

### 15.1 Semantic controls

The application vocabulary uses whole actions:

- send;
- interrupt;
- rename/autoname;
- answer question;
- decide plan;
- rewind/fork;
- compact;
- switch model/effort;
- close;
- resume;
- migrate/handover.

It never exposes "press Escape" or "type slash command" as a core operation.

### 15.2 Control flow

```
surface request
    |
    v
validate auth + Conversation/AgentSession capability
    |
    v
create Operation(kind=control, state=pending)
    |
    v
transactional outbox request
    |
    v
provider RuntimeDriver
    |
    v
attempt/receipt Observations
    |
    v
Operation succeeded/failed/unknown
```

The HTTP request normally returns an accepted operation ID. Completion streams
through the structural change feed.

### 15.3 TUI-backed controls

A TUI-backed RuntimeDriver composes the Terminal port:

```
ClaudeRuntimeDriver
  -> TerminalInput
  -> TerminalScreen
  -> provider-specific dialog driver
```

The screen driver remains provider-specific. Terminal mechanics remain
terminal-specific. The composition occurs inside the provider adapter.

### 15.4 Programmatic controls

An SDK/app-server/ACP adapter implements the same semantic RuntimeDriver
without a terminal:

```
CodexAppServerDriver.control(interrupt)
  -> app-server turn interrupt RPC
```

No core or surface change is required.

### 15.5 Indeterminate outcomes

External controls are not exactly-once facts. A crash can occur after a key
press/RPC reached the provider but before receipt was stored.

The truthful outcome vocabulary is:

- succeeded;
- failed before action;
- rejected/unsupported;
- indeterminate after possible action.

The Operation state `unknown` plus attempt detail represents the last case.
Reconciliation probes may later resolve it.

### 15.6 Message delivery is not immediate Node creation

A surface send creates `Operation(kind=message_delivery)` with an idempotency
key and the intended content/resource manifest. It does not optimistically
commit a user Node.

Lifecycle detail can distinguish:

```text
accepted -> dispatching -> queued_at_provider -> observed_in_history -> delivered
                           \-> cancelled/lost/unknown
```

The provider's observed prompt record creates the canonical user Node and links
it with `delivered_as`. In a `baqylau_captured`/ephemeral headless mode with no
native history, the RuntimeDriver's durable input-acceptance receipt is the
authoritative boundary evidence and can commit the Node instead. This preserves
the distinction between:

- a browser's optimistic bubble;
- a message known to be in the TUI's mid-turn queue;
- a paste acknowledged by the terminal but not yet observed by the provider;
- a transport response lost after the action may have happened; and
- the actual provider-native prompt.

Queued status is capability/evidence-based. A stale attention colour alone is
not enough: the legacy code has to screen-probe turn motion because a terminal
cancel produces no hook and can leave the colour frozen.

The canonical matcher may use provider-specific evidence such as an external
ID or normalized content suffix, but stores the decision and ambiguity. It
must account for attachment prefixes and pre-existing terminal input without
silently associating the wrong Node.

### 15.7 Shared drafts and terminal input

The application exposes InputBuffer use cases separately from send:

- read/save/clear composer draft;
- read/save new-session draft by project;
- read/save an interaction response draft;
- observe terminal input; and
- consume/replace a provider-restored draft during resend.

Terminal screen observation is asymmetric:

- non-empty observed text may update a terminal-origin draft;
- observed empty clears only the terminal-origin revision it descends from;
- unreadable is not empty;
- unchanged screen text is not repeatedly written; and
- text baqylau just pasted is suppressed for a short correlation window so it
  is not echoed back as a new draft.

Modal interactions block ordinary sends unless the provider explicitly
supports a concurrent queue. Otherwise pasted text can enter the question or
plan dialog and be lost.

### 15.8 Rewind spans conversation and workspace planes

A rewind target can mean:

- conversation only;
- code/workspace only; or
- both.

Changing `Conversation.head_node_id` models only the first. A rewind Operation
therefore records:

- target Node and provider-native checkpoint identity;
- requested mode;
- pre-action Conversation revision/head;
- pre-action workspace revision/fingerprint;
- provider control attempt;
- observed post-action provider head;
- observed post-action workspace fingerprint; and
- partial/indeterminate outcome if only one plane changed.

The Conversation head moves only after provider/history evidence confirms the
semantic branch. Workspace restoration is an external effect and never inferred
from the head change. A provider menu offering different modes is adapter
vocabulary returned at runtime, not a universal hard-coded list.

### 15.9 Durable multi-step workflows

Handover, account migration, rewind-both, and close-then-resume are sagas in the
plain sense: several local transactions and non-transactional external effects.
Do not add a generic workflow language initially. Each Operation kind owns a
versioned detail table/checkpoint reducer and uses the common outbox/attempt
machinery.

Every step records:

- expected precondition revision;
- effect request/attempt;
- observed receipt or reconciliation evidence;
- next safe step;
- compensation/manual recovery guidance; and
- cooldown/loop guard where automatic retries could oscillate.

For legacy `relimit`, closing the source, waiting for end/park evidence, starting
the target account, and observing the resumed session are distinct checkpoints.
A daemon crash must resume from them without launching a duplicate provider.

---

## 16. Streaming assistant responses and operational output

### 16.1 Source capability levels

Providers/modes declare:

1. **ordered semantic deltas** — SDK/app-server/JSONL gives item identity and
   ordered text deltas;
2. **snapshot revisions** — a screen/probe gives only the current provisional
   display;
3. **final only** — the assistant message appears only after provider commit.

The product degrades honestly:

- ordered deltas provide terminal-like live text;
- snapshots provide best-effort provisional display with correction;
- final-only sessions show activity state followed by the final message.

### 16.2 Live response lifecycle

```
provider reports assistant item start
  -> create provisional Node + open Stream

provider emits deltas
  -> coalesce + append staging frames
  -> publish stream revisions to live broker

provider emits final item/transcript record
  -> reconcile content
  -> seal Stream
  -> commit Node
  -> advance Conversation head
```

### 16.3 Multiple items in one provider turn

A provider turn may alternate:

```
assistant text
tool request
tool execution/result
assistant text
```

Represent it as:

```
Node N10: assistant text
Operation O11: tool
Operation O12: command
Node N13: assistant continuation
```

All share a `turn_key`. Each live assistant item has its own provisional Node,
with one or more part Streams when the provider separates text/reasoning or
content blocks. This prevents one monolithic mutable "turn response."

Provider-private chain-of-thought is never requested or manufactured. A
provider-exposed reasoning summary/progress item can be represented as a
separate channel/Operation subject to provider policy and surface visibility.

### 16.4 Structural feed versus stream feed

The structural feed carries low-frequency durable changes:

- Node started/committed/aborted;
- AgentSession state;
- Operation state;
- projection revision;
- control/handover outcome.

The stream feed carries high-frequency coalesced content:

- stream started;
- append/replace/reset delta;
- stream resync required;
- stream sealed/aborted.

Structural clients use a retained change cursor. Stream clients use Stream ID,
revision, and offset/current snapshot.

### 16.5 Client registration race

For a Conversation view:

1. register the live connection;
2. read a snapshot and captured structural high-water mark;
3. include open Streams with their revisions/lengths;
4. fetch missing Stream content;
5. replay structural changes after the snapshot mark;
6. splice into live delivery with deduplication.

If a Stream queue overflows during catch-up, refetch that Stream's current
snapshot. Do not restart the entire page or replay every token delta.

### 16.6 Rendering

Surfaces receive neutral text/structured deltas.

- Terminal panes sanitize control sequences and render with local width.
- Web escapes/sanitizes every render.
- CLI can print normalized text.
- MCP consumers can ignore provisional deltas and wait for committed Nodes.

Incremental Markdown is surface policy. A surface may:

- render plain text while streaming and rich Markdown on seal;
- rerender the current block incrementally; or
- render only stable completed blocks.

---

## 17. Cross-provider handover

### 17.1 Definition

Cross-provider handover is:

> **A new target AgentSession attached to the same Conversation, bootstrapped
> from a versioned, provider-neutral snapshot of one semantic head and its
> relevant work state.**

It is not:

- mutation of the source AgentSession's provider;
- raw transcript conversion;
- transfer of hidden model state;
- replay of prior commands;
- inheritance of approvals/credentials;
- continuation of process or shell state.

### 17.2 Handover Operation

Start with `Operation(kind=handover)`:

```json
{
  "source_agent_session_id": "AS1",
  "source_head_node_id": "N47",
  "source_conversation_revision": 184,
  "source_workspace_revision": "sha256:...",
  "target": {
    "backend_id": "local-mac",
    "provider_id": "codex",
    "account_id": "personal"
  },
  "bundle_ref": "blob:...",
  "target_agent_session_id": null,
  "omissions_ref": "blob:..."
}
```

If Handover later needs indexed states/fields beyond Operation's lifecycle,
add a one-to-one `handover_details` table.

### 17.3 Workflow

#### Step 1 — request and settle

Validate:

- target capability and configuration;
- source Conversation/AgentSession;
- active semantic head;
- workspace/backend accessibility;
- pending dialogs/permissions;
- in-flight non-portable Operations;
- user authority.

If the source is active, choose explicitly:

- wait for current turn to settle;
- interrupt and reconcile;
- snapshot live with declared uncertainty; or
- refuse until a safe boundary.

#### Step 2 — logical snapshot

Record:

- Conversation ID/revision/head;
- selected live ancestor path;
- source AgentSession;
- newest trustworthy source ContextCheckpoint;
- workspace root, git HEAD, branch/worktree;
- dirty-state fingerprint;
- changed/untracked file manifest;
- open/unknown Operations;
- current projection versions; and
- retention/redaction policy.

The snapshot is immutable. Later source changes produce divergence, not silent
mutation of the delivered bundle.

#### Step 3 — compile portable context

The HandoverCompiler builds:

1. objective;
2. constraints;
3. important decisions;
4. open questions;
5. older-history summary;
6. recent near-verbatim dialogue tail;
7. current plan/tasks;
8. relevant Operation ledger;
9. typed Resource/artifact/diff/log references;
10. workspace state;
11. explicit omissions/uncertainties;
12. provenance and source revision.

Accepted interaction responses, provider-injected constraints, and peer
messages that materially changed the task are included with their origin. Open
composer drafts, notification state, surface preferences, hidden reasoning, and
unaccepted optimistic sends are not conversation context.

The compiler uses deterministic selection first. Optional AI summarization is
an optimization whose model/prompt/version and source inputs are recorded.

#### Step 4 — apply target budget/capabilities

Targets commonly accept a linear context, not an arbitrary tree. The compiler:

- selects one semantic head;
- linearizes its ancestor path;
- preserves the full tree only in baqylau;
- summarizes older material;
- includes the recent tail within a target budget;
- converts Operations to portable facts, not fake target tool calls;
- encodes Node parts/Resources through the target's attachment capabilities;
- uses ContextCheckpoint coverage to avoid feeding both an old context region
  and its summary as if both were independently current; and
- provides large evidence on demand.

#### Step 5 — workspace preparation

Conversation and filesystem transfer are separate:

- same backend/cwd: verify current files and fingerprint;
- new worktree: create at recorded revision and apply patch;
- different backend: sync Git revision, dirty diff, required untracked files,
  and selected artifacts;
- unsafe conflict: fail or require a user decision.

PIDs, monitors, and shell-local state do not transfer. Their status appears in
the omissions/work ledger.

#### Step 6 — create target AgentSession

Use the target RuntimeDriver to create a fresh native provider session/thread.
Store its identity immediately and attach it to the Conversation in a
starting state.

#### Step 7 — deliver package

Choose the strongest supported strategy:

1. supported native foreign-history import;
2. structured role-history import;
3. MCP handover resource;
4. read-only bootstrap file;
5. direct structured initial input;
6. compact inline prompt.

Raw foreign transcript rewriting is never a fallback.

An MCP-based bootstrap can say:

```text
You are continuing an existing software-development conversation.
Read handover H1 from the baqylau MCP service.
Verify repository state before changing files.
Treat prior commands and tool results as historical evidence, not actions
you performed. Do not repeat completed work.
```

#### Step 8 — acknowledgement

The target acknowledges through a structured result/tool:

```json
{
  "handover_id": "H1",
  "accepted": true,
  "understood_objective": "...",
  "recognized_workspace_revision": "sha256:...",
  "recognized_open_items": ["..."],
  "questions": []
}
```

Validate obvious mismatches before activation.

#### Step 9 — activation

On success:

- mark target AgentSession active;
- mark source idle/superseded but resumable;
- mark handover Operation succeeded;
- keep the same Conversation and semantic head;
- record provider boundary in the timeline;
- append the target's first genuine assistant Node under the selected head.

The bootstrap is infrastructure input, not a fake human Node. It is retained
in handover provenance and normally collapsed in presentation.

### 17.4 What transfers

| Category | Treatment |
|---|---|
| Human/assistant semantic history | Live branch; older summary + recent tail |
| Decisions/constraints/open questions | Direct structured fields |
| Changed files/diffs | Manifest plus artifacts |
| Test/build results | Summary plus full-output reference |
| Commands already executed | Historical facts, never auto-replayed |
| Tool results | Relevant result/artifact, not native target tool records |
| Subagent results | Result and open task status |
| Context/model/usage metadata | Informational where portable |
| Abandoned branches | Remain in baqylau; omitted by default |

### 17.5 What does not transfer

- hidden reasoning/model state;
- provider prompt cache;
- provider-native compaction internals;
- permission approvals;
- credentials;
- pending dialogs;
- running processes/PIDs;
- terminal input/screen state;
- shell functions and volatile environment unless explicitly captured;
- native tool-call identifiers;
- provider-specific subagent runtime state.

The bundle reports omissions explicitly.

### 17.6 Provider-specific accelerators

A provider's supported importer may preserve more native texture. Use it as an
adapter optimization and record:

- importer version;
- source items selected;
- returned target identity;
- warnings/omissions;
- supplemental bootstrap applied.

The canonical handover workflow does not depend on any one import command
being present or symmetric.

### 17.7 Divergence and rollback

If the source Conversation changes after snapshot:

- detect revision/head mismatch;
- mark the handover source-diverged;
- offer delta compilation or a separate semantic branch;
- never silently interleave two active providers by timestamp.

If target startup/delivery fails:

- source AgentSession remains intact;
- target is ended/failed or archived;
- handover Operation records failure;
- no Conversation head moves.

---

## 18. Durable effects and the transactional outbox

### 18.1 Outbox

Any committed state change requiring external action inserts an outbox row in
the same transaction:

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

Examples:

- provider control;
- session launch/resume;
- terminal tab paint;
- pane lifecycle;
- alert delivery/retraction;
- peer message delivery;
- webhook/push;
- structural client-feed notification.

### 18.2 Effect attempts

```text
effect_attempts
  id
  outbox_id
  adapter_id
  attempt_number
  started_at
  ended_at
  outcome
  receipt_ref
  error_ref
```

Workers claim with leases, use idempotency keys where the external system
supports them, and reconcile indeterminate attempts.

### 18.3 Idempotent versus non-idempotent effects

Idempotent:

- set tab colour to a value;
- refresh file watch;
- publish a replaceable projection invalidation.

Non-idempotent:

- type keys/text;
- send Telegram/push;
- launch a terminal tab;
- send a peer message.

Non-idempotent effects require a pre-effect attempt/lease. A crash after action
but before receipt becomes indeterminate and must not blindly retry.

### 18.4 Time

Persist state and deadlines; keep timers ephemeral:

```text
alert/control/workflow row: due_at = ...
async timer: only a wake-up optimization
```

On restart:

- reload open due items;
- schedule future items;
- process overdue items now;
- recheck current conditions before acting.

Presentation ticks remain surface-local.

---

## 19. Queries, projections, and client delivery

### 19.1 CQRS-lite

Writes go through application use cases and domain rules.

Reads use:

- canonical tables directly for simple entity/history queries;
- indexed projection tables for expensive/current facets;
- blob/Stream range reads for large content; and
- provider adapters only for explicitly live external data that is not a
  stored domain fact.

There is no requirement to route reads through domain repositories if a
read-only SQL query is clearer.

### 19.2 Snapshot APIs

Representative routes:

```text
GET /api/v1/conversations
GET /api/v1/conversations/{id}
GET /api/v1/conversations/{id}/nodes?head=&before=&limit=
GET /api/v1/conversations/{id}/operations?before=&limit=&kind=
GET /api/v1/conversations/{id}/activity?head=&before=&blocks=
GET /api/v1/agent-sessions/{id}
GET /api/v1/streams/{id}
GET /api/v1/streams/{id}/content?after=&limit=
GET /api/v1/blobs/{ref}
GET /api/v1/resources/{id}
GET /api/v1/input-buffers/{scope}

POST /api/v1/conversations
POST /api/v1/agent-sessions/{id}/actions
POST /api/v1/conversations/{id}/handovers
POST /api/v1/conversations/{id}/messages
PUT /api/v1/input-buffers/{scope}
POST /api/v1/interactions/{id}/responses
```

The detail snapshot includes:

- Conversation revision/head;
- active path tail;
- attached AgentSessions/capabilities;
- open/recent Operations;
- open Streams/revisions;
- projections with source revisions;
- health/uncertainty;
- control capabilities.

### 19.3 Backlog and pagination

Initial history is ordinary compressed HTTP, not SSE:

- newest useful block/message window;
- backward pagination;
- boundaries aligned to whole semantic activity blocks;
- one server-composed activity page from Nodes and Operations;
- a page token containing branch head, projection generation/source revision,
  and block boundary; and
- explicit resnapshot if late evidence invalidated that generation.

Do not make clients merge `/nodes` and `/operations` by timestamp. The legacy
dashboard had to implement the same merge for initial backlog, lazy history,
and live deltas, plus a special causal child-task pass. v3 makes the server's
ActivityComposer the single owner of that order.

SSE/live transport carries increments after the snapshot, not hundreds of KB
of initial history.

### 19.4 Structural change feed

Use one retained feed generated from committed changes/outbox:

```json
{
  "change_id": 44102,
  "type": "conversation.changed",
  "entity_id": "C1",
  "entity_revision": 184
}
```

The feed can carry typed deltas where useful or merely invalidate a query
resource.

It is a delivery mechanism, not domain truth. Retention is bounded. When a
cursor expires, the server returns an explicit resnapshot response.

### 19.5 Projection consistency

Each snapshot includes projection source revisions. For a query requiring a
consistent bundle:

- update cheap critical projections in the canonical transaction; or
- wait until required asynchronous projections reach the requested
  Conversation revision; or
- return the facet with an explicit stale/source revision.

Never race sibling followers and silently present stale final state.

### 19.6 Attention

Attention is a projection derived from:

- AgentSession state;
- open/terminal Operations;
- dialog/permission observations;
- current actor/turn correlations;
- provider-specific notification classification;
- Conversation activity.

Its provider-neutral precedence is explicit and fixtured. The legacy states
roughly require this order, highest first: asking/permission, executing,
background/agent running, working/thinking, done, then idle. Actor filtering
prevents a child's inner hook from painting the host as if the main actor
changed state. Provider notification payloads are classified by adapters;
unknown notification kinds do not silently mean “done.”

It is not an event-sourced aggregate and does not own the open-operation
ledger.

Compute attention first per AgentSession/actor scope. A Conversation-level
attention row is an explicit aggregate for list/notification policy—normally
the active interactive AgentSession plus any other session with a blocking
interaction. Do not let one busy sidecar erase an asking main session, or one
finished child paint an unrelated provider session done.

The projection can retain transition history for alert/debug consumers:

```text
attention_current(scope_type, scope_id, state, source_revision, ...)
attention_transitions(id, conversation_id, agent_session_id,
                      from_state, to_state, cause, ...)
```

Rebuilding current attention does not re-notify old alerts because alert
delivery consumes real-time transition rows/outbox entries with freshness and
idempotency rules, not projection replay.

### 19.7 Presence and notifications

The legacy notifier contains product semantics that cannot be reduced to
“attention changed, send push.” Model notification intent and delivery as
durable supporting workflow records:

```text
notification_intents
  id
  conversation_id
  agent_session_id       nullable source scope
  attention_transition_id
  kind                  asking | done
  state                 armed | suppressed | delivering | delivered |
                        retracting | retracted | expired
  due_at
  policy_version
  cause/reason

notification_deliveries
  id
  intent_id
  channel_id
  device_id             nullable
  effect_attempt_id
  external_handle_ref   nullable
  state
  expires_at
```

Presence is ephemeral evidence with TTL, scoped correctly:

- device active;
- a browser viewing a particular Conversation;
- a terminal application active;
- a particular terminal tab focused;
- composing an input buffer; and
- interaction activity observed on a provider screen.

Policy preserves the measured asymmetry:

- asking can alert immediately because the provider is blocked;
- done waits for a settle window so a transient green state does not alert;
- looking at a done response can resolve/retract it;
- merely looking at an unanswered question does not resolve it;
- composing cancels/retracts relevant reminders;
- device-wide presence may suppress a duplicate pre-delivery alert but must not
  erase already delivered reminders for unrelated Conversations;
- channel escalation and retraction depend on durable delivery handles; and
- mute/global/device routing preferences are read at decision time.

Timers are reconstructed from intent rows on restart. This improves on v1's
in-memory pending/sent collections, whose delivered handles are forgotten by a
dashboard restart.

---

## 20. Terminal and surface architecture

### 20.1 Terminology

Use:

- **Surface** — web, pane, CLI, phone, MCP.
- **Terminal adapter** — kitty, future terminal, null.
- **Provider adapter** — Claude Code, Codex, OpenCode.
- **Backend** — machine/service connection on which integration runs.

Avoid using "frontend" for both terminal control and a UI.

### 20.2 Narrow terminal protocols

Instead of one unbounded interface, compose role protocols:

- TerminalPresence;
- TerminalDiscovery;
- TerminalDisplay;
- TerminalInput;
- PaneManager;
- ViewportReader;
- FocusProbe;
- Clipboard.

A terminal implements only supported roles. A null implementation is normal,
not exceptional.

### 20.3 Pane host

The pane host remains a thin process inside the pane because it owns:

- stdout/scrollback;
- SIGWINCH;
- local width;
- incremental renderer state;
- click/viewport behavior.

It consumes Conversation snapshots and live changes/Streams. The daemon sends
semantic content, not prewrapped ANSI.

### 20.4 Presentation security

Sanitize at the rendering leaf:

- terminal: allow only owned SGR/OSC constructs;
- web: HTML escape/sanitize and allowlist link schemes;
- blobs: non-renderable content type + nosniff;
- new surfaces: explicit sanitizer contract.

Raw provider/output evidence is never rewritten merely to make one surface
safe.

### 20.5 Portable presentation blocks

The legacy mirror is richer than plain text: command headers/bodies/outcomes,
copy groups, expandable file/diff views, actor scope, activity classes,
line-number metadata, and stream completion chips all reflow at the current
width. The domain must remain presentation-free, but surfaces still need one
semantic presentation contract.

The API/presenter layer maps Nodes, Operations, Resources, and Streams into
typed blocks such as:

```text
MessageBlock
CommandBlock(header, request_ref, output_streams, outcome, resource_links)
FileChangeBlock(resource, verb, extent, additions, removals, diff_ref)
AgentTaskBlock(task_key, actor, phase, content_ref)
InteractionBlock(interaction_id, state, options/response)
NoticeBlock(severity, text, provenance_link)
```

Blocks can expose semantic actions—copy request, copy output, expand Resource,
open file, inspect evidence—using opaque IDs. They never contain terminal
escape sequences or trusted HTML. Terminal and web renderers independently
choose colors, glyphs, Markdown policy, folding/view modes, and width.

The ActivityComposer owns block boundaries and ordering. Pagination never cuts
inside a block; this preserves gap/overlap-free lazy backlog and makes live and
reloaded views agree.

### 20.6 Pane and terminal lifecycle

Pane management is an effect workflow, not part of AgentSession identity.
Preserve the legacy invariants:

- headless/anchorless sessions do not create phantom panes;
- pane creation is anchored to the provider's actual terminal tab, not current
  focus;
- opening helper panes must not steal application focus;
- remembered width is a preference scoped to the intended project/backend;
- resize is measured/reconciled because terminal application is asynchronous;
- stale panes from a resumed/forked runtime are closed by verified binding;
- session end closes panes, finalizes/archives state, and clears verified tab
  presentation in a declared order; and
- failed terminal effects are not persisted as successful desired/observed
  state.

Viewport restoration and click-to-expand are surface capabilities. If absent,
content remains accessible through the Resource API; the domain behavior does
not depend on exact terminal scroll control.

---

## 21. Configurable backends, accounts, and execution targets

### 21.1 Backend

A Backend configuration answers "where can this run or be observed?"

Examples:

- local daemon host;
- another Mac reached through an authenticated agent;
- remote workstation over a configured transport;
- future hosted runner.

Suggested configuration fields:

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

Do not bake one tunnel topology into the frontend or core.

### 21.2 Accounts

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

Credentials should remain in keychain/provider-native stores where possible.
`credential_ref` points to them; it is not the secret.

Plugins supply account discovery/usage/switching capabilities. The UI renders
returned profiles and never reads one developer's account file format.

### 21.3 Execution target

An ExecutionTarget is a validated value:

```
backend_id + provider_id + optional account_id + mode/config
```

It need not be a core entity. The configuration/application layer can return
available targets with capabilities to surfaces.

### 21.4 Remote backends

Do not prematurely distribute the canonical database. A remote backend adapter
can:

- launch/observe provider processes remotely;
- frame Observations back to the controller;
- spool during disconnection;
- expose terminal/runtime capabilities;
- transfer workspace artifacts.

Only introduce per-backend canonical stores and replication when a concrete
offline/multi-controller requirement exists.

---

## 22. Extensions and plugins

### 22.1 Initial trust model

Plugins are trusted in-process Python packages registered at the composition
root. They can execute code with daemon authority; installation is therefore
an administrative action.

Future untrusted plugins require a subprocess/RPC sandbox. The same capability
protocols can cross that boundary later.

### 22.2 Manifest

A plugin manifest declares:

- stable ID;
- version;
- plugin type(s);
- provided capability implementations;
- compatible core protocol versions;
- configuration schema;
- Observation schemas;
- Operation kinds and their JSON schemas;
- plugin-owned migrations/detail tables;
- optional surface contributions;
- permissions/trust requirements.

### 22.3 Extension data

Rules:

1. Core fields remain stable and provider-neutral.
2. Extension Operation data is namespaced and version-validated.
3. An extension needing indexed relational state owns a namespaced/detail
   table keyed by stable core IDs.
4. Extensions do not mutate another module's tables directly.
5. Cross-module changes use public application services.
6. Arbitrary EAV is not the default.

### 22.4 Surface extensions

A feature extension may register:

- query endpoints;
- control commands;
- navigation/tab descriptors;
- live change types;
- typed view schemas.

It does not inject unsanitized HTML or import provider/terminal internals.
The frontend renders declared data through shared components where possible.

### 22.5 Architecture enforcement

Contract tests verify:

- capability registration and signatures;
- explicit implemented/declined matrix;
- plugin/core import direction;
- provider-name literals absent from core/surfaces except declared registries;
- Operation schema/version registration;
- terminal adapter conformance;
- no plugin direct access to another module's tables;
- config/frontend capability payload completeness.

---

## 23. Collaboration and session-to-session communication

Collaboration starts as a first-party domain module built on stable
Conversation and AgentSession IDs. It is not purely future: Claude teammate
mail, inbox/read tracking, broadcasts, and Codex child follow-ups already
exercise part of this problem. The future extension is direct communication
between otherwise independent Conversations/providers.

It does not require changes to the core model.

Possible module-owned tables:

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

Delivery uses provider RuntimeDriver/MCP capabilities and the outbox.

Rules:

- peer content is untrusted data with provenance;
- it cannot approve permissions;
- it is never auto-executed;
- delivery is quota/loop limited;
- recipient policy/consent controls injection;
- messages can wait for a natural turn boundary;
- activity is visible/auditable;
- broadcast delivery/read state is tracked per recipient copy, not only per
  message ID;
- one child/peer can receive several task assignments without merging them;
- filesystem claims remain advisory unless a violation detector exists.

The module can later promote participants/mailboxes without introducing Actor
into the minimal core prematurely.

---

## 24. Security boundaries

### 24.1 Threat model

Reaching provider controls can mean code execution on a backend. The design
must protect:

- HTTP/MCP control plane;
- local/remote ingestion sockets;
- backend credentials;
- provider/account credentials;
- terminal control;
- blob contents;
- plugin installation;
- handover/bootstrap inputs.

### 24.2 HTTP

Retain:

- loopback bind by default;
- authenticated reverse proxy/edge identity for remote exposure;
- application credential as defense in depth;
- secure HttpOnly SameSite cookie for browser;
- JSON/custom-header/origin CSRF guard;
- no permissive CORS;
- read-only deployment mode;
- separate capability/auth checks on every mutation.

Do not hard-code a particular tunnel vendor into the core.

### 24.3 Ingestion socket

Filesystem permissions alone do not distinguish trusted processes running as
the same user.

Use:

- per-edge installation identity/secret where meaningful;
- OS peer credentials/PID ancestry as provenance;
- source-kind allowlists per socket/channel;
- daemon-minted-only presence/control records;
- anomaly detection for implausible sender/provider/session combinations;
- no acceptance of raw user-minted gesture results.

This may make forgery detectable rather than impossible for a compromised
same-user process; the threat model must say so.

### 24.4 Handover security

Before packaging:

- redact known secrets;
- exclude credentials and approvals;
- treat provider/model output as untrusted;
- include only selected artifacts;
- label provenance;
- make target revalidate repository/environment;
- require explicit authority for cross-backend transfer.

A handover package is context, not an authorization token.

### 24.5 Plugin security

Trusted in-process plugins have daemon authority. The manifest and UI must
state:

- filesystem/network needs;
- external binaries;
- credential access;
- control capabilities;
- schemas/routes added.

Third-party plugin marketplaces are deferred until process isolation and
permission policy exist.

### 24.6 Resources, uploads, clipboard, and dictation

- Uploaded names are sanitized; size/media limits are enforced before storage.
- A Resource path supplied back to a provider must resolve inside the staged
  upload/workspace root authorized for that backend. Basename or text claims
  alone never authorize an arbitrary local path.
- Clipboard path discovery is a local privileged capability, exact-match
  constrained and separately audited.
- Clipboard images are cleared/consumed only by provider adapters whose send
  path would otherwise attach them accidentally.
- Blob/resource downloads use safe content disposition, content type, and
  `nosniff`; active content is not rendered directly.
- Browser dictation can use a short-lived restricted third-party token minted by
  a surface service. Audio need not traverse the baqylau daemon, and the
  provider/API secret never goes to the browser.
- Surface-only telemetry records client-observed transport failures without
  claiming that a server-side effect failed.

---

## 25. Crash recovery and degradation

### 25.1 Daemon restart

On startup:

1. acquire single-instance ownership/supervisor identity;
2. recover SQLite/migrations;
3. scan open staging Streams and discard torn frames;
4. reload unprocessed/quarantined Observations;
5. recover expired outbox leases;
6. reload open Operations and deadlines;
7. restart file/process/terminal/provider watchers from durable registrations;
8. reconcile active AgentSessions;
9. drain edge spools;
10. publish health/degradation changes.

### 25.2 Source outage

Record an Observation gap/degraded source state where detection is possible.
Do not report confident current attention/session state when the only source
has been unavailable beyond its declared freshness.

### 25.3 Lost closer

If an Operation remains open:

- provider-specific evidence may close it;
- process watcher may mark it lost;
- a later authoritative history import may resolve it;
- presentation may fail off after a display-only horizon;
- state remains unknown if evidence cannot determine the result.

Quiet time alone is not generic proof of completion.

### 25.4 Stream crash

An open Stream after crash is recovered from frames.

- If provider final history exists, reconcile and seal.
- If source reconnects, continue from source/local sequence where possible.
- If no source can confirm completion, mark Stream/owner lost or aborted
  according to evidence.
- Never silently convert partial bytes into a successful committed Node.

### 25.5 Database failure

Hooks/edges degrade to spool/pass-through. The daemon:

- stops accepting state-changing control requests;
- keeps best-effort source capture if safe;
- surfaces health loudly;
- does not let an unavailable audit path break provider work.

### 25.6 Supervisor

The deployment design must name:

- supervisor (e.g. launchd for the primary macOS deployment);
- socket activation/handover strategy if used;
- restart/backoff/crash-loop behavior;
- upgrade command;
- migration/backup policy;
- log locations;
- health command;
- safe shutdown sequence.

---

## 26. Performance design

### 26.1 Principles

1. Bulk bytes bypass per-item metadata fan-out.
2. SQLite transactions stay short.
3. Source deltas are coalesced.
4. Work is fair across Conversations.
5. Latency-sensitive lifecycle/control work has priority over bulk progress.
6. Reads use indexed query shapes, not replay.
7. Projections rebuild per Conversation/entity where possible.
8. Performance gates measure interaction between workloads.

### 26.2 SQLite writer

SQLite serializes writes. Use one application-level writer service or tightly
controlled transaction gateway with:

- bounded queue;
- explicit lanes: answerable hook replies, lifecycle/control, ordinary
  structural ingestion, and projection/maintenance;
- fair scheduling by Conversation/source;
- batchable independent updates;
- hard transaction-duration instrumentation;
- no blocking subprocess/file/network I/O inside transactions.

Do not enqueue each token or output chunk as a transaction.

The answerable lane has a reserved latency budget but cannot starve ordinary
work indefinitely. Its transaction contains only dedup/correlation/decision
rows required to return the provider reply.

The legacy per-session DBs avoided cross-session writer contention while the
global audit DB accepted cross-session evidence. v3's single metadata DB is a
benchmark hypothesis, not doctrine. Before schema lock, compare:

1. one WAL database with the prioritized writer; and
2. a catalog database plus per-Conversation partitions behind the same storage
   gateway.

Prefer one DB if it meets the compound latency gates because atomic invariants,
backup, and queries are simpler. If it does not, partition only Conversation-
local Nodes/Operations/activity while keeping explicit non-atomic boundaries;
do not casually spread one use-case transaction across SQLite files.

### 26.3 Bulk output

Command/tool output goes to Stream staging files. Metadata updates are coarse:

- start;
- periodic revision/length checkpoint;
- important truncation/runaway marker;
- seal/end.

Presentation caps do not destroy stored content. A separately named retention
or runaway policy may truncate with an honest marker.

### 26.4 Indexes

At minimum:

- Node by Conversation/parent/created time;
- committed source external identity;
- AgentSession by provider/backend/external identity;
- Operation by Conversation/time/kind/state;
- activity position/link by referenced item and relation;
- materialized Conversation activity by head/generation/local sequence;
- open Operation by AgentSession/state;
- Observation dedup/source cursor;
- outbox by state/priority/available_at;
- projection source revision;
- alias lookup;
- Resource by backend/workspace/URI and version;
- InputBuffer/preferences by scope and revision;
- UsageFact dedup/account/session/source position;
- Stream state/update time;
- provenance by entity and Observation.

### 26.5 Benchmarks

Migration entry gates include compound scenarios:

1. One session streams a large build log.
2. A second session transitions to asking/executing.
3. Web and terminal live clients are connected.
4. An assistant response streams concurrently.
5. A control gesture is issued.

Measure:

- p50/p95/p99 Observation-to-attention;
- answerable-hook end-to-end and daemon-transaction p99 against the provider's
  safe deadline;
- tab paint completion;
- assistant delta display latency;
- control acceptance/completion;
- SQLite transaction time/queue depth;
- dropped/resynced client deltas;
- CPU/memory;
- stream disk throughput;
- catch-up after daemon restart.

Also measure a long-session backlog containing interleaved Nodes, Operations,
late child-task results, and expandable Resources. Initial load and every lazy
page must use the same ActivityComposer ordering and must not perform an
O(nodes × operations) timestamp merge.

Benchmarks must state pass thresholds before migration.

### 26.6 PostgreSQL trigger

PostgreSQL becomes justified by a deployment requirement such as:

- multiple controller processes writing shared canonical state;
- true multi-user server deployment;
- remote shared database;
- write concurrency exceeding SQLite after bulk separation and batching;
- operational backup/HA requirements.

It is not selected to satisfy a third-party library. Storage SQL is isolated
behind gateways/modules, but portability is tested rather than claimed.

---

## 27. Relational schema outline

This is illustrative, not the final migration SQL.

### 27.1 Core

```sql
CREATE TABLE conversations (
  id TEXT PRIMARY KEY,
  title TEXT,
  head_node_id TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  project_ref TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  archived_at REAL
);

CREATE TABLE agent_sessions (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  provider_id TEXT NOT NULL,
  backend_id TEXT NOT NULL,
  account_id TEXT,
  external_id TEXT,
  mode TEXT NOT NULL,
  state TEXT NOT NULL,
  resumable INTEGER NOT NULL,
  persistence_kind TEXT NOT NULL,
  source_ref TEXT,
  started_at REAL NOT NULL,
  last_seen_at REAL NOT NULL,
  ended_at REAL,
  end_reason TEXT
);

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
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
  completion_reason TEXT,
  source_timestamp REAL,
  created_at REAL NOT NULL,
  committed_at REAL
);

CREATE TABLE operations (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id),
  agent_session_id TEXT REFERENCES agent_sessions(id),
  anchor_node_id TEXT REFERENCES nodes(id),
  parent_operation_id TEXT REFERENCES operations(id),
  turn_key TEXT,
  task_key TEXT,
  actor_key TEXT,
  source_position TEXT,
  kind TEXT NOT NULL,
  state TEXT NOT NULL,
  origin TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  data TEXT NOT NULL,
  result_ref TEXT,
  source_timestamp REAL,
  started_at REAL NOT NULL,
  ended_at REAL
);

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
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  sealed_at REAL,
  UNIQUE(owner_type, owner_id, channel, ordinal)
);

CREATE TABLE resources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  backend_id TEXT,
  workspace_ref TEXT,
  canonical_uri TEXT,
  media_type TEXT,
  current_version_id TEXT,
  retention_class TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE node_parts (
  node_id TEXT NOT NULL REFERENCES nodes(id),
  ordinal INTEGER NOT NULL,
  kind TEXT NOT NULL,
  media_type TEXT,
  content_ref TEXT,
  stream_id TEXT REFERENCES streams(id),
  resource_id TEXT REFERENCES resources(id),
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

CREATE TABLE agent_session_links (
  from_agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id),
  to_agent_session_id TEXT NOT NULL REFERENCES agent_sessions(id),
  relation TEXT NOT NULL,
  operation_id TEXT REFERENCES operations(id),
  source_position TEXT,
  provenance_id TEXT,
  created_at REAL NOT NULL,
  PRIMARY KEY(from_agent_session_id, to_agent_session_id, relation)
);
```

The Conversation head foreign key may require a deferred constraint or
post-create migration due to the circular relation. Application invariants
still verify membership/state.

`streams.owner_type/owner_id` and heterogeneous `activity_links` cannot use an
ordinary foreign key for both target tables. Registered owner/relation services
validate them transactionally; optional SQLite triggers can enforce the closed
core owner types. No plugin writes these tables directly.

### 27.2 Evidence and delivery

```sql
CREATE TABLE observations (...);
CREATE TABLE provenance (...);
CREATE TABLE provenance_links (...);
CREATE TABLE outbox (...);
CREATE TABLE effect_attempts (...);
CREATE TABLE agent_session_aliases (...);
CREATE TABLE agent_session_attempts (...);
CREATE TABLE terminal_bindings (...);
CREATE TABLE conversation_workspaces (...);
CREATE TABLE conversation_title_facts (...);
CREATE TABLE context_checkpoints (...);
CREATE TABLE resource_versions (...);
CREATE TABLE interaction_details (...);
CREATE TABLE input_buffers (...);
CREATE TABLE preferences (...);
CREATE TABLE usage_facts (...);
CREATE TABLE quota_windows (...);
CREATE TABLE notification_intents (...);
CREATE TABLE notification_deliveries (...);
CREATE TABLE conversation_activity (...);
CREATE TABLE repair_records (...);
```

### 27.3 Revisions

Entity revisions can be added to AgentSession, Node, Operation, and projection
rows where optimistic concurrency/client invalidation benefits from them.
Do not add a single global domain sequence. The structural delivery feed has
its own monotonically increasing `change_id`.

### 27.4 JSON discipline

`operations.data`:

- is validated against kind + schema_version;
- contains no unbounded output bytes;
- is not used for core foreign identities that need indexes;
- promotes frequently queried fields to detail tables;
- preserves unknown additive plugin fields;
- never substitutes for provider raw Observation retention.

---

## 28. Python package structure

One possible layout:

```text
baqylau/
  kernel/
    ids.py
    time.py
    results.py
    capabilities.py

  domain/
    conversation.py
    node.py
    agent_session.py
    operation.py
    stream.py
    rules.py

  application/
    ingest.py
    answerable.py
    conversations.py
    controls.py
    handover.py
    streams.py
    activity.py
    interactions.py
    resources.py
    input_buffers.py
    projections.py
    queries.py

  ports/
    providers.py
    terminals.py
    backends.py
    alerts.py
    transform.py
    storage.py

  adapters/
    providers/
      claude_code/
        plugin.py
        decode.py
        history.py
        runtime.py
        screen.py
        handover.py
      codex/
        plugin.py
        decode.py
        rollout.py
        appserver.py
        runtime.py
        handover.py
      opencode/
        plugin.py
        decode.py
        server.py
        runtime.py
        handover.py
    terminals/
      kitty/
      null/
    storage/
      sqlite/
      blobs/
      streams/
    backends/
      local/

  infrastructure/
    inbox.py
    outbox.py
    provenance.py
    effects.py
    workflow.py
    supervisor.py
    watchers.py
    probers.py

  projections/
    attention.py
    conversation_index.py
    usage.py
    context.py
    tasks.py
    stats.py
    health.py
    activity.py

  transport/
    edge_socket/
    http/
    sse/
    mcp/

  surfaces/
    panehost/
    web/
    cli/

  extensions/
    registry.py

  composition.py
  main.py
```

The exact folders matter less than enforced dependency direction and public
module ownership. Avoid a global `folds/policies/reactors` topology that
splits one feature across the whole repository merely to satisfy event-log
terminology.

Pydantic belongs at protocol/config/plugin boundaries. Domain values can use
stdlib dataclasses/enums. SQLite rows and API models need not be the same
class.

---

## 29. Testing strategy

### 29.1 Architecture tests

- import direction;
- no provider/terminal literals in domain/surfaces;
- capability manifest completeness;
- plugin schema registration;
- adapter contract suites;
- no direct cross-module table mutation;
- null/headless substitutability.

### 29.2 Mapper fixtures

For every provider:

```
raw Observation sequence
  -> expected Nodes
  -> expected AgentSession changes
  -> expected Operations
  -> expected Stream frames
  -> expected provenance decisions
```

Port existing measured transcripts/rollouts and bug fixtures verbatim.

### 29.3 Domain/property tests

- Node tree/head invariants;
- rewind/fork behavior;
- stale-head concurrent append creates divergence;
- Node streaming commit/abort;
- Operation transition tables;
- idempotent duplicate ingestion;
- alias reassignment;
- handover snapshot immutability;
- multi-part Node and multi-channel Stream ownership;
- provider-local activity ordering with clock movement/late input;
- causal child-task result placement before the consuming final answer;
- repeated tasks on one child stay distinct;
- ContextCheckpoint summary coverage;
- InputBuffer stale-write/tombstone races;
- message delivery never commits a Node before provider evidence;
- interaction response CAS/stale-card refusal;
- branch-sensitive versus cumulative projection behavior;
- outbox lease/idempotency/indeterminate cases;
- Stream frame/torn-write recovery.

### 29.4 Projection tests

Pure table-driven/property tests over canonical rows:

- attention precedence and closers;
- usage/pricing;
- current context/compaction;
- title/tasks/goal;
- active time;
- stats/error rollups;
- source revision/staleness.
- activity backlog/live ordering equivalence and whole-block pagination;
- supported-empty versus unsupported provider facets;
- notification suppress/retract asymmetry and restart recovery;
- two-ledger usage/source precedence.

### 29.5 Adapter contracts

- provider discovery/ownership;
- RuntimeDriver semantic actions;
- streaming mode behavior;
- answerable InputTransformer deadline/pass-through behavior;
- foreground tee/reporter command preservation and exit-code fidelity;
- attachment encoding by provider capability;
- source cursor/inode/truncation handling;
- terminal role protocols;
- backend spool/reconnect;
- HandoverTarget delivery/ack;
- account/capability enumeration.

### 29.6 End-to-end

Scenarios:

- interactive Claude with terminal;
- headless Claude streaming;
- interactive/headless Codex;
- OpenCode server mode;
- daemon killed mid-stream;
- daemon unavailable during hook;
- answerable hook under SQLite contention;
- foreground/subagent/background/monitor stream closer matrix;
- malformed payload quarantine;
- same-provider resume;
- Claude -> Codex -> Claude handover;
- handover source divergence;
- multi-session build flood plus control/attention;
- web reconnect with open Streams;
- terminal pane resize/reflow/sanitize;
- shared draft device race and terminal-input reconciliation;
- queued send, transport-unknown send, and eventual prompt match;
- ask/plan/permission dialog control and stale identity;
- rewind conversation/code/both with partial failure;
- account migration close/resume crash recovery;
- file/diff expansion, copy, uploads, and Resource expiry;
- teammate mail broadcast/read tracking and repeated child tasks;
- notification delivery/escalation/retraction across restart;
- no-terminal null adapter.

### 29.7 Parity

v1 and v3 consume the same live traffic/fixtures and compare:

- attention/tab output;
- conversation live branch;
- command/tool/file summaries;
- agent/subagent activity;
- usage/context/tasks/title;
- rendered terminal/web blocks;
- timeline ordering and lazy-page boundaries;
- composer/new-session/interaction drafts and preferences;
- queued/optimistic message delivery state;
- questions, plans, permissions, and accepted responses;
- click-to-view/copy/resources and attachments;
- team mail, child-task contribution order, and nested provider sessions;
- notification presence/suppression/retraction;
- account limits, migrations, and usage ledger precedence;
- alert decisions;
- control results;
- audit explanations.

Differences require an explicit accepted product decision.

---

## 30. Migration strategy

Use a strangler migration. v1 remains authoritative until each plane passes
its gate.

### Phase 0 — coverage inventory and benchmarks

- Mechanical v1 feature/env/doc lesson -> v3 owner or explicit drop.
- Use section 36's code-audit matrix as the initial checklist; expand it to
  individual test/fixture IDs before deleting a legacy plane.
- Freeze fixture corpus.
- Implement compound performance benchmarks.
- Decide retention, backup, supervisor, and installation contracts.

### Phase 1 — daemon, storage, Observation inbox

- SQLite schema/migrations;
- blob/Stream staging;
- Resource/version and preferences/InputBuffer stores;
- edge protocol and spooling;
- answerable-request transport in pass-through-only mode;
- provider Observation capture;
- provenance/quarantine/health;
- no production effects.

Gate: no lost supported/spooled observations across restart tests; poison
payload isolation; performance baseline.

### Phase 2 — canonical import/read model

- Conversation/Node/AgentSession/Operation mapping for Claude;
- Node parts, activity positions/links, ContextCheckpoints, and nested
  AgentSession lineage;
- v1/history importer;
- read-only API;
- web adapter or compatibility payloads;
- branch/head parity.

Gate: conversation/session list and history parity on live and parked corpus.

The gate includes merged activity order, late child results, discarded prompt
branches, plan/question records, sidecar/native child scope, and whole-block
pagination—not only user/assistant text.

### Phase 3 — streaming

- assistant provisional Nodes/Streams;
- command output Streams;
- answerable Claude foreground tee transform;
- background/monitor/subagent/Codex source readers and closer parity;
- structural/live client feeds;
- reconnect/resync;
- web/pane rendering.

Gate: fidelity, sanitization, resize, catch-up, and compound-load latency.

### Phase 4 — projections

- attention;
- usage/context/tasks/title;
- Resource/memory/input-buffer views;
- provider capability absence semantics;
- operation/agent views;
- stats/health.

Gate: days of v1/v3 observable agreement with explained differences.

### Phase 5 — control/outbox

- RuntimeDriver controls;
- effect attempts/reconciliation;
- message_delivery and interaction workflows;
- rewind conversation/code/both workflow;
- durable notification intents/deliveries and presence policy;
- terminal controls;
- programmatic no-terminal controls;
- capability-driven UI.

Every exclusive effect plane has durable ownership/lease during coexistence.

### Phase 6 — additional providers/backends

- Codex programmatic mode;
- OpenCode server/ACP mode;
- backend/account configuration;
- same-provider account migration workflow;
- target enumeration;
- provider contract proof without core/surface edits.

### Phase 7 — handover

- compiler/package;
- workspace verification/transfer;
- target delivery;
- acknowledgement;
- divergence/rollback;
- provider boundary presentation.

Handover is off the initial replacement critical path unless product priority
changes.

### Phase 8 — retire v1 by plane

Deletion lags disablement. Preserve import tooling and read compatibility for
parked v1 history until a declared sunset.

---

## 31. Tradeoff ledger

| Gained | Paid |
|---|---|
| Conversation tree represented directly | Provider adapters must perform semantic mapping rather than dump every native record |
| Small core model | Several narrow supporting tables—parts, links, resources, buffers, checkpoints—are still required for real behavior |
| Stable mixed activity order | Activity projection generations/page tokens are more complex than timestamp merge |
| Multimodal/multi-channel content | More joins and ownership validation than one text/stream column |
| Explicit drafts/preferences | A separate mutable-state tier with CAS/retention policy |
| SQLite-friendly transactional state | No automatic event replay/snapshot framework |
| Targeted repair/rebuild | Need explicit migrations and repair commands |
| High-volume Streams outside DB log | Two coordinated storage shapes and stream recovery |
| Provider/terminal/headless independence | Capability matrices and contract tests |
| Handover without raw transcript conversion | Cross-provider continuation is context rehydration, not exact native resume |
| Durable inbox/outbox reliability | More infrastructure tables/workers than plain CRUD |
| Audit with decisions/provenance | Additional write/storage discipline |
| One daemon/modular monolith | Supervised process remains a critical component |
| Surface-neutral semantic state | Presenters must own real rendering complexity |
| Explicit unknown/lost states | UI/product must tolerate uncertainty rather than always show a clean answer |

---

## 32. Deferred promotions and their triggers

### Branch entity

Trigger:

- named branches;
- several user-managed heads;
- branch metadata/permissions;
- branch-level handover/merge operations.

Until then: Node tree + Conversation head.

### Turn entity

Trigger:

- user-visible turn operations;
- cross-process turn continuation invariants;
- substantial turn-indexed querying that keys cannot support.

Until then: `turn_key`.

### Actor entity

Trigger:

- independent actor profiles/lifecycle;
- permissions;
- durable actor mailbox;
- identity across AgentSessions/providers.

Until then: provider-scoped `actor_key` and nested Operations.

### Dedicated Handover entity/table

Trigger:

- many indexed handover fields;
- complex retry/approval workflow;
- partial/delta transfers;
- handover reporting as a primary product surface.

Until then: Operation kind + versioned data/blob.

### Generalized Resource graph

The basic Resource/version index is no longer deferred: the legacy file/diff
viewer, uploads, copy links, memory browser, and search cards already justify
it (section 12.6).

Still deferred:

- arbitrary user-authored Resource relationship types;
- cross-user sharing/permissions;
- full-text indexing of every artifact;
- global artifact browsing detached from Conversation/project scope; and
- treating the Resource layer as a universal knowledge graph.

Promote those only after concrete product queries and authorization rules exist.

### PostgreSQL

Trigger: section 26.6.

### Out-of-process plugins

Trigger:

- third-party marketplace;
- untrusted plugins;
- crash isolation needs;
- language-independent adapters.

### Distributed backend stores

Trigger:

- offline backend operation requiring local canonical decisions;
- multiple controllers;
- replicated/multi-user state.

---

## 33. Implementation order

The first implementation slice should prove the architecture with the fewest
features:

1. SQLite migrations and transaction gateway.
2. Blob/Resource/Stream staging and crash recovery.
3. Observation inbox, dedup, quarantine, provenance.
4. Conversation/Node/NodePart/AgentSession/Operation records and rules.
5. One Claude HistoryReader/ObservationDecoder subset.
6. Provider-local positions, Activity links, and one ActivityComposer backlog.
7. Snapshot query showing committed/provisional Nodes and Operations.
8. One assistant streaming path.
9. Answerable InputTransformer plus one command output Stream path.
10. Structural change feed + resync.
11. Attention projection.
12. InputBuffer and message-delivery proof.
13. Null/headless RuntimeDriver proof.
14. One terminal adapter proof.

Do not begin with:

- every v1 feature;
- Rust edge optimization;
- handover;
- third-party plugin loading;
- PostgreSQL;
- full SPA rewrite.

First prove:

- tree/head correctness;
- activity order/backlog correctness;
- durable mapping;
- live Stream behavior;
- synchronous transform fallback/deadline behavior;
- SQLite performance under mixed load;
- provider/terminal independence;
- crash recovery.

---

## 34. Architectural laws

These are normative:

1. A Conversation is not a provider session, process, account, or terminal.
2. A Node is semantic conversation content; an Operation is work.
3. Conversation ancestry is explicit and never inferred from global arrival
   order.
4. Native provider siblings do not automatically mean semantic branches.
5. Only committed Nodes can become the Conversation head.
6. Committed Nodes are immutable.
7. AgentSession identities are namespaced by provider and backend.
8. Terminal binding is optional.
9. Missing evidence yields unknown/lost, not invented success.
10. Bulk bytes do not travel through per-token metadata transactions.
11. Raw Observations, mapping decisions, and external-effect receipts are
    distinct audit layers.
12. Provider adapters translate and drive; they do not define core semantics.
13. Surfaces own presentation and sanitize at the leaf.
14. External effects use outbox/attempt/idempotency discipline.
15. Projections declare source revision, branch class, and rebuild scope.
16. Cross-provider handover creates a new AgentSession; it does not mutate the
    source.
17. Handover transfers context and work state, never authority or credentials.
18. Capabilities come from implementations/probes, not provider-name branches.
19. New core entities must earn independent identity, lifecycle, invariants,
    and queries.
20. v1's measured rules are ported by fixture and parity, not memory.
21. Node content is ordered and multimodal; provider attachment syntax is an
    adapter concern.
22. An owner may have several named Streams.
23. Conversation ancestry, activity containment, causal contribution, and
    execution lineage are distinct relationships.
24. Provider-local source position and causal links order activity; wall clock
    alone never does.
25. A surface send is not a committed user Node until provider history observes
    it or a baqylau-owned headless runner durably accepts it as the authoritative
    input.
26. Provider context checkpoints do not delete semantic history.
27. Drafts/preferences are durable user state, not projections or Nodes.
28. An answerable edge request always has a provider-safe pass-through failure
    mode.
29. Rewind changes the Conversation head and workspace only through separately
    observed outcomes.
30. Usage facts preserve source/ledger identity before totals are projected.

---

## 35. Consolidated architecture outcome

The v3 core is intentionally small:

```
Conversation
  provider-independent continuity and active semantic head

Node
  semantic message tree with ordered content parts, provisional while streaming
  and immutable once committed

AgentSession
  provider/backend/account-specific incarnation, terminal optional

Operation
  typed lifecycle for work and controls, nestable and extensible

Stream
  named local-revision incremental content; owners may have several channels
```

Around it:

- Observations and provenance explain inputs and decisions.
- Inbox transactions provide idempotent canonical effects.
- Outbox and attempts make external effects recoverable and honest.
- Projections make reads fast without becoming truth.
- Activity positions and causal links produce one stable Node/Operation
  timeline without changing the Conversation tree.
- Context checkpoints describe provider memory without deleting semantic
  history.
- Resources make attachments, file/diff views, memory, copy, and handover
  portable across surfaces/backends.
- Input buffers/preferences preserve multi-device drafts and UI state outside
  the domain/projection split.
- Answerable input transforms preserve Claude foreground streaming while every
  failure degrades to provider-safe pass-through.
- Message-delivery and interaction workflows keep optimistic UI distinct from
  provider-confirmed facts.
- Usage and notification facts preserve their source/policy semantics.
- Provider capabilities support radically different integration styles.
- Terminal adapters remain optional.
- Handover compiles one Conversation head and relevant Operations into a
  portable context package for a new AgentSession.
- Extensions add capabilities, typed Operation kinds, detail tables, and
  surfaces without replacing the core vocabulary.

This design preserves the strongest lesson from the event-sourced proposal—
facts and boundaries must be separated from presentation—while choosing a
persistence model that matches the actual domain: a conversation tree,
provider-native incarnations, structured operational lifecycles, and live byte
streams.

---

## 36. Legacy implementation audit and parity corrections

### 36.1 Audit method and scope

This section is based on code, tests, and measured design documentation—not
only the rewrite proposals.

The audit traced representative behavior through all four stages:

1. physical source and ingestion;
2. correlation/domain state;
3. read-model/presentation assembly; and
4. control/effect/reconciliation.

Primary implementation areas reviewed:

- `core/state.py`, `slots.py`, `tail.py`, `audit.py`,
  `sessionapi.py`, `hostpane.py`, `tabs.py`, `tabpaint.py`,
  `childtask.py`, `agentblocks.py`, `ops.py`, `streamfmt.py`,
  `copy.py`, and the rendering/terminal primitives;
- Claude hook dispatch, transcript parsing, foreground/background/monitor
  streaming, subagents, teams/mail, tasks, memory, status-line usage, account
  migration, adoption, dialogs, and HostControl;
- Codex rollout parsing, watching, standalone/sidecar/native-child modes,
  controls, dialogs, usage, title handling, and stream rendering;
- the terminal interface and kitty adapter;
- dashboard snapshots, merged backlog/live delivery, controls, drafts,
  attachments, preferences, presence, notification channels, Resource-like
  views, and client audit;
- the behavioral suite, especially conversation merging, dialog races,
  notification semantics, stream recovery, Codex parity, memory, copy/view,
  audit, tab state, and account migration.

This was an architectural audit, not a claim that every line of the 47k-line
legacy implementation should survive. The question was: can v3 preserve the
user-visible behavior and the hard-won correctness invariant without smuggling
legacy implementation details into the core?

### 36.2 Verdict

After the corrections in this revision, no important legacy feature is
fundamentally incompatible with the proposed architecture.

Before the audit, however, several features were not cleanly achievable by the
suggested schema/ports. They would have required ad hoc JSON, client-side
merging, provider-name branches, or a new core entity invented during
implementation. Those were real design gaps, not missing prose.

| Pre-audit gap | Why the prior design was insufficient | Correction |
|---|---|---|
| Synchronous foreground-command rewrite | The intake pipeline was asynchronous, but Claude `PreToolUse` must receive `updatedInput` before the command runs | Answerable-request lane + `InputTransformer` (§13.8) |
| Message/tool/child-result ordering | Nodes and Operations were separate lists with timestamps; legacy has causal order that intentionally differs from time | Provider-local position + Activity links + server ActivityComposer (§10.8–10.9) |
| Child and sidecar topology | `parent_operation_id` cannot relate whole provider sessions or repeated tasks | AgentSession lineage links + separate `task_key` (§9.9, §10.5) |
| Attachments and structured content | One `content_ref` and one Stream per Node could not represent images/files/structured parts | Ordered Node parts + Resource references + multiple Streams (§8, §11, §12.6) |
| stdout/stderr/reasoning/progress channels | One `stream_id` per owner collapses independent streams | Streams own typed channels; owners may have many (§11) |
| Compaction/context semantics | A tree head says what happened, not which earlier content a provider replaced with a summary | ContextCheckpoint supporting records (§9.11) |
| Shared drafts/preferences | These are durable user-authored values, neither domain history nor rebuildable projections | InputBuffer/preferences tier with revisions/tombstones (§12.7) |
| Optimistic/queued send truth | Creating a user Node on POST would assert delivery before provider/runner evidence | `message_delivery` Operation; Node only on observed prompt or authoritative headless input receipt (§15.6) |
| Rewind with code restoration | Moving Conversation head cannot describe or verify worktree restoration | Two-plane rewind workflow (§15.8) |
| Ask/plan/permission state | Generic control JSON did not define stale-card identity, draft, accepted response, or dialog loss | Structured interaction detail + CAS (§10.10) |
| Account migration identity | Mutating AgentSession account loses historical placement; a bare external-ID uniqueness rule can reject valid migration/fork shapes | New session/attempt placement plus lineage and validity-bounded aliases (§9.10) |
| File/diff/memory/copy behavior | Deferring the artifact catalog ignored already-existing user-facing Resource features | Basic Resource/version index is now required (§12.6) |
| Usage source precedence | A generic “usage projection” can double-count OTEL and transcript ledgers | Source-labelled UsageFacts and quota windows (§12.8) |
| Restart-safe alert retraction | An attention projection plus outbox did not preserve channel handles/policy workflow | Durable notification intents/deliveries (§19.7) |

The minimal core remains Conversation, Node, AgentSession, Operation, and
Stream. The corrections are mostly supporting records and use-case contracts.
The audit therefore enlarges the architecture's precision, not its conceptual
center.

### 36.3 Ingestion, lifecycle, and evidence coverage

| Legacy behavior | Evidence in implementation | v3 owner | Required parity |
|---|---|---|---|
| Every hook reaches one dispatcher but preserves handler identity | Claude/Codex dispatchers stamp handler-specific audit vocabulary | Edge adapter + Observation source metadata | Preserve semantic handler/source identity even if executable names change |
| Hooks must never fail the provider | Hook harnesses swallow/audit; audit has a spool fallback | Edge transport + Observation spool | Hard provider deadline; pass-through on daemon/DB/decoder failure |
| Foreground command streaming requires input rewrite | `plugins/claude_code/cmd_pre.py` tees stdout/stderr through `updatedInput` | `InputTransformer` + command Operation/Stream | Return synchronously; persist transform decision/correlation; later result reconciles |
| Rewrite changes permission behavior | Claude requires allow/ask for updated input | Provider/version security capability | Explicit opt-in/policy and visible tradeoff; never imply ordinary permission semantics |
| Background/monitor/foreground/subagent output uses different physical discovery | Tee files, task output glob, redirects, process/lsof discovery, rollout watchers | Provider SourceReaders/SourceDescriptors | Preserve source type, cursor, ownership, closer, and truncation semantics |
| Complete-line/byte discipline | Tailers retain cursors, reject torn lines, detect replacement/truncation | Stream staging + SourceCursor | No partial JSON parse; cursor survives restart; bounded pump fairness |
| Cancellation often emits no hook | PID liveness, transcript growth, screen/rollout rechecks close stale state | Probers + provider closers | Missing closer becomes lost/unknown; no timeout-manufactured success |
| Session end must stop watchers without recreating state | Legacy uses state-DB path disappearance/park | AgentSession lifecycle + watcher registrations | Watchers key off durable session lifecycle/lease, not accidental file creation |
| Resume/fork can change runtime session ID | `adopt.py`, sid chains, pane retagging, parked DB restore | aliases + AgentSession/Conversation lineage | Evidence-based association, repairable merge/split, no cwd-only merge |
| Codex has no reliable SessionEnd hook | process liveness drives teardown | AgentSession attempt watcher | Attempt exit is evidence; lifecycle end remains provider-adapter policy |
| Post-end telemetry can arrive | OTLP receiver handles late/final facts | UsageFacts with sanctioned post-end amendment | Amend ledger/projection without reactivating the runtime |
| Swallowed exceptions must be visible | audit error rows, mirror warning, dashboard error count | evidence/anomaly records + health projection | Every catch boundary records or deliberately propagates; error path recursion guard |
| Audit is queryable by timeline/anomaly/signature | audit CLI and canned anomaly sections | evidence/provenance/repair/effect records + diagnostic CLI | Port useful queries, not necessarily legacy table names |

Design consequence: “one daemon” must not reduce failure isolation. Each
Observation mapping boundary, SourceReader, AgentSession runtime, and effect
worker is supervised independently. A malformed Codex rollout line cannot stop
Claude hooks; a broken provider plugin cannot wedge unrelated Conversations.

### 36.4 Conversation and context coverage

| Legacy behavior | Why it matters | v3 representation |
|---|---|---|
| Transcript is a native record tree with many legitimate siblings | Attachments and parallel tool results are not abandoned branches | Adapter applies the measured prompt-sibling branch rule; semantic Node parent is not copied blindly from native parent |
| A cancelled/taken-back prompt may be suspected dead before a replacement sibling exists | The filesystem alone temporarily cannot prove discard | provisional/advisory provenance plus later tree reconciliation; do not delete the Node on screen evidence alone |
| User prompts can be plain text, list content, attachments, or slash-command envelopes | Treating only plain strings as prompts loses real turns | Node parts + provider decoder; semantic kind/origin retain human vs injected |
| Provider-injected stop feedback/resume nudges appear in conversation but are not human-authored | Focus/history/handover need authorship | Node `origin` and `semantic_kind` |
| Tool result records can carry user-visible ask answers and plan decisions | Dropping all tool results loses semantic user choices | interaction Operations/results, included in Activity and handover |
| Compaction remains visible while earlier history remains inspectable | Logical history and model context differ | compaction Operation, optional summary Node, ContextCheckpoint coverage |
| Title has provider records, generated fallback, explicit rename, and sticky override | One title field without provenance can revert incorrectly | Conversation display-title projection over title facts/preferences; provider rename remains effect |
| Goal/tasks/model/effort/context/fallback are branch/current-state facets | Rewind may invalidate current values without undoing cumulative work | declared branch-sensitive projections anchored to head/checkpoint |
| Prompt count and compact enablement are provider capabilities | Absent/unknown differs from zero | tri-state facet capability |
| Conversation can be viewed in child-agent scope | The active main branch is not the only readable semantic thread | AgentSession/actor-scoped Activity query; child messages remain associated with producing session/task |

The semantic Node tree deliberately does not attempt a lossless copy of the
native provider record DAG. Raw evidence retains provider detail. The canonical
tree is the provider-neutral human/agent narrative required for head selection,
rewind, handover, and browsing.

### 36.5 Activity ordering and child work

The strongest counterexample to a plain tree or timestamp timeline is encoded
in `core/childtask.py` and `dashboard/read/mirror.py`:

1. a child is assigned work in parent turn T;
2. its useful result reaches the parent;
3. the parent writes the final answer for T;
4. the child's own terminal completion card arrives later;
5. chronological merge puts the completion after the answer; but semantically
   it belongs before the answer it contributed to.

The legacy system corrects this with task endpoint stamps and a second semantic
ordering pass. v3 generalizes the fact:

- `parent_operation_id` expresses containment;
- `task_key` distinguishes several assignments to one child;
- `turn_key` correlates provider turn;
- `contributes_to` links the result/activity to the consuming final Node;
- provider `source_position` expresses native local order;
- ActivityComposer materializes one branch-aware presentation sequence.

This also covers:

- a Codex sidecar inside a Claude host;
- a Codex-native subagent whose rollout replays a parent prefix;
- a teammate re-tasked through mail;
- several parallel tools under one assistant item;
- assistant prose alternating with tool operations;
- a late imported record whose timestamp precedes already-seen activity; and
- lazy backlog where no page may split a command/copy group.

The causal graph is deliberately bounded. It is not a generic graph database,
and there is still no total order across Conversations.

### 36.6 Operation and Stream coverage

| Legacy feature | Canonical form | Important detail retained |
|---|---|---|
| Main foreground shell command | command Operation + stdout/stderr Streams | request/result correlation, start/end, exit code, interruption, tee transform provenance |
| Background job | command Operation with execution mode/background + Streams | remains open across parent turn; liveness/TaskOutput closer |
| Monitor | monitor Operation + command child/Streams | process/source discovery and lost-state reconciliation |
| Read command collapsed into a file one-liner | command/file_read Operation + Resource | classification stored once; full command/output remains accessible |
| Edit/Write/MultiEdit/NotebookEdit | file_edit Operation + ResourceVersion/diff | additions/removals/range and expand/copy links |
| Generic tools/Web/MCP/search | tool Operation | never laundered into a shell command merely because provider wire format calls it exec |
| Skill invocation | namespaced/core tool Operation kind | request/result and failure; presentation symbol is not domain state |
| Subagent launch/progress/result | agent_task Operation + child AgentSession/actor + task key | repeated tasks distinct; result causality |
| Team mail/inbox/read/broadcast | collaboration message + per-recipient delivery state | the same message ID can have several recipient copies |
| Codex standalone | main AgentSession | its messages become Nodes; exec/tools Operations |
| Codex sidecar | nested AgentSession launched by Operation | provider-neutral scope and result link |
| Codex native child | delegated AgentSession + task Operations | replayed parent prefix excluded by adapter evidence |
| Reasoning summary/progress | separate exposed Stream/Operation channel | never claim hidden chain-of-thought |
| Compaction | compaction Operation + ContextCheckpoint | display latch expiry does not fabricate completion |
| Audit warning | health/anomaly projection -> NoticeBlock | not fake provider activity |

Stream finalization always prefers authoritative provider-final content when
available. Provisional screen snapshots/deltas may be corrected. Partial
content is retained with an interrupted/lost state when useful, but it does not
advance the Conversation head unless the provider's context/history includes
it.

### 36.7 Control-plane coverage

| Legacy gesture/state | v3 design |
|---|---|
| Launch fresh session by host/account/model/effort/project/prompt | ExecutionTarget + RuntimeDriver start + launch Operation/attempt |
| Resume parked session | RuntimeDriver resume + aliases/lineage + attempt |
| Send now | message_delivery Operation; provider confirmation creates Node |
| Send while busy/queued | same Operation with provider-queued evidence; pinned UI is a DTO |
| Shared composer/new-session draft | InputBuffer with server revision and tombstone |
| Terminal draft takeover/clear | terminal-origin InputBuffer observation with asymmetric empty/unreadable rules |
| Interrupt | control Operation + provider driver + verification; outcome can be unknown |
| Prompt take-back after interrupt | provider observation linked to message delivery/Node branch; optional InputBuffer restore |
| Rewind to checkpoint | rewind Operation across semantic/workspace planes |
| Rename/autoname live | provider control effect plus title observation/projection |
| Rename parked/provider history | provider history-mutation capability or explicit unsupported result |
| Compact/model/effort | semantic controls with dynamic provider vocabulary and confirmation |
| AskUserQuestion/Codex request_user_input | interaction Operation, live option schema, CAS response, driver |
| Plan approve/feedback/dismiss/chat | interaction Operation/result; provider-specific driver |
| Permission prompt | interaction Operation + attention asking state; strict principal authorization |
| Close | lifecycle workflow + terminal/provider effects + reconciliation |
| Same-provider account migration | account_migration workflow + target selection evidence + new placement/lineage |
| Cross-provider handover | handover workflow + portable package + target acknowledgement |

Whole gestures remain the port boundary. Screen drivers can press keys, but the
application asks “answer interaction I7 with R3,” never “press down twice.”
Dynamic labels are read and validated against the live provider state; a stale
label/interaction ID refuses without acting.

The HTTP response should normally acknowledge acceptance and return the
Operation ID. A compatibility endpoint may wait for a short verified result,
but it cannot collapse “request reached daemon,” “terminal accepted keys,” and
“provider history confirms outcome” into one boolean.

### 36.8 Surface, Resource, and preference coverage

| Legacy surface behavior | Architectural home |
|---|---|
| Width-independent terminal reflow | semantic PresentationBlocks + terminal renderer/PanHost |
| Rich Markdown/JSON/YAML/source rendering | surface renderer selected from Resource/media metadata |
| ANSI/control neutralization | leaf sanitizer in every surface |
| Copy command/output/all | block action resolving Operation/Stream/Resource content |
| Click-to-expand Read/Edit/Write/diff | ResourceVersion and presenter block; terminal viewport capability optional |
| Exact scroll restoration/follow mode | terminal surface state; graceful degradation when unsupported |
| Initial compressed backlog + lazy older pages | ActivityComposer, whole-block page tokens |
| Live SSE and reconnect | structural cursor + per-Stream revision/resync |
| Verbose/default/focus views | per-Conversation surface preference + block activity classes |
| Agent breadcrumbs/scope/scoreboard | scoped Activity/Usage projections |
| Memory tree/backlinks/search cards | Resource module/plugin detail tables and projections |
| Uploads/pasted files/images | staged Resource + Node part + provider AttachmentEncoder |
| Clipboard promise/path matching | privileged local surface capability, audited and constrained |
| Dictation | surface service/token-mint capability feeding InputBuffer |
| New-session prefs/drafts | project-scoped preferences/InputBuffer |
| Hidden directories/task-card dismissal/mute/view mode | typed preferences with scope/version |
| Git branch/worktree chips | backend/workspace read projection with TTL |
| Client optimistic hints and transport failures | client telemetry evidence; never canonical success/failure |
| PWA badge/wake lock/favicon/chrome | web surface implementation only |

The domain does not preserve legacy paint-op JSON, CSS class names, glyphs, or
ANSI. It preserves enough semantic detail to reproduce or improve the behavior
on both terminal and web without coupling one representation to the other.

### 36.9 Attention, presence, and alert coverage

The alert system is a workflow over attention, not a side effect of rendering.
Parity fixtures must cover:

1. permission/question states outrank running/busy states;
2. child inner events do not drive main-host attention;
3. ending a foreground command returns to working, not automatically done;
4. live background/agent work prevents a false done state;
5. cancelled turns are repaired by evidence/probes where no hook exists;
6. an asking alert does not wait for the done settle window;
7. a transient done state never sends;
8. tab focus/web viewing/device activity/composing remain differently scoped;
9. looking at done can retract it, while looking at asking cannot;
10. a provider/tab state change or session end cancels/retracts stale notices;
11. mute/global toggle and presence routing are evaluated consistently;
12. push/Telegram escalation produces separate delivery attempts/handles;
13. a failed/vanished retraction remains bounded and diagnosable; and
14. daemon restart recovers armed/delivered workflow state where the channel
    supplied a durable handle.

The web attention strip and terminal tab colors both consume the same Attention
projection, but each effect records its own verified outcome. A failed tab
paint is not persisted as observed presentation state and must be retried.

### 36.10 Accounts, usage, and automatic migration coverage

The legacy account plane contains more than a list of credentials:

- provider/backend-scoped configured accounts and launch aliases;
- current session account captured from environment/status line;
- several quota windows with independent reset times;
- account-wide versus model-scoped limits;
- logged-out state distinct from limited state;
- target ranking using effective use, reset horizon, model availability, and a
  ceiling;
- a model downgrade ladder when the current model is exhausted;
- cooldown against migration loops;
- manual and automatic migration with different continuation prompts;
- close/wait/resume ordering;
- per-session and aggregate token/cost figures;
- OTEL auxiliary usage absent from transcripts; and
- provider-specific price models.

v3 therefore separates:

- AccountProfile configuration and credential references;
- QuotaWindow observations;
- UsageFacts with source and ledger;
- Usage/cost projections;
- target-selection policy with recorded reasoning; and
- account_migration workflow.

The target selector is pure and fixtured. It receives current observations and
policy; it does not read provider files or keychains internally. Credential and
usage acquisition remain adapter capabilities.

### 36.11 Terminal and pane coverage

The Terminal ports listed in §20 are sufficient only if their contracts retain
the distinctions the legacy `Frontend` interface learned:

- availability versus currently usable control channel;
- terminal/application focus versus selected tab/window focus;
- raw text, bracketed paste, and key events are different operations;
- launch tab must avoid stealing OS application focus;
- pane launch must support a specific tab/anchor, not merely current focus;
- set title is a capability and may have different ownership per provider;
- get visible text with/without ANSI differs from reading full scrollback;
- exact viewport scroll and end positioning are optional capabilities;
- pane geometry is observable and resizing is asynchronous;
- terminal bindings are verified fresh before destructive input/close; and
- null terminal behavior is a normal explicit decline.

No-terminal mode cannot be implemented by returning “no terminal” from every
control. The AgentSession's RuntimeDriver must advertise programmatic send,
interrupt, and other capabilities independently. A surface chooses the
provider action; only the adapter decides whether it uses terminal, RPC,
app-server, SDK, process signal, or provider file mutation.

### 36.12 What is intentionally not preserved as architecture

These legacy implementation choices may be replaced while preserving behavior:

- the paint-op table as canonical history;
- temporary file names and the historical `claude-*` executable vocabulary;
- per-hook Python process fan-out;
- DB-file disappearance as the universal liveness signal;
- palette-slot allocation as domain state;
- provider-specific KV keys/handoff JSON shapes;
- client-side merging of message and operation timelines;
- exact terminal glyph/color/CSS choices;
- direct free-form SQL write as a normal repair workflow;
- in-memory-only notification arms/handles;
- hard-coded account registry location/tunnel topology;
- source/provider name switches in the web application; and
- screen scraping where a semantic provider interface exists.

They remain migration inputs and compatibility concerns, not constraints on the
new core.

### 36.13 Explicit residual risks

The corrected design makes the features representable; it does not make every
provider capable.

1. **TUI assistant streaming remains approximate** when the provider persists
   only final messages. Screen snapshot polling can improve immediacy, but must
   reconcile to provider-final content.
2. **Input transformation is provider-fragile and security-sensitive.** A
   provider version may remove or change `updatedInput` semantics. Capability
   probing and pass-through are mandatory.
3. **Provider-native context is partly unknowable.** ContextCheckpoint records
   observations/inferences, not hidden state.
4. **Cross-provider handover cannot transfer hidden reasoning, prompt cache,
   approvals, processes, or exact compaction state.**
5. **Workspace rewind/handover can be partially applied.** The workflow must
   expose and recover from split outcomes.
6. **Activity causal links depend on provider evidence.** When a provider has no
   task/turn identity, v3 must not invent a relation; chronology/source order is
   the honest fallback.
7. **Single-file SQLite may miss latency goals.** The mandatory benchmark
   decides whether Conversation-local partitioning is needed.
8. **In-process plugins are trusted.** Untrusted extension execution still
   requires a process boundary.
9. **Remote backend disconnects create uncertainty.** Spooling can preserve
   observations, not guarantee real-time control or exact remote liveness.
10. **Resource retention can expire useful detail.** The UI/handover package
    must show omissions instead of presenting an incomplete blob as complete.

### 36.14 Parity gate derived from the audit

A legacy plane is replaceable only when its checklist has:

- a named v3 owner;
- imported or newly captured evidence;
- mapper/domain fixtures;
- query/presenter fixtures;
- effect/adapter contract tests where applicable;
- crash/restart and uncertainty behavior;
- performance threshold;
- security review;
- an explicit behavior difference accepted by the owner; and
- an operational rollback path.

The minimum feature groups are:

1. session identity/start/end/resume/adoption;
2. Conversation tree, discard/rewind, title, and compaction;
3. foreground/background/monitor commands and file/tool classification;
4. subagents, teammates, Codex sidecars/native children, task order, and mail;
5. usage/context/tasks/goal/model/effort/account/quota projections;
6. terminal tab/pane/renderer/copy/view behavior;
7. web backlog/live/history/scopes/preferences/drafts/attachments;
8. send/queue/interrupt/rewind/rename/compact/model/effort/dialog/close controls;
9. attention/presence/toast/push/Telegram/retraction;
10. audit/anomaly/error/client telemetry;
11. account migration; and
12. headless/provider-programmatic paths.

This checklist is intentionally stricter than “the page looks similar.” The
legacy system's difficult features are mostly recovery and distinction:
queued versus delivered, empty versus unsupported, unseen versus unresolved,
chronological versus causal, provider history versus canonical history,
semantic branch versus workspace state, and attempted versus confirmed.
Those distinctions are now first-class in the v3 architecture.

---

## 37. Post-audit conclusion

The legacy audit does not justify returning to the traditional coupled design,
nor does it justify event sourcing. It strengthens the transactional modular
monolith while keeping the central model small.

The final recommendation is:

- keep Conversation, Node, AgentSession, Operation, and Stream as the core;
- represent dialogue ancestry only in the Node tree;
- represent execution containment, causal contribution, and provider/session
  lineage separately;
- give Nodes ordered parts and owners multiple named Streams;
- make provider-local position plus causal links the basis of a server-owned
  Activity timeline;
- add narrow supporting tiers for Resources, ContextCheckpoints, UsageFacts,
  InputBuffers/preferences, interactions, notifications, evidence, and effects;
- keep every provider/terminal integration behind capability objects;
- add the synchronous answerable lane required by provider hook protocols;
- distinguish requested, attempted, accepted, observed, and committed states;
  and
- use the code-derived parity gate in §36 before retiring any legacy plane.

That structure can reproduce the current terminal cockpit and dashboard while
also supporting headless runtimes, configurable backends/accounts, plugins,
cross-provider handover, and future inter-session collaboration. The additional
supporting records are not speculative domain inflation: each one is justified
by behavior already present in the legacy implementation.
