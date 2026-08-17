"""Codex canonical translation: dispatch across the rollout's records and hooks."""

from __future__ import annotations

import json
import os
import re

from harness.contract import HarnessTranslator
from harness.models import RawEvent, TranslationError, TranslationResult
from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    ActorMessageSent,
    ActorStarted,
    AttentionRequested,
    CanonicalEvent,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    EffortChanged,
    EventPayload,
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
    TaskId,
    TurnId,
)
from domain.values import (
    ActorRole,
    AttentionChoice,
    AttentionPrompt,
    FileAction,
    GoalState,
    MessagePhase,
    MessageRole,
    OperationCategory,
    Outcome,
    TokenUsage,
)
from harness.impl.codex.canonical import rollout
from harness.impl.codex.canonical.events import PHASE_FINAL
from harness.impl.codex.canonical.sources import lead_rollout, session_metadata
from harness.impl.codex.canonical.support import (
    content,
    event,
    exit_code,
    instant_operation,
    model_reference,
    timestamp,
)


def _codex_tool(native_name: str, arguments) -> tuple[OperationCategory, str]:
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
    mapping: dict[str, tuple[OperationCategory, str]] = {
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
                role: ActorRole = "sidecar" if raw_event.source_type == "sidecar_rollout" else "child"
                source = metadata.get("source") or {}
                spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
                actor_name = str(spawn.get("agent_path") or "").rsplit("/", 1)[-1]
                actor_name = actor_name.replace("_", " ").strip() or "codex"
                actor_started = event(
                    raw_event,
                    "actor",
                    str(raw_event.actor_id),
                    "started",
                    ActorStarted(actor_name, role),
                    occurred_at=timestamp(document.get("timestamp")),
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
            payload: EventPayload = CompactionStarted(before_tokens if isinstance(before_tokens, int) else None)
            return [event(raw_event, "compaction", native_identity, "started", payload)]
        if hook_name == "PostCompact":
            before_tokens = document.get("before_tokens")
            after_tokens = document.get("after_tokens")
            payload = CompactionFinished(
                before_tokens if isinstance(before_tokens, int) else None,
                after_tokens if isinstance(after_tokens, int) else None,
            )
            return [event(raw_event, "compaction", native_identity, "finished", payload)]
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
        occurred_at = timestamp(document.get("timestamp"))
        if occurred_at is None:
            occurred_at = timestamp(record.get("at"))

        if kind == "task_started":
            turn_id = TurnId(record.get("turn") or f"{raw_event.session_id}:{native_identity}")
            events = [event(raw_event, "turn", str(turn_id), "started", TurnStarted(None), turn_id, occurred_at)]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                metadata = session_metadata(raw_event.source_name)
                source = metadata.get("source") or {}
                spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
                actor_path = str(spawn.get("agent_path") or "")
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
        if kind == "task_complete":
            turn_id = TurnId(record.get("turn") or f"{raw_event.session_id}:{native_identity}")
            events = [
                event(
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
                result = content(record.get("last"), markdown=True) if record.get("last") else None
                events.append(
                    event(
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
            events = [event(raw_event, "turn", str(turn_id), "aborted", TurnAborted(None), turn_id, occurred_at)]
            if raw_event.parent_actor_id is not None and raw_event.source_type == "child_rollout":
                assignment_id = AssignmentId(str(turn_id))
                events.append(event(
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
            # Declared, not inferred: both are read off native JSON and land in
            # a payload that accepts a closed set. The normalisation below
            # already rejects an unknown role — the annotation is what makes
            # that rejection checkable instead of incidental.
            role: MessageRole = "user" if kind == "prompt" else "assistant"
            native_role = record.get("role")
            if native_role in ("user", "assistant", "system"):
                role = native_role
            synthetic = bool(record.get("synthetic"))
            if synthetic:
                role = "system"
            phase: MessagePhase | None = "synthetic" if synthetic else None
            if kind == "prompt" and not synthetic:
                phase = "prompt"
            elif role == "user" and phase is None:
                phase = "prompt"
            elif record.get("phase") == PHASE_FINAL:
                phase = "final"
            elif role == "assistant":
                phase = "intermediate"
            message_id = MessageId(native_identity)
            message_content = content(record["text"], markdown=role == "assistant")
            payload: EventPayload = MessageCreated(message_id, role, message_content, phase, None)
            # A message need not belong to a turn; the bindings above in this
            # same function always do, so the name has to admit None here.
            message_turn_id: TurnId | None = TurnId(record["turn"]) if record.get("turn") else None
            return [event(
                raw_event,
                "message",
                native_identity,
                "created",
                payload,
                message_turn_id,
                occurred_at,
            )]
        if kind in ("reasoning", "think"):
            payload = ReasoningCreated(native_identity, content(record["text"], markdown=True), kind == "think")
            return [event(raw_event, "reasoning", native_identity, "created", payload, occurred_at=occurred_at)]
        if kind == "collaboration_call":
            call_id = str(record.get("call_id") or "")
            # Fetched once and then tested: the isinstance guard and the value
            # it guards were two separate .get() calls, so the check proved
            # nothing about the thing actually stored.
            call_arguments = record.get("args")
            self._collaboration_calls[(os.path.realpath(raw_event.source_name), call_id)] = (
                str(record["name"]),
                call_arguments if isinstance(call_arguments, dict) else {},
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
                return [event(
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
            # Typed so the table itself is checked: every value here has to be
            # a state GoalChanged accepts, and a typo in one of them used to
            # travel all the way into a stored fact.
            states: dict[str, GoalState] = {
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
            return [event(
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
            events = [event(
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
                events.append(event(
                    raw_event, "task", str(task_id), f"changed:{call_id}", task,
                    occurred_at=occurred_at,
                ))
            self._plan_tasks[plan_key] = current
            return events
        if kind in ("exec", "tool"):
            operation_id = OperationId(record.get("call_id") or native_identity)
            if kind == "exec":
                category: OperationCategory = "shell"
                name = "exec"
            else:
                category, name = _codex_tool(record.get("name") or "", record.get("args"))
            arguments = record.get("cmd") or record.get("args")
            payload = OperationStarted(operation_id, category, name, "foreground", content(arguments), None, None)
            return [event(raw_event, "operation", str(operation_id), "started", payload, occurred_at=occurred_at)]
        if kind == "stdin":
            process_id = str(record.get("process_id") or "")
            if not process_id:
                raise TranslationError("Codex write_stdin has no process session")
            source_key = self._source_key(raw_event)
            # A distinct name from the `operation_id` bound elsewhere in this
            # function: a lookup that can miss is not the same thing as an id
            # built from the record, and sharing one binding for both made the
            # non-optional uses depend on which branch ran.
            known_operation_id = self._process_operations.get((source_key, process_id))
            if known_operation_id is None:
                raise TranslationError(f"Codex write_stdin references unknown process session: {process_id}")
            operation_id = known_operation_id
            call_id = str(record.get("call_id") or native_identity)
            self._continuation_operations[(source_key, call_id)] = operation_id
            text = str(record.get("text") or "")
            if not text:
                return []
            if (source_key, operation_id) in self._finished_operations:
                raise TranslationError(f"Codex write_stdin targets finished operation: {operation_id}")
            payload = OperationInputProvided(operation_id, content(text), False)
            return [event(
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
                    content(output),
                    "append",
                )
                return [event(
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
            process_exit_code = exit_code(record)
            process_id = str(record.get("process_id") or "")
            if process_id:
                self._process_operations[(source_key, process_id)] = operation_id
            if record.get("running"):
                output = str(record.get("output") or "")
                if not output:
                    return []
                ordinal = int(raw_event.source_position)
                payload = OperationProgressed(operation_id, ordinal, "output", content(output), "append")
                return [event(
                    raw_event,
                    "operation",
                    str(operation_id),
                    f"progress:{ordinal}",
                    payload,
                    occurred_at=occurred_at,
                )]
            outcome: Outcome = "succeeded" if process_exit_code in (None, 0) else "failed"
            payload = OperationFinished(operation_id, outcome, content(record.get("output")), process_exit_code)
            self._finished_operations.add((source_key, operation_id))
            return [
                event(
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
            # Same reason as the write_stdin branch above: the lookup is
            # optional, the id every line after it uses is not.
            completed_operation_id = self._process_operations.get((source_key, process_id))
            if completed_operation_id is None or (source_key, completed_operation_id) in self._finished_operations:
                return []
            operation_id = completed_operation_id
            process_exit_code = exit_code(record)
            outcome = "succeeded" if process_exit_code == 0 else "failed"
            self._finished_operations.add((source_key, operation_id))
            payload = OperationFinished(operation_id, outcome, content(record.get("output")), process_exit_code)
            return [event(
                raw_event,
                "operation",
                str(operation_id),
                "finished",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "search":
            return instant_operation(
                raw_event,
                native_identity,
                "search",
                "web_search",
                record["query"],
                occurred_at,
            )
        if kind == "patch":
            operation_id = OperationId(native_identity)
            events = instant_operation(
                raw_event,
                native_identity,
                "file_edit",
                "apply_patch",
                record.get("files") or [],
                occurred_at,
                succeeded=record.get("success", False),
            )
            finished_event = events.pop()
            action_by_change: dict[str, FileAction] = {
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
                        content(file_record["content"])
                        if file_record.get("content") is not None
                        else None
                    ),
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
                event(
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
                    event(
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
                model = model_reference(record["model"])
                events.append(
                    event(
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
                    event(
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
                event(
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
                event(
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
                event(
                    raw_event,
                    "attention",
                    str(attention_id),
                    "requested",
                    payload,
                    occurred_at=occurred_at,
                )
            ]
        return []
