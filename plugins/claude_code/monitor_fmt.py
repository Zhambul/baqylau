# plugins/claude_code/monitor_fmt.py — PostToolUse(Monitor) launcher
# Entry point: claude-monitor-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-monitor-fmt.py MIRROR_LOG WIDTH
#
# Reads a Claude Code PostToolUse(Monitor) payload on stdin, writes a monitor
# header to the mirror log, and spawns claude-stream.py (detached) to tail the
# monitor's event stream into the same log. Monitor's PostToolUse fires at start
# (the tool returns immediately while streaming continues), with the stream's
# taskId in tool_response — same shape as a background Bash launch.
import os

from core import ops as O
from core import slots as claude_slots
from plugins.claude_code import hookkit as H
from plugins.claude_code import stream as ST

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

# Failed-monitor header colour: the One Dark cyan (render.COL's builtin/op hue),
# NOT a semantic ops.py constant or a monitor-palette slot — a failed call never
# claims a slot (nothing will stream), so it gets this fixed, palette-free tint.
CYAN_FAIL_HDR = (86, 182, 194)


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
    # monitor_sig is the ONE owner of this extraction (wire contract w/ find_proc).
    sig = ST.monitor_sig(ti.get("command"))
    desc = " ".join((ti.get("description") or "").split())
    # If a subagent launched this monitor, note which one in the header (the stream
    # still uses the monitor palette + tailer — monitors-within-subagents are rare).
    # Its ops also get the agent's producer-source stamp: this hook runs in the
    # dispatcher process (no ambient set_src), so the header takes the explicit
    # emit(src=) and the tailer inherits via $CLAUDE_OPS_SRC in its spawn env —
    # the web mirror is main-agent-only and a subagent's monitor is its activity.
    opsrc = None
    if d.get("agent_id"):
        atype = d.get("agent_type") or "agent"
        desc = (atype + " · " + desc) if desc else atype
        opsrc = "sub:" + d["agent_id"]
    text = "◉ monitor · " + desc if desc else "◉ monitor"

    if H.is_failure(d) or not taskid:
        # A failed Monitor call (PostToolUseFailure carries no taskId — nothing
        # will ever stream) used to paint just the open header: a dangling,
        # footer-less block indistinguishable from a running monitor. Close the
        # block inline instead, with the error on the chip.
        err = " ".join((d.get("error") or "").split())
        chip = "■ monitor failed" + ((" · " + err[:80]) if err else "")
        O.emit(LOG, O.blank(), O.rule(), O.label(text, CYAN_FAIL_HDR),
               O.label(chip, O.RED), O.rule(), src=opsrc)
        A.hook_event(d, decision="monitor failed / no taskId: block closed inline")
        return

    # Claim a monitor palette slot and colour the header with it, so this monitor's
    # header, gutter, and finish chip all share one colour (and parallel monitors
    # differ). The streamer (passed the slot) does the gutter + finish.
    slot, marker = claude_slots.claim("monitor", LOG)
    head_rgb = claude_slots.color("monitor", slot)

    O.emit(LOG, O.blank(), O.rule(), O.label(text, head_rgb), O.rule(), src=opsrc)

    # The FULL command rides along in env: find_proc prefers a whole-command argv
    # match over the single longest-token `sig`, which alone can also match an
    # unrelated long-lived process (see claude-stream.py find_proc).
    env = dict(os.environ)
    env["CLAUDE_MONITOR_CMD"] = ti.get("command") or ""
    if opsrc:
        env["CLAUDE_OPS_SRC"] = opsrc
    proc = H.spawn_streamer("claude-stream.py",
                            ["monitor", taskid, LOG, slot, sig], LOG, env=env,
                            purpose=f"stream:monitor task={taskid}",
                            audit_argv=["monitor", taskid, str(slot)])
    if proc is not None:
        claude_slots.set_owner(marker, proc.pid)
    else:
        claude_slots.release("monitor", LOG, slot, os.getpid())
    A.hook_event(d, decision=f"monitor header: task={taskid} slot={slot} sig={sig!r}")


def entry():
    H.run(main)
