# plugins/claude_code/stop_fmt.py — Stop/StopFailure/SessionEnd accounting
# Entry point: claude-stop-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
#
# Cost/token accounting is now OTEL-authoritative: the per-machine OTLP receiver
# (plugins/otel/) folds Claude Code's claude_code.token.usage / cost.usage exports
# into the SAME per-session counters (tk_*/cost/tokens) the transcript fold used
# to, and it captures what folding never could — the hidden "auxiliary" agents.
# So this handler NO LONGER folds the transcript on every Stop. It keeps two jobs:
#
#   1. StopFailure carrying an agent_id — a subagent turn that DIED on an API error
#      (e.g. 529) fires this and NO SubagentStop (nor a stoppedByUser stamp), so it
#      is the agent's only stop signal. Ignoring it leaves the agent's streamer
#      holding its sub.pid slot row — the tab's liveness signal — forever, wedging
#      the tab blue (audit-debug stuck-blue shape). Hand it to the same finaliser
#      SubagentStop uses. (This has nothing to do with cost — it's tab recovery.)
#
#   2. SessionEnd FALLBACK — the transcript fold survives ONLY here, and ONLY when
#      the receiver wrote nothing for this session (otel_seen == 0: telemetry off,
#      receiver down, or a machine without the env). Then we fold the whole
#      transcript once (idempotent via the txpos cursor) so cost isn't $0. When OTEL
#      DID flow (the normal path) the fold is skipped — no double-count. Runs as an
#      ORDERED dispatcher step BEFORE claude-split.py parks the state DB.
#
# It paints nothing and touches no tab colour (that stays claude-tab-status.py,
# read-only on the state DB); this handler is the one sanctioned Stop-time WRITER.
from core import ops as O
from core import state as St
from plugins.claude_code import accounting as ACC
from plugins.claude_code import hookkit as H
from plugins.claude_code import subagent_fmt as SUB

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    # Agent-inner stop: the substream owns that agent's rendering. A plain Stop is
    # just an inner turn boundary (ignore); a StopFailure is the API-error stuck-blue
    # recovery (job #1 above).
    if d.get("agent_id"):
        if d.get("hook_event_name") == "StopFailure":
            SUB.finalize(LOG, d, d.get("agent_id"),
                         d.get("agent_type") or "agent",
                         d.get("transcript_path") or "", tag="stopfail")
            return
        return H.ignore(d, "agent_id (substream owns agent accounting)")
    # Main session. A plain Stop/StopFailure no longer folds — OTEL is authoritative
    # and updates the scoreboard live. Only SessionEnd runs the fold, as a fallback.
    if d.get("hook_event_name") != "SessionEnd":
        return A.hook_event(d, decision="otel authoritative — no transcript fold on Stop")
    seen = int((St.stats(LOG) or {}).get("otel_seen") or 0)
    if seen:
        return A.hook_event(d, decision=f"otel authoritative (otel_seen={seen}) — fold skipped")
    st = ACC.bump_transcript(LOG, d.get("transcript_path"))
    tok = int((st or {}).get("tokens") or 0)
    cost = float((st or {}).get("cost") or 0.0)
    A.hook_event(d, decision=f"otel absent — folded transcript fallback; tokens={tok} cost={cost:.4f}")


def entry():
    H.run(main)
