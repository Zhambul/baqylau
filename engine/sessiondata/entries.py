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
    TurnState,
    WebBody,
    WorktreeBody,
)
from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CanonicalEvent,
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
from engine.sessiondata.naming import ModelNaming
from domain.ids import HarnessName
from domain.values import MediaType, Outcome, TextContent, content_text
from engine.sessiondata.contract import SessionEntryWriter


def run_state(outcome: Outcome) -> RunState:
    """A feed shows three ends, not five. `rejected` is a refusal to run, which
    is a failure to whoever was waiting for it, and `unknown` is the honest
    answer to "did it work?" only where somebody can act on it — nobody can."""
    if outcome == Outcome.CANCELLED:
        return RunState.CANCELLED
    return RunState.SUCCEEDED if outcome == Outcome.SUCCEEDED else RunState.FAILED


def file_state(outcome: Outcome) -> FileState:
    return FileState.SUCCEEDED if outcome == Outcome.SUCCEEDED else FileState.FAILED


class EntryWriter(SessionEntryWriter):
    def __init__(self, model_naming: ModelNaming | None = None) -> None:
        self.model_naming = model_naming or ModelNaming()

    def entry(self, canonical_event: CanonicalEvent[EventPayload]) -> SessionEntry | None:
        event = canonical_event
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


def _summary(event_payload: EventPayload) -> str | None:
    """The harness's own words about the fact, where it offered any."""
    if isinstance(event_payload, ShellStarted):
        return event_payload.description
    if isinstance(event_payload, ActorAssignmentStarted):
        return content_text(event_payload.brief) or None
    return None


def _body(event_payload: EventPayload, harness: HarnessName, model_naming: ModelNaming) -> EntryBody | None:
    if isinstance(event_payload, TurnStarted):
        return TurnStartedBody()
    if isinstance(event_payload, TurnFinished):
        return TurnFinishedBody(TurnState.FINISHED)
    if isinstance(event_payload, TurnAborted):
        return TurnFinishedBody(TurnState.ABORTED)
    if isinstance(event_payload, MessageCreated):
        return MessageBody(
            event_payload.message_id,
            event_payload.role,
            event_payload.phase,
            event_payload.content,
            event_payload.recipient_actor_id,
            event_payload.reply_to,
        )
    if isinstance(event_payload, ReasoningCreated):
        return ReasoningBody(event_payload.reasoning_id, event_payload.content)
    if isinstance(event_payload, ShellStarted):
        return ShellStartedBody(event_payload.shell_id, event_payload.command, event_payload.execution)
    if isinstance(event_payload, ShellProgressed):
        return ShellOutputBody(
            event_payload.shell_id, event_payload.stream, event_payload.mode, event_payload.content
        )
    if isinstance(event_payload, ShellBackgrounded):
        return ShellBackgroundedBody(event_payload.shell_id)
    if isinstance(event_payload, ShellFinished):
        return ShellFinishedBody(
            event_payload.shell_id, run_state(event_payload.outcome), event_payload.exit_code, event_payload.result
        )
    if isinstance(event_payload, FileAccessed):
        return FileBody(
            event_payload.path,
            event_payload.action,
            file_state(event_payload.outcome),
            event_payload.previous_path,
            event_payload.lines_added,
            event_payload.lines_removed,
            # A changed file is shown as its diff, a created or read one as its
            # text. Both arrive on the fact; which one reads as "the content"
            # depends on what happened to it.
            event_payload.content if event_payload.unified_diff is None else _diff(event_payload),
        )
    if isinstance(event_payload, SearchPerformed):
        return SearchBody(
            event_payload.tool, event_payload.query, file_state(event_payload.outcome), event_payload.result
        )
    if isinstance(event_payload, WebFetched):
        return WebBody(event_payload.url, file_state(event_payload.outcome), event_payload.result)
    if isinstance(event_payload, WorktreeChanged):
        return WorktreeBody(
            event_payload.action, file_state(event_payload.outcome), event_payload.arguments
        )
    if isinstance(event_payload, SkillStarted):
        return SkillStartedBody(event_payload.skill_id, event_payload.name, event_payload.arguments)
    if isinstance(event_payload, SkillFinished):
        return SkillFinishedBody(
            event_payload.skill_id, run_state(event_payload.outcome), event_payload.result
        )
    if isinstance(event_payload, QuestionAsked):
        return QuestionAskedBody(event_payload.attention_id, event_payload.questions)
    if isinstance(event_payload, QuestionAnswered):
        return QuestionAnsweredBody(
            event_payload.attention_id, event_payload.answers, event_payload.feedback
        )
    if isinstance(event_payload, PlanProposed):
        return PlanProposedBody(event_payload.attention_id, event_payload.plan)
    if isinstance(event_payload, PlanResolved):
        return PlanResolvedBody(
            event_payload.attention_id, event_payload.state, event_payload.feedback, event_payload.edited
        )
    if isinstance(event_payload, CompactionStarted):
        return CompactionStartedBody(event_payload.before_tokens)
    if isinstance(event_payload, CompactionFinished):
        return CompactionFinishedBody(event_payload.before_tokens, event_payload.after_tokens)
    if isinstance(event_payload, ActorAssignmentStarted):
        return AssignmentStartedBody(
            event_payload.assignment_id, event_payload.actor_name, event_payload.prompt
        )
    if isinstance(event_payload, ActorAssignmentFinished):
        return AssignmentFinishedBody(
            event_payload.assignment_id, run_state(event_payload.outcome), event_payload.result
        )
    if isinstance(event_payload, ModelChanged):
        if not _is_a_switch(event_payload):
            return None
        return ModelChangeBody(
            model_naming.display(harness, event_payload.current),
            model_naming.display(harness, event_payload.previous)
            if event_payload.previous is not None
            else None,
            event_payload.reason == "automatic_fallback",
        )
    if isinstance(event_payload, EffortChanged):
        if event_payload.previous is None or event_payload.previous == event_payload.current:
            return None
        return EffortChangeBody(event_payload.current, event_payload.previous)
    return None


def _is_a_switch(model_changed: ModelChanged) -> bool:
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
    previous, current = model_changed.previous, model_changed.current
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


def _diff(file_accessed: FileAccessed) -> TextContent:
    """A diff is text somebody reads, so it travels as content rather than as a
    field of its own — which is what lets the entry body have ONE content field
    instead of two that are never both filled."""
    return TextContent(file_accessed.unified_diff or "", MediaType.TEXT_PLAIN)



