#!/usr/bin/env python3
# claude-mirror.py MIRROR_LOG [WIDTH]
#
# The command-mirror RENDERER. Runs inside the kitty split pane (launched by
# claude-mirror.sh) and replaces the old `tail -F`. It reads the structured paint-op
# log (JSONL, written by claude_ops producers), renders each op at the pane's CURRENT
# width, and — the whole point of this design — RE-RENDERS EVERYTHING on resize so the
# content reflows. Resizing the pane changes its pty size, which delivers SIGWINCH
# here; we recompute the width, clear the screen, and repaint every op.
#
# Width is read live from the pane itself (os.get_terminal_size), so producers never
# need to know it — they only emit width-independent ops. A literal WIDTH argv is
# accepted for non-tty testing.
import json, os, signal, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_render as R

LOG = sys.argv[1] if len(sys.argv) > 1 else ""
FIXED_WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None

BANNER = "\033[38;5;244m ◧ command mirror — waiting for commands… \033[0m"

OPS = []            # every parsed op, so a resize can repaint without re-reading
_resized = True     # paint once at startup (and whenever a SIGWINCH arrives)


def width():
    if FIXED_WIDTH:
        return max(16, FIXED_WIDTH)
    try:
        return max(16, os.get_terminal_size(sys.stdout.fileno()).columns)
    except Exception:
        return 53


def fit(s, avail):
    return s if len(s) <= avail else (s[:avail - 1] + "…" if avail > 1 else s[:avail])


def render(op, w):
    """One paint op -> ANSI text for the current width (may contain newlines)."""
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
    # Start fresh each time the pane opens (the old tailer truncated the log too).
    try:
        open(LOG, "w").close()
    except Exception:
        pass
    signal.signal(signal.SIGWINCH, _on_winch)

    pos, pending = 0, b""
    while True:
        # Drain any new ops appended to the log.
        new = []
        try:
            size = os.path.getsize(LOG)
        except OSError:
            size = 0
        if size < pos:                       # log was truncated/rotated — restart
            pos, pending, OPS[:] = 0, b"", []
            _resized = True
        if size > pos:
            try:
                with open(LOG, "rb") as fh:
                    fh.seek(pos); pending += fh.read(); pos = size
            except OSError:
                pass
            *lines, pending = pending.split(b"\n")
            for ln in lines:
                s = ln.decode("utf-8", "replace").strip()
                if not s:
                    continue
                try:
                    op = json.loads(s)
                except Exception:
                    continue
                OPS.append(op); new.append(op)

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
