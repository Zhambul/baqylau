# Copyright (c) 2026 Zhambyl Yermagambet
"""Define stored session feed rows and their closed body vocabulary."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from domain import (
    entry_attention,
    entry_conversation,
    entry_lifecycle,
    entry_resources,
    entry_shells,
)
from domain.entry_base import EntryBody
from domain.ids import ActorId, CanonicalEventId, SessionId, TurnId

if TYPE_CHECKING:
    from domain.ids import AttentionId


class EntryTypeName(StrEnum):
    """Name each body type that can occur in a stored session feed."""

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
    BROWSER = "browser"
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


ENTRY_TYPES: Mapping[type[EntryBody], EntryTypeName] = MappingProxyType(
    {
        entry_conversation.TurnStartedBody: EntryTypeName.TURN_STARTED,
        entry_conversation.TurnFinishedBody: EntryTypeName.TURN_FINISHED,
        entry_conversation.MessageBody: EntryTypeName.MESSAGE,
        entry_conversation.ReasoningBody: EntryTypeName.REASONING,
        entry_shells.ShellStartedBody: EntryTypeName.SHELL_STARTED,
        entry_shells.ShellOutputBody: EntryTypeName.SHELL_OUTPUT,
        entry_shells.ShellBackgroundedBody: EntryTypeName.SHELL_BACKGROUNDED,
        entry_shells.ShellFinishedBody: EntryTypeName.SHELL_FINISHED,
        entry_resources.FileBody: EntryTypeName.FILE,
        entry_resources.SearchBody: EntryTypeName.SEARCH,
        entry_resources.WebBody: EntryTypeName.WEB,
        entry_resources.BrowserBody: EntryTypeName.BROWSER,
        entry_resources.WorktreeBody: EntryTypeName.WORKTREE,
        entry_attention.SkillStartedBody: EntryTypeName.SKILL_STARTED,
        entry_attention.SkillFinishedBody: EntryTypeName.SKILL_FINISHED,
        entry_attention.QuestionAskedBody: EntryTypeName.QUESTION_ASKED,
        entry_attention.QuestionAnsweredBody: EntryTypeName.QUESTION_ANSWERED,
        entry_attention.PlanProposedBody: EntryTypeName.PLAN_PROPOSED,
        entry_attention.PlanResolvedBody: EntryTypeName.PLAN_RESOLVED,
        entry_lifecycle.CompactionStartedBody: EntryTypeName.COMPACTION_STARTED,
        entry_lifecycle.CompactionFinishedBody: EntryTypeName.COMPACTION_FINISHED,
        entry_lifecycle.AssignmentStartedBody: EntryTypeName.ASSIGNMENT_STARTED,
        entry_lifecycle.AssignmentFinishedBody: EntryTypeName.ASSIGNMENT_FINISHED,
        entry_lifecycle.ModelChangeBody: EntryTypeName.MODEL_CHANGE,
        entry_lifecycle.EffortChangeBody: EntryTypeName.EFFORT_CHANGE,
    },
)

BODY_TYPES: Mapping[EntryTypeName, type[EntryBody]] = MappingProxyType(
    {entry_type: body_type for body_type, entry_type in ENTRY_TYPES.items()},
)

PENDING_ATTENTION_ENTRY_TYPES: tuple[EntryTypeName, ...] = (
    EntryTypeName.QUESTION_ASKED,
    EntryTypeName.QUESTION_ANSWERED,
    EntryTypeName.PLAN_PROPOSED,
    EntryTypeName.PLAN_RESOLVED,
    # The turn end closes the attentions of that actor. It is not an attention
    # body, but the fold cannot answer without it: a harness cannot end a turn
    # while its dialog waits, so an attention that outlives its turn has no one
    # left to answer it. The actor link is used, not the turn id, because a
    # source can lose the turn join and still record the turn end.
    EntryTypeName.TURN_FINISHED,
)


@dataclass(frozen=True)
class SessionEntry:
    """Hold one immutable row in a stored session feed."""

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
        """Closed vocabulary name for this entry body."""
        return ENTRY_TYPES[type(self.body)]


def pending_attention(entries: Sequence[SessionEntry]) -> tuple[SessionEntry, ...]:
    """Return unresolved questions and plans, from the oldest to the newest.

    The supplied entries must carry PENDING_ATTENTION_ENTRY_TYPES: a turn end
    is an input to the fold. An attention a source failed to resolve stays
    pending until its turn ends, and no longer.

    Returns:
        Unresolved questions and plans, from the oldest to the newest.

    """
    open_attentions: dict[AttentionId, SessionEntry] = {}
    for entry in entries:
        entry_body = entry.body
        if isinstance(
            entry_body,
            (entry_attention.QuestionAskedBody, entry_attention.PlanProposedBody),
        ):
            open_attentions[entry_body.attention_id] = entry
        elif isinstance(
            entry_body,
            (entry_attention.QuestionAnsweredBody, entry_attention.PlanResolvedBody),
        ):
            open_attentions.pop(entry_body.attention_id, None)
        elif isinstance(entry_body, entry_conversation.TurnFinishedBody):
            open_attentions = {
                attention_id: open_entry
                for attention_id, open_entry in open_attentions.items()
                if open_entry.actor_id != entry.actor_id
            }
    return tuple(open_attentions.values())
