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
# What it keys on, in order of preference:
#   0. The PRODUCER's OWN ANSWER — the op's `act` field (core/ops.py), validated
#      against the vocabulary below. Everything under it is the fallback.
#   1. STRUCTURE — the op's `t`, and the semantic colours imported from core.ops.
#      Nothing here re-encodes an RGB literal: a chip painted in the shared
#      `ops.RED` IS a failure, and a chip painted in a SLOT PALETTE colour
#      rather than a semantic one is a per-stream block, not a main-session one.
#   2. The FILE-OP VERB, taken from its owner `streamfmt.FILE_ACTS` (Read/
#      Update/Write) rather than spelled again here.
#   3. The block-opening GLYPH (`▶ ▷ ◉ ↻ ■`). These are producer vocabulary
#      (cmd_fmt / monitor_fmt / subagent_fmt bake them into their chip text) and
#      this table is their ONE reader — deliberately the glyph and not the WORD
#      beside it ("foreground"/"background"/"monitor"), because the glyph is the
#      stable part: the words have been reworded, the glyphs never have.
#
# Steps 1-3 USED to be the whole story, and the header here argued for it: the
# class is recoverable from the op the producer already wrote, stamping it would
# put the same knowledge in eight formatters, and the fallback would be needed
# for parked history anyway — two implementations that drift. Three things
# happened to that argument. The drift it feared arrived from the OTHER
# direction: the glyph tables are a closed enumeration of TWO hosts' vocabulary,
# so a third host's blocks fall through to "some agent" and a whole session folds
# into "ran N agents" (docs/dashboard.md *View modes*) — the sniff is not
# host-agnostic, it is host-blind. The "eight formatters" became ONE for every
# child agent (core/agentblocks.AgentStream). And `bubbled`, `chrome` and `nf`
# each made the same move already, keeping the sniffer as a parked-history
# fallback, and none of them drifted — because for a LIVE op the fallback is
# dead code, and for a PARKED one it is the only code. So: the producer says
# what it painted, and the tables below classify what is already on disk.
#
# The glyph is ambiguous on its own and the colour disambiguates it: a subagent
# LAUNCH header is also `▶ <agent type> · <desc>` (subagent_fmt), so `▶` means a
# shell command only when the chip carries one of the semantic command colours
# (SLATE ok / ORANGE interrupted / RED failed) — a `▶` in a slot-palette colour
# is an agent. No palette entry collides with a semantic colour (core/slots.py).
import re

from core import agentblocks as AB
from core import errwatch as EW
from core import ops as O
from core import render as R
from core import slots as SL
from core import streamfmt as SF
from plugins.claude_code import msgs as MSGS
from plugins.claude_code import task_fmt as TASKS

# The `act` vocabulary — the closed set every stream item is classified into,
# IMPORTED from its owner (core/ops.py, the `act` op field): the producers stamp
# from that table and this module answers in it, so the two cannot disagree about
# a token. Re-exported under the presenter's own names because this is where the
# page's contract lives: app.05-session.js `ACT_PHRASE` is keyed by exactly these
# strings, so adding one without a phrase there leaves it uncounted (grep-tested:
# test_act_vocabulary_matches_the_page_phrase_table).
ACT_BASH    = O.ACT_BASH      # a foreground shell command block
ACT_BG      = O.ACT_BG        # a background job block (long-lived, own stream)
ACT_MONITOR = O.ACT_MONITOR   # a monitor block (long-lived, own stream)
ACT_READ    = O.ACT_READ      # a Read one-liner (native, or a collapsed code-read command)
ACT_EDIT    = O.ACT_EDIT      # an Update one-liner (Edit/MultiEdit/NotebookEdit)
ACT_WRITE   = O.ACT_WRITE     # a Write one-liner
ACT_AGENT   = O.ACT_AGENT     # a SUBAGENT launch, prompt or result (a one-shot delegate)
ACT_TEAM    = O.ACT_TEAM      # …the same, for an agent-TEAM member (a named, mailable peer)
ACT_TASK    = O.ACT_TASK      # a task-list row (✚ created / ✓ completed)
ACT_MAIL    = O.ACT_MAIL      # agent-team mail surfaced in the mirror (● / ◉ read)
ACT_SKILL   = O.ACT_SKILL     # a Skill invocation (✦ / `Skill(<name>)`)
ACT_TOOL    = O.ACT_TOOL      # any OTHER tool call (· ToolSearch / WebFetch / Grep …)
ACT_CODEX   = O.ACT_CODEX     # a codex run's block (standalone host OR sidecar) — its
#                               OWN act, so default names the codex run rather than
#                               folding it into "ran N agents" (docs/codex.md)
ACT_WARN    = O.ACT_WARN      # the audit warning light's ⚠ one-liner
ACT_MSG     = O.ACT_MSG       # conversation text (stamped by read.mirror, not here)

ACTS = O.ACTS
_ACTS = frozenset(ACTS)

# The main session's own command colours — the semantic table, imported. A chip
# in any of these is main-session command activity; anything else is a palette
# colour, i.e. a per-stream (agent/job/monitor) block.
_CMD_RGB = (tuple(O.SLATE), tuple(O.ORANGE), tuple(O.RED))

# Block-opening glyphs — the CLASSIFY-path fallback for ops written before the
# producer stamped `act` (a parked session cannot be re-stamped). Live ops answer
# from the field and never reach the glyph ladder in _classify; the glyphs stay
# load-bearing for everything ELSE this module reads a header for (lead_head's
# marker set, cmd_note's quiet register, as_lead's recolour test), which is about
# a block's SHAPE rather than its class.
#
# Four of them have an owner in core and are imported from it (agentblocks paints
# them, this module reads them back); three — ◉ ⇄ ↻ — are still spelled here
# because nothing in core paints them: they are plugins/claude_code vocabulary
# (monitor_fmt, subagent_fmt) that no other host shares, and a dashboard module
# may not reach into a plugin for a string. `■` opens nothing — it CLOSES a block
# ("■ finished · 3.2s"), so it names no class and contributes only the outcome.
_GLYPH_BASH = AB.CMD_GLYPH      # a foreground command … or a subagent launch (see above)
_GLYPH_BG = AB.BG_GLYPH
_GLYPH_MONITOR = "◉"
_GLYPH_WS = "⇄"            # a WebSocket monitor's SUBJECT line (monitor_fmt._cmd_op)
_GLYPH_RESUMED = "↻"       # a RESUMED subagent's launch header
_GLYPH_FINISH = AB.FOOT_MARK
_GLYPH_TOOL = AB.TOOL_GLYPH     # an agent's GENERIC tool block (agentblocks.tool_open)

# The stream palettes a bg job's / monitor's chips wear (core/slots.py owns the
# tables — imported, never re-spelled). Together with the semantic command
# colours above these are the COMMAND FAMILY: the three block kinds the web
# renders in the quiet note register (see cmd_note). The five palettes are
# mutually disjoint and none collides with a semantic colour, which is what makes
# a chip's colour a reliable answer to "whose block is this" — a subagent's
# `■ <type> ended` footer wears SUB_PALETTE and is therefore NOT command family.
_STREAM_RGB = frozenset(tuple(c) for c in (SL.BG_PALETTE + SL.MON_PALETTE))

# The kind word a quiet command line DROPS — core/agentblocks' own (the word a
# child's `▶` header is opened with), imported rather than re-spelled. A
# foreground command is the default kind and its line already shows the command
# itself, so `▶ foreground` would say nothing the dot and the command don't
# ("⏺ make test · 0.6s"); `background` and `monitor` are kept, because with the
# chip colour gone the word is the only thing left that distinguishes a job you
# didn't wait for from one you did.
_CMD_KIND_MUTE = AB.CMD_KIND


def _mute_kind(rest):
    """A command header's text minus the muted kind word — but keeping anything the
    producer appended AFTER it. What gets appended is an observer MARK (❖, a
    memory-wiki read or search — plugins/claude_code/fileobs.py), and the quiet line
    should keep saying that: it is the one thing about the block the command text
    itself doesn't state. Matched as a PREFIX rather than by equality for exactly
    that reason — the equality test alone put the word `foreground` back on screen
    the moment a marker was added beside it."""
    if rest == _CMD_KIND_MUTE:
        return ""
    if rest.startswith(_CMD_KIND_MUTE):
        return rest[len(_CMD_KIND_MUTE):].strip()
    return rest

# Team mail + task rows: the glyphs are their PRODUCERS' vocabulary, imported
# rather than spelled again (msgs.event_ops paints the mail chips, task_fmt the
# task line). `◉` is shared with a monitor block's chip and is disambiguated by
# colour exactly as `▶` is — mail wears the semantic YELLOW/GREEN, a monitor
# wears its slot's palette colour. Reading `◉ read · …` as a MONITOR is precisely
# what happened before these classes existed: a team session's summary counted
# its mail as "watched 7 monitors".
_MAIL_RGB = (tuple(MSGS.MSG_NEW_RGB), tuple(MSGS.MSG_READ_RGB))

# …and a SKILL row's two: its own semantic hue, and RED when the call failed.
_SKILL_RGB = (tuple(O.VIOLET), tuple(O.RED))

# The file-op one-liner's shape, `verb(name)` — built FROM its owner's table so
# the three verbs and the class each names live in exactly one place
# (core/streamfmt owns file_line's shape AND its FILE_ACTS map; each host maps
# its own tool names onto those verbs). This was a local dict plus a regex built
# from the claude_code plugin's own tool-name table — the presenter re-deciding,
# for ONE host's tool names, a question the shape's owner answers for every host.
# That reach is gone from this module with it.
_VERB_ACT = SF.FILE_ACTS
_FILE_RE = re.compile(r"^(%s)\(" % "|".join(sorted(_VERB_ACT)))
# The `+A -R` line counts a mutation one-liner carries, in the same shape
# file_line paints them (after the closing paren, so a filename containing a
# digit-plus can't be read as a count).
_DIFF_RE = re.compile(r"\)\s+\+(\d+)(?:\s+-(\d+))?|\)\s+-(\d+)")

# The audit warning light's own one-liner (core/errwatch.py emits `⚠ audit: …`);
# it must never be swallowed by a collapse, so it gets its own class. The glyph is
# that module's vocabulary, imported rather than spelled again.
_WARN_GLYPH = EW.GLYPH


# The producer-source REGISTER -> the agent class it names — the FALLBACK for an
# op with no `act` stamp, which since P6 means a PARKED one (core/ops.py owns the
# `src` vocabulary; this is its one reader on the classify path). A prefix absent
# here — or an op with no stamp — falls through to the palette test in _classify.
#
# DERIVED from the one core-owned register table (core/agentblocks.REGISTERS),
# not authored: two independent spellings of one closed vocabulary meant a third
# host had to edit two packages with no failure if it edited one. The tokens it
# yields ARE the ACT_* constants above (pinned by tests/test_l1i_host_contract.py,
# which also proves this map is imported rather than re-spelled).
_SRC_ACT = AB.src_acts()


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


def nfiles(op):
    """How many files a read one-liner stands for — 1 for an ordinary one, N for a
    Bash read of several at once (`cat app.py utils.py`, one block because the
    command produced one undivided output).

    The web's collapsed summary must WEIGHT that row: an item that read two files is
    "Read 2 files", where counting rows said "Read 1 file" — the same under-report
    the one-liner itself used to make by naming only the first file. Straight off the
    op's `nf` field (core/ops.line), NOT parsed out of the painted text: the text
    lists some names and counts the remainder, so the total is not a fragment of it.
    (An earlier cut did parse the text, which was defensible while the fragment was
    the whole count — the `diffstat` rule below — and stopped being so the moment the
    line started listing files.)"""
    try:
        return max(1, int(op.get("nf") or 1))
    except (TypeError, ValueError):
        return 1


def diffstat(op):
    """The (added, removed) line counts a MUTATION one-liner carries, (0, 0) when
    it carries none. The collapsed edit fragment sums these over its run
    ("edited 3 files +12 -4"), which is the whole of focus mode's edit summary —
    so the numbers are read here, off the op, rather than scraped back out of
    rendered HTML by the page.

    Straight off the producer's `add`/`rem` FIELDS (core/ops.line/gut), with the
    regex over the painted text as the parked-history fallback. Those two
    integers were the LAST fact this module recovered by parsing rendered output:
    the producer computed them, painted them, and the presenter read them back
    out of the ANSI — where a change to how file_line spaces its counts would
    have silently zeroed every edit summary. Same move `nf` made."""
    try:
        add, rem = op.get("add"), op.get("rem")
        if add is not None or rem is not None:
            return int(add or 0), int(rem or 0)
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
# No leading space: `who` is a FIELD now (core/ops.py) so a live chip's text
# OPENS with the marker; pre-field history still has the name before it, and
# `find` locating it at 0 vs >0 is exactly what tells the two eras apart.
# The VERBS are core/streamfmt's too (SF.VERB_*, the words the producer's own
# note ends on) — spelling them again here is how history came to read
# differently from today for no reason a reader could see.
_LEGACY_NOTE = (("%s %s" % SF.MARK_PROMPT, SF.VERB_LAUNCHED),
                ("%s %s" % SF.MARK_RESULT, SF.VERB_FINISHED))


def legacy_agent_note(op):
    """`Agent "<type>" launched|finished` / `Teammate @<name> …` recovered from a
    pre-`note` subagent chip, or None. Reads the marker, not the whole chip: `<who>` is
    simply the text before it, and the model/ctx tags after it are dropped (they belong
    on the agent's card). No duration — the chip never carried one; a live op's own note
    does.

    WHICH REGISTER's word it gets comes from the op's `src` stamp, which is OLDER
    than `note`, so history is worded right too — through the register table
    (agentblocks.register_of_src → its `word`), the same data the PRODUCER words
    a live note with, so the two eras cannot say it differently. An op older than
    the stamp as well reads as an Agent: the neutral register, and the only guess
    available. (This used to ask a boolean `_is_team` and pick between two
    constants — which had no answer at all for a third register, and quietly
    named a codex sidecar's chip an Agent.)"""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        text = _plain(op)
        reg = AB.register_of_src(op.get("src")) or AB.REG_AGENT
        for mark, verb in _LEGACY_NOTE:
            at = text.find(mark)
            if at >= 0:
                # `<who>` is a FIELD now (core/ops.py), so a live chip opens AT
                # the marker and only pre-field history has text before it
                return SF.register_note(
                    AB.register_word(reg),
                    (text[:at].strip() or op.get("who") or ""), verb)
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
        return any(text.find(mark) >= 0 for mark, _verb in _LEGACY_NOTE)
    except Exception:
        return False                    # unreadable: keep it (fail toward showing)


# The three ROLES a quiet command-header op plays in its block, which is all the page
# needs to place it: the kind-declaring OPENER (`▶ foreground`, `◉ monitor · …` — the
# line's dot rides with it), a further SUBJECT line beside it (a ws monitor's `⇄ ws ·
# <url>`, which must not mint a second dot), and the CLOSER (`■ finished · 0.6s`), whose
# words go after the command where a duration reads as one.
CQ_OPEN, CQ_SUB, CQ_CLOSE = "open", "sub", "close"


# Every marker a block header can OPEN with — the command glyphs above plus the
# subagent's own four (their owner is core/streamfmt, which both the producer and
# this presenter read). `core.streamfmt.chip` builds a header as
# `"<glyph> <kind>[  tag]…"`, so one of these leading the text is what says this
# IS a header and its tags may be cut.
_HEAD_MARKS = frozenset((_GLYPH_BASH, _GLYPH_BG, _GLYPH_MONITOR, _GLYPH_WS,
                         _GLYPH_RESUMED, _GLYPH_FINISH, _GLYPH_TOOL,
                         SF.SKILL_MARK,
                         SF.MARK_PROMPT[0], SF.MARK_RESULT[0],
                         SF.MARK_MESSAGE[0], SF.MARK_MAIL))


def lead_head(text):
    """A block header's text as the LEAD's own — the pre-field `<who> ` prefix
    and the trailing model/ctx TAGS removed (docs/dashboard.md *Agent scope*).

    Both are the same thing: per-agent identity, which the shared TERMINAL pane
    needs on every line and an agent SCOPE — one agent, named once in its header —
    does not. `opus-4.8·high  ctx 0% · 9k/1M` is noise once the scoreboard shows
    both figures; the name is noise for the same reason. What's left is exactly
    what the lead's own blocks say — `▶ foreground`, `✎ message`, `✉ from
    team-lead` — which is what lets the same quiet-note register word them.

    The name is only ever in the text for ops written BEFORE `who` became a field
    (core/streamfmt.compose); live chips open at the marker and the `find` below
    returns 0 for them, so one rule covers both eras. It is cut by finding the
    block MARKER rather than by matching a name, because the marker set is closed
    and owned right here while the name is not knowable from the op — the same
    technique legacy_agent_note already reads these chips with. (A body op's
    prefix has no marker to key on and is undone by colour instead, in
    streamfmt.strip_who — its docstring says why the two halves live apart.)

    The EARLIEST marker wins, which is what makes `·` — a generic tool block's
    glyph, and also the separator inside a ctx tag (`ctx 13% · 132k/1M`) — safe to
    include: the header's own `·` always precedes any tag's.

    Tags are split on the DOUBLE space `chip()` joins them with, so a kind
    containing single spaces (`from team-lead`) survives whole. Text with no known
    marker anywhere is returned unchanged — a header this doesn't recognise keeps
    what it says rather than being cut on a guess."""
    at = min((text.find(m) for m in _HEAD_MARKS if m in text), default=-1)
    if at < 0:
        return text
    return text[at:].split("  ", 1)[0].strip()


# The agent palettes a substream's OWN chips wear (core/slots.py owns the tables).
# A nested bg job / monitor of an agent is NOT here — that block is painted by the
# same claude-stream.py the lead's is, in the same slot palette, so it already
# reads as command family.
_AGENT_RGB = frozenset(tuple(c) for c in (SL.SUB_PALETTE + SL.TEAM_PALETTE))

# …and EVERY register's palette, from the one table that lists them
# (agentblocks.stream_palettes → core/slots). as_lead's colour test uses this
# wider set: it asks "is this a CHILD's block header", and a child of a host this
# module has never heard of wears its own register's palette. The narrower
# _AGENT_RGB above stays where the question really is "a CLAUDE agent's" —
# prose_block, whose parked-codex hole is documented as accepted.
_CHILD_RGB = frozenset(AB.stream_palettes())

# The codex palette a codex run's chips wear (core/slots.CODEX_PALETTE) — disjoint
# from every other palette and from the semantic colours, so a chip in it is a
# codex block whoever the host is. It is the FALLBACK for deciding that, behind
# the `src` register (see _SRC_ACT): a standalone codex session's own ops are
# unstamped by design and every parked op predates the stamp, and those are what
# this covers. A codex-NATIVE subagent wears the SUB palette and stamps `sub:`,
# because it is a child agent — it is not this.
_CODEX_RGB = frozenset(tuple(c) for c in SL.CODEX_PALETTE)
# the REASONING chip's glyph (`⋯ reasoning`), from its owner core/streamfmt — the
# fourth prose marker, emitted only by a host whose stream carries a thinking
# summary (codex's rollout does, a Claude subagent's transcript does not). Its
# three siblings are imported two lines below; this one was spelled here, which
# is exactly the drift the styleguide's single-owner row names.
_REASONING_MARK = SF.MARK_REASONING[0]

# The markers on an agent's own PROSE blocks — the brief it was handed, its
# assistant text, its final result. core/streamfmt owns all three.
_PROSE_MARKS = (SF.MARK_PROMPT[0], SF.MARK_RESULT[0], SF.MARK_MESSAGE[0])
# …and mail, in BOTH directions: an agent's team mail is its conversation, and the
# transcript holds all of it — an INCOMING message as a `teammsg` record, an
# OUTGOING one as the SendMessage tool_use conversation() reads back for an agent
# (transcript.mail_send). So both headers are prose and both drop, which is what
# replaces the terminal's coloured pill over a 12-line excerpt with the same
# message bubble the incoming one already got, holding the WHOLE message. The
# direction is only legible from the kind word, whose owner is core/streamfmt
# (MAIL_FROM/MAIL_TO), never re-spelled here.
_MAIL_MARKS = tuple((SF.MARK_MAIL + " " + w % "").rstrip()
                    for w in (SF.MAIL_FROM, SF.MAIL_TO))


def prose_block(op, scope=None):
    """True for the header of an agent's own PROSE block (`⇢ prompt`,
    `✎ message`, `⇠ result`, `✉ from|to <peer>`) — the blocks AGENT SCOPE drops
    because it reads that agent's conversation from its transcript instead
    (docs/dashboard.md *Agent scope*).

    This is now the LEGACY FALLBACK for parked PRE-`bubbled` ops: a fresh op
    declares its own re-bubbling via the producer-set `bubbled` flag (core/ops.py),
    which op_items drops directly — one signal across Claude subagents AND codex
    sidecars, replacing this predicate's old per-tool arms (a Claude agent-palette
    sniff here, a `codexprose:<label>` scope marker for codex). Kept because ops
    already ON DISK carry no flag, so a parked session's agent scope still needs
    the structural recognition below.

    The lead's stream works exactly this way already: its prose is not in the ops
    at all, only in its transcript, and the merge puts it back as bubbles. The
    substream paints an agent's prose into the ops too — it has to, because the
    terminal pane has no other channel for it — so in scope those ops are the one
    thing that WOULD be duplicated, and dropping them is what makes an agent's
    stream the same shape as the lead's. Its body op goes with it (same copy
    group), which is the caller's job.

    Colour-gated to the agent palettes so a lead-stream op can never match: `✎`
    and `⇢` are not the lead's vocabulary, but the gate costs nothing and keeps
    the "whose op is this" question answered the one way this module answers it.
    (A parked codex sidecar predates the flag AND wears the codex palette, not an
    agent one, so its rollout prose is the one legacy case this fallback no longer
    catches — a narrow, cosmetic doubling in an aged codex sidecar scope, accepted
    over a blunt palette sniff that would wrongly drop a companion run's prose.)

    Reads the text as `lead_head` leaves it, so a pre-field header — which opens
    with the agent's NAME, not its marker — is recognised too. Without that every
    one of those blocks stayed in the stream beside the bubble the transcript
    produced, which is the doubled prompts/messages/results a scoped mirror over
    history showed."""
    try:
        if op.get("t") != "label":
            return False
        if tuple(op.get("c") or ()) not in _AGENT_RGB:
            return False
        text = lead_head(_plain(op))
        return text[:1] in _PROSE_MARKS or text.startswith(_MAIL_MARKS)
    except Exception:
        return False                    # unreadable: keep it (fail toward showing)


# ---------------------------------------------------------------------------
# PARKED-HISTORY ONLY, all three below. A live codex run stamps `bubbled` on its
# prose and `chrome` on its frame (plugins/codex/stream.py), and those flags are
# what op_items reads; these recover the same two facts from ops ALREADY ON DISK,
# which no restart can re-stamp. They are palette-gated, so they can only ever
# match codex's OWN ops — a session that never hosted a codex run has none — and
# they are FROZEN: a new host must never grow a fourth one. It has no history to
# recover, because it is stamping the flags from its first op.
#
# "Parked-history only" is a CLAIM these predicates have to earn, not a comment:
# op_items runs them over every op in every view, live ops included, and a
# TEXT test over live content is a trapdoor — a codex run's search query, tool
# arguments, tool output and patch lines are guts carrying whatever the user and
# the tools said, and one that opened with the word "codex" was matched as a run
# banner and took its whole copy group off the page with it. So each predicate
# below is pinned by op TYPE as well as by text, and skips any op the producer
# STAMPED (`bubbled`/`act`/`web` — `chrome` short-circuits before op_items ever
# asks). Both pins are measured, not guessed: over the 237 parked DBs in the
# corpus (159,757 ops, 2026-07-31) the sniffers match exactly 62 ops — 22
# `codex ▶ <label>` banners and 18 `■ codex <label> ended · …` footers, both
# LABELS; 22 `⚙ <model> · <effort>` turn-context lines, all GUTS, all one line
# and none longer than 23 chars — and every single one of the 62 is unstamped.
# ---------------------------------------------------------------------------

# The banner/footer/turn-context shapes, per op type (see the note above). A live
# ⚙ line is a `dim_gut` that sets chrome=True, so the only ⚙ gut that can reach
# this test is history's.
_CHROME_BANNER = "codex ▶ "         # `codex ▶ <label>` — the run banner (label)
_CHROME_FOOTER = "■ codex "         # `■ codex <label> ended · …` (label)
_CHROME_ENDED = " ended"            # …the word that finishes a footer's head
_CHROME_GEAR = "⚙ "                 # `⚙ <model> · <effort>` (gut)
_GEAR_MAX = 64                      # corpus max is 23; a tool output is not this

def is_codex(op):
    """True when `op` wears the codex palette — a codex run's block, standalone
    host or sidecar (the one signal that says 'this op is codex's'). Survives as
    the two predicates below need it; nothing on the LIVE path asks."""
    try:
        return tuple(op.get("c") or ()) in _CODEX_RGB
    except Exception:
        return False


def codex_prose(op):
    """True for a codex PROSE block header (⇢ prompt / ✎ message / ⋯ reasoning /
    ⇠ result) — the codex twin of prose_block, and like it a PARKED-only
    fallback (opshtml.ops.rebubbled). The session view drops these because
    plugins.conversation re-bubbles the same prose, exactly as agent scope drops
    an agent's; keeping both doubles the conversation AND folds it into 'ran N
    codex runs'. A LIVE standalone run paints no prose into the mirror at all,
    so every op this matches is history (25 parked sessions in the measured
    corpus, 17 of them holding such ops)."""
    try:
        if op.get("t") != "label" or not is_codex(op):
            return False
        h = lead_head(_plain(op))[:1]
        return h in _PROSE_MARKS or h == _REASONING_MARK
    except Exception:
        return False


def codex_chrome(op):
    """A codex-only SCAFFOLDING line — the `⚙ <model> · <effort>` turn-context
    tag, the `codex ▶ <label>` run banner, AND the `■ codex <label> ended · …`
    run FOOTER. Dropped by every web view so a codex session reads UNIFORMLY
    with Claude's — bubbles + real activity, no per-run banners/footers ('I told
    you no codex specific ui'). The model + token totals still show in the
    scoreboard; the footer's token rollup is redundant with it.

    The PARKED-history twin of the producer-set `chrome` flag, which a live run
    stamps on all three (plugins/codex/stream.py). Frozen at those three shapes:
    a new host declares `chrome` and needs no sniffer, which is why this one
    keeps a host's NAME in a string compare and no successor may.

    Pinned by op TYPE and gated on the producer's stamps, because op_items asks
    this of every op in every view and the ops it must NOT match are live ones
    whose text is CONTENT (see the section note above)."""
    try:
        t = op.get("t")
        if t not in ("label", "gut") or not is_codex(op):
            return False
        if op.get("bubbled") or op.get("act") or op.get("web"):
            # a producer-STAMPED op: it is live and it already said what it is.
            # (`chrome` itself never gets here — op_items tests the flag first.)
            return False
        # RAW text, NOT lead_head: lead_head strips the leading token as an
        # agent `who` name ("codex ▶ cli" -> "▶ cli", "⚙ model" -> "· …"), which
        # is exactly the prefix this predicate keys on. A real command opens
        # "▶ foreground" in a SEMANTIC colour (not is_codex), so the banner and
        # footer heads below can only be a run's own frame (no collision).
        text = _plain(op).lstrip()
        if t == "gut":
            # the ⚙ turn-context line — one short line, never a body of content
            return (text.startswith(_CHROME_GEAR) and "\n" not in text
                    and len(text) <= _GEAR_MAX)
        return (text.startswith(_CHROME_BANNER)
                or (text.startswith(_CHROME_FOOTER) and _CHROME_ENDED in text))
    except Exception:
        return False


# The three classes a FILE one-liner can carry — what as_lead recognises a
# child's file `gut` by when it converts it into the lead's bare `line`.
_FILE_ACTS = frozenset(SF.FILE_ACTS.values())


def _file_row(op, text):
    """Is this body op a file one-liner (and therefore a whole block)? The
    producer's `act` first, its painted VERB as the parked-history fallback —
    the same order everything else here reads the two in."""
    if op.get("act") in _FILE_ACTS:
        return True
    return bool(_FILE_RE.match(R.strip_ansi(text).strip()))


def as_lead(op):
    """One AGENT-produced op rewritten in the LEAD's own vocabulary — THE single
    place agent scope differs from the session view (docs/dashboard.md *Agent
    scope*).

    The two producers shape a block header differently, because the TERMINAL needs
    them to: every agent shares one pane there, so the substream prefixes each
    header with `<who>`, paints it in that agent's palette, and appends the
    model/ctx tags. On the web a scope is exactly ONE identity, so all three are
    redundant — and, worse, they made every downstream stage fail to recognise the
    block: `cmd_note` is colour-gated, the activity classifier reads the leading
    glyph, and the view modes count what those two answer. An agent's `▶
    foreground` therefore fell through to the legacy coloured pill while the lead's
    became a quiet `⏺` line, and its `■ finished` closer — already SLATE — went
    quiet beside it, which is the mismatch that reads as "legacy styles".

    Rather than teach each of those stages about agents (a second code path
    through the whole renderer, and a second case to chase for every future bug),
    the op is normalised HERE, once, right after the scope filter: strip the
    `<who>` + tags, recolour a command header to the semantic colour the lead's
    equivalent wears, and drop the `outer` bar (the double gutter exists to say
    WHICH agent, which is the one thing the scope already says). Everything below
    this point — classification, the quiet register, folding, summaries, filters —
    then runs on one vocabulary and needs no notion of scope at all.

    The agent's NAME is normally not part of this either: producers carry it as
    the op's own `who` field (core/ops.py), which the terminal composes at paint
    time and the web simply never renders. Ops written BEFORE that field have it
    baked into their text, and no restart can re-stamp a parked session — so the
    composition is undone here for them (lead_head for a header, streamfmt's
    strip_who for a body line). Not cosmetic: with the name still leading the
    text, every gate below keyed on what a block OPENS with missed, so history's
    prose blocks stayed in the stream beside their own transcript bubbles.

    Anything unrecognised passes through untouched."""
    out = op
    if out.get("t") == "gut":
        # a body line's `<who> ` is wrapped in the stream's own colour — the one
        # shape that carries it (a file-op one-liner), undone by its owner
        s = out.get("s") or ""
        lean = s if out.get("who") else SF.strip_who(s, out.get("c") or ())
        if not out.get("g") and _file_row(out, lean):
            # …and THAT one-liner is the lead's own file op, painted for a shared
            # pane: a `gut` so it hangs off this stream's gutter bar, where the
            # lead's is a bare `line`. In scope the bar says nothing (one agent),
            # and the difference costs more than it looks — a gut op names no
            # ACTIVITY CLASS, so an agent's reads and edits were invisible to the
            # item kind and to every view-mode summary. `line` carries the same
            # click-to-view and memory tags — and the producer's own `act`, which
            # is the whole reason this conversion can stop depending on the three
            # display verbs being spelled the way this module expects.
            return O.line(lean, view=out.get("v"), mem=bool(out.get("mem")),
                          nfiles=out.get("nf") or 0, act=out.get("act"),
                          add=out.get("add") or 0, rem=out.get("rem") or 0)
        if lean == s and out.get("outer") is None:
            return out
        out = dict(out)
        out["s"] = lean
        out.pop("outer", None)
        return out
    if out.get("t") != "label":
        return out
    text = _plain(out)
    stripped = lead_head(text)
    c = tuple(out.get("c") or ())
    # a command-family header of a CHILD AGENT: the lead paints that block in a
    # semantic colour, and the colour is what cmd_note/classify gate on. `·` — a
    # GENERIC tool block — is in the set although the lead has no equivalent (its
    # hooks paint no tool but Bash/files/monitors/skills/mail): it is the same
    # thing, one call the agent made, and without the recolour it was the last
    # block in scope still wearing the terminal's coloured pill.
    # Whose block it is: ANY producer stamp says a child painted it (in this view
    # every op is that agent's, by construction — in_scope let it through), with
    # any register's PALETTE as the fallback for ops written before the stamp.
    # Both arms are deliberately host-BLIND. They were narrower — the two Claude
    # palettes, and a `lead` field in the register table listing which registers
    # may recolour — which made the recolour an enumeration of known hosts: a
    # register outside it kept the terminal's coloured pill (cmd_note is
    # colour-gated) and, worse, its `gut` file ops never became `line` ops, so
    # its reads and edits were invisible to every view-mode summary. There is
    # nothing host-specific about "this is a child's command header, paint it
    # like the lead's" — which is the whole job of this function.
    recolour = (c in _CHILD_RGB or bool(out.get("src"))) \
        and stripped[:1] in (_GLYPH_BASH, _GLYPH_BG, _GLYPH_MONITOR, _GLYPH_WS,
                             _GLYPH_FINISH, _GLYPH_TOOL)
    if stripped == text and not recolour and out.get("outer") is None:
        return out                      # already in the lead's shape
    out = dict(out)
    out["s"] = stripped
    if recolour:
        out["c"] = list(O.SLATE)
    out.pop("outer", None)
    return out


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
        if head in (_GLYPH_BASH, _GLYPH_BG, _GLYPH_MONITOR, _GLYPH_TOOL):
            # …`·` included: a generic tool block (`· ToolSearch`) reads as
            # `⏺ ToolSearch` with its request AND its result behind the click —
            # the same quiet line every other block kind gets. It can only reach
            # here in AGENT SCOPE, where as_lead has recoloured it: in the session
            # view an agent's tool block is producer-source-stamped and dropped,
            # and pre-`src` history keeps its chip (nothing there says WHOSE call
            # it was except the colour).
            return _mute_kind(rest), CQ_OPEN
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


def legacy_task_note(op):
    """The web wording for a pre-`note` TASK row (`✚ task #4 · <subj>` /
    `✓ …`), or None. The terminal pill said created-vs-completed in its COLOUR;
    the quiet register says it with the dot plus the producer's own DONE word
    (plugins.claude_code.task_fmt.task_note — the same wording a live op now
    carries, so history and today read identically)."""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        text = _plain(op)
        glyph = text[:1]
        if glyph not in TASKS.GLYPHS:
            return None
        rest = text[1:].strip()          # `task #4 · <subject>`, already worded
        if not rest:
            return None
        return (rest + " · " + TASKS.DONE_WORD
                if glyph == TASKS.GLYPH_DONE else rest)
    except Exception:
        return None                     # unreadable: keep the chip


def legacy_warn_note(op):
    """…and for a pre-`note` audit ⚠ line (core/errwatch.py), reworded through
    its owner. Colourless like every other note — the RED dot is what says an
    audit error is never a clean outcome (see note_out)."""
    try:
        if op.get("t") != "label" or op.get("note"):
            return None
        text = _plain(op)
        return EW.warn_note(text) if text.startswith(_WARN_GLYPH) else None
    except Exception:
        return None                     # unreadable: keep the chip


def legacy_note(op):
    """The web wording for a chip written before its producer carried one — an
    agent's, team mail's, a task row's, an audit warning's. One door, so
    op_html/op_items ask the question once."""
    return (legacy_agent_note(op) or legacy_mail_note(op)
            or legacy_task_note(op) or legacy_warn_note(op))


# The note OUTCOMES this module can answer from the op ALONE — the `data-out`
# the quiet register's dot is tinted by (style.css `[data-out]`). Deliberately
# NOT the agent notes' route: an agent's outcome is a fact about the AGENT, not
# about the op (a launch note is written before there is one), so the page joins
# it from the agents payload (app.05-session.js tintAgentNotes) and this function
# says nothing about it. These three do know:
#   skill — Claude Code loaded it (green) unless the call failed (red); the dot
#           was grey, which read as "still running" for a thing that is over
#           ("skills loaded should be a green dot and not grey"),
#   task  — green once COMPLETED; a created/pending row keeps the dim dot,
#   warn  — an audit error is never ok.
def note_out(op):
    """`"ok"`/`"bad"` for a note whose outcome the op itself knows, else None."""
    try:
        act, bad = classify(op)
        if act == ACT_WARN:
            return "bad"
        if act == ACT_SKILL:
            return "bad" if bad else "ok"
        if act == ACT_TASK:
            if bad:
                return "bad"
            return "ok" if _plain(op)[:1] == TASKS.GLYPH_DONE else None
        return None
    except Exception:
        return None                     # unknown outcome: the dim dot


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
    # THE PRODUCER'S OWN ANSWER, first (core/ops.py's `act`). Validated against
    # the vocabulary, and fail-OPEN: an op stamped with a token this build does
    # not know falls through to the derivation below rather than being handed to
    # a page that has no phrase for it. `bad` is deliberately NOT the producer's
    # to state — an outcome is structural (the shared semantic RED, the ORANGE
    # interrupt), the same read for a stamped op and a parked one.
    hint = op.get("act")
    if hint in _ACTS:
        return hint, bad
    if t == "line":
        if text.startswith(_WARN_GLYPH):
            return ACT_WARN, bad
        m = _FILE_RE.match(text)
        return (_VERB_ACT.get(m.group(1)) if m else None), bad
    # Which of the three agent classes this op would be, if it turns out to be
    # agent activity at all — they count and collapse separately ("ran 2
    # teammates, ran 4 agents, ran 1 codex run"), because they are different kinds
    # of thing and the notes say so.
    #
    # The PRODUCER's stamp decides, first: `src` is the register (core/ops.py), and
    # since a codex-native subagent stamps `sub:` it classifies as the AGENT it is
    # — while `codex:` means exactly what it now says, a sidecar codex run inside a
    # Claude host. Keying on the stamp rather than the palette is what let one
    # child-agent vocabulary cover both tools: the alternative was a palette test
    # per host, which is how a codex subagent's whole run folded into "ran 1 codex
    # run" no matter what it did.
    #
    # The PALETTE is the fallback, for ops with no stamp at all: a standalone codex
    # host's own run (unstamped by design — there codex IS the main agent) and
    # every parked op written before the stamp existed, which no restart can
    # re-stamp.
    agent_act = _SRC_ACT.get(str(op.get("src") or "").split(":", 1)[0])
    if agent_act is None:
        agent_act = (ACT_CODEX if tuple(op.get("c") or ()) in _CODEX_RGB
                     else ACT_AGENT)
    if text.startswith(_WARN_GLYPH):
        # the audit warning light's own line, `label` or `line` alike (errwatch
        # emits a label). Before this it fell through to the agent fallback below
        # and a ⚠ folded into "ran N agents".
        return ACT_WARN, True
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
    if head == SF.SKILL_MARK:
        # ✦ is this producer's alone (skill_fmt.py) — no palette wears it and nothing
        # else in the mirror opens with it, so unlike ▶/◉ it needs no colour to
        # disambiguate. The colour gate is still here for the same reason it is
        # everywhere else: a semantic hue (its own VIOLET, or RED when the call failed)
        # is the main session's work, a palette hue would be somebody's stream.
        if tuple(op.get("c") or ()) in _SKILL_RGB:
            return ACT_SKILL, bad
    if head == _GLYPH_TOOL:
        # · <name> — an agent's GENERIC tool call (substream_render._use_other).
        # No colour gate, unlike ▶/◉: `·` is that producer's alone (no palette
        # wears it, no other block opens with it), and by the time this runs in
        # agent scope as_lead has already recoloured it to the lead's SLATE — a
        # palette test would answer differently in the two views for one op.
        # Its own class rather than the agent fallback below: in scope every row
        # is that agent's, so folding a ToolSearch into "ran 1 teammate" named
        # the wrong thing entirely.
        return ACT_TOOL, bad
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
