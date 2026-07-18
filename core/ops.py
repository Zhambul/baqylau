# core/ops.py — structured paint-op log + scoreboard counters for the mirror.
# (Historical name: claude_ops.py — the top-level compat aggregator re-exports
# this module together with the claude-code plugin's accounting/tools halves.)
#
# THE REFLOW REFACTOR. Producers (the *-fmt.py hooks + claude-stream.py /
# claude-substream.py) no longer bake the pane width into final ANSI. Instead they
# append width-INDEPENDENT paint ops — rows of the per-session state DB's `ops`
# table (claude_state, keyed by the historical mirror-log path) —
# and claude-mirror.py (running inside the pane) renders them at the CURRENT width,
# re-rendering EVERYTHING on resize (SIGWINCH) so the content reflows. Each op carries
# its colours and already-highlighted / pre-styled text; only the width-dependent
# layout (rule length, gutter wrapping, code + chip wrapping) is deferred to paint.
#
# Op vocabulary (the "t" field):
#   blank                  -> an empty line
#   rule                   -> a full-width divider
#   label  s, c[, outer]   -> a header/summary chip (dark text on colour c), truncated
#                             to width; optional single outer "│ " gutter bar prefix
#   code   s[, ind]        -> a command: syntax-highlighted + word-wrapped to width
#   gut    s, c[, outer][, bg] -> body text behind a "│ " gutter in colour c (double
#                             gutter when outer is given), wrapped so the gutter repeats
#                             on every visual row. s may already contain ANSI (zero-width).
#                             bg=(r,g,b) fills each row to the pane width -> a panel
#                             (markdown code blocks); the fill is width-dependent so the
#                             renderer (wrap_gutter) does it, not the producer
#   line   s               -> a verbatim pre-styled single line (no gutter, no wrap)
#
# A gut op may carry "lex" (a pygments lexer name) and/or "num" (a first line
# number): the RENDERER then highlights/line-numbers the raw "s" at paint time
# (producers may run a python without pygments). A line/gut op may carry "v",
# a click-to-view id: the renderer paints the kv-stashed block `view:<id>`
# inline under the op while the id is in the `view-open` kv set (toggled by
# claude-copy.py — the file-op expansion feature; see file_fmt.py).
#
# Any op may carry "src": the PRODUCER-SOURCE stamp — absent for the main
# session's own activity, else who painted it: "sub:<agent_id>" /
# "team:<agent_id>" (the substream + the tailers it spawns) or "codex:<label>"
# (a secondary-source codex run; a STANDALONE codex host's own rollout is
# unstamped — there codex IS the main agent). Stamped by emit() from the
# ambient per-process source (set_src / $CLAUDE_OPS_SRC) or an explicit
# src= kwarg. The terminal renderer ignores it (it paints everything); the web
# dashboard's mirror is main-agent-only and drops stamped ops at render
# (dashboard/opshtml.op_items — agent detail lives in the drill-down there).
#
# Any label/code/gut op may additionally carry "g": a COPY-GROUP id tying the ops of
# one activity block together (the Bash tool_use_id, the backgroundTaskId for a
# background job, or any synthesised per-block id). A g-tagged label is painted with
# clickable ⧉ OSC 8 hyperlinks (claude-copy:// — resolved by kitty's
# open-actions.conf into claude-copy.py, which re-reads the group's ops and pipes the
# text to the clipboard). WHICH links a header shows is its "lk" spec — a list of
# [what, glyph] pairs (what ∈ {cmd → the code ops, out → the gut ops, all → both});
# absent lk defaults to the command block's " ⧉cmd ⧉out" pair. ⧉cmd copies the code
# op's text as displayed (the pretty-printed form — owner's call: WYSIWYG; it's
# equivalent, runnable bash either way); ⧉out/⧉copy the ANSI-stripped gut text.
import os

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import
from core import state as S             # per-session runtime state (SQLite, /tmp)


def _rgb(c):
    return [int(c[0]), int(c[1]), int(c[2])]


def blank():
    return {"t": "blank"}


def rule():
    return {"t": "rule"}


def label(s, c, outer=None, g=None, lk=None):
    o = {"t": "label", "s": s, "c": _rgb(c)}
    if outer is not None:
        o["outer"] = _rgb(outer)
    if g:
        o["g"] = str(g)
        # lk (link spec): the ⧉ copy affordances to paint on this header, as a list of
        # [what, glyph] pairs — what ∈ {cmd, out, all} (claude-copy.py maps each to the
        # group's code/gut/both ops). Omitted → the renderer's default cmd/out pair, so
        # every existing command block is unchanged. A body-only block (message, prompt,
        # result, file op) passes lk=[["all", "⧉copy"]] for a single whole-block copy.
        if lk:
            o["lk"] = [[w, gl] for w, gl in lk]
    return o


def code(s, ind="  ", g=None):
    # Pretty-print the command once, here at op creation (width-independent — the
    # renderer still wraps the result to the pane). Best-effort and gated by
    # CLAUDE_MIRROR_FORMAT (set to "0" to show commands verbatim). Only producers call
    # code(); the renderer never does, so this never runs in the paint loop.
    if os.environ.get("CLAUDE_MIRROR_FORMAT", "1") != "0":
        try:
            from core import codefmt
            s = codefmt.format_code(s)
        except Exception:
            # Audited: a broken formatter (pygments regression) otherwise paints
            # every command unformatted with nothing in the DB saying why.
            A.error("", "format_code (command shown verbatim)")
    o = {"t": "code", "s": s, "ind": ind}
    if g:
        o["g"] = str(g)
    return o


def gut(s, c, outer=None, g=None, bg=None, lex=None, num=None, view=None):
    o = {"t": "gut", "s": s, "c": _rgb(c)}
    if view:
        # Click-to-view id (see line()) — a subagent's file-op one-liner is a
        # gut op, so gut carries the same expansion tag.
        o["v"] = str(view)
    if outer is not None:
        o["outer"] = _rgb(outer)
    if g:
        o["g"] = str(g)
    if bg is not None:
        # A panel background filled to the pane width at paint time (markdown code
        # blocks) — width-DEPENDENT, so the fill is the renderer's job (wrap_gutter),
        # not baked in here.
        o["bg"] = _rgb(bg)
    if lex:
        # Syntax-highlight `s` with this pygments lexer AT PAINT TIME. Producers
        # (hook processes) may run under a python without pygments — the renderer
        # re-execs into one that has it, which is why code highlighting always
        # belongs there (same reason `code` ops carry raw text). Used by the
        # file-op click-to-view blocks.
        o["lex"] = str(lex)
    if num is not None:
        # Prefix each line of `s` with a dim line number, counting from `num`,
        # at paint time (rides with lex — numbering must wrap AFTER highlight).
        o["num"] = int(num)
    return o


def line(s, view=None):
    # view: a click-to-view group id (the file op's tool_use_id). The renderer
    # paints the kv-stashed block `view:<id>` INLINE under this line while the
    # id is in the session's `view-open` kv set (toggled by claude-copy.py on a
    # /view click); the producer bakes the matching OSC 8 hyperlink into `s`.
    o = {"t": "line", "s": s}
    if view:
        o["v"] = str(view)
    return o


# The single ⧉ copy affordance for a body-only block (message/prompt/result/…): one
# whole-block copy link. Producers pass this as label(..., lk=COPY_ALL) alongside a
# fresh new_group() id (below), and tag the block's gut/code ops with the same id.
COPY_ALL = [["all", "⧉copy"]]


def new_group(log):
    """Session-unique copy-group id for a block that has no natural tool_use_id, so
    its ops can be tied together for the ⧉ copy handler. 'b<n>' or None on failure
    (a falsy id means the producer simply omits the copy affordance)."""
    n = S.next_group(log)
    return ("b%d" % n) if n else None


# --- producer-source stamp (the op "src" field) -------------------------------------
# One ambient value per PROCESS: every detached streamer serves exactly one source
# (a substream serves one agent, a codex stream one run), so the stamp is process
# state, not per-call plumbing. set_src also EXPORTS $CLAUDE_OPS_SRC, and every
# child-tailer launch copies os.environ (hookkit.stream_env, spawn_detached's
# env=None default) — so a subagent's fg/bg/monitor tailers inherit the stamp with
# no per-launcher wiring. Hook processes never see the var (Claude Code's env has
# no reason to carry it), so main-session ops stay unstamped. The explicit
# emit(src=) kwarg covers the one in-hook-process producer of agent ops
# (monitor_fmt: a subagent's monitor header).
_SRC = None
_SRC_INIT = False   # lazy: $CLAUDE_OPS_SRC is read once, at first emit/op_src


def set_src(src):
    """Declare every op this process emits (and every process it spawns with an
    inherited env) as coming from a non-main source. src vocabulary: see the
    "src" op-field comment above. Falsy src clears the ambient stamp in-process
    (the env export is left alone — tests aside, nothing clears a stamp)."""
    global _SRC, _SRC_INIT
    _SRC = str(src) if src else None
    _SRC_INIT = True
    if _SRC:
        os.environ["CLAUDE_OPS_SRC"] = _SRC


def op_src():
    """The ambient producer-source stamp (None = the main session)."""
    if not _SRC_INIT:
        set_src(os.environ.get("CLAUDE_OPS_SRC"))
    return _SRC


def emit(log, *ops, src=None):
    """Append paint ops to the session's `ops` table (core.state, keyed by the
    mirror-log path). One transaction so a block of ops lands contiguously relative
    to other producers inserting concurrently — the atomicity the old single
    O_APPEND write() to the JSONL log provided. Each op is stamped with the
    producer source (explicit `src` kwarg, else the ambient set_src /
    $CLAUDE_OPS_SRC value) BEFORE the append + audit, so both records carry it."""
    if not ops:
        return
    s = src or op_src()
    if s:
        for o in ops:
            if isinstance(o, dict):
                o.setdefault("src", s)
    try:
        if not S.ops_append(log, list(ops)):
            A.error(log, "emit", {"ops": len(ops)})
    except Exception:
        A.error(log, "emit", {"ops": len(ops)})
    A.ops(log, ops)


# --- session statistics (the "▪ session" scoreboard pane) ----------------------
# The scoreboard is a running "so far" summary, aggregated across the SEPARATE, short-
# lived hook processes that produce the mirror (one per Bash call, one per file op, one
# per subagent). They share no memory, so the counters live in the per-session state
# DB keyed to the mirror-log path (core.state — parked at SessionEnd, restored on
# resume). Each producer bumps its deltas atomically; claude-scorebar.py (a small
# dedicated window under the mirror, opened by claude-split.py) renders the
# scoreboard live off the DB's change counter.

SCORE_RGB = (120, 132, 158)   # muted slate-blue — reads as a divider, not an event

# --- shared status colours (RGB) -------------------------------------------------
# One table for the hues that must agree across producers (several used to be
# duplicated per-file with "must match" comments). Palette families for streams
# stay in core.slots; these are the fixed semantic colours.
SLATE  = (170, 185, 210)   # foreground OK (neutral, distinct from the vivid palettes)
ORANGE = (209, 154, 102)   # background header / interrupted
RED    = (224, 108, 117)   # failure / removals
GREEN  = (152, 195, 121)   # success / additions / a written file
YELLOW = (229, 192, 123)   # modification / warning
BLUE   = (97, 175, 239)    # a read
AMBER  = (214, 153, 92)    # a task entering the list


def fmt_dur(sec, decimals=True):
    """Wall-clock duration chip text: '3.2s' / '4m07s' / '7h37m' / '2d07h'
    (negatives clamp to 0). Above an hour only the two largest units show —
    second precision stops mattering at that altitude and the string must
    still fit a ~8-char chip. decimals=False drops the sub-minute fraction
    ('3s') — the scoreboard's ⏱ chip."""
    sec = max(0.0, sec)
    if sec < 60:
        return f"{sec:.1f}s" if decimals else f"{int(sec)}s"
    if sec < 3600:
        return f"{int(sec // 60)}m{int(sec % 60):02d}s"
    if sec < 86400:
        return f"{int(sec // 3600)}h{int(sec % 3600 // 60):02d}m"
    return f"{int(sec // 86400)}d{int(sec % 86400 // 3600):02d}h"


def fmt_usd(c):
    """Compact dollar string: '<$0.01' / '$0.42' / '$12' / '$1.2k'. '' for None."""
    if c is None:
        return ""
    if c < 0.005:
        return "<$0.01"
    if c < 10:
        return f"${c:.2f}"
    if c < 1000:
        return f"${c:.0f}"
    return f"${c / 1000:.1f}k"


def bump(log, tool=None, file=None, meta=None, **deltas):
    """Fold scoreboard deltas into the per-session state DB (core.state) — was a
    flock'd read-modify-write of a JSON sidecar, now atomic SQL increments, so
    concurrent hook processes can't tear or clobber each other. Adds each numeric
    delta to its counter, increments tools[tool], and stamps 'start' (epoch secs) on
    first write. `file` records a touched path (unique-file set) and the scoreboard's
    files figure is its size — re-editing the same file doesn't inflate it. Returns
    the updated stats dict — {} on any failure (stats are best-effort, never fatal
    to a hook).

    `meta` attributes an AGENT streamer's spend bump: agent_id/kind/model plus the
    in/out/cache/create split that cost_usd priced. Those rows are audited under the
    distinct action 'bump-agent', so "which agent inflated the scoreboard" is a
    column, not a timestamp correlation against `streams`, and the cost math is
    re-derivable from the DB alone (a wrong PRICES entry shows as right tokens /
    wrong dollars). A token/cost delta with NO meta on a current build is itself an
    anomaly — an unattributed producer (see the `anomalies` canned query)."""
    st = S.incr(log, tool=tool, file=file, **deltas)
    if st:
        # Audit the state evolution: the applied deltas + the resulting headline
        # totals, so a wrong scoreboard number can be traced to the exact bump that
        # skewed it (double count, missed count) instead of manual state digging.
        # Exception: the scorebar's paused-only ticks (one per second while the tab
        # is green) — pure noise that buried real bumps; their running total rides
        # in every other bump row's `now`.
        if set(deltas) == {"paused"}:
            return st
        content = {"deltas": deltas, "tool": tool, "file": file,
                   "now": {k: st.get(k) for k in
                           ("commands", "failed", "files", "added", "removed",
                            "tokens", "cost") if st.get(k)}}
        if meta:
            content["meta"] = meta
        A.state_file(log, S.db_path(log), "bump-agent" if meta else "bump", content)
    else:
        A.error(log, "bump", {"deltas": deltas, "tool": tool})
    return st


def stats_now(log):
    """Current scoreboard stats dict (old sidecar shape) from the state DB."""
    return S.stats(log)


def kfmt(n):
    """Compact token count: 124000 -> "124k", 1200000 -> "1.2M"."""
    # Branch on the ROUNDED value: 999_500..999_999 rounds to 1.0M, so it must
    # take the M branch ("1M"), not render as the k branch's "1000k". The k
    # boundary has no integer equivalent (999 -> "999", 1000 -> "1k").
    if n >= 999_500:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def scoreboard_parts(st, now):
    """Structured scoreboard data from a stats dict (as returned by bump()); `now` is
    epoch secs. Returns (parts, tools): parts is [(kind, text), …] in display order —
    kinds: cmds / fail / time — and tools is the top-5
    [(name, count), …] EXCLUDING Bash, whose count is already the cmds figure (same
    bump — listing it again would just duplicate the head). The ▪ row is now just
    activity (commands + active time); the other figures moved to where they belong:
    the unique-`files` count AND the ± line-diff render on the tools row (all
    file/tool stats together), and the `≈ $` cost rides the Σ token row (spend
    derives from tokens) — all three rendered by claude-scorebar.py from the same
    `st` dict. The renderer owns the styling; kinds exist so it can colour failures
    red, etc. Token counts live on the separate Σ row (token_parts) — the 'tokens'
    counter still backs the cost figure and the Σ row's total."""
    parts = []
    cmds = int(st.get("commands") or 0)
    if cmds > 0:
        parts.append(("cmds", f"{cmds} cmd" + ("s" if cmds != 1 else "")))
        failed = int(st.get("failed") or 0)
        if failed:
            parts.append(("fail", f"({failed}✗)"))
    # ± line-diff moved to the tools row (with files/Read/Edit/Write); token/cost
    # figures moved to the Σ row. Both rendered by claude-scorebar.py from `st`.
    start = st.get("start")
    if start:
        # ⏱ shows ACTIVE time: wall clock minus the green "your turn" stretches
        # (tab awaiting-response) the scorebar accumulates into 'paused' — the
        # timer freezes while Claude waits on you and resumes when work restarts.
        parts.append(("time", "⏱ " + fmt_dur(now - start - float(st.get("paused") or 0), decimals=False)))
    tools = st.get("tools")
    top = []
    if isinstance(tools, dict) and tools:
        top = [(k, int(v or 0)) for k, v in
               sorted(tools.items(), key=lambda kv: -int(kv[1] or 0))
               if k != "Bash"][:5]
    return parts, top


def token_parts(st):
    """The Σ token-breakdown row (claude-scorebar.py), as [(kind, text), …] in
    display order — kinds: ttot / tin / tout / tread / twrite. The all-in TOTAL
    goes FIRST so a narrow pane (tail-drop) keeps it. [] until something's counted.

    Sums the four per-category counters (tk_in/tk_out/tk_read/tk_create) that BOTH
    accountants feed from the same usage_fields split cost_usd prices —
    bump_transcript (main session) and claude-substream.py (agents). Unlike the ▪
    row's "tok" (billed spend = fresh input + output only), this total ADDS cache
    read (tk_read — replay, not billed as fresh) and cache write, so it reconciles
    with `claude --resume`'s "Usage by model" line, which lists all four categories.
    That's why the Σ total is far larger than the ▪ tok headline — different metrics,
    on purpose."""
    ti = int(st.get("tk_in") or 0)
    to = int(st.get("tk_out") or 0)
    tr = int(st.get("tk_read") or 0)
    tc = int(st.get("tk_create") or 0)
    total = ti + to + tr + tc
    if not total:
        return []
    return [("ttot", kfmt(total) + " total"), ("tin", kfmt(ti) + " in"),
            ("tout", kfmt(to) + " out"), ("tread", kfmt(tr) + " cache"),
            ("twrite", kfmt(tc) + " write")]


def split_tokens(inp, out, read, create):
    """The ONE usage-fields → Σ-row token split (the counters token_parts sums).

    `inp` is the usage input_tokens figure in the Anthropic shape: it INCLUDES
    cache creation and EXCLUDES cache reads — so tk_in (fresh input) subtracts
    `create`. Invariants every producer relies on: tk_in + tk_create == inp
    (the billed fresh-input figure), so tk_in + tk_out + tk_create == the
    ▪-row 'tokens'. A producer whose input figure carries no creation share
    (codex: its `fresh` is already net of cache reads, and codex reports no
    cache-creation category) passes create=0, which leaves inp untouched.
    Every Σ-row producer goes through this rather than re-encoding the
    subtraction per-site."""
    return {"tk_in": inp - create, "tk_out": out,
            "tk_read": read, "tk_create": create}
