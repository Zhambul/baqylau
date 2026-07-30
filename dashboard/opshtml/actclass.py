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
ACT_SKILL   = "skill"     # a Skill invocation (✦ / `Skill(<name>)`)
ACT_TOOL    = "tool"      # any OTHER tool call (· ToolSearch / WebFetch / Grep …)
ACT_CODEX   = "codex"     # a codex run's block (standalone host OR sidecar) — its
#                           OWN act, so default names the codex run rather than
#                           folding it into "ran N agents" (docs/codex.md)
ACT_WARN    = "warn"      # the audit warning light's ⚠ one-liner
ACT_MSG     = "msg"       # conversation text (stamped by read.mirror, not here)

ACTS = (ACT_BASH, ACT_BG, ACT_MONITOR, ACT_READ, ACT_EDIT, ACT_WRITE,
        ACT_AGENT, ACT_TEAM, ACT_TASK, ACT_MAIL, ACT_SKILL, ACT_TOOL,
        ACT_CODEX, ACT_WARN, ACT_MSG)

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
_GLYPH_TOOL = "·"          # an agent's GENERIC tool block (substream_render.render_tool)

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

# …and a SKILL row's two: its own semantic hue, and RED when the call failed.
_SKILL_RGB = (tuple(O.VIOLET), tuple(O.RED))

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
# No leading space: `who` is a FIELD now (core/ops.py) so a live chip's text
# OPENS with the marker; pre-field history still has the name before it, and
# `find` locating it at 0 vs >0 is exactly what tells the two eras apart.
_LEGACY_NOTE = (("%s %s" % SF.MARK_PROMPT, "launched"),
                ("%s %s" % SF.MARK_RESULT, "finished"))


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
            if at >= 0:
                # `<who>` is a FIELD now (core/ops.py), so a live chip opens AT
                # the marker and only pre-field history has text before it
                return SF.agent_note((text[:at].strip() or op.get("who") or ""),
                                     verb, team=_is_team(op))
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

# The codex palette a codex run's chips wear (core/slots.CODEX_PALETTE) — disjoint
# from every other palette and from the semantic colours, so a chip in it is a
# codex block whoever the host is (a standalone codex session, unstamped; a codex
# sidecar inside a Claude host, `codex:<label>`-stamped). Classifying it ACT_CODEX
# is deliverable B; dropping its PROSE in agent scope is deliverable C.
_CODEX_RGB = frozenset(tuple(c) for c in SL.CODEX_PALETTE)
# codex's reasoning chip glyph (plugins/codex/stream.py `⋯ reasoning`) — its
# prompt/message/result glyphs already coincide with the substream's _PROSE_MARKS
# (⇢/✎/⇠); only reasoning is codex-only, and its `think` bubbles come from
# plugins.conversation in scope, so it drops with the rest.
_CODEX_REASONING = "⋯"

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

    A CODEX sidecar's prose (docs/codex.md *sidecar parity*) drops the same way,
    but ONLY when the run is ROLLOUT-backed — its `chat`/`think` bubbles then come
    from plugins.conversation, exactly as a Claude subagent's do. A COMPANION
    `.log` run has no rollout to re-bubble from, so its prose must STAY as ops;
    read/mirror.agent_scope signals the difference with a `codexprose:<label>`
    marker in `scope` (present only for a rollout-backed run), which is why this
    predicate now takes the scope set. Without the marker a codex prose op is
    kept, so a companion run's scoped mirror is unchanged.

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

    Reads the text as `lead_head` leaves it, so a pre-field header — which opens
    with the agent's NAME, not its marker — is recognised too. Without that every
    one of those blocks stayed in the stream beside the bubble the transcript
    produced, which is the doubled prompts/messages/results a scoped mirror over
    history showed."""
    try:
        if op.get("t") != "label":
            return False
        # a rollout-backed codex sidecar's prose (colour-agnostic — a codex chip
        # wears its own palette, not an agent one — keyed on the src stamp + the
        # scope marker)
        src = str(op.get("src") or "")
        if scope and src.startswith("codex:") \
                and ("codexprose:" + src[len("codex:"):]) in scope:
            h = lead_head(_plain(op))[:1]
            if h in _PROSE_MARKS or h == _CODEX_REASONING:
                return True
        if tuple(op.get("c") or ()) not in _AGENT_RGB:
            return False
        text = lead_head(_plain(op))
        return text[:1] in _PROSE_MARKS or text.startswith(_MAIL_MARKS)
    except Exception:
        return False                    # unreadable: keep it (fail toward showing)


def is_codex(op):
    """True when `op` wears the codex palette — a codex run's block, standalone
    host or sidecar (the one signal that says 'this op is codex's')."""
    try:
        return tuple(op.get("c") or ()) in _CODEX_RGB
    except Exception:
        return False


def codex_prose(op):
    """True for a codex PROSE block header (⇢ prompt / ✎ message / ⋯ reasoning /
    ⇠ result) — the codex twin of prose_block. A STANDALONE codex session's view
    DROPS these (op_items `codex_lead`) because plugins.conversation re-bubbles
    the same prose, exactly as agent scope drops an agent's prose ops; keeping
    them both doubles the conversation AND folds it into 'ran N codex runs'."""
    try:
        if op.get("t") != "label" or not is_codex(op):
            return False
        h = lead_head(_plain(op))[:1]
        return h in _PROSE_MARKS or h == _CODEX_REASONING
    except Exception:
        return False


def codex_chrome(op):
    """A codex-only SCAFFOLDING line a STANDALONE session doesn't need — the
    `⚙ <model> · <effort>` turn-context tag, the `codex ▶ <label>` run banner,
    AND the `■ codex <label> ended · …` run FOOTER. All dropped in op_items
    `codex_lead` (alongside codex_prose) so a standalone codex session's view is
    UNIFORM with Claude's — bubbles + real activity, no per-run banners/footers
    ('I told you no codex specific ui'). The model + token totals still show in
    the scoreboard; the footer's token rollup is redundant with it. A Claude
    session has no such per-session footer, so keeping codex's is exactly the
    codex-specific chrome to remove."""
    try:
        if op.get("t") not in ("label", "gut") or not is_codex(op):
            return False
        # RAW text, NOT lead_head: lead_head strips the leading token as an
        # agent `who` name ("codex ▶ cli" -> "▶ cli", "⚙ model" -> "· …"), which
        # is exactly the prefix this predicate keys on. A real command opens
        # "▶ foreground" in a SEMANTIC colour (not is_codex), so "codex " / the
        # "■ codex " footer can only be the run banner/footer (no collision).
        text = _plain(op).lstrip()
        return (text[:1] == "⚙" or text.startswith("codex ")
                or text.startswith("■ codex "))
    except Exception:
        return False


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
        if not out.get("g") and _FILE_RE.match(R.strip_ansi(lean).strip()):
            # …and THAT one-liner is the lead's own file op, painted for a shared
            # pane: a `gut` so it hangs off this stream's gutter bar, where the
            # lead's is a bare `line`. In scope the bar says nothing (one agent),
            # and the difference costs more than it looks — a gut op names no
            # ACTIVITY CLASS, so an agent's reads and edits were invisible to the
            # item kind and to every view-mode summary. `line` carries the same
            # click-to-view and memory tags, so the conversion is total.
            return O.line(lean, view=out.get("v"), mem=bool(out.get("mem")))
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
    # a command-family header in an AGENT palette: the lead paints that block in a
    # semantic colour, and the colour is what cmd_note/classify gate on. `·` — a
    # GENERIC tool block — is in the set although the lead has no equivalent (its
    # hooks paint no tool but Bash/files/monitors/skills/mail): it is the same
    # thing, one call the agent made, and without the recolour it was the last
    # block in scope still wearing the terminal's coloured pill.
    recolour = c in _AGENT_RGB and stripped[:1] in (
        _GLYPH_BASH, _GLYPH_BG, _GLYPH_MONITOR, _GLYPH_WS, _GLYPH_FINISH,
        _GLYPH_TOOL)
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
    # A CODEX run is a THIRD kind (docs/codex.md): its chips wear the codex palette
    # (disjoint from every other), so a codex block — a standalone host's own
    # (unstamped) or a sidecar's (`codex:<label>`) — classifies ACT_CODEX and the
    # default summary NAMES it ("ran N codex runs") instead of "ran N agents".
    if tuple(op.get("c") or ()) in _CODEX_RGB:
        agent_act = ACT_CODEX
    elif _is_team(op):
        agent_act = ACT_TEAM
    else:
        agent_act = ACT_AGENT
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
