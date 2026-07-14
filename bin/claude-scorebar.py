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
# The ▪ row is just activity (commands + active time) — plus, only when the
# session has swallowed exceptions, a leading AMBER `⚠ N` audit warning-light
# chip (core/errwatch.py, polled from the global audit DB at EW.POLL_S and
# memoized between ticks; the same poll emits `⚠ audit: …` one-liners into the
# mirror for each NEW error row). The Σ row is all token
# counts plus the `≈ $` cost (spend derives from tokens, so it goes last). The last
# row carries every file/line/tool figure: unique files · ± line-diff · tool tallies.
# The Σ total is the all-in count INCLUDING cache-read replay, so it reconciles with
# `claude --resume`'s "Usage by model" (see O.token_parts). Its breakdown is
# input · output · cache read · cache write; the total dwarfs billed spend because
# cache read is the bulk on a long session.
#
# The ✉ row is fed by the plugins registry's census fan-out (today: the
# claude_code plugin's msgs.update_messages — stateful inbox polling) and
# always shows a count (0 included).
#
# A separate window — not lines pinned inside the mirror — because that's the only
# thing that survives SCROLLING: anything drawn in the mirror's own screen scrolls
# away with its history, and a DECSTBM scroll region would keep it pinned only by
# discarding scrolled lines instead of pushing them to scrollback. This window never
# scrolls (it repaints two rows in place), so the scoreboard is simply always there.
#
# Data comes from the per-session state DB (<log>.state.db — core.state) that
# every producer bumps atomically (core.ops.bump); we re-read it when its change
# counter moves and repaint once a second regardless so the ⏱ duration ticks. The ⏱
# counts ACTIVE time: it pauses while the tab is green (awaiting-response — your
# turn) and resumes otherwise — see the pause-accounting block below. Reads are
# plain SELECTs (WAL — never block the producers). Exits when the mirror log
# disappears (SessionEnd removes it), which auto-closes the window;
# claude-split.py close is the safety net.
import os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
import frontends
from core import errwatch as EW
from core import ops as O
from core import panescript as PS
from core import paths as P
from core import render as R
from core import state as St
from core.noaudit import load_audit
import plugins as PLUGINS

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

LOG, FIXED_WIDTH = PS.parse_argv()

# Muted styling — the scoreboard is ambient context, not an event, so no background
# chips: dim separators, slate words, slightly brighter numbers, and colour only
# where it carries meaning (failures/removed red, added green, cost orange).
SLATE = R.fg(*O.SCORE_RGB)          # words: cmds / files / tool names
VAL   = R.fg(171, 178, 191)         # numbers — the part your eye scans for
KIND  = {"fail": R.fg(*O.RED), "rem": R.fg(*O.RED),
         "add": R.fg(*O.GREEN), "cost": R.fg(*O.ORANGE),
         # message-census kinds (✉ row): unread yellow, stale orange, read green
         "unread": R.fg(*O.YELLOW), "stale": R.fg(*O.ORANGE),
         "read": R.fg(*O.GREEN),
         # audit warning light (▪ row ⚠ chip): amber — a degradation warning,
         # deliberately distinct from the row's red ✗ command failures
         "warn": R.fg(*O.AMBER)}
SEP   = R.DIM + " · " + R.RST
_NUM  = re.compile(r"\d[\d.,]*")

TICK_S   = 0.25  # main tick cadence: poll the state DB / inboxes 4x/s (SIGWINCH wakes early)

SEP_W    = 3     # plain-text width of the " · " joiner between segments
PREFIX_W = 3     # every row's 3-column glyph prefix (" ⬡ " / " ✉ " / " ▪ " / " Σ ")


def fit_parts(parts, avail, min_keep=1, text=lambda p: p[1]):
    """Tail-drop shrink-to-fit: pop segments off the END of `parts` (in place)
    until the plain text — `text(part)` per segment, SEP_W between segments —
    fits in `avail` columns, but never below `min_keep` segments. Returns
    `parts` for convenience."""
    while len(parts) > min_keep and \
            sum(len(text(p)) for p in parts) + SEP_W * (len(parts) - 1) > avail:
        parts.pop()
    return parts


fit = PS.fit


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
MSG_NEW_RGB  = O.YELLOW
MSG_READ_RGB = O.GREEN


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
    Read through core.state.tab_state — the tab DB's schema is owned by
    claude-tab-status.py, not hardcoded here."""
    return St.tab_state(win) == "awaiting-response"


def session_id():
    """The session id this scorebar belongs to, parsed from the mirror log path
    (/tmp/claude-mirror-<session_id>.log). Falls back to the raw path."""
    return P.sid_from_log(LOG)




def compose(w, mparts, st, nerr=0):
    """The scoreboard rows for width w, as styled strings: [session-id, messages,
    session-stats, tokens, tools]. Row 0 is the always-on ⬡ session id; row 1 is the
    ✉ message census `mparts` (always shown — defaults to '0 msgs'); row 2 is the ▪
    activity summary (commands + active time); row 3 is the Σ token breakdown
    (input/output/cache/write + an all-in total) with the `≈ $` cost last; row 4 is
    the unique-file count + the ± line-diff + the tool tallies.
    Segments drop from the tail until the plain text fits.
    `st` is the caller's ONE St.stats(LOG) snapshot for this tick — atomic from
    the state DB, read once and shared, so all five rows agree (no torn reads)."""
    now = time.time()

    # Row 0: session id — always visible, truncated to width (dim glyph, brighter id).
    line_sid = R.DIM + " ⬡ " + R.RST + VAL + fit(session_id(), max(1, w - 3)) + R.RST

    # Row 1: message census — never blank; default to a 0 count when there's nothing.
    if not mparts:
        mparts = [("msgs", "0 msgs")]
    fit_parts(mparts, w - PREFIX_W)                  # " ✉ " prefix; keep >= 1
    line_msg = R.DIM + " ✉ " + R.RST + SEP.join(style(k, t) for k, t in mparts)

    if not isinstance(st, dict):
        st = {}
    parts, tools = O.scoreboard_parts(st, now)

    # ⚠ audit warning light — the session's swallowed-error count (core.errwatch,
    # polled at its own slow cadence and memoized by main()). Only when > 0, and
    # FIRST on the ▪ row so a narrow pane's tail-drop never sheds the warning.
    if nerr:
        parts.insert(0, EW.chip_part(nerr))

    fit_parts(parts, w - PREFIX_W, min_keep=0)       # " ▪ " prefix; may empty out
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
    fit_parts(tparts, w - PREFIX_W)                  # " Σ " prefix; keep the total
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
    avail = w - PREFIX_W                             # aligned under the parts
    for t, _ in lead:
        avail -= len(t) + SEP_W                      # + its separator
    fit_parts(tools, avail, min_keep=0,              # leads kept; tools pop first
              text=lambda kv: f"{kv[0]} {kv[1]}")
    segs = [s for _, s in lead] \
            + [SLATE + k + " " + VAL + str(v) + R.RST for k, v in tools]
    line_tools = "   " + SEP.join(segs) if segs else ""
    return [line_sid, line_msg, line_sess, line_tok, line_tools]


width = PS.make_width(FIXED_WIDTH)

_winch = False


def _on_winch():
    global _winch
    _winch = True


def main():
    global _winch
    if not LOG:
        return
    PS.install_winch(_on_winch)
    last, mt_seen = 0.0, None
    win, win_retry, prev_ts, pend = None, 0.0, None, 0.0
    # ⚠ audit warning light: errwatch.poll runs at its own SLOW cadence
    # (EW.POLL_S — the audit DB is global, don't open it every TICK_S) and the
    # count is memoized between polls; a None poll keeps the last good value.
    nerr, err_next = 0, 0.0
    while True:
        if St.parked(LOG):                        # SessionEnd parked the state DB -> window closes
            return
        now = time.time()
        st = None                           # this tick's ONE St.stats snapshot, read lazily
        # ⏱ pause accounting: while the tab is green, fold the elapsed tick into
        # the sidecar's 'paused' total (flushed ~1s so the display freezes cleanly).
        # Only once the sidecar exists — pausing shouldn't create an empty session.
        if win is None and now >= win_retry:
            win, win_retry = _claude_window(), now + 5.0   # tag lands async at start
        if win and _tab_green(win) and prev_ts is not None:
            st = St.stats(LOG)
        if st is not None and st.get("start"):
            pend += now - prev_ts
            if pend >= 1.0:
                O.bump(LOG, paused=round(pend, 2))
                pend = 0.0
        elif pend:                          # left green -> flush the remainder
            O.bump(LOG, paused=round(pend, 2))
            pend = 0.0
        prev_ts = now
        if now >= err_next:                 # slow poll — chip memoized in between
            err_next = now + EW.POLL_S
            v = EW.poll(LOG, session_id())  # also emits ⚠ mirror blocks for new rows
            if v is not None and v != nerr:
                nerr, last = v, 0.0         # force a repaint (chip changed; the
                                            # state-DB change counter didn't move)
        mt = St.version(LOG)                # state-DB change counter (was file mtime)
        if _winch or mt != mt_seen or now - last >= 1.0:   # 1s floor keeps ⏱ ticking
            _winch, mt_seen, last = False, mt, now
            try:                            # poll inboxes: census parts + mirror events
                mparts, events = PLUGINS.census(LOG)
            except Exception:
                # Audited: a crashing tracker otherwise freezes the ✉ row at
                # stale/0 counts with zero trace in the audit DB.
                A.error(LOG, "update_messages (✉ row frozen this tick)")
                mparts, events = [], []
            if events:
                emit_events(events)         # surface arrivals/reads in the mirror pane
            if st is None:                  # not already read by the pause block
                st = St.stats(LOG)          # atomic snapshot — shared with compose
            lines = compose(width(), mparts, st, nerr)
            try:                            # hide cursor; repaint both rows in place
                sys.stdout.write("\033[?25l\033[H\033[2J" + "\n".join(lines))
                sys.stdout.flush()
            except Exception:
                pass
        time.sleep(TICK_S)                  # SIGWINCH interrupts the sleep early


if __name__ == "__main__":
    PS.run_renderer(main, LOG, A)
