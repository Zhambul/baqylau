#!/usr/bin/env python3
# claude_ops.py — structured paint-op log for the kitty command mirror.
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
#   gut    s, c[, outer]   -> body text behind a "│ " gutter in colour c (double gutter
#                             when outer is given), wrapped so the gutter repeats on
#                             every visual row. s may already contain ANSI (zero-width)
#   line   s               -> a verbatim pre-styled single line (no gutter, no wrap)
import difflib, json, os, re, shlex, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import claude_audit as A            # always-on audit trail (CLAUDE_AUDIT=0 disables)
except Exception:                       # audit must never break a producer
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()
import claude_paths as P                # the one owner of the mirror-log path format
import claude_state as S                # per-session runtime state (SQLite, /tmp)


def parse_redirect(cmd, cwd):
    """If `cmd` sends stdout to a file (… > file / &> file / 1>> file), return
    (absolute_target, append) — else None. Used by BOTH Bash hooks: claude-cmd-pre
    tails the redirect target instead of tee-ing a second copy, and claude-cmd-fmt
    points the background tailer at it (the task's own output file stays empty
    when the bytes go to the redirect). Conservative: only stdout (or &>)
    redirects, skip /dev/* and fd-dup targets (&1), give up on anything we can't
    tokenise. Last redirect wins (the effective stdout sink)."""
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    target, append, i = None, False, 0
    while i < len(toks):
        t = toks[i]
        if ">" in t and not t.startswith("2"):
            m = re.match(r"^(?:&|1)?(>>?)(.*)$", t)
            if m:
                rest = m.group(2)
                if rest:
                    target, append = rest, m.group(1) == ">>"
                elif i + 1 < len(toks):
                    target, append = toks[i + 1], m.group(1) == ">>"
                    i += 1
        i += 1
    if not target or target.startswith("&") or target.startswith("/dev/"):
        return None
    # shlex does NO shell expansion: a target holding $vars, backticks, globs, or a
    # leading ~ is not the path the shell will actually write to (`> "$OUT"` would
    # have us tail a literal file named $OUT). Fall back to the caller's side file.
    if any(c in target for c in "$`*?[") or target.startswith("~"):
        return None
    if not os.path.isabs(target):
        target = os.path.join(cwd or os.getcwd(), target)
    return target, append


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
    payload somehow lacks session_id. claude-split.py derives the SAME path (from
    the SessionStart payload's session_id, and from the focused pane's
    claude_session var) so the renderer tails exactly what the producers write."""
    return P.mirror_log(d.get("session_id"), d.get("cwd"))


def claude_dirs(start=None):
    """Every `.claude` directory to consult for project-level config (agents, settings),
    NEAREST-FIRST, always ending with ~/.claude. Used instead of a bare os.getcwd()
    lookup, because a subagent/teammate frequently runs in a SUBDIRECTORY (a task's
    `.zhambyl/tasks/<t>/db`, or a git worktree under `.zhambyl/parallel/<wt>`) where
    `<cwd>/.claude` lacks the def/field we need.

    Resolution:
      - $CLAUDE_PROJECT_DIR (the harness's own project override; same as claude-split.py)
        pins the single project `.claude` when set;
      - otherwise walk UP from `start`, collecting EVERY ancestor `.claude` (stopping at
        `/` or $HOME).
    Collecting *all* of them — not just the nearest — is deliberate: an intermediate dir
    may hold its own `.claude/` that is missing `agents/` or the field we want (e.g. a
    task's `db/.claude`), and we must still fall through to the repo-root `.claude` above
    it. Nearest-first means a more-specific dir still overrides a parent. Since the
    agent-defs here are UNTRACKED (present only in the main working tree, absent from
    worktree checkouts), a nested worktree resolves up to the main repo's defs correctly."""
    dirs = []
    env = (os.environ.get("CLAUDE_PROJECT_DIR") or "").strip()
    if env:
        c = os.path.join(env, ".claude")
        if os.path.isdir(c):
            dirs.append(c)
    else:
        d = os.path.abspath(start or os.getcwd())
        home = os.path.expanduser("~")
        while d not in ("/", home):
            c = os.path.join(d, ".claude")
            if os.path.isdir(c):
                dirs.append(c)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    home_claude = os.path.expanduser("~/.claude")
    if home_claude not in dirs:
        dirs.append(home_claude)
    return dirs


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
    """Append paint ops to the session's `ops` table (claude_state, keyed by the
    mirror-log path). One transaction so a block of ops lands contiguously relative
    to other producers inserting concurrently — the atomicity the old single
    O_APPEND write() to the JSONL log provided."""
    if not ops:
        return
    try:
        import claude_state
        if not claude_state.ops_append(log, list(ops)):
            A.error(log, "emit", {"ops": len(ops)})
    except Exception:
        A.error(log, "emit", {"ops": len(ops)})
    A.ops(log, ops)


# --- session statistics (the "▪ session" scoreboard pane) ----------------------
# The scoreboard is a running "so far" summary, aggregated across the SEPARATE, short-
# lived hook processes that produce the mirror (one per Bash call, one per file op, one
# per subagent). They share no memory, so the counters live in the per-session state
# DB keyed to the mirror-log path (claude_state — parked at SessionEnd, restored on
# resume). Each producer bumps its deltas atomically; claude-scorebar.py (a small
# dedicated window under the mirror, opened by claude-split.py) renders the
# scoreboard live off the DB's change counter.

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
SONNET5_INTRO_UNTIL = 1788220800   # 2026-08-31T00:00:00Z — end of the 2/10 intro rate
_SONNET5 = ("sonnet-5", 2.0, 10.0) if time.time() < SONNET5_INTRO_UNTIL else ("sonnet-5", 3.0, 15.0)
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

# --- shared status colours (RGB) -------------------------------------------------
# One table for the hues that must agree across producers (several used to be
# duplicated per-file with "must match" comments). Palette families for streams
# stay in claude_slots; these are the fixed semantic colours.
SLATE  = (170, 185, 210)   # foreground OK (neutral, distinct from the vivid palettes)
ORANGE = (209, 154, 102)   # background header / interrupted
RED    = (224, 108, 117)   # failure / removals
GREEN  = (152, 195, 121)   # success / additions / a written file
YELLOW = (229, 192, 123)   # modification / warning
BLUE   = (97, 175, 239)    # a read
AMBER  = (214, 153, 92)    # a task entering the list

# File-op verbs + colours, shared by claude-file-fmt.py (main session) and
# claude-substream.py (agents) — verbs mirror Claude Code's own UI.
FILE_LABEL = {"Read": "Read", "Edit": "Update", "MultiEdit": "Update",
              "Write": "Write", "NotebookEdit": "Update"}
FILE_RGB   = {"Read": BLUE, "Update": YELLOW, "Write": GREEN}


def fmt_dur(sec):
    """Wall-clock duration chip text: '3.2s' / '4m07s' (negatives clamp to 0)."""
    sec = max(0.0, sec)
    return f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m{int(sec % 60):02d}s"


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


def usage_fields(u):
    """(fin, out, cache_read, cache_create) ints from an assistant message's usage
    dict — fin is the fresh billed input (input + cache_creation), the argument
    order cost_usd takes."""
    create = int(u.get("cache_creation_input_tokens") or 0)
    fin = int(u.get("input_tokens") or 0) + create
    out = int(u.get("output_tokens") or 0)
    cr = int(u.get("cache_read_input_tokens") or 0)
    return fin, out, cr, create


def usage_fold(mid, fields, prev):
    """THE per-message.id dedup fold (the ~2.2x scoreboard-inflation fix): one
    assistant MESSAGE is written as one transcript line PER CONTENT BLOCK, each line
    repeating that message's usage with output_tokens a growing snapshot — so usage
    counts once per message id, and later lines of the SAME id add only the
    (clamped non-negative) per-field delta. Both accountants — bump_transcript here
    (main session) and claude-substream.py (agents) — must share this one
    implementation; they drifted apart once and the fix had to be made twice.

    `fields` is usage_fields(); `prev` is the carried record {"id", "f": [4 ints]}
    (or None). Returns (delta_fields, new_prev). Since cost_usd is linear, pricing
    the deltas equals the full-minus-previous cost, so callers price delta_fields
    directly."""
    if prev and mid and prev.get("id") == mid:
        pf = prev.get("f") or [0, 0, 0, 0]
        deltas = tuple(max(v - int(p or 0), 0) for v, p in zip(fields, pf))
    else:
        deltas = fields
    return deltas, ({"id": mid, "f": list(fields)} if mid else prev)


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
    """Fold scoreboard deltas into the per-session state DB (claude_state) — was a
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


def bump_transcript(log, transcript):
    """Fold the MAIN session's own token spend into the scoreboard state.

    Agents/codex bump their spend when their streamer finishes, but the main session
    has no streamer of its own — without this, the scoreboard's tokens/cost only move
    when an agent run ends (they'd sit "stuck" through plain main-session work). Hooks
    call this with the payload's transcript_path: it reads the session JSONL forward
    from the last position (the 'txpos' counter), sums each new assistant turn's
    usage into 'tokens' (fresh billed input + output — cache reads are replay, not
    spend) and 'cost' (cost_usd on that turn's model, cache read/write rates
    included), and advances the cursor. The whole read-modify runs inside ONE
    BEGIN IMMEDIATE transaction on the state DB (was an flock on the JSON sidecar),
    so concurrent hooks never double-count a turn. Sidechain (subagent) records are
    skipped — their own streamer already bumps them.

    One assistant MESSAGE is written as one JSONL line PER CONTENT BLOCK, each line
    repeating that message's usage (input/cache fields identical, output_tokens a
    growing snapshot — the last line has the final count). So usage is counted once
    per message.id, from its last line (usage_fold, carried across calls in the
    state's 'txlast' record). The read-modify-write of the cursor runs inside ONE
    BEGIN IMMEDIATE transaction, owned by claude_state.transcript_fold — this
    function only parses and prices. Best-effort: any failure rolls the
    transaction back and leaves the state unchanged."""
    if not log or not transcript:
        return {}
    moved = {}                              # what fold counted, for the audit below

    def fold(pos, prev):
        try:
            size = os.path.getsize(transcript)
        except OSError:
            return None
        if size < pos:                      # transcript rotated/replaced — restart
            pos = 0
            prev = None                     # ids from the old file mustn't dedup the new one
        if size <= pos:
            return None
        try:
            with open(transcript, "rb") as tf:
                tf.seek(pos)
                chunk = tf.read(size - pos)
        except OSError:
            return None
        end = chunk.rfind(b"\n")
        if end < 0:                         # no complete new line yet — keep cursor
            return None
        tok, usd = 0, 0.0
        # Per-category token split for the scoreboard's Σ breakdown row, from the
        # SAME usage_fields cost_usd prices: input (fresh, EXCL. cache creation —
        # fields[0] is input+create, so subtract fields[3]), output, cache read
        # (replay), cache write (creation). tk_in+tk_create == the billed 'fin', so
        # tk_in+tk_out+tk_create == the ▪-row 'tokens'; +tk_read is the extra the Σ
        # total carries (why it dwarfs the ▪ headline).
        cin = cout = cread = ccreate = 0
        rows = {}                           # message id -> last usage line seen for it
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
            fields = usage_fields(u)
            mid = m.get("id")
            if not mid:                     # no id to dedup on — count the line as-is
                tok += fields[0] + fields[1]
                usd += cost_usd(m.get("model"), *fields) or 0.0
                cin += fields[0] - fields[3]; cout += fields[1]
                cread += fields[2]; ccreate += fields[3]
                continue
            rows[mid] = (m.get("model"), fields)
        for mid, (model, fields) in rows.items():
            if prev and mid == prev.get("id") and "f" not in prev:
                # txlast persisted by a pre-usage_fold build ({"id","tok","usd"}):
                # delta the whole-message totals once, then re-persist as {"id","f"}.
                d_t = (fields[0] + fields[1]) - int(prev.get("tok") or 0)
                d_c = (cost_usd(model, *fields) or 0.0) - float(prev.get("usd") or 0.0)
                tok += max(d_t, 0)
                usd += max(d_c, 0.0)
                # legacy carry has no per-field split — count this one straddling
                # message's categories in full (a one-time small Σ-row over-count,
                # never of billed tok/usd), then re-persist in the {"id","f"} shape.
                cin += fields[0] - fields[3]; cout += fields[1]
                cread += fields[2]; ccreate += fields[3]
                prev = {"id": mid, "f": list(fields)}
                continue
            d, prev = usage_fold(mid, fields, prev)
            tok += d[0] + d[1]
            usd += cost_usd(model, *d) or 0.0
            cin += d[0] - d[3]; cout += d[1]
            cread += d[2]; ccreate += d[3]
        comps = {"tk_in": cin, "tk_out": cout, "tk_read": cread, "tk_create": ccreate}
        moved.update(tok=tok, usd=usd, txpos=pos + end + 1, txlast=prev, comps=comps)
        return pos + end + 1, prev, tok, usd, comps

    try:
        st = S.transcript_fold(log, fold)
        # Audit only when spend actually moved (this is called on every hook; a
        # no-new-turns call is noise). Records the delta, the cursor advance, and
        # the resulting totals — the trail a token/cost inflation bug needs.
        if moved.get("tok") or moved.get("usd"):
            A.state_file(log, S.db_path(log), "bump-transcript", {
                "d_tokens": moved["tok"], "d_cost": round(moved["usd"], 6),
                "d_split": moved.get("comps"),
                "txpos": moved["txpos"], "txlast": moved["txlast"],
                "now": {"tokens": st.get("tokens"), "cost": st.get("cost")}})
        return st
    except Exception:
        A.error(log, "bump_transcript", {"transcript": transcript})
        return {}


def stats_now(log):
    """Current scoreboard stats dict (old sidecar shape) from the state DB."""
    return S.stats(log)


def _dur(sec):
    sec = max(0, int(sec))
    return f"{sec}s" if sec < 60 else f"{sec // 60}m{sec % 60:02d}s"


def kfmt(n):
    """Compact token count: 124000 -> "124k", 1200000 -> "1.2M"."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


_kfmt = kfmt                       # historical internal name


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
        # ⏱ shows ACTIVE time: wall clock minus the green "your turn" stretches
        # (tab awaiting-response) the scorebar accumulates into 'paused' — the
        # timer freezes while Claude waits on you and resumes when work restarts.
        parts.append(("time", "⏱ " + _dur(now - start - float(st.get("paused") or 0))))
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
