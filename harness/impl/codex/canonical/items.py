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
# One parser per `payload.type` (RESPONSES) plus one per `function_call` name;
# rollout.py dispatches through RESPONSES. An unlisted TYPE is None, never an
# exception — the grammar is VERSION-FRAGILE, so a new codex tool degrades to
# "not rendered". A LISTED type whose payload does not match records.py's
# declared shape (`extra="forbid"`) raises `pydantic.ValidationError`, which
# becomes the `translation_failed` verdict (the owner's decision, TASKS.md
# 2026-08-21) — see events.py's header for the same split spelled out in full.
import ast
import json
import re

from pydantic import JsonValue, ValidationError

from domain.ids import CallId, ShellNativeId
from harness.impl.codex.canonical.records import (
    AskArguments,
    COLLABORATION_ARGUMENTS,
    CombinedToolResult,
    ContentPart,
    CustomToolCallOutputPayload,
    CustomToolCallPayload,
    ExecArguments,
    FunctionCallOutputPayload,
    FunctionCallPayload,
    MessagePayload,
    PlanArguments,
    ReasoningPayload,
    StdinArguments,
    WebSearchCallPayload,
)
from harness.impl.codex.canonical.records import (
    AskOptionRecord,
    AskQuestionRecord,
    AskRecord,
    ChatRecord,
    CollaborationCallRecord,
    ExecRecord,
    ExecResultRecord,
    GoalToolRecord,
    PatchCallRecord,
    PlanRecord,
    PlanTask,
    RolloutRecord,
    SearchRecord,
    StdinRecord,
    TaskListRecord,
    ThinkRecord,
    ToolRecord,
    UnmappedToolRecord,
)
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


def _plan_tasks(arguments: str) -> tuple[PlanTask, ...] | None:
    """A `update_plan` JS call's steps, JSON or JS-literal — see PlanArguments
    (records.py): the args are usually JSON even inside the JS snippet, but a
    JS object literal with unquoted keys falls back to a targeted scan, the
    same duality _stdin_record below reads for `write_stdin`."""
    try:
        return tuple(PlanArguments.model_validate_json(arguments).plan or ())
    except ValidationError:
        pass
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
        tasks.append(PlanTask(step=step, status=status))
    return tuple(tasks)


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


def content_text(c: str | list[ContentPart | str] | None) -> str:
    """A response_item content list -> its text. The items are usually
    {"type": "input_text"|"output_text", "text": …}; older versions (and the
    custom-tool outputs) sometimes hand a bare string instead, either for the
    whole field (caught above) or for one entry inside an otherwise-typed
    list (caught below)."""
    if isinstance(c, str):
        return c.strip()
    parts: list[str] = []
    for part in (c or ()):
        if isinstance(part, str):
            parts.append(part)
        elif part.text is not None:
            parts.append(part.text)
    return "\n".join(parts).strip()


def _args(arguments: str | None) -> dict[str, JsonValue]:
    """A function_call's `arguments` (a JSON *string*) -> a dict; {} when the
    version at hand wrote something else or the line was truncated. The dict
    is untyped on purpose: which declared Arguments shape applies depends on
    the call's NAME, decided by the caller before validating it."""
    try:
        a = json.loads(arguments or "{}")
    except Exception:
        return {}
    return a if isinstance(a, dict) else {}


def _rsp_web_search_call(raw: dict[str, JsonValue]) -> SearchRecord | None:
    p = WebSearchCallPayload.model_validate(raw)
    q = (p.action.query if p.action else None) or ""
    return SearchRecord(query=q) if q else None


def _rsp_function_call_output(raw: dict[str, JsonValue]) -> ExecResultRecord | None:
    p = FunctionCallOutputPayload.model_validate(raw)
    out = content_text(p.output) if not isinstance(p.output, str) else p.output
    if not out:
        return None
    m = EXIT_RE.search(out[:EXIT_SCAN_B])
    return ExecResultRecord(exit=m.group(1) if m else None,
                             output=_exec_output_body(out), call_id=CallId(p.call_id or ""))


def _rsp_message(raw: dict[str, JsonValue]) -> RolloutRecord:
    # The response_item register (module header): the conversation as the
    # model API records it — assistant/user/developer, and the ONLY place a
    # post-abort or queued prompt appears. Deliberately NOT kind "message"/
    # "prompt": those are the event_msg register the mirror paints, and one
    # turn shows up in both.
    p = MessagePayload.model_validate(raw)
    txt = content_text(p.content)
    if not txt:
        return empty_record()
    role = (p.role or "").strip()
    # A PLAN before anything else: it is an assistant turn wearing a wrapper tag,
    # so the structural synthetic rule below would drop it as machinery (see
    # vocabulary.PLAN_WRAPPER). Its own kind, not a `chat`, because it is a
    # different KIND of turn — the web renders it as a plan bubble, exactly as a
    # Claude ExitPlanMode plan is.
    if role == "assistant":
        plan = plan_body(txt)
        if plan:
            return PlanRecord(text=plan, id="")
    # role-aware synthetic on the RAW text (the `<tag>` is the signal), THEN unwrap
    # an INPUT wrapper so a kept `<task>` prompt reads as its inner text.
    synth = is_synthetic(txt, role)
    # …carrying the assistant PHASE too (see events.PHASE_FINAL): this register is
    # the twin of the event_msg one, and the web's conversation read takes
    # whichever arrives first — so the fact that a reply is the turn's FINAL
    # ANSWER has to survive both spellings or it survives neither.
    metadata = p.internal_chat_message_metadata_passthrough
    return ChatRecord(role=role, text=strip_input_wrapper(txt), synthetic=synth,
                       phase=(p.phase or "").strip(),
                       turn=(metadata.turn_id if metadata else None) or "")


def _rsp_reasoning(raw: dict[str, JsonValue]) -> RolloutRecord:
    # summary is a list of {"type": "summary_text", "text": …}; it is empty
    # whenever the think was stored as `encrypted_content` instead.
    p = ReasoningPayload.model_validate(raw)
    txt = content_text(p.summary)
    return ThinkRecord(text=txt) if txt else empty_record()


def _rsp_custom_tool_call(raw: dict[str, JsonValue]) -> RolloutRecord | None:
    p = CustomToolCallPayload.model_validate(raw)
    call_id = CallId(p.call_id or "")
    if p.name == "exec":
        js = content_text(p.input) if not isinstance(p.input, str) else p.input
        cmd = _exec_cmd_from_js(js)
        if cmd:
            return ExecRecord(cmd=cmd, call_id=call_id)
        # …not a shell command: any OTHER `tools.<fn>(…)` through the same exec
        # tool is a TOOL CALL and gets its own record — structured (name + args)
        # rather than laundered into the exec/command shape, which painted a
        # subagent's web lookups as a `▶ cmd` block of raw JS.
        fn, args = js_tool_call(js)
        if not fn:
            return None
        if fn == "apply_patch":
            return None
        if fn == "write_stdin":
            return _stdin_record(CallId(call_id), args)
        if fn == "update_plan":
            tasks = _plan_tasks(args)
            if tasks is None:
                return UnmappedToolRecord(name="update_plan")
            return TaskListRecord(tasks=tasks, call_id=call_id)
        if fn in ("create_goal", "get_goal", "update_goal"):
            return GoalToolRecord(call_id=call_id)
        return ToolRecord(name=fn, args=args, call_id=call_id)
    if p.name == "apply_patch":
        patch = content_text(p.input) if not isinstance(p.input, str) else p.input
        return PatchCallRecord(patch=patch, call_id=call_id)
    return None


def _rsp_custom_tool_call_output(raw: dict[str, JsonValue]) -> RolloutRecord | None:
    p = CustomToolCallOutputPayload.model_validate(raw)
    txt = content_text(p.output) if not isinstance(p.output, str) else p.output
    body = CITATION_RE.sub("", _exec_output_body(txt))
    try:
        combined = CombinedToolResult.model_validate_json(body)
    except ValidationError:
        combined = None
    # An apply_patch-only wrapper returns `{}`; the authoritative FileChange
    # item carries the immutable patch. A combined patch + command wrapper
    # returns both results, so retain only the command result that its matching
    # custom_tool_call opened.
    if combined is not None:
        if combined.patch is not None:
            command_result = combined.test
            if command_result is None:
                return None
            session_id = command_result.session_id
            return ExecResultRecord(
                exit=command_result.exit_code,
                output=command_result.output or "",
                process_id=ShellNativeId(str(session_id)) if session_id is not None else None,
                running=session_id is not None and command_result.exit_code is None,
                call_id=CallId(p.call_id or ""),
            )
        if combined.output is not None or combined.session_id is not None or combined.exit_code is not None:
            process_id = combined.session_id
            return ExecResultRecord(
                exit=combined.exit_code,
                output=combined.output or "",
                process_id=ShellNativeId(str(process_id)) if process_id is not None else None,
                running=process_id is not None and combined.exit_code is None,
                call_id=CallId(p.call_id or ""),
            )
        return None
    m = EXIT_RE.search(txt[:EXIT_SCAN_B])
    return ExecResultRecord(exit=m.group(1) if m else None, output=body, call_id=CallId(p.call_id or ""))


def _call_exec(function_call_payload: FunctionCallPayload, exec_arguments: ExecArguments) -> ExecRecord | None:
    cmd = exec_arguments.cmd or exec_arguments.command or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    if not cmd:
        return None
    return ExecRecord(cmd=cmd, call_id=CallId(function_call_payload.call_id or ""))


def _stdin_record(call_id: CallId, arguments: StdinArguments | str) -> StdinRecord:
    """Normalize only the measured write_stdin argument shape.

    Current custom-tool rollouts contain either JSON (validated strictly by
    StdinArguments) or a JavaScript object literal with unquoted keys, which
    this parser does not interpret; it extracts the two fields that define the
    continuation with a targeted regex instead.
    """
    fields: StdinArguments | None
    if isinstance(arguments, StdinArguments):
        fields = arguments
    else:
        try:
            fields = StdinArguments.model_validate_json(arguments)
        except ValidationError:
            fields = None
        if fields is None:
            process_match = re.search(r'(?:^|[,{])\s*["\']?session_id["\']?\s*:\s*(\d+)', arguments)
            chars_match = re.search(
                r'(?:^|[,{])\s*["\']?chars["\']?\s*:\s*("(?:[^"\\]|\\.)*")',
                arguments,
            )
            if process_match is None or chars_match is None:
                return StdinRecord(text="", call_id=call_id, process_id=ShellNativeId(""))
            fields = StdinArguments(
                session_id=ShellNativeId(process_match.group(1)),
                chars=json.loads(chars_match.group(1)),
            )
    process_id = fields.session_id
    return StdinRecord(
        text=fields.chars or "",
        call_id=call_id,
        process_id=ShellNativeId(str(process_id)) if process_id is not None else ShellNativeId(""),
    )


def _call_stdin(function_call_payload: FunctionCallPayload, stdin_arguments: StdinArguments) -> StdinRecord:
    return _stdin_record(CallId(function_call_payload.call_id or ""), stdin_arguments)


def _call_ask(function_call_payload: FunctionCallPayload, ask_arguments: AskArguments) -> AskRecord | None:
    questions = tuple(
        AskQuestionRecord(
            id=question.id or "", header=question.header or "", question=question.question or "",
            options=tuple(
                AskOptionRecord(label=option.label or "", description=option.description or "")
                for option in (question.options or ())
            ),
        )
        for question in (ask_arguments.questions or ())
    )
    # call_id rides along so a presenter can pair the ask with its
    # function_call_output ANSWER without re-reading the raw payload.
    call_id = CallId(function_call_payload.call_id or "")
    return AskRecord(call_id=call_id, questions=questions) if questions else None


def _rsp_function_call(raw: dict[str, JsonValue]) -> RolloutRecord | None:
    p = FunctionCallPayload.model_validate(raw)
    name = p.name or ""
    args_raw = _args(p.arguments)
    # `shell` is the pre-0.1x spelling of `exec_command` (same {command: [...]}
    # shape) and still turns up in older rollouts.
    if name in ("exec_command", "shell"):
        return _call_exec(p, ExecArguments.model_validate(args_raw))
    if name == "write_stdin":
        return _call_stdin(p, StdinArguments.model_validate(args_raw))
    if name == "request_user_input":
        return _call_ask(p, AskArguments.model_validate(args_raw))
    collaboration_arguments = COLLABORATION_ARGUMENTS.get(name)
    if collaboration_arguments is not None:
        return CollaborationCallRecord(
            name=name, args=collaboration_arguments.model_validate(args_raw),
            call_id=CallId(p.call_id or ""),
        )
    # An unlisted name is None, so a new codex tool degrades to "not rendered"
    # rather than to an exception.
    return UnmappedToolRecord(name=name)


RESPONSES = {"web_search_call": _rsp_web_search_call,
             "function_call_output": _rsp_function_call_output,
             "function_call": _rsp_function_call,
             "message": _rsp_message, "reasoning": _rsp_reasoning,
             "custom_tool_call": _rsp_custom_tool_call,
             "custom_tool_call_output": _rsp_custom_tool_call_output}
