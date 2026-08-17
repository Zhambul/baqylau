"""Cross-session application insights from canonical and operational state."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from repository.contract.diagnostics import DiagnosticReadRepository
from core.repository import RepositoryQueries
from harness.models import TerminalSessionState
from domain.ids import SessionId
from domain.values import TokenUsage
from repository.contract.facts import CanonicalEventRepository
from engine.projections import SessionQueries


class TerminalSessionReader(Protocol):
    def state(self, session_id: SessionId) -> TerminalSessionState: ...


@dataclass(frozen=True)
class DailySessionCount:
    date: str
    session_count: int


@dataclass(frozen=True)
class HourlySessionCount:
    day_of_week: int
    hour: int
    session_count: int


@dataclass(frozen=True)
class InsightProjectSummary:
    working_directory: str
    name: str
    session_count: int


@dataclass(frozen=True)
class InsightWindow:
    session_count: int
    active_session_count: int
    finished_session_count: int
    token_count: int
    cost_in_usd: float
    error_count: int
    projects: tuple[InsightProjectSummary, ...]


@dataclass(frozen=True)
class ProjectInsights:
    working_directory: str
    name: str
    session_count: int
    token_count: int
    cost_in_usd: float
    error_count: int
    last_session_at: float
    daily_sessions: tuple[DailySessionCount, ...]


@dataclass(frozen=True)
class ApplicationInsights:
    generated_at: float
    total_session_count: int
    daily_sessions: tuple[DailySessionCount, ...]
    hourly_sessions: tuple[HourlySessionCount, ...]
    last_seven_days: InsightWindow
    last_thirty_days: InsightWindow
    all_time: InsightWindow
    projects: tuple[ProjectInsights, ...]


@dataclass(frozen=True)
class _SessionInsight:
    session_id: SessionId
    working_directory: str
    started_at: float
    finished: bool
    active: bool
    token_count: int
    cost_in_usd: float
    error_count: int


class ApplicationInsightsService:
    def __init__(
        self,
        canonical_events: CanonicalEventRepository,
        sessions: SessionQueries,
        terminal: TerminalSessionReader,
        diagnostics: DiagnosticReadRepository,
        repositories: RepositoryQueries,
        top_project_count: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.canonical_events = canonical_events
        self.sessions = sessions
        self.terminal = terminal
        self.diagnostics = diagnostics
        self.repositories = repositories
        self.top_project_count = top_project_count
        self.clock = clock

    def snapshot(self) -> ApplicationInsights:
        generated_at = self.clock()
        cursor = self.canonical_events.latest_cursor()
        error_counts = self.diagnostics.error_counts()
        rows = []
        for summary in self.sessions.sessions(cursor):
            usage = self.sessions.usage(summary.session_id, cursor)
            rows.append(
                _SessionInsight(
                    session_id=summary.session_id,
                    working_directory=self.repositories.project_directory(
                        summary.initial_working_directory
                    ),
                    started_at=summary.started_at,
                    finished=summary.state == "finished",
                    active=self.terminal.state(summary.session_id).window_id is not None,
                    token_count=_token_count(usage.tokens),
                    cost_in_usd=float(usage.cost_in_usd or 0),
                    error_count=error_counts.get(summary.session_id, 0),
                )
            )

        daily_counts: dict[str, int] = {}
        hourly_counts: dict[tuple[int, int], int] = {}
        for row in rows:
            started = datetime.fromtimestamp(row.started_at)
            date = started.strftime("%Y-%m-%d")
            daily_counts[date] = daily_counts.get(date, 0) + 1
            day_and_hour = (int(started.strftime("%w")), started.hour)
            hourly_counts[day_and_hour] = hourly_counts.get(day_and_hour, 0) + 1

        return ApplicationInsights(
            generated_at=generated_at,
            total_session_count=len(rows),
            daily_sessions=tuple(
                DailySessionCount(date, count)
                for date, count in sorted(daily_counts.items())
            ),
            hourly_sessions=tuple(
                HourlySessionCount(day, hour, count)
                for (day, hour), count in sorted(hourly_counts.items())
            ),
            last_seven_days=self._window(rows, generated_at - 7 * 86400),
            last_thirty_days=self._window(rows, generated_at - 30 * 86400),
            all_time=self._window(rows, None),
            projects=self._projects(rows),
        )

    def _window(
        self,
        rows: list[_SessionInsight],
        started_after: float | None,
    ) -> InsightWindow:
        selected = [
            row
            for row in rows
            if started_after is None or row.started_at >= started_after
        ]
        project_counts: dict[str, int] = {}
        for row in selected:
            if row.working_directory:
                project_counts[row.working_directory] = (
                    project_counts.get(row.working_directory, 0) + 1
                )
        top_projects = sorted(
            project_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[: self.top_project_count]
        return InsightWindow(
            session_count=len(selected),
            active_session_count=sum(row.active for row in selected),
            finished_session_count=sum(row.finished for row in selected),
            token_count=sum(row.token_count for row in selected),
            cost_in_usd=sum(row.cost_in_usd for row in selected),
            error_count=sum(row.error_count for row in selected),
            projects=tuple(
                InsightProjectSummary(
                    working_directory=directory,
                    name=os.path.basename(directory) or directory,
                    session_count=count,
                )
                for directory, count in top_projects
            ),
        )

    @staticmethod
    def _projects(rows: list[_SessionInsight]) -> tuple[ProjectInsights, ...]:
        grouped: dict[str, list[_SessionInsight]] = {}
        for row in rows:
            if row.working_directory:
                grouped.setdefault(row.working_directory, []).append(row)
        projects = []
        for directory, project_rows in grouped.items():
            daily_counts: dict[str, int] = {}
            for row in project_rows:
                date = datetime.fromtimestamp(row.started_at).strftime("%Y-%m-%d")
                daily_counts[date] = daily_counts.get(date, 0) + 1
            projects.append(
                ProjectInsights(
                    working_directory=directory,
                    name=os.path.basename(directory) or directory,
                    session_count=len(project_rows),
                    token_count=sum(row.token_count for row in project_rows),
                    cost_in_usd=sum(row.cost_in_usd for row in project_rows),
                    error_count=sum(row.error_count for row in project_rows),
                    last_session_at=max(row.started_at for row in project_rows),
                    daily_sessions=tuple(
                        DailySessionCount(date, count)
                        for date, count in sorted(daily_counts.items())
                    ),
                )
            )
        return tuple(
            sorted(
                projects,
                key=lambda project: (-project.session_count, project.name),
            )
        )


def _token_count(tokens: TokenUsage) -> int:
    return (
        tokens.input_tokens
        + tokens.output_tokens
        + tokens.cache_read_tokens
        + tokens.cache_write_tokens
        + tokens.one_hour_cache_write_tokens
    )
