# Recorders and the one interpreter (2026-08-15 redesign)

The authoritative description of how sessions, raw events, hooks and watches flow
after the 2026-08-15 redesign. Where `canonical-harness-architecture.md` or the
teaching document disagree with this file, this file wins.

## The rule

**A component either appends evidence or interprets it, never both — and only
the interpreter has a terminal or a thread.** The split used to be per-process;
since hooks became thin clients it holds per-component inside the daemon too:
the hook gateway records and returns, the interpreter interprets on its next
tick, and nothing does both.

```
LAUNCH — the wrapper is the sole registrar; it acts BEFORE hooks can exist
──────────────────────────────────────────────────────────────────────
 plugins/claude_code/command.py   uuid → SessionRegistry.register → claude --session-id
 plugins/codex/command.py         Popen codex → poll rollout → SessionRegistry.register
 (the dashboard launcher and the shell aliases both route through these)
 wrapper lifetime brackets the session: records the process-finished raw event

              session_harness: written ONCE, at launch, by SessionRegistry
──────────────────────────────────────────────────────────────────────
WRITERS — observation only; append-only; never see session_harness
──────────────────────────────────────────────────────────────────────
 otel receiver │ wrappers (process raw events)
      └──▶ RawEventRecorder.record(raw_events)          # direct, daemon-independent

 hook (thin client) ─▶ POST /api/harnesses/<name>/hooks   body = EXACT stdin bytes
   plugins/*/canonical_hook.py = app/hook_client.run(HARNESS, ENVIRONMENT_KEYS)
   X-Baqylau-Environment header = the env subset only the hook process can see
   daemon: HookGatewayService ─▶ plugin.hooks.raw_events() ─▶ RawEventRecorder
   hook stdout ◀─ response body = the gateway-computed reply (the Bash tee rewrite)
   the gateway may also emit WATCH DIRECTIVES (see below) — never files

              raw_events: append-only, byte-conflict-checked
──────────────────────────────────────────────────────────────────────
INTERPRETER — one process; heartbeat 0.25s ─▶ Interpreter.tick()  (app/interpreter.py)
  0 register   orphan raw events (no session row) are offered to their harness's
               `session_evidence` — a hook payload announces its own session, so
               launches that skipped the wrapper become visible one tick later
  1 pull       for session in SessionRegistry.watchable():           # all unfinished
                 for source in session.plugin.sources.for_session(session)
                              + WatchRegistry.for_session(session_id):
                   raw_events = source.read(recorder.position(source))
                   recorder.record(raw_events)
  2 translate  for raw_event in CanonicalEventStore.untranslated_raw_events(n):
                 source_type "watch" → WatchRegistry.apply, verdict ignored_nonsemantic
                 else plugin.translator.translate(raw_event)
                 CanonicalEventStore.store_translation(...)            # one txn each
                 plugin.reactor.react(raw_event, controls)             # claude: otel,
                                                                       # memory, migrate
  3 react      committed session.started  ─▶ open kitty panes (shared impl)
               committed session.finished ─▶ close panes

              translation_records · canonical_events · canonical_provenance
──────────────────────────────────────────────────────────────────────
READERS        projections / SSE / dashboard ◀─ CanonicalEventStore
               (all inside the daemon — the ONE application graph)
──────────────────────────────────────────────────────────────────────
CLIENTS — every process outside the daemon that is not a recorder is a thin
HTTP/SSE client of it (app/daemon_client.py); none builds the graph or opens
the store (test_the_application_graph_is_built_only_by_the_daemon)
──────────────────────────────────────────────────────────────────────
 mirror pane      app/terminal_process.py   ◀─ SSE /api/sessions/<id>/panes/mirror/stream
 scoreboard pane  app/scoreboard_process.py ◀─ SSE /api/sessions/<id>/panes/scoreboard/stream
 hook delivery    plugins/*/canonical_hook.py ─▶ POST /api/harnesses/<name>/hooks (exact stdin)
 pane keybinding  app/terminal_panes.py     ─▶ POST /api/terminal/panes {command, window_id, cwd}
 view click       bin/baqylau-view.py        ─▶ POST /api/terminal/views {content_reference}
 copy click       bin/baqylau-content.py     ◀─ GET  /api/content/<reference>
 (bin/baqylau-audit.py is the ONE sanctioned direct reader: the forensic CLI
  must work when the daemon is the thing being debugged)
```

## Hooks deliver through the daemon

A hook process does four things: read stdin, `ensure_running()`, POST the exact
bytes plus its env subset, print whatever body comes back
(`app/hook_client.py`; one shared body, per-harness stubs). Everything it used
to compute — parsing, watch directives, the account snapshot, the terminal
window anchor, the synchronous reply — moved daemon-side behind the
`HarnessHookGateway` protocol (`plugins/<name>/hooks.py`), invoked by
`HookGatewayService` (`app/hook_gateway.py`), the ONE recorder of pushed hook
evidence.

- **The body is evidence**: the exact hook stdin, never decoded and re-encoded
  in transit (`_post_guard_bytes`); the gateway embeds it unmodified in its raw
  events. The env vars only the hook process can see ride the
  `X-Baqylau-Environment` header (each plugin's `ENVIRONMENT_KEYS` is the one
  owner of which keys ship).
- **A hook still never blocks or fails its harness**: every client failure path
  audits and exits 0 with empty output, and the delivery timeout is short
  (`DELIVERY_TIMEOUT_SECONDS`) — a wedged daemon costs each hook that pause,
  never more.
- **Recording rides the HTTP threads**, not the interpreter thread — a wedged
  `tick()` does not stop evidence capture, and a hook fact still turns
  canonical on the next tick like any other raw event.
- **The daemon is now a single point of failure for hook evidence, by
  decision** (it already was for presentation). `ensure_running()` in the
  client keeps the window small — the first hook of a session boots the daemon
  exactly as it used to — but a delivery the daemon never accepted is LOST, not
  spooled: there is deliberately no client-side fallback write, because two
  writers of hook evidence was the bug this design removes. The loss is always
  visible: a client-side `errors` row (`<harness> hook (deliver)`) per dropped
  delivery, a daemon-side one (`hook delivery`) per refused delivery.

## Panes are rendered in the daemon

The pane processes used to each build the whole application graph and poll the
store directly. That was three more constructions of the graph per session and
a second family of store readers to keep correct. Now the daemon renders
(`app/pane_streams.py`) and the pane process copies bytes:

- **One shared block model per session** feeds every mirror connection. The
  block model is width-independent (wrapping happens at render time), so each
  client renders at its own width from the same model; whichever connection
  polls first advances it under the model's lock — a single writer at a time
  with no feeder thread to manage. Tab painting rides the same advance, so it
  happens once per state change regardless of how many clients watch.
- **A pane resize is a reconnect** at the new width; the server re-renders the
  warm model, so no history is replayed. Idle server ticks (SSE comments,
  every 0.25s) are the client's resize/liveness clock.
- **A pending identity is resolved server-side**: the stream holds until the
  wrapper binds it, then announces the session on a `session` event; the client
  reconnects under the real identity from then on.
- **The daemon is a single point of failure for presentation, by decision.**
  Canonical facts were only ever produced by the daemon's interpreter, so a
  down daemon always meant a frozen pane; the panes now say "reconnecting"
  instead of silently showing stale history, and recover on their own
  (RECONNECT_DELAY_SECONDS). Hook evidence later joined presentation behind the
  daemon (see *Hooks deliver through the daemon*); the pull-side recorders (the
  wrappers, the otel receiver) still write directly and must keep doing so —
  they run on the launch path, before any daemon is guaranteed.

## The five storage/flow classes

| class | one responsibility | writes |
|---|---|---|
| `SessionRegistry` (`runtime/sessions.py`) | sessions: `register()` at launch, `watchable()`, `find()`/`load()` with `.plugin` attached | `session_harness` (insert-once) |
| `RawEventRecorder` (`runtime/recorder.py`) | record observations; `position()` = last recorded raw event per `source_identity` | `raw_events` |
| `WatchRegistry` (`runtime/watches.py`) | file watches: apply directives, hand out generic chunk sources | `watches` |
| `CanonicalEventStore` (`runtime/canonical_store.py`) | store and serve interpretations; the untranslated backlog | `translation_records`, `canonical_events`, `canonical_provenance` |
| `Interpreter` (`app/interpreter.py`) | `tick()`: pull → translate → react; the ONE thread | nothing itself |

`HarnessRegistry` (`runtime/harnesses.py`) is the in-memory name→plugin map.
`runtime/database.py` owns the schema and the connection policy; every storage
class initializes through it.

## Why each alternative failed

- **Hooks used to build the entire application graph** (`build_default_application`
  per tool call) to insert one or two rows. Every Claude Code tool call paid the
  full bootstrap import, and any bug anywhere in the graph could lose evidence.
  The first fix made each hook its own recorder (parse + `RawEventRecorder`
  in-process) — which left TWO writers of hook evidence (each hook subprocess
  and the daemon's pull loop) racing on the same store, and hook parsing running
  whatever code was on disk mid-upgrade while the daemon ran what it imported at
  startup. Now the hook is a thin client and the daemon's gateway is the one
  recorder of pushed evidence; the accepted price is the loss window a dead
  daemon opens (see *Hooks deliver through the daemon*).
- **The presenters built the graph too** — each pane process, the keybinding
  helper and the click handlers all called `build_default_application` (five
  more construction sites, one per keypress for the keybinding). Interpreting
  is one process's job, so they became thin HTTP/SSE clients of the daemon
  (see *Panes are rendered in the daemon*); the keybinding got FASTER (one
  localhost round-trip instead of importing the whole graph per keypress) and
  its refusals became visible (a 4xx body and a `pane-command` audit row where
  `launch --type=background` used to discard a traceback).
- **Hooks used to translate** (with whatever code was on disk at that moment)
  while the server translated pulled events with the code it imported at startup —
  two translator versions running concurrently. Now translation happens only in the
  interpreter, from the backlog: `raw_events LEFT JOIN translation_records WHERE
  verdict IS NULL`, ordered by arrival. Every raw event leaves the backlog exactly
  once (even a translator *bug* becomes a `translation_failed` verdict — an
  unverdicted row would wedge everything behind it).
- **Sessions used to be "discovered"** (`SessionRecognizer.discover/recognize`,
  glob-and-parse in both plugins, ambiguity errors) and *registered by hooks* with
  upsert-and-merge semantics (`INSERT OR IGNORE` + ownership conflicts + a pid
  fill-once). All of it fell to one observation: **registration is a launch-time
  act** — the wrapper that starts the harness knows the full identity (Claude Code
  even accepts `--session-id`, so the row exists before the process does; Codex's
  wrapper registers when the rollout appears). The row is immutable; everything
  that changes during a session (cwd, title, model) is a canonical fact.
  Wrapper-only registration then met reality: sessions launched by any OTHER path
  (a plain alias in a kitty tab) recorded evidence forever while staying
  invisible. Filesystem polling (the v1 answer) was rejected again — it registers
  sessions there is no evidence for and re-walks history — in favour of
  **evidence-driven registration**: the orphan evidence itself announces the
  session (`HarnessSessionEvidence.from_raw_event`; a hook payload carries the
  identity and the source reference, Codex reuses its lead-rollout filter), and
  the interpreter registers it. Hook evidence that beats any registration waits
  in the backlog, auditable, and interprets the moment registration lands.
  Evidence-registered sessions simply lack a pid, so they have no process
  backstop — the wrapper stays the better path, not the required one.
- **`source_checkpoints` was a second encoding of progress** and its drift from
  the evidence was the single most diagnostic bug shape in triage. Deleted: a
  source resumes from the `source_position` of the last raw event carrying its
  `source_identity`. Progress cannot drift from evidence because it *is* the
  evidence. The invariant that makes this sound: **a source may only advance past
  input by emitting it as evidence.** Position encodings are source-private
  (line-start + skip-one-line for transcripts/rollouts, chunk-end for watches, a
  snapshot digest for task files, a `started`/`finished` latch for process
  sources).
- **Background commands stream through the same watches.** Claude Code runs a
  `run_in_background` Bash itself and writes its output to a native file
  (`/tmp/claude-<uid>/<cwd-slug>/<session>/tasks/<taskId>.output`). The hook can't
  tee-rewrite those, so the watch starts at PostToolUse instead: the payload's
  `backgroundTaskId` locates the native file (globbed by the unique
  session/task pair — the slug rule stays Claude's), and the directive watches it
  with `delete_source=False`. There is no finish hook for a background job, so
  the session's committed `session.finished` is the finish: the interpreter's
  react step drains each remaining watch's tail and removes the rows (a finished
  session leaves `watchable()`, so they would otherwise never be pulled again).
- **Foreground manifests were coordination state pretending not to be a table**
  (`<op>.json` / `.done` files under the Claude config dir). Now the PreToolUse
  hook records a `watch` raw event — a directive-as-evidence — and the interpreter
  applies it to the `watches` table and pulls the file with the generic
  `FileWatchRawEventSource` until the PostToolUse `finish` directive plus EOF ends
  it. The watch history is permanently queryable in `raw_events`.
- **Per-harness lifecycle was the same algorithm twice.** `ClaudeCodeLifecycle`
  and `CodexLifecycle` differed only in a width constant and `otel.start()`. Pane
  open/close moved into the interpreter's react step (one shared implementation,
  `app/pane_preferences` width); the genuinely harness-specific bits moved into
  `HarnessReactor` (`plugins/claude_code/reactor.py`): otel receiver spawn on
  SessionStart, memory bookkeeping on PostToolUse, and account migration on a
  committed rate-limit StopFailure — which also removed the contradiction of a
  hook process *raising* on a failed control.
- **The observed-session cap (4) was a quota where liveness is an evidence
  question.** `watchable()` is every session without a committed
  `session.finished`, ordered by recency. An up-to-date source costs one stat per
  tick, and sessions finish because the wrappers and process sources record it
  even for killed harnesses (SIGKILLed wrapper → the Codex process source's
  latch; Claude relies on the wrapper's `finally` plus `SessionEnd`).

## Naming

A bare "event" names nothing. Contracts carry the `Harness` prefix
(`HarnessRawEventSource`, `HarnessRawEventSources`, `HarnessCanonicalTranslator`,
`HarnessReactor`); implementations carry the harness name
(`ClaudeTranscriptRawEventSource`, `CodexRolloutRawEventSource`, …). The plugin
contract is: `info` · `sources` · `translator` · optional `hooks` (the push
twin of `sources` — `HarnessHookGateway`) / `reactor` / `controller` /
`launcher` / `catalog` / `usage` / `memory` / `terminal_probe`.

## Costs accepted deliberately

- Hook facts turn canonical on the next interpreter tick (≤0.25s later than the
  old synchronous ingest). SSE polls at the same cadence, so worst case adds one
  beat; pane open/close shifts by the same amount.
- A hook delivery the daemon never accepted is lost (no client-side fallback
  write; every drop leaves an `errors` row). Each hook pays one localhost
  round-trip, and up to `DELIVERY_TIMEOUT_SECONDS` when the daemon is wedged.
- The session row's `working_directory` is only "where the session began"; live
  cwd is the projection's (`session.working_directory_changed`).
- A session launched without its wrapper becomes visible one tick after its
  first hook, but carries no pid (no process-exit backstop) and gets no
  deterministic pane anchor.
- The kitty mirror panes never anchor by the server's "current window" — that
  is at best absent and at worst a stale identity inherited from whichever hook
  spawned the server (measured: it planted one session's panes in another's
  tab). The anchor is either the session's own tagged window (the wrapper opens
  PENDING panes from inside the session's window and adopts them on
  registration, as Codex always did) or the `terminal` raw event a hook records
  from inside the session's tab (`terminal_window_raw_event`; one row per
  session-and-window, self-healing on every hook). No anchor evidence → no
  panes, honestly.
- Web `send_text` is VERIFIED against the input box itself: the CR after a
  bracketed paste is swallowed intermittently (a message once sat unsubmitted
  in the draft while the control audited `acknowledged`), so `tui.type_command`
  reads the box back, retries the Enter with backoff, and reports a message
  that never left the draft as a failure instead of a false success.
- The store schema changed incompatibly (schema version 14); old `events.db`
  files are refused, not migrated — by decision, the database was dropped at the
  cutover.
