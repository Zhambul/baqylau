"""The actor half of the aggregate: who is working, on what, and at what cost.

Five writers over one row per actor. Every one of them updates an actor that
already EXISTS and none of them creates one — `ActorWriter` is the only birth,
because a usage report for an actor nobody announced is a fact about a name we
cannot describe, and putting a nameless row on the list is worse than waiting.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from domain.events import (
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    ActorDescriptionChanged,
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
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
    SessionFinished,
    SessionStarted,
    ShellBackgrounded,
    ShellFinished,
    ShellOutputFinished,
    ShellStarted,
    SkillFinished,
    SkillStarted,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
    TurnStarted,
    UsageReported,
    WebFetched,
    WorktreeChanged,
)
from domain.ids import AttentionId, ShellId
from domain.records import CommittedEvent
from domain.sessiondata import (
    ActorContext,
    ActorFacts,
    ActorStatistics,
    ActorUsage,
)
from domain.values import FileAction
from engine.sessiondata.contract import AggregateState, SessionDataWriter
from engine.sessiondata.naming import ModelNaming

# Which tool name a file access counts as on the scoreboard. A file fact has no
# tool of its own — every harness spells the tools differently — so the ACTION
# is the name, which is what a reader is counting anyway.
FILE_TOOLS: dict[FileAction, str] = {
    "read": "Read",
    "created": "Write",
    "updated": "Edit",
    "deleted": "Delete",
    "renamed": "Move",
}


class ActorWriter(SessionDataWriter):
    """Who the actors are: their birth, their names, and whether they are done."""

    def __init__(self, model_naming: ModelNaming | None = None) -> None:
        self.model_naming = model_naming or ModelNaming()

    def write(
        self, committed_event: CommittedEvent, aggregate_state: AggregateState
    ) -> AggregateState:
        event = committed_event.event
        payload = event.payload
        if isinstance(payload, ActorStarted):
            existing = aggregate_state.actor(event.actor_id)
            born = ActorFacts(
                session_id=event.session_id,
                actor_id=event.actor_id,
                role=payload.role,
                name=payload.name,
                state="running",
                parent_actor_id=event.parent_actor_id,
                started_at=committed_event.happened_at,
            )
            # An actor announced twice — two evidence streams both saying so —
            # keeps everything already folded about it and only reopens.
            return aggregate_state.with_actor(
                born
                if existing is None
                else replace(existing, state="running", finished_at=None)
            )
        actor = aggregate_state.actor(event.actor_id)
        if actor is None:
            return aggregate_state
        if isinstance(payload, ActorNameChanged):
            return aggregate_state.with_actor(replace(actor, name=payload.name))
        if isinstance(payload, ActorDescriptionChanged):
            return aggregate_state.with_actor(replace(actor, description=payload.description))
        if isinstance(payload, (ActorFinished, ActorAssignmentFinished)):
            return aggregate_state.with_actor(
                replace(actor, state="finished", finished_at=committed_event.happened_at)
            )
        if isinstance(payload, ActorAssignmentStarted):
            return aggregate_state.with_actor(replace(actor, state="running", finished_at=None))
        if isinstance(payload, ModelChanged):
            if payload.current.native_id == "<synthetic>":
                return aggregate_state  # a machine-injected record, not a model

            # The display settles HERE, through the harness's one namer, so an
            # unrefined alias ("sonnet") and its later native id show the same
            # name — and a rebuild re-settles history too.
            named = self.model_naming.named(event.harness, payload.current)
            return aggregate_state.with_actor(replace(actor, model=named))
        if isinstance(payload, EffortChanged):
            return aggregate_state.with_actor(replace(actor, effort=payload.current))
        return aggregate_state


class StatusWriter(SessionDataWriter):
    """The one word an actor's tab colour and list row are painted from.

    A replay, not a rule engine: the last fact wins, and the ORDER of the
    branches below is the whole semantics. Two sets carry between events — the
    background commands still running, and the attentions still unanswered —
    because two of the branches ask a question about the past that no single
    event can answer.

    The one asymmetry worth naming: a command's finish does NOT end background
    work. A background job's launch reports finished immediately, while its
    output still flows, so ending it there emptied the set before a turn could
    ever end on it — which is how `awaiting_background` became unreachable and a
    session with a job still running read as idle. `shell.output_finished` is
    what ends background work.
    """

    def write(
        self, committed_event: CommittedEvent, aggregate_state: AggregateState
    ) -> AggregateState:
        event = committed_event.event
        payload = event.payload
        if isinstance(payload, SessionFinished):
            # The session is over: nobody is doing anything, and every actor
            # should say so rather than keep the last thing it was doing.
            return aggregate_state.with_actors(
                {
                    actor_id: replace(actor, status=None)
                    for actor_id, actor in dict(aggregate_state.actors).items()
                }
            )
        actor = aggregate_state.actor(event.actor_id)
        if actor is None:
            return aggregate_state
        if isinstance(payload, SessionStarted) or (
            isinstance(payload, ActorStarted) and actor.status is None
        ):
            # An actor that has just been announced has done nothing yet, which
            # is what `idle` says. Both facts are here because they arrive in
            # either order and either one can be the first this actor sees: a
            # lead is born right after its session, a subagent long after.
            return aggregate_state.with_actor(replace(actor, status="idle"))
        if isinstance(payload, TurnStarted) or _is_prompt(payload):
            return aggregate_state.with_actor(replace(actor, status="thinking"))
        if isinstance(payload, ReasoningCreated):
            return aggregate_state.with_actor(replace(actor, status="working"))
        if isinstance(payload, ShellStarted):
            return aggregate_state.with_actor(_shell_started(actor, payload))
        if isinstance(payload, (SkillStarted, TaskChanged, TaskListChanged)):
            # A task tool is work being done, the same as a command: this is
            # what the `task` category set before the categories dissolved.
            return aggregate_state.with_actor(replace(actor, status="executing"))
        if isinstance(payload, ShellBackgrounded):
            # Background work gained, status untouched: `awaiting_background` is
            # reached at the END of a turn, not the moment a job moves.
            return aggregate_state.with_actor(
                _with_background(actor, payload.shell_id, counts_as_job=True)
            )
        if isinstance(payload, ShellOutputFinished):
            return aggregate_state.with_actor(_without_background(actor, payload.shell_id))
        if isinstance(payload, (QuestionAsked, PlanProposed)):
            return aggregate_state.with_actor(
                replace(
                    actor,
                    status="awaiting_attention",
                    pending_attention_internal=_added(
                        actor.pending_attention_internal, payload.attention_id
                    ),
                )
            )
        if isinstance(payload, (QuestionAnswered, PlanResolved)):
            return aggregate_state.with_actor(
                replace(
                    actor,
                    status="working",
                    pending_attention_internal=tuple(
                        pending
                        for pending in actor.pending_attention_internal
                        if pending != payload.attention_id
                    ),
                )
            )
        if isinstance(payload, CompactionStarted):
            return aggregate_state.with_actor(replace(actor, status="working"))
        if _is_finished_work(payload):
            return aggregate_state.with_actor(
                replace(
                    actor,
                    status=(
                        "awaiting_attention"
                        if actor.pending_attention_internal
                        else "working"
                    ),
                )
            )
        if isinstance(payload, (TurnFinished, TurnAborted)):
            return aggregate_state.with_actor(
                replace(
                    actor,
                    status=(
                        "awaiting_background"
                        if actor.background.running_shell_ids
                        else "awaiting_response"
                    ),
                )
            )
        return aggregate_state


def _is_prompt(event_payload: EventPayload) -> bool:
    return (
        isinstance(event_payload, MessageCreated)
        and event_payload.role == "user"
        and event_payload.phase == "prompt"
    )


def _is_finished_work(event_payload: EventPayload) -> bool:
    """Work that ended and was not background. Every one of these was one
    `operation.finished` before the operation abstraction dissolved, and the
    file and search ones arrive only at result time now — which is the same
    branch they used to land on twice."""
    return isinstance(
        event_payload,
        (ShellFinished, SkillFinished, FileAccessed, SearchPerformed, WebFetched, WorktreeChanged),
    )


def _shell_started(actor_facts: ActorFacts, shell_started: ShellStarted) -> ActorFacts:
    if shell_started.execution == "foreground":
        return replace(actor_facts, status="executing")
    counted = replace(
        actor_facts.background,
        monitor_count=(
            actor_facts.background.monitor_count + (shell_started.execution == "monitor")
        ),
        background_job_count=(
            actor_facts.background.background_job_count
            + (shell_started.execution == "background")
        ),
    )
    return replace(
        _with_background(replace(actor_facts, background=counted), shell_started.shell_id),
        status="executing",
    )


def _with_background(
    actor_facts: ActorFacts,
    shell_id: ShellId,
    *,
    counts_as_job: bool = False,
) -> ActorFacts:
    """Add a command to the running-background set.

    `counts_as_job` is for the command that MOVED there mid-run: nothing counted
    it at launch, because at launch nobody knew.
    """
    background = actor_facts.background
    if shell_id in background.running_shell_ids:
        return actor_facts
    return replace(
        actor_facts,
        background=replace(
            background,
            running_shell_ids=(*background.running_shell_ids, shell_id),
            background_job_count=background.background_job_count + counts_as_job,
        ),
    )


def _without_background(actor_facts: ActorFacts, shell_id: ShellId) -> ActorFacts:
    return replace(
        actor_facts,
        background=replace(
            actor_facts.background,
            running_shell_ids=tuple(
                running
                for running in actor_facts.background.running_shell_ids
                if running != shell_id
            ),
        ),
    )


def _added(
    pending: tuple[AttentionId, ...], value: AttentionId
) -> tuple[AttentionId, ...]:
    return pending if value in pending else (*pending, value)


class UsageWriter(SessionDataWriter):
    """Tokens and money, cumulatively.

    A harness reports usage either as a running total or as one response's
    share, and it says which — so a total REPLACES and a share ADDS, and
    treating them alike is how a session's cost silently doubles.
    """

    def write(
        self, committed_event: CommittedEvent, aggregate_state: AggregateState
    ) -> AggregateState:
        payload = committed_event.event.payload
        if not isinstance(payload, UsageReported):
            return aggregate_state
        actor = aggregate_state.actor(committed_event.event.actor_id)
        if actor is None:
            return aggregate_state
        usage = actor.usage
        tokens = payload.tokens if payload.cumulative else usage.tokens + payload.tokens
        cost = _cost(usage.cost_in_usd, payload.cost_in_usd, payload.cumulative)
        return aggregate_state.with_actor(replace(actor, usage=ActorUsage(tokens, cost)))


def _cost(
    known: Decimal | None,
    reported: Decimal | None,
    cumulative: bool,
) -> Decimal | None:
    if reported is None:
        return known
    if cumulative or known is None:
        return reported
    return known + reported


class ContextWriter(SessionDataWriter):
    """How full the window is, and whether it is being emptied."""

    def write(
        self, committed_event: CommittedEvent, aggregate_state: AggregateState
    ) -> AggregateState:
        payload = committed_event.event.payload
        actor = aggregate_state.actor(committed_event.event.actor_id)
        if actor is None:
            return aggregate_state
        if isinstance(payload, ContextReported):
            return aggregate_state.with_actor(
                replace(
                    actor,
                    context=ActorContext(
                        used_tokens=payload.used_tokens,
                        window_tokens=payload.window_tokens,
                        compacting=actor.context.compacting,
                    ),
                )
            )
        if isinstance(payload, CompactionStarted):
            return aggregate_state.with_actor(
                replace(actor, context=replace(actor.context, compacting=True))
            )
        if isinstance(payload, CompactionFinished):
            return aggregate_state.with_actor(
                replace(
                    actor,
                    context=replace(
                        actor.context,
                        compacting=False,
                        used_tokens=(
                            payload.after_tokens
                            if payload.after_tokens is not None
                            else actor.context.used_tokens
                        ),
                    ),
                )
            )
        return aggregate_state


class StatisticsWriter(SessionDataWriter):
    """The scoreboard: what the actor did, counted once as it happened.

    `active_seconds` counts CLOSED intervals only — a prompt to the end of the
    turn it started. The interval still open has no length until somebody asks,
    so the route that answers adds it; storing a number that grows on its own
    would mean writing a row per second.
    """

    def write(
        self, committed_event: CommittedEvent, aggregate_state: AggregateState
    ) -> AggregateState:
        event = committed_event.event
        payload = event.payload
        actor = aggregate_state.actor(event.actor_id)
        if actor is None:
            return aggregate_state
        statistics = _counted(actor.statistics, payload)
        statistics = _timed(statistics, committed_event)
        if statistics == actor.statistics:
            return aggregate_state
        return aggregate_state.with_actor(replace(actor, statistics=statistics))


def _counted(actor_statistics: ActorStatistics, event_payload: EventPayload) -> ActorStatistics:
    if _is_prompt(event_payload):
        return replace(actor_statistics, prompt_count=actor_statistics.prompt_count + 1)
    if isinstance(event_payload, MessageCreated) and event_payload.recipient_actor_id is not None:
        return replace(
            actor_statistics, actor_message_count=actor_statistics.actor_message_count + 1
        )
    if isinstance(event_payload, ShellStarted):
        return replace(
            actor_statistics, shell_command_count=actor_statistics.shell_command_count + 1
        )
    if isinstance(event_payload, ShellFinished):
        if event_payload.outcome == "succeeded":
            return actor_statistics
        return replace(
            actor_statistics,
            failed_shell_command_count=actor_statistics.failed_shell_command_count + 1,
        )
    if isinstance(event_payload, FileAccessed):
        return _file_counted(actor_statistics, event_payload)
    if isinstance(event_payload, SearchPerformed):
        return _tool_counted(actor_statistics, event_payload.tool)
    if isinstance(event_payload, WebFetched):
        return _tool_counted(actor_statistics, "WebFetch")
    if isinstance(event_payload, WorktreeChanged):
        return _tool_counted(
            actor_statistics,
            "EnterWorktree" if event_payload.action == "entered" else "ExitWorktree",
        )
    if isinstance(event_payload, SkillStarted):
        return _tool_counted(actor_statistics, "Skill")
    return actor_statistics


def _file_counted(actor_statistics: ActorStatistics, file_accessed: FileAccessed) -> ActorStatistics:
    paths = actor_statistics.file_paths_internal
    if file_accessed.path not in paths:
        paths = (*paths, file_accessed.path)
    return _tool_counted(
        replace(
            actor_statistics,
            file_paths_internal=paths,
            file_count=len(paths),
            lines_added=actor_statistics.lines_added + (file_accessed.lines_added or 0),
            lines_removed=actor_statistics.lines_removed + (file_accessed.lines_removed or 0),
        ),
        FILE_TOOLS[file_accessed.action],
    )


def _tool_counted(actor_statistics: ActorStatistics, tool: str) -> ActorStatistics:
    counts = dict(actor_statistics.tool_counts)
    counts[tool] = counts.get(tool, 0) + 1
    return replace(actor_statistics, tool_counts=tuple(sorted(counts.items())))


def _timed(actor_statistics: ActorStatistics, committed_event: CommittedEvent) -> ActorStatistics:
    """One interval at a time: it opens when the actor has something to do and
    closes when the turn it was doing ends.

    Three facts can open it, because any of them can be the first this actor
    sees — its session starting, itself starting, or a prompt arriving mid-run.
    Whichever comes first opens it, and the rest are already inside it.
    """
    payload = committed_event.event.payload
    at = committed_event.happened_at
    if actor_statistics.active_since_internal is None:
        if isinstance(payload, (SessionStarted, ActorStarted)) or _is_prompt(payload):
            return replace(actor_statistics, active_since_internal=at)
        return actor_statistics
    if isinstance(payload, (TurnFinished, TurnAborted, SessionFinished)):
        return replace(
            actor_statistics,
            active_seconds=actor_statistics.active_seconds
            + max(0.0, at - actor_statistics.active_since_internal),
            active_since_internal=None,
        )
    return actor_statistics
