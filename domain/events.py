"""Closed canonical event vocabulary from the architecture proposal."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from domain.stored import STORED
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
    AttentionDecision,
    AttentionPrompt,
    AttentionType,
    Content,
    ExecutionMode,
    FileAction,
    GoalState,
    MessagePhase,
    MessageRole,
    ModelReference,
    OperationCategory,
    Outcome,
    ProgressStream,
    TitleOrigin,
    TokenUsage,
)


@dataclass(frozen=True)
class EventPayload:
    """Marker base for semantic payloads.

    Carries the stored-shape config, which every payload inherits: a canonical
    event is decoded from a row, and an unknown field there is drift.
    """

    __pydantic_config__ = STORED


@dataclass(frozen=True)
class SessionStarted(EventPayload):
    working_directory: str
    source_reference: str
    resumed_from: SessionId | None
    title: str | None
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None


@dataclass(frozen=True)
class SessionTitleChanged(EventPayload):
    title: str
    origin: TitleOrigin


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
    role: MessageRole
    content: Content
    phase: MessagePhase | None
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
    stream: ProgressStream
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
class OperationOutputLocated(EventPayload):
    """The operation's output can be read from this file — emitted ONCE per
    operation, when the location becomes known (not per chunk of output; the
    chunks are separate evidence, read later by the collect phase)."""

    operation_id: OperationId
    source_path: str
    chunk_source_type: str
    delete_source: bool
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: bool
    until: Literal["operation_finished", "session_finished"]


@dataclass(frozen=True)
class OperationBackgrounded(EventPayload):
    """This operation's `operation.finished` no longer means it ended.

    One sentence, three consumers, each of which gets it wrong by default:
      * the output following — `operation.finished` must stop ending it, or the
        file the job is still writing to is drained once and unlinked;
      * the tab state — the session is `awaiting_background`, not idle;
      * the activity — it is still running, and saying otherwise reports a
        `succeeded` for work that has not happened yet.

    Emitted when an operation that STARTED in the foreground moves to the
    background mid-run — the case `OperationStarted.execution` cannot express,
    because at start time nobody knew. Both harnesses report it only in the
    RESULT of the launching call, which is also why this fact must be committed
    BEFORE the `operation.finished` derived from that same evidence.

    `native_id` is the harness's own handle on the thing (the id a user or the
    model needs to interact with it again), or None where there isn't one.
    """

    operation_id: OperationId
    native_id: str | None


@dataclass(frozen=True)
class OperationOutputFinished(EventPayload):
    """The operation's output file is complete — the harness announced the
    background job's true end, which its launch-time `operation.finished`
    (reported while output still flowed) could not. Ends the following early
    instead of waiting for the session to finish.

    `outcome` is how the JOB ended, which is not how its launch ended: a
    background command that exits 1 launched perfectly. None when the harness
    announced an end without saying what kind.
    """

    operation_id: OperationId
    outcome: Outcome | None = None


@dataclass(frozen=True)
class FileAccessed(EventPayload):
    operation_id: OperationId | None
    path: str
    action: FileAction
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
    # Optional facts a harness may know at launch: the assigned actor's display
    # name, and the verbatim prompt it was launched with (None when the harness
    # does not expose it). Defaulted so rows written before these fields decode.
    actor_name: str | None = None
    prompt: Content | None = None


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
    state: GoalState
    reason: str | None


@dataclass(frozen=True)
class ActorMessageSent(EventPayload):
    message_id: MessageId
    recipient_actor_id: ActorId
    content: Content | None


@dataclass(frozen=True)
class AttentionRequested(EventPayload):
    attention_id: AttentionId
    attention_type: AttentionType
    prompts: tuple[AttentionPrompt, ...]
    operation_id: OperationId | None


@dataclass(frozen=True)
class AttentionResolved(EventPayload):
    attention_id: AttentionId
    decision: AttentionDecision
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
    terminal_window_id: str | None
    harness_process_id: int | None
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
    OperationOutputLocated: "operation.output_located",
    OperationBackgrounded: "operation.backgrounded",
    OperationOutputFinished: "operation.output_finished",
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
