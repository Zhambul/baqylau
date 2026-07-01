#!/usr/bin/env python3
# claude-stream.py KIND TASKID MIRROR_LOG SLOT [SIG] [OUTER]
#
# Detached tailer for the kitty command-mirror pane. Background Bash jobs and
# Monitor streams both write their output to a …/tasks/<id>.output file, but no
# hook fires while they run — so this process (spawned detached by the launch
# hook) tails that file and appends each new line to the mirror log (as structured
# paint ops via claude_ops), then a closing rule + finish chip when the job ends.
#
#   KIND  "bg" | "monitor"  — only changes the gutter colour + finish label
#   TASKID                  — backgroundTaskId / Monitor taskId (globally unique)
#   MIRROR_LOG              — /tmp/claude-mirror-<slug>.log
#   SLOT                    — palette slot index claimed by the launcher
#   SIG                     — monitor: signature token to find its process
#   OUTER                   — "r,g,b" subagent colour -> double gutter (nested job)
#
# Completion is detected the same way claude-tab-status.sh detects a running
# background job: the writing process holds the output file open the whole time,
# so when no write-holder remains (lsof) and the size has stopped growing, the
# job is done. The tailer only reads the file, so it never counts itself.
import errno, glob, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_render as R
import claude_ops as O

KIND   = sys.argv[1] if len(sys.argv) > 1 else "bg"
TASKID = sys.argv[2] if len(sys.argv) > 2 else ""
LOG    = sys.argv[3] if len(sys.argv) > 3 else ""
SIG    = sys.argv[5] if len(sys.argv) > 5 else ""   # monitor: signature to find its process
OUTER  = sys.argv[6] if len(sys.argv) > 6 else ""   # "r,g,b" subagent colour -> double gutter

# The launcher (claude-cmd-fmt / claude-monitor-fmt) claims a palette slot, colours
# the header chip with it, and passes the index here so the gutter + finish chip
# match — header, gutter, and finish all share one colour, and parallel jobs differ.
# (If no slot is passed we claim our own, as a fallback.)
if len(sys.argv) > 4 and sys.argv[4].lstrip("-").isdigit():
    SLOT, _MARKER = int(sys.argv[4]), None
else:
    SLOT, _MARKER = claude_slots.claim(KIND, LOG)
SLOT_RGB = claude_slots.color(KIND, SLOT)
# When this background/monitor job was launched *by a subagent*, a second gutter bar
# in the subagent's colour (outer = which subagent, inner = which bg/monitor job)
# keeps nested parallel jobs distinguishable. claude-substream.py passes "r,g,b".
OUTER_RGB = None
if OUTER:
    try:
        OUTER_RGB = tuple(int(x) for x in OUTER.split(","))
    except Exception:
        OUTER_RGB = None


def release_slot():
    claude_slots.release(KIND, LOG, SLOT, os.getpid())


def unescape(s):
    # Restore any escape sequences the job printed as text, then highlight section
    # banners (`=== title ===` …) — this wraps ONLY tailed command output, so it's
    # the right place to emphasise them.
    return R.emphasize(R.unescape(s))


# When a background command redirects stdout to a file (… > file), the task's own
# output file stays empty, so the launch hook passes the redirect target here and we
# tail THAT instead — live data lands there. We wait for it to appear (a `>` truncate
# may create it a beat after launch) and fall back to the task output file if it never
# does. Tailing from offset 0 is right for `>` (the file is freshly truncated).
SRC = os.environ.get("CLAUDE_STREAM_SRC") or ""


def find_file(deadline):
    pats = [f"/private/tmp/claude-*/*/*/tasks/{TASKID}.output",
            f"/private/tmp/claude-*/*/tasks/{TASKID}.output",
            f"/private/tmp/claude-*/*/*/*/tasks/{TASKID}.output"]
    while time.time() < deadline:
        if SRC:                                   # redirect target preferred while we wait
            try:
                if os.path.exists(SRC):
                    return SRC
            except Exception:
                pass
        else:
            for p in pats:
                m = glob.glob(p)
                if m:
                    return m[0]
        time.sleep(0.3)
    # SRC was named but never appeared — fall back to the task output file.
    for p in pats:
        m = glob.glob(p)
        if m:
            return m[0]
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
        O.emit(LOG, O.rule(), O.label("■ output not found", SLOT_RGB))
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
                O.emit(LOG, O.gut("\n".join(unescape(ln.decode("utf-8", "replace")) for ln in lines),
                                  SLOT_RGB, outer=OUTER_RGB))
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
        O.emit(LOG, O.gut(unescape(pending.decode("utf-8", "replace")), SLOT_RGB, outer=OUTER_RGB))
    elapsed = max(0.0, last_active - start)      # active duration, excluding any idle wait
    dur = f"{elapsed:.1f}s" if elapsed < 60 else f"{int(elapsed // 60)}m{int(elapsed % 60):02d}s"
    text = "background finished" if KIND == "bg" else "monitor ended"
    # Finish chip uses this stream's slot colour (same as its gutter) so you can
    # tell which stream finished. Top-level jobs get a RULE-bracketed finish; a
    # subagent's nested job gets just the chip behind its single outer gutter bar
    # (the subagent block already frames it), so it stays visually contained.
    chip_txt = "■ " + text + " · " + dur
    if OUTER_RGB:
        O.emit(LOG, O.label(chip_txt, SLOT_RGB, outer=OUTER_RGB))
    else:
        O.emit(LOG, O.rule(), O.label(chip_txt, SLOT_RGB), O.rule())

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
        subprocess.run([os.path.join(here, "claude-tab-status.sh"), "bg-recheck", LOG + ".slots"],
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
