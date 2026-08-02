# baqylau v2 — Design

Status: **PROPOSED** — design complete, not implemented. This is the
implementor-facing specification of the v2 rewrite. The current (v1) system —
everything else in this repository — stays authoritative until the migration
gates (§19) are met. v1 is referenced throughout as the source of measured
lessons and ported test fixtures; where this document says "v1 measured", the
cited docs/ file holds the evidence and the fixture to port.

Vocabulary note: this document uses the `eventsourcing` library's terminology
throughout — **application, aggregate, domain event, stored event,
notification, notification log, follower, `ProcessApplication`, `policy()`,
repository, snapshot, tracking record**. Code uses the same words. §3.1 is a
self-contained primer.

---

## 1. Purpose, goals, constraints

**baqylau v2** replaces v1's architecture — ~20 short-lived hook processes
coordinating through SQLite side-tables, take-once hand-offs, sentinel files
and pid-liveness slots — with one supervised daemon over an event-sourced
core. The three problems it exists to solve:

1. **v1 fuses presentation with semantics.** Paint ops carry glyphs, RGB and
   per-consumer routing flags; every time a second consumer needed to
   *understand* rather than *display*, a baked string had to be promoted to a
   field. v2 stores facts; every surface owns its own presentation.
2. **v1 has no long-lived brain.** The coordination machinery (slots, adopt,
   `parked()` probes, inode revalidation, stale-row stealing) is most of the
   complexity and most of the bug history. v2's daemon simply *knows*.
3. **v1's derived numbers are producer-maintained promises.** Twenty callers
   must each call `bump()` correctly; wrong numbers are permanent. v2 derives
   everything from one log; derived state is disposable and rebuildable.

**Goals:** pluggable agent tools (Claude Code, codex, opencode — the last
does not follow the hooks pattern; by design of this architecture that must
not matter), pluggable terminals, pluggable surfaces; every derived fact
rebuildable; every input replayable; audit = the system itself.

**Constraints no architecture removes** (design around, never assume away):
hooks must never block or fail; Claude Code fires **no hook on
cancel/interrupt**; tool payloads are undocumented and version-fragile; TUI
screen-scraping is irreducible for the control plane. v2 *contains* these
per-tool; it does not shrink them.

---

## 2. System overview

```
                 INBOUND                          DAEMON (one supervised process)                OUTBOUND
 ┌──────────────────────────────┐   ┌─────────────────────────────────────────────────┐   ┌──────────────────┐
 │ baqylau-shim (Rust)          │──▶│ Intake application ─▶ SessionLog application     │──▶│ Terminal port    │
 │   claude_code + codex hooks, │   │ (envelope events)     (mapper policies inside;   │   │  kitty · null    │
 │   statusline mode            │   │        │               domain events, one        │   ├──────────────────┤
 │ in-shell reporter /          │──▶│        ▼               notification log)         │   │ AlertChannel port│
 │   baqylau-exec (Rust)        │   │   BLOB STORE                  │                  │   │ webpush·telegram │
 │ opencode plugin (TS)         │──▶│                               ▼                  │   │ ·toast           │
 │ FileWatcher observations     │──▶│        FOLLOWERS (ProcessApplications):          │   ├──────────────────┤
 │ OTLP receive adapter         │──▶│        folds · policies · reactors               │──▶│ AgentControl port│
 │ gestures (web / MCP)         │──▶│                                                  │   │  per tool        │
 │ presence beats · clientlog   │   │   QueryService · ControlService · MCP server     │   └──────────────────┘
 └──────────────────────────────┘   └─────────────────────────────────────────────────┘
                                        ▲ FastAPI: REST + SSE + gestures ▲        pane-host processes
                                        └── web SPA · CLI · phone ───────┘        (terminals/kitty/, §15)
```

Boundary rules — each exists to kill a v1 bug class:

- **Everything crossing into the core is an envelope**, pushed (shims,
  reporter, plugin, gestures, presence, clientlog, OTLP) or pulled (file
  watchers). Persisted verbatim *before* interpretation, so mapping is
  replayable and "what did the tool actually send" is always answerable.
- **All domain events are appended by one application, `SessionLog`.**
  Mapping runs *inside* it (§6), so "new events + consumed-input tracking
  record" is one transaction — exactly-once by the library's own mechanism,
  and no two writers ever race an aggregate's version sequence.
- **Followers never communicate except through notification logs.** No shared
  mutable state, no direct calls. One medium ⇒ one ordering, one recovery
  story, one audit trail. The two sanctioned departures are specified in §5.4
  (synchronous edges).
- **Surfaces own presentation entirely** and sanitize at the leaf (§16).

---

## 3. Storage model

### 3.1 The `eventsourcing` primer (what implementors must know)

The library's storage unit is an **application**: a family of tables it
creates and manages. Per application:

- **`stored_events`** — every domain event of every aggregate in the
  application: `(originator_id, originator_version, topic, state,
  notification_id)`. Two orderings coexist: per-aggregate
  (`originator_version` — gap-free, and the optimistic-concurrency guard) and
  global (`notification_id` — commit order across the application; reading in
  this order is the **notification log**, and a notification ID is what this
  document means by *position*).
- **`snapshots`** — periodic photos of a rebuilt aggregate's state
  (`snapshotting_intervals` per class), so `repository.get(id)` is
  newest-snapshot + short tail replay instead of full replay. Disposable
  accelerators; truth never lives here.
- **`tracking`** — for a **follower** (`ProcessApplication`): one bookmark
  row per upstream, `(application_name, notification_id)` = the last
  notification fully processed. The library commits a follower's new
  writes **and** its tracking record in ONE transaction — that is the
  exactly-once guarantee, and it is why each follower's tables are its own.

Restart is cheap (resume from tracking records; process only the gap).
**Rebuild** is a deliberate maintenance action for logic fixes: drop a
follower's tables, zero its tracking, replay — history recomputed under the
corrected code. Snapshots cannot serve a rebuild (they are the old code's
output); that is not a limitation but the point.

### 3.2 Our applications

| Application | Holds | Written by |
|---|---|---|
| `Intake` | envelope events; its notification log is what mapping consumes | the socket server, watchers, adapters |
| `SessionLog` | **the truth**: all domain events, one `Session(lineage)` aggregate per session | its own mapper policies (§6) — nothing else, ever |
| one per follower | that follower's derived state (§9: an event-sourced aggregate OR a materialized table, declared per follower) + its tracking records | itself |

Physically: SQLite, WAL, one file (or one per application — config).

### 3.3 The storage-shape rule (event-source vs materialize)

> **Event-source where the *changes* are the product; materialize where only
> the *current value* is.**

The truth (`SessionLog`) is event-sourced, non-negotiably. Among derived
state, only two things have consumers of their *ordered changes*: agent
attention (its transitions are followed by the tab reactor, the alert policy
and the session index) and the alert lifecycle (its history IS the notifier's
audit). Those are event-sourced aggregates. Every other derived fact —
counters, ctx, titles, the session index, stats rollups — is a **materialized
table** updated inside the follower's tracking transaction (a
library-sanctioned pattern): plain rows, plain UPDATEs, indexed however reads
want. Exactly-once and rebuildability are properties of the tracking
transaction, not of aggregates, so nothing is lost — an `Updated(count=47)`
event stream nobody will ever read as history is ceremony, and v2 does not
pay for it. Each follower's storage shape is a declared column in §9's
registry.

### 3.4 Intake retention, blob store, caps

- **Intake** is the evidence tier: long-but-bounded retention, and the bound
  is load-bearing — **the remap window IS the intake retention** (§3.5).
  Named config. Consequence, stated: mapper working state must be
  reconstructible from intake only *within* the window; correlations older
  than it survive only as their already-mapped domain events.
- **Blob store**: content-addressed (sha256) bulk bytes — command output,
  file contents, diffs, tool responses, plan texts, message bodies. Domain
  events carry `BlobRef`s; blobs are immutable (HTTP-cacheable forever) and
  served only per §16's rules. **Class-based retention**: command-output
  blobs expire on a horizon (v1 explicitly held "full historical fg output is
  out of scope" — docs/sessionapi.md's fidelity ladder; v2 keeps it *longer*
  than v1's transient tee files but not forever); file-content / diff / plan /
  message blobs are kept. An expired blob's events remain (the `BlobRef`
  resolves to a named "expired" answer, never an error).
- **Ingestion caps** (v1's three-level lesson, docs/streaming.md): per-pump,
  per-line (non-JSONL only), per-block, so a 10MB build log cannot become
  thousands of notifications fanned to every follower forever. Cap events
  record truncation honestly (`CommandOutput(truncated=true, total_bytes)`).

### 3.5 Rebuild vs remap

- **Rebuild** (a follower): drop tables + tracking, replay. Routine,
  retroactive; scoping to one lineage is supported and preferred for
  targeted fixes.
- **Remap** (a mapper bug): re-run corrected mapping over stored intake.
  This rewrites truth that followers already consumed — a migration with a
  decision, not a casual replay; possible at all only inside the intake
  retention window.
- **Policy emissions are never rebuilt** (§9.2): they are decisions made
  with a past clock and past state — history, not derivations.

---

## 4. Identity: lineage

Claude Code forks the runtime session id: `--resume` continues the
conversation under a NEW sid with no SessionStart of its own; backgrounding
does the same (the new sid is the background-job id). v1 chased this with
physical adoption (DB renames, symlinks, pane retags — docs/mirror-pane.md).
v2 removes the problem from consumers entirely:

- The aggregate key of `Session` — and of every derived fact — is a
  **lineage id**, minted once at first sight of a conversation. Runtime sids
  are aliases, recorded by the `SidAliased(lineage, new_sid)` domain event.
- Alias resolution happens **inside the `SessionLog` application, before any
  append**: fork detection is part of mapping (§6.2), mapping runs inside the
  application, and the application will not append an unknown sid's events
  until the fork rules have run against that first envelope — the evidence
  (resume source, background-job id, cwd, transcript path) is in the envelope
  itself. No event is ever appended under a wrong lineage.
- Domain events still carry the runtime `sid` as evidence. Queries accept
  either and resolve through the alias table.
- Terminal window binding (the kitty user-var tag) is lineage-keyed state,
  refreshed on `SidAliased` (§15).

---

## 5. Edge components

Hot-path, logic-free by decree: all evolution happens daemon-side in mapper
policies. The moment per-tool logic wants to live in an edge component, the
answer is "no — mapper".

### 5.1 `protocol/` — the language-neutral contract

Length-prefixed frames over a unix socket; three implementations (Rust edge,
Python daemon, TS plugin) bind to one written spec, conformance-tested from
all three sides. Envelope kinds:

```
hook:<EventName>              baqylau-shim (claude_code, codex)
report:started|chunk|exited   the command reporter (in-shell tee / baqylau-exec)
plugin:<event>                the opencode TS plugin
obs:transcript|rollout|inbox  daemon-side FileWatcher consumers
otel:metrics                  the daemon's OTLP receive adapter
statusline:update             baqylau-shim in statusline mode
gesture:<g>.requested|.result ControlService / MCP server
presence:beat|away            web pages + the terminal-focus prober
client:<record>               the SPA's frontend audit channel (§12.6)
```

Socket writes are **non-blocking with a hard deadline as a property of the
frame codec** — a wedged daemon must never block a hook path. The socket's
trust boundary is filesystem permissions (0600, owner-only runtime dir); two
semantic guards ride above it: the reserved `terminal` presence device may
only be stamped by the daemon's own focus prober (a browser claiming it would
route every alert to Telegram — v1's measured refusal), and `gesture:*`
envelopes are minted only by ControlService/MCP, never accepted raw.

### 5.2 `baqylau-shim` (Rust)

One binary wired into Claude Code's and codex's hook configs
(`baqylau-shim <tool> <event>`): read stdin, frame it with harvested env
metadata, send, **print the daemon's reply to stdout if the kind is
answerable**, exit 0 — always. A shim never exits non-zero toward the tool
(a hook's exit code is a control signal that can block the tool call; the
first constraint in §1 wins). "Failure" means: best-effort stderr line, no
envelope, and the daemon-side absence is surfaced by the CLI health command.
The narrowed guarantee is stated plainly: **a daemon outage is an
unrecoverable observation gap for pushed envelopes** — accepted; watcher
sources re-cover most of it after recovery.

**Install contract — hooks the shim must NEVER be wired to:**
`WorktreeCreate`/`WorktreeRemove` are DELEGATING hooks — registering any
handler tells Claude Code "I will create the worktree" and must print the
worktree path; a silent exit-0 reads as "succeeded, no path" and breaks every
worktree-isolated agent spawn on the machine (v1 hit this live —
docs/wiring.md). Generally: any hook whose stdout is load-bearing to the tool
is excluded and listed in `protocol/envelope-frames.md`. The accepted cost:
those events are unobservable.

**Statusline mode**: `baqylau-shim statusline` wraps the user's real status
line — exec the configured downstream command with the same stdin, relay its
stdout verbatim, ship the stdin JSON as `statusline:update`. It must never
break the status line, including when the wrapped command dies (fall back to
pass-through). This is the ONLY channel Claude Code exposes 5h/7d rate-limit
windows on (v1 `plugins/claude_code/statusline.py`).

### 5.3 Command capture: the in-shell reporter (and when the exec wrapper applies)

**The problem capture must not create:** Claude Code's Bash tool keeps ONE
persistent shell for the whole session — `cd`, exports, functions survive
between tool calls, and agents depend on that constantly. Any capture
mechanism that runs the command in a child shell breaks it. v1's tee wrapped
commands in a **brace group in the tool's own shell** for exactly this reason
(docs/streaming.md).

**The Claude Code injection shape** (via the PreToolUse `updatedInput`
reply): keep the brace group, upgrade the transport —

```
{ <original command>
} > >(baqylau-tee --tid T --out) 2> >(baqylau-tee --tid T --err); baqylau-report --tid T --exit $?
```

- State is preserved (same shell). Output streams to the daemon socket live
  (`report:chunk`, flushed ~8KB/~50ms). The trailer reports the real exit
  code (`report:exited`). `baqylau-tee` passes bytes through to the tool
  unchanged — the tool's own capture is not perturbed.
- Reporting is best-effort, never load-bearing: socket unreachable → the tee
  degrades to pure pass-through; the command cannot fail because
  observability failed. The PostToolUse hook remains the second witness and
  the correlation closer of last resort.
- The rewrite is visible in the tool's transcript; mapping strips it so every
  domain event carries the *original* command string.

**`baqylau-exec`** (child-process wrapper: own process group, pid/rusage
reporting, signal forwarding) is the injection shape ONLY for tools whose
command shell is **proven non-persistent** — a per-tool measured checklist
item, alongside "commands run without a TTY" (Claude Code: persistent shell,
piped, measured; codex/opencode: measure at integration). Where used, its
kill semantics are stated: the child is orphaned if the wrapper dies by
SIGKILL (a parent-death watch covers ordinary deaths; the hook envelope
closes the correlation regardless).

**Permission note:** injecting via `updatedInput` requires
`permissionDecision:"allow"` — on every tool that supports it, injection is
therefore also auto-approval of the rewritten call (v1 made the same trade
knowingly: `plugins/claude_code/cmd_pre.py`). The gate is per-tool config,
default ON; it matters only for installs that use permission prompts at all.

Injection matrix:

| Tool | Injection | Shape |
|---|---|---|
| Claude Code | PreToolUse `updatedInput` (+ auto-allow, above) | in-shell reporter |
| codex | PreToolUse `updatedInput` + `permissionDecision:"allow"`, behind `features.hooks=true` — probed per session (the reporter's first frame is the proof), degrade = rollout-only reconstruction. Source: developers.openai.com/codex/hooks (verified 2026-08-01) | measure shell persistence, then reporter or exec |
| opencode | plugin `tool.execute.before` mutable `output.args.command` | same rule |

### 5.4 Synchronous edges (the two sanctioned departures from "everything is a log read")

The architecture's recovery story is that delivery is always a
notification-log read. Exactly two places cannot be that, and they are named:

1. **Answerable envelopes.** A PreToolUse reply must reach the shim within
   ~200ms on an open socket — it cannot wait for mapping to reach the
   envelope in log order. Rule: **an answerable kind must be answerable by a
   pure function of the envelope's own bytes** (the rewrite decision is: is
   it Bash, is it already wrapped, emit the wrapped string). The responder
   runs on the socket thread; the reply is **recorded into the intake row**
   beside the envelope, so when mapping later processes it in order it does
   not re-decide — it reads the recorded reply as evidence. Nothing that
   jumped the queue can disagree with anything, because nothing stateful ran.
   A future answerable kind that needs state must use an immutable snapshot
   of mapper state with the same recorded-reply rule — and must argue its way
   into this paragraph first.
2. **Effects.** Reactors act on the world (paint, deliver, type) outside any
   transaction; their contract is at-least-once + idempotent, their outcomes
   return to the log as effect events (§9.3), and non-idempotent effects
   take a durable pre-effect lease (§9.3).

---

## 6. The `SessionLog` application

The single writer of truth. Mapping runs **inside** it: per-tool mapper
policies are invoked as it consumes the Intake notification log, so lineage
resolution → mapping → append → tracking is one transaction. Mappers hold
working state (open correlations, probe results); that state is derived —
reconstructible from intake within the retention window — never load-bearing
on its own.

### 6.1 Mapper policies

| Policy | Consumes | Notes |
|---|---|---|
| `claude_code` | `hook:*`, `report:*`, `obs:transcript` | the richest: §6.2 inference, §6.3 classification, the answerable responder (§5.4) |
| `codex` | `hook:*`, `report:*`, `obs:rollout` | dual role — standalone host AND sidecar: a codex run inside a Claude session is discovered from codex's global session dirs (standing machine-wide watches), correlated to its host lineage by cwd + launch evidence; its events carry `actor=codex:<aid>` in the host's stream, while a codex-native subagent is `actor=sub:<aid>` (v1's unified scope key, docs/codex.md). Guard ported: a codex session started OUTSIDE the terminal (the ChatGPT desktop app shares `~/.codex/hooks.json`) mints no lineage and no card — the phantom-session lesson |
| `opencode` | `plugin:*`, `report:*` | |
| `otel` | `otel:metrics` | delta temporality: datapoints are SUMMED, never read as gauges (the silent order-of-magnitude error); emits `UsageReported(query_source ∈ main\|subagent\|auxiliary)`. The OTLP receive adapter exists because Claude Code's hidden auxiliary agents (summarizer/title runs) fire only SubagentStop, carry no payload usage and write no transcript — 11.6% of one v1 session's cost (docs/otel.md). Transcript-derived usage survives only as a SessionEnd fallback, gated by an OTEL-seen watermark so late exports and the fallback never double-count |
| `statusline` | `statusline:update` | `RateLimitReported`, `AccountSeen` |
| `gestures` | `gesture:*` | interrupt/rename/migrate/… requested + results |
| `presence` | `presence:*` | `DeviceSeen`, `ViewingChanged` |
| `clientlog` | `client:*` | `ClientRecord` (browser-side transport evidence, §12.6) |

Subagent ordering rule: a subagent's story is emitted in **transcript
order** — the transcript is the only in-order source of its
prompt/messages/tools/result; that agent's hook/report envelopes corroborate
(exit codes, live chunks) but never reorder (v1 measured hooks racing and
mis-ordering against messages — docs/subagents.md). A small per-agent
resequencing buffer inside the mapper implements this.

### 6.2 Inference: events asserted without the tool saying so

Claude Code is silent at specific moments; each silence is filled by a
**named, versioned, individually-tested rule** (module `inference.py`; every
rule ships with fixtures ported from v1's measured sessions, not rewritten
from memory):

- **Interrupt**: no hook on Esc. Match the `[Request interrupted by user]`
  marker as the content of a `type:"user"` transcript RECORD — never raw
  bytes (growth that merely QUOTES the marker must not trigger; v1 was burned
  by an attachment quoting its own docs) — and check what FOLLOWS: a queued
  message is delivered the instant the Esc lands; its prompt record right
  after the marker means the turn continued.
- **Sid fork** (§4): resume source / background-job id at first sight →
  `SidAliased`.
- **Closers** — attention (§9.1.1) depends on every `*Started` eventually
  closing, and the tools regularly break that promise. Per-gap named rules,
  no general watchdog:
  - denied/never-ran Bash (no PostToolUse fires) → `CommandAborted
    (why="never-ran")`; never infer "your turn" from it (PostToolBatch is
    not reliably fired — v1 measured);
  - killed subagent → `meta.json stoppedByUser`; rejected/abandoned Task →
    the parent transcript's `tool_result` (fires neither SubagentStop nor
    stoppedByUser); died-on-API-error → StopFailure carrying the agent id;
  - reported commands → `report:exited`; the hook outcome substitutes when
    the reporter was absent;
  - **monitors** → process-containment liveness (a monitor writes in bursts
    with no held handle, so only its command process proves life; ws
    monitors have no command at all — v1's ps-check with its normalisation
    fixes, ported with fixtures — docs/streaming.md);
  - **session host death** (kill -9, terminal crash — no SessionEnd fires)
    → host pid check → `SessionEnded(reason="host-died")`; without it a
    dead session suppresses alerts, inflates active time and blocks
    relaunch via the live-lineage guard;
  - standalone codex end → rollout EOF + pid death (codex fires no
    session-end event).
  - Residual, accepted: a gap none of these anticipates sticks open until
    its rule is added; the failure is visible (a stuck "working" card) and
    the fix is always one more closer.
- **Plan/ask declines**: ExitPlanMode and AskUserQuestion declines fire NO
  hook and wear the generic tool-rejection text every tool shares — matched
  by the dialog's own tool_use id with a bounded backward seed, failing
  quiet (v1 `PLAN_LOOKBEHIND`).
- **Branch discard** (rewind / reverted compaction): nothing is emitted at
  the moment of discard — the next transcript record attaching to an
  older-than-leaf parent is the tell → `BranchDiscarded(leaf_uuid,
  new_parent_uuid)` — keyed by transcript-record identity, never by
  notification IDs (other actors' events interleave and are not part of the
  branch). Fail-open rules ported from v1's `_boundary_live` (bounded scan;
  the by-construction-undetectable window before anything is appended).
- All closers are **event/evidence-triggered, never idle timeouts** (v1's
  idle backstop false-positived on every long think — quiet ≠ dead). The one
  timeout-ish check: a *web-initiated* interrupt's recheck (an event we
  generated ourselves), bailing on ANY tab-state movement or transcript
  growth over the press-time baseline.
- Duplicate hooks are real (`SubagentStart`/`SubagentStop` can each fire
  more than once); every rule dedups on its correlation key, with fixtures.
- Failures arrive on `PostToolUseFailure`, not `PostToolUse` — every
  consumer of the latter subscribes both.

### 6.3 Classification: commands that are really file reads

Some shell commands are semantically file reads (`sed -n '1,120p' f`,
`cat f`, `find -exec cat`). The classification is shared (v1 lesson: the
memory feature recorded nothing for a year while sessions read notes via
`cat`), decided once at mapping, and expressed as an ADDED event linked to
its evidence — never a mutation of it:

```
CommandStarted(tid, cmd="sed -n '1,120p' core/ops.py", interp="read")   # hint, pre-exit
CommandFinished(tid, exit=0, interpreted=true)                          # the guard
FileRead(path="core/ops.py", extent="1-120", via="sed", from_cmd=tid)   # only on exit 0
```

A failed read-shaped command is just a failed command (no `FileRead`;
`interpreted` stays false; it counts as a failure). The general classifier is
a pure function of the command string; **the memory-vault classifier is the
one exception allowed filesystem lookups** (bounded, cached vault-membership
proof + name-index fallback — a grammar can neither prove membership nor
resolve a bare basename; v1 `memcmd`), with the recorded consequence that its
verdicts are map-time facts, not byte-stable under remap. `qmd` search
output is parsed at map time from the reporter stream when present, else
from the PostToolUse payload (v1's source) → `MemorySearched`.

Principle: **mappers own interpretation, folds own arithmetic, presenters own
appearance.**

---

## 7. Domain-event vocabulary

All events carry `lineage`, `sid` (runtime, evidence), `actor`
(`main` | `sub:<aid>` | `team:<aid>` | `codex:<aid>` | `aux`), `ts`.
Transcript-derived events also carry their transcript-record identity
(`rec_uuid`, `parent_uuid`) — the branch model rides on it. Evolution rule:
**additive only**; consumers ignore unknown types (they must anyway — old
notifications never disappear).

| Group | Events |
|---|---|
| Lifecycle | `SessionStarted(cwd, tool, account)` · `SessionEnded(reason)` · `SidAliased(lineage, new_sid)` |
| Branching | `BranchDiscarded(leaf_uuid, new_parent_uuid)` — consumer classes in §9.4 |
| Conversation | `ConversationMessage(role ∈ user\|assistant, blob, rec_uuid, attachments)` · `ToolResultRecorded(tid, blob)` — the transcript's story as events; what the web's conversation view renders |
| Turns | `TurnStarted` · `TurnEnded` · `TurnInterrupted` · `PromptSubmitted(text)` |
| Commands | `CommandStarted(tid, cmd, kind ∈ fg\|bg, wrapped, interp?)` · `CommandOutput(tid, blob, truncated?)` · `CommandFinished(tid, exit, dur, interpreted?)` · `CommandAborted(tid, why)` |
| Monitors | `MonitorStarted(mid, desc, cmd? \| ws_url?, persistent)` · `MonitorEvent(mid, blob)` (the task-notification stream, with v1's not-every-notification-is-a-monitor disambiguation) · `MonitorEnded(mid, why)` — monitors are their own tool with their own lifecycle, not a command kind |
| Files | `FileRead(path, extent, via?, from_cmd?)` · `FileEdited(path, add, rem, blob_diff)` · `FileWritten(path, blob)` |
| Tools | `ToolInvoked(name, args_blob, result_blob)` — the complement of every claimed tool: an unclaimed tool must render as SOMETHING (v1's allowlist-silence bug) · `SkillInvoked(name, args_blob)` |
| Permissions | `PermissionRequested(tid, tool)` · `PermissionResolved(tid, verdict)` — an input to the red attention state |
| Agents | `AgentSpawned(aid, kind, task)` · `AgentTasked(aid, task_key, task)` / `AgentTaskDone(aid, task_key, status, result_blob)` — the child-task model: a child is not always one task (codex follow-ups, teammate re-tasking merged two results into one card in v1), and a task's completion can land AFTER the parent's final answer, so presenters order the answer after the task-end card SEMANTICALLY, not by notification order (v1 `childtask.py`, measured) · `AgentFinished(aid, status, result_blob)` |
| Dialogs | `QuestionAsked(qid, options, multi)` · `QuestionAnswered(qid, answer)` · `PlanProposed(blob)` · `PlanDecided(verdict, feedback, edited)` |
| Control | `InterruptRequested(by)` · `InterruptConfirmed` |
| Usage | `UsageReported(model, in, out, read, create, query_source)` — token counts only, NO cost: cost is arithmetic over a price table (v1's most-corrected artifact), so the usage follower prices and a price fix is a rebuild, not a remap |
| Limits | `RateLimitReported(account, five_hour, seven_day, resets_at, model_windows)` · `RateLimitHit(account, scope, resets_at)` — an EVENT deliberately: the status line freezes ~95% once requests bounce (v1 docs/relimit.md) · `AccountSeen(slug, label)` · `LoggedOut(account)` / `LoggedIn(account)` |
| Meta | `TaskListChanged(tasks)` · `TeamTaskChanged(op, n, text)` · `TitleChanged(title, source ∈ the v1 five-step ladder + renamed-override)` · `GoalSet(text)` / `GoalMet` · `CompactionStarted/Ended` · `ContextReported(used, limit, model)` · `ModelChanged(model, effort, fallback?)` |
| Memory | `MemoryRecalled(paths, via)` · `MemoryWritten(path, verb)` · `MemorySearched(kind, query, hits)` |
| Team mail | `TeamMailSent(from_actor, to, blob)` · `TeamMailDelivered/Read(msg_id, recipient)` — INTRA-session agent-team mail, keyed `(msg_id, recipient)` per copy; delivered/read transitions come from the inbox watcher (`obs:inbox` — no hook fires on read; lifecycle frames travel the same inboxes with no SendMessage anywhere); poll-diff semantics ported: read = flipped OR disappeared, stale after 60s |
| Presence | `DeviceSeen(device)` · `ViewingChanged(device, lineage?, viewing)` |
| Cooperation | `PeerMessageSent(from_lineage, to_lineage, blob)` · `PeerMessageDelivered/Read(msg_id)` · `WorkClaimed(path, ttl)` / `WorkReleased(path)` — the CROSS-session MCP hub (§14); deliberately distinct from team mail |
| Effects (appended by reactors about their own effects, §9.3) | `TabPainted/TabPaintFailed` · `AlertDelivered(alert_key, channel, message_id)/DeliveryFailed` · `GestureCompleted(gesture_id, verdict)` · `SessionLaunched/LaunchFailed(gesture_id)` |
| Operational | `ClientRecord(kind, payload)` · `AnomalyDetected(rule, detail)` |

---

## 8. Ports (the abstraction budget: each must earn rent)

| Port | Contract | Adapters |
|---|---|---|
| `Terminal` | window discovery/tagging, panes, tab paint (verified: returns rc), send-text/keys, `get_text(ansi=)`, focus probes, `launch_tab`/`close_tab`, `capabilities()` | kitty (package, §15), null (inert: headless `claude -p`, scrubbed-env daemon-origin sessions — an anchorless session SKIPS the pane lifecycle entirely; v1's phantom-mirror lesson) |
| `AgentControl` | per-tool gestures: interrupt / send / rename / autoname / ask / plan / rewind / compact / model / effort / migrate — capability-declared; a missing gesture is a named 409. Per-gesture v1 semantics ported with fixtures: interrupt's queue-drain stop rule (stop pressing Esc the moment the transcript shows the queue draining — a screen-delta-only verdict killed the queued delivery) + the take-back read; rename's live/parked split (live = paste the tool's own `/rename`, write nothing — Claude Code re-emits its in-memory name every turn boundary and overwrites foreign records, measured 13×; parked = append the naming record + durable override; codex inverts — its TUI emits no OSC title); the clipboard-image guard (`paste_grabs_clipboard_image` declaration — Claude Code auto-attaches a clipboard image on ANY bracketed paste/argv launch; ControlService clears an image board before send/launch/ask, text board untouched); the ghost-suggestion probe | claude_code, codex, opencode |
| `AlertChannel` | deliver / retract (retraction requires the channel receipt — §13) | webpush, telegram, toast |
| `Clock` | `now()`, `call_at()` | real (asyncio), fake (tests) |
| `FileWatcher` | register/drop path watches → `obs:*` envelopes. The adapter implements v1's tailer byte-discipline, which change-notification libraries do NOT (they only say "something changed"): read exactly `size - pos` (an unbounded read loses bytes appended mid-read, which `pos = size` then duplicates); truncation ⇒ restart at 0; complete lines only (torn-tail re-read); NO line cap on JSONL (a truncated line breaks `json.loads` and silently drops records); watch paths refreshed per event (Claude Code relocates a transcript when the cwd changes projects); polling cadence supplements FSEvents (macOS coalesces/delays; the interrupt record must be seen promptly) | watchfiles + polling fallback, fake |
| `ProcessRunner` | spawn/supervise/probe | real, fake |
| Storage | the library's recorders + the blob store, behind `core/spine.py` | SQLite |

Deliberately NOT ports: config, logging, ID generation, serialization, the
event schema. Test: would a second implementation change what the core does?

`core/spine.py` is the only module importing `eventsourcing` — the walk-away
seam: the library is healthy but bus-factor-one, and this document is the
reimplementation spec if it ever stalls.

---

## 9. Followers

Everything consuming a notification log from a tracking record is a
**follower** (`ProcessApplication`). Three kinds by output, one rule each:

> **Folds compute, policies decide, reactors act.**

| Kind | Rule |
|---|---|
| **Fold** | pure function of its subscription — no clock, no reads of other followers' state; event `ts` allowed (it is in the log). Freely rebuildable. |
| **Policy** | reads clock and/or other state — replaying later could decide differently. Emissions are history, never rebuilt. |
| **Reactor** | effects + **effect events** (§9.3); at-least-once + idempotent. |

Structural rules: **one follower = one output** (one aggregate type / table /
emitted event family); one output = one writing follower; `ls daemon/folds/`
is the data-lineage map. Every fold declares two properties in the registry:
its **storage shape** (§3.3) and its **branch class** (§9.4).

### 9.1 Folds

| Fold | Output (shape) | Subscribes to | Branch class |
|---|---|---|---|
| `agent_attention` | `AgentAttention(lineage)` — event-sourced: its `Changed` transitions ARE the product | command/monitor/turn/dialog/permission/agent events | current-state |
| `command_stats` | table | `CommandStarted/Finished/Aborted` — skips `CommandFinished(interpreted=true)` | cumulative |
| `file_stats` | table | `FileRead/Edited/Written` | cumulative |
| `tool_stats` | table | `ToolInvoked`, `SkillInvoked` | cumulative |
| `active_time` | table | `SessionStarted/Ended`, `AgentAttention.Changed` | cumulative |
| `usage` | table — prices via its own price table; unknown model = tokens counted, no cost; codex priced separately, unverified versions refused (v1 `PRICES`/`CODEX_PRICES` semantics) | `UsageReported` | cumulative |
| `tasks` · `goal` · `model_state` · `title` · `compaction` | tables (latest-value) | their §7 events | current-state |
| `context` | table | `ContextReported`, `CompactionEnded`, `BranchDiscarded` | current-state — the named `BranchDiscarded` subscriber: a rewind past a compaction must stop honoring that compaction's boundary (v1 measured 13,805 shown vs 223,546 held) |
| `presence` | table (singleton) | `DeviceSeen`, `ViewingChanged` | — |
| `team_mail` | table | `TeamMail*` | current-state (unread counts) |
| `account_usage` | table, keyed `Account(slug)` | `RateLimitReported/Hit`, `AccountSeen`, `LoggedOut/In` | — window %, rolled-over zeroing, limit-hit, logged-out grace, per-model weeklies (v1 `usage.py` arithmetic with its tests) |
| `mailbox` · `claims` | tables | `PeerMessage*` / `WorkClaimed/Released` | — |
| `session_index` | table — the list page: lineage, last_active, project by FROZEN start-cwd resolved to its worktree owner, state, ended | `SessionStarted/Ended`, `SidAliased`, `AgentAttention.Changed` | current-state |
| `stats_rollup` | table — heatmap buckets, punch card, per-project rollups | lifecycle, `UsageReported`, `AnomalyDetected` (event-ts batched) | cumulative |
| `errors` | table, per-lineage — the errors tab + the stats KPI | `AnomalyDetected`, effect `*Failed` events | — |

Shared predicates (e.g. session liveness, used by both `session_index` and
`stats_rollup`) live in ONE module — two independently derived liveness
answers is a measured v1 bug (stats over-reported active 13 vs 4).

Multi-upstream rule: a fold following two notification logs has no cross-log
order guarantee (tracking is per-upstream; interleaving is scheduling). Such
a fold must be **order-insensitive across its upstreams** — tolerate an
attention change for a not-yet-seen lineage by creating the row; join on
lineage + event ts for arithmetic that needs pairing, accepting event-ts
granularity. A fold that cannot be made order-insensitive follows ONE log.

#### 9.1.1 The attention state machine

States (v1's set): `idle · working · executing · awaiting-bg ·
awaiting-command (asking) · awaiting-response (done) · cleared`. `working`
deliberately merges thinking / non-Bash tools / reply-writing / compaction —
no signal separates them. The fold tracks `open_cmds` / `open_agents` /
`open_monitors` from starts and closers (§6.2) and carries a full transition
table ported from v1's dispatch (docs/tab-colors.md), including:

- **Precedence is a rule, not arithmetic**: `asking > executing >
  awaiting-bg > working > done` — an agent starting must not erase red
  (a teammate spawning while Claude is blocked on you; v1 measured).
- **What a finished stream proves**: bg/monitor ended → may go done; a
  subagent ended → working (green flashed before the main repainted — v1
  bug); a foreground command ended → working, NEVER done.
- `actor` filtering: a child's inner events never drive the host tab (v1's
  main-session-only invariant).
- Permission events drive red alongside dialogs.
- Emits `AgentAttentionChanged(state, prev)` — the ONE intermediate producer
  in the follow graph. NOT casually rebuildable: replay re-emits its
  transition history to consumers that treat it as news; rebuilding it is a
  runbook operation (quiesce followers first). Accepted; revisit with
  durable emission dedup only if it bites (§21).

### 9.2 Policies

| Policy | Subscribes to | Reads | Emits |
|---|---|---|---|
| `alert` | `AgentAttention.Changed`, `ViewingChanged` + Clock | presence table; **Prefs — a narrow sanctioned read of exactly `{muted(lineage), global_enabled, composing(lineage)}`: prefs are web-local state (§12.5), and these are GATES checked at decision time, not history; the one deliberate exception to log-only inputs** | `Alert.Armed/Held/Dispatched/Cancelled/Retracted/Escalated` — v1's measured semantics ported as fixtures: red `asking` alerts promptly and a look HOLDS, never cancels (seeing a question is not answering it); the ask-region screen-diff hold (typing at the terminal is the one trace answering leaves); green `done` serves a 20s settle (measured knee: 46 retracted pushes, median 14.3s lifetime) and a look resolves; retraction only where "what you were told is no longer true"; machine-wide device activity retracts nothing; presence-MRU channel routing (browser → push, terminal → telegram, ties to browser); stage-1 telegram never escalates |
| `relimit` | `RateLimitHit`, migrate gestures + Clock | account table | `MigrationDecided(lineage, to_account, to_model, mode)` — the downgrade ladder (first rung with headroom, never skip a rung, most headroom within a rung), the scope/reset prose parsers, model-scoped resets from the per-model window (the measured false-clear), the logged-out sibling with v1's never-probe-credentials doctrine (docs/relimit.md) |
| `claims` | `FileEdited/Written` | claims table | `ClaimViolated` — ships only with claims (§14): advisory claims WITHOUT a violation detector are worse than none |

### 9.3 Reactors, effect events, leases

Reactors act on the world; the world's answers must land in the log or the
outbound half of the system is unauditable and unrecoverable. So reactors
carry a narrow emission license:

> **A reactor may append events about its own effect and nothing else** —
> facts nobody else could know, describing the effect just performed, into
> its own output family. An effect event describes the past; it never
> instructs. A reactor reacting to another reactor's effect events needs the
> same grade of argument as a new intermediate producer.

Non-idempotent effects (send a Telegram message, type Escapes, launch a tab)
additionally take a **durable pre-effect lease**: record the attempt intent +
idempotency key before acting, so a crash between act and record resolves to
"attempt in doubt — reconcile", never a silent duplicate.

| Reactor | Consumes | Effect | Effect events |
|---|---|---|---|
| `tab` | `AgentAttention.Changed`, `SessionEnded` (clear — codex's rollout-EOF closer produces the same clear; it has no SessionEnd hook) | `Terminal.set_tab_color` — dedup by last-**verified**-paint: persist only on rc==0 (persisting a failed paint strands a colour and dedup then suppresses every retry — v1 tabpaint's core rule) | `TabPainted/TabPaintFailed` |
| `mirror_feed` | conversation/command/monitor/file/tool/agent/dialog events | serves the pane-host subscription (§15) — the daemon-side half of the terminal mirror | — |
| `sse` | everything (per-connection filters) | browser deltas (§12.2) | — |
| `alert_delivery` | `Alert.Dispatched/Retracted` | `AlertChannel.deliver/retract` — retraction needs the channel receipt (`message_id`), which is why delivery outcomes are events; unconfigured telegram credentials degrade to deliver-yes/retract-no (v1) | `AlertDelivered(message_id)/DeliveryFailed` |
| `peer_delivery` | `PeerMessageSent` | recipient inbox notice / turn-boundary injection (§14) | delivery receipts |
| `watch_supervisor` | `SessionStarted/Ended`, `SidAliased`, `AgentSpawned/Finished`, unwrapped `CommandStarted`, `MonitorStarted/Ended` | `FileWatcher` register/drop — session transcripts, agent transcripts (`subagents/agent-<id>.jsonl`, from `AgentSpawned`), monitor outputs, plus the standing machine-wide watches: codex's global session dirs, team-mail inbox dirs. The watch set is derived state, rebuilt from the log on restart | — |
| `control` (ControlService) | gesture POSTs / MCP calls (inbound) | drives `AgentControl` (screen-verified, seconds-long, failure-prone) | `gesture:*.result` envelopes → `GestureCompleted` |
| `launcher` (SessionLauncher) | `MigrationDecided`, launch/resume gestures | `Terminal.launch_tab` with the host's login-shell argv (`$SHELL -lic '<alias> "$@"' …` — a GUI terminal execs with no user PATH/aliases; v1 measured). Guards: refuse resume on a LIVE lineage (two processes on one transcript corrupt it — also why relimit waits for `SessionEnded` before relaunching); gone-transcript 410; wrong-tool guard (never `claude --resume` a codex transcript); `--keep-focus` when the terminal is not frontmost; `--continue` supported | `SessionLaunched/LaunchFailed` |

The pane/scorebar rendering is NOT a follower — see §15.

### 9.4 Branch classes (rewind semantics)

A rewind (checkpoint restore) abandons transcript turns; `BranchDiscarded`
(§6.2) records it. Consumers split by declared class:

- **cumulative** — ignores `BranchDiscarded`, by design: "what did this
  session consume globally" includes branches that were thrown away
  (commands, tokens, files, time). Matches v1, which never un-counted
  either.
- **current-state** — must be live-branch-true. Latest-value folds are
  overwritten by the next live-branch event and additionally handle
  `BranchDiscarded` where a stale dead-branch value could linger with no
  successor (a goal set only on the dead branch must clear); `context` has
  real semantics behind it (§9.1). A fold handles the event in its own
  `policy()` like any other — compensation-by-subscription; no rebuild
  machinery.
- **read-side** — conversation, mirror history and the events endpoint
  filter to the live branch at query time via record ancestry.

### 9.5 The follow graph

```
Intake ─▶ SessionLog (mapper policies) ─▶ folds · policies · reactors
                └─ agent_attention ─▶ tab · alert · session_index · active_time · sse
```

Flat except the one sanctioned intermediate. A new intermediate producer
needs an argument as good as "consumers need ordered transitions, not
current state".

### 9.6 Time

1. **Deadlines that decide** (settle windows, escalation): *arms are truth,
   timers are doorbells.* Arming is an event appended BEFORE any timer
   exists; the `clock.call_at` timer is ephemeral and never persisted — on
   restart the policy rehydrates open arms (future due → re-schedule; past
   due → fire now: late, never lost). Firing re-checks conditions at fire
   time; emission is idempotent on the arm.
2. **Presentation ticks** (the scorebar's ⏱): loop timers in renderers; a
   missed repaint repaints next tick.
3. **Timestamps in logic**: always the event's `ts` — folds stay
   deterministic; history renders identically forever.

With `Clock` injected, every deadline behavior is table-driven-testable in
milliseconds (v1 validated the same semantics by measuring 46 production
pushes).

---

## 10. Delivery mechanics

- The runner prompts followers on append; followers pull from their tracking
  records. Prompts carry no data; **delivery is always a notification-log
  read** (§5.4 lists the only two exceptions) — a lost prompt, slow
  follower, crash or restart all reduce to "behind", which self-heals.
- Coalescing is automatic (N appends while busy = one catch-up batch).
- Any in-memory fast path is a **cache of the log, never a channel beside
  it**: only committed notifications, in order, with IDs. This sentence is
  load-bearing; optimizations that publish pre-commit or reorder silently
  break the recovery story.
- Async policy: asyncio for the waiting (intake socket, watchers, SSE,
  timers); sync for the working (the library is synchronous — bridged via a
  thread; `kitten @` calls in executors). anyio task groups supervise
  follower runners and watcher lifecycles — no orphaned tasks.
- The library's runner has each follower pull notifications itself. At this
  system's volume over one WAL SQLite that is acceptable; the named escape
  hatch is a custom read-once runner (§21).

---

## 11–12. API

Surfaces: the intake socket (edge), the **web API** (below), MCP
(cooperation, §14), the CLI (daemon start/stop/status + the query/debug
successor of v1's audit CLI). One idea does most of the work:
**notification IDs extend to clients — a browser is a follower with a
checkpoint.**

### 12.1 Read side (FastAPI)

```
GET /api/v1/sessions                      → session_index table (sorted, grouped)
GET /api/v1/sessions/{key}                → the facet bundle: attention, stats,
     usage, tasks, goal, compaction, model, context, title, team_mail, account
     + AgentControl caps; {key} is lineage or any aliased sid. Facets are
     DECLARED per host — a host with no such mechanism (codex: tasks,
     monitors) returns absent, not zero, and the card hides (v1's
     implemented/DECLINED matrix)
GET /api/v1/sessions/{key}/events?after=N&types=…&actor=…&branch=live
GET /api/v1/stats                         → stats_rollup + errors tables
GET /api/v1/blobs/{ref}                   → §16 rules; immutable, ETag=ref
```

- Facet reads are indexed selects on materialized tables; `repository.get`
  only for the event-sourced few. No folding at request time.
- The events endpoint IS the log, filtered: mirror backlog, agent scope
  (`actor=`), parked history — one route. View modes are queries plus
  rendering; "parked" is not a state, just a stream that stopped growing.
- Read-time extras computed in QueryService (they need I/O and are nobody's
  fold): git branch/worktree/dirty chips (TTL-cached; the dirty flag is the
  one sanctioned `git status` subprocess — v1 rule), slash-command discovery
  for the composer's "/" menu (per-host vocabulary: a codex session
  completes against codex's palette, not Claude's), the compact-enablement
  prompt count (Claude Code refuses `/compact` on a fresh chat).

### 12.2 SSE

`GET /api/v1/events?after=N…` — each SSE event's `id:` is its notification
ID; reconnect resumes via standard `Last-Event-ID`. The registration race is
closed broker-side: register the connection first, then serve a log-backed
catch-up from N to the captured high-water mark, then splice into the live
feed deduping by ID; overflow **during** catch-up degrades to a fresh
server-side catch-up at the new high-water mark, never a client retry loop.
Position-keying replaces v1's delta-vs-snapshot reconciliation; it does NOT
replace the static-asset half of v1's boot-id (cache-busted asset URLs + the
"updated — refresh" toast survive as-is).

### 12.3 Gestures (write side — asynchronous, honestly)

```
POST /api/v1/sessions/{key}/gestures {kind, args} → 202 {gesture_id} | 409 | 401
POST /api/v1/sessions                {cwd, account, model, effort, prompt?}
POST /api/v1/sessions/{key}/resume   {message?}    (resume & send: the message
                                                    rides the --resume argv)
```

The POST appends the `gesture:*.requested` envelope and returns; outcomes
arrive as `GestureCompleted` / launch effect events over SSE, correlated by
`gesture_id` — driving a TUI takes seconds and can fail after acceptance; a
synchronous 200 would be a lie papered over with polling. `?wait=3s` is
sugar. Capability discovery rides the bundle; UIs grey buttons with the
named missing condition, never hide them.

### 12.4 Auth

The tunnel makes this API internet-reachable, and v1's threat model holds:
**reaching the control plane is RCE on the laptop** (docs/remote.md). Bearer
token on everything. Browser reality: native `EventSource` cannot set an
Authorization header, so the browser credential is a cookie — `Secure;
HttpOnly; SameSite=Strict` — and every mutating route additionally requires
v1's `_post_guard` set: JSON content-type (forces preflight), custom header
or allowlisted Origin, no `Access-Control-Allow-*` ever emitted. Bind stays
127.0.0.1 with NO `0.0.0.0` knob (exposure is the proxy's job); a `READONLY`
mode kills the control plane before any other guard.

### 12.5 Web-local state

Prefs (mutes, the global alerts switch, hidden dirs with the live-session
409 + started-after-hide re-appear, tasks-dismissal keyed by task IDs with
the all-done 409, keep-awake, new-session prefs), composer/new-session
drafts, uploads (staged under an uploads dir; **attachment mentions admit
only paths inside it** — a security jail, v1 `_attachment_paths`), the
clipboard-file paste resolution (the server reads the same pasteboard kitty
reads; basenames must AGREE so a remote paste can never be answered with a
host path — v1 `dashboard/clipboard.py`), dictation token minting. All
web-local — not domain events — with exactly two bridges: presence (promoted
to domain; alert routing needs it) and the alert policy's narrow Prefs read
(§9.2).

### 12.6 Frontend audit channel

The server only sees requests that ARRIVE; v1's "still not closing" bug
class (a control POST the browser tried that never reached a handler) was
invisible until the browser reported its own transport lifecycle. The SPA
posts `client:*` envelopes: per-gesture begin/ok/fail with timing, SSE
up/down, uncaught JS errors, a per-load boot record — landing in intake,
queryable beside the server-side story. Close/stop rides plain `fetch`,
never `sendBeacon` (v1 measured the tunnel queue-then-drop regression).

### 12.7 Web extensions

v1's memory tab is a plug-in seam, kept: an `api/ext/` registry — tab
descriptor, route table, SSE channels, QueryService accessors — spliced at
composition; core imports no extension (contract-enforced); an extension's
POSTs pass the same tier guards as built-ins. The memory extension itself:
the note tree (reads/writes/updates labelled, team-wide), the note viewer
(markdown → safe HTML, `[[wikilink]]` whole-vault resolution, backlinks),
search cards, and the project scope gate (active only for sessions under the
configured vault project).

---

## 13. Alerting slice

`policies/alert.py` decides WHEN/WHETHER (§9.2); `alerting/` owns HOW:

```
alerting/
├── events.py      the Alert aggregate + its lifecycle events
├── delivery.py    AlertDeliverer (§9.3)
└── channels/      webpush.py · telegram.py · toast.py  (private to delivery.py)
```

Import rule: the policy emits `alerting.events`; `alerting/` consumes them;
nothing else imports `alerting.channels` or `alerting.delivery`.

---

## 14. Cooperation (MCP surface)

The daemon serves MCP tools to sessions: `sessions.discover(project=…)`,
`sessions.send`, `sessions.inbox()`, `work.claim/release`. Every call is an
envelope → domain event. Mediated hub topology, never peer-to-peer:
observable (traffic is in the log), governable (quotas/mutes/consent are hub
policies), tool-agnostic (a Claude Code session can message a codex
session).

Hazards, designed-in: a peer message is untrusted model output — delivered
with provenance framing (treat as data; a peer cannot approve actions),
never auto-executed, never able to answer a pending permission prompt.
Runaway loops: per-session send quotas, thread TTLs, replies wait for the
recipient's next natural turn. The human stays the principal: discovery
read-only by default, messaging opt-in, active TUI delivery human-initiated
only. Sequencing: discovery first, mailboxes second, claims last and only
with the `claims` policy. **New scope — explicitly off the migration's
critical path.**

---

## 15. Terminal surfaces

### 15.1 The pane-host

The mirror and scorebar are panes whose *content* the daemon computes but
whose *terminal* only a process in the pane can own — SIGWINCH is delivered
to the pane's process, and scrollback is written through its stdout. So each
terminal package ships a **pane-host**: a thin process launched into the
pane (`terminals/kitty/panehost.py`) that (a) subscribes to the daemon
(socket/SSE, from a notification ID), (b) owns stdout + SIGWINCH, and
(c) runs the **renderer library** locally. All kitty-specific code lives in
`terminals/kitty/` — pane-host included; a future terminal brings its own; a
headless/programmatic deployment launches none and the mirror remains fully
available via the web. A daemon outage freezes the pane (no live data, no
special indicator — accepted); it un-freezes by catch-up on reconnect, like
every other follower.

### 15.2 The renderer library (terminal-agnostic)

The reflow contract, inherited from v1 whole: keep an in-memory block model
built from domain events (bounded — v1's resident-ops cap, ported; older
blocks summarized), render at the current width, **re-render everything on
SIGWINCH**. Width-dependent work lives here and only here: wrapping, gutter
repetition with ANSI re-assertion per visual row, rule lengths, full-width
panels, chip fitting, display-width math (wcwidth: CJK/emoji 2, ZWJ/VS16 0;
tabs expanded before width math), per-block render caching keyed on
immutable event identity. Render classification (source/markdown/JSON/YAML,
fenced-output sniffing, read-collapse one-liners, file display naming) is a
pure classifier module with fixtures ported from v1's golden files.

The scorebar renders from the facet tables (1s tick through the pane-host's
API reads) with v1's tail-truncation priority (the ⚠ chip is never shed; Σ
drops first). The ⚠ operational-warning chip reads the daemon's health
counters (the errors table / health endpoint) — v1's errwatch semantics: one
mirror line per new anomaly, flood-collapsed.

### 15.3 The click transport

The pane's only input channel: OSC 8 hyperlinks with a custom URL scheme →
kitty `open-actions.conf` → a handler that posts a gesture envelope → the
daemon updates view state → the pane-host repaints, with **viewport
restoration** (relocate the pre-toggle screen content in the repainted
scrollback and scroll back to it; v1's locate/probe/corrective-scroll
machinery is the reference — docs/click-to-view.md, six named regressions
live there). ⧉ copy links and click-to-expand ride it; copy groups: commands
by `tid`, prose blocks get synthetic groups. This wiring is part of the
kitty package's install contract.

### 15.4 Screen-driver discipline (unchanged v1 law)

Prefer screen-delta over any literal marker; vim editorMode makes Escape
modal (first Esc exits INSERT); verify every keypress by re-reading the
screen; rewind confirm picked by parsed LABEL, never position; ask-dialog
Escape DECLINES (a failed step leaves the dialog untouched) while codex
Escape ABORTS THE TURN (never Esc-close a codex dialog); dialogs detected by
shape, not header text; open-checks poll (a single capture bailed on a
dialog still rendering after resume). Each is a fixture, not folklore.

---

## 16. Presentation security (neutralize)

**The invariant, generalized from v1:** raw captured output must never
execute in a rendering context. The pane repaints history on every reflow —
an embedded escape sequence executes again on every resize, forever (v1
found this live: a tee'd `@kitty-cmd` DCS scrolled the pane on every
repaint). The web renders the same bytes in the origin holding the control
credential — there the failure is XSS against an authenticated control
plane.

- **Sanitize at render time (the leaf), never at ingestion** — ingestion
  scrubbing destroys evidence, cannot retro-protect history from sanitizer
  bugs fixed later, and must guess every consumer's context.
- Terminal: strip everything except SGR and the renderer's OWN OSC 8 links.
- Web: HTML-escape at the leaf; link schemes allowlisted to http(s).
- `GET /blobs/{ref}` never serves bytes as a renderable type into a browser:
  `text/plain; charset=utf-8` + `X-Content-Type-Options: nosniff`; the SPA
  renders through its sanitizer, never by pointing the document at a blob.
- A new surface inherits this section as a port-level obligation.

---

## 17. File structure

```
baqylau/
├── protocol/                        # the language-neutral contract
│   ├── envelope-frames.md           #   frames, kinds, answerable list, excluded hooks
│   └── schemas/                     #   JSON Schema from pydantic → Rust/TS codegen
├── edge/                            # Rust workspace — hot path, logic-free
│   ├── crates/  baqylau-proto/ · baqylau-shim/ · baqylau-tee/ · baqylau-exec/
│   └── tests/                       #   daemon-killed-mid-command, signals, frame fuzz
├── plugins-ts/opencode/             # the one TS exception (Bun host requirement)
├── daemon/
│   ├── core/
│   │   ├── events.py                # the §7 vocabulary (pydantic v2)
│   │   ├── envelopes.py · ports.py · blobs.py
│   │   ├── spine.py                 # the ONLY importer of `eventsourcing`
│   │   └── intake.py                # Intake application + answerable responder routing
│   ├── sessionlog/                  # THE writing application
│   │   ├── app.py                   #   SessionLog ProcessApplication + the lineage table
│   │   └── mappers/
│   │       ├── claude_code/  policy.py · inference.py · classify.py
│   │       ├── codex/        policy.py · rollout.py
│   │       ├── opencode/     policy.py
│   │       ├── otel.py · statusline.py · gestures.py · presence.py · clientlog.py
│   ├── controls/                    # AgentControl adapters + their screen drivers
│   │   ├── claude_code/ · codex/ · opencode/
│   ├── folds/                       # one file = one follower = one output
│   │   ├── agent_attention.py · command_stats.py · file_stats.py · tool_stats.py
│   │   ├── active_time.py · usage.py · tasks.py · goal.py · compaction.py
│   │   ├── model_state.py · title.py · context.py · presence.py · team_mail.py
│   │   ├── account_usage.py · mailbox.py · claims.py
│   │   ├── session_index.py · stats_rollup.py · errors.py
│   ├── policies/   alert.py · relimit.py · claims.py
│   ├── reactors/   tab.py · mirror_feed.py · sse.py · alert_delivery.py
│   │               peer_delivery.py · watch_supervisor.py · control.py · launcher.py
│   ├── alerting/   events.py · delivery.py · channels/{webpush,telegram,toast}.py
│   ├── render/                      # the terminal-agnostic renderer library (§15.2)
│   ├── terminals/
│   │   ├── kitty/   remote.py · windows.py · panes.py · screen.py · panehost.py
│   │   ├── wezterm/                 # (future) a neighbor package
│   │   └── null/
│   ├── adapters/   clock.py · watchfiles_.py · procrun.py
│   ├── api/
│   │   ├── app.py                   # create_app(), composition of routes
│   │   ├── routes/  sessions.py · events.py · gestures.py · stats.py · weblocal.py · clientlog.py
│   │   ├── sse.py · auth.py · query.py · ext/
│   ├── mcp/server.py
│   ├── web/                         # the SPA (TS; types generated from protocol/schemas)
│   └── main.py                      # composition root — the only place concretions meet
├── cli/baqylau.py
├── tests/
│   ├── contracts/                   # per-port suites × adapters; frame conformance (3 langs)
│   ├── folds/ · policies/           # table-driven + hypothesis; fake-clock scenarios
│   ├── mappers/                     # envelope fixtures → expected events; PORTED v1 fixtures
│   ├── render/                      # golden-ANSI tier (ported from v1)
│   ├── web/                         # DOM-executing SPA tier
│   └── e2e/
├── docs/   design.md · decisions/ · runbook.md
├── Makefile                         # build edge, gen schemas, test, lint
└── pyproject.toml
```

Structure-enforced rules (pylint AST plugins): import direction (`core`
imports nothing above it; tiers import core only; `spine.py` is the sole
`eventsourcing` importer; `alerting.channels` private; tool knowledge jailed
in `sessionlog/mappers/<tool>/` + `controls/<tool>/`); one fold, one output.
Folder doctrine: tier folders are the default; a feature earns a vertical
slice only when it owns ≥3 tiers AND its events have no outside consumer
(`alerting/` qualifies; a future `cooperation/` slice is pre-approved).

---

## 18. Tech stack

**Rust**: `baqylau-shim`, `baqylau-tee`, `baqylau-exec` — per-call hot path,
logic-free, protocol-stable. **TypeScript**: the opencode plugin (host
requirement) + the SPA. **Python**: the daemon, where all change
concentrates.

| Concern | Choice | Note |
|---|---|---|
| Event sourcing | `eventsourcing` | the spine (§3.1), behind `core/spine.py` |
| Types | pydantic v2 (+ pydantic-settings) | the single type layer: events, envelopes, API models, JSON-Schema export; settings replaces env-knob folklore |
| HTTP / SSE | FastAPI + uvicorn / sse-starlette | resumption logic is ours (§12.2); the library provides transport + pings |
| Async | anyio | structured concurrency; no orphaned tasks |
| File watching | watchfiles + polling fallback | change *notification* only — the byte-discipline is ours (§8) |
| Logging | structlog | operational log (bound `lineage=`, `follower=`), distinct from the event log |
| Retries | tenacity | channel delivery, kitten RC calls |
| CLI | typer + rich | |
| Tests | pytest + pytest-asyncio + hypothesis | property tests over pure folds; the Clock fake is ours — never freezegun: we designed the seam |
| Packaging | uv + hatchling | |
| Lint/type | mypy --strict · pylint (deep passes + the architecture plugins) · ruff (format + fast lint) | strict from day one |

Deliberately not adopted: celery/redis (the runner + log IS the task
system), SQLAlchemy/alembic (recorders own storage), DI frameworks
(main.py + Depends), APScheduler (arms-are-truth is the scheduler),
Kafka-anything. Rule: adopt libraries for solved generic problems, never for
anything touching the architecture's own guarantees.

---

## 19. Migration (strangler fig — empirical knowledge is ported, never rewritten from memory)

Entry criterion before step 1: two benchmarks pass — tab-paint end-to-end
latency under the full chain (target <100ms; v1 paints in ~0.1ms from inside
the hook, so this is the regression to bound) and a synthetic build-log
stream at reporter chunk cadence. Performance figures elsewhere in this
document are targets, not measurements, until these run.

1. **Daemon + logs.** Intake + SessionLog + a claude_code mapper subset. v1's
   hooks dual-emit envelopes; v1 remains production.
2. **Web reads events.** Dashboard read side onto QueryService/SSE; terminal
   still v1. The comparison gate covers attention/counters/metadata — not
   output fidelity (no reporter yet).
3. **Reporter + folds + policies.** The `updatedInput` slot is singular: v1's
   cmd-pre stands down per-session when v2 claims it (a per-session flag —
   the same flag is the arbiter of pane ownership in step 4). The alert
   state machine ported with its measured tables as fixtures. Sandbox
   interference with the reporter's socket is measured here.
4. **Terminal surfaces.** Parity harness: golden-ANSI files rendered by both
   pipelines from the same session, diffed BEFORE v1's pipeline is deleted;
   the resize/toggle/click checklist; the DOM-executing SPA tier (v1's
   lesson: three JS fixes were "verified" clean while the screen was
   unchanged).
5. Deletion always lags one gate: every step's v1 half is disabled by flag,
   not deleted, until the NEXT step's gate passes — a failed gate rolls back
   by flag-flip. During coexistence, every effectful plane (tab paint,
   notifications, gestures, transcript watching, usage accounting) has a
   per-session owner flag — the losing side runs read-only — so no duplicate
   alerts and no double Escapes.

Gate for each step: v1 and v2 agree on observable outputs for the same live
traffic, for days, before the v1 half is retired.

---

## 20. Tradeoff ledger (the commitments, stated plainly)

| Gained | Paid |
|---|---|
| One source of truth; audit = the system; every input replayable | a supervised daemon as single point of failure; a daemon outage is an accepted observation gap for pushed envelopes (§5.2) |
| Derived state disposable (rebuild); evidence replayable (remap, within intake retention) | two logs to govern; remap of consumed history is surgery |
| New tool/terminal/surface = one adapter; view modes = queries | more upfront structure: ports, contract tests, fakes, a build system (Rust edge) in a repo that had none; ~2–3× the engineering of "patch v1" |
| Folds pure and property-testable; policies fake-clock-testable; no sleeps, no live kitty in tests | the latency chain (envelope→map→append→prompt→fold→paint) must be *benchmarked* against v1's 0.1ms in-hook paint (§19 entry criterion) |
| Notification IDs: loss-free, restart-proof, late-joiner-proof consumption — extended to browsers | eventual consistency between views (tens of ms) — a property, not a bug |
| Presentation fully surface-owned; no producer flags | shared classifications must live in the schema deliberately or two presenters drift (v1's lesson — encoded as §6.3's principle) |
| The empirical tool knowledge concentrated in named, versioned, fixtured rules | it does not shrink; every rule re-risks its bug until its fixture is ported |
| Rust edge: fixed, protocol-stable, near-zero overhead | compiled artifacts; recompile-to-change (deliberate: edges never change) |
| the `eventsourcing` spine: tracking/snapshots/runner shipped, not built | sync library under async daemon (one bridged seam); bus-factor-one wrapped behind `spine.py` with this document as the reimplementation spec |

---

## 21. Deferred items (each with its named trigger)

- Custom read-once runner — trigger: the step-1 volume benchmark, then
  measured notification-scan cost in production.
- Durable emission dedup for `agent_attention` rebuilds — trigger: the first
  time such a rebuild is actually needed (until then: runbook surgery,
  §9.1.1).
- Branch-sensitive compensation for any cumulative fold — trigger: someone
  actually wants post-rewind-exact totals (§9.4's classes are the spec until
  then).
- Provenance side-table (event → envelope IDs + rule version) — trigger: the
  first painful mapper-debugging session; until then intake + structlog.
- codex `inference.py` as a module — trigger: its third no-signal rule.
- FTS / arbitrary-predicate queries over the corpus — trigger: the first
  real query the index tables can't serve.
- `cooperation/` vertical slice — trigger: shipping mailboxes + claims (off
  the migration's critical path).
- Python fallback for the Rust edge — legal any time; the protocol is the
  contract.
