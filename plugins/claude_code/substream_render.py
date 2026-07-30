# plugins/claude_code/substream_render.py — subagent transcript BLOCK RENDERING.
#
# The rendering half of the subagent/teammate streamer (entry: claude-substream.py
# -> plugins/claude_code/substream.py, which owns the lifecycle: argv/env contract,
# tailer spawning, cancellation signals, resume checkpointing, the footer). This
# module owns turning transcript RECORDS into mirror paint ops — the PAINT half
# of the parse/paint split: line→record parsing (and the text helpers
# result_text / input_summary) lives in transcript.py, whose records
# handle_line dispatches on; the Renderer class holds the per-run render state
# (pending message buffer, ctx-tag turn tracking, the pend tool_use ledger, the
# footer's cumulative usage rollup).
#
# Import-safe by design (no argv parsing, no META resolution) — substream.py keeps
# those top-level side effects; everything identity-shaped (LOG, agent id, label,
# colour, the model/ctx tag callables, the tailer spawners) is INJECTED.
import os
import re

from core import agentblocks as AB
from core import ops as O
from core import render as R
from core import state as S
from core import streamfmt as SF
from plugins.claude_code import accounting as ACC
from plugins.claude_code import fileobs as FOBS
from plugins.claude_code import tools as CT
from plugins.claude_code import transcript as TR

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)
RST = R.RST

# Verbs for file ops — the shared claude_code.tools table (claude-file-fmt.py
# renders the main session's file ops with the same; the colours ride into
# streamfmt.file_line straight from CT.FILE_RGB).
FILE_LABEL = CT.FILE_LABEL

# The transcript record shapes (type discrimination, teammate-message
# unwrapping, content-block walk, tool_result text normalisation) live in
# transcript.py — the parse half of the parse/paint split; this module is the
# paint half. Delegating aliases keep the historical call sites/tests working
# (same pattern as render.py's format_code/render aliases into codefmt).
result_text = TR.result_text
input_summary = TR.input_summary

# Line-capped excerpt — shared with the codex stream (core/streamfmt.py).
cap = SF.cap

# Line caps per excerpt kind (how many lines of each block the mirror shows before
# "… (+N lines)"). These deliberately DIVERGE from plugins/codex/stream.py's caps —
# the two renderers weight their content differently; don't unify the values.
#
# An assistant MESSAGE and the final RESULT are deliberately NOT in this table:
# they are uncapped (flush_msg). They were capped at 40 lines, briefly 80, and
# then not at all (2026-07-25) — a long result is precisely the thing you opened
# the mirror to read, and an elision there just forwarded you to the drill-down.
# Everything still in the table is content you SKIM (a command's output, a
# request summary, a launch note), where a ceiling is what keeps the stream
# scannable. Note codex's own CAP_MSG stays, per the divergence note above.
CAP_PROMPT   = 24   # the spawn prompt
CAP_TEAMMSG  = 24   # an incoming teammate message
CAP_SENDMSG  = 12   # an outgoing SendMessage body
CAP_TOOL_REQ = 10   # a generic tool's request summary (query/url/...)
CAP_BODY     = 60   # a command's output body
CAP_JOB_NOTE = 8    # a bg/monitor launch note without a job id


class Renderer:
    """Renders one subagent's transcript records into mirror paint ops.

    Holds the mutable per-run render state that used to be substream.py module
    globals; the lifecycle module reads the cumulative rollup (tot_*, tool_n)
    for the footer and get/sets usage_last across the checkpoint handoff.
    """

    def __init__(self, *, log, agent, label, rgb, sub_fg,
                 op_tag, ctx_tag, take_subfg, spawn_fg_tailer, spawn_tailer,
                 agent_dur=None, team=False):
        self.log = log
        self.agent = agent
        self.label = label
        self.rgb = rgb
        self.sub_fg = sub_fg
        # Injected from the lifecycle module: model/effort tag + per-turn ctx tag
        # (both depend on model resolution, which stays in substream.py), and the
        # three tailer hooks (fg tee hand-off consume + the two spawners).
        self._op_tag = op_tag
        self._ctx_tag = ctx_tag
        self._take_subfg = take_subfg
        self._spawn_fg_tailer = spawn_fg_tailer
        self._spawn_tailer = spawn_tailer
        # The BLOCK BUILDERS: the shared child-agent presenter (core/agentblocks.py),
        # which owns the STAMP POLICY every block below depends on — which headers
        # carry `web` (surface in the lead's mirror), their `note` wording, `bubbled`
        # (re-bubbled as conversation, so agent scope drops the op), and the `who` +
        # model/ctx `tags` fields. Constructed HERE from the pieces the lifecycle
        # already injects (rather than injected itself), so this constructor's
        # contract is unchanged. `team` selects only the note's REGISTER: a teammate
        # rides the very same machinery as a Task-spawned subagent (the lifecycle's
        # `team` palette), and all that differs is the word the web calls it —
        # `Teammate @<name>` vs `Agent "<type>"`. `agent_dur` is how long this agent
        # has been running, for the finish note's `· 21m 31s` (the slot row holds the
        # start ts; the footer reads it the same way) — optional, and without it the
        # note simply carries no duration.
        self.blocks = AB.AgentStream(
            label=label, rgb=rgb,
            register=AB.REG_TEAM if team else AB.REG_AGENT,
            tags=op_tag, agent_dur=agent_dur)

        self.fg_live = {}         # tool_use_id -> the subfg hand-off rec, while its fg tailer runs
        self.pend = {}            # tool_use_id -> (kind, cmd)
        self.pending_msg = None   # latest assistant text, held so the LAST one (the result) can be labelled
        self.last_usage = None    # most recent assistant message.usage — drives the context-fill %
        self.last_model = None    # model id from that message — picks the context-window size
        self.cur_tag = ""         # colour-coded ctx token for the turn being processed right now
        self.turn_ctx_shown = False   # have we already emitted the ctx line for the current turn?
        self.pending_tag = ""     # ctx token snapshotted when the pending_msg was buffered (see below)

        # Cumulative usage over the WHOLE run, for the ended-footer rollup. Distinct from
        # last_usage (a single turn's snapshot, which drives the live ctx %): these sum every
        # assistant turn. tot_in is FRESH billed input (input_tokens + cache_creation) — the
        # tokens actually sent, not replayed; tot_cache is cache_read (cheap replay); tot_create
        # is the cache_creation share of tot_in, kept separately so cost_usd can bill its write
        # premium (5m TTL 1.25×; tot_create_1h is the 1h-TTL share, which bills 2×).
        # So the footer's "cache %" = tot_cache / (tot_in + tot_cache) is the
        # share of all context reads served from cache — a thrash/reuse signal. tool_n counts
        # tool_use blocks.
        #
        # Counted once per MESSAGE, not per line: one assistant message is written as one
        # JSONL line PER CONTENT BLOCK, each repeating that message's usage (input/cache
        # fields identical, output_tokens a growing snapshot — the last line has the final
        # count). Summing per line inflated the rollup ~2.2× (same bug as the main session's
        # bump_transcript, fixed there first). usage_last remembers the last counted id and
        # what was counted for it, so later lines of the same message only add the delta; it
        # is persisted in the state DB next to the byte checkpoint so a successor streamer
        # (idle-teammate restart) doesn't recount a message straddling the handoff.
        self.tot_in = 0
        self.tot_out = 0
        self.tot_cache = 0
        self.tot_create = 0
        self.tot_create_1h = 0    # 1-hour-TTL share of tot_create — bills 2× input, not 1.25×
        self.tool_n = 0
        self.usage_last = None    # O.usage_fold carry record {"id", "f"} of the last counted message

    # --- small line/block builders ------------------------------------------
    # Every BLOCK this renderer paints is built by self.blocks (core/agentblocks.py):
    # the chip's shape, the ctx tag that rides its header for the first op of a turn,
    # the ⧉ copy wiring the caller's `g` ties together, the markdown body an agent's
    # prose gets, and the stamps on all of it. What is left here is the one body line
    # that belongs to no block — a bg/monitor launch note (_res_job), which has no
    # header of its own to hang off.

    def gutter(self, text, g=None, web=False, bubbled=False):
        return SF.gutter(text, self.rgb, g=g, web=web, bubbled=bubbled)

    # --- transcript blocks ----------------------------------------------------

    def flush_msg(self, is_result=False):
        # Commit the buffered assistant message. The final one before the subagent ends
        # is its returned *result* (labelled ⇠ result); earlier ones are ✎ message. The
        # message's ctx % was snapshotted when it was buffered (last_usage may since have
        # advanced to the next turn), so emit that, not the live value.
        if self.pending_msg is None:
            return
        g = O.new_group(self.log)
        # The builder owns the rest, and the difference between the two is entirely
        # its POLICY (core/agentblocks.py): the ⇠ result is one of the two blocks the
        # web dashboard's main mirror surfaces (web=1) and the one whose note carries
        # the run's DURATION — producer-side, from the injected agent_dur, since the
        # note is written once and the page has no business re-wording a chip;
        # an intermediate ✎ message stays drill-down only. Both are `bubbled` (agent
        # scope re-bubbles them from this agent's own transcript) and both are
        # UNCAPPED, deliberately — the one excerpt in this renderer with no line
        # ceiling (see the CAP_* table's note). An agent's message and its returned
        # result are what the whole stream exists to deliver; eliding them sent the
        # reader to the drill-down for the substance, which is the opposite of the
        # summary's job.
        build = self.blocks.result if is_result else self.blocks.message
        O.emit(self.log, *build(self.pending_msg, g, ctx=self.pending_tag))
        self.pending_msg = None
        self.pending_tag = ""

    def render_compact(self, meta):
        # A "compact_boundary" system record: the conversation was compacted. Show it
        # inline (amber) so the gap in history makes sense. preTokens is always present;
        # postTokens is NOT always there, so degrade to "→ ?" when it's missing.
        self.flush_msg()
        O.emit(self.log, *self.blocks.compact(meta.get("preTokens"),
                                              meta.get("postTokens"),
                                              meta.get("trigger") or "?"))

    def render_prompt(self, text):
        # The spawn prompt is the other subagent block the web dashboard's main
        # mirror surfaces (web=True) — see flush_msg above and core/ops.py's "web".
        self.flush_msg()
        # A launch opens the agent's transcript with TWO user records, not one: the
        # brief, then a record that is NOTHING but the addressable-teammates roster
        # <system-reminder> (measured 2026-07-27, v2.1.220, on a 20-agent team). Both
        # parse as `prompt`, so each painted its own ⇢ prompt block — two identical
        # `Agent "X" launched` lines in the web feed, and only ONE of them opened onto
        # the brief ("why one is expandable where I can see the initial prompt and the
        # other is not"). The reminder-only one is machinery with nothing behind the
        # click, so it paints NOTHING: strip first, and a block with no brief left is
        # not a block. Not a web-side drop — the terminal pane showed the same empty
        # pair. (Ops already ON DISK can't be re-stamped; the web drops their
        # bodiless note in dashboard/opshtml/ops.py.)
        brief = cap(TR.strip_reminders(text).strip(), CAP_PROMPT)
        if not brief:
            return
        g = O.new_group(self.log)
        O.emit(self.log, *self.blocks.launch(brief, g))

    def render_teammsg(self, sender, body):
        # An incoming agent-team message (mail from another teammate or the lead).
        # `sender or "?"` here and not in the builder: an INCOMING message with no
        # sender still has to name one, where an OUTGOING SendMessage's recipient
        # comes from the tool input and is shown as written (_use_sendmsg).
        self.flush_msg()
        g = O.new_group(self.log)
        O.emit(self.log, *self.blocks.mail(True, sender or "?",
                                           cap(body.strip(), CAP_TEAMMSG), g))

    def render_message(self, text):
        text = text.strip()
        if not text:
            return
        self.flush_msg()          # commit the previous message; buffer this one
        self.pending_msg = text
        # Tie this turn's ctx % to its message (shown at flush). If the turn already
        # showed it on a tool line, don't repeat it.
        self.pending_tag = "" if self.turn_ctx_shown else self.cur_tag
        self.turn_ctx_shown = True

    def render_file(self, name_tool, inp, result=None, ctx="", failed=False, tid=None):
        label = FILE_LABEL.get(name_tool, "Read")
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        name = os.path.basename(path.rstrip("/")) or path or "?"
        # A read shows how much of the file it took ('' == the whole file); a mutation shows
        # its added/removed line counts plus the line range(s) it touched. All go before the
        # model tag so they survive truncation on a narrow pane. Extent/range come from the
        # tool_result (`result`); counts from the input. A failed op gets none of these
        # (diff_counts would count lines never written) — just the red verb + ✗ mark.
        added = removed = 0
        ext = rng = ""
        if not failed:
            if name_tool == "Read":
                ext = CT.read_extent(result.get("file") if isinstance(result, dict) else None, inp)
            else:
                added, removed = CT.diff_counts(name_tool, inp)
                rng = CT.edit_range(result.get("structuredPatch") if isinstance(result, dict) else None)
        # WHO did it — the agent's name/type — rides as the op's own field below, so a
        # Read/Update/Write is attributable to the subagent (or teammate) that ran it,
        # the same identity cue the builder puts on this agent's Bash header. The
        # gutter bar already carries the colour, but the explicit name is what the eye
        # reads. The one-liner itself is the shared core shape (streamfmt.file_line,
        # via agentblocks — same anatomy as the main session's file ops and codex
        # patches).
        # Same location-aware display as the main session's file ops
        # (streamfmt.file_display: ✎ scratchpad / dim out-of-project dir); the
        # tailer inherits the hook's cwd = the session directory, so the
        # default process-cwd baseline is the right one.
        disp, _loc = SF.file_display(path)
        # A subagent's file op runs the same OBSERVER registry as the main
        # agent's (fileobs.OBSERVERS — memory the one row; the match's cwd
        # default is this tailer's cwd = the session dir): each match bakes its
        # marker into the line before the emit; the kv snapshot happens AFTER
        # the emit, into the SAME per-session kv the main agent writes
        # (self.agent names the subagent — e.g. the note-writer). The mirror
        # block stays main-agent-only; this op only surfaces in the agent's
        # drill-down, but the Memory tab is team-wide.
        obs = FOBS.matches(path)
        is_mem = any(o.key == "memory" for o in obs)
        # The failure ✗ and those markers are this caller's own tail on the shared
        # one-liner (core/streamfmt.file_line, reached through the builder), in that
        # order; the who + model/effort + ctx chips ride as the op's own fields when
        # the row is built below.
        marks = ("  " + R.DIM + "✗" + RST) if failed else ""
        marks += "".join("  " + R.DIM + o.mark + RST for o in obs)
        line = self.blocks.file_text(label, disp, CT.FILE_RGB.get(label, O.SLATE),
                                     failed=failed, extent=ext, added=added,
                                     removed=removed, rng=rng, marks=marks)
        # Click-to-view, exactly like the main session's file ops (file_fmt.py owns
        # the block builder): stash the pre-rendered content under the agent's
        # tool_use_id, bake the /view hyperlink into the line (the OSC 8 sequence is
        # zero-width to wrap_gutter), and tag the gut op with "v" so the renderer
        # expands the block in place. A subagent transcript's tool_result rarely
        # carries the Read content/structuredPatch — view_ops falls back to the
        # disk re-read / input-strings difflib for those.
        vid = None
        if not failed and tid:
            from plugins.claude_code import file_fmt as FF
            line, vid = FF.stash_view(
                self.log, tid, name_tool, label, name, path, inp,
                result if isinstance(result, dict) else {}, line,
                who="substream render", extra={"agent": self.agent})
        O.emit(self.log, *self.blocks.file_row(line, view=vid, mem=is_mem, ctx=ctx))
        for o in obs:
            o.record(self.log, path, label, agent=self.agent)
        # Feed the session scoreboard so its files/+/- chips (and the tools breakdown)
        # reflect TEAM-WIDE file activity, not just the main session's own file ops
        # (claude-file-fmt.py skips agent_id calls — the substream owns their rendering,
        # and now their accounting too, mirroring how the ended-footer already folds each
        # agent's token spend into the scoreboard). `files` is a UNIQUE-path set, so an
        # agent re-touching a path — or touching one the main session already did — never
        # inflates it; added/removed sum. Handoff-safe: each transcript line is consumed
        # exactly once across the streamer chain (the `pos` checkpoint), so an idle-teammate
        # restart can't double-count, same as the per-streamer tool_n above. Emitted as a
        # plain `bump` (no meta) — the deltas are files/lines, not the tokens/cost that the
        # unattributed-bump anomaly guards.
        O.bump(self.log, tool=name_tool, file=path, added=added, removed=removed)

    # --- tool_use dispatch ------------------------------------------------------
    # One handler per tool kind, selected via the _USE table below (unknown tools
    # fall to _use_other). Each takes the same (name, inp, tid, ctx) unpacked view
    # of the tool_use block; adding a tool kind is one method + one registration.

    def _use_bash(self, name, inp, tid, ctx):
        cmd = inp.get("command", "")
        # An agent's shell command runs the same OBSERVER command plane as the
        # lead's (fileobs.cmd_matches — memory the one row; the cwd default is this
        # tailer's cwd = the session dir, as for its file ops). The marker + `mem`
        # flag ride the block HEADER here, because a command's memory-ness is a
        # property of the whole block; the kv snapshot happens at the RESULT, which
        # is where this transcript reveals the output a search's answer lives in.
        obs = FOBS.cmd_matches(cmd)
        marks = "".join("  " + R.DIM + o.mark + RST for o in obs)
        mem = FOBS.cmd_mem_flag(cmd, None, obs)
        if inp.get("run_in_background"):
            O.emit(self.log, *self.blocks.cmd_open(cmd, tid, background=True,
                                                   marks=marks, mem=mem, ctx=ctx))
            # A backgrounded command's output never reaches this transcript, so its
            # record is taken NOW, without one (same trade as the lead's bg path).
            self._observe(cmd, "")
            self.pend[tid] = ("bg", cmd)
        else:
            O.emit(self.log, *self.blocks.cmd_open(cmd, tid, marks=marks, mem=mem,
                                                   ctx=ctx))
            rec = self._take_subfg(tid) if (self.sub_fg and tid) else None
            if rec and self._spawn_fg_tailer(tid, rec, cmd):
                # A live fg tailer now owns this command's OUTPUT + finish chip; we
                # only hand it the outcome (below) and skip re-rendering the body.
                self.fg_live[tid] = rec
                self.pend[tid] = ("fg-live", cmd)
            else:
                self.pend[tid] = ("fg", cmd)

    def _observe(self, cmd, output):
        """Run the OBSERVER command plane's RECORD half for one of this agent's Bash
        calls, attributed to it (`agent=self.agent` — the Memory tab is team-wide,
        unlike the mirror block). The lead's twin is cmd_fmt._observe; both run after
        the block's emit, and the record functions are parked-guarded."""
        for o in FOBS.cmd_matches(cmd):
            o.cmd_record(self.log, cmd, None, output, self.agent)

    def _use_file(self, name, inp, tid, ctx):
        # Defer to the result: absolute line info — a Read's EXTENT
        # (startLine/numLines/totalLines) and an edit's touched hunks (structuredPatch)
        # — lives only on the tool_result, which lands in the very next record, so
        # ordering is preserved. Carry (tool, input, ctx) for rendering there.
        self.pend[tid] = ("file", (name, inp, ctx))

    def _use_monitor(self, name, inp, tid, ctx):
        # PAINTS NOTHING, deliberately — the one tool this renderer defers on.
        # `monitor_fmt.py` has no agent_id guard (unlike every other formatter,
        # and by design — CLAUDE.md's main-session-only invariant names it as the
        # exception): it renders an AGENT's Monitor too, keyed by the taskId, and
        # it is the richer block by far — description, lifetime, the streamed
        # events, the finish chip — because the taskId is what the tailer it
        # spawns paints under. Emitting our own header + command here on top of
        # that put TWO monitor blocks and TWO copies of the command in the stream
        # for one Monitor call, the second one holding all the output; the first
        # was a stub that could never receive any.
        #
        # The RESULT is silent for the same reason (_RESULT below): its "Monitor
        # started (task <id>, …)" text is the taskId the other block's header
        # already states. Note this is not the fg/bg shape — there `cmd_fmt`
        # SKIPS agent events, so the substream owns those blocks and the tailer
        # joins its copy group.
        self.pend[tid] = ("monitor", inp.get("command", ""))

    def _use_sendmsg(self, name, inp, tid, ctx):
        # Mail this teammate sends to another teammate / the lead. Show recipient +
        # the message body; the tool_result is just a "{success:true,…}" ack (noise),
        # so it's suppressed in on_tool_result. The (recipient, text) pair comes from
        # its owner (TR.mail_send) — the web reads the same call out of the transcript
        # as a message bubble, and the two must not disagree about which field holds
        # the body.
        to, text = TR.mail_send(inp)
        g = O.new_group(self.log)
        O.emit(self.log, *self.blocks.mail(False, to, cap(text.strip(), CAP_SENDMSG),
                                           g, ctx=ctx))
        self.pend[tid] = ("sendmsg", "")

    def _use_agent(self, name, inp, tid, ctx):
        # A nested subagent gets its OWN block via its own SubagentStart/Stop hooks.
        sub = (inp.get("subagent_type") or "subagent")
        tag = self._op_tag()
        st = "⊂ spawns " + sub + ("  " + tag if tag else "") + ("  " + ctx if ctx else "")
        O.emit(self.log, O.gut(R.DIM + st + RST, self.rgb))
        self.pend[tid] = ("agent", "")

    def _use_other(self, name, inp, tid, ctx):
        # A group is minted UNCONDITIONALLY, even for a request-less tool, because
        # the block's other half is its RESULT — emitted at the tool_result, which
        # has no group of its own to fall back on (unlike a Bash block, whose
        # tool_use_id IS the group). Carried through `pend` so _res_body can put the
        # answer behind the same click as the question; ungrouped, it landed in the
        # feed as a loose row of its own and the block you clicked held only the
        # query ("I should see the result of the ToolSearch").
        g = O.new_group(self.log)
        req = input_summary(inp)                 # show the request (e.g. the query/url)
        O.emit(self.log, *self.blocks.tool_open(
            name, cap(req, CAP_TOOL_REQ) if req else "", g, ctx=ctx))
        self.pend[tid] = ("other", g)

    # tool name -> use handler; unknown names fall to _use_other. File tools share
    # one deferred handler (rendered at the result, which carries extent/range).
    _USE = {"Bash": _use_bash, "Monitor": _use_monitor, "SendMessage": _use_sendmsg,
            "Task": _use_agent, "Agent": _use_agent}
    _USE.update(dict.fromkeys(FILE_LABEL, _use_file))

    def on_tool_use(self, b):
        self.tool_n += 1              # count every tool call, for the ended-footer rollup
        self.flush_msg()
        ctx = ""                      # ctx rides the FIRST op header of a turn (if no msg led it)
        if not self.turn_ctx_shown:
            ctx = self.cur_tag
            self.turn_ctx_shown = True
        name = b.get("name") or ""
        inp = b.get("input") or {}
        tid = b.get("id")
        self._USE.get(name, Renderer._use_other)(self, name, inp, tid, ctx)

    # --- tool_result dispatch -----------------------------------------------------
    # One handler per pend KIND (what on_tool_use recorded), selected via _RESULT;
    # kinds without an entry fall to the generic fg/other body render.

    def _res_file(self, kind, cmd, b, tur, tid):
        # Deferred from on_tool_use: render the file op now, with the extent (Read) or
        # touched range (edit) the result carries. cmd holds the saved (tool, input).
        # A FAILED op (is_error) counts the path + tool but NO line deltas, matching
        # the main session's claude-file-fmt.py — otherwise a failed Write would
        # inflate +added with lines it never wrote.
        name_tool, saved_inp, saved_ctx = cmd if isinstance(cmd, tuple) else ("Read", {}, "")
        self.render_file(name_tool, saved_inp, tur, saved_ctx,
                         failed=bool(b.get("is_error")), tid=tid)

    def _res_silent(self, kind, cmd, b, tur, tid):
        return                                      # already shown / handled elsewhere

    def _res_fg_live(self, kind, cmd, b, tur, tid):
        # A live fg tailer streamed this command's output and owns its finish chip.
        # Hand it the real outcome (this is the ONLY place the subagent's transcript
        # reveals pass/fail) via the "done:" sentinel the tailer polls, and SUPPRESS
        # our own body render so the block isn't drawn twice. The tailer computes the
        # duration itself; fallback_body covers the (unexpected) empty-tee case. Still
        # feed the team-wide command tally, exactly as the plain fg path does below.
        rec = self.fg_live.pop(tid, None)
        err = bool(b.get("is_error"))
        body = result_text(b.get("content")).rstrip("\n")
        if rec:
            fb = R.emphasize(R.unescape(cap(body, CAP_BODY))) if body else R.DIM + "(no output)" + RST
            if S.hand_put(self.log, "done:" + rec["done"], {"failed": err, "fallback_body": fb}):
                A.state_file(self.log, "state:done:" + rec["done"], "write", {"failed": err})
        O.bump(self.log, tool="Bash", commands=1, **({"failed": 1} if err else {}))
        self._observe(cmd, body)             # the memory kv record (see _use_bash)

    def _res_job(self, kind, cmd, b, tur, tid):
        txt = result_text(b.get("content"))
        if kind == "bg":
            # A background Bash launch is a command — count it (its finish is owned
            # by the tailer), same as the main session's _render_background.
            O.bump(self.log, tool="Bash", commands=1)
        m = re.search(r"with ID:\s*([^\s.]+)", txt)
        if m:
            # Pass the block's ⧉ copy group (this tool_use_id) so the tailer's
            # streamed output/finish ops join the header+code we already emitted.
            self._spawn_tailer(kind, m.group(1), cmd, group=tid)
        elif txt.strip():
            O.emit(self.log, self.gutter(cap(txt.strip(), CAP_JOB_NOTE)))

    def _res_body(self, kind, cmd, b, tur, tid):
        # fg / other: show the command's output (banners emphasised — this is real
        # command output, unlike the messages/prompts that share gutter()).
        # fg output joins this command's ⧉ copy group (the tool_use_id) so ⧉out copies it.
        # A generic tool's output joins ITS block the same way — through the group
        # `_use_other` minted and parked in `pend` (there is no tool_use_id-keyed op
        # for it to key on). `cmd` is that group for an "other", the command for an
        # "fg": the pend payload is per-kind by design (a "file" carries a tuple).
        txt = result_text(b.get("content"))
        g = tid if kind == "fg" else (cmd or None)
        body = txt.rstrip("\n")
        err = bool(b.get("is_error"))
        # Same closing shape either way (output / the dim `(no output)` stand-in /
        # a red failure mark), asked for through the builder each block OPENED with,
        # so a future divergence has a place to live. `body` stays UNCAPPED below —
        # only the painted excerpt is capped; the observer's record wants it whole.
        close = self.blocks.cmd_close if kind == "fg" else self.blocks.tool_close
        O.emit(self.log, *close(g, cap(body, CAP_BODY), failed=err))
        if kind == "fg":
            # Team-wide command accounting, mirroring the main session's
            # claude-cmd-fmt.py — which deliberately SKIPS any agent_id event (the
            # substream owns subagent rendering AND, now, its command tally). Without
            # this, a subagent's Bash calls and their FAILURES never reached the
            # scoreboard's ▪ `N cmds (M✗)` (only its file ops were team-wide, via
            # render_file). Count every foreground Bash call + its failure, exactly as
            # _render_finished does for the lead.
            O.bump(self.log, tool="Bash", commands=1, **({"failed": 1} if err else {}))
            self._observe(cmd, body)         # the memory kv record (see _use_bash)

    # pend kind -> result handler; anything else (fg / other) is a body render.
    # `monitor` is SILENT on both halves — monitor_fmt.py owns an agent's monitor
    # block end to end (see _use_monitor); _res_job stays for `bg`, which the
    # substream does own and whose result carries the taskId its tailer needs.
    _RESULT = {"file": _res_file, "agent": _res_silent, "sendmsg": _res_silent,
               "fg-live": _res_fg_live, "bg": _res_job, "monitor": _res_silent}

    def on_tool_result(self, b, tur=None):
        self.flush_msg()
        tid = b.get("tool_use_id")
        kind, cmd = self.pend.pop(tid, ("other", ""))
        self._RESULT.get(kind, Renderer._res_body)(self, kind, cmd, b, tur, tid)

    # --- transcript line pump -----------------------------------------------------

    def handle_line(self, s):
        # Parse via transcript.parse_line (the ONE reader of the record shapes);
        # this method is pure record→paint dispatch. A "results" record's
        # `texts` (a parent transcript's user text blocks) are deliberately
        # ignored here — the pre-split renderer never painted them either;
        # timeline() is their consumer.
        rec = TR.parse_line(s)
        if rec is None:
            return
        kind = rec["kind"]
        if kind == "bad":
            A.error(self.log, "handle_line", {"agent": self.agent,
                                              "line": rec["raw"][:300]})
        elif kind == "compact":
            self.render_compact(rec["meta"])
        elif kind == "prompt":
            self.render_prompt(rec["text"])
        elif kind == "teammsg":
            self.render_teammsg(rec["sender"], rec["body"])
        elif kind == "results":
            for blk in rec["blocks"]:
                self.on_tool_result(blk, rec["tur"])
        elif kind == "assistant":
            u = rec["usage"]
            if u is not None:                 # refresh the live context fill for this turn
                self.last_usage = u
                self.last_model = rec["model"] or self.last_model
                # Accumulate for the ended-footer rollup — once per message.id, deltas
                # only for repeat lines of the same message (O.usage_fold, the shared
                # dedup — see usage_last above).
                d, self.usage_last = ACC.usage_fold(rec["id"], ACC.usage_fields(u),
                                                    self.usage_last)
                self.tot_in += d[0]; self.tot_out += d[1]; self.tot_cache += d[2]
                self.tot_create += d[3]; self.tot_create_1h += d[4]
            self.cur_tag = self._ctx_tag()
            self.turn_ctx_shown = False       # each turn shows its ctx % once (msg or tool)
            for bkind, blk in rec["blocks"]:
                if bkind == "text":
                    self.render_message(blk)
                else:
                    self.on_tool_use(blk)
