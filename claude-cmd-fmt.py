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
import json, os, shlex, subprocess, sys, re

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


def _spawn_stream(kind, taskid, slot, src=None, skip_existing=False):
    # Launch claude-stream.py detached (own session) so it keeps tailing the job's
    # output file after this hook exits. Passes the claimed slot so its gutter +
    # finish chip match the header colour. If the command redirected stdout to a
    # file (`src`), hand it to the streamer via env so it tails that instead of the
    # empty task output file. `skip_existing` is for a Ctrl+B conversion handoff: the
    # departing fg tailer already showed whatever came through its own tee copy, so
    # the replacement bg tailer should skip whatever's already in the task's output
    # file rather than re-showing it from the start. Returns the Popen (or None).
    here = os.path.dirname(os.path.abspath(__file__))
    streamer = os.path.join(here, "claude-stream.py")
    if not (taskid and os.path.exists(streamer)):
        return None
    env = dict(os.environ)
    if src:
        env["CLAUDE_STREAM_SRC"] = src
    if skip_existing:
        env["CLAUDE_STREAM_SKIP_EXISTING"] = "1"
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
    # Ctrl+B mid-command: the model asked for a plain foreground run, but the USER
    # backgrounded it before it finished. Claude Code reports this the same way as a
    # real completion — this Bash call's own PostToolUse fires right away, with a
    # `duration_ms` covering only the time UP TO the keypress — but tool_response
    # carries backgroundTaskId (+ backgroundedByUser) so it can be told apart from an
    # actually-finished command. Confirmed empirically: this is undocumented.
    taskid = tr.get("backgroundTaskId") if isinstance(tr, dict) else None
    converted = bool(taskid) and not bg

    # A foreground command's own live-stream marker (claude-cmd-pre.py), if any — read
    # it up front since the genuine/converted-background path below and the ordinary
    # finish path further down both need to know about it.
    marker = LOG + ".fg-live"
    live = None
    if os.path.exists(marker):
        try:
            with open(marker) as f:
                live = json.load(f)
        except Exception:
            live = None
        try:
            os.remove(marker)
        except Exception:
            pass

    if bg or converted:
        # Claim a palette slot now and colour the "▷ background" header with it, so
        # this job's header, gutter, and finish chip all share one colour and the
        # parallel jobs differ. The streamer (passed the slot) does gutter + finish.
        if taskid:
            slot, slot_marker = claude_slots.claim("bg", LOG)
            head_rgb = claude_slots.color("bg", slot)
        else:
            slot, slot_marker, head_rgb = None, None, LBL_BG

        if converted and live and live.get("src"):
            # Our own fg tailer was tee-ing this command's own side file — but once
            # Ctrl+B hands it off, Claude Code captures further output into its OWN
            # backgroundTaskId file instead (empirically: our tee file gets nothing
            # more from this point on), so tell that tailer to bow out quietly (no
            # finish chip, no fallback body) instead of racing the bg tailer below,
            # which is about to own the rest of this block.
            try:
                with open(live["src"] + ".done", "w") as f:
                    json.dump({"converted": True}, f)
            except Exception:
                pass
            O.emit(LOG, O.label("▷ backgrounded (ctrl+b) — continuing below", LBL_BG))
        else:
            O.emit(LOG, O.blank(), O.rule(), O.label("▷ background", head_rgb), O.code(cmd), O.rule())

        O.bump(LOG, tool="Bash", commands=1)     # count it; the streamer owns its finish
        O.bump_transcript(LOG, d.get("transcript_path"))
        if taskid:
            # Converted: find_file() locates tasks/<taskid>.output itself, same as any
            # genuine background command — this cmd string's own redirect (if any) is
            # irrelevant to where Claude Code is now writing the real output.
            src = None if converted else parse_redirect(cmd, d.get("cwd"))
            proc = _spawn_stream("bg", taskid, slot, src, skip_existing=converted)
            if proc is not None:
                claude_slots.set_owner(slot_marker, proc.pid)
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

    # claude-cmd-pre.py (PreToolUse) may already have rendered the header and be
    # tailing this command's output live (see its module docstring; `live` was read
    # further up, before the bg/converted branch above). If so, this is the only
    # place the REAL outcome (duration/exit code/interrupted) is known, so hand it to
    # that tailer via a sentinel instead of re-rendering the header + body ourselves —
    # it also carries gut_body as a fallback in case the rewrite never took effect and
    # nothing was ever streamed.
    if live and live.get("src"):
        try:
            with open(live["src"] + ".done", "w") as f:
                json.dump({"chip": chip_txt, "color": list(col), "fallback_body": gut_body}, f)
        except Exception:
            live = None    # couldn't hand off -> fall through to the normal render below

    if not live:
        O.emit(LOG, O.blank(), O.rule(), O.label("▶ foreground", col), O.code(cmd), O.rule(),
               O.gut(gut_body, col), O.rule(), O.label(chip_txt, col), O.rule())

    # Update the session scoreboard. claude-scorebar.py (its own small window under
    # the mirror) refreshes off this sidecar bump — nothing is emitted into the log.
    # bump_transcript folds in the main session's own token spend since the last hook
    # (agents bump theirs at stream end). Best-effort — a failed bump must never
    # break the command block above.
    O.bump(LOG, tool="Bash", commands=1, **({"failed": 1} if failed else {}))
    O.bump_transcript(LOG, d.get("transcript_path"))


if __name__ == "__main__":
    main()
