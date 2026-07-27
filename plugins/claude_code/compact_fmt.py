# plugins/claude_code/compact_fmt.py — the COMPACTION-IN-PROGRESS tracker.
# Entry point: claude-compact-fmt.py; in-process via dispatch.py on
# PreCompact (arm) and PostCompact (clear).
#
# WHY: the web dashboard's ctx bar animates while the conversation is being
# compacted (docs/dashboard.md, *Compaction on the ctx bar*). Compaction is a
# genuinely LONG operation — 104-139s across the seven measured runs in the
# audit trail — during which Claude Code emits no tool call, no reply and no
# transcript growth the read side could see. The tab goes magenta (dispatch's
# PreCompact step) exactly as it would for any think, so until now the whole
# two minutes were indistinguishable from a slow turn, and the ctx bar sat
# frozen at its pre-compaction figure with no hint that the number was about
# to fall off a cliff.
#
# The two hooks ARE the window: PreCompact fires when compaction starts,
# PostCompact when it finishes, both on the main session (a subagent never
# compacts). So this handler is a pure kv latch — write on the first, delete
# on the second — and the read side turns "the latch is set" into the
# animation.
#
# WHY A TTL RATHER THAN TRUSTING THE CLEAR: a PreCompact whose PostCompact
# never arrives is real — the audit trail holds one (session d1646cb7,
# 2026-07-24) — because a compaction can die on an API error or be
# interrupted, and neither fires a closing hook (the same no-hook-on-cancel
# invariant as everywhere else in this repo, CLAUDE.md). A latch with no
# expiry would leave that session's bar animating forever, so the READ side
# ages it out (dashboard COMPACT_MAX_S) rather than this side inventing a
# timer: an animation must fail OFF, and a hook process that has already
# exited cannot retract anything.
import time

from core import state as ST
from plugins.claude_code import hookkit as H

A = H.A

KEY = "compacting"


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    ev = d.get("hook_event_name") or ""
    if d.get("agent_id"):
        # a subagent has no compaction of its own; if one is ever routed here
        # it is not the MAIN thread's ctx bar it would be talking about
        return H.ignore(d, "subagent event (agent_id present)")
    if ST.parked(LOG):
        # no live state DB = an unhosted session (headless claude -p / daemon)
        # or one already parked — NOTHING here may connect: the DB's
        # file-existence is the session-alive signal watchers poll, and kv_get
        # would CREATE it (the ghost-DB bug class). No pane ⇒ no web card.
        return H.ignore(d, "no state DB (unhosted session)")
    sdb = ST.db_path(LOG)
    trigger = d.get("trigger") or ""          # "manual" (/compact) | "auto"
    if ev == "PreCompact":
        rec = {"ts": time.time(), "trigger": trigger}
        ST.kv_set(LOG, KEY, rec)
        A.state_file(LOG, sdb, KEY, {"action": "write", "trigger": trigger})
        A.hook_event(d, decision="compacting armed (%s)" % (trigger or "?"))
        return
    # PostCompact: the conversation is compacted and the transcript now carries
    # the compact_boundary record context_probe reads the new occupancy from.
    prev = ST.kv_get(LOG, KEY)
    if prev is None:
        # no arm to clear — a PostCompact whose PreCompact predates this
        # handler (a session running across the deploy), or a duplicate
        return H.ignore(d, "no %s latch to clear" % KEY)
    ST.kv_del(LOG, KEY)
    took = time.time() - float((prev or {}).get("ts") or 0) \
        if isinstance(prev, dict) and prev.get("ts") else 0
    A.state_file(LOG, sdb, KEY,
                 {"action": "remove", "reason": "PostCompact",
                  "trigger": trigger, "took_s": round(took, 1)})
    A.hook_event(d, decision="compacting cleared (%s, %.1fs)"
                             % (trigger or "?", took))


def entry():
    H.run(main)
