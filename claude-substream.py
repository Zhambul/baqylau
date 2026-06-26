#!/usr/bin/env python3
# claude-substream.py AGENT_ID TRANSCRIPT_PATH MIRROR_LOG WIDTH SLOT AGENT_TYPE
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

AGENT   = sys.argv[1]
TPATH   = sys.argv[2]
LOG     = sys.argv[3]
WIDTH   = max(16, int(sys.argv[4]))
SLOT    = int(sys.argv[5])
ATYPE   = sys.argv[6] if len(sys.argv) > 6 else "agent"
# Which palette to colour this block with. An in-process agent-team TEAMMATE rides
# the very same machinery as an ordinary subagent (same "sub" slot + sub.* markers,
# same transcript layout) — only the colour family differs, so it's "team" instead
# of "sub". Everything else (slot index, completion sentinel, footer) is identical.
PALETTE = sys.argv[7] if len(sys.argv) > 7 else "sub"

SUB_RGB = claude_slots.color(PALETTE, SLOT)
RST  = R.RST
RULE = R.rule(WIDTH)
GUT  = R.fg(*SUB_RGB) + "│ " + RST            # single subagent gutter (messages / output)
GW   = 2
HERE = os.path.dirname(os.path.abspath(__file__))

# Where the subagent's transcript + completion sentinel live.
BASE = TPATH[:-6] if TPATH.endswith(".jsonl") else TPATH
SUBDIR = os.path.join(BASE, "subagents")
JSONL  = os.path.join(SUBDIR, f"agent-{AGENT}.jsonl")
SENT   = os.path.join(LOG + ".slots", f"sub.done.{AGENT}")

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


def append(text):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def fit(s):
    return s if len(s) <= WIDTH - 2 else s[:WIDTH - 3] + "…"


def chip(glyph, kind):
    return R.label(fit(f"{ATYPE} {glyph} {kind}"), SUB_RGB)


def cap(text, n):
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    more = len(lines) - n
    return "\n".join(lines[:n]) + f"\n… ({more} more line{'s' if more != 1 else ''})"


def gutter(text):
    return R.wrap_gutter(R.unescape(text), WIDTH, GUT, GW)


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
    #   KIND TASKID LOG WIDTH SLOT SIG OUTER
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
            [sys.executable, streamer, kind, taskid, LOG, str(WIDTH), str(slot), sig, outer],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        claude_slots.set_owner(marker, proc.pid)
    except Exception:
        claude_slots.release(kind, LOG, slot, os.getpid())


# --- rendering of transcript blocks --------------------------------------------
pend = {}                 # tool_use_id -> (kind, cmd)
pending_msg = None        # latest assistant text, held so the LAST one (the result) can be labelled


def flush_msg(is_result=False):
    # Commit the buffered assistant message. The final one before the subagent ends
    # is its returned *result* (labelled ⇠ result); earlier ones are ✎ message.
    global pending_msg
    if pending_msg is None:
        return
    glyph, kind = ("⇠", "result") if is_result else ("✎", "message")
    append(chip(glyph, kind) + "\n" + gutter(cap(pending_msg, 40)) + "\n")
    pending_msg = None


def render_prompt(text):
    flush_msg()
    append(chip("⇢", "prompt") + "\n" + gutter(cap(text.strip(), 24)) + "\n")


def render_teammsg(sender, body):
    # An incoming agent-team message (mail from another teammate or the lead).
    flush_msg()
    append(chip("✉", "from " + (sender or "?")) + "\n" + gutter(cap(body.strip(), 24)) + "\n")


def render_message(text):
    global pending_msg
    text = text.strip()
    if not text:
        return
    flush_msg()               # commit the previous message; buffer this one
    pending_msg = text


def render_file(name_tool, inp):
    label = FILE_LABEL.get(name_tool, "Read")
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    name = os.path.basename(path.rstrip("/")) or path or "?"
    col = FILE_COL.get(label, R.COL["def"])
    line = col + label + R.DIM + "(" + R.COL["def"] + name + R.DIM + ")" + RST
    append(R.fg(*SUB_RGB) + "│ " + RST + line + "\n")


def on_tool_use(b):
    flush_msg()
    name = b.get("name") or ""
    inp = b.get("input") or {}
    tid = b.get("id")
    if name == "Bash":
        cmd = inp.get("command", "")
        if inp.get("run_in_background"):
            append(chip("▷", "background") + "\n" + R.render(cmd, WIDTH) + "\n")
            pend[tid] = ("bg", cmd)
        else:
            append(chip("▶", "foreground") + "\n" + R.render(cmd, WIDTH) + "\n")
            pend[tid] = ("fg", cmd)
    elif name in FILE_LABEL:
        render_file(name, inp)
        pend[tid] = ("file", "")
    elif name == "Monitor":
        cmd = inp.get("command", "")
        append(chip("◉", "monitor") + "\n" + R.render(cmd, WIDTH) + "\n")
        pend[tid] = ("monitor", cmd)
    elif name == "SendMessage":
        # Mail this teammate sends to another teammate / the lead. Show recipient +
        # the message body; the tool_result is just a "{success:true,…}" ack (noise),
        # so it's suppressed in on_tool_result.
        to = inp.get("to") or inp.get("recipient") or "?"
        text = inp.get("message") or inp.get("content") or inp.get("summary") or ""
        append(chip("✉", "to " + to) + "\n" + gutter(cap(text.strip(), 12)) + "\n")
        pend[tid] = ("sendmsg", "")
    elif name in ("Task", "Agent"):
        # A nested subagent gets its OWN block via its own SubagentStart/Stop hooks.
        sub = (inp.get("subagent_type") or "subagent")
        append(R.fg(*SUB_RGB) + "│ " + RST + R.DIM + "⊂ spawns " + sub + RST + "\n")
        pend[tid] = ("agent", "")
    else:
        append(chip("·", name or "tool") + "\n")
        req = input_summary(inp)                 # show the request (e.g. the query/url)
        if req:
            append(gutter(cap(req, 10)) + "\n")
        pend[tid] = ("other", "")


def on_tool_result(b):
    flush_msg()
    tid = b.get("tool_use_id")
    kind, cmd = pend.pop(tid, ("other", ""))
    if kind in ("file", "agent", "sendmsg"):
        return                                      # already shown / handled elsewhere
    txt = result_text(b.get("content"))
    if kind in ("bg", "monitor"):
        m = re.search(r"with ID:\s*([^\s.]+)", txt)
        if m:
            spawn_tailer(kind, m.group(1), cmd)
        elif txt.strip():
            append(gutter(cap(txt.strip(), 8)) + "\n")
        return
    # fg / other: show the command's output
    body = txt.rstrip("\n")
    if body:
        append(gutter(cap(body, 60)) + "\n")
    else:
        append(GUT + R.DIM + "(no output)" + RST + "\n")
    if b.get("is_error"):
        append(GUT + R.fg(224, 108, 117) + "■ failed" + RST + "\n")


def handle_line(s):
    try:
        o = json.loads(s)
    except Exception:
        return
    t = o.get("type")
    content = (o.get("message") or {}).get("content")
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
                    on_tool_result(blk)
    elif t == "assistant" and isinstance(content, list):
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
        append(RULE + "\n" + R.label(fit(f"■ {ATYPE} (no transcript)"), SUB_RGB) + "\n" + RULE + "\n")
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
    # by the stop hook). NOT meta.json: that's written at subagent *start*, so it
    # can't mark the end. A long cap is a backstop for a stuck/lost streamer.
    while True:
        pump()
        if os.path.exists(SENT):
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
    append(RULE + "\n" + R.label(fit(f"■ {ATYPE} ended · {dur}"), SUB_RGB) + "\n" + RULE + "\n")


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
        subprocess.run([os.path.join(HERE, "claude-tab-status.sh"), "bg-recheck"],
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
