"""Codex rollout discovery, raw capture, and canonical translation."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
from datetime import datetime
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
    ActorStarted,
    ActorMessageSent,
    AttentionRequested,
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
    OperationInputProvided,
    OperationProgressed,
    OperationStarted,
    ReasoningCreated,
    SessionStarted,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
    TurnStarted,
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
    TurnId,
)
from domain.values import AttentionChoice, AttentionPrompt, ModelReference, StructuredContent, TextContent, TokenUsage
from plugins.codex import rollout

ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl$")
EVENT_BATCH_SIZE = 100


def _model_reference(native_id: str) -> ModelReference:
    return ModelReference(native_id, native_id, native_id)


def _harness_session_id(path: str) -> str:
    match = ROLLOUT_NAME.search(os.path.basename(path))
    return match.group(1) if match else os.path.splitext(os.path.basename(path))[0]


def _session_metadata(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as source:
            for _ in range(5):
                line = source.readline()
                if not line:
                    break
                document = json.loads(line)
                if document.get("type") == "session_meta":
                    return document.get("payload") or {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {}


def _parent_thread_id(metadata: dict) -> str | None:
    source = metadata.get("source")
    spawn = (
        ((source.get("subagent") or {}).get("thread_spawn") or {})
        if isinstance(source, dict)
        else {}
    )
    parent = spawn.get("parent_thread_id") or metadata.get("parent_thread_id")
    return str(parent).strip() if parent else None


def _rollout_paths() -> tuple[str, ...]:
    codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    pattern = os.path.join(codex_home, "sessions", "*", "*", "*", "rollout-*.jsonl")
    return tuple(sorted(glob.glob(pattern)))


def lead_rollout(path: str) -> bool:
    """Whether the path names a LEAD rollout — subagent rollouts and
    non-rollouts announce no session of their own."""
    path = os.path.realpath(path)
    if not os.path.isfile(path) or not ROLLOUT_NAME.search(os.path.basename(path)):
        return False
    metadata = _session_metadata(path)
    if not metadata:
        return False
    return metadata.get("thread_source") != "subagent" and not metadata.get("parent_thread_id")


class CodexRolloutRawEventSource(HarnessRawEventSource):
    """One rollout file, read as complete lines.

    Position encoding: the byte offset where the last emitted line STARTS (the
    translator keys on it — `source_position == "0"` marks the opening
    session_meta, and the collaboration backscan reads everything BEFORE it).
    Resuming therefore seeks to it and skips one line.
    """

    def __init__(
        self,
        context: RawEventSourceContext,
        child_body_position: int | None = None,
        actor_relation: Literal["child", "sidecar"] | None = None,
    ) -> None:
        self.context = context
        self.child_body_position = child_body_position
        self.actor_relation = actor_relation
        self.source_path = os.path.realpath(context.source_reference)
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = f"codex:rollout:{source_hash}"

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
            for _ in range(EVENT_BATCH_SIZE):
                line_position = source.tell()
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                raw_events.append(RawEvent(
                    raw_event_id=RawEventId(f"{self.source_identity}:{line_position}"),
                    harness="codex",
                    source_type=self._source_type(line_position),
                    source_name=self.source_path,
                    source_position=str(line_position),
                    session_id=self.context.session_id,
                    actor_id=self.context.actor_id,
                    parent_actor_id=self.context.parent_actor_id,
                    observed_at=time.time(),
                    encoding="jsonl",
                    payload=line,
                    source_identity=self.source_identity,
                ))
        return tuple(raw_events)

    def _source_type(self, line_position: int) -> str:
        if (
            self.child_body_position is not None
            and 0 < line_position < self.child_body_position
        ):
            return f"{self.actor_relation}_replay"
        return f"{self.actor_relation}_rollout" if self.actor_relation else "rollout"


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


def _exit_code(record: dict) -> int | None:
    """The record's exit status, honest about zero: `0` is a real exit code
    (a falsy-int coercion once turned a clean exit into outcome "failed")."""
    value = record.get("exit")
    return int(value) if str(value).lstrip("-").isdigit() else None


def _content(value, *, markdown: bool = False):
    if isinstance(value, (dict, list)):
        return StructuredContent(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return TextContent(str(value or ""), "text/markdown" if markdown else "text/plain")


def _codex_tool(native_name: str, arguments) -> tuple[str, str]:
    """Map Codex transport names onto the canonical operation vocabulary."""
    if native_name == "web__run":
        try:
            fields = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            fields = {
                match.group(1): None
                for match in re.finditer(
                    r'(?:^|[,{])\s*["\']?([A-Za-z_][A-Za-z0-9_]*)["\']?\s*:',
                    arguments,
                )
            }
        if not isinstance(fields, dict) or not fields:
            raise TranslationError("Codex web tool arguments are not an object")
        if any(field in fields for field in ("search_query", "image_query", "weather", "finance", "sports")):
            return "search", "WebSearch"
        if any(field in fields for field in ("open", "click", "find", "screenshot")):
            return "network", "WebFetch"
        if "time" in fields:
            return "network", "TimeLookup"
        raise TranslationError("unmapped Codex web action")
    mapping = {
        "view_image": ("file_read", "ReadImage"),
        "image_gen__imagegen": ("media", "GenerateImage"),
        "update_plan": ("task", "UpdatePlan"),
        "create_goal": ("task", "CreateGoal"),
        "get_goal": ("task", "ReadGoal"),
        "update_goal": ("task", "UpdateGoal"),
    }
    mapped = mapping.get(native_name)
    if mapped is None:
        raise TranslationError(f"unmapped Codex tool: {native_name or '<missing>'}")
    return mapped


class CodexRawEventSources(HarnessRawEventSources):
    def __init__(self) -> None:
        self._known_rollout_paths: tuple[str, ...] = ()
        self._child_rollouts: dict[str, tuple[str, ...]] = {}
        self._next_child: dict[str, int] = {}

    def _next_child_rollout(self, parent_harness_session_id: str) -> tuple[str, ...]:
        rollout_paths = _rollout_paths()
        if rollout_paths != self._known_rollout_paths:
            children: dict[str, list[str]] = {}
            for rollout_path in rollout_paths:
                parent_id = _parent_thread_id(_session_metadata(rollout_path))
                if parent_id:
                    children.setdefault(parent_id, []).append(rollout_path)
            self._known_rollout_paths = rollout_paths
            self._child_rollouts = {
                parent_id: tuple(paths)
                for parent_id, paths in children.items()
            }
        child_rollouts = self._child_rollouts.get(parent_harness_session_id, ())
        if not child_rollouts:
            return ()
        position = self._next_child.get(parent_harness_session_id, 0) % len(child_rollouts)
        self._next_child[parent_harness_session_id] = position + 1
        return (child_rollouts[position],)

    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        sources: list[HarnessRawEventSource] = []
        owns_lead_session = lead_rollout(session.source_reference)
        if owns_lead_session:
            sources.append(CodexRolloutRawEventSource(session.source_context))
        for child_path in self._next_child_rollout(session.harness_session_id):
            child_body_position = rollout.subagent_body_offset(child_path)
            if child_body_position == 0:
                continue
            sources.append(
                CodexRolloutRawEventSource(
                    RawEventSourceContext(
                        session_id=session.session_id,
                        lead_actor_id=session.lead_actor_id,
                        actor_id=ActorId(_harness_session_id(child_path)),
                        parent_actor_id=session.lead_actor_id,
                        source_reference=child_path,
                    ),
                    child_body_position,
                    "child" if owns_lead_session else "sidecar",
                )
            )
        return tuple(sources)


class CodexCanonicalTranslator(HarnessTranslator):
    def __init__(self) -> None:
        self._collaboration_calls: dict[tuple[str, str], tuple[str, dict]] = {}
        self._process_operations: dict[tuple[str, str], OperationId] = {}
        self._continuation_operations: dict[tuple[str, str], OperationId] = {}
        self._finished_operations: set[tuple[str, OperationId]] = set()
        self._semantic_tool_calls: set[tuple[str, str]] = set()
        self._plan_tasks: dict[tuple[str, str], dict[TaskId, TaskChanged]] = {}

    @staticmethod
    def _source_key(raw_event: RawEvent) -> str:
        return os.path.realpath(raw_event.source_name)

    @staticmethod
    def _collaboration_call_from_document(document: dict, call_id: str) -> tuple[str, dict] | None:
        payload = document.get("payload") or {}
        if not (
            document.get("type") == "response_item"
            and payload.get("type") == "function_call"
            and payload.get("call_id") == call_id
            and payload.get("name") in {
                "spawn_agent",
                "send_message",
                "followup_task",
                "wait_agent",
                "interrupt_agent",
                "list_agents",
            }
        ):
            return None
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            arguments = {}
        return str(payload["name"]), arguments if isinstance(arguments, dict) else {}

    def _collaboration_call(self, raw_event: RawEvent, call_id: str) -> tuple[str, dict] | None:
        """Resolve the preceding call without scanning historical rollout data."""
        source_path = os.path.realpath(raw_event.source_name)
        key = (source_path, call_id)
        remembered = self._collaboration_calls.get(key)
        if remembered is not None:
            return remembered
        try:
            end_position = int(raw_event.source_position)
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
                        call = self._collaboration_call_from_document(document, call_id)
                        if call is not None:
                            self._collaboration_calls[key] = call
                            return call
                    end_position = start_position
        except (OSError, ValueError):
            return None
        return None

    def translate(self, raw_event: RawEvent) -> TranslationResult:
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
                    "ignored_nonsemantic",
                    "subagent delivery; its activity arrives through the lead's rollout",
                )
            events = self._translate_hook(raw_event, document)
            if events:
                return TranslationResult(tuple(events), "translated")
            return TranslationResult((), "ignored_nonsemantic", "hook carries no unique canonical activity")

        if raw_event.source_type in ("child_replay", "sidecar_replay"):
            return TranslationResult(
                (),
                "ignored_nonsemantic",
                "parent history replayed in child rollout",
            )

        if document.get("type") == "session_meta":
            if raw_event.source_position != "0":
                return TranslationResult((), "ignored_nonsemantic", "replayed session metadata")
            metadata = document.get("payload") or {}
            if raw_event.parent_actor_id is not None:
                role = "sidecar" if raw_event.source_type == "sidecar_rollout" else "child"
                source = metadata.get("source") or {}
                spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
                actor_name = str(spawn.get("agent_path") or "").rsplit("/", 1)[-1]
                actor_name = actor_name.replace("_", " ").strip() or "codex"
                actor_started = self._event(
                    raw_event,
                    "actor",
                    str(raw_event.actor_id),
                    "started",
                    ActorStarted(actor_name, role),
                    occurred_at=_timestamp(document.get("timestamp")),
                )
                return TranslationResult((actor_started,), "translated")
            return TranslationResult(
                tuple(self._session_started_events(
                    raw_event,
                    metadata.get("cwd") or "",
                    os.path.realpath(raw_event.source_name),
                )),
                "translated",
            )

        record = rollout.parse(document)
        if record is None:
            return TranslationResult((), "ignored_unknown", f"unhandled Codex record {document.get('type')!r}")
        if record.get("kind") == "bad":
            raise TranslationError("malformed Codex rollout record", context=raw_event.source_position)

        events = self._translate_record(raw_event, document, record)
        if not events:
            return TranslationResult((), "ignored_nonsemantic", f"nonsemantic Codex record {record['kind']!r}")
        return TranslationResult(tuple(events), "translated")

    def _translate_hook(self, raw_event: RawEvent, document: dict) -> list[CanonicalEvent]:
        hook_name = str(document.get("hook_event_name") or "")
        native_identity = str(
            document.get("hook_event_id")
            or document.get("uuid")
            or raw_event.source_position
        )
        if hook_name == "SessionStart":
            path = str(document.get("transcript_path") or "")
            if not lead_rollout(path):
                # A subagent thread announces no session of its own.
                return []
            return self._session_started_events(
                raw_event,
                document.get("cwd") or "",
                os.path.realpath(path),
            )
        if hook_name == "PreCompact":
            before_tokens = document.get("before_tokens")
            payload = CompactionStarted(before_tokens if isinstance(before_tokens, int) else None)
            return [self._event(raw_event, "compaction", native_identity, "started", payload)]
        if hook_name == "PostCompact":
            before_tokens = document.get("before_tokens")
            after_tokens = document.get("after_tokens")
            payload = CompactionFinished(
                before_tokens if isinstance(before_tokens, int) else None,
                after_tokens if isinstance(after_tokens, int) else None,
            )
            return [self._event(raw_event, "compaction", native_identity, "finished", payload)]
        return []

    def _session_started_events(
        self,
        raw_event: RawEvent,
        working_directory: str,
        source_reference: str,
        *,
        occurred_at: float | None = None,
    ) -> list[CanonicalEvent]:
        return [
            self._event(
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
            self._event(
                raw_event,
                "actor",
                str(raw_event.actor_id),
                "started",
                ActorStarted("codex", "lead"),
                occurred_at=occurred_at,
            ),
        ]

    def _translate_record(self, raw_event: RawEvent, document: dict, record: dict) -> list[CanonicalEvent]:
        kind = record["kind"]
        native_payload = document.get("payload") or {}
        native_identity = str(
            record.get("call_id")
            or native_payload.get("id")
            or native_payload.get("item_id")
            or raw_event.source_position
        )
        occurred_at = _timestamp(document.get("timestamp"))
        if occurred_at is None:
            occurred_at = _timestamp(record.get("at"))

        if kind == "task_started":
            turn_id = TurnId(record.get("turn") or f"{raw_event.session_id}:{native_identity}")
            events = [self._event(raw_event, "turn", str(turn_id), "started", TurnStarted(None), turn_id, occurred_at)]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                metadata = _session_metadata(raw_event.source_name)
                source = metadata.get("source") or {}
                spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
                actor_path = str(spawn.get("agent_path") or "")
                actor_name = actor_path.rsplit("/", 1)[-1].replace("_", " ").strip()
                assignment_id = AssignmentId(str(turn_id))
                # No prompt: the task payload is encrypted_content in the child
                # rollout, unreadable by design (rollout.subagent_brief).
                events.append(self._event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "started",
                    ActorAssignmentStarted(
                        assignment_id,
                        _content(actor_name or "actor assignment"),
                        actor_name=actor_name or None,
                    ),
                    turn_id,
                    occurred_at,
                ))
            return events
        if kind == "task_complete":
            turn_id = TurnId(record.get("turn") or f"{raw_event.session_id}:{native_identity}")
            events = [
                self._event(
                    raw_event,
                    "turn",
                    str(turn_id),
                    "finished",
                    TurnFinished(None, "succeeded"),
                    turn_id,
                    occurred_at,
                )
            ]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                assignment_id = AssignmentId(str(turn_id))
                result = _content(record.get("last"), markdown=True) if record.get("last") else None
                events.append(
                    self._event(
                        raw_event,
                        "actor_assignment",
                        str(assignment_id),
                        "finished",
                        ActorAssignmentFinished(assignment_id, "succeeded", result, None),
                        turn_id,
                        occurred_at,
                    )
                )
            return events
        if kind == "turn_aborted":
            turn_id = TurnId(str(record.get("turn") or native_payload.get("turn_id") or native_identity))
            events = [self._event(raw_event, "turn", str(turn_id), "aborted", TurnAborted(None), turn_id, occurred_at)]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                assignment_id = AssignmentId(str(turn_id))
                events.append(self._event(
                    raw_event,
                    "actor_assignment",
                    str(assignment_id),
                    "finished",
                    ActorAssignmentFinished(assignment_id, "cancelled", None, "interrupted"),
                    turn_id,
                    occurred_at,
                ))
            return events
        if kind in ("prompt", "message", "chat"):
            role = record.get("role") or ("user" if kind == "prompt" else "assistant")
            if role not in ("user", "assistant", "system"):
                role = "system"
            synthetic = bool(record.get("synthetic"))
            if synthetic:
                role = "system"
            phase = "synthetic" if synthetic else None
            if kind == "prompt" and not synthetic:
                phase = "prompt"
            elif role == "user" and phase is None:
                phase = "prompt"
            elif record.get("phase") == rollout.PHASE_FINAL:
                phase = "final"
            elif role == "assistant":
                phase = "intermediate"
            message_id = MessageId(native_identity)
            content = _content(record["text"], markdown=role == "assistant")
            payload = MessageCreated(message_id, role, content, phase, None)
            turn_id = TurnId(record["turn"]) if record.get("turn") else None
            return [self._event(
                raw_event,
                "message",
                native_identity,
                "created",
                payload,
                turn_id,
                occurred_at,
            )]
        if kind in ("reasoning", "think"):
            payload = ReasoningCreated(native_identity, _content(record["text"], markdown=True), kind == "think")
            return [self._event(raw_event, "reasoning", native_identity, "created", payload, occurred_at=occurred_at)]
        if kind == "collaboration_call":
            call_id = str(record.get("call_id") or "")
            self._collaboration_calls[(os.path.realpath(raw_event.source_name), call_id)] = (
                str(record["name"]),
                record.get("args") if isinstance(record.get("args"), dict) else {},
            )
            return []
        if kind == "actor_activity":
            call_id = str(record.get("call_id") or "")
            call = self._collaboration_call(raw_event, call_id)
            if call is None:
                raise TranslationError(f"Codex actor activity has no collaboration call: {call_id or '<missing>'}")
            call_name, _arguments = call
            activity = record.get("activity")
            expected_calls = {
                "started": "spawn_agent",
                "interrupted": "interrupt_agent",
            }
            expected_call = expected_calls.get(str(activity))
            if expected_call is not None and call_name != expected_call:
                raise TranslationError(f"Codex actor activity {activity!r} came from {call_name!r}")
            if activity == "interacted":
                if call_name == "followup_task":
                    return []
                if call_name != "send_message":
                    raise TranslationError(f"Codex actor interaction came from {call_name!r}")
                message_id = MessageId(call_id)
                payload = ActorMessageSent(message_id, ActorId(str(record["actor_id"])), None)
                return [self._event(
                    raw_event,
                    "actor_message",
                    str(message_id),
                    "sent",
                    payload,
                    TurnId(record["turn"]) if record.get("turn") else None,
                    occurred_at,
                )]
            if activity in ("started", "interrupted"):
                return []
            raise TranslationError(f"unknown Codex actor activity: {activity!r}")
        if kind == "unmapped_tool":
            raise TranslationError(f"unmapped Codex tool: {record.get('name') or '<missing>'}")
        if kind == "goal":
            native_state = str(record.get("status") or "")
            states = {
                "active": "active",
                "paused": "paused",
                "blocked": "blocked",
                "usageLimited": "usage_limited",
                "budgetLimited": "budget_limited",
                "complete": "completed",
                "cleared": "cleared",
            }
            state = states.get(native_state)
            if state is None:
                raise TranslationError(f"unknown Codex goal state: {native_state or '<missing>'}")
            objective = str(record.get("objective") or "").strip() or None
            if state != "cleared" and objective is None:
                raise TranslationError("Codex goal has no objective")
            payload = GoalChanged(objective, state, str(record.get("reason") or "").strip() or None)
            return [self._event(
                raw_event,
                "goal",
                native_identity,
                "changed",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "goal_tool":
            call_id = str(record.get("call_id") or native_identity)
            self._semantic_tool_calls.add((self._source_key(raw_event), call_id))
            return []
        if kind == "task_list":
            call_id = str(record.get("call_id") or native_identity)
            source_key = self._source_key(raw_event)
            self._semantic_tool_calls.add((source_key, call_id))
            plan_key = (str(raw_event.session_id), str(raw_event.actor_id))
            previous = self._plan_tasks.get(plan_key, {})
            current: dict[TaskId, TaskChanged] = {}
            for task_index, task in enumerate(record.get("tasks") or (), start=1):
                if not isinstance(task, dict):
                    raise TranslationError("Codex plan task is not an object")
                subject = str(task.get("step") or "").strip()
                state = task.get("status")
                if not subject:
                    raise TranslationError("Codex plan task has no step")
                if state not in ("pending", "in_progress", "completed"):
                    raise TranslationError(f"unknown Codex plan task state: {state!r}")
                task_id = TaskId(f"{raw_event.actor_id}:plan:{task_index}")
                current[task_id] = TaskChanged(
                    task_id,
                    str(task_index),
                    subject,
                    None,
                    state,
                    raw_event.actor_id,
                )
            events = [self._event(
                raw_event,
                "task_list",
                str(raw_event.actor_id),
                f"changed:{call_id}",
                TaskListChanged(str(raw_event.actor_id), tuple(current)),
                occurred_at=occurred_at,
            )]
            for task_id, task in current.items():
                if previous.get(task_id) == task:
                    continue
                events.append(self._event(
                    raw_event, "task", str(task_id), f"changed:{call_id}", task,
                    occurred_at=occurred_at,
                ))
            self._plan_tasks[plan_key] = current
            return events
        if kind in ("exec", "tool"):
            operation_id = OperationId(record.get("call_id") or native_identity)
            if kind == "exec":
                category = "shell"
                name = "exec"
            else:
                category, name = _codex_tool(record.get("name") or "", record.get("args"))
            arguments = record.get("cmd") or record.get("args")
            payload = OperationStarted(operation_id, category, name, "foreground", _content(arguments), None, None)
            return [self._event(raw_event, "operation", str(operation_id), "started", payload, occurred_at=occurred_at)]
        if kind == "stdin":
            process_id = str(record.get("process_id") or "")
            if not process_id:
                raise TranslationError("Codex write_stdin has no process session")
            source_key = self._source_key(raw_event)
            operation_id = self._process_operations.get((source_key, process_id))
            if operation_id is None:
                raise TranslationError(f"Codex write_stdin references unknown process session: {process_id}")
            call_id = str(record.get("call_id") or native_identity)
            self._continuation_operations[(source_key, call_id)] = operation_id
            text = str(record.get("text") or "")
            if not text:
                return []
            if (source_key, operation_id) in self._finished_operations:
                raise TranslationError(f"Codex write_stdin targets finished operation: {operation_id}")
            payload = OperationInputProvided(operation_id, _content(text), False)
            return [self._event(
                raw_event,
                "operation",
                str(operation_id),
                f"input:{call_id}",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "exec_result":
            call_id = str(record.get("call_id") or native_identity)
            source_key = self._source_key(raw_event)
            if (source_key, call_id) in self._semantic_tool_calls:
                return []
            continued_operation = self._continuation_operations.get((source_key, call_id))
            if continued_operation is not None:
                if (source_key, continued_operation) in self._finished_operations:
                    return []
                output = str(record.get("output") or "")
                if not output:
                    return []
                ordinal = int(raw_event.source_position)
                payload = OperationProgressed(
                    continued_operation,
                    ordinal,
                    "output",
                    _content(output),
                    "append",
                )
                return [self._event(
                    raw_event,
                    "operation",
                    str(continued_operation),
                    f"progress:{ordinal}",
                    payload,
                    occurred_at=occurred_at,
                )]
            if self._collaboration_call(raw_event, call_id) is not None:
                return []
            operation_id = OperationId(call_id)
            exit_code = _exit_code(record)
            process_id = str(record.get("process_id") or "")
            if process_id:
                self._process_operations[(source_key, process_id)] = operation_id
            if record.get("running"):
                output = str(record.get("output") or "")
                if not output:
                    return []
                ordinal = int(raw_event.source_position)
                payload = OperationProgressed(operation_id, ordinal, "output", _content(output), "append")
                return [self._event(
                    raw_event,
                    "operation",
                    str(operation_id),
                    f"progress:{ordinal}",
                    payload,
                    occurred_at=occurred_at,
                )]
            outcome = "succeeded" if exit_code in (None, 0) else "failed"
            payload = OperationFinished(operation_id, outcome, _content(record.get("output")), exit_code)
            self._finished_operations.add((source_key, operation_id))
            return [
                self._event(
                    raw_event,
                    "operation",
                    str(operation_id),
                    "finished",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        if kind == "command_completed":
            source_key = self._source_key(raw_event)
            process_id = str(record.get("process_id") or "")
            operation_id = self._process_operations.get((source_key, process_id))
            if operation_id is None or (source_key, operation_id) in self._finished_operations:
                return []
            exit_code = _exit_code(record)
            outcome = "succeeded" if exit_code == 0 else "failed"
            self._finished_operations.add((source_key, operation_id))
            payload = OperationFinished(operation_id, outcome, _content(record.get("output")), exit_code)
            return [self._event(
                raw_event,
                "operation",
                str(operation_id),
                "finished",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "search":
            return self._instant_operation(
                raw_event,
                native_identity,
                "search",
                "web_search",
                record["query"],
                occurred_at,
            )
        if kind == "patch":
            operation_id = OperationId(native_identity)
            events = self._instant_operation(
                raw_event,
                native_identity,
                "file_edit",
                "apply_patch",
                record.get("files") or [],
                occurred_at,
                succeeded=record.get("success", False),
            )
            finished_event = events.pop()
            action_by_change = {
                "add": "created",
                "delete": "deleted",
                "move": "renamed",
                "update": "updated",
            }
            for file_order, file_record in enumerate(record.get("files") or ()):
                path = file_record.get("path") or ""
                payload = FileAccessed(
                    operation_id=operation_id,
                    path=path,
                    action=action_by_change.get(file_record.get("change"), "updated"),
                    previous_path=file_record.get("previous_path"),
                    lines_added=file_record.get("added"),
                    lines_removed=file_record.get("removed"),
                    unified_diff=file_record.get("diff"),
                    content=(
                        _content(file_record["content"])
                        if file_record.get("content") is not None
                        else None
                    ),
                )
                events.append(
                    self._event(
                        raw_event,
                        "file",
                        f"{native_identity}:{file_order}:{path}",
                        "accessed",
                        payload,
                        occurred_at=occurred_at,
                    )
                )
            events.append(finished_event)
            return events
        if kind == "usage":
            usage = record["usage"]
            tokens = TokenUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_tokens=int(usage.get("cached_input_tokens") or 0),
            )
            events = [
                self._event(
                    raw_event,
                    "usage",
                    native_identity,
                    "reported",
                    UsageReported("session", str(raw_event.session_id), None, None, tokens, True, None),
                    occurred_at=occurred_at,
                )
            ]
            if record.get("last") and record.get("window"):
                last = record["last"]
                used_tokens = int(last.get("total_tokens") or 0)
                events.append(
                    self._event(
                        raw_event,
                        "context",
                        native_identity,
                        "reported",
                        ContextReported(used_tokens, int(record["window"]), None),
                        occurred_at=occurred_at,
                    )
                )
            return events
        if kind in ("turn_context", "settings"):
            events = []
            if record.get("model"):
                model = _model_reference(record["model"])
                events.append(
                    self._event(
                        raw_event,
                        "model",
                        native_identity,
                        "changed",
                        ModelChanged(None, model, "reported_by_harness"),
                        occurred_at=occurred_at,
                    )
                )
            if record.get("effort"):
                events.append(
                    self._event(
                        raw_event,
                        "effort",
                        native_identity,
                        "changed",
                        EffortChanged(None, record["effort"], "reported_by_harness"),
                        occurred_at=occurred_at,
                    )
                )
            return events
        if kind in ("compact", "compact_boundary"):
            return [
                self._event(
                    raw_event,
                    "compaction",
                    native_identity,
                    "finished",
                    CompactionFinished(None, None),
                    occurred_at=occurred_at,
                )
            ]
        if kind == "ask":
            prompts = tuple(
                AttentionPrompt(
                    prompt_id=question.get("id") or str(index),
                    title=question.get("header") or None,
                    prompt=question.get("question") or "",
                    multiple=False,
                    choices=tuple(
                        AttentionChoice(
                            option.get("label") or "",
                            option.get("label") or "",
                            option.get("description") or None,
                        )
                        for option in question.get("options") or ()
                    ),
                )
                for index, question in enumerate(record.get("questions") or ())
            )
            attention_id = AttentionId(record.get("call_id") or native_identity)
            payload = AttentionRequested(attention_id, "question", prompts, None)
            return [
                self._event(
                    raw_event,
                    "attention",
                    str(attention_id),
                    "requested",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        if kind == "plan":
            attention_id = AttentionId(record.get("id") or native_identity)
            payload = AttentionRequested(
                attention_id,
                "plan",
                (
                    AttentionPrompt(
                        "plan",
                        "Plan",
                        record.get("text") or "",
                        False,
                        (),
                    ),
                ),
                None,
            )
            return [
                self._event(
                    raw_event,
                    "attention",
                    str(attention_id),
                    "requested",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        return []

    def _instant_operation(
        self,
        raw_event: RawEvent,
        native_identity: str,
        category,
        native_name: str,
        arguments,
        occurred_at: float | None,
        *,
        succeeded: bool = True,
    ) -> list[CanonicalEvent]:
        operation_id = OperationId(native_identity)
        started = OperationStarted(operation_id, category, native_name, "foreground", _content(arguments), None, None)
        finished = OperationFinished(operation_id, "succeeded" if succeeded else "failed", None, None)
        return [
            self._event(raw_event, "operation", native_identity, "started", started, occurred_at=occurred_at),
            self._event(raw_event, "operation", native_identity, "finished", finished, occurred_at=occurred_at),
        ]

    @staticmethod
    def _event(
        raw_event: RawEvent,
        subject_type: str,
        subject_id: str,
        phase: str,
        payload,
        turn_id: TurnId | None = None,
        occurred_at: float | None = None,
    ) -> CanonicalEvent:
        return canonical_event(
            raw_event, subject_type, subject_id, phase, payload,
            turn_id=turn_id, occurred_at=occurred_at,
        )
