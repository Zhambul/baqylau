#!/usr/bin/env python3
# claude-substream.py AGENT_ID TRANSCRIPT_PATH MIRROR_LOG SLOT AGENT_TYPE [PALETTE]
#
# Detached streamer for a SUBAGENT (Task/Agent tool). A subagent fires real hooks
# for each tool it runs, but those alone can't show its *messages* (assistant text)
# or keep messages/commands/results in order. Its full transcript can, though:
# `<dir>/<session>/subagents/agent-<id>.jsonl` records — in order — the prompt, the
# subagent's text messages, every tool_use, and every tool_result. So this process
# (spawned by the SubagentStart hook) tails that transcript and renders all of it
# into the mirror in the subagent's colour, giving full visibility.
#
# Division of labour: the SubagentStart hook claims the colour slot and writes the
# "▶ <type> · <desc>" header; this streamer writes everything below it (prompt,
# messages, commands+output, file ops, the final result) and the "■ <type> ended"
# footer, then releases the slot. A subagent's BACKGROUND command / monitor is
# streamed by claude-stream.py with a DOUBLE gutter (outer = this subagent's
# colour, inner = the job's own palette slot) so nested parallel jobs stay distinct.
import json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_model as M
import claude_render as R
import claude_ops as O
import claude_state as S
import claude_tail as T

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

AGENT   = sys.argv[1]
TPATH   = sys.argv[2]
LOG     = sys.argv[3]
SLOT    = int(sys.argv[4])
ATYPE   = sys.argv[5] if len(sys.argv) > 5 else "agent"
# Which palette to colour this block with. An in-process agent-team TEAMMATE rides
# the very same machinery as an ordinary subagent (same "sub" slot + sub.* markers,
# same transcript layout) — only the colour family differs, so it's "team" instead
# of "sub". Everything else (slot index, completion sentinel, footer) is identical.
PALETTE = sys.argv[6] if len(sys.argv) > 6 else "sub"
# The task description (from the PreToolUse payload), passed through by the start hook.
DESC    = sys.argv[7] if len(sys.argv) > 7 else ""
# What each per-operation line labels this agent with. "general-purpose" is a
# meaningless catch-all in the mirror, so for it we substitute the task description
# (e.g. "Get Bali weather") when one is known; every other type keeps its own name,
# and the header ("▶ general-purpose · <desc>") is untouched — this is the body only.
LABEL = DESC if (ATYPE == "general-purpose" and DESC) else ATYPE

SUB_RGB = claude_slots.color(PALETTE, SLOT)
RST  = R.RST
HERE = os.path.dirname(os.path.abspath(__file__))


# Context-fill thresholds (percent) for the live per-turn % shown on each turn:
# < WARN green, < CRIT amber, else red. Tunable per workload via the env, same as
# CLAUDE_MIRROR_BIAS — e.g. a [1m] session wants higher cutoffs.
CTX_WARN = M.int_env("CLAUDE_MIRROR_CTX_WARN", 30)
CTX_CRIT = M.int_env("CLAUDE_MIRROR_CTX_CRIT", 60)
CTX_GREEN = R.fg(*O.GREEN)
CTX_AMBER = R.fg(*O.YELLOW)
CTX_RED   = R.fg(*O.RED)

# Model / effort / context-window resolution lives in claude_model.py; this block
# just binds it to THIS agent's identity (its meta.json, definition file, and the
# parent session's transcript).
RESOLVED_MODEL = None      # authoritative model id (with [1m]) read from the parent
META = M.agent_meta(TPATH, AGENT)
# Look up the definition by its real name (customAgentType) — the short agentType a
# teammate reports ("container") won't match the def's `name:`/filename ("task-container").
DEF_TYPE = META.get("customAgentType") or ATYPE
AGENT_DEF_FILE  = M.agent_def_file(DEF_TYPE)
AGENT_DEF_MODEL = M.def_field(AGENT_DEF_FILE, "model")
SETTINGS_MODEL  = M.settings_field("model")
SESSION_MODEL   = M.session_model(TPATH)
EFFORT_CFG      = M.effort_config(AGENT_DEF_FILE)

short_model = M.short_model


def disp_model():
    # The model to display, best-known-first: the agent's own resolved id > this agent's
    # configured model (meta) or an explicit frontmatter override > the parent session's
    # version (for inheriting agents, before the first turn) > footer id > config alias.
    return (last_model or META.get("model") or AGENT_DEF_MODEL or SESSION_MODEL
            or RESOLVED_MODEL or SETTINGS_MODEL)


def effort():
    # Configured effort (env > frontmatter > session) if any, else the running model's
    # default — so an agent that inherits shows the level it actually reasons at.
    return EFFORT_CFG or M.model_default_effort(disp_model())


def op_tag():
    # "opus-4.8·high" — the model this agent is running plus the resolved effort.
    # Constant per agent; appended to every operation header.
    return "·".join(x for x in (short_model(disp_model()), effort()) if x)

# Where the subagent's transcript lives. The completion signal and the resume
# checkpoint live on this agent's record in the per-session state DB
# (claude_state.agents — was sub.done.* / sub.pos.* files in the .slots dir):
#   done      — set to 1 by the SubagentStop hook; this streamer polls it.
#   pos       — byte offset of the last fully consumed transcript line. An idle
#               TEAMMATE fires SubagentStop (this streamer finalises and dies) and a
#               later message fires a fresh SubagentStart, which spawns a NEW streamer
#               for the SAME transcript — without the checkpoint that streamer would
#               re-render the whole history. Deliberately NOT cleared in cleanup():
#               it must outlive the streamer to make the resume seamless.
BASE = TPATH[:-6] if TPATH.endswith(".jsonl") else TPATH
SUBDIR = os.path.join(BASE, "subagents")
JSONL  = os.path.join(SUBDIR, f"agent-{AGENT}.jsonl")
META_PATH = os.path.join(SUBDIR, f"agent-{AGENT}.meta.json")
STATE_KEY = "state:agent." + AGENT          # audit label for checkpoint rows
USAGE_KEY = "usage_last:" + AGENT           # kv slot for the usage dedup record


def cancelled_by_user():
    # A manually killed/cancelled subagent fires NO SubagentStop hook — the same
    # gap documented throughout this codebase for interrupts (claude-tab-status.py's
    # idle-watch, claude-cmd-pre.py's cancelled-foreground-command fix) — so the
    # done flag never flips and this tailer would otherwise hang until the 6h backstop
    # below, leaving the tab stuck blue the whole time. But Claude Code stamps
    # `stoppedByUser: true` onto this agent's meta.json sidecar the moment that
    # happens (confirmed empirically), giving a fast, reliable end signal instead.
    try:
        with open(META_PATH, encoding="utf-8") as fh:
            return bool(json.load(fh).get("stoppedByUser"))
    except Exception:
        return False

# Verbs + colours for file ops — the shared claude_ops table (claude-file-fmt.py
# renders the main session's file ops with the same).
FILE_LABEL = O.FILE_LABEL
FILE_COL   = {verb: R.fg(*rgb) for verb, rgb in O.FILE_RGB.items()}

# A message DELIVERED to this teammate appears in its transcript as a plain user
# record whose text is wrapped in <teammate-message teammate_id="<sender>" …>BODY
# </teammate-message> (the very first one is the lead's spawn prompt). We render it
# as "✉ from <sender>" + the unwrapped body, rather than as a raw ⇢ prompt.
TEAMMSG = re.compile(r'^\s*<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>\s*$', re.S)
_TM_ID  = re.compile(r'teammate_id="([^"]*)"')


def chip(glyph, kind, ctx=""):
    # ctx (e.g. "ctx 42% · 84k/200k") rides in the chip header for the first op of a
    # turn, rather than on its own gutter line below it.
    tag = op_tag()
    s = f"{LABEL} {glyph} {kind}" + (f"  {tag}" if tag else "") + (f"  {ctx}" if ctx else "")
    return O.label(s, SUB_RGB)


def cap(text, n):
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    more = len(lines) - n
    return "\n".join(lines[:n]) + f"\n… ({more} more line{'s' if more != 1 else ''})"


def gutter(text):
    return O.gut(R.unescape(text), SUB_RGB)


def msg_gutter(text):
    # Assistant text is markdown -> render the subset (bold/italic/code/headings/bullets).
    return O.gut(R.markdown(R.unescape(text)), SUB_RGB)


kfmt = O.kfmt        # compact token count: 124000 -> "124k"


def model_ctx():
    # Context window for the fill %, derived purely from config/model — NO empirical
    # self-correct. Precedence, first that resolves wins:
    #   0. CLAUDE_CODE_DISABLE_1M_CONTEXT — Claude Code's own kill-switch, caps at 200k
    #   1. RESOLVED_MODEL — authoritative id from the parent transcript (footer only)
    #   2. AGENT_DEF_MODEL — an explicit `model:` in this agent's definition frontmatter
    #   3. last_model — the bare id the agent actually ran (family is reliable; the
    #      known-1M table covers Opus 4.8 etc. even though the [1m] suffix is stripped)
    #   4. SETTINGS_MODEL — the session default, for agents that inherit
    return M.context_window(RESOLVED_MODEL, AGENT_DEF_MODEL, last_model, SETTINGS_MODEL)


def ctx_used():
    # The occupied context window for the latest assistant turn: every input token the
    # model saw — fresh + just-cached + replayed-from-cache. output_tokens is excluded
    # (that's what it produced back, not context). 0 if no usage seen yet.
    if not last_usage:
        return 0
    return (last_usage.get("input_tokens", 0)
            + last_usage.get("cache_creation_input_tokens", 0)
            + last_usage.get("cache_read_input_tokens", 0))


def ctx_tag():
    # Plain "ctx 42% · 84k/200k" for the current turn, or "" if no usage. Rendered as
    # dark text inside the operation chip (see chip()), so no inline threshold colour —
    # the chip's own solid background carries the identity hue.
    used = ctx_used()
    if used <= 0:
        return ""
    mx = model_ctx()
    return f"ctx {used * 100 // mx}% · {kfmt(used)}/{kfmt(mx)}"


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("type")
                if t == "text" or isinstance(b.get("text"), str):
                    parts.append(b.get("text", ""))
                elif t == "tool_reference":                 # ToolSearch result
                    parts.append("→ loaded tool: " + str(b.get("tool_name", "")))
                elif t == "image":
                    parts.append("[image]")
                else:                                        # unknown block -> show it
                    try:
                        parts.append(json.dumps(b, ensure_ascii=False))
                    except Exception:
                        parts.append(str(b))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    return str(content)


def input_summary(inp):
    # Compact "key: value" view of a tool's input, so the REQUEST is visible (e.g.
    # a WebSearch query, a WebFetch url). Used for tools we don't render specially.
    if not isinstance(inp, dict) or not inp:
        return ""
    lines = []
    for k, v in inp.items():
        vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {vs}")
    return "\n".join(lines)


alive = S.pid_alive                 # EPERM (foreign-owned) counts as alive


def spawn_tailer(kind, taskid, cmd=""):
    # Stream a subagent's background/monitor job with a DOUBLE gutter (outer = this
    # subagent's colour, inner = the job's own palette slot). claude-stream.py argv:
    #   KIND TASKID LOG SLOT SIG OUTER
    streamer = os.path.join(HERE, "claude-stream.py")
    if not (taskid and os.path.exists(streamer)):
        return
    slot, marker = claude_slots.claim(kind, LOG)
    sig = ""
    if kind == "monitor":
        toks = re.findall(r"[\w./:@=+-]{5,}", cmd or "")
        sig = max(toks, key=len) if toks else ""
    outer = ",".join(str(x) for x in SUB_RGB)
    try:
        proc = subprocess.Popen(
            [sys.executable, streamer, kind, taskid, LOG, str(slot), sig, outer],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        claude_slots.set_owner(marker, proc.pid)
        A.spawn(LOG, proc.pid, [streamer, kind, taskid, str(slot)],
                purpose=f"stream:{kind} (nested under agent {AGENT[:8]})")
    except Exception:
        A.error(LOG, "spawn_tailer", {"kind": kind, "taskid": taskid, "agent": AGENT})
        claude_slots.release(kind, LOG, slot, os.getpid())


# --- rendering of transcript blocks --------------------------------------------
pend = {}                 # tool_use_id -> (kind, cmd)
pending_msg = None        # latest assistant text, held so the LAST one (the result) can be labelled
last_usage = None         # most recent assistant message.usage — drives the context-fill %
last_model = None         # model id from that message — picks the context-window size
cur_tag = ""              # colour-coded ctx token for the turn being processed right now
turn_ctx_shown = False    # have we already emitted the ctx line for the current turn?
pending_tag = ""          # ctx token snapshotted when the pending_msg was buffered (see below)

# Cumulative usage over the WHOLE run, for the ended-footer rollup. Distinct from
# last_usage (a single turn's snapshot, which drives the live ctx %): these sum every
# assistant turn. tot_in is FRESH billed input (input_tokens + cache_creation) — the
# tokens actually sent, not replayed; tot_cache is cache_read (cheap replay); tot_create
# is the cache_creation share of tot_in, kept separately so cost_usd can bill its 1.25×
# write premium. So the footer's "cache %" = tot_cache / (tot_in + tot_cache) is the
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
tot_in = 0
tot_out = 0
tot_cache = 0
tot_create = 0
tool_n = 0
usage_last = None         # O.usage_fold carry record {"id", "f"} of the last counted message


def flush_msg(is_result=False):
    # Commit the buffered assistant message. The final one before the subagent ends
    # is its returned *result* (labelled ⇠ result); earlier ones are ✎ message. The
    # message's ctx % was snapshotted when it was buffered (last_usage may since have
    # advanced to the next turn), so emit that, not the live value.
    global pending_msg, pending_tag
    if pending_msg is None:
        return
    glyph, kind = ("⇠", "result") if is_result else ("✎", "message")
    O.emit(LOG, chip(glyph, kind, pending_tag), msg_gutter(cap(pending_msg, 40)))
    pending_msg = None
    pending_tag = ""


def render_compact(meta):
    # A "compact_boundary" system record: the conversation was compacted. Show it
    # inline (amber) so the gap in history makes sense. preTokens is always present;
    # postTokens is NOT always there, so degrade to "→ ?" when it's missing.
    flush_msg()
    pre, post, trig = meta.get("preTokens"), meta.get("postTokens"), meta.get("trigger") or "?"
    txt = "⟳ compacted"
    if pre:
        txt += f" · {kfmt(pre)} → " + (kfmt(post) if post else "?")
    txt += f" ({trig})"
    O.emit(LOG, O.gut(CTX_AMBER + txt + RST, SUB_RGB))


def render_prompt(text):
    flush_msg()
    O.emit(LOG, chip("⇢", "prompt"), gutter(cap(text.strip(), 24)))


def render_teammsg(sender, body):
    # An incoming agent-team message (mail from another teammate or the lead).
    flush_msg()
    O.emit(LOG, chip("✉", "from " + (sender or "?")), gutter(cap(body.strip(), 24)))


def render_message(text):
    global pending_msg, pending_tag, turn_ctx_shown
    text = text.strip()
    if not text:
        return
    flush_msg()               # commit the previous message; buffer this one
    pending_msg = text
    # Tie this turn's ctx % to its message (shown at flush). If the turn already
    # showed it on a tool line, don't repeat it.
    pending_tag = "" if turn_ctx_shown else cur_tag
    turn_ctx_shown = True


def render_file(name_tool, inp, result=None, ctx=""):
    label = FILE_LABEL.get(name_tool, "Read")
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    name = os.path.basename(path.rstrip("/")) or path or "?"
    col = FILE_COL.get(label, R.COL["def"])
    # Lead with WHO did it — the agent's name/type in its own colour — so a Read/Update/
    # Write is attributable to the subagent (or teammate) that ran it, the same identity
    # cue chip() puts on this agent's Bash ops. The gutter bar already carries the colour,
    # but the explicit name is what the eye reads.
    who = R.fg(*SUB_RGB) + LABEL + " " + RST
    line = who + col + label + R.DIM + "(" + R.COL["def"] + name + R.DIM + ")" + RST
    # A read shows how much of the file it took ('' == the whole file); a mutation shows
    # its added/removed line counts plus the line range(s) it touched. All go before the
    # model tag so they survive truncation on a narrow pane. Extent/range come from the
    # tool_result (`result`); counts from the input.
    added = removed = 0
    if name_tool == "Read":
        ext = O.read_extent(result.get("file") if isinstance(result, dict) else None, inp)
        if ext:
            line += "  " + R.DIM + ext + RST
    else:
        added, removed = O.diff_counts(name_tool, inp)
        d = []
        if added:
            d.append(R.fg(152, 195, 121) + f"+{added}" + RST)   # green additions
        if removed:
            d.append(R.fg(224, 108, 117) + f"-{removed}" + RST)  # red removals
        if d:
            line += "  " + " ".join(d)
        rng = O.edit_range(result.get("structuredPatch") if isinstance(result, dict) else None)
        if rng:
            line += "  " + R.DIM + rng + RST
    tag = op_tag()
    if tag:
        line += "  " + R.DIM + tag + RST
    if ctx:
        line += "  " + R.DIM + ctx + RST
    O.emit(LOG, O.gut(line, SUB_RGB))
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
    O.bump(LOG, tool=name_tool, file=path, added=added, removed=removed)


def on_tool_use(b):
    global turn_ctx_shown, tool_n
    tool_n += 1                   # count every tool call, for the ended-footer rollup
    flush_msg()
    ctx = ""                      # ctx rides the FIRST op header of a turn (if no msg led it)
    if not turn_ctx_shown:
        ctx = cur_tag
        turn_ctx_shown = True
    name = b.get("name") or ""
    inp = b.get("input") or {}
    tid = b.get("id")
    if name == "Bash":
        cmd = inp.get("command", "")
        if inp.get("run_in_background"):
            O.emit(LOG, chip("▷", "background", ctx), O.code(cmd))
            pend[tid] = ("bg", cmd)
        else:
            O.emit(LOG, chip("▶", "foreground", ctx), O.code(cmd))
            pend[tid] = ("fg", cmd)
    elif name in FILE_LABEL:
        # Defer to the result: absolute line info — a Read's EXTENT
        # (startLine/numLines/totalLines) and an edit's touched hunks (structuredPatch)
        # — lives only on the tool_result, which lands in the very next record, so
        # ordering is preserved. Carry (tool, input, ctx) for rendering there.
        pend[tid] = ("file", (name, inp, ctx))
    elif name == "Monitor":
        cmd = inp.get("command", "")
        O.emit(LOG, chip("◉", "monitor", ctx), O.code(cmd))
        pend[tid] = ("monitor", cmd)
    elif name == "SendMessage":
        # Mail this teammate sends to another teammate / the lead. Show recipient +
        # the message body; the tool_result is just a "{success:true,…}" ack (noise),
        # so it's suppressed in on_tool_result.
        to = inp.get("to") or inp.get("recipient") or "?"
        text = inp.get("message") or inp.get("content") or inp.get("summary") or ""
        O.emit(LOG, chip("✉", "to " + to, ctx), gutter(cap(text.strip(), 12)))
        pend[tid] = ("sendmsg", "")
    elif name in ("Task", "Agent"):
        # A nested subagent gets its OWN block via its own SubagentStart/Stop hooks.
        sub = (inp.get("subagent_type") or "subagent")
        st = "⊂ spawns " + sub + ("  " + op_tag() if op_tag() else "") + ("  " + ctx if ctx else "")
        O.emit(LOG, O.gut(R.DIM + st + RST, SUB_RGB))
        pend[tid] = ("agent", "")
    else:
        O.emit(LOG, chip("·", name or "tool", ctx))
        req = input_summary(inp)                 # show the request (e.g. the query/url)
        if req:
            O.emit(LOG, gutter(cap(req, 10)))
        pend[tid] = ("other", "")


def on_tool_result(b, tur=None):
    flush_msg()
    tid = b.get("tool_use_id")
    kind, cmd = pend.pop(tid, ("other", ""))
    if kind == "file":
        # Deferred from on_tool_use: render the file op now, with the extent (Read) or
        # touched range (edit) the result carries. cmd holds the saved (tool, input).
        name_tool, saved_inp, saved_ctx = cmd if isinstance(cmd, tuple) else ("Read", {}, "")
        render_file(name_tool, saved_inp, tur, saved_ctx)
        return
    if kind in ("agent", "sendmsg"):
        return                                      # already shown / handled elsewhere
    txt = result_text(b.get("content"))
    if kind in ("bg", "monitor"):
        m = re.search(r"with ID:\s*([^\s.]+)", txt)
        if m:
            spawn_tailer(kind, m.group(1), cmd)
        elif txt.strip():
            O.emit(LOG, gutter(cap(txt.strip(), 8)))
        return
    # fg / other: show the command's output (banners emphasised — this is real
    # command output, unlike the messages/prompts that share gutter()).
    body = txt.rstrip("\n")
    if body:
        O.emit(LOG, O.gut(R.emphasize(R.unescape(cap(body, 60))), SUB_RGB))
    else:
        O.emit(LOG, O.gut(R.DIM + "(no output)" + RST, SUB_RGB))
    if b.get("is_error"):
        O.emit(LOG, O.gut(R.fg(224, 108, 117) + "■ failed" + RST, SUB_RGB))


def handle_line(s):
    global last_usage, last_model, cur_tag, turn_ctx_shown, tot_in, tot_out, tot_cache, tot_create, usage_last
    try:
        o = json.loads(s)
    except Exception:
        A.error(LOG, "handle_line", {"agent": AGENT, "line": s[:300]})
        return
    t = o.get("type")
    msg = o.get("message") or {}
    content = msg.get("content")
    if t == "system" and o.get("subtype") == "compact_boundary":
        render_compact(o.get("compactMetadata") or {})
        return
    if t == "user":
        if isinstance(content, str):
            if content.strip():
                m = TEAMMSG.match(content)
                if m:
                    sid = _TM_ID.search(m.group(1))
                    render_teammsg(sid.group(1) if sid else "", m.group(2))
                else:
                    render_prompt(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    on_tool_result(blk, o.get("toolUseResult"))
    elif t == "assistant":
        u = msg.get("usage")
        if isinstance(u, dict):           # refresh the live context fill for this turn
            last_usage = u
            last_model = msg.get("model") or last_model
            # Accumulate for the ended-footer rollup — once per message.id, deltas
            # only for repeat lines of the same message (O.usage_fold, the shared
            # dedup — see usage_last above).
            d, usage_last = O.usage_fold(msg.get("id"), O.usage_fields(u), usage_last)
            tot_in += d[0]; tot_out += d[1]; tot_cache += d[2]; tot_create += d[3]
        cur_tag = ctx_tag()
        turn_ctx_shown = False            # each turn shows its ctx % once (msg or tool)
        if isinstance(content, list):
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text":
                    render_message(blk.get("text", ""))
                elif blk.get("type") == "tool_use":
                    on_tool_use(blk)


def main(run):
    start = time.time()
    # Wait for the transcript to appear.
    if not T.wait_for(JSONL, start + 15):
        O.emit(LOG, O.rule(), O.label(f"■ {LABEL} (no transcript)", SUB_RGB), O.rule())
        run.end("transcript-never-appeared")
        return

    pos = 0
    # Resume from the previous streamer's checkpoint (idle-teammate restart) so
    # already-rendered history isn't replayed. Line 2 (optional JSON) is the
    # predecessor's last-counted usage record, restored so a message straddling
    # the handoff isn't recounted from zero. Ignore a checkpoint past EOF (a
    # rewritten/foreign transcript) and start over. The adopted-vs-fresh outcome is
    # audited (one row per streamer, not per pump — the per-tick writes are too hot):
    # paired with the predecessor's 'final' row it makes a bad handoff (recounted or
    # skipped transcript, dropped dedup state) visible in `state_files`.
    global usage_last
    resume = {"agent": AGENT}
    try:
        saved = int(S.agent_get(LOG, AGENT).get("pos") or 0)
        if 0 < saved <= os.path.getsize(JSONL):
            pos = saved
            lu = S.kv_get(LOG, USAGE_KEY)
            if isinstance(lu, dict) and lu.get("id"):
                if "f" not in lu:   # predecessor predates O.usage_fold's record shape
                    lu = {"id": lu.get("id"),
                          "f": [int(lu.get(k) or 0) for k in ("in", "out", "cache", "create")]}
                usage_last = lu
            resume.update({"adopted_pos": pos, "usage_last": usage_last})
        elif saved:
            resume["fresh"] = f"checkpoint {saved} empty or past EOF"
        else:
            resume["fresh"] = "no checkpoint (first streamer)"
    except Exception:
        resume["fresh"] = "unreadable checkpoint"
    A.state_file(LOG, STATE_KEY, "resume", resume)

    tail = T.FileTailer(JSONL, pos=pos)
    ckpt = {"pos": -1}

    def pump():
        lines = tail.pump()
        for ln in (lines or ()):
            s = ln.decode("utf-8", "replace").strip()
            if s:
                handle_line(s)
        # Checkpoint only what was fully consumed — a trailing partial line
        # stays uncounted so a successor re-reads it whole. The last-counted
        # usage record rides along for the successor's dedup.
        if tail.consumed != ckpt["pos"]:
            ckpt["pos"] = tail.consumed
            S.agent_set(LOG, AGENT, pos=tail.consumed)
            if usage_last:
                S.kv_set(LOG, USAGE_KEY, usage_last)

    # Completion: the SubagentStop sentinel (the authoritative end signal — written
    # by the stop hook) for a normal finish, OR meta.json's stoppedByUser for a
    # manual cancel (see cancelled_by_user() above — no hook fires for that case),
    # OR the state DB vanishing (SessionEnd parked it as *.keep — quitting Claude
    # Code kills a background agent with no SubagentStop and no stoppedByUser
    # stamp, so without this check the streamer spun for the full backstop as a
    # zombie while its checkpoint writes mutated the parked snapshot through the
    # cached connection; the codex tailers run the same check). A long cap is a
    # backstop for a stuck/lost streamer either way.
    cancelled = False
    while True:
        pump()
        if not os.path.exists(S.db_path(LOG)):
            run.end("state-db-parked (session end)")
            # No footer, no bumps, no checkpoint past this point: every write
            # would either recreate the state DB (whose file-existence IS the
            # session-alive signal watchers poll) or land in the parked snapshot.
            return
        if S.agent_get(LOG, AGENT).get("done"):
            run.end("stop-sentinel")
            break
        if cancelled_by_user():
            cancelled = True
            run.end("stoppedByUser (manual cancel)")
            break
        if time.time() - start > T.BACKSTOP_S:
            run.end("backstop-timeout")
            break
        time.sleep(T.POLL_S)

    # Final drain — let the last lines land, then read them.
    time.sleep(0.3)
    pump(); pump()
    flush_msg(is_result=True)        # the last buffered message is the returned result

    got = claude_slots.lookup_id("sub", LOG, AGENT)
    ts = got[1] if (got and got[1]) else start
    sec = max(0.0, time.time() - ts)
    dur = O.fmt_dur(sec)
    foot = f"■ {LABEL} " + ("cancelled" if cancelled else "ended") + f" · {dur}"
    global RESOLVED_MODEL
    RESOLVED_MODEL = M.parent_resolved_model(TPATH, AGENT)   # authoritative window, best-effort
    used = ctx_used()                    # final context fill (plain — the chip is dark text)
    if used > 0:
        mx = model_ctx()
        foot += f" · ctx {used * 100 // mx}% ({kfmt(used)}/{kfmt(mx)})"
    # Cumulative rollup: fresh in / generated out / cache-hit share / tool count.
    if tot_in or tot_out:
        foot += f" · {kfmt(tot_in)} in · {kfmt(tot_out)} out"
        reads = tot_in + tot_cache
        if reads > 0:
            foot += f" · cache {tot_cache * 100 // reads}%"
    if tool_n:
        foot += f" · {tool_n} tool" + ("s" if tool_n != 1 else "")
    # Cost estimate from the tokens already summed, priced on the resolved model
    # (cache_creation billed at its 1.25× write premium via tot_create).
    usd = O.cost_usd(disp_model(), tot_in, tot_out, tot_cache, tot_create)
    if usd:
        foot += " · ≈ " + O.fmt_usd(usd)
    O.emit(LOG, O.rule(), O.label(foot, SUB_RGB), O.rule())
    # Checkpoint-trail bookend to the 'resume' row above: what this streamer
    # consumed and last counted. A successor whose 'resume' row disagrees with this
    # 'final' row is the handoff bug the persisted usage_last exists to prevent.
    A.state_file(LOG, STATE_KEY, "final",
                 {"agent": AGENT, "pos": tail.consumed, "usage_last": usage_last,
                  "in": tot_in, "out": tot_out, "cache": tot_cache, "create": tot_create})
    # Feed this agent's metered spend into the session scoreboard (the main session's
    # own spend is folded in separately by claude_ops.bump_transcript, called from the
    # cmd/file hooks — together they cover the whole session). tokens = fresh billed
    # input + generated output — cache reads are replay, not spend, so they're excluded.
    # `meta` makes the bump attributable and re-priceable straight from the audit DB
    # (agent, model priced on, and the four totals cost_usd saw).
    deltas = {}
    if usd:
        deltas["cost"] = usd
    if tot_in or tot_out:
        deltas["tokens"] = tot_in + tot_out
    if tot_in or tot_out or tot_cache or tot_create:
        # Per-category split feeding the scoreboard's Σ row — same four totals
        # cost_usd priced. tk_in is fresh input EXCL. cache creation (tot_in is
        # input+create), so tk_in+tk_create == tot_in and the Σ total stays
        # consistent with the ▪-row 'tokens' plus cache read. See O.token_parts.
        deltas["tk_in"] = tot_in - tot_create
        deltas["tk_out"] = tot_out
        deltas["tk_read"] = tot_cache
        deltas["tk_create"] = tot_create
    if deltas:
        O.bump(LOG, meta={"agent_id": AGENT,
                          "kind": "teammate" if PALETTE == "team" else "subagent",
                          "model": disp_model(), "in": tot_in, "out": tot_out,
                          "cache": tot_cache, "create": tot_create, "src": JSONL},
               **deltas)


def cleanup():
    # Release this agent's markers FIRST (so the recheck below doesn't see our own
    # still-live sub.pid), then ask claude-tab-status.py to flip a stale bg-running
    # blue back to green — a background agent finishing has no other hook to do it.
    # (No-op unless the tab is currently awaiting-bg and nothing else is running.)
    claude_slots.release_id("sub", LOG, AGENT)
    # Clear the done flag (NOT pos — a resumed teammate needs the checkpoint) so a
    # later SubagentStart for this agent_id doesn't finalise its new streamer at once.
    S.agent_set(LOG, AGENT, done=0)
    claude_slots.pid_del(LOG, AGENT)
    try:
        subprocess.run([os.path.join(HERE, "claude-tab-status.py"), "bg-recheck", LOG, "sub"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    with T.stream_lifecycle(LOG, "teammate" if PALETTE == "team" else "subagent",
                            agent_id=AGENT, src_path=JSONL,
                            ctx={"agent": AGENT, "type": ATYPE}) as run:
        def _finalize():
            run.lines = tool_n          # tools seen — recorded even on a crash
            cleanup()
        run.on_exit = _finalize
        main(run)
