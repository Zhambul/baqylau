# plugins/codex/rollout.py — codex ROLLOUT-record parsing.
#
# The parse half of the codex stream's parse/paint split — the same shape as
# plugins/claude_code/transcript.py (docs/sessionapi.md). This module is the
# ONE owner of the codex rollout record shapes (styleguide single-owner
# table): the `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` event grammar —
# turn_context / event_msg / response_item discrimination, the exec-arguments
# decode, the patch-change line counts, the exec-output exit extraction, and
# the cumulative total_token_usage field mapping (usage_split). ONE presenter
# consumes its records:
#
#   plugins/codex/stream.py Renderer.feed_rollout — the mirror's CAPPED,
#       styled paint (byte-identical to the pre-split renderer; the e2e
#       codex suite is the equivalence pin)
#
# There was a second — an uncapped drill-down timeline behind plugins.activity()
# — and it is gone with that fan-out: a codex run's web view is the mirror it
# already paints, scoped (docs/dashboard.md *Agent scope*).
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
# line that isn't JSON. parse_line/parse are pure (no I/O, no state) — with the
# timeline gone this module does no I/O at all.
import json
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
    input is input - cached. The stream footer's rollup/fold calls this;
    re-encoding the arithmetic per-site is banned (styleguide single-owner
    rule)."""
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
