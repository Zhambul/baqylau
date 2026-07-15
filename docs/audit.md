# Audit system (always on)

Everything in these docs is ~20 short-lived hook processes plus detached tailers/watchers
coordinating through per-session and global SQLite state DBs (plus the few
deliberate files physics demands) — and almost every
failure used to be swallowed (`except Exception: pass`, `2>/dev/null`), so when a tab
stuck blue or a block never closed, the evidence evaporated with the processes.
**Every session is now audited into SQLite** so a bug can be chased after the fact.

- **Where:** `~/.claude/kitty-audit/audit.db` (one global DB, all sessions; override
  the dir with `CLAUDE_AUDIT_DIR`). WAL mode, so the many concurrent short-lived
  writers never block each other. Deliberately *not* under `/tmp` — session artifacts
  there are deleted at SessionEnd, and the audit must survive the session.
- **On/off:** ON by default; set `CLAUDE_AUDIT=0` (env / settings `env` block) to
  disable — every audit call becomes a no-op. The DB and spool are gitignored.
- **Never breaks a hook:** a failed DB write degrades to an append-only
  `spool.jsonl`, re-ingested on the next successful open — including failures of
  the auditor itself. Ingest claims the spool by an exclusive rename to
  `spool.jsonl.<pid>` (exactly one concurrent drainer can win), and each pass also
  ADOPTS orphaned claim files whose pid is dead (`core.state.pid_alive`; EPERM =
  alive foreign-owned, left alone) via the same claim-by-rename — a drainer
  hard-killed between claim and remove no longer strands its rows forever. A
  drain that fails mid-pass leaves the claim at the drainer's own pid suffix
  (renaming back could clobber a freshly re-created spool); it becomes an
  adoptable orphan the moment that process exits. Row chronology is each row's
  own `ts` column, so late-adopted rows land in the right place. The tab-status
  path writes fire-and-forget in the background, so the latency-sensitive colour
  path is never blocked.
- **Retention:** sessions older than 30 days are pruned at SessionEnd — every
  per-session table including `pane_events` (once omitted from the prune loops,
  which grew it unboundedly with permanently orphaned rows).

What's recorded (all tables keyed by `session_id`, written by `core/audit.py`):

| table | one row per |
|---|---|
| `sessions` | Claude session — cwd, transcript, mirror log, window id, start/end, env. A SessionEnd that can't reach the DB spools a `session_end` pseudo-row (same mechanism as `stream_end`), so a locked DB no longer leaves the session "(open)" forever |
| `hook_events` | hook invocation — **full stdin payload** + the handler's **decision** ("ignored: agent_id", "handed off to fg tailer: ■ failed (exit 1)", …) |

`hook_events` is fed two ways. The mirror's own handlers record the events they
process, *with* the decision they took. On top of that, a **universal subscriber**
records **every** event with its full payload, `handler = 'subscriber'`. It used to
be its own `async` settings entry (`bin/claude-audit.py hook subscriber`); since the
single-dispatcher refactor ([wiring.md](wiring.md)) the dispatcher writes it in-process at the end
of `route()` — `A.hook_event(d, handler="subscriber")` — for **all 30 hook events**,
so it still covers the ones nothing else listens to:
`PermissionRequest`/`PermissionDenied`, `PostToolBatch`, `MessageDisplay`,
`TeammateIdle`, `Pre`/`PostCompact`, `ConfigChange`, `CwdChanged`, `FileChanged`,
`WorktreeCreate`/`Remove`, `Elicitation`/`ElicitationResult`, `Setup`,
`UserPromptExpansion`, `InstructionsLoaded`. So nothing that happens in a session is
invisible to the audit, and a mirror-handler row can be cross-checked against the
subscriber's independent record of the same event.
| `tab_transitions` | tab-colour decision — dispatch, prev → new, applied *or skipped*, with the **reason** (replaces the old opt-in `CLAUDE_TAB_DEBUG` flat-file logs). "Applied" is **verified against kitty**: the `kitten @` exit code is captured, so a socket call that failed records `applied=0` + a "kitten @ failed rc=N" reason instead of claiming a colour change that never happened |
| `slots` | palette/liveness-slot event (`live`-table rows) — claim / claim-id / claim-pid / steal-stale / release-stale / claim-denied / release / release-id / release-pid / set-owner. `steal-stale` is an acquisition; each steal is preceded by a synthesized `release-stale` for the displaced dead holder, so the anomalies' claim/release pairing balances a healthy steal (pre-2026-07-15 sessions lack release-stale rows) |
| `streams` | detached tailer/streamer/watcher lifecycle — with the **end reason** (writer-gone / sentinel / stoppedByUser / parent-task-resolved / converted-ctrl-b / backstop-timeout / crash). Includes the **shell watchers** (`bg-watch`, `interrupt-watch`) — a watcher that dies mid-poll leaves an open row the `anomalies` query flags — and the codex watcher's **cross-session claims** (slots, kind `codex-claim`), so "why didn't session A show that codex run" is answerable. A streamer whose end couldn't reach the DB spools it and ingest applies it later, so it never falsely reads as "never ended" |
| `ops` | paint op written to the mirror log — full pane reconstruction, survives SessionEnd |
| `errors` | **swallowed exception — full traceback + context** (every `except: pass` site records before swallowing) |
| `spawns` | detached process launch — parent, child pid, argv, purpose |
| `state_files` | coordination-file transition — `.done` sentinels, `.fg-live`, `sub.done`, … — plus the **scoreboard sidecar's evolution**: every `bump` (deltas + resulting totals), every agent-spend bump (`bump-agent`: same, plus `meta` with agent_id/kind/model and the in/out/cache/create(+create_1h, the 2×-billed 1h cache-write share) split `cost_usd` priced — attribution and re-pricing without timestamp correlation), every transcript-spend fold (`bump-transcript`: token/cost delta + cursor), every team-message transition (`msg-transitions`), and each substream streamer's checkpoint bookends (`resume`/`final` on `sub.pos.<agent>`: adopted vs left-behind pos + dedup state — a mismatched pair is a broken idle-restart handoff) — so a wrong scoreboard number is traceable to the exact bump that skewed it. The scorebar's per-second `paused` ticks are deliberately **not** audited (they buried real bumps ~1000:1; the running total rides every other bump row) |
| `pane_events` | mirror/scoreboard **pane operation** — open / close / toggle / resize with `ok` verified against kitty (a mirror that failed to open, or a resize that changed nothing, is recorded — the kitten calls used to be silent) |

Explore it with the CLI (from the repo root):

```sh
python3 bin/claude-audit.py sessions            # recent sessions
python3 bin/claude-audit.py timeline  <sid> [limit] [--ops] [--otel]
                                                # merged chronological story
                                                # (--ops/--otel merge those high-volume tables in)
python3 bin/claude-audit.py errors    <sid>     # swallowed exceptions, full tracebacks
python3 bin/claude-audit.py anomalies <sid>     # canned queries for known bug signatures
python3 bin/claude-audit.py sql "<query>"       # free-form read-only SQL (mode=ro)
python3 bin/claude-audit.py sql-write "<query>" # read-write SQL for deliberate manual fixups
python3 bin/claude-audit.py prune [days]        # manual retention pass
```

**The warning light (live, push):** the audit used to be pull-only — every
swallowed exception was recorded, but nothing told the user the session was
degraded. **`core/errwatch.py`** now surfaces the `errors` table live: the
scorebar polls it every 5 s (`EW.POLL_S`, `mode=ro` — a probe that never creates
the DB) and shows an AMBER **`⚠ N` chip** on its `▪` row when N > 0, and emits an
AMBER **`⚠ audit: <script>: <exception>` one-liner into the mirror** for each new
row, exactly once (rowid checkpoint in the state-DB kv `errseen`, its advance
audited as a `state_files` row), flood-collapsed past 3 rows into one line
pointing at `bin/claude-audit.py errors <sid>`. GLOBAL rows (`session_id=''` —
auditor-outage rows, pre-session/CLI errors) are surfaced too, in EVERY live
session (an audit outage affects them all): counted on the chip, emitted as
`⚠ audit: global: …` one-liners, deduped per session via a second kv checkpoint
(`errseen-global`, its advances audited with `"global": true`). The watcher's own failure is
audited at most once per process and then silenced (the recursion guard — a
persistently failing watcher must not append an `errors` row per poll that the
next poll would report), so "the warning light is broken" still shows up as one
`errwatch.poll` row in `errors`. See [scoreboard.md](scoreboard.md) /
[mirror-pane.md](mirror-pane.md).

Or just hand Claude Code a session id: the **`audit-debug` skill**
(`.claude/skills/audit-debug/SKILL.md`) walks the triage — anomalies → errors →
timeline → targeted SQL — and names the bug from the evidence: which rows, which
code path, and a suggested fix.
