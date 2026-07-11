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
import os, select, signal, subprocess, sys


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
        chip = R.label(fit(op.get("s", ""), max(1, avail)), op["c"]) + links
        if outer:
            return R.fg(*outer) + "│ " + R.RST + chip
        return chip
    if t == "code":
        return R.render(op.get("s", ""), w, op.get("ind", "  "))
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
        return R.wrap_gutter(s, w, gprefix, gw, bg=op.get("bg"))
    if t == "line":
        return op.get("s", "")
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


def repaint():
    w = width()
    out = ["\033[H\033[2J\033[3J", BANNER, "\n"]    # home, clear screen + scrollback
    for op in OPS:
        for o in expanded(op):
            out.append(render(o, w)); out.append("\n")
    try:
        sys.stdout.write("".join(out)); sys.stdout.flush()
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


def tail_row(idx, total_pre):
    """The 1-based screen row of line-offset `idx` when the viewport is at the
    bottom — the toggle FAST PATH precondition. None when the line lives above
    the live screen (the user clicked in scrollback) or there is no pty
    (tests): the caller then takes the full repaint path. `idx`/`total_pre`
    are measure()d BEFORE the open-set flips; the toggled line's own offset is
    the same either way (only content BELOW it changes)."""
    if idx is None:
        return None
    try:
        h = os.get_terminal_size().lines
    except OSError:
        return None
    row = idx + 1 - max(0, total_pre - h)
    return row if row >= 1 else None


def repaint_tail(pos, row):
    """The toggle FAST PATH: the toggled line sits within the bottom screenful
    — and a line is only clickable while visible, so unless the user has
    scrolled up this is every click — so rewrite just from its row down
    (cursor address + clear-below) instead of clearing and rewriting the
    entire scrollback. Instant, scrollback untouched, and wrapped in a DEC
    2026 synchronized update so kitty commits the rewrite as one frame."""
    w = width()
    out = ["\033[?2026h\033[%d;1H\033[J" % row]
    for op in OPS[pos:]:
        for o in expanded(op):
            out.append(render(o, w)); out.append("\n")
    out.append("\033[?2026l")
    try:
        sys.stdout.write("".join(out)); sys.stdout.flush()
    except Exception:
        pass


def viewport_anchor(idx):
    """The scrollback-click anchor: the viewport's top line as an OFFSET into
    the rendered content, recovered by matching the pane's currently VISIBLE
    text (`kitten @ get-text --extent screen` — verified to return the
    scrolled-to viewport, not the live screen) against the pre-toggle rendered
    rows. The clicked line pins the search: its offset `idx` must be visible,
    so the top is one of rows [idx-h+1, idx]. Everything ABOVE the clicked
    line is unchanged by the toggle, so scrolling back to this offset after
    the reflow restores the user's exact view — the expansion just appears
    under the clicked line. None (fall back to clicked-line-at-top) when
    anchorless, capture fails, or nothing matches confidently."""
    win = os.environ.get("KITTY_WINDOW_ID")
    if not win:
        return None
    try:
        import frontends
        txt = frontends.get().get_text(win)
    except Exception:
        txt = None
        try:
            from core import audit as A
            A.error(LOG, "viewport_anchor (get-text)")
        except Exception:
            pass
    if not txt:
        return None
    cap = [l.rstrip() for l in txt.split("\n")]
    while cap and not cap[-1]:
        cap.pop()
    if not cap:
        return None
    w = width()
    rows = [R.strip_ansi(BANNER).rstrip()]
    for op in OPS:
        for o in expanded(op):
            rows.extend(r.rstrip() for r in
                        R.strip_ansi(render(o, w)).split("\n"))
    lo, hi = max(0, idx - len(cap) + 1), min(idx, max(0, len(rows) - 1))
    best, best_score = None, 0
    for j in range(lo, hi + 1):
        score = sum(1 for a, b in zip(cap, rows[j:j + len(cap)]) if a == b)
        if score > best_score:
            best, best_score = j, score
    if best is None or best_score < max(3, len(cap) // 2):
        return None
    return best


def scroll_to_offset(j, gid):
    """Scroll the pane (viewport currently at the bottom, right after a full
    repaint) so content line-offset `j` is the viewport's top row again."""
    win = os.environ.get("KITTY_WINDOW_ID")
    if not win:
        return
    try:
        h = os.get_terminal_size().lines
    except OSError:
        return
    total = 1
    for op in OPS:
        for o in expanded(op):
            total += render(o, width()).count("\n") + 1
    up = total - h - j
    if up <= 0:
        return
    try:
        import frontends
        frontends.get().scroll_window(win, up)
    except Exception:
        try:
            from core import audit as A
            A.error(LOG, "scroll_to_offset (view toggle)", {"gid": gid, "up": up})
        except Exception:
            pass


def scroll_to(gid):
    """A view toggle just reflowed the pane, which parks the viewport at the
    bottom — if the user had scrolled up to click an OLD file-op line, their
    place is gone. Restore it: compute the toggled line's offset in the freshly
    painted stream (render heights are cached, this is cheap) and scroll the
    pane so that line sits at the top of the viewport. When the line is already
    within the bottom screenful (the common just-ran-op case) this is a no-op,
    so a user sitting at the bottom stays there. Best-effort via the frontend's
    scroll_window (kitten @ scroll-window N-); silently skipped when anchorless
    (no KITTY_WINDOW_ID — e.g. the test harness), audited on failure."""
    win = os.environ.get("KITTY_WINDOW_ID")
    if not win:
        return
    w = width()
    try:
        h = os.get_terminal_size().lines
    except OSError:
        return
    acc, idx = 1, None                       # 1: the banner line repaint() leads with
    for op in OPS:
        for o in expanded(op):
            if idx is None and o is op and op.get("v") == gid:
                idx = acc
            acc += render(o, w).count("\n") + 1
    if idx is None:
        return
    up = acc - h - idx
    if up <= 0:
        return                               # already visible at the bottom
    try:
        import frontends
        frontends.get().scroll_window(win, up)
    except Exception:
        try:
            from core import audit as A
            A.error(LOG, "scroll_to (view toggle)", {"gid": gid, "up": up})
        except Exception:
            pass


def main():
    global _resized
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

    db = St.db_path(LOG)
    last, ino, toggled, anchor = 0, None, None, None
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
                _resized = True
            ino = cur_ino
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
                _resized = True
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

            # Click-to-view toggles: any change to the `view-open` kv set (a
            # claude-copy.py /view click) reflows the whole pane, expanding or
            # collapsing the affected blocks in place. Remember the toggled id
            # so the post-repaint scroll can bring it back into view.
            try:
                cur_open = set(St.kv_get(LOG, "view-open") or [])
            except Exception:
                cur_open = VIEW_OPEN
            if cur_open != VIEW_OPEN:
                delta = cur_open ^ VIEW_OPEN
                toggled = delta.pop() if len(delta) == 1 else None
                # Plan the paint BEFORE flipping the set: the fast-path check
                # needs the pre-toggle total (what's on screen right now), and
                # the scrollback-click anchor must capture/match the viewport
                # against the PRE-toggle rendered rows.
                tail = None
                if toggled and not _resized:
                    pos, idx, total = measure(toggled)
                    row = tail_row(idx, total)
                    if row is not None:
                        tail = (pos, row)
                    else:
                        anchor = viewport_anchor(idx)
                VIEW_OPEN.clear(); VIEW_OPEN.update(cur_open)
                if tail is not None:
                    repaint_tail(*tail)
                    scroll_to(toggled)       # only scrolls if it grew off-screen
                    toggled = None           # painted — no full reflow needed
                else:
                    _resized = True

        if _resized:                         # startup / resize / toggle -> reflow
            _resized = False
            # For a toggle reflow, freeze rendering (DEC 2026 synchronized
            # update) across repaint + scroll restore so the user never sees
            # the intermediate viewport-at-bottom frame — the screen goes
            # straight from the old view to the same view with the block
            # expanded/collapsed. kitty's sync timeout un-freezes on its own
            # if the scroll RPC stalls.
            sync = toggled is not None
            if sync:
                try:
                    sys.stdout.write("\033[?2026h")
                except Exception:
                    pass
            repaint()
            if toggled:
                if anchor is not None:
                    scroll_to_offset(anchor, toggled)   # exact restore
                else:
                    scroll_to(toggled)                  # fallback: line at top
                toggled, anchor = None, None
            if sync:
                try:
                    sys.stdout.write("\033[?2026l"); sys.stdout.flush()
                except Exception:
                    pass
        elif new:
            paint_new(new)

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
