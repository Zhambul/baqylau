# OTEL cost pipeline (`plugins/otel/`)

The authoritative source of the scoreboard's token/cost counters
(see [scoreboard.md](scoreboard.md) for the display, [wiring.md](wiring.md) for the
telemetry env it requires).

  - **Tokens + cost are OTEL-authoritative (`plugins/otel/`).** Cost/token accounting
    no longer comes from folding the transcript — it comes from **OpenTelemetry**.
    Claude Code, with telemetry enabled (env in settings.json, see [wiring.md](wiring.md)), exports
    `claude_code.token.usage` / `claude_code.cost.usage` after **every API request**,
    tagged with `session.id`, `query_source` (`main`/`subagent`/**`auxiliary`**),
    `model`, and `type`. A per-machine singleton HTTP receiver
    (`claude-otlp-receiver.py`, spawned at SessionStart via
    `plugins/otel/on_session_start` → the detach-fast `claude-otlp-launch.py`) ingests
    these and writes the SAME per-session counters the fold used to
    (`tk_in`/`tk_out`/`tk_read`/`tk_create` from the `type` attribute, `cost` from
    cost.usage), keyed by `session.id`, so the scorebar display is unchanged.
    **Why OTEL and not the transcript** (which is what shipped before): folding the
    transcript structurally CANNOT see Claude Code's hidden "auxiliary" agents
    (summarizers / title generators) — they fire only a `SubagentStop` with no usage
    in the payload and write no transcript, yet their (cache-read-dominated) spend
    reaches `/cost`. Measured: on one session those hidden agents were **11.6%** of
    cost, entirely invisible to the fold. OTEL captures them as `query_source=auxiliary`.
    **Why delta temporality**: Claude Code exports delta datapoints (verified
    non-monotonic per session), so the receiver SUMS them; the settings env pins
    `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`. **Why a global
    singleton**: the OTLP endpoint is a process-global env var, so ONE receiver serves
    every session (dual-guarded by a pid-lock in `core.paths.OTLP_DB` AND the port
    bind; a duplicate exits with a clean `duplicate` streams row). **The long-lived
    receiver must revalidate its cached state-DB connection.** `core.state._connect`
    caches one connection per DB path (right for the short-lived hook processes it was
    built for), but a `--compact`/`--resume` cycle parks the DB with
    `os.replace(db, db+".keep")` and creates a FRESH inode at the same path — a cached
    connection then keeps writing token counters to the ORPHANED `*.keep` inode while
    the scorebar reads the new live DB, and *no error is ever raised* (both are valid
    DBs), so the Σ breakdown silently goes blank. `_connect` therefore revalidates by
    `st_ino` on every call: same inode → reuse; a DIFFERENT inode at the path → close
    the stale fd (fixing an fd-leak too) and reconnect to the fresh DB; the path simply
    GONE (parked, not yet recreated) → keep the stale conn and **never recreate** (the
    live path's absence is the session-alive exit signal streamers poll). The
    `anomalies` CLI cross-checks this: `bump-otel` rows for a session whose live state
    DB has no `tokens`/`tk_read` counter is the stranded-receiver signature.
    **…and must EVICT it once the session parks.** That path-gone stale-conn behavior
    is deliberate for *per-session* processes, but the receiver is the one *multi*-
    session state-DB writer: without eviction every ENDED session pinned its cached
    connection + WAL/SHM fds for the receiver's lifetime (bounded only by the 900s
    idle exit). `receiver.sweep_parked()` runs on every batch and every serve-loop
    tick: for each session it has connected to, a `state.parked()` exists-probe, and
    on park a `state.evict(log)` (close + drop from the cache — an API reserved for
    exactly this multi-session case; per-session processes must never call it) plus
    an audited `state_files` row, action `evict-parked`. **Straggler datapoints for a
    parked session are DROPPED, audibly, never written.** `write_session` guards with
    `parked()` *before* connecting — a connect would CREATE a fresh DB at the live
    path, whose file-existence is the session-alive signal (the phantom-session
    hazard). The counters are final by then (the SessionEnd fold fallback, gated on
    `otel_seen`, ran before the park), so the deltas are dropped with a `state_files`
    row, action `drop-otel-parked`, carrying the deltas + raw datapoints — NOT rows
    in the audit `otel` table, whose `SUM(value)` must keep equalling the live
    counters. A connect FAILURE past the parked check (locked/perms/corrupt live DB)
    is the same audited drop, action `drop-otel-noconn` — it used to `return False`
    silently, and because the dropped rows never reached the `otel` table the
    SUM(otel)==counters invariant still held, so no anomaly could ever see it. **Codex is exempt**:
    it runs in a separate process OTEL can't see, so it keeps its own rollout fold
    (`bump-agent`, `meta.kind=codex`). Every raw datapoint is captured in the audit
    `otel` table (`python3 bin/claude-audit.py otel <sid>`), so the counters are fully
    reconstructible.
  - **The transcript fold survives ONLY as a resilience fallback.**
    `accounting.bump_transcript()` (transcript JSONL → `txpos` cursor → the same
    `tk_*`/`cost` counters) now runs from `claude-stop-fmt.py` on **`SessionEnd` only**,
    and only when the receiver wrote nothing for the session (`otel_seen == 0`:
    telemetry off, receiver down, or a machine without the env). So a session that
    never exported still isn't $0. It runs as an ORDERED dispatcher step *before*
    `claude-split.py` parks the state DB, and is idempotent (the `txpos` cursor), so a
    telemetry-on session skips it and never double-counts (a `bump-transcript` row
    alongside `bump-otel` rows is the double-count regression its anomaly flags). The
    agent-streamer footer (`claude-substream.py`) and `reconcile_spend`
    (`claude-subagent-fmt.py`) likewise stopped bumping cost — OTEL's
    `query_source=subagent` books agent spend live, including a crashed streamer's
    tail — though `reconcile` still records a transcript cross-check row and the footer
    still prints its `≈ $` estimate.
