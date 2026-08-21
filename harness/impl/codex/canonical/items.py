# harness/impl/codex/canonical/items.py — the codex rollout's `response_item`
# register, and the custom-tool argument grammar only this register speaks.
#
# The model-API record the conversation is rebuilt from on resume: the complete,
# in-order register, and the ONLY source of a post-abort / queued prompt. Its
# event_msg twin — codex's own digested UI stream, which a mirror paints — lives
# in events.py; the two are deliberately not unified (docs/codex.md *Two
# registers*), so this register's message/think get their own `chat`/`think`
# kinds rather than the mirror's `message`/`reasoning`.
#
# One parser per `payload.type` (RESPONSES) plus one per `function_call` name
# (CALLS); rollout.py dispatches through RESPONSES. An unlisted name or type is
# None, never an exception — the grammar is VERSION-FRAGILE, so a new codex tool
# degrades to "not rendered".
import ast
import json
import re
from typing import Any

from domain.ids import CallId
from harness.impl.codex.canonical.vocabulary import (
    empty_record,
    is_synthetic,
    plan_body,
    strip_input_wrapper,
)

# The exec output's exit-status head line ("Exit code: 2" / "Process exited
# with code 2") — scanned only in the head window: the status line leads the
# output, and a multi-MB output must not be regex-walked whole.
EXIT_RE = re.compile(r"(?:^|\n)(?:Exit code|Process exited with code)[: ]+(\d+)")
EXIT_SCAN_B = 300
CITATION_RE = re.compile(r"cite[^]+\s*")

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
# it): a list joins on spaces, a string is taken verbatim. The `cmd` key comes
# BOTH unquoted (`{cmd:"ls"}` — a JS object literal) AND double-quoted
# (`{"cmd":"pwd"}` — valid JSON), across codex models/versions, so the optional
# quotes around the key are load-bearing: without them the quoted form matched
# nothing and the command silently vanished (verified live — a gpt-5.6-terra run's
# `pwd` showed no block at all).
_JS_CMD = re.compile(
    r"""["']?cmd["']?\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
# The custom-exec output preamble ends in this marker; the block body wants only
# what follows it (the exit is still read from the whole head window).
_OUTPUT_MARK = "Output:\n"


_JS_TOOL = re.compile(r"tools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# The quote characters the argument scan below must not read structure inside.
_JS_QUOTES = "\"'`"
_JS_PLAN_STEP = re.compile(
    r'''["']?step["']?\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)
_JS_PLAN_STATUS = re.compile(
    r'''["']?status["']?\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)


def js_tool_call(js: str) -> tuple[str, str]:
    """(name, args) of the `tools.<fn>(…)` call in a `custom_tool_call` name=exec
    JS input — ("", "") when there is none.

    codex ≥ 0.146 runs MANY tools through the SAME `exec` custom tool: a shell
    command is `tools.exec_command({cmd:…})` (handled by _exec_cmd_from_js
    below), but a web/MCP lookup is
    `const r = await tools.web__run({…}); text(JSON.stringify(r))`. The NAME is
    the function (`web__run`) and the ARGS are what it was called with, so a
    presenter can paint the same quiet `· <name>` block every other tool call in
    this repo gets, with the arguments behind the click.

    The args end at the call's MATCHING close paren, found by a depth count that
    skips quoted text. A fixed suffix list ("; text(r)", …) was the previous
    approach and matched NONE of the five real calls in the measured child
    rollout — the wrapper's tail varies per call (`text(JSON.stringify(r))`,
    `text(r.content.map(x=>x.text||"").join("\\n"))`), so the whole `; text(…)`
    tail was landing in the rendered command. An unbalanced (truncated) input
    falls open to the rest of the string rather than raising."""
    m = _JS_TOOL.search(js or "")
    if not m:
        return "", ""
    i, depth, quote, esc = m.end(), 1, "", False
    while i < len(js) and depth:
        ch = js[i]
        if esc:
            esc = False
        elif quote:
            if ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
        elif ch in _JS_QUOTES:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    args = js[m.end():i - 1] if not depth else js[m.end():]
    return m.group(1), args.strip()


def _plan_tasks(arguments: str | dict[str, Any]) -> list[Any] | None:
    if isinstance(arguments, dict):
        plan = arguments.get("plan")
        return plan if isinstance(plan, list) else None
    try:
        decoded = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, dict):
        decoded_plan = decoded.get("plan")
        if isinstance(decoded_plan, list):
            return decoded_plan
    matches = list(_JS_PLAN_STEP.finditer(arguments or ""))
    if not matches:
        return None
    tasks = []
    for index, step_match in enumerate(matches):
        item_end = matches[index + 1].start() if index + 1 < len(matches) else len(arguments)
        status_match = _JS_PLAN_STATUS.search(arguments, step_match.end(), item_end)
        if status_match is None:
            return None
        try:
            step = ast.literal_eval(step_match.group(1))
            status = ast.literal_eval(status_match.group(1))
        except (SyntaxError, ValueError):
            return None
        tasks.append({"step": step, "status": status})
    return tasks


def _exec_cmd_from_js(js: str) -> str:
    """The SHELL command out of a `custom_tool_call` name=exec JS `input`, or ''
    when the call is not a shell one — `tools.exec_command({cmd:…})` yields its
    cmd, anything else is a different tool and belongs to js_tool_call above (the
    `tool` record), not to a command block."""
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


def _exec_output_body(txt: str) -> str:
    """A custom-exec output stripped of codex's `…Output:\\n` status preamble, so
    the block body is the command's real output (uniform with a Claude command);
    the whole text is still what the exit is scanned from."""
    i = txt.find(_OUTPUT_MARK)
    return txt[i + len(_OUTPUT_MARK):].lstrip("\n") if i >= 0 else txt


def content_text(c: str | list[Any] | None) -> str:
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


def _args(p: dict[str, Any]) -> dict[str, Any]:
    """A function_call's `arguments` (a JSON *string*) -> a dict; {} when the
    version at hand wrote something else or the line was truncated."""
    try:
        a = json.loads(p.get("arguments") or "{}")
    except Exception:
        return {}
    return a if isinstance(a, dict) else {}


def _rsp_web_search_call(p: dict[str, Any]) -> dict[str, Any] | None:
    q = (p.get("action") or {}).get("query") or ""
    return {"kind": "search", "query": q} if q else None


def _rsp_function_call_output(p: dict[str, Any]) -> dict[str, Any] | None:
    # The OLDER exec channel's output. Same normalisation as the custom-tool one:
    # the exit is scanned from the FULL head (the `Chunk ID…\nWall time…\nProcess
    # exited with code N\n…Output:\n` preamble codex 0.14x prints leads it), THEN
    # the body is stripped of that preamble so a standalone block shows the real
    # output, not codex's status noise (verified live: a `run ls` used THIS
    # channel with exactly that preamble).
    out = p.get("output") or ""
    if not isinstance(out, str):
        out = content_text(out)
    if not out:
        return None
    m = EXIT_RE.search(out[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": _exec_output_body(out), "call_id": p.get("call_id") or ""}


def _rsp_message(p: dict[str, Any]) -> dict[str, Any]:
    # The response_item register (module header): the conversation as the
    # model API records it — assistant/user/developer, and the ONLY place a
    # post-abort or queued prompt appears. Deliberately NOT kind "message"/
    # "prompt": those are the event_msg register the mirror paints, and one
    # turn shows up in both.
    txt = content_text(p.get("content"))
    if not txt:
        return empty_record()
    role = (p.get("role") or "").strip()
    # A PLAN before anything else: it is an assistant turn wearing a wrapper tag,
    # so the structural synthetic rule below would drop it as machinery (see
    # vocabulary.PLAN_WRAPPER). Its own kind, not a `chat`, because it is a
    # different KIND of turn — the web renders it as a plan bubble, exactly as a
    # Claude ExitPlanMode plan is (docs/codex.md *Plan mode in the conversation*).
    if role == "assistant":
        plan = plan_body(txt)
        if plan:
            return {"kind": "plan", "role": role, "text": plan}
    # role-aware synthetic on the RAW text (the `<tag>` is the signal), THEN unwrap
    # an INPUT wrapper so a kept `<task>` prompt reads as its inner text.
    synth = is_synthetic(txt, role)
    # …carrying the assistant PHASE too (see events.PHASE_FINAL): this register is
    # the twin of the event_msg one, and the web's conversation read takes
    # whichever arrives first — so the fact that a reply is the turn's FINAL
    # ANSWER has to survive both spellings or it survives neither.
    metadata = p.get("internal_chat_message_metadata_passthrough") or {}
    return {"kind": "chat", "role": role,
            "text": strip_input_wrapper(txt), "synthetic": synth,
            "phase": (p.get("phase") or "").strip(),
            "turn": metadata.get("turn_id") or ""}


def _rsp_reasoning(p: dict[str, Any]) -> dict[str, Any]:
    # summary is a list of {"type": "summary_text", "text": …}; it is empty
    # whenever the think was stored as `encrypted_content` instead.
    txt = content_text(p.get("summary"))
    return {"kind": "think", "text": txt} if txt else empty_record()


def _rsp_custom_tool_call(p: dict[str, Any]) -> dict[str, Any] | None:
    # codex ≥ 0.13x runs BOTH apply_patch and exec through custom tools:
    #   name="exec"       -> an exec record (cmd out of the JS input) — the
    #                        0.14x+ command channel (see _JS_CMD above).
    #   name="apply_patch"-> a lightweight "patch call started" marker; the
    #                        resolved file ops come from the FileChange item
    #                        (events._file_change), counting both would double.
    # Any other custom tool degrades to None (forward-compatible).
    name = p.get("name")
    if name == "exec":
        js = p.get("input") or ""
        cmd = _exec_cmd_from_js(js)
        if cmd:
            return {"kind": "exec", "cmd": cmd, "call_id": p.get("call_id") or ""}
        # …not a shell command: any OTHER `tools.<fn>(…)` through the same exec
        # tool is a TOOL CALL and gets its own record — structured (name + args)
        # rather than laundered into the exec/command shape, which painted a
        # subagent's web lookups as a `▶ cmd` block of raw JS.
        fn, args = js_tool_call(js)
        if fn:
            if fn == "apply_patch":
                return None
            if fn == "write_stdin":
                return _stdin_record(CallId(p.get("call_id") or ""), args)
            if fn == "update_plan":
                plan = _plan_tasks(args)
                if not isinstance(plan, list):
                    return {"kind": "unmapped_tool", "name": "update_plan"}
                return {
                    "kind": "task_list",
                    "tasks": plan,
                    "call_id": p.get("call_id") or "",
                }
            if fn in ("create_goal", "get_goal", "update_goal"):
                return {"kind": "goal_tool", "call_id": p.get("call_id") or ""}
            return {"kind": "tool", "name": fn, "args": args,
                    "call_id": p.get("call_id") or ""}
        return None
    if name == "apply_patch":
        inp = p.get("input")
        return {"kind": "patch_call",
                "patch": inp if isinstance(inp, str) else content_text(inp),
                "call_id": p.get("call_id") or ""}
    return None


def _rsp_custom_tool_call_output(p: dict[str, Any]) -> dict[str, Any] | None:
    # The output carries no tool name, so this is the exec/patch OUTPUT for
    # whatever `custom_tool_call` opened this call_id — an `exec_result` in both
    # cases, paired by call_id in the renderer: an exec's closes its command
    # block, an apply_patch's is an orphan (its file ops come from the FileChange
    # item) that shows only a FAILED exit, never a stray block. Same record shape
    # the function_call_output (older channel) yields, so one renderer path
    # handles both.
    out = p.get("output")
    txt = out if isinstance(out, str) else content_text(out)
    body = CITATION_RE.sub("", _exec_output_body(txt))
    try:
        combined_result = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        combined_result = None
    # An apply_patch-only wrapper returns `{}`; the authoritative FileChange
    # item carries the immutable patch. A combined patch + command wrapper
    # returns both results, so retain only the command result that its matching
    # custom_tool_call opened.
    if combined_result == {}:
        return None
    if isinstance(combined_result, dict) and "patch" in combined_result:
        command_result = combined_result.get("test")
        if not isinstance(command_result, dict):
            return None
        return {
            "kind": "exec_result",
            "exit": command_result.get("exit_code"),
            "output": command_result.get("output") or "",
            "process_id": command_result.get("session_id"),
            "running": command_result.get("session_id") is not None
                       and command_result.get("exit_code") is None,
            "call_id": p.get("call_id") or "",
        }
    if isinstance(combined_result, dict) and any(
        field in combined_result for field in ("output", "session_id", "exit_code")
    ):
        process_id = combined_result.get("session_id")
        exit_code = combined_result.get("exit_code")
        return {
            "kind": "exec_result",
            "exit": exit_code,
            "output": combined_result.get("output") or "",
            "process_id": str(process_id) if process_id is not None else None,
            "running": process_id is not None and exit_code is None,
            "call_id": p.get("call_id") or "",
        }
    m = EXIT_RE.search(txt[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": body, "call_id": p.get("call_id") or ""}


def _call_exec(p: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
    cmd = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    if not cmd:
        return None
    return {"kind": "exec", "cmd": cmd, "call_id": p.get("call_id") or ""}


def _call_stdin(p: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    # The backgrounded-exec continuation poll: codex writes into a running
    # exec session and reads more of its output. Its function_call_output is
    # an ordinary `exec_result` — this record exists so that output is not
    # orphaned (a presenter pairs the two by call_id).
    return _stdin_record(CallId(p.get("call_id") or ""), args)


def _stdin_record(call_id: CallId, arguments: str | dict[str, Any]) -> dict[str, Any]:
    """Normalize only the measured write_stdin argument shape.

    Current custom-tool rollouts contain either JSON or a JavaScript object
    literal with unquoted keys. This parser does not interpret JavaScript; it
    extracts the two fields that define the continuation.
    """
    if isinstance(arguments, dict):
        fields = arguments
    else:
        try:
            fields = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            process_match = re.search(r'(?:^|[,{])\s*["\']?session_id["\']?\s*:\s*(\d+)', arguments or "")
            chars_match = re.search(
                r'(?:^|[,{])\s*["\']?chars["\']?\s*:\s*("(?:[^"\\]|\\.)*")',
                arguments or "",
            )
            if process_match is None or chars_match is None:
                return {"kind": "stdin", "text": "", "call_id": call_id, "process_id": ""}
            fields = {
                "session_id": process_match.group(1),
                "chars": json.loads(chars_match.group(1)),
            }
    process_id = fields.get("session_id") if isinstance(fields, dict) else None
    chars = fields.get("chars") if isinstance(fields, dict) else None
    return {
        "kind": "stdin",
        "text": chars if isinstance(chars, str) else "",
        "call_id": call_id,
        "process_id": str(process_id) if process_id is not None else "",
    }


def _call_ask(p: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
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
    # question card's pending_dialog read — harness/impl/codex/read.py).
    return {"kind": "ask", "call_id": p.get("call_id") or "",
            "questions": out} if out else None


# function_call `name` → its argument grammar. `shell` is the pre-0.1x
# spelling of `exec_command` (same {command: [...]} shape) and still turns up
# in older rollouts; an unlisted name is None, so a new codex tool degrades to
# "not rendered" rather than to an exception.
_CALL = {"exec_command": _call_exec, "shell": _call_exec,
         "write_stdin": _call_stdin, "request_user_input": _call_ask}


def _rsp_function_call(p: dict[str, Any]) -> dict[str, Any] | None:
    h = _CALL.get(p.get("name") or "")
    if h:
        return h(p, _args(p))
    if p.get("name") in {
        "spawn_agent",
        "wait_agent",
        "send_message",
        "followup_task",
        "interrupt_agent",
        "list_agents",
    }:
        return {
            "kind": "collaboration_call",
            "name": p.get("name"),
            "args": _args(p),
            "call_id": p.get("call_id") or "",
        }
    return {"kind": "unmapped_tool", "name": p.get("name") or ""}


RESPONSES = {"web_search_call": _rsp_web_search_call,
             "function_call_output": _rsp_function_call_output,
             "function_call": _rsp_function_call,
             "message": _rsp_message, "reasoning": _rsp_reasoning,
             "custom_tool_call": _rsp_custom_tool_call,
             "custom_tool_call_output": _rsp_custom_tool_call_output}
