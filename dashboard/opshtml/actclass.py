# dashboard/opshtml/actclass.py — a paint op -> its ACTIVITY CLASS (`act`).
#
# The web mirror's view modes (docs/dashboard.md, *View modes*) collapse runs of
# adjacent stream items into one summary line ("Read 3 files, ran 2 shell
# commands"), which needs a fact no op carries: WHAT KIND of activity this block
# is. This module is the single owner of that classification and of the `act`
# vocabulary it answers in; `op_items` stamps every stream item with it and the
# page only reads it (it used to sniff the chip glyph itself — `CMD_GLYPH` in
# app.05-session.js — which is why the classification now has ONE home).
#
# Why classify at RENDER time instead of stamping `act` in the producers (the
# route `src`/`web` took): unlike a producer's identity, the activity class is
# fully recoverable from the op the producer already wrote. Stamping would put
# the same knowledge in eight formatters AND still need this fallback for every
# PARKED session (a dashboard is mostly read over history, which can't be
# re-stamped) — two implementations that drift. One reader it is.
#
# What it keys on, in order of preference:
#   1. STRUCTURE — the op's `t`, and the semantic colours imported from core.ops.
#      Nothing here re-encodes an RGB literal: a chip painted in the shared
#      `ops.RED` IS a failure, and a chip painted in a SLOT PALETTE colour
#      rather than a semantic one is a per-stream block, not a main-session one.
#   2. The FILE-OP VERB, taken from its owner `tools.FILE_LABEL` (Read/Update/
#      Write) rather than spelled again here.
#   3. The block-opening GLYPH (`▶ ▷ ◉ ↻ ■`). These are producer vocabulary
#      (cmd_fmt / monitor_fmt / subagent_fmt bake them into their chip text) and
#      this table is their ONE reader — deliberately the glyph and not the WORD
#      beside it ("foreground"/"background"/"monitor"), because the glyph is the
#      stable part: the words have been reworded, the glyphs never have.
#
# The glyph is ambiguous on its own and the colour disambiguates it: a subagent
# LAUNCH header is also `▶ <agent type> · <desc>` (subagent_fmt), so `▶` means a
# shell command only when the chip carries one of the semantic command colours
# (SLATE ok / ORANGE interrupted / RED failed) — a `▶` in a slot-palette colour
# is an agent. No palette entry collides with a semantic colour (core/slots.py).
import re

from core import ops as O
from core import render as R
from core import slots as SL
from core import streamfmt as SF
from plugins.claude_code import msgs as MSGS
from plugins.claude_code import task_fmt as TASKS
from plugins.claude_code.tools import FILE_LABEL

# The `act` vocabulary — the closed set every stream item is classified into.
# The page's phrase table (app.05-session.js `ACT_PHRASE`) is keyed by exactly
# these tokens, so adding one here without a phrase there leaves it uncounted
# (grep-tested: test_act_vocabulary_matches_the_page_phrase_table).
ACT_BASH    = "bash"      # a foreground shell command block
ACT_BG      = "bg"        # a background job block (long-lived, own stream)
ACT_MONITOR = "monitor"   # a monitor block (long-lived, own stream)
ACT_READ    = "read"      # a Read one-liner (native, or a collapsed code-read command)
ACT_EDIT    = "edit"      # an Update one-liner (Edit/MultiEdit/NotebookEdit)
ACT_WRITE   = "write"     # a Write one-liner
ACT_AGENT   = "agent"     # a SUBAGENT launch, prompt or result (a one-shot delegate)
ACT_TEAM    = "team"      # …the same, for an agent-TEAM member (a named, mailable peer)
ACT_TASK    = "task"      # a task-list row (✚ created / ✓ completed)
ACT_MAIL    = "mail"      # agent-team mail surfaced in the mirror (● / ◉ read)
ACT_WARN    = "warn"      # the audit warning light's ⚠ one-liner
ACT_MSG     = "msg"       # conversation text (stamped by read.mirror, not here)

ACTS = (ACT_BASH, ACT_BG, ACT_MONITOR, ACT_READ, ACT_EDIT, ACT_WRITE,
        ACT_AGENT, ACT_TEAM, ACT_TASK, ACT_MAIL, ACT_WARN, ACT_MSG)

# The main session's own command colours — the semantic table, imported. A chip
# in any of these is main-session command activity; anything else is a palette
# colour, i.e. a per-stream (agent/job/monitor) block.
_CMD_RGB = (tuple(O.SLATE), tuple(O.ORANGE), tuple(O.RED))

# Block-opening glyphs. `■` opens nothing — it CLOSES a block ("■ finished ·
# 3.2s"), so it names no class and contributes only the outcome.
_GLYPH_BASH = "▶"          # a foreground command … or a subagent launch (see above)
_GLYPH_BG = "▷"
_GLYPH_MONITOR = "◉"
_GLYPH_WS = "⇄"            # a WebSocket monitor's SUBJECT line (monitor_fmt._cmd_op)
_GLYPH_RESUMED = "↻"       # a RESUMED subagent's launch header
_GLYPH_FINISH = "■"

# The stream palettes a bg job's / monitor's chips wear (core/slots.py owns the
# tables — imported, never re-spelled). Together with the semantic command
# colours above these are the COMMAND FAMILY: the three block kinds the web
# renders in the quiet note register (see cmd_note). The five palettes are
# mutually disjoint and none collides with a semantic colour, which is what makes
# a chip's colour a reliable answer to "whose block is this" — a subagent's
# `■ <type> ended` footer wears SUB_PALETTE and is therefore NOT command family.
_STREAM_RGB = frozenset(tuple(c) for c in (SL.BG_PALETTE + SL.MON_PALETTE))

# The kind word a quiet command line DROPS. A foreground command is the default
# kind and its line already shows the command itself, so `▶ foreground` would say
# nothing the dot and the command don't ("⏺ make test · 0.6s"); `background` and
# `monitor` are kept, because with the chip colour gone the word is the only thing
# left that distinguishes a job you didn't wait for from one you did.
_CMD_KIND_MUTE = "foreground"

# Team mail + task rows: the glyphs are their PRODUCERS' vocabulary, imported
# rather than spelled again (msgs.event_ops paints the mail chips, task_fmt the
# task line). `◉` is shared with a monitor block's chip and is disambiguated by
# colour exactly as `▶` is — mail wears the semantic YELLOW/GREEN, a monitor
# wears its slot's palette colour. Reading `◉ read · …` as a MONITOR is precisely
# what happened before these classes existed: a team session's summary counted
# its mail as "watched 7 monitors".
_MAIL_RGB = (tuple(MSGS.MSG_NEW_RGB), tuple(MSGS.MSG_READ_RGB))

# The file-op one-liner's shape, `verb(name)` — built FROM its owner's verb set
# so the three verbs live in exactly one place (core/streamfmt.file_line paints
# the shape, plugins/claude_code/tools.FILE_LABEL names the verbs).
_VERB_ACT = {"Read": ACT_READ, "Update": ACT_EDIT, "Write": ACT_WRITE}
_FILE_RE = re.compile(r"^(%s)\(" % "|".join(sorted(set(FILE_LABEL.values()))))
# The `+A -R` line counts a mutation one-liner carries, in the same shape
# file_line paints them (after the closing paren, so a filename containing a
# digit-plus can't be read as a count).
_DIFF_RE = re.compile(r"\)\s+\+(\d+)(?:\s+-(\d+))?|\)\s+-(\d+)")

# The audit warning light's own one-liner (core/errwatch.py emits `⚠ audit: …`);
# it must never be swallowed by a collapse, so it gets its own class.
_WARN_GLYPH = "⚠"


def _is_team(op):
    """Is this op an agent-TEAM member's, rather than a subagent's? The producer
    already says so in the `src` stamp it wears for the web mirror's main-agent-only
    drop (`team:<id>` vs `sub:<id>` — core/ops.py owns that vocabulary), so nothing
    here parses a name or a colour. An op with no stamp at all (pre-`src` history, or
    the main session's own launch header) is not claimed as a teammate."""
    return str(op.get("src") or "").startswith("team:")


def _plain(op):
    """The op's paint text with ANSI stripped — what the glyph/verb tests read."""
    try:
        return R.strip_ansi(op.get("s") or "").strip()
    except Exception:
        return ""                       # unclassifiable is a legal answer


def _failed(op, text):
    """True when this op REPORTS a non-clean outcome (failed or interrupted), so
    a summary line that swallows it can still show red. Structural, not textual:
    cmd_fmt / monitor_fmt / stream.py colour a failure chip with the shared
    semantic RED (`ops.RED` — imported here, the same table they colour from),
    and a failed file op's verb is painted in that same RED at the head of its
    line (which is why this reads the RAW text, before ANSI is stripped; a
    failed file op is never hyperlinked, so the SGR really is first).

    ORANGE needs the glyph too: it is BOTH an interrupted finish chip and a
    slot-less background header, and only the former is an outcome."""
    if tuple(op.get("c") or ()) == tuple(O.RED):
        return True
    if op.get("t") == "line":
        return (op.get("s") or "").startswith(R.fg(*O.RED))
    return (text[:1] == _GLYPH_FINISH
            and tuple(op.get("c") or ()) == tuple(O.ORANGE))


def diffstat(op):
    """The (added, removed) line counts a MUTATION one-liner carries, (0, 0) when
    it carries none. The collapsed edit fragment sums these over its run
    ("edited 3 files +12 -4"), which is the whole of focus mode's edit summary —
    so the numbers are read here, off the op, rather than scraped back out of
    rendered HTML by the page. Same single owner as the verb: the shape is
    core/streamfmt.file_line's."""
    try:
        m = _DIFF_RE.search(_plain(op))
        if not m:
            return 0, 0
        return int(m.group(1) or 0), int(m.group(2) or m.group(3) or 0)
    except Exception:
        return 0, 0


# A subagent's ⇢ prompt / ⇠ result markers, from their owner (core/streamfmt), and
# the web wording each maps to. Producers now write that wording themselves as the
# op's `note` (core/ops.py); this is the fallback for ops ALREADY ON DISK, which
# cannot be re-stamped — a parked or long-running session would otherwise keep
# showing the terminal's colour-coded chip forever.
_LEGACY_NOTE = ((" %s %s" % SF.MARK_PROMPT, "launched"),
                (" %s %s" % SF.MARK_RESULT, "finished"))


def legacy_agent_note(op):
    """`Agent "<type>" launched|finished` / `Teammate @<name> …` recovered from a
    pre-`note` subagent chip, or None. Reads the marker, not the whole chip: `<who>` is
    simply the text before it, and the model/ctx tags after it are dropped (they belong
    on the agent's card). No duration — the chip never carried one; a live op's own note
    does. Which of the two registers it gets comes from the op's `src` stamp, which is
    OLDER than `note`, so history is worded right too; an op older than both reads as an
    Agent (the neutral one, and the only guess available)."""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        text = _plain(op)
        for mark, verb in _LEGACY_NOTE:
            at = text.find(mark)
            if at > 0:
                return SF.agent_note(text[:at].strip(), verb, team=_is_team(op))
        return None
    except Exception:
        return None                     # unreadable: keep the chip


def agent_brief(op):
    """True for a subagent's ⇢ prompt / ⇠ result CHIP — live or pre-`note` — the two
    blocks whose whole point is the BODY behind the click (the brief, the result).
    Read off the same markers as the wording above, so it holds for both eras. Its
    one reader is the bodiless-note drop in ops.py: such a chip with nothing behind
    it is not a block."""
    try:
        if op.get("t") != "label":
            return False
        text = _plain(op)
        return any(text.find(mark) > 0 for mark, _verb in _LEGACY_NOTE)
    except Exception:
        return False                    # unreadable: keep it (fail toward showing)


# The three ROLES a quiet command-header op plays in its block, which is all the page
# needs to place it: the kind-declaring OPENER (`▶ foreground`, `◉ monitor · …` — the
# line's dot rides with it), a further SUBJECT line beside it (a ws monitor's `⇄ ws ·
# <url>`, which must not mint a second dot), and the CLOSER (`■ finished · 0.6s`), whose
# words go after the command where a duration reads as one.
CQ_OPEN, CQ_SUB, CQ_CLOSE = "open", "sub", "close"


def cmd_note(op):
    """A COMMAND-FAMILY chip -> (text, role) in the quiet note register, or None when
    the op is not one. The roles are the CQ_* constants above.

    An EMPTY text is a legal answer (the muted `foreground`) and NOT the same as None:
    the op still declares its block quiet and still carries the block's ⧉ links, so the
    caller must test for None, never for falsiness.

    The web renders a foreground command, a background job and a monitor as one dim
    line — `⏺ make test · 0.6s` — instead of the terminal's colour-coded pill card
    ("I don't like those boxy blocks / also get rid of the colors / I still want the dot
    and the time info"). The wording is the PRODUCER's, minus the parts the register
    drops: the glyph (`▶ ▷ ◉ ■`, which only carried meaning through its colour — see
    `_MAIL_RGB` on `◉` — and cannot survive un-coloured) and the muted kind word above.
    What's left is what the producer already wrote: `background`, `monitor · <desc>`,
    `finished · 0.6s`, `failed (exit 1) · 2.1s`, `interrupted · 12s`, `monitor ended ·
    no output`. The dot's colour carries the outcome (the page stamps `data-out` from
    the same ops), so nothing is lost with the pill.

    Colour-gated exactly like the classifier, and for the same reason: a `■ <type>
    ended` footer in the SUBAGENT palette is an agent's block, not a command's, and a
    `◉` in a mail colour is a read notice. An op that carries its own `note` is already
    in this register (an agent's, mail's) and is left alone."""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        c = tuple(op.get("c") or ())
        if c not in _CMD_RGB and c not in _STREAM_RGB:
            return None
        text = _plain(op)
        head, rest = text[:1], text[1:].strip()
        if head == _GLYPH_FINISH:
            return rest, CQ_CLOSE
        if head == _GLYPH_WS:
            return rest, CQ_SUB
        if head in (_GLYPH_BASH, _GLYPH_BG, _GLYPH_MONITOR):
            return ("" if rest == _CMD_KIND_MUTE else rest), CQ_OPEN
        return None
    except Exception:
        return None                     # unreadable: keep the chip


def mail_pair(op):
    """A team-mail chip -> (from, to, kind), or None; kind is `sent` (the ✉ MESSAGE
    row, written at send time and carrying the text), `new` or `read` (the inbox
    poller's plumbing rows). Colour-gated like the classifier: only the semantic mail
    colours qualify, so a monitor's `◉` is never read as mail. The one parser of that
    chip's shape — its readers are the wording fallback below, the legacy SUBJECT key
    in op_items, and the plumbing flag beside it."""
    try:
        if op.get("t") != "label":
            return None
        if tuple(op.get("c") or ()) not in _MAIL_RGB:
            return None
        text = _plain(op)
        if text.startswith(MSGS.READ_PREFIX):
            pair, kind = text[len(MSGS.READ_PREFIX):], "read"
        elif text.startswith(MSGS.GLYPH_NEW + " "):
            pair, kind = text[2:], "new"
        elif text.startswith(MSGS.GLYPH_SENT + " "):
            pair, kind = text[2:], "sent"
        else:
            return None
        # a poller row may carry its state after the pair (`● a → b · delivered`);
        # the pair itself is what identifies the message
        frm, sep, rest = pair.partition(" → ")
        to = rest.split(" · ")[0].strip()
        return (frm, to, kind) if sep else None
    except Exception:
        return None                     # unreadable: not mail as far as we can tell


def legacy_mail_note(op):
    """The same fallback for a pre-`note` team-mail chip, reworded through its owner
    (`msgs.note_message` / `note_mail`), or None. History has only the poller's rows
    — the send-time MESSAGE row did not exist then — and an arrival there is the ONLY
    trace of a real message, so a pre-`note` `● <frm> → <to>` keeps the `Message`
    wording rather than being demoted to plumbing. Its summary rides the separate
    body op, where the reader still finds it."""
    if op.get("note"):
        return None
    got = mail_pair(op)
    if not got:
        return None
    frm, to, kind = got
    return (MSGS.note_mail(frm, to, "read") if kind == "read"
            else MSGS.note_message(frm, to))


def legacy_note(op):
    """The web wording for a chip written before its producer carried one — an
    agent's, or team mail's. One door, so op_html/op_items ask the question once."""
    return legacy_agent_note(op) or legacy_mail_note(op)


def mail_plumbing(op, body_follows=False):
    """True when this mail row is the mail SYSTEM reporting on a message rather than
    the message itself: the inbox poller's `delivered`/`read`/lifecycle lines. The web
    shows those in VERBOSE only — asked for in exactly those words: *"I don't want to
    see the lifecycle messages, mail arrivals in the default or focus mode, only the
    real messages on sent time … but in verbose mode I want to see all of them with a
    label"*.

    `body_follows` is the escape hatch for HISTORY: before the send-time row existed,
    an arrival WITH a message body was the only trace of a real message, and demoting
    those would leave an old session showing no mail at all outside verbose. A read
    notice is plumbing whatever follows it."""
    got = mail_pair(op)
    if not got:
        return False
    kind = got[2]
    if kind == "sent":
        return False                    # the message itself
    return kind == "read" or not body_follows


def agent_header(op):
    """True for the MAIN session's own subagent launch/resume header (`▶ <type> ·
    <desc>` / `↻ …`, in a slot-palette colour). The web mirror drops it: the
    substream's ⇢ prompt block says the same thing AND carries the brief behind the
    click, so keeping both puts two launch lines in the feed for one launch
    (docs/dashboard.md *View modes*). Structural, like everything here — the glyph
    plus a palette (not semantic) colour."""
    try:
        if op.get("t") != "label" or op.get("g"):
            return False
        if tuple(op.get("c") or ()) in _CMD_RGB:
            return False                      # a semantic colour is main-session work
        return _plain(op)[:1] in (_GLYPH_BASH, _GLYPH_RESUMED)
    except Exception:
        return False                          # unreadable: keep it (fail toward showing)


def classify(op):
    """One paint op -> (act, bad): its activity class (a token from ACTS, or
    None when the op says nothing about kind — a body `code`/`gut` op inherits
    its block's class) and whether it reports a failure.

    Never raises: an unclassifiable op is (None, False), which the page treats
    as "not collapsible", so a classification gap always fails toward SHOWING
    content rather than hiding it."""
    try:
        return _classify(op)
    except Exception:
        return None, False


def _classify(op):
    t = op.get("t")
    if t not in ("label", "line"):
        return None, False             # body ops inherit their block's class
    text = _plain(op)
    bad = _failed(op, text)
    if t == "line":
        if text.startswith(_WARN_GLYPH):
            return ACT_WARN, bad
        m = _FILE_RE.match(text)
        return (_VERB_ACT.get(m.group(1)) if m else None), bad
    # Which of the two agent classes this op would be, if it turns out to be agent
    # activity at all: a TEAMMATE's blocks count and collapse as teammates ("ran 2
    # teammates, ran 4 agents"), because they are a different kind of thing and the
    # notes now say so. Read off `src`, never the name or the palette.
    agent_act = ACT_TEAM if _is_team(op) else ACT_AGENT
    head = text[:1]
    if not head:
        # An empty / unreadable chip names nothing. It must NOT reach the agent
        # fallback below: that would make a degenerate op foldable, i.e. hide
        # content we failed to understand (the rule is the opposite).
        return None, bad
    if head == _GLYPH_FINISH:
        return None, bad               # closes a block it does not name
    mail = tuple(op.get("c") or ()) in _MAIL_RGB
    if head == MSGS.GLYPH_NEW:
        return ACT_MAIL, bad           # ● from → to (nothing else opens with ●)
    if head == MSGS.GLYPH_SENT and mail:
        # ✉ from → to — the MESSAGE row (mail_fmt.py). Colour-gated like the rest: a
        # substream's own `<who> ✉ from <sender>` chip wears a slot palette and is
        # that agent's block, not the session's mail.
        return ACT_MAIL, bad
    if head == _GLYPH_MONITOR:
        # ◉ is shared: a mail READ notice wears mail's semantic colour, a monitor
        # block's chip wears its slot palette (see _MAIL_RGB).
        return (ACT_MAIL if mail else ACT_MONITOR), bad
    if head in TASKS.GLYPHS:
        return ACT_TASK, bad
    if head == _GLYPH_BG:
        return ACT_BG, bad
    if head == _GLYPH_BASH:
        # ▶ is shared: a main-session command chip wears a semantic colour, a
        # subagent launch header wears its slot's palette colour.
        cmd = tuple(op.get("c") or ()) in _CMD_RGB
        return (ACT_BASH if cmd else agent_act), bad
    if head == _GLYPH_RESUMED:
        return agent_act, bad
    # No main-session glyph at all: a subagent/teammate/codex stream chip
    # (`<who> ⇢ prompt`, `⇠ result`, a codex run header) — agent activity.
    return agent_act, bad
