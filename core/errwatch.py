# core/errwatch.py — the audit WARNING LIGHT: surface the flight recorder live.
#
# The audit `errors` table records every swallowed exception (the
# hooks-must-never-fail invariant means failures degrade silently), but it was
# pull-only — the user never learned a session was degraded until they went
# digging with `bin/claude-audit.py errors <sid>`. This module is the single
# owner of the live surface over that table:
#
#   * the scorebar's `⚠ N` chip (chip_part) — the session's total swallowed-error
#     count, shown ONLY when N > 0, and
#   * the mirror's `⚠ audit: <script>: <exception>` one-liner blocks (err_ops) —
#     each NEW errors row emitted into the ops stream exactly once, so
#     degradation is seen in context, not just counted.
#
# WHO polls/emits: claude-scorebar.py's main loop calls poll() every POLL_S. The
# scorebar is the one long-lived per-session process that already owns a slow
# ambient poll (the ✉ census) and already emits mirror ops (emit_events), so it
# gets the audit poll too — the renderer's drain loop was rejected because it
# repaints on SIGWINCH/backfill and has no natural once-only cadence, and a
# per-hook check would open the GLOBAL audit DB from every short-lived process.
# The audit DB is a different, global DB from the per-session state DB the
# scorebar ticks against at TICK_S (0.25s) — polling it 4×/s per session is
# waste, so poll() runs at its own slow cadence and the count is memoized
# between ticks by the caller. A `counters` bump from A.error itself was
# rejected: core/audit.py must not import core/state (the audit must stay a
# leaf the state layer can depend on, not the reverse).
#
# Dedupe: the last-seen errors rowid is persisted in the state DB kv
# (KV_KEY) — parked/restored with the session, so a scorebar restart, mirror
# toggle, or --resume never re-emits old rows. Flood control: more than
# FLOOD_N new rows in one poll collapse into a single "⚠ audit: N new errors
# (bin/claude-audit.py errors <sid>)" line pointing at the CLI.
#
# Probe rules: the audit DB is opened READ-ONLY (mode=ro uri) per poll and the
# open is skipped when the file doesn't exist — a probe must never create the
# DB (its schema belongs to core/audit._connect alone).
#
# RECURSION GUARD (an error in the error-watcher must not recurse): poll()'s
# own failure is audited AT MOST ONCE per process (_self_audited, the same
# once-per-process shape as audit.py's _FAILED) and then swallowed silently —
# without the guard, a persistent poll failure would append one errors row per
# POLL_S forever, and each row would itself be new material for the next poll.
# The single audited row is then surfaced by the next SUCCESSFUL poll like any
# other error — "the warning light is broken" is itself a warning. Emitting
# the ⚠ ops goes through O.emit → A.ops (the `ops` table, not `errors`), so a
# healthy emission never feeds the poll it came from.
import os

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import
from core import ops as O
from core import paths as P
from core import state as St
from core import streamfmt as SF

POLL_S = 5.0        # audit-DB poll cadence — deliberately slower than the
                    # scorebar's TICK_S (0.25s): the audit DB is global, not
                    # per-session; the chip is memoized between polls
FLOOD_N = 3         # > this many NEW rows in one poll collapse into one line
TEXT_MAX = 120      # per-line char cap for the ⚠ mirror one-liner
KV_KEY = "errseen"  # state-DB kv: last errors rowid already emitted to the mirror

_self_audited = False   # poll()'s own failure audited once per process (see header)


def chip_part(n):
    """The scorebar chip segment for n swallowed errors, in the ▪-row
    (kind, text) shape — kind 'warn' renders AMBER (a degradation warning,
    distinct from the row's RED ✗ command failures)."""
    return ("warn", f"⚠ {int(n)}")


def _summary(tb):
    """The one meaningful line of a stored traceback — the LAST non-empty line
    (the 'ValueError: …' summary; the first line is just 'Traceback …')."""
    for ln in reversed((tb or "").strip().splitlines()):
        if ln.strip():
            return ln.strip()
    return "?"


def err_ops(rows, sid):
    """Mirror paint ops for a batch of NEW errors rows [(id, script, traceback),
    …]: one AMBER ⚠ label per row, or — past FLOOD_N — a single collapsed line
    pointing at the audit CLI. Text is line-capped via streamfmt.cap (the shared
    truncation vocabulary) and char-capped to TEXT_MAX."""
    if len(rows) > FLOOD_N:
        return [O.label(f"⚠ audit: {len(rows)} new errors "
                        f"(bin/claude-audit.py errors {sid})", O.AMBER)]
    out = []
    for _id, script, tb in rows:
        text = SF.cap(f"⚠ audit: {script}: {_summary(tb)}", 1)
        out.append(O.label(text[:TEXT_MAX], O.AMBER))
    return out


def poll(log, sid=None):
    """One warning-light pass: read this session's audit `errors` rows
    (mode=ro — never creates the DB), emit a ⚠ mirror block for each row not
    yet seen (rowid > the KV_KEY checkpoint, flood-collapsed), advance the
    checkpoint, and return the session's TOTAL error count for the chip.
    Returns None when the count couldn't be determined (audit off / DB absent
    / failure) so the caller keeps its memoized value."""
    global _self_audited
    try:
        if not A.enabled():
            return None
        db = A.db_path()
        if not db or not os.path.exists(db):
            return None
        sid = sid or P.sid_from_log(log)
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            n = conn.execute("SELECT COUNT(*) FROM errors WHERE session_id=?",
                             (sid,)).fetchone()[0]
            rows = []
            if n:
                last = int(St.kv_get(log, KV_KEY) or 0)
                rows = conn.execute(
                    "SELECT id, script, traceback FROM errors "
                    "WHERE session_id=? AND id>? ORDER BY id",
                    (sid, last)).fetchall()
        finally:
            conn.close()
        if rows:
            # Checkpoint BEFORE emitting: a failing emit must not re-emit the
            # same rows every POLL_S forever (at-most-once beats at-least-once
            # for an ambient warning; the audit `ops` rows say if one dropped).
            St.kv_set(log, KV_KEY, int(rows[-1][0]))
            # Audit the checkpoint advance (state_files, like every other
            # coordination write) so "why was this error never shown" / "why
            # twice" is answerable: which rowids one poll consumed is a row.
            A.state_file(log, St.db_path(log), "errseen",
                         {"last": int(rows[-1][0]), "new": len(rows)})
            O.emit(log, *err_ops(rows, sid))
        return int(n)
    except Exception:
        if not _self_audited:
            _self_audited = True
            A.error(log, "errwatch.poll (warning light dark this session)")
        return None
