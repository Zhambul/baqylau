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
import json, os, shlex, subprocess, sys, re, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_render as R
import claude_ops as O

LOG = ""   # set in main() from the payload's session_id (per-session log)

LBL_FG   = (170, 185, 210)  # slate   — foreground OK (neutral, distinct from the vivid palettes)
LBL_BG   = (209, 154, 102)  # orange  — background header chip / foreground "interrupted"
LBL_FAIL = (224, 108, 117)  # red     — a failed tool (PostToolUseFailure)


def parse_redirect(cmd, cwd):
    # If the command sends stdout to a file (… > file / &> file / 1>> file), the
    # background task's own output file stays EMPTY — the bytes go to that file
    # instead. Return its absolute path so the tailer can follow it live; otherwise
    # there'd be nothing to show until the job exits. Conservative: only stdout
    # (or &>) redirects, skip /dev/* and fd-dup targets (&1), give up on anything
    # we can't tokenise. Last redirect wins (the effective stdout sink).
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    target, i = None, 0
    while i < len(toks):
        t = toks[i]
        if ">" in t and not t.startswith("2"):
            m = re.match(r"^(?:&|1)?>>?(.*)$", t)
            if m:
                rest = m.group(1)
                if rest:
                    target = rest
                elif i + 1 < len(toks):
                    target = toks[i + 1]; i += 1
        i += 1
    if not target or target.startswith("&") or target.startswith("/dev/"):
        return None
    if not os.path.isabs(target):
        target = os.path.join(cwd or os.getcwd(), target)
    return target


def _spawn_stream(kind, taskid, slot, src=None):
    # Launch claude-stream.py detached (own session) so it keeps tailing the job's
    # output file after this hook exits. Passes the claimed slot so its gutter +
    # finish chip match the header colour. If the command redirected stdout to a
    # file (`src`), hand it to the streamer via env so it tails that instead of the
    # empty task output file. Returns the Popen (or None).
    here = os.path.dirname(os.path.abspath(__file__))
    streamer = os.path.join(here, "claude-stream.py")
    if not (taskid and os.path.exists(streamer)):
        return None
    env = dict(os.environ)
    if src:
        env["CLAUDE_STREAM_SRC"] = src
    try:
        return subprocess.Popen(
            [sys.executable, streamer, kind, taskid, LOG, str(slot)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    except Exception:
        return None


def main():
    global LOG
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    LOG = O.log_path(d)
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
            slot, marker = claude_slots.claim("bg", LOG)
            head_rgb = claude_slots.color("bg", slot)
        else:
            slot, marker, head_rgb = None, None, LBL_BG
        O.emit(LOG, O.blank(), O.rule(), O.label("▷ background", head_rgb), O.code(cmd), O.rule())
        O.bump(LOG, tool="Bash", commands=1)     # count it; the streamer owns its finish
        if taskid:
            src = parse_redirect(cmd, d.get("cwd"))
            proc = _spawn_stream("bg", taskid, slot, src)
            if proc is not None:
                claude_slots.set_owner(marker, proc.pid)
            else:
                claude_slots.release("bg", LOG, slot, os.getpid())
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
    gut_body = R.emphasize(R.unescape(body)) if body else R.DIM + "(no output)" + R.RST
    O.emit(LOG, O.blank(), O.rule(), O.label("▶ foreground", col), O.code(cmd), O.rule(),
           O.gut(gut_body, col), O.rule(), O.label(chip_txt, col), O.rule())

    # Update the session scoreboard, and emit it every N commands (or right after a
    # failure, so a red result carries its running context). Best-effort — a failed
    # bump/emit must never break the command block above.
    st = O.bump(LOG, tool="Bash", commands=1, **({"failed": 1} if failed else {}))
    every = max(1, int(os.environ.get("CLAUDE_MIRROR_SCORE_EVERY", "5") or "5"))
    if st and (failed or int(st.get("commands") or 0) % every == 0):
        O.emit(LOG, *O.scoreboard_ops(st, time.time()))


if __name__ == "__main__":
    main()
