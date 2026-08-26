"""The session feed, as stored rows: one immutable entry per feed-worthy fact.

Built once, at push time, by `engine/sessiondata/entries.py`, and never touched
again — every entry is immutable, so materializing one is a pure append and
reading the feed is one indexed range scan. The canonical log stays behind them
as raw events and as the replay source.

An entry is NOT its canonical event. The event says what happened in the words
the translators produce; the entry says what a reader is shown, which drops
every field nobody displays (the reasons, the operation links, the harness's own
decision words) and folds the rest into one flat body per kind.

Content stays `Content` rather than a bare string: whether a message is markdown
or plain text is a fact about it, the harness told us, and a body that dropped
it would leave every client guessing by role.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from domain.ids import (
    ActorId,
    AssignmentId,
    AttentionId,
    CanonicalEventId,
    MessageId,
    ReasoningId,
    SessionId,
    ShellId,
    SkillId,
    TurnId,
)
from domain.stored import STORED
from domain.values import (
    AttentionAnswer,
    AttentionPrompt,
    Content,
    ExecutionMode,
    FileAction,
    MessagePhase,
    MessageRole,
    OutputMode,
    PlanState,
    ProgressStream,
    WorktreeAction,
)


class EntryTypeName(StrEnum):
    """The kinds a feed has, as the name that travels with a stored row.
    Typed rather than `str` so the api layer's own copy of this vocabulary is
    CHECKED against it: two lists that must agree, and a type error the
    moment they do not."""

    TURN_STARTED = "turn_started"
    TURN_FINISHED = "turn_finished"
    MESSAGE = "message"
    REASONING = "reasoning"
    SHELL_STARTED = "shell_started"
    SHELL_OUTPUT = "shell_output"
    SHELL_BACKGROUNDED = "shell_backgrounded"
    SHELL_FINISHED = "shell_finished"
    FILE = "file"
    SEARCH = "search"
    WEB = "web"
    WORKTREE = "worktree"
    SKILL_STARTED = "skill_started"
    SKILL_FINISHED = "skill_finished"
    QUESTION_ASKED = "question_asked"
    QUESTION_ANSWERED = "question_answered"
    PLAN_PROPOSED = "plan_proposed"
    PLAN_RESOLVED = "plan_resolved"
    COMPACTION_STARTED = "compaction_started"
    COMPACTION_FINISHED = "compaction_finished"
    ASSIGNMENT_STARTED = "assignment_started"
    ASSIGNMENT_FINISHED = "assignment_finished"
    MODEL_CHANGE = "model_change"
    EFFORT_CHANGE = "effort_change"


class RunState(StrEnum):
    """How a thing that runs ended. One word for every kind, in place of the
    `outcome` + `reason` pair the canonical events carry: a feed shows the
    state, and nothing displayed the reason."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TurnState(StrEnum):
    FINISHED = "finished"
    ABORTED = "aborted"


class FileState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class EntryBody:
    """Marker base for entry bodies. Same stored-shape rule as a payload: an
    unknown field in a decoded row is drift, not a new feature."""

    __pydantic_config__ = STORED


@dataclass(frozen=True)
class TurnStartedBody(EntryBody):
    """The grouping marker. Carries nothing: a turn's identity is on the
    stored event, and everything between two markers belongs to it."""


@dataclass(frozen=True)
class TurnFinishedBody(EntryBody):
    state: TurnState


@dataclass(frozen=True)
class MessageBody(EntryBody):
    message_id: MessageId
    role: MessageRole
    phase: MessagePhase | None
    content: Content
    recipient_actor_id: ActorId | None = None
    # The prompt this one replaced. A harness that re-parents around a DISCARDED
    # prompt still reports the dead one, and the only thing that tells the two
    # apart is that the survivor names the same parent — so a feed without this
    # shows a prompt nobody sent, for the rest of the session.
    reply_to: MessageId | None = None


@dataclass(frozen=True)
class ReasoningBody(EntryBody):
    reasoning_id: ReasoningId
    content: Content


@dataclass(frozen=True)
class ShellStartedBody(EntryBody):
    shell_id: ShellId
    command: Content
    execution: ExecutionMode


@dataclass(frozen=True)
class ShellOutputBody(EntryBody):
    """One chunk, exactly as it arrived. The client folds chunks per shell,
    which is a bounded fold over what is on screen — re-sending a growing
    output on every line was the churn this replaces."""

    shell_id: ShellId
    stream: ProgressStream
    mode: OutputMode
    content: Content


@dataclass(frozen=True)
class ShellBackgroundedBody(EntryBody):
    shell_id: ShellId


@dataclass(frozen=True)
class ShellFinishedBody(EntryBody):
    shell_id: ShellId
    state: RunState
    # Only ever filled by a harness that reports one, and not all of them do.
    exit_code: int | None = None
    # What the command PRINTED, when its harness reports the output at the END
    # rather than streaming it. Both channels exist because both kinds of harness
    # exist: one follows the output file and emits chunks as it grows, leaving
    # this empty; another reports the whole thing once, with the call's result,
    # and emits no chunks at all. A feed that read only the chunks showed every
    # command of the second kind with no output whatsoever.
    result: Content | None = None


@dataclass(frozen=True)
class FileBody(EntryBody):
    path: str
    action: FileAction
    state: FileState
    previous_path: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    # The diff when the file was changed, its text when it was created or read.
    content: Content | None = None


@dataclass(frozen=True)
class SearchBody(EntryBody):
    tool: str
    query: Content
    state: FileState
    result: Content | None = None


@dataclass(frozen=True)
class WebBody(EntryBody):
    url: str | None
    state: FileState
    result: Content | None = None


@dataclass(frozen=True)
class WorktreeBody(EntryBody):
    action: WorktreeAction
    state: FileState
    arguments: Content | None = None


@dataclass(frozen=True)
class SkillStartedBody(EntryBody):
    skill_id: SkillId
    name: str
    arguments: Content | None = None


@dataclass(frozen=True)
class SkillFinishedBody(EntryBody):
    skill_id: SkillId
    state: RunState
    result: Content | None = None


@dataclass(frozen=True)
class QuestionAskedBody(EntryBody):
    """Pending until its answered twin arrives — which is how every client
    derives "this session is waiting on me", with no stored flag to go stale."""

    attention_id: AttentionId
    questions: tuple[AttentionPrompt, ...]


@dataclass(frozen=True)
class QuestionAnsweredBody(EntryBody):
    attention_id: AttentionId
    answers: tuple[AttentionAnswer, ...] = ()
    feedback: str | None = None


@dataclass(frozen=True)
class PlanProposedBody(EntryBody):
    attention_id: AttentionId
    plan: Content


@dataclass(frozen=True)
class PlanResolvedBody(EntryBody):
    attention_id: AttentionId
    state: PlanState
    feedback: str | None = None
    edited: bool = False


@dataclass(frozen=True)
class CompactionStartedBody(EntryBody):
    before_tokens: int | None = None


@dataclass(frozen=True)
class CompactionFinishedBody(EntryBody):
    before_tokens: int | None = None
    after_tokens: int | None = None
    context: Content | None = None


@dataclass(frozen=True)
class AssignmentStartedBody(EntryBody):
    assignment_id: AssignmentId
    assigned_actor_name: str | None = None
    prompt: Content | None = None


@dataclass(frozen=True)
class AssignmentFinishedBody(EntryBody):
    assignment_id: AssignmentId
    state: RunState = RunState.SUCCEEDED
    result: Content | None = None


@dataclass(frozen=True)
class ModelChangeBody(EntryBody):
    """`automatic` marks a harness fallback — the one `reason` that survived,
    because a model the harness chose for you is worth a warning."""

    current: str
    previous: str | None = None
    automatic: bool = False


@dataclass(frozen=True)
class EffortChangeBody(EntryBody):
    current: str
    previous: str | None = None


ENTRY_TYPES: dict[type[EntryBody], EntryTypeName] = {
    TurnStartedBody: EntryTypeName.TURN_STARTED,
    TurnFinishedBody: EntryTypeName.TURN_FINISHED,
    MessageBody: EntryTypeName.MESSAGE,
    ReasoningBody: EntryTypeName.REASONING,
    ShellStartedBody: EntryTypeName.SHELL_STARTED,
    ShellOutputBody: EntryTypeName.SHELL_OUTPUT,
    ShellBackgroundedBody: EntryTypeName.SHELL_BACKGROUNDED,
    ShellFinishedBody: EntryTypeName.SHELL_FINISHED,
    FileBody: EntryTypeName.FILE,
    SearchBody: EntryTypeName.SEARCH,
    WebBody: EntryTypeName.WEB,
    WorktreeBody: EntryTypeName.WORKTREE,
    SkillStartedBody: EntryTypeName.SKILL_STARTED,
    SkillFinishedBody: EntryTypeName.SKILL_FINISHED,
    QuestionAskedBody: EntryTypeName.QUESTION_ASKED,
    QuestionAnsweredBody: EntryTypeName.QUESTION_ANSWERED,
    PlanProposedBody: EntryTypeName.PLAN_PROPOSED,
    PlanResolvedBody: EntryTypeName.PLAN_RESOLVED,
    CompactionStartedBody: EntryTypeName.COMPACTION_STARTED,
    CompactionFinishedBody: EntryTypeName.COMPACTION_FINISHED,
    AssignmentStartedBody: EntryTypeName.ASSIGNMENT_STARTED,
    AssignmentFinishedBody: EntryTypeName.ASSIGNMENT_FINISHED,
    ModelChangeBody: EntryTypeName.MODEL_CHANGE,
    EffortChangeBody: EntryTypeName.EFFORT_CHANGE,
}

BODY_TYPES: dict[EntryTypeName, type[EntryBody]] = {
    entry_type: body for body, entry_type in ENTRY_TYPES.items()
}


ATTENTION_ENTRY_TYPES: tuple[EntryTypeName, ...] = (
    EntryTypeName.QUESTION_ASKED,
    EntryTypeName.QUESTION_ANSWERED,
    EntryTypeName.PLAN_PROPOSED,
    EntryTypeName.PLAN_RESOLVED,
)


@dataclass(frozen=True)
class SessionEntry:
    """One row of `session_entries`.

    `entry_id` is the source canonical event's id, which is what makes writing
    one idempotent: a replay inserts the same row and the UNIQUE constraint
    keeps it at one. `cursor` is the read model's own monotonic stamp — the SSE
    event id, and the paging key.
    """

    entry_id: CanonicalEventId
    session_id: SessionId
    actor_id: ActorId
    parent_actor_id: ActorId | None
    turn_id: TurnId | None
    occurred_at: float
    summary: str | None
    body: EntryBody
    cursor: int = 0

    @property
    def entry_type(self) -> EntryTypeName:
        return ENTRY_TYPES[type(self.body)]


def pending_attention(entries: Sequence[SessionEntry]) -> tuple[SessionEntry, ...]:
    """The questions and plans still waiting on a person, oldest first.

    DERIVED, from the attention entries in order: an asked or proposed entry
    whose resolution has not arrived. Nothing stores "still pending" — a stored
    flag would be a second answer to the same question, and it could disagree
    with the feed the person is looking at.

    The same rule both clients apply to draw the attention badge, so it lives
    here rather than three times.
    """
    open_attentions: dict[AttentionId, SessionEntry] = {}
    for entry in entries:
        body = entry.body
        if isinstance(body, (QuestionAskedBody, PlanProposedBody)):
            open_attentions[body.attention_id] = entry
        elif isinstance(body, (QuestionAnsweredBody, PlanResolvedBody)):
            open_attentions.pop(body.attention_id, None)
    return tuple(open_attentions.values())
