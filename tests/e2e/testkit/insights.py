"""Exact changes between two typed application-insight snapshots."""

from __future__ import annotations

import math
from datetime import date, datetime

from api.application.models.insights.application_insights_response import (
    ApplicationInsightsResponse,
    DailySessionCountResponse,
    HourlySessionCountResponse,
    InsightProjectSummaryResponse,
    InsightWindowResponse,
    ProjectInsightsResponse,
)
from sdk.state import SessionSnapshot


def assert_completed_session_delta(
    before: ApplicationInsightsResponse,
    after: ApplicationInsightsResponse,
    session: SessionSnapshot,
) -> None:
    summary = session.data.session
    if summary.started_at is None:
        raise AssertionError(f"session {summary.session_id!r} has no start time")
    if summary.state != "finished":
        raise AssertionError(
            f"session {summary.session_id!r} has state {summary.state!r}"
        )
    token_count = sum(
        actor.usage.tokens.input_tokens
        + actor.usage.tokens.output_tokens
        + actor.usage.tokens.cache_read_tokens
        + actor.usage.tokens.cache_write_tokens
        + actor.usage.tokens.one_hour_cache_write_tokens
        for actor in session.data.actors
    )
    cost_in_usd = sum(
        float(actor.usage.cost_in_usd or 0) for actor in session.data.actors
    )
    started = datetime.fromtimestamp(summary.started_at)
    working_directory = summary.working_directory

    assert after.generated_at >= before.generated_at
    assert after.total_session_count == before.total_session_count + 1
    assert _daily_count(after.daily_sessions, started.date()) == (
        _daily_count(before.daily_sessions, started.date()) + 1
    )
    assert _hourly_count(after.hourly_sessions, started) == (
        _hourly_count(before.hourly_sessions, started) + 1
    )
    for before_window, after_window in (
        (before.last_seven_days, after.last_seven_days),
        (before.last_thirty_days, after.last_thirty_days),
        (before.all_time, after.all_time),
    ):
        _assert_window_delta(
            before_window,
            after_window,
            working_directory,
            token_count,
            cost_in_usd,
        )

    before_project = _project(before, working_directory)
    after_project = _project(after, working_directory)
    if after_project is None:
        raise AssertionError(f"insights have no project {working_directory!r}")
    assert after_project.session_count == _project_value(before_project, "session_count") + 1
    assert after_project.token_count == _project_value(before_project, "token_count") + token_count
    _assert_float_delta(
        _project_value(before_project, "cost_in_usd"),
        after_project.cost_in_usd,
        cost_in_usd,
    )
    assert after_project.error_count == _project_value(before_project, "error_count")
    assert after_project.last_session_at == summary.started_at
    assert _daily_count(after_project.daily_sessions, started.date()) == (
        _daily_count(
            before_project.daily_sessions if before_project is not None else (),
            started.date(),
        )
        + 1
    )


def _assert_window_delta(
    before: InsightWindowResponse,
    after: InsightWindowResponse,
    working_directory: str,
    token_count: int,
    cost_in_usd: float,
) -> None:
    assert after.session_count == before.session_count + 1
    assert after.active_session_count == before.active_session_count
    assert after.finished_session_count == before.finished_session_count + 1
    assert after.token_count == before.token_count + token_count
    _assert_float_delta(before.cost_in_usd, after.cost_in_usd, cost_in_usd)
    assert after.error_count == before.error_count
    assert _project_summary_count(after.projects, working_directory) == (
        _project_summary_count(before.projects, working_directory) + 1
    )


def _project(
    insights: ApplicationInsightsResponse,
    working_directory: str,
) -> ProjectInsightsResponse | None:
    found = [
        project
        for project in insights.projects
        if project.working_directory == working_directory
    ]
    if len(found) > 1:
        raise AssertionError(
            f"insights have {len(found)} projects for {working_directory!r}"
        )
    return found[0] if found else None


def _project_summary_count(
    projects: tuple[InsightProjectSummaryResponse, ...],
    working_directory: str,
) -> int:
    found = [
        project.session_count
        for project in projects
        if project.working_directory == working_directory
    ]
    if len(found) > 1:
        raise AssertionError(
            f"insight window has {len(found)} projects for {working_directory!r}"
        )
    return found[0] if found else 0


def _daily_count(
    rows: tuple[DailySessionCountResponse, ...],
    day: date,
) -> int:
    found = [row.session_count for row in rows if row.date == day]
    if len(found) > 1:
        raise AssertionError(f"insights have {len(found)} rows for {day}")
    return found[0] if found else 0


def _hourly_count(
    rows: tuple[HourlySessionCountResponse, ...],
    started: datetime,
) -> int:
    day_of_week = int(started.strftime("%w"))
    found = [
        row.session_count
        for row in rows
        if row.day_of_week == day_of_week and row.hour == started.hour
    ]
    if len(found) > 1:
        raise AssertionError(
            f"insights have {len(found)} rows for day {day_of_week}, hour {started.hour}"
        )
    return found[0] if found else 0


def _project_value(
    project: ProjectInsightsResponse | None,
    field: str,
) -> int | float:
    return getattr(project, field) if project is not None else 0


def _assert_float_delta(before: float, after: float, expected_delta: float) -> None:
    if not math.isclose(after - before, expected_delta, rel_tol=1e-9, abs_tol=1e-9):
        raise AssertionError(
            f"insight value changed by {after - before}, expected {expected_delta}"
        )
