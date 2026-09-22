# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the entry module."""

# The feed, as both frontends receive it: one stored event and twenty-five bodies.
#
# All in one module, like the control outcomes: this is ONE closed vocabulary,
# and a reader deciding what to draw needs to see the whole of it at once. The
# discriminator is `type` on the STORED EVENT rather than inside each body, because
# an entry's kind is a fact about the entry, not a field of what it holds.

from pydantic import BaseModel, Field

from api.sessiondata.models.entry_attention_bodies import (
    EmptyQuestionIdError as EmptyQuestionIdError,
    QuestionAnsweredBodyResponse as QuestionAnsweredBodyResponse,
    QuestionAnswerResponse as QuestionAnswerResponse,
    QuestionAskedBodyResponse as QuestionAskedBodyResponse,
    QuestionChoiceResponse as QuestionChoiceResponse,
    QuestionResponse as QuestionResponse,
)
from api.sessiondata.models.entry_extension_bodies import (
    ExtensionBodyResponse as ExtensionBodyResponse,
    ExtensionSchemaRefResponse as ExtensionSchemaRefResponse,
)
from api.sessiondata.models.entry_lifecycle_bodies import (
    AssignmentFinishedBodyResponse as AssignmentFinishedBodyResponse,
    AssignmentStartedBodyResponse as AssignmentStartedBodyResponse,
    CompactionFinishedBodyResponse as CompactionFinishedBodyResponse,
    CompactionStartedBodyResponse as CompactionStartedBodyResponse,
    EffortChangeBodyResponse as EffortChangeBodyResponse,
    ModelChangeBodyResponse as ModelChangeBodyResponse,
)
from api.sessiondata.models.entry_plan_bodies import (
    PlanProposedBodyResponse as PlanProposedBodyResponse,
    PlanResolvedBodyResponse as PlanResolvedBodyResponse,
)
from api.sessiondata.models.entry_resource_bodies import (
    BrowserBodyResponse as BrowserBodyResponse,
    FileBodyResponse as FileBodyResponse,
    SearchBodyResponse as SearchBodyResponse,
    WebBodyResponse as WebBodyResponse,
    WorktreeBodyResponse as WorktreeBodyResponse,
)
from api.sessiondata.models.entry_shell_bodies import (
    ShellBackgroundedBodyResponse as ShellBackgroundedBodyResponse,
    ShellFinishedBodyResponse as ShellFinishedBodyResponse,
    ShellOutputBodyResponse as ShellOutputBodyResponse,
    ShellStartedBodyResponse as ShellStartedBodyResponse,
)
from api.sessiondata.models.entry_skill_bodies import (
    SkillFinishedBodyResponse as SkillFinishedBodyResponse,
    SkillStartedBodyResponse as SkillStartedBodyResponse,
)
from api.sessiondata.models.entry_turn_bodies import (
    MessageBodyResponse as MessageBodyResponse,
    ReasoningBodyResponse as ReasoningBodyResponse,
    TurnFinishedBodyResponse as TurnFinishedBodyResponse,
    TurnStartedBodyResponse as TurnStartedBodyResponse,
)
from domain.entries import EntryTypeName

type EntryBodyResponse = (
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
    | BrowserBodyResponse
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
    | ExtensionBodyResponse
)

type EntryType = EntryTypeName


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
    """Represent entry page response.

    One page, oldest first. `oldest_cursor` is where the next page back
        starts, and `has_more` says whether there is one.
    """

    entries: tuple[EntryResponse, ...] = Field(alias="items")
    oldest_cursor: int
    has_more: bool
