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
import os
import re

from core import ops as O
from core import render as R


def cap(text, n):
    """First n lines of text, plus an '… (N more lines)' marker when truncated."""
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    more = len(lines) - n
    return "\n".join(lines[:n]) + f"\n… ({more} more line{'s' if more != 1 else ''})"


# The two block markers a subagent's WEB-surfaced headers wear — `<who> ⇢ prompt`
# and `<who> ⇠ result` (glyph, kind). Named here, in the shared block-shaping
# vocabulary, because two surfaces read them: substream_render builds the chips
# from them, and the web presenter recovers `Agent "<who>" launched / finished`
# from a chip written BEFORE producers started carrying that wording themselves
# (core/ops.py's "note"). A parked session's ops cannot be re-stamped, so history
# needs the marker; live ops carry the note and never consult it.
MARK_PROMPT = ("⇢", "prompt")
MARK_RESULT = ("⇠", "result")
# The other two markers a subagent's own block headers wear: its assistant text
# (`<who> ✎ message`) and a piece of team mail (`<who> ✉ from|to <peer>`). Named
# here for the same reason as the pair above — the web presenter has to find them
# to know where a header's `<who>` prefix ENDS, which is what AGENT SCOPE strips
# (the name is redundant when the whole view is that agent; docs/dashboard.md
# *Agent scope*). Only the glyph is shared: the kind word differs per mail
# direction, so it stays at the call site.
MARK_MESSAGE = ("✎", "message")
MARK_MAIL = "✉"

# …and how those headers are WORDED for the web (the op's `note`). Claude Code has
# two registers for the two kinds of agent and so do we, verbatim: a Task-spawned
# subagent is `Agent "<type>"` (quoted), an agent-TEAM member is `Teammate @<name>`
# (measured against the 2.1.220 TUI, which prints `⏺ Teammate @fix-smoke-dedup
# finished`). They are genuinely different things — a teammate is a named, long-lived
# peer you can mail, a subagent is a one-shot delegate — and one word for both read as
# a bug ("I want a clear distinction Agent from Teammate"). Here, in core, because BOTH
# sides need it: the producer stamps the note (substream_render.agent_note) and the web
# presenter recovers it for pre-`note` ops (opshtml/actclass.legacy_agent_note), and a
# dashboard module may not reach into a plugin for a string.
AGENT_WORD = 'Agent "%s"'
TEAM_WORD = "Teammate @%s"


def agent_note(label, verb, team=False, dur=""):
    """`Agent "Explore" launched` / `Teammate @fix-smoke-dedup finished · 21m 31s` —
    the web mirror's one-liner for an agent's launch/finish. `dur` is appended when
    the caller has one (a launch has nothing to report yet)."""
    note = ((TEAM_WORD if team else AGENT_WORD) % label) + " " + verb
    return note + " · " + dur if dur else note


# A SKILL invocation's marker and its web wording. `Skill(<name>)` is Claude Code's
# own — verbatim what its transcript prints (`⏺ Skill(slack)`), asked for in exactly
# that shape. Here, in core, for the same reason AGENT_WORD/TEAM_WORD are: the producer
# (plugins/claude_code/skill_fmt.py) stamps the note and the web presenter reads the
# marker back to classify the row (dashboard/opshtml/actclass.py), and a dashboard
# module may not reach into a plugin for a string. `✦` is deliberately a glyph no other
# producer writes — nothing else in the mirror opens with it.
SKILL_MARK = "✦"
SKILL_WORD = "Skill(%s)"


def skill_note(name, failed=False):
    """`Skill(slack)` — the web mirror's one-liner for a skill invocation, `Skill(slack)
    failed` when the call did not succeed (the dot goes red beside it either way, from
    the op's colour; the word is for the reader who is looking at the line, not the
    dot)."""
    note = SKILL_WORD % (name or "?")
    return note + " failed" if failed else note


def chip(who, glyph, kind, rgb, tags=(), g=None, lk=None, web=False, note=None):
    """The block-header label op: '<glyph> <kind>[  tag]…' in the stream's
    colour, with `who` carried as the op's own field rather than concatenated
    into the text (core/ops.py's "who" — the terminal composes it at paint time,
    the web ignores it). `tags` are optional trailing chips (model/effort tag,
    ctx %) — empty ones are skipped, each joins with a double space. g/lk are the
    ⧉ copy-group wiring (core/copy.py), passed straight through to O.label.
    web=True keeps this stamped op in the web dashboard's main mirror (a subagent
    prompt/result header — see core/ops.py's "web" field), and `note` is that
    surface's own wording for it (the quiet one-liner, tags dropped)."""
    s = f"{glyph} {kind}"
    for t in tags:
        if t:
            s += f"  {t}"
    return O.label(s, rgb, g=g, lk=lk, web=web, note=note, who=who)


def gutter(text, rgb, g=None, web=False):
    """Body text behind the stream-coloured gutter bar (escapes neutralised).
    web=True keeps this stamped op in the web dashboard's main mirror (a subagent
    prompt/result body — see core/ops.py's "web" field)."""
    return O.gut(R.unescape(text), rgb, g=g, web=web)


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


SCRATCH_ICON = "✎"
# The per-session scratchpad agent tools offer for temp files
# (/tmp/claude-<uid>/<cwd-slug>/<sid>/scratchpad/…, surfaced as /private/tmp on
# macOS). No env var names it for hook children, so detection is by path shape;
# ANY session's scratchpad matches (another session's scratch file is still
# scratch space, and per-path precision would need the sid threaded everywhere).
_SCRATCH_RE = re.compile(r"/claude-\d+/[^/]+/[^/]+/scratchpad(?:/|$)")


def _abbrev_dir(d):
    """A directory as the one-liner's location hint: home → ~, and long chains
    middle-elided to the first and last two components (the tail is what
    orients the eye; the head says which tree it lives in)."""
    home = os.path.expanduser("~")
    if d == home:
        return "~"
    if d.startswith(home + "/"):
        d = "~" + d[len(home):]
    parts = d.split("/")
    if len(parts) > 5:
        parts = parts[:2] + ["…"] + parts[-2:]
    return "/".join(parts)


def file_display(path, cwd=None):
    """What a file-op one-liner shows inside file_line's parens, as
    (display, kind). kind '' = under the session cwd: basename alone, the
    unchanged quiet default. 'scratch' = a session scratchpad file: '✎ name' —
    the icon IS the location. 'out' = anywhere else: dim abbreviated directory
    + basename, so an op outside the project is visibly elsewhere (a bare
    basename hid WHERE — scratchpad, wiki, and repo ops all looked alike).

    `cwd` defaults to the process cwd (hook children and detached tailers all
    run in the session directory). The 'out' display embeds SGR (the dim
    prefix, then COL['def'] restored for the basename — matching what
    file_line paints around the name); callers needing plain text (audit
    decision strings) use kind + the basename they already have."""
    name = os.path.basename(path.rstrip("/")) or path or "?"
    if _SCRATCH_RE.search(path):
        return SCRATCH_ICON + " " + name, "scratch"
    base = (cwd or os.getcwd()).rstrip("/")
    apath = os.path.abspath(path)
    if base and (apath == base or apath.startswith(base + "/")):
        return name, ""
    d = _abbrev_dir(os.path.dirname(apath) or "/")
    return R.DIM + d + "/" + R.RST + R.COL["def"] + name, "out"


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
