# baqylau v2 — Rewrite Design

Status: **PROPOSED, revision 2** — design complete, not implemented. This
document is the distillation of a long design conversation (2026-07-31 …
2026-08-01) and records every committed decision, the vocabulary, the full
component registry, and the tradeoff ledger. The current (v1) system stays
authoritative until the migration section's gates are met.

Revision 2 (2026-08-01) applies the Tier-1 resolutions from the three-way
adversarial review: **C1** reactors may emit *effect events* (restricted
emission license) · **C2** `SessionWriter` serializes all canonical appends ·
**C3** stable **lineage id** with runtime sids as aliases · **C4**
`BranchDiscarded` rewind/branch model · **C5** per-gap bespoke closers, NO
general liveness prober (decided; residual recorded) · **C6** emitting-fold
rebuild duplication accepted as a labeled corner case (no machinery) · **C7**
the terminal renderer subsystem (reflow) + presentation security (neutralize)
sections. It ALSO fills the Tier-2 gaps (OTEL ingest §5.4; statusline/
accounts/relimit §5.5; subagent ordering + tailer discipline §5.6; session
launch §7A; monitors/skills/team-mail/team-tasks/child-tasks/ctx/title in the
vocabulary §6; clientlog §11.6; web extensions §11.7; clipboard guard,
attention spec §9.1.1) and the Tier-3 contradiction fixes (shim fail-loud
definition, interp guard on success, cost out of the event, intake-TTL =
remap window, multi-upstream ordering, SSE registration race, auth hardening,
socket trust boundary, retention/caps, codex auto-allow gate, wrapper kill
semantics, migration hand-off/golden-tier/rollback). Tier-4 decisions
recorded: fs lookup ALLOWED for memory classification (§5.3); prefs STAY
web-local with one sanctioned `AlertPolicy` read (§9.2).

---

## 1. Why rewrite

v1 (this repo today) works, but its architecture is accidental in three ways:

1. **The ops stream fuses presentation with semantics.** Paint ops carry glyphs
   (`▶ foreground`), RGB triples, and a growing pile of per-consumer routing
   flags (`web`, `note`, `chrome`, `bubbled`, `who`, `tags`, `act`, `mem`,
   `ctask` …). Every time the web needed to *understand* rather than *display*,
   a baked-in string got promoted to a field — the design converges on semantic
   events anyway, one bug at a time.
2. **No long-lived brain.** ~20 short-lived hook processes coordinate through
   SQLite tables, take-once hand-offs, sentinel files, pid-liveness slot rows
   and detached tailers. That coordination layer (slots, adopt machinery,
   `parked()` probes, inode revalidation, stale-row stealing) IS most of the
   complexity — and most of the bug history.
3. **Derived views are producer-maintained promises.** Twenty callers must each
   call `bump()` correctly; the tab state machine is smeared across dispatch +
   recovery hacks; the dashboard re-parses raw transcripts per request. Wrong
   numbers are permanent (hence `sql-write` fixups) instead of rebuildable.

What a rewrite cannot fix (constraints that survive any architecture): hooks
must never block or fail; Claude Code fires **no hook on cancel/interrupt**;
tool payloads are undocumented and version-fragile; TUI screen-scraping is
irreducible for the control plane. The rewrite *contains* these per-tool; it
does not shrink them.

### Goals

- Separation of concerns / loose coupling / SOLID: agent tools, terminals, and
  presentation surfaces are all pluggable behind small ports.
- Event-driven, async where waiting happens, sync where computing happens.
- Multi-tool: Claude Code, codex, **opencode** (which does not follow the
  Claude Code hooks pattern) — and future tools — as symmetric source adapters.
- Multi-terminal: kitty first, wezterm/others as neighbor packages.
- Multi-surface: terminal mirror+scorebar, web dashboard, notifier — and future
  surfaces — as subscribers over one truth.
- Every derived fact rebuildable; every input replayable; audit = the system
  itself, not a parallel write path.

### Non-goals

- No distributed operation. One machine, one daemon.
- No conceptual purity for its own sake — pragmatic exceptions are recorded
  with their triggers (see §9.6, §18).

---

## 2. System overview

One supervised **daemon** (Python, asyncio at the edges) hosts everything.
Around it: thin **edge components** (Rust) inside the agent tools, and
**adapters** for terminals/channels.

```
                    INBOUND                            CORE (daemon)                       OUTBOUND
 ┌─────────────────────────────┐   ┌────────────────────────────────────────────┐   ┌──────────────────┐
 │ baqylau-shim (Rust)         │──▶│  INTAKE LOG ──▶ MAPPERS ──▶ SESSION LOG    │──▶│ Terminal port    │
 │   claude_code + codex hooks │   │  (envelopes)   (per-tool)  (canonical      │   │   kitty · null   │
 │ baqylau-exec (Rust)         │──▶│                             events)        │   ├──────────────────┤
 │   command wrapper, all tools│   │        │                        │          │   │ AlertChannel port│
 │ opencode plugin (TS)        │──▶│        ▼                        ▼          │   │  webpush·telegram│
 │ FileWatcher observations    │──▶│    BLOB STORE          FOLDS · POLICIES ·  │   │  · toast         │
 │ gestures (web POST / MCP)   │──▶│  (content-addressed)   REACTORS           │──▶│ AgentControl port│
 │ presence beats              │──▶│                        (subscribers)       │   │   per tool       │
 └─────────────────────────────┘   │   QueryService · ControlService · MCP      │   └──────────────────┘
                                   └────────────────────────────────────────────┘
                                        ▲ FastAPI: REST + SSE + gestures ▲
                                        └────── web SPA · CLI · phone ───┘
```

Rules that define the shape:

- **Everything crossing into the core is an envelope** — pushed (shims, wrapper,
  plugin, gestures, presence) or pulled (file watchers). Persisted verbatim
  *before* interpretation.
- **Mappers propose canonical truth; `SessionWriter` appends it.** Mappers map
  envelopes to events and hand them to the one serializing writer (§5.0); they
  never paint, count, or notify. No other component may append to a session
  stream.
- **Subscribers never communicate except through the log.** No shared state, no
  direct calls; the one medium gives one ordering, one recovery story, one
  audit trail. Reactors carry a narrow emission license — *effect events*
  about their own effects (§9.0) — so outcomes are in the log too.
- **Surfaces own presentation entirely.** No producer pre-words or pre-colors
  anything for any consumer. Every surface sanitizes at the leaf (§13B).

---

## 3. Storage: two logs, blobs, and what we dropped

### 3.1 Intake log (evidence)

Append-only table of raw **envelopes**:

```
Envelope(tool, kind, payload: bytes (verbatim), env: dict, ts)
```

- `tool` is a routing key only. `env` is ambient facts only (cwd, window id,
  pid, sid-hint) — the moment a shim pre-digests ("this was a failure"),
  vocabulary has leaked to the edge. Forbidden.
- Position-numbered; mappers follow it with exactly-once tracking.
- Retention: long-but-bounded, and the bound is load-bearing — **the remap
  window IS the intake retention** (§8): past it, history keeps whatever the
  then-current mapper concluded. The canonical log is the archive; intake is
  the evidence tier. State the retention in config, not folklore.
- **Socket trust boundary**: the intake socket accepts unauthenticated frames
  from anything on the machine, so it is protected by filesystem permissions
  (0600, owner-only, in a user-owned runtime dir). Two semantic guards ride on
  top: the reserved `terminal` presence device may only be stamped by the
  daemon's own focus prober (a browser claiming it would route every alert to
  Telegram — v1's measured refusal, kept), and gesture envelopes are minted
  only by ControlService/MCP, never accepted raw from the socket.
- Purpose: replayable mapping (a fixed mapper re-runs over stored intake), and
  debugging (intake says what the tool sent; canonical says what we concluded;
  a divergence is located *between two persisted logs*).

### 3.2 Canonical event log (truth)

The `eventsourcing`-library-backed store (§8). Append-only, globally
position-ordered (the notification log), one aggregate stream per session plus
derived and global aggregates. This is the mirror history, the audit trail, and
every consumer's input — one write path.

### 3.3 Blob store

Content-addressed (`sha256`) store for bulk bytes: command output chunks,
file contents, diffs, tool responses, plan texts, peer-message bodies. Events
carry `BlobRef`s; the log stays lean; blobs are immutable so HTTP-cacheable
forever (served per §13B — never as a renderable type into a browser).

**Retention/caps (review fix — a forever-log now carries all command
output):** per-command output is capped at ingestion (v1's three-level lesson:
per-pump, per-line for non-JSONL, per-block; a 10MB build log must not become
1250 events × 24 subscribers × forever) — the cap events record truncation
honestly (`CommandOutput(truncated=True, total_bytes)`); blob GC follows the
canonical log's referencing horizon (a blob unreferenced by any retained
event is collectable); intake follows its own retention (§3.1). All three
figures are named config, reviewed together.

### 3.4 Dropped (deliberate, recorded)

- **Provenance table** (event → envelope ids + inference rule + mapper
  version): dropped for now. Intake still preserves the evidence; what we lose
  is the pre-built join — mapper debugging becomes manual archaeology. Cheap to
  re-add later (it was always a side table nothing was allowed to read for
  logic).
- **Shim failure tracking / spool files / IntakeGapMonitor**: dropped. Shims
  are thin; when they fail they must fail **fast and loud** — with "loud"
  precisely defined, because a hook's non-zero exit is a CONTROL SIGNAL in
  Claude Code (it can block the tool call), and the first invariant is that
  hooks never block: **a shim always exits 0 toward the tool**; loud =
  a stderr line (best-effort) + the daemon-side absence being surfaced by the
  CLI health command and the scorebar ⚠ light (§9.3). The narrowed guarantee
  is stated plainly: **a daemon outage is an unrecoverable observation gap**
  for pushed envelopes (accepted trade; hooks corroborated by watchers close
  most correlations after recovery).
- **LivenessMonitor**: dropped — `baqylau-exec` makes command liveness a
  reported fact. Residual known gap: a session process dying without a
  SessionEnd hook (kill -9, terminal crash) leaves an eternally-"working"
  card; detectable later by a trivial pid check at read time if it ever
  annoys. Recorded, not solved.

---

## 4. Edge components (Rust + one TS exception)

Hot-path, logic-free by decree: all evolution happens daemon-side in mappers.
The moment someone wants per-tool logic in a shim, the answer is "no — mapper".

### 4.1 `protocol/` — the language-neutral contract

Length-prefixed frames over a unix socket. Three implementations bind to it
(Rust edge, Python daemon, TS plugin), so it is written down precisely,
versioned, and conformance-tested from all three sides. Envelope kinds:

```
hook:<EventName>        pushed by baqylau-shim (claude_code, codex)
wrapper:started|chunk|exited   pushed by baqylau-exec
plugin:<event>          pushed by the opencode TS plugin
obs:transcript|rollout|inbox   pushed by daemon-side FileWatcher consumers
otel:metrics            pushed by the daemon's OTLP receive adapter (§5.4)
statusline:update       pushed by baqylau-shim in statusline mode (§5.5)
gesture:<g>.requested|.result  appended by ControlService / MCP
presence:beat|away      pushed by web pages and the terminal-focus prober
client:<record>         pushed by the SPA (frontend audit channel, §11.6)
```

### 4.2 `baqylau-shim` (Rust)

One binary wired into Claude Code's and codex's hook configs
(`baqylau-shim <tool> <event>`): read stdin, frame as envelope with harvested
env metadata, write to the socket, **print the daemon's reply to stdout if the
kind is answerable**, exit 0 always. Timeout budget ~50ms fire-and-forget,
~200ms for answerable kinds. Zero vocabulary — a tool payload change requires
zero shim changes.

Answerable kinds (request-response exceptions to fire-and-forget):
- `hook:PreToolUse` for Bash — the daemon may reply with the `updatedInput`
  wrapper rewrite (§4.3).

Socket writes are **non-blocking with a hard deadline as a property of the
frame codec** (not a prose budget): a full socket buffer or wedged daemon must
never block the hook path.

**Install contract — hooks the shim must NEVER be wired to:**
`WorktreeCreate`/`WorktreeRemove` are DELEGATING hooks — registering any
handler tells Claude Code "I will create the worktree" and the handler must
print the worktree path; a silent exit-0 reads as "succeeded, no path" and
breaks every `EnterWorktree` and worktree-isolated agent spawn on the machine
(v1 hit this live; docs/wiring.md). The accepted cost: those two events are
unobservable. Generally: any hook whose STDOUT is load-bearing to the tool is
excluded from the generic wiring and listed in `protocol/envelope-frames.md`.

**Statusline mode** (§5.5): `baqylau-shim statusline` wraps the user's real
status line — forward stdin/stdout verbatim (never break the status line),
ship the stdin JSON as a `statusline:update` envelope. This is the ONLY
channel Claude Code exposes rate-limit windows on.

### 4.3 `baqylau-exec` (Rust) — the command wrapper

One sentence: **a transparent exec-level tee whose report channel is allowed to
die and whose pass-through channel is not.**

Invocation (injected per-tool at rewrite time; metadata in env, not argv — no
quoting hazards, nothing leaking into the visible command string):

```
BAQYLAU_SID=<sid> BAQYLAU_TID=<tool_use_id> BAQYLAU_SOCK=<path> \
  baqylau-exec -- <original command string>
```

Behavior, in priority order:

1. **The command's behavior is sacred.** Runs `bash -c "$1"` in its own process
   group — shell semantics (pipes, `&&`, heredocs, globs) preserved exactly
   because we wrap at exec level, never edit the command text. stdout/stderr
   pass through byte-for-byte; exit code propagated exactly; signals forwarded
   to the child's pgroup; child reaped. Commands never had a TTY under these
   tools, so the added pipe hop changes no `isatty` semantics.
2. **Reporting is best-effort, never load-bearing.** Socket unreachable at
   start → run untouched, report nothing. Socket dies mid-stream → keep
   pumping stdout, stop reporting.

Frames: `wrapper:started {sid,tid,pid,pgid,cmd,ts}` ·
`wrapper:chunk {tid,stream,seq,bytes}` (flush at ~8KB or ~50ms) ·
`wrapper:exited {tid,exit,dur,rusage}`. Constant memory (never buffers whole
output); daemon writes chunks to the blob store.

Cross-check for free: PostToolUse still fires and carries the outcome — two
independent witnesses. Where they disagree, or `exited` never arrives (someone
kill-9ed the wrapper), the hook envelope closes the correlation with coarser
data. **The wrapper upgrades fidelity when present; hooks remain sufficient
when it isn't.**

Injection matrix (verified against official docs, 2026-08-01):

| Tool | Injection | Notes |
|---|---|---|
| Claude Code | PreToolUse `updatedInput` | the only sanctioned mechanism; `CLAUDE_CODE_SHELL_PREFIX` exists but is undocumented/enterprise — not production-safe |
| codex | PreToolUse `updatedInput` + `permissionDecision:"allow"` | requires `features.hooks=true`; hooks young & moving — the mapper PROBES injection per session (wrapper's own `started` is the proof) and degrades to rollout-only reconstruction. **Gate + rationale:** replying `"allow"` makes the observability layer an AUTO-APPROVER of every codex Bash call — so injection is behind an explicit per-install config gate (default OFF), documented as a permission trade, and the degrade (rollout-only) keeps observation without granting anything. Source: learn.chatgpt.com/docs/hooks, verified 2026-08-01. |
| opencode | plugin `tool.execute.before` mutable `output.args.command` | |

Edge cases: backgrounded commands (run_in_background / Ctrl+B) are the payoff —
the wrapper survives and keeps streaming; nothing special. Idempotent injection
by prefix check (never double-wrap). The rewrite is visible in the tool's own
transcript — the mapper strips the prefix so every emitted event carries the
*original* command string; the model seeing the wrapper prefix is the honest
cost (same one v1's tee-rewrite pays). OS sandbox profiles may deny the socket
connect — degrade is automatic (rule 2), but **measure before counting on
wrapper-grade fidelity as the norm**.

Kill semantics (stated, not assumed): the child runs in its own process group,
so the TOOL's own cancellation (a `killpg` on the tool's group) hits the
wrapper, which forwards to the child's pgroup — fine while the wrapper lives.
If the wrapper itself dies hard (SIGKILL/OOM), the child is ORPHANED and keeps
running, and `wrapper:exited` never arrives: the wrapper therefore watches its
parent (parent-death → forward SIGTERM to the child pgroup, best-effort), and
the hook envelope remains the correlation closer of last resort. "Commands
never had a TTY under these tools" is a **per-tool measured checklist item**
(Claude Code: verified, pipes; codex/opencode: measure at integration), not an
assumption.

### 4.4 opencode plugin (TypeScript — the host-required exception)

opencode plugins execute inside its Bun runtime; no binary-plugin mode. The TS
plugin (~100 lines, dependency-free, dumb) does exactly two things: forward
hook/SSE events to the socket as `plugin:*` envelopes, and rewrite
`tool.execute.before` bash args to inject the Rust `baqylau-exec`. Do NOT build
on opencode's on-disk storage — it is a moving target (JSON-tree → SQLite
migration with data-loss issues); the plugin + its server SSE are the source.

---

## 5. Mappers (Tier 1) and the SessionWriter

### 5.0 `SessionWriter` — the one appender (C2)

Multiple mappers legitimately produce events for the SAME session (a codex
sidecar inside a Claude session; a web gesture racing a hook), which would
violate the one-writer-per-aggregate rule (§8) if each appended directly. So
appending is centralized: mappers are **proposers** — they map envelopes to
events and hand `(lineage, [events])` to the single `SessionWriter`, which
serializes appends per session stream. It is a tiny, single-purpose
coordinator: no domain logic, no state beyond its queue; it kills the
version-conflict class by construction rather than managing it with retries.
It is also the one home of **identity assignment** (§5.0.1).

#### 5.0.1 Lineage: the session's stable identity (C3)

`--resume` and backgrounding continue a conversation under a NEW runtime sid
with no SessionStart — and an append-only store cannot re-key a stream. So the
aggregate key is a **lineage id**, minted once by `SessionWriter` at first
sight of a conversation; runtime sids are **aliases**. On fork detection (the
inference rules in §5.1) the writer records `SidAliased(lineage, new_sid)` and
routes the new sid's events into the SAME lineage-keyed stream. All folds,
queries, SSE filters and the index key on lineage — one card, one mirror,
continuous stats, by construction. Detection lag is harmless: pre-detection
events are aliased retroactively at read time via the same sid→lineage table.
Events keep carrying the runtime `sid` as a field (evidence); `lineage` is the
key.

### 5.1 The mappers

`ProcessApplication`s following the intake log. Stateful processes, not pure
functions: correlation, dedup, inference. Their working state (pending
correlations, watch positions) must be **derived** — reconstructible from
intake — never load-bearing on its own. Mappers propose; only `SessionWriter`
appends.

| Mapper | Consumes | Notes |
|---|---|---|
| `ClaudeCodeMapper` | `hook:*`, `wrapper:*`, `obs:transcript` | `respond()` answers PreToolUse Bash with the wrapper rewrite; `inference.py`; `classify.py` |
| `CodexMapper` | `hook:*`, `wrapper:*`, `obs:rollout` | probes wrapper injection per session; rollout parse in `rollout.py`. **Dual role (v1 parity):** standalone HOST and SIDECAR source — a codex run launched INSIDE a Claude session is discovered from codex's global session directories (the `WatchSupervisor` registers those two dirs machine-wide, not per-session), correlated to its host lineage by cwd+launch evidence, and its events carry `actor=codex:<aid>` in the host's stream, while a codex-NATIVE subagent is `actor=sub:<aid>` (v1's unified scope key, docs/codex.md) |
| `OpencodeMapper` | `plugin:*`, `wrapper:*` | |
| `OtelMapper` | `otel:metrics` | §5.4 — `UsageReported(query_source)`, delta-temporality sums |
| `GestureMapper` | `gesture:*` | → `InterruptRequested/Confirmed`, `PeerMessageSent`, rename/compact/model/migrate outcomes … |
| `PresenceMapper` | `presence:*` | → `DeviceSeen`, `ViewingChanged` |
| `StatuslineMapper` | `statusline:update` | §5.5 — `RateLimitReported`, `AccountSeen` |

### 5.2 `inference.py` (claude_code only — earned, not architectural)

The module for **events asserted without the tool saying so** — filling
Claude Code's silences. Every rule is a named, versioned, individually-tested
function from evidence to event. Canonical residents (each a hard-won v1 rule
whose tests get ported, not rewritten from memory):

- **Interrupt**: no hook on Esc. Match the `[Request interrupted by user]`
  *record* (as the content of a `type:"user"` record — never raw bytes: growth
  that merely QUOTES the marker must not trigger), and check what FOLLOWS (a
  queued message delivered on the interrupt means the turn continued).
- **Sid fork**: `--resume` and backgrounding continue the conversation under a
  NEW sid with no SessionStart → the rule detects the fork and hands it to
  `SessionWriter`, which records `SidAliased(lineage, new_sid)` (§5.0.1).
  (Replaces v1's adopt machinery: DB renames, symlinks, pane retags.) The
  backgrounding fork's evidence: the new sid IS the background-job id from the
  Ctrl+B payload.
- **The bespoke CLOSERS (C5 — decided: per-gap rules, NO general liveness
  prober).** `AgentAttentionFold` depends on every `*Started` eventually
  closing, and the tools regularly break that promise. Each known gap gets its
  own named closer rule:
  - *Never-ran commands*: a Bash call denied by permission fires no
    PostToolUse → close as `CommandAborted(why="never-ran")`. v1 lesson
    encoded: do NOT infer "your turn" from it; PostToolBatch is not reliably
    fired and cannot be the only backstop.
  - *Cancelled/abandoned/dead subagents*: killed Task → `meta.json`
    `stoppedByUser`; rejected/abandoned Task → the parent transcript's
    `tool_result` (fires neither SubagentStop nor stoppedByUser); died-on-API-
    error → StopFailure carrying the subagent's `agent_id`. Each closes with
    `AgentFinished(status=…)`.
  - *Wrapped commands*: `wrapper:exited` is the closer; a hook outcome
    corroborates or substitutes.
  - *Standalone codex end*: rollout EOF + host pid death → `SessionEnded`
    (codex fires no session-end event at all).
  - **Accepted residual (decision)**: a gap none of these rules anticipates
    ships broken — a stuck-open item — until its rule is added. This is the
    deliberate trade against a general pid-probing watchdog (rejected as
    reintroducing scattered liveness machinery); the failure mode is visible
    (a stuck "working" card/tab) and the fix is always "add the closer rule".
- All closers are **event/evidence-triggered, never idle timeouts** (v1's
  idle-timeout backstop false-positived on every long think — "quiet" and
  "dead" are different). Cancel-before-first-signal remains deliberately
  unhandled for a terminal Esc. The one sanctioned timeout-ish check is a
  *web-initiated* interrupt's recheck — an event we generated ourselves.

codex/opencode get no `inference.py` initially: their primary sources are
comprehensive journals (rollout / plugin events) with far less silence. One-off
inferences live inline in their mappers; a *body* of rules earns the named
module the day the third rule shows up (the module is really a test-suite
anchor).

### 5.3 `classify.py` — command interpretation

Certain shell commands are *really* file reads (`sed -n '1,120p' f`, `cat f`,
`find -exec cat`) — v1 learned the hard way that this classification must be
shared, not per-presenter (the memory tab recorded NOTHING for a year while
sessions read notes via `cat`). Rules:

- Classification is a **pure function of the command string** for the general
  read-collapse case — trust the text; occasional false positives are cosmetic
  and fixed by tightening the classifier. **One decided exception (Tier-4): the
  memory vault.** `MemoryRecalled(paths)` needs REAL vault paths, and v1's
  measured lesson is that a grammar can neither prove vault membership nor
  resolve a bare `-name x.md` basename — so the memory classifier MAY do a
  bounded, cached filesystem lookup (vault-membership check + name-index
  fallback, v1's `memcmd` semantics). Consequence recorded: memory
  classification is NOT byte-stable under replay (the vault changes); the
  verdict is computed once at map time and baked into the event, exactly like
  every other map-time interpretation.
- The mapper emits **both the truth and the interpretation**, linked by `tid`;
  the interpretation only on success:

```
CommandStarted(tid, cmd="sed -n '1,120p' core/ops.py", interp="read")
CommandFinished(tid, exit=0)
FileRead(path="core/ops.py", extent="1-120", via="sed", from_cmd=tid)   # only if exit==0
```

- A failed read-shaped command (`exit!=0`, e.g. no such file) is **just a
  failed command** — no `FileRead`, renders and counts as a failure.
- Command events are never suppressed (it WAS a command; erasing that would
  make the log lie). **The double-count guard keys on the interpretation's
  SUCCESS, not the pre-exit flag** (review fix): `interp` on `CommandStarted`
  is a rendering hint only; `CommandFinished` carries `interpreted: true`
  exactly when the linked interpretation event was emitted, and
  `CommandStatsFold` skips on THAT — a failed read-shaped command therefore
  counts as a failed command, never falling between the folds.
  `FileStatsFold` picks up the `FileRead`; `MirrorRenderer` sees both events
  share a `tid` and paints the collapsed one-liner (`Read(ops.py) sed` —
  `via` is a field; presenters own wording).
- The same shape carries the family: memory-vault reads (`FileRead` +
  `MemoryRecalled`), `qmd` searches (`MemorySearched(query, hits)` parsed from
  the command's output at map time — from the wrapper stream when present,
  else from the PostToolUse hook payload's tool_response (v1's source),
  which is the named degrade so an unwrapped/sandboxed command still records
  its searches).

Principle: **mappers own interpretation, folds own arithmetic, presenters own
appearance — an interpretation is always an added event linked to its
evidence, never a mutation of it.**

### 5.4 OTEL ingest (gap fill — the AUTHORITATIVE usage source)

v1's measured position, kept: transcript-derived usage MISSES Claude Code's
hidden `auxiliary` agents (summarizer/title runs that fire only SubagentStop,
carry no payload usage and write no transcript — 11.6% of one session's cost).
So the daemon runs an **OTLP receive adapter** (per-machine singleton, HTTP
listener — an input device like the FileWatcher), shipping datapoints as
`otel:metrics` envelopes; an **`OtelMapper`** emits `UsageReported(model,
in, out, read, create, query_source)` where `query_source ∈ main | subagent |
auxiliary` (the v1 taxonomy) and `actor` may be `aux` — a value neither main
nor any agent-id. Two ported rules: **delta temporality** (datapoints are
summed, never treated as gauges — the silent order-of-magnitude error), and
the transcript fold surviving only as a SessionEnd fallback when telemetry
was off.

### 5.5 Statusline, accounts, and relimit migration (gap fill)

**The limits channel.** Claude Code exposes 5h/7d rate-limit windows on
exactly one channel: statusLine stdin. `baqylau-shim statusline` (§4.2) ships
it; the mapper emits `RateLimitReported(account, five_hour, seven_day,
resets_at, model_windows)` and `AccountSeen(slug, label)`.
`AccountUsageFold` maintains `Account(slug)` aggregates (effective window %
with rolled-over-window zeroing, `limit_hit`, `logged_out` with v1's measured
grace, per-model weekly windows — v1's `usage.py` arithmetic ported with its
tests). The dashboard's account pills, limit bars and new-session default
pick read these aggregates; codex fills the same shape from its own usage
source.

**Relimit migration** (v1 docs/relimit.md, whole feature ported):
- Trigger: a main-session StopFailure with `error="rate_limit"` → the mapper
  emits `RateLimitHit(account, scope, resets_at)` — an EVENT, deliberately,
  because the status line freezes at ~95% once requests bounce; scope/reset
  parsed with v1's prose parsers (model-scoped resets from the per-model
  window, the measured false-clear).
- `RelimitPolicy` (Tier-3 policy; reads `Account` aggregates + Clock) picks
  the target account (least-used, v1's downgrade ladder: `fable→opus→sonnet`,
  first rung with headroom wins, never skip a rung) and emits
  `MigrationDecided(lineage, to_account, mode=auto|manual)`; the manual ⇆
  gesture emits the same event via ControlService (no confirm, no ceiling —
  v1 semantics).
- `SessionLauncher` (§7A) executes: close the tab, **wait for `SessionEnded`**
  (the safety interlock against two `--resume` processes on one transcript —
  the positive edge v1 waited on, now an event), relaunch
  `<alias> claude --resume <sid> <nudge>`; lineage continuity (§5.0.1)
  carries the mirror history. `CLAUDE_RELIMIT=0`-equivalent config disables.

### 5.6 Subagent ordering + the tailer discipline (gap fill)

**Ordering truth for a subagent is its TRANSCRIPT, not hook arrival.** v1
tails `subagents/agent-<id>.jsonl` because it is the only in-order source of
prompt/messages/tool_uses/results, and deliberately skips `agent_id` hook
events for rendering (hooks race and mis-order against messages). Kept: the
mapper emits a subagent's events in TRANSCRIPT order; its hook/wrapper
envelopes corroborate (fill exit codes, live output) but never reorder.
Global log position orders across streams; within one agent's story the
transcript is law.

**`obs:transcript` has a defined shape** — the FileWatcher adapter implements
v1's tailer byte-discipline, which `watchfiles` does NOT solve (it only says
"something changed"): byte-offset tailing reading exactly `size - pos`
(an unbounded read loses bytes appended mid-read, which `pos = size` then
DUPLICATES); truncation ⇒ restart at 0; only complete lines shipped (cursor
to last newline, torn tail re-read); NO line-length cap on JSONL (truncation
breaks `json.loads` and silently drops records); watch paths REFRESHED per
event because Claude Code relocates a transcript when the cwd moves projects.
Polling cadence supplements FSEvents (macOS coalesces/delays; the interrupt
record must be seen promptly).

---

## 6. Canonical event vocabulary

All events carry `sid` (the runtime sid, as evidence), `actor`
(main | agent-id), `ts`. Streams are keyed by **lineage** (§5.0.1). Proposed
by mappers, appended only by `SessionWriter`, into `Session(lineage)`
aggregates.

| Group | Events (key fields) |
|---|---|
| Lifecycle | `SessionStarted(cwd, tool, account)` · `SessionEnded(reason)` · `SidAliased(lineage, new_sid)` |
| Branching | `BranchDiscarded(from_pos, to_pos)` — a rewind/checkpoint-restore discarded the transcript branch whose events span those log positions; folds and history queries select the LIVE branch (the event-log generalization of v1's `_boundary_live`) |
| Turns | `TurnStarted` · `TurnEnded` · `TurnInterrupted` · `PromptSubmitted(text)` |
| Commands | `CommandStarted(tid, cmd, kind, wrapped, interp?)` — `kind ∈ fg | bg | monitor | ws-monitor | persistent` (v1 distinguishes all five; one boolean was a review finding) · `CommandOutput(tid, blob)` · `CommandFinished(tid, exit, dur, interpreted?)` · `CommandAborted(tid, why)` |
| Files | `FileRead(path, extent, via?, from_cmd?)` · `FileEdited(path, add, rem, blob_diff)` · `FileWritten(path, blob)` |
| Tools | `ToolInvoked(name, args_blob, result_blob)` · `SkillInvoked(name, args_blob)` — a skill's SKILL.md arrives as a user-shaped turn, so the invocation is its own fact |
| Agents | `AgentSpawned(aid, kind, task)` · `AgentTasked(aid, task_key, task)` / `AgentTaskDone(aid, task_key, status, result_blob)` — the CHILD-TASK model: a child is not always one task (codex follow-ups, teammate re-tasking merged two results into one card in v1), and a task's completion can land AFTER the parent's final answer, so presenters order the answer after the task-end card SEMANTICALLY, not by log position (v1 `childtask.py`, measured) · `AgentFinished(aid, status, result_blob)` |
| Dialogs | `QuestionAsked(qid, options, multi)` · `QuestionAnswered(qid, answer)` · `PlanProposed(blob)` · `PlanDecided(verdict, feedback, edited)` |
| Control | `InterruptRequested(by)` · `InterruptConfirmed` |
| Meta | `UsageReported(model, in, out, read, create, query_source)` — **token counts only, NO cost** (cost is arithmetic over the most-corrected price table in v1; `UsageFold` prices, so a price fix is a rebuild, not a remap) · `TaskListChanged(tasks)` · `TeamTaskChanged(op, n, text)` (the ✚/✓ team-task rows — a different thing from the todo list) · `TitleChanged(title, source)` — source ∈ the v1 five-step ladder (agent-name / ai-title / summary / first-prompt / slash-label) + `renamed-override` · `GoalSet(text)` / `GoalMet` · `CompactionStarted` / `CompactionEnded` · `ContextReported(used, limit, model)` — from the transcript probe, honouring the compact-boundary + reverted-compaction (`_boundary_live`) rules as named inference residents with ported fixtures · `ModelChanged(model, effort, fallback?)` |
| Memory | `MemoryRecalled(paths, via)` · `MemorySearched(kind, query, hits)` |
| Team mail | `TeamMailSent(from_actor, to, blob)` · `TeamMailDelivered/Read(msg_id, recipient)` — INTRA-session agent-team mail, keyed `(msg_id, recipient)` per copy; delivered/read transitions come from the daemon-side INBOX WATCHER (`obs:inbox` — no hook fires on read, and lifecycle frames — idle notices, task assignments, terminations — travel the same inboxes with no SendMessage anywhere; v1's poll-diff semantics ported: `read` = flipped OR disappeared, `stale` after 60s). Deliberately DISTINCT from cross-session Peer mail below — a review finding: two systems were wearing one event name |
| Presence | `DeviceSeen(device)` · `ViewingChanged(device, sid?, viewing)` |
| Cooperation | `PeerMessageSent(from_lineage, to_lineage, blob)` · `PeerMessageDelivered/Read(msg_id)` · `WorkClaimed(path, ttl)` / `WorkReleased(path)` — the cross-session MCP hub (§11.5) |

Evolution rule: **additive only**. New event types must be ignorable by old
consumers — which every consumer needs anyway, since old log events never
disappear. (API compatibility and append-only discipline are the same rule.)

Attribution: `actor` distinguishes the main agent from subagents/teammates/
sidecar runs. View scoping (v1's `src`-stamp machinery, `web=1` overrides,
`in_scope` predicates) becomes a `WHERE actor` filter plus per-presenter
selection policy (e.g. the web's main scope additionally selects
`AgentSpawned/Finished` — a line of query logic in the one consumer that wants
it). No producer-written routing flags exist.

---

## 7. Ports (the abstraction budget: each must earn rent)

| Port | Contract | First adapters |
|---|---|---|
| `Terminal` | window discovery/tagging, panes, tab paint, send-text/keys, `get_text(ansi=)`, focus probes, `capabilities()` | kitty (package), null |
| `AgentControl` | per-tool gestures: interrupt/send/rename/autoname/ask/plan/rewind/compact/model/effort/**migrate**; capability-declared, missing gesture = named 409. Per-gesture v1 semantics ported with fixtures (§17): interrupt's queue-drain stop rule (stop pressing Esc the moment the transcript shows the queue draining — a screen-delta-only verdict killed the queued delivery) + the take-back read; rename's LIVE/PARKED split (live = paste the TUI's own `/rename`, writing nothing — Claude Code re-emits its in-memory name every turn boundary and overwrites foreign records, measured 13×; parked = append the naming record + durable override; codex INVERTS — its TUI emits no OSC title); the **clipboard-image guard** — Claude Code auto-attaches a clipboard image on ANY bracketed paste/argv launch, so hosts declare `paste_grabs_clipboard_image` and ControlService clears an image clipboard before send/launch/ask (text clipboard untouched, `clip` recorded on the effect event); the ghost-suggestion probe (screen-scrape mirror, ephemeral) | claude_code, codex, opencode |
| `AlertChannel` | deliver / retract | webpush, telegram, toast |
| `Clock` | `now()`, `call_at()` | asyncio real, fake for tests |
| `FileWatcher` | register/drop path watches → observation envelopes | watchfiles, polling fallback, fake |
| `ProcessRunner` | spawn/supervise/probe subprocesses | real, fake |
| Storage | the `eventsourcing` recorder + blob store behind `core/spine.py` and `core/blobs.py` | SQLite (one file) |

Deliberately NOT ports: config parsing, logging, ID generation, serialization
(pydantic is a decision, not a seam), the event schema itself. Test: *would a
second implementation change what the core does?* If no, it's a library choice.

Storage-port discipline: the contract is append-ordered-read + get/set, nothing
more. No query language crosses it. A consumer needing `WHERE … GROUP BY` is a
fold materializing into its own aggregate — rich queries live above the port.

## 7A. Session launch (gap fill — a lifecycle port, not a gesture)

v1 CREATES terminal sessions from three places: the web `+ session` form,
"resume & send" on a parked session (the composer message rides the
`--resume` relaunch's argv — "tab exists" ≠ "input ready"), and the relimit
migrator. v2 hosts this in **`SessionLauncher`** — a reactor-with-license
(effects: spawn; effect events: `SessionLaunched/LaunchFailed(gesture_id)`):

- `Terminal` port gains `launch_tab(cwd, argv)` / `close_tab(win)`.
- The argv is the host's `launch_argv`: `$SHELL -lic '<alias> "$@"' …` —
  a LOGIN shell, because a GUI terminal execs with its own env (no user PATH,
  no aliases; v1's measured lesson).
- Guards ported: refuse `--resume` on a lineage with a LIVE session (two
  processes on one transcript corrupt it); gone-transcript → 410; wrong-tool
  guard (`owns_by`-equivalent: never `claude --resume` a codex transcript);
  `--keep-focus` when the terminal is not frontmost (no focus theft).
- API: `POST /api/v1/sessions` `{cwd, account, model, effort, prompt?}` and
  `POST /api/v1/sessions/{lineage}/resume` `{message?}` → 202 + effect events
  over SSE, exactly like gestures (§11.3).

---

## 8. The spine: `eventsourcing` library

Adopted as the **spine, never the skeleton** — everything imports our ports;
only `core/spine.py` imports the library (the walk-away seam: if the
one-maintainer project stalls, the reimplementation surface is one module and
this document is the spec).

What we lean on (verified against its docs):

- **Recorder** (SQLite) as the event store; the **notification log** as the
  global position-ordered view.
- **Aggregates**: `Session(sid)` — sid as aggregate ID gives per-session
  streams natively; derived aggregates for every fold output; global
  aggregates for the corpus-level facts. Data-only style — no
  command-methods-with-invariants ceremony (a session doesn't validate
  business rules; it accumulates facts).
- **Snapshots**: `snapshotting_intervals` per aggregate class;
  `repository.get()` = snapshot + a tail replay of up to interval-1 events
  (NOT O(1) — review fix; intervals are named config, and the hot list read
  is a step-1 benchmark, §19). This is what makes **state-as-events** viable:
  folds don't write to a separate state store; they append to snapshot-backed
  derived aggregates.
- **`ProcessApplication` + tracking records**: state change + upstream
  position committed in one transaction → exactly-once folding; emission dedup
  on replay (the caused-by position is the idempotency key).
- **Runners**: followers are *prompted* on append and *pull* from the
  notification log at their tracked position — the signal-pushes/truth-pulls
  sandwich. A lost prompt only ever means "behind", which self-heals.

Rules layered on top:

- **One aggregate has exactly one writing subscriber** (two writers would fight
  over the version sequence). For session streams this holds **by
  construction**: `SessionWriter` (§5.0) is the one appender and every mapper
  is a proposer. Corollary elsewhere: the global read-model splits into
  `SessionIndex` and `StatsRollup`, one fold each.
- **Thin source-of-truth aggregate, thin derived aggregates.** Do not fold
  attention/counters into `Session` itself — a fat session aggregate re-fuses
  concerns, and every fold bug becomes a session-stream migration instead of a
  disposable derived-aggregate rebuild.
- **Fat-state warning** (global aggregates): a snapshot serializes the whole
  state. `StatsRollup` is naturally bounded (day×hour buckets, per-project
  maps). `SessionIndex` grows with the corpus — keep only what the list page
  shows, prune ended sessions past a horizon, snapshot less frequently.
  Thresholds that flip the decision back to a plain indexed table maintained by
  the same fold: tens of thousands of sessions, or any query needing arbitrary
  predicates over the corpus (FTS over titles, "sessions touching file X").
  The swap is invisible above the QueryService.
- **Update-frequency warning**: the global folds debounce/batch on **event
  timestamps** (never wall clock — determinism), so the global streams stay
  low-frequency and log volume doesn't double with bookkeeping events.
- Known friction, accepted: the library is synchronous — the async edges bridge
  via a thread/`to_thread` (one seam, designed once); serialization goes
  through its transcoders (a thin pydantic adapter).
- Runner IO note: the library's runner has each follower pull notifications
  itself (per-follower reads, not a single-reader dispatcher). At our volume
  over one WAL SQLite this is fine (page-cached). If it ever isn't, the named
  escape hatch is a custom runner that reads once and feeds followers — the
  narrow `interested_in` sets make the routing trivial. Deferred, with a name.

Rebuild vs remap semantics (write these on the wall):

- **Rebuild** (a fold): wipe its aggregates + tracking, replay. Free, routine,
  retroactive — every session ever recorded self-corrects. **Known exception
  (C6, accepted corner case — no machinery for now):** a fold that EMITS
  events (`AgentAttentionFold`) re-emits its whole transition history on
  replay, and downstream consumers (policies, reactors) would receive it as
  news. Do NOT rebuild an emitting fold casually — treat it as surgery
  (quiesce downstream first). Revisit with a durable emission-dedup key if it
  ever bites in practice (§19).
- **Remap** (a mapper bug): re-run the fixed mapper over stored intake. This
  rewrites truth downstream already consumed — a *migration with a decision*
  (append corrections vs shadow log), not a casual replay. Going forward cheap;
  retroactively surgery. Possible at all only because intake persisted the
  evidence.
- **Policies are never rebuilt-and-re-emitted**: their emissions are historical
  decisions (made with a past clock and past presence), not derivable facts.

---

## 9. Subscribers

Vocabulary (final): everything consuming a log from a checkpoint is a
**subscriber**. Three kinds by *output*, each one rule:

> **Folds compute, policies decide, reactors act.**

| Kind | Rule |
|---|---|
| **Fold** | pure function of its subscription — no clock, no reads of other aggregates; event timestamps allowed (they're in the log). Freely rebuildable (emitting-fold exception: §8/C6). |
| **Policy** | reads clock and/or other aggregates — same events replayed later could decide differently. Emissions are history. |
| **Reactor** | effects + **effect events** (C1); at-least-once + idempotent (checkpoint after the effect; tolerate replays). |

### 9.0 The reactor emission license: effect events (C1)

"Reactors emit nothing" was meant to keep the follow graph flat, but it made
every effect's OUTCOME unrecordable — the Telegram `message_id` a retraction
needs, the kitty rc that distinguishes a painted tab from a stranded one, a
gesture's result — quietly falsifying "the logs ARE the audit" for the whole
outbound half. So reactors get exactly one emission right:

> **A reactor may emit events about its own effect and nothing else** — facts
> nobody else could know, describing the effect just performed, into the
> reactor's own aggregate. `AlertDelivered(alert_key, channel, message_id)`,
> `TabPainted(win, state)` / `TabPaintFailed(win, rc)`, `GestureCompleted
> (gesture_id, verdict)`.

Effect events are ordinary log events (same store, positions, consumption).
The restriction is what keeps the graph legible: an effect event describes the
past (this effect happened/failed), never instructs — a reactor reacting to
another reactor's effect events needs the same grade of argument as a new
intermediate fold (§9.4). Consequences now available: `TabReactor` remembers
only **verified** paints (persist-on-rc==0 — v1's `tabpaint.py` rule, kept),
and alert retraction survives a restart because the channel receipt is in the
log, not in process memory.

`ControlService` is hereby placed in the taxonomy: it is a **reactor with the
standard license** — its effect is driving a TUI gesture, its effect events
are the `gesture:*.result` envelopes it appends to intake (mapped to
`GestureCompleted` etc.).

Structural rule: **one subscriber = one output** (one derived aggregate type,
or one emitted event type). `ls daemon/folds/` is the data-lineage map.

### 9.1 Folds (follow `SessionLog` unless noted)

Derived aggregates' own events are mostly a single `Updated(...)`; state is the
product; the canonical log holds the story. One exception, called out.

| Fold | Aggregate (key) | Subscribes to | Notes |
|---|---|---|---|
| `AgentAttentionFold` | `AgentAttention(lineage)` | command/turn/dialog/agent events | tracks `open_cmds` itself from starts/ends. Every start closes via the bespoke CLOSER rules (§5.2, C5) — with the accepted residual that an unanticipated gap sticks open until its rule is added. **Emits `AgentAttentionChanged(state, prev)` — transitions are the product**; the ONE intermediate producer in the graph; NOT casually rebuildable (C6, §8). Named to not clash with human presence. |
| `CommandStatsFold` | `CommandStats(sid)` | `CommandStarted/Finished/Aborted` | skips `interp`-flagged commands |
| `FileStatsFold` | `FileStats(sid)` | `FileRead/Edited/Written` | unique-file set, ±diff |
| `ToolStatsFold` | `ToolStats(sid)` | `ToolInvoked` | per-tool tallies |
| `ActiveTimeFold` | `ActiveTime(sid)` | `SessionStarted/Ended`, `AgentAttentionChanged` | ⏱ pauses while attention is `done` (green = your turn); event-ts arithmetic only |
| `UsageFold` | `Usage(sid)` | `UsageReported` | token split (`tk_in` subtracts cache-creation — the ONE split, encoded once) + cost |
| `TasksFold` | `Tasks(sid)` | `TaskListChanged` | |
| `GoalFold` | `Goal(sid)` | `GoalSet/Met` | |
| `CompactionFold` | `Compaction(sid)` | `CompactionStarted/Ended` | latch; read-side expiry (an interrupted compaction fires no closing signal — animation must fail OFF) |
| `ModelFold` | `ModelState(sid)` | `ModelChanged` | incl. refusal-fallback flag |
| `PresenceFold` | `Presence` (singleton) | `DeviceSeen`, `ViewingChanged` | device map + viewing map; NOT owned by alerting (shared input) |
| `MailboxFold` | `Mailbox(sid)` | `PeerMessage*` | cooperation |
| `ClaimsFold` | `Claims` (singleton) | `WorkClaimed/Released` | active leases; ships only with claims |
| `AccountUsageFold` | `Account(slug)` | `RateLimitReported`, `AccountSeen`, `RateLimitHit` | §5.5 — window %, limit-hit, logged-out grace, per-model weeklies |
| `ContextFold` | `Context(lineage)` | `ContextReported`, `CompactionEnded`, `BranchDiscarded` | ctx saturation for cards/header/agents |
| `TitleFold` | `Title(lineage)` | `TitleChanged` | the five-step ladder resolved; `renamed-override` wins only when the tail rename is empty (v1's 64KB-rollback defence) |
| `TeamMailFold` | `TeamMail(lineage)` | `TeamMailSent/Delivered/Read` | the scorebar ✉ census: cumulative transition counters (survive an inbox drain), stale detection |
| `SessionIndexFold` | `SessionIndex` (global) | `SessionStarted/Ended`, `SidAliased` + `AgentAttentionChanged` (follows `AgentAttentionFold`), event-ts debounced | the list page. **Liveness rule shared with `StatsRollupFold` via one module-level predicate** — two independently-derived liveness answers is exactly v1's measured 13-vs-4 stats bug |
| `StatsRollupFold` | `StatsRollup` (global) | `SessionStarted/Ended`, `UsageReported` (event-ts batched) | the stats page |

**Multi-upstream ordering rule (review fix):** a fold following TWO logs
(`SessionIndexFold`, `ActiveTimeFold`) has no cross-log order guarantee —
tracking is per-upstream, so interleaving is scheduling, not data, and would
differ between live run and rebuild. The declared merge rule: process each
upstream independently and make the fold's arithmetic **order-insensitive
across upstreams** (join on lineage + event ts; tolerate an attention change
for a not-yet-seen session by creating the row). A fold that cannot be made
order-insensitive must follow ONE log.

Cross-fact consistency is loose by construction (independent folds may
momentarily disagree about "now"); shared rules (liveness) live in ONE
predicate module rather than being re-derived per fold (the v1 stats bug);
otherwise recorded as a property, not a bug.

#### 9.1.1 The attention state machine, specified (review fix)

States (v1's full set, not the three-word sketch): `idle · working ·
executing · awaiting-bg · awaiting-command · asking · done · cleared`, with
`working` deliberately merging thinking / non-Bash tools / reply-writing /
compaction (no signal separates them). **Precedence is a rule, not a
computation:** `asking > executing > awaiting-bg > working > done` — in
particular an agent starting paints awaiting-bg EXCEPT red wins (a teammate
starting must not erase the one cue that Claude is blocked on you; v1
measured). **What a finished stream proves** (ported table): bg/monitor ended
→ may flip to done; a SUBAGENT ended → working (green flashed before the
main's own signals repainted); a FOREGROUND command ended → working, NEVER
done (the fg-cancel guess was removed outright in v1). Tab clear on
`SessionEnded` is the `TabReactor`'s job (worked example §16 step 8).

### 9.2 Policies

| Policy | Subscribes to | Reads | Emits |
|---|---|---|---|
| `AlertPolicy` (`policies/alert.py`) | `AgentAttentionChanged`, `ViewingChanged` + Clock | `Presence`, **Prefs (sanctioned read — decided)** | `Alert.Armed(sid, kind, due_ts)` · `Held(why)` · `Dispatched(channel, device)` · `Cancelled(why)` · `Retracted(why)` · `Escalated(channel)` — the arm/settle/hold/retract state machine (v1's measured semantics ported: `asking` = blocked, alert promptly, a look HOLDS not cancels; the ask-region screen-diff hold — typing at the terminal is the one trace answering leaves; `done` = resting state, 20s settle, a look resolves; retraction when the state stops being true; machine-wide device activity retracts nothing; Telegram credentials unconfigured = alert-yes/retract-no degrade). **Prefs decision (Tier-4): mutes, the global alerts switch and composer drafts STAY web-local**; `AlertPolicy` gets a narrow read-only `Prefs` accessor for exactly `{muted(lineage), global_enabled, composing(lineage)}` — a documented exception to log-only inputs, because these are GATES (current-value checks at decision time), not history the log needs |
| `RelimitPolicy` (`policies/relimit.py`) | `RateLimitHit`, `gesture: migrate` + Clock | `Account` aggregates | `MigrationDecided(lineage, to_account, mode)` — §5.5 |
| `ClaimPolicy` (`policies/claims.py`) | `FileEdited/Written` | `Claims` | `ClaimViolated(sid, path, holder_sid)` — ships only with claims; advisory claims WITHOUT a violation detector are worse than none |

### 9.3 Reactors

| Reactor | Consumes | Reads | Effect |
|---|---|---|---|
| `TabReactor` | `AgentAttentionChanged`, `SessionEnded` (clear) | — | `Terminal.set_tab_color`, deduped by last-**verified**-paint (persist only on rc==0 — v1 tabpaint rule); emits `TabPainted/TabPaintFailed` |
| `MirrorRenderer` | command/file/tool/agent/dialog events | `ModelState` (tags) | mirror pane blocks — owns ALL terminal glyphs/colors/wording |
| `ScorebarRenderer` | 1s tick (taxonomy footnote: a tick-driven READER, not a log subscriber — the one component exempt from the checkpoint definition) | `CommandStats/FileStats/ToolStats/ActiveTime/Usage/TeamMail` + the daemon-health ⚠ count | scorebar rows; the ⚠ warning light is PUSH into the terminal you're already staring at (v1 errwatch semantics): daemon operational errors emit `OperationalAnomaly` effect events, the chip shows the count, one mirror line per new anomaly, flood-collapsed |
| `SseBroadcaster` | everything | — | per-connection filtered browser deltas (§11) |
| `AlertDeliverer` (`alerting/delivery.py`) | `Alert.Dispatched/Retracted` | — | `AlertChannel.deliver/retract` — the one owner of HOW an alert is (un)delivered; emits `AlertDelivered(message_id)/DeliveryFailed` so retraction survives a restart |
| `PeerDelivery` | `PeerMessageSent` | recipient's `AgentControl` caps | inbox notice / turn-boundary injection; active TUI paste is human-initiated only |
| `WatchSupervisor` | `SessionStarted/Ended`, `SidAliased`, unwrapped `CommandStarted` | — | `FileWatcher` register/drop — the watch set is derived state, rebuilt from the log on restart. Also owns the MACHINE-WIDE standing watches: codex's two global session dirs (sidecar discovery, §5) and the team-mail inbox dirs (`obs:inbox`). Transcript watch paths refreshed per event (relocation, §5.6) — `SidAliased` subscription is what keeps a resumed session's transcript watched (a fork has no SessionStarted) |

### 9.4 The follow graph (whole system — keep it this flat)

```
Intake ─▶ 5 mappers ─▶ SessionLog ─▶ 15 folds · 2 policies · 7 reactors
                            └─ AgentAttentionFold ─▶ TabReactor · AlertPolicy · SessionIndexFold · SseBroadcaster
```

One sanctioned intermediate. Every additional follows-a-follower hop adds a lag
stage and a rebuild-ordering constraint; a lattice is illegible. New
intermediates need an argument as good as "consumers need ordered transitions,
not current state".

### 9.5 What replaced v1's coordination machinery (for the reviewer's checklist)

- Slot rows / palettes / pid-liveness → gone; the daemon knows what's live;
  palette assignment is `MirrorRenderer`-local.
- Take-once hand-offs (`fg-live`, outcome hand-offs) → mapper correlation
  state.
- `parked()` / DB park+restore / inode revalidation → a parked session is a
  stream that stopped growing; history is the identical query.
- Adopt machinery → `SessionForked` + re-keying in folds.
- `bg-recheck`, escape-recheck, interrupt-watch → `inference.py` closer rules
  (§5.2) + `AgentAttentionFold`.
- Adopt machinery / `sid_chain()` → lineage ids + `SidAliased` (§5.0.1).
- Outbound-effect audit (`tab_transitions.applied`, `telegram-notify` rows,
  `web-*` gesture rows) → reactor effect events (§9.0).
- The audit DB as a parallel write path → the logs ARE the audit; the anomaly
  CLI becomes queries over intake+canonical.
- errwatch ⚠ → daemon structured logs (structlog) + an operational health
  endpoint; swallowed-exception accounting is replaced by "the daemon does not
  swallow: subscriber exceptions are supervised, logged, and leave the
  subscriber behind (visible as checkpoint lag), never silent".

### 9.6 Time

Three uses, three treatments:

1. **Deadlines that produce decisions** (settle windows, escalation): *arms are
   truth, timers are doorbells.* Arming is an event (`Alert.Armed(due_ts)`)
   appended BEFORE any timer exists; the `clock.call_at` timer is ephemeral and
   never persisted — on restart the policy rehydrates open arms (future due →
   re-schedule; past due → fire now, late but never lost). Firing re-checks
   conditions at fire time, then emits with idempotency keyed on the arm.
2. **Presentation ticks** (scorebar ⏱): plain loop timers in reactors; a missed
   repaint is repainted next tick.
3. **Timestamps in logic**: always the event's `ts` — folds stay deterministic;
   history renders identically forever.

With `Clock` injected, every deadline behavior is table-driven-testable in
milliseconds (events at t, presence at t+5, clock→t+20, assert exactly one
`Dispatched`) — v1 validated the same semantics by measuring 46 production
pushes.

---

## 10. Delivery mechanics (inside the daemon)

- The runner prompts followers on append; followers pull from their tracking
  position. Prompts carry no data; **delivery is always a log read** — lost
  prompt, slow consumer, crash, restart all reduce to "behind", which
  self-heals (plus a lazy poll backstop).
- Coalescing is automatic (N appends while busy = one catch-up batch). No
  queue-growth/backpressure protocol needed.
- The in-memory fast path is a **cache of the log, never a channel beside it**:
  it may only deliver committed events, in log order, with positions. The
  moment someone publishes pre-commit or reorders, the recovery story silently
  breaks — this is the load-bearing sentence of the section.
- Async policy: asyncio for the waiting (intake socket, watchers, SSE, timers);
  sync for the working (SQLite via the library, reducers, `kitten @` calls in
  executors). anyio task groups supervise subscriber runners and watcher
  lifecycles — no orphaned bare tasks.

---

## 11. API

Three external surfaces + CLI. One idea does most of the work: **the log's
position vocabulary extends to clients — a browser is just another subscriber
with a checkpoint.**

### 11.1 Read side (FastAPI)

```
GET /api/v1/sessions                          → SessionIndex (sorted, grouped)
GET /api/v1/sessions/{sid}                    → aggregate bundle: AgentAttention,
      CommandStats, FileStats, ToolStats, ActiveTime, Usage, Tasks, Goal,
      Compaction, ModelState, Context, Title, TeamMail, Account + AgentControl
      caps; sid resolves through the lineage alias table (§5.0.1). Read-time
      extras computed in QueryService (NOT folds — they need I/O): git
      branch/worktree/dirty chips (TTL-cached, v1 git_info), grouping by the
      FROZEN start_cwd resolved to its worktree owner
GET /api/v1/sessions/{sid}/events?after=P&types=…&actor=…   → canonical log page
GET /api/v1/stats                             → StatsRollup
GET /api/v1/blobs/{ref}                       → immutable, cache-forever (ETag=ref)
```

- Aggregate reads are lookups (snapshot+tail); handlers are trivially thin over
  `QueryService`.
- The events endpoint IS the log, filtered: mirror backlog, agent scope
  (`actor=`), parked history — one route, different parameters. View modes are
  queries plus rendering; collapsed/expanded and click-to-view are pure UI
  state (which blob refs are currently fetched); parked is not a mode at all.

### 11.2 SSE

```
GET /api/v1/events?after=P&sid=…      (each event's SSE id: = its log position)
```

Client protocol = a subscriber's: REST backlog to position N, subscribe
`after=N`. Reconnect resumes via standard `Last-Event-ID`. Server-side:
`SseBroadcaster` with per-connection filter + bounded queue; overflow → drop
queue, client re-pulls from its position (the same catch-up degrade as
internal subscribers, all the way to the browser). **The registration race is
closed server-side** (review fix): "backlog to N, subscribe after N" leaves a
window between the backlog read and the live registration, so `after=N` is
honoured by the BROKER — register the connection first, then serve it a
log-backed catch-up read from N to the current high-water mark, then splice
into the live feed, deduping by position at the splice. This replaces v1's
delta-vs-snapshot reconciliation. What position-keying does NOT replace
(review fix): the STATIC-ASSET half of v1's boot-id — an open browser holding
cached JS across a redeploy still needs cache-busted asset URLs + an "updated
— refresh" toast; that machinery is kept as-is.

### 11.3 Write side — gestures are asynchronous, honestly

```
POST /api/v1/sessions/{sid}/gestures  {kind, args}
  → 202 {gesture_id} | 409 {missing_capability} | 401
```

The POST appends `gesture:*.requested` and returns. Outcomes arrive as events
on SSE (correlated by `gesture_id`) — driving a TUI takes seconds and can fail
after acceptance; a synchronous 200 would be a lie papered over with polling.
`?wait=3s` is sugar (a server-side subscription with timeout) for curl/scripts,
not a second path. Capability discovery rides the session bundle; UIs grey
buttons with the named missing condition (v1's best pattern, kept).

### 11.4 Cross-cutting

- **Auth (hardened per review)**: one bearer token on everything incl. SSE —
  the tunnel makes this API internet-reachable, and v1's threat model holds:
  **reaching the control plane is RCE on the laptop**. The naive "cookie-set
  for ergonomics" is ambient authority (CSRF against gesture POSTs from any
  open page), so: the cookie, if used, is `Secure; HttpOnly; SameSite=Strict`
  AND every mutating route additionally requires v1's `_post_guard` set —
  JSON content-type (forces preflight), custom header or allowlisted Origin,
  no `Access-Control-Allow-*` ever emitted. The bind stays 127.0.0.1 with NO
  `0.0.0.0` knob (exposure is the proxy's job); a `READONLY` mode kills the
  control plane before any other guard.
- **Schema**: wire types ARE the pydantic event/aggregate structs → JSON Schema
  (`protocol/schemas/`) → generated TS types. The API cannot drift from the
  domain because it has no types of its own.
- **Versioning**: `/v1`, additive-only within it.
- **Consistency**: reads eventually consistent behind folds (tens of ms);
  gesture outcomes via SSE avoid the stale re-GET trap structurally.
- Presence beats, uploads, prefs, drafts, dictation-token: ordinary routes;
  prefs/drafts/uploads are web-local state (`api/weblocal.py`), NOT domain
  events (Tier-4 decision) — with presence as the one deliberate promotion to
  domain (routing needs it), and the narrow `Prefs` read granted to
  `AlertPolicy` (§9.2) as the documented exception.

### 11.6 Frontend audit channel (gap fill — clientlog)

The server only sees requests that ARRIVE; v1's whole "still not closing" bug
class (a control POST the browser tried that never reached a handler) was
invisible until the browser reported its own transport lifecycle. Kept: the
SPA posts `client:*` envelopes via `POST /api/v1/clientlog` — per-gesture
`begin/ok/fail` with timing (auto-logged by the instrumented fetch spine),
SSE up/down transitions, uncaught JS errors, a per-load boot record with
origin — landing in intake like everything else, queryable beside the
server-side story. Close/stop rides plain `fetch`, never `sendBeacon` (v1
measured the tunnel queue-then-drop regression).

### 11.7 Web extensions (gap fill — the seam, not a folder rule)

v1's memory tab is a PLUG-IN: `ext/` packages declaring name/label/tab/badge/
routes/SSE channels, spliced by a registry, with a contract test forbidding
core from importing an extension — adding one edits no core file, and its
POSTs pass the same tier guards as built-ins. v2 keeps that seam in the API
layer (`api/ext/` registry: tab descriptor + route table + SSE channels +
QueryService accessors). §14's folder doctrine is code ORGANIZATION; this is
the extension CONTRACT — they are different things (review finding).

### 11.5 MCP surface (cooperation)

The daemon serves MCP tools to sessions: `sessions.discover(project=…)`,
`sessions.send(sid, blob)`, `sessions.inbox()`, `work.claim(path, ttl)` /
`work.release`. Every call is an ordinary envelope → event. Mediated
hub-topology, never peer-to-peer: observable (traffic in the log),
governable (mutes/quotas/consent are hub policies), tool-agnostic (a Claude
Code session can message a codex session).

Hazards, designed-in:

- **Cross-session prompt injection**: a peer message is untrusted model output
  landing in another model's context. Delivered with provenance framing (treat
  as data; a peer cannot approve actions or grant permissions); never
  auto-executed; never able to answer a pending permission prompt.
- **Runaway loops**: per-session send quotas, thread TTLs; replies don't wake a
  session — they wait for its next natural turn.
- **The human stays the principal**: discovery read-only by default; messaging
  opt-in per session; active TUI delivery human-initiated only.
- Sequencing: discovery + read-only awareness first (fixes the two-sessions-
  one-repo friction with zero injection surface); mailboxes second; claims last
  and only with `ClaimPolicy`.

---

## 12. Alerting slice

`policies/alert.py` decides WHEN/WHETHER (with its policy siblings);
`alerting/` owns the delivery loop:

```
alerting/
├── events.py       Alert aggregate + Armed/Held/Dispatched/Cancelled/Retracted/Escalated
├── delivery.py     AlertDeliverer — HOW an alert is delivered and un-delivered
└── channels/       webpush.py · telegram.py · toast.py   (private to delivery.py)
```

Import rule: `policies/alert.py` emits `alerting.events`; `alerting/` consumes
them; nothing outside `alerting/` may import `alerting.channels` or
`alerting.delivery`. Ported v1 semantics (measured, not re-derived): presence
routing (MRU device pick incl. the reserved `terminal` device; browser wins
ties), zero base delay + per-kind settle (done: 20s knee), hold-don't-cancel
for `asking` on a glance, retraction with kind-dependent seen-reasons,
Telegram escalation, escalate-nothing for stage-1 Telegram.

---

## 13. Terminals

One package per terminal under `daemon/terminals/`; contract-tested against
the `Terminal` port; capability-declared so features degrade per-terminal
(tab colors, hyperlinks, screen read). kitty package layout:

```
terminals/kitty/
├── remote.py     kitten @ / socket RC protocol (timeouts; send-text via STDIN —
│                 never a shell argument nor a kitten escape vector)
├── windows.py    ls-tree walk, user-var tagging (claude_session/claude_mirror),
│                 focus probes (app/tab), ppid-walk socket resolution
├── panes.py      mirror/scorebar split lifecycle
└── screen.py     get-text(ansi=) capture for screen drivers
```

`null/` is the inert stub (headless `claude -p`, daemon-spawned scrubbed-env
sessions): every operation a silent no-op with failure-shaped returns; pane
lifecycle SKIPS when no anchor exists (v1's phantom-session lesson).

Screen-driver discipline (ports of v1's hard-won rules, they do not change):
prefer screen-delta ("is it still changing") over any literal marker; vim
editorMode makes Escape modal (first Esc exits INSERT — interrupt needs a
re-press loop); verify every keypress by re-reading the screen.

---

## 13A. The terminal mirror renderer is a SUBSYSTEM (reflow — C7)

`MirrorRenderer` is not a table row; it is the successor of v1's entire
pane-rendering stack and inherits its defining contract:

**The reflow contract.** A pane has a width and the width changes (resize,
mirror grow/shrink). The renderer keeps an in-memory BLOCK MODEL built from
events (width-independent by construction — semantic events + blobs are even
more width-independent than v1's ops), renders it at the current width, and
**re-renders everything on SIGWINCH**. Everything width-dependent lives here
and only here: wrapping, gutter repetition (with ANSI colour re-assertion per
visual row), rule lengths, full-width code panels, chip fitting.

Sub-contracts ported from v1 (each is a measured rule, not a nicety —
`docs/mirror-pane.md`, `docs/click-to-view.md` are the source inventories and
the golden-ANSI test tier is the parity harness):

- Display-width math over wcwidth (CJK/emoji = 2 cells, ZWJ/VS16 = 0), tab
  expansion BEFORE width math.
- Per-block render caching keyed on immutable event identity (sound only
  because events never mutate), invalidated by width.
- **The click transport**: the pane's ONLY input channel is OSC 8 hyperlinks →
  a custom URL scheme → kitty `open-actions.conf` → a handler process → the
  daemon (a gesture envelope) → the renderer repaints. ⧉ copy links and
  click-to-expand both ride it. This wiring is part of the kitty terminal
  package's install contract (§13), not UI state.
- Click-to-expand on the terminal is therefore NOT "pure UI state" (that is
  true only of the SPA): a toggle triggers a full reflow repaint plus
  **viewport restoration** (re-locate the pre-toggle screen content in the
  repainted scrollback and scroll back to it; v1's locate/probe/corrective-
  scroll machinery is the reference).
- Scorebar tail-truncation priority (the ⚠ warning chip is never shed; Σ
  drops first) and the copy-group model (commands group by `tid`; prose blocks
  need synthetic groups) carry over.

Migration note: this subsystem is ported LAST (§17 step 4) — its parity gate is
golden-ANSI comparison against v1 BEFORE v1's pipeline is deleted, plus the
resize/toggle/click manual checklist.

## 13B. Presentation security: sanitize at the leaf (neutralize — C7)

**The invariant (v1's, generalized to every surface):** raw captured output
must never execute in a rendering context. The mirror repaints history on
every reflow, so an embedded terminal escape sequence executes **again on
every resize, forever** (v1 found this live: a tee'd `@kitty-cmd` DCS scrolled
the pane to the top on every repaint). The web renders the same bytes into the
origin that holds the control-plane credential, where the failure mode is XSS
against an authenticated control plane.

Rules:

- **Every surface sanitizes at render time (the leaf), never at ingestion.**
  Ingestion-scrubbing destroys evidence, cannot retro-protect history from
  sanitizer bugs fixed later, and must guess every consumer's context
  (terminal-safe ≠ HTML-safe). Blobs store the raw truth; renderers defuse it.
- **Terminal**: strip everything except SGR styling and the renderer's OWN
  OSC 8 links; captured output contributes text only.
- **Web**: HTML-escape at the leaf; link schemes allowlisted to http(s) — raw
  output must not be able to mint a `javascript:` href in the dashboard
  origin.
- **`GET /blobs/{ref}` never serves raw bytes as a renderable type into a
  browser**: `Content-Type: text/plain; charset=utf-8` +
  `X-Content-Type-Options: nosniff` (or pre-escaped variants); the SPA fetches
  and renders through its sanitizer, never by pointing the document at the
  blob.
- A new surface inherits this section as an obligation of the Surface
  contract — it is port-level, not per-surface folklore.

---

## 14. File structure

```
baqylau/
├── protocol/
│   ├── envelope-frames.md
│   └── schemas/                     # JSON Schema exported from pydantic → Rust/TS codegen
├── edge/                            # Rust workspace — hot path, logic-free by decree
│   ├── Cargo.toml
│   ├── crates/
│   │   ├── baqylau-proto/           # frame codec + socket client (shared)
│   │   ├── baqylau-exec/
│   │   └── baqylau-shim/
│   └── tests/                       # daemon-killed-mid-command, signal propagation, frame fuzz
├── plugins-ts/
│   └── opencode/                    # the one TS exception (Bun host requirement)
│       ├── package.json
│       └── src/index.ts
├── daemon/                          # Python — ALL change concentrates here
│   ├── core/
│   │   ├── events.py                # canonical vocabulary (pydantic v2) — THE shared language
│   │   ├── envelopes.py
│   │   ├── ports.py                 # ABCs only
│   │   ├── spine.py                 # the ONLY file importing `eventsourcing` (walk-away seam)
│   │   ├── intake.py                # intake application + answerable-kind respond() routing
│   │   └── blobs.py
│   ├── writer.py                    # SessionWriter — the ONE appender (§5.0) + lineage table
│   ├── sources/
│   │   ├── claude_code/  mapper.py · classify.py · inference.py · control.py · statusline.py
│   │   ├── codex/        mapper.py · rollout.py · control.py
│   │   ├── opencode/     mapper.py · control.py
│   │   ├── otel.py                  # OTLP receive adapter + OtelMapper (§5.4)
│   │   ├── gestures.py
│   │   └── presence.py
│   ├── folds/                       # one file = one subscriber = one aggregate
│   │   ├── agent_attention.py · command_stats.py · file_stats.py · tool_stats.py
│   │   ├── active_time.py · usage.py · tasks.py · goal.py · compaction.py
│   │   ├── model_state.py · presence.py · mailbox.py · team_mail.py · claims.py
│   │   ├── account_usage.py · context.py · title.py
│   │   ├── session_index.py · stats_rollup.py
│   ├── policies/
│   │   ├── alert.py                 # AlertPolicy (fake-clock tested)
│   │   ├── relimit.py               # RelimitPolicy (§5.5)
│   │   └── claims.py                # ClaimPolicy (ships only with claims)
│   ├── reactors/
│   │   ├── tab.py · mirror.py · scorebar.py
│   │   ├── sse.py · peer_delivery.py · watch_supervisor.py
│   ├── alerting/
│   │   ├── events.py · delivery.py
│   │   └── channels/  webpush.py · telegram.py · toast.py
│   ├── terminals/
│   │   ├── kitty/    remote.py · windows.py · panes.py · screen.py
│   │   ├── wezterm/                 # (future) a neighbor package
│   │   └── null/
│   ├── adapters/
│   │   ├── clock.py · watchfiles_.py · procrun.py
│   ├── api/                         # FastAPI
│   │   ├── app.py                   # create_app(), DI wiring
│   │   ├── routes/  sessions.py · events.py · gestures.py · stats.py · weblocal.py
│   │   ├── sse.py                   # sse-starlette, position-keyed, Last-Event-ID
│   │   ├── models.py                # thin — wire types are the domain types
│   │   └── auth.py
│   ├── mcp/
│   │   └── server.py
│   ├── web/                         # SPA (TS; types generated from protocol/schemas)
│   │   └── src/ · dist/
│   └── main.py                      # composition root — the ONLY place concretions meet
├── cli/
│   └── baqylau.py                   # start/stop/status · query/debug (audit-CLI successor)
├── tests/
│   ├── contracts/                   # per-port suites × every adapter; frame conformance
│   │                                # driven against the edge binaries + TS plugin
│   ├── folds/                       # table-driven + hypothesis property tests
│   ├── policies/                    # fake-clock scenario tables
│   ├── mappers/                     # envelope fixtures → expected events; classifier tables;
│   │                                # PORTED empirical fixtures from v1's measured bugs
│   └── e2e/                         # real daemon + fake tool → API assertions
├── docs/
│   ├── design.md                    # this document, maintained
│   ├── decisions/                   # ADRs
│   └── runbook.md                   # supervision, rebuild, remap-surgery procedures
├── Makefile                         # build edge, gen schemas/types, test, lint
└── pyproject.toml
```

Structure-enforced rules (pylint plugins, replacing v1's grep-tests with AST
checks): import direction (`core` imports nothing above; tiers import `core`
only; adapters import `core.ports`; `api`/`mcp` import core+query; `main.py`
imports everything); one fold one aggregate; `spine.py` is the only
`eventsourcing` importer; `alerting.channels` private; tool knowledge jailed in
`sources/<tool>/`.

Folder doctrine: tier folders are the default; a feature earns a vertical
folder only when it owns components in ≥3 tiers AND its events have no
consumer outside the slice. Current qualifiers: `alerting/` (delivery side);
a future `cooperation/` slice (mailbox + peer delivery + claims pair) is
pre-approved on the same grounds. `PresenceFold` and `AgentAttentionFold` stay
in `folds/` — shared inputs are not owned by their consumers.

---

## 15. Tech stack

Component-language map: **Rust** — `baqylau-exec`, `baqylau-shim` (hot path,
logic-free, protocol-stable). **TypeScript** — opencode plugin (host
requirement) + the SPA. **Python** — the daemon, where all change concentrates.

| Concern | Library | Note |
|---|---|---|
| HTTP | FastAPI + uvicorn | DI via Depends |
| SSE | sse-starlette | Last-Event-ID + keep-alives done right |
| Types/validation | **pydantic v2** (+ pydantic-settings) | the single type layer: events, envelopes, aggregate state, API models, JSON-Schema export. msgspec dropped — FastAPI is pydantic-native and two schema systems is the drift disease this design exists to kill. Settings replaces the env-knob farm. |
| Async plumbing | anyio | structured concurrency; no orphaned tasks |
| HTTP client/tests | httpx | ASGITransport → in-process API tests |
| Spine | eventsourcing | §8; behind `core/spine.py` |
| File watching | watchfiles | + polling fallback |
| Logging | structlog | bound context (`sid=`, `subscriber=`) — operational log, distinct from the event log |
| Retries | tenacity | channel delivery, kitten RC calls |
| CLI | typer + rich | |
| Tests | pytest + pytest-asyncio + **hypothesis** | property tests over pure folds ("attention never ends `working` with no open commands"; permutation-invariance where claimed) |
| Time in tests | the Clock fake (ours) | deliberately NOT freezegun/time-machine — we designed the seam, use it |
| Packaging | uv + hatchling | lockfile |

Typing/lint: `mypy --strict` from day one (retrofitting strict is the expensive
order) + **pylint** (deep passes in CI, custom plugins for the architecture
rules) + **ruff** (format + fast lint; complement, not rival).

Deliberately not adopting: celery/redis/rabbitmq (the runner + log IS the task
system), SQLAlchemy/alembic (the recorder owns storage; an ORM invites ad-hoc
tables beside the log), DI frameworks (main.py + Depends suffices), APScheduler
(arms-are-truth already solved durable scheduling correctly *for us*; a
scheduler with its own persistence is a competing recovery story),
Kafka-anything (scale cosplay). Meta-rule: adopt libraries for solved generic
problems; never for anything touching the architecture's own guarantees
(delivery, scheduling, storage semantics).

---

## 16. Worked example (the reference flow)

Scenario: a session runs `pytest`; you interrupt from your phone; the tab turns
green; you don't look; 20s later a push fires; the session ends; a week later
you open its history.

1. PreToolUse → shim → envelope 9231 → `ClaudeCodeMapper.respond()` returns the
   `baqylau-exec` rewrite → `CommandStarted(tid, interp=None)`.
2. Wrapper reports `started(pid)` (9235), streams chunks (blobs), Ctrl+B
   backgrounding changes nothing.
3. Phone POST /gestures interrupt → `gesture:interrupt.requested` envelope →
   ControlService drives AgentControl (double-Esc, screen-delta verify) →
   `gesture:interrupt.result` → `InterruptRequested/Confirmed`.
4. Transcript watcher ships the `[Request interrupted]` record (9268); the
   inference rule corroborates and emits `TurnInterrupted` + `CommandAborted`.
5. Runner prompts; `AgentAttentionFold` empties `open_cmds`, concludes green →
   appends `AgentAttentionChanged(done)`.
6. `TabReactor` paints green; `MirrorRenderer` paints the abort footer;
   `SseBroadcaster` updates the phone.
7. `AlertPolicy`: presence says away → `Alert.Armed(due+20s)`; clock fires;
   conditions re-checked → `Alert.Dispatched(webpush)`; `AlertDeliverer`
   delivers. A glance would have emitted `Cancelled(seen)`; post-delivery
   viewing emits `Retracted` and the channel un-delivers.
8. SessionEnd → `SessionEnded`; `TabReactor` CLEARS the tab colour (v1's
   host_end ordering, kept — codex needs the same clear from its rollout-EOF
   closer since it has no SessionEnd hook); the "park" is otherwise nothing:
   a stream stopped growing.
9. A week later: same queries; renderer improvements apply retroactively.
10. A fold bug found: wipe + replay; every recorded session self-corrects.

---

## 17. Migration strategy (strangler fig — the empirical knowledge must not be
ported from memory)

The classic rewrite failure is rediscovering v1's measured edge cases one
production bug at a time. Sequence:

1. **Daemon + logs first.** Stand up intake + spine + a `ClaudeCodeMapper`
   subset. Wire v1's existing hooks to ALSO post envelopes (dual-emit); v1
   remains the production system. **Entry criterion (review fix): the two
   volume benchmarks pass first** — tab-paint end-to-end latency under the
   full chain (<100ms target) and a synthetic build-log stream at wrapper
   chunk cadence (§18's "needs a benchmark" items gate step 1, not the
   retrospective).
2. **Web reads events.** Port the dashboard read side onto QueryService/SSE
   while the terminal mirror still runs on v1 ops. Compare against v1
   daily-driving — knowing (review fix) that command output is hook-grade
   here (no wrapper yet), so the comparison gate covers attention/counters/
   metadata, NOT output fidelity.
3. **Wrapper + folds + policies.** `baqylau-exec` and v1's tee CANNOT both
   hold the single `updatedInput` rewrite slot (review fix), so injection
   hands off PER SESSION: a config flag makes v1's `cmd-pre` stand down for
   sessions v2 claims, and the claim is per-session so a bad day rolls back
   by flipping the flag. Port the alerting state machine with its measured
   tables as tests.
4. **Terminal surfaces last** (mirror, scorebar, tab). **Parity harness
   (review fix): golden-ANSI files rendered by both pipelines from the same
   session, diffed — BEFORE v1's pipeline is deleted** — plus the
   resize/toggle/click manual checklist (§13A) and a DOM-executing test tier
   for the SPA (v1's jsdom lesson: three JS fixes were "verified" clean while
   the screen was unchanged). Only then delete the v1 ops pipeline, slots,
   adopt machinery, audit write path.
5. Every inference rule and classifier lands with a fixture ported from v1's
   test suite / measured sessions — verified continuously against the running
   old system, not trusted from memory. The screen-driver micro-fixtures are
   named deliverables, not folklore: rewind confirm by parsed LABEL never
   position; the dated marker drifts (`to continue` case-insensitive; the
   padded whitespace-tolerant `Rewind` anchor); ask-dialog Escape DECLINES
   (a failed step must leave the dialog untouched) vs codex Escape ABORTS THE
   TURN (never Esc-close a codex dialog); the digit-inert cursor+Enter ask
   key model; confirm-dialog detected by SHAPE not header; the open-check
   POLLS (a single capture bailed on a dialog still rendering after
   `--resume`).

Gate for each step: v1 and v2 agree on the observable outputs for the same
live traffic (attention states, counters, alert decisions), for days, before
v1's half is retired. **Rollback story (review fix):** every step's v1 half is
disabled by flag, not deleted, until the NEXT step's gate passes — deletion
always lags one gate, so a failed gate rolls back by flag-flip; two renderers
never paint one pane because the mirror-pane claim (step 3's per-session flag)
is the arbiter of which system owns a session's pane.

---

## 18. Tradeoff ledger (the commitments, stated plainly)

| We gain | We pay |
|---|---|
| One source of truth; audit = the system; provenance-by-evidence (intake) | **A supervised daemon as single point of failure.** v1 degraded piecewise. Mitigation: supervision + auto-restart; edge components never block execution; the biggest single commitment. |
| Derived state disposable (rebuild); evidence permanent (remap possible) | Two logs to govern: intake TTL; remap of history is surgery, not replay. |
| New tool/terminal/surface = one adapter; view modes = queries | More upfront structure: ports, contract tests, fakes; a build system (Rust edge) in a repo that had none; ~2–3× the engineering of "patch v1". Pays only if the extension axes get used. |
| Testability: folds pure, policies fake-clocked, no sleeps, no live kitty | Latency chain envelope→map→append→prompt→fold→paint; each hop sub-ms locally, but tab color must stay <100ms — **needs a benchmark, not faith**. |
| Positions: loss-free, restart-proof, late-joiner-proof consumption — extended to browsers | Eventual consistency between views (tens of ms); a property to understand, not a bug. |
| Presentation fully surface-owned (no flag pile) | Some duplication returns: two presenters word `CommandFinished` independently; shared *classifications* must be pushed into the schema deliberately (the `act`/`mem` lesson) or they drift. |
| The empirical tool knowledge concentrated in named, versioned, tested rules | **It does not shrink.** Interrupt inference, vim Escapes, sid forks are as hard as ever — located and evidenced, not smaller. A rewrite re-risks each until its test is ported. |
| Rust edge: ~2ms per-call overhead, protocol-stable | Compiled artifacts, cargo in CI, recompile-to-change shims (deliberate: shims must never change). |
| eventsourcing library: tracking/snapshots/runner shipped, not built | Sync library under async daemon (one bridge seam); its transcoders (thin pydantic adapter); bus-factor-one wrapped behind `spine.py` with this doc as the reimplementation spec. |

Meta-tradeoff: every *subsequent* change becomes local to one mapper, fold, or
presenter — a good trade iff the project keeps growing along the axes made
cheap (more tools, more surfaces, more views). If it is actually
feature-complete, the honest answer remains: don't rewrite.

---

## 19. Deferred / open items (named, with triggers)

- Custom single-reader runner — trigger: the step-1 volume benchmark, then
  measured notification-scan cost in production.
- `SessionIndex` → plain indexed table — trigger: tens of thousands of
  sessions, arbitrary-predicate queries (FTS), or measured snapshot-WRITE cost
  (a global snapshot serializes the whole state on the most-frequently-updated
  aggregate — the swap trigger is probably earlier than corpus size suggests).
- Provenance table — trigger: the first painful mapper-debugging session
  (dropped for now; the audit `decision`-string property moves to structlog
  until then — a known debugging regression, accepted).
- codex `inference.py` — trigger: its third no-signal rule.
- Zombie-session detection (host process died, no SessionEnd — the one gap
  the C5 bespoke closers do not cover) — trigger: the eternally-working card
  annoys in practice; the fix shape is one more closer rule (read-time pid
  check), not a watchdog.
- Emitting-fold rebuild dedup (C6 — accepted corner case): trigger: the first
  time an `AgentAttentionFold` rebuild is actually needed; until then the §8
  warning (treat as surgery, quiesce downstream) is the whole mechanism.
- `cooperation/` vertical slice — trigger: shipping mailboxes+claims; NEW
  SCOPE, explicitly off the migration's critical path (parity first).
- Sandbox interference with the wrapper's socket — **measure during step 3 of
  the migration**, before wrapper-grade fidelity is assumed.
- Rust vs Python for the wrapper's first cut — decided Rust; if cargo friction
  ever dominates, the protocol is the contract and a Python fallback is legal.
- Snapshot read cost is snapshot + up-to-interval tail replay, NOT O(1) —
  intervals per aggregate are named config; benchmark the hot `/sessions`
  list read alongside the step-1 benchmarks.
