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

from core import ops as O
from core import render as R
from core import state as S
from core import streamfmt as SF
from plugins.claude_code import accounting as ACC
from plugins.claude_code import tools as CT
from plugins.claude_code import transcript as TR

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)
RST = R.RST
kfmt = O.kfmt        # compact token count: 124000 -> "124k"
AMBER = R.fg(*O.YELLOW)   # the compact-boundary notice colour

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
CAP_MSG      = 40   # an assistant message / final result
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
                 op_tag, ctx_tag, take_subfg, spawn_fg_tailer, spawn_tailer):
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

    def chip(self, glyph, kind, ctx="", g=None, lk=None, web=False):
        # ctx (e.g. "ctx 42% · 84k/200k") rides in the chip header for the first op of a
        # turn, rather than on its own gutter line below it. g ties a block's header + its
        # code/gut body ops into one ⧉ copy group — a tool_use_id for commands, else a
        # fresh O.new_group() id for a message/prompt/mail block (lk=O.COPY_ALL then gives
        # it a single whole-block ⧉copy link). Same mechanism as the main session's fg/bg
        # blocks (core/copy.py), just double-guttered here. Shape shared with the codex
        # stream via core/streamfmt.py; the model tag + ctx ride as trailing tags.
        return SF.chip(self.label, glyph, kind, self.rgb,
                       tags=(self._op_tag(), ctx), g=g, lk=lk, web=web)

    def gutter(self, text, g=None, web=False):
        return SF.gutter(text, self.rgb, g=g, web=web)

    def msg_gutter(self, text, g=None, web=False):
        # Assistant text is markdown -> render the subset (bold/italic/code/headings/bullets).
        return O.gut(R.markdown(R.unescape(text)), self.rgb, g=g, web=web)

    # --- transcript blocks ----------------------------------------------------

    def flush_msg(self, is_result=False):
        # Commit the buffered assistant message. The final one before the subagent ends
        # is its returned *result* (labelled ⇠ result); earlier ones are ✎ message. The
        # message's ctx % was snapshotted when it was buffered (last_usage may since have
        # advanced to the next turn), so emit that, not the live value.
        if self.pending_msg is None:
            return
        glyph, kind = ("⇠", "result") if is_result else ("✎", "message")
        # The final result is one of the two subagent blocks the web dashboard's
        # main mirror surfaces (web=True); intermediate ✎ messages stay drill-down
        # only — see core/ops.py's "web" field and dashboard/opshtml.op_items.
        g = O.new_group(self.log)
        O.emit(self.log,
               self.chip(glyph, kind, self.pending_tag, g=g, lk=O.COPY_ALL, web=is_result),
               self.msg_gutter(cap(self.pending_msg, CAP_MSG), g=g, web=is_result))
        self.pending_msg = None
        self.pending_tag = ""

    def render_compact(self, meta):
        # A "compact_boundary" system record: the conversation was compacted. Show it
        # inline (amber) so the gap in history makes sense. preTokens is always present;
        # postTokens is NOT always there, so degrade to "→ ?" when it's missing.
        self.flush_msg()
        pre, post, trig = meta.get("preTokens"), meta.get("postTokens"), meta.get("trigger") or "?"
        txt = "⟳ compacted"
        if pre:
            txt += f" · {kfmt(pre)} → " + (kfmt(post) if post else "?")
        txt += f" ({trig})"
        O.emit(self.log, O.gut(AMBER + txt + RST, self.rgb))

    def render_prompt(self, text):
        # The spawn prompt is the other subagent block the web dashboard's main
        # mirror surfaces (web=True) — see flush_msg above and core/ops.py's "web".
        self.flush_msg()
        g = O.new_group(self.log)
        O.emit(self.log, self.chip("⇢", "prompt", g=g, lk=O.COPY_ALL, web=True),
               self.gutter(cap(text.strip(), CAP_PROMPT), g=g, web=True))

    def render_teammsg(self, sender, body):
        # An incoming agent-team message (mail from another teammate or the lead).
        self.flush_msg()
        g = O.new_group(self.log)
        O.emit(self.log, self.chip("✉", "from " + (sender or "?"), g=g, lk=O.COPY_ALL),
               self.gutter(cap(body.strip(), CAP_TEAMMSG), g=g))

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
        # Lead with WHO did it — the agent's name/type in its own colour — so a Read/Update/
        # Write is attributable to the subagent (or teammate) that ran it, the same identity
        # cue chip() puts on this agent's Bash ops. The gutter bar already carries the colour,
        # but the explicit name is what the eye reads. The one-liner itself is the shared
        # core builder (streamfmt.file_line — same anatomy as the main session's file ops
        # and codex patches).
        who = R.fg(*self.rgb) + self.label + " " + RST
        # Same location-aware display as the main session's file ops
        # (streamfmt.file_display: ✎ scratchpad / dim out-of-project dir); the
        # tailer inherits the hook's cwd = the session directory, so the
        # default process-cwd baseline is the right one.
        disp, _loc = SF.file_display(path)
        line = who + SF.file_line(label, disp, CT.FILE_RGB.get(label, O.SLATE),
                                  failed=failed, extent=ext,
                                  added=added, removed=removed, rng=rng)
        if failed:
            line += "  " + R.DIM + "✗" + RST
        tag = self._op_tag()
        if tag:
            line += "  " + R.DIM + tag + RST
        if ctx:
            line += "  " + R.DIM + ctx + RST
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
        O.emit(self.log, O.gut(line, self.rgb, view=vid))
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
        if inp.get("run_in_background"):
            O.emit(self.log, self.chip("▷", "background", ctx, g=tid), O.code(cmd, g=tid))
            self.pend[tid] = ("bg", cmd)
        else:
            O.emit(self.log, self.chip("▶", "foreground", ctx, g=tid), O.code(cmd, g=tid))
            rec = self._take_subfg(tid) if (self.sub_fg and tid) else None
            if rec and self._spawn_fg_tailer(tid, rec, cmd):
                # A live fg tailer now owns this command's OUTPUT + finish chip; we
                # only hand it the outcome (below) and skip re-rendering the body.
                self.fg_live[tid] = rec
                self.pend[tid] = ("fg-live", cmd)
            else:
                self.pend[tid] = ("fg", cmd)

    def _use_file(self, name, inp, tid, ctx):
        # Defer to the result: absolute line info — a Read's EXTENT
        # (startLine/numLines/totalLines) and an edit's touched hunks (structuredPatch)
        # — lives only on the tool_result, which lands in the very next record, so
        # ordering is preserved. Carry (tool, input, ctx) for rendering there.
        self.pend[tid] = ("file", (name, inp, ctx))

    def _use_monitor(self, name, inp, tid, ctx):
        cmd = inp.get("command", "")
        O.emit(self.log, self.chip("◉", "monitor", ctx, g=tid), O.code(cmd, g=tid))
        self.pend[tid] = ("monitor", cmd)

    def _use_sendmsg(self, name, inp, tid, ctx):
        # Mail this teammate sends to another teammate / the lead. Show recipient +
        # the message body; the tool_result is just a "{success:true,…}" ack (noise),
        # so it's suppressed in on_tool_result.
        to = inp.get("to") or inp.get("recipient") or "?"
        # message/content may be a plain string OR a structured content block
        # (dict / list of blocks) — normalise through result_text so .strip() never
        # hits a dict (that AttributeError crashed the streamer mid-run, dropping the
        # agent's un-bumped token tail; reconcile_spend recovers it, but don't crash).
        text = result_text(inp.get("message") or inp.get("content") or inp.get("summary") or "")
        g = O.new_group(self.log)
        O.emit(self.log, self.chip("✉", "to " + to, ctx, g=g, lk=O.COPY_ALL),
               self.gutter(cap(text.strip(), CAP_SENDMSG), g=g))
        self.pend[tid] = ("sendmsg", "")

    def _use_agent(self, name, inp, tid, ctx):
        # A nested subagent gets its OWN block via its own SubagentStart/Stop hooks.
        sub = (inp.get("subagent_type") or "subagent")
        tag = self._op_tag()
        st = "⊂ spawns " + sub + ("  " + tag if tag else "") + ("  " + ctx if ctx else "")
        O.emit(self.log, O.gut(R.DIM + st + RST, self.rgb))
        self.pend[tid] = ("agent", "")

    def _use_other(self, name, inp, tid, ctx):
        req = input_summary(inp)                 # show the request (e.g. the query/url)
        g = O.new_group(self.log) if req else None
        O.emit(self.log, self.chip("·", name or "tool", ctx, g=g, lk=O.COPY_ALL if g else None))
        if req:
            O.emit(self.log, self.gutter(cap(req, CAP_TOOL_REQ), g=g))
        self.pend[tid] = ("other", "")

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
        if rec:
            body = result_text(b.get("content")).rstrip("\n")
            fb = R.emphasize(R.unescape(cap(body, CAP_BODY))) if body else R.DIM + "(no output)" + RST
            if S.hand_put(self.log, "done:" + rec["done"], {"failed": err, "fallback_body": fb}):
                A.state_file(self.log, "state:done:" + rec["done"], "write", {"failed": err})
        O.bump(self.log, tool="Bash", commands=1, **({"failed": 1} if err else {}))

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
        txt = result_text(b.get("content"))
        g = tid if kind == "fg" else None
        body = txt.rstrip("\n")
        if body:
            O.emit(self.log, O.gut(R.emphasize(R.unescape(cap(body, CAP_BODY))), self.rgb, g=g))
        else:
            O.emit(self.log, O.gut(R.DIM + "(no output)" + RST, self.rgb, g=g))
        err = bool(b.get("is_error"))
        if err:
            O.emit(self.log, O.gut(R.fg(*O.RED) + "■ failed" + RST, self.rgb, g=g))
        if kind == "fg":
            # Team-wide command accounting, mirroring the main session's
            # claude-cmd-fmt.py — which deliberately SKIPS any agent_id event (the
            # substream owns subagent rendering AND, now, its command tally). Without
            # this, a subagent's Bash calls and their FAILURES never reached the
            # scoreboard's ▪ `N cmds (M✗)` (only its file ops were team-wide, via
            # render_file). Count every foreground Bash call + its failure, exactly as
            # _render_finished does for the lead.
            O.bump(self.log, tool="Bash", commands=1, **({"failed": 1} if err else {}))

    # pend kind -> result handler; anything else (fg / other) is a body render.
    _RESULT = {"file": _res_file, "agent": _res_silent, "sendmsg": _res_silent,
               "fg-live": _res_fg_live, "bg": _res_job, "monitor": _res_job}

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
