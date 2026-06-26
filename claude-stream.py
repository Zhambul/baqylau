#!/usr/bin/env python3
# claude-stream.py KIND TASKID MIRROR_LOG WIDTH
#
# Detached tailer for the kitty command-mirror pane. Background Bash jobs and
# Monitor streams both write their output to a …/tasks/<id>.output file, but no
# hook fires while they run — so this process (spawned detached by the launch
# hook) tails that file and appends each new line to the mirror log, then a
# closing rule + finish line when the job ends.
#
#   KIND  "bg" | "monitor"  — only changes the gutter colour + finish label
#   TASKID                  — backgroundTaskId / Monitor taskId (globally unique)
#   MIRROR_LOG              — /tmp/claude-mirror-<slug>.log
#   WIDTH                   — pane columns (for the closing rule)
#
# Completion is detected the same way claude-tab-status.sh detects a running
# background job: the writing process holds the output file open the whole time,
# so when no write-holder remains (lsof) and the size has stopped growing, the
# job is done. The tailer only reads the file, so it never counts itself.
import errno, glob, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots

KIND   = sys.argv[1] if len(sys.argv) > 1 else "bg"
TASKID = sys.argv[2] if len(sys.argv) > 2 else ""
LOG    = sys.argv[3] if len(sys.argv) > 3 else ""
WIDTH  = max(16, int(sys.argv[4])) if len(sys.argv) > 4 else 53
SIG    = sys.argv[6] if len(sys.argv) > 6 else ""   # monitor: signature to find its process
OUTER  = sys.argv[7] if len(sys.argv) > 7 else ""   # "r,g,b" subagent colour -> double gutter


def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


DIM  = fg(92, 99, 112)
RST  = "\033[0m"
RULE = DIM + ("─" * WIDTH) + RST

# The launcher (claude-cmd-fmt / claude-monitor-fmt) claims a palette slot, colours
# the header chip with it, and passes the index here so the gutter + finish chip
# match — header, gutter, and finish all share one colour, and parallel jobs differ.
# (If no slot is passed we claim our own, as a fallback.)
if len(sys.argv) > 5 and sys.argv[5].lstrip("-").isdigit():
    SLOT, _MARKER = int(sys.argv[5]), None
else:
    SLOT, _MARKER = claude_slots.claim(KIND, LOG)
SLOT_RGB = claude_slots.color(KIND, SLOT)
GUT  = fg(*SLOT_RGB) + "│ " + RST
GW   = 2
# When this background/monitor job was launched *by a subagent*, prefix a second
# gutter bar in the subagent's colour (outer = which subagent, inner = which bg/
# monitor job) so nested parallel jobs stay distinguishable. claude-substream.py
# passes the subagent colour as "r,g,b".
OUTER_BAR = ""
if OUTER:
    try:
        _o = tuple(int(x) for x in OUTER.split(","))
        OUTER_BAR = fg(*_o) + "│ " + RST
        GUT = OUTER_BAR + fg(*SLOT_RGB) + "│ " + RST
        GW = 4
    except Exception:
        OUTER_BAR = ""


def release_slot():
    claude_slots.release(KIND, LOG, SLOT, os.getpid())


def label(text, rgb):
    r, g, b = rgb
    return f"\033[1;38;2;24;26;30;48;2;{r};{g};{b}m {text} {RST}"


# ANSI-aware hard-wrap so the gutter repeats on every visual row of a wide line
# (a plain prefix would vanish on soft-wrapped continuations). Escapes are copied
# verbatim and the active SGR colour is re-asserted after each wrap.
_ANSI = re.compile(r"\x1b\[[0-9;:?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def wrap_gutter(text, width, gut, gw):
    cw = max(1, width - gw)
    pieces, lines = [], text.split("\n")
    for li, line in enumerate(lines):
        if li:
            pieces.append("\n")
        pieces.append(gut)
        col, active, i, n = 0, "", 0, len(line)
        while i < n:
            m = _ANSI.match(line, i)
            if m:
                seq = m.group(0)
                pieces.append(seq)
                if seq.endswith("m"):
                    active = "" if seq in ("\x1b[0m", "\x1b[m") else active + seq
                i = m.end()
                continue
            if col >= cw:
                pieces.append(RST + "\n" + gut + active); col = 0
            pieces.append(line[i]); col += 1; i += 1
        pieces.append(RST)
    return "".join(pieces)


# Render escape sequences a job printed as text ("^[[…m", "\033[…m", …) back to
# real ESC bytes so the pane interprets them. Unescapes ALL sequences, not just
# colours — cursor/clear escapes will then execute in the pane too.
_ESC_UNESC = re.compile(r"\^\[|\\0?33|\\x1[bB]|\\e|\\u001[bB]|<[Ee][Ss][Cc]>")


def unescape(s):
    return _ESC_UNESC.sub("\x1b", s)


def append(text):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def find_file(deadline):
    pats = [f"/private/tmp/claude-*/*/*/tasks/{TASKID}.output",
            f"/private/tmp/claude-*/*/tasks/{TASKID}.output",
            f"/private/tmp/claude-*/*/*/*/tasks/{TASKID}.output"]
    while time.time() < deadline:
        for p in pats:
            m = glob.glob(p)
            if m:
                return m[0]
        time.sleep(0.3)
    return None


def has_writer(path):
    # True if some process holds the file open for writing (lsof FD ends w/u/W).
    try:
        out = subprocess.run(["lsof", "--", path], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return False
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3][-1:] in "wuW":
            return True
    return False


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError as e:
        return e.errno == errno.EPERM           # exists but owned by another user


def find_proc(sig):
    # Find the command process whose args contain `sig` (the monitor's command
    # runs as `zsh -c … eval '<command>'`, so the signature is in its argv). This
    # process stays alive across event gaps and exits exactly when the monitor
    # ends — a definitive completion signal at any cadence. Excludes self/streamers.
    if not sig:
        return None
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    me, hits = os.getpid(), []
    for line in out.splitlines():
        line = line.strip()
        pid_s, _, args = line.partition(" ")
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == me or "claude-stream.py" in args:
            continue
        if sig in args:
            hits.append(pid)
    return hits[-1] if hits else None


def main():
    if not (TASKID and LOG):
        return
    start = time.time()
    path = find_file(start + 12)
    if not path:
        append(RULE + "\n" + label("■ output not found", SLOT_RGB) + "\n")
        return

    pos, pending, last_active, last_size = 0, b"", start, -1

    def pump():
        nonlocal pos, pending, last_active, last_size
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size > pos:
            try:
                with open(path, "rb") as fh:
                    fh.seek(pos); pending += fh.read(); pos = size
            except OSError:
                return size
            *lines, pending = pending.split(b"\n")
            if lines:
                append("".join(wrap_gutter(unescape(ln.decode("utf-8", "replace")), WIDTH, GUT, GW) + "\n"
                               for ln in lines))
        if size > last_size:
            last_active = time.time()
        last_size = size
        return size

    # Completion signal differs by kind:
    #   bg      — the command holds its output file open the whole time, so the
    #             write-holder vanishing is definitive (works for long silent jobs).
    #   monitor — writes in bursts (no held file), but its command PROCESS is
    #             persistent and identifiable, and exits exactly when the monitor
    #             ends, so we track that process — robust at ANY cadence, no grace.
    # Fallbacks use a short idle so the streamer always terminates.
    mon_pid, find_deadline, GRACE = None, start + 20, (2.0 if KIND == "bg" else 8.0)
    while True:
        if pump() is None:
            break
        now = time.time()
        if KIND == "monitor":
            if mon_pid is None and now < find_deadline:
                mon_pid = find_proc(SIG)
            if mon_pid is not None:
                if not alive(mon_pid):                       # process gone -> definitively done
                    break
            elif now > find_deadline and (now - last_active) >= GRACE:
                break                                        # never found process -> idle fallback
        else:  # bg
            if not has_writer(path) and (now - last_active) >= GRACE and last_size >= 0:
                break
        time.sleep(0.4)

    pump()                                                   # final catch-up read
    if pending.strip():
        append(wrap_gutter(unescape(pending.decode("utf-8", "replace")), WIDTH, GUT, GW) + "\n")
    elapsed = max(0.0, last_active - start)      # active duration, excluding any idle wait
    dur = f"{elapsed:.1f}s" if elapsed < 60 else f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"
    text = "background finished" if KIND == "bg" else "monitor ended"
    # Finish chip uses this stream's slot colour (same as its gutter) so you can
    # tell which stream finished. Top-level jobs get a RULE-bracketed finish; a
    # subagent's nested job gets just the chip behind its double gutter (the
    # subagent block already frames it), so it stays visually contained.
    chip = label("■ " + text + " · " + dur, SLOT_RGB)
    if OUTER_BAR:
        append(OUTER_BAR + chip + "\n")
    else:
        append(RULE + "\n" + chip + "\n" + RULE + "\n")

    # Release this job's slot marker BEFORE the recheck below — bg_command_running
    # now detects running jobs via live slot markers, so the recheck must not see
    # this (now-finished) tailer's own marker, or it would refuse to clear the red.
    release_slot()

    # No "background finished" hook exists, so if the tab went red while Claude
    # handed back to the user, it would stay red. Now that this job is done, ask
    # claude-tab-status.sh to flip a *stale red* back to green (it no-ops unless
    # the tab is currently red and nothing else is still running). The detached
    # process inherited KITTY_LISTEN_ON / KITTY_WINDOW_ID from the launch hook.
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.run([os.path.join(here, "claude-tab-status.sh"), "bg-recheck"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    finally:
        release_slot()
