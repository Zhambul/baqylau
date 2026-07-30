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
# *Agent scope*).
MARK_MESSAGE = ("✎", "message")
MARK_MAIL = "✉"
# …and the two DIRECTIONS that mail chip's kind word spells. Named here, beside
# the glyph, because the direction is load-bearing on the web: an INCOMING message
# is part of the agent's conversation and arrives a second time as a transcript
# record (`teammsg`), so agent scope drops the op and keeps the bubble, while an
# OUTGOING one has no record at all and must be kept. A presenter cannot tell them
# apart without these words, and re-spelling them at the reader is how the two
# halves drift.
MAIL_FROM = "from %s"
MAIL_TO = "to %s"

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


CODEX_WORD = 'Codex "%s"'


def agent_note(label, verb, team=False, dur=""):
    """`Agent "Explore" launched` / `Teammate @fix-smoke-dedup finished · 21m 31s` —
    the web mirror's one-liner for an agent's launch/finish. `dur` is appended when
    the caller has one (a launch has nothing to report yet)."""
    note = ((TEAM_WORD if team else AGENT_WORD) % label) + " " + verb
    return note + " · " + dur if dur else note


def codex_note(label, verb, dur=""):
    """`Codex "Dewey" ran` — the web mirror's one-liner for a codex SIDECAR run's
    launch card, the codex twin of agent_note (so a codex run surfaces in the lead
    the same way a Claude subagent does, docs/codex.md *Sidecar → subagent
    parity*). Its own word (`Codex`) rather than `Agent`, since a codex run is a
    third kind the summary counts as `ran N codex runs`."""
    note = (CODEX_WORD % label) + " " + verb
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


def chip(who, glyph, kind, rgb, tags=(), g=None, lk=None, web=False, note=None,
         mem=False, bubbled=False):
    """The block-header label op: `<glyph> <kind>` in the stream's colour, with
    `who` (the agent's name) and `tags` (its model/effort + ctx chips) carried as
    the op's OWN fields rather than concatenated into the text (core/ops.py — the
    terminal composes both at paint time via compose() below, the web ignores
    them). Empty tags are skipped. g/lk are the ⧉ copy-group wiring
    (core/copy.py), passed straight through to O.label. web=True keeps this
    stamped op in the web dashboard's main mirror (a subagent prompt/result
    header — see core/ops.py's "web" field), and `note` is that surface's own
    wording for it (the quiet one-liner). `mem` marks the block as a memory-wiki
    touch (see O.label) — an agent's vault read/search, the same flag the lead's
    command header carries. `bubbled` marks a PROSE block re-bubbled via
    plugins.conversation (see O.label) — the one unified agent-scope prose-drop
    signal across Claude subagents and codex sidecars."""
    return O.label(f"{glyph} {kind}", rgb, g=g, lk=lk, web=web, note=note,
                   who=who, tags=tags, mem=mem, bubbled=bubbled)


def compose(op, s=None):
    """An op's paint text with its WHO prefix and its model/ctx TAGS composed
    back in — the shape the TERMINAL pane paints, and the ONE owner of that shape.

    Every op of a per-agent stream carries both (core/ops.py's "who" and "tags"),
    because the terminal pane is shared: without them a subagent's `Read` and the
    lead's are the same line, under no stated model. They are the op's own FIELDS,
    not part of `s`, so the web dashboard's agent scope — the one surface where
    both are redundant, the whole view being that agent — can simply not render
    them. The renderer (bin/claude-mirror.py) composes them here instead, in the
    two shapes the two op kinds need: a `label` is painted wholesale in the
    stream's colour, so the name is plain text at its head and the tags plain at
    its tail; a `gut` body carries its own SGR, so each piece is coloured and
    closed itself.

    Tags land INSIDE a trailing OSC 8 hyperlink when the text has one, so a
    file-op one-liner stays one click target end to end (the click-to-view link
    wraps the whole line — core/copy.py). `s` overrides the op's text for a caller
    that has already transformed it (the renderer's highlight/line-number pass).
    An op with neither field is returned unchanged.

    strip_who below is this function's inverse for the prefix, and the two must
    stay in step."""
    text = op.get("s", "") if s is None else s
    who, tags = op.get("who"), op.get("tags") or ()
    if not who and not tags:
        return text
    label = op.get("t") == "label"
    if who:
        text = (who + " " + text) if label \
            else (R.fg(*op["c"]) + who + " " + R.RST + text)
    if tags:
        tail = "".join(("  " + t) if label else ("  " + R.DIM + t + R.RST)
                       for t in tags)
        if text.endswith(R.LINK_END):
            text = text[:-len(R.LINK_END)] + tail + R.LINK_END
        else:
            text += tail
    return text


def strip_who(s, rgb):
    """A BODY line's pre-field `<who> ` prefix removed — compose_who's exact
    inverse, for ops ALREADY ON DISK.

    `who` became a field only recently; every op written before that has the name
    baked into `s` in exactly the shape compose_who now builds. A parked session
    cannot be re-stamped, so agent scope would show the name (and, worse, fail to
    recognise the block: its whole normalisation keys on what the text OPENS with).
    Undoing the composition is the only way history reads like the present.

    Byte-exact rather than pattern-matched: the span removed is precisely
    `fg(rgb) <name> RST` at the head of the line, allowed to sit behind an OSC 8
    hyperlink opener (a file-op one-liner's click-to-view link, which is emitted
    first and must survive). Anything else is returned untouched — a body op whose
    text merely happens to contain the stream's colour keeps every byte.

    Headers are NOT handled here: a chip's name is plain text with no colour to
    key on, and its boundary is the block MARKER that follows it — a vocabulary
    that lives with the presenter reading it (dashboard/opshtml/actclass.py)."""
    if len(rgb or ()) != 3:
        return s                    # no stream colour: nothing to key the span on
    pre = R.fg(*rgb)
    at = s.find(pre)
    if at < 0:
        return s
    if at and not (s.startswith(R.LINK_OPEN) and s[:at].endswith(R.LINK_ST)):
        return s                    # only an OSC 8 opener may precede the name
    beg = at + len(pre)
    end = s.find(R.RST, beg)
    if end < 0:
        return s
    name = s[beg:end]
    if not name.endswith(" ") or "\x1b" in name or "\n" in name:
        return s                    # not a bare `<name> ` run: leave it alone
    return s[:at] + s[end + len(R.RST):]


def gutter(text, rgb, g=None, web=False, bubbled=False):
    """Body text behind the stream-coloured gutter bar (escapes neutralised).
    web=True keeps this stamped op in the web dashboard's main mirror (a subagent
    prompt/result body — see core/ops.py's "web" field). `bubbled` marks a prose
    body re-bubbled via plugins.conversation (see O.gut / chip)."""
    return O.gut(R.unescape(text), rgb, g=g, web=web, bubbled=bubbled)


def dim_gut(text, rgb, g=None, bubbled=False):
    """gutter(), dimmed — reasoning summaries and other low-salience body text.
    `bubbled` marks a prose body re-bubbled via plugins.conversation (a codex
    sidecar's `⋯ reasoning` — see O.gut)."""
    return O.gut(R.DIM + R.unescape(text) + R.RST, rgb, g=g, bubbled=bubbled)


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


# The main session's FOREGROUND-command colours — the semantic table imported
# from core.ops, named here so the two command-block painters share one source:
# plugins/claude_code/cmd_fmt.py (Claude's own Bash blocks) and
# plugins/codex/stream.py (a standalone codex run's exec blocks, docs/codex.md
# *Standalone command parity*). Slate = a clean finish, orange = interrupted / a
# slot-less background header, red = a failure. A codex command painted in these
# reads IDENTICALLY to Claude's — same colour, same glyph, same shape — so every
# downstream stage (the web activity classifier, the quiet-note register, the
# view-mode fold) treats it as main-session command activity with no codex
# special-casing (dashboard/opshtml/actclass.py keys on exactly this table).
CMD_OK, CMD_BG, CMD_FAIL = O.SLATE, O.ORANGE, O.RED


def finish_chip(dur, failed=False, interrupted=False, exit_code=None):
    """The block-CLOSING chip's (text, colour) for a finished command — the shared
    shape cmd_fmt._render_finished and the codex stream both paint: `■ finished ·
    3.2s` (slate), `■ failed (exit N) · …` (red), `■ interrupted · …` (orange).
    `dur` is the caller's already-formatted duration string ('?' when unknown)."""
    if not failed:
        return "■ finished · " + dur, CMD_OK
    if interrupted:
        return "■ interrupted · " + dur, CMD_BG
    if exit_code is not None:
        return f"■ failed (exit {exit_code}) · {dur}", CMD_FAIL
    return "■ failed · " + dur, CMD_FAIL


def no_output_body():
    """The dim `(no output)` gutter body a command block shows when its command
    printed nothing — shared so an empty codex exec reads like an empty Bash run."""
    return R.DIM + "(no output)" + R.RST


def command_open(cmd, gid, col=None, head="▶ foreground", mem=False):
    """The OPENING ops of a foreground command block — a blank + rule, the coloured
    header, the command as a `code` op, and a rule. The renderer auto-paints the
    ⧉cmd/⧉out copy links onto the g-tagged header (bin/claude-mirror.py), so no
    `lk` is passed; the header colour is always CMD_OK (slate) — the OUTCOME colour
    rides the gutter + closing chip, exactly as the live Claude path paints it
    (plugins/claude_code/cmd_pre.py). Emitted the instant the command starts so a
    long-running / backgrounded one shows immediately; command_close appends its
    body + closer when the result lands.

    `mem` rides the header op (core/ops.label) — a command that read or searched
    the memory wiki, which is a property of the BLOCK rather than of any line in
    it; the ❖ marker itself goes in `head`, which the caller builds."""
    return [O.blank(), O.rule(), O.label(head, col or CMD_OK, g=gid, mem=mem),
            O.code(cmd, g=gid), O.rule()]


def command_close(body, chip_txt, col, gid):
    """The CLOSING ops of a foreground command block — the output behind the
    outcome-coloured gutter, a rule, the closing chip, a rule. `body` is the
    already-shaped gut text (emphasised/neutralised, or no_output_body())."""
    return [O.gut(body, col, g=gid), O.rule(), O.label(chip_txt, col, g=gid),
            O.rule()]


def command_block(cmd, body, chip_txt, col, gid, head="▶ foreground", mem=False):
    """A COMPLETE foreground command block (open + close) as one op list — the
    shared anatomy cmd_fmt paints when it renders the whole block AT ONCE (no live
    tailer owns it, so there is no start/finish split): the header, command,
    output and chip are all painted in `col`, the block's single outcome colour.
    (The live path splits this — cmd_pre opens the block slate, the finish chip
    lands the outcome colour later — which is what command_open/command_close are
    for; codex's rollout is tailed the same split way.)"""
    return command_open(cmd, gid, col=col, head=head, mem=mem) + \
        command_close(body, chip_txt, col, gid)


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
