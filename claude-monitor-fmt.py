#!/usr/bin/env python3
# claude-monitor-fmt.py MIRROR_LOG WIDTH
#
# Reads a Claude Code PostToolUse(Monitor) payload on stdin, writes a monitor
# header to the mirror log, and spawns claude-stream.py (detached) to tail the
# monitor's event stream into the same log. Monitor's PostToolUse fires at start
# (the tool returns immediately while streaming continues), with the stream's
# taskId in tool_response — same shape as a background Bash launch.
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_ops as O

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        A.error("", "payload parse (stdin not valid JSON)")
        return
    if (d.get("tool_name") or "") != "Monitor":
        A.hook_event(d, decision="ignored: not the Monitor tool")
        return
    LOG = O.log_path(d)
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

    # Claim a monitor palette slot and colour the header with it, so this monitor's
    # header, gutter, and finish chip all share one colour (and parallel monitors
    # differ). The streamer (passed the slot) does the gutter + finish.
    slot, marker = (claude_slots.claim("monitor", LOG) if taskid else (None, None))
    head_rgb = claude_slots.color("monitor", slot) if taskid else (86, 182, 194)

    O.emit(LOG, O.blank(), O.rule(), O.label(text, head_rgb), O.rule())

    here = os.path.dirname(os.path.abspath(__file__))
    streamer = os.path.join(here, "claude-stream.py")
    if taskid and os.path.exists(streamer):
        try:
            proc = subprocess.Popen(
                [sys.executable, streamer, "monitor", taskid, LOG, str(slot), sig],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            claude_slots.set_owner(marker, proc.pid)
            A.spawn(LOG, proc.pid, [streamer, "monitor", taskid, str(slot)],
                    purpose=f"stream:monitor task={taskid}")
        except Exception:
            A.error(LOG, "spawn monitor tailer", {"taskid": taskid})
            claude_slots.release("monitor", LOG, slot, os.getpid())
    A.hook_event(d, decision=f"monitor header: task={taskid or '?'} slot={slot} sig={sig!r}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        A.error("", "main")
