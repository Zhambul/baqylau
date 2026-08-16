"""Claude Code transcript/hook discovery, raw capture, and canonical translation."""

from __future__ import annotations

import glob
import base64
import hashlib
import json
import os
import time
from datetime import datetime
from decimal import Decimal
from typing import Literal

from contracts.harness import (
    HarnessRawEventSource,
    HarnessRawEventSources,
    HarnessTranslator,
    RawEvent,
    RawEventSourceContext,
    Session,
    TranslationError,
    TranslationResult,
    canonical_event,
)
from domain.events import (
    ActorNameChanged,
    ActorStarted,
    AttentionRequested,
    AttentionResolved,
    CanonicalEvent,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    EffortChanged,
    FileAccessed,
    GoalChanged,
    MessageCreated,
    ModelChanged,
    OperationFinished,
    OperationOutputFinished,
    OperationProgressed,
    OperationStarted,
    ActorMessageSent,
    ReasoningCreated,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    TaskChanged,
    TaskListChanged,
    TurnFinished,
    UsageReported,
)
from domain.ids import (
    ActorId,
    AttentionId,
    AssignmentId,
    MessageId,
    OperationId,
    RawEventId,
    SessionId,
    TaskId,
)
from domain.values import (
    AccountReference,
    AttentionAnswer,
    AttentionChoice,
    AttentionPrompt,
    ModelReference,
    StructuredContent,
    TextContent,
    TokenUsage,
)
from plugins.claude_code import model, transcript


# The tool_result boilerplate Claude Code emits when a Bash command is launched
# in the background. Its operation.finished still converges from the hook
# evidence; only this text is suppressed.
BACKGROUND_LAUNCH_STUB = "Command running in background with ID:"


def _model_reference(native_id: str) -> ModelReference:
    return ModelReference(
        native_id=native_id,
        display_name=model.short_model(native_id),
        selection_id=model.family(native_id),
    )


class ClaudeTranscriptRawEventSource(HarnessRawEventSource):
    """One transcript file, read as complete lines.

    Position encoding: the byte offset where the last emitted line STARTS (the
    translator keys on it — `source_position == "0"` marks a record that opens
    its transcript). Resuming therefore seeks to it and skips one line.
    """

    EVENT_BATCH_SIZE = 100

    def __init__(
        self,
        context: RawEventSourceContext,
        actor_role: Literal["child", "teammate"] | None = None,
    ) -> None:
        self.context = context
        self.actor_role = actor_role
        self.source_path = os.path.realpath(context.source_reference)
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = f"claude_code:transcript:{source_hash}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        raw_events: list[RawEvent] = []
        try:
            source = open(self.source_path, "rb")
        except FileNotFoundError:
            return ()
        with source:
            if after_position is not None:
                source.seek(int(after_position))
                skipped = source.readline()
                if not skipped.endswith(b"\n"):
                    return ()
            for _ in range(self.EVENT_BATCH_SIZE):
                line_position = source.tell()
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                actor_id, parent_actor_id = self._actor_context(line)
                raw_events.append(RawEvent(
                    raw_event_id=RawEventId(f"{self.source_identity}:{line_position}"),
                    harness="claude_code",
                    source_type=(f"{self.actor_role}_transcript" if self.actor_role else "transcript"),
                    source_name=self.source_path,
                    source_position=str(line_position),
                    session_id=self.context.session_id,
                    actor_id=actor_id,
                    parent_actor_id=parent_actor_id,
                    observed_at=time.time(),
                    encoding="jsonl",
                    payload=line,
                    source_identity=self.source_identity,
                ))
        return tuple(raw_events)

    def _actor_context(self, line: bytes) -> tuple[ActorId, ActorId | None]:
        try:
            record = transcript.parse_line(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            record = None
        if record and record.get("kind") == "teammsg":
            sender_text = str(record.get("sender") or "")
            if not sender_text:
                return self.context.actor_id, self.context.parent_actor_id
            sender = ActorId(sender_text)
            parent_actor_id = None if sender == self.context.lead_actor_id else self.context.lead_actor_id
            return sender, parent_actor_id
        if record and record.get("kind") == "actor_assignment_finished" and record.get("actor_id"):
            return ActorId(str(record["actor_id"])), self.context.lead_actor_id
        return self.context.actor_id, self.context.parent_actor_id


class ClaudeTaskRawEventSource(HarnessRawEventSource):
    """Capture Claude Code's session task files as immutable raw observations.

    Position encoding: `list:<digest of the whole task snapshot>`, carried by the
    MEMBERSHIP event, which is therefore emitted last. When anything changed,
    every current task is emitted — unchanged ones carry their previous identity
    and deduplicate on record. Deletions need no synthetic record: the
    membership fact names the survivors and the projection prunes the rest.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        config_directory = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        session_prefix = session.harness_session_id.split("-", 1)[0]
        self.task_directory = os.path.join(config_directory, "tasks", f"session-{session_prefix}")
        self.source_identity = f"claude_code:tasks:{session.harness_session_id}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        current = {}
        for path in sorted(glob.glob(os.path.join(self.task_directory, "*.json"))):
            try:
                with open(path, encoding="utf-8") as source:
                    task = json.load(source)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(task, dict) and task.get("id") is not None:
                current[str(task["id"])] = task
        if not current and after_position is None:
            return ()
        snapshot = json.dumps(current, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        snapshot_digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        position = f"list:{snapshot_digest}"
        if position == after_position:
            return ()
        raw_events = []
        for task in current.values():
            encoded = json.dumps(task, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            raw_events.append(RawEvent(
                raw_event_id=RawEventId(f"{self.source_identity}:{task['id']}:{digest}"),
                harness="claude_code",
                source_type="tasks",
                source_name=self.task_directory,
                source_position=f"{task['id']}:{digest}",
                session_id=self.session.session_id,
                actor_id=self.session.lead_actor_id,
                parent_actor_id=None,
                observed_at=time.time(),
                encoding="json",
                payload=encoded.encode("utf-8"),
                source_identity=self.source_identity,
            ))
        membership = json.dumps(
            {"list_id": "session", "task_ids": list(current)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # The raw identity chains from the previous position so that returning to
        # an EARLIER snapshot still records a new observation (a bare digest would
        # deduplicate against the old row and the position could never latch);
        # the canonical fact still converges on the snapshot itself.
        revision = hashlib.sha256(f"{after_position or ''}::{snapshot_digest}".encode("utf-8")).hexdigest()
        raw_events.append(RawEvent(
            raw_event_id=RawEventId(f"{self.source_identity}:list:{revision}"),
            harness="claude_code",
            source_type="task_list",
            source_name=self.task_directory,
            source_position=position,
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=membership.encode("utf-8"),
            source_identity=self.source_identity,
        ))
        return tuple(raw_events)


def _timestamp(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _lead_actor(session_id: SessionId) -> ActorId:
    return ActorId(f"{session_id}:lead")


def _content(value, *, markdown: bool = False):
    if isinstance(value, (dict, list)):
        return StructuredContent(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return TextContent(str(value or ""), "text/markdown" if markdown else "text/plain")


def _tool_category(native_name: str) -> str:
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


def _tool_arguments(native_name: str, arguments: dict):
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
        return _content(arguments[primary_field])
    return _content(arguments)


def _structured_patch(path: str, tool_response: dict) -> tuple[str | None, int | None, int | None]:
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


def _attention_answers(arguments: dict) -> tuple[AttentionAnswer, ...]:
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


def _plan_resolution(native: dict, failed: bool) -> tuple[str, str | None, bool]:
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


class ClaudeRawEventSources(HarnessRawEventSources):
    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        if not transcript.owns(session.source_reference):
            return ()
        sources: list[HarnessRawEventSource] = [
            ClaudeTranscriptRawEventSource(session.source_context),
            ClaudeTaskRawEventSource(session),
        ]
        transcript_base = (
            session.source_reference[:-len(".jsonl")]
            if session.source_reference.endswith(".jsonl")
            else session.source_reference
        )
        child_pattern = os.path.join(transcript_base, transcript.AGENT_SUBDIR, "agent-*.jsonl")
        for child_path in sorted(glob.glob(child_pattern)):
            filename = os.path.basename(child_path)
            actor_name = filename[len("agent-"):-len(".jsonl")]
            if not actor_name:
                continue
            sources.append(
                ClaudeTranscriptRawEventSource(
                    RawEventSourceContext(
                        session_id=session.session_id,
                        lead_actor_id=session.lead_actor_id,
                        actor_id=ActorId(actor_name),
                        parent_actor_id=session.lead_actor_id,
                        source_reference=child_path,
                    ),
                    (
                        "teammate"
                        if model.agent_meta(session.source_reference, actor_name).get("taskKind")
                        == "in_process_teammate"
                        else "child"
                    ),
                )
            )
        return tuple(sources)


class ClaudeCanonicalTranslator(HarnessTranslator):
    TASK_TOOLS = frozenset({"TaskCreate", "TaskUpdate", "TaskGet", "TaskList"})

    def __init__(self) -> None:
        self._task_tool_ids: set[str] = set()

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        try:
            text = raw_event.payload.decode("utf-8")
            document = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TranslationError("malformed Claude Code record", context=raw_event.source_position) from error
        if not isinstance(document, dict):
            raise TranslationError("Claude Code record is not an object", context=raw_event.source_position)
        if raw_event.source_type == "otel":
            events = self._translate_otel(raw_event, document)
            if not events:
                return TranslationResult((), "ignored_nonsemantic", "OTEL request carries no session usage")
            return TranslationResult(tuple(events), "translated")
        if raw_event.source_type == "foreground_output":
            operation_id = OperationId(str(document.get("operation_id") or ""))
            if not operation_id:
                raise TranslationError("foreground output has no operation id")
            try:
                ordinal = int(document["ordinal"])
                content = base64.b64decode(document["content_base64"], validate=True)
            except (KeyError, TypeError, ValueError) as error:
                raise TranslationError("malformed foreground output") from error
            progress = OperationProgressed(
                operation_id,
                ordinal,
                str(document.get("stream") or "output"),
                _content(content.decode("utf-8", errors="replace")),
                "append",
            )
            return TranslationResult(
                (self._event(
                    raw_event,
                    "operation",
                    str(operation_id),
                    f"progress:{ordinal}",
                    progress,
                ),),
                "translated",
            )
        if raw_event.source_type == "tasks":
            event = self._task_event(raw_event, document)
            return TranslationResult((event,), "translated")
        if raw_event.source_type == "task_list":
            task_ids = document.get("task_ids")
            list_id = document.get("list_id")
            if (
                not isinstance(list_id, str)
                or not isinstance(task_ids, list)
                or not all(isinstance(task_id, str) for task_id in task_ids)
            ):
                raise TranslationError("malformed Claude Code task list")
            payload = TaskListChanged(list_id, tuple(TaskId(task_id) for task_id in task_ids))
            event = self._event(raw_event, "task_list", raw_event.source_position, "changed", payload)
            return TranslationResult((event,), "translated")
        if raw_event.source_type in ("hook", "teammate_hook"):
            events = self._translate_hook(raw_event, document)
            if not events:
                return TranslationResult((), "ignored_nonsemantic", "hook carries no canonical activity")
            return TranslationResult(tuple(events), "translated")

        starts_lead_session = (
            raw_event.parent_actor_id is None
            and bool(document.get("cwd"))
            and document.get("parentUuid") is None
        )
        starts_child_actor = (
            raw_event.parent_actor_id is not None
            and raw_event.source_position == "0"
        )
        session_events = (
            self._session_events(raw_event, document)
            if starts_lead_session or starts_child_actor
            else []
        )
        metadata_events = self._transcript_metadata(raw_event, document)
        record = transcript.parse_line(text)
        if record is None:
            if session_events or metadata_events:
                return TranslationResult(tuple(session_events + metadata_events), "translated")
            return TranslationResult((), "ignored_nonsemantic", "transcript plumbing record")
        if record.get("kind") == "bad":
            raise TranslationError("malformed Claude Code transcript record", context=raw_event.source_position)
        transcript_events = self._translate_transcript(
            raw_event,
            document,
            record,
            actor_started=starts_child_actor,
        )
        events = session_events + metadata_events + transcript_events
        if not events:
            return TranslationResult((), "ignored_nonsemantic", f"nonsemantic Claude record {record['kind']!r}")
        return TranslationResult(tuple(events), "translated")

    def _translate_transcript(
        self,
        raw_event: RawEvent,
        document: dict,
        record: dict,
        *,
        actor_started: bool,
    ) -> list[CanonicalEvent]:
        kind = record["kind"]
        native_identity = str(
            document.get("uuid")
            or document.get("message", {}).get("id")
            or raw_event.source_position
        )
        occurred_at = _timestamp(document.get("timestamp"))
        if kind == "prompt":
            synthetic = bool(record.get("meta"))
            phase = "synthetic" if synthetic else "prompt"
            role = (
                "system"
                if synthetic
                else "parent"
                if raw_event.parent_actor_id is not None
                else "user"
            )
            payload = MessageCreated(MessageId(native_identity), role, _content(record["text"]), phase, None)
            return [self._event(raw_event, "message", native_identity, "created", payload, occurred_at=occurred_at)]
        if kind == "slash_command":
            return self._slash_command(raw_event, record, native_identity, occurred_at)
        if kind == "goal":
            payload = GoalChanged(record.get("objective"), record["state"], record.get("reason"))
            return [self._event(raw_event, "goal", native_identity, "changed", payload, occurred_at=occurred_at)]
        if kind == "background_command_completed":
            operation_id = OperationId(str(record.get("operation_id") or ""))
            if not operation_id:
                raise TranslationError(
                    "Claude Code background completion has no operation id",
                    context=raw_event.source_position,
                )
            payload = OperationOutputFinished(operation_id)
            return [self._event(
                raw_event,
                "operation",
                str(operation_id),
                "output_finished",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "actor_assignment_finished":
            assignment_id = AssignmentId(record["assignment_id"])
            status = record["status"]
            outcome = "failed" if status == "failed" else "cancelled" if status == "cancelled" else "succeeded"
            result = record.get("result")
            payload = ActorAssignmentFinished(
                assignment_id,
                outcome,
                _content(result, markdown=True) if result else None,
                None,
            )
            return [
                self._event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "finished",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        if kind == "teammsg":
            if not record.get("sender"):
                raise TranslationError(
                    "Claude Code teammate message has no sender",
                    context=raw_event.source_position,
                )
            payload = MessageCreated(MessageId(native_identity), "peer", _content(record["body"]), None, None)
            events = []
            if raw_event.parent_actor_id is not None and not actor_started:
                events.append(self._event(
                    raw_event,
                    "actor",
                    str(raw_event.actor_id),
                    "started",
                    ActorStarted(str(raw_event.actor_id), "teammate"),
                    occurred_at=None,
                ))
            events.append(
                self._event(
                    raw_event,
                    "message",
                    native_identity,
                    "created",
                    payload,
                    occurred_at=occurred_at,
                )
            )
            return events
        if kind == "assistant":
            events = []
            message_identity = native_identity
            native_blocks = (document.get("message") or {}).get("content")
            if not isinstance(native_blocks, list):
                native_blocks = []
            for block_index, block in enumerate(native_blocks):
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text" and str(block.get("text") or "").strip():
                    block_identity = f"{message_identity}:{block_index}"
                    payload = MessageCreated(
                        MessageId(block_identity),
                        "assistant",
                        _content(block.get("text"), markdown=True),
                        "intermediate",
                        None,
                    )
                    events.append(
                        self._event(
                            raw_event,
                            "message",
                            block_identity,
                            "created",
                            payload,
                            occurred_at=occurred_at,
                        )
                    )
                elif block_type == "thinking" and str(block.get("thinking") or "").strip():
                    block_identity = f"{message_identity}:{block_index}"
                    payload = ReasoningCreated(
                        block_identity,
                        _content(block.get("thinking"), markdown=True),
                        False,
                    )
                    events.append(
                        self._event(
                            raw_event,
                            "reasoning",
                            block_identity,
                            "created",
                            payload,
                            occurred_at=occurred_at,
                        )
                    )
                elif block_type == "tool_use":
                    events.extend(self._tool_started(raw_event, block))
            model_id = record.get("model")
            model_reference = _model_reference(model_id) if model_id else None
            if model_reference is not None:
                events.append(
                    self._event(
                        raw_event,
                        "model",
                        message_identity,
                        "reported",
                        ModelChanged(None, model_reference, "reported_by_harness"),
                        occurred_at=occurred_at,
                    )
                )
            usage = record.get("usage")
            if isinstance(usage, dict) and model_reference is not None:
                events.append(
                    self._event(
                        raw_event,
                        "context",
                        message_identity,
                        "reported",
                        ContextReported(
                            model.context_used(usage),
                            model.context_window(model_id),
                            model_reference,
                        ),
                        occurred_at=occurred_at,
                    )
                )
            return events
        if kind == "results":
            events = []
            for block in record.get("blocks") or ():
                operation_id = OperationId(str(block.get("tool_use_id") or native_identity))
                if str(operation_id) in self._task_tool_ids:
                    continue
                result_content = block.get("content")
                result_text = transcript.result_text(result_content)
                # A background launch's tool_result is boilerplate ("Command
                # running in background with ID … Output is being written to …"),
                # and its REPLACE mode would wipe any watch chunk that committed
                # first. The real output arrives through the file watch.
                if result_text.startswith(BACKGROUND_LAUNCH_STUB):
                    continue
                progress = OperationProgressed(
                    operation_id,
                    0,
                    "output",
                    _content(result_text),
                    "replace",
                )
                events.append(
                    self._event(raw_event, "operation", str(operation_id), "progress:0", progress)
                )
                outcome = "failed" if block.get("is_error") else "succeeded"
                finished = OperationFinished(operation_id, outcome, None, None)
                events.append(self._event(raw_event, "operation", str(operation_id), "finished", finished))
            for text_index, result_text in enumerate(record.get("texts") or ()):
                text_identity = f"{native_identity}:text:{text_index}"
                payload = MessageCreated(
                    MessageId(text_identity),
                    "system" if record.get("meta") else "user",
                    _content(result_text),
                    "synthetic" if record.get("meta") else "prompt",
                    None,
                )
                events.append(self._event(raw_event, "message", text_identity, "created", payload))
            return events
        if kind == "compact":
            before = (record.get("meta") or {}).get("preTokens")
            payload = CompactionFinished(int(before) if isinstance(before, int) else None, None)
            return [self._event(raw_event, "compaction", native_identity, "finished", payload, occurred_at=occurred_at)]
        if kind == "recap":
            payload = MessageCreated(
                MessageId(native_identity),
                "system",
                _content(record["text"], markdown=True),
                "recap",
                None,
            )
            return [self._event(raw_event, "message", native_identity, "created", payload, occurred_at=occurred_at)]
        return []

    def _slash_command(
        self,
        raw_event: RawEvent,
        record: dict,
        native_identity: str,
        occurred_at: float | None,
    ) -> list[CanonicalEvent]:
        """A `/command` turn: ONE prompt bubble holding what the human typed,
        plus the SESSION-STATE event the command asked for, where there is one.

        The state event is emitted from the ARGUMENT, which is a selection ALIAS
        ("opus"), not a native model id — Claude Code's transcript never carries
        the native id here, and the true one arrives a turn later on the next
        assistant record as `reported_by_harness`. Recording the alias is what
        lets the switch be seen AT THE MOMENT it was made; the two events
        describe one switch and the later one is authoritative on the id.

        A bare `/model` (no argument) opens the picker and settles nothing, and a
        multi-token argument is not a selection, so neither emits a state event.
        """
        role = "parent" if raw_event.parent_actor_id is not None else "user"
        events = [
            self._event(
                raw_event,
                "message",
                native_identity,
                "created",
                MessageCreated(MessageId(native_identity), role, _content(record["text"]), "prompt", None),
                occurred_at=occurred_at,
            )
        ]
        name = record["name"].lstrip("/").strip().lower()
        selection = record["args"].strip()
        if not selection or len(selection.split()) != 1:
            return events
        if name == "model":
            payload = ModelChanged(None, _model_reference(selection), "selected")
        elif name == "effort":
            payload = EffortChanged(None, selection, "selected")
        else:
            return events
        events.append(
            self._event(raw_event, name, native_identity, "selected", payload, occurred_at=occurred_at)
        )
        return events

    def _transcript_metadata(self, raw_event: RawEvent, document: dict) -> list[CanonicalEvent]:
        if raw_event.parent_actor_id is not None:
            return []
        record_type = document.get("type")
        if record_type == "agent-name":
            title = str(document.get("agentName") or "").strip()
            origin = "custom"
        elif record_type == "ai-title":
            title = str(document.get("aiTitle") or "").strip()
            origin = "automatic"
        elif record_type == "summary":
            title = str(document.get("summary") or "").strip()
            origin = "summary"
        else:
            return []
        if not title:
            return []
        return [
            self._event(
                raw_event,
                "session",
                str(raw_event.session_id),
                f"title:{origin}:{raw_event.source_position}",
                SessionTitleChanged(title, origin),
            )
        ]

    def _translate_hook(self, raw_event: RawEvent, document: dict) -> list[CanonicalEvent]:
        hook_name = document.get("hook_event_name") or ""
        native_identity = str(document.get("hook_event_id") or document.get("uuid") or raw_event.source_position)
        if hook_name == "SessionStart":
            return self._session_events(raw_event, document)
        if hook_name == "SessionEnd":
            payload = SessionFinished("succeeded", document.get("reason") or None)
            return [self._event(raw_event, "session", str(raw_event.session_id), "finished", payload)]
        if hook_name == "Stop":
            payload = TurnFinished(None, "succeeded")
            return [self._event(raw_event, "turn", native_identity, "finished", payload)]
        if hook_name == "StopFailure":
            events = [
                self._event(
                    raw_event, "turn", native_identity, "finished", TurnFinished(None, "failed")
                )
            ]
            if document.get("error") == "rate_limit":
                events.append(self._event(
                    raw_event,
                    "goal",
                    native_identity,
                    "changed",
                    GoalChanged(None, "usage_limited", "rate_limit"),
                ))
            return events
        if hook_name == "PreToolUse":
            return self._tool_started(raw_event, document)
        if hook_name in ("PostToolUse", "PostToolUseFailure"):
            return self._tool_finished(raw_event, document, hook_name == "PostToolUseFailure")
        if hook_name == "SubagentStart":
            actor_id = raw_event.actor_id
            role = "teammate" if raw_event.source_type == "teammate_hook" else "child"
            events = [
                self._event(
                    raw_event,
                    "actor",
                    str(actor_id),
                    "started",
                    ActorStarted(str(actor_id), role),
                )
            ]
            if document.get("agent_type"):
                events.append(
                    self._event(
                        raw_event,
                        "actor",
                        str(actor_id),
                        "name",
                        ActorNameChanged(str(document["agent_type"])),
                    )
                )
            return events
        if hook_name == "SubagentStop":
            return []
        if hook_name in ("TaskCreated", "TaskCompleted"):
            return []
        if hook_name == "PreCompact":
            return [
                self._event(
                    raw_event,
                    "compaction",
                    native_identity,
                    "started",
                    CompactionStarted(None),
                )
            ]
        if hook_name == "PostCompact":
            return [
                self._event(
                    raw_event,
                    "compaction",
                    native_identity,
                    "finished",
                    CompactionFinished(None, None),
                )
            ]
        return []

    def _task_event(self, raw_event: RawEvent, task: dict) -> CanonicalEvent:
        task_id = TaskId(str(task.get("id") or ""))
        if not task_id:
            raise TranslationError("Claude Code task has no id", context=raw_event.source_position)
        state = task.get("status")
        if state not in ("pending", "in_progress", "completed", "deleted"):
            raise TranslationError(
                f"unknown Claude Code task state: {state!r}",
                context=raw_event.source_position,
            )
        owner = str(task.get("owner") or "").strip()
        payload = TaskChanged(
            task_id,
            str(task_id),
            str(task.get("subject") or ""),
            str(task.get("description") or "").strip() or None,
            state,
            ActorId(owner) if owner else None,
        )
        return self._event(raw_event, "task", str(task_id), "changed", payload)

    def _translate_otel(self, raw_event: RawEvent, document: dict) -> list[CanonicalEvent]:
        grouped: dict[tuple[str, str], dict[str, Decimal]] = {}
        for resource in document.get("resourceMetrics", []):
            for scope in resource.get("scopeMetrics", []):
                for metric in scope.get("metrics", []):
                    metric_name = str(metric.get("name") or "")
                    if "token.usage" not in metric_name and "cost.usage" not in metric_name:
                        continue
                    for point in (metric.get("sum") or {}).get("dataPoints", []):
                        attributes = {}
                        for attribute in point.get("attributes", []):
                            value = attribute.get("value") or {}
                            attributes[str(attribute.get("key") or "")] = next(
                                (value[key] for key in ("stringValue", "intValue", "doubleValue") if key in value),
                                None,
                            )
                        if str(attributes.get("session.id") or "") != str(raw_event.session_id):
                            continue
                        native_value = point.get("asDouble", point.get("asInt"))
                        if native_value is None:
                            continue
                        model_id = str(attributes.get("model") or "")
                        query_source = str(attributes.get("query_source") or "")
                        values = grouped.setdefault((model_id, query_source), {})
                        usage_type = str(attributes.get("type") or "")
                        key = "cost" if "cost.usage" in metric_name else usage_type
                        values[key] = values.get(key, Decimal(0)) + Decimal(str(native_value))

        events = []
        for index, ((model_id, query_source), values) in enumerate(sorted(grouped.items())):
            tokens = TokenUsage(
                input_tokens=int(values.get("input", 0)),
                output_tokens=int(values.get("output", 0)),
                cache_read_tokens=int(values.get("cacheRead", 0)),
                cache_write_tokens=int(values.get("cacheCreation", 0)),
            )
            cost = values.get("cost")
            if tokens == TokenUsage() and cost is None:
                continue
            model = _model_reference(model_id) if model_id else None
            payload = UsageReported(
                "session",
                str(raw_event.session_id),
                model,
                None,
                tokens,
                False,
                cost,
            )
            events.append(self._event(
                raw_event,
                "usage",
                f"{raw_event.source_position}:{index}:{model_id}:{query_source}",
                "reported",
                payload,
            ))
        return events

    def _session_events(self, raw_event: RawEvent, document: dict) -> list[CanonicalEvent]:
        lead_actor_id = raw_event.actor_id
        if raw_event.parent_actor_id is not None:
            metadata = {}
            if raw_event.source_type == "child_transcript":
                metadata_path = os.path.splitext(raw_event.source_name)[0] + ".meta.json"
                try:
                    with open(metadata_path, encoding="utf-8") as metadata_file:
                        metadata = json.load(metadata_file)
                except (OSError, json.JSONDecodeError):
                    metadata = {}
            events = [
                self._event(
                    raw_event,
                    "actor",
                    str(raw_event.actor_id),
                    "started",
                    ActorStarted(
                        str(raw_event.actor_id),
                        "teammate" if raw_event.source_type == "teammate_transcript" else "child",
                    ),
                )
            ]
            description = str(metadata.get("description") or "").strip()
            if description:
                events.append(self._event(
                    raw_event,
                    "actor",
                    str(raw_event.actor_id),
                    "name:description",
                    ActorNameChanged(description),
                ))
            return events
        transcript_path = str(document.get("transcript_path") or "")
        session_started = SessionStarted(
            working_directory=document.get("cwd") or "",
            source_reference=(
                os.path.realpath(transcript_path) if transcript_path else raw_event.source_name
            ),
            resumed_from=None,
            title=None,
            model=None,
            effort=None,
            account=None,
        )
        events = [
            self._event(
                raw_event,
                "session",
                str(raw_event.session_id),
                "started",
                session_started,
            ),
            self._event(
                raw_event,
                "actor",
                str(lead_actor_id),
                "started",
                ActorStarted("claude", "lead"),
            ),
        ]
        if raw_event.account_id is not None or raw_event.account_display_name is not None:
            account_id = raw_event.account_id or ""
            display_name = raw_event.account_display_name or account_id or "default"
            events.append(self._event(
                raw_event,
                "session",
                str(raw_event.session_id),
                f"account:{raw_event.source_position}",
                SessionAccountChanged(AccountReference(account_id, display_name)),
            ))
        return events

    def _tool_started(self, raw_event: RawEvent, native: dict) -> list[CanonicalEvent]:
        operation_id = OperationId(str(native.get("tool_use_id") or native.get("id") or raw_event.source_position))
        native_name = native.get("tool_name") or native.get("name") or "tool"
        if native_name in self.TASK_TOOLS:
            self._task_tool_ids.add(str(operation_id))
            return []
        arguments = native.get("tool_input") if "tool_input" in native else native.get("input")
        arguments = arguments if isinstance(arguments, dict) else {}
        if native_name == "Monitor":
            execution = "monitor"
        elif native_name == "Bash" and arguments.get("run_in_background"):
            execution = "background"
        else:
            execution = "foreground"
        started = OperationStarted(
            operation_id,
            _tool_category(native_name),
            native_name,
            execution,
            _tool_arguments(native_name, arguments),
            arguments.get("description") or None,
            None,
        )
        events = [self._event(raw_event, "operation", str(operation_id), "started", started)]
        events.extend(self._tool_side_facts(raw_event, operation_id, native_name, arguments))
        return events

    def _tool_finished(self, raw_event: RawEvent, native: dict, failed: bool) -> list[CanonicalEvent]:
        operation_id = OperationId(str(native.get("tool_use_id") or native.get("id") or raw_event.source_position))
        native_name = native.get("tool_name") or "tool"
        if native_name in self.TASK_TOOLS:
            self._task_tool_ids.add(str(operation_id))
            return []
        arguments = native.get("tool_input") or {}
        finished = OperationFinished(operation_id, "failed" if failed else "succeeded", None, None)
        events = [self._event(raw_event, "operation", str(operation_id), "finished", finished)]
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
            payload = ActorAssignmentFinished(
                assignment_id,
                "failed" if failed else "succeeded",
                None,
                None,
            )
            events.append(
                self._event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "finished",
                    payload,
                )
            )
        if native_name in ("AskUserQuestion", "ExitPlanMode"):
            attention_id = AttentionId(str(operation_id))
            if native_name == "AskUserQuestion":
                decision = "answered"
                answers = _attention_answers(arguments)
                feedback = None
                edited = False
            else:
                decision, feedback, edited = _plan_resolution(native, failed)
                answers = ()
            payload = AttentionResolved(
                attention_id,
                decision,
                answers,
                feedback,
                edited,
                "failed" if failed else "succeeded",
            )
            events.append(self._event(raw_event, "attention", str(attention_id), "resolved", payload))
        events.extend(
            self._file_facts(raw_event, operation_id, native_name, arguments, tool_response)
        )
        return events

    def _tool_side_facts(
        self,
        raw_event: RawEvent,
        operation_id: OperationId,
        native_name: str,
        arguments: dict,
    ) -> list[CanonicalEvent]:
        events = self._file_facts(raw_event, operation_id, native_name, arguments, None)
        if native_name in ("Task", "Agent"):
            assignment_id = AssignmentId(str(operation_id))
            actor_name = arguments.get("name") or arguments.get("subagent_type")
            prompt = arguments.get("prompt")
            payload = ActorAssignmentStarted(
                assignment_id,
                _content(arguments.get("description") or prompt or ""),
                actor_name=str(actor_name) if actor_name else None,
                prompt=_content(prompt, markdown=True) if prompt else None,
            )
            events.append(
                self._event(
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
            content = _content(arguments.get("content") or arguments.get("message"))
            payload = ActorMessageSent(message_id, recipient, content)
            events.append(self._event(raw_event, "actor_message", str(message_id), "sent", payload))
        if native_name in ("AskUserQuestion", "ExitPlanMode"):
            attention_id = AttentionId(str(operation_id))
            prompts = self._attention_prompts(native_name, arguments)
            attention_type = "question" if native_name == "AskUserQuestion" else "plan"
            payload = AttentionRequested(attention_id, attention_type, prompts, operation_id)
            events.append(self._event(raw_event, "attention", str(attention_id), "requested", payload))
        return events

    def _file_facts(
        self,
        raw_event: RawEvent,
        operation_id: OperationId,
        native_name: str,
        arguments: dict,
        tool_response: dict | None,
    ) -> list[CanonicalEvent]:
        action_by_tool = {
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
        content = _content(content_value) if native_name == "Write" else None
        unified_diff, lines_added, lines_removed = _structured_patch(path, response)
        payload = FileAccessed(
            operation_id,
            path,
            action,
            lines_added=lines_added,
            lines_removed=lines_removed,
            unified_diff=unified_diff,
            content=content,
        )
        file_identity = f"{operation_id}:{action}:{path}"
        phase = "finished" if tool_response is not None else "started"
        return [self._event(raw_event, "file", file_identity, phase, payload)]

    @staticmethod
    def _attention_prompts(native_name: str, arguments: dict) -> tuple[AttentionPrompt, ...]:
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

    @staticmethod
    def _event(
        raw_event: RawEvent,
        subject_type: str,
        subject_id: str,
        phase: str,
        payload,
        *,
        occurred_at: float | None = None,
    ) -> CanonicalEvent:
        return canonical_event(
            raw_event, subject_type, subject_id, phase, payload, occurred_at=occurred_at
        )
