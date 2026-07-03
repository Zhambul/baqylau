#!/usr/bin/env python3
# claude_ops.py — structured paint-op log for the kitty command mirror.
#
# THE REFLOW REFACTOR. Producers (the *-fmt.py hooks + claude-stream.py /
# claude-substream.py) no longer bake the pane width into final ANSI. Instead they
# append width-INDEPENDENT paint ops — one JSON object per line — to the mirror log,
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
#   gut    s, c[, outer]   -> body text behind a "│ " gutter in colour c (double gutter
#                             when outer is given), wrapped so the gutter repeats on
#                             every visual row. s may already contain ANSI (zero-width)
#   line   s               -> a verbatim pre-styled single line (no gutter, no wrap)
import difflib, fcntl, json, os, re, time


def diff_counts(tool_name, inp):
    """(added, removed) line counts for a file-mutating tool's input, matching Claude
    Code's own additions/removals: a real line-level diff for Edit/MultiEdit, the whole
    body for Write, the edited cell for NotebookEdit. (0, 0) for Read or when nothing is
    determinable — callers show a suffix only when there's a non-zero delta."""
    inp = inp or {}

    def delta(old, new):
        a, b = (old or "").splitlines(), (new or "").splitlines()
        add = rem = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                rem += i2 - i1
            if tag in ("replace", "insert"):
                add += j2 - j1
        return add, rem

    if tool_name == "Edit":
        return delta(inp.get("old_string"), inp.get("new_string"))
    if tool_name == "MultiEdit":
        add = rem = 0
        for e in inp.get("edits") or []:
            if isinstance(e, dict):
                da, dr = delta(e.get("old_string"), e.get("new_string"))
                add += da; rem += dr
        return add, rem
    if tool_name == "Write":
        return len((inp.get("content") or "").splitlines()), 0
    if tool_name == "NotebookEdit":
        n = len((inp.get("new_source") or "").splitlines())
        return (0, n) if inp.get("edit_mode") == "delete" else (n, 0)
    return 0, 0


def read_extent(file_info, inp=None):
    """Compact 'start-end/total' describing how much of a file a Read actually returned,
    or '' when it read the WHOLE file (or the extent can't be determined) — so a plain
    Read(name) means the entire file and any range is a signal that it did NOT.

    file_info is the result's file dict (Claude Code records startLine / numLines /
    totalLines on the Read result); inp is the tool input, a fallback (offset/limit) for
    when the result isn't in hand yet. Note a bare Read caps at 2000 lines, so a big file
    shows e.g. '1-2000/5000' — partial even though nothing was passed."""
    if isinstance(file_info, dict) and file_info.get("numLines") is not None:
        start = int(file_info.get("startLine") or 1)
        total = int(file_info.get("totalLines") or 0)
        end = start + int(file_info.get("numLines") or 0) - 1
        if start <= 1 and (total == 0 or end >= total):
            return ""                          # read the whole file
        return f"{start}-{end}/{total}" if total else f"{start}-{end}"
    inp = inp or {}
    off, lim = inp.get("offset"), inp.get("limit")
    if off or lim:
        s = int(off or 1)
        return f"{s}-{s + int(lim) - 1}" if lim else f"{s}+"
    return ""


def edit_range(structured_patch):
    """Compact line range(s) a mutation touched, from the result's structuredPatch hunks
    (each carries newStart / newLines, the affected span in the resulting file) — e.g.
    '445-462' or '445-462,501-503'. '' when there's no patch (a brand-new Write, whose
    +N count already conveys its size) or it can't be read. Caps at 3 shown ranges,
    appending '+k' for the rest, so a big MultiEdit stays short."""
    if not isinstance(structured_patch, list) or not structured_patch:
        return ""
    parts = []
    for h in structured_patch:
        if not isinstance(h, dict) or h.get("newStart") is None:
            continue
        start = int(h.get("newStart"))
        end = start + max(int(h.get("newLines") or 0), 1) - 1
        parts.append(str(start) if end <= start else f"{start}-{end}")
    if not parts:
        return ""
    if len(parts) > 3:
        return ",".join(parts[:3]) + f",+{len(parts) - 3}"
    return ",".join(parts)


def log_path(d):
    """The mirror log for a hook payload, keyed by session_id so PARALLEL Claude
    sessions get separate logs (separate content). Falls back to a cwd slug if a
    payload somehow lacks session_id. claude-split.sh derives the SAME path (from
    the SessionStart payload's session_id, and from the focused pane's
    claude_session var) so the renderer tails exactly what the producers write."""
    sid = (d.get("session_id") or "").strip()
    if sid:
        key = re.sub(r"[^A-Za-z0-9._-]", "-", sid)
    else:
        key = re.sub(r"[/.]", "-", d.get("cwd") or os.getcwd())
    return "/tmp/claude-mirror-" + key + ".log"


def _rgb(c):
    return [int(c[0]), int(c[1]), int(c[2])]


def blank():
    return {"t": "blank"}


def rule():
    return {"t": "rule"}


def label(s, c, outer=None):
    o = {"t": "label", "s": s, "c": _rgb(c)}
    if outer is not None:
        o["outer"] = _rgb(outer)
    return o


def code(s, ind="  "):
    # Pretty-print the command once, here at op creation (width-independent — the
    # renderer still wraps the result to the pane). Best-effort and gated by
    # CLAUDE_MIRROR_FORMAT (set to "0" to show commands verbatim). Only producers call
    # code(); the renderer never does, so this never runs in the paint loop.
    if os.environ.get("CLAUDE_MIRROR_FORMAT", "1") != "0":
        try:
            import claude_render as R
            s = R.format_code(s)
        except Exception:
            pass
    return {"t": "code", "s": s, "ind": ind}


def gut(s, c, outer=None):
    o = {"t": "gut", "s": s, "c": _rgb(c)}
    if outer is not None:
        o["outer"] = _rgb(outer)
    return o


def line(s):
    return {"t": "line", "s": s}


def emit(log, *ops):
    """Append paint ops to the mirror log as JSON lines. One write so a block of ops
    lands atomically relative to other producers appending concurrently."""
    if not ops:
        return
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in ops))
    except Exception:
        pass


# --- session statistics (the "▪ session" scoreboard pane) ----------------------
# The scoreboard is a running "so far" summary, aggregated across the SEPARATE, short-
# lived hook processes that produce the mirror (one per Bash call, one per file op, one
# per subagent). They share no memory, so the counters live in a sidecar JSON keyed to
# the mirror log (removed with it at SessionEnd). Each producer bumps its deltas under
# an flock; claude-scorebar.py (a small dedicated window under the mirror, opened by
# claude-split.sh) renders the scoreboard live off the sidecar's mtime.

# Approximate per-MTok (input, output) USD for the resolved model — for the "≈ $X" cost
# estimate. First substring match wins, so order specific → general. Verified against
# the published price list (2026-06): Fable/Mythos 10/50 · Opus 4.6-4.8 5/25 · Sonnet
# 3/15 · Haiku 4.5 1/5 · legacy Opus 4.1/4.0/3 15/75. Cache reads bill 0.1× input,
# cache writes 1.25× (cost_usd handles both); an unknown model shows no cost
# (cost_usd → None) rather than guess.
#
# Sonnet 5 has an introductory 2/10 rate through 2026-08-31; the entry is picked at
# import time (hook processes are short-lived, so this is per-event in practice) and
# reverts to the 3/15 sticker automatically after the intro window.
_SONNET5 = ("sonnet-5", 2.0, 10.0) if time.time() < 1788220800 else ("sonnet-5", 3.0, 15.0)
PRICES = (
    ("haiku",     1.0,  5.0),
    ("fable",    10.0, 50.0),
    ("mythos",   10.0, 50.0),
    _SONNET5,
    ("sonnet",    3.0, 15.0),
    ("opus-4-1", 15.0, 75.0),
    ("opus-4-0", 15.0, 75.0),
    ("opus-3",   15.0, 75.0),
    ("opus",      5.0, 25.0),
)

SCORE_RGB = (120, 132, 158)   # muted slate-blue — reads as a divider, not an event


def cost_usd(model, tot_in, tot_out, tot_cache=0, tot_create=0):
    """Approximate USD for a run's token totals, or None for an empty/unknown model.
    tot_in = fresh billed input (input + cache_creation, priced at the input rate),
    tot_out = generated, tot_cache = cache_read (0.1× input), tot_create = the
    cache_creation share of tot_in — billed at 1.25× input, so it adds the +0.25×
    premium on top of the flat rate tot_in already paid. See PRICES."""
    m = (model or "").lower()
    if not m:
        return None
    for key, pin, pout in PRICES:
        if key in m:
            return (tot_in * pin + tot_create * pin * 0.25
                    + tot_cache * pin * 0.1 + tot_out * pout) / 1_000_000
    return None


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


def stats_path(log):
    return log + ".stats.json"


def bump(log, tool=None, file=None, **deltas):
    """Read-modify-write the scoreboard sidecar under an flock so concurrent hook
    processes don't clobber each other. Adds each numeric delta to its key, increments
    tools[tool], and stamps 'start' (epoch secs) on first write. `file` records a
    touched path into the 'file_set' map and keeps 'files' at its size — so the
    scoreboard's files figure counts UNIQUE files, not file operations (re-editing
    the same file doesn't inflate it). Returns the updated dict — {} on any failure
    (stats are best-effort, never fatal to a hook)."""
    p = stats_path(log)
    try:
        f = open(p, "a+", encoding="utf-8")
    except OSError:
        return {}
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try:
            st = json.loads(raw) if raw.strip() else {}
        except Exception:
            st = {}
        if not isinstance(st, dict):
            st = {}
        for k, v in deltas.items():
            st[k] = (st.get(k) or 0) + v
        if tool:
            tools = st.get("tools")
            if not isinstance(tools, dict):
                tools = {}
            tools[tool] = (tools.get(tool) or 0) + 1
            st["tools"] = tools
        if file:
            fs = st.get("file_set")
            if not isinstance(fs, dict):
                fs = {}
            fs[file] = 1
            st["file_set"] = fs
            st["files"] = len(fs)
        st.setdefault("start", int(time.time()))
        f.seek(0)
        f.truncate()
        f.write(json.dumps(st, ensure_ascii=False))
        return st
    except Exception:
        return {}
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def bump_transcript(log, transcript):
    """Fold the MAIN session's own token spend into the scoreboard sidecar.

    Agents/codex bump their spend when their streamer finishes, but the main session
    has no streamer of its own — without this, the scoreboard's tokens/cost only move
    when an agent run ends (they'd sit "stuck" through plain main-session work). Hooks
    call this with the payload's transcript_path: it reads the session JSONL forward
    from the last position (kept in the sidecar as 'txpos'), sums each new assistant
    turn's usage into 'tokens' (fresh billed input + output — cache reads are replay,
    not spend) and 'cost' (cost_usd on that turn's model, cache read/write rates
    included), and advances the cursor. Runs under the same flock as bump(), so
    concurrent hooks never double-count a turn. Sidechain (subagent) records are
    skipped — their own streamer already bumps them. Best-effort: any failure leaves
    the sidecar unchanged."""
    if not log or not transcript:
        return {}
    p = stats_path(log)
    try:
        f = open(p, "a+", encoding="utf-8")
    except OSError:
        return {}
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        raw = f.read()
        try:
            st = json.loads(raw) if raw.strip() else {}
        except Exception:
            st = {}
        if not isinstance(st, dict):
            st = {}
        pos = int(st.get("txpos") or 0)
        try:
            size = os.path.getsize(transcript)
        except OSError:
            return st
        if size < pos:                      # transcript rotated/replaced — restart
            pos = 0
        if size <= pos:
            return st
        try:
            with open(transcript, "rb") as tf:
                tf.seek(pos)
                chunk = tf.read(size - pos)
        except OSError:
            return st
        end = chunk.rfind(b"\n")
        if end < 0:                         # no complete new line yet — keep cursor
            return st
        tok, usd = 0, 0.0
        for ln in chunk[:end].split(b"\n"):
            try:
                o = json.loads(ln)
            except Exception:
                continue
            if not isinstance(o, dict) or o.get("type") != "assistant" or o.get("isSidechain"):
                continue
            m = o.get("message") or {}
            u = m.get("usage") if isinstance(m, dict) else None
            if not isinstance(u, dict):
                continue
            create = int(u.get("cache_creation_input_tokens") or 0)
            fin = int(u.get("input_tokens") or 0) + create
            out = int(u.get("output_tokens") or 0)
            tok += fin + out
            c = cost_usd(m.get("model"), fin, out,
                         int(u.get("cache_read_input_tokens") or 0), create)
            if c:
                usd += c
        if tok:
            st["tokens"] = (st.get("tokens") or 0) + tok
        if usd:
            st["cost"] = (st.get("cost") or 0) + usd
        st["txpos"] = pos + end + 1
        st.setdefault("start", int(time.time()))
        f.seek(0)
        f.truncate()
        f.write(json.dumps(st, ensure_ascii=False))
        return st
    except Exception:
        return {}
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def _dur(sec):
    sec = max(0, int(sec))
    return f"{sec}s" if sec < 60 else f"{sec // 60}m{sec % 60:02d}s"


def _kfmt(n):
    # Compact token count: 124000 -> "124k", 1200000 -> "1.2M".
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def scoreboard_parts(st, now):
    """Structured scoreboard data from a stats dict (as returned by bump()); `now` is
    epoch secs. Returns (parts, tools): parts is [(kind, text), …] in display order —
    kinds: cmds / fail / files / add / rem / tok / time / cost (cost goes last so a
    narrow pane drops it first) — and tools is the top-5
    [(name, count), …] EXCLUDING Bash, whose count is already the cmds figure (same
    bump — listing it again would just duplicate the head). The renderer
    (claude-scorebar.py) owns the styling; kinds exist so it can colour failures
    red, added lines green, etc."""
    parts = []
    cmds = int(st.get("commands") or 0)
    if cmds > 0:
        parts.append(("cmds", f"{cmds} cmd" + ("s" if cmds != 1 else "")))
        failed = int(st.get("failed") or 0)
        if failed:
            parts.append(("fail", f"({failed}✗)"))
    files = int(st.get("files") or 0)
    if files:
        parts.append(("files", f"{files} file" + ("s" if files != 1 else "")))
    add, rem = int(st.get("added") or 0), int(st.get("removed") or 0)
    if add:
        parts.append(("add", f"+{add}"))
    if rem:
        parts.append(("rem", f"-{rem}"))
    tok = int(st.get("tokens") or 0)     # metered agent/codex spend (fresh in + out),
    if tok:                              # same provenance as the cost field below
        parts.append(("tok", _kfmt(tok) + " tok"))
    start = st.get("start")
    if start:
        parts.append(("time", "⏱ " + _dur(now - start)))
    cost = float(st.get("cost") or 0)
    if cost > 0:
        parts.append(("cost", "≈ " + fmt_usd(cost)))
    tools = st.get("tools")
    top = []
    if isinstance(tools, dict) and tools:
        top = [(k, int(v or 0)) for k, v in
               sorted(tools.items(), key=lambda kv: -int(kv[1] or 0))
               if k != "Bash"][:5]
    return parts, top
