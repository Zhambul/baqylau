"""Tool-call semantics: which fact a tool call IS, and when it is known.

One table, `TOOL_KINDS`, decides what a tool means; there is no generic
operation verb any more. A shell call has a life (started, output, finished); a
file, search, fetch or worktree call has only a RESULT — the path or query and
what came back of it are one fact, and neither half is worth recording without
the other. So the result-time kinds emit nothing at start, and the call's
arguments are remembered until the result arrives.
"""

from __future__ import annotations

import json
from typing import Literal, TypeAlias

from pydantic import JsonValue

from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CanonicalEvent,
    EventPayload,
    FileAccessed,
    MessageCreated,
    PlanProposed,
    PlanResolved,
    QuestionAnswered,
    QuestionAsked,
    SearchPerformed,
    ShellBackgrounded,
    ShellFinished,
    ShellProgressed,
    ShellStarted,
    SkillFinished,
    SkillStarted,
    WebFetched,
    WorktreeChanged,
)
from domain.ids import (
    ActorId,
    AssignmentId,
    AttentionId,
    CallId,
    MessageId,
    QuestionId,
    ShellId,
    ShellNativeId,
    SkillId,
)
from domain.values import (
    AttentionAnswer,
    AttentionChoice,
    AttentionPrompt,
    Content,
    ExecutionMode,
    FileAction,
    Outcome,
    PlanState,
    WorktreeAction,
)
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.support import content, event
from harness.models import RawEvent, UnknownRawEvent

# The tool_result boilerplate Claude Code emits when a Bash command is launched
# in the background. Its shell.finished still converges from the hook's raw event;
# only this text is suppressed.
BACKGROUND_LAUNCH_STUB = "Command running in background with ID:"

ToolKind: TypeAlias = Literal[
    "shell",
    "file",
    "search",
    "web",
    "worktree",
    "skill",
    "assignment",
    "message",
    "question",
    "plan",
    "ignored",
]

# What each tool IS. The `ignored` entries are named rather than left to fall
# through: a task tool's fact arrives as `task.changed` from the task source, a
# generated image exposes no readable path to put on a file fact, and the two
# agent-plumbing calls carry nothing anybody reads. An unlisted name is drift.
TOOL_KINDS: dict[str, ToolKind] = {
    "Bash": "shell",
    "Monitor": "shell",
    "exec_command": "shell",
    "read_command": "shell",
    "py": "shell",
    "mcp__node_repl__js": "shell",
    "Read": "file",
    "Write": "file",
    "Edit": "file",
    "MultiEdit": "file",
    "NotebookEdit": "file",
    "Grep": "search",
    "Glob": "search",
    "WebSearch": "search",
    "ToolSearch": "search",
    "WebFetch": "web",
    "EnterWorktree": "worktree",
    "ExitWorktree": "worktree",
    "Skill": "skill",
    "Task": "assignment",
    "Agent": "assignment",
    "SendMessage": "message",
    "AskUserQuestion": "question",
    "ExitPlanMode": "plan",
    "TaskCreate": "ignored",
    "TaskUpdate": "ignored",
    "TaskGet": "ignored",
    "TaskList": "ignored",
    "TaskStop": "ignored",
    "ListAgents": "ignored",
    "GenerateImage": "ignored",
    "image_gen__imagegen": "ignored",
}

# Which field of a search tool's input holds what was searched for.
SEARCH_QUERY_FIELDS = ("pattern", "query")

# Kinds whose whole fact a transcript tool_result can complete on its own. An
# assignment's end and an attention's resolution are deliberately absent: the
# async-launch marker and the plan's decision text live in the native response
# document, which only the hook delivery carries, so those two facts stay the
# hook's — exactly the division of labour the generic operation finish had.
TRANSCRIPT_RESULT_KINDS: frozenset[ToolKind] = frozenset(
    {"shell", "file", "search", "web", "worktree", "skill"}
)

FILE_ACTIONS: dict[str, FileAction] = {
    "Read": "read",
    "Write": "created",
    "Edit": "updated",
    "MultiEdit": "updated",
    "NotebookEdit": "updated",
}


def tool_kind(native_name: str) -> ToolKind:
    kind = TOOL_KINDS.get(native_name)
    if kind is None:
        raise UnknownRawEvent(f"unmapped Claude Code tool: {native_name or '<missing>'}")
    return kind


def structured_patch(
    path: str,
    tool_response: records.ToolResponse,
) -> tuple[str | None, int | None, int | None]:
    patches = tool_response.structuredPatch
    if not patches:
        return None, None, None
    lines = [f"--- {path}", f"+++ {path}"]
    added = 0
    removed = 0
    for patch in patches:
        old_start = patch.oldStart or 0
        old_lines = patch.oldLines or 0
        new_start = patch.newStart or 0
        new_lines = patch.newLines or 0
        lines.append(f"@@ -{old_start},{old_lines} +{new_start},{new_lines} @@")
        for line in patch.lines or ():
            text = str(line)
            lines.append(text)
            if text.startswith("+"):
                added += 1
            elif text.startswith("-"):
                removed += 1
    return "\n".join(lines) + "\n", added, removed


def result_content(tool_response: JsonValue) -> Content | None:
    """What a call answered, whatever shape the raw event held it in.

    A hook reports the native response document, the transcript reports the
    text of the same answer; both are readable and both converge on one fact,
    so neither is preferred over the other here.
    """
    if not tool_response:
        return None
    return content(tool_response)


def attention_answers(
    arguments: records.QuestionArguments,
) -> tuple[AttentionAnswer, ...]:
    native_answers = arguments.answers
    if not isinstance(native_answers, dict):
        return ()
    answers = []
    for question_index, question in enumerate(arguments.questions or ()):
        prompt = str(question.question or "")
        native_answer = native_answers.get(prompt)
        if native_answer is None:
            continue
        if isinstance(native_answer, list):
            labels = tuple(str(value) for value in native_answer)
        elif question.multiSelect:
            labels = tuple(part.strip() for part in str(native_answer).split(", ") if part.strip())
        else:
            labels = (str(native_answer),)
        answers.append(
            AttentionAnswer(
                prompt_id=QuestionId(str(question.id if question.id is not None else question_index)),
                labels=labels,
            )
        )
    return tuple(answers)


def plan_resolution(
    tool_response: records.ToolResponse | str | None,
    failed: bool,
) -> tuple[PlanState, str | None, bool]:
    if not failed:
        edited = bool(
            isinstance(tool_response, records.ToolResponse) and tool_response.planWasEdited
        )
        return "approved", None, edited
    if isinstance(tool_response, str):
        text = tool_response
    elif tool_response is None:
        text = "{}"
    else:
        text = json.dumps(tool_response.model_dump(exclude_none=True), ensure_ascii=False)
    marker = "the user said:"
    marker_position = text.find(marker)
    if marker_position >= 0:
        return "changes_requested", text[marker_position + len(marker):].strip(), False
    return "rejected", None, False


class ToolCallSemantics:
    """Cross-event state for one translator's lifetime.

    `calls` is the whole of it: a tool_use_id's name and input, remembered from
    the request. Three things need it. A transcript tool_result names no tool
    and carries no input, so the result-time facts (file, search, fetch,
    worktree) could not be built from it alone. A REFUSED attention tool is
    only ever seen there — Claude Code fires no PostToolUse for a call that
    never ran, so the request would otherwise stay open forever. And a task
    tool has to stay silent on both paths, not just the one that named it.
    """

    def __init__(self) -> None:
        self.calls: dict[CallId, tuple[str, dict[str, JsonValue]]] = {}
        # An armed Monitor's TASK id -> the shell that armed it, and how many
        # of its events have been attributed so far. A monitor's per-event
        # notification names only the task id — never the tool_use_id (measured
        # in claude-code 2.1.233) — so this is the only route from an event back
        # to the command the monitors tab lists. Its stream-ENDED notification
        # does carry the tool_use_id, so the end needs no memory and survives a
        # daemon restart that loses this.
        self.monitor_tasks: dict[ShellNativeId, str] = {}
        self.monitor_event_counts: dict[ShellNativeId, int] = {}

    # --- what a call was, across the two raw event streams ------------------

    def remember(
        self,
        call_id: CallId,
        native_name: str,
        arguments: dict[str, JsonValue],
    ) -> None:
        self.calls[call_id] = (native_name, arguments)

    def recall(
        self,
        call_id: CallId,
        native_name: str | None,
        arguments: dict[str, JsonValue] | None,
    ) -> tuple[str, dict[str, JsonValue]]:
        """The call's name and input: what this record carries, else what the
        request said. A record that has neither is a call whose start we never
        saw — a daemon that restarted mid-call — and it cannot be classified."""
        remembered_name, remembered_arguments = self.calls.get(call_id, ("", {}))
        name = native_name or remembered_name
        if not name:
            raise UnknownRawEvent(f"Claude Code tool result names no call: {call_id or '<missing>'}")
        return name, arguments if arguments else remembered_arguments

    def known(self, call_id: CallId) -> bool:
        return call_id in self.calls

    def monitor_armed(self, task_id: ShellNativeId, shell_id: ShellId) -> None:
        self.monitor_tasks[task_id] = str(shell_id)

    def monitor_shell(self, task_id: ShellNativeId) -> ShellId | None:
        remembered = self.monitor_tasks.get(task_id)
        return ShellId(remembered) if remembered else None

    def next_monitor_ordinal(self, task_id: ShellNativeId) -> int:
        """The position of the next event of this monitor, counted from zero.

        Part of the event's identity, not decoration: `stable_event_id` is built
        from the subject and the phase, so two events of one monitor recorded
        under the same phase would collapse into one row (measured — six ticks
        became one canonical event that way)."""
        ordinal = self.monitor_event_counts.get(task_id, 0)
        self.monitor_event_counts[task_id] = ordinal + 1
        return ordinal

    # --- the request ---------------------------------------------------------

    def tool_started(
        self,
        raw_event: RawEvent,
        native: dict[str, JsonValue],
    ) -> list[CanonicalEvent[EventPayload]]:
        call = records.ToolCallNative.model_validate(native)
        call_id = CallId(str(call.tool_use_id or call.id or raw_event.source_position))
        native_name = str(call.tool_name or call.name or "tool")
        kind = tool_kind(native_name)
        arguments = call.tool_input if call.tool_input is not None else call.input
        arguments = arguments if arguments is not None else {}
        self.remember(call_id, native_name, arguments)
        if kind == "shell":
            return [self._shell_started(raw_event, call_id, native_name, arguments)]
        if kind == "skill":
            return [self._skill_started(raw_event, call_id, arguments)]
        if kind == "assignment":
            return [self._assignment_started(raw_event, call_id, arguments)]
        if kind == "message":
            return [self._actor_message(raw_event, call_id, arguments)]
        if kind == "question":
            attention_id = AttentionId(call_id)
            payload: EventPayload = QuestionAsked(attention_id, self.questions(arguments))
            return [event(raw_event, "question", str(attention_id), "asked", payload)]
        if kind == "plan":
            attention_id = AttentionId(call_id)
            plan_arguments = records.PlanArguments.model_validate(arguments)
            payload = PlanProposed(attention_id, content(plan_arguments.plan or "", markdown=True))
            return [event(raw_event, "plan", str(attention_id), "proposed", payload)]
        # file, search, web, worktree: nothing is known yet that is worth a
        # fact. `ignored`: nothing ever will be.
        return []

    def _shell_started(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        native_name: str,
        arguments: dict[str, JsonValue],
    ) -> CanonicalEvent[EventPayload]:
        shell = records.ShellArguments.model_validate(arguments)
        shell_id = ShellId(call_id)
        if native_name == "Monitor":
            execution: ExecutionMode = "monitor"
        elif native_name == "Bash" and shell.run_in_background:
            execution = "background"
        else:
            execution = "foreground"
        command = shell.command
        payload = ShellStarted(
            shell_id,
            content(command) if isinstance(command, str) and command else content(arguments),
            execution,
            shell.description or None,
        )
        return event(raw_event, "shell", str(shell_id), "started", payload)

    def _skill_started(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        arguments: dict[str, JsonValue],
    ) -> CanonicalEvent[EventPayload]:
        skill_id = SkillId(call_id)
        skill = records.SkillArguments.model_validate(arguments)
        name = str(skill.skill or "")
        # The input a Skill call carries is the skill name and, at most, an
        # `args` string; when that is all there is, there are no arguments to
        # show and saying so beats echoing the name twice.
        extra = {key: value for key, value in arguments.items() if key != "skill"}
        payload = SkillStarted(skill_id, name, content(extra) if extra else None)
        return event(raw_event, "skill", str(skill_id), "started", payload)

    def _assignment_started(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        arguments: dict[str, JsonValue],
    ) -> CanonicalEvent[EventPayload]:
        assignment_id = AssignmentId(call_id)
        assignment = records.AssignmentArguments.model_validate(arguments)
        actor_name = assignment.name or assignment.subagent_type
        prompt = assignment.prompt
        payload = ActorAssignmentStarted(
            assignment_id,
            content(assignment.description or prompt or ""),
            actor_name=str(actor_name) if actor_name else None,
            prompt=content(prompt, markdown=True) if prompt else None,
        )
        return event(raw_event, "actor_assignment", str(assignment_id), "started", payload)

    def _actor_message(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        arguments: dict[str, JsonValue],
    ) -> CanonicalEvent[EventPayload]:
        """A SendMessage: the actor speaking to a named peer, which is a message
        with a recipient — not a tool call with a text argument."""
        message = records.SendMessageArguments.model_validate(arguments)
        recipient = ActorId(str(message.recipient or message.to or "peer"))
        message_id = MessageId(call_id)
        payload = MessageCreated(
            message_id,
            "assistant",
            content(message.content or message.message, markdown=True),
            "intermediate",
            None,
            recipient,
        )
        return event(raw_event, "message", str(message_id), "created", payload)

    # --- the result ----------------------------------------------------------

    def tool_finished(
        self,
        raw_event: RawEvent,
        native: dict[str, JsonValue],
        failed: bool,
        *,
        result: Content | None = None,
    ) -> list[CanonicalEvent[EventPayload]]:
        """Everything one tool call's RESULT says.

        `result` is the transcript's own text of the answer, when that is where
        this observation came from; the hook path leaves it None and the native
        response document below stands in for it. Both spellings of one answer
        converge on one fact.
        """
        call = records.ToolCallNative.model_validate(native)
        call_id = CallId(str(call.tool_use_id or call.id or raw_event.source_position))
        native_name, arguments = self.recall(
            call_id,
            call.tool_name if call.tool_name else None,
            call.tool_input,
        )
        kind = tool_kind(native_name)
        if kind in ("ignored", "message"):
            return []
        tool_response = (
            call.tool_response
            if isinstance(call.tool_response, records.ToolResponse)
            else records.ToolResponse()
        )
        outcome: Outcome = "failed" if failed else "succeeded"
        raw_tool_response: JsonValue = (
            call.tool_response.model_dump(exclude_none=True)
            if isinstance(call.tool_response, records.ToolResponse)
            else call.tool_response
        )
        answered = result if result is not None else result_content(raw_tool_response)
        if kind == "shell":
            return self._shell_finished(raw_event, call_id, native_name, arguments, tool_response, outcome)
        if kind == "skill":
            skill_id = SkillId(call_id)
            payload: EventPayload = SkillFinished(skill_id, outcome, answered)
            return [event(raw_event, "skill", str(skill_id), "finished", payload)]
        if kind == "assignment":
            return self._assignment_finished(raw_event, call_id, tool_response, outcome)
        if kind == "question":
            attention_id = AttentionId(call_id)
            question_arguments = records.QuestionArguments.model_validate(arguments)
            payload = QuestionAnswered(attention_id, attention_answers(question_arguments), None)
            return [event(raw_event, "question", str(attention_id), "answered", payload)]
        if kind == "plan":
            attention_id = AttentionId(call_id)
            state, feedback, edited = plan_resolution(call.tool_response, failed)
            payload = PlanResolved(attention_id, state, feedback, edited)
            return [event(raw_event, "plan", str(attention_id), "resolved", payload)]
        if kind == "file":
            return self.file_facts(raw_event, call_id, native_name, arguments, tool_response, outcome)
        if kind == "search":
            search_arguments = records.SearchArguments.model_validate(arguments)
            query = next(
                (
                    getattr(search_arguments, field)
                    for field in SEARCH_QUERY_FIELDS
                    if getattr(search_arguments, field)
                ),
                None,
            )
            payload = SearchPerformed(native_name, content(query), answered, outcome)
            return [event(raw_event, "search", call_id, "performed", payload)]
        if kind == "web":
            url = records.WebFetchArguments.model_validate(arguments).url
            payload = WebFetched(str(url) if url else None, answered, outcome)
            return [event(raw_event, "web", call_id, "fetched", payload)]
        records.WorktreeArguments.model_validate(arguments)  # shape-check only
        action: WorktreeAction = "entered" if native_name == "EnterWorktree" else "exited"
        payload = WorktreeChanged(action, content(arguments) if arguments else None, outcome)
        return [event(raw_event, "worktree", call_id, "changed", payload)]

    def tool_result(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        result_text: str,
        failed: bool,
        tool_response: JsonValue,
    ) -> list[CanonicalEvent[EventPayload]]:
        """One tool_result block from the transcript, as facts.

        The transcript names no tool and carries no input, so a call whose start
        this translator never saw — a daemon that restarted mid-call — yields
        nothing rather than a fact with a guessed kind. The hook delivery of the
        same result stands on its own and converges on the same event ids.
        """
        if not self.known(call_id):
            return []
        native_name, _arguments = self.recall(call_id, None, None)
        kind = tool_kind(native_name)
        if kind not in TRANSCRIPT_RESULT_KINDS:
            return []
        events: list[CanonicalEvent[EventPayload]] = []
        if kind == "shell":
            shell_id = ShellId(call_id)
            # REPLACE, and ordinal zero: this is the whole output as the harness
            # recorded it, not one more slice of a file being followed.
            events.append(event(
                raw_event,
                "shell",
                str(shell_id),
                "progress:0",
                ShellProgressed(shell_id, 0, "output", content(result_text), "replace"),
            ))
        native: dict[str, JsonValue] = {"tool_use_id": str(call_id), "tool_response": tool_response}
        events.extend(
            self.tool_finished(raw_event, native, failed, result=content(result_text))
        )
        return events

    def pending_attention(self, call_id: CallId) -> bool:
        """Whether this call was one that asks a person something."""
        if not self.known(call_id):
            return False
        native_name, _arguments = self.recall(call_id, None, None)
        return tool_kind(native_name) in ("question", "plan")

    def _shell_finished(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        native_name: str,
        arguments: dict[str, JsonValue],
        tool_response: records.ToolResponse,
        outcome: Outcome,
    ) -> list[CanonicalEvent[EventPayload]]:
        shell_id = ShellId(call_id)
        events: list[CanonicalEvent[EventPayload]] = []
        response = tool_response
        shell_arguments = records.ShellArguments.model_validate(arguments)
        # BACKGROUNDED MID-RUN (ctrl+b on a running command). Structural, from the
        # one document that holds both halves: the input never asked to run in the
        # background, and the response carries a background task id anyway. The
        # stub in the transcript's tool_result says the same thing in prose, but its
        # message id belongs to a namespace the transcript never uses again.
        #
        # NOT keyed on the response's `backgroundedByUser` flag, though it is right
        # there beside the task id (measured: `{"backgroundTaskId":"b18ibyhwf",
        # "backgroundedByUser":true}`). The flag answers WHO moved it, and the
        # harness can move a command itself — `isAutobackgroundingAllowed` decides
        # when — which is the same fact about the command arriving with the flag
        # false. What matters here is that it moved.
        #
        # BEFORE the finish below, deliberately: the follow of the file this
        # command is still writing to is ended by `shell.finished` unless this
        # fact has already re-armed it (see ShellBackgrounded).
        background_task_id = ShellNativeId(str(response.backgroundTaskId or ""))
        if background_task_id and not shell_arguments.run_in_background:
            events.append(event(
                raw_event,
                "shell",
                str(shell_id),
                "backgrounded",
                ShellBackgrounded(shell_id, background_task_id),
            ))
        # An armed Monitor names its task id here and nowhere else this
        # translation can see it. The `shell.finished` below is the ARM
        # returning, not the watch ending — the watch runs on, and its own end
        # arrives as a notification (see monitor_armed).
        if native_name == "Monitor":
            task_id = ShellNativeId(str(response.taskId or ""))
            if task_id:
                self.monitor_armed(task_id, shell_id)
        events.append(event(
            raw_event,
            "shell",
            str(shell_id),
            "finished",
            # Claude Code reports no exit status anywhere in a tool result.
            ShellFinished(shell_id, outcome, None, None),
        ))
        return events

    def _assignment_finished(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        tool_response: records.ToolResponse,
        outcome: Outcome,
    ) -> list[CanonicalEvent[EventPayload]]:
        async_launched = (
            tool_response.isAsync is True or tool_response.status == "async_launched"
        )
        if async_launched:
            return []
        assignment_id = AssignmentId(call_id)
        payload = ActorAssignmentFinished(assignment_id, outcome, None, None)
        return [event(raw_event, "actor_assignment", str(assignment_id), "finished", payload)]

    def attention_declined(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        result_text: str,
    ) -> CanonicalEvent[EventPayload]:
        """The resolution of an attention the user REFUSED. A refused tool call never
        runs, so Claude Code fires no PostToolUse and `tool_finished` — the only other
        emitter — never sees it; the transcript's tool_result is the sole raw event the
        request ended. It names no tool, hence the remembered call. Refusal carries
        no answers, so nothing is lost if the hook path also reports the same fact:
        both derive the resolution from the same text and converge on one event."""
        attention_id = AttentionId(call_id)
        native_name, _arguments = self.recall(call_id, None, None)
        if native_name == "AskUserQuestion":
            payload: EventPayload = QuestionAnswered(attention_id, (), None)
            return event(raw_event, "question", str(attention_id), "answered", payload)
        state, feedback, edited = plan_resolution(result_text, True)
        payload = PlanResolved(attention_id, state, feedback, edited)
        return event(raw_event, "plan", str(attention_id), "resolved", payload)

    def file_facts(
        self,
        raw_event: RawEvent,
        call_id: CallId,
        native_name: str,
        arguments: dict[str, JsonValue],
        tool_response: records.ToolResponse,
        outcome: Outcome,
    ) -> list[CanonicalEvent[EventPayload]]:
        action = FILE_ACTIONS.get(native_name)
        if action is None:
            return []
        file_arguments = records.FileArguments.model_validate(arguments)
        path = file_arguments.file_path or file_arguments.notebook_path or ""
        if not path:
            return []
        content_value = (
            tool_response.content if tool_response.content is not None else file_arguments.content
        )
        unified_diff, lines_added, lines_removed = structured_patch(path, tool_response)
        payload = FileAccessed(
            path,
            action,
            outcome,
            lines_added=lines_added,
            lines_removed=lines_removed,
            unified_diff=unified_diff,
            # The file's own text, where the raw event carries it: what Write
            # wrote, or what Read read back. An edit's text is its diff.
            content=content(content_value) if content_value and unified_diff is None else None,
        )
        return [event(raw_event, "file", f"{call_id}:{action}:{path}", "accessed", payload)]

    @staticmethod
    def questions(
        arguments: dict[str, JsonValue],
    ) -> tuple[AttentionPrompt, ...]:
        question_arguments = records.QuestionArguments.model_validate(arguments)
        prompts = []
        for index, question in enumerate(question_arguments.questions or ()):
            choices = tuple(
                AttentionChoice(option.label or "", option.description or None)
                for option in question.options or ()
            )
            prompts.append(
                AttentionPrompt(
                    prompt_id=QuestionId(str(question.id if question.id is not None else index)),
                    title=question.header or None,
                    prompt=question.question or "",
                    multiple=bool(question.multiSelect),
                    choices=choices,
                )
            )
        return tuple(prompts)
