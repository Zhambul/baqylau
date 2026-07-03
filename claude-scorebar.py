#!/usr/bin/env python3
# claude-scorebar.py MIRROR_LOG [WIDTH]
#
# The session-scoreboard renderer. Runs inside a SMALL DEDICATED kitty window
# (2 rows, hsplit under the mirror pane — opened by claude-split.sh alongside the
# mirror) and paints a running "so far" summary of the session:
#
#   ▪ 45 cmds (5✗) · 56 files · +791 -29 · 1.2M tok · ⏱ 68m24s · ≈ $1.20
#     Read 34 · Edit 18 · Write 4
#
# A separate window — not lines pinned inside the mirror — because that's the only
# thing that survives SCROLLING: anything drawn in the mirror's own screen scrolls
# away with its history, and a DECSTBM scroll region would keep it pinned only by
# discarding scrolled lines instead of pushing them to scrollback. This window never
# scrolls (it repaints two rows in place), so the scoreboard is simply always there.
#
# Data comes from the stats sidecar (<log>.stats.json) that every producer bumps
# under an flock (claude_ops.bump); we re-read it on mtime change and repaint once a
# second regardless so the ⏱ duration ticks. Reads skip the flock — a torn read just
# fails to parse and keeps the previous paint for one tick, which beats making hook
# processes wait on a renderer. Exits when the mirror log disappears (SessionEnd
# removes it), which auto-closes the window; claude-split.sh close is the safety net.
import json, os, re, signal, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_render as R
import claude_ops as O

LOG = sys.argv[1] if len(sys.argv) > 1 else ""
FIXED_WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
STATS = O.stats_path(LOG) if LOG else ""

# Muted styling — the scoreboard is ambient context, not an event, so no background
# chips: dim separators, slate words, slightly brighter numbers, and colour only
# where it carries meaning (failures/removed red, added green, cost orange).
SLATE = R.fg(*O.SCORE_RGB)          # words: cmds / files / tool names
VAL   = R.fg(171, 178, 191)         # numbers — the part your eye scans for
KIND  = {"fail": R.fg(224, 108, 117), "rem": R.fg(224, 108, 117),
         "add": R.fg(152, 195, 121), "cost": R.fg(209, 154, 102)}
SEP   = R.DIM + " · " + R.RST
_NUM  = re.compile(r"\d[\d.,]*")


def style(kind, text):
    c = KIND.get(kind)
    if c:
        return c + text + R.RST
    # neutral segments: brighten the digits, mute the words ("45 cmds", "⏱ 68m24s")
    return SLATE + _NUM.sub(lambda m: VAL + m.group(0) + SLATE, text) + R.RST


def joiner(prev_kind, kind):
    # "(5✗)" clings to its count, "-29" to its "+791" — a dot between them would
    # read as separate facts when they're one.
    return " " if kind == "fail" or (kind == "rem" and prev_kind == "add") else SEP


def compose(w):
    """The two scoreboard rows for width w, as styled strings. Segments drop from the
    tail (cost first — it's last) until the plain text fits the row."""
    try:
        with open(STATS, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = None
    if not isinstance(st, dict):
        return [R.DIM + " ▪ session" + R.RST, ""]
    parts, tools = O.scoreboard_parts(st, time.time())

    avail = w - 3                                    # " ▪ " prefix
    while parts and sum(len(t) for _, t in parts) + 3 * (len(parts) - 1) > avail:
        parts.pop()
    row = ""
    for i, (kind, text) in enumerate(parts):
        row += ("" if i == 0 else joiner(parts[i - 1][0], kind)) + style(kind, text)
    line1 = R.DIM + " ▪ " + R.RST + row if parts else R.DIM + " ▪ session" + R.RST

    avail = w - 3                                    # aligned under the parts
    while tools and sum(len(f"{k} {v}") for k, v in tools) + 3 * (len(tools) - 1) > avail:
        tools.pop()
    line2 = "   " + SEP.join(SLATE + k + " " + VAL + str(v) + R.RST for k, v in tools) \
            if tools else ""
    return [line1, line2]


def width():
    if FIXED_WIDTH:
        return max(16, FIXED_WIDTH)
    try:
        return max(16, os.get_terminal_size(sys.stdout.fileno()).columns)
    except Exception:
        return 53


_winch = False


def _on_winch(signum, frame):
    global _winch
    _winch = True


def main():
    global _winch
    if not LOG:
        return
    signal.signal(signal.SIGWINCH, _on_winch)
    last, mt_seen = 0.0, None
    while True:
        if not os.path.exists(LOG):        # SessionEnd removed the log -> window closes
            return
        try:
            mt = os.path.getmtime(STATS)
        except OSError:
            mt = None
        now = time.time()
        if _winch or mt != mt_seen or now - last >= 1.0:   # 1s floor keeps ⏱ ticking
            _winch, mt_seen, last = False, mt, now
            lines = compose(width())
            try:                            # hide cursor; repaint both rows in place
                sys.stdout.write("\033[?25l\033[H\033[2J" + "\n".join(lines))
                sys.stdout.flush()
            except Exception:
                pass
        time.sleep(0.25)                    # SIGWINCH interrupts the sleep early


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
