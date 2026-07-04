#!/usr/bin/env python3
# claude-stream.py KIND TASKID MIRROR_LOG SLOT [SIG] [OUTER]
#
# Detached tailer for the kitty command-mirror pane. Background Bash jobs and
# Monitor streams both write their output to a …/tasks/<id>.output file, but no
# hook fires while they run — so this process (spawned detached by the launch
# hook) tails that file and appends each new line to the mirror log (as structured
# paint ops via claude_ops), then a closing rule + finish chip when the job ends.
#
#   KIND  "bg" | "monitor" | "fg"  — only changes the gutter colour + finish label
#   TASKID                  — backgroundTaskId / Monitor taskId (globally unique);
#                             for "fg" just a disambiguating string (SRC is used
#                             directly, no task-output-file glob lookup needed)
#   MIRROR_LOG              — /tmp/claude-mirror-<slug>.log
#   SLOT                    — palette slot index claimed by the launcher
#   SIG                     — monitor: signature token to find its process
#   OUTER                   — "r,g,b" subagent colour -> double gutter (nested job)
#
# Completion is detected the same way claude-tab-status.py detects a running
# background job: the writing process holds the output file open the whole time,
# so when no write-holder remains (lsof) and the size has stopped growing, the
# job is done. The tailer only reads the file, so it never counts itself.
#
# "fg" (claude-cmd-pre.py, a PreToolUse(Bash) hook) is different again: it wants
# the EXACT finish chip PostToolUse computes (duration_ms, exit code, interrupted)
# rather than guessing from file activity, so it waits for a small ".done" sentinel
# that claude-cmd-fmt.py's PostToolUse handler drops next to SRC once the real hook
# payload is in hand, and paints using that. If the sentinel never shows up (e.g. an
# older Claude Code build ignores PreToolUse's updatedInput, so the command never
# even got wrapped to write SRC) it falls back to a generic chip after a grace
# period — and if SRC ended up empty, to the real output PostToolUse hands it in
# that same sentinel, so a failed rewrite never means silently losing the output.
import glob, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_render as R
import claude_ops as O
import claude_state as S
import claude_tail as T

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

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
if KIND == "fg":
    SLOT_RGB = O.SLATE           # matches claude-cmd-pre.py's "▶ foreground" header
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
# Set only for a "fg" job whose output file we created ourselves (a tee target, not
# the command's own explicit redirect) — safe to delete once fully read.
OWN = os.environ.get("CLAUDE_STREAM_OWN") == "1"
# The ".done" sentinel path claude-cmd-pre.py agreed with claude-cmd-fmt.py on — a
# session-keyed /tmp path, deliberately NOT derived from SRC (when SRC is the
# command's own redirect target, `SRC + ".done"` would land next to a user file).
# Falls back to `path + ".done"` below for launchers predating this env var.
DONE = os.environ.get("CLAUDE_STREAM_DONE") or ""
# Set in two cases where the tailed file already holds bytes that are NOT this job's
# output: (a) a Ctrl+B-converted command's replacement "bg" tailer — the departing fg
# tailer already showed everything up to the hand-off, and Claude Code's task-output
# file holds the FULL output from the start; (b) a `>>` append redirect — the target
# file's prior contents predate the command. Skip whatever exists at spawn time.
SKIP_EXISTING = os.environ.get("CLAUDE_STREAM_SKIP_EXISTING") == "1"


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


_LSOF_STATE = {"missing": False, "audited": False}


def has_writer(path):
    # True if some process holds the file open for writing (lsof FD ends w/u/W).
    # A FAILED lsof (5s timeout on a busy box) must read as "can't tell — assume
    # still writing", NOT "no writer": returning False here once let writer_gone
    # fire mid-command during a silent phase (premature finish chip, tab flipped
    # green, remaining output lost). Only a MISSING lsof binary keeps returning
    # False — writer-liveness is impossible without it, and "always alive" would
    # mean bg streams never end (they deliberately have no backstop).
    if _LSOF_STATE["missing"]:
        return False
    try:
        out = subprocess.run(["lsof", "--", path], capture_output=True,
                             text=True, timeout=5).stdout
    except FileNotFoundError:
        _LSOF_STATE["missing"] = True
        A.error(LOG, "lsof missing — writer-liveness disabled", {"path": path})
        return False
    except Exception:
        if not _LSOF_STATE["audited"]:          # first occurrence only, or it spams
            _LSOF_STATE["audited"] = True
            A.error(LOG, "lsof failed — assuming writer still present",
                    {"path": path})
        return True
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3][-1:] in "wuW":
            return True
    return False


alive = S.pid_alive                             # EPERM (foreign-owned) counts as alive


def find_proc(sig):
    # Find the command process whose args contain `sig` (the monitor's command
    # runs as `zsh -c … eval '<command>'`, so the signature is in its argv). This
    # process stays alive across event gaps and exits exactly when the monitor
    # ends — a definitive completion signal at any cadence. Excludes self/streamers.
    #
    # Disambiguation: a FULL-command argv match (CLAUDE_MONITOR_CMD, set by the
    # launcher) always wins — `sig` is just the command's longest token, which can
    # equally match an UNRELATED long-lived process (another tail/editor holding
    # the same file path in its argv); latching onto that pid kept the monitor
    # block open and the tab blue forever, with the slot row owned by a live pid
    # so it was never reaped. With a full command available, ambiguous token-only
    # hits return None — the idle fallback then closes the block instead.
    if not sig:
        return None
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    full = os.environ.get("CLAUDE_MONITOR_CMD") or ""
    me, hits, full_hits = os.getpid(), [], []
    for line in out.splitlines():
        line = line.strip()
        pid_s, _, args = line.partition(" ")
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == me or "claude-stream.py" in args:
            continue
        if full and full in args:
            full_hits.append(pid)
        elif sig in args:
            hits.append(pid)
    if full_hits:
        return full_hits[-1]
    if not full:
        return hits[-1] if hits else None   # old launcher, no full cmd: old behavior
    return hits[0] if len(hits) == 1 else None


def main(run):
    if not (TASKID and LOG):
        return
    start = time.time()
    path = find_file(start + 12)
    if not path:
        O.emit(LOG, O.rule(), O.label("■ output not found", SLOT_RGB))
        run.end("output-file-not-found")
        return

    pos0 = 0
    if SKIP_EXISTING:
        try:
            pos0 = os.path.getsize(path)
        except OSError:
            pos0 = 0
    tail = T.FileTailer(path, pos=pos0)
    run.lines = 0

    def pump():
        lines = tail.pump()
        if lines:
            run.lines += len(lines)
            O.emit(LOG, O.gut("\n".join(unescape(ln.decode("utf-8", "replace")) for ln in lines),
                              SLOT_RGB, outer=OUTER_RGB))
        return lines                            # None -> file vanished

    # Completion signal differs by kind:
    #   bg / fg — the command holds its output file open the whole time, so the
    #             write-holder vanishing (plus a short idle grace) is definitive
    #             (works for long silent jobs). For fg it is what keeps a
    #             still-running command's tab BLUE however long it runs, whether
    #             or not PostToolUse ever shows up (a Ctrl+B-backgrounded
    #             command's process — and our tee pipe — runs on well past when
    #             the original tool call's Post would have fired; a flat timeout
    #             would have wrongly declared it done). fg first checks the
    #             PostToolUse outcome hand-off (take-once state-DB record keyed
    #             by CLAUDE_STREAM_DONE — was a .done sentinel file).
    #   monitor — writes in bursts (no held file), but its command PROCESS is
    #             persistent and identifiable, and exits exactly when the monitor
    #             ends, so we track that process — robust at ANY cadence, no
    #             grace. Idle fallback only if the process was never found.
    GRACE = 2.0 if KIND in ("bg", "fg") else 8.0
    sentinel = ("done:" + (DONE or path + ".done")) if KIND == "fg" else None
    mon = {"pid": None, "deadline": start + 20}
    # Backstop for fg ONLY (and shorter than the tailers' shared 6h cap): an
    # interactive foreground command past 2h is far likelier a wedged tailer than
    # a real command, and its live fg slot row keeps the tab blue the whole time.
    # bg/monitor deliberately have NO backstop — their completion signals
    # (write-holder vanishing / monitor process exit) are definitive, and a
    # legitimately long background job must keep streaming past any cap.
    backstop = start + 7200 if KIND == "fg" else None
    override = None

    def writer_gone(now):
        return (not has_writer(path) and tail.idle_for(now) >= GRACE
                and tail.size >= 0)

    def fg_done(now):
        nonlocal override
        taken = S.hand_take(LOG, sentinel) if sentinel else None
        if taken is not None:
            override = taken
            return "sentinel"
        if writer_gone(now):
            return "writer-gone"                # process gone, sentinel never showed
        return None

    def monitor_done(now):
        if mon["pid"] is None and now < mon["deadline"]:
            mon["pid"] = find_proc(SIG)
        if mon["pid"] is not None:
            if not alive(mon["pid"]):           # process gone -> definitively done
                return "monitor-process-exited"
            return None
        if now > mon["deadline"] and tail.idle_for(now) >= GRACE:
            return "idle-fallback (monitor process never found)"
        return None

    def bg_done(now):
        return "writer-gone" if writer_gone(now) else None

    is_done = {"fg": fg_done, "monitor": monitor_done}.get(KIND, bg_done)
    while True:
        if pump() is None:
            run.end("src-file-vanished")
            break
        now = time.time()
        reason = is_done(now)
        if reason:
            run.end(reason)
            break
        if backstop and now > backstop:         # stuck fg tailer can't run forever
            run.end("backstop-timeout")
            break
        time.sleep(T.POLL_S)

    pump()                                      # final catch-up read
    converted = KIND == "fg" and override and override.get("converted")
    if converted:
        run.end("converted-ctrl-b")
    if tail.pending.strip():
        O.emit(LOG, O.gut(unescape(tail.pending.decode("utf-8", "replace")), SLOT_RGB, outer=OUTER_RGB))
    elif KIND == "fg" and tail.pos == 0 and not converted and override and override.get("fallback_body"):
        # Nothing ever landed in SRC — most likely an older Claude Code build that
        # ignored PreToolUse's updatedInput, so the command ran unwrapped. Fall back
        # to the real output PostToolUse captured itself rather than showing nothing.
        O.emit(LOG, O.gut(override["fallback_body"], SLOT_RGB))

    if not converted:
        # Ctrl+B-converted (see claude-cmd-fmt.py): a fresh "bg" tailer against the
        # REAL backgroundTaskId output now owns the rest of this block (header, body,
        # finish chip) — this tailer just bows out quietly, no chip of its own, so the
        # two don't race or double-render.
        elapsed = max(0.0, tail.changed_at - start)  # active duration, excluding any idle wait
        dur = O.fmt_dur(elapsed)
        if KIND == "fg" and override and override.get("chip"):
            chip_txt = override["chip"]
            chip_rgb = tuple(override.get("color") or SLOT_RGB)
        else:
            text = {"bg": "background finished", "fg": "foreground finished"}.get(KIND, "monitor ended")
            chip_txt, chip_rgb = "■ " + text + " · " + dur, SLOT_RGB
        # Finish chip uses this stream's slot colour (same as its gutter) so you can
        # tell which stream finished. Top-level jobs get a RULE-bracketed finish; a
        # subagent's nested job gets just the chip behind its single outer gutter bar
        # (the subagent block already frames it), so it stays visually contained.
        if OUTER_RGB:
            O.emit(LOG, O.label(chip_txt, chip_rgb, outer=OUTER_RGB))
        else:
            O.emit(LOG, O.rule(), O.label(chip_txt, chip_rgb), O.rule())

    if KIND == "fg" and OWN:
        try:
            os.remove(path)
        except Exception:
            pass

    if KIND == "fg":
        # Remove our own fg-live record (matched on our pid so a NEWER command's
        # record is never touched). Normally PostToolUse consumes it — but a
        # cancelled command fires no hook at all, and the surviving record made
        # the next command's Pre think a live fg block was still in flight (no
        # live-streaming) until it noticed the dead pid.
        if S.hand_take(LOG, "fg-live", match={"pid": os.getpid()}) is not None:
            A.state_file(LOG, "state:fg-live", "remove-own",
                         "fg tailer exiting — reclaimed its own record")

    # Release this job's slot marker BEFORE the recheck below — bg_command_running
    # now detects running jobs via live slot markers, so the recheck must not see
    # this (now-finished) tailer's own marker, or it would refuse to clear the red.
    release_slot()

    # No "background finished" hook exists, so if the tab went red while Claude
    # handed back to the user, it would stay red. Now that this job is done, ask
    # claude-tab-status.py to flip a *stale red* back to green (it no-ops unless
    # the tab is currently red and nothing else is still running). The detached
    # process inherited KITTY_LISTEN_ON / KITTY_WINDOW_ID from the launch hook.
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        subprocess.run([os.path.join(here, "claude-tab-status.py"), "bg-recheck", LOG, KIND],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    with T.stream_lifecycle(LOG, KIND, task_id=TASKID, src_path=SRC,
                            ctx={"kind": KIND, "taskid": TASKID},
                            on_exit=release_slot) as run:
        main(run)
