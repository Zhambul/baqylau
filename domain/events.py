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
    SessionId,
    ShellId,
    SkillId,
    TaskId,
    TurnId,
)
from domain.values import (
    AccountReference,
    ActorRole,
    AttentionAnswer,
    AttentionPrompt,
    Content,
    EffortChangeReason,
    ExecutionMode,
    FileAction,
    GoalState,
    MessagePhase,
    MessageRole,
    ModelChangeReason,
    ModelReference,
    Outcome,
    PlanState,
    ProgressStream,
    TitleOrigin,
    TokenUsage,
    WorktreeAction,
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
    reason: ModelChangeReason


@dataclass(frozen=True)
class EffortChanged(EventPayload):
    previous: str | None
    current: str
    reason: EffortChangeReason


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
    """Anything anyone said, including one actor to another.

    `recipient_actor_id` is what absorbed the old `actor.message_sent`: a
    SendMessage is not a tool call, it is the actor speaking to a named peer.
    """

    message_id: MessageId
    role: MessageRole
    content: Content
    phase: MessagePhase | None
    reply_to: MessageId | None
    recipient_actor_id: ActorId | None = None


@dataclass(frozen=True)
class ReasoningCreated(EventPayload):
    reasoning_id: str
    content: Content


@dataclass(frozen=True)
class ShellStarted(EventPayload):
    """A command was launched. `description` is the harness's own one-line
    account of it, where the harness offers one."""

    shell_id: ShellId
    command: Content
    execution: ExecutionMode
    description: str | None


@dataclass(frozen=True)
class ShellProgressed(EventPayload):
    shell_id: ShellId
    ordinal: int
    stream: ProgressStream
    content: Content
    mode: Literal["append", "replace"]


@dataclass(frozen=True)
class ShellInputProvided(EventPayload):
    shell_id: ShellId
    content: Content | None
    closed: bool


@dataclass(frozen=True)
class ShellFinished(EventPayload):
    shell_id: ShellId
    outcome: Outcome
    result: Content | None
    exit_code: int | None


@dataclass(frozen=True)
class ShellOutputLocated(EventPayload):
    """The command's output can be read from this file — emitted ONCE per
    command, when the location becomes known (not per chunk of output; the
    chunks are separate evidence, read later by the collect phase)."""

    shell_id: ShellId
    source_path: str
    chunk_source_type: str
    delete_source: bool
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: bool
    until: Literal["shell_finished", "session_finished"]


@dataclass(frozen=True)
class ShellBackgrounded(EventPayload):
    """This command's `shell.finished` no longer means it ended.

    One sentence, three consumers, each of which gets it wrong by default:
      * the output following — `shell.finished` must stop ending it, or the
        file the job is still writing to is drained once and unlinked;
      * the actor's status — the session is `awaiting_background`, not idle;
      * the feed — it is still running, and saying otherwise reports a
        `succeeded` for work that has not happened yet.

    Emitted when a command that STARTED in the foreground moves to the
    background mid-run — the case `ShellStarted.execution` cannot express,
    because at start time nobody knew. Both harnesses report it only in the
    RESULT of the launching call, which is also why this fact must be committed
    BEFORE the `shell.finished` derived from that same evidence.

    `native_id` is the harness's own handle on the thing (the id a user or the
    model needs to interact with it again), or None where there isn't one.
    """

    shell_id: ShellId
    native_id: str | None


@dataclass(frozen=True)
class ShellOutputFinished(EventPayload):
    """The command's output file is complete — the harness announced the
    background job's true end, which its launch-time `shell.finished`
    (reported while output still flowed) could not. Ends the following early
    instead of waiting for the session to finish.

    `outcome` is how the JOB ended, which is not how its launch ended: a
    background command that exits 1 launched perfectly. None when the harness
    announced an end without saying what kind.
    """

    shell_id: ShellId
    outcome: Outcome | None = None


@dataclass(frozen=True)
class FileAccessed(EventPayload):
    """One file touched, at the moment the touch RESOLVED — which is the only
    moment both the path and what came back of it are known."""

    path: str
    action: FileAction
    outcome: Outcome
    previous_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    unified_diff: str | None = None
    content: Content | None = None


@dataclass(frozen=True)
class SearchPerformed(EventPayload):
    """A search and what it found, as one fact: a search has no life between
    its query and its result that anyone reads."""

    tool: str
    query: Content
    result: Content | None
    outcome: Outcome


@dataclass(frozen=True)
class SkillStarted(EventPayload):
    """`arguments` is nullable because a harness may collapse the call to the
    bare skill name and keep nothing else."""

    skill_id: SkillId
    name: str
    arguments: Content | None


@dataclass(frozen=True)
class SkillFinished(EventPayload):
    skill_id: SkillId
    outcome: Outcome
    result: Content | None


@dataclass(frozen=True)
class WebFetched(EventPayload):
    """One page fetched. `url` is None when a harness reports the result of a
    fetch without the call that made it."""

    url: str | None
    result: Content | None
    outcome: Outcome


@dataclass(frozen=True)
class WorktreeChanged(EventPayload):
    """A worktree was entered or left. No harness exposes a path for this
    today, so the call's own arguments ride along verbatim rather than a
    parsed field that would be empty."""

    action: WorktreeAction
    arguments: Content | None
    outcome: Outcome


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
class QuestionAsked(EventPayload):
    """The session is waiting on a person. Pending until its answer arrives."""

    attention_id: AttentionId
    questions: tuple[AttentionPrompt, ...]


@dataclass(frozen=True)
class QuestionAnswered(EventPayload):
    """What was answered. The harnesses' own verdict words — answered,
    rejected, discussed — are deliberately not carried: every reader that had
    them collapsed all three to one line, and the answer itself says more."""

    attention_id: AttentionId
    answers: tuple[AttentionAnswer, ...]
    feedback: str | None


@dataclass(frozen=True)
class PlanProposed(EventPayload):
    attention_id: AttentionId
    plan: Content


@dataclass(frozen=True)
class PlanResolved(EventPayload):
    attention_id: AttentionId
    state: PlanState
    feedback: str | None
    edited: bool


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
    ShellStarted: "shell.started",
    ShellInputProvided: "shell.input_provided",
    ShellProgressed: "shell.progressed",
    ShellFinished: "shell.finished",
    ShellOutputLocated: "shell.output_located",
    ShellBackgrounded: "shell.backgrounded",
    ShellOutputFinished: "shell.output_finished",
    FileAccessed: "file.accessed",
    SearchPerformed: "search.performed",
    SkillStarted: "skill.started",
    SkillFinished: "skill.finished",
    WebFetched: "web.fetched",
    WorktreeChanged: "worktree.changed",
    TaskChanged: "task.changed",
    TaskListChanged: "task.list_changed",
    GoalChanged: "goal.changed",
    QuestionAsked: "question.asked",
    QuestionAnswered: "question.answered",
    PlanProposed: "plan.proposed",
    PlanResolved: "plan.resolved",
    UsageReported: "usage.reported",
    ContextReported: "context.reported",
    CompactionStarted: "compaction.started",
    CompactionFinished: "compaction.finished",
}

PAYLOAD_TYPES: dict[str, type[EventPayload]] = {event_type: payload for payload, event_type in EVENT_TYPES.items()}
