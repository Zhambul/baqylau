# plugins/codex/rollout.py — codex ROLLOUT-record parsing.
#
# The parse half of the codex stream's parse/paint split — the same shape as
# plugins/claude_code/transcript.py (docs/sessionapi.md). This module is the
# ONE owner of the codex rollout record shapes (styleguide single-owner
# table): the `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` event grammar —
# turn_context / event_msg / response_item discrimination, the exec-arguments
# decode, the patch-change line counts, the exec-output exit extraction, and
# the cumulative total_token_usage field mapping (usage_split). Two
# presenters consume its records:
#
#   plugins/codex/stream.py Renderer.feed_rollout — the mirror's CAPPED,
#       styled paint (byte-identical to the pre-split renderer; the e2e
#       codex suite is the equivalence pin)
#   timeline() below — the UNCAPPED drill-down read model behind
#       plugins.activity() (activity() below is the codex provider)
#
# parse(o) takes one DECODED rollout object and returns a typed record
# (None = nothing renderable — unknown types fall through silently, exactly
# as the pre-split renderer did):
#   {"kind": "turn_context", "model": str, "effort": str}
#   {"kind": "usage", "usage": dict}     cumulative total_token_usage snapshot
#   {"kind": "patch", "success": bool,
#    "files": [{"path", "change", "added", "removed"}, …]}
#   {"kind": "compact"} | {"kind": "task_started", "at": …}
#   {"kind": "task_complete", "at": …} | {"kind": "turn_aborted"}
#   {"kind": "prompt" | "reasoning" | "message", "text": str}   (never empty)
#   {"kind": "search", "query": str}
#   {"kind": "exec", "cmd": str, "call_id": str}
#   {"kind": "exec_result", "exit": str|None, "output": str, "call_id": str}
# parse_line(s) wraps json.loads: {"kind": "bad", "raw": s} for a complete
# line that isn't JSON. parse_line/parse are pure (no I/O, no state); the
# only I/O here is timeline()/activity()'s own file read.
import json
import os
import re

# The exec output's exit-status head line ("Exit code: 2" / "Process exited
# with code 2") — scanned only in the head window: the status line leads the
# output, and a multi-MB output must not be regex-walked whole.
EXIT_RE = re.compile(r"(?:^|\n)(?:Exit code|Process exited with code)[: ]+(\d+)")
EXIT_SCAN_B = 300


def _patch_delta(ch):
    """(added, removed) line counts for one patch_apply_end change entry."""
    t = ch.get("type")
    if t == "add":
        return len((ch.get("content") or "").splitlines()), 0
    if t == "delete":
        return 0, len((ch.get("content") or "").splitlines())
    add = rem = 0
    for ln in (ch.get("unified_diff") or "").splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    return add, rem


def usage_split(u):
    """The ONE total_token_usage → (fresh_in, out, cached, total_in) mapping:
    codex's cumulative input_tokens INCLUDES the cached share, so fresh billed
    input is input - cached. Both consumers — the stream footer's rollup/fold
    and timeline()'s usage dict — call this; re-encoding the arithmetic
    per-site is banned (styleguide single-owner rule)."""
    tin = int(u.get("input_tokens") or 0)
    tcache = int(u.get("cached_input_tokens") or 0)
    tout = int(u.get("output_tokens") or 0)
    return max(tin - tcache, 0), tout, tcache, tin


# --- one parser per record shape (the _EVENT/_RESP registries below) --------

def _turn_context(p):
    eff = (((p.get("collaboration_mode") or {}).get("settings") or {})
           .get("reasoning_effort") or "").strip()
    return {"kind": "turn_context", "model": (p.get("model") or "").strip(),
            "effort": eff}


def _ev_token_count(p):
    # Cumulative usage snapshot (info is null on rate-limit-only events).
    u = (p.get("info") or {}).get("total_token_usage") if isinstance(
        p.get("info"), dict) else None
    return {"kind": "usage", "usage": u} if isinstance(u, dict) else None


def _ev_patch_apply_end(p):
    # The authoritative file-op record: RESOLVED absolute paths + per-file
    # diffs. The apply_patch response_item is deliberately NOT parsed — it
    # only carries repo-relative patch text; surfacing both would duplicate.
    files = []
    for path, ch in (p.get("changes") or {}).items():
        if not isinstance(ch, dict):
            continue
        add, rem = _patch_delta(ch)
        files.append({"path": path, "change": ch.get("type"),
                      "added": add, "removed": rem})
    return {"kind": "patch", "success": bool(p.get("success")), "files": files}


def _ev_context_compacted(p):
    return {"kind": "compact"}


def _ev_task_started(p):
    return {"kind": "task_started", "at": p.get("started_at")}


def _ev_task_complete(p):
    return {"kind": "task_complete", "at": p.get("completed_at")}


def _ev_turn_aborted(p):
    return {"kind": "turn_aborted"}


def _ev_user_message(p):
    msg = (p.get("message") or "").strip()
    return {"kind": "prompt", "text": msg} if msg else None


def _ev_agent_reasoning(p):
    txt = (p.get("text") or "").strip()
    return {"kind": "reasoning", "text": txt} if txt else None


def _ev_agent_message(p):
    msg = (p.get("message") or "").strip()
    return {"kind": "message", "text": msg} if msg else None


def _rsp_web_search_call(p):
    q = (p.get("action") or {}).get("query") or ""
    return {"kind": "search", "query": q} if q else None


def _rsp_function_call_output(p):
    out = p.get("output") or ""
    m = EXIT_RE.search(out[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": out, "call_id": p.get("call_id") or ""}


def _rsp_function_call(p):
    if p.get("name") != "exec_command":
        return None
    try:
        args = json.loads(p.get("arguments") or "{}")
    except Exception:
        args = {}
    cmd = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    if not cmd:
        return None
    return {"kind": "exec", "cmd": cmd, "call_id": p.get("call_id") or ""}


_EVENT = {"token_count": _ev_token_count, "patch_apply_end": _ev_patch_apply_end,
          "context_compacted": _ev_context_compacted,
          "task_started": _ev_task_started, "task_complete": _ev_task_complete,
          "turn_aborted": _ev_turn_aborted, "user_message": _ev_user_message,
          "agent_reasoning": _ev_agent_reasoning,
          "agent_message": _ev_agent_message}
_RESP = {"web_search_call": _rsp_web_search_call,
         "function_call_output": _rsp_function_call_output,
         "function_call": _rsp_function_call}


def parse(o):
    """One decoded rollout object -> a typed record (module header) or None."""
    t = o.get("type")
    p = o.get("payload") or {}
    if t == "turn_context":
        return _turn_context(p)
    if t == "event_msg":
        h = _EVENT.get(p.get("type"))
        return h(p) if h else None
    if t == "response_item":
        h = _RESP.get(p.get("type"))
        return h(p) if h else None
    return None


def parse_line(s):
    """One rollout JSONL line -> a typed record; {"kind": "bad", "raw": s}
    when the line isn't JSON at all (the stream keeps its own json.loads so
    its malformed-line audit contract stays where it was)."""
    try:
        o = json.loads(s)
    except Exception:
        return {"kind": "bad", "raw": s}
    return parse(o)


# --- the drill-down timeline (full fidelity — deliberately UNCAPPED) --------

def timeline(path):
    """Parse a whole rollout into the SAME activity-timeline dict shape
    plugins/claude_code/transcript.timeline returns ({"entries", "model",
    "tools", "usage", "bad_lines"}), so the dashboard's drill-down renders a
    codex run with zero special-casing. Entry mapping:
      user_message  -> {"t": "prompt", "text"}
      agent_message -> {"t": "message", "text"[, "final": True on the last]}
      exec          -> {"t": "tool", "tool": "exec_command",
                        "input": {"cmd"}, "id"} — paired with its
                       function_call_output by call_id (the tool_use_id
                       analog); output/failed fill in from the result, an
                       unmatched output is an {"t": "orphan-result"}
      web_search    -> {"t": "tool", "tool": "web_search", "input": {"query"}}
      patch         -> one {"t": "tool", "tool": "apply_patch"} per changed
                       file (input: file_path/change/±counts) — codex's own
                       vocabulary, deliberately not the mirror's Claude-look
                       verb map (that's paint, plugins/codex/stream.py's)
      compacted     -> {"t": "compact", "meta": {}}
    Reasoning records and the task/turn lifecycle are NOT timeline entries —
    the same fidelity line the claude timeline draws (its parse_line drops
    thinking blocks). usage is the run's LAST cumulative total_token_usage
    through usage_split (create=0: codex reports no cache-creation category).
    Raises OSError on an unreadable path — callers own the audit/swallow."""
    entries, pend = [], {}
    model, usage, bad = None, None, 0
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = parse_line(raw)
            except Exception:
                # A JSON line whose payload defeats a field walk (e.g. a
                # non-dict `action`): counted as bad — surfaced in the
                # returned bad_lines figure, not swallowed silently.
                bad += 1
                continue
            if rec is None:
                continue
            k = rec["kind"]
            if k == "bad":
                bad += 1
            elif k == "turn_context":
                model = rec["model"] or model
            elif k == "usage":
                usage = rec["usage"]
            elif k == "prompt":
                entries.append({"t": "prompt", "text": rec["text"]})
            elif k == "message":
                entries.append({"t": "message", "text": rec["text"]})
            elif k == "compact":
                entries.append({"t": "compact", "meta": {}})
            elif k == "search":
                entries.append({"t": "tool", "tool": "web_search",
                                "input": {"query": rec["query"]}, "id": None})
            elif k == "exec":
                e = {"t": "tool", "tool": "exec_command",
                     "input": {"cmd": rec["cmd"]}, "id": rec["call_id"] or None}
                entries.append(e)
                if rec["call_id"]:
                    pend[rec["call_id"]] = e
            elif k == "exec_result":
                failed = bool(rec["exit"] and rec["exit"] != "0")
                e = pend.pop(rec["call_id"], None)
                if e is None:
                    entries.append({"t": "orphan-result",
                                    "output": rec["output"], "failed": failed})
                else:
                    e["output"] = rec["output"]
                    e["failed"] = failed
            elif k == "patch":
                if rec["success"]:
                    for f in rec["files"]:
                        entries.append({"t": "tool", "tool": "apply_patch",
                                        "input": {"file_path": f["path"],
                                                  "change": f["change"],
                                                  "added": f["added"],
                                                  "removed": f["removed"]},
                                        "id": None})
                else:
                    entries.append({"t": "tool", "tool": "apply_patch",
                                    "input": {}, "id": None, "failed": True})
    if entries and entries[-1]["t"] == "message":
        entries[-1]["final"] = True
    fresh, tout, tcache, _tin = usage_split(usage or {})
    return {"entries": entries, "model": model, "bad_lines": bad,
            "tools": sum(1 for e in entries if e["t"] == "tool"),
            "usage": {"in": fresh, "out": tout, "cache": tcache,
                      "create": 0, "create_1h": 0}}


def activity(sid, agent_id=None):
    """The codex activity provider behind plugins.activity(): the timeline of
    one codex run of a hosting session (agent_id = the sessionapi.codex_aid
    identity the agents() list shows for kind='codex' streams rows), or —
    with agent_id=None — of a STANDALONE codex session's own rollout (the
    rollout filename uuid IS the sid, watch.py's standalone match, so the
    derived aid ends in "-<sid>"). None when the pair isn't a codex run here,
    when the run is a companion job (its .log activity stream is not a
    rollout — no parse), or when the rollout file is gone. Resolution reads
    the audit streams keystone through core/sessionapi.codex_runs(); imports
    are deferred so parse()/parse_line stay usable without the API (and the
    API imports no plugin, per the dependency rule)."""
    from core import sessionapi as API
    for run in API.codex_runs(sid):
        aid = run["agent_id"]
        hit = (aid == agent_id) if agent_id else aid.endswith("-" + sid)
        if not hit:
            continue
        path = run["transcript"]
        if path.endswith(".jsonl") and os.path.isfile(path):
            return timeline(path)
    return None
