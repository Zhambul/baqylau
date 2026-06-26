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

LOG   = sys.argv[1]
WIDTH = max(16, int(sys.argv[2]))


def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


DIM = fg(92, 99, 112)
RST = "\033[0m"
RULE = DIM + ("─" * WIDTH) + RST


def label(text, rgb):
    r, g, b = rgb
    return f"\033[1;38;2;24;26;30;48;2;{r};{g};{b}m {text} {RST}"


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    if (d.get("tool_name") or "") != "Monitor":
        return
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
    if len(text) > WIDTH - 2:
        text = text[:WIDTH - 3] + "…"

    # Claim a monitor palette slot and colour the header with it, so this monitor's
    # header, gutter, and finish chip all share one colour (and parallel monitors
    # differ). The streamer (passed the slot) does the gutter + finish.
    slot, marker = (claude_slots.claim("monitor", LOG) if taskid else (None, None))
    head_rgb = claude_slots.color("monitor", slot) if taskid else (86, 182, 194)

    block = ["", RULE, label(text, head_rgb), RULE]
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")

    here = os.path.dirname(os.path.abspath(__file__))
    streamer = os.path.join(here, "claude-stream.py")
    if taskid and os.path.exists(streamer):
        try:
            proc = subprocess.Popen(
                [sys.executable, streamer, "monitor", taskid, LOG, str(WIDTH), str(slot), sig],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
            claude_slots.set_owner(marker, proc.pid)
        except Exception:
            claude_slots.release("monitor", LOG, slot, os.getpid())


if __name__ == "__main__":
    main()
