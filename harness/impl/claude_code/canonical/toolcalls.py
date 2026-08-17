"""Tool-call lifecycle semantics: operations, file facts, assignments, attention."""

from __future__ import annotations

import json

from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    ActorMessageSent,
    AttentionRequested,
    AttentionResolved,
    CanonicalEvent,
    EventPayload,
    FileAccessed,
    OperationFinished,
    OperationStarted,
)
from domain.ids import ActorId, AssignmentId, AttentionId, MessageId, OperationId
from domain.values import (
    AttentionAnswer,
    AttentionChoice,
    AttentionDecision,
    AttentionPrompt,
    AttentionType,
    ExecutionMode,
    FileAction,
    OperationCategory,
)
from harness.impl.claude_code.canonical.support import content, event
from harness.models import RawEvent, TranslationError

# The tool_result boilerplate Claude Code emits when a Bash command is launched
# in the background. Its operation.finished still converges from the hook
# evidence; only this text is suppressed.
BACKGROUND_LAUNCH_STUB = "Command running in background with ID:"

# The dashboard's own `discuss` gesture (and the TUI's "let me clarify") comes back
# as a REJECTED tool call whose result says so in these words. Anything else that
# rejects a question is a plain decline.
QUESTION_DISCUSSION_MARKER = "wants to clarify these questions"


def tool_category(native_name: str) -> OperationCategory:
    if native_name in ("Bash", "Monitor", "exec_command", "read_command", "py", "mcp__node_repl__js"):
        return "shell"
    if native_name == "Read":
        return "file_read"
    if native_name in ("Write",):
        return "file_write"
    if native_name in ("Edit", "MultiEdit", "NotebookEdit"):
        return "file_edit"
    if native_name in ("Grep", "Glob", "WebSearch", "ToolSearch"):
        return "search"
    if native_name in ("WebFetch",):
        return "network"
    if native_name in ("Task", "Agent", "TaskCreate", "TaskUpdate", "TaskStop", "ListAgents"):
        return "task"
    if native_name in ("EnterWorktree", "ExitWorktree"):
        return "workspace"
    if native_name in ("GenerateImage", "image_gen__imagegen"):
        return "media"
    if native_name in ("SendMessage",):
        return "message"
    if native_name in ("AskUserQuestion", "ExitPlanMode"):
        return "attention"
    if native_name in ("Skill",):
        return "skill"
    raise TranslationError(f"unmapped Claude Code tool: {native_name or '<missing>'}")


def tool_arguments(native_name: str, arguments: dict):
    primary_field = {
        "Bash": "command",
        "Read": "file_path",
        "Write": "file_path",
        "Edit": "file_path",
        "MultiEdit": "file_path",
        "NotebookEdit": "notebook_path",
        "Grep": "pattern",
        "Glob": "pattern",
        "WebSearch": "query",
        "ToolSearch": "query",
        "WebFetch": "url",
        "Skill": "skill",
        "Task": "prompt",
        "Agent": "prompt",
        "SendMessage": "content",
    }.get(native_name)
    if primary_field and arguments.get(primary_field) is not None:
        return content(arguments[primary_field])
    return content(arguments)


def structured_patch(path: str, tool_response: dict) -> tuple[str | None, int | None, int | None]:
    patches = tool_response.get("structuredPatch")
    if not isinstance(patches, list) or not patches:
        return None, None, None
    lines = [f"--- {path}", f"+++ {path}"]
    added = 0
    removed = 0
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        old_start = int(patch.get("oldStart") or 0)
        old_lines = int(patch.get("oldLines") or 0)
        new_start = int(patch.get("newStart") or 0)
        new_lines = int(patch.get("newLines") or 0)
        lines.append(f"@@ -{old_start},{old_lines} +{new_start},{new_lines} @@")
        for line in patch.get("lines") or ():
            text = str(line)
            lines.append(text)
            if text.startswith("+"):
                added += 1
            elif text.startswith("-"):
                removed += 1
    return "\n".join(lines) + "\n", added, removed


def attention_answers(arguments: dict) -> tuple[AttentionAnswer, ...]:
    native_answers = arguments.get("answers")
    if not isinstance(native_answers, dict):
        return ()
    answers = []
    for question_index, question in enumerate(arguments.get("questions") or ()):
        if not isinstance(question, dict):
            continue
        prompt = str(question.get("question") or "")
        native_answer = native_answers.get(prompt)
        if native_answer is None:
            continue
        if isinstance(native_answer, list):
            values = tuple(str(value) for value in native_answer)
        elif question.get("multiSelect"):
            values = tuple(part.strip() for part in str(native_answer).split(", ") if part.strip())
        else:
            values = (str(native_answer),)
        answers.append(
            AttentionAnswer(
                prompt_id=str(question.get("id") or question_index),
                values=values,
            )
        )
    return tuple(answers)


def plan_resolution(native: dict, failed: bool) -> tuple[AttentionDecision, str | None, bool]:
    response = native.get("tool_response") or native.get("tool_result")
    if not failed:
        edited = bool(isinstance(response, dict) and response.get("planWasEdited"))
        return "approved", None, edited
    text = response if isinstance(response, str) else json.dumps(response or {}, ensure_ascii=False)
    marker = "the user said:"
    marker_position = text.find(marker)
    if marker_position >= 0:
        return "changes_requested", text[marker_position + len(marker):].strip(), False
    return "rejected", None, False


def question_resolution(response: object, failed: bool) -> AttentionDecision:
    if not failed:
        return "answered"
    text = response if isinstance(response, str) else json.dumps(response or {}, ensure_ascii=False)
    return "discussed" if QUESTION_DISCUSSION_MARKER in text else "rejected"


class ToolCallSemantics:
    """Cross-event state for one translator's lifetime: which tool_use_ids were
    task tools (silent) or attention tools (need a resolution even when no
    PostToolUse ever fires for them)."""

    TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate", "TaskGet", "TaskList"})
    ATTENTION_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})

    def __init__(self) -> None:
        self.task_tool_ids: set[str] = set()
        # Which attention tool a tool_use_id was, remembered from the request. A
        # transcript tool_result names no tool, and a REJECTED attention tool is
        # only ever seen there — Claude Code fires no PostToolUse for a call that
        # never ran, so the request would otherwise stay open forever.
        self.attention_tool_ids: dict[str, str] = {}

    def tool_started(self, raw_event: RawEvent, native: dict) -> list[CanonicalEvent]:
        operation_id = OperationId(str(native.get("tool_use_id") or native.get("id") or raw_event.source_position))
        native_name = native.get("tool_name") or native.get("name") or "tool"
        if native_name in self.TASK_TOOLS:
            self.task_tool_ids.add(str(operation_id))
            return []
        arguments = native.get("tool_input") if "tool_input" in native else native.get("input")
        arguments = arguments if isinstance(arguments, dict) else {}
        if native_name == "Monitor":
            execution: ExecutionMode = "monitor"
        elif native_name == "Bash" and arguments.get("run_in_background"):
            execution = "background"
        else:
            execution = "foreground"
        started = OperationStarted(
            operation_id,
            tool_category(native_name),
            native_name,
            execution,
            tool_arguments(native_name, arguments),
            arguments.get("description") or None,
            None,
        )
        events = [event(raw_event, "operation", str(operation_id), "started", started)]
        events.extend(self._tool_side_facts(raw_event, operation_id, native_name, arguments))
        return events

    def tool_finished(self, raw_event: RawEvent, native: dict, failed: bool) -> list[CanonicalEvent]:
        operation_id = OperationId(str(native.get("tool_use_id") or native.get("id") or raw_event.source_position))
        native_name = native.get("tool_name") or "tool"
        if native_name in self.TASK_TOOLS:
            self.task_tool_ids.add(str(operation_id))
            return []
        arguments = native.get("tool_input") or {}
        finished = OperationFinished(operation_id, "failed" if failed else "succeeded", None, None)
        events = [event(raw_event, "operation", str(operation_id), "finished", finished)]
        tool_response = native.get("tool_response") or {}
        async_launched = (
            isinstance(tool_response, dict)
            and (
                tool_response.get("isAsync") is True
                or tool_response.get("status") == "async_launched"
            )
        )
        if native_name in ("Task", "Agent") and not async_launched:
            assignment_id = AssignmentId(str(operation_id))
            payload: EventPayload = ActorAssignmentFinished(
                assignment_id,
                "failed" if failed else "succeeded",
                None,
                None,
            )
            events.append(
                event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "finished",
                    payload,
                )
            )
        if native_name in self.ATTENTION_TOOLS:
            attention_id = AttentionId(str(operation_id))
            if native_name == "AskUserQuestion":
                decision = question_resolution(tool_response, failed)
                answers = attention_answers(arguments)
                feedback = None
                edited = False
            else:
                decision, feedback, edited = plan_resolution(native, failed)
                answers = ()
            payload = AttentionResolved(
                attention_id,
                decision,
                answers,
                feedback,
                edited,
                "failed" if failed else "succeeded",
            )
            events.append(event(raw_event, "attention", str(attention_id), "resolved", payload))
        events.extend(
            self.file_facts(raw_event, operation_id, native_name, arguments, tool_response)
        )
        return events

    def attention_declined(
        self,
        raw_event: RawEvent,
        operation_id: OperationId,
        result_text: str,
    ) -> CanonicalEvent:
        """The resolution of an attention the user REFUSED. A refused tool call never
        runs, so Claude Code fires no PostToolUse and `tool_finished` — the only other
        emitter — never sees it; the transcript's tool_result is the sole evidence the
        request ended. It names no tool, hence `attention_tool_ids`. Refusal carries
        no answers, so nothing is lost if the hook path also reports the same fact:
        both derive the decision from the same text and converge on one event."""
        attention_id = AttentionId(str(operation_id))
        if self.attention_tool_ids[str(operation_id)] == "AskUserQuestion":
            decision, feedback, edited = question_resolution(result_text, True), None, False
        else:
            decision, feedback, edited = plan_resolution({"tool_response": result_text}, True)
        payload = AttentionResolved(attention_id, decision, (), feedback, edited, "failed")
        return event(raw_event, "attention", str(attention_id), "resolved", payload)

    def _tool_side_facts(
        self,
        raw_event: RawEvent,
        operation_id: OperationId,
        native_name: str,
        arguments: dict,
    ) -> list[CanonicalEvent]:
        events = self.file_facts(raw_event, operation_id, native_name, arguments, None)
        if native_name in ("Task", "Agent"):
            assignment_id = AssignmentId(str(operation_id))
            actor_name = arguments.get("name") or arguments.get("subagent_type")
            prompt = arguments.get("prompt")
            payload: EventPayload = ActorAssignmentStarted(
                assignment_id,
                content(arguments.get("description") or prompt or ""),
                actor_name=str(actor_name) if actor_name else None,
                prompt=content(prompt, markdown=True) if prompt else None,
            )
            events.append(
                event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "started",
                    payload,
                )
            )
        if native_name == "SendMessage":
            recipient = ActorId(str(arguments.get("recipient") or arguments.get("to") or "peer"))
            message_id = MessageId(str(operation_id))
            message_content = content(arguments.get("content") or arguments.get("message"))
            payload = ActorMessageSent(message_id, recipient, message_content)
            events.append(event(raw_event, "actor_message", str(message_id), "sent", payload))
        if native_name in self.ATTENTION_TOOLS:
            self.attention_tool_ids[str(operation_id)] = native_name
            attention_id = AttentionId(str(operation_id))
            prompts = self.attention_prompts(native_name, arguments)
            attention_type: AttentionType = "question" if native_name == "AskUserQuestion" else "plan"
            payload = AttentionRequested(attention_id, attention_type, prompts, operation_id)
            events.append(event(raw_event, "attention", str(attention_id), "requested", payload))
        return events

    def file_facts(
        self,
        raw_event: RawEvent,
        operation_id: OperationId,
        native_name: str,
        arguments: dict,
        tool_response: dict | None,
    ) -> list[CanonicalEvent]:
        action_by_tool: dict[str, FileAction] = {
            "Read": "read",
            "Write": "created",
            "Edit": "updated",
            "MultiEdit": "updated",
            "NotebookEdit": "updated",
        }
        action = action_by_tool.get(native_name)
        if action is None:
            return []
        path = arguments.get("file_path") or arguments.get("notebook_path") or ""
        if not path:
            return []
        response = tool_response if isinstance(tool_response, dict) else {}
        content_value = response.get("content", arguments.get("content"))
        file_content = content(content_value) if native_name == "Write" else None
        unified_diff, lines_added, lines_removed = structured_patch(path, response)
        payload = FileAccessed(
            operation_id,
            path,
            action,
            lines_added=lines_added,
            lines_removed=lines_removed,
            unified_diff=unified_diff,
            content=file_content,
        )
        file_identity = f"{operation_id}:{action}:{path}"
        phase = "finished" if tool_response is not None else "started"
        return [event(raw_event, "file", file_identity, phase, payload)]

    @staticmethod
    def attention_prompts(native_name: str, arguments: dict) -> tuple[AttentionPrompt, ...]:
        if native_name == "ExitPlanMode":
            return (AttentionPrompt("plan", "Plan", arguments.get("plan") or "", False, ()),)
        prompts = []
        for index, question in enumerate(arguments.get("questions") or ()):
            choices = tuple(
                AttentionChoice(
                    option.get("label") or "",
                    option.get("label") or "",
                    option.get("description") or None,
                )
                for option in question.get("options") or ()
                if isinstance(option, dict)
            )
            prompts.append(
                AttentionPrompt(
                    prompt_id=str(question.get("id") or index),
                    title=question.get("header") or None,
                    prompt=question.get("question") or "",
                    multiple=bool(question.get("multiSelect")),
                    choices=choices,
                )
            )
        return tuple(prompts)
