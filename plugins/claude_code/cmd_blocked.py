# plugins/claude_code/cmd_blocked.py — the Bash call that was RESOLVED WITHOUT
# EVER RUNNING.
# Entry point: claude-cmd-blocked.py (a thin shim — the entry FILENAME is the audit
# vocabulary).
#
# WHY THIS EXISTS. `claude-cmd-pre.py` commits to a foreground command at
# PreToolUse: it paints the `▶ foreground` header, tees the command into a side
# file, claims an fg slot, spawns a tailer, and the tab dispatch turns the tab BLUE
# (executing). All of that happens BEFORE anyone knows whether the call is
# permitted — and two outcomes resolve a Bash call with no PostToolUse at all:
#
#   * another PreToolUse hook DENIES it   -> tool_response "PreToolUse:Bash hook
#                                            error: [...]: Blocked: …"
#   * the permission prompt is REJECTED   -> "The user doesn't want to proceed …"
#
# Neither fires PostToolUse, so `claude-cmd-fmt.py` never hands the tailer an
# outcome. The orphaned tailer then sits on a file nobody will ever write, times
# out on writer-liveness (`writer-gone`, CLAUDE_STREAM_GRACE_S = 2s), paints a
# fake `■ foreground finished · 0.0s`, releases its slot and calls `bg-recheck` —
# whose fg branch exists SOLELY for a manually CANCELLED command and therefore
# reads a vanished writer as "the turn is over" and paints the tab GREEN (your
# turn) in the middle of a turn that is still running. Measured 2026-07-31 on
# session 674d78d1: deny at 13:59:10, tailer gave up 13:59:12, tab green 13:59:16,
# the model's next command 13:59:27 — eleven seconds of "your turn" on a busy
# session (docs/tab-colors.md *A Bash call that never ran*).
#
# The fix is an EVENT, per the standing rule that every cancellation path needs a
# real signal and never an idle timeout: `PostToolBatch` fires once the batch has
# resolved and carries EVERY call of it with its `tool_response` — including the
# blocked one. So this handler runs there.
#
# THE TEST FOR "never ran" IS NOT A STRING. Matching the denial wording would be
# version-fragile (and would have to enumerate the permission-rejection text too).
# The exact, local fact is the fg-live hand-off: `claude-cmd-pre.py` writes one per
# foreground command and `claude-cmd-fmt.py` CONSUMES it at PostToolUse. By
# PostToolBatch every call in the batch has resolved — so a call whose fg-live
# record is STILL THERE is a call whose PostToolUse never fired, i.e. one that
# never ran. That covers every present and future reason Claude Code resolves a
# call without executing it.
#
# A cancelled command cannot be confused with this: an interrupt kills the turn, so
# no PostToolBatch fires for its batch at all — and even if one did, painting
# WORKING here is the recoverable direction (interrupt-watch flips green off the
# transcript's own interrupt record), whereas painting green over a live turn is not.
from core import ops as O
from core import state as ST
from core import streamfmt as SF
from plugins.claude_code import hookkit as H
from plugins.claude_code import tabstatus

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def _response_text(tc):
    """The blocked call's `tool_response` as display text. Deliberately a plain
    stringify and NOT cmd_fmt._combined_output: that shape (stdout/stderr keys)
    is what a command that RAN produces, and a call resolved without running
    always carries a bare message string. Nothing is being re-encoded here."""
    tr = tc.get("tool_response")
    return "" if tr is None else str(tr).rstrip("\n")


def _reclaim(log, tc):
    """Consume the fg-live record of `tc` if it is still there — i.e. this call
    resolved without its PostToolUse ever running. Returns the record, or None
    for the ordinary case (cmd_fmt already took it).

    match=tid is what keys the take to THIS call: the batch also lists the calls
    that ran fine (already consumed — nothing to match), and a command started
    AFTER this batch owns the key by then, so its record must be left alone (the
    same cross-wiring guard cmd_fmt documents)."""
    tid = tc.get("tool_use_id") or ""
    if not tid or (tc.get("tool_name") or "") != "Bash":
        return None
    live = ST.hand_take(log, ST.FG_LIVE, match={"tid": tid})
    if live:
        A.state_file(log, "state:fg-live", "remove", live)
    return live


def _hand_off(log, live, body):
    """Give the orphaned tailer the outcome it will otherwise never get, on the
    SAME take-once key cmd_fmt uses (the tailer polls it as CLAUDE_STREAM_DONE).
    It then closes the block at once with an honest chip and the denial text as
    its body, instead of waiting out writer-liveness and painting a fake finish.

    `blocked` rides the record so the tailer also knows not to call bg-recheck —
    the belt to this handler's braces: the tab paint below already moved the tab
    off blue, but if this handler ever loses the race to the 2s writer-gone the
    flag is what stops the green (plugins/claude_code/stream.py cleanup())."""
    src = live.get("src")
    done = live.get("done") or ((src + ".done") if src else None)
    if not done:
        return False
    chip, col = SF.finish_chip("", blocked=True)
    rec = {"chip": chip, "color": list(col), "fallback_body": body, "blocked": True}
    if not ST.hand_put(log, "done:" + done, rec):
        A.error(log, "write blocked handoff", {"done": done})
        return False
    A.state_file(log, "state:done:" + done, "write", {"chip": chip, "blocked": True})
    return True


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    if d.get("agent_id"):
        # a subagent's own batch — the substream owns that stream's rendering and
        # its own subfg records, and the main tab tracks the lead only
        return H.ignore(d, "subagent event (agent_id present)")
    if ST.parked(LOG):
        # no live state DB = unhosted (headless/daemon) or already parked — a take
        # would CREATE the DB whose file-existence is the session-alive signal
        return H.ignore(d, "no state DB (unhosted session)")
    calls = d.get("tool_calls") or []
    blocked = []
    for tc in calls:
        live = _reclaim(LOG, tc)
        if live:
            body = _response_text(tc)
            blocked.append((tc.get("tool_use_id") or "", _hand_off(LOG, live, body), body))
    if not blocked:
        return H.ignore(d, "no foreground command left unresolved (%d call(s))"
                        % len(calls))
    # The turn is NOT over: the model is about to read the refusal and act on it.
    # Paint exactly what this call's PostToolUse would have — "main agent between
    # tools" — which is also what makes the tailer's later bg-recheck a no-op
    # ("tab not on a bg-running colour") instead of a green flip. Driven through
    # the same IN-PROCESS entrypoint dispatch.py uses (not hookkit.notify_tab,
    # which respawns the shim with DEVNULL stdin — the transition row would then
    # carry no session_id), under the tab dispatcher's own audit identity so a
    # failed paint still lands in errors.script as claude-tab-status.py.
    A.set_handler("claude-tab-status.py")
    try:
        tabstatus.dispatch("posttool", d)
    finally:
        A.set_handler("claude-cmd-blocked.py")
    A.hook_event(d, decision="blocked before it ran: "
                 + ", ".join("%s (%s, %d chars)"
                             % (tid[:12] or "?", "handed off" if ok else "no tailer handoff",
                                len(body))
                             for tid, ok, body in blocked))


def entry():
    H.run(main)
