# plugins/claude_code/stream.py — bg/fg/monitor output tailer
# Entry point: claude-stream.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-stream.py KIND TASKID MIRROR_LOG SLOT [SIG] [OUTER]
#
# Detached tailer for the kitty command-mirror pane. Background Bash jobs and
# Monitor streams both write their output to a …/tasks/<id>.output file, but no
# hook fires while they run — so this process (spawned detached by the launch
# hook) tails that file and appends each new line to the mirror log (as structured
# paint ops via core.ops), then a closing rule + finish chip when the job ends.
# NB Claude Code creates that file LAZILY, on the first output byte — a quiet
# persistent monitor has none for minutes/hours, so a monitor waits for it keyed
# on its command process's liveness (monitor_wait_file), not a bounded deadline.
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
import glob, os, re, subprocess, sys, time

from core import mdrender as MDR
from core import ops as O
from core import render as R
from core import slots as claude_slots
from core import state as S
from core import tail as T

from core.paths import BIN  # bin/, where the sibling ENTRY scripts live

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


# --- run identity (argv/env contract) --------------------------------------------
# All of this used to be parsed at module top level — importing the module read
# argv and could even CLAIM a palette slot (a state-DB write). It now lives in
# _init(), called from entry(), so IMPORTING this module (tests, tooling) reads
# no argv and touches no DB — only running it does. The placeholders below just
# name the module globals every phase function reads at call time.
KIND   = "bg"
TASKID = LOG = SIG = OUTER = ""
SLOT, _MARKER = 0, None
SLOT_RGB = (0, 0, 0)
OUTER_RGB = None
SRC = DONE = CMD = ""
OWN = SKIP_EXISTING = False
POS0 = None
GROUP = None
RKIND = RVALUE = RENDER_KIND = None
MD = SNIFF = False
# cleanup()'s run-once state: the ran-flag (teardown must not run twice — it is
# both main()'s tail step by way of stream_lifecycle's on_exit AND the crash
# path's) plus the tailed path, bound in main() the moment wait_source resolves
# it so a crash after that point can still remove our own tee file.
_CLEANED = {"done": False, "path": None}


def _init(argv):
    """Bind this run's identity from the shim's argv:
      claude-stream.py KIND TASKID MIRROR_LOG SLOT [SIG] [OUTER]
    plus the CLAUDE_STREAM_* env contract and everything derived from them
    (slot claim/colour, outer gutter colour, content-render detection)."""
    global KIND, TASKID, LOG, SIG, OUTER, SLOT, _MARKER, SLOT_RGB, OUTER_RGB
    global SRC, OWN, DONE, SKIP_EXISTING, GROUP, CMD
    global RKIND, RVALUE, MD, RENDER_KIND, SNIFF
    KIND   = argv[1] if len(argv) > 1 else "bg"
    TASKID = argv[2] if len(argv) > 2 else ""
    LOG    = argv[3] if len(argv) > 3 else ""
    SIG    = argv[5] if len(argv) > 5 else ""   # monitor: signature to find its process
    OUTER  = argv[6] if len(argv) > 6 else ""   # "r,g,b" subagent colour -> double gutter

    # The launcher (claude-cmd-fmt / claude-monitor-fmt) claims a palette slot, colours
    # the header chip with it, and passes the index here so the gutter + finish chip
    # match — header, gutter, and finish all share one colour, and parallel jobs differ.
    # (If no slot is passed we claim our own, as a fallback.)
    if len(argv) > 4 and argv[4].lstrip("-").isdigit():
        SLOT, _MARKER = int(argv[4]), None
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

    # (env contract — see the comments on each name above the placeholders' old
    # top-level homes, kept below at their point of use)
    SRC = os.environ.get("CLAUDE_STREAM_SRC") or ""
    OWN = os.environ.get("CLAUDE_STREAM_OWN") == "1"
    DONE = os.environ.get("CLAUDE_STREAM_DONE") or ""
    SKIP_EXISTING = os.environ.get("CLAUDE_STREAM_SKIP_EXISTING") == "1"
    global POS0
    try:
        POS0 = int(os.environ["CLAUDE_STREAM_POS0"])
    except (KeyError, ValueError):
        POS0 = None
    GROUP = os.environ.get("CLAUDE_STREAM_GROUP") or None
    CMD = os.environ.get("CLAUDE_STREAM_CMD") or ""

    _CLEANED["done"], _CLEANED["path"] = False, None   # fresh run, fresh teardown

    RKIND, RVALUE = _detect_render(CMD)
    MD = bool(RKIND and RKIND.name == "md")
    RENDER_KIND = (None if RKIND is None
                   else RKIND.name + ":" + RVALUE if RKIND.streamer_takes_value
                   else RKIND.name)
    SNIFF = (RENDER_KIND is None and KIND == "fg"
             and os.environ.get("CLAUDE_MIRROR_MD_SNIFF", "1") != "0")


def release_slot():
    # No-op once the session's state DB is parked: the live table went with it,
    # and slots.release's connect would CREATE a fresh empty DB at the live
    # path — the exact hazard the completion loops' parked() exits avoid. This
    # runs inside cleanup() (stream_lifecycle's on_exit), i.e. also ON the
    # parked exit path, and a silent-so-far tailer has no cached connection to
    # hide behind.
    if S.parked(LOG):
        return
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
# -> SRC = $CLAUDE_STREAM_SRC (bound in _init)
#
# OWN ($CLAUDE_STREAM_OWN):
# Set only for a "fg" job whose output file we created ourselves (a tee target, not
# the command's own explicit redirect) — safe to delete once fully read.
#
# DONE ($CLAUDE_STREAM_DONE):
# The ".done" sentinel path claude-cmd-pre.py agreed with claude-cmd-fmt.py on — a
# session-keyed /tmp path, deliberately NOT derived from SRC (when SRC is the
# command's own redirect target, `SRC + ".done"` would land next to a user file).
# Falls back to `path + ".done"` below for launchers predating this env var.
#
# SKIP_EXISTING ($CLAUDE_STREAM_SKIP_EXISTING):
# Set in two cases where the tailed file already holds bytes that are NOT this job's
# output: (a) a Ctrl+B-converted command's replacement "bg" tailer — the departing fg
# tailer already showed everything up to the hand-off, and Claude Code's task-output
# file holds the FULL output from the start; (b) a `>>` append redirect — the target
# file's prior contents predate the command. Skip whatever exists at spawn time.
#
# GROUP ($CLAUDE_STREAM_GROUP):
# The block's copy-group id (⧉ copy links), set by the launcher (claude-cmd-pre.py:
# the tool_use_id; claude-cmd-fmt.py: the backgroundTaskId) so this tailer's
# gut/finish ops join the same group as the header/code ops the launcher emitted.
# Unset for launchers that don't tag (monitors, a subagent's nested jobs) — those
# blocks just render without copy links, exactly as before.
#
# CMD ($CLAUDE_STREAM_CMD):
# The ORIGINAL (pre-tee-wrap) command whose output this stream carries, passed by
# every launcher via hookkit.stream_env. Content-render detection — does this fg
# command stream a markdown / JSON / YAML / source file's raw contents
# (cat/head/tail of a .md|.json|.yml|.kt, `sed -n … x.py`, a bare `< file.md`)?
# — runs HERE, in the presenting process, not at the launch site. It's a pure
# function of the command (the CT.RENDER_KINDS registry, one entry per render
# mode, gated default-on by each entry's CLAUDE_MIRROR_* env), and when it lived in
# claude-cmd-pre.py's main path the OTHER fg launch site (a subagent's command,
# substream.spawn_fg_tailer) silently missed it — every launcher gets rendering
# by passing the command, and a new render mode is one change in one place.
# fg-only, as before: bg/monitor output is a job log, not a file's contents
# (the fence-sniff below stays the only content-keyed fallback).


def _detect_render(cmd):
    """(RenderKind, detection value) for this stream's command — the FIRST
    registry entry (priority order) whose env gate is on and whose detector
    fires; (None, None) for a non-fg stream, an empty command, or a command
    that streams no such file. Iterates CT.RENDER_KINDS, so a new render mode
    is a single registry entry."""
    if not cmd or KIND != "fg":
        return None, None
    from plugins.claude_code import tools as CT
    for rk in CT.RENDER_KINDS:
        if os.environ.get(rk.env, "1") == "0":
            continue
        v = rk.detect(cmd)
        if v:
            return rk, v
    return None, None


def _content_streamer():
    """Instantiate RKIND's core content streamer from its registry-declared
    "module:Class" spec (passing the detection value — the lexer — when the
    entry says its ctor takes one). None when no render mode was picked."""
    if RKIND is None:
        return None
    import importlib
    mod, cls = RKIND.streamer.split(":")
    factory = getattr(importlib.import_module(mod), cls)
    return factory(RVALUE) if RKIND.streamer_takes_value else factory()
# "Fenced output is markdown": when NO filename render mode was picked, sniff a fg
# command's output for a fenced code block (```lang) — the one markdown signal that's
# both unambiguous and rare in logs. If the FIRST data-bearing read contains a fence,
# the whole stream renders as markdown (prose + per-language fences); otherwise it
# streams verbatim, exactly as before. The decision is made on that first read only —
# never buffered across polls — so live line-by-line streaming is preserved. Off with
# CLAUDE_MIRROR_MD_SNIFF=0. (SNIFF, bound in _init.)
_FENCE = re.compile(r"(?m)^[ \t]{0,3}(```|~~~)[^\n`]*$")
# Timing seams (docs/testing.md): how long bg/fg wait for the task output file
# to appear (a monitor waits on process liveness instead — monitor_wait_file),
# and how long to keep trying to identify the monitor's process (find_proc)
# before concluding there is nothing to key liveness on.
FIND_S = float(os.environ.get("CLAUDE_STREAM_FIND_S") or 12)
PROCFIND_S = float(os.environ.get("CLAUDE_STREAM_PROCFIND_S") or 20)
# Backstop for fg ONLY (and shorter than the tailers' shared 6h cap): an
# interactive foreground command past 2h is far likelier a wedged tailer than
# a real command, and its live fg slot row keeps the tab blue the whole time.
# bg/monitor deliberately have NO backstop — their completion signals
# (write-holder vanishing / monitor process exit) are definitive, and a
# legitimately long background job must keep streaming past any cap.
FG_BACKSTOP_S = 7200
# Ceiling on ONE gut op's raw text (bytes of joined lines, pre-ANSI). A capped
# pump can still hand over up to PUMP_MAX_B at once, and one giant op is the
# renderer's worst case: EVERY reflow (resize, click-to-view toggle, pane
# re-open) re-wraps every op's full text, so a monolithic burst op re-costs its
# whole wrap forever. Split the batch into multiple ≤128KB gut ops instead —
# each op still a multi-line batch, never a per-line op (per-line ops were the
# original design and the emit/txn overhead is why batching exists).
OP_MAX_B = int(os.environ.get("CLAUDE_STREAM_OP_MAX_B") or 128 * 1024)

# Where Claude Code drops its tasks/<id>.output files for bg jobs and monitors.
# This is Claude Code's OWN on-disk layout (empirical, macOS), not ours — so it
# does NOT belong in core/paths.py, which owns only the paths this repo mints.
# Env-overridable so the test suite can point the glob at a per-test sandbox
# instead of shared host /tmp (docs/testing.md); unset, behavior is identical.
TASKS_GLOB_ROOT = os.environ.get("CLAUDE_TASKS_GLOB_ROOT") or "/private/tmp/claude-*"


def glob_task_output(taskid=None):
    tid = taskid or TASKID
    pats = [f"{TASKS_GLOB_ROOT}/*/*/tasks/{tid}.output",
            f"{TASKS_GLOB_ROOT}/*/tasks/{tid}.output",
            f"{TASKS_GLOB_ROOT}/*/*/*/tasks/{tid}.output"]
    for p in pats:
        m = glob.glob(p)
        if m:
            return m[0]
    return None


def find_file(deadline):
    while time.time() < deadline:
        if SRC:                                   # redirect target preferred while we wait
            try:
                if os.path.exists(SRC):
                    return SRC
            except Exception:
                pass
        else:
            m = glob_task_output()
            if m:
                return m
        time.sleep(0.3)
    # SRC was named but never appeared — fall back to the task output file.
    return glob_task_output()


def _outcome_pending():
    """Non-destructive: has PostToolUse handed off this fg command's outcome yet?
    Its arrival means the (blocking) Bash tool call resolved — the command is no
    longer running, so a still-absent output file is now GENUINELY absent rather
    than merely late. Peek, never take: make_is_done's fg_done() must still
    consume this record to pick up the finish chip / fallback body. Keyed the
    same way as that consumer — "done:" + DONE (see cmd_fmt.py's hand_put)."""
    return bool(DONE) and S.hand_peek(LOG, "done:" + DONE) is not None


def wait_fg_src(run, start):
    """Wait for an fg command's output file (its own redirect target, or the tee
    file) to appear — LIVENESS-bounded, not the flat FIND_S deadline find_file
    uses. A command that creates its redirect target only late (`sleep 45; cmd >
    out`, a retry loop that writes on the Nth pass) is STILL RUNNING, so the file
    may yet appear; giving up at FIND_S released the fg slot and flipped the tab
    off blue mid-command while the late output never streamed (the audit tell was
    an `output-file-not-found` fg stream whose PostToolUse fired seconds later).
    This mirrors the monitor's process-liveness wait (monitor_wait_file) — keep
    waiting until the file lands, OR the outcome hand-off arrives (the command
    truly finished with no file), bounded by FG_BACKSTOP_S against a wedged
    tailer. Returns the path, or None (session parked -> run.end set here; else
    the caller paints the not-found chip)."""
    deadline = start + FG_BACKSTOP_S
    while time.time() < deadline:
        try:
            if os.path.exists(SRC):
                return SRC
        except Exception:
            pass
        if S.parked(LOG):
            run.end("state-db-parked (session end)")   # session over — quit quietly
            return None
        if _outcome_pending():
            # Command finished. One last look (it may have written the file and
            # exited between ticks), then fall back — a genuinely fileless command.
            try:
                if os.path.exists(SRC):
                    return SRC
            except Exception:
                pass
            return glob_task_output()
        time.sleep(0.3)
    return glob_task_output()


# `lsof -- path` scans the WHOLE process fd table on macOS (seconds on a loaded
# box), and every tailer used to run it SYNCHRONOUSLY on every poll tick (POLL_S
# can be 0.05 under test). Several concurrent tailers then spawn dozens of lsofs
# per second, each lsof slows the others down, and once one exceeded the old 5s
# subprocess timeout the "assume still writing" fallback starved writer-gone
# INDEFINITELY — the CI macOS-runner flake class no wait-ceiling could fix; a
# blocking slow lsof also froze the whole tailer loop (fg sentinel hand-offs
# went unconsumed for its full duration). So the probe is ASYNC and throttled:
# has_writer() starts an lsof at most every LSOF_MIN_S, returns the last known
# answer immediately, and harvests the result on a later tick — the loop never
# blocks, and lsof pressure is capped at ~1/s per tailer. The answer therefore
# lags by up to one lsof duration + LSOF_MIN_S, which only delays writer-gone
# (grace is 2s in production) and can never fire it early: the initial and
# every failure answer is True ("assume still writing").
LSOF_MIN_S = float(os.environ.get("CLAUDE_STREAM_LSOF_S") or 1.0)
# Cap on one probe's runtime. Generous: a slow-but-successful lsof still yields
# a REAL answer; a tight cap turns mere slowness into a poisoned permanent True.
LSOF_TIMEOUT_S = 30
_LSOF_STATE = {"missing": False, "audited": False,
               "proc": None, "started": 0.0, "at": 0.0, "val": True}


def _lsof_parse(out):
    # True if some process holds the file open for writing (FD ends w/u/W).
    return any(len(p) >= 4 and p[3][-1:] in "wuW"
               for p in (line.split() for line in out.splitlines()[1:]))


def has_writer(path):
    # Non-blocking: last known answer now, fresh probe harvested next tick.
    # A FAILED/hung lsof must read as "can't tell — assume still writing", NOT
    # "no writer": returning False here once let writer_gone fire mid-command
    # during a silent phase (premature finish chip, tab flipped green, remaining
    # output lost). Only a MISSING lsof binary keeps returning False —
    # writer-liveness is impossible without it, and "always alive" would mean
    # bg streams never end (they deliberately have no backstop).
    st = _LSOF_STATE
    if st["missing"]:
        return False
    now = time.time()
    p = st["proc"]
    if p is not None:
        if p.poll() is None:
            if now - st["started"] > LSOF_TIMEOUT_S:    # hung probe: kill, retry later
                try:
                    p.kill()
                    p.wait()
                except Exception:
                    pass
                st["proc"] = None
                st["at"], st["val"] = now, True
                if not st["audited"]:       # first occurrence only, or it spams
                    st["audited"] = True
                    A.error(LOG, "lsof timed out — assuming writer still present",
                            {"path": path})
        else:
            try:
                out = p.stdout.read() or ""
            except Exception:
                out = ""
            finally:
                p.stdout.close()
            st["proc"] = None
            st["at"], st["val"] = now, _lsof_parse(out)
    if st["proc"] is None and now - st["at"] >= LSOF_MIN_S:
        try:
            st["proc"] = subprocess.Popen(
                ["lsof", "--", path], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True)
            st["started"] = now
        except FileNotFoundError:
            st["missing"] = True
            A.error(LOG, "lsof missing — writer-liveness disabled", {"path": path})
            return False
        except Exception:
            if not st["audited"]:
                st["audited"] = True
                A.error(LOG, "lsof spawn failed — assuming writer still present",
                        {"path": path})
            st["at"], st["val"] = now, True
    return st["val"]


alive = S.pid_alive                             # EPERM (foreign-owned) counts as alive


def monitor_sig(cmd):
    """Extract the monitor signature token from a command — the WIRE CONTRACT
    between the monitor launch sites (monitor_fmt.py's PostToolUse handler and
    substream.py's nested spawn_tailer) and find_proc below, which greps this
    token in `ps` argv output to identify the monitor's persistent command
    process. The extraction (longest run of 5+ command-ish chars) must be
    identical at launch and match time, so both launchers call this ONE helper
    — the two hand-copied versions could drift and silently break monitor
    completion detection."""
    toks = re.findall(r"[\w./:@=+-]{5,}", cmd or "")
    return max(toks, key=len) if toks else ""


# ps whitespace-escapes — a command's embedded newlines/tabs never survive into
# `ps` argv output verbatim: they come back escaped (`$'\n'` from zsh's quoting,
# octal `\012`/`\011` from BSD ps, or a bare `\n`), while CLAUDE_MONITOR_CMD holds
# the RAW command with real newlines. `_norm_cmd` erases exactly these (the whole
# escape token, `n`/`012` residue and all) plus real whitespace and shell
# backslash-escapes (`\ `, `\>`, `\(` …), so the same command normalizes identically
# whether it arrives raw or ps-escaped — the containment check below is then
# insensitive to HOW ps rendered a multi-line command (a `python3 - <<'PY'` heredoc
# monitor whose raw `\n` could never be a substring of the escaped argv, which
# silently killed the FULL-command disambiguation and dropped find_proc to the
# ambiguous sig-only path — docs/streaming.md, *Monitor completion detection*).
_WS_ESC = re.compile(r"\$'\\[nrt]'|\\0\d\d|\\[nrt]")


def _norm_cmd(s):
    return re.sub(r"[\s\\]+", "", _WS_ESC.sub("", s or ""))


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
    # hits return None — the idle fallback then closes the block instead. The full
    # match is whitespace/escape-INSENSITIVE (`_norm_cmd`): a multi-line heredoc
    # monitor's raw newlines never match ps's escaped rendering, so a raw substring
    # check always failed for it and left only the ambiguous sig (often just the
    # project path) — every session in that dir then matched and find_proc gave up.
    if not sig:
        return None
    try:
        out = subprocess.run(["ps", "-axww", "-o", "pid=,command="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    full = os.environ.get("CLAUDE_MONITOR_CMD") or ""
    full_n = _norm_cmd(full) if full else ""
    me, hits, full_hits = os.getpid(), [], []
    for line in out.splitlines():
        line = line.strip()
        pid_s, _, args = line.partition(" ")
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == me or "claude-stream.py" in args:
            continue
        # sig is a token OF the command (and unescaped by the shell — the monitor_sig
        # charset carries no shell-special chars), so it appears verbatim in the
        # wrapper's argv and cheaply gates the more expensive full-command normalize.
        if sig not in args:
            continue
        if full_n and full_n in _norm_cmd(args):
            full_hits.append(pid)
        else:
            hits.append(pid)
    if full_hits:
        return full_hits[-1]
    if not full:
        return hits[-1] if hits else None   # old launcher, no full cmd: old behavior
    return hits[0] if len(hits) == 1 else None


def mon_proc_found(pid):
    """Audit the monitor pid latch — the moment completion detection is keyed
    to a real process. Makes "why did this monitor end idle-fallback?" (was the
    process ever identified?) answerable from the DB, and gives the e2e suite
    an observable to synchronise on before it kills its monitor stand-in (a
    short-lived stand-in raced the tailer's startup on loaded runners)."""
    A.state_file(LOG, "monitor:" + TASKID, "proc-found", {"pid": pid})


def monitor_wait_file(run, start):
    # Claude Code creates tasks/<id>.output LAZILY, on the monitor's first output
    # byte — a quiet persistent monitor (poll every N seconds, print only on
    # activity) legitimately has NO file for minutes or hours. A bounded wait here
    # painted "■ output not found", released the slot, and let the tab clear to
    # green while the monitor ran on by design. So a monitor waits for the file
    # for as long as its command PROCESS is alive (the same find_proc/alive
    # liveness completion detection uses); the process dying with no file ever
    # written is the real "nothing to show" signal. If the process can't be
    # identified at all (ambiguous argv match — see find_proc) there is nothing to
    # key liveness on, so fall back to the old bounded give-up rather than hold
    # the tab blue forever.
    # Returns (path, pid); path None means the stream was ended here.
    pid, find_deadline = None, start + PROCFIND_S
    while True:
        m = glob_task_output()
        if m:
            return m, pid
        if S.parked(LOG):
            # SessionEnd parked the state DB — session over, quit footer-less
            # (S.parked, the shared probe the substream/codex tailers poll too).
            run.end("state-db-parked (session end)")
            return None, None
        now = time.time()
        if pid is None:
            pid = find_proc(SIG)
            if pid is not None:
                mon_proc_found(pid)
            elif now > find_deadline:
                O.emit(LOG, O.rule(), O.label("■ output not found", SLOT_RGB))
                run.end("output-file-not-found (monitor process never found)")
                return None, None
        elif not alive(pid):
            O.emit(LOG, O.rule(),
                   O.label("■ monitor ended · no output", SLOT_RGB), O.rule())
            run.end("monitor-exited-silent")
            return None, None
        time.sleep(T.POLL_S)


def wait_source(run, start):
    """Locate the output file to tail — the wait differs by kind (a monitor
    waits on its process's liveness, bg/fg on a bounded deadline). Returns
    (path, mon_pid); path None means the stream already ended here (the
    "not found" chip / silent-monitor exit was painted and run.end called)."""
    mon_pid = None
    if KIND == "monitor" and not SRC:
        path, mon_pid = monitor_wait_file(run, start)
    else:
        # fg waits on command LIVENESS (the outcome hand-off), like the monitor
        # waits on its process — a late-created redirect target must not be given
        # up on at FIND_S while the command still runs (wait_fg_src). bg (and a
        # legacy fg with no DONE key) keeps the flat FIND_S deadline.
        if KIND == "fg" and SRC and DONE:
            path = wait_fg_src(run, start)
        else:
            path = find_file(start + FIND_S)
        if not path and run.end_reason == "?":     # not already ended (e.g. parked)
            O.emit(LOG, O.rule(), O.label("■ output not found", SLOT_RGB))
            run.end("output-file-not-found")
    return path, mon_pid


def open_tailer(path):
    """A FileTailer positioned at the start of THIS job's output: offset 0
    normally, or the file's SPAWN-time size when the existing bytes predate the
    job (Ctrl+B hand-off / `>>` append — see SKIP_EXISTING above). The launcher
    measures that size and passes it as CLAUDE_STREAM_POS0 (hookkit.stream_env)
    — measuring here, at open time, silently skipped any output that landed
    during this tailer's own startup (seconds under load): a permanently lost
    line. The open-time getsize survives only as the fallback for a launcher
    that predates POS0."""
    pos0 = 0
    if SKIP_EXISTING:
        if POS0 is not None:
            pos0 = POS0
        else:
            try:
                pos0 = os.path.getsize(path)
            except OSError:
                pos0 = 0
        A.state_file(LOG, "tail:" + TASKID, "open", {"path": path, "pos0": pos0})
    # line_max: this tailer surfaces command OUTPUT (verbatim / content-rendered
    # display), never parsed line-records — the one place the LINE_MAX_B
    # truncation is safe and wanted (see core/tail.py). The ⧉out copy link reads
    # the OP text, so an elided line copies elided too — accepted trade-off
    # (docs/click-to-view.md): the tee file is transient (deleted at cleanup),
    # so a full-output copy was never guaranteed anyway, and a monitor pane
    # truncating pathological output beats freezing on it.
    return T.FileTailer(path, pos=pos0, line_max=T.LINE_MAX_B)


def verbatim_batches(parts, op_max=None):
    """Split one pump's completed lines into ≤op_max-byte batches (by summed raw
    line length), each destined for ONE gut op. A batch always holds at least one
    line — a single over-cap line still becomes its own op (LINE_MAX_B already
    bounds it upstream)."""
    op_max = OP_MAX_B if op_max is None else op_max
    batch, n = [], 0
    for p in parts:
        if batch and n + len(p) > op_max:
            yield batch
            batch, n = [], 0
        batch.append(p)
        n += len(p) + 1
    if batch:
        yield batch


def make_pump(run, tail):
    """Build the line pump: read new complete lines and emit them as gut ops
    through one of three paths — a registry-picked content renderer (markdown/
    JSON/…), the fence-sniff (first data read decides md vs raw), or verbatim.
    Returns (pump, ctx); ctx carries the mutable render state the drain needs
    afterwards: cs (the live content streamer, possibly sniff-created),
    md_count, render_meta, and emit_md."""
    # Render mode: feed tailed text through a content renderer that returns
    # (text, bg) segments to emit as gut ops. Markdown holds incomplete blocks and
    # emits each completed one live (append-only); JSON buffers whole and renders at
    # close (a partial document is invalid). Non-render path is unchanged.
    run.lines = 0
    ctx = {"cs": _content_streamer(),
           "md_count": {"n": 0},
           "render_meta": {"kind": RENDER_KIND},
           "sniff": {"mode": "sniff" if SNIFF else "off"}}   # sniff -> md/raw on first data read
    if ctx["cs"] is not None:
        A.state_file(LOG, "render:" + TASKID, "start",
                     {"kind": RENDER_KIND, "wenmode": MDR.AVAILABLE})

    def emit_md(segments):
        for text, bg in segments:
            ctx["md_count"]["n"] += 1
            O.emit(LOG, O.gut(text, SLOT_RGB, outer=OUTER_RGB, g=GROUP, bg=bg))
    ctx["emit_md"] = emit_md

    def emit_verbatim(text):
        # text = complete lines joined with "\n" (a trailing "\n" per line); drop the
        # one trailing empty so we don't paint a spurious blank gutter row. A big
        # batch is split into ≤OP_MAX_B gut ops (verbatim_batches) so no single op
        # ever carries a whole burst — each op still a multi-line batch.
        parts = text.split("\n")
        if parts and parts[-1] == "":
            parts = parts[:-1]
        for batch in verbatim_batches(parts):
            O.emit(LOG, O.gut("\n".join(unescape(p) for p in batch),
                              SLOT_RGB, outer=OUTER_RGB, g=GROUP))

    def pump():
        lines = tail.pump()
        if lines:
            run.lines += len(lines)
            # Re-add the \n pump() stripped from each complete line, so a renderer
            # sees real line/block boundaries.
            text = "".join(ln.decode("utf-8", "replace") + "\n" for ln in lines)
            if ctx["cs"] is not None:
                emit_md(ctx["cs"].feed(text))
            elif ctx["sniff"]["mode"] == "sniff":
                # First data read decides — no cross-poll buffering (liveness).
                if _FENCE.search(text):
                    ctx["cs"] = MDR.MarkdownStreamer()
                    ctx["render_meta"]["kind"] = "md-sniff"
                    A.state_file(LOG, "render:" + TASKID, "start",
                                 {"kind": "md-sniff", "wenmode": MDR.AVAILABLE})
                    ctx["sniff"]["mode"] = "md"
                    emit_md(ctx["cs"].feed(text))
                else:
                    ctx["sniff"]["mode"] = "raw"
                    emit_verbatim(text)
            else:
                emit_verbatim(text)
        return lines                            # None -> file vanished
    return pump, ctx


def make_is_done(tail, path, mon_pid, st):
    """Build this kind's completion predicate `is_done(now) -> reason|None`
    (the closure dispatch below). st is the shared mutable state: st["override"]
    receives the fg PostToolUse outcome hand-off when the sentinel fires.

    Completion signal differs by kind:
      bg / fg — the command holds its output file open the whole time, so the
                write-holder vanishing (plus a short idle grace) is definitive
                (works for long silent jobs). For fg it is what keeps a
                still-running command's tab BLUE however long it runs, whether
                or not PostToolUse ever shows up (a Ctrl+B-backgrounded
                command's process — and our tee pipe — runs on well past when
                the original tool call's Post would have fired; a flat timeout
                would have wrongly declared it done). fg first checks the
                PostToolUse outcome hand-off (take-once state-DB record keyed
                by CLAUDE_STREAM_DONE — was a .done sentinel file).
      monitor — writes in bursts (no held file), but its command PROCESS is
                persistent and identifiable, and exits exactly when the monitor
                ends, so we track that process — robust at ANY cadence, no
                grace. Idle fallback only if the process was never found.
    """
    GRACE = float(os.environ.get("CLAUDE_STREAM_GRACE_S") or
                  (2.0 if KIND in ("bg", "fg") else 8.0))
    sentinel = ("done:" + (DONE or path + ".done")) if KIND == "fg" else None
    # mon_pid: already resolved by monitor_wait_file while waiting for a lazily
    # created output file. The find-deadline is keyed to NOW, not launch — the
    # file may have appeared minutes in, and a launch-keyed deadline would leave
    # no time to find the process before the idle fallback could fire.
    mon = {"pid": mon_pid, "deadline": time.time() + PROCFIND_S}

    def writer_gone(now):
        # Cheap pure checks FIRST: while output is flowing (idle < grace) no
        # lsof runs at all — has_writer is a whole-fd-table scan (see throttle
        # note above) and calling it on every poll tick was the lsof storm
        # that starved CI completion detection.
        return (tail.idle_for(now) >= GRACE and tail.size >= 0
                and not has_writer(path))

    def fg_done(now):
        taken = S.hand_take(LOG, sentinel) if sentinel else None
        if taken is not None:
            st["override"] = taken
            return "sentinel"
        if writer_gone(now):
            return "writer-gone"                # process gone, sentinel never showed
        return None

    def monitor_done(now):
        if mon["pid"] is None and now < mon["deadline"]:
            mon["pid"] = find_proc(SIG)
            if mon["pid"] is not None:
                mon_proc_found(mon["pid"])
        if mon["pid"] is not None:
            if not alive(mon["pid"]):           # process gone -> definitively done
                return "monitor-process-exited"
            return None
        if now > mon["deadline"] and tail.idle_for(now) >= GRACE:
            return "idle-fallback (monitor process never found)"
        return None

    def bg_done(now):
        return "writer-gone" if writer_gone(now) else None

    return {"fg": fg_done, "monitor": monitor_done}.get(KIND, bg_done)


def drain(run, pump, tail, ctx, override):
    """Final catch-up after the completion loop: one last pump, then flush
    whatever the render/verbatim path still holds (the trailing incomplete
    block or partial line), or fall back to the PostToolUse-captured output
    when nothing ever landed in SRC. Returns `converted` (Ctrl+B hand-off —
    the replacement bg tailer owns the rest of the block)."""
    while pump() is not None and tail.capped:   # final catch-up: drain the WHOLE
        pass                                    # backlog, not just one capped pump
    converted = KIND == "fg" and override and override.get("converted")
    if converted:
        run.end("converted-ctrl-b")
    if ctx["cs"] is not None:
        # Flush the trailing incomplete block (the last line has no terminating
        # blank, so the buffer held it) plus any fallback body, all through the
        # markdown renderer so the final block is styled like the rest.
        if tail.pending:
            ctx["cs"].feed(tail.pending.decode("utf-8", "replace"))
        if tail.pos == 0 and KIND == "fg" and not converted and override and override.get("fallback_body"):
            ctx["cs"].feed(override["fallback_body"])
        ctx["emit_md"](ctx["cs"].close())
        # Zero blocks from a non-empty render stream is the render-failure tell —
        # see claude_audit.py `anomalies` (render blocks=0).
        A.state_file(LOG, "render:" + TASKID, "done",
                     {"kind": ctx["render_meta"]["kind"], "blocks": ctx["md_count"]["n"]})
    elif tail.pending.strip():
        O.emit(LOG, O.gut(unescape(tail.pending.decode("utf-8", "replace")), SLOT_RGB,
                          outer=OUTER_RGB, g=GROUP))
    elif KIND == "fg" and tail.pos == 0 and not converted and override and override.get("fallback_body"):
        # Nothing ever landed in SRC — most likely an older Claude Code build that
        # ignored PreToolUse's updatedInput, so the command ran unwrapped. Fall back
        # to the real output PostToolUse captured itself rather than showing nothing.
        O.emit(LOG, O.gut(override["fallback_body"], SLOT_RGB, g=GROUP))
    return converted


def build_chip(kind, override, dur, slot_rgb):
    """(chip_txt, chip_rgb) for the finish chip — the four override branches:
      1. fg with a precomputed PostToolUse chip (the outcome hand-off carries
         the exact duration/exit/interrupted text) -> use it, in its colour.
      2. fg whose hand-off carries only pass/FAIL (a subagent fg command via
         claude-substream.py — no precomputed chip, the tailer owns the
         duration) -> "■ failed · <dur>" in red.
      3. default (bg / monitor / fg with no override) -> the kind's generic
         "■ <kind> finished/ended · <dur>" in the stream's slot colour.
    (The fourth case, a Ctrl+B-converted fg, never reaches here — the caller
    skips the chip entirely; see emit_finish_chip.)"""
    if kind == "fg" and override and override.get("chip"):
        return override["chip"], tuple(override.get("color") or slot_rgb)
    if kind == "fg" and override and override.get("failed"):
        return "■ failed · " + dur, O.RED
    text = {"bg": "background finished", "fg": "foreground finished"}.get(kind, "monitor ended")
    return "■ " + text + " · " + dur, slot_rgb


def emit_finish_chip(start, tail, override):
    """Paint the closing chip for this stream (rule-bracketed at top level, bare
    behind the outer gutter for a subagent's nested job)."""
    elapsed = max(0.0, tail.changed_at - start)  # active duration, excluding any idle wait
    chip_txt, chip_rgb = build_chip(KIND, override, O.fmt_dur(elapsed), SLOT_RGB)
    # Finish chip uses this stream's slot colour (same as its gutter) so you can
    # tell which stream finished. Top-level jobs get a RULE-bracketed finish; a
    # subagent's nested job gets just the chip behind its single outer gutter bar
    # (the subagent block already frames it), so it stays visually contained.
    if OUTER_RGB:
        O.emit(LOG, O.label(chip_txt, chip_rgb, outer=OUTER_RGB))
    else:
        # g on the finish chip too: after a long stream the header's ⧉ links are
        # far up in scrollback — the chip at the bottom offers the same copy.
        O.emit(LOG, O.rule(), O.label(chip_txt, chip_rgb, g=GROUP), O.rule())


def cleanup():
    """Post-stream teardown: remove our own tee file and fg-live record, release
    the slot, then ask the tab tracker to clear a stale red — in that order (the
    recheck must not see this tailer's own slot marker).

    Runs as stream_lifecycle's on_exit, so it fires on EVERY exit path — happy,
    crash, parked. It used to be main()'s last statement only: a main() that
    raised after open_tailer (renderer exception, signal) had its crash audited
    and its slot released, but leaked the tee .out until the 7-day sweep, the
    fg-live record until the next Bash PreToolUse noticed the dead pid, and
    never cleared a stale red tab. The ran-flag makes it once-only, so the
    happy path's ordering (chip -> cleanup -> stream_end) is unchanged.
    Parked-aware, per the invariant release_slot's guard documents: past a park
    only the tee-file removal (a plain /tmp file of ours, not a DB) survives —
    the fg-live take is a state-DB write whose connect would recreate the live
    DB or pollute the parked snapshot, and the bg-recheck is moot (the session
    is over and SessionEnd already cleared the tab)."""
    if _CLEANED["done"]:
        return
    _CLEANED["done"] = True
    path = _CLEANED["path"]

    if KIND == "fg" and OWN and path:
        try:
            os.remove(path)
        except Exception:
            pass

    parked = S.parked(LOG)
    if KIND == "fg" and not parked:
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
    # (release_slot carries its own parked no-op.)
    release_slot()

    if parked:
        return
    # No "background finished" hook exists, so if the tab went red while Claude
    # handed back to the user, it would stay red. Now that this job is done, ask
    # claude-tab-status.py to flip a *stale red* back to green (it no-ops unless
    # the tab is currently red and nothing else is still running). The detached
    # process inherited KITTY_LISTEN_ON / KITTY_WINDOW_ID from the launch hook.
    try:
        subprocess.run([os.path.join(BIN, "claude-tab-status.py"), "bg-recheck", LOG, KIND],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def main(run):
    if not (TASKID and LOG):
        return
    start = time.time()
    path, mon_pid = wait_source(run, start)
    if not path:
        return
    _CLEANED["path"] = path      # from here a crash can still remove the tee file

    tail = open_tailer(path)
    pump, ctx = make_pump(run, tail)

    st = {"override": None}          # the fg PostToolUse outcome hand-off, if any
    is_done = make_is_done(tail, path, mon_pid, st)
    backstop = start + FG_BACKSTOP_S if KIND == "fg" else None
    while True:
        if S.parked(LOG):
            # SessionEnd parked the state DB — session over, quit footer-less
            # (S.parked, the shared probe the substream/codex tailers poll too;
            # monitor_wait_file above runs the same check while waiting for a
            # lazily-created file). Checked BEFORE the pump, and returning past
            # drain/chip (cleanup still runs, parked-gated, as on_exit): a
            # post-park pump's O.emit would either
            # recreate a fresh empty DB at the live path — whose absence IS the
            # session-alive signal, so the next resume sees reuse-live-db and
            # strands the real history in the park — or, through a cached
            # connection, pollute the parked snapshot (docs/streaming.md).
            run.end("state-db-parked (session end)")
            return
        if pump() is None:
            run.end("src-file-vanished")
            break
        now = time.time()
        if backstop and now > backstop:         # stuck fg tailer can't run forever
            run.end("backstop-timeout")
            break
        if tail.capped:
            # A backlog remains past the per-pump read ceiling: keep pumping
            # (no sleep) and DON'T consult is_done yet — the writer may already
            # be gone (idle grace elapsed) while unemitted bytes remain, and
            # firing writer-gone here would truncate the drain to one last pump.
            continue
        reason = is_done(now)
        if reason:
            run.end(reason)
            break
        time.sleep(T.POLL_S)

    converted = drain(run, pump, tail, ctx, st["override"])
    if not converted:
        # Ctrl+B-converted (see claude-cmd-fmt.py): a fresh "bg" tailer against the
        # REAL backgroundTaskId output now owns the rest of this block (header, body,
        # finish chip) — this tailer just bows out quietly, no chip of its own, so the
        # two don't race or double-render.
        emit_finish_chip(start, tail, st["override"])
    # Teardown (tee file, fg-live record, slot release, bg-recheck) is
    # stream_lifecycle's on_exit — cleanup() — so it also runs when this
    # function raises or exits on the parked path above.


def entry():
    _init(sys.argv)
    with T.stream_lifecycle(LOG, KIND, task_id=TASKID, src_path=SRC,
                            ctx={"kind": KIND, "taskid": TASKID, "md": MD},
                            on_exit=cleanup) as run:
        main(run)
