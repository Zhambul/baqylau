"""The read-model vocabulary: what a fold over canonical facts produces.

Every dataclass here is a fact SHAPE the surfaces render — the browser's
response models and the pane's blocks are both built from these. Nothing in this file
reads a store or decides anything; the folds that produce these live beside it,
one module per concern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping, TypeAlias

from domain.events import (
    AttentionRequested,
    FileAccessed,
    ModelChanged,
    OperationProgressed,
    TaskChanged,
)
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
    StructuredContent,
    TextContent,
    TokenUsage,
)


@dataclass(frozen=True)
class ActivityContext:
    activity_id: str
    source_event_ids: tuple[CanonicalEventId, ...]
    session_id: SessionId
    actor_id: ActorId
    actor_name: str | None
    parent_actor_id: ActorId | None
    turn_id: TurnId | None
    started_at: float | None
    finished_at: float | None


@dataclass(frozen=True)
class MessageActivity:
    context: ActivityContext
    message_id: MessageId
    # Copied verbatim from MessageCreated.role, so it carries the SAME five
    # roles the domain event does. "parent" was missing here while every
    # renderer downstream already branched on it — those branches typed as
    # unreachable even though a parent-agent message reaches them at runtime.
    role: Literal["user", "assistant", "system", "peer", "parent"]
    phase: Literal["prompt", "intermediate", "final", "synthetic", "recap"] | None
    reply_to: MessageId | None
    content: Content


@dataclass(frozen=True)
class ReasoningActivity:
    context: ActivityContext
    reasoning_id: str
    content: Content
    summary: bool


@dataclass(frozen=True)
class OperationActivity:
    context: ActivityContext
    operation_id: OperationId
    category: OperationCategory | None
    native_name: str | None
    execution: ExecutionMode | None
    arguments: Content | None
    description: str | None
    parent_operation_id: OperationId | None
    progress: tuple[OperationProgressed, ...]
    state: Literal["running", "finished"]
    outcome: Outcome | None
    result: Content | None
    exit_code: int | None
    content_event_id: CanonicalEventId | None = None
    content_field: str | None = None

    def current_progress(self) -> tuple[Content, ...]:
        streams: dict[str, list[Content]] = {}
        for progress in self.progress:
            if progress.mode == "replace":
                streams[progress.stream] = [progress.content]
            else:
                streams.setdefault(progress.stream, []).append(progress.content)
        return tuple(content for stream in streams.values() for content in stream)

    @staticmethod
    def _text(content: Content | None) -> str:
        if content is None:
            return ""
        if isinstance(content, TextContent):
            return content.text
        if isinstance(content, StructuredContent):
            return json.dumps(
                json.loads(content.json_text),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        raise TypeError(f"unsupported content: {type(content).__name__}")

    def command_text(self) -> str:
        if isinstance(self.arguments, StructuredContent):
            document = json.loads(self.arguments.json_text)
            if isinstance(document, dict) and isinstance(document.get("command"), str):
                return document["command"]
        return self._text(self.arguments)

    def output_text(self) -> str:
        if self.result is not None:
            return self._text(self.result)
        return "\n".join(filter(None, map(self._text, self.current_progress())))


@dataclass(frozen=True)
class FileActivity:
    context: ActivityContext
    file: FileAccessed
    progress: tuple[OperationProgressed, ...]
    outcome: Outcome | None
    result: Content | None
    content_event_id: CanonicalEventId | None
    content_field: str | None


@dataclass(frozen=True)
class AttentionActivity:
    context: ActivityContext
    attention_id: AttentionId
    attention_type: Literal["permission", "question", "plan", "confirmation"] | None
    prompts: tuple[AttentionPrompt, ...]
    phase: Literal["requested", "resolved"]
    decision: Literal[
        "answered",
        "approved",
        "changes_requested",
        "rejected",
        "confirmed",
        "denied",
        "discussed",
    ] | None
    answers: tuple[AttentionAnswer, ...]
    feedback: str | None
    edited: bool
    outcome: Outcome | None


@dataclass(frozen=True)
class TaskActivity:
    context: ActivityContext
    change: TaskChanged


@dataclass(frozen=True)
class CompactionActivity:
    context: ActivityContext
    before_tokens: int | None
    after_tokens: int | None


@dataclass(frozen=True)
class ActorAssignmentActivity:
    context: ActivityContext
    assignment_id: AssignmentId
    brief: Content | None
    state: Literal["running", "finished"]
    outcome: Outcome | None
    result: Content | None
    reason: str | None
    assigned_actor_name: str | None = None
    prompt: Content | None = None


@dataclass(frozen=True)
class ActorMessageActivity:
    context: ActivityContext
    message_id: MessageId
    recipient_actor_id: ActorId
    content: Content | None


Activity: TypeAlias = (
    MessageActivity
    | ReasoningActivity
    | OperationActivity
    | FileActivity
    | AttentionActivity
    | TaskActivity
    | CompactionActivity
    | ActorAssignmentActivity
    | ActorMessageActivity
)


@dataclass(frozen=True)
class SessionSummary:
    session_id: SessionId
    harness: str
    title: str | None
    working_directory: str
    initial_working_directory: str
    started_at: float
    finished_at: float | None
    lead_actor_id: ActorId
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None
    prompt_count: int
    automatic_model_change: ModelChanged | None
    state: Literal["running", "finished"]


@dataclass(frozen=True)
class ActorSummary:
    actor_id: ActorId
    parent_actor_id: ActorId | None
    harness: str
    role: ActorRole
    name: str
    description: str | None
    model: ModelReference | None
    effort: str | None
    state: Literal["running", "finished"]
    started_at: float | None
    finished_at: float | None


@dataclass(frozen=True)
class UsageSummary:
    tokens: TokenUsage
    cost_in_usd: Decimal | None
    by_actor: Mapping[ActorId, TokenUsage]
    by_model: Mapping[str, TokenUsage]


@dataclass(frozen=True)
class PendingAttention:
    actor_id: ActorId
    request: AttentionRequested


@dataclass(frozen=True)
class AttentionState:
    pending: tuple[PendingAttention, ...]


@dataclass(frozen=True)
class ContextWindow:
    used_tokens: int
    window_tokens: int
    model: ModelReference | None


@dataclass(frozen=True)
class ContextSummary:
    by_actor: Mapping[ActorId, ContextWindow]
    compacting_actor_ids: tuple[ActorId, ...]


@dataclass(frozen=True)
class TaskSummary:
    task_id: TaskId
    label: str
    subject: str
    description: str | None
    state: Literal["pending", "in_progress", "completed"]
    owner_actor_id: ActorId | None


@dataclass(frozen=True)
class GoalState:
    objective: str
    state: Literal[
        "active",
        "paused",
        "blocked",
        "usage_limited",
        "budget_limited",
        "completed",
    ]
    reason: str | None


@dataclass(frozen=True)
class BackgroundWorkSummary:
    running_operation_ids: tuple[OperationId, ...]
    monitor_count: int
    background_job_count: int


@dataclass(frozen=True)
class ActivityStatistics:
    shell_command_count: int
    failed_shell_command_count: int
    file_count: int
    lines_added: int
    lines_removed: int
    actor_message_count: int
    operation_counts: Mapping[str, int]


TabState: TypeAlias = Literal[
    "idle",
    "thinking",
    "working",
    "executing",
    "awaiting_background",
    "awaiting_attention",
    "awaiting_response",
]


@dataclass(frozen=True)
class ActivityScope:
    actor_id: ActorId | None = None


@dataclass(frozen=True)
class ActivityPage:
    cursor: int
    latest_cursor: int | None
    activities: tuple[Activity, ...]


@dataclass(frozen=True)
class ActivityWindow:
    oldest_cursor: int
    activities: tuple[Activity, ...]
    has_more: bool
