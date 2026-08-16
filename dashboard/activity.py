"""Canonical dashboard pages, content resolution, and one-cursor SSE frames."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Protocol

from harness.models import TerminalSessionState
from core.repository import RepositoryQueries, RepositoryStatus
from dashboard.markdown import md_html
from dashboard.ansi import ansi_html, escape_html
from dashboard.highlight import source_ansi
from dashboard.presenter import DashboardItem, DashboardPresenter
from domain.ids import ActorId, AttentionId, OperationId, SessionId
from domain.values import Content, StructuredContent, TextContent
from engine.store.canonical import CanonicalEventStore
from engine.projections import (
    ActorSummary,
    ActivityStatistics,
    ActivityScope,
    AttentionState,
    ContextSummary,
    GoalState,
    OperationActivity,
    SessionQueries,
    SessionSummary,
    TaskSummary,
    TabState,
    UsageSummary,
)

SESSION_REFRESH_SECONDS = 0.25
COLD_SESSION_COUNT = 20


def _content_text(content: Content | None) -> str:
    if content is None:
        return ""
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, StructuredContent):
        return json.dumps(json.loads(content.json_text), ensure_ascii=False, indent=2, sort_keys=True)
    raise TypeError(f"unsupported content type: {type(content).__name__}")


def to_wire(value):
    if is_dataclass(value):
        return {field.name: to_wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_wire(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


@dataclass(frozen=True)
class DashboardActivityPage:
    oldest_cursor: int
    latest_cursor: int | None
    has_more: bool
    items: tuple[DashboardItem, ...]


@dataclass(frozen=True)
class DashboardAttentionOption:
    value: str
    label: str
    description: str | None


@dataclass(frozen=True)
class DashboardQuestion:
    question_id: str
    title: str | None
    text: str
    multiple: bool
    options: tuple[DashboardAttentionOption, ...]


@dataclass(frozen=True)
class DashboardPendingAttention:
    actor_id: ActorId
    attention_id: AttentionId
    attention_type: str
    questions: tuple[DashboardQuestion, ...]
    plan_html: str | None


@dataclass(frozen=True)
class DashboardAttentionState:
    pending: tuple[DashboardPendingAttention, ...]


@dataclass(frozen=True)
class DashboardMonitorEvent:
    event: str
    status: str | None
    summary: str | None
    timestamp: float | None


@dataclass(frozen=True)
class DashboardBackgroundOperation:
    task: str
    actor_id: ActorId
    command: str
    command_html: str
    description: str | None
    live: bool
    started_at: float | None
    ended_at: float | None
    end_reason: str | None
    output: str
    line_count: int
    events: tuple[DashboardMonitorEvent, ...]


@dataclass(frozen=True)
class DashboardBackgroundWork:
    running_operation_ids: tuple[OperationId, ...]
    monitor_count: int
    background_job_count: int
    monitors: tuple[DashboardBackgroundOperation, ...]
    jobs: tuple[DashboardBackgroundOperation, ...]


def _dashboard_attention(state: AttentionState) -> DashboardAttentionState:
    pending = []
    for item in state.pending:
        request = item.request
        questions = tuple(
            DashboardQuestion(
                question_id=prompt.prompt_id,
                title=prompt.title,
                text=prompt.prompt,
                multiple=prompt.multiple,
                options=tuple(
                    DashboardAttentionOption(choice.value, choice.label, choice.description)
                    for choice in prompt.choices
                ),
            )
            for prompt in request.prompts
        )
        plan_text = "\n\n".join(prompt.prompt for prompt in request.prompts if prompt.prompt)
        pending.append(
            DashboardPendingAttention(
                actor_id=item.actor_id,
                attention_id=request.attention_id,
                attention_type=request.attention_type,
                questions=questions,
                plan_html=md_html(plan_text) if request.attention_type == "plan" else None,
            )
        )
    return DashboardAttentionState(tuple(pending))


@dataclass(frozen=True)
class DashboardSessionSnapshot:
    cursor: int
    session: SessionSummary | None
    tab_state: TabState | None
    actors: tuple[ActorSummary, ...]
    usage: UsageSummary
    context: ContextSummary
    attention: DashboardAttentionState
    tasks: tuple[TaskSummary, ...]
    goal: GoalState | None
    background_work: DashboardBackgroundWork
    statistics: ActivityStatistics


@dataclass(frozen=True)
class DashboardSessionListItem:
    session: SessionSummary
    terminal: TerminalSessionState
    project_directory: str
    tab_state: TabState | None
    statistics: ActivityStatistics
    usage: UsageSummary
    context: ContextSummary
    repository: RepositoryStatus | None


@dataclass(frozen=True)
class CanonicalSessionListItem:
    cursor: int
    summary: SessionSummary
    tab_state: TabState | None
    statistics: ActivityStatistics
    usage: UsageSummary
    context: ContextSummary


class TerminalSessionReader(Protocol):
    def state(self, session_id: SessionId) -> TerminalSessionState: ...


class DashboardSessionService:
    def __init__(
        self,
        canonical_store: CanonicalEventStore,
        queries: SessionQueries,
        terminal: TerminalSessionReader,
        repositories: RepositoryQueries,
    ) -> None:
        self.canonical_store = canonical_store
        self.queries = queries
        self.terminal = terminal
        self.repositories = repositories
        self._sessions_lock = threading.Lock()
        self._sessions_refresh_lock = threading.Lock()
        self._sessions_at = 0.0
        self._sessions_cache: tuple[DashboardSessionListItem, ...] | None = None
        self._sessions_refreshing = False
        self._canonical_sessions: dict[SessionId, CanonicalSessionListItem] = {}

    def sessions(self) -> tuple[DashboardSessionListItem, ...]:
        with self._sessions_lock:
            cached = self._sessions_cache
            if cached is not None:
                if (
                    time.monotonic() - self._sessions_at >= SESSION_REFRESH_SECONDS
                    and not self._sessions_refreshing
                ):
                    self._sessions_refreshing = True
                    threading.Thread(
                        target=self._refresh_sessions,
                        daemon=True,
                        name="baqylau-session-list-refresh",
                    ).start()
                return cached
        with self._sessions_refresh_lock:
            with self._sessions_lock:
                if self._sessions_cache is not None:
                    return self._sessions_cache
            session_ids = self.canonical_store.session_ids()
            cold_session_ids = session_ids[:COLD_SESSION_COUNT]
            sessions = self._build_sessions(cold_session_ids)
            with self._sessions_lock:
                self._sessions_cache = sessions
                self._sessions_at = time.monotonic()
            return sessions

    def _refresh_sessions(self) -> None:
        try:
            with self._sessions_refresh_lock:
                sessions = self._build_sessions(
                    self.canonical_store.session_ids()[:COLD_SESSION_COUNT]
                )
            with self._sessions_lock:
                self._sessions_cache = sessions
                self._sessions_at = time.monotonic()
        finally:
            with self._sessions_lock:
                self._sessions_refreshing = False

    def _build_sessions(
        self,
        session_ids: tuple[SessionId, ...] | None = None,
    ) -> tuple[DashboardSessionListItem, ...]:
        cursor = self.canonical_store.latest_cursor()
        if cursor is None:
            return ()
        repository_statuses: dict[str, RepositoryStatus | None] = {}

        def repository_status(working_directory: str) -> RepositoryStatus | None:
            if working_directory not in repository_statuses:
                repository_statuses[working_directory] = self.repositories.status(
                    working_directory
                )
            return repository_statuses[working_directory]

        canonical_sessions = []
        selected_session_ids = (
            self.canonical_store.session_ids() if session_ids is None else session_ids
        )
        for session_id in selected_session_ids:
            session_cursor = self.canonical_store.latest_session_cursor(session_id, cursor)
            if session_cursor is None:
                continue
            canonical = self._canonical_sessions.get(session_id)
            if canonical is None or canonical.cursor != session_cursor:
                summary = self.queries.summary(session_id, cursor)
                if summary is None:
                    continue
                canonical = CanonicalSessionListItem(
                    session_cursor,
                    summary,
                    self.queries.tab_state(session_id, cursor),
                    self.queries.statistics(session_id, ActivityScope(), cursor),
                    self.queries.usage(session_id, cursor),
                    self.queries.context(session_id, cursor),
                )
                self._canonical_sessions[session_id] = canonical
            canonical_sessions.append(canonical)
        canonical_sessions.sort(
            key=lambda item: item.summary.started_at,
            reverse=True,
        )

        return tuple(
            DashboardSessionListItem(
                canonical.summary,
                self.terminal.state(canonical.summary.session_id),
                self.repositories.project_directory(
                    canonical.summary.initial_working_directory
                ),
                canonical.tab_state,
                canonical.statistics,
                canonical.usage,
                canonical.context,
                repository_status(canonical.summary.working_directory),
            )
            for canonical in canonical_sessions
        )

    def snapshot(
        self,
        session_id: SessionId,
        scope: ActivityScope,
    ) -> DashboardSessionSnapshot:
        cursor = self.canonical_store.through(session_id).latest_cursor or 0
        return self.snapshot_at(session_id, scope, cursor)

    def snapshot_at(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        cursor: int,
    ) -> DashboardSessionSnapshot:
        return DashboardSessionSnapshot(
            cursor=cursor,
            session=self.queries.summary(session_id, cursor),
            tab_state=self.queries.tab_state(session_id, cursor),
            actors=self.queries.actors(session_id, cursor),
            usage=self.queries.usage(session_id, cursor),
            context=self.queries.context(session_id, cursor),
            attention=_dashboard_attention(self.queries.attention(session_id, cursor)),
            tasks=self.queries.tasks(session_id, cursor),
            goal=self.queries.goal(session_id, cursor),
            background_work=self._background_work(session_id, scope, cursor),
            statistics=self.queries.statistics(session_id, scope, cursor),
        )

    def _background_work(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        cursor: int,
    ) -> DashboardBackgroundWork:
        operations = self.queries.background_operations(session_id, scope, cursor)
        monitors = tuple(
            self._background_operation(operation)
            for operation in operations
            if operation.execution == "monitor"
        )
        jobs = tuple(
            self._background_operation(operation)
            for operation in operations
            if operation.execution == "background"
        )
        return DashboardBackgroundWork(
            running_operation_ids=tuple(
                operation.operation_id for operation in operations if operation.state == "running"
            ),
            monitor_count=len(monitors),
            background_job_count=len(jobs),
            monitors=monitors,
            jobs=jobs,
        )

    @staticmethod
    def _background_operation(operation: OperationActivity) -> DashboardBackgroundOperation:
        command = _content_text(operation.arguments)
        output_values = (
            (operation.result,)
            if operation.result is not None
            else operation.current_progress()
        )
        output = "\n".join(_content_text(value) for value in output_values if value is not None)
        highlighted = source_ansi(command, "bash")
        command_html = (
            f'<pre class="oc">{ansi_html(highlighted)}</pre>'
            if highlighted is not None
            else f'<pre class="oc">{escape_html(command)}</pre>'
        )
        events = tuple(
            DashboardMonitorEvent(
                event=_content_text(progress.content),
                status=(progress.stream if progress.stream == "status" else None),
                summary=None,
                timestamp=None,
            )
            for progress in operation.progress
        )
        return DashboardBackgroundOperation(
            task=str(operation.operation_id),
            actor_id=operation.context.actor_id,
            command=command,
            command_html=command_html,
            description=operation.description,
            live=operation.state == "running",
            started_at=operation.context.started_at,
            ended_at=operation.context.finished_at,
            end_reason=operation.outcome,
            output=output,
            line_count=len(output.splitlines()),
            events=events,
        )


@dataclass(frozen=True)
class DashboardActivityFrame:
    cursor: int
    items: tuple[DashboardItem, ...]
    snapshot: DashboardSessionSnapshot

    def json(self) -> str:
        return json.dumps(to_wire(self), ensure_ascii=False, separators=(",", ":"))

    def sse(self) -> str:
        return f"id: {self.cursor}\nevent: activity\ndata: {self.json()}\n\n"


class DashboardActivityService:
    def __init__(
        self,
        canonical_store: CanonicalEventStore,
        queries: SessionQueries,
        presenter: DashboardPresenter | None = None,
    ) -> None:
        self.canonical_store = canonical_store
        self.queries = queries
        self.presenter = presenter or DashboardPresenter()

    def backlog(
        self,
        session_id: SessionId,
        before_cursor: int | None,
        scope: ActivityScope,
        block_count: int,
    ) -> DashboardActivityPage:
        snapshot_cursor = self.canonical_store.latest_cursor() or 0
        window = self.queries.activity_before(
            session_id,
            before_cursor,
            scope,
            block_count,
            through_cursor=snapshot_cursor,
        )
        return DashboardActivityPage(
            oldest_cursor=window.oldest_cursor,
            latest_cursor=snapshot_cursor,
            has_more=window.has_more,
            items=tuple(self.presenter.present(activity) for activity in window.activities),
        )


class DashboardStreamService:
    def __init__(
        self,
        canonical_store: CanonicalEventStore,
        queries: SessionQueries,
        terminal: TerminalSessionReader,
        repositories: RepositoryQueries,
        presenter: DashboardPresenter | None = None,
    ) -> None:
        self.canonical_store = canonical_store
        self.queries = queries
        self.presenter = presenter or DashboardPresenter()
        self.sessions = DashboardSessionService(
            canonical_store, queries, terminal, repositories
        )

    def frame(
        self,
        session_id: SessionId,
        cursor: int,
        scope: ActivityScope,
        limit: int = 200,
    ) -> DashboardActivityFrame | None:
        examined = self.canonical_store.after(session_id, cursor, limit)
        if not examined.events:
            return None
        frame_cursor = examined.cursor
        activity_page = self.queries.activity_after(
            session_id,
            cursor,
            scope,
            limit,
            through_cursor=frame_cursor,
        )
        changed_event_ids = {
            str(stored.event.event_id)
            for stored in examined.events
        }
        items = tuple(
            self.presenter.present(activity)
            for activity in activity_page.activities
            if changed_event_ids.intersection(map(str, activity.context.source_event_ids))
        )
        return DashboardActivityFrame(
            cursor=frame_cursor,
            items=items,
            snapshot=self.sessions.snapshot_at(session_id, scope, frame_cursor),
        )
