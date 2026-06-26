#!/usr/bin/env python3
# claude-cmd-fmt.py — formatter for the kitty command-mirror pane.
#
# Reads a Claude Code PostToolUse(Bash) hook payload (JSON) on stdin and appends
# a formatted block (command | output | elapsed) to the mirror log given as
# argv[1], wrapped to the pane width given as argv[2]. Invoked by
# claude-cmd-log.sh. The bash/python highlighting, gutter-wrapping, and escape
# handling live in claude_render (shared with claude-substream.py).
#
# Subagent (Task/Agent) tool calls fire this same hook (with an agent_id), but the
# subagent's whole transcript is streamed in order by claude-substream.py instead,
# so we IGNORE agent_id events here to avoid double-rendering / mis-ordering.
import json, os, subprocess, sys, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_render as R

WIDTH = max(16, int(sys.argv[2]))
RULE  = R.rule(WIDTH)

LBL_FG   = (170, 185, 210)  # slate   — foreground OK (neutral, distinct from the vivid palettes)
LBL_BG   = (209, 154, 102)  # orange  — background header chip / foreground "interrupted"
LBL_FAIL = (224, 108, 117)  # red     — a failed tool (PostToolUseFailure)


def _spawn_stream(kind, taskid, slot):
    # Launch claude-stream.py detached (own session) so it keeps tailing the job's
    # output file after this hook exits. Passes the claimed slot so its gutter +
    # finish chip match the header colour. Returns the Popen (or None).
    here = os.path.dirname(os.path.abspath(__file__))
    streamer = os.path.join(here, "claude-stream.py")
    if not (taskid and os.path.exists(streamer)):
        return None
    try:
        return subprocess.Popen(
            [sys.executable, streamer, kind, taskid, sys.argv[1], str(WIDTH), str(slot)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        return None


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    # A subagent's tool calls are rendered (in transcript order, with messages) by
    # claude-substream.py — skip them here so they aren't rendered twice.
    if d.get("agent_id"):
        return
    ti  = d.get("tool_input") or {}
    tr  = d.get("tool_response") or {}
    cmd = (ti.get("command") or "")
    if not cmd.strip():
        return
    bg = bool(ti.get("run_in_background"))

    if bg:
        # Claim a palette slot now and colour the "▷ background" header with it, so
        # this job's header, gutter, and finish chip all share one colour and the
        # parallel jobs differ. The streamer (passed the slot) does gutter + finish.
        taskid = tr.get("backgroundTaskId") if isinstance(tr, dict) else None
        if taskid:
            slot, marker = claude_slots.claim("bg", sys.argv[1])
            head_rgb = claude_slots.color("bg", slot)
        else:
            slot, marker, head_rgb = None, None, LBL_BG
        block = ["", RULE, R.label("▷ background", head_rgb), R.render(cmd, WIDTH), RULE]
        with open(sys.argv[1], "a", encoding="utf-8") as f:
            f.write("\n".join(block) + "\n")
        if taskid:
            proc = _spawn_stream("bg", taskid, slot)
            if proc is not None:
                claude_slots.set_owner(marker, proc.pid)
            else:
                claude_slots.release("bg", sys.argv[1], slot, os.getpid())
        return

    ms  = d.get("duration_ms")
    dur = "?" if ms is None else (f"{ms/1000:.1f}s" if ms < 60000 else f"{int(ms//60000)}m{int(ms//1000)%60:02d}s")
    failed = "Failure" in (d.get("hook_event_name") or "")
    interrupted = bool(d.get("is_interrupt"))

    if failed:
        # A failed tool has no tool_response; its combined output (often prefixed
        # "Exit code N") is in the top-level `error` field. Pull the exit code
        # into the chip so it isn't duplicated in the body.
        body = (d.get("error") or "").rstrip("\n")
        m = re.match(r"Exit code (\d+)\n?", body)
        code = m.group(1) if m else None
        if m:
            body = body[m.end():]
        if interrupted:
            chip_txt, col = "■ interrupted · " + dur, LBL_BG
        elif code is not None:
            chip_txt, col = f"■ failed (exit {code}) · {dur}", LBL_FAIL
        else:
            chip_txt, col = "■ failed · " + dur, LBL_FAIL
    else:
        out = tr.get("stdout", "") if isinstance(tr, dict) else str(tr)
        err = tr.get("stderr", "") if isinstance(tr, dict) else ""
        body = (out + (("\n" + err) if err else "")).rstrip("\n")
        chip_txt, col = "■ finished · " + dur, LBL_FG

    # One colour for the whole block — header, gutter, and finish chip all use it
    # (slate ok / red failed / orange interrupted), so the finish line matches the
    # gutter and you can tell which stream finished.
    body = R.unescape(body)
    GUT_FG = R.fg(*col) + "│ " + R.RST
    gbody = R.wrap_gutter(body, WIDTH, GUT_FG, 2) if body else GUT_FG + R.DIM + "(no output)" + R.RST
    block = ["", RULE, R.label("▶ foreground", col), R.render(cmd, WIDTH), RULE,
             gbody, RULE, R.label(chip_txt, col), RULE]
    with open(sys.argv[1], "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")


if __name__ == "__main__":
    main()
