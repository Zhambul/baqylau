# core/agentblocks.py — the CHILD-AGENT stream presenter.
#
# Every host runs children: Claude Code spawns subagents and agent-team
# teammates, codex spawns its own subagents, a future tool will spawn something
# else again. Each child streams the SAME anatomy into the mirror — a launch
# card, its messages, its tool calls and shell commands, its file ops, its
# returned result, a closing footer — and each host's tailer used to spell that
# anatomy out itself. The low-level SHAPES were already shared
# (core/streamfmt.py: the chip, the gutter, the file one-liner); what was NOT
# shared is the POLICY, which is the half that actually drifted: which of those
# blocks carry `web` (surface in the LEAD's mirror, not just the child's own
# scope), `note` (the web's own quiet wording for the header), `bubbled` (this
# prose is ALSO a plugins.conversation record, so the op must be dropped
# wherever that conversation renders), `who`, and the model/ctx `tags`.
# A codex-native subagent emitted no launch card and no result card at all,
# because its stream carried its own copy of those rules and nobody could see
# that the copy was missing two of them.
#
# So the policy lives here, once, and a new host writes an adapter rather than a
# second set of rules. This module is PURE: every builder RETURNS a list of
# paint ops and emits nothing, the ⧉ copy group is passed IN (the caller owns
# its block's copy wiring), and text arrives already capped — the caps are
# per-host and deliberately different (the CAP_* tables in the two renderers).
# Host-specific machinery — a tee hand-off, a pending-tool ledger, scoreboard
# bumps, memory observers, click-to-view stashes — stays in the caller, wrapped
# AROUND these calls; nothing about one tool's payloads may leak in here.
#
# The same argument covers WHICH TASK a child's two endpoint cards are about
# (core/childtask.py): a child is not always one task — codex hands a running
# child a follow-up, a teammate is re-tasked by mail — and a stream that stamped
# only the agent id merged two results into one card and gave the web's merge no
# way to tell a completion from the turn it belonged to. So `task()` below is part
# of the same policy: the host says which task started, the two endpoint builders
# stamp it, and every consumer reads one vocabulary.
#
# In core, and not in a plugin, for exactly the reason core/streamfmt.py is: the
# dependency rule forbids codex importing claude_code, and both hosts need the
# same answers. This module composes streamfmt's vocabulary and re-spells none
# of it.
from core import childtask as CT
from core import ops as O
from core import render as R
from core import slots as SL
from core import streamfmt as SF

# The three REGISTERS a child is named in on the web — the one thing about a
# child that is not the same for every host. They select the WORDING of the
# `note` (core/streamfmt owns the format strings themselves: `Agent "<type>"` for
# a Task-spawned subagent, `Teammate @<name>` for an agent-team member, `Codex
# "<label>"` for a codex run), and nothing else: the block shapes, the stamps
# and the colours are register-independent by design.
#
# A register NAME is an internal key and nothing branches on one outside the
# table below — REG_CODEX in particular is not a host check: it names the
# register a codex SIDECAR run is painted in, and a codex-NATIVE subagent uses
# REG_AGENT because it is a child agent.
REG_AGENT = "agent"
REG_TEAM = "team"
REG_CODEX = "codex"

# …and the three facts each register carries on the READ side, in ONE table.
#
# A child's ops are stamped with a producer SOURCE (`core/ops.py` `src`) shaped
# `<prefix>:<agent id>`, and two independent consumers key off that prefix: the
# scope filter that decides WHOSE mirror an op belongs to
# (dashboard/read/mirror.agent_scope) and the web presenter's activity CLASS
# (dashboard/opshtml/actclass._SRC_ACT). Both used to spell the prefix set out
# themselves — three closed lists of the same vocabulary, in three packages, so
# a fourth host meant editing all three and a missed one degrades SILENTLY (a
# blank agent mirror, or every block folding into "ran N agents"). One table,
# three readers; a new host adds a row.
#
# Per row:
#   src   the `src` stamp prefix its producer writes (NB the AGENT register's is
#         `sub:`, not `agent:` — the stamp is older than this module and parked
#         ops carry it, so the table records the fact rather than renaming it)
#   act   the activity-class token its blocks are STAMPED with (core/ops.py's
#         `act` field owns the vocabulary; the row picks this register's member,
#         and the web presenter derives its register→act fallback map from here
#         instead of re-spelling it)
#   word  the format string the web NAMES this register's child with, `%s` the
#         child's own label (`Agent "Explore"`, `Teammate @fix-smoke`, `Codex
#         "Dewey"`). DATA in the table rather than a per-register branch in
#         note() below: the wording was the ONE thing a register selected, and
#         it selected it with `if self.register == REG_CODEX` over a core
#         constant literally named CODEX — a host's NAME baked into the
#         tool-agnostic tier, in the module whose own header forbids exactly
#         that. As a row, a fourth host adds a word instead of a branch.
#   palette  the slot palette its stream colours come from (core/slots.py owns
#         the tables — the row REFERENCES one, so a new host adds a row here and
#         a palette there rather than a constant in each reader).
REGISTERS = {
    REG_AGENT: {"src": "sub",   "act": O.ACT_AGENT,
                "word": SF.AGENT_WORD,           "palette": SL.SUB_PALETTE},
    REG_TEAM:  {"src": "team",  "act": O.ACT_TEAM,
                "word": SF.TEAM_WORD,            "palette": SL.TEAM_PALETTE},
    REG_CODEX: {"src": "codex", "act": O.ACT_CODEX,
                "word": SF.host_word("Codex"),   "palette": SL.CODEX_PALETTE},
}


def src_prefixes():
    """Every register's `src` stamp prefix, in table order ("sub", "team",
    "codex") — the set an agent-scope filter builds `<prefix>:<aid>` from."""
    return tuple(r["src"] for r in REGISTERS.values())


def src_acts():
    """{src prefix: activity-class token} — the presenter's register→act map."""
    return {r["src"]: r["act"] for r in REGISTERS.values()}


def src_stamp(register):
    """The `<prefix>:` a `register`'s producer stamps its ops with — the form a
    consumer tests with str.startswith (`src_stamp(REG_TEAM)` == "team:"). The
    colon is part of it: a bare prefix would also match a longer register name."""
    return REGISTERS[register]["src"] + ":"


def register_of_src(src):
    """Which REGISTER produced an op stamped `src` ("team:a1b2" -> REG_TEAM), or
    None for an unstamped / unknown one. The read-side inverse of src_stamp, for
    the one consumer that has an op and wants the register's DATA (the web
    presenter's wording fallback for a chip written before producers carried a
    note)."""
    pre = str(src or "").split(":", 1)[0]
    for name, row in REGISTERS.items():
        if row["src"] == pre:
            return name
    return None


def register_word(register):
    """The naming template a register's child is worded with (`Agent "%s"`), for
    a caller holding the register rather than an AgentStream."""
    return REGISTERS[register]["word"]


def stream_palettes():
    """Every register's slot palette, flattened — the colours a CHILD's block can
    wear. Its one consumer is the web presenter's `as_lead`, which recognises a
    child's header by colour for ops written before the `src` stamp existed; a
    per-register list there would miss whichever host was added last."""
    return tuple(tuple(c) for r in REGISTERS.values() for c in r["palette"])

# The block-opening glyphs + kind words this presenter paints, beyond the four
# MARK_* pairs core/streamfmt already owns (prompt/result/message/mail — those
# are read BACK by the web presenter, which is why they live there). These are
# producer vocabulary: a generic tool call's quiet `·`, and a shell command's
# `▶ foreground` / `▷ background` header — and dashboard/opshtml/actclass.py
# IMPORTS them from here rather than keeping a second table: the parked-history
# ops it must also classify carry these very bytes (`▶ foreground` predates this
# module unchanged), so one spelling covers history and today alike.
TOOL_GLYPH = "·"
CMD_GLYPH, CMD_KIND = "▶", "foreground"
BG_GLYPH, BG_KIND = "▷", "background"
# …and the two BODY lines a block can end on: the red failure mark a tool/command
# result carries (`■ failed`, `■ failed (exit 3)` where the host has an exit
# code), and a compaction notice.
FAIL_MARK = "■ failed"
COMPACT_TEXT = "⟳ compacted"
# The run FOOTER's opening mark — the same `■` a finish chip wears (core/
# streamfmt.finish_chip), here closing a whole child stream rather than one
# command.
FOOT_MARK = "■"


def fail_text(exit_code=None):
    """`■ failed`, or `■ failed (exit 3)` when the host's result carries an exit
    status. Claude's subagent transcript carries none (its tool_result says only
    is_error), so the bare mark is the default and the parenthesis is opt-in.
    Public because a host that closes a block WITHOUT these builders still owes
    the same words (plugins/codex/stream.py's tool close)."""
    return FAIL_MARK if exit_code is None else "%s (exit %s)" % (FAIL_MARK, exit_code)


class AgentStream:
    """One child agent's block builders, bound to that child's identity.

    `label` is the name every block wears (`who`), `rgb` its stream colour,
    `register` which of the three words the web calls it (REG_*), `tags` a
    zero-arg callable returning its model/effort chip ('' for none), and
    `agent_dur` a zero-arg callable returning how long it has been running, for
    the finish note's `· 21m 31s` (optional: without it the note carries no
    duration, which is also what a LAUNCH note wants — it has nothing to report
    yet).

    Both callables are the caller's because both depend on host-specific
    resolution (a Claude agent's model comes from its meta.json + the parent
    transcript; its start ts from its slot row).
    """

    def __init__(self, *, label, rgb, register=REG_AGENT, tags=None,
                 agent_dur=None):
        self.label = label
        self.rgb = rgb
        self.register = register
        self._tags = tags
        self._agent_dur = agent_dur
        # This child's CURRENT TASK (core/childtask.py) — the identity its launch
        # and result cards are stamped with, and the parent turn that task runs
        # in. A child is not necessarily one task: codex hands a running child a
        # follow-up task, a teammate is re-tasked by mail, so the host calls
        # `task()` whenever a new one starts and the two endpoint builders stamp
        # whatever is current. Empty until a host says otherwise, which is what
        # keeps a host that names no tasks painting exactly as before.
        self._task = ""
        self._turn = ""
        # This child's ACTIVITY CLASS (core/ops.py's `act`), from the register
        # table — every block this stream paints that is the CHILD ITSELF (its
        # launch card, its prose, its result) carries it, so the web classifies
        # the child by what its producer said rather than by matching a glyph
        # against two hosts' vocabulary. Blocks that are a KIND OF WORK rather
        # than the child (a command, a file op, a tool call) carry that work's
        # class instead — a subagent's `▶ make test` is a shell command in
        # anybody's summary.
        self.act = REGISTERS[register]["act"]

    # --- identity -----------------------------------------------------------

    def task(self, task="", turn=""):
        """Declare WHICH TASK this child is now working on, and the PARENT TURN
        it was started in — the shared child-task model (core/childtask.py).

        `task` is the task's own identity (`childtask.key`), stable while it runs
        and different for the next one the same child is handed: that is what
        makes two uses of one child two results instead of one merged card.
        `turn` is the parent turn the task belongs to, "" when the host cannot
        name one (Claude Code records no turn id anywhere) — the ordering rule is
        then simply inert, and the cards are unchanged.

        Called by the host at each task boundary; `launch()` and `result()` stamp
        whatever is current, which is why the two endpoints of one task cannot
        disagree about which task they close."""
        self._task, self._turn = str(task or ""), str(turn or "")

    def _endpoint(self, step):
        # The `ctask` op field for one endpoint of the current task, or None when
        # this host names no task (childtask.stamp validates and returns None).
        return CT.stamp(self._task, step, self._turn)

    def _tagset(self, ctx=""):
        # The op's `tags` field: the model/effort chip, then this turn's ctx
        # chip. Both ride as the op's OWN fields (core/ops.py) — the terminal
        # composes them at paint time, the web's agent scope drops them. Empty
        # entries are dropped by label()/gut(), so a stream with no model
        # resolved and a turn with no ctx simply carry no tags.
        return ((self._tags() if self._tags else ""), ctx)

    def note(self, verb, dur=False):
        """This child's web-mirror one-liner for a launch/finish — `Agent
        "Explore" launched` / `Teammate @fix-smoke-dedup finished · 21m 31s` /
        `Codex "Dewey" ran` (core/ops.py's "note"). ONE builder so the launch and
        finish blocks cannot drift, and the WORDING belongs to core/streamfmt,
        which the web presenter reads too. `dur` appends how long the child has
        run, when the caller injected a way to know.

        WHICH word is the register table's `word` row — data, not a branch: this
        was `if self.register == REG_CODEX` picking a second builder, which is a
        host name deciding a sentence inside the tool-agnostic tier."""
        d = ""
        if dur and self._agent_dur:
            try:
                d = self._agent_dur()
            except Exception:
                d = ""              # a note without a duration beats no note
        return SF.register_note(register_word(self.register), self.label, verb,
                                dur=d)

    # --- the shared op shapes, bound to this stream -------------------------

    def _chip(self, glyph, kind, ctx="", g=None, lk=None, web=False, note=None,
              mem=False, bubbled=False, act=None, ctask=None):
        # `act` defaults to the child's own class (self.act): most of these
        # blocks ARE the child speaking, and the ones that are a kind of WORK
        # pass their own.
        return SF.chip(self.label, glyph, kind, self.rgb, tags=self._tagset(ctx),
                       g=g, lk=lk, web=web, note=note, mem=mem, bubbled=bubbled,
                       act=self.act if act is None else act, ctask=ctask)

    def _gut(self, text, g=None, web=False, bubbled=False, ctask=None):
        return SF.gutter(text, self.rgb, g=g, web=web, bubbled=bubbled,
                         ctask=ctask)

    def _md(self, text, g=None, web=False, bubbled=False, ctask=None):
        # A child's own PROSE is markdown — render the subset (bold/italic/code/
        # headings/bullets). Command output is NOT (that goes through _gut's
        # emphasise path), which is why the two body shapes stay apart.
        return O.gut(R.markdown(R.unescape(text)), self.rgb, g=g, web=web,
                     bubbled=bubbled, ctask=ctask)

    # --- the blocks ---------------------------------------------------------

    def launch(self, brief, g, resumed=False):
        """`⇢ prompt` — the brief this child was handed.

        One of the TWO blocks stamped `web` (core/ops.py): the lead's mirror
        shows a child's launch and its result and nothing in between, and this
        is the launch, with the brief itself behind the click. `bubbled` because
        agent scope re-bubbles the same brief from the child's own transcript.

        Also the START endpoint of the current task (core/childtask.py, `task()`
        above): stamped on BOTH ops, header and body, exactly as `bubbled` is —
        a consumer holding only the header would have to reconstruct the block to
        know where it ends."""
        ct = self._endpoint(CT.STEP_START)
        return [self._chip(*SF.MARK_PROMPT, g=g, lk=O.COPY_ALL, web=True,
                           note=self.note(SF.VERB_RESUMED if resumed
                                          else SF.VERB_LAUNCHED),
                           bubbled=True, ctask=ct),
                self._gut(brief, g=g, web=True, bubbled=True, ctask=ct)]

    def prompt(self, text, g, ctx=""):
        """`⇢ prompt` again, MID-RUN — a follow-up task handed to a child that is
        already running (codex's `followup_task`; a Claude subagent gets exactly
        one brief and never reaches this).

        Deliberately NOT `web` and carrying no note: the lead's mirror shows a
        child's two ENDPOINTS, and a follow-up is neither — it would read as a
        second launch of the same agent. `bubbled` like every other prose block."""
        return [self._chip(*SF.MARK_PROMPT, ctx, g=g, lk=O.COPY_ALL, bubbled=True),
                self._gut(text, g=g, bubbled=True)]

    def result(self, text, g, ctx=""):
        """`⇠ result` — what the child returned. The other `web`-stamped block,
        and the only one whose note carries a duration (the run is over, so
        there is one to report).

        …and the END endpoint of the current task — the one stamp the web's stream
        merge acts on, because this is the block whose place in the feed the
        parent's final answer must follow rather than precede (core/childtask.py).
        A child handed a SECOND task emits a second result card with a second task
        id, which is what keeps the two from folding into one."""
        ct = self._endpoint(CT.STEP_END)
        return [self._chip(*SF.MARK_RESULT, ctx, g=g, lk=O.COPY_ALL, web=True,
                           note=self.note(SF.VERB_FINISHED, dur=True),
                           bubbled=True, ctask=ct),
                self._md(text, g=g, web=True, bubbled=True, ctask=ct)]

    def message(self, text, g, ctx=""):
        """`✎ message` — an INTERMEDIATE assistant message. Deliberately NOT
        `web`: the lead's mirror shows the two endpoints, the middle belongs to
        the child's own scope."""
        return [self._chip(*SF.MARK_MESSAGE, ctx, g=g, lk=O.COPY_ALL,
                           bubbled=True),
                self._md(text, g=g, bubbled=True)]

    def reasoning(self, text, g):
        """`⋯ reasoning` — a low-salience thinking summary, dimmed. Emitted by
        the hosts whose stream carries one (codex's rollout does; a Claude
        subagent's transcript does not)."""
        return [self._chip(*SF.MARK_REASONING, g=g, lk=O.COPY_ALL, bubbled=True),
                SF.dim_gut(text, self.rgb, g=g, bubbled=True)]

    def mail(self, incoming, peer, body, g, ctx=""):
        """`✉ from <peer>` / `✉ to <peer>` — a piece of agent-team mail, in the
        DIRECTION the caller states. `peer` is the already-resolved name (a
        missing sender's fallback is the caller's call, since only it knows
        which of its two payloads is allowed to be empty). Both directions are
        `bubbled`: the incoming one arrives again as a transcript record, the
        outgoing one as the SendMessage conversation record."""
        kind = (SF.MAIL_FROM if incoming else SF.MAIL_TO) % peer
        return [self._chip(SF.MARK_MAIL, kind, ctx, g=g, lk=O.COPY_ALL,
                           bubbled=True),
                self._gut(body, g=g, bubbled=True)]

    def tool_open(self, name, request, g, ctx=""):
        """`· <name>` — a GENERIC tool call, in the quiet register, with its
        request summary (query/url/…) behind the same click. A request-less tool
        is just the header; the group is minted by the caller either way,
        because the block's other half is its RESULT and that op has no group of
        its own to fall back on."""
        ops = [self._chip(TOOL_GLYPH, name or "tool", ctx, g=g, lk=O.COPY_ALL,
                          act=O.ACT_TOOL)]
        if request:
            ops.append(self._gut(request, g=g))
        return ops

    def tool_close(self, g, body, failed=False):
        """…and that tool's answer, behind the same copy group."""
        return self._body(g, body, failed)

    def cmd_open(self, cmd, g, background=False, marks="", mem=False, ctx=""):
        """`▶ foreground` / `▷ background` + the command — a child's shell block.

        `marks` is whatever the caller appends to the kind word (an observer's
        ❖ memory marker), `mem` the matching op flag: a command's memory-ness is
        a property of the whole BLOCK, so it rides the header and not any line
        of the output. No `lk` — a command block wears the renderer's default
        ⧉cmd/⧉out pair."""
        glyph, kind = (BG_GLYPH, BG_KIND) if background else (CMD_GLYPH, CMD_KIND)
        act = O.ACT_BG if background else O.ACT_BASH
        return [self._chip(glyph, kind + marks, ctx, g=g, mem=mem, act=act),
                O.code(cmd, g=g)]

    def cmd_close(self, g, body, failed=False, exit_code=None):
        """…and that command's output + outcome, behind the same copy group."""
        return self._body(g, body, failed, exit_code)

    def _body(self, g, body, failed=False, exit_code=None):
        # The shared close of a tool/command block: the output behind this
        # stream's gutter (emphasised — this is real output, unlike the prose
        # _md renders), the dim `(no output)` stand-in when it printed nothing,
        # and a red failure mark when the call did not succeed.
        ops = [O.gut(R.emphasize(R.unescape(body)), self.rgb, g=g) if body
               else O.gut(SF.no_output_body(), self.rgb, g=g)]
        if failed:
            ops.append(O.gut(R.fg(*O.RED) + fail_text(exit_code) + R.RST,
                             self.rgb, g=g))
        return ops

    def file_text(self, verb, disp, verb_rgb, failed=False, extent="", added=0,
                  removed=0, rng="", marks=""):
        """A file op's one-liner TEXT — the shared shape (core/streamfmt.
        file_line) plus whatever the caller appends to it (`marks`: a ✗ failure
        mark, an observer's ❖).

        Split from file_row below because a caller may still TRANSFORM the
        finished text — Claude's substream wraps it in a click-to-view OSC 8
        hyperlink, which must sit outside the marks — and the op cannot be built
        until that has happened. file_line() is the two together, for a caller
        with nothing to insert between them."""
        return SF.file_line(verb, disp, verb_rgb, failed=failed, extent=extent,
                            added=added, removed=removed, rng=rng) + marks

    def file_row(self, text, view=None, mem=False, ctx="", verb="", added=0,
                 removed=0):
        """…and that text as this child's op: a `gut` (so it hangs off the
        stream's gutter bar in the shared terminal pane) carrying `who` + tags,
        the click-to-view id and the memory flag.

        `verb` is the display verb the text opens with, and names the op's
        ACTIVITY CLASS through its one owner (streamfmt.file_act). The class
        matters even though a `gut` is a body op: agent scope turns this exact op
        into the bare `line` the lead paints (actclass.as_lead), and it is the
        one body op that IS a whole block — so it must arrive with its own class
        rather than inheriting the block above it. `added`/`removed` ride it for
        the same reason: the web's collapsed edit fragment SUMS them, and the
        producer is holding the numbers the presenter used to re-parse."""
        return [O.gut(text, self.rgb, view=view, mem=mem, who=self.label,
                      tags=self._tagset(ctx), act=SF.file_act(verb),
                      add=added, rem=removed)]

    def file_line(self, verb, disp, verb_rgb, failed=False, extent="", added=0,
                  removed=0, rng="", marks="", view=None, mem=False, ctx=""):
        """One file op, text and op together."""
        return self.file_row(
            self.file_text(verb, disp, verb_rgb, failed=failed, extent=extent,
                           added=added, removed=removed, rng=rng, marks=marks),
            view=view, mem=mem, ctx=ctx, verb=verb,
            added=0 if failed else added, removed=0 if failed else removed)

    def compact(self, pre=None, post=None, trigger=None):
        """`⟳ compacted · 120k → 30k (auto)` — the child's conversation was
        compacted. Shown inline (amber) so the gap in its history makes sense.
        Every figure is optional: a host that reports only THAT it compacted
        paints just the mark."""
        txt = COMPACT_TEXT
        if pre:
            txt += " · %s → %s" % (O.kfmt(pre), O.kfmt(post) if post else "?")
        if trigger:
            txt += " (%s)" % trigger
        return [O.gut(R.fg(*O.YELLOW) + txt + R.RST, self.rgb)]

    def footer(self, state, dur, extra=""):
        """`■ <label> ended · 4m07s …` between two rules — the run's closing
        line. `state` is the caller's outcome word (ended / cancelled / failed)
        and `extra` its own rollup tail (ctx fill, tokens, cost), which is
        host-specific arithmetic and stays there."""
        return [O.rule(),
                O.label("%s %s %s · %s%s" % (FOOT_MARK, self.label, state, dur,
                                             extra), self.rgb),
                O.rule()]
