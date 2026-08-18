"""Session projections to the session read plane's models.

Where the api layer decides what a session LOOKS like on the wire. Every
identity is carried across as the identity type it is, and the projections'
`Mapping`s become plain dicts — one of them is a MappingProxyType, which is a
fact about how the fold protects itself and not one the browser has any
business hearing about.
"""

from __future__ import annotations

from api.common.mapper import values
from api.dashboard.models.sessions.activity_statistics import ActivityStatisticsResponse
from api.dashboard.models.sessions.actor_summary import ActorSummaryResponse
from api.dashboard.models.sessions.attention_state import (
    AttentionOptionResponse,
    AttentionQuestionResponse,
    AttentionStateResponse,
    PendingAttentionResponse,
)
from api.dashboard.models.sessions.background_work import (
    BackgroundOperationResponse,
    BackgroundWorkResponse,
    MonitorEventResponse,
)
from api.dashboard.models.sessions.canonical_snapshot import CanonicalSnapshotResponse
from api.dashboard.models.sessions.context_summary import (
    ContextSummaryResponse,
    ContextWindowResponse,
)
from api.dashboard.models.sessions.goal_state import GoalStateResponse
from api.dashboard.models.sessions.model_change import ModelChangeResponse
from api.dashboard.models.sessions.session_list_item import SessionListItemResponse
from api.dashboard.models.sessions.session_summary import SessionSummaryResponse
from api.dashboard.models.sessions.task_summary import TaskSummaryResponse
from api.dashboard.models.sessions.usage_summary import UsageSummaryResponse
from api.dashboard.models.sessions.session_snapshot_response import SessionSnapshotResponse
from api.dashboard.mapper import application
from dashboard.services.models import (
    DashboardAttentionState,
    DashboardBackgroundOperation,
    DashboardBackgroundWork,
    DashboardSessionListItem,
    DashboardSessionSnapshot,
)
from dashboard.services.workspace import SessionApplicationSnapshot
from domain.events import ModelChanged
from engine.projections import (
    ActivityStatistics,
    ActorSummary,
    ContextSummary,
    GoalState,
    SessionSummary,
    TaskSummary,
    UsageSummary,
)


def maybe_model_change(change: ModelChanged | None) -> ModelChangeResponse | None:
    if change is None:
        return None
    return ModelChangeResponse(
        previous=values.maybe_model_reference(change.previous),
        current=values.model_reference(change.current),
        reason=change.reason,
    )


def session_summary(summary: SessionSummary) -> SessionSummaryResponse:
    return SessionSummaryResponse(
        session_id=summary.session_id,
        harness=summary.harness,
        title=summary.title,
        working_directory=summary.working_directory,
        initial_working_directory=summary.initial_working_directory,
        started_at=summary.started_at,
        finished_at=summary.finished_at,
        lead_actor_id=summary.lead_actor_id,
        model=values.maybe_model_reference(summary.model),
        effort=summary.effort,
        account=values.maybe_account_reference(summary.account),
        prompt_count=summary.prompt_count,
        automatic_model_change=maybe_model_change(summary.automatic_model_change),
        state=summary.state,
    )


def maybe_session_summary(summary: SessionSummary | None) -> SessionSummaryResponse | None:
    return session_summary(summary) if summary is not None else None


def actor_summary(actor: ActorSummary) -> ActorSummaryResponse:
    return ActorSummaryResponse(
        actor_id=actor.actor_id,
        parent_actor_id=actor.parent_actor_id,
        harness=actor.harness,
        role=actor.role,
        name=actor.name,
        description=actor.description,
        model=values.maybe_model_reference(actor.model),
        effort=actor.effort,
        state=actor.state,
        started_at=actor.started_at,
        finished_at=actor.finished_at,
    )


def usage_summary(usage: UsageSummary) -> UsageSummaryResponse:
    return UsageSummaryResponse(
        tokens=values.token_usage(usage.tokens),
        cost_in_usd=usage.cost_in_usd,
        by_actor={
            actor_id: values.token_usage(tokens)
            for actor_id, tokens in usage.by_actor.items()
        },
        by_model={
            model_id: values.token_usage(tokens)
            for model_id, tokens in usage.by_model.items()
        },
    )


def context_summary(context: ContextSummary) -> ContextSummaryResponse:
    return ContextSummaryResponse(
        by_actor={
            actor_id: ContextWindowResponse(
                used_tokens=window.used_tokens,
                window_tokens=window.window_tokens,
                model=values.maybe_model_reference(window.model),
            )
            for actor_id, window in context.by_actor.items()
        },
        compacting_actor_ids=context.compacting_actor_ids,
    )


def activity_statistics(statistics: ActivityStatistics) -> ActivityStatisticsResponse:
    return ActivityStatisticsResponse(
        shell_command_count=statistics.shell_command_count,
        failed_shell_command_count=statistics.failed_shell_command_count,
        file_count=statistics.file_count,
        lines_added=statistics.lines_added,
        lines_removed=statistics.lines_removed,
        actor_message_count=statistics.actor_message_count,
        operation_counts=dict(statistics.operation_counts),
    )


def task_summary(task: TaskSummary) -> TaskSummaryResponse:
    return TaskSummaryResponse(
        task_id=task.task_id,
        label=task.label,
        subject=task.subject,
        description=task.description,
        state=task.state,
        owner_actor_id=task.owner_actor_id,
    )


def maybe_goal_state(goal: GoalState | None) -> GoalStateResponse | None:
    if goal is None:
        return None
    return GoalStateResponse(objective=goal.objective, state=goal.state, reason=goal.reason)


def attention_state(state: DashboardAttentionState) -> AttentionStateResponse:
    return AttentionStateResponse(
        pending=tuple(
            PendingAttentionResponse(
                actor_id=item.actor_id,
                attention_id=item.attention_id,
                attention_type=item.attention_type,
                questions=tuple(
                    AttentionQuestionResponse(
                        question_id=question.question_id,
                        title=question.title,
                        text=question.text,
                        multiple=question.multiple,
                        options=tuple(
                            AttentionOptionResponse(
                                value=option.value,
                                label=option.label,
                                description=option.description,
                            )
                            for option in question.options
                        ),
                    )
                    for question in item.questions
                ),
                plan_html=item.plan_html,
            )
            for item in state.pending
        )
    )


def background_operation(
    operation: DashboardBackgroundOperation,
) -> BackgroundOperationResponse:
    return BackgroundOperationResponse(
        task=operation.task,
        actor_id=operation.actor_id,
        command=operation.command,
        command_html=operation.command_html,
        description=operation.description,
        live=operation.live,
        started_at=operation.started_at,
        ended_at=operation.ended_at,
        end_reason=operation.end_reason,
        output=operation.output,
        line_count=operation.line_count,
        events=tuple(
            MonitorEventResponse(
                event=event.event,
                status=event.status,
                summary=event.summary,
                timestamp=event.timestamp,
            )
            for event in operation.events
        ),
    )


def background_work(work: DashboardBackgroundWork) -> BackgroundWorkResponse:
    return BackgroundWorkResponse(
        running_operation_ids=work.running_operation_ids,
        monitor_count=work.monitor_count,
        background_job_count=work.background_job_count,
        monitors=tuple(background_operation(item) for item in work.monitors),
        jobs=tuple(background_operation(item) for item in work.jobs),
    )


def session_list_item(row: DashboardSessionListItem) -> SessionListItemResponse:
    return SessionListItemResponse(
        session=session_summary(row.session),
        terminal=values.terminal_state(row.terminal),
        project_directory=row.project_directory,
        tab_state=row.tab_state,
        statistics=activity_statistics(row.statistics),
        usage=usage_summary(row.usage),
        context=context_summary(row.context),
        repository=values.maybe_repository_status(row.repository),
    )


def canonical_snapshot(snapshot: DashboardSessionSnapshot) -> CanonicalSnapshotResponse:
    return CanonicalSnapshotResponse(
        cursor=snapshot.cursor,
        session=maybe_session_summary(snapshot.session),
        tab_state=snapshot.tab_state,
        actors=tuple(actor_summary(actor) for actor in snapshot.actors),
        usage=usage_summary(snapshot.usage),
        context=context_summary(snapshot.context),
        attention=attention_state(snapshot.attention),
        tasks=tuple(task_summary(task) for task in snapshot.tasks),
        goal=maybe_goal_state(snapshot.goal),
        background_work=background_work(snapshot.background_work),
        statistics=activity_statistics(snapshot.statistics),
    )


def session_snapshot(
    canonical: DashboardSessionSnapshot, workspace: SessionApplicationSnapshot
) -> SessionSnapshotResponse:
    """The session page's whole reply, both halves."""
    return SessionSnapshotResponse(
        canonical=canonical_snapshot(canonical),
        application=application.session_application(workspace),
    )
