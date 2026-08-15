# Handover: the recorder/interpreter redesign (2026-08-15)

A complete account of the architecture rewrite performed on 2026-08-15 and the
live-fire fixes that followed the same day — what changed, why each decision
fell the way it did, what is deployed, and what is deliberately left open.
Written for whoever works on this codebase next (human or agent).
`docs/recorder-interpreter.md` is the normative description of the result; this
document is the narrative: the road, the rejected turns, and the state of the
world at the end of the day.

Commits, in order: `1dad4f9` (HarnessInfo consolidation, prior work) →
`ac90b5b` (the redesign) → `8d8ec1a` (background watches) → `b7868ee`
(evidence-driven registration + pane/stub fixes) → the send-verification +
terminal-anchor commit that ships with this document.

---

## 1. Where it started

The pre-redesign system already had the canonical event model (raw evidence →
translation verdicts → idempotent canonical facts + provenance; see
`docs/canonical-harness-architecture.md`, which remains authoritative for that
layer). What prompted the rewrite was a review of how sessions, hooks and
observation actually flowed around that model:

- **Every hook invocation built the entire application graph.**
  `record_hook()` called `build_default_application()` — ~25 services, two
  databases, all plugins — to insert one or two rows and exit. Every single
  Claude Code tool call paid that import bill, and any bug anywhere in
  bootstrap could lose evidence.
- **Hooks translated.** Hook-delivered raw events were translated inside the
  hook process with whatever code was on disk at that moment, while the server
  translated pulled events with the code it imported at startup — two
  translator versions running concurrently against one store.
- **Session identity was smeared across four types** (`RecognizedSession`,
  `RegisteredSession`, `SessionCandidate`, `SessionRecognizer`) and two owners
  (`HarnessRegistry` did session lookups through the `EventStore`, which owned
  the session tables and defended them with `_verify_registered_session`,
  `actor_harness`, `HarnessOwnershipConflict` — paranoia checks for writers
  that no longer existed).
- **Registration was upsert-and-merge**: hooks re-registered on every
  invocation, identity columns were first-writer, hint columns refreshed, the
  pid was fill-once. A session row was not a fact; it was a negotiation.
- **Coordination hid in files**: foreground streaming used manifest/`.done`
  files under the Claude config dir; observation progress lived in a separate
  `source_checkpoints` table whose drift from the evidence was the single most
  diagnostic bug shape in triage.

## 2. How the design was reached (the dialogue, decision by decision)

The design was iterated in conversation before any code. Each round tightened
it; recording the rounds matters because several "obvious" alternatives were
explicitly rejected and should not be re-proposed:

1. **"One `Session`, one owner."** `RecognizedSession`/`RegisteredSession`
   merged into a single `Session` carrying `plugin` as an attachment field
   (`compare=False`; identity is the data, the plugin is a convenience the
   server-side `SessionRegistry` attaches). `SessionRegistry` became the single
   point of registering and handing out sessions; every paranoia check in the
   store was deleted rather than relocated.
2. **"A process either appends evidence or interprets it, never both."** The
   generalization that drove everything else. Recorders (hooks, the otel
   receiver, the wrappers) append `raw_events` and exit; exactly one process —
   the interpreter — reads, translates, and reacts. The database is the only
   rendezvous. This kills the whole-app-graph-per-hook problem *and* the
   two-translator-versions problem at once: translation happens only in the
   interpreter, from the untranslated backlog (`raw_events LEFT JOIN
   translation_records WHERE verdict IS NULL`, ordered by arrival).
3. **Sources became pure readers.** `drain(delivery)` — a source that both
   produced and wrote events — was rejected as a single-responsibility
   violation. The protocol is `read(after_position) -> tuple[RawEvent, ...]`;
   the interpreter records. The position is not stored anywhere separate: **a
   source resumes from the `source_position` of the last raw event carrying its
   `source_identity`** — `source_checkpoints` was deleted, and progress cannot
   drift from evidence because it *is* the evidence. The invariant that makes
   this sound: a source may only advance past input by emitting it as
   evidence. Position encodings are source-private (line-start plus
   skip-one-line for transcripts/rollouts — the translators key on line-start
   positions; chunk-END for watch chunks — arbitrary slices of a growing file
   must not be re-read from their start; a snapshot digest for the task files,
   carried by the membership event emitted last; a started/finished latch for
   process sources).
4. **No cap on observed sessions.** The old `RECENT_SESSION_COUNT = 4` was a
   quota where liveness is an evidence question. `watchable()` is every
   session without a committed `session.finished`; an up-to-date source costs
   one stat per tick.
5. **Panes are not per-harness.** `ClaudeCodeLifecycle` and `CodexLifecycle`
   were the same algorithm, differing only in a width constant and
   `otel.start()`. Pane open/close became the interpreter's react step (one
   shared implementation); the harness-specific residue moved into an optional
   `HarnessReactor` (`plugins/claude_code/reactor.py`: otel receiver spawn on
   SessionStart, memory bookkeeping on PostToolUse, account migration on a
   committed rate-limit StopFailure). This also removed the contradiction of a
   hook process *raising* on a failed control.
6. **`HookIntake` left the contract entirely.** Once nothing outside the
   plugin called `plugin.hook.receive`, hooks became plugin-private scripts:
   parse stdin → `RawEventRecorder.record(raw_events)` → print the reply →
   exit. The rule, verbatim: *a hook parses stdin, records raw events, prints
   its reply, and exits* — no registration, no translation, no terminal, no
   files, no application graph (enforced by
   `test_recorder_entries_never_build_the_application`).
7. **Coordination state moved into evidence.** Foreground manifests became
   `watch` raw events — directives-as-evidence the interpreter applies to a
   `watches` table and pulls with a generic `FileWatchRawEventSource`. The
   hook's only remaining file act is creating the tee target its rewritten
   command requires.
8. **Registration became a launch-time act.** The upsert fell in stages:
   first to "identity first-writer, hints refresh", then to insert-once with a
   pid fill-once, and finally — once the wrapper became the registrar — to a
   plain insert. The wrapper that starts the harness knows the full identity;
   Claude Code even accepts `--session-id`, so the row exists before the
   process does. A new Claude wrapper (`plugins/claude_code/command.py`) was
   written to mirror the Codex one.
9. **Terminology**: a bare "event" names nothing. `CanonicalEventStore`,
   `HarnessRawEventSource(s)`, `HarnessCanonicalTranslator`,
   `StoredCanonicalEvent`, `TranslationResult.canonical_events`; contracts
   carry the `Harness` prefix, implementations the harness name
   (`ClaudeTranscriptRawEventSource`, `CodexRolloutRawEventSource`, …).
10. **Backward compatibility was explicitly dropped.** The store schema moved
    to version 14 (autoincrement `id` + `source_identity` on `raw_events`, the
    `watches` table, `session_harness.registered_at`, `actor_harness` and
    `source_checkpoints` and all session foreign keys gone). The old
    `events.db` was set aside as `events.db.pre-recorder-interpreter`, not
    migrated — by decision.

## 3. The result (one screen)

```
LAUNCH     wrappers register the session and anchor its panes (pending → adopt)
RECORDERS  claude hook · codex hook · wrappers · otel receiver
           └─▶ RawEventRecorder.record(raw_events)      — and nothing else
INTERPRETER (one thread, app/interpreter.py, tick every 0.25s)
  0 register   orphan evidence → plugin.session_evidence → SessionRegistry.register
  1 pull       for every unfinished session: plugin sources + watch sources,
               read(position-from-evidence) → record
  2 translate  untranslated backlog → verdict + canonical + provenance (one txn);
               watch/terminal directives applied generically; translator BUGS
               become translation_failed verdicts (the ordered backlog must
               never wedge); plugin.reactor runs after commit
  3 react      session.started → open panes (anchored by evidence, never focus)
               session.finished → reap watches, close panes
READERS    projections / SSE / dashboard / terminal ◀─ CanonicalEventStore
```

Storage classes and their write ownership: `SessionRegistry` →
`session_harness` (insert-once); `RawEventRecorder` → `raw_events` (append-only,
byte-conflict-checked); `WatchRegistry` → `watches`; `CanonicalEventStore` →
`translation_records` + `canonical_events` + `canonical_provenance`.
`runtime/database.py` owns the schema; `HarnessRegistry` is the in-memory
name→plugin map. The plugin contract: `info` · `sources` · `translator` ·
optional `session_evidence` / `reactor` / `controller` / `launcher` / `catalog`
/ `usage` / `memory` / `terminal_probe`.

## 4. What broke in live fire, and what each failure taught

Every one of these was found by using the system the same day, diagnosed from
the store/audit (never guessed), and fixed with a test:

1. **Background jobs showed no output** (session `681c98ec`). Background Bash
   is run by Claude Code itself; the hook cannot tee-rewrite it, so nothing
   ever watched its native output file — true in the old canonical system too.
   Fix (`8d8ec1a`): the watch starts at PostToolUse — `backgroundTaskId`
   locates `/tmp/claude-<uid>/<cwd-slug>/<session>/tasks/<taskId>.output`
   (globbed by the unique session/task pair; the slug rule stays Claude's),
   watched with `delete_source=False`. A background job has no finish hook, so
   the committed `session.finished` is its finish: the react step drains each
   remaining watch's tail and removes the rows (a finished session leaves
   `watchable()` and would otherwise never be pulled again).
2. **Kitty-launched sessions were invisible** — wrapper-only registration met
   reality: the `c1`/`c2` aliases don't run the wrapper. Patching every launch
   path (a `BAQYLAU_WRAPPED` recursion guard in `claude-subscription`) was
   attempted, **reverted on review**, and replaced after a design round with
   **evidence-driven registration** (`b7868ee`): filesystem polling (the v1
   answer) was rejected again — it registers sessions there is no evidence for
   and re-walks history — in favour of the orphan evidence announcing its own
   session (`HarnessSessionEvidence.from_raw_event`; Codex reuses its
   lead-rollout subagent filter). Deployed, it registered the four invisible
   sessions within seconds. Evidence-registered sessions carry no pid: no
   process-exit backstop.
3. **The background launch stub wiped output** (session `1f9a0425`): the
   "Command running in background with ID …" tool_result translated as a
   REPLACE-mode progress event, which could commit after the first watch chunk
   and erase it (that is how "it lost the first line"). The stub is now
   suppressed in the results branch (`BACKGROUND_LAUNCH_STUB`); the
   operation-finished fact still converges from the hook evidence.
4. **Panes anchored in the wrong tab, then in no tab** (sessions `1f9a0425`,
   `4597c616`). Root cause: `current_window()` is `$KITTY_WINDOW_ID` *of the
   calling process*. The old lifecycle ran inside the hook (which inherits the
   session tab's id — accidentally correct); the interpreter runs in the
   server, whose value is absent or a stale identity inherited from whichever
   hook spawned it via `ensure_running`. Interim fix (freshness-gated focus
   fallback) was superseded by the final one: **the anchor is evidence** —
   every hook records a `terminal` raw event with its window id
   (`terminal_window_raw_event`, one row per session-and-window, deduplicating,
   self-healing on every hook), the Claude wrapper opens pending panes from
   inside its own window and adopts them on registration (the Codex pattern),
   and react anchors at the session's tagged window, else the recorded anchor,
   else nowhere. The server's own window identity is never consulted
   (`RecordingTerminal.current_window` in the tests raises on touch).
5. **A web send was `acknowledged` but never submitted** (session `4597c616`):
   the bracketed paste landed and the separate CR 0.15s later was swallowed —
   the intermittent shape `kitten_send_text`'s own comment documents. The
   message sat in the input box while the control audit claimed success.
   Fix: `tui.type_command` now VERIFIES delivery against the box itself (the
   probe reads the draft; the Enter is retried with backoff; a message that
   never leaves the draft returns failure → the handler reports
   `indeterminate` honestly). Multi-line pastes collapse into Claude Code's
   placeholder and keep the optimistic contract.

The meta-lesson repeated across 4 and 5: **anything the server "just knows"
about the terminal is a guess; anything a hook observes from inside the
session's own window is evidence.** When in doubt, record it and read it back.

## 5. Deployment state (end of 2026-08-15)

- All commits pushed to `main`; history linear; the dashboard singleton
  restarted on the final code (it does NOT hot-reload — after any future
  change: `bin/baqylau-dashboard.py stop && … start`, then hard-reload the
  browser).
- `~/.local/share/baqylau/events.db` is a fresh schema-14 store; the
  pre-redesign one is preserved beside it as `events.db.pre-recorder-interpreter`
  (plus `.bak-schema11`, `.bak-premigrate12` from earlier eras).
- Hooks in `~/.claude/settings.json` were NOT rewired — same entry points, new
  behavior.
- `~/.local/bin/claude-subscription` still `exec claude` directly (the wrapper
  edit was reverted by request); kitty `c1`/`c2` launches are therefore
  evidence-registered: visible, watched, translated — but no pid backstop and
  panes anchored via recorded hook evidence. The `codex()` shell function
  already routes through its wrapper. The dashboard launcher and the account
  migration control both route Claude through
  `plugins/claude_code/command.py`.
- Test suite: 281 passing; `make lint` clean. The suite neutralizes
  `KITTY_WINDOW_ID` (conftest) because hooks now record it as evidence.

## 6. Known gaps and deliberate non-fixes

- **Evidence-registered sessions have no process backstop.** A SIGKILLed CLI
  in an unwrapped session ends via `SessionEnd`/rollout evidence or not at
  all; the session then stays in `watchable()` (cost: one stat per tick). If
  stale unfinished sessions ever accumulate, slow-cadence old sessions —
  do NOT reintroduce a count cap.
- **Sessions that pre-date a fix don't heal retroactively**: the store is
  append-only, so e.g. an already-stored background stub stays rendered, and a
  background job whose PostToolUse fired before `8d8ec1a` is not watched.
- **Multi-line web sends are not delivery-verified** (paste placeholder).
- **The teaching document** (`docs/teaching/baqylau.html`) carries a banner:
  Parts I–III describe the pre-redesign pipeline in places; Parts IV–VI (HTTP,
  SSE, audit) are current. A full rewrite was deliberately deferred.
- **`docs/canonical-harness-architecture.md`** is still authoritative for the
  canonical event model, superseded (and bannered) for session/hook/checkpoint
  flow.
- **The Codex collaboration backscan** (`_collaboration_call`) reads backwards
  from `raw_event.source_position` — one reason rollout/transcript positions
  stay line-START encoded. Do not "simplify" positions to end-offsets for line
  sources without reworking it.
- **`model.changed`/`effort.changed` still render no activity item** (an older,
  unrelated request): the desired behavior is a dot-style "Model changed
  sonnet → opus" entry, with Claude's `/model opus` bubble suppressed. Not
  started.

## 7. How to verify the whole flow in one sitting

```sh
make test && make lint
# live: run any claude session in kitty (wrapped or not) and watch:
sqlite3 "file:$HOME/.local/share/baqylau/events.db?mode=ro" \
  "SELECT count(*) FROM raw_events WHERE session_id NOT IN (SELECT session_id FROM session_harness);"
# 0 within a tick of the first hook = evidence registration works
python3 bin/baqylau-audit.py session <sid>       # exact evidence + verdicts
```

For any misbehavior, start from the **`audit-debug` skill** — its schema table
and bug shapes were updated with this redesign (backlog queries, position
derivation, unregistered-session shape, send verification) and must be kept in
lockstep with future changes, per `CLAUDE.md`'s audit-coverage rule.
