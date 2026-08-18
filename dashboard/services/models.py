"""The shapes the browser receives — one dataclass per thing a page draws.

These are the dashboard's OWN vocabulary, not the engine's: a question with its
options rendered, a background job with its last few output lines, a session
row with its git status beside it. The engine's read models are facts; these
are facts arranged for one page.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.repository import RepositoryStatus
from dashboard.render.items import DashboardItem
from dashboard.render.markdown import md_html
from domain.ids import ActorId, AttentionId, OperationId
from domain.values import AttentionType
from engine.projections import (
    ActorSummary,
    ActivityStatistics,
    AttentionState,
    ContextSummary,
    GoalState,
    SessionSummary,
    TabState,
    TaskSummary,
    UsageSummary,
)
from harness.models import TerminalSessionState


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
    attention_type: AttentionType
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


@dataclass(frozen=True)
class DashboardActivityFrame:
    """Everything that changed since a reader's cursor, and the snapshot as of
    it. Data only: how a frame reaches a browser is api/sse.py's business, and
    this file used to carry a `.sse()` that made it the dashboard's."""

    cursor: int
    items: tuple[DashboardItem, ...]
    snapshot: DashboardSessionSnapshot


def attention_state(state: AttentionState) -> DashboardAttentionState:
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
