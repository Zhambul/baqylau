#!/usr/bin/env python3
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
import json, os, signal, subprocess, sys, time


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
import claude_render as R
import claude_state as St

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
    if FIXED_WIDTH:
        return max(16, FIXED_WIDTH)
    try:
        return max(16, os.get_terminal_size(sys.stdout.fileno()).columns)
    except Exception:
        return 53


def fit(s, avail):
    if R.dwidth(s) <= avail:
        return s
    if avail > 1:
        return R.dsplit(s, avail - 1)[0] + "…"
    return R.dsplit(s, max(0, avail))[0]


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


def _render(op, w):
    t = op.get("t")
    if t == "blank":
        return ""
    if t == "rule":
        return R.rule(w)
    if t == "label":
        outer = op.get("outer")
        avail = w - 2 - (2 if outer else 0)            # chip eats 2 cols; outer bar 2 more
        chip = R.label(fit(op.get("s", ""), max(1, avail)), op["c"])
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
        return R.wrap_gutter(op.get("s", ""), w, gprefix, gw)
    if t == "line":
        return op.get("s", "")
    return ""


def repaint():
    w = width()
    out = ["\033[H\033[2J\033[3J", BANNER, "\n"]    # home, clear screen + scrollback
    for op in OPS:
        out.append(render(op, w)); out.append("\n")
    try:
        sys.stdout.write("".join(out)); sys.stdout.flush()
    except Exception:
        pass


def paint_new(ops):
    w = width()
    out = []
    for op in ops:
        out.append(render(op, w)); out.append("\n")
    try:
        sys.stdout.write("".join(out)); sys.stdout.flush()
    except Exception:
        pass


def _on_winch(signum, frame):
    global _resized
    _resized = True


def main():
    global _resized
    if not LOG:
        return
    # Do NOT clear the ops table: it is the session's history (parked/restored
    # across resume by claude-split.py, fresh for a new session). Reading it from
    # id 0 means TOGGLING the pane off/on re-shows everything that happened — and
    # while off there is no process at all, so no resources are used.
    signal.signal(signal.SIGWINCH, _on_winch)

    db = St.db_path(LOG)
    last, ino = 0, None
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
                St._CONNS.pop(db, None)
                last, OPS[:] = 0, []
                _resized = True
            ino = cur_ino

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

        if _resized:                         # startup or a resize -> full reflow
            _resized = False
            repaint()
        elif new:
            paint_new(new)

        time.sleep(0.2)                      # SIGWINCH interrupts the sleep early


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        try:
            import claude_audit
            claude_audit.error(LOG, "main (renderer crashed)")
        except Exception:
            pass
        raise
