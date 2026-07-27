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
ACT_AGENT   = "agent"     # a subagent/teammate launch, prompt or result
ACT_TASK    = "task"      # a task-list row (✚ created / ✓ completed)
ACT_MAIL    = "mail"      # agent-team mail surfaced in the mirror (● / ◉ read)
ACT_WARN    = "warn"      # the audit warning light's ⚠ one-liner
ACT_MSG     = "msg"       # conversation text (stamped by read.mirror, not here)

ACTS = (ACT_BASH, ACT_BG, ACT_MONITOR, ACT_READ, ACT_EDIT, ACT_WRITE,
        ACT_AGENT, ACT_TASK, ACT_MAIL, ACT_WARN, ACT_MSG)

# The main session's own command colours — the semantic table, imported. A chip
# in any of these is main-session command activity; anything else is a palette
# colour, i.e. a per-stream (agent/job/monitor) block.
_CMD_RGB = (tuple(O.SLATE), tuple(O.ORANGE), tuple(O.RED))

# Block-opening glyphs. `■` opens nothing — it CLOSES a block ("■ finished ·
# 3.2s"), so it names no class and contributes only the outcome.
_GLYPH_BASH = "▶"          # a foreground command … or a subagent launch (see above)
_GLYPH_BG = "▷"
_GLYPH_MONITOR = "◉"
_GLYPH_RESUMED = "↻"       # a RESUMED subagent's launch header
_GLYPH_FINISH = "■"

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
    """`Agent "<who>" launched|finished` recovered from a pre-`note` subagent chip,
    or None. Reads the marker, not the whole chip: `<who>` is simply the text before
    it, and the model/ctx tags after it are dropped (they belong on the agent's
    card). No duration — the chip never carried one; a live op's own note does."""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        text = _plain(op)
        for mark, verb in _LEGACY_NOTE:
            at = text.find(mark)
            if at > 0:
                return 'Agent "%s" %s' % (text[:at].strip(), verb)
        return None
    except Exception:
        return None                     # unreadable: keep the chip


def legacy_mail_note(op):
    """The same fallback for a pre-`note` team-mail chip: `● <frm> → <to>` and
    `◉ read · <frm> → <to>` reworded through their owner (msgs.note_new /
    note_read), or None. The summary can't be recovered — a legacy arrival keeps
    it in the separate body op, where the reader still finds it — so the note is
    the bare `Message <frm> → <to>`. Colour-gated like the classifier: only the
    two semantic mail colours qualify, so a monitor's ◉ is never reworded."""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        if tuple(op.get("c") or ()) not in _MAIL_RGB:
            return None
        text = _plain(op)
        if text.startswith(MSGS.READ_PREFIX):
            pair, read = text[len(MSGS.READ_PREFIX):], True
        elif text.startswith(MSGS.GLYPH_NEW + " "):
            pair, read = text[2:], False
        else:
            return None
        frm, sep, to = pair.partition(" → ")
        if not sep:
            return None
        return (MSGS.note_read(frm, to) if read
                else MSGS.note_new(frm, to))
    except Exception:
        return None                     # unreadable: keep the chip


def legacy_note(op):
    """The web wording for a chip written before its producer carried one — an
    agent's, or team mail's. One door, so op_html/op_items ask the question once."""
    return legacy_agent_note(op) or legacy_mail_note(op)


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
        return (ACT_BASH if cmd else ACT_AGENT), bad
    if head == _GLYPH_RESUMED:
        return ACT_AGENT, bad
    # No main-session glyph at all: a subagent/teammate/codex stream chip
    # (`<who> ⇢ prompt`, `⇠ result`, a codex run header) — agent activity.
    return ACT_AGENT, bad
