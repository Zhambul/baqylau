---
name: audit-debug
description: Diagnose a session bug from the canonical event store and the operational audit trail. Use when the user reports a bug in a session (missing messages, a frozen dashboard feed, a session that looks alive but says nothing, a control gesture that did nothing, wrong usage/cost numbers) and gives a session id — or asks to investigate "what happened in session X".
---

# audit-debug — root-cause a session bug from the canonical evidence

Given a session id, reconstruct what happened and name the bug **from evidence, not
guesswork**. Two SQLite stores hold it, and knowing which one answers which question
is most of the skill.

## Where the data is

Both live under the application data directory — `~/.local/share/baqylau`, overridable
with `$BAQYLAU_DATA_DIR` (`app/data.py`). Open them **read-only** (`file:<path>?mode=ro`)
so triage can never mutate the evidence.

| store | path | what it answers |
|---|---|---|
| **event store** | `<data>/events.db` | What the session *did*. The product's own record: every observation and its interpretation. This is the primary evidence. |
| **operational audit** | `<data>/audit/audit.db` | What the *machinery* did and where it degraded: swallowed exceptions, detached processes, control-plane gestures, browser telemetry. Env: `$BAQYLAU_AUDIT_DIRECTORY`, `BAQYLAU_AUDIT=0` disables (`core/audit.py`). |

CLI: `python3 bin/baqylau-audit.py session <session_id>` dumps every raw observation for a
session with its translation and the canonical events it produced; `... raw <raw_event_id>`
does one. Both print JSON (payloads base64-encoded, so the bytes are exact). For anything
aggregate, query the DBs directly with `sqlite3` — there is no canned-anomaly command.

## The model (read this before querying)

Evidence flows one way, and each stage is recorded:

```
source (file/stream/hook)  →  raw_events  →  translation_records  →  canonical_events
                                   │                                        │
                              source_checkpoints                   canonical_provenance
```

- **`raw_events`** is *immutable evidence*: the exact bytes a source produced. Reusing a
  `raw_event_id` with a different payload raises `EventIdentityConflict` — that is
  corruption, not convergence.
- **`canonical_events`** is an *idempotent projection*. `event_id` names a **fact**, so
  several independent sources may converge on one event; re-observing it is a no-op that
  only appends provenance. The store keeps the **first writer** and does **not** compare
  bodies — a later, differing rendering is not lost, it stays recoverable from its own raw
  event. (Raising on a disagreement instead used to abort the whole observation pass.)
- **`cursor`** (`INTEGER PRIMARY KEY AUTOINCREMENT`) is the monotonic ordering key
  everything pages by. `event_id` is *not* an ordering key.
- **`source_checkpoints`** is how far each source has been read. **A stuck checkpoint is
  the single most diagnostic value in the whole store** — see the first bug shape.

**Who drives what.** `ObservationRunner` (`app/observe.py`) is the ONE scheduler that
polls every *pulled* source (transcripts, rollouts, foreground output, liveness, process
state) — it runs as a thread inside the dashboard server process, every
`OBSERVATION_INTERVAL_SECONDS` (0.25s), over the `RECENT_SESSION_COUNT` most recent
sessions plus everything already active. **Pushed** sources (`hook`, `otel`, `account`) are
written by separate short-lived processes and do not depend on it. That split is the key
asymmetry behind the headline failure mode: when the scheduler stops, a session keeps
producing hook and otel events and therefore still looks alive, while its conversation
silently stops.

## Schema

### `events.db`

| table | one row per | key columns |
|---|---|---|
| `raw_events` | one observation, verbatim | `raw_event_id` (PK), `session_id`, `harness`, **`source_type`**, `source_name`, `source_position`, `actor_id`, `parent_actor_id`, `observed_at`, `encoding`, `payload` |
| `translation_records` | one translation verdict | `raw_event_id` (PK), `translator_version`, **`decision`** ∈ `translated` / `ignored_nonsemantic` / `ignored_unknown` / `translation_failed`, `reason`, `completed_at` |
| `canonical_events` | one interpreted fact | **`cursor`** (monotonic), `event_id` (unique), `schema_version`, **`event_type`**, `session_id`, `actor_id`, `turn_id`, `parent_actor_id`, `harness`, **`occurred_at`** (NULLABLE), `accepted_at`, `payload` |
| `canonical_provenance` | one (event, evidence) link | `event_id`, `raw_event_id`, `event_order`, **`storage_result`** ∈ `accepted` / `deduplicated` |
| `source_checkpoints` | how far one source is read | `source_identity`, `session_id`, **`position`** |
| `session_harness` | one recognized session | `session_id`, `lead_actor_id`, `harness`, `native_session_id`, **`source_reference`** (the transcript/rollout path), `working_directory`, `native_process_id` |
| `session_application_state` | one session's UI state | composer text/origin/sequence, queued messages, dialog attention id/answers |
| `actor_harness` | one actor's owning harness | `session_id`, `actor_id`, `harness` |
| `event_store_metadata` | store-wide settings | `schema_version` — must equal `domain.codec.SCHEMA_VERSION` or the store refuses to open |

**`source_type` vocabulary** (which observer produced the evidence): pushed —
`hook`, `otel`, `account`; pulled — `transcript`, `rollout`, `foreground_output`,
`liveness`, `process`, `repair`, and the child/teammate variants
`child_transcript`, `teammate_transcript`, `child_rollout`, `child_replay`,
`sidecar_rollout`.

**`event_type` vocabulary** (33, from `domain.events.EVENT_TYPES`):
`session.started` / `.finished` / `.title_changed` / `.account_changed` /
`.working_directory_changed` · `actor.started` / `.finished` / `.name_changed` /
`.description_changed` / `.message_sent` / `.assignment_started` / `.assignment_finished` ·
`turn.started` / `.finished` / `.aborted` · `message.created` · `reasoning.created` ·
`operation.started` / `.progressed` / `.finished` / `.input_provided` · `file.accessed` ·
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

### `audit.db` (`core/audit.py`)

| table | one row per | key columns |
|---|---|---|
| `errors` | swallowed exception | `ts`, `session_id`, `script`, **`func`**, `traceback` (full), `context`, `pid` |
| `state_files` | notable non-error act | `ts`, `session_id`, `path`, **`action`**, `content`, `script`, `pid` |
| `spawns` | detached process launch | `ts`, `session_id`, `parent_script`, `child_pid`, `argv`, `purpose` |
| `streams` | detached tailer/watcher | `session_id`, `kind`, `agent_id`, `task_id`, `src_path`, `pid`, `started_at`, `ended_at`, `end_reason`, `lines_emitted` |

`state_files.action` values in use: `browser-event`, `browser-optimistic-action`,
`browser-client-failure` (the frontend telemetry channel — what the browser saw that the
server cannot), `web-reject` (a control POST bounced by the request guard, `content` names
the code and why), `web-push`, `notification-route`, `notification-suppressed`,
`telegram-notify`, plus `observation (...)` failures recorded as `errors`.

## Triage order

1. **Establish the session exists and which harness owns it.**
   `SELECT * FROM session_harness WHERE session_id='<sid>'` — `harness` decides whose
   translator and whose control gestures apply; `source_reference` is the file the pulled
   sources read.
2. **Compare each source's checkpoint against its source.** This is the fastest way to
   find a frozen feed (below).
3. **Ask whether the scheduler is alive** — the `max(observed_at)` per `source_type`
   query below. It answers "is this session-specific or machine-wide?" in one row set.
4. **Read the translation verdicts** for the session: an `ignored_unknown` or
   `translation_failed` run explains a *specific* thing missing while everything else
   flows.
5. **`errors` around the symptom's timestamp**, then `state_files` for the gesture.
6. **Exact bytes**: `bin/baqylau-audit.py session <sid>` when you need to see what a
   source actually emitted rather than what we made of it.

## Known bug shapes → what to look for

### A session looks alive but its conversation stops (messages missing, feed frozen)

**The headline shape, and the first thing to rule out.** The scheduler thread died, so
every *pulled* source froze while *pushed* sources kept flowing — which is exactly why the
session still looks alive.

One query names it:

```sql
SELECT source_type, datetime(max(observed_at),'unixepoch','localtime')
FROM raw_events GROUP BY 1 ORDER BY 2 DESC;
```

If `hook`/`otel` are current but `transcript`/`rollout`/`foreground_output`/`liveness`/
`process` all stop at the **same instant**, the `ObservationRunner` thread stopped at that
instant — machine-wide, every session, not just this one. Confirm with the checkpoint:

```sql
SELECT source_identity, position FROM source_checkpoints WHERE session_id='<sid>';
```

against the real size of `session_harness.source_reference`. A `position` far below the
file size, frozen, with the file still growing, is conclusive. (Measured instance: position
10,744 of 527,284 — 12 of 165 lines — and exactly two `message.created` events for a
session that had run for hours.)

`ObservationRunner` now contains failures per source *and* per pass, auditing each swallow
as an `errors` row with `func` `observation (source drain)` / `observation (observation
pass)` and a `context` naming the `source_identity`. **So on a current build, look for
those rows first** — they name the failing source directly. Their *absence*, together with
frozen pulled sources, means the thread died some other way (or the server is running
pre-fix code — it does **not** hot-reload; check `bin/baqylau-dashboard.py status` and the
process start time against the fix).

A restart is not a fix on its own: the source re-drains from its checkpoint and, if the
underlying record still fails, freezes again within seconds. Reproduce before and after:

```python
BAQYLAU_DATA_DIR=<a .backup copy>  # never the live store
from app.bootstrap import build_default_application
build_default_application().observation_runner.run_once()
```

Take the copy with `sqlite3 "file:<events.db>?mode=ro" ".backup '<dest>'"` — a plain `cp`
of a WAL database yields `database disk image is malformed` and wastes a triage cycle.

### The dashboard is unreachable, or needed several refreshes to load

Check the operational audit first — this is machinery, not session data:

```sql
SELECT datetime(ts,'unixepoch','localtime'), func, substr(traceback,-400)
FROM errors WHERE func LIKE 'dashboard%' ORDER BY ts DESC LIMIT 10;
```

- **`unsupported event-store schema version: N`** — `events.db` was migrated by newer code
  while this process expects `domain.codec.SCHEMA_VERSION`, or vice versa. The server
  crash-loops. The companion signature is a long run of **`dashboard serve (lock denied)`**
  rows every ~10s (the supervisor retrying against a port/lock the dying process holds) and
  **`CanonicalCodecError: unsupported canonical schema version: N`** from any read path
  decoding older rows. Fix by getting code and store to the same version and restarting
  once; the retry rows stop the moment one server holds the lock.
- Confirm what the store actually says:
  `SELECT * FROM event_store_metadata;` vs
  `python3 -c "from domain.codec import SCHEMA_VERSION; print(SCHEMA_VERSION)"`.

### One specific thing never appears, but everything else flows

Not the scheduler — the **translator**. Read the verdicts:

```sql
SELECT t.decision, t.reason, count(*)
FROM translation_records t JOIN raw_events r USING(raw_event_id)
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

### An event's content looks wrong / two sources disagree

Convergence is by design: several sources may describe one fact. Read the fan-in:

```sql
SELECT event_id, count(DISTINCT raw_event_id) n
FROM canonical_provenance GROUP BY 1 HAVING n > 1 ORDER BY n DESC LIMIT 10;
```

Multi-raw events are **normal** (measured: ~200 events with 2–4 sources, one with 32).
`storage_result` says which observation was `accepted` (the first writer, authoritative)
and which were `deduplicated`. **The first writer wins, and bodies are not compared** — so
if the stored rendering is the poorer one, the fix belongs in the *ordering or the identity
derivation*, not in the store. To see what the losing observer said, pull its raw event:
`bin/baqylau-audit.py raw <raw_event_id>` — nothing is lost.

An `EventIdentityConflict` naming a **raw** event (`raw event identity reused`) is
different and is a genuine corruption signal: the same evidence id arrived with different
bytes.

### A control gesture (send / interrupt / rename / answer) did nothing

The server-side trace is `state_files`; the browser-side trace is the same table's
telemetry actions, and you usually need both:

```sql
SELECT datetime(ts,'unixepoch','localtime'), action, substr(content,1,300)
FROM state_files WHERE session_id='<sid>' ORDER BY ts;
```

- A **`web-reject`** row means the request arrived and the guard bounced it — `content`
  carries the code and reason (missing header, foreign origin, read-only).
- A **`browser-client-failure`** row is the browser reporting a request that failed on its
  side; paired with no server row at all, the request never arrived (tunnel/upstream drop).
- **No row of any kind** means the gesture never left the page — look at
  `browser-event` rows for the load and any JS error, and remember a stale cached SPA is a
  standing cause (the server restart bumps its build id but cannot evict the browser's
  cache; the user must hard-reload).

Note the tunnel is a distinct failure domain from the bind: reproduce against
`http://127.0.0.1:8377` before blaming the application. A request that is 200 locally and
4xx/3xx through `https://baqylau.zhambyl.top` is a proxy concern, not an app bug.

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

Two habits that paid off and are cheap to repeat: **compare a checkpoint against its
source** before theorising, and **reproduce against a `.backup` copy** so you can prove the
failure and then prove the fix.
