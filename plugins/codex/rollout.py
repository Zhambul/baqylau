# plugins/codex/rollout.py — codex ROLLOUT-record parsing.
#
# The parse half of the codex stream's parse/paint split — the same shape as
# plugins/claude_code/transcript.py (docs/sessionapi.md). This module is the
# ONE owner of the codex rollout record shapes (styleguide single-owner
# table): the `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` event grammar —
# turn_context / event_msg / response_item / top-level discrimination, the
# exec-arguments decode, the patch-change line counts, the exec-output exit
# extraction, the synthetic-message vocabulary, and the cumulative
# total_token_usage field mapping (usage_split).
#
# PRESENTERS (a record's consumers — there may be MORE THAN ONE; the grep
# contract in tests/test_l1f_codex_rollout.py pins only that no presenter
# re-walks the raw grammar, not that a single one exists):
#
#   plugins/codex/stream.py Renderer.feed_rollout — the mirror's CAPPED,
#       styled paint (byte-identical to the pre-split renderer; the e2e
#       codex suite is the equivalence pin). It dispatches on a `kind`
#       TABLE and silently ignores every kind it has no handler for, so a
#       record added here for another presenter never changes the mirror.
#   a dashboard `conversation` provider (a later phase) — the codex run's
#       web bubbles, off the `chat`/`think` register below.
#
# There was a third — an uncapped drill-down timeline behind plugins.activity()
# — and it is gone with that fan-out: a codex run's web view is the mirror it
# already paints, scoped (docs/dashboard.md *Agent scope*).
#
# TWO REGISTERS, deliberately not unified (docs/codex.md *Two registers*): a
# codex rollout says most things TWICE — once as an `event_msg` (codex's own
# digested UI stream) and once as a `response_item` (the model-API record the
# conversation is rebuilt from on resume). The MIRROR paints the event_msg
# register (`prompt`/`message`/`reasoning`); a CONVERSATION presenter reads the
# response_item register (`chat`/`think`), which is the complete, in-order,
# resume-restored one and the ONLY source of a post-abort / queued prompt.
# Giving the second register its own kinds is what keeps the mirror from
# painting every message and every think twice.
#
# parse(o) takes one DECODED rollout object and returns a typed record
# (None = nothing renderable — unknown types fall through silently, exactly
# as the pre-split renderer did; the grammar is VERSION-FRAGILE — verified
# drift across codex 0.95 → 0.144 — so an unknown type/payload.type must
# always be None, never an exception):
#   {"kind": "turn_context", "model": str, "effort": str}
#   {"kind": "usage", "usage": dict,      cumulative total_token_usage snapshot
#    "last": dict|None, "window": int|None}   last turn's usage + ctx window
#   {"kind": "patch", "success": bool,
#    "files": [{"path", "change", "added", "removed"}, …]}
#   {"kind": "compact"} | {"kind": "task_started", "at": …, "ts": …}
#   {"kind": "task_complete", "at": …, "ts": …} | {"kind": "turn_aborted"}
#   {"kind": "prompt" | "reasoning" | "message", "text": str}   (never empty)
#   {"kind": "search", "query": str}
#   {"kind": "exec", "cmd": str, "call_id": str, "ts": str|None}
#   {"kind": "exec_result", "exit": str|None, "output": str,
#    "call_id": str, "ts": str|None}
#   {"kind": "stdin", "text": str, "call_id": str}      backgrounded-exec poll
#   {"kind": "chat", "role": str, "text": str, "synthetic": bool}
#   {"kind": "think", "text": str}                      (never empty)
#   {"kind": "patch_call", "patch": str, "call_id": str}
#   {"kind": "ask", "call_id": str, "questions": [{"id", "header", "question",
#                                  "options": [{"label", "description"}]}]}
#   {"kind": "compact_boundary", "message": str, "replaced": int,
#    "window_id": …, "previous_window_id": …}
# parse_line(s) wraps json.loads: {"kind": "bad", "raw": s} for a complete
# line that isn't JSON. parse_line/parse are pure (no I/O, no state) — with the
# timeline gone this module does no I/O at all (owns() below is a pure
# filename/layout test — the codex twin of transcript.owns — so that stays true).
import json
import os
import re

# The exec output's exit-status head line ("Exit code: 2" / "Process exited
# with code 2") — scanned only in the head window: the status line leads the
# output, and a multi-MB output must not be regex-walked whole.
EXIT_RE = re.compile(r"(?:^|\n)(?:Exit code|Process exited with code)[: ]+(\d+)")
EXIT_SCAN_B = 300

# codex has TWO exec channels across versions (docs/codex.md, the
# custom_tool_call exec channel), both funnelled to the same
# `exec`/`exec_result` records:
#   - OLDER: a `function_call` named exec_command/shell, arguments a JSON
#     `{cmd:[…]}`; its `function_call_output` is a plain string.
#   - 0.14x+: a `custom_tool_call` named "exec" whose `input` is a JS snippet
#     `tools.exec_command({cmd:"ls",…})`, and a `custom_tool_call_output` whose
#     `output` is a list of parts led by a "Script completed…\nOutput:\n"
#     preamble. This is what a real `run ls` produced (verified 0.144.1) — the
#     reason a codex command showed NO block before: the parser knew only the
#     function_call channel.
# The command is pulled from the JS with a targeted match (never by executing
# it): a list joins on spaces, a string is taken verbatim.
_JS_CMD = re.compile(r"""cmd\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
# The custom-exec output preamble ends in this marker; the block body wants only
# what follows it (the exit is still read from the whole head window).
_OUTPUT_MARK = "Output:\n"


def _exec_cmd_from_js(js):
    """The command out of a `custom_tool_call` name=exec JS `input`, or ''."""
    m = _JS_CMD.search(js or "")
    if not m:
        return ""
    raw = m.group(1)
    try:
        v = json.loads(raw)                 # "ls" or ["bash","-lc","…"] (double-quoted)
    except Exception:
        raw = raw.strip()                   # single-quoted / unquoted — light cleanup
        return raw[1:-1] if raw[:1] in "\"'" else raw
    return " ".join(str(x) for x in v) if isinstance(v, list) else str(v)


def _exec_output_body(txt):
    """A custom-exec output stripped of codex's `…Output:\\n` status preamble, so
    the block body is the command's real output (uniform with a Claude command);
    the whole text is still what the exit is scanned from."""
    i = txt.find(_OUTPUT_MARK)
    return txt[i + len(_OUTPUT_MARK):].lstrip("\n") if i >= 0 else txt

# The canonical codex ROLLOUT path layout (docs/codex.md): a `rollout-*.jsonl`
# file under a `.../sessions/YYYY/MM/DD/` tree (`~/.codex/sessions/…` in
# production; the `sessions` ancestor is the stable, non-$HOME-pinned part a
# fixture reproduces). `owns()` recognises one by that FILENAME PREFIX + one
# ancestor dir — the single-owner codex path recogniser (docs/styleguide.md
# single-owner table), the codex twin of plugins/claude_code/transcript.owns.
# Deliberately a PURE filename/layout test: a rollout's records are the grammar
# above, but ownership must be answerable once per session per poll WITHOUT
# opening the file, and the `rollout-` stem is codex-specific (a Claude transcript
# is a bare `<uuid>.jsonl`, an agent sidecar `agent-<id>.jsonl`), so the two
# vocabularies cannot collide. The `sessions/` ancestor keeps a stray
# `rollout-*.jsonl` elsewhere from being claimed.


def owns(path):
    """Is `path` a codex rollout this plugin SPEAKS — the `owns` provider behind
    plugins._first_path (the ownership gate on every path-keyed read fan-out) and
    plugins.owns_by / host_of (the dashboard's host attribution)? True for a
    `rollout-*.jsonl` under a `sessions/` tree, False for everything else — a
    Claude transcript above all (its parsers fail OPEN on a file they never fully
    read, so an ungated fan-out would hand a codex rollout to a Claude parser; the
    same reason claude_code grew `owns`). An empty path (codex sent no
    transcript_path in its SessionStart payload) is not ours — session_caps then
    keeps the session on the empty-path default rather than attributing it here."""
    if not path or not path.endswith(".jsonl"):
        return False
    if not os.path.basename(path).startswith("rollout-"):
        return False
    return "sessions" in os.path.normpath(path).split(os.sep)[:-1]

# The SYNTHETIC-message vocabulary: a `response_item/message` whose text opens
# with one of these is codex's own machinery talking to the model, not a turn
# of the conversation — the context blocks it re-injects every turn, the
# abort marker it writes in the user's voice, the approval bookkeeping. A
# conversation presenter drops them from the bubbles (the record is still
# emitted, flagged `synthetic:True`, so a debugging view can still see them).
# Verified over a 34-file rollout corpus + fresh 0.144.1 runs; the ONE owner of
# this list — a presenter must not re-encode it.
SYNTHETIC_PREFIXES = (
    "<turn_aborted>",
    "<environment_context>",
    "<permissions instructions>",
    "<skills_instructions>",
    "<plugins_instructions>",
    "<collaboration_mode>",
    "<model_switch>",
    "<app-context>",
    "Approved command prefix saved:",
    "# AGENTS.md instructions",
)


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


def is_synthetic(text):
    """Is this `chat` text codex machinery rather than a conversation turn?
    The one reader of SYNTHETIC_PREFIXES."""
    s = (text or "").lstrip()
    return s.startswith(SYNTHETIC_PREFIXES)


def _content_text(c):
    """A response_item content list -> its text. The items are
    {"type": "input_text"|"output_text", "text": …}; older versions (and the
    custom-tool outputs) sometimes hand a bare string instead."""
    if isinstance(c, str):
        return c.strip()
    parts = []
    for it in (c or ()):
        if isinstance(it, dict) and isinstance(it.get("text"), str):
            parts.append(it["text"])
        elif isinstance(it, str):
            parts.append(it)
    return "\n".join(parts).strip()


def _args(p):
    """A function_call's `arguments` (a JSON *string*) -> a dict; {} when the
    version at hand wrote something else or the line was truncated."""
    try:
        a = json.loads(p.get("arguments") or "{}")
    except Exception:
        return {}
    return a if isinstance(a, dict) else {}


# --- one parser per record shape (the _EVENT/_RESP/_CALL/_TOP registries) ---

def _turn_context(p):
    # `reasoning_effort` moved under collaboration_mode.settings in 0.14x; the
    # bare top-level `effort` is the older (and still emitted) spelling.
    eff = (((p.get("collaboration_mode") or {}).get("settings") or {})
           .get("reasoning_effort") or p.get("effort") or "").strip()
    return {"kind": "turn_context", "model": (p.get("model") or "").strip(),
            "effort": eff}


def _ev_token_count(p):
    # Cumulative usage snapshot (info is null on rate-limit-only events).
    # `last_token_usage` + `model_context_window` ride along: the CUMULATIVE
    # total never resets across a compaction, so only the last turn's total
    # over the window measures ctx saturation.
    info = p.get("info") if isinstance(p.get("info"), dict) else {}
    u = info.get("total_token_usage")
    if not isinstance(u, dict):
        return None
    last = info.get("last_token_usage")
    win = info.get("model_context_window")
    return {"kind": "usage", "usage": u,
            "last": last if isinstance(last, dict) else None,
            "window": win if isinstance(win, int) else None}


def _ev_patch_apply_end(p):
    # The authoritative file-op record: RESOLVED absolute paths + per-file
    # diffs. The apply_patch call itself (a `patch_call` record, from either
    # the function_call or the custom_tool_call spelling) carries only
    # repo-relative patch TEXT and is deliberately a lightweight "started"
    # marker — the file ops are counted here, exactly once.
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
    # The OLDER exec channel's output. Same normalisation as the custom-tool one:
    # the exit is scanned from the FULL head (the `Chunk ID…\nWall time…\nProcess
    # exited with code N\n…Output:\n` preamble codex 0.14x prints leads it), THEN
    # the body is stripped of that preamble so a standalone block shows the real
    # output, not codex's status noise (verified live: a `run ls` used THIS
    # channel with exactly that preamble).
    out = p.get("output") or ""
    if not isinstance(out, str):
        out = _content_text(out)
    m = EXIT_RE.search(out[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": _exec_output_body(out), "call_id": p.get("call_id") or ""}


def _rsp_message(p):
    # The response_item register (module header): the conversation as the
    # model API records it — assistant/user/developer, and the ONLY place a
    # post-abort or queued prompt appears. Deliberately NOT kind "message"/
    # "prompt": those are the event_msg register the mirror paints, and one
    # turn shows up in both.
    txt = _content_text(p.get("content"))
    if not txt:
        return None
    return {"kind": "chat", "role": (p.get("role") or "").strip(),
            "text": txt, "synthetic": is_synthetic(txt)}


def _rsp_reasoning(p):
    # summary is a list of {"type": "summary_text", "text": …}; it is empty
    # whenever the think was stored as `encrypted_content` instead.
    txt = _content_text(p.get("summary"))
    return {"kind": "think", "text": txt} if txt else None


def _rsp_custom_tool_call(p):
    # codex ≥ 0.13x runs BOTH apply_patch and exec through custom tools:
    #   name="exec"       -> an exec record (cmd out of the JS input) — the
    #                        0.14x+ command channel (see _JS_CMD above).
    #   name="apply_patch"-> a lightweight "patch call started" marker; the
    #                        resolved file ops come from patch_apply_end
    #                        (_ev_patch_apply_end), counting both would double.
    # Any other custom tool degrades to None (forward-compatible).
    name = p.get("name")
    if name == "exec":
        cmd = _exec_cmd_from_js(p.get("input") or "")
        return {"kind": "exec", "cmd": cmd,
                "call_id": p.get("call_id") or ""} if cmd else None
    if name == "apply_patch":
        inp = p.get("input")
        return {"kind": "patch_call",
                "patch": inp if isinstance(inp, str) else _content_text(inp),
                "call_id": p.get("call_id") or ""}
    return None


def _rsp_custom_tool_call_output(p):
    # The output carries no tool name, so this is the exec/patch OUTPUT for
    # whatever `custom_tool_call` opened this call_id — an `exec_result` in both
    # cases, paired by call_id in the renderer: an exec's closes its command
    # block, an apply_patch's is an orphan (its file ops come from
    # patch_apply_end) that shows only a FAILED exit, never a stray block. Same
    # record shape the function_call_output (older channel) yields, so one
    # renderer path handles both.
    out = p.get("output")
    txt = out if isinstance(out, str) else _content_text(out)
    m = EXIT_RE.search(txt[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": _exec_output_body(txt), "call_id": p.get("call_id") or ""}


def _call_exec(p, args):
    cmd = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    if not cmd:
        return None
    return {"kind": "exec", "cmd": cmd, "call_id": p.get("call_id") or ""}


def _call_stdin(p, args):
    # The backgrounded-exec continuation poll: codex writes into a running
    # exec session and reads more of its output. Its function_call_output is
    # an ordinary `exec_result` — this record exists so that output is not
    # orphaned (a presenter pairs the two by call_id).
    ch = args.get("chars")
    return {"kind": "stdin", "text": ch if isinstance(ch, str) else "",
            "call_id": p.get("call_id") or ""}


def _call_ask(p, args):
    # codex's EXPERIMENTAL question tool (plan mode in practice) — the schema
    # is Claude's AskUserQuestion in codex spelling.
    out = []
    for q in (args.get("questions") or ()):
        if not isinstance(q, dict):
            continue
        opts = [{"label": (o.get("label") or ""),
                 "description": (o.get("description") or "")}
                for o in (q.get("options") or ()) if isinstance(o, dict)]
        out.append({"id": q.get("id") or "", "header": q.get("header") or "",
                    "question": q.get("question") or "", "options": opts})
    # call_id rides along so a presenter can pair the ask with its
    # function_call_output ANSWER without re-reading the raw payload (the web
    # question card's pending_dialog read — plugins/codex/read.py).
    return {"kind": "ask", "call_id": p.get("call_id") or "",
            "questions": out} if out else None


# function_call `name` → its argument grammar. `shell` is the pre-0.1x
# spelling of `exec_command` (same {command: [...]} shape) and still turns up
# in older rollouts; an unlisted name is None, so a new codex tool degrades to
# "not rendered" rather than to an exception.
_CALL = {"exec_command": _call_exec, "shell": _call_exec,
         "write_stdin": _call_stdin, "request_user_input": _call_ask}


def _rsp_function_call(p):
    h = _CALL.get(p.get("name"))
    return h(p, _args(p)) if h else None


def _top_compacted(p):
    # The TOP-LEVEL compaction record (distinct from the event_msg
    # `context_compacted` notice the mirror paints as ⟳): it is the boundary
    # itself, and `message` is usually "" because the summary is encrypted.
    # `replacement_history` — the entire rewritten conversation — is
    # deliberately NOT carried, only its length: a record shape must not be a
    # megabyte.
    hist = p.get("replacement_history")
    return {"kind": "compact_boundary", "message": p.get("message") or "",
            "replaced": len(hist) if isinstance(hist, list) else 0,
            "window_id": p.get("window_id"),
            "previous_window_id": p.get("previous_window_id")}


def _top_world_state(p):
    # A large periodic state snapshot (open files, shell sessions, todos).
    # Explicitly ignored: nothing in it is renderable and it would otherwise
    # look like an unhandled type to the next reader of this table.
    return None


_EVENT = {"token_count": _ev_token_count, "patch_apply_end": _ev_patch_apply_end,
          "context_compacted": _ev_context_compacted,
          "task_started": _ev_task_started, "task_complete": _ev_task_complete,
          "turn_aborted": _ev_turn_aborted, "user_message": _ev_user_message,
          "agent_reasoning": _ev_agent_reasoning,
          "agent_message": _ev_agent_message}
_RESP = {"web_search_call": _rsp_web_search_call,
         "function_call_output": _rsp_function_call_output,
         "function_call": _rsp_function_call,
         "message": _rsp_message, "reasoning": _rsp_reasoning,
         "custom_tool_call": _rsp_custom_tool_call,
         "custom_tool_call_output": _rsp_custom_tool_call_output}
_TOP = {"turn_context": _turn_context, "compacted": _top_compacted,
        "world_state": _top_world_state}

# Record kinds that carry the ENVELOPE's `timestamp` as a separate `ts` string.
# Two families: the task lifecycle records whose OWN timestamp field is absent in
# many codex versions (task_started/task_complete), and the exec pair — a codex
# exec record carries no duration of its own, so the standalone command block
# times itself from the exec's `ts` to its exec_result's `ts` (the elapsed on
# `■ finished · Ns`, plugins/codex/stream.py). `ts` is always the ISO envelope
# string, never folded into the numeric `at` a task duration subtracts.
_ENVELOPE_TS = ("task_started", "task_complete", "exec", "exec_result")


def _stamp(rec, o):
    if rec is not None and rec["kind"] in _ENVELOPE_TS:
        rec["ts"] = o.get("timestamp")
    return rec


# The COMPLETE set of record kinds parse()/parse_line() can return — the ONE
# owner of the codex rollout KIND vocabulary (docs/styleguide.md single-owner
# table; docs/codex.md *Kind drift contract*). Hand-maintained rather than
# derived: the kind a handler returns is NOT its registry key
# (_ev_user_message → "prompt", _ev_patch_apply_end → "patch",
# _ev_context_compacted → "compact"), so it can't be read off the
# _EVENT/_RESP/_CALL/_TOP tables. But a new or renamed kind can never drift past
# a renderer SILENTLY: tests/test_l1f_codex_rollout.py pins every kind here to
# be EITHER rendered (stream.Renderer._RO) OR explicitly ignored
# (stream.IGNORE_KINDS), and every rendered/ignored kind to be a real member
# here — so adding a parser kind fails the suite until someone decides
# render-vs-ignore. `bad` is parse_line's non-JSON record.
KINDS = frozenset({
    "turn_context", "usage", "patch", "compact", "task_started",
    "task_complete", "turn_aborted", "prompt", "reasoning", "message",
    "search", "exec", "exec_result", "stdin", "chat", "think", "patch_call",
    "ask", "compact_boundary", "bad",
})


def parse(o):
    """One decoded rollout object -> a typed record (module header) or None."""
    t = o.get("type")
    p = o.get("payload") or {}
    if t == "event_msg":
        h = _EVENT.get(p.get("type"))
        return _stamp(h(p), o) if h else None
    if t == "response_item":
        h = _RESP.get(p.get("type"))
        # response_item too: exec / exec_result carry the envelope timestamp so a
        # standalone exec block can time itself (_stamp is a no-op for the rest).
        return _stamp(h(p), o) if h else None
    h = _TOP.get(t)
    # A top-level record's fields sit under `payload` in the enveloped
    # spelling and at the top level in the older bare-item one — hand the
    # handler whichever mapping actually holds them.
    return h(o.get("payload") or o) if h else None


def parse_line(s):
    """One rollout JSONL line -> a typed record; {"kind": "bad", "raw": s}
    when the line isn't JSON at all (the stream keeps its own json.loads so
    its malformed-line audit contract stays where it was)."""
    try:
        o = json.loads(s)
    except Exception:
        return {"kind": "bad", "raw": s}
    return parse(o)
