# plugins/claude_code/stop_fmt.py — Stop/StopFailure accounting flush
# Entry point: claude-stop-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-stop-fmt.py — main-session Stop / StopFailure accounting flush.
#
# The scoreboard's token/cost totals are folded out of the session transcript by
# claude_ops.bump_transcript, but that only ran from the Bash/file PostToolUse
# hooks. So the LAST assistant turn of a session — a reply with no trailing tool
# call — was never folded: its tokens and (cache-read-dominated) cost silently
# dropped, leaving the scoreboard a few % under `claude --resume`'s real total.
# Wired to Stop (fires at the end of EVERY turn, tool-terminated or not) this
# closes that tail; every turn is folded before the next begins. It is ALSO wired
# to SessionEnd (dispatch.py) as a backstop: the closing assistant line of the very
# last turn can be flushed to the transcript a beat AFTER that turn's Stop hook read
# it, so the final-turn Stop fold lands short of EOF and the last reply's
# (cache-read-dominated) cost drops. The SessionEnd fold catches it — the transcript
# is fully flushed by then. It runs as an ORDERED dispatcher step BEFORE claude-split.py
# parks the state DB (they are no longer separate racing hook processes), and is
# idempotent via the txpos cursor, so it is a no-op whenever Stop already reached EOF.
#
# It ONLY folds accounting — it paints nothing and touches no tab colour. The tab's
# Stop dispatch stays claude-tab-status.py (which is read-only on the state DB by
# design); this handler is the one sanctioned Stop-time WRITER.
#
# Wire it alongside the existing Stop tab dispatch (see README § Wiring):
#   Stop         -> claude-stop-fmt.py   (in addition to claude-tab-status.py stop)
#   StopFailure  -> claude-stop-fmt.py   (turn ended on an API error — still fold
#                                         whatever landed in the transcript)
import os, sys

from core import ops as O
from plugins.claude_code import accounting as ACC
from plugins.claude_code import hookkit as H
from plugins.claude_code import subagent_fmt as SUB

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    # A subagent/teammate Stop is inner — its own streamer (claude-substream.py)
    # bumps that agent's spend; folding the main transcript here would be wrong
    # (and it isn't the agent's transcript anyway). Main session only — EXCEPT a
    # StopFailure carrying an agent_id: a subagent turn that DIED on an API error
    # (e.g. 529 Overloaded) fires this and NO SubagentStop (nor a stoppedByUser
    # stamp), so it is the agent's only stop signal. Ignoring it leaves the agent's
    # streamer holding its sub.pid slot row — the tab's liveness signal — forever,
    # wedging the tab blue (audit-debug stuck-blue shape). Hand it to the same
    # finaliser SubagentStop uses. A plain Stop with an agent_id stays ignored: it is
    # just an inner turn boundary and SubagentStop still owns finalisation.
    if d.get("agent_id"):
        if d.get("hook_event_name") == "StopFailure":
            SUB.finalize(LOG, d, d.get("agent_id"),
                         d.get("agent_type") or "agent",
                         d.get("transcript_path") or "", tag="stopfail")
            return
        return H.ignore(d, "agent_id (substream owns agent accounting)")
    st = ACC.bump_transcript(LOG, d.get("transcript_path"))
    tok = int((st or {}).get("tokens") or 0)
    cost = float((st or {}).get("cost") or 0.0)
    A.hook_event(d, decision=f"folded transcript tail; tokens={tok} cost={cost:.4f}")


def entry():
    H.run(main)
