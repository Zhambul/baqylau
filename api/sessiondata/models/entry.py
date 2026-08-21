# The feed, as both frontends receive it: one stored event and twenty-four bodies.
#
# All in one module, like the control outcomes: this is ONE closed vocabulary,
# and a reader deciding what to draw needs to see the whole of it at once. The
# discriminator is `type` on the STORED EVENT rather than inside each body, because
# an entry's kind is a fact about the entry, not a field of what it holds.
from typing import Literal, TypeAlias

from pydantic import BaseModel

from api.common.models.values.content import ContentResponse


class TurnStartedBodyResponse(BaseModel):
    """The grouping marker. Everything until the next end marker belongs to it,
    which is all a client needs to draw a collapsed turn."""


class TurnFinishedBodyResponse(BaseModel):
    state: Literal["finished", "aborted"]


class MessageBodyResponse(BaseModel):
    message_id: str
    role: Literal["user", "assistant", "system", "peer", "parent"]
    phase: Literal["prompt", "intermediate", "end_turn", "synthetic", "recap"] | None
    content: ContentResponse
    recipient_actor_id: str | None
    # The prompt this one replaced, when a harness re-parented around a discarded
    # one. Two prompts naming the same parent means the older is dead.
    reply_to: str | None


class ReasoningBodyResponse(BaseModel):
    reasoning_id: str
    content: ContentResponse


class ShellStartedBodyResponse(BaseModel):
    shell_id: str
    command: ContentResponse
    execution: Literal["foreground", "background", "monitor"]


class ShellOutputBodyResponse(BaseModel):
    """One chunk. The client folds chunks per shell, honouring append/replace per
    stream — a bounded fold over what is on screen."""

    shell_id: str
    stream: Literal["output", "error", "status"]
    mode: Literal["append", "replace"]
    content: ContentResponse


class ShellBackgroundedBodyResponse(BaseModel):
    shell_id: str


class ShellFinishedBodyResponse(BaseModel):
    """`result` is the whole output at once, for a harness that reports it at the
    end rather than streaming chunks. A client folds it exactly as it folds a
    replacing chunk — which is what it is."""

    shell_id: str
    state: Literal["succeeded", "failed", "cancelled"]
    exit_code: int | None
    result: ContentResponse | None


class FileBodyResponse(BaseModel):
    path: str
    action: Literal["read", "created", "updated", "deleted", "renamed"]
    state: Literal["succeeded", "failed"]
    previous_path: str | None
    lines_added: int | None
    lines_removed: int | None
    content: ContentResponse | None


class SearchBodyResponse(BaseModel):
    tool: str
    query: ContentResponse
    state: Literal["succeeded", "failed"]
    result: ContentResponse | None


class WebBodyResponse(BaseModel):
    url: str | None
    state: Literal["succeeded", "failed"]
    result: ContentResponse | None


class WorktreeBodyResponse(BaseModel):
    action: Literal["entered", "exited"]
    state: Literal["succeeded", "failed"]
    arguments: ContentResponse | None


class SkillStartedBodyResponse(BaseModel):
    skill_id: str
    name: str
    arguments: ContentResponse | None


class SkillFinishedBodyResponse(BaseModel):
    skill_id: str
    state: Literal["succeeded", "failed", "cancelled"]
    result: ContentResponse | None


class QuestionChoiceResponse(BaseModel):
    """A label and what it means. The label IS the value the answer sends back."""

    label: str
    description: str | None


class QuestionResponse(BaseModel):
    question_id: str
    title: str | None
    question: str
    multiple: bool
    choices: tuple[QuestionChoiceResponse, ...]


class QuestionAskedBodyResponse(BaseModel):
    """Pending until its answered twin arrives — which is how a client derives
    "this session is waiting on me", with no stored flag to go stale."""

    attention_id: str
    questions: tuple[QuestionResponse, ...]


class QuestionAnswerResponse(BaseModel):
    question_id: str
    labels: tuple[str, ...]


class QuestionAnsweredBodyResponse(BaseModel):
    attention_id: str
    answers: tuple[QuestionAnswerResponse, ...]
    feedback: str | None


class PlanProposedBodyResponse(BaseModel):
    attention_id: str
    plan: ContentResponse


class PlanResolvedBodyResponse(BaseModel):
    attention_id: str
    state: Literal["approved", "changes_requested", "rejected"]
    feedback: str | None
    edited: bool


class CompactionStartedBodyResponse(BaseModel):
    before_tokens: int | None


class CompactionFinishedBodyResponse(BaseModel):
    before_tokens: int | None
    after_tokens: int | None


class AssignmentStartedBodyResponse(BaseModel):
    assignment_id: str
    assigned_actor_name: str | None
    prompt: ContentResponse | None


class AssignmentFinishedBodyResponse(BaseModel):
    assignment_id: str
    state: Literal["succeeded", "failed", "cancelled"]
    result: ContentResponse | None


class ModelChangeBodyResponse(BaseModel):
    """`automatic` marks a model the harness chose for you, which is worth a
    warning — the one `reason` that survived into the read model."""

    current: str
    previous: str | None
    automatic: bool


class EffortChangeBodyResponse(BaseModel):
    current: str
    previous: str | None


EntryBodyResponse: TypeAlias = (
    TurnStartedBodyResponse
    | TurnFinishedBodyResponse
    | MessageBodyResponse
    | ReasoningBodyResponse
    | ShellStartedBodyResponse
    | ShellOutputBodyResponse
    | ShellBackgroundedBodyResponse
    | ShellFinishedBodyResponse
    | FileBodyResponse
    | SearchBodyResponse
    | WebBodyResponse
    | WorktreeBodyResponse
    | SkillStartedBodyResponse
    | SkillFinishedBodyResponse
    | QuestionAskedBodyResponse
    | QuestionAnsweredBodyResponse
    | PlanProposedBodyResponse
    | PlanResolvedBodyResponse
    | CompactionStartedBodyResponse
    | CompactionFinishedBodyResponse
    | AssignmentStartedBodyResponse
    | AssignmentFinishedBodyResponse
    | ModelChangeBodyResponse
    | EffortChangeBodyResponse
)

EntryType: TypeAlias = Literal[
    "turn_started",
    "turn_finished",
    "message",
    "reasoning",
    "shell_started",
    "shell_output",
    "shell_backgrounded",
    "shell_finished",
    "file",
    "search",
    "web",
    "worktree",
    "skill_started",
    "skill_finished",
    "question_asked",
    "question_answered",
    "plan_proposed",
    "plan_resolved",
    "compaction_started",
    "compaction_finished",
    "assignment_started",
    "assignment_finished",
    "model_change",
    "effort_change",
]


class EntryResponse(BaseModel):
    """One immutable line of the feed.

    `cursor` is three things at once and that is the point: the SSE event id, the
    paging key, and the client's idempotency key's companion — an entry the
    client already holds (by `entry_id`) is skipped, so an overlapping frame can
    never show twice.
    """

    entry_id: str
    type: EntryType
    cursor: int
    actor_id: str
    parent_actor_id: str | None
    turn_id: str | None
    occurred_at: float
    summary: str | None
    body: EntryBodyResponse


class EntryPageResponse(BaseModel):
    """One page, oldest first. `oldest_cursor` is where the next page back
    starts, and `has_more` says whether there is one."""

    items: tuple[EntryResponse, ...]
    oldest_cursor: int
    has_more: bool
