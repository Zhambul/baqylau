---
name: audit-debug
description: Diagnose a session bug from the canonical event store and the operational audit trail. Use when the user reports a bug in a session (missing messages, a frozen dashboard feed, a session that looks alive but says nothing, a control gesture that did nothing, wrong usage/cost numbers) and gives a session id — or asks to investigate "what happened in session X".
---

# audit-debug — root-cause a session bug from the canonical evidence

Given a session id, reconstruct what happened and name the bug **from evidence, not
guesswork**. Two SQLite stores hold it, and knowing which one answers which question
is most of the skill.

## Where the data is

Both stores live under `~/.local/share/baqylau`, overridable with
`$BAQYLAU_DATA_DIR`. `core/data.py` owns both paths. Open them **read-only**
(`file:<path>?mode=ro`) so triage can never mutate the record being inspected.

| store | path | what it answers |
|---|---|---|
| **main** | `<data>/main.db` | Everything the application owns and reads back: evidence and its interpretation, your unsent work, your preferences, terminal state, plan usage, uploads. Schema: `repository/impl/sqlite/schema.py`. |
| **audit** | `<data>/audit.db` | Debug-only records of what the *machinery* did and where it degraded: swallowed exceptions, detached processes, control-plane gestures, browser telemetry. It remains separate so it is readable when `main.db` is the suspect. `BAQYLAU_AUDIT=0` disables it (`audit/record.py`). |

**Nothing outside `repository/impl/sqlite/` opens a database.** The one module elsewhere
that does is `harness/impl/codex/canonical/title.py`, which is itself a repository
implementation and lives there only because a shared package may not contain a harness's
name. If you find SQL anywhere else, that is the bug.

CLI: `python3 bin/baqylau-raw-events-audit.py session <session_id>` dumps every
`RawEventAudit` for a session; `... raw <raw_event_id>` does one. Each contains the exact
raw event and its optional `InterpretationAudit` with emitted canonical events. The CLI
prints JSON, base64-encodes payload bytes, and opens `main.db` read-only. Query the two
stores directly with `sqlite3` for aggregate questions.

## The model (read this before querying)

Evidence flows one way, and each stage is recorded. **Only the daemon writes `main.db`.**
The daemon normally writes `audit.db` through injected `AuditRecorder`; `audit/record.py`
is the free-function floor for the few boot, guard, clipboard, and notification paths
without an injection graph. Audit writes are best effort and never raise.

```
hooks · statusline · otel receiver  ──POST exact bytes──▶  the daemon
   /api/harnesses/<name>/hooks      ──▶ hook gateway      ─┐
   /api/harnesses/<name>/telemetry  ──▶ telemetry gateway ─┤
                                                           ├──append──▶ raw_events
   interpreter: pulled sources (transcripts, rollouts, ────┘
                output chunks, liveness)
                       │
   translate ──▶ interpretations + canonical_events + interpretation_events
                 (ONE transaction: CanonicalEventRepository.record_translation)
                       │
   react ──▶ sessions (upsert) · operation_output · panes · plugin reactors
```

- **`sessions` is NOT written at launch.** The row is born by the interpreter's
  session-upsert reaction from the session's own `session.started` FACT, and its two
  live columns (`terminal_window_id`, `harness_process_id`) are refreshed from the
  envelope of every later hook-borne fact — which is how a resume into a new window
  updates it. Nothing upstream of the store ever requires a row to exist: evidence
  may precede its session, and the first delivery is what births it.
- **`raw_events`** is *immutable evidence*: the exact bytes a source produced. Reusing a
  `raw_event_id` with a different payload raises `EventIdentityConflict` — that is
  corruption, not convergence. Re-recording an IDENTICAL observation is a deliberate
  no-op (sources re-read their last record on resume).
- **`canonical_events`** is an *idempotent projection*. `event_id` names a **fact**, so
  several independent sources may converge on one event; re-observing it is a no-op that
  only appends an `interpretation_events` row. The store keeps the **first writer** and does **not** compare
  bodies — a later, differing rendering is not lost, it stays recoverable from its own raw
  event.
- **`cursor`** (`INTEGER PRIMARY KEY AUTOINCREMENT`) is the monotonic ordering key
  everything pages by. `event_id` is *not* an ordering key.
- **There is no checkpoint table.** A pulled source resumes from the `source_position`
  of the LAST raw event carrying its `source_identity` — recorded progress and evidence
  are the same rows and cannot drift. "How far has this source been read?" is:
  `SELECT source_position FROM raw_events WHERE source_identity=? ORDER BY id DESC LIMIT 1`.
- **The untranslated backlog IS the queue**: raw events with no `interpretations`
  row await the interpreter, in `raw_events.id` order. Every raw event leaves the backlog
  exactly once, because the verdict and the facts are written in one transaction.
- **A translation that disagrees with its evidence is a VERDICT, not a crash.** Five
  envelope checks (`engine/interpret/loop.py:checked`) compare each canonical event
  against the raw event it came from; a violation lands as `decision='translation_failed'`
  with the reason in `reason`, and the queue moves on.

**Who drives what.** The `Interpreter` (`engine/interpret/loop.py`) is the ONE
read-and-interpret loop: it expires stale output followings, pulls every unfinished
session's sources, translates the backlog (hook evidence included — hooks do NOT
translate), and reacts to committed facts. It runs as a thread inside the dashboard
server process, every `TICK_INTERVAL_SECONDS` (0.25 s), with no session-count cap.

**Every other process is a thin HTTP client** — hooks, the status-line shim, the OTLP
receiver, the pane renderers, the keybinding and click handlers. None of them opens a
database. (The status-line shim and the OTLP receiver used to write one directly; they
POST to the telemetry endpoint now, which is why a stopped daemon loses rate-limit and
metric captures rather than silently storing them.)

That leaves the key asymmetry behind the headline failure mode: hook and telemetry
deliveries are recorded on the **HTTP threads**, not the interpreter thread. So when the
interpreter thread stops but the daemon process lives, a session keeps accumulating raw
evidence and still looks partly alive, while NOTHING turns canonical. Only a fully dead
daemon stops capture too — and then each dropped delivery leaves a client-side `errors`
row (`func` = `<harness> hook (deliver)`, or `otel delivery (daemon unreachable)`).

## Schema

### `main.db` — 25 tables

**The evidence spine** (what a session did):

| table | one row per | key columns |
|---|---|---|
| `raw_events` | one observation, verbatim | **`id`** (arrival order), `raw_event_id` (unique), `session_id`, `harness`, **`source_type`**, **`source_identity`** (the resume key), `source_name`, `source_position`, `actor_id`, `parent_actor_id`, `observed_at`, `encoding`, `payload` (BLOB), `terminal_window_id`, `harness_process_id`, `account_id`, `account_display_name` |
| `interpretations` | one translation verdict | `raw_event_id` (PK), `translator_version`, **`decision`** ∈ `translated` / `ignored_nonsemantic` / `ignored_unknown` / `translation_failed`, `reason`, `completed_at` — a raw row with NO verdict is the untranslated backlog |
| `canonical_events` | one interpreted fact | **`cursor`** (monotonic), `event_id` (unique), `schema_version`, **`event_type`**, `session_id`, `actor_id`, `turn_id`, `parent_actor_id`, `harness`, **`occurred_at`** (NULLABLE), `terminal_window_id`, `harness_process_id`, `accepted_at`, `payload` (JSON) |
| `interpretation_events` | one canonical event emitted by an interpretation | `event_id`, `raw_event_id`, `event_order`, **`storage_result`** ∈ `accepted` / `deduplicated` |
| `sessions` | one observed session — a READ-MODEL, born from `session.started` | `session_id`, `lead_actor_id`, `harness`, `harness_session_id`, **`source_reference`** (the transcript/rollout path), `working_directory`, `terminal_window_id`, `harness_process_id`, `created_at` |
| `operation_output` | one output file being followed | `session_id`, `operation_id`, `harness`, `actor_id`, `source_path`, `chunk_source_type`, `delete_source`, `initial_size`, `initial_modified_at`, `wait_for_source_change`, **`until`** ∈ `operation_finished` / `session_finished`, **`state`** ∈ `active` / `finishing`, `created_at` — removed once drained |
| `schema_version` | the store itself (singleton, `id=1`) | `version`, `applied_at` — a mismatch refuses to open the file |

**Your unsent work** (state the session never sees; four tables, one composer):

`session_workspaces` (composer text/origin/sequence, queue origin, dialog attention id/origin)
· `composer_queue_items` (`session_id`, `position`, `text`)
· `dialog_answers` (`session_id`, `prompt_index`, `other_text`)
· `dialog_answer_selections` (`session_id`, `prompt_index`, `selection_index`, `selected_value`).

**What you chose** (nine tables that replaced one key–value blob store):
`notification_settings` (singleton `alerting_enabled`) · `session_notification_mutes`
· `session_view_modes` (CHECK-constrained to verbose/default/focus) · `hidden_directories`
· `new_session_preferences` (singleton) · `new_session_drafts` (per directory, with the
stale-write `sequence`) · `task_dismissals` (`session_id`, `task_id` — the id SET, so the
card returns when the list moves on) · `push_subscriptions` · `push_signing_keys`.

**The rest**: `pane_widths` · `opened_views` · `account_usage_snapshots` +
`account_usage_windows` (keyed by harness + account; `used_percent` is TEXT so a
`Decimal` never round-trips through a float) · `uploads` (the row beside each staged
attachment — the bytes stay on disk because the harness is handed an `@path`).

**Three opaque columns, and only three**: `canonical_events.payload` (a closed
vocabulary the codec validates on both encode and decode), `raw_events.payload` (the
verbatim bytes, which is the point), and `state_files.content` in the audit database.
Everything else is a typed column — `test_no_key_value_table_exists` enforces it.

**`source_type` vocabulary** (which observer produced the evidence). Pushed to the daemon
over HTTP: `hook`, `teammate_hook`, `account`, `launch` (launch-time model/effort from the
hook's inherited environment), `output_location` (a hook's directive naming a command's
output file), and `otel` (the OTLP receiver's export, recorded by the telemetry gateway).
Pulled by the interpreter: `transcript`, `rollout`, `tasks`, `task_list`,
`foreground_output` (output chunks), `liveness` (the CLI process probe), and the
child/teammate variants `child_transcript`, `teammate_transcript`, `child_rollout`,
`child_replay`, `sidecar_rollout`, `sidecar_replay`.

**`event_type` vocabulary** (35, from `domain.events.EVENT_TYPES`):
`session.started` / `.finished` / `.title_changed` / `.account_changed` /
`.working_directory_changed` · `actor.started` / `.finished` / `.name_changed` /
`.description_changed` / `.message_sent` / `.assignment_started` / `.assignment_finished` ·
`turn.started` / `.finished` / `.aborted` · `message.created` · `reasoning.created` ·
`operation.started` / `.progressed` / `.finished` / `.input_provided` /
`.output_located` / `.output_finished` · `file.accessed` ·
`attention.requested` / `.resolved` · `compaction.started` / `.finished` ·
`usage.reported` · `context.reported` · `model.changed` · `effort.changed` ·
`goal.changed` · `task.changed` / `.list_changed`.

**`occurred_at` is NULL by design.** It means "when the *source* said this happened".
Sources carrying no clock of their own (hook payloads) honestly leave it unset; sources
that timestamp their records (transcripts) populate it. Every read path must fall back to
`accepted_at` (when *we* recorded it) — `ORDER BY MAX(COALESCE(occurred_at, accepted_at))`
is the sanctioned form, and a contract test
(`test_no_read_path_orders_on_a_bare_occurred_at`) forbids ordering on the bare column.
So **a mostly-NULL `occurred_at` is not a bug** and is not worth chasing.

### `audit.db` (`audit/record.py`, stored by `repository/impl/sqlite/audit.py`)

| table | one row per | key columns |
|---|---|---|
| `errors` | swallowed exception | `ts`, `session_id`, `script`, **`func`**, `traceback` (full), `context`, `pid` |
| `state_files` | notable non-error act | `ts`, `session_id`, `path`, **`action`**, `content`, `script`, `pid` |
| `spawns` | detached process launch | `ts`, `session_id`, `parent_script`, `child_pid`, `argv`, `purpose` |
| `streams` | detached tailer/watcher | `session_id`, `kind`, `agent_id`, `task_id`, `src_path`, `pid`, `started_at`, `ended_at`, `end_reason`, `lines_emitted` |

`state_files.action` values in use: **`control`** (every control gesture's OUTCOME —
`{control, request_id, status, reason, ms}`, written at the one dispatch point
`harness/services/controls.py HarnessControlService.execute`; `status` ∈ `acknowledged` /
`rejected` / `indeterminate` / `raised`, and `reason` carries the harness's own words,
e.g. a screen driver's failed step), `browser-event`, `browser-optimistic-action`,
`browser-client-failure` (the frontend telemetry channel — what the browser saw that the
server cannot), `web-reject` (a control POST bounced by the request guard, `content` names
the code and why), `web-push`, `notification-route`, `notification-suppressed`,
`telegram-notify`, **`pane-command`** (every pane keybinding gesture the daemon
executed — `{command, window_id, session_id, ok, why}`, written by
`terminal/panes/commands.py`; `path` is the keypress's working directory),
**`terminal-view`** (a mirror click-to-view toggle, `path` = the content
reference, `content` = `opened` / `closed`, written by
`terminal/services/views.py`).

`errors.func` values worth knowing: `interpreter (tick)` / `(source read)` /
`(source construction)` / `(resume positions)` / `(output expiry)` — the interpreter's
own contained failures, each naming the step; `<harness> hook (deliver)` — a hook that
could not reach the daemon; `otel delivery (daemon unreachable)` — a metrics export
with nowhere to go; `statusline capture` — a rate-limit report that never shipped.

`streams.kind` values include **`pane-mirror`** / **`pane-scoreboard`** — one row
per pane SSE connection (the pane processes are thin clients of the daemon;
`end_reason` ∈ `client-gone` (a closed pane, a resize reconnect) / `error`
(the render loop threw — its traceback is in `errors` under `pane <kind>
stream`)).

**Only `control` and the `web-*`/notification rows carry the session in the `session_id`
COLUMN.** The `browser-*` telemetry rows leave it empty and bury it in the JSON, so a
`WHERE session_id='<sid>'` triage query returns nothing for them — match
`content LIKE '%<sid>%'` as well before concluding a gesture left no trace.

## Triage order

1. **Establish the session EXISTS as a row, and which harness owns it.**
   `SELECT * FROM sessions WHERE session_id='<sid>'` — no row means no `session.started`
   fact has been translated for it yet. That is NOT a registration failure: the row is a
   read-model born by a reaction, so its absence means either the evidence has not been
   interpreted (check the backlog next) or no source ever produced a start fact.
   `harness` decides whose translator and control gestures apply; `source_reference` is
   the file the pulled sources read.
2. **Measure the untranslated backlog** — the fastest health check of the interpreter:
   ```sql
   SELECT count(*), min(id), max(id) FROM raw_events
   LEFT JOIN interpretations USING(raw_event_id)
   WHERE interpretations.raw_event_id IS NULL;
   ```
   No session filter, deliberately: there is no registration gate — facts legitimately
   precede their session, and one of them is what births it.
   A growing backlog with a stuck `min(id)` names the wedge (its `raw_event_id`); an
   empty backlog with a stale feed means recording stopped, not interpreting.
3. **Compare each pulled source's position against its file.** The position is the last
   recorded raw event: `SELECT source_position FROM raw_events WHERE source_identity=?
   ORDER BY id DESC LIMIT 1` against the real size of `source_reference`.
4. **Ask whether the interpreter is alive** — the `max(observed_at)` per `source_type`
   query below. Pulled types stale while `hook`/`otel` are current = the interpreter
   thread stopped, machine-wide.
5. **Read the translation verdicts** for the session: an `ignored_unknown` or
   `translation_failed` run explains a *specific* thing missing while everything else
   flows.
6. **`errors` around the symptom's timestamp** (`func` prefixed `interpreter (...)` for
   pull/translate/react swallows; `claude_code hook (deliver)` / `codex hook (deliver)`
   for a hook whose POST failed client-side; `hook delivery` for a delivery the daemon
   refused), then `state_files` for the gesture.
7. **Exact bytes**: `bin/baqylau-raw-events-audit.py session <sid>` when you need to see what a
   source actually emitted rather than what we made of it.

## Known bug shapes → what to look for

### A session looks alive but its conversation stops (messages missing, feed frozen)

**The headline shape, and the first thing to rule out.** The interpreter thread died (or
its backlog wedged), so nothing turns canonical while hook and telemetry deliveries keep
landing on the HTTP threads — which is exactly why the session still looks partly alive.

One query names it:

```sql
SELECT source_type, datetime(max(observed_at),'unixepoch','localtime')
FROM raw_events GROUP BY 1 ORDER BY 2 DESC;
```

If `hook`/`otel` are current but `transcript`/`rollout`/`foreground_output`
all stop at the **same instant**, the `Interpreter` thread stopped at that instant —
machine-wide, every session, not just this one. Distinguish the two sub-shapes with the
backlog query from the triage order:

- **backlog growing** → the thread is dead or a raw event has no verdict. The
  interpreter verdicts even translator BUGS (`translation_failed` with the exception
  name), so a persistent wedge should be impossible; if `min(id)` is stuck, read that
  raw event and the matching `errors` rows (`func` = `interpreter (translation)` /
  `interpreter (canonical consistency)`).
- **backlog empty, pulled types stale** → pulling stopped: look for `errors` rows with
  `func` = `interpreter (source read)` / `interpreter (source construction)` /
  `interpreter (tick)`, and check the position-vs-file-size comparison per source.

Their *absence*, together with frozen pulled sources, means the thread died some other
way (or the server is running pre-fix code — it does **not** hot-reload; check
`bin/baqylau-dashboard.py status` and the process start time against the fix).

A restart is not a fix on its own: the source re-reads from its last recorded position
and, if the underlying record still fails, degrades again within seconds. Reproduce before and after:

```python
BAQYLAU_DATA_DIR=<a .backup copy>  # never the live store
from app import providers
from app.injection import registry, resolve
resolve(registry(), providers.interpreter).tick()
```

Take the copy with `sqlite3 "file:<main.db>?mode=ro" ".backup '<dest>'"` — a plain `cp`
of a WAL database yields `database disk image is malformed` and wastes a triage cycle.

### A session never appears at all, though the harness is clearly running

A session appears exactly one way: a source produces evidence, the interpreter
translates it into a `session.started` fact, and the reaction to that fact writes the
row. So a lingering invisible session means one of the three stages did not happen —
the backlog is wedged (step 2 of triage), the translator refused the payload (look for
its `interpretations.decision` and `reason`), or the payload never carried a usable
source reference (a Codex hook pointing at a non-lead rollout). Find the waiting
evidence:

```sql
SELECT session_id, count(*), datetime(max(observed_at),'unixepoch','localtime')
FROM raw_events
WHERE session_id NOT IN (SELECT session_id FROM sessions)
GROUP BY 1 ORDER BY 3 DESC;
```

Rows here are recorded-but-invisible sessions: evidence exists, but nothing has yet
produced the `session.started` fact that births the row. Interpreting that fact makes
them appear on the next tick — nothing is ever lost. Note such a session has no pid: no
process-exit backstop, and no
deterministic pane anchor (panes anchor by focus only within seconds of start).

### A session ran hooks but no hook evidence was recorded at all

Hooks record NOTHING locally — each delivery is a POST to the daemon
(`client/claude_hook.py` or `client/codex_hook.py` → `/api/harnesses/<name>/hooks`), and a delivery the daemon
never accepted is lost by design (no fallback write). The loss is always audited;
read both sides:

```sql
SELECT datetime(ts,'unixepoch','localtime'), func, context
FROM errors WHERE func LIKE '%hook (deliver)%' OR func = 'hook delivery'
ORDER BY ts DESC LIMIT 20;
```

- **`<harness> hook (deliver)` rows** (client-side) — the POST failed: daemon down and
  `ensure_running` couldn't boot it within its timeout, daemon wedged past
  `DELIVERY_TIMEOUT_SECONDS`, or the daemon answered non-200. The `traceback` names
  which. A burst of these during a restart window is normal-ish (a few deliveries);
  a steady stream means the daemon can't start — check `bin/baqylau-dashboard.py status`
  and `dashboard%` errors.
- **`hook delivery` rows** (daemon-side) — the daemon refused it: an unparseable
  payload (400, gateway `ValueError`) or an `EventIdentityConflict` (409, same
  `hook_event_id` re-sent with different bytes). The context carries the harness and
  payload size; the client swallowed the same failure, so both rows describe one event.
- **Neither, and yet no `hook` raw events** — the harness never fired hooks at all
  (check `~/.claude/settings.json` wiring points at `harness/impl/*/hooks/entry.py`),
  or the hooks ran under an environment where the repo path is wrong.

### The dashboard is unreachable, or needed several refreshes to load

Check the operational audit first — this is machinery, not session data:

```sql
SELECT datetime(ts,'unixepoch','localtime'), func, substr(traceback,-400)
FROM errors WHERE func LIKE 'dashboard%' ORDER BY ts DESC LIMIT 10;
```

- **`… was written by schema version N`** — `main.db` was written by other code
  while this process expects `domain.codec.SCHEMA_VERSION`, or vice versa. The server
  crash-loops. The companion signature is a long run of **`dashboard serve (lock denied)`**
  rows every ~10s (the supervisor retrying against a port/lock the dying process holds) and
  **`CanonicalCodecError: unsupported canonical schema version: N`** from any read path
  decoding older rows. Fix by getting code and store to the same version and restarting
  once; the retry rows stop the moment one server holds the lock.
- Confirm what the store actually says. **Two different version numbers, do not confuse
  them**: `SELECT * FROM schema_version;` is the FILE's table layout
  (`repository/impl/sqlite/schema.py`, refused by `SchemaVersionMismatch` at open), while
  `python3 -c "from domain.codec import SCHEMA_VERSION; print(SCHEMA_VERSION)"` is the
  canonical PAYLOAD schema, refused per row by `CanonicalCodecError` on decode.

### One specific thing never appears, but everything else flows

Not the scheduler — the **translator**. Read the verdicts:

```sql
SELECT t.decision, t.reason, count(*)
FROM interpretations t JOIN raw_events r USING(raw_event_id)
WHERE r.session_id='<sid>' GROUP BY 1,2 ORDER BY 3 DESC;
```

- **`ignored_nonsemantic`** — plumbing the translator deliberately drops. Expected in bulk.
- **`ignored_unknown`** — a record shape this translator has no rule for. Expected in bulk
  too (harnesses emit plenty we do not model); only interesting when the *missing thing*
  matches it.
- **`translation_failed`** with a `reason` — the actionable one. Real examples:
  `unmapped Codex tool: write_stdin`, `Codex write_stdin targets finished operation:
  <call_id>`. A tool absent from the dashboard whose reason says "unmapped" is a
  translator gap, not an ingest failure — the evidence is safely in `raw_events` and will
  translate once the rule exists.

### The model/effort selectors sit empty (or on the wrong value) for a fresh session

Launch-time selections have exactly one evidence source: the launcher exports
`BAQYLAU_LAUNCH_MODEL`/`BAQYLAU_LAUNCH_EFFORT` on the launched CLI, the hook
entry ships them as launch headers, and the gateway records ONE `launch` raw
event per SessionStart — translated to `model.changed`/`effort.changed` with
`reason="selected"` (measured, session 7245e266: before this event existed, a
dashboard launch showed no model for the first three minutes and no effort
ever — Claude Code never echoes the effort, and reports the model only on the
first assistant record). Triage: is the `launch` raw event there (`source_type
= 'launch'`)? If not, the launch bypassed the dashboard launcher (a hand-typed
`claude` carries no env), the running daemon predates the feature, or the
SessionStart delivery itself was lost (see the hook-evidence shape). If it
exists, read its translation verdict. Interleaved wrong-model values are a
different shape: subagent actors report their own models; the summary
projection filters `model.changed`/`effort.changed` to the LEAD actor, so
check `actor_id` before blaming the projection.

### An event's content looks wrong / two sources disagree

Convergence is by design: several sources may describe one fact. Read the fan-in:

```sql
SELECT event_id, count(DISTINCT raw_event_id) n
FROM interpretation_events GROUP BY 1 HAVING n > 1 ORDER BY n DESC LIMIT 10;
```

Multi-raw events are **normal** (measured: ~200 events with 2–4 sources, one with 32).
`storage_result` says which observation was `accepted` (the first writer, authoritative)
and which were `deduplicated`. **The first writer wins, and bodies are not compared** — so
if the stored rendering is the poorer one, the fix belongs in the *ordering or the identity
derivation*, not in the store. To see what the losing observer said, pull its raw event:
`bin/baqylau-raw-events-audit.py raw <raw_event_id>` — nothing is lost.

An `EventIdentityConflict` naming a **raw** event (`raw event identity reused`) is
different and is a genuine corruption signal: the same evidence id arrived with different
bytes.

### A control gesture (send / interrupt / rename / answer) did nothing

The server-side trace is `state_files`; the browser-side trace is the same table's
telemetry actions, and you usually need both:

```sql
SELECT datetime(ts,'unixepoch','localtime'), action, substr(content,1,400)
FROM state_files
WHERE session_id='<sid>' OR content LIKE '%<sid>%' ORDER BY ts;
```

- A **`control`** row is the gesture's own verdict and is where to start.
  **`status: "indeterminate"` is a FAILURE**, and the one most easily missed: the request
  arrived and the gesture was attempted, but the harness never confirmed it — a screen
  driver that bailed, a paste the TUI refused. `reason` names the step. It is served as
  **HTTP 202**, which every `r.ok` check calls success, so the browser's own row for the
  same gesture reads `command.ok`. Trust the `control` row, not the browser's.
  `status: "raised"` means the gesture threw; the traceback is in `errors`.
  A short `ms` against a screen driver is itself diagnostic — a driver that bailed in
  under its step timeout did not wait for anything, it failed on a screen it had already
  read (measured, codex session 01a0037d: 440ms against a 2.5s `STEP_TIMEOUT_S`, because
  codex-cli 0.147.0 had dropped the picker step the driver was looking for).
- A **`web-reject`** row means the request arrived and the guard bounced it — `content`
  carries the code and reason (missing header, foreign origin, read-only).
- A **`browser-client-failure`** row is the browser reporting a request that failed on its
  side; paired with no server row at all, the request never arrived (tunnel/upstream drop).
- **No row of any kind** means the gesture never left the page — look at
  `browser-event` rows for the load and any JS error, and remember a stale cached SPA is a
  standing cause (the server restart bumps its build id but cannot evict the browser's
  cache; the user must hard-reload).

A `send_text` row with `status: "indeterminate"` and reason `terminal message was
not delivered` means delivery was VERIFIED against the input box and the message
never left the draft (the post-paste Enter is swallowed intermittently; the
handler retries it, then reports honestly). An older `acknowledged` row with the
message still sitting in the box predates that verification.

Note the tunnel is a distinct failure domain from the bind: reproduce against
`http://127.0.0.1:8377` before blaming the application. A request that is 200 locally and
4xx/3xx through `https://baqylau.zhambyl.top` is a proxy concern, not an app bug.

### A kitty pane is frozen, blank, or stuck on its startup banner

The pane processes are thin SSE clients of the daemon — they render nothing
themselves (`terminal/panes/streams.py` renders; `core/daemon/client.py` copies bytes).
So a broken pane is one of three shapes, each with its own rows:

- **The daemon is down or restarting.** No `streams` row with kind
  `pane-mirror`/`pane-scoreboard` opened recently for the session, and the
  `dashboard` stream row itself has ended. The pane retries every couple of
  seconds and recovers on its own the moment `serve()` is back; this is the
  designed single point of failure, not a pane bug.
- **The stream keeps dying server-side.** `streams` rows for the session with
  `end_reason='error'` accumulating at the client's reconnect cadence — read the
  paired `errors` rows (`func` = `pane mirror stream` / `pane scoreboard
  stream`). The render loop threw on the same input each retry; the traceback
  names the projection or presenter at fault.
- **The stream is open but silent.** A live `pane-*` `streams` row (no
  `ended_at`) yet a stale pane means frames are flowing but empty — the shared
  mirror model advances only when the canonical cursor moves, so this is the
  frozen-feed headline shape above (interpreter dead), seen through a pane.
  Check the backlog before suspecting the pane plumbing.

A pane stuck on `⬡ starting session…` means the daemon has no `sessions` row to anchor
to yet, or the row has no `terminal_window_id`. Both are the same question — has a
`session.started` fact been translated, and did the hook that carried it report a window?
Check `SELECT session_id, terminal_window_id, harness_process_id FROM sessions WHERE
session_id='<sid>'`; a NULL window is a headless launch, which correctly gets no panes.

### Usage / cost numbers look wrong

`usage.reported` and `context.reported` events carry them, and `otel` is a pushed source —
so these survive a dead scheduler and can look healthy while the conversation is frozen.
Sum from the events rather than trusting any rendered total, and check `harness`: a
harness whose model has no price entry reports tokens without cost by design, which reads
as "$0.00" rather than as a missing number.

## Output contract

Report: (1) the bug in one sentence, (2) the evidence rows (timestamps + table), (3) the
code path responsible (file + mechanism), (4) a suggested fix. If the evidence is
inconclusive, say exactly which signal is missing and what extra instrumentation would
capture it next time.

Two habits that paid off and are cheap to repeat: **compare a source's last recorded
position against its file** before theorising, and **reproduce against a `.backup` copy**
so you can prove the failure and then prove the fix.
