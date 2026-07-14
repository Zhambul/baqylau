#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-mirror.py MIRROR_LOG [WIDTH]
#
# The command-mirror RENDERER. Runs inside the kitty split pane (launched by
# claude-split.py) and replaces the old `tail -F`. It polls the session's `ops`
# table (the per-session state DB, core.state — argv[1] is the mirror-log KEY the
# DB path derives from; written by core.ops producers), renders each op at the
# pane's CURRENT width, and — the whole point of this design — RE-RENDERS EVERYTHING
# on resize so the content reflows. Resizing the pane changes its pty size, which
# delivers SIGWINCH here; we recompute the width, clear the screen, and repaint
# every op.
#
# Width is read live from the pane itself (os.get_terminal_size), so producers never
# need to know it — they only emit width-independent ops. A literal WIDTH argv is
# accepted for non-tty testing.
import os, select, signal, subprocess, sys, time


def _ensure_pygments():
    """The renderer syntax-highlights commands (bash + embedded python) with
    pygments, and does so IN THIS PROCESS — so the interpreter running this file
    must have pygments, or every command paints in the plain default colour with no
    highlighting at all. kitty launches this pane with a PATH whose `python3` is
    often the bare macOS / Xcode build (no pygments), so when pygments is missing,
    probe for an interpreter that can import it and re-exec into it; if none is
    found, keep running here (still works, just uncoloured). Set
    CLAUDE_MIRROR_PYTHON to force a specific interpreter. (This replaces the old
    claude-mirror.sh wrapper, whose only job was this probe.)"""
    try:
        import pygments  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("_CLAUDE_MIRROR_REEXEC"):     # never re-exec twice
        return
    import shutil
    cands = [os.environ.get("CLAUDE_MIRROR_PYTHON"), shutil.which("python3"),
             os.path.expanduser("~/.pyenv/shims/python3"),
             "/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
    # Newest pyenv-installed CPython (e.g. .../versions/3.12.1/bin/python3), if any.
    try:
        import glob
        vers = sorted(glob.glob(os.path.expanduser("~/.pyenv/versions/[0-9]*/")),
                      key=lambda p: [int(x) for x in
                                     os.path.basename(p.rstrip("/")).split(".")
                                     if x.isdigit()])
        if vers:
            cands.append(os.path.join(vers[-1], "bin", "python3"))
    except Exception:
        pass
    me = os.path.realpath(sys.executable or "")
    for c in cands:
        if not c or not os.access(c, os.X_OK):
            continue
        if os.path.realpath(c) == me:
            continue
        try:
            ok = subprocess.run([c, "-c", "import pygments"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).returncode == 0
        except OSError:
            ok = False
        if ok:
            os.environ["_CLAUDE_MIRROR_REEXEC"] = "1"
            try:
                os.execv(c, [c, os.path.abspath(__file__)] + sys.argv[1:])
            except OSError:
                continue


_ensure_pygments()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from core import panescript as PS
from core import paths as P
from core import codefmt as CF
from core import render as R
from core import state as St
from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

_FE = None         # memoized Frontend — env is fixed at process start, so one resolve suffices


def _fe():
    global _FE
    if _FE is None:
        import frontends
        _FE = frontends.get()
    return _FE

LOG, FIXED_WIDTH = PS.parse_argv()

BANNER = "\033[38;5;244m ◧ command mirror — waiting for commands… \033[0m"

# Keep the last MAX_OPS parsed ops in memory so a resize can repaint without
# re-reading the file. Bounded so a very long-lived session can't grow memory
# without limit (the user's concern) — oldest history is dropped past the cap.
MAX_OPS = 8000

# The frame must FIT the terminal's scrollback: every full reflow rewrites the
# whole buffer (clear-scrollback + repaint), so painted rows beyond
# scrollback_lines simply don't exist afterwards — and a click-to-view restore
# targeting them CLAMPS at the ceiling (observed live: landed == total+1-h-5000
# on a 5000-line kitty scrollback = "the expand jumped somewhere random").
# Trimming the ops list to this row budget therefore loses nothing that could
# ever be scrolled to, and keeps every anchor/restore target reachable. Match
# it to kitty.conf's scrollback_lines minus a screenful (default 5000 - 200).
ROW_BUDGET = int(os.environ.get("CLAUDE_MIRROR_SCROLLBACK", "4800"))

OPS = []            # parsed ops (capped), for repaint-on-resize


width = PS.make_width(FIXED_WIDTH)

fit = PS.fit


def render(op, w):
    """One paint op -> ANSI text for the current width (may contain newlines).
    Cached per (op, width) so repeated repaints at the same width — and the common
    case of re-rendering unchanged ops — don't re-highlight/re-wrap needlessly."""
    c = op.get("_c")
    if c is not None and c[0] == w:
        return c[1]
    s = _render(op, w)
    op["_c"] = (w, s)
    return s


# ⧉ copy links: a g-tagged label op gets clickable " ⧉cmd ⧉out" affordances (OSC 8
# hyperlinks, claude-copy:// scheme). kitty resolves a click via open-actions.conf,
# which launches claude-copy.py with the URL; that handler re-reads the group's ops
# from the state DB and pipes command/output text to the clipboard
# (docs/click-to-view.md). The links are dim and zero-cost to everything else — a label without
# "g" renders exactly as before.
_LINK_TXT = (("cmd", "⧉cmd"), ("out", "⧉out"))   # default when a label carries no "lk"


def _copy_links(g, lk):
    """(rendered links, display width). `lk` is the label's [what, glyph] spec (a
    command block's default cmd/out pair when absent). The OSC 8 wrapper is
    zero-width, so the width is just " glyph" per link."""
    from urllib.parse import quote
    key = quote(P.sid_from_log(LOG), safe="")
    pairs = lk or _LINK_TXT
    parts, width = [], 0
    for what, txt in pairs:
        url = "claude-copy:///%s/%s/%s" % (key, quote(str(g), safe=""), what)
        parts.append(" " + R.hyperlink(url, R.DIM + txt + R.RST))
        width += 1 + R.dwidth(txt)
    return "".join(parts), width


def _render(op, w):
    # Every op's text passes R.neutralize on its way to the pane: the ops
    # stream carries RAW command output, and a replayed escape sequence
    # EXECUTES on every repaint (a tee'd @kitty-cmd DCS scrolled the pane to
    # the top on each reflow — observed live). SGR + OSC 8 survive.
    t = op.get("t")
    if t == "blank":
        return ""
    if t == "rule":
        return R.rule(w)
    if t == "label":
        outer = op.get("outer")
        avail = w - 2 - (2 if outer else 0)            # chip eats 2 cols; outer bar 2 more
        links = ""
        g = op.get("g")
        if g:
            rendered, lw = _copy_links(g, op.get("lk"))
            if avail >= lw + 24:                       # keep a useful chip width; a very
                links = rendered                       # narrow pane just drops the links
                avail -= lw
        chip = R.label(fit(R.neutralize(op.get("s", "")), max(1, avail)),
                       op["c"]) + links
        if outer:
            return R.fg(*outer) + "│ " + R.RST + chip
        return chip
    if t == "code":
        return CF.render(R.neutralize(op.get("s", "")), w, op.get("ind", "  "))
    if t == "gut":
        outer = op.get("outer")
        if outer:
            gprefix = R.fg(*outer) + "│ " + R.fg(*op["c"]) + "│ " + R.RST
            gw = 4
        else:
            gprefix = R.fg(*op["c"]) + "│ " + R.RST
            gw = 2
        s = viewbody(op) if (op.get("lex") or op.get("num") is not None) \
            else op.get("s", "")
        return R.wrap_gutter(R.neutralize(s), w, gprefix, gw, bg=op.get("bg"))
    if t == "line":
        return R.neutralize(op.get("s", ""))
    return ""


# Click-to-view expansion: a line op tagged "v" (a file-op one-liner —
# file_fmt.py bakes the claude-copy:///…/view hyperlink into its text) expands
# in place while its id is in the session's `view-open` kv set, which
# claude-copy.py toggles per click. The expanded body is the kv-stashed
# pre-rendered op list `view:<id>` (highlighted content / ± diff, stashed at
# hook time when the payload still existed). The main loop polls the open-set
# each tick and schedules a FULL repaint on any change — expansion/collapse is
# a reflow, exactly like a resize.
VIEW_OPEN = set()      # ids currently expanded (mirror of the `view-open` kv row)
_VIEW_OPS = {}         # id -> stashed op list (immutable once written; cached)


def view_block(gid):
    ops = _VIEW_OPS.get(gid)
    if ops is None:
        try:
            val = St.kv_get(LOG, "view:" + gid)
        except Exception:
            val = None
        ops = [o for o in (val or []) if isinstance(o, dict)]
        if ops:                # don't negative-cache a transient read failure
            _VIEW_OPS[gid] = ops
    return ops


def expanded(op):
    """The op plus its in-place view block when its id is open."""
    g = op.get("v")
    if g and g in VIEW_OPEN:
        return [op] + view_block(g)
    return [op]


def viewbody(op):
    """A view-block gut op's paint text: the raw stashed body, syntax-highlighted
    per its 'lex' lexer (pygments lives HERE — the producer hook may have run a
    bare python3) and line-numbered from its 'num'. Width-independent, so cached
    once on the op."""
    c = op.get("_hl")
    if c is not None:
        return c
    s = op.get("s", "")
    lex = op.get("lex")
    if lex:
        try:
            from core import coderender as C
            hi = C.render_code(s, lex)
            if hi is not None:
                s = hi
        except Exception:
            pass                                   # unhighlighted is still correct
    num = op.get("num")
    if num is not None:
        s = "\n".join(R.DIM + "%5d " % (num + i) + R.RST + ln
                      for i, ln in enumerate(s.split("\n")))
    op["_hl"] = s
    return s


def iter_painted(w, ops=None):
    """THE painted-row walk, shared by every site that must agree on what a
    full frame contains: each (op_index, op, inner_op, rendered_text) in
    paint order — every op with its open view blocks expanded, rendered at
    width `w`. Any two of these walks disagreeing is a model-vs-buffer
    divergence (a restore lands where the frame math said, not where the
    frame is), so there is exactly one."""
    for i, op in enumerate(ops if ops is not None else OPS):
        for o in expanded(op):
            yield i, op, o, render(o, w)


def painted_rows(w):
    """Total painted rows of a full frame at width `w`: the banner line (the
    leading 1) plus, per rendered op, its newline count + 1 — the +1 is the
    row the op's own trailing newline occupies. Callers computing a restore
    amount add ONE more (+1) themselves for the cursor row the frame's final
    newline leaves; that cursor +1 is scroll math, not frame content."""
    return 1 + sum(txt.count("\n") + 1 for _, _, _, txt in iter_painted(w))


def frame_bytes(w):
    """The full-reflow byte string: clear screen + scrollback, banner, every
    op (with open view blocks expanded) at width `w`."""
    out = ["\033[H\033[2J\033[3J", BANNER, "\n"]    # home, clear screen + scrollback
    for _, _, _, txt in iter_painted(w):
        out.append(txt); out.append("\n")
    return "".join(out)


def _height():
    try:
        return os.get_terminal_size().lines
    except OSError:
        return None


def repaint():
    w = width()
    body = frame_bytes(w)
    try:
        sys.stdout.write(body); sys.stdout.flush()
    except Exception:
        pass
    L.painted_size = (w, _height())
    _audit_paint("repaint", w, body)


def _audit_paint(kind, w, body):
    """One audit row per full reflow: what was actually painted (rows/width/
    ops/open-set). This is the ground truth against the toggle math — a
    view-reflow whose `up` disagrees with the painted row count is exactly
    the model-vs-buffer divergence class of bug."""
    try:
        A.state_file(LOG, St.db_path(LOG), "paint",
                     {"kind": kind, "w": w, "rows": body.count("\n"),
                      "ops": len(OPS), "open": len(VIEW_OPEN)})
    except Exception:
        pass


def paint_new(ops):
    w = width()
    out = []
    for _, _, _, txt in iter_painted(w, ops):
        out.append(txt); out.append("\n")
    try:
        sys.stdout.write("".join(out)); sys.stdout.flush()
    except Exception:
        pass


def _on_winch():
    L.resized = True


def trim_to_budget(w):
    """Drop the oldest ops until the rendered frame fits ROW_BUDGET rows.
    Like the MAX_OPS trim, this only changes what a FUTURE full repaint
    draws — already-painted lines stay in the terminal's scrollback until
    the next reflow rewrites it (at which point the trimmed rows would have
    fallen off the scrollback ceiling anyway)."""
    heights = [0] * len(OPS)
    for i, _, _, txt in iter_painted(w):
        heights[i] += txt.count("\n") + 1
    total = 1 + sum(heights)
    if total <= ROW_BUDGET:
        return
    drop, acc = 0, 0
    while drop < len(heights) and total - acc > ROW_BUDGET:
        acc += heights[drop]
        drop += 1
    del OPS[:drop]


def measure(gid):
    """(op_pos, line_idx, total_lines) of the v-tagged op under the CURRENT
    expansion state — op_pos its index in OPS, line_idx its 0-based line offset
    (the banner is line 0), total the full painted line count. line_idx is None
    when the op isn't in OPS (trimmed / never painted)."""
    w = width()
    acc, pos, idx = 1, None, None
    for i, op, o, txt in iter_painted(w):
        if idx is None and o is op and op.get("v") == gid:
            pos, idx = i, acc
        acc += txt.count("\n") + 1
    return pos, idx, acc


_TTY_OK = False      # stdin switched to no-echo/non-canonical (DSR handshake usable)

# A first miss this gross (rows) is not scroll-unit bias — it means residual
# trackpad momentum raced the restore itself, so the fix is redoing the
# absolute restore, not a delta correction (toggle_repaint), and a landing
# this far off must not be "converged" onto by restore_to.
GROSS_MISS_ROWS = 400

DRIFT_WATCH_S = 8.0      # post-toggle drift-watch window: how long every viewport movement is recorded
SETTLE_GUARD_S = 0.7     # settle-guard window: the landing owns the viewport this long (momentum hits early)
GUARD_CORRECTIONS = 2    # correction budget per toggle — never fight the user more than this
GUARD_SLACK_ROWS = 5     # displacement (rows) the guard tolerates before it corrects (wrapped-row bias)
TICK_S = 0.2             # idle tick cadence of the renderer loop (drift-watch sampling rate)
TICK_GUARD_S = 0.08      # faster tick while the settle guard is armed — catch momentum small


def tty_setup():
    """Put the pane's tty into no-echo, non-canonical mode so the renderer can
    read kitty's DSR cursor-position reply (the toggle bracket's ordering
    handshake) without the line discipline echoing it onto the screen or
    holding it for a newline. The pane is display-only, so losing canonical
    input costs nothing. Best-effort: without it the handshake is skipped."""
    global _TTY_OK
    try:
        import termios
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
        a = termios.tcgetattr(fd)
        a[3] &= ~(termios.ECHO | termios.ICANON)
        a[6][termios.VMIN], a[6][termios.VTIME] = 0, 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        _TTY_OK = True
    except Exception:
        pass


def await_dsr(timeout=1.0):
    """Wait for kitty's cursor-position report (the reply to the \\033[6n we
    append to the toggle frame). Its arrival PROVES kitty has parsed every
    byte written before it — the ordering handshake that lets the scroll
    command (a different channel: the rc socket) run against the final buffer
    instead of racing the pty stream. True when the reply (…R) arrived."""
    if not _TTY_OK:
        return False
    fd = sys.stdin.fileno()
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        try:
            if not select.select([fd], [], [], left)[0]:
                return False
            chunk = os.read(fd, 512)
        except Exception:
            return False
        if not chunk:
            return False
        if b"R" in chunk:
            return True


def locate_viewport(w, tag=None, near=None):
    """Where the viewport ACTUALLY is: match the pane's visible text (raw-
    socket get-text) against the current rendered rows — a GLOBAL search.
    This is both the pre-toggle anchor and the post-restore verifier. It
    replaced a click-pinned window search ([idx-h+1, idx], on the assumption
    the clicked line must be visible): a confident global match is stronger
    evidence than that assumption, and the window version missed real
    viewports (observed live — score 1/58 inside the window while the global
    match was 58/58 elsewhere), silently degrading to line-at-top jumps. A
    probe row (the first distinctive visible line) narrows the candidate set
    so this stays ~O(matches) instead of O(rows·h); full scan is the
    fallback. None (audited when `tag` names the caller) when anchorless or
    nothing matches confidently."""

    def _bail(reason, extra=None):
        # EVERY null path leaves evidence when `tag` names a caller that will
        # degrade on it — the 4x anchor:null / jump-to-end incident was only
        # crackable because the no-match path audited; the no-capture path
        # didn't, and stayed invisible.
        if tag:
            try:
                A.error(LOG, "viewport_anchor (%s)" % reason,
                        dict({"tag": tag}, **(extra or {})))
            except Exception:
                pass
        return None

    win = os.environ.get("KITTY_WINDOW_ID")
    if not win:
        return _bail("no window")
    txt, exc = None, None
    for attempt in range(3):        # the capture flakes under load — transient
        try:
            txt = _fe().get_text(win)
        except Exception as e:
            exc = e
        if txt:
            break
        time.sleep(0.08)
    if not txt:
        return _bail("no capture", {"exc": repr(exc) if exc else None})
    cap = [l.rstrip() for l in txt.split("\n")]
    while cap and not cap[-1]:
        cap.pop()
    if not cap:
        return _bail("empty capture")
    L.last_cap0 = cap[0][:60]
    rows = [R.strip_ansi(BANNER).rstrip()]
    for _, _, _, rtxt in iter_painted(w):
        rows.extend(r.rstrip() for r in R.strip_ansi(rtxt).split("\n"))
    L.loc_rows = len(rows)
    # Candidate offsets from a distinctive probe row; full scan as fallback.
    cands = None
    for k, line in enumerate(cap[:12]):
        if len(line) > 8:
            cands = [j - k for j, r in enumerate(rows) if r == line and j >= k]
            if cands:
                break
    if not cands:
        cands = range(max(1, len(rows) - len(cap) + 1))
    scored = []
    for j in cands:
        sc = sum(1 for a, b in zip(cap, rows[j:j + len(cap)]) if a == b)
        scored.append((sc, j))
    best_score = max((sc for sc, _ in scored), default=0)
    if best_score < max(3, len(cap) // 2):
        return _bail("no match", {"cap": len(cap), "rows": len(rows),
                                  "score": best_score,
                                  "cap0": cap[0][:60]})
    # TWIN DISAMBIGUATION: a buffer full of near-identical content (many
    # expanded views of the same file, repeated command outputs) makes the
    # capture match at MULTIPLE offsets — and picking the first best-scorer
    # teleported restores to the wrong copy while the verify then confirmed
    # that same wrong copy (a perfect-looking audit row for a real
    # user-visible jump — the "hide jumps to a random location" bug, and the
    # impossible 3600-row there-and-back "bounces" in drift rows). Every
    # caller has a natural prior (`near`): the clicked line for the anchor,
    # the restore target for the verify, the previous sample for the drift
    # watch — among near-best matches, take the one closest to it.
    ties = [j for sc, j in scored if sc >= best_score - 1]
    if near is not None:
        return min(ties, key=lambda j: abs(j - near))
    for sc, j in scored:                     # no prior: first best match
        if sc == best_score:
            return j


def restore_to(j0):
    """ABSOLUTE re-restore of the viewport top to row `j0`, computed against
    the CURRENT content (appends may have grown the doc since the toggle).
    Used by the settle guard — a relative correction against a moving target
    (trackpad momentum mid-flight) amplifies instead of fixing."""
    win = os.environ.get("KITTY_WINDOW_ID")
    h = _height()
    if not win or h is None:
        return False
    w = width()
    total = painted_rows(w)
    try:
        fe = _fe()
        ok = _restore(fe, win, total + 1 - h - j0)
        landed = locate_viewport(w, near=j0)
        if landed is not None and landed != j0 \
                and abs(landed - j0) <= GROSS_MISS_ROWS:
            fe.scroll_window_fast(win, landed - j0)   # converge exactly
        return ok
    except Exception:
        return False


def _restore(fe, win, up):
    """The absolute restore: scroll to END (deterministic base), then up.
    True when the up-scroll (or the end, for up<=0) was delivered."""
    try:
        ok = bool(fe.scroll_window_end(win))
    except Exception:
        ok = False
    if up <= 0:
        return ok
    try:
        if fe.scroll_window_fast(win, up):
            return True
    except Exception:
        pass
    return fe.scroll_window(win, up) == 0


def toggle_repaint(gid, j0, follow=False):
    """The toggle reflow, tuned so the intermediate viewport-at-bottom frame
    lives for ~1ms — under one display frame, i.e. no visible flicker:

        full frame + DSR     ─ one pty write, one flush
        await_dsr()          ─ kitty's cursor report proves the frame is
                               parsed (the scroll goes over a DIFFERENT
                               channel, so without this it races the pty)
        scroll_window_fast() ─ raw-socket rc scroll, ~1ms

    Deliberately NO DEC 2026 freeze around this: kitty BUFFERS (does not
    parse) input while frozen, so the DSR reply stalls until the freeze ends
    — the handshake and the freeze are mutually exclusive, and a raced
    scroll landed at the buffer start (observed live). A ~100ms kitten
    subprocess here was the original flicker; the raw-socket scroll is what
    actually closes the visible gap.

    `j0` is the top-line anchor to restore (top-line rule). The restore is
    ABSOLUTE, not relative-to-wherever: the frame's clear-scrollback under a
    SCROLLED viewport (e.g. collapsing a block that was expanded-and-pinned)
    leaves kitty's scroll state clamped somewhere undefined — relative math
    from there landed at random offsets (the "hide jumps to random places"
    bug). So after the parse handshake the restore is scroll-to-END (a
    deterministic base) then up by (total+1)-h-j0 — the +1 is the cursor row
    the final newline leaves; up<=0 means the bottom IS the target frame.
    The landing is then VERIFIED (locate_viewport) and the restore retried
    once on a miss — a DSR timeout means the scrolls raced the frame parse.
    `follow=True` overrides the pin: the viewport was AT the bottom before
    the click, so the restore target is the new bottom (end, up 0) — pinning
    an at-bottom viewport to an absolute offset silently detaches it from
    the live tail, and the user later finds the pane parked on old content.

    Returns the result dict merged into the caller's view-reflow audit row
    (up / applied / dsr / landed / retried / follow)."""
    win = os.environ.get("KITTY_WINDOW_ID")
    w = width()
    body = frame_bytes(w)
    h = _height()
    up = None
    if h is not None:
        total = painted_rows(w)                # 1 (banner) + every op's rows
        if follow:
            j0 = total + 1 - h                 # the POST-toggle bottom
        up = total + 1 - h - j0
    if not win or up is None:
        try:
            sys.stdout.write(body); sys.stdout.flush()
        except Exception:
            pass
        return {"up": up, "applied": False}
    try:
        sys.stdout.write(body + "\033[6n"); sys.stdout.flush()
    except Exception:
        return {"up": up, "applied": False}
    L.painted_size = (w, h)
    _audit_paint("toggle", w, body)
    dsr = await_dsr(1.0)
    applied, landed, retried = False, None, False
    try:
        fe = _fe()
        applied = _restore(fe, win, up)
        # Verify-and-retry: where did the viewport actually land? A DSR
        # timeout means the scrolls raced kitty's parse of the frame and
        # clamped against a partial buffer (landed near the top — the
        # "random places" bug). The locate read itself came back AFTER the
        # parse, so one retry from that point is deterministic.
        landed = locate_viewport(w, near=j0)
        if up is not None and up > 0:
            # CONVERGE onto j0 exactly — "in place" means ZERO rows off, a
            # 17-row near-miss reads as "where did my expand go?". Each pass
            # scrolls by the measured error (never by re-running the same
            # absolute amount — a systematic bias like kitty's visual-line
            # scroll units vs these logical rows reproduces identically and
            # never converges). A GROSS first miss (>GROSS_MISS_ROWS) means residual
            # trackpad momentum raced the restore itself — redo the absolute
            # restore once, then delta-correct.
            for i in range(3):
                if landed is None or landed == j0:
                    break
                retried = True
                try:
                    if abs(landed - j0) > GROSS_MISS_ROWS and i == 0:
                        _restore(fe, win, up)
                    else:
                        fe.scroll_window_fast(win, landed - j0)
                except Exception:
                    pass
                landed = locate_viewport(w, near=j0)
    except Exception:
        try:
            A.error(LOG, "toggle_scroll (view toggle)", {"gid": gid, "up": up})
        except Exception:
            pass
    return {"up": up, "applied": applied, "dsr": dsr, "landed": landed,
            "retried": retried, "follow": follow, "home": j0}


# --- main-loop phases -------------------------------------------------------
# main() is a fixed sequence of phase functions sharing one small mutable
# context: the single module-level `_Loop` instance `L` below. The ORDER of
# phases is load-bearing (toggle planning must precede the reflow dispatch; the
# settle guard runs after it; the wait picks its tick from the guard) — each
# phase below is the corresponding block of the old monolithic loop, moved
# verbatim.


class _Loop:
    """ALL the loop-owned mutable state: the per-iteration fields the phases
    hand each other, plus the toggle/watch/guard/paint state that used to be
    module globals. One module-level instance (`L`) so everything — the phase
    functions, the paint/locate helpers they call, and the SIGWINCH handler —
    mutates the same object without `global` declarations."""
    __slots__ = ("db", "wake_r", "last", "ino", "cur_ino",
                 "toggled", "t_idx", "anchor", "follow",
                 "resized", "force_paint", "painted_size",
                 "last_cap0", "loc_rows",
                 "watch_until", "watch_pos", "watch_home",
                 "guard_until", "guard_left")

    def __init__(self):
        self.db, self.wake_r = None, None       # filled in by main()
        self.last, self.ino, self.cur_ino = 0, None, None
        self.toggled, self.t_idx, self.anchor, self.follow = \
            None, None, None, False
        self.resized = True       # paint once at startup (and on any SIGWINCH)
        self.force_paint = True   # next plain repaint must run even at an
        #                           unchanged size
        self.painted_size = None  # (w, h) of the last full frame — the
        #                           spurious-WINCH gate
        self.last_cap0 = None     # first line of locate_viewport's last
        #                           capture (audit evidence)
        self.loc_rows = None      # rendered row count from locate_viewport's
        #                           last successful run
        self.watch_until = 0.0    # post-toggle drift watch deadline (monotonic)
        self.watch_pos = None     # last verified viewport offset during the watch
        self.watch_home = None    # the toggle's verified landing — the
        #                           settle-guard target
        self.guard_until = 0.0    # settle-guard deadline (monotonic) — the
        #                           pane's position belongs to the TOGGLE
        #                           until then, not to input
        self.guard_left = 0       # settle-guard corrections remaining for
        #                           this toggle


L = _Loop()


def sync_inode(L):
    """Connection/inode lifecycle: detect a recreated DB file and reset.
    A recreated DB file (new session reusing the key, or a park/restore
    cycle) leaves the cached connection pointing at the OLD inode — drop it
    and re-read from the top. A missing DB just means no producer has
    written yet (or a resume is mid-restore): keep waiting, don't reset."""
    try:
        L.cur_ino = os.stat(L.db).st_ino
    except OSError:
        L.cur_ino = None
    if L.cur_ino is not None and L.cur_ino != L.ino:
        if L.ino is not None:
            stale = St._CONNS.pop(L.db, None)
            if stale is not None:
                try:
                    # Close, don't just drop: the discarded connection holds
                    # open fds to the DELETED old inode (+ its WAL/SHM),
                    # pinning them for this long-lived renderer's lifetime —
                    # one leak per park/restore or session-recreate cycle.
                    # (St._connect now also self-evicts on inode change; this
                    # stays because it drives the paint-state reset below.)
                    stale[0].close()
                except Exception:
                    pass
            L.last, OPS[:] = 0, []
            VIEW_OPEN.clear(); _VIEW_OPS.clear()
            L.resized = L.force_paint = True
        L.ino = L.cur_ino
        # ADOPT the persisted open-set silently: at startup (and after a
        # park/restore reset) the kv `view-open` rows are inherited state,
        # not a click — letting the poll below see them as a delta planned
        # a toggle restore toward some op's line, so a freshly toggled
        # pane opened scrolled deep into history instead of at the bottom.
        try:
            VIEW_OPEN.update(St.kv_get(LOG, "view-open") or [])
        except Exception:
            pass
        # Register this renderer's pid so claude-copy.py can SIGWINCH-nudge
        # an instant reflow after a view toggle (re-registered per inode:
        # a park/restore cycle starts a fresh kv table).
        try:
            St.kv_set(LOG, "renderer-pid", os.getpid())
        except Exception:
            pass


def drain_ops(L):
    """Drain any new ops appended to the table. Returns (new_ops, restart) —
    restart True means the table shrank under us and the loop must start the
    iteration over (the old `continue`)."""
    L.last, new = St.ops_after(LOG, L.last)
    if L.last < 0:                       # table shrank under us — restart
        L.last, OPS[:] = 0, []
        L.resized = L.force_paint = True
        return new, True
    OPS.extend(new)
    # Bound memory on a long session by dropping oldest ops from the in-memory
    # list. This only affects what a FUTURE full repaint (on resize) draws — the
    # already-printed lines stay in the terminal's scrollback — so we must NOT
    # repaint here. Repainting on every append once over the cap was the cause of
    # the per-message flicker on big sessions. Trim with hysteresis to avoid
    # slicing the list on literally every append.
    if len(OPS) > MAX_OPS + 1000:
        del OPS[:len(OPS) - MAX_OPS]
    if new:
        trim_to_budget(width())
    return new, False


def poll_toggles(L):
    """Click-to-view toggles: any change to the `view-open` kv set (a
    claude-copy.py /view click) reflows the whole pane, expanding or
    collapsing the affected blocks in place. BEFORE flipping the set,
    find the toggled line's offset and recover the current viewport
    top (the anchor must match against the PRE-toggle rendered rows —
    exactly what's on screen right now)."""
    try:
        cur_open = set(St.kv_get(LOG, "view-open") or [])
    except Exception:
        cur_open = VIEW_OPEN
    if cur_open != VIEW_OPEN:
        # Multiple gids in one delta (fast clicks coalescing into one
        # poll tick) still get the anchored restore — any one of them
        # serves as the plan's gid; the anchor is gid-independent.
        delta = cur_open ^ VIEW_OPEN
        L.toggled = min(delta) if delta else None
        L.t_idx = L.anchor = None
        L.follow = False
        # Plan UNCONDITIONALLY — in particular do NOT skip when
        # L.resized is set: the click handler's own SIGWINCH nudge sets
        # it before this branch ever runs, so gating on it silently
        # disabled the anchor for every nudged toggle (the pane then
        # parked at the bottom — the "scrolls to the very end" bug).
        # A genuine concurrent resize just changes width() under the
        # match, which fails confidence and degrades to the fallback.
        if L.toggled:
            # Trim BEFORE measuring: dropping rows after the plan
            # would shift every index the plan just computed.
            trim_to_budget(width())
            _, L.t_idx, _ = measure(L.toggled)
            if L.t_idx is not None:
                # near=t_idx: the user clicked a VISIBLE line, so the
                # true viewport is near it — a tie-breaking prior
                # among twin matches, NOT a search constraint (the
                # old windowed search that mistook this for a hard
                # assumption missed real viewports).
                L.anchor = locate_viewport(width(), tag="anchor",
                                           near=L.t_idx)
                # An at-bottom viewport follows the live tail; pinning
                # it to an absolute offset would silently detach it —
                # so the restore target becomes the NEW bottom. The
                # small tolerance absorbs the logical-vs-visual line
                # bias in the bottom math (wrapped rows).
                h = _height()
                if (L.anchor is not None and h and L.loc_rows
                        and L.anchor >= L.loc_rows + 1 - h - 1):
                    L.follow = True
        VIEW_OPEN.clear(); VIEW_OPEN.update(cur_open)
        L.resized = True


def dispatch_reflow(L, new):
    """Reflow dispatch: startup / resize / toggle -> full reflow (toggle plan
    wins over a plain repaint); otherwise just append-paint the new ops."""
    if L.resized:                        # startup / resize / toggle -> reflow
        L.resized = False
        if L.toggled:
            res = {}
            if L.t_idx is not None:
                # anchor None (capture failed / no match) degrades to the
                # clicked-line-at-top frame: j0 = the line's own offset.
                res = toggle_repaint(
                    L.toggled, L.anchor if L.anchor is not None else L.t_idx,
                    follow=L.follow)
            else:
                repaint()
            # The one row that makes "the view jumped" diagnosable: what
            # the plan saw, what the scroll did, and where the viewport
            # VERIFIABLY landed (plus whether the DSR handshake made it,
            # whether the restore had to be retried, and what the top of
            # the pre-toggle capture actually said).
            try:
                A.state_file(LOG, St.db_path(LOG), "view-reflow",
                             dict({"gid": L.toggled, "idx": L.t_idx,
                                   "anchor": L.anchor,
                                   "cap0": L.last_cap0}, **res))
            except Exception:
                pass
            L.toggled, L.t_idx, L.anchor, L.follow = None, None, None, False
            # Arm the post-toggle DRIFT WATCH: a toggle can verify its
            # landing and the pane still end up somewhere else moments
            # later ("transported to the top", observed live, with zero
            # audit rows in between). For the next few seconds every
            # viewport movement is recorded with its offset and timing —
            # a user wheel-scroll shows as gradual steps, a bug as one
            # instant leap.
            if res.get("landed") is not None:
                L.watch_until = time.monotonic() + DRIFT_WATCH_S
                L.watch_pos = res["landed"]
                # The guard defends the INTENDED anchor, not the measured
                # landing — momentum in flight DURING the restore corrupts
                # the landing itself, and adopting it as home left the
                # guard defending the wrong place (observed: landed 1176
                # off, guard content).
                L.watch_home = res.get("home", res["landed"])
                L.guard_until = time.monotonic() + SETTLE_GUARD_S
                L.guard_left = GUARD_CORRECTIONS
        elif L.force_paint or (width(), _height()) != L.painted_size:
            L.force_paint = False
            repaint()
        else:
            # A WINCH with an UNCHANGED size and no toggle plan (a stray
            # or duplicate click-nudge). A full repaint here is not just
            # wasted work — its clear-scrollback CLAMPS a scrolled-up
            # viewport to the bottom with no restore (observed live).
            _audit_paint("skip", width(), "")
    elif new:
        paint_new(new)


def drift_watch():
    """Post-toggle drift watch: sample where the viewport actually is and
    record every movement (state_files `view-drift`) until the watch
    expires. locate_viewport with the probe index is ~ms-cheap at this
    cadence (one sample per 200ms tick, for 8s after a toggle).
    SELF-HEAL: a movement in the first ~600ms is the instant-leap bug
    (a verified landing yanked ~1000 rows within one tick, observed
    live at +224ms with no rc actor and no repaint) — scroll it back,
    once per toggle. Deliberate post-click navigation starts later
    (observed at +1100ms) and is never fought."""
    if not L.watch_until:
        return
    now = time.monotonic()
    if now < L.watch_until:
        j = locate_viewport(width(), near=L.watch_pos)
        if j is not None:
            # SETTLE GUARD: for the first ~700ms after a verified
            # landing the pane's position belongs to the TOGGLE, not
            # to input — the user's residual trackpad MOMENTUM (they
            # flick-scrolled to reach the line, clicked, and the
            # leftover momentum applies on top of the fresh restore)
            # is what every "hide jumped me ~1000 rows" trace shows:
            # a huge displacement within one or two ticks of the
            # landing. Deliberate post-click navigation starts later
            # (observed at +1100ms) and is never fought. Corrections
            # are ABSOLUTE (recomputed against current content) —
            # a relative fix against a still-moving target amplifies.
            corrected = False
            if (now < L.guard_until and L.guard_left > 0
                    and L.watch_home is not None
                    and abs(j - L.watch_home) > GUARD_SLACK_ROWS):
                corrected = restore_to(L.watch_home)
                if corrected:
                    L.guard_left -= 1
            if corrected or (L.watch_pos is not None
                             and j != L.watch_pos):
                try:
                    A.state_file(LOG, St.db_path(LOG), "view-drift",
                                 {"from": L.watch_pos, "to": j,
                                  "left_ms": int((L.watch_until - now)
                                                 * 1000),
                                  "corrected": corrected})
                except Exception:
                    pass
            L.watch_pos = L.watch_home if corrected else j
    else:
        L.watch_until, L.watch_pos = 0.0, None


def wait_tick(L):
    """Wait for the next tick — or an instant SIGWINCH wake (resize, or the
    click handler's post-toggle nudge) via the wakeup pipe. While the
    settle guard is active, tick fast: momentum hits within ~200ms of
    the landing, and the sooner a displacement is caught the smaller
    the visible wobble."""
    try:
        guarding = L.guard_until and time.monotonic() < L.guard_until
        if select.select([L.wake_r], [], [],
                         TICK_GUARD_S if guarding else TICK_S)[0]:
            os.read(L.wake_r, 4096)
    except Exception:
        pass


def main():
    if not LOG:
        return
    # Do NOT clear the ops table: it is the session's history (parked/restored
    # across resume by claude-split.py, fresh for a new session). Reading it from
    # id 0 means TOGGLING the pane off/on re-shows everything that happened — and
    # while off there is no process at all, so no resources are used.
    #
    # The wakeup pipe makes the idle wait interruptible: a signal writes a byte,
    # so the select in wait_tick returns IMMEDIATELY instead of finishing a 200ms
    # sleep (PEP 475 would otherwise resume it). SIGWINCH is both the resize
    # signal and the click-to-view nudge claude-copy.py sends after a toggle (it
    # finds this pid via the `renderer-pid` kv row sync_inode registers) — either
    # way the answer is "reflow now".
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w)
    PS.install_winch(_on_winch)
    tty_setup()

    L.db, L.wake_r = St.db_path(LOG), wake_r
    while True:
        sync_inode(L)
        new = []
        if L.cur_ino is not None:
            new, restart = drain_ops(L)
            if restart:
                continue
            poll_toggles(L)
        dispatch_reflow(L, new)
        drift_watch()
        wait_tick(L)


if __name__ == "__main__":
    PS.run_renderer(main, LOG, A)
