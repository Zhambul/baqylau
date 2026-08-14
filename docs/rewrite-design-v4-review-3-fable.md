# baqylau v4 rewrite design — review 3 (legacy-superset audit + architecture critique)

Reviewed document: `docs/rewrite-design-v4-codex.md` (12,677 lines, 2026-08-05 revision,
self-declared "IMPLEMENTATION-READY DESIGN — VALIDATED SPECIFICATION").
Review date: 2026-08-05.

**Method.** The core design (§0–37) and the structure of both normative closure layers
(§38, §40) were read directly. Six parallel area auditors then compared the design against
the legacy implementation, one per area: tab colors / hook wiring / session lifecycle;
mirror pane / streaming / scoreboard / rendering; subagents / teammates / mail / codex;
dashboard read side; dashboard control plane / notifications; audit / OTEL / usage /
accounts / core infra. Every "missing" claim below was verified by grepping the design for
the mechanism's vocabulary and synonyms before being accepted (the design's §38/§40
already close two earlier reviews, so most naively obvious gaps are in fact closed — the
findings below survived that check). Citations give design line numbers and legacy
`file:line` sources.

**Answering the three questions asked of this review:**

1. **Is the design a superset of the legacy system?** No — close, but not yet. About 20
   legacy features/mechanisms have **no coverage at all**, seven of them critical
   (Part 1). Two of the criticals are whole user-facing features (mirror view modes; the
   terminal-presence Telegram routing); two are the physical transport of the system's own
   authoritative cost source (OTLP listener; provider-config installation of the telemetry
   env and statusLine shim).
2. **Where does the design describe an existing feature but with gaps?** ~35 places
   (Part 2). The most dangerous class is where the design's text, read literally,
   **reintroduces a measured, already-fixed bug** — the sid-fork adoption schema, the
   post-compaction context probe, the single-Escape interrupt, the viewport restore, and
   the `asking`-alert retraction precedence all do this. A second class is
   **self-inconsistency**: the design's own completeness gates (§0.2) fail on at least
   five tables that its traceability matrix references but its "authoritative" five-unit
   DDL never creates.
3. **Overall review** — Part 3: what's genuinely good, architecture critique, performance
   critique, and where it is over-complicated for no reason.

Severity vocabulary: **CRITICAL** = blocks implementation or regresses a measured
production bug class; **MAJOR** = a real feature/lesson is lost or a contract is
unimplementable as written; **MINOR** = detail loss, cheap to fix.

---

## Part 1 — Legacy features with NO design coverage

### 1.1 Critical

**M1. Mirror view modes (verbose · default · focus) — the entire feature.**
The only trace is one clause naming `activity_class` as usable "for view filtering"
(design:5020). Missing: the three-mode vocabulary; the deliberately server-side,
per-session, cross-device preference (`dashboard/prefs.py` `view_mode`, chosen over
localStorage, survives park/resume) with its own SSE channel and own-echo suppression; the
per-mode fold table (file mutations, conversation messages and the ⚠ audit line never
fold); focus mode's dim-not-hide rule and its earned reason ("hiding read as content
vanishing"); the rule that dimming is paint-only and must not re-cut runs; the "N of M
shown" count. Legacy: docs/dashboard.md:7051–7135, `dashboard/opshtml/actclass.py:64-80`,
`POST /api/session/<sid>/viewmode`.

**M2. OTLP ingestion transport — the authoritative cost source has no listener.**
Zero hits for receiver/port/OTLP wire mechanics in 12,677 lines; the only mention is
§21.5:2778's "do not crash the listener" — a listener defined nowhere. Not in §29's
entrypoints, not in the 112-endpoint manifest. The design's universal wire rules
(§38.36:9505–9550 — closed objects, 1 MiB cap, X-Request-ID, authenticated principal) are
ones an OTLP exporter cannot satisfy. Legacy: `plugins/claude_code/otel/receiver.py` — 127.0.0.1:4319,
OTLP/JSON, gzip + chunked decoding, and the load-bearing always-200 rule (an error status
only earns an exporter resend loop). Needs a named loopback OTLP entrypoint with its own
contract.

**M3. Provider-config installation never writes the telemetry env block or the
statusLine command.** §38.4's subscription manifest installs hooks only. Legacy
`~/.claude/settings.json` must also carry `CLAUDE_CODE_ENABLE_TELEMETRY=1` +
four `OTEL_*` variables including the **delta temporality pin** that §21.5's "delta
temporality is summed" silently depends on (`plugins/claude_code/otel/__init__.py:15-19`), and
`statusLine` pointing at the capture-then-delegate shim (`statusline.py:6-17`) — the ONLY
channel for per-session 5h/7d rate limits. §10.3 specifies statusline delegation behavior
but nothing installs or verifies either. Without this, the authoritative usage source
never emits.

**M4. Nothing polls terminal frontmost, so presence routing to Telegram is dead.**
The reserved `terminal` device id cannot beat for itself; legacy's notifier polls
`Frontend.app_focused` on a cadence precisely to build the history the MRU pick needs
("I was at the terminal two minutes ago" cannot be recovered after the fact) —
`dashboard/notify/notifier.py:260-280`, `presence.py:166-172`. The design's
`PUT /api/v1/terminal-presence/{id}` (6219) is push-only and the complete runtime-task
inventory (6424–6508) has no worker that reads terminal focus. Consequence:
`device_presence.last_active_at` for `terminal` is never written, the MRU pick
(5544–5548) can never select it, and every alert routes to Web Push — the "you were last
at the terminal ⇒ Telegram, there may be no browser on that machine" route silently dies,
along with `tab_focused` suppression that rides the same poll.

**M5. Which child message IS the result: codex `phase: "final_answer"`.**
`final_answer`, `task_started`, `task_complete`, `last_agent_message`: zero hits.
`AgentTaskBlock(task_key, actor, phase, result)` (2665) has the slots but nothing says
which provider record fills them. docs/codex.md records two REJECTED alternatives
("whatever is pending at task_complete" — wrong in both directions; `last_agent_message`
as primary) and the pending-buffer flush rule (`_FLUSH_BEFORE` — flush only on records
that OPEN a block, because a trailing `token_count` demoted the result card). This is
also the parent-turn input `core/childtask.py:final_turn` reads, so its absence disables
the child-completion-after-parent-answer ordering the design promises at §14.1:1941.

**M6. The three Claude subagent cancellation closers have no evidence sources.**
§12.3:1786 requires closers for "subagent killed, rejected, or API failed" and §38.6
requires each entry to name its records — but `stoppedByUser`, `meta.json`, `toolUseId`,
`agent_transcript_path`: zero hits. Legacy: `substream.py:185` (meta.json `stoppedByUser`,
stat-gated 0.4s poll, monotonic latch, torn-read must not advance the signature);
`substream.py:430` (parent-transcript `tool_result` keyed by meta.json `toolUseId`,
`is_error=True` = rejected Task); `stop_fmt.py:42-44` (`StopFailure` carrying `agent_id`
routes to the SubagentStop finalizer). Worse: the design's one StopFailure row (10153)
says the **error enum** is the discriminator; for the third closer the discriminator is
the **presence of `agent_id`**, and it is exactly the case the parent `tool_result` cannot
recover (an async agent's ack is "launched successfully"). Without these, the child's
slot row — the tab's liveness signal — survives forever and wedges the tab blue.

**M7. The terminal text-layout contract does not exist.**
No hits for display width / wide chars / grapheme / tab stops / word wrap / gutters.
Legacy's five load-bearing rules, each with a recorded regression: cell-width (not code
point) accounting (`core/render.py:22-64`); tab expansion to 8 before width math (196-197);
word-boundary soft wrap that re-asserts live SGR after each wrapped row (227, 235);
single over-long-word hard break; width-dependent panel fill living renderer-side. Under
the design all of it collapses into one unspecified filename,
`clients/pane_host/.../renderer.py` (3833). Together with syntax highlighting (M8) this
is ~1,500 lines of the repo's most-debugged code with no owner inside the enforced
architecture (see Part 3, R4).

**M8. Syntax highlighting and command pretty-printing are unowned.**
`syntax`/`highlight`/`pygments`/`lexer`: zero hits. `render_kind='source'` (4973) exists
with no statement of what source rendering does, who tokenizes, or when. Legacy's split is
deliberate and measured: `code` op text pretty-printed once at op creation (audited on
formatter failure), view-body highlighting deferred to paint via `lex`/`num` (producer may
lack pygments while the renderer re-execs into an interpreter that has it), plus the
lexer singleton cache that undid a per-render regex-table compile
(`core/ops.py:279-348`, `bin/claude-mirror.py:19-71`, `core/render.py:356-367`).

### 1.2 Major

**M9. Server-side resume-picker search.** `GET /api/v1/conversations` (6144) has
project/provider/account/state/attention filters and no text-search parameter anywhere in
the manifest. Legacy searches title AND sid across a directory's WHOLE history
server-side because the client only holds ≤limit rows (`dashboard/read/lists.py:142-215`),
returning per-row `tool` so the form picks `claude --resume` vs `codex resume`.

**M10. The compaction ctx-bar animation contract.** The latch is covered; the
presentation contract it exists to drive is not: geometry frozen with brightness-only
breath, the rejected width-animation alternative ("size is the loudest channel a 9px bar
has"), the per-bar remembered width keyed `s:<sid>`/`a:<aid>` so the drop eases, and "an
agent's bar never rehearses". `CompactionLiveDTO` carrying `actor_key` (12660) actively
invites the actor-scoped compacting bar legacy forbids. (`app.04-list.js:344-407`.)

**M11. Codex's two-register duplication has no authority row.** A rollout says most
things twice (`event_msg` vs `response_item` — the latter the only source of a post-abort
or queued prompt), deliberately not unified (`plugins/codex/rollout.py`). §38.7's
authority table is explicitly "the initial Claude Code policy" with no codex rows, and
§38.7's own law ("a new family cannot ship until its authority row exists") thereby
blocks codex ingestion on an omission the design never flags.

**M12. Codex sidecar discovery specifics.** One clause at §6.2:609. Missing
(`plugins/codex/watch.py:22-38`): the byte-for-byte slug derivation
(`basename(git-root)+sha256(realpath)[:16]`, matching codex's own state.mjs); the
two-source dedup rule (companion jobs vs native rollouts, where the rollout uuid IS the
sidecar threadId); ownership of identity-less rollouts via an atomic per-repo claim
(`core/locks.py`) — without which the same run replays in every same-repo session; the
originator skip/adopt asymmetry between secondary and standalone modes.

**M13. Per-excerpt semantic line caps and the uncapped-result rule.** §38.8 gives byte
bounds only. Legacy carries a per-kind line-cap table (CAP_PROMPT 24 … CAP_BODY 60) with
an explicit "deliberately diverge from codex's caps" marker and the measured decision that
an agent's MESSAGE and RESULT are uncapped ("a long result is precisely the thing you
opened the mirror to read") — `substream_render.py:48-65`.

**M14. Team lifecycle-frame rendering (positive half).** §38.1:4534 pins only "lifecycle
mail is never rendered as prose". Missing (`msgs.py:102-161`): the `FRAME_PHRASE`
vocabulary (six types; the design's `peer_messages.kind` enum covers four), the
non-ordinary-qualifier rule, the `FRAME_TEXT` first-present body extraction, and the
unknown-type-still-gets-a-line rule. Measured: 12 of 14 arrivals in one lead session were
frames; painting raw JSON was explicitly rejected.

**M15. The screen-delta liveness primitive.** Two ANSI-stripped captures 0.5s apart;
equal ⇒ dead — replacing marker strings after two marker guesses each shipped a broken
verify (`hostctl.py:63-95`, 735-753). The design **depends** on it twice ("evidence shows
the turn refused to stop" 4884; "independent screen/native queue probe" 5397) and never
specifies it; the rejected alternative (literal matching) is what an implementor will
reach for.

**M16. The multi-press interrupt under modal editors.** The design sends one Escape
(4876–4882). With `editorMode: vim` the first Escape only exits INSERT — measured: every
real single-Esc interrupt on a thinking tab missed. Legacy presses up to 4 times while
the screen still animates, and only on a busy tab (a stray Esc on an idle box could open
/rewind) — `hostctl.py:139-241`. `agent_session_input_modality` (5383) makes the fact
representable; nothing requires the interrupt to spend a press on the mode transition.
This is the "web STOP did nothing" bug class, reintroduced.

**M17. Notification collapse tag and the resolve push are unencodable.**
`push_tag(sid)` is the one encoding shared by sender/retraction/service-worker — it makes
a repeat alert replace its predecessor and is what the resolve push closes
(`channels.py:64-69`, 245-268). The design's `PushPayloadDTO` (11964–11969) is a closed
schema with no `type` discriminator and no tag, and per-DELIVERY dedup means
asking→done→asking stacks three banners. §38.16's silent resolve push (5602) cannot be
encoded in its own payload schema.

**M18. No `query_source` (main/subagent/**auxiliary**) dimension in usage storage.**
§21.5:2782 promises the auxiliary bucket; `usage_facts`/`usage_credit_state`/both rollup
tables have no such column (actor_key exists on facts only, absent from the rollup keys).
The auxiliary bucket is the entire reason OTEL replaced transcript folding (measured 11.6%
of one session's cost, structurally invisible to folds — docs/otel.md), and the design's
own required CLI `baqylau otel <sid>` (5783) cannot produce its breakdown once raw facts
age out at 30 days (8192).

**M19. Legacy audit DB and dashboard prefs are never imported.** §38.3's importer scans
parked mirror DBs only. Nothing names `~/.claude/baqylau-audit/audit.db` or the prefs DB.
Consequences: Stats/Insights (computed from audit `sessions`/`otel`/`errors`) starts
empty despite §40.3's "preserves" claim; every durable preference — global alerts switch,
mutes, hidden dirs, dismissed tasks, ns-prefs/drafts — silently resets at cutover.
Adjacent: the parked-DB importer converts ops/streams but drops the `counters` table and
kv facets, so imported history shows a zeroed scorebar (against §40.3's claim that
`agent_session_scoreboards` is "the durable owner of the five-row scorebar").

**M20. The pane's ordinary append path, resize idempotence, and the post-restore
convergence machine.** §38.9 specifies full repaints only. Missing: append writes only
the new rows (repaint-per-append was the measured per-message flicker,
`claude-mirror.py:819-826`); an unchanged-size SIGWINCH must not repaint (the repaint's
clear-scrollback clamps a scrolled viewport, audited as paint-skip, :953-961); and the
entire verification/convergence machine after a viewport restore — delta-correcting
passes scrolling by measured error (never re-running the absolute amount), the
gross-miss ⇒ re-restore rule for trackpad momentum, the 8s drift watch defending the
intended anchor, and twin disambiguation by the caller's prior ("a perfect-looking audit
row for a real user-visible jump") — :417-436, 554-570, 679-706, 966-1012.

**M21. The scoreboard is a separate pinned terminal window, and geometry must exclude
it.** Nothing says the scorebar is its own window under the mirror (anything drawn in the
mirror scrolls away; DECSTBM would discard scrolled lines — docs/scoreboard.md:72-76),
nor that pane geometry passes `exclude_var="claude_scorebar"` because the bar shares the
mirror's column and counting it double-counts (`split.py:285-294`) — and kitty resizes
only relatively, so §38.10's absolute-percent endpoint needs that measured-geometry walk.

### 1.3 Minor (compact)

**M22.** Ask-card assistant preamble (`preamble_html`) — likely obviated by the activity
stream; the design should say so rather than drop it silently.
**M23.** Per-command refusal floors and their input (compact needs 2 prompts, argless
rename 1 — `hostctl.py:788-801`); §17.4 names "refusal floors" abstractly with no values.
**M24.** Machine-wide control-plane read-only switch (`config.READONLY`).
**M25.** `sql-write` successor unstated (repairs are registered; an unanticipated fixup
has no path — `auditcli.py:873-887`).
**M26.** No storage for a swallowed exception's traceback (`health_errors` has
code/title/remediation only; legacy `errors` carries the full traceback that
`baqylau-audit.py errors` prints — the primary debugging tool).
**M27.** grow/shrink cell step + `CLAUDE_MIRROR_STEP`/`BIAS` and the settings-layering
that resolves them (design's pane verbs carry no body).
**M28.** The warning-light surface's own recursion guard (audited once per process; ⚠
one-liners emitted into the ops plane so a healthy emission never feeds the poll —
`errwatch.py:39-47`). §9.6's guard covers the mapper path only.
**M29.** The anomaly catalogue's mechanism is ported but has no content, and no
requirement to classify each of the ~45 legacy `ANOMALY_SECTIONS` as ported or
obsolete-by-construction (see also G-class finding on hook manifest content).

---

## Part 2 — Coverage gaps: the design describes the feature, but not implementably

### 2.1 The design fails its own completeness gates (internal consistency)

**G1 — CRITICAL. Five tables are referenced normatively but never created.**
`terminal_bindings` and `pane_state` are pseudo-DDL sketches (§20.2) that the
traceability matrix binds to real storage methods and error codes (10648–10665);
`attention_projection`, `active_time_projection` (10590) and `conversation_overviews`
(10759) back listed endpoints/events. None appears in the five "authoritative" DDL units
(§38.35/§38.39/§40.7) — I enumerated all 139 `CREATE TABLE` statements. §0.2:156 declares
"one executable SQLite DDL schema for every table"; the design's own clean-install
verification gate (9463) should be failing. Two rows even disagree on whether the
overview is materialized at all. Adjacent prose/DDL mismatches: §38.6 says
`expected_revision` where the DDL spells `revision`; `attention_current` vs
`attention_projection` vs `agent_session_active_time` is a three-way naming disagreement.

**G2 — MAJOR. Closed enums vs extensible prose.** `cancel_reason` is CHECK-constrained
to 11 literals in two tables while §38.15/38.16 prose declares provider probers
"registered consumers of alert cancellation" and requires exactly one policy row per
registered pair with a startup health degrade when sets differ — so registering a reason
either cannot be stored or permanently degrades health. Same shape: `PushPayloadDTO`
(G-see M17), and `AnomalyDefinition` prose fields the `anomaly_definitions` table doesn't
store (only a sha256) while `AnomalyDTO` returns them — say the code catalogue is the
source of record.

### 2.2 Gaps that reintroduce measured bugs (critical/major)

**G3 — CRITICAL. The sid-fork adoption schema cannot express the legacy adoption.**
`session_adoption_notes` (6543–6557) requires `candidate_external_id NOT NULL` and keys
uniqueness on it — but the whole point of the legacy note is that **the successor's sid
does not exist when the note is written**: it's written at the predecessor's hosted
SessionStart keyed by CWD, and the fork's first event looks it up by cwd
(`split.py:461-462`, `adopt.py:99`, docs/mirror-pane.md:541–553). The table has no
workspace/cwd column, so there is no key an unknown sid can find a pending note by.
Compounding it, §38.37.6:10161 requires "positive continuation evidence" as a separate
conjunct — evidence Claude Code by construction never emits (legacy's guards are all
negative/circumstantial + the take-once note). As written, adoption never fires;
regression = 1,100+ events accruing into a state DB nothing renders, frozen scoreboard,
a tab that never repaints. Also missing: only a HOSTED start may leave a note (a skipped
daemon/headless start must never shadow the real predecessor), predecessor-liveness
refusal, INSERT-OR-REPLACE supersession semantics, and a fixture for the backgrounding
fork shape (no SessionStart under either sid).

**G4 — CRITICAL. Post-compaction context occupancy: §38.2 reproduces the 523k bug.**
Design 4566 makes checkpoints "not the current-state owner" and the newest
last-assistant record authoritative. The measured requirement is the opposite: a
compaction writes NO assistant usage, so the last-assistant probe reports the
pre-compaction figure (523k shown against a 9k context for 22 records); the fix honors a
`compact_boundary` NEWER than the last assistant record, matched as a record — and only
while that compaction is the LIVE branch, proven by walking the record graph from the
leaf (a reverted compaction writes nothing and leaves a boundary describing a discarded
context — measured 13,805 shown vs 223,546 held), failing open on everything unprovable.
Also: the display latch "expires on read" (5324) with no bound; legacy pins
`COMPACT_MAX_S`, the only thing that can end a latch whose arming hook died.

**G5 — CRITICAL. §38.1 rule 4 presumes a launch event carrying the actor key.**
"Launching a child creates the agent_task Operation and actor track in the same
transaction" (4492) is unimplementable for Claude Code: `PreToolUse(Task)` has the
description + tool_use_id but no `agent_id`; `SubagentStart` has the agent_id but no
description; the meta.json joining them isn't written until the child finishes
(`subagent_fmt.py:6-9`). The join needs the FIFO-with-scope-rules §12.2 already blesses
(1772) but never names for this case — including the measured resume hazard (a resumed
teammate fires SubagentStart with no preceding push; popping would steal another agent's
description, hence the persisted description + `↻` marker). §38.37.6 has no rows for
PreToolUse/SubagentStart/SubagentStop at all — an asymmetry, since the same table names
InstructionsLoaded, StopFailure, `uuid`/`parentUuid`. Same gap swallows the racy
`taskKind == "in_process_teammate"` retry-read and the duplicate-START guard (§12.3
covers duplicate stop only; a second header re-renders the whole transcript).

**G6 — CRITICAL. The scoreboard projection cannot render the Σ row it owns.**
`agent_session_scoreboards` (12295–12321) and `ScoreboardDTO` (10381) carry ONE
`cache_tokens` column, while §8.7/§38.17 correctly keep four cache categories and the
legacy Σ row renders four figures + total (`Σ 56M total · 428k in · 197k out · 55M cache ·
410k write`). Also lost: total ADDS cache read (reconciles with `claude --resume`'s
"Usage by model"; deliberately a different metric from billed spend); Σ renders first so
narrow-pane tail-drop keeps the headline; and `split_tokens`' invariant that usage
`input_tokens` INCLUDES cache creation so `tk_in` subtracts it (`core/ops.py:634-647`) —
the single-owner mapping whose per-site re-encoding is banned; `usage_facts.input_tokens`
declares no inclusivity and no per-source normalization (transcript=gross, OTLP=net,
codex create=0), so an authority swap (5651) silently changes displayed totals.
Schema-locked (§36.2) — must be fixed before the DDL freezes.

**G7 — CRITICAL. Viewport restore as written reintroduces "jumps to random places".**
§38.9 step 5 treats DSR as a geometry probe with a value; legacy never reads the value —
the reply's ARRIVAL is an ordering handshake proving the terminal parsed the pty bytes
before the rc-socket scroll races them (`claude-mirror.py:460-484`, 668-678), requiring
no-echo/non-canonical tty and the rule that DEC 2026 synchronized-output must NOT wrap
the handshake (kitty buffers rather than parses while frozen). Step 6's "scroll to the
absolute matched row" assumes an absolute-scroll primitive; legacy scrolls to END first —
the only deterministic base after clear-scrollback leaves scroll state "clamped somewhere
undefined" — then up by `total+1-h-j0`, with the follow-mode declaration and
post-toggle-bottom recompute. Step 4's global anchor search drops the confidence
threshold, the prior-based twin tie-break, and the audit-every-null-path-with-reason rule
that cracked the 4× anchor:null incident.

**G8 — MAJOR. `asking`-alert retraction precedence inverted.** Design 5560–5572 orders
`web_viewing` above `tab_moved`/`composing`; its policy row says web_viewing must not
retract a delivered asking alert — so a user who opens the session and starts typing an
answer keeps the stale Telegram message legacy deletes ("the tab moving off its alerted
state is the strongest signal there is" — `notifier.py:341-352`). Order by evidence
strength: state/truth changes above mere presence. Adjacent (same section):
`answered` retracts delivered alerts with no bound on the probe producing it — legacy
deliberately excludes screen-scraped signals from the retraction pass (a delivered alert
is tracked for hours; the design's rule implies a per-delivery `get-text` subprocess at
1 Hz for 24h); and `holding` needs the two-flag split (current-state vs fired-once latch)
with an explicit "suppressed probes resume on re-arm" rule, or a one-second hold loses
its terminal-answering cancels for the whole escalation window.

**G9 — MAJOR. Take-back / draft-restore demands "the exact text", which a wrapped
capture cannot supply.** 5202–5203. Legacy proves it via a 40-char prefix of a
whitespace-removed key (box wrapping joins lines without separators; the prefix keeps a
clipped tail from reading as mismatch), with the split that makes it work: the SCREEN
says whether a restored prompt exists, the TRANSCRIPT record says what it is
(`hostctl.py:850-917`). As written, interrupt mirror-back and rewind restore never fire.

**G10 — MAJOR. Model/effort inheritance for child actors is reversed.** 4576: "a
non-lead track never inherits host context, model, or effort." Right for context; wrong
for model/effort — Claude Code's `"inherit"` frontmatter maps to fall-through by design,
effort has a provider-documented precedence ending at the host (`model.py:193-197`,
354-362), and the authoritative child model (`toolUseResult.resolvedModel`, with `[1m]`
suffix) lives in the PARENT transcript at completion — child evidence in the host's
artifact, which "missing child evidence is unknown" doesn't contemplate. Under the law as
written every inheriting agent (the default) reports unknown, blanking the
`opus-4.8·high` tag on every agent header and web card. Also absent:
`CLAUDE_CODE_DISABLE_1M_CONTEXT` outranking every other window input.

**G11 — MAJOR. Model-switch confirmation cannot complete "within the same Operation"
when the command queues.** 6176/5258–5262 require the adapter to press Yes inside the
initiating gesture. Mid-turn, /model sits in the TUI's own queue and runs at the turn
boundary — there is no menu to wait for; legacy skips `_confirm` when queueing and lets
the late menu surface as the red-tab notification (`hostctl.py:557-568`). The design has
no "expected confirmation after this Operation closes" state, so its rule is
unsatisfiable for every mid-turn switch.

**G12 — MAJOR. OTLP delta dedup identity.** §9.3's generic key hashes payload bytes —
but two byte-identical OTLP export bodies are legitimately distinct under delta
temporality; an edge supplying no source_sequence collapses them into one Observation and
one usage_fact: silent token undercount no anomaly can see. §9.3's own principle ("false
deduplication is more damaging") argues for a per-receipt monotonic sequence; the OTLP
mapping must state it, plus how `source_record_key` is formed per datapoint.

**G13 — MAJOR. The benign-signature suppression registry has no storage.**
§38.19 makes suppression read-time "including to already existing rows"; §38.26 lists
"suppressions" as durable DiagnosticService state; fixture
`new_suppression_clears_existing_warning` requires it. No suppression table exists in
the schema; the only artifact (`anomaly_results.suppressed_by_registry`) is stored at
detection time — the opposite semantics. This is the whole global-errors-skill workflow.

**G14 — MAJOR. Completion-detection failure paths (three).** (i) Monitor matching by
process identity omits whole-command-match-wins + whitespace/escape-insensitive argv
normalization (`ps` renders heredoc newlines as `$'\n'`/`\012`/`\n`) and
ambiguous-multi-hit ⇒ not-found so the idle fallback closes the block
(`stream.py:461-512`; the measured silent failure chain in docs/streaming.md:106–129).
(ii) Writer-liveness must be async + throttled and FAIL-OPEN — a failed/hung lsof reads
"can't tell, assume writing", never "no writer" (a False there once ended a stream
mid-command); §12.5's "idle duration plus absence of a write holder" (1827) admits the
fatal reading. (iii) Foreground give-up is liveness-bounded (`FG_BACKSTOP_S=7200`),
deliberately NOT the bg path's flat 12s deadline (which painted "output not found" and
cleared the tab while the command ran on 40s more); bg/monitor deliberately have NO
backstop. The design pins no per-kind ceilings and never notes the asymmetry.

**G15 — MAJOR. Tailer byte discipline.** Read exactly `min(size-pos, cap)` (an
unbounded read grabs bytes appended during the read; the next pump duplicates them);
truncation response = restart from byte 0; and the checkpoint is THREE-way —
`pos - len(pending) - dropped` — the `dropped` term being what makes a mid-truncation
restart correct (`core/tail.py:115-170`). §15.4's two-way "last read vs last surfaced"
loses it. Adjacent: the 64 KiB cap can cut an escape sequence in half — legacy survives
because neutralize runs AFTER truncation and drops a dangling ESC (`render.py:147-167`);
§38.8 neutralizes categories but never addresses a partial sequence or the ordering.

**G16 — MAJOR. Pane budgets and caches.** The 4,800-row budget must derive from the
terminal's actual `scrollback_lines` minus a screenful (frozen 4800 against a 2000-line
kitty re-opens the measured clamp bug), with trim hysteresis and trim-before-measure
ordering (`claude-mirror.py:103-111`, 825-826, 874-877). The render cache's legacy
validity condition is "ops are immutable"; the rewrite's activity plane is explicitly
mutable (amend/move/supersede), so the cache must key on (item_id, item_revision, width)
— §20.3 says "renderer caches" and stops. Expand state needs its persistence contract +
the inherited-state-is-not-a-delta rule (a fresh pane otherwise opens scrolled deep into
history).

**G17 — MAJOR. Foreground→background transfer ordering.** §15.4 names the fields; the
legacy pins the sequence as un-reorderable (sentinel → measure offset → spawn; "losing
output is worse than showing it twice") and the offset-provenance rule appears twice for
two mechanisms: measure at the LAUNCH site, never at reader-open time — output landing
during reader startup is otherwise permanently lost (docs/streaming.md:58-66, 270-297).

**G18 — MAJOR. Semantic child-task order is never stated as the invariant.** §14.2's
inputs (source position #2 above causal links #3) read literally reproduce the measured
inversion (`Agent finished` sorting after the answer it fed — session 019fb66b);
the rule — a child task's result belongs before the final response of the parent turn it
ran in, whatever the clocks say — plus "the RECORD moves, never the op" (op ids are the
cursor backbone) and the inert-for-Claude turn rule, are all absent. §38.21 covers
correcting placement, not placement precedence.

**G19 — MAJOR. Legacy import does not reconstruct audience/register for pre-flag ops.**
§38.3's importer says nothing about deriving `bubbled`/`chrome` for rows written before
the flags existed; legacy carries four measured fallback sniffers for exactly that
history (17 of 25 parked sessions in the corpus hold such ops). Without a derivation
rule, imported history double-renders prose and shows host chrome every web view drops.

**G20 — MAJOR. `CLAUDE_AUDIT=0` semantics leave the warning light undecided.**
The design keeps canonical evidence under the switch and disables "optional diagnostic
provenance" — but the swallowed-exception plane is a product feature (scorebar ⚠ chip +
one mirror line per error row). State whether health errors/anomalies are in the optional
set.

### 2.3 Minor gaps (compact)

**G21.** ConversationOverviewDTO is closed ("no others") yet lacks ctx bar, git chip,
cmds/tokens/cost, recency — the legacy list card cannot be rendered from it, and none of
those can be per-conversation fetches at 50–131 rows/tick (measured 2.2 MB/min pre-delta).
**G22.** Git identity has no storage column or DTO field; `git_info`'s
branch-or-detached-sha, worktree→owner root, `DIRTY_TTL_S=10` matched to the slow SSE
cadence, and timeout⇒unknown-cached are all unplaced (§38.23 separates identity from
dirty and then stores only dirty).
**G23.** Stats window counters (7d/30d/all Pulse strip) and the active-count lesson
(ended_at-IS-NULL inflation; host_state=lost largely fixes it — say so).
**G24.** Per-tab badge counts: the deliberately-cheap sources and the `scoped` bit
(without it an agent with 19 jobs read "jobs 1").
**G25.** Memory tree subtree rollups, the stable-sort rationale, out-of-vault basename
fallback.
**G26.** NS-draft directory settle-on-blur and `NS_DRAFT_MAX` pruning.
**G27.** Alert-delay composition: `done` waits **max(delay, settle)**, not sum
(`notifier.alert_delay` is the declared owner); the design lists both knobs and never
states composition.
**G28.** Bracketed-paste mechanics: submit CR outside the paste; `CLEAR_GAP_S=0.15`
between clear and paste (measured leading-byte mangle); the raw-send "test"→"t"
counterexample.
**G29.** Relimit details: the limit message TEXT as third evidence source
(`limit_model` scoping — which 5722 requires the policy to respect — and `limit_reset`
tz parsing fixing the lit-for-hours pill); the nudge's model-downgrade clause;
`COOLDOWN_S=600` per-session; limit-hit stamped before enable check and target selection.
**G30.** Host model vocabulary: `model_match="family"` vs exact, `model_short`,
default-effort — without it the ✦ menu can't mark the running row.
**G31.** Dictation grant loosened: legacy binds model+keyterms+sample-rate server-side
and returns the assembled URL; the design hands key_terms to the client at 60s.
**G32.** Capability-resolution failure direction: legacy degrades OPEN ("a read error
must never disable a real Claude session's control plane"); two adjacent unknowns
correctly fail closed; the third case is unstated.
**G33.** Tab-paint audit parity: legacy writes a row on applied AND both skip paths AND
failure ("state row unchanged") — the design's deduped paint produces neither a
transition nor an attempt row, making "the tab is wrong and nothing tried to fix it"
unanswerable from the DB. Also: nothing stores the last VERIFIED painted presentation
(the dedup must compare the last SUCCEEDED attempt or the stranded-colour bug returns),
and nothing requires the inactive-tab colour (the feature's entire premise is reading
state from another tab).
**G34.** Statusline payload details: `resets_at` seconds-vs-ms ambiguity (>1e12⇒ms);
never overwrite a good window with nulls; generic window keys with hygiene + cap.
**G35.** Stop payload's `background_tasks` and the `agent_type` counter-lesson (filtering
on agent_type left an orchestrator tab stuck magenta — confirmed live); the subagent
meta.json and Ctrl+B payloads are absent from §38.37.6's closed field mapping while
§38.37.9 makes an unmapped field unsupported.
**G36.** Display honesty rules: blocked chip carries NO duration; zero-output bg job
paints `(no output)`; narrow-pane drop order (Σ total first, ⚠ leads its row);
slot-palette cross-family constraints (no red/green in the subagent family; teammates
REUSE subagent slot rows — one namespace, two palettes, which `(entity_kind, slot)`
would model as two); markdown fence-sniff late binding for `render_kind`.
**G37.** Probe-safety framing of the ghost suggestion (a red/modal tab makes the ❯
region the dialog's input — garbage; host-owned input_box with inert base), and the
typed-half-on-ANY-tab asymmetry.

---

## Part 3 — Review

### 3.1 Verdict

This is the strongest document in the series, and most of its core is genuinely good.
The five-concept domain model (Conversation/Node/AgentSession/Operation/Stream) is the
right decomposition; the decision NOT to event-source, with provider artifacts as truth
plus retained boundary evidence, is correct and well argued; and a striking number of
hard-won legacy lessons are not just carried but *improved* — the closer catalogue,
escape-recheck baselining, slot-release-before-attention as law 47, queue-drained
evidence outranking a stale screen, the per-field positive-delta usage credit (which
subsumes four legacy mechanisms and the 2.2× double-count), rename ownership, adoption's
InstructionsLoaded negative mark, the SGR/OSC-8 allowlist, and the mailbox census
semantics all check out against the measured record. The auditors' verified-covered
lists are long; this review's findings are the residue after that filter.

But the document is not what its status line claims. "An implementor can write the code
without making a product decision" (§0.2) fails on its own terms: five referenced tables
have no DDL, several closed schemas cannot encode behaviors the prose mandates
(G1, G2, M17, G6), and at least six places specify a mechanism in a form that
re-ships a bug the legacy already paid to find (G3, G4, G5, G7, G8/M16). The closure
sections closed the two earlier reviews' findings; they did not close the design against
itself.

### 3.2 Architecture critique

**R1. The document has become what it forbids in code: multiple owners of one fact.**
Core §0–37 states a rule; §38 overrides it "when less specific"; §40 overrides both; the
superseded §28 SQL is retained in-line with a do-not-execute warning. Prose fields
disagree with DDL spellings; three names exist for the attention store. The single-owner
rule the design (rightly) imposes on modules should be imposed on the spec: fold the
closures into the sections they amend before implementation, or every implementor
performs the three-layer merge independently and some will get it wrong.

**R2. The daemon as a single availability boundary is a real product regression and
deserves a franker accounting.** Today, tab colors need only a hook process and the kitty
socket; the pane reads the same SQLite the producers write and works while everything
else is down; a toggle recovers full history with one indexed query. Under v4, every
feature — paint, pane, audit capture, alerts — is dark whenever the daemon is down,
during every upgrade, and through supervisor backoff (up to 60s), with evidence from the
gap "permanently absent" (§26.2). §9.7 is careful that a *hook* never fails when storage
is down; no equivalent care is spent on *surfaces*. The decision is coherent and stated
(§0), but it trades away the property that made the legacy a flight recorder: it was most
useful precisely when things were broken. At minimum, give the pane a local cold-start
read path and reconsider the tab-paint fast path (R6).

**R3. The most-debugged code in the repo lands outside every architectural guarantee.**
`clients/pane_host` sits outside §3.4's single-owner rule, §29's import rules, and
§30.1's architecture tests, yet must implement the terminal layout contract (M7/M8), the
viewport machine (G7/M20), and block composition. One filename currently stands in for
~1,500 lines of measured behavior. Either fold the renderer vocabulary into
`presentation/` with a declared owner or extend the architecture tests across the client
boundary.

**R4. Policy-as-DDL is the wrong rigidity in exactly the places policy was learned by
measurement.** The 22-row `notification_retraction_policy` with no-update/no-delete
triggers, and the lead-head trigger cascade (`lead_track_head_projection` +
`conversation_head_is_derived` on top of a deferred-FK preallocation dance), both encode
rules that will be re-measured — the alert policy demonstrably (the 20s knee came from a
46-push analysis; the next analysis will move it), the head invariant testably. A seeded
registry in code asserted at startup, or one application-level writer plus an
architecture test, gives the same guarantee with a stack trace when it breaks and no
schema migration when policy shifts.

**R5. Where the design is genuinely better than legacy — mark these so they survive
implementation pressure:** actor tracks over src-string prefixes (designs out the
codex-native-subagent misclassification and the whole REGISTERS table); server-owned
placement retiring the merge_live/_merge_order duplication; the usage credit rule (G6's
schema bug notwithstanding); environment `presence_state` with "absent never overwrites";
`client_upgrade_required` over the BOOT_ID toast; the task-set digest; interaction CAS
against stale cards; no-edge-fallback-service (eliminates the process-group-hang class by
construction); and the anomaly-catalogue commit discipline, which is a strict port of
CLAUDE.md's dual-update rule.

### 3.3 Performance critique

**P1. The single-writer math is the central bet and the design's own rules fight it.**
Seven independent consumer transactions per Observation at 200/s (bursts 1,000/s) is on
the order of 1,400 commits/s through one FIFO writer with `busy_timeout=0`, replacing a
legacy that has zero cross-session write contention by construction (per-session DBs,
~20 short-lived processes). WAL commits are cheap, so the gates (admission p95<25ms, txn
p99<50ms) look reachable — but only with aggressive coalescing/batching, which §38.30
lists merely as an allowed *corrective* after a failed gate while §40.6 forbids
savepoint fan-in. Make per-consumer batching a pre-approved design property, not a
remedy discovered at gate time.

**P2. Blob fsync amplification, with the fix foreclosed.** 95% of the storm's payloads
are 0.5–8 KiB, each paying exclusive-create + fsync(file) + rename + fsync(dir) before
its metadata commit — 400+ fsyncs/s for what is today one row in an audit table. §40.6
explicitly disallows inline payloads. Pre-authorize a size-thresholded inline store for
evidence-class payloads (30-day, diagnostic-only); it is behavior-preserving and removes
the sharpest risk in the whole benchmark.

**P3. Tab paint: a ~0.1 ms effect acquires a 200–750 ms budget and a durable row per
paint.** Legacy deliberately removed a 20–100 ms kitten subprocess from this path
(docs/tab-colors.md:46-53); the design routes every paint through
outbox→lease→attempt→receipt→Observation→attention recompute and forbids bypass (§38.28).
Tab color's entire value is immediacy. Declare paint an idempotent, coalescing effect
with a synchronous fast path (see R6/O1).

**P4. The pane gates measure the cheap half.** §38.30 gates byte availability (250 ms
p95) — whose dominant term, the coalescing window, §15.3 leaves unnumbered, making the
gate unfalsifiable — and never gates the actual dominant cost: the mandated full repaint
at the 8,000-item ceiling, toggle→stable-viewport time, or highlight cost. Legacy's
intermediate frame lives ~1 ms; nothing holds the rewrite to that.

**P5. No agent-team benchmark dimension.** The known heavy case (one conversation,
20 actor tracks, measured 2026-07-27) stresses exactly what §38 adds — per-actor facet
writes, per-turn runtime revisions, broadcast delivery rows, activity_links fan-out —
and appears in no benchmark phase; §27.3 says only "several AgentSessions".

**P6. Unbounded growth.** v4 removes the legacy's only automatic size control (30-day
pruning at SessionEnd, with prunable-by-default table discipline) and retains canonical
rows until manual owner purge. The only size alarm in the document is for the WAL. Add
an age-based archive class or a DB-size health gate, and keep the "a new table is
prunable by default or explicitly classified" rule.

**P7. Read-path churn.** If the overview projection gains the fields it needs (G21),
every ctx observation and dirty-probe expiry bumps a principal-scope revision across
131 rows — legacy solved this with deliberately blind diff keys (payload exact, diff
blind); state per overview field whether it bumps revision or is computed at read. The
retraction scanner needs its cost bounds back (narrow reasons, screen=False pass,
SENT_CAP). And with no current-attention table (G1), the 1s alert diff and the list page
both become latest-row-per-scope over an append-only table — the shape the design itself
forbids at 5853.

### 3.4 Over-complicated for no reason

**O1. Transactional outbox for same-machine terminal gestures.** Durable leases, attempt
rows, `indeterminate` reconciliation and 202-plus-SSE for pressing a key in a terminal
5 ms away — while the legacy's synchronous press-verify-respond loop is precisely what
makes the four-press interrupt and queued-send verification *work* (and the button feel
connected). Keep the outbox for launch/resume, push/Telegram, and sagas — where effects
are genuinely remote or multi-step and legacy already detaches — and carve out
synchronous same-machine gestures that record an attempt row after the fact.

**O2. Pane resize as a durable workflow.** Five endpoints, mandatory Idempotency-Key, an
Operation, an outbox effect, an attempt, a receipt — for a kitty keybinding. The real
content (kitty resizes relatively; geometry must be measured; helper window excluded) is
the part the design *doesn't* specify (M21). The one genuinely valuable addition is the
`ambiguous_terminal_focus` error naming a failure legacy returns "" for.

**O3. Signed, nonce-bearing OSC 8 action URLs** for links in the user's own pane,
handled by the user's own open-actions handler, against a threat model (§25.1) that
doesn't include the user's own terminal. It also self-contradicts: the nonce must outlive
a scrolled-back parked block whose Copy §38.8 promises "for as long as the block is
retained" — so it either expires (breaking the promise) or never does (voiding the
signature). A local-authority check is the honest design.

**O4. Five usage tables + a pricing_epoch dimension replacing four integers — while
missing the one dimension the tooling is asked for** (per-actor / query_source, M18).
pricing_epoch is derivable at read time from observed_at + the price table — which is
the stated reason cost is computed on read. If only one dimension can exist, trade the
derivable one for the needed one.

**O5. Immutable seeded policy tables** (R4) — 22 retraction-policy rows plus guard
triggers for what legacy expresses as two frozensets and a three-line function, learned
from measurement and certain to be re-measured.

**O6. The dedup-key hash ceremony vs its actual blind spot.** §9.3 specifies
length-prefixed SHA-256 serialization to seven components — and then the one source
where dedup is genuinely dangerous (OTLP deltas, G12) has no defined sequence component
at all. The ceremony is in the wrong place.

Weighed against these, several "heavy" mechanisms are *justified* and should stay:
framed staging files with torn-tail recovery (crash-mid-stream is real), the durable
Observation inbox (the no-hook-on-cancel class was only cracked by always-on evidence),
CAS drafts with tombstones (the resurrect-sent-text bug), and actor tracks (C1 above).
The complaint is not "too much machinery"; it is machinery misallocated — heaviest where
effects are lightest (O1–O3) and absent where the legacy actually bled (Part 2).

### 3.5 What to fix first (ordered)

1. **G1 + G2** — make the schema/DDL/DTO layer self-consistent (five missing tables,
   closed-enum-vs-prose conflicts, PushPayloadDTO). The design's own §9463 gate should
   run and pass. Schema-locked items (G6's cache columns) must land before the freeze.
2. **G3, G5, M6** — the identity/lifecycle joins Claude Code actually emits: cwd-keyed
   adoption notes, the launch/actor-key FIFO join, the three cancellation evidence
   sources. These are the "tab wedged blue / frozen mirror" class.
3. **G4, G7, M16/G8** — the re-shipped measured bugs (compaction probe, viewport
   restore, single-Escape interrupt, retraction precedence).
4. **M2 + M3 + M18/G12** — the telemetry pipeline end to end: listener, installation,
   temporality pin, query_source, delta dedup. Without these the cost numbers are wrong
   or absent, silently.
5. **M1, M4, M9** — the three missing user-facing features (view modes, terminal
   presence routing, resume search).
6. **M7/M8 + R3** — write the terminal layout/highlighting contract and put the pane
   host inside the tested architecture.
7. **P1/P2** — pre-approve consumer batching and inline small-evidence storage before
   Phase 1, not after a failed gate.
8. **M19/G19** — legacy data import: audit DB, prefs, scoreboard counters,
   audience/register derivation. Cutover without these silently zeroes history the
   product promised to keep.

A final note on process: the two closure sections demonstrate that this design improves
reliably under adversarial review — most of what the earlier reviews found is now
genuinely closed, often better than legacy. The residue above is concentrated where no
reviewer had yet compared the *normative closure text itself* against the measured
legacy record line by line. One more closure pass of that shape — plus an executable
check that every table/DTO named in the traceability matrix exists in the DDL — and the
"superset" claim would be defensible.
