#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-scorebar.py MIRROR_LOG [WIDTH]
#
# The scoreboard renderer. Runs inside a SMALL DEDICATED kitty window (5 rows, hsplit
# under the mirror pane — opened by claude-split.py alongside the mirror) and paints
# an always-on session id, a team-message census, the session summary, and a token
# breakdown:
#
#   ⬡ 95466f49-240b-4b69-92b4-96bd1541a9a9
#   ✉ 5 msgs · 3● unread · 2◉ read
#   ▪ 45 cmds (5✗) · ⏱ 68m24s
#   Σ 56M total · 428k in · 197k out · 55M cache · 410k write · ≈ $1.20
#     56 files · +791 -29 · Read 34 · Edit 18 · Write 4
#
# The ▪ row is just activity (commands + active time). The Σ row is all token
# counts plus the `≈ $` cost (spend derives from tokens, so it goes last). The last
# row carries every file/line/tool figure: unique files · ± line-diff · tool tallies.
# The Σ total is the all-in count INCLUDING cache-read replay, so it reconciles with
# `claude --resume`'s "Usage by model" (see O.token_parts). Its breakdown is
# input · output · cache read · cache write; the total dwarfs billed spend because
# cache read is the bulk on a long session.
#
# The ✉ row is tracked by claude_msgs.update_messages (stateful inbox polling → a persisted
# sidecar) and always shows a count (0 included). See claude_ops.py.
#
# A separate window — not lines pinned inside the mirror — because that's the only
# thing that survives SCROLLING: anything drawn in the mirror's own screen scrolls
# away with its history, and a DECSTBM scroll region would keep it pinned only by
# discarding scrolled lines instead of pushing them to scrollback. This window never
# scrolls (it repaints two rows in place), so the scoreboard is simply always there.
#
# Data comes from the per-session state DB (<log>.state.db — claude_state) that
# every producer bumps atomically (claude_ops.bump); we re-read it when its change
# counter moves and repaint once a second regardless so the ⏱ duration ticks. The ⏱
# counts ACTIVE time: it pauses while the tab is green (awaiting-response — your
# turn) and resumes otherwise — see the pause-accounting block below. Reads are
# plain SELECTs (WAL — never block the producers). Exits when the mirror log
# disappears (SessionEnd removes it), which auto-closes the window;
# claude-split.py close is the safety net.
import os, re, signal, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import frontends
from core import ops as O
from core import paths as P
from core import render as R
from core import state as St
from plugins.claude_code import msgs as MSG

LOG = sys.argv[1] if len(sys.argv) > 1 else ""
FIXED_WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None

# Muted styling — the scoreboard is ambient context, not an event, so no background
# chips: dim separators, slate words, slightly brighter numbers, and colour only
# where it carries meaning (failures/removed red, added green, cost orange).
SLATE = R.fg(*O.SCORE_RGB)          # words: cmds / files / tool names
VAL   = R.fg(171, 178, 191)         # numbers — the part your eye scans for
KIND  = {"fail": R.fg(224, 108, 117), "rem": R.fg(224, 108, 117),
         "add": R.fg(152, 195, 121), "cost": R.fg(209, 154, 102),
         # message-census kinds (✉ row): unread yellow, stale orange, read green
         "unread": R.fg(229, 192, 123), "stale": R.fg(209, 154, 102),
         "read": R.fg(152, 195, 121)}
SEP   = R.DIM + " · " + R.RST
_NUM  = re.compile(r"\d[\d.,]*")


fit = R.fit


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


# Mirror-event colours: a delivered/unread message is yellow, a read one green —
# matching the ●/◉ glyphs the census row uses, so the two surfaces read as one system.
MSG_NEW_RGB  = (229, 192, 123)
MSG_READ_RGB = (152, 195, 121)


def emit_events(events):
    """Surface inbox transitions in the MIRROR itself (not just the census): a chip +
    summary gutter when a message is delivered (unread), a chip when it's read. Ops go
    to the shared mirror log, so they interleave with the command stream."""
    ops = []
    for kind, frm, to, summ in events:
        if kind == "new":
            ops.append(O.label("● " + frm + " → " + to, MSG_NEW_RGB))
            if summ:
                ops.append(O.gut(summ, MSG_NEW_RGB))
        else:                                        # read
            ops.append(O.label("◉ read · " + frm + " → " + to, MSG_READ_RGB))
    if ops:
        O.emit(LOG, *ops)


# --- ⏱ pause accounting -----------------------------------------------------
# The session timer tracks ACTIVE time: it stops while the tab is GREEN
# (awaiting-response — Claude is done, your turn) and resumes on any other
# colour. claude-tab-status.py persists the current colour per Claude window in
# the global tab DB (/tmp/claude-kitty-tab.db, `tab` table — was a
# /tmp/claude-tab-state-<window_id> file); the Claude pane for this session carries the
# kitty user-var claude_session=<sid> (tagged by claude-split.py at SessionStart),
# which is how we map our sid to that window id. Green ticks are accumulated into
# the stats sidecar's 'paused' field (same flock'd bump as every other producer,
# so it survives a scorebar restart/toggle), and scoreboard_parts subtracts it.


def _claude_window():
    """Terminal window id (str) of this session's Claude pane, or None. One
    window-enumeration round-trip — callers cache the result and retry
    sparingly. Goes through the Frontend so a non-kitty terminal only needs
    its own window_for_session."""
    fe = frontends.get()
    if not fe.usable():
        return None
    return fe.window_for_session(session_id())


def _tab_green(win):
    """True when the session's tab currently shows awaiting-response (green).
    Read through claude_state.tab_state — the tab DB's schema is owned by
    claude-tab-status.py, not hardcoded here."""
    return St.tab_state(win) == "awaiting-response"


def session_id():
    """The session id this scorebar belongs to, parsed from the mirror log path
    (/tmp/claude-mirror-<session_id>.log). Falls back to the raw path."""
    return P.sid_from_log(LOG)




def compose(w, mparts):
    """The scoreboard rows for width w, as styled strings: [session-id, messages,
    session-stats, tokens, tools]. Row 0 is the always-on ⬡ session id; row 1 is the
    ✉ message census `mparts` (always shown — defaults to '0 msgs'); row 2 is the ▪
    activity summary (commands + active time); row 3 is the Σ token breakdown
    (input/output/cache/write + an all-in total) with the `≈ $` cost last; row 4 is
    the unique-file count + the ± line-diff + the tool tallies.
    Segments drop from the tail until the plain text fits."""
    st = St.stats(LOG)      # atomic snapshot from the state DB — no torn reads
    now = time.time()

    # Row 0: session id — always visible, truncated to width (dim glyph, brighter id).
    line_sid = R.DIM + " ⬡ " + R.RST + VAL + fit(session_id(), max(1, w - 3)) + R.RST

    # Row 1: message census — never blank; default to a 0 count when there's nothing.
    if not mparts:
        mparts = [("msgs", "0 msgs")]
    avail = w - 3                                    # " ✉ " prefix
    while len(mparts) > 1 and sum(len(t) for _, t in mparts) + 3 * (len(mparts) - 1) > avail:
        mparts.pop()
    line_msg = R.DIM + " ✉ " + R.RST + SEP.join(style(k, t) for k, t in mparts)

    if not isinstance(st, dict):
        return [line_sid, line_msg, R.DIM + " ▪ session" + R.RST, "", ""]
    parts, tools = O.scoreboard_parts(st, now)

    avail = w - 3                                    # " ▪ " prefix
    while parts and sum(len(t) for _, t in parts) + 3 * (len(parts) - 1) > avail:
        parts.pop()
    row = ""
    for i, (kind, text) in enumerate(parts):
        row += ("" if i == 0 else joiner(parts[i - 1][0], kind)) + style(kind, text)
    line_sess = R.DIM + " ▪ " + R.RST + row if parts else R.DIM + " ▪ session" + R.RST

    # Row 3: Σ token breakdown + cost — total-first so a narrow pane keeps the
    # headline; the `≈ $` cost rides this row now (spend derives from tokens) and
    # goes LAST so tail-drop sheds it before the token breakdown.
    tparts = O.token_parts(st)
    cost = float(st.get("cost") or 0)
    if cost > 0:
        tparts.append(("cost", "≈ " + O.fmt_usd(cost)))
    avail = w - 3                                    # " Σ " prefix
    while len(tparts) > 1 and sum(len(t) for _, t in tparts) + 3 * (len(tparts) - 1) > avail:
        tparts.pop()
    line_tok = R.DIM + " Σ " + R.RST + SEP.join(style(k, t) for k, t in tparts) \
            if tparts else ""

    # Row 4: file/line stats + tool tallies. The unique-file count and the ± line-diff
    # lead (relocated here from the ▪ row so every file/tool figure sits together),
    # then the top tool tallies. `files` is a UNIQUE-path set; the tool counts are
    # operations — so "5 files · Edit 18" reads as 18 edits across 5 distinct files.
    # The leads are kept when the row must drop segments to fit (tools pop first).
    files = int(st.get("files") or 0)
    add, rem = int(st.get("added") or 0), int(st.get("removed") or 0)
    lead = []                                        # (plain, styled) — priority segs
    if files:
        t = f"{files} file" + ("s" if files != 1 else "")
        lead.append((t, style("files", t)))
    if add and rem:                                  # "+791 -29" clings (one segment)
        lead.append((f"+{add} -{rem}",
                     style("add", f"+{add}") + " " + style("rem", f"-{rem}")))
    elif add:
        lead.append((f"+{add}", style("add", f"+{add}")))
    elif rem:
        lead.append((f"-{rem}", style("rem", f"-{rem}")))
    avail = w - 3                                    # aligned under the parts
    for t, _ in lead:
        avail -= len(t) + 3                          # + its separator
    while tools and sum(len(f"{k} {v}") for k, v in tools) + 3 * (len(tools) - 1) > avail:
        tools.pop()
    segs = [s for _, s in lead] \
            + [SLATE + k + " " + VAL + str(v) + R.RST for k, v in tools]
    line_tools = "   " + SEP.join(segs) if segs else ""
    return [line_sid, line_msg, line_sess, line_tok, line_tools]


def width():
    return R.term_width(FIXED_WIDTH)


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
    win, win_retry, prev_ts, pend = None, 0.0, None, 0.0
    while True:
        if not os.path.exists(St.db_path(LOG)):   # SessionEnd parked the state DB -> window closes
            return
        now = time.time()
        # ⏱ pause accounting: while the tab is green, fold the elapsed tick into
        # the sidecar's 'paused' total (flushed ~1s so the display freezes cleanly).
        # Only once the sidecar exists — pausing shouldn't create an empty session.
        if win is None and now >= win_retry:
            win, win_retry = _claude_window(), now + 5.0   # tag lands async at start
        if win and _tab_green(win) and prev_ts is not None and St.stats(LOG).get("start"):
            pend += now - prev_ts
            if pend >= 1.0:
                O.bump(LOG, paused=round(pend, 2))
                pend = 0.0
        elif pend:                          # left green -> flush the remainder
            O.bump(LOG, paused=round(pend, 2))
            pend = 0.0
        prev_ts = now
        mt = St.version(LOG)                # state-DB change counter (was file mtime)
        if _winch or mt != mt_seen or now - last >= 1.0:   # 1s floor keeps ⏱ ticking
            _winch, mt_seen, last = False, mt, now
            try:                            # poll inboxes: census parts + mirror events
                mparts, events = MSG.update_messages(LOG)
            except Exception:
                # Audited: a crashing tracker otherwise freezes the ✉ row at
                # stale/0 counts with zero trace in the audit DB.
                O.A.error(LOG, "update_messages (✉ row frozen this tick)")
                mparts, events = [], []
            if events:
                emit_events(events)         # surface arrivals/reads in the mirror pane
            lines = compose(width(), mparts)
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
    except Exception:
        try:
            from core import audit
            audit.error(LOG, "main (renderer crashed)")
        except Exception:
            pass
        raise
