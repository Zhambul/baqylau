"""Closed canonical event vocabulary from the architecture proposal."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    AssignmentId,
    MessageId,
    OperationId,
    SessionId,
    TaskId,
    TurnId,
)
from domain.values import (
    AccountReference,
    ActorRole,
    AttentionAnswer,
    AttentionPrompt,
    Content,
    ExecutionMode,
    ModelReference,
    OperationCategory,
    Outcome,
    TokenUsage,
)


@dataclass(frozen=True)
class EventPayload:
    """Marker base for semantic payloads."""


@dataclass(frozen=True)
class SessionStarted(EventPayload):
    working_directory: str
    resumed_from: SessionId | None
    title: str | None
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None


@dataclass(frozen=True)
class SessionTitleChanged(EventPayload):
    title: str
    origin: Literal["custom", "automatic", "summary"]


@dataclass(frozen=True)
class SessionWorkingDirectoryChanged(EventPayload):
    working_directory: str


@dataclass(frozen=True)
class SessionAccountChanged(EventPayload):
    account: AccountReference


@dataclass(frozen=True)
class SessionFinished(EventPayload):
    outcome: Outcome
    reason: str | None


@dataclass(frozen=True)
class ModelChanged(EventPayload):
    previous: ModelReference | None
    current: ModelReference
    reason: Literal["selected", "automatic_fallback", "account_migration", "reported_by_harness"]


@dataclass(frozen=True)
class EffortChanged(EventPayload):
    previous: str | None
    current: str
    reason: Literal["selected", "account_migration", "reported_by_harness"]


@dataclass(frozen=True)
class ActorStarted(EventPayload):
    name: str
    role: ActorRole


@dataclass(frozen=True)
class ActorNameChanged(EventPayload):
    name: str


@dataclass(frozen=True)
class ActorDescriptionChanged(EventPayload):
    description: str


@dataclass(frozen=True)
class ActorFinished(EventPayload):
    reason: str | None


@dataclass(frozen=True)
class TurnStarted(EventPayload):
    prompt_message_id: MessageId | None


@dataclass(frozen=True)
class TurnFinished(EventPayload):
    final_message_id: MessageId | None
    outcome: Outcome


@dataclass(frozen=True)
class TurnAborted(EventPayload):
    reason: str | None


@dataclass(frozen=True)
class MessageCreated(EventPayload):
    message_id: MessageId
    role: Literal["user", "assistant", "system", "peer", "parent"]
    content: Content
    phase: Literal["prompt", "intermediate", "final", "synthetic", "recap"] | None
    reply_to: MessageId | None


@dataclass(frozen=True)
class ReasoningCreated(EventPayload):
    reasoning_id: str
    content: Content
    summary: bool


@dataclass(frozen=True)
class OperationStarted(EventPayload):
    operation_id: OperationId
    category: OperationCategory
    native_name: str
    execution: ExecutionMode
    arguments: Content | None
    description: str | None
    parent_operation_id: OperationId | None


@dataclass(frozen=True)
class OperationProgressed(EventPayload):
    operation_id: OperationId
    ordinal: int
    stream: Literal["output", "error", "status"]
    content: Content
    mode: Literal["append", "replace"]


@dataclass(frozen=True)
class OperationInputProvided(EventPayload):
    operation_id: OperationId
    content: Content | None
    closed: bool


@dataclass(frozen=True)
class OperationFinished(EventPayload):
    operation_id: OperationId
    outcome: Outcome
    result: Content | None
    exit_code: int | None


@dataclass(frozen=True)
class FileAccessed(EventPayload):
    operation_id: OperationId | None
    path: str
    action: Literal["read", "created", "updated", "deleted", "renamed"]
    previous_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    unified_diff: str | None = None
    content: Content | None = None


@dataclass(frozen=True)
class ActorAssignmentStarted(EventPayload):
    assignment_id: AssignmentId
    brief: Content


@dataclass(frozen=True)
class ActorAssignmentFinished(EventPayload):
    assignment_id: AssignmentId
    outcome: Outcome
    result: Content | None
    reason: str | None


@dataclass(frozen=True)
class TaskChanged(EventPayload):
    task_id: TaskId
    label: str
    subject: str
    description: str | None
    state: Literal["pending", "in_progress", "completed", "deleted"]
    owner_actor_id: ActorId | None


@dataclass(frozen=True)
class TaskListChanged(EventPayload):
    list_id: str
    task_ids: tuple[TaskId, ...]


@dataclass(frozen=True)
class GoalChanged(EventPayload):
    objective: str | None
    state: Literal[
        "active",
        "paused",
        "blocked",
        "usage_limited",
        "budget_limited",
        "completed",
        "cleared",
    ]
    reason: str | None


@dataclass(frozen=True)
class ActorMessageSent(EventPayload):
    message_id: MessageId
    recipient_actor_id: ActorId
    content: Content | None


@dataclass(frozen=True)
class AttentionRequested(EventPayload):
    attention_id: AttentionId
    attention_type: Literal["permission", "question", "plan", "confirmation"]
    prompts: tuple[AttentionPrompt, ...]
    operation_id: OperationId | None


@dataclass(frozen=True)
class AttentionResolved(EventPayload):
    attention_id: AttentionId
    decision: Literal[
        "answered",
        "approved",
        "changes_requested",
        "rejected",
        "confirmed",
        "denied",
        "discussed",
    ]
    answers: tuple[AttentionAnswer, ...]
    feedback: str | None
    edited: bool
    outcome: Outcome


@dataclass(frozen=True)
class UsageReported(EventPayload):
    scope: Literal["session", "actor", "turn", "operation"]
    subject_id: str
    model: ModelReference | None
    account: AccountReference | None
    tokens: TokenUsage
    cumulative: bool
    cost_in_usd: Decimal | None


@dataclass(frozen=True)
class ContextReported(EventPayload):
    used_tokens: int
    window_tokens: int
    model: ModelReference | None


@dataclass(frozen=True)
class CompactionStarted(EventPayload):
    before_tokens: int | None


@dataclass(frozen=True)
class CompactionFinished(EventPayload):
    before_tokens: int | None
    after_tokens: int | None


EventPayloadType = TypeVar("EventPayloadType", bound=EventPayload)


@dataclass(frozen=True)
class CanonicalEvent(Generic[EventPayloadType]):
    event_id: CanonicalEventId
    session_id: SessionId
    actor_id: ActorId
    turn_id: TurnId | None
    parent_actor_id: ActorId | None
    harness: str
    occurred_at: float | None
    payload: EventPayloadType


EVENT_TYPES: dict[type[EventPayload], str] = {
    SessionStarted: "session.started",
    SessionTitleChanged: "session.title_changed",
    SessionWorkingDirectoryChanged: "session.working_directory_changed",
    SessionAccountChanged: "session.account_changed",
    SessionFinished: "session.finished",
    ModelChanged: "model.changed",
    EffortChanged: "effort.changed",
    ActorStarted: "actor.started",
    ActorNameChanged: "actor.name_changed",
    ActorDescriptionChanged: "actor.description_changed",
    ActorAssignmentStarted: "actor.assignment_started",
    ActorAssignmentFinished: "actor.assignment_finished",
    ActorFinished: "actor.finished",
    TurnStarted: "turn.started",
    TurnFinished: "turn.finished",
    TurnAborted: "turn.aborted",
    MessageCreated: "message.created",
    ReasoningCreated: "reasoning.created",
    OperationStarted: "operation.started",
    OperationInputProvided: "operation.input_provided",
    OperationProgressed: "operation.progressed",
    OperationFinished: "operation.finished",
    FileAccessed: "file.accessed",
    TaskChanged: "task.changed",
    TaskListChanged: "task.list_changed",
    GoalChanged: "goal.changed",
    ActorMessageSent: "actor.message_sent",
    AttentionRequested: "attention.requested",
    AttentionResolved: "attention.resolved",
    UsageReported: "usage.reported",
    ContextReported: "context.reported",
    CompactionStarted: "compaction.started",
    CompactionFinished: "compaction.finished",
}

PAYLOAD_TYPES: dict[str, type[EventPayload]] = {event_type: payload for payload, event_type in EVENT_TYPES.items()}
