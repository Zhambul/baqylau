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
import json, os, re


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
