# plugins/claude_code/transcript.py — Claude Code transcript PARSING.
#
# The parse half of the substream's parse/paint split (docs/sessionapi.md).
# This module is the ONE reader of the Claude Code transcript JSONL record
# shapes — the type/user/assistant discrimination, the teammate-message
# unwrapping, the content-block walk, the tool_result text normalisation — for
# BOTH a subagent's transcript (subagents/agent-<id>.jsonl) and the parent
# session's own transcript (the same record grammar). Two presenters consume
# its records:
#
#   substream_render.Renderer.handle_line  — the mirror's CAPPED, styled paint
#   timeline() below                       — the UNCAPPED drill-down entries
#                                             behind plugins.activity(), read
#                                             through core/sessionapi.py
#
# Re-encoding a transcript record shape anywhere else is a bug (styleguide
# single-owner table). parse_line() is pure (no I/O, no state); the only
# I/O here is timeline()/activity()'s own file read.
#
# parse_line(s) returns one record per JSONL line (None = nothing renderable):
#   {"kind": "bad", "raw": s}                       unparseable JSON
#   {"kind": "compact", "meta": {...}}              a compact_boundary system record
#   {"kind": "prompt", "text": str}                 a plain user prompt (unstripped)
#   {"kind": "teammsg", "sender": str, "body": str} an incoming teammate message
#   {"kind": "results", "blocks": [...], "tur": …, "texts": [str, ...]}
#       a user record carrying tool_result blocks (in order) — `tur` is the
#       line's toolUseResult sidecar; `texts` collects the line's plain text
#       blocks (a PARENT transcript's user turns arrive as text blocks in list
#       content — the mirror renderer deliberately ignores them, byte-identical
#       to the pre-split behavior; timeline() renders them)
#   {"kind": "assistant", "usage": dict|None, "model": str|None, "id": str|None,
#    "blocks": [("text", str) | ("tool", block), ...]}
#       one assistant message line — blocks preserve the content order; the
#       record is returned even with no content list (usage/turn tracking must
#       still run)
import json
import os
import re

# A message DELIVERED to a teammate appears in its transcript as a plain user
# record whose text is wrapped in <teammate-message teammate_id="<sender>" …>BODY
# </teammate-message> (the very first one is the lead's spawn prompt).
TEAMMSG = re.compile(r'^\s*<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>\s*$', re.S)
_TM_ID  = re.compile(r'teammate_id="([^"]*)"')


def result_text(content):
    """Normalise a tool_result's content (str | block | block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):       # a lone content block — normalise to a 1-list
        content = [content]
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
    """Compact "key: value" view of a tool's input, so the REQUEST is visible
    (e.g. a WebSearch query, a WebFetch url)."""
    if not isinstance(inp, dict) or not inp:
        return ""
    lines = []
    for k, v in inp.items():
        vs = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        lines.append(f"{k}: {vs}")
    return "\n".join(lines)


def classify_user_text(text):
    """("teammsg", sender, body) for a wrapped teammate message, else
    ("prompt", text, None). `text` is the raw user content string."""
    m = TEAMMSG.match(text)
    if m:
        sid = _TM_ID.search(m.group(1))
        return "teammsg", (sid.group(1) if sid else ""), m.group(2)
    return "prompt", text, None


def parse_line(s):
    """One transcript JSONL line -> a typed record (see the module header)."""
    try:
        o = json.loads(s)
    except Exception:
        return {"kind": "bad", "raw": s}
    t = o.get("type")
    msg = o.get("message") or {}
    content = msg.get("content")
    if t == "system" and o.get("subtype") == "compact_boundary":
        return {"kind": "compact", "meta": o.get("compactMetadata") or {}}
    if t == "user":
        if isinstance(content, str):
            if not content.strip():
                return None
            kind, a, b = classify_user_text(content)
            if kind == "teammsg":
                return {"kind": "teammsg", "sender": a, "body": b}
            return {"kind": "prompt", "text": content}
        if isinstance(content, list):
            blocks, texts = [], []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_result":
                    blocks.append(blk)
                elif blk.get("type") == "text" and (blk.get("text") or "").strip():
                    texts.append(blk.get("text"))
            if blocks or texts:
                return {"kind": "results", "blocks": blocks,
                        "tur": o.get("toolUseResult"), "texts": texts}
        return None
    if t == "assistant":
        blocks = []
        if isinstance(content, list):
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text":
                    blocks.append(("text", blk.get("text", "")))
                elif blk.get("type") == "tool_use":
                    blocks.append(("tool", blk))
        u = msg.get("usage")
        return {"kind": "assistant", "usage": u if isinstance(u, dict) else None,
                "model": msg.get("model"), "id": msg.get("id"), "blocks": blocks}
    return None


def agent_paths(parent_tpath, agent_id):
    """(jsonl, meta_json) for a subagent of the session whose PARENT transcript
    is parent_tpath — the <base>/subagents/agent-<id>.{jsonl,meta.json} layout
    (the one owner of that derivation; substream._init binds through it)."""
    base = parent_tpath[:-6] if parent_tpath.endswith(".jsonl") else parent_tpath
    subdir = os.path.join(base, "subagents")
    return (os.path.join(subdir, "agent-%s.jsonl" % agent_id),
            os.path.join(subdir, "agent-%s.meta.json" % agent_id))


# --- session title + the main-thread conversation (dashboard read models) ----------

TITLE_SCAN = 200        # head-window lines session_title inspects: summary records
#                         are PREPENDED on resume, so they precede the first prompt;
#                         a title must never cost a full multi-MB transcript read


def session_title(path):
    """Best-effort display TITLE for a session transcript — effectively what
    the `claude --resume` picker shows: the LAST `summary` record in the head
    window when Claude Code wrote one, else the first line of the first REAL
    user prompt (isMeta rows and `<command-*>`/`<local-command-*>` wrappers
    are plumbing, not prompts). '' when unreadable / nothing found."""
    summary, prompt = "", ""
    try:
        with open(path, encoding="utf-8") as fh:
            for i, raw in enumerate(fh):
                if i >= TITLE_SCAN or prompt:
                    break
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    o = json.loads(raw)
                except Exception:
                    continue
                t = o.get("type")
                if t == "summary":
                    summary = o.get("summary") or summary
                elif t == "user" and not o.get("isMeta"):
                    c = (o.get("message") or {}).get("content")
                    if isinstance(c, str):
                        s = c.strip()
                        if s and not s.startswith("<"):
                            prompt = s.split("\n", 1)[0][:200]
    except OSError:
        return ""
    return summary or prompt


def _complete_lines(path, pos):
    """Complete lines from byte `pos`: ([line, …], new_pos). A trailing
    partial line is NOT consumed (new_pos stops before it), so a json parse
    never sees a torn record — the read-exactly discipline of core/tail's
    pump, as a one-shot."""
    try:
        with open(path, "rb") as fh:
            fh.seek(pos)
            data = fh.read()
    except OSError:
        return [], pos
    end = data.rfind(b"\n")
    if end < 0:
        return [], pos
    return data[:end].decode("utf-8", "replace").split("\n"), pos + end + 1


def conversation(path, pos=0):
    """The MAIN-THREAD conversation for the dashboard's merged mirror stream
    (docs/dashboard.md): every prompt / assistant message / teammate message
    from byte `pos` on, in transcript order, each carrying `anchor` — the id
    of the last tool_use seen BEFORE it. Ops carry the same ids (`g`/`v`), so
    the dashboard can interleave conversation into the op stream without any
    timestamp column: a message goes after its anchor's last op. anchor is
    None before the first tool — and for every record of an incremental
    (pos > 0) call, where the preceding anchor is unknowable; incremental
    consumers append in arrival order instead. Returns (records, new_pos)."""
    lines, new_pos = _complete_lines(path, pos)
    out, anchor = [], None
    for s in lines:
        s = s.strip()
        if not s:
            continue
        rec = parse_line(s)
        if rec is None:
            continue
        kind = rec["kind"]
        if kind == "prompt":
            t = rec["text"].strip()
            if t and not t.startswith("<"):        # command/caveat wrappers
                out.append({"kind": "prompt", "text": t, "anchor": anchor})
        elif kind == "teammsg":
            out.append({"kind": "teammsg", "text": rec["body"],
                        "sender": rec["sender"], "anchor": anchor})
        elif kind == "results":
            for text in rec["texts"]:
                k, a, b = classify_user_text(text)
                if k == "teammsg":
                    out.append({"kind": "teammsg", "text": b, "sender": a,
                                "anchor": anchor})
                elif text.strip() and not text.strip().startswith("<"):
                    out.append({"kind": "prompt", "text": text.strip(),
                                "anchor": anchor})
        elif kind == "assistant":
            for bkind, blk in rec["blocks"]:
                if bkind == "text":
                    if blk.strip():
                        out.append({"kind": "message", "text": blk.strip(),
                                    "anchor": anchor})
                elif blk.get("id"):
                    anchor = blk["id"]
    return out, new_pos


def conversation_for(sid, pos=0):
    """The conversation provider behind plugins.conversation(): the session's
    MAIN transcript from byte `pos`. None when this plugin has no transcript
    for the sid (the fan-out then asks the next plugin) — same resolution and
    deferred-import shape as activity()."""
    from core import sessionapi as API
    row = API.session_row(sid)
    path = (row or {}).get("transcript_path") or ""
    if not path or not os.path.isfile(path):
        return None
    return conversation(path, pos)


# --- the drill-down timeline (full fidelity — deliberately UNCAPPED) ---------------

def timeline(path):
    """Parse a whole transcript into plain activity entries + a usage rollup.

    This is the read-model view (docs/sessionapi.md): text is uncapped and
    unstyled — the fidelity limit is the transcript itself (large tool outputs
    are truncated by Claude Code at the source; a tool_result rarely carries
    Read content). Entries, in transcript order:
      {"t": "prompt", "text"}                   a user prompt
      {"t": "teammsg", "sender", "body"}        incoming teammate mail
      {"t": "message", "text"[, "final": True]} assistant text ("final" marks the
                                                last entry when it is a message —
                                                the returned result, mirroring
                                                the substream's flush semantics)
      {"t": "compact", "meta"}                  a compaction boundary
      {"t": "tool", "tool", "input", "id"[, "output", "failed"]}
                                                a tool call; output/failed fill
                                                in from its tool_result
      {"t": "orphan-result", "output", "failed"} a result whose tool_use wasn't
                                                seen (checkpointed/foreign tail)
    Usage is deduped per message.id exactly like both accountants
    (accounting.usage_fold — one fold implementation, three consumers).
    Raises OSError on an unreadable path — callers own the audit/swallow."""
    from plugins.claude_code import accounting as ACC   # deferred: keep parse_line import-light
    entries, pend = [], {}
    usage_last, model = None, None
    tot = [0, 0, 0, 0, 0]
    bad = 0
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            rec = parse_line(raw)
            if rec is None:
                continue
            kind = rec["kind"]
            if kind == "bad":
                bad += 1
            elif kind == "compact":
                entries.append({"t": "compact", "meta": rec["meta"]})
            elif kind == "prompt":
                entries.append({"t": "prompt", "text": rec["text"].strip()})
            elif kind == "teammsg":
                entries.append({"t": "teammsg", "sender": rec["sender"],
                                "body": rec["body"]})
            elif kind == "results":
                for blk in rec["blocks"]:
                    out = result_text(blk.get("content"))
                    failed = bool(blk.get("is_error"))
                    e = pend.pop(blk.get("tool_use_id"), None)
                    if e is None:
                        entries.append({"t": "orphan-result", "output": out,
                                        "failed": failed})
                    else:
                        e["output"] = out
                        e["failed"] = failed
                for text in rec["texts"]:
                    tkind, a, b = classify_user_text(text)
                    if tkind == "teammsg":
                        entries.append({"t": "teammsg", "sender": a, "body": b})
                    else:
                        entries.append({"t": "prompt", "text": text.strip()})
            elif kind == "assistant":
                if rec["usage"] is not None:
                    model = rec["model"] or model
                    d, usage_last = ACC.usage_fold(
                        rec["id"], ACC.usage_fields(rec["usage"]), usage_last)
                    for i in range(5):
                        tot[i] += d[i]
                for bkind, blk in rec["blocks"]:
                    if bkind == "text":
                        if blk.strip():
                            entries.append({"t": "message", "text": blk.strip()})
                    else:
                        e = {"t": "tool", "tool": blk.get("name") or "",
                             "input": blk.get("input") or {}, "id": blk.get("id")}
                        entries.append(e)
                        if blk.get("id"):
                            pend[blk["id"]] = e
    if entries and entries[-1]["t"] == "message":
        entries[-1]["final"] = True
    return {"entries": entries, "model": model, "bad_lines": bad,
            "tools": sum(1 for e in entries if e["t"] == "tool"),
            "usage": {"in": tot[0], "out": tot[1], "cache": tot[2],
                      "create": tot[3], "create_1h": tot[4]}}


def activity(sid, agent_id=None):
    """The claude_code activity provider behind plugins.activity(): the
    timeline for a session's MAIN thread (agent_id=None, the parent transcript)
    or one of its subagents/teammates. None when this plugin has no transcript
    for the pair — the fan-out then asks the next plugin. Path resolution goes
    through core/sessionapi.py (the audit streams row is the keystone mapping;
    the subagents/ layout derivation is the fallback for streams-less agents).
    Deferred import: parse_line stays usable without the API (and the API
    imports no plugin, per the dependency rule)."""
    from core import sessionapi as API
    path = ""
    if agent_id:
        path = API.agent_transcript(sid, agent_id)
        if not path:
            row = API.session_row(sid)
            tp = (row or {}).get("transcript_path") or ""
            if tp:
                path = agent_paths(tp, agent_id)[0]
    else:
        row = API.session_row(sid)
        path = (row or {}).get("transcript_path") or ""
    if not path or not os.path.isfile(path):
        return None
    return timeline(path)
