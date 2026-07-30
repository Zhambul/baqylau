# plugins/codex/facets.py — codex's SESSION-STATE FACETS: the two things a
# standalone codex host is DOING right now that the web shows live.
#
#   compacting  — armed on codex's PreCompact hook, cleared on PostCompact.
#                 Behind plugins.compacting(sid) → the ctx bar's breath.
#   fg-live     — opened when the rollout stream paints a foreground command
#                 block, taken when it closes it. Behind plugins.fg_running(sid)
#                 → the live ⏱ elapsed chip on that block.
#
# Both are the codex twins of rows plugins/claude_code writes, and both are
# WRITTEN here / READ here — the read side lives with the writer (docs/
# styleguide.md), which is the whole reason the dashboard stopped reading either
# by kv name.
#
# WHY THE TWO ARE WRITTEN FROM DIFFERENT PLACES (the load-bearing asymmetry).
# The compaction latch carries only "since when", so codex's own Pre/PostCompact
# HOOKS can arm and clear it — and must, because a compaction is exactly the
# stretch during which the rollout says nothing at all. The fg record carries
# WHICH MIRROR BLOCK is running, and the hook cannot know that. Measured
# 2026-07-31 against the audit trail and a real rollout:
#
#   hook payload  tool_use_id   "exec-dcecc41c-1683-410e-87cc-e44041b13d22"
#   rollout       call_id       "call_65mJCSCDWK6F7G9OxYCAD8PF"
#   mirror block  copy group    ops.new_group() — a per-session integer
#
# Three disjoint id spaces. Claude Code gets away with the hook because ITS
# tool_use_id is literally the copy group its own header ops are stamped with
# (cmd_pre.py); codex has no such id to reuse, so a hook-stamped record would
# name a block that does not exist and the chip would tick on nothing. The
# rollout STREAM is the one place holding the group id and the command's start
# at the same moment (stream.py _ro_exec / _exec_close, the standalone register),
# so it writes the record — and its own pid is the tailer liveness backstop,
# exactly as the Claude fg tailer's is.
#
# NESTED GUARD (load-bearing, both halves). A codex run inside a CLAUDE session
# writes into the CLAUDE host's state DB — where these keys belong to Claude's
# own hooks. Latching there would animate the host's ctx bar for a compaction
# that is not its own, and an fg record would collide with the one Claude Code's
# PreToolUse just wrote. So: the hook half runs only for a recorded STANDALONE
# host (dispatch.py's tabs.codex_host_win gate), and the stream half only in the
# REG_STANDALONE register.
#
# KEY NAMES. They match claude_code's spellings deliberately, because they mean
# the same thing and an audit `state_files` row then reads identically whichever
# host produced it. They are NOT a shared owner: a state DB belongs to exactly
# one session, a session to exactly one host, so each host owns its own storage
# key and the FAN-OUT's docstring (plugins.compacting / plugins.fg_running) owns
# the SHAPE both must produce.
import os
import time

from core import state as ST
from core.noaudit import load_audit

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)

COMPACT_KEY = "compacting"   # kv:      {ts, trigger}
FG_KEY = "fg-live"           # hand-off: {tid, ts, pid} — take-once


# --- compaction: the hook half ------------------------------------------------

def on_compact(d, sid):
    """Route codex's Pre/PostCompact into the compaction latch. Returns the
    DECISION string the dispatcher folds into its hook_event row (the audit's
    only record of what this chose), or "" when the event is not ours.

    The same latch/clear shape as plugins/claude_code/compact_fmt.py, and for
    the same reason: compaction emits nothing a read side could see, and the
    hook PAIR is the only signal that it is running. Never raises — the
    dispatcher's caller is a hook (CLAUDE.md: hooks must never block or fail),
    so every failure audits and degrades to "no animation"."""
    from core import paths as P
    ev = d.get("hook_event_name") or ""
    if ev not in ("PreCompact", "PostCompact"):
        return ""
    if d.get("agent_id"):
        # a codex SUBAGENT's event: it has no compaction of its own, and the
        # bar this would animate is the MAIN thread's (compact_fmt's own rule)
        return "compact ignored (agent_id present)"
    log = P.mirror_log(sid)
    if ST.parked(log):
        # no live state DB — nothing here may CONNECT: the file's existence is
        # the session-alive signal watchers poll, and kv_set would create it
        return "compact ignored (no state DB)"
    sdb = ST.db_path(log)
    trigger = str(d.get("trigger") or "")      # codex sends "manual" for /compact
    try:
        if ev == "PreCompact":
            ST.kv_set(log, COMPACT_KEY, {"ts": time.time(), "trigger": trigger})
            A.state_file(log, sdb, COMPACT_KEY,
                         {"action": "write", "trigger": trigger})
            return "compacting armed (%s)" % (trigger or "?")
        prev = ST.kv_get(log, COMPACT_KEY)
        if prev is None:
            # no arm to clear: a PostCompact whose PreCompact predates this
            # handler (a session running across the deploy), or a duplicate
            return "no %s latch to clear" % COMPACT_KEY
        ST.kv_del(log, COMPACT_KEY)
        took = time.time() - float(prev.get("ts") or 0) \
            if isinstance(prev, dict) and prev.get("ts") else 0.0
        A.state_file(log, sdb, COMPACT_KEY,
                     {"action": "remove", "reason": "PostCompact",
                      "trigger": trigger, "took_s": round(took, 1)})
        return "compacting cleared (%s, %.1fs)" % (trigger or "?", took)
    except Exception:
        A.error(log, "codex compact latch (%s)" % ev, {"sid": sid})
        return "compact latch failed (%s)" % ev


# --- the foreground command: the rollout-stream half --------------------------

def fg_open(log, gid, ts=None):
    """The standalone rollout stream has just PAINTED a foreground command block
    with copy group `gid`: record that the block is running. `ts` is the exec
    record's own start (epoch) when the rollout carried one — the chip counts
    from the command's real start, not from when the tailer noticed it.

    `pid` is this TAILER's pid, the same liveness backstop Claude's fg record
    carries: a codex turn aborted mid-exec writes no closing record and fires no
    hook (`turn_aborted` is a rollout note, not an event we can wait on), so
    without it a killed run would tick a chip forever. The stream dies with the
    run; a dead pid reads as not-running."""
    rec = {"tid": gid, "ts": float(ts if ts is not None else time.time()),
           "pid": os.getpid()}
    try:
        if not ST.hand_put(log, FG_KEY, rec):
            A.error(log, "write codex fg-live record", {"tid": gid})
            return
        A.state_file(log, "state:" + FG_KEY, "write", rec)
    except Exception:
        A.error(log, "codex fg_open", {"tid": gid})


def fg_close(log, gid):
    """…and it has just closed that block (the `■ finished · 3.2s` chip). TAKE
    the record — take-once presence IS the liveness signal, so the chip retires
    the moment the block does, with no second source to keep in step.

    Matched on `tid`, so a stale record from an earlier command can never be
    eaten by a later block's close (the cross-wiring cmd_pre's own match guard
    exists to prevent)."""
    try:
        if ST.hand_take(log, FG_KEY, match={"tid": gid}) is not None:
            A.state_file(log, "state:" + FG_KEY, "remove", {"tid": gid})
    except Exception:
        A.error(log, "codex fg_close", {"tid": gid})


# --- the read halves (behind plugins.compacting / plugins.fg_running) ---------

def compacting(sid, sdb=None):
    """The RAW compaction latch `{ts, trigger}`, or None. The TTL that ages an
    un-cleared latch out belongs to the reader (dashboard config.COMPACT_MAX_S)
    — see plugins.compacting. An interrupted codex compaction fires no
    PostCompact either, so the animation must be able to fail OFF without this
    process being alive to retract anything."""
    from core import sessionapi as API
    if sdb is None:
        sdb = API.state_db_for(sid)
    rec = API.kv_at(sdb, COMPACT_KEY) if sdb else None
    return rec if isinstance(rec, dict) else None


def fg_running(sid, sdb=None):
    """The in-flight foreground command as {"g", "start_ts"}, or None — `g`
    being the mirror block's copy group (see fg_open). PEEKS, never takes: the
    stream's own close is the consumer, and eating the record here would leave
    the block with no finish. A dead tailer pid reads as not-running (an aborted
    turn), and a parked session yields None for the same reason."""
    from core import sessionapi as API
    if sdb is None:
        sdb = API.state_db_for(sid)
    if not sdb:
        return None
    rec = ST.hand_peek_at(sdb, FG_KEY)
    if not isinstance(rec, dict):
        return None
    gid, ts, pid = rec.get("tid"), rec.get("ts"), rec.get("pid")
    if not gid or not ts:
        return None
    if not (pid and ST.pid_alive(pid)):
        return None
    return {"g": gid, "start_ts": ts}
