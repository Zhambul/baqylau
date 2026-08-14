# v4 design review — legacy coverage audit + architecture critique (Claude)

Status: **REVIEW INPUT for the v5 decision process** (`rewrite-design-v5-decisions.md`)

Date: 2026-08-05

Subject: `docs/rewrite-design-v4-codex.md` (read in full), audited against the
LIVE legacy implementation — not against the earlier design docs. Method: six
parallel auditors, one per subsystem area (terminal cockpit · dashboard read
side · control plane · attention/presence/alerts · audit/accounting · session
lifecycle/hooks/recovery), each reading the whole v4 doc plus the legacy code
and docs/ for its area first-hand. Findings were deduplicated and consolidated
here; v4's own self-declared gaps (§0.3, §18.3, §28) are **not** re-reported.

The user asked for three things, and the document is organized as three parts:

1. **Part 1** — legacy features v4 does not cover at all (v4 must be a
   superset of the legacy system; these are superset violations).
2. **Part 2** — features v4 *does* describe, but incompletely or in
   contradiction with measured legacy behavior (missing fields, missing
   states, rules that reintroduce fixed bugs).
3. **Part 3** — my own review: overall verdict, architecture critique,
   performance critique, and where the design is over-complicated for no
   reason.

Severity vocabulary: **CRITICAL** = reintroduces a fixed bug or makes a
shipped feature unbuildable as specified · **MAJOR** = a feature or
load-bearing decision is lost / left for the implementor to invent ·
**MINOR** = real but small or arguably presentation-tier.

A calibration note, so the lists below are read fairly: the audit also found
that **large parts of v4 are faithful, sometimes near-verbatim, ports of the
hardest legacy lessons** — §11's "never rewrite a command unless capture is
certain", §12.3's closer catalogue, §12.5's ban on silence-as-success, §17.2's
verified-receipt paint dedup, §19.1's attention rules (fg completion → working
never done; child events never drive host attention), §8.5's terminal-draft
asymmetry ("unreadable is not empty"), §8.7's ledger separation, §15.4's
Ctrl+B ownership transfer, §21.5's OTLP contract (which is in one respect
*stronger* than legacy), §13.3's lossy-sampler branch class (team mail's
poller), §18.4's whole-block backward pagination, and §7.4's
turn/task/actor-key distinctness (a verbatim port of `core/childtask.py`).
The gaps below are the residue after all of that was checked and excluded.

---

## Part 1 — Legacy features with NO owner anywhere in v4

### Core model

**1.1 CRITICAL — A child agent's own conversation has no home in the Node
tree.** The dashboard's agent scope (`#/s/<sid>/a/<aid>/<tab>`,
docs/dashboard.md *Agent scope*) renders a subagent/teammate/codex-sidecar's
prose as ordinary conversation bubbles read from that agent's OWN transcript
(`subagents/agent-<id>.jsonl`). v4 gives `nodes.actor_key` and then forecloses
every use of it: a committed Node belongs to exactly one Conversation, one
active head, semantic views are read-filtered by head ancestry (§7, §7.1,
§13.3), and two AgentSessions appending from one head are "explicit
divergence". Under those rules, N concurrent subagents are N divergent
branches of which only one holds the head — a child's messages are invisible
in the lead view AND unreachable in a scoped view, and §10.4 maps only the
child's *lifecycle* ("agent_task Operations and AgentSession links"), never
its semantic messages. The implementor must invent a per-actor head, a
child-conversation relation, or something else — in the core model, which
§0.2 forbids. This is the single most important finding in this review.

**1.2 MAJOR — Current context occupancy has no owner outside compaction.**
The ctx-saturation bars (every session card, session header, every agent
card) come from `transcript.context_probe` + `model.context_used` — a
*last-assistant-record* fact, with recorded reasons why OTEL, the state DB,
and the status line were all rejected as sources. In v4,
`context_window`/`context_used` exist only on `context_checkpoints` (§8.2),
whose stated purpose is compaction/summaries. A Conversation that never
compacted has no context fact anywhere, and §8.7's `usage_facts` has no
"which row is current occupancy" rule.

**1.3 MAJOR — None of v4's own named branch-sensitive facets has a
projection contract.** §13.3 names "goal, task state, title inputs, context,
plan"; §18.2 requires every projection to declare owner/source/rebuild
scope/semantics; none of them is declared anywhere. Concretely unowned: the
goal card (`transcript.goal_probe` — NO hook fires for `/goal`; tail-scan for
the last `goal_status` attachment; amber→green ✓) and the pinned tasks card,
whose defining constraint is that Claude Code's on-disk task dir is **wiped at
session end** and a status flip fires **no dedicated hook** — so the snapshot
must be captured on every task-touching hook *before the source disappears*.
§12.4's prober list has no provider-task-directory reader.

**1.4 MAJOR — Provider-initiated mid-session model fallback.** Claude Code
writes a `model_refusal_fallback` system record ONCE, mid-file, no hook, when
a safeguard refusal reroutes the session (measured: fable-5 → opus-4.8); the
⚠ on the ✦ model button retires itself when the ctx probe's current model
stops equalling `to`. §0.3.3 self-declares requested-vs-effective gaps for
start/resume/fork/migration — this is none of those: the provider unilaterally
changed the effective model mid-conversation, and v4 has no fact, state, or
surface for it. (It also requires a forward-scan-from-checkpoint read shape —
the record is written once mid-file, where every bounded tail probe misses
it.)

### Terminal cockpit

**1.5 MAJOR — Pane user controls and focus-derived session resolution.**
`claude-split.py toggle|grow|shrink|reset|setpct` — five user gestures on a
kitty keybinding that arrive with **no session payload** and a stripped env,
and must resolve the session from the focused tab's `claude_session` var.
§17.3's control list, §18.3's routes, and §20.3 contain no pane control and
nothing anywhere resolves a Conversation from terminal focus.

**1.6 MAJOR — The content render-kind decision (markdown/JSON/YAML/source/
fence-sniff) has no owner and no field.** Legacy's `tools.RENDER_KINDS` is a
single-owner registry consulted by both hook planes and the web presenter,
with the measured rule that detection runs from the RAW pre-tee command in
the presenting process (per-launcher env assembly silently lost colouring for
subagent fg reads). v4 §20.4 pushes rendering entirely to surfaces — the
per-surface re-derivation legacy rejected — and the §7.5 `streams` record has
**no `media_type`/language column** (while `node_parts` and `resources` both
have one). A command-output Stream cannot say "these bytes are markdown".

**1.7 MINOR — Escape-sequence *unescaping*** (`^[`, `\033`, `\x1b`, `\e`,
`<ESC>` printed as text, restored to real ESC at the producer — the deliberate
counterpart of paint-time `neutralize()`). §20.5 specifies only the
neutralize half; read alone, it forbids the transformation.

**1.8 MINOR — The scoreboard pane's attention-gated active-time clock.** The
`⏱` counts active time, pausing while the tab is green. §13.3 classifies
elapsed time as plainly Cumulative and no projection derives an
attention-gated accumulator (derivable from `attention_transitions`, but
nobody is told to).

**1.9 MINOR — Palette-slot reclaim.** Legacy slot rows carry the owner pid,
are liveness-checked, and stale ones are stolen. §8.6's `slot_allocations`
has no owner/liveness/reclaim rule, so a crashed producer holds its slot
forever; v4 states the right liveness primitive (§12.4 EPERM=alive) but never
connects it to slots.

### Hooks / lifecycle / providers

**1.10 CRITICAL — Delegating hooks: SUBSCRIBING to an event is itself a
destructive act.** `WorktreeCreate`/`WorktreeRemove` must stay UNWIRED:
registering one tells Claude Code "I will create the worktree" and must print
the path; the dispatcher's silent exit-0 broke every `EnterWorktree` on the
machine (live, 2026-07-15). v4's architecture pushes toward one universal
edge subscribing to everything, and no section classifies event families as
observational vs delegating/blocking. Needs a per-provider **subscription
manifest**, with delegating families excluded by contract.

**1.11 MAJOR — Provider-side edge installation, upgrade, revert, and hook
TRUST.** The settings.json hook table, the statusLine prepend-with-`.bak`,
codex's `hooks.json` — and sharpest: codex's **hash-keyed hook trust**
(editing the hook re-triggers a TUI review prompt; until a human re-trusts
it, all codex ingestion is silently blind). §26.3's supervisor contract
covers the daemon only; §10.5's provider checklist never covers the edge
written into the *provider's* config. A daemon upgrade that touches the edge
script indefinitely de-registers the edge with no evidence.

**1.12 MAJOR — Mid-session artifact relocation, and the frozen grouping
key.** Claude Code RELOCATES the transcript when the session enters a
worktree (measured via `EnterWorktree`); legacy re-stamps
`transcript_path`/cwd on every event (`A.session_paths`), *skipping
`agent_id` events* (a child's worktree cwd must not flap the host row), while
`sessions.start_cwd` is stamped once and NEVER re-stamped so a mid-session
`cd` can't move a card between project groups — two facts from the same
input with opposite mutability. v4: `source_ref` and `project_ref` have no
mutability rules, "worktree" appears zero times, §15.4 covers in-place
replacement but not a move, and `conversation_workspaces.role` is a handover
enum, not "which main checkout owns this worktree". A v4 source reader holds
a dead path cursor and silently stops following the session.

**1.13 MAJOR — The relimit auto-continue nudge and the manual-migrate
plane.** The migration saga (§21.4) ends at "activate" and never re-drives
the interrupted turn — legacy delivers `relimit.NUDGE` in the relaunch argv
(auto path only), which is what makes migration *transparent*. And the manual
⇆ migrate deliberately differs four ways (no % ceiling — "an explicit click
outranks the refuge rule"; no nudge; migrator-side announce; and it launches
over a LIVE state DB when the window is gone — the ONLY recovery for a
logged-out session, whose `authentication_failed` fires no SessionEnd, so
§21.4 step 6 "wait for verified park/end" deadlocks there). v4 has one path
and no user-override concept.

**1.14 MINOR — Non-answerable edge latency has no gate.** §27.3 measures the
answerable lane only. Legacy has hard numbers on the fire-and-forget lane
(pyenv shim ~140 ms/process; one PostToolUse used to fan to 5+ processes;
the dispatcher's lazy imports cut a tab-only event 69→17 ms).

### Dashboard / web

**1.15 MAJOR — No import path for the legacy parked-mirror corpus.**
237 parked state DBs / ~160k ops are, per docs/sessionapi.md's fidelity
ladder, the ONLY surviving record of bg-job/monitor output for parked
sessions. They are neither "provider artifacts" (§16.1) nor v4 canonical
rows; §31 Phase 2 demands "live and parked history parity" with no assigned
source, importer, or explicit drop.

**1.16 MAJOR — Grouping and hidden directories.** `group_dir(start_cwd)`
resolves a linked worktree to its owning main checkout so agent fan-outs stay
one group; empty-key sessions are dropped from every overview but still open
by direct link; a hide is a *timestamp* (auto-expiring re-appear predicate,
no unhide button) refused while the dir has a live session. v4's
`preferences` can store it, but the derivation, freeze, worktree-owner
resolution, expiry semantics and liveness guard are all unowned.

**1.17 MAJOR — The provider slash-command vocabulary** (curated CLI builtins
+ the `.claude/commands`/`skills` walk, host-scoped never concatenated, an
empty menu for a host with no vocabulary). None of §10.1's nine provider
protocols enumerates a command vocabulary; "slash" appears zero times.

**1.18 MINOR — Stats/Insights has no canonical rollup to compute from.**
§16.4 forbids recomputing user-visible cumulative counts from prunable
evidence, yet v4 defines no per-Conversation cumulative rollup
(tokens/cost/errors/day) — so the stats page must read the very evidence
tables §16.4 says may be pruned.

**1.19 MINOR — The web-extension SURFACE contract** (the `dashboard/ext`
registry: badge woven into the same table as the payload so SSE and payload
cannot disagree; the producer half registering an observer inside a
*provider* adapter). §24.1 lists "surface contributions" with no schema, and
§24.2's rules forbid the cross-module producer-half without naming the
service that would allow it.

### Control plane

**1.20 CRITICAL — The exclusive input channel.** An open interaction
captures the session's ONE physical input: a message pasted during an
ask/plan dialog goes INTO the dialog and is lost (measured 2026-07-19); an
Esc there DECLINES the dialog and killed an answer mid-typing (2026-07-20);
digits pasted on a red tab *decide* the dialog. Legacy blocks send / quick
commands / interrupt / rewind while an interaction is open. v4 models
interactions (§8.4) and controls (§17.3) as independent Operations, and
§17.4's reachability inputs (capabilities, config, window state, refusal
floors) do not include an open interaction. Both failure modes destroy user
input irreversibly.

**1.21 MAJOR — Baqylau-owned occupancy of the provider's input box.** The
`tui-draft` record ("the web KNOWS this text is sitting in the `❯` box
because the web put it there" — interrupt take-back, rewind restore),
consumed by the next send, with a per-LINE clear extent (a multi-line
take-back needs N kills; measured session 8b9f870b). §8.5 covers surface
drafts and *observed* terminal drafts thoroughly, but has no representation
of provider-side input occupancy *authored by the daemon* — and the next
`message_delivery` is incorrect without it (`testingtesting2`, 2026-07-25).

**1.22 MAJOR — Provisional retraction of a committed prompt (the take-back
flag).** A taken-back prompt is orphaned in the transcript but has NO sibling
until a replacement is sent — which may be never. Legacy stashes an advisory,
self-correcting suspect flag (dropped the moment anything descends from the
prompt). v4's only correction mechanism is supersession by a later sibling
(§13.2) — evidence that does not exist yet — so a faithful v4 implementation
re-displays a prompt the provider already discarded, indefinitely.

**1.23 MAJOR — Surface-side control telemetry (clientlog).** The
`<gesture>.begin/.ok/.fail` spine is the only evidence a control never
ARRIVED (a tunnel-dropped `/stop` = `close.begin` with no pair — the whole
"still not closing" bug class), and it is what proved `sendBeacon` regressed
close. v4 instruments only the daemon→provider leg; §16.4 assigns a
retention class to "surface telemetry" that no section requires anyone to
emit.

**1.24 MAJOR — The escape-recheck: self-caused evidence.** v4's Law 11 and
§12.5 read as a blanket ban on silence-derived success — correct in general
(the banned idle-watch false-positived on every long think), but legacy's ONE
sanctioned exception is licensed by a different epistemic category: a WEB
interrupt is an event *we generated*, so a bounded recheck keyed to that
press honours events-never-timeouts. Includes two guard rules v4 has nowhere
to put: baseline captured BEFORE the key lands, and NO recheck when the turn
refused to stop (green would mask a live turn). Without the carve-out, a
mid-think web interrupt leaves the tab stuck on the working colour forever
(no hook fires on cancel).

**1.25 MINOR — Ghost-suggestion acceptance semantics** (client-side accept,
nothing typed back to the TUI; push gated on settled tab + no modal + empty
draft). §12.1 classifies the suggestion live-only but never says what
accepting one *is*; the wrong guess turns a read-only mirror into a write
into the user's input box.

### Attention / alerts

**1.26 MAJOR — The in-page toast channel and the focused-device premise.**
The `notify` SSE broadcast + the page's own visibility gate is the stated
PREMISE of machine-wide device-active suppression ("a focused page already
toasted you"). v4 keeps the conclusion (§19.2 "device active") without the
premise or the channel, and the global toggle must gate toast AND arm at one
site — inexpressible with only external deliveries.

**1.27 MAJOR — Push-subscription registry and channel identity keys.**
Durable per-device subscriptions, subscribe/unsubscribe endpoints, pruning on
a 404/410 send, and a persisted VAPID keypair whose rotation silently
orphans every subscription. §8.8 has `device_id`/`channel_id` but no
subscription table, no registration endpoint, and no key-stability rule.

**1.28 MAJOR — The externally-reachable alert origin.**
`CLAUDE_DASH_PUBLIC_URL`, and the deep link as a `?s=<sid>` QUERY param
because Telegram's auto-linker drops URL fragments. §25.2's loopback default
is exactly the value that makes an alert useless on a phone; v4 has no owner
for the URL a notification points at — an implementor ships 127.0.0.1 links.

**1.29 MINOR** — the resolve-push silent:visible budget (iOS
`userVisibleOnly` bending, `sweepStale` fallback); per-alert route-decision
evidence (the `notify-route` row with every candidate + presence age — v4
demands exactly this of the *account* selector, §21.3, and nothing of device
routing); the reserved `terminal` device id and its impersonation refusal;
the retractable:False record for the fire-and-forget Telegram fallback.

### Audit / accounting

**1.30 MAJOR — The anomalies catalogue as a registered, tested artifact.**
~45 canned queries, each with a why-comment naming the bug it detects, plus
the standing rule that a new failure signature extends the list in the same
commit, plus the measured `json_valid` guard (triage tooling must never die
on its own evidence — a truncated row once aborted the whole `anomalies`
run). v4 mentions "anomalies" as a CLI noun and a table; no signature
definition, owner, registration rule, or test requirement.

**1.31 MAJOR — The triage playbook artifacts** (`audit-debug` /
`global-errors` skills; the schema-table + bug-shapes playbook that must move
together because they drifted once and the skill "triaged blind"). No owner,
no location in §29's layout.

**1.32 MINOR** — the `otel <sid>` cost diagnostic missing from §9.8's CLI
list; the `CLAUDE_AUDIT=0` / `CLAUDE_RELIMIT=0` kill switches (the latter
two-state: the limit-hit stamp still writes, only migration is suppressed) —
§31 Phase 0 requires every env gate mapped to an owner or an explicit drop,
and none are; and the audit spool's "never lose evidence" guarantee, which
§9.7 explicitly reverses (defensible with a daemon, but the consequence —
outage-window Operations become lost/unknown — belongs in the Phase 0
inventory as a decision, not a silence).

---

## Part 2 — Features v4 describes, with gaps or contradictions

### Closers, correlation, recovery (§11–§12)

**2.1 CRITICAL — The closer catalogue has no entry for a closer that matches
NO opener, nor for an ACK wearing the closer's identity.** Two measured
shapes: (a) Claude Code's hidden auxiliary agents fire `SubagentStop` with no
`SubagentStart` and no on-disk transcript (~$14 of invisible spend on the
session that exposed it) — an unmatched closer must *materialize* an
Operation with honest origin, not be dropped, and the legacy discriminator
(only a real start sets a slot) shows "no opener ever" ≠ "already closed";
(b) an async agent's Task resolves its parent `tool_result` immediately with
a synthetic *"Async agent launched successfully"* ack at the very
`toolUseId` the real completion later uses — identity matches, semantics
don't ("async launch-ack ended the substream early": the agent's entire
block never rendered). v4 has exactly this acceptance-vs-completion ladder
for *outbound* sends (§17.5) and never generalizes it to inbound closers;
§12.2's "validates identity" cannot catch a semantically-wrong,
identity-correct ack.

**2.2 MAJOR — No law that provider markers are matched as parsed RECORDS,
never raw substrings.** Both hosts independently needed it: the interrupt
marker must be the content of a `type:"user"` record because this repo's own
CLAUDE.md *documents the marker* and a `nested_memory` attachment injecting
it flipped the tab green mid-turn three times in one session; codex's
`turn_aborted` carries the identical invariant. Second half of the same
class: an effect's own byproducts must be excluded from the evidence that
reconciles that effect (the cancel gesture's `ai-title`/`last-prompt`
appends false-positived a raw-growth bail). v4 has the torn-tail half
(§15.4) and fixtures (§30.2), but no such rule in §12 and no law in §33.

**2.3 MAJOR — The synchronous transform must be invertible, blind-applicable,
and inverted at the ingestion boundary.** `updatedInput` REPLACES the
command, so every later consumer — PostToolUse payload, the stored audit
payload, every mapper — sees the wrapped text. Legacy owns
`tee_wrap`/`unwrap_tee` as a pair; the measured cost of not knowing was
silent and total (the memory Bash plane found 2 of 10 reads, 0 of 2 searches,
nothing errored). §11 names `PreparedTransform` and correctly says the stored
answer isn't proof of use (§11.2.9), but never requires an inverse or its
application before consumers read the command. v4 makes this *worse* than
legacy because it multiplies command-text consumers.

**2.4 MAJOR — §11.2.6 trusts a reconciling closer that measurably does not
always fire.** PostToolBatch: for one blocked call Claude Code fired no
batch hook at all, and a parallel batch clobbers the single-key hand-off so a
second orphan is unreachable either way — which is why legacy retired the
inference entirely (`bg-recheck(fg)` clears to WORKING, never green). §19.1
encodes the safe default, but §11/§12 never state the connecting rule: a
registered closer may itself not fire, so the verdict in its absence must be
independently safe.

**2.5 MAJOR — sid-fork adoption lacks the negative start-evidence rule and
the arrival-ordering constraint.** The decisive legacy guard is a
*disqualifier* — a sid with its own start evidence may never adopt — and the
mark must be set on `InstructionsLoaded` too, because it precedes
SessionStart for a real session and is NEVER emitted by a fork; missing that
TOCTOU let a new session consume a concurrent same-cwd session's note and
steal its panes (live, 2026-07-13). The fork's first event can also arrive
*before* the predecessor's SessionStart, so the rule cannot be "later
wins". §13.1 lists positive evidence only.

**2.6 MAJOR — The environment snapshot has no scrubbed-continuation rule.**
A forked sid has no session-start frame at all and its events arrive with a
scrubbed env (no `KITTY_WINDOW_ID`). Two rules an implementor must invent:
a continuation attempt INHERITS the predecessor attempt's snapshot rather
than overwriting it with an empty one, and absent ≠ empty (unknown, not
"not in a terminal") — §10.3 never gives the snapshot missing-value
semantics, violating §0.2's own no-optimistic-collapse gate.

**2.7 MAJOR — Per-Observation fan-out isolation is lost.** One hook event
feeds 2–5 independent concerns (tab, formatter, path re-stamp, evidence row);
legacy's dispatcher was consolidated from separate processes *specifically
without losing* per-step crash isolation. §9.4's single `CanonicalBatch` in
one transaction + §9.6's whole-Observation quarantine means one buggy mapping
rule takes the attention transition and the evidence row down with it. In
genuine tension with atomicity — needs an explicit decision, not silence.

**2.8 MAJOR — No per-tool-family authority table between hook and transcript
evidence.** §14.2 orders by provider source position, but a hook event has no
position inside the child transcript, so duplicate views of the same work
fall to arrival order — the exact race legacy eliminated, and the
arbitration FLIPS per family (fg/bg commands: transcript wins, hook only
prepares; monitors: hook wins, substream deliberately paints nothing;
SendMessage: hook wins and deliberately handles `agent_id` events). v4
declares final authority per Stream kind (§7.5, Law 17) and has no analogue
for Operation/Node facts.

**2.9 MINOR** — "interruption and queued-delivery cancellation" (§12.3)
names the closer but not the successor-evidence rule both hosts needed
(settle one tick, check what FOLLOWS the marker: a queued prompt's user
record right after means keep watching, not done); §12.7's park sequence
assumes writers stop, but bg jobs/monitors outlive the host — under a
machine-global daemon "keep ingesting into a parked Conversation vs abort
with a named reason" becomes a real product decision v4 doesn't make; §19.1
never states attention recomputes from POST-transaction state (the legacy
release-slots-before-recheck invariant — one sentence closes it, and §36.2's
open sync/async projection decision reopens it).

### Streaming and presentation (§15, §20)

**2.10 MAJOR — "No lossy semantic line cap" (§15.4) contradicts a measured,
necessary cap.** Legacy's three named caps exist because a 100MB no-newline
line grew `pending` unboundedly and one giant op became a permanent
reflow-latency tax. The surfaced-line cap is deliberately lossy *before the
newline arrives*, with an honest `(N bytes elided)` marker; §15.7's capture
caps are about storage, not the surfaced line, and nothing in v4 bounds
block size (the third cap). Also §15.7's "if a block advertises copy, its
raw source must still exist" inverts legacy's measured WYSIWYG decision (the
tee file is transient; a copy that changes with *when* you click is worse
than a faithful one).

**2.11 MAJOR — §20.4/§20.5 as written strip legitimate ANSI colour from
command output.** "Blocks contain no terminal escape sequences" + "terminal
allowlists only renderer-owned control sequences" — but legacy's rule is SGR
vs everything else, not ours vs theirs: `make`/`git`/`pytest` colours pass
through and render, on both surfaces (`neutralize()` keeps SGR + OSC 8).

**2.12 MAJOR — The presenter has no generic block and no activity-class
channel.** §9.4 correctly forbids unknown activity from vanishing at the
mapper, but §20.4's six blocks have no generic-tool block — the silence bug
moves from ingestion to presentation (legacy hit it twice: the `tool_fmt`
complement matcher, and codex's kind-drift contract where added rollout
kinds silently vanished until a both-directions completeness test pinned
parser ∪ renderer). Nor can any block carry the served activity class
(`act`) the view modes count by ("used 3 tools" — added specifically because
a generic tool folded into "ran 1 teammate").

**2.13 MAJOR — `FileChangeBlock(Resource, …)` is singular.** `cat a.py b.py`
is ONE block naming all files, with three *recorded rejected alternatives*
(delimiter injection changes semantics; re-reading from disk lies under
`head -20`; N one-liners break the copy stash) and the stated reason: which
OUTPUT belongs to which file can never be recovered. A per-Resource block
reintroduces all three measured regressions.

**2.14 MAJOR — Click handling: v4 assigns it to the pane host; legacy
deliberately rejected that design.** OSC 8 links + `open-actions.conf`, NOT
mouse reporting (grabbing the mouse steals text selection and needs
reflow-invalidated row geometry); clicks arrive out-of-band via a separate
process. That needs a terminal capability (URL-scheme/open-action
registration) none of §20.1's nine roles covers. Related: links live on the
never-wrapped `label` op only (OSC 8's re-open-per-row problem), dropped
below ~34 cols.

**2.15 MAJOR — Viewport restoration is "optional" in §20.3; it is a hard
requirement with a non-rederivable protocol.** Every expand/collapse is a
full reflow repaint (a terminal can't insert lines mid-scrollback) that
parks the viewport at the bottom; without restoration every click teleports
the pane. The measured protocol (global text match with prior — 58/58 vs
1/58 for windowed; DSR handshake vs the DEC 2026 freeze; absolute
scroll-then-up; follow-mode exception; ROW_BUDGET clamp) needs a home, and
"incremental renderer state" mis-describes a model where any toggle
re-renders the whole buffer.

**2.16 MINOR — `peer_messages` has no kind discriminator and cannot name a
teammate.** Lifecycle frames (idle/task-assignment/termination JSON) are the
MAJORITY of team mail and must never be painted as prose bodies; and the
participant columns are Conversation/AgentSession ids while §24.3 itself
says actors stay keys — an agent-team teammate is an actor, so team mail has
no expressible sender/recipient. (The lead's-sends vs agent's-sends
asymmetry — `mail_fmt` fires for every teammate's send while a transcript
sees only its own — is the concrete test any replacement must pass.)

### Conversation semantics (§13, §8.4)

**2.17 MAJOR — Compaction revert: the head-moves-on-evidence rule cannot
hold.** A reverted compaction writes NOTHING (no hook, not one byte); the
only trace is the record GRAPH — a later record's `parentUuid` hanging off
the pre-compaction leaf (measured, session c2442d36: 13,805 shown vs
223,546 held). Evidence arrives arbitrarily late, so the head is
retroactively wrong for an unbounded window; v4 has amendment ops for
activity but no retroactive-correction mechanism for branch-sensitive
projections, no bounded-scan fail-open policy, and doesn't carry the honest
by-construction-undetectable window legacy documents. Similarly §13.4's
"latch clear and post-compaction occupancy commit under one revision"
presumes a closing event that measurably doesn't always arrive (interrupted
compaction fires no PostCompact — the expiry must live on the READ side, and
§12.3 has no abandoned-compaction closer, so the Operation sits `running`
forever).

**2.18 MAJOR — The fork tell (§13.2) is stated without the selection rule or
subtree consequence.** Which sibling is live (the LAST); a dead prompt takes
its WHOLE subtree; `_prompt_bearing`'s text-vs-tool_result distinction
(without it, ~30 legitimate forks per 250-record session get pruned — one
advisory flag once pruned 130 of 184 records); and the arriving record's
parent is an ancestor of the head, so §9.4's expected-head validation would
*reject* the very evidence of an unannounced terminal-side fork.

**2.19 MAJOR — `interaction_details` cannot record a verdict, partial
progress, or a decline-plus-delivery.** The plan pair's verdict
(`approved`/`changes`/`rejected` + `edited`) lives in the label/class since
two of three have no body; the `changes` feedback exists NOWHERE else; the
plan text survives only in the tool_use. v4's `answered|dismissed` collapses
all of it. Ask dialogs are drive-by-DIFF over approximate screen state
(multiSelect Enter TOGGLES — a blind write inverts selections), FORWARD-ONLY
(the driver answers whatever question is current, recovering half-answered
dialogs), and a preview-layout typed answer decomposes into decline +
follow-up `message_delivery` (per-host, per-layout). CAS-on-revision rejects
a stale card but says nothing about a dialog whose position moved under a
fresh card.

**2.20 MAJOR — No rule forbidding the generic cancel key as a failed drive's
bail.** The four legacy drivers declare opposite bail semantics
(ask/plan/confirm: leave it exactly as-is — Escape DECLINES/REJECTS;
rewind: Escape-close everything). "Close what you opened" — the default
instinct — silently rejects a plan the user was about to approve, with a
decline that fires no hook. One normative line fixes it.

**2.21 MAJOR — Claude rewind produces NO history evidence until the next
prompt** (state changes in memory; the file forks by `parentUuid` only on
the next send — measured 2026-07-18; no programmatic restore API exists).
Under §17.6/§13.2 the head cannot move and the Operation is `indeterminate`
for an unbounded interval; v4 defines no interim semantics (branch view,
checkpoint, meaning of a send in the window). Also missing: the
degraded-to-weaker-mode verdict (`both` at a no-code-change checkpoint
degrades to conversation-only, reported not errored) and §17.4's
"confirmation required" verdict contradicting the measured auto-Yes (the
model/effort switch-confirm menu makes the command do NOTHING until
answered; the clicked button IS the consent, so the server presses Yes —
bouncing it to the surface is exactly the dead-looking click legacy fixed).

**2.22 MAJOR — Control-plane evidence rules.** `queued_at_provider` (§17.5)
has no admissible-evidence rule — the natural source (derived attention) is
precisely the measured failure (tab frozen magenta by a terminal-side
cancel promised `queued` for an instantly-submitted message; the fix is an
independent screen-delta probe run BEFORE the paste, since our own paste is
motion). The interrupt is a verified-retry effect (§17.2's "never blindly
retried" either forbids it or exiles the loop) whose stop condition must
consult a DIFFERENT Operation's history state (`queue_drained` outranks the
screen — 4 Escapes once interrupted the just-delivered queued message).
`TerminalInput` must be three primitives with different guarantees (key
events ~2/3 reliable, unacknowledged; raw text drops bytes; bracketed paste
atomic and mode-proof) and input effects are STATEFUL ACROSS GESTURES (vim
NORMAL-mode residue turned a later `/rewind` into the message `nd`) — an
unobserved modality that changes the next control's semantics. And Law 8's
"verified before destructive control" doesn't class *typing* as destructive:
every text-delivering POST re-resolves the window fresh (a reused window id
once closed an unrelated live tab; the memoized map is explicitly never
trusted for writes).

**2.23 MINOR — §3.4's owner table has no session-title row.** The ownership
FLIPS with AgentSession state: live, the provider's in-memory title is
authoritative and overwrites appended records within one turn (measured:
survived ~4 KB, re-clobbered 13×) — so the only live rename is making the
provider change its own mind, and a sticky tab title is forbidden as a
second writer; parked, baqylau appends + a durable override (the record
scrolls out of the 64KB tail window). Nothing in v4 says a fact's owner can
transfer between provider and baqylau by session state. Also `resumable`
(§7.3) is trusted as a column where legacy proves the artifact at gesture
time (410 when the transcript is gone — before the owner guard,
deliberately).

### Attention and alerts (§19, §8.8)

**2.24 CRITICAL — `notification_intents.state` cannot express a HELD
alert.** The third fire-time outcome for a watched `asking` arm: neither
send nor cancel — HOLD, still armed, firing the moment you stop being there
("seeing a question is not answering it"). v4's states admit only the two
outcomes that are both documented legacy regressions (fire anyway = the
second copy device-active suppression exists to stop; drop = the reminder
dies with a glance). The hold also carries `holding` vs `held` (a
once-per-arm audit latch whose folding once cost an alert its
terminal-answering cancels) and suppresses the screen probes while held —
none of it representable.

**2.25 MAJOR — "Evaluated at decision time" (§19.3) collapses three
deliberately different clocks.** Global toggle at ARM time (why it overrides
a per-session mute); mute at SEND time (so muting a held alert still
works); presence at two cadences BY KIND — done's "have I seen it" runs
every scan during the settle window, asking's runs only at send time. Under
one decision-time rule, a done alert glanced at second 3 of the settle still
delivers — exactly what the rule forbids.

**2.26 MAJOR — Presence has no end-of-presence and no here-now/last-here
split.** A TTL cannot express "I left now": measured, 20 of 99 suppressed
done alerts reached the user through NO channel (server still suppressing on
a fresh TTL, page refusing on lost focus). `mark_away` clears viewing/active
but NEVER the device-seen map — freshness and routing are different
questions. §19.2's flat TTL model cannot state either.

**2.27 MAJOR — No device-selection rule, no escalation topology, no
delivered+armed duality.** The MRU pick over ONE map with web winning ties
(cold start routes to the quiet channel), channel following the winner
(browser→push, terminal→Telegram); without a stated rule the natural
implementation is fan-out-to-all — the exact measured regression. A stage-1
push escalates to Telegram after ESCALATE_S; a stage-1 Telegram NEVER
escalates (it already reaches every device). And a stage-1 push leaves the
intent pending *while* a delivery of it is tracked sent — one reaction must
both cancel the escalation and retract the delivered push; a single state
column makes delivered and armed mutually exclusive. Plus: §12.6's `arms`
and §8.8's own `due_at` are two overlapping timer models with no ownership
rule; cancel-reason precedence is undefined but decides retraction
(tab-moved outranks screen reads); the two terminal presence signals have
disjoint consumers (frontmost→routing only, tab-focused→suppression only —
deliberately no terminal twin of device-active); and the two
"answering at the terminal" probes (ask-region diff on red; typed-vs-faint
input on green) have probers in §12.4 but no alert-cancel consumer — an
unsubmitted dialog being edited is invisible in v4's model.

### Accounting (§8.7, §21)

**2.28 MAJOR — Message-snapshot dedup is under-specified; both naive
readings are measurably wrong.** One assistant message = one line PER
CONTENT BLOCK, usage repeated with output a GROWING snapshot: the correct
rule is last-snapshot-per-message-id, credited as the clamped per-field
delta over what was already credited. Summing gave 2.24× inflation twice;
keep-first under-counts. `dedup_key` doesn't say which datapoint survives,
and the cross-batch carry (legacy persists `txlast` beside the cursor) has
no durable home since coordinator caches are explicitly non-authoritative.

**2.29 MAJOR — The five token categories cannot be populated from the
authoritative source.** OTLP exposes FOUR (no 5m/1h cache-creation split);
only the transcript fallback sees the TTL split. Every OTEL-sourced fact
prices creation as all-5m (measured ~$0.9 undercount) with no `unknown`
state — violating §0.2's own no-optimistic-collapse rule. And §21.5 mandates
recomputing cost from a price table while the product's headline `$` is the
provider's OWN `cost.usage` metric — `usage_facts` has no vendor-cost
column, so the figure silently changes provenance with no way to compare or
explain divergence.

**2.30 MAJOR — The write-time health counter (§9.8) contradicts working
retroactive suppression.** Legacy suppresses benign signatures at READ time
— which is what makes "ignore this benign degrade" work on rows that
already exist and turns the warning light off immediately. A write-time
counter + no-silent-decrement leaves the light on forever; v4 defines no
recount/backfill. Also unspecified: the machine-global anomaly fan-out
(global rows counted in EVERY session's ⚠ chip, surfaced once per session
via a second checkpoint that advances BEFORE emitting — an explicit
at-most-once decision), and once-only-consumption evidence (legacy audits
both ends of every hand-off; §9.3's atomic take records nothing, yet the
unconsumed-record test is how cmd_blocked diagnoses never-ran commands).

**2.31 MAJOR — Migration evidence rules.** The trigger is an EVENT
(`StopFailure error=rate_limit`) — a percentage threshold cannot work (the
status line's utilization header is a separate channel from the block
decision; measured stamping 95% thirteen seconds AFTER the block, and it
can never reach 100 while requests bounce); v4 should name the event and
forbid threshold inference. `logged_out` has no evidence rule and the
natural implementation (probe) is a measured false negative (two different
credentials; minted tokens stay valid ~8h after revocation; the only
authoritative probe can itself log the user out) — the signal is the
`authentication_failed` StopFailure. The model-downgrade ladder
(fable→opus→sonnet, floor at Sonnet, current model tried across every
account before any downgrade, scope-aware limit-hit disqualification) is
user-visible product policy §0.2 forbids leaving to the implementor. And
the sole source of the 5h/7d quota windows is a PUSH channel (statusLine
stdin — in no hook payload, transcript, or OTEL) while §10.1 models
`UsageSource` as a pull; model-scoped weekly windows need the separate
credentialed OAuth source with different freshness/failure modes.

**2.32 MINOR — Skip-path decisions produce no evidence.** §9.5 anchors
provenance to *facts*; legacy doctrine anchors it to *decisions* — every
deliberately-declined path names itself (`ignored: agent_id`, `cooldown`,
`migration off`), which is what makes hook_events diagnostic and what
relimit triage rests on. §30.1 tests only catch-and-swallow boundaries.
Related: nothing requires the edge to forward event families with no
consumer (legacy's universal subscriber records all 30 hook events, which is
what makes "nothing is invisible to the audit" true); evidence reads across
an identity repair have no named resolver or index (legacy: `sid_chain`
resolved on nearly every read, needing its own index — ~700 ms cold without
it); and scope-dependent reset fallbacks (an unknown reset must not be
filled from a DIFFERENT window's cadence — a weekly cap once "expired"
within hours off the 5h reset).

**2.33 MINOR — §18.4's "no cut inside a block" vs §14.2's source-position
interleave are not simultaneously satisfiable** when two producers write
into one region — measured: a bg job emitting mid-foreground-block makes the
group's rows non-contiguous, so a group CAN straddle the cut and the client
must fold older ops into the already-live block. v4 asserts both and never
says which yields. Also `dirty_fingerprint` (§7.1 durable row) vs §18.7
("TTL-cached git status is a named query adapter, never a domain fact") —
two owners declared for one fact; legacy's answer is deliberate:
`.git`-file reads for branch/worktree, ONE sanctioned TTL-cached subprocess
for dirty, and the value is three-valued (unknown ≠ clean).

---

## Part 3 — Review: architecture, performance, over-complication

### 3.1 Overall verdict

The domain model at the center of v4 is **good — genuinely good**. The
five-concept core (Conversation / Node / AgentSession / Operation / Stream)
is the right ontology for this product; the four-relationship distinction
(dialogue ancestry ≠ work containment ≠ causal contribution ≠ runtime
lineage) names precisely the thing the legacy system spent a year
discovering op-stamp by op-stamp; the rejection of event sourcing (§2) is
well-argued and correct; "honest uncertainty" (`unknown`/`lost`/
`indeterminate` as first-class values) is the single most valuable idea in
the document, because nearly every hard legacy bug was some form of
optimistic collapse. The tradeoff ledger (§34) and deferred-promotions
section (§35 — Branch/Turn/Actor entities must *earn* existence) show real
architectural discipline. Nothing in the audit suggests the concept center
is wrong.

The problems are of three kinds, in increasing order of concern:

1. **Coverage holes** (Parts 1–2): ~30 major-or-worse places where the
   abstraction cannot express a shipped, measured behavior, plus one hole in
   the core model itself (child-agent conversations, 1.1). These are fixable
   and mostly cheap to fix *now*; several are one-sentence laws.
2. **Uniformly applied heavy machinery** where one or two instances justify
   the pattern and the rest pay for it (§3.4 below). The v5 review list
   (points 2–10) has already smelled most of these; my verdicts are below.
3. **The process posture**: §0.2 plus the v5 decisions ("KEEP FULL SCOPE",
   "no decision left to the implementor", nine authoritative artifacts
   including complete DDL and OpenAPI for *every* future feature including
   cross-provider handover and collaboration) commits to a full waterfall
   blueprint of a system whose defining property is that its correctness
   was *measured into it*, weekly, against undocumented, version-fragile
   provider behavior. I think this is the design's biggest risk — bigger
   than any individual gap — and I argue it in §3.3.

### 3.2 What the design gets right (worth protecting in v5)

- Nodes-vs-Operations, and refusing to copy native record parentage into
  semantic parentage (§7.2) — the prompt-sibling fork rule is the proof this
  distinction pays.
- Durable open facts + rehydration instead of replay (§12.1). This is the
  correct generalization of legacy's scattered sentinels/hand-offs, and it
  genuinely dissolves several legacy contraptions (the fg-live three-id
  problem, DB-file-existence-as-liveness, `sid_chain` on every read).
- Per-kind Stream final authority (§7.5) — "no universal final-record-wins"
  is a lesson many systems never learn.
- The answerable lane as a narrow, explicitly-not-general escape hatch
  (§11), with pass-through as the failure mode.
- Capability objects over provider-name branches (§10.1), including
  "routing follows the actor that produced the item".
- The two-plane delivery split (structural vs stream bytes) and
  slow-clients-resync-never-backpressure (§15.6).
- Evidence/provenance for *inferred* facts (§9.5) — the right instinct; my
  quarrel in §3.4 is with its uniform application, not its existence.
- Fixture-driven parity (§30.2's "port measured transcripts verbatim, do not
  reconstruct tricky semantics from memory", §30.7, §31's gates). This is
  the correct porting epistemology — see §3.3 for why I'd promote it from
  test strategy to *the* completeness mechanism.

### 3.3 Architecture critique

**(a) The availability inversion is under-confronted.** Today the cockpit is
~20 short-lived hook processes + detached tailers coordinating through
SQLite; the only long-lived singleton is the *optional* dashboard. Tab
colors, the mirror, the audit trail all work with the dashboard dead,
because there is nothing to be dead. v4 inverts this: one supervised daemon
is the mandatory center, and §9.7's answer to daemon death is pass-through —
i.e., **every feature stops**: no tab paint, no mirror, no audit rows, no
capture of the very evidence the recovery model depends on. The doc treats
this as an edge case ("hooks never fail the provider" — good, necessary),
but it is a strict availability downgrade of the terminal cockpit, and the
supervisor that is supposed to make it acceptable is deferred to a
deployment contract (§26.3) with no named implementation. The design should
either (i) explicitly accept "daemon dead = cockpit dark" as a product
decision in the Phase 0 inventory, or (ii) keep a minimal edge-side
fallback for the one feature with hard real-time value and trivial state
(the tab paint), or (iii) commit to a concrete supervision story
(launchd/systemd unit, crash-loop policy, watchdog) *in the design*, since
the whole architecture leans on it.

**(b) One hole in the core model, found independently by two auditors from
two directions**: child-agent semantic content (1.1) and actor-addressed
mail (2.16). Both reduce to the same missing decision — *what is the
semantic home of an actor's own dialogue inside one Conversation?* The
Node tree answers only the lead's dialogue. This should be settled before
schema lock; every option (per-actor head, child Conversations with a
containment link, actor-scoped Node subtrees) ripples through §13, §14,
§18's scopes and §24.3.

**(c) The missing-laws pattern.** A striking regularity across all six
audit areas: v4 captures legacy's *conclusions* and drops the *premises*,
and the premise is usually the load-bearing half — device-active
suppression without the in-page toast that justifies it (1.26);
silence-never-proves-success without the self-caused-evidence carve-out
that makes web interrupts recoverable (1.24); closers that match identity
without the ack-vs-completion distinction (2.1); markers without the
record-not-bytes rule (2.2). The legacy docs are unusual in that they
record *why the alternative failed*; v4's condensation systematically
sheds exactly that layer. Whatever v5 becomes, the "measured counterexample"
must be a first-class citizen of the spec (attach the fixture/session id to
the rule), or the same bugs get re-derived from first principles by an
implementor acting in good faith.

**(d) The process posture: completeness-by-prose vs completeness-by-
fixture.** §0.2 demands that an implementor never make a decision; v5 has
decided to apply that bar to the *entire* future product, including
cross-provider handover (§23 — honestly a research project: "compile,
budget, deliver, acknowledge" over capabilities no provider currently
exposes), collaboration, remote backends, and OpenCode. Two observations:

- The legacy system's correctness is *measured*, not derived. The docs
  are full of dated discoveries from the last 30 days alone (the PostCompact
  routing, the queue-drain rule, the padded-Rewind-header drift, the
  tasks-dir drift, the ns-draft per-directory split…). Provider behavior is
  undocumented and version-fragile *by the design's own admission* (§1.2).
  A prose-complete blueprint of behavior that is still being discovered
  weekly is a depreciating asset the moment it is signed; the two marker
  drifts that each broke the rewind driver within a fortnight are the
  existence proof.
- v4 already contains the better completeness mechanism: §30.2/§30.7/§31's
  frozen fixture corpus and behavioral parity gates. A fixture IS a
  decision an implementor cannot get wrong, and it survives condensation
  (see (c)) in a way prose does not. My recommendation: hold §0.2's bar for
  the *invariant core* — domain model, state machines, DDL, storage ports,
  API/SSE contracts, the laws — and define feature completeness as "the
  frozen fixture corpus passes + every difference is an explicit product
  decision", rather than writing the nine-artifact encyclopedia for
  handover and collaboration before Phase 1 code exists. Otherwise v5 is a
  multi-month document project whose hardest sections describe features
  with no measurement behind them — the exact epistemic condition the
  legacy docs exist to prevent.

**(e) Migration realism.** The strangler plan (§31) is sound in shape, but
it dual-runs against a *moving* target: this product is used daily and
changed weekly. Phases 2–5 each gate on "sustained old/new observable
agreement" while the old system keeps evolving; either legacy gets frozen
(a real product cost the plan should name) or every legacy change is made
twice for the duration. The plan should also name the ONE thing phase
ordering gets right and must keep: the read model (Phases 1–2) can ship
value read-only against live legacy traffic before any effect plane moves —
that is the cheapest possible validation of the core model and should be
pushed as early and as visibly as possible.

**(f) Edge runtime is undecided.** The hooks remain short-lived processes
that must reach the daemon inside a provider deadline; the doc never fixes
the transport, the edge language, or the deadline number, and 1.14's
measured per-process interpreter tax (~140 ms under pyenv) is exactly the
kind of constant that decides whether the answerable lane is viable at all.
This belongs in §36.2's pre-schema-lock list.

### 3.4 Performance critique

**(a) Write amplification into one writer.** Legacy writes 1–2 rows per
hook event, spread across per-session `/tmp` state DBs plus one audit DB —
parallel sessions *cannot* contend, by construction. v4 routes every event
of every session through one SQLite WAL file as: observation row + payload
ref + canonical mutations + provenance + provenance_links + decision +
projection updates + outbox rows + change-feed row — call it 5–10× legacy's
row count, serialized through SQLite's single physical writer, sharing that
writer with stream-metadata checkpoints, maintenance batches, and the
answerable lane's transactions. §16.2's answer is "benchmark, then maybe
partition" — but the legacy system already *ran* this experiment: its
per-session partitioning is not an accident, it is the discovered shape.
Treating catalog+per-Conversation partitions as a reluctant fallback
discards a measured result. I would invert the default: per-Conversation
DB partitions (or at least per-Conversation stream/ops tables) with a small
machine catalog, and let the benchmark argue for *merging*, not splitting.

**(b) The answerable lane's latency budget is the feature.** Today the
tee-rewrite decision is made in-process in the hook, in microseconds. Under
v4 it is: hook process spawn → socket → daemon → identity resolution →
coordinator mailbox → eligibility gates → filesystem prepare → SQLite
transaction → reply, all inside a provider deadline, while the same daemon
coalesces a build-log flood. §27.3 measures the timeout/pass-through rate —
good — but never states a budget, and never confronts that pass-through is
not a graceful degrade: it is the silent loss of live foreground streaming,
per command, invisibly. The pass-through *rate under compound load* should
be a numbered acceptance gate (e.g. <0.1%), and if it can't be met, the
eligibility decision should move edge-side with the daemon only receiving
the fait accompli — which is exactly the legacy design.

**(c) The structural feed should not ride the outbox.** §17.1 lists
"structural-feed publication" as an outbox example; §18.5 simultaneously
(and correctly) says the feed is a bounded delivery mechanism, not truth,
and clients must resnapshot on cursor expiry anyway. Durable rows + leases
for disposable delivery hints is pure write-path overhead on every UI
update — the busiest write class in the system. An in-memory broker with
snapshot-on-reconnect (what the legacy dashboard does, over a tunnel, fine)
costs one code path and zero writes.

**(d) Activity materialization invalidation is unbounded.** §14.3's
generations mean a rewind, a late child record, or a head move can
invalidate and recompute the materialized activity of an arbitrarily long
Conversation; no bound is stated, and §27.3's backlog benchmark tests
composition cost but not invalidation storms. Note §18.2 *already* states
the right rule — "materialize only when a measured query or correctness
contract justifies it" — and §14.3 then mandates the table anyway. Compose
read-side per window first (legacy does, it's fine); materialize when the
benchmark says so.

**(e) Self-inflicted evidence volume.** Full provenance rows per
Observation exist to answer questions legacy answers with a `decision`
string column on one row. The retention machinery (§16.4) is then needed
largely to clean up what the design chose to write. See §3.5.

**(f) Latency of the terminal mirror.** Legacy's pane paints from a local
SQLite poll at 250ms with zero intermediaries. v4's path is provider →
edge → daemon → coalescer → staging frame → stream feed → pane host →
paint. Each hop is cheap; the sum is a new floor, and §27.3's "assistant
Stream display latency" gate should explicitly include the *command output*
pane path with a target at least as good as the current 250ms poll.

### 3.5 Over-complicated for no reason (with verdicts per v5 review point)

- **v5 #2, audit records per event: TRIM.** Keep the raw-payload retention
  and the quarantine machinery; drop *routine* provenance rows. Write
  provenance only for INFERRED or corrected facts (which is where §9.5's
  value lives) and keep a legacy-style decision string on the observation
  row for everything else. One row instead of 3–5, and 2.32's skip-path
  doctrine comes back for free.
- **v5 #3, coordinator-per-Conversation: SIMPLIFY.** The *serialization* is
  right and cheap (a per-Conversation asyncio lock + a small cache). The
  supervised actor with mailbox, overflow policy, rehydration lifecycle and
  parking is machinery the doc itself undercuts by declaring coordinators
  non-authoritative caches (§6.1). Build the lock; grow the actor if
  probe-scheduling actually demands it.
- **v5 #4, framed staging files: SIMPLIFY.** Append-only files + byte
  cursors (legacy's `tail.py` model) with a tolerated torn tail. The
  checksummed frame format defends command-output bytes whose loss window
  is a UI artifact — §7.5's own authority rules already reconcile assistant
  text to provider-final and command output to the provider result when
  capture is imperfect. Add frames only if measured corruption appears.
- **v5 #5, activity amendment protocol: TRIM.** Keep server-owned ordering
  and whole-block pages (genuinely right, hard-won). Drop
  generations + `move`/`supersede` wire ops: `append`, `amend`, and
  `resnapshot_required` cover the known world — the one measured reorder
  (child-task endpoint before the consuming answer) is a *placement rule at
  compose time*, not a live mutation class. Revisit if a second reorder
  class ever appears.
- **v5 #6, cursor replay: DROP.** Snapshot-on-reconnect + per-Stream
  revision refetch. The clients must implement resnapshot anyway (cursor
  expiry, §18.5); retained structural cursors are a second protocol whose
  only payoff is saving one snapshot fetch after a blip. Legacy resnapshots
  over a phone tunnel today.
- **v5 #7, outbox-for-everything: SPLIT.** Outbox + durable handles for
  alert delivery/retraction (measured need — restart-safe retraction is a
  real legacy gap v4 improves on) and for non-idempotent provider/terminal
  *gestures* (typed keys, launches). Direct call + verify/reconcile for tab
  paints and pane ops (idempotent, cheap to recompute, already
  receipt-verified by §17.2's rule), and no outbox for feed publication
  (see 3.4c).
- **v5 #8, restart-safe temp state: KEEP most of it.** Durable open
  Operations/correlations are a genuine win over legacy's sentinel zoo, and
  §12.1's "declared live-only facets" is the right pressure valve. Trim
  only the arms whose firing condition is recomputable from durable state
  at scan time (most notification timing — the scan cadence is 1s anyway).
- **v5 #9, draft CAS machinery: KEEP.** This one is *not* speculative:
  legacy converged to exactly seq-CAS + tombstones + origin-echo through
  three dated, user-reported bugs (2026-07-19/-22/-25). §8.5 is a faithful
  generalization. Do not simplify it back to last-write-wins.
- **v5 #10, interfaces designed early: PARTIAL.** The provider protocols
  are justified — codex already exercises most seams and is the reason the
  legacy plugin registry exists. The nine terminal *roles* are speculative
  with one terminal in existence (and the audit shows the role list is
  simultaneously too fine and incomplete — no click/open-action channel,
  2.14, and one undifferentiated TerminalInput, 2.22). Collapse to the
  legacy `Frontend` surface + capability flags; split roles when terminal
  #2 arrives.
- **Content-addressed blob store: SIMPLIFY.** SHA-256 dedup + reachability
  manifests + orphan GC for a single-user local tool whose blobs are mostly
  unique command outputs — the dedup win is ~nil and the GC machinery is
  real. Per-stream/per-resource files with class-based retention (legacy's
  park model) do the job; keep the digest as an integrity field, not as the
  storage key.
- **`/api/v1/global` was rightly banned — but don't over-rotate.** Splitting
  into *owned* endpoints is right; splitting into a dozen chatty ones
  recreates the N-requests-per-view problem the legacy session payload
  exists to avoid. One composed snapshot per *view*, each field with a
  declared owner, is compatible with §18.7.

### 3.6 Internal contradictions (the doc fails its own §0.2 in places)

Collected from the audits — each needs a consistency decision, not prose:

1. §9.8 write-time health counter + never-decrement **vs** working
   retroactive benign-signature suppression (2.30).
2. §16.4 "cumulative counts never recomputed from pruned evidence" **vs**
   usage totals as read-time projections over retention-classed
   `usage_facts` (2.31-adjacent; also strands the stats page, 1.18).
3. §18.4 "no cut inside a block" **vs** §14.2 source-position interleave
   under concurrent producers (2.33).
4. §7.1 `dirty_fingerprint` as a durable domain row **vs** §18.7's TTL git
   status as a named query adapter (2.33).
5. §17.1 feed-publication-as-outbox **vs** §18.5 feed-as-bounded-non-truth
   (3.4c).
6. §12.6 `arms` **vs** §8.8's private `due_at`/state — two duplicate timer
   models with no ownership rule (2.27).
7. §18.2 "materialize only when measured" **vs** §14.3 mandating the
   materialized activity table (3.4d).
8. §13.2 "head moves only on confirming evidence" **vs** the two measured
   cases where the evidence arrives arbitrarily late or consists of an
   ancestor-parented record §9.4's head validation would reject (2.17,
   2.18, 2.21).

### 3.7 Recommendations, in priority order

1. **Fix the core-model holes before schema lock** — child-agent semantic
   content (1.1), interaction verdicts (2.19), audience/register fields on
   operations (2.19/2.16/2.12's `act` channel), context-occupancy owner
   (1.2), title ownership flip (2.23). All are cheap now and schema-breaking
   later.
2. **Add the missing laws to §33** (one sentence each): markers matched as
   records never bytes, with self-byproduct exclusion (2.2); transforms
   invertible and inverted at the boundary (2.3); an ack is not a closer —
   acceptance ≠ completion for inbound lifecycles (2.1); an unmatched closer
   materializes, never drops (2.1); a registered closer may not fire — the
   no-closer verdict must be independently safe (2.4); subscription
   manifests classify delegating hook families, which are never wired
   (1.10); a self-caused effect licenses a bounded reconciliation prober
   (1.24); a failed interaction drive leaves the dialog untouched unless the
   provider's cancel is declared neutral (2.20); typing is destructive —
   input effects require a fresh binding (2.22); attention recomputes from
   post-commit state (2.9).
3. **Resolve the eight internal contradictions** (§3.6) explicitly.
4. **Re-scope v5's completeness contract**: prose-complete invariant core +
   fixture-complete behavior (§3.3d). Cut handover/collaboration to
   architecture-shaped chapters (data model + security boundary + triggers)
   rather than implementation-complete specs of unmeasured workflows.
5. **Decide the availability posture** of the cockpit under daemon death,
   and name the supervisor (§3.3a).
6. **Make the benchmark honest**: numbered gates including answerable
   pass-through rate and mirror-path latency vs today's 250ms; default to
   per-Conversation partitioning and let measurement argue for merging
   (§3.4a/b/f).
7. **Apply the §3.5 trims** — they remove the majority of v5 review points
   2–8's machinery while keeping every measured-need instance.
8. **Sequence for validation**: push the read-only Phases 1–2 against live
   legacy traffic as early as possible; that is where the core model is
   proven or falsified cheaply (§3.3e).

---

## Appendix — audit provenance

Six auditors, 2026-08-05, each reading the full v4 doc plus its area's
legacy code/docs first-hand; ~100 raw findings consolidated to the ~60
above (overlapping findings — e.g. the async launch-ack, the `updatedInput`
inverse, the hidden-agent husk rows — were found independently by 2–3
auditors each, which is decent evidence they are real and load-bearing).
Areas: terminal cockpit · dashboard read side · control plane ·
attention/presence/alerts · audit/accounting · lifecycle/hooks/recovery.
The auditors also verified, area by area, what v4 covers *faithfully*; the
"calibration note" list in the header and the per-part "covered well" notes
in their raw reports were used to keep these lists precise rather than
exhaustive.
