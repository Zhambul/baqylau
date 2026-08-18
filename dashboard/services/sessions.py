"""What a session IS, arranged for the browser: the list, and one snapshot.

The list page and the session page both read from here. The list is refreshed
on a short interval and kept warm, because it folds every session in the store
and a page that polls it must not pay that each time; a snapshot is built for
one session at one cursor, so a client can ask for exactly what it already has
and get back exactly what changed.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol

from core.repository import RepositoryQueries, RepositoryStatus
from dashboard.render.ansi import ansi_html, escape_html
from dashboard.render.highlight import source_ansi
from dashboard.services.models import (
    CanonicalSessionListItem,
    DashboardBackgroundOperation,
    DashboardBackgroundWork,
    DashboardMonitorEvent,
    DashboardSessionListItem,
    DashboardSessionSnapshot,
    attention_state,
)
from domain.ids import SessionId
from domain.values import content_text
from engine.projections import ActivityScope, OperationActivity, SessionQueries
from repository.contract.facts import CanonicalEventRepository
from harness.models import TerminalSessionState

SESSION_REFRESH_SECONDS = 0.25
COLD_SESSION_COUNT = 20


class TerminalSessionReader(Protocol):
    def state(self, session_id: SessionId) -> TerminalSessionState: ...


class DashboardSessionService:
    def __init__(
        self,
        canonical_events: CanonicalEventRepository,
        queries: SessionQueries,
        terminal: TerminalSessionReader,
        repositories: RepositoryQueries,
    ) -> None:
        self.canonical_events = canonical_events
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
            session_ids = self.canonical_events.session_ids()
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
                    self.canonical_events.session_ids()[:COLD_SESSION_COUNT]
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
        cursor = self.canonical_events.latest_cursor()
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
            self.canonical_events.session_ids() if session_ids is None else session_ids
        )
        # One query for every session's newest cursor. This runs on a 250 ms
        # refresh, and asking per session made it twenty round trips a tick.
        session_cursors = self.canonical_events.latest_session_cursors(
            selected_session_ids, cursor
        )
        for session_id in selected_session_ids:
            session_cursor = session_cursors.get(session_id)
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
        cursor = self.canonical_events.latest_cursor() or 0
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
            attention=attention_state(self.queries.attention(session_id, cursor)),
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
        command = content_text(operation.arguments)
        output_values = (
            (operation.result,)
            if operation.result is not None
            else operation.current_progress()
        )
        output = "\n".join(content_text(value) for value in output_values if value is not None)
        highlighted = source_ansi(command, "bash")
        command_html = (
            f'<pre class="oc">{ansi_html(highlighted)}</pre>'
            if highlighted is not None
            else f'<pre class="oc">{escape_html(command)}</pre>'
        )
        events = tuple(
            DashboardMonitorEvent(
                event=content_text(progress.content),
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
