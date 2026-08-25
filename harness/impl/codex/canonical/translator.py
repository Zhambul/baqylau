"""Codex canonical translation: dispatch across the rollout's records and hooks."""

from __future__ import annotations

import ast
import os
import re
import shlex
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeVar
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from harness.contract import HarnessTranslator
from harness.models import (
    TITLE_SOURCE_TYPE,
    RawEvent,
    TranslationError,
    TranslationResult,
    UnknownRawEvent,
    session_run_started_events,
)
from harness.models.directives import NativeTitleObservation
from repository.mapper.documents import StoredDocumentError, decode_document
from domain.records import RecordedTranslationDecision
from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    ActorStarted,
    CanonicalEvent,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    EventPayload,
    FileAccessed,
    GoalChanged,
    MessageCreated,
    PlanProposed,
    QuestionAnswered,
    QuestionAsked,
    ReasoningCreated,
    SearchPerformed,
    SessionStarted,
    SessionTitleChanged,
    ShellBackgrounded,
    ShellFinished,
    ShellInputProvided,
    ShellOutputFinished,
    ShellProgressed,
    ShellStarted,
    SkillFinished,
    SkillStarted,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
    TurnStarted,
    UsageReported,
    WebFetched,
)
from domain.ids import (
    SessionId,
    ShellId,
    SkillId,
    TurnId,
)
from harness.impl.codex.ids import (
    CodexActorId,
    CodexAttentionId,
    CodexCallId,
    CodexMessageId,
    CodexQuestionId,
    CodexReasoningId,
    CodexSessionId,
    CodexShellId,
    CodexSkillId,
    CodexTaskId,
    CodexTaskListId,
    CodexTurnId,
    actor_id_from_codex,
    assignment_id_from_codex_turn,
    attention_id_from_codex,
    attention_id_from_codex_call,
    message_id_from_codex,
    message_id_from_codex_call,
    question_id_from_codex,
    reasoning_id_from_codex,
    session_id_from_codex,
    shell_id_from_codex_call,
    skill_id_from_codex,
    task_id_from_codex,
    task_list_id_from_codex,
    turn_id_from_codex,
)
from harness.impl.codex.model import CodexModel
from domain.values import (
    ActorRole,
    AttentionAnswer,
    AttentionChoice,
    AttentionPrompt,
    Content,
    EffortChangeReason,
    ExecutionMode,
    FileAction,
    GoalState,
    ModelChangeReason,
    MessagePhase,
    MessageRole,
    Outcome,
    OutputMode,
    ProgressStream,
    TaskState,
    TokenUsage,
    TitleOrigin,
    UsageScope,
)

from harness.impl.codex.canonical import rollout
from harness.impl.codex.canonical.events import PHASE_FINAL
from harness.impl.codex.canonical.records import (
    ActorActivityRecord,
    AskRecord,
    AskResultDocument,
    BadRecord,
    ChatRecord,
    CodexHookPayload,
    CodexToolArguments,
    CollaborationArguments,
    CollaborationCallRecord,
    CommandCompletedRecord,
    CompactBoundaryRecord,
    CompactRecord,
    ExecRecord,
    ExecResultRecord,
    GoalRecord,
    GoalToolRecord,
    MessageRecord,
    McpToolCompletedRecord,
    NodeReplResultDocument,
    PatchCallRecord,
    PatchRecord,
    PlanRecord,
    PromptRecord,
    ReasoningRecord,
    RolloutDocument,
    RolloutHeader,
    RolloutObservation,
    RolloutRecord,
    SearchRecord,
    SendMessageArguments,
    SessionMetaPayload,
    SessionMetaSource,
    SettingsRecord,
    SkillRecord,
    StdinRecord,
    ToolBatchRecord,
    TaskCompleteRecord,
    TaskListRecord,
    TaskStartedRecord,
    ThinkRecord,
    ToolRecord,
    ToolRequest,
    TurnAbortedRecord,
    TurnContextRecord,
    UnmappedToolRecord,
    UsageRecord,
)
from harness.impl.codex.continuity import RewindContinuity
from harness.impl.codex.canonical.sources import lead_rollout, session_metadata
from harness.impl.codex.canonical.support import (
    content,
    event,
    exit_code,
    model_reference,
    outcome_of,
    timestamp,
)
from harness.models.selections import SelectionSemantics

SourceIndexValue = TypeVar("SourceIndexValue")


def _drop_source_keys(
    index: MutableMapping[tuple[str, str], SourceIndexValue],
    source_keys: set[str],
) -> None:
    """Remove keys that belong to a finished session's source files."""
    for key in tuple(index):
        if key[0] in source_keys:
            del index[key]


# What one of Codex's non-shell tool calls IS. `IGNORED` is named rather than
# left to fall through: a generated image exposes no readable path to put on a
# file fact, so there is nothing to record about it.
class CodexToolKind(StrEnum):
    SEARCH = "search"
    WEB = "web"
    FILE = "file"
    IGNORED = "ignored"


@dataclass(frozen=True)
class ToolMeaning:
    kind: CodexToolKind
    native_name: str


CODEX_TOOLS: Mapping[str, ToolMeaning] = {
    "view_image": ToolMeaning(CodexToolKind.FILE, "ReadImage"),
    "read_mcp_resource": ToolMeaning(CodexToolKind.FILE, "ReadResource"),
    "list_mcp_resources": ToolMeaning(CodexToolKind.IGNORED, "ListResources"),
    "list_mcp_resource_templates": ToolMeaning(
        CodexToolKind.IGNORED, "ListResourceTemplates"
    ),
    "image_gen__imagegen": ToolMeaning(CodexToolKind.IGNORED, "GenerateImage"),
    # Deferred web execution yields a local orchestration handle and Codex
    # later waits on that handle.  The search/fetch call owns the user-visible
    # fact; waiting for its cell has no separate canonical meaning.
    "wait": ToolMeaning(CodexToolKind.IGNORED, "WaitForTool"),
}

GOAL_STATES: Mapping[str, GoalState] = {
    "active": GoalState.ACTIVE, "paused": GoalState.PAUSED,
    "blocked": GoalState.BLOCKED, "usageLimited": GoalState.USAGE_LIMITED,
    "budgetLimited": GoalState.BUDGET_LIMITED, "complete": GoalState.COMPLETED,
    "cleared": GoalState.CLEARED,
}

ACTIVITY_CALLS: Mapping[str, str] = {
    "started": "spawn_agent",
    "interrupted": "interrupt_agent",
}

FILE_ACTIONS: Mapping[str, FileAction] = {
    "add": FileAction.CREATED, "delete": FileAction.DELETED,
    "move": FileAction.RENAMED, "update": FileAction.UPDATED,
}


def _codex_tool(native_name: str, arguments: str | None) -> tuple[CodexToolKind, str]:
    """Map Codex transport names onto the canonical vocabulary.

    A name with no fact behind it raises `UnknownRawEvent`: the delivery is
    verdicted `ignored_unknown` — visible in the audit, absent from the feed —
    rather than failing the whole record.
    """
    if native_name == "web__run":
        fields = _tool_fields(arguments)
        if not fields:
            raise TranslationError("Codex web tool arguments are not an object")
        if any(fields.has(field) for field in _SEARCH_QUERY_FIELDS[:-1]):
            return CodexToolKind.SEARCH, "WebSearch"
        if any(fields.has(field) for field in (
            CodexToolField.OPEN, CodexToolField.CLICK,
            CodexToolField.FIND, CodexToolField.SCREENSHOT,
        )):
            return CodexToolKind.WEB, "WebFetch"
        # A time lookup is neither a search nor a fetch: it has no query, no url
        # and no reader.
        raise UnknownRawEvent("unmapped Codex web action")
    if native_name == "mcp__node_repl__js":
        if _node_read_path(arguments):
            return CodexToolKind.FILE, "Read"
        return CodexToolKind.IGNORED, "NodeRepl"
    mapped = CODEX_TOOLS.get(native_name)
    if mapped is None:
        raise UnknownRawEvent(f"unmapped Codex tool: {native_name or '<missing>'}")
    return mapped.kind, mapped.native_name



# A string field of a JavaScript object literal — `{cmd:"ls"}` as codex writes
# it through the exec custom tool, where the key may or may not be quoted. The
# same shape `_JS_CMD` in items.py reads, and for the same reason: the arguments
# are JavaScript source, and nothing here interprets JavaScript.
_JS_STRING_FIELD = re.compile(r"""["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*:\s*"((?:[^"\\]|\\.)*)\"""")
_JS_REQUEST_VALUE = re.compile(
    r'''["']?(?:q|query|url|ref_id)["']?\s*:\s*'''
    r'''("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)
_NODE_READ_FILE = re.compile(
    r'''\breadFile\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)
_NODE_READ_TEMPLATE_EXPRESSION = re.compile(
    r'''\breadFile\(\s*["']\$\{\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*\}["']'''
)
_NODE_READ_CWD_SUFFIX = re.compile(
    r'''\breadFile\(\s*nodeRepl\.cwd\s*\+\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)

# Which field of a web search holds what was searched for, in the order codex
# spells them (`_codex_tool` recognises a search by exactly these names).
class CodexToolField(StrEnum):
    SEARCH_QUERY = "search_query"
    IMAGE_QUERY = "image_query"
    WEATHER = "weather"
    FINANCE = "finance"
    SPORTS = "sports"
    QUERY = "query"
    OPEN = "open"
    CLICK = "click"
    FIND = "find"
    SCREENSHOT = "screenshot"
    URL = "url"
    PATH = "path"
    FILE_PATH = "file_path"
    URI = "uri"


_SEARCH_QUERY_FIELDS = (
    CodexToolField.SEARCH_QUERY, CodexToolField.IMAGE_QUERY, CodexToolField.WEATHER,
    CodexToolField.FINANCE, CodexToolField.SPORTS, CodexToolField.QUERY,
)


@dataclass(frozen=True)
class ToolStringField:
    name: str
    value: str


@dataclass(frozen=True)
class ToolFields:
    document: CodexToolArguments | None
    javascript_strings: tuple[ToolStringField, ...]
    javascript_source: str

    def _javascript_array(self, codex_tool_field: CodexToolField) -> str | None:
        """Return one named JavaScript array without evaluating JavaScript."""
        match = re.search(
            rf'''(?:^|[{{,])\s*["']?{re.escape(codex_tool_field.value)}["']?\s*:\s*\[''',
            self.javascript_source,
        )
        if match is None:
            return None
        start = match.end() - 1
        depth = 0
        quote = ""
        escaped = False
        for index in range(start, len(self.javascript_source)):
            character = self.javascript_source[index]
            if escaped:
                escaped = False
            elif quote:
                if character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
            elif character in "\"'`":
                quote = character
            elif character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    return self.javascript_source[start:index + 1]
        return self.javascript_source[start:]

    def requests(self, codex_tool_field: CodexToolField) -> list[ToolRequest] | None:
        name = codex_tool_field
        if self.document is None:
            return None
        if name is CodexToolField.SEARCH_QUERY: return self.document.search_query
        if name is CodexToolField.IMAGE_QUERY: return self.document.image_query
        if name is CodexToolField.WEATHER: return self.document.weather
        if name is CodexToolField.FINANCE: return self.document.finance
        if name is CodexToolField.SPORTS: return self.document.sports
        if name is CodexToolField.OPEN: return self.document.open
        if name is CodexToolField.CLICK: return self.document.click
        if name is CodexToolField.FIND: return self.document.find
        if name is CodexToolField.SCREENSHOT: return self.document.screenshot
        return None

    def has(self, codex_tool_field: CodexToolField) -> bool:
        name = codex_tool_field
        if self.document is not None:
            if name is CodexToolField.QUERY: return self.document.query is not None
            if name is CodexToolField.URL: return self.document.url is not None
            if name is CodexToolField.PATH: return self.document.path is not None
            if name is CodexToolField.FILE_PATH: return self.document.file_path is not None
            if name is CodexToolField.URI: return self.document.uri is not None
            return self.requests(name) is not None
        return any(field.name == name.value for field in self.javascript_strings) or re.search(
            rf'''(?:^|[{{,])\s*["']?{re.escape(name.value)}["']?\s*:''',
            self.javascript_source,
        ) is not None

    def string(self, codex_tool_field: CodexToolField) -> str | None:
        name = codex_tool_field
        if self.document is not None:
            if name is CodexToolField.QUERY: return self.document.query
            if name is CodexToolField.URL: return self.document.url
            if name is CodexToolField.PATH: return self.document.path
            if name is CodexToolField.FILE_PATH: return self.document.file_path
            if name is CodexToolField.URI: return self.document.uri
            requests = self.requests(name)
            if requests:
                request = requests[0]
                return request.query or request.q or request.url or request.reference
        direct = next(
            (field.value for field in self.javascript_strings if field.name == name.value),
            None,
        )
        if direct is not None:
            return direct
        request_array = self._javascript_array(name)
        request_value = _JS_REQUEST_VALUE.search(request_array or "")
        if request_value is None:
            return None
        try:
            return str(ast.literal_eval(request_value.group(1)))
        except (SyntaxError, ValueError):
            return None


def _tool_fields(arguments: str | None) -> ToolFields:
    """A Codex non-shell tool call's arguments as fields.

    This is the CALL's own argument blob for a tool this codebase does not
    fully model (a web search, an image read) — deliberately read
    best-effort rather than through a declared, `extra="forbid"` shape: only
    one or two of its fields are ever consulted below, by NAME, and a vendor
    field this reads past is not drift worth failing translation over. Two
    spellings arrive: JSON text, and a JavaScript object literal with
    unquoted keys. The latter is read for its STRING fields only — which is
    every field anything below wants — rather than interpreted.
    """
    try:
        parsed = CodexToolArguments.model_validate_json(
            arguments or CodexToolArguments().model_dump_json()
        )
    except ValidationError:
        parsed = None
    strings = tuple(
        ToolStringField(match.group(1), match.group(2).encode().decode("unicode_escape"))
        for match in _JS_STRING_FIELD.finditer(arguments or "")
    )
    return ToolFields(parsed, strings, arguments or "")


def _search_query(arguments: str | None) -> Content:
    """What was searched for. The whole argument blob is the fallback: a query
    nobody can read is still a better raw event than an empty one."""
    fields = _tool_fields(arguments)
    for name in _SEARCH_QUERY_FIELDS:
        value = fields.string(name)
        if value:
            return content(value)
    return content(arguments)


def _web_url(arguments: str | None) -> str | None:
    """The address a fetch was for, when the call names one. Codex's `open` is
    often an index into a previous search's results rather than an address, so
    only something that reads as one counts."""
    fields = _tool_fields(arguments)
    for name in CodexToolField:
        value = fields.string(name)
        if value and value.startswith(("http://", "https://")):
            return value
    return None


def _tool_path(arguments: str | None) -> str:
    fields = _tool_fields(arguments)
    for name in (CodexToolField.PATH, CodexToolField.FILE_PATH, CodexToolField.URI):
        value = fields.string(name)
        if value:
            parsed = urlparse(value)
            if parsed.scheme == "file":
                return unquote(parsed.path)
            return value
    return _node_read_path(arguments)


def _node_read_path(arguments: str | None) -> str:
    source = arguments or ""
    cwd_match = _NODE_READ_CWD_SUFFIX.search(source)
    match = cwd_match or _NODE_READ_TEMPLATE_EXPRESSION.search(source) or _NODE_READ_FILE.search(source)
    if match is None:
        return ""
    try:
        path = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return ""
    found = str(path)
    return found.lstrip("/") if cwd_match is not None else found


_REPORTED_PROCESS_ID = re.compile(
    r"(?:session(?:_id)?\s*[:=]?\s*)?(\d+)"
)
_SKILL_DIRECTORY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _read_skill_name(command: str) -> str | None:
    """Return the name from one direct read of a Codex skill file."""
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if len(words) != 2 or words[0] != "cat":
        return None
    skill_parts = os.path.normpath(words[1]).split(os.sep)
    if len(skill_parts) < 4 or skill_parts[-4:-2] != [".agents", "skills"]:
        return None
    name, filename = skill_parts[-2:]
    if filename != "SKILL.md":
        return None
    return name if _SKILL_DIRECTORY_NAME.fullmatch(name) else None


class CodexCanonicalTranslator(HarnessTranslator):
    def __init__(self, rewind_continuity: RewindContinuity | None = None) -> None:
        self._collaboration_calls: dict[tuple[str, str], tuple[str, CollaborationArguments]] = {}
        self._process_shells: dict[tuple[str, str], ShellId] = {}
        self._continuation_shells: dict[tuple[str, str], ShellId] = {}
        self._finished_shells: set[tuple[str, ShellId]] = set()
        self._finished_shell_outcomes: set[tuple[str, ShellId, Outcome]] = set()
        self._finished_skills: set[tuple[str, SkillId]] = set()
        # Announced background once. An exec that outlived its yield is reported
        # again by every continuation poll, and the fact is about the command,
        # not about the poll that observed it.
        self._backgrounded_shells: set[tuple[str, ShellId]] = set()
        self._semantic_tool_calls: set[tuple[str, str]] = set()
        self._call_records: dict[
            tuple[str, str], ExecRecord | ToolRecord | AskRecord | None
        ] = {}
        self._mcp_tool_outcomes: dict[tuple[str, str], Outcome] = {}
        self._finished_tool_calls: set[tuple[str, str]] = set()
        self._plan_tasks: dict[tuple[str, str], tuple[TaskChanged, ...]] = {}
        self._goals: dict[str, GoalChanged] = {}
        self._working_directories: dict[str, str] = {}
        # Codex's `turn_aborted` payload may omit `turn_id`.  One rollout file
        # carries at most one active turn, so retain the task-start identity by
        # source and use it to close that same turn when the terminal interrupt
        # record is sparse.
        self._active_turns: dict[str, CodexTurnId] = {}
        self._sources_by_session: dict[SessionId, set[str]] = {}
        self._selections = SelectionSemantics()
        self._rewind_continuity = rewind_continuity or RewindContinuity()

    def _continued_from(
        self,
        raw_event: RawEvent,
        declared_from: str | None,
    ) -> SessionId | None:
        return self._rewind_continuity.resolve(
            raw_event.session_id,
            raw_event.terminal_window_id,
            declared_from=(
                session_id_from_codex(CodexSessionId(declared_from))
                if declared_from is not None
                else None
            ),
        )

    @staticmethod
    def _source_key(raw_event: RawEvent) -> str:
        return os.path.realpath(raw_event.source_name)

    def release_session(self, session_id: SessionId) -> None:
        """Release all transient correlation for one finished session."""
        sources = self._sources_by_session.pop(session_id, set())
        _drop_source_keys(self._collaboration_calls, sources)
        _drop_source_keys(self._process_shells, sources)
        _drop_source_keys(self._continuation_shells, sources)
        self._finished_shells = {
            key for key in self._finished_shells if key[0] not in sources
        }
        self._finished_shell_outcomes = {
            key for key in self._finished_shell_outcomes if key[0] not in sources
        }
        self._finished_skills = {
            key for key in self._finished_skills if key[0] not in sources
        }
        self._backgrounded_shells = {
            key for key in self._backgrounded_shells if key[0] not in sources
        }
        self._semantic_tool_calls = {
            key for key in self._semantic_tool_calls if key[0] not in sources
        }
        _drop_source_keys(self._call_records, sources)
        _drop_source_keys(self._mcp_tool_outcomes, sources)
        self._finished_tool_calls = {
            key for key in self._finished_tool_calls if key[0] not in sources
        }
        _drop_source_keys(self._plan_tasks, sources)
        for source in sources:
            self._active_turns.pop(source, None)
        self._goals.pop(str(session_id), None)
        self._working_directories.pop(str(session_id), None)
        self._selections.release_session(session_id)
        self._rewind_continuity.release(session_id)

    def _only_pending_exec_shell(self, source_key: str) -> ShellId | None:
        """The shell belonging to a fast CommandExecution item.

        Current Codex emits the authoritative item (with its real exit code)
        before the wrapper output. The item names a process id but not the
        wrapper call id; when exactly one exec is awaiting its result, that
        ordering is the correlation. Ambiguity stays uninterpreted rather than
        attaching an outcome to the wrong command.
        """
        candidates = [
            shell_id_from_codex_call(CodexCallId(call_id))
            for (known_source, call_id), record in self._call_records.items()
            if known_source == source_key
            and isinstance(record, ExecRecord)
            and _read_skill_name(record.cmd) is None
            and (source_key, shell_id_from_codex_call(CodexCallId(call_id)))
                not in self._finished_shells
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _pending_exec_shell_for_command(
        self,
        source_key: str,
        native_command: tuple[str, ...],
    ) -> ShellId | None:
        """Match a completed native process to its wrapper command.

        Current Codex does not put the wrapper call id on a CommandExecution
        item. It does put the exact shell command there. This identity remains
        usable when a JavaScript cell starts several commands in parallel,
        where the old "only pending command" fallback is ambiguous.
        """
        if not native_command:
            return None
        command_texts = {" ".join(native_command)}
        if len(native_command) >= 3 and native_command[-2] in ("-c", "-lc"):
            command_texts.add(native_command[-1])
        candidates = [
            shell_id_from_codex_call(CodexCallId(call_id))
            for (known_source, call_id), record in self._call_records.items()
            if known_source == source_key
            and isinstance(record, ExecRecord)
            and _read_skill_name(record.cmd) is None
            and record.cmd in command_texts
            and (
                source_key,
                shell_id_from_codex_call(CodexCallId(call_id)),
            ) not in self._finished_shells
        ]
        # Equal commands have equal meaning here. Native completions arrive in
        # start order, so the oldest open wrapper is the stable owner.
        return candidates[0] if candidates else None

    def _process_shell(
        self,
        raw_event: RawEvent,
        process_id: CodexShellId,
    ) -> ShellId | None:
        """Resolve a Codex process after the application restarts.

        A yielded exec result joins the wrapper call id to the native process
        id. This link is in the rollout, but the fast path also keeps it in
        memory. Search the earlier rollout when new translator memory does not
        have it. A recovered link also proves that the shell was backgrounded,
        so its later completion must close the running-work set.
        """
        source_key = self._source_key(raw_event)
        key = source_key, process_id
        remembered = self._process_shells.get(key)
        if remembered is not None:
            return remembered
        try:
            end_position = int(raw_event.source_position)
        except ValueError:
            return None
        # False: the typed result carried the process id. True: the wrapper
        # printed a process reference, which is valid only when the call says
        # that it can print `r.session_id`.
        result_calls: dict[CodexCallId, bool] = {}
        try:
            with open(source_key, "rb") as source:
                while end_position > 0:
                    start_position = max(0, end_position - 65_536)
                    source.seek(start_position)
                    chunk = source.read(end_position - start_position)
                    for line in reversed(chunk.splitlines()):
                        try:
                            record = rollout.parse_line(line.decode())
                        except (UnicodeDecodeError, ValidationError):
                            continue
                        if isinstance(record, ExecResultRecord):
                            if record.running and record.process_id == process_id:
                                result_calls[record.call_id] = False
                                continue
                            reported = _REPORTED_PROCESS_ID.fullmatch(record.output.strip())
                            if reported is not None and reported.group(1) == process_id:
                                result_calls[record.call_id] = True
                                continue
                        if isinstance(record, ExecRecord) and record.call_id in result_calls:
                            if result_calls[record.call_id] and not record.reports_session_id:
                                continue
                            shell_id = shell_id_from_codex_call(record.call_id)
                            self._call_records[(source_key, record.call_id)] = record
                            self._process_shells[key] = shell_id
                            self._backgrounded_shells.add((source_key, shell_id))
                            return shell_id
                        if isinstance(record, ExecResultRecord):
                            continue
                    end_position = start_position
        except OSError:
            return None
        return None

    @staticmethod
    def _collaboration_call_from_line(
        line: bytes, call_id: CodexCallId,
    ) -> tuple[str, CollaborationArguments] | None:
        try:
            record = rollout.parse_line(line.decode())
        except (UnicodeDecodeError, ValidationError):
            return None
        if not isinstance(record, CollaborationCallRecord) or record.call_id != call_id:
            return None
        return record.name, record.args

    def _collaboration_call(
        self,
        raw_event: RawEvent,
        call_id: CodexCallId,
    ) -> tuple[str, CollaborationArguments] | None:
        """Resolve the preceding call without scanning historical rollout data."""
        source_path = os.path.realpath(raw_event.source_name)
        key = (source_path, call_id)
        remembered = self._collaboration_calls.get(key)
        if remembered is not None:
            return remembered
        try:
            end_position = int(raw_event.source_position)
        except ValueError:
            return None
        # OSError only: a `pydantic.ValidationError` raised while validating a
        # recovered call's arguments (_collaboration_call_from_document) must
        # propagate as `translation_failed`, not be read as "no call found" —
        # the two are different facts, and this used to conflate them because
        # ValidationError IS a ValueError.
        try:
            with open(source_path, "rb") as source:
                while end_position > 0:
                    start_position = max(0, end_position - 65_536)
                    source.seek(start_position)
                    chunk = source.read(end_position - start_position)
                    for line in reversed(chunk.splitlines()):
                        call = self._collaboration_call_from_line(line, call_id)
                        if call is not None:
                            self._collaboration_calls[key] = call
                            return call
                    end_position = start_position
        except OSError:
            return None
        return None

    @staticmethod
    def _call_from_line(
        line: bytes, call_id: CodexCallId,
    ) -> ExecRecord | ToolRecord | AskRecord | Literal[False] | None:
        """The parsed call this output belongs to.

        None means this is not the call being sought; False means it is the
        call, but its grammar is deliberately nonsemantic/unsupported. A record
        rather than a bare yes: what the output MEANS is the call's kind and
        arguments — a command's exit, or a search's results — and only the call
        carries them.
        """
        try:
            record = rollout.parse_line(line.decode())
        except (UnicodeDecodeError, ValidationError):
            return None
        if not isinstance(record, (ExecRecord, ToolRecord, AskRecord)) or record.call_id != call_id:
            return None
        return record

    def _call_record(
        self,
        raw_event: RawEvent,
        call_id: CodexCallId,
    ) -> ExecRecord | ToolRecord | AskRecord | None:
        """Pair an output with the call that opened it.

        The in-memory answer handles the normal adjacent call/output pair. The
        bounded backwards scan handles a daemon restart between those records,
        when the canonical start is durable but translator memory is fresh.
        """
        source_path = self._source_key(raw_event)
        key = (source_path, call_id)
        if key in self._call_records:
            return self._call_records[key]
        try:
            end_position = int(raw_event.source_position)
        except ValueError:
            end_position = 0
        # OSError only — see _collaboration_call: a ValidationError while
        # re-parsing the recovered call must propagate, not read as "no call".
        try:
            with open(source_path, "rb") as source:
                while end_position > 0:
                    start_position = max(0, end_position - 65_536)
                    source.seek(start_position)
                    chunk = source.read(end_position - start_position)
                    for line in reversed(chunk.splitlines()):
                        opened = self._call_from_line(line, call_id)
                        if opened is not None:
                            found = (
                                opened
                                if isinstance(opened, (ExecRecord, ToolRecord, AskRecord))
                                else None
                            )
                            self._call_records[key] = found
                            return found
                    end_position = start_position
        except OSError:
            pass
        self._call_records[key] = None
        return None

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        self._sources_by_session.setdefault(raw_event.session_id, set()).add(
            self._source_key(raw_event)
        )
        try:
            return self._translate(raw_event)
        except UnknownRawEvent as unknown:
            return TranslationResult((), RecordedTranslationDecision.IGNORED_UNKNOWN, unknown.reason)

    def _translate(self, raw_event: RawEvent) -> TranslationResult:
        try:
            raw_text = raw_event.payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise TranslationError("malformed Codex rollout record", context=raw_event.source_position) from error

        if raw_event.source_type == "hook":
            if raw_event.parent_actor_id is not None:
                return TranslationResult(
                    (),
                    RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                    "subagent delivery; its activity arrives through the lead's rollout",
                )
            try:
                hook = CodexHookPayload.model_validate_json(raw_text)
            except ValidationError as error:
                raise TranslationError(
                    "malformed Codex hook delivery", context=raw_event.source_position
                ) from error
            events = self._translate_hook(raw_event, hook)
            if events:
                return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "hook carries no unique canonical activity"
            )

        if raw_event.source_type == TITLE_SOURCE_TYPE:
            try:
                title_observation = decode_document(
                    NativeTitleObservation,
                    raw_event.payload,
                )
            except StoredDocumentError as error:
                raise TranslationError(
                    "malformed Codex title observation",
                    context=raw_event.source_position,
                ) from error
            changed = SessionTitleChanged(
                title_observation.title,
                TitleOrigin(title_observation.origin),
            )
            return TranslationResult(
                (event(
                    raw_event,
                    "session",
                    str(raw_event.session_id),
                    f"title:{title_observation.origin}:{raw_event.source_position}",
                    changed,
                ),),
                RecordedTranslationDecision.TRANSLATED,
            )

        if raw_event.source_type in ("child_replay", "sidecar_replay"):
            return TranslationResult(
                (),
                RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "parent history replayed in child rollout",
            )

        try:
            header = RolloutHeader.model_validate_json(raw_text)
        except ValidationError as error:
            raise TranslationError(
                "malformed Codex rollout record", context=raw_event.source_position
            ) from error
        if header.type == "session_meta":
            if raw_event.source_position != "0":
                return TranslationResult(
                    (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "replayed session metadata"
                )
            metadata = RolloutDocument[SessionMetaPayload].model_validate_json(raw_text).payload
            if raw_event.parent_actor_id is not None:
                role: ActorRole = (
                    ActorRole.SIDECAR if raw_event.source_type == "sidecar_rollout" else ActorRole.CHILD
                )
                metadata_source = metadata.source if isinstance(metadata.source, SessionMetaSource) else None
                spawn = (
                    metadata_source.subagent.thread_spawn
                    if metadata_source and metadata_source.subagent else None
                )
                actor_name = ((spawn.agent_path if spawn else None) or "").rsplit("/", 1)[-1]
                actor_name = actor_name.replace("_", " ").strip() or "codex"
                actor_started = event(
                    raw_event,
                    "actor",
                    str(raw_event.actor_id),
                    "started",
                    ActorStarted(actor_name, role),
                    occurred_at=timestamp(metadata.timestamp),
                )
                return TranslationResult((actor_started,), RecordedTranslationDecision.TRANSLATED)
            return TranslationResult(
                tuple(self._session_started_events(
                    raw_event,
                    metadata.cwd or "",
                    os.path.realpath(raw_event.source_name),
                    continued_from=self._continued_from(
                        raw_event,
                        str(metadata.forked_from_id)
                        if metadata.forked_from_id is not None
                        else None,
                    ),
                )),
                RecordedTranslationDecision.TRANSLATED,
            )

        record = rollout.parse_line(raw_text)
        if record is None:
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_UNKNOWN, f"unhandled Codex record {header.type!r}"
            )
        if isinstance(record, BadRecord):
            raise TranslationError("malformed Codex rollout record", context=raw_event.source_position)

        observation = RolloutObservation.model_validate_json(raw_text)
        events = self._translate_record(raw_event, observation, record)
        if not events:
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, f"nonsemantic Codex record {record.kind!r}"
            )
        return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)

    def _translate_hook(
        self,
        raw_event: RawEvent,
        codex_hook_payload: CodexHookPayload,
    ) -> list[CanonicalEvent[EventPayload]]:
        hook = codex_hook_payload
        hook_name = hook.hook_event_name or ""
        native_identity = hook.hook_event_id or hook.uuid or raw_event.source_position
        run_started = self._hook_run_started_events(raw_event, hook)
        if hook_name == "SessionStart":
            return run_started
        if hook_name == "PreCompact":
            payload: EventPayload = CompactionStarted(hook.before_tokens)
            return [*run_started, event(raw_event, "compaction", native_identity, "started", payload)]
        if hook_name == "PostCompact":
            payload = CompactionFinished(hook.before_tokens, hook.after_tokens)
            return [*run_started, event(raw_event, "compaction", native_identity, "finished", payload)]
        return run_started

    def _hook_run_started_events(
        self,
        raw_event: RawEvent,
        codex_hook_payload: CodexHookPayload,
    ) -> list[CanonicalEvent[EventPayload]]:
        """Let any lead hook confirm a run whose SessionStart hook was missed.

        All hooks from one terminal window produce the same canonical start
        identities. Normal deliveries therefore converge on the SessionStart
        facts. A later hook repairs the run only when those facts are absent.
        """
        if raw_event.parent_actor_id is not None:
            return []
        if codex_hook_payload.hook_event_name != "SessionStart" and raw_event.terminal_window_id is None:
            return []
        path = codex_hook_payload.transcript_path or ""
        if not lead_rollout(path):
            # A subagent thread announces no session of its own.
            return []
        metadata = session_metadata(path)
        return self._session_started_events(
            raw_event,
            codex_hook_payload.cwd or "",
            os.path.realpath(path),
            continued_from=self._continued_from(
                raw_event,
                str(metadata.forked_from_id)
                if metadata is not None and metadata.forked_from_id is not None
                else None,
            ),
        )

    def _session_started_events(
        self,
        raw_event: RawEvent,
        working_directory: str,
        source_reference: str,
        *,
        occurred_at: float | None = None,
        continued_from: SessionId | None = None,
    ) -> list[CanonicalEvent[EventPayload]]:
        self._working_directories[str(raw_event.session_id)] = working_directory
        session_started = SessionStarted(
            working_directory=working_directory,
            source_reference=source_reference,
            resumed_from=None,
            title=None,
            model=None,
            effort=None,
            account=None,
            continued_from=continued_from,
        )
        actor_started = ActorStarted("codex", ActorRole.LEAD)
        if raw_event.source_type == "hook" and raw_event.terminal_window_id is not None:
            return list(
                session_run_started_events(
                    raw_event,
                    session_started,
                    actor_started,
                    occurred_at=occurred_at,
                )
            )
        return [
            event(
                raw_event,
                "session",
                str(raw_event.session_id),
                "started",
                session_started,
                occurred_at=occurred_at,
            ),
            event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                actor_started,
                occurred_at=occurred_at,
            ),
        ]

    # Record kinds that carry a `call_id`/`turn`/`at` field of the same NAME —
    # narrowed here once so the branches below read `record.call_id` etc.
    # directly rather than re-deriving the tuple per branch.
    _CALL_ID_RECORDS = (
        ExecRecord, ExecResultRecord, StdinRecord, ToolRecord, PatchCallRecord, AskRecord,
        ActorActivityRecord, CollaborationCallRecord, TaskListRecord, GoalToolRecord,
        ToolBatchRecord,
    )
    _AT_RECORDS = (TaskStartedRecord, TaskCompleteRecord)

    def _translate_record(
        self,
        raw_event: RawEvent,
        rollout_observation: RolloutObservation,
        record: RolloutRecord,
    ) -> list[CanonicalEvent[EventPayload]]:
        observation = rollout_observation
        native_payload = observation.payload
        record_call_id = record.call_id if isinstance(record, self._CALL_ID_RECORDS) else None
        native_identity = str(
            record_call_id
            or (native_payload.id if native_payload else None)
            or (native_payload.item_id if native_payload else None)
            or raw_event.source_position
        )
        occurred_at = timestamp(observation.timestamp)
        if occurred_at is None:
            record_at = record.at if isinstance(record, self._AT_RECORDS) else None
            occurred_at = timestamp(record_at)

        if isinstance(record, TaskStartedRecord):
            source_key = self._source_key(raw_event)
            native_turn_id = CodexTurnId(
                record.turn or f"{raw_event.session_id}:{native_identity}"
            )
            self._active_turns[source_key] = native_turn_id
            turn_id = turn_id_from_codex(native_turn_id)
            events = [event(raw_event, "turn", str(turn_id), "started", TurnStarted(None), turn_id, occurred_at)]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                metadata = session_metadata(raw_event.source_name)
                metadata_source = (
                    metadata.source if metadata and isinstance(metadata.source, SessionMetaSource) else None
                )
                spawn = (
                    metadata_source.subagent.thread_spawn
                    if metadata_source and metadata_source.subagent else None
                )
                actor_path = (spawn.agent_path if spawn else None) or ""
                actor_name = actor_path.rsplit("/", 1)[-1].replace("_", " ").strip()
                assignment_id = assignment_id_from_codex_turn(native_turn_id)
                # No prompt: the task payload is encrypted_content in the child
                # rollout, unreadable by design (rollout.subagent_brief).
                events.append(event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "started",
                    ActorAssignmentStarted(
                        assignment_id,
                        content(actor_name or "actor assignment"),
                        actor_name=actor_name or None,
                    ),
                    turn_id,
                    occurred_at,
                ))
            return events
        if isinstance(record, TaskCompleteRecord):
            source_key = self._source_key(raw_event)
            native_turn_id = CodexTurnId(
                record.turn
                or self._active_turns.get(source_key)
                or f"{raw_event.session_id}:{native_identity}"
            )
            if self._active_turns.get(source_key) == native_turn_id:
                self._active_turns.pop(source_key, None)
            turn_id = turn_id_from_codex(native_turn_id)
            events = [
                event(
                    raw_event,
                    "turn",
                    str(turn_id),
                    "finished",
                    TurnFinished(None, Outcome.SUCCEEDED),
                    turn_id,
                    occurred_at,
                )
            ]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                assignment_id = assignment_id_from_codex_turn(native_turn_id)
                result = content(record.last, markdown=True) if record.last else None
                events.append(
                    event(
                        raw_event,
                        "actor_assignment",
                        str(assignment_id),
                        "finished",
                        ActorAssignmentFinished(assignment_id, Outcome.SUCCEEDED, result, None),
                        turn_id,
                        occurred_at,
                    )
                )
            return events
        if isinstance(record, TurnAbortedRecord):
            source_key = self._source_key(raw_event)
            native_turn_id = CodexTurnId(
                record.turn
                or (str(native_payload.turn_id or "") if native_payload else "")
                or self._active_turns.get(source_key)
                or native_identity
            )
            if self._active_turns.get(source_key) == native_turn_id:
                self._active_turns.pop(source_key, None)
            turn_id = turn_id_from_codex(native_turn_id)
            events = [event(raw_event, "turn", str(turn_id), "aborted", TurnAborted(None), turn_id, occurred_at)]
            interrupted_shells = [
                shell_id_from_codex_call(CodexCallId(call_id))
                for (known_source, call_id), call in self._call_records.items()
                if known_source == source_key
                and isinstance(call, ExecRecord)
                and _read_skill_name(call.cmd) is None
                and call.turn == native_turn_id
                and (source_key, shell_id_from_codex_call(CodexCallId(call_id)))
                    not in self._finished_shells
            ]
            for shell_id in interrupted_shells:
                if (source_key, shell_id) in self._backgrounded_shells:
                    continue
                self._finished_shells.add((source_key, shell_id))
                self._finished_shell_outcomes.add(
                    (source_key, shell_id, Outcome.CANCELLED)
                )
                events.append(event(
                    raw_event,
                    "shell",
                    str(shell_id),
                    "finished",
                    ShellFinished(shell_id, Outcome.CANCELLED, None, None),
                    turn_id,
                    occurred_at,
                ))
            interrupted_skills = [
                skill_id_from_codex(CodexSkillId(call_id))
                for (known_source, call_id), call in self._call_records.items()
                if known_source == source_key
                and isinstance(call, ExecRecord)
                and _read_skill_name(call.cmd) is not None
                and call.turn == native_turn_id
                and (
                    source_key,
                    skill_id_from_codex(CodexSkillId(call_id)),
                ) not in self._finished_skills
            ]
            for skill_id in interrupted_skills:
                self._finished_skills.add((source_key, skill_id))
                events.append(event(
                    raw_event,
                    "skill",
                    str(skill_id),
                    "finished",
                    SkillFinished(skill_id, Outcome.CANCELLED, None),
                    turn_id,
                    occurred_at,
                ))
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                assignment_id = assignment_id_from_codex_turn(native_turn_id)
                events.append(event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "finished",
                    ActorAssignmentFinished(assignment_id, Outcome.CANCELLED, None, "interrupted"),
                    turn_id,
                    occurred_at,
                ))
            return events
        if isinstance(record, (PromptRecord, MessageRecord, ChatRecord)):
            # Declared, not inferred: both are read off native JSON and land in
            # a payload that accepts a closed set. The normalisation below
            # already rejects an unknown role — the annotation is what makes
            # that rejection checkable instead of incidental.
            role: MessageRole = MessageRole.USER if isinstance(record, PromptRecord) else MessageRole.ASSISTANT
            if isinstance(record, ChatRecord):
                if record.role == "user":
                    role = MessageRole.USER
                elif record.role == "assistant":
                    role = MessageRole.ASSISTANT
                elif record.role == "system":
                    role = MessageRole.SYSTEM
            synthetic = record.synthetic if isinstance(record, ChatRecord) else False
            if synthetic:
                role = MessageRole.SYSTEM
            phase: MessagePhase | None = MessagePhase.SYNTHETIC if synthetic else None
            if isinstance(record, PromptRecord) and not synthetic:
                phase = MessagePhase.PROMPT
            elif role == MessageRole.USER and phase is None:
                phase = MessagePhase.PROMPT
            elif isinstance(record, (MessageRecord, ChatRecord)) and record.phase == PHASE_FINAL:
                phase = MessagePhase.END_TURN
            elif role == MessageRole.ASSISTANT:
                phase = MessagePhase.INTERMEDIATE
            message_id = message_id_from_codex(CodexMessageId(native_identity))
            message_content = content(record.text, markdown=role == "assistant")
            payload: EventPayload = MessageCreated(message_id, role, message_content, phase, None)
            # A message need not belong to a turn; the bindings above in this
            # same function always do, so the name has to admit None here.
            message_turn_id: TurnId | None = (
                turn_id_from_codex(CodexTurnId(record.turn))
                if isinstance(record, ChatRecord) and record.turn
                else None
            )
            return [event(
                raw_event,
                "message",
                native_identity,
                "created",
                payload,
                message_turn_id,
                occurred_at,
            )]
        if isinstance(record, SkillRecord):
            skill_id = skill_id_from_codex(CodexSkillId(native_identity))
            skill_turn_id = (
                turn_id_from_codex(CodexTurnId(record.turn))
                if record.turn
                else None
            )
            return [
                event(
                    raw_event,
                    "skill",
                    native_identity,
                    "started",
                    SkillStarted(skill_id, record.name, None),
                    skill_turn_id,
                    occurred_at,
                ),
                event(
                    raw_event,
                    "skill",
                    native_identity,
                    "finished",
                    SkillFinished(skill_id, Outcome.SUCCEEDED, None),
                    skill_turn_id,
                    occurred_at,
                ),
            ]
        if isinstance(record, (ReasoningRecord, ThinkRecord)):
            payload = ReasoningCreated(
                reasoning_id_from_codex(CodexReasoningId(native_identity)),
                content(record.text, markdown=True),
            )
            return [event(raw_event, "reasoning", native_identity, "created", payload, occurred_at=occurred_at)]
        if isinstance(record, CollaborationCallRecord):
            call_id = CodexCallId(record.call_id or "")
            self._collaboration_calls[(os.path.realpath(raw_event.source_name), call_id)] = (
                record.name, record.args,
            )
            return []
        if isinstance(record, ActorActivityRecord):
            call_id = CodexCallId(record.call_id or "")
            call = self._collaboration_call(raw_event, call_id)
            if call is None:
                raise TranslationError(f"Codex actor activity has no collaboration call: {call_id or '<missing>'}")
            call_name, call_arguments = call
            activity = record.activity
            expected_call = ACTIVITY_CALLS.get(activity)
            if expected_call is not None and call_name != expected_call:
                raise TranslationError(f"Codex actor activity {activity!r} came from {call_name!r}")
            if activity == "interacted":
                if call_name == "followup_task":
                    return []
                if call_name != "send_message" or not isinstance(call_arguments, SendMessageArguments):
                    raise TranslationError(f"Codex actor interaction came from {call_name!r}")
                message_id = message_id_from_codex_call(call_id)
                # The text is in the call's own arguments, which used to be
                # fetched and dropped: an actor-to-actor message with no message
                # is a fact about nothing.
                spoken = call_arguments.message or call_arguments.content or ""
                payload = MessageCreated(
                    message_id,
                    MessageRole.ASSISTANT,
                    content(spoken, markdown=True),
                    MessagePhase.INTERMEDIATE,
                    None,
                    actor_id_from_codex(CodexActorId(record.actor_id)),
                )
                return [event(
                    raw_event,
                    "message",
                    str(message_id),
                    "created",
                    payload,
                    turn_id_from_codex(CodexTurnId(record.turn)) if record.turn else None,
                    occurred_at,
                )]
            if activity in ("started", "interrupted"):
                return []
            raise TranslationError(f"unknown Codex actor activity: {activity!r}")
        if isinstance(record, UnmappedToolRecord):
            raise UnknownRawEvent(f"unmapped Codex tool: {record.name or '<missing>'}")
        if isinstance(record, GoalRecord):
            native_state = record.status or ""
            # Typed so the table itself is checked: every value here has to be
            # a state GoalChanged accepts, and a typo in one of them used to
            # travel all the way into a stored fact.
            state = GOAL_STATES.get(native_state)
            if state is None:
                raise TranslationError(f"unknown Codex goal state: {native_state or '<missing>'}")
            objective = (record.objective or "").strip() or None
            if state != GoalState.CLEARED and objective is None:
                raise TranslationError("Codex goal has no objective")
            payload = GoalChanged(objective, state, (record.reason or "").strip() or None)
            self._goals[str(raw_event.session_id)] = payload
            return [event(
                raw_event,
                "goal",
                native_identity,
                "changed",
                payload,
                occurred_at=occurred_at,
            )]
        if isinstance(record, ToolBatchRecord):
            self._semantic_tool_calls.add((
                self._source_key(raw_event),
                CodexCallId(record.call_id or native_identity),
            ))
            batch_events: list[CanonicalEvent[EventPayload]] = []
            for action in record.actions:
                if isinstance(action, (CollaborationCallRecord, ExecRecord, StdinRecord)):
                    batch_events.extend(self._translate_record(
                        raw_event,
                        rollout_observation,
                        action,
                    ))
                    continue
                if isinstance(action, TaskListRecord):
                    batch_events.extend(self._translate_record(
                        raw_event,
                        rollout_observation,
                        action,
                    ))
                    continue
                if action.name == "get_goal":
                    continue
                if action.name == "create_goal":
                    objective = (action.objective or "").strip() or None
                    native_state = action.status or "active"
                elif action.name == "update_goal":
                    previous_goal = self._goals.get(str(raw_event.session_id))
                    objective = (
                        (action.objective or "").strip()
                        or (previous_goal.objective if previous_goal else None)
                    )
                    native_state = action.status or ""
                else:
                    raise TranslationError(
                        f"unknown Codex goal tool: {action.name or '<missing>'}"
                    )
                state = GOAL_STATES.get(native_state)
                if state is None:
                    raise TranslationError(
                        f"unknown Codex goal state: {native_state or '<missing>'}"
                    )
                if objective is None:
                    raise TranslationError("Codex goal has no objective")
                goal_changed = GoalChanged(
                    objective,
                    state,
                    (action.reason or "").strip() or None,
                )
                self._goals[str(raw_event.session_id)] = goal_changed
                batch_events.append(event(
                    raw_event,
                    "goal",
                    str(action.call_id),
                    "changed",
                    goal_changed,
                    occurred_at=occurred_at,
                ))
            return batch_events
        if isinstance(record, GoalToolRecord):
            call_id = CodexCallId(record.call_id or native_identity)
            self._semantic_tool_calls.add((self._source_key(raw_event), call_id))
            return []
        if isinstance(record, TaskListRecord):
            call_id = CodexCallId(record.call_id or native_identity)
            source_key = self._source_key(raw_event)
            self._semantic_tool_calls.add((source_key, call_id))
            plan_key = (str(raw_event.session_id), str(raw_event.actor_id))
            previous = self._plan_tasks.get(plan_key)
            current: list[TaskChanged] = []
            for task_index, plan_task in enumerate(record.tasks, start=1):
                subject = (plan_task.step or "").strip()
                if not subject:
                    raise TranslationError("Codex plan task has no step")
                task_state: TaskState
                if plan_task.status == "pending":
                    task_state = TaskState.PENDING
                elif plan_task.status == "in_progress":
                    task_state = TaskState.IN_PROGRESS
                elif plan_task.status == "completed":
                    task_state = TaskState.COMPLETED
                else:
                    raise TranslationError(f"unknown Codex plan task state: {plan_task.status!r}")
                task_id = task_id_from_codex(
                    CodexTaskId(f"{raw_event.actor_id}:plan:{task_index}")
                )
                current.append(TaskChanged(
                    task_id,
                    subject,
                    None,
                    task_state,
                    raw_event.actor_id,
                ))
            events = [event(
                raw_event,
                "task_list",
                str(raw_event.actor_id),
                f"changed:{call_id}",
                TaskListChanged(
                    task_list_id_from_codex(CodexTaskListId(str(raw_event.actor_id))),
                    tuple(task_changed.task_id for task_changed in current),
                ),
                occurred_at=occurred_at,
            )]
            for task_changed in current:
                task_id = task_changed.task_id
                if previous is not None and task_changed in previous:
                    continue
                events.append(event(
                    raw_event, "task", str(task_id), f"changed:{call_id}", task_changed,
                    occurred_at=occurred_at,
                ))
            self._plan_tasks[plan_key] = tuple(current)
            return events
        if isinstance(record, (ExecRecord, ToolRecord)):
            call_id = CodexCallId(record.call_id or native_identity)
            # Remembered whichever kind it is: the output that lands later is
            # only meaningful as this call's output (see `_call_record`).
            self._call_records[(self._source_key(raw_event), call_id)] = record
            if isinstance(record, ToolRecord):
                # A search, a fetch or a file read is one fact at result time —
                # its query and what came back of it are the same fact, and the
                # call alone is half of it. Validated here so an unmapped tool is
                # reported at the CALL, where the name is.
                _codex_tool(record.name, record.args)
                return []
            skill_name = _read_skill_name(record.cmd)
            if skill_name is not None:
                skill_id = skill_id_from_codex(CodexSkillId(call_id))
                skill_turn_id = (
                    turn_id_from_codex(CodexTurnId(record.turn))
                    if record.turn
                    else None
                )
                return [event(
                    raw_event,
                    "skill",
                    str(skill_id),
                    "started",
                    SkillStarted(skill_id, skill_name, None),
                    skill_turn_id,
                    occurred_at,
                )]
            shell_id = shell_id_from_codex_call(call_id)
            payload = ShellStarted(shell_id, content(record.cmd), ExecutionMode.FOREGROUND, None)
            return [event(raw_event, "shell", str(shell_id), "started", payload, occurred_at=occurred_at)]
        if isinstance(record, StdinRecord):
            process_id = record.process_id
            if not process_id:
                raise TranslationError("Codex write_stdin has no process session")
            source_key = self._source_key(raw_event)
            # A distinct name from the `shell_id` bound elsewhere in this
            # function: a lookup that can miss is not the same thing as an id
            # built from the record, and sharing one binding for both made the
            # non-optional uses depend on which branch ran.
            known_shell_id = self._process_shell(raw_event, process_id)
            if known_shell_id is None:
                raise TranslationError(f"Codex write_stdin references unknown process session: {process_id}")
            shell_id = known_shell_id
            call_id = CodexCallId(record.call_id or native_identity)
            self._continuation_shells[(source_key, call_id)] = shell_id
            text = record.text
            if not text:
                return []
            if (source_key, shell_id) in self._finished_shells:
                # The native command-completed event and the wrapper tool call
                # are separate streams. Under load the completed observation
                # can be ingested first even though this input call preceded it
                # in the model's turn. The settled shell is authoritative; a
                # late input observation cannot reopen or mutate it.
                return []
            payload = ShellInputProvided(shell_id, content(text), False)
            return [event(
                raw_event,
                "shell",
                str(shell_id),
                f"input:{call_id}",
                payload,
                occurred_at=occurred_at,
            )]
        if isinstance(record, ExecResultRecord):
            call_id = CodexCallId(record.call_id or native_identity)
            source_key = self._source_key(raw_event)
            if (source_key, call_id) in self._semantic_tool_calls:
                return []
            continued_shell = self._continuation_shells.get((source_key, call_id))
            if continued_shell is not None:
                if (source_key, continued_shell) in self._finished_shells:
                    return []
                output = record.output
                if not output:
                    return []
                ordinal = int(raw_event.source_position)
                payload = ShellProgressed(
                    continued_shell,
                    ordinal,
                    ProgressStream.OUTPUT,
                    content(output),
                    OutputMode.APPEND,
                )
                return [event(
                    raw_event,
                    "shell",
                    str(continued_shell),
                    f"progress:{ordinal}",
                    payload,
                    occurred_at=occurred_at,
                )]
            if self._collaboration_call(raw_event, call_id) is not None:
                return []
            call_record = self._call_record(raw_event, call_id)
            if call_record is None:
                return []
            if isinstance(call_record, ToolRecord):
                return self._tool_result(raw_event, call_id, call_record, record, occurred_at)
            if isinstance(call_record, AskRecord):
                return self._question_result(
                    raw_event,
                    call_record,
                    record,
                    occurred_at,
                )
            skill_name = _read_skill_name(call_record.cmd)
            if skill_name is not None:
                skill_id = skill_id_from_codex(CodexSkillId(call_id))
                if (source_key, skill_id) in self._finished_skills:
                    return []
                self._finished_skills.add((source_key, skill_id))
                process_exit_code = exit_code(record.exit)
                if record.interrupted:
                    skill_outcome = Outcome.CANCELLED
                elif process_exit_code not in (None, 0):
                    skill_outcome = Outcome.FAILED
                else:
                    skill_outcome = Outcome.SUCCEEDED
                skill_turn_id = (
                    turn_id_from_codex(CodexTurnId(call_record.turn))
                    if call_record.turn
                    else None
                )
                return [event(
                    raw_event,
                    "skill",
                    str(skill_id),
                    "finished",
                    SkillFinished(skill_id, skill_outcome, None),
                    skill_turn_id,
                    occurred_at,
                )]
            shell_id = shell_id_from_codex_call(call_id)
            if (source_key, shell_id) in self._finished_shells:
                settled_outcome = next(
                    (
                        outcome
                        for known_source, known_shell, outcome
                        in self._finished_shell_outcomes
                        if known_source == source_key and known_shell == shell_id
                    ),
                    None,
                )
                if (
                    settled_outcome is not None
                    and call_record.yield_ms is not None
                    and record.exit is None
                    and not record.output
                ):
                    # The native completion can precede an empty yielded
                    # wrapper. This closing fact is harmless when no background
                    # state exists and repairs an older false background fact
                    # when a versioned source replay finds one.
                    return [event(
                        raw_event,
                        "shell",
                        str(shell_id),
                        "settled_after_native_finish",
                        ShellOutputFinished(shell_id, settled_outcome),
                        occurred_at=occurred_at,
                    )]
                return []
            process_exit_code = exit_code(record.exit)
            reported_process_id = (
                _REPORTED_PROCESS_ID.fullmatch(record.output.strip())
                if call_record.reports_session_id
                else None
            )
            process_id = record.process_id or CodexShellId(
                reported_process_id.group(1) if reported_process_id is not None else ""
            )
            if process_id:
                self._process_shells[(source_key, process_id)] = shell_id
            if record.interrupted:
                if (source_key, shell_id) in self._backgrounded_shells:
                    return []
                self._finished_shells.add((source_key, shell_id))
                self._finished_shell_outcomes.add(
                    (source_key, shell_id, Outcome.CANCELLED)
                )
                return [event(
                    raw_event,
                    "shell",
                    str(shell_id),
                    "finished",
                    ShellFinished(shell_id, Outcome.CANCELLED, None, None),
                    occurred_at=occurred_at,
                )]
            yielded_without_identity = (
                call_record.yield_ms is not None
                and record.exit is None
                and not record.output
            )
            yielded_with_identity = (
                call_record.yield_ms is not None
                and record.exit is None
                and bool(process_id)
                and reported_process_id is not None
            )
            if record.running or yielded_without_identity or yielded_with_identity:
                # A BACKGROUND TERMINAL: the command outlived its yield budget, so
                # codex handed back a live session (its `session_id`, the cell id
                # `/ps` lists and `write_stdin` polls) with no exit code. Announced
                # as backgrounded — nothing here ever falsely finished it, but
                # without the fact it is not background WORK either, and the jobs
                # panel cannot list what is still running.
                running_events: list[CanonicalEvent[EventPayload]] = []
                if (source_key, shell_id) not in self._backgrounded_shells:
                    self._backgrounded_shells.add((source_key, shell_id))
                    running_events.append(event(
                        raw_event,
                        "shell",
                        str(shell_id),
                        "backgrounded",
                        ShellBackgrounded(shell_id),
                        occurred_at=occurred_at,
                    ))
                output = "" if yielded_with_identity else record.output
                if output:
                    ordinal = int(raw_event.source_position)
                    running_events.append(event(
                        raw_event,
                        "shell",
                        str(shell_id),
                        f"progress:{ordinal}",
                        ShellProgressed(shell_id, ordinal, ProgressStream.OUTPUT, content(output), OutputMode.APPEND),
                        occurred_at=occurred_at,
                    ))
                return running_events
            outcome: Outcome = Outcome.SUCCEEDED if process_exit_code in (None, 0) else Outcome.FAILED
            payload = ShellFinished(shell_id, outcome, content(record.output), process_exit_code)
            self._finished_shells.add((source_key, shell_id))
            self._finished_shell_outcomes.add((source_key, shell_id, outcome))
            finished_events = [
                event(
                    raw_event,
                    "shell",
                    str(shell_id),
                    "finished",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
            if (source_key, shell_id) in self._backgrounded_shells:
                self._backgrounded_shells.discard((source_key, shell_id))
                finished_events.append(event(
                    raw_event,
                    "shell",
                    str(shell_id),
                    "output_finished",
                    ShellOutputFinished(shell_id, outcome),
                    occurred_at=occurred_at,
                ))
            return finished_events
        if isinstance(record, CommandCompletedRecord):
            source_key = self._source_key(raw_event)
            process_id = record.process_id
            # Same reason as the write_stdin branch above: the lookup is
            # optional, the id every line after it uses is not.
            completed_shell_id = self._process_shell(raw_event, process_id)
            if completed_shell_id is None:
                completed_shell_id = self._pending_exec_shell_for_command(
                    source_key,
                    record.command,
                )
            if completed_shell_id is None:
                completed_shell_id = self._only_pending_exec_shell(source_key)
            if completed_shell_id is None:
                if not record.command:
                    # A late or partial completion cannot open a command when
                    # it does not identify the command that ran.
                    return []
                # Code-mode Codex can build a command dynamically or run one
                # exec_command call in a JavaScript loop. The wrapper cannot
                # declare those commands before execution. The native completed
                # item is a complete record, so it owns both lifecycle facts.
                completed_shell_id = shell_id_from_codex_call(
                    CodexCallId(record.item_id or native_identity)
                )
                self._process_shells[(source_key, process_id)] = completed_shell_id
                command = " ".join(record.command)
                if len(record.command) >= 3 and record.command[-2] in ("-c", "-lc"):
                    command = record.command[-1]
                started = event(
                    raw_event,
                    "shell",
                    str(completed_shell_id),
                    "started",
                    ShellStarted(
                        completed_shell_id,
                        content(command),
                        ExecutionMode.FOREGROUND,
                        None,
                    ),
                    occurred_at=occurred_at,
                )
            else:
                self._process_shells[(source_key, process_id)] = completed_shell_id
                started = None
            if (source_key, completed_shell_id) in self._finished_shells:
                return []
            shell_id = completed_shell_id
            process_exit_code = exit_code(record.exit)
            outcome = Outcome.SUCCEEDED if process_exit_code == 0 else Outcome.FAILED
            self._finished_shells.add((source_key, shell_id))
            self._finished_shell_outcomes.add((source_key, shell_id, outcome))
            payload = ShellFinished(shell_id, outcome, content(record.output), process_exit_code)
            finished_events = [event(
                raw_event,
                "shell",
                str(shell_id),
                "finished",
                payload,
                occurred_at=occurred_at,
            )]
            if started is not None:
                finished_events.insert(0, started)
            if (source_key, shell_id) in self._backgrounded_shells:
                self._backgrounded_shells.discard((source_key, shell_id))
                finished_events.append(event(
                    raw_event,
                    "shell",
                    str(shell_id),
                    "output_finished",
                    ShellOutputFinished(shell_id, outcome),
                    occurred_at=occurred_at,
                ))
            return finished_events
        if isinstance(record, McpToolCompletedRecord):
            source_key = self._source_key(raw_event)
            native_name = f"mcp__{record.server}__{record.tool}"
            # Codex exposes built-in resource operations without an MCP prefix
            # in the JavaScript wrapper. The native completion can attribute
            # the same call to an internal server such as `codex` or
            # `filesystem`, so both native spellings identify the pending call.
            known_names = {native_name, record.tool}
            candidates = [
                call_id
                for (known_source, call_id), call_record in self._call_records.items()
                if known_source == source_key
                and isinstance(call_record, ToolRecord)
                and call_record.name in known_names
                and (source_key, call_id) not in self._finished_tool_calls
            ]
            if len(candidates) != 1:
                raise TranslationError(
                    "Codex MCP completion does not identify exactly one pending "
                    f"{native_name} call"
                )
            if record.status == "failed":
                outcome = Outcome.FAILED
            elif record.status == "completed":
                outcome = Outcome.SUCCEEDED
            else:
                raise TranslationError(
                    f"unknown Codex MCP completion state: {record.status!r}"
                )
            self._mcp_tool_outcomes[(source_key, candidates[0])] = outcome
            return []
        if isinstance(record, SearchRecord):
            # Codex reports the query and nothing of what came back, so the
            # result is honestly absent rather than an empty string.
            payload = SearchPerformed("web_search", content(record.query), None, Outcome.SUCCEEDED)
            return [event(
                raw_event,
                "search",
                native_identity,
                "performed",
                payload,
                occurred_at=occurred_at,
            )]
        if isinstance(record, PatchRecord):
            outcome = outcome_of(record.success)
            events = []
            for file_order, file_record in enumerate(record.files):
                path = file_record.path
                payload = FileAccessed(
                    path=path,
                    action=FILE_ACTIONS.get(file_record.change or "", FileAction.UPDATED),
                    outcome=outcome,
                    previous_path=file_record.previous_path,
                    lines_added=file_record.added,
                    lines_removed=file_record.removed,
                    unified_diff=file_record.diff,
                    content=content(file_record.content) if file_record.content is not None else None,
                )
                events.append(
                    event(
                        raw_event,
                        "file",
                        f"{native_identity}:{file_order}:{path}",
                        "accessed",
                        payload,
                        occurred_at=occurred_at,
                    )
                )
            return events
        if isinstance(record, UsageRecord):
            usage = record.usage
            tokens = TokenUsage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_tokens=usage.cached_input_tokens or 0,
            )
            events = [
                event(
                    raw_event,
                    "usage",
                    native_identity,
                    "reported",
                    UsageReported(UsageScope.SESSION, str(raw_event.session_id), None, None, tokens, True, None),
                    occurred_at=occurred_at,
                )
            ]
            if record.last is not None and record.window:
                used_tokens = record.last.total_tokens or 0
                events.append(
                    event(
                        raw_event,
                        "context",
                        native_identity,
                        "reported",
                        ContextReported(used_tokens, record.window, None),
                        occurred_at=occurred_at,
                    )
                )
            return events
        if isinstance(record, (TurnContextRecord, SettingsRecord)):
            # Codex restates the whole turn context on every turn, so all but
            # the first restatement of one model is a change with nothing
            # changed; only a real transition survives `_selections`.
            events = []
            if record.model:
                changed = self._selections.model(
                    raw_event.session_id,
                    raw_event.actor_id,
                    model_reference(CodexModel(record.model)),
                    ModelChangeReason.REPORTED_BY_HARNESS,
                    record.model,
                )
                if changed is not None:
                    events.append(event(
                        raw_event,
                        "model",
                        native_identity,
                        "changed",
                        changed,
                        occurred_at=occurred_at,
                    ))
            if record.effort:
                chosen = self._selections.effort(
                    raw_event.session_id,
                    raw_event.actor_id,
                    record.effort,
                    EffortChangeReason.REPORTED_BY_HARNESS,
                )
                if chosen is not None:
                    events.append(event(
                        raw_event,
                        "effort",
                        native_identity,
                        "changed",
                        chosen,
                        occurred_at=occurred_at,
                    ))
            return events
        if isinstance(record, (CompactRecord, CompactBoundaryRecord)):
            # Codex reports the same compaction through PreCompact/PostCompact
            # hooks. Those hooks own the lifecycle and token counts. The
            # rollout records are display notices for native clients; mapping
            # them too creates a second `compaction.finished` feed row.
            return []
        if isinstance(record, AskRecord):
            call_id = CodexCallId(record.call_id or native_identity)
            self._call_records[(self._source_key(raw_event), call_id)] = record
            questions = tuple(
                AttentionPrompt(
                    prompt_id=question_id_from_codex(
                        CodexQuestionId(question.id or str(index))
                    ),
                    title=question.header or None,
                    prompt=question.question or "",
                    multiple=False,
                    choices=tuple(
                        AttentionChoice(option.label, option.description)
                        for option in question.options
                    ),
                )
                for index, question in enumerate(record.questions)
            )
            attention_id = attention_id_from_codex_call(
                call_id
            )
            payload = QuestionAsked(attention_id, questions)
            return [
                event(
                    raw_event,
                    "question",
                    str(attention_id),
                    "asked",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        if isinstance(record, PlanRecord):
            attention_id = attention_id_from_codex(
                CodexAttentionId(record.id or native_identity)
            )
            payload = PlanProposed(attention_id, content(record.text or "", markdown=True))
            return [
                event(
                    raw_event,
                    "plan",
                    str(attention_id),
                    "proposed",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        return []

    def _tool_result(
        self,
        raw_event: RawEvent,
        call_id: CodexCallId,
        tool_record: ToolRecord,
        exec_result_record: ExecResultRecord,
        occurred_at: float | None,
    ) -> list[CanonicalEvent[EventPayload]]:
        """One non-shell tool call and its result, as the single fact it is.

        Both halves are here: the call's name and arguments come from the record
        that opened it, the outcome and the text from the record that closed it.
        """
        kind, native_name = _codex_tool(tool_record.name, tool_record.args)
        if kind == CodexToolKind.IGNORED:
            return []
        arguments = tool_record.args
        output = exec_result_record.output
        source_key = self._source_key(raw_event)
        mcp_outcome = self._mcp_tool_outcomes.pop((source_key, call_id), None)
        native_failed = False
        if tool_record.name == "mcp__node_repl__js":
            try:
                node_result = NodeReplResultDocument.model_validate_json(output)
            except ValidationError:
                # The custom-exec wrapper can print the MCP text directly.
                # In that form, `output` already is the complete result.
                pass
            else:
                output = "\n".join(
                    part.text for part in node_result.content if part.text is not None
                )
                native_failed = node_result.isError
        outcome: Outcome = (
            Outcome.FAILED
            if (
                native_failed
                or mcp_outcome == Outcome.FAILED
                or exit_code(exec_result_record.exit) not in (None, 0)
            )
            else Outcome.SUCCEEDED
        )
        self._finished_tool_calls.add((source_key, call_id))
        answered = content(output) if output else None
        if kind == CodexToolKind.SEARCH:
            payload: EventPayload = SearchPerformed(
                native_name, _search_query(arguments), answered, outcome
            )
            return [event(raw_event, "search", call_id, "performed", payload, occurred_at=occurred_at)]
        if kind == CodexToolKind.WEB:
            payload = WebFetched(_web_url(arguments), answered, outcome)
            return [event(raw_event, "web", call_id, "fetched", payload, occurred_at=occurred_at)]
        path = _tool_path(arguments)
        if not path:
            # No path is readable from the call, and a file fact whose path was
            # invented is worse than no fact.
            return []
        if not os.path.isabs(path):
            working_directory = self._working_directories.get(str(raw_event.session_id))
            if working_directory:
                path = os.path.normpath(os.path.join(working_directory, path))
        payload = FileAccessed(
            path=path,
            action=FileAction.READ,
            outcome=outcome,
            content=(
                answered
                if tool_record.name == "mcp__node_repl__js"
                else None
            ),
        )
        return [event(raw_event, "file", f"{call_id}:read:{path}", "accessed", payload, occurred_at=occurred_at)]

    @staticmethod
    def _question_result(
        raw_event: RawEvent,
        ask_record: AskRecord,
        exec_result_record: ExecResultRecord,
        occurred_at: float | None,
    ) -> list[CanonicalEvent[EventPayload]]:
        attention_id = attention_id_from_codex_call(ask_record.call_id)
        if exec_result_record.interrupted:
            payload = QuestionAnswered(attention_id, (), None)
            return [event(
                raw_event,
                "question",
                str(attention_id),
                "answered",
                payload,
                occurred_at=occurred_at,
            )]
        try:
            document = AskResultDocument.model_validate_json(exec_result_record.output)
        except ValidationError:
            if exec_result_record.output.strip() != (
                "request_user_input can only be used by the root thread"
            ):
                raise
            payload = QuestionAnswered(attention_id, (), None)
            return [event(
                raw_event,
                "question",
                str(attention_id),
                "answered",
                payload,
                occurred_at=occurred_at,
            )]
        answers: list[AttentionAnswer] = []
        for index, question in enumerate(ask_record.questions):
            native_question_id = question.id or str(index)
            answer = document.answers.root.get(native_question_id)
            if answer is None:
                raise TranslationError(
                    f"Codex question result has no answer for {native_question_id!r}"
                )
            answers.append(AttentionAnswer(
                question_id_from_codex(CodexQuestionId(native_question_id)),
                _question_answer_labels(answer.answers),
            ))
        payload = QuestionAnswered(attention_id, tuple(answers), None)
        return [event(
            raw_event,
            "question",
            str(attention_id),
            "answered",
            payload,
            occurred_at=occurred_at,
        )]


def _question_answer_labels(native_labels: tuple[str, ...]) -> tuple[str, ...]:
    """Remove Codex dialog controls from one canonical question answer."""
    note_prefix = "user_note:"
    note = next(
        (
            label[len(note_prefix):].strip()
            for label in native_labels
            if label.casefold().startswith(note_prefix)
        ),
        "",
    )
    if not note:
        return native_labels
    selected = tuple(
        label
        for label in native_labels
        if label != "None of the above"
        and not label.casefold().startswith(note_prefix)
    )
    return (*selected, note)
