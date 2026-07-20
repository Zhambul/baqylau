# plugins/claude_code/transcript.py — Claude Code transcript PARSING.
#
# The parse half of the substream's parse/paint split (docs/sessionapi.md).
# This module is the ONE owner of the Claude Code transcript JSONL record
# shapes — reader AND writer: the type/user/assistant discrimination, the
# teammate-message unwrapping, the content-block walk, the tool_result text
# normalisation — for BOTH a subagent's transcript (subagents/agent-<id>.jsonl)
# and the parent session's own transcript (the same record grammar); the one
# sanctioned WRITE is set_session_title()'s `agent-name` naming-record append
# (the dashboard's web rename). Two presenters consume its records:
#
#   substream_render.Renderer.handle_line  — the mirror's CAPPED, styled paint
#   timeline() below                       — the UNCAPPED drill-down entries
#                                             behind plugins.activity(), read
#                                             through core/sessionapi.py
#
# Re-encoding a transcript record shape anywhere else is a bug (styleguide
# single-owner table). parse_line() is pure (no I/O, no state); the only
# I/O here is timeline()/activity()'s own file read and set_session_title()'s
# one-line append.
#
# parse_line(s) returns one record per JSONL line (None = nothing renderable):
#   {"kind": "bad", "raw": s}                       unparseable JSON
#   {"kind": "compact", "meta": {...}}              a compact_boundary system record
#   {"kind": "prompt", "text": str}                 a user prompt (unstripped) —
#       a plain `user` string OR a `queued_command` attachment (the delivered
#       form of a message queued mid-turn; commandMode=="prompt" only)
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
#   {"kind": "monitor_event", "task": str, "summary": str,
#    "event": str|None, "status": str|None}
#       a Monitor tool's EVENT — Claude Code delivers each one mid-turn as a
#       `queue-operation` record whose `content` is a <task-notification> XML
#       block (one per event; a final <status>completed</status> when the
#       monitor's stream ends). Empirically confirmed (docs/streaming.md). The
#       drill-down timeline surfaces these; conversation()/the mirror do NOT —
#       the events already ride the ops stream via claude-stream.py, so
#       re-emitting there would DOUBLE them.
import json
import os
import re
from datetime import datetime

# A message DELIVERED to a teammate appears in its transcript as a plain user
# record whose text is wrapped in <teammate-message teammate_id="<sender>" …>BODY
# </teammate-message> (the very first one is the lead's spawn prompt).
TEAMMSG = re.compile(r'^\s*<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>\s*$', re.S)
_TM_ID  = re.compile(r'teammate_id="([^"]*)"')

# A Monitor EVENT is delivered as a `queue-operation` record whose `content` is
# a <task-notification> XML block (docs/streaming.md, *Monitor events in the
# transcript*). We read it with plain tag scans rather than an XML parser: the
# blocks are small, fixed-shape, and produced by Claude Code (not user input).
_TASK_NOTE = re.compile(r'<task-notification>(.*?)</task-notification>', re.S)


def _note_tag(xml, name):
    m = re.search(r'<%s>(.*?)</%s>' % (name, name), xml, re.S)
    return m.group(1).strip() if m else None


def _monitor_note(content):
    """A queue-operation's `content` -> a monitor_event record, or None when it
    isn't a <task-notification> (queue-operation carries other harness traffic
    too). `event` is the per-event line; `status` (e.g. "completed") marks the
    stream-ended notification, which carries no `event`."""
    if not isinstance(content, str) or "<task-notification>" not in content:
        return None
    m = _TASK_NOTE.search(content)
    xml = m.group(1) if m else content
    return {"kind": "monitor_event",
            "task": _note_tag(xml, "task-id") or "",
            "summary": _note_tag(xml, "summary") or "",
            "event": _note_tag(xml, "event"),
            "status": _note_tag(xml, "status")}


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
    if t == "attachment":
        # A message typed while a turn is running is QUEUED by Claude Code and,
        # when the turn boundary delivers it, recorded ONLY as this
        # `queued_command` attachment — never as a plain `user` string (verified
        # across the transcript corpus). So a mid-turn queued message is the one
        # user prompt that never reaches conversation()/timeline() as a prompt:
        # the dashboard mirror silently drops it AND the composer's ⧗ chip never
        # drains (drainQueue matches a delivered prompt by text) — the "queued
        # message stuck / missing from the transcript" report. Surface it as a
        # prompt so both work. `commandMode` separates real prompts (human +
        # auto-continuation) from the `task-notification` re-injections (which
        # are harness noise, not user turns); conversation()'s own `<`-wrapper
        # filter still drops any command/caveat wrapper, same as a typed prompt.
        att = o.get("attachment") or {}
        if att.get("type") == "queued_command" and att.get("commandMode") == "prompt":
            return {"kind": "prompt", "text": att.get("prompt") or ""}
        return None
    if t == "queue-operation":
        # A Monitor tool's events land here (see _monitor_note / the module
        # header). None for any other queue-operation (harness noise).
        return _monitor_note(o.get("content"))
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

TITLE_TAIL_B = 65536    # tail-window bytes session_title scans for the LAST naming
#                         record: `ai-title` rows are re-emitted every few turns, so
#                         the current one sits within lines of EOF — the bounded tail
#                         keeps the no-full-read rule while a mid-file `agent-name`
#                         in a >64KB transcript is the one accepted gap


def _title_records(path):
    """(agent_name, ai_title) — the LAST naming record of each kind in the tail
    window (docs/session-naming-findings.md): `agent-name`/`agentName` is the
    /rename custom name, `ai-title`/`aiTitle` the auto title Claude Code's OSC
    tab title mirrors. '' / '' when absent or unreadable."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TITLE_TAIL_B))
            lines = fh.read().split(b"\n")
    except OSError:
        return "", ""
    if size > TITLE_TAIL_B:
        lines = lines[1:]                   # first line of a mid-file seek is torn
    named, ai = "", ""
    for raw in lines:
        if b'"agent-name"' not in raw and b'"ai-title"' not in raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        if o.get("type") == "agent-name":
            named = o.get("agentName") or named
        elif o.get("type") == "ai-title":
            ai = o.get("aiTitle") or ai
    return named, ai


def session_title(path):
    """Best-effort display TITLE for a session transcript — what the kitty tab
    (Claude Code's OSC title) and the `claude --resume` picker show: the last
    `agent-name` (a /rename custom name — never clobbered by auto titles), else
    the last `ai-title`, else the LAST `summary` record in the head window,
    else the first line of the first REAL user prompt (isMeta rows and
    `<command-*>`/`<local-command-*>` wrappers are plumbing, not prompts).
    '' when unreadable / nothing found."""
    named, ai = _title_records(path)
    if named or ai:
        return named or ai
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


def set_session_title(path, name):
    """Append the `agent-name` naming record — the /rename channel `_title_records`
    parses back (docs/session-naming-findings.md §2) — to a Claude session
    transcript: the web rename's write half. True on success; None when `path`
    is not a Claude session transcript (`…/projects/<hash>/<sid>.jsonl` — a
    codex standalone host's transcript_path is a codex ROLLOUT, and a missing
    file must never be created just to name it). OSError propagates — the
    caller (dashboard/server.py post_rename) turns it into a 502 + A.error;
    this is a user-facing request/reply path, not a hook, so no swallow here.
    `sessionId` derives from the FILENAME stem, not the caller's sid — an
    adopt/fork chain's current sid differs from the transcript's own (the
    findings doc: "sessionId must match the filename")."""
    if not path.endswith(".jsonl") or not os.path.isfile(path) or \
            os.path.basename(os.path.dirname(os.path.dirname(path))) != "projects":
        return None
    sid = os.path.basename(path)[:-len(".jsonl")]
    rec = json.dumps({"type": "agent-name", "agentName": name,
                      "sessionId": sid}, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(rec + "\n")                # ONE write: atomic O_APPEND line
    return True


CTX_TAIL_B = 262144     # tail-window bytes context_probe scans backwards for the
#                         LAST assistant-usage record: a single Write/tool_result
#                         line can run >100KB, so the naming-record window (64KB)
#                         is too tight; the bounded read keeps the no-full-read
#                         rule — a transcript whose final assistant record sits
#                         deeper than this simply shows no ctx


def context_probe(path, main=False):
    """Context saturation from a transcript's tail — the LAST assistant
    record's usage IS the occupied window of the most recent turn (fresh +
    cache-write + cache-read input; model.context_used), and its `model` id
    resolves the window size (model.context_window). Returns {"used",
    "window", "pct", "model"}, or None (unreadable / no assistant usage in the
    tail window — a fresh session, or a codex rollout this parser doesn't
    speak). main=True skips isSidechain records: an inline sidechain turn in a
    MAIN transcript belongs to its agent, and its (smaller) usage would paint a
    phantom shrink over the main thread's fill — the same main/agent split as
    accounting.bump_transcript vs fold_usage."""
    from plugins.claude_code import model as M   # deferred: keep parse_line import-light
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - CTX_TAIL_B))
            lines = fh.read().split(b"\n")
    except OSError:
        return None
    if size > CTX_TAIL_B:
        lines = lines[1:]                   # first line of a mid-file seek is torn
    for raw in reversed(lines):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue
        if not isinstance(o, dict) or o.get("type") != "assistant":
            continue
        if main and o.get("isSidechain"):
            continue
        msg = o.get("message") or {}
        used = M.context_used(msg.get("usage"))
        if used <= 0:
            continue
        window = M.context_window(msg.get("model"))
        return {"used": used, "window": window,
                "pct": min(100, used * 100 // window),
                "model": msg.get("model") or ""}
    return None


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


def _iso_epoch(v):
    """A transcript line's ISO-8601 `timestamp` (e.g. "2026-07-17T12:34:56.789Z")
    as an epoch float, or None when absent/unparseable. The trailing Z is
    normalised to +00:00 so datetime.fromisoformat accepts it on the system
    python3 (pre-3.11). Best-effort — the caller falls back to the anchor."""
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _line_ts(s):
    """The `timestamp` epoch of a raw transcript line (parse_line is fed the
    same string; a second parse only for the few lines that yield conversation
    records — prompts/messages/mail — not every noise line)."""
    try:
        return _iso_epoch(json.loads(s).get("timestamp"))
    except Exception:
        return None


def conversation(path, pos=0):
    """The MAIN-THREAD conversation for the dashboard's merged mirror stream
    (docs/dashboard.md): every prompt / assistant message / teammate message
    from byte `pos` on, in transcript order, each carrying `ts` — the line's
    `timestamp` as an epoch float (None when absent) — and `anchor`, the id of
    the last tool_use seen BEFORE it. Ops carry both a wall-clock `_ts` and the
    same tool ids (`g`/`v`), so the dashboard interleaves conversation into the
    op stream by TIMESTAMP when both sides have one, falling back to the anchor
    (a message goes after its anchor's last op) for pre-migration history.
    anchor is None before the first tool — and for every record of an
    incremental (pos > 0) call, where the preceding anchor is unknowable;
    incremental consumers append in arrival order instead. Returns
    (records, new_pos)."""
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
        ts = _line_ts(s)
        if kind == "prompt":
            t = rec["text"].strip()
            if t and not t.startswith("<"):        # command/caveat wrappers
                out.append({"kind": "prompt", "text": t, "anchor": anchor,
                            "ts": ts})
        elif kind == "teammsg":
            out.append({"kind": "teammsg", "text": rec["body"],
                        "sender": rec["sender"], "anchor": anchor, "ts": ts})
        elif kind == "results":
            # an AskUserQuestion ANSWER is a tool_result, not plain user text,
            # so it lands in `blocks` — which this stream otherwise drops (tool
            # results are the terminal mirror's job, as ops). Surface it so a
            # web-submitted answer actually shows in the dashboard mirror (the
            # "my answer didn't appear" report, 2026-07-19). The toolUseResult
            # sidecar is a dict carrying `answers` for exactly this tool; the
            # tool_result content string is Claude Code's clean "Your questions
            # have been answered: …" recap (docs/dashboard.md, *Web ask*).
            tur = rec.get("tur")
            if isinstance(tur, dict) and "answers" in tur:
                for blk in rec["blocks"]:
                    txt = result_text(blk.get("content")).strip()
                    if txt:
                        out.append({"kind": "answer", "text": txt,
                                    "anchor": anchor, "ts": ts})
            for text in rec["texts"]:
                k, a, b = classify_user_text(text)
                if k == "teammsg":
                    out.append({"kind": "teammsg", "text": b, "sender": a,
                                "anchor": anchor, "ts": ts})
                elif text.strip() and not text.strip().startswith("<"):
                    out.append({"kind": "prompt", "text": text.strip(),
                                "anchor": anchor, "ts": ts})
        elif kind == "assistant":
            for bkind, blk in rec["blocks"]:
                if bkind == "text":
                    if blk.strip():
                        out.append({"kind": "message", "text": blk.strip(),
                                    "anchor": anchor, "ts": ts})
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

def _fold_record(rec, entries, pend, acc, on_unresolved, ACC):
    """Fold ONE parse_line record into the timeline accumulators — the single
    per-record entry builder shared by timeline() and timeline_since() (the
    styleguide single-owner rule: the record-shape → entry mapping lives here,
    nowhere else). `entries` gains the record's entries in transcript order;
    `pend` maps a tool_use id → its (mutable) tool entry so a later tool_result
    patches `output`/`failed` in place; `acc` (keys "usage_last"/"model"/"tot"
    [5]/"bad") carries the usage-fold cursor + rollup + bad-line count.

    on_unresolved(entries, tool_use_id, output, failed) fires — INLINE, so its
    entry keeps its position within a results record — for a tool_result whose
    tool_use isn't in `pend`. The two callers diverge only here: whole-file
    timeline() appends an orphan-result entry (the tool_use genuinely never
    appeared); timeline_since() records a cross-increment resolution (the
    tool_use was in an EARLIER increment, already serialized and sent)."""
    kind = rec["kind"]
    if kind == "bad":
        acc["bad"] += 1
    elif kind == "compact":
        entries.append({"t": "compact", "meta": rec["meta"]})
    elif kind == "prompt":
        entries.append({"t": "prompt", "text": rec["text"].strip()})
    elif kind == "teammsg":
        entries.append({"t": "teammsg", "sender": rec["sender"],
                        "body": rec["body"]})
    elif kind == "monitor_event":
        entries.append({"t": "monitor", "task": rec["task"],
                        "summary": rec["summary"], "event": rec.get("event"),
                        "status": rec.get("status")})
    elif kind == "results":
        for blk in rec["blocks"]:
            out = result_text(blk.get("content"))
            failed = bool(blk.get("is_error"))
            e = pend.pop(blk.get("tool_use_id"), None)
            if e is None:
                on_unresolved(entries, blk.get("tool_use_id"), out, failed)
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
            acc["model"] = rec["model"] or acc["model"]
            d, acc["usage_last"] = ACC.usage_fold(
                rec["id"], ACC.usage_fields(rec["usage"]), acc["usage_last"])
            for i in range(5):
                acc["tot"][i] += d[i]
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


def _read(path, pos, on_unresolved):
    """Fold the COMPLETE JSONL lines from byte `pos` through _fold_record,
    returning (entries, acc, new_pos). Uses the torn-record-safe _complete_lines
    cursor (a trailing partial line is not consumed), so a byte-window read
    never parses half a record — the same discipline conversation() uses. An
    unreadable path yields ([], fresh-acc, pos) (callers guard existence)."""
    from plugins.claude_code import accounting as ACC   # deferred: keep parse_line import-light
    lines, new_pos = _complete_lines(path, pos)
    entries, pend = [], {}
    acc = {"usage_last": None, "model": None, "tot": [0, 0, 0, 0, 0], "bad": 0}
    for s in lines:
        s = s.strip()
        if not s:
            continue
        rec = parse_line(s)
        if rec is None:
            continue
        _fold_record(rec, entries, pend, acc, on_unresolved, ACC)
    return entries, acc, new_pos


def _append_orphan(entries, tool_use_id, output, failed):
    """The whole-file (timeline()/activity()) on_unresolved: a tool_result with
    no preceding tool_use is a genuine orphan (checkpointed/foreign tail)."""
    entries.append({"t": "orphan-result", "output": output, "failed": failed})


def _rollup(entries, acc):
    """Shape the read's (entries, acc) into the timeline dict — the returned
    dict shape both timeline() and activity() hand out. The last entry is
    marked `final` when it is a message (the returned result, mirroring the
    substream's flush semantics)."""
    if entries and entries[-1]["t"] == "message":
        entries[-1]["final"] = True
    tot = acc["tot"]
    return {"entries": entries, "model": acc["model"], "bad_lines": acc["bad"],
            "tools": sum(1 for e in entries if e["t"] == "tool"),
            "usage": {"in": tot[0], "out": tot[1], "cache": tot[2],
                      "create": tot[3], "create_1h": tot[4]}}


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
      {"t": "monitor", "task", "summary", "event", "status"}
                                                a Monitor tool event (or its
                                                stream-ended `status`) — see
                                                parse_line's monitor_event record
      {"t": "tool", "tool", "input", "id"[, "output", "failed"]}
                                                a tool call; output/failed fill
                                                in from its tool_result
      {"t": "orphan-result", "output", "failed"} a result whose tool_use wasn't
                                                seen (checkpointed/foreign tail)
    Usage is deduped per message.id exactly like both accountants
    (accounting.usage_fold — one fold implementation, three consumers). The
    per-record building is shared with timeline_since() via _fold_record."""
    entries, acc, _pos = _read(path, 0, _append_orphan)
    return _rollup(entries, acc)


def timeline_since(path, pos):
    """Incremental timeline from byte cursor `pos` — the LIVE-growth companion
    to timeline() behind plugins.activity_since() (docs/dashboard.md). Returns
    (entries, resolutions, new_pos):

      entries      the new increment's entries (same shapes as timeline(), sans
                   the whole-file `final` marking — a live turn isn't over), in
                   transcript order, each carrying its CURRENT state.
      resolutions  [(tool_use_id, output, failed), …] for every tool_result in
                   this window whose tool_use is NOT in the window: a tool_use
                   whose result lands in a LATER call can't be patched in place
                   (its entry was already serialized and sent), so the consumer
                   fills in the earlier entry by tool_use id — or ignores it (a
                   genuine orphan whose tool_use it never saw either; increments
                   deliberately do NOT emit orphan-result entries, since a
                   window can't tell a cross-increment result from a true
                   orphan). A tool_use and its result in the SAME window still
                   pair in place, exactly as in timeline().
      new_pos      the resume cursor (stops before a trailing partial line).

    Usage is deliberately OMITTED: usage_fold dedups by message.id with a
    running cursor that can't survive a per-call byte window (a message split
    across the boundary would mis-delta), and the drill-down header's rollup is
    a whole-file figure from the initial /agent fetch, not a live counter."""
    resolutions = []

    def _resolve(entries, tool_use_id, output, failed):
        resolutions.append((tool_use_id, output, failed))

    entries, _acc, new_pos = _read(path, pos, _resolve)
    return entries, resolutions, new_pos


def _timeline_path(sid, agent_id):
    """Resolve the transcript path for (sid, agent_id) — None when this plugin
    has none. Shared by activity()/activity_since(). Goes through
    core/sessionapi.py (the audit streams row is the keystone mapping; the
    subagents/ layout derivation is the fallback for streams-less agents).
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
    return path


def activity(sid, agent_id=None):
    """The claude_code activity provider behind plugins.activity(): the
    timeline for a session's MAIN thread (agent_id=None, the parent transcript)
    or one of its subagents/teammates. None when this plugin has no transcript
    for the pair — the fan-out then asks the next plugin. Carries an additive
    `pos` (the byte cursor after the last complete line read) so a consumer can
    hand it to activity_since() for a race-free live resume."""
    path = _timeline_path(sid, agent_id)
    if not path:
        return None
    entries, acc, new_pos = _read(path, 0, _append_orphan)
    tl = _rollup(entries, acc)
    tl["pos"] = new_pos
    return tl


def activity_since(sid, agent_id, pos):
    """The claude_code LIVE drill-down provider behind plugins.activity_since():
    timeline_since over the same transcript activity() resolves, from byte
    cursor `pos`. None when this plugin has no transcript for the pair (the
    fan-out then asks the next plugin — codex declines, no incremental
    provider)."""
    path = _timeline_path(sid, agent_id)
    if not path:
        return None
    return timeline_since(path, pos)


# --- the monitors read-model (the dashboard's monitors tab) ------------------------

# The taskId in a Monitor tool's "Monitor started (task <id>, …)" result — the
# ONE way to map a Monitor tool_use to the taskId its events (and the audit
# streams row) are keyed by (the tool_use INPUT carries no taskId).
_MON_TASK = re.compile(r'\btask\s+([A-Za-z0-9]+)')
# Cap on events carried per monitor in the read model: a chatty persistent
# monitor can fire thousands over a session, and the whole list is one JSON
# response. Keep the most RECENT (the drill-down's live tail); `event_count`
# stays exact and `events_truncated` flags the elision.
MON_EVENT_CAP = 2000


def monitors(path):
    """Every Monitor tool run in a transcript, in launch order, each with its
    command/description/lifetime and its EVENTS (the queue-operation
    <task-notification> records — parse_line's monitor_event; docs/streaming.md).
    Keyed by taskId (from the "Monitor started (task <id>)" result). A run whose
    launch we never saw (a truncated transcript head) still appears from its
    events alone, command/description blank. Pure read (one file scan); [] when
    unreadable.

    Each dict: {task, command, description, source ("command"|"ws"),
    persistent, timeout_ms, launched_at, tool_use_id, events:[…]} where an event
    is {"event": str, "ts": float|None} or, for the stream-ended notification,
    {"status": str, "summary": str, "ts": float|None}."""
    launches, mons, order = {}, {}, []

    def ensure(task):
        if task not in mons:
            mons[task] = {"task": task, "command": "", "description": "",
                          "source": "", "persistent": None, "timeout_ms": None,
                          "launched_at": None, "tool_use_id": "", "events": []}
            order.append(task)
        return mons[task]

    try:
        with open(path, encoding="utf-8") as fh:
            for s in fh:
                s = s.strip()
                if not s:
                    continue
                rec = parse_line(s)
                if rec is None:
                    continue
                k = rec["kind"]
                if k == "assistant":
                    for bkind, blk in rec["blocks"]:
                        if bkind != "text" and blk.get("name") == "Monitor":
                            launches[blk.get("id")] = {"input": blk.get("input") or {},
                                                       "ts": _line_ts(s)}
                elif k == "results":
                    for blk in rec["blocks"]:
                        L = launches.get(blk.get("tool_use_id"))
                        if L is None:
                            continue
                        m = _MON_TASK.search(result_text(blk.get("content")) or "")
                        if not m:
                            continue
                        r, inp = ensure(m.group(1)), L["input"]
                        cmd = inp.get("command") or ""
                        ws = inp.get("ws") if isinstance(inp.get("ws"), dict) else None
                        r["command"] = cmd or (ws.get("url") or "" if ws else "")
                        r["source"] = "ws" if (ws and not cmd) else "command"
                        r["description"] = " ".join((inp.get("description") or "").split())
                        r["persistent"] = bool(inp["persistent"]) if "persistent" in inp else None
                        r["timeout_ms"] = inp.get("timeout_ms")
                        r["launched_at"] = L["ts"]
                        r["tool_use_id"] = blk.get("tool_use_id") or ""
                elif k == "monitor_event":
                    ev = {"ts": _line_ts(s)}
                    if rec.get("status"):
                        ev["status"] = rec["status"]
                        ev["summary"] = rec.get("summary") or ""
                    else:
                        ev["event"] = rec.get("event") or ""
                    ensure(rec["task"])["events"].append(ev)
    except OSError:
        return []
    return [mons[t] for t in order]


def _merge_monitor(m, st):
    """One monitor's read-model dict — the transcript detail (`m`, from
    monitors()) merged with its audit `streams` lifecycle row (`st`, from
    sessionapi.monitor_streams — {} when the streamer left no row). Streams own
    the STATE (started/ended/end_reason/live), the transcript owns the
    command/description/events. `event_count` is the transcript's real event
    count (excludes the stream-ended status), falling back to the streamer's
    line tally; the carried `events` are the most-recent MON_EVENT_CAP."""
    events = m.get("events") or []
    ev_count = sum(1 for e in events if "event" in e)
    trunc = len(events) > MON_EVENT_CAP
    return {"task": m["task"],
            "command": m.get("command") or "",
            "description": m.get("description") or "",
            "source": m.get("source") or "",
            "persistent": m.get("persistent"),
            "timeout_ms": m.get("timeout_ms"),
            "tool_use_id": m.get("tool_use_id") or "",
            "started_at": st.get("started_at") or m.get("launched_at"),
            "ended_at": st.get("ended_at"),
            "end_reason": st.get("end_reason") or "",
            "live": bool(st.get("live")),
            "agent_id": st.get("agent_id") or "",
            "event_count": ev_count if events else int(st.get("lines") or 0),
            "events_truncated": trunc,
            "events": events[-MON_EVENT_CAP:] if trunc else events}


def session_monitors(sid):
    """The monitors read-model behind plugins.monitors(): every Monitor run in a
    session's MAIN transcript (monitors()), merged with its audit `streams`
    lifecycle state (sessionapi.monitor_streams). A streams row with no matching
    transcript launch (a truncated head, a subagent's monitor) still surfaces —
    state only, blank command — so a running monitor is never hidden. Sorted by
    start. None when this plugin has no transcript for the sid (the fan-out then
    asks the next plugin)."""
    from core import sessionapi as API
    row = API.session_row(sid)
    path = (row or {}).get("transcript_path") or ""
    if not path or not os.path.isfile(path):
        return None
    streams = API.monitor_streams(sid)
    out, seen = [], set()
    for m in monitors(path):
        seen.add(m["task"])
        out.append(_merge_monitor(m, streams.get(m["task"]) or {}))
    for task, st in streams.items():
        if task in seen:
            continue
        out.append(_merge_monitor({"task": task, "events": [],
                                   "launched_at": st.get("started_at")}, st))
    out.sort(key=lambda r: r.get("started_at") or 0)
    return out
