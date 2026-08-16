"""Harness-neutral semantic read models folded from canonical facts."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from threading import RLock
from types import MappingProxyType
from typing import Literal, Mapping, TypeAlias

from domain.events import (
    ActorDescriptionChanged,
    ActorFinished,
    ActorNameChanged,
    ActorStarted,
    AttentionRequested,
    AttentionResolved,
    CanonicalEvent,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    CompactionFinished,
    CompactionStarted,
    ContextReported,
    EffortChanged,
    FileAccessed,
    GoalChanged,
    MessageCreated,
    ModelChanged,
    OperationFinished,
    OperationProgressed,
    OperationStarted,
    ActorMessageSent,
    ReasoningCreated,
    SessionAccountChanged,
    SessionFinished,
    SessionStarted,
    SessionTitleChanged,
    SessionWorkingDirectoryChanged,
    TaskChanged,
    TaskListChanged,
    TurnAborted,
    TurnFinished,
    TurnStarted,
    UsageReported,
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
from runtime.canonical_store import CanonicalEventPage, CanonicalEventStore, StoredCanonicalEvent
from runtime.sessions import SessionStore


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
    role: Literal["user", "assistant", "system", "peer"]
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


def _context(
    stored_event: StoredCanonicalEvent,
    activity_id: str,
    actor_name: str | None,
    *,
    finished: bool = False,
) -> ActivityContext:
    event = stored_event.event
    event_time = event.occurred_at if event.occurred_at is not None else stored_event.accepted_at
    return ActivityContext(
        activity_id=activity_id,
        source_event_ids=(event.event_id,),
        session_id=event.session_id,
        actor_id=event.actor_id,
        actor_name=actor_name,
        parent_actor_id=event.parent_actor_id,
        turn_id=event.turn_id,
        started_at=None if finished else event_time,
        finished_at=event_time if finished else None,
    )


def _activity_id(event: CanonicalEvent, activity_type: str, subject_id: object) -> str:
    return f"{activity_type}:{event.actor_id}:{subject_id}"


class SessionQueries:
    def __init__(self, canonical_store: CanonicalEventStore, sessions: SessionStore) -> None:
        self.canonical_store = canonical_store
        self.session_registry = sessions
        self._latest_pages: dict[SessionId, tuple[int | None, tuple[StoredCanonicalEvent, ...]]] = {}
        self._tail_pages: dict[tuple[SessionId, int], CanonicalEventPage] = {}
        self._latest_pages_lock = RLock()

    def _tail_page(
        self,
        session_id: SessionId,
        event_limit: int,
        through_cursor: int,
    ) -> CanonicalEventPage:
        key = (session_id, event_limit)
        with self._latest_pages_lock:
            cached = self._tail_pages.get(key)
            if cached is not None and cached.cursor == through_cursor:
                return cached
        page = self.canonical_store.tail(session_id, through_cursor, event_limit)
        with self._latest_pages_lock:
            self._tail_pages[key] = page
        return page

    def _page(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> CanonicalEventPage:
        selected_cursor = (
            self.canonical_store.latest_cursor()
            if through_cursor is None
            else through_cursor
        )
        session_cursor = self.canonical_store.latest_session_cursor(session_id, selected_cursor)
        with self._latest_pages_lock:
            cached = self._latest_pages.get(session_id)
            if (
                cached is not None
                and cached[0] is not None
                and session_cursor is not None
                and cached[0] < session_cursor
            ):
                cached = (
                    session_cursor,
                    cached[1] + self.canonical_store.between(
                        session_id,
                        cached[0],
                        session_cursor,
                    ),
                )
                self._latest_pages[session_id] = cached
            elif cached is None or cached[0] != session_cursor:
                page = self.canonical_store.through(session_id, selected_cursor)
                cached = (session_cursor, page.events)
                self._latest_pages[session_id] = cached
            events = cached[1]
        page_cursor = events[-1].cursor if events else (selected_cursor or 0)
        return CanonicalEventPage(events, page_cursor, selected_cursor, False)

    def sessions(self, through_cursor: int | None = None) -> tuple[SessionSummary, ...]:
        summaries = (
            self.summary(session_id, through_cursor)
            for session_id in self.canonical_store.session_ids()
        )
        return tuple(
            sorted(
                (summary for summary in summaries if summary is not None),
                key=lambda summary: summary.started_at,
                reverse=True,
            )
        )

    def summary(self, session_id: SessionId, through_cursor: int | None = None) -> SessionSummary | None:
        stored_events = self._page(session_id, through_cursor).events
        started = next((stored for stored in stored_events if isinstance(stored.event.payload, SessionStarted)), None)
        if started is None:
            return None
        payload = started.event.payload
        custom_title = None
        automatic_title = payload.title
        summary_title = None
        prompt_title = None
        working_directory = payload.working_directory
        finished_at = None
        model = payload.model
        effort = payload.effort
        account = payload.account
        prompt_count = 0
        automatic_model_change = None
        state = "running"
        for stored in stored_events:
            event = stored.event
            if isinstance(event.payload, SessionStarted):
                state = "running"
                finished_at = None
                if event.payload.working_directory:
                    working_directory = event.payload.working_directory
            elif isinstance(event.payload, SessionTitleChanged):
                if event.payload.origin == "custom":
                    custom_title = event.payload.title or None
                elif event.payload.origin == "automatic":
                    automatic_title = event.payload.title or None
                else:
                    summary_title = event.payload.title or None
            elif isinstance(event.payload, SessionWorkingDirectoryChanged):
                working_directory = event.payload.working_directory
            elif isinstance(event.payload, SessionAccountChanged):
                account = event.payload.account
            elif isinstance(event.payload, SessionFinished):
                state = "finished"
                finished_at = event.occurred_at if event.occurred_at is not None else stored.accepted_at
            elif isinstance(event.payload, ModelChanged) and event.actor_id == started.event.actor_id:
                model = event.payload.current
                automatic_model_change = event.payload if event.payload.reason == "automatic_fallback" else None
            elif isinstance(event.payload, EffortChanged) and event.actor_id == started.event.actor_id:
                effort = event.payload.current
            elif (
                isinstance(event.payload, MessageCreated)
                and event.payload.role == "user"
                and event.payload.phase == "prompt"
            ):
                prompt_count += 1
                if prompt_title is None and isinstance(event.payload.content, TextContent):
                    first_line = event.payload.content.text.strip().splitlines()
                    prompt_title = first_line[0][:200] if first_line else None
        title = custom_title or automatic_title or summary_title or prompt_title
        return SessionSummary(
            session_id=session_id,
            harness=started.event.harness,
            title=title,
            working_directory=working_directory,
            initial_working_directory=payload.working_directory,
            started_at=(
                started.event.occurred_at
                if started.event.occurred_at is not None
                else started.accepted_at
            ),
            finished_at=finished_at,
            lead_actor_id=started.event.actor_id,
            model=model,
            effort=effort,
            account=account,
            prompt_count=prompt_count,
            automatic_model_change=automatic_model_change,
            state=state,
        )

    def actors(self, session_id: SessionId, through_cursor: int | None = None) -> tuple[ActorSummary, ...]:
        actors: dict[ActorId, ActorSummary] = {}
        for stored in self._page(session_id, through_cursor).events:
            event = stored.event
            payload = event.payload
            if isinstance(payload, ActorStarted):
                actors[event.actor_id] = ActorSummary(
                    actor_id=event.actor_id,
                    parent_actor_id=event.parent_actor_id,
                    harness=event.harness,
                    role=payload.role,
                    name=payload.name,
                    description=None,
                    model=None,
                    effort=None,
                    state="running",
                    started_at=(
                        event.occurred_at
                        if event.occurred_at is not None
                        else stored.accepted_at
                    ),
                    finished_at=None,
                )
            elif event.actor_id in actors:
                actor = actors[event.actor_id]
                if isinstance(payload, ActorNameChanged):
                    actors[event.actor_id] = replace(actor, name=payload.name)
                elif isinstance(payload, ActorDescriptionChanged):
                    actors[event.actor_id] = replace(actor, description=payload.description)
                elif isinstance(payload, ActorFinished):
                    actors[event.actor_id] = replace(
                        actor,
                        state="finished",
                        finished_at=(
                            event.occurred_at
                            if event.occurred_at is not None
                            else stored.accepted_at
                        ),
                    )
                elif isinstance(payload, ActorAssignmentStarted):
                    actors[event.actor_id] = replace(
                        actor,
                        state="running",
                        finished_at=None,
                    )
                elif isinstance(payload, ActorAssignmentFinished):
                    actors[event.actor_id] = replace(
                        actor,
                        state="finished",
                        finished_at=(
                            event.occurred_at
                            if event.occurred_at is not None
                            else stored.accepted_at
                        ),
                    )
                elif isinstance(payload, ModelChanged):
                    actors[event.actor_id] = replace(actor, model=payload.current)
                elif isinstance(payload, EffortChanged):
                    actors[event.actor_id] = replace(actor, effort=payload.current)
        return tuple(actors[actor_id] for actor_id in sorted(actors, key=str))

    def activity_after(
        self,
        session_id: SessionId,
        cursor: int,
        scope: ActivityScope,
        limit: int,
        through_cursor: int | None = None,
    ) -> ActivityPage:
        stored_events = self._page(session_id, through_cursor).events
        activities, _position_cursors, revision_cursors = self._activities(stored_events, scope)
        changed = sorted(
            (
                activity
                for activity in activities
                if revision_cursors[activity.context.activity_id] > cursor
            ),
            key=lambda activity: revision_cursors[activity.context.activity_id],
        )
        selected = tuple(changed[:limit])
        latest_cursor = self._page(session_id, through_cursor).latest_cursor
        page_cursor = (
            max(revision_cursors[activity.context.activity_id] for activity in selected)
            if changed
            else latest_cursor or cursor
        )
        return ActivityPage(page_cursor, latest_cursor, selected)

    def activity_before(
        self,
        session_id: SessionId,
        before_cursor: int | None,
        scope: ActivityScope,
        block_count: int,
        through_cursor: int | None = None,
    ) -> ActivityWindow:
        stored_events = self._page(session_id, through_cursor).events
        activities, position_cursors, _revision_cursors = self._activities(stored_events, scope)
        eligible = [
            activity
            for activity in activities
            if before_cursor is None or position_cursors[activity.context.activity_id] < before_cursor
        ]
        selected = tuple(eligible[-block_count:])
        cursors = [position_cursors[activity.context.activity_id] for activity in selected]
        return ActivityWindow(
            oldest_cursor=min(cursors, default=before_cursor or 0),
            activities=selected,
            has_more=len(eligible) > len(selected),
        )

    def activity_tail(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        event_limit: int,
        activity_limit: int,
        through_cursor: int,
    ) -> ActivityWindow:
        page = self._tail_page(session_id, event_limit, through_cursor)
        actor_events = self.canonical_store.events_of_types(
            session_id,
            ("actor.started", "actor.name_changed"),
            through_cursor,
        )
        recent_event_ids = {stored.event.event_id for stored in page.events}
        stored_events = tuple(
            sorted(
                (*(
                    stored for stored in actor_events
                    if stored.event.event_id not in recent_event_ids
                ), *page.events),
                key=lambda stored: stored.cursor,
            )
        )
        activities, position_cursors, _revision_cursors = self._activities(
            stored_events,
            scope,
        )
        complete = [
            activity for activity in activities
            if not isinstance(activity, OperationActivity) or activity.native_name is not None
        ]
        selected = tuple(complete[-activity_limit:])
        cursors = [position_cursors[activity.context.activity_id] for activity in selected]
        return ActivityWindow(
            oldest_cursor=min(cursors, default=through_cursor),
            activities=selected,
            has_more=page.has_more or len(complete) > len(selected),
        )

    def usage(self, session_id: SessionId, through_cursor: int | None = None) -> UsageSummary:
        reports = [
            stored.event
            for stored in self._page(session_id, through_cursor).events
            if isinstance(stored.event.payload, UsageReported)
        ]
        session_tokens = TokenUsage()
        session_cost: Decimal | None = None
        by_actor: dict[ActorId, TokenUsage] = {}
        by_model: dict[str, TokenUsage] = {}
        latest_cumulative: dict[tuple[str, str, str, str], UsageReported] = {}
        additive: list[tuple[CanonicalEvent, UsageReported]] = []
        for event in reports:
            report = event.payload
            model_id = report.model.native_id if report.model else ""
            account_id = report.account.account_id if report.account else ""
            if report.cumulative:
                latest_cumulative[(report.scope, report.subject_id, model_id, account_id)] = report
            else:
                additive.append((event, report))
        for event, report in additive:
            if report.scope == "session":
                session_tokens += report.tokens
                if report.cost_in_usd is not None:
                    session_cost = (session_cost or Decimal(0)) + report.cost_in_usd
            if report.scope == "actor":
                by_actor[event.actor_id] = by_actor.get(event.actor_id, TokenUsage()) + report.tokens
            if report.model:
                by_model[report.model.native_id] = by_model.get(report.model.native_id, TokenUsage()) + report.tokens
        for report in latest_cumulative.values():
            if report.scope == "session":
                session_tokens += report.tokens
                if report.cost_in_usd is not None:
                    session_cost = (session_cost or Decimal(0)) + report.cost_in_usd
            if report.scope == "actor":
                actor_id = ActorId(report.subject_id)
                by_actor[actor_id] = by_actor.get(actor_id, TokenUsage()) + report.tokens
            if report.model:
                by_model[report.model.native_id] = by_model.get(report.model.native_id, TokenUsage()) + report.tokens
        return UsageSummary(
            session_tokens,
            session_cost,
            MappingProxyType(by_actor),
            MappingProxyType(by_model),
        )

    def context(self, session_id: SessionId, through_cursor: int | None = None) -> ContextSummary:
        windows: dict[ActorId, ContextWindow] = {}
        compacting: set[ActorId] = set()
        models: dict[ActorId, ModelReference] = {}
        for stored in self._page(session_id, through_cursor).events:
            event = stored.event
            if isinstance(event.payload, ModelChanged):
                models[event.actor_id] = event.payload.current
            elif isinstance(event.payload, ContextReported):
                windows[event.actor_id] = ContextWindow(
                    event.payload.used_tokens,
                    event.payload.window_tokens,
                    event.payload.model or models.get(event.actor_id),
                )
            elif isinstance(event.payload, CompactionStarted):
                compacting.add(event.actor_id)
            elif isinstance(event.payload, CompactionFinished):
                compacting.discard(event.actor_id)
        return ContextSummary(
            MappingProxyType(windows),
            tuple(sorted(compacting, key=str)),
        )

    def attention(self, session_id: SessionId, through_cursor: int | None = None) -> AttentionState:
        pending: dict[tuple[ActorId, str], PendingAttention] = {}
        for stored in self._page(session_id, through_cursor).events:
            event = stored.event
            payload = event.payload
            key = (event.actor_id, str(payload.attention_id)) if isinstance(
                payload,
                (AttentionRequested, AttentionResolved),
            ) else None
            if isinstance(payload, AttentionRequested):
                pending[key] = PendingAttention(event.actor_id, payload)
            elif isinstance(payload, AttentionResolved):
                pending.pop(key, None)
            elif isinstance(
                payload,
                (
                    TurnFinished,
                    TurnAborted,
                    ActorAssignmentFinished,
                    ActorFinished,
                    SessionFinished,
                ),
            ):
                pending = {
                    pending_key: attention
                    for pending_key, attention in pending.items()
                    if attention.actor_id != event.actor_id
                }
        return AttentionState(tuple(pending.values()))

    def tasks(self, session_id: SessionId, through_cursor: int | None = None) -> tuple[TaskSummary, ...]:
        tasks: dict[TaskId, TaskSummary] = {}
        task_lists: dict[str, set[TaskId]] = {}
        for stored in self._page(session_id, through_cursor).events:
            payload = stored.event.payload
            if isinstance(payload, TaskListChanged):
                previous_ids = task_lists.get(payload.list_id, set())
                current_ids = set(payload.task_ids)
                task_lists[payload.list_id] = current_ids
                retained_ids = set().union(*task_lists.values()) if task_lists else set()
                for task_id in previous_ids - current_ids:
                    if task_id not in retained_ids:
                        tasks.pop(task_id, None)
            elif isinstance(payload, TaskChanged) and payload.state == "deleted":
                tasks.pop(payload.task_id, None)
                for task_ids in task_lists.values():
                    task_ids.discard(payload.task_id)
            elif isinstance(payload, TaskChanged):
                tasks[payload.task_id] = TaskSummary(
                    payload.task_id,
                    payload.label,
                    payload.subject,
                    payload.description,
                    payload.state,
                    payload.owner_actor_id,
                )
        return tuple(tasks[task_id] for task_id in sorted(tasks, key=str))

    def goal(self, session_id: SessionId, through_cursor: int | None = None) -> GoalState | None:
        session = self.session_registry.find_by_id(session_id)
        if session is None:
            return None
        goal = None
        for stored in self._page(session_id, through_cursor).events:
            if stored.event.actor_id != session.lead_actor_id:
                continue
            payload = stored.event.payload
            if not isinstance(payload, GoalChanged):
                continue
            if payload.state == "cleared" or payload.objective is None:
                goal = None
            else:
                goal = GoalState(payload.objective, payload.state, payload.reason)
        return goal

    def background_work(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        through_cursor: int | None = None,
    ) -> BackgroundWorkSummary:
        activities, _position_cursors, _revision_cursors = self._activities(
            self._page(session_id, through_cursor).events,
            scope,
        )
        operations = [activity for activity in activities if isinstance(activity, OperationActivity)]
        return BackgroundWorkSummary(
            running_operation_ids=tuple(
                activity.operation_id
                for activity in operations
                if activity.state == "running" and activity.execution in ("background", "monitor")
            ),
            monitor_count=sum(activity.execution == "monitor" for activity in operations),
            background_job_count=sum(activity.execution == "background" for activity in operations),
        )

    def background_operations(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        through_cursor: int | None = None,
    ) -> tuple[OperationActivity, ...]:
        activities, _position_cursors, _revision_cursors = self._activities(
            self._page(session_id, through_cursor).events,
            scope,
        )
        return tuple(
            activity
            for activity in activities
            if isinstance(activity, OperationActivity)
            and activity.execution in ("background", "monitor")
        )

    def statistics(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        through_cursor: int | None = None,
    ) -> ActivityStatistics:
        activities, _position_cursors, _revision_cursors = self._activities(
            self._page(session_id, through_cursor).events,
            scope,
        )
        shell_commands = [
            activity
            for activity in activities
            if isinstance(activity, OperationActivity) and activity.category == "shell"
        ]
        files = [activity for activity in activities if isinstance(activity, FileActivity)]
        operation_counts: dict[str, int] = {}
        for activity in activities:
            if isinstance(activity, OperationActivity) and activity.category != "shell":
                operation_name = activity.native_name or activity.category or "operation"
                operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
            elif isinstance(activity, FileActivity):
                operation_name = {
                    "read": "Read",
                    "created": "Write",
                    "updated": "Edit",
                    "deleted": "Delete",
                    "renamed": "Move",
                }[activity.file.action]
                operation_counts[operation_name] = operation_counts.get(operation_name, 0) + 1
        return ActivityStatistics(
            shell_command_count=len(shell_commands),
            failed_shell_command_count=sum(
                activity.outcome == "failed" for activity in shell_commands
            ),
            file_count=len({activity.file.path for activity in files}),
            lines_added=sum(activity.file.lines_added or 0 for activity in files),
            lines_removed=sum(activity.file.lines_removed or 0 for activity in files),
            actor_message_count=sum(isinstance(activity, ActorMessageActivity) for activity in activities),
            operation_counts=MappingProxyType(operation_counts),
        )

    def active_seconds(
        self,
        session_id: SessionId,
        current_time: float,
        through_cursor: int | None = None,
    ) -> float:
        active_since = None
        active_seconds = 0.0
        lead_actor_id = None
        for stored in self._page(session_id, through_cursor).events:
            event = stored.event
            event_time = event.occurred_at if event.occurred_at is not None else stored.accepted_at
            payload = event.payload
            if isinstance(payload, SessionStarted):
                lead_actor_id = event.actor_id
                active_since = event_time
            elif event.actor_id == lead_actor_id and isinstance(payload, MessageCreated):
                if payload.role == "user" and payload.phase == "prompt" and active_since is None:
                    active_since = event_time
            elif event.actor_id == lead_actor_id and isinstance(payload, (TurnFinished, TurnAborted)):
                if active_since is not None:
                    active_seconds += max(0.0, event_time - active_since)
                    active_since = None
            elif isinstance(payload, SessionFinished) and active_since is not None:
                active_seconds += max(0.0, event_time - active_since)
                active_since = None
        if active_since is not None:
            active_seconds += max(0.0, current_time - active_since)
        return active_seconds

    def operation_activity(
        self,
        session_id: SessionId,
        actor_id: ActorId,
        operation_id: OperationId,
        through_cursor: int,
    ) -> OperationActivity:
        activities, _positions, _revisions = self._activities(
            self._page(session_id, through_cursor).events,
            ActivityScope(actor_id=actor_id),
        )
        for activity in activities:
            if isinstance(activity, OperationActivity) and activity.operation_id == operation_id:
                return activity
        raise KeyError(str(operation_id))

    def tab_state(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> TabState | None:
        return self._tab_state(self._page(session_id, through_cursor).events, None)

    def tab_state_tail(
        self,
        session_id: SessionId,
        event_limit: int,
        through_cursor: int,
    ) -> TabState | None:
        events = self._tail_page(session_id, event_limit, through_cursor).events
        return self._tab_state(events, "idle")

    @staticmethod
    def _tab_state(
        stored_events: tuple[StoredCanonicalEvent, ...],
        initial_state: TabState | None,
    ) -> TabState | None:
        state = initial_state
        background_operations: set[OperationId] = set()
        pending_attention: set[tuple[ActorId, AttentionId]] = set()
        for stored in stored_events:
            event = stored.event
            payload = event.payload
            if isinstance(payload, SessionStarted):
                state = "idle"
            elif isinstance(payload, SessionFinished):
                state = None
            elif isinstance(payload, TurnStarted):
                state = "thinking"
            elif (
                isinstance(payload, MessageCreated)
                and payload.role == "user"
                and payload.phase == "prompt"
            ):
                state = "thinking"
            elif isinstance(payload, ReasoningCreated):
                state = "working"
            elif isinstance(payload, OperationStarted):
                if payload.execution in ("background", "monitor"):
                    background_operations.add(payload.operation_id)
                if payload.category == "attention":
                    state = "awaiting_attention"
                elif payload.category in ("shell", "task") or payload.execution in (
                    "background",
                    "monitor",
                ):
                    state = "executing"
                else:
                    state = "working"
            elif isinstance(payload, OperationFinished):
                background_operations.discard(payload.operation_id)
                state = "awaiting_attention" if pending_attention else "working"
            elif isinstance(payload, AttentionRequested):
                pending_attention.add((event.actor_id, payload.attention_id))
                state = "awaiting_attention"
            elif isinstance(payload, AttentionResolved):
                pending_attention.discard((event.actor_id, payload.attention_id))
                state = "working"
            elif isinstance(payload, CompactionStarted):
                state = "working"
            elif isinstance(payload, (TurnFinished, TurnAborted)):
                state = (
                    "awaiting_background"
                    if background_operations
                    else "awaiting_response"
                )
        return state

    @staticmethod
    def _activities(
        stored_events: tuple[StoredCanonicalEvent, ...],
        scope: ActivityScope,
    ) -> tuple[list[Activity], dict[str, int], dict[str, int]]:
        activities: dict[str, Activity] = {}
        order: list[str] = []
        position_cursors: dict[str, int] = {}
        revision_cursors: dict[str, int] = {}
        file_operation_ids: set[str] = set()
        hidden_operation_ids: set[str] = set()
        file_activity_ids_by_operation: dict[str, list[str]] = {}
        actor_names: dict[ActorId, str] = {}
        attention_requests: dict[tuple[ActorId, AttentionId], AttentionRequested] = {}
        actor_assignment_starts: dict[tuple[ActorId, AssignmentId], ActorAssignmentActivity] = {}
        for stored in stored_events:
            event = stored.event
            assignment_for_parent = (
                isinstance(event.payload, (ActorAssignmentStarted, ActorAssignmentFinished))
                and event.parent_actor_id is not None
            )
            activity_actor_id = (
                event.parent_actor_id if assignment_for_parent else event.actor_id
            )
            if isinstance(event.payload, ActorStarted):
                actor_names[event.actor_id] = event.payload.name
            elif isinstance(event.payload, ActorNameChanged):
                actor_names[event.actor_id] = event.payload.name
            if scope.actor_id is not None and activity_actor_id != scope.actor_id:
                continue
            payload = event.payload
            actor_name = actor_names.get(activity_actor_id)
            activity: Activity | None = None
            if isinstance(payload, MessageCreated):
                activity_id = _activity_id(event, "message", payload.message_id)
                activity = MessageActivity(
                    _context(stored, activity_id, actor_name),
                    payload.message_id,
                    payload.role,
                    payload.phase,
                    payload.reply_to,
                    payload.content,
                )
            elif isinstance(payload, ReasoningCreated):
                activity_id = _activity_id(event, "reasoning", payload.reasoning_id)
                activity = ReasoningActivity(
                    _context(stored, activity_id, actor_name),
                    payload.reasoning_id,
                    payload.content,
                    payload.summary,
                )
            elif isinstance(payload, OperationStarted):
                activity_id = _activity_id(event, "operation", payload.operation_id)
                if payload.category in ("file_read", "file_write", "file_edit"):
                    file_operation_ids.add(activity_id)
                    hidden_operation_ids.add(activity_id)
                    continue
                if payload.category in ("attention", "task", "message"):
                    hidden_operation_ids.add(activity_id)
                    continue
                activity = OperationActivity(
                    _context(stored, activity_id, actor_name),
                    payload.operation_id,
                    payload.category,
                    payload.native_name,
                    payload.execution,
                    payload.arguments,
                    payload.description,
                    payload.parent_operation_id,
                    (),
                    "running",
                    None,
                    None,
                    None,
                    content_event_id=(event.event_id if payload.arguments is not None else None),
                    content_field=("operation_content" if payload.arguments is not None else None),
                )
            elif isinstance(payload, OperationProgressed):
                activity_id = _activity_id(event, "operation", payload.operation_id)
                if activity_id in file_operation_ids:
                    for file_activity_id in file_activity_ids_by_operation.get(activity_id, ()):
                        file_activity = activities[file_activity_id]
                        if isinstance(file_activity, FileActivity):
                            activities[file_activity_id] = replace(
                                file_activity,
                                context=replace(
                                    file_activity.context,
                                    source_event_ids=file_activity.context.source_event_ids + (event.event_id,),
                                ),
                                progress=file_activity.progress + (payload,),
                                content_event_id=(
                                    file_activity.content_event_id or event.event_id
                                ),
                                content_field=(
                                    file_activity.content_field or "content"
                                ),
                            )
                            revision_cursors[file_activity_id] = stored.cursor
                    continue
                if activity_id in hidden_operation_ids:
                    continue
                existing = activities.get(activity_id)
                if isinstance(existing, OperationActivity):
                    activity = replace(
                        existing,
                        context=replace(
                            existing.context,
                            source_event_ids=existing.context.source_event_ids + (event.event_id,),
                        ),
                        progress=existing.progress + (payload,),
                        content_event_id=event.event_id,
                        content_field="operation_content",
                    )
                else:
                    activity = OperationActivity(
                        _context(stored, activity_id, actor_name),
                        payload.operation_id,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        (payload,),
                        "running",
                        None,
                        None,
                        None,
                        content_event_id=event.event_id,
                        content_field="operation_content",
                    )
            elif isinstance(payload, OperationFinished):
                activity_id = _activity_id(event, "operation", payload.operation_id)
                if activity_id in file_operation_ids:
                    for file_activity_id in file_activity_ids_by_operation.get(activity_id, ()):
                        file_activity = activities[file_activity_id]
                        if isinstance(file_activity, FileActivity):
                            activities[file_activity_id] = replace(
                                file_activity,
                                context=replace(
                                    file_activity.context,
                                    source_event_ids=file_activity.context.source_event_ids + (event.event_id,),
                                    finished_at=(
                                        event.occurred_at
                                        if event.occurred_at is not None
                                        else stored.accepted_at
                                    ),
                                ),
                                outcome=payload.outcome,
                                result=payload.result,
                                content_event_id=(
                                    event.event_id
                                    if payload.result is not None
                                    else file_activity.content_event_id
                                ),
                                content_field=(
                                    "result"
                                    if payload.result is not None
                                    else file_activity.content_field
                                ),
                            )
                            revision_cursors[file_activity_id] = stored.cursor
                    continue
                if activity_id in hidden_operation_ids:
                    continue
                existing = activities.get(activity_id)
                if isinstance(existing, OperationActivity):
                    activity = replace(
                        existing,
                        context=replace(
                            existing.context,
                            source_event_ids=existing.context.source_event_ids + (event.event_id,),
                            finished_at=(
                                event.occurred_at
                                if event.occurred_at is not None
                                else stored.accepted_at
                            ),
                        ),
                        state="finished",
                        outcome=payload.outcome,
                        result=payload.result,
                        exit_code=payload.exit_code,
                        content_event_id=(
                            event.event_id
                            if payload.result is not None
                            else existing.content_event_id
                        ),
                        content_field=(
                            "operation_content"
                            if payload.result is not None
                            else existing.content_field
                        ),
                    )
                else:
                    activity = OperationActivity(
                        _context(stored, activity_id, actor_name, finished=True),
                        payload.operation_id,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        (),
                        "finished",
                        payload.outcome,
                        payload.result,
                        payload.exit_code,
                        content_event_id=(event.event_id if payload.result is not None else None),
                        content_field=("operation_content" if payload.result is not None else None),
                    )
            elif isinstance(payload, FileAccessed):
                activity_id = (
                    _activity_id(event, "file", f"{payload.operation_id}:{payload.path}")
                    if payload.operation_id is not None
                    else f"file:{event.event_id}"
                )
                if payload.unified_diff is not None:
                    content_field = "unified_diff"
                elif payload.content is not None:
                    content_field = "content"
                else:
                    content_field = None
                existing = activities.get(activity_id)
                context = _context(stored, activity_id, actor_name)
                if isinstance(existing, FileActivity):
                    context = replace(
                        existing.context,
                        source_event_ids=existing.context.source_event_ids + (event.event_id,),
                    )
                activity = FileActivity(
                    context,
                    payload,
                    existing.progress if isinstance(existing, FileActivity) else (),
                    existing.outcome if isinstance(existing, FileActivity) else None,
                    existing.result if isinstance(existing, FileActivity) else None,
                    event.event_id if content_field is not None else None,
                    content_field,
                )
                if payload.operation_id is not None:
                    operation_activity_id = _activity_id(event, "operation", payload.operation_id)
                    file_activity_ids_by_operation.setdefault(operation_activity_id, []).append(activity_id)
            elif isinstance(payload, AttentionRequested):
                attention_requests[(event.actor_id, payload.attention_id)] = payload
                activity_id = f"attention-request:{event.event_id}"
                activity = AttentionActivity(
                    _context(stored, activity_id, actor_name),
                    payload.attention_id,
                    payload.attention_type,
                    payload.prompts,
                    "requested",
                    None,
                    (),
                    None,
                    False,
                    None,
                )
            elif isinstance(payload, AttentionResolved):
                request = attention_requests.pop((event.actor_id, payload.attention_id), None)
                activity_id = f"attention-resolution:{event.event_id}"
                activity = AttentionActivity(
                    _context(stored, activity_id, actor_name, finished=True),
                    payload.attention_id,
                    request.attention_type if request is not None else None,
                    request.prompts if request is not None else (),
                    "resolved",
                    payload.decision,
                    payload.answers,
                    payload.feedback,
                    payload.edited,
                    payload.outcome,
                )
            elif isinstance(payload, TaskChanged):
                activity_id = f"task:{event.event_id}"
                activity = TaskActivity(_context(stored, activity_id, actor_name), payload)
            elif isinstance(payload, CompactionFinished):
                activity_id = f"compaction:{event.event_id}"
                activity = CompactionActivity(
                    _context(stored, activity_id, actor_name, finished=True),
                    payload.before_tokens,
                    payload.after_tokens,
                )
            elif isinstance(payload, ActorAssignmentStarted):
                activity_id = f"actor_assignment-start:{event.event_id}"
                activity = ActorAssignmentActivity(
                    _context(stored, activity_id, actor_name),
                    payload.assignment_id,
                    payload.brief,
                    "running",
                    None,
                    None,
                    None,
                    assigned_actor_name=payload.actor_name,
                    prompt=payload.prompt,
                )
                actor_assignment_starts[(activity_actor_id, payload.assignment_id)] = activity
            elif isinstance(payload, ActorAssignmentFinished):
                activity_id = f"actor_assignment-finish:{event.event_id}"
                started = actor_assignment_starts.get((activity_actor_id, payload.assignment_id))
                context = _context(stored, activity_id, actor_name, finished=True)
                if assignment_for_parent:
                    context = replace(
                        context,
                        actor_id=activity_actor_id,
                        parent_actor_id=None,
                    )
                if started is not None:
                    context = replace(
                        context,
                        turn_id=started.context.turn_id,
                        started_at=started.context.started_at,
                    )
                activity = ActorAssignmentActivity(
                    context,
                    payload.assignment_id,
                    started.brief if started is not None else None,
                    "finished",
                    payload.outcome,
                    payload.result,
                    payload.reason,
                    assigned_actor_name=(
                        started.assigned_actor_name if started is not None else None
                    ),
                    prompt=started.prompt if started is not None else None,
                )
            elif isinstance(payload, ActorMessageSent):
                activity_id = _activity_id(event, "actor_message", payload.message_id)
                activity = ActorMessageActivity(
                    _context(stored, activity_id, actor_name),
                    payload.message_id,
                    payload.recipient_actor_id,
                    payload.content,
                )
            if activity is None:
                continue
            activity_id = activity.context.activity_id
            if activity_id not in activities:
                order.append(activity_id)
                position_cursors[activity_id] = stored.cursor
            activities[activity_id] = activity
            revision_cursors[activity_id] = stored.cursor
        return (
            [activities[activity_id] for activity_id in order],
            position_cursors,
            revision_cursors,
        )
