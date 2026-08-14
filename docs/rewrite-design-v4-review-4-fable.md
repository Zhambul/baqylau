# baqylau v4 rewrite design — review 4 (post-§40 revision)

Reviewed document: `docs/rewrite-design-v4-codex.md` (13,635 lines, 2026-08-05
revision, self-declared "IMPLEMENTATION-READY DESIGN — VALIDATED SPECIFICATION").
Review date: 2026-08-05. Reviewer: Claude (Fable 5).

**Method.** The core design (§0–37) and the key closure sections (§38.28, §38.30,
§38.32, §40.6–40.8) were read directly; six parallel area auditors then compared
the full design against the legacy implementation, one per area (session
lifecycle/hooks/tabs · mirror/streaming/rendering/scoreboard · child
agents/mail/codex/formatters · dashboard read side · control plane/alerts ·
audit/OTEL/usage/infra/testing). Every auditor was required to grep the design
for each mechanism's vocabulary AND its renames before claiming an absence, and
to dedupe against all three prior reviews plus `rewrite-design-v5-decisions.md`.
The four most damning findings were re-verified against the design text by the
consolidating reviewer before inclusion (§38.2:4614–4629, §38.6:5065–5069,
§40.3:12552–12555, and the zero-hit greps for `Stop`/`UserPromptSubmit`/
`PostToolUse`).

**Closure status of the review series** (matters for dedupe): the design has
formally closed `rewrite-design-v4-review-claude.md` (§38) and
`rewrite-design-v4-review-2-fable.md` (§40). `rewrite-design-v4-review-3-fable.md`
is **not** formally closed — §40.7 states it closes the storage gaps "found by
the second and third legacy-coverage reviews", and spot checks confirm review-3's
G1 tables now exist — but most of review-3's non-storage findings have no closure
text and remain open review input. Nothing already reported there is re-reported
here except where the closure text that claims to fix it is itself wrong.
(Trivially: the document's top-level numbering skips from §38 to §40; there is
no §39.)

**Answering the three questions asked of this review:**

1. **Is the design a superset of the legacy system?** Not yet. After three
   review rounds the residue is smaller but real: ~25 legacy mechanisms still
   have no home (Part 1), four of them critical — and three of the four
   criticals are concentrated in one place, the provider adapters' *parse-side
   judgements* (the Claude hook subscription manifest has no content; the codex
   adapter is missing the measured rules that make its artifacts readable).
2. **Where does the design describe an existing feature with gaps?** ~30
   findings (Part 2). The signature failure mode of this round is new and
   worse than omission: **normative closure text that states a measured legacy
   rule with its polarity inverted**. Five confirmed instances — sid-fork
   adoption liveness (§38.6), the compaction-boundary fail-open direction
   (§38.2), the OTLP attribute level (§38.4), focus-mode reply selection
   (§38.9), and the Σ-row input figure (§40.3) — each of which, implemented as
   written, precisely reintroduces (or newly manufactures) a measured
   production bug while *appearing* to close the finding that reported it.
3. **Overall review** — Part 3: verdict, architecture critique, performance
   critique, over-complication (keyed to the open v5-decisions points 2–10),
   and an ordered fix-first list.

Severity vocabulary: **CRITICAL** = blocks implementation or regresses a
measured production bug class · **MAJOR** = a real feature/lesson is lost or a
contract is unimplementable as written · **MINOR** = real but small, cheap to
fix. Design citations are line numbers in `rewrite-design-v4-codex.md`; legacy
citations are `file:line` in this repo.

---

## Part 1 — Legacy features with NO home in the design

### 1.1 Critical

**1.1.1 The Claude Code hook subscription manifest is never populated — ten
event families exist nowhere in the design.** §38.4 defines the manifest
*schema* (4820–4822) and the rule "unknown families default to disabled";
§38.37.9 gates shipping on a per-family field mapping (10863–10865); §38.37.6
calls itself the *closed* Claude schema registry. But grepping all 13,635 lines:
**`Stop`, `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`,
`Notification`, `PreCompact`, `TaskCreated`/`TaskCompleted`,
`PermissionRequest`, and `PreToolUse` for every non-Task tool are never named**
(verified — zero hits each). Read against the design's own default-disabled
rule, it ships with the majority of Claude Code's hook surface disabled and
unmappable. Consequences land on the attention plane §19.1 specifies: no `Stop`
⇒ nothing can ever produce `done` (the most-used tab state,
`plugins/claude_code/tabstatus.py:591`, docs/tab-colors.md:17); no
`UserPromptSubmit` ⇒ no turn-start evidence; no non-Task `PreToolUse` ⇒ no
`executing` and no `asking`; no `PostToolUse(Failure)` ⇒ no tool closers at
all — the CLAUDE.md invariant "failures arrive on PostToolUseFailure, not
PostToolUse" has its law stated (§12.3:1798) but nothing to apply it to, and
the manifest schema has no field expressing the success/failure pairing; no
`PreCompact` ⇒ the compaction latch §13.4/§38.2 depends on has no arming event
(5601 reasons about the missing *close* without ever naming the *open*). The
exhaustive legacy routing table is docs/wiring.md:44–59 +
`plugins/claude_code/dispatch.py:129–214`. No prior review reported this; the
manifest's *existence* was requested and closed, its *content* never was.

**1.1.2 Nothing can ever classify a child as a teammate.** `track_kind =
teammate` exists as an enum value in five places (4497, 3296, 9072, 10949,
11125) and §40.3:12559 even knows teammates reuse the subagent slot under a
second palette — but nothing supplies the fact. The single legacy source is
`meta.json`'s `taskKind == "in_process_teammate"`
(`plugins/claude_code/subagent_fmt.py:38–46`, consumed at :217–218, :159, and
`substream.py:95` where it selects the `team:` vs `sub:` producer stamp that is
the entire basis of actor scope). `taskKind`, `in_process_teammate`, and
`customAgentType` have **zero hits** in the design, and §38.37.6:10713 declares
the child `meta.json` field list closed at exactly `agentId`, `toolUseId`,
`agent_transcript_path`, `stoppedByUser` — i.e. the mapping table *forbids*
reading the field that decides the track kind. Three more facts ride on the
same omission: `customAgentType` (the agent-definition lookup key, input to
2.2.9 below), the measured race (meta.json can lag `SubagentStart`; gating on
the verdict was itself a shipped bug, `subagent_fmt.py:280–287`), and the
inbound-mail record shape (`<teammate-message teammate_id=…>` wrapping a
`user` record, `transcript.py:207–214`).

**1.1.3 The codex child rollout's replayed-parent prefix has no boundary
rule.** A codex subagent rollout *opens with a replay of the parent thread's
history as of the fork* — left in, the prefix doubles the parent's prose and
exec into the child's scoped view; the measured symptom was that clicking a
subagent looked identical to the lead (docs/codex.md:1320–1336). Legacy owns
the boundary in one predicate applied two deliberately different ways
(`plugins/codex/rollout.py:845–870` `subagent_fork_epoch`/`is_child_bootstrap`;
a live per-record gate in `stream.py` and a byte offset
`subagent_body_offset` for the random-access reader; both fail open — show
everything, never an empty scope). `replayed`, `thread_source`, `parent
thread`: zero hits. Worse, the **parent turn** — which §14.2:1965–1967 and
§38.37.7:10783 *require* as an ordering input — is taken *from that very
prefix* (the last replayed `task_started` before the bootstrap,
docs/codex.md:1262–1270, the only place it exists since the child's own
NEW_TASK payload is `encrypted_content`). The design demands the fact and
describes no source for it.

**1.1.4 Codex child discovery and the creation-time admission gate.** How a
child rollout is *found* and linked to its parent (`watch.py:247–280`
`rollout_subagent`: first `session_meta`'s `thread_source == "subagent"` or a
`source.subagent.thread_spawn` block) appears nowhere in §38.37.7. Neither
does the admission clock: `rollout_created` (`watch.py:202–218`) uses the
rollout **filename timestamp** (fallback inode birthtime), *deliberately not
mtime* — measured failure: a long `codex exec` started before the session
refreshes its mtime forever, passed an mtime filter, had its dead claim
stolen, and replayed its entire history from byte 0 into the new session's
mirror (docs/codex.md:50–54). The same gate is what stops a resume replaying
the prior run's subagents. §38.37.5:10677's "mtime never chooses a branch or
current facet" is about ordering, not discovery admission — a different
question with a different answer.

### 1.2 Major

**1.2.1 Codex's structural synthetic-record rule and its two carve-outs.**
Codex re-injects context blocks as user/developer messages every turn; legacy
drops them *structurally* rather than by allowlist (`rollout.py:316–332`
`is_synthetic`: developer/system roles always synthetic; a role=user `<tag>`
wrapper synthetic **by default** — robust to new tags — except the
`INPUT_WRAPPERS` member `<task>`, kept and unwrapped), with the
`PLAN_WRAPPER = "proposed_plan"` carve-out answered *before* the synthetic
question because the structural rule otherwise ate codex's plan proposals
(docs/codex.md:1378–1389). `synthetic` has one unrelated hit (4533);
`proposed_plan` zero. §38.37.7:10778 ("user item | parsed kind, not embedded
text | prompt Node") instructs the opposite of what is needed.

**1.2.2 Codex plan mode: a plan with no interaction and no verdict.** The
design models plans exclusively as Interactions with an approve/changes/reject
verdict (1147–1154, 5489–5508, §38.2:4694). Codex writes **no tool call and no
decision anywhere**; the measured record (docs/codex.md:1391–1401) shows the
only thing following a `<proposed_plan>` is more planning or a
collaboration-mode switch — which is *not* a verdict, since approving and
abandoning both leave plan mode. Legacy emits a `plan` conversation record
with deliberately no `plandecision` twin (`plugins/codex/read.py:423`).
Nothing in the design permits a verdict-less plan or forbids inferring one
from a mode change. Second half: codex records the `/plan` turn twice itself
(raw submission + `turn_aborted` + stripped re-submission); legacy de-doubles
by comparing the *adjacent* prompt with the slash-run stripped from the key
only (`read.py:237`, `:356`). Design 10787 makes `turn_aborted` an
interruption closer and never de-doubles, so the prompt bubbles twice.

**1.2.3 A non-shell codex tool call is not a laundered command.** Codex ≥0.146
routes many tools through the same `exec` custom tool; parsing them all as
shell commands rendered a child's entire real work as `▶ cmd` blocks of raw
JavaScript (measured: five such calls, zero shell commands,
docs/codex.md:1423–1447; the quote-aware close-paren cut is
`rollout.py:102–162`). §9.4's unknown-tool default (1449–1451) is good but
covers the *unknown*-kind case, not a **known kind that is the wrong kind** —
§38.37.7:10786 is where the distinction would live and does not draw it.
Adjacent: `web_search_end` events are where codex searches actually live (five
events, zero `web_search_call` response_items in the measured child rollout);
without parsing the event a codex web search renders nothing.

**1.2.4 The codex standalone-HOST role has no rows.** Distinctive measured
facts with no home (all `plugins/codex/session.py` + docs/codex.md:273–327):
codex fires **no session-end event** (only per-turn Stop), so teardown rides a
ppid-walk-resolved *process pid* handed to the watcher — the same class of
lesson as "Claude fires no hook on cancel"; the nested guard (a `codex exec`
inside a Claude session fires codex's own SessionStart — detected via the
tab's live `claude_mirror` var and skipped, no second pane, no double stream);
the originator inversion (standalone adopts `codex-tui` rollouts, the exact
opposite of the secondary-source rule); only a codex in a kitty window gets a
mirror (the ChatGPT-app skip); and standalone tails the whole session and
never ends on the per-turn grace while sidecar/subagent tasks do
(`stream.py:1093–1113`). The mechanism *classes* exist in the design (host
death closer 1788, PID probers 1809); the codex rows do not.

**1.2.5 The price table does not exist; `pricing_epoch` is an undefined
required column.** `pricing_epoch TEXT NOT NULL` is a primary-key component of
both usage rollup tables (8140, 8167); §21.5/§38.17 say cost is "read-time
arithmetic against a time-indexed provider/model price table" — and no table
in the 142-table DDL holds prices, no module in §29 owns one, and no
model-id→price resolution rule exists. Legacy carries four measured facts
nothing preserves (`plugins/claude_code/accounting.py:30–69`): substring
matching, specific-before-general, with a recorded real 3× undercount from
wrong keys; unknown model → `None`, never a guess; cache multipliers as
pricing *structure* (read 0.1×, 5m-create 1.25×, 1h-create 2×) that a flat
rate pair cannot express — and which §38.17:5956's range-or-unknown rule
*depends on*; and `SONNET5_INTRO_UNTIL`, the one worked example of an epoch.
Because cost is never persisted, a wrong resolution silently rewrites all
historical displayed cost.

**1.2.6 No test-environment isolation / live-fire safety contract.** §30 is a
topic list; nothing specifies how the suite avoids the developer's real
machine. `docs/testing.md` is an entire document of rules earned by incidents
that apply *more* to v4 (it adds Web Push, Telegram, a provider-config
installer that rewrites `~/.claude/settings.json`, and a credential port):
the fallback-transport rule ("hermeticity has to name both" — measured:
sandboxing the Telegram credential dir made the channel degrade to the legacy
transport and `make test` paged the developer's real phone, testing.md:63–84);
in-process audit sandboxing (deliberate test error rows lit the ⚠ light in
every live session); `CLAUDE_CONFIG_DIR` sandboxing (a test **truncated the
real `~/.claude/settings.json`** through the switcher's symlink); no DB in the
repo tree (enforced in `core/state._connect`); test-only env knobs that leave
shipped behavior bit-identical and stamp `sessions.env`; `wait_until`-only
discipline. Under the v5 bar this is a whole missing contract, and v4's
eventually-consistent worker architecture makes it harder, not easier.

**1.2.7 How an account is placed onto a launch has no owner.** §21.4 step 8
says "open a new attempt with target account placement"; nothing says what
placement *is*. Legacy: `account.launch_argv`
(`plugins/claude_code/account.py:96–112`) — `[$SHELL, "-lic", '<alias> "$@"',
…]`, with the recorded reason (kitty execs with kitty's own env; no user PATH,
no aliases; a bare `["claude"]` dies command-not-found while `kitten @ launch`
still exits 0; `c1`/`c2` are zsh aliases only an interactive login shell
resolves; the alias must be registry-vetted because it lands in the fixed
command string) — and `config_dir_for(slug)` (:67–77) resolving *another*
session's `CLAUDE_CONFIG_DIR`. The `accounts` DDL (8948) has no launch word
and no config reference; §40.5 reads the aliases only in the *importer*, into
an explicit cutover artifact. After cutover, §38.18's automatic migration —
which must launch under a chosen account — has no argument to build from.

**1.2.8 Symlinked provider config vs the atomic-replace installer.** §38.4's
install workflow (temp sibling → `.bak` → atomic replace → verify, 4837–4846)
meets the account switcher's reality: `configs/<slug>/settings.json` is a
**symlink to the shared `~/.claude/settings.json`** (docs/testing.md:42–51,
including the truncation incident). Atomic rename over a symlink replaces the
*link*, not the target: the managed config lands in one account's dir and
every other account silently stops seeing the telemetry env and statusLine
shim — the two things §38.4 exists to install — and the verify step passes
because it re-reads the file just written. The design must choose
realpath-and-replace-target vs deliberately-break-the-link, and state that
installation covers every account's config dir (nothing requires N rows).

**1.2.9 The attention-state → visible-presentation table has no owner.** The
design specifies the whole attention machinery and never says what the user
*sees*: no state→colour mapping, no user-tunability statement.
`magenta`/`#61afef`/"tab colour": zero hits; 5384 says the verified-paint
record holds two colours without saying where they come from. Legacy:
`core/tabs.py:261–273` (`COLORS`, state → active fg/bg + darkened inactive bg
— the darkened variant being the feature's premise), one consumer
(`core/tabpaint.py:88`), documented user-tunable (docs/tab-colors.md:486).
Against §0.2 this is a hole in the thing the feature's name refers to.

**1.2.10 `AttentionService` is named exactly once in 13,635 lines** — in the
traceability matrix (11395) — and has no §38.26 ownership row, no store
protocol in §38.25/§40.8, no runtime task, no method list, while
`WorkspaceGitStore` (a branch name and a dirty bit) gets a complete protocol.
The tab-colour feature's brain is the one service with no contract.

**1.2.11 Provider notification classification has a rule but no parser.**
§19.1 lists "provider notification classification" as an attention input;
nothing supplies it (the `Notification` hook is unmapped per 1.1.1). Legacy is
precise (`tabstatus.py:846–884`): the `[Pp]ermission|[Aa]pprov|confirmation`
text discriminator → red, red wins over the bg check; a notification arriving
while the main session is mid-turn is a *teammate ping* and is ignored (in an
agent team these fire constantly and painted green over a working lead); an
`awaiting-bg` tab whose bg just finished means the main is *taking over* →
working, not green. The precedence ladder may subsume (b) and (c); the parser
in (a) is subsumed by nothing.

**1.2.12 Per-project remembered mirror width, and the write-back that creates
it.** Every resize measures the achieved percentage and records it keyed by
the *project* cwd (`split.py:305–311` `save_size`, `:266–282` `project_bias`,
`~/.claude/kitty-mirror.db`; docs/mirror-pane.md:853–859), restored at the
next SessionStart in that project. §38.10:5337–5343's resolution chain
(request → per-principal preference → machine config → imported
`CLAUDE_MIRROR_STEP` → default) has **no project scope** — though the generic
`preferences` table admits `scope_type='project'` (9485) — and nothing states
that grow/shrink/setpct/reset persists the measured result. (Found
independently by two auditors.)

**1.2.13 Scorebar row 0 is missing from the section that declares itself the
five-row scorebar's owner.** §40.3:12527 makes `agent_session_scoreboards`
"the durable owner of the five-row scorebar" and then enumerates rows 1–4
only. Row 0 — `⬡ <sid> · ◈ <slug> · <label> · 5h N% · 7d N%`, with its own
tail-drop order (usage before account, id always kept) and a measured
wrap-bug fix in its width math (`bin/claude-scorebar.py:162–198`, comment at
:171–177) — appears nowhere. The *data* exists (§8.7, §38.18, §21.1); the row
does not. Internal inconsistency inside the closure written for review-2 §1.7.

**1.2.14 The content-streamer incrementality contract.** Legacy pins *when*
each render kind may render, with measured reasons: markdown streams
incrementally holding an incomplete trailing block fence-aware; JSON/YAML/
source **buffer to close** (a partial JSON document is invalid) and degrade to
the single-sourced verbatim fallback `emphasize(unescape(raw))`; the fence
sniff runs on the **first data-bearing read only**, never buffering across
polls, because live streaming of `make build` is worth more than catching a
late fence (`core/render.py:370–394`; docs/mirror-pane.md:160–207, 358–376).
§38.8:5160–5165 gives the detection registry and priority but never says
whether a kind streams or buffers, nor the degrade path. (§38.8:5188's
late-fence *amendment* rule silently reverses the documented first-read-only
trade-off; defensible under revisions, but it should be stated as a
reversal.)

**1.2.15 Actor attribution fields on ordinary blocks in the shared pane.**
Every op of a per-agent stream carries the agent's name and its
`opus-5·high` / `ctx 5% · 50k/1M` chips as op FIELDS (`who`/`tags`) —
composed into text only by the terminal, deliberately fields-not-text so the
web's actor-scoped view can simply not render them; the prior string-parsing
version failed on every unanticipated op shape (`core/ops.py:221–234`,
`core/streamfmt.py:161–236` `compose`/`strip_who`). §38.1 solves the *domain*
side (actor tracks); but in §20.4's block list only AgentTaskBlock carries
`actor` — CommandBlock, FileChangeBlock, MessageBlock carry no actor,
register, audience, or runtime label, so a subagent's command block in the
shared terminal pane is unattributable.

**1.2.16 The nested double gutter.** A child agent's own bg/monitor job paints
two gutter bars — outer = the agent's slot colour, inner = the job's own
palette slot — the only thing that says "this job belongs to THAT agent" in a
shared pane (`core/ops.py:329–330` `outer`; `bin/claude-mirror.py:229–235`;
`substream.py:268–295`; docs/subagents.md:287–292). `gutter` has one hit
(5179, renderer width); §8.6 gives one slot per entity; §14.2's "declared slot
adoption" is about ordering, not nesting. The information exists
(`parent_track_id`); the presentation rule does not. (Found independently by
two auditors.)

**1.2.17 The per-session Errors tab has no read route or DTO.** One of the
four secondary tabs of every session view (badge + swallowed-exception rows,
fork-chain-aware; `core/sessionapi.py:705–727`,
`dashboard/read/session.py:154–161`, docs/dashboard.md:636). Storage now
exists (`health_errors` carries tracebacks), and the *badge* is arguably
`warning_count` — but the closed endpoint inventory (§38.24) has no route that
returns the rows, and §40.4's badge inventory (12606–12609) omits errors.
Needs an endpoint row or an explicit product drop.

**1.2.18 Focus mode's errand boundaries.** Focus keeps exactly one assistant
reply per turn and restarts the search at an *errand boundary* — an injected
prompt that RESUMED an ended turn (a blocking Stop hook's feedback, the
`resumed` flag from `transcript._resumes_turn`), or a memory-wiki write but
only while *armed* by a segment ending on a solo bookkeeping reply — the
armed condition being load-bearing (an unconditional boundary turned 6 replies
into 16; the fix was in response to a verbatim user complaint)
(`dashboard/static/app.05-session.js:1307–1421`; docs/dashboard.md:7085–7093).
`errand`, `resumes_turn`, `housekeeping`: zero hits. The Node model's `origin`
enum cannot distinguish an injection that resumed an ended turn from one that
did not — the exact discriminator the boundary needs.

**1.2.19 The folded-run summary counting model.** "Used 3 tools" / "N of M
shown" (5223, 5242) needs two absent facts: Claude Code's own fragment
vocabulary (`[counter, active verb, done verb, singular, plural]` in its own
emission order, extracted from the 2.1.220 binary, plus the rows Claude has no
vocabulary for; `app.05-session.js:1060–1110`), and **distinct-subject
counting** — agent/team/codex count distinct agent ids, mail counts message
ids, never rows (counting rows produced "running 77 agents" for a session with
21), which needs the server-side subject stamps `it["agent"]`/`it["mid"]`
(`dashboard/opshtml/ops.py:520–561`). `ActivityItemDTO` (10959) carries no
subject identity and no statement that folds count subjects.

**1.2.20 Mail: the verbose-only plumbing axis.** The inbox poller's
`delivered`/`read`/lifecycle rows are shown in **verbose only** (a verbatim
user request, `dashboard/opshtml/actclass.py:845–861`; measured: 33 sent, 12
poller rows, 10 of them lifecycle frames, `mail_fmt.py:6–17`), with the
`body_follows` history escape hatch for pre-send-row sessions. §40.3 closes
the census sampling well, but the design has no third visibility axis:
`audience` is lead/actor/both/hidden and `view_mode` folds by class; neither
expresses "verbose-only". Also absent: the `Mail …` vs `Message …` wording
rule ("a line can never promise content it does not have", `msgs.py:94–99`)
and the mail row's own `CAP_TEXT = 60`, deliberately different from the two
child-stream caps §38.8:5168–5169 does pin. (Found independently by two
auditors.)

**1.2.21 No live propagation of notification settings or per-session mutes.**
Legacy pushes `notify-config` over SSE when the global alerts switch flips so
the ◉/○ button stays in sync across open pages/devices
(`dashboard/http/post/state.py:317`, docs/dashboard.md *Global alerts
toggle*), and the same for per-session mutes. The registered 36-event set
(6368–6372) has nothing for `PUT /api/v1/notification-settings` or
preferences other than view mode. Server behavior is fine; cross-device UI
sync — the reason the event exists — is lost.

### 1.3 Minor (compact)

- **Section-banner emphasis** (`=== title ===` → bold amber inside command
  output, with the deliberately conservative detector that spares `x == y`,
  valgrind `==123==`, and diff `--- a/file`; `core/render.py:258–285`). Zero
  coverage.
- **Inline markdown for prose blocks in the pane** — `render_kind` detection
  is specified only for command output (5161, default `plain`); nothing routes
  `assistant_text`/agent prose through the inline-markdown subset
  (`core/agentblocks.py:289`, `core/render.py:288–322`), so as written the
  pane paints literal `**bold**`.
- **Monitor presentation**: the watched command under the header as a `code`
  op, `⇄ ws · <url>` for WebSocket monitors, `· persistent`/`· ≤<dur>`
  lifetime suffix (docs/streaming.md:378–392). `monitor` exists only as an
  Operation kind.
- **Warning-light display contents**: flood collapse is named with no
  threshold/format (legacy: >3 new rows in one 5 s poll → one line pointing at
  the CLI, `core/errwatch.py:61,186–190`); the last-traceback-line summary
  with the `NoneType: None` → func-string substitution (:196–201).
- **Positive paint audit rows**: §38.9 requires only the negative paths
  (`paint_skipped`, null-anchor reasons); legacy's per-reflow `paint` row and
  the `view-reflow`/`view-drift` rows (`bin/claude-mirror.py:355–366,
  927–933, 1002–1009`) are why the anchor/drift incidents were crackable.
- **`errors.context`** — the swallow site's free-form local values
  (`core/audit.py:430–448`), printed by the errors CLI before the traceback;
  `health_errors`' `evidence_ids_json` points at rows, not the values the
  site chose to capture.
- **OTLP port configurability** — 4319 hard-pinned in two places that must
  agree (4863, 4876) with no config value; legacy single-sites it plus
  `CLAUDE_OTEL_PORT` for tests (`plugins/claude_code/otel/receiver.py:38–42`).
- **`<system-reminder>` stripping** inside a child's prompt record and the
  empty-after-strip ⇒ paint-no-block rule (`transcript.py:199–204`,
  docs/subagents.md:16–23). Design 10706 excludes record *kinds* only.
- **The `general-purpose` label substitution** (body lines use the task
  description, header keeps the type; `substream.py:84–88`).
- **The `/command` prompt-bubble tint projection** (`read/meta.cmd_names` —
  one source so the optimistic bubble, the queued chip and server-rendered
  bubbles cannot disagree; `dashboard/read/session.py:391–396`).
- **`window_label`** — the duration-keyed label table ("10080 minutes reads
  `7d` on every host's row", `dashboard/read/lists.py:243–265`);
  `QuotaWindowDTO` pushes the label back into per-client derivation.
- **Dictation availability probe** (`GET /api/dictate → {available}` so the
  mic button is invisible, never dead; `dashboard/http/get.py:180–184`). The
  design's 403-on-click is the race fallback, not the discovery.
- **Rewind modes as host vocabulary** — legacy declares modes AND their menu
  labels on `HostControl` (`plugins/host.py:362–372`) so the web and TUI
  cannot word the same restore differently; the design freezes
  `conversation|workspace|both` into the endpoint and renames legacy's third
  mode.
- **The attachment mention grammar** — `@path` is `HostControl.mention` with
  a bare-path fallback (the codex sigil-leak lesson,
  `dashboard/http/post/files.py:119–136`); the design has a
  `provider_path_token` and no grammar or fallback rule.
- **Pre-handler control-POST rejections unaudited** — legacy's `web-reject`
  row for CSRF/origin/read-only/oversize refusals
  (`dashboard/http/base.py:88–106`), the one place a control POST could
  vanish without a trace; the design's client-side 60 s begin-without-end
  cannot distinguish "never arrived" from "refused at the gate".

---

## Part 2 — Features the design describes, with gaps or contradictions

### 2.1 Critical — closure text that inverts the measured rule

**2.1.1 The sid-fork adoption liveness rule is inverted (§38.6:5065–5069).**
Verified verbatim: "a live predecessor … creates none" and the successor
lookup "refuses adoption while the predecessor is live". Legacy is the exact
opposite, necessarily: the note is written *by the live predecessor's own
hosted SessionStart* (`split.py:461–462`), and adoption *requires* the
predecessor still live — `adopt.py:101–103` returns early when the old state
DB is gone ("predecessor not live — stale note"; docs/mirror-pane.md:585–586).
The measured shape (docs/mirror-pane.md:541–553): `--resume` fires
SessionStart under the OLD sid seconds before the fork, and backgrounding has
no SessionStart at all — in both real cases the predecessor is live at
adoption time, so as written adoption fires in 0% of real cases and *only* in
the stale-note case legacy refuses. Compounding: nothing retires a note at
clean SessionEnd (legacy `adopt_drop`), and `expires_at REAL NOT NULL` (7035)
has no defined horizon while legacy's note deliberately has none (a
backgrounding fork can happen hours in; any finite expiry reintroduces the
un-adopted regression — 1,100+ events into a state DB nothing renders). The
regression signature is already a canned anomaly: "hook traffic under a sid
with no sessions row".

**2.1.2 The compaction-boundary fail-open direction is inverted
(§38.2:4614–4629).** Verified verbatim: a boundary outranks the last
assistant record "only when native-parent walking **proves** the boundary is
on the current live branch"; "a reverted **or unprovable** boundary is
ignored"; "a missing/unreadable branch proof fails open to the last assistant
value." The measured legacy rule is the opposite: `_boundary_live`
(`transcript.py:775–826`) **fails open to the boundary** on everything it
cannot prove — including precisely the window the override exists for, the
~22 records after a compaction when nothing yet descends from the boundary
(measured: probe reported 522,826 for a context holding 8,969; the boundary's
`preTokens` agreed with the stale record to 0.04%). Under the design text
that window is "unprovable" ⇒ boundary ignored ⇒ the 523k-vs-9k bug ships
again, wearing its fix's name. Also internally inconsistent with §38.13's
apply-then-correct revert handling and §13.4's latch rule. Fix: honour the
newest boundary unless the record-graph walk *positively proves* a revert
(the reverted case being the only drop; measured session c2442d36).

**2.1.3 OTLP `query_source` is put at the wrong level (§38.4:4893–4895).**
"Exporter **resource attributes** resolve the Conversation/AgentSession and
`query_source`" is measurably wrong: main, subagent, and auxiliary work all
run inside one Claude Code process with one OTel resource; `session.id`,
`query_source`, `model`, `type` are **per-datapoint** attributes
(`plugins/claude_code/otel/receiver.py:74–81, 108–115`; docs/otel.md:8–17), and one
export body may span sessions. A resource-level read collapses the auxiliary
bucket — the entire reason OTEL replaced transcript folding, measured at
11.6% of one session's cost — into whatever value dominates.

**2.1.4 The clean-install completeness gate still fails, at larger scale than
review 3 found.** §0.2 requires executable DDL for every table; §38.31 names
the fixture `storage_manifest_methods_resolve_to_real_tables`; §40.7 asserts
a final version-1 digest. Enumerating every `CREATE TABLE` outside superseded
§28 yields **142 tables**, and not among them: **(a) the entire
authentication persistence layer** — §38.36:10150–10169's `principals`,
`principal_role_bindings`, `auth_credentials`, `browser_sessions`,
`certificate_authorities`, `certificate_revocations`,
`invitation_credentials`, plus `bootstrap_credentials` (named only by the
operation manifest at 11313) — none created, and every `principal_id` in the
142 real tables is unconstrained TEXT with no FK, so `foreign_key_check`
won't catch it but the first `/auth/bootstrap` will; **(b)** manifest tables
that don't exist under those names (`credential_references`,
`conversation_title_facts`, `control_details`, `compaction_details`,
`provider_plugins`, `backend_health`, `repairs`/`repair_decisions`,
`workflow_checkpoints`, `blobs`); **(c)** `stream_frames` (11251, 11394)
listed as a *table* while §38.34:8796–8821 defines frames as *file* content
in BQSF format with an explicit SQLite-never-ahead rule — two sections
disagreeing about where the highest-volume data in the system lives. The
declared digest is a digest of an incomplete schema.

### 2.2 Major

**2.2.1 §40.3's Σ row contradicts §38.17, the DDL, and legacy.** Verified
verbatim (12552–12555): the row "shows total, **gross input**, output, cache
read, and cache write". §38.17:6006–6010 mandates the opposite for this exact
consumer (`fresh_input_tokens = max(0, input − creates)` "for a scoreboard"),
the DDL and ScoreboardDTO carry both columns correctly, and legacy's Σ row is
fresh input (`core/ops.py:634–647` `split_tokens`, "per-site re-encoding is
banned"). Showing gross input beside cache write double-displays creation, so
the five visible figures no longer partition the headline — destroying the
reconciliation with `claude --resume`'s "Usage by model" total that the row
exists for (`core/ops.py:609–621`). Only §40.3's prose is wrong — but §40.3
is the section an implementor reads to build the row.

**2.2.2 `terminal_bindings` cannot be rebound across resume/adopt/`/clear`.**
§40.7's DDL: `UNIQUE(terminal_adapter_id, backend_id, window_id)` (13205)
meets `pane_state … ON DELETE RESTRICT` (13223–13224) plus a revision-pinned
composite FK `(terminal_binding_id, binding_revision)` (13237–13238) and
`tab_paint_* ON DELETE CASCADE` (13381, 13391). A binding belongs to an
AgentSession *attempt* (§20.2:2631), so a resume in the same kitty window
needs a new row for the same `window_id` — forbidden by the UNIQUE, while
deleting the old row is blocked by the RESTRICT. No replacement protocol is
stated anywhere, though retagging a window on every fork is exactly what
legacy does (`adopt.py:190–210`, `split.py:346–351`). Consequences: paint
history cascades away for any window that ever hosted two sessions
(destroying the diagnostic value G33 was closed to provide), and the 1 Hz
`TerminalFrontmostPoller` writes (5375–5382) advance the binding `revision`
that `pane_state` pins with no `ON UPDATE` action — the two rules cannot both
hold.

**2.2.3 The logged-out fact has no clearing rule, and the obvious
implementation is the measured bug.** §38.18 creates the fact
(6031–6032); nothing clears it. Legacy: `usage.logged_out_active` holds it
until the account's freshest status-line snapshot is `LOGGED_OUT_GRACE_S`
(60 s) **newer** than the stamp — because the naive `stamp.ts >= usage.ts`
predicate was shipped and measured wrong (session 518b6f4d: Claude re-renders
its status line at the end of every turn *including the failed one*, ~0.3 s
after the stamp, so the dying session self-cleared its own badge instantly
while the audit looked perfect; docs/relimit.md:216–238). Content screening
is impossible (the snapshot reports healthy percentages) and
"proof of a successful turn" is documented-rejected (a bare `/login`
produces no turn). An implementor writes the naive predicate and reproduces
the bug exactly — invisibly.

**2.2.4 `background_tasks` is closed on the wrong event.** 10717 maps
`SubagentStop.background_tasks`. The load-bearing read is the **main
session's `Stop`** — the second, independent "am I awaiting the team?" test
(`tabstatus.py:622–629`), with the recorded reason that slot markers are
burst-scoped (a teammate idling between tasks has released its streamer, so
the payload is the more truthful signal). `Stop` has no mapping row at all
(1.1.1), so the design closes the child-side snapshot and drops the host-side
signal that makes §19.1's "open blocking work prevents false done" true.

**2.2.5 `attention_transitions` records no reason.** Prose says `cause`
(2564); the authoritative DDL has `cause_operation_id` — a nullable FK, no
text (9544–9553). Every transition that matters for triage in this area is
probe/timer-driven with no Operation to point at (bg-recheck, interrupt-watch,
escape-recheck, notification flips). Legacy's `tab_transitions.reason` free
text is the diagnostic backbone and the canned "bg-recheck painted green
mid-turn" anomaly matches on it (docs/tab-colors.md:270–275). The paint-side
attempt rows are a different plane; a skipped *decision* never reaches one.

**2.2.6 The mirror bias changed units and defaults.** §38.10:5341 "default 0"
and DDL `bias_cells … DEFAULT 0 CHECK(BETWEEN -200 AND 200)` (13231) vs
legacy: `CLAUDE_MIRROR_BIAS` is a **percentage of the tab, default 25**,
passed straight to kitty's `launch --bias` (`core/hostpane.py:42`,
`frontends/kitty.py:395–396`, docs/mirror-pane.md:823–828). The declared
import puts a percentage into a signed cell-offset column, and a fresh
install opens the mirror at width 0. (Adjacent `cell_step DEFAULT 4` is
correct. Related: `pane_state.percentage CHECK(BETWEEN 10 AND 90)` is a CHECK
on the *recorded observation* of an asynchronous resize — §20.3:2676 itself
says the observed value can land outside any requested range; the clamp
belongs on the request.) Found independently by two auditors.

**2.2.7 "Links appear only on the unwrapped label operation" (5309–5310)
forbids click-to-view entirely.** Copy links are indeed renderer-painted onto
label ops — but the click-to-EXPAND links are baked by the *producer* into
`line`/`gut` op text (a `Read(x.py)` one-liner, a generic-tool line, a
subagent file one-liner; `core/copy.py:71–106`, `core/ops.py:326–384`,
`bin/claude-mirror.py:245–302`, docs/click-to-view.md). Under the rule as
written a file one-liner — a whole block in one non-label op — can carry no
link, and the expand feature disappears for it. The 34-column threshold is
also a mis-transcription of a per-link-spec gate (`avail ≥ link_width + 24`
where avail = width − 2 ⇒ 36 for ⧉cmd ⧉out, 32 for a lone ⧉copy). Suggested
wording: copy affordances are renderer-painted on the block header;
expansion links are producer-declared on the block's own item.

**2.2.8 The viewport machinery is specified beyond its validity conditions.**
Three related findings. (a) The mandated global anchor search omits what
makes it affordable: the probe-row candidate narrowing and the lazily cached
ANSI-stripped line lists (`bin/claude-mirror.py:149–174, 536–548`; the drift
watch runs ~40–50 full-frame searches per toggle), and no §38.30 gate
measures a drift sample. (b) §38.9's restore/settle math (5279–5286) is pure
legacy math over an *append-only, immutable* buffer — its documented validity
condition (`bin/claude-mirror.py:157–160`) — but §14.4's
`move`/`retract`/`supersede`/`amend` can change an item above the anchor
mid-watch, at which point the settle guard corrects the viewport toward the
wrong content with a perfect-looking audit row; the design must say an
amendment forces re-locating the anchor by text or abandoning the watch.
(c) 5261–5262 mandates the entire machine (DSR handshake, global search,
drift watch) for "every full repaint caused by expand, collapse, resize, or
backfill" — in legacy it exists for exactly one cause, the user-initiated
click-to-view toggle (trackpad momentum racing the restore); a SIGWINCH
resize is a bare repaint and an append is `paint_new`. Mandating an 8-second
sub-second-cadence capture-and-search loop after every *backfill* on a
100,000-item conversation is machinery applied far outside the problem it
solved.

**2.2.9 §38.8 says WHEN code is formatted, never WHAT the formatting is.**
Timing, failure rule, highlight split, lexer cache, interpreter re-exec —
all pinned (5182–5189). The content is absent (`pretty`, `heredoc`,
`command position`: zero hits): bash reflow (break after top-level
`&&`/`||`/`|`; `;` → newline; block keywords own their line), the two
distinct indents and why they differ, the command-position rule (`echo done`
must not dedent), braces deliberately excluded (`${VAR}`, `awk '{print}'`),
the command-word retag pass, embedded-Python segmentation via `ast`, and the
`CLAUDE_MIRROR_FORMAT=0` escape hatch (`core/codefmt.py` in full;
docs/mirror-pane.md:88–121). Under the v5 bar this is invention left to the
implementor for the most-seen text in the pane.

**2.2.10 The mail fold-rule contradiction.** 5233's table folds `mail` in
`default`; 5236–5238's sentence says "peer messages … never fold in
`default`". Legacy: mail **does** fold in default
(`app.05-session.js:1046–1055` `VIEW_FOLD.default` includes `"mail"`); what
never folds is file edits/writes and the ⚠ line. The two mail rules work
together — folding the class AND hiding the poller plumbing (1.2.20) is what
removes the noise; an implementor taking the sentence gets every mail row
standing open with the plumbing (which the design cannot suppress) beside it.
If "peer messages" was meant as the conversation *bubbles*, one word of
disambiguation fixes it. Found independently by two auditors.

**2.2.11 Nothing says a codex-native subagent classifies as a CHILD AGENT,
not a codex run.** This is the rejected design of docs/codex.md:1179–1196,
recorded with its measurement: stamped `codex:<aid>`, an agent's entire run
folded into "Ran 1 codex run", no launch/result cards, tool calls as raw-JS
command blocks. The fix was one assignment — a native subagent stamps
`sub:<aid>` (the same register a Claude child uses); `codex:` means exactly
one thing, a sidecar inside a Claude host. The design has every vocabulary
piece (`track_kind` has both `subagent` and `sidecar`, 4497; `register` has
all three, 5224) and never makes the assignment; §38.37.7:10788's one child
row says only "actor track with separate task/turn keys". The guarantee
mechanism is also worth porting: `tests/test_l1h_child_agent_parity.py`
drives one synthetic sequence through BOTH hosts' renderers and compares op
kinds, stamps, copy-group topology, and derived classification with exactly
two declared differences; the design's fixture inventory is per-provider and
has no cross-provider parity fixture.

**2.2.12 The child's own model/effort ladder is named but its rungs are
missing.** §38.2:4633–4637 closes inheritance (`model: inherit` fall-through,
`resolvedModel` override) — good. Still missing: where a NON-inheriting
child's value comes from — the agent-definition file's frontmatter, located
by `customAgentType` (1.1.2) across **every ancestor `.claude/` dir**
(`model.py:41` `claude_dirs`, `$CLAUDE_PROJECT_DIR` honoured; the ancestor
walk is measured, not stylistic — a teammate in a worktree subdirectory whose
`cwd/.claude` lacks `agents/` read `effort: high` only because all ancestors
are collected, docs/subagents.md:160–171); the effort precedence
(`CLAUDE_CODE_EFFORT_LEVEL` env > def frontmatter > settings `effortLevel` >
model default); and the recorded caveat that a session-only `/effort max`
reaches none of them. Same gap one level down: `actor_track_context_state`
(4626) never says the child's context *window* may resolve from its
definition, nor that the parent transcript's `resolvedModel` upgrades the
window at completion (4635 invokes it for the model only).

**2.2.13 The hidden-auxiliary join is never made.** §38.6's
opener-missing Operations and §38.17's never-folded `query_source=auxiliary`
are both right — but nothing says a Claude child completion with **no opener
and no readable transcript** (the design's expression of "SubagentStop with
no SubagentStart and an `agent_transcript_path` never written",
docs/subagents.md:137–145 — one every ~35 s, ~$14 on the session that exposed
it) is the `auxiliary` bucket rather than `subagent`. And the artifact that
made it diagnosable — the dedicated anomaly plus the stop handler's three
decision strings (`subagent_fmt.py:178–187`) — has no successor because
§38.19's anomaly catalogue mechanism has no content rows yet (review-3 M29,
still open).

**2.2.14 Child-reader grace constants are absent from the inventory that
§12.5 requires.** §38.6:5010–5013 pins `FG_BACKSTOP_S` and the deliberate
bg/monitor asymmetry; missing from the same inventory: the child transcript
reader's stuck-streamer backstop (`core/tail.py BACKSTOP_S`, used at
`substream.py:498–500`), codex's per-turn grace (`CLAUDE_CODEX_GRACE_S`,
default 8 s, `stream.py:1093`), and the standalone-host exception (a host's
own rollout never ends on that grace, `stream.py:1104–1113`). §12.5:1833
demands every such constant be a named evidence rule; the design fails its
own gate for three constants it does not name. (Same class: `BG_MISS_GRACE_N
= 4` over a 2 s poll — the "N consecutive missing marker observations" rule
at 1827 gives no N; legacy's value and its teammate-burst rationale are
`tabstatus.py:125–127, 364–373`.)

**2.2.15 OTLP dedup solves false-dedup and leaves true duplicates
unacknowledged.** §38.4's `(listener_instance_id, receipt_sequence)` identity
correctly stops two legitimately identical delta exports from collapsing —
and thereby makes a genuinely *retried* body (exporter retry after a
consumed-but-unacknowledged request or daemon crash; the design itself notes
retry-on-non-200 at 4883) credit twice, with fixture
`identical_delta_receipts_credit_twice` (8587) enshrining it. The actual
disambiguator appears nowhere: OTLP datapoints carry
`startTimeUnixNano`/`timeUnixNano`, and a retry repeats the window while a
new delta advances it. Either add the window to the key or state the residual
— §9.1:1356 currently claims exactly-once canonical effects, and per-receipt
is not per-export.

**2.2.16 The `accounts` DDL cannot support its own endpoints or DTO.**
`AccountDTO` (10937) declares `execution_target_id`, `label`, `enabled`,
`priority`, `credential_state`, …; `GET /accounts` filters on target, `PATCH`
accepts label/enabled/priority, `POST` 409s on `account_label_exists` — while
`CREATE TABLE accounts` (8948–8958) has none of those columns and no label
uniqueness, and the document contains no `ALTER TABLE` anywhere.
Schema-locked territory (§36.2); must land before the digest freezes.

**2.2.17 Retention has no owner for the highest-volume classes.** §38.34
assigns retention to eleven classes; §38.26 declares the runtime-task
inventory exact; the only deletion workers are feed/blob/upload/telemetry.
Nothing deletes: raw Observations + provenance (the 200/s class, 30 days),
`ingestion_decisions`, command/tool payload evidence, `health_errors`,
anomaly runs, `effect_attempts`, notification intents/routes/deliveries, or
`otlp_receipts` (one durable row per HTTP export, ≈40k rows/day/machine,
in no retention class at all — two integers in listener state plus a bounded
failure ring would carry the same guarantee). Legacy's structural rule —
"a NEW audit table is prunable by default or explicitly classified", derived
from the schema and unit-tested (`core/audit.py:616–627`) — has no analog;
§38.30's classification is a health-report field, not a schema-change-time
declaration.

**2.2.18 `device_active` is self-contradictory, and the losing reading drops
duplicate-alert suppression.** 5899–5901 ("routing eligibility **only** …
never marks seen, never retracts") vs 5732 (the in-page toast "is the premise
for suppressing a duplicate external notification on an active device") and
the cancel-precedence list at 5865. Legacy: machine-wide activity is in
neither retract set — correct — but IS the third `_watching` channel that
holds/cancels a PENDING arm ("a focused page toasts every session; an
off-device push would be a second copy", `notifier.py:282–323`,
`presence.py:175–193`). Fix: machine-wide activity affects pending intents,
never delivered ones.

**2.2.19 `composing` must mean a SURFACE-origin buffer.** §19.2 lists
"composing an InputBuffer"; §38.16's table makes composing retract delivered
alerts of both kinds; `input_buffers.origin` includes `terminal`, and §8.5
syncs observed terminal text into terminal-origin buffers. Legacy keeps them
apart: `presence.composing()` reads only the web draft; terminal typing is
the separate reason `terminal-input`, applied only to a `done` arm and
excluded from retraction (`notifier.py:369–377`, rationale :62–67). As
written, stale text in a kitty box deletes the Telegram message for an
unanswered question.

**2.2.20 The CSRF contract makes the `pagehide` beacon flush impossible while
§38.14 assumes it.** §38.36 rule 3 requires `X-Baqylau-CSRF` on every
cookie-authenticated POST; `navigator.sendBeacon` cannot set headers, so the
telemetry flush 5638 relies on is 403'd. Legacy hit exactly this and accepts
a second proof (custom header OR present-and-allowlisted Origin,
`dashboard/http/base.py:117–146`) — the same comment carrying the paired rule
the design also never states: the CLOSE gesture was regressed by sendBeacon
(queued-then-dropped by the tunnel) and is deliberately plain `fetch`.

**2.2.21 §38.14's closed telemetry vocabulary drops families the design
itself mandates.** §17.5:2394 mandates shown/reconciled/dropped optimistic-
bubble telemetry; the closed six-family list (5621–5635, "unknown names are
rejected") has no family for it — legacy's `web-hint` (op × phase +
`wait_ms`, `dashboard/http/post/telemetry.py:18–62`, whose point is that
stuck greyed DOM is invisible server-side), and also `launch.arm/hit/timeout`
(did my new session appear), `meta.stuck/resolved/fail`, `backlog.fail`,
`composer.recall`, `close.reconciled`. `surface_control_attempts` is
begin/ok/fail only; none fit.

**2.2.22 The input-modality gate, applied to paste, kills the recovery
path.** 5690 blocks typing on unknown modality "even when input occupancy
says free". The legacy fix for the vim hazard is that bracketed paste is
mode-proof *by construction* (`tui.py:21–50`, recording the 2026-07-25
NORMAL-mode `/rewind` → `nd`-submitted failure) — and the design already
mandates bracketed paste for all text (5651). Since 5675–5678 set
`input_mode=unknown` after any failed/partial drive, the composer 409s
exactly when the user most needs to type. Scope the modality gate to
key-event drivers (menu digits, Space/Enter), not paste delivery.

**2.2.23 A dialog resolved AT THE TERMINAL must release input occupancy.**
§38.11 blocks send with `409 interaction_owns_input`, freed only by
"successful provider acceptance" (5473). Claude fires no hook on a decline,
so a question answered in kitty leaves the interaction open forever
daemon-side and the composer permanently 409'd. Legacy's `heal_stash` rule —
a drive-time proof of absence IS release evidence, with the failure mode
recorded ("a lingering stash made the modal gate refuse that very message",
`dashboard/http/post/dialogs.py:184–205`) — has all its materials in the
design (probers, the decline closer, `expired|lost`) but the sentence
connecting them is never written.

**2.2.24 `ActorTrackDTO.scoreboard` has no storage owner.** The only
scoreboard table is keyed `agent_session_id` (13013), the access table says
"exact AgentSession" (13589) — while §38.2:4623–4626 states the governing
fact that a provider child may have NO AgentSession, which is why the other
actor facets got `actor_track_*` siblings. This is the storage for the
agent-scope scoreboard swap (`dashboard/read/session.py:84–96` →
`plugins.agent_usage`, message.id-deduped; the docstring records why OTEL can
never supply it — aggregate by query_source, unattributable to one agent).
Schema-locked; needs `actor_track_scoreboards` or an explicit derivation
rule before the freeze.

**2.2.25 `ForegroundLiveDTO` cannot drive the live elapsed chip.**
`{running:boolean?, observed_at, …}` (13609–13610) vs legacy's `{g,
start_ts}` where `g` IS the mirror block's copy-group id — with the measured
note that codex's hook id, rollout call id, and the block's copy group are
three disjoint id spaces, so only the stream knows which block is running
(`plugins/__init__.py:1044–1062`). A bare boolean cannot attach the chip to
the right block, show true elapsed (`observed_at` is daemon-sight time, the
exact regression payload seeding fixed), or anchor a collapsed run's chip. If
the intent is "derive from the running Operation's identity + `started_at`",
say so; as written neither plane states the mechanism.

**2.2.26 §38.9's focus-mode sentence contradicts the measured behaviour.**
5239–5241: "mid-turn assistant prose is dimmed, never hidden". The code
keeps exactly ONE reply per turn (`!sawReply` else `"hide"`,
`app.05-session.js:1370–1387`) and dims only the newest, provisional reply
while the turn is in flight. Taken literally the design produces a focus mode
showing every assistant message at 50% opacity — the density the feature
exists to remove. (The adjacent "dimming is paint-only, cannot split runs"
rules are correct and should stay.)

**2.2.27 §38.3's import fallback classifiers are under-counted.** "The four
checked-in fixture-backed fallback classifiers" (4805–4809, closing review-3
G19 incompletely) covers bubbled prose and chrome; the legacy set measured
over 237 parked DBs / 159,757 ops also includes: the `legacy_*_note`
rewordings (history and today must read identically,
`actclass.py:275–306, 756–813`), the `lead_head`/`strip_who` identity strip
(without it every gate keyed on what a block opens with misses, and imported
blocks show another agent's name and ctx tags inside their text,
`actclass.py:343–374`), `cmd_note` quiet-register derivation, the
`mail:<row id>` synthetic subject reconstruction (`opshtml/ops.py:500–568`),
and the `diffstat`/`nf` parked fallbacks. Since §38.3 makes a missing
classifier an import *error*, the enumeration must be right.

### 2.3 Minor (compact)

- **`SessionStart.source` unmapped; mid-session SessionStart has no rule** —
  compaction fires SessionStart mid-session (`hostpane.py:285–286`);
  "attempt opener" applied unconditionally opens a spurious attempt (which
  2.2.2 then can't bind); codex's wiring uses a `matcher` field the manifest
  schema lacks (docs/wiring.md:181). Legacy's lesson: key on file existence,
  never on `source`.
- **§12.7's park sequence contradicts §38.20** — §12.7 orders "close every
  open correlation, drain and seal Streams" at end; §38.20 (correctly) keeps
  ingesting background output after host parking. One deferring sentence in
  §12.7 closes it.
- **Attention reconciliation after a daemon outage is unstated** — the turn
  that ended during the outage produced no Stop observation, so the
  projection stays `working` and the tab magenta — the stuck-colour class
  this subsystem exists to prevent; §26.1 step 9 covers sessions/bindings,
  not attention re-derivation from the transcript, and the paint dedup
  compares against a verified-but-now-wrong paint.
- **`tab_focused` must be `is_focused`, not `is_active`** — a tab selected
  inside a backgrounded terminal is `is_active` but not focused (verified
  empirically, `frontends/base.py:119–133`); an adapter reporting the
  selected-tab flag suppresses every alert for web-launched sessions — the
  case the feature exists for. 6619 takes the booleans with no definition.
- **Manual account migration is one-click with server-side selection** —
  legacy's ⇆ has no picker (same least-used selection and model ladder,
  ceiling relaxed, nudge suppressed; `hostctl.py:461–520`,
  docs/relimit.md *Manual migrate*); 6577 requires a target argument, and
  the "no account qualifies" 409 with audited picker reasoning has no named
  error.
- **No pre-decision live read of plan-dialog options** — ExitPlanMode labels
  vary with permission mode and are screen-read by a read-only gesture
  BEFORE any decision exists (`hostctl.py:650–665`); §8.4/§17.4 attach the
  live read to a control Operation, and the interactions GET is a read-model
  query that cannot probe a terminal.
- **The marker-SELECTION rule** — match the JSX-literal half of a screen
  marker, never a runtime-composed phrase (`MENU_FOOT = "to continue"`; the
  composed chord label broke every web rewind at v2.1.220,
  `rewindmenu.py:49–62`). §38.12's manifest+fixtures catch drift after the
  fact; this rule prevents it.
- **Suppression retroactivity, the second checkpoint, and statusline
  hygiene** — whether a new benign-signature suppression hides
  already-emitted ⚠ Activity items (legacy: yes, prospectively only); the
  per-session `errseen` vs `errseen-global` pair (global rows must surface
  once per session) with checkpoint-BEFORE-emit; the generic-window cap 32
  vs legacy's 8 and the `ts`-key rejection (`statusline.py:55, 81`).
- **The worktree NAME is missing from the closed overview DTO** — the card's
  separate `⋔ <worktree>` chip (`dashboard/read/meta.py:216–244`);
  `ConversationOverviewDTO` (10948) carries branch/owner-root/dirty only,
  and §38.23 calls that projection "complete".
- **`last_activity_at` declares no fallback ladder** (§18.7 requires one) —
  legacy's ladder and its two rejected alternatives are measured
  (`dashboard/read/lists.py:22–43`).
- **`register` bakes a provider name into a closed API enum** (5224 vs the
  open DDL at 8245) — the exact shape `core/agentblocks.py:78–88` removed on
  purpose ("a fourth host adds a word instead of a branch"); an OpenCode
  sidecar must wear `extension` or force an API change, against §10.5's own
  rule 8.
- **Codex pricing's refuse-to-guess rule** — version-exact prefix match; an
  unverified newer model shows NO cost rather than an older rate
  (`stream.py:178–199`); `pricing_epoch` cannot encode "unrecognised ⇒ no
  number".
- **§24.2 rule 7 vs the memory extension** — "never unsanitized HTML" reads
  as forbidding the `MemoryNoteDTO.html` field §40.2 requires; restate as
  "a declared, server-sanitized HTML field is a typed output".
- **§27.2 is vestigial** — fourteen prose index bullets at incompatible
  precision with the real named/partial/covering indexes in the DDL; delete
  or reduce to a pointer (this is the same two-owners disease review-3 R1
  named, confirmed empirically by 2.1.4b).

---

## Part 3 — Review

### 3.1 Verdict

The core of this design remains what the previous reviews said it was: the
right five-concept domain model, the correct decision not to event-source,
and an uncertainty discipline (closers, correlation identity, silence-never-
proves-success, requested-vs-effective everywhere) that is a genuinely
excellent distillation of this repo's measured history. The closure process
works: most of three reviews' findings are now truly closed, frequently
better than legacy (the per-field usage credit rule, durable alert delivery
handles, actor tracks, the ingestion-gap record, `baqylau repair scaffold`).
The auditors' verified-covered lists are long and several legacy mechanisms
are correctly obsolete *by construction* under the new model.

But round 4 exposes a failure mode the earlier rounds did not have a name
for. The document's remaining errors are no longer mostly omissions — they
are **confident normative statements of measured rules with the polarity
flipped**: adoption fires only when legacy forbids it (2.1.1), the
compaction probe fails open to the wrong side (2.1.2), OTLP attributes are
read at the level that collapses the bucket OTEL exists to capture (2.1.3),
focus mode dims what it must hide (2.2.26), the Σ row shows the figure the
adjacent section bans (2.2.1), `device_active` does the thing the same
section says it never does (2.2.18). Each of these *cites the right
mechanism* and would pass a vocabulary-level audit — they were only caught
by reading the closure text against the code line by line. A design whose
laws include "existing measured behavior is ported through fixtures and
parity, not memory" (law 37) and "a rule carries its measured
counterexample" (law 53) is breaking both laws in the layer that was written
to enforce them, which strongly suggests the closures were written from
review summaries and recollection rather than from the code.

The completeness is also still asymmetric in a hazardous way: the document
pins `CLEAR_GAP_S=0.15` and embeds SHA-256 digests of its own DDL, yet never
lists which hook events it subscribes to (1.1.1), never defines the price
table its two rollup keys reference (1.2.5), and digest-locks a schema
missing its entire authentication layer (2.1.4). Density of specification is
being read as completeness, including by the document itself ("VALIDATED
SPECIFICATION"). It is not yet either.

### 3.2 Architecture critique

**A1. The spec still has multiple owners per fact, and it is now the
dominant defect generator.** Review-3's R1 stands and has compounded: core
§0–37, §38 overriding "less-specific" wording, §40 overriding both, §28
retained inline with a do-not-execute warning, plus prose↔DDL↔DTO↔manifest
quadruplication. Every internal contradiction found this round lives on one
of those seams (§12.7 vs §38.20; §38.2 vs §38.13; fold table vs fold
sentence; §40.3 vs §38.17; prose `cause` vs DDL `cause_operation_id`;
manifest table names vs DDL names; `stream_frames` table vs BQSF file). The
v5 plan's decision to split authoritative artifacts into separate files
(schema.sql, openapi.yaml, per-contract docs) is the right medicine —
provided the prose *quotes* the machine-readable files and not the reverse,
and provided an executable cross-reference (every table/DTO/endpoint named
anywhere resolves) runs in CI. That check alone would have caught 2.1.4,
2.2.16, and half of Part 2's minors mechanically.

**A2. Write v5 from the code, not from the reviews.** The polarity-inversion
class has a specific process cause: a human (or model) summarizing "the
probe honours the compaction boundary with a liveness check" can flip the
default direction without noticing, because both directions sound like the
same mechanism. The counter-practice is already in the repo's culture: the
legacy code states decision rules as *directional* comments with their
counterexample attached ("FAILS OPEN (True) on everything it cannot
prove…"). Every closure that encodes a measured rule should carry the rule
in truth-table or decision-list form (condition → verdict, with the measured
session id), copied from the code, not paraphrased. Law 53 should apply to
the design's own text, and the v5 acceptance pass must include a
line-by-line comparison of every ported decision rule against its legacy
implementation — the one pass no round has done completely, and the pass
that found everything in §2.1.

**A3. The attention plane is under-owned relative to its blast radius.**
It is the product's most visible surface; it consumes nearly every other
subsystem's evidence; and it has no service contract, no store port, no
state→colour table, no reconciliation-after-outage rule, and a transitions
row that cannot say why it moved (1.2.9, 1.2.10, 2.2.5, 2.3). Meanwhile a
branch-name-and-dirty-bit store gets a full protocol. Promote
`AttentionService`/`AttentionStore` to the §38.25/38.26 treatment, put the
presentation table next to it, and give `attention_transitions` a `cause`
payload that survives probe-driven transitions.

**A4. The provider adapters' parse-side judgement is the least-ported and
most expensive layer.** Part 1's critical mass is not schema — it is the
accumulated judgement of *how to read provider artifacts*: the hook manifest
content, the teammate classifier, the codex fork-epoch/synthetic/plan/exec
rules. The design demonstrably *can* capture this class — it preserved
codex's `final_answer`-over-`last_agent_message` authority decision with its
rejected alternative — it just did so unevenly. These are exactly the rules
a rewrite loses first because they look like implementation detail and are
actually measured product behavior. Each one in 1.1.2–1.2.4 has a recorded
measurement and a recorded rejected alternative in docs/; porting them is
transcription, not design work.

**A5. The two most-debugged clients still sit outside every architectural
guarantee.** Review-3's R3 stands, extended by this round: the pane host is
"thin" in name and ~1,500 measured-behavior lines in fact (render/codefmt/
viewport machinery, §38.8+§38.9 rules), and the web side now inherits the
whole `opshtml` presenter contract (SGR→spans, OSC 8 → actions, code blocks,
markdown, view hooks) with no named owning module and no payload schema per
block type. Both need the §38.26-style ownership treatment, and §30.1's
architecture tests should cross the client boundary.

**A6. Say which legacy mechanisms are deliberately dead.** Several auditors
independently noted machinery that is obsolete *by construction* (park/
restore of per-session DBs, sid_chain on every query, `_merge_order`,
WAL-fingerprint caches, the mode=ro probe discipline, the no-writes-past-park
rule). Right now "not in the design" and "deliberately obsolete" are
indistinguishable from outside, which invites both false gap reports and —
worse — faithful reimplementation by an implementor working from the legacy
code. §31's phase plan should carry an explicit **dropped-by-construction
list** with one line of reasoning each; the auditors' Part-3 lists in this
round are a ready first draft.

### 3.3 Performance critique

The prior rounds' P1/P2 (single-writer math; blob fsync amplification, with
the fixes currently foreclosed by §40.6) remain the top structural risks and
map directly onto open v5 decisions 2/4/7 — not re-argued here. New or
sharpened this round:

**P1. Clock-derived fields need value-blind diff keys, not rate caps.**
§38.23 caps overview churn at one revision/second/Conversation — but
`active_time_ms` and ctx occupancy are clock-driven for every live session,
so the cap IS the steady state: a full DTO per second per live conversation
to every subscriber. Legacy solved this by excluding continuously-moving
fields from the change KEY while still shipping their values
(`dashboard/read/lists.py:229–240, 268–278`). State it as a rule: a
projection field that is a clock derivative must not by itself increment the
projection revision.

**P2. The 1 Hz presence write lands on the most contended row in the
terminal subsystem.** `TerminalFrontmostPoller` writes onto
`terminal_bindings` — the row `pane_state` pins by `(id, revision)` and
every pane/paint CAS reads (2.2.2). Presence is a per-device observation
with its own freshness; it does not belong on the identity row that gates
destructive writes. A `terminal_binding_presence` sibling removes the
contention and the FK conflict at once.

**P3. The per-actor fan-out is the real team-load multiplier and needs a
budget.** `actor_track_context_state` + `actor_track_runtime_revisions` +
per-track DTO (context + runtime + scoreboard) × the measured ~20 concurrent
actors is the shape that produced 2.2 MB/min pre-delta in legacy. §38.30's
team phase now exists (good); a per-actor projection write/emit budget
should sit beside it.

**P4. The viewport/drift machinery is mandated at 100,000-item scale with
its cost controls omitted and unmeasured** (2.2.8): no probe-row narrowing,
no line-list caching, no sampling cadence, no gate on a drift sample, and
the whole machine required after every backfill. Either port the cost
controls or scope the mandate to user-initiated reflows.

**P5. Unbounded growth is back, in specific tables.** `otlp_receipts`
(≈40k rows/day) and the retention-ownerless evidence classes (2.2.17) —
plus the scoreboard single-row hot spot across all sessions being an
*accepted* risk that §38.30 measures but never names as the deliberate
inversion of legacy's zero-cross-session-contention design. Name it, and
restore the "prunable by default or explicitly classified" schema-change
rule.

### 3.4 Over-complicated for no reason — keyed to the open v5 decisions

Verdicts on the pending review-list points (2–10), from this round's
evidence; full-scope (points 1, 11) is decided and not re-argued.

- **#2 (audit-record volume): keep the model, right-size the physics.** The
  durable inbox + provenance is what cracked the no-hook-on-cancel class and
  should stay. But note the ratio audit-infra established: v4 keeps raw
  evidence 30 days and owns totals in rollups, so the 7-transactions-per-
  observation and blob-per-payload cost is buying a 30-day diagnostic
  window, not a permanent audit. Pre-approve per-consumer batching and
  inline small-payload storage (both behavior-preserving, both currently
  banned by §40.6/§38.28) before Phase 1, not as post-gate remedies.
- **#3 (per-Conversation coordinators): keep.** Serialized mutation with
  durable open facts is load-bearing for everything in §12; a plain lock
  genuinely is not a replacement once probes and post-end amendments exist.
  The mailbox/overflow/parking spec is heavy but earns itself.
- **#4 (framed staging files): keep.** Crash-mid-stream is real, torn-tail
  recovery is measured legacy pain, and §38.34's frame spec is good. (But
  resolve 2.1.4c — the design elsewhere lists `stream_frames` as a SQLite
  table.)
- **#5 (activity protocol): keep materialization, scope the amendments.**
  Server-owned placement retiring the double merge implementation is a real
  win. The amendment vocabulary (`move`/`supersede`/generations) is
  justified by late child results and rewinds — but it breaks the viewport
  math (2.2.8b) and its invalidation storms are the benchmark's scariest
  phase; consider constraining amendments to generation bumps for anything
  above the live tail.
- **#6 (cursor replay): simplify toward resnapshot-first.** The
  24 h/100,000-event retained feed with exact replay is enterprise SSE for
  a single user whose client already must handle resnapshot (the design
  requires it on cursor expiry anyway). Resnapshot-on-reconnect as the
  *primary* path, with a short in-memory ring as an optimization, delivers
  the same UX with a fraction of the machinery — and deletes the
  feed-publication blob per event (P2's biggest contributor).
- **#7 (outbox breadth): carve out same-machine gestures.** Two rounds of
  area evidence now agree (review-3 O1, audit-control this round): interrupt/
  send/rewind compute their verdicts *inside* the gesture from screen probes
  (0.5–2 s), are non-idempotent, and can never be blind-retried — the outbox
  buys nothing and costs an async reconciliation path that legacy needed
  telemetry to debug. Keep the outbox for launch/resume, alerts, sagas, and
  feed publication; record same-machine gesture attempts after the fact.
- **#8 (restart-safe state): keep, with the declared live-only list.** The
  design already has the right shape (durable open facts + declared
  ephemeral facets); nothing found this round argues for less.
- **#9 (draft CAS): keep CAS/tombstones, drop blob-per-edit.** The
  resurrect-sent-text bug justifies the model; minting a content-addressed
  blob plus a feed event per keystroke-debounce does not (review-2's
  composer-churn numbers; §40.6 profile 2 now measures it — pre-approve the
  inline fix instead of measuring a shape everyone expects to fail).
- **#10 (early interfaces): keep the narrow protocols.** They are the reason
  the codex dual role and the null terminal cost nothing structurally. The
  real risk is not early interface *design* but early interface *freezing* —
  see A1; keep the protocols, regenerate the manifests.

Additions to the over-complication ledger from this round: `peer_messages`
carrying four required sender columns for a fact legacy derives from one
string with a fallback (and whose required `sender_actor_track_id` cannot be
satisfied by the lead's own send — the one sender with no name in any
payload); the `pane_state` percentage CHECK on an *observation* (2.2.6); and
the `register` closed enum with a provider name in it (2.3). Weighed against
these, this round also *confirms* several heavy mechanisms as justified —
framed staging, the inbox, CAS drafts, actor tracks — the complaint remains
misallocation, not volume.

### 3.5 What to fix first (ordered)

1. **The five polarity inversions** (2.1.1, 2.1.2, 2.1.3, 2.2.1, 2.2.26 —
   plus 2.2.18's self-contradiction). Each is a one-paragraph fix and each,
   as written, ships a measured bug with a closed finding's name on it.
2. **The hook subscription manifest content** (1.1.1) and the
   teammate/meta.json field list (1.1.2) — nothing in the attention or actor
   planes is implementable, or even testable, until the evidence sources
   exist on paper.
3. **Make the schema/manifest layer self-consistent and complete** (2.1.4,
   2.2.16, 2.2.24): the auth tables, the name drift, `stream_frames`, the
   accounts columns, actor-track scoreboards — then regenerate the digest.
   Add the executable everything-named-resolves check to CI so this class
   dies permanently.
4. **The codex parse-side judgement set** (1.1.3, 1.1.4, 1.2.1–1.2.4) —
   transcription work from docs/codex.md, highest bug-density-per-line in
   the whole gap list.
5. **The silent-failure pair**: logged-out clearing (2.2.3) and the
   OTLP retry double-credit (2.2.15) — both invisible in the audit when
   wrong.
6. **`terminal_bindings` rebinding protocol + presence split** (2.2.2, P2).
7. **The price table** (1.2.5) and retention owners (2.2.17).
8. **The test-isolation contract** (1.2.6) — before Phase 1 exists to hurt
   anyone, since v4's first phases are exactly the daemon+evidence layer
   whose legacy tests once paged a phone and truncated settings.json.
9. **The remaining UX-visible gaps** in rough user-impact order: click-to-
   view rule (2.2.7), focus errand boundaries (1.2.18), summary counting
   (1.2.19), mail visibility axis (1.2.20/2.2.10), scorebar row 0 (1.2.13),
   per-project width (1.2.12), errors tab (1.2.17), Σ-row wording (2.2.1).

One process recommendation to close: this design improves reliably under
adversarial review, but each round has audited a different projection of it
(coverage → schema → closures-vs-code). The v5 rewrite should build the
missing pass into its acceptance: for every rule that cites a measured
incident, a reviewer (or a test) must diff the v5 statement against the
legacy code path it claims to port — direction, default, and counterexample
included — before the artifact freezes. That is the pass that found
everything in §2.1, and it is mechanical enough to delegate.
