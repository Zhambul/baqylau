"""Insight aggregates to the insights page's models."""

from __future__ import annotations

from api.application.models.insights.application_insights_response import (
    ApplicationInsightsResponse,
    DailySessionCountResponse,
    HourlySessionCountResponse,
    InsightProjectSummaryResponse,
    InsightWindowResponse,
    ProjectInsightsResponse,
)
from app.services.insights import ApplicationInsights, DailySessionCount, InsightWindow


def daily_sessions(counts: tuple[DailySessionCount, ...]) -> tuple[DailySessionCountResponse, ...]:
    return tuple(
        DailySessionCountResponse(date=day.date, session_count=day.session_count)
        for day in counts
    )


def insight_window(window: InsightWindow) -> InsightWindowResponse:
    return InsightWindowResponse(
        session_count=window.session_count,
        active_session_count=window.active_session_count,
        finished_session_count=window.finished_session_count,
        token_count=window.token_count,
        cost_in_usd=window.cost_in_usd,
        error_count=window.error_count,
        projects=tuple(
            InsightProjectSummaryResponse(
                working_directory=project.working_directory,
                name=project.name,
                session_count=project.session_count,
            )
            for project in window.projects
        ),
    )


def application_insights(insights: ApplicationInsights) -> ApplicationInsightsResponse:
    return ApplicationInsightsResponse(
        generated_at=insights.generated_at,
        total_session_count=insights.total_session_count,
        daily_sessions=daily_sessions(insights.daily_sessions),
        hourly_sessions=tuple(
            HourlySessionCountResponse(
                day_of_week=hour.day_of_week,
                hour=hour.hour,
                session_count=hour.session_count,
            )
            for hour in insights.hourly_sessions
        ),
        last_seven_days=insight_window(insights.last_seven_days),
        last_thirty_days=insight_window(insights.last_thirty_days),
        all_time=insight_window(insights.all_time),
        projects=tuple(
            ProjectInsightsResponse(
                working_directory=project.working_directory,
                name=project.name,
                session_count=project.session_count,
                token_count=project.token_count,
                cost_in_usd=project.cost_in_usd,
                error_count=project.error_count,
                last_session_at=project.last_session_at,
                daily_sessions=daily_sessions(project.daily_sessions),
            )
            for project in insights.projects
        ),
    )
