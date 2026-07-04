#!/usr/bin/env python3
# claude-stop-fmt.py — main-session Stop / StopFailure accounting flush.
#
# The scoreboard's token/cost totals are folded out of the session transcript by
# claude_ops.bump_transcript, but that only ran from the Bash/file PostToolUse
# hooks. So the LAST assistant turn of a session — a reply with no trailing tool
# call — was never folded: its tokens and (cache-read-dominated) cost silently
# dropped, leaving the scoreboard a few % under `claude --resume`'s real total.
# Wired to Stop (fires at the end of EVERY turn, tool-terminated or not) this
# closes that tail; every turn is folded before the next begins, and the final
# turn is folded before SessionEnd parks the state DB — so no SessionEnd trigger is
# needed (and none is wanted: it would race claude-split.py's park/rename).
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_hook as H
import claude_ops as O

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    # A subagent/teammate Stop is inner — its own streamer (claude-substream.py)
    # bumps that agent's spend; folding the main transcript here would be wrong
    # (and it isn't the agent's transcript anyway). Main session only.
    if d.get("agent_id"):
        return H.ignore(d, "agent_id (substream owns agent accounting)")
    st = O.bump_transcript(LOG, d.get("transcript_path"))
    tok = int((st or {}).get("tokens") or 0)
    cost = float((st or {}).get("cost") or 0.0)
    A.hook_event(d, decision=f"folded transcript tail; tokens={tok} cost={cost:.4f}")


if __name__ == "__main__":
    H.run(main)
