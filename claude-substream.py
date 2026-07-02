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
import errno, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_render as R
import claude_ops as O

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


def _int_env(name, default):
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except Exception:
        return default


# Context-fill thresholds (percent) for the live per-turn % shown on each turn:
# < WARN green, < CRIT amber, else red. Tunable per workload via the env, same as
# CLAUDE_MIRROR_BIAS — e.g. a [1m] session wants higher cutoffs.
CTX_WARN = _int_env("CLAUDE_MIRROR_CTX_WARN", 30)
CTX_CRIT = _int_env("CLAUDE_MIRROR_CTX_CRIT", 60)
CTX_GREEN = R.fg(152, 195, 121)
CTX_AMBER = R.fg(229, 192, 123)
CTX_RED   = R.fg(224, 108, 117)

# --- context-window detection -------------------------------------------------
# There is NO context-size frontmatter field (docs): the window follows the resolved
# MODEL, which a subagent can pin explicitly (e.g. `model: opus[1m]`). Determining it
# is messy — Sonnet 5 / Fable 5 / Opus 4.6-4.8 run 1M by default (no suffix), older
# models are 200k unless [1m], and CLAUDE_CODE_DISABLE_1M_CONTEXT caps everything.
DISABLE_1M = bool(_int_env("CLAUDE_CODE_DISABLE_1M_CONTEXT", 0))
KNOWN_1M = ("fable-5", "sonnet-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6")
RESOLVED_MODEL = None      # authoritative model id (with [1m]) read from the parent


def _window(model):
    # A model alias / id (with or without [1m]) -> its context window; None if empty.
    if not model:
        return None
    m = model.lower().strip()
    if "haiku" in m:
        return 200_000
    if "[1m]" in m:
        return 1_000_000
    if any(tok in m for tok in KNOWN_1M):
        return 1_000_000
    if m in ("opus", "sonnet", "fable"):     # current aliases -> latest gen -> 1M
        return 1_000_000
    return 200_000                           # older / unknown pinned versions


def _fm_field(path, field):
    # Scalar field from a markdown file's YAML frontmatter (the first --- ... --- block).
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return None
            for line in fh:
                if line.strip() == "---":
                    break
                k, sep, v = line.partition(":")
                if sep and k.strip() == field:
                    return v.strip().strip('"\'') or None
    except Exception:
        return None
    return None


def _agent_def_file(atype):
    # The DEFINITION file for this agent type, if any. Identity is the frontmatter
    # `name:` (docs); fall back to the filename stem. Project defs shadow user defs.
    roots = [os.path.join(os.getcwd(), ".claude", "agents"),
             os.path.expanduser("~/.claude/agents")]
    stem_hit = None
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dp, _dirs, files in os.walk(r):
            for f in files:
                if not f.endswith(".md"):
                    continue
                p = os.path.join(dp, f)
                if _fm_field(p, "name") == atype:
                    return p
                if os.path.splitext(f)[0] == atype and stem_hit is None:
                    stem_hit = p
    return stem_hit


def _def_field(field):
    # A frontmatter field from this agent's definition; "inherit"/unset -> None so
    # resolution falls through to what the agent actually ran / the session default.
    v = _fm_field(AGENT_DEF_FILE, field) if AGENT_DEF_FILE else None
    return None if (not v or v == "inherit") else v


def _settings_field(field):
    # A field from the merged settings (project overriding global) — the same layering
    # claude-split.sh reads. Used for values an agent inherits (model, effortLevel).
    for p in (os.path.join(os.getcwd(), ".claude", "settings.local.json"),
              os.path.join(os.getcwd(), ".claude", "settings.json"),
              os.path.expanduser("~/.claude/settings.json")):
        try:
            with open(p, encoding="utf-8") as fh:
                v = json.load(fh).get(field)
            if v:
                return v
        except Exception:
            pass
    return None


def _session_model():
    # The model VERSION the parent session runs (e.g. "claude-opus-4-8"), from the last
    # assistant turn in its transcript. Gives the prompt line a precise version for
    # agents that INHERIT, before the agent's own first turn reveals it. Tail-scan only
    # (the latest turn is near the end) so it stays cheap even on long sessions.
    try:
        with open(TPATH, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            chunk = fh.read().decode("utf-8", "replace")
        last = None
        for line in chunk.splitlines():
            if '"assistant"' in line and '"model"' in line:
                try:
                    m = (json.loads(line).get("message") or {}).get("model")
                except Exception:
                    continue
                if m:
                    last = m
        return last
    except Exception:
        return None


def _parent_resolved_model():
    # The authoritative resolved model (carrying [1m]) is recorded in the PARENT
    # transcript on this agent's Task result — but only at completion. Best-effort:
    # scan TPATH for our agentId; returns None if not written yet (footer falls back).
    try:
        hit = None
        with open(TPATH, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if AGENT not in line or "resolvedModel" not in line:
                    continue
                try:
                    tur = (json.loads(line).get("toolUseResult") or {})
                except Exception:
                    continue
                if tur.get("agentId") == AGENT and tur.get("resolvedModel"):
                    hit = tur["resolvedModel"]
        return hit
    except Exception:
        return None


def _meta():
    # The agent's meta.json sidecar (present at SubagentStart for teammates; may lag a
    # beat for ordinary subagents, so retry briefly). Carries `customAgentType` — the
    # DEFINITION's name, which for a teammate differs from its short display type
    # (agentType "container" vs def "task-container") — and its configured `model`.
    base = TPATH[:-6] if TPATH.endswith(".jsonl") else TPATH
    p = os.path.join(base, "subagents", f"agent-{AGENT}.meta.json")
    for _ in range(6):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            time.sleep(0.05)
        except Exception:
            break
    return {}


META = _meta()
# Look up the definition by its real name (customAgentType) — the short agentType a
# teammate reports ("container") won't match the def's `name:`/filename ("task-container").
DEF_TYPE = META.get("customAgentType") or ATYPE
AGENT_DEF_FILE  = _agent_def_file(DEF_TYPE)
AGENT_DEF_MODEL = _def_field("model")
SETTINGS_MODEL  = _settings_field("model")
SESSION_MODEL   = _session_model()

# Effort is NOT recorded in any transcript — it's config-only. Resolve it in the order
# the docs mandate (model-config: "The environment variable takes precedence over all
# other methods … Frontmatter effort … overriding the session level but not the
# environment variable"): env > agent-def frontmatter `effort` > session `effortLevel`
# > the running MODEL's default. A subagent with no explicit effort inherits the session
# level; with nothing configured it falls to the model default (docs: high on Opus 4.8 /
# 4.6 / Sonnet 5 / Sonnet 4.6 / Fable 5, xhigh on Opus 4.7). Caveat: a session-only
# `/effort max`/`ultracode`/`--effort` isn't persisted, so it can't be seen here.
EFFORT_CFG = ((os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or "").strip()
              or _def_field("effort") or _settings_field("effortLevel") or "")


def _model_default_effort(model):
    if not model:
        return ""
    m = model.lower()
    if "opus-4-7" in m:
        return "xhigh"
    if any(t in m for t in ("opus-4-8", "opus-4-6", "sonnet-5", "sonnet-4-6", "fable-5")):
        return "high"
    return ""                                # models without adaptive reasoning


def short_model(model):
    # "claude-opus-4-8" -> "opus-4.8", "claude-haiku-4-5-20251001" -> "haiku-4.5",
    # "claude-sonnet-5" -> "sonnet-5", alias "opus" -> "opus". [1m] is dropped (the
    # window already shows in the ctx line).
    if not model:
        return ""
    s = model.lower().replace("[1m]", "").strip()
    if s.startswith("claude-"):
        s = s[7:]
    parts = s.split("-")
    ver = []
    for p in parts[1:]:
        if p.isdigit() and len(p) <= 2:      # version component; skip 8-digit dates
            ver.append(p)
        else:
            break
    return parts[0] + ("-" + ".".join(ver) if ver else "")


def disp_model():
    # The model to display, best-known-first: the agent's own resolved id > this agent's
    # configured model (meta) or an explicit frontmatter override > the parent session's
    # version (for inheriting agents, before the first turn) > footer id > config alias.
    return (last_model or META.get("model") or AGENT_DEF_MODEL or SESSION_MODEL
            or RESOLVED_MODEL or SETTINGS_MODEL)


def effort():
    # Configured effort (env > frontmatter > session) if any, else the running model's
    # default — so an agent that inherits shows the level it actually reasons at.
    return EFFORT_CFG or _model_default_effort(disp_model())


def op_tag():
    # "opus-4.8·high" — the model this agent is running plus the resolved effort.
    # Constant per agent; appended to every operation header.
    return "·".join(x for x in (short_model(disp_model()), effort()) if x)

# Where the subagent's transcript + completion sentinel live.
BASE = TPATH[:-6] if TPATH.endswith(".jsonl") else TPATH
SUBDIR = os.path.join(BASE, "subagents")
JSONL  = os.path.join(SUBDIR, f"agent-{AGENT}.jsonl")
SENT   = os.path.join(LOG + ".slots", f"sub.done.{AGENT}")
META_PATH = os.path.join(SUBDIR, f"agent-{AGENT}.meta.json")


def cancelled_by_user():
    # A manually killed/cancelled subagent fires NO SubagentStop hook — the same
    # gap documented throughout this codebase for interrupts (claude-tab-status.sh's
    # idle-watch, claude-cmd-pre.py's cancelled-foreground-command fix) — so SENT
    # never appears and this tailer would otherwise hang until the 6h backstop
    # below, leaving the tab stuck blue the whole time. But Claude Code stamps
    # `stoppedByUser: true` onto this agent's meta.json sidecar the moment that
    # happens (confirmed empirically), giving a fast, reliable end signal instead.
    try:
        with open(META_PATH, encoding="utf-8") as fh:
            return bool(json.load(fh).get("stoppedByUser"))
    except Exception:
        return False

# Verb colours for file ops (match claude-file-fmt.py).
FILE_LABEL = {"Read": "Read", "Edit": "Update", "MultiEdit": "Update",
              "Write": "Write", "NotebookEdit": "Update"}
FILE_COL   = {"Read": R.fg(97, 175, 239), "Update": R.fg(229, 192, 123),
              "Write": R.fg(152, 195, 121)}

# A message DELIVERED to this teammate appears in its transcript as a plain user
# record whose text is wrapped in <teammate-message teammate_id="<sender>" …>BODY
# </teammate-message> (the very first one is the lead's spawn prompt). We render it
# as "✉ from <sender>" + the unwrapped body, rather than as a raw ⇢ prompt.
TEAMMSG = re.compile(r'^\s*<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>\s*$', re.S)
_TM_ID  = re.compile(r'teammate_id="([^"]*)"')


def chip(glyph, kind):
    tag = op_tag()
    s = f"{LABEL} {glyph} {kind}" + (f"  {tag}" if tag else "")
    return O.label(s, SUB_RGB)


def cap(text, n):
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    more = len(lines) - n
    return "\n".join(lines[:n]) + f"\n… ({more} more line{'s' if more != 1 else ''})"


def gutter(text):
    return O.gut(R.unescape(text), SUB_RGB)


def kfmt(n):
    # Compact token count: 124000 -> "124k", 1000000 -> "1M".
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def model_ctx():
    # Context window for the fill %, derived purely from config/model — NO empirical
    # self-correct. Precedence, first that resolves wins:
    #   0. CLAUDE_CODE_DISABLE_1M_CONTEXT — Claude Code's own kill-switch, caps at 200k
    #   1. RESOLVED_MODEL — authoritative id from the parent transcript (footer only)
    #   2. AGENT_DEF_MODEL — an explicit `model:` in this agent's definition frontmatter
    #   3. last_model — the bare id the agent actually ran (family is reliable; the
    #      known-1M table covers Opus 4.8 etc. even though the [1m] suffix is stripped)
    #   4. SETTINGS_MODEL — the session default, for agents that inherit
    if DISABLE_1M:
        return 200_000
    for m in (RESOLVED_MODEL, AGENT_DEF_MODEL, last_model, SETTINGS_MODEL):
        w = _window(m)
        if w:
            return w
    return 200_000


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
    # Colour-by-threshold "ctx 42% · 84k/200k" for the current turn, or "" if no usage.
    used = ctx_used()
    if used <= 0:
        return ""
    mx = model_ctx()
    pct = used * 100 // mx
    col = CTX_GREEN if pct < CTX_WARN else CTX_AMBER if pct < CTX_CRIT else CTX_RED
    return f"{col}ctx {pct}% · {kfmt(used)}/{kfmt(mx)}{RST}"


def emit_ctx(tag):
    # One colour-coded context line in this agent's stream colour (the digits carry the
    # threshold colour via inline ANSI; the gutter bar stays the agent's identity hue).
    if tag:
        O.emit(LOG, O.gut(tag, SUB_RGB))


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


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError as e:
        return e.errno == errno.EPERM


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
    except Exception:
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
# tokens actually sent, not replayed; tot_cache is cache_read (cheap replay). So the
# footer's "cache %" = tot_cache / (tot_in + tot_cache) is the share of all context
# reads served from cache — a thrash/reuse signal. tool_n counts tool_use blocks.
tot_in = 0
tot_out = 0
tot_cache = 0
tool_n = 0


def flush_msg(is_result=False):
    # Commit the buffered assistant message. The final one before the subagent ends
    # is its returned *result* (labelled ⇠ result); earlier ones are ✎ message. The
    # message's ctx % was snapshotted when it was buffered (last_usage may since have
    # advanced to the next turn), so emit that, not the live value.
    global pending_msg, pending_tag
    if pending_msg is None:
        return
    emit_ctx(pending_tag)
    glyph, kind = ("⇠", "result") if is_result else ("✎", "message")
    O.emit(LOG, chip(glyph, kind), gutter(cap(pending_msg, 40)))
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


def render_file(name_tool, inp, result=None):
    label = FILE_LABEL.get(name_tool, "Read")
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    name = os.path.basename(path.rstrip("/")) or path or "?"
    col = FILE_COL.get(label, R.COL["def"])
    line = col + label + R.DIM + "(" + R.COL["def"] + name + R.DIM + ")" + RST
    # A read shows how much of the file it took ('' == the whole file); a mutation shows
    # its added/removed line counts plus the line range(s) it touched. All go before the
    # model tag so they survive truncation on a narrow pane. Extent/range come from the
    # tool_result (`result`); counts from the input.
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
    O.emit(LOG, O.gut(line, SUB_RGB))


def on_tool_use(b):
    global turn_ctx_shown, tool_n
    tool_n += 1                   # count every tool call, for the ended-footer rollup
    flush_msg()
    if not turn_ctx_shown:        # one ctx line per turn, here if no message led it
        emit_ctx(cur_tag)
        turn_ctx_shown = True
    name = b.get("name") or ""
    inp = b.get("input") or {}
    tid = b.get("id")
    if name == "Bash":
        cmd = inp.get("command", "")
        if inp.get("run_in_background"):
            O.emit(LOG, chip("▷", "background"), O.code(cmd))
            pend[tid] = ("bg", cmd)
        else:
            O.emit(LOG, chip("▶", "foreground"), O.code(cmd))
            pend[tid] = ("fg", cmd)
    elif name in FILE_LABEL:
        # Defer to the result: absolute line info — a Read's EXTENT
        # (startLine/numLines/totalLines) and an edit's touched hunks (structuredPatch)
        # — lives only on the tool_result, which lands in the very next record, so
        # ordering is preserved. Carry (tool, input) for rendering there.
        pend[tid] = ("file", (name, inp))
    elif name == "Monitor":
        cmd = inp.get("command", "")
        O.emit(LOG, chip("◉", "monitor"), O.code(cmd))
        pend[tid] = ("monitor", cmd)
    elif name == "SendMessage":
        # Mail this teammate sends to another teammate / the lead. Show recipient +
        # the message body; the tool_result is just a "{success:true,…}" ack (noise),
        # so it's suppressed in on_tool_result.
        to = inp.get("to") or inp.get("recipient") or "?"
        text = inp.get("message") or inp.get("content") or inp.get("summary") or ""
        O.emit(LOG, chip("✉", "to " + to), gutter(cap(text.strip(), 12)))
        pend[tid] = ("sendmsg", "")
    elif name in ("Task", "Agent"):
        # A nested subagent gets its OWN block via its own SubagentStart/Stop hooks.
        sub = (inp.get("subagent_type") or "subagent")
        st = "⊂ spawns " + sub + ("  " + op_tag() if op_tag() else "")
        O.emit(LOG, O.gut(R.DIM + st + RST, SUB_RGB))
        pend[tid] = ("agent", "")
    else:
        O.emit(LOG, chip("·", name or "tool"))
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
        name_tool, saved_inp = cmd if isinstance(cmd, tuple) else ("Read", {})
        render_file(name_tool, saved_inp, tur)
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
    global last_usage, last_model, cur_tag, turn_ctx_shown, tot_in, tot_out, tot_cache
    try:
        o = json.loads(s)
    except Exception:
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
            # Accumulate for the ended-footer rollup (each turn's usage counted once).
            tot_in += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            tot_cache += u.get("cache_read_input_tokens", 0)
            tot_out += u.get("output_tokens", 0)
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


def main():
    start = time.time()
    # Wait for the transcript to appear.
    while not os.path.exists(JSONL) and time.time() < start + 15:
        time.sleep(0.2)
    if not os.path.exists(JSONL):
        O.emit(LOG, O.rule(), O.label(f"■ {LABEL} (no transcript)", SUB_RGB), O.rule())
        return

    pos, pending = 0, b""

    def pump():
        nonlocal pos, pending
        try:
            size = os.path.getsize(JSONL)
        except OSError:
            return
        if size > pos:
            try:
                with open(JSONL, "rb") as fh:
                    fh.seek(pos); pending += fh.read(); pos = size
            except OSError:
                return
            *lines, pending2 = pending.split(b"\n")
            pending = pending2
            for ln in lines:
                s = ln.decode("utf-8", "replace").strip()
                if s:
                    handle_line(s)

    # Completion: the SubagentStop sentinel (the authoritative end signal — written
    # by the stop hook) for a normal finish, OR meta.json's stoppedByUser for a
    # manual cancel (see cancelled_by_user() above — no hook fires for that case).
    # A long cap is a backstop for a stuck/lost streamer either way.
    cancelled = False
    while True:
        pump()
        if os.path.exists(SENT):
            break
        if cancelled_by_user():
            cancelled = True
            break
        if time.time() - start > 6 * 3600:
            break
        time.sleep(0.3)

    # Final drain — let the last lines land, then read them.
    time.sleep(0.3)
    pump(); pump()
    flush_msg(is_result=True)        # the last buffered message is the returned result

    got = claude_slots.lookup_id("sub", LOG, AGENT)
    ts = got[1] if (got and got[1]) else start
    sec = max(0.0, time.time() - ts)
    dur = f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m{int(sec % 60):02d}s"
    foot = f"■ {LABEL} " + ("cancelled" if cancelled else "ended") + f" · {dur}"
    global RESOLVED_MODEL
    RESOLVED_MODEL = _parent_resolved_model()   # authoritative window, best-effort
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
    # Cost estimate from the tokens already summed, priced on the resolved model.
    usd = O.cost_usd(disp_model(), tot_in, tot_out, tot_cache)
    if usd:
        foot += " · ≈ " + O.fmt_usd(usd)
    O.emit(LOG, O.rule(), O.label(foot, SUB_RGB), O.rule())
    # Feed this agent's metered spend into the session scoreboard (the main session has
    # no token stream of its own, so the scoreboard's "≈ $" reflects agent/codex runs).
    if usd:
        O.bump(LOG, cost=usd)


def cleanup():
    # Release this agent's markers FIRST (so the recheck below doesn't see our own
    # still-live sub.pid), then ask claude-tab-status.sh to flip a stale bg-running
    # blue back to green — a background agent finishing has no other hook to do it.
    # (No-op unless the tab is currently awaiting-bg and nothing else is running.)
    claude_slots.release_id("sub", LOG, AGENT)
    for p in (SENT, os.path.join(LOG + ".slots", f"sub.pid.{AGENT}")):
        try:
            os.remove(p)
        except Exception:
            pass
    try:
        subprocess.run([os.path.join(HERE, "claude-tab-status.sh"), "bg-recheck", LOG + ".slots"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    finally:
        cleanup()
