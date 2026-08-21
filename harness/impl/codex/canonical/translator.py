"""Codex canonical translation: dispatch across the rollout's records and hooks."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal, TypeAlias

from harness.contract import HarnessTranslator
from harness.models import RawEvent, TranslationError, TranslationResult, UnknownRawEvent
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
    FileAction,
    GoalState,
    MessagePhase,
    MessageRole,
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
    model_reference,
    outcome_of,
    timestamp,
)
from harness.models.selections import SelectionSemantics


# What one of Codex's non-shell tool calls IS. `ignored` is named rather than
# left to fall through: a generated image exposes no readable path to put on a
# file fact, so there is nothing to record about it.
CodexToolKind: TypeAlias = Literal["search", "web", "file", "ignored"]


def _codex_tool(
    native_name: str,
    arguments: str | dict[str, Any] | None,  # loose: codex JSON, wave 2 gives it a real shape
) -> tuple[CodexToolKind, str]:
    """Map Codex transport names onto the canonical vocabulary.

    A name with no fact behind it raises `UnknownRawEvent`: the delivery is
    verdicted `ignored_unknown` — visible in the audit, absent from the feed —
    rather than failing the whole record.
    """
    if native_name == "web__run":
        if not isinstance(arguments, str):
            fields = arguments
        else:
            try:
                fields = json.loads(arguments)
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
            return "web", "WebFetch"
        # A time lookup is neither a search nor a fetch: it has no query, no url
        # and no reader.
        raise UnknownRawEvent("unmapped Codex web action")
    mapping: dict[str, tuple[CodexToolKind, str]] = {
        "view_image": ("file", "ReadImage"),
        "image_gen__imagegen": ("ignored", "GenerateImage"),
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


def _tool_fields(
    arguments: str | dict[str, Any] | None,  # loose: codex JSON, wave 2 gives it a real shape
) -> dict[str, Any]:  # loose: codex JSON, wave 2 gives it a real shape
    """A Codex tool call's arguments as fields.

    Three spellings arrive: a dict, JSON text, and a JavaScript object literal
    with unquoted keys. The last is read for its STRING fields only — which is
    every field anything below wants — rather than interpreted.
    """
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
    except (TypeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    return {
        match.group(1): match.group(2).encode().decode("unicode_escape")
        for match in _JS_STRING_FIELD.finditer(str(arguments or ""))
    }


def _search_query(arguments: str | dict[str, Any] | None) -> Content:  # loose: codex JSON, wave 2 gives it a real shape
    """What was searched for. The whole argument blob is the fallback: a query
    nobody can read is still a better raw event than an empty one."""
    fields = _tool_fields(arguments)
    for name in _SEARCH_QUERY_FIELDS:
        value = fields.get(name)
        if isinstance(value, str) and value:
            return content(value)
    return content(arguments)


def _web_url(arguments: str | dict[str, Any] | None) -> str | None:  # loose: codex JSON, wave 2 gives it a real shape
    """The address a fetch was for, when the call names one. Codex's `open` is
    often an index into a previous search's results rather than an address, so
    only something that reads as one counts."""
    for value in _tool_fields(arguments).values():
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _tool_path(arguments: str | dict[str, Any] | None) -> str:  # loose: codex JSON, wave 2 gives it a real shape
    fields = _tool_fields(arguments)
    for name in ("path", "file_path"):
        value = fields.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


class CodexCanonicalTranslator(HarnessTranslator):
    def __init__(self) -> None:
        self._collaboration_calls: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self._process_shells: dict[tuple[str, str], ShellId] = {}
        self._continuation_shells: dict[tuple[str, str], ShellId] = {}
        self._finished_shells: set[tuple[str, ShellId]] = set()
        # Announced background once. An exec that outlived its yield is reported
        # again by every continuation poll, and the fact is about the command,
        # not about the poll that observed it.
        self._backgrounded_shells: set[tuple[str, ShellId]] = set()
        self._semantic_tool_calls: set[tuple[str, str]] = set()
        self._call_records: dict[tuple[str, str], dict[str, Any] | None] = {}
        self._plan_tasks: dict[tuple[str, str], dict[TaskId, TaskChanged]] = {}
        self._selections = SelectionSemantics()

    @staticmethod
    def _source_key(raw_event: RawEvent) -> str:
        return os.path.realpath(raw_event.source_name)

    @staticmethod
    def _collaboration_call_from_document(
        document: dict[str, Any], call_id: CallId  # loose: codex JSON, wave 2 gives it a real shape
    ) -> tuple[str, dict[str, Any]] | None:  # loose: codex JSON, wave 2 gives it a real shape
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

    def _collaboration_call(
        self,
        raw_event: RawEvent,
        call_id: CallId,
    ) -> tuple[str, dict[str, Any]] | None:  # loose: codex JSON, wave 2 gives it a real shape
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

    @staticmethod
    def _call_from_document(
        document: dict[str, Any],  # loose: codex JSON, wave 2 gives it a real shape
        call_id: CallId,
    ) -> dict[str, Any] | bool | None:  # loose: codex JSON, wave 2 gives it a real shape
        """The parsed call this output belongs to.

        None means this is not the call being sought; False means it is the
        call, but its grammar is deliberately nonsemantic/unsupported. A record
        rather than a bare yes: what the output MEANS is the call's kind and
        arguments — a command's exit, or a search's results — and only the call
        carries them.
        """
        payload = document.get("payload") or {}
        if not (
            document.get("type") == "response_item"
            and payload.get("type") in ("function_call", "custom_tool_call")
            and payload.get("call_id") == call_id
        ):
            return None
        record = rollout.parse(document)
        if record is None or record.get("kind") not in ("exec", "tool"):
            return False
        return record

    def _call_record(
        self,
        raw_event: RawEvent,
        call_id: CallId,
    ) -> dict[str, Any] | None:  # loose: codex JSON, wave 2 gives it a real shape
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
                        opened = self._call_from_document(document, call_id)
                        if opened is not None:
                            found = opened if isinstance(opened, dict) else None
                            self._call_records[key] = found
                            return found
                    end_position = start_position
        except (OSError, ValueError):
            pass
        self._call_records[key] = None
        return None

    def translate(self, raw_event: RawEvent) -> TranslationResult:
        try:
            return self._translate(raw_event)
        except UnknownRawEvent as unknown:
            return TranslationResult((), "ignored_unknown", unknown.reason)

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

    def _translate_hook(
        self,
        raw_event: RawEvent,
        document: dict[str, Any],  # loose: codex JSON, wave 2 gives it a real shape
    ) -> list[CanonicalEvent[EventPayload]]:
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
                ActorStarted("codex", "lead"),
                occurred_at=occurred_at,
            ),
        ]

    def _translate_record(
        self,
        raw_event: RawEvent,
        document: dict[str, Any],  # loose: codex JSON, wave 2 gives it a real shape
        record: dict[str, Any],  # loose: codex JSON, wave 2 gives it a real shape
    ) -> list[CanonicalEvent[EventPayload]]:
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
                phase = "end_turn"
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
            payload = ReasoningCreated(ReasoningId(native_identity), content(record["text"], markdown=True))
            return [event(raw_event, "reasoning", native_identity, "created", payload, occurred_at=occurred_at)]
        if kind == "collaboration_call":
            call_id = CallId(str(record.get("call_id") or ""))
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
            call_id = CallId(str(record.get("call_id") or ""))
            call = self._collaboration_call(raw_event, call_id)
            if call is None:
                raise TranslationError(f"Codex actor activity has no collaboration call: {call_id or '<missing>'}")
            call_name, call_arguments = call
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
                # The text is in the call's own arguments, which used to be
                # fetched and dropped: an actor-to-actor message with no message
                # is a fact about nothing.
                spoken = call_arguments.get("message") or call_arguments.get("content") or ""
                payload = MessageCreated(
                    message_id,
                    "assistant",
                    content(spoken, markdown=True),
                    "intermediate",
                    None,
                    ActorId(str(record["actor_id"])),
                )
                return [event(
                    raw_event,
                    "message",
                    str(message_id),
                    "created",
                    payload,
                    TurnId(record["turn"]) if record.get("turn") else None,
                    occurred_at,
                )]
            if activity in ("started", "interrupted"):
                return []
            raise TranslationError(f"unknown Codex actor activity: {activity!r}")
        if kind == "unmapped_tool":
            raise UnknownRawEvent(f"unmapped Codex tool: {record.get('name') or '<missing>'}")
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
            call_id = CallId(str(record.get("call_id") or native_identity))
            self._semantic_tool_calls.add((self._source_key(raw_event), call_id))
            return []
        if kind == "task_list":
            call_id = CallId(str(record.get("call_id") or native_identity))
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
                TaskListChanged(TaskListId(str(raw_event.actor_id)), tuple(current)),
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
            call_id = CallId(str(record.get("call_id") or native_identity))
            # Remembered whichever kind it is: the output that lands later is
            # only meaningful as this call's output (see `_call_record`).
            self._call_records[(self._source_key(raw_event), call_id)] = record
            if kind == "tool":
                # A search, a fetch or a file read is one fact at result time —
                # its query and what came back of it are the same fact, and the
                # call alone is half of it. Validated here so an unmapped tool is
                # reported at the CALL, where the name is.
                _codex_tool(record.get("name") or "", record.get("args"))
                return []
            shell_id = ShellId(call_id)
            payload = ShellStarted(shell_id, content(record.get("cmd")), "foreground", None)
            return [event(raw_event, "shell", str(shell_id), "started", payload, occurred_at=occurred_at)]
        if kind == "stdin":
            process_id = str(record.get("process_id") or "")
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
            call_id = CallId(str(record.get("call_id") or native_identity))
            self._continuation_shells[(source_key, call_id)] = shell_id
            text = str(record.get("text") or "")
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
        if kind == "exec_result":
            call_id = CallId(str(record.get("call_id") or native_identity))
            source_key = self._source_key(raw_event)
            if (source_key, call_id) in self._semantic_tool_calls:
                return []
            continued_shell = self._continuation_shells.get((source_key, call_id))
            if continued_shell is not None:
                if (source_key, continued_shell) in self._finished_shells:
                    return []
                output = str(record.get("output") or "")
                if not output:
                    return []
                ordinal = int(raw_event.source_position)
                payload = ShellProgressed(
                    continued_shell,
                    ordinal,
                    "output",
                    content(output),
                    "append",
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
            if call_record.get("kind") == "tool":
                return self._tool_result(raw_event, call_id, call_record, record, occurred_at)
            shell_id = ShellId(call_id)
            process_exit_code = exit_code(record)
            process_id = str(record.get("process_id") or "")
            if process_id:
                self._process_shells[(source_key, process_id)] = shell_id
            if record.get("running"):
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
                output = str(record.get("output") or "")
                if output:
                    ordinal = int(raw_event.source_position)
                    running_events.append(event(
                        raw_event,
                        "shell",
                        str(shell_id),
                        f"progress:{ordinal}",
                        ShellProgressed(shell_id, ordinal, "output", content(output), "append"),
                        occurred_at=occurred_at,
                    ))
                return running_events
            outcome: Outcome = "succeeded" if process_exit_code in (None, 0) else "failed"
            payload = ShellFinished(shell_id, outcome, content(record.get("output")), process_exit_code)
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
        if kind == "command_completed":
            source_key = self._source_key(raw_event)
            process_id = str(record.get("process_id") or "")
            # Same reason as the write_stdin branch above: the lookup is
            # optional, the id every line after it uses is not.
            completed_shell_id = self._process_shells.get((source_key, process_id))
            if completed_shell_id is None or (source_key, completed_shell_id) in self._finished_shells:
                return []
            shell_id = completed_shell_id
            process_exit_code = exit_code(record)
            outcome = "succeeded" if process_exit_code == 0 else "failed"
            self._finished_shells.add((source_key, shell_id))
            payload = ShellFinished(shell_id, outcome, content(record.get("output")), process_exit_code)
            return [event(
                raw_event,
                "shell",
                str(shell_id),
                "finished",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "search":
            # Codex reports the query and nothing of what came back, so the
            # result is honestly absent rather than an empty string.
            payload = SearchPerformed("web_search", content(record["query"]), None, "succeeded")
            return [event(
                raw_event,
                "search",
                native_identity,
                "performed",
                payload,
                occurred_at=occurred_at,
            )]
        if kind == "patch":
            outcome = outcome_of(record.get("success", False))
            action_by_change: dict[str, FileAction] = {
                "add": "created",
                "delete": "deleted",
                "move": "renamed",
                "update": "updated",
            }
            events = []
            for file_order, file_record in enumerate(record.get("files") or ()):
                path = file_record.get("path") or ""
                payload = FileAccessed(
                    path=path,
                    action=action_by_change.get(file_record.get("change"), "updated"),
                    outcome=outcome,
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
            # Codex restates the whole turn context on every turn, so all but
            # the first restatement of one model is a change with nothing
            # changed; only a real transition survives `_selections`.
            events = []
            if record.get("model"):
                changed = self._selections.model(
                    raw_event.session_id,
                    raw_event.actor_id,
                    model_reference(ModelId(record["model"])),
                    "reported_by_harness",
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
            if record.get("effort"):
                chosen = self._selections.effort(
                    raw_event.session_id,
                    raw_event.actor_id,
                    record["effort"],
                    "reported_by_harness",
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
            questions = tuple(
                AttentionPrompt(
                    prompt_id=QuestionId(question.get("id") or str(index)),
                    title=question.get("header") or None,
                    prompt=question.get("question") or "",
                    multiple=False,
                    choices=tuple(
                        AttentionChoice(
                            option.get("label") or "",
                            option.get("description") or None,
                        )
                        for option in question.get("options") or ()
                    ),
                )
                for index, question in enumerate(record.get("questions") or ())
            )
            attention_id = AttentionId(record.get("call_id") or native_identity)
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
        if kind == "plan":
            attention_id = AttentionId(record.get("id") or native_identity)
            payload = PlanProposed(attention_id, content(record.get("text") or "", markdown=True))
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
        call_record: dict[str, Any],  # loose: codex JSON, wave 2 gives it a real shape
        result: dict[str, Any],  # loose: codex JSON, wave 2 gives it a real shape
        occurred_at: float | None,
    ) -> list[CanonicalEvent[EventPayload]]:
        """One non-shell tool call and its result, as the single fact it is.

        Both halves are here: the call's name and arguments come from the record
        that opened it, the outcome and the text from the record that closed it.
        """
        kind, native_name = _codex_tool(call_record.get("name") or "", call_record.get("args"))
        if kind == "ignored":
            return []
        arguments = call_record.get("args")
        output = str(result.get("output") or "")
        outcome: Outcome = "failed" if exit_code(result) not in (None, 0) else "succeeded"
        answered = content(output) if output else None
        if kind == "search":
            payload: EventPayload = SearchPerformed(
                native_name, _search_query(arguments), answered, outcome
            )
            return [event(raw_event, "search", call_id, "performed", payload, occurred_at=occurred_at)]
        if kind == "web":
            payload = WebFetched(_web_url(arguments), answered, outcome)
            return [event(raw_event, "web", call_id, "fetched", payload, occurred_at=occurred_at)]
        path = _tool_path(arguments)
        if not path:
            # No path is readable from the call, and a file fact whose path was
            # invented is worse than no fact.
            return []
        payload = FileAccessed(path=path, action="read", outcome=outcome)
        return [event(raw_event, "file", f"{call_id}:read:{path}", "accessed", payload, occurred_at=occurred_at)]
