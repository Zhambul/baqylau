"""Harness-neutral semantic read models folded from canonical facts.

    models.py    the vocabulary every surface renders
    pages.py     one session's events, fetched once and kept warm
    activity.py  the fold that turns facts into the blocks a surface shows
    paging.py    the three ways to ask for a slice of that stream
    session.py · usage.py · attention.py · work.py · tabstate.py
                 one fold per question, each a pure function over a page
    queries.py   `SessionQueries` — the one door, a line per projection

The read models are re-exported here, so a consumer imports the shape it
renders from `engine.projections` and never has to know which fold built it.
"""

from engine.projections.models import (
    Activity,
    ActivityContext,
    ActivityPage,
    ActivityScope,
    ActivityStatistics,
    ActivityWindow,
    ActorAssignmentActivity,
    ActorMessageActivity,
    ActorSummary,
    AttentionActivity,
    AttentionState,
    BackgroundWorkSummary,
    CompactionActivity,
    ContextSummary,
    ContextWindow,
    FileActivity,
    GoalState,
    MessageActivity,
    OperationActivity,
    PendingAttention,
    ReasoningActivity,
    SessionSummary,
    TabState,
    TaskActivity,
    TaskSummary,
    UsageSummary,
)
from engine.projections.queries import SessionQueries

__all__ = [
    "Activity",
    "ActivityContext",
    "ActivityPage",
    "ActivityScope",
    "ActivityStatistics",
    "ActivityWindow",
    "ActorAssignmentActivity",
    "ActorMessageActivity",
    "ActorSummary",
    "AttentionActivity",
    "AttentionState",
    "BackgroundWorkSummary",
    "CompactionActivity",
    "ContextSummary",
    "ContextWindow",
    "FileActivity",
    "GoalState",
    "MessageActivity",
    "OperationActivity",
    "PendingAttention",
    "ReasoningActivity",
    "SessionQueries",
    "SessionSummary",
    "TabState",
    "TaskActivity",
    "TaskSummary",
    "UsageSummary",
]
