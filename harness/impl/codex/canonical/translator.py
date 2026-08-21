"""Codex canonical translation: dispatch across the rollout's records and hooks."""

from __future__ import annotations

import json
import os
import re
from enum import StrEnum
from typing import Literal

from pydantic import JsonValue

from harness.contract import HarnessTranslator
from harness.models import RawEvent, TranslationError, TranslationResult, UnknownRawEvent
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
    QuestionAsked,
    ReasoningCreated,
    SearchPerformed,
    SessionStarted,
    ShellBackgrounded,
    ShellFinished,
    ShellInputProvided,
    ShellProgressed,
    ShellStarted,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
    TurnStarted,
    UsageReported,
    WebFetched,
)
from domain.ids import (
    ActorId,
    AttentionId,
    AssignmentId,
    CallId,
    MessageId,
    ModelId,
    QuestionId,
    ReasoningId,
    ShellId,
    ShellNativeId,
    TaskId,
    TaskListId,
    TurnId,
)
from domain.values import (
    ActorRole,
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
    UsageScope,
)
from harness.impl.codex.canonical import rollout
from harness.impl.codex.canonical.events import PHASE_FINAL
from harness.impl.codex.canonical.records import (
    COLLABORATION_ARGUMENTS,
    ActorActivityRecord,
    AskRecord,
    BadRecord,
    ChatRecord,
    CodexHookPayload,
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
    PatchCallRecord,
    PatchRecord,
    PlanRecord,
    PromptRecord,
    ReasoningRecord,
    RolloutRecord,
    SearchRecord,
    SendMessageArguments,
    SessionMetaPayload,
    SessionMetaSource,
    SettingsRecord,
    StdinRecord,
    TaskCompleteRecord,
    TaskListRecord,
    TaskStartedRecord,
    ThinkRecord,
    ToolRecord,
    TurnAbortedRecord,
    TurnContextRecord,
    UnmappedToolRecord,
    UsageRecord,
)
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


# What one of Codex's non-shell tool calls IS. `IGNORED` is named rather than
# left to fall through: a generated image exposes no readable path to put on a
# file fact, so there is nothing to record about it.
class CodexToolKind(StrEnum):
    SEARCH = "search"
    WEB = "web"
    FILE = "file"
    IGNORED = "ignored"


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
        if any(field in fields for field in ("search_query", "image_query", "weather", "finance", "sports")):
            return CodexToolKind.SEARCH, "WebSearch"
        if any(field in fields for field in ("open", "click", "find", "screenshot")):
            return CodexToolKind.WEB, "WebFetch"
        # A time lookup is neither a search nor a fetch: it has no query, no url
        # and no reader.
        raise UnknownRawEvent("unmapped Codex web action")
    mapping: dict[str, tuple[CodexToolKind, str]] = {
        "view_image": (CodexToolKind.FILE, "ReadImage"),
        "image_gen__imagegen": (CodexToolKind.IGNORED, "GenerateImage"),
    }
    mapped = mapping.get(native_name)
    if mapped is None:
        raise UnknownRawEvent(f"unmapped Codex tool: {native_name or '<missing>'}")
    return mapped



# A string field of a JavaScript object literal — `{cmd:"ls"}` as codex writes
# it through the exec custom tool, where the key may or may not be quoted. The
# same shape `_JS_CMD` in items.py reads, and for the same reason: the arguments
# are JavaScript source, and nothing here interprets JavaScript.
_JS_STRING_FIELD = re.compile(r"""["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*:\s*"((?:[^"\\]|\\.)*)\"""")

# Which field of a web search holds what was searched for, in the order codex
# spells them (`_codex_tool` recognises a search by exactly these names).
_SEARCH_QUERY_FIELDS = ("search_query", "image_query", "weather", "finance", "sports", "query")


def _tool_fields(arguments: str | None) -> dict[str, JsonValue]:
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
        parsed = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {
        match.group(1): match.group(2).encode().decode("unicode_escape")
        for match in _JS_STRING_FIELD.finditer(arguments or "")
    }


def _search_query(arguments: str | None) -> Content:
    """What was searched for. The whole argument blob is the fallback: a query
    nobody can read is still a better raw event than an empty one."""
    fields = _tool_fields(arguments)
    for name in _SEARCH_QUERY_FIELDS:
        value = fields.get(name)
        if isinstance(value, str) and value:
            return content(value)
    return content(arguments)


def _web_url(arguments: str | None) -> str | None:
    """The address a fetch was for, when the call names one. Codex's `open` is
    often an index into a previous search's results rather than an address, so
    only something that reads as one counts."""
    for value in _tool_fields(arguments).values():
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _json_scalar(value: JsonValue) -> str | int | float | None:
    """A JSON value narrowed to the scalar shapes `support.timestamp` and a
    handful of native-identity fallbacks accept — never the container shapes
    (list/dict) those raw payload lookups can otherwise widen to."""
    return value if isinstance(value, (str, int, float)) else None


def _json_str(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _tool_path(arguments: str | None) -> str:
    fields = _tool_fields(arguments)
    for name in ("path", "file_path"):
        value = fields.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


class CodexCanonicalTranslator(HarnessTranslator):
    def __init__(self) -> None:
        self._collaboration_calls: dict[tuple[str, str], tuple[str, CollaborationArguments]] = {}
        self._process_shells: dict[tuple[str, str], ShellId] = {}
        self._continuation_shells: dict[tuple[str, str], ShellId] = {}
        self._finished_shells: set[tuple[str, ShellId]] = set()
        # Announced background once. An exec that outlived its yield is reported
        # again by every continuation poll, and the fact is about the command,
        # not about the poll that observed it.
        self._backgrounded_shells: set[tuple[str, ShellId]] = set()
        self._semantic_tool_calls: set[tuple[str, str]] = set()
        self._call_records: dict[tuple[str, str], ExecRecord | ToolRecord | None] = {}
        self._plan_tasks: dict[tuple[str, str], dict[TaskId, TaskChanged]] = {}
        self._selections = SelectionSemantics()

    @staticmethod
    def _source_key(raw_event: RawEvent) -> str:
        return os.path.realpath(raw_event.source_name)

    @staticmethod
    def _decode_arguments(arguments: JsonValue | None) -> dict[str, JsonValue]:
        """A `function_call`'s JSON `arguments` STRING, decoded to a dict — {}
        when the version at hand wrote something else or the line was
        truncated (items._args is the live-path twin of this backscan one)."""
        if not isinstance(arguments, str):
            return {}
        try:
            decoded = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _collaboration_call_from_document(
        document: dict[str, JsonValue], call_id: CallId,
    ) -> tuple[str, CollaborationArguments] | None:
        payload = document.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        name = payload.get("name")
        arguments_model = COLLABORATION_ARGUMENTS.get(name) if isinstance(name, str) else None
        if not (
            document.get("type") == "response_item"
            and payload.get("type") == "function_call"
            and payload.get("call_id") == call_id
            and isinstance(name, str)
            and arguments_model is not None
        ):
            return None
        return name, arguments_model.model_validate(
            CodexCanonicalTranslator._decode_arguments(payload.get("arguments"))
        )

    def _collaboration_call(
        self,
        raw_event: RawEvent,
        call_id: CallId,
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
                        try:
                            document = json.loads(line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if not isinstance(document, dict):
                            continue
                        call = self._collaboration_call_from_document(document, call_id)
                        if call is not None:
                            self._collaboration_calls[key] = call
                            return call
                    end_position = start_position
        except OSError:
            return None
        return None

    @staticmethod
    def _call_from_document(
        document: dict[str, JsonValue], call_id: CallId,
    ) -> ExecRecord | ToolRecord | Literal[False] | None:
        """The parsed call this output belongs to.

        None means this is not the call being sought; False means it is the
        call, but its grammar is deliberately nonsemantic/unsupported. A record
        rather than a bare yes: what the output MEANS is the call's kind and
        arguments — a command's exit, or a search's results — and only the call
        carries them.
        """
        payload = document.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if not (
            document.get("type") == "response_item"
            and payload.get("type") in ("function_call", "custom_tool_call")
            and payload.get("call_id") == call_id
        ):
            return None
        record = rollout.parse(document)
        if isinstance(record, (ExecRecord, ToolRecord)):
            return record
        return False

    def _call_record(
        self,
        raw_event: RawEvent,
        call_id: CallId,
    ) -> ExecRecord | ToolRecord | None:
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
                        try:
                            document = json.loads(line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if not isinstance(document, dict):
                            continue
                        opened = self._call_from_document(document, call_id)
                        if opened is not None:
                            found = opened if isinstance(opened, (ExecRecord, ToolRecord)) else None
                            self._call_records[key] = found
                            return found
                    end_position = start_position
        except OSError:
            pass
        self._call_records[key] = None
        return None

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        try:
            return self._translate(raw_event)
        except UnknownRawEvent as unknown:
            return TranslationResult((), RecordedTranslationDecision.IGNORED_UNKNOWN, unknown.reason)

    def _translate(self, raw_event: RawEvent) -> TranslationResult:
        try:
            raw_text = raw_event.payload.decode("utf-8")
            document = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TranslationError("malformed Codex rollout record", context=raw_event.source_position) from error
        if not isinstance(document, dict):
            raise TranslationError("Codex rollout record is not an object", context=raw_event.source_position)

        if raw_event.source_type == "hook":
            if raw_event.parent_actor_id is not None:
                return TranslationResult(
                    (),
                    RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                    "subagent delivery; its activity arrives through the lead's rollout",
                )
            events = self._translate_hook(raw_event, document)
            if events:
                return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "hook carries no unique canonical activity"
            )

        if raw_event.source_type in ("child_replay", "sidecar_replay"):
            return TranslationResult(
                (),
                RecordedTranslationDecision.IGNORED_NONSEMANTIC,
                "parent history replayed in child rollout",
            )

        if document.get("type") == "session_meta":
            if raw_event.source_position != "0":
                return TranslationResult(
                    (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, "replayed session metadata"
                )
            raw_metadata = document.get("payload")
            metadata = SessionMetaPayload.model_validate(raw_metadata if isinstance(raw_metadata, dict) else {})
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
                )),
                RecordedTranslationDecision.TRANSLATED,
            )

        record = rollout.parse(document)
        if record is None:
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_UNKNOWN, f"unhandled Codex record {document.get('type')!r}"
            )
        if isinstance(record, BadRecord):
            raise TranslationError("malformed Codex rollout record", context=raw_event.source_position)

        events = self._translate_record(raw_event, document, record)
        if not events:
            return TranslationResult(
                (), RecordedTranslationDecision.IGNORED_NONSEMANTIC, f"nonsemantic Codex record {record.kind!r}"
            )
        return TranslationResult(tuple(events), RecordedTranslationDecision.TRANSLATED)

    def _translate_hook(
        self,
        raw_event: RawEvent,
        document: dict[str, JsonValue],
    ) -> list[CanonicalEvent[EventPayload]]:
        hook = CodexHookPayload.model_validate(document)
        hook_name = hook.hook_event_name or ""
        native_identity = hook.hook_event_id or hook.uuid or raw_event.source_position
        if hook_name == "SessionStart":
            path = hook.transcript_path or ""
            if not lead_rollout(path):
                # A subagent thread announces no session of its own.
                return []
            return self._session_started_events(
                raw_event,
                hook.cwd or "",
                os.path.realpath(path),
            )
        if hook_name == "PreCompact":
            payload: EventPayload = CompactionStarted(hook.before_tokens)
            return [event(raw_event, "compaction", native_identity, "started", payload)]
        if hook_name == "PostCompact":
            payload = CompactionFinished(hook.before_tokens, hook.after_tokens)
            return [event(raw_event, "compaction", native_identity, "finished", payload)]
        return []

    def _session_started_events(
        self,
        raw_event: RawEvent,
        working_directory: str,
        source_reference: str,
        *,
        occurred_at: float | None = None,
    ) -> list[CanonicalEvent[EventPayload]]:
        return [
            event(
                raw_event,
                "session",
                str(raw_event.session_id),
                "started",
                SessionStarted(
                    working_directory=working_directory,
                    source_reference=source_reference,
                    resumed_from=None,
                    title=None,
                    model=None,
                    effort=None,
                    account=None,
                ),
                occurred_at=occurred_at,
            ),
            event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                ActorStarted("codex", ActorRole.LEAD),
                occurred_at=occurred_at,
            ),
        ]

    # Record kinds that carry a `call_id`/`turn`/`at` field of the same NAME —
    # narrowed here once so the branches below read `record.call_id` etc.
    # directly rather than re-deriving the tuple per branch.
    _CALL_ID_RECORDS = (
        ExecRecord, ExecResultRecord, StdinRecord, ToolRecord, PatchCallRecord, AskRecord,
        ActorActivityRecord, CollaborationCallRecord, TaskListRecord, GoalToolRecord,
    )
    _AT_RECORDS = (TaskStartedRecord, TaskCompleteRecord)

    def _translate_record(
        self,
        raw_event: RawEvent,
        document: dict[str, JsonValue],
        record: RolloutRecord,
    ) -> list[CanonicalEvent[EventPayload]]:
        native_payload = document.get("payload")
        native_payload = native_payload if isinstance(native_payload, dict) else {}
        record_call_id = record.call_id if isinstance(record, self._CALL_ID_RECORDS) else None
        native_identity = str(
            record_call_id
            or native_payload.get("id")
            or native_payload.get("item_id")
            or raw_event.source_position
        )
        occurred_at = timestamp(_json_scalar(document.get("timestamp")))
        if occurred_at is None:
            record_at = record.at if isinstance(record, self._AT_RECORDS) else None
            occurred_at = timestamp(record_at)

        if isinstance(record, TaskStartedRecord):
            turn_id = TurnId(record.turn or f"{raw_event.session_id}:{native_identity}")
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
                assignment_id = AssignmentId(str(turn_id))
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
            turn_id = TurnId(record.turn or f"{raw_event.session_id}:{native_identity}")
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
                assignment_id = AssignmentId(str(turn_id))
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
            turn_id = TurnId(record.turn or _json_str(native_payload.get("turn_id")) or native_identity)
            events = [event(raw_event, "turn", str(turn_id), "aborted", TurnAborted(None), turn_id, occurred_at)]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                assignment_id = AssignmentId(str(turn_id))
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
            message_id = MessageId(native_identity)
            message_content = content(record.text, markdown=role == "assistant")
            payload: EventPayload = MessageCreated(message_id, role, message_content, phase, None)
            # A message need not belong to a turn; the bindings above in this
            # same function always do, so the name has to admit None here.
            message_turn_id: TurnId | None = (
                TurnId(record.turn) if isinstance(record, ChatRecord) and record.turn else None
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
        if isinstance(record, (ReasoningRecord, ThinkRecord)):
            payload = ReasoningCreated(ReasoningId(native_identity), content(record.text, markdown=True))
            return [event(raw_event, "reasoning", native_identity, "created", payload, occurred_at=occurred_at)]
        if isinstance(record, CollaborationCallRecord):
            call_id = CallId(record.call_id or "")
            self._collaboration_calls[(os.path.realpath(raw_event.source_name), call_id)] = (
                record.name, record.args,
            )
            return []
        if isinstance(record, ActorActivityRecord):
            call_id = CallId(record.call_id or "")
            call = self._collaboration_call(raw_event, call_id)
            if call is None:
                raise TranslationError(f"Codex actor activity has no collaboration call: {call_id or '<missing>'}")
            call_name, call_arguments = call
            activity = record.activity
            expected_calls = {
                "started": "spawn_agent",
                "interrupted": "interrupt_agent",
            }
            expected_call = expected_calls.get(activity)
            if expected_call is not None and call_name != expected_call:
                raise TranslationError(f"Codex actor activity {activity!r} came from {call_name!r}")
            if activity == "interacted":
                if call_name == "followup_task":
                    return []
                if call_name != "send_message" or not isinstance(call_arguments, SendMessageArguments):
                    raise TranslationError(f"Codex actor interaction came from {call_name!r}")
                message_id = MessageId(call_id)
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
                    ActorId(record.actor_id),
                )
                return [event(
                    raw_event,
                    "message",
                    str(message_id),
                    "created",
                    payload,
                    TurnId(record.turn) if record.turn else None,
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
            states: dict[str, GoalState] = {
                "active": GoalState.ACTIVE,
                "paused": GoalState.PAUSED,
                "blocked": GoalState.BLOCKED,
                "usageLimited": GoalState.USAGE_LIMITED,
                "budgetLimited": GoalState.BUDGET_LIMITED,
                "complete": GoalState.COMPLETED,
                "cleared": GoalState.CLEARED,
            }
            state = states.get(native_state)
            if state is None:
                raise TranslationError(f"unknown Codex goal state: {native_state or '<missing>'}")
            objective = (record.objective or "").strip() or None
            if state != GoalState.CLEARED and objective is None:
                raise TranslationError("Codex goal has no objective")
            payload = GoalChanged(objective, state, (record.reason or "").strip() or None)
            return [event(
                raw_event,
                "goal",
                native_identity,
                "changed",
                payload,
                occurred_at=occurred_at,
            )]
        if isinstance(record, GoalToolRecord):
            call_id = CallId(record.call_id or native_identity)
            self._semantic_tool_calls.add((self._source_key(raw_event), call_id))
            return []
        if isinstance(record, TaskListRecord):
            call_id = CallId(record.call_id or native_identity)
            source_key = self._source_key(raw_event)
            self._semantic_tool_calls.add((source_key, call_id))
            plan_key = (str(raw_event.session_id), str(raw_event.actor_id))
            previous = self._plan_tasks.get(plan_key, {})
            current: dict[TaskId, TaskChanged] = {}
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
                task_id = TaskId(f"{raw_event.actor_id}:plan:{task_index}")
                current[task_id] = TaskChanged(
                    task_id,
                    subject,
                    None,
                    task_state,
                    raw_event.actor_id,
                )
            events = [event(
                raw_event,
                "task_list",
                str(raw_event.actor_id),
                f"changed:{call_id}",
                TaskListChanged(TaskListId(str(raw_event.actor_id)), tuple(current)),
                occurred_at=occurred_at,
            )]
            for task_id, task_changed in current.items():
                if previous.get(task_id) == task_changed:
                    continue
                events.append(event(
                    raw_event, "task", str(task_id), f"changed:{call_id}", task_changed,
                    occurred_at=occurred_at,
                ))
            self._plan_tasks[plan_key] = current
            return events
        if isinstance(record, (ExecRecord, ToolRecord)):
            call_id = CallId(record.call_id or native_identity)
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
            shell_id = ShellId(call_id)
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
            known_shell_id = self._process_shells.get((source_key, process_id))
            if known_shell_id is None:
                raise TranslationError(f"Codex write_stdin references unknown process session: {process_id}")
            shell_id = known_shell_id
            call_id = CallId(record.call_id or native_identity)
            self._continuation_shells[(source_key, call_id)] = shell_id
            text = record.text
            if not text:
                return []
            if (source_key, shell_id) in self._finished_shells:
                raise TranslationError(f"Codex write_stdin targets finished command: {shell_id}")
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
            call_id = CallId(record.call_id or native_identity)
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
            shell_id = ShellId(call_id)
            process_exit_code = exit_code(record.exit)
            process_id = record.process_id or ShellNativeId("")
            if process_id:
                self._process_shells[(source_key, process_id)] = shell_id
            if record.running:
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
                        ShellBackgrounded(shell_id, ShellNativeId(process_id) if process_id else None),
                        occurred_at=occurred_at,
                    ))
                output = record.output
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
            return [
                event(
                    raw_event,
                    "shell",
                    str(shell_id),
                    "finished",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        if isinstance(record, CommandCompletedRecord):
            source_key = self._source_key(raw_event)
            process_id = record.process_id
            # Same reason as the write_stdin branch above: the lookup is
            # optional, the id every line after it uses is not.
            completed_shell_id = self._process_shells.get((source_key, process_id))
            if completed_shell_id is None or (source_key, completed_shell_id) in self._finished_shells:
                return []
            shell_id = completed_shell_id
            process_exit_code = exit_code(record.exit)
            outcome = Outcome.SUCCEEDED if process_exit_code == 0 else Outcome.FAILED
            self._finished_shells.add((source_key, shell_id))
            payload = ShellFinished(shell_id, outcome, content(record.output), process_exit_code)
            return [event(
                raw_event,
                "shell",
                str(shell_id),
                "finished",
                payload,
                occurred_at=occurred_at,
            )]
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
            action_by_change: dict[str, FileAction] = {
                "add": FileAction.CREATED,
                "delete": FileAction.DELETED,
                "move": FileAction.RENAMED,
                "update": FileAction.UPDATED,
            }
            events = []
            for file_order, file_record in enumerate(record.files):
                path = file_record.path
                payload = FileAccessed(
                    path=path,
                    action=action_by_change.get(file_record.change or "", FileAction.UPDATED),
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
                    model_reference(ModelId(record.model)),
                    ModelChangeReason.REPORTED_BY_HARNESS,
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
            return [
                event(
                    raw_event,
                    "compaction",
                    native_identity,
                    "finished",
                    CompactionFinished(None, None),
                    occurred_at=occurred_at,
                )
            ]
        if isinstance(record, AskRecord):
            questions = tuple(
                AttentionPrompt(
                    prompt_id=QuestionId(question.id or str(index)),
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
            attention_id = AttentionId(record.call_id or native_identity)
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
            attention_id = AttentionId(record.id or native_identity)
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
        call_id: CallId,
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
        outcome: Outcome = (
            Outcome.FAILED if exit_code(exec_result_record.exit) not in (None, 0) else Outcome.SUCCEEDED
        )
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
        payload = FileAccessed(path=path, action=FileAction.READ, outcome=outcome)
        return [event(raw_event, "file", f"{call_id}:read:{path}", "accessed", payload, occurred_at=occurred_at)]
