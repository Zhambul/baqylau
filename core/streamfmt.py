# core/streamfmt.py — shared block-shaping vocabulary of the stream RENDERERS.
#
# The subagent transcript renderer (plugins/claude_code/substream_render.py) and
# the codex run tailer (plugins/codex/stream.py) paint the same block anatomy —
# a `<who> <glyph> <kind>` header chip, gutter/dim-gutter body text, a line-capped
# excerpt, and an ended-footer token rollup — and each grew its own copy (the
# dependency rule forbids codex importing claude_code, so the shared shapes live
# here in core, next to ops.py's fmt_dur/kfmt/fmt_usd producer vocabulary).
# Everything here is width-INDEPENDENT (it runs once at op creation), takes the
# caller's identity (who/rgb) as parameters, and returns paint ops / plain text —
# it never emits.
from core import ops as O
from core import render as R


def cap(text, n):
    """First n lines of text, plus an '… (N more lines)' marker when truncated."""
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    more = len(lines) - n
    return "\n".join(lines[:n]) + f"\n… ({more} more line{'s' if more != 1 else ''})"


def chip(who, glyph, kind, rgb, tags=(), g=None, lk=None):
    """The block-header label op: '<who> <glyph> <kind>[  tag]…' in the stream's
    colour. `tags` are optional trailing chips (model/effort tag, ctx %) — empty
    ones are skipped, each joins with a double space. g/lk are the ⧉ copy-group
    wiring (core/copy.py), passed straight through to O.label."""
    s = f"{who} {glyph} {kind}"
    for t in tags:
        if t:
            s += f"  {t}"
    return O.label(s, rgb, g=g, lk=lk)


def gutter(text, rgb, g=None):
    """Body text behind the stream-coloured gutter bar (escapes neutralised)."""
    return O.gut(R.unescape(text), rgb, g=g)


def dim_gut(text, rgb, g=None):
    """gutter(), dimmed — reasoning summaries and other low-salience body text."""
    return O.gut(R.DIM + R.unescape(text) + R.RST, rgb, g=g)


def file_line(verb, name, rgb, failed=False, extent="", added=0, removed=0,
              rng=""):
    """The file-op one-liner text: `verb(name)[  extent][  +A -R][  range]`.

    The shared shape three producers paint — the main session's file formatter
    (plugins/claude_code/file_fmt.py), the subagent substream renderer
    (plugins/claude_code/substream_render.py), and the codex patch renderer
    (plugins/codex/stream.py) — each of which used to hand-build it. Verb in
    the caller's FILE_RGB colour (red when failed), name in the default fg
    inside dim parens; a Read passes `extent` ('' == whole file), a mutation
    its added/removed counts (green +A / red -R) and the structuredPatch line
    `rng` — all dim except the counts. A failed op renders just the red
    verb(name) head: no extent/counts/range (counts would claim lines never
    written). Deliberately NOT shared: the who-prefix, model/ctx tags, failure
    mark, and click-to-view hyperlink — those differ per caller and are
    appended/wrapped around this text."""
    col = R.fg(*O.RED) if failed else R.fg(*rgb)
    line = col + verb + R.DIM + "(" + R.COL["def"] + name + R.DIM + ")" + R.RST
    if failed:
        return line
    if extent:
        line += "  " + R.DIM + extent + R.RST
    parts = []
    if added:
        parts.append(R.fg(*O.GREEN) + f"+{added}" + R.RST)
    if removed:
        parts.append(R.fg(*O.RED) + f"-{removed}" + R.RST)
    if parts:
        line += "  " + " ".join(parts)
    if rng:
        line += "  " + R.DIM + rng + R.RST
    return line


def tok_rollup(fresh, out, cached, reads=None):
    """The ended-footer token fragment: ' · Xk in · Yk out[ · cache Z%]'.

    fresh is the BILLED input actually sent (not replayed), out the generated
    output, cached the cache-read share. The cache % denominator `reads` — the
    total context reads the model saw — defaults to fresh + cached; the codex
    rollout passes its own cumulative input_tokens (which already includes the
    cached share). Empty string when there were no tokens at all, so callers can
    append it unconditionally."""
    if not (fresh or out):
        return ""
    s = f" · {O.kfmt(fresh)} in · {O.kfmt(out)} out"
    if reads is None:
        reads = fresh + cached
    if reads > 0:
        s += f" · cache {cached * 100 // reads}%"
    return s
