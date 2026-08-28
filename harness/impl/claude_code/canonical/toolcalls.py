"""Tool-call semantics: which fact a tool call IS, and when it is known.

One table, `TOOL_KINDS`, decides what a tool means; there is no generic
operation verb any more. A shell call has a life (started, output, finished); a
file, search, fetch or worktree call has only a RESULT — the path or query and
what came back of it are one fact, and neither half is worth recording without
the other. So the result-time kinds emit nothing at start, and the call's
arguments are remembered until the result arrives.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    BrowserInteracted,
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
    ShellOutputFinished,
    ShellProgressed,
    ShellStarted,
    SkillFinished,
    SkillStarted,
    WebFetched,
    WorktreeChanged,
)
from domain.ids import (
    SessionId,
    ShellId,
)
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeCallId,
    ClaudeCodeQuestionId,
    ClaudeCodeShellId,
    actor_id_from_claude_code,
    assignment_id_from_claude_code_call,
    attention_id_from_claude_code_call,
    message_id_from_claude_code_call,
    question_id_from_claude_code,
    shell_id_from_claude_code_call,
    skill_id_from_claude_code_call,
)
from domain.values import (
    AttentionAnswer,
    AttentionChoice,
    AttentionPrompt,
    Content,
    ExecutionMode,
    FileAction,
    MessagePhase,
    MessageRole,
    Outcome,
    OutputMode,
    PlanState,
    ProgressStream,
    WorktreeAction,
    content_text as value_content_text,
)
from harness.impl.claude_code.canonical import records, transcript
from harness.impl.claude_code.canonical.support import content, event
from harness.models import RawEvent, UnknownRawEvent, plan_resolution_phase

# The tool_result boilerplate Claude Code emits when a Bash command is launched
# in the background. Its shell.finished still converges from the hook's raw event;
# only this text is suppressed.
BACKGROUND_LAUNCH_STUB = "Command running in background with ID:"
SHELL_EXIT_CODE = re.compile(r"(?:^|\n)(?:Error: )?Exit code (\d+)(?:\n|$)")

class ToolKind(StrEnum):
    SHELL = "shell"
    FILE = "file"
    SEARCH = "search"
    WEB = "web"
    BROWSER = "browser"
    WORKTREE = "worktree"
    SKILL = "skill"
    ASSIGNMENT = "assignment"
    MESSAGE = "message"
    QUESTION = "question"
    PLAN = "plan"
    IGNORED = "ignored"


# What each tool IS. The `IGNORED` entries are named rather than left to fall
# through: a task tool's fact arrives as `task.changed` from the task source, a
# generated image exposes no readable path to put on a file fact, and the two
# agent-plumbing calls carry nothing anybody reads. `EnterPlanMode` is the same
# shape: every call in the real corpus carries no arguments, and every result
# is the one fixed instruction text Claude Code always sends back — nothing
# session-specific for the feed to show. Its sibling `ExitPlanMode` is PLAN
# below, because that call carries the plan text and the person's decision.
# TaskStop is ignored by this generic call-result table because hooks.py turns
# its successful result into the background shell's structural end. TaskOutput
# is the read-back of that same background shell's already-recorded output, so
# a second operation would duplicate it. An unlisted name is drift, except for
# the browser MCP prefix handled by `tool_kind` below.
TOOL_KINDS: Mapping[str, ToolKind] = {
    "Bash": ToolKind.SHELL, "Monitor": ToolKind.SHELL,
    "exec_command": ToolKind.SHELL, "read_command": ToolKind.SHELL,
    "py": ToolKind.SHELL, "mcp__node_repl__js": ToolKind.SHELL,
    "Read": ToolKind.FILE, "Write": ToolKind.FILE, "Edit": ToolKind.FILE,
    "MultiEdit": ToolKind.FILE, "NotebookEdit": ToolKind.FILE,
    "Grep": ToolKind.SEARCH, "Glob": ToolKind.SEARCH,
    "WebSearch": ToolKind.SEARCH, "ToolSearch": ToolKind.SEARCH,
    "WebFetch": ToolKind.WEB, "EnterWorktree": ToolKind.WORKTREE,
    "ExitWorktree": ToolKind.WORKTREE, "Skill": ToolKind.SKILL,
    "Task": ToolKind.ASSIGNMENT, "Agent": ToolKind.ASSIGNMENT,
    "SendMessage": ToolKind.MESSAGE, "AskUserQuestion": ToolKind.QUESTION,
    "ExitPlanMode": ToolKind.PLAN, "EnterPlanMode": ToolKind.IGNORED,
    "TaskCreate": ToolKind.IGNORED, "TaskUpdate": ToolKind.IGNORED,
    "TaskGet": ToolKind.IGNORED, "TaskList": ToolKind.IGNORED,
    "TaskStop": ToolKind.IGNORED, "TaskOutput": ToolKind.IGNORED,
    "ListAgents": ToolKind.IGNORED,
    "DesignSync": ToolKind.IGNORED,
    "GenerateImage": ToolKind.IGNORED, "image_gen__imagegen": ToolKind.IGNORED,
}

# Which field of a search tool's input holds what was searched for.
SEARCH_QUERY_FIELDS = ("pattern", "query")

# Kinds whose whole fact a transcript tool_result can complete on its own. An
# assignment's end and an attention's resolution are deliberately absent: the
# async-launch marker and the plan's decision text live in the native response
# document, which only the hook delivery carries, so those two facts stay the
# hook's — exactly the division of labour the generic operation finish had.
TRANSCRIPT_RESULT_KINDS: frozenset[ToolKind] = frozenset(
    {
        ToolKind.SHELL,
        ToolKind.FILE,
        ToolKind.SEARCH,
        ToolKind.WEB,
        ToolKind.BROWSER,
        ToolKind.WORKTREE,
        ToolKind.SKILL,
    }
)

FILE_ACTIONS: Mapping[str, FileAction] = {
    "Read": FileAction.READ, "Write": FileAction.CREATED,
    "Edit": FileAction.UPDATED, "MultiEdit": FileAction.UPDATED,
    "NotebookEdit": FileAction.UPDATED,
}


def tool_kind(native_name: str) -> ToolKind:
    # Claude in Chrome is a deferred MCP family whose individual verbs evolve
    # independently. They all describe interaction with or reading from a web
    # page, the same canonical family Codex uses for open/click/find/screenshot.
    if native_name.startswith("mcp__claude-in-chrome__"):
        return ToolKind.BROWSER
    kind = TOOL_KINDS.get(native_name)
    if kind is None:
        raise UnknownRawEvent(f"unmapped Claude Code tool: {native_name or '<missing>'}")
    return kind


CHROME_TOOL_PREFIX = "mcp__claude-in-chrome__"

CHROME_ACTIONS: tuple[tuple[str, str], ...] = (
    ("browser_batch", "Run browser actions"),
    ("file_upload", "Upload file in browser"),
    ("form_input", "Fill browser form"),
    ("get_page_text", "Read page text"),
    ("gif_creator", "Record browser GIF"),
    ("javascript_tool", "Run JavaScript in browser"),
    ("list_connected_browsers", "List connected browsers"),
    ("read_console_messages", "Read browser console"),
    ("read_network_requests", "Read browser network requests"),
    ("read_page", "Read page"),
    ("resize_window", "Resize browser window"),
    ("select_browser", "Select browser"),
    ("shortcuts_execute", "Run browser shortcut"),
    ("shortcuts_list", "List browser shortcuts"),
    ("switch_browser", "Switch browser"),
    ("tabs_close_mcp", "Close browser tab"),
    ("tabs_context_mcp", "Read browser tabs"),
    ("tabs_create_mcp", "Create browser tab"),
    ("upload_image", "Upload image in browser"),
)


def _plain_action(value: str) -> str:
    return " ".join(value.removesuffix("_mcp").split("_")).capitalize()


def browser_action(native_name: str, arguments: records.ToolArguments) -> str:
    """Make one short action label from a Claude Chrome call."""
    tool_name = native_name.removeprefix(CHROME_TOOL_PREFIX)
    if tool_name == "navigate" and arguments.url:
        return f"Navigate to {arguments.url}"
    if tool_name == "find":
        query = arguments.query or arguments.pattern
        if query:
            return f"Find {query} on page"
    if tool_name == "computer" and arguments.action:
        action = arguments.action.strip().lower()
        if action == "screenshot":
            return "Capture browser screenshot"
        if action == "wait":
            return "Wait in browser"
        return f"{_plain_action(action)} in browser"
    return next(
        (
            action
            for chrome_tool_name, action in CHROME_ACTIONS
            if chrome_tool_name == tool_name
        ),
        _plain_action(tool_name),
    )


def browser_result_content(
    tool_response: records.ToolResponse | records.ToolResponseBlocks | str | None,
) -> Content | None:
    """Keep browser text. Do not copy binary image data into the feed."""
    if not tool_response:
        return None
    if isinstance(tool_response, str):
        return content(tool_response)
    if isinstance(tool_response, records.ToolResponse):
        native_result = tool_response.result or tool_response.content
        if isinstance(native_result, str):
            return content(native_result)
        tool_response = native_result
    if isinstance(tool_response, records.ToolResponseBlocks):
        parts = []
        for part in tool_response.root:
            if isinstance(part, str):
                parts.append(part)
            elif part.type == "image":
                parts.append("[image]")
            elif part.text:
                parts.append(part.text)
        text = "\n".join(part for part in parts if part).strip()
        return content(text) if text else None
    return None


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


def result_content(
    tool_response: records.ToolResponse | records.ToolResponseBlocks | str | None,
) -> Content | None:
    """What a call answered, whatever shape the raw event held it in.

    A hook reports the native response document, the transcript reports the
    text of the same answer; both are readable and both converge on one fact,
    so neither is preferred over the other here.
    """
    if not tool_response:
        return None
    if isinstance(tool_response, records.ToolResponse):
        native_result = tool_response.result or tool_response.content
        if isinstance(native_result, str):
            return content(native_result)
    return content(tool_response)


def web_search_content(
    tool_response: records.ToolResponse,
    fallback_query: str,
) -> Content:
    """Render Claude WebSearch identically from hook and transcript sources."""
    query = tool_response.query or fallback_query
    links: list[str] = []
    answers: list[str] = []
    for result in tool_response.results or ():
        if isinstance(result, str):
            if result.strip():
                answers.append(result.strip())
            continue
        for link in result.content or ():
            title = (link.title or link.url or "").strip()
            url = (link.url or "").strip()
            if title and url:
                links.append(f"- {title} — {url}")
            elif title:
                links.append(f"- {title}")
    parts = [f'Web search results for query: "{query}"']
    if links:
        parts.append("Links:\n" + "\n".join(links))
    parts.extend(answers)
    return content("\n\n".join(parts))


def attention_answers(
    arguments: records.ToolArguments,
) -> tuple[AttentionAnswer, ...]:
    native_answers = arguments.answers
    if native_answers is None:
        return ()
    answers = []
    for question_index, question in enumerate(arguments.questions or ()):
        prompt = str(question.question or "")
        native_answer = native_answers.root.get(prompt)
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
                prompt_id=question_id_from_claude_code(
                    ClaudeCodeQuestionId(
                        str(question.id if question.id is not None else question_index)
                    )
                ),
                labels=labels,
            )
        )
    return tuple(answers)


def plan_resolution(
    tool_response: records.ToolResponse | records.ToolResponseBlocks | str | None,
    failed: bool,
) -> tuple[PlanState, str | None, bool]:
    if not failed:
        edited = bool(
            isinstance(tool_response, records.ToolResponse) and tool_response.planWasEdited
        )
        return PlanState.APPROVED, None, edited
    if isinstance(tool_response, str):
        text = tool_response
    elif tool_response is None:
        text = "{}"
    else:
        text = tool_response.model_dump_json(exclude_none=True)
    marker = "the user said:"
    marker_position = text.find(marker)
    if marker_position >= 0:
        return PlanState.CHANGES_REQUESTED, text[marker_position + len(marker):].strip(), False
    return PlanState.REJECTED, None, False


@dataclass(frozen=True)
class RememberedCall:
    session_id: SessionId
    call_id: ClaudeCodeCallId
    native_name: str
    arguments: records.ToolArguments


@dataclass
class MonitorState:
    session_id: SessionId
    task_id: ClaudeCodeShellId
    shell_id: ShellId
    event_count: int = 0


@dataclass(frozen=True)
class BackgroundTaskState:
    session_id: SessionId
    task_id: ClaudeCodeShellId
    shell_id: ShellId


@dataclass(frozen=True)
class AgentAssignmentState:
    session_id: SessionId
    actor_id: ClaudeCodeActorId
    call_id: ClaudeCodeCallId


class ToolCallSemantics:
    """Session-scoped state that joins related native events.

    A call's name and input stay here until all result channels can use them.
    Assignment, monitor, and background-task links stay only until their native
    finish record. The interpreter clears all remaining state when the session
    finishes.
    """

    def __init__(self) -> None:
        self.calls: dict[tuple[SessionId, ClaudeCodeCallId], RememberedCall] = {}
        self.loaded_skills: set[tuple[SessionId, ClaudeCodeCallId]] = set()
        self.agent_assignments: dict[
            tuple[SessionId, ClaudeCodeActorId], AgentAssignmentState
        ] = {}
        # An armed Monitor's TASK id -> the shell that armed it, and how many
        # of its events have been attributed so far. A monitor's per-event
        # notification names only the task id — never the tool_use_id (measured
        # in claude-code 2.1.233) — so this is the only route from an event back
        # to the command the monitors tab lists. Its stream-ENDED notification
        # does carry the tool_use_id, so the end needs no memory and survives a
        # daemon restart that loses this.
        self.monitors: dict[tuple[SessionId, ClaudeCodeShellId], MonitorState] = {}
        # A background task's native id -> the Bash shell that launched it.
        # TaskStop names only this task id, so the stop needs this link to close
        # the shell. The transcript is the durable fallback after a restart.
        self.background_tasks: dict[
            tuple[SessionId, ClaudeCodeShellId], BackgroundTaskState
        ] = {}

    # --- what a call was, across the two raw event streams ------------------

    def remember(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        native_name: str,
        arguments: records.ToolArguments,
    ) -> None:
        key = raw_event.session_id, call_id
        self.calls[key] = RememberedCall(
            raw_event.session_id,
            call_id,
            native_name,
            arguments,
        )

    def recall(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        native_name: str | None,
        arguments: records.ToolArguments | None,
    ) -> tuple[str, records.ToolArguments]:
        """The call's name and input: what this record carries, else what the
        request said. A record that has neither is a call whose start we never
        saw — a daemon that restarted mid-call — and it cannot be classified."""
        remembered = self.calls.get((raw_event.session_id, call_id))
        name = native_name or (remembered.native_name if remembered else "")
        if not name:
            raise UnknownRawEvent(f"Claude Code tool result names no call: {call_id or '<missing>'}")
        return name, arguments or (remembered.arguments if remembered else records.ToolArguments())

    def known(self, raw_event: RawEvent, call_id: ClaudeCodeCallId) -> bool:
        return (raw_event.session_id, call_id) in self.calls

    def is_skill(self, raw_event: RawEvent, call_id: ClaudeCodeCallId) -> bool:
        remembered = self.calls.get((raw_event.session_id, call_id))
        return remembered is not None and remembered.native_name == "Skill"

    def forget(self, raw_event: RawEvent, call_id: ClaudeCodeCallId) -> None:
        """Release a call after all result channels have used its input."""
        self.calls.pop((raw_event.session_id, call_id), None)

    def clear_session(self, session_id: SessionId) -> None:
        """Release all transient correlation after one native session ends."""
        for call_key in tuple(self.calls):
            if call_key[0] == session_id:
                del self.calls[call_key]
        for assignment_key in tuple(self.agent_assignments):
            if assignment_key[0] == session_id:
                del self.agent_assignments[assignment_key]
        for monitor_key in tuple(self.monitors):
            if monitor_key[0] == session_id:
                del self.monitors[monitor_key]
        for background_key in tuple(self.background_tasks):
            if background_key[0] == session_id:
                del self.background_tasks[background_key]
        self.loaded_skills = {
            key for key in self.loaded_skills if key[0] != session_id
        }

    def skill_loaded(
        self,
        raw_event: RawEvent,
        name: str,
        output: str,
    ) -> CanonicalEvent[EventPayload] | None:
        """Finish the most recent matching Skill call with its loaded file.

        Claude's Skill tool first answers with an empty ``{}``, then injects a
        synthetic prompt containing the actual SKILL.md text.  The injected
        prompt is the useful result of the call, not a separate conversation
        message.  It has no tool-use id, so join it to the newest unclaimed
        matching call in this session.
        """
        for remembered in reversed(tuple(self.calls.values())):
            key = remembered.session_id, remembered.call_id
            if (
                remembered.session_id != raw_event.session_id
                or remembered.native_name != "Skill"
                or str(remembered.arguments.skill or "") != name
                or key in self.loaded_skills
            ):
                continue
            self.loaded_skills.add(key)
            skill_id = skill_id_from_claude_code_call(remembered.call_id)
            return event(
                raw_event,
                "skill",
                str(skill_id),
                "finished",
                SkillFinished(skill_id, Outcome.SUCCEEDED, content(output)),
            )
        return None

    def assignment_launched(
        self,
        raw_event: RawEvent,
        actor_id: ClaudeCodeActorId,
        call_id: ClaudeCodeCallId,
    ) -> None:
        key = raw_event.session_id, actor_id
        self.agent_assignments[key] = AgentAssignmentState(
            raw_event.session_id,
            actor_id,
            call_id,
        )

    def assignment_call(
        self,
        raw_event: RawEvent,
        actor_id: ClaudeCodeActorId | None,
        notification_call_id: ClaudeCodeCallId,
    ) -> ClaudeCodeCallId:
        """Return the Agent call that owns one child completion.

        A resumed async child names the SendMessage call in its final task
        notification. The Agent result is the durable child-to-assignment
        relation. Keep the live relation in memory and recover it from the
        parent transcript after an application restart.
        """
        if actor_id is None:
            return notification_call_id
        remembered = self.agent_assignments.get((raw_event.session_id, actor_id))
        if remembered is not None:
            return remembered.call_id
        durable = transcript.assignment_call_before(
            raw_event.source_name,
            raw_event.source_position,
            actor_id,
        )
        if durable is not None:
            self.assignment_launched(raw_event, actor_id, durable)
            return durable
        return notification_call_id

    def assignment_finished(
        self,
        raw_event: RawEvent,
        actor_id: ClaudeCodeActorId | None,
    ) -> None:
        if actor_id is not None:
            self.agent_assignments.pop((raw_event.session_id, actor_id), None)

    def monitor_armed(
        self,
        raw_event: RawEvent,
        task_id: ClaudeCodeShellId,
        shell_id: ShellId,
    ) -> None:
        key = raw_event.session_id, task_id
        existing = self.monitors.get(key)
        if existing is not None and existing.shell_id == shell_id:
            return
        self.monitors[key] = MonitorState(
            raw_event.session_id,
            task_id,
            shell_id,
        )

    def monitor_shell(
        self,
        raw_event: RawEvent,
        task_id: ClaudeCodeShellId,
    ) -> ShellId | None:
        monitor = self.monitors.get((raw_event.session_id, task_id))
        return monitor.shell_id if monitor else None

    def next_monitor_ordinal(
        self,
        raw_event: RawEvent,
        task_id: ClaudeCodeShellId,
    ) -> int:
        """The position of the next event of this monitor, counted from zero.

        Part of the event's identity, not decoration: `stable_event_id` is built
        from the subject and the phase, so two events of one monitor recorded
        under the same phase would collapse into one row (measured — six ticks
        became one canonical event that way)."""
        monitor = self.monitors.get((raw_event.session_id, task_id))
        if monitor is None:
            return 0
        ordinal = monitor.event_count
        monitor.event_count += 1
        return ordinal

    def monitor_finished(self, raw_event: RawEvent, shell_id: ShellId) -> None:
        for key, monitor in tuple(self.monitors.items()):
            if key[0] == raw_event.session_id and monitor.shell_id == shell_id:
                del self.monitors[key]

    def background_launched(
        self,
        raw_event: RawEvent,
        task_id: ClaudeCodeShellId,
        shell_id: ShellId,
    ) -> None:
        self.background_tasks[(raw_event.session_id, task_id)] = BackgroundTaskState(
            raw_event.session_id,
            task_id,
            shell_id,
        )

    def background_stopped(
        self,
        raw_event: RawEvent,
        task_id: ClaudeCodeShellId,
        transcript_path: str,
    ) -> list[CanonicalEvent[EventPayload]]:
        """Close a background Bash command after a successful TaskStop."""
        if not task_id:
            return []
        key = raw_event.session_id, task_id
        background = self.background_tasks.get(key)
        if background is None:
            call_id = transcript.background_call(transcript_path, task_id)
            if call_id is None:
                return []
            shell_id = shell_id_from_claude_code_call(call_id)
            background = BackgroundTaskState(raw_event.session_id, task_id, shell_id)
        self.background_tasks.pop(key, None)
        return [event(
            raw_event,
            "shell",
            str(background.shell_id),
            "output_finished",
            ShellOutputFinished(background.shell_id, Outcome.CANCELLED),
        )]

    # --- the request ---------------------------------------------------------

    def tool_started(
        self,
        raw_event: RawEvent,
        tool_call_native: records.ToolCallNative,
    ) -> list[CanonicalEvent[EventPayload]]:
        call = tool_call_native
        call_id = ClaudeCodeCallId(str(call.tool_use_id or call.id or raw_event.source_position))
        native_name = str(call.tool_name or call.name or "tool")
        kind = tool_kind(native_name)
        arguments = call.tool_input if call.tool_input is not None else call.input
        arguments = arguments if arguments is not None else records.ToolArguments()
        self.remember(raw_event, call_id, native_name, arguments)
        if kind == ToolKind.SHELL:
            return [self._shell_started(raw_event, call_id, native_name, arguments)]
        if kind == ToolKind.SKILL:
            return [self._skill_started(raw_event, call_id, arguments)]
        if kind == ToolKind.ASSIGNMENT:
            return [self._assignment_started(raw_event, call_id, arguments)]
        if kind == ToolKind.MESSAGE:
            return [self._actor_message(raw_event, call_id, arguments)]
        if kind == ToolKind.QUESTION:
            attention_id = attention_id_from_claude_code_call(call_id)
            payload: EventPayload = QuestionAsked(attention_id, self.questions(arguments))
            return [event(raw_event, "question", str(attention_id), "asked", payload)]
        if kind == ToolKind.PLAN:
            attention_id = attention_id_from_claude_code_call(call_id)
            payload = PlanProposed(attention_id, content(arguments.plan or "", markdown=True))
            return [event(raw_event, "plan", str(attention_id), "proposed", payload)]
        # file, search, web, worktree: nothing is known yet that is worth a
        # fact. `ignored`: nothing ever will be.
        return []

    def _shell_started(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        native_name: str,
        arguments: records.ToolArguments,
    ) -> CanonicalEvent[EventPayload]:
        shell = arguments
        shell_id = shell_id_from_claude_code_call(call_id)
        if native_name == "Monitor":
            execution: ExecutionMode = ExecutionMode.MONITOR
        elif native_name == "Bash" and shell.run_in_background:
            execution = ExecutionMode.BACKGROUND
        else:
            execution = ExecutionMode.FOREGROUND
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
        call_id: ClaudeCodeCallId,
        arguments: records.ToolArguments,
    ) -> CanonicalEvent[EventPayload]:
        skill_id = skill_id_from_claude_code_call(call_id)
        name = str(arguments.skill or "")
        # The input a Skill call carries is the skill name and, at most, an
        # `args` string; when that is all there is, there are no arguments to
        # show and saying so beats echoing the name twice.
        payload = SkillStarted(skill_id, name, content(arguments.args) if arguments.args else None)
        return event(raw_event, "skill", str(skill_id), "started", payload)

    def _assignment_started(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        arguments: records.ToolArguments,
    ) -> CanonicalEvent[EventPayload]:
        assignment_id = assignment_id_from_claude_code_call(call_id)
        assignment = arguments
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
        call_id: ClaudeCodeCallId,
        arguments: records.ToolArguments,
    ) -> CanonicalEvent[EventPayload]:
        """A SendMessage: the actor speaking to a named peer, which is a message
        with a recipient — not a tool call with a text argument."""
        message = arguments
        recipient_text = str(message.recipient or message.to or "peer")
        recipient = (
            raw_event.parent_actor_id
            if recipient_text == transcript.LEAD_TEAMMATE_ID
            and raw_event.parent_actor_id is not None
            else actor_id_from_claude_code(ClaudeCodeActorId(recipient_text))
        )
        message_id = message_id_from_claude_code_call(call_id)
        payload = MessageCreated(
            message_id,
            MessageRole.ASSISTANT,
            content(message.content or message.message, markdown=True),
            MessagePhase.INTERMEDIATE,
            None,
            recipient,
        )
        return event(raw_event, "message", str(message_id), "created", payload)

    # --- the result ----------------------------------------------------------

    def tool_finished(
        self,
        raw_event: RawEvent,
        tool_call_native: records.ToolCallNative,
        failed: bool,
        *,
        result: Content | None = None,
        cancelled: bool = False,
    ) -> list[CanonicalEvent[EventPayload]]:
        """Everything one tool call's RESULT says.

        `result` is the transcript's own text of the answer, when that is where
        this observation came from; the hook path leaves it None and the native
        response document below stands in for it. Both spellings of one answer
        converge on one fact.
        """
        call = tool_call_native
        call_id = ClaudeCodeCallId(str(call.tool_use_id or call.id or raw_event.source_position))
        native_name, arguments = self.recall(
            raw_event,
            call_id,
            call.tool_name if call.tool_name else None,
            call.tool_input,
        )
        kind = tool_kind(native_name)
        if kind in (ToolKind.IGNORED, ToolKind.MESSAGE):
            return []
        tool_response = (
            call.tool_response
            if isinstance(call.tool_response, records.ToolResponse)
            else records.ToolResponse()
        )
        outcome: Outcome = (
            Outcome.CANCELLED
            if cancelled
            else Outcome.FAILED
            if failed
            else Outcome.SUCCEEDED
        )
        answered = (
            result
            if result is not None
            else browser_result_content(call.tool_response)
            if kind == ToolKind.BROWSER
            else result_content(call.tool_response)
        )
        if (
            native_name == "WebSearch"
            and isinstance(call.tool_response, records.ToolResponse)
            and call.tool_response.results is not None
        ):
            answered = web_search_content(
                call.tool_response,
                str(arguments.query or ""),
            )
        if (
            result is None
            and native_name == "ToolSearch"
            and isinstance(call.tool_response, records.ToolResponse)
            and call.tool_response.matches is not None
        ):
            loaded_tools = "\n".join(
                f"→ loaded tool: {tool_name}"
                for tool_name in call.tool_response.matches
            )
            answered = content(
                loaded_tools or "No matching tools."
            )
        if (
            result is None
            and native_name in ("Grep", "Glob")
            and isinstance(call.tool_response, records.ToolResponse)
            and call.tool_response.filenames is not None
        ):
            answered = content("\n".join(call.tool_response.filenames))
        if kind == ToolKind.SHELL:
            return self._shell_finished(
                raw_event,
                call_id,
                native_name,
                arguments,
                tool_response,
                answered,
                outcome,
            )
        if kind == ToolKind.SKILL:
            skill_id = skill_id_from_claude_code_call(call_id)
            # A successful Claude Skill call normally returns only `{}`.  Its
            # real answer is the synthetic prompt containing the loaded
            # SKILL.md, handled by ``skill_loaded`` above.  Keep meaningful
            # responses and failures for compatibility with older versions.
            skill_answer = value_content_text(answered).strip()
            if outcome == Outcome.SUCCEEDED and (
                skill_answer in ("", "{}")
                or skill_answer.startswith("Launching skill:")
            ):
                return []
            payload: EventPayload = SkillFinished(skill_id, outcome, answered)
            return [event(raw_event, "skill", str(skill_id), "finished", payload)]
        if kind == ToolKind.ASSIGNMENT:
            return self._assignment_finished(raw_event, call_id, tool_response, outcome)
        if kind == ToolKind.QUESTION:
            attention_id = attention_id_from_claude_code_call(call_id)
            payload = QuestionAnswered(attention_id, attention_answers(arguments), None)
            return [event(raw_event, "question", str(attention_id), "answered", payload)]
        if kind == ToolKind.PLAN:
            attention_id = attention_id_from_claude_code_call(call_id)
            state, feedback, edited = plan_resolution(call.tool_response, failed)
            payload = PlanResolved(attention_id, state, feedback, edited)
            return [event(
                raw_event,
                "plan",
                str(attention_id),
                plan_resolution_phase(payload),
                payload,
            )]
        if kind == ToolKind.FILE:
            return self.file_facts(raw_event, call_id, native_name, arguments, tool_response, outcome)
        if kind == ToolKind.SEARCH:
            query = next(
                (
                    getattr(arguments, field)
                    for field in SEARCH_QUERY_FIELDS
                    if getattr(arguments, field)
                ),
                None,
            )
            payload = SearchPerformed(native_name, content(query), answered, outcome)
            return [event(raw_event, "search", call_id, "performed", payload)]
        if kind == ToolKind.WEB:
            url = arguments.url
            payload = WebFetched(str(url) if url else None, answered, outcome)
            return [event(raw_event, "web", call_id, "fetched", payload)]
        if kind == ToolKind.BROWSER:
            payload = BrowserInteracted(
                browser_action(native_name, arguments),
                answered,
                outcome,
            )
            return [event(raw_event, "browser", call_id, "interacted", payload)]
        action: WorktreeAction = WorktreeAction.ENTERED if native_name == "EnterWorktree" else WorktreeAction.EXITED
        payload = WorktreeChanged(action, content(arguments) if arguments else None, outcome)
        return [event(raw_event, "worktree", call_id, "changed", payload)]

    def tool_result(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        result_text: str,
        failed: bool,
        tool_response: records.ToolResponse | records.ToolResponseBlocks | str | None,
        *,
        cancelled: bool = False,
    ) -> list[CanonicalEvent[EventPayload]]:
        """One tool_result block from the transcript, as facts.

        The transcript names no tool and carries no input. Recover these fields
        from the earlier tool-use record when the daemon restarted mid-call.
        The hook delivery of the same result stands on its own and converges on
        the same event ids.
        """
        if not self.known(raw_event, call_id):
            recovered = transcript.tool_call_before(
                raw_event.source_name,
                raw_event.source_position,
                call_id,
            )
            if recovered is None:
                return []
            native_name, arguments = recovered
            self.remember(raw_event, call_id, native_name, arguments)
        native_name, _arguments = self.recall(raw_event, call_id, None, None)
        kind = tool_kind(native_name)
        if kind not in TRANSCRIPT_RESULT_KINDS:
            return []
        events: list[CanonicalEvent[EventPayload]] = []
        if kind == ToolKind.SHELL:
            shell_id = shell_id_from_claude_code_call(call_id)
            # REPLACE, and ordinal zero: this is the whole output as the harness
            # recorded it, not one more slice of a file being followed.
            events.append(event(
                raw_event,
                "shell",
                str(shell_id),
                "progress:0",
                ShellProgressed(shell_id, 0, ProgressStream.OUTPUT, content(result_text), OutputMode.REPLACE),
            ))
        events.extend(
            self.tool_finished(
                raw_event,
                records.ToolCallNative(tool_use_id=call_id, tool_response=tool_response),
                failed,
                result=content(result_text),
                cancelled=cancelled,
            )
        )
        return events

    def pending_attention(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
    ) -> bool:
        """Whether this call was one that asks a person something."""
        if not self.known(raw_event, call_id):
            return False
        native_name, _arguments = self.recall(raw_event, call_id, None, None)
        return tool_kind(native_name) in (ToolKind.QUESTION, ToolKind.PLAN)

    def _shell_finished(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        native_name: str,
        arguments: records.ToolArguments,
        tool_response: records.ToolResponse,
        result: Content | None,
        outcome: Outcome,
    ) -> list[CanonicalEvent[EventPayload]]:
        shell_id = shell_id_from_claude_code_call(call_id)
        events: list[CanonicalEvent[EventPayload]] = []
        response = tool_response
        shell_arguments = arguments
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
        background_task_id = ClaudeCodeShellId(str(response.backgroundTaskId or ""))
        if background_task_id:
            self.background_launched(raw_event, background_task_id, shell_id)
        if background_task_id and not shell_arguments.run_in_background:
            events.append(event(
                raw_event,
                "shell",
                str(shell_id),
                "backgrounded",
                ShellBackgrounded(shell_id),
            ))
        # An armed Monitor names its task id here and nowhere else this
        # translation can see it. The `shell.finished` below is the ARM
        # returning, not the watch ending — the watch runs on, and its own end
        # arrives as a notification (see monitor_armed).
        monitor_task_id = ClaudeCodeShellId("")
        if native_name == "Monitor":
            monitor_task_id = ClaudeCodeShellId(str(response.taskId or ""))
            if monitor_task_id:
                self.monitor_armed(raw_event, monitor_task_id, shell_id)
        exit_match = SHELL_EXIT_CODE.search(value_content_text(result))
        exit_code = int(exit_match.group(1)) if exit_match is not None else None
        if outcome == Outcome.FAILED and exit_code in (130, 137, 143):
            outcome = Outcome.CANCELLED
        events.append(event(
            raw_event,
            "shell",
            str(shell_id),
            "finished",
            ShellFinished(shell_id, outcome, None, exit_code),
        ))
        if native_name == "Monitor" and not monitor_task_id:
            # A rejected monitor has no native task and cannot send a later
            # end notification. Its tool result is its complete lifetime.
            events.append(event(
                raw_event,
                "shell",
                str(shell_id),
                "output_finished",
                ShellOutputFinished(shell_id, outcome),
            ))
        return events

    def _assignment_finished(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        tool_response: records.ToolResponse,
        outcome: Outcome,
    ) -> list[CanonicalEvent[EventPayload]]:
        async_launched = (
            tool_response.isAsync is True
            or tool_response.status in ("async_launched", "teammate_spawned")
        )
        if async_launched:
            native_actor_id = ClaudeCodeActorId(
                str(tool_response.agentId or tool_response.agent_id or "")
            )
            if tool_response.status == "teammate_spawned" and tool_response.name:
                native_actor_id = (
                    transcript.teammate_actor_id(raw_event.source_name, tool_response.name)
                    or ClaudeCodeActorId(tool_response.name)
                )
            if native_actor_id:
                self.assignment_launched(
                    raw_event,
                    native_actor_id,
                    call_id,
                )
            return []
        # A successful Agent hook says that the tool call returned. It does not
        # carry the subagent result. Claude Code sends the semantic completion
        # as a task notification, with the result. If this hook writes the same
        # canonical identity first, normal deduplication must discard the richer
        # notification. Keep a failed hook because no successful completion
        # notification will follow it.
        if outcome == Outcome.SUCCEEDED:
            return []
        assignment_id = assignment_id_from_claude_code_call(call_id)
        payload = ActorAssignmentFinished(assignment_id, outcome, None, None)
        return [event(raw_event, "actor_assignment", str(assignment_id), "finished", payload)]


    def attention_declined(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        result_text: str,
    ) -> CanonicalEvent[EventPayload]:
        """The resolution of an attention the user REFUSED. A refused tool call never
        runs, so Claude Code fires no PostToolUse and `tool_finished` — the only other
        emitter — never sees it; the transcript's tool_result is the sole raw event the
        request ended. It names no tool, hence the remembered call. Refusal carries
        no answers, so nothing is lost if the hook path also reports the same fact:
        both derive the resolution from the same text and converge on one event."""
        attention_id = attention_id_from_claude_code_call(call_id)
        native_name, _arguments = self.recall(raw_event, call_id, None, None)
        if native_name == "AskUserQuestion":
            payload: EventPayload = QuestionAnswered(attention_id, (), None)
            return event(raw_event, "question", str(attention_id), "answered", payload)
        state, feedback, edited = plan_resolution(result_text, True)
        payload = PlanResolved(attention_id, state, feedback, edited)
        return event(
            raw_event,
            "plan",
            str(attention_id),
            plan_resolution_phase(payload),
            payload,
        )

    def file_facts(
        self,
        raw_event: RawEvent,
        call_id: ClaudeCodeCallId,
        native_name: str,
        arguments: records.ToolArguments,
        tool_response: records.ToolResponse,
        outcome: Outcome,
    ) -> list[CanonicalEvent[EventPayload]]:
        action = FILE_ACTIONS.get(native_name)
        if action is None:
            return []
        file_arguments = arguments
        path = file_arguments.file_path or file_arguments.notebook_path or ""
        if not path:
            return []
        read_content = tool_response.file.content if tool_response.file is not None else None
        content_value = (
            file_arguments.content
            if action == FileAction.CREATED
            else read_content
            if read_content is not None
            else tool_response.content
            if tool_response.content is not None
            else file_arguments.content
        )
        unified_diff, lines_added, lines_removed = structured_patch(path, tool_response)
        if (
            action == FileAction.CREATED
            and lines_added is None
            and file_arguments.content is not None
        ):
            lines_added = len(file_arguments.content.splitlines())
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
        arguments: records.ToolArguments,
    ) -> tuple[AttentionPrompt, ...]:
        prompts = []
        for index, question in enumerate(arguments.questions or ()):
            choices = tuple(
                AttentionChoice(option.label or "", option.description or None)
                for option in question.options or ()
            )
            prompts.append(
                AttentionPrompt(
                    prompt_id=question_id_from_claude_code(
                        ClaudeCodeQuestionId(
                            str(question.id if question.id is not None else index)
                        )
                    ),
                    title=question.header or None,
                    prompt=question.question or "",
                    multiple=bool(question.multiSelect),
                    choices=choices,
                )
            )
        return tuple(prompts)
