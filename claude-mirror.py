#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-mirror.py MIRROR_LOG [WIDTH]
#
# The command-mirror RENDERER. Runs inside the kitty split pane (launched by
# claude-split.py) and replaces the old `tail -F`. It polls the session's `ops`
# table (the per-session state DB, claude_state — argv[1] is the mirror-log KEY the
# DB path derives from; written by claude_ops producers), renders each op at the
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import paths as P
from core import render as R
from core import state as St

LOG = sys.argv[1] if len(sys.argv) > 1 else ""
FIXED_WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None

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
_resized = True     # paint once at startup (and whenever a SIGWINCH arrives)


def width():
    return R.term_width(FIXED_WIDTH)


fit = R.fit


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
# from the state DB and pipes command/output text to the clipboard (README §
# Copy links). The links are dim and zero-cost to everything else — a label without
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
        return R.render(R.neutralize(op.get("s", "")), w, op.get("ind", "  "))
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


def frame_bytes(w):
    """The full-reflow byte string: clear screen + scrollback, banner, every
    op (with open view blocks expanded) at width `w`."""
    out = ["\033[H\033[2J\033[3J", BANNER, "\n"]    # home, clear screen + scrollback
    for op in OPS:
        for o in expanded(op):
            out.append(render(o, w)); out.append("\n")
    return "".join(out)


def _height():
    try:
        return os.get_terminal_size().lines
    except OSError:
        return None


def repaint():
    global _PAINTED_SIZE
    w = width()
    body = frame_bytes(w)
    try:
        sys.stdout.write(body); sys.stdout.flush()
    except Exception:
        pass
    _PAINTED_SIZE = (w, _height())
    _audit_paint("repaint", w, body)


def _audit_paint(kind, w, body):
    """One audit row per full reflow: what was actually painted (rows/width/
    ops/open-set). This is the ground truth against the toggle math — a
    view-reflow whose `up` disagrees with the painted row count is exactly
    the model-vs-buffer divergence class of bug."""
    try:
        from core import audit as A
        A.state_file(LOG, St.db_path(LOG), "paint",
                     {"kind": kind, "w": w, "rows": body.count("\n"),
                      "ops": len(OPS), "open": len(VIEW_OPEN)})
    except Exception:
        pass


def paint_new(ops):
    w = width()
    out = []
    for op in ops:
        for o in expanded(op):
            out.append(render(o, w)); out.append("\n")
    try:
        sys.stdout.write("".join(out)); sys.stdout.flush()
    except Exception:
        pass


def _on_winch(signum, frame):
    global _resized
    _resized = True


def trim_to_budget(w):
    """Drop the oldest ops until the rendered frame fits ROW_BUDGET rows.
    Like the MAX_OPS trim, this only changes what a FUTURE full repaint
    draws — already-painted lines stay in the terminal's scrollback until
    the next reflow rewrites it (at which point the trimmed rows would have
    fallen off the scrollback ceiling anyway)."""
    total, heights = 1, []
    for op in OPS:
        n = 0
        for o in expanded(op):
            n += render(o, w).count("\n") + 1
        heights.append(n)
        total += n
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
    for i, op in enumerate(OPS):
        for o in expanded(op):
            if idx is None and o is op and op.get("v") == gid:
                pos, idx = i, acc
            acc += render(o, w).count("\n") + 1
    return pos, idx, acc


_TTY_OK = False      # stdin switched to no-echo/non-canonical (DSR handshake usable)
_LAST_CAP0 = None    # first line of locate_viewport's last capture (audit evidence)
_LOC_ROWS = None     # rendered row count from locate_viewport's last successful run
_PAINTED_SIZE = None  # (w, h) of the last full frame — the spurious-WINCH gate
_FORCE_PAINT = True  # next plain repaint must run even at an unchanged size
_WATCH_UNTIL = 0.0   # post-toggle drift watch deadline (monotonic)
_WATCH_POS = None    # last verified viewport offset during the watch
_WATCH_HOME = None   # the toggle's verified landing — the settle-guard target
_GUARD_UNTIL = 0.0   # settle-guard deadline (monotonic) — the pane's position
#                      belongs to the TOGGLE until then, not to input
_GUARD_LEFT = 0      # settle-guard corrections remaining for this toggle


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
    global _LAST_CAP0, _LOC_ROWS

    def _bail(reason, extra=None):
        # EVERY null path leaves evidence when `tag` names a caller that will
        # degrade on it — the 4x anchor:null / jump-to-end incident was only
        # crackable because the no-match path audited; the no-capture path
        # didn't, and stayed invisible.
        if tag:
            try:
                from core import audit as A
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
            import frontends
            txt = frontends.get().get_text(win)
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
    _LAST_CAP0 = cap[0][:60]
    rows = [R.strip_ansi(BANNER).rstrip()]
    for op in OPS:
        for o in expanded(op):
            rows.extend(r.rstrip() for r in
                        R.strip_ansi(render(o, w)).split("\n"))
    _LOC_ROWS = len(rows)
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
    total = 1
    for op in OPS:
        for o in expanded(op):
            total += render(o, w).count("\n") + 1
    try:
        import frontends
        return _restore(frontends.get(), win, total + 1 - h - j0)
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
    global _PAINTED_SIZE
    win = os.environ.get("KITTY_WINDOW_ID")
    w = width()
    body = frame_bytes(w)
    h = _height()
    up = None
    if h is not None:
        total = 1                              # the banner line
        for op in OPS:
            for o in expanded(op):
                total += render(o, w).count("\n") + 1
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
    _PAINTED_SIZE = (w, h)
    _audit_paint("toggle", w, body)
    dsr = await_dsr(1.0)
    applied, landed, retried = False, None, False
    try:
        import frontends
        fe = frontends.get()
        applied = _restore(fe, win, up)
        # Verify-and-retry: where did the viewport actually land? A DSR
        # timeout means the scrolls raced kitty's parse of the frame and
        # clamped against a partial buffer (landed near the top — the
        # "random places" bug). The locate read itself came back AFTER the
        # parse, so one retry from that point is deterministic.
        landed = locate_viewport(w, near=j0)
        if (landed is not None and landed != j0 and up > 0
                and abs(landed - j0) <= 200):
            # CORRECTIVE retry: scroll by the measured error, not by re-running
            # the same absolute restore — a systematic bias (kitty scrolls
            # VISUAL lines, this math counts logical rows, wrapped rows make
            # them differ) reproduces identically on a re-run and never
            # converges (observed live: landed 17 short, retried, still 17
            # short). landed > j0 = viewport below target → scroll UP by the
            # difference; negative goes down over the same wire. The 200-row
            # cap keeps a residual matcher misread from being AMPLIFIED into
            # a giant correction (a misread chased once landed 1476 off).
            retried = True
            try:
                fe.scroll_window_fast(win, landed - j0)
            except Exception:
                pass
            landed = locate_viewport(w, near=j0)
    except Exception:
        try:
            from core import audit as A
            A.error(LOG, "toggle_scroll (view toggle)", {"gid": gid, "up": up})
        except Exception:
            pass
    return {"up": up, "applied": applied, "dsr": dsr,
            "landed": landed, "retried": retried, "follow": follow}


def main():
    global _resized, _FORCE_PAINT, _WATCH_UNTIL, _WATCH_POS, \
        _WATCH_HOME, _GUARD_UNTIL, _GUARD_LEFT
    if not LOG:
        return
    # Do NOT clear the ops table: it is the session's history (parked/restored
    # across resume by claude-split.py, fresh for a new session). Reading it from
    # id 0 means TOGGLING the pane off/on re-shows everything that happened — and
    # while off there is no process at all, so no resources are used.
    #
    # The wakeup pipe makes the idle wait interruptible: a signal writes a byte,
    # so the select below returns IMMEDIATELY instead of finishing a 200ms sleep
    # (PEP 475 would otherwise resume it). SIGWINCH is both the resize signal and
    # the click-to-view nudge claude-copy.py sends after a toggle (it finds this
    # pid via the `renderer-pid` kv row registered below) — either way the answer
    # is "reflow now".
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w)
    signal.signal(signal.SIGWINCH, _on_winch)
    tty_setup()

    db = St.db_path(LOG)
    last, ino, toggled, t_idx, anchor, follow = 0, None, None, None, None, False
    while True:
        # A recreated DB file (new session reusing the key, or a park/restore
        # cycle) leaves the cached connection pointing at the OLD inode — drop it
        # and re-read from the top. A missing DB just means no producer has
        # written yet (or a resume is mid-restore): keep waiting, don't reset.
        try:
            cur_ino = os.stat(db).st_ino
        except OSError:
            cur_ino = None
        if cur_ino is not None and cur_ino != ino:
            if ino is not None:
                stale = St._CONNS.pop(db, None)
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
                last, OPS[:] = 0, []
                VIEW_OPEN.clear(); _VIEW_OPS.clear()
                _resized = _FORCE_PAINT = True
            ino = cur_ino
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

        # Drain any new ops appended to the table.
        new = []
        if cur_ino is not None:
            last, new = St.ops_after(LOG, last)
            if last < 0:                     # table shrank under us — restart
                last, OPS[:] = 0, []
                _resized = _FORCE_PAINT = True
                continue
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

            # Click-to-view toggles: any change to the `view-open` kv set (a
            # claude-copy.py /view click) reflows the whole pane, expanding or
            # collapsing the affected blocks in place. BEFORE flipping the set,
            # find the toggled line's offset and recover the current viewport
            # top (the anchor must match against the PRE-toggle rendered rows —
            # exactly what's on screen right now).
            try:
                cur_open = set(St.kv_get(LOG, "view-open") or [])
            except Exception:
                cur_open = VIEW_OPEN
            if cur_open != VIEW_OPEN:
                # Multiple gids in one delta (fast clicks coalescing into one
                # poll tick) still get the anchored restore — any one of them
                # serves as the plan's gid; the anchor is gid-independent.
                delta = cur_open ^ VIEW_OPEN
                toggled = min(delta) if delta else None
                t_idx = anchor = None
                follow = False
                # Plan UNCONDITIONALLY — in particular do NOT skip when
                # _resized is set: the click handler's own SIGWINCH nudge sets
                # it before this branch ever runs, so gating on it silently
                # disabled the anchor for every nudged toggle (the pane then
                # parked at the bottom — the "scrolls to the very end" bug).
                # A genuine concurrent resize just changes width() under the
                # match, which fails confidence and degrades to the fallback.
                if toggled:
                    # Trim BEFORE measuring: dropping rows after the plan
                    # would shift every index the plan just computed.
                    trim_to_budget(width())
                    _, t_idx, _ = measure(toggled)
                    if t_idx is not None:
                        # near=t_idx: the user clicked a VISIBLE line, so the
                        # true viewport is near it — a tie-breaking prior
                        # among twin matches, NOT a search constraint (the
                        # old windowed search that mistook this for a hard
                        # assumption missed real viewports).
                        anchor = locate_viewport(width(), tag="anchor",
                                                 near=t_idx)
                        # An at-bottom viewport follows the live tail; pinning
                        # it to an absolute offset would silently detach it —
                        # so the restore target becomes the NEW bottom. The
                        # small tolerance absorbs the logical-vs-visual line
                        # bias in the bottom math (wrapped rows).
                        h = _height()
                        if (anchor is not None and h and _LOC_ROWS
                                and anchor >= _LOC_ROWS + 1 - h - 3):
                            follow = True
                VIEW_OPEN.clear(); VIEW_OPEN.update(cur_open)
                _resized = True

        if _resized:                         # startup / resize / toggle -> reflow
            _resized = False
            if toggled:
                res = {}
                if t_idx is not None:
                    # anchor None (capture failed / no match) degrades to the
                    # clicked-line-at-top frame: j0 = the line's own offset.
                    res = toggle_repaint(
                        toggled, anchor if anchor is not None else t_idx,
                        follow=follow)
                else:
                    repaint()
                # The one row that makes "the view jumped" diagnosable: what
                # the plan saw, what the scroll did, and where the viewport
                # VERIFIABLY landed (plus whether the DSR handshake made it,
                # whether the restore had to be retried, and what the top of
                # the pre-toggle capture actually said).
                try:
                    from core import audit as A
                    A.state_file(LOG, St.db_path(LOG), "view-reflow",
                                 dict({"gid": toggled, "idx": t_idx,
                                       "anchor": anchor,
                                       "cap0": _LAST_CAP0}, **res))
                except Exception:
                    pass
                toggled, t_idx, anchor, follow = None, None, None, False
                # Arm the post-toggle DRIFT WATCH: a toggle can verify its
                # landing and the pane still end up somewhere else moments
                # later ("transported to the top", observed live, with zero
                # audit rows in between). For the next few seconds every
                # viewport movement is recorded with its offset and timing —
                # a user wheel-scroll shows as gradual steps, a bug as one
                # instant leap.
                if res.get("landed") is not None:
                    _WATCH_UNTIL = time.monotonic() + 8.0
                    _WATCH_POS = _WATCH_HOME = res["landed"]
                    _GUARD_UNTIL = time.monotonic() + 0.7
                    _GUARD_LEFT = 2
            elif _FORCE_PAINT or (width(), _height()) != _PAINTED_SIZE:
                _FORCE_PAINT = False
                repaint()
            else:
                # A WINCH with an UNCHANGED size and no toggle plan (a stray
                # or duplicate click-nudge). A full repaint here is not just
                # wasted work — its clear-scrollback CLAMPS a scrolled-up
                # viewport to the bottom with no restore (observed live).
                _audit_paint("skip", width(), "")
        elif new:
            paint_new(new)

        # Post-toggle drift watch: sample where the viewport actually is and
        # record every movement (state_files `view-drift`) until the watch
        # expires. locate_viewport with the probe index is ~ms-cheap at this
        # cadence (one sample per 200ms tick, for 8s after a toggle).
        # SELF-HEAL: a movement in the first ~600ms is the instant-leap bug
        # (a verified landing yanked ~1000 rows within one tick, observed
        # live at +224ms with no rc actor and no repaint) — scroll it back,
        # once per toggle. Deliberate post-click navigation starts later
        # (observed at +1100ms) and is never fought.
        if _WATCH_UNTIL:
            now = time.monotonic()
            if now < _WATCH_UNTIL:
                j = locate_viewport(width(), near=_WATCH_POS)
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
                    if (now < _GUARD_UNTIL and _GUARD_LEFT > 0
                            and _WATCH_HOME is not None
                            and abs(j - _WATCH_HOME) > 30):
                        corrected = restore_to(_WATCH_HOME)
                        if corrected:
                            _GUARD_LEFT -= 1
                    if corrected or (_WATCH_POS is not None
                                     and j != _WATCH_POS):
                        try:
                            from core import audit as A
                            A.state_file(LOG, St.db_path(LOG), "view-drift",
                                         {"from": _WATCH_POS, "to": j,
                                          "left_ms": int((_WATCH_UNTIL - now)
                                                         * 1000),
                                          "corrected": corrected})
                        except Exception:
                            pass
                    _WATCH_POS = _WATCH_HOME if corrected else j
            else:
                _WATCH_UNTIL, _WATCH_POS = 0.0, None

        # Wait for the next tick — or an instant SIGWINCH wake (resize, or the
        # click handler's post-toggle nudge) via the wakeup pipe.
        try:
            if select.select([wake_r], [], [], 0.2)[0]:
                os.read(wake_r, 4096)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        try:
            from core import audit
            audit.error(LOG, "main (renderer crashed)")
        except Exception:
            pass
        raise
