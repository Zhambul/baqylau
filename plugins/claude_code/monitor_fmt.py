# plugins/claude_code/monitor_fmt.py — PostToolUse(Monitor) launcher
# Entry point: claude-monitor-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-monitor-fmt.py MIRROR_LOG WIDTH
#
# Reads a Claude Code PostToolUse(Monitor) payload on stdin, writes a monitor
# header + the watched COMMAND (a highlighted `code` op, like a bg/fg block — a
# WebSocket monitor shows its `ws.url` instead) to the mirror log, and spawns
# claude-stream.py (detached) to tail the monitor's event stream into the same
# log. Header, command, streamed events, and finish chip all share the taskId
# copy-group (⧉cmd/⧉out links). Monitor's PostToolUse fires at start (the tool
# returns immediately while streaming continues), with the stream's taskId in
# tool_response — same shape as a background Bash launch.
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


def _mode(ti):
    """The monitor's lifetime, as a header suffix: "persistent" for a
    session-length watch, else "≤<dur>" for its timeout, else "". Both come
    straight off the Monitor tool_input (docs/streaming.md — the Monitor payload
    fields this repo reads)."""
    if ti.get("persistent"):
        return "persistent"
    tms = ti.get("timeout_ms")
    return "≤" + O.fmt_dur(tms / 1000) if isinstance(tms, (int, float)) and tms > 0 else ""


def _cmd_op(ti, rgb, grp):
    """The monitor's SUBJECT op, painted right under the header so the mirror
    shows WHAT is being watched — not just the description. A command monitor
    gets a highlighted `code` op (same as a bg/fg command block); a WebSocket
    monitor (`ws.url`, no command) gets a labelled `⇄ ws · <url>` line. None
    when neither is present (nothing to show)."""
    cmd = ti.get("command") or ""
    if cmd.strip():
        return O.code(cmd, g=grp)
    ws = ti.get("ws")
    if isinstance(ws, dict) and ws.get("url"):
        return O.label("⇄ ws · " + str(ws["url"]), rgb, g=grp)
    return None


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
    mode = _mode(ti)
    if mode:
        text += " · " + mode
    # Copy-group: header + command + streamed events + finish chip all share the
    # taskId so the block carries ⧉cmd/⧉out links like a bg command (the tailer
    # joins the same group via CLAUDE_STREAM_GROUP below). None on a failed call
    # (no taskId — nothing streams, but the header/command still group together).
    grp = taskid or None

    if H.is_failure(d) or not taskid:
        # A failed Monitor call (PostToolUseFailure carries no taskId — nothing
        # will ever stream) used to paint just the open header: a dangling,
        # footer-less block indistinguishable from a running monitor. Close the
        # block inline instead, showing the command + the error on the chip.
        err = " ".join((d.get("error") or "").split())
        chip = "■ monitor failed" + ((" · " + err[:80]) if err else "")
        cmd_op = _cmd_op(ti, CYAN_FAIL_HDR, grp)
        ops = [O.blank(), O.rule(), O.label(text, CYAN_FAIL_HDR, g=grp)]
        if cmd_op is not None:
            ops.append(cmd_op)
        ops += [O.label(chip, O.RED, g=grp), O.rule()]
        O.emit(LOG, *ops, src=opsrc)
        A.hook_event(d, decision="monitor failed / no taskId: block closed inline")
        return

    # Claim a monitor palette slot and colour the header with it, so this monitor's
    # header, gutter, and finish chip all share one colour (and parallel monitors
    # differ). The streamer (passed the slot) does the gutter + finish.
    slot, marker = claude_slots.claim("monitor", LOG)
    head_rgb = claude_slots.color("monitor", slot)

    cmd_op = _cmd_op(ti, head_rgb, grp)
    ops = [O.blank(), O.rule(), O.label(text, head_rgb, g=grp)]
    if cmd_op is not None:
        ops.append(cmd_op)
    ops.append(O.rule())
    O.emit(LOG, *ops, src=opsrc)

    # Base env via the ONE CLAUDE_STREAM_* builder (hookkit.stream_env) so the
    # tailer's event/finish ops join the header's copy-group. The FULL command
    # ALSO rides along as CLAUDE_MONITOR_CMD (monitor-specific, not a STREAM_* key):
    # find_proc prefers a whole-command argv match over the single longest-token
    # `sig`, which alone can also match an unrelated long-lived process (see
    # claude-stream.py find_proc).
    env = H.stream_env(group=grp)
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
    A.hook_event(d, decision=f"monitor header+command: task={taskid} slot={slot} sig={sig!r}")


def entry():
    H.run(main)
