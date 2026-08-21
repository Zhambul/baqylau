"""Canonical event → feed entry, one mapping per feed-worthy kind.

The one place the two vocabularies meet. Everything a reader is shown is decided
here, once, at push time: which facts appear at all, and what each carries after
the audit detail is dropped.

Events that produce nothing are as deliberate as the ones that do —
`session.*`, `actor.*`, `task.*`, `goal.changed`, `usage.reported`,
`context.reported`, `shell.input_provided` and `shell.output_located` feed the
aggregate or the daemon's own machinery, and a feed that showed them would be
showing plumbing.
"""

from __future__ import annotations

from domain.entries import (
    AssignmentFinishedBody,
    AssignmentStartedBody,
    CompactionFinishedBody,
    CompactionStartedBody,
    EffortChangeBody,
    EntryBody,
    FileBody,
    FileState,
    MessageBody,
    ModelChangeBody,
    PlanProposedBody,
    PlanResolvedBody,
    QuestionAnsweredBody,
    QuestionAskedBody,
    ReasoningBody,
    RunState,
    SearchBody,
    SessionEntry,
    ShellBackgroundedBody,
    ShellFinishedBody,
    ShellOutputBody,
    ShellStartedBody,
    SkillFinishedBody,
    SkillStartedBody,
    TurnFinishedBody,
    TurnStartedBody,
    WebBody,
    WorktreeBody,
)
from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CompactionFinished,
    CompactionStarted,
    EffortChanged,
    EventPayload,
    FileAccessed,
    MessageCreated,
    ModelChanged,
    PlanProposed,
    PlanResolved,
    QuestionAnswered,
    QuestionAsked,
    ReasoningCreated,
    SearchPerformed,
    ShellBackgrounded,
    ShellFinished,
    ShellProgressed,
    ShellStarted,
    SkillFinished,
    SkillStarted,
    TurnAborted,
    TurnFinished,
    TurnStarted,
    WebFetched,
    WorktreeChanged,
)
from domain.records import CommittedEvent
from engine.sessiondata.naming import ModelNaming
from domain.values import Outcome, TextContent, content_text
from engine.sessiondata.contract import SessionEntryWriter


def run_state(outcome: Outcome) -> RunState:
    """A feed shows three ends, not five. `rejected` is a refusal to run, which
    is a failure to whoever was waiting for it, and `unknown` is the honest
    answer to "did it work?" only where somebody can act on it — nobody can."""
    if outcome == "cancelled":
        return "cancelled"
    return "succeeded" if outcome == "succeeded" else "failed"


def file_state(outcome: Outcome) -> FileState:
    return "succeeded" if outcome == "succeeded" else "failed"


class EntryWriter(SessionEntryWriter):
    def __init__(self, model_naming: ModelNaming | None = None) -> None:
        self.model_naming = model_naming or ModelNaming()

    def entry(self, canonical_event: CommittedEvent) -> SessionEntry | None:
        event = canonical_event.event
        body = _body(event.payload, event.harness, self.model_naming)
        if body is None:
            return None
        return SessionEntry(
            entry_id=event.event_id,
            session_id=event.session_id,
            actor_id=event.actor_id,
            parent_actor_id=event.parent_actor_id,
            turn_id=event.turn_id,
            # Always a number: a feed shows when things happened, and a source
            # that carries no clock of its own would otherwise leave a hole in
            # the middle of a conversation.
            occurred_at=canonical_event.happened_at,
            summary=_summary(event.payload),
            body=body,
        )


def _summary(payload: EventPayload) -> str | None:
    """The harness's own words about the fact, where it offered any."""
    if isinstance(payload, ShellStarted):
        return payload.description
    if isinstance(payload, ActorAssignmentStarted):
        return content_text(payload.brief) or None
    return None


def _body(payload: EventPayload, harness: str, model_naming: ModelNaming) -> EntryBody | None:
    if isinstance(payload, TurnStarted):
        return TurnStartedBody()
    if isinstance(payload, TurnFinished):
        return TurnFinishedBody("finished")
    if isinstance(payload, TurnAborted):
        return TurnFinishedBody("aborted")
    if isinstance(payload, MessageCreated):
        return MessageBody(
            payload.message_id,
            payload.role,
            payload.phase,
            payload.content,
            payload.recipient_actor_id,
            payload.reply_to,
        )
    if isinstance(payload, ReasoningCreated):
        return ReasoningBody(payload.reasoning_id, payload.content)
    if isinstance(payload, ShellStarted):
        return ShellStartedBody(payload.shell_id, payload.command, payload.execution)
    if isinstance(payload, ShellProgressed):
        return ShellOutputBody(
            payload.shell_id, payload.stream, payload.mode, payload.content
        )
    if isinstance(payload, ShellBackgrounded):
        return ShellBackgroundedBody(payload.shell_id)
    if isinstance(payload, ShellFinished):
        return ShellFinishedBody(
            payload.shell_id, run_state(payload.outcome), payload.exit_code, payload.result
        )
    if isinstance(payload, FileAccessed):
        return FileBody(
            payload.path,
            payload.action,
            file_state(payload.outcome),
            payload.previous_path,
            payload.lines_added,
            payload.lines_removed,
            # A changed file is shown as its diff, a created or read one as its
            # text. Both arrive on the fact; which one reads as "the content"
            # depends on what happened to it.
            payload.content if payload.unified_diff is None else _diff(payload),
        )
    if isinstance(payload, SearchPerformed):
        return SearchBody(
            payload.tool, payload.query, file_state(payload.outcome), payload.result
        )
    if isinstance(payload, WebFetched):
        return WebBody(payload.url, file_state(payload.outcome), payload.result)
    if isinstance(payload, WorktreeChanged):
        return WorktreeBody(
            payload.action, file_state(payload.outcome), payload.arguments
        )
    if isinstance(payload, SkillStarted):
        return SkillStartedBody(payload.skill_id, payload.name, payload.arguments)
    if isinstance(payload, SkillFinished):
        return SkillFinishedBody(
            payload.skill_id, run_state(payload.outcome), payload.result
        )
    if isinstance(payload, QuestionAsked):
        return QuestionAskedBody(payload.attention_id, payload.questions)
    if isinstance(payload, QuestionAnswered):
        return QuestionAnsweredBody(
            payload.attention_id, payload.answers, payload.feedback
        )
    if isinstance(payload, PlanProposed):
        return PlanProposedBody(payload.attention_id, payload.plan)
    if isinstance(payload, PlanResolved):
        return PlanResolvedBody(
            payload.attention_id, payload.state, payload.feedback, payload.edited
        )
    if isinstance(payload, CompactionStarted):
        return CompactionStartedBody(payload.before_tokens)
    if isinstance(payload, CompactionFinished):
        return CompactionFinishedBody(payload.before_tokens, payload.after_tokens)
    if isinstance(payload, ActorAssignmentStarted):
        return AssignmentStartedBody(
            payload.assignment_id, payload.actor_name, payload.prompt
        )
    if isinstance(payload, ActorAssignmentFinished):
        return AssignmentFinishedBody(
            payload.assignment_id, run_state(payload.outcome), payload.result
        )
    if isinstance(payload, ModelChanged):
        if not _is_a_switch(payload):
            return None
        return ModelChangeBody(
            model_naming.display(harness, payload.current),
            model_naming.display(harness, payload.previous)
            if payload.previous is not None
            else None,
            payload.reason == "automatic_fallback",
        )
    if isinstance(payload, EffortChanged):
        if payload.previous is None or payload.previous == payload.current:
            return None
        return EffortChangeBody(payload.current, payload.previous)
    return None


def _is_a_switch(payload: ModelChanged) -> bool:
    """Whether this fact is a person or a harness CHANGING the model, as opposed
    to reporting one for the first time or spelling it more precisely.

    Two things that are not switches, and both used to draw a line in the feed:

    An INITIAL REPORT. Both harnesses state the current model at launch and again
    on the first response, and a first observation cannot know what it replaced —
    so `previous` is empty, and the feed drew "model <name>" as though somebody
    had just chosen it. The fact still lands: the aggregate takes the value
    (engine/sessiondata/actors.py), because what an actor is running on is
    exactly the kind of thing an aggregate holds. It is only not an EVENT in the
    reader's sense.

    A NAME BEING REFINED. A launch selects an alias, and the harness later reports
    the full id that alias resolved to. Same selection, two spellings, and the
    feed drew an arrow between them — the product telling a person something
    changed when they had made one choice. Compared by `selection_id` when both
    sides carry one, because that is the identity of the CHOICE; native ids are
    the fallback for the sources that report no selection at all.
    """
    previous, current = payload.previous, payload.current
    if previous is None:
        return False
    # A source may stamp machine-injected records with the pseudo-model
    # "<synthetic>". Facts carrying it exist in stored history, and a change to
    # or from a pseudo-model is a report about machinery, never a switch.
    if "<synthetic>" in (previous.native_id, current.native_id):
        return False
    if previous.selection_id is not None and current.selection_id is not None:
        return previous.selection_id != current.selection_id
    return previous.native_id != current.native_id


def _diff(payload: FileAccessed) -> TextContent:
    """A diff is text somebody reads, so it travels as content rather than as a
    field of its own — which is what lets the entry body have ONE content field
    instead of two that are never both filled."""
    return TextContent(payload.unified_diff or "", "text/plain")



