#!/usr/bin/env python3
# claude-monitor-fmt.py MIRROR_LOG WIDTH
#
# Reads a Claude Code PostToolUse(Monitor) payload on stdin, writes a monitor
# header to the mirror log, and spawns claude-stream.py (detached) to tail the
# monitor's event stream into the same log. Monitor's PostToolUse fires at start
# (the tool returns immediately while streaming continues), with the stream's
# taskId in tool_response — same shape as a background Bash launch.
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_hook as H
import claude_slots
import claude_ops as O

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    if (d.get("tool_name") or "") != "Monitor":
        return H.ignore(d, "not the Monitor tool")
    # NO agent_id guard here, deliberately: a subagent's monitors ARE rendered by
    # this hook (noted in the header below) — the substream doesn't own them.
    ti = d.get("tool_input") or {}
    tr = d.get("tool_response") or {}
    taskid = tr.get("taskId") if isinstance(tr, dict) else None
    # A distinctive token from the monitor command, used by claude-stream.py to
    # find the monitor's (persistent) command process and watch it for completion.
    toks = re.findall(r"[\w./:@=+-]{5,}", ti.get("command") or "")
    sig = max(toks, key=len) if toks else ""
    desc = " ".join((ti.get("description") or "").split())
    # If a subagent launched this monitor, note which one in the header (the stream
    # still uses the monitor palette + tailer — monitors-within-subagents are rare).
    if d.get("agent_id"):
        atype = d.get("agent_type") or "agent"
        desc = (atype + " · " + desc) if desc else atype
    text = "◉ monitor · " + desc if desc else "◉ monitor"

    if H.is_failure(d) or not taskid:
        # A failed Monitor call (PostToolUseFailure carries no taskId — nothing
        # will ever stream) used to paint just the open header: a dangling,
        # footer-less block indistinguishable from a running monitor. Close the
        # block inline instead, with the error on the chip.
        err = " ".join((d.get("error") or "").split())
        chip = "■ monitor failed" + ((" · " + err[:80]) if err else "")
        O.emit(LOG, O.blank(), O.rule(), O.label(text, (86, 182, 194)),
               O.label(chip, O.RED), O.rule())
        A.hook_event(d, decision="monitor failed / no taskId: block closed inline")
        return

    # Claim a monitor palette slot and colour the header with it, so this monitor's
    # header, gutter, and finish chip all share one colour (and parallel monitors
    # differ). The streamer (passed the slot) does the gutter + finish.
    slot, marker = claude_slots.claim("monitor", LOG)
    head_rgb = claude_slots.color("monitor", slot)

    O.emit(LOG, O.blank(), O.rule(), O.label(text, head_rgb), O.rule())

    # The FULL command rides along in env: find_proc prefers a whole-command argv
    # match over the single longest-token `sig`, which alone can also match an
    # unrelated long-lived process (see claude-stream.py find_proc).
    env = dict(os.environ)
    env["CLAUDE_MONITOR_CMD"] = ti.get("command") or ""
    proc = H.spawn_streamer("claude-stream.py",
                            ["monitor", taskid, LOG, slot, sig], LOG, env=env,
                            purpose=f"stream:monitor task={taskid}",
                            audit_argv=["monitor", taskid, str(slot)])
    if proc is not None:
        claude_slots.set_owner(marker, proc.pid)
    else:
        claude_slots.release("monitor", LOG, slot, os.getpid())
    A.hook_event(d, decision=f"monitor header: task={taskid} slot={slot} sig={sig!r}")


if __name__ == "__main__":
    H.run(main)
